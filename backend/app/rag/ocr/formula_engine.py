"""Optional PaddleOCR formula-to-LaTeX recognition."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.config import FORMULA_RECOGNITION_DEVICE, FORMULA_RECOGNITION_MODEL

logger = logging.getLogger(__name__)


@dataclass
class FormulaResult:
    latex: list[str]
    confidence: float | None = None


class PaddleFormulaRecognizer:
    """Lazy wrapper around PaddleOCR FormulaRecognition.

    The model is loaded only when the first image-like asset is processed.
    PaddleOCR downloads its formula model on demand if no local model_dir is set.
    """

    def __init__(
        self,
        model_name: str = FORMULA_RECOGNITION_MODEL,
        device: str | None = FORMULA_RECOGNITION_DEVICE,
    ):
        self.model_name = model_name
        self.device = device
        self._model: Any | None = None

    @property
    def model(self) -> Any:
        if self._model is None:
            from paddleocr import FormulaRecognition

            kwargs: dict[str, Any] = {"model_name": self.model_name}
            if self.device:
                kwargs["device"] = self.device
            self._model = FormulaRecognition(**kwargs)
        return self._model

    def __call__(self, image_path: str | Path) -> FormulaResult:
        try:
            results = self.model.predict(str(image_path))
            latex: list[str] = []
            confidences: list[float] = []
            for result in results:
                payload = self._payload(result)
                self._collect(payload, latex, confidences)
            unique = list(dict.fromkeys(value.strip() for value in latex if value.strip()))
            confidence = sum(confidences) / len(confidences) if confidences else None
            return FormulaResult(unique, confidence)
        except Exception as exc:
            logger.warning("Formula recognition failed for %s: %s", image_path, exc)
            return FormulaResult([])

    @staticmethod
    def _payload(result: Any) -> Any:
        if isinstance(result, (dict, list, tuple)):
            return result
        for name in ("json", "res"):
            value = getattr(result, name, None)
            if callable(value):
                try:
                    value = value()
                except Exception:
                    continue
            if value is not None:
                return value
        return result.__dict__ if hasattr(result, "__dict__") else result

    @classmethod
    def _collect(cls, value: Any, latex: list[str], confidences: list[float]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                key_lower = str(key).lower()
                if key_lower in {"rec_formula", "formula", "latex", "rec_latex"}:
                    if isinstance(item, str):
                        latex.append(item)
                    elif isinstance(item, list):
                        latex.extend(str(entry) for entry in item)
                elif key_lower in {"rec_score", "formula_score", "confidence"}:
                    try:
                        if isinstance(item, list):
                            confidences.extend(float(entry) for entry in item)
                        else:
                            confidences.append(float(item))
                    except (TypeError, ValueError):
                        pass
                else:
                    cls._collect(item, latex, confidences)
        elif isinstance(value, (list, tuple)):
            for item in value:
                cls._collect(item, latex, confidences)


def create_formula_recognizer(device: str | None = FORMULA_RECOGNITION_DEVICE) -> PaddleFormulaRecognizer:
    return PaddleFormulaRecognizer(device=device)
