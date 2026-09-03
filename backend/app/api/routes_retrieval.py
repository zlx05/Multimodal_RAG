"""Retrieval and chat APIs, including automatic document routing."""

from pathlib import Path
import json
import re
import time
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.config import (
    CLARIFICATION_GATE_ENABLED,
    DATA_DIR,
    DUAL_RECALL_ENABLED,
    LLM_API_KEY,
    LLM_MAX_RETRIES,
    LLM_MODEL,
    LLM_REQUEST_TIMEOUT,
    MILVUS_HOST,
    MILVUS_PORT,
    QUERY_EXPANSION_ENABLED,
    QUERY_REWRITE_ENABLED,
    get_model_config,
    list_model_configs,
)
from langchain_openai import ChatOpenAI
from langchain_core.callbacks import BaseCallbackHandler
from sqlalchemy.exc import SQLAlchemyError

from .deps import get_current_user
from .rate_limit import RateLimitExceeded, check_chat_rate_limit
from ..db import org
from ..rag.metrics import get_metrics

from ..rag.agent_rag import (
    MAX_ITERATIONS,
    AgentChatContext,
    RouterDecision,
    build_executor,
    estimate_tokens,
    expand_query,
    generate_clarification_questions,
    judge_vague_question,
    rewrite_query,
    route_query,
    synthesize_final_answer,
)
from ..rag.profile_evolution import apply_profile_evolution
from ..rag.assets import asset_url, original_url
from ..rag.document_registry import get_document, list_documents as list_registered_documents
from ..rag.hybrid_pipeline import SHARED_COLLECTION

router = APIRouter(prefix="/api/v1", tags=["retrieval"])
UPLOAD_DIR = DATA_DIR / "uploads"


class SearchRequest(BaseModel):
    collection: str | None = None
    question: str
    scope: Literal["auto", "all", "selected"] = "auto"
    document_ids: list[str] = Field(default_factory=list, max_length=20)
    top_k: int = Field(default=5, ge=1, le=20)


class ChatRequest(SearchRequest):
    model: str | None = Field(default=None, max_length=80)
    conversation_id: str | None = Field(default=None, max_length=64)


class _PipelineCache:
    """进程内缓存 collection -> pipeline，避免重复加载 Embedding。"""

    def __init__(self):
        self._pipelines = {}

    def get(self, collection: str, with_llm: bool = True):
        from ..rag.hybrid_pipeline import HybridRAGPipeline

        key = (collection, with_llm)
        if key not in self._pipelines:
            self._pipelines[key] = HybridRAGPipeline(
                collection,
                LLM_API_KEY,
                milvus_host=MILVUS_HOST,
                milvus_port=MILVUS_PORT,
                with_llm=with_llm,
            )
        return self._pipelines[key]


_pipeline_cache = _PipelineCache()

# 每文档多样性上限：单库全局 RRF 把 top-K 集中到最相似的单个文档，削弱跨
# 文档覆盖（迁移后关系型 all_docs@5 从 0.4667 掉到 0.30）。按相关性顺序贪心
# 选取、每 document_id 至多 N 条，是 MMR 式最大源多样性的轻量实现：
# 6 文档受限 R@5 0.9459→1.0000、关系型 all_docs@5 0.30→0.60。N 越小越
# 偏多样性、越牺牲单文档上下文深度；cap=2 在两组评估上均优于 cap=3。
MAX_PER_DOC_DIVERSITY = 2


def _hidden_document_ids() -> set[str]:
    """有 upload 记录但非 approved 的 document_id 集合（Phase 2 可见性规则）。

    不吞 SQLAlchemyError——由调用方 `_visible_document_records` 统一降级处理。
    """
    return org.hidden_document_ids()


