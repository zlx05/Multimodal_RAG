"""Safe URLs for original documents and extracted document assets."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import quote

from ..core.config import DATA_DIR

DATA_ROOT = DATA_DIR.resolve()


def original_url(document_id: str) -> str:
    return f"/api/v1/documents/{quote(document_id, safe='')}/original"


def asset_url(document_id: str, image_path: str | None) -> str | None:
    if not image_path:
        return None
    try:
        path = Path(image_path).resolve()
        relative = path.relative_to(DATA_ROOT).as_posix()
    except (OSError, ValueError):
        return None
    # Every extracted asset is stored under a document-specific path. This
    # prevents an arbitrary path from becoming a file-serving URL.
    if document_id not in path.parts and not path.name.startswith(document_id):
        return None
    return f"/api/v1/documents/{quote(document_id, safe='')}/assets/{quote(relative, safe='/')}"


def media_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
