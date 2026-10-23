#!/usr/bin/env python3
"""Token 级上下文预算层（Agent 内核升级 WP-A，对应任务 6.3）。

职责
----
所有进入 LLM 的消息列表先经本模块估算 token 总量；超预算时按
「不可分割单元」做滑动截断，保证 OpenAI 兼容协议的结构合法性。

设计铁律
--------
1. 零依赖硬要求：tiktoken 缺失（未安装，或离线取不到 BPE 词表）时回退启发式
   估算，绝不抛异常。
2. fail-open：本模块任何内部异常一律返回入参原样，绝不阻断对话。
3. 不反向依赖上层：只依赖标准库、可选的 tiktoken，以及运行时才懒导入的主库
   取库函数；不导入 orchestrator / agent_runner / engine。

核心正确性点
------------
OpenAI 兼容 API 对孤儿 tool 消息返回 400；反向同样不合法——保留了
assistant 的 tool_calls 却缺少对应 tool 响应也会报错。因此单元边界定义为：

    assistant 且带 tool_calls + 其后连续的全部 role='tool' 消息 = 1 个单元
    其余消息各自成 1 个单元

截断只允许整单元丢弃，绝不允许切在单元内部。

system_config 开关
------------------
    context_budget_tokens     默认 -1（窗口自适应）；0 表示关闭（原样直通）
                              -1 = 取「模型窗口 − 预留」；正数 = 固定预算（旧行为）
    context_compress_enabled  默认 0；1=仅截断；2=截断+微压缩+摘要压缩；
                              3=在 2 之上增加主动压缩（WP-10，auto_compact_messages，
                              阈值触发全对话摘要重建）；仅在调用方注入 summarize
                              回调时，被丢弃片段才走摘要。摘要回调需要模型调用，
                              不在本模块内实现，由 agent_runner._make_summarizer 注入。
"""

import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

# ── 预算默认值（system_config 无对应 key 时生效）──
CONTEXT_DEFAULTS = {
    # -1 = 窗口自适应（min(模型窗口−预留)），由 resolve_context_budget 收口；
    #  0 = 关闭（fit_messages 直通）；正数 = 固定预算（旧行为）。
    # 哨兵 -1 是 truthy，下游 `or 0` 不会把它吞成 0；但它是负数，
    # 直接喂给 fit_messages 会被 `budget <= 0` 当成「关闭」——因此所有消费点
    # 必须走 resolve_context_budget()，不得直接消费 get_context_budget()。
    'context_budget_tokens': -1,
    'context_compress_enabled': 0,    # 超预算时是否启用摘要（需调用方注入回调）
}

# CJK 及全角区段：按 1 token/字保守估算（真实约 0.6~0.7，宁高勿低）
_CJK_RE = re.compile(
    r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af\uff00-\uffef]'
)

_ENCODER = None
_ENCODER_TRIED = False


def _get_encoder():
    """懒加载 tiktoken 编码器；不可用返回 None（不抛异常）。"""
    global _ENCODER, _ENCODER_TRIED
    if _ENCODER_TRIED:
        return _ENCODER
    _ENCODER_TRIED = True
    try:
        import tiktoken
        _ENCODER = tiktoken.get_encoding('cl100k_base')
    except Exception as e:      # 含离线取不到 BPE 词表的情况
        logger.info('[ContextBudget] tiktoken unavailable, fallback to heuristic: %s', e)
        _ENCODER = None
    return _ENCODER


def reset_encoder_cache():
    """重置编码器探测缓存（仅供单测注入桩模块使用）。"""
    global _ENCODER, _ENCODER_TRIED
    _ENCODER = None
    _ENCODER_TRIED = False


def _count(text) -> int:
    """估算单段文本的 token 数；空值返回 0。"""
    if not text:
        return 0
    text = str(text)
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    cjk = len(_CJK_RE.findall(text))
    return cjk + int((len(text) - cjk) * 0.25) + 1