def _degraded_catalog_from_disk() -> list[dict]:
    """MySQL 不可用时的降级目录：仅靠 uploads 目录重建（单共享 collection）。

    单库迁移后 collection 恒为 rag_all，不再需要按 document_id 反推 collection。
    Milvus 也不可用时返回空目录（知识库为空，不崩，避免对死库发起检索）。
    降级模式不做 approved 过滤（所有落盘文件可见），保证个人检索问答仍可用。
    """
    try:
        from pymilvus import connections, utility

        connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)
        if not utility.has_collection(SHARED_COLLECTION):
            return []
    except Exception as exc:
        print(f"[retrieval] 降级目录无法读取 Milvus，返回空: {exc}")
        return []

    result: list[dict] = []
    for path in sorted(UPLOAD_DIR.glob("*"), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        document_id = path.stem
        result.append(
            {
                "document_id": document_id,
                "filename": path.name,
                "source_path": str(path),
                "collection_name": SHARED_COLLECTION,
                "topic_label": path.stem,
                "source_type": path.suffix.lstrip("."),
                "size": path.stat().st_size,
                "degraded": True,
            }
        )
    return result


def _visible_document_records() -> list[dict]:
    """注册文档里对当前成员可见的（approved 或 legacy 无 upload 记录）。

    MySQL 不可用时降级为磁盘目录 + Milvus 重建（`_degraded_catalog_from_disk`，
    不做校验过滤），保证个人检索问答仍可用。
    """
    try:
        hidden = _hidden_document_ids()
        records = list_registered_documents(UPLOAD_DIR)
    except SQLAlchemyError as exc:
        print(f"[retrieval] 数据库不可用，退化为磁盘目录（无校验过滤）: {exc}")
        return _degraded_catalog_from_disk()
    if not hidden:
        return records
    return [
        item
        for item in records
        if item.get("document_id") not in hidden
    ]


# 文档子章节标题缓存：{document_id: (读取时间, 摘要串)}。单库后所有文档共一个
# collection，缓存键从 collection 换到 document_id，避免跨文档串读。
_SUBSECTION_CACHE: dict[str, tuple[float, str]] = {}
_SUBSECTION_TTL = 600


def _subsection_summary(document_id: str) -> str:
    """返回该文档的顶层子章节标题（如「声明；赋值；交换；比较」）。

    供 router 看到文档级主题之外的具体小节（比较/相等/类型转换…），避免只凭
    「变量」「数据类型」这类文件主题路由选错文档。Milvus 不可用或没有子章节
    时返回空串，不阻塞路由（降级为只看文件名+主题）。
    """
    now = time.monotonic()
    cached = _SUBSECTION_CACHE.get(document_id)
    if cached and now - cached[0] < _SUBSECTION_TTL:
        return cached[1]
    summary = ""
    try:
        pipeline = _pipeline_cache.get(SHARED_COLLECTION, with_llm=False)
        rows = pipeline.collection.query(
            expr=f'document_id == "{document_id}"', output_fields=["heading_path"]
        )
        headings: list[str] = []
        for row in rows:
            path = str(row.get("heading_path") or "").strip()
            if not path:
                continue
            # 顶级标题往往等于文件主题（topic_label 已给 router），取第二级
            # 小节（如「变量 > 比较」里的「比较」）才有区分度。
            parts = [p.strip() for p in path.split(">")]
            sub = parts[1] if len(parts) >= 2 else ""
            if sub and sub not in headings:
                headings.append(sub)
        summary = "；".join(headings[:8])
    except Exception as exc:
        print(f"[retrieval] 子章节读取失败 {document_id}: {exc}")
    _SUBSECTION_CACHE[document_id] = (now, summary)
    return summary


def _router_catalog() -> list[dict]:
    """给 router 的文档目录：附上每个文档的子章节标题，让路由能看到小节内容。

    只在 agent 路由路径用（gateway.document_catalog），不影响其他调用方。
    """
    records = _visible_document_records()
    for record in records:
        subsections = _subsection_summary(record.get("document_id", ""))
        if subsections:
            record["subsections"] = subsections
    return records


def _uploaded_collections() -> list[str]:
    return [SHARED_COLLECTION] if _visible_document_records() else []


def _collection_for_document(document_id: str) -> str:
    # 单库迁移后所有文档共用一个 collection，文档身份靠 chunk 上的 document_id
    # 分区，collection 名恒为 rag_all。
    return SHARED_COLLECTION


def _resolve_collections(req: SearchRequest) -> list[str]:
    # 单共享 collection：检索永远发生在 rag_all 上，范围收窄走 document_ids filter。
    if req.collection and req.collection != SHARED_COLLECTION:
        raise HTTPException(status_code=400, detail="Collection 名称无效，检索范围为单共享库 rag_all")
    return [SHARED_COLLECTION]


def _resolve_document_filter(req: SearchRequest) -> list[str] | None:
    """把 scope/document_ids 解析成 Milvus 的 document_id 过滤集。

    - scope=selected：必须给 document_ids（无则 400），只搜指定文档。
    - scope=auto/all 且带了 document_ids：仍按指定文档收窄（宽松语义）。
    - 其余返回 None（全库检索）。
    """
    if req.scope == "selected":
        if not req.document_ids:
            raise HTTPException(status_code=400, detail="selected 范围需要 document_ids")
        return list(req.document_ids)
    if req.document_ids:
        return list(req.document_ids)
    return None


def _serialize_source(item: dict) -> dict:
    chunk = item["chunk"]
    record = get_document(chunk.get("document_id", "")) or {}
    metadata = chunk.get("metadata", {}) or {}
    return {
        "text": chunk.get("content", ""),
        "document_id": chunk.get("document_id", ""),
        "filename": chunk.get("filename", ""),
        "topic_label": (chunk.get("metadata") or {}).get("topic_label") or record.get("topic_label", ""),
        "original_url": original_url(chunk.get("document_id", "")),
        "asset_url": asset_url(chunk.get("document_id", ""), chunk.get("image_path")),
        "page": chunk.get("page_number") or None,
        "heading_path": chunk.get("heading_path", ""),
        "source_type": chunk.get("source_type", ""),
        "content_type": chunk.get("content_type", "text"),
        "image_path": chunk.get("image_path") or None,
        "bbox": chunk.get("bbox", []),
        "confidence": chunk.get("confidence", -1.0),
        "metadata": metadata,
        "parent_chunk_id": metadata.get("parent_chunk_id"),
        "chunk_level": metadata.get("chunk_level", 0),
        "score": item["score"],
        "rrf_score": item.get("rrf_score"),
        "signals": item.get("signals", {}),
        "origins": item["origins"],
    }


_QUESTION_WORDS = ("什么", "如何", "怎么", "是否", "为何", "为什么", "哪些", "能否")
_FUNCTION_CHARS = "的是吗呢吧么"
_ENTITY_ATTRIBUTES = (
    "学号", "姓名", "电话", "手机号", "邮箱", "班级", "专业", "学院", "地址", "身份证",
)


def _is_meaningful_term(term: str) -> bool:
    if not term or any(word in term for word in _QUESTION_WORDS):
        return False
    return not any(char in term for char in _FUNCTION_CHARS)


def _is_meaningful_phrase(phrase: str) -> bool:
    if not phrase or any(word in phrase for word in _QUESTION_WORDS):
        return False
    return phrase[0] not in _FUNCTION_CHARS and phrase[-1] not in _FUNCTION_CHARS


def _query_terms(question: str) -> set[str]:
    """Extract Chinese bigrams and Latin terms for document-level routing."""
    from ..rag.hybrid.bm25_store import BM25Store

    return {
        term
        for term in BM25Store._tokenize(question)
        if len(term) >= 2 and _is_meaningful_term(term)
    }


def _query_phrases(question: str) -> set[str]:
    """Keep three-character Chinese phrases to avoid bigram false positives."""
    phrases: set[str] = set()
    for segment in re.findall(r"[一-鿿]{3,}", question):
        phrases.update(
            phrase
            for index in range(len(segment) - 2)
            for phrase in (segment[index : index + 3],)
            if _is_meaningful_phrase(phrase)
        )
    return phrases


def _lexical_hits(question: str, text: str) -> int:
    terms = _query_terms(question)
    return sum(1 for term in terms if term in text)


def _query_entity_anchors(question: str) -> set[str]:
    """Extract the subject in questions such as ``张三的学号是什么``."""
    anchors: set[str] = set()
    attributes = "|".join(_ENTITY_ATTRIBUTES)
    pattern = rf"([一-鿿]{{2,12}})\s*的\s*(?:{attributes})"
    for match in re.finditer(pattern, question):
        candidate = re.sub(r"^(?:请问|告诉我|查询|查一下|想知道)", "", match.group(1))
        if len(candidate) >= 2:
            anchors.add(candidate)
    return anchors


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _anchor_hits(anchors: set[str], text: str) -> int:
    compact = _compact_text(text)
    return sum(1 for anchor in anchors if anchor in compact)


def _relevance_score(
    question: str,
    chunk: dict,
    signals: dict,
    rank: int,
    total: int,
    extra_phrases: set[str] | None = None,
    *,
    w_vector: float = 0.65,
    w_term: float = 0.25,
    w_rank: float = 0.10,
) -> float:
    """Return a display score in [0, 1]; RRF remains an internal rank signal."""
    text = str(chunk.get("content", ""))
    terms = _query_terms(question)
    phrases = _query_phrases(question) | (extra_phrases or set())
    entity_anchors = _query_entity_anchors(question)
    term_ratio = min(1.0, _lexical_hits(question, text) / max(1, len(terms)))
    phrase_ratio = min(1.0, _anchor_hits(phrases, text) / max(1, len(phrases)))
    entity_ratio = min(1.0, _anchor_hits(entity_anchors, text) / max(1, len(entity_anchors)))
    rank_ratio = 1.0 - ((rank - 1) / max(1, total - 1))

    raw_vector = float(signals.get("vector", -1.0))
    vector_score = max(0.0, min(1.0, (raw_vector + 1.0) / 2.0))

    if entity_anchors and entity_ratio:
        # An exact entity match is strong evidence; field terms distinguish the
        # answer-bearing block from another mention of the same person.
        return round(min(0.99, 0.82 + 0.10 * term_ratio + 0.06 * phrase_ratio), 4)

    # 语义相似度主导。短语命中只在语义也支持时小幅加权——否则纯标题/
    # 碰巧重复短语的 chunk（如"可见性"）会压过语义正确但不含该短语的答案。
    score = w_vector * vector_score + w_term * term_ratio + w_rank * rank_ratio
    if phrase_ratio:
        score += 0.06 * min(1.0, vector_score * phrase_ratio)
    return round(max(0.0, min(0.95, score)), 4)


def _expand_learning_question(question: str) -> str:
    """Add stable Chinese learning synonyms for retrieval only."""
    normalized = question.lower()
    terms: list[str] = []
    go_context = "go" in normalized or any(term in question for term in ("变量", "标识符", "包", "可见性"))
    if go_context and any(term in normalized for term in ("全局变量", "局部变量", "包外", "其他包", "导入", "导出", "访问")):
        terms.extend(("可见性", "公有", "私有", "大写", "小写", "包内", "包外"))
    if "标识符" in question:
        terms.extend(("命名规则", "名称", "类型", "变量"))
    suffix = " ".join(dict.fromkeys(terms))
    return f"{question} {suffix}".strip() if suffix else question


def _heading_matches_intent(question: str, heading_path: str) -> bool:
    return "可见性" in question and "可见性" in heading_path


def _federated_search(
    question: str,
    collections: list[str],
    top_k: int,
    *,
    document_ids: list[str] | None = None,
    rrf_k: int = 60,
    w_vector: float = 0.65,
    w_term: float = 0.25,
    w_rank: float = 0.10,
) -> tuple[list[dict], dict]:
    """全局检索：单共享 collection（rag_all）上一次 BM25+向量+RRF 融合。

    文档级路由门控已删除（74 题消融：路由只多救 1/74 题 q102、0.001 分硬币
    翻转、不省算力）。单库后所有文档 chunk 同处一个 collection，RRF 排名
    跨文档直接可比——当初「每文档一 collection」导致的跨库 rank 制榜首平局
    （R@1=0.465）从根上消失。范围收窄靠 Milvus `document_id in [...]` filter
    （pipeline.search 的 document_ids），最终排序由 _relevance_score 给出
    可解释分数。
    """
    retrieval_question = _expand_learning_question(question)
    anchor_phrases = _query_phrases(question) | _query_phrases(retrieval_question)
    try:
        pipeline = _pipeline_cache.get(SHARED_COLLECTION, with_llm=False)
        # 跨进程新鲜度：worker 单独进程入库/删除后，本进程内存池可能滞后。
        # count(*) 聚合（尊重 tombstone）是检索视角的真值，与池大小不符即触发
        # 全量 reload（覆盖新增/删除）；不能用 collection.num_entities——它把
        # 已删除的 tombstone 行也计入，删除后恒与池大小不符导致每次检索都全量
        # 重载。重入库会换一批新 id 而计数不变，靠候选里查不到 id 再 reload 兜底。
        current = pipeline.collection.query(
            expr="chunk_index >= 0", output_fields=["count(*)"]
        )[0]["count(*)"]
        if current != len(pipeline._pool_by_id):
            pipeline._load_bm25_from_milvus()
        ranked = pipeline.search(
            retrieval_question, top_k=max(12, top_k * 3), rrf_k=rrf_k,
            document_ids=document_ids,
        )
    except Exception as exc:
        # Milvus 不可用/未迁移时整个库不可检索，返回空而不是崩（降级为
        # 「资料中没有找到相关内容」，与旧版单 collection 故障语义一致）。
        print(f"[retrieval] 全局检索失败 {SHARED_COLLECTION}: {exc}")
        return [], {
            "mode": "selected" if document_ids else "global",
            "candidate_documents": len(collections),
            "used_documents": [],
            "skipped_collections": [SHARED_COLLECTION],
            "routed_documents": 0,
            "routing_strategy": "relevance",
        }

    candidates: list[dict] = []
    reloaded = False
    for rank, item in enumerate(ranked, start=1):
        chunk = pipeline._chunk_pool_by_index(item["index"])
        if chunk is None:
            # 进程内池过期（重入库换新 id）：重载一次后再查，仍无则跳过。
            if not reloaded:
                pipeline._load_bm25_from_milvus()
                reloaded = True
                chunk = pipeline._chunk_pool_by_index(item["index"])
            if chunk is None:
                continue
        # RRF 融合分直接作为候选排序分；0 分兜底（本链路无 gate 注入，防御性）。
        rank_score = float(item.get("score", 0.0))
        if rank_score <= 0.0:
            rank_score = 1.0 / (rrf_k + rank)
        if _heading_matches_intent(retrieval_question, str(chunk.get("heading_path", ""))):
            rank_score += 0.012
        candidates.append(
            {
                "collection": SHARED_COLLECTION,
                "index": item["index"],
                "score": rank_score,
                "origins": item.get("origins", []),
                "signals": item.get("signals", {}),
                "chunk": chunk,
            }
        )

    # RRF 只是候选生成顺序。用户看到的顺序必须按可解释分数排，否则单库里
    # 多个文档可能各自贡献一个 RRF 榜首，掩盖真正的更优匹配。
    for rank, item in enumerate(candidates, start=1):
        item["rrf_score"] = item["score"]
        item["score"] = _relevance_score(
            question,
            item["chunk"],
            item["signals"],
            rank,
            len(candidates),
            extra_phrases=anchor_phrases,
            w_vector=w_vector,
            w_term=w_term,
            w_rank=w_rank,
        )
    candidates.sort(key=lambda item: (item["score"], item["rrf_score"]), reverse=True)
    # 每文档多样性上限：见 MAX_PER_DOC_DIVERSITY。贪心按可解释分取，同一
    # document_id 至多 N 条后再取下一文档，保证 top-K 跨源覆盖（不改变榜首——
    # 全局第一永远是第一，只把集中在一篇的后续位置让给其它文档）。
    fused: list[dict] = []
    per_doc_counts: dict[str, int] = {}
    for item in candidates:
        document_id = (item["chunk"] or {}).get("document_id", "")
        if per_doc_counts.get(document_id, 0) >= MAX_PER_DOC_DIVERSITY:
            continue
        fused.append(item)
        per_doc_counts[document_id] = per_doc_counts.get(document_id, 0) + 1
        if len(fused) >= top_k:
            break

    used_documents: dict[str, dict] = {}
    for rank, item in enumerate(fused, start=1):
        chunk = item["chunk"]
        document_id = chunk.get("document_id", "")
        if document_id not in used_documents:
            record = get_document(document_id) or {}
            used_documents[document_id] = {
                "document_id": document_id,
                "filename": chunk.get("filename", ""),
                "topic_label": (chunk.get("metadata") or {}).get("topic_label") or record.get("topic_label", ""),
                "score": item["score"],
                "reason": f"命中第 {rank} 个相关知识块",
            }

    routing = {
        "mode": "selected" if document_ids else "global",
        "candidate_documents": len(collections),
        "used_documents": list(used_documents.values()),
        "skipped_collections": [],
        "routed_documents": len(collections),
        "routing_strategy": "relevance",
    }
    return fused, routing


def _run_search(req: SearchRequest) -> tuple[list[dict], dict]:
    collections = _resolve_collections(req)
    document_ids = _resolve_document_filter(req)
    return _federated_search(req.question, collections, req.top_k, document_ids=document_ids)


@router.get("/models")
async def available_models():
    """Return model metadata without exposing provider credentials."""
    return {"models": list_model_configs(), "default_model": LLM_MODEL}


@router.post("/retrieval/search")
async def retrieval_search(req: SearchRequest):
    """只检索，不生成答案，默认自动搜索全部资料。"""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="请输入问题")
    fused, routing = _run_search(req)
    return {
        "query": req.question,
        "results": [_serialize_source(item) for item in fused],
        "count": len(fused),
        "routing": routing,
    }


