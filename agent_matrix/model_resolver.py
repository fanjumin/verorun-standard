#!/usr/bin/env python3
"""
Agent Matrix — 模型选择策略解析器（§3）
========================================
按 plugin.json 的 model_policy 三层策略解析模型参数：

  tier     → system_config 查 model_tier_{tier}（provider_model_id），命中即用
  explicit → 策略内显式 provider + model
  inherit / fallback → 全局默认（system_config 种子 ai_text_provider / ai_text_model）

统一入口，供各插件复用，避免在 content_factory / enterprise_verify 等处重复实现。

档位值约定（A4）：`model_tier_{tier}` 存的是 `provider_models.id`（正整数），
由 deploy/seed_data.py 按此写入。DB 里存什么并不保证——模型灰化会删/置灰行、
人工改配置会填错、个别插件历史上按 "provider:model" 字符串读过同一 key
（plugins/visitor_profile 的 model_tier_cheap）。因此解析前一律先校验：
非法值（空 / 非正整数 / 指向不存在或未激活的模型）不再原样透传给 engine
（`WHERE pm.id = %s` 拿到非数字会触发 invalid input syntax for type bigint，
拿到已置灰的 id 会抛 Model not found or inactive），而是告警后降级到
inherit/fallback 路径。
"""
from typing import Dict, Tuple

import logging

logger = logging.getLogger(__name__)

_TIER_PREFIX = 'model_tier_'
_TIER_AUDIT_DONE = False  # 惰性启动自检：每个 worker 只跑一次


def _validate_tier_target(raw_value) -> Tuple[bool, str]:
    """校验 model_tier_* 的值是不是一个"当前可用"的 provider_model_id。

    返回 (ok, reason)，永不抛异常 —— 校验本身不能成为新的故障源，
    失败一律按"该档位不可用"处理，由调用方降级。
    """
    raw = str(raw_value or '').strip()
    if not raw:
        return False, 'empty value'
    if not raw.isdigit() or int(raw) <= 0:
        # 非数字值（如 'dashscope:qwen-turbo'）直接下传会让 engine 的
        # `WHERE pm.id = %s` 撞 invalid input syntax for type bigint。
        return False, f'not a provider_model_id: {raw!r}'
    try:
        from models import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT pm.id FROM provider_models pm "
                "JOIN providers p ON p.id = pm.provider_id "
                "WHERE pm.id = %s AND pm.is_active = 1 AND p.is_active = 1",
                (int(raw),)
            ).fetchone()
    except Exception as e:
        # 自检路径不允许静默吞掉：记录后按未知处理。
        logger.warning('[model_tier] validate %r failed: %s', raw, e)
        return False, f'lookup failed: {e}'
    if not row:
        return False, f'no active provider_model with id={raw}'
    return True, ''


def audit_model_tiers(force: bool = False):
    """扫全部 model_tier_* 并告警脏值 + 空兜底链，每个 worker 只跑一次。

    用途：把"配置笔误"从"第一次调用才炸"提前到启动后第一次解析时暴露。
    返回脏档位列表 [(key, value, reason), ...]，供运维脚本复用。
    """
    global _TIER_AUDIT_DONE
    if _TIER_AUDIT_DONE and not force:
        return []
    _TIER_AUDIT_DONE = True
    bad = []
    try:
        from models import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT key, value FROM system_config WHERE key LIKE %s ORDER BY key",
                (_TIER_PREFIX + '%',)
            ).fetchall()
            fb = conn.execute(
                "SELECT value FROM system_config WHERE key = 'ai_fallback_models'"
            ).fetchone()
    except Exception as e:
        logger.warning('[model_tier] startup audit skipped: %s', e)
        return bad

    for r in rows:
        r = dict(r)
        ok, reason = _validate_tier_target(r.get('value'))
        if not ok:
            bad.append((r.get('key'), r.get('value'), reason))
            logger.error('[model_tier] %s = %r 无效（%s）；该档位调用将降级到 '
                         'ai_text_provider/ai_text_model', r.get('key'), r.get('value'), reason)
    if not (fb and str(dict(fb).get('value') or '').strip()):
        logger.warning('[model_tier] ai_fallback_models 为空：主模型失败时没有兜底模型，'
                       '档位调用失败会直接抛给业务')
    return bad


def resolve_model_args(model_policy: dict,
                       default_provider: str = '',
                       default_model: str = '') -> dict:
    """按 model_policy 三层策略解析模型参数，返回 get_gateway().chat() 可用的 kwargs。

    参数:
        model_policy: plugin.json agents[].model_policy 声明的策略 dict（可为空 dict）
        default_provider: inherit/fallback 兜底用的默认 provider（兼容调用方旧默认值）
        default_model:    inherit/fallback 兜底用的默认 model
    """
    from agent_matrix.engine import _get_system_key
    strategy = (model_policy or {}).get('strategy', 'inherit')
    # 1. tier：读 system_config model_tier_{tier}（provider_model_id），命中则用之
    if strategy == 'tier':
        tier = model_policy.get('tier', 'standard')
        pm_id = _get_system_key(f'model_tier_{tier}')
        audit_model_tiers()  # 惰性启动自检：首次解析时把脏档位一次性告警出来
        if pm_id:
            ok, reason = _validate_tier_target(pm_id)
            if ok:
                return {'provider_model_id': pm_id}
            # 脏值不再透传：engine 拿到它只会抛 ValueError / DataError
            logger.error('model_tier_%s = %r 无效（%s），已降级到 inherit/fallback 路径',
                         tier, pm_id, reason)
        else:
            # BUG-8：tier 未配置时不再静默降级，显式告警以便排查
            logger.warning('model_tier_%s 未在 system_config 配置，已降级到 inherit/fallback 路径', tier)
    # 2. explicit：策略内显式 provider+model
    if strategy == 'explicit':
        provider = model_policy.get('provider', '')
        model = model_policy.get('model', '')
        if provider and model:
            return {'provider': provider, 'model': model}
    # 3. inherit / fallback：全局默认（system_config 种子 ai_text_provider/ai_text_model）
    provider = _get_system_key('ai_text_provider') or default_provider
    model = _get_system_key('ai_text_model') or default_model
    if not provider or not model:
        logger.error('system_config 缺少 ai_text_provider/ai_text_model 且无显式入参，'
                     '模型解析失败（请确认部署已执行 deploy/seed_data.py 种子）')
    return {'provider': provider, 'model': model}
