#!/usr/bin/env python3
"""IM Gateway Plugin — 数据库模型

独立数据库 im_gateway.db，存放频道配置表 channel_configs。
从主库迁移而来（feishu/wecom/qq/dingtalk），主库结构保持一致。
"""
import json
import os
import psycopg2
from plugins._base.db import get_pooled_connection
from .secure_config import decrypt_fields, encrypt_fields, SecureConfigUnavailable

# 默认频道种子（channel, is_enabled）
_SEED_CHANNELS = [
    ('feishu', 1),
    ('wecom', 1),
    ('qq', 0),
    ('dingtalk', 0),
]


def get_im_db():
    """每次从共享池借取连接并设置 im_gateway schema（用完必须 close() 归还池）"""
    conn = None
    try:
        conn = get_pooled_connection()
        conn.execute("CREATE SCHEMA IF NOT EXISTS im_gateway")
        conn.execute("SET search_path TO im_gateway")
        conn.execute("SELECT 1").fetchone()
        return conn
    except psycopg2.DatabaseError as e:
        if conn is not None:
            try:
                conn.close()  # 归还（坏连接由池丢弃）
            except Exception:
                pass
        print(f'[IMGateway] ⚠️ Database damaged, reconnect: {e}')
        conn = get_pooled_connection()
        conn.execute("CREATE SCHEMA IF NOT EXISTS im_gateway")
        conn.execute("SET search_path TO im_gateway")
        return conn


def init_im_db():
    """初始化频道配置表 + 种子数据（幂等）。

    表结构与主库 channel_configs 保持完全一致，便于数据迁移。
    """
    with get_im_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_configs (
                id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                channel         TEXT NOT NULL UNIQUE,
                config_json     TEXT NOT NULL DEFAULT '{}',
                is_enabled      BIGINT DEFAULT 0,
                created_at      TEXT DEFAULT NOW(),
                updated_at      TEXT DEFAULT NOW()
            )
        """)
        # 跨 worker 频控计数表（gateway._rate_limited 使用），幂等创建。
        # DEF-16②：表名从通用 `rate_limit_events` 改为 `im_channel_rate_events`，
        # 消除与平台共享频控表 `public.rate_limit_events(rate_key, ts)` 的同名异构
        # 冲突（两条路径都用不带前缀的表名时，谁命中取决于当时连接的 search_path）。
        conn.execute("""
            CREATE TABLE IF NOT EXISTS im_channel_rate_events (
                id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                channel     TEXT NOT NULL,
                ts          TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_im_channel_rate_events_channel_ts
                ON im_channel_rate_events(channel, ts)
        """)
        # 第三方登录提供方配置表（Phase 5，方案 A：插件自包含）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS login_providers (
                id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                provider        TEXT NOT NULL UNIQUE,
                display_name    TEXT NOT NULL DEFAULT '',
                client_id       TEXT NOT NULL DEFAULT '',
                client_secret   TEXT NOT NULL DEFAULT '',
                scopes          TEXT NOT NULL DEFAULT '',
                redirect_uri    TEXT NOT NULL DEFAULT '',
                is_enabled      BIGINT DEFAULT 0,
                created_at      TEXT DEFAULT NOW(),
                updated_at      TEXT DEFAULT NOW()
            )
        """)
        # 第三方登录用户绑定表（Phase 5，方案 B：统一走绑定表，联邦身份）
        # 五平台（wechat/qq/weibo/github/google）登录用户统一落此表，
        # 不做主库 users 结构扩展；JWT 仍由 auth-center 签发。
        conn.execute("""
            CREATE TABLE IF NOT EXISTS login_user_bindings (
                id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                provider         TEXT NOT NULL,
                provider_user_id TEXT NOT NULL,
                user_id          BIGINT NOT NULL,
                username         TEXT NOT NULL DEFAULT '',
                display_name     TEXT NOT NULL DEFAULT '',
                avatar           TEXT NOT NULL DEFAULT '',
                created_at       TIMESTAMPTZ DEFAULT NOW(),
                updated_at       TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_login_bindings UNIQUE (provider, provider_user_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_login_bindings_user_id
                ON login_user_bindings(user_id)
        """)
        # 第三方登录 CSRF state 表（一次性、10 分钟过期）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oauth_login_states (
                id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                state      TEXT NOT NULL UNIQUE,
                provider   TEXT NOT NULL DEFAULT '',
                consumed   BIGINT DEFAULT 0,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        for channel, is_enabled in _SEED_CHANNELS:
            exists = conn.execute(
                "SELECT id FROM channel_configs WHERE channel=%s", (channel,)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO channel_configs (channel, config_json, is_enabled) VALUES (%s, '{}', %s)",
                    (channel, is_enabled)
                )
        conn.commit()


