"""统一的多模态文档中间结构 DocumentBlock。

所有解析器（PDF / Markdown / TXT / 图片 / DOCX / PPTX）都必须产出 DocumentBlock，
后续的分块、Embedding、检索和回答生成只依赖这个结构，不关心原始文件类型。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocumentBlock:
    """一份文档解析后的最小语义单元。

    对应 docs/architecture.md 第 3 节定义的结构。
    每个 block 代表一个可独立检索的片段（一段文本、一个 OCR 区域、一个表格）。
    """

    document_id: str
    source_type: str  # pdf | markdown | image | docx | pptx | text
    content_type: str  # text | table | image_ocr | image_description | formula
    text: str
    page_number: int | None = None
    image_path: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    heading_path: list[str] = field(default_factory=list)
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def source_label(self) -> str:
        """用于溯源展示的简短来源描述。"""
        parts = [self.source_type]
        if self.page_number is not None:
            parts.append(f"第{self.page_number}页")
        if self.image_path:
            parts.append(self.image_path.split("\\")[-1].split("/")[-1])
        return " · ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source_type": self.source_type,
            "content_type": self.content_type,
            "text": self.text,
            "page_number": self.page_number,
            "image_path": self.image_path,
            "bbox": self.bbox,
            "heading_path": list(self.heading_path),
            "confidence": self.confidence,
            "metadata": self.metadata,
        }
