"""复现 q014 的生产 federated_search 重排结果，对比 RRF 序。"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.api.routes_retrieval import _federated_search, _expand_learning_question

Q = "Go 中标识符名称首字母大小写与可见性的关系是什么"  # q014 改写后
COLLECTIONS = ["rag_ji_ben_yu_fa_e393694f5a06"]
ANSWER_IDX = 13


def main():
    fused, routing = _federated_search(Q, COLLECTIONS, top_k=5)
    print("routing_strategy:", routing["routing_strategy"])
    print("\n== 生产重排后 top-5（agent 实际看到的）==")
    for i, item in enumerate(fused, 1):
        chunk = item["chunk"]
        mark = "  <<答案" if chunk["chunk_index"] == ANSWER_IDX else ""
        print(f"  #{i} chunk[{chunk['chunk_index']}] score={item['score']:.4f} rrf={item['rrf_score']:.4f} | {chunk['content'][:50]}{mark}")
    if not any(item["chunk"]["chunk_index"] == ANSWER_IDX for item in fused):
        print("\n!! 答案 chunk[13] 不在 top-5")
        # 单独算它的 relevance score
        from backend.app.api.routes_retrieval import _relevance_score, _query_phrases, _query_terms
        retrieval_question = _expand_learning_question(Q)
        anchor_phrases = _query_phrases(Q) | _query_phrases(retrieval_question)
        from backend.app.api.routes_retrieval import _PipelineCache
        pipe = _PipelineCache().get(COLLECTIONS[0], with_llm=False)
        for idx in (13, 43, 42, 12):
            chunk = pipe._chunk_pool_by_index(idx)
            if not chunk:
                continue
            # 用 RRF 里的 rank 近似
            import numpy as np
            from pymilvus import connections
            connections.connect(alias="default", host="127.0.0.1", port="19530")
            result = pipe.collection.query(expr=f"chunk_index == {idx}", output_fields=["embedding"])
            e = np.array(result[0]["embedding"]) if result else None
            vec = pipe.embedder.encode([retrieval_question], normalize_embeddings=True)[0]
            vecscore = float(np.dot(vec, e)) if e is not None else -1
            score = _relevance_score(Q, chunk, {"vector": vecscore}, rank=2, total=111, extra_phrases=anchor_phrases)
            print(f"  chunk[{idx}] 若 rank=2 的 relevance={score:.4f} (vec={vecscore:.4f}) | {chunk['content'][:45]}")


if __name__ == "__main__":
    main()
