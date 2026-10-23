#!/usr/bin/env python3
"""
Agent Tool Approval System (系统级内核定域，v1.2)
==================================================
职责：
  - TOOL_RISK 风险档定义（auto / approve_session / always）
  - 审批五模式：off / session / strict / accept_edits / dont_ask
    （后两档为 S3 新增的自动放行档，经 agent_approval_modes_v2_enabled
    灰度，关闭即回落 session）
  - ApprovalService 审批单读写（读/写独立 schema agent_tools）
  - resolve_tool_risk()   工具名 → 风险档匹配（含 MCP 前缀剥壳）
  - approval_pre_execute() `agent_tool.pre_execute` 钩子回调（fail-open 铁律）

数据库：审批单存独立 schema agent_tools (agent_tools_approvals) —— schema 名
       保留历史命名，Python 代码已全部迁回 agent_matrix 内核。
"""
import hashlib
import json
import logging
import re

logger = logging.getLogger('agent_matrix.approval')

# ── 风险档 ──────────────────────────────────────────────
#   auto            = 直接放行（仍受 A4 白名单约束）
#   approve_session = 首次落单并软阻塞；同任务内已批准则放行
#   always          = 每次调用都要审批
TOOL_RISK = {
    'file_read': 'auto',
    'file_list': 'auto',
    'file_write': 'approve_session',
    'file_edit': 'approve_session',
    'http_request': 'approve_session',
    'code_exec': 'always',
    # EM-3：Agent 对外发信属外发副作用，首次落单并软阻塞（同任务内批准后放行）
    'email_send': 'approve_session',
    'email_send_contact': 'approve_session',
}

# ── 钩子点 & 审批模式 ──────────────────────────────────
FILTER_PRE_EXECUTE = 'agent_tool.pre_execute'
APPROVAL_MODE_KEY = 'agent_tool_approval_mode'
DEFAULT_APPROVAL_MODE = 'session'
# session/strict/off 为既有模式；accept_edits/dont_ask 为 S3 新增的两档，
# 受 V2_FEATURE_KEY 灰度（关闭时回落 session，绝不静默提权 —— fail-closed）。
VALID_MODES = ('off', 'session', 'strict', 'accept_edits', 'dont_ask')
V2_MODES = ('accept_edits', 'dont_ask')
V2_FEATURE_KEY = 'agent_approval_modes_v2_enabled'

# accept_edits 档：这些 approve_session 风险工具在同任务内免批（文件编辑语义）；
# 经 resolve_tool_risk MCP 剥壳后的裸名匹配，其余 approve_session 风险仍落单。
SESSION_AUTO_EDIT_TOOLS = frozenset({'file_write', 'file_edit'})

# ── 注入模式最小集（内容层第二道防线）─────────────────────
# 只收「高置信破坏 / 外泄 / 持久化」模式，宁缺勿滥：误杀正常命令的代价
# （一张多余审批单）虽低，但会训练用户无脑批准，反而消解门禁。
# 命中效果：本次调用风险档视同 'always'（每次落审批单），不改 TOOL_RISK
# 静态表、不阻断执行（审批单本身仍是 fail-open 的软阻塞语义）。
INJECTION_PATTERNS = (
    ('ifs_override',   re.compile(r'\bIFS\s*=')),
    # rm -rf / 、rm -rf /* 、rm 后紧跟根路径
    ('rm_root',        re.compile(r'\brm\s+(-[a-zA-Z]+\s+)*/(\s|$|\*)')),
    ('pipe_to_shell',  re.compile(
        r'\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da)?sh\b')),
    ('base64_exec',    re.compile(
        r'\bbase64\b[^|;&]*(-d|--decode)\b[^|;&]*\|\s*(ba|z|da)?sh\b')),
    ('devtcp_shell',   re.compile(r'/dev/(tcp|udp)/')),
    ('cred_read',      re.compile(
        r'\b(cat|head|tail|less|more)\b[^;&|]*'
        r'(\.env\b|id_rsa\b|\.pem\b|\bcredentials?\b)')),
    # 路径即凭据文件（file_read/file_write 的 path 形态）：提示注入最经典的
    # 「读 .env 再外泄」落点。要求以该文件名结尾，避免误命中
    # .env.example / id_rsa.pub / my.pem.bak 这类同族但无害的名字。
    ('cred_path',      re.compile(r'(\.env|\.pem|id_rsa)$')),
    ('history_tamper', re.compile(
        r'\b(history\s+(-c|--clear)\b|unset\s+HISTFILE\b)')),
    ('env_exfil',      re.compile(
        r'\benv\b[^;&|]*\|\s*(curl|wget|nc|ncat|python)\b')),
    ('fork_bomb',      re.compile(r':\(\)\s*\{.*\}\s*;\s*:')),
    # 写入 authorized_keys = 提示注入最典型的持久化落点
    ('ssh_key_write',  re.compile(r'>\s*[^\s;|]*authorized_keys')),
)

