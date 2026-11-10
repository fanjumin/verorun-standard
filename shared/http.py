#!/usr/bin/env python3
"""
统一 API 响应契约（后端对齐阶段 1）
====================================
信封格式（全站统一）：
  成功: {"success": true,  "data": ..., "code": 0}
  失败: {"success": false, "error": "...", "code": <业务错误码>}

code=0 表示成功；业务错误码 ≥1000，按域分段（4xxx 客户端错误 / 5xxx 服务端错误）。
HTTP 状态码仅表示传输层语义，业务细节由 code 承载。
"""
from flask import jsonify, request

from i18n import _

ERROR_CODES = {
    'PARAM_INVALID': 40001,
    'NOT_AUTHENTICATED': 40101,
    'FORBIDDEN': 40301,
    'NOT_FOUND': 40401,
    'CONFLICT': 40901,
    'RATE_LIMITED': 42901,
    'NEEDS_CONFIG': 42201,
    'INTERNAL': 50001,
    'PAYMENT_NOT_CONFIGURED': 50002,
}

_DEFAULT_CODE_BY_HTTP = {
    400: ERROR_CODES['PARAM_INVALID'],
    401: ERROR_CODES['NOT_AUTHENTICATED'],
    403: ERROR_CODES['FORBIDDEN'],
    404: ERROR_CODES['NOT_FOUND'],
    409: ERROR_CODES['CONFLICT'],
    429: ERROR_CODES['RATE_LIMITED'],
    500: ERROR_CODES['INTERNAL'],
}


def api_ok(data=None, code=0):
    """成功响应：{success: True, data: ..., code: 0}。"""
    return jsonify({'success': True, 'data': data, 'code': code})


def api_err(msg, http_code=400, code=None, **extra):
    """失败响应：{success: False, error: msg, code: <业务码>} + HTTP 状态码。

    code 未显式指定时按 http_code 映射默认业务错误码；
    extra 字段合并进响应体（如校验明细）。
    """
    if code is None:
        code = _DEFAULT_CODE_BY_HTTP.get(http_code, ERROR_CODES['PARAM_INVALID'])
    body = {'success': False, 'error': msg, 'code': code}
    body.update(extra)
    return jsonify(body), http_code


def needs_config_response(msg, config_path=''):
    """缺前置配置（非故障）统一响应：422 + 结构化标记。

    为什么是 422 而不是 5xx：5xx 在桌面壳层被翻译成「程序坏了」（如 502 →
    「服务器网关异常」），而「邮箱未配置 / 数据源缺 Key」是用户可自解的前置条件，
    必须与故障在传输层就分开。error_code='NEEDS_CONFIG' 与 meta.needs_config
    是桌面壳层 http.ts 拦截器的识别契约：命中即抑制全局故障红字，改走配置引导。
    config_path 为壳层路由（如 '/mail?tab=config'），供引导跳转使用。
    """
    return api_err(
        msg, 422, code=ERROR_CODES['NEEDS_CONFIG'],
        error_code='NEEDS_CONFIG',
        meta={'needs_config': True, 'config_path': config_path},
    )


def _parse_positive_int(name: str, default: int, lo: int = 1, hi: int = 100000) -> int:
    """解析有界正整数查询参数（从 plugin_manager/routes.py 抽取，统一放公共层）。

    缺省/空 → default；非整数 → 抛 ValueError；超出 [lo, hi] → 截断到边界。
    """
    raw = request.args.get(name, '')
    if raw == '' or raw is None:
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        raise ValueError(_('Parameter {name} must be an integer', name=name))
    return max(lo, min(hi, v))
