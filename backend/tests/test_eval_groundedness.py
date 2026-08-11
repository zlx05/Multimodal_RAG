"""groundedness 指标纯函数测试（无 Milvus / 无模型依赖）。"""

import pytest

from backend.app.rag.eval.groundedness import (
    aggregate_groundedness,
    cross_with_retrieval,
    score_distribution,
    validate_score,
    iter_scored,
)

R0 = {"score": 0, "retrieval_hit": True}
R1 = {"score": 1, "retrieval_hit": False}
R2 = {"score": 2, "retrieval_hit": True}


def test_validate_score_accepts_012():
    assert validate_score(0) == 0
    assert validate_score(1) == 1
    assert validate_score(2) == 2


def test_validate_score_rejects_out_of_range():
    for bad in (-1, 3, 5):
        with pytest.raises(ValueError):
            validate_score(bad)


def test_score_distribution_counts_each_level():
    dist = score_distribution([R0, R1, R2, R2])
    assert dist == {"0": 1, "1": 1, "2": 2}


def test_aggregate_groundedness_means_and_rates():
    summary = aggregate_groundedness([R0, R1, R2, R2])
    assert summary["questions"] == 4
    assert summary["mean_score"] == round((0 + 1 + 2 + 2) / 4, 4)
    assert summary["fully_grounded"] == 0.5
    assert summary["grounded"] == 0.75
    assert summary["distribution"] == {"0": 1, "1": 1, "2": 2}


def test_aggregate_groundedness_empty():
    summary = aggregate_groundedness([])
    assert summary["questions"] == 0
    assert summary["mean_score"] == 0.0
    assert summary["grounded"] == 0.0
    assert summary["distribution"] == {"0": 0, "1": 0, "2": 0}


def test_cross_with_retrieval_splits_by_hit():
    records = [
        {"score": 2, "retrieval_hit": True},
        {"score": 2, "retrieval_hit": True},
        {"score": 0, "retrieval_hit": False},
        {"score": 0, "retrieval_hit": True},
    ]
    cross = cross_with_retrieval(records)
    assert cross["hit"]["questions"] == 3
    assert cross["miss"]["questions"] == 1
    assert cross["hit"]["mean_score"] == round(4 / 3, 4)
    assert cross["miss"]["mean_score"] == 0.0


def test_cross_with_retrieval_empty_bucket():
    records = [{"score": 1, "retrieval_hit": True}]
    cross = cross_with_retrieval(records)
    assert cross["hit"]["questions"] == 1
    assert cross["miss"]["questions"] == 0


def test_iter_scored_filters_null():
    records = [
        {"score": 2},
        {"score": None},
        {"score": 0},
    ]
    scored = iter_scored(records)
    assert [r["score"] for r in scored] == [2, 0]
