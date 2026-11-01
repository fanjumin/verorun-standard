# pptx.py — 通用 deck → pptx 渲染器（T2）
#
# 职责边界（越界即返工）：
#   - 只认契约 agent_matrix/deck/schema.py，不知道任何业务含义；金融的估值口径、
#     单位、术语一律由插件侧通过参数传入，不进内核；
#   - **不计算**：deck 里是字符串就按字符串画，缩放只是呈现层乘法，不做业务推算；
#   - **不造数**：页文案、表格行列全部来自 deck；缺失留空，绝不补全或估算；
#   - **不造文案**：用户可见文字一律走 _()，译文在 i18n yml（CI 硬编码中文零增长门禁）。
#
# 与 xlsx.py 的三条一致性约定（不必成对出现，但行为不许分叉）：
#   1. cover 进首页、disclaimer 进末页、sections 里的 cover/disclaimer 段不再单占页；
#   2. chart 段落落成数据表（T4 才做 pptx 原生图表），不丢信息也不假装画了图；
#   3. 数值换算按 section.unit 查 scale_by_unit，绝不全局套用（xlsx.py 实测踩过）。
#
# 渲染器通用：给它一份 audit 类 deck 同样能出文件（T2 验收项之一）。
from __future__ import annotations

import os
import re
from datetime import datetime

from pptx.util import Emu, Inches

from i18n import _

from . import layouts
from .charts import build_chart
from .formula import resolve_rows
from .provenance import note_for
# 千分位串 → float 的边界逻辑（NaN/inf/百分号）已在 T1 写过，这里复用而非复制，
# 免得两处走样。渲染器之间共享同一个解析口径是必要的。
from .xlsx import _to_number

# agent_matrix/export/pptx.py → 上三级即项目根
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MEDIA_DIR = os.path.join(_PROJECT_ROOT, 'data', 'media', 'temp')

# 一页容量：key_points 一条可能占两行（head + body），按 2 行折算
KP_PER_PAGE = 7
# 正文页字数上限：正文区 4.5 英寸、约 14 号字，实测 900 字内不溢出。
# 超了按段落切页，宁可多一页也不把字挤成蚂蚁。
TEXT_PER_PAGE = 900
# 图表高度：正文区高度减去下方单位/来源小字留白
_CHART_H = Inches(4.3)


def render_pptx(deck, out_dir=None, scale_by_unit=None, filename=None):
    """把 deck 渲染成 pptx，返回文件绝对路径。

    deck          ResearchDeck（调用前应先过 validate_deck）
    out_dir       落盘目录，默认 data/media/temp/
    scale_by_unit {unit: (scale, label)}——按 section.unit 查表的呈现层换算；
                  金融侧传 {'CNY': (1e-8, 'CNY 100M')} 把金额换算成亿元。
                  unit 不在表里的 section 不缩放（估值倍数、每股指标等）。
    filename      指定文件名；默认 标的_数据日期.pptx

    渲染器不计算：缩放只是呈现层乘法，deck 里没有的字段一个也不补。
    渲染器通用：给它一份 audit 类 deck 同样能出文件。
    """
    prs = layouts.new_deck_presentation()
    scales = scale_by_unit or {}
    subject = deck.get('subject') or {}
    subject_line = ' · '.join(x for x in (
        '%s %s' % (subject.get('id') or '', subject.get('name') or ''),
        _('Data as of') + ' ' + str(subject.get('as_of') or ''),
    ) if x.strip())

    _render_cover(prs, deck, subject_line)

    # 来源脚注：整份 deck 一个口径，Excel 批注与 PPT 脚注共用同一串。
    # 无来源时 note_for 返回「模型估算（无来源记录）」——不静默留空。
    source_note = note_for(deck.get('provenance'), as_of=subject.get('as_of'))

    for sec in deck.get('sections') or []:
        kind = sec.get('type')
        if kind in ('cover', 'disclaimer'):
            continue          # cover 已进首页，disclaimer 进末页
        title = sec.get('title') or _(kind or 'text')
        if kind == 'table':
            _render_table(prs, title, sec, scales, source_note)
        elif kind == 'chart':
            _render_chart(prs, title, sec, scales, source_note)
        elif kind == 'key_points':
            _render_key_points(prs, title, sec, source_note)
        else:
            _render_text(prs, title, sec, source_note)

    _render_disclaimer(prs, deck)
    _add_footers(prs, subject_line)

    sid = re.sub(r'[^A-Za-z0-9._-]', '_', str(subject.get('id') or '')) or 'deck'
    day = str(subject.get('as_of') or deck.get('generated_at') or '')[:10].replace('-', '')
    name = filename or ('%s_%s.pptx' % (sid, day or datetime.now().strftime('%Y%m%d')))

    outdir = out_dir or _MEDIA_DIR
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, name)
    return layouts.save(prs, path)


