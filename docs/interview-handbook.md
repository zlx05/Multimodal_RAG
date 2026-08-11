# 多模态 RAG 面试与手写学习手册

## 1. 一分钟项目介绍

这是一个面向学生复习场景的多模态私有知识库 RAG 系统。系统接收教材 PDF、Markdown 笔记、截图错题和手写笔记，使用 PyPDF 和 PaddleOCR 统一解析，再进行知识点感知分块、Embedding 和 Milvus 向量入库。同时使用 BM25 解决专业术语、公式和题号的精确匹配，通过 Redis 管理异步解析和增量索引，最终返回带文件名、页码和图片区域的溯源答案。

回答时不要把项目包装成 LangChain Agent：当前系统使用 FastAPI + Python Application Services 自主编排，重点说明为什么使用多模态、为什么混合检索、如何做增量、如何保证来源可信。

问答路径使用 Agent 工具循环（LangChain 经典 AgentExecutor + `search_library`/`search_documents` 工具，见 /chat/agent），**刻意不用 MCP**：系统是独立应用，前端自己消费后端检索工具，无需向第三方 MCP 客户端暴露。检索工具通过自有 FastAPI 端点被自家前端调用，不是暴露给任意模型的开放协议。

## 2. 高频架构问题

### Q1：为什么要做多模态 RAG？

参考回答：

学生资料不只有纯文本，截图错题、扫描 PDF 和手写笔记中的信息如果不做 OCR 就无法检索。系统需要同时保存 OCR 文本和原始图片来源。OCR 文本用于召回，原图用于复核，这样既能检索又能溯源。

### Q2：为什么不能只使用向量检索？

参考回答：

向量检索擅长语义相似，但公式、题号、英文缩写和专业名词对字符级匹配敏感。例如查询“泰勒公式余项”或“例题 3-2”时，BM25 更容易命中精确文本。因此采用 BM25 + 向量双路召回，再用 RRF 或重排模型合并结果。

### Q3：为什么要用 Redis？

参考回答：

PDF 解析、图片 OCR 和批量 Embedding 都是长耗时任务，不应该阻塞上传请求。API 只保存文件并写入任务，Worker 从 Redis 消费任务，按解析、OCR、分块、向量化、索引阶段更新状态。失败任务可以重试，也可以通过 task_id 给前端展示进度。

### Q4：如何实现增量更新？

参考回答：

对原始文件计算内容哈希，把 document_hash、parser_version、chunk_config 和 embedding_model 组成索引版本键。文件内容未变化时直接复用已有索引，内容变化时创建新的版本，不需要重建整个知识库。

### Q5：如何保证回答可溯源？

参考回答：

Chunk 不只保存文本，还保存 document_id、filename、page_number、heading_path、image_path 和 bbox。检索结果把来源元数据一起传给 LLM，最终 API 原样返回 sources。模型只负责基于上下文生成，前端展示来源片段和原图入口。

### Q6：Milvus 和 Redis 分别做什么？

参考回答：

Milvus 是向量数据库，负责长期保存 Embedding 和执行相似度检索。Redis 是任务队列和短期状态存储，负责调度 OCR、分块和向量化任务。两者职责不同，不能用 Redis 代替 Milvus 做大规模向量检索。

### Q7：Embedding 模型为什么选 BGE-small-zh-v1.5？

参考回答：

项目主要使用中文，BGE-small-zh-v1.5 对中文语义检索适配较好，模型相对轻量，适合个人电脑本地运行。当前模型输出 512 维向量，归一化后使用 COSINE 相似度。后续如果追求更高召回，可以替换更大的中文模型，但必须重新建立索引。

### Q8：不同资料如何选择分块策略？

项目通过 Chunking Profile 路由：Markdown/HTML 采用标题级联和段落边界；长篇文章先按章节和页码聚合，再在章节内部做语义切分；扫描 PDF、表格、公式和图片按版面区域保留；短问答保持问答对或使用固定窗口；高价值资料在 Parent-Child 基础上可选 Contextual Retrieval。策略选择依赖文件类型、解析出的 content_type、文本长度和问答标记，而不是只看扩展名。

### Q9：Parent-Child 为什么能提升检索质量？

Child 保持较小粒度，适合 BM25 和向量精确召回；Parent 保存章节或段落背景，生成答案时再根据 `parent_chunk_id` 扩展上下文。这样不会因为把整章直接向量化而降低召回精度，也不会因为只存很短的句子而失去上下文。

