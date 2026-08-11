"""Phase 2 组织/上传/画像数据层测试（users/classes/members/uploads/profiles）。

用 SQLite 内存库 monkeypatch `org._session_factory`，不依赖真实 MySQL。
覆盖：用户/班级/成员建查、upload 状态流转、可见性过滤规则、画像读写。
"""

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.db import org


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


# ---------------------------------------------------------------- users

def test_create_user_defaults_member(sqlite_db):
    user = org.create_user("小明")
    assert user["username"] == "小明"
    assert user["role"] == "member"
    assert org.get_user(user["id"]) == user


def test_create_admin_user(sqlite_db):
    user = org.create_user("老师", role="admin")
    assert user["role"] == "admin"


def test_list_users(sqlite_db):
    org.create_user("A")
    org.create_user("B")
    assert len(org.list_users()) == 2


# ---------------------------------------------------------------- classes / members

def test_create_class_auto_joins_admin(sqlite_db):
    admin = org.create_user("老师", role="admin")
    cls = org.create_class("高数班", admin["id"], "微积分")
    assert cls["name"] == "高数班"
    members = org.list_members(cls["id"])
    assert [m["user_id"] for m in members] == [admin["id"]]


def test_add_member_and_remove(sqlite_db):
    admin = org.create_user("老师", role="admin")
    cls = org.create_class("班", admin["id"])
    student = org.create_user("小明")
    member = org.add_member(cls["id"], student["id"])
    assert member is not None
    assert member["user_id"] == student["id"]
    # 重复加入幂等
    again = org.add_member(cls["id"], student["id"])
    assert again["user_id"] == student["id"]
    assert len(org.list_members(cls["id"])) == 2
    removed = org.remove_member(cls["id"], student["id"])
    assert removed["user_id"] == student["id"]
    assert len(org.list_members(cls["id"])) == 1


def test_add_member_missing_user_or_class_returns_none(sqlite_db):
    cls = org.create_class("班", org.create_user("老师", role="admin")["id"])
    assert org.add_member(cls["id"], "u_ghost") is None
    assert org.add_member("c_ghost", "u_x") is None


# ---------------------------------------------------------------- uploads + visibility

def _seed_upload(document_id: str, status: str = "pending", uploader: str = "u_1") -> dict:
    upload = org.create_upload(document_id=document_id, uploader_user_id=uploader,
                               filename=f"{document_id}.md", source_type="md")
    if status != "pending":
        org.update_upload(upload["id"], status=status, reviewed_by="agent")
    return org.get_upload(upload["id"])


def test_upload_status_lifecycle(sqlite_db):
    upload = _seed_upload("doc_a")
    assert upload["status"] == "pending"
    updated = org.update_upload(upload["id"], status="approved", reviewed_by="agent",
                                reviewed_at=time.time())
    assert updated["status"] == "approved"
    assert updated["reviewed_by"] == "agent"
    fetched = org.get_upload(upload["id"])
    assert fetched["status"] == "approved"


def test_list_uploads_by_status(sqlite_db):
    _seed_upload("doc_a", "approved")
    _seed_upload("doc_b", "rejected")
    _seed_upload("doc_c", "pending")
    assert len(org.list_uploads("approved")) == 1
    assert len(org.list_uploads("rejected")) == 1
    assert len(org.list_uploads()) == 3


def test_hidden_document_ids_rule(sqlite_db):
    """有 upload 记录但非 approved → 隐藏；无 upload 记录（legacy）→ 可见。"""
    _seed_upload("doc_approved", "approved")
    _seed_upload("doc_pending", "pending")
    _seed_upload("doc_rejected", "rejected")
    _seed_upload("doc_hidden", "hidden")
    hidden = org.hidden_document_ids()
    assert hidden == {"doc_pending", "doc_rejected", "doc_hidden"}
    assert "doc_approved" not in hidden  # approved 可见
    assert "doc_legacy" not in hidden  # 无 upload 记录可见


def test_delete_upload(sqlite_db):
    upload = _seed_upload("doc_a", "approved")
    assert org.delete_upload(upload["id"]) is not None
    assert org.get_upload(upload["id"]) is None
    assert org.delete_upload("up_ghost") is None


# ---------------------------------------------------------------- profiles

def test_profile_defaults(sqlite_db):
    profile = org.upsert_profile("u_1")
    assert profile["user_id"] == "u_1"
    assert profile["preferred_style"] == "standard"
    assert profile["profile_version"] == 1


def test_profile_update_bumps_version(sqlite_db):
    org.upsert_profile("u_1", subjects=["数学"], preferred_style="beginner")
    profile = org.upsert_profile("u_1", weak_points=["导数"])
    assert profile["profile_version"] == 2
    assert org.get_profile("u_1")["preferred_style"] == "beginner"
    assert "导数" in profile["weak_points"]
