#!/usr/bin/env python3
# tools/migrate_im_credentials.py — IM Gateway 历史明文凭据收敛为密文（P0-03）
#
# 背景：P0-03 之前 im_gateway 的凭据（channel_configs.config_json 各频道 secret/
#   token 字段、login_providers.client_secret）为明文落库。P0-03 落地后读路径仍兼容
#   明文（只记 WARNING），因此不迁移也不会立刻故障，但明文残留是安全尽调的一票否决项。
#
# 用法（默认 dry-run，不写库）：
#   python plugins/im_gateway/tools/migrate_im_credentials.py
#   python plugins/im_gateway/tools/migrate_im_credentials.py --apply
#
# 纪律：
#   - 必须配置 ENCRYPTION_KEY，否则 encrypt 抛 SecureConfigUnavailable，脚本拒绝写库；
#   - 写库前请先备份 channel_configs / login_providers 两表（结构备份即可，勿导出明文环境）。
from __future__ import annotations

import argparse
import json
import os
import sys

# 工具脚本位于 plugins/im_gateway/tools/，项目根 = 上四级
# （上三级只到 plugins/，会把 plugins/email 遮蔽标准库 email，必须到仓库根）
_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plugins.im_gateway.models import get_im_db  # noqa: E402
from plugins.im_gateway.secure_config import (  # noqa: E402
    SENSITIVE_FIELDS,
    SecureConfigUnavailable,
    _crypto_available,
    encrypt_secret,
    encrypt_fields,
    is_encrypted,
)


def _print_table(rows) -> None:
    width = max((len(r[0]) for r in rows), default=20)
    print(f"  {'key'.ljust(width)} | 已加密 | 结果")
    print(f"  {'-' * width}-+--------+------------------")
    for key, encrypted, result in rows:
        print(f"  {key.ljust(width)} | {encrypted.center(6)} | {result}")


def _scan_channels(conn):
    """扫描 channel_configs，返回 (报告行, 待加密 {channel: cfg})。"""
    rows_out, pending = [], {}
    rows = conn.execute(
        'SELECT channel, config_json FROM channel_configs ORDER BY id'
    ).fetchall()
    for r in rows:
        channel = r['channel']
        fields = SENSITIVE_FIELDS.get(channel, ())
        try:
            cfg = json.loads(r['config_json'] or '{}')
        except (ValueError, TypeError):
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        if not fields:
            rows_out.append((channel, '-', 'skipped: 无敏感字段'))
            continue
        need = False
        for key in fields:
            val = cfg.get(key)
            if not isinstance(val, str) or not val.strip():
                continue
            if is_encrypted(val):
                rows_out.append((f'{channel}.{key}', 'yes', 'skipped: 已是密文'))
            else:
                rows_out.append((f'{channel}.{key}', 'no', 'pending: 待加密'))
                need = True
        if need:
            pending[channel] = cfg
    return rows_out, pending


def _scan_login_providers(conn):
    """扫描 login_providers.client_secret，返回 (报告行, 待加密 {provider: secret})。"""
    rows_out, pending = [], {}
    try:
        rows = conn.execute(
            'SELECT provider, client_secret FROM login_providers ORDER BY id'
        ).fetchall()
    except Exception as e:  # 表未建
        rows_out.append(('login_providers', '-', f'skipped: {e}'))
        return rows_out, pending
    for r in rows:
        provider = r['provider']
        secret = r['client_secret'] or ''
        if not secret:
            rows_out.append((f'login.{provider}', '-', 'skipped: 未配置'))
        elif is_encrypted(secret):
            rows_out.append((f'login.{provider}', 'yes', 'skipped: 已是密文'))
        else:
            rows_out.append((f'login.{provider}', 'no', 'pending: 待加密'))
            pending[provider] = secret
    return rows_out, pending


def main() -> int:
    ap = argparse.ArgumentParser(description='将 IM Gateway 历史明文凭据收敛为密文（P0-03）')
    ap.add_argument('--apply', action='store_true', help='真正写库（默认 dry-run 只报告）')
    args = ap.parse_args()

    if args.apply and not _crypto_available():
        print('[migrate] 拒绝写库：未配置 ENCRYPTION_KEY。请配置后重试。')
        return 2

    with get_im_db() as conn:
        ch_rows, ch_pending = _scan_channels(conn)
        lg_rows, lg_pending = _scan_login_providers(conn)

        print('[migrate] channel_configs.config_json 凭据状态：')
        _print_table(ch_rows)
        print('[migrate] login_providers.client_secret 状态：')
        _print_table(lg_rows)

        if not ch_pending and not lg_pending:
            print('[migrate] 无明文凭据，无需迁移。')
            return 0
        if not args.apply:
            print('[migrate] dry-run：未写库。确认无误后加 --apply；'
                  '写库前请先备份 channel_configs / login_providers 两表。')
            return 0

        count = 0
        for channel, cfg in ch_pending.items():
            try:
                stored = encrypt_fields(channel, cfg)
            except SecureConfigUnavailable as exc:
                print(f'[migrate] 失败 {channel}: {exc} —— 未写库；请配置 ENCRYPTION_KEY 后重试。')
                return 2
            conn.execute(
                'UPDATE channel_configs SET config_json=%s, updated_at=NOW() WHERE channel=%s',
                (json.dumps(stored, ensure_ascii=False), channel))
            count += 1
        for provider, secret in lg_pending.items():
            try:
                enc = encrypt_secret(secret)
            except SecureConfigUnavailable as exc:
                print(f'[migrate] 失败 login.{provider}: {exc} —— 未写库。')
                return 2
            conn.execute(
                'UPDATE login_providers SET client_secret=%s, updated_at=NOW() WHERE provider=%s',
                (enc, provider))
            count += 1
        conn.commit()

    print(f'[migrate] 已加密并写回 {count} 项。建议重启相关服务使进程内缓存失效。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
