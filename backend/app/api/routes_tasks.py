"""任务接口：查询状态、重试。"""

from fastapi import APIRouter, HTTPException

from ..core.config import REDIS_URL
from ..tasks import TaskStore, enqueue_ingestion

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

_task_store = TaskStore(REDIS_URL)


@router.get("/{task_id}")
async def get_task(task_id: str):
    """查询任务状态。"""
    task = _task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return task


@router.post("/{task_id}/retry")
async def retry_task(task_id: str):
    """重试失败任务：重置状态并入队。"""
    task = _task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    if task["status"] != "FAILED":
        raise HTTPException(status_code=400, detail="只有失败的任务才能重试")

    retry_count = int(task.get("retry_count", 0)) + 1
    _task_store.update_status(
        task_id,
        "PENDING",
        stage="",
        progress=0,
    )
    _task_store.client.hset(
        TaskStore.KEY_PREFIX + task_id, mapping={"retry_count": retry_count}
    )
    enqueue_ingestion(_task_store, task_id)
    return {"ok": True, "task_id": task_id, "status": "PENDING", "retry_count": retry_count}
