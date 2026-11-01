# ResearchDeck 契约 v1.0（T0a）

> 一句话：**一份 deck 描述「一份研究报告有什么内容」，不描述它长什么样。**
> PPT、Excel、引用三处消费同一份数据；非金融 edition 复用同一套渲染器。
>
> 反面先例：`_generate_ppt_file` 与 `generate_docx` 是两套互不相干的实现——
> 同一份数据要写两遍装配逻辑。契约就是为了让这种事不再发生第三次。

---

## 1. 信封（envelope）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | 固定 `"1.0"`；不等于该值即违规 |
| `doc_kind` | string | 是 | 文档种类，如 `research_report`；非空 |
| `generated_at` | string | 是 | ISO-8601 且带时区偏移，如 `2026-10-28T09:12:33+08:00` |
| `subject` | object | 是 | `{id, name, as_of}`；`id` 非空，`as_of` 为 `YYYY-MM-DD` 或空串 |
| `sections` | array | 是 | 非空，元素见 §2 |
| `provenance` | object | 是 | `{data_sources[], indicators[], model, evidence_hash}` |
| `disclaimer` | string | 是 | 非空，全文免责声明 |
| `ext` | object | 是 | 业务扩展位，见 §4 |

## 2. sections[].type — 封闭六值

**词表封闭，业务侧不得自定义。不够用就用 `text` 兜底，而不是加 type。**

| type | 必填字段 | 可选字段 | 用途 |
|---|---|---|---|
| `cover` | `title` | `subtitle` | 封面 |
| `key_points` | `title`, `items[]` | — | 要点列举；item = `{head, body?, ref?}` |
| `table` | `title`, `columns[]`, `rows[][]` | `unit`, `note`, `formula_cells[]` | 表格 |
| `chart` | `title`, `chart`, `categories[]`, `series[]` | `unit`, `note` | 图；series = `{name, values[]}` |
| `text` | `text` | `title` | 纯文本段 |
| `disclaimer` | `text` | — | 免责声明段 |

补充约束：

- `table.rows` 每行元素个数必须等于 `columns` 长度；单元格只能是 字符串 / 数值 / `null`。
- `table.formula_cells[]` = `{cell, formula, value?}`；`cell` 是单元格地址（如 `D8`），
  `formula` **必须是以 `=` 开头的公式字符串**——渲染器只负责写入，不做任何计算。
  `value` 可选，是算式的**结果**：写进单元格的缓存值（`<v>`），让读文件的一方
  （预览、校验、下游脚本）不必自己求值。它不改变「以公式为准」——Excel 打开后
  照常重算，`value` 只是给不会算的读者一个答案。给了就必须是有限数值。
- `chart.series[].values` 必须全是数值，且长度与 `categories` 一致。
- 带 `title` 的 type（`cover`/`key_points`/`table`/`chart`）title 必填非空；
  `text`/`disclaimer` 靠 `text` 承载内容。

### 表格在 xlsx 中的行列布局

`agent_matrix/export/xlsx.py` 把一个 `table` 段落写进独立 sheet 时的布局：

| 行 | 内容 |
|---|---|
| 第 1 行 | `section.title`（第 2 列附单位） |
| 第 2 行 | `columns[]` 表头 |
| 第 3 行起 | `rows[]`，逐行下移 |

装配侧按此推算 `formula_cells` 的地址——见
`plugins/stock_analysis/deck/valuation.py`：`rows[i]`（i 从 0 起）落在第 `i + 3` 行，
列字母由 `columns` 下标决定。

**改动这个布局必须同步改装配侧的推算**，否则公式会指向错误的单元格
（不会报错，只会算出一个看起来合理的错数——比崩掉更难发现）。

## 3. provenance

| 字段 | 说明 |
|---|---|
| `data_sources` | 实际取到数的数据源名列表（`["tushare","akshare"]`） |
| `indicators` | 本报告用到的指标名列表 |
| `model` | 参与生成的模型名；装配器不调 LLM 时为空串 |
| `evidence_hash` | 由 `compliance.compute_evidence_hash(data_sources, indicators, model)` 生成 |

## 4. ext — 业务扩展位

- **渲染器一律不读 `ext`。** 它只服务装配层与调试（回溯、排查、后续工单取数）。
- 金融侧的 `valuation` / `consensus` 等金融专有字段**只能出现在 `ext` 内**，
  契约主体不允许出现任何金融专字段名。
- `ext.gaps`：装配器登记的数据缺口类型列表（如 `["valuation"]`）。
- `ext` 内容是自由对象，校验器只要求它是 object。

## 5. 校验

```python
from agent_matrix.deck.schema import validate_deck

errs = validate_deck(deck)      # [] 即通过；非空为违规项清单（人类可读字符串）
```

