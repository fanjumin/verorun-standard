#!/usr/bin/env python3
"""
VeroGuard 官方监控服务（Official Guardian Monitor）
====================================================
接收客户站点守护进程（veroguard.guardian）的加密心跳与命令回执，
落库供官方管理后台在线检查站点存活与完整性状态。

安全协议（与 veroguard/modules/communicator.py 严格对齐）:
  载荷加密: AES-256-GCM，密钥 = sha256(f"{PROBE_SECRET}:communication")
  请求签名: HMAC-SHA256(key=PROBE_SECRET, msg=ciphertext + nonce)
  防重放:   X-VG-Timestamp ±300s 窗口 + nonce 数据库唯一约束
密钥解析:  环境变量 PROBE_SECRET（全局）→ guardian_deployments.probe_secret（站点级）
"""
import base64
import hashlib
import hmac as _hmac
import json
import os
import secrets
import threading
import time as _time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from models.database import get_db

# 客户端文档承诺 5 分钟防重放窗口（communicator.py 模块注释）
REPLAY_WINDOW_SECONDS = 300
DEFAULT_HEARTBEAT_INTERVAL = int(os.getenv('GUARDIAN_HEARTBEAT_INTERVAL', '300'))
# 在线判定窗口：2 个心跳周期（默认 10 分钟）
ONLINE_WINDOW_SECONDS = int(os.getenv('GUARDIAN_ONLINE_WINDOW', str(DEFAULT_HEARTBEAT_INTERVAL * 2)))
HEARTBEAT_RETENTION_DAYS = int(os.getenv('GUARDIAN_HEARTBEAT_RETENTION_DAYS', '30'))

# 远程命令白名单（与 veroguard/modules/executor.py ALLOWED_ACTIONS 一致）
ALLOWED_ACTIONS = {'warn', 'lock_ai', 'lock_full', 'shutdown', 'self_destruct', 'update_config'}
DESTRUCTIVE_ACTIONS = {'shutdown', 'self_destruct'}

_TABLES_LOCK = threading.Lock()
_TABLES_READY = False


class GuardianAuthError(Exception):
    """心跳/回执验签或解密失败（协议层拒绝）。"""


def ensure_tables() -> None:
    """自愈建表（幂等，进程内只执行一次）。"""
    global _TABLES_READY
    if _TABLES_READY:
        return
    with _TABLES_LOCK:
        if _TABLES_READY:
            return
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS guardian_deployments (
                    id SERIAL PRIMARY KEY,
                    deployment_code VARCHAR(128) UNIQUE NOT NULL,
                    display_name VARCHAR(255) DEFAULT '',
                    site_url VARCHAR(255) DEFAULT '',
                    probe_secret TEXT DEFAULT '',
                    heartbeat_interval INTEGER DEFAULT %d,
                    notes TEXT DEFAULT '',
                    last_heartbeat_at TIMESTAMPTZ,
                    last_ip VARCHAR(64) DEFAULT '',
                    last_version VARCHAR(32) DEFAULT '',
                    last_integrity VARCHAR(16) DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """ % DEFAULT_HEARTBEAT_INTERVAL)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS guardian_heartbeats (
                    id SERIAL PRIMARY KEY,
                    deployment_code VARCHAR(128) NOT NULL,
                    integrity_status VARCHAR(16) DEFAULT 'unknown',
                    fingerprint TEXT DEFAULT '{}',
                    integrity TEXT DEFAULT '{}',
                    runtime TEXT DEFAULT '{}',
                    client_ts BIGINT DEFAULT 0,
                    ip VARCHAR(64) DEFAULT '',
                    nonce VARCHAR(64) NOT NULL,
                    received_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_guardian_heartbeats_nonce "
                "ON guardian_heartbeats(nonce)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_guardian_heartbeats_code "
                "ON guardian_heartbeats(deployment_code, received_at DESC)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS guardian_commands (
                    id SERIAL PRIMARY KEY,
                    command_id VARCHAR(64) UNIQUE NOT NULL,
                    deployment_code VARCHAR(128) NOT NULL,
                    action VARCHAR(32) NOT NULL,
                    params TEXT DEFAULT '{}',
                    status VARCHAR(16) DEFAULT 'pending',
                    result TEXT DEFAULT '',
                    created_by VARCHAR(64) DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    sent_at TIMESTAMPTZ,
                    acked_at TIMESTAMPTZ
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_guardian_commands_code "
                "ON guardian_commands(deployment_code, status, created_at)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS guardian_security_events (
                    id SERIAL PRIMARY KEY,
                    deployment_code VARCHAR(128) NOT NULL,
                    event_type VARCHAR(64) DEFAULT '',
                    severity VARCHAR(16) DEFAULT 'info',
                    detail TEXT DEFAULT '{}',
                    received_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_guardian_events_code "
                "ON guardian_security_events(deployment_code, received_at DESC)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subscription_deployments (
                    code VARCHAR(128) PRIMARY KEY,
                    hostname VARCHAR(255) DEFAULT '',
                    version VARCHAR(32) DEFAULT '',
                    valid BOOLEAN DEFAULT FALSE,
                    days_remaining INTEGER DEFAULT 0,
                    expires_at TIMESTAMPTZ,
                    message VARCHAR(255) DEFAULT '',
                    plan VARCHAR(64) DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    last_seen_at TIMESTAMPTZ
                )
            """)
            conn.commit()
        _TABLES_READY = True


