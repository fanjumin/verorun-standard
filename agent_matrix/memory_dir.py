#!/usr/bin/env python3
"""WP-11：每 Agent 持久记忆文件（内核只读层）。

职责
----
按约定目录 `data/agent_memory/<agent_id>/MEMORY.md` 读取该 Agent 的持久
记忆，拼装为 system prompt 的一个**分节**（并存模式：与记忆插件经
orchestrator→PromptResolver→before_prompt_resolve 的动态注入各占一节，
互不覆盖）。

设计铁律
--------
1. **内核只读不写**：写入继续由记忆插件经 `AGENT_TASK_COMPLETED` 事件
   完成（agent_runner._emit_task_completed，任务收尾时抽取）——零重复建设、
   零新表、零 migration。
2. **fail-open**：文件缺失 / 读取异常 / 超长 / agent_id 非法，一律安全
   返回空串，绝不影响对话主流程。
3. 超长截断（不拒绝）：记忆宁可有残缺，也不因为一条过长记录丢掉全部。

目录先例：`data/agent_runs/`（WP-6 检查点）与 `data/media` 同约定。
"""

import os
import logging

logger = logging.getLogger(__name__)

# ── 目录与文件约定 ──
MEMORY_ROOT_NAME = ('data', 'agent_memory')
MEMORY_FILE_NAME = 'MEMORY.md'
DEFAULT_MAX_CHARS = 4000
TRUNCATION_MARKER = '\n...[truncated]'
# 分节头：标明来源（并存模式下与插件注入的检索记忆相区分）
SECTION_HEADER = '[Persistent Memory — {}]'


def memory_root():
    """记忆根目录（项目根 data/agent_memory/），与 _agent_runs_dir 同范式。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, *MEMORY_ROOT_NAME)


def _safe_agent_id(agent_id):
    """把 agent_id 收敛为单个安全路径分量。

    拒绝空值 / 路径穿越（'..'、含分隔符）——调用方传入的 agent_id 若不可
    信，宁可退回缺省名也不能越出目录。返回 None 表示不可用。
    """
    if agent_id is None:
        return None
    s = str(agent_id).strip()
    if not s:
        return None
    if '/' in s or '\\' in s or s in ('.', '..'):
        return None
    if os.path.basename(s) != s:
        return None
    return s


def memory_dir(agent_id):
    """该 Agent 的记忆目录；agent_id 非法时返回 None。"""
    safe = _safe_agent_id(agent_id)
    if safe is None:
        return None
    return os.path.join(memory_root(), safe)


def memory_path(agent_id):
    """该 Agent 的 MEMORY.md 路径（供管理端/插件定位）；非法返回 None。"""
    d = memory_dir(agent_id)
    if d is None:
        return None
    return os.path.join(d, MEMORY_FILE_NAME)


def load_memory_prompt(agent_id, max_chars=DEFAULT_MAX_CHARS) -> str:
    """读取并拼装持久记忆分节；任何不可用情形返回 ''（fail-open）。

    Args:
        agent_id: Agent 标识（单路径分量；含分隔符/穿越一律视为不可用）
        max_chars: 正文最大字符数；<=0 视为关闭（返回 ''）

    Returns:
        分节字符串（含来源标注头），或 ''（无记忆/关闭/异常）。
        拼接进 system prompt 由调用方负责（空串即原样）。
    """
    try:
        try:
            limit = int(max_chars)
        except (TypeError, ValueError):
            limit = DEFAULT_MAX_CHARS
        if limit <= 0:
            return ''
        path = memory_path(agent_id)
        if path is None:
            logger.warning('[MemoryDir] unsafe agent_id=%r, skip memory load', agent_id)
            return ''
        if not os.path.isfile(path):
            return ''
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            content = fh.read().strip()
        if not content:
            return ''
        if len(content) > limit:
            keep = max(0, limit - len(TRUNCATION_MARKER))
            content = content[:keep] + TRUNCATION_MARKER
            logger.info('[MemoryDir] memory truncated to %d chars (agent_id=%s)',
                        len(content), agent_id)
        return SECTION_HEADER.format(path) + '\n' + content
    except Exception as e:
        logger.warning('[MemoryDir] load failed (fail-open): %s', e)
        return ''
