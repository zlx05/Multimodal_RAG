"""后台任务 Worker：消费 Redis 队列，执行异步入库。

处理链路（对应 docs/architecture.md 第 6 节）：
    PENDING -> PARSING -> OCR -> CHUNKING -> EMBEDDING -> INDEXING -> SUCCEEDED

Worker 独立于 API 进程运行。API 只负责创建任务，Worker 在这里消费。
"""

import json
import os
import time
import urllib.request
from pathlib import Path

from .task_store import TaskStore
from ..core.config import RAG_ORIGINAL_DIR, RAG_WORK_DIR
from ..rag.document_registry import (
    document_collection_name,
    infer_document_topic,
    update_document,
)
from ..rag.hybrid_pipeline import HybridRAGPipeline
from ..rag.chunking_profiles import resolve_profile
from ..rag.metrics import get_metrics

QUEUE_KEY = "rag:ingestion:queue"


class IngestionWorker:
    """后台入库 Worker。blocking 模式下无限循环消费队列。"""

    def __init__(
        self,
        redis_url: str = "redis://127.0.0.1:6379/0",
        milvus_host: str = "127.0.0.1",
        milvus_port: str = "19530",
        api_key: str = "",
        poll_seconds: float = 2.0,
    ):
        self.tasks = TaskStore(redis_url)
        self.milvus_host = milvus_host
        self.milvus_port = milvus_port
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.poll_seconds = poll_seconds
        # pipeline 缓存：同一 collection 复用一个 pipeline，避免重复加载模型
        self._pipelines: dict[str, HybridRAGPipeline] = {}

    # ---------- 主循环 ----------

    def run_forever(self):
        """阻塞式主循环，从队列取任务处理。"""
        print(f"[worker] 开始消费任务队列 {QUEUE_KEY}")
        while True:
            try:
                self._poll_once()
            except KeyboardInterrupt:
                print("[worker] 收到中断，退出")
                break
            except Exception as exc:
                print(f"[worker] 主循环异常: {exc}")
            time.sleep(self.poll_seconds)

    def _poll_once(self) -> None:
        """取一个任务并处理（用于阻塞循环的单次迭代）。"""
        # Use a finite Redis block timeout. An infinite BLPOP conflicts with
        # redis-py's socket timeout and produces a false error on an idle queue.
        raw = self.tasks.client.blpop([QUEUE_KEY], timeout=1)
        if not raw:
            return
        _, task_id = raw
        try:
            self.process_task(task_id)
        except Exception as exc:
            print(f"[worker] 任务 {task_id} 处理异常: {exc}")
            self.tasks.mark_failed(task_id, str(exc)[:500])

    # ---------- 单任务处理 ----------

    def process_task(self, task_id: str) -> None:
        """处理一个已入队任务。供 run_forever 和外部（测试）调用。"""
        task = self.tasks.get_task(task_id)
        if not task:
            print(f"[worker] 任务 {task_id} 不存在，跳过")
            return

        document_id = task["document_id"]
        source_path = task["source_path"]
        collection = task.get("collection_name", f"rag_{document_id}")

        self.tasks.update_status(task_id, "PENDING", stage="PARSING", progress=5)
        try:
            source_url = task.get("source_url", "")
            if source_url:
                self._download_html(source_url, source_path)
            t0 = time.perf_counter()
            blocks = self._parse(document_id, source_path, task.get("filename", ""), source_url)
            get_metrics().observe("parse_ms", (time.perf_counter() - t0) * 1000)
            self.tasks.update_status(task_id, "PENDING", stage="OCR", progress=40)

            topic_label = infer_document_topic(task.get("filename", ""), blocks)
            collection = document_collection_name(document_id, topic_label)
            for block in blocks:
                block.metadata["topic_label"] = topic_label
            update_document(
                document_id,
                topic_label=topic_label,
                collection_name=collection,
                chunk_profile=task.get("chunk_profile", "auto"),
            )
            self.tasks.client.hset(
                self.tasks.KEY_PREFIX + task_id,
                mapping={"topic_label": topic_label, "collection_name": collection},
            )

            review = self._review(document_id, task, blocks)
            upload = self._get_upload(document_id)
            # 管理员已人工放行/驳回的，以管理员为准：worker 自动校验不再覆盖台账。
            admin_owned = bool(upload and upload.get("reviewed_by") == "admin")
            # 提示词注入是强信号：模型 approved 判定可能含糊，但注入标记命中即拦截
            # （fail-closed，管理员可在审计后台复核放行）。完整判定存入 review_payload。
            rejected = not review["approved"] or bool(review.get("prompt_injection"))
            if rejected:
                # 校验不通过：标记 rejected（隐藏但保留，不进检索），管理员可在审计后台放行/删除。
                note = str(review["reason"] or "").strip() or (
                    "检测到提示词注入风险" if review.get("prompt_injection") else "校验未通过"
                )
                if upload and not admin_owned:
                    self._update_upload(
                        upload["id"],
                        status="rejected",
                        review_note=note[:500],
                        review_payload=json.dumps(review, ensure_ascii=False),
                        reviewed_by="agent",
                        reviewed_at=time.time(),
                    )
                self.tasks.update_status(
                    task_id,
                    "REJECTED",
                    stage="REVIEW",
                    progress=100,
                    error_message=note[:500],
                )
                print(f"[worker] 任务 {task_id} 校验未通过，未入库: {note}")
                return

            if upload and not admin_owned:
                self._update_upload(
                    upload["id"],
                    status="approved",
                    review_payload=json.dumps(review, ensure_ascii=False),
                    reviewed_by="agent",
                    reviewed_at=time.time(),
                )

            t1 = time.perf_counter()
            chunks_count = self._index(collection, blocks, task)
            get_metrics().observe("index_ms", (time.perf_counter() - t1) * 1000)
            self.tasks.mark_succeeded(
                task_id,
                chunks=chunks_count,
                collection_name=collection,
            )
            get_metrics().incr("docs_ingested")
            print(f"[worker] 任务 {task_id} 完成，入库 {chunks_count} chunks")
        except Exception as exc:
            print(f"[worker] 任务 {task_id} 失败: {exc}")
            get_metrics().incr("docs_failed")
            self.tasks.mark_failed(task_id, str(exc)[:500])
            raise

    def _parse(self, document_id: str, source_path: str, filename: str = "", source_url: str = ""):
        """解析文档为 DocumentBlock 列表。"""
        from ..rag.parsers import create_parser

        parser_kwargs = {
            "original_dir": RAG_ORIGINAL_DIR,
            "work_dir": RAG_WORK_DIR,
            "vision_analyzer": self._get_vision_analyzer(),
            "formula_recognizer": self._get_formula_recognizer(),
        }
        if source_url:
            parser_kwargs["base_url"] = source_url
        parser = create_parser(
            document_id,
            source_path,
            **parser_kwargs,
        )
        blocks = parser.parse(source_path)
        # 清洗：字符归一化 + 页眉页脚/水印/OCR 噪声剥离（选型前，见 rag/cleaning.py）
        from ..rag.cleaning import clean_blocks

        blocks = clean_blocks(blocks)
        # 补上文件名元数据，供溯源展示
        filename = filename or Path(source_path).name
        for block in blocks:
            block.metadata["filename"] = filename
            block.metadata["document_name"] = filename
            if source_url:
                block.metadata["source_url"] = source_url
        if not blocks:
            raise ValueError("解析结果为空，没有可索引的内容")
        return blocks

    # ---------- 上传校验 ----------

    def _review(self, document_id: str, task: dict, blocks) -> dict:
        """校验上传内容是否合理。开关关闭、管理员放行（skip_review）或调用失败都放行。"""
        from ..core.config import UPLOAD_REVIEW_ENABLED
        from ..rag.review_agent import review_content

        if not UPLOAD_REVIEW_ENABLED:
            return {"approved": True, "reason": "上传校验已关闭"}
        if task.get("skip_review"):
            return {"approved": True, "reason": "管理员放行，跳过校验"}
        try:
            from ..rag.review_agent import _blocks_preview

            decision = review_content(
                self._get_review_llm(),
                filename=task.get("filename", ""),
                source_type="",
                blocks_preview=_blocks_preview(blocks),
            )
            return decision.model_dump()
        except Exception as exc:
            # 校验调用失败不能阻塞入库 → 放行并记录，管理员可后续纠正。
            print(f"[worker] 校验 agent 调用失败，按放行处理: {exc}")
            return {"approved": True, "reason": f"校验调用失败，按放行处理: {str(exc)[:200]}"}

    def _get_review_llm(self):
        """复用默认问答模型的 OpenAI 兼容客户端（惰性创建）。"""
        if getattr(self, "_review_llm", None) is None:
            from langchain_openai import ChatOpenAI

            from ..core.config import get_model_config

            config = get_model_config(None)
            self._review_llm = ChatOpenAI(
                base_url=str(config["base_url"]),
                api_key=str(config["api_key"]),
                model=str(config["id"]),
                temperature=0,
                max_tokens=1024,
            )
        return self._review_llm

    @staticmethod
    def _get_upload(document_id: str):
        from ..db.org import get_upload_by_document

        return get_upload_by_document(document_id)

    @staticmethod
    def _update_upload(upload_id: str, **updates):
        from ..db.org import update_upload

        return update_upload(upload_id, **updates)

    @staticmethod
    def _download_html(url: str, target: str) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": "ContextLab/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read(20 * 1024 * 1024 + 1)
            if len(content) > 20 * 1024 * 1024:
                raise ValueError("网页超过 20MB 限制")
        Path(target).write_bytes(content)

    @staticmethod
    def _get_vision_analyzer():
        from ..rag.vision import VisionAnalyzer

        return VisionAnalyzer()

    @staticmethod
    def _get_formula_recognizer():
        from ..core.config import FORMULA_RECOGNITION_DEVICE, FORMULA_RECOGNITION_ENABLED

        if not FORMULA_RECOGNITION_ENABLED:
            return None
        from ..rag.ocr.formula_engine import create_formula_recognizer

        return create_formula_recognizer(device=FORMULA_RECOGNITION_DEVICE)

    def _index(self, collection: str, blocks, task: dict) -> int:
        """分块 + 向量化 + 入库（Milvus + BM25）。"""
        from ..rag.chunkers import get_chunker

        profile = resolve_profile(task.get("chunk_profile", "auto"), task.get("filename", ""), blocks)
        pipeline = self._get_pipeline(collection)
        params = dict(profile.params)
        if profile.chunker == "semantic":
            params["embedder"] = pipeline.embedder
        chunker = get_chunker(profile.chunker, **params)

        self.tasks.update_status(task["task_id"], "PENDING", stage="CHUNKING", progress=60)
        n = pipeline.build(blocks, chunker, profile=profile)
        self.tasks.client.hset(
            self.tasks.KEY_PREFIX + task["task_id"],
            mapping={"chunk_profile": profile.id, "chunker": profile.chunker},
        )
        self.tasks.update_status(task["task_id"], "PENDING", stage="INDEXING", progress=90)
        return n

    def _get_pipeline(self, collection: str) -> HybridRAGPipeline:
        """获取（或创建）collection 对应的 pipeline。

        pipeline 会缓存 Milvus 句柄（collection 内部 ID）。若 collection 已被
        管理员驳回/删除时 drop，缓存句柄指向已删除的旧 collection，重入库
        insert 会报 `collection not found`。这里每次校验 collection 是否还存在，
        被删过就丢弃旧句柄重建（__init__ 会自动按 schema 重新建 collection）。
        """
        from pymilvus import connections, utility

        connections.connect(alias="default", host=self.milvus_host, port=self.milvus_port)
        if collection in self._pipelines:
            if utility.has_collection(collection):
                return self._pipelines[collection]
            # 旧句柄已失效（collection 被 drop），丢弃重建
            del self._pipelines[collection]
        self._pipelines[collection] = HybridRAGPipeline(
            collection,
            self.api_key,
            milvus_host=self.milvus_host,
            milvus_port=self.milvus_port,
            with_llm=False,
        )
        return self._pipelines[collection]


def enqueue_ingestion(tasks: TaskStore, task_id: str) -> None:
    """把任务 ID 推入队列（API 上传时调用）。"""
    tasks.client.rpush(QUEUE_KEY, task_id)


def run_worker() -> None:
    """启动 Worker 的入口函数（用于 python -m backend.app.tasks.worker）。"""
    from ..core.database import init_db

    try:
        init_db()
    except Exception as exc:
        # Worker 独立于 API 进程运行，必须自己保证表存在。
        print(f"[worker] MySQL 初始化失败（documents 表未建）：{exc}")
    worker = IngestionWorker()
    worker.run_forever()


if __name__ == "__main__":
    run_worker()
