"""画像进化测试：judge 解析（含宽容解析）、薄弱点去重合并、personality 记忆、
风格漂移需连续一致信号防抖、无画像跳过、LLM 异常软失败。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.db import org
from backend.app.rag.profile_evolution import apply_profile_evolution, judge_user


@pytest.fixture
def sqlite_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(org, "_session_factory", sessionmaker(bind=engine))
    return engine


class _JudgeLLM:
    """按预设原文返回 JSON 的假 LLM。"""

    def __init__(self, raw: str):
        self.raw = raw
        self.captured = []

    def invoke(self, prompt):
        self.captured.append(prompt)
        from types import SimpleNamespace

        return SimpleNamespace(content=self.raw)


def test_judge_parses_clean_json():
    llm = _JudgeLLM(
        '{"weak_points": ["换元法"], "style_tendency": "socratic", "behaviors": ["喜欢追问"]}'
    )
    out = judge_user(llm, "q", "a")
    assert out["weak_points"] == ["换元法"]
    assert out["style_tendency"] == "socratic"
    assert out["behaviors"] == ["喜欢追问"]


def test_judge_tolerates_wrapped_json():
    llm = _JudgeLLM(
        '好的，以下是分析：\n```json\n{"weak_points": ["极限"], "style_tendency": null, "behaviors": []}\n```'
    )
    out = judge_user(llm, "q", "a")
    assert out["weak_points"] == ["极限"]
    assert out["style_tendency"] is None


def test_judge_falls_back_on_garbage():
    llm = _JudgeLLM("完全不是 JSON")
    out = judge_user(llm, "q", "a")
    assert out == {"weak_points": [], "style_tendency": None, "behaviors": []}


def test_apply_merges_weak_points_and_personality(sqlite_db):
    sid = org.create_user("学生", role="member")["id"]
    org.upsert_profile(sid, weak_points=["导数"], preferred_style="guiding")
    llm = _JudgeLLM(
        '{"weak_points": ["导数", "换元"], "style_tendency": null, "behaviors": ["先看结论"]}'
    )
    apply_profile_evolution(sid, llm, "q", "a")
    profile = org.get_profile(sid)
    assert "导数" in profile["weak_points"]
    assert "换元" in profile["weak_points"]
    assert profile["profile_version"] >= 2
    memories = org.list_memory(sid)
    assert any(m["memory_type"] == "personality" and m["content"] == "先看结论" for m in memories)


def test_apply_skips_without_profile(sqlite_db):
    sid = org.create_user("无画像学生", role="member")["id"]
    llm = _JudgeLLM('{"weak_points": ["x"], "style_tendency": null, "behaviors": []}')
    apply_profile_evolution(sid, llm, "q", "a")
    assert org.get_profile(sid) is None  # 不给无画像用户自动建画像
    assert org.list_memory(sid) == []


def test_apply_style_drift_needs_consensus(sqlite_db):
    sid = org.create_user("学生", role="member")["id"]
    org.upsert_profile(sid, preferred_style="guiding")
    judge = '{"weak_points": [], "style_tendency": "socratic", "behaviors": []}'
    # 第一次 socratic 信号：只有一个，不漂移
    apply_profile_evolution(sid, _JudgeLLM(judge), "q", "a")
    assert org.get_profile(sid)["preferred_style"] == "guiding"
    # 第二次 socratic 信号：达到连续一致共识，漂移
    apply_profile_evolution(sid, _JudgeLLM(judge), "q2", "a2")
    assert org.get_profile(sid)["preferred_style"] == "socratic"


def test_apply_soft_fails_on_llm_error(sqlite_db):
    sid = org.create_user("学生", role="member")["id"]
    org.upsert_profile(sid, preferred_style="guiding")

    class _Exploding:
        def invoke(self, prompt):
            raise RuntimeError("模型挂了")

    apply_profile_evolution(sid, _Exploding(), "q", "a")  # 不抛异常
    assert org.get_profile(sid)["preferred_style"] == "guiding"