def _validate_model(model_id: str | None) -> str:
    selected = model_id or LLM_MODEL
    try:
        config = get_model_config(selected)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not config["ready"]:
        raise HTTPException(status_code=400, detail=f"模型 {selected} 尚未配置 API 密钥")
    return selected


async def _answer(req: ChatRequest) -> dict:
    selected_model = _validate_model(req.model)
    fused, routing = _run_search(req)
    if not fused:
        return {
            "answer": "资料中没有找到相关内容。",
            "model": selected_model,
            "sources": [],
            "used_documents": [],
            "retrieval": {"scope": req.scope, **routing, "top_k": req.top_k, "rerank": "rrf"},
        }

    generator = _pipeline_cache.get(fused[0]["collection"], with_llm=True)
    result = generator.answer_from_fused(
        req.question,
        fused,
        top_k=req.top_k,
        model_id=selected_model,
        retrieval={"scope": req.scope, **routing, "top_k": req.top_k, "rerank": "rrf"},
    )
    result["used_documents"] = routing["used_documents"]
    return result


@router.post("/chat/ask")
async def chat_ask(req: ChatRequest):
    """自动资料路由后生成带来源答案。"""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="请输入问题")
    return await _answer(req)


@router.post("/chat/ask-with-llm")
async def chat_ask_with_llm(req: ChatRequest):
    """兼容旧接口，行为与 /chat/ask 相同。"""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="请输入问题")
    return await _answer(req)


