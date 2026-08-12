"""文档接口：上传、列表、详情、删除。

上传接口只做"保存文件 + 创建任务 + 入队"，立即返回 task_id，不阻塞。
实际解析/OCR/向量化由后台 Worker 完成。
"""

import hashlib
import ipaddress
import socket
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .deps import (
    get_current_user,
    require_admin,
    require_asset_signature,
    require_original_signature,
)
from ..core.config import DATA_DIR, MILVUS_HOST, MILVUS_PORT, RAG_ORIGINAL_DIR, RAG_WORK_DIR, REDIS_URL
from ..db.org import (
    create_upload,
    delete_upload,
    get_upload_by_document,
    hidden_document_ids,
)
from ..rag.document_registry import (
    get_by_content_hash,
    get_document,
    list_documents as list_registered_documents,
    register_document,
    remove_document,
)
from ..rag.assets import asset_url, media_type, original_url
from ..rag.chunking_profiles import PROFILES, profile_catalog
from ..tasks import TaskStore, enqueue_ingestion

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

# 上传文件保存目录
UPLOAD_DIR = DATA_DIR / "uploads"
SUPPORTED_EXTENSIONS = {
    ".pdf", ".md", ".txt", ".doc", ".docx", ".ppt", ".pptx",
    ".png", ".jpg", ".jpeg", ".bmp", ".webp",
    ".xlsx", ".csv",
}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


class UrlIngestRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    chunk_profile: str = Field(default="auto", max_length=40)

# 单实例共享 TaskStore
_task_store = TaskStore(REDIS_URL)

# 同 content_hash 查重时，各 uploads 状态的优先级（数值大者优先返回）
_UPLOAD_STATUS_PRIORITY = {"approved": 4, "rejected": 3, "pending": 2, "hidden": 1}


def _existing_by_hash(content_hash: str) -> dict | None:
    """查重：按 hash 找已存在记录中状态最"有用"的一条，附带 uploads 状态。

    状态优先级 approved > rejected > pending > hidden（同 hash 多条时取最高优先）。
    content_hash 为空（URL 上传）返回 None，不做查重。
    返回 dict：document_id / filename / status / content_hash / upload_id / review_note。
    """
    if not content_hash:
        return None
    best = None
    for record in get_by_content_hash(content_hash):
        upload = get_upload_by_document(record["document_id"]) or {}
        status = upload.get("status") or "pending"
        priority = _UPLOAD_STATUS_PRIORITY.get(status, 2)
        if best is None or priority > best["_priority"]:
            best = {
                "document_id": record["document_id"],
                "filename": record.get("filename", ""),
                "status": status,
                "content_hash": content_hash,
                "upload_id": upload.get("id", ""),
                "review_note": upload.get("review_note", ""),
                "_priority": priority,
            }
    if best:
        best.pop("_priority")
    return best


def _duplicate_message(existing: dict) -> str:
    if existing["status"] == "approved":
        return f"内容重复：已存在该资料（{existing['filename']}），无需重新上传。"
    if existing["status"] == "rejected":
        note = existing.get("review_note") or ""
        return f"内容重复：相同资料此前已被驳回{f'（原因：{note}）' if note else ''}，请勿重复上传。"
    return f"内容重复：该资料正在上传处理中（{existing['filename']}）。"


