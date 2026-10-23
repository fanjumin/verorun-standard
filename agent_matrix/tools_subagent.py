#!/usr/bin/env python3
"""WP-8: 子代理同循环工具（Agent-as-Tool）—— spawn_subagent 实现体。

设计对齐方案 WP-8：
  - 独立 run_id：子代理执行生成独立 run_id（checkpoint 文件隔离）。
  - 独立预算：子代理消费按子 task_id（<parent>__sub）写入 agent_token_logs，
    与父预算隔离聚合（额度继承父 task.token_budget，但计数独立）。
  - 工具白名单 = 父白名单 ∩ 子角色 allowed_tools：子 AgentRunner 的
    allowed_tools_parsed 取交集，杜绝父未授权工具经子代理间接执行。
  - 单循环复用：内部构造子 AgentRunner，经 _run_react_loop → 同一 iter_react()。
  - 深度上限 2 层（MAX_SUBAGENT_DEPTH）：允许「分解 → 子分解」一层；再深
    直接拒绝。放开的这一层由白名单交集 + 独立 run_id + gates 闸门 +
    token 预算四道防线兜底，不再单靠硬深度。
  - 计费归集：子 task_id = f'{parent_task_id}__sub'。

工具由 agent_matrix/tools.py 注册（schema + executor + 不可中断），
受父 agent 的 allowed_tools 白名单管控。
"""
import json
import logging
import uuid

logger = logging.getLogger(__name__)

MAX_SUBAGENT_DEPTH = 2   # 允许一层再分解；递归风暴由 gates 闸门 + token 预算兜底


def _tool_spawn_subagent(args, context=None):
    """spawn_subagent 工具实现体：派发子任务到专业子代理，返回其最终文本。

    Args:
        args: {'agent': slug/name, 'task': str, 'expected_output': str(可选)}
        context: 父执行上下文 dict（agent_id/task_id/name/task_phase/
                 token_budget/subagent_depth/allowed_tools_parsed）
    Returns:
        str：子代理最终文本或 Error 前缀错误。
    """
    try:
        agent_ref = str(args.get('agent', '') or '').strip()
        task = str(args.get('task', '') or '').strip()
        if not agent_ref or not task:
            return 'Error: spawn_subagent requires non-empty "agent" and "task"'
        expected = str(args.get('expected_output', '') or '').strip()

        depth = int((context or {}).get('subagent_depth', 0) or 0)
        if depth >= MAX_SUBAGENT_DEPTH:
            return (f'Error: sub-agent recursion depth exceeded '
                    f'(max {MAX_SUBAGENT_DEPTH})')

        from agent_matrix.models import get_agent_by_slug, get_agent_by_name, get_agent
        agent_config = get_agent_by_slug(agent_ref)
        if agent_config is None:
            agent_config = get_agent_by_name(agent_ref)
        if agent_config is None and str(agent_ref).isdigit():
            agent_config = get_agent(int(agent_ref))
        if not agent_config:
            return f'Error: sub-agent not found: {agent_ref}'

        return _execute_subagent(agent_config, task, expected, context, depth)
    except Exception as e:
        logger.warning('[spawn_subagent] failed: %s', e)
        return f'Error: spawn_subagent failed: {e}'


def _execute_subagent(agent_config, task, expected, context, depth):
    """在子 AgentRunner 内走同一 iter_react 循环，返回终态文本。"""
    from agent_matrix.agent_runner import AgentRunner
    from agent_matrix.tools import get_tools_for_agent, resolve_agent_tools
    from agent_matrix.loop_gates import build_gates

    parent_task_id = str((context or {}).get('task_id') or 'sub')
    sub_task_id = f'{parent_task_id}__sub'

    # 白名单交集：父白名单 ∩ 子角色 allowed_tools
    parent_allowed = (context or {}).get('allowed_tools_parsed') or []
    parent_allowed = list(parent_allowed) if isinstance(parent_allowed, list) else []
    child_allowed = resolve_agent_tools(
        agent_config, agent_config.get('allowed_tools') or [])
    permitted = ([t for t in child_allowed if t in set(parent_allowed)]
                 if parent_allowed else [])

    cfg = dict(agent_config)
    cfg['allowed_tools_parsed'] = permitted if permitted else None

    runner = AgentRunner(cfg)
    engine = runner._get_engine()
    if not engine or not engine.is_ready():
        return (f'Error: sub-agent engine not ready '
                f'({cfg.get("provider")}/{cfg.get("model_name")})')

    user_query = task
    if expected:
        user_query = f'{task}\n\nExpected output: {expected}'

    tools_schemas = get_tools_for_agent(permitted) if permitted else []
    _max_rounds = int(cfg.get('max_tool_rounds') or cfg.get('max_turns') or 5)

    sub_task = {
        'task_id': sub_task_id,
        'token_budget': (context or {}).get('token_budget') or 0,
    }
    gates = build_gates(sub_task, sub_task_id)
    run_id = 'sub-' + uuid.uuid4().hex[:12]

    runner._subagent_depth = depth + 1   # 子代理内再派发将触发深度拦截
    sub_logs = []
    text = runner._run_react_loop(
        engine, user_query, [], tools_schemas, sub_logs, sub_task_id,
        max_rounds=_max_rounds, task_phase=(context or {}).get('task_phase', ''),
        gates=gates, run_id=run_id)
    return text if isinstance(text, str) else str(text)
