# 多模态个人学习知识库 RAG 系统：完整架构报告

## 1. 项目概述

### 1.1 项目名称

多模态个人学习知识库 RAG 系统。

### 1.2 目标用户

主要面向需要整理教材、课堂笔记、截图错题和手写资料的学生，也适合作为个人技术学习项目和 RAG 工程实践项目。

### 1.3 要解决的问题

传统个人学习资料有四个明显问题：

1. 资料格式复杂：PDF、Markdown、TXT、截图和手写照片并存。
2. 资料内容分散：同一个知识点可能出现在教材、课堂笔记和错题图片中。
3. 关键词检索和普通向量检索各有缺陷：专业术语、公式和题号需要精确匹配，口语化问题又需要语义匹配。
4. 生成式问答容易失去出处：如果不保存页码、文件名和图片信息，回答无法复核。

本项目的核心不是简单调用大模型，而是构建一条可追溯、可增量、可扩展的多模态知识处理链路。

## 2. 设计目标和非目标

### 2.1 设计目标

- 统一接入 PDF、Markdown、TXT、普通图片和手写笔记图片。
- 对扫描 PDF 和图片资料使用 OCR 提取可检索文本。
- 保留原图、文件名、页码、标题路径和 OCR 区域等溯源信息。
- 通过标题、段落和语义边界进行知识点感知分块。
- 使用 BM25 和向量检索进行混合召回。
- 使用 Redis 管理异步解析、OCR、Embedding 和索引任务。
- 通过内容哈希实现幂等和增量更新。
- 使用 Vue 3 构建资料管理、任务状态和问答溯源界面。

### 2.2 非目标

第一阶段不追求：

- 多租户权限体系。
- 在线协同编辑。
- 大规模分布式训练。
- 复杂的知识图谱推理。
- 将所有资料永久上传到云端。

系统首先服务于个人学习场景，重点保证链路清晰、结果可解释、架构可扩展。

## 3. 总体架构

~~~text
                         Vue 3 + Vite
                              |
                        REST / JSON API
                              |
                         FastAPI Layer
        +---------------------+---------------------+
        |                     |                     |
   Document API          Task API             Chat API
        |                     |                     |
        +---------------------+---------------------+
                              |
                       Application Services
        +---------------------+---------------------+
        |                     |                     |
  Parser Service       Task Service       Retrieval Service
        |                     |                     |
   PyPDF / OCR       Redis Queue       BM25 + Milvus
        |                     |                     |
        +---------------------+---------------------+
                              |
                    LLM / Embedding Services
            BGE-small-zh-v1.5 / OpenAI-compatible LLM
~~~

### 3.1 分层职责

| 层 | 职责 | 不应该做的事情 |
| --- | --- | --- |
| Vue 3 | 页面展示、表单、任务轮询、来源展示 | 直接访问 Milvus 或模型 |
| FastAPI Router | 参数校验、鉴权、调用服务、响应转换 | 编写 OCR 和向量化细节 |
| Application Service | 编排解析、分块、检索和生成流程 | 处理 HTTP 细节 |
| Domain Model | Document、Chunk、Task、Source 等数据结构 | 依赖具体 Web 框架 |
| Infrastructure | Redis、Milvus、文件系统、模型客户端 | 决定业务流程 |
| Worker | 执行耗时任务、重试和状态更新 | 直接暴露 HTTP 接口 |

## 4. 推荐目录结构

