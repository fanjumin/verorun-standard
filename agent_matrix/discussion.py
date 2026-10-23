# agent_matrix/discussion.py — 系统级多阶段讨论引擎（四阶段对辩等）
#
# 设计原则（避免版本割裂）：
#   四阶段讨论是「系统能力」，不应在业务插件里 fork 一份副本。本模块是唯一的
#   阶段→角色→Key/模型 解析与执行引擎；业务插件（如 stock_analysis 的金融对辩）
#   只提供领域提示词 + 出境闸门，调用本引擎，不持有任何阶段/角色硬编码。
#
#   阶段到角色的映射存于 system_config.sa_discuss_stage_roles（JSON），
#   运行时读取、配置变更即时生效；DEFAULT_STAGE_ROLES 仅作未 seed 时的兜底。
#   每个角色在 agent_matrix 表已绑定自己的 provider_model_id（专属 Key+厂商+模型），
#   故「四阶段用不同角色」≡「四阶段用不同 Key/不同 LLM」，纯配置即可调整。
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# 系统默认 四阶段 → 角色 slug（finance-desktop 版）。
# 仅在 system_config.sa_discuss_stage_roles 缺失时兜底；生产以配置为准。
DEFAULT_STAGE_ROLES = {
    "planner": "rs_planner",
    "reviewer": "rs_risk",
    "revise": "rs_fundamental",
    "decider": "rs_pm",
}

# system_config 键名（单一事实来源）
STAGE_ROLES_KEY = "sa_discuss_stage_roles"


def load_stage_roles():
    """运行时读取 阶段→角色slug 映射（动态；配置变更即时生效，无需改代码）。

    返回 dict：{stage: role_slug}。解析失败/缺失时回退 DEFAULT_STAGE_ROLES。
    """
    try:
        from agent_matrix.engine import _get_system_key
        raw = _get_system_key(STAGE_ROLES_KEY)
    except Exception as exc:
        logger.warning("load_stage_roles: 读 %s 失败: %s，回退默认", STAGE_ROLES_KEY, exc)
        return dict(DEFAULT_STAGE_ROLES)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data:
                return data
        except Exception:
            logger.warning("load_stage_roles: %s 解析失败，回退默认", STAGE_ROLES_KEY)
    return dict(DEFAULT_STAGE_ROLES)


def resolve_role(slug):
    """按 agent_matrix 角色 slug 取 (provider_model_id, system_prompt)。

    角色未绑定/查不到/异常 → (None, None)，由调用方降级到全局默认。
    一次查询同时取模型绑定与角色人设，避免每阶段多次 DB 命中。
    """
    try:
        from agent_matrix.models import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT provider_model_id, system_prompt FROM agent_matrix WHERE slug=%s",
                (slug,),
            ).fetchone()
        if not row:
            return None, None
        if isinstance(row, dict):
            return row.get("provider_model_id"), (row.get("system_prompt") or None)
        # 非 dict 行兜底（理论上不会，dict 游标）
        return row[0], (row[1] if len(row) > 1 else None)
    except Exception as exc:
        logger.warning("resolve_role failed: slug=%s err=%s", slug, exc)
        return None, None


def run_discussion_stage(stage, user_prompt, *, egress_guard=None, license_ids=None,
                         symbol="", temperature=0.3, max_tokens=8192,
                         module="agent_matrix", inject_role_prompt=True):
    """执行单个讨论阶段：解析该阶段绑定的角色，用其专属 Key/模型 调 LLM。

    Args:
        stage: 阶段名（planner/reviewer/revise/decider），用于查阶段→角色映射。
        user_prompt: 该阶段的用户消息（领域提示词由调用方提供）。
        egress_guard: 可选出境闸门回调(license_ids, symbol=)，由业务插件注入其专有闸门。
        license_ids: 商业源许可 id（透传给 egress_guard）。
        symbol: 标的（透传给 egress_guard）。
        inject_role_prompt: True 时把角色 system_prompt 注入为 system 消息（角色身份）；
                            False 时仅用 provider_model_id 选模型，不注入人设
                            （领域提示词本身已声明角色时用）。
    Returns:
        str，模型回复。
    """
    if egress_guard is not None:
        egress_guard(license_ids, symbol=symbol or "-")   # 闸门异常上抛，由调用方归类
    if not stage:
        # stage 缺失：无法做阶段→角色解析，显式告警并跳过配置读取（不白读一次）
        logger.warning("run_discussion_stage: 未指定 stage，跳过角色解析，降级 standard tier")
        stage_roles = {}
    else:
        stage_roles = load_stage_roles()
    role_slug = stage_roles.get(stage) if stage else None
    cfg = None
    sys_prompt = None
    if role_slug:
        pm_id, role_prompt = resolve_role(role_slug)
        if pm_id:
            cfg = {"provider_model_id": pm_id}
            if inject_role_prompt and role_prompt:
                sys_prompt = role_prompt
        else:
            logger.warning("discuss stage=%s role=%s 未绑定 provider_model_id，降级 standard", stage, role_slug)
    if cfg is None:
        from agent_matrix.model_resolver import resolve_model_args
        cfg = resolve_model_args({"strategy": "tier", "tier": "standard"})
    if not cfg.get("model_name") and cfg.get("model"):     # H-1 同款兼容映射
        cfg["model_name"] = cfg["model"]
    messages = []
    if sys_prompt:
        messages.append({"role": "system", "content": sys_prompt})
    messages.append({"role": "user", "content": user_prompt})
    from agent_matrix.engine import UnifiedLLM
    return UnifiedLLM(cfg).chat(messages, temperature=temperature,
                               max_tokens=max_tokens, module=module)
