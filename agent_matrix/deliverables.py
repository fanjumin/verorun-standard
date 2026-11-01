# deliverables.py — 交付物中心（一份研报 = 一个条目，多种格式）
#
# 为什么不是「一堆文件」：客户拿到的是**一份研报**，不是三个孤立文件。所以列目录
# 以条目为单位，行内给 md / xlsx / pptx 三个下载；三种格式由同一份 deck 装配，
# 数字不会互相打架。
#
# 落盘：data/deliverables/<id>/<标的>_<日期>.<ext>
# 索引：data/deliverables/index.json —— 列表页只读它，不扫目录（扫目录会把
#       半写成的文件也列出来，且每次列表都要 stat 几百个 inode）。
#
# 与 data/media/temp 的区别：那边是 7 天清理的临时区，这边是客户交付物，
# 不自动清理。清理动作只能由人显式发起。
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from datetime import date, datetime

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(_PROJECT_ROOT, 'data', 'deliverables')
_INDEX = os.path.join(ROOT, 'index.json')

# 目录名即 id：只允许「日期-随机」，杜绝 ../ 之类的路径穿越
_ID_RE = re.compile(r'^\d{8}-[0-9a-f]{6}$')
# 同进程内的读改写锁（Flask 多线程；跨进程的场景本机形态下不存在）
_LOCK = threading.Lock()

FORMATS = ('md', 'xlsx', 'pptx')
# 可预览为 PDF 的格式：md 直接读文本，不需要转换
PREVIEW_FORMATS = ('xlsx', 'pptx')
# 预览缓存目录（条目内隐藏目录，不参与 formats/sizes 统计）
_PREVIEW_SUBDIR = '.preview'
# 网页表格预览的单元格总量上限：整本研报 20+ 个 sheet，全铺给前端既慢也没人看。
# 超了标 truncated，前端提示「只显示前 N 格」而不是静默截断。
_SHEET_CELL_BUDGET = 20000
# 表格在 xlsx 里的表头行数（第 1 行表名、第 2 行列头），与 deck/CONTRACT.md
# 「表格在 xlsx 中的行列布局」是同一个约定——改那边必须改这里。
_TABLE_HEADER_ROWS = 2
# deck 快照文件名：重生成在同一批数据上重渲染，不重新取数
DECK_FILE = 'deck.json'
# 正在转换中的 (id, fmt)；后台线程去重，避免同一份文件被同时转两次
_PREVIEW_INFLIGHT: set[tuple[str, str]] = set()
# 转换失败过的 (id, fmt)：不反复重试（没装 LibreOffice 时每次点都等一轮没意义）
_PREVIEW_FAILED: set[tuple[str, str]] = set()
_PREVIEW_LOCK = threading.Lock()


