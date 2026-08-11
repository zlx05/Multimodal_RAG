"""混合检索 RAG Pipeline：BM25 + 向量 双路召回，RRF 融合，带溯源。

相比旧 pipeline.py（单文档、仅 text+embedding），本模块：
1. Milvus collection 保存完整溯源字段（document_id/filename/page_number/...）。
2. 同时维护 BM25 关键词索引。
3. 检索走 BM25 + 向量双路，RRF 融合。
4. 回答携带来源元数据。
"""

import os
import threading
from pathlib import Path
from typing import Any

import numpy as np
from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)

from ..core.config import (
    CONTEXTUAL_RETRIEVAL_ENABLED,
    CONTEXTUAL_RETRIEVAL_MODEL,
    LLM_MODEL,
    get_model_config,
)
from .hybrid import BM25Store, reciprocal_rank_fusion
from .model_config import EMBEDDING_MODEL
from .chunking_profiles import TEXT_CONTENT_TYPES, ChunkingProfile, group_blocks_for_profile

MILVUS_HOST = os.getenv("MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")
INDEX_BATCH_SIZE = 200
# 上下文绑定的目标块：图片描述/公式 OCR/整页扫描 OCR。检索"讲 XX 那张图/那个公式"
# 这类问题时，仅靠这些块的自身文本召回差，需要邻近正文补上下文。
CONTEXT_BINDING_TYPES = {"image_description", "formula", "image_ocr"}
# 邻近正文窗口：前块尾部 + 后块头部各取多少字
CONTEXT_WINDOW = 150


