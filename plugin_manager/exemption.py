#!/usr/bin/env python3
"""
豁免 Key 管理
==============
管理员签发豁免 Key → 交付指定用户/站点 → 站点管理员兑换 → 动态获得
scope 内插件的 License（plugin_licenses active 记录）。

设计要点:
  - Key 是「预签发的 License Key」：兑换复用 plugin_licenses 唯一校验口
    （LicenseManager.validate），内核与校验逻辑零改动。
  - DB 只存 SHA256(key)（key_hash），明文仅在创建响应中出现一次，不落库。
  - scope_plugins: ["*"] = 全部付费插件豁免；或显式插件 identifier 列表。
  - max_redemptions 限制可兑换站点数；redeemed_sites 留审计链。
  - expires_at 为空 = 永久豁免；否则兑换出的 License 到该时间自动过期
    （validate() 既有逻辑处理）。
"""
import hashlib
import json
import os
import secrets
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from .license import LicenseManager, get_site_id
from .models_store import (
    LicenseRecord, LicenseStatus, LicenseType, get_registry_db,
    init_license_store_tables,
)

KEY_PREFIX = 'VREX'

# ── 跨站点在线兑换（用户站 ← 官方站）────────────────────────────
# 官方站（签发方）两个配置项（用户站侧 env，缺一即回退纯本地兑换）：
#   EXEMPTION_ISSUER_URL    — 官方站基址，如 https://www.verorun.com
#   EXEMPTION_ISSUER_SECRET — 官方站 PLUGIN_LICENSE_SECRET（验 HMAC 票据用）


def _issuer_config() -> Optional[tuple]:
    """返回 (base_url, secret)；未配置返回 None（=纯本地模式）"""
    base = (os.environ.get('EXEMPTION_ISSUER_URL') or '').strip().rstrip('/')
    secret = (os.environ.get('EXEMPTION_ISSUER_SECRET') or '').strip()
    if base and secret:
        return (base, secret)
    return None


def _verify_issuer_ticket(ticket: str, site_id: str, secret: str) -> Optional[dict]:
    """验官方站 HMAC 票据，返回 payload；任何异常返回 None（fail-closed）

    票据格式与 license.generate_offline_token 同构：b64(json_payload.hmac[:32])
    payload = {'s': site_id, 'scope': [...], 'e': expires_at, 'ts': iso, 'v': 1}
    """
    import base64
    import hmac

    try:
        decoded = base64.urlsafe_b64decode(ticket.encode()).decode()
        payload_str, sig = decoded.rsplit('.', 1)
        payload = json.loads(payload_str)
        expected = hmac.new(secret.encode(), payload_str.encode(),
                            hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig.encode(), expected.encode()):
            return None
        if payload.get('v') != 1 or payload.get('s') != site_id:
            return None
        return payload
    except Exception:
        return None


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def _format_key(raw: str) -> str:
    """VREX-XXXX-XXXX-XXXX-XXXX（可读分组，纯大写十六进制）"""
    h = raw.upper()
    return '-'.join([KEY_PREFIX] + [h[i:i + 4] for i in range(0, 16, 4)])


