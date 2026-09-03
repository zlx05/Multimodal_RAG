"""Persistent document metadata used by ingestion and retrieval.

Metadata lives in MySQL (documents 表) instead of a JSON file. The API and
Worker are separate processes; a database gives cross-process consistency
that a process-local RLock + atomic file replace cannot provide.

Milvus 侧从每文档一个 collection 迁移到单共享 collection（rag_all）后，
collection 名退化为常量，文档身份靠 chunk 上的 document_id 分区。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from ..core.database import SessionLocal
from ..db.models import Document
from .hybrid_pipeline import SHARED_COLLECTION

# 会话工厂注入点：测试用 SQLite sessionmaker 替换。运行期默认 MySQL。
_session_factory = SessionLocal


def _to_dict(row: Document) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def document_collection_name(document_id: str, filename: str) -> str:
    """所有文档共用单共享 collection（rag_all），chunk 靠 document_id 分区。

    文档级路由消融 + 单库迁移后，每文档一个 collection 不再必要（检索全局化、
    删除按 document_id expr）。参数保留以兼容调用方，返回值恒为 SHARED_COLLECTION。
    """
    return SHARED_COLLECTION


def infer_document_topic(filename: str, blocks: list[Any]) -> str:
    """Infer a human-readable topic from parser output after ingestion."""
    for block in blocks:
        content_type = str(getattr(block, "content_type", ""))
        text = str(getattr(block, "text", "") or "").strip()
        if content_type == "heading" and len(text) >= 2:
            return _clean_topic(text)

    for block in blocks:
        for heading in getattr(block, "heading_path", []) or []:
            if len(str(heading).strip()) >= 2:
                return _clean_topic(str(heading))

    for block in blocks:
        text = str(getattr(block, "text", "") or "").strip()
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if len(first_line) >= 2:
            return _clean_topic(first_line)

    return Path(filename).stem or "未命名资料"


def _clean_topic(value: str) -> str:
    value = re.sub(r"^#+\s*", "", value).strip()
    value = re.sub(r"[\[\]【】]", "", value)
    return value[:80].strip() or "未命名资料"


def _all_records() -> dict[str, dict[str, Any]]:
    with _session_factory() as db:
        return {row.document_id: _to_dict(row) for row in db.query(Document).all()}


def register_document(
    document_id: str,
    filename: str,
    source_path: str,
    content_hash: str,
    source_type: str,
    source_url: str = "",
) -> dict[str, Any]:
    record = {
        "document_id": document_id,
        "filename": filename,
        "source_path": source_path,
        "collection_name": document_collection_name(document_id, filename),
        "topic_label": Path(filename).stem or "未命名资料",
        "content_hash": content_hash,
        "source_type": source_type,
        "source_url": source_url,
        "created_at": time.time(),
    }
    # merge 以 document_id 为主键做 upsert，天然幂等
    with _session_factory() as db:
        db.merge(Document(**record))
        db.commit()
    return record


def update_document(document_id: str, **updates: Any) -> dict[str, Any] | None:
    with _session_factory() as db:
        row = db.get(Document, document_id)
        if row is None:
            return None
        for key, value in updates.items():
            if hasattr(row, key):  # 白名单防御：只更新模型已有字段
                setattr(row, key, value)
        db.commit()
        return _to_dict(row)


def get_document(document_id: str) -> dict[str, Any] | None:
    with _session_factory() as db:
        row = db.get(Document, document_id)
        return _to_dict(row) if row is not None else None


def get_by_content_hash(content_hash: str) -> list[dict[str, Any]]:
    """按 content_hash 返回 documents 记录列表（无则空列表）。

    空 hash（URL 上传）直接返回空，不做匹配。不设唯一约束：URL 上传
    content_hash="" 也会落库，无法做唯一索引，同一 hash 允许多条记录并存。
    """
    if not content_hash:
        return []
    with _session_factory() as db:
        rows = db.query(Document).filter(Document.content_hash == content_hash).all()
        return [_to_dict(row) for row in rows]


def remove_document(document_id: str) -> dict[str, Any] | None:
    with _session_factory() as db:
        row = db.get(Document, document_id)
        if row is None:
            return None
        record = _to_dict(row)
        db.delete(row)
        db.commit()
        return record


def list_documents(upload_dir: Path) -> list[dict[str, Any]]:
    """List registered documents and provide a legacy fallback for old uploads."""
    records = _all_records()

    result: list[dict[str, Any]] = []
    known_paths: set[str] = set()
    for document_id, record in records.items():
        source_path = Path(str(record.get("source_path", "")))
        if not source_path.exists():
            candidates = list(upload_dir.glob(f"{document_id}.*"))
            source_path = candidates[0] if candidates else Path()
        if not source_path.is_file():
            continue
        known_paths.add(str(source_path.resolve()))
        result.append(
            {
                **record,
                "size": source_path.stat().st_size,
                "source_type": record.get("source_type") or source_path.suffix.lstrip("."),
                "source_path": str(source_path),
            }
        )

    # Old test uploads predate the registry. Keep them visible and searchable
    # through their legacy collection until the user re-ingests them.
    for path in sorted(upload_dir.glob("*"), key=lambda item: item.name.lower()):
        if not path.is_file() or str(path.resolve()) in known_paths:
            continue
        document_id = path.stem
        result.append(
            {
                "document_id": document_id,
                "filename": path.name,
                "source_path": str(path),
                "collection_name": SHARED_COLLECTION,
                "topic_label": path.stem,
                "content_hash": "",
                "source_type": path.suffix.lstrip("."),
                "created_at": path.stat().st_mtime,
                "size": path.stat().st_size,
                "legacy": True,
            }
        )

    return sorted(result, key=lambda item: str(item.get("filename", "")).lower())
