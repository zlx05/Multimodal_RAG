"""RAG 分片策略统一导出

使用方式：
    from backend.app.rag.chunkers import get_chunker, CHUNKER_REGISTRY

    # 获取一个分片器实例
    chunker = get_chunker("fixed", chunk_size=300)

    # 直接导入具体分片器
    from backend.app.rag.chunkers import FixedLengthChunker, RecursiveChunker
"""

from typing import Dict, List, Type

from .base import BaseChunker, ChunkResult
from .fixed_chunker import FixedLengthChunker
from .markdown_chunker import MarkdownChunker
from .recursive_chunker import RecursiveChunker
from .semantic_chunker import SemanticChunker
from .sliding_window_chunker import SlidingWindowChunker
from .token_chunker import TokenChunker
from .preserve_chunker import PreserveBlockChunker

# 分片器注册表
CHUNKER_REGISTRY: Dict[str, Type[BaseChunker]] = {
    "fixed": FixedLengthChunker,
    "recursive": RecursiveChunker,
    "semantic": SemanticChunker,
    "token": TokenChunker,
    "markdown": MarkdownChunker,
    "sliding": SlidingWindowChunker,
    "preserve": PreserveBlockChunker,
}

# 分片器元信息
CHUNKER_INFO: Dict[str, Dict[str, str]] = {
    "fixed": {
        "name": "固定长度 + 段落边界",
        "description": "按字符数切分，同时尽量保持段落边界完整。支持重叠窗口避免信息被切断。",
        "category": "字符级",
    },
    "recursive": {
        "name": "递归字符分片",
        "description": "按优先级尝试不同的分隔符（段落 > 换行 > 句子 > 字符）进行切分，LangChain 风格。",
        "category": "字符级",
    },
    "semantic": {
        "name": "语义分片",
        "description": "基于 Embedding 相似度判断语义边界，在语义断点处切分，保持语义完整性。",
        "category": "语义级",
    },
    "token": {
        "name": "Token 分片",
        "description": "按 LLM 的 token 计数进行切分，比字符分片更精准控制输入大小。",
        "category": "Token 级",
    },
    "markdown": {
        "name": "Markdown 分片",
        "description": "按 Markdown 标题层级结构切分，保留标题作为上下文，适合结构化文档。",
        "category": "结构级",
    },
    "sliding": {
        "name": "滑动窗口分片",
        "description": "按句子边界切分，窗口之间有重叠，确保信息不丢失，适合高召回率场景。",
        "category": "句子级",
    },
    "preserve": {
        "name": "版面区域保留",
        "description": "保留解析器识别出的图片、表格、公式和页面区域，不跨区域合并。",
        "category": "版面级",
    },
}


def get_chunker(name: str, **kwargs) -> BaseChunker:
    """工厂函数：获取分片器实例

    Args:
        name: 分片器名称，如 "fixed", "recursive", "semantic" 等
        **kwargs: 传递给分片器的参数

    Returns:
        分片器实例

    Raises:
        ValueError: 当分片器名称不存在时

    Example:
        # 使用默认参数
        chunker = get_chunker("fixed")

        # 自定义参数
        chunker = get_chunker("semantic", similarity_threshold=0.6)
        chunks = chunker.chunk(text)
    """
    if name not in CHUNKER_REGISTRY:
        available = ", ".join(CHUNKER_REGISTRY.keys())
        raise ValueError(
            f"未知的分片器: {name}。可用分片器: {available}"
        )
    return CHUNKER_REGISTRY[name](**kwargs)


def list_chunker_names() -> List[str]:
    """返回所有可用的分片器名称"""
    return list(CHUNKER_REGISTRY.keys())


def get_chunker_info(name: str) -> Dict[str, str]:
    """获取分片器元信息"""
    return CHUNKER_INFO.get(name, {})


__all__ = [
    # 基类
    "BaseChunker",
    "ChunkResult",
    # 具体分片器
    "FixedLengthChunker",
    "RecursiveChunker",
    "SemanticChunker",
    "TokenChunker",
    "MarkdownChunker",
    "SlidingWindowChunker",
    "PreserveBlockChunker",
    # 注册表和工厂
    "CHUNKER_REGISTRY",
    "CHUNKER_INFO",
    "get_chunker",
    "list_chunker_names",
    "get_chunker_info",
]
