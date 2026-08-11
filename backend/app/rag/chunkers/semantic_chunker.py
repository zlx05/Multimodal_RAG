"""语义分片器 - 基于 Embedding 相似度"""

import os
from typing import Callable, List, Optional

import numpy as np
from sentence_transformers import SentenceTransformer

from .base import BaseChunker
from ..model_config import EMBEDDING_MODEL

# 设置 HuggingFace 镜像
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


class SemanticChunker(BaseChunker):
    """语义分片器

    基于 Embedding 相似度来判断语义边界：
    1. 将文本分割成句子
    2. 计算相邻句子的向量相似度
    3. 在相似度低于阈值的地方切分

    适合需要保持语义完整性的场景。
    """

    name = "semantic"
    description = "语义分片（基于 Embedding 相似度）"

    def __init__(
        self,
        model_name: Optional[str] = None,
        similarity_threshold: float = 0.5,
        min_chunk_size: int = 100,
        max_chunk_size: int = 1500,
        embedder: Optional[SentenceTransformer] = None,
        similarity_fn: Optional[Callable[[np.ndarray, np.ndarray], float]] = None,
    ):
        """
        Args:
            model_name: Embedding 模型名称或路径，默认使用本地 BGE 模型
            similarity_threshold: 相似度阈值，低于此值时切分
            min_chunk_size: 最小块大小（字符数）
            max_chunk_size: 最大块大小（字符数），超过则强制切分
            embedder: 外部传入的 Embedder 实例（避免重复加载）
            similarity_fn: 自定义相似度函数，默认用余弦相似度
        """
        self.model_name = model_name or EMBEDDING_MODEL
        self.similarity_threshold = similarity_threshold
        self.min_chunk_size = min_chunk_size
        self.max_chunk_size = max_chunk_size
        self._embedder = embedder
        self._similarity_fn = similarity_fn or self._cosine_similarity

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """计算余弦相似度"""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    @property
    def embedder(self) -> SentenceTransformer:
        """延迟加载 Embedder"""
        if self._embedder is None:
            print(f"Loading embedding model for semantic chunking: {self.model_name}")
            self._embedder = SentenceTransformer(self.model_name)
        return self._embedder

    def chunk(self, text: str) -> List[str]:
        """基于语义相似度切分文本"""
        if not text or not text.strip():
            return []

        # 分割成句子
        sentences = self._split_into_sentences(text)
        if not sentences:
            return []

        # 只用一个句子的情况
        if len(sentences) == 1:
            return [text]

        # 计算句子嵌入
        embeddings = self.embedder.encode(sentences, normalize_embeddings=True)

        # 根据相似度分块
        chunks = []
        current_chunk = [sentences[0]]
        current_len = len(sentences[0])

        for i in range(1, len(sentences)):
            sentence = sentences[i]
            sentence_len = len(sentence)

            # 检查是否需要强制切分（超过最大大小）
            if current_len + sentence_len > self.max_chunk_size:
                chunks.append("".join(current_chunk))
                current_chunk = [sentence]
                current_len = sentence_len
                continue

            # 检查语义相似度
            similarity = self._similarity_fn(embeddings[i - 1], embeddings[i])

            if similarity < self.similarity_threshold:
                # 相似度低，触发切分
                if current_len >= self.min_chunk_size:
                    chunks.append("".join(current_chunk))
                    current_chunk = [sentence]
                    current_len = sentence_len
                else:
                    # 当前块太小，继续累积
                    current_chunk.append(sentence)
                    current_len += sentence_len
            else:
                # 相似度高，合并
                current_chunk.append(sentence)
                current_len += sentence_len

        # 处理最后一个块
        if current_chunk:
            chunks.append("".join(current_chunk))

        return [c for c in chunks if len(c) >= self.min_chunk_size]

    def _split_into_sentences(self, text: str) -> List[str]:
        """将文本分割成句子"""
        import re

        # 匹配常见的中英文句子结束符
        sentence_endings = r'[。！？\.!?\n]+'
        parts = re.split(sentence_endings, text)

        sentences = []
        for part in parts:
            part = part.strip()
            if part:
                # 添加回结束符（如果原始文本有的话）
                for ending in ['。', '！', '？', '.', '!', '?']:
                    if text.count(ending) > 0 and part + ending in text:
                        sentences.append(part + ending)
                        break
                else:
                    sentences.append(part)

        return sentences

    def get_config(self) -> dict:
        return {
            "similarity_threshold": self.similarity_threshold,
            "min_chunk_size": self.min_chunk_size,
            "max_chunk_size": self.max_chunk_size,
        }

    @staticmethod
    def get_common_params() -> List[dict]:
        return [
            {
                "name": "similarity_threshold",
                "type": "float",
                "default": 0.5,
                "min": 0.0,
                "max": 1.0,
                "step": 0.05,
                "label": "相似度阈值",
                "help": "低于此值时触发切分（0-1，越低越严格）",
            },
            {
                "name": "min_chunk_size",
                "type": "int",
                "default": 100,
                "min": 20,
                "max": 1000,
                "step": 10,
                "label": "最小块大小",
                "help": "每个块的最少字符数",
            },
            {
                "name": "max_chunk_size",
                "type": "int",
                "default": 1500,
                "min": 200,
                "max": 5000,
                "step": 100,
                "label": "最大块大小",
                "help": "每个块的最多字符数",
            },
        ]
