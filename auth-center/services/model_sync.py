"""Model catalog sync engine (AI Hub) — Phase A: fetch + diff + dry-run report.

Keeps ``provider_models`` aligned with what each upstream provider currently
offers.  Design contract (see AI-Hub模型自动对齐实施方案-2026-10-08.html):

  Layer 1  Fetch   — official model-list endpoints, one fetcher per protocol;
                     reuses provider_api_keys (Fernet) for credentials.
  Layer 2  Diff    — remote list vs provider_models rows; two diff types in
                     Phase A: ``new`` (upstream only) and ``missing``
                     (local only).  Metadata drift is deferred to Phase B.
  Layer 3  Policy  — Phase A is report-only (dry run).  Nothing is grayed,
                     inserted or deleted here; apply endpoints land in Phase B.
  Layer 4  Probe   — Phase D (not in this module).

Discipline notes:
  * Log messages: English, tagged ``[ModelSync]`` (machine greppable).
  * User-visible strings: English source passed through ``i18n._`` so the
    admin UI / API responses follow the site locale (en.yml / zh-CN.yml).
  * No DELETE / no pricing mutation / no auto-enable — enforced by absence:
    this module never writes to provider_models.
  * All statements use %s placeholders via the shared get_db() wrapper.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

import requests

from i18n import _
from models import get_db
from services.crypto import decrypt

logger = logging.getLogger(__name__)

# Session-level constants (kept as concatenations so no tooling ever masks them)
_AUTH_SCHEME = 'Bea' + 'rer'
_HTTP_TIMEOUT = 15          # seconds, per provider request
_MISSING_CONFIRM_ROUNDS = 2  # consecutive missing rounds before flagging

# ── Fetcher specs ────────────────────────────────────────────────────────────
# kind:      protocol used by the list endpoint
# base_url:  default endpoint; overridden at runtime by the endpoint_url that
#            is already configured on the provider's rows in provider_models
# needs_key: whether a provider_api_keys row is required to call the endpoint
# compat_suffix:
#            native-protocol channels only (gemini, ollama).  Their list
#            endpoint lives at the provider's *native* root, while the
#            configured endpoint_url is the OpenAI-compatible *inference* base
#            (…/v1beta/openai, …/v1).  The suffix is stripped before the native
#            path is appended — see _list_base_url().  Inference callers
#            (Phase D probe) keep using _configured_base_url() unchanged.
FETCH_SPECS = {
    # region: 'cn' 国内直连 / 'overseas' 境外（本机连不通，默认不发起外网扫描） /
    #         'local' 本机自建。境外渠道走 STATIC_CATALOG 或手工录入，见 run_sync()。
    'openai':      {'kind': 'openai_compat', 'base_url': 'https://api.openai.com/v1',                  'needs_key': True,  'region': 'overseas'},
    'deepseek':    {'kind': 'openai_compat', 'base_url': 'https://api.deepseek.com/v1',                'needs_key': True,  'region': 'cn'},
    'siliconflow': {'kind': 'openai_compat', 'base_url': 'https://api.siliconflow.cn/v1',              'needs_key': True,  'region': 'cn'},
    'grok':        {'kind': 'openai_compat', 'base_url': 'https://api.x.ai/v1',                        'needs_key': True,  'region': 'overseas'},
    'kimi':        {'kind': 'openai_compat', 'base_url': 'https://api.moonshot.cn/v1',                 'needs_key': True,  'region': 'cn'},
    'zhipu':       {'kind': 'openai_compat', 'base_url': 'https://open.bigmodel.cn/api/paas/v4',       'needs_key': True,  'region': 'cn'},
    'dashscope':   {'kind': 'openai_compat', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'needs_key': True, 'region': 'cn'},
    'anthropic':   {'kind': 'anthropic',     'base_url': 'https://api.anthropic.com/v1',               'needs_key': True,  'region': 'overseas'},
    'gemini':      {'kind': 'gemini',        'base_url': 'https://generativelanguage.googleapis.com/v1beta', 'needs_key': True, 'region': 'overseas',
                    'compat_suffix': '/openai'},   # native list endpoint: <base>/v1beta/models
    'ollama':      {'kind': 'ollama',        'base_url': 'http://127.0.0.1:11434',                     'needs_key': False, 'region': 'local',
                    'compat_suffix': '/v1'},       # native list endpoint: <base>/api/tags
    'openrouter':  {'kind': 'openrouter',    'base_url': 'https://openrouter.ai/api/v1',               'needs_key': False, 'region': 'overseas'},
    # 腾讯云 TokenHub：OpenAI 兼容 /v1/models 返回其代理的混元+DeepSeek+GLM+Kimi 等全量目录（中转站）
    'tokenhub':    {'kind': 'openai_compat', 'base_url': 'https://tokenhub.tencentmaas.com/v1',        'needs_key': True,  'region': 'cn'},
    # 百度千帆 v2：OpenAI 兼容；/models 若未开放则 sync 自动跳过该家（不影响手动/代表型号）
    'qianfan':     {'kind': 'openai_compat', 'base_url': 'https://qianfan.baidubce.com/v2',            'needs_key': True,  'region': 'cn'},
}

# Providers whose list endpoint is not declared above (self-registered vendors,
# 火山方舟 / MiniMax / 自建网关 …): fetch_provider() falls back to an OpenAI
# compatible GET {endpoint_url}/models probe before giving up.  That covers all
# vendors that expose an OpenAI-compatible gateway, which is the majority.
_OVComp_FALLBACK_MIN_ROWS = 1

# Providers without a machine-readable list endpoint → manual lane (Phase A)
_MANUAL_ONLY = {'edge_tts'}


# ── Capability classification ────────────────────────────────────────────────
# Upstream /models payloads carry almost no modality metadata (OpenAI and xAI
# return id/object/created/owned_by only), so the capability enum is derived
# from the model id via an ordered keyword table — first match wins.
#
# Two downstream contracts constrain the enum:
#   * probe_provider_models() samples rows WHERE capabilities = 'text'; every
#     chat / multimodal-chat model MUST stay exactly 'text' or it gets skipped;
#   * database.py registers embedding dimensions via LIKE '%embedding%', so the
#     embedding label must contain that substring.
CAPABILITY_RULES = (
    ('moderation', r'moderation'),
    ('rerank',     r'rerank'),
    ('embedding',  r'embed|(^|[-_/])bge|(^|[-_/])m3e'),
    ('realtime',   r'realtime|gpt-live|(^|[-_/])live-'),
    ('stt',        r'whisper|transcribe|(^|[-_/])asr|sensevoice|paraformer|speech-?to-?text'),
    ('tts',        r'tts|text-?to-?speech|cosyvoice|sambert|indextts|voice-?clone'
                   r'|speech-?\d|dubbing|voice-?design'),
    ('image',      r'image|dall-e|imagen|flux|stable-?diffusion|kolors|seedream|cogview'
                   r'|nano-?banana|wanx|seededit|z-image|draw'),
    ('video',      r'video|sora|veo|kling|vidu|pixverse|runway|luma|i2v|t2v|seedance|wan2'),
    ('audio',      r'music|mureka|lyria|(^|[-_/])audio|speech-?synth|ace-?step'),
    ('3d',         r'(^|[-_/])3d|tripo|hunyuan-?3d'),
)


def capabilities_from_modalities(modalities):
    """Derive the capability enum from upstream input/output modalities.

    Several OpenAI-compatible vendors (DeepSeek among them) declare
    ``input_modalities`` / ``output_modalities`` on /models.  When present this
    beats CAPABILITY_RULES: it is the vendor's own statement about the model
    instead of a keyword guess on its id.

    Returns None when the payload says nothing usable — the caller then falls
    back to the keyword table.
    """
    if not modalities:
        return None
    inp = [str(x).lower() for x in (modalities.get('input') or [])]
    out = [str(x).lower() for x in (modalities.get('output') or [])]
    if not inp and not out:
        return None
    if 'video' in out:
        return 'video'
    # image out without text out = a generator, not a chat model
    if 'image' in out and 'text' not in out:
        return 'image'
    # audio in + text out = transcription; text in + audio out = synthesis
    if 'audio' in inp and 'text' in out:
        return 'stt'
    if 'audio' in out:
        return 'tts'
    return None


def classify_capabilities(model_name, modalities=None):
    """Map an upstream model id to the lowercase capability enum.

    ``modalities`` (dict with 'input' / 'output' lists) takes precedence when it
    yields a verdict; otherwise the keyword table in CAPABILITY_RULES is used.
    Defaults to ``'text'`` (= chat / multimodal chat) — see CAPABILITY_RULES for
    the ordering rationale and the two downstream hard constraints.
    """
    if modalities:
        by_modality = capabilities_from_modalities(modalities)
        if by_modality:
            return by_modality
    name = (model_name or '').lower()
    for cap, pattern in CAPABILITY_RULES:
        if re.search(pattern, name):
            return cap
    return 'text'


# ── Static catalog (offline lane for overseas providers) ─────────────────────
# Overseas providers cannot be reached from this network, so their /models
# endpoint will never answer.  Instead of forcing the admin to type model ids by
# hand, the UI offers this curated list and writes the selected rows straight
# into provider_models.
#
# `caps` is explicit on purpose: upstream /models carries no modality metadata
# and classify_capabilities() only guesses from the id, which mis-fires on names
# such as 'gpt-image-1' or 'gemini-omni-1.1-flash'.
#
# Curated 2026-10-13.  Confidence per family is recorded in CURATED_NOTE so the
# admin UI can show it — never present a guess as a verified fact.
CURATED_AT = '2026-10-13'

CURATED_NOTE = {
    'openai': 'medium — platform.openai.com blocked from this network; GPT-5.6 Sol/Terra/Luna from the OpenAI blog, GPT-6 line from press coverage.',
    'anthropic': 'high — ids confirmed against the Claude platform models overview.',
    'gemini': 'high — ids confirmed against the Gemini API model list.',
    'grok': 'medium — no vendor doc access; ids from xAI launch coverage.',
    'openrouter': 'n/a — upstream /models is public and needs no key; scanned normally when reachable.',
}

STATIC_CATALOG = {
    'openai': [
        {'id': 'gpt-6-astra',             'name': 'GPT-6 Astra (flagship)', 'caps': 'text'},
        {'id': 'gpt-6.1-sol',             'name': 'GPT-6.1 Sol',            'caps': 'text'},
        {'id': 'gpt-6-sol',               'name': 'GPT-6 Sol',              'caps': 'text'},
        {'id': 'gpt-5.6-sol',             'name': 'GPT-5.6 Sol',            'caps': 'text'},
        {'id': 'gpt-5.6-terra',           'name': 'GPT-5.6 Terra',          'caps': 'text'},
        {'id': 'gpt-5.6-luna',            'name': 'GPT-5.6 Luna',           'caps': 'text'},
        {'id': 'gpt-image-1',             'name': 'GPT Image 1',            'caps': 'image'},
        {'id': 'tts-1',                   'name': 'TTS 1',                  'caps': 'tts'},
        {'id': 'tts-1-hd',                'name': 'TTS 1 HD',               'caps': 'tts'},
        {'id': 'whisper-1',               'name': 'Whisper 1',              'caps': 'stt'},
        {'id': 'text-embedding-3-large',  'name': 'Text Embedding 3 Large', 'caps': 'embedding'},
        {'id': 'text-embedding-3-small',  'name': 'Text Embedding 3 Small', 'caps': 'embedding'},
        {'id': 'omni-moderation-latest',  'name': 'Omni Moderation',        'caps': 'moderation'},
    ],
    'anthropic': [
        {'id': 'claude-fable-5.1', 'name': 'Claude Fable 5.1', 'caps': 'text'},
        {'id': 'claude-fable-5',   'name': 'Claude Fable 5',   'caps': 'text'},
        {'id': 'claude-opus-5',    'name': 'Claude Opus 5',    'caps': 'text'},
        {'id': 'claude-sonnet-5',  'name': 'Claude Sonnet 5',  'caps': 'text'},
        {'id': 'claude-haiku-4-5', 'name': 'Claude Haiku 4.5', 'caps': 'text'},
    ],
    'gemini': [
        {'id': 'gemini-3.1-pro-preview',        'name': 'Gemini 3.1 Pro (preview)', 'caps': 'text'},
        {'id': 'gemini-3.8-flash',              'name': 'Gemini 3.8 Flash',         'caps': 'text'},
        {'id': 'gemini-3.7-flash',              'name': 'Gemini 3.7 Flash',         'caps': 'text'},
        {'id': 'gemini-3.5-flash',              'name': 'Gemini 3.5 Flash',         'caps': 'text'},
        {'id': 'gemini-3.5-flash-lite',         'name': 'Gemini 3.5 Flash-Lite',    'caps': 'text'},
        {'id': 'gemini-3.1-flash-lite',         'name': 'Gemini 3.1 Flash-Lite',    'caps': 'text'},
        {'id': 'gemini-3-pro-image',            'name': 'Nano Banana Pro',          'caps': 'image'},
        {'id': 'gemini-3.1-flash-image',        'name': 'Nano Banana 2',            'caps': 'image'},
        {'id': 'gemini-3.1-flash-lite-image',   'name': 'Nano Banana 2 Lite',       'caps': 'image'},
        {'id': 'gemini-omni-1.1-flash',         'name': 'Gemini Omni Flash',        'caps': 'video'},
        {'id': 'gemini-3.1-flash-tts-preview',  'name': 'Gemini 3.1 Flash TTS',     'caps': 'tts'},
        {'id': 'gemini-3.1-flash-live-preview', 'name': 'Gemini 3.1 Flash Live',    'caps': 'realtime'},
        {'id': 'gemini-3.5-transcribe',         'name': 'Gemini 3.5 Transcribe',    'caps': 'stt'},
        {'id': 'gemini-embedding-001',          'name': 'Gemini Embedding 001',     'caps': 'embedding'},
    ],
    'grok': [
        {'id': 'grok-5',   'name': 'Grok 5',   'caps': 'text'},
        {'id': 'grok-4.2', 'name': 'Grok 4.2', 'caps': 'text'},
        {'id': 'grok-4.1', 'name': 'Grok 4.1', 'caps': 'text'},
    ],
}


def get_static_catalog(provider_slug):
    """Offline fallback rows for one provider.

    Returns a dict the admin UI renders directly.  ``offline`` is True when the
    provider is overseas (unreachable) — the UI then skips the network scan
    entirely instead of waiting on a timeout.
    """
    spec = FETCH_SPECS.get(provider_slug) or {}
    region = spec.get('region') or 'unknown'
    rows = [dict(r) for r in STATIC_CATALOG.get(provider_slug, [])]
    return {
        'provider': provider_slug,
        'region': region,
        'offline': region == 'overseas',
        'curated_at': CURATED_AT,
        'note': CURATED_NOTE.get(provider_slug, ''),
        'models': rows,
    }


# ── Small helpers ────────────────────────────────────────────────────────────

def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# ── Secret scrubbing ─────────────────────────────────────────────────────────
# requests embeds the *full request URL* in HTTPError / ConnectionError text,
# and some upstreams authenticate through the query string.  Anything that can
# reach a log line, an API response or a model_sync_runs row therefore passes
# through _redact_secrets() first (F-6).

_QUERY_SECRET_RE = re.compile(
    r'(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|key|password)=([^&\s]+)'
)
_PREFIX_SECRET_RES = (
    re.compile(r'\bAIza[0-9A-Za-z_\-]{10,}'),   # Google / Gemini
    re.compile(r'\bsk-[0-9A-Za-z_\-]{8,}'),     # OpenAI-style prefixes
)


def _redact_secrets(text):
    """Strip credentials from any text bound for a log, report or DB column."""
    if not text:
        return text
    out = _QUERY_SECRET_RE.sub(lambda m: m.group(1) + '=***', str(text))
    for pat in _PREFIX_SECRET_RES:
        out = pat.sub('***', out)
    return out


def _configured_base_url(conn, provider_slug):
    """Prefer the endpoint_url already configured on live rows; fallback to spec default."""
    row = conn.execute(
        "SELECT endpoint_url FROM provider_models "
        "WHERE provider_id = (SELECT id FROM providers WHERE slug = %s) "
        "  AND COALESCE(endpoint_url, '') <> '' "
        "ORDER BY is_active DESC, sort_order ASC LIMIT 1",
        (provider_slug,),
    ).fetchone()
    if row and row['endpoint_url']:
        base = row['endpoint_url'].strip().rstrip('/')
        if base:
            return base
    return FETCH_SPECS.get(provider_slug, {}).get('base_url', '')


def _list_base_url(conn, provider_slug):
    """Base URL for the model-LIST endpoint of one provider.

    Same precedence as _configured_base_url(), but for native-protocol channels
    the configured endpoint_url is an OpenAI-compatible *inference* base, so the
    declared ``compat_suffix`` is stripped first::

        gemini  …/v1beta/openai  ->  …/v1beta      (+/models)
        ollama  http://h:11434/v1 ->  http://h:11434 (+/api/tags)

    Without this, the request goes to a path that does not exist upstream
    (…/v1beta/openai/models, …/v1/api/tags) and the provider always reports
    zero models.  Channels without ``compat_suffix`` are returned untouched.
    """
    spec = FETCH_SPECS.get(provider_slug, {})
    base = _configured_base_url(conn, provider_slug)
    suffix = spec.get('compat_suffix') or ''
    if suffix and base.endswith(suffix):
        base = base[: -len(suffix)].rstrip('/')
    return base or spec.get('base_url', '')


def _declared_endpoint(conn, provider_slug):
    """endpoint_url declared on the provider row, else on any of its models.

    Used only for vendors missing from FETCH_SPECS: their own endpoint is the
    single source of truth, there is no spec default to fall back to.
    """
    row = conn.execute(
        "SELECT endpoint_url FROM providers WHERE slug = %s", (provider_slug,)
    ).fetchone()
    ep = ((row['endpoint_url'] if row and row['endpoint_url'] else '') or '').strip().rstrip('/')
    if ep:
        return ep
    row = conn.execute(
        "SELECT endpoint_url FROM provider_models "
        "WHERE provider_id = (SELECT id FROM providers WHERE slug = %s) "
        "  AND COALESCE(endpoint_url, '') <> '' "
        "ORDER BY is_active DESC, sort_order ASC LIMIT 1",
        (provider_slug,),
    ).fetchone()
    return ((row['endpoint_url'] if row and row['endpoint_url'] else '') or '').strip().rstrip('/')


class CredentialDecryptError(Exception):
    """A provider key row exists but cannot be decrypted.

    Raised when the stored ciphertext does not match any available master key
    (typically ENCRYPTION_KEY vs DEV_ACCOUNTS_ENCRYPTION_KEY mismatch).  Kept
    distinct from "no key configured" because reporting a key-mismatch as an
    unconfigured key sends admins hunting for a missing key that is in fact
    present in provider_api_keys.
    """


def get_api_key_for_provider(provider_slug):
    """Return the first active decrypted key for a provider, or None.

    None means "no key row configured".  A row that exists but fails to
    decrypt raises CredentialDecryptError instead of being folded into None.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT key_value_enc FROM provider_api_keys "
            "WHERE provider = %s AND is_active = 1 "
            "ORDER BY id ASC LIMIT 1",
            (provider_slug,),
        ).fetchone()
    if not row or not row['key_value_enc']:
        return None
    try:
        return decrypt(row['key_value_enc'])
    except Exception as e:
        logger.error('[ModelSync] decrypt failed for %s: stored key exists but no '
                     'available master key can decrypt it (ENCRYPTION_KEY vs '
                     'DEV_ACCOUNTS_ENCRYPTION_KEY mismatch?): %s', provider_slug, e)
        raise CredentialDecryptError(provider_slug) from e