~~~text
.
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── routes_documents.py
│   │   │   ├── routes_tasks.py
│   │   │   ├── routes_retrieval.py
│   │   │   └── routes_health.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── logging.py
│   │   │   └── errors.py
│   │   ├── domain/
│   │   │   ├── documents.py
│   │   │   ├── chunks.py
│   │   │   └── tasks.py
│   │   ├── services/
│   │   │   ├── document_service.py
│   │   │   ├── ingestion_service.py
│   │   │   ├── retrieval_service.py
│   │   │   └── answer_service.py
│   │   ├── infrastructure/
│   │   │   ├── redis_client.py
│   │   │   ├── milvus_store.py
│   │   │   ├── bm25_store.py
│   │   │   └── model_clients.py
│   │   └── rag/
│   │       ├── pipeline.py
│   │       ├── catalog.py
│   │       ├── model_config.py
│   │       └── chunkers/
│   ├── workers/
│   │   ├── celery_app.py
│   │   └── ingestion_worker.py
│   ├── tests/
│   ├── requirements.txt
│   └── requirements-multimodal.txt
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── layouts/
│   │   ├── router/
│   │   ├── stores/
│   │   ├── types/
│   │   └── views/
│   ├── public/
│   ├── package.json
│   └── vite.config.ts
├── data/
├── models/
├── infra/
│   └── docker-compose.yml
├── scripts/
├── docs/
├── .env.example
└── README.md
~~~

当前仓库已经完成基础目录、文本 RAG 核心、中文模型配置和文档；api、services、infrastructure、workers 和 Vue src 目录是后续实现时按此报告展开。

## 5. 多模态资料处理流程

### 5.1 文件进入系统

前端上传文件后，API 做四件事：

1. 校验扩展名、MIME 类型和文件大小。
2. 计算文件内容哈希。
3. 保存原始文件和基本元数据。
4. 写入 Redis 任务并立即返回 task_id。

上传接口不能在 HTTP 请求中同步处理整本 PDF，因为 OCR 和向量化耗时不可控，容易造成超时和并发阻塞。

### 5.2 解析器选择