# ── 协议层：验签 + 解密 ─────────────────────────────────────────────

def _derive_key(secret: str) -> bytes:
    """与客户端 _derive_key(secret, 'communication') 完全一致。"""
    return hashlib.sha256(f"{secret}:communication".encode()).digest()


def _candidate_secrets() -> List[str]:
    """全局 PROBE_SECRET 优先，其后是各站点注册的站点级密钥。"""
    out: List[str] = []
    g = os.getenv('PROBE_SECRET', '')
    if g:
        out.append(g)
    try:
        ensure_tables()
        with get_db() as conn:
            rows = conn.execute(
                "SELECT probe_secret FROM guardian_deployments "
                "WHERE probe_secret <> ''").fetchall()
        out.extend(r['probe_secret'] for r in rows or [])
    except Exception:
        pass
    # 去重保序
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def verify_guardian_request(raw: bytes, nonce_b64: str, signature: str,
                            ts_str: str) -> Tuple[Dict[str, Any], str]:
    """验证客户端心跳/回执请求。

    Returns:
        (payload_dict, matched_secret)
    Raises:
        GuardianAuthError: 时间窗/签名/解密任一失败。
    """
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        ts = int(str(ts_str or '0'))
    except (TypeError, ValueError):
        raise GuardianAuthError('missing or invalid X-VG-Timestamp')
    if abs(int(_time.time()) - ts) > REPLAY_WINDOW_SECONDS:
        raise GuardianAuthError('timestamp outside replay window')

    try:
        nonce = base64.b64decode(nonce_b64 or '')
    except Exception:
        raise GuardianAuthError('invalid X-VG-Nonce')
    if len(nonce) != 12:
        raise GuardianAuthError('invalid nonce length')
    if not signature or not raw:
        raise GuardianAuthError('missing signature or body')

    last_err: Optional[Exception] = None
    for secret in _candidate_secrets():
        expected = _hmac.new(secret.encode(), raw + nonce, hashlib.sha256).hexdigest()
        if not secrets.compare_digest(expected, str(signature)):
            last_err = GuardianAuthError('signature mismatch')
            continue
        try:
            plaintext = AESGCM(_derive_key(secret)).decrypt(nonce, raw, None)
            return json.loads(plaintext.decode('utf-8')), secret
        except (InvalidTag, ValueError, UnicodeDecodeError) as e:
            last_err = e
            continue
    raise last_err or GuardianAuthError('signature mismatch')


