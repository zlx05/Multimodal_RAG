"""答案 groundedness 评估：生产链路生成答案 → 人工标注 0/1/2 → 汇总指标。

复用 data/eval/questions.jsonl 的 43 题，用与 /chat/agent 相同的生产链路
（意图路由 → 查询改写 → ReAct 检索推理 → 证据门控，stage_intent + stage_react，
不落库、不建会话）为每题生成答案与 top sources，写出标注文件；人工逐题
填 score（0/1/2）后，聚合出 groundedness 分布、平均分与检索交叉表。

用法（从仓库根目录运行）:
    conda activate rag11
    python scripts/eval_groundedness.py                 # 生成标注文件（score 留空）
    python scripts/eval_groundedness.py --limit 3       # 冒烟
    python scripts/eval_groundedness.py --aggregate     # 读标注文件，汇总指标

标注文件：data/eval/groundedness.jsonl（人工把每行 score 从 null 改成 0/1/2，
可加 note 说明判断理由）。

前提：Milvus 已启动、embedding 模型已下载、库内已有入库资料、LLM 通道可用。
"""

import argparse
import json
import sys
from pathlib import Path

# Windows 控制台/重定向默认 GBK，问题文本可能含 − 等数学符号，
# 强制定向 utf-8 避免 print 时 UnicodeEncodeError 中断整轮生成。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.rag.eval.groundedness import aggregate_groundedness, cross_with_retrieval
from backend.app.rag.eval.metrics import evaluate_question, first_rank

TOP_K = 5  # 与 /chat/agent 默认 top_k 一致
MAX_SOURCE_TEXT = 600  # 标注文件里每条来源截断到该长度，控制文件体积


def resolve_documents(registry_path: Path) -> list[dict]:
    """从 document_registry.json 读全部文档（与 eval_retrieval 同构）。"""
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
    """读取 questions.jsonl（与 eval_retrieval 同构校验）。"""
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


def _production_answer(question: str, collections: list[str], llm, gateway) -> dict:
    """跑生产答案链路（stage_intent + stage_react），返回答案 + 排名来源。

    与 /chat/agent 的区别：不建会话、不落库、不做画像注入（profile=None），
    chat_history 为空——评估的是「单轮问答」的 groundedness。
    """
    from backend.app.api.routes_retrieval import ChatRequest, stage_intent, stage_react

    req = ChatRequest(question=question, top_k=TOP_K, scope="auto")
    intent = stage_intent(req, llm, gateway, profile=None)
    react = stage_react(
        req, llm, gateway, intent.decision, intent.rewritten, [], profile=None
    )
    sources = [
        {
            "text": (item["chunk"].get("content") or "")[:MAX_SOURCE_TEXT],
            "document_id": (item["chunk"] or {}).get("document_id", ""),
            "filename": (item["chunk"] or {}).get("filename", ""),
            "score": round(float(item["score"]), 6),
        }
        for item in react.ranked[:TOP_K]
    ]
    return {
        "answer": react.answer,
        "sources": sources,
        "evidence": {
            "sufficient": bool(react.ranked),
            "score_best": round(float(react.ranked[0]["score"]), 6) if react.ranked else None,
        },
        "retrieval": {
            "document_ids": [item["chunk"].get("document_id", "") for item in react.ranked[:TOP_K]],
            "rewritten_question": intent.rewritten if intent.rewritten != question else None,
        },
    }


