"""OpenAI-compatible vision client for image understanding during ingestion."""

from __future__ import annotations

import base64
import logging
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.config import (
    VISION_LLM_API_KEY,
    VISION_LLM_BASE_URL,
    VISION_LLM_MODEL,
    VISION_LLM_TIMEOUT,
)

logger = logging.getLogger(__name__)


DEFAULT_VISION_PROMPT = """你是学习资料解析器。请分析这张图片，输出适合知识库检索的中文结构化文字。
必须尽量保留题目、定义、数字、单位、上下标、公式和表格关系。
公式请尽量使用 LaTeX；表格请按“表头：...；行：...”的形式表达。
如果图片主要是手写内容，请在无法确定的字符处使用 [不确定]，不要凭空补全。
只输出解析后的内容，不要输出分析过程。
"""


@dataclass
class VisionAnalysis:
    """A normalized result returned by a vision-capable provider."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class VisionAnalyzer:
    """Analyze local images through an OpenAI-compatible vision endpoint.

    The analyzer is optional. Missing credentials or provider failures return
    no result so OCR-only ingestion can continue.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        client: Any | None = None,
    ):
        self.api_key = (api_key if api_key is not None else VISION_LLM_API_KEY).strip()
        self.base_url = (base_url if base_url is not None else VISION_LLM_BASE_URL).strip()
        self.model = (model if model is not None else VISION_LLM_MODEL).strip()
        self.timeout = timeout if timeout is not None else VISION_LLM_TIMEOUT
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    @property
    def client(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    def analyze(
        self,
        image_path: str | Path,
        ocr_text: str = "",
        prompt: str = DEFAULT_VISION_PROMPT,
    ) -> VisionAnalysis | None:
        """Return a text representation and preserve provider metadata."""
        if not self.enabled:
            return None

        path = Path(image_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        reference = (
            "\n以下是 OCR 初步结果，请纠正明显错误但不要忽略图片内容：\n"
            + ocr_text
            if ocr_text.strip()
            else ""
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt + reference},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{encoded}",
                                    "detail": "high",
                                },
                            },
                        ],
                    }
                ],
                temperature=0.1,
                max_tokens=2048,
            )
            text = (response.choices[0].message.content or "").strip()
            if not text:
                return None
            return VisionAnalysis(
                text=text,
                metadata={
                    "vision_model": self.model,
                    "vision_base_url": self.base_url,
                },
            )
        except Exception as exc:  # provider failure must not lose OCR results
            logger.warning("Vision analysis failed for %s: %s", path, exc)
            return None
