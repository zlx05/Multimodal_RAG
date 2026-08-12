"""阶段3测试：PDF 文档级分类（原生件 / 扫描件 / 混合图表件）。

用 reportlab 构造三类最小 PDF，断言 classify_pdf 的路由结果与阈值边界。
不引入 OCR / vision / mineru，纯 pypdf + reportlab。
"""

from pathlib import Path

import pytest

from backend.app.rag.parsers.pdf_classifier import PdfKind, classify_pdf
from backend.app.rag.parsers.pdf_parser import TEXT_PAGE_MIN_CHARS

TEXT_LINE = "This is a text page used for PDF classification testing. "


def _text_pdf(path: Path, pages: int) -> Path:
    """构造原生件 PDF：每页一段可被 pypdf 提取的文本，无图片。"""
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    for _ in range(pages):
        for i in range(6):
            c.drawString(50, 750 - i * 20, TEXT_LINE)
        c.showPage()
    c.save()
    return path


def _blank_pdf(path: Path, pages: int) -> Path:
    """构造扫描件 PDF：纯空白页（无文本层），等价于扫描图无文本。"""
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    for _ in range(pages):
        c.showPage()
    c.save()
    return path


def _single_line_pdf(path: Path, text: str) -> Path:
    """构造单行文本 PDF，用于 TEXT_PAGE_MIN_CHARS 阈值边界。"""
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    c.drawString(50, 750, text)
    c.showPage()
    c.save()
    return path


def test_native_pdf_classified_native(tmp_path):
    pdf = _text_pdf(tmp_path / "native.pdf", pages=4)
    result = classify_pdf(pdf)
    assert result.kind == PdfKind.native
    assert result.text_page_ratio == 1.0
    assert result.image_per_page == 0.0


def test_blank_pdf_classified_scanned(tmp_path):
    pdf = _blank_pdf(tmp_path / "scanned.pdf", pages=3)
    assert classify_pdf(pdf).kind == PdfKind.scanned


def test_mixed_text_and_blank_pages_classified_mixed(tmp_path):
    from reportlab.pdfgen import canvas

    # 第 1 页有文本，第 2 页空白（扫描页）-> text_ratio 0.5，介于两阈值之间
    pdf = tmp_path / "mixed.pdf"
    c = canvas.Canvas(str(pdf))
    c.drawString(50, 750, TEXT_LINE)
    c.showPage()
    c.showPage()
    c.save()
    result = classify_pdf(pdf)
    assert result.kind == PdfKind.mixed
    assert 0.25 < result.text_page_ratio < 0.75


def test_mostly_text_with_one_scanned_page_is_mixed(tmp_path):
    """回归：'作品报告' 场景——9 页有文本 + 1 页扫描页（无文本层）。

    旧规则 text_ratio=0.9 直接判 native，导致扫描页走逐页 PaddleOCR 质量差、
    图片丢失。新规则要求 native 不含任何扫描页 -> 判 mixed -> MinerU。
    """
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "report.pdf"
    c = canvas.Canvas(str(pdf))
    for i in range(9):
        for j in range(6):
            c.drawString(50, 750 - j * 20, TEXT_LINE)
        c.showPage()
    c.showPage()  # 第 10 页空白（扫描页）
    c.save()
    result = classify_pdf(pdf)
    assert result.kind == PdfKind.mixed
    assert result.text_page_ratio == 0.9
    assert result.scanned_page_ratio == 0.1
    # 分类证据进入 metadata，入库可追溯路线
    assert result.to_metadata()["pdf_kind"] == "mixed"


def test_text_pages_with_sparse_image_and_no_scanned_is_native(tmp_path):
    """有内嵌图片但每页都有文本、无扫描页 -> 仍走 native 快路（图片稀疏不该上 MinerU）。"""
    from reportlab.pdfgen import canvas

    _TINY_PNG = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    img = tmp_path / "tiny.png"
    img.write_bytes(__import__("base64").b64decode(_TINY_PNG))

    pdf = tmp_path / "text_with_img.pdf"
    c = canvas.Canvas(str(pdf))
    for i in range(4):
        for j in range(6):
            c.drawString(50, 750 - j * 20, TEXT_LINE)
        if i == 1:
            c.drawImage(str(img), 300, 300, width=20, height=20)
        c.showPage()
    c.save()
    result = classify_pdf(pdf)
    assert result.kind == PdfKind.native
    assert result.scanned_page_ratio == 0.0
    assert 0 < result.image_per_page < 0.5


def test_single_page_at_text_threshold_is_native(tmp_path):
    # 恰好 >= TEXT_PAGE_MIN_CHARS：1/1 页有文本，无图 -> native
    pdf = _single_line_pdf(tmp_path / "at.pdf", "x" * TEXT_PAGE_MIN_CHARS)
    assert classify_pdf(pdf).kind == PdfKind.native


def test_single_page_below_text_threshold_is_scanned(tmp_path):
    # 低于阈值：文本不足一页 -> 判扫描（rule 3：text_ratio 0 <= 0.25）
    pdf = _single_line_pdf(tmp_path / "below.pdf", "x" * (TEXT_PAGE_MIN_CHARS - 1))
    assert classify_pdf(pdf).kind == PdfKind.scanned


def test_empty_pdf_is_scanned(tmp_path):
    pdf = _blank_pdf(tmp_path / "empty.pdf", pages=0)
    result = classify_pdf(pdf)
    assert result.kind == PdfKind.scanned
    assert result.sampled_pages == []


def test_mineru_unavailable_falls_back_to_per_page(tmp_path, monkeypatch):
    """MinerU 失败时 PdfParser 回退逐页路线，且仍标注 pdf_kind（路由回归保护）。"""
    from reportlab.pdfgen import canvas

    from backend.app.rag.blocks import DocumentBlock
    from backend.app.rag.parsers.pdf_parser import PdfParser

    # 混合件：文本页 + 空白页（模拟扫描页）-> classify 判 mixed -> 尝试 MinerU
    pdf = tmp_path / "mixed_fallback.pdf"
    c = canvas.Canvas(str(pdf))
    c.drawString(50, 750, TEXT_LINE * 3)
    c.showPage()
    c.showPage()
    c.save()

    class _MineruDown:
        def __init__(self, *args, **kwargs):
            pass

        def parse(self, path):
            from backend.app.rag.parsers.mineru_parser import MineruUnavailable

            raise MineruUnavailable("mineru 未安装")

    monkeypatch.setattr(
        "backend.app.rag.parsers.mineru_parser.MineruParser", _MineruDown
    )
    # 空白页走 OCR，测试里替换为平凡块，避免拉起 PaddleOCR
    def _fake_scanned(self, page_number):
        return DocumentBlock(
            self.document_id, "pdf", "image_ocr", "占位", page_number=page_number
        )

    monkeypatch.setattr(PdfParser, "_parse_scanned_page", _fake_scanned)

    p = PdfParser(
        "doc_fb",
        mineru_enabled=True,
        ocr_engine=object(),
        vision_analyzer=None,
        formula_recognizer=None,
        work_dir=str(tmp_path),
    )
    blocks = p.parse(pdf)
    assert blocks, "MinerU 失败后回退路线也应产出块"
    assert all(b.metadata.get("pdf_kind") == "mixed" for b in blocks)