# ── 各 type 的落页 ──────────────────────────────────────────────

def _render_cover(prs, deck, subject_line):
    sec = next((s for s in deck.get('sections') or [] if s.get('type') == 'cover'), None)
    subject = deck.get('subject') or {}
    if sec:
        title = sec.get('title') or str(subject.get('name') or subject.get('id') or '')
        subtitle = sec.get('subtitle') or ''
    else:
        title = str(subject.get('name') or subject.get('id') or _('Untitled'))
        subtitle = ''
    slide = layouts.blank_slide(prs)
    layouts.cover(slide, title, subtitle, subject_line)


def _cell_text(value, scale):
    """单元格 → 显示串。能解析成数就按 scale 缩放后再千分位格式化，
    解析不了就原样当文本。缩放只发生在呈现层，deck 里的原值不受影响。"""
    num = _to_number(value)
    if num is None:
        return '' if value is None else str(value)
    if scale:
        num = num * scale
    return format(round(num, 2), ',.2f')


def _render_table(prs, title, sec, scales, source_note):
    unit = sec.get('unit') or ''
    entry = scales.get(unit)
    scale, label = entry if entry else (None, None)
    columns = list(sec.get('columns') or [])
    # formula 的结果填进对应格子：PPT 没有单元格公式，不填就永远是个空窗
    rows = [[_cell_text(v, scale) for v in row] for row in resolve_rows(sec)]

    if not columns and not rows:
        return

    # 空表也要出一页（标题 + 表头），否则该段落在交付物里凭空消失
    chunks = [rows[i:i + layouts.ROW_CAP] for i in range(0, len(rows), layouts.ROW_CAP)] or [[]]
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        slide = layouts.blank_slide(prs)
        page_title = title if total == 1 else '%s (%d/%d)' % (title, idx, total)
        layouts.title_block(slide, page_title)
        layouts.table(slide, columns, chunk, unit=label or unit, note=sec.get('note'))
        layouts.source_line(slide, source_note)
    # 注：formula_cells 在 PPT 里无处安放——PPT 没有单元格公式。
    # 这里不画公式，也不把公式文本塞进表格假装它是数据（xlsx 才有活公式）。


