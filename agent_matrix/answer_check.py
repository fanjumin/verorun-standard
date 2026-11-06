# VeroRun 维洛智能 (verorun.com / verorun.cn)
# 版权所有 (c) 2026 樊聚民 (fanjumin). All Rights Reserved.
"""终稿数值保真校验（P1）。

任务：终稿里的数字必须可回溯到某次工具调用返回。本模块只做**比对与告警**，
不修改答复内容 —— 数字口径（千分位、单位换算、四舍五入）天然存在噪声，
硬拦截会误伤，故仅用于把"数值漂移"从静默失败变成可观测事件。
"""

import logging
import re
from typing import Iterable, List, Set

logger = logging.getLogger(__name__)

# 支持 1,234 / 1,234.56 / 12.5 / 0.07 等形式；不吃掉版本号、路径里的数字片段
_NUM_RE = re.compile(r'(?<![A-Za-z0-9_.\-])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![A-Za-z0-9_])')


def _norm(raw: str) -> str:
    try:
        value = float(raw.replace(',', ''))
    except ValueError:
        return ''
    return '%.10g' % value


def extract_numbers(text: str) -> Set[str]:
    """提取文本中的数值（归一化后），忽略无法解析的片段。"""
    if not text:
        return set()
    out: Set[str] = set()
    for match in _NUM_RE.finditer(text):
        norm = _norm(match.group(1))
        if norm:
            out.add(norm)
    return out


def unmatched_numbers(answer: str, tool_texts: Iterable[str]) -> List[str]:
    """返回终稿中出现、但从未在任何工具结果中出现过的数值。"""
    seen: Set[str] = set()
    for chunk in tool_texts:
        seen |= extract_numbers(chunk or '')
    missing = extract_numbers(answer) - seen
    return sorted(missing, key=lambda x: abs(float(x)), reverse=True)


def summarize_missing(answer: str, tool_texts: Iterable[str], limit: int = 8) -> str:
    """把缺失数值压缩成一行告警文本，供日志/事件使用。"""
    missing = unmatched_numbers(answer, tool_texts)
    if not missing:
        return ''
    return 'final answer has %d number(s) absent from tool results: %s' % (
        len(missing), ', '.join(missing[:limit]))


# 日期：2026-03-01 / 2026/3/1 / 2026年3月1日
_DATE_RE = re.compile(r'(?<!\d)(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?!\d)')


def extract_dates(text: str) -> Set[str]:
    """提取归一化日期（YYYY-MM-DD），用于硬约束类核对。"""
    if not text:
        return set()
    out: Set[str] = set()
    for y, mo, d in _DATE_RE.findall(text):
        try:
            out.add('%04d-%02d-%02d' % (int(y), int(mo), int(d)))
        except ValueError:
            continue
    return out


def summarize_missing_dates(answer: str, tool_texts: Iterable[str], limit: int = 5) -> str:
    """终稿出现的日期若从未在工具结果中出现，返回一行告警（对应"漏/错硬截止日期"）。"""
    seen: Set[str] = set()
    for chunk in tool_texts:
        seen |= extract_dates(chunk or '')
    missing = sorted(extract_dates(answer) - seen)
    if not missing:
        return ''
    return 'final answer has %d date(s) absent from tool results: %s' % (
        len(missing), ', '.join(missing[:limit]))
