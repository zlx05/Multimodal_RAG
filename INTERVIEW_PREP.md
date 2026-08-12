# 多模态个人学习知识库 RAG 系统

## 面试准备手册

本文档服务于两个目标：

1. 帮助面试时用清晰、真实、可追问的方式介绍项目。
2. 帮助继续完善项目时，知道每个设计选择解决了什么问题，以及如何验证它是否有效。

本文档中的“当前实现”只描述仓库里已经存在并验证过的能力；“建议方案”和“下一阶段”表示后续计划，不应在面试中说成已经上线。

---

## 1. 项目定位

### 1.1 一句话介绍

这是一个面向学生复习场景的多模态私有知识库 RAG 系统，能够把 PDF、Word、PPT、Markdown、截图错题和手写笔记统一解析，经过知识点分块、混合检索后生成带文件名、页码和原始区域信息的可追溯答案。

### 1.2 解决的实际问题

学生的复习资料有几个典型特点：

- 文件格式多，纯文本、扫描 PDF、截图、Word、PPT 和手写照片同时存在。
- 同一个知识点可能分散在教材、课堂笔记和错题截图中。
- 专业名词、公式、题号和英文缩写对精确匹配敏感，普通向量检索不一定稳定。
- 只给出生成答案不够，复习时必须能回到原文、页码或图片进行核对。
- 新增一张错题截图不应该触发整库重建。

项目的重点不是简单调用一个大模型，而是建立一条“解析准确、检索可解释、任务可恢复、答案可溯源”的数据链路。

### 1.3 当前技术栈

| 层次 | 技术 | 主要职责 |
| --- | --- | --- |
| 前端 | Vue 3、Vite、TypeScript、Pinia、Vue Router | 资料上传、任务状态、问答、来源和切片检查 |
| API | FastAPI、Pydantic | 上传、任务、检索、问答、模型目录和切片查询 |
| Agent 编排 | LangChain（经典 AgentExecutor，非 LangGraph） | Agentic 问答：意图路由、检索工具、Thought-Action-Observation 循环 |
| 元数据存储 | MySQL（SQLAlchemy） | 文档注册表、会话和记忆（跨进程一致） |
| 异步任务 | Redis | 任务队列、任务状态和失败重试 |
| 向量检索 | Milvus | Embedding 存储、向量索引和相似度召回 |
| 关键词检索 | BM25 | 专业术语、公式、题号和关键词精确召回 |
| 文档解析 | PyPDF、python-docx、python-pptx、Microsoft Office COM | PDF、Word、PPT 和旧格式文件解析 |
| 图片解析 | PaddleOCR、PP-FormulaNet_plus-M | OCR、公式识别、框坐标和置信度 |
| PDF 整档解析 | MinerU（opendatalab，可选） | 扫描件 OCR、混合图表件的布局/表格/公式/图片 caption |
| 视觉理解 | OpenAI 兼容视觉模型 | 图片、手写、页面版面和复杂图文的补充理解 |
| 文本生成 | OpenAI 兼容文本模型 | 基于检索上下文生成最终答案 |
| Embedding | BGE-small-zh-v1.5 | 中文文本向量化 |

当前最终回答支持以下模型切换：

- `gpt-5.6-terra`
- `gpt-5.6-luna`
- `deepseek-v4-flash`

密钥只存在后端 `.env`，前端只能提交公开的模型 ID。

---

## 2. 三十秒项目介绍

可以这样回答：

> 我做的是一个面向学生复习的多模态个人知识库 RAG 系统。它支持教材 PDF、Word、PPT、Markdown、截图错题和手写笔记。入库时先按文件类型解析，图片和扫描页面使用 OCR、公式识别以及视觉模型补充理解，再统一生成带页码、图片路径、OCR 框和置信度的文档块。检索阶段同时使用 BM25 和向量检索，解决专业术语、公式和自然语言问题的不同召回需求。问答层做成了轻量 Agent：先做意图路由决定查哪些资料，再在 ReAct 循环里多轮检索，证据不足会主动补检索，最后带来源编号作答。长耗时解析放到 Redis Worker 中异步执行，新增资料只做增量更新，不重建整库。

这段话包含了项目最重要的五个特点：

1. 多模态输入，不只是纯文本 RAG。
2. 混合检索，不只依赖向量相似度。
3. 来源可追溯，不只返回一段没有依据的答案。
4. 异步增量入库，不把耗时任务放在 HTTP 请求线程中。
5. 问答是 workflow 骨架 + 内嵌 ReAct Agent：意图识别、检索推理、画像更新由模型决策，检索在 Thought→Action→Observation 循环里多轮补查，只有落库等纯 I/O 是确定性流水线。

---

## 3. 两分钟完整介绍

面试官要求详细介绍时，可以按下面顺序展开：

### 3.1 场景

学生资料格式混杂，尤其是截图错题、扫描 PDF 和手写笔记，无法直接交给普通文本 RAG。即便 OCR 能提取文字，公式、表格、页码、图片区域和原文关系仍然容易丢失。

### 3.2 解析

系统先根据扩展名和 MIME 类型选择解析器。文本类文件提取段落和标题，PDF 保留页码，Word 和 PPT 提取段落、表格、内嵌图片以及原生公式。图片和扫描页面先用 OCR，公式使用专门的公式识别模型，复杂版面再调用视觉语言模型补充结构化描述。

### 3.3 统一数据结构

不同解析器最终都输出 `DocumentBlock`。后面的分块、Embedding、Milvus 和来源返回不需要关心原始文件类型。

一个文档块至少包含：

- `document_id`
- `source_type`
- `content_type`
- `content`
- `page_number`
- `heading_path`
- `image_path`
- `bbox`
- `confidence`
- `metadata`

### 3.4 检索

同一个问题经过两路召回：BM25 负责关键词、题号、专业名词和公式，Milvus 负责语义相似。两路结果通过 RRF 融合，保留两路命中来源，便于调试和解释。

### 3.5 场景化分块

我没有让所有资料使用同一个 chunk_size，而是引入 Chunking Profile：技术文档和 HTML 使用标题级联加段落边界；长篇文章先按章节和页码聚合，再在章节内部做语义切分；扫描 PDF、表格、公式和图片使用版面区域保留；短问答优先保持问答对完整；高价值资料可在 Parent-Child 基础上开启 Contextual Retrieval。原始 chunk 和增强后的 `search_text` 分开保存，避免标题增强文本污染用户看到的原文。

### 3.6 问答：workflow 骨架 + 内嵌 ReAct Agent

`/api/v1/chat/agent` 整条链路是一个**四段 workflow**，只有其中一段是 agentic 的：

```text
意图识别 ──> ReAct 检索推理（agent） ──> 落库 + 自动压缩 ──> 画像更新
   │                │                        │                 │
 一次LLM调用      循环检索/作答            确定性I/O         一次LLM判断
```

1. **意图识别**：先用 structured output（`bind_tools`，不强制 response_format 以兼容 DeepSeek thinking）决定检索范围——全库还是指定资料，返回 `scope` 和 `document_ids`，并给出来源理由。
2. **ReAct 检索推理（唯一的 agent 段）**：LangChain 经典 `AgentExecutor` 把混合检索封装成 `search_library` 和 `search_documents` 两个工具，模型在 Thought→Action→Observation 循环里自主决定查不查、查哪个工具、查几次；证据不足会主动补检索，**证据充分后输出最终答案结束循环**——检索与作答是同一个循环，不可拆开。
3. **落库 + 自动压缩**：确定性流程，把消息与 Agent 轨迹写入 MySQL；长会话（超过阈值）把旧消息折叠进一条滚动摘要（摘要由单次 LLM 调用生成，但无循环，仍是 workflow）。
4. **画像更新**：长回答后单次 LLM 判断学生行为、薄弱点、风格倾向，再用确定性规则合并写回画像。

关键判断标准：**"用没用 LLM"不等于 agent，"有没有自主循环"才是**。四段里只有第 2 段内部存在 ReAct 循环，其余三段都是单次 LLM 调用或纯 I/O，属于 workflow。路由决策和工具调用链会原样返回给前端，保证过程可解释、可追踪。之所以不用 LangGraph，是因为单轮问答场景 `AgentExecutor` 足够，不需要持久化图状态。

### 3.7 生成

检索结果会被拼成带编号的上下文，提示模型只能根据参考资料回答，并要求关键结论标注 `[n]`。模型不直接读取 Milvus，也不直接处理原始图片，它负责把已经解析和检索过的文本组织成答案。

### 3.8 工程化

上传接口只保存文件、计算哈希、创建任务并写入 Redis。Worker 消费任务，依次执行解析、OCR、分块、Embedding 和 Milvus 入库。每一步更新状态，前端通过 task ID 轮询进度。失败任务可以重试，视觉模型失败时保留 OCR 结果继续入库。

---

## 4. 当前系统架构

```text
Vue 3 + Vite
        |
        | REST / JSON
        v
FastAPI
  |             |                |
  |             |                +--> Model catalog
  |             +-------------------> Retrieval / chat
  |             |                   |   ├─ /chat/ask     混合检索 + 一次性生成
  |             |                   |   └─ /chat/agent   AgentExecutor（LangChain）
  |             |                   |        意图路由 → search_library / search_documents
  |             |                   |        → Thought→Action→Observation → 带 [n] 作答
  |             +-------------------> 文档注册表 / 会话 / 记忆
  |             |                        MySQL + SQLAlchemy
  +----------------------------------> Document / task API
        |
        +--> Redis queue
        |       |
        |       v
        |   Ingestion Worker
        |       |
        |       +--> PDF / DOCX / PPTX / Markdown parser
        |       +--> PaddleOCR / formula recognizer
        |       +--> Vision LLM
        |       +--> BGE Embedding
        |       +--> Milvus + BM25
        |
        +--> OpenAI-compatible text model
```

### 4.1 服务职责

#### Vue 3

负责页面、交互、任务轮询和来源展示。它不能直接访问 Milvus、Redis、模型 API 或任何密钥。

#### FastAPI

负责鉴权边界、参数校验、文件保存、任务创建、检索和问答编排。模型切换通过服务端白名单完成。

#### Redis

负责待处理任务队列和任务状态。Redis 不保存向量，也不负责语义检索。

#### Worker

独立消费 Redis 任务，执行 CPU 或外部 API 密集的长耗时操作。API 不会因为一本 PDF 的 OCR 而长时间阻塞。

#### Milvus

负责持久化向量和向量相似度检索。当前每份资料使用独立 Collection，Collection 名称包含 document ID。

#### BM25

负责关键词检索。当前 Worker 在每个 Collection 建立 BM25 语料，服务重启时从 Milvus 读取文本重建 BM25。

#### 文本模型

只消费检索后的文本上下文。当前使用 OpenAI 兼容接口，后端根据模型 ID选择 API 地址和密钥。

---

## 5. 当前前端问题与建议方案