def generate(args) -> None:
    """为每题生成答案与 top sources，写出标注文件（score 留 null）。"""
    registry_path = PROJECT_ROOT / "data/document_registry.json"
    questions = load_questions(Path(args.questions))
    if args.limit:
        questions = questions[: args.limit]
    documents = resolve_documents(registry_path)
    collections = [doc["collection_name"] for doc in documents]
    print(f"评估集 {len(questions)} 题，文档 {len(documents)} 份，top_k={TOP_K}")

    from backend.app.api.routes_retrieval import _get_agent_llm
    from backend.app.core.config import LLM_MODEL
    from backend.app.rag.catalog import connect_milvus

    model = args.model or LLM_MODEL
    llm = _get_agent_llm(model)
    gateway = _build_gateway()
    connect_milvus()
    print(f"Milvus connected, model={model}")

    out_path = Path(args.infile)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 断点续跑：已存在同 id 的行跳过（覆盖中断/重跑场景，不重复生成已完成的题）
    existing: set[str] = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        existing.add(json.loads(line)["id"])
                    except (ValueError, KeyError):
                        continue
    todo = [q for q in questions if q["id"] not in existing]
    if len(existing) > 0:
        print(f"断点续跑：跳过已存在 {len(existing)} 题，待生成 {len(todo)} 题", flush=True)
    with open(out_path, "a", encoding="utf-8") as fh:
        for q in todo:
            qid = q["id"]
            print(f"[{qid}] {q['question'][:40]}", flush=True)
            try:
                prod = _production_answer(q["question"], collections, llm, gateway)
            except Exception as exc:
                print(f"  ! 生成失败: {exc}", flush=True)
                prod = {
                    "answer": f"<生成失败> {exc}",
                    "sources": [],
                    "evidence": {"sufficient": False, "score_best": None},
                    "retrieval": {"document_ids": [], "rewritten_question": None},
                }
            ranked_doc_ids = prod["retrieval"]["document_ids"]
            record = {
                "id": qid,
                "question": q["question"],
                "expected_document_id": q["document_id"],
                "answer": prod["answer"],
                "sources": prod["sources"],
                "evidence": prod["evidence"],
                "retrieval": prod["retrieval"],
                "retrieval_hit": q["document_id"] in ranked_doc_ids,
                "score": None,  # 人工填写：0 / 1 / 2
                "note": "",
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
    print(f"标注文件写出: {out_path}（score 均为 null，请人工逐条填 0/1/2）")


def aggregate(args) -> None:
    """读标注文件，聚合 groundedness 指标，写出汇总 JSON 并打印。"""
    records = []
    with open(Path(args.infile), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    scored = [r for r in records if r.get("score") is not None]
    if not scored:
        print("标注文件中没有已填 score 的行，请先人工标注后再聚合。")
        return
    if len(scored) < len(records):
        print(f"注意：{len(records) - len(scored)} 行未标注，聚合时跳过。")

    summary = aggregate_groundedness(scored)
    cross = cross_with_retrieval(scored)
    output = {
        "questions": summary["questions"],
        "scored": len(scored),
        "unscored": len(records) - len(scored),
        "summary": summary,
        "cross_with_retrieval": cross,
        "per_question": scored,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)

    dist = summary["distribution"]
    print("\n=== Groundedness 汇总 ===")
    print(f"已标注 {summary['questions']} 题")
    print(f"平均分: {summary['mean_score']:.4f}")
    print(f"fully_grounded(=2): {summary['fully_grounded']:.2%}")
    print(f"grounded(>=1):     {summary['grounded']:.2%}")
    print(f"分布 0/1/2: {dist['0']} / {dist['1']} / {dist['2']}")
    print("\n交叉（expected 是否进 top sources）:")
    for key, row in cross.items():
        print(
            f"  {key:<6} {row['questions']:>3} 题, 平均 {row['mean_score']:.4f}, "
            f"fully_grounded {row['fully_grounded']:.2%}"
        )
    print(f"\n汇总写出: {out_path}")


def _build_gateway():
    """构造与 /chat/agent 一致的 RetrievalGateway（复用生产检索链路）。"""
    from backend.app.api.routes_retrieval import _build_gateway as _prod_gateway

    return _prod_gateway()


def main() -> None:
    parser = argparse.ArgumentParser(description="答案 groundedness 评估（生成 + 人工标注 + 汇总）")
    parser.add_argument("--mode", default="generate", choices=("generate", "aggregate"),
                        help="generate=生成答案标注文件；aggregate=读标注文件汇总指标")
    parser.add_argument("--questions", default=str(PROJECT_ROOT / "data/eval/questions.jsonl"))
    parser.add_argument("--out", default=str(PROJECT_ROOT / "data/eval/groundedness.json"))
    parser.add_argument("--infile", default=str(PROJECT_ROOT / "data/eval/groundedness.jsonl"),
                        help="aggregate 模式的标注输入文件")
    parser.add_argument("--model", default=None, help="回答模型 id，默认取 .env 的 LLM_MODEL")
    parser.add_argument("--limit", type=int, default=0, help="generate 只跑前 N 题（冒烟）")
    args = parser.parse_args()

    if args.mode == "generate":
        generate(args)
    else:
        aggregate(args)


if __name__ == "__main__":
    main()
