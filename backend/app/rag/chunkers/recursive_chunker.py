"""递归字符分片器 - LangChain 风格"""

from typing import List, Optional

from .base import BaseChunker


class RecursiveChunker(BaseChunker):
    """递归字符分片器

    按优先级尝试不同的分隔符进行切分：
    段落分隔符 -> 换行符 -> 句子分隔符 -> 字符

    类似于 LangChain 的 RecursiveCharacterTextSplitter。
    """

    name = "recursive"
    description = "递归字符分片（LangChain 风格）"

    def __init__(
        self,
        chunk_size: int = 500,
        overlap: int = 80,
        separators: Optional[List[str]] = None,
        min_chunk_size: int = 50,
    ):
        """
        Args:
            chunk_size: 每个文本块的最大字符数
            overlap: 相邻块之间的重叠字符数
            separators: 分隔符列表，按优先级排序，默认使用中文优先
            min_chunk_size: 最小块大小
        """
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chunk_size = min_chunk_size
        # 默认分隔符：中文优先
        self.separators = separators or [
            "\n\n",      # 段落分隔
            "\n",        # 换行
            "。",        # 句号
            "！",        # 感叹号
            "？",        # 问号
            "；",        # 分号
            "，",        # 逗号
            " ",         # 空格
            "",          # 最后按字符切
        ]

    def chunk(self, text: str) -> List[str]:
        """将文本递归切分成块"""
        if not text or not text.strip():
            return []

        # 首先尝试用最高优先级的分隔符分割
        chunks = self._split_by_separators(text, self.separators)

        # 过滤太小的块
        return [chunk for chunk in chunks if len(chunk) >= self.min_chunk_size]

    def _split_by_separators(self, text: str, separators: List[str]) -> List[str]:
        """递归使用分隔符切分"""
        if not separators:
            # 最后一个分隔符：按字符切
            return self._split_by_char(text)

        separator = separators[0]
        remaining_separators = separators[1:]

        if not separator:
            # 空分隔符，直接按字符切
            return self._split_by_char(text)

        parts = text.split(separator)
        result: List[str] = []
        current = ""

        for part in parts:
            candidate = f"{current}{separator}{part}" if current else part

            if len(candidate) <= self.chunk_size:
                current = candidate
            else:
                # 当前块已满
                if current:
                    result.append(current)
                # 尝试用更低优先级的分隔符处理这个部分
                if len(part) > self.chunk_size:
                    # part 仍然太长，递归处理
                    result.extend(self._split_by_separators(part, remaining_separators))
                    current = ""
                else:
                    current = part

        if current:
            result.append(current)

        # 合并太小的块
        return self._merge_small_chunks(result)

    def _split_by_char(self, text: str) -> List[str]:
        """按字符数硬切"""
        chunks = []
        for i in range(0, len(text), self.chunk_size):
            chunks.append(text[i:i + self.chunk_size])
        return chunks

    def _merge_small_chunks(self, chunks: List[str]) -> List[str]:
        """合并太小的块到前一个块"""
        if not chunks:
            return []

        result = [chunks[0]]
        for chunk in chunks[1:]:
            if len(chunk) < self.min_chunk_size and result:
                result[-1] = f"{result[-1]}{chunk}"
            else:
                result.append(chunk)
        return result

    def get_config(self) -> dict:
        return {
            "chunk_size": self.chunk_size,
            "overlap": self.overlap,
            "separators": self.separators,
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