def estimate_tokens(text_or_messages) -> int:
    """估算 token 数。

    传入消息列表时，逐条计 4（角色/分隔等结构开销）再加 content 与
    tool_calls 的 name / arguments。任何异常返回 0。
    """
    try:
        if isinstance(text_or_messages, (list, tuple)):
            total = 0
            for m in text_or_messages:
                if not isinstance(m, dict):
                    continue
                total += 4
                total += _count(m.get('content'))
                for tc in (m.get('tool_calls') or []):
                    fn = tc.get('function') if isinstance(tc, dict) else None
                    if isinstance(fn, dict):
                        total += _count(fn.get('name'))
                        total += _count(fn.get('arguments'))
            return total
        return _count(text_or_messages)
    except Exception:
        return 0


def _drop_orphan_tools(messages):
    """防御性清洗入参中已存在的孤儿 tool 消息。

    上游 history 若已带入孤儿（历史缺陷或外部调用方拼错），任何截断策略都救不了
    协议报错，必须在此拦掉。判定依据：tool_call_id 能否在**其之前**的
    assistant.tool_calls 中找到。
    """
    live_ids, out, dropped = set(), [], 0
    for m in messages:
        role = m.get('role') if isinstance(m, dict) else None
        if role == 'assistant':
            for tc in (m.get('tool_calls') or []):
                tid = tc.get('id') if isinstance(tc, dict) else None
                if tid:
                    live_ids.add(tid)
            out.append(m)
        elif role == 'tool':
            if m.get('tool_call_id') in live_ids:
                out.append(m)
            else:
                dropped += 1
        else:
            out.append(m)
    if dropped:
        logger.warning('[ContextBudget] dropped %d orphan tool message(s)', dropped)
    return out


def _group_units(messages):
    """切成不可分割单元：assistant 且带 tool_calls + 其后连续的全部 tool 消息。

    非 dict 条目各自成单元，避免后续访问属性时抛异常。
    """
    units, i, n = [], 0, len(messages)
    while i < n:
        m = messages[i]
        if isinstance(m, dict) and m.get('role') == 'assistant' and m.get('tool_calls'):
            j = i + 1
            while j < n and isinstance(messages[j], dict) and messages[j].get('role') == 'tool':
                j += 1
            units.append(list(messages[i:j]))
            i = j
        else:
            units.append([m])
            i += 1
    return units


