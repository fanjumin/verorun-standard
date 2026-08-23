"""auth-center/services/session_service.py
通用登录签发通道：所有登录方式（密码/短信/手机注册/邮箱注册/OAuth/抖音小程序）统一经过此函数。

设计原则（插件化铁律）：
- 本文件不包含任何 2FA 专属逻辑（无 TOTP/恢复码/challenge）。
- 插件通过 `auth.pre_issue_token` 过滤器拦截签发：核心只调钩子 → 若被阻断则原样透传 block_info → 否则签发 token。
- 插件未启用时钩子链为空，ctx 不变，直接签发，登录零影响。
"""
import os
import hashlib
from services.jwt_service import create_token


def _get_registry():
    """获取 HookRegistry；插件系统未初始化时返回 None（fail-open）。"""
    try:
        from plugin_manager.hooks import get_hook_registry
        return get_hook_registry()
    except Exception:
        return None


def issue_auth_session(user_id, phone, app_name, is_admin=False, role='user',
                       user_info=None, device_name='Web Login', device_type='web'):
    """统一登录签发入口。

    返回 dict（三种形态）：
      - {'blocked': False, 'token': <jwt>, 'user': {...}}      已签发
      - {'blocked': True, 'block_info': {...}}                 插件拦截（如 2FA challenge）
      - {'blocked': True, 'block_info': {...}, 'error': ...}   插件拦截但无法提供服务（fail-closed）
    """
    registry = _get_registry()
    ctx = {'issue': True, 'user_id': user_id, 'app_name': app_name,
           'is_admin': bool(is_admin), 'role': role, 'phone': phone}
    if registry is not None:
        ctx = registry.apply_filters('auth.pre_issue_token', ctx, user_id=user_id)

    if not ctx.get('issue', True):
        # 插件拦截：除标准字段外，插件写入的任何附加信息原样透传。
        # 核心不解析 challenge_token/methods 等字段含义，只负责搬运。
        block_info = {k: v for k, v in ctx.items()
                      if k not in ('issue', 'user_id', 'app_name',
                                   'is_admin', 'role', 'phone')}
        error = block_info.pop('error', None)
        if error:
            return {'blocked': True, 'block_info': block_info, 'error': error}
        return {'blocked': True, 'block_info': block_info}

    token = create_token(user_id, phone=phone, app_name=app_name,
                         is_admin=is_admin, role=role)
    _write_user_session(user_id, token, device_name, device_type)
    return {'blocked': False, 'token': token,
            'user': {'id': user_id, 'phone': phone,
                     'is_admin': bool(is_admin), 'role': role,
                     **(user_info or {})}}


def _write_user_session(user_id, token, device_name, device_type):
    """写 user_sessions（与各登录入口现有逻辑一致）。"""
    from models import get_db
    import flask
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    ua = (flask.request.headers.get('User-Agent', '') or '')[:200]
    ip = flask.request.remote_addr or ''
    with get_db() as conn:
        conn.execute(
            "INSERT INTO user_sessions "
            "(user_id, token_hash, device_name, device_type, ip_address, user_agent, is_current, last_active, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,1,NOW(),NOW())",
            (user_id, token_hash, device_name, device_type, ip, ua))
        conn.commit()


def set_sso_cookie(resp, token):
    """跨子域 SSO cookie（现有 4 份重复实现收敛到此）。"""
    main_domain = os.environ.get('DEPLOY_DOMAIN', '')
    is_https = os.environ.get('DEPLOY_PROTOCOL', 'https') == 'https'
    if main_domain:
        resp.set_cookie('sso_token', token, domain='.' + main_domain,
                        path='/', max_age=604800, samesite='Lax',
                        secure=is_https, httponly=True)
    else:
        resp.set_cookie('sso_token', token, path='/', max_age=604800,
                        samesite='Lax', secure=is_https, httponly=True)
