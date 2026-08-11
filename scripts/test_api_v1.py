"""验证 /api/v1 完整流程：上传 -> 任务 -> Worker 处理 -> 检索问答。

用法:
    conda activate rag11
    python scripts/test_api_v1.py
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.tasks.worker import IngestionWorker


def main():
    api_key = os.environ.get("LLM_API_KEY", "")
    client = TestClient(app)

    # 1. 上传一个 Markdown 文件
    doc = PROJECT_ROOT / "data" / "数据结构完整复习笔记.md"
    print("=== 1. 上传文档 ===")
    with open(doc, "rb") as f:
        resp = client.post(
            "/api/v1/documents",
            files={"file": (doc.name, f, "text/markdown")},
        )
    data = resp.json()
    print("  响应:", data)
    task_id = data["task_id"]
    collection = data["document_id"]

    # 2. 任务初始状态
    print("\n=== 2. 初始任务状态 ===")
    status = client.get(f"/api/v1/tasks/{task_id}").json()
    print(f"  status={status['status']} stage={status['stage']}")

    # 3. 手动驱动 Worker 处理这个任务（模拟后台 worker）
    print("\n=== 3. Worker 处理 ===")
    worker = IngestionWorker(api_key=api_key)
    worker.process_task(task_id)

    final = client.get(f"/api/v1/tasks/{task_id}").json()
    print(f"  处理后 status={final['status']} chunks={final.get('chunks')}")

    # 4. 用上传文档的 collection 做检索
    real_collection = f"rag_{collection}"
    print(f"\n=== 4. 检索 (collection={real_collection}) ===")
    search_resp = client.post(
        "/api/v1/retrieval/search",
        json={"collection": real_collection, "question": "快速排序的时间复杂度", "top_k": 3},
    )
    search_data = search_resp.json()
    for r in search_data["results"][:3]:
        print(f"  {r['score']:.4f} {r['origins']} | {r['text'][:35]} | src={r['filename']}")

    # 5. 问答
    print("\n=== 5. 问答 (chat/ask) ===")
    chat_resp = client.post(
        "/api/v1/chat/ask",
        json={"collection": real_collection, "question": "堆排序的时间复杂度是多少？", "top_k": 3},
    )
    chat_data = chat_resp.json()
    print(f"  answer: {chat_data['answer'][:120]}...")
    print(f"  sources: {len(chat_data['sources'])} 个来源")
    if chat_data["sources"]:
        s = chat_data["sources"][0]
        print(f"  首个来源: {s['filename']} 页{s.get('page')} {s['heading_path']}")

    print("\n✓ /api/v1 完整流程验证通过")


if __name__ == "__main__":
    main()
