#!/usr/bin/env python3
"""主题查询辅助 — 供 main_site 读取站点当前激活的主题 slug。

历史遗留：本文件曾是 `admin/routes/theme_admin.py` 的整份字节副本（含 theme_bp 蓝图），
但 auth-center 侧从未注册该蓝图，main_site 也只用到这里的一个查询函数。
为避免同一份逻辑双份维护产生漂移，此处仅保留被使用的查询；
主题管理 API 的唯一实现位于 `admin/routes/theme_admin.py`。
"""


def _get_db():
    from models import get_db
    return get_db()


def get_active_theme_slug_for_site(site_key):
    """查询某站点当前激活的主题 slug，返回 None 表示默认"""
    try:
        with _get_db() as conn:
            row = conn.execute(
                'SELECT t.slug FROM site_theme_config s '
                'LEFT JOIN themes t ON s.theme_id = t.id '
                'WHERE s.site_key=%s', (site_key,)
            ).fetchone()
        if row and row['slug'] and row['slug'] != 'default':
            return row['slug']
    except Exception:
        pass
    return None