### 5.1 问题一：界面提示性文字过多

当前页面存在一些说明型文案，例如：

- 解释系统每个区域怎么使用。
- 反复说明“密钥在后端”“模型如何工作”。
- 空状态中使用较长的引导段落。
- 顶部副标题、面板 eyebrow、辅助文字同时出现，造成信息层级过多。

这类文字在技术演示时有帮助，但在真实使用中会造成两个问题：

1. 用户需要在真正操作前阅读大量说明。
2. 重要信息，例如任务状态、答案和来源，被说明文字稀释。

### 5.2 文案收敛原则

后续界面采用三层信息策略：

| 层级 | 内容 | 处理方式 |
| --- | --- | --- |
| 第一层 | 当前任务、答案、错误、来源和按钮 | 直接显示 |
| 第二层 | 文件类型、页码、模型名称、召回方式 | 紧凑显示 |
| 第三层 | 原理说明、模型安全说明、操作帮助 | 默认折叠或放入 tooltip |

具体修改：

- 去掉大段顶部介绍，只保留页面标题。
- 去掉面板中重复的英文小标题和解释句。
- 上传区只保留支持格式和文件大小限制。
- 问答页默认只显示问题输入框、资料范围、模型和提交按钮。
- 任务状态只显示当前阶段、进度和错误，不显示长篇解释。
- “密钥由后端保管”保留为一个锁图标 tooltip，不占据主要版面。
- 空状态只保留一句动作指引，例如“上传一份资料开始”。
- 失败信息使用具体错误和一个重试按钮，不使用安慰型或营销型措辞。

### 5.3 问题二：不能要求用户记住文件名

当前问答页让用户手动选择 Collection 对应的文件。这在测试时可以工作，但真实使用不合理：用户记得的是“高数极限”“数据库错题”“操作系统课堂笔记”，而不是自动生成的 document ID 或原始文件名。

问答应该默认面向“知识空间”，而不是面向“文件选择器”。

---

## 6. 推荐的自动资料路由方案

推荐把问答流程改成“默认全库自动检索，用户按需修正范围”。

### 6.1 用户体验

问答页初始状态只显示：

- 一个问题输入框。
- 一个默认范围按钮：`全部资料`。
- 一个回答模型选择器。
- 一个提交按钮。

用户提问后，系统返回结果顶部显示：

```text
已使用资料：高等数学教材、极限错题截图
命中 6 个知识块
```

“已使用资料”是可点击的紧凑范围条。用户不满意时，可以展开资料列表，取消某一份资料或切换到某个主题。这个动作是修正自动路由，而不是每次问答的前置条件。

### 6.2 推荐后端流程

```text
用户问题
   |
   v
问题清洗与主题表示
   |
   +--> 资料级路由
   |       标题、标签、摘要和代表向量
   |       选出候选资料 Top N
   |
   +--> 全局关键词检索
   |       文件名、标题、题号、关键词
   |
   v
候选资料内的 BM25 + 向量检索
   |
   v
RRF 融合与可选重排
   |
   v
上下文压缩、去重、来源编号
   |
   v
文本模型生成答案
```

### 6.3 文档级路由的实现方式

当前每份资料一个 Collection，直接跨所有 Collection 查询会产生三个问题：

1. 每个 Collection 都要单独执行向量搜索，资料数量增多后延迟线性增长。
2. 不同 Collection 的 BM25 分数和向量分数不一定可直接比较。
3. 前端无法只凭文件名理解资料主题。

推荐下一阶段增加“资料画像”并逐步引入工作区级 Collection。

每份资料完成入库后，保存一条文档画像：

```json
{
  "document_id": "doc_xxx",
  "title": "高等数学极限错题",
  "summary": "极限、泰勒展开、洛必达法则相关错题和解析",
  "topics": ["极限", "泰勒展开", "洛必达法则"],
  "source_types": ["pdf", "image"],
  "chunk_count": 38,
  "profile_embedding": [0.012, -0.087, 0.031]
}
```

用户问题先和文档画像做一次轻量相似度搜索，选出候选资料。然后只在候选资料中做 Chunk 级混合检索。

### 6.4 Collection 的两种选择

#### 方案 A：保留每文档 Collection，做联邦检索

流程是并发查询多个 Collection，再在应用层统一 RRF。

优点：

- 改造小，符合当前代码结构。
- 删除一份资料时只删除对应 Collection。
- 适合当前个人知识库规模。

缺点：

- Collection 数量增多后管理复杂。
- 每个 Collection 都需要加载和搜索。
- 全局 BM25 需要单独维护。

#### 方案 B：工作区级 Collection

所有 Chunk 进入一个或少量 Collection，用 `document_id`、`source_type`、`subject` 等字段保存来源。

优点：

- 统一做向量检索和 BM25。
- 更容易实现全部资料自动路由。
- 分数、索引和召回统计更容易统一。

缺点：

- 删除资料需要按 `document_id` 过滤删除。
- Collection schema 和索引迁移成本更高。
- 单 Collection 数据量变大后需要规划分区和索引。

对于当前个人项目，推荐先实现方案 A，完成自动路由后再根据资料数量决定是否迁移方案 B。面试时可以说明这是“小规模个人库的工程取舍”，而不是盲目追求复杂架构。

### 6.5 API 设计建议

当前请求要求 `collection`，后续可以兼容旧字段并新增范围字段：

```json
{
  "question": "数据库事务的隔离级别如何区分？",
  "model": "gpt-5.6-terra",
  "scope": "auto",
  "document_ids": [],
  "top_k": 5
}
```

字段含义：

- `scope=auto`：系统自动选择相关资料。
- `scope=all`：搜索全部资料。
- `scope=selected`：只搜索 `document_ids`。
- `document_ids`：用户展开范围条后手动修正的资料集合。
- `model`：公开模型 ID，服务端再解析 API 密钥。

返回结果增加路由信息：

```json
{
  "answer": "...",
  "model": "gpt-5.6-terra",
  "used_documents": [
    {
      "document_id": "doc_xxx",
      "filename": "数据库课堂笔记.md",
      "reason": "命中事务隔离级别主题",
      "score": 0.82
    }
  ],
  "sources": [],
  "retrieval": {
    "scope": "auto",
    "candidate_documents": 2,
    "vector_top_k": 8,
    "bm25_top_k": 8,
    "rerank": "rrf"
  }
}
```

### 6.6 自动路由的失败处理

自动路由不能假设永远正确，需要给用户一个可见的纠正入口：

- 资料画像没有命中时，回退到全库检索。
- 候选资料置信度低时，显示“已从全部资料检索”。
- 用户可以展开“已使用资料”，取消资料或加入指定资料。
- 如果回答没有足够来源，模型必须回答“资料中没有找到”，不能强行生成。
- 记录路由结果，便于后续分析“错在资料路由还是错在 Chunk 召回”。

### 6.7 为什么不直接让大模型决定文件

可以让大模型根据所有文件名做选择，但不推荐作为唯一方案：

- 文件名可能没有主题信息。
- 每次都把所有文件名送给模型增加成本和延迟。
- 模型选择结果不稳定，难以复现。
- 不能替代向量和关键词检索的召回评估。

更合理的方式是“结构化文档画像 + 轻量向量路由 + 可解释的 Chunk 检索”，模型只做最终答案生成和必要的查询改写。

---

## 7. 多模态解析深挖

### Q1：为什么这是多模态 RAG，而不是普通文本 RAG？

参考回答：

资料源本身包含图片、扫描页面、手写笔记、表格和公式。系统不是只接收一个已经存在的纯文本字段，而是针对不同模态做解析、识别和来源保留。图片和页面被转成可检索文本，同时保留原图路径、页码、OCR 框和置信度，所以检索输入是结构化的多模态解析结果。

需要补充边界：当前最终文本模型不直接读取图片，视觉模型主要用于入库阶段的理解。这属于“多模态数据处理 + 文本 RAG 生成”，不是“多模态生成模型直接看图回答”。

### Q2：PDF 如何处理？

先在文档级分类（`pdf_classifier.py`）：用 pypdf 均匀抽样 ≤10 页，统计文本密度（与 `TEXT_PAGE_MIN_CHARS` 对齐）和图片密度，判定 `native / scanned / mixed`，分类结果写入每块 `metadata.pdf_kind` 供追溯。然后按类别走不同路线：

1. 文本型 PDF（native）：pdfplumber 按阅读顺序提取文字、表格、标题层级；失败逐页回退 PyPDF；内嵌图片走 OCR/视觉。
2. 扫描型 PDF（scanned）：整档交给 MinerU 以 `-m ocr` 全页 OCR；MinerU 未启用或失败时，逐页渲染后走 PaddleOCR + 公式识别 + 视觉模型。
3. 混合图表件（mixed）：文本页与图表/扫描页混杂，整档交给 MinerU `-m auto`（布局 + 表格 + 公式 + 图片 caption），再把 `middle.json` 按页码映射为 DocumentBlock。

MinerU 是可选引擎（`MINERU_ENABLED`），配 CUDA torch 走 GPU，首次运行需下载模型；它只服务 scanned/mixed，`native` 仍走轻量快路。MinerU 不可用/超时/空结果一律回退现有逐页路线，不阻塞入库。原始 PDF 和渲染图片保留在本地资料目录，Milvus 保存用于检索的文本和来源元数据。

### Q3：Word 和 PPT 的公式会不会乱码？

原生公式通常不是普通字符串，而是 Office Open XML 中的 OMML 结构。系统对 Word 和 PPT 中的原生公式读取 OMML，再转换为 LaTeX 文本。图片中的公式不能依赖普通 OCR，而使用 `PP-FormulaNet_plus-M`。原图始终保留，LaTeX 结果用于召回和展示，复杂公式可以回看原图。

### Q4：表格和图片怎么存？

表格不会只拼成没有结构的字符串。解析器会保留表格内容和表格类型元数据，并把适合检索的文本序列化到 Chunk 中。图片不直接塞进 Milvus 向量字段，而是：

- 原图保存到本地原始资料目录。
- OCR 文本、公式文本和视觉描述进入 `content`。
- 图片路径进入 `image_path`。
- OCR 框坐标进入 `bbox`。
- 识别置信度进入 `confidence`。
- 表格、版面和解析器信息进入 `metadata`。

这样做的好处是：向量库负责检索，文件存储负责原始证据，两者职责分离。

### Q5：OCR 识别错了怎么办？

不能把 OCR 结果当作绝对正确。系统采用三层策略：

1. 保存 OCR 置信度和框坐标。
2. 对复杂图片调用视觉模型做纠错和结构化描述。
3. 结果中保留原图，让用户可以复核。

对于公式，普通 OCR 结果不作为最终公式结果，而是优先使用专门公式模型或原生 OMML 转换。

### Q6：为什么视觉模型失败后还可以继续？

