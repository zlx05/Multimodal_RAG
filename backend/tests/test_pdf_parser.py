"""阶段4测试：pdfplumber 版面解析的纯函数（字号标题启发式 + 段落重建 + 表格）。"""

from backend.app.rag.parsers.pdf_parser import (
    _classify_heading_lines,
    _line_in_tables,
    _reconstruct_paragraphs,
    _sections_from_headings,
    _serialize_table,
    _split_heading_lines,
)


def _ln(text, size, x0=0, top=0, x1=100, bottom=12, fontname="Helvetica"):
    return {
        "text": text,
        "x0": x0,
        "top": top,
        "x1": x1,
        "bottom": bottom,
        "chars": [{"size": size, "fontname": fontname, "text": text}],
    }


def _blk(text, size, bold=False):
    fontname = "Helvetica-Bold" if bold else "Helvetica"
    return _ln(text, size, fontname=fontname)


# ---------------------------------------------------------------- 标题启发式

def test_headings_detected_by_larger_font_size():
    lines = [
        _blk("第一章 概述", 18),   # 标题
        _blk("正文第一行。", 11),  # 正文（中位数 11）
        _blk("正文第二行。", 11),
        _blk("1.1 背景", 14),      # 子标题
        _blk("子节正文。", 11),
    ]
    classified = _classify_heading_lines(lines)
    levels = [level for _, level in classified]
    assert levels == [1, 0, 0, 2, 0]
    assert classified[0][0] == "第一章 概述"
    assert classified[3][0] == "1.1 背景"


def test_bold_same_size_is_heading():
    lines = [_blk("普通行。", 10), _blk("加粗标题", 10, bold=True), _blk("普通行2。", 10)]
    levels = [level for _, level in _classify_heading_lines(lines)]
    assert levels == [0, 1, 0]


def test_all_same_size_no_headings():
    lines = [_blk("a", 10), _blk("b", 10), _blk("c", 10)]
    assert [level for _, level in _classify_heading_lines(lines)] == [0, 0, 0]


# ---------------------------------------------------------------- 章节切分

def test_sections_build_heading_path_hierarchy():
    parts = _split_heading_lines(
        [_blk("第一章 概述", 18), _blk("正文。", 11), _blk("1.1 细节", 14), _blk("细节正文。", 11)],
        table_bboxes=[],
    )
    sections = _sections_from_headings(parts)
    # 两节：第一章(标题+正文) 和 1.1(标题+正文)
    assert len(sections) == 2

    head0, body0 = sections[0]
    assert head0[2] == "第一章 概述"
    assert head0[1] == ["第一章 概述"]
    assert [b[0] for b in body0] == ["正文。"]

    head1, body1 = sections[1]
    assert head1[2] == "1.1 细节"
    assert head1[1] == ["第一章 概述", "1.1 细节"]
    assert [b[0] for b in body1] == ["细节正文。"]


def test_lead_text_before_heading_has_no_heading_path():
    parts = _split_heading_lines(
        [_blk("文档开头引导。", 11), _blk("第一章", 18), _blk("正文。", 11)],
        table_bboxes=[],
    )
    sections = _sections_from_headings(parts)
    assert len(sections) == 2
    assert sections[0][0] is None  # 引导段无标题
    assert sections[1][0][2] == "第一章"


def test_table_region_lines_excluded_from_body():
    parts = _split_heading_lines(
        [
            _ln("正文开头。", 11),
            _ln("单元格A", 10, x0=150, top=30, x1=200, bottom=42),  # 表格区内
            _ln("正文结尾。", 11, top=60),
        ],
        table_bboxes=[(140, 28, 210, 45)],
    )
    bodies = [p for p in parts if p[0] == "body"]
    assert [b[1] for b in bodies] == ["正文开头。", "正文结尾。"]


def test_line_in_tables_intersection_and_disjoint():
    bbox = (100, 20, 300, 80)
    assert _line_in_tables(_ln("x", 10, x0=150, top=30, x1=200, bottom=42), [bbox])
    assert not _line_in_tables(_ln("x", 10, x0=10, top=5, x1=90, bottom=15), [bbox])
    assert not _line_in_tables(_ln("x", 10), [])  # 无表格区 -> False


# ---------------------------------------------------------------- 段落重建

def test_reconstruct_paragraphs_splits_on_vertical_gap():
    lines = [
        ("第一段第一行。", 0, 0, 100, 12),
        ("第一段第二行。", 0, 12, 100, 24),
        ("第二段第一行。", 0, 40, 100, 52),  # 间隙 16 > 阈值
    ]
    text = _reconstruct_paragraphs(lines)
    assert "第一段第一行。\n第一段第二行。" in text
    assert "\n\n第二段第一行。" in text


def test_serialize_table_markdown():
    rows = [["名称", "数量"], ["苹果", "3"]]
    out = _serialize_table(rows)
    assert "| 名称 | 数量 |" in out
    assert "|---|" in out
    assert "| 苹果 | 3 |" in out
