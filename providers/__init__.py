"""Provider Registry — factory for market-based provider resolution.

Usage:
    from providers import get_provider, get_provider_list, register_provider
    sms = get_provider('sms')        # AliyunSMSProvider or TwilioSMSProvider
    oauth_cls = get_provider('oauth')  # GoogleOAuthProvider etc.

架构原则：
  - 只加载当前 MARKET 的 Provider
  - 延迟导入（不会因未安装的依赖而报错）
  - 不存在则通过 register_provider() 运行时注册
"""
import os
import importlib

MARKET = os.environ.get('DEPLOY_MARKET', 'cn')


def _lazy_load(module_path: str, class_name: str = None):
    """延迟加载一个模块中的类，失败返回 None"""
    try:
        module = importlib.import_module(module_path)
        if class_name:
            return getattr(module, class_name, None)
        return module
    except (ImportError, AttributeError, ModuleNotFoundError):
        return None


# ═══════════════════════════════════════════════════════════════════
# Provider Registry — maps market → category → [provider classes]
# 格式: (module_path, class_name) or None for skip
# ═══════════════════════════════════════════════════════════════════

_PROVIDER_REGISTRY = {
    'cn': {
        'sms':       [('providers.sms.aliyun', 'AliyunSMSProvider')],
        'payment':   [],  # 国内支付渠道未在 providers 包内实现
        'verify':    [],  # 见 _PROVIDER_BACKLOG
        'social':    [],  # 见 _PROVIDER_BACKLOG
        'logistics': [],  # 见 _PROVIDER_BACKLOG
        'address':   [('cn', None)],  # 使用行政区划 code 体系
    },
    'intl': {
        'sms':       [('providers.sms.twilio', 'TwilioSMSProvider')],
        'payment':   [('providers.payment.stripe', 'StripePaymentGateway'),
                      ('providers.payment.paypal', 'PayPalPaymentGateway')],
        'verify':    [],  # 国际区跳过实名认证
        'social':    [('providers.social.twitter', 'TwitterPushProvider'),
                      ('providers.social.linkedin', 'LinkedInPushProvider')],
        'logistics': [('providers.logistics.shippo', 'ShippoProvider')],
        'address':   [('intl', None)],  # 使用自由文本地址体系
    },
}

# ─── 待实现登记（backlog）─────────────────────────────────────────────
# 下列条目曾直接写在 _PROVIDER_REGISTRY 中，但 providers 包内并不存在对应模块，
# _lazy_load() 恒返回 None —— 即 cn 市场下 get_provider('verify'/'social'/
# 'logistics') 永远拿不到类，属于"声明了但没实现"的悬空条目。
#
# 主注册表只保留真实可导入的条目（scripts/selfcheck.py 的 registry 项据此判定），
# 悬空条目降格登记到这里，注明能力当前的真实承载方；待实现落地后再移回主注册表。
_PROVIDER_BACKLOG = {
    'cn': {
        'verify':    [('providers.verify.alipay', 'AlipayVerificationProvider',
                       '实名认证实际由 auth-center/services/verification_service.py 承载')],
        'social':    [('providers.social.wechat', 'WeChatPushProvider',
                       '社媒推送实际由插件 plugins/social_push 承载'),
                      ('providers.social.weibo', 'WeiboPushProvider',
                       '社媒推送实际由插件 plugins/social_push 承载'),
                      ('providers.social.toutiao', 'ToutiaoPushProvider',
                       '社媒推送实际由插件 plugins/social_push 承载')],
        'logistics': [('providers.logistics.kdniao', 'KdNiaoProvider',
                       '快递鸟物流查询，未实现')],
    },
}

# ─── Runtime registration (用于 oauth_service.py 等动态注册) ───
_RUNTIME_CLASSES = {}  # market → category → [class]


def register_provider(market: str, category: str, provider_cls):
    """Register a provider class at runtime.
    Used by oauth_service.py to register intl OAuth providers lazily.
    """
    _RUNTIME_CLASSES.setdefault(market, {}).setdefault(category, []).append(provider_cls)


def get_provider(category: str, position: int = 0):
    """
    获取指定类别的 Provider 类（需调用方实例化）。
    Args:
        category: 'sms', 'oauth', 'payment', 'verify', 'social', 'logistics', 'address'
        position: 列表中第几个 Provider（0 = 默认）
    Returns:
        Provider 类或 None（需要调用方自行处理）
    """
    market_cfg = _PROVIDER_REGISTRY.get(MARKET, {})
    providers = market_cfg.get(category)
    if not providers or position >= len(providers):
        return None
    entry = providers[position]
    if isinstance(entry, tuple) and len(entry) == 2:
        return _lazy_load(entry[0], entry[1])
    return entry


def get_provider_list(category: str, market: str = '') -> list:
    """
    返回指定类别所有 Provider 类（延迟加载）。
    Args:
        category: 'sms', 'oauth', 'payment', 'verify', 'social', 'logistics', 'address'
        market: 'cn' or 'intl' (default: from env)
    Returns:
        [(provider_class_or_None, module_path), ...] for iteration
    """
    mkt = market or MARKET
    market_cfg = _PROVIDER_REGISTRY.get(mkt, {})
    providers = market_cfg.get(category, [])
    result = []
    for entry in providers:
        if isinstance(entry, tuple) and len(entry) == 2:
            loaded = _lazy_load(entry[0], entry[1])
            result.append((loaded, entry[0]))
        else:
            result.append((entry, ''))
    # Append runtime-registered classes
    runtime = _RUNTIME_CLASSES.get(mkt, {}).get(category, [])
    for cls in runtime:
        result.append((cls, cls.__module__ if hasattr(cls, '__module__') else ''))
    return result


def get_market() -> str:
    """返回当前市场标识 'cn' or 'intl'"""
    return MARKET
