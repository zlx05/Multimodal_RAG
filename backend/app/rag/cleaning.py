"""入库前清洗层：字符归一化 + 页眉页脚/水印剥离 + OCR 噪声过滤。

RAG 检索质量的第一道闸门是数据质量（业界共识：脏文本污染向量与 BM25）。
本模块在解析之后、Profile 选型之前运行（见 worker._parse）：
1. clean_text：折叠多余空白、修连字/断行、去控制字符；
2. 页眉页脚/水印剥离：正则 + 跨块频次启发（同一行在多块重复出现 -> 页眉页脚）；
3. OCR 噪声：过滤重复标点分隔线；
4. 安全兜底：任何块清洗后为空 -> 回退原文，绝不丢块。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from .blocks import DocumentBlock

# ---------------------------------------------------------------- 字符归一化

# 常见 Unicode 连字 -> ASCII 拆字（PDF/扫描常见）
_LIGATURES = {
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬀ": "ff",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
}

# 行尾断连字符：exam-\nple -> example（仅拉丁字母，中文无此写法）
_HYPHEN_LINE_BREAK = re.compile(r"([A-Za-z])-([ \t]*)\n([A-Za-z])")

# 控制/不可打印字符（保留 \n \t）
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# 连续空白（行内，不动换行）；1+ 也折叠（单个 Tab/全角空格也要归一化）
_RUNNING_SPACE = re.compile(r"[ \t　]+")

# 全角空格 -> 半角
_FULLWIDTH_SPACE = "　"


def clean_text(text: str, *, preserve_whitespace: bool = False) -> str:
    """字符级归一化。preserve_whitespace=True 时保留连续空白（代码块）。"""
    if not text:
        return text
    for src, dst in _LIGATURES.items():
        text = text.replace(src, dst)
    text = text.replace("\f", "")
    text = _CONTROL_CHARS.sub("", text)
    if not preserve_whitespace:
        text = _HYPHEN_LINE_BREAK.sub(r"\1\3", text)
        text = text.replace(_FULLWIDTH_SPACE, " ")
        text = _RUNNING_SPACE.sub(" ", text)
    return text


# ---------------------------------------------------------------- 页眉页脚/水印

_PAGE_NUMBER_RE = re.compile(
    r"^\s*第?\s*(\d{1,4})\s*页\s*(?:[/of共]\s*\d*\s*页?)?\s*[，,。.．]?\s*$"
)
_PAGE_OF_RE = re.compile(r"^\s*page\s+\d{1,4}\s+of\s+\d{1,4}\s*$", re.I)
_PURE_DIGITS_RE = re.compile(r"^\s*\d{1,4}\s*$")
_WATERMARK_RE = re.compile(
    r"^\s*("
    r"机密|保密|内部资料|内部文件|草稿|草案|版权所有|版权声明|"
    r"Copyright|Confidential|Draft|All rights reserved|"
    r"未经.*许可.*不得|仅供.*使用|请勿.*外传"
    r")\s*[。.]?\s*$",
    re.I,
)
_DIVIDER_RE = re.compile(r"^\s*[-*_=~]{3,}\s*$")
_MAX_BOILERPLATE_LEN = 60


def _is_boilerplate_line(line: str) -> bool:
    """单行是否像页眉页脚/水印/分隔线。"""
    s = line.strip()
    if not s or len(s) > _MAX_BOILERPLATE_LEN:
        return False
    return bool(
        _PAGE_NUMBER_RE.match(line)
        or _PAGE_OF_RE.match(line)
        or _WATERMARK_RE.match(line)
        or _DIVIDER_RE.match(line)
        or _PURE_DIGITS_RE.match(line)
    )


def _frequency_boilerplate(blocks: Iterable[DocumentBlock], threshold_ratio: float = 0.6) -> set[str]:
    """跨块频次启发：同一源类型里，在 >= threshold_ratio 的块中重复出现的短行

    视为页眉页脚剥离（页码/章节名格式五花八门时正则兜不住）。
    """
    blocks_by_source: dict[str, list[DocumentBlock]] = {}
    for block in blocks:
        blocks_by_source.setdefault(block.source_type, []).append(block)

    frequent: set[str] = set()
    for group in blocks_by_source.values():
        if len(group) < 3:
            continue
        counts: Counter[str] = Counter()
        for block in group:
            for line in block.text.splitlines():
                s = line.strip()
                if s and len(s) <= _MAX_BOILERPLATE_LEN:
                    counts[s] += 1
        threshold = max(2, int(len(group) * threshold_ratio))
        for line, count in counts.items():
            if count >= threshold:
                frequent.add(line)
    return frequent


# ---------------------------------------------------------------- 主入口

def clean_blocks(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """清洗整个块列表（就地修改 text，返回同一列表便于链式调用）。

    - 代码块（content_type="code"）只做最小清洗：去控制字符/连字，保留空白。
    - 空块清洗后回退原文，绝不丢块。
    """
    frequent = _frequency_boilerplate(blocks)
    for block in blocks:
        original = block.text or ""
        text = clean_text(original, preserve_whitespace=block.content_type == "code")

        # 页眉页脚剥离仅对多行文本有意义；单行正文块不猜。
        if "\n" in text:
            kept_lines = [
                line
                for line in text.split("\n")
                if not (line.strip() and (line.strip() in frequent or _is_boilerplate_line(line)))
            ]
            text = "\n".join(kept_lines).strip("\n")

        if not text.strip():
            text = original  # 兜底：绝不因为清洗把整块内容弄丢
        block.text = text
    return blocks
