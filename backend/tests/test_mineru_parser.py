"""阶段3测试：mineru middle.json → DocumentBlock 映射。

不真跑 mineru 子进程，用按 MinerU 3.x 真实嵌套 schema 伪造的 middle.json fixture，
断言 para_blocks 到 DocumentBlock 的字段映射。

真实结构（mineru/utils/pdf_image_tools.py 的 cut_image / 3.x 序列化）：
- 文本/标题在 lines[].spans[].content（不一定是顶层 content）；
- 表格 html 在 table_body 子块 lines[].spans[].html；
- 图片路径在 image_body 子块 lines[].spans[].image_path（扁平 <sha256>.jpg）；
- caption 在 *_caption 子块。
"""

import json
from pathlib import Path

from backend.app.rag.parsers.mineru_parser import (
    MineruParser,
    _find_middle_json,
    _table_rows_from_html,
)

IMG_DIR = Path("/virtual/images")


def _parser(tmp_path) -> MineruParser:
    return MineruParser("doc_test", work_dir=str(tmp_path))


def _write_middle(tmp_path, pdf_info) -> Path:
    out = tmp_path / "mineru_doc_test" / "sample"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "sample_middle.json"
    path.write_text(json.dumps({"pdf_info": pdf_info}), encoding="utf-8")
    return path


def _text_span(content: str) -> dict:
    return {"type": "text", "content": content}


def _text_block(text: str) -> dict:
    return {"type": "text", "lines": [{"spans": [_text_span(text)]}]}


def _title_block(text: str) -> dict:
    return {"type": "title", "lines": [{"spans": [_text_span(text)]}]}


def _table_block(html: str, caption: str = "") -> dict:
    block = {
        "type": "table",
        "blocks": [
            {
                "type": "table_body",
                "lines": [{"spans": [{"type": "table", "html": html}]}],
            }
        ],
    }
    if caption:
        block["blocks"].append(
            {"type": "table_caption", "lines": [{"spans": [_text_span(caption)]}]}
        )
    return block


def _image_block(image_path: str, caption: str = "") -> dict:
    block = {
        "type": "image",
        "blocks": [
            {
                "type": "image_body",
                "lines": [{"spans": [{"type": "image", "image_path": image_path}]}],
            }
        ],
    }
    if caption:
        block["blocks"].append(
            {"type": "image_caption", "lines": [{"spans": [_text_span(caption)]}]}
        )
    return block


def test_find_middle_json_recursive(tmp_path):
    path = _write_middle(tmp_path, [])
    assert _find_middle_json(tmp_path / "mineru_doc_test", "sample") == path


def test_map_text_block_from_nested_spans():
    p = _parser(".")
    block = p._map_block(
        _text_block("正文内容"), page_number=2, image_dir=IMG_DIR
    )
    assert block is not None
    assert block.content_type == "text"
    assert block.text == "正文内容"
    assert block.page_number == 2
    assert block.metadata["mineru_engine"] is True
    assert block.metadata["mineru_type"] == "text"  # 统一小写


def test_map_title_block_builds_heading_path():
    p = _parser(".")
    block = p._map_block(
        _title_block("第一章 概述"), page_number=1, image_dir=IMG_DIR
    )
    assert block.content_type == "heading"
    assert block.heading_path == ["第一章 概述"]


def test_map_table_block_html_and_rows():
    p = _parser(".")
    html = (
        "<table><tr><td>名称</td><td>数量</td></tr>"
        "<tr><td>苹果</td><td>3</td></tr></table>"
    )
    block = p._map_block(_table_block(html), page_number=1, image_dir=IMG_DIR)
    assert block.content_type == "table"
    assert block.metadata["table"] == [["名称", "数量"], ["苹果", "3"]]
    assert "| 名称 | 数量 |" in block.text
    assert "| 苹果 | 3 |" in block.text


def test_table_caption_merged_into_metadata():
    p = _parser(".")
    html = "<table><tr><td>a</td><td>b</td></tr></table>"
    block = p._map_block(
        _table_block(html, caption="表1 实验数据"), page_number=1, image_dir=IMG_DIR
    )
    assert block.metadata["table_caption"] == "表1 实验数据"
    assert "表1 实验数据" in block.text