def fit_messages(messages, budget, *, keep_system=True, keep_recent=4, summarize=None):
    """在预算内裁剪消息列表。

    Args:
        messages: 原始消息列表（OpenAI 兼容格式）
        budget: token 预算；<= 0 表示关闭，原样直通
        keep_system: 是否恒保留开头的 system 消息（不参与裁剪）
        keep_recent: 至少保留的尾部**单元**数（预算再紧也不裁穿）
        summarize: 可选回调，接收被丢弃的消息列表并返回摘要字符串

    Returns:
        裁剪后的消息列表；未超预算 / 关闭 / 任意异常时返回**入参原对象**。
    """
    try:
        if not isinstance(messages, list) or not messages:
            return messages
        try:
            budget = int(budget)
        except (TypeError, ValueError):
            return messages
        if budget <= 0:
            logger.info('[ContextBudget] disabled (budget=%s), passthrough', budget)
            return messages
        if estimate_tokens(messages) <= budget:
            logger.info('[ContextBudget] passthrough (estimated<=%d)', budget)
            return messages

        msgs = _drop_orphan_tools(messages)
        units = _group_units(msgs)

        head, body = [], []
        for u in units:
            first = u[0] if u else None
            if (keep_system and not body and isinstance(first, dict)
                    and first.get('role') == 'system'):
                head.append(u)
            else:
                body.append(u)

        floor = max(1, int(keep_recent))
        dropped, dropped_n, summary_text = [], 0, ''

        def _assemble():
            """按当前 head / body / 丢弃计数组装结果（含摘要与省略提示）。"""
            out = [m for u in head for m in u]
            if dropped_n:
                if summary_text:
                    out.append({'role': 'system',
                                'content': '[Summary of earlier conversation] ' + str(summary_text)})
                out.append({'role': 'system',
                            'content': '[Earlier conversation] %d earlier messages were omitted to control context length.' % dropped_n})
            out.extend([m for u in body for m in u])
            return out

        # 第一轮：按单元从前向后丢弃，直到落入预算或触达 keep_recent 保底
        while len(body) > floor:
            if estimate_tokens(_assemble()) <= budget:
                break
            victim = body.pop(0)
            dropped.extend(victim)
            dropped_n += len(victim)

        # 摘要回调：仅在确有丢弃时调用一次；异常不影响主流程
        if dropped_n and summarize is not None:
            try:
                summary_text = summarize(dropped) or ''
            except Exception as e:
                logger.warning('[ContextBudget] summarize failed: %s', e)
                summary_text = ''

        # 第二轮：摘要与省略提示本身有开销，若因此再度超预算则继续丢弃
        while estimate_tokens(_assemble()) > budget and len(body) > floor:
            victim = body.pop(0)
            dropped_n += len(victim)

        out = _assemble()
        if not dropped_n:
            # WP-7：超预算但保底单元完整保留、未发生丢弃时补一行审计
            # （只收口可观测性，fail-open 语义不变）
            logger.info('[ContextBudget] over budget but keep_recent floor kept, '
                        'no messages dropped (dropped_n=0)')
        logger.info('[ContextBudget] trimmed %d msg(s) -> estimated %d (budget=%d)',
                    dropped_n, estimate_tokens(out), budget)
        return out
    except Exception as e:
        logger.warning('[ContextBudget] fit failed, passthrough: %s', e)
        return messages


def get_context_config() -> dict:
    """读取 system_config 中的上下文预算配置，缺失或异常回退默认值。

    读取与降级范式对齐 agent_matrix/engine.py 的 _get_ai_budget_config。
    """
    cfg = dict(CONTEXT_DEFAULTS)
    try:
        from models import get_db as _main_get_db
        with _main_get_db() as conn:
            rows = conn.execute(
                "SELECT key, value FROM system_config WHERE key IN "
                "('context_budget_tokens','context_compress_enabled')"
            ).fetchall()
        for r in (rows or []):
            key = r['key'] if not isinstance(r, tuple) else r[0]
            val = r['value'] if not isinstance(r, tuple) else r[1]
            if key not in cfg or val is None or str(val).strip() == '':
                continue
            try:
                cfg[key] = int(val)
            except (ValueError, TypeError):
                logger.warning('[ContextBudget] bad value for %s=%r, keep default', key, val)
    except Exception as e:
        logger.warning('[ContextBudget] read config failed, use defaults: %s', e)
    return cfg


def get_context_budget() -> int:
    """返回 system_config 里的**原始**预算值；0 表示关闭，-1 是哨兵。

    注意：-1（窗口自适应）不是可直接使用的值 —— 它是负数，喂给
    fit_messages 会被 `budget <= 0` 判定为「关闭」而整段直通。
    需要可用预算的调用方一律改用 resolve_context_budget()。
    """
    try:
        return int(get_context_config().get('context_budget_tokens') or 0)
    except Exception:
        return int(CONTEXT_DEFAULTS['context_budget_tokens'])


def resolve_context_budget(model=None) -> int:
    """预算收口点：原始配置值 → 可直接喂给 fit_messages 的有效预算。

    这是哨兵 -1 唯一的翻译处，避免每个消费点各自实现而漏改（历史上
    `context_budget_tokens` 已有 4 处消费者，分散收口必然漂移）。

    语义：
        0     → 0（关闭，fit_messages 直通）
        -1    → get_effective_budget(model, -1) = 模型窗口 − 预留
        正数  → min(正数, 模型窗口 − 预留)

    Args:
        model: get_effective_budget 接受的模型标识；None 按缺省窗口。

    Returns:
        >= 0 的预算 token 数。
    """
    raw = get_context_budget()
    if raw == 0:
        return 0                      # 「关闭」必须原样保留，不得被收口抬升
    try:
        return int(get_effective_budget(model, raw))
    except Exception as e:
        logger.warning('[ContextBudget] resolve failed, treat as closed: %s', e)
        return 0


