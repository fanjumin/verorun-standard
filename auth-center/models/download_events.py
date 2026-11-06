#!/usr/bin/env python3
"""下载事件流水 — download_events

职责边界：
  - 只负责记录"一次下载跳转"并提供聚合查询；
  - 不决定是否允许下载（该判断在 main_site/routes/download.py）；
  - 计数属旁路能力：record_download() 吞掉全部异常并只记日志。
    数据库不可用时用户仍必须能拿到安装包 —— 统计失败绝不能变成下载失败。

建表策略：与 models/cms.py 同构，模块内 CREATE TABLE IF NOT EXISTS，
进程内只执行一次（_TABLE_READY + 锁），避免每次下载多一次 DDL 往返。
不修改 models/database.py 的 Mega DDL 块，降低对核心迁移流程的侵入。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
from typing import Any, Optional

from models import get_db

logger = logging.getLogger(__name__)

_TABLE_READY = False
_TABLE_LOCK = threading.Lock()
_SALT_WARNED = False

_DDL_TABLE = """
CREATE TABLE IF NOT EXISTS download_events (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts          TIMESTAMP NOT NULL DEFAULT NOW(),
    slug        TEXT NOT NULL,
    channel     TEXT NOT NULL DEFAULT 'stable',
    version     TEXT DEFAULT '',
    artifact    TEXT DEFAULT '',
    source      TEXT DEFAULT '',
    referer     TEXT DEFAULT '',
    user_id     BIGINT,
    ip_hash     TEXT DEFAULT '',
    ua_hash     TEXT DEFAULT '',
    request_id  TEXT DEFAULT ''
)
"""

_DDL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_download_events_ts ON download_events(ts)",
    "CREATE INDEX IF NOT EXISTS idx_download_events_slug ON download_events(slug, ts)",
    "CREATE INDEX IF NOT EXISTS idx_download_events_channel ON download_events(channel, ts)",
)

_SUMMARY_SQL = """
SELECT to_char(date_trunc('day', ts), 'YYYY-MM-DD') AS day,
       channel,
       count(*) AS clicks,
       count(DISTINCT (COALESCE(ip_hash, '') || ':' || COALESCE(ua_hash, ''))) AS uniques
FROM download_events
WHERE ts >= NOW() - make_interval(days => %s)
GROUP BY 1, 2
ORDER BY 1 DESC, 2
"""


def _salt() -> str:
    """去重盐。缺失时退化为无盐哈希（可运行但可被彩虹表反推），只在启动日志告警一次。"""
    global _SALT_WARNED
    salt = (os.environ.get('DOWNLOAD_IP_SALT') or '').strip()
    if not salt and not _SALT_WARNED:
        _SALT_WARNED = True
        logger.warning(
            '[download_events] DOWNLOAD_IP_SALT 未配置：IP/UA 去重哈希无盐，'  # i18n:tech
            '建议在 .env 中补一个随机串后再上线'  # i18n:tech
        )
    return salt


def _digest(value: str, salt: str) -> str:
    if not value:
        return ''
    return hmac.new(
        salt.encode('utf-8'),
        value.encode('utf-8', 'replace'),
        hashlib.sha256,
    ).hexdigest()[:32]


def ensure_table() -> None:
    """幂等建表。异常向上抛，由 record_download 统一吞。"""
    global _TABLE_READY
    if _TABLE_READY:
        return
    with _TABLE_LOCK:
        if _TABLE_READY:
            return
        with get_db() as conn:
            conn.execute(_DDL_TABLE)
            for idx_sql in _DDL_INDEXES:
                conn.execute(idx_sql)
        _TABLE_READY = True
        logger.info('[download_events] 表与索引就绪')  # i18n:tech


def record_download(
    *,
    slug: str,
    channel: str = 'stable',
    version: str = '',
    artifact: str = '',
    source: str = '',
    referer: str = '',
    user_id: Optional[int] = None,
    ip: str = '',
    ua: str = '',
    request_id: str = '',
) -> bool:
    """记录一次下载点击。永不抛错：返回 True/False 仅用于日志与测试断言。"""
    try:
        ensure_table()
        salt = _salt()
        with get_db() as conn:
            conn.execute(
                "INSERT INTO download_events "
                "(slug, channel, version, artifact, source, referer, user_id, ip_hash, ua_hash, request_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    slug, channel, version, artifact, source, referer,
                    user_id, _digest(ip, salt), _digest(ua, salt), request_id,
                ),
            )
            # 兼容既有 CMS 列表页的 download_count 展示列（明细才是事实源）
            conn.execute(
                "UPDATE downloads SET download_count = download_count + 1, updated_at = NOW() "
                "WHERE slug = %s",
                (slug,),
            )
        return True
    except Exception:
        logger.exception(
            '[download_events] 记录失败（已忽略，不影响下载） slug=%s channel=%s',  # i18n:tech
            slug, channel,
        )
        return False


def summary(days: int = 30) -> list[dict[str, Any]]:
    """按天 × 渠道聚合。clicks=点击口径，uniques=IP+UA 日去重口径。"""
    try:
        with get_db() as conn:
            rows = conn.execute(_SUMMARY_SQL, (int(days),)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception('[download_events] 聚合查询失败 days=%s', days)  # i18n:tech
        return []


def verify_unique_logic() -> dict[str, Any]:
    """自检：真实 IP 是否被反向代理吞掉。

    若 ProxyFix 未修 x_for，同一出口的 ip_hash 会全部相同，
    count(distinct) 会错误地恒为 1。此函数用于上线后第一次核对。
    """
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT count(*) AS total, "
                "       count(DISTINCT ip_hash) AS distinct_ip, "
                "       count(DISTINCT ua_hash) AS distinct_ua "
                "FROM download_events"
            ).fetchone()
        data = dict(row) if row else {}
        total = int(data.get('total') or 0)
        distinct_ip = int(data.get('distinct_ip') or 0)
        return {
            'total': total,
            'distinct_ip': distinct_ip,
            'distinct_ua': int(data.get('distinct_ua') or 0),
            # 点击超过 5 次却只有 1 个 IP 哈希 → 极可能是真实 IP 未被透传
            'ip_passthrough_suspect': total >= 5 and distinct_ip <= 1,
        }
    except Exception:
        logger.exception('[download_events] 自检失败')  # i18n:tech
        return {'error': 'query failed'}
