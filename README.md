# 多模态个人学习知识库 RAG 系统

面向学生复习场景的私有知识库。系统将教材 PDF、Markdown 笔记、网页、截图错题和手写笔记统一解析、索引，并在回答中返回可追溯的资料出处。

## 项目定位

这是一个以多模态 RAG 为核心的个人学习系统，重点解决三类问题：

- 复习资料格式不统一，文本、图片和 PDF 难以统一管理。
- 专业术语、公式和错题描述不适合只依赖纯向量检索。
- 问答结果需要能回到原资料，方便复盘和查漏补缺。

当前仓库已经具备多模态解析、异步入库和混合检索骨架；视觉模型用于入库阶段的图片理解，第三方 OpenAI 兼容文本模型用于最终问答。

## 技术栈

| 层次 | 技术 | 作用 |
| --- | --- | --- |
| 前端 | Vue 3、Vite、TypeScript | 知识库、任务、检索和溯源结果界面 |
| API | Python、FastAPI、Pydantic | 文档、任务、检索和问答接口 |
| 编排 | LangChain（经典 AgentExecutor，非 LangGraph）+ Application Services | Agentic 问答的意图路由、检索工具和推理循环；解析、分块、混合检索编排 |
| 文档解析 | PyPDF、python-docx、python-pptx、Office COM、Markdown Parser | PDF、Word、PPT 和结构化文本解析 |
| 图片解析 | PaddleOCR、OpenAI-compatible Vision LLM | 截图错题、手写笔记、内嵌图片 OCR 和视觉描述 |
| 向量检索 | Milvus | Embedding 存储和语义检索 |
| 关键词检索 | BM25 | 专业术语、公式和精确关键词召回 |
| 元数据存储 | MySQL（Docker） | 文档注册表、会话和记忆（跨进程一致，替代 JSON 注册表） |
| 异步任务 | Redis | 解析、OCR、切分和向量化任务队列 |
| 模型 | Sentence Transformers、OpenAI-compatible LLM | 向量化和答案生成 |

## 多模态 RAG 数据流

~~~text
Vue 3
  -> FastAPI
  -> Redis 任务队列
  -> 文档识别与解析
       PDF: 文本提取 + 扫描页 OCR + 内嵌图片理解
       HTML: 正文提取 + 远程图片下载 + 图片视觉理解
       图片/手写: PaddleOCR + 可选视觉模型
       DOC/DOCX: Office 转换 + 段落 + 标题 + 表格 + 原生公式
       PPT/PPTX: Office 转换 + 幻灯片文本 + 表格 + 原生公式 + 图片
       Markdown / TXT: 结构化解析
  -> Chunking Profile 路由
       技术文档：标题级联 + 段落
       长文报告：章节聚合 + 语义边界 + Parent-Child
       扫描/PDF图表：版面区域保留
       短问答：固定长度/问答对
       高价值资料：可选 Contextual Retrieval
  -> Embedding
  -> Milvus + BM25 索引
  -> 混合检索与重排
  -> LLM 生成答案
  -> 返回答案、原文片段、页码/文件名/图片位置/原始文档入口
~~~

## 目录结构

~~~text
.
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI 应用入口
│   │   ├── core/
│   │   │   ├── config.py            # 统一配置读取（含 MySQL）
│   │   │   └── database.py          # SQLAlchemy 引擎与会话
│   │   ├── db/
│   │   │   ├── models.py            # Document 表模型
│   │   │   └── migrate.py           # JSON 注册表 → MySQL 迁移脚本
│   │   ├── api/
│   │   │   └── routes_retrieval.py  # 检索/问答/agent 接口
│   │   └── rag/
│   │       ├── pipeline.py          # Milvus RAG 基线管道
│   │       ├── catalog.py           # 基线文档和 Collection 目录能力
│   │       ├── document_registry.py # 文档注册表（MySQL 持久化）
│   │       ├── agent_rag.py         # Agentic 问答：意图路由 + 检索工具 + AgentExecutor
│   │       ├── hybrid_pipeline.py   # BM25 + 向量混合检索管线
│   │       ├── model_config.py      # 中文 Embedding 模型配置
│   │       └── chunkers/            # 分块策略
│   ├── tests/                       # 后端自动化测试
│   ├── requirements.txt             # 当前 Python 依赖
│   ├── requirements-multimodal.txt  # 多模态运行依赖
│   └── requirements-dev.txt         # 测试依赖
├── frontend/
│   ├── src/                         # Vue 3 工作台源码
│   └── README.md                    # 前端启动和安全边界
├── data/                            # 本地学习资料，不作为代码模块
├── docs/
│   ├── architecture.md              # 系统架构和多模态数据流
│   ├── api.md                       # 当前接口和升级后的接口约定
│   ├── development.md               # 本地开发和验证方式
│   ├── project-report.md             # 完整系统架构报告
│   └── upgrade-plan.md              # 分阶段升级路线
├── infra/docker-compose.yml         # Milvus 依赖服务
├── scripts/                         # 启停和模型准备脚本
├── .env.example
└── README.md
~~~

