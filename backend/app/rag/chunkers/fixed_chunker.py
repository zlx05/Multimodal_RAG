"""固定长度分片器 - 基于段落边界的字符分片"""

from typing import List

from .base import BaseChunker


class FixedLengthChunker(BaseChunker):
    """固定长度分片器

    按字符数切分，同时尽量保持段落边界完整。
    支持重叠窗口以避免信息被切断。

    切分逻辑：
    1. 按段落分割文本
    2. 尝试合并多个短段落
    3. 超过 chunk_size 的段落内部继续切分
    4. 相邻块之间保留 overlap 重叠
    """

    name = "fixed"
    description = "固定长度 + 段落边界（基于字符）"

    def __init__(
        self,
        chunk_size: int = 500,
        overlap: int = 80,
        min_chunk_size: int = 50,
    ):
        """
        Args:
            chunk_size: 每个文本块的最大字符数
            overlap: 相邻块之间的重叠字符数
            min_chunk_size: 最小块大小，过小的块会被丢弃
        """
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chunk_size = min_chunk_size

    def chunk(self, text: str) -> List[str]:
        """将文本切分成固定长度的块"""
        if not text or not text.strip():
            return []

        paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
        if not paragraphs:
            return []

        chunks: List[str] = []
        current = ""

        for paragraph in paragraphs:
            # 处理超长段落：在段落内部继续切分
            if len(paragraph) > self.chunk_size:
                if current:
                    chunks.append(current.strip())
                    current = ""
                start = 0
                while start < len(paragraph):
                    end = start + self.chunk_size
                    chunks.append(paragraph[start:end].strip())
                    if end >= len(paragraph):
                        break
                    start = max(end - self.overlap, start + 1)
                continue

            # 尝试将当前段落合并到已有块
            candidate = f"{current}\n{paragraph}".strip() if current else paragraph
            if current and len(candidate) > self.chunk_size:
                chunks.append(current.strip())
                current = f"{current[-self.overlap:]}\n{paragraph}".strip()
            else:
                current = candidate

        # 处理最后剩余的块
        if current:
            chunks.append(current.strip())

        # 过滤太小的块
        return [chunk for chunk in chunks if len(chunk) >= self.min_chunk_size]

    def get_config(self) -> dict:
        return {
            "chunk_size": self.chunk_size,
            "overlap": self.overlap,
            "min_chunk_size": self.min_chunk_size,
        }

    @staticmethod
    def get_common_params() -> List[dict]:
        return [
            {
                "name": "chunk_size",
                "type": "int",
                "default": 500,
                "min": 50,
                "max": 5000,
                "step": 50,
                "label": "块大小",
                "help": "每个文本块的最大字符数",
            },
            {
                "name": "overlap",
                "type": "int",
                "default": 80,
                "min": 0,
                "max": 500,
                "step": 10,
                "label": "重叠字数",
                "help": "相邻块之间的重叠字符数",
            },
        ]