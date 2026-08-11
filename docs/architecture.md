# 系统架构

## 1. 设计目标

系统服务于个人学习和复习，不追求多租户 SaaS 的复杂度，优先保证：

1. 文本、PDF、图片、手写笔记、DOC/DOCX 和 PPT/PPTX 可以进入同一知识库。
2. 专业术语、公式和错题描述具有稳定召回能力。
3. 答案能够回到文件、页码、图片区域或原文片段。
4. 新增资料只触发增量任务，不重建全部索引。

## 2. 分层

~~~text
Vue 3
  ├── 文档库视图
  ├── 任务进度视图
  └── 问答与来源视图
        |
FastAPI API
  ├── document：上传、列表、详情
  ├── task：提交任务、查询状态、重试
  ├── retrieval：检索和问答
  └── health：依赖服务状态
        |
Application Services
  ├── Parser Service：按 MIME 类型选择解析器
  ├── OCR Service：图片和 PDF 页面 OCR
  ├── Vision Service：图片描述、版面理解和复杂图文补充解析
  ├── Chunk Service：标题、段落、语义边界分块
  ├── Index Service：Embedding、Milvus、BM25
  └── Query Service：混合召回、重排、答案生成
        |
Infrastructure
  ├── Redis：任务队列和任务状态
  ├── Milvus：向量索引
  └── LLM / Embedding / PaddleOCR：模型能力
~~~

## 3. 多模态解析

统一产出 DocumentBlock，避免后续检索逻辑依赖具体文件类型：

~~~text
DocumentBlock
├── document_id
├── source_type: pdf | markdown | image | docx | pptx | text
├── content_type: text | table | image_ocr | image_description | formula
├── text
├── page_number
├── image_path / bbox
├── heading_path
└── metadata
~~~

- 清洗（所有格式）：解析后统一经过 `cleaning.py`——连字归一、行尾断连字符、折叠运行空白、剥离页眉页脚/页码/水印（正则 + 跨 60% 块重复的频次启发），任何块清洗后为空则回退原文，绝不丢块。
- PDF：pdfplumber 按阅读顺序抽取文本（失败逐页回退 PyPDF，`metadata.parser_fallback` 可观测）；`find_tables()` 检出表格为独立 `table` 块并从正文排除表格区；字号启发式把明显大于正文中位数的行判为标题并构建标题层级；扫描页仍整页渲染交给 OCR/视觉。
- PDF 内嵌图片、图片和手写笔记：优先调用 OpenAI 兼容视觉模型生成单份规范化中文内容；OCR 作为证据和视觉失败时的回退，不把同一图片的 OCR、公式和视觉结果重复切成多个知识块。
- DOC/DOCX：旧格式先由 Office 转成 DOCX，再提取标题、段落、原生表格、原生 OMML 公式和内嵌图片。
- PPT/PPTX：旧格式先由 Office 转成 PPTX，再按幻灯片提取文本、表格、原生公式和图片；幻灯片标题作为标题路径、幻灯片号作为来源定位，组块时绝不跨幻灯片合并。
- 图片：保存原图引用，视觉文本进入检索字段，OCR 原文、OCR 框坐标和置信度进入元数据。
- Markdown/HTML：保留标题层级、代码块和列表关系；pipe 表 / `<table>` 识别为单个 `table` 块（行/列结构保留在 metadata），不再退化成 `|` 连接的散行文本。
- Excel（.xlsx/.csv）：合并单元格先展开左上角值广播；首个有内容行作表头；按行组切片（每块 ≤50 行），每行序列化成「列名：值；…」并在块内重复表头保证自包含；`heading_path=[sheet 名, 行 N-M]`，`content_type="table"`。
- 手写笔记：OCR 结果标注较低置信度，回答时展示原图作为复核入口。
- 公式：Office 原生公式转 LaTeX；图片/扫描页优先由视觉模型识别，只有没有视觉结果时才接收经过长度和重复模式校验的 `PP-FormulaNet_plus-M` 结果，原图始终保留用于复核。

视觉模型配置使用 `VISION_LLM_API_KEY`、`VISION_LLM_BASE_URL` 和 `VISION_LLM_MODEL`，与最终回答使用的 `LLM_*` 配置分离；当前两者统一指向同一个第三方 OpenAI 兼容接口。原始文件始终保留，Milvus 保存 chunk 文本、向量和来源元数据，不把原文件替换成一段描述。

