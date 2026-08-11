"""Markdown 分片器 - 保留文档结构"""

import re
from typing import List, Optional, Tuple

from .base import BaseChunker


class MarkdownChunker(BaseChunker):
    """Markdown 分片器

    按 Markdown 标题层级结构进行切分：
    1. 识别 # 标题层级
    2. 按同级或跨级标题切分
    3. 保留标题作为上下文

    适合有明确结构的 Markdown 文档。
    """

    name = "markdown"
    description = "Markdown 分片（保留标题结构）"

    def __init__(
        self,
        min_chunk_size: int = 100,
        max_chunk_size: int = 2000,
        heading_levels: Optional[List[int]] = None,
        preserve_lead: bool = True,
        add_heading_context: bool = True,
    ):
        """
        Args:
            min_chunk_size: 最小块大小（字符数）
            max_chunk_size: 最大块大小，超过则按段落继续切分
            heading_levels: 要识别的标题级别，如 [1, 2, 3] 表示识别 h1-h3
            preserve_lead: 是否保留文档开头的非标题内容
            add_heading_context: 是否在每个块前添加当前标题作为上下文
        """
        self.min_chunk_size = min_chunk_size
        self.max_chunk_size = max_chunk_size
        self.heading_levels = heading_levels or [1, 2, 3, 4, 5, 6]
        self.preserve_lead = preserve_lead
        self.add_heading_context = add_heading_context

    def chunk(self, text: str) -> List[str]:
        """按 Markdown 标题结构切分文本"""
        if not text or not text.strip():
            return []

        # 解析 Markdown 结构
        sections = self._parse_markdown(text)
        if not sections:
            # 没有标题，退回到段落分片
            return self._fallback_chunk(text)

        chunks = []
        current_heading = ""
        current_content: List[str] = []

        for heading, content in sections:
            # 更新当前标题
            if heading:
                current_heading = heading

            content = content.strip()
            if not content:
                continue

            # 检查是否需要切分
            content_with_heading = f"{current_heading}\n{content}" if current_heading and self.add_heading_context else content

            if len(content_with_heading) > self.max_chunk_size:
                # 内容太长，需要在段落级别继续切分
                sub_chunks = self._split_long_content(content, current_heading)
                chunks.extend(sub_chunks)
            else:
                # 内容合适，直接添加
                if content_with_heading and len(content_with_heading) >= self.min_chunk_size:
                    chunks.append(content_with_heading)

        # 过滤太小的块
        return [c for c in chunks if len(c) >= self.min_chunk_size]

    def _parse_markdown(self, text: str) -> List[Tuple[str, str]]:
        """解析 Markdown，返回 (标题, 内容) 列表"""
        lines = text.split('\n')
        sections: List[Tuple[str, str]] = []
        current_heading = ""
        current_content: List[str] = []

        heading_pattern = re.compile(r'^(#{1,6})\s+(.+)$')

        for line in lines:
            match = heading_pattern.match(line)
            if match:
                # 保存上一个 section
                if current_content:
                    content = '\n'.join(current_content).strip()
                    if content:
                        sections.append((current_heading, content))
                    current_content = []

                level = len(match.group(1))
                if level in self.heading_levels:
                    current_heading = match.group(2).strip()
                else:
                    current_heading = ""  # 忽略不关注的标题级别
            else:
                current_content.append(line)

        # 最后一个 section
        if current_content:
            content = '\n'.join(current_content).strip()
            if content:
                sections.append((current_heading, content))

        return sections

    def _split_long_content(self, content: str, heading: str) -> List[str]:
        """将超长内容按段落继续切分"""
        paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
        chunks = []
        current = ""

        prefix = f"{heading}\n" if heading and self.add_heading_context else ""

        for para in paragraphs:
            candidate = f"{current}\n\n{para}".strip() if current else para

            if len(prefix + candidate) > self.max_chunk_size:
                if current:
                    final = prefix + current if prefix else current
                    if len(final) >= self.min_chunk_size:
                        chunks.append(final)
                current = para
            else:
                current = candidate

        if current:
            final = prefix + current if prefix else current
            if len(final) >= self.min_chunk_size:
                chunks.append(final)

        return chunks

    def _fallback_chunk(self, text: str) -> List[str]:
        """没有标题时的回退策略：按段落分片"""
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        chunks = []
        current = ""

        for para in paragraphs:
            candidate = f"{current}\n\n{para}".strip() if current else para
            if len(candidate) > self.max_chunk_size:
                if current:
                    chunks.append(current)
                current = para
            else:
                current = candidate

        if current:
            chunks.append(current)

        return [c for c in chunks if len(c) >= self.min_chunk_size]

    def get_config(self) -> dict:
        return {
            "min_chunk_size": self.min_chunk_size,
            "max_chunk_size": self.max_chunk_size,
            "heading_levels": self.heading_levels,
            "add_heading_context": self.add_heading_context,
        }

    @staticmethod
    def get_common_params() -> List[dict]:
        return [
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
                "default": 2000,
                "min": 200,
                "max": 10000,
                "step": 100,
                "label": "最大块大小",
                "help": "每个块的最多字符数",
            },
        ]