"""插件管理命令（GET/POST /admin/plugins/*，路径已核对 plugin_manager/routes.py）。

过滤说明：服务端 /admin/plugins 仅支持 status 过滤参数
（见 plugin_manager/routes.py:146），因此这里只暴露 --status，
不伪造其它未实现的过滤开关。
"""

from __future__ import annotations

import click

from ..constants import _
from ._base import client_of, get, parse_json_option, post, seg, wants_json
from ..output import emit_json, render

PREFIX = "/admin/plugins"


def register(group):
    @group.group("plugins", help=_("Plugin management"))
    def plugins():
        pass

    @plugins.command("list")
    @click.option("--status", default=None,
                  help=_("Filter by status (the server only supports status filtering)"))
    @click.pass_context
    def list_plugins(ctx, status):
        """列出插件（GET /admin/plugins）"""
        get(ctx, PREFIX, {"status": status})

    @plugins.command("install")
    @click.argument("identifier")
    @click.pass_context
    def install(ctx, identifier):
        """安装插件（POST /admin/plugins/<id>/install）"""
        post(ctx, f"{PREFIX}/{seg(identifier)}/install", _("Install"))

    @plugins.command("enable")
    @click.argument("identifier")
    @click.pass_context
    def enable(ctx, identifier):
        """启用插件（POST /admin/plugins/<id>/enable）"""
        post(ctx, f"{PREFIX}/{seg(identifier)}/enable", _("Enable"))

    @plugins.command("disable")
    @click.argument("identifier")
    @click.pass_context
    def disable(ctx, identifier):
        """停用插件（POST /admin/plugins/<id>/disable）"""
        post(ctx, f"{PREFIX}/{seg(identifier)}/disable", _("Disable"))

    @plugins.command("config")
    @click.argument("identifier")
    @click.option("--set", "set_json", help=_("Set config (JSON string)"))
    @click.pass_context
    def config(ctx, identifier, set_json):
        """查看 / 设置插件配置（GET/POST /admin/plugins/<id>/config）"""
        path = f"{PREFIX}/{seg(identifier)}/config"
        if set_json:
            payload = parse_json_option(set_json, "--set")
            post(ctx, path, _("Configure"), payload)
        else:
            get(ctx, path)

    # ── plugin-dev：插件开发工具链（区别于上面的 plugins 管理命令）──
    @group.group("plugin-dev", help=_("Plugin dev toolchain (scaffold / validate / pack / submit)"))
    def plugin_dev():
        pass

    @plugin_dev.command("init")
    @click.argument("name")
    @click.option("--dir", "out_dir", default=".", show_default=True,
                  help=_("Output directory (a <name>/ subdirectory is created inside it)"))
    def plugin_dev_init(name, out_dir):
        """生成插件脚手架（plugin.json + main.py + README）"""
        _make_scaffold(name, out_dir)

    @plugin_dev.command("validate")
    @click.option("--dir", "plugin_dir", default=".", show_default=True,
                  help=_("Plugin directory (containing plugin.json)"))
    def plugin_dev_validate(plugin_dir):
        """本地校验 plugin.json + 危险代码静态扫描（最终以服务端 AI 审核为准）"""
        _validate_plugin(plugin_dir)

    @plugin_dev.command("pack")
    @click.option("--dir", "plugin_dir", default=".", show_default=True,
                  help=_("Plugin directory (containing plugin.json)"))
    @click.option("--out", "out_path", default=None,
                  help=_("Output zip path (default <parent>/<identifier>.zip)"))
    def plugin_dev_pack(plugin_dir, out_path):
        """打包插件目录为 zip（排除 __pycache__/.git 等）"""
        _pack_plugin(plugin_dir, out_path)

    @plugin_dev.command("submit")
    @click.option("--dir", "plugin_dir", default=None,
                  help=_("Plugin directory (packed automatically before submit)"))
    @click.option("--zip", "zip_path", default=None,
                  help=_("Path to an already packed zip"))
    @click.pass_context
    def plugin_dev_submit(ctx, plugin_dir, zip_path):
        """上传提审（POST /admin/plugins/developer/submit）"""
        _submit_plugin(ctx, plugin_dir, zip_path)


# ── plugin-dev 实现（纯本地操作，不依赖 ctx.client，除 submit 外）──
import json as _json
import os as _os
import re as _re
import sys as _sys
import shutil as _shutil
import tempfile as _tempfile
import zipfile as _zipfile
from pathlib import Path as _Path

# manifest 必填字段（对齐 plugin_manager/audit._check_structure + submit_plugin）
_REQUIRED_MANIFEST = ("identifier", "name", "version", "description", "category")
_MANIFEST_SEMVER = _re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$")
_MANIFEST_ID_RE = _re.compile(r"^[a-z0-9_]+$")

# 危险代码静态扫描（轻量正则，仅供参考；服务端 AI 审核为最终裁决）
# 展示用说明保留英文源串，在命中处经 _() 按当前语言翻译（模块级常量不可调用 _()）。
_DANGEROUS_PATTERNS = [
    (r"\bos\.system\b", "os.system direct shell call"),
    (r"\bsubprocess\.(call|run|Popen)\b", "subprocess child process execution"),
    (r"\bsocket\.(socket|create_connection)\b", "direct network socket connection"),
    (r"\beval\s*\(", "eval dynamic execution"),
    (r"\bexec\s*\(", "exec dynamic execution"),
    (r"\b__import__\s*\(", "__import__ dynamic import"),
    (r"base64\.(b64decode|decodestring)\s*\(", "base64 decoding (obfuscated payload)"),
]


def _scaffold_manifest(name: str) -> dict:
    return {
        "identifier": _re.sub(r"[^a-z0-9_]", "_", name.lower()),
        "name": name,
        "version": "0.1.0",
        "description": "Describe what this plugin does",
        "category": "utility",
        "entry": "main.py",
        "min_app_version": "",
        "agent_role": "",
        "capabilities": [],
        "permissions": [],
    }


def _make_scaffold(name: str, out_dir: str) -> None:
    base = _Path(out_dir).expanduser()
    target = base / name
    if target.exists():
        raise click.ClickException(_("Directory already exists: {target}", target=target))
    target.mkdir(parents=True, exist_ok=True)
    (target / "plugin.json").write_text(
        _json.dumps(_scaffold_manifest(name), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (target / "main.py").write_text(
        (_('"""%s plugin entry."""') + "\n\ndef register(ctx):\n    pass\n") % name,
        encoding="utf-8")
    (target / "README.md").write_text(
        f"# {name}\n\n{_('VeroRun plugin.')}\n", encoding="utf-8")
    click.echo(_("✅ Scaffold generated: {target}", target=target))


def _find_manifest(plugin_dir: str) -> _Path:
    base = _Path(plugin_dir).expanduser()
    cand = base / "plugin.json"
    if not cand.exists():
        raise click.ClickException(_("plugin.json not found: {cand}", cand=cand))
    return cand


def _validate_plugin(plugin_dir: str) -> None:
    manifest_path = _find_manifest(plugin_dir)
    try:
        meta = _json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise click.ClickException(_("plugin.json is not valid JSON: {exc}", exc=exc))
    if not isinstance(meta, dict):
        raise click.ClickException(_("plugin.json must have an object at the top level"))

    errors = []
    for field in _REQUIRED_MANIFEST:
        if not str(meta.get(field) or "").strip():
            errors.append(_("Missing required field: {field}", field=field))
    if errors:
        raise click.ClickException("; ".join(errors))

    identifier = str(meta.get("identifier") or "").strip().lower()
    version = str(meta.get("version") or "").strip()
    if not _MANIFEST_ID_RE.match(identifier):
        raise click.ClickException(_(
            "Invalid identifier: {identifier} (only lowercase letters, digits and "
            "underscores are allowed)", identifier=identifier))
    if not _MANIFEST_SEMVER.match(version):
        raise click.ClickException(_(
            "Invalid version: {version} (expected X.Y.Z or X.Y.Z-prerelease)", version=version))
    min_ver = str(meta.get("min_app_version") or "").strip()
    if min_ver and not _MANIFEST_SEMVER.match(min_ver):
        raise click.ClickException(_("Invalid min_app_version: {min_version}", min_version=min_ver))
    for arr_field in ("capabilities", "permissions", "compatible_editions"):
        val = meta.get(arr_field)
        if val is not None and not isinstance(val, list):
            raise click.ClickException(_("{field} must be an array", field=arr_field))

    # 危险代码静态扫描：优先复用服务端 audit._scan_dangerous（同仓库时）
    hits = []
    try:
        _sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))
        from plugin_manager.audit import _scan_dangerous  # type: ignore
        hits = _scan_dangerous(str(_Path(plugin_dir).expanduser()))
    except Exception:
        _scan_dangerous = None  # type: ignore
        for root, _dirs, files in _os.walk(_Path(plugin_dir).expanduser()):
            for fn in files:
                if not fn.endswith((".py", ".js")):
                    continue
                text = _Path(root, fn).read_text(encoding="utf-8", errors="ignore")
                for pattern, desc in _DANGEROUS_PATTERNS:
                    if _re.search(pattern, text):
                        hits.append(f"{_Path(root, fn).name}: {_(desc)}")

    click.echo(_("✅ manifest validated: {identifier} v{version}",
                 identifier=identifier, version=version))
    if hits:
        click.echo(_("⚠️ Risky code detected (server-side AI review still applies after submission):"))
        for h in hits:
            click.echo(f"  - {h}")
    else:
        click.echo(_("✅ No risky code patterns found"))


def _pack_plugin(plugin_dir: str, out_path: str = None) -> None:
    base = _Path(plugin_dir).expanduser()
    _find_manifest(str(base))
    identifier = str(_json.loads((base / "plugin.json").read_text(encoding="utf-8"))
                     .get("identifier") or base.name)
    if not out_path:
        out_path = str(base.parent / f"{identifier}.zip")
    out = _Path(out_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)

    _SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", ".pending"}
    _SKIP_EXT = {".pyc", ".pyo"}
    with _zipfile.ZipFile(out, "w", _zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in _os.walk(base):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fn in files:
                if _Path(fn).suffix in _SKIP_EXT:
                    continue
                full = _Path(root, fn)
                arc = full.relative_to(base).as_posix()
                zf.write(full, arc)
    click.echo(_("✅ Packed: {out}", out=out))


def _submit_plugin(ctx, plugin_dir: str, zip_path: str) -> None:
    tmp = None
    if not zip_path and plugin_dir:
        tmp = _tempfile.mkstemp(suffix=".zip")[1]
        _pack_plugin(plugin_dir, tmp)
        zip_path = tmp
    if not zip_path:
        raise click.UsageError(_("Specify the plugin to submit via --dir <directory> or --zip <file>"))
    zip_file = _Path(zip_path).expanduser()
    if not zip_file.exists():
        raise click.ClickException(_("zip not found: {zip_file}", zip_file=zip_file))
    with open(zip_file, "rb") as fh:
        data = client_of(ctx).request_json(
            "POST", "/admin/plugins/developer/submit",
            files={"file": (zip_file.name, fh, "application/zip")},
            timeout=180)
    if tmp:
        try:
            _os.unlink(tmp)
        except OSError:
            pass
    if wants_json(ctx):
        emit_json(data)
    else:
        render(data)
