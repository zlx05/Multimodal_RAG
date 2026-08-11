"""解析器统一导出和工厂。

create_parser(document_id, path) 根据文件扩展名选择解析器，
上层（ingestion / worker）只需要调用 parse() 拿到统一的 DocumentBlock。
"""

from pathlib import Path
from typing import Any

from .base import BaseParser
from .csv_parser import CsvParser
from .docx_parser import DocxParser
from .html_parser import HtmlParser
from .image_parser import ImageParser
from .markdown_parser import MarkdownParser
from .office_converter import LEGACY_EXTENSIONS, convert_legacy_office
from .pdf_parser import PdfParser
from .pptx_parser import PptxParser
from .text_parser import TextParser
from .xlsx_parser import XlsxParser

SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".txt": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".bmp": "image",
    ".webp": "image",
    ".docx": "docx",
    ".pptx": "pptx",
    ".doc": "legacy_doc",
    ".ppt": "legacy_ppt",
    ".html": "html",
    ".htm": "html",
    ".xlsx": "xlsx",
    ".csv": "csv",
}


def get_parser_type(path: str | Path) -> str:
    """根据扩展名返回解析器类型。"""
    ext = Path(path).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"不支持的文件类型: {ext}。支持: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    return SUPPORTED_EXTENSIONS[ext]


def create_parser(document_id: str, path: str | Path, **kwargs: Any) -> BaseParser:
    """创建合适的解析器实例。"""
    parser_type = get_parser_type(path)
    if parser_type in {"legacy_doc", "legacy_ppt"}:
        path = convert_legacy_office(
            document_id,
            path,
            work_dir=kwargs.get("work_dir"),
        )
        parser_type = get_parser_type(path)
    if parser_type == "pdf":
        return PdfParser(document_id, **kwargs)
    if parser_type == "markdown":
        return MarkdownParser(document_id)
    if parser_type == "text":
        return TextParser(document_id)
    if parser_type == "html":
        return HtmlParser(document_id, **kwargs)
    if parser_type == "image":
        return ImageParser(document_id, **kwargs)
    if parser_type == "docx":
        return DocxParser(document_id, **kwargs)
    if parser_type == "pptx":
        return PptxParser(document_id, **kwargs)
    if parser_type == "xlsx":
        return XlsxParser(document_id, **kwargs)
    if parser_type == "csv":
        return CsvParser(document_id, **kwargs)
    raise ValueError(f"未知解析器类型: {parser_type}")


__all__ = [
    "BaseParser",
    "PdfParser",
    "MarkdownParser",
    "TextParser",
    "HtmlParser",
    "ImageParser",
    "DocxParser",
    "PptxParser",
    "XlsxParser",
    "CsvParser",
    "create_parser",
    "get_parser_type",
    "SUPPORTED_EXTENSIONS",
    "LEGACY_EXTENSIONS",
    "convert_legacy_office",
]