# 参与检测的参数键（工具入参常见命名；未列键不检测）
_INJECTION_ARG_KEYS = ('command', 'cmd', 'script', 'code',
                       'path', 'filepath', 'content', 'url')


def detect_command_injection(tool, args):
    """对工具入参做注入模式扫描；命中返回模式名，未命中返回 None。

    fail-open 铁律：任何异常返回 None（绝不因检测器自身故障阻断工具）。
    只做字符串包含级匹配（正则区分度有限），定位是「升档审批」而非拦截，
    误报的代价只是一张多余审批单。

    已知盲区（本层不覆盖，需别的机制兜）：http_request 把数据发往外部域名。
    外泄目的地应由域名白名单管控，正则无法区分合法回调与 attacker 域名。
    """
    try:
        if not isinstance(args, dict):
            return None
        for key in _INJECTION_ARG_KEYS:
            val = args.get(key)
            if not isinstance(val, str) or not val:
                continue
            for name, pat in INJECTION_PATTERNS:
                if pat.search(val):
                    return name
        return None
    except Exception as e:
        logger.warning('injection detect failed, treat as clean: %s', e)
        return None

# ── 单例管理 ───────────────────────────────────────────
# agent_matrix 启动时调用 init_approval() 构造；插件 activate() 也会调一次（幂等）
_approval_svc = None
_approval_config = {}


def init_approval(config=None):
    """Initialize the approval system singleton. Idempotent."""
    global _approval_svc, _approval_config
    if config is not None:
        _approval_config = config if isinstance(config, dict) else {}
    if _approval_svc is None:
        _approval_svc = ApprovalService(_approval_config)
    return _approval_svc


def get_approval():
    """Return the singleton ApprovalService, or None if not initialized."""
    return _approval_svc


