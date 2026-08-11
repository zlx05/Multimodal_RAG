"""验证多模态解析器（PDF / 图片 / Markdown / TXT）端到端。

用法:
    conda activate rag11
    python scripts/test_parsers.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.rag.parsers import create_parser


def main():
    test_files = [
        PROJECT_ROOT / "data" / "示例-高等数学复习笔记.md",
        PROJECT_ROOT / "data" / "test" / "错题-泰勒展开.png",
        PROJECT_ROOT / "data" / "test" / "手写-线性代数笔记.png",
        PROJECT_ROOT / "data" / "test" / "概率论讲义.pdf",
    ]

    for path in test_files:
        if not path.exists():
            print(f"跳过不存在: {path.name}")
            continue
        print(f"\n{'=' * 60}")
        print(f"解析: {path.name} ({path.suffix})")
        print("=" * 60)
        parser = create_parser(f"doc_{path.stem}", str(path))
        blocks = parser.parse(path)
        print(f"共 {len(blocks)} 个 block")
        for b in blocks:
            if not b.text:
                continue
            label = b.content_type
            page = f" p{b.page_number}" if b.page_number else ""
            conf = f" conf={b.confidence:.2f}" if b.confidence else ""
            img = f" img={Path(b.image_path).name}" if b.image_path else ""
            print(f"  [{label}]{page}{conf}{img} | {b.text[:60]}...")


if __name__ == "__main__":
    main()
