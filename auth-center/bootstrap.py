"""auth-center/bootstrap.py — 桌面单机版（VR_LOCAL_MODE=1）首次启动引导。

职责（只在本地单机模式下生效）：
- 空库（没有任何 is_admin=1 账号）时创建默认管理员，初始密码**随机生成**；
- 把初始账号明文写入"首次运行凭据文件"（路径由 Electron 经 VR_FIRST_RUN_CRED_FILE 注入），
  仅用于登录页展示"首次安装的默认账号密码 + 请修改"提醒；
- 用户完成首次改密后，由 routes/local_auth.py 调用 clear_first_run_credentials() 删除明文
  （登录页提醒随之消失）。

设计约束（与 deploy/seed_data.py 刻意区分）：
- 仅在 VR_LOCAL_MODE=1 时执行 —— 线上部署（install.sh seed）行为完全不变；
- 仅空库执行，绝不覆盖已有管理员的用户名/密码（seed 的 UPDATE 分支会覆盖密码，不可复用）；
- 明文只写本地 0600 文件，不落库、不入日志正文以外的任何传输通道。
"""
import hashlib
import json
import logging
import os
import secrets

logger = logging.getLogger(__name__)

DEFAULT_ADMIN_USERNAME = 'administrator'
DEFAULT_ADMIN_DISPLAY = 'Administrator'
ADMIN_PERMISSIONS = '["users","content","finance","system","matrix","admins"]'
_PBKDF2_ITERATIONS = 600000


def _hash_password(password: str) -> str:
    """与 routes/local_auth.py / admin/app.py 登录校验完全一致的 PBKDF2 格式。"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        'sha256', password.encode(), salt.encode(), _PBKDF2_ITERATIONS).hex()
    return f'pbkdf2:sha256:{_PBKDF2_ITERATIONS}:{salt}:{digest}'


def credentials_file() -> str:
    """首次运行凭据文件路径（Electron 注入）；未注入时返回空串 → 不展示、不写盘。"""
    return (os.environ.get('VR_FIRST_RUN_CRED_FILE') or '').strip()


def _write_credentials_file(username: str, password: str) -> None:
    path = credentials_file()
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        tmp = f'{path}.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'username': username, 'password': password}, f, ensure_ascii=False)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # Windows 无 POSIX 权限语义；父目录 ACL 已限当前用户
        os.replace(tmp, path)
    except Exception:
        logger.exception('bootstrap: failed to write first-run credentials file')


def clear_first_run_credentials() -> None:
    """首次改密完成后删除明文凭据文件（幂等；登录页提示随之消失）。"""
    path = credentials_file()
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        logger.warning('bootstrap: failed to remove first-run credentials file: %s', path)


def ensure_default_admin() -> dict | None:
    """空库时创建默认管理员。返回 {'username': ...} / None（未创建或不需要创建）。

    调用方（admin/app.py）应吞掉异常：引导失败只影响首启体验，不应阻断服务启动。
    """
    if (os.environ.get('VR_LOCAL_MODE') or '').strip() != '1':
        return None

    from models import get_db

    with get_db() as conn:
        row = conn.execute(
            'SELECT id FROM users WHERE is_admin=1 ORDER BY id LIMIT 1').fetchone()
        if row:
            return None  # 已有管理员：永不覆盖用户名/密码

        username = (os.environ.get('VR_ADMIN_USERNAME') or '').strip() or DEFAULT_ADMIN_USERNAME
        password = (os.environ.get('VR_ADMIN_PASSWORD') or '').strip()
        if not password:
            from services.password_validator import generate_strong_password
            password = generate_strong_password(12)

        cur = conn.execute(
            'INSERT INTO users (username, display_name, password_hash, is_admin, active) '
            'VALUES (%s, %s, %s, 1, 1) ON CONFLICT (username) DO NOTHING RETURNING id',
            (username, DEFAULT_ADMIN_DISPLAY, _hash_password(password)))
        created = cur.fetchone()
        if not created:
            # 同名账号被占用（非管理员）→ 不接管，交由人工处理
            logger.warning(
                'bootstrap: username "%s" already exists but is not an admin; skipped', username)
            return None
        user_id = created['id']

        # RBAC 依赖 admin_profiles：缺失时写操作端点会 403（见 routes/admin._enforce_admin_perm）
        conn.execute(
            'INSERT INTO admin_profiles (user_id, role, permissions, real_name, notes) '
            'VALUES (%s, %s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING',
            (user_id, 'super_admin', ADMIN_PERMISSIONS, username, 'Initial Super Admin'))
        conn.commit()

    _write_credentials_file(username, password)
    logger.warning(
        '[bootstrap] default admin created: %s (random initial password, must change after first sign-in)',
        username)
    # 桌面版安装输出/日志可见；登录页亦会展示一次，改密后自动消失
    print(f'[bootstrap] default admin created: {username} / {password} (change after first sign-in)')
    return {'username': username}