# ── 心跳落库 ─────────────────────────────────────────────────────────

def record_heartbeat(payload: Dict[str, Any], ip: str) -> Dict[str, Any]:
    """落库一次心跳，返回响应体（含待执行远程命令）。"""
    ensure_tables()
    code = str(payload.get('deployment_code') or '').strip()
    if not code:
        raise GuardianAuthError('deployment_code required')
    nonce = payload.get('_nonce') or ''
    integrity = payload.get('integrity') or {}
    if not isinstance(integrity, dict):
        integrity = {}
    integrity_status = str(integrity.get('status') or 'unknown')[:16]
    runtime = payload.get('runtime') or {}
    if not isinstance(runtime, dict):
        runtime = {}
    version = str(runtime.get('version') or runtime.get('app_version') or '')[:32]
    client_ts = int(payload.get('timestamp') or 0)

    with get_db() as conn:
        # 站点档案 upsert（未注册站点自动建档，官方端可见后可补配密钥/备注）
        conn.execute("""
            INSERT INTO guardian_deployments
                (deployment_code, last_heartbeat_at, last_ip, last_version,
                 last_integrity, updated_at)
            VALUES (%s, NOW(), %s, %s, %s, NOW())
            ON CONFLICT (deployment_code) DO UPDATE SET
                last_heartbeat_at = NOW(),
                last_ip = EXCLUDED.last_ip,
                last_version = EXCLUDED.last_version,
                last_integrity = EXCLUDED.last_integrity,
                updated_at = NOW()
        """, (code, ip[:64], version, integrity_status))
        # 心跳明细（nonce 唯一约束天然防重放）
        conn.execute("""
            INSERT INTO guardian_heartbeats
                (deployment_code, integrity_status, fingerprint, integrity,
                 runtime, client_ts, ip, nonce)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (code, integrity_status,
              json.dumps(payload.get('fingerprint') or {}, ensure_ascii=False),
              json.dumps(integrity, ensure_ascii=False),
              json.dumps(runtime, ensure_ascii=False),
              client_ts, ip[:64], nonce))
        # 完整性异常 → 安全事件流
        if integrity_status in ('warn', 'critical'):
            conn.execute("""
                INSERT INTO guardian_security_events
                    (deployment_code, event_type, severity, detail)
                VALUES (%s, 'integrity', %s, %s)
            """, (code, integrity_status, json.dumps(integrity, ensure_ascii=False)))
        # 取待下发命令并标记 sent
        cmd_rows = conn.execute("""
            SELECT command_id, action, params FROM guardian_commands
            WHERE deployment_code = %s AND status = 'pending'
            ORDER BY created_at ASC LIMIT 10
        """, (code,)).fetchall()
        commands = []
        for r in cmd_rows or []:
            try:
                params = json.loads(r['params'] or '{}')
            except (TypeError, ValueError):
                params = {}
            commands.append({
                'command_id': r['command_id'],
                'action': r['action'],
                'params': params,
            })
            conn.execute(
                "UPDATE guardian_commands SET status='sent', sent_at=NOW() "
                "WHERE command_id=%s AND status='pending'", (r['command_id'],))
        conn.commit()

    # 明细保留期清理（每次心跳顺手执行，命中索引代价可忽略）
    try:
        with get_db() as conn:
            conn.execute(
                "DELETE FROM guardian_heartbeats WHERE received_at < NOW() "
                "- INTERVAL '%d days'" % HEARTBEAT_RETENTION_DAYS)
            conn.commit()
    except Exception:
        pass

    interval = _deployment_interval(code)
    resp: Dict[str, Any] = {'status': 'ok', 'commands': commands}
    if interval and interval != DEFAULT_HEARTBEAT_INTERVAL:
        resp['next_heartbeat_seconds'] = interval
    return resp


def _deployment_interval(code: str) -> int:
    try:
        with get_db() as conn:
            row = conn.execute(
                'SELECT heartbeat_interval FROM guardian_deployments '
                'WHERE deployment_code=%s', (code,)).fetchone()
        return int(row['heartbeat_interval'] or 0) if row else 0
    except Exception:
        return 0


def record_ack(payload: Dict[str, Any]) -> Dict[str, Any]:
    """命令回执落库：executed / failed 更新 guardian_commands。"""
    ensure_tables()
    code = str(payload.get('deployment_code') or '').strip()
    command_id = str(payload.get('command_id') or '').strip()
    status = str(payload.get('status') or '').strip()
    if status not in ('executed', 'failed'):
        status = 'failed'
    if not code or not command_id:
        raise GuardianAuthError('deployment_code and command_id required')
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM guardian_commands WHERE command_id=%s AND "
            "deployment_code=%s", (command_id, code)).fetchone()
        if not row:
            return {'status': 'ok', 'acknowledged': False}
        conn.execute(
            "UPDATE guardian_commands SET status=%s, result=%s, acked_at=NOW() "
            "WHERE command_id=%s", (status,
                                    str(payload.get('result') or '')[:2000],
                                    command_id))
        conn.commit()
    return {'status': 'ok', 'acknowledged': True}


# ── 订阅心跳（/api/subscription/heartbeat）───────────────────────────

def subscription_heartbeat(data: Dict[str, Any]) -> Dict[str, Any]:
    """客户端 auth-center/services/license_service.refresh() 的服务端对端。

    客户端期望: resp.json()['data'] = {valid, days_remaining, message, expires_at}
    """
    ensure_tables()
    code = str(data.get('code') or '').strip()
    if not code:
        return {'success': False,
                'data': {'valid': False, 'days_remaining': 0,
                         'message': 'deployment code required'}}
    hostname = str(data.get('hostname') or '')[:255]
    version = str(data.get('version') or '')[:32]
    with get_db() as conn:
        row = conn.execute(
            'SELECT valid, expires_at, days_remaining, message, plan '
            'FROM subscription_deployments WHERE code=%s', (code,)).fetchone()
        if not row:
            return {'success': True,
                    'data': {'valid': False, 'days_remaining': 0,
                             'message': 'unknown deployment code',
                             'expires_at': None}}
        expires_at = row['expires_at']
        valid = bool(row['valid'])
        if valid and expires_at is not None:
            exp = expires_at if isinstance(expires_at, datetime) else None
            if exp is not None:
                exp = exp.replace(tzinfo=exp.tzinfo) if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
                valid = exp.timestamp() > _time.time()
                days_remaining = max(0, int((exp.timestamp() - _time.time()) // 86400))
            else:
                days_remaining = int(row['days_remaining'] or 0)
        else:
            days_remaining = int(row['days_remaining'] or 0) if valid else 0
        conn.execute(
            'UPDATE subscription_deployments SET hostname=%s, version=%s, '
            'last_seen_at=NOW() WHERE code=%s', (hostname, version, code))
        conn.commit()
    return {'success': True,
            'data': {'valid': valid, 'days_remaining': days_remaining,
                     'message': row['message'] or '',
                     'expires_at': str(expires_at) if expires_at else None}}


# ── 管理端查询 / 操作 ────────────────────────────────────────────────

def list_deployments() -> List[Dict[str, Any]]:
    """站点列表 + 在线状态（最后心跳在 ONLINE_WINDOW 内视为在线）。"""
    ensure_tables()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT d.id, d.deployment_code, d.display_name, d.site_url,
                   d.notes, d.heartbeat_interval,
                   d.last_heartbeat_at, d.last_ip, d.last_version,
                   d.last_integrity, d.created_at,
                   (SELECT COUNT(1) FROM guardian_commands c
                     WHERE c.deployment_code = d.deployment_code
                       AND c.status = 'pending') AS pending_commands
            FROM guardian_deployments d
            ORDER BY d.last_heartbeat_at DESC NULLS LAST, d.deployment_code
        """).fetchall()
    now = _time.time()
    out = []
    for r in rows or []:
        last = r['last_heartbeat_at']
        if isinstance(last, datetime):
            ts = last.timestamp()
            online = (now - ts) <= ONLINE_WINDOW_SECONDS
            last_str = last.isoformat()
        else:
            online, last_str = False, ''
        out.append({
            'id': r['id'], 'deployment_code': r['deployment_code'],
            'display_name': r['display_name'], 'site_url': r['site_url'],
            'notes': r['notes'], 'heartbeat_interval': r['heartbeat_interval'],
            'online': online, 'last_heartbeat_at': last_str,
            'last_ip': r['last_ip'], 'last_version': r['last_version'],
            'last_integrity': r['last_integrity'] or 'unknown',
            'pending_commands': int(r['pending_commands'] or 0),
            'never_reported': not bool(last),
        })
    return out


