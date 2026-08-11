"""routes_conversations 的 API 层测试：历史会话列表的预览字段（last_message/message_count）。

仿 test_org_api 的 TestClient + SQLite 内存库注入；只挂 conversations_router。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.routes_conversations import router as conversations_router
from backend.app.core.database import Base
from backend.app.db import org
from backend.app.db.seed import DEFAULT_ADMIN_ID, seed_default_admin
from auth_helpers import as_admin, auth_headers


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(org, "_session_factory", Session)
    monkeypatch.setattr("backend.app.db.seed._session_factory", Session)
    seed_default_admin()

    app = FastAPI()
    app.include_router(conversations_router)
    with TestClient(app) as c:
        yield c


def test_list_conversations_includes_preview_and_count(client):
    conv = org.create_conversation(DEFAULT_ADMIN_ID, title="微积分复习")
    org.add_message(conv["id"], "user", "什么是极限？")
    long_answer = "极限是函数值无限接近的值。" + "额外补充。" * 20
    org.add_message(conv["id"], "assistant", long_answer, model="m")

    resp = client.get("/api/v1/conversations", headers=as_admin())
    assert resp.status_code == 200
    items = resp.json()["conversations"]
    assert len(items) == 1
    assert items[0]["id"] == conv["id"]
    assert items[0]["message_count"] == 2
    # 预览取最后一条真实消息并截断到 60 字（过滤掉压缩摘要 role=system）
    assert items[0]["last_message"] == long_answer[:60]
    assert len(items[0]["last_message"]) == 60


def test_list_conversations_isolated_by_user(client):
    other = org.create_user("别人", role="member")
    mine = org.create_conversation(DEFAULT_ADMIN_ID, title="我的会话")
    org.add_message(mine["id"], "user", "q", model="m")
    theirs = org.create_conversation(other["id"], title="别人的会话")

    mine_resp = client.get("/api/v1/conversations", headers=as_admin())
    theirs_resp = client.get("/api/v1/conversations", headers=auth_headers(other["id"], "member"))
    assert [c["id"] for c in mine_resp.json()["conversations"]] == [mine["id"]]
    assert [c["id"] for c in theirs_resp.json()["conversations"]] == [theirs["id"]]