# ── 审批服务 ───────────────────────────────────────────
class ApprovalService:
    """审批单读写。操作独立 schema agent_tools，不触碰内核其他表。"""

    def __init__(self, config: dict):
        self._config = config or {}

    # ── 配置 ──────────────────────────────────────────────
    def _v2_modes_enabled(self) -> bool:
        """accept_edits/dont_ask 灰度开关（system_config）。

        与 planner_loop.is_replan_enabled 同范式：键不存在、值非法、DB
        不可达一律按关闭处理——新模式永不被隐式激活（fail-closed 提权门）。
        """
        try:
            from models import get_db
            with get_db() as conn:
                row = conn.execute(
                    "SELECT value FROM system_config"
                    " WHERE key = %s", (V2_FEATURE_KEY,)).fetchone()
            if not row:
                return False
            raw = row.get('value') if isinstance(row, dict) else row[0]
            return str(raw or '').strip().lower() in ('1', 'true', 'yes', 'on')
        except Exception as e:
            logger.warning('approval v2 feature read failed, treated as off: %s', e)
            return False

    def approval_mode(self) -> str:
        """审批模式判定。

        读取顺序：system_config(agent_tool_approval_mode) -> plugin.json config.approval_mode -> 默认。
        任何异常一律回退默认值（默认是「审批开启」而非「关闭」：
        降级到关闭会让高风险工具失去门禁，方向不安全）。
        accept_edits/dont_ask 为灰度模式：灰度开关关闭时，即使 DB 里配了
        该值也回落 session —— 新权限档不得因配置脏值而静默生效。
        """
        mode = ''
        v2_on = self._v2_modes_enabled()
        try:
            from models import get_db
            with get_db() as conn:
                row = conn.execute(
                    "SELECT value FROM system_config"
                    " WHERE key = 'agent_tool_approval_mode'").fetchone()
            if row:
                try:
                    raw = row['value']
                except (TypeError, KeyError, IndexError):
                    raw = row[0]
                mode = str(raw or '').strip().lower()
        except Exception as e:
            logger.warning('approval mode read failed, use default: %s', e)
        if mode in V2_MODES and not v2_on:
            logger.warning('approval mode %r configured while v2 feature off, '
                           'falling back to session', mode)
            mode = ''
        if mode not in VALID_MODES:
            mode = str(self._config.get('approval_mode') or '').strip().lower()
        if mode in V2_MODES and not v2_on:
            mode = ''
        if mode not in VALID_MODES:
            mode = DEFAULT_APPROVAL_MODE
        return mode

    def _ttl_minutes(self) -> int:
        try:
            return max(1, min(1440, int(self._config.get('approval_ttl_minutes', 30))))
        except (TypeError, ValueError):
            return 30

    @staticmethod
    def _digest(args) -> str:
        """入参摘要：只存摘要，不入全量入参，避免敏感内容落库。"""
        try:
            payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            payload = repr(type(args))
        return hashlib.sha256(payload.encode('utf-8', 'replace')).hexdigest()

    # ── 读写 ──────────────────────────────────────────────
    def find_grant(self, task_id, agent_id, tool_name):
        """approve_session 语义：同任务内是否已有未过期的批准单。"""
        if not task_id:
            return None
        try:
            from agent_matrix.approval_db import get_approval_db
            conn = get_approval_db()
        except Exception as e:
            logger.warning('approval lookup unavailable: %s', e)
            return None
        try:
            row = conn.execute(
                "SELECT id FROM agent_tools_approvals"
                " WHERE task_id = ? AND agent_id = ? AND tool_name = ?"
                "   AND status = 'approved'"
                "   AND (expires_at IS NULL OR expires_at > now())"
                " ORDER BY decided_at DESC LIMIT 1",
                (task_id, agent_id, tool_name)).fetchone()
            return row['id'] if row else None
        except Exception as e:
            logger.warning('approval lookup failed: %s', e)
            return None
        finally:
            conn.close()

    def request(self, task_id, agent_id, tool_name, args):
        """落一条 pending 审批单；同一三元组已有 pending 则复用。返回单号或 None。

        注意：PgConnection.close() 会回滚未提交事务，故此处必须显式 commit()。
        """
        digest = self._digest(args)
        try:
            from agent_matrix.approval_db import get_approval_db
            conn = get_approval_db()
        except Exception as e:
            logger.warning('approval record unavailable: %s', e)
            return None
        try:
            row = conn.execute(
                "SELECT id FROM agent_tools_approvals"
                " WHERE task_id = ? AND agent_id = ? AND tool_name = ?"
                "   AND status = 'pending'"
                " ORDER BY requested_at DESC LIMIT 1",
                (task_id, agent_id, tool_name)).fetchone()
            if row:
                return row['id']
            row = conn.execute(
                "INSERT INTO agent_tools_approvals"
                " (task_id, agent_id, tool_name, args_digest, status, reason, expires_at)"
                " VALUES (?, ?, ?, ?, 'pending', ?, now() + make_interval(mins => ?))"
                " RETURNING id",
                (task_id, agent_id, tool_name, digest, 'pending approval', self._ttl_minutes()),
            ).fetchone()
            conn.commit()
            return row['id'] if row else None
        except Exception as e:
            logger.warning('approval record failed: %s', e)
            try:
                conn.rollback()
            except Exception:
                pass
            return None
        finally:
            conn.close()


