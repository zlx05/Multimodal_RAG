"""滑动窗口分片器 - 基于句子边界的重叠分片"""

from typing import List, Optional

from .base import BaseChunker


class SlidingWindowChunker(BaseChunker):
    """滑动窗口分片器

    按句子边界切分，支持任意步长的重叠窗口：
    1. 将文本分割成句子
    2. 按固定句子数滑动窗口
    3. 窗口之间有重叠，确保信息不丢失

    适合需要高召回率的场景。
    """

    name = "sliding"
    description = "滑动窗口分片（基于句子边界）"

    def __init__(
        self,
        chunk_size: int = 5,
        step: int = 2,
        min_chunk_size: int = 50,
        separator: str = "\n",
    ):
        """
        Args:
            chunk_size: 每个窗口的句子数量
            step: 每次滑动移动的句子数量（小于 chunk_size 则有重叠）
            min_chunk_size: 最小块大小（字符数）
            separator: 句子连接符
        """
        self.chunk_size = chunk_size
        self.step = step
        self.min_chunk_size = min_chunk_size
        self.separator = separator

    def chunk(self, text: str) -> List[str]:
        """按滑动窗口切分文本"""
        if not text or not text.strip():
            return []

        # 分割成句子
        sentences = self._split_into_sentences(text)
        if not sentences:
            return []

        # 如果句子数小于 chunk_size，直接返回整个文本
        if len(sentences) <= self.chunk_size:
            result = [text]
            return [r for r in result if len(r) >= self.min_chunk_size]

        # 滑动窗口
        chunks = []
        for i in range(0, len(sentences), self.step):
            window = sentences[i:i + self.chunk_size]
            if not window:
                break
            chunk = self.separator.join(window)
            chunks.append(chunk)
            # 确保不会无限循环
            if i + self.chunk_size >= len(sentences):
                break

        # 过滤太小的块
        return [c for c in chunks if len(c) >= self.min_chunk_size]

    def _split_into_sentences(self, text: str) -> List[str]:
        """将文本分割成句子"""
        import re

        # 中英文句子结束符
        sentence_endings = r'[。！？\.!?；\n]+'
        parts = re.split(sentence_endings, text)

        sentences = []
        for part in parts:
            part = part.strip()
            if part:
                # 检查原始文本中是否有结束符
                for ending in ['。', '！', '？', ';', '.', '!', '?']:
                    if ending in text and ending not in part[-2:]:
                        sentences.append(part + ending)
                        break
                else:
                    sentences.append(part)

        return sentences

    def get_config(self) -> dict:
        return {
            "chunk_size": self.chunk_size,
            "step": self.step,
            "min_chunk_size": self.min_chunk_size,
            "separator": self.separator,
        }

    @staticmethod
    def get_common_params() -> List[dict]:
        return [
            {
                "name": "chunk_size",
                "type": "int",
                "default": 5,
                "min": 1,
                "max": 20,
                "step": 1,
                "label": "窗口句子数",
                "help": "每个窗口包含的句子数量",
            },
            {
                "name": "step",
                "type": "int",
                "default": 2,
                "min": 1,
                "max": 20,
                "step": 1,
                "label": "滑动步长",
                "help": "每次滑动移动的句子数量（小于窗口大小则有重叠）",
            },
        ]