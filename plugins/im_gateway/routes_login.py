#!/usr/bin/env python3
"""第三方登录 — 登录 OAuth 提供方管理端点（Phase 5，方案 A：插件自包含）。

提供方目录、凭据保存、启用/停用、授权 URL 生成（测试）。
登录闭环（回调→用户绑定→JWT）需 auth-center 系统集成，属方案 B，另行立项。
"""
import logging

from flask import Blueprint, jsonify, request

from .login import list_login_providers, get_login_provider_class
from .models import get_im_db
from .secure_config import encrypt_secret, SecureConfigUnavailable

logger = logging.getLogger(__name__)

login_bp = Blueprint('im_gateway_login', __name__,
                     url_prefix='/admin/channels/login')


def _require_admin():
    from routes.admin import _require_admin as _ra
    return _ra()


def _mask_secret(secret: str) -> str:
    if not secret:
        return ''
    if len(secret) <= 4:
        return '●' * len(secret)
    return secret[:4] + '●' * (len(secret) - 4)


def _load_configs() -> dict:
    """读取全部提供方配置（client_secret 经统一入口解密后再供掩码/使用）"""
    from .models import load_login_providers
    return {r['provider']: r for r in load_login_providers()}


@login_bp.route('/providers', methods=['GET'])
def list_providers():
    """内置提供方目录 + 已保存配置（client_secret 掩码）"""
    admin, err = _require_admin()
    if err:
        return err
    configs = _load_configs()
    result = []
    for p in list_login_providers():
        cfg = configs.get(p['id'], {})
        result.append({
            'provider': p['id'],
            'display_name': p['display_name'],
            'icon': p['icon'],
            'default_scopes': p['default_scopes'],
            'configured': bool(cfg.get('client_id')),
            'is_enabled': int(cfg.get('is_enabled', 0)),
            'client_id': cfg.get('client_id', ''),
            'client_secret_masked': _mask_secret(cfg.get('client_secret', '')),
            'scopes': cfg.get('scopes', ''),
            'redirect_uri': cfg.get('redirect_uri', ''),
        })
    return jsonify({'success': True, 'data': result})


@login_bp.route('/providers/save', methods=['POST'])
def save_provider():
    """保存提供方凭据（client_secret 留空时保留原值）"""
    admin, err = _require_admin()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    provider = data.get('provider', '')
    valid = {p['id'] for p in list_login_providers()}
    if provider not in valid:
        return jsonify({'success': False, 'error': 'unknown provider'}), 400
    client_secret = data.get('client_secret', '') or ''
    with get_im_db() as conn:
        exists = conn.execute(
            'SELECT id FROM login_providers WHERE provider=%s', (provider,)
        ).fetchone()
        if exists:
            if not client_secret:
                # 留空保留原值（库内已是密文，原样保留，不重复加密）
                cur = conn.execute(
                    'SELECT client_secret FROM login_providers WHERE provider=%s',
                    (provider,),
                ).fetchone()
                client_secret = cur['client_secret'] or ''
            else:
                try:
                    client_secret = encrypt_secret(client_secret)
                except SecureConfigUnavailable as e:
                    return jsonify({'success': False, 'error': str(e)}), 503
            conn.execute(
                'UPDATE login_providers SET display_name=%s, client_id=%s, '
                'client_secret=%s, scopes=%s, redirect_uri=%s, is_enabled=%s, '
                'updated_at=NOW() WHERE provider=%s',
                (data.get('display_name', '') or '',
                 data.get('client_id', '') or '',
                 client_secret,
                 data.get('scopes', '') or '',
                 data.get('redirect_uri', '') or '',
                 1 if data.get('is_enabled') else 0,
                 provider),
            )
        else:
            if client_secret:
                try:
                    client_secret = encrypt_secret(client_secret)
                except SecureConfigUnavailable as e:
                    return jsonify({'success': False, 'error': str(e)}), 503
            conn.execute(
                'INSERT INTO login_providers (provider, display_name, client_id, '
                'client_secret, scopes, redirect_uri, is_enabled) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s)',
                (provider,
                 data.get('display_name', '') or '',
                 data.get('client_id', '') or '',
                 client_secret,
                 data.get('scopes', '') or '',
                 data.get('redirect_uri', '') or '',
                 1 if data.get('is_enabled') else 0),
            )
        conn.commit()
    return jsonify({'success': True})


@login_bp.route('/providers/enable', methods=['POST'])
def enable_provider():
    """启用/停用提供方"""
    admin, err = _require_admin()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    provider = data.get('provider', '')
    with get_im_db() as conn:
        conn.execute(
            'UPDATE login_providers SET is_enabled=%s, updated_at=NOW() '
            'WHERE provider=%s',
            (1 if data.get('is_enabled') else 0, provider),
        )
        conn.commit()
    return jsonify({'success': True})


@login_bp.route('/authorize/<provider>', methods=['GET'])
def authorize_url(provider):
    """生成授权 URL（测试用）"""
    admin, err = _require_admin()
    if err:
        return err
    cls = get_login_provider_class(provider)
    if cls is None:
        return jsonify({'success': False, 'error': 'unknown provider'}), 400
    cfg = _load_configs().get(provider, {})
    if not cfg.get('client_id'):
        return jsonify({'success': False, 'error': 'provider not configured'}), 400
    state = request.args.get('state', 'im_gateway_login_test')
    url = cls.build_authorize_url(
        client_id=cfg['client_id'],
        redirect_uri=cfg.get('redirect_uri', ''),
        state=state,
        scopes=cfg.get('scopes', ''),
    )
    return jsonify({'success': True, 'authorize_url': url})