视觉模型是补充解析能力，不应该成为唯一数据来源。调用失败时，解析器保留已有文本提取和 OCR 结果，并记录失败信息。这样外部 API 超时不会让整份资料完全丢失，用户仍然可以检索基础文本。

---

## 8. 分块与索引

### Q7：为什么不能直接按固定长度切分？

固定长度简单，但可能把标题和正文、定义和公式、题目和答案拆开。对于学习资料，Chunk 的语义完整性比平均长度更重要。当前分块优先考虑标题路径、段落和语义边界，过长段落再退化到字符或 Token 层切分。

### Q8：Chunk 太大或太小分别有什么问题？

Chunk 太小：

- 上下文不完整。
- 检索命中后模型缺少定义或条件。
- 来源数量变多，生成上下文碎片化。

Chunk 太大：

- 主题边界变模糊。
- 向量表示被多个主题平均。
- 单次问答上下文变长，成本和延迟增加。

需要通过离线问题集比较不同 chunk 配置，而不是只凭感觉调参数。

### Q8.1：为什么不同资料要使用不同的分块策略？

因为文件后缀不等于知识结构。Markdown 和 HTML 的信息边界通常是标题和段落，研究报告的边界更适合在章节内部由语义相似度判断；扫描 PDF 的边界是版面区域，表格、公式和图片不能与正文随意合并；Excel/CSV 的边界是行组，每块要重复表头才能自包含；短问答最重要的是保持问题和答案完整。因此系统用 `Chunking Profile` 路由策略，而不是给所有资料设置同一个 chunk size。分块前还有统一清洗层（字符归一化、行尾断连字符、页眉页脚/页码/水印剥离），避免噪声污染向量与 BM25。

### Q8.2：Parent-Child 和 Contextual Retrieval 分别解决什么问题？

Parent-Child 解决“召回粒度”和“生成上下文”的矛盾：Child 小而精确，Parent 保留章节或段落背景。Contextual Retrieval 则让模型为原始 chunk 补充文档主题和章节语境，改善脱离上下文后难以检索的短片段。它会增加 LLM 调用成本，所以项目只对高价值 Profile 开启，并缓存增强结果；原始 chunk 始终保留。

### Q9：BM25 和向量检索如何互补？

BM25 依赖词频、逆文档频率和文档长度归一化，适合字面匹配。向量检索把问题和 Chunk 映射到向量空间，适合“如何避免重复提交”和“幂等设计”这类表达不同但语义接近的问题。

项目采用两路召回：

```text
问题
  +--> BM25 top-k
  +--> 向量 top-k
          |
          v
      RRF 融合
          |
          v
      统一排序
```

### Q10：RRF 是什么？为什么不用直接相加两个分数？

两路分数的量纲和分布不同，BM25 分数与 Cosine 分数不能未经处理直接相加。RRF 使用排名而不是原始分数：

```text
RRF(d) = sum(1 / (k + rank_i(d)))
```

其中 `rank_i(d)` 是文档在第 `i` 路结果中的排名，`k` 是平滑常数。一个 Chunk 同时进入 BM25 和向量结果时会获得更高的融合分数，即使两路原始分数不可比也可以稳定合并。

### Q10.1：为什么会出现很多 `0.016`？这是不是相关度很低？

不是。`0.016` 通常来自 `1 / (60 + 1)`，它是 RRF 的内部排名分，只表示某个结果在某一路排第 1，不能当作概率或用户可理解的相关度。当前接口保留 `rrf_score` 作为调试字段，同时将精确实体命中、有效关键词覆盖、向量相似度和最终排名合成为 0～1 的 `score`，前端再显示成百分比。

### Q10.2：如何避免“张林翔的学号”召回 Go 语言片段？

问题不只在向量模型。BM25Plus 对没有命中的文档也可能产生正的基线分；如果把疑问词“什么”当作关键词，Go 文档也会被误判为命中。当前做法分三层：

1. 过滤“什么、如何、怎么、是否”等疑问词和包含虚词的 Bigram。
2. 对“实体 + 属性”问题抽取实体锚点，例如从“张林翔的学号是什么”抽取“张林翔”，资料级路由要求 Collection 的 chunk 中出现该实体。
3. 对命中实体的资料补回所有精确命中的 chunk，再做 BM25 + 向量融合，避免正确片段因局部 Top-K 不稳定而丢失。

这类门控属于检索前的约束，不是让最终模型自行“猜哪个文件相关”。

另外，跨 Collection 做资料级路由时，不能把 RRF 原始值直接当成最终展示分数。每个 Collection 的局部排名都从 1 开始，多个资料的第一名都可能是 `1 / 61 = 0.0164`。因此系统先用 RRF 生成候选，再按实体命中、关键词覆盖、向量相似度和排名计算展示分数，并按展示分数重新排序。

### Q11：Milvus 中应该保存什么？

核心字段包括：

- 主键 `id`
- `document_id`
- `filename`
- `source_type`
- `content_type`
- `page_number`
- `chunk_index`
- `content`
- `heading_path`
- `image_path`
- `bbox`
- `confidence`
- `metadata`
- `embedding`

向量字段保存 Embedding，文本和来源字段用于过滤、重建 BM25 以及结果展示。不要只保存向量，否则检索后无法回答来源问题。

### Q12：为什么用 Cosine 相似度？

中文文本向量通常关注方向而不是绝对长度。Embedding 归一化后，Cosine 可以衡量两个向量方向的接近程度，且实现简单。当前 BGE-small-zh-v1.5 输出 512 维向量，Collection 的向量维度必须和模型保持一致。更换 Embedding 模型后不能直接复用旧 Collection，需要重建或迁移。

---

## 9. 异步任务与增量入库

### Q13：为什么上传接口不能直接解析？

PDF OCR、视觉 API、公式识别和批量 Embedding 都可能耗时较长。如果在请求线程执行：

- 上传请求超时。
- FastAPI Worker 被长任务占满。
- 用户看不到中间进度。
- 外部模型失败时难以重试。

所以 API 只负责保存和入队，Worker 负责执行。

### Q14：任务状态如何设计？

当前状态字段包括：

- `task_id`
- `document_id`
- `status`
- `stage`
- `progress`
- `error_message`
- `retry_count`
- `created_at`
- `updated_at`
- `chunks`
- `collection_name`

典型流程：

```text
PENDING
  -> PARSING
  -> OCR
  -> CHUNKING
  -> EMBEDDING
  -> INDEXING
  -> SUCCEEDED
             |
             +--> FAILED -> RETRYING -> PENDING
```

当前 Worker 会在解析、OCR、分块和索引阶段更新状态，前端用 task ID 轮询 `/api/v1/tasks/{task_id}`。

### Q15：如何实现幂等和增量？

对文件计算 SHA-256 内容哈希，并把解析器版本、分块配置和 Embedding 模型视为索引版本的一部分。相同内容和相同配置不需要重复构建；内容或关键配置变化时创建新版本或重建对应 Collection。

面试追问“如果任务执行到一半进程崩溃怎么办”时，可以回答：

- 任务状态不能只存在内存中，要存在 Redis。
- Worker 取任务后更新阶段。
- 任务完成后才标记 `SUCCEEDED`。
- 失败任务保留错误信息并支持重试。
- Collection 写入需要批量、flush 和完成校验，避免留下空索引。

### Q16：为什么不用 Celery？

当前项目规模是个人学习工具，Redis + 自定义 Worker 足够简单，依赖更少，状态字段也更容易和前端对齐。Celery 更适合任务类型多、调度规则复杂、需要成熟重试和并发管理的系统。面试时不要说 Celery 不好，而要说明当前规模下选择简单方案的原因。

---

## 10. 模型、密钥与安全

### Q17：前端如何切换模型但不暴露密钥？

前端只调用：

```http
GET /api/v1/models
```

返回模型 ID、显示名称、描述、是否可用和默认状态，不返回 API Key。提问时只传：

```json
{
  "model": "gpt-5.6-luna"
}
```

后端使用模型白名单查询对应配置：

```text
model id
   |
   v
server-side allowlist
   |
   +--> base_url
   +--> api_key
   +--> provider model name
```

服务端拒绝未知模型 ID，也拒绝没有配置密钥的模型。前端构建产物中不应该出现 `LLM_API_KEY`、`VISION_LLM_API_KEY` 或第三方 API 地址。

### Q18：为什么视觉模型和回答模型要分开配置？

视觉模型负责图像理解，最终回答模型负责文本生成，两者的输入格式、成本、延迟和能力重点不同。分开配置后可以：

- 视觉模型失败时回退 OCR。
- 最终问答按用户需要切换模型。
- 不需要把视觉模型绑定成最终生成模型。
- 后续可以给不同阶段设置不同超时、限流和预算。

### Q19：为什么最终回答不直接把图片交给模型？

当前架构把视觉理解前置到入库阶段，优势是同一份图片只解析一次，之后可以被多个问题复用；检索阶段保留文本、页码和图片区域，最终模型只读取相关上下文，成本和链路更可控。代价是前置解析质量非常重要，所以必须保留原图并记录置信度。

### Q20：如何防止模型脱离资料胡编？

四层控制：

1. 检索阶段只把相关 Chunk 放进上下文。
2. Prompt 明确要求只依据参考资料回答。
3. 要求关键结论标注来源编号 `[n]`。
4. 没有证据时输出“资料中没有找到”，前端展示来源片段供复核。

这不能保证模型绝对不幻觉，所以还需要离线评估“回答是否被来源支持”。

---

## 11. 当前遇到的工程难点与解决方式

### 难点一：多模态解析结果不统一

问题：PDF、Word、PPT 和图片的页码、标题、图片和表格结构不同，后续检索逻辑容易写成大量文件类型分支。

解决：所有解析器统一输出 `DocumentBlock`，把文件差异收敛到 Parser 层。Chunk、Embedding 和 Milvus 只依赖统一字段。

可验证结果：图片、PDF、DOCX、PPTX 解析测试覆盖已通过，全套后端单元测试结果为 `206 passed`（含注册表 MySQL 持久化、Agent 工具的 fake gateway、Phase 2 组织/校验/画像/会话/降级兜底、Phase 2C 调查报告/压缩/画像进化测试、Phase 4 评估指标纯函数、忠诚度 judge 纯函数）。

### 难点二：公式容易被普通 OCR 破坏

问题：普通 OCR 对上下标、分式、积分和根号的识别不稳定，直接把公式当普通中文文本会影响召回。

解决：Office 原生公式走 OMML 到 LaTeX；图片和扫描 PDF 使用公式识别模型；同时保存原图路径，方便复核。

当前可以在面试中说的事实：公式模型已成功加载并使用 CPU 推理，避免和本地大模型争抢显存。

### 难点三：本地 Qwen 与 OCR 模型显存冲突

问题：RTX 4060 Laptop GPU 约 8GB。Qwen、PaddleOCR 和公式模型同时占用 GPU 时会出现显存不足，甚至导致 Ollama 的 `llama-server` 崩溃。

