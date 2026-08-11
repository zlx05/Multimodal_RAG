"""清洗层（backend/app/rag/cleaning.py）单元测试。"""

from backend.app.rag.blocks import DocumentBlock
from backend.app.rag.cleaning import clean_blocks, clean_text, _is_boilerplate_line


def block(text: str, *, content_type: str = "text", source_type: str = "pdf"):
    return DocumentBlock(
        document_id="doc_clean",
        source_type=source_type,
        content_type=content_type,
        text=text,
        page_number=1,
    )


# ---------------------------------------------------------------- clean_text

def test_clean_text_collapses_running_spaces_and_fullwidth_space():
    assert clean_text("RAG  系统\t很好") == "RAG 系统 很好"
    assert clean_text("　全角　空格　") == " 全角 空格 "


def test_clean_text_removes_ligatures_and_control_chars():
    assert clean_text("ﬁ ﬂ ﬀ") == "fi fl ff"
    assert "\x07" not in clean_text("abc\x07def")
    assert clean_text("a\fb") == "ab"


def test_clean_text_fixes_hyphen_line_break():
    assert clean_text("exam-\nple text") == "example text"
    # 中文不误伤
    assert clean_text("学习-\n资料") == "学习-\n资料"


def test_clean_text_preserves_newlines():
    assert clean_text("第一行\n第二行") == "第一行\n第二行"


# ---------------------------------------------------------------- 页眉页脚/水印

def test_boilerplate_line_patterns():
    assert _is_boilerplate_line("第 3 页")
    assert _is_boilerplate_line("第3页")
    assert _is_boilerplate_line("Page 3 of 12")
    assert _is_boilerplate_line("12")
    assert _is_boilerplate_line("机密")
    assert _is_boilerplate_line("Confidential")
    assert _is_boilerplate_line("版权所有")
    assert _is_boilerplate_line("---")
    assert not _is_boilerplate_line("RAG 检索增强生成")
    assert not _is_boilerplate_line("2024 年报告")


def test_clean_blocks_strips_page_number_and_watermark_lines():
    blocks = [
        block("第 3 页\nRAG 是检索增强生成。\nConfidential"),
        block("第 4 页\n向量检索与 BM25 融合。\nConfidential"),
    ]
    clean_blocks(blocks)
    assert "第 3 页" not in blocks[0].text
    assert "Confidential" not in blocks[0].text
    assert "RAG 是检索增强生成。" in blocks[0].text


def test_frequency_heuristic_removes_repeated_boilerplate():
    # 5 块里 3 块都出现 "某某大学 内部资料" -> 频次启发剥离
    blocks = [block(f"行{i}\n某某大学 内部资料\n正文{i}") for i in range(5)]
    clean_blocks(blocks)
    for item in blocks:
        assert "某某大学 内部资料" not in item.text


def test_clean_blocks_never_empties_a_block():
    # 全是页码的单行块 -> 兜底回退原文，不丢块
    blocks = [block("3", content_type="image_ocr")]
    clean_blocks(blocks)
    assert blocks[0].text == "3"


def test_code_block_preserves_whitespace():
    blocks = [block("def  f():\n    return  1", content_type="code", source_type="markdown")]
    clean_blocks(blocks)
    # 代码块保留缩进与连续空格
    assert "def  f():\n    return  1" in blocks[0].text
