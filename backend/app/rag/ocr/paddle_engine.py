"""PaddleOCR 引擎实现。

使用 PaddleOCR 识别中文印刷体/手写体，返回文本、文本框坐标和置信度。
通过 use_gpu 参数控制是否使用 GPU（PaddlePaddle 需要在装包时选定 CUDA 版本）。
"""

import os
import tempfile
from pathlib import Path
from typing import Any

from .base import BaseOcrEngine, OcrResult


class PaddleOcrEngine(BaseOcrEngine):
    """基于 PaddleOCR 的 OCR 引擎。

    默认使用 GPU 推理；若检测不到 GPU 会打印提示并回退 CPU。
    模型首次运行时自动下载（约十几 MB），下载到临时目录或指定缓存目录。
    """

    def __init__(
        self,
        use_gpu: bool | None = None,
        lang: str = "ch",
        model_cache_dir: str | None = None,
        device: str | None = None,
    ):
        self.use_gpu = use_gpu
        self.lang = lang
        self.model_cache_dir = model_cache_dir
        # 允许通过环境变量强制指定推理设备（gpu / cpu），便于不同机器切换
        self.device = device or os.getenv("OCR_DEVICE", "").strip() or None
        self._ocr: Any | None = None

    @property
    def ocr(self) -> Any:
        if self._ocr is None:
            self._load()
        return self._ocr

    def _detect_gpu(self) -> bool:
        try:
            import paddle

            return bool(paddle.device.is_compiled_with_cuda() and paddle.device.is_available())
        except Exception:
            return False

    def _load(self) -> None:
        from paddleocr import PaddleOCR

        # PaddleOCR 3.x 不再用 use_gpu 参数，改用 device="gpu:0"/"cpu"。
        # 参数优先级：显式 device > 自动检测。
        device = self.device
        if not device:
            if self.use_gpu is True:
                device = "gpu:0"
            elif self.use_gpu is False:
                device = "cpu"
            else:
                device = "gpu:0" if self._detect_gpu() else "cpu"
        print(f"[OCR] PaddleOCR 使用设备: {device}")

        kwargs: dict[str, Any] = {
            "lang": self.lang,
            "device": device,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
        }
        if self.model_cache_dir:
            kwargs["model_dir"] = self.model_cache_dir

        self._ocr = PaddleOCR(**kwargs)

    def __call__(self, image_path: str | Path) -> OcrResult:
        path = str(image_path)
        results = self.ocr.predict(path)

        # PaddleOCR 3.x 返回结构：[OCRResult(dict), ...]
        # OCRResult 关键字段：rec_texts(文本列表), rec_scores(置信度), rec_boxes(文本框)
        if not results:
            return OcrResult.empty()

        texts: list[str] = []
        boxes: list[list[float]] = []
        confidences: list[float] = []

        for page_result in results:
            rec_texts = page_result.get("rec_texts") or []
            rec_scores = page_result.get("rec_scores") or []
            rec_boxes = page_result.get("rec_boxes")
            if rec_boxes is None:
                rec_boxes = []
            for i, text in enumerate(rec_texts):
                text = str(text)
                if not text.strip():
                    continue
                texts.append(text)
                # 置信度：rec_scores 与 rec_texts 一一对应
                score = float(rec_scores[i]) if i < len(rec_scores) else 0.0
                confidences.append(score)
                # 文本框坐标：rec_boxes[i] 是 4 个值（扁平化的 4 点坐标）
                if i < len(rec_boxes) and rec_boxes[i] is not None:
                    flat = [float(coord) for coord in rec_boxes[i]]
                    boxes.append(flat)

        full_text = "\n".join(texts)
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        return OcrResult(
            text=full_text,
            boxes=boxes,
            confidences=confidences,
            confidence=avg_conf,
        )


def create_ocr_engine(**kwargs) -> BaseOcrEngine:
    """工厂：创建 OCR 引擎。默认 PaddleOCR，未来可在此扩展其他实现。"""
    return PaddleOcrEngine(**kwargs)
