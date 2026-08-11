# 学习文档：Redis 异步增量入库

> 目标读者：项目作者本人（要面试、要能讲清楚）。看完这份文档，你应该能：
> 1. 说清楚为什么入库必须异步、同步有什么问题。
> 2. 复述任务状态机的流转。
> 3. 说清楚 Redis 在里面扮演什么角色、任务队列怎么设计。
> 4. 说清楚内容哈希去重和增量更新怎么实现。
> 5. 回答面试官关于"失败重试 / 幂等 / 并发"的追问。

---

## 1. 这一模块解决什么问题

### 1.1 同步入库的问题

如果不做异步，上传一个 PDF 的完整链路是：

```
HTTP 请求进来
  -> 保存文件
  -> 解析 PDF（读文本、提取图片）
  -> OCR 图片（很慢，GPU 也要几秒/张）
  -> 分块
  -> Embedding（几十个 chunk 向量化）
  -> 写入 Milvus
  -> 返回结果
```

问题很明显：

1. **耗时不可控**：一本 200 页带扫描图的 PDF，OCR + Embedding 可能要好几分钟。HTTP 请求几秒就超时了。
2. **并发阻塞**：一次 OCR 占用 GPU/CPU，其他请求一起进来就排队，整个服务卡死。
3. **用户体验差**：前端上传后一直转圈等，不知道进度。

### 1.2 解决方案：生产者-消费者

```
API（生产者）                    Worker（消费者）
  POST 上传
    -> 保存文件
    -> 创建任务写 Redis
    -> 返回 task_id     ---->    Redis 队列
    （立即返回，不阻塞）   <----    取任务
                                    解析 -> OCR -> 分块 -> 向量化 -> 入库
                                    更新任务状态
```

- **API 只做轻活**：校验文件、保存文件、写任务、返回 task_id。HTTP 请求毫秒级返回。
- **Worker 做重活**：在后台消费队列，逐个处理入库任务，更新任务状态。
- **前端轮询**：拿到 task_id 后，轮询 `/tasks/{id}` 看进度。

这就是经典的**生产者-消费者模式**，面试时讲清楚这个就成功了一半。

---

## 2. 核心概念

### 2.1 Redis 的双重角色

Redis 在这个模块里有两个职责，容易混淆：

| 角色 | 用 Redis 的什么 | 作用 |
| --- | --- | --- |
| **任务队列** | List（RPUSH/LPUSH + BLPOP） | 存放待处理的 task_id，Worker 阻塞消费 |
| **任务状态** | Hash（HSET/HGETALL） | 存每个任务的 status / stage / progress / 错误信息 |

为什么不是只用一个？因为**队列和状态是两回事**：
- 队列是"还有哪些事要做"——先进先出，Worker 从队头取。
- 状态是"某件事做到哪一步了"——前端随时能查。

### 2.2 任务状态机

```
PENDING -> PARSING -> OCR -> CHUNKING -> EMBEDDING -> INDEXING -> SUCCEEDED
                                        |
                                        +-> FAILED -> RETRYING
```

每个状态含义：
- **PENDING**：已创建，等待 Worker 消费。
- **PARSING**：正在解析（PDF 提文本 / 图片 OCR / Markdown 分段）。
- **CHUNKING**：正在分块。
- **EMBEDDING**：正在向量化。
- **INDEXING**：正在写入 Milvus + BM25。
- **SUCCEEDED**：完成，入库成功。
- **FAILED**：失败，记录错误信息。

状态存 Redis Hash：

```
rag:task:{task_id}
  task_id:        task_abc123
  document_id:    doc_001
  filename:       高等数学.pdf
  status:         PENDING
  stage:          PARSING
  progress:       5
  retry_count:    0
  error_message:  ""
  created_at:     1720000000.0
  updated_at:     1720000010.0
```

### 2.3 为什么用 Redis 而不是内存队列

面试常问"为什么不直接用 Python 的 asyncio.Queue / 内存队列"。答案：