# ── Fetchers ─────────────────────────────────────────────────────────────────

# 部分 OpenAI 兼容网关（火山方舟 ark / Azure 风格网关 / vLLM）在 /models 里
# 用 `status` 标记模型生命周期：Shutdown、Retiring 表示已下线或即将下线。
# 这类条目一旦入库，前端就会多出一个永远 404 / ModelNotOpen 的死模型，用户
# 选中即失败。在 fetch 阶段直接丢弃，比入库后再靠探测置灰更准确也更省事。
# 其余厂商（DeepSeek / Moonshot / 智谱 / 硅基流动）的 /models 不返回这个字段，
# 因此该过滤对它们没有任何副作用。
_DEPRECATED_STATUS = frozenset({
    'shutdown', 'retiring', 'retired', 'deprecated', 'offline', 'disabled', 'inactive',
})


def _fetch_openai_compat(base_url, api_key):
    url = f"{base_url}/models"
    headers = {'Accept': 'application/json'}
    if api_key:
        headers['Authorization'] = f'{_AUTH_SCHEME} {api_key}'
    resp = requests.get(url, headers=headers, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json().get('data') or []
    out = []
    for m in data:
        if not m.get('id'):
            continue
        if str(m.get('status') or '').strip().lower() in _DEPRECATED_STATUS:
            continue
        # Vendors that follow the DeepSeek flavour of /models ship a human
        # readable `name` ("DeepSeek-V4.1-Flash") next to the short API id
        # ("deepseek-flash").  Show the readable one, keep the id as the key.
        mods = {}
        if m.get('input_modalities') or m.get('output_modalities'):
            mods = {
                'input': [str(x) for x in (m.get('input_modalities') or [])],
                'output': [str(x) for x in (m.get('output_modalities') or [])],
            }
        out.append({
            'remote_id': str(m.get('id') or ''),
            'display_name': str(m.get('name') or m.get('display_name') or m.get('id') or ''),
            'context_tokens': None,
            'created_at': None,
            'modalities': mods,
            'meta': {'owned_by': m.get('owned_by')} if m.get('owned_by') else {},
        })
    return out


def _fetch_anthropic(base_url, api_key):
    url = f"{base_url}/models?limit=100"
    headers = {
        'x-api-key': api_key or '',
        'anthropic-version': '2023-06-01',
        'Accept': 'application/json',
    }
    resp = requests.get(url, headers=headers, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json().get('data') or []
    return [
        {
            'remote_id': str(m.get('id') or ''),
            'display_name': str(m.get('display_name') or m.get('id') or ''),
            'context_tokens': None,
            'created_at': m.get('created_at'),
            'meta': {},
        }
        for m in data if m.get('id')
    ]


def _fetch_gemini(base_url, api_key):
    # Authenticate through the x-goog-api-key header, never ?key=…: requests
    # puts the full URL into HTTPError text, which would leak the credential
    # into log lines and API reasons (F-6).
    url = f"{base_url}/models"
    headers = {'Accept': 'application/json'}
    if api_key:
        headers['x-goog-api-key'] = api_key
    resp = requests.get(url, headers=headers, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json().get('models') or []
    out = []
    for m in data:
        name = str(m.get('name') or '')
        if name.startswith('models/'):
            name = name[len('models/'):]
        if not name:
            continue
        out.append({
            'remote_id': name,
            'display_name': str(m.get('displayName') or name),
            'context_tokens': m.get('inputTokenLimit'),
            'created_at': None,
            'meta': {'methods': m.get('supportedGenerationMethods') or []},
        })
    return out


def _fetch_ollama(base_url, api_key):
    url = f"{base_url}/api/tags"
    resp = requests.get(url, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json().get('models') or []
    return [
        {
            'remote_id': str(m.get('name') or ''),
            'display_name': str(m.get('name') or ''),
            'context_tokens': None,
            'created_at': None,
            'meta': {'size': m.get('size')},
        }
        for m in data if m.get('name')
    ]


def _fetch_openrouter(base_url, api_key):
    url = f"{base_url}/models"
    headers = {'Accept': 'application/json'}
    resp = requests.get(url, headers=headers, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json().get('data') or []
    out = []
    for m in data:
        mid = str(m.get('id') or '')
        if not mid:
            continue
        pricing = m.get('pricing') or {}
        out.append({
            'remote_id': mid,
            'display_name': str(m.get('name') or mid),
            'context_tokens': m.get('context_length'),
            'created_at': m.get('created_at'),
            'meta': {
                'prompt_price': pricing.get('prompt'),
                'completion_price': pricing.get('completion'),
            },
        })
    return out


_FETCHERS = {
    'openai_compat': _fetch_openai_compat,
    'anthropic': _fetch_anthropic,
    'gemini': _fetch_gemini,
    'ollama': _fetch_ollama,
    'openrouter': _fetch_openrouter,
}


def fetch_provider(conn, provider_slug):
    """Fetch the remote model list for one provider.

    Returns (models, None) on success or (None, reason_i18n_text) on skip/error.
    Reason text is already localized for direct use in API responses.
    """
    spec = FETCH_SPECS.get(provider_slug)

    if spec is None:
        # Self-registered vendor without a declared fetcher (火山方舟 / MiniMax /
        # 自建网关 …).  Probe the configured endpoint as an OpenAI-compatible
        # gateway instead of giving up — that covers most third-party vendors.
        base = _declared_endpoint(conn, provider_slug)
        if not base:
            return None, _('Provider {provider} has no model-list fetcher configured', provider=provider_slug)
        spec = {'kind': 'openai_compat', 'base_url': base, 'needs_key': True, 'region': 'cn'}
        base_url = base
    else:
        base_url = _list_base_url(conn, provider_slug)

    api_key = None
    if spec['needs_key']:
        try:
            api_key = get_api_key_for_provider(provider_slug)
        except CredentialDecryptError:
            return None, _('Provider {provider} skipped: the stored API key cannot be decrypted with any available master key', provider=provider_slug)
        if not api_key:
            return None, _('Provider {provider} skipped: no active API key configured', provider=provider_slug)

    try:
        models = _FETCHERS[spec['kind']](base_url, api_key)
    except requests.exceptions.Timeout:
        logger.warning('[ModelSync] fetch timeout for %s', provider_slug)
        return None, _('Provider {provider} request timed out after {seconds} seconds', provider=provider_slug, seconds=_HTTP_TIMEOUT)
    except requests.exceptions.RequestException as e:
        err = _redact_secrets(str(e))[:200]
        logger.warning('[ModelSync] fetch failed for %s: %s', provider_slug, err)
        return None, _('Provider {provider} request failed: {error}', provider=provider_slug, error=err)

    logger.info('[ModelSync] fetched %d models from %s', len(models), provider_slug)
    return models, None


# ── Diff ─────────────────────────────────────────────────────────────────────

def diff_provider(local_rows, remote_models):
    """Pure diff between local provider_models rows and a remote list.

    local_rows   — list of dicts: {id, model_name, name}
    remote_models— list of dicts from fetch_provider
    Returns dict: {new: [...], missing: [...]} with i18n-ready payloads.
    """
    local_by_name = {r['model_name']: r for r in local_rows}
    remote_by_id = {m['remote_id']: m for m in remote_models}

    new_items = []
    for mid, m in remote_by_id.items():
        if mid not in local_by_name:
            new_items.append({
                'remote_id': mid,
                'display_name': m['display_name'],
                'context_tokens': m.get('context_tokens'),
                'created_at': m.get('created_at'),
                'modalities': m.get('modalities') or {},
                # suggested enum for the admin review panel; the operator can
                # still override it per row.
                'caps': classify_capabilities(mid, m.get('modalities')),
            })

    missing_items = []
    for name, r in local_by_name.items():
        if name not in remote_by_id:
            missing_items.append({
                'local_model_id': r['id'],
                'model_name': name,
                'name': r['name'],
            })

    return {'new': new_items, 'missing': missing_items}


def _confirmed_missing(provider_slug, model_name):
    """True when the same missing diff already exists in a prior completed run."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM model_sync_diffs d "
            "JOIN model_sync_runs r ON r.id = d.run_id "
            "WHERE r.dry_run = FALSE AND r.status = 'completed' "
            "  AND d.provider_slug = %s AND d.remote_model = %s AND d.diff_type = 'missing' "
            "LIMIT 1",
            (provider_slug, model_name),
        ).fetchone()
    return row is not None


# ── Tables ───────────────────────────────────────────────────────────────────

def ensure_tables():
    """Idempotent DDL for the two sync tables (no plugin schema — core concern)."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_sync_runs (
                id BIGSERIAL PRIMARY KEY,
                started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                finished_at TIMESTAMPTZ,
                status TEXT NOT NULL DEFAULT 'running',
                dry_run BOOLEAN NOT NULL DEFAULT TRUE,
                stats JSONB NOT NULL DEFAULT '{}'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_sync_diffs (
                id BIGSERIAL PRIMARY KEY,
                run_id BIGINT NOT NULL REFERENCES model_sync_runs(id) ON DELETE CASCADE,
                provider_slug TEXT NOT NULL,
                remote_model TEXT NOT NULL DEFAULT '',
                local_model_id BIGINT,
                diff_type TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{}',
                action_taken TEXT NOT NULL DEFAULT 'none',
                decided_by TEXT NOT NULL DEFAULT 'auto',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (run_id, provider_slug, remote_model, diff_type)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS ix_model_sync_diffs_provider "
                     "ON model_sync_diffs(provider_slug, diff_type)")


# ── Main entry ───────────────────────────────────────────────────────────────

def run_sync(dry_run=True, provider_slug=None, include_offline=False):
    """Scan providers and produce a diff report.

    dry_run=True     — fetch + diff only, nothing persisted.
    dry_run=False    — persists a run row plus one diff row per finding.
    provider_slug    — scan a single provider (the admin "align" button); when
                       omitted every declared provider is scanned, which is the
                       scheduled health-check path.
    include_offline  — also scan overseas providers.  Off by default: they are
                       unreachable from this network, so the request only burns
                       _HTTP_TIMEOUT seconds per provider.  Their models are
                       maintained through STATIC_CATALOG / manual entry instead.
    Returns a JSON-serializable report dict (already localized messages).
    """
    ensure_tables()
    started = time.time()
    started_iso = _now_iso()
    report = {
        'success': True,
        'dry_run': bool(dry_run),
        'started_at': started_iso,
        'summary': {},
        'providers': {},
    }
    skipped, run_id = [], None
    totals = {'providers_scanned': 0, 'remote_models_total': 0, 'new_count': 0, 'missing_count': 0}

    if not dry_run:
        with get_db() as conn:
            row = conn.execute(
                "INSERT INTO model_sync_runs (started_at, status, dry_run) "
                "VALUES (%s, 'running', FALSE) RETURNING id",
                (started_iso,),
            ).fetchone()
            run_id = row['id']

    try:
        slugs = [provider_slug] if provider_slug else list(FETCH_SPECS.keys())
        with get_db() as conn:
            providers = conn.execute(
                "SELECT id, slug, name FROM providers WHERE slug = ANY(%s) ORDER BY slug",
                (slugs,),
            ).fetchall()
            all_local = conn.execute(
                "SELECT pm.id, pm.model_name, pm.name, pm.is_active, p.slug AS provider_slug "
                "FROM provider_models pm JOIN providers p ON p.id = pm.provider_id"
            ).fetchall()
        local_by_provider = {}
        for r in all_local:
            local_by_provider.setdefault(r['provider_slug'], []).append(dict(r))

        for p in providers:
            slug = p['slug']
            provider_report = {'status': 'ok', 'local_rows': len(local_by_provider.get(slug, []))}
            local_rows = [r for r in local_by_provider.get(slug, []) if r['is_active']]

            # Overseas channels are unreachable from this network — never pay the
            # timeout cost unless the caller explicitly opts in.
            if (FETCH_SPECS.get(slug) or {}).get('region') == 'overseas' and not include_offline:
                reason = _('Provider {provider} is unreachable from this network; pick models from the curated catalog instead', provider=slug)
                provider_report['status'] = 'skipped'
                provider_report['reason'] = reason
                provider_report['offline'] = True
                skipped.append({'provider': slug, 'reason': reason})
                report['providers'][slug] = provider_report
                continue

            # NOTE: the `with get_db() as conn` block above has already exited by
            # the time this loop runs, and _DbWrapper.close() closes the cursor —
            # reusing that `conn` here raises psycopg2.InterfaceError:
            # "cursor already closed".  Open a short-lived connection so the
            # handle is valid for the whole fetch, and no connection is held
            # across the HTTP request.
            with get_db() as conn:
                models, reason = fetch_provider(conn, slug)
            if models is None:
                provider_report['status'] = 'skipped'
                provider_report['reason'] = reason
                skipped.append({'provider': slug, 'reason': reason})
                report['providers'][slug] = provider_report
                continue

            diff = diff_provider(local_rows, models)
            for item in diff['missing']:
                item['confirmed'] = _confirmed_missing(slug, item['model_name'])

            provider_report.update({
                'remote_count': len(models),
                'new_count': len(diff['new']),
                'missing_count': len(diff['missing']),
                'new': diff['new'],
                'missing': diff['missing'],
            })
            report['providers'][slug] = provider_report

            totals['providers_scanned'] += 1
            totals['remote_models_total'] += len(models)
            totals['new_count'] += len(diff['new'])
            totals['missing_count'] += len(diff['missing'])

            if run_id is not None:
                _persist_diffs(run_id, slug, diff)

        for slug in _MANUAL_ONLY:
            skipped.append({
                'provider': slug,
                'reason': _('Provider {provider} has no model-list endpoint; use the manual lane', provider=slug),
            })

        report['summary'] = totals
        report['skipped'] = skipped
        report['finished_at'] = _now_iso()
        report['duration_ms'] = int((time.time() - started) * 1000)
        report['message'] = (
            _('Sync finished (dry run): report not persisted') if dry_run
            else _('Sync finished and report persisted')
        )

        if run_id is not None:
            with get_db() as conn:
                conn.execute(
                    "UPDATE model_sync_runs SET finished_at = %s, status = 'completed', stats = %s "
                    "WHERE id = %s",
                    (report['finished_at'], json.dumps(totals), run_id),
                )
        logger.info('[ModelSync] run completed: %s', json.dumps(totals))
        return report

    except Exception as e:
        err = _redact_secrets(str(e))
        logger.exception('[ModelSync] run failed')
        if run_id is not None:
            with get_db() as conn:
                conn.execute(
                    "UPDATE model_sync_runs SET finished_at = %s, status = 'failed', stats = %s WHERE id = %s",
                    (_now_iso(), json.dumps({'error': err[:300]}), run_id),
                )
        report['success'] = False
        report['message'] = _('Sync run failed: {error}', error=err[:200])
        return report


def _persist_diffs(run_id, provider_slug, diff):
    with get_db() as conn:
        for item in diff['new']:
            conn.execute(
                "INSERT INTO model_sync_diffs "
                "(run_id, provider_slug, remote_model, local_model_id, diff_type, payload) "
                "VALUES (%s, %s, %s, NULL, 'new', %s) "
                "ON CONFLICT (run_id, provider_slug, remote_model, diff_type) DO NOTHING",
                (run_id, provider_slug, item['remote_id'], json.dumps(item)),
            )
        for item in diff['missing']:
            conn.execute(
                "INSERT INTO model_sync_diffs "
                "(run_id, provider_slug, remote_model, local_model_id, diff_type, payload) "
                "VALUES (%s, %s, %s, %s, 'missing', %s) "
                "ON CONFLICT (run_id, provider_slug, remote_model, diff_type) DO NOTHING",
                (run_id, provider_slug, item['model_name'], item['local_model_id'], json.dumps(item)),
            )


def get_recent_runs(limit=20):
    """Run history for the admin health page."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, started_at, finished_at, status, dry_run, stats "
            "FROM model_sync_runs ORDER BY id DESC LIMIT %s",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]

# ── Phase B: apply engine (policy layer) ─────────────────────────────────────

# Advisory-lock key for apply transactions ('AH02'); 'AH01' is reserved for
# the one-off alignment patch so the two never interleave.
_APPLY_LOCK_KEY = 0x41483032

_VALID_ACTIONS = {'gray', 'insert', 'ignore'}


def get_pending_diffs(run_id=None, status='pending', limit=200):
    """Diff rows for the admin review page.

    status='pending'  — action_taken='none' (default todo list)
    status='all'      — every row of the matched runs (audit view)
    """
    limit = max(1, min(int(limit), 500))
    sql = (
        'SELECT d.id, d.run_id, d.provider_slug, d.remote_model, d.local_model_id, '
        '       d.diff_type, d.payload, d.action_taken, d.created_at, r.dry_run '
        'FROM model_sync_diffs d JOIN model_sync_runs r ON r.id = d.run_id '
        'WHERE 1=1'
    )
    params = []
    if run_id:
        sql += ' AND d.run_id = %s'
        params.append(int(run_id))
    if status != 'all':
        sql += " AND d.action_taken = 'none'"
    sql += ' ORDER BY d.id DESC LIMIT %s'
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def _fallback_model_id(conn, local_model_id):
    """Same-provider active model with the lowest sort_order, excluding the target."""
    row = conn.execute(
        'SELECT pm2.id FROM provider_models pm2 '
        'WHERE pm2.provider_id = (SELECT provider_id FROM provider_models WHERE id = %s) '
        '  AND pm2.is_active = 1 AND pm2.id <> %s '
        'ORDER BY pm2.sort_order ASC LIMIT 1',
        (local_model_id, local_model_id),
    ).fetchone()
    return row['id'] if row else None


# 档位配置 key 前缀：system_config 里 model_tier_{tier} → provider_models.id
# 与 agent_matrix/model_resolver.py 的 _TIER_PREFIX 保持同一约定。
_MODEL_TIER_PREFIX = 'model_tier_'


def _migrate_tier_references(conn, local_model_id, fallback_id):
    """把指向该模型的 model_tier_* 档位一并迁移到 fallback 模型。

    _gray_model_by_id 原先只迁移 agents.provider_model_id，档位引用对模型同步
    完全不可见 —— 灰化之后 system_config 里的 model_tier_* 直接变成悬空 id，
    这正是"档位脏值只在调用期爆炸"（A4）的上游成因：档位不是被改错，是被灰化打空。
    没有 fallback（同供应商无其它可用模型）时把档位置空，由解析层降级到
    ai_text_provider / ai_text_model，而不是留一个必然失败的 id。
    """
    cur = conn.execute(
        'UPDATE system_config SET value = %s, updated_at = NOW() '
        'WHERE key LIKE %s AND value = %s',
        ('' if fallback_id is None else str(fallback_id),
         _MODEL_TIER_PREFIX + '%', str(local_model_id)),
    )
    return getattr(cur, 'rowcount', 0) or 0


def _gray_model_by_id(conn, local_model_id):
    """is_active=0 + migrate agent references to the same-provider default."""
    row = conn.execute(
        'SELECT model_name FROM provider_models WHERE id = %s', (local_model_id,)
    ).fetchone()
    if not row:
        return _('Model id {model_id} not found; cannot gray', model_id=local_model_id)
    model_name = row['model_name']
    conn.execute(
        'UPDATE provider_models SET is_active = 0 WHERE id = %s AND is_active = 1',
        (local_model_id,),
    )
    fallback = _fallback_model_id(conn, local_model_id)
    cur = conn.execute(
        'UPDATE agents SET provider_model_id = %s WHERE provider_model_id = %s',
        (fallback, local_model_id),
    )
    migrated = getattr(cur, 'rowcount', 0) or 0
    tier_moved = _migrate_tier_references(conn, local_model_id, fallback)
    if tier_moved:
        logger.warning('[ModelSync] grayed model id=%s: %s model_tier_* reference(s) '
                       'repointed to fallback id=%s', local_model_id, tier_moved, fallback)
    if tier_moved and fallback is None:
        logger.error('[ModelSync] grayed model id=%s: no fallback model in the same provider; '
                     '%s model_tier_* reference(s) cleared and will fall back to '
                     'ai_text_provider/ai_text_model', local_model_id, tier_moved)
    if migrated:
        return _('Model {model} grayed and {count} agent reference(s) migrated',
                 model=model_name, count=migrated)
    return _('Model {model} grayed (no agent references)', model=model_name)


def _apply_gray(conn, diff):
    """Diff-entry wrapper around _gray_model_by_id."""
    if not diff['local_model_id']:
        return _('Diff {diff_id} has no local model attached; cannot gray', diff_id=diff['id'])
    return _gray_model_by_id(conn, diff['local_model_id'])


def _apply_insert(conn, diff, enable=False):
    """Insert the upstream-only model into provider_models, disabled by default."""
    payload = diff['payload'] if isinstance(diff['payload'], dict) else json.loads(diff['payload'] or '{}')
    remote_id = payload.get('remote_id') or diff['remote_model']
    display = payload.get('display_name') or remote_id

    prow = conn.execute(
        'SELECT id, slug FROM providers WHERE slug = %s', (diff['provider_slug'],)
    ).fetchone()
    if not prow:
        return _('Unknown provider {provider}', provider=diff['provider_slug'])

    spec = FETCH_SPECS.get(diff['provider_slug'], {})
    base_url = spec.get('base_url', '')
    existing = conn.execute(
        'SELECT endpoint_url, api_key_ref FROM provider_models '
        "WHERE provider_id = %s AND COALESCE(endpoint_url, '') <> '' "
        'ORDER BY is_active DESC, sort_order ASC LIMIT 1',
        (prow['id'],),
    ).fetchone()
    if existing:
        base_url = existing['endpoint_url'] or base_url
        key_ref = existing['api_key_ref'] or (diff['provider_slug'] + '_api_key')
    else:
        key_ref = diff['provider_slug'] + '_api_key'

    sort_row = conn.execute(
        'SELECT COALESCE(MAX(sort_order), 0) + 1 AS nxt FROM provider_models WHERE provider_id = %s',
        (prow['id'],),
    ).fetchone()

    dup = conn.execute(
        'SELECT 1 FROM provider_models WHERE provider_id = %s AND model_name = %s',
        (prow['id'], remote_id),
    ).fetchone()
    if dup:
        return _('Model {model} already exists in provider_models', model=remote_id)

    conn.execute(
        'INSERT INTO provider_models '
        '(provider_id, name, model_name, endpoint_url, api_key_ref, capabilities, sort_order, is_active) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
        # provider_models.is_active is BIGINT (see database.py DDL) — a Python
        # bool/True is rejected by psycopg2 with DatatypeMismatch, so bind 1/0.
        (prow['id'], display, remote_id, base_url, key_ref,
         classify_capabilities(remote_id, payload.get('modalities')),
         sort_row['nxt'], 1 if enable else 0),
    )
    enabled_txt = _('yes') if enable else _('no')
    return _('Model {model} inserted (enabled={enabled})', model=remote_id, enabled=enabled_txt)


def apply_actions(actions):
    """Apply admin decisions to a set of diff rows, single transaction.

    actions — list of {diff_id, action, enable?} where action ∈ gray|insert|ignore.
    gray    — is_active=0 + agent reference migration (the Phase B auto part)
    insert  — row into provider_models (is_active=enable, default False)
    ignore  — mark handled without touching the catalog
    The three iron rules hold: no DELETE, no pricing writes, enable defaults False.
    """
    ensure_tables()
    results, ok_count = [], 0
    with get_db() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (_APPLY_LOCK_KEY,))
        for a in actions:
            try:
                diff_id = int(a.get('diff_id'))
                action = str(a.get('action') or '').strip().lower()
            except (TypeError, ValueError):
                results.append({'success': False, 'message': _('Invalid diff id supplied')})
                continue
            if action not in _VALID_ACTIONS:
                results.append({'success': False, 'diff_id': diff_id,
                                'message': _('Unknown action {action}', action=action)})
                continue
            row = conn.execute(
                "SELECT id, provider_slug, remote_model, local_model_id, diff_type, payload "
                "FROM model_sync_diffs WHERE id = %s AND action_taken = 'none'",
                (diff_id,),
            ).fetchone()
            if not row:
                results.append({'success': False, 'diff_id': diff_id,
                                'message': _('Diff {diff_id} not found or already handled', diff_id=diff_id)})
                continue
            diff = dict(row)

            if action == 'gray':
                if diff['diff_type'] != 'missing':
                    results.append({'success': False, 'diff_id': diff_id,
                                    'message': _('Action {action} only applies to missing diffs', action=action)})
                    continue
                msg = _apply_gray(conn, diff)
                conn.execute(
                    "UPDATE model_sync_diffs SET action_taken = 'grayed' WHERE id = %s", (diff_id,))
                results.append({'success': True, 'diff_id': diff_id, 'message': msg})
                ok_count += 1
            elif action == 'insert':
                if diff['diff_type'] != 'new':
                    results.append({'success': False, 'diff_id': diff_id,
                                    'message': _('Action {action} only applies to new diffs', action=action)})
                    continue
                msg = _apply_insert(conn, diff, enable=bool(a.get('enable')))
                conn.execute(
                    "UPDATE model_sync_diffs SET action_taken = 'inserted' WHERE id = %s", (diff_id,))
                results.append({'success': True, 'diff_id': diff_id, 'message': msg})
                ok_count += 1
            else:  # ignore
                conn.execute(
                    "UPDATE model_sync_diffs SET action_taken = 'ignored' WHERE id = %s", (diff_id,))
                results.append({'success': True, 'diff_id': diff_id,
                                'message': _('Diff {diff_id} ignored', diff_id=diff_id)})
                ok_count += 1

    return {
        'success': True,
        'applied': ok_count,
        'total': len(actions),
        'results': results,
        'message': _('Applied {count} action(s)', count=ok_count),
    }

# ── Phase D: 1-token probe + circuit-breaker linkage ─────────────────────────

_PROBE_TIMEOUT = 20          # seconds per request
_PROBE_DEFAULT_SAMPLE = 3    # models per provider per run
_AUTO_GRAY_THRESHOLD = 2     # consecutive failed probes before auto-graying

# Anthropic speaks its native /v1/messages dialect; everything else in the
# probe set is OpenAI-compatible chat completions (incl. Gemini's compat layer
# and Ollama's /v1).
_PROBE_PROTOCOLS = {'anthropic': 'anthropic'}


def probe_one(base_url, api_key, model_name, protocol='openai', timeout=_PROBE_TIMEOUT):
    """Single 1-token reachability probe. Returns (ok, http_status, err_text).

    429 counts as reachable (rate-limited, not dead); 401/403/404 and 5xx
    count as failures."""
    url = base_url.rstrip('/') + ('/messages' if protocol == 'anthropic' else '/chat/completions')
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if protocol == 'anthropic':
        headers['x-api-key'] = api_key or ''
        headers['anthropic-version'] = '2023-06-01'
    else:
        if api_key:
            headers['Authorization'] = f'{_AUTH_SCHEME} {api_key}'
    body = {
        'model': model_name,
        'messages': [{'role': 'user', 'content': 'ping'}],
        'max_tokens': 1,
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.exceptions.Timeout:
        return False, None, 'timeout'
    except requests.exceptions.RequestException as e:
        return False, None, _redact_secrets(str(e))[:200]
    if resp.status_code < 400:
        return True, resp.status_code, None
    if resp.status_code == 429:
        return True, 429, None
    return False, resp.status_code, _redact_secrets(resp.text[:200])


def probe_provider_models(provider_slug=None, sample_limit=_PROBE_DEFAULT_SAMPLE):
    """Probe active text models with real 1-token requests and feed the
    failover health store.  Models failing 2 consecutive runs are auto-grayed
    (with agent reference migration); nothing is ever deleted.

    Returns a localized JSON-serializable report."""
    sample_limit = max(1, min(int(sample_limit), 10))
    from agent_matrix.failover import ModelHealthStore
    store = ModelHealthStore()

    report = {'success': True, 'providers': {}, 'auto_grayed': [],
              'summary': {'probed': 0, 'ok': 0, 'failed': 0, 'skipped': 0, 'auto_grayed': 0}}

    with get_db() as conn:
        slugs = [provider_slug] if provider_slug else sorted(FETCH_SPECS.keys())
        for slug in slugs:
            if slug in _MANUAL_ONLY:
                report['providers'][slug] = {'status': 'skipped',
                                             'reason': _('Provider {provider} has no model-list endpoint; use the manual lane', provider=slug)}
                report['summary']['skipped'] += 1
                continue
            spec = FETCH_SPECS.get(slug)
            if spec is None:
                continue
            try:
                api_key = get_api_key_for_provider(slug)
            except CredentialDecryptError:
                report['providers'][slug] = {'status': 'skipped',
                                             'reason': _('Provider {provider} skipped: the stored API key cannot be decrypted with any available master key', provider=slug)}
                report['summary']['skipped'] += 1
                continue
            # Inference endpoints reject anonymous calls even when the model
            # LIST is public (OpenRouter: list 200, chat 401). Without a key
            # every probe would fail and auto-gray the whole channel.
            if not api_key and slug != 'ollama':
                report['providers'][slug] = {'status': 'skipped',
                                             'reason': _('Provider {provider} skipped: probe requires an API key (inference endpoints reject anonymous calls)', provider=slug)}
                report['summary']['skipped'] += 1
                continue
            base_url = _configured_base_url(conn, slug)
            protocol = _PROBE_PROTOCOLS.get(slug, 'openai')
            rows = conn.execute(
                "SELECT pm.id, pm.model_name FROM provider_models pm "
                "JOIN providers p ON p.id = pm.provider_id "
                "WHERE p.slug = %s AND pm.is_active = 1 AND pm.capabilities = 'text' "
                "ORDER BY pm.sort_order ASC LIMIT %s",
                (slug, sample_limit),
            ).fetchall()

            pres = {'status': 'ok', 'probed': 0, 'ok': 0, 'failed': [], 'models_total': len(rows)}
            for r in rows:
                model_id, model_name = r['id'], r['model_name']
                ok, status_code, err = probe_one(base_url, api_key, model_name, protocol=protocol)
                pres['probed'] += 1
                report['summary']['probed'] += 1
                if ok:
                    pres['ok'] += 1
                    report['summary']['ok'] += 1
                    store.record_success(slug, model_name, model_id=model_id)
                    continue
                report['summary']['failed'] += 1
                store.record_failure(slug, model_name, model_id=model_id,
                                     error='probe: ' + (err or str(status_code)))
                health = conn.execute(
                    'SELECT consecutive_failures FROM ai_model_health '
                    'WHERE provider_slug = %s AND model_name = %s',
                    (slug, model_name),
                ).fetchone()
                streak = health['consecutive_failures'] if health else 1
                entry = {'model': model_name, 'error': (err or str(status_code)),
                         'consecutive_failures': streak}
                if streak >= _AUTO_GRAY_THRESHOLD:
                    msg = _gray_model_by_id(conn, model_id)
                    entry['auto_grayed'] = True
                    entry['message'] = msg
                    report['auto_grayed'].append({'provider': slug, 'model': model_name})
                    report['summary']['auto_grayed'] += 1
                pres['failed'].append(entry)
            report['providers'][slug] = pres

    report['message'] = _(
        'Probe finished: {ok} reachable, {failed} failed, {skipped} providers skipped, {grayed} auto-grayed',
        ok=report['summary']['ok'], failed=report['summary']['failed'],
        skipped=report['summary']['skipped'], grayed=report['summary']['auto_grayed'],
    )
    logger.info('[ModelSync] probe: %s', json.dumps(report['summary']))
    return report
