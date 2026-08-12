"""PDF 文档级分类：判定原生件 / 扫描件 / 混合图表件。

在 worker 解析前运行一次，决定整档用哪条解析路线：
- native：绝大多数页有可提取文本层、图片稀疏 → 走 pdfplumber 逐页快路（质量已好、快）；
- scanned：几乎无文本层（纯扫描件）→ 整档 MinerU OCR；
- mixed：既有文本页又有图片/表格/扫描页 → MinerU auto（布局 + 表格 + 公式 + 图片 caption）。

纯函数、只依赖 pypdf（文档级抽样成本远低于逐页 OCR），可在不引入 OCR/vision/mineru 的
情况下单测。阈值与 pdf_parser 的 TEXT_PAGE_MIN_CHARS 对齐。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path

from .pdf_parser import TEXT_PAGE_MIN_CHARS

# 抽样页数上限（文档页再多也只看这么多，避免大 PDF 分类耗时）
SAMPLE_PAGES_MAX = 10
# 文本页占比 >= 该值且图片密度低 → native
NATIVE_TEXT_RATIO = 0.75
# 文本页占比 <= 该值 → scanned
SCANNED_TEXT_RATIO = 0.25
# 平均每页图片数 < 该值才考虑 native（图片多视为图表件）
NATIVE_IMAGE_PER_PAGE = 0.5
# 整档文本总量 < 页数 * 该值 → 直接判 scanned（纯扫描件省去逐页统计）
SCANNED_MIN_CHARS_PER_PAGE = 5


class PdfKind(str, enum.Enum):
    native = "native"
    scanned = "scanned"
    mixed = "mixed"


@dataclass
class PageSample:
    """抽样页的文本与图片统计。"""

    page_number: int
    text_len: int
    image_count: int


@dataclass
class PdfClassification:
    """分类结果 + 抽样证据（供调试/前端展示/评估追溯）。"""

    kind: PdfKind
    sampled_pages: list[PageSample]

    @property
    def text_page_ratio(self) -> float:
        if not self.sampled_pages:
            return 0.0
        return sum(1 for s in self.sampled_pages if s.text_len >= TEXT_PAGE_MIN_CHARS) / len(
            self.sampled_pages
        )

    @property
    def image_per_page(self) -> float:
        if not self.sampled_pages:
            return 0.0
        return sum(s.image_count for s in self.sampled_pages) / len(self.sampled_pages)

    @property
    def scanned_page_ratio(self) -> float:
        """抽样页中无文本层（扫描页）的占比。>0 表示文档含扫描页，逐页 OCR 质量差。"""
        if not self.sampled_pages:
            return 0.0
        return sum(1 for s in self.sampled_pages if s.text_len < TEXT_PAGE_MIN_CHARS) / len(
            self.sampled_pages
        )

    def to_metadata(self) -> dict:
        """转为 block metadata，便于入库追溯解析路线。"""
        return {
            "pdf_kind": self.kind.value,
            "pdf_text_page_ratio": round(self.text_page_ratio, 3),
            "pdf_scanned_page_ratio": round(self.scanned_page_ratio, 3),
            "pdf_image_per_page": round(self.image_per_page, 3),
        }


def sample_pages(path: str | Path, max_pages: int = SAMPLE_PAGES_MAX) -> list[PageSample]:
    """均匀抽样文档页，返回每页的文本长度与图片数。

    用 pypdf 提取文本层（不渲染图片）；图片数用 page.images（懒加载，逐页 len 一次）。
    单页提取失败按空文本处理（该页会被计入 scanned 侧），不阻塞整档分类。
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    total = len(reader.pages)
    if total == 0:
        return []

    # 均匀取 max_pages 个索引：小文档全取，大文档跨页分布（含首页与末页）。
    if total <= max_pages:
        indices = list(range(total))
    else:
        step = (total - 1) / (max_pages - 1)
        indices = [round(i * step) for i in range(max_pages)]

    samples: list[PageSample] = []
    for idx in indices:
        page = reader.pages[idx]
        try:
            text_len = len((page.extract_text() or "").strip())
        except Exception:
            text_len = 0
        try:
            image_count = len(list(page.images))
        except Exception:
            image_count = 0
        samples.append(PageSample(page_number=idx + 1, text_len=text_len, image_count=image_count))
    return samples


def classify_pdf(path: str | Path) -> PdfClassification:
    """文档级分类：根据抽样页文本密度与图片密度判定 PdfKind。

    规则（与 TEXT_PAGE_MIN_CHARS 对齐）：
      1. 整档文本总量 < 页数 * SCANNED_MIN_CHARS_PER_PAGE → scanned（纯扫描件省成本）；
      2. 文本页占比 >= NATIVE_TEXT_RATIO 且图片密度 < NATIVE_IMAGE_PER_PAGE → native；
      3. 文本页占比 <= SCANNED_TEXT_RATIO → scanned；
      4. 其余 → mixed（文本与图表/扫描混杂）。
    """
    samples = sample_pages(path)
    if not samples:
        return PdfClassification(PdfKind.scanned, samples)

    total_text = sum(s.text_len for s in samples)
    text_ratio = sum(1 for s in samples if s.text_len >= TEXT_PAGE_MIN_CHARS) / len(samples)
    image_density = sum(s.image_count for s in samples) / len(samples)
    # 任一抽样页无文本层即视为含扫描页：逐页 pdfplumber+PaddleOCR 对这类页质量差，
    # 应交给 MinerU 整档解析（OCR 模式覆盖扫描页，布局模式保留文本页）。
    scanned_count = sum(1 for s in samples if s.text_len < TEXT_PAGE_MIN_CHARS)

    if total_text < len(samples) * SCANNED_MIN_CHARS_PER_PAGE:
        return PdfClassification(PdfKind.scanned, samples)
    if text_ratio <= SCANNED_TEXT_RATIO:
        return PdfClassification(PdfKind.scanned, samples)
    # 原生件：绝大多数页有文本 + 不含任何扫描页 + 图片稀疏（该条件下逐页快路质量已足够）。
    if (
        text_ratio >= NATIVE_TEXT_RATIO
        and scanned_count == 0
        and image_density < NATIVE_IMAGE_PER_PAGE
    ):
        return PdfClassification(PdfKind.native, samples)
    # 其余（含扫描页 / 图表密集 / 文本混杂）→ mixed，交给 MinerU auto。
    return PdfClassification(PdfKind.mixed, samples)