1. **进程隔离**：Worker 和 API 是不同进程（甚至不同机器），内存队列跨进程不可见。
2. **持久化**：API 挂了，任务还在 Redis 里；Worker 起来继续消费。内存队列一重启就丢。
3. **多 Worker 扩展**：可以起多个 Worker 并行消费，Redis 天然支持。
4. **状态可查**：前端要查进度，状态得放在 API 和 Worker 都能访问的地方，Redis 正合适。

### 2.4 内容哈希去重与增量

**为什么用内容哈希而不是文件名？** 同名文件可能内容不同，文件名不可靠。对文件内容算 SHA-256 哈希，同一份内容只处理一次。

**增量更新的思路**（不重建全量索引）：
- 处理版本键 = `document_hash + parser_version + chunk_config + embedding_model`。
- 同一个版本键已经 SUCCEEDED → 直接返回已有任务，不重复处理。
- 内容变了 → 哈希变了 → 新版本键 → 只处理这个文件的新索引。

项目当前实现：每个 document_id 对应一个 collection，重新上传同一文件时直接重建该 collection，不影响其他文档的 collection。这样"新增一个笔记"只动它自己的索引。

---

## 3. 代码怎么读

### 3.1 任务存储：[tasks/task_store.py](../backend/app/tasks/task_store.py)

```python
class TaskStore:
    KEY_PREFIX = "rag:task:"

    def create_task(self, document_id, filename, source_path, **extra) -> str:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        data = {"task_id": task_id, ..., "status": "PENDING", ...}
        data.update(extra)
        # Redis Hash 只存标量，dict/list 值序列化为 JSON
        for key, value in data.items():
            if not isinstance(value, (str, int, float, bool)):
                data[key] = json.dumps(value)
        self.client.hset(self.KEY_PREFIX + task_id, mapping=data)
        return task_id

    def update_status(self, task_id, status, stage="", progress=None, error_message=""):
        self.client.hset(self.KEY_PREFIX + task_id, mapping={...})

    def get_task(self, task_id) -> dict | None:
        return self.client.hgetall(self.KEY_PREFIX + task_id)
```

要点：
- `uuid4().hex[:12]` 生成唯一 task_id。
- 一个任务一个 Redis Hash，字段全存进去。
- 注意 **dict/list 要 JSON 序列化**——Redis Hash 只接受标量。这是实测踩过的坑。

### 3.2 Worker：[tasks/worker.py](../backend/app/tasks/worker.py)

```python
class IngestionWorker:
    def run_forever(self):
        while True:
            raw = self.tasks.client.blpop([QUEUE_KEY], timeout=0)  # 阻塞取任务
            _, task_id = raw
            self.process_task(task_id)   # 处理
            time.sleep(self.poll_seconds)

    def process_task(self, task_id):
        task = self.tasks.get_task(task_id)
        blocks = self._parse(document_id, source_path)   # 解析
        n = self._index(collection, blocks, task)        # 分块+向量化+入库
        self.tasks.mark_succeeded(task_id, chunks=n)     # 标记完成
```

- `blpop(..., timeout=0)`：阻塞等待队列有任务。0 表示永久等待，比轮询更高效。
- `process_task` 内部逐步更新状态（PARSING -> OCR -> CHUNKING -> INDEXING）。
- 异常时 `mark_failed(task_id, str(exc))` 记录错误。

### 3.3 完整链路（API 上传侧）

```
POST /api/v1/documents
  -> 保存文件到 data/
  -> tasks.create_task(...)            # 写任务状态
  -> enqueue_ingestion(tasks, task_id) # 推入队列
  -> 返回 {document_id, task_id, status: "PENDING"}
```

---

## 4. 实测结果（本项目验证）

异步入库全链路测试通过：

```
创建任务: task_xxx
任务状态: SUCCEEDED
chunks: 18
collection: rag_async_test_v1
=== 异步入库后的检索验证 ===
0.0328 ['bm25', 'vector'] | 快速排序：选一个基准元素...
```

