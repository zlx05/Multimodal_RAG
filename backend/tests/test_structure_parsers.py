"""阶段2测试：Markdown/HTML 表格结构化解析 + technical profile 结构重建。"""

from pathlib import Path

from backend.app.rag.blocks import DocumentBlock
from backend.app.rag.chunking_profiles import group_blocks_for_profile, resolve_profile
from backend.app.rag.parsers.html_parser import HtmlParser
from backend.app.rag.parsers.markdown_parser import MarkdownParser


# ---------------------------------------------------------------- Markdown 表格

def test_markdown_pipe_table_becomes_structured_table_block(tmp_path):
    md = tmp_path / "t.md"
    md.write_text(
        "# 概述\n\n"
        "| 名称 | 数量 | 备注 |\n"
        "|------|------|------|\n"
        "| 苹果 | 3    | 红色 |\n"
        "| 香蕉 | 5    | 黄色 |\n\n"
        "正文。\n",
        encoding="utf-8",
    )
    blocks = MarkdownParser("doc_md").parse(md)
    table = [b for b in blocks if b.content_type == "table"]
    assert len(table) == 1
    # metadata 里行结构不含分隔行
    assert table[0].metadata["table"] == [
        ["名称", "数量", "备注"],
        ["苹果", "3", "红色"],
        ["香蕉", "5", "黄色"],
    ]
    # heading_path 归属所在章节
    assert table[0].heading_path == ["概述"]
    # text 保留原始 Markdown 表格（含分隔行）
    assert "|------|" in table[0].text


def test_markdown_plain_pipe_line_not_table_without_separator(tmp_path):
    md = tmp_path / "t.md"
    md.write_text("第一行 | 第二行（无分隔行，不是表格）\n", encoding="utf-8")
    blocks = MarkdownParser("doc_md").parse(md)
    assert all(b.content_type != "table" for b in blocks)


# ---------------------------------------------------------------- HTML 表格

def test_html_table_becomes_structured_table_block(tmp_path):
    html = tmp_path / "t.html"
    html.write_text(
        "<html><body><article><h1>概述</h1>"
        "<table><caption>成绩单</caption>"
        "<tr><th>姓名</th><th>分数</th></tr>"
        "<tr><td>张三</td><td>95</td></tr>"
        "</table>"
        "<p>结束。</p></article></body></html>",
        encoding="utf-8",
    )
    blocks = HtmlParser("doc_html", vision_analyzer=None).parse(html)
    table = [b for b in blocks if b.content_type == "table"]
    assert len(table) == 1
    assert table[0].metadata["table"] == [["姓名", "分数"], ["张三", "95"]]
    assert table[0].metadata["caption"] == "成绩单"
    assert table[0].heading_path == ["概述"]
    assert "| 姓名 | 分数 |" in table[0].text


# ---------------------------------------------------------------- technical 结构重建

def _blk(text, *, content_type="text", heading, src="markdown"):
    return DocumentBlock("doc_t", src, content_type, text, heading_path=heading)


def test_technical_reconstructs_headings_into_chunk_text():
    blocks = [
        _blk("第一章 概述", content_type="heading", heading=["第一章 概述"]),
        _blk("第一段正文。", heading=["第一章 概述"]),
        _blk("第二段正文。", heading=["第一章 概述"]),
        _blk("| A | B |\n|---|---|\n| 1 | 2 |", content_type="table", heading=["第一章 概述"]),
        _blk("1.1 细节", content_type="heading", heading=["第一章 概述", "1.1 细节"]),
        _blk("细节正文。", heading=["第一章 概述", "1.1 细节"]),
    ]
    profile = resolve_profile("technical", "go.md", blocks)
    assert profile.reconstruct_structure and profile.parent_child

    grouped = group_blocks_for_profile(blocks, profile)
    sections = [g for g in grouped if g.content_type in {"text", "heading", "code"}]
    # 两个章节：第一章(合并了标题+两段正文) 与 1.1 细节
    assert len(sections) == 2
    assert sections[0].text.startswith("# 第一章 概述")
    assert "第一段正文。" in sections[0].text and "第二段正文。" in sections[0].text
    assert sections[1].text.startswith("## 1.1 细节")
    assert "细节正文。" in sections[1].text

    # 表格仍是屏障块
    tables = [g for g in grouped if g.content_type == "table"]
    assert len(tables) == 1


def test_technical_strips_existing_hash_markers():
    blocks = [
        _blk("# 已经带 # 的标题", content_type="heading", heading=["已经带 # 的标题"]),
        _blk("正文。", heading=["已经带 # 的标题"]),
    ]
    grouped = group_blocks_for_profile(blocks, resolve_profile("technical", "a.md", blocks))
    assert grouped[0].text.startswith("# 已经带 # 的标题")
    assert not grouped[0].text.startswith("# # ")


def test_technical_lead_text_without_heading_stays_one_group():
    blocks = [
        _blk("文档开头无标题的引导段落。", heading=[]),
        _blk("第一章", content_type="heading", heading=["第一章"]),
        _blk("章节正文。", heading=["第一章"]),
    ]
    grouped = group_blocks_for_profile(blocks, resolve_profile("technical", "a.md", blocks))
    assert len(grouped) == 2
    assert "文档开头无标题的引导段落。" in grouped[0].text