def _fit(messages, keep_recent=4, summarize=None, model=None):
    """engine 层门面：读 system_config 预算后裁剪。

    命名沿用已批准方案（v1.1 §3.4 A-1）中的 `_fit`，虽以下划线开头，但它是
    供 engine 与 agent_runner 复用的模块门面。摘要回调仅在
    context_compress_enabled 打开且调用方显式注入时透传。

    预算经 resolve_context_budget 收口：缺省 -1 时按 model 的窗口自适应，
    0 保持直通。model 缺省为 None —— 调用方若持有模型信息（engine 的
    self._provider/self._model）应显式传入，否则会按 DEFAULT_MODEL_WINDOW
    收口，与 agent_runner 路径的窗口感知结果不一致。
    """
    cfg = get_context_config()
    budget = resolve_context_budget(model)
    if not int(cfg.get('context_compress_enabled') or 0):
        summarize = None
    return fit_messages(messages, budget, keep_recent=keep_recent, summarize=summarize)


# ============================================================
# WP-1：压缩管线第二层 —— 微压缩 + 分级开关
# ============================================================

# 旧工具结果清理后的占位标记（英文，WP-0 语言政策；语义对齐
# 语义为"旧工具结果已清理"，清理侧与重建侧共用同一常量）。
MICROCOMPACT_CLEARED = '[Old tool result content cleared]'


def normalize_compress_level(value) -> int:
    """把 system_config 的 context_compress_enabled 归一为 0/1/2/3。

    0 = 关闭新特性（仅保留 WP-A 截断；与历史行为一致）
    1 = 仅截断（等价 0 的新特性语义，占位以便未来区分）
    2 = 截断 + 微压缩 + 摘要压缩（完整管线）
    3 = 截断 + 微压缩 + 摘要压缩 + 主动压缩（WP-10 autocompact）
    解析失败/负数/空值 → 0（新特性关闭，最保守）。
    """
    try:
        if value is None:
            return 0
        if isinstance(value, str):
            value = value.strip()
            if value == '':
                return 0
        level = int(value)
    except (TypeError, ValueError):
        return 0
    if level <= 0:
        return 0
    if level == 1:
        return 1
    if level == 2:
        return 2
    return 3


def get_compress_level() -> int:
    """读取压缩等级（0/1/2/3）。读取失败 → 0（新特性关闭，fail-open）。"""
    try:
        return normalize_compress_level(get_context_config().get('context_compress_enabled'))
    except Exception as e:
        logger.warning('[ContextBudget] read compress level failed, features off: %s', e)
        return 0


