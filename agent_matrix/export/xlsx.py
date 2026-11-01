# xlsx.py — 通用 deck → xlsx 渲染器（T1）
#
# 职责边界（越界即返工）：
#   - 只认契约 agent_matrix/deck/schema.py，不知道任何业务含义；金融的估值口径、
#     单位、术语一律由插件侧通过参数传入，不进内核；
#   - **不计算**：formula_cells 只把字符串写进单元格，不解析、不求值、不校验；
#     item 里给了 value 就照抄成缓存值（<v>），那是装配侧算的，本层不核对；
#     只认「有 <f> 且 <v> 为空」的形态，已有值一律不覆盖；
#   - **不造数**：单元格内容全部来自 deck；缺失留空，绝不补全或估算；
#   - **不造文案**：用户可见文字一律走 _()，译文在 i18n yml（CI 硬编码中文零增长门禁）。
#
# 单位换算（scale_by_unit）：deck 存原始值（如元），呈现层按 **section 自己的 unit**
# 查表决定缩放因子与显示标签。金融侧传 {'CNY': (1e-8, 'CNY 100M')} 得亿元。
# 必须按 unit 而不是全局套：同一份 deck 里金额是「元」、PE 是「倍数」、EPS 是「元/股」，
# 全局缩放会把估值倍数也除以 1e8（实测踩过）。渲染器只认 unit 字符串，不知道它是什么量纲。
#
# 图表：本单不出图形（T4 做 pptx 原生图表，Excel 图形另议），chart 段落落成数据表，
# 不丢信息也不假装画了图。
from __future__ import annotations

import os
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

from i18n import _

from .provenance import note_for

# agent_matrix/export/xlsx.py → 上三级即项目根
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MEDIA_DIR = os.path.join(_PROJECT_ROOT, 'data', 'media', 'temp')

_SHEET_MAX = 31
_SHEET_INVALID = re.compile(r'[\[\]:*?/\\]')
# 千分位与裸数值两种形态：装配器可能已把数值格式化成字符串（反解析成真数字，
# 否则 Excel 拿到的是文本单元格，不能参与公式、改假设也不联动）
_NUM_TEXT = re.compile(r'^-?\d{1,3}(,\d{3})*(\.\d+)?$|^-?\d+(\.\d+)?$')
_NUMFMT = '#,##0.00'
_MIN_COL_W = 8
_MAX_COL_W = 42

# xlsx 包内的命名空间：sheet 名与 sheetN.xml 的对应关系靠 workbook.xml 的 r:id
# 加 rels 解析，不能按创建顺序猜——猜错会把值写进别人家的表。
_XL_MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
_XL_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def _sheet_targets(zf):
    """读 workbook.xml + rels，返回 {sheet 名: 'xl/worksheets/sheetN.xml'}。"""
    root = ET.fromstring(zf.read('xl/workbook.xml'))
    rels = ET.fromstring(zf.read('xl/_rels/workbook.xml.rels'))
    rid2target = {}
    for rel in rels:
        rid2target[rel.get('Id')] = rel.get('Target') or ''

    out = {}
    for sheet in root.iter('{%s}sheet' % _XL_MAIN):
        rid = sheet.get('{%s}id' % _XL_REL)
        target = rid2target.get(rid) or ''
        if target.startswith('/'):
            target = target[1:]          # 绝对路径形态
        elif not target.startswith('xl/'):
            target = 'xl/' + target      # 相对 xl/ 的形态
        out[sheet.get('name')] = target
    return out


def _fill_sheet(xml, values):
    """把一张 sheet 里公式单元格的空 `<v></v>` 填上数，返回 (新 xml, 填充格数)。

    只认「有 <f> 但 <v> 为空」的形态：已经有值的单元格一律不碰，否则就是用
    我们的数冒充 Excel 算出来的数。openpyxl 目前写公式正是留空 <v></v>。
    """
    filled = 0
    for addr, value in values.items():
        pattern = re.compile(
            r'(<c r="%s"[^>]*>\s*<f[^>]*>[^<]*</f>)\s*<v\s*/?>(?:</v>)?'
            % re.escape(addr)
        )
        xml, n = pattern.subn(r'\g<1><v>%s</v>' % ('%.15g' % value), xml, count=1)
        filled += n
    return xml, filled


