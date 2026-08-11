"""纯文本（TXT）解析器：按空行分段，产出无标题路径的 block。"""

from pathlib import Path
from typing import Iterator

from .base import BaseParser
from ..blocks import DocumentBlock


class TextParser(BaseParser):
    source_type = "text"

    def parse(self, path: str | Path) -> list[DocumentBlock]:
        text = Path(path).read_text(encoding="utf-8")
        return list(self._parse_text(text))

    def _parse_text(self, text: str) -> Iterator[DocumentBlock]:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        for para in paragraphs:
            yield self._block(para)