## 首次安装

~~~powershell
conda activate rag11
python -m pip install -r backend/requirements-multimodal.txt
python scripts/download_embedding_model.py
cd frontend
npm install
~~~

模型和依赖只需要首次准备，日常启动不需要重复安装或下载。

## 日常启动

### 1. 启动基础设施

先确认项目根目录的 `.env` 已配置（MySQL 需要，docker-compose 强制要求，可复制 `.env.example` 改）：

~~~powershell
cd E:\github项目\rag
copy .env.example .env   # 首次：填 LLM_API_KEY、MYSQL_ROOT_PASSWORD、MYSQL_PASSWORD
~~~

启动 Milvus、Redis 和 MySQL（**必须带 `--env-file .env`**，否则 compose 读不到根目录
`.env` 里的 `MYSQL_ROOT_PASSWORD`/`MYSQL_PASSWORD` 会直接报错）：

~~~powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d
~~~

确认三个依赖都已启动（MySQL 也是必需的，负责文档注册表/身份/上传台账/会话）：

~~~powershell
docker compose -f infra/docker-compose.yml ps
# rag-mysql / milvus-standalone / rag-redis 都应为 Up (healthy)
~~~

> **MySQL 没起会怎样**：API 与 Worker 仍能启动（启动不阻塞），检索问答也照常用。
> 具体降级行为：
> - 携带**合法 token** 的请求用 token claims 合成**降级身份**（`degraded`）——能继续个人
>   问答，但**管理操作全部 503**（fail-closed，不会因为 DB 挂而升级成管理员权限）；
>   无 token / token 无效则 401。
> - 会话/画像不落库、不注入（单轮问答，`conversation_id` 返回 null）。
> - 文档目录退化为"uploads 目录 + Milvus collection"重建（不做校验过滤）；Milvus
>   也连不上时目录为空、不崩。
> 恢复 MySQL 后一切自动回到正常态。

### 2. 启动 API

在新的 PowerShell 窗口执行：

~~~powershell
cd E:\github项目\rag
conda activate rag11
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8504
~~~

首次启动 API 可能需要 30～60 秒加载本地中文 Embedding 模型并连接 Milvus。看到下面日志后才算启动完成：

~~~text
Uvicorn running on http://127.0.0.1:8504
~~~

也可以在另一个窗口检查：

~~~powershell
Invoke-RestMethod http://127.0.0.1:8504/api/v1/health
~~~

### 3. 启动 Worker

在另一个新的 PowerShell 窗口执行：

~~~powershell
cd E:\github项目\rag
conda activate rag11
python -m backend.app.tasks.worker
~~~

Worker 负责 Redis 队列中的解析、OCR、视觉理解、分块和向量入库任务。API 和 Worker 必须同时运行。

### 4. 启动 Vue 前端

在另一个新的 PowerShell 窗口执行：

~~~powershell
cd E:\github项目\rag\frontend
npm run dev
~~~

前端访问 `http://127.0.0.1:5173`，API 文档访问 `http://127.0.0.1:8504/docs`。不需要启动 Ollama。

首次打开前端会进入**登录页**：输入用户名 + 密码。老师账号 `老师`（初始密码需首次登录设置）、学生用老师创建账号时下发的用户名（老师建号时可填初始密码，留空则学生首登引导式补设）。登录成功后持有 JWT（7 天），登录态保存在浏览器 localStorage；账号无密码时登录页自动进入"设置密码"第二步。

## 当前状态与升级重点

### 已实现