class HybridRAGPipeline:
    """一个文档对应一个 collection 的混合检索管道。

    用法：
        pipe = HybridRAGPipeline(collection_name, ...)
        pipe.build(blocks, chunker)   # blocks 来自解析器，分块后入库
        results = pipe.search(question)
        answer = pipe.answer(question, top_k=5)
    """

    _shared_embedder = None
    _embedder_lock = threading.Lock()

    def __init__(
        self,
        collection_name: str,
        api_key: str = "",
        milvus_host: str = MILVUS_HOST,
        milvus_port: str = MILVUS_PORT,
        rebuild: bool = False,
        with_llm: bool = True,
    ):
        connections.connect(alias="default", host=milvus_host, port=milvus_port)
        self.collection_name = collection_name
        self.embedder = self._load_embedder()

        if rebuild and utility.has_collection(collection_name):
            utility.drop_collection(collection_name)

        self.collection = self._get_or_create_collection()
        self.bm25 = BM25Store()
        self._chunk_pool: list[dict] = []  # 所有 chunk 的元数据池
        self._pool_by_index: dict[int, dict] = {}  # chunk_index -> chunk 元数据

        if self.collection.num_entities > 0 and not rebuild:
            self._load_bm25_from_milvus()

        self.llm = None
        self._llm_clients = {}
        if with_llm:
            self.llm = self._get_llm_client(LLM_MODEL)

    def _get_llm_client(self, model_id: str):
        """Create a cached provider client from a server-side model allowlist."""
        from openai import OpenAI

        config = get_model_config(model_id)
        if not config["ready"]:
            raise ValueError(f"模型 {model_id} 尚未配置 API 密钥")
        cache_key = (config["id"], config["base_url"])
        if cache_key not in self._llm_clients:
            self._llm_clients[cache_key] = OpenAI(
                api_key=str(config["api_key"]),
                base_url=str(config["base_url"]),
            )
        return self._llm_clients[cache_key]

    # ---------- 初始化 ----------

    @staticmethod
    def _load_embedder():
        from sentence_transformers import SentenceTransformer

        if HybridRAGPipeline._shared_embedder is None:
            with HybridRAGPipeline._embedder_lock:
                if HybridRAGPipeline._shared_embedder is None:
                    model = SentenceTransformer(EMBEDDING_MODEL)
                    print(f"Embedding ready, dim={model.get_sentence_embedding_dimension()}")
                    HybridRAGPipeline._shared_embedder = model
        return HybridRAGPipeline._shared_embedder

    def _get_or_create_collection(self) -> Collection:
        if utility.has_collection(self.collection_name):
            collection = Collection(self.collection_name)
            required_fields = {
                "document_id", "filename", "source_type", "content_type",
                "page_number", "chunk_index", "content", "heading_path",
                "image_path", "bbox", "confidence", "metadata", "embedding",
            }
            actual_fields = {field.name for field in collection.schema.fields}
            missing_fields = sorted(required_fields - actual_fields)
            if missing_fields:
                raise ValueError(
                    f"Collection {self.collection_name} 使用旧版 schema，缺少字段 {missing_fields}。"
                    "请使用新的 collection 名称或删除后重新入库。"
                )
            collection.load()
            return collection

        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="document_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="filename", dtype=DataType.VARCHAR, max_length=255),
            FieldSchema(name="source_type", dtype=DataType.VARCHAR, max_length=16),
            FieldSchema(name="content_type", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="page_number", dtype=DataType.INT64),
            FieldSchema(name="chunk_index", dtype=DataType.INT64),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="heading_path", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="image_path", dtype=DataType.VARCHAR, max_length=1024),
            FieldSchema(name="bbox", dtype=DataType.JSON),
            FieldSchema(name="confidence", dtype=DataType.FLOAT),
            FieldSchema(name="metadata", dtype=DataType.JSON),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.embedder.get_sentence_embedding_dimension()),
        ]
        schema = CollectionSchema(fields, description="Multimodal RAG chunks with source metadata")
        collection = Collection(name=self.collection_name, schema=schema)
        collection.create_index(
            field_name="embedding",
            index_params={"index_type": "AUTOINDEX", "metric_type": "COSINE", "params": {}},
        )
        collection.load()
        return collection

    def _load_bm25_from_milvus(self):
        """从已有 collection 读回文本重建 BM25（重启后恢复关键词索引）。"""
        collection = self.collection
        results = collection.query(
            expr="chunk_index >= 0",
            output_fields=[
                "id", "chunk_index", "content", "document_id", "filename",
                "page_number", "heading_path", "source_type", "content_type",
                "image_path", "bbox", "confidence", "metadata",
            ],
        )
        results.sort(key=lambda r: r["chunk_index"])
        self._chunk_pool = results
        self._pool_by_index = {r["chunk_index"]: r for r in results}
        self.bm25.build([self._search_text(r) for r in results], results)

    @staticmethod
    def _search_text(row: dict) -> str:
        metadata = row.get("metadata") or {}
        return str(metadata.get("search_text") or row.get("content") or "")

    def _contextualize_chunk(self, chunk: str, parent: str, context_prefix: str) -> str:
        """Optionally add a short document context before embedding/BM25."""
        if not CONTEXTUAL_RETRIEVAL_ENABLED:
            return ""
        try:
            client = self._get_llm_client(CONTEXTUAL_RETRIEVAL_MODEL)
            response = client.chat.completions.create(
                model=CONTEXTUAL_RETRIEVAL_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "你是中文知识库索引助手，只输出不超过80字的客观上下文，不要改写原文。",
                    },
                    {
                        "role": "user",
                        "content": (
                            f"文档/章节：{context_prefix}\n"
                            f"父级内容：{parent[:3000]}\n"
                            f"当前片段：{chunk}\n"
                            "请说明当前片段属于什么主题、回答什么问题。"
                        ),
                    },
                ],
                max_tokens=160,
                temperature=0,
            )
            return str(response.choices[0].message.content or "").strip()[:500]
        except Exception as exc:
            print(f"[hybrid] contextual retrieval fallback: {exc}")
            return ""

    # ---------- 入库 ----------

    def build(
        self,
        blocks: list[Any],
        chunker,
        profile: ChunkingProfile | None = None,
    ) -> int:
        """把解析得到的 DocumentBlock 列表分块后入库。

        Args:
            blocks: 解析器产出的 DocumentBlock 列表。
            chunker: 分块器，chunk() 方法接收文本返回块列表。

        Returns:
            入库的 chunk 数。
        """
        profile = profile or ChunkingProfile(
            id="legacy",
            label="兼容模式",
            description="",
            chunker=getattr(chunker, "name", "unknown"),
            params={},
        )
        blocks = group_blocks_for_profile(blocks, profile)
        # 图片/公式块夹在合并后的文本块之间，相邻性有语义：
        # 把邻近正文片段并入其 search_text，BM25 + 向量双路同时受益。
        bind_context_around_media(blocks)

        # 每个 block 可能切出多个 child chunk，编号在本 Collection 内递增。
        chunk_index = 0
        rows: list[dict] = []
        pool: list[dict] = []

        for block_position, block in enumerate(blocks):
            text = block.text
            if not text or not text.strip():
                continue
            # 跨页合并块在 metadata 里带了 _page_segments（文本偏移->页码），
            # 用它把语义切出的每个 chunk 归属到页码范围；用完即弃，不入库。
            page_segments = block.metadata.pop("_page_segments", None)
            chunks = chunker.chunk(text)
            if not chunks:
                # A chunker's minimum-size filter must not silently discard a
                # real source block. This is especially important for HTML
                # paragraphs, headings, code samples, tables, and image
                # descriptions that are naturally short.
                chunks = [text]
            clean_chunks = [c.strip() for c in chunks if c and c.strip()]
            if not clean_chunks:
                continue
            splitter = getattr(chunker, "_split_into_sentences", None)
            chunk_pages = (
                _attribute_pages(text, clean_chunks, page_segments, block.page_number, splitter)
                if page_segments and splitter
                else None
            )
            for chunk_inner, chunk in enumerate(clean_chunks):
                if chunk_pages is not None:
                    page_start, page_end = chunk_pages[chunk_inner]
                else:
                    page_start = page_end = block.page_number
                heading = " > ".join(block.heading_path) if block.heading_path else ""
                parent_chunk_id = f"{block.document_id}:parent:{block_position}"
                context_parts = [
                    str(block.metadata.get("filename") or "").strip(),
                    heading,
                ]
                context_prefix = "\n".join(part for part in context_parts if part)
                contextual_text = (
                    self._contextualize_chunk(chunk, text.strip(), context_prefix)
                    if profile.contextual_retrieval
                    else ""
                )
                # 图片/公式块的邻近正文上下文（见 bind_context_around_media）
                context_text = str(block.metadata.get("context_text") or "").strip()
                search_parts = [context_prefix, contextual_text, context_text, chunk]
                search_text = "\n".join(part for part in search_parts if part)
                metadata = dict(block.metadata or {})
                metadata.update(
                    {
                        "chunk_profile": profile.id,
                        "parent_chunk_id": parent_chunk_id,
                        "chunk_level": 1 if profile.parent_child else 0,
                        "parent_content": text.strip() if profile.parent_child else "",
                        "context_prefix": context_prefix,
                        "contextual_text": contextual_text,
                        "search_text": search_text,
                        "page_start": page_start,
                        "page_end": page_end,
                    }
                )
                rows.append(
                    {
                        "document_id": block.document_id,
                        "filename": block.metadata.get("filename", ""),
                        "source_type": block.source_type,
                        "content_type": block.content_type,
                        "page_number": page_start or block.page_number or 0,
                        "chunk_index": chunk_index,
                        "content": chunk,
                        "heading_path": heading,
                        "image_path": block.image_path or "",
                        "bbox": list(block.bbox) if block.bbox else [],
                        "confidence": float(block.confidence) if block.confidence is not None else -1.0,
                        "metadata": metadata,
                    }
                )
                pool.append(
                    {
                        "chunk_index": chunk_index,
                        "content": chunk,
                        "document_id": block.document_id,
                        "filename": block.metadata.get("filename", ""),
                        "source_type": block.source_type,
                        "content_type": block.content_type,
                        "page_number": page_start or block.page_number or 0,
                        "heading_path": heading,
                        "image_path": block.image_path or "",
                        "bbox": list(block.bbox) if block.bbox else [],
                        "confidence": float(block.confidence) if block.confidence is not None else -1.0,
                        "metadata": metadata,
                    }
                )
                chunk_index += 1

        if not rows:
            print("[hybrid] 没有可入库的 chunk")
            return 0

        # 批量 embedding + 插入 Milvus
        for start in range(0, len(rows), INDEX_BATCH_SIZE):
            batch = rows[start : start + INDEX_BATCH_SIZE]
            texts = [self._search_text(r) for r in batch]
            vectors = self.embedder.encode(texts, normalize_embeddings=True).tolist()
            self.collection.insert(
                [
                    [r["document_id"] for r in batch],
                    [r["filename"] for r in batch],
                    [r["source_type"] for r in batch],
                    [r["content_type"] for r in batch],
                    [r["page_number"] for r in batch],
                    [r["chunk_index"] for r in batch],
                    [r["content"] for r in batch],
                    [r["heading_path"] for r in batch],
                    [r["image_path"] for r in batch],
                    [r["bbox"] for r in batch],
                    [r["confidence"] for r in batch],
                    [r["metadata"] for r in batch],
                    vectors,
                ]
            )
        self.collection.flush()
        self.collection.load()

        # 重建 BM25
        self._chunk_pool = pool
        self._pool_by_index = {r["chunk_index"]: r for r in pool}
        self.bm25.build([self._search_text(r) for r in pool], pool)
        print(f"[hybrid] 入库 {len(pool)} chunks")
        return len(pool)

    # ---------- 检索 ----------

    def _vector_search(self, question: str, top_k: int = 8) -> list[dict]:
        vector = self.embedder.encode([question], normalize_embeddings=True).tolist()
        results = self.collection.search(
            data=vector,
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {}},
            limit=top_k,
            output_fields=[
                "chunk_index", "document_id", "filename", "page_number",
                "content", "heading_path", "source_type", "content_type",
                "image_path", "bbox", "confidence", "metadata",
            ],
        )
        return [
            {
                "index": int(hit.entity.get("chunk_index")),
                "score": float(hit.score),
                "source": "vector",
                "chunk": {
                    "text": hit.entity.get("content"),
                    "document_id": hit.entity.get("document_id"),
                    "filename": hit.entity.get("filename"),
                    "page_number": hit.entity.get("page_number"),
                    "heading_path": hit.entity.get("heading_path"),
                    "source_type": hit.entity.get("source_type"),
                    "content_type": hit.entity.get("content_type"),
                    "image_path": hit.entity.get("image_path"),
                    "bbox": hit.entity.get("bbox"),
                    "confidence": hit.entity.get("confidence"),
                    "metadata": hit.entity.get("metadata"),
                },
            }
            for hit in results[0]
        ]

    def _bm25_search(self, question: str, top_k: int = 8) -> list[dict]:
        results = self.bm25.search(question, top_k=top_k)
        return [
            {
                "index": r["index"],
                "score": r["score"],
                "source": "bm25",
                "chunk": {
                    "text": r["text"],
                    "document_id": r["metadata"].get("document_id", ""),
                    "filename": r["metadata"].get("filename", ""),
                    "page_number": r["metadata"].get("page_number", 0),
                    "heading_path": r["metadata"].get("heading_path", ""),
                    "source_type": r["metadata"].get("source_type", ""),
                    "content_type": r["metadata"].get("content_type", "text"),
                    "image_path": r["metadata"].get("image_path", ""),
                    "bbox": r["metadata"].get("bbox", []),
                    "confidence": r["metadata"].get("confidence", -1.0),
                    "metadata": r["metadata"].get("metadata", {}),
                },
            }
            for r in results
        ]

    def search(self, question: str, top_k: int = 8, bm25_k: int = 8, vector_k: int = 8, rrf_k: int = 60) -> list[dict]:
        """混合检索：BM25 + 向量 -> RRF 融合。

        返回按融合分数排序的结果列表（每个结果带 index 和 origins）。
        """
        if self.collection.num_entities == 0:
            return []
        bm25_results = self._bm25_search(question, top_k=bm25_k)
        vector_results = self._vector_search(question, top_k=vector_k)
        return reciprocal_rank_fusion([bm25_results, vector_results], k=rrf_k)

    def _chunk_pool_by_index(self, index: int) -> dict | None:
        return self._pool_by_index.get(index)

    # ---------- 问答 ----------

    def answer(self, question: str, top_k: int = 5, model_id: str | None = None) -> dict:
        """混合检索后交给 LLM 生成带来源的答案。"""
        fused = self.search(question, top_k=top_k)
        return self.answer_from_fused(question, fused, top_k=top_k, model_id=model_id)

    def answer_from_fused(
        self,
        question: str,
        fused: list[dict],
        top_k: int = 5,
        model_id: str | None = None,
        retrieval: dict | None = None,
    ) -> dict:
        """Generate an answer from local or federated fused results."""
        if not fused:
            return {"answer": "知识库为空或没有找到相关内容。", "sources": [], "retrieval": {}}

        sources: list[dict] = []
        context_parts: list[str] = []
        parent_contexts: set[str] = set()
        for idx, item in enumerate(fused[:top_k], start=1):
            chunk = item.get("chunk") or self._chunk_pool_by_index(item["index"])
            if not chunk:
                continue
            text = chunk["content"]
            metadata = chunk.get("metadata", {}) or {}
            parent_id = str(metadata.get("parent_chunk_id") or "")
            parent_text = str(metadata.get("parent_content") or "").strip()
            context = f"[{idx}]\n{text}"
            if parent_id and parent_text and parent_id not in parent_contexts and parent_text != text:
                context += f"\n[父级上下文]\n{parent_text[:4000]}"
                parent_contexts.add(parent_id)
            context_parts.append(context)
            sources.append(
                {
                    "text": text,
                    "document_id": chunk["document_id"],
                    "filename": chunk["filename"],
                    "page": chunk["page_number"] or None,
                    "heading_path": chunk["heading_path"],
                    "source_type": chunk["source_type"],
                    "content_type": chunk["content_type"],
                    "image_path": chunk["image_path"] or None,
                    "bbox": chunk["bbox"],
                    "confidence": chunk["confidence"],
                    "metadata": metadata,
                    "parent_chunk_id": metadata.get("parent_chunk_id"),
                    "chunk_level": metadata.get("chunk_level", 0),
                    "score": item["score"],
                    "origins": item["origins"],
                }
            )

        context = "\n\n---\n\n".join(context_parts)
        prompt = (
            "请使用参考资料回答问题。如果资料中没有答案，请明确说明'资料中没有找到'。\n"
            "回答的关键结论请标注来源编号 [n]。不要编造页码或来源。\n\n"
            f"参考资料：\n{context}\n\n问题：{question}"
        )
        selected_model = model_id or LLM_MODEL
        llm = self._get_llm_client(selected_model)
        response = llm.chat.completions.create(
            model=selected_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.2,
        )
        return {
            "answer": response.choices[0].message.content,
            "model": selected_model,
            "sources": sources,
            "retrieval": retrieval or {"top_k": len(fused[:5]), "rerank": "rrf"},
        }