def upsert_deployment(deployment_code: str, probe_secret: str = '',
                      display_name: str = '', site_url: str = '',
                      notes: str = '', heartbeat_interval: int = 0) -> Dict[str, Any]:
    """注册/更新站点（站点级 PROBE_SECRET 仅管理员可写）。"""
    ensure_tables()
    code = (deployment_code or '').strip()
    if not code:
        raise ValueError('deployment_code required')
    interval = int(heartbeat_interval or 0)
    with get_db() as conn:
        existing = conn.execute(
            'SELECT probe_secret FROM guardian_deployments WHERE deployment_code=%s',
            (code,)).fetchone()
        if existing is None:
            conn.execute("""
                INSERT INTO guardian_deployments
                    (deployment_code, display_name, site_url, probe_secret,
                     heartbeat_interval, notes)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (code, display_name[:255], site_url[:255],
                  probe_secret.strip(), interval or DEFAULT_HEARTBEAT_INTERVAL,
                  notes))
        else:
            conn.execute("""
                UPDATE guardian_deployments SET
                    display_name=%s, site_url=%s, notes=%s,
                    heartbeat_interval=%s,
                    probe_secret=CASE WHEN %s <> '' THEN %s
                                      ELSE probe_secret END,
                    updated_at=NOW()
                WHERE deployment_code=%s
            """, (display_name[:255], site_url[:255], notes,
                  interval or DEFAULT_HEARTBEAT_INTERVAL,
                  probe_secret.strip(), probe_secret.strip(), code))
        conn.commit()
    return {'deployment_code': code, 'stored': True}


def recent_heartbeats(deployment_code: str, limit: int = 50) -> List[Dict[str, Any]]:
    ensure_tables()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT integrity_status, runtime, ip, client_ts, received_at
            FROM guardian_heartbeats WHERE deployment_code=%s
            ORDER BY received_at DESC LIMIT %d
        """ % max(1, min(int(limit), 200)), (deployment_code,)).fetchall()
    out = []
    for r in rows or []:
        try:
            runtime = json.loads(r['runtime'] or '{}')
        except (TypeError, ValueError):
            runtime = {}
        out.append({
            'integrity_status': r['integrity_status'], 'ip': r['ip'],
            'client_ts': r['client_ts'],
            'received_at': r['received_at'].isoformat() if r['received_at'] else '',
            'uptime': runtime.get('uptime'), 'version': runtime.get('version'),
        })
    return out