解决：OCR 和公式识别切换到 CPU，最终问答统一使用第三方 OpenAI 兼容 API。这样视觉模型和文本模型都不再依赖本地 Qwen 显存。

结果：三个回答模型均已实际调用成功，Ollama 不再是项目启动依赖，当前 GPU 不需要加载 Qwen。

### 难点四：旧版 `.doc` 和 `.ppt` 解析

问题：`python-docx` 和 `python-pptx` 主要处理 Office Open XML，旧版二进制格式不能直接稳定读取。

解决：检测到旧格式时，通过本机 Microsoft Office COM 转换为 `.docx` 或 `.pptx`，再复用统一解析器。Worker 运行机器需要安装 Office，这是明确的运行前提。

### 难点五：旧 Collection schema 与新多模态字段不兼容

问题：旧 Collection 只有文本和向量，新版本需要 `content_type`、`bbox`、`confidence` 和 `metadata`。直接复用旧 Collection 会导致字段缺失。

解决：启动和 Pipeline 初始化时检查 schema。缺少必需字段时返回明确错误，要求使用新 Collection 或删除旧 Collection 后重新入库，避免静默返回错误结果。

### 难点六：模型切换不能把密钥放到 Vue

问题：如果前端直接根据选择拼接 API 地址或携带 API Key，构建产物、浏览器开发者工具和网络请求都可能泄露密钥。

解决：前端只提交模型 ID，后端使用固定白名单和服务端环境变量解析实际地址、密钥和模型配置。模型列表接口只返回 `ready` 状态。

### 难点七：用户不应该记住文件名

问题：Collection 名称和自动生成的 document ID 对用户没有知识意义。

当前解决：`/api/v1/chat/agent` 的意图路由发生在检索之前，把文档目录（文件名 + 中文主题）交给模型，让模型决定查全库还是指定资料，返回 `scope` 和 `document_ids`，并给出选择理由。路由结果通过响应里的 `retrieval.router` 返回，前端可展示“已使用资料”并允许展开修正。

已实现（Phase 2）：用户画像（关注科目）拼进路由 prompt，让路由偏向当前学习科目；多轮问答历史经 `conversation_id` 传入 chat_history，上下文随轮次累积，超过阈值自动压缩成滚动摘要（上下文有界）。前端体验（Phase 5）：问答状态提升到 Pinia store，会话按用户持久化——同一次登录内切换栏目、刷新页面自动从后端恢复进行中的会话，历史会话列表（预览 + 点开续聊）按用户隔离、同一问题不会重复问；**每次登录是全新空会话，历史只能主动点选进入**（登录/登出清 `rag_chat_{userId}` 本地引用，后端历史保留）；画像进化已把问答后的薄弱点增量写回画像并沉淀为长期记忆（画像页可见）。

**会话持久化阶段 live 验证抓出的两个真 bug（回答"怎么测出来的/怎么修的"用）**：
1. `ChatView` 的 `<template v-for="turn in turns">` 没按 role 分流，每轮同时渲染 user 和 assistant 两个气泡 → 每条消息界面出现两次（用 Playwright 断言气泡数时发现 turns=2 但只有 1 个回答还没回来，定位是模板问题）。修：`v-if="turn.role === 'user'"` / `v-else`。
2. 历史回放崩溃：后端 `stage_persist` 落库 `metadata_json.sources` 只存 `{document_id, filename, score}`，刷新/点开历史时 `SourceList` 渲染要 `content_type`，`content_type.includes()` 对 undefined 抛 TypeError → Vue 渲染中断，页面只显示 1 条消息。修：SourceList 空安全（兼容旧数据）+ 后端改落完整 `_serialize_source(item)`（try/except 降级最小来源）。教训：**持久化字段必须等于渲染消费字段，旧数据要防御**。

---

## 12. 指标如何测量

不能为了让项目听起来更好而编造指标。当前已经有以下可验证结果：

| 指标 | 当前结果 | 证据 |
| --- | --- | --- |
| 后端单元测试 | `206 passed` | `pytest backend/tests -q`，覆盖注册表 SQLite mock、Agent 工具 fake gateway、Phase 2/2C 组织与画像、Phase 4 评估指标纯函数、忠诚度 judge 纯函数 |
| 检索评估集 | 74 题 × 4 路对比 | `python scripts/eval_retrieval.py`，纯向量 / 纯 BM25 / 原始 RRF / 生产路径，Recall@1/3/5 + Precision@1/3 + MRR；43 题基线 + 74 题扩容结果见 `data/eval/results_new.json`、`results_74.json` |
| 答案忠诚度自动评估 | 43/43（零人工标注） | `python scripts/eval_faithfulness.py`，RAGAS 风格 LLM-as-judge，结果见 `data/eval/faithfulness.json` |
| 跨文档关系型评估 | 30 题单遍 vs ReAct | `python scripts/eval_relational_react.py`，单遍 all_docs@5=0.533 vs ReAct 证据 0.833，证据级闭环 86.7%（26/30）、单遍∪ReAct 联合闭环 96.7%（29/30），残留仅 rq021（3.3%）→ LightRAG 不需要，见 `data/eval/results_relational_react_fixed.json`；问题扩展在精确 30 题与口语化题集（`questions_broad.jsonl` 9 题——8 道有明确主题的跨文档拼接题、仅 bq001 真模糊，单遍 all_docs@3=0.0 全失效）上复测均无净收益（精确闭环 26→25、口语化 8/9→7/9，扩展只省工具调用却以丢覆盖为代价），`QUERY_EXPANSION_ENABLED` 保持默认关 |
| 评估对比报告 | 切片前后 + 重排前后 + 参数敏感性 + 评估集扩容 | 三指标一览 / 切片升级前后 / 重排前后 / 参数敏感性分析 / 43→74 扩容调参 / 逐题明细，见 `data/eval/report.md` |
| Python 编译检查 | 通过 | `python -m compileall -q backend` |
| FastAPI 健康检查 | HTTP 200 | `/api/v1/health` |
| 模型切换 | 3 个模型实际调用成功 | Terra、Luna、DeepSeek V4 Flash 逐一请求 |
| 前端生产构建 | 通过 | `npm run build` |
| 前端响应式检查 | 桌面和移动端无横向溢出 | Playwright 检查 |
| 切片接口 | 可返回 Chunk 和来源字段 | `/api/v1/documents/{id}/chunks` |

### 12.1 检索质量评估方案

已建立 43 条问题的评估集（`data/eval/questions.jsonl`），全部来自库内真实内容，每题标注唯一 `document_id`（文档级）。当前用**四路对比**测量：

```text
vector      纯向量（cosine，跨库可比）
bm25        纯 BM25（BM25Plus）
rrf         原始 BM25+向量+RRF 跨库 max 合并
production  真实生产链路（_federated_search：路由门控 + relevance 重排 + top_k）
```

#### Recall@K

前 K 个结果里是否出现任意一个人工标注的相关 Chunk：

```text
Recall@K = 命中相关问题数 / 问题总数
```

分别比较纯向量、纯 BM25、原始 RRF 与真实生产链路，以及不同 Chunk 策略。

#### MRR

如果第一个正确结果排得更靠前，MRR 更高：

```text
MRR = 平均值(1 / 第一个相关结果的排名)
```

#### 当前实测结果（43 题，真实库，切片升级后新索引）

```text
Variant      Recall@1     Recall@3     Recall@5      MRR       Prec@1
vector       0.9302       0.9767       0.9767      0.9507      0.9302
bm25         0.8140       0.9302       0.9302      0.8661      0.8140
rrf          0.4651       0.8837       0.9535      0.6868      0.4651
production   0.9767       0.9767       1.0000      0.9826      0.9767
```

结论与面试要点：

- **纯向量是最强单通道**（cosine 跨库可比），Recall@1=0.930；纯 BM25 靠字面命中稍弱（0.814）。
- **原始跨库 RRF 有"榜首平局"缺陷**：每个库的 rank-1 RRF 分挤在同一量级，跨库合并后前几名在各库榜首之间近似随机，Recall@1 只有 0.465。
- **真实生产链路（RRF + 路由门控 + relevance 重排）把 Recall@1 拉到 0.977、MRR 0.983**，成为四路最优——重排环节是承重的，不是可有可无。

#### 切片策略升级前后对比（同一 43 题、同一批文档）

2026-08 对切分做了全面升级（清洗层 + MD/HTML 结构重建 + PDF 版面识别 + PPT 幻灯片单元 + Excel 表格入库 + 图片公式上下文绑定）。`results.json`（8-07 旧索引快照）vs `results_new.json`（新索引重跑）：

```text
Variant      Recall@1（旧→新）     MRR（旧→新）
vector       0.907 → 0.930         0.948 → 0.951
bm25         0.837 → 0.814         0.867 → 0.866
rrf          0.419 → 0.465         0.626 → 0.687
production   0.930 → 0.977         0.954 → 0.983
```

- **结构化切分的增益可量化**：生产链路 Recall@1 0.930→0.977、MRR 0.954→0.983——标题/表格/代码块以更完整语义单元入向量，端到端检索质量显著提升。
- 诚实的细节：纯 BM25 微降（0.837→0.814），因为结构化重建后 chunk 变大、字面密度稀释；但端到端链路（向量为主 + 重排）大幅受益。**单通道和端到端要分开看**——这是个能体现思考深度的点。

#### 重排机制前后对比（同一索引内）

```text
链路                      Recall@1      Recall@3       MRR
无重排（原始 RRF）          0.4651        0.8837        0.6868
有重排（路由+relevance）    0.9767        0.9767        0.9826
```

- 原始 RRF 的「榜首平局」是固有缺陷，重排层（`_federated_search` 路由门控 + `_relevance_score` 可解释打分）把 Recall@1 从 0.465 拉回 0.977——**重排是检索质量承重的关键环节**。

#### 评估集扩容（43 → 74）与权重调优

43 题下生产链路 Recall@1=0.977 接近天花板，区分度不足——看不出参数好坏。扩到 74 题（新增 31 题覆盖数据结构/Go 语法/数据类型/常量，`questions.jsonl` q101-q131）后用四路重跑：

```text
Variant      Recall@1     Recall@3     Recall@5      MRR       Prec@1
vector       0.9324       0.9730       0.9730      0.9520      0.9324
bm25         0.8243       0.9189       0.9324      0.8719      0.8243
rrf          0.4054       0.8784       0.9595      0.6492      0.4054
production   0.9595       0.9865       1.0000      0.9741      0.9595
```

- **扩题暴露了真实短板**：74 题下旧权重 0.55/0.35/0.10 的 production Recall@1 掉到 0.932——不是系统变差，而是新增的「类型定义/语义相似文档」类题目跨文档混淆，43 题覆盖不到。
- **用敏感性分析量化权重收益**：扫描 relevance 打分权重，0.65/0.25/0.10（强化语义主导、降低字面词项）在 74 题下把 Recall@1 从 0.932 拉回 **0.9595**、MRR 0.9741，43 题下持平（0.9767 不变）——已落到生产默认值。43 题下两权重无差异，说明**评估集容量不够会掩盖调参收益**，这是先扩题再调参的原因。

