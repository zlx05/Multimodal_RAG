"""会话持久化测试：conversation 创建、消息写入、chat_history 组装、trace 落库。

复用 org 的 SQLite 内存库注入；_load_chat_history 来自 routes_retrieval，
与 org 共享同一个 _session_factory（monkeypatch 后一并生效）。
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.db import org
from backend.app.api.routes_retrieval import _load_chat_history


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


def test_create_and_list_conversations(sqlite_db):
    conv = org.create_conversation("u_1", class_id="c_default", title="微积分复习")
    assert conv["user_id"] == "u_1"
    assert conv["class_id"] == "c_default"
    conv2 = org.create_conversation("u_2", title="")
    mine = org.list_conversations("u_1")
    assert [c["id"] for c in mine] == [conv["id"]]
    assert org.get_conversation(conv["id"])["title"] == "微积分复习"
    assert org.get_conversation(conv2["id"])["user_id"] == "u_2"


def test_add_and_list_messages(sqlite_db):
    conv = org.create_conversation("u_1")
    org.add_message(conv["id"], "user", "什么是极限？", model="deepseek")
    assistant = org.add_message(
        conv["id"], "assistant", "极限是…",
        model="deepseek",
        metadata_json=json.dumps({"router": {"scope": "auto"}}, ensure_ascii=False),
    )
    messages = org.list_messages(conv["id"])
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["id"] == assistant["id"]
    assert "router" in json.loads(messages[1]["metadata_json"])


def test_load_chat_history_builds_basemessages(sqlite_db):
    conv = org.create_conversation("u_1")
    org.add_message(conv["id"], "user", "第一问", model="m")
    org.add_message(conv["id"], "assistant", "第一答", model="m")
    history = _load_chat_history(conv["id"], "u_1")
    assert len(history) == 2
    assert history[0].type == "human"
    assert history[0].content == "第一问"
    assert history[1].type == "ai"
    assert history[1].content == "第一答"


def test_load_chat_history_rejects_foreign_conv(sqlite_db):
    conv = org.create_conversation("u_1")
    org.add_message(conv["id"], "user", "x", model="m")
    with pytest.raises(Exception):
        _load_chat_history(conv["id"], "u_other")


def test_add_and_list_traces(sqlite_db):
    conv = org.create_conversation("u_1")
    org.add_message(conv["id"], "user", "q", model="m")
    msg = org.add_message(conv["id"], "assistant", "a", model="m")
    org.add_trace(msg["id"], 0, "search_library", json.dumps({"question": "q"}), "证据串")
    org.add_trace(msg["id"], 1, "search_documents", "{}", "证据串2")
    traces = org.list_traces(msg["id"])
    assert len(traces) == 2
    assert [t["step_index"] for t in traces] == [0, 1]
    assert traces[0]["tool"] == "search_library"


def test_list_conversation_traces_sorted(sqlite_db):
    conv = org.create_conversation("u_1")
    org.add_message(conv["id"], "user", "q1", model="m")
    m1 = org.add_message(conv["id"], "assistant", "a1", model="m")
    org.add_message(conv["id"], "user", "q2", model="m")
    m2 = org.add_message(conv["id"], "assistant", "a2", model="m")
    org.add_trace(m1["id"], 0, "search_library", "{}", "o1")
    org.add_trace(m2["id"], 0, "search_documents", "{}", "o2")
    traces = org.list_conversation_traces(conv["id"])
    assert len(traces) == 2
    assert traces[0]["tool"] == "search_library"
    assert traces[1]["tool"] == "search_documents"


def test_delete_conversation(sqlite_db):
    conv = org.create_conversation("u_1")
    org.add_message(conv["id"], "user", "q", model="m")
    org.delete_conversation(conv["id"])
    assert org.get_conversation(conv["id"]) is None