## 4. 知识点感知分块

系统通过 Chunking Profile 选择策略，而不是所有文件统一固定长度切分：

~~~text
auto
  ├── Markdown/HTML/技术资料 -> technical   （结构重建，标题进 chunk 正文）
  ├── 长篇报告/研究资料 -> long_form
  ├── 扫描件/表格/公式/图片 -> layout
  ├── Excel/CSV 表格 -> spreadsheet
  ├── 问题-答案资料 -> short_qa
  └── 高价值资料 -> high_value
~~~

`technical` 在组块时做**结构重建**：把标题块与正文按章节合并成带 `#` 层级的 Markdown 再切分，章节标题真正进入 chunk 正文（而不是只进 `search_text`），表格/公式/图片是屏障块独立保留溯源。`long_form` 先在相同标题、页码和幻灯片范围内聚合，再使用 Embedding 判断语义边界；`layout` 保持解析器识别出的区域，不跨表格、公式、图片合并；`spreadsheet` 保持解析器产出的行组块（块内已重复表头）；`high_value` 在父子块基础上可选调用 LLM 生成短上下文。每个子块的 `metadata` 保存 `chunk_profile`、`parent_chunk_id`、`chunk_level`、`parent_content`、`context_prefix` 和 `search_text`。

图片/公式块在组块后做**邻近正文绑定**：把前一个文本块尾部 + 后一个文本块头部各 150 字拼进 `metadata.context_text` 并并入 `search_text`，使"讲梯度下降那张图/那个公式"这类用正文词汇提问的检索能同时命中 BM25 与向量路。

原始 `content` 用于来源展示，`search_text` 用于 Embedding 和 BM25。这样标题上下文可以增强召回，同时不会把重复的标题前缀展示给用户。每个分块仍保存来源信息和内容哈希，不能只把一段字符串写入 Milvus，否则无法做到页码和图片区域溯源。

## 5. 混合检索

~~~text
问题
  -> 查询清洗与关键词提取
  -> BM25 召回专业术语和公式
  -> Milvus 召回语义相关片段
  -> 合并、去重、重排
  -> 按来源分组
  -> LLM 生成带引用答案
~~~

BM25 解决精确词命中，向量检索解决表达方式变化。系统先在文档级做门控：实体属性问题（例如“张林翔的学号”）要求资料中出现对应实体，主题短语问题优先要求资料中出现有效短语，普通问题再使用关键词/语义门控。进入候选 Collection 后执行 BM25 + 向量 RRF，避免无关资料仅凭排名分进入上下文。BM25Plus 的正基线分数不代表真实命中，关键词路还会做 token 重叠校验。

RRF 分数只用于内部融合排序；接口返回的 `score` 是结合精确实体、关键词、向量相似度和最终排名计算的 0～1 展示匹配度，原始 RRF 值通过 `rrf_score` 保留给调试。这样不会把 `1 / (60 + rank)` 产生的 `0.016` 误解成概率或真实相关度。

每次上传都会写入 MySQL 的 `documents` 表（注册表），保存原始文件名、文档 ID、内容哈希、中文主题和 Collection 名。解析完成后从 Markdown 标题、文档标题或视觉理解首行提取主题，例如“数据结构完整复习笔记”。Collection 使用主题拼音和文档 ID 组成 Milvus 安全标识符，中文主题始终保留在注册表、chunk 元数据和接口返回中。注册表用 SQLAlchemy 持久化（`backend/app/db/models.py`），替代了原先的 `data/document_registry.json`，使 API 与 Worker 双进程写入由数据库事务兜底，避免进程内锁的跨进程竞态。

## 6. 异步增量入库

上传接口只负责保存文件和创建任务，不在 HTTP 请求内执行 OCR 与向量化：

~~~text
POST /api/v1/documents
  -> Redis task: created
  -> parse
  -> ocr
  -> chunk
  -> review        <- Phase 2：上传校验 agent 审核内容是否合理；Phase 1.2 增加文档内提示词注入指令检测（命中即强制驳回）
  -> embed
  -> index
  -> task: succeeded / REJECTED
~~~

同一个文件通过 content_hash 去重。只有文件内容发生变化时才创建新的索引版本。
校验被驳回（rejected）时跳过 embed/index，任务标记完成但状态为 REJECTED；文件与记录保留，管理员可在后台放行（重新补索引）或删除。

## 7. 班级学习库（Phase 2）

