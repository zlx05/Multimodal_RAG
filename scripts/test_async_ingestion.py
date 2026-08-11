"""验证 Redis 异步增量入库全链路。

流程：
1. 创建任务（模拟 API 上传后写 Redis + 入队）
2. Worker 后台消费队列，执行 parse -> chunk -> embed -> index
3. 查询任务状态，验证 SUCCEEDED

用法:
    conda activate rag11
    python scripts/test_async_ingestion.py
"""

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.tasks.task_store import TaskStore
from backend.app.tasks.worker import IngestionWorker, enqueue_ingestion


def main():
    api_key = os.environ.get("LLM_API_KEY", "")

    # 1. 创建任务
    tasks = TaskStore()
    doc = PROJECT_ROOT / "data" / "数据结构完整复习笔记.md"
    task_id = tasks.create_task(
        document_id="doc_async_test",
        filename=doc.name,
        source_path=str(doc),
        collection_name="rag_async_test_v1",
        chunker="markdown",
        chunker_params={"min_chunk_size": 50},
    )
    print(f"创建任务: {task_id}")
    enqueue_ingestion(tasks, task_id)

    # 2. 启动 worker 处理这个任务（单次）
    worker = IngestionWorker(api_key=api_key)
    worker.process_task(task_id)

    # 3. 查询状态
    task = tasks.get_task(task_id)
    print(f"\n任务状态: {task['status']}")
    print(f"chunks: {task.get('chunks')}")
    print(f"collection: {task.get('collection_name')}")

    # 4. 验证检索
    from backend.app.rag.hybrid_pipeline import HybridRAGPipeline

    pipe = HybridRAGPipeline("rag_async_test_v1", api_key, rebuild=False)
    print("\n=== 异步入库后的检索验证 ===")
    for r in pipe.search("快速排序", top_k=2):
        c = pipe._chunk_pool_by_index(r["index"])
        print(f"  {r['score']:.4f} {r['origins']} | {c['content'][:40]}")

    print("\n✓ 异步增量入库全链路验证通过")


if __name__ == "__main__":
    main()