def microcompact_tool_results(messages, budget=0, keep_recent=6,
                              trigger_ratio=0.8):
    """压力触发的微压缩：把较早的 tool 结果内容替换为占位标记。

    按 tool 消息清内容、不检查语义、不删除消息 ——assistant(tool_calls) 与
    tool 消息的配对结构保持完整，绝不产生孤儿消息。仅当估算 token 超过
    budget*trigger_ratio 时触发；
    budget<=0 或未超阈值时原样返回（不入参拷贝，零开销直通）。

    Args:
        messages: OpenAI 兼容消息列表
        budget: token 预算（<=0 时功能关闭）
        keep_recent: 保留最近 N 条 tool 消息的原文
        trigger_ratio: 触发阈值（占预算比例）

    Returns:
        (new_messages, cleared_count)：new_messages 为新列表（被修改的条目
        为浅拷贝，不污染调用方原对象）；cleared_count 为被清空的条数。
    """
    try:
        if not isinstance(messages, list) or not messages:
            return messages, 0
        try:
            budget = int(budget)
        except (TypeError, ValueError):
            return messages, 0
        if budget <= 0:
            return messages, 0
        if estimate_tokens(messages) <= budget * trigger_ratio:
            return messages, 0

        # 收集全部 tool 消息下标（只清 tool 内容，绝不动 assistant/user/system）
        tool_idx = [i for i, m in enumerate(messages)
                    if isinstance(m, dict) and m.get('role') == 'tool']
        floor = max(1, int(keep_recent))
        clear_idx = set(tool_idx[:-floor]) if len(tool_idx) > floor else set()
        if not clear_idx:
            return messages, 0

        out = list(messages)
        cleared = 0
        for i in clear_idx:
            m = out[i]
            if m.get('content') in (None, '', MICROCOMPACT_CLEARED):
                continue
            cleared += 1
            new_m = dict(m)
            new_m['content'] = MICROCOMPACT_CLEARED
            out[i] = new_m
        if cleared:
            logger.info('[ContextBudget] microcompact cleared %d old tool result(s)', cleared)
        return out, cleared
    except Exception as e:
        logger.warning('[ContextBudget] microcompact failed, passthrough: %s', e)
        return messages, 0


# ============================================================
# WP-10：压缩管线第三层 —— 主动压缩（autocompact）+ 模型窗口感知
# ============================================================

# ── 主动压缩配置（env 可覆盖） ──
ENV_AUTO_COMPACT_RATIO = 'AGENT_AUTO_COMPACT_RATIO'
ENV_AUTO_COMPACT_FAIL_BREAKER = 'AGENT_AUTO_COMPACT_FAIL_BREAKER'
DEFAULT_AUTO_COMPACT_RATIO = 0.8        # 估算超 budget×ratio 即触发
DEFAULT_AUTO_COMPACT_FAIL_BREAKER = 3   # 连续摘要失败熔断阈值


def _env_ratio(name, default):
    """读取 0<r<=1 的触发比例；解析失败/越界回退默认（fail-open）。"""
    try:
        r = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    if not (0 < r <= 1):
        return default
    return r


def _env_int_pos(name, default):
    try:
        v = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return v if v >= 1 else default


def get_auto_compact_ratio():
    """主动压缩触发比例（env 可覆盖，缺省 0.8）。"""
    return _env_ratio(ENV_AUTO_COMPACT_RATIO, DEFAULT_AUTO_COMPACT_RATIO)


class _AutoCompactBreaker:
    """连续摘要失败熔断（范式对齐 loop_gates.FailureBreaker）。

    连续失败达阈值后停止尝试主动压缩（直接放行入参），直至一次成功
    reset。进程级单例：摘要回调依赖模型可用性，模型侧故障时没必要
    每轮重试。阈值每次动态读取（env 热生效，便于测试与运维调整）；
    锁保护计数，多线程 waitress 下安全。
    """

    def __init__(self):
        self._fails = 0
        self._lock = threading.Lock()

    def _threshold(self):
        return _env_int_pos(ENV_AUTO_COMPACT_FAIL_BREAKER,
                            DEFAULT_AUTO_COMPACT_FAIL_BREAKER)

    def tripped(self):
        with self._lock:
            return self._fails >= self._threshold()

    def record_failure(self):
        with self._lock:
            self._fails += 1
            return self._fails

    def record_success(self):
        with self._lock:
            self._fails = 0

    def fail_count(self):
        with self._lock:
            return self._fails

    def reset(self):
        with self._lock:
            self._fails = 0


_BREAKER = _AutoCompactBreaker()


def reset_auto_compact_state():
    """重置熔断计数（仅供单测使用）。"""
    _BREAKER.reset()


