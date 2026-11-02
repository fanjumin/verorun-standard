#!/usr/bin/env python3
"""
VeroGuard 站点监控 — 管理后台端点与看板页面
==============================================
官方端管理能力：
  GET  /admin/veroguard                          — 监控看板页面
  GET  /admin/api/veroguard/overview             — 站点列表 + 在线状态 + 汇总
  POST /admin/api/veroguard/deployments          — 注册/更新站点（站点级 PROBE_SECRET）
  GET  /admin/api/veroguard/heartbeats/<code>    — 站点心跳历史
  GET  /admin/api/veroguard/events               — 安全事件流（完整性告警）
  GET  /admin/api/veroguard/commands             — 命令历史
  POST /admin/api/veroguard/commands             — 下发远程命令
  GET  /admin/api/veroguard/subscriptions        — 订阅授权列表
  POST /admin/api/veroguard/subscriptions        — 登记/更新站点订阅授权

鉴权与管理端 JSON 风格与 admin/app.py 既有约定一致（Bearer/cookie JWT + is_admin）。
"""
import logging

from flask import Blueprint, jsonify, redirect, render_template, request

from services.guardian_monitor import (
    ALLOWED_ACTIONS, DESTRUCTIVE_ACTIONS,
    enqueue_command, list_commands, list_deployments, list_subscriptions,
    recent_events, recent_heartbeats, upsert_deployment, upsert_subscription,
)

veroguard_admin_bp = Blueprint('veroguard_admin', __name__)
logger = logging.getLogger('veroguard_admin')


def _admin_payload():
    from services.jwt_service import validate_token
    token = (request.headers.get('Authorization', '').replace('Bearer ', '')
             or request.cookies.get('sso_token', ''))
    if not token:
        return None
    payload = validate_token(token)
    if not payload or not payload.get('is_admin'):
        return None
    return payload


def _denied():
    return jsonify({'success': False, 'error': 'unauthorized'}), 401


# ── 看板页面 ─────────────────────────────────────────────────────────

@veroguard_admin_bp.route('/admin/veroguard', strict_slashes=False)
def veroguard_page():
    """站点监控看板（独立页面，复用 admin 登录态）。"""
    if not _admin_payload():
        return redirect('/admin/login')
    return render_template('veroguard.html')


# ── 站点总览 ─────────────────────────────────────────────────────────

@veroguard_admin_bp.route('/admin/api/veroguard/overview', methods=['GET'])
def api_overview():
    if not _admin_payload():
        return _denied()
    deployments = list_deployments()
    summary = {
        'total': len(deployments),
        'online': sum(1 for d in deployments if d['online']),
        'pending_commands': sum(d['pending_commands'] for d in deployments),
        'integrity_alerts': sum(1 for d in deployments
                                if d['last_integrity'] in ('warn', 'critical')),
    }
    return jsonify({'success': True,
                    'data': {'summary': summary, 'deployments': deployments}})


# ── 站点注册 ─────────────────────────────────────────────────────────

@veroguard_admin_bp.route('/admin/api/veroguard/deployments', methods=['POST'])
def api_upsert_deployment():
    admin = _admin_payload()
    if not admin:
        return _denied()
    data = request.get_json(silent=True) or {}
    try:
        result = upsert_deployment(
            deployment_code=data.get('deployment_code', ''),
            probe_secret=data.get('probe_secret', ''),
            display_name=data.get('display_name', ''),
            site_url=data.get('site_url', ''),
            notes=data.get('notes', ''),
            heartbeat_interval=data.get('heartbeat_interval', 0),
        )
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error('upsert deployment failed: %s', e)  # i18n:tech — 诊断日志
        return jsonify({'success': False, 'error': 'internal error'}), 500
    return jsonify({'success': True, 'data': result})


# ── 心跳历史 ─────────────────────────────────────────────────────────

@veroguard_admin_bp.route('/admin/api/veroguard/heartbeats/<code>', methods=['GET'])
def api_heartbeats(code: str):
    if not _admin_payload():
        return _denied()
    try:
        limit = int(request.args.get('limit', '50'))
    except ValueError:
        limit = 50
    return jsonify({'success': True,
                    'data': {'heartbeats': recent_heartbeats(code, limit)}})


# ── 安全事件 ─────────────────────────────────────────────────────────

@veroguard_admin_bp.route('/admin/api/veroguard/events', methods=['GET'])
def api_events():
    if not _admin_payload():
        return _denied()
    try:
        limit = int(request.args.get('limit', '30'))
    except ValueError:
        limit = 30
    return jsonify({'success': True, 'data': {'events': recent_events(limit)}})


# ── 远程命令 ─────────────────────────────────────────────────────────

@veroguard_admin_bp.route('/admin/api/veroguard/commands', methods=['GET'])
def api_commands():
    if not _admin_payload():
        return _denied()
    return jsonify({'success': True, 'data': {'commands': list_commands()}})


@veroguard_admin_bp.route('/admin/api/veroguard/commands', methods=['POST'])
def api_enqueue_command():
    admin = _admin_payload()
    if not admin:
        return _denied()
    data = request.get_json(silent=True) or {}
    params = data.get('params')
    if params is not None and not isinstance(params, dict):
        return jsonify({'success': False, 'error': 'params must be an object'}), 400
    try:
        result = enqueue_command(
            deployment_code=data.get('deployment_code', ''),
            action=data.get('action', ''),
            params=params or {},
            created_by=str(admin.get('username') or admin.get('sub') or ''),
        )
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error('enqueue command failed: %s', e)  # i18n:tech — 诊断日志
        return jsonify({'success': False, 'error': 'internal error'}), 500
    if result.get('destructive'):
        # fail-closed 提示：破坏性命令还需站点本地 VG_OPS_CONFIRM 才会执行
        result['notice'] = (
            'Destructive command queued. It will only execute if the target '
            'site has VG_OPS_CONFIRM configured and the ops_token in params matches.')
    return jsonify({'success': True, 'data': result})


# ── 订阅授权 ─────────────────────────────────────────────────────────

@veroguard_admin_bp.route('/admin/api/veroguard/subscriptions', methods=['GET'])
def api_subscriptions():
    if not _admin_payload():
        return _denied()
    return jsonify({'success': True,
                    'data': {'subscriptions': list_subscriptions(),
                             'actions_allowed': sorted(ALLOWED_ACTIONS),
                             'destructive_actions': sorted(DESTRUCTIVE_ACTIONS)}})


@veroguard_admin_bp.route('/admin/api/veroguard/subscriptions', methods=['POST'])
def api_upsert_subscription():
    if not _admin_payload():
        return _denied()
    data = request.get_json(silent=True) or {}
    try:
        result = upsert_subscription(
            code=data.get('code', ''),
            valid=bool(data.get('valid', False)),
            days_remaining=data.get('days_remaining', 0),
            expires_at=data.get('expires_at', ''),
            message=data.get('message', ''),
            plan=data.get('plan', ''),
        )
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error('upsert subscription failed: %s', e)  # i18n:tech — 诊断日志
        return jsonify({'success': False, 'error': 'internal error'}), 500
    return jsonify({'success': True, 'data': result})
