"""异步任务模块：Redis 任务队列 + 后台 Worker。"""

from .task_store import TaskStore

__all__ = ["TaskStore", "IngestionWorker", "enqueue_ingestion"]


def __getattr__(name):
    """Load Worker symbols lazily so ``python -m ...worker`` stays warning-free."""
    if name in {"IngestionWorker", "enqueue_ingestion"}:
        from .worker import IngestionWorker, enqueue_ingestion

        return {"IngestionWorker": IngestionWorker, "enqueue_ingestion": enqueue_ingestion}[name]
    raise AttributeError(name)
