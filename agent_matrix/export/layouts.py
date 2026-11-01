# layouts.py — PPT 版式原语（T2）
#
# 职责边界（越界即返工）：
#   - 只管「怎么画」，不管「画什么」：不读 deck、不认识业务字段、不判断量纲；
#     所有入参都是渲染器切好片、换好算的成品数据；
#   - **不造数**：表格行列、要点文本一律来自调用方，缺失留空，绝不补全或估算；
#   - **不造文案**：用户可见文字一律走 _()，译文在 i18n yml（CI 硬编码中文零增长门禁）。
#
# 视觉语言复用 agent_matrix/routes.py:_generate_ppt_file（深色 16:9 / 空白版
# layout[6] / ACCENT 青）。不另起一套主题，否则同一产品出现两种配色。
#
# 图表：本单不出图形（T4 做 pptx 原生图表）。chart 段落落成数据表——与 xlsx.py 的
# 处理保持一致：不丢信息，也不假装画了图。
from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Emu, Inches, Pt

from i18n import _

# ── 主题 ──
BG = RGBColor(0x0D, 0x11, 0x17)
ACCENT = RGBColor(0x00, 0xD4, 0xAA)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
MUTED = RGBColor(0x88, 0x99, 0xAA)
GRID = RGBColor(0x2A, 0x33, 0x40)

# 多系列配色：首个系列固定走 ACCENT，之后按序取，避免默认 Office 蓝
SERIES_COLORS = (
    RGBColor(0x00, 0xD4, 0xAA),   # 青
    RGBColor(0xFF, 0x8A, 0x4C),   # 橙
    RGBColor(0xA7, 0x8B, 0xFA),   # 紫
    RGBColor(0xFB, 0xBF, 0x24),   # 黄
)

# ── 版面（16:9）──
SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
MARGIN = Inches(0.75)
CONTENT_W = Inches(11.833)

# 正文区：标题块下方到页脚之间
BODY_TOP = Inches(1.75)
BODY_H = Inches(4.9)
FOOTER_TOP = Inches(6.85)

# ── 字号 ──
COVER_TITLE_PT = 40
COVER_SUB_PT = 18
TITLE_PT = 26
BODY_PT = 14
SMALL_PT = 11
NOTE_PT = 9

# 表格几何：表头 + 数据行的固定行高，超出的行由渲染器切片分页
HEADER_H = Inches(0.42)
ROW_H = Inches(0.30)
ROW_CAP = 14          # (BODY_H - HEADER_H) / ROW_H 的下取整数值
FONT = 'Microsoft YaHei'


def new_deck_presentation():
    """建一副 16:9 空白演示文稿。"""
    from pptx import Presentation

    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    return prs


def blank_slide(prs):
    """加一页空白页并铺背景色，返回 slide。"""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = BG
    return slide


def _textbox(slide, left, top, width, height):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    return tf


def _run(para, text, size, color, bold=False, font=FONT):
    run = para.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = font
    return run


def _accent_bar(slide, top):
    """标题左侧的青色竖条——整份 deck 的页级识别元素。"""
    from pptx.enum.shapes import MSO_SHAPE

    bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, MARGIN, top + Inches(0.08),
        Inches(0.06), Inches(0.42))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()
    bar.shadow.inherit = False


def cover(slide, title, subtitle=None, subject_line=None):
    """封面页：标题居中偏上，副标题青色，底部一行标的/日期小字。"""
    tf = _textbox(slide, MARGIN, Inches(2.3), CONTENT_W, Inches(1.6))
    tf.paragraphs[0].alignment = PP_ALIGN.CENTER
    _run(tf.paragraphs[0], title, COVER_TITLE_PT, WHITE, bold=True)

    if subtitle:
        p = tf.add_paragraph()
        p.alignment = PP_ALIGN.CENTER
        _run(p, subtitle, COVER_SUB_PT, ACCENT)

    if subject_line:
        tf2 = _textbox(slide, MARGIN, Inches(4.3), CONTENT_W, Inches(0.6))
        tf2.paragraphs[0].alignment = PP_ALIGN.CENTER
        _run(tf2.paragraphs[0], subject_line, SMALL_PT, MUTED)


def title_block(slide, title, subtitle=None):
    """内页标题块（含青色竖条），返回正文可用高度起点。"""
    _accent_bar(slide, Inches(0.95))
    tf = _textbox(slide, MARGIN + Inches(0.25), Inches(0.95), CONTENT_W, Inches(0.7))
    _run(tf.paragraphs[0], title, TITLE_PT, WHITE, bold=True)
    if subtitle:
        p = tf.paragraphs[0]
        _run(p, '    ', TITLE_PT, WHITE)
        _run(p, subtitle, SMALL_PT, MUTED)
    return BODY_TOP


