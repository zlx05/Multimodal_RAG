"""造一份测试扫描件 PDF（纯图片页，无文本层）。

用途：验证 PDF 文档级分类对扫描件的判定，以及 MinerU -m ocr 的真实 OCR 路线。
做法：用 pypdfium2 把已有的原生 PDF 逐页渲染成位图，再用 reportlab 把每页图片
嵌进新 PDF —— 生成的文件只有图像流，pypdf extract_text 为空，classify_pdf 应判 scanned。

用法：
    python scripts/make_scanned_sample.py [源PDF] [输出路径]
默认：data/uploads/doc_224cb977f79b.pdf -> data/test/scanned_sample.pdf
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = PROJECT_ROOT / "data" / "uploads" / "doc_224cb977f79b.pdf"
DEFAULT_OUT = PROJECT_ROOT / "data" / "test" / "scanned_sample.pdf"


def make_scanned_sample(src: Path, out: Path, scale: float = 1.5) -> Path:
    import pypdfium2 as pdfium
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_images: list[Path] = []

    c = canvas.Canvas(str(out))
    try:
        with pdfium.PdfDocument(str(src)) as pdf:
            for idx in range(len(pdf)):
                page = pdf[idx]
                w, h = page.get_size()  # pt
                bitmap = page.render(scale=scale)
                img = tmp = out.parent / f".scan_tmp_{idx}.png"
                bitmap.to_pil().save(img)
                tmp_images.append(tmp)
                c.setPageSize((w, h))
                c.drawImage(ImageReader(str(img)), 0, 0, width=w, height=h)
                c.showPage()
        c.save()
    finally:
        for img in tmp_images:
            img.unlink(missing_ok=True)
    return out


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(PROJECT_ROOT))  # 使 backend 可导入
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    if not src.exists():
        raise SystemExit(f"源 PDF 不存在: {src}")
    result = make_scanned_sample(src, out)
    print(f"已生成扫描件样本: {result}")

    # 顺手断言分类结果，避免误用
    from backend.app.rag.parsers.pdf_classifier import PdfKind, classify_pdf

    kind = classify_pdf(result).kind
    print(f"classify_pdf -> {kind.value}")
    assert kind == PdfKind.scanned, f"期望 scanned，实际 {kind.value}"