#### Answer faithfulness（答案忠诚度，RAGAS 风格，零人工标注）

用 LLM-as-judge 把答案拆成原子事实断言，逐条对照检索来源判断支持性（supported / partial / unsupported，权重 1 / 0.5 / 0），忠诚度 = 被支持断言的加权占比。脚本 `scripts/eval_faithfulness.py`，全程无需人工标注，可扩展到任意规模。

- 43 题全评：平均 **0.865**；fully_grounded(≥0.9) 占 53.5%，grounded(≥0.7) 占 79.1%。
- 与检索命中交叉：43/43 全部命中（expected 文档进入 top sources），说明低分不是"没召回到"，而是"答案从来源外补了细节"。
- 能逐题暴露真实薄弱点：如 q012「反转单链表用哪三个指针」只得 0.438——来源只提三指针法、没写明各指针语义，答案靠模型常识补全被判 unsupported。**这证明 judge 不是走过场，评估是真的能定位问题**。

#### 工程指标

- 文档解析成功率。
- OCR 失败回退率。
- 入库任务成功率。
- 入库 p50 和 p95 延迟。
- 问答 p50 和 p95 延迟。
- 单个问题输入 Token、输出 Token 和 API 成本。
- Worker 重试后的最终成功率。

### 12.2 面试中如何表达指标

现在已经有评估集，可以这样回答：

> 当前我已验证完整链路和工程稳定性，自动化测试为 206 个用例，覆盖文档注册表的 MySQL 持久化、Agent 检索工具的 fake 网关测试、班级学习库的上传校验/画像注入/会话持久化/可见性过滤/MySQL 降级兜底、Phase 2C 的调查报告/压缩/画像进化、评估指标纯函数和忠诚度 judge 纯函数。检索质量我用一个 43 题的评估集（全部来自库内真实内容，每题标注正确答案所在文档）做了四路对比：纯向量 Recall@1=0.930，纯 BM25 为 0.814，原始跨库 RRF 只有 0.465——因为 RRF 的 rank-1 分在跨库合并时挤在同一量级，跨库无法区分榜首。真实生产链路（RRF + 路由门控 + relevance 重排）把 Recall@1 拉到 0.977、MRR 0.983，是四路最优。

我又用同一评估集做了两次对照实验：一是**切片策略升级前后**（同一批文档旧索引 vs 结构化切分后新索引），生产链路 Recall@1 从 0.930 升到 0.977、MRR 从 0.954 升到 0.983；二是**重排机制前后**（同一索引内原始 RRF vs 加上路由+relevance 重排），Recall@1 从 0.465 拉回 0.977。两组对比分别量化了"切片怎么切"和"重排层有没有必要"两个决策的收益，都不是拍脑袋说的。

答案质量我用 RAGAS 风格的自动评估（LLM-as-judge，**零人工标注**）：把答案拆成原子事实断言、逐条对照检索来源判断支持性，43 题全评，平均忠诚度 0.865。它能逐题暴露真实薄弱点——比如"反转单链表用哪三个指针"这道题只有 0.44 分，因为来源只提了三指针法、没写明各指针语义。这样检索和答案各有独立指标，调检索参数时两个都能量化收益。

我又把评估集从 43 题扩到 74 题来提升区分度——43 题时生产链路 Recall@1 已经 0.977，接近天花板看不出参数好坏。扩题后旧权重掉到 0.932，暴露了「类型定义/语义相似文档」的跨文档混淆；我再用敏感性分析扫描 relevance 打分权重，把语义权重从 0.55 提到 0.65，74 题下 Recall@1 回到 0.9595、43 题下持平，这个收益正是扩题后才看得见的。所以我的调参方法是先扩评估集、再量化收益，不是凭感觉调。

跨文档关系型问题我用专门的题集测（30 道必须拼接 2~3 份资料的问题，每题标注多个期望文档）：单遍检索 all_docs@5 只有 0.533——入口几乎总能找到（mrr 0.934），但拼接材料经常凑不全。这正好是 LightRAG/GraphRAG 声称要解决的缺口，我实测确认它真实存在。然后我让真实 ReAct 链路（最多 4 次工具调用）做补检，把证据级 all_docs@5 拉到 0.833、单遍∪ReAct 联合闭环 96.7%（29/30），真正残留只有 1 个文档（rq021，3.3%），逐题归因后属于题设边缘效应而不是图连通性需求，所以结论是当前架构不需要 LightRAG。我还专门测了问题扩展——LLM 把模糊问题拆成子问题做多路召回。第一版评估脚本其实漏了把扩展真正注入检索（只记录不注入），修正后才拿到有效数据：精确 30 题闭环 26/30→25/30，唯一残留 rq021 的恢复是 ReAct 随机补检的功劳、真正用到扩展的那题 rq024 反而丢闭环；又建了一套 9 道口语化问法题，其中 8 道是「有明确主题但要跨文档拼接」（跟我 30 题关系型题集同类），只有 bq001「go语言怎么学」才是真模糊开放式。这类题单遍检索完全失效（all_docs@3=0.0），但 ReAct 自主补检就能做到 8/9 闭环，加扩展反而降到 7/9——扩展唯一收益是省工具调用，代价是 bq004 这种拼接题被扩展拆漏子主题、agent 提前停手丢文档——扩展子问题被 ReAct 自己的自主补检吸收，净收益为零，所以这个开关保持默认关闭。而真模糊题「怎么学」这种，答案其实主要在文档库外的教学法，硬检索和扩展都不对，正解是识别模糊后反问用户——那是下一步的澄清门控。这就是我验证新技术的方法：先把它声称擅长的题型构造出来、把题集扩到能稳定测量、再定位缺口在召回层还是规划层，而不是看到新技术直接装上去。

强调：这些数字是我在真实库、43/74 条真实问题上实测出来的，不是假设。

---

## 13. 高频追问与参考回答

### Q：为什么不用 Elasticsearch 直接做全文和向量检索？

当前项目规模是个人学习工具，Milvus 对向量检索路径更直接，BM25 在应用层维护也足够。Elasticsearch 更适合已经有成熟全文检索、过滤、聚合和运维体系的团队。两者都可以，关键是根据规模和团队能力选择，而不是说某个数据库绝对更好。

### Q：为什么不用 FAISS？

FAISS 是优秀的本地向量检索库，但持久化、服务化、过滤和集合管理需要自己补很多工程能力。Milvus 提供服务化 Collection、索引和加载能力，适合把向量检索作为独立基础设施。个人原型也可以先用 FAISS，项目升级后再切 Milvus。

### Q：为什么不用 Mem0 这类记忆框架？

Mem0 是通用记忆层：把记忆抽取/存储/检索做成服务（add 时用 LLM 抽取，查时做语义 + BM25 + 实体多信号召回），自带时间感知、记忆衰减、user/session/agent 多级隔离。它解决的是「跨很多天的用户事实，模糊描述也能精确召回」这类检索式记忆。

本项目没用的原因：

1. **记忆形态不同**。本项目是「结构化画像 + 可审计记忆」：`user_profiles` 存科目/薄弱点/偏好风格（断言式），`user_memory` 存画像进化自动写的行为观察（memory_type 区分 fact/personality/preference，可查可删）。这是学习库场景需要的形态——老师要看到「这个学生薄弱在哪」的可审计记录，不是不可解释的向量记忆。
2. **引入成本**。Mem0 自带向量存储（Qdrant/Chroma 等），接了就是第二个向量层；它依赖 LLM 做抽取和检索，端到端要重新验；和现有 `profile_evolution` 是两套并行记忆，维护面翻倍。
3. **当前没有「模糊检索历史事实」的体验需求**。班级学习库是几十人规模，多轮上下文 + 结构化画像已覆盖。为一个尚不存在的痛点买基础设施，违背简单优先。

什么时候才值得上：需要跨会话模糊召回用户事实、或让 LangGraph 等外部框架直接查这套记忆时。正确姿势是 `Mem0Client(vector_store=Milvus)` 复用已有 Milvus 做检索索引，同时保留现有审计表，而不是替换。

表达重点：不是「不会接」，而是「能说清什么形态的记忆适合什么场景」——断言式画像适合可审计的班级教学，检索式记忆适合大规模个性化，两者是取舍不是替代。

### Q：为什么 Embedding 不用大模型？

Embedding 模型的目标是稳定地表示文本语义，不等于生成模型越大越好。中文个人项目优先考虑本地可运行、延迟和向量质量的平衡。BGE-small-zh-v1.5 足够轻量，后续可以用评估集验证是否值得换更大的模型。

### Q：LangChain 在哪里？

需要区分两条链路：

- 入库链路（解析、OCR、分块、向量化）是确定性 pipeline，由 FastAPI + Redis Worker 直接编排，不使用 LangChain——为纯 I/O 编排引入框架是没必要的抽象。
- 问答链路是"四段 workflow，内嵌 ReAct Agent"：整条链路是意图识别、ReAct 检索推理、落库+自动压缩、画像更新四段。只有 ReAct 检索段用了 LangChain 1.x 的经典 `AgentExecutor`（`create_tool_calling_agent`，来自 `langchain-classic`），把现有混合检索封装成 `search_library` / `search_documents` 两个工具，模型在 Thought→Action→Observation 循环里决定检索范围、补检索和作答；其余三段是单次 LLM 调用或确定性 I/O，不属于 agent。不用 LangGraph，是因为这个场景只需要单轮问答加少量工具调用，`AgentExecutor` 足够，不想为持久化图状态引入额外复杂度。

准确的说法是：多模态解析和异步入库不依赖 LangChain，Agentic 问答这一层用了 LangChain。

### Q：为什么不把图片向量直接存到向量库？

当前方案是图片先经过 OCR、公式识别和视觉理解，生成可检索文本，再用中文 Embedding 入库。优点是工程轻量、来源解释容易、适配纯文本问答模型。缺点是会损失部分原始视觉关系。后续如果需要真正的图像相似度检索，可以增加图像 Embedding 字段，形成文本向量和图像向量的双路检索。

### Q：如何处理查询中的专业术语和错别字？

BM25 负责专业词、题号和公式的精确命中，向量检索对表达变化更宽容。查询改写可以作为可选能力，但不能让改写结果替代原问题，否则可能丢失题号、变量名和公式字符。稳妥方式是原查询和改写查询并行召回，再融合。**（Phase 2.2 已落地：改写后问题为主路、原问题为副路并行召回，按 `(collection, chunk)` 取两路高分融合；改写未变化时副路自动跳过。Phase 3.2：改写阶段已接多轮对话上下文，能消解"他/这个/刚才"等指代——"他的变量声明"结合上文"go语言的数据类型"改写为"go语言的变量声明"；broad 问题还可由 LLM 扩展成多个检索子问题做 N 路 max-score 融合召回，`QUERY_EXPANSION_ENABLED` 默认关——在精确 30 题与口语化题集（9 道，8 道为有明确主题的跨文档拼接题、仅 bq001 真模糊）上分别做了增量评估均无净收益（精确闭环 26→25、口语化 8/9→7/9，扩展只省工具调用却以丢覆盖为代价），且这类题单遍检索完全失效（all_docs@3=0.0）必须靠 ReAct 自主补检，扩展子问题被 ReAct 吸收，故保持默认关。）**