def test_map_image_block_resolves_flat_path_and_caption():
    p = _parser(".")
    block = p._map_block(
        _image_block("a1b2c3.jpg", caption="图1 系统架构图"),
        page_number=3,
        image_dir=IMG_DIR,
    )
    assert block.content_type == "image_description"
    assert block.page_number == 3
    # 扁平 <sha256>.jpg -> images/ 目录下
    assert block.image_path == str(IMG_DIR / "images" / "a1b2c3.jpg")
    assert block.text == "图1 系统架构图"


def test_map_image_block_without_caption_falls_back_to_path():
    p = _parser(".")
    block = p._map_block(
        _image_block("xyz.jpg"), page_number=1, image_dir=IMG_DIR
    )
    assert block.content_type == "image_description"
    assert block.text != ""


def test_image_block_vision_enrichment_appends_description(tmp_path):
    """图片内容可召回：MinerU 图片块在 caption 上追加 vision 描述，像素内容进库。"""
    class FakeVision:
        def analyze(self, image_path, **kwargs):
            return type(
                "R",
                (),
                {"text": "图中是三层架构：前端 / 后端 / 数据库", "metadata": {"vision_model": "fake-model"}},
            )()

    img = tmp_path / "images" / "a1b2c3.jpg"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"\xff\xd8\xff")

    p = MineruParser("doc_test", work_dir=str(tmp_path), vision_analyzer=FakeVision())
    block = p._map_block(
        _image_block("a1b2c3.jpg", caption="图1 系统架构图"),
        page_number=3,
        image_dir=tmp_path / "images",
    )
    assert block.content_type == "image_description"
    assert "图1 系统架构图" in block.text
    assert "三层架构" in block.text
    assert block.metadata["vision_description"] == "图中是三层架构：前端 / 后端 / 数据库"
    assert block.metadata["vision_model"] == "fake-model"


def test_image_block_vision_failure_keeps_caption(tmp_path):
    """vision 失败不阻塞解析：保留 caption，不加描述。"""
    class FailingVision:
        def analyze(self, image_path, **kwargs):
            raise RuntimeError("provider down")

    img = tmp_path / "images" / "x.jpg"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"\xff\xd8\xff")

    p = MineruParser("doc_test", work_dir=str(tmp_path), vision_analyzer=FailingVision())
    block = p._map_block(
        _image_block("x.jpg", caption="图2 流程图"),
        page_number=1,
        image_dir=tmp_path / "images",
    )
    assert block.content_type == "image_description"
    assert block.text == "图2 流程图"
    assert "vision_description" not in block.metadata


def test_map_equation_block_wraps_latex():
    p = _parser(".")
    block = p._map_block(
        {
            "type": "interline_equation",
            "lines": [{"spans": [{"type": "interline_equation", "content": "E = mc^2"}]}],
        },
        page_number=1,
        image_dir=IMG_DIR,
    )
    assert block.content_type == "formula"
    assert "$$" in block.text
    assert "E = mc^2" in block.text
    assert block.metadata["formulas_latex"] == ["E = mc^2"]


def test_map_inline_equation_wrapped_in_dollar():
    p = _parser(".")
    block = p._map_block(
        {
            "type": "text",
            "lines": [
                {
                    "spans": [
                        _text_span("当 "),
                        {"type": "inline_equation", "content": "x > 0"},
                        _text_span(" 时"),
                    ]
                }
            ],
        },
        page_number=1,
        image_dir=IMG_DIR,
    )
    assert block.content_type == "text"
    assert "$x > 0$" in block.text


def test_map_unknown_block_skipped():
    p = _parser(".")
    assert (
        p._map_block(
            {"type": "footer", "content": "页脚"}, page_number=1, image_dir=IMG_DIR
        )
        is None
    )


def test_blocks_from_middle_json_page_offset_and_filter(tmp_path):
    middle = _write_middle(
        tmp_path,
        [
            {
                "page_idx": 0,
                "para_blocks": [
                    _title_block("标题"),
                    _text_block("第一页正文"),
                    {"type": "footer", "content": "页脚（应被跳过）"},
                ],
            },
            {
                "page_idx": 1,
                "para_blocks": [_text_block("第二页正文")],
            },
        ],
    )
    p = _parser(tmp_path)
    blocks = list(
        p._blocks_from_middle_json(middle, tmp_path / "mineru_doc_test" / "sample")
    )
    assert [b.content_type for b in blocks] == ["heading", "text", "text"]
    assert [b.page_number for b in blocks] == [1, 1, 2]  # page_idx + 1
    assert blocks[0].heading_path == ["标题"]