覆盖四类畸形输入：缺字段 / 未知 type / 越界值（含 `schema_version` 越界、时间格式、
行宽不一致、公式不以 `=` 开头）/ 类型错。

## 6. 示例（真实样例，可直接作为实现参照）

```json
{
  "schema_version": "1.0",
  "doc_kind": "research_report",
  "generated_at": "2026-10-28T09:12:33+08:00",
  "subject": { "id": "600519.SH", "name": "贵州茅台", "as_of": "2026-10-27" },
  "sections": [
    { "type": "cover", "title": "贵州茅台 · 首次覆盖", "subtitle": "买入 · 目标价 1,805 元" },
    { "type": "key_points", "title": "投资结论", "items": [
      { "head": "高端白酒需求韧性", "body": "…", "ref": "verdict.reasons[0]" }
    ] },
    { "type": "table", "title": "主要财务指标", "columns": ["科目", "2025A", "2026E"],
      "rows": [ ["营业收入", "1,234.56", "1,388.00"] ], "unit": "亿元",
      "note": "来源：定期报告；2026E 为一致预期",
      "formula_cells": [ { "cell": "D8", "formula": "=D7*$B$3" } ] },
    { "type": "chart", "title": "毛利率趋势", "chart": "line",
      "categories": ["2022","2023","2024","2025"],
      "series": [ { "name": "毛利率", "values": [91.5,92.0,91.8,92.3] } ] },
    { "type": "disclaimer", "text": "…" }
  ],
  "provenance": {
    "data_sources": ["tushare","akshare"],
    "indicators": ["ma20","roe"],
    "model": "deepseek-v3",
    "evidence_hash": "a1b2c3d4e5f60718"
  },
  "disclaimer": "…",
  "ext": {
    "valuation": { "pe_ttm": 28.4, "pe_percentile_5y": 35.2,
                   "target_price": null, "method": "comparable" },
    "consensus": { "eps_mean": 68.4, "eps_min": 62.0, "eps_max": 74.5 }
  }
}
```

---

## 附：T0b 金融装配器字段映射表

装配器：`plugins/stock_analysis/deck/builder.py` · `build_deck(symbol, periods=8, report=None) -> dict`

`periods` 会先 clamp 到 `[1, 20]`（与 `fundamental_detail` 路由 `routes.py:648-649` 同口径），
再透传给 `gateway.get_fundamental`。

| 契约字段 | 来源函数 | 来源字段 / 说明 |
|---|---|---|
| `schema_version` | 常量 | `agent_matrix.deck.schema.SCHEMA_VERSION` = `"1.0"` |
| `doc_kind` | 常量 | `"research_report"` |
| `generated_at` | 装配器 | 本地时区 `datetime.now().astimezone()` ISO-8601（秒精度） |
| `subject.id` | 入参 | `build_deck(symbol=…)` 原样 |
| `subject.name` | `gateway.get_quote(symbol)` | `["name"]`；取不到 → `""` + 登记 `gaps: subject_name` |
| `subject.as_of` | `gateway.get_fundamental(symbol, periods)` | `income[].end_date` 最大值 → `YYYY-MM-DD`；无财报 → 当日 |
| `sections[cover]` | 装配器合成 | `title = "{name} · Research brief"`，`subtitle = "Data as of {as_of}"` |
| `sections[key_points].items` | `evidence.parse_structured_output(report)` | `reasons[]` → `{head, body:"", ref:"verdict.reasons[i]"}`；`signal/confidence` 进 `title`；无 `report` → 整段不产出 + `gaps: verdict` |
| `sections[text]`「Key financials」 | `evidence.digest_fundamental(fund, periods)` | 返回文本行列表，按 `\n` 合并为 `text`；空 → 不产出 + `gaps: digest` |
| `sections[table]`「Income statement」 | `gateway.get_fundamental` | `income[]`：`revenue`/`operate_profit`/`n_income`/`basic_eps` |
| `sections[table]`「Balance sheet」 | `gateway.get_fundamental` | `balance[]`：`total_assets`/`total_liab`/`total_equity` |
| `sections[table]`「Cash flow statement」 | `gateway.get_fundamental` | `cashflow[]`：`n_cashflow_act`/`n_cashflow_inv`/`n_cashflow_fin` |
| `sections[chart]`「Revenue and net income trend」 | `gateway.get_fundamental` | `income[]` 多期：`revenue` / `n_income`，按 `end_date` 升序；两列都齐的期才入图（不补 0），不足 2 期 → 整段不产出 + `gaps: chart`；数值保持「元」，不做单位换算 |
| 三表 `columns` / `rows` | 同上 | `columns = [_('Item')] + end_date 列`；数值千分位两位小数字符串，缺值 `""` |
| 三表 `unit` / `note` | 装配器常量 | `unit=_('CNY')`；`note=_('Source: periodic reports')` |
| `ext.valuation` | `valuation.pe_pb_percentile(symbol)` | `pe_ttm`/`pb`/`pe_percentile_5y`/`pb_percentile_5y`/`pe_min_5y`/`pe_max_5y`/`pb_min_5y`/`pb_max_5y`/`window_years`/`samples`/`source` **原样透传**（分位字段是 0–100 百分数，禁止二次换算）+ `target_price: null` + `method: "comparable"`。`source` 为实际供数方：主源 `tushare`，降级免费备源 `baidu` |
| `ext.consensus` | `gateway.get_consensus(symbol)` → `akshare_consensus` | `forecasts[0]`（最近预测年度）的 `eps_mean`/`eps_min`/`eps_max` |
| `ext.gaps` | 装配器 | 缺口类型列表：`fundamental` / `digest` / `chart` / `valuation` / `consensus` / `subject_name` / `verdict` |
| `provenance.data_sources` | `gateway.last_source(category)` / 估值返回值 `source` | 真实命中源名（`tushare` / `akshare_fundamental` / `akshare_consensus` / `baidu`）；gateway 未记录时退回静态兜底常量。估值分位不过 gateway，取 `pe_pb_percentile()` 返回的 `source`（主源 `tushare`，降级 `baidu`），不得硬编码 |
| `provenance.indicators` | 装配器常量 | 三表科目 + `pe_ttm`/`pb`/`eps` |
| `provenance.model` | 装配器 | 空串（T0b 不做任何 LLM 调用） |
| `provenance.evidence_hash` | `compliance.compute_evidence_hash(...)` | `(data_sources, indicators, model)` |
| `disclaimer` | `compliance.DEFAULT_DISCLAIMER` | 原样 |
| `sections[disclaimer].text` | `compliance.DEFAULT_DISCLAIMER` | 原样 |