### Q：上下文过长怎么办？

先控制召回数量，再做去重和压缩：

1. 使用固定的 `top_k`。
2. 相同来源或高度重复 Chunk 去重。
3. 对每个 Chunk 保留标题路径和必要正文。
4. 依据模型上下文窗口设置总字符或 Token 上限。
5. 超出预算时优先保留高融合分数、双路命中的 Chunk。

不能无限增加 top_k 来解决召回问题，因为上下文越长不代表答案越准。

本项目已落地三层：召回证据预算 `MAX_EVIDENCE_TOKENS=4000` / 最终合成 `8000`（token 口径）；对话历史上下文 3000 token 硬预算（超限丢最旧、最新一条强制保留）；配合滚动摘要（旧消息折叠进 ≤500 字摘要），发给模型的上下文总量有界——对话再长也不会无限增长。

### Q：系统里有哪些异常？对应怎么处理？

总原则三条：
1. **每一处外部依赖都可能失败，每一处都有兜底**（文本模型 / 视觉 / MinerU / OCR / MySQL / Redis），失败绝不静默、也不把引擎报错串直接返回给用户。
2. **两类策略互补**：前置约束（token 预算、输入校验）让异常"走不到"；失败兜底（回退 / 降级 / 强制作答）保证"真失败了也有可用输出"。前置守卫不替代异常处理，两者都要有。
3. **fail-closed 优先**：权限相关一律拒绝而非放行，宁可 503 也不降权放行。

| 环节 | 异常 | 处理方式 |
|---|---|---|
| 改写 | 改写 LLM 失败 / 空 / 异常长度 | 回退原问题，不破坏链路 |
| 扩展 | 扩展 LLM 失败 | 返回 `[]` 跳过扩展路 |
| ReAct | 迭代超限被截断（output="Agent stopped..."） | 用已检索证据强制作答；无证据明确答"资料中没有找到"，绝不把引擎报错串返回 |
| ReAct | 工具调用偶发解析异常 | `handle_parsing_errors=True`，不崩 |
| Prompt 组装 | 模型生成的理由/改写含 `{}`（如 `interface Run() {}`） | `_escape_prompt_braces` 转义，防 ChatPromptTemplate 模板解析抛错 |
| 上下文 | 对话超 14 条消息 | 滚动摘要压缩：旧消息折叠进 ≤500 字摘要，只留最近 8 条原文，DB 消息数有界 |
| 上下文 | 压缩摘要生成失败 | 保留原消息，不丢数据 |
| 上下文 | 历史/证据超预算（防爆窗） | 证据 4000/8000 token 预算；历史上下文 3000 token 硬预算，超限丢最旧、最新一条强制保留 |
| 证据 | 检索证据不足 / 模糊 / 无证据 | 澄清门控：反问用户澄清后再走正常链路，澄清轮不落库不进画像 |
| 鉴权 | MySQL 不可用 + token 已验签 | 降级合成身份（degraded），个人问答可用；require_admin/head 一律 503 fail-closed，绝不升级权限 |
| 鉴权 | token 无效/过期/用户被删 | 统一 401（用户被删不与"无效"区分，防账号存在性探测） |
| 限流 | 单用户请求过频 | 429 |
| 上传 | 同内容重复上传（sha256 相同） | 409 + 清理本次孤儿文件 + 返回已有记录状态与驳回备注 |
| 上传 | 文件超 50MB / 不支持类型 | 400 |
| 安全 | 资料内嵌提示词注入指令 | 上传校验 `prompt_injection` 命中即强制驳回；讨论该主题的教材例外 |
| 解析 | native PDF 提取失败 | 逐页回退 PyPDF |
| 解析 | MinerU 不可用/超时/空结果 | 回退逐页 OCR 路线，不阻塞入库 |
| 解析 | 视觉模型失败 | 保留 OCR/文本结果继续入库、记录失败信息，图片不成为唯一数据源 |
| 指标 | LLM 未返回 usage | token 计数记 0，不影响链路 |

### Q：如何防止 Prompt Injection？

学习资料可能包含“忽略系统要求”之类文本。检索内容必须被视为不可信资料，Prompt 需要明确区分系统规则、用户问题和参考资料。模型只能从资料中抽取事实，不能执行资料中的指令。更严格的系统还要对外部内容做安全分类和输出审计。

### Q：如何处理并发上传？

上传接口使用唯一 document ID 和唯一 task ID，文件名不作为保存路径。Redis 队列保证任务进入待处理队列，Worker 可以从单实例逐步扩展为多个消费者。Milvus 写入需要按任务和 Collection 做隔离，避免同一个 Collection 的并发重建和增量写入互相覆盖。

### Q：为什么前端使用轮询，不使用 WebSocket？

当前任务状态是低频变化的，1 到 2 秒轮询实现简单、断线后容易恢复、部署成本低。WebSocket 适合高频实时事件和大量在线用户，但会增加连接管理、重连和广播复杂度。个人工具先使用轮询更合理，后续可以用 SSE 作为中间方案。

### Q：如何删除一份资料？

删除操作需要同时处理：

- 上传目录中的原始文件。
- 原始图片和工作目录。
- Milvus 对应 Collection 或工作区 Collection 中的过滤记录。
- 文档画像和任务记录。

如果只删文件不删向量，系统仍然可能回答已经不存在的资料，这是数据一致性问题。

### Q：系统的主要瓶颈是什么？

个人场景下通常是：

1. 外部视觉模型调用延迟。
2. PDF 页面渲染和 OCR。
3. Embedding 批量计算。
4. Milvus Collection 加载。
5. 最终文本模型响应时间。

优化顺序应该从指标出发：缓存重复文件、批量 Embedding、异步处理、限制视觉调用、控制上下文和对 Collection 做合理加载，而不是一开始就引入复杂分布式系统。

---

## 14. 手写解释模板

### 14.1 手写余弦相似度

```python
import math


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vector dimensions do not match")

    dot_product = sum(x * y for x, y in zip(left, right))
    left_norm = math.sqrt(sum(x * x for x in left))
    right_norm = math.sqrt(sum(y * y for y in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot_product / (left_norm * right_norm)
```

解释顺序：

1. 先检查维度。
2. 计算点积。
3. 计算两个向量的 L2 范数。
4. 用点积除以范数乘积。
5. 零向量直接返回 0，避免除零。

### 14.2 手写 RRF

```python
from collections import defaultdict


def reciprocal_rank_fusion(result_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = defaultdict(float)
    for results in result_lists:
        for rank, document_id in enumerate(results, start=1):
            scores[document_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
```

解释：不比较 BM25 和向量原始分数，而比较它们的排名。一个结果出现在多路召回中，会累加多个倒数排名分数。

### 14.3 手写滑动窗口分块

```python
def split_with_overlap(text: str, chunk_size: int, overlap: int) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    chunks: list[str] = []
    step = chunk_size - overlap
    start = 0
    while start < len(text):
        piece = text[start:start + chunk_size].strip()
        if piece:
            chunks.append(piece)
        start += step
    return chunks
```

解释：步长等于窗口大小减去重叠大小。重叠可以减少知识点刚好被切在边界时的信息损失，但过大时会造成重复向量和上下文膨胀。

### 14.4 手写异步任务状态机

```python
from dataclasses import dataclass


@dataclass
class TaskState:
    status: str = "PENDING"
    stage: str = ""
    progress: int = 0
    error_message: str = ""


def mark_stage(task: TaskState, stage: str, progress: int) -> TaskState:
    allowed = {"PARSING", "OCR", "CHUNKING", "EMBEDDING", "INDEXING", "DONE"}
    if stage not in allowed:
        raise ValueError(f"unknown stage: {stage}")
    task.stage = stage
    task.progress = max(0, min(progress, 100))
    task.status = "SUCCEEDED" if stage == "DONE" else "PENDING"
    return task
```

面试中要补充：真实项目的状态写入 Redis，状态机还要处理异常、重试和 Worker 崩溃，不只是内存中的 dataclass。

### 14.5 手写安全模型选择

```python
import os


MODEL_CONFIGS = {
    "gpt-5.6-terra": {"api_key_env": "LLM_API_KEY", "base_url_env": "LLM_BASE_URL"},
    "gpt-5.6-luna": {"api_key_env": "LLM_LUNA_API_KEY", "base_url_env": "LLM_LUNA_BASE_URL"},
    "deepseek-v4-flash": {
        "api_key_env": "LLM_DEEPSEEK_FLASH_API_KEY",
        "base_url_env": "LLM_DEEPSEEK_FLASH_BASE_URL",
    },
}


def resolve_model(model_id: str) -> dict[str, str]:
    if model_id not in MODEL_CONFIGS:
        raise ValueError("unsupported model")
    config = MODEL_CONFIGS[model_id]
    return {
        "api_key": os.environ[config["api_key_env"]],
        "base_url": os.environ[config["base_url_env"]],
        "model": model_id,
    }
```

真正实现时不能把 `api_key` 放进返回给前端的 JSON，模型解析只应在后端发生。

---

## 15. 面试演示流程

### 15.1 演示顺序

1. 打开资料入库页，上传一张截图或一个 PDF。
2. 展示任务从解析、OCR、分块到完成的状态变化。
3. 打开切片检查页，展示 Chunk 内容、页码、OCR 框和 metadata。
4. 打开问答页，输入一个具体问题。
5. 展示混合检索和生成状态。
6. 展示回答中的来源片段和召回路径。
7. 切换到另一个已经配置的文本模型，说明前端只提交模型 ID，密钥在后端。
8. 进入 Swagger 展示 `/api/v1/models`、`/api/v1/retrieval/search` 和 `/api/v1/chat/ask`。
9. 演示 `/api/v1/chat/agent`：提交一个需要判断查哪份资料的问题，展示响应里的 `retrieval.router`（scope、document_ids、rationale）和 `trace`（工具调用链），说明 Agent 先路由、再检索、再作答。

### 15.2 推荐演示问题

不要问“这份资料讲了什么”这种太宽的问题，建议使用：

- “这份资料中泰勒展开的使用条件是什么？”
- “这道错题为什么不能直接使用洛必达法则？”
- “事务隔离级别中幻读和不可重复读有什么区别？”
- “请指出这个结论来自哪一页，并列出原文依据。”

这些问题可以同时展示专业术语、语义检索和来源定位。

### 15.3 演示时不要说的话