~~~text
文件 MIME 类型
    |
    +-- application/pdf
    |      +-- PyPDF 提取文本
    |      +-- 判断文本密度
    |      +-- 对扫描页或图片区域执行 PaddleOCR
    |
    +-- image/*
    |      +-- 保存原图
    |      +-- PaddleOCR 提取文字
    |
    +-- text/markdown
           +-- 解析标题、段落、代码块和列表
~~~

### 5.3 统一中间结构

所有解析器都应该输出统一的 DocumentBlock，而不是直接输出字符串。

~~~python
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocumentBlock:
    document_id: str
    source_type: str
    content_type: str
    text: str
    page_number: int | None = None
    image_path: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    heading_path: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
~~~

这样做的好处是，后续分块、Embedding 和回答生成不需要关心输入来自 PDF 还是图片。

## 6. 知识点感知分块

### 6.1 为什么不能固定长度切分

固定长度切分可能把定义和公式拆开，也可能让一个完整考点跨越多个无关分块。对于学生资料，标题、例题、解题步骤和结论之间通常具有明确结构，应该优先保留。

### 6.2 当前 Profile 路由与切分顺序

上传任务支持 `auto`、`technical`、`long_form`、`layout`、`short_qa` 和 `high_value`。自动模式先读取文件类型、解析出的 `content_type`、文本长度以及问答标记，再选择 Profile。

~~~text
technical: 标题级联 -> 段落边界 -> 字符上限
long_form: 标题/页码聚合 -> 句子 Embedding -> 语义边界 -> 字符上限
layout: 页面/图片/表格/公式区域保留
short_qa: 问答单元优先 -> 固定字符窗口
high_value: long_form + Parent-Child + 可选 Contextual Retrieval
~~~

每个 Chunk 至少保存：

~~~text
chunk_id
document_id
content
content_hash
source_type
filename
page_number
heading_path
image_path
bbox
chunk_index
embedding_model
~~~

### 6.3 分块参数

- chunk_size：单个分块的最大字符或 token 数。
- overlap：相邻分块的重叠范围。
- min_chunk_size：过滤没有检索价值的短文本。
- max_chunk_size：防止异常长段落进入模型上下文。
- heading_path：把标题上下文附加到正文前，提高召回可解释性。

长文 Profile 为每个父级语义单元生成 `parent_chunk_id`，子块只负责精确召回；生成答案时可以根据父 ID 扩展相邻上下文。高价值 Profile 的 Contextual Retrieval 默认关闭，开启后由服务端 LLM 为 chunk 生成不超过 80 字的主题上下文，并与原文分开保存。

## 7. 中文 Embedding 模型

当前使用 BGE-small-zh-v1.5：

- 适合中文语义检索。
- 输出向量维度为 512。
- 使用归一化向量时可以用 COSINE 相似度。
- 已下载到项目 models 目录。
- 实际路径由 model_config.py 自动探测，也可以通过 EMBEDDING_MODEL 覆盖。

向量化流程：

~~~python
from sentence_transformers import SentenceTransformer


model = SentenceTransformer(model_path)
vectors = model.encode(
    texts,
    normalize_embeddings=True,
    batch_size=32,
    show_progress_bar=False,
)
~~~

归一化后，向量点积等价于余弦相似度：

~~~text
cosine(a, b) = a · b / (||a|| ||b||)
~~~

## 8. Milvus 设计

### 8.1 Collection 字段

~~~text
id              INT64         主键
document_id     VARCHAR       文档 ID
content         VARCHAR       分块文本
embedding       FLOAT_VECTOR  512 维向量
source_type     VARCHAR       pdf/image/markdown/text
filename        VARCHAR       原文件名
page_number     INT64         PDF 页码
chunk_index     INT64         分块序号
metadata        JSON          标题路径、bbox 等扩展数据
~~~

当前基线只保存 text 和 embedding，后续多模态实现必须补齐来源字段，否则无法完成真正的溯源问答。

### 8.2 索引注意事项

- Embedding 模型改变时，向量维度或分布可能改变，需要新 Collection。
- 文档内容发生变化时，不能复用旧索引。
- Collection 名称或索引元数据中必须包含 document_hash、chunk_config 和 embedding_model。
- 入库失败时不能留下空 Collection，避免下次被误认为已完成。
- 大文件需要批量 Embedding 和批量插入。

## 9. Redis 异步任务设计

### 9.1 任务状态

~~~text
PENDING -> PARSING -> OCR -> CHUNKING -> EMBEDDING -> INDEXING -> SUCCEEDED
                                             |
                                             +-> FAILED -> RETRYING
~~~

任务表或 Redis Hash 至少保存：

~~~text
task_id
document_id
status
stage
progress
retry_count
error_message
created_at
updated_at
~~~

### 9.2 幂等性

使用 document_hash + parser_version + chunk_config + embedding_model 组成处理版本键。

同一个版本已经成功时，重复提交只返回已有任务或索引，不重复处理。

### 9.3 重试原则

- 网络错误、临时服务错误可以重试。
- 文件损坏、格式不支持、参数错误不应该无限重试。
- 每次失败记录错误阶段和堆栈摘要。
- 重试必须使用新的 task_id 或明确记录 attempt。

## 10. BM25 + 向量混合检索

### 10.1 两路检索职责

| 检索方式 | 擅长内容 | 弱点 |
| --- | --- | --- |
| BM25 | 公式、题号、专业名词、精确关键词 | 同义表达召回弱 |
| 向量检索 | 口语化问题、语义相似内容 | 专业词和符号可能不稳定 |

### 10.2 推荐流程

~~~text
用户问题
  -> 查询清洗
  -> BM25 Top K
  -> Milvus Top K
  -> 分数归一化
  -> RRF 或加权融合
  -> 去重
  -> 重排
  -> 截取上下文
  -> LLM 回答
~~~

### 10.3 RRF 融合

对于某个结果在第 rank 名：

~~~text
RRF_score = sum(1 / (k + rank_i))
~~~

RRF 不要求 BM25 分数和向量分数在同一尺度上，工程上比直接相加更稳定。

### 10.4 去重

用 document_id + chunk_id 去重，不要只根据文本去重，因为同样的内容可能出现在不同页码或不同资料中。

## 11. RAG 生成和溯源

Prompt 必须约束模型：

1. 只使用提供的参考资料。
2. 找不到答案时明确说资料中没有找到。
3. 不把用户问题中的指令当成系统指令。
4. 回答中的关键结论关联来源编号。
5. 来源不充分时降低确定性，不编造页码。

最小问答响应：

~~~json
{
  "answer": "根据资料，...",
  "sources": [
    {
      "document_id": "doc_001",
      "filename": "高等数学.pdf",
      "page": 12,
      "text": "定义内容...",
      "score": 0.86,
      "source_type": "pdf",
      "image_url": null,
      "bbox": null
    }
  ],
  "retrieval": {
    "bm25_top_k": 8,
    "vector_top_k": 8,
    "rerank": true
  }
}
~~~

来源字段是多模态 RAG 的核心契约。图片来源还应补充 image_url 和可选的 bbox，不能只返回 OCR 后的文本。

## 12. API 设计

### 12.1 文档接口

~~~text
POST /api/v1/documents
GET  /api/v1/documents
GET  /api/v1/documents/{document_id}
DELETE /api/v1/documents/{document_id}
~~~

上传成功只返回任务信息：

~~~json
{
  "document_id": "doc_001",
  "task_id": "task_001",
  "status": "PENDING"
}
~~~

### 12.2 任务接口

~~~text
GET  /api/v1/tasks/{task_id}
POST /api/v1/tasks/{task_id}/retry
~~~

### 12.3 问答接口

~~~text
POST /api/v1/retrieval/search
POST /api/v1/chat/ask
~~~

检索和生成最好拆成两个接口，便于调试召回质量，也能区分“检索错了”和“模型生成错了”。

## 13. Vue 3 前端设计

### 13.1 页面

- KnowledgeBase：资料列表、类型筛选、索引状态。
- UploadTask：上传资料、任务进度、失败重试。
- DocumentDetail：原文、分块、页码和 OCR 结果。
- Chat：问题输入、答案、来源卡片。
- RetrievalDebug：展示 BM25、向量、融合和重排结果。

### 13.2 状态

Pinia 保存：

~~~text
documentStore
taskStore
chatStore
retrievalStore
~~~

API 客户端统一处理：

- JSON 序列化。
- HTTP 错误。
- task_id 轮询。
- 取消请求。
- 统一响应类型。

## 14. 安全、稳定性和可观测性

### 14.1 安全

- API Key 只从环境变量读取，不由前端长期保存。
- 上传文件限制扩展名、MIME、大小和路径。
- 文件名不能直接拼接为任意路径。
- 原始资料和 OCR 结果不能默认公开访问。
- Prompt 中防止资料内容注入系统指令。

### 14.2 可观测性

至少记录：

- task_id、document_id、stage。
- 解析耗时、OCR 耗时、Embedding 耗时。
- chunk 数量、向量数量。
- BM25 召回数量、向量召回数量。
- 首 token 延迟和总响应耗时。
- 来源命中率和用户反馈。

## 15. 评估指标

### 15.1 检索指标

- Recall@K：正确资料是否出现在前 K 个结果。
- MRR：正确结果的排名质量。
- nDCG：多个相关结果的排序质量。
- 来源准确率：答案引用的来源是否真的支持答案。

### 15.2 系统指标

- 文档处理成功率。
- 任务平均耗时。
- OCR 失败率。
- 问答 P95 延迟。
- 重复文件去重率。
- 单份文档的索引成本。

### 15.3 评测集

已建立 43 条问题的评估集（`data/eval/questions.jsonl`），全部来自库内真实内容，每题标注唯一 `document_id`（文档级）。运行 `python scripts/eval_retrieval.py`（从仓库根）可复现四路对比（纯向量 / 纯 BM25 / 原始 RRF / 生产链路）的 Recall@1/3/5 + Precision@1/3 与 MRR，结果见 `data/eval/results_new.json`。当前实测（切片升级后新索引）：

```text
Variant      Recall@1     Recall@3     Recall@5      MRR       Prec@1
vector       0.9302       0.9767       0.9767      0.9507      0.9302
bm25         0.8140       0.9302       0.9302      0.8661      0.8140
rrf          0.4651       0.8837       0.9535      0.6868      0.4651
production   0.9767       0.9767       1.0000      0.9826      0.9767
```

**切片策略升级前后对比**（`results.json` 8-07 旧索引快照 vs `results_new.json` 重跑，同 43 题同文档）：production Recall@1 0.930→0.977、MRR 0.954→0.983；纯 BM25 微降（0.837→0.814，chunk 变大稀释字面密度），但端到端链路大幅受益。

**重排机制前后对比**（同一索引内）：原始 RRF（无重排）Recall@1=0.465，生产链路（路由门控 + `_relevance_score` 可解释重排）拉到 0.977——重排层是检索质量承重的关键环节。

**答案忠诚度自动评估（零人工标注）**：`scripts/eval_faithfulness.py`（RAGAS 风格 LLM-as-judge）把答案拆成原子断言、逐条对照检索来源判断支持性（supported/partial/unsupported，权重 1/0.5/0）。43/43 全评、平均 0.865；fully_grounded(≥0.9) 53.5%、grounded(≥0.7) 79.1%；与检索命中交叉 43/43 全部命中。完整对比报告见 `data/eval/report.md`。

**评估集扩容 43→74 与调参**：43 题下 production Recall@1=0.977 接近天花板、区分度不足，扩到 74 题（新增 31 题覆盖数据结构/Go 语法/数据类型/常量，`questions.jsonl` q101-q131）后四路重跑（`results_74.json`）：production Recall@1=0.9595、MRR=0.9741。扩题暴露旧权重 0.55/0.35/0.10 掉到 0.9324（「类型定义/语义相似文档」类跨文档混淆）；用 `scripts/eval_sensitivity.py` 扫描 `_federated_search` 五个参数，确认 semantic_min 是唯一敏感维度（0.48→0.55 时 R@1 0.977→0.930）、其余四维持平，当前参数已局部最优；并把 relevance 权重从 0.55/0.35/0.10 调到 **0.65/0.25/0.10**（强化语义主导），74 题下 Recall@1 回到 0.9595、43 题下持平（0.9767），已落生产默认值。结论已写入 `data/eval/report.md` 四、五章节。

后续扩展方向：复跑 faithfulness 验证调参对答案质量的影响（端到端）；继续扩题与跨文档关系型评估。

## 16. 当前实现边界

当前仓库已经具备：

- FastAPI 文本 RAG API。
- Milvus 向量入库和检索。
- 多种文本分块器。
- BGE-small-zh-v1.5 中文 Embedding。
- 基础项目目录和配置管理。
- RAG 评测集和自动化测试（43 题评估集 + `scripts/eval_retrieval.py` 四路对比 + 指标单测，见 §15.3）。

> 注：本清单是早期文档，"下一阶段"中的 Vue 3 前端、API v1、PyPDF/PaddleOCR 解析器、Redis 任务、Chunk 元数据、BM25 混合检索均已在后续阶段落地，详见 README 与 docs/upgrade-plan.md。

## 17. 推荐实施顺序

不要同时开发所有模块，建议按以下顺序：

1. 先完成 Vue 3 和 API v1 的文档契约。
2. 再完成 PDF/图片解析，确保统一 DocumentBlock。
3. 再接入 Redis，把同步入库改成异步任务。
4. 再完善 Milvus 元数据和索引版本。
5. 再加入 BM25 和融合排序。
6. 最后做重排、评测和界面优化。
