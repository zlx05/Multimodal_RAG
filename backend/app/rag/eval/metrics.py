"""文档级检索指标：Recall@K 与 MRR。

本模块只依赖标准库，不 import Milvus / sentence-transformers / app 内模块，
因此 backend/tests 可以在没有向量库和模型的情况下单测这些纯函数。

约定：每题只有一个相关文档（evaluate set 里每行一个 expected_document_id）。
"""

from typing import Iterable, Sequence


def first_rank(ranked_doc_ids: Sequence[str], expected: str) -> int | None:
    """返回 expected 在 ranked_doc_ids 中的第一个位置（1-based），未出现则 None。"""
    for rank, doc_id in enumerate(ranked_doc_ids, start=1):
        if doc_id == expected:
            return rank
    return None


def recall_at_k(ranked_doc_ids: Sequence[str], expected: str, k: int) -> int:
    """正确文档是否出现在前 k 位。单相关文档语义：命中返回 1，否则 0。"""
    if k <= 0:
        return 0
    return 1 if expected in ranked_doc_ids[:k] else 0


def mrr(ranked_doc_ids: Sequence[str], expected: str) -> float:
    """第一个正确命中的倒数排名，未出现则 0.0。"""
    rank = first_rank(ranked_doc_ids, expected)
    return 1.0 / rank if rank else 0.0


def precision_at_k(ranked_doc_ids: Sequence[str], expected: str, k: int) -> float:
    """精确率：前 k 位中相关文档的比例。单相关文档语义下 = 命中则 1/k，否则 0。"""
    if k <= 0:
        return 0.0
    hits = sum(1 for doc_id in ranked_doc_ids[:k] if doc_id == expected)
    return round(hits / k, 4)


def evaluate_question(
    ranked_doc_ids: Sequence[str],
    expected: str,
    ks: tuple[int, ...] = (1, 3, 5),
) -> dict:
    """对单个问题求所有指标，返回 {"recall@k": int, "precision@k": float, ..., "mrr": float}。"""
    result: dict = {}
    for k in ks:
        result[f"recall@{k}"] = recall_at_k(ranked_doc_ids, expected, k)
        result[f"precision@{k}"] = precision_at_k(ranked_doc_ids, expected, k)
    result["mrr"] = mrr(ranked_doc_ids, expected)
    return result


# ---------------------------------------------------------------------------
# 跨文档关系型（LightRAG 缺口验证）指标：每题有多个期望文档。
# 与单相关文档语义不同——评估"关系型问题所需的全部文档是否被召回"，
# 而不是"唯一正确文档是否命中"。
# ---------------------------------------------------------------------------


def doc_coverage_at_k(
    ranked_doc_ids: Sequence[str], expected_docs: Sequence[str], k: int
) -> float:
    """前 k 位命中的期望文档数 / 期望文档总数（去重），衡量"部分召回"程度。

    例如期望 [A, B]，前 3 位含 A 不含 B → 0.5。k<=0 返回 0.0。
    """
    if k <= 0:
        return 0.0
    expected = set(expected_docs)
    if not expected:
        return 0.0
    # 只数"去重后的期望文档"，同一文档多次命中不能重复计分
    found = set(doc_id for doc_id in ranked_doc_ids[:k] if doc_id in expected)
    return round(len(found) / len(expected), 4)


def all_docs_at_k(
    ranked_doc_ids: Sequence[str], expected_docs: Sequence[str], k: int
) -> int:
    """全部期望文档是否都出现在前 k 位（关系型问题能否检索闭环）。命中返回 1，否则 0。"""
    if k <= 0:
        return 0
    expected = set(expected_docs)
    return 1 if expected and expected.issubset(ranked_doc_ids[:k]) else 0


def first_expected_rank(
    ranked_doc_ids: Sequence[str], expected_docs: Sequence[str]
) -> int | None:
    """第一个期望文档的排名（1-based，任意命中即可），全不命中返回 None。

    用于 MRR-any：只要有一个期望文档靠前，agent 就有拼接入口。
    """
    expected = set(expected_docs)
    for rank, doc_id in enumerate(ranked_doc_ids, start=1):
        if doc_id in expected:
            return rank
    return None


def evaluate_relational(
    ranked_doc_ids: Sequence[str],
    expected_docs: Sequence[str],
    ks: tuple[int, ...] = (1, 3, 5),
) -> dict:
    """对单个关系型问题求多文档指标。

    返回 {"doc_coverage@k": float, "all_docs@k": int, "first_rank": int|None, "mrr_any": float}。
    """
    result: dict = {}
    for k in ks:
        result[f"doc_coverage@{k}"] = doc_coverage_at_k(ranked_doc_ids, expected_docs, k)
        result[f"all_docs@{k}"] = all_docs_at_k(ranked_doc_ids, expected_docs, k)
    rank = first_expected_rank(ranked_doc_ids, expected_docs)
    result["first_rank"] = rank
    result["mrr_any"] = 1.0 / rank if rank else 0.0
    return result


def aggregate_relational(
    per_question: Iterable[dict],
    ks: tuple[int, ...] = (1, 3, 5),
) -> dict:
    """汇总多个关系型问题的指标均值（四舍五入到 4 位小数）。"""
    items = list(per_question)
    total = len(items)
    empty = {"questions": 0}
    for k in ks:
        empty[f"doc_coverage@{k}"] = 0.0
        empty[f"all_docs@{k}"] = 0.0
    empty["mrr_any"] = 0.0
    if total == 0:
        return empty

    sums: dict[str, float] = {"mrr_any": 0.0}
    for k in ks:
        sums[f"doc_coverage@{k}"] = 0.0
        sums[f"all_docs@{k}"] = 0.0
    for item in items:
        for key in sums:
            sums[key] += item.get(key, 0.0)
    summary = {"questions": total}
    for key, value in sums.items():
        summary[key] = round(value / total, 4)
    return summary


def aggregate_metrics(
    per_question: Iterable[dict],
    ks: tuple[int, ...] = (1, 3, 5),
) -> dict:
    """汇总多个问题的指标均值（四舍五入到 4 位小数）。

    per_question 的元素是 evaluate_question 的返回值。
    """
    items = list(per_question)
    total = len(items)
    if total == 0:
        return {
            "questions": 0,
            "recall@1": 0.0, "recall@3": 0.0, "recall@5": 0.0,
            "precision@1": 0.0, "precision@3": 0.0, "precision@5": 0.0,
            "mrr": 0.0,
        }

    sums: dict[str, float] = {"mrr": 0.0}
    for k in ks:
        sums[f"recall@{k}"] = 0.0
        sums[f"precision@{k}"] = 0.0

    for item in items:
        for key in sums:
            sums[key] += item.get(key, 0.0)

    summary = {"questions": total}
    for key, value in sums.items():
        summary[key] = round(value / total, 4)
    return summary
