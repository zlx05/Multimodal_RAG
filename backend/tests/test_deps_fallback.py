"""DB 不可用时的降级行为测试。

- 身份：get_current_user 回退**降级** u_admin（degraded=True），require_admin 一律 503
  （不会因为 DB 挂了升级成管理员权限）；非默认身份 503。
- 目录：_visible_document_records / _collection_for_document 降级为磁盘+Milvus 重建。

把 org._session_factory 与 document_registry._session_factory 换成抛
OperationalError 的桩模拟 MySQL 挂掉；Milvus 用假连接返回 collection 列表。
"""

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from backend.app.api import deps
from backend.app.api import routes_retrieval as rr
from backend.app.api.routes_retrieval import _profile_for
from backend.app.db import org
from backend.app.db.seed import DEFAULT_ADMIN_ID
from backend.app.rag import document_registry as dr
from auth_helpers import make_token


@pytest.fixture
def db_down(monkeypatch):
    def boom():
        raise OperationalError("stmt", {}, Exception("can not connect to mysql"))

    monkeypatch.setattr(org, "_session_factory", boom)
    monkeypatch.setattr(dr, "_session_factory", boom)


@pytest.fixture
def fake_milvus(monkeypatch):
    """_degraded_catalog_from_disk 内部 `from pymilvus import connections, utility`。"""
    collections = {"rag_gao_shu_abc123def456", "rag_other_zzzzzzzzzzzz"}
    import pymilvus

    monkeypatch.setattr(pymilvus.connections, "connect", lambda *a, **k: None)
    monkeypatch.setattr(pymilvus.utility, "list_collections", lambda: collections)


def _seed_upload_dir(tmp_path, filename="doc_abc123def456.pdf") -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir(exist_ok=True)
    (uploads / filename).write_text("x", encoding="utf-8")


# ---------------------------------------------------------------- 身份与权限

def test_no_header_raises_401():
    # Phase 1.1：无 Authorization 头一律 401（不再回退默认管理员）
    with pytest.raises(HTTPException) as excinfo:
        deps.get_current_user(None)
    assert excinfo.value.status_code == 401


def test_valid_token_db_down_degrades_head(db_down):
    # 合法 token + MySQL 挂 → 用 token claims 合成降级身份（role 来自快照）
    token = make_token(DEFAULT_ADMIN_ID, role="head", username="老师")
    user = deps.get_current_user(f"Bearer {token}")
    assert user["id"] == DEFAULT_ADMIN_ID
    assert user["role"] == "head"
    assert user.get("degraded") is True


def test_valid_token_db_down_degrades_member(db_down):
    token = make_token("u_student", role="member", username="学生")
    user = deps.get_current_user(f"Bearer {token}")
    assert user["id"] == "u_student"
    assert user["role"] == "member"
    assert user.get("degraded") is True


def test_degraded_admin_cannot_require_admin(db_down):
    # fail-closed：DB 挂时不因降级身份而升级管理权限，require_admin 一律 503
    token = make_token(DEFAULT_ADMIN_ID, role="head", username="老师")
    user = deps.get_current_user(f"Bearer {token}")
    assert user.get("degraded") is True
    with pytest.raises(HTTPException) as excinfo:
        deps.require_admin(user)
    assert excinfo.value.status_code == 503


def test_real_admin_still_passes_require_admin():
    user = {"id": DEFAULT_ADMIN_ID, "role": "admin"}  # 无 degraded 标记 = 正常管理员
    assert deps.require_admin(user) == user


def test_profile_for_returns_none_when_db_down(db_down):
    assert _profile_for(DEFAULT_ADMIN_ID) is None


# ---------------------------------------------------------------- 目录降级

def test_visible_records_degrades_to_disk_catalog(db_down, fake_milvus, monkeypatch, tmp_path):
    _seed_upload_dir(tmp_path)
    monkeypatch.setattr(rr, "UPLOAD_DIR", tmp_path / "uploads")
    catalog = rr._visible_document_records()
    assert len(catalog) == 1
    item = catalog[0]
    assert item["document_id"] == "doc_abc123def456"
    # collection 名通过 document_id 后缀精确匹配假 Milvus 里的真实 collection
    assert item["collection_name"] == "rag_gao_shu_abc123def456"
    assert item.get("degraded") is True


def test_uploaded_collections_degrades(db_down, fake_milvus, monkeypatch, tmp_path):
    _seed_upload_dir(tmp_path)
    monkeypatch.setattr(rr, "UPLOAD_DIR", tmp_path / "uploads")
    assert rr._uploaded_collections() == ["rag_gao_shu_abc123def456"]


def test_collection_for_document_degraded(db_down, fake_milvus, monkeypatch, tmp_path):
    _seed_upload_dir(tmp_path)
    monkeypatch.setattr(rr, "UPLOAD_DIR", tmp_path / "uploads")
    assert rr._collection_for_document("doc_abc123def456") == "rag_gao_shu_abc123def456"
    # 磁盘上没有的 document_id → 默认 rag_<id>
    assert rr._collection_for_document("doc_ghost") == "rag_doc_ghost"


def test_catalog_empty_when_milvus_also_down(db_down, monkeypatch, tmp_path):
    """Milvus 也连不上时返回空目录（知识库为空，不崩）。"""
    _seed_upload_dir(tmp_path)
    monkeypatch.setattr(rr, "UPLOAD_DIR", tmp_path / "uploads")
    import pymilvus

    def boom_connect(*a, **k):
        raise Exception("milvus down")

    monkeypatch.setattr(pymilvus.connections, "connect", boom_connect)
    assert rr._visible_document_records() == []
