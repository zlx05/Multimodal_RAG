"""检索评估：纯向量 / 纯 BM25 / BM25+向量+RRF / 生产路径 四路对比。

用 data/eval/questions.jsonl 的标注问题（每题一个期望文档），对全部
collection 做检索并按变体合并，计算文档级 Recall@K 与 MRR，打印对比表并
写出 data/eval/results.json。

用法（从仓库根目录运行）:
    conda activate rag11
    python scripts/eval_retrieval.py                      # 全量
    python scripts/eval_retrieval.py --limit 3            # 冒烟
    python scripts/eval_retrieval.py --variants vector,rrf

前提：Milvus 已启动、embedding 模型已下载、库内已有入库资料。
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.rag.catalog import connect_milvus
from backend.app.rag.eval.metrics import aggregate_metrics, evaluate_question
from backend.app.rag.hybrid_pipeline import HybridRAGPipeline

PER_COLLECTION_K = 20  # 每 collection 召回数，prod 路径 top_k=5 时用 max(12, 5*3)=15
DEFAULT_KS = (1, 3, 5)
ALL_VARIANTS = ("vector", "bm25", "rrf", "production")


def resolve_documents(registry_path: Path) -> list[dict]:
    """从 document_registry.json 读全部文档（document_id/filename/collection_name）。"""
    with open(registry_path, encoding="utf-8") as fh:
        registry = json.load(fh)
    documents = [
        {
            "document_id": rec["document_id"],
            "filename": rec.get("filename", ""),
            "collection_name": rec["collection_name"],
        }
        for rec in registry.values()
    ]
    return sorted(documents, key=lambda d: d["document_id"])


def load_questions(path: Path) -> list[dict]:
    """读取 questions.jsonl 并做基础校验（重复 id / 空问题 / 未知文档）。"""
    questions = []
    seen: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if not record.get("id"):
                raise ValueError("questions.jsonl 存在缺少 id 的行")
            if record["id"] in seen:
                raise ValueError(f"重复的 question id: {record['id']}")
            seen.add(record["id"])
            if not record.get("question", "").strip():
                raise ValueError(f"问题 {record['id']} 的 question 为空")
            questions.append(record)
    return questions


def _hit_to_record(pipe: HybridRAGPipeline, collection: str, index: int, score: float) -> dict:
    """把 (collection, index) 命中映射为带 document_id/filename 的记录。"""
    pool = pipe._chunk_pool_by_index(index) or {}
    return {
        "collection": collection,
        "index": index,
        "score": round(float(score), 6),
        "document_id": pool.get("document_id", ""),
        "filename": pool.get("filename", ""),
    }


def _sort_hits(hits: list[dict]) -> list[dict]:
    return sorted(hits, key=lambda h: (-h["score"], h["collection"], h["index"]))


def variant_vector(question: str, pipelines: dict[str, HybridRAGPipeline], per_k: int) -> list[dict]:
    """纯向量：cosine 分跨 collection 可比，直接按分合并。"""
    hits = []
    for collection, pipe in pipelines.items():
        for item in pipe._vector_search(question, top_k=per_k):
            hits.append(_hit_to_record(pipe, collection, item["index"], item["score"]))
    return _sort_hits(hits)


def variant_bm25(question: str, pipelines: dict[str, HybridRAGPipeline], per_k: int) -> list[dict]:
    """纯 BM25：BM25Plus 分按 collection 各自 idf 归一，跨库直并按分是近似做法。"""
    hits = []
    for collection, pipe in pipelines.items():
        for item in pipe._bm25_search(question, top_k=per_k):
            hits.append(_hit_to_record(pipe, collection, item["index"], item["score"]))
    return _sort_hits(hits)


def variant_rrf(question: str, pipelines: dict[str, HybridRAGPipeline], per_k: int) -> list[dict]:
    """BM25+向量+RRF：同 (collection, index) 取 max RRF，镜像 _federated_search 合并。"""
    merged: dict[tuple[str, int], tuple[float, HybridRAGPipeline]] = {}
    for collection, pipe in pipelines.items():
        for item in pipe.search(question, top_k=per_k, bm25_k=per_k, vector_k=per_k):
            key = (collection, item["index"])
            current = merged.get(key)
            if current is None or item["score"] > current[0]:
                merged[key] = (item["score"], pipe)
    hits = [
        _hit_to_record(pipe, collection, index, score)
        for (collection, index), (score, pipe) in merged.items()
    ]
    return _sort_hits(hits)


def variant_production(question: str, collections: list[str], top_k: int) -> list[dict]:
    """真实生产路径：直接调 `_federated_search`（路由门控 + relevance 重排 + top_k 截断）。

    这不是纯召回通道，而是用户实际看到的排序——用于回答"生产链路把原始 RRF
    的跨库平局问题拉回多少"。内部 pipeline 缓存由 routes_retrieval 的
    `_pipeline_cache` 管理（with_llm=False，embedder 复用共享缓存）。
    """
    from backend.app.api.routes_retrieval import _federated_search

    fused, _routing = _federated_search(question, collections, top_k)
    return [
        {
            "collection": item["collection"],
            "index": item["index"],
            "score": item["score"],
            "document_id": (item["chunk"] or {}).get("document_id", ""),
            "filename": (item["chunk"] or {}).get("filename", ""),
        }
        for item in fused
    ]


VARIANTS = {
    "vector": variant_vector,
    "bm25": variant_bm25,
    "rrf": variant_rrf,
    "production": variant_production,
}


def print_table(summary: dict[str, dict], ks: tuple[int, ...]) -> None:
    header = "Variant      " + "".join(f"Recall@{k:<9}" for k in ks) + "".join(f"Prec@{k:<9}" for k in ks) + "MRR"
    print("\n" + header)
    for variant in ALL_VARIANTS:
        if variant not in summary:
            continue
        row = summary[variant]
        cells = "".join(f"{row[f'recall@{k}']:<11.4f}" for k in ks)
        cells += "".join(f"{row[f'precision@{k}']:<11.4f}" for k in ks)
        print(f"{variant:<13}" + cells + f"{row['mrr']:.4f}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="检索四路对比评估")
    parser.add_argument("--questions", default=str(PROJECT_ROOT / "data/eval/questions.jsonl"))
    parser.add_argument("--out", default=str(PROJECT_ROOT / "data/eval/results.json"))
    parser.add_argument("--per-collection-k", type=int, default=PER_COLLECTION_K)
    parser.add_argument("--ks", default="1,3,5", help="评估的 K 列表，逗号分隔")
    parser.add_argument("--variants", default=",".join(ALL_VARIANTS), help="逗号分隔变体")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟）")
    args = parser.parse_args()

    ks = tuple(sorted(int(k) for k in args.ks.split(",") if k.strip()))
    variants = tuple(v for v in args.variants.split(",") if v.strip() in VARIANTS)
    if not variants:
        parser.error("--variants 必须包含 vector/bm25/rrf/production 之一")

    registry_path = PROJECT_ROOT / "data/document_registry.json"
    questions = load_questions(Path(args.questions))
    if args.limit:
        questions = questions[: args.limit]
    documents = resolve_documents(registry_path)
    print(f"评估集 {len(questions)} 题，文档 {len(documents)} 份，变体 {variants}")

    connect_milvus()
    pipelines: dict[str, HybridRAGPipeline] = {}
    collections: list[str] = []
    for doc in documents:
        collection = doc["collection_name"]
        collections.append(collection)
        pipelines[collection] = HybridRAGPipeline(collection, with_llm=False)
    print(f"Milvus connected, {len(pipelines)} collections")

    per_question = []
    for q in questions:
        qid = q["id"]
        question = q["question"]
        expected = q["document_id"]
        record = {"id": qid, "question": question, "expected_document_id": expected, "results": {}}
        for variant in variants:
            if variant == "production":
                hits = VARIANTS[variant](question, collections, max(ks))
            else:
                hits = VARIANTS[variant](question, pipelines, args.per_collection_k)
            ranked_doc_ids = [h["document_id"] for h in hits]
            top_hits = [_hit_for_output(h) for h in hits[:10]]
            record["results"][variant] = {
                "ranked_doc_ids": ranked_doc_ids[: max(ks)],
                **evaluate_question(ranked_doc_ids, expected, ks=ks),
                "top_hits": top_hits,
            }
        per_question.append(record)

    summary = {}
    for variant in variants:
        gen = (item["results"][variant] for item in per_question)
        summary[variant] = aggregate_metrics(gen, ks=ks)

    print_table(summary, ks)

    output = {
        "generated_at": _now_iso(),
        "config": {"per_collection_k": args.per_collection_k, "ks": list(ks), "variants": list(variants)},
        "questions": per_question,
        "summary": summary,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)
    print(f"results 写出: {out_path}")


def _hit_for_output(hit: dict) -> dict:
    return {k: hit[k] for k in ("collection", "index", "score", "document_id", "filename")}


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    main()
