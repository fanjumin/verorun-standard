#!/usr/bin/env python3
"""Local auth routes — offline password management for the desktop standalone edition.

桌面单机版没有短信/邮件通道，现有改密接口（/user/password/set、
/admin/admins/<uid>、/reset-password）全部依赖短信，离线环境不可用。
本蓝图只提供不依赖任何外部通道的本地密码能力：

  GET  /api/admin/local/status              账户状态（是否已设密 / 是否已配置恢复码）
  PUT  /api/admin/local/username            修改本地管理员用户名（3-20 字符，30 天冷却）
  POST /api/admin/local/password/first-set  首次设密（password_changed_at IS NULL 时）
  POST /api/admin/local/password/change     旧密码改密并重新签发 token
  POST /api/admin/local/recovery/issue      签发一次性恢复码（明文只返回一次）
  POST /api/admin/local/recovery/reset      用恢复码自助重置密码

门控：仅 VR_LOCAL_MODE=1 且请求来自本机回环地址时可用，其余一律 404。
不使用 VR_EDITION 判断，避免线上部署误开。
"""
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time

from flask import Blueprint, jsonify, request

from bootstrap import clear_first_run_credentials

logger = logging.getLogger(__name__)

local_auth_bp = Blueprint('local_auth', __name__, url_prefix='/api/admin/local')

# 仅允许本机回环地址（含 IPv4-mapped IPv6 与 localhost 字面值）
_LOCAL_ADDRS = frozenset(('127.0.0.1', '::1', 'localhost', '::ffff:127.0.0.1'))

# 恢复码字母表：去掉 0/O/1/I 等易混淆字符
_RECOVERY_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
_RECOVERY_LENGTH = 16

# 恢复码尝试限流：同一 username+IP 10 分钟内最多 5 次失败
_RECOVERY_MAX_ATTEMPTS = 5
_RECOVERY_WINDOW_SECONDS = 600
_recovery_failures = {}
_recovery_lock = threading.Lock()

# 与现有登录一致：pbkdf2:sha256:600000:<salt_hex>:<hex_digest>
_PBKDF2_ITERATIONS = 600000


# ── 响应构造 ──
def _ok(data=None):
    return jsonify({'ok': True, 'data': data or {}})


def _err(code, error, status=400):
    return jsonify({'ok': False, 'code': code, 'error': error}), status


def _not_found():
    return jsonify({'ok': False, 'error': 'Not found'}), 404


# ── 本地门控 ──
def _is_local_request():
    """仅 VR_LOCAL_MODE=1 且来源为本机回环地址时返回 True。"""
    return (os.environ.get('VR_LOCAL_MODE') == '1'
            and (request.remote_addr or '') in _LOCAL_ADDRS)


@local_auth_bp.before_request
def _guard_local_only():
    """所有端点共用的本地门控：非本地单机环境一律返回 404。"""
    if not _is_local_request():
        return _not_found()
    return None


