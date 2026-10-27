#!/usr/bin/env python3
"""API Key encryption/decryption using Fernet symmetric encryption.
   Requires ENCRYPTION_KEY env var (32-byte hex string, set once)."""
import os, base64, logging
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# 主密钥环境变量，按优先级排列：首个是写入方（桌面壳注入 ENCRYPTION_KEY），
# 其余是历史密钥，仅用于解密回退。
_MASTER_KEY_ENVS = ('ENCRYPTION_KEY', 'DEV_ACCOUNTS_ENCRYPTION_KEY')

# SB-BUG-1 根因修复：salt 必须位于部署树外的持久目录（data/ 不随部署同步），
# 否则部署重建 auth-center/services/ 时 salt 丢失 → 新 salt 派生 key 变化 →
# 已存密文全部 InvalidToken（2026-09-18 事故：全部 LLM 功能静默死亡 3 天）。
# 可用 VR_CRYPTO_SALT_PATH 环境变量显式指定；默认迁移到项目 data/ 目录。
_SALT_PATH = os.environ.get('VR_CRYPTO_SALT_PATH') or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data', '.crypto_salt')
_LEGACY_SALT_PATH = os.path.join(os.path.dirname(__file__), '.crypto_salt')

def _get_or_create_salt() -> bytes:
    """读取已有 salt 或生成新的随机 salt 并持久化。

    优先持久路径；首次运行时若旧部署树内 salt 仍存在则一次性迁移
    （保证派生 key 不变，已存密文可继续解密）。
    """
    if os.path.exists(_SALT_PATH):
        with open(_SALT_PATH, 'rb') as f:
            return f.read()
    # 一次性迁移：旧路径（部署树内）salt 搬到持久路径
    if os.path.exists(_LEGACY_SALT_PATH):
        with open(_LEGACY_SALT_PATH, 'rb') as f:
            salt = f.read()
        try:
            os.makedirs(os.path.dirname(_SALT_PATH), exist_ok=True)
            with open(_SALT_PATH, 'wb') as f:
                f.write(salt)
        except OSError:
            return salt  # 迁移写失败不致命：本轮仍用旧 salt，下次重试
        return salt
    salt = os.urandom(16)
    os.makedirs(os.path.dirname(_SALT_PATH), exist_ok=True)
    with open(_SALT_PATH, 'wb') as f:
        f.write(salt)
    return salt

def _raw_keys():
    """按优先级返回可用的主密钥原文（去重、校验强度）。

    历史原因，凭据可能由两个环境变量之一加密写入：桌面壳恒注入
    ENCRYPTION_KEY（来自 runtime/secrets.json），而早期部署与 .env 用的是
    DEV_ACCOUNTS_ENCRYPTION_KEY。二者不一致时单密钥解密必然 InvalidToken，
    表现为所有凭据"解不开"——上游常把它误报成"未配置 Key"。
    """
    keys = []
    for name in _MASTER_KEY_ENVS:
        raw = (os.environ.get(name) or '').strip()
        if not raw:
            continue
        # VR-SEC-011: 校验主密钥强度，杜绝空/过短密钥静默使用
        if len(raw) < 16:
            raise RuntimeError(f"{name} is too weak (minimum 16 characters). "
                               "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\"")
        if raw not in keys:
            keys.append(raw)
    return keys


def _derive(raw: str) -> bytes:
    """PBKDF2(salt 统一) 派生 Fernet key；候选密钥共用同一 salt 文件。"""
    salt = _get_or_create_salt()
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000)
    return base64.urlsafe_b64encode(kdf.derive(raw.encode()))


def _get_key():
    keys = _raw_keys()
    if not keys:
        raise RuntimeError("ENCRYPTION_KEY environment variable is not set")
    return _derive(keys[0])

_fernets = None

def _get_fernets():
    """懒加载候选 Fernet 列表：索引 0 为写入方，其后为历史密钥（只读回退）。

    懒加载是为了避免模块导入时因缺少 ENCRYPTION_KEY 崩溃。
    """
    global _fernets
    if _fernets is None:
        _fernets = [Fernet(_derive(raw)) for raw in _raw_keys()]
    return _fernets

def _get_fernet():
    return _get_fernets()[0]

def encrypt(plaintext: str) -> str:
    """用主密钥加密；写入始终用同一个密钥，保证密文可预期。"""
    if not plaintext:
        return ''
    return _get_fernet().encrypt(plaintext.encode()).decode()

def decrypt(ciphertext: str) -> str:
    """解密，允许历史密钥回退；全部失败时抛 InvalidToken，绝不返回密文原文。"""
    if not ciphertext:
        return ''
    token = ciphertext.encode()
    first_err = None
    for idx, fernet in enumerate(_get_fernets()):
        try:
            plain = fernet.decrypt(token).decode()
        except InvalidToken as e:
            if first_err is None:
                first_err = e
            continue
        if idx:
            # 用历史密钥解开了：提示需要迁移，但不阻断读取（升级不断服）
            logger.warning(
                'decrypt succeeded with a secondary master key (index %d); '
                're-encrypt this credential with the primary key', idx)
        return plain
    raise first_err or InvalidToken('no master key could decrypt this value')