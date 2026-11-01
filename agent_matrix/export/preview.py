# preview.py — 交付物预览：xlsx / pptx → PDF（LibreOffice headless）
#
# 为什么不像网页表格那样直接渲染单元格：估值页的目标价是**活公式**，而 openpyxl
# 用 data_only 读出来是 None —— 公式是我们自己写进去的，没有缓存值。只有
# LibreOffice 会真的算一遍。所以预览必须走「转 PDF」：用户看到的与 Excel 里
# 打开的一致，包括算出来的数。
#
# 为什么要缓存：实测冷启动 79s、热 15~27s。预览是点开就要看的东西，不能每次都等。
# 转换在登记交付物时后台跑一次，之后再打开直接读缓存文件（见 deliverables.py）。
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile

# 常见安装位置：打包后的机器不一定有 PATH，但装过 LibreOffice 就在这几个地方
_CANDIDATES = (
    r'C:\Program Files\LibreOffice\program\soffice.exe',
    r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
    '/usr/bin/soffice',
    '/usr/local/bin/soffice',
    '/usr/lib/libreoffice/program/soffice',
    '/Applications/LibreOffice.app/Contents/MacOS/soffice',
)

# 单次转换上限：13 页 pptx 冷启动 79s、热 15s；给足余量但不无限等
_TIMEOUT_SECONDS = 240


def find_soffice() -> str | None:
    """找 LibreOffice 可执行文件；找不到返回 None（调用方降级，不抛）。"""
    override = (os.environ.get('VERORUN_SOFFICE') or '').strip()
    if override and os.path.isfile(override):
        return override
    for name in ('soffice', 'soffice.exe'):
        found = shutil.which(name)
        if found:
            return found
    for path in _CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def available() -> bool:
    """预览能力是否可用。前端据此决定「预览」按钮亮不亮，而不是点了才失败。"""
    return find_soffice() is not None


def _run(args: list[str]) -> None:
    kwargs = {
        # 桌面端没有控制台；不抑制会闪一个黑窗口
        'stdout': subprocess.PIPE,
        'stderr': subprocess.PIPE,
        'timeout': _TIMEOUT_SECONDS,
    }
    if os.name == 'nt':
        kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    proc = subprocess.run(args, **kwargs)
    if proc.returncode != 0:
        detail = (proc.stderr or b'').decode('utf-8', 'replace').strip()[:300]
        raise RuntimeError('convert failed (exit %s): %s' % (proc.returncode, detail))


def convert_to_pdf(src: str, out_dir: str) -> str:
    """把 src（xlsx/pptx）转成 PDF，返回 PDF 路径。

    每次转换用一个临时的 UserInstallation 配置目录：LibreOffice 默认共用同一份
    用户配置，两个 headless 进程同时启动会互相锁死（表现为永久阻塞），而我们的
    转换是在后台线程里跑的，并发并不罕见。
    """
    exe = find_soffice()
    if not exe:
        raise RuntimeError('libreoffice not found')

    os.makedirs(out_dir, exist_ok=True)
    # ignore_cleanup_errors：LibreOffice 退出后仍可能在写自己的配置目录，删到一半
    # 报「目录不是空的」。这是清理噪音，不能让它把一次成功的转换判成失败。
    with tempfile.TemporaryDirectory(prefix='vr-lo-profile-',
                                     ignore_cleanup_errors=True) as profile:
        profile_uri = pathlib.Path(profile).resolve().as_uri()
        _run([
            exe,
            '--headless', '--norestore', '--nolockcheck', '--nodefault',
            '--convert-to', 'pdf',
            '--outdir', out_dir,
            src,
            '-env:UserInstallation=' + profile_uri,
        ])

    out = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0] + '.pdf')
    if not os.path.isfile(out):
        raise RuntimeError('convert produced no pdf')
    return out