# ---------------------------------------------------------------- 跨页页码归属

def _page_at(segments: list[tuple[int, int, int | None]], offset: int) -> int | None:
    """返回文本偏移量 offset 所属页码（segments 按起始偏移升序、互不重叠）。"""
    page: int | None = segments[0][2] if segments else None
    for start, end, item_page in segments:
        if offset < end:
            page = item_page
            break
        page = item_page
    return page


def _attribute_pages(
    text: str,
    chunks: list[str],
    segments: list[tuple[int, int, int | None]],
    fallback_page: int | None,
    split_sentences,
) -> list[tuple[int | None, int | None]]:
    """把语义分块器对跨页合并文本切出的 chunk 归属到页码范围。

    语义分块器（SemanticChunker）按句子切分并原样 ``"".join`` 拼接，
    chunk 与句子序列严格对齐。流程：
    1. 用同一句子切分器给每个句子定位起始页码（句子按序在文本中 find）；
    2. 按句子长度累计，把每个 chunk 映射到它覆盖的句子区间；
    3. 取区间内页码的 min/max 作为该 chunk 的页码范围（跨页 chunk 正确报范围）。
    """
    sentences = split_sentences(text)
    sent_lens = [len(sentence) for sentence in sentences]

    cursor = 0
    sent_pages: list[int | None] = []
    for sentence in sentences:
        index = text.find(sentence, cursor)
        if index < 0:
            index = text.find(sentence)
        if index < 0:
            index = cursor
        sent_pages.append(_page_at(segments, index))
        cursor = index + len(sentence)

    results: list[tuple[int | None, int | None]] = []
    sentence_index = 0
    for chunk in chunks:
        target_len = len(chunk)
        covered = 0
        start_index = sentence_index
        while sentence_index < len(sentences) and covered < target_len:
            covered += sent_lens[sentence_index]
            sentence_index += 1
        pages = [page for page in sent_pages[start_index:sentence_index] if page is not None]
        if pages:
            results.append((min(pages), max(pages)))
        else:
            results.append((fallback_page, fallback_page))
    return results


