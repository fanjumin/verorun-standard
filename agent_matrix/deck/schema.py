# schema.py — ResearchDeck 文档契约（T0a）
#
# 定位：通用外壳 + 业务扩展位（ext）。PPT / Excel / 引用三处消费同一份数据，
# 非金融 edition 复用同一套渲染器。
#
# 边界（写死，改前先过评审）：
#   1. 契约只描述「内容与结构」，不描述「呈现」——配色、页序、字号一律不进契约；
#   2. sections[].type 是封闭六值词表，业务侧不得自定义；不够用就用 text 兜底，
#      而不是加 type；
#   3. ext 是业务自留地（金融的 valuation/consensus 等），渲染器一律不读，
#      仅供装配层与调试使用；
#   4. 不为「未来可能的 type」预留占位。
#
# 不先定契约的先例：_generate_ppt_file 与 generate_docx 是两套互不相干的实现，
# 同一份数据要写两遍装配逻辑。
from __future__ import annotations

import re

SCHEMA_VERSION = "1.0"

# 封闭词表：首批只开放六个。新增需改这里 + CONTRACT.md + 测试。
SECTION_TYPES = ("cover", "key_points", "table", "chart", "text", "disclaimer")

# 信封字段（缺一即违规）
ENVELOPE_FIELDS = (
    "schema_version",
    "doc_kind",
    "generated_at",
    "subject",
    "sections",
    "provenance",
    "disclaimer",
    "ext",
)

# 需要 title 的 type（text/disclaimer 靠 text 承载内容，title 可选）
_TITLED_TYPES = ("cover", "key_points", "table", "chart")

_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_str(v) -> bool:
    return isinstance(v, str)