# ── 工具名 → 风险档匹配 ──────────────────────────────────
def resolve_tool_risk(tool_name: str):
    """Match TOOL_RISK by exact name or MCP-prefixed suffix.

    MCP-routed tool names look like 'mcp__agent_tools__<server>__<tool>'.
    This resolves bare names, prefixed names, and unknown tools uniformly.
    """
    risk = TOOL_RISK.get(tool_name)
    if risk is not None:
        return risk
    # MCP suffix extraction: last '__' segment is the actual tool name
    if tool_name.startswith('mcp__agent_tools__'):
        bare = tool_name.rsplit('__', 1)[-1]
        return TOOL_RISK.get(bare)
    return None


# ── pre_execute 钩子回调 ────────────────────────────────
def approval_pre_execute(gate):
    """`agent_tool.pre_execute` 过滤器回调（fail-open 铁律）。

    本回调抛出的任何异常都不得阻断工具执行 ——
    异常时原样返回 gate（内核侧另有一层兜底）。
    """
    try:
        if not isinstance(gate, dict):
            return gate
        tool = gate.get('tool') or ''
        risk = resolve_tool_risk(tool)
        # 内容层第二道防线：入参命中注入模式 → 本次视同 always 档。
        # 必须位于下方早退之前 —— 未知工具（risk=None，含全部 MCP 路由
        # 工具）原本完全不进门，此处是它们唯一的收口点。
        # 注：'off' 模式下整段门禁不干预（下方 return），注入检测同样不生效，
        # 这是既有设计（off = 关闭全部审批），未在此处改变。
        injection_hit = detect_command_injection(tool, gate.get('args'))
        if injection_hit and risk != 'always':
            logger.warning('injection pattern %r escalated tool %s to always',
                           injection_hit, tool)
            risk = 'always'
        if risk is None or risk == 'auto':
            return gate                       # 非本系统工具 / 低风险档 -> 不干预
        svc = get_approval()
        if svc is None:
            return gate                       # 服务未构造 -> 不干预
        mode = svc.approval_mode()
        if mode == 'off':
            return gate
        # 两列均为 varchar(64)（migrations/0001_init.sql:10-11）。调用方可能传
        # int（如 agent_runner 的 context['agent_id']），直接比较/写入会报
        # 「操作符不存在: character varying = integer」，导致落单失败并 fail-open
        # 放行，故此处统一归一为字符串并截断到列宽。
        task_id = str(gate.get('task_id') or '')[:64]
        agent_id = str(gate.get('agent_id') or '')[:64]
        # dont_ask：不弹任何审批——同任务已有批准单则放行；否则直接拒绝
        # （不挂 pending、不软阻塞）。
        if mode == 'dont_ask':
            if svc.find_grant(task_id, agent_id, tool):
                return gate
            blocked = dict(gate)
            blocked['allowed'] = False
            blocked['deny_reason'] = (
                f'Tool {tool} is auto-denied under "dont_ask" approval mode '
                f'(no interactive prompts). Approve it explicitly for this '
                f'task or switch the approval mode, then retry.')
            return blocked
        # accept_edits：文件写/改类（file_write/file_edit）同任务内免批。
        # MCP 路由工具名形如 mcp__agent_tools__<server>__<tool>，须用剥壳后
        # 裸名匹配免批集合（审批单记录仍用原始工具名，不同 server 的同名工具
        # 各自独立落单批准）；其余 approve_session 风险仍按 session 落单。
        if (mode == 'accept_edits' and risk == 'approve_session'
                and (tool in SESSION_AUTO_EDIT_TOOLS
                     or (tool.startswith('mcp__agent_tools__')
                         and tool.rsplit('__', 1)[-1]
                         in SESSION_AUTO_EDIT_TOOLS))):
            return gate
        if mode != 'strict' and risk == 'approve_session':
            if svc.find_grant(task_id, agent_id, tool):
                return gate                   # 同任务内已批准 -> 放行
        approval_id = svc.request(task_id, agent_id, tool, gate.get('args'))
        if not approval_id:
            return gate                       # 落单失败 -> 放行（fail-open）
        blocked = dict(gate)
        blocked['allowed'] = False
        blocked['deny_reason'] = (
            f'Tool {tool} requires admin approval (ticket #{approval_id}). '
            f'Please go to "Agent Tools" page to approve, then retry.')
        return blocked
    except Exception as e:
        logger.warning('approval gate failed, fail-open: %s', e)
        return gate
