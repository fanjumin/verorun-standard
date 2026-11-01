# provenance.py — 来源标注（T3）
#
# 职责边界（越界即返工）：
#   - 只把 deck.provenance 里**已经存在**的信息拼成一行人可读的标注，
#     不查库、不联网、不推断「这个数大概来自哪」；
#   - **不造数**：没有来源就明说没有（「模型估算（无来源记录）」），绝不静默留空，
#     也绝不用「综合数据」这类模糊说法糊过去；
#   - **不造文案**：用户可见文字一律走 _()，译文在 i18n yml（CI 硬编码中文零增长门禁）。
#
# Excel 批注与 PPT 脚注共用这一个函数——两处标注必须一致，否则客户会问
# 「为什么 Excel 说是 tushare、PPT 说是别的」。
#
# 关于「抓取时间」：deck.provenance **没有**这个字段（只有 data_sources /
# indicators / model / evidence_hash）。本模块用 subject.as_of（数据截至）代替，
# 并如实写成「数据截至」而不是「抓取于」——不冒充一个 deck 里没有的时间。
from __future__ import annotations

from i18n import _

_HASH_PREFIX = 16


def _sources_of(provenance):
    src = (provenance or {}).get('data_sources') or []
    return [str(s) for s in src if s]


def has_source(provenance):
    """该 deck（或该字段）是否真有来源记录。无来源必须显式标注，不能留空。"""
    return bool(_sources_of(provenance))


def note_for(provenance, ref=None, as_of=None):
    """来源标注字符串；Excel 批注与 PPT 脚注共用。

    provenance  deck.provenance（dict，可为空）
    ref         字段路径（如 "verdict.reasons[0]"）；当前只用于追加定位提示，
                不改变来源判定——字段级来源还做不到精确定位。
    as_of       数据截至（subject.as_of）；deck 不记录抓取时间，这里如实标注。

    三种典型输出：
      有来源   → "来源：tushare, akshare_fundamental · 数据截至 2026-06-30 · 证据 a1b2…"
      仅模型   → "模型输出：deepseek-v3 · 证据 a1b2…"
      都没有   → "模型估算（无来源记录）"
    """
    prov = provenance or {}
    sources = _sources_of(prov)
    model = str(prov.get('model') or '')
    digest = str(prov.get('evidence_hash') or '')[:_HASH_PREFIX]

    bits = []
    if sources:
        bits.append(_('Source: {sources}', sources=', '.join(sources)))
    elif model:
        bits.append(_('Model output: {model}', model=model))
    else:
        return _('Model estimate (no source recorded)')

    if as_of:
        bits.append('%s %s' % (_('Data as of'), as_of))
    if digest:
        bits.append(_('Evidence {hash}', hash=digest))
    if ref:
        bits.append(str(ref))
    return ' · '.join(bits)
