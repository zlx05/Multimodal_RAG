"""一次性：把 data/document_registry.json 导入 MySQL documents 表。

用法（从仓库根目录）：
    python -m backend.app.db.migrate

幂等：Document 以 document_id 为主键，db.merge 按主键 upsert，
重复执行只会覆盖同名记录，不会重复插入。迁移后 JSON 文件保留作为备份。
"""

import json

from pathlib import Path

from ..core.config import DATA_DIR
from ..core.database import SessionLocal, init_db
from .models import Document

REGISTRY_PATH = DATA_DIR / "document_registry.json"


def migrate() -> int:
    init_db()
    if not REGISTRY_PATH.exists():
        print("[migrate] 未发现 document_registry.json，跳过")
        return 0

    records = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    count = 0
    with SessionLocal() as db:
        for document_id, rec in records.items():
            filename = str(rec.get("filename", ""))
            row = Document(
                document_id=document_id,
                filename=filename,
                source_path=str(rec.get("source_path", "")),
                collection_name=str(
                    rec.get("collection_name") or f"rag_{document_id}"
                ),
                topic_label=str(
                    rec.get("topic_label") or Path(filename).stem or "未命名资料"
                ),
                content_hash=str(rec.get("content_hash", "")),
                source_type=str(rec.get("source_type", "")),
                source_url=str(rec.get("source_url", "")),
                chunk_profile=str(rec.get("chunk_profile", "")),
                created_at=float(rec.get("created_at", 0.0)),
                legacy=False,
            )
            db.merge(row)
            count += 1
        db.commit()
    print(f"[migrate] 导入 {count} 条记录")
    return count


if __name__ == "__main__":
    raise SystemExit(migrate())
