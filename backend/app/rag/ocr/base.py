"""OCR 引擎抽象接口。

后续接入 EasyOCR、RapidOCR 等实现时，只需实现 OcrResult 和 BaseOcrEngine。
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class OcrResult:
    """OCR 识别结果：识别文本 + 每个文本框坐标 + 置信度。"""

    text: str
    boxes: list[list[float]]  # 每个文本框的 4 点坐标（8 个数字）
    confidences: list[float]
    # 合并后的整体置信度（简单平均，后续可换成按字数加权）
    confidence: float

    @classmethod
    def empty(cls) -> "OcrResult":
        return cls(text="", boxes=[], confidences=[], confidence=0.0)


class BaseOcrEngine:
    """OCR 引擎基类，所有实现必须支持 __call__(image_path) -> OcrResult。"""

    def __call__(self, image_path: str | Path) -> OcrResult:
        raise NotImplementedError
