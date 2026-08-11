"""图片解析器：对截图错题、手写笔记等图片执行 OCR 和可选视觉理解。

保存原图引用（image_path），OCR 文本进入检索字段，原图路径进入来源字段，
供前端展示"原文 + 原图复核"。
"""

import os
import shutil
from pathlib import Path

from .base import BaseParser
from .media import analyze_media
from ..blocks import DocumentBlock
from ..vision import VisionAnalyzer


class ImageParser(BaseParser):
    source_type = "image"

    def __init__(
        self,
        document_id: str,
        ocr_engine=None,
        vision_analyzer: VisionAnalyzer | None = None,
        formula_recognizer=None,
        original_dir: str | None = None,
        work_dir: str | None = None,
        copy_original: bool = True,
    ):
        super().__init__(document_id)
        if ocr_engine is None:
            from ..ocr import create_ocr_engine

            ocr_engine = create_ocr_engine()
        self.ocr_engine = ocr_engine
        self.vision_analyzer = vision_analyzer
        self.formula_recognizer = formula_recognizer
        self.original_dir = original_dir or os.getenv("RAG_ORIGINAL_DIR", "")
        self.copy_original = copy_original

    def parse(self, path: str | Path) -> list[DocumentBlock]:
        path = Path(path)
        stored_path = str(path)

        # 把原图复制到管理目录，避免解析后原始位置被清理
        if self.copy_original and self.original_dir:
            target = Path(self.original_dir) / f"{self.document_id}{path.suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            stored_path = str(target)

        text, metadata, confidence, content_type = analyze_media(
            stored_path,
            self.ocr_engine,
            self.vision_analyzer,
            self.formula_recognizer,
        )
        if not text.strip():
            # OCR 无内容也保留一个空 block，让上层知道这个图已被处理
            return [
                self._block(
                    "",
                    content_type=content_type,
                    image_path=stored_path,
                    confidence=confidence,
                    metadata=metadata,
                )
            ]

        return [
            self._block(
                text,
                content_type=content_type,
                image_path=stored_path,
                confidence=confidence,
                metadata=metadata,
            )
        ]
