"""routes_org 的 API 层测试（TestClient + SQLite 内存库注入）。

不 import main（会拉起 Milvus），只挂 org_router 造一个最小 FastAPI 应用；
仿数据层测试替换 org._session_factory 为 SQLite sessionmaker；
走真实 header 解析，验证身份端点与班级/成员/画像路由。
"""

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
    # 种子管理员 u_admin + 默认班级必须存在，否则 get_current_user 回退会 404
    seed_default_admin()

    app = FastAPI()
    app.include_router(org_router)
    with TestClient(app) as c:
        yield c


def test_me_returns_identity(client):
    resp = client.get("/api/v1/users/me", headers=as_admin())
    assert resp.status_code == 200
    user = resp.json()["user"]
    assert user["id"] == DEFAULT_ADMIN_ID
    assert user["role"] == "head"  # Phase 2C：u_admin 是「默认班级」的班主任
    assert user["username"] == "老师"


def test_me_returns_401_for_unknown_user(client):
    # 无效/已删用户的 token → 401（与"无效 token"统一，避免探测账号存在性）
    resp = client.get("/api/v1/users/me", headers=auth_headers("u_ghost", "member"))
    assert resp.status_code == 401


def test_no_header_returns_401(client):
    resp = client.get("/api/v1/users/me")
    assert resp.status_code == 401


def test_admin_creates_member_and_joins_default_class(client):
    resp = client.post(
        "/api/v1/admin/members",
        json={"username": "小明"},
        headers=as_admin(),
    )
    assert resp.status_code == 200
    body = resp.json()
    student_id = body["user"]["id"]
    assert body["class_id"] == "c_default"
    # 学生身份立即生效（老师建号即入班，无需审批）
    me = client.get("/api/v1/users/me", headers=auth_headers(student_id, "member"))
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "member"
    # 默认班级成员列表包含该学生
    members = client.get("/api/v1/classes/c_default/members", headers=as_admin())
    ids = [m["user_id"] for m in members.json()["members"]]
    assert student_id in ids


def test_member_cannot_create_student(client):
    member = org.create_user("学生", role="member")
    resp = client.post(
        "/api/v1/admin/members",
        json={"username": "被拒绝"},
        headers=auth_headers(member["id"], "member"),
    )
    assert resp.status_code == 403


def test_profile_roundtrip(client):
    resp = client.put(
        "/api/v1/users/me/profile",
        json={"subjects": ["数学"], "weak_points": ["导数"], "preferred_style": "socratic"},
        headers=as_admin(),
    )
    assert resp.status_code == 200
    profile = resp.json()
    assert profile["subjects"] == ["数学"]
    assert profile["preferred_style"] == "socratic"
    assert profile["profile_version"] == 1
    # 再次读取
    again = client.get("/api/v1/users/me/profile", headers=as_admin())
    assert again.json()["weak_points"] == ["导数"]


def test_head_creates_teacher(client):
    resp = client.post(
        "/api/v1/admin/teachers",
        json={"username": "王老师"},
        headers=as_admin(),  # u_admin 是 head
    )
    assert resp.status_code == 200
    teacher_id = resp.json()["user"]["id"]
    assert resp.json()["user"]["role"] == "admin"
    me = client.get("/api/v1/users/me", headers=auth_headers(teacher_id, "admin"))
    assert me.json()["user"]["role"] == "admin"


def test_teacher_cannot_create_teacher(client):
    teacher = org.create_user("李老师", role="admin")
    resp = client.post(
        "/api/v1/admin/teachers",
        json={"username": "越权老师"},
        headers=auth_headers(teacher["id"], "admin"),
    )
    assert resp.status_code == 403


def test_member_cannot_create_teacher(client):
    member = org.create_user("学生", role="member")
    resp = client.post(
        "/api/v1/admin/teachers",
        json={"username": "越权学生"},
        headers=auth_headers(member["id"], "member"),
    )
    assert resp.status_code == 403