# ---------------------------------------------------------------------------
# Agentic RAG（Phase 1）：/chat/agent 用 LangChain AgentExecutor 做多轮检索推理
# ---------------------------------------------------------------------------

# 对话自动压缩（Phase 2C）：超过阈值后把旧消息折叠进滚动摘要，
# 只保留最近 RECENT_WINDOW 条原文。摘要存为 role="system" 的消息。
RECENT_WINDOW = 8
COMPRESS_THRESHOLD = 14
# 历史上下文 token 硬预算（预防性守卫）：调 LLM 前把「摘要 + 最近原文」按 token 封顶，
# 超限时从最旧开始丢（最新一条强制保留），防止粘贴超长文本撑爆模型上下文窗口。
HISTORY_MAX_TOKENS = 3000
# 单条历史消息进上下文时的正文截断长度。
HISTORY_MSG_CHARS = 3000
# 画像进化的触发门槛：回答超过该长度才做一次 LLM 画像判断（省 token，弱信号不误判）
PROFILE_EVOLUTION_MIN_ANSWER = 200
# 证据充分性门控（Phase 3）：top-k 里最高分低于该阈值视为证据不足；
# 前置探针只在 router 选 selected 且 req.scope=auto 时做一次小范围检索。
EVIDENCE_WEAK_THRESHOLD = 0.4
PROBE_TOP_K = 5