def _safe_filename(name: str) -> str:
    """文件名合法化：去掉路径分隔符与控制字符，截断到 80。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', str(name or '').strip())
    return (cleaned or 'deliverable')[:80]


def _load_index() -> list:
    try:
        with open(_INDEX, encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        # 索引损坏/不存在 → 空列表。不抛：列表页空着比整页 500 好，
        # 已落盘的文件仍在，可人工恢复。
        return []


def _save_index(items: list) -> None:
    os.makedirs(ROOT, exist_ok=True)
    tmp = _INDEX + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(items, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, _INDEX)          # 原子替换：写一半断电不会留下半份索引


def new_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime('%Y%m%d')
    return '%s-%s' % (stamp, secrets.token_hex(3))


def list_items(limit: int = 200) -> list:
    """按创建时间倒序。索引只存相对文件名，列表页拿不到服务器绝对路径。"""
    items = _load_index()
    items.sort(key=lambda it: str(it.get('created_at') or ''), reverse=True)
    return items[:max(1, int(limit))]


def get_item(did: str) -> dict | None:
    if not _ID_RE.match(str(did or '')):
        return None
    for item in _load_index():
        if item.get('id') == did:
            return item
    return None


def delete_item(did: str) -> bool:
    """删除一个交付物条目：索引移除 + 目录移入 .trash（不物理删除，可人工恢复）。

    返回 False 表示条目不存在；调用方回 404，不静默成功。
    """
    if not _ID_RE.match(str(did or '')):
        return False
    with _LOCK:
        items = _load_index()
        kept = [it for it in items if it.get('id') != did]
        if len(kept) == len(items):
            return False
        _save_index(kept)
    src = os.path.join(ROOT, did)
    if os.path.isdir(src):
        trash = os.path.join(ROOT, '.trash')
        os.makedirs(trash, exist_ok=True)
        try:
            os.replace(src, os.path.join(trash, did))
        except OSError:
            # 目录移动失败不回滚索引：索引已移除，文件留在原处可人工处理
            pass
    return True


def file_path(item: dict, fmt: str) -> str | None:
    """条目 + 格式 → 绝对路径；不存在返回 None（调用方回 404）。"""
    rel = (item.get('files') or {}).get(fmt)
    if not rel:
        return None
    path = os.path.join(ROOT, item['id'], rel)
    # 二次确认没有跑出交付物目录（rel 由本模块写入，仍做一次防御）
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(
            os.path.join(ROOT, item['id'])):
        return None
    return path if os.path.isfile(path) else None


def create(*, title: str, subject_id: str = '', subject_name: str = '',
           narrative: str | None = None, deck: dict | None = None,
           scale_by_unit: dict | None = None, source: str = 'research',
           assumptions: dict | None = None, extra: dict | None = None) -> dict:
    """登记一份交付物，返回索引条目。

    deck 给出时渲染 md / xlsx / pptx 三种格式（同一份 deck，数字同源）；
    不给时只落 md（智能会话的产物——它本来就是一段 markdown，没有结构化数据
    可渲染，硬给它套一个三表模板反而是在编）。

    deck 会同时**原样存一份 deck.json**：交付物的「重新生成」要在同一批数据上
    改假设重渲染，绝不能重新取数——取数有漂移，客户手里那份报告的正文写着
    上一批数字，重生成却换了一批，就成了自相矛盾的文件。存快照是唯一确定的
    做法（代价是体积，一份 deck 几十 KB，可以忽略）。

    assumptions 是可调假设（如 target_pe / signal），随条目持久化，重生成时
    作为起点读回；不含假设的条目（会话产物）不支持重生成。
    """
    did = new_id()
    item_dir = os.path.join(ROOT, did)
    os.makedirs(item_dir, exist_ok=True)

    sid = re.sub(r'[^A-Za-z0-9._-]', '_', str(subject_id or '')) or 'report'
    day = datetime.now().strftime('%Y%m%d')
    base = _safe_filename('%s_%s' % (sid, day))
    files: dict[str, str] = {}

    if deck:
        from .export.markdown import render_markdown
        from .export.pptx import render_pptx
        from .export.xlsx import render_xlsx

        scales = scale_by_unit or {}
        files['md'] = os.path.basename(
            render_markdown(deck, out_dir=item_dir, filename=base + '.md',
                            scale_by_unit=scales))
        files['xlsx'] = os.path.basename(
            render_xlsx(deck, out_dir=item_dir, filename=base + '.xlsx',
                        scale_by_unit=scales))
        files['pptx'] = os.path.basename(
            render_pptx(deck, out_dir=item_dir, filename=base + '.pptx',
                        scale_by_unit=scales))
        # 快照：重生成只改假设，不重新取数（理由见 docstring）
        with open(os.path.join(item_dir, DECK_FILE), 'w',
                  encoding='utf-8', newline='\n') as fh:
            json.dump(deck, fh, ensure_ascii=False)
        provenance = deck.get('provenance') or {}
        evidence_hash = str(provenance.get('evidence_hash') or '')
        gaps = list((deck.get('ext') or {}).get('gaps') or [])
        as_of = str((deck.get('subject') or {}).get('as_of') or '')
    else:
        text = str(narrative or '').strip()
        if not text:
            raise ValueError('empty content')
        name = base + '.md'
        with open(os.path.join(item_dir, name), 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text + '\n')
        files['md'] = name
        evidence_hash, gaps, as_of = '', [], ''

    item = {
        'id': did,
        'title': _safe_filename(title) if title else base,
        'subject_id': str(subject_id or ''),
        'subject_name': str(subject_name or ''),
        'source': source,             # research | chat
        'as_of': as_of,
        'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'formats': [f for f in FORMATS if f in files],
        'files': files,
        'sizes': {f: os.path.getsize(os.path.join(item_dir, p))
                  for f, p in files.items()},
        'evidence_hash': evidence_hash,
        'gaps': gaps,
        # 可调假设：重生成时读回作为起点。会话产物没有，字段干脆不出现
        'assumptions': dict(assumptions or {}) if deck else {},
    }
    if extra:
        item.update({k: v for k, v in extra.items() if k not in item})

    with _LOCK:
        items = _load_index()
        items.append(item)
        _save_index(items)

    # 预览转换放后台：一次转 PDF 热 15~27s、冷 79s，不能挂在登记请求上
    if deck:
        for fmt in PREVIEW_FORMATS:
            if fmt in files:
                start_preview(item, fmt)
    return item


def deck_path(item: dict) -> str | None:
    """条目对应的 deck 快照路径；没有（会话产物）返回 None。"""
    path = os.path.join(ROOT, str(item.get('id') or ''), DECK_FILE)
    return path if os.path.isfile(path) else None


def load_deck(did: str) -> dict | None:
    """读回 deck 快照。条目不存在或没有快照 → None。"""
    item = get_item(did)
    if not item:
        return None
    path = deck_path(item)
    if not path:
        return None
    try:
        with open(path, encoding='utf-8') as fh:
            deck = json.load(fh)
    except (OSError, ValueError):
        return None
    return deck if isinstance(deck, dict) else None


def read_sheet(item: dict, budget: int = _SHEET_CELL_BUDGET, deck: dict | None = None) -> dict | None:
    """把 xlsx 读成前端可直接渲染的结构：值 + 轻重样式。

    为什么不让前端自己解析 xlsx：那要求前端能算公式。而公式的结果现在已经由
    渲染层写进单元格缓存值（见 export/xlsx.py:_fill_formula_cache），这里用
    data_only 一读就是对的数——前端因此不必引入任何表格引擎，也不必装
    LibreOffice 转 PDF。

    只给渲染必需的东西：值、数字格式、是否加粗、列宽、合并区。行高与主题色
    一律不透传——研报表格不靠它们传信息，透传只会让前端去追 Excel 的版式。

    deck 给出（或条目自带快照）时，给 deck 表格里的数值格附上 e=[section, row,
    col] 坐标：前端凭它把改动精确回写到 deck，从而三格式一起重渲染。没有快照
    的条目（会话产物、手工修订版）一律只读——没有可回写的地方。
    """
    path = file_path(item, 'xlsx')
    if not path:
        return None
    try:
        from openpyxl import load_workbook

        wb = load_workbook(path, data_only=True)
    except Exception:
        return None

    if deck is None:
        deck = load_deck(str(item.get('id') or ''))
    # sheet 顺序 = cover + 「非 cover/disclaimer 的 sections」+ disclaimer，
    # 记下原索引，前端回写时才能落回正确的那一段
    editable = [(i, s) for i, s in enumerate((deck or {}).get('sections') or [])
                if s.get('type') not in ('cover', 'disclaimer')]

    sheets, left, truncated = [], budget, False
    try:
        for pos, ws in enumerate(wb.worksheets):
            if left <= 0:
                truncated = True
                break

            column = editable[pos - 1] if 0 <= pos - 1 < len(editable) else (None, None)
            sec_index, sec = column

            rows, last_filled = [], -1
            for idx, row in enumerate(ws.iter_rows()):
                cells = []
                for ci, cell in enumerate(row):
                    payload = _cell_payload(cell)
                    if payload is not None:
                        left -= 1
                        _mark_editable(payload, sec, sec_index, idx - _TABLE_HEADER_ROWS, ci)
                    cells.append(payload)
                rows.append(cells)
                if any(c is not None for c in cells):
                    last_filled = idx
                if left <= 0:
                    truncated = True
                    break
            # 末尾的整行空白是格式留下的，不是内容，剪掉省带宽
            rows = rows[:last_filled + 1]

            widths = {}
            for letter, dim in (ws.column_dimensions or {}).items():
                width = getattr(dim, 'width', None)
                if width:
                    widths[letter] = round(float(width), 1)

            sheets.append({
                'name': ws.title,
                'rows': rows,
                'widths': widths,
                'merges': [str(rng) for rng in ws.merged_cells.ranges],
            })
    finally:
        wb.close()

    return {'sheets': sheets, 'cell_budget': budget, 'truncated': truncated}


def _mark_editable(payload: dict, sec: dict | None, sec_index, row_index: int, col_index: int) -> None:
    """给 deck 表格里的数值格打上回写坐标，并附上呈现缩放比。

    只认数值：改文字会让「叙述与数字」脱钩，而本系统的全部价值就是两者一致；
    文字要改就去改 deck 的 text 段，那是另一条路。

    缩放比 k 是必须的：表格显示的是亿元（deck 存元），用户在屏幕上填 950，
    回写时要变成 9.5e10；不换算就是把「950 亿」写成「950 元」。k 由「屏幕上
    的值 ÷ deck 里的值」现算——不依赖条目上有没有记过 scale_by_unit，老条目
    一样能算准。
    """
    if not sec or sec.get('type') != 'table' or sec_index is None:
        return
    if row_index < 0 or col_index < 0:
        return
    rows = sec.get('rows') or []
    if row_index >= len(rows):
        return
    if col_index >= len(rows[row_index]):
        return
    value = rows[row_index][col_index]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    shown = payload.get('v')
    if isinstance(shown, bool) or not isinstance(shown, (int, float)):
        return
    # deck 里是 0 时反推不出比例（0 乘任何数都是 0），这种格子宁可不给改
    if not value:
        return
    factor = float(shown) / float(value)
    if not (1e-12 < abs(factor) < 1e12):
        return

    payload['e'] = [sec_index, row_index, col_index]
    if abs(factor - 1.0) > 1e-12:
        payload['k'] = factor


def _cell_payload(cell):
    """单个单元格 → {v, t?, n?, b?}；空且无样式的格子返回 None。

    日期转 ISO 串：JSON 里没有日期类型，留 datetime 会在序列化时炸掉整个响应。
    """
    value = cell.value
    if value is None:
        return None

    if isinstance(value, datetime):
        payload = {'v': value.isoformat()[:10], 't': 's'}
    elif isinstance(value, date):
        payload = {'v': value.isoformat(), 't': 's'}
    else:
        payload = {'v': value}
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            payload['t'] = 'n'

    fmt = cell.number_format
    if fmt and fmt != 'General':
        payload['n'] = fmt
    if cell.font is not None and cell.font.bold:
        payload['b'] = 1
    return payload


def _coerce_number(value):
    """前端传来的值 → float；不是有限数就返回 None（调用方拒绝，不猜）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip().replace(',', '')
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if number != number or number in (float('inf'), float('-inf')):
        return None
    return number


