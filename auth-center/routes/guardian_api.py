#!/usr/bin/env python3
"""
VeroGuard / 订阅心跳 — 官方接收端点（公开 API，HMAC+AES 鉴权）
================================================================
客户站点守护进程与授权服务的上报对端：
  POST /api/veroguard/heartbeat        — 加密心跳（存活 + 完整性 + 运行时）
  POST /api/veroguard/ack              — 远程命令执行回执（同加密协议）
  POST /api/subscription/heartbeat     — 订阅授权心跳（明文 JSON，按部署码）

鉴权说明:
  veroguard 两端点走 AES-256-GCM + HMAC-SHA256 协议（见
  services/guardian_monitor.py，与 veroguard/modules/communicator.py 对齐），
  无需登录态；subscription 端点仅按部署码查库，不泄露敏感字段。
"""
import logging

from flask import Blueprint, jsonify, request

from services.guardian_monitor import (
    GuardianAuthError, record_ack, record_heartbeat,
    subscription_heartbeat, verify_guardian_request,
)

guardian_api_bp = Blueprint('guardian_api', __name__)
logger = logging.getLogger('guardian_api')


def _client_ip() -> str:
    fwd = request.headers.get('X-Forwarded-For', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.remote_addr or ''


@guardian_api_bp.route('/api/veroguard/heartbeat', methods=['POST'])
def veroguard_heartbeat():
    """接收加密心跳 → 落库 → 响应待执行远程命令。"""
    try:
        payload, _secret = verify_guardian_request(
            raw=request.get_data(cache=False) or b'',
            nonce_b64=request.headers.get('X-VG-Nonce', ''),
            signature=request.headers.get('X-VG-Signature', ''),
            ts_str=request.headers.get('X-VG-Timestamp', ''),
        )
    except GuardianAuthError as e:
        logger.warning('heartbeat rejected from %s: %s', _client_ip(), e)  # i18n:tech — 安全审计日志
        return jsonify({'status': 'error', 'message': 'unauthorized'}), 401
    except Exception as e:
        logger.error('heartbeat verify failed: %s', e)  # i18n:tech — 诊断日志
        return jsonify({'status': 'error', 'message': 'bad request'}), 400

    payload['_nonce'] = request.headers.get('X-VG-Nonce', '')
    try:
        result = record_heartbeat(payload, _client_ip())
    except GuardianAuthError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    except Exception as e:
        logger.error('heartbeat record failed: %s', e)  # i18n:tech — 诊断日志
        return jsonify({'status': 'error', 'message': 'internal error'}), 500
    return jsonify(result)


@guardian_api_bp.route('/api/veroguard/ack', methods=['POST'])
def veroguard_ack():
    """接收远程命令执行回执（同加密协议）。"""
    try:
        payload, _secret = verify_guardian_request(
            raw=request.get_data(cache=False) or b'',
            nonce_b64=request.headers.get('X-VG-Nonce', ''),
            signature=request.headers.get('X-VG-Signature', ''),
            ts_str=request.headers.get('X-VG-Timestamp', ''),
        )
    except GuardianAuthError as e:
        logger.warning('ack rejected from %s: %s', _client_ip(), e)  # i18n:tech — 安全审计日志
        return jsonify({'status': 'error', 'message': 'unauthorized'}), 401
    except Exception as e:
        logger.error('ack verify failed: %s', e)  # i18n:tech — 诊断日志
        return jsonify({'status': 'error', 'message': 'bad request'}), 400

    try:
        result = record_ack(payload)
    except GuardianAuthError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    except Exception as e:
        logger.error('ack record failed: %s', e)  # i18n:tech — 诊断日志
        return jsonify({'status': 'error', 'message': 'internal error'}), 500
    return jsonify(result)


@guardian_api_bp.route('/api/subscription/heartbeat', methods=['POST'])
def subscription_heartbeat_api():
    """订阅授权心跳：客户端 license_service.refresh() 的服务端对端。"""
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(subscription_heartbeat(data))
    except Exception as e:
        logger.error('subscription heartbeat failed: %s', e)  # i18n:tech — 诊断日志
        return jsonify({'success': False,
                        'data': {'valid': False, 'days_remaining': 0,
                                 'message': 'internal error'}}), 500
