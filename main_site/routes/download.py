#!/usr/bin/env python3
"""官网下载入口 — 下载按钮的唯一收口（302 + 计数）

为什么不把按钮直接指向 CDN：
  直链一旦离开站点，后续下载行为就完全不可观测。把 302 放在站内，
  才有"点击 → 下载"的漏斗，并能按渠道 / 来源页归因。

边界：
  - 本模块只做 取数 → 记流水 → 302，不做业务判断，不签发任何凭证；
  - 计数为旁路，写库失败不影响跳转（见 models/download_events.record_download）；
  - 302 而非 301/308：永久重定向会被浏览器缓存，后续点击不再经过服务端，
    统计将永久漏计且无法从数据上察觉。
"""
from __future__ import annotations

import logging
import os
from urllib.parse import unquote, urlparse

from flask import Blueprint, jsonify, redirect, request

from models.download_events import record_download

logger = logging.getLogger(__name__)

download_bp = Blueprint('download', __name__, url_prefix='/api/download')

_ALLOWED_CHANNELS = ('stable', 'beta', 'internal')
_SCHEME_WHITELIST = ('http://', 'https://')


def _client_ip() -> str:
    """取真实客户端 IP。

    注意：main_site/app.py 的 ProxyFix 只修了 x_proto，没有 x_for，
    request.remote_addr 拿到的是反向代理地址。此处手动读 XFF 首段，
    避免所有请求被算成同一个 IP（否则日去重恒等于 1）。
    """
    xff = (request.headers.get('X-Forwarded-For') or '').strip()
    if xff:
        return xff.split(',')[0].strip()
    return (request.headers.get('X-Real-IP') or '').strip() or (request.remote_addr or '')


def _artifact_name(url: str) -> str:
    try:
        return os.path.basename(unquote(urlparse(url).path))[:255]
    except Exception:
        return ''


def _load_item(slug: str) -> dict | None:
    from models import get_db
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT slug, version, download_url, file_size, checksum_sha256 "
                "FROM downloads WHERE slug=%s AND is_published=1",
                (slug,),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        logger.exception('[download] 读取 downloads 失败 slug=%s', slug)  # i18n:tech
        return None


@download_bp.get('/<slug>')
def redirect_to_artifact(slug: str):
    channel = (request.args.get('channel') or 'stable').strip().lower()
    if channel not in _ALLOWED_CHANNELS:
        return jsonify({'error': 'invalid channel'}), 400

    item = _load_item(slug)
    if not item:
        return jsonify({'error': 'download item not found'}), 404

    target = (item.get('download_url') or '').strip()
    if not target.startswith(_SCHEME_WHITELIST):
        logger.warning(
            '[download] slug=%s 的 download_url 未配置或非 http(s)：「%s」',  # i18n:tech
            slug, target[:120],
        )
        return jsonify({'error': 'artifact not available'}), 503

    if request.method == 'GET':
        record_download(
            slug=slug,
            channel=channel,
            version=str(item.get('version') or ''),
            artifact=_artifact_name(target),
            source=(request.args.get('src') or '')[:64],
            referer=(request.headers.get('Referer') or '')[:512],
            user_id=None,  # 游客可下载：不鉴权、不记录用户身份
            ip=_client_ip(),
            ua=(request.headers.get('User-Agent') or '')[:512],
            request_id=(request.headers.get('X-Request-Id') or '')[:64],
        )

    resp = redirect(target, code=302)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@download_bp.get('/<slug>/meta')
def artifact_meta(slug: str):
    """供下载页展示校验信息（不计数、不跳转）。"""
    item = _load_item(slug)
    if not item:
        return jsonify({'error': 'download item not found'}), 404
    return jsonify({
        'slug': item.get('slug'),
        'version': item.get('version'),
        'artifact': _artifact_name(item.get('download_url') or ''),
        'file_size': item.get('file_size') or '',
        'checksum_sha256': item.get('checksum_sha256') or '',
    })
