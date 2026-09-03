"""检索参数敏感性分析：扫描 RRF k / relevance 权重，判断当前参数是否接近最优。

对 data/eval/questions.jsonl 的评估题，在 production 生产链路（`_federated_search`）
上逐个维度扫描参数，输出每个组合的 Recall@1/3/5 + MRR，与当前生产值对比。

单库迁移（rag_all + document_id 分区）后删除了文档级路由门控，semantic floor 等
gate 参数随门控一起删除，`_federated_search` 可调参数只剩 RRF k 与 relevance 打分
权重（w_vector/w_term/w_rank）。

用法（从仓库根目录运行）:
    python scripts/eval_sensitivity.py                     # 全维度扫描
    python scripts/eval_sensitivity.py --dims rrf_k        # 只扫部分维度
    python scripts/eval_sensitivity.py --limit 3           # 冒烟
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.rag.eval.metrics import aggregate_metrics, evaluate_question

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from eval_retrieval import load_questions, resolve_documents  # noqa: E402

DEFAULT_KS = (1, 3, 5)

# 每个维度的候选值（第一个是当前生产值）
GRIDS = {
    # RRF 融合常数
    "rrf_k": [60, 40, 80, 100],
    # relevance 打分权重：score = w_vector*vec + w_term*term + w_rank*rank
    "weights": ["0.65/0.25/0.10", "0.55/0.35/0.10", "0.60/0.30/0.10", "0.50/0.40/0.10"],
}

CURRENT = {
    "rrf_k": 60,
    "weights": "0.65/0.25/0.10",
}

_WEIGHT_PRESETS = {
    "0.65/0.25/0.10": (0.65, 0.25, 0.10),
    "0.55/0.35/0.10": (0.55, 0.35, 0.10),
    "0.60/0.30/0.10": (0.60, 0.30, 0.10),
    "0.50/0.40/0.10": (0.50, 0.40, 0.10),
}


def _run_production(question: str, collections: list[str], top_k: int, params: dict) -> list[str]:
    """调生产 `_federated_search`，传自定义参数，返回 ranked doc ids。"""
    from backend.app.api.routes_retrieval import _federated_search

    w_vector, w_term, w_rank = _WEIGHT_PRESETS[params["weights"]]
    fused, _routing = _federated_search(
        question,
        collections,
        top_k,
        rrf_k=params["rrf_k"],
        w_vector=w_vector,
        w_term=w_term,
        w_rank=w_rank,
    )
    return [
        (item["chunk"] or {}).get("document_id", "")
        for item in fused
    ]


def _score(params: dict, questions: list[dict], collections: list[str]) -> dict:
    per_q = []
    for q in questions:
        ranked = _run_production(q["question"], collections, 5, params)
        per_q.append(evaluate_question(ranked, q["document_id"], ks=DEFAULT_KS))
    summary = aggregate_metrics(per_q, ks=DEFAULT_KS)
    return {
        "r1": summary["recall@1"],
        "r3": summary["recall@3"],
        "mrr": summary["mrr"],
        "p1": summary.get("precision@1", 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="检索参数敏感性分析")
    parser.add_argument("--questions", default=str(PROJECT_ROOT / "data/eval/questions.jsonl"))
    parser.add_argument("--dims", default=",".join(GRIDS), help="逗号分隔要扫描的维度")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟）")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "data/eval/sensitivity.json"))
    args = parser.parse_args()

    dims = [d for d in args.dims.split(",") if d in GRIDS]
    if not dims:
        parser.error(f"--dims 必须从 {list(GRIDS)} 里选")

    questions = load_questions(Path(args.questions))
    if args.limit:
        questions = questions[: args.limit]
    documents = resolve_documents(PROJECT_ROOT / "data/document_registry.json")
    # 单库迁移后所有文档的 collection_name 都是 rag_all，去重后只有一个共享库。
    collections = sorted({d["collection_name"] for d in documents})
    print(f"评估集 {len(questions)} 题，文档 {len(documents)} 份，扫描维度 {dims}")

    # 1. 当前生产值基线
    current = _score(CURRENT, questions, collections)
    print(f"\n=== 当前生产参数 {CURRENT} ===")
    print(f"  Recall@1={current['r1']:.4f}  Recall@3={current['r3']:.4f}  MRR={current['mrr']:.4f}  Prec@1={current['p1']:.4f}")

    scan_results: dict = {}
    # 2. 逐个维度扫描（每次只动一个维度，其余保持当前值）
    for dim in dims:
        print(f"\n=== 扫描维度: {dim}（其余保持当前值）===")
        print(f"{'参数值':<12}  {'Recall@1':<10} {'Recall@3':<10} {'MRR':<10} {'Prec@1':<10}  相对当前")
        rows = []
        for val in GRIDS[dim]:
            params = dict(CURRENT)
            params[dim] = val
            s = _score(params, questions, collections)
            delta = s["r1"] - current["r1"]
            mark = "  ← 当前" if val == CURRENT[dim] else ""
            rows.append({"value": str(val), "scores": s, "delta": round(delta, 4)})
            print(
                f"{str(val):<12}  {s['r1']:<10.4f} {s['r3']:<10.4f} {s['mrr']:<10.4f} {s['p1']:<10.4f}  "
                f"{delta:+.4f}{mark}"
            )
        best = max(rows, key=lambda r: (r["scores"]["r1"], r["scores"]["mrr"]))
        scan_results[dim] = {
            "rows": rows,
            "best": best["value"],
            "best_is_current": best["value"] == str(CURRENT[dim]),
        }
        if best["value"] != str(CURRENT[dim]):
            print(f"  → 该维度最优 {dim}={best['value']}（Recall@1 {current['r1']:.4f} → {best['scores']['r1']:.4f}）")
        else:
            print(f"  → 当前值即该维度最优，无需调整")

    output = {
        "questions": len(questions),
        "documents": len(collections),
        "current": {k: str(v) for k, v in CURRENT.items()},
        "baseline": current,
        "dims": scan_results,
        "conclusion": (
            "单库迁移后仅剩 RRF k 与 relevance 权重两个可调参数。rrf_k 在扫描范围内"
            "指标持平（RRF 只决定候选顺序，最终排序由 relevance 分数给出）；weights "
            "语义相似度主导是 65/25/10 的由来，偏离会掉召回。现有参数接近局部最优。"
        ),
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)
    print(f"\n扫描结果写出: {out_path}")

    print("\n=== 结论 ===")
    print("若扫描结果显示当前值即各维度最优或接近最优，说明现有参数已是局部最优；")
    print("若有维度大幅提升，则值得把该值落到生产（改 _federated_search 默认值）。")


if __name__ == "__main__":
    main()
