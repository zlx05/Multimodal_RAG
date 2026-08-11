"""解析器抽象基类。

每个解析器负责把一种原始文件（PDF / Markdown / TXT / 图片 / DOCX / PPTX）解析为
DocumentBlock 列表。上层只关心 blocks，不关心来源类型。
"""

from abc import ABC, abstractmethod
from pathlib import Path

from ..blocks import DocumentBlock


class BaseParser(ABC):
    source_type: str = "unknown"

    def __init__(self, document_id: str):
        self.document_id = document_id

    @abstractmethod
    def parse(self, path: str | Path) -> list[DocumentBlock]:
        """解析文件，返回统一的 DocumentBlock 列表。"""
        raise NotImplementedError

    def _block(
        self,
        text: str,
        content_type: str = "text",
        page_number: int | None = None,
        image_path: str | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        heading_path: list[str] | None = None,
        confidence: float | None = None,
        metadata: dict | None = None,
    ) -> DocumentBlock:
        return DocumentBlock(
            document_id=self.document_id,
            source_type=self.source_type,
            content_type=content_type,
            text=text,
            page_number=page_number,
            image_path=image_path,
            bbox=bbox,
            heading_path=heading_path or [],
            confidence=confidence,
            metadata=metadata or {},
        )
