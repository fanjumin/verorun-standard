# formula.py — 把公式单元格的结果值填回 rows
#
# 为什么需要这一步：formula_cells 里的地址是按 **xlsx 布局**定义的（第 1 行表名、
# 第 2 行列头、第 3 行起是数据）。md 与 pptx 渲染器同样要出这张表，但它们没有
# 「单元格」这个概念，只能逐行逐列摊平——不填这一步，估值表的「Target price」
# 在 md/pptx 里就是空的，客户拿到的正文缺了最关键的那个数。
#
# 本层只做「填」：值来自装配侧（formula_cells[].value），这里不计算、不猜。
# 没有 value 的公式（老 deck）保持原样——渲染器不会凭空造一个数出来。
from __future__ import annotations

import re

# 与 deck/CONTRACT.md「表格在 xlsx 中的行列布局」、deliverables.py 同一约定
_HEADER_ROWS = 2
_ADDR = re.compile(r'^([A-Z]{1,3})(\d+)$')


def _col_index(letters: str) -> int:
    """A → 0，B → 1，AA → 26。"""
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def resolve_rows(section: dict) -> list:
    """返回把公式结果填进去的 rows 副本（不改动 deck 本身）。"""
    rows = [list(row) for row in (section.get('rows') or [])]
    for item in section.get('formula_cells') or []:
        value = item.get('value')
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        match = _ADDR.match(str(item.get('cell') or ''))
        if not match:
            continue
        col = _col_index(match.group(1))
        row = int(match.group(2)) - 1 - _HEADER_ROWS
        if not (0 <= row < len(rows)) or col >= len(rows[row]):
            continue
        rows[row][col] = value
    return rows
