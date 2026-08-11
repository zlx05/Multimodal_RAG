"""Formula extraction helpers for native Office math and image formulas."""

from __future__ import annotations

from typing import Any


def extract_native_formulas(element: Any) -> list[str]:
    """Extract OMML/DrawingML formulas as LaTeX from an OOXML element."""
    try:
        from paddleocr._doc2md.math import convert_omath, extract_math_from_paragraph

        if str(getattr(element, "tag", "")).endswith("}oMath"):
            formulas = [convert_omath(element)]
        else:
            formulas = extract_math_from_paragraph(element)
        return [formula for formula in formulas if formula]
    except Exception:
        # Formula conversion is optional for environments without pylatexenc.
        return []


def append_formulas(text: str, formulas: list[str]) -> str:
    """Add canonical LaTeX without replacing the original paragraph text."""
    if not formulas:
        return text.strip()
    formula_text = "\n".join(f"$$\n{formula}\n$$" for formula in formulas)
    return "\n".join(part for part in (text.strip(), "[公式 LaTeX]\n" + formula_text) if part)
