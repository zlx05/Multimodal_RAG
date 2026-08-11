"""Shared OCR + vision analysis for image-like document assets."""

from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Any

from ..metrics import get_metrics
from ..ocr.base import BaseOcrEngine, OcrResult
from ..ocr.formula_engine import FormulaResult
from ..vision import VisionAnalyzer

logger = logging.getLogger(__name__)


def analyze_media(
    path: str | Path,
    ocr_engine: BaseOcrEngine,
    vision_analyzer: VisionAnalyzer | None = None,
    formula_recognizer=None,
) -> tuple[str, dict[str, Any], float, str]:
    """Return one canonical searchable representation for an image asset.

    OCR, formula recognition and vision output are evidence for the same asset,
    not three independent documents. The vision result is preferred because it
    preserves layout and formulas; OCR remains in metadata for audit/fallback.
    """
    metrics = get_metrics()
    metrics.incr("ocr_attempts")
    try:
        ocr_result = ocr_engine(path)
    except Exception as exc:
        # PaddleOCR can fail on a local oneDNN/CUDA runtime. Vision parsing is
        # still useful and must not be blocked by the optional OCR branch.
        metrics.incr("ocr_failures")
        logger.warning("OCR failed for %s, continuing with vision: %s", path, exc)
        ocr_result = OcrResult.empty()
    metadata: dict[str, Any] = {
        "ocr_boxes": ocr_result.boxes,
        "ocr_confidences": ocr_result.confidences,
        "ocr_text": ocr_result.text,
    }

    # A vision-capable model can see the formula's actual region. Run it first
    # and avoid a second full-image formula model call when it succeeds.
    vision_result = None
    if vision_analyzer is not None:
        vision_result = vision_analyzer.analyze(path, ocr_text=ocr_result.text)
    if vision_result:
        metadata.update(vision_result.metadata)
        metadata["vision_description"] = vision_result.text
        return vision_result.text.strip(), metadata, ocr_result.confidence, "image_description"

    formula_result: FormulaResult | None = None
    if formula_recognizer is not None:
        formula_result = formula_recognizer(path)
    valid_formulas = [formula for formula in (formula_result.latex if formula_result else []) if _is_plausible_formula(formula)]
    if valid_formulas:
        formula_text = "\n".join(f"$$\n{formula}\n$$" for formula in valid_formulas)
        metadata["formulas_latex"] = valid_formulas
        metadata["formula_confidence"] = formula_result.confidence if formula_result else None
        ocr_text = ocr_result.text.strip()
        text = f"{ocr_text}\n\n[公式 LaTeX]\n{formula_text}" if ocr_text else f"[公式 LaTeX]\n{formula_text}"
        return text, metadata, ocr_result.confidence, "formula"

    text = ocr_result.text.strip()
    if not text:
        # OCR produced no text for this asset (vision may still rescue content).
        metrics.incr("ocr_failures")
        # Keep an asset-only block when both OCR and vision are unavailable.
        # The original image remains inspectable and the ingestion result is
        # not silently discarded by the chunking stage.
        metadata["asset_only"] = True
        text = f"图片资源：{Path(path).name}（原始图片已保存，请查看来源）"
        return text, metadata, ocr_result.confidence, "image_description"
    return text, metadata, ocr_result.confidence, "image_ocr"


def _is_plausible_formula(value: str) -> bool:
    """Reject full-page formula hallucinations before they enter Milvus."""
    formula = re.sub(r"\s+", " ", str(value or "")).strip()
    if not formula or len(formula) > 1600:
        return False
    if formula.count(r"\quad") > 8 or formula.count("本题") > 4:
        return False
    # A formula result should contain mathematical structure, not only prose.
    return bool(re.search(r"(?:\\[a-zA-Z]+|[_^{}=+*/]|\d)", formula))
