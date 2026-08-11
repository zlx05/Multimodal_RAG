"""跨页语义分块后的页码归属测试。

覆盖 hybrid_pipeline._attribute_pages：把语义分块器对跨页合并文本切出的
chunk 归属到页码范围（跨页 chunk 应报起始~结束页）。
"""

import re


def _split_sentences_like_semantic_chunker(text: str) -> list[str]:
    """镜像 SemanticChunker._split_into_sentences 的分词规则（同为正则+回补标点）。"""
    parts = re.split(r"[。！？\.!?\n]+", text)
    sentences = []
    for part in parts:
        part = part.strip()
        if part:
            for ending in ["。", "！", "？", ".", "!", "?"]:
                if text.count(ending) > 0 and part + ending in text:
                    sentences.append(part + ending)
                    break
            else:
                sentences.append(part)
    return sentences


def test_attribute_pages_single_page_chunks():
    from backend.app.rag.hybrid_pipeline import _attribute_pages

    text = "第一句。第二句。第三句。第四句。"
    # 前 8 个字符来自第 3 页，后 8 个来自第 4 页
    segments = [(0, 8, 3), (8, 16, 4)]
    chunks = ["第一句。第二句。", "第三句。第四句。"]
    pages = _attribute_pages(text, chunks, segments, 3, _split_sentences_like_semantic_chunker)
    assert pages == [(3, 3), (4, 4)]


def test_attribute_pages_cross_page_chunk_reports_range():
    from backend.app.rag.hybrid_pipeline import _attribute_pages

    text = "第一句。第二句。第三句。第四句。"
    segments = [(0, 8, 3), (8, 16, 4)]
    # 一个 chunk 跨第 3、4 页
    chunks = ["第一句。第二句。第三句。"]
    pages = _attribute_pages(text, chunks, segments, 3, _split_sentences_like_semantic_chunker)
    assert pages == [(3, 4)]


def test_page_at_offset_boundaries():
    from backend.app.rag.hybrid_pipeline import _page_at

    segments = [(0, 8, 3), (8, 16, 4)]
    assert _page_at(segments, 0) == 3
    assert _page_at(segments, 7) == 3
    assert _page_at(segments, 8) == 4
    assert _page_at(segments, 15) == 4
    assert _page_at(segments, 100) == 4  # 越界归到最后一页