def _has_orphan_tools(messages) -> bool:
    """结构自检：是否存在没有对应 assistant.tool_calls 的孤儿 tool 消息。

    复用 _group_units 的单元语义：tool 消息必须能在其之前的
    assistant.tool_calls 中找到 tool_call_id。
    """
    live_ids = set()
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get('role') == 'assistant':
            for tc in (m.get('tool_calls') or []):
                tid = tc.get('id') if isinstance(tc, dict) else None
                if tid:
                    live_ids.add(tid)
        elif m.get('role') == 'tool':
            if m.get('tool_call_id') not in live_ids:
                return True
    return False


def auto_compact_messages(messages, budget, summarize=None, keep_recent=6,
                          trigger_ratio=None):
    """WP-10 主动压缩：中段全量摘要重建。

    与 fit_messages 的差别：fit 是"逐单元丢到落入预算"的被动截断；
    本函数是估算一超 `budget×trigger_ratio` 就把〔system 之后 → 尾部
    keep_recent 个单元〕之间的**全部中段**一次性交给摘要回调压成要点，
    重建为 `system + [Summary] + 尾部单元`。适用于长任务后段——
    被动截断此时只能保命，主动压缩能保住语义。

    Args:
        messages: 原始消息列表（OpenAI 兼容格式）
        budget: token 预算；<= 0 表示关闭，原样直通
        summarize: 摘要回调，接收被压缩的消息列表、返回摘要字符串
                   （签名与 fit_messages 的 summarize 完全一致；
                   agent_runner 侧复用 _make_summarizer 闭包）。
                   异常/空串按"无摘要"处理（仍走重建，附省略提示）。
        keep_recent: 尾部保留的**单元**数
        trigger_ratio: 触发阈值（占预算比例）；None 用 env/缺省 0.8

    Returns:
        重建后的消息列表；未超阈值 / 无中段 / 关闭 / 熔断 / 任意异常时
        返回**入参原对象**（fail-open 铁律）。
    """
    try:
        if not isinstance(messages, list) or not messages:
            return messages
        try:
            budget = int(budget)
        except (TypeError, ValueError):
            return messages
        if budget <= 0:
            return messages
        ratio = get_auto_compact_ratio() if trigger_ratio is None else float(trigger_ratio)
        if not (0 < ratio <= 1):
            ratio = DEFAULT_AUTO_COMPACT_RATIO
        if estimate_tokens(messages) <= budget * ratio:
            return messages
        if _BREAKER.tripped():
            logger.info('[AutoCompact] breaker tripped (%d consecutive fails), '
                        'passthrough', _BREAKER.fail_count())
            return messages

        msgs = _drop_orphan_tools(messages)
        units = _group_units(msgs)

        head, body = [], []
        for u in units:
            first = u[0] if u else None
            if (not body and isinstance(first, dict)
                    and first.get('role') == 'system'):
                head.append(u)
            else:
                body.append(u)

        floor = max(1, int(keep_recent))
        if len(body) <= floor:
            # 全是尾部单元，无中段可压 —— 交由 fit_messages 处理
            return messages
        middle = body[:-floor]
        tail = body[-floor:]

        dropped = [m for u in middle for m in u]
        if summarize is None:
            # 无回调时不重建（无摘要的主动压缩 = 纯截断，fit_messages 已覆盖）
            return messages
        try:
            summary_text = str(summarize(dropped) or '').strip()[:2000]
        except Exception as e:
            # 摘要异常 → 保守返回入参原样（fail-open 铁律：绝不静默丢消息），
            # 并记录熔断计数；连续失败达阈值后不再尝试。
            fails = _BREAKER.record_failure()
            logger.warning('[AutoCompact] summarize failed (%d consecutive), '
                           'passthrough: %s', fails, e)
            return messages

        out = [m for u in head for m in u]
        out.append({'role': 'system',
                    'content': '[Summary of earlier conversation] ' + summary_text})
        out.append({'role': 'system',
                    'content': '[Earlier conversation] %d earlier messages were '
                               'compacted to control context length.' % len(dropped)})
        out.extend([m for u in tail for m in u])

        # 重建结构自检：按单元整体搬运不可能产生孤儿，但铁律要求显式断言
        if _has_orphan_tools(out):
            logger.warning('[AutoCompact] rebuilt messages contain orphan tool '
                           'messages, passthrough (fail-open)')
            return messages

        _BREAKER.record_success()
        logger.info('[AutoCompact] compacted %d msg(s) -> %d (estimated %d -> %d, '
                    'budget=%d)', len(messages), len(out),
                    estimate_tokens(messages), estimate_tokens(out), budget)
        return out
    except Exception as e:
        logger.warning('[AutoCompact] failed, passthrough: %s', e)
        return messages


# ── 模型窗口感知（WP-10 收口点） ──
# 粗表：按 provider slug 命中；同 provider 多模型取最常见档位（宁低勿高，
# 低估只会让压缩更早触发，高估才会引发 API 400）。精确窗口待 AI Hub 侧
# provider_models 增加窗口列后替换本表（届时本函数为唯一收口点）。
MODEL_WINDOW_DEFAULTS = {
    'openai': 128000, 'azure': 128000, 'azure-openai': 128000,
    'anthropic': 200000, 'claude': 200000,
    'deepseek': 65536,
    'qwen': 131072, 'dashscope': 131072, 'aliyun': 131072,
    'zhipu': 128000, 'glm': 128000,
    'moonshot': 128000, 'kimi': 128000,
    'baichuan': 32768,
    'minimax': 245760,
    'volcengine': 128000, 'doubao': 128000,
    'tencent': 128000, 'hunyuan': 128000,
    'ollama': 32768, 'vllm': 32768, 'local': 32768,
}
DEFAULT_MODEL_WINDOW = 32768
WINDOW_RESERVE = 4000   # 为响应与工具调用结果预留的窗口余量


def _model_window(model) -> int:
    """取模型上下文窗口（粗表命中或缺省 32k）。任何异常 → 缺省值。"""
    try:
        provider = ''
        name = ''
        if isinstance(model, dict):
            provider = str(model.get('provider') or '').strip().lower()
            name = str(model.get('model') or '').strip().lower()
        elif isinstance(model, str):
            raw = model.strip().lower()
            # 'provider/model' 或纯模型名两种形态
            if '/' in raw:
                provider, _, name = raw.partition('/')
            else:
                name = raw
        if provider in MODEL_WINDOW_DEFAULTS:
            return MODEL_WINDOW_DEFAULTS[provider]
        for key, window in MODEL_WINDOW_DEFAULTS.items():
            if key in name:
                return window
    except Exception as e:
        logger.warning('[ContextBudget] model window lookup failed, default: %s', e)
    return DEFAULT_MODEL_WINDOW


def get_effective_budget(model=None, system_budget=None) -> int:
    """模型窗口感知的有效预算：min(system_config 预算, 窗口 − 预留)。

    模型窗口感知缺口的收口点。system_budget 为 None 或 <=0（关闭）时
    退化为窗口上限；窗口本身不足以预留时按窗口原值返回。

    Args:
        model: model 配置 dict {'provider','model'} / 'provider/model' /
               纯模型名；None 按缺省窗口
        system_budget: 调用方已读到的 system_config 预算（避免本函数打库；
               None 时内部读取，读取失败按"无配置"处理）

    Returns:
        有效预算 token 数（>= 0）
    """
    try:
        if system_budget is None:
            system_budget = get_context_budget()
        window = _model_window(model)
        cap = window - WINDOW_RESERVE
        if cap <= 0:
            cap = window
        try:
            system_budget = int(system_budget or 0)
        except (TypeError, ValueError):
            system_budget = 0
        if system_budget <= 0:
            return cap
        return min(system_budget, cap)
    except Exception as e:
        logger.warning('[ContextBudget] effective budget failed, fallback: %s', e)
        try:
            return int(CONTEXT_DEFAULTS['context_budget_tokens'])
        except Exception:
            return 0
