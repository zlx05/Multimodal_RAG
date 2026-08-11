"""分片策略抽象基类"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ChunkResult:
    """分片结果"""
    chunks: List[str]
    metadata: Optional[Dict[str, Any]] = None

    def __len__(self) -> int:
        return len(self.chunks)

    def __iter__(self):
        return iter(self.chunks)


class BaseChunker(ABC):
    """分片策略抽象基类

    所有分片策略必须继承此类并实现 chunk() 方法。
    """

    name: str = "base"
    description: str = "基础分片器"

    def chunk(self, text: str) -> List[str]:
        """将文本切分成块

        Args:
            text: 原始文本

        Returns:
            文本块列表
        """
        raise NotImplementedError

    def chunk_with_meta(self, text: str) -> ChunkResult:
        """带元数据的分片（可选实现）

        Args:
            text: 原始文本

        Returns:
            包含元数据的分片结果
        """
        chunks = self.chunk(text)
        return ChunkResult(chunks=chunks, metadata={"chunker": self.name})

    def get_config(self) -> Dict[str, Any]:
        """返回当前配置参数"""
        return {}

    @staticmethod
    def get_common_params() -> List[Dict[str, Any]]:
        """返回通用参数定义

        子类可覆盖此方法添加自己的参数
        """
        return []