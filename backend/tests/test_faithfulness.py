"""答案忠诚度（faithfulness）纯函数测试：无 LLM / 无向量库依赖。"""

from backend.app.rag.eval.metrics import first_rank

from scripts.eval_faithfulness import _extract_json, aggregate, faithfulness_score


def test_faithfulness_score_weights():
    claims = [
        {"verdict": "supported"},
        {"verdict": "supported"},
        {"verdict": "partial"},
        {"verdict": "unsupported"},
    ]
    # (1 + 1 + 0.5 + 0) / 4 = 0.625
    assert faithfulness_score(claims) == 0.625


def test_faithfulness_score_empty_returns_none():
    assert faithfulness_score([]) is None


def test_faithfulness_score_unknown_verdict_counts_zero():
    claims = [{"verdict": "supported"}, {"verdict": "wat"}]
    assert faithfulness_score(claims) == 0.5


def test_extract_json_fenced():
    raw = '```json\n{"claims": [{"claim": "x", "verdict": "supported"}]}\n```'
    data = _extract_json(raw)
    assert data["claims"][0]["verdict"] == "supported"


def test_extract_json_nested_braces_in_content():
    # 断言文本里含花括号（如 LaTeX）时也要能取到完整 JSON
    raw = '{"claims": [{"claim": "平均移动 n/2 个元素，公式 {n/2}", "verdict": "supported"}]}'
    data = _extract_json(raw)
    assert "公式" in data["claims"][0]["claim"]


def test_extract_json_invalid_returns_none():
    assert _extract_json("不是 JSON") is None
    assert _extract_json("") is None
    assert _extract_json("{broken json}") is None


def test_aggregate_means_and_cross():
    records = [
        {"faithfulness": 1.0, "retrieval_hit": True},
        {"faithfulness": 1.0, "retrieval_hit": True},
        {"faithfulness": 0.5, "retrieval_hit": False},
        {"faithfulness": None, "retrieval_hit": True},  # 跳过
    ]
    agg = aggregate(records)
    assert agg["questions"] == 4
    assert agg["scored"] == 3
    assert agg["skipped"] == 1
    # (1 + 1 + 0.5) / 3
    assert agg["mean_faithfulness"] == round(5 / 6, 4)
    assert agg["cross_with_retrieval"]["hit"]["mean"] == 1.0
    assert agg["cross_with_retrieval"]["miss"]["mean"] == 0.5


def test_first_rank_reused_from_metrics():
    # expected 文档在 sources 第 3 位
    assert first_rank(["a", "b", "c"], "c") == 3
    assert first_rank(["a", "b"], "z") is None
