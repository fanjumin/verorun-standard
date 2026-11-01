#!/usr/bin/env python3
"""
Plugin Manager — 插件审核引擎（AI 审核网关 · 批次2）
====================================================
上传插件进入 pending 队列后，调用本模块进行规则引擎自动审核，
输出结构化报告供人工审批兜底（插件标准 §16：AI 辅助 + 人工审批）。

检查项：
  1. 官方水印/签名复核（复用 watermark.detect_official_watermark）
  2. 结构合规（plugin.json 必填字段 / identifier 格式 / 入口 __init__.py）
  3. 官方插件代码相似度比对（文件级 SHA256 精确匹配，识别直接复制）
  4. 危险代码扫描（os.system / subprocess / eval / exec / pickle / socket 等）

审核结论（audit_status）:
  reject  — 硬拒（官方水印/签名命中、结构不合规、命中危险代码）
  manual  — 需人工复核（官方 identifier 白名单命中、与官方文件完全一致）
  pass    — 通过（无风险特征，等待 approve 安装）
"""

import os
import re
import json
import ast
import hashlib
from typing import Any, Dict, List, Optional

from i18n import _

from .watermark import detect_official_watermark, OFFICIAL_PLUGIN_IDS

# 必填字段
_REQUIRED_FIELDS = ('identifier', 'name', 'version', 'min_app_version')
_IDENTIFIER_RE = re.compile(r'^[a-z0-9_]+$')

# 危险代码模式（正则，忽略大小写）
_DANGEROUS_PATTERNS = [
    (r'\bos\.system\s*\(', 'os.system command execution'),
    (r'\bos\.popen\s*\(', 'os.popen command execution'),
    (r'\bsubprocess\s*(\.|\[)', 'subprocess process invocation'),
    (r'\bPopen\s*\(', 'Popen process invocation'),
    (r'\beval\s*\(', 'eval dynamic execution'),
    (r'\bexec\s*\(', 'exec dynamic execution'),
    (r'\bpickle\s*\.\s*(loads?|dumps)\s*\(', 'pickle deserialization'),
    (r'\bmarshal\s*\.\s*loads\s*\(', 'marshal deserialization'),
    (r'\b__import__\s*\(', 'dynamic import'),
    (r'\bsocket\s*\.\s*connect\s*\(', 'socket outbound connection'),
    (r'\bshutil\s*\.\s*rmtree\s*\(', 'shutil.rmtree recursive deletion'),
]

# 参与相似度比对的文件后缀
_SIM_EXTS = ('.py', '.json')

# 水印/签名文件不入官方指纹库（M4 修复）
_WM_FILES = {'verorun.manifest', 'verorun.signature'}

# 超大文件只扫首尾各 1MB，避免整文件载入内存（M1 修复）
_MAX_SCAN_BYTES = 1024 * 1024

# 默认跳过的目录
_SKIP_DIRS = {'__pycache__', '.git'}


def _sha256_file(path: str) -> str:
    sha = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            sha.update(chunk)
    return sha.hexdigest()


