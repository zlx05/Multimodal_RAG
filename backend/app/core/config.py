"""统一配置读取。

所有环境变量集中在这里读取，避免散落在各个模块。
"""

import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

# 项目根目录 = backend/app/core 的上一级 x3
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

# 兼容项目早期使用的 `llm-api:...` 配置格式（见 .env）
if not os.getenv("LLM_API_KEY"):
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("llm-api:"):
                os.environ["LLM_API_KEY"] = line.split(":", 1)[1].strip()
                break


def _get(key: str, default: str) -> str:
    return os.getenv(key, default)


def _project_path(value: str, default: Path) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else PROJECT_ROOT / path)


# LLM
LLM_API_KEY = _get("LLM_API_KEY", "")
LLM_BASE_URL = _get("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = _get("LLM_MODEL", "gpt-5.6-luna")
CONTEXTUAL_RETRIEVAL_ENABLED = _get("CONTEXTUAL_RETRIEVAL_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
CONTEXTUAL_RETRIEVAL_MODEL = _get("CONTEXTUAL_RETRIEVAL_MODEL", LLM_MODEL)

# Vision model used during ingestion. It is intentionally separate from the
# final text-generation model so the provider can be switched independently.
VISION_LLM_API_KEY = _get("VISION_LLM_API_KEY", "")
VISION_LLM_BASE_URL = _get("VISION_LLM_BASE_URL", "")
VISION_LLM_MODEL = _get("VISION_LLM_MODEL", "")
VISION_LLM_TIMEOUT = float(_get("VISION_LLM_TIMEOUT", "90"))

# When final generation and visual analysis use the same provider, allow the
# final client to reuse the vision key without duplicating the secret in .env.
if LLM_API_KEY in {"", "ollama", "${VISION_LLM_API_KEY}"} and not LLM_BASE_URL.startswith(
    ("http://127.0.0.1:11434", "http://localhost:11434")
):
    LLM_API_KEY = VISION_LLM_API_KEY

LLM_LUNA_API_KEY = _get("LLM_LUNA_API_KEY", "")
LLM_LUNA_BASE_URL = _get("LLM_LUNA_BASE_URL", LLM_BASE_URL)
LLM_DEEPSEEK_FLASH_API_KEY = _get("LLM_DEEPSEEK_FLASH_API_KEY", "")
LLM_DEEPSEEK_FLASH_BASE_URL = _get("LLM_DEEPSEEK_FLASH_BASE_URL", LLM_BASE_URL)


def _is_configured(value: str) -> bool:
    return bool(value and not value.startswith(("replace-with", "填写", "${")))


def get_model_config(model_id: str | None = None) -> dict[str, str | bool]:
    """Resolve a public model id to server-side provider credentials."""
    selected = (model_id or LLM_MODEL).strip()
    configs = {
        "gpt-5.6-terra": {
            "id": "gpt-5.6-terra",
            "label": "GPT-5.6 Terra",
            "description": "适合复杂资料的完整推理与溯源回答",
            "base_url": LLM_BASE_URL,
            "api_key": LLM_API_KEY,
        },
        "gpt-5.6-luna": {
            "id": "gpt-5.6-luna",
            "label": "GPT-5.6 Luna",
            "description": "更轻量的日常复习问答",
            "base_url": LLM_LUNA_BASE_URL,
            "api_key": LLM_LUNA_API_KEY,
        },
        "deepseek-v4-flash": {
            "id": "deepseek-v4-flash",
            "label": "DeepSeek V4 Flash",
            "description": "快速处理短问题和知识点回顾",
            "base_url": LLM_DEEPSEEK_FLASH_BASE_URL,
            "api_key": LLM_DEEPSEEK_FLASH_API_KEY,
        },
    }
    if selected not in configs:
        raise ValueError(f"不支持的问答模型: {selected}")
    config = configs[selected]
    config["ready"] = _is_configured(str(config["api_key"])) and bool(config["base_url"])
    return config


def list_model_configs() -> list[dict[str, str | bool]]:
    """Return safe model metadata. Credentials are intentionally omitted."""
    result = []
    for model_id in ("gpt-5.6-terra", "gpt-5.6-luna", "deepseek-v4-flash"):
        config = get_model_config(model_id)
        result.append(
            {
                "id": config["id"],
                "label": config["label"],
                "description": config["description"],
                "ready": config["ready"],
                "default": model_id == LLM_MODEL,
            }
        )
    return result
FORMULA_RECOGNITION_ENABLED = _get("FORMULA_RECOGNITION_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
FORMULA_RECOGNITION_MODEL = _get("FORMULA_RECOGNITION_MODEL", "PP-FormulaNet_plus-M")
OCR_DEVICE = _get("OCR_DEVICE", "cpu")

# 上传校验 agent 开关（Phase 2）。开启时每次上传先审核内容，通过才入库可检索。
UPLOAD_REVIEW_ENABLED = _get("UPLOAD_REVIEW_ENABLED", "true").lower() in {
    "1", "true", "yes", "on"
}
# 检索前查询改写开关（Phase 3）。开启时 /chat/agent 先用 LLM 把口语化问题改写成
# 检索友好表述再路由与检索；失败回退原问题，不影响链路。
QUERY_REWRITE_ENABLED = _get("QUERY_REWRITE_ENABLED", "true").lower() in {
    "1", "true", "yes", "on"
}
# 双路召回开关（Phase 2.2）。改写后的问题为主路，原问题作为副路并行召回再按
# (collection, chunk) 取高分融合，避免改写丢失原问题的关键词；改写未变化时副路自动跳过。
DUAL_RECALL_ENABLED = _get("DUAL_RECALL_ENABLED", "true").lower() in {
    "1", "true", "yes", "on"
}
# 问题扩展开关（Phase 4）。开启时从改写后的问题用 LLM 生成补充检索子问题（broad 问题
# 如「go语言怎么学」→ 具体子主题「go语言的数据类型」等），作为额外检索路与主路融合，
# 避免 broad 问题漏掉具体子主题。默认关——先评估收益再决定是否默认开。
QUERY_EXPANSION_ENABLED = _get("QUERY_EXPANSION_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
FORMULA_RECOGNITION_DEVICE = _get("FORMULA_RECOGNITION_DEVICE", OCR_DEVICE)

# Milvus
MILVUS_HOST = _get("MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = _get("MILVUS_PORT", "19530")

# Redis
REDIS_URL = _get("REDIS_URL", "redis://127.0.0.1:6379/0")

# MySQL（元数据 / 会话 / 记忆）。值由 docker-compose 从 .env 注入。
MYSQL_HOST = _get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = _get("MYSQL_PORT", "3306")
MYSQL_DATABASE = _get("MYSQL_DATABASE", "rag")
MYSQL_USER = _get("MYSQL_USER", "rag")
MYSQL_PASSWORD = _get("MYSQL_PASSWORD", "")
MYSQL_CHARSET = _get("MYSQL_CHARSET", "utf8mb4")
DATABASE_URL = (
    f"mysql+pymysql://{MYSQL_USER}:{quote_plus(MYSQL_PASSWORD)}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset={MYSQL_CHARSET}"
)

# 鉴权（JWT）。SECRET_KEY 必须设置强随机值；未设置时用不安全默认值并打印警告，
# 仅在本地开发可接受，生产请务必在 .env 配置。
SECRET_KEY = _get("JWT_SECRET_KEY", "dev-insecure-change-me")
if SECRET_KEY == "dev-insecure-change-me":
    print("[config] 警告：JWT_SECRET_KEY 未设置，使用不安全默认值，请在 .env 配置")
ACCESS_TOKEN_EXPIRE_MINUTES = int(_get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))  # 7 天
SETUP_TOKEN_EXPIRE_MINUTES = int(_get("JWT_SETUP_TOKEN_EXPIRE_MINUTES", "15"))

# 数据目录
DATA_DIR = PROJECT_ROOT / "data"

# OCR 工作目录（PDF 页渲染、图片临时文件）
RAG_WORK_DIR = _project_path(
    _get("RAG_WORK_DIR", str(PROJECT_ROOT / "data" / ".work")),
    PROJECT_ROOT / "data" / ".work",
)
RAG_ORIGINAL_DIR = _project_path(
    _get("RAG_ORIGINAL_DIR", str(DATA_DIR / "original")),
    DATA_DIR / "original",
)

# Keep PaddleX model downloads under the repository's local model directory.
PADDLE_PDX_CACHE_HOME = _project_path(
    _get("PADDLE_PDX_CACHE_HOME", str(PROJECT_ROOT / "models" / "paddlex")),
    PROJECT_ROOT / "models" / "paddlex",
)
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", PADDLE_PDX_CACHE_HOME)
