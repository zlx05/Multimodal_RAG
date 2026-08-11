"""版面资料分块器：一个解析区域保持为一个 chunk。"""

from typing import List

from .base import BaseChunker


class PreserveBlockChunker(BaseChunker):
    name = "preserve"
    description = "保留解析区域，不跨图片、表格、公式或页面合并"

    def chunk(self, text: str) -> List[str]:
        text = (text or "").strip()
        return [text] if text else []

    def get_config(self) -> dict:
        return {}