_AGENT_LLMS: dict[str, ChatOpenAI] = {}


class TokenUsageCallback(BaseCallbackHandler):
    """累加一次问答全链路（改写/路由/ReAct 循环/画像/压缩）的 input/output token。

    挂在 llm.with_config({"callbacks": [...]}) 上，经 bind_tools / AgentExecutor
    传递给所有 LLM 调用。DeepSeek 等 OpenAI 兼容端点返回 usage 时才有数，
    拿不到就 0，不影响链路。
    """

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0

    def on_llm_end(self, response, **kwargs) -> None:
        try:
            for generation_list in response.generations:
                for generation in generation_list:
                    usage = getattr(generation.message, "usage_metadata", None) or {}
                    self.input_tokens += usage.get("input_tokens") or 0
                    self.output_tokens += usage.get("output_tokens") or 0
        except Exception:
            pass  # 用法字段缺失/异常一律忽略，不打断问答


def _get_agent_llm(model_id: str) -> ChatOpenAI:
    """按模型缓存 ChatOpenAI 客户端，凭据来自服务端白名单。"""
    if model_id not in _AGENT_LLMS:
        config = get_model_config(model_id)
        _AGENT_LLMS[model_id] = ChatOpenAI(
            base_url=str(config["base_url"]),
            api_key=str(config["api_key"]),
            model=model_id,
            temperature=0.2,
            max_tokens=1024,
            # 超时 + 重试：DeepSeek 偶发慢响应/限流，卡死单次请求最多
            # LLM_REQUEST_TIMEOUT 秒；瞬时失败按指数退避自动重试。
            timeout=LLM_REQUEST_TIMEOUT,
            max_retries=LLM_MAX_RETRIES,
        )
    return _AGENT_LLMS[model_id]


def _build_gateway() -> SimpleNamespace:
    """把现有检索能力绑定成 RetrievalGateway 协议。"""
    return SimpleNamespace(
        resolve_collections=lambda scope, ids: _resolve_collections(
            SearchRequest(question="", scope=scope, document_ids=ids, top_k=5)
        ),
        federated_search=_federated_search,
        serialize_source=_serialize_source,
        document_catalog=_router_catalog,
    )


def _used_documents_from(fused_items: list[dict]) -> list[dict]:
    """从最终命中的证据块汇总 used_documents，与 /chat/ask 结构一致。"""
    used: dict[str, dict] = {}
    for item in fused_items:
        chunk = item["chunk"]
        document_id = chunk.get("document_id", "")
        if not document_id:
            continue
        record = get_document(document_id) or {}
        if document_id not in used:
            used[document_id] = {
                "document_id": document_id,
                "filename": chunk.get("filename", ""),
                "topic_label": (chunk.get("metadata") or {}).get("topic_label") or record.get("topic_label", ""),
                "score": item["score"],
                "reason": "Agent 检索命中",
            }
        else:
            used[document_id]["score"] = max(used[document_id]["score"], item["score"])
    return list(used.values())


def _profile_for(user_id: str) -> dict | None:
    """加载用户画像；从未设置或 DB 不可用时返回 None（不注入，走默认风格）。"""
    try:
        profile = org.get_profile(user_id)
    except SQLAlchemyError as exc:
        print(f"[retrieval] 画像读取失败（数据库不可用），按无画像处理: {exc}")
        return None
    if profile is None:
        return None
    return {
        "subjects": json.loads(profile.get("subjects") or "[]"),
        "weak_points": json.loads(profile.get("weak_points") or "[]"),
        "preferred_style": profile.get("preferred_style", "standard"),
    }