- **文本 RAG 基线**：FastAPI 文档目录、Collection 管理和问答接口；Milvus 向量入库和 COSINE 检索；6 种分块策略 + auto 自动选型。
- **多模态解析**：PDF（pdfplumber 阅读顺序 + 表格/标题检测，失败回退 PyPDF）、图片/手写、DOC/DOCX、PPT/PPTX（幻灯片为单元）、Markdown/HTML（结构化表格）、Excel（.xlsx/.csv 合并单元格展开 + 行组分块）、TXT 统一解析为 DocumentBlock；视觉结果优先作为图片的单份 canonical 内容，OCR 作为证据和回退，公式结果经过质量门控后才入库。
- **分块前清洗**：所有格式解析后统一做字符归一化、断连字符修复、页眉页脚/页码/水印剥离（正则 + 频次启发），清洗为空时回退原文不丢块。
- **场景化分块**：上传时支持自动、技术文档、长文报告、版面资料、表格数据、短问答和高价值知识库 Profile；技术文档按章节重建 Markdown 让标题真正进入 chunk 正文；长文保留 Parent-Child 关系；表格作为屏障块独立保留行/列结构与溯源；图片/公式块绑定邻近正文上下文，正文里的提问词也能召回。
- **资料级路由**：每份资料独立 Collection，并通过文档注册表保存原始文件名和 Collection 名；查询先做实体/主题/关键词/语义门控，再执行 Collection 内 BM25 + 向量 RRF，避免跨资料串库。
- **Redis 异步增量入库**：上传即返回 task_id，后台 Worker 执行 解析→分块→向量化→入库，任务状态机 + 内容哈希。
- **MySQL 文档注册表**：文档元数据从 JSON 文件迁到 MySQL documents 表（SQLAlchemy），API 与 Worker 双进程写入由数据库事务兜底，修复原进程内锁的跨进程竞态。
- **Agentic 问答**：`/api/v1/chat/agent` 用 LangChain 经典 AgentExecutor 做 ReAct 推理——先做意图路由（structured output 决定检索哪些资料分区），再在多轮 Thought→Action→Observation 循环里检索、判断证据是否足够、带来源编号作答；可调用 `search_library`（全库）和 `search_documents`（指定资料）两个工具。
- **API v1**：`/api/v1/documents`、`/api/v1/documents/url`、`/api/v1/documents/{id}/original`、`/api/v1/documents/{id}/assets`、`/api/v1/tasks`、`/api/v1/retrieval/search`、`/api/v1/chat/ask`、`/api/v1/chat/agent`。
- **原始资料溯源**：切片检查页可查看原始 PDF/HTML/图片，Word/PPT 提供原文件入口；解析出的图片会单独展示，问答来源同时返回原文和图片链接。
- **检索评估集**：43 题文档级评估集（`data/eval/questions.jsonl`），`scripts/eval_retrieval.py` 四路对比纯向量 / 纯 BM25 / 原始 RRF / 生产链路，产出 Recall@1/3/5 + Precision@1/3 + MRR（见 `data/eval/results_new.json`）。实测（切片升级后新索引）：生产链路 Recall@1=0.9767、Precision@1=0.9767、MRR=0.9826。四路对比同时体现"重排机制前后"——原始跨库 RRF 因「榜首平局」只有 Recall@1=0.465，重排层（路由门控 + `_relevance_score`）拉回 0.977。
- **切片升级前后对比**：`data/eval/results.json`（8-07 旧索引快照）vs `data/eval/results_new.json`（本次新索引重跑），同一 43 题、同一批文档，指标直接可比——生产链路 Recall@1 0.930→0.977、MRR 0.954→0.983，结构化切分的增益可量化。
- **答案忠诚度自动评估**：`scripts/eval_faithfulness.py`（RAGAS 风格断言验证，LLM-as-judge，**零人工标注**）把答案拆成原子事实断言、逐条对照检索来源判断支持性，聚合出平均忠诚度 + fully_grounded 占比 + 与检索命中交叉表（见 `data/eval/faithfulness.json`）。实测 43 题全评、平均 0.865、fully_grounded(≥0.9) 53.5%、grounded(≥0.7) 79.1%，且能逐题暴露低分薄弱点。
- **评估报告**：`scripts/eval_report.py` 把上述数据合成一份对比报告 `data/eval/report.md`（三指标一览 + 切片前后 + 重排前后 + 逐题明细）。
- **真实鉴权（Phase 1.1）**：`Authorization: Bearer <JWT>`（HS256、7 天）替换轻量 `X-User-Id`，无 header 一律 401；密码 bcrypt 哈希入库、任何响应不外泄；无密码账号首登走**引导式补设**（scope=setup 短效 token，已设密 → 409 防抢先竞态）；登录/设密/改密三个端点；MySQL 挂时已验签 token 降级为 degraded 身份、管理操作 fail-closed 503。

高价值资料的 Contextual Retrieval 默认关闭，通过 `.env` 开启：

~~~dotenv
CONTEXTUAL_RETRIEVAL_ENABLED=true
CONTEXTUAL_RETRIEVAL_MODEL=gpt-5.6-luna
~~~

### 后续优化

- 用评估集调检索参数（per-collection top_k、RRF k、semantic floor）——检索与答案两套基线都有了（Recall@1=0.93 + groundedness 标注），调参收益可直接量化。
- 给 groundedness 评估加更多题（43 题答案标注完成后可作为基线，扩题量提升区分度）。
- 增加音频转写和时间戳溯源。
- 增加多资料对比视图，以及按课程/标签管理资料目录。
- **Phase 2 班级学习库（后端 + 前端已完成）**：小团体班级——老师与学生共享文档库，上传经校验 agent 审核（驳回隐藏但保留）、管理员审计后台、用户画像调整回答形式（beginner/advanced）、会话持久化 + chat_history 多轮。前端已接入：登录页（区分老师/学生，老师可创建学生账号）、我的画像页（含长期记忆列表，可查看/删除画像进化自动积累的行为观察）、班级管理页（创建学生 + 上传审计放行/驳回/删除）、知识问答页接入 `/chat/agent` 多轮会话 + Agent 轨迹面板。会话是**真持久化**：每轮问答按用户落库 MySQL，问答状态提升到 Pinia store，切换栏目、刷新页面后自动从后端恢复，并带**历史会话列表**（标题 + 最后消息预览，点开续聊、可删除）——同一问题不必重复问。详见 [docs/upgrade-plan.md](docs/upgrade-plan.md) 阶段七。

详细设计见 docs/project-report.md、docs/architecture.md 和 docs/upgrade-plan.md。
