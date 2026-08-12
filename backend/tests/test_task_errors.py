"""任务结构化错误码测试。

覆盖：error_code_for_stage 阶段→错误码映射、mark_failed 落库 error_code、
process_task 失败按当前阶段映射错误码（fake Redis + monkeypatch 的 _parse）。
"""

import pytest

from backend.app.tasks.task_store import TaskStore
from backend.app.tasks.worker import IngestionWorker, error_code_for_stage


class _FakeRedis:
    def __init__(self):
        self.data = {}

    def hset(self, key, mapping=None):
        self.data.setdefault(key, {}).update(mapping or {})

    def hgetall(self, key):
        return dict(self.data.get(key, {}))

    def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture
def store(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("backend.app.tasks.task_store.redis.from_url", lambda url, **kw: fake)
    return TaskStore("redis://fake/0")


class _NoopMetrics:
    def incr(self, *args, **kwargs):
        pass

    def observe(self, *args, **kwargs):
        pass


def test_error_code_for_stage():
    assert error_code_for_stage("DOWNLOAD") == "DOWNLOAD_FAILED"
    assert error_code_for_stage("PARSING") == "PARSE_FAILED"
    assert error_code_for_stage("REVIEW") == "REVIEW_FAILED"
    assert error_code_for_stage("INDEXING") == "INDEXING_FAILED"
    assert error_code_for_stage("UNKNOWN_STAGE") == "UNKNOWN"


def test_mark_failed_writes_error_code(store):
    task_id = store.create_task(document_id="d1", filename="f.pdf", source_path="/tmp/f.pdf")
    store.mark_failed(task_id, "解析抛错", error_code="PARSE_FAILED")
    task = store.get_task(task_id)
    assert task["status"] == "FAILED"
    assert task["error_code"] == "PARSE_FAILED"
    assert task["error_message"] == "解析抛错"


def test_mark_failed_default_error_code(store):
    task_id = store.create_task(document_id="d2", filename="g.md", source_path="/tmp/g.md")
    store.mark_failed(task_id, "boom")
    assert store.get_task(task_id)["error_code"] == "UNKNOWN"


def test_process_task_failure_uses_stage_error_code(store, monkeypatch):
    """_parse 抛异常时 stage=PARSING → 错误码映射为 PARSE_FAILED 落库。"""
    monkeypatch.setattr("backend.app.tasks.worker.get_metrics", lambda: _NoopMetrics())
    worker = IngestionWorker(redis_url="redis://fake/0")
    worker.tasks = store

    def _boom(*args, **kwargs):
        raise ValueError("解析失败: 文件损坏")

    monkeypatch.setattr(worker, "_parse", _boom)
    task_id = store.create_task(document_id="d3", filename="h.pdf", source_path="/tmp/h.pdf")
    worker.process_task(task_id)

    task = store.get_task(task_id)
    assert task["status"] == "FAILED"
    assert task["error_code"] == "PARSE_FAILED"
    assert "解析失败" in task["error_message"]
