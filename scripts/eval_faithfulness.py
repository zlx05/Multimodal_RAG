"""答案忠诚度（faithfulness）自动评估：RAGAS 风格断言验证，零人工标注。

RAGAS faithfulness 的做法：把答案拆成原子事实断言（claims），逐条对照
检索到的来源判断是否被支持，忠诚度 = 被支持的断言数 / 断言总数。

本脚本复用 eval_groundedness.py 生成好的答案 + 来源（--infile），用 LLM
作为 judge 自动完成「拆断言 + 判支持」两步（一个调用返回结构化 JSON），
聚合出平均忠诚度 + 分布 + 与检索命中的交叉表。全程无需人工标注。

用法（从仓库根目录运行）:
    python scripts/eval_faithfulness.py                          # 默认读 groundedness_new.jsonl
    python scripts/eval_faithfulness.py --infile data/eval/groundedness.jsonl
    python scripts/eval_faithfulness.py --limit 3                # 冒烟
    python scripts/eval_faithfulness.py --out data/eval/faithfulness.json

输出：data/eval/faithfulness.json（含 summary + cross + per_question）。
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.rag.eval.metrics import first_rank

TOP_K = 5
MAX_SOURCE_TEXT = 600  # 来源文本截断长度（与 eval_groundedness 一致）
MAX_CLAIMS = 8  # 单答案最多评估的断言数（防超长答案爆 token）


# ---------------------------------------------------------------- judge prompt

JUDGE_SYSTEM = (
    "你是严谨的检索增强生成（RAG）事实核查员。给定一个用户问题、一段 AI 回答，"
    "以及检索到的参考来源，判断回答中的每个事实断言是否被来源支持。\n"
    "支持判定：\n"
    "- \"supported\"：断言完全能被来源文本证实。\n"
    "- \"partial\"：断言部分成立，或来源只覆盖了部分内容，存在细微出入。\n"
    "- \"unsupported\"：来源无法证实该断言（来源缺失该信息、或断言与来源矛盾、"
    "或断言来自模型自身常识而来源并未提及）。\n"
    "注意：判断依据 ONLY 提供的来源文本，不要用你自身对世界知识的了解来补足。\n"
    "按回答内容原样列出所有事实断言（判断、数据、公式、结论），不要遗漏，也不要合并。"
)


def _build_judge_prompt(question: str, answer: str, sources: list[dict]) -> str:
    source_texts = "\n\n".join(
        f"[来源{i + 1}]（{s.get('filename', '')}）\n{s.get('text', '')}"
        for i, s in enumerate(sources)
    )
    return (
        f"用户问题：{question}\n\n"
        f"AI 回答：\n{answer}\n\n"
        f"检索到的参考来源：\n{source_texts}\n\n"
        "请输出 JSON 对象，形如：\n"
        '{"claims": [{"claim": "断言文本", "verdict": "supported|partial|unsupported", '
        '"evidence": "支持该判定的来源片段或理由"}]}\n'
        "只输出这个 JSON，不要输出其他文字。"
    )


# ---------------------------------------------------------------- JSON 宽容解析

def _extract_json(raw: str) -> dict | None:
    """从模型输出里宽容提取 JSON（剥代码围栏、截取第一个 {...}）。"""
    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------- 忠诚度指标

def faithfulness_score(claims: list[dict]) -> float | None:
    """从断言验证结果算 faithfulness = 支持的断言 / 总断言。空列表返回 None。"""
    if not claims:
        return None
    weights = {"supported": 1.0, "partial": 0.5, "unsupported": 0.0}
    total = len(claims)
    score = sum(weights.get(c.get("verdict", ""), 0.0) for c in claims) / total
    return round(score, 4)


def _short(text: str, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


# ---------------------------------------------------------------- 主流程

def run_judge(records: list[dict], llm) -> list[dict]:
    """对每条答案调用 judge，返回带 faithfulness 评分的 record 列表。"""
    out = []
    for rec in records:
        qid = rec["id"]
        answer = rec.get("answer") or ""
        sources = rec.get("sources") or []
        # 失败/无来源的答案没有判据，标记不可评
        if not answer or answer.startswith("<生成失败>") or not sources:
            rec["faithfulness"] = None
            rec["judge_note"] = "答案缺失或无来源，跳过"
            out.append(rec)
            print(f"[{qid}] 跳过（答案/来源缺失）", flush=True)
            continue

        prompt = _build_judge_prompt(rec["question"], answer[:1500], sources)
        raw = None
        try:
            raw = llm.invoke(
                [
                    ("system", JUDGE_SYSTEM),
                    ("user", prompt),
                ]
            )
            raw_text = raw.content if hasattr(raw, "content") else str(raw)
        except Exception as exc:
            raw_text = ""
            rec["judge_note"] = f"judge 调用失败: {exc}"
            print(f"[{qid}] ! judge 调用失败: {exc}", flush=True)

        data = _extract_json(raw_text) if raw_text else None
        claims = (data or {}).get("claims") if data else None
        if not isinstance(claims, list) or not claims:
            rec["faithfulness"] = None
            rec["judge_note"] = "judge 未返回结构化断言"
            print(f"[{qid}] ! judge 未返回结构化断言", flush=True)
            out.append(rec)
            continue

        claims = claims[:MAX_CLAIMS]
        rec["claims"] = [
            {
                "claim": str(c.get("claim", ""))[:300],
                "verdict": c.get("verdict", "unsupported"),
                "evidence": _short(str(c.get("evidence", "")), 150),
            }
            for c in claims
            if isinstance(c, dict) and c.get("claim")
        ]
        rec["faithfulness"] = faithfulness_score(rec["claims"])
        rec["judge_note"] = ""
        # expected 文档是否进入 top sources（复用 groundedness 的 retrieval_hit 语义）
        ranked_doc_ids = [s.get("document_id", "") for s in sources]
        rec["retrieval_hit"] = rec.get("expected_document_id", "") in ranked_doc_ids
        rec["first_rank"] = first_rank(ranked_doc_ids, rec.get("expected_document_id", ""))
        print(
            f"[{qid}] faithfulness={rec['faithfulness']} "
            f"claims={len(rec['claims'])}",
            flush=True,
        )
        out.append(rec)
    return out


def aggregate(records: list[dict]) -> dict:
    """聚合忠诚度指标：平均分 + 各档占比 + 检索命中交叉表。"""
    scored = [r for r in records if r.get("faithfulness") is not None]
    total = len(records)
    if not scored:
        return {"questions": total, "scored": 0, "mean": 0.0, "distribution": {}, "cross": {}}

    values = [r["faithfulness"] for r in scored]
    n = len(values)
    mean = round(sum(values) / n, 4)
    distribution = {
        "fully_grounded(>=0.9)": round(sum(1 for v in values if v >= 0.9) / n, 4),
        "grounded(>=0.7)": round(sum(1 for v in values if v >= 0.7) / n, 4),
        "weak(<0.7)": round(sum(1 for v in values if v < 0.7) / n, 4),
    }

    buckets = {"hit": [], "miss": []}
    for r in scored:
        buckets["hit" if r.get("retrieval_hit") else "miss"].append(r)
    cross = {}
    for key, group in buckets.items():
        if not group:
            continue
        vals = [r["faithfulness"] for r in group]
        cross[key] = {
            "questions": len(vals),
            "mean": round(sum(vals) / len(vals), 4),
        }
    return {
        "questions": total,
        "scored": n,
        "skipped": total - n,
        "mean_faithfulness": mean,
        "distribution": distribution,
        "cross_with_retrieval": cross,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="答案忠诚度自动评估（RAGAS 风格断言验证，零人工标注）")
    parser.add_argument("--infile", default=str(PROJECT_ROOT / "data/eval/groundedness_new.jsonl"),
                        help="eval_groundedness.py 生成的答案+来源 JSONL")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "data/eval/faithfulness.json"))
    parser.add_argument("--limit", type=int, default=0, help="只评估前 N 题（冒烟）")
    parser.add_argument("--model", default=None, help="judge 模型 id，默认取 .env 的 LLM_MODEL")
    args = parser.parse_args()

    records = []
    with open(Path(args.infile), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    if args.limit:
        records = records[: args.limit]
    print(f"评估 {len(records)} 条答案", flush=True)

    from backend.app.api.routes_retrieval import _get_agent_llm
    from backend.app.core.config import LLM_MODEL

    model = args.model or LLM_MODEL
    llm = _get_agent_llm(model)
    print(f"judge model={model}", flush=True)

    results = run_judge(records, llm)
    summary = aggregate(results)

    output = {"summary": summary, "per_question": results}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)

    print("\n=== Faithfulness 汇总 ===")
    print(f"已评估 {summary['scored']}/{summary['questions']} 题（跳过 {summary['skipped']}）")
    print(f"平均忠诚度: {summary['mean_faithfulness']:.4f}")
    for k, v in summary["distribution"].items():
        print(f"  {k}: {v:.2%}")
    print("\n交叉（expected 是否进 top sources）:")
    for key, row in summary["cross_with_retrieval"].items():
        print(f"  {key:<6} {row['questions']:>3} 题, 平均 {row['mean']:.4f}")
    print(f"\n汇总写出: {out_path}")


if __name__ == "__main__":
    main()
