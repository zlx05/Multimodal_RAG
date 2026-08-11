"""文档级检索指标纯函数测试（无 Milvus / 无模型依赖）。"""

from backend.app.rag.eval.metrics import (
    aggregate_metrics,
    aggregate_relational,
    all_docs_at_k,
    doc_coverage_at_k,
    evaluate_question,
    evaluate_relational,
    first_expected_rank,
    first_rank,
    mrr,
    precision_at_k,
    recall_at_k,
)

EXPECTED = "doc_a"
RANKED = ["doc_b", "doc_a", "doc_c", "doc_a"]


def test_first_rank_returns_1_based_position():
    assert first_rank(RANKED, "doc_a") == 2
    assert first_rank(RANKED, "doc_c") == 3


def test_first_rank_none_when_absent():
    assert first_rank(RANKED, "doc_z") is None
    assert first_rank([], "doc_a") is None


def test_first_rank_uses_first_occurrence():
    # doc_a 出现两次，应返回第一个位置 2，而不是 4
    assert first_rank(RANKED, "doc_a") == 2


def test_recall_at_k_boundary():
    # doc_a 在第 2 位：@1 不命中，@2 命中，@3 命中
    assert recall_at_k(RANKED, EXPECTED, 1) == 0
    assert recall_at_k(RANKED, EXPECTED, 2) == 1
    assert recall_at_k(RANKED, EXPECTED, 5) == 1


def test_recall_at_k_non_positive_k_returns_0():
    assert recall_at_k(RANKED, EXPECTED, 0) == 0
    assert recall_at_k(RANKED, EXPECTED, -1) == 0


def test_recall_at_k_absent_doc_is_0():
    assert recall_at_k(RANKED, "doc_z", 5) == 0


def test_mrr_is_inverse_of_first_rank():
    assert mrr(RANKED, "doc_a") == 0.5
    assert mrr(["doc_a", "doc_b"], "doc_a") == 1.0
    assert mrr(["doc_b", "doc_c"], "doc_a") == 0.0


def test_evaluate_question_shape():
    # RANKED = ["doc_b", "doc_a", "doc_c", "doc_a"]：doc_a 在 top-3 出现 1 次、top-5 出现 2 次
    result = evaluate_question(RANKED, EXPECTED)
    assert result == {
        "recall@1": 0, "recall@3": 1, "recall@5": 1,
        "precision@1": 0.0, "precision@3": round(1 / 3, 4), "precision@5": 0.4,
        "mrr": 0.5,
    }


def test_evaluate_question_custom_ks():
    result = evaluate_question(RANKED, EXPECTED, ks=(2,))
    assert result == {"recall@2": 1, "precision@2": 0.5, "mrr": 0.5}


def test_precision_at_k_semantics():
    # 前 k 位中相关文档占比：命中 1 个则 1/k，多个累加，未命中则 0
    assert precision_at_k(["doc_a", "doc_b", "doc_c"], "doc_a", 1) == 1.0
    assert precision_at_k(["doc_a", "doc_b", "doc_c"], "doc_a", 3) == round(1 / 3, 4)
    assert precision_at_k(["doc_a", "doc_b", "doc_a"], "doc_a", 3) == round(2 / 3, 4)
    assert precision_at_k(["doc_a", "doc_b", "doc_c"], "doc_z", 3) == 0.0
    assert precision_at_k(["doc_a", "doc_b", "doc_c"], "doc_a", 0) == 0.0


def test_aggregate_metrics_means():
    per_question = [
        evaluate_question(["doc_a"], "doc_a"),   # r1, mrr 1.0
        evaluate_question(["doc_b"], "doc_a"),   # 全 0
        evaluate_question(["doc_c", "doc_a"], "doc_a"),  # r3,r5, mrr 0.5
    ]
    summary = aggregate_metrics(per_question)
    assert summary["questions"] == 3
    assert summary["recall@1"] == round(1 / 3, 4)
    assert summary["recall@3"] == round(2 / 3, 4)
    assert summary["recall@5"] == round(2 / 3, 4)
    assert summary["mrr"] == round((1.0 + 0.0 + 0.5) / 3, 4)


