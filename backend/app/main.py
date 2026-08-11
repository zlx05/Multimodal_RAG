"""FastAPI service for the RAG learning workbench."""

import json
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parents[2]
DATA_DIR = PROJECT_ROOT / "data"
load_dotenv(PROJECT_ROOT / ".env")

for env_file in (PROJECT_ROOT / ".env", APP_DIR / ".env"):
    pass

# 兼容项目早期使用的 `llm-api:...` 配置格式。
for env_file in (PROJECT_ROOT / ".env", APP_DIR / ".env"):
    if not os.getenv("LLM_API_KEY") and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("llm-api:"):
                os.environ["LLM_API_KEY"] = line.split(":", 1)[1].strip()
                break

from .api.routes_auth import router as auth_router
from .api.routes_documents import router as documents_router
from .api.routes_tasks import router as tasks_router
from .api.routes_retrieval import router as retrieval_router
from .api.routes_org import router as org_router
from .api.routes_conversations import router as conversations_router
from .rag.chunkers import CHUNKER_INFO, get_chunker
from .rag.pipeline import MilvusRAGPipeline
from .rag.catalog import (
    chunker_catalog,
    connect_milvus,
    document_has_content,
    get_collection_candidates,
    get_collection_count,
    get_collection_name,
    list_documents,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    from .core.database import init_db

    try:
        init_db()
    except Exception as exc:
        # MySQL 未启动时不阻塞 API 启动；用到 DB 的接口会在实际查询时报错。
        print(f"[startup] MySQL 初始化失败（documents 表未建）：{exc}")
    yield


app = FastAPI(title="RAG 学习工作台", version="1.0.0", lifespan=lifespan)

# 注册 /api/v1 路由（多模态异步接口）
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(tasks_router)
app.include_router(retrieval_router)
app.include_router(org_router)
app.include_router(conversations_router)
PIPELINES: dict[str, MilvusRAGPipeline] = {}
PIPELINE_LOCK = threading.RLock()


class IndexRequest(BaseModel):
    document: str
    chunker: str
    params: dict[str, Any] = Field(default_factory=dict)
    action: Literal["use", "create", "rebuild"]
    collection: str | None = None
    api_key: str | None = None


class QueryRequest(BaseModel):
    document: str
    chunker: str
    params: dict[str, Any] = Field(default_factory=dict)
    collection: str
    question: str
    top_k: int = Field(default=3, ge=1, le=10)
    api_key: str | None = None


def resolve_document(document_name: str) -> Path:
    document = (DATA_DIR / Path(document_name).name).resolve()
    if document.parent != DATA_DIR.resolve() or not document.exists():
        raise HTTPException(status_code=404, detail="文档不存在")
    return document


def resolve_api_key(request_key: str | None) -> str:
    key = (request_key or os.getenv("LLM_API_KEY", "")).strip()
    if not key:
        raise HTTPException(status_code=400, detail="未配置 LLM_API_KEY，请检查项目根目录的 .env")
    return key


def validate_chunker(chunker_name: str, params: dict[str, Any]):
    if chunker_name not in CHUNKER_INFO:
        raise HTTPException(status_code=400, detail=f"未知分片策略: {chunker_name}")
    try:
        chunker = get_chunker(chunker_name, **params)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"分片参数无效: {exc}") from exc

    if chunker_name in {"fixed", "recursive"} and params.get("overlap", 0) >= params.get("chunk_size", 500):
        raise HTTPException(status_code=400, detail="重叠字数必须小于块大小")
    if chunker_name == "token" and params.get("overlap_tokens", 0) >= params.get("max_tokens", 500):
        raise HTTPException(status_code=400, detail="重叠 Token 必须小于最大 Token")
    if chunker_name in {"semantic", "markdown"} and params.get("min_chunk_size", 0) >= params.get("max_chunk_size", 1):
        raise HTTPException(status_code=400, detail="最小块大小必须小于最大块大小")
    if chunker_name == "sliding" and params.get("step", 1) > params.get("chunk_size", 1):
        raise HTTPException(status_code=400, detail="滑动步长不能大于窗口句数")
    return chunker


def get_pipeline(
    document: Path,
    api_key: str,
    collection: str,
    chunker_name: str,
    params: dict[str, Any],
    rebuild: bool = False,
) -> MilvusRAGPipeline:
    validate_chunker(chunker_name, params)
    with PIPELINE_LOCK:
        if rebuild:
            PIPELINES.pop(collection, None)
        elif collection in PIPELINES:
            return PIPELINES[collection]

        pipeline = MilvusRAGPipeline(
            str(document),
            api_key,
            collection,
            rebuild=rebuild,
            chunker=get_chunker(chunker_name, **params),
        )
        PIPELINES[collection] = pipeline
        return pipeline


@app.get("/api/health")
def health():
    try:
        connect_milvus()
        return {"status": "ok", "milvus": "connected"}
    except Exception as exc:
        return {"status": "degraded", "milvus": str(exc)}


@app.get("/api/v1/health")
def v1_health():
    """Versioned health endpoint used by the Vue client and deployment checks."""
    return health()


@app.get("/api/catalog")
def catalog():
    docs = []
    for document in list_documents(DATA_DIR):
        docs.append(
            {
                "name": document.name,
                "size": document.stat().st_size,
                "ready": document_has_content(document),
            }
        )
    return {"documents": docs, "chunkers": chunker_catalog()}


@app.get("/api/collections")
def collections(
    document: str,
    chunker: str,
    params: str = Query(default="{}"),
):
    document_path = resolve_document(document)
    try:
        chunker_params = json.loads(params)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="分片参数格式无效") from exc

    target = get_collection_name(document_path, chunker, chunker_params)
    result = []
    for name in get_collection_candidates(document_path, chunker, chunker_params):
        count = get_collection_count(name)
        if count > 0:
            result.append({"name": name, "entities": count, "current": name == target})
    return {"target": target, "collections": result}


@app.post("/api/index")
def index_document(request: IndexRequest):
    document = resolve_document(request.document)
    if not document_has_content(document):
        raise HTTPException(status_code=400, detail=f"文档为空: {document.name}")

    chunker = validate_chunker(request.chunker, request.params)
    target = get_collection_name(document, request.chunker, request.params)
    collection = request.collection if request.action == "use" else target
    if request.action == "use" and not collection:
        raise HTTPException(status_code=400, detail="没有选择要调用的 Collection")
    if request.action == "use" and get_collection_count(collection) <= 0:
        raise HTTPException(status_code=404, detail="选择的 Collection 不存在或没有向量")

    pipeline = get_pipeline(
        document,
        resolve_api_key(request.api_key),
        collection,
        request.chunker,
        request.params,
        rebuild=request.action == "rebuild",
    )
    return {
        "ok": True,
        "action": request.action,
        "collection": collection,
        "entities": int(pipeline.collection.num_entities),
        "chunker": chunker.name,
    }


@app.post("/api/query")
def query(request: QueryRequest):
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="请输入问题")
    document = resolve_document(request.document)
    if get_collection_count(request.collection) <= 0:
        raise HTTPException(status_code=404, detail="当前 Collection 没有可检索的向量")

    pipeline = get_pipeline(
        document,
        resolve_api_key(request.api_key),
        request.collection,
        request.chunker,
        request.params,
    )
    result = pipeline.query(question, top_k=request.top_k)
    return {
        "answer": result["answer"],
        "sources": [
            {"text": text, "score": score}
            for text, score in result["sources"]
        ],
        "collection": request.collection,
    }
