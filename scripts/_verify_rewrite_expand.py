# -*- coding: utf-8 -*-
"""手工验证（真实 LLM）：改写上下文感知消指代 + 问题扩展。一次性临时脚本。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from langchain_core.messages import AIMessage, HumanMessage

from backend.app.api.routes_retrieval import _get_agent_llm
from backend.app.rag.agent_rag import expand_query, rewrite_query

llm = _get_agent_llm()

print("=== 1) 改写上下文感知：消解「他/它」===")
history = [
    HumanMessage("go语言的数据类型是什么"),
    AIMessage("Go 语言的基本数据类型有 int、float64、bool、string，以及切片、映射等复合类型。"),
]
for q in ("他的变量声明", "它的输入输出怎么写", "那指针呢"):
    out = rewrite_query(llm, q, history)
    print(f"  历史前文: 「go语言的数据类型是什么」 → 提问「{q}」")
    print(f"    改写后: 「{out}」")

print()
print("=== 2) 问题扩展：broad 问题拆子主题 ===")
out = expand_query(llm, "go语言怎么学")
print("  提问: 「go语言怎么学」")
print("  扩展子问题:")
for s in out:
    print(f"    - {s}")

print()
print("=== 3) 问题扩展：单点问题应回退空列表 ===")
out2 = expand_query(llm, "Go 中切片扩容的算法是什么")
print("  提问: 「Go 中切片扩容的算法是什么」")
print("  扩展子问题:", out2 if out2 else "[]（单点问题，无扩展，正确）")