### 估值分位的源选择

`ext.valuation` 由 `valuation.pe_pb_percentile()` 供数，主源 `tushare pro.daily_basic`，
主源不可用（token 缺失 / 无权限 / 接口失败 / 空序列）时降级免费备源 `akshare` 百度估值。

- **整条序列必须同源**：禁止「当前值取主源 + 历史序列取备源」混算；
- **不做双源平均或交叉校验**，否则同一标的两次装配可能得到两个分位；
- 两源分位口径统一由 `valuation._percentile`（`(series <= cur).mean() * 100`，round 1）计算；
- 已知差异：备源样本约 913 个交易日（主源约 1200），同一标的分位可能相差 1~3pct，
  该差异已登记在 `samples` 字段，消费方可据此判断；
- 两源皆不可用 → `ext.valuation` 不产出 + `ext.gaps: valuation`，deck 仍须合法。
  调用方不得把缺失当成「估值合理」渲染。

### 装配器红线

- 不渲染任何文件（模块内不得 import `pptx` / `openpyxl`）；
- 不调 LLM；不算 `target_price`（T1 做，口径可比公司法）；
- 不产出任何只为某个版式服务的字段（颜色 / 字号 / 页序一律不进 deck）；
- 数据缺失 → 该 section 整段不产出 + `ext.gaps` 登记，deck 仍须通过校验。

### 单位口径

deck 内金额一律存**原始值**，`unit` 如实标注其量纲（金融侧为 `CNY`，即元）。
呈现层的换算是渲染器的职责，由调用方经 `scale_by_unit` 传入
（金融侧 `{"CNY": (1e-8, _('CNY 100M'))}` 得亿元）。

- 装配器**不得**预先换算：同一份 deck 要同时供 PPT、Excel、引用消费，
  换算写死在装配层会让不同交付物口径打架；
- `table.unit` / `chart.unit` 必须如实反映 deck 内数值的量纲。**混合量纲的表
  （如估值表的 PE 倍数 / EPS 元每股 / 分位百分数）不得标 `CNY`**——渲染层按 unit
  查表缩放，标了 CNY 会把 PE 也除以 1e8（T1 实测踩过，已加回归测试
  `tests/test_export_xlsx.py::test_mixed_unit_table_not_scaled`）；
- **有数值的 section 就该标 unit，chart 也不例外**。趋势图的 series 同样是金额，
  不标会让同一份交付物里三表显示亿元、趋势表显示元（T2 实测踩过）。

### 文案与语言

deck 里的用户可见文案（封面、表名、科目名、单位、图例）一律走插件 i18n：
模块级只存英文源串，装配时调 `_()` 翻译，译文在 `plugins/stock_analysis/i18n/{en,zh-CN}.yml`，
由 `i18n.seed_plugin_translations()` 落库到 `i18n_strings` 后生效；未 seed 时回落到英文源串
（与插件其它文案同一机制，装配器不自行决定语言）。`ext` 内的业务值不做翻译。