def _is_num(v) -> bool:
    """数值：int/float，不含 bool（bool 是 int 子类，会被静默当 1/0）。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_cell(v) -> bool:
    """表格单元格：字符串 / 数值 / None（None 表示该格无数据）。"""
    return v is None or _is_str(v) or _is_num(v)


def validate_deck(deck) -> list[str]:
    """校验一个 ResearchDeck。

    返回违规项清单（人类可读字符串）；空列表 = 通过。不做异常抛出，
    调用方自行决定是否 raise。
    """
    err: list[str] = []

    if not isinstance(deck, dict):
        return ["deck: expected object, got %s" % type(deck).__name__]

    for field in ENVELOPE_FIELDS:
        if field not in deck:
            err.append("missing field: %s" % field)

    if "schema_version" in deck:
        sv = deck["schema_version"]
        if not _is_str(sv) or sv != SCHEMA_VERSION:
            err.append("schema_version: %r not supported (expected %r)"
                       % (sv, SCHEMA_VERSION))

    if "doc_kind" in deck:
        dk = deck["doc_kind"]
        if not _is_str(dk) or not dk.strip():
            err.append("doc_kind: expected non-empty string, got %r" % (dk,))

    if "generated_at" in deck:
        ga = deck["generated_at"]
        if not _is_str(ga) or not _ISO_RE.match(ga):
            err.append("generated_at: expected ISO-8601 datetime with offset, got %r"
                       % (ga,))

    if "subject" in deck:
        _validate_subject(deck["subject"], err)

    if "sections" in deck:
        sections = deck["sections"]
        if not isinstance(sections, list):
            err.append("sections: expected list, got %s" % type(sections).__name__)
        elif not sections:
            err.append("sections: must not be empty")
        else:
            for idx, sec in enumerate(sections):
                _validate_section(idx, sec, err)

    if "provenance" in deck:
        _validate_provenance(deck["provenance"], err)

    if "disclaimer" in deck:
        d = deck["disclaimer"]
        if not _is_str(d) or not d.strip():
            err.append("disclaimer: expected non-empty string, got %r" % (d,))

    if "ext" in deck:
        if not isinstance(deck["ext"], dict):
            err.append("ext: expected object, got %s" % type(deck["ext"]).__name__)

    return err


# ── 分部校验 ──────────────────────────────────────────────────────────────

def _validate_subject(subject, err: list[str]) -> None:
    if not isinstance(subject, dict):
        err.append("subject: expected object, got %s" % type(subject).__name__)
        return
    sid = subject.get("id")
    if not _is_str(sid) or not sid.strip():
        err.append("subject.id: expected non-empty string, got %r" % (sid,))
    name = subject.get("name")
    if not _is_str(name):
        err.append("subject.name: expected string, got %r" % (name,))
    as_of = subject.get("as_of")
    if not _is_str(as_of):
        err.append("subject.as_of: expected string, got %r" % (as_of,))
    elif as_of and not _DATE_RE.match(as_of):
        err.append("subject.as_of: expected YYYY-MM-DD or empty, got %r" % (as_of,))


def _validate_provenance(prov, err: list[str]) -> None:
    if not isinstance(prov, dict):
        err.append("provenance: expected object, got %s" % type(prov).__name__)
        return
    for key in ("data_sources", "indicators"):
        val = prov.get(key)
        if not isinstance(val, list):
            err.append("provenance.%s: expected list, got %s"
                       % (key, type(val).__name__))
        elif not all(_is_str(x) for x in val):
            err.append("provenance.%s: all items must be strings" % key)
    model = prov.get("model")
    if not _is_str(model):
        err.append("provenance.model: expected string, got %r" % (model,))
    eh = prov.get("evidence_hash")
    if not _is_str(eh) or not eh.strip():
        err.append("provenance.evidence_hash: expected non-empty string, got %r" % (eh,))


def _validate_section(idx: int, sec, err: list[str]) -> None:
    """校验单个 section。前缀统一 sections[i]，便于装配层定位。"""
    pre = "sections[%d]" % idx

    if not isinstance(sec, dict):
        err.append("%s: expected object, got %s" % (pre, type(sec).__name__))
        return

    stype = sec.get("type")
    if stype is None:
        err.append("%s.type: missing (allowed: %s)" % (pre, list(SECTION_TYPES)))
        return
    if stype not in SECTION_TYPES:
        err.append("%s.type: %r is not a closed-vocabulary type (allowed: %s)"
                   % (pre, stype, list(SECTION_TYPES)))
        return

    if stype in _TITLED_TYPES:
        title = sec.get("title")
        if not _is_str(title) or not title.strip():
            err.append("%s.title: required non-empty string for type=%s" % (pre, stype))

    handler = {
        "cover": _validate_cover,
        "key_points": _validate_key_points,
        "table": _validate_table,
        "chart": _validate_chart,
        "text": _validate_text,
        "disclaimer": _validate_text,
    }[stype]
    handler(pre, sec, err)


def _validate_cover(pre: str, sec: dict, err: list[str]) -> None:
    sub = sec.get("subtitle")
    if sub is not None and not _is_str(sub):
        err.append("%s.subtitle: expected string, got %r" % (pre, sub))


def _validate_key_points(pre: str, sec: dict, err: list[str]) -> None:
    items = sec.get("items")
    if not isinstance(items, list):
        err.append("%s.items: expected list, got %s" % (pre, type(items).__name__))
        return
    if not items:
        err.append("%s.items: must not be empty" % pre)
        return
    for i, item in enumerate(items):
        ip = "%s.items[%d]" % (pre, i)
        if not isinstance(item, dict):
            err.append("%s: expected object, got %s" % (ip, type(item).__name__))
            continue
        head = item.get("head")
        if not _is_str(head) or not head.strip():
            err.append("%s.head: expected non-empty string, got %r" % (ip, head))
        body = item.get("body")
        if body is not None and not _is_str(body):
            err.append("%s.body: expected string, got %r" % (ip, body))
        ref = item.get("ref")
        if ref is not None and not _is_str(ref):
            err.append("%s.ref: expected string, got %r" % (ip, ref))


def _validate_table(pre: str, sec: dict, err: list[str]) -> None:
    columns = sec.get("columns")
    if not isinstance(columns, list) or not columns:
        err.append("%s.columns: expected non-empty list" % pre)
        columns = None
    elif not all(_is_str(c) for c in columns):
        err.append("%s.columns: all items must be strings" % pre)
        columns = None

    rows = sec.get("rows")
    if not isinstance(rows, list) or not rows:
        err.append("%s.rows: expected non-empty list of rows" % pre)
    elif columns is not None:
        for i, row in enumerate(rows):
            if not isinstance(row, list):
                err.append("%s.rows[%d]: expected list, got %s"
                           % (pre, i, type(row).__name__))
                continue
            if len(row) != len(columns):
                err.append("%s.rows[%d]: %d cells, %d columns"
                           % (pre, i, len(row), len(columns)))
            for cell in row:
                if not _is_cell(cell):
                    err.append("%s.rows[%d]: cell %r is not string/number/null"
                               % (pre, i, cell))
                    break

    unit = sec.get("unit")
    if unit is not None and not _is_str(unit):
        err.append("%s.unit: expected string, got %r" % (pre, unit))
    note = sec.get("note")
    if note is not None and not _is_str(note):
        err.append("%s.note: expected string, got %r" % (pre, note))

    fc = sec.get("formula_cells")
    if fc is None:
        return
    if not isinstance(fc, list):
        err.append("%s.formula_cells: expected list, got %s"
                   % (pre, type(fc).__name__))
        return
    for i, cell in enumerate(fc):
        cp = "%s.formula_cells[%d]" % (pre, i)
        if not isinstance(cell, dict):
            err.append("%s: expected object, got %s" % (cp, type(cell).__name__))
            continue
        addr = cell.get("cell")
        if not _is_str(addr) or not addr.strip():
            err.append("%s.cell: expected non-empty string (e.g. D8), got %r"
                       % (cp, addr))
        formula = cell.get("formula")
        # 渲染器只写公式字符串，不做计算 —— 不以 "=" 开头者一律拒收
        if not _is_str(formula) or not formula.startswith("="):
            err.append("%s.formula: expected string starting with '=', got %r"
                       % (cp, formula))
        # value 可选：算式的结果值，写进单元格缓存（<v>）。没有它，读文件的一方
        # 要自己求值；有它，openpyxl(data_only=True) 直接读到数。类型必须严格，
        # 字符串混进来会被 Excel 当成文本，反而破坏公式。
        cached = cell.get("value")
        if cached is not None:
            # NaN/inf 一并拒：它们写进 <v> 会让 Excel 报 #NUM!（与 xlsx 渲染层同一条线）
            bad = (not _is_num(cached)
                   or cached != cached
                   or cached in (float("inf"), float("-inf")))
            if bad:
                err.append("%s.value: expected finite number or null, got %r"
                           % (cp, cached))


def _validate_chart(pre: str, sec: dict, err: list[str]) -> None:
    kind = sec.get("chart")
    if not _is_str(kind) or not kind.strip():
        err.append("%s.chart: expected non-empty string (e.g. line), got %r"
                   % (pre, kind))

    categories = sec.get("categories")
    if not isinstance(categories, list) or not categories:
        err.append("%s.categories: expected non-empty list" % pre)
        categories = None

    series = sec.get("series")
    if not isinstance(series, list) or not series:
        err.append("%s.series: expected non-empty list" % pre)
        return
    for i, s in enumerate(series):
        sp = "%s.series[%d]" % (pre, i)
        if not isinstance(s, dict):
            err.append("%s: expected object, got %s" % (sp, type(s).__name__))
            continue
        name = s.get("name")
        if not _is_str(name) or not name.strip():
            err.append("%s.name: expected non-empty string, got %r" % (sp, name))
        values = s.get("values")
        if not isinstance(values, list) or not values:
            err.append("%s.values: expected non-empty list" % sp)
            continue
        if not all(_is_num(v) for v in values):
            err.append("%s.values: all items must be numbers" % sp)
        elif categories is not None and len(values) != len(categories):
            err.append("%s.values: %d values, %d categories"
                       % (sp, len(values), len(categories)))


def _validate_text(pre: str, sec: dict, err: list[str]) -> None:
    text = sec.get("text")
    if not _is_str(text) or not text.strip():
        err.append("%s.text: expected non-empty string, got %r" % (pre, text))
