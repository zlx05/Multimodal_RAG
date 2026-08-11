"""PDF 解析器：同时处理文本页和扫描页。

策略（对应 docs/architecture.md 第 3 节）：
1. 优先用 pdfplumber 逐页提取文本（阅读顺序更稳），失败逐页回退 pypdf。
2. 文本充足 -> 文本页：
   - pdfplumber find_tables() 检测表格 -> content_type="table" 块（行/列结构保留）；
   - 字号启发式检测标题 -> heading_path（构建章节层级）；
   - 页面内嵌图片交给 OCR/视觉。
3. 文本为空或极少 -> 扫描页：整页渲染成图片，交给 OCR 和可选视觉理解。

渲染 PDF 页为图片使用 pypdfium2（轻量、无外部系统依赖）。
"""

import os
import re
import statistics
import tempfile
from pathlib import Path
from typing import Iterator

from .base import BaseParser
from .media import analyze_media
from ..blocks import DocumentBlock
from ..vision import VisionAnalyzer

# 一页提取到多少字算"有文本"，低于此阈值视为扫描页
TEXT_PAGE_MIN_CHARS = 20
EMPTY_IMAGE_DIR = "empty"
# 字号 >= 正文中位数 * 该倍数 视为标题
HEADING_SIZE_RATIO = 1.15
# 表格 bbox 与行 bbox 判交叠的容差（pt）
TABLE_TOLERANCE = 2.0