在原有 RAG 管线之上加一层"班级组织 + 内容治理 + 个性化"：

~~~text
Bearer JWT 鉴权（Phase 1.1 真实鉴权：用户名+密码，bcrypt + HS256）
  -> users / classes / class_members        组织模型（单班级起步，class_id 预留）
  -> uploads                                每次上传的校验台账（谁传了什么 + 校验结果）
     status: pending -> approved / rejected / hidden
  -> 可见性过滤                             检索入口只看 approved（无 upload 记录的 legacy 保持可见）
  -> user_profiles                          科目 / 薄弱点 / 偏好风格
     /chat/agent 注入路由与 system prompt：beginner 步骤化、advanced 推导+反例
  -> conversations / messages / agent_traces 会话持久化 + 多轮 chat_history + 工具链轨迹
~~~

关键取舍：

- **上传校验是单次结构化审核调用**（手动 bind_tools），不是多轮 ReAct——够用且省 token；DeepSeek thinking 不支持 response_format/强制 tool_choice，故沿用 agent 路由的手动 bind_tools 模式。
- **提示词注入检测（Phase 1.2）**：校验 agent 在"学习相关"之外显式检测文档内隐藏的注入指令（覆盖/忽略助手约束、扮演其他角色、诱导泄露个人信息、嵌入条件指令等），`prompt_injection` 命中即强制驳回（fail-closed，不依赖模型 approved 判定是否含糊），完整判定存入 `uploads.review_payload` 供审计；system prompt 的「最高优先级边界块」仍是运行时主防线，入库前检测是纵深防御的预处理层。
- **驳回 = 隐藏但保留**：不进检索、不可被召回，但文件/记录/驳回原因都保留，管理员可放行/删除。状态全部放在 uploads 表（documents 表已存在于 MySQL，`create_all` 不会加列）。
- **真实鉴权（Phase 1.1）**：`Authorization: Bearer <JWT>`，无 header 一律 401；密码 bcrypt 哈希、响应绝不外泄；无密码账号首登引导式补设（scope=setup 短效 token，已设密 → 409 防抢先竞态）；DB 挂时已验签 token 降级为 degraded 身份，管理操作 fail-closed 503。
- **运行指标（Phase 2.1）**：worker 与 API 共用 Redis 计数（`rag/metrics.py` 的 `MetricsStore`，`/admin/metrics` 读快照）。延迟用 LPUSH+LTRIM 保留最近 500 条算滑窗 p50/p95；token 成本用 langchain `BaseCallbackHandler` 挂在 `llm.with_config({"callbacks"})` 上，覆盖改写/路由/ReAct/画像/压缩全部调用。**Redis 不可用时静默降级、一次失败即禁用**（避免每个请求重连），指标丢失不影响业务——指标是观测层不是依赖。
- **双路召回（Phase 2.2）**：查询改写从"改写替换"升级为"改写后问题为主路 + 原问题为副路并行召回再融合"。改写可能丢题号/变量名/公式字符，副路补回；融合 `_merge_dual` 按 `(collection, chunk_index)` 取两路更高分再降序——RRF 只适合合并同一查询内多路证据，两条独立查询链分数不可直接 RRF，按 chunk 取 max 是跨查询最稳的保底；改写未变化时副路自动跳过。检索 trace 加 `dual_recall` 可观测。
- **跨文档关系型评估（Phase 3.1，LightRAG 缺口验证）**：构造 7 道关系型题（`data/eval/questions_relational.jsonl`，每题 2~3 个期望文档，全部来自库内真实内容：Go 语法跨文档拼接/对比、数据结构与 Go 切片跨领域对比、大创申请书与图表 PPT 跨域拼接），用生产链路单遍检索量度"全部期望文档是否都被召回"（`scripts/eval_relational.py` + `metrics.py` 的 `evaluate_relational`：doc_coverage@K / all_docs@K / mrr_any）。**实测结论**：单遍 all_docs@5=0.5714、mrr_any=0.9286——入口几乎总能找到但拼接材料不全，LightRAG 声称要解决的跨文档关系型缺口真实存在；ReAct 补检（最多 4 次）+ P2.2 双路召回把单遍漏掉的 3 题中 2 题拉到闭环，残留 1 题（rq005 锚点文档+答案文档分离）agent 拿到答案就停手，属生成/规划层残余缺口——图连边正是这类场景的解药。结论写入 `data/eval/report.md` 第六节。
