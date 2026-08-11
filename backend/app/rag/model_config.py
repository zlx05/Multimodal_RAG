"""Embedding model configuration shared by the RAG pipeline and chunkers."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODEL_CANDIDATES = (
    PROJECT_ROOT / "models" / "models" / "AI-ModelScope--bge-small-zh-v1.5" / "snapshots" / "master",
    PROJECT_ROOT / "models" / "bge-small-zh-v1.5",
)
DEFAULT_MODEL = next(
    (str(path) for path in MODEL_CANDIDATES if path.exists()),
    str(MODEL_CANDIDATES[0]),
)


def _resolve_model_path(value: str) -> str:
    """Resolve a possibly-relative EMBEDDING_MODEL to an absolute path.

    `.env` keeps `EMBEDDING_MODEL=models/models/...` relative so it is
    portable across machines; sentence-transformers resolves relative model
    paths against the process CWD, so launching from `backend/` (instead of
    the repo root) silently made every collection unsearchable. Anchor it to
    PROJECT_ROOT instead (same trick as config._project_path).
    """
    path = Path(value)
    return str(path if path.is_absolute() else PROJECT_ROOT / path)


EMBEDDING_MODEL = _resolve_model_path(os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL))