### Q10：Contextual Retrieval 为什么默认关闭？

它需要对每个 chunk 额外调用一次 LLM，成本和入库延迟会明显增加。项目只在 `high_value` Profile 且配置 `CONTEXTUAL_RETRIEVAL_ENABLED=true` 时开启，并把生成的上下文写入 `search_text`，原始 `content` 不被覆盖；调用失败则回退到标题上下文和原文。

## 3. 高频手写代码一：余弦相似度

### 题目

手写两个向量的余弦相似度。

~~~python
import math


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("vector dimensions do not match")

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)
~~~

### 手写解释

1. 先检查维度，否则点积没有意义。
2. 分子是点积，表示方向相似程度。
3. 分母是两个向量模长的乘积，用来消除长度影响。
4. 零向量没有方向，返回 0。
5. 如果向量已经归一化，余弦相似度可以直接用点积计算。

## 4. 高频手写代码二：RRF 融合排序

### 题目

合并 BM25 和向量检索的两个有序结果列表。

~~~python
from collections import defaultdict


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    k: int = 60,
) -> list[tuple[str, float]]:
    scores = defaultdict(float)

    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] += 1.0 / (k + rank)

    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
~~~

### 手写解释

RRF 不直接相加原始分数，因为 BM25 分数和向量距离不在同一尺度。它只使用排名位置，某个文档在两路结果中都靠前，就会得到更高的融合分数。

## 5. 高频手写代码三：内容哈希和幂等键

