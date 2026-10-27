"""pdf_text.py — PDF 文本抽取（平台共享件）。

为什么在 shared/plugin_base：
    与 egress_policy.py 同因——跨插件复用不能放在任一插件内，否则 import
    会触发插件包初始化形成环。本模块不 import 任何插件，仅第三方 PDF 库。

许可约束（重要，勿改）：
    只允许 pdfplumber(MIT) / pypdf(BSD-3)。**禁止 PyMuPDF(fitz)**——AGPL-3.0，
    随闭源商业软件分发将触发全量开源义务，商用需另购 Artifex 授权。
    历史遗留：plugins/project_workspace/services/doc_processor.py 曾用 fitz，
    已在本模块统一后移除。

设计要点：
    1. 逐页抽取并保留页边界（<<<PAGE:n>>>），供后续引用定位到页而非"接口"；
    2. 三道上限（字节/页数/字符）防止大文件打爆内存与超时；
    3. 缺库/解析失败一律抛 PdfUnavailable，调用方必须降级留痕，不得静默返回空串。
"""
from __future__ import annotations

import io
import logging
from typing import List

logger = logging.getLogger(__name__)

MAX_BYTES = 32 * 1024 * 1024      # 单文件 32MB
MAX_PAGES = 300                   # 单文件最多 300 页（年报通常 150-250）
MAX_CHARS = 400_000               # 抽取字符上限

PAGE_MARK = "<<<PAGE:%d>>>"


class PdfUnavailable(RuntimeError):
    """PDF 后端缺失或解析失败。调用方必须降级，不得静默返回空串。"""


def extract_pages(raw: bytes, *, max_pages: int = MAX_PAGES) -> List[str]:
    """逐页抽取文本，返回每页字符串（失败页为空串，不中断整体）。"""
    if not raw:
        raise PdfUnavailable("empty pdf")
    if len(raw) > MAX_BYTES:
        raise PdfUnavailable("pdf too large: %d bytes > %d" % (len(raw), MAX_BYTES))
    try:
        import pdfplumber
    except ImportError as err:
        raise PdfUnavailable("pdfplumber not installed") from err

    pages: List[str] = []
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for idx, page in enumerate(pdf.pages):
                if idx >= max_pages:
                    break
                try:
                    pages.append(page.extract_text() or "")
                except Exception as err:      # 单页失败不阻断
                    logger.warning("pdf page %d extract failed: %s", idx + 1, err)
                    pages.append("")
    except Exception as err:
        raise PdfUnavailable("pdf parse failed: %s" % err) from err
    return pages


def extract_text(raw: bytes, *, max_pages: int = MAX_PAGES,
                 max_chars: int = MAX_CHARS, with_page_marks: bool = True) -> str:
    """抽取全文。with_page_marks=True 时插入 <<<PAGE:n>>> 分页标记。"""
    pages = extract_pages(raw, max_pages=max_pages)
    buf: List[str] = []
    total = 0
    for i, text in enumerate(pages, start=1):
        chunk = ("\n" + PAGE_MARK % i + "\n" + text) if with_page_marks else text
        buf.append(chunk)
        total += len(chunk)
        if total >= max_chars:
            buf.append("\n<<<TRUNCATED at page %d>>>" % i)
            break
    return "".join(buf)
