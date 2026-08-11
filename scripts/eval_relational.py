"""跨文档关系型检索评估：验证"关系型问题能否跨文档检索闭环"（LightRAG 缺口）。

LightRAG 的核心卖点是跨文档关系型/全局型查询（实体跨文档连边、全局摘要）。
本脚本用生产链路（_federated_search，路由门控 + relevance 重排）对
data/eval/questions_relational.jsonl 的关系型题目（每题 2~3 个期望文档）做检索，
量度「全部期望文档是否都被召回」——这是关系型问题可回答的必要条件。

指标语义（见 backend/app/rag/eval/metrics.py 的 evaluate_relational）：
  doc_coverage@K  前 K 个（去重后）文档里找到的期望文档占比（部分召回）
  all_docs@K      全部期望文档是否都在前 K 内（检索闭环，1/0）
  mrr_any         第一个期望文档的倒数排名（只要有一个就有拼接入口）

用法（从仓库根目录运行）:
    conda activate rag11
    python scripts/eval_relational.py                # 全量
    python scripts/eval_relational.py --limit 3      # 冒烟
    python scripts/eval_relational.py --top-k 12     # 提高每路召回数

前提：Milvus 已启动、embedding 模型已下载、文档已入库（documents 表有 collection_name）。
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.rag.catalog import connect_milvus
from backend.app.rag.eval.metrics import aggregate_relational, evaluate_relational

DEFAULT_KS = (1, 3, 5)


def load_document_collections() -> dict[str, str]:
    """从 MySQL documents 表读 (document_id -> collection_name)，覆盖全部已入库文档。"""
    from sqlalchemy import text

    from backend.app.core.database import engine

    mapping: dict[str, str] = {}
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT document_id, collection_name FROM documents WHERE collection_name IS NOT NULL")
        ).fetchall()
    for doc_id, collection in rows:
        mapping[doc_id] = collection
    return mapping


def load_relational_questions(path: Path) -> list[dict]:
    """读取 questions_relational.jsonl 并校验：重复 id / 空问题 / 至少 2 个期望文档。"""
    questions = []
    seen: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if not record.get("id"):
                raise ValueError("questions_relational.jsonl 存在缺少 id 的行")
            if record["id"] in seen:
                raise ValueError(f"重复的 question id: {record['id']}")
            seen.add(record["id"])
            if not record.get("question", "").strip():
                raise ValueError(f"问题 {record['id']} 的 question 为空")
            doc_ids = record.get("document_ids") or []
            if len(doc_ids) < 2:
                raise ValueError(f"问题 {record['id']} 至少需要 2 个期望文档")
            if len(set(doc_ids)) != len(doc_ids):
                raise ValueError(f"问题 {record['id']} 的 document_ids 有重复")
            questions.append(record)
    return questions


def _dedup_keep_order(doc_ids: list[str]) -> list[str]:
    """按首次出现顺序去重，得到文档级排序（用于输出与人工核对）。"""
    seen: set[str] = set()
    result = []
    for doc_id in doc_ids:
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            result.append(doc_id)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="跨文档关系型检索评估（LightRAG 缺口验证）")
    parser.add_argument("--questions", default=str(PROJECT_ROOT / "data/eval/questions_relational.jsonl"))
    parser.add_argument("--out", default=str(PROJECT_ROOT / "data/eval/results_relational.json"))
    parser.add_argument("--top-k", type=int, default=8, help="生产链路每路召回块数（agent 检索工具默认 8）")
    parser.add_argument("--ks", default="1,3,5", help="评估的 K 列表，逗号分隔")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟）")
    args = parser.parse_args()

    ks = tuple(sorted(int(k) for k in args.ks.split(",") if k.strip()))
    questions = load_relational_questions(Path(args.questions))
    if args.limit:
        questions = questions[: args.limit]
    doc_to_collection = load_document_collections()
    print(f"关系型评估集 {len(questions)} 题，已入库文档 {len(doc_to_collection)} 份，top_k={args.top_k}")

    # 校验每个期望文档已入库，否则该题无法评估
    for q in questions:
        missing = [d for d in q["document_ids"] if d not in doc_to_collection]
        if missing:
            raise ValueError(f"问题 {q['id']} 的期望文档未入库: {missing}")

    connect_milvus()
    collections = sorted(set(doc_to_collection.values()))
    print(f"Milvus connected, {len(collections)} collections")

    from backend.app.api.routes_retrieval import _federated_search

    per_question = []
    for q in questions:
        qid = q["id"]
        question = q["question"]
        expected = q["document_ids"]
        fused, _routing = _federated_search(question, collections, args.top_k)
        ranked_doc_ids = [(item["chunk"] or {}).get("document_id", "") for item in fused]
        doc_ranking = _dedup_keep_order(ranked_doc_ids)
        found = {d for d in expected if d in set(ranked_doc_ids[: args.top_k])}
        per_question.append(
            {
                "id": qid,
                "question": question,
                "relation": q.get("relation", ""),
                "note": q.get("note", ""),
                "expected_documents": expected,
                "found_documents": sorted(found),
                "missing_documents": [d for d in expected if d not in found],
                "doc_ranking": doc_ranking[: max(ks) * 2],
                **evaluate_relational(ranked_doc_ids, expected, ks=ks),
            }
        )

    summary = aggregate_relational((item for item in per_question), ks=ks)

    # 表格：每题期望文档数、全部命中情况、缺失文档
    header = f"{'id':<7}{'rel':<10}{'期望':<4}" + "".join(f"cov@{k:<7}" for k in ks) + "".join(
        f"all@{k:<6}" for k in ks
    ) + "miss"
    print("\n" + header)
    for item in per_question:
        cov = "".join(f"{item[f'doc_coverage@{k}']:<9}" for k in ks)
        al = "".join(f"{item[f'all_docs@{k}']:<8}" for k in ks)
        miss = ",".join(item["missing_documents"]) or "-"
        print(f"{item['id']:<7}{item['relation']:<10}{len(item['expected_documents']):<4}{cov}{al}{miss}")
    print("\n汇总：")
    print(f"  questions          = {summary['questions']}")
    for k in ks:
        print(f"  doc_coverage@{k} = {summary[f'doc_coverage@{k}']:.4f}   (平均召回期望文档比例)")
        print(f"  all_docs@{k}     = {summary[f'all_docs@{k}']:.4f}   (全部期望文档都进前 {k} 的比例)")
    print(f"  mrr_any            = {summary['mrr_any']:.4f}   (第一个期望文档的 MRR)")

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {"top_k": args.top_k, "ks": list(ks)},
        "metric_semantics": (
            "doc_coverage@K=前K个去重文档里找到的期望文档比例；all_docs@K=全部期望文档是否都在前K；"
            "mrr_any=第一个期望文档的倒数排名。关系型问题可回答的必要条件是 all_docs 高。"
        ),
        "questions": per_question,
        "summary": summary,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)
    print(f"results 写出: {out_path}")


if __name__ == "__main__":
    main()