def migrate_from_main_db():
    """从主库 channel_configs 迁移已有配置到插件库（幂等）。

    仅当插件库对应频道 config_json 为空 '{}' 时才覆盖，避免回退用户新改的配置。
    主库无 channel_configs 表或无数据时静默跳过。
    返回迁移的记录数。

    P0-03：写入插件库前统一走字段级加密（无 ENCRYPTION_KEY 时该项不迁移，
    绝不落明文；启动期 on_install 会记录告警，可配置密钥后重跑）。
    """
    try:
        from models import get_db as get_main_db
    except Exception:
        return 0

    migrated = 0
    try:
        with get_main_db() as main:
            has_table = main.execute(
                "SELECT tablename FROM pg_catalog.pg_tables WHERE tablename='channel_configs'"
            ).fetchone()
            if not has_table:
                return 0
            rows = main.execute(
                "SELECT channel, config_json, is_enabled FROM channel_configs"
            ).fetchall()
    except Exception:
        return 0

    with get_im_db() as conn:
        for r in rows:
            channel = r['channel']
            is_enabled = r['is_enabled']
            try:
                # 主库可能为历史明文或密文；统一解密后再加密，确保插件库内为密文
                config_json = json.dumps(
                    encrypt_fields(channel, decrypt_fields(
                        channel, json.loads(r['config_json'] or '{}'))),
                    ensure_ascii=False)
            except (SecureConfigUnavailable, ValueError, TypeError) as e:
                print(f'[IMGateway] ⚠️ skip migration for {channel}: {e}')
                continue
            local = conn.execute(
                "SELECT config_json FROM channel_configs WHERE channel=%s", (channel,)
            ).fetchone()
            if local is None:
                conn.execute(
                    "INSERT INTO channel_configs (channel, config_json, is_enabled) VALUES (%s, %s, %s)",
                    (channel, config_json, is_enabled)
                )
                migrated += 1
            elif (local['config_json'] or '{}') == '{}' and config_json != '{}':
                conn.execute(
                    "UPDATE channel_configs SET config_json=%s, is_enabled=%s, updated_at=NOW() WHERE channel=%s",
                    (config_json, is_enabled, channel)
                )
                migrated += 1
        conn.commit()
    return migrated


# ── 凭据读取收敛（P0-03：所有 config_json / client_secret 读取的唯一入口） ──

def load_channel_config(channel, enabled_only=False) -> dict:
    """读取并解密单个频道配置（内部走 secure_config.decrypt_fields）。

    Args:
        channel: 频道标识（feishu / wecom / ...）
        enabled_only: True 时仅返回 is_enabled=1 的配置

    Returns:
        dict: 已解密配置；无记录或 config_json 为空时返回 {}
        解密失败抛 SecureConfigUnavailable（不静默降级为密文/明文）。
    """
    from .secure_config import decrypt_fields
    sql = "SELECT config_json FROM channel_configs WHERE channel=%s"
    params = [channel]
    if enabled_only:
        sql += " AND is_enabled=1"
    sql += " LIMIT 1"
    with get_im_db() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    if not row or not row['config_json']:
        return {}
    try:
        raw = json.loads(row['config_json'])
    except (ValueError, TypeError):
        return {}
    return decrypt_fields(channel, raw if isinstance(raw, dict) else {})


def load_login_providers() -> list:
    """读取全部第三方登录提供方配置（client_secret 已解密）。

    解密失败抛 SecureConfigUnavailable（不静默降级）。表未建时抛异常，由调用方兜底。
    """
    from .secure_config import decrypt_secret
    with get_im_db() as conn:
        rows = conn.execute(
            'SELECT provider, display_name, client_id, client_secret, scopes, '
            'redirect_uri, is_enabled FROM login_providers ORDER BY id'
        ).fetchall()
    result = []
    for r in rows:
        cfg = dict(r)
        cfg['client_secret'] = decrypt_secret(cfg.get('client_secret') or '')
        result.append(cfg)
    return result


def load_login_provider(provider) -> dict:
    """读取单个第三方登录提供方配置（client_secret 已解密）。未配置返回 {}。"""
    from .secure_config import decrypt_secret
    with get_im_db() as conn:
        row = conn.execute(
            'SELECT client_id, client_secret, scopes, redirect_uri, is_enabled '
            'FROM login_providers WHERE provider=%s', (provider,)
        ).fetchone()
    if not row:
        return {}
    cfg = dict(row)
    cfg['client_secret'] = decrypt_secret(cfg.get('client_secret') or '')
    return cfg