def _load_chat_history(conversation_id: str, user_id: str) -> list:
    """把会话里的消息组装成 AgentExecutor 接受的 chat_history（BaseMessage 列表）。

    校验会话归属（404）。Phase 2C 自动压缩：若存在滚动摘要（role="system" 消息），
    前置为 SystemMessage 作为背景；原文只取最近 RECENT_WINDOW 条（更早的已折叠进摘要）。
    """
    conversation = org.get_conversation(conversation_id)
    if conversation is None or conversation["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    messages = org.list_messages(conversation_id)
    summary = next(
        (m["content"] for m in messages if m["role"] == "system"), None
    )
    recent = [m for m in messages if m["role"] != "system"][-RECENT_WINDOW:]

    history: list = []
    if summary:
        history.append(
            SystemMessage(content=f"以下是与此学生此前的对话摘要（作为背景，不必重复验证）：\n{summary}")
        )

    # 历史上下文 token 硬预算：摘要占掉一部分后，最近原文从新到旧装进剩余预算，
    # 最旧装不下的直接丢弃；最新一条强制进（保证「刚才那个」类追问有紧邻上下文）。
    budget = HISTORY_MAX_TOKENS
    if history:
        budget -= estimate_tokens(str(history[0].content))
    loaded: list = []
    for idx, msg in enumerate(reversed(recent)):
        content = str(msg["content"] or "")[:HISTORY_MSG_CHARS].strip()
        if not content:
            continue
        tokens = estimate_tokens(content)
        if idx > 0 and budget - tokens < 0:
            break
        budget -= tokens
        loaded.append(
            HumanMessage(content=content) if msg["role"] == "user" else AIMessage(content=content)
        )
    history.extend(reversed(loaded))
    return history


def _summarize(llm, existing: str, folded: str) -> str:
    """把「已有摘要 + 折叠的旧对话」合并成一份更新的滚动摘要。"""
    prompt = (
        "你是学习对话的摘要助手。结合已有摘要与新增对话，输出一份更新后的滚动摘要。\n"
        "要求：保留学生提问过的知识点、暴露的薄弱点、已给出的结论与关键步骤；"
        "用简洁中文，控制在 500 字以内；不要编造没有出现过的内容。\n\n"
        f"已有摘要：\n{existing or '（无）'}\n\n"
        f"新增对话：\n{folded}\n\n输出新摘要："
    )
    response = llm.invoke(prompt)
    return str(getattr(response, "content", response)).strip()


def _maybe_compress_conversation(conversation_id: str, llm) -> None:
    """消息超过 COMPRESS_THRESHOLD 时，把旧消息折叠进滚动摘要。

    只保留最近 RECENT_WINDOW 条原文 + 一条新摘要（DB 里消息数有界）。
    摘要生成失败则保留原消息不动（不丢数据）。
    """
    try:
        messages = org.list_messages(conversation_id)
    except SQLAlchemyError as exc:
        print(f"[chat/agent] 压缩前读取消息失败（数据库不可用），跳过: {exc}")
        return
    if len(messages) <= COMPRESS_THRESHOLD:
        return

    summary_msg = next((m for m in messages if m["role"] == "system"), None)
    existing = summary_msg["content"] if summary_msg else ""
    keep_ids = {m["id"] for m in messages[-RECENT_WINDOW:]}
    fold_messages = [
        m for m in messages
        if m["id"] not in keep_ids and m["role"] != "system"
    ]
    if not fold_messages:
        return
    folded = "\n".join(
        f"{'学生' if m['role'] == 'user' else '老师'}：{m['content']}"
        for m in fold_messages
    )
    try:
        new_summary = _summarize(llm, existing, folded)
    except Exception as exc:
        print(f"[chat/agent] 压缩摘要生成失败，保留原消息: {exc}")
        return

    remove_ids = [m["id"] for m in fold_messages]
    if summary_msg:
        remove_ids.append(summary_msg["id"])
    org.delete_messages(conversation_id, remove_ids)
    org.add_message(conversation_id, "system", new_summary, metadata_json='{"summary": true}')


def _evidence_sufficient(fused: list[dict]) -> tuple[bool, str]:
    """后置证据判定（Phase 3）：空 → no_evidence；最高分低于阈值 → weak_evidence。

    只上报不覆盖：agent 循环已自行处理「没找到」，这里给前端一个显式信号。
    """
    if not fused:
        return False, "no_evidence"
    best = max((float(item.get("score", 0.0)) for item in fused), default=0.0)
    if best < EVIDENCE_WEAK_THRESHOLD:
        return False, "weak_evidence"
    return True, "sufficient"


_CLARIFICATION_LEADINS = {
    "no_evidence": "你问的问题在资料里没有找到相关内容。为了更准确地帮你，请先确认一下你想问的是：",
    "weak_evidence": "找到的相关内容比较有限，为了避免答偏，请先确认一下你想问的是：",
    "vague": "你问的问题范围比较宽泛，为了给你更有针对性的回答，请先确认一下你想了解的是：",
}


def run_clarification_gate(
    llm, question: str, rewritten: str, ranked: list[dict]
) -> SimpleNamespace | None:
    """澄清门控（Phase 5）：开关开 + 检索证据不足（no/weak）**或问题模糊（vague）**时，
    用 LLM 生成澄清问题反问用户，而不是硬凑答案。

    返回 SimpleNamespace(triggered=True, reason, vague, questions, prompt)；未命中返回 None。
    - 证据不足（no_evidence/weak_evidence）：直接触发。
    - 证据充分但 LLM 判定问题本身模糊（如「go语言怎么学」检索到教程仍太泛）：也触发。
    - LLM 模糊判断失败默认 False（不误伤正常题）；澄清问题生成失败/空时仍触发，
      回退一条基于改写后问题的通用澄清（宁反问不硬凑）。
    """
    if not CLARIFICATION_GATE_ENABLED:
        return None
    sufficient, reason = _evidence_sufficient(ranked)
    vague = False
    if sufficient:
        # 证据够但问题太泛 → 仍需澄清（纯检索阈值区分不出「真模糊」）
        vague = judge_vague_question(llm, question, rewritten, ranked)
        if not vague:
            return None
        reason = "vague"
    questions = generate_clarification_questions(llm, question, rewritten, ranked)
    if not questions:
        questions = [f"你具体想了解「{rewritten or question}」的哪一方面？"]
    return SimpleNamespace(
        triggered=True,
        reason=reason,
        vague=vague,
        questions=questions[:2],
        prompt=_CLARIFICATION_LEADINS[reason],
    )


def _probe_and_escalate(req: ChatRequest, decision: RouterDecision, question: str) -> tuple[RouterDecision, bool]:
    """证据前置门控（Phase 3）：router 选了 selected（且用户范围是 auto）时，
    对指定资料做一次小范围检索探针；无命中则升级为全库检索。

    用户显式锁定 selected 不升级（尊重意图，仅标记）；探针异常/DB 挂保持原决策。
    """
    if req.scope != "auto" or decision.scope != "selected" or not decision.document_ids:
        return decision, False
    try:
        collections = _resolve_collections(
            SearchRequest(question=question, scope="selected", document_ids=decision.document_ids, top_k=PROBE_TOP_K)
        )
        probe, _ = _federated_search(
            question, collections, PROBE_TOP_K, document_ids=decision.document_ids or None
        )
    except Exception as exc:
        print(f"[chat/agent] 证据探针失败，保持原路由: {exc}")
        return decision, False
    if not probe:
        return RouterDecision(
            scope="all",
            document_ids=[],
            rationale=f"{decision.rationale or '路由'}（指定资料无命中，升级为全库检索）",
        ), True
    return decision, False


def _prepare_conversation(user_id: str, req: ChatRequest) -> tuple[str | None, list]:
    """落库 workflow 的准备段：带 conversation_id 加载历史，不带则新建会话。

    DB 不可用时降级为单轮（不查历史、不落库，仅返回答案），语义与 Phase 2 一致。
    """
    conversation_id = req.conversation_id
    chat_history: list = []
    if conversation_id:
        try:
            chat_history = _load_chat_history(conversation_id, user_id)
        except HTTPException:
            raise
        except SQLAlchemyError as exc:
            print(f"[chat/agent] 加载会话历史失败（数据库不可用），按单轮处理: {exc}")
            conversation_id = None
            chat_history = []
    else:
        try:
            conversation = org.create_conversation(user_id, "", req.question[:60])
            conversation_id = conversation["id"]
        except SQLAlchemyError as exc:
            print(f"[chat/agent] 创建会话失败（数据库不可用），跳过持久化: {exc}")
            conversation_id = None
    return conversation_id, chat_history


def stage_intent(
    req: ChatRequest, llm, gateway, profile: dict | None, chat_history: list | None = None
) -> SimpleNamespace:
    """阶段 1 意图识别：查询改写 → 问题扩展 → 意图路由 → 前置证据门控。

    单次 LLM 判断的 workflow 段（改写、扩展、路由各一次调用，无循环）。
    chat_history（BaseMessage 列表）传给改写让指代消解（「他」→「go语言」）；
    扩展（QUERY_EXPANSION_ENABLED 时）从改写后问题生成补充检索子问题。
    """
    start = time.perf_counter()
    rewritten = (
        rewrite_query(llm, req.question, chat_history)
        if QUERY_REWRITE_ENABLED
        else req.question
    )
    expansions = (
        expand_query(llm, rewritten, chat_history) if QUERY_EXPANSION_ENABLED else []
    )
    if req.scope == "selected" and req.document_ids:
        decision = RouterDecision(
            scope=req.scope,
            document_ids=req.document_ids,
            rationale="用户指定范围",
        )
    else:
        decision = route_query(llm, gateway, rewritten, gateway.document_catalog(), profile=profile)
    decision, escalated = _probe_and_escalate(req, decision, rewritten)
    return SimpleNamespace(
        rewritten=rewritten,
        expansions=expansions,
        decision=decision,
        escalated=escalated,
        ms=round((time.perf_counter() - start) * 1000, 1),
    )


def stage_react(
    req: ChatRequest,
    llm,
    gateway,
    decision: RouterDecision,
    rewritten: str,
    chat_history: list,
    profile: dict | None,
    expansions: list[str] | tuple[str, ...] = (),
) -> SimpleNamespace:
    """阶段 2 ReAct 检索推理：唯一 agentic 段。

    AgentExecutor 在 Thought-Action-Observation 循环里自主决定检索范围、
    补检次数与停止时机，证据充分才输出最终答案（检索与作答不可拆开）。
    """
    start = time.perf_counter()
    ctx = AgentChatContext()
    executor = build_executor(
        llm,
        ctx,
        gateway,
        decision,
        profile=profile,
        rewritten_question=rewritten,
        # Phase 2.2 双路召回：原问题作为检索副路，改写路漏掉的词由原路补回。
        original_question=req.question if DUAL_RECALL_ENABLED else None,
        # Phase 4 问题扩展：扩展子问题作为补充检索路，broad 问题不漏具体子主题。
        expansion_queries=tuple(expansions),
    )
    result = executor.invoke({"input": req.question, "chat_history": chat_history})
    answer = str(result.get("output") or "")
    # 兜底：ReAct 循环把迭代全花在补检上、被 max_iterations 截断时，executor 把
    # "Agent stopped due to max iterations." 直接当 output。这里改用已检索证据强制作答，
    # 有证据给真实答案，没证据明确回答资料中未找到，绝不把引擎报错串返回给用户。
    if answer.strip().lower().startswith("agent stopped"):
        print(f"[retrieval] agent 超限截断，改用证据兜底作答: {req.question[:40]}")
        answer = (
            synthesize_final_answer(llm, ctx, req.question)
            if ctx.fused
            else "资料中没有找到相关内容。"
        )
    ranked = sorted(ctx.fused.values(), key=lambda item: item["score"], reverse=True)[: req.top_k]
    return SimpleNamespace(
        answer=answer,
        ctx=ctx,
        ranked=ranked,
        result=result,
        ms=round((time.perf_counter() - start) * 1000, 1),
    )


def stage_persist(
    conversation_id: str | None,
    question: str,
    answer: str,
    selected_model: str,
    decision: RouterDecision,
    ctx: AgentChatContext,
    ranked: list[dict],
    intermediate_steps: list,
    llm,
) -> SimpleNamespace:
    """阶段 3 落库 + 自动压缩：确定性 workflow（纯 I/O + 单次摘要 LLM 调用，无循环）。

    落 user 消息 / assistant 消息 / Agent 轨迹，长会话折叠进滚动摘要。
    DB 不可用时跳过落库（降级语义保留，不影响返回答案）。
    """
    start = time.perf_counter()
    if conversation_id:
        # 落库完整来源对象（与实时回答同形），历史回放/刷新恢复时来源照常展示；
        # 任何来源序列化异常降级为最小来源，不影响落库与回答返回。
        try:
            source_payload = [_serialize_source(item) for item in ranked]
        except Exception:
            source_payload = [
                {
                    "document_id": item["chunk"].get("document_id", ""),
                    "filename": item["chunk"].get("filename", ""),
                    "score": item["score"],
                }
                for item in ranked
            ]
        metadata_json = json.dumps(
            {
                "model": selected_model,
                "router": decision.model_dump(),
                "tool_calls": ctx.tool_calls,
                "sources": source_payload,
            },
            ensure_ascii=False,
        )
        try:
            org.add_message(conversation_id, "user", question, model=selected_model)
            assistant_msg = org.add_message(
                conversation_id, "assistant", answer, model=selected_model,
                metadata_json=metadata_json,
            )
            for index, step in enumerate(intermediate_steps):
                org.add_trace(
                    assistant_msg["id"],
                    index,
                    step[0].tool,
                    json.dumps(step[0].tool_input, ensure_ascii=False)[:2000],
                    str(step[1])[:2000],
                )
            _maybe_compress_conversation(conversation_id, llm)
        except SQLAlchemyError as exc:
            print(f"[chat/agent] 会话落库失败（数据库不可用），跳过: {exc}")
    return SimpleNamespace(
        conversation_id=conversation_id,
        ms=round((time.perf_counter() - start) * 1000, 1),
    )


def stage_profile(user_id: str, llm, question: str, answer: str) -> SimpleNamespace:
    """阶段 4 画像更新：长回答后单次 LLM 判断学生行为/薄弱点/风格，确定性合并回画像。

    无画像或任何异常都软失败，不影响问答。
    """
    start = time.perf_counter()
    if len(answer) >= PROFILE_EVOLUTION_MIN_ANSWER:
        apply_profile_evolution(user_id, llm, question, answer)
    return SimpleNamespace(
        applied=len(answer) >= PROFILE_EVOLUTION_MIN_ANSWER,
        ms=round((time.perf_counter() - start) * 1000, 1),
    )


@router.post("/chat/agent")
def chat_agent(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    """Agentic 问答：四段 workflow（意图识别 → ReAct 检索推理 → 落库压缩 → 画像更新）。

    同步 def：FastAPI 自动丢线程池执行，避免 LLM 同步阻塞事件循环。
    只有「ReAct 检索推理」段是 agentic（Thought-Action-Observation 循环）；
    其余三段是单次 LLM 判断或确定性 I/O 的 workflow 阶段。
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="请输入问题")
    try:
        check_chat_rate_limit(current_user["id"])
    except RateLimitExceeded:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    selected_model = _validate_model(req.model)
    llm = _get_agent_llm(selected_model)
    # Phase 2.1 成本统计：全链路挂 token 回调，收集改写/路由/ReAct/画像/压缩的用量。
    usage = TokenUsageCallback()
    llm = llm.with_config({"callbacks": [usage]})
    gateway = _build_gateway()
    profile = _profile_for(current_user["id"])

    start_total = time.perf_counter()
    conversation_id, chat_history = _prepare_conversation(current_user["id"], req)
    intent = stage_intent(req, llm, gateway, profile, chat_history)
    react = stage_react(
        req, llm, gateway, intent.decision, intent.rewritten, chat_history, profile,
        intent.expansions,
    )
    # Phase 5 澄清门控：ReAct 自主补检之后、落库之前判定证据是否足够。
    # 证据不足（no/weak）时返回澄清轮（反问用户），跳过 stage_persist/stage_profile——
    # 澄清轮不落库、不进画像，避免把反问当正式回答；用户澄清后再走正常链路。
    gate = run_clarification_gate(llm, req.question, intent.rewritten, react.ranked)
    if gate is not None:
        metrics = get_metrics()
        metrics.incr("chat_queries")
        metrics.observe("chat_total_ms", (time.perf_counter() - start_total) * 1000)
        metrics.observe("chat_intent_ms", intent.ms)
        metrics.observe("chat_react_ms", react.ms)
        metrics.incr("tokens_input", usage.input_tokens)
        metrics.incr("tokens_output", usage.output_tokens)
        return {
            "conversation_id": conversation_id,
            "answer": gate.prompt,
            "model": selected_model,
            "sources": [],
            "used_documents": [],
            "retrieval": {
                "strategy": "agent",
                "router": intent.decision.model_dump(),
                "tool_calls": react.ctx.tool_calls,
                "top_k": req.top_k,
                "max_iterations": MAX_ITERATIONS,
                "evidence": {
                    "sufficient": False,
                    "reason": gate.reason,
                    "escalated": intent.escalated,
                    "clarification": {"questions": gate.questions, "prompt": gate.prompt},
                },
                "rewritten_question": intent.rewritten if intent.rewritten != req.question else None,
                "stages": {
                    "intent_ms": intent.ms,
                    "react_ms": react.ms,
                    "persist_ms": 0,
                    "profile_ms": 0,
                },
            },
            "trace": [
                {
                    "tool": step[0].tool,
                    "input": step[0].tool_input,
                    "output": str(step[1])[:500],
                }
                for step in react.result.get("intermediate_steps", [])
            ],
        }
    persist = stage_persist(
        conversation_id,
        req.question,
        react.answer,
        selected_model,
        intent.decision,
        react.ctx,
        react.ranked,
        react.result.get("intermediate_steps", []),
        llm,
    )
    profile_stage = stage_profile(current_user["id"], llm, req.question, react.answer)
    metrics = get_metrics()
    metrics.incr("chat_queries")
    metrics.observe("chat_total_ms", (time.perf_counter() - start_total) * 1000)
    metrics.observe("chat_intent_ms", intent.ms)
    metrics.observe("chat_react_ms", react.ms)
    metrics.observe("chat_persist_ms", persist.ms)
    metrics.observe("chat_profile_ms", profile_stage.ms)
    metrics.incr("tokens_input", usage.input_tokens)
    metrics.incr("tokens_output", usage.output_tokens)

    sufficient, evidence_reason = _evidence_sufficient(react.ranked)

    return {
        "conversation_id": persist.conversation_id,
        "answer": react.answer,
        "model": selected_model,
        "sources": [gateway.serialize_source(item) for item in react.ranked],
        "used_documents": _used_documents_from(react.ranked),
        "retrieval": {
            "strategy": "agent",
            "router": intent.decision.model_dump(),
            "tool_calls": react.ctx.tool_calls,
            "top_k": req.top_k,
            "max_iterations": MAX_ITERATIONS,
            "evidence": {
                "sufficient": sufficient,
                "reason": evidence_reason,
                "escalated": intent.escalated,
            },
            "rewritten_question": intent.rewritten if intent.rewritten != req.question else None,
            "stages": {
                "intent_ms": intent.ms,
                "react_ms": react.ms,
                "persist_ms": persist.ms,
                "profile_ms": profile_stage.ms,
            },
        },
        "trace": [
            {
                "tool": step[0].tool,
                "input": step[0].tool_input,
                "output": str(step[1])[:500],
            }
            for step in react.result.get("intermediate_steps", [])
        ],
    }
