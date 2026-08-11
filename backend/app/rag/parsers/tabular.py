"""表格数据（xlsx/csv）行组切分与序列化的共享逻辑。

Excel 表格入库最佳实践：每行是「列名：值」键值对、块内重复表头保证自包含，
避免「表头在块 A、值在块 B」导致向量与 BM25 检索不到整行语义。行组切片
（每块 ≤ CHUNK_ROWS 行）限制单块规模，同时保住行内关联。
"""

CHUNK_ROWS = 50


def iter_row_groups(rows: list[list], chunk_rows: int = CHUNK_ROWS):
    """把嵌套行按行组切片。yield (start_index, group_rows)。"""
    for start in range(0, len(rows), chunk_rows):
        yield start, rows[start : start + chunk_rows]


def normalize_row(header: list, row: list) -> list:
    """行宽与表头不一致时补空列，避免列错位。"""
    if len(row) == len(header):
        return row
    if len(row) > len(header):
        return row[: len(header)]
    return row + [""] * (len(header) - len(row))


def table_text(header: list, group: list) -> str:
    """把行组序列化成「列名：值；…」的文本（每行一行，块内重复表头）。"""
    lines = []
    for raw_row in group:
        row = normalize_row(header, raw_row)
        pairs = [f"{h}：{v}" if str(h).strip() else str(v) for h, v in zip(header, row)]
        lines.append("；".join(pairs))
    return "\n".join(lines)


def rows_to_nested(rows: list[list]) -> list[list]:
    """统一为字符串嵌套行，供 metadata["table"]（与其他解析器 table 块一致）。"""
    return [[str(c) for c in row] for row in rows]
