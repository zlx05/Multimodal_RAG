"""单遍检索 vs ReAct 补检的跨文档关系型评估（LightRAG 缺口的数据支撑）。

在同一份关系型题集（data/eval/questions_relational.jsonl，每题 2~3 个期望文档）上
对比两条链路：

  A. 单遍（single-pass）  _federated_search(question, collections, top_k)
       —— 生产检索工具的首路，与 scripts/eval_relational.py 完全同参。
  B. ReAct                stage_intent（改写+路由） + stage_react（AgentExecutor 自主补检）
       —— 收集 ctx.fused 全部证据（不只 top_k），额外量度「证据级闭环率」：
         只要期望文档出现在 agent 任何一次工具调用的证据里，就认为它可被引用。

指标（复用 backend/app/rag/eval/metrics.py 的 evaluate_relational / aggregate_relational）：
  doc_coverage@K / all_docs@K / mrr_any   —— 与单遍评估同语义
  evidence_all_docs                       —— 全部期望文档是否出现在 ctx.fused 任意位置（无 K 限制）
  tool_calls                              —— ReAct 工具调用次数（补检强度）

逐题归因（用于判断残留缺口的层级）：
  recovered  —— 单遍 top_k 漏掉、但 ReAct 证据里找回了
  residual   —— 单遍与 ReAct 证据都漏 → 召回层/规划层的真实缺口

用法（从仓库根目录运行）:
    conda activate rag11
    python scripts/eval_relational_react.py --limit 3     # 冒烟
    python scripts/eval_relational_react.py               # 全量
    python scripts/eval_relational_react.py --top-k 8     # 与生产检索工具同参

前提：Milvus 已启动、embedding 模型已下载、文档已入库、LLM 通道可用。
"""

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

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
    """按首次出现顺序去重，得到文档级排序（用于指标与人工核对）。"""
    seen: set[str] = set()
    result = []
    for doc_id in doc_ids:
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            result.append(doc_id)
    return result


def _evidence_doc_ranking(fused: dict) -> list[str]:
    """把 ctx.fused 的全部证据按 score 降序转成文档级排序（跨全部工具调用）。"""
    items = sorted(fused.values(), key=lambda item: item["score"], reverse=True)
    return _dedup_keep_order(
        [(item.get("chunk") or {}).get("document_id", "") for item in items]
    )


def _resume_existing(jsonl_path: Path) -> set[str]:
    """读断点 jsonl，返回已完成问题的 id 集合（损坏行跳过）。"""
    done: set[str] = set()
    if not jsonl_path.exists():
        return done
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["id"])
            except (ValueError, KeyError):
                continue
    return done


