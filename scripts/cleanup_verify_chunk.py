"""清理 verify_chunk 测试残留（用户已授权）。

删除范围（镜像 routes_org.delete_upload + 补 Redis task + 特殊文件名）：
- 上传源文件 data/uploads/doc_verify_e0af02.html（前缀与 document_id 不同，补删）
- 原始目录 data/original/doc_verify_e0af02.html（如有）
- 工作目录 data/.work/doc_482925e0d1a5
- Milvus collection：rag_verify_482925e0d1a5 与 rag_verify_chunk_482925e0d1a5（task/document 两处命名不一致）
- uploads 行 up_fc6f711c75c34268b7543dd93109、documents 行 doc_482925e0d1a5
- Redis task rag:task:task_9210ca8fa3ef（PENDING 残留，队列已空）
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.database import SessionLocal
from backend.app.api.routes_org import UPLOAD_DIR, RAG_ORIGINAL_DIR, RAG_WORK_DIR

DOC_ID = "doc_482925e0d1a5"
UPLOAD_ID = "up_fc6f711c75c34268b7543dd93109"
TASK_ID = "task_9210ca8fa3ef"
SOURCE_FILE = "doc_verify_e0af02.html"
COLLECTIONS = ["rag_verify_482925e0d1a5", "rag_verify_chunk_482925e0d1a5"]


def main() -> None:
    # 1) 文件
    for base, label in ((UPLOAD_DIR, "uploads"), (RAG_ORIGINAL_DIR, "original")):
        f = Path(base) / SOURCE_FILE
        if f.exists():
            f.unlink()
            print(f"删文件 {label}: {f}")
    for base in (UPLOAD_DIR, RAG_ORIGINAL_DIR):
        for f in Path(base).glob(f"{DOC_ID}.*"):
            f.unlink(missing_ok=True)
            print(f"删文件 {base.name}: {f}")
    wd = Path(RAG_WORK_DIR) / DOC_ID
    if wd.exists():
        import shutil

        shutil.rmtree(wd, ignore_errors=True)
        print(f"删工作目录: {wd}")

    # 2) Milvus collection
    try:
        from pymilvus import connections, utility

        connections.connect(alias="default", host="127.0.0.1", port="19530")
        for coll in COLLECTIONS:
            if utility.has_collection(coll):
                utility.drop_collection(coll)
                print(f"删 Milvus collection: {coll}")
    except Exception as exc:
        print(f"Milvus 清理异常（可忽略）: {exc}")

    # 3) DB 行
    db = SessionLocal()
    try:
        from sqlalchemy import text

        for stmt, args, label in (
            ("DELETE FROM uploads WHERE id=:id", {"id": UPLOAD_ID}, "uploads"),
            ("DELETE FROM documents WHERE document_id=:id", {"id": DOC_ID}, "documents"),
        ):
            r = db.execute(text(stmt), args)
            if r.rowcount:
                print(f"删 DB {label}: {r.rowcount} 行")
        db.commit()
    finally:
        db.close()

    # 4) Redis task 残留
    import redis

    r = redis.Redis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)
    key = f"rag:task:{TASK_ID}"
    if r.exists(key):
        r.delete(key)
        print(f"删 Redis task: {key}")

    print("清理完成")


if __name__ == "__main__":
    main()
