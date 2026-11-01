# charts.py — pptx 原生图表（T4）
#
# 职责边界（越界即返工）：
#   - 只认 `categories` / `series`，不认识任何业务含义，不做数值换算与格式化
#     （换算由 pptx.py 按 section.unit 做完后传进来）；
#   - **不造数**：values 里的 None 原样留空，绝不补 0、绝不插值；
#   - **不静默降级**：未知 chart 类型抛 ValueError，而不是画一个看不出问题的空图。
#
# 为什么不用 matplotlib：仓库已在 PDF 导出模块记录过重二进制依赖在无 AVX 机器
# 触发 SIGILL 的教训。pptx.chart 是零新增依赖，且图表在 PowerPoint 里可编辑
# （右键「编辑数据」改完图形联动）——券商交付物里这条比视觉精度更重要。
from __future__ import annotations

from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.util import Pt

from . import layouts

# 契约首批只认这三种。要加第四种应先改契约，不是在这里加分支。
_CHART_TYPES = {
    'line': XL_CHART_TYPE.LINE,
    'bar': XL_CHART_TYPE.COLUMN_CLUSTERED,
    'stacked_bar': XL_CHART_TYPE.COLUMN_STACKED,
}


def supported_types():
    return tuple(sorted(_CHART_TYPES))


def build_chart(slide, spec, left, top, width, height):
    """在 slide 上画一个原生图表，返回 chart 对象。

    spec  {chart, categories, series:[{name, values}]}——values 须已完成呈现层换算
    （本模块不做换算，避免与 pptx.py 的 scale_by_unit 出现第二套口径）。

    无数据（categories 或 series 为空）返回 None，由调用方降级；
    未知 chart 类型抛 ValueError——静默画空图比报错更难发现。
    """
    kind = spec.get('chart')
    categories = list(spec.get('categories') or [])
    series = list(spec.get('series') or [])
    if not categories or not series:
        return None

    if kind not in _CHART_TYPES:
        raise ValueError('unsupported chart type %r (supported: %s)'
                         % (kind, ', '.join(supported_types())))

    data = CategoryChartData()
    data.categories = [str(c) for c in categories]
    for s in series:
        values = list(s.get('values') or [])
        # 长度不足不补：缺的期就是缺的，补 0 等于编数据
        padded = values + [None] * (len(categories) - len(values))
        data.add_series(str(s.get('name') or ''), tuple(padded[:len(categories)]))

    frame = slide.shapes.add_chart(_CHART_TYPES[kind], left, top, width, height, data)
    chart = frame.chart

    # 页面标题已经承载了「这是什么图」，图表自带标题只会重复
    chart.has_title = False
    chart.has_legend = len(series) > 1
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(10)
        chart.legend.font.color.rgb = layouts.MUTED
        chart.legend.font.name = layouts.FONT

    chart.font.size = Pt(10)
    chart.font.color.rgb = layouts.MUTED
    chart.font.name = layouts.FONT

    _apply_theme(chart)
    return chart


def _apply_theme(chart):
    """深色底上的图表：不设会是一块白板。失败不改图表内容——样式不是数据。"""
    try:
        chart.chart_area.format.fill.solid()
        chart.chart_area.format.fill.fore_color.rgb = layouts.BG
        chart.chart_area.line.color.rgb = layouts.GRID
    except Exception:
        pass

    for axis in (chart.category_axis, chart.value_axis):
        if axis is None:
            continue
        try:
            axis.format.line.color.rgb = layouts.GRID
            axis.tick_labels.font.size = Pt(9)
            axis.tick_labels.font.color.rgb = layouts.MUTED
            axis.tick_labels.font.name = layouts.FONT
        except Exception:
            pass

    try:
        chart.value_axis.has_major_gridlines = True
        gl = chart.value_axis.major_gridlines
        gl.format.line.color.rgb = layouts.GRID
    except Exception:
        pass

    # 折线/柱体取主题色，避免默认的 Office 蓝与页面配色打架
    try:
        for i, plot in enumerate(chart.plots):
            plot.gap_width = 60
            for j, ser in enumerate(plot.series):
                color = layouts.ACCENT if j == 0 else layouts.SERIES_COLORS[
                    (i + j) % len(layouts.SERIES_COLORS)]
                ser.format.fill.solid()
                ser.format.fill.fore_color.rgb = color
                if chart.chart_type == XL_CHART_TYPE.LINE:
                    ser.format.line.color.rgb = color
                    ser.format.line.width = Pt(2.25)
    except Exception:
        pass