~~~python
import hashlib
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def build_version_key(
    document_hash: str,
    parser_version: str,
    chunk_config: str,
    embedding_model: str,
) -> str:
    raw = "|".join(
        [document_hash, parser_version, chunk_config, embedding_model]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
~~~

### 手写解释

内容哈希比文件名可靠，因为同名文件可能内容不同。版本键还必须包含解析器、分块配置和 Embedding 模型，否则配置变化后可能误用旧索引。

## 6. 高频手写代码四：递归分块

### 题目

按段落、换行和字符的优先级切分文本。

~~~python
def recursive_split(
    text: str,
    max_length: int,
    separators: list[str] | None = None,
) -> list[str]:
    separators = separators or ["\n\n", "\n", "。", "，", " "]

    if len(text) <= max_length:
        return [text]

    separator = separators[0] if separators else ""
    if not separator:
        return [
            text[index:index + max_length]
            for index in range(0, len(text), max_length)
        ]

    parts = text.split(separator)
    chunks: list[str] = []
    current = ""

    for part in parts:
        candidate = part if not current else current + separator + part

        if len(candidate) <= max_length:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(part) > max_length:
            chunks.extend(
                recursive_split(part, max_length, separators[1:])
            )
        else:
            current = part

    if current:
        chunks.append(current)

    return chunks
~~~

### 手写解释

递归分块先尝试高层次分隔符，只有某一段仍然超过长度限制时才下降到更细的分隔符。这样可以尽量保留段落和句子的完整性。

## 7. 高频手写代码五：生产者消费者任务模型

~~~python
import asyncio


async def worker(queue: asyncio.Queue):
    while True:
        task = await queue.get()

        try:
            await process_task(task)
        except Exception as exc:
            await mark_failed(task, str(exc))
        finally:
            queue.task_done()


async def submit_task(queue: asyncio.Queue, task: dict):
    await queue.put(task)
    return {"task_id": task["task_id"], "status": "PENDING"}
~~~

### 手写解释

上传接口是生产者，只负责放入任务；Worker 是消费者，执行耗时逻辑。queue.task_done 必须放在 finally 中，否则任务异常时队列可能永远无法正确结束等待。

真实项目中可以把 asyncio.Queue 替换为 Redis 队列或 Celery/RQ，但任务状态、异常处理和幂等原则不变。

## 8. 高频手写代码六：FastAPI 上传接口

~~~python
from fastapi import APIRouter, File, UploadFile

router = APIRouter(prefix="/api/v1/documents")


@router.post("")
async def upload_document(file: UploadFile = File(...)):
    allowed = {"application/pdf", "text/plain", "text/markdown"}

    if file.content_type not in allowed:
        raise ValueError("unsupported file type")

    document_id = create_document_id()
    path = save_upload(document_id, file.filename, file.file)
    task_id = enqueue_ingestion(document_id, path)

    return {
        "document_id": document_id,
        "task_id": task_id,
        "status": "PENDING",
    }
~~~

### 手写解释

接口只做校验、保存和入队，不同步解析文件。生产代码还需要限制文件大小、清洗文件名、校验真实文件类型，并把 ValueError 换成 HTTPException 或统一异常处理。

## 9. 高频手写代码七：Milvus 检索流程

~~~python
def search(collection, query_vector, top_k: int = 5):
    results = collection.search(
        data=[query_vector],
        anns_field="embedding",
        param={
            "metric_type": "COSINE",
            "params": {},
        },
        limit=top_k,
        output_fields=[
            "document_id",
            "content",
            "filename",
            "page_number",
        ],
    )

    return [
        {
            "content": hit.entity.get("content"),
            "document_id": hit.entity.get("document_id"),
            "filename": hit.entity.get("filename"),
            "page_number": hit.entity.get("page_number"),
            "score": float(hit.score),
        }
        for hit in results[0]
    ]
~~~

### 手写解释

向量字段必须和查询向量维度一致，metric_type 必须与建索引时一致。output_fields 决定检索结果是否能够完成溯源，不能只返回文本而丢失文件和页码。

## 10. 高频手写代码八：重试和指数退避

~~~python
import asyncio


async def retry(operation, max_attempts: int = 3):
    for attempt in range(max_attempts):
        try:
            return await operation()
        except TemporaryError:
            if attempt == max_attempts - 1:
                raise

            delay = 2 ** attempt
            await asyncio.sleep(delay)
~~~

### 手写解释

只有临时性错误才重试，例如网络超时和服务暂时不可用。文件损坏、格式不支持和参数校验失败属于永久错误，不能无限重试。生产环境还要加入随机抖动，避免大量任务同时重试。

## 11. 高频追问：为什么要分检索和生成

把检索和生成拆开有三个好处：

1. 可以单独评估召回质量。
2. 可以区分“没有召回正确资料”和“模型没有正确使用资料”。
3. 可以让前端展示检索调试信息和来源分数。

面试时可以说：RAG 系统不是一个黑盒调用，必须把 Retriever、Reranker 和 Generator 分开观测。

## 12. 高频追问：如何处理 OCR 错误

回答要点：

- 保存 OCR 置信度。
- 原图和 OCR 文本同时保留。
- 低置信度文本可以降低检索权重。
- 公式和表格不要只依赖普通 OCR，必要时保留图片作为最终来源。
- 让 LLM 在来源不确定时明确说明，而不是自动修正成看似合理的内容。

## 13. 高频追问：如何处理 Prompt Injection

回答要点：

- 资料内容是数据，不是系统指令。
- Prompt 明确要求只回答问题，不执行资料中的指令。
- 对检索文本做长度限制和来源标注。
- 不把外部文档内容拼接到 system message。
- 对用户输入、文档内容和系统规则做边界分离。

## 14. 高频追问：系统瓶颈在哪里

可能的瓶颈：

1. OCR：图片数量多时 CPU/GPU 消耗大。
2. Embedding：批量编码占用显存或内存。
3. Milvus：大量小批量 insert 会降低吞吐。
4. LLM：生成延迟和上下文长度限制。
5. Redis Worker：任务并发过高会导致模型重复加载。

优化方向：

- 批量 OCR 和批量 Embedding。
- Worker 进程内复用模型，不要每个任务重新加载。
- 批量插入 Milvus。
- 控制上下文长度。
- 对热门查询和文档列表做缓存。

## 15. 高频追问：为什么做这些技术取舍

- 选择 BGE-small-zh-v1.5：中文适配、资源消耗和效果之间平衡。
- 选择 Milvus：向量检索能力成熟，适合后续扩展。
- 选择 Redis：任务状态和异步队列简单，符合个人项目规模。
- 选择 BM25 + 向量：兼顾专业关键词和自然语言表达。
- 选择内容哈希：实现增量索引和幂等。
- 保存来源元数据：保证答案可解释和可复核。

## 16. 面试回答结构

遇到任何模块问题，按以下顺序回答：

1. 业务问题是什么。
2. 为什么单一方案不够。
3. 当前方案的核心流程。
4. 关键数据结构是什么。
5. 异常和边界情况怎么处理。
6. 如何评估效果。
7. 还有什么优化空间。

例如问“为什么用 BM25”时，不要只回答“关键词检索更准”，而要说明专业名词、公式、题号的精确召回需求，以及它和向量召回的互补关系。
