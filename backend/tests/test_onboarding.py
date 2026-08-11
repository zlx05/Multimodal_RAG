"""首次调查报告测试：needs_onboarding 判定、提交写 survey+profile、只允许一次。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.routes_org import router as org_router
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
    app.include_router(org_router)
    with TestClient(app) as c:
        yield c


def _member() -> str:
    return org.create_user("新生", role="member")["id"]


def test_member_needs_onboarding(client):
    sid = _member()
    resp = client.get("/api/v1/users/me/onboarding", headers=auth_headers(sid, "member"))
    assert resp.status_code == 200
    assert resp.json()["needs_onboarding"] is True
    assert resp.json()["survey"] is None


def test_head_never_needs_onboarding(client):
    resp = client.get("/api/v1/users/me/onboarding", headers=as_admin())
    assert resp.json()["needs_onboarding"] is False


def test_teacher_never_needs_onboarding(client):
    tid = org.create_user("老师甲", role="admin")["id"]
    resp = client.get("/api/v1/users/me/onboarding", headers=auth_headers(tid, "admin"))
    assert resp.json()["needs_onboarding"] is False


def test_submit_survey_writes_survey_and_profile(client):
    sid = _member()
    resp = client.post(
        "/api/v1/users/me/survey",
        json={"subjects": ["数学"], "weak_points": ["不定积分"], "answer_style": "socratic"},
        headers=auth_headers(sid, "member"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_onboarding"] is False
    assert body["survey"]["answer_style"] == "socratic"
    # 调查同步进画像
    profile = client.get("/api/v1/users/me/profile", headers=auth_headers(sid, "member")).json()
    assert profile["preferred_style"] == "socratic"
    assert profile["weak_points"] == ["不定积分"]
    # 只允许一次
    again = client.post(
        "/api/v1/users/me/survey",
        json={"subjects": [], "weak_points": [], "answer_style": "direct"},
        headers=auth_headers(sid, "member"),
    )
    assert again.status_code == 409
    # 不再弹
    onboarding = client.get("/api/v1/users/me/onboarding", headers=auth_headers(sid, "member")).json()
    assert onboarding["needs_onboarding"] is False


def test_teacher_cannot_submit_survey(client):
    tid = org.create_user("老师乙", role="admin")["id"]
    resp = client.post(
        "/api/v1/users/me/survey",
        json={"subjects": [], "weak_points": [], "answer_style": "guiding"},
        headers=auth_headers(tid, "admin"),
    )
    assert resp.status_code == 403