class PdfParser(BaseParser):
    source_type = "pdf"

    def __init__(
        self,
        document_id: str,
        ocr_engine=None,
        vision_analyzer: VisionAnalyzer | None = None,
        formula_recognizer=None,
        work_dir: str | None = None,
        original_dir: str | None = None,
    ):
        super().__init__(document_id)
        # 延迟导入，避免没装 OCR 时整个解析器不可用
        if ocr_engine is None:
            from ..ocr import create_ocr_engine

            ocr_engine = create_ocr_engine()
        self.ocr_engine = ocr_engine
        self.vision_analyzer = vision_analyzer
        self.formula_recognizer = formula_recognizer
        # work_dir 存扫描页渲染图和内嵌图片；original_dir 兼容统一接口（PDF 原图保留在原位，无需复制）
        self.work_dir = work_dir or os.getenv("RAG_WORK_DIR", tempfile.gettempdir())
        self.pdf_path: str | None = None

    def parse(self, path: str | Path) -> list[DocumentBlock]:
        from pypdf import PdfReader

        self.pdf_path = str(path)
        reader = PdfReader(str(path))
        try:
            import pdfplumber

            pdf = pdfplumber.open(str(path))
        except Exception:
            pdf = None

        blocks: list[DocumentBlock] = []
        try:
            for page_idx, page in enumerate(reader.pages, start=1):
                text = ""
                plumber_page = None
                if pdf is not None:
                    try:
                        plumber_page = pdf.pages[page_idx - 1]
                        text = (plumber_page.extract_text() or "").strip()
                    except Exception:
                        plumber_page = None
                if len(text) < TEXT_PAGE_MIN_CHARS:
                    # pdfplumber 失败或该页文本稀疏：pypdf 兜底（逐页降级，不影响其他页）
                    try:
                        text = (page.extract_text() or "").strip()
                    except Exception:
                        text = ""
                    plumber_page = None
                if len(text) >= TEXT_PAGE_MIN_CHARS:
                    if plumber_page is not None:
                        blocks.extend(self._parse_text_page_plumber(page_idx, plumber_page, page))
                    else:
                        blocks.extend(self._parse_text_page(page_idx, text, parser_fallback=True))
                        blocks.extend(self._parse_page_images(page_idx, page))
                else:
                    # 扫描页：整页 OCR
                    blocks.append(self._parse_scanned_page(page_idx))
        finally:
            if pdf is not None:
                try:
                    pdf.close()
                except Exception:
                    pass
        return blocks

    def _parse_text_page(self, page_number: int, text: str, parser_fallback: bool = False) -> list[DocumentBlock]:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [p for p in text.split("\n") if p.strip()]
        blocks = []
        for para in paragraphs:
            if para.strip():
                metadata = {"parser_fallback": True} if parser_fallback else {}
                blocks.append(
                    self._block(para, page_number=page_number, content_type="text", metadata=metadata)
                )
        return blocks

    def _parse_text_page_plumber(self, page_number: int, plumber_page, pypdf_page) -> list[DocumentBlock]:
        """pdfplumber 文本页：表格块 + 字号标题层级 + 正文段落，内嵌图片另走 OCR。"""
        blocks: list[DocumentBlock] = []
        table_blocks, table_bboxes = self._extract_tables(page_number, plumber_page)
        blocks.extend(table_blocks)

        try:
            parts = _split_heading_lines(plumber_page.extract_text_lines(), table_bboxes)
            for head, body in _sections_from_headings(parts):
                heading_path: list[str] = []
                if head is not None:
                    _, heading_path, title = head
                    blocks.append(
                        self._block(
                            title,
                            content_type="heading",
                            page_number=page_number,
                            heading_path=heading_path,
                        )
                    )
                if body:
                    paragraph = _reconstruct_paragraphs(body)
                    if paragraph.strip():
                        blocks.append(
                            self._block(
                                paragraph,
                                page_number=page_number,
                                heading_path=heading_path,
                            )
                        )
        except Exception as exc:
            print(f"[pdf_parser] 页面 {page_number} 版面解析失败，退回整页文本: {exc}")
            try:
                full_text = plumber_page.extract_text() or ""
            except Exception:
                full_text = ""
            for para in [p.strip() for p in full_text.split("\n\n") if p.strip()]:
                blocks.append(self._block(para, page_number=page_number))

        blocks.extend(self._parse_page_images(page_number, pypdf_page))
        return blocks

    def _extract_tables(self, page_number: int, plumber_page) -> tuple[list[DocumentBlock], list[tuple]]:
        """find_tables() 检测表格：产出 table 块 + 表格 bbox 列表（供正文排除，避免重复）。"""
        blocks: list[DocumentBlock] = []
        try:
            tables = plumber_page.find_tables()
        except Exception:
            return blocks, []
        bboxes: list[tuple] = []
        for table in tables:
            try:
                raw_rows = table.extract() or []
            except Exception:
                continue
            rows = [r for r in raw_rows if any((c or "").strip() for c in r)]
            if not rows:
                continue
            bboxes.append(tuple(table.bbox))
            cleaned = [[re.sub(r"\s+", " ", (c or "").strip()) for c in row] for row in rows]
            blocks.append(
                self._block(
                    _serialize_table(cleaned),
                    content_type="table",
                    page_number=page_number,
                    metadata={"table": cleaned, "table_format": "pdf"},
                )
            )
        return blocks, bboxes

    def _parse_page_images(self, page_number: int, page) -> list[DocumentBlock]:
        """从文本页中提取内嵌图片并 OCR（公式图、插图等）。"""
        blocks = []
        try:
            images = list(page.images)
        except Exception:
            return blocks

        for img_idx, img in enumerate(images):
            try:
                suffix = (img.name or f"img{img_idx}").rsplit(".", 1)[-1].lower()
                if suffix not in {"png", "jpg", "jpeg", "bmp"}:
                    suffix = "png"
                img_bytes = img.data
                img_path = Path(self.work_dir) / f"pdf_{self.document_id}_p{page_number}_{img_idx}.{suffix}"
                img_path.parent.mkdir(parents=True, exist_ok=True)
                img_path.write_bytes(img_bytes)
                text, metadata, confidence, content_type = analyze_media(
                    img_path,
                    self.ocr_engine,
                    self.vision_analyzer,
                    self.formula_recognizer,
                )
                if text.strip():
                    blocks.append(
                        self._block(
                            text,
                            content_type=content_type,
                            page_number=page_number,
                            image_path=str(img_path),
                            confidence=confidence,
                            metadata=metadata,
                        )
                    )
            except Exception as exc:
                print(f"[pdf_parser] 页面 {page_number} 第 {img_idx} 张图片 OCR 失败: {exc}")
        return blocks

    def _parse_scanned_page(self, page_number: int) -> DocumentBlock:
        """把整个扫描页渲染为图片并 OCR。"""
        try:
            import pypdfium2 as pdfium

            with pdfium.PdfDocument(self.pdf_path) as pdf:
                page = pdf[page_number - 1]
                bitmap = page.render(scale=2.0)  # 2x 提高小字识别率
                pil_image = bitmap.to_pil()
            img_path = Path(self.work_dir) / f"pdf_{self.document_id}_p{page_number}_scan.png"
            img_path.parent.mkdir(parents=True, exist_ok=True)
            pil_image.save(img_path)
            text, metadata, confidence, content_type = analyze_media(
                img_path,
                self.ocr_engine,
                self.vision_analyzer,
                self.formula_recognizer,
            )
            return self._block(
                text,
                content_type=content_type,
                page_number=page_number,
                image_path=str(img_path),
                confidence=confidence,
                metadata={**metadata, "scan_page": True},
            )
        except Exception as exc:
            print(f"[pdf_parser] 扫描页 {page_number} OCR 失败: {exc}")
            return self._block("", content_type="image_ocr", page_number=page_number)


# ---------------------------------------------------------------------------
# 纯函数：字号标题启发式 + 段落重建 + 表格序列化。可在不引入 pdfplumber 的
# 情况下用构造的 line/char 数据单测。
# ---------------------------------------------------------------------------


def _line_is_heading(size: float, bold: bool, body_size: float) -> bool:
    """字号启发式：明显大于正文中位数（且至少大 1pt）或同字号加粗 => 标题。"""
    if size <= 0 or body_size <= 0:
        return False
    if size >= body_size * HEADING_SIZE_RATIO and size - body_size >= 1.0:
        return True
    return bold and size >= body_size