def _walk_files(root: str):
    """遍历目录下全部文件（跳过缓存目录）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            yield os.path.join(dirpath, fname)


def build_official_fingerprint(plugins_root: str) -> Dict[str, Dict[str, str]]:
    """构建官方插件文件指纹库 {identifier: {relpath: sha256}}。

    仅收录白名单内的官方插件目录（避免把用户已安装的自研插件计入）。
    """
    lib = {}
    if not os.path.isdir(plugins_root):
        return lib
    for entry in sorted(os.listdir(plugins_root)):
        if entry.startswith('_') or entry.startswith('.'):
            continue
        if entry not in OFFICIAL_PLUGIN_IDS:
            continue
        pdir = os.path.join(plugins_root, entry)
        if not os.path.isdir(pdir) or not os.path.isfile(os.path.join(pdir, 'plugin.json')):
            continue
        files = {}
        for path in _walk_files(pdir):
            if not path.endswith(_SIM_EXTS):
                continue
            rel = os.path.relpath(path, pdir).replace('\\', '/')
            if rel in _WM_FILES:  # M4 修复：水印/签名文件不入指纹
                continue
            try:
                files[rel] = _sha256_file(path)
            except OSError:
                continue
        lib[entry] = files
    return lib


def _check_structure(plugin_dir: str) -> List[str]:
    """结构合规检查，返回问题列表（空列表 = 合规）。"""
    reasons = []
    meta_path = os.path.join(plugin_dir, 'plugin.json')
    if not os.path.isfile(meta_path):
        return [_('Missing plugin.json')]
    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
    except json.JSONDecodeError:
        return [_('Invalid plugin.json: not valid JSON')]
    except OSError as e:
        return [_('Failed to read plugin.json: {error}', error=e)]

    missing = [k for k in _REQUIRED_FIELDS if not str(meta.get(k) or '').strip()]
    if missing:
        reasons.append(_('Missing required fields: {fields}', fields=', '.join(missing)))

    identifier = str(meta.get('identifier') or '').strip().lower()
    if identifier and not _IDENTIFIER_RE.match(identifier):
        reasons.append(_('Invalid identifier format: {identifier}', identifier=identifier))

    # 入口文件检查：支持插件目录内直接 __init__.py 或 <identifier>/ 子目录
    if os.path.isfile(os.path.join(plugin_dir, '__init__.py')):
        pass
    elif identifier and os.path.isfile(os.path.join(plugin_dir, identifier, '__init__.py')):
        pass
    else:
        reasons.append(_('Missing plugin entry point __init__.py'))

    # P1-1 / P1-2：agent_role/capabilities + 版本字段校验（并入 reasons）
    reasons.extend(_check_roles_and_capabilities(meta))
    reasons.extend(_check_version_fields(meta))
    return reasons


# ── P1-1：agent_role + capabilities 强制校验（对齐标准 §2.2，v1.6 起必填）──
def _check_roles_and_capabilities(meta: dict) -> list:
    """agent_role 必须为 9 核心角色之一；capabilities 非空。

    核心角色集从 agent_matrix.models.get_core_role_slugs() 动态推导（与 enable() 同源）。
    """
    problems = []
    _role = (meta or {}).get('agent_role') or ''
    try:
        from agent_matrix.models import get_core_role_slugs
        _core = get_core_role_slugs()
    except ImportError:
        _core = []
    if _role not in _core:
        problems.append(f"agent_role '{_role}' must be one of core roles: {sorted(_core)}")
    _caps = (meta or {}).get('capabilities') or []
    if not isinstance(_caps, list) or not _caps:
        problems.append('capabilities must be a non-empty array')
    return problems


# ── P1-2：min_app_version / compatible_editions 格式与取值校验（对齐标准 §13.4/§14.9.3）──
def _check_version_fields(meta: dict) -> list:
    problems = []
    _min = (meta or {}).get('min_app_version') or ''
    if _min and not re.match(r'^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$', str(_min)):
        problems.append(f"min_app_version '{_min}' must be semver")
    _eds = (meta or {}).get('compatible_editions') or []
    if not isinstance(_eds, list):
        problems.append('compatible_editions must be an array (empty [] = all editions)')
    return problems


def _scan_dangerous(plugin_dir: str) -> List[str]:
    """危险代码扫描，返回命中列表（超大 .py 仅扫头尾，防内存暴涨）。"""
    hits = []
    for path in _walk_files(plugin_dir):
        if not path.endswith('.py'):
            continue
        try:
            _sz = os.path.getsize(path)
            if _sz > _MAX_SCAN_BYTES * 4:
                continue  # 超大文件交由人工复核
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                if _sz <= _MAX_SCAN_BYTES:
                    text = f.read()
                else:
                    head = f.read(_MAX_SCAN_BYTES)
                    f.seek(max(0, _sz - _MAX_SCAN_BYTES))
                    tail = f.read(_MAX_SCAN_BYTES)
                    text = head + tail
        except OSError:
            continue
        for pat, label in _DANGEROUS_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                hits.append(f'{_(label)} ({os.path.relpath(path, plugin_dir)})')
    # 去重保序
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _load_plugin_meta(plugin_dir: str) -> Dict[str, Any]:
    """读取插件 plugin.json（失败返回空 dict，供权限一致性校验）。"""
    meta_path = os.path.join(plugin_dir, 'plugin.json')
    if not os.path.isfile(meta_path):
        return {}
    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _scan_dangerous_ast(plugin_dir: str) -> List[str]:
    """AST 级危险代码扫描（补充正则盲区，P0-1）。

    正则 _DANGEROUS_PATTERNS 已覆盖 os.system()/eval() 等直接调用；
    AST 层补抓可确定性判定的间接/混淆调用：
      1) getattr(任意, 'system'/'popen'/'eval' 等) 动态取危险函数
      2) 任意对象 .system()/.popen()/.Popen()（别名/局部变量绕过 os 前缀）
      3) __import__('os').system(...) 链式调用
    命中即并入「危险代码」判定（reject）。
    """
    hits = []
    for path in _walk_files(plugin_dir):
        if not path.endswith('.py'):
            continue
        try:
            _sz = os.path.getsize(path)
            if _sz > _MAX_SCAN_BYTES * 4:
                continue  # 超大文件交由人工复核
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                src = f.read()
        except OSError:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        rel = os.path.relpath(path, plugin_dir).replace('\\', '/')
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            # ① getattr 动态取危险函数
            if (isinstance(fn, ast.Name) and fn.id == 'getattr'
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and str(node.args[1].value) in ('system', 'popen', 'Popen', 'eval', 'exec')):
                hits.append(_('getattr dynamically obtains dangerous function {name}() ({path})',
                              name=node.args[1].value,
                              path=os.path.relpath(path, plugin_dir)))
                continue
            # ② 任意对象 .system()/.popen()/.Popen()（含 __import__('os').system 链式）
            if isinstance(fn, ast.Attribute) and fn.attr in ('system', 'popen', 'Popen'):
                if isinstance(fn.value, ast.Call) and isinstance(fn.value.func, ast.Name) and fn.value.func.id == '__import__':
                    hits.append(_('__import__().{attr} dynamic command execution ({path})',
                                  attr=fn.attr, path=rel))
                elif not (isinstance(fn.value, ast.Name) and fn.value.id == 'os'):
                    hits.append(_('.{attr}() command execution ({path})', attr=fn.attr, path=rel))
                continue
    # 去重保序
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _call_root_module(fn) -> Optional[str]:
    """提取调用表达式根的模块名：urllib.request.urlopen → 'urllib'；requests.get → 'requests'；
    无法确定（局部变量/调用链）返回 None。"""
    node = fn
    while isinstance(node, ast.Attribute):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_network_call(fn) -> bool:
    """判定 Call.func 是否命中常见网络请求写法。

    覆盖：裸名 urlopen/urlretrieve、requests.*、requests.Session().xxx、
    aiohttp.ClientSession().xxx、urllib.request.urlopen/urlretrieve。
    仅匹配确定性网络动词，避免把 urllib.parse、aiohttp.web（服务端）等误判为外连。
    """
    _verbs = ('get', 'post', 'put', 'delete', 'patch', 'request')
    if isinstance(fn, ast.Name) and fn.id in ('urlopen', 'urlretrieve'):
        return True  # from urllib.request import urlopen 等
    if not isinstance(fn, ast.Attribute):
        return False
    root = _call_root_module(fn)
    if root == 'urllib':
        return fn.attr in ('urlopen', 'urlretrieve')
    if root in ('requests', 'aiohttp') and fn.attr in _verbs:
        return True
    # requests.Session().xxx / aiohttp.ClientSession().xxx（fn.value 是 Call，root 提取不到）
    if (fn.attr in _verbs
            and isinstance(fn.value, ast.Call)
            and isinstance(fn.value.func, ast.Attribute)
            and fn.value.func.attr in ('Session', 'ClientSession')
            and _call_root_module(fn.value.func.value) in ('requests', 'aiohttp')):
        return True
    return False


def _check_permission_consistency(plugin_dir: str, meta: Dict[str, Any]) -> List[str]:
    """权限一致性校验（P0-1）：代码实际使用的网络外连/文件写操作，
    必须在 plugin.json permissions 中显式声明。

    仅覆盖可确定性判定的写法，避免误伤：
      - 网络请求（urlopen/urlretrieve、requests.*、requests.Session().*、
        aiohttp.ClientSession().*、urllib.request.urlopen/urlretrieve）
      - 文件写/删除（open(...,'w'/'a'/'x')、os.remove/unlink、shutil.rmtree/move/rename）

    读文件为插件常规操作，不纳入。结果以「权限不一致：」前缀进入人工复核（manual），不直接拒绝。
    """
    declared = set(meta.get('permissions') or [])
    issues = []
    for path in _walk_files(plugin_dir):
        if not path.endswith('.py'):
            continue
        try:
            _sz = os.path.getsize(path)
            if _sz > _MAX_SCAN_BYTES * 4:
                continue
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                src = f.read()
        except OSError:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        rel = os.path.relpath(path, plugin_dir).replace('\\', '/')
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            # 网络外连（含 urllib.request.urlopen / requests.Session().xxx 链式）
            if 'network:request' not in declared:
                if _is_network_call(fn):
                    issues.append(_('Permission mismatch: code uses network requests but does not '
                                    'declare network:request ({path})', path=rel))
            # 文件写/删除
            if 'filesystem:write' not in declared:
                fs = False
                if isinstance(fn, ast.Name) and fn.id == 'open' and len(node.args) >= 2:
                    mode = node.args[1]
                    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
                        mode_str = mode.value.lstrip()
                        if mode_str and mode_str[0] in ('w', 'a', 'x'):
                            fs = True
                elif (isinstance(fn, ast.Attribute)
                      and fn.attr in ('remove', 'unlink', 'rmtree', 'rename', 'move')
                      and isinstance(fn.value, ast.Name) and fn.value.id in ('os', 'shutil')):
                    fs = True
                if fs:
                    issues.append(_('Permission mismatch: code performs file write/delete but does '
                                    'not declare filesystem:write ({path})', path=rel))
    # 去重
    seen, out = set(), []
    for h in issues:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _check_similarity(plugin_dir: str,
                      official_lib: Dict[str, Dict[str, str]]) -> List[str]:
    """与官方插件做文件级 SHA256 精确匹配，返回完全一致的命中列表。"""
    matches = []
    for path in _walk_files(plugin_dir):
        if not path.endswith(_SIM_EXTS):
            continue
        rel = os.path.relpath(path, plugin_dir).replace('\\', '/')
        if rel in ('verorun.manifest', 'verorun.signature'):
            continue
        try:
            sha = _sha256_file(path)
        except OSError:
            continue
        for pid, files in official_lib.items():
            if sha in files.values():
                official_rel = next((k for k, v in files.items() if v == sha), '?')
                matches.append(_('{path} is identical to official plugin {plugin}/{official}',
                                 path=rel, plugin=pid, official=official_rel))
                break
    return matches


def review_plugin(plugin_dir: str, watermark_result: Dict[str, Any] = None,
                  plugins_root: str = None, submitted_version: str = '') -> Dict[str, Any]:
    """对 pending 插件执行规则引擎审核。

    Args:
        plugin_dir:        待审核插件目录（plugins/.pending/<id>/）
        watermark_result:  预先的水印检测结果（缺省则内部重新检测）
        plugins_root:      官方插件目录（缺省为项目 plugins/）
        submitted_version: 提交时声明的版本（P1-5 一致性守卫；与包内 plugin.json.version 比对）

    Returns:
        {'status': 'reject' | 'manual' | 'pass',
         'reasons': [...],
         'report': {...}}
    """
    if watermark_result is None:
        watermark_result = detect_official_watermark(plugin_dir)

    report = {'watermark': watermark_result}
    reasons: List[str] = []

    # ① 官方水印/签名
    wm = watermark_result or {}
    if wm.get('official'):
        if wm.get('method') in ('signature', 'manifest', 'wm_comment', 'wm_meta'):
            reasons.append(_('Official watermark/signature detected ({reason})',
                             reason=wm.get("reason", "")))
        else:
            reasons.append(_('Official marker detected, manual review required ({reason})',
                             reason=wm.get("reason", "")))

    # ② 结构合规
    structure = _check_structure(plugin_dir)
    report['structure'] = structure
    reasons.extend(structure)

    # ③ 官方插件相似度比对
    official_lib = build_official_fingerprint(plugins_root or
        os.path.join(os.path.dirname(__file__), '..', 'plugins'))
    similarity = _check_similarity(plugin_dir, official_lib)
    report['similarity'] = similarity
    if similarity:
        reasons.append(_('{count} files are identical to official plugin files, manual review required',
                         count=len(similarity)))

    # ④ 危险代码扫描
    dangerous = _scan_dangerous(plugin_dir)
    report['dangerous'] = dangerous
    reasons.extend(_('Dangerous code: {detail}', detail=d) for d in dangerous)

    # ⑤ AST 级危险扫描（补充正则盲区）
    ast_dangerous = _scan_dangerous_ast(plugin_dir)
    report['ast_dangerous'] = ast_dangerous
    reasons.extend(_('Dangerous code: {detail}', detail=d) for d in ast_dangerous)

    # ⑥ 权限一致性校验（声明必须覆盖实际使用，进入人工复核）
    perm_issues = _check_permission_consistency(plugin_dir, _load_plugin_meta(plugin_dir))
    report['permission_consistency'] = perm_issues
    reasons.extend(perm_issues)

    # ⑦ P1-5：包内版本与提交版本一致性守卫（审计 P1-2 修复）
    if submitted_version:
        _pkg_meta = _load_plugin_meta(plugin_dir)
        _pkg_ver = str((_pkg_meta or {}).get('version') or '').strip()
        if _pkg_ver and _pkg_ver != str(submitted_version).strip():
            reasons.append(
                _('Package plugin.json.version({pkg}) does not match submitted version({submitted}), '
                  'manual review required', pkg=_pkg_ver, submitted=submitted_version))

    # 判定优先级：reject > manual > pass
    status = 'pass'
    for r in reasons:
        if ('Official watermark/signature' in r or r.startswith('Missing')
                or 'Invalid' in r or r.startswith('Dangerous code')):
            status = 'reject'
            break
    if status == 'pass':
        for r in reasons:
            # P0 修复：agent_role/capabilities/版本字段问题必须显式置 manual，
            # 防止缺 agent_role/capabilities 的第三方包自动审核静默 pass 后仍被发布。
            if ('manual review required' in r or 'is identical to official plugin' in r
                    or 'Permission mismatch' in r
                    or r.startswith('agent_role') or r.startswith('capabilities')
                    or r.startswith('min_app_version') or r.startswith('compatible_editions')):
                status = 'manual'
                break

    return {'status': status, 'reasons': reasons, 'report': report}
