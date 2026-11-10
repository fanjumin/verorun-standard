#!/usr/bin/env python3
"""Aliyun SMS Provider — uses Aliyun dysmsapi20170525 SDK.

Environment variables (fallback):
    ALIYUN_SMS_ACCESS_KEY
    ALIYUN_SMS_SECRET
    ALIYUN_SMS_SIGN_NAME

DB config keys (priority):
    aliyun_sms_access_key
    aliyun_sms_secret
    aliyun_sms_sign_name
"""
import json
import os
from .base import BaseSMSProvider

# Template mapping: purpose -> Aliyun template code
TEMPLATE_MAP = {
    'register':        'SMS_506135003',
    'change_phone':    'SMS_506380001',
    'reset_password':  'SMS_506285002',
    'modify_password': 'SMS_506190002',
    'login':           'SMS_506330002',
}
DEFAULT_TEMPLATE = 'SMS_506135003'


class AliyunSMSProvider(BaseSMSProvider):
    """Aliyun SMS — sends verification codes via purpose-specific templates."""

    PROVIDER = 'aliyun'

    def __init__(self):
        self._cfg = None

    @staticmethod
    def _load_plugin_config():
        """Read plugin_registry.config for the SMS plugin.

        The admin SMS settings page persists credentials via
        PluginManager.set_config_batch('sms', ...), i.e. plugin_registry.config.
        The provider historically read only system_config, so UI-saved
        credentials were silently ignored. Read plugin config first, then
        fall back to system_config / environment.
        """
        try:
            from plugin_manager.models import get_registry_db
            with get_registry_db() as conn:
                row = conn.execute(
                    "SELECT config FROM plugin_registry WHERE identifier='sms'"
                ).fetchone()
            if row and row.get('config'):
                data = json.loads(row['config'])
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def _load_config(self):
        if self._cfg is not None:
            return self._cfg
        cfg = {'access_key': '', 'secret': '', 'sign_name': ''}
        plugin_cfg = self._load_plugin_config()
        cfg['access_key'] = str(plugin_cfg.get('aliyun_sms_access_key') or '')
        cfg['secret'] = str(plugin_cfg.get('aliyun_sms_secret') or '')
        cfg['sign_name'] = str(plugin_cfg.get('aliyun_sms_sign_name') or '')
        try:
            from models import get_db
            with get_db() as conn:
                for row in conn.execute(
                    "SELECT key, value FROM system_config WHERE key IN "
                    "('aliyun_sms_access_key','aliyun_sms_secret','aliyun_sms_sign_name')"
                ).fetchall():
                    if row['key'] == 'aliyun_sms_access_key' and not cfg['access_key']:
                        cfg['access_key'] = row['value']
                    elif row['key'] == 'aliyun_sms_secret' and not cfg['secret']:
                        cfg['secret'] = row['value']
                    elif row['key'] == 'aliyun_sms_sign_name' and not cfg['sign_name']:
                        cfg['sign_name'] = row['value']
        except Exception:
            pass
        # Env fallback
        if not cfg['access_key']:
            cfg['access_key'] = os.environ.get('ALIYUN_SMS_ACCESS_KEY', '')
            cfg['secret'] = os.environ.get('ALIYUN_SMS_SECRET', '')
            cfg['sign_name'] = os.environ.get('ALIYUN_SMS_SIGN_NAME', '')
        self._cfg = cfg
        return cfg

    def send(self, phone: str, message: str = '', **kwargs) -> dict:
        """Send SMS using Aliyun template.

        Args:
            phone: Chinese phone number (11 digits)
            message: Ignored for Aliyun (uses template)
            **kwargs: purpose (for template selection), code (verification code)
        """
        cfg = self._load_config()
        purpose = kwargs.get('purpose', 'login')
        code = kwargs.get('code', '')
        template_code = TEMPLATE_MAP.get(purpose, DEFAULT_TEMPLATE)
        sign_name = cfg.get('sign_name', '')
        if not sign_name:
            sign_name = '徐州易开网络科技'  # i18n:tech — 阿里云短信签名（须与运营商备案一致，不可翻译）

        if not cfg['access_key'] or not cfg['secret']:
            return {'success': False, 'provider': 'aliyun', 'error': 'Aliyun SMS not configured'}

        try:
            from alibabacloud_dysmsapi20170525.client import Client
            from alibabacloud_dysmsapi20170525 import models
            from alibabacloud_tea_openapi import models as open_api_models
            config = open_api_models.Config(
                access_key_id=cfg['access_key'],
                access_key_secret=cfg['secret']
            )
            config.endpoint = 'dysmsapi.aliyuncs.com'
            client = Client(config)
            req = models.SendSmsRequest(
                phone_numbers=phone,
                sign_name=sign_name,
                template_code=template_code,
                template_param='{"code":"%s"}' % code
            )
            resp = client.send_sms(req)
            is_ok = resp.body.code == 'OK'
            return {
                'success': is_ok,
                'provider': 'aliyun',
                'biz_id': resp.body.biz_id,
                'template': template_code,
                'message': resp.body.message if not is_ok else 'OK',
            }
        except Exception as e:
            return {'success': False, 'provider': 'aliyun', 'error': str(e), 'template': template_code}

    def is_configured(self) -> bool:
        cfg = self._load_config()
        return bool(cfg.get('access_key'))