- 不要说“支持所有格式”，当前支持的是明确列出的格式。
- 不要说“完全不会幻觉”，只能说有上下文约束和来源复核。
- 不要说“Recall 提升了 30%”，除非有评估集和实验记录。
- 不要说“LangChain 完成了全部流程”，只有 Agentic 问答（`/chat/agent`）用了 LangChain，多模态解析和异步入库链路仍是直接编排。
- 不要说“模型直接理解所有图片”，当前最终问答主要使用解析后的文本上下文。
- 不要把开发环境中的示例资料数量当成系统容量指标。

---

## 16. 项目当前边界与下一步

### 当前已经完成

- 多模态文件解析骨架。
- 图片、手写、PDF、DOCX、PPTX 解析。
- 旧版 DOC/PPT 的 Office 转换。
- OMML 公式转 LaTeX。
- 图片和扫描 PDF 公式识别。
- BM25 + Milvus 向量混合检索。
- Redis 异步入库和任务状态。
- 来源字段、切片检查接口和 Vue 3 工作台。
- 三个文本模型的安全切换。
- 文档注册表迁移到 MySQL documents 表（SQLAlchemy），API 与 Worker 双进程写入由数据库事务兜底，替代原 JSON 文件 + 进程内锁。
- Agentic 问答：`/chat/agent` 用 LangChain AgentExecutor，先意图路由（scope / document_ids / rationale）再 ReAct 多轮检索，返回 `retrieval.router` 和工具调用 `trace`。
- **Phase 2 班级学习库（后端完成）**：轻量身份（`X-User-Id`，无密码，非安全设计）区分老师/学生；老师与学生共享文档库、都能上传，每次上传经校验 agent 审核（`uploads` 表 status 流转），驳回标记隐藏但保留、管理员可放行/删除；管理员审计后台看"谁传了什么 + 校验结果"；用户画像（科目/薄弱点/偏好风格）注入 `/chat/agent` 改变回答形式；会话持久化（conversations / messages / agent_traces）支持多轮上下文。
- **Phase 2C（完成）**：三级权限（班主任/老师/学生，创建者即班主任，堵住老师建老师越权）、学生首次调查报告（一次性三步调查）、画像进化（长回答后 LLM 判断行为/薄弱点/风格，自动更新画像）、对话自动压缩（滚动摘要 + 保留最近 8 条原文）、前端打磨（去英文 eyebrow，身份中文标签）。
- **Phase 3（完成）**：问答四段 workflow 显式化 + 检索前查询改写 + 显式证据充分性门控。`/chat/agent` 拆成 intent / react / persist / profile 四段，改写问题注入 ReAct 段检索、executor 外圈做前置探针（scope=auto 且路由 selected 时 0 命中 → 升级全库）与后置证据判定（no_evidence / weak_evidence / sufficient）；响应新增 `retrieval.rewritten_question` / `retrieval.evidence` / `retrieval.stages`。开关 `QUERY_REWRITE_ENABLED`。详见 INTERVIEW_PREP.md 3.6 与 memory。
- **Phase 4（完成）**：检索评估集。43 题文档级评估集 + `scripts/eval_retrieval.py` 四路对比（纯向量 / 纯 BM25 / 原始 RRF / 生产链路），产出 Recall@1/3/5 + Precision@1/3 + MRR。
- **评估集扩容 43→74 + 参数敏感性（2026-08-10，完成）**：扩到 74 题提升区分度（43 题 production R@1=0.977 接近天花板）；`scripts/eval_sensitivity.py` 扫描 `_federated_search` 五个参数，确认 semantic_min 是唯一敏感维度（0.48→0.55 时 R@1 0.977→0.930）、当前参数已局部最优；扩题暴露旧权重 0.55/0.35/0.10 在 74 题下掉到 0.9324，**权重调优到 0.65/0.25/0.10（已落生产默认）**，74 题下 R@1 回到 0.9595、43 题下持平。
- **答案 groundedness 评估（2026-08-08）**：`scripts/eval_groundedness.py` 用生产链路生成答案与 top sources。
- **答案忠诚度自动评估 + 对比报告（2026-08-10，零人工标注）**：`scripts/eval_faithfulness.py`（RAGAS 风格 LLM-as-judge）把答案拆原子断言逐条对照来源判断支持性，43/43 全评、平均 0.865；`scripts/eval_report.py` 合成 `data/eval/report.md`——三指标一览 + 切片升级前后（R@1 0.930→0.977）+ 重排前后（rrf 0.465→production 0.977）+ 参数敏感性 + 43→74 扩容调参 + 逐题明细。后端测试 `206 passed`。
- **Phase 1.1 真实鉴权（2026-08-10，完成）**：`Authorization: Bearer <JWT>`（HS256、7 天，无 refresh）替换轻量 `X-User-Id`。密码 bcrypt 哈希（72 字节截断，绕开 passlib 用原生 bcrypt 5.x），`users.password_hash` 列幂等迁移；无 header 一律 401（不再回退 u_admin）；无密码账号首登**引导式补设**（`/auth/login` 返回 scope=setup 短效 token 15min，`/auth/setup-password` 设密换正式 token，已设密 → 409 防"谁先设谁拥有"竞态）；`/auth/change-password` 验旧密；已删用户 401；DB 挂时已验签 token 降级为 `degraded` 身份、`require_admin`/`require_head` fail-closed 503。前端两步登录（login→setup）、建号可选初始密码、画像页改密。后端测试 `226 passed`，前端 `npm run build` 通过。
- **Phase 1.2 上传校验补提示词注入检测（2026-08-10，完成）**：上传校验 agent 之前只查"是否学习相关"，不查文档里藏的注入指令——补上纵深防御的入库前检测层。`ReviewDecision` 加结构化字段 `prompt_injection`，审核 prompt 显式要求检测「忽略你之前的指令/系统提示」「扮演系统管理员/开发者」「输出 system prompt 或隐私数据」「查看/泄露某人账号/密码/画像/记忆」「当用户问 X 时必须回答 Y」这类隐藏指令（命中即强制驳回，fail-closed 不依赖 approved 判定是否含糊），并给出「讨论该安全主题的教学文档不算注入」的例外规则防误伤安全教材；完整判定存 `uploads.review_payload`（此前空置的列）供审计后台查证据片段。运行时的 system prompt「最高优先级边界块」仍是主防线，这条是预处理层。后端测试 `228 passed`。
- **Phase 5（完成）**：前端会话持久化体验 + 历史会话列表 + 长期记忆可视化。问答状态提升到 Pinia store（`frontend/src/stores/chat.ts`，key `rag_chat_{userId}` 按用户隔离），切栏目/刷新自动恢复；历史会话列表（标题 + 最后消息预览，点开续聊/删除）；画像页「长期记忆」面板（`/users/me/memory`，可删除）。**登录会话语义**：登录后永远是全新空会话，历史必须主动点选进入；同一次登录内导航/刷新仍自动恢复；登出清本地引用。**消息顺序修复**：MySQL `FLOAT` 存 epoch 时间戳丢亚秒 → 平局 → 消息乱序，改 `DOUBLE` + 确定性次级排序键（同秒 user 在前）。后端测试 `132 passed`，前端 `npm run build` 通过。
- **Phase 2.1 运行指标（2026-08-10，完成）**：新增 Redis 计数的 `MetricsStore`（`rag/metrics.py`，worker 与 API 共用同一 Redis 聚合；Redis 连不上静默降级、一次失败即禁用避免反复重连）。worker 记录 `parse_ms`/`index_ms`、`docs_ingested`/`docs_failed`；OCR 失败率在共享 `media.py` 的 `analyze_media` 唯一咽喉点计数 `ocr_attempts`/`ocr_failures`（抛异常或返回空文本都算失败，vision 补救仍计失败）；`/chat/agent` 全链路挂 `TokenUsageCallback`（langchain BaseCallbackHandler，经 `with_config({"callbacks"})` 透传 rewrite/route/ReAct 循环/画像/压缩所有 LLM 调用）累加 `tokens_input`/`tokens_output`，并记录 `chat_total_ms` + 四段分阶段延迟（复用已有 stages ms）。`/admin/metrics`（require_admin）读快照：计数器 + 各延迟的滑窗 count/avg/p50/p95/min/max（LPUSH+LTRIM 保留最近 500 条）。实测一题：tokens 571 in/391 out，total 13.5s（intent 4.8/react 3.3/persist 0.1/profile 5.3）。后端测试 `235 passed`。**踩坑：`MetricsStore._disabled` 初值写成 `client is None`，生产 client=None 直接禁用、指标全空——被注入 fake client 的测试掩盖，补了真实连不上 Redis 的回退测试**。
- **Phase 2.2 双路召回（2026-08-10，完成）**：查询改写从"改写替换"升级为"改写后问题为主路 + 原问题为副路并行召回再融合"。理由：改写可能丢失题号、变量名、公式字符等精确词，副路补回。融合用 `_merge_dual`：按 `(collection, chunk_index)` 取两路更高分、再按分降序——**RRF 只适合合并同一查询内多路证据，两条独立查询链分数不可直接 RRF（各自分箱不同），按 chunk 取 max 是跨查询最稳的保底**；改写未变化时（`dual_question == question`）副路自动跳过，零额外开销。检索工具 trace 新增 `dual_recall` 字段可观测。开关 `DUAL_RECALL_ENABLED`（默认开）。后端测试 `238 passed`；live 冒烟：改写前后不同 → 全部 tool_call `dual_recall=True`，`used_chunks` 9→11→11→12 随 ReAct 轮次累积融合证据。
- **Phase 3.1 跨文档关系型评估 + 单遍/ReAct 对比 + LightRAG 决策（2026-08-11，完成）**：把关系型题集从 7 题扩到 **30 题**（`data/eval/questions_relational.jsonl` rq001-rq030，每题 2~3 个期望文档，全部来自库内真实内容：Go 跨概念桥接如切片×字符串/方法×接口/映射×结构体/函数×指针/循环×条件、数据结构与 Go 跨领域对比如哈希冲突/顺序表×切片/循环队列/反转链表×for 循环、北邮申请书×传感器 PPT 跨域 join），`metrics.py` 新增多文档纯函数（`doc_coverage_at_k`/`all_docs_at_k`/`first_expected_rank`/`evaluate_relational`/`aggregate_relational`），`scripts/eval_relational.py` 跑生产链路单遍检索，`scripts/eval_relational_react.py` 跑真实 ReAct 链路（`stage_intent`+`stage_react`，收集 `ctx.fused` 全部工具调用证据，带断点续跑），`eval_report.py` 加第六节。**扩容验证了"7 题样本太小"的假设**：单遍 all_docs@5 从 7 题的 0.5714 降到 30 题的 0.533，且只有扩到 30 题才能稳定做逐题归因、区分召回层与规划层缺口。**实测结论（可讲）**：单遍检索 all_docs@3=0.40、all_docs@5=0.533、mrr_any=0.934（入口几乎总能找到、但拼接材料经常凑不全）——关系型问题的跨文档缺口真实存在；**ReAct 补检把证据级 all_docs@3 拉到 0.833、all_docs@5 拉到 0.833、doc_coverage@3 0.694→0.922**，证据级闭环 86.7%（26/30），**单遍∪ReAct 联合闭环 96.7%（29/30）**——单遍漏掉的 13 个文档全部由 agent 补检找回；**真正残留只剩 1 个文档（rq021 反转链表三指针），占 3.3%**，且这题即使 agent 跑满工具调用也没找回，归因是"期望文档与其它已命中文档内容重复、答案自包含在已召回材料里"的题设边缘效应，不是图连通性缺口——**LightRAG 不需要**。ReAct 平均工具调用 3.8 次（成本约为单遍的 4 倍上下）。后端测试 `285 passed`。**方法论**：验证新技术卖点（图检索、GraphRAG）最扎实的方式不是装它，而是先把它声称擅长的题目类型构造出来、测当前系统的缺口在哪一层；题集要先扩到能稳定测量的规模，7 题会低估缺口。
- **Phase 3.2 改写上下文感知 + LLM 问题扩展（2026-08-11，完成）**：`rewrite_query(llm, question, chat_history=None)` 的 `_REWRITE_PROMPT` 增加最近对话上下文块，明确要求把指代词（他/它/这个/刚才）结合上文补全——实测"他的变量声明应该怎么写？"+ 历史"go语言的数据类型" → 改写为「go语言的变量声明应该怎么写？」（无历史时退化为"变量声明"）；`chat_agent` 把 `_prepare_conversation` 加载的历史同时传给 `stage_intent` 与 `stage_react`，改写阶段真正看到多轮上下文。新增 `expand_query(llm, question) -> list[str]`：broad 问题拆成多个检索子问题（实测"go语言怎么学" → [环境安装配置, 基本语法和数据类型, 变量与常量定义]），具体问题返回空列表避免噪音（"切片append扩容机制" → []）；检索融合从双路泛化为 N 路 max-score 融合 `_merge_queries(*lists)`（按 `(collection, chunk_index)` 取各查询最高分，`_merge_dual` 变薄封装），`build_tools` 对扩展查询各跑小 top_k 并注入每次工具调用，ReAct 补检同样受益。开关 `QUERY_EXPANSION_ENABLED`（默认 **false**）。改写返回进 `retrieval.rewritten_question`，扩展查询进 `ctx.tool_calls` trace。**增量评估（先披露测量缺陷：第一版 `eval_relational_react.py` 漏把 `expansions` 传给 `stage_react`，扩展只记录未注入检索，30 题复测结果无效；修正后重跑）**：精确 30 题（`results_relational_react_exp30.json` vs `_fixed.json`）证据级闭环 26/30→25/30、all_docs@3 0.833→0.70、mrr 1.0→0.967，唯一残留 rq021 的恢复是 ReAct 随机补检（该题扩展字段为空）、真正用到扩展的 rq024 反而丢闭环；**口语化题集 9 题**（`questions_broad.jsonl`——**8 道为有明确主题的跨文档拼接题、仅 bq001「go语言怎么学」真模糊**）**单遍检索完全失效 all_docs@3=0.0**，ReAct 自主补检做到 8/9 闭环（丢 bq006 项目概览，两路都漏传感器 PPT），加扩展反而降到 7/9（bq004 拼接题被扩展拆漏「方法」子主题、提前停手丢文档；bq006 仍漏）、mrr 0.91→0.76——扩展唯一收益是省工具调用（bq002 4→2、bq008 13→6、bq009 9→6），总调用 6.44→4.78（-26%）但以丢覆盖为代价；**bq001 真模糊题两次都闭环、扩展无差别，但「期望文档命中」框架本来就测不了真模糊题（「怎么学」答案主要在库外教学法）**——**扩展子问题被 ReAct 自主补检吸收、无净收益，`QUERY_EXPANSION_ENABLED` 保持默认关；真模糊题的正解是「证据不足反问澄清」门控（`_evidence_sufficient` 现仅信息性、未改变行为），而非预打包扩展**。后端测试 `285 passed`。

