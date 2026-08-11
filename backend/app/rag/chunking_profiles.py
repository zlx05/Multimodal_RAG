"""资料分块 Profile：按资料结构和使用场景选择切分策略。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .blocks import DocumentBlock


@dataclass(frozen=True)
class ChunkingProfile:
    id: str
    label: str
    description: str
    chunker: str
    params: dict[str, Any]
    parent_child: bool = False
    contextual_retrieval: bool = False
    # 结构化重建：把标题块与正文按章节合并成带 # 层级的 Markdown 文本，
    # 让 markdown 分块器按标题真正切分（标题进 chunk 正文，而非只进 search_text）。
    reconstruct_structure: bool = False


PROFILES: dict[str, ChunkingProfile] = {
    "technical": ChunkingProfile(
        id="technical",
        label="技术文档",
        description="按标题结构切片并保留章节上下文，适合 Markdown、HTML 和代码资料。",
        chunker="markdown",
        params={"min_chunk_size": 40, "max_chunk_size": 1600},
        parent_child=True,
        reconstruct_structure=True,
    ),
    "long_form": ChunkingProfile(
        id="long_form",
        label="长文/研究报告",
        description="按章节聚合后进行语义边界切分，并保留父章节上下文。",
        chunker="semantic",
        params={"similarity_threshold": 0.55, "min_chunk_size": 120, "max_chunk_size": 1800},
        parent_child=True,
    ),
    "layout": ChunkingProfile(
        id="layout",
        label="版面资料",
        description="保留 PDF、扫描件、表格、公式和图片区域，不跨区域强行合并。",
        chunker="preserve",
        params={},
    ),
    "short_qa": ChunkingProfile(
        id="short_qa",
        label="短问答库",
        description="固定长度切分并保留少量重叠，适合短知识卡片和问答资料。",
        chunker="fixed",
        params={"chunk_size": 500, "overlap": 60, "min_chunk_size": 20},
    ),
    "high_value": ChunkingProfile(
        id="high_value",
        label="高价值知识库",
        description="父子块 + 标题上下文，可选 Contextual Retrieval 增强。",
        chunker="semantic",
        params={"similarity_threshold": 0.55, "min_chunk_size": 100, "max_chunk_size": 1600},
        parent_child=True,
        contextual_retrieval=True,
    ),
    "spreadsheet": ChunkingProfile(
        id="spreadsheet",
        label="表格数据",
        description="Excel/CSV 按行组切片、块内重复表头，行内关联不丢失。",
        chunker="preserve",
        params={},
    ),
}


def profile_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": profile.id,
            "label": profile.label,
            "description": profile.description,
            "parent_child": profile.parent_child,
            "contextual_retrieval": profile.contextual_retrieval,
        }
        for profile in PROFILES.values()
    ]


# 版面块：表格/公式/整页扫描 OCR。image_description 是视觉对插图的描述，
# 带插图的文本 PDF 不应因此被拖进 layout（否则跨页语义合并失效）。
LAYOUT_CONTENT_TYPES = {"table", "formula", "image_ocr", "image"}
TEXT_CONTENT_TYPES = {"text", "paragraph", "heading", "code"}


def resolve_profile(
    requested: str | None,
    filename: str,
    blocks: list[DocumentBlock],
) -> ChunkingProfile:
    """Resolve an explicit profile or choose one from source characteristics."""
    requested = (requested or "auto").strip().lower()
    if requested != "auto":
        if requested not in PROFILES:
            raise ValueError(f"未知的分块 Profile: {requested}")
        return PROFILES[requested]

    extension = Path(filename).suffix.lower()
    total_chars = sum(len(block.text or "") for block in blocks)
    content_types = {str(block.content_type) for block in blocks}
    if _looks_like_qa(blocks):
        return PROFILES["short_qa"]
    if extension in {".xlsx", ".csv"}:
        return PROFILES["spreadsheet"]
    if extension in {".md", ".html", ".htm"}:
        if extension in {".html", ".htm"}:
            # 实测反馈：HTML 章节重建（reconstruct_structure）把标题+正文合并成
            # 大块，检索效果反而不如升级前的逐块切分。HTML 恢复为每块独立成
            # chunk（chunk_level=0，不带父级合并），标题仍进 search_text 作为上下文。
            return replace(
                PROFILES["technical"],
                parent_child=False,
                reconstruct_structure=False,
            )
        return PROFILES["technical"]
    if LAYOUT_CONTENT_TYPES & content_types:
        # 只有版面块真正占主导（或纯视觉、几乎无文本）才用 layout。
        # 以文本为主的资料（如带插图的 PDF/PPT）交给 semantic：
        # 文本跨页语义切分，图片/表格在分块时作为屏障保留自身溯源。
        layout_count = sum(1 for block in blocks if block.content_type in LAYOUT_CONTENT_TYPES)
        text_count = sum(1 for block in blocks if block.content_type in TEXT_CONTENT_TYPES)
        layout_dominant = layout_count >= 2 and layout_count * 2 > text_count
        if layout_dominant or total_chars <= 600:
            return PROFILES["layout"]
    if total_chars <= 2400:
        return PROFILES["short_qa"]
    return PROFILES["long_form"]


def group_blocks_for_profile(
    blocks: list[DocumentBlock],
    profile: ChunkingProfile,
) -> list[DocumentBlock]:
    """Group adjacent textual blocks within one section for long-form profiles.

    文本块在**同一标题段落内跨页合并**（PDF 一页一个块，连续主题经常跨页），
    布局块（表格/公式/图片）是屏障：保留自身溯源，绝不与正文混并。

    跨页合并时折叠空白得到连续规范文本（PDF 行断不携带语义），并在 metadata
    记录 ``_page_segments``（文本偏移 -> 页码），供入库时把 chunk 归属到具体页。
    """
    if profile.reconstruct_structure:
        return _group_structured(blocks, profile)

    if not profile.parent_child:
        return blocks

    grouped: list[DocumentBlock] = []
    pending: list[DocumentBlock] = []

    def flush() -> None:
        if not pending:
            return
        first = pending[0]
        if len(pending) == 1:
            grouped.append(first)
            return
        has_pages = any(item.page_number is not None for item in pending)
        if has_pages:
            canonical: list[str] = []
            segments: list[tuple[int, int, int | None]] = []
            pos = 0
            for item in pending:
                text = " ".join((item.text or "").split())
                if not text:
                    continue
                canonical.append(text)
                segments.append((pos, pos + len(text), item.page_number))
                pos += len(text) + 1  # 连接用空格
            metadata = dict(first.metadata)
            metadata["_page_segments"] = segments
            grouped.append(replace(first, text=" ".join(canonical), metadata=metadata))
        else:
            merged_text = "\n\n".join(item.text.strip() for item in pending if item.text.strip())
            grouped.append(replace(first, text=merged_text))

    for block in blocks:
        text_like = block.content_type in TEXT_CONTENT_TYPES
        same_context = (
            pending
            and pending[-1].heading_path == block.heading_path
            and pending[-1].source_type == block.source_type
            and _same_slide(pending[-1], block)
        )
        if text_like and same_context:
            pending.append(block)
            continue
        flush()
        pending.clear()
        if text_like:
            pending.append(block)
        else:
            grouped.append(block)
    flush()
    return grouped


def _same_slide(a: DocumentBlock, b: DocumentBlock) -> bool:
    """幻灯片边界：带 slide_number 的块（PPT）只在同一张幻灯片内合并。

    PDF 块不带 slide_number -> 返回 True，跨页语义合并不受影响。
    """
    slide_a = a.metadata.get("slide_number")
    slide_b = b.metadata.get("slide_number")
    if slide_a is None and slide_b is None:
        return True
    return slide_a == slide_b


def _group_structured(blocks: list[DocumentBlock], profile: ChunkingProfile) -> list[DocumentBlock]:
    """结构化重建：按章节把标题 + 正文合并成一个带 Markdown 层级的块。

    HTML/Markdown 解析器产出的是「标题块 + 若干正文块」的独立序列；若原样喂给
    markdown 分块器，每块单独切分，章节标题只进 search_text 不进 chunk 正文。
    这里按标题路径重建 ``# 标题`` + ``\n\n`` 正文 的 Markdown 文本，
    markdown 分块器据此按标题真正切分；表格/公式/图片仍是屏障块，独立保留溯源。
    """
    grouped: list[DocumentBlock] = []
    section_parts: list[str] = []
    section_head: DocumentBlock | None = None

    def flush() -> None:
        nonlocal section_parts, section_head
        if not section_parts:
            return
        if section_head is None:
            section_head = blocks[0]
        text = "\n\n".join(part for part in section_parts if part.strip()).strip()
        if text:
            grouped.append(replace(section_head, text=text))
        section_parts = []
        section_head = None

    for block in blocks:
        if block.content_type in TEXT_CONTENT_TYPES:
            if block.content_type == "heading":
                flush()
                depth = max(len(block.heading_path), 1)
                # 标题文本可能已带 # 前缀（不同解析器输出不同），先剥掉再重建层级
                title = block.text.strip().lstrip("#").strip()
                section_parts.append(f"{'#' * depth} {title}")
                section_head = block
            else:
                section_parts.append(block.text.strip())
                if section_head is None:
                    section_head = block
        else:
            # 屏障块（表格/公式/图片）：终止当前章节，保留自身溯源
            flush()
            grouped.append(block)
    flush()
    return grouped


def _looks_like_qa(blocks: list[DocumentBlock]) -> bool:
    sample = "\n".join(block.text or "" for block in blocks[:30])
    markers = ("问题：", "答案：", "Q：", "A：", "问：", "答：")
    return sum(sample.count(marker) for marker in markers) >= 2
