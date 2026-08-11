"""Shared document and Milvus collection catalog helpers."""

import hashlib
import json
import os
import re
from pathlib import Path

from .chunkers import CHUNKER_INFO


def get_doc_digest(document: Path) -> str:
    digest = hashlib.sha1()
    with document.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()[:8]


def get_document_slug(document: Path) -> str:
    readable = re.sub(r"[^A-Za-z0-9]+", "_", document.stem).strip("_").lower()[:20]
    return readable or "document"


def get_collection_name(document: Path, chunker_type: str, chunker_params: dict | None = None) -> str:
    params = chunker_params or {}
    config_digest = hashlib.sha1(
        json.dumps(params, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:8]
    return (
        f"rag_{get_document_slug(document)}_{chunker_type}_"
        f"{get_doc_digest(document)}_{config_digest}"
    )


def connect_milvus():
    from pymilvus import connections

    if not connections.has_connection("default"):
        connections.connect(
            alias="default",
            host=os.getenv("MILVUS_HOST", "127.0.0.1"),
            port=os.getenv("MILVUS_PORT", "19530"),
        )


def get_collection_candidates(document: Path, chunker_type: str, chunker_params: dict) -> list[str]:
    try:
        from pymilvus import utility

        connect_milvus()
        all_collections = set(utility.list_collections())
    except Exception:
        return []

    target = get_collection_name(document, chunker_type, chunker_params)
    prefix = f"rag_{get_document_slug(document)}_{chunker_type}_{get_doc_digest(document)}_"
    candidates = [target] if target in all_collections else []
    candidates.extend(sorted(name for name in all_collections if name.startswith(prefix) and name != target))
    return list(dict.fromkeys(candidates))


def get_collection_count(collection_name: str) -> int:
    from pymilvus import Collection, utility

    connect_milvus()
    if not utility.has_collection(collection_name):
        return 0
    return int(Collection(collection_name).num_entities)


def document_has_content(document: Path) -> bool:
    try:
        return document.exists() and document.stat().st_size > 0
    except OSError:
        return False


def list_documents(data_dir: Path) -> list[Path]:
    extensions = {".pdf", ".md", ".txt", ".doc", ".docx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".xlsx", ".csv"}
    if not data_dir.exists():
        return []
    return sorted(
        [path for path in data_dir.iterdir() if path.is_file() and path.suffix.lower() in extensions],
        key=lambda path: path.name.lower(),
    )


def chunker_catalog() -> list[dict]:
    return [
        {
            "key": key,
            "name": info["name"],
            "description": info["description"],
            "category": info["category"],
        }
        for key, info in CHUNKER_INFO.items()
    ]