说明：API 创建任务后立即返回，Worker 后台完成 18 个 chunk 的解析+分块+向量化+入库，之后检索能命中"快速排序"。

---

## 5. 面试问答

### Q1：为什么上传 PDF 要用异步任务？

**参考回答**：PDF 解析、OCR、批量 Embedding 都是长耗时任务。如果同步做，HTTP 请求会超时，且并发时阻塞整个服务。所以上传接口只保存文件 + 创建任务 + 入队，立即返回 task_id；Worker 后台消费队列处理，前端轮询任务状态。这就是生产者-消费者模式。

### Q2：Redis 在系统里具体做什么？

**参考回答**：两个职责。一是**任务队列**，用 List 存待处理的 task_id，Worker 用 BLPOP 阻塞消费。二是**任务状态存储**，用 Hash 存每个任务的 status / stage / progress / 错误信息，前端轮询查询。队列管"还有哪些事要做"，状态管"某件事做到哪了"。

### Q3：任务失败怎么处理？

**参考回答**：分两类。**临时错误**（网络超时、Milvus 暂时不可用）可以重试，用指数退避（2s、4s、8s），重试次数有限制。**永久错误**（文件损坏、格式不支持）直接 FAILED，不无限重试。每次失败记录错误阶段和原因，前端可以展示并手动重试。

### Q4：怎么保证同一个文件不会重复入库？

**参考回答**：内容哈希。对文件算 SHA-256，用 `document_hash + parser_version + chunk_config + embedding_model` 组成处理版本键。同一版本已成功就复用，不重复处理。文件名不可靠（同名不同内容），所以用内容哈希。

### Q5：并发时怎么保证 Worker 不重复处理同一任务？（进阶）

**参考回答**：可以用 Redis 的分布式锁（SETNX + 过期时间）保证同一任务只有一个 Worker 处理；或用 Redis Streams 的消费者组（consumer group），消息被一个消费者处理后自动从组里移除，天然避免重复消费。个人项目规模，任务幂等性 + 处理版本键基本够用。

### Q6：为什么任务状态用 Hash 不用 String？

**参考回答**：一个任务有十几个字段（status、stage、progress、error、时间戳等），Hash 能一次 HSET 全存、一次 HGETALL 全取，比多个 String key 或一个 JSON String 都清晰。HSET 还能单独更新某个字段（比如只更新 progress），不用整个 JSON 重写。

### Q7：前端怎么知道任务完成了？

**参考回答**：轮询。上传返回 task_id 后，前端每隔几秒调 `GET /api/v1/tasks/{id}`，看 status 变成 SUCCEEDED 或 FAILED。也可以做 WebSocket / SSE 推送，但个人项目轮询简单够用。轮询可以带指数退避：开始时 1s，几秒后 3s，之后 5s，减少无效请求。

---

## 6. 你该记住的"一句话总结"

> 异步增量入库 = 上传接口只做"保存文件 + 写任务 + 入队 + 返回 task_id"，Worker 在后台消费 Redis 队列，按 解析→OCR→分块→向量化→入库 的顺序处理并更新任务状态；用内容哈希做去重，同一文档内容变化时才重建它自己的索引。

面试问"为什么异步" → 答"长耗时任务不该阻塞 HTTP，生产者-消费者解耦"。问"Redis 干嘛" → 答"任务队列 + 状态存储，两个职责"。问"怎么增量" → 答"内容哈希 + 版本键，只更新变化文档的索引"。

---

## 7. 相关文件

| 文件 | 作用 |
| --- | --- |
| [tasks/task_store.py](../backend/app/tasks/task_store.py) | 任务状态存储（Redis Hash） |
| [tasks/worker.py](../backend/app/tasks/worker.py) | 后台入库 Worker |
| [hybrid_pipeline.py](../backend/app/rag/hybrid_pipeline.py) | Worker 调用的入库 pipeline |
| [scripts/test_async_ingestion.py](../scripts/test_async_ingestion.py) | 异步入库全链路测试 |