def _col_index(letters: str) -> int:
    """A → 0，B → 1，AA → 26。"""
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return index - 1


# 只认「两个单元格 + 四则运算」：这是估值目标价（=B7*B8）的全部形态。
# 更复杂的公式一律不猜——猜错就是给了个假数，比不给更糟。
_FORMULA_BINARY = re.compile(r'^=\s*([A-Z]{1,3}\d+)\s*([+\-*/])\s*([A-Z]{1,3}\d+)\s*$')


def _cell_number(rows, addr: str):
    match = re.match(r'^([A-Z]{1,3})(\d+)$', addr)
    if not match:
        return None
    col = _col_index(match.group(1))
    # 地址 1-based 且表格前有 _TABLE_HEADER_ROWS 行表头
    row = int(match.group(2)) - 1 - _TABLE_HEADER_ROWS
    if not (0 <= row < len(rows)) or col >= len(rows[row]):
        return None
    value = rows[row][col]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _recalc_formulas(section: dict) -> None:
    """按被改动的单元格重算该表的公式缓存值（只更新 value，不动 formula）。"""
    rows = section.get('rows') or []
    for item in section.get('formula_cells') or []:
        match = _FORMULA_BINARY.match(str(item.get('formula') or ''))
        if not match:
            continue
        left = _cell_number(rows, match.group(1))
        right = _cell_number(rows, match.group(3))
        if left is None or right is None:
            continue
        op = match.group(2)
        if op == '/' and right == 0:
            continue
        item['value'] = {'*': left * right, '/': left / right,
                         '+': left + right, '-': left - right}[op]