def test_admin_users_lists_roles(client):
    resp = client.get("/api/v1/admin/users", headers=as_admin())
    assert resp.status_code == 200
    roles = {u["role"] for u in resp.json()["users"]}
    assert "head" in roles  # u_admin


def test_delete_user_hierarchy_guards(client):
    student = org.create_user("可删学生", role="member")
    teacher_a = org.create_user("老师甲", role="admin")
    teacher_b = org.create_user("老师乙", role="admin")
    # 老师不能删另一个老师
    resp = client.delete(
        f"/api/v1/admin/users/{teacher_b['id']}", headers=auth_headers(teacher_a["id"], "admin")
    )
    assert resp.status_code == 403
    # 老师能删学生
    resp = client.delete(
        f"/api/v1/admin/users/{student['id']}", headers=auth_headers(teacher_a["id"], "admin")
    )
    assert resp.status_code == 200
    assert org.get_user(student["id"]) is None
    # 不能删自己
    resp = client.delete(
        f"/api/v1/admin/users/{DEFAULT_ADMIN_ID}", headers=as_admin()
    )
    assert resp.status_code == 400


def test_delete_user_cascades_personal_data(client):
    student = org.create_user("级联学生", role="member")
    org.upsert_profile(student["id"], subjects=["数学"], weak_points=["导数"])
    org.add_memory(student["id"], "personality", "喜欢先看结论")
    conv = org.create_conversation(student["id"], "c_default", "测试会话")
    org.add_message(conv["id"], "user", "q", model="m")
    resp = client.delete(
        f"/api/v1/admin/users/{student['id']}", headers=as_admin()
    )
    assert resp.status_code == 200
    assert org.get_user(student["id"]) is None
    assert org.get_profile(student["id"]) is None
    assert org.list_memory(student["id"]) == []
    assert org.get_conversation(conv["id"]) is None


def test_admin_reads_student_profile(client):
    student = org.create_user("画像学生", role="member")
    org.upsert_profile(student["id"], subjects=["数学"], weak_points=["导数"], preferred_style="guiding")
    resp = client.get(
        f"/api/v1/admin/users/{student['id']}/profile", headers=as_admin()
    )
    assert resp.status_code == 200
    profile = resp.json()
    assert profile["user_id"] == student["id"]
    assert profile["subjects"] == ["数学"]
    assert profile["weak_points"] == ["导数"]
    assert profile["preferred_style"] == "guiding"


def test_admin_reads_empty_student_profile_returns_defaults_without_upsert(client):
    student = org.create_user("无画像学生", role="member")
    resp = client.get(
        f"/api/v1/admin/users/{student['id']}/profile", headers=as_admin()
    )
    assert resp.status_code == 200
    profile = resp.json()
    assert profile["subjects"] == []
    assert profile["weak_points"] == []
    assert profile["preferred_style"] == "standard"
    assert profile["profile_version"] == 1
    # 老师只读查看不得写库（与 my_profile 的自写自读不同）
    assert org.get_profile(student["id"]) is None


def test_admin_reads_student_memory(client):
    student = org.create_user("记忆学生", role="member")
    org.add_memory(student["id"], "personality", "喜欢先看结论", source_question="你喜欢什么风格")
    resp = client.get(
        f"/api/v1/admin/users/{student['id']}/memory", headers=as_admin()
    )
    assert resp.status_code == 200
    memory = resp.json()["memory"]
    assert len(memory) == 1
    assert memory[0]["memory_type"] == "personality"
    assert memory[0]["content"] == "喜欢先看结论"


def test_member_cannot_read_other_students_profile(client):
    student = org.create_user("被看学生", role="member")
    member = org.create_user("越权学生", role="member")
    resp = client.get(
        f"/api/v1/admin/users/{student['id']}/profile",
        headers=auth_headers(member["id"], "member"),
    )
    assert resp.status_code == 403


def test_admin_profile_unknown_user_404(client):
    resp = client.get("/api/v1/admin/users/u_ghost/profile", headers=as_admin())
    assert resp.status_code == 404
    resp = client.get("/api/v1/admin/users/u_ghost/memory", headers=as_admin())
    assert resp.status_code == 404
