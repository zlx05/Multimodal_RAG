"""Dump 全部 chunk 与源文档，对比定位入库丢失的内容。"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.api.routes_retrieval import _PipelineCache

COLLECTION = "rag_shu_ju_jie_gou_wan_zheng_fu_xi_bi_ji_3f570a771fd1"
SRC = PROJECT_ROOT / "data/uploads/doc_3f570a771fd1.md"


def main():
    pipe = _PipelineCache().get(COLLECTION, with_llm=False)
    pool = sorted(pipe._chunk_pool, key=lambda c: c["chunk_index"])
    print(f"== 库内 chunk 数: {len(pool)} ==")
    for c in pool:
        text = c["content"] or ""
        print(f"\n--- chunk[{c['chunk_index']}] len={len(text)} heading={c['heading_path']!r} ---")
        print(text)

    src_text = SRC.read_text(encoding="utf-8")
    print("\n" + "=" * 70)
    print(f"== 源文档: {SRC.name} 总字符 {len(src_text)} ==")
    # 源文档按行列出，方便对比
    for i, line in enumerate(src_text.splitlines(), start=1):
        print(f"{i:>4}| {line}")


if __name__ == "__main__":
    main()