def apply_cell_edits(deck: dict, edits: list, limit: int = 200) -> dict:
    """把前端提交的单元格改动写回 deck（原地改并返回）。

    只允许改 deck 表格里**原本就是数值**的格子：这样三格式重渲染后仍然同源——
    改了收入假设，md 的表、pptx 的表一起变，不会出现「正文说一套、表格是另一套」。
    标签列（原本是文字的格子）一律拒收：把标签改成数字，语义就乱了。

    改动落在哪些表，就只重算那些表的公式缓存值；没动的表一个字节都不碰。
    """
    if not isinstance(edits, list) or not edits:
        raise ValueError('no edits')
    if len(edits) > limit:
        raise ValueError('too many edits')

    sections = deck.get('sections') or []
    touched = set()
    for raw in edits:
        if not isinstance(raw, dict):
            raise ValueError('bad edit')
        coord = [raw.get('section'), raw.get('row'), raw.get('col')]
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in coord):
            raise ValueError('bad coordinate')
        si, ri, ci = coord
        if si >= len(sections):
            raise ValueError('section out of range')
        section = sections[si]
        if section.get('type') != 'table':
            raise ValueError('not a table')
        rows = section.get('rows') or []
        if ri >= len(rows) or ci >= len(rows[ri]):
            raise ValueError('cell out of range')
        if isinstance(rows[ri][ci], bool) or not isinstance(rows[ri][ci], (int, float)):
            raise ValueError('cell is not numeric')

        number = _coerce_number(raw.get('value'))
        if number is None:
            raise ValueError('value is not a number')
        # 屏幕上填的是缩放后的数（亿元），deck 存的是原值（元）。不换算的话，
        # 用户写 950 会被存成 950 元——差了八个数量级。
        factor = raw.get('k')
        if factor is not None:
            factor = _coerce_number(factor)
            if factor is None or not (1e-12 < abs(factor) < 1e12):
                raise ValueError('bad scale factor')
            number = number / factor
        rows[ri][ci] = number
        touched.add(si)

    for si in touched:
        _recalc_formulas(sections[si])
    return deck