def _rewrite_zip(path, changed):
    """用改过的条目重写 xlsx（zip 内是 XML 包，做不到原地改）。"""
    tmp = path + '.tmp'
    try:
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                data = changed.get(item.filename)
                dst.writestr(item, data if data is not None else src.read(item.filename))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _fill_formula_cache(path, pending):
    """把公式的计算结果写进单元格缓存值，返回实际填充的格数。

    为什么值得多这一趟：openpyxl 写公式只写 `<f>`，`<v>` 留空。于是任何不求值的
    读者（openpyxl 的 data_only、SheetJS、下游脚本）都只拿到 None，预览端因此
    不得不启一个 LibreOffice 去算一遍——实测冷启 79 秒。把装配时已经算好的数
    直接落盘，文件自带答案。

    这不改变「以公式为准」：配了 fullCalcOnLoad，Excel 打开会重算，用户改假设
    也照常联动；缓存值只服务于不想算的读者。
    """
    if not pending:
        return 0

    with zipfile.ZipFile(path) as zf:
        targets = _sheet_targets(zf)
        changed, total = {}, 0
        for name, values in pending.items():
            target = targets.get(name)
            if not target:
                continue
            xml, n = _fill_sheet(zf.read(target).decode('utf-8'), values)
            if n:
                changed[target] = xml.encode('utf-8')
                total += n

    if changed:
        _rewrite_zip(path, changed)
    return total


def _safe_text(value):
    """文本落单元格前中性化：以 = + - @ 开头的串会被 Excel 当公式执行。

    这是真实的注入面——key_points 的文本可能来自客户端传来的研判结论，
    一条「=HYPERLINK(...)」之类的串就能让打开表格的人中招。前置单引号是
    Excel 的转义约定，显示与再导出都不带这个引号。

    只作用于真正的文本：能解析成数值的已走 number 分支（所以 "-3.2"
    不会被误伤成文本）。
    """
    text = str(value)
    if text[:1] in ('=', '+', '-', '@'):
        return "'" + text
    return text


