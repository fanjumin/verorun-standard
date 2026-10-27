#!/usr/bin/env python3
"""JWT Service — token creation and validation using PyJWT.
Supports token revocation via jti blacklist (Redis / in-memory fallback)."""
import os, json, time, threading, logging
import jwt as pyjwt

logger = logging.getLogger(__name__)

JWT_SECRET = os.environ.get('JWT_SECRET')
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable is not set")

JWT_ALGO = 'HS256'
JWT_EXPIRY_HOURS = 24 * 7  # 7 days
JWT_REFRESH_EXPIRY_HOURS = 24 * 30  # 30 days

# ── Token blacklist (in-memory fallback, use Redis in production) ──
_token_blacklist = {}
_blacklist_lock = threading.Lock()
_BLACKLIST_CLEANUP_INTERVAL = 3600  # 1h cleanup

# D1：jti"未吊销"结果的负缓存。吊销只发生在主动登出/强制下线，秒级延迟可接受；
# 而没有负缓存时，每个已鉴权请求（SSE 建立、轮询、流式）都要为"未吊销"这个
# 答案穿透一次 DB。TTL 1s（F-03：30s 会让其他 worker 的登出延迟最多 30 秒生效），
# 容量兜底清理，且 _revoke_jti() 会立即作废对应条目。
_JTI_NEG_TTL = 1
_JTI_NEG_MAX = 10000
_jti_neg_cache = {}  # jti -> 该结论的失效时刻
_neg_lock = threading.Lock()


def _neg_cache_hit(jti):
    """True = 近期刚确认过这个 jti 未被吊销，可以跳过 DB 查询。"""
    now = time.time()
    with _neg_lock:
        exp = _jti_neg_cache.get(jti)
        if exp is None:
            return False
        if exp < now:
            _jti_neg_cache.pop(jti, None)
            return False
        return True


def _neg_cache_put(jti):
    with _neg_lock:
        _jti_neg_cache[jti] = time.time() + _JTI_NEG_TTL
        if len(_jti_neg_cache) > _JTI_NEG_MAX:
            now = time.time()
            for k in [k for k, v in _jti_neg_cache.items() if v < now]:
                _jti_neg_cache.pop(k, None)
            if len(_jti_neg_cache) > _JTI_NEG_MAX:
                # 全是未过期的：直接砍掉一半，宁可多查一次库也不无界增长
                for k in list(_jti_neg_cache)[:_JTI_NEG_MAX // 2]:
                    _jti_neg_cache.pop(k, None)


def _neg_cache_drop(jti):
    with _neg_lock:
        _jti_neg_cache.pop(jti, None)


def _cleanup_blacklist():
    """Remove expired entries from blacklist."""
    now = time.time()
    with _blacklist_lock:
        expired = [k for k, v in _token_blacklist.items() if v < now]
        for k in expired:
            del _token_blacklist[k]
    if expired:
        logger.info(f"Cleaned {len(expired)} expired tokens from blacklist")


def _revoke_jti(jti, expires_at):
    """Add a jti to the blacklist (DB-backed with in-memory cache).
    P1-F03: 落库确保多 worker/多进程同步生效。
    """
    with _blacklist_lock:
        _token_blacklist[jti] = expires_at
    # 吊销必须立即覆盖"未吊销"的负缓存结论（同进程内不允许有 30s 窗口）
    _neg_cache_drop(jti)
    # 持久化到数据库
    try:
        from models import get_db
        with get_db() as conn:
            conn.execute(
                "INSERT INTO system_config (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (f'revoked_jti_{jti}', str(int(expires_at)))
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to persist revoked jti {jti} to DB: {e}")


def _is_jti_revoked(jti):
    """Check if a jti is blacklisted (memory cache + DB fallback).
    P1-F03: 内存缓存未命中时查DB，确保多进程同步。
    """
    with _blacklist_lock:
        if jti in _token_blacklist:
            return True
    # D1：近期已经确认过这个 jti 未被吊销 → 不再为"未吊销"这个答案打一次 DB
    if _neg_cache_hit(jti):
        return False
    # 查数据库（多 worker 场景下其它进程吊销的 token）
    try:
        from models import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key=%s",
                (f'revoked_jti_{jti}',)
            ).fetchone()
        if row:
            expires_at = int(row['value'])
            if time.time() < expires_at:
                # 回填内存缓存
                with _blacklist_lock:
                    _token_blacklist[jti] = expires_at
                return True
            # VR-SEC-005: 过期吊销记录一并从 DB 清理，防止 system_config 无限膨胀
            try:
                from models import get_db as _db2
                with _db2() as conn:
                    conn.execute(
                        "DELETE FROM system_config WHERE key=%s",
                        (f'revoked_jti_{jti}',)
                    )
                    conn.commit()
            except Exception as e:
                logger.error(f"Failed to purge expired revoked jti {jti}: {e}")
    except Exception as e:
        # VR-SEC-004: DB 异常时 fail-closed，与 user revocation 检查（L169）保持一致
        logger.error(f"DB check for revoked jti failed: {e}")
        return True
    _neg_cache_put(jti)
    return False


def create_token(user_id, phone=None, app_name='main', is_admin=False,
                 token_type='access', role=None, permissions=None):
    """Create a JWT with jti for revocation support.
    role: 'super_admin' | 'admin' | 'operator' | 'user' (defaults to 'user')
    permissions: list of permission strings (e.g. ['stock.read', 'stock.write'])"""
    import secrets
    jti = secrets.token_urlsafe(16)
    # B-09：签到时间同时落秒级 iat（JWT 标准）与毫秒级 iat_ms（用户级吊销比对用）。
    # 两者必须同源，避免跨毫秒边界时 iat_ms 与 iat 不自洽。
    now_ms = int(time.time() * 1000)
    now = now_ms // 1000
    exp_hours = JWT_EXPIRY_HOURS if token_type == 'access' else JWT_REFRESH_EXPIRY_HOURS
    payload = {
        'jti': jti,
        'user_id': user_id,
        'phone': phone,
        'app_name': app_name,
        'is_admin': is_admin,
        'role': role or 'user',
        'permissions': permissions or [],
        'token_type': token_type,
        'iat': now,
        'iat_ms': now_ms,
        'exp': now + exp_hours * 3600,
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def revoke_token(token):
    """Revoke a token by adding its jti to the blacklist."""
    payload = _decode_token(token, verify_exp=False)
    if payload and 'jti' in payload:
        _revoke_jti(payload['jti'], payload.get('exp', time.time() + 3600))
        _cleanup_blacklist()
        return True
    return False


def revoke_all_user_tokens(user_id):
    """Revoke ALL tokens for a given user (force logout everywhere).
    Since we can't enumerate all jtis for a user, we use a user-level
    revocation timestamp stored in the token payload.
    This is a simplified approach; for production use Redis sets."""
    # B-09：时间戳改为毫秒级。秒级粒度下"同一秒内改密"的旧 token 与改密后新签发
    # 的 token iat 完全相同，无法区分 → 旧 token 会残留到下一秒。
    from models import get_db
    now_ms = int(time.time() * 1000)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            (f'user_token_revoked_at_{user_id}', str(now_ms))
        )
        conn.commit()


def _decode_token(token, verify_exp=True):
    """Internal: decode without raising."""
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO],
                            options={'verify_exp': verify_exp})
    except pyjwt.ExpiredSignatureError:
        return None
    except Exception:
        return None