def _render_chart(prs, title, sec, scales, source_note):
    """chart → pptx 原生图表（T4）。

    量纲按 sec.unit 查表，与三表同口径——否则同一份 deck 里三表显示亿元、
    趋势图显示元，交付物自己打架。换算在这里做完再交给 charts.build_chart，
    图表模块不碰数值，避免第二套口径。
    """
    series = sec.get('series') or []
    categories = list(sec.get('categories') or [])
    entry = scales.get(sec.get('unit') or '')
    scale = entry[0] if entry else None
    unit_label = entry[1] if entry else (sec.get('unit') or '')

    resolved = []
    for s in series:
        vals = s.get('values') or []
        nums = []
        for v in vals:
            n = _to_number(v)
            nums.append(n * scale if (n is not None and scale) else n)
        resolved.append({'name': s.get('name'), 'values': nums})

    has_data = bool(categories) and any(
        any(v is not None for v in s['values']) for s in resolved)
    if not has_data:
        # 无数据不画空图：兜底成数据表，且明确标注「这是图的降级形态」
        subtitle = _('{kind} chart (rendered as table)',
                     kind=str(sec.get('chart') or 'line'))
        columns = [_('Category')] + [str(s.get('name') or '') for s in resolved]
        rows = [[str(cat)] + [_cell_text(s['values'][i], None)
                              if i < len(s['values']) else '' for s in resolved]
                for i, cat in enumerate(categories)]
        slide = layouts.blank_slide(prs)
        layouts.title_block(slide, title, subtitle)
        layouts.table(slide, columns, rows, unit=unit_label, note=sec.get('note'))
        layouts.source_line(slide, source_note)
        return

    slide = layouts.blank_slide(prs)
    layouts.title_block(slide, title)
    # 未知 chart 类型由 build_chart 抛 ValueError：静默画空图比报错更难发现
    build_chart(slide,
                {'chart': sec.get('chart'), 'categories': categories, 'series': resolved},
                layouts.MARGIN, layouts.BODY_TOP, layouts.CONTENT_W, _CHART_H)
    layouts.note_line(slide, [unit_label, sec.get('note')],
                      Emu(int(layouts.BODY_TOP) + int(_CHART_H) + int(Inches(0.06))))
    layouts.source_line(slide, source_note)




def _render_key_points(prs, title, sec, source_note):
    items = list(sec.get('items') or [])
    if not items:
        return
    chunks = [items[i:i + KP_PER_PAGE] for i in range(0, len(items), KP_PER_PAGE)]
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        slide = layouts.blank_slide(prs)
        page_title = title if total == 1 else '%s (%d/%d)' % (title, idx, total)
        layouts.title_block(slide, page_title)
        # 每条要点带 ref 的编上序号，页脚按序号回指——序号与 deck 里的 ref 一一对应
        numbered, notes = [], []
        for it in chunk:
            ref = it.get('ref')
            if ref:
                notes.append('[%d] %s' % (len(notes) + 1, ref))
                numbered.append(dict(it, head='%s [%d]' % (it.get('head') or '',
                                                           len(notes))))
            else:
                numbered.append(it)
        layouts.bullets(slide, numbered)
        layouts.source_line(slide, '   '.join([source_note] + notes))


def _chunk_text(text, limit=TEXT_PER_PAGE):
    """正文按段落切页：单页塞进 PPT 的高度是有限的，硬放会把字挤成蚂蚁。

    只在段落边界切（空行/换行），不从句子中间断——研报正文断在半句上比多一页难读。
    超长单段无边界可切时才按字数硬断。
    """
    paras = [p for p in str(text or '').split('\n')]
    pages, buf, size = [], [], 0
    for para in paras:
        p = para.strip()
        if not p:
            continue
        while len(p) > limit:                 # 单段本身就超长
            if buf:
                pages.append('\n'.join(buf))
                buf, size = [], 0
            pages.append(p[:limit])
            p = p[limit:]
        if size + len(p) + 1 > limit and buf:
            pages.append('\n'.join(buf))
            buf, size = [], 0
        buf.append(p)
        size += len(p) + 1
    if buf:
        pages.append('\n'.join(buf))
    return pages or ['']


def _render_text(prs, title, sec, source_note):
    pages = _chunk_text(sec.get('text'))
    total = len(pages)
    for idx, page in enumerate(pages, start=1):
        slide = layouts.blank_slide(prs)
        page_title = title if total == 1 else '%s (%d/%d)' % (title, idx, total)
        layouts.title_block(slide, page_title)
        layouts.paragraph(slide, page)
        layouts.source_line(slide, source_note)


def _render_disclaimer(prs, deck):
    text = deck.get('disclaimer') or ''
    if not text.strip():
        return
    slide = layouts.blank_slide(prs)
    layouts.disclaimer_page(slide, text)


def _add_footers(prs, subject_line):
    """页码统一在最后加（此时才知道总页数）；封面不编号。"""
    slides = list(prs.slides)
    total = len(slides)
    for i, slide in enumerate(slides):
        if i == 0:
            layouts.footer(slide, subject_line)
        else:
            layouts.footer(slide, subject_line, '%d / %d' % (i + 1, total))