def bind_context_around_media(blocks: list) -> list:
    """给图片/公式块绑定邻近正文上下文（原地修改 metadata，返回原列表）。

    检索"讲梯度下降那张图/那个公式"这类问题时，仅靠 image_description 的
    OCR/视觉描述文本召回差——用户用正文里的词提问，图片自身文本里没有。
    在组块之后（屏障块夹在合并文本块之间，相邻性有语义），把前一个文本块
    尾部 + 后一个文本块头部各 CONTEXT_WINDOW 字拼进 metadata["context_text"]，
    build() 再把它并入 search_text，BM25 与向量双路同时受益。
    孤立图片块（无邻近文本）不绑定；普通文本块不受影响。
    """
    for i, block in enumerate(blocks):
        if str(block.content_type) not in CONTEXT_BINDING_TYPES:
            continue
        before = ""
        for j in range(i - 1, -1, -1):
            if str(blocks[j].content_type) in TEXT_CONTENT_TYPES:
                before = (blocks[j].text or "")[-CONTEXT_WINDOW:]
                break
        after = ""
        for j in range(i + 1, len(blocks)):
            if str(blocks[j].content_type) in TEXT_CONTENT_TYPES:
                after = (blocks[j].text or "")[:CONTEXT_WINDOW]
                break
        parts = [p.strip() for p in (before, after) if p and p.strip()]
        if parts:
            block.metadata.setdefault("context_text", "\n".join(parts))
    return blocks
