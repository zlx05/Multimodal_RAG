"""HTML article parser with image extraction for web learning materials."""

from __future__ import annotations

import html
import logging
import mimetypes
import re
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .base import BaseParser
from .media import analyze_media
from ..blocks import DocumentBlock
from ..vision import VisionAnalyzer

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 12 * 1024 * 1024


class _ArticleExtractor(HTMLParser):
    """Extract readable article elements while ignoring site chrome."""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "template", "iframe", "form"}
    TEXT_TAGS = {"p", "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}
    # 表格单元格不单独成块：同一行的相邻 td/th 先累积，整表结束时合成一个 table 块，
    # 保留行/列结构（metadata["table"]），避免"表头与值分离"导致行内关联丢失。
    TABLE_CELL_TAGS = {"td", "th"}
    TABLE_CONTAINER_TAGS = {"table"}
    # 列表项不单独成块：同一 ul/ol 的相邻 li 合并为一个块，避免"可比较类型有：布尔/数字/字符串…"
    # 这类枚举被切碎成单行 chunk，检索只能看到片段、agent 反复补检直到 max_iterations 截断。
    LIST_CELL_TAGS = {"li"}
    LIST_CONTAINER_TAGS = {"ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.skip_depth = 0
        self.main_depth: int | None = None
        self.text_parts: list[str] = []
        self.current: list[str] = []
        self.current_tag = ""
        self.code_line_depth: int | None = None
        self.headings: list[str] = []
        # blocks: (content_type, text, headings, metadata)
        self.blocks: list[tuple[str, str, list[str], dict]] = []
        self.images: list[tuple[str, str, list[str]]] = []
        self.row_parts: list[str] = []
        self.list_parts: list[str] = []
        self.in_table = False
        self.table_rows: list[list[str]] = []
        self.table_caption = ""
        # 暂存的段落文本：若下一个结构块是 <ul>/<ol>，则作为列表引导语并入列表块，
        # 使"可比较类型有：布尔；数字；字符串…"这类枚举自含检索词，避免列表块孤立不可召回。
        self.pending_text = ""

    def handle_starttag(self, tag: str, attrs):
        attrs_dict = dict(attrs)
        self.depth += 1
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag in {"main", "article"} and self.main_depth is None:
            self.main_depth = self.depth
        if self.skip_depth or not self._in_content():
            return
        if tag == "span" and self.current_tag == "pre":
            classes = set((attrs_dict.get("class") or "").split())
            if "line" in classes:
                if self.current and not self.current[-1].endswith("\n"):
                    self.current.append("\n")
                self.code_line_depth = self.depth
        if tag == "br" and self.current_tag == "pre":
            self.current.append("\n")
        if tag == "img":
            source = attrs_dict.get("src") or attrs_dict.get("data-src") or ""
            if source:
                self.images.append((source, attrs_dict.get("alt", ""), list(self.headings)))
        if tag in self.TABLE_CONTAINER_TAGS:
            # 整表一个块：开始新表，行数据先累积进 table_rows
            self._flush()
            self.in_table = True
            self.table_rows = []
            self.table_caption = ""
        elif tag == "caption" and self.in_table:
            self._emit_pending()
            self.current_tag = "caption"
        elif tag in self.TEXT_TAGS:
            self._flush()
            self.current_tag = tag
        elif tag in self.TABLE_CELL_TAGS:
            # 单元格不立即 flush：与同行的其他单元格累积进 row_parts
            self._emit_pending()
            self.current_tag = tag
        elif tag == "tr":
            self._emit_pending()
        elif tag in self.LIST_CELL_TAGS:
            # 列表项不立即 flush：与同列表的其他项累积进 list_parts。
            # 不调 _flush：保留 pending_text，让引导段并入列表块。
            self.current = []
            self.current_tag = tag
        elif tag in self.LIST_CONTAINER_TAGS:
            # <ul>/<ol> 开始：不 flush，pending_text 保留给列表块当引导语
            pass

    def handle_startendtag(self, tag: str, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str):
        if tag == "span" and self.code_line_depth == self.depth:
            self.code_line_depth = None
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
        if not self.skip_depth:
            if tag == "caption" and self.in_table:
                text = _clean_text(" ".join(self.current))
                if text:
                    self.table_caption = text
                self.current = []
                self.current_tag = ""
            elif tag in self.TABLE_CELL_TAGS:
                text = _clean_text(" ".join(self.current))
                if text:
                    self.row_parts.append(text)
                self.current = []
                self.current_tag = ""
            elif tag == "tr":
                if self.row_parts:
                    if self.in_table:
                        self.table_rows.append(list(self.row_parts))
                    else:
                        self.blocks.append(("text", " | ".join(self.row_parts), list(self.headings), {}))
                self.row_parts = []
                self.current = []
                self.current_tag = ""
            elif tag in self.TABLE_CONTAINER_TAGS:
                if self.in_table:
                    if self.table_rows:
                        metadata: dict = {"table": self.table_rows, "table_format": "html"}
                        if self.table_caption:
                            metadata["caption"] = self.table_caption
                        self.blocks.append(("table", _serialize_table(self.table_rows), list(self.headings), metadata))
                    self.in_table = False
                    self.table_rows = []
                    self.table_caption = ""
            elif tag in self.LIST_CELL_TAGS:
                text = _clean_text(" ".join(self.current))
                if text:
                    self.list_parts.append(text)
                self.current = []
                self.current_tag = ""
            elif tag in self.LIST_CONTAINER_TAGS:
                if self.list_parts:
                    items = "；".join(self.list_parts)
                    if self.pending_text:
                        # 列表直接跟在段落后：把引导段并入列表块，自含上下文便于检索
                        lead = self.pending_text
                        sep = "" if lead.endswith(("：", ":", ":")) else "："
                        items = lead + sep + items
                        self.pending_text = ""
                    self.blocks.append(("text", items, list(self.headings), {}))
                else:
                    self._emit_pending()  # 空列表：引导段按普通段落落出
                self.list_parts = []
                self.current = []
                self.current_tag = ""
            elif tag in self.TEXT_TAGS:
                text = _clean_code("".join(self.current)) if tag == "pre" else _clean_text(" ".join(self.current))
                if text:
                    if tag.startswith("h"):
                        self._emit_pending()
                        self.headings = self.headings[: max(int(tag[1:]) - 1, 0)]
                        self.headings.append(text)
                        self.blocks.append(("heading", text, list(self.headings), {}))
                    elif tag == "p":
                        # 段落先暂存：紧跟 <ul>/<ol> 则作为引导语合并，否则在下个块开始处落出
                        self._emit_pending()
                        self.pending_text = text
                    else:
                        self._emit_pending()
                        self.blocks.append(("code" if tag == "pre" else "text", text, list(self.headings), {}))
                self.current = []
                self.current_tag = ""
        if self.main_depth == self.depth and tag in {"main", "article"}:
            self.main_depth = None
        self.depth = max(0, self.depth - 1)

    def handle_data(self, data: str):
        if self.skip_depth or not self._in_content():
            return
        if self.current_tag:
            if self.current_tag == "pre" and self.code_line_depth is None and not data.strip():
                return
            self.current.append(data)

    def close(self):
        super().close()
        self._flush()
        if self.in_table and self.table_rows:
            metadata: dict = {"table": self.table_rows, "table_format": "html"}
            if self.table_caption:
                metadata["caption"] = self.table_caption
            self.blocks.append(("table", _serialize_table(self.table_rows), list(self.headings), metadata))
            self.in_table = False
            self.table_rows = []
        if self.row_parts:
            self.blocks.append(("text", " | ".join(self.row_parts), list(self.headings), {}))
            self.row_parts = []
        if self.list_parts:
            self.blocks.append(("text", "；".join(self.list_parts), list(self.headings), {}))
            self.list_parts = []

    def _emit_pending(self):
        """把暂存的引导段落按普通文本块落出（下个非列表块开始时调用）。"""
        if self.pending_text:
            self.blocks.append(("text", self.pending_text, list(self.headings), {}))
            self.pending_text = ""

    def _flush(self):
        self._emit_pending()
        if self.current_tag:
            text = _clean_text(" ".join(self.current))
            if text:
                self.blocks.append(("text", text, list(self.headings), {}))
        self.current = []

    def _in_content(self) -> bool:
        return self.main_depth is not None or self.depth == 0


class HtmlParser(BaseParser):
    source_type = "html"

    def __init__(
        self,
        document_id: str,
        ocr_engine=None,
        vision_analyzer: VisionAnalyzer | None = None,
        formula_recognizer=None,
        work_dir: str | None = None,
        original_dir: str | None = None,
        base_url: str = "",
    ):
        super().__init__(document_id)
        if ocr_engine is None:
            from ..ocr import create_ocr_engine

            ocr_engine = create_ocr_engine()
        self.ocr_engine = ocr_engine
        self.vision_analyzer = vision_analyzer
        self.formula_recognizer = formula_recognizer
        self.work_dir = Path(work_dir or "data/.work")
        self.base_url = base_url

    def parse(self, path: str | Path) -> list[DocumentBlock]:
        parser = _ArticleExtractor()
        parser.feed(Path(path).read_text(encoding="utf-8", errors="replace"))
        parser.close()
        blocks = [
            self._block(
                text,
                content_type=kind,
                heading_path=headings,
                metadata={**{"source_url": self.base_url}, **meta},
            )
            for kind, text, headings, meta in parser.blocks
        ]
        blocks.extend(self._parse_images(parser.images))
        if not blocks:
            raise ValueError("网页正文为空，未找到可解析的 main/article 内容")
        return blocks

    def _parse_images(self, images: list[tuple[str, str, list[str]]]) -> list[DocumentBlock]:
        asset_dir = self.work_dir / self.document_id / "html_assets"
        blocks: list[DocumentBlock] = []
        for index, (source, alt, headings) in enumerate(images, start=1):
            image_path = self._download_image(source, asset_dir, index)
            if image_path is None:
                if alt.strip():
                    blocks.append(self._block(
                        f"图片：{alt.strip()}",
                        content_type="image_description",
                        heading_path=headings,
                        metadata={"source_url": urljoin(self.base_url, source), "alt": alt.strip()},
                    ))
                continue
            text, metadata, confidence, content_type = analyze_media(
                image_path,
                self.ocr_engine,
                self.vision_analyzer,
                self.formula_recognizer,
            )
            metadata.update({"source_url": urljoin(self.base_url, source), "alt": alt.strip()})
            if not text.strip() and alt.strip():
                text = f"图片：{alt.strip()}"
                content_type = "image_description"
            if text.strip():
                blocks.append(self._block(
                    text,
                    content_type=content_type,
                    heading_path=headings,
                    image_path=str(image_path),
                    confidence=confidence,
                    metadata=metadata,
                ))
        return blocks

    def _download_image(self, source: str, asset_dir: Path, index: int) -> Path | None:
        url = urljoin(self.base_url, html.unescape(source))
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "ContextLab/1.0"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = response.read(MAX_IMAGE_BYTES + 1)
                    if len(data) > MAX_IMAGE_BYTES:
                        return None
                    content_type = response.headers.get_content_type()
                suffix = Path(parsed.path).suffix.lower()
                if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                    suffix = mimetypes.guess_extension(content_type) or ".png"
                asset_dir.mkdir(parents=True, exist_ok=True)
                image_path = asset_dir / f"image_{index:03d}{suffix}"
                image_path.write_bytes(data)
                return image_path
            except Exception as exc:
                if attempt == 2:
                    logger.warning("HTML image download failed for %s: %s", url, exc)
                else:
                    time.sleep(1)
        return None


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _serialize_table(rows: list[list[str]]) -> str:
    """把嵌套行序列化为 Markdown 表格文本（供嵌入与展示）。"""
    if not rows:
        return ""
    lines = ["| " + " | ".join(str(c) for c in rows[0]) + " |"]
    lines.append("|" + "|".join("---" for _ in rows[0]) + "|")
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def _clean_code(value: str) -> str:
    """Keep code line breaks while removing blank wrapper lines."""
    lines = [line.rstrip() for line in value.replace("\r\n", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)
