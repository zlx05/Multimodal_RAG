"""诊断单 collection 内答案 chunk 的排名（向量 + BM25 + RRF 融合）。

用法：python scripts/diag_rank.py <collection> "<query>" "<keyword1>|<keyword2>|..."
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
from backend.app.api.routes_retrieval import _PipelineCache
from backend.app.rag.hybrid import reciprocal_rank_fusion


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    collection, question, kwarg = sys.argv[1], sys.argv[2], sys.argv[3]
    keywords = [k for k in kwarg.split("|") if k]
    print(f"collection={collection}\nquery={question}\nkeywords={keywords}\n")

    pipe = _PipelineCache().get(collection, with_llm=False)
    pool = pipe._chunk_pool
    print(f"chunks: {len(pool)}")

    from pymilvus import connections
    connections.connect(alias="default", host="127.0.0.1", port="19530")
    result = pipe.collection.query(expr="chunk_index >= 0", output_fields=["chunk_index", "embedding"])
    emb_by_index = {int(r["chunk_index"]): np.array(r["embedding"]) for r in result}

    vec = pipe.embedder.encode([question], normalize_embeddings=True)[0]
    vec_scores = sorted(
        ((c["chunk_index"], float(np.dot(vec, emb_by_index[c["chunk_index"]]))) for c in pool),
        key=lambda x: x[1], reverse=True,
    )
    bm25 = pipe.bm25.search(question, top_k=len(pool))
    bm25_by_index = {r["index"]: r["score"] for r in bm25}
    vtop = [{"index": i, "score": s, "source": "vector"} for i, s in vec_scores[:8]]
    btop = [{"index": r["index"], "score": r["score"], "source": "bm25"} for r in bm25[:8]]
    fused = reciprocal_rank_fusion([vtop, btop])

    def get_text(idx):
        return pool[idx]["content"] or ""

    # 关键 chunk
    key = [(c["chunk_index"], sum(1 for kw in keywords if kw in get_text(c["chunk_index"])))
           for c in pool if sum(1 for kw in keywords if kw in get_text(c["chunk_index"])) > 0]
    key.sort(key=lambda x: x[1], reverse=True)
    print(f"含答案关键词的 chunk: {key}")
    for kc, hits in key:
        vs = next(s for i, s in vec_scores if i == kc)
        bm = bm25_by_index.get(kc, 0.0)
        rr = next((x for x in fused if x["index"] == kc), None)
        rrf = rr["score"] if rr else None
        fused_rank = (fused.index(rr) + 1) if rr else None
        print(f"  关键 chunk[{kc}] hits={hits} vec={vs:.4f} bm25={bm:.4f} rrf={rrf} fused_rank={fused_rank}")
        print(f"    content: {get_text(kc)[:100]}")

    print("\n前 12 融合排名:")
    for rank, item in enumerate(fused[:12], start=1):
        idx = item["index"]
        vs = next(s for i, s in vec_scores if i == idx)
        print(f"  #{rank} chunk[{idx}] rrf={item['score']:.4f} vec={vs:.4f} bm25={bm25_by_index.get(idx,0.0):.4f} | {get_text(idx)[:55]}")


if __name__ == "__main__":
    main()