@router.post("")
async def upload_document(
    file: UploadFile,
    chunk_profile: str = Form(default="auto"),
    current_user: dict = Depends(get_current_user),
):
    """上传资料并创建异步解析任务。返回 document_id + task_id。

    老师与学生都能上传；上传会创建 uploads 校验记录（pending），
    由 Worker 里的校验 agent 审核通过后才进入检索。
    """
    filename = file.filename or "unnamed"
    _validate_chunk_profile(chunk_profile)
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型 {ext}，支持: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    # 生成 document_id 和保存路径（用 UUID 避免文件名冲突/路径穿越）
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    safe_name = f"{document_id}{ext}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    save_path = UPLOAD_DIR / safe_name

    # 分块写文件，同时计算内容哈希
    digest = hashlib.sha256()
    with save_path.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            if save_path.stat().st_size + len(chunk) > MAX_FILE_SIZE:
                save_path.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail="文件超过 50MB 限制")
            digest.update(chunk)
            out.write(chunk)

    content_hash = digest.hexdigest()

    # 查重：同一内容（sha256 相同）已存在则 409 拒绝，不建第二个索引。
    # 命中时本次只写入了 save_path 文件，清理掉即无孤儿（尚未落库/建任务）。
    existing = _existing_by_hash(content_hash)
    if existing:
        save_path.unlink(missing_ok=True)
        detail = {
            "message": _duplicate_message(existing),
            "document_id": existing["document_id"],
            "filename": existing["filename"],
            "status": existing["status"],
            "content_hash": existing["content_hash"][:16],
            "upload_id": existing["upload_id"],
        }
        if existing["status"] == "rejected" and existing["review_note"]:
            detail["review_note"] = existing["review_note"]
        raise HTTPException(status_code=409, detail=detail)

    record = register_document(
        document_id=document_id,
        filename=filename,
        source_path=str(save_path),
        content_hash=content_hash,
        source_type=ext.lstrip("."),
    )

    # 创建上传校验台账（status=pending），记录上传人；审核由 Worker 校验 agent 完成。
    create_upload(
        document_id=document_id,
        uploader_user_id=current_user["id"],
        filename=filename,
        source_type=ext.lstrip("."),
    )

    # 创建任务并入队
    task_id = _task_store.create_task(
        document_id=document_id,
        filename=filename,
        source_path=str(save_path),
        content_hash=content_hash,
        collection_name=record["collection_name"],
        chunk_profile=chunk_profile,
    )
    enqueue_ingestion(_task_store, task_id)

    return {
        "document_id": document_id,
        "filename": filename,
        "task_id": task_id,
        "status": "PENDING",
        "content_hash": content_hash[:16],
    }


@router.post("/url")
async def upload_url(
    request: UrlIngestRequest,
    current_user: dict = Depends(get_current_user),
):
    """Create an async task for a public HTML article URL."""
    url = request.url.strip()
    _validate_chunk_profile(request.chunk_profile)
    _validate_remote_url(url)
    parsed = urlparse(url)
    filename = _url_filename(parsed)
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    save_path = UPLOAD_DIR / f"{document_id}.html"
    record = register_document(
        document_id=document_id,
        filename=filename,
        source_path=str(save_path),
        content_hash="",
        source_type="html",
        source_url=url,
    )
    create_upload(
        document_id=document_id,
        uploader_user_id=current_user["id"],
        filename=filename,
        source_type="html",
    )
    task_id = _task_store.create_task(
        document_id=document_id,
        filename=filename,
        source_path=str(save_path),
        source_url=url,
        content_hash="",
        collection_name=record["collection_name"],
        chunk_profile=request.chunk_profile,
    )
    enqueue_ingestion(_task_store, task_id)
    return {
        "document_id": document_id,
        "filename": filename,
        "task_id": task_id,
        "status": "PENDING",
        "source_url": url,
    }


def _validate_chunk_profile(profile: str) -> None:
    if profile not in {"auto", *PROFILES}:
        raise HTTPException(status_code=400, detail=f"未知的分块 Profile: {profile}")


@router.get("/profiles")
async def list_chunking_profiles():
    return {"profiles": [{"id": "auto", "label": "自动选择", "description": "按资料类型和内容特征选择策略"}, *profile_catalog()]}


def _url_filename(parsed) -> str:
    stem = Path(parsed.path.rstrip("/")).stem or parsed.netloc.split(".")[0] or "web_article"
    stem = "".join(char if char.isalnum() or char in "-_" else "_" for char in stem)
    return f"{stem[:100] or 'web_article'}.html"


def _validate_remote_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="只支持 http/https 网页地址")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                raise HTTPException(status_code=400, detail="不允许抓取内网地址")
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="网页地址无法解析") from exc


@router.get("")
async def list_documents(current_user: dict = Depends(get_current_user)):
    """列出已上传的资料及其索引状态。

    附带 task_status：上传失败的任务会留下注册记录但 collection 不存在，
    前端据此把失败资料标记出来，避免用户误选后看到"尚未完成入库"。
    """
    task_status_by_doc: dict[str, str] = {}
    try:
        # 一次 SCAN 汇总所有任务的 document_id -> status（每份资料对应一个任务）
        for key in _task_store.client.scan_iter(match="rag:task:*", count=200):
            status = _task_store.client.hget(key, "status")
            doc_id = _task_store.client.hget(key, "document_id")
            if status and doc_id:
                task_status_by_doc[doc_id] = status
    except Exception as exc:
        print(f"[documents] 读取任务状态失败: {exc}")

    # Phase 2 可见性：有 upload 记录但未 approved（pending/rejected/hidden）的资料
    # 对成员不可见，与检索侧 _visible_document_records 保持一致；MySQL 不可用时
    # 退化为全部可见（列表照常返回，不因台账异常阻塞）。
    try:
        hidden = hidden_document_ids()
    except Exception as exc:
        print(f"[documents] 读取可见性规则失败，按全部可见处理: {exc}")
        hidden = set()

    docs = [
        {
            "document_id": item["document_id"],
            "filename": item["filename"],
            "size": item["size"],
            "source_type": item["source_type"],
            "collection_name": item["collection_name"],
            "topic_label": item.get("topic_label", item["filename"]),
            "source_url": item.get("source_url", ""),
            "original_url": original_url(item["document_id"]),
            "chunk_profile": item.get("chunk_profile", "auto"),
            "task_status": task_status_by_doc.get(item["document_id"]),
        }
        for item in list_registered_documents(UPLOAD_DIR)
        if item["document_id"] not in hidden
    ]
    return {"documents": docs}


@router.delete("/{document_id}")
async def delete_document(document_id: str, admin: dict = Depends(require_admin)):
    """删除资料：源文件 + 注册记录 + 上传台账 + Milvus collection + Redis 任务。

    用于清理上传失败留下的残留（collection 从未建成），或整份移除资料。
    幂等：collection/源文件不存在时静默跳过。
    """
    record = get_document(document_id)
    if record is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 源文件（UPLOAD_DIR 里以 document_id 命名的文件）
    source_path = Path(str(record.get("source_path", "")))
    if not source_path.is_file():
        candidates = list(UPLOAD_DIR.glob(f"{document_id}.*"))
        source_path = candidates[0] if candidates else Path()
    if source_path.is_file():
        source_path.unlink(missing_ok=True)

    # Milvus collection（失败残留没有 collection，跳过即可）
    collection_name = record.get("collection_name") or f"rag_{document_id}"
    try:
        from pymilvus import connections, utility

        connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)
        if utility.has_collection(collection_name):
            utility.drop_collection(collection_name)
    except Exception as exc:
        print(f"[documents] 删除 collection {collection_name} 失败: {exc}")

    # 上传校验台账
    upload = get_upload_by_document(document_id)
    if upload:
        delete_upload(upload["id"])

    # 注册记录
    remove_document(document_id)

    # Redis 任务
    try:
        for key in _task_store.client.scan_iter(match="rag:task:*", count=200):
            if _task_store.client.hget(key, "document_id") == document_id:
                _task_store.client.delete(key)
    except Exception as exc:
        print(f"[documents] 删除任务失败: {exc}")

    return {"deleted": document_id}


@router.get("/{document_id}/original")
async def get_original_document(
    document_id: str,
    _guard: None = Depends(require_original_signature),
):
    record = get_document(document_id)
    source_path = Path(str((record or {}).get("source_path", "")))
    if not source_path.is_file():
        candidates = list(UPLOAD_DIR.glob(f"{document_id}.*"))
        source_path = candidates[0] if candidates else Path()
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail="原始资料不存在")
    return FileResponse(
        source_path,
        media_type=media_type(source_path),
        filename=(record or {}).get("filename", source_path.name),
        content_disposition_type="inline",
    )


@router.get("/{document_id}/assets")
async def list_document_assets(
    document_id: str,
    current_user: dict = Depends(get_current_user),
):
    candidates: list[Path] = []
    work_root = Path(RAG_WORK_DIR)
    for directory in (work_root, Path(RAG_ORIGINAL_DIR)):
        if directory.exists():
            if directory.is_file():
                candidates.append(directory)
            else:
                candidates.extend(
                    path
                    for path in directory.rglob("*")
                    if path.is_file()
                    and (document_id in path.parts or path.name.startswith(document_id))
                )
    assets = []
    for path in candidates:
        ctype = media_type(path)
        # 栏目叫"解析出的图片"、前端用 <img> 渲染：只列图片，跳过 MinerU
        # 中间产物（layout/span/origin PDF、middle/content_list JSON、markdown 等）。
        if not ctype.startswith("image/"):
            continue
        url = asset_url(document_id, str(path))
        if url:
            assets.append({"filename": path.name, "url": url, "content_type": ctype})
    return {"document_id": document_id, "assets": assets}


@router.get("/{document_id}/assets/{asset_path:path}")
async def get_document_asset(
    document_id: str,
    asset_path: str,
    _guard: None = Depends(require_asset_signature),
):
    candidate = (DATA_DIR / asset_path).resolve()
    try:
        candidate.relative_to(DATA_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="资源路径无效") from exc
    if document_id not in candidate.parts and not candidate.name.startswith(document_id):
        raise HTTPException(status_code=404, detail="资源不属于当前资料")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="资源不存在")
    return FileResponse(candidate, media_type=media_type(candidate), content_disposition_type="inline")


@router.get("/{document_id}/chunks")
async def list_document_chunks(
    document_id: str,
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """Return indexed chunks and provenance for the chunk inspection view."""
    from pymilvus import Collection, connections, utility

    connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)
    record = get_document(document_id)
    if not record:
        raise HTTPException(status_code=404, detail="资料不存在")
    # 驳回/隐藏的资料即使残留 collection 也不展示切片（与 /documents 可见性一致）。
    try:
        if document_id in hidden_document_ids():
            raise HTTPException(status_code=404, detail="该资料已驳回，切片不可见")
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[documents] 读取可见性规则失败，跳过隐藏校验: {exc}")
    collection_name = record.get("collection_name", f"rag_{document_id}")
    if not utility.has_collection(collection_name):
        raise HTTPException(status_code=404, detail="该资料尚未完成入库")

    collection = Collection(collection_name)
    available = {field.name for field in collection.schema.fields}
    required = {"chunk_index", "content"}
    if not required.issubset(available):
        raise HTTPException(status_code=409, detail="该索引使用旧版 schema，无法展示切片")

    fields = [
        field
        for field in (
            "chunk_index", "content", "heading_path", "page_number", "source_type",
            "content_type", "image_path", "bbox", "confidence", "metadata",
        )
        if field in available
    ]
    rows = collection.query(
        expr="chunk_index >= 0",
        output_fields=fields,
        limit=offset + limit,
    )
    rows.sort(key=lambda row: int(row.get("chunk_index", 0)))
    page = rows[offset: offset + limit]
    for row in page:
        metadata = row.get("metadata", {}) or {}
        row["original_url"] = original_url(document_id)
        row["image_url"] = asset_url(document_id, row.get("image_path"))
        row["parent_chunk_id"] = metadata.get("parent_chunk_id")
        row["chunk_level"] = metadata.get("chunk_level", 0)
    return {
        "document_id": document_id,
        "collection": collection_name,
        "original_url": original_url(document_id),
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "chunks": page,
    }


@router.delete("/{document_id}")
async def delete_document(document_id: str):
    """删除文档文件及关联索引（当前简化：删文件 + 对应 collection）。"""
    record = get_document(document_id)
    collection_name = (record or {}).get("collection_name", f"rag_{document_id}")

    # 删除文件
    removed = False
    for path in UPLOAD_DIR.glob(f"{document_id}.*"):
        path.unlink(missing_ok=True)
        removed = True
    for path in Path(RAG_ORIGINAL_DIR).glob(f"{document_id}.*"):
        path.unlink(missing_ok=True)
        removed = True
    work_dir = Path(RAG_WORK_DIR) / document_id
    if work_dir.exists():
        try:
            if work_dir.is_dir():
                shutil.rmtree(work_dir)
            else:
                work_dir.unlink(missing_ok=True)
            removed = True
        except OSError:
            # Index deletion should remain available even if a temporary OCR
            # artifact is locked by an external viewer.
            pass
    # 删除 Milvus collection
    try:
        from pymilvus import connections, utility

        connections.connect(
            alias="default",
            host=__import__("os").getenv("MILVUS_HOST", "127.0.0.1"),
            port=__import__("os").getenv("MILVUS_PORT", "19530"),
        )
        coll = collection_name
        if utility.has_collection(coll):
            utility.drop_collection(coll)
            removed = True
    except Exception:
        pass
    if not removed:
        raise HTTPException(status_code=404, detail=f"文档 {document_id} 不存在")
    remove_document(document_id)
    return {"ok": True, "document_id": document_id}
