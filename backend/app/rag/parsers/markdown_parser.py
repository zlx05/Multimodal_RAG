"""Markdown 解析器：保留标题层级，产出带 heading_path 的 block。

解析策略：
- 按 # 标题层级分段，记录每个段落的标题路径（如 ["第一章", "1.1 随机事件"]）。
- 代码块（``` 包裹）单独作为 content_type="code" 的 block。
- pipe 表格（`| a | b |` + `|---|---|`）识别为 content_type="table" 块，
  metadata["table"] 保存嵌套行结构，text 保留原始 Markdown 表格文本。
- 段落之间保留，不把过长内容硬切（分块阶段再处理长度）。
"""

import re
from pathlib import Path
from typing import Iterator

from .base import BaseParser
from ..blocks import DocumentBlock

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
FENCE_RE = re.compile(r"^```")
TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")


def _split_table_row(line: str) -> list[str]:
    """拆 pipe 表行为单元格列表（去首尾管道、去空格）。"""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_table_separator(line: str) -> bool:
    """Markdown 表格分隔行：| :--- | :---: | --- |。"""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


class MarkdownParser(BaseParser):
    source_type = "markdown"

    def parse(self, path: str | Path) -> list[DocumentBlock]:
        text = Path(path).read_text(encoding="utf-8")
        return list(self._parse_text(text))

    def _parse_text(self, text: str) -> Iterator[DocumentBlock]:
        heading_stack: list[str] = []
        current_level = 0
        in_code = False
        current_text: list[str] = []
        current_kind = "text"

        def flush(force: bool = False):
            nonlocal current_text, current_kind
            content = "\n".join(current_text).strip()
            if content:
                yield self._block(
                    content,
                    content_type=current_kind,
                    heading_path=list(heading_stack),
                )
            current_text = []
            current_kind = "text"

        lines = text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].rstrip("\n")

            # 代码块开始/结束
            if FENCE_RE.match(line.strip()):
                if not in_code:
                    in_code = True
                    yield from flush()
                    current_kind = "code"
                    i += 1
                    continue
                else:
                    in_code = False
                    yield from flush()
                    i += 1
                    continue

            if in_code:
                current_text.append(line)
                i += 1
                continue

            # pipe 表格：首行 + 分隔行 -> 整表一个 table 块
            if (
                TABLE_ROW_RE.match(line.strip())
                and i + 1 < len(lines)
                and _is_table_separator(lines[i + 1])
            ):
                yield from flush()
                rows: list[list[str]] = []
                kept: list[str] = []
                row_in_table = 0
                while i < len(lines) and TABLE_ROW_RE.match(lines[i].strip()):
                    # 第二行是分隔行（| :--- |），进数据结构会污染行/列关系，跳过
                    if row_in_table != 1:
                        rows.append(_split_table_row(lines[i]))
                    row_in_table += 1
                    kept.append(lines[i].strip())
                    i += 1
                yield self._block(
                    "\n".join(kept),
                    content_type="table",
                    heading_path=list(heading_stack),
                    metadata={"table": rows, "table_format": "markdown"},
                )
                continue

            m = HEADING_RE.match(line.strip())
            if m:
                yield from flush()
                level = len(m.group(1))
                title = m.group(2).strip()
                # 更新标题栈：同级或更高级标题弹出
                while heading_stack and level <= current_level and len(heading_stack) >= level:
                    heading_stack.pop()
                    current_level -= 1
                heading_stack.append(title)
                current_level = level
                # 标题本身作为一个小 block，便于"按标题检索"
                yield self._block(title, content_type="heading", heading_path=list(heading_stack))
                i += 1
                continue

            current_text.append(line)
            i += 1

        yield from flush()
