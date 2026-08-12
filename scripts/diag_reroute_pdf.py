"""诊断：用新代码重跑 作品报告 的解析路线，验证分类 → MinerU → 图片路径。

不写库，只解析本地文件并打印：
- classify_pdf 的判定（期望 mixed）
- 解析出的 block 类型分布、pdf_kind / mineru_engine
- 图片块的 image_path 是否满足 asset_url 校验（能生成可展示 URL）
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.rag.assets import asset_url
from backend.app.rag.parsers.pdf_classifier import classify_pdf
from backend.app.rag.parsers.pdf_parser import PdfParser

PDF = PROJECT_ROOT / "data" / "uploads" / "doc_510eb48849af.pdf"
DOC_ID = "doc_510eb48849af"
WORK_DIR = PROJECT_ROOT / "data" / ".work"


def main():
    kind = classify_pdf(PDF)
    print(f"classify → {kind.kind.value} (text_ratio={kind.text_page_ratio}, "
          f"scanned_ratio={kind.scanned_page_ratio}, img/page={kind.image_per_page})")

    parser = PdfParser(DOC_ID, work_dir=str(WORK_DIR))
    blocks = parser.parse(PDF)
    print(f"\n解析完成：{len(blocks)} blocks")
    from collections import Counter
    print("类型分布:", dict(Counter(b.content_type for b in blocks)))
    kinds = {b.metadata.get("pdf_kind") for b in blocks}
    mineru = {b.metadata.get("mineru_engine") for b in blocks}
    print("pdf_kind 标注:", kinds, "| mineru_engine:", mineru)

    print("\n图片块检查（asset_url 能否生成可展示 URL）:")
    img_blocks = [b for b in blocks if b.content_type in ("image_description", "image_ocr")]
    for b in img_blocks:
        ip = b.image_path or ""
        url = asset_url(DOC_ID, ip)
        exists = Path(ip).exists() if ip else False
        print(f"  p{b.page_number} [{b.content_type}] image_path={ip!r} "
              f"exists={exists} asset_url={'✓' if url else '✗ None'}")
        print(f"      text: {b.text[:80].replace(chr(10), ' ')}")


if __name__ == "__main__":
    main()