def recent_events(limit: int = 30) -> List[Dict[str, Any]]:
    ensure_tables()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT deployment_code, event_type, severity, detail, received_at
            FROM guardian_security_events
            ORDER BY received_at DESC LIMIT %d
        """ % max(1, min(int(limit), 100))).fetchall()
    out = []
    for r in rows or []:
        try:
            detail = json.loads(r['detail'] or '{}')
        except (TypeError, ValueError):
            detail = {}
        out.append({'deployment_code': r['deployment_code'],
                    'event_type': r['event_type'], 'severity': r['severity'],
                    'detail': detail,
                    'received_at': r['received_at'].isoformat() if r['received_at'] else ''})
    return out


def enqueue_command(deployment_code: str, action: str, params: Dict[str, Any],
                    created_by: str = '') -> Dict[str, Any]:
    """下发远程命令（心跳到达时送达；破坏性命令需站点本地 VG_OPS_CONFIRM）。"""
    ensure_tables()
    code = (deployment_code or '').strip()
    action = (action or '').strip()
    if not code:
        raise ValueError('deployment_code required')
    if action not in ALLOWED_ACTIONS:
        raise ValueError('action not allowed')
    command_id = secrets.token_hex(8)
    with get_db() as conn:
        exists = conn.execute(
            'SELECT 1 FROM guardian_deployments WHERE deployment_code=%s',
            (code,)).fetchone()
        if not exists:
            raise ValueError('deployment not registered')
        conn.execute("""
            INSERT INTO guardian_commands
                (command_id, deployment_code, action, params, created_by)
            VALUES (%s, %s, %s, %s, %s)
        """, (command_id, code, action,
              json.dumps(params or {}, ensure_ascii=False), created_by[:64]))
        conn.commit()
    return {'command_id': command_id, 'action': action,
            'destructive': action in DESTRUCTIVE_ACTIONS}


def list_commands(limit: int = 50) -> List[Dict[str, Any]]:
    ensure_tables()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT command_id, deployment_code, action, params, status,
                   result, created_by, created_at, sent_at, acked_at
            FROM guardian_commands ORDER BY created_at DESC LIMIT %d
        """ % max(1, min(int(limit), 200))).fetchall()
    out = []
    for r in rows or []:
        try:
            params = json.loads(r['params'] or '{}')
        except (TypeError, ValueError):
            params = {}
        out.append({
            'command_id': r['command_id'], 'deployment_code': r['deployment_code'],
            'action': r['action'], 'params': params, 'status': r['status'],
            'result': r['result'], 'created_by': r['created_by'],
            'created_at': r['created_at'].isoformat() if r['created_at'] else '',
            'sent_at': r['sent_at'].isoformat() if r['sent_at'] else '',
            'acked_at': r['acked_at'].isoformat() if r['acked_at'] else '',
        })
    return out


