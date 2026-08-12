"""澄清门控（Phase 5）三指标对比评估：同一题集「门控 OFF」vs「门控 ON」。

对每题只跑一次生产检索链路（stage_intent + stage_react，与 /chat/agent 相同），
然后对**同一份** react.ranked 分别评估：
  - OFF（旧系统，无门控）：答案恒为 react.answer（证据不足也硬答）。
  - ON（新系统，门控开）：`run_clarification_gate` 在 no_evidence/weak_evidence 时
    返回澄清轮（固定引导文案 + 1-2 个澄清问题），否则正常 react.answer。

共用同一检索结果 → 隔离「门控」这一变量本身的影响，检索层召回不变。

三指标（LLM-as-judge，全自动，零人工标注）：
  - 准确率 accuracy：回答是否直接、正确回答了问题（0/1）。反问轮 = 0（未直接回答）。
  - 忠诚度 faithfulness：RAGAS 风格断言验证（拆断言判 supported/partial/unsupported）。
    反问轮无事实断言 → 判 1.0（未编造任何事实，比硬凑更忠实）。
  - 召回率 recall（回答覆盖层）：能产出真实回答的问题比例 = 1 - 反问率。
    （检索层召回由 eval_retrieval/eval_relational 承担，本脚本不改检索。）

用法（从仓库根目录运行）:
    conda activate rag11
    python scripts/eval_clarification.py                        # 默认 10 题混合集
    python scripts/eval_clarification.py --limit 3              # 冒烟
    python scripts/eval_clarification.py --questions data/eval/questions.jsonl   # 全量 74 题
    python scripts/eval_clarification.py --out data/eval/clarification_ab.json

输出：<out>.json（含 summary + per_question），关键看点：
    bq001(cls001) 真模糊是否触发（只证据不足触发能否兜住真模糊——核心假设验证）；
    反问率高 → 量化「覆盖损失」vs「错误答案减少」的权衡。
"""

import argparse
import json
import sys
from pathlib import Path

# Windows 控制台/重定向默认 GBK，问题文本可能含 − 等符号，强制 utf-8。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.eval_faithfulness import (
    JUDGE_SYSTEM,
    _build_judge_prompt,
    _extract_json,
    faithfulness_score,
)

TOP_K = 5  # 与 /chat/agent 默认 top_k 一致
MAX_SOURCE_TEXT = 600  # judge 来源文本截断长度（与 eval_faithfulness 一致）


# ---------------------------------------------------------------- judge prompts

ACCURACY_SYSTEM = (
    "你是严谨的问答质量评审员。给定一个用户问题与一个 AI 回答，判断该回答是否"
    "直接、正确地回答了问题。\n"
    "- correct：回答直接给出了问题所需的信息，且内容正确、切题。\n"
    "- incorrect：回答错误、答非所问、回避问题，或反过来要求用户澄清/反问。\n"
    "注意：反问澄清（要求用户提供更多信息）视为「没有回答问题」，判 incorrect。\n"
    '只输出 JSON：{"correct": true|false, "reason": "一句话理由"}'
)


def _accuracy_judge(question: str, answer: str, llm) -> float | None:
    """回答准确率 judge：0/1（是否直接正确回答）。调用失败返回 None。"""
    user = (
        f"用户问题：{question}\n\nAI 回答：\n{answer[:1200]}\n\n"
        '输出 JSON：{"correct": true|false, "reason": "一句话理由"}'
    )
    try:
        raw = llm.invoke([("system", ACCURACY_SYSTEM), ("user", user)])
        text = getattr(raw, "content", None) or str(raw)
    except Exception as exc:
        print(f"  ! accuracy judge 调用失败: {exc}", flush=True)
        return None
    data = _extract_json(text)
    if not data or "correct" not in data:
        return None
    return 1.0 if data["correct"] is True else 0.0


