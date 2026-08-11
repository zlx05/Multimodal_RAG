"""Token 分片器 - 基于 token 计数切分"""

from typing import List, Optional

from .base import BaseChunker


class TokenChunker(BaseChunker):
    """Token 分片器

    按 LLM 的 token 计数进行切分，比字符分片更精准。
    使用 tiktoken 编码器进行 token 计数。

    1 token ≈ 0.75 个英文单词 ≈ 1-2 个中文字符
    """

    name = "token"
    description = "Token 分片（基于 LLM token 计数）"

    def __init__(
        self,
        max_tokens: int = 500,
        overlap_tokens: int = 50,
        encoding_name: str = "cl100k_base",
        min_chunk_size: int = 50,
        min_tokens: int = 20,
    ):
        """
        Args:
            max_tokens: 每个块的最大 token 数
            overlap_tokens: 相邻块之间的重叠 token 数
            encoding_name: tiktoken 编码器名称
            min_chunk_size: 最小块大小（字符数）
            min_tokens: 每个块的最少 token 数
        """
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.encoding_name = encoding_name
        self.min_chunk_size = min_chunk_size
        self.min_tokens = min_tokens
        self._encoder = None

    @property
    def encoder(self):
        """延迟加载 tiktoken 编码器"""
        if self._encoder is None:
            try:
                import tiktoken
                self._encoder = tiktoken.get_encoding(self.encoding_name)
            except ImportError:
                raise ImportError(
                    "tiktoken 未安装。请运行: pip install tiktoken"
                )
        return self._encoder

    def chunk(self, text: str) -> List[str]:
        """按 token 计数切分文本"""
        if not text or not text.strip():
            return []

        # 尝试使用 tiktoken
        try:
            return self._chunk_with_tiktoken(text)
        except ImportError:
            # 回退到基于字符的估计
            return self._chunk_with_estimation(text)

    def _chunk_with_tiktoken(self, text: str) -> List[str]:
        """使用 tiktoken 精确分块"""
        encoder = self.encoder

        # 编码整个文本
        tokens = encoder.encode(text)
        total_tokens = len(tokens)

        if total_tokens <= self.max_tokens:
            return [text]

        chunks = []
        start = 0

        while start < total_tokens:
            end = min(start + self.max_tokens, total_tokens)
            chunk_tokens = tokens[start:end]
            chunk_text = encoder.decode(chunk_tokens)

            # 解码的文本可能比原始略有偏差，这里简单处理
            if chunk_text:
                chunks.append(chunk_text)

            # 移动到下一个位置（考虑重叠）
            if end >= total_tokens:
                break
            start = max(start + self.max_tokens - self.overlap_tokens, start + 1)

        # 过滤太小的块
        return [c for c in chunks if len(c) >= self.min_chunk_size]

    def _chunk_with_estimation(self, text: str) -> List[str]:
        """使用字符数估计 token（无 tiktoken 时的回退）"""
        # 粗略估计：1 token ≈ 4 字符（英文）或 2 字符（中文）
        # 这里简化处理，假设平均 3 字符 = 1 token
        estimated_chars_per_token = 3
        max_chars = self.max_tokens * estimated_chars_per_token
        overlap_chars = self.overlap_tokens * estimated_chars_per_token

        paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
        chunks = []
        current = ""

        for paragraph in paragraphs:
            if len(paragraph) > max_chars:
                # 超长段落，按估计硬切
                if current:
                    chunks.append(current)
                    current = ""
                for i in range(0, len(paragraph), max_chars - overlap_chars):
                    chunk = paragraph[i:i + max_chars]
                    if len(chunk) >= self.min_chunk_size:
                        chunks.append(chunk)
            else:
                candidate = f"{current}\n{paragraph}".strip() if current else paragraph
                if current and len(candidate) > max_chars:
                    chunks.append(current)
                    current = f"{current[-overlap_chars:]}\n{paragraph}".strip()
                else:
                    current = candidate

        if current:
            chunks.append(current)

        return [c for c in chunks if len(c) >= self.min_chunk_size]

    def get_config(self) -> dict:
        return {
            "max_tokens": self.max_tokens,
            "overlap_tokens": self.overlap_tokens,
            "encoding_name": self.encoding_name,
            "min_chunk_size": self.min_chunk_size,
        }

    @staticmethod
    def get_common_params() -> List[dict]:
        return [
            {
                "name": "max_tokens",
                "type": "int",
                "default": 500,
                "min": 50,
                "max": 4000,
                "step": 50,
                "label": "最大 Token 数",
                "help": "每个块的最大 Token 数量",
            },
            {
                "name": "overlap_tokens",
                "type": "int",
                "default": 50,
                "min": 0,
                "max": 500,
                "step": 10,
                "label": "重叠 Token 数",
                "help": "相邻块之间的重叠 Token 数",
            },
        ]