#!/usr/bin/env python3
"""WP-7: ReAct 循环三重停机闸门（预算 / 边际递减 / 熔断）。
WP-12: TokenBudgetGate 增加 soft 软收尾模式（首达 90% nudge，100% 硬停）。

全部 system_config/env 可配、**缺省关闭**；任何配置/数据读取失败一律按
关闭处理（fail-open，内核铁律），与升级前行为逐字节一致。

- TokenBudgetGate : task.token_budget，消费从 agent_token_logs 按 task_id
                    聚合读取。hard 模式（缺省）达 budget×ratio（90%）即
                    停机；soft 模式（env AGENT_TOKEN_GATE_MODE=soft）首达
                    90% 仅注入一次 nudge 软收尾，达 100% 才硬停机。
- DiminishingGate : 连续 N 轮 token 增量低于阈值且无新工具调用 → 判空转停机。
                    "无新工具调用" = 本轮工具名全部在历史工具名集合内。
- FailureBreaker  : 连续 LLM 失败达阈值熔断终止（env 可配，缺省 3，范式对齐
                    plugin_manager/guard.py:22 的 PLUGIN_CIRCUIT_BREAKER_THRESHOLD）。

iter_react 仅消费 LoopGates 的五个入口（budget_stop / budget_nudge / observe /
diminish_stop / failure），不感知具体实现与 DB——token 消费通过注入回调读取，
测试可直接喂桩。
"""
import os
import logging

logger = logging.getLogger(__name__)

# ── 环境变量（缺省关闭） ──
ENV_TOKEN_GATE = 'AGENT_TOKEN_GATE_ENABLED'
ENV_DIMINISH_GATE = 'AGENT_DIMINISHING_GATE_ENABLED'
ENV_FAILURE_BREAKER = 'AGENT_FAILURE_BREAKER_ENABLED'
ENV_FAILURE_THRESHOLD = 'AGENT_FAILURE_BREAKER_THRESHOLD'
ENV_DIMINISH_ROUNDS = 'AGENT_DIMINISHING_ROUNDS'
ENV_DIMINISH_DELTA = 'AGENT_DIMINISHING_DELTA'
ENV_TOKEN_RATIO = 'AGENT_TOKEN_GATE_RATIO'
ENV_TOKEN_GATE_MODE = 'AGENT_TOKEN_GATE_MODE'

DEFAULT_TOKEN_RATIO = 0.9
DEFAULT_DIMINISH_ROUNDS = 3
DEFAULT_DIMINISH_DELTA = 50
DEFAULT_FAILURE_THRESHOLD = 3

# WP-12：预算闸门停机模式。hard = 达 ratio 即停机（WP-7 原行为，缺省）；
# soft = 首达 ratio 只注入 nudge（软收尾，不停机），达 100% 才硬停机。
GATE_MODE_HARD = 'hard'
GATE_MODE_SOFT = 'soft'

# soft 模式 nudge 文案（注入 system，提示模型收尾；WP-0 语言政策）
NUDGE_TEMPLATE = ('[TokenGate] Token budget is close to the limit '
                  '({used}/{budget} tokens, >= {pct}%). Please wrap up '
                  'and provide your final answer now.')


def _flag(name):
    """读取 0/1 开关（1/true/yes/on 视为开），解析失败按 0（关闭）。"""
    return str(os.environ.get(name, '0')).strip().lower() in ('1', 'true', 'yes', 'on')


def _env_float(name, default):
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


class TokenBudgetGate:
    """预算闸门：累计消费达阈值停机（续轮推送预算提示）。

    budget <= 0（task 未配置 token_budget）或 disabled → 永不触发（缺省关闭）。
    token_reader 可注入回调 () -> int；读取失败一律按 0（fail-open）。

    WP-12 停机模式（mode）：
      hard（缺省）—— 消费 ≥ budget×ratio 即停机（WP-7 原行为）；
      soft        —— 首达 budget×ratio 只返回一次 nudge（软收尾，不停机），
                     消费 ≥ budget（100%）才硬停机。nudged 标记已注入过。
    """

    def __init__(self, budget=0, token_reader=None, ratio=DEFAULT_TOKEN_RATIO,
                 enabled=True, mode=None):
        self.budget = int(budget or 0)
        self.ratio = float(ratio) if float(ratio) > 0 else DEFAULT_TOKEN_RATIO
        self.enabled = enabled and self.budget > 0
        self._token_reader = token_reader or (lambda: 0)
        mode = str(mode or os.environ.get(ENV_TOKEN_GATE_MODE, GATE_MODE_HARD)
                   ).strip().lower()
        self.mode = mode if mode in (GATE_MODE_HARD, GATE_MODE_SOFT) else GATE_MODE_HARD
        self._nudged = False

    def spent(self):
        """当前累计消费（fail-open：读取失败返回 0）。"""
        try:
            return int(self._token_reader() or 0)
        except Exception as e:
            logger.warning('[TokenGate] token read failed (fail-open): %s', e)
            return 0

    def stop_reason(self):
        """达到硬停机阈值返回预算提示文本，否则 None。

        hard：阈值为 budget×ratio（WP-7 行为）；
        soft：阈值为 budget（100%），ratio 档只 nudge 不停机。
        """
        if not self.enabled:
            return None
        used = self.spent()
        if self.mode == GATE_MODE_SOFT:
            if used >= self.budget:
                return (f'[TokenGate] Token budget exhausted: {used}/{self.budget} '
                        f'tokens (100%), loop stopped')
            return None
        if used >= int(self.budget * self.ratio):
            return (f'[TokenGate] Token budget exhausted: {used}/{self.budget} '
                    f'tokens (>= {int(self.ratio * 100)}%), loop stopped')
        return None

    def nudge_reason(self):
        """WP-12 soft 模式软收尾：首次达到 ratio 阈值返回 nudge 文本（仅一次）。

        hard 模式 / 未启用 / 已注入过 → None。注入后标记 nudged，后续轮次
        同一档位不重复打扰；消费继续涨到 100% 由 stop_reason 硬停机。
        """
        if not self.enabled or self.mode != GATE_MODE_SOFT or self._nudged:
            return None
        used = self.spent()
        if used >= int(self.budget * self.ratio):
            self._nudged = True
            return NUDGE_TEMPLATE.format(used=used, budget=self.budget,
                                         pct=int(self.ratio * 100))
        return None

    @property
    def nudged(self):
        return self._nudged


class DiminishingGate:
    """边际递减闸门：连续 N 轮消费增量低于阈值且无新工具调用 → 空转停机。

    observe(spent, tool_names) 每轮结束喂一次：spent 为累计消费，
    tool_names 为本轮工具名列表。首轮只记录基线不计入停滞计数。
    """

    def __init__(self, max_rounds=DEFAULT_DIMINISH_ROUNDS,
                 delta=DEFAULT_DIMINISH_DELTA, enabled=True):
        self.max_rounds = max(1, int(max_rounds))
        self.delta = max(0, int(delta))
        self.enabled = enabled
        self._last_spent = None
        self._seen_tools = set()
        self._stagnant = 0

    def observe(self, spent, tool_names):
        if not self.enabled:
            return
        spent = int(spent or 0)
        if self._last_spent is not None:
            inc = max(0, spent - self._last_spent)
            new_tool = any(n not in self._seen_tools for n in (tool_names or []))
            if inc < self.delta and not new_tool:
                self._stagnant += 1
            else:
                self._stagnant = 0
        self._last_spent = spent
        self._seen_tools.update(tool_names or [])

    def stop_reason(self):
        if not self.enabled:
            return None
        if self._stagnant >= self.max_rounds:
            return (f'[DiminishingGate] No new tool calls and token increment '
                    f'below {self.delta} for {self.max_rounds} consecutive rounds, '
                    f'loop stopped')
        return None


class FailureBreaker:
    """连续失败熔断：连续 LLM 失败达阈值时熔断终止（env 可配，缺省 3）。

    范式对齐 plugin_manager/guard.py:22：阈值从环境变量读取，失败计数与
    阈值分离，成功路径由调用方决定是否 reset。
    """

    def __init__(self, threshold=DEFAULT_FAILURE_THRESHOLD, enabled=True):
        self.threshold = max(1, int(threshold))
        self.enabled = enabled
        self._fails = 0

    def record_failure(self, exc=None):
        """记录一次失败，返回是否已达熔断阈值。disabled 时不计数、不熔断。"""
        if not self.enabled:
            return False
        self._fails += 1
        return self.should_trip()

    def reset(self):
        self._fails = 0

    def should_trip(self):
        return self.enabled and self._fails >= self.threshold

    def fail_count(self):
        return self._fails


def _make_token_reader(task_id):
    """从 agent_token_logs 按 task_id 聚合累计消费（fail-open：失败返回 0）。"""
    if not task_id:
        return lambda: 0

    def _reader():
        try:
            from models import get_db
            with get_db() as conn:
                row = conn.execute(
                    "SELECT COALESCE(SUM(total_tokens), 0) AS c FROM agent_token_logs "
                    "WHERE task_id = %s", (str(task_id),)).fetchone()
            return int(row['c'] if isinstance(row, dict) else (row[0] or 0))
        except Exception as e:
            logger.warning('[TokenGate] DB aggregate failed (fail-open): %s', e)
            return 0

    return _reader


class LoopGates:
    """三闸门命名空间：iter_react 循环内统一挂点入口（五个方法）。"""

    def __init__(self, token, diminish, failure):
        self.token = token
        self.diminish = diminish
        self.failure = failure

    def budget_stop(self):
        return self.token.stop_reason()

    def budget_nudge(self):
        """WP-12：soft 模式软收尾 nudge（hard 模式恒 None）。"""
        return self.token.nudge_reason()

    def observe(self, spent, tool_names):
        self.diminish.observe(spent, tool_names)

    def diminish_stop(self):
        return self.diminish.stop_reason()


def build_gates(task=None, task_id=None):
    """构造闸门组。task 提供 token_budget；三闸门全部 env 可配、缺省关闭。

    Args:
        task: 任务 dict（取 token_budget，缺失/非法按 0 = 预算闸门关闭）
        task_id: agent_token_logs.task_id（预算消费聚合维度）
    Returns:
        LoopGates 实例（永不抛异常；任何配置异常按对应闸门关闭处理）
    """
    task_budget = 0
    if isinstance(task, dict):
        try:
            task_budget = int(task.get('token_budget') or 0)
        except (TypeError, ValueError):
            task_budget = 0

    token_gate = TokenBudgetGate(
        budget=task_budget,
        token_reader=_make_token_reader(task_id),
        ratio=_env_float(ENV_TOKEN_RATIO, DEFAULT_TOKEN_RATIO),
        enabled=_flag(ENV_TOKEN_GATE),
        mode=os.environ.get(ENV_TOKEN_GATE_MODE))
    diminish_gate = DiminishingGate(
        max_rounds=_env_int(ENV_DIMINISH_ROUNDS, DEFAULT_DIMINISH_ROUNDS),
        delta=_env_int(ENV_DIMINISH_DELTA, DEFAULT_DIMINISH_DELTA),
        enabled=_flag(ENV_DIMINISH_GATE))
    failure_breaker = FailureBreaker(
        threshold=_env_int(ENV_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD),
        enabled=_flag(ENV_FAILURE_BREAKER))
    return LoopGates(token_gate, diminish_gate, failure_breaker)
