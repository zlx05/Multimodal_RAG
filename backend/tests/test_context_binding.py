"""阶段6测试：图片/公式块的邻近正文上下文绑定。"""

from backend.app.rag.blocks import DocumentBlock
from backend.app.rag.chunking_profiles import group_blocks_for_profile, resolve_profile
from backend.app.rag.hybrid_pipeline import (
    CONTEXT_BINDING_TYPES,
    CONTEXT_WINDOW,
    bind_context_around_media,
)


def _blk(text, *, content_type="text", heading=None, src="pdf", page=None):
    return DocumentBlock(
        "doc_c", src, content_type, text, heading_path=heading or [], page_number=page
    )


def test_image_block_gets_surrounding_text_context():
    blocks = [
        _blk("这是第一段正文，讲的是梯度下降的基本思想。" * 3),
        _blk("损失函数示意图", content_type="image_description", heading=["第3章"]),
        _blk("接下来介绍反向传播。" * 3),
    ]
    bind_context_around_media(blocks)
    context = blocks[1].metadata.get("context_text", "")
    # 前块尾部 + 后块头部
    assert "梯度下降" in context
    assert "反向传播" in context
    # 普通文本块不受影响
    assert "context_text" not in blocks[0].metadata
    assert "context_text" not in blocks[2].metadata


def test_context_window_truncated_to_last_words():
    long_prev = "开头。" + "中间内容。" * 100
    blocks = [
        _blk(long_prev),
        _blk("公式图", content_type="formula"),
        _blk("结尾段。" * 30),
    ]
    bind_context_around_media(blocks)
    context = blocks[1].metadata["context_text"]
    before, _, after = context.partition("\n")
    # 前块只取尾部窗口
    assert "中间内容" in before
    assert len(before) <= CONTEXT_WINDOW + 10
    assert "结尾段" in after
    assert len(after) <= CONTEXT_WINDOW + 10


def test_isolated_media_without_neighbors_gets_no_context():
    blocks = [_blk("孤立图片", content_type="image_ocr")]
    bind_context_around_media(blocks)
    assert "context_text" not in blocks[0].metadata


def test_media_skips_other_barrier_blocks_when_looking_for_text():
    # 图片夹在两张表格之间，前后最近的文本块才被取为上下文
    blocks = [
        _blk("正文开头。"),
        _blk("| A |\n|---|\n| 1 |", content_type="table"),
        _blk("示意图", content_type="image_description"),
        _blk("| B |\n|---|\n| 2 |", content_type="table"),
        _blk("正文结尾。"),
    ]
    bind_context_around_media(blocks)
    context = blocks[2].metadata["context_text"]
    assert "正文开头" in context
    assert "正文结尾" in context
    assert "| A |" not in context


def test_binding_after_grouping_keeps_media_as_barrier():
    # 真实流程：先按 profile 组块（图片是屏障块保持独立），再绑定上下文
    blocks = [
        _blk("第一章", content_type="heading", heading=["第一章"], src="markdown"),
        _blk("介绍损失函数。", heading=["第一章"], src="markdown"),
        _blk("图：损失曲线", content_type="image_description", heading=["第一章"], src="markdown"),
        _blk("损失越小越好。", heading=["第一章"], src="markdown"),
    ]
    profile = resolve_profile("technical", "a.md", blocks)
    grouped = group_blocks_for_profile(blocks, profile)
    bind_context_around_media(grouped)
    media = [b for b in grouped if b.content_type == "image_description"]
    assert len(media) == 1
    context = media[0].metadata.get("context_text", "")
    assert "损失函数" in context
    assert "损失越小越好" in context


def test_binding_types_cover_images_and_formulas():
    assert {"image_description", "formula", "image_ocr"} <= CONTEXT_BINDING_TYPES
