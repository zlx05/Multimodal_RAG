"""对话自动压缩测试：_load_chat_history 摘要前置、_maybe_compress_conversation 折叠。

复用 org 的 SQLite 内存库注入；压缩函数与 org 共享同一个 _session_factory。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.db import org
from backend.app.api.routes_retrieval import (
    HISTORY_MAX_TOKENS,
    HISTORY_MSG_CHARS,
    RECENT_WINDOW,
    _load_chat_history,
    _maybe_compress_conversation,
    _summarize,
)


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


class _FakeLLM:
    """返回固定摘要的假 LLM，captured 记录收到的 prompt。"""

    def __init__(self, summary: str = "压缩后的摘要"):
        self.summary = summary
        self.captured = []

    def invoke(self, prompt):
        self.captured.append(prompt)
        from types import SimpleNamespace

        return SimpleNamespace(content=self.summary)


def _fill_messages(conversation_id: str, turns: int):
    for i in range(turns):
        org.add_message(conversation_id, "user", f"问题{i}", model="m")
        org.add_message(conversation_id, "assistant", f"回答{i}", model="m")


def test_load_chat_history_prepends_summary(sqlite_db):
    conv = org.create_conversation("u_1")
    org.add_message(conv["id"], "system", "此前摘要", metadata_json='{"summary": true}')
    org.add_message(conv["id"], "user", "新问题", model="m")
    org.add_message(conv["id"], "assistant", "新回答", model="m")
    history = _load_chat_history(conv["id"], "u_1")
    assert history[0].type == "system"
    assert "此前摘要" in history[0].content
    assert [m.type for m in history] == ["system", "human", "ai"]


def test_load_chat_history_keeps_only_recent_window(sqlite_db):
    conv = org.create_conversation("u_1")
    _fill_messages(conv["id"], RECENT_WINDOW + 4)
    history = _load_chat_history(conv["id"], "u_1")
    assert len(history) == RECENT_WINDOW
    # 只保留最近的消息
    assert history[-1].content == f"回答{RECENT_WINDOW + 3}"


def test_compress_folds_old_messages(sqlite_db):
    conv = org.create_conversation("u_1")
    _fill_messages(conv["id"], 8)  # 16 条 > 14 阈值
    fake = _FakeLLM("折叠后的摘要")
    _maybe_compress_conversation(conv["id"], fake)
    messages = org.list_messages(conv["id"])
    roles = [m["role"] for m in messages]
    # 1 条摘要 + 最近 RECENT_WINDOW 条原文
    assert roles.count("system") == 1
    assert len(messages) == 1 + RECENT_WINDOW
    summary = next(m for m in messages if m["role"] == "system")
    assert summary["content"] == "折叠后的摘要"
    assert "summary" in summary["metadata_json"]


def test_compress_below_threshold_is_noop(sqlite_db):
    conv = org.create_conversation("u_1")
    _fill_messages(conv["id"], 4)  # 8 条 ≤ 阈值
    fake = _FakeLLM()
    _maybe_compress_conversation(conv["id"], fake)
    assert len(org.list_messages(conv["id"])) == 8
    assert fake.captured == []  # 未触发摘要调用


def test_summarize_receives_existing_and_folded(sqlite_db):
    fake = _FakeLLM("新摘要")
    out = _summarize(fake, "旧摘要", "新对话")
    assert out == "新摘要"
    prompt = fake.captured[0]
    assert "旧摘要" in prompt
    assert "新对话" in prompt


def test_compress_repeated_keeps_bounded(sqlite_db):
    """压缩之后继续对话，消息数始终有界（1 摘要 + 最近窗口条）。"""
    conv = org.create_conversation("u_1")
    _fill_messages(conv["id"], 8)
    _maybe_compress_conversation(conv["id"], _FakeLLM("摘要一"))
    for i in range(3):
        _fill_messages(conv["id"], 3)
        _maybe_compress_conversation(conv["id"], _FakeLLM(f"摘要{i}"))
    messages = org.list_messages(conv["id"])
    assert sum(1 for m in messages if m["role"] == "system") <= 1
    assert len(messages) <= 1 + RECENT_WINDOW


def test_load_chat_history_budget_drops_oldest_long_messages(sqlite_db):
    """历史 token 超预算时，从最旧开始丢长消息，摘要与最新原文保留。"""
    conv = org.create_conversation("u_1")
    org.add_message(conv["id"], "system", "摘要", metadata_json='{"summary": true}')
    for i in range(3):  # 6 条长消息（各约 800 字）→ 远超 HISTORY_MAX_TOKENS
        org.add_message(conv["id"], "user", "旧" * 800, model="m")
        org.add_message(conv["id"], "assistant", "旧答" * 800, model="m")
    org.add_message(conv["id"], "user", "新问题", model="m")
    org.add_message(conv["id"], "assistant", "新回答", model="m")

    history = _load_chat_history(conv["id"], "u_1")
    assert history[0].type == "system"  # 摘要保留
    assert history[-1].content == "新回答"  # 最新保留
    assert any(getattr(m, "content", "") == "新问题" for m in history)
    # 长消息被预算挤掉一部分：实际带的原文条数 < RECENT_WINDOW
    assert len(history) < 1 + RECENT_WINDOW


def test_load_chat_history_keeps_newest_even_over_budget(sqlite_db):
    """最新一条超长消息截断后强制保留（保证追问有紧邻上下文），最旧超预算丢弃。"""
    conv = org.create_conversation("u_1")
    org.add_message(conv["id"], "user", "旧" * (HISTORY_MSG_CHARS + 500), model="m")
    org.add_message(conv["id"], "assistant", "新" * (HISTORY_MSG_CHARS + 500), model="m")

    history = _load_chat_history(conv["id"], "u_1")
    # 最新一条被截断到 HISTORY_MSG_CHARS 且必然在
    assert history[-1].content == "新" * HISTORY_MSG_CHARS
    # 最旧的超预算消息被丢
    assert all(getattr(m, "content", "") != "旧" * (HISTORY_MSG_CHARS + 500) for m in history)
