"""验证 PaddleOCR GPU 引擎和图片解析器。

用法:
    conda activate rag11
    python scripts/test_ocr.py [image_path]
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.rag.ocr import PaddleOcrEngine


def main():
    default_images = [
        PROJECT_ROOT / "data" / "test" / "错题-泰勒展开.png",
        PROJECT_ROOT / "data" / "test" / "手写-线性代数笔记.png",
    ]
    images = [Path(a) for a in sys.argv[1:]] or default_images

    engine = PaddleOcrEngine(use_gpu=True)
    for img in images:
        if not img.exists():
            print(f"跳过不存在: {img}")
            continue
        print(f"\n{'=' * 60}")
        print(f"OCR: {img.name}")
        print("=" * 60)
        result = engine(img)
        print(result.text)
        print(f"\n[置信度 {result.confidence:.3f}, {len(result.boxes)} 个文本框]")


if __name__ == "__main__":
    main()
