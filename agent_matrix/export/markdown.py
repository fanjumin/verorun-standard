# markdown.py — 通用 deck → markdown 渲染器
#
# 职责边界（与 xlsx.py / pptx.py 完全一致，越界即返工）：
#   - 只认契约 agent_matrix/deck/schema.py，不知道任何业务含义；
#   - 不计算、不造数、不造文案：单元格与段落全部来自 deck，缺失留空；
#   - 用户可见文字一律走 _()，译文在 i18n/*.yml。
#
# 为什么需要它：同一个 deck 出三种格式时，.md 不能是「另写一份」——客户会把
# md 与 xlsx 对照着看，数字走样比缺一种格式更糟。这里与另两个渲染器共用同一份
# sections、同一套 unit 换算、同一条来源标注。
from __future__ import annotations

import os
import re
from datetime import datetime

from i18n import _

from .formula import resolve_rows
from .provenance import note_for

# agent_matrix/export/markdown.py → 上三级即项目根
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MEDIA_DIR = os.path.join(_PROJECT_ROOT, 'data', 'media', 'temp')


def _fmt_cell(value, scale):
    """单元格 → 显示串。能解析成数就按 scale 缩放后千分位两位小数。

    与 pptx._cell_text 同口径；不 import 对方是为了不让 markdown 渲染拉进
    python-pptx 依赖（本渲染器应当只用标准库）。
    """
    if value is None:
        return ''
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        num = float(value)
        if num != num or num in (float('inf'), float('-inf')):
            return ''
        if scale:
            num *= scale
        return format(round(num, 2), ',.2f')
    return str(value)


def _escape_pipe(text):
    """表格单元格里的 | 会把列撑断，转义掉。"""
    return str(text).replace('|', '\\|').replace('\n', ' ')


def _render_table(sec, scales):
    entry = scales.get(sec.get('unit') or '')
    scale, label = entry if entry else (None, None)
    columns = list(sec.get('columns') or [])
    rows = resolve_rows(sec)
    if not columns:
        return []
    lines = ['| ' + ' | '.join(_escape_pipe(c) for c in columns) + ' |',
             '| ' + ' | '.join('---' for _ in columns) + ' |']
    for row in rows:
        cells = [_fmt_cell(row[i] if i < len(row) else None, scale)
                 for i in range(len(columns))]
        lines.append('| ' + ' | '.join(_escape_pipe(c) for c in cells) + ' |')
    if label or sec.get('note'):
        lines.append('')
        bits = ([_('Unit: {unit}', unit=label)] if label else []) + [sec.get('note')]
        lines.append('_' + ' · '.join(str(b) for b in bits if b) + '_')
    return lines


def _render_chart(sec, scales):
    """chart → 数据表。不出图形（与 xlsx 同决策）：markdown 里的图形没有可编辑性，
    凭空画一张还会与 pptx 的原生图表口径打架。"""
    entry = scales.get(sec.get('unit') or '')
    scale, label = entry if entry else (None, None)
    categories = list(sec.get('categories') or [])
    series = list(sec.get('series') or [])
    if not categories or not series:
        return []
    header = [_('Category')] + [str(s.get('name') or '') for s in series]
    lines = ['| ' + ' | '.join(_escape_pipe(c) for c in header) + ' |',
             '| ' + ' | '.join('---' for _ in header) + ' |']
    for i, cat in enumerate(categories):
        cells = [str(cat)]
        for s in series:
            values = s.get('values') or []
            cells.append(_fmt_cell(values[i] if i < len(values) else None, scale))
        lines.append('| ' + ' | '.join(_escape_pipe(c) for c in cells) + ' |')
    if label:
        lines += ['', '_' + _('Unit: {unit}', unit=label) + '_']
    return lines


def _render_key_points(sec):
    items = list(sec.get('items') or [])
    if not items:
        return []
    lines = []
    for item in items:
        head = str(item.get('head') or '').strip()
        body = str(item.get('body') or '').strip()
        ref = str(item.get('ref') or '').strip()
        if not head:
            continue
        line = '- **%s**' % head
        if body:
            line += ' — ' + body
        if ref:
            line += ' `%s`' % ref
        lines.append(line)
    return lines


def render_markdown(deck, out_dir=None, scale_by_unit=None, filename=None):
    """把 deck 渲染成 markdown，返回文件绝对路径。

    deck          ResearchDeck（调用前应先过 validate_deck）
    out_dir       落盘目录，默认 data/media/temp/
    scale_by_unit {unit: (scale, label)}——与 xlsx / pptx 同一张表，保证三份
                  交付物的数字字面一致（金额亿元、倍数不缩放）。
    filename      指定文件名；默认 标的_数据日期.md

    正文段落（type=text）按原样输出：研报的叙述不该被渲染器改写。
    """
    scales = scale_by_unit or {}
    subject = deck.get('subject') or {}
    source_note = note_for(deck.get('provenance'), as_of=subject.get('as_of'))
    sections = list(deck.get('sections') or [])

    cover = next((s for s in sections if s.get('type') == 'cover'), None) or {}
    title = str(cover.get('title') or subject.get('name') or subject.get('id') or _('Research brief'))
    out = ['# ' + title, '']
    subtitle = str(cover.get('subtitle') or '').strip()
    if subtitle:
        out += ['> ' + subtitle, '']

    for sec in sections:
        kind = sec.get('type')
        if kind == 'cover':
            continue
        if kind == 'disclaimer':
            continue                      # 统一放文末，避免夹在正文中间
        heading = str(sec.get('title') or '')
        body = []
        if kind == 'table':
            body = _render_table(sec, scales)
        elif kind == 'chart':
            body = _render_chart(sec, scales)
        elif kind == 'key_points':
            body = _render_key_points(sec)
        elif kind == 'text':
            # 正文原样输出：段内已有换行，不重排、不折行、不加粗
            body = [str(sec.get('text') or '').rstrip()]
        if not body:
            continue
        if heading:
            out += ['## ' + heading, '']
        out += body + ['']

    disclaimer = str(deck.get('disclaimer') or '').strip()
    if not disclaimer:
        disclaimer = str(next((s.get('text') for s in sections
                               if s.get('type') == 'disclaimer'), '') or '').strip()
    if disclaimer:
        out += ['---', '', '> ' + disclaimer, '']
    if source_note:
        out += ['_' + source_note + '_', '']

    sid = re.sub(r'[^A-Za-z0-9._-]', '_', str(subject.get('id') or '')) or 'deck'
    day = str(subject.get('as_of') or deck.get('generated_at') or '')[:10].replace('-', '')
    name = filename or ('%s_%s.md' % (sid, day or datetime.now().strftime('%Y%m%d')))

    outdir = out_dir or _MEDIA_DIR
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, name)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(out).rstrip() + '\n')
    return path