def _aggregate_per_question(records: list[dict], ks: tuple[int, ...]) -> dict:
    """从逐题记录聚合：单遍 / ReAct 指标 + 证据闭环 + 归因计数。"""
    single_agg = aggregate_relational((p["single_pass"] for p in records), ks=ks)
    react_agg = aggregate_relational((p["react"] for p in records), ks=ks)
    n = len(records)
    closure = sum(p["react"]["evidence_all_docs"] for p in records) / n if n else 0.0
    recovered_total = sum(len(p["attribution"]["recovered_by_react"]) for p in records)
    residual_total = sum(len(p["attribution"]["residual"]) for p in records)
    tool_total = sum(p["react"]["tool_calls"] for p in records)
    return {
        "single_pass": single_agg,
        "react_evidence": react_agg,
        "evidence_closure": round(closure, 4),
        "evidence_closure_questions": sum(1 for p in records if p["react"]["evidence_all_docs"]),
        "recovered_docs": recovered_total,
        "residual_docs": residual_total,
        "avg_tool_calls": round(tool_total / n, 2) if n else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="单遍 vs ReAct 关系型检索评估")
    parser.add_argument("--questions", default=str(PROJECT_ROOT / "data/eval/questions_relational.jsonl"))
    parser.add_argument("--out", default=str(PROJECT_ROOT / "data/eval/results_relational_react.json"))
    parser.add_argument("--top-k", type=int, default=8, help="检索工具每路召回块数（生产默认 8）")
    parser.add_argument("--ks", default="1,3,5", help="评估的 K 列表，逗号分隔")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟）")
    parser.add_argument("--model", default=None, help="agent LLM 模型 id，默认取 .env 的 LLM_MODEL")
    args = parser.parse_args()

    ks = tuple(sorted(int(k) for k in args.ks.split(",") if k.strip()))
    questions = load_relational_questions(Path(args.questions))
    if args.limit:
        questions = questions[: args.limit]
    doc_to_collection = load_document_collections()
    print(f"关系型评估集 {len(questions)} 题，已入库文档 {len(doc_to_collection)} 份，top_k={args.top_k}")

    for q in questions:
        missing = [d for d in q["document_ids"] if d not in doc_to_collection]
        if missing:
            raise ValueError(f"问题 {q['id']} 的期望文档未入库: {missing}")

    from backend.app.api.routes_retrieval import (
        ChatRequest,
        _build_gateway,
        _federated_search,
        _get_agent_llm,
        stage_intent,
        stage_react,
    )
    from backend.app.core.config import LLM_MODEL
    from backend.app.rag.catalog import connect_milvus

    # 断点续跑：逐题落盘到 <out>.jsonl，已完成的题跳过（中断/被杀不重跑已完成部分）。
    out_path = Path(args.out)
    jsonl_path = out_path.with_suffix(".jsonl")
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    done = _resume_existing(jsonl_path)
    todo = [q for q in questions if q["id"] not in done]
    if done:
        print(f"断点续跑：跳过已存在 {len(done)} 题，待生成 {len(todo)} 题", flush=True)

    model = args.model or LLM_MODEL
    llm = _get_agent_llm(model)
    gateway = _build_gateway()
    connect_milvus()
    collections = sorted(set(doc_to_collection.values()))
    print(f"Milvus connected, {len(collections)} collections, model={model}", flush=True)

    per_question = []
    with open(jsonl_path, "a", encoding="utf-8") as jsonl_fh:
        for q in todo:
            qid = q["id"]
            question = q["question"]
            expected = q["document_ids"]
            print(f"[{qid}] {question[:40]}", flush=True)

            # --- A. 单遍 ---
            try:
                fused_single, _routing = _federated_search(question, collections, args.top_k)
                single_docs = _dedup_keep_order(
                    [(item.get("chunk") or {}).get("document_id", "") for item in fused_single[: args.top_k]]
                )
                single_metrics = evaluate_relational(single_docs, expected, ks=ks)
            except Exception as exc:
                print(f"  ! 单遍失败: {exc}", flush=True)
                single_docs, single_metrics = [], {}

            # --- B. ReAct ---
            react_docs, react_metrics, tool_calls, react_failed = [], {}, 0, False
            try:
                req = ChatRequest(question=question, top_k=args.top_k, scope="auto")
                intent = stage_intent(req, llm, gateway, profile=None, chat_history=[])
                react = stage_react(
                    req, llm, gateway, intent.decision, intent.rewritten, [], profile=None
                )
                react_docs = _evidence_doc_ranking(react.ctx.fused)
                react_metrics = evaluate_relational(react_docs, expected, ks=ks)
                tool_calls = len(react.ctx.tool_calls)
            except Exception as exc:
                react_failed = True
                print(f"  ! ReAct 失败: {exc}", flush=True)

            # --- 逐题归因 ---
            expected_set = set(expected)
            single_set = set(single_docs)
            react_set = set(react_docs)
            recovered = sorted((expected_set - single_set) & react_set)
            residual = sorted(expected_set - single_set - react_set)
            evidence_all = int(expected_set and expected_set.issubset(react_set))

            record = {
                "id": qid,
                "question": question,
                "relation": q.get("relation", ""),
                "expected_documents": expected,
                "single_pass": {
                    "doc_ranking": single_docs[: max(ks) * 2],
                    **single_metrics,
                },
                "react": {
                    "evidence_doc_ranking": react_docs[: max(ks) * 2],
                    "evidence_all_docs": evidence_all,
                    "tool_calls": tool_calls,
                    "failed": react_failed,
                    **react_metrics,
                },
                "attribution": {
                    "in_single": sorted(expected_set & single_set),
                    "recovered_by_react": recovered,
                    "residual": residual,
                },
            }
            per_question.append(record)
            jsonl_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            jsonl_fh.flush()

    # --- 汇总：读全量 jsonl（含断点续跑的历史行），保证聚合覆盖所有已完成题 ---
    records = []
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
    if not records:
        print("没有可汇总的记录（可能全部题目都跑挂了）。")
        return
    # 按题目文件顺序排列，去掉重复 id（重跑时后写的覆盖先写的）
    order = {q["id"]: i for i, q in enumerate(questions)}
    by_id: dict[str, dict] = {}
    for r in records:
        by_id[r["id"]] = r
    per_question = [by_id[q["id"]] for q in questions if q["id"] in by_id]
    summary = _aggregate_per_question(per_question, ks=ks)
    single_agg, react_agg = summary["single_pass"], summary["react_evidence"]
    closure = summary["evidence_closure"]
    recovered_total = summary["recovered_docs"]
    residual_total = summary["residual_docs"]
    tool_total = sum(p["react"]["tool_calls"] for p in per_question)
    n = len(per_question)

    # --- 对比表 ---
    header = (
        f"{'id':<7}{'rel':<10}"
        + "".join(f"单遍all@{k:<7}" for k in ks)
        + "".join(f"ReActall@{k:<7}" for k in ks)
        + f"{'闭m证据':<7}{'工具':<5}rec/res"
    )
    print("\n" + header)
    for p in per_question:
        sa = "".join(f"{p['single_pass'].get(f'all_docs@{k}', 0):<11}" for k in ks)
        ra = "".join(f"{p['react'].get(f'all_docs@{k}', 0):<11}" for k in ks)
        closure_mark = "✓" if p["react"]["evidence_all_docs"] else "✗"
        rec = len(p["attribution"]["recovered_by_react"])
        res = len(p["attribution"]["residual"])
        print(f"{p['id']:<7}{p['relation']:<10}{sa}{ra}{closure_mark:<7}{p['react']['tool_calls']:<5}{rec}/{res}")

    print("\n=== 汇总 ===")
    print(f"questions = {single_agg['questions']}")
    for k in ks:
        print(f"  单遍 all_docs@{k}      = {single_agg[f'all_docs@{k}']:.4f}")
        print(f"  ReAct all_docs@{k}     = {react_agg[f'all_docs@{k}']:.4f}")
        print(f"  ReAct cov@{k}          = {react_agg[f'doc_coverage@{k}']:.4f}")
    print(f"  证据级闭环率(任意位置)  = {closure:.4f}   ({summary['evidence_closure_questions']}/{n})")
    print(f"  ReAct 平均工具调用       = {tool_total / n:.2f}  (总 {tool_total})")
    print(f"  单遍漏→ReAct 找回文档    = {recovered_total}  残留缺口文档 = {residual_total}")

    output = {
        "generated_at": None,  # Date 由脚本外标注；保持可重复
        "config": {"top_k": args.top_k, "ks": list(ks), "model": model},
        "metric_semantics": (
            "单遍/ReAct all_docs@K=全部期望文档进前K去重文档的比例；证据级闭环=全部期望文档出现在 "
            "ctx.fused 任意位置（无K限制）；recovered=单遍漏但ReAct证据找到；residual=两者都漏。"
        ),
        "summary": summary,
        "questions": per_question,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)
    print(f"\nresults 写出: {out_path}")


if __name__ == "__main__":
    main()
