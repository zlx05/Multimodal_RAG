"""验证多模态 RAG 完整链路：图片 OCR + PDF 扫描页 + Markdown 统一入库，混合检索。

用法:
    conda activate rag11
    python scripts/test_multimodal_rag.py
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.rag.parsers import create_parser
from backend.app.rag.hybrid_pipeline import HybridRAGPipeline
from backend.app.rag.chunkers import get_chunker


def main():
    api_key = os.environ.get("LLM_API_KEY", "")
    test_files = [
        PROJECT_ROOT / "data" / "test" / "错题-泰勒展开.png",       # 图片
        PROJECT_ROOT / "data" / "test" / "手写-线性代数笔记.png",     # 图片
        PROJECT_ROOT / "data" / "test" / "概率论讲义.pdf",            # PDF(扫描页)
        PROJECT_ROOT / "data" / "示例-高等数学复习笔记.md",           # Markdown
    ]

    # 1. 解析所有文件
    all_blocks = []
    for path in test_files:
        if not path.exists():
            continue
        print(f"解析: {path.name}")
        parser = create_parser(f"doc_{path.stem}", str(path), original_dir=str(PROJECT_ROOT / "data" / ".orig"))
        blocks = parser.parse(path)
        for b in blocks:
            b.metadata["filename"] = path.name
        all_blocks.extend(blocks)
        print(f"  -> {len(blocks)} blocks, 首个: {blocks[0].text[:40] if blocks else '空'}")
    print(f"\n共 {len(all_blocks)} blocks")

    # 2. 统一入库
    print("\n=== 统一入库 ===")
    pipe = HybridRAGPipeline("rag_mm_v1", api_key, rebuild=True)
    n = pipe.build(all_blocks, get_chunker("markdown", min_chunk_size=30))
    print(f"入库 {n} chunks")

    # 3. 混合检索验证（跨类型）
    print("\n=== 混合检索验证 ===")
    queries = [
        "求极限 lim(x->0) (sin x - x)/x^3",       # 应命中图片错题 OCR
        "矩阵的秩是什么",                           # 应命中手写笔记 OCR
        "随机事件和概率的定义",                     # 应命中 PDF OCR
        "拉格朗日中值定理",                         # 应命中 Markdown
        "泰勒公式",                                 # 可能命中 Markdown + 图片错题
    ]
    for q in queries:
        print(f"\n[查询] {q}")
        fused = pipe.search(q, top_k=3)
        for r in fused:
            c = pipe._chunk_pool_by_index(r["index"])
            src = c["source_type"]
            fname = c["filename"]
            print(f"  {r['score']:.4f} {r['origins']} [{src}] {fname} | {c['content'][:38]}")

    # 4. 溯源展示
    print("\n=== 溯源示例 ===")
    fused = pipe.search("求极限", top_k=1)
    c = pipe._chunk_pool_by_index(fused[0]["index"])
    print(f"  filename: {c['filename']}")
    print(f"  source_type: {c['source_type']}")
    print(f"  image_path: {c['image_path']}")
    print(f"  page_number: {c['page_number']}")
    print(f"  heading_path: {c['heading_path']}")

    print("\n✓ 多模态 RAG 完整链路验证通过")


if __name__ == "__main__":
    main()
