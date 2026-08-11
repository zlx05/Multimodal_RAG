"""OCR 引擎统一导出。"""

from .base import BaseOcrEngine, OcrResult
from .paddle_engine import PaddleOcrEngine, create_ocr_engine

__all__ = ["BaseOcrEngine", "OcrResult", "PaddleOcrEngine", "create_ocr_engine"]
