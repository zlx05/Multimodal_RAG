"""document_registry 的 SQLAlchemy 实现测试。

用 SQLite 内存库 monkeypatch `_session_factory`，不依赖真实 MySQL。
MySQL 专用类型（JSON 等）未使用，模型字段在 SQLite 下全部兼容。
"""

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.rag import document_registry as dr


@pytest.fixture
def sqlite_db(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(dr, "_session_factory", sessionmaker(bind=engine))
    return tmp_path


def _make_record(document_id="doc_abc123", filename="data_structures.md"):
    src = tmp_file = None  # 由调用方传入真实文件路径
    return dr.register_document(
        document_id=document_id,
        filename=filename,
        source_path=str(src) if src else f"/tmp/{document_id}.md",
        content_hash="deadbeef",
        source_type="md",
    )


def test_register_then_get_roundtrip(sqlite_db):
    record = dr.register_document(
        document_id="doc_abc123",
        filename="data_structures.md",
        source_path="/tmp/doc_abc123.md",
        content_hash="deadbeef",
        source_type="md",
    )
    fetched = dr.get_document("doc_abc123")
    assert fetched is not None
    for key in ("document_id", "filename", "source_path", "content_hash", "source_type"):
        assert fetched[key] == record[key]
    assert fetched["collection_name"].startswith("rag_data_structures_")
    assert fetched["topic_label"] == "data_structures"
    assert fetched["created_at"] > 0


def test_register_is_upsert_idempotent(sqlite_db):
    first = dr.register_document(
        document_id="doc_abc123", filename="a.md", source_path="/tmp/a.md",
        content_hash="h1", source_type="md",
    )
    second = dr.register_document(
        document_id="doc_abc123", filename="b.md", source_path="/tmp/b.md",
        content_hash="h2", source_type="md",
    )
    assert first["document_id"] == second["document_id"]
    got = dr.get_document("doc_abc123")
    assert got["filename"] == "b.md"  # 同名 upsert 覆盖


def test_update_document_merges_fields(sqlite_db):
    dr.register_document(
        document_id="doc_abc123", filename="a.md", source_path="/tmp/a.md",
        content_hash="h1", source_type="md",
    )
    updated = dr.update_document("doc_abc123", topic_label="图论", chunk_profile="auto")
    assert updated["topic_label"] == "图论"
    assert updated["chunk_profile"] == "auto"
    # 未更新的字段保持不变
    assert updated["filename"] == "a.md"


def test_update_document_missing_returns_none(sqlite_db):
    assert dr.update_document("doc_missing", topic_label="x") is None


def test_update_document_ignores_unknown_keys(sqlite_db):
    dr.register_document(
        document_id="doc_abc123", filename="a.md", source_path="/tmp/a.md",
        content_hash="h1", source_type="md",
    )
    updated = dr.update_document("doc_abc123", not_a_column=1)
    assert "not_a_column" not in updated


def test_remove_document(sqlite_db):
    dr.register_document(
        document_id="doc_abc123", filename="a.md", source_path="/tmp/a.md",
        content_hash="h1", source_type="md",
    )
    removed = dr.remove_document("doc_abc123")
    assert removed["document_id"] == "doc_abc123"
    assert dr.get_document("doc_abc123") is None
    assert dr.remove_document("doc_abc123") is None


def test_list_documents_computes_size_and_skips_missing(sqlite_db, tmp_path):
    src = tmp_path / "doc_abc123.md"
    src.write_text("content", encoding="utf-8")
    dr.register_document(
        document_id="doc_abc123", filename="a.md", source_path=str(src),
        content_hash="h1", source_type="md",
    )
    # 注册了但文件不存在 → list 跳过
    dr.register_document(
        document_id="doc_ghost", filename="ghost.md", source_path="/tmp/ghost.md",
        content_hash="h1", source_type="md",
    )

    docs = dr.list_documents(tmp_path)
    ids = {d["document_id"] for d in docs}
    assert "doc_abc123" in ids
    assert "doc_ghost" not in ids
    item = next(d for d in docs if d["document_id"] == "doc_abc123")
    assert item["size"] == len("content")
    assert item["source_type"] == "md"


def test_list_documents_legacy_fallback(sqlite_db, tmp_path):
    # 在 upload_dir 放一个未注册文件 → 出现 legacy 回退记录
    legacy = tmp_path / "doc_legacy.md"
    legacy.write_text("legacy", encoding="utf-8")

    docs = dr.list_documents(tmp_path)
    item = next((d for d in docs if d["document_id"] == "doc_legacy"), None)
    assert item is not None
    assert item["legacy"] is True
    assert item["collection_name"] == "rag_doc_legacy"
    assert item["size"] == len("legacy")


def test_list_documents_source_path_glob_fallback(sqlite_db, tmp_path):
    # source_path 失效但 uploads 目录里有同名文件 → 找回
    missing = tmp_path / "doc_glob.md"
    missing.write_text("x", encoding="utf-8")
    dr.register_document(
        document_id="doc_glob", filename="glob.md", source_path="/nowhere/doc_glob.md",
        content_hash="h1", source_type="md",
    )

    docs = dr.list_documents(tmp_path)
    item = next((d for d in docs if d["document_id"] == "doc_glob"), None)
    assert item is not None
    assert item["source_path"] == str(missing)


def test_get_by_content_hash_matches_and_empty(sqlite_db):
    dr.register_document(
        document_id="doc_a", filename="a.md", source_path="/tmp/a.md",
        content_hash="hash-aaa", source_type="md",
    )
    dr.register_document(
        document_id="doc_b", filename="b.md", source_path="/tmp/b.md",
        content_hash="hash-bbb", source_type="md",
    )

    matched = dr.get_by_content_hash("hash-aaa")
    assert [d["document_id"] for d in matched] == ["doc_a"]

    # 空 hash（URL 上传语义）与不存在的 hash 都返回空列表
    assert dr.get_by_content_hash("") == []
    assert dr.get_by_content_hash("no-such-hash") == []
