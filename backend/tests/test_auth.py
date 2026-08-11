"""Phase 1.1 真实鉴权：login / 引导式补设 / 改密 / token 生命周期 / 哈希不泄漏。

挂 auth_router + org_router（/users/me 验 access token 是否真的能用），
SQLite 内存库注入；登录全分支、setup 竞态与 scope 校验、过期/篡改/已删 token、
以及所有返回 JSON 绝不携带 password_hash。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.routes_auth import router as auth_router
from backend.app.api.routes_org import router as org_router
from backend.app.core.database import Base
from backend.app.db import org
from backend.app.db.seed import DEFAULT_ADMIN_ID, seed_default_admin
from auth_helpers import auth_headers, make_token, tampered_token


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
    app.include_router(auth_router)
    app.include_router(org_router)
    with TestClient(app) as c:
        yield c


def _setup_member(client) -> dict:
    """创建无密码学生，走完整 login→setup-password 流程，返回最终用户。"""
    resp = client.post("/api/v1/admin/members", json={"username": "新生"}, headers=auth_headers(DEFAULT_ADMIN_ID, "head"))
    assert resp.status_code == 200
    sid = resp.json()["user"]["id"]
    login = client.post("/api/v1/auth/login", json={"username": "新生", "password": "whatever"})
    assert login.status_code == 200
    body = login.json()
    assert body["needs_password_setup"] is True
    setup = client.post(
        "/api/v1/auth/setup-password",
        json={"setup_token": body["setup_token"], "password": "hunter2"},
    )
    assert setup.status_code == 200
    return sid, setup.json()


# ---------------------------------------------------------------- login 全分支

def test_login_unknown_user_401(client):
    resp = client.post("/api/v1/auth/login", json={"username": "不存在", "password": "x"})
    assert resp.status_code == 401


def test_login_wrong_password_401(client):
    sid, _ = _setup_member(client)
    resp = client.post("/api/v1/auth/login", json={"username": "新生", "password": "wrong"})
    assert resp.status_code == 401
    assert org.get_user(sid) is not None  # 用户名存在但密码错 → 不泄漏"用户存在"


def test_login_needs_setup_when_no_password(client):
    # u_admin（老师）默认无密码 → 首登引导式补设，不发正式 token
    resp = client.post("/api/v1/auth/login", json={"username": "老师", "password": "whatever"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_password_setup"] is True
    assert body.get("setup_token")
    assert "access_token" not in body
    assert "password_hash" not in body["user"]


def test_login_after_setup_returns_access_token(client):
    sid, setup = _setup_member(client)
    assert "password_hash" not in setup["user"]
    # access token 真实可用
    me = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {setup['access_token']}"})
    assert me.status_code == 200
    assert me.json()["user"]["id"] == sid
    # 用密码再登录也成功
    login = client.post("/api/v1/auth/login", json={"username": "新生", "password": "hunter2"})
    assert login.status_code == 200
    assert login.json()["access_token"]


def test_login_admin_password_set_on_seed(client):
    """u_admin 默认无密码 → 首登也走引导式补设（上线第一步给 u_admin 设密）。"""
    resp = client.post("/api/v1/auth/login", json={"username": "老师", "password": "x"})
    assert resp.status_code == 200
    assert resp.json()["needs_password_setup"] is True


# ---------------------------------------------------------------- 引导式补设竞态/scope

def test_setup_twice_conflicts_409(client):
    # 两人在密码尚未设置时同时 login → 各自拿到 setup token
    resp = client.post("/api/v1/admin/members", json={"username": "新生"}, headers=auth_headers(DEFAULT_ADMIN_ID, "head"))
    assert resp.status_code == 200
    login_a = client.post("/api/v1/auth/login", json={"username": "新生", "password": "whatever"}).json()
    login_b = client.post("/api/v1/auth/login", json={"username": "新生", "password": "whatever"}).json()
    assert login_a["needs_password_setup"] and login_b["needs_password_setup"]
    # 先到者设密成功
    first = client.post(
        "/api/v1/auth/setup-password",
        json={"setup_token": login_a["setup_token"], "password": "hunter2"},
    )
    assert first.status_code == 200
    # 后到者用仍有效的 setup token 再设 → 已有哈希，409（防"谁先设谁拥有"竞态）
    again = client.post(
        "/api/v1/auth/setup-password",
        json={"setup_token": login_b["setup_token"], "password": "another-pass"},
    )
    assert again.status_code == 409
    # 先到者的密码仍有效
    assert client.post("/api/v1/auth/login", json={"username": "新生", "password": "hunter2"}).status_code == 200


def test_setup_with_access_scope_token_401(client):
    _, _ = _setup_member(client)
    access = make_token("u_ghost", role="member", scope="access")
    resp = client.post(
        "/api/v1/auth/setup-password",
        json={"setup_token": access, "password": "abcdef"},
    )
    assert resp.status_code == 401


def test_setup_with_tampered_token_401(client):
    resp = client.post(
        "/api/v1/auth/setup-password",
        json={"setup_token": tampered_token(), "password": "abcdef"},
    )
    assert resp.status_code == 401


def test_setup_with_expired_token_401(client):
    expired = make_token("u_ghost", role="member", scope="setup", expires_minutes=-1)
    resp = client.post(
        "/api/v1/auth/setup-password",
        json={"setup_token": expired, "password": "abcdef"},
    )
    assert resp.status_code == 401


def test_setup_password_too_short_422(client):
    login = client.post("/api/v1/auth/login", json={"username": "老师", "password": "x"})
    setup_token = login.json()["setup_token"]
    resp = client.post(
        "/api/v1/auth/setup-password",
        json={"setup_token": setup_token, "password": "abc"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------- change-password

def test_change_password_wrong_old_400(client):
    sid, _ = _setup_member(client)
    resp = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "not-it", "new_password": "newpass1"},
        headers=auth_headers(sid, "member"),
    )
    assert resp.status_code == 400


def test_change_password_then_login_new(client):
    sid, _ = _setup_member(client)
    resp = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "hunter2", "new_password": "newpass1"},
        headers=auth_headers(sid, "member"),
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert client.post("/api/v1/auth/login", json={"username": "新生", "password": "hunter2"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "新生", "password": "newpass1"}).status_code == 200


def test_change_password_no_auth_401(client):
    resp = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "x", "new_password": "newpass1"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------- token 生命周期

def test_deleted_user_token_401(client):
    student = org.create_user("将被删", role="member")
    resp = client.delete(f"/api/v1/admin/users/{student['id']}", headers=auth_headers(DEFAULT_ADMIN_ID, "head"))
    assert resp.status_code == 200
    me = client.get("/api/v1/users/me", headers=auth_headers(student["id"], "member"))
    assert me.status_code == 401


def test_expired_access_token_401(client):
    expired = make_token(DEFAULT_ADMIN_ID, role="head", expires_minutes=-1)
    resp = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


def test_tampered_token_401(client):
    resp = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {tampered_token()}"})
    assert resp.status_code == 401


def test_non_bearer_header_401(client):
    resp = client.get("/api/v1/users/me", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert resp.status_code == 401


def test_setup_token_not_accepted_as_access(client):
    login = client.post("/api/v1/auth/login", json={"username": "老师", "password": "x"})
    setup_token = login.json()["setup_token"]
    resp = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {setup_token}"})
    assert resp.status_code == 401


# ---------------------------------------------------------------- 哈希绝不泄漏

def test_no_password_hash_in_any_user_json(client):
    sid, _ = _setup_member(client)
    client.post("/api/v1/admin/members", json={"username": "带密学生", "password": "secret1"}, headers=auth_headers(DEFAULT_ADMIN_ID, "head"))
    assert "password_hash" not in client.get("/api/v1/users/me", headers=auth_headers(sid, "member")).json()["user"]
    users = client.get("/api/v1/admin/users", headers=auth_headers(DEFAULT_ADMIN_ID, "head")).json()["users"]
    assert all("password_hash" not in u for u in users)
    login = client.post("/api/v1/auth/login", json={"username": "新生", "password": "hunter2"}).json()
    assert "password_hash" not in login["user"]
    me = client.get("/api/v1/users/me", headers=auth_headers(DEFAULT_ADMIN_ID, "head")).json()
    assert "password_hash" not in me["user"]


def test_create_member_with_password_can_login(client):
    resp = client.post(
        "/api/v1/admin/members",
        json={"username": "小明", "password": "initial-pass"},
        headers=auth_headers(DEFAULT_ADMIN_ID, "head"),
    )
    assert resp.status_code == 200
    assert "password_hash" not in resp.json()["user"]
    # 带初始密码的学生直接登录（不弹引导式补设）
    login = client.post("/api/v1/auth/login", json={"username": "小明", "password": "initial-pass"})
    assert login.status_code == 200
    assert "needs_password_setup" not in login.json()
    assert login.json()["access_token"]