def _classify_heading_lines(lines) -> list[tuple[str, int]]:
    """把 pdfplumber extract_text_lines() 的行 dict 分为正文与标题。

    返回 [(text, level)]：level=0 为正文；>=1 为标题，按字号从大到小编号（最大字号=1）。
    """
    entries = []
    for ln in lines:
        chars = ln.get("chars") or []
        size = max((c.get("size") or 0) for c in chars) if chars else 0.0
        bold = any("bold" in (c.get("fontname") or "").lower() for c in chars)
        entries.append((ln.get("text") or "", size, bold))
    valid = [size for _, size, _ in entries if size > 0]
    body_size = statistics.median(valid) if valid else 0.0
    # 大字号标题会把中位数抬到正文之上，小字号标题因此漏检；
    # 排除明显偏大的字号（> 正文中位数 1.3 倍）后重估正文基线。
    core = [size for size in valid if size <= body_size * 1.3]
    if core:
        body_size = statistics.median(core)
    heading_sizes = sorted(
        {size for text, size, bold in entries if text.strip() and _line_is_heading(size, bold, body_size)},
        reverse=True,
    )
    size_to_level = {size: idx + 1 for idx, size in enumerate(heading_sizes)}
    # 标题层级按字号全局排名，但某行是否算标题要按本行判定：
    # 否则加粗标题所在字号会把同字号普通正文一并带成标题。
    result = []
    for text, size, bold in entries:
        if text.strip() and _line_is_heading(size, bold, body_size):
            result.append((text.strip(), size_to_level.get(size, 0) or 1))
        else:
            result.append((text.strip(), 0))
    return result


def _line_in_tables(line, table_bboxes) -> bool:
    """行 bbox 是否与任一表格区域相交（正文抽取时排除表格区，避免重复）。"""
    if not table_bboxes:
        return False
    x0, top = line.get("x0"), line.get("top")
    x1, bottom = line.get("x1"), line.get("bottom")
    if None in (x0, top, x1, bottom):
        return False
    for tx0, ttop, tx1, tbottom in table_bboxes:
        if (
            x0 <= tx1 - TABLE_TOLERANCE
            and x1 >= tx0 + TABLE_TOLERANCE
            and top <= tbottom - TABLE_TOLERANCE
            and bottom >= ttop + TABLE_TOLERANCE
        ):
            return True
    return False


def _split_heading_lines(lines, table_bboxes) -> list[tuple]:
    """按表格区域过滤 + 字号启发式分类，返回有序事件列表。

    每条为 ("head", level, text) 或 ("body", text, x0, top, x1, bottom)。
    """
    kept = [ln for ln in lines if not _line_in_tables(ln, table_bboxes)]
    parts: list[tuple] = []
    for ln, (text, level) in zip(kept, _classify_heading_lines(kept)):
        if level > 0 and text:
            parts.append(("head", level, text))
        elif text:
            parts.append(("body", text, ln.get("x0"), ln.get("top"), ln.get("x1"), ln.get("bottom")))
    return parts


def _sections_from_headings(parts) -> list[tuple]:
    """把标题/正文事件切成节。返回 [(head, body_lines)]。

    head=(depth, heading_path, title)（正文引导段前无标题则为 None）；
    heading_path 含自身标题（与 markdown_parser 语义一致，跨页同章节可合并）。
    """
    sections: list[tuple] = []
    stack: list[tuple[int, str]] = []
    cur_head = None
    cur_body: list[tuple] = []
    for part in parts:
        if part[0] == "head":
            if cur_head is not None or cur_body:
                sections.append((cur_head, cur_body))
            _, level, title = part
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            cur_head = (level, [t for _, t in stack], title)
            cur_body = []
        else:
            cur_body.append(part[1:])
    if cur_head is not None or cur_body:
        sections.append((cur_head, cur_body))
    return sections


def _reconstruct_paragraphs(line_infos) -> str:
    """按行间垂直间距重建段落。line_infos: [(text, x0, top, x1, bottom)]。

    相邻行垂直间隙超过阈值 -> 空行分段；否则单换行相连（保留阅读顺序）。
    """
    if not line_infos:
        return ""
    heights = [bottom - top for _, _, top, _, bottom in line_infos]
    median_h = statistics.median(heights) if heights else 12.0
    gap_threshold = max(median_h * 0.5, 2.0)
    out = [line_infos[0][0]]
    for prev, curr in zip(line_infos, line_infos[1:]):
        gap = curr[2] - prev[4]  # curr.top - prev.bottom
        out.append(("\n\n" if gap > gap_threshold else "\n") + curr[0])
    return "".join(out)


def _serialize_table(rows) -> str:
    """把嵌套行序列化为 Markdown 表格文本（供嵌入与展示）。"""
    if not rows:
        return ""
    lines = ["| " + " | ".join(str(c) for c in rows[0]) + " |"]
    lines.append("|" + "|".join("---" for _ in rows[0]) + "|")
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)
