from backend.app.rag.blocks import DocumentBlock
from backend.app.rag.chunking_profiles import group_blocks_for_profile, resolve_profile


def block(text: str, *, content_type: str = "text", page: int = 1, heading: list[str] | None = None):
    return DocumentBlock(
        document_id="doc_test",
        source_type="pdf",
        content_type=content_type,
        text=text,
        page_number=page,
        heading_path=heading or ["第一章"],
    )


def test_auto_profile_routes_by_source_and_content():
    assert resolve_profile("auto", "go.md", [block("标题")]).id == "technical"
    assert resolve_profile("auto", "report.pdf", [block("x" * 3000)]).id == "long_form"
    assert resolve_profile("auto", "scan.pdf", [block("OCR", content_type="image_ocr")]).id == "layout"
    assert resolve_profile("auto", "cards.txt", [block("问题：什么是 RAG？\n答案：检索增强生成。")]).id == "short_qa"


def test_long_form_groups_same_section_but_keeps_layout_barrier():
    profile = resolve_profile("long_form", "report.pdf", [])
    grouped = group_blocks_for_profile(
        [
            block("第一段"),
            block("第二段"),
            block("表格", content_type="table"),
            block("第三段"),
        ],
        profile,
    )

    assert [item.content_type for item in grouped] == ["text", "table", "text"]
    assert "第一段" in grouped[0].text and "第二段" in grouped[0].text


def test_long_form_merges_text_across_pages_and_tracks_page_segments():
    """回归：PDF 每页产一个 block，连续主题跨页时不应按页硬切。

    跨页合并后 text 应为折叠空白的规范文本，metadata 记录偏移->页码。
    """
    profile = resolve_profile("long_form", "report.pdf", [])
    grouped = group_blocks_for_profile(
        [
            block("第一页内容", page=3),
            block("第二页继续", page=4),
            block("第三页继续", page=5),
        ],
        profile,
    )

    assert len(grouped) == 1
    assert "第一页内容" in grouped[0].text and "第三页继续" in grouped[0].text
    # 折叠空白：段间是单个空格而非换行
    assert "\n" not in grouped[0].text
    segments = grouped[0].metadata["_page_segments"]
    assert [(start, end, page) for start, end, page in segments] == [(0, 5, 3), (6, 11, 4), (12, 17, 5)]


def test_auto_profile_semantic_for_text_heavy_pdf_with_images():
    """带插图的长 PDF 应以文本为主走 semantic，而不是被一两个图片块拖进 layout。

    版面块占主导（或纯视觉无文本）时才选 layout。
    """
    blocks = [block("x" * 500) for _ in range(6)] + [block("插图说明", content_type="image_description", page=2)]
    assert resolve_profile("auto", "report.pdf", blocks).id == "long_form"
    # 纯扫描页/纯视觉仍走 layout
    assert resolve_profile("auto", "scan.pdf", [block("OCR", content_type="image_ocr")]).id == "layout"


def test_ppt_blocks_never_merge_across_slides():
    """PPT 以幻灯片为单元：无标题幻灯片 heading_path 全空，靠 slide_number 边界不跨页合并。

    PDF 块不带 slide_number -> 跨页语义合并不受影响（见 test_long_form_merges_text_across_pages）。
    """

    def slide(text: str, n: int):
        return DocumentBlock(
            document_id="doc_ppt",
            source_type="pptx",
            content_type="text",
            text=text,
            page_number=n,
            heading_path=[],
            metadata={"slide_number": n},
        )

    blocks = [slide("第一页要点一。", 1), slide("第二页要点二。", 2), slide("第三页要点三。", 3)]
    grouped = group_blocks_for_profile(blocks, resolve_profile("long_form", "deck.pptx", blocks))

    assert len(grouped) == 3
    assert [item.page_number for item in grouped] == [1, 2, 3]
    assert all("_page_segments" not in item.metadata for item in grouped)
