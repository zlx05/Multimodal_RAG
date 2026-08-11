"""诊断：doc_3f570a771fd1 的关键 chunk 为何漏召回。

对给定查询，逐 chunk 计算：
  1. 向量相似度（embedder.encode + 已入库 embedding 的 cosine）
  2. BM25 分数（复用 BM25Store）
  3. 本地 RRF 融合排名（pipeline.search 同款）
并打印前 12 与关键 chunk（含答案名词）的得分与内容。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
from backend.app.api.routes_retrieval import _PipelineCache

COLLECTION = "rag_shu_ju_jie_gou_wan_zheng_fu_xi_bi_ji_3f570a771fd1"
QUERIES = {
    "q007": "AVL 树的四种旋转分别是什么？",
    "q008": "邻接矩阵和邻接表分别适合稠密图还是稀疏图？",
    "q011": "顺序查找的平均查找长度 ASL 是多少？",
}

KEYWORDS = {  # 每题的答案关键名词，用于定位含答案的 chunk
    "q007": ("AVL", "旋转", "LL", "RR"),
    "q008": ("邻接矩阵", "邻接表", "稠密图", "稀疏图"),
    "q011": ("顺序查找", "ASL", "平均查找长度"),
}


def main():
    pipe = _PipelineCache().get(COLLECTION, with_llm=False)
    pool = pipe._chunk_pool
    print(f"chunks: {len(pool)}")
    # 预计算所有 embedding（从 Milvus 拉向量）
    from pymilvus import connections
    connections.connect(alias="default", host="127.0.0.1", port="19530")
    result = pipe.collection.query(
        expr="chunk_index >= 0",
        output_fields=["chunk_index", "embedding"],
    )
    emb_by_index = {int(r["chunk_index"]): np.array(r["embedding"]) for r in result}

    # 找出含答案关键词的 chunk
    def has_key(qid, text):
        return sum(1 for kw in KEYWORDS[qid] if kw in text)

    for qid, question in QUERIES.items():
        print("\n" + "=" * 78)
        print(f"{qid} | {question}")
        vec = pipe.embedder.encode([question], normalize_embeddings=True)[0]
        vec_scores = []
        for idx, chunk in enumerate(pool):
            e = emb_by_index.get(chunk["chunk_index"])
            if e is None:
                continue
            sim = float(np.dot(vec, e))
            vec_scores.append((chunk["chunk_index"], sim))
        vec_scores.sort(key=lambda x: x[1], reverse=True)
        bm25 = pipe.bm25.search(question, top_k=len(pool))
        bm25_by_index = {r["index"]: r["score"] for r in bm25}

        from backend.app.rag.hybrid import reciprocal_rank_fusion
        vtop = [{"index": i, "score": s, "source": "vector"} for i, s in vec_scores[:8]]
        btop = [
            {"index": r["index"], "score": r["score"], "source": "bm25"}
            for r in bm25[:8]
        ]
        fused = reciprocal_rank_fusion([vtop, btop])

        # 关键 chunk 定位
        key_chunks = [
            (c["chunk_index"], has_key(qid, c["content"] or ""))
            for c in pool
            if has_key(qid, c["content"] or "") > 0
        ]
        key_chunks.sort(key=lambda x: x[1], reverse=True)
        print(f"  含答案关键词的 chunk: {key_chunks}")
        if key_chunks:
            kc = key_chunks[0][0]
            ktext = pool[kc]["content"]
            ksim = next(s for i, s in vec_scores if i == kc)
            kbm = bm25_by_index.get(kc, 0.0)
            print(f"  关键 chunk[{kc}] 向量分={ksim:.4f} BM25分={kbm:.4f}")
            print(f"    content: {ktext[:150]}")

        print("  前 12 融合排名（pipeline 候选生成）：")
        for rank, item in enumerate(fused[:12], start=1):
            idx = item["index"]
            text = pool[idx]["content"]
            print(
                f"    #{rank} chunk[{idx}] rrf={item['score']:.4f} "
                f"vec={vec_scores[[i for i,_ in vec_scores].index(idx)][1]:.4f} "
                f"bm25={bm25_by_index.get(idx, 0.0):.4f} | {text[:60]}"
            )


if __name__ == "__main__":
    main()
