#!/usr/bin/env python3
"""IM Gateway — 频道配置字段级加密（P0-03）。

复用系统主密钥 ENCRYPTION_KEY（与 social_push / stock_analysis 同密钥体系）：
base64.urlsafe_b64encode(sha256(ENCRYPTION_KEY)) → cryptography Fernet。
本模块只依赖环境变量，不 import 其他插件。

纪律：
  - 写路径 fail-closed：无可用 ENCRYPTION_KEY 或加密链路异常，抛
    SecureConfigUnavailable，**绝不落明文**。
  - 读路径向后兼容：只解带前缀 `enc:v1:` 的字段；历史明文原样返回并记 WARNING
    （由 tools/migrate_im_credentials.py 收敛，避免升级瞬间打断在用部署）；
    带前缀但解不开 → 抛异常，**绝不把密文当明文继续用**。

加密后仍是合法 JSON 字符串（值带 `enc:v1:` 前缀，老代码 json.loads 不报错）。
"""
import os
import base64
import hashlib
import logging

_log = logging.getLogger('im_gateway.secure_config')

# 密文前缀标记（字段级，非 Fernet 自带前缀；迁移与读路径分流用）
_PREFIX = 'enc:v1:'

# 各频道需字段级加密的凭据字段。
# 按各 adapter get_config_fields() 中 type='password' 的字段校准，并纳入
# webhook 入站验签实际读取的凭据字段（详见 P0-03）：
#   feishu   : app_secret / verification_token / encrypt_key（均 password）
#   wecom    : secret / token / encoding_aes_key（均 password，后两项为回调凭据）
#   dingtalk : app_secret（password；此频道无 secret 字段）
#   telegram : bot_token / secret_token（secret_token 供 webhook_signing 验签）
#   line     : channel_secret / access_token（password；原清单 channel_access_token 名不符）
#   slack    : bot_token / signing_secret（signing_secret 供 webhook_signing 验签）
#   discord  : bot_token / application_public_key（公钥字段，原清单 public_key 名不符；
#              供 webhook_signing 验签，经同一读取路径解密）
#   qq       : client_secret / bot_secret（client_secret 取 token；bot_secret 供验签）
SENSITIVE_FIELDS = {
    'feishu': ('app_secret', 'verification_token', 'encrypt_key'),
    'wecom': ('secret', 'token', 'encoding_aes_key'),
    'dingtalk': ('app_secret',),
    'telegram': ('bot_token', 'secret_token'),
    'line': ('channel_secret', 'access_token'),
    'slack': ('bot_token', 'signing_secret'),
    'discord': ('bot_token', 'application_public_key'),
    'qq': ('client_secret', 'bot_secret'),
}

_cipher = None


class SecureConfigUnavailable(RuntimeError):
    """加密能力不可用。调用方必须拒绝写入（503），不得降级为明文。"""


def _get_cipher():
    """懒加载 Fernet 实例（首次调用时才派生密钥）。"""
    global _cipher
    if _cipher is None:
        from cryptography.fernet import Fernet
        raw = os.environ.get('ENCRYPTION_KEY')
        if not raw:
            raise SecureConfigUnavailable('ENCRYPTION_KEY environment variable is not set')
        key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        _cipher = Fernet(key)
    return _cipher


def _crypto_available() -> bool:
    """ENCRYPTION_KEY 是否已配置。"""
    return bool(os.environ.get('ENCRYPTION_KEY'))


def is_encrypted(value: str) -> bool:
    """判断字段是否为本模块产出的密文（前缀标记，迁移与读路径分流用）。"""
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt_fields(channel: str, cfg: dict) -> dict:
    """对 channel 的敏感字段加密（仅非空且未加密者）。

    无可用 ENCRYPTION_KEY 或加密失败一律抛 SecureConfigUnavailable —— 绝不返回明文。
    """
    out = dict(cfg or {})
    fields = SENSITIVE_FIELDS.get(channel, ())
    if not fields:
        return out
    pending = [k for k in fields
               if isinstance(out.get(k), str) and out[k] and not is_encrypted(out[k])]
    if not pending:
        return out
    if not _crypto_available():
        raise SecureConfigUnavailable(
            'ENCRYPTION_KEY missing; credential write refused for channel ' + str(channel))
    for k in pending:
        try:
            token = _get_cipher().encrypt(out[k].encode()).decode()
        except SecureConfigUnavailable:
            raise
        except Exception as exc:                       # 加密链路故障 = 拒绝写入
            raise SecureConfigUnavailable(
                'encrypt failed for {}.{}: {}'.format(channel, k, exc)) from exc
        out[k] = _PREFIX + token
    return out


def decrypt_fields(channel: str, cfg: dict) -> dict:
    """解密 channel 的敏感字段。

    只解带前缀者；无前缀按历史明文原样返回并记 WARNING；
    带前缀但解不开 → 抛 SecureConfigUnavailable（绝不把密文当明文继续用）。
    """
    out = dict(cfg or {})
    fields = SENSITIVE_FIELDS.get(channel, ())
    if not fields:
        return out
    for k in fields:
        val = out.get(k)
        if not isinstance(val, str) or not val:
            continue
        if not is_encrypted(val):
            _log.warning(
                'plaintext credential detected for %s.%s; '
                'run tools/migrate_im_credentials.py to converge', channel, k)
            continue
        try:
            out[k] = _get_cipher().decrypt(val[len(_PREFIX):].encode()).decode()
        except SecureConfigUnavailable:
            raise
        except Exception as exc:
            raise SecureConfigUnavailable(
                'decrypt failed for {}.{}: {}'.format(channel, k, exc)) from exc
    return out


# ── 单值凭据（login_providers.client_secret 等）加解密 ──

def encrypt_secret(value: str) -> str:
    """加密单值凭据；空值原样返回；密钥不可用或加密失败抛 SecureConfigUnavailable。"""
    if not value:
        return ''
    if is_encrypted(value):
        return value
    if not _crypto_available():
        raise SecureConfigUnavailable('ENCRYPTION_KEY missing; credential write refused')
    try:
        return _PREFIX + _get_cipher().encrypt(value.encode()).decode()
    except SecureConfigUnavailable:
        raise
    except Exception as exc:
        raise SecureConfigUnavailable('encrypt failed for secret: {}'.format(exc)) from exc


def decrypt_secret(value: str) -> str:
    """解密单值凭据；空值返回空；历史明文原样返回并记 WARNING；解不开抛异常。"""
    if not value:
        return ''
    if not is_encrypted(value):
        _log.warning(
            'plaintext login secret detected; '
            'run tools/migrate_im_credentials.py to converge')
        return value
    try:
        return _get_cipher().decrypt(value[len(_PREFIX):].encode()).decode()
    except SecureConfigUnavailable:
        raise
    except Exception as exc:
        raise SecureConfigUnavailable('decrypt failed for secret: {}'.format(exc)) from exc