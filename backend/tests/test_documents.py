"""routes_documents 上传接口测试（TestClient + SQLite 内存库注入 + 假任务存储）。

不 import main（会拉起 Milvus），只挂 documents_router 造最小 FastAPI 应用。
替换 _session_factory（document_registry / org / seed 共用同一 SQLite engine）、
UPLOAD_DIR 到 tmp_path、TaskStore 与 enqueue_ingestion 为假实现——上传接口
不触碰真实 Redis / data/uploads。走真实 JWT header（auth_helpers）。
"""

import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.routes_documents import router as documents_router
from backend.app.core.database import Base
from backend.app.db import org
from backend.app.db.seed import seed_default_admin
from backend.app.rag import document_registry as dr
from auth_helpers import as_admin


class FakeTaskStore:
    """记录 create_task 调用的假 TaskStore，验证任务创建次数/参数。"""

    def __init__(self):
        self.created: list[tuple[str, dict]] = []

    def create_task(self, **kwargs):
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        self.created.append((task_id, kwargs))
        return task_id


@pytest.fixture
def client(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    # registry 与 org 共用同一引擎，跨表可见性（documents ↔ uploads 反查）才成立。
    monkeypatch.setattr(dr, "_session_factory", Session)
    monkeypatch.setattr(org, "_session_factory", Session)
    monkeypatch.setattr("backend.app.db.seed._session_factory", Session)
    seed_default_admin()  # get_current_user 需要 u_admin 存在

    monkeypatch.setattr(
        "backend.app.api.routes_documents.UPLOAD_DIR", tmp_path
    )  # 不写真实 data/uploads
    store = FakeTaskStore()
    monkeypatch.setattr("backend.app.api.routes_documents._task_store", store)
    monkeypatch.setattr(
        "backend.app.api.routes_documents.enqueue_ingestion",
        lambda _store, _task_id: None,
    )

    app = FastAPI()
    app.include_router(documents_router)
    with TestClient(app) as c:
        yield c, store, tmp_path


def _upload(c: TestClient, content: bytes, filename: str = "a.md"):
    return c.post(
        "/api/v1/documents",
        headers=as_admin(),
        files={"file": (filename, content, "text/markdown")},
        data={"chunk_profile": "auto"},
    )


def test_upload_creates_document_task_and_upload(client):
    c, store, tmp_path = client
    resp = _upload(c, b"hello world", "note.md")
    assert resp.status_code == 200
    body = resp.json()
    assert body["document_id"].startswith("doc_")
    assert body["task_id"].startswith("task_")
    assert body["status"] == "PENDING"
    assert len(body["content_hash"]) == 16

    doc = dr.get_document(body["document_id"])
    assert doc is not None
    assert doc["filename"] == "note.md"

    upload = org.get_upload_by_document(body["document_id"])
    assert upload is not None
    assert upload["status"] == "pending"

    assert len(store.created) == 1
    assert (tmp_path / f"{body['document_id']}.md").is_file()


def test_upload_duplicate_hash_returns_409_and_cleans_orphan(client):
    c, store, tmp_path = client
    first = _upload(c, b"same content", "one.md")
    assert first.status_code == 200
    first_id = first.json()["document_id"]

    # 换文件名、内容字节相同 → 409，且不建第二个索引/任务/文件
    second = _upload(c, b"same content", "renamed.md")
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["document_id"] == first_id
    assert detail["status"] == "pending"
    assert "重复" in detail["message"]

    # 孤儿清理：tmp_path 下仍只有第一个文件；未二次建任务
    files = sorted(tmp_path.glob("doc_*.md"))
    assert len(files) == 1, files
    assert len(store.created) == 1


def test_upload_duplicate_prefers_approved(client):
    c, store, tmp_path = client
    first = _upload(c, b"approved doc", "approved.md")
    first_id = first.json()["document_id"]
    upload = org.get_upload_by_document(first_id)
    org.update_upload(
        upload["id"], status="approved", reviewed_by="agent", reviewed_at=time.time()
    )

    second = _upload(c, b"approved doc", "copy.md")
    assert second.status_code == 409
    assert second.json()["detail"]["status"] == "approved"
    assert len(store.created) == 1


def test_upload_duplicate_rejected_includes_review_note(client):
    c, store, tmp_path = client
    first = _upload(c, b"bad content", "bad.md")
    first_id = first.json()["document_id"]
    upload = org.get_upload_by_document(first_id)
    org.update_upload(
        upload["id"],
        status="rejected",
        reviewed_by="agent",
        reviewed_at=time.time(),
        review_note="内容与课程无关",
    )

    second = _upload(c, b"bad content", "bad_copy.md")
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["status"] == "rejected"
    assert detail["review_note"] == "内容与课程无关"
    assert len(store.created) == 1


def test_upload_different_content_not_duplicate(client):
    c, store, tmp_path = client
    first = _upload(c, b"content A")
    second = _upload(c, b"content B")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["document_id"] != second.json()["document_id"]
    assert len(store.created) == 2
