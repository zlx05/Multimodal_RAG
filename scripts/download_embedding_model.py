"""Download the Chinese BGE embedding model to the configured model cache."""

import os
from pathlib import Path

from modelscope import snapshot_download


MODEL_ID = "AI-ModelScope/bge-small-zh-v1.5"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_CACHE = Path(os.getenv("MODEL_CACHE", PROJECT_ROOT / "models"))

path = snapshot_download(MODEL_ID, cache_dir=str(MODEL_CACHE))
print(f"Embedding model is ready: {path}")