# B-09：用户级吊销时间戳存在两种精度。历史记录为秒级（< 10^11），
# 新写入为毫秒级（>= 10^11）。毫秒值用 `<=` 精确到签发时刻，
# 秒级历史值沿用 ` < ` 以免把同一秒内新签发的 token 也误杀。
_MS_EPOCH_FLOOR = 10 ** 11


def _is_revoked_by_user(payload, raw_revoked_at):
    """True 表示该 token 落在用户级吊销范围内。

    raw_revoked_at 无法解析为整数时抛 ValueError，由调用方按 fail-closed 处理。
    """
    revoked = int(raw_revoked_at)
    if revoked >= _MS_EPOCH_FLOOR:
        iat_ms = payload.get('iat_ms')
        if not isinstance(iat_ms, int):
            # 本功能上线前签发的 token 没有 iat_ms，用秒级 iat 折算
            iat_ms = int(payload.get('iat', 0)) * 1000
        return iat_ms <= revoked
    return int(payload.get('iat', 0)) < revoked


def validate_token(token, allow_apps=None):
    """Returns decoded payload or None.
    Checks jti blacklist and per-user revocation.

    allow_apps: F-10 —— 允许的签发面（app_name）白名单。为空则不限制；
    传入后 token 的 app_name 不在白名单内即视为无效（拒绝跨面使用）。
    """
    payload = _decode_token(token)
    if not payload:
        return None

    # 主站 app_name 统一：历史遗留 trademind/platform 归一为 main（兼容存量 JWT）
    if payload.get('app_name') in ('trademind', 'platform'):
        payload['app_name'] = 'main'
    if allow_apps and payload.get('app_name') not in allow_apps:
        return None

    # Check jti blacklist
    jti = payload.get('jti')
    if jti and _is_jti_revoked(jti):
        return None

    # D1：账号冻结 + 用户级吊销合并为一次往返（原来两次独立查询，且每个已鉴权
    # 请求都要付）。SQL 仍是 fail-closed：查询异常、用户不存在、 revoked_at 无法
    # 解析，一律拒绝 token。
    user_id = payload.get('user_id')
    if user_id:
        from models import get_db
        try:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT u.active AS active, ur.value AS revoked_at "
                    "FROM users u "
                    "LEFT JOIN system_config ur ON ur.key = %s "
                    "WHERE u.id = %s",
                    (f'user_token_revoked_at_{user_id}', user_id)
                ).fetchone()
        except Exception as e:
            logger.error(f"User auth-state DB check failed for user={user_id}: {e}")
            return None  # P1-F03: fail-closed — DB 异常时拒绝 token

        if row is None or row['active'] != 1:
            return None  # 账号不存在或已冻结
        if row['revoked_at']:
            try:
                if _is_revoked_by_user(payload, row['revoked_at']):
                    return None  # Token issued before user was force-logged-out
            except (TypeError, ValueError):
                logger.error(f"Malformed user_token_revoked_at for user={user_id}: "
                             f"{row['revoked_at']!r}; rejecting token (fail-closed)")
                return None

    return payload


def get_user_id(token):
    """Safely extract user_id from token."""
    if not token:
        return None
    payload = validate_token(token)
    return payload.get('user_id') if payload else None