def preview_path(item: dict, fmt: str) -> str | None:
    """**已缓存**的预览 PDF 路径；没转过 / 比源文件旧 → None。不触发转换。"""
    if fmt not in PREVIEW_FORMATS:
        return None
    src = file_path(item, fmt)
    if not src:
        return None
    out = os.path.join(ROOT, item['id'], _PREVIEW_SUBDIR, fmt + '.pdf')
    if os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(src):
        return out
    return None


def _convert_preview(item: dict, fmt: str) -> None:
    """后台线程体：转一次 PDF，原子落盘，失败静默（预览缺了不影响文件本身）。"""
    from .export import preview as _preview   # 懒导入：导出层依赖较重，列表页用不到

    key = (str(item.get('id')), fmt)
    try:
        src = file_path(item, fmt)
        if not src:
            return
        out_dir = os.path.join(ROOT, item['id'], _PREVIEW_SUBDIR)
        pdf = _preview.convert_to_pdf(src, out_dir)
        # 以 fmt 命名（同条目三种格式各自一份），覆盖旧缓存
        target = os.path.join(out_dir, fmt + '.pdf')
        if os.path.abspath(pdf) != os.path.abspath(target):
            os.replace(pdf, target)
    except Exception:
        # 没装 LibreOffice / 文件损坏 / 超时：预览不可用，但交付物本体是好的
        with _PREVIEW_LOCK:
            _PREVIEW_FAILED.add(key)
    finally:
        with _PREVIEW_LOCK:
            _PREVIEW_INFLIGHT.discard(key)


def start_preview(item: dict, fmt: str) -> bool:
    """确保预览转换已启动；返回是否**新**起了一个转换（已在跑/已完成 → False）。"""
    if preview_path(item, fmt):
        return False
    key = (str(item.get('id')), fmt)
    with _PREVIEW_LOCK:
        if key in _PREVIEW_INFLIGHT:
            return False
        _PREVIEW_INFLIGHT.add(key)
    threading.Thread(target=_convert_preview, args=(item, fmt), daemon=True).start()
    return True


def preview_state(item: dict, fmt: str) -> str:
    """预览状态：ready（有缓存）/ converting（转换中）/ unavailable（转不了）。

    路由据此回 200 PDF、202 请轮询、503 请直接下载——三者对前端的处理完全不同，
    不能都回一个 404 让前端猜。
    """
    if fmt not in PREVIEW_FORMATS or fmt not in (item.get('formats') or []):
        return 'unavailable'
    if preview_path(item, fmt):
        return 'ready'
    key = (str(item.get('id')), fmt)
    with _PREVIEW_LOCK:
        if key in _PREVIEW_INFLIGHT:
            return 'converting'
        if key in _PREVIEW_FAILED:
            return 'unavailable'
    from .export import preview as _preview
    if not _preview.available():
        return 'unavailable'
    return 'converting' if start_preview(item, fmt) else 'unavailable'