def _faithfulness_judge(question: str, answer: str, sources: list[dict], llm) -> float | None:
    """RAGAS 风格忠诚度 judge：被支持的断言 / 总断言。无来源/无断言返回 None。"""
    if not sources or not answer:
        return None
    prompt = _build_judge_prompt(question, answer[:1500], sources)
    try:
        raw = llm.invoke([("system", JUDGE_SYSTEM), ("user", prompt)])
        text = getattr(raw, "content", None) or str(raw)
    except Exception as exc:
        print(f"  ! faithfulness judge 调用失败: {exc}", flush=True)
        return None
    data = _extract_json(text)
    claims = (data or {}).get("claims")
    if not isinstance(claims, list) or not claims:
        return None
    claims = [
        {"verdict": c.get("verdict", "unsupported")}
        for c in claims[:8]
        if isinstance(c, dict) and c.get("claim")
    ]
    return faithfulness_score(claims)


def _evidence_sources(ranked: list[dict]) -> list[dict]:
    """从 react.ranked 构造 judge 用 sources（与 eval_groundedness 同构）。"""
    return [
        {
            "text": (item["chunk"].get("content") or "")[:MAX_SOURCE_TEXT],
            "document_id": (item["chunk"] or {}).get("document_id", ""),
            "filename": (item["chunk"] or {}).get("filename", ""),
            "score": round(float(item["score"]), 6),
        }
        for item in ranked[:TOP_K]
    ]


# ---------------------------------------------------------------- 单题 A/B

def run_question_ab(q: dict, llm, gateway) -> dict:
    """单题：一次生产检索 → OFF/ON 两路径 → 三指标 judge。"""
    import backend.app.api.routes_retrieval as rr
    from backend.app.api.routes_retrieval import ChatRequest, stage_intent, stage_react

    question = q["question"]
    req = ChatRequest(question=question, top_k=TOP_K, scope="auto")
    intent = stage_intent(req, llm, gateway, profile=None, chat_history=[])
    react = stage_react(
        req, llm, gateway, intent.decision, intent.rewritten, [], profile=None,
        expansions=intent.expansions,
    )
    sources = _evidence_sources(react.ranked)
    rewritten = intent.rewritten

    # --- OFF：无门控，恒硬答 ---
    answer_off = react.answer

    # --- ON：门控开，证据不足则反问 ---
    rr.CLARIFICATION_GATE_ENABLED = True
    gate = rr.run_clarification_gate(llm, question, rewritten, react.ranked)
    if gate is not None:
        answer_on = gate.prompt
        triggered = True
        questions = list(gate.questions)
        reason = gate.reason
    else:
        answer_on = react.answer
        triggered = False
        questions = []
        reason = "sufficient"

    # --- 三指标 judge ---
    # 准确率：反问轮未直接回答 → 0；但 ON 反问轮无事实断言 → 忠诚度判 1.0（未编造）。
    acc_off = _accuracy_judge(question, answer_off, llm)
    acc_on = _accuracy_judge(question, answer_on, llm)
    faith_off = _faithfulness_judge(question, answer_off, sources, llm)
    if triggered:
        faith_on = 1.0  # 澄清轮无事实断言，不背叛证据
    else:
        faith_on = _faithfulness_judge(question, answer_on, sources, llm)

    return {
        "id": q["id"],
        "question": question,
        "relation": q.get("relation", ""),
        "rewritten": rewritten,
        "gate_triggered": triggered,
        "gate_reason": reason,
        "clarification_questions": questions,
        "evidence_best_score": round(float(react.ranked[0]["score"]), 6) if react.ranked else None,
        "evidence_n": len(react.ranked),
        "off": {
            "answer": answer_off,
            "accuracy": acc_off,
            "faithfulness": faith_off,
            "answerable": True,  # OFF 恒硬答
        },
        "on": {
            "answer": answer_on,
            "accuracy": acc_on,
            "faithfulness": faith_on,
            "answerable": not triggered,  # 触发反问 = 未产出真实回答
        },
    }


# ---------------------------------------------------------------- 汇总