# ── 密码哈希（与登录流程完全一致）──
def _hash_password(password):
    """生成 pbkdf2:sha256:600000:<salt>:<hash>（salt 随机）。"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        'sha256', password.encode(), salt.encode(), _PBKDF2_ITERATIONS).hex()
    return f'pbkdf2:sha256:{_PBKDF2_ITERATIONS}:{salt}:{digest}'


def _verify_password(stored, password):
    """校验密码；兼容 pbkdf2 自有格式与旧 werkzeug 哈希。"""
    if not stored or not password:
        return False
    parts = stored.split(':')
    if len(parts) == 5 and parts[0] == 'pbkdf2' and parts[1] == 'sha256':
        try:
            check = hashlib.pbkdf2_hmac(
                'sha256', password.encode(), parts[3].encode(),
                int(parts[2])).hex()
            return hmac.compare_digest(parts[4], check)
        except Exception:
            return False
    try:
        from werkzeug.security import check_password_hash
        return bool(check_password_hash(stored, password))
    except Exception:
        return False


# ── 恢复码 ──
def _generate_recovery_code():
    """生成 XXXX-XXXX-XXXX-XXXX 格式的 16 位恢复码（无易混淆字符）。"""
    chars = [secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_LENGTH)]
    return '-'.join(''.join(chars[i:i + 4]) for i in range(0, _RECOVERY_LENGTH, 4))


def _normalize_recovery_code(code):
    """去掉非字母数字字符并转大写，用于比对。"""
    return re.sub(r'[^A-Za-z0-9]', '', code or '').upper()


# ── 恢复码限流（模块级内存字典）──
def _recovery_rate_key(username, ip):
    return f'{(username or "").lower()}|{ip or ""}'


def _recovery_rate_limited(key, now=None):
    """该 key 在窗口内失败次数是否已达上限。"""
    now = time.time() if now is None else now
    with _recovery_lock:
        entry = _recovery_failures.get(key)
        if not entry:
            return False
        count, started = entry
        if now - started >= _RECOVERY_WINDOW_SECONDS:
            _recovery_failures.pop(key, None)
            return False
        return count >= _RECOVERY_MAX_ATTEMPTS


def _recovery_record_failure(key, now=None):
    """记录一次恢复码校验失败；窗口过期则重新计数。"""
    now = time.time() if now is None else now
    with _recovery_lock:
        count, started = _recovery_failures.get(key, (0, now))
        if now - started >= _RECOVERY_WINDOW_SECONDS:
            count, started = 0, now
        _recovery_failures[key] = (count + 1, started)


def _recovery_clear_failures(key):
    with _recovery_lock:
        _recovery_failures.pop(key, None)


# ── 审计 ──
def _audit(admin_id, action, target_id, detail=''):
    """写 admin_logs；失败静默，绝不影响主流程。"""
    try:
        from models import get_db
        with get_db() as conn:
            conn.execute(
                'INSERT INTO admin_logs (admin_id, action, target_type, target_id, detail, ip_address) '
                'VALUES (%s,%s,%s,%s,%s,%s)',
                (admin_id, action, 'user', str(target_id), detail,
                 request.remote_addr or '')
            )
            conn.commit()
    except Exception:
        logger.exception('local_auth audit write failed: %s', action)


# ── 认证 ──
def _load_admin_user(user_id):
    """按 user_id 读取本地账户所需字段；不存在返回 None。"""
    from models import get_db
    with get_db() as conn:
        row = conn.execute(
            'SELECT id, username, phone, password_hash, password_changed_at, '
            'recovery_code_hash FROM users WHERE id=%s', (user_id,)
        ).fetchone()
    return dict(row) if row else None


def _require_auth():
    """校验 Authorization Bearer JWT（is_admin 且用户存在）。失败返回 401 响应。"""
    auth = request.headers.get('Authorization', '') or ''
    token = auth[7:] if auth.startswith('Bearer ') else ''
    from services.jwt_service import validate_token
    payload = validate_token(token) if token else None
    if not payload or not payload.get('is_admin'):
        return None, None, _err('UNAUTHORIZED', 'Unauthorized', 401)
    user = _load_admin_user(payload.get('user_id'))
    if not user:
        return None, None, _err('UNAUTHORIZED', 'Unauthorized', 401)
    return payload, user, None


def _policy_error(password):
    """按 IAM v2 策略校验新密码；不通过时返回 400 响应。"""
    from services.password_validator import validate_password
    result = validate_password(password or '')
    if result['valid']:
        return None
    return _err('PASSWORD_POLICY', '; '.join(result['errors']), 400)


def _admin_role(user_id):
    """读取 admin_profiles.role，缺失时回退 super_admin。"""
    try:
        from models import get_db
        with get_db() as conn:
            row = conn.execute(
                'SELECT role FROM admin_profiles WHERE user_id=%s', (user_id,)
            ).fetchone()
        return (row or {}).get('role') or 'super_admin'
    except Exception:
        logger.exception('local_auth: failed to load admin role for user=%s', user_id)
        return 'super_admin'


def _issue_token_after_revoke(user):
    """吊销该用户全部旧 token 后重新签发 admin token。

    revoke_all_user_tokens 写入毫秒级吊销时间戳，validate_token 以
    `iat_ms <= revoked` 判定失效；同毫秒内签发的新 token 会被立即判为
    已吊销，因此等到下一毫秒再签发。
    """
    from services.jwt_service import revoke_all_user_tokens
    from services.session_service import issue_auth_session
    revoke_all_user_tokens(user['id'])
    revoked_ms = int(time.time() * 1000)
    while int(time.time() * 1000) <= revoked_ms:
        time.sleep(0.001)
    session = issue_auth_session(
        user['id'], user.get('phone') or None,
        app_name='admin', is_admin=True, role=_admin_role(user['id']))
    return session['token']


# ── 端点 ──
@local_auth_bp.route('/status', methods=['GET'])
def local_status():
    """返回本地账户设密/恢复码状态。"""
    _, user, err = _require_auth()
    if err:
        return err
    return _ok({
        'username': user.get('username') or '',
        'password_set': user.get('password_changed_at') is not None,
        'recovery_configured': bool(user.get('recovery_code_hash')),
    })


@local_auth_bp.route('/username', methods=['PUT'])
def local_username_change():
    """修改本地管理员用户名，并重新签发 token。

    为什么不复用 /user/username：该端点默认只接受 main 面 token（F-10），
    桌面本地核心持的是 admin 面 token，会被 401 拒绝。本端点沿用本地门控
    （VR_LOCAL_MODE=1 + 回环来源），校验规则与主站保持一致。
    """
    _, user, err = _require_auth()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    if not username:
        return _err('USERNAME_EMPTY', 'Username cannot be empty', 400)
    if len(username) < 3 or len(username) > 20:
        return _err('USERNAME_LENGTH', 'Username must be 3-20 characters long', 400)
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_-]+$', username):
        return _err('USERNAME_FORMAT',
                    'Username must start with a letter; letters, digits, '
                    'underscores and hyphens only', 400)
    if username == (user.get('username') or ''):
        return _err('USERNAME_UNCHANGED', 'Username unchanged', 400)

    from services.name_validator import check_username
    check = check_username(username)
    if not check['valid']:
        return _err('USERNAME_RESERVED', check['error'], 400)

    from models import get_db, now_iso
    with get_db() as conn:
        existing = conn.execute(
            'SELECT id FROM users WHERE username=%s AND id!=%s',
            (username, user['id'])
        ).fetchone()
        if existing:
            return _err('USERNAME_TAKEN', 'Username already in use', 409)
        row = conn.execute(
            'SELECT username_changed_at FROM users WHERE id=%s', (user['id'],)
        ).fetchone()
        changed_at = (row or {}).get('username_changed_at')
        if changed_at:
            from datetime import datetime, timedelta
            try:
                changed = datetime.fromisoformat(str(changed_at))
                if datetime.now() - changed < timedelta(days=30):
                    remaining = 30 - (datetime.now() - changed).days
                    return _err('USERNAME_COOLDOWN',
                                f'Less than 30 days since last change, {remaining} days remaining', 429)
            except ValueError:
                # 历史数据时间格式异常时不阻塞改名（不因脏数据锁死账户）
                logger.warning('local_username_change: bad username_changed_at=%r', changed_at)
        conn.execute(
            'UPDATE users SET username=%s, username_changed_at=%s WHERE id=%s',
            (username, now_iso(), user['id'])
        )
        conn.commit()

    # 用户名进了登录凭据：吊销旧 token，返回新 token，避免会话里仍是旧名
    token = _issue_token_after_revoke({**user, 'username': username})
    _audit(user['id'], 'local_username_change', user['id'],
           f"{user.get('username') or ''} -> {username}")
    return _ok({'token': token, 'username': username})


@local_auth_bp.route('/password/first-set', methods=['POST'])
def local_password_first_set():
    """首次设密：仅当 password_changed_at IS NULL 时允许，保留当前 token。"""
    _, user, err = _require_auth()
    if err:
        return err
    if user.get('password_changed_at') is not None:
        return _err('PASSWORD_ALREADY_SET', 'Password already set', 409)
    data = request.get_json(silent=True) or {}
    new_password = data.get('new_password') or ''
    pol_err = _policy_error(new_password)
    if pol_err:
        return pol_err
    from models import get_db
    with get_db() as conn:
        conn.execute(
            'UPDATE users SET password_hash=%s, password_changed_at=NOW() WHERE id=%s',
            (_hash_password(new_password), user['id'])
        )
        conn.commit()
    # 初始密码已失效：删除首启明文凭据文件（登录页"默认账号"提示随之消失）
    clear_first_run_credentials()
    _audit(user['id'], 'local_password_first_set', user['id'], 'first password set')
    return _ok({'username': user.get('username') or ''})


@local_auth_bp.route('/password/change', methods=['POST'])
def local_password_change():
    """旧密码改密：校验旧密码、写新哈希、吊销旧 token 并返回新 token。"""
    _, user, err = _require_auth()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    old_password = data.get('old_password') or ''
    new_password = data.get('new_password') or ''
    if not _verify_password(user.get('password_hash'), old_password):
        return _err('BAD_OLD_PASSWORD', 'Old password is incorrect', 400)
    pol_err = _policy_error(new_password)
    if pol_err:
        return pol_err
    from models import get_db
    with get_db() as conn:
        conn.execute(
            'UPDATE users SET password_hash=%s, password_changed_at=NOW() WHERE id=%s',
            (_hash_password(new_password), user['id'])
        )
        conn.commit()
    clear_first_run_credentials()
    token = _issue_token_after_revoke(user)
    _audit(user['id'], 'local_password_change', user['id'], 'password changed')
    return _ok({'token': token})


@local_auth_bp.route('/recovery/issue', methods=['POST'])
def local_recovery_issue():
    """签发一次性恢复码；明文仅在本次响应中出现。"""
    _, user, err = _require_auth()
    if err:
        return err
    code = _generate_recovery_code()
    from models import get_db
    with get_db() as conn:
        conn.execute(
            'UPDATE users SET recovery_code_hash=%s, recovery_code_created_at=NOW() WHERE id=%s',
            (_hash_password(_normalize_recovery_code(code)), user['id'])
        )
        conn.commit()
    # 审计只记录「已签发」，绝不记录明文/哈希
    _audit(user['id'], 'local_recovery_issue', user['id'], 'recovery code issued')
    return _ok({'code': code})


@local_auth_bp.route('/recovery/reset', methods=['POST'])
def local_recovery_reset():
    """用恢复码自助重置密码（无需登录）。失败一律返回同一提示，不泄露账号是否存在。"""
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    recovery_code = data.get('recovery_code') or ''
    new_password = data.get('new_password') or ''

    key = _recovery_rate_key(username, request.remote_addr or '')
    if _recovery_rate_limited(key):
        return _err('TOO_MANY_ATTEMPTS', 'Too many attempts, please try again later', 429)

    invalid = _err('RECOVERY_INVALID', 'Invalid or expired recovery code', 400)

    from models import get_db
    with get_db() as conn:
        row = conn.execute(
            'SELECT id, username, phone, recovery_code_hash FROM users '
            'WHERE username=%s AND is_admin=1',
            (username,)
        ).fetchone()
    user = dict(row) if row else None

    stored = user.get('recovery_code_hash') if user else ''
    if not user or not stored or not _verify_password(
            stored, _normalize_recovery_code(recovery_code)):
        _recovery_record_failure(key)
        return invalid

    pol_err = _policy_error(new_password)
    if pol_err:
        return pol_err

    with get_db() as conn:
        conn.execute(
            'UPDATE users SET password_hash=%s, password_changed_at=NOW(), '
            'recovery_code_hash=%s, recovery_code_created_at=NULL WHERE id=%s',
            (_hash_password(new_password), '', user['id'])
        )
        conn.commit()

    clear_first_run_credentials()
    from services.jwt_service import revoke_all_user_tokens
    revoke_all_user_tokens(user['id'])
    _recovery_clear_failures(key)
    _audit(user['id'], 'local_recovery_reset', user['id'], 'recovery code used')
    return _ok({'username': user.get('username') or username})