def list_subscriptions() -> List[Dict[str, Any]]:
    ensure_tables()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT code, hostname, version, valid, days_remaining, expires_at,
                   message, plan, created_at, last_seen_at
            FROM subscription_deployments ORDER BY code
        """).fetchall()
    out = []
    for r in rows or []:
        out.append({
            'code': r['code'], 'hostname': r['hostname'], 'version': r['version'],
            'valid': bool(r['valid']), 'days_remaining': int(r['days_remaining'] or 0),
            'expires_at': r['expires_at'].isoformat() if r['expires_at'] else '',
            'message': r['message'], 'plan': r['plan'],
            'last_seen_at': r['last_seen_at'].isoformat() if r['last_seen_at'] else '',
        })
    return out


def upsert_subscription(code: str, valid: bool, days_remaining: int = 0,
                        expires_at: str = '', message: str = '',
                        plan: str = '') -> Dict[str, Any]:
    """登记/更新站点订阅授权（官方端管理动作，心跳接口只读不改）。"""
    ensure_tables()
    c = (code or '').strip()
    if not c:
        raise ValueError('code required')
    exp = (expires_at or '').strip() or None
    with get_db() as conn:
        conn.execute("""
            INSERT INTO subscription_deployments
                (code, valid, days_remaining, expires_at, message, plan)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (code) DO UPDATE SET
                valid = EXCLUDED.valid,
                days_remaining = EXCLUDED.days_remaining,
                expires_at = EXCLUDED.expires_at,
                message = EXCLUDED.message,
                plan = EXCLUDED.plan
        """, (c, bool(valid), int(days_remaining or 0), exp,
              message[:255], plan[:64]))
        conn.commit()
    return {'code': c, 'stored': True}
