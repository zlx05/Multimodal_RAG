"""从 groundedness.jsonl 生成可人工标注的 review 视图 markdown。

用法（从仓库根目录运行）:
    python scripts/gen_review_view.py [--infile data/eval/groundedness.jsonl] [--out data/eval/groundedness_review.md]
"""
import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = Path(__file__).resolve().parents[1]

TITLE = """# Groundedness 标注视图（43 题）

逐题判断 **answer** 是否被 **sources** 支持，把 **0 / 1 / 2** 填到题号后的空格里。

- **2**：答案关键点都能在来源中找到，引用编号合理
- **1**：部分有来源支撑、部分没有，或引用对不上
- **0**：来源不支持 / 编造

> 来源 [n] 与答案里的 [n] 引用编号对应。retrieval_hit = expected 文档是否进了 top sources。
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--infile", default=str(PROJECT_ROOT / "data/eval/groundedness.jsonl"))
    parser.add_argument("--out", default=str(PROJECT_ROOT / "data/eval/groundedness_review.md"))
    args = parser.parse_args()

    records = []
    with open(args.infile, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    lines = [TITLE]
    for r in records:
        qid = r["id"]
        lines.append("---\n")
        lines.append(f"### {qid}   score: ____")
        lines.append("")
        lines.append(f"**问题**：{r['question']}")
        lines.append("")
        expected = r.get("expected_document_id", "")
        hit = "OK" if r.get("retrieval_hit") else "MISS"
        lines.append(f"**expected**：`{expected}`  ·  **retrieval_hit**：{hit}  ·  **sources**：{len(r.get('sources', []))} 条")
        lines.append("")
        lines.append("**答案**：")
        lines.append("")
        lines.append(f"> {r.get('answer', '').strip()}")
        lines.append("")
        lines.append("**来源**：")
        for i, s in enumerate(r.get("sources", []), start=1):
            text = s.get("text", "").replace("\n", " ")
            if len(text) > 160:
                text = text[:160] + "..."
            lines.append(f"- **[{i}]** `{s.get('filename', '')}` (score={s.get('score', 0):.4f})：{text}")
        lines.append("")

    out_path = Path(args.out)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"标注视图写出: {out_path}（{len(records)} 题）")


if __name__ == "__main__":
    main()