class ExemptionKeyManager:
    """豁免 Key：签发 / 兑换 / 吊销 / 审计"""

    def __init__(self):
        self._lock = threading.Lock()
        init_license_store_tables()
        self._licenses = LicenseManager()

    # ── 签发 ─────────────────────────────────────────────────────────

    def create_key(self, plugins: List[str], max_redemptions: int = 1,
                   expires_at: Optional[str] = None,
                   granted_by: str = '', note: str = '') -> Dict[str, Any]:
        """签发豁免 Key。明文 Key 仅在本返回值中出现一次。"""
        if not plugins:
            raise ValueError(_('plugins must be a non-empty list or ["*"]'))
        if max_redemptions < 1:
            raise ValueError(_('max_redemptions must be >= 1'))

        raw = secrets.token_hex(16)
        key = _format_key(raw)
        with self._lock, get_registry_db() as conn:
            conn.execute("""
                INSERT INTO plugin_exemption_keys (
                    key_hash, key_prefix, scope_plugins,
                    max_redemptions, key_status, expires_at,
                    granted_by, note, created_at, updated_at
                ) VALUES (%s,%s,%s,%s,'active',%s,%s,%s,NOW(),NOW())
            """, (
                _hash_key(key), key[:12], json.dumps(plugins),
                int(max_redemptions), expires_at, granted_by, note,
            ))
            conn.commit()
            row = conn.execute(
                'SELECT id FROM plugin_exemption_keys WHERE key_hash=%s',
                (_hash_key(key),)
            ).fetchone()
        return {
            'id': row['id'] if row else None,
            'key': key,               # 明文，仅此一次
            'plugins': plugins,
            'max_redemptions': max_redemptions,
            'expires_at': expires_at or '',
            'granted_by': granted_by,
            'note': note,
        }

    # ── 兑换 ─────────────────────────────────────────────────────────

    def redeem_key(self, key: str, customer_email: str = '') -> Dict[str, Any]:
        """在目标站点兑换：按 scope 写入 plugin_licenses（active）。

        Returns:
            {'success': bool, 'activated': [plugin_id...], 'error': str}
        """
        key = (key or '').strip()
        if not key:
            raise ValueError(_('key required'))

        # 跨站点桥接（2026-11-04 豁免方案）：本地无此 Key 且配置了官方站
        # （EXEMPTION_ISSUER_URL + EXEMPTION_ISSUER_SECRET）→ 在线兑换。
        # 预检只读不占锁；本地 Key 的消耗仍由下方原子 UPDATE 保证。
        with get_registry_db() as conn:
            _hit = conn.execute(
                'SELECT 1 FROM plugin_exemption_keys WHERE key_hash=%s',
                (_hash_key(key),)
            ).fetchone()
        if _hit is None:
            issuer = _issuer_config()
            if issuer:
                return self._redeem_remote(key, customer_email, issuer)
            # 未配置官方站：维持原语义，走下方本地路径抛「未知 Key」

        with self._lock, get_registry_db() as conn:
            row = conn.execute(
                'SELECT * FROM plugin_exemption_keys WHERE key_hash=%s',
                (_hash_key(key),)
            ).fetchone()

            if row is None:
                raise ValueError(_('Invalid or unknown exemption key'))
            if row['key_status'] != 'active':
                raise ValueError(_('Exemption key has been revoked'))
            if row['expires_at']:
                try:
                    if datetime.now() > datetime.fromisoformat(row['expires_at']):
                        raise ValueError(_('Exemption key has expired'))
                except ValueError:
                    raise
                except Exception:
                    pass
            if row['redeemed_count'] >= row['max_redemptions']:
                raise ValueError(_('Exemption key has reached its redemption limit'))

            site_id = get_site_id()
            scope = json.loads(row['scope_plugins'] or '[]')

            # 原子占坑： redemption 计数 + 站点审计一次性更新
            sites = json.loads(row['redeemed_sites'] or '[]')
            sites.append(site_id)
            conn.execute("""
                UPDATE plugin_exemption_keys
                SET redeemed_count = redeemed_count + 1,
                    redeemed_sites = %s, updated_at = NOW()
                WHERE id = %s AND redeemed_count < max_redemptions
                  AND key_status = 'active'
            """, (json.dumps(sites), row['id']))
            if conn.execute('SELECT changes() AS n').fetchone()['n'] != 1:
                raise ValueError(_('Exemption key has reached its redemption limit'))
            conn.commit()

        # scope = ["*"] 时解析商店内全部付费插件；空商店则报错防误用
        if scope == ['*']:
            scope = self._paid_plugin_ids()
            if not scope:
                raise ValueError(_('No paid plugins in store to exempt'))

        # 逐插件写入 License（复用 _save_license 唯一写路径）
        activated = self._activate_scope(
            scope, key, row['expires_at'], customer_email,
            key_id=row['id'], source='exemption_key')

        return {'success': True, 'activated': activated,
                'expires_at': row['expires_at'] or ''}

    def _activate_scope(self, scope: List[str], key: str, expires_at: Optional[str],
                        customer_email: str, key_id: Optional[int],
                        source: str) -> List[str]:
        """按 scope 逐插件写 active License（本地与在线兑换共用的唯一写路径）"""
        activated: List[str] = []
        for plugin_id in scope:
            record = LicenseRecord(
                plugin_id=plugin_id,
                license_key=f'{key}#{plugin_id}',  # license_key UNIQUE，派生键
                license_type=LicenseType.FREE,
                license_status=LicenseStatus.ACTIVE,
                site_id=get_site_id(),
                customer_email=customer_email,
                activated_at=datetime.now().isoformat(),
                expires_at=expires_at,
                metadata={'source': source, 'key_id': key_id},
                last_validated=datetime.now().isoformat(),
            )
            self._licenses._save_license(record)
            activated.append(plugin_id)
        return activated

    def _redeem_remote(self, key: str, customer_email: str,
                       issuer: tuple) -> Dict[str, Any]:
        """在线兑换：回调官方站 redeem-remote → 验 HMAC 票据 → 本地写 License

        消耗（redeemed_count）只发生在官方站；本地仅产出 License 记录。
        任一环节失败即抛 ValueError（fail-closed），不产生半授权。
        """
        from urllib.request import Request, urlopen

        base, secret = issuer
        site_id = get_site_id()
        url = f'{base}/admin/plugins/exemption-keys/redeem-remote'
        req = Request(
            url,
            data=json.dumps({'key': key, 'site_id': site_id}).encode(),
            method='POST',
        )
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', 'PluginManager/1.0')
        try:
            resp = urlopen(req, timeout=10)
            body = json.loads(resp.read().decode())
        except Exception as e:
            raise ValueError(f'{_("Exemption issuer unreachable")}: {e}')

        data = body.get('data') or {}
        if not body.get('success') or not data.get('ticket'):
            raise ValueError(body.get('error') or _('Exemption redemption failed'))

        payload = _verify_issuer_ticket(data['ticket'], site_id, secret)
        if payload is None:
            raise ValueError(_('Exemption issuer ticket verification failed'))

        scope = payload.get('scope') or []
        if scope == ['*']:
            scope = self._paid_plugin_ids()
            if not scope:
                raise ValueError(_('No paid plugins in store to exempt'))

        expires_at = payload.get('e') or ''
        activated = self._activate_scope(
            scope, key, expires_at, customer_email,
            key_id=None, source='exemption_key_remote')

        return {'success': True, 'activated': activated,
                'expires_at': expires_at, 'source': 'remote'}

    def _paid_plugin_ids(self) -> List[str]:
        """商店内全部付费插件 identifier（scope=["*"] 用）"""
        with get_registry_db() as conn:
            rows = conn.execute(
                "SELECT identifier FROM store_plugins WHERE price_type != 'free'"
            ).fetchall()
        return [r['identifier'] for r in rows]

    # ── 查询 / 吊销 ──────────────────────────────────────────────────

    def list_keys(self) -> List[Dict[str, Any]]:
        """列出全部豁免 Key（不含哈希，含对账前缀与审计字段）"""
        with get_registry_db() as conn:
            rows = conn.execute("""
                SELECT id, key_prefix, scope_plugins, max_redemptions,
                       redeemed_count, redeemed_sites, key_status,
                       expires_at, granted_by, note, created_at, updated_at
                FROM plugin_exemption_keys ORDER BY id DESC
            """).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d['scope_plugins'] = json.loads(d.get('scope_plugins') or '[]')
            d['redeemed_sites'] = json.loads(d.get('redeemed_sites') or '[]')
            out.append(d)
        return out

    def revoke_key(self, key_id: int) -> bool:
        """吊销：已兑换出的 License 不回收（尊重已交付权益），仅阻断后续兑换"""
        with self._lock, get_registry_db() as conn:
            cur = conn.execute(
                "UPDATE plugin_exemption_keys SET key_status='revoked', "
                'updated_at=NOW() WHERE id=%s AND key_status=%s',
                (key_id, 'active'),
            )
            conn.commit()
            return cur.rowcount > 0


# ── i18n（与 routes.py 相同的 fallback 语义）──────────────────────────

try:
    from i18n import _  # noqa: E402
except ImportError:  # pragma: no cover
    def _(s, **kwargs):
        return s
