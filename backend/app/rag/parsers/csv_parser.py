"""CSV 解析器：复用表格行组分块逻辑，sheet 名 = 文件名 stem。

与 xlsx 相同的表头行语义：首行有内容行为表头；每个行组块「列名：值；…」。
"""

import csv
from pathlib import Path

from .base import BaseParser
from .tabular import iter_row_groups, rows_to_nested, table_text
from ..blocks import DocumentBlock


class CsvParser(BaseParser):
    source_type = "csv"

    def __init__(self, document_id: str, **kwargs):
        # original_dir/work_dir/vision 等由 worker 统一传入，csv 直接读原文件无需复制
        super().__init__(document_id)

    def parse(self, path: str | Path) -> list[DocumentBlock]:
        sheet_name = Path(path).stem
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            reader = csv.reader(fh)
            rows = [[str(c).strip() for c in row] for row in reader]
        return self._rows_to_blocks(rows, sheet_name)

    def _rows_to_blocks(self, rows: list[list], sheet_name: str) -> list[DocumentBlock]:
        try:
            header = next(row for row in rows if any(c for c in row))
        except StopIteration:
            return []
        header_idx = rows.index(header)
        data_rows = rows[header_idx + 1 :]
        blocks: list[DocumentBlock] = []
        for start, group in iter_row_groups(data_rows):
            row_start = header_idx + start + 1
            row_end = header_idx + start + len(group)
            blocks.append(
                self._block(
                    table_text(header, group),
                    content_type="table",
                    heading_path=[sheet_name, f"行 {row_start}-{row_end}"],
                    metadata={
                        "sheet_name": sheet_name,
                        "row_start": row_start,
                        "row_end": row_end,
                        "table": rows_to_nested(group),
                        "table_format": "csv",
                    },
                )
            )
        return blocks
