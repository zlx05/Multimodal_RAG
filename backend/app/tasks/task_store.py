"""Redis 任务状态存储。

任务状态机的设计（对应 docs/project-report.md 第 9 节）：
    PENDING -> PARSING -> OCR -> CHUNKING -> EMBEDDING -> INDEXING -> SUCCEEDED
                                     |
                                     +-> FAILED -> RETRYING

使用 Redis Hash 存每个任务的所有字段，用 task_id 作 key 前缀。
任务状态写入和查询都走这里，Worker 更新状态，API 查询状态。
"""

import json
import time
import uuid
from typing import Any

import redis


class TaskStore:
    """基于 Redis 的任务存储。每个任务是一个 hash：rag:task:{task_id}。"""

    KEY_PREFIX = "rag:task:"

    def __init__(self, redis_url: str = "redis://127.0.0.1:6379/0", password: str | None = None):
        self.client = redis.from_url(redis_url, decode_responses=True)

    # ---------- 任务创建 ----------

    def create_task(
        self,
        document_id: str,
        filename: str,
        source_path: str,
        **extra: Any,
    ) -> str:
        """创建任务并返回 task_id。初始状态 PENDING。"""
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        now = time.time()
        data = {
            "task_id": task_id,
            "document_id": document_id,
            "filename": filename,
            "source_path": source_path,
            "status": "PENDING",
            "stage": "",
            "progress": 0,
            "retry_count": 0,
            "error_message": "",
            "created_at": now,
            "updated_at": now,
        }
        data.update(extra)
        # Redis Hash 只存标量，dict/list/bool 值序列化为 JSON
        # （bool 不转直接 hset 会抛 DataError，曾导致 skip_review 放行入队失败）
        for key, value in data.items():
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                data[key] = json.dumps(value, ensure_ascii=False)
        self.client.hset(self.KEY_PREFIX + task_id, mapping=data)
        return task_id

    # ---------- 状态更新 ----------

    def update_status(
        self,
        task_id: str,
        status: str,
        stage: str = "",
        progress: int | None = None,
        error_message: str = "",
    ) -> None:
        """更新任务状态。"""
        key = self.KEY_PREFIX + task_id
        mapping: dict[str, Any] = {
            "status": status,
            "stage": stage,
            "updated_at": time.time(),
        }
        if progress is not None:
            mapping["progress"] = progress
        if error_message:
            mapping["error_message"] = error_message
        elif status != "FAILED":
            mapping["error_message"] = ""
        self.client.hset(key, mapping=mapping)

    def mark_succeeded(self, task_id: str, **extra: Any) -> None:
        """标记任务成功，可附带结果（如 chunk 数、向量数）。"""
        self.update_status(task_id, "SUCCEEDED", stage="DONE", progress=100)
        if extra:
            self.client.hset(self.KEY_PREFIX + task_id, mapping=extra)

    def mark_failed(self, task_id: str, error_message: str, error_code: str = "UNKNOWN") -> None:
        """标记任务失败，记录结构化错误码 + 面向用户的错误信息。

        error_code 是稳定机器可读标识（如 PARSE_FAILED / INDEXING_FAILED），
        供前端/运维按类型提示或重试；error_message 保留原始异常文本供排查。
        """
        self.update_status(task_id, "FAILED", error_message=error_message)
        self.client.hset(self.KEY_PREFIX + task_id, mapping={"error_code": error_code})

    # ---------- 查询 ----------

    def get_task(self, task_id: str) -> dict | None:
        """查询任务状态，返回 dict 或 None（任务不存在）。"""
        data = self.client.hgetall(self.KEY_PREFIX + task_id)
        if not data:
            return None
        # 反序列化 JSON 字段（时间戳转 float，dict/list 还原）
        for field in ("created_at", "updated_at"):
            if field in data and data[field] not in (None, ""):
                try:
                    data[field] = float(data[field])
                except (TypeError, ValueError):
                    pass
        for field in ("chunker_params", "extra", "skip_review"):
            if field in data and data[field]:
                try:
                    data[field] = json.loads(data[field])
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
        return data

    # ---------- 清理 ----------

    def delete_task(self, task_id: str) -> None:
        self.client.delete(self.KEY_PREFIX + task_id)