### 建议下一阶段（按优先级）

**核心（评估闭环补全）**：
1. ~~用评估集调检索参数~~（已完成）：`scripts/eval_sensitivity.py` 扫描 `_federated_search` 五个参数，**结论是当前参数已局部最优**——semantic_min 提高明显掉指标（0.48→0.55 时 R@1 0.977→0.930），其余四维持平。面试可答"我用评估集做了参数敏感性分析，确认当前参数已接近最优，无需为了调参而调参"。详见 `data/eval/report.md` 四、参数敏感性分析。
2. ~~扩评估题量~~（已完成，43→74 题）：43 题时 production R@1=0.977 接近天花板、区分度不足，扩题后旧权重掉到 0.932 暴露「类型定义/语义相似文档」跨文档混淆，据此把 relevance 权重从 0.55/0.35/0.10 调到 0.65/0.25/0.10（74 题 R@1 回到 0.9595、43 题持平），生产默认已更新。详见 `data/eval/report.md` 五、评估集扩容。

**安全债（独立后端改动，不影响现有个人场景）**：
3. ~~真实鉴权（密码/JWT）替换轻量 `X-User-Id`，`require_admin`/`require_head` 角色校验保留复用。~~（已完成，见上方 Phase 1.1 条目）

**工程/产品补全**：
4. ~~记录解析耗时、OCR 失败率、向量化耗时和问答延迟。~~（已完成，见上方 Phase 2.1 条目）
5. ~~上下文 Token 预算和 Chunk 去重。~~（已完成：上传侧按 sha256 `content_hash` 查重，同内容重复上传 409 拒绝并清理孤儿文件；证据预算从"字符"口径升级为 tiktoken `cl100k_base` 估算的 token 口径，中文 1 字≈1 token、英文 4 字≈1 token）
6. ~~查询改写与原问题并行召回（现有改写是"改写替换"，可改"双路召回再融合"）。~~（已完成，见上方 Phase 2.2 条目）
7. ~~原图访问接口 + 安全临时 URL（前端不再用本机绝对路径）。~~（已完成：`/original` 与 `/assets` 走签名临时 URL，`require_original_signature`/`require_asset_signature` 校验；列表与来源带 `original_url`/`asset_url`，前端 ChatView 来源展示即用签名链接，不再暴露本机绝对路径）
8. ~~任务状态更细的 `EMBEDDING` 阶段和结构化错误码。~~（已完成：状态机 `PENDING→PARSING→OCR→CHUNKING→EMBEDDING→INDEXING→SUCCEEDED`，`STAGE_ERROR_CODES` 按阶段映射 `*_FAILED` 结构化错误码，`error_code_for_stage` 兜底）
9. ~~模型调用**超时、重试、限流**（成本统计已完成，见 Phase 2.1 条目）。~~（已完成：agent LLM 配 `LLM_REQUEST_TIMEOUT` 超时 + `LLM_MAX_RETRIES` 重试，避免 DeepSeek 慢响应卡死单次请求；聊天限流 `check_chat_rate_limit` 超频 429）

---

## 17. 最后检查清单

面试前确认：

- [ ] 能在一分钟内说清场景、输入、解析、检索、生成和异步任务。
- [ ] 能解释为什么需要视觉模型，但最终回答不一定需要多模态生成模型。
- [ ] 能手写 Cosine、RRF、滑动窗口和简单状态机。
- [ ] 能说明 BM25 和向量检索各自解决什么问题。
- [ ] 能说清 Milvus、MySQL 和 Redis 的职责边界（向量 / 元数据与记忆 / 任务队列）。
- [ ] 能解释 Chunk 为什么不能只按固定长度切。
- [ ] 能解释公式、表格、图片和原图如何保存。
- [ ] 能说明模型切换为什么不会暴露密钥。
- [ ] 能说清 Agentic 问答的意图路由（scope / document_ids）和 ReAct 循环，以及入库链路为什么不用 LangChain。
- [ ] 能说清"workflow 骨架 + 内嵌 ReAct Agent"的四段分层（意图识别 / ReAct 检索推理 / 落库+压缩 / 画像更新），以及"用 LLM ≠ agent，有自主循环才是"的判断标准。
- [ ] 能说清 Phase 2 班级学习库：轻量身份（X-User-Id，明确非安全）、上传校验 agent 的"驳回隐藏但保留"、可见性过滤规则、画像如何改变回答形式、会话持久化。
- [ ] 能说清评估集四路对比结论：纯向量 Recall@1=0.930 是最强单通道，原始跨库 RRF 因"榜首平局"只有 0.465，生产链路（路由+重排）拉回 0.977、MRR 0.983（43 题）。
- [ ] 能说清两组对照实验：切片策略升级前后 R@1 0.930→0.977（结构化切分增益可量化）；重排机制前后 rrf 0.465→production 0.977（重排层承重）。
- [ ] 能说清评估集扩容 43→74 + 权重调优的因果链：43 题接近天花板→扩题暴露旧权重掉到 0.932→敏感性扫描权重 0.65/0.25/0.10 拉回 0.9595（43 题持平）→生产默认已改。面试可讲"先扩评估集再量化调参收益"的方法论。
- [ ] 能说清答案忠诚度零标注自动评估：RAGAS 风格 LLM-as-judge，43/43 平均 0.865，能逐题暴露真实薄弱点（如 q012 三指针题 0.438）。
- [ ] 能说清跨文档关系型评估 + LightRAG 决策：30 道必须拼接 2~3 份资料的关系型题（rq001-rq030），单遍 all_docs@5=0.533、mrr_any=0.934（入口易找、拼接材料不全）——**LightRAG 声称的跨文档缺口实测存在**；ReAct 补检把证据级 all_docs@5 拉到 0.833、单遍∪ReAct 联合闭环 96.7%（29/30），真正残留仅 rq021 一个文档（3.3%）归因为题设边缘效应——**LightRAG 不需要**；问题扩展在精确 30 题与口语化题集（9 道，仅 bq001 真模糊、其余 8 道与 rq 集同类）上复测均无净收益（精确闭环 26→25、口语化 ReAct 8/9→7/9，扩展只省工具调用 -26% 却以丢覆盖为代价），且这类题单遍 all_docs@3=0.0 完全失效必须靠 ReAct 自主补检，`QUERY_EXPANSION_ENABLED` 保持默认关。面试可讲"验证 GraphRAG 卖点最扎实的方式是构造它声称擅长的题型、先扩题到能稳定测量、再定位缺口在召回层还是规划层"，也可讲"发现第一版评估脚本漏传扩展参数导致结论无效、修正后重跑"的测量严谨性。
- [ ] 有一组真实演示资料和三到五个具体问题。
- [ ] 有测试结果、接口截图或终端输出作为证据。
- [ ] 不使用没有实验依据的性能百分比。
