#!/usr/bin/env python3
"""DAG 节点：把发信变成工作流一等公民（WP-E1）。

设计要点：
  * 落点在本插件内，经 BasePlugin.register_dag_nodes() 由平台注册进引擎，
    内核不需要知道邮件业务的存在（插件声明业务）。
  * 处理器签名固定为 handler(node_def, input_data)，与 veroscholar /
    stock_analysis / project_workspace 的既有契约一致。
  * 失败必须抛异常：引擎只在异常时把节点标记为 failed；返回
    {'success': False} 会被当成功完成（workflow_engine.py:_execute_node），
    那就是假成功。抛错前先写 email_sent(status='failed')，保证可审计。
  * 幂等键 dedup_key 只落在成功记录上（部分唯一索引）。失败记录不带键，
    便于重跑；成功后重复执行会因 pre-check 直接跳过，不会二次外发。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List

from plugin_manager.logger import get_plugin_logger

logger = get_plugin_logger('email')

_VAR_RE = re.compile(r'\{\{\s*([A-Za-z0-9_.]+)\s*\}\}')


def _lookup(ctx: Dict[str, Any], path: str) -> Any:
    """按点号路径从上下文取值（缺失返回空串）。"""
    cur: Any = ctx
    for part in path.split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return ''
    return cur


def _render(value: Any, ctx: Dict[str, Any]) -> Any:
    """把 `{{ key }}` / `{{ a.b }}` 渲染成上下文值；支持 str/list/dict。"""
    if isinstance(value, str):
        return _VAR_RE.sub(lambda m: str(_lookup(ctx, m.group(1))), value)
    if isinstance(value, list):
        return [_render(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: _render(v, ctx) for k, v in value.items()}
    return value


def _as_list(value: Any) -> List[str]:
    """收件人归一：支持 list / 逗号或分号分隔的字符串。"""
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [p.strip() for p in re.split(r'[;,，；]', value) if p.strip()]
    return []


def _dedup_key(instance_id: str, node_id: str, recipients: List[str]) -> str:
    """同一实例 + 同一节点 + 同一收件人集合 → 同一键。"""
    raw = '{}|{}|{}'.format(instance_id or '', node_id or '',
                            ','.join(sorted(a.lower() for a in recipients)))
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


def _already_sent(dedup_key: str) -> bool:
    """成功记录是否已存在。查询失败按“未发送”处理并告警——发信本身可降级，
    但绝不因为审计库不可用而静默成功（成功仍会由 email_sent 落库兜底）。"""
    try:
        from .models import get_email_db
        with get_email_db() as db:
            row = db.execute(
                "SELECT 1 FROM email_sent WHERE dedup_key = %s AND status = 'sent' LIMIT 1",
                (dedup_key,)).fetchone()
            return bool(row)
    except Exception as e:  # noqa: BLE001
        logger.warning('dedup pre-check failed: %s', e)
        return False


def handle_send_email(node_def: Dict[str, Any], input_data: Dict[str, Any],
                      pm: Any = None) -> Dict[str, Any]:
    """发送一封邮件（支持 cc/bcc/reply_to/HTML/附件/上下文变量渲染）。

    pm: 插件注册节点时注入的 PluginManager 引用。工作流引擎在裸线程里执行
    节点，没有 Flask app context，必须靠它读后台 SMTP 配置（WP-E2）。

    config:
      to / cc / bcc / reply_to / subject / body / body_html
      attachments: [{"filename","data"(base64),"content_type"}]
    """
    config = node_def.get('config') or {}
    ctx = (input_data or {}).get('context') or {}
    instance_id = str((input_data or {}).get('_instance_id') or '')
    node_id = str(node_def.get('id') or '')

    to_list = _as_list(_render(config.get('to'), ctx))
    subject = str(_render(config.get('subject'), ctx) or '').strip()
    body_text = str(_render(config.get('body'), ctx) or '')
    body_html = _render(config.get('body_html'), ctx) or None
    cc = _as_list(_render(config.get('cc'), ctx))
    bcc = _as_list(_render(config.get('bcc'), ctx))
    reply_to = str(_render(config.get('reply_to'), ctx) or '').strip() or None
    attachments = _render(config.get('attachments'), ctx) or None

    if not to_list:
        raise RuntimeError('send_email: config.to is empty')
    if not subject:
        raise RuntimeError('send_email: config.subject is empty')
    if not body_text and not body_html:
        raise RuntimeError('send_email: config.body / body_html is empty')

    key = _dedup_key(instance_id, node_id, to_list)
    if instance_id and _already_sent(key):
        logger.info('send_email skipped (already sent): node=%s to=%s', node_id, to_list)
        return {'success': True, 'skipped': True, 'reason': 'duplicate',
                'dedup_key': key, 'to': to_list}

    from . import services

    ok, message = services.send_email(
        to_list, subject, body_text,
        body_html=body_html, cc=cc, bcc=bcc, reply_to=reply_to,
        attachments=attachments, dedup_key=key or None, pm=pm)

    if not ok:
        services.log_failed_send(to_list, subject, body_text, message)
        raise RuntimeError('send_email failed: {}'.format(message))

    return {'success': True, 'to': to_list, 'subject': subject,
            'dedup_key': key, 'message': message}


def get_dag_nodes() -> Dict[str, Any]:
    """返回 {节点类型: 处理器}，由插件基类注册到 WorkflowEngine。"""
    return {
        'send_email': handle_send_email,
    }
