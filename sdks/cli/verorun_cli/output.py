"""输出渲染：rich 优先，缺失时降级为纯文本。

命名约定（修复审计指出的 `emit()` 语义混淆）：
    render()    —— 通用输出：dict/list 走 JSON，其余走 str
    emit_json() —— 强制 JSON
    emit_text() —— 不换行的裸文本（流式逐字输出用）
    err/warn/info —— 一律写 stderr，避免污染 stdout 的可管道数据
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Iterable, List, Optional

from .constants import _

try:
    from rich.console import Console
    from rich.table import Table

    _console: Optional["Console"] = Console()
    HAS_RICH = True
except ImportError:  # pragma: no cover - 取决于运行环境是否装了 rich
    _console = None
    HAS_RICH = False


def render(obj: Any) -> None:
    """通用输出：对象以 JSON 打印（便于脚本集成），标量直接打印。"""
    if isinstance(obj, (dict, list)):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(str(obj) + "\n")


def emit_json(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def emit_text(text: str, end: str = "") -> None:
    sys.stdout.write(text)
    if end:
        sys.stdout.write(end)
    sys.stdout.flush()


def err(msg: str) -> None:
    sys.stderr.write(_("[Error] {message}", message=msg) + "\n")


def info(msg: str) -> None:
    sys.stderr.write(_("[Info] {message}", message=msg) + "\n")


def warn(msg: str) -> None:
    sys.stderr.write(_("[Warning] {message}", message=msg) + "\n")


def render_obj_as_table(title: str, rows: Optional[List[Dict[str, Any]]],
                        columns: Iterable[str]) -> None:
    """用 rich 表格渲染对象列表；无 rich 或无数据时降级为 JSON。"""
    if not HAS_RICH or not rows:
        emit_json(rows if rows is not None else {title: _("(empty)")})
        return
    table = Table(title=title, show_lines=False)
    cols = list(columns)
    for col in cols:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(row.get(col, "")) for col in cols])
    _console.print(table)
