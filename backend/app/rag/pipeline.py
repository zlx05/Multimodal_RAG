"""RAG pipeline backed by a Milvus server and an OpenAI-compatible LLM."""

import os
from typing import List, Optional, Union

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import numpy as np
from openai import OpenAI
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility
from sentence_transformers import SentenceTransformer

from ..core.config import LLM_BASE_URL, LLM_MODEL
from .chunkers import (
    BaseChunker,
    FixedLengthChunker,
    get_chunker,
)
from .model_config import EMBEDDING_MODEL


MILVUS_HOST = os.getenv("MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")
INDEX_BATCH_SIZE = 500


def load_document(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read()


class Embedder:
    def __init__(self, model_name: str = EMBEDDING_MODEL):
        print(f"Loading embedding model: {model_name}")
        self.model = SentenceTransformer(model_name)
        self.dimension = self.model.get_sentence_embedding_dimension()
        print(f"Embedding model ready, dimension={self.dimension}")

    def encode(self, texts: List[str]) -> np.ndarray:
        return self.model.encode(texts, normalize_embeddings=True, show_progress_bar=True)


class MilvusRAGPipeline:
    def __init__(
        self,
        document_path: str,
        api_key: str,
        collection_name: str = "rag_docs",
        milvus_host: str = MILVUS_HOST,
        milvus_port: str = MILVUS_PORT,
        rebuild: bool = False,
        chunker: Optional[Union[str, BaseChunker]] = None,
        chunk_size: int = 500,
        overlap: int = 80,
    ):
        """初始化 RAG 管道

        Args:
            document_path: 文档路径
            api_key: LLM API 密钥
            collection_name: Milvus 集合名称
            milvus_host: Milvus 主机地址
            milvus_port: Milvus 端口
            rebuild: 是否重建集合
            chunker: 分片策略，可以是:
                - str: 分片器名称（如 "fixed", "recursive"）
                - BaseChunker: 分片器实例
                - None: 使用默认的 FixedLengthChunker
            chunk_size: 当 chunker 为字符串时的默认块大小
            overlap: 当 chunker 为字符串时的默认重叠大小
        """
        print(f"Connecting to Milvus at {milvus_host}:{milvus_port}")
        connections.connect(alias="default", host=milvus_host, port=milvus_port)

        self.document_path = document_path
        self.collection_name = collection_name

        # 处理分片器
        if chunker is None:
            self.chunker = FixedLengthChunker(chunk_size=chunk_size, overlap=overlap)
        elif isinstance(chunker, str):
            # 使用 get_chunker 工厂函数创建
            self.chunker = get_chunker(chunker, chunk_size=chunk_size, overlap=overlap)
        else:
            self.chunker = chunker  # 已经是 BaseChunker 实例

        self.embedder = Embedder()
        # 仅在需要新建/重建索引时读取和切分文档；调用已有 Collection 不重复切片。
        self.document = ""
        self.chunks = []

        if rebuild and utility.has_collection(self.collection_name):
            print(f"Rebuilding collection: {self.collection_name}")
            utility.drop_collection(self.collection_name)

        self.collection = self._get_or_create_collection()

        if self.collection.num_entities == 0:
            cleanup_collection = True
            try:
                self.document = load_document(document_path)
                if not self.document.strip():
                    raise ValueError(
                        f"文档内容为空，无法创建向量索引: {document_path}。"
                        "请先把有效文本放入该文件。"
                    )

                self.chunks = [
                    chunk.strip()
                    for chunk in self.chunker.chunk(self.document)
                    if chunk and chunk.strip()
                ]
                if not self.chunks:
                    raise ValueError(
                        f"当前分片策略没有生成有效 chunk: {self.chunker.name}。"
                        "请检查文档内容或调小最小块大小。"
                    )

                self._index_documents()
                if self.collection.num_entities == 0:
                    raise RuntimeError("入库后 Collection 仍然没有实体，索引创建失败。")
            except Exception:
                # 不留下“有 Collection、没向量”的假索引，避免下次被误认为已入库。
                if cleanup_collection and utility.has_collection(self.collection_name):
                    utility.drop_collection(self.collection_name)
                raise
        else:
            print(f"Using existing collection with {self.collection.num_entities} entities")

        self.llm = OpenAI(api_key=api_key, base_url=LLM_BASE_URL)

    def _get_or_create_collection(self) -> Collection:
        if utility.has_collection(self.collection_name):
            collection = Collection(self.collection_name)
            embedding_field = next(
                field for field in collection.schema.fields if field.name == "embedding"
            )
            existing_dimension = int(embedding_field.params["dim"])
            if existing_dimension != self.embedder.dimension:
                raise ValueError(
                    f"Collection {self.collection_name} uses dimension {existing_dimension}, "
                    f"but the current embedding model uses {self.embedder.dimension}. "
                    "Choose Rebuild collection or use a new collection name."
                )
            collection.load()
            return collection

        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(
                name="embedding",
                dtype=DataType.FLOAT_VECTOR,
                dim=self.embedder.dimension,
            ),
        ]
        schema = CollectionSchema(fields=fields, description="RAG document chunks")
        collection = Collection(name=self.collection_name, schema=schema)
        collection.create_index(
            field_name="embedding",
            index_params={
                "index_type": "AUTOINDEX",
                "metric_type": "COSINE",
                "params": {},
            },
        )
        collection.load()
        return collection

    def _index_documents(self) -> None:
        print(f"Indexing {len(self.chunks)} document chunks")
        total_chunks = len(self.chunks)
        if total_chunks == 0:
            raise ValueError("没有可入库的文本 chunk。")
        for start in range(0, total_chunks, INDEX_BATCH_SIZE):
            end = min(start + INDEX_BATCH_SIZE, total_chunks)
            batch_chunks = self.chunks[start:end]
            batch_embeddings = self.embedder.encode(batch_chunks).tolist()
            self.collection.insert([batch_chunks, batch_embeddings])
            self.collection.flush()
            print(f"Indexed batch {end}/{total_chunks} chunks")
        self.collection.load()
        print(f"Indexed {self.collection.num_entities} entities")

    def search(self, query: str, top_k: int = 5):
        query_vector = self.embedder.encode([query]).tolist()
        results = self.collection.search(
            data=query_vector,
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {}},
            limit=top_k,
            output_fields=["text"],
        )
        return [(hit.entity.get("text"), float(hit.score)) for hit in results[0]]

    def query(self, question: str, top_k: int = 3) -> dict:
        results = self.search(question, top_k=top_k)
        context = "\n\n---\n\n".join(
            f"[Document {index + 1}]\n{text}" for index, (text, _) in enumerate(results)
        )
        prompt = (
            "Answer the question using only the reference documents. "
            "If the answer is not present, say that the documents do not contain it.\n\n"
            f"Reference documents:\n{context}\n\nQuestion: {question}"
        )
        response = self.llm.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.2,
        )
        return {
            "answer": response.choices[0].message.content,
            "sources": results,
        }

    def get_chunker_info(self) -> dict:
        """获取当前分片器信息"""
        return {
            "name": self.chunker.name,
            "description": self.chunker.description,
            "config": self.chunker.get_config(),
            "chunk_count": len(self.chunks),
        }

    def clear(self) -> None:
        if utility.has_collection(self.collection_name):
            utility.drop_collection(self.collection_name)