def aggregate(records: list[dict]) -> dict:
    def _bucket(key: str) -> dict:
        mean = lambda vals: round(sum(v for v in vals if v is not None) / len(vals), 4) if vals else None
        vals = [r[key]["accuracy"] for r in records if r[key]["accuracy"] is not None]
        faith = [r[key]["faithfulness"] for r in records if r[key]["faithfulness"] is not None]
        answerable = sum(1 for r in records if r[key]["answerable"])
        return {
            "questions": len(records),
            "accuracy_mean": mean(vals),
            "accuracy_judged": len(vals),
            "faithfulness_mean": mean(faith),
            "faithfulness_judged": len(faith),
            "answerable": answerable,
            "answerable_rate": round(answerable / len(records), 4),
        }

    triggered = [r for r in records if r["gate_triggered"]]
    reasons = {}
    for r in records:
        reasons[r["gate_reason"]] = reasons.get(r["gate_reason"], 0) + 1
    return {
        "off": _bucket("off"),
        "on": _bucket("on"),
        "clarification_rate": round(len(triggered) / len(records), 4),
        "triggered_ids": [r["id"] for r in triggered],
        "trigger_reasons": reasons,
        "vague_ids": [r["id"] for r in records if r.get("relation") == "vague"],
        "vague_triggered": [r["id"] for r in records if r.get("relation") == "vague" and r["gate_triggered"]],
    }


def _print_summary(summary: dict) -> None:
    off, on = summary["off"], summary["on"]
    print("\n=== 澄清门控 三指标对比（OFF vs ON）===")
    print(f"题量: {off['questions']}   反问率: {summary['clarification_rate']:.2%}")
    print(f"触发反问: {summary['triggered_ids']}")
    print(f"理由分布: {summary['trigger_reasons']}")
    print(f"真模糊题触发: {summary['vague_triggered']}（全部真模糊: {summary['vague_ids']}）")
    print("\n" + " " * 6 + f"{'OFF(旧)':>12} {'ON(新)':>12}")
    fmt = lambda v: f"{v:.4f}" if v is not None else "  N/A"
    print(f"{'准确率':<10}{fmt(off['accuracy_mean']):>12} {fmt(on['accuracy_mean']):>12}")
    print(f"{'忠诚度':<10}{fmt(off['faithfulness_mean']):>12} {fmt(on['faithfulness_mean']):>12}")
    print(f"{'可答率':<10}{off['answerable_rate']:>12} {on['answerable_rate']:>12}")


def main() -> None:
    parser = argparse.ArgumentParser(description="澄清门控三指标对比评估（OFF vs ON，同一检索 A/B）")
    parser.add_argument("--questions", default=str(PROJECT_ROOT / "data/eval/questions_clarification.jsonl"))
    parser.add_argument("--out", default=str(PROJECT_ROOT / "data/eval/clarification_ab.json"))
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟）")
    parser.add_argument("--model", default=None, help="agent + judge 模型 id，默认取 .env 的 LLM_MODEL")
    args = parser.parse_args()

    questions = []
    with open(Path(args.questions), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                questions.append(json.loads(line))
    if args.limit:
        questions = questions[: args.limit]
    print(f"评估集 {len(questions)} 题（top_k={TOP_K}）", flush=True)

    from backend.app.api.routes_retrieval import _build_gateway, _get_agent_llm
    from backend.app.core.config import LLM_MODEL
    from backend.app.rag.catalog import connect_milvus

    model = args.model or LLM_MODEL
    llm = _get_agent_llm(model)
    gateway = _build_gateway()
    connect_milvus()
    print(f"Milvus connected, model={model}", flush=True)

    records = []
    for q in questions:
        qid = q["id"]
        print(f"[{qid}] {q['question'][:40]}", flush=True)
        try:
            rec = run_question_ab(q, llm, gateway)
        except Exception as exc:
            print(f"  ! 失败: {exc}", flush=True)
            rec = {
                "id": qid, "question": q["question"], "relation": q.get("relation", ""),
                "rewritten": None, "gate_triggered": None, "gate_reason": "error",
                "clarification_questions": [], "evidence_best_score": None, "evidence_n": 0,
                "off": {"answer": "", "accuracy": None, "faithfulness": None, "answerable": True},
                "on": {"answer": "", "accuracy": None, "faithfulness": None, "answerable": True},
            }
        records.append(rec)

    summary = aggregate(records)
    output = {"summary": summary, "per_question": records}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)

    _print_summary(summary)
    print(f"\n汇总写出: {out_path}")


if __name__ == "__main__":
    main()