def _to_number(v):
    """单元格值 → float；不可解析返回 None（当作文本处理）。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            f = float(v)
        except (TypeError, ValueError, OverflowError):
            return None
        # NaN / inf 不能落单元格（Excel 打开会报 #NUM!）
        if f != f or f in (float('inf'), float('-inf')):
            return None
        return f
    if isinstance(v, str):
        s = v.strip()
        if not s or not _NUM_TEXT.match(s):
            return None
        try:
            return float(s.replace(',', ''))
        except (TypeError, ValueError):
            return None
    return None


def _safe_sheet_name(raw, used):
    """sheet 名合法化：非法字符替换、截断到 31、与已用名去重。"""
    name = _SHEET_INVALID.sub('_', str(raw or '').strip()) or 'Sheet'
    name = name[:_SHEET_MAX]
    base, i = name, 2
    while name in used:
        suffix = '_%d' % i
        name = base[:_SHEET_MAX - len(suffix)] + suffix
        i += 1
    used.add(name)
    return name


def _autosize(ws):
    """按内容设列宽；CJK 字符按 2 个宽度计。"""
    from openpyxl.utils import get_column_letter

    widths = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            # 一个 CJK 字符约等于两个西文字符宽
            length = sum(2 if ord(ch) > 127 else 1 for ch in str(cell.value))
            widths[cell.column] = max(widths.get(cell.column, 0), length)
    for col, length in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = max(
            _MIN_COL_W, min(_MAX_COL_W, length + 2)
        )


def attach_comment(ws, addr, text):
    """给单元格写批注（T3 的引用标注用本接口）。

    本单只提供写入能力，不定义批注内容的生成规则——那是 T3 的事。
    """
    from openpyxl.comments import Comment

    try:
        ws[addr].comment = Comment(str(text), 'deck')
        return True
    except Exception:
        return False


def _write_cover(ws, deck):
    from openpyxl.styles import Font

    subject = deck.get('subject') or {}
    prov = deck.get('provenance') or {}

    r = 1
    for sec in deck.get('sections') or []:
        if sec.get('type') == 'cover':
            ws.cell(row=r, column=1, value=sec.get('title') or '').font = Font(bold=True, size=14)
            r += 1
            if sec.get('subtitle'):
                ws.cell(row=r, column=1, value=_safe_text(sec.get('subtitle') or ''))
                r += 1
            break
    r += 1

    sources = prov.get('data_sources') or []
    indicators = prov.get('indicators') or []
    facts = (
        (_('Subject'), '%s  %s' % (subject.get('id') or '', subject.get('name') or '')),
        (_('Data as of'), subject.get('as_of') or _('Not available')),
        (_('Generated at'), deck.get('generated_at') or _('Not available')),
        (_('Data sources'), ', '.join(str(s) for s in sources) or _('Not available')),
        (_('Indicators'), ', '.join(str(s) for s in indicators) or _('Not available')),
        (_('Model'), prov.get('model') or _('Not available')),
        (_('Evidence hash'), prov.get('evidence_hash') or _('Not available')),
    )
    for label, value in facts:
        ws.cell(row=r, column=1, value=label).font = Font(bold=True)
        ws.cell(row=r, column=2, value=value)
        r += 1


def _write_table(ws, sec, scale, scale_label, source_note=None):
    """source_note  来源标注（T3）：写进每个有值的单元格批注。无来源时调用方
    应传「模型估算（无来源记录）」——静默留空等于没标注。

    返回 {单元格地址: 结果值}，给 _fill_formula_cache 填缓存值用。
    """
    from openpyxl.comments import Comment
    from openpyxl.styles import Font

    columns = list(sec.get('columns') or [])
    rows = list(sec.get('rows') or [])

    ws.cell(row=1, column=1, value=sec.get('title') or '').font = Font(bold=True, size=12)
    unit = scale_label or sec.get('unit') or ''
    if unit:
        ws.cell(row=1, column=2, value=_('Unit: {unit}', unit=unit))

    header_row = 2
    for c, name in enumerate(columns, start=1):
        ws.cell(row=header_row, column=c, value=name).font = Font(bold=True)

    r = header_row
    for row in rows:
        r += 1
        for c, value in enumerate(row, start=1):
            number = _to_number(value)
            if number is not None:
                if scale:
                    number = number * scale
                cell = ws.cell(row=r, column=c, value=number)
                cell.number_format = _NUMFMT
                if source_note:
                    cell.comment = Comment(source_note, 'deck')
            elif value is None:
                continue
            else:
                ws.cell(row=r, column=c, value=_safe_text(value))

    # 活公式：只写字符串，渲染器不理解语义也不求值；结果值若给了，另行填进 <v>
    cached = {}
    for item in sec.get('formula_cells') or []:
        addr, formula = item.get('cell'), item.get('formula')
        if addr and isinstance(formula, str):
            ws[addr] = formula
            # 公式格也要有数字格式：否则算出来是 1396.2564，同一列上下都是
            # 1,396.26 的小数两位，唯独目标价在裸奔
            ws[addr].number_format = item.get('number_format') or _NUMFMT
            valued = _to_number(item.get('value'))
            if valued is not None:
                cached[addr] = valued

    if sec.get('note'):
        ws.cell(row=r + 2, column=1, value=_safe_text(sec.get('note') or ''))

    return cached


def _write_chart(ws, sec):
    """chart 段落落成数据表。

    不出图形：图形是 T4 的事，且 Excel 侧是否要图另议。落成表是为了不丢数据。
    """
    from openpyxl.styles import Font

    ws.cell(row=1, column=1, value=sec.get('title') or '').font = Font(bold=True, size=12)
    categories = list(sec.get('categories') or [])
    ws.cell(row=2, column=1, value=_('Category')).font = Font(bold=True)
    for c, name in enumerate(categories, start=2):
        ws.cell(row=2, column=c, value=_safe_text(name))

    r = 2
    for series in sec.get('series') or []:
        r += 1
        ws.cell(row=r, column=1, value=_safe_text(series.get('name') or ''))
        for c, value in enumerate(series.get('values') or [], start=2):
            number = _to_number(value)
            if number is not None:
                cell = ws.cell(row=r, column=c, value=number)
                cell.number_format = _NUMFMT
            elif value is not None:
                ws.cell(row=r, column=c, value=_safe_text(value))


def _write_text(ws, sec):
    from openpyxl.styles import Font

    r = 1
    if sec.get('title'):
        ws.cell(row=r, column=1, value=sec.get('title')).font = Font(bold=True, size=12)
        r += 1
    for line in str(sec.get('text') or '').split('\n'):
        ws.cell(row=r, column=1, value=_safe_text(line))
        r += 1


def _write_key_points(ws, sec):
    from openpyxl.styles import Font

    ws.cell(row=1, column=1, value=sec.get('title') or '').font = Font(bold=True, size=12)
    r = 2
    for item in sec.get('items') or []:
        ws.cell(row=r, column=1, value=_safe_text(item.get('head') or '')).font = Font(bold=True)
        if item.get('body'):
            ws.cell(row=r, column=2, value=_safe_text(item.get('body')))
        if item.get('ref'):
            ws.cell(row=r, column=3, value=item.get('ref'))
        r += 1


def _write_disclaimer(ws, deck):
    from openpyxl.styles import Alignment, Font

    ws.cell(row=1, column=1, value=_('Disclaimer')).font = Font(bold=True, size=12)
    wrap = Alignment(wrap_text=True, vertical='top')

    r = 2
    for line in str(deck.get('disclaimer') or '').split('\n'):
        ws.cell(row=r, column=1, value=_safe_text(line)).alignment = wrap
        r += 1
    # disclaimer 类型的 section 一并合并到本页，避免同一份文本散在两个 sheet
    for sec in deck.get('sections') or []:
        if sec.get('type') == 'disclaimer' and sec.get('text'):
            r += 1
            for line in str(sec.get('text')).split('\n'):
                ws.cell(row=r, column=1, value=_safe_text(line)).alignment = wrap
                r += 1


def render_xlsx(deck, out_dir=None, scale_by_unit=None, filename=None):
    """把 deck 渲染成 xlsx，返回文件绝对路径。

    deck          ResearchDeck（调用前应先过 validate_deck）
    out_dir       落盘目录，默认 data/media/temp/
    scale_by_unit {unit: (scale, label)}——按 section.unit 查表的呈现层换算；
                  金融侧传 {'CNY': (1e-8, 'CNY 100M')} 把金额换算成亿元。
                  unit 不在表里的 section 不缩放（估值倍数、每股指标等）。
    filename      指定文件名；默认 标的_数据日期.xlsx

    渲染器不计算：公式以字符串写入，是否联动由 Excel 决定；带了结果值就一并
    写成缓存值，让不求值的读者也能读到数（见 _fill_formula_cache）。
    渲染器通用：给它一份 audit 类 deck 同样能出文件（T1 验收项之一）。
    """
    from openpyxl import Workbook

    scales = scale_by_unit or {}
    subject = deck.get('subject') or {}
    # 来源标注（T3）：整份 deck 一个口径，与 PPT 脚注共用同一串
    source_note = note_for(deck.get('provenance'), as_of=subject.get('as_of'))
    wb = Workbook()
    wb.remove(wb.active)
    used = set()
    # 公式单元格的缓存值，按 sheet 名归集，保存后统一填进 <v>
    pending = {}

    ws = wb.create_sheet(_safe_sheet_name(_('Cover'), used))
    _write_cover(ws, deck)
    _autosize(ws)

    for sec in deck.get('sections') or []:
        kind = sec.get('type')
        if kind in ('cover', 'disclaimer'):
            continue          # cover 进首页，disclaimer 进末页
        sheet = wb.create_sheet(_safe_sheet_name(sec.get('title') or _(kind), used))
        if kind == 'table':
            entry = scales.get(sec.get('unit') or '')
            factor, label = entry if entry else (None, None)
            cached = _write_table(sheet, sec, factor, label, source_note)
            if cached:
                pending[sheet.title] = cached
        elif kind == 'chart':
            _write_chart(sheet, sec)
        elif kind == 'key_points':
            _write_key_points(sheet, sec)
        else:
            _write_text(sheet, sec)
        _autosize(sheet)

    ws = wb.create_sheet(_safe_sheet_name(_('Disclaimer'), used))
    _write_disclaimer(ws, deck)
    _autosize(ws)

    subject = deck.get('subject') or {}
    sid = re.sub(r'[^A-Za-z0-9._-]', '_', str(subject.get('id') or '')) or 'deck'
    day = str(subject.get('as_of') or deck.get('generated_at') or '')[:10].replace('-', '')
    name = filename or ('%s_%s.xlsx' % (sid, day or datetime.now().strftime('%Y%m%d')))

    outdir = out_dir or _MEDIA_DIR
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, name)
    # 打开时重算一遍：缓存值给不会算的读者，Excel 看到的永远是它自己算的结果
    wb.calculation.fullCalcOnLoad = True
    wb.save(path)
    _fill_formula_cache(path, pending)
    return path