def bullets(slide, items, top=BODY_TOP, height=BODY_H):
    """要点列表：head 为主行，body（非空时）缩进为副行。"""
    tf = _textbox(slide, MARGIN + Inches(0.25), top, CONTENT_W, height)
    first = True
    for it in items:
        head = str(it.get('head') or '')
        body = str(it.get('body') or '')
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.space_after = Pt(10)
        _run(p, '▪  ', BODY_PT, ACCENT)
        _run(p, head, BODY_PT, WHITE)
        if body:
            pb = tf.add_paragraph()
            pb.space_after = Pt(10)
            _run(pb, '     ' + body, SMALL_PT, MUTED)


def table(slide, columns, rows, top=BODY_TOP, height=BODY_H, unit=None, note=None):
    """一页表格。rows 必须是已按 ROW_CAP 切好片的单页数据。"""
    if not columns:
        return
    nrow = len(rows) + 1
    width = CONTENT_W
    shape_h = min(int(HEADER_H + ROW_H * len(rows)), int(height))

    gt = slide.shapes.add_table(nrow, len(columns), MARGIN, top,
                                width, Emu(shape_h)).table
    gt.first_row = True
    gt.horz_banding = False

    for c, name in enumerate(columns):
        cell = gt.cell(0, c)
        cell.text = str(name)
        cell.fill.solid()
        cell.fill.fore_color.rgb = GRID
        para = cell.text_frame.paragraphs[0]
        para.runs[0].font.size = Pt(SMALL_PT)
        para.runs[0].font.bold = True
        para.runs[0].font.color.rgb = ACCENT
        para.runs[0].font.name = FONT
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE

    for r, row in enumerate(rows, start=1):
        for c in range(len(columns)):
            cell = gt.cell(r, c)
            cell.text = str(row[c]) if c < len(row) else ''
            cell.fill.solid()
            cell.fill.fore_color.rgb = BG
            para = cell.text_frame.paragraphs[0]
            if para.runs:
                para.runs[0].font.size = Pt(SMALL_PT)
                para.runs[0].font.color.rgb = WHITE
                para.runs[0].font.name = FONT
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE

    if unit or note:
        bits = ([_('Unit: {unit}', unit=unit)] if unit else []) + ([note] if note else [])
        note_line(slide, bits, Emu(int(top) + shape_h + int(Inches(0.12))), width)


def note_line(slide, bits, top, width=CONTENT_W):
    """表格/图表下方的单位与来源小字。bits 为空则不画。"""
    bits = [b for b in bits if b]
    if not bits:
        return
    tf = _textbox(slide, MARGIN, top, width, Inches(0.4))
    _run(tf.paragraphs[0], '   ·   '.join(str(b) for b in bits), NOTE_PT, MUTED)


def paragraph(slide, text, top=BODY_TOP, height=BODY_H, size=BODY_PT, color=WHITE):
    """纯文本块，按换行分段。"""
    tf = _textbox(slide, MARGIN + Inches(0.25), top, CONTENT_W, height)
    first = True
    for line in str(text or '').split('\n'):
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.space_after = Pt(6)
        _run(p, line, size, color)


def disclaimer_page(slide, text):
    """末页免责：整页小字灰体。"""
    title_block(slide, _('Disclaimer'))
    paragraph(slide, text, top=BODY_TOP, height=Inches(4.6), size=SMALL_PT, color=MUTED)


def source_line(slide, text, width=CONTENT_W):
    """页脚上方的来源脚注。空串不画——但调用方不该给空串：无来源应传
    「模型估算（无来源记录）」，静默留空等于没标注。"""
    text = str(text or '')
    if not text:
        return
    tf = _textbox(slide, MARGIN, Emu(int(FOOTER_TOP) - int(Inches(0.42))), width, Inches(0.35))
    _run(tf.paragraphs[0], text, NOTE_PT, MUTED)


def footer(slide, left_text, right_text=None):
    """页脚：左标的信息，右页码。"""
    tf = _textbox(slide, MARGIN, FOOTER_TOP, Inches(9.0), Inches(0.35))
    _run(tf.paragraphs[0], left_text, NOTE_PT, MUTED)
    if right_text:
        tf2 = _textbox(slide, Inches(11.5), FOOTER_TOP, Inches(1.1), Inches(0.35))
        tf2.paragraphs[0].alignment = PP_ALIGN.RIGHT
        _run(tf2.paragraphs[0], right_text, NOTE_PT, MUTED)


def save(prs, path):
    prs.save(path)
    return path
