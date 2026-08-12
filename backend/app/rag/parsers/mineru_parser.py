"""MinerU PDF 全管线适配器：middle.json → DocumentBlock。

MinerU（opendatalab）把 PDF 解析为 Markdown + middle.json（带页码的布局块），
专门解决扫描件（OCR）与混合图表件（布局 + 表格 + 公式 + 图片 caption）的质量问题。

集成方式：
- 惰性 import + 子进程调用 `mineru -p in.pdf -o outdir -b pipeline -m {mode}`。
  mineru 内部多进程 + 显存管理，放子进程隔离，不污染 worker 常驻进程。
  设备通过环境变量 MINERU_DEVICE_MODE 指定。backend 必须显式传 pipeline——
  3.4.x 默认 hybrid-engine（VLM），8G 显存扛不住且需额外依赖。
- 解析 middle.json 的 pdf_info[].para_blocks。para_block 是嵌套结构：
    type: title|text|list|table|image|chart|interline_equation|code|...
    文本在 lines[].spans[].content；表格 html 在 table_body 子块 span.html；
    图片路径在 image_body 子块 span.image_path（扁平 <sha256>.jpg，落在 images/ 目录）。
  映射为 DocumentBlock：
    TITLE              -> heading（heading_path 含自身标题）
    TEXT / LIST / INDEX-> text
    TABLE              -> table（metadata["table"] 行结构 + markdown 文本 + caption）
    IMAGE / CHART      -> image_description（保留图片路径与 caption）
    INTERLINE_EQUATION -> formula（$$...$$ LaTeX 文本）
- mineru 不可用 / 超时 / 空结果 -> 抛 MineruUnavailable，上层（pdf_parser）回退现有路线。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterator

from .base import BaseParser
from ..blocks import DocumentBlock
from ..vision import VisionAnalyzer

# 从 pdf_parser 复用表格序列化，保持 table 块文本格式一致
from .pdf_parser import _serialize_table

# mineru ContentType / BlockType 的小写取值（见 mineru/utils/enum_class.py）
_TYPE_TEXT = "text"
_TYPE_INLINE_EQ = "inline_equation"
_TYPE_INTERLINE_EQ = "interline_equation"
_TYPE_IMAGE = "image"
_TYPE_TABLE = "table"
_TYPE_CHART = "chart"


logger = logging.getLogger(__name__)


class MineruUnavailable(RuntimeError):
    """MinerU 未启用 / 未安装 / 调用失败，上层应回退其他解析路线。"""


def _subprocess_bin() -> str:
    """返回 mineru 可执行文件路径。

    Windows 下 console_scripts 是 <venv>/Scripts/mineru.exe，而 Scripts 目录
    常不在 PATH（pip 装包时会提示），所以优先查当前解释器的 Scripts。
    """
    import sys

    if sys.platform == "win32":
        candidate = Path(sys.prefix) / "Scripts" / "mineru.exe"
        if candidate.exists():
            return str(candidate)
    return shutil.which("mineru") or "mineru"


def _mineru_available() -> bool:
    """mineru 可执行文件是否可解析（venv Scripts 或 PATH）。"""
    import sys

    if sys.platform == "win32":
        if (Path(sys.prefix) / "Scripts" / "mineru.exe").exists():
            return True
    return shutil.which("mineru") is not None


def _find_middle_json(output_dir: Path, stem: str) -> Path | None:
    """在 mineru 输出目录递归找 {stem}_middle.json（不同目录名兼容）。"""
    if not output_dir.exists():
        return None
    expected = output_dir / f"{stem}_middle.json"
    if expected.exists():
        return expected
    for candidate in output_dir.rglob(f"{stem}_middle.json"):
        return candidate
    matches = list(output_dir.rglob("*_middle.json"))
    return matches[0] if matches else None


# ---------------------------------------------------------------- 嵌套块解析


def _iter_spans(block: dict) -> Iterator[dict]:
    """递归遍历 lines/blocks 里的所有 span（mineru 视觉类块的统一查找方式）。"""
    for line in block.get("lines") or []:
        for span in line.get("spans") or []:
            yield span
    for sub in block.get("blocks") or []:
        yield from _iter_spans(sub)


def _block_text(block: dict) -> str:
    """合并文本块内容：优先顶层 content（个别版本直带），否则按 span 类型拼接。

    text span -> 原文本；inline_equation -> $...$；interline_equation -> $$...$$。
    """
    top = str(block.get("content") or "").strip()
    if top:
        return top
    parts: list[str] = []
    for span in _iter_spans(block):
        stype = str(span.get("type") or "").lower()
        text = str(span.get("content") or "").strip()
        if not text:
            continue
        if stype == _TYPE_TEXT:
            parts.append(text)
        elif stype == _TYPE_INLINE_EQ:
            parts.append(f"${text}$")
        elif stype == _TYPE_INTERLINE_EQ:
            parts.append(f"$$\n{text}\n$$")
    return "\n".join(parts)


def _collect_latex(block: dict) -> list[str]:
    """收集公式块内所有 interline_equation span 的 LaTeX 文本（不包定界符）。"""
    return [
        str(span.get("content") or "").strip()
        for span in _iter_spans(block)
        if str(span.get("type") or "").lower() == _TYPE_INTERLINE_EQ
        and str(span.get("content") or "").strip()
    ]


def _find_span(block: dict, body_type: str, span_type: str) -> dict | None:
    """在指定 body 子块（image_body/table_body/chart_body）里找第一个目标 span。"""
    for sub in block.get("blocks") or []:
        if str(sub.get("type") or "").lower() != body_type:
            continue
        for span in _iter_spans(sub):
            if str(span.get("type") or "").lower() == span_type:
                return span
    return None


def _caption_text(block: dict, caption_type: str) -> str:
    """收集 caption 子块（image_caption/table_caption）的标题文本。"""
    parts = []
    for sub in block.get("blocks") or []:
        if str(sub.get("type") or "").lower() != caption_type:
            continue
        text = _block_text(sub)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _resolve_image(image_dir: Path, image_path: str) -> str | None:
    """把 middle.json 里的图片路径解析为磁盘绝对路径。

    mineru 存的是扁平 <sha256>.jpg，落在输出目录 images/ 下；容错 images/ 缺失
    时退到 image_dir 本身。文件不存在也返回期望路径（供排查），不抛异常。
    """
    if not image_path:
        return None
    p = Path(image_path)
    if p.is_absolute():
        return str(p)
    candidates = [image_dir / "images" / p, image_dir / p]
    for c in candidates:
        if c.exists():
            return str(c)
    return str(candidates[0])


def _cell_to_str(cell) -> str:
    """表格单元格可能是 dict/list/str，统一转字符串。"""
    if cell is None:
        return ""
    if isinstance(cell, str):
        return cell
    if isinstance(cell, list):
        return " ".join(_cell_to_str(item) for item in cell if item is not None)
    if isinstance(cell, dict):
        for key in ("text", "content", "html"):
            if cell.get(key):
                return str(cell[key])
        return " ".join(str(v) for v in cell.values() if v is not None)
    return str(cell)


def _table_rows_from_html(html: str) -> list[list[str]] | None:
    """从 MinerU 表格 HTML（<tr>/<td>）提取行结构。"""
    if "<tr" not in (html or "").lower():
        return None
    import re

    rows: list[list[str]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)
        if cells:
            rows.append([_cell_to_str(c) for c in cells])
    return rows or None


class MineruParser(BaseParser):
    """把 PDF 交给 Mineru 子进程解析，再把 middle.json 映射为 DocumentBlock。"""

    source_type = "pdf"

    def __init__(
        self,
        document_id: str,
        work_dir: str | None = None,
        mineru_mode: str = "auto",
        timeout: int = 1200,
        device_mode: str | None = None,
        model_source: str | None = None,
        backend: str = "pipeline",
        vision_analyzer: VisionAnalyzer | None = None,
    ):
        super().__init__(document_id)
        self.work_dir = Path(work_dir or os.getenv("RAG_WORK_DIR", tempfile.gettempdir()))
        self.mineru_mode = mineru_mode  # auto | ocr | txt
        self.timeout = timeout
        self.device_mode = device_mode
        self.model_source = model_source
        # 默认 pipeline（布局+表格+公式+OCR）；hybrid-engine 需 VLM，显存要求高。
        # 3.4.x 默认 hybrid-engine，不显式指定会尝试加载 VLM 模型。
        self.backend = backend
        # 多模态 LLM：MinerU 图片块只有 caption，像素内容靠 vision 描述后进库可召回。
        self.vision_analyzer = vision_analyzer

    # ---------------------------------------------------------------- 主流程

    def parse(self, path: str | Path) -> list[DocumentBlock]:
        if not _mineru_available():
            raise MineruUnavailable("mineru 未安装，无法解析扫描/混合 PDF")

        pdf_path = Path(path)
        # {work_dir}/{document_id}/mineru/：document_id 作路径段，middle.json 里解析出的图片
        # （{stem}/images/*.jpg）落在这个目录下，asset_url 的安全校验才能生成可展示的 URL。
        output_dir = self.work_dir / self.document_id / "mineru"
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        self._run_mineru(pdf_path, output_dir)
        middle = _find_middle_json(output_dir, pdf_path.stem)
        if middle is None:
            raise MineruUnavailable(f"mineru 未产出 middle.json（{pdf_path.name}）")

        # 图片相对路径基于 middle.json 所在目录（{stem}/images/）解析
        blocks = list(self._blocks_from_middle_json(middle, middle.parent))
        if not blocks:
            raise MineruUnavailable(f"mineru 解析结果为空（{pdf_path.name}）")
        return blocks

    def _run_mineru(self, pdf_path: Path, output_dir: Path) -> None:
        """子进程调用 mineru CLI。超时/非零退出抛 MineruUnavailable。"""
        cmd = [
            _subprocess_bin(),
            "-p",
            str(pdf_path),
            "-o",
            str(output_dir),
            "-b",
            self.backend,
            "-m",
            self.mineru_mode,
        ]
        env = dict(os.environ)
        if self.device_mode:
            env["MINERU_DEVICE_MODE"] = self.device_mode
        if self.model_source:
            env["MINERU_MODEL_SOURCE"] = self.model_source
        env.setdefault("MINERU_LOG_LEVEL", "INFO")
        try:
            proc = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            raise MineruUnavailable(
                f"mineru 超时（>{self.timeout}s，{pdf_path.name}）"
            ) from None
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = " | ".join(detail[-3:]) if detail else "无输出"
            raise MineruUnavailable(
                f"mineru 退出码 {proc.returncode}（{pdf_path.name}）：{tail}"
            )

    # ---------------------------------------------------------------- 映射

    def _blocks_from_middle_json(
        self, middle_path: Path, image_dir: Path
    ) -> Iterator[DocumentBlock]:
        with middle_path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
        pdf_info = payload.get("pdf_info") or []
        for page_info in pdf_info:
            page_idx = int(page_info.get("page_idx", 0))
            page_number = page_idx + 1
            for block in page_info.get("para_blocks") or []:
                mapped = self._map_block(block, page_number, image_dir)
                if mapped is not None:
                    yield mapped

    def _map_block(
        self, block: dict, page_number: int, image_dir: Path
    ) -> DocumentBlock | None:
        block_type = str(block.get("type") or "").upper()
        bbox = block.get("bbox")
        bbox_tuple = (
            tuple(float(v) for v in bbox)
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4
            else None
        )
        meta = {"mineru_engine": True, "mineru_type": block_type.lower()}

        if block_type == "TITLE":
            text = _block_text(block)
            if text:
                return self._block(
                    text,
                    content_type="heading",
                    heading_path=[text],
                    page_number=page_number,
                    bbox=bbox_tuple,
                    metadata=meta,
                )

        if block_type in {"TEXT", "LIST", "INDEX"}:
            text = _block_text(block)
            if text:
                return self._block(
                    text,
                    content_type="text",
                    page_number=page_number,
                    bbox=bbox_tuple,
                    metadata=meta,
                )

        if block_type == "TABLE":
            span = _find_span(block, "table_body", _TYPE_TABLE) or {}
            rows = _table_rows_from_html(str(span.get("html") or ""))
            caption = _caption_text(block, "table_caption")
            text = _serialize_table(rows) if rows else ""
            if caption:
                text = f"{caption}\n\n{text}".strip()
            if not text:
                text = "表格（mineru 提取，请查看原始文件）"
            return self._block(
                text,
                content_type="table",
                page_number=page_number,
                bbox=bbox_tuple,
                metadata={
                    **meta,
                    "table": rows or [],
                    "table_caption": caption or "",
                },
            )

        if block_type in {"IMAGE", "CHART"}:
            body_type = f"{block_type.lower()}_body"
            span = _find_span(block, body_type, block_type.lower()) or {}
            image_path = _resolve_image(image_dir, str(span.get("image_path") or ""))
            caption = (
                _caption_text(block, f"{block_type.lower()}_caption")
                or str(block.get("content") or "").strip()
                or "图片（mineru 提取，请查看原始文件）"
            )
            vision_meta: dict[str, Any] = {}
            # 图片内容可召回：MinerU 只给 caption，像素内容交给多模态 LLM 描述后
            # 追加进块文本（vision 不可用/失败时保留 caption，不阻塞解析）。
            if (
                self.vision_analyzer is not None
                and image_path
                and Path(image_path).is_file()
            ):
                try:
                    vision_result = self.vision_analyzer.analyze(image_path)
                except Exception as exc:
                    logger.warning("mineru 图片 vision 失败 %s: %s", image_path, exc)
                    vision_result = None
                if vision_result:
                    vision_meta["vision_description"] = vision_result.text
                    vision_meta.update(vision_result.metadata)
                    caption = f"{caption}\n\n{vision_result.text.strip()}".strip()
            return self._block(
                caption,
                content_type="image_description",
                image_path=image_path,
                page_number=page_number,
                bbox=bbox_tuple,
                metadata={**meta, "mineru_image_path": image_path, **vision_meta},
            )

        if block_type in {"INTERLINE_EQUATION", "EQUATION"}:
            text = _block_text(block)
            latex = _collect_latex(block)
            if text:
                return self._block(
                    text,
                    content_type="formula",
                    page_number=page_number,
                    bbox=bbox_tuple,
                    metadata={**meta, "formulas_latex": latex},
                )

        if block_type == "CODE":
            text = _block_text(block)
            if text:
                return self._block(
                    text,
                    content_type="code",
                    page_number=page_number,
                    bbox=bbox_tuple,
                    metadata=meta,
                )

        return None  # 其他类型（页眉页脚/废弃块/正文块等）跳过


def create_mineru_parser(document_id: str, **kwargs) -> MineruParser:
    """工厂，与现有 create_parser 风格一致。"""
    return MineruParser(document_id, **kwargs)