def test_aggregate_metrics_empty():
    summary = aggregate_metrics([])
    assert summary["questions"] == 0
    assert summary["mrr"] == 0.0


def test_aggregate_metrics_accepts_generator():
    gen = (evaluate_question(["doc_a"], "doc_a") for _ in range(2))
    summary = aggregate_metrics(gen)
    assert summary["questions"] == 2
    assert summary["recall@1"] == 1.0


# --- 跨文档关系型指标（P3.1，LightRAG 缺口验证） ---

EXPECTED_TWO = ["doc_a", "doc_c"]
# RANKED = ["doc_b", "doc_a", "doc_c", "doc_a"]：期望 [A, C]，A@2、C@3


def test_doc_coverage_at_k_partial_recall():
    # 期望 [A, C]：@1 只含 B → 0；@2 含 A → 0.5；@3 含 A、C → 1.0
    assert doc_coverage_at_k(RANKED, EXPECTED_TWO, 1) == 0.0
    assert doc_coverage_at_k(RANKED, EXPECTED_TWO, 2) == 0.5
    assert doc_coverage_at_k(RANKED, EXPECTED_TWO, 3) == 1.0
    assert doc_coverage_at_k(RANKED, EXPECTED_TWO, 0) == 0.0


def test_doc_coverage_at_k_dedups_expected():
    # 期望文档去重：同一文档即使出现多次也只算一个
    assert doc_coverage_at_k(["doc_a", "doc_a"], ["doc_a"], 2) == 1.0
    assert doc_coverage_at_k(["doc_a", "doc_a"], ["doc_a", "doc_a"], 2) == 1.0


def test_all_docs_at_k_requires_all_expected():
    assert all_docs_at_k(RANKED, EXPECTED_TWO, 2) == 0  # A@2 但 C 不在前 2
    assert all_docs_at_k(RANKED, EXPECTED_TWO, 3) == 1  # A、C 都在前 3
    assert all_docs_at_k(RANKED, EXPECTED_TWO, 5) == 1
    assert all_docs_at_k(RANKED, EXPECTED_TWO, 0) == 0


def test_first_expected_rank_any_hit():
    assert first_expected_rank(RANKED, EXPECTED_TWO) == 2  # A@2
    assert first_expected_rank(RANKED, ["doc_z"]) is None
    assert first_expected_rank([], EXPECTED_TWO) is None


def test_evaluate_relational_shape():
    result = evaluate_relational(RANKED, EXPECTED_TWO, ks=(1, 2, 3, 5))
    assert result["doc_coverage@1"] == 0.0
    assert result["doc_coverage@2"] == 0.5
    assert result["doc_coverage@3"] == 1.0
    assert result["all_docs@2"] == 0  # C@3 不在前 2
    assert result["all_docs@3"] == 1  # A、C 都在前 3
    assert result["all_docs@5"] == 1
    assert result["first_rank"] == 2
    assert result["mrr_any"] == 0.5


def test_aggregate_relational_means():
    per_question = [
        evaluate_relational(["doc_a", "doc_c"], EXPECTED_TWO, ks=(1, 2)),  # 全命中 first=1
        evaluate_relational(["doc_b", "doc_a"], EXPECTED_TWO, ks=(1, 2)),  # 只 A → coverage@2=0.5, all=0
    ]
    summary = aggregate_relational(per_question, ks=(1, 2))
    assert summary["questions"] == 2
    assert summary["all_docs@2"] == 0.5
    assert summary["doc_coverage@2"] == 0.75
    assert summary["mrr_any"] == round((1.0 + 0.5) / 2, 4)


def test_aggregate_relational_empty():
    summary = aggregate_relational([])
    assert summary["questions"] == 0
    assert summary["mrr_any"] == 0.0
    assert summary["all_docs@3"] == 0.0
