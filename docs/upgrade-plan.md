# 升级路线

## 阶段一：仓库和运行基线

- 完成 GitHub 标准目录和文档分层。
- 移除机器相关硬编码路径。
- 固化 Python 依赖和环境变量。
- 删除未使用的旧 UI 入口，保持前端与后端边界清晰。

## 阶段二：Vue 3 与 API v1

- 创建 Vue 3 + Vite + TypeScript 工程。
- 抽取 API 类型和请求客户端。
- 实现资料列表、上传、任务状态和问答页面。
- 保留 /api 兼容层，新增 /api/v1。

## 阶段三：多模态解析

- 增加 PyPDF 文本和页面解析。
- 增加 PaddleOCR 图片与扫描页处理。
- 统一 DocumentBlock 和来源元数据。
- 支持原图引用、页码和 OCR 区域。

## 阶段四：异步增量入库

- Redis 任务队列。
- 解析、OCR、分块、Embedding、索引任务拆分。
- 任务状态、失败原因和重试接口。
- 内容哈希去重和索引版本管理。

## 阶段五：混合检索与评估（已完成）

- BM25 关键词索引。
- 向量与 BM25 结果合并、去重和重排。
- 构造学生复习场景评测集（43 题，`data/eval/questions.jsonl`，见阶段八）。
- 对召回率、来源准确率、回答正确率和响应耗时做对比。

## 阶段六：Agentic RAG（已完成）

- 文档注册表迁移到 MySQL（SQLAlchemy documents 表），修复 JSON 文件 + 进程内锁的跨进程竞态。
- 引入 LangChain 经典 AgentExecutor（非 LangGraph）：意图路由（structured output）在检索之前，决定检索哪些文档分区。
- 现有混合检索封装为 `search_library` / `search_documents` 工具，LLM 在 Thought→Action→Observation 循环里多轮检索、判断证据、带来源编号作答。
- 新增 `POST /api/v1/chat/agent` 端点，旧 `/chat/ask` 与 `/retrieval/search` 保留。

## 阶段七：班级学习库（已完成，前端待接入）

从个人工具升级为**小团体班级学习库**：老师（admin）与学生（member）共享同一文档库，两者都能上传/检索，每次上传先经过校验 agent 审核。

- **轻量身份（非安全设计）**：`X-User-Id` 请求头区分老师/学生（无密码/JWT），缺失时默认管理员 `u_admin`。新增 `users` / `classes` / `class_members` 表，单班级起步、class_id 预留多班级。
- **上传校验 agent**：`review_agent.py` 在入库前审核内容是否学习相关且合理（手动 bind_tools，兼容 DeepSeek thinking）；`uploads` 表记录 status（pending/approved/rejected/hidden）。驳回内容**标记隐藏但保留**，不进检索，管理员可放行/删除。开关：`UPLOAD_REVIEW_ENABLED`。
- **管理员审计后台**：`GET /admin/uploads` 看谁传了什么 + 校验结果；`approve`/`reject`/`delete` 放行/驳回/删除。
- **用户画像**：`user_profiles` 表存科目/薄弱点/偏好风格；`/chat/agent` 注入路由与 system prompt——beginner 步骤化解释、advanced 推导+反例。
- **会话持久化**：`conversations` / `messages` / `agent_traces` 表；`/chat/agent` 带 `conversation_id` 加载历史做多轮上下文，回答后落库消息 + Agent 工具链轨迹。
- **可见性过滤**：有 upload 记录的文档只看 approved；无 upload 记录（legacy）保持可见。枚举入口（document_catalog / _uploaded_collections）都过过滤。
- 遗留：真实鉴权（密码/JWT）后续阶段。

## 阶段八：四段 workflow + 检索评估集（已完成）

- **Phase 3 四段 workflow 显式化**：`/chat/agent` 拆成 意图识别 → ReAct 检索推理（唯一 agent 段）→ 落库+压缩 → 画像更新。意图识别步加入查询改写（`rewrite_query`，DeepSeek 兼容的宽容提取），ReAct 段外圈加前置探针（scope=auto 且路由 selected 时 0 命中 → 升级全库）与后置证据判定（no_evidence / weak_evidence / sufficient）。响应新增 `retrieval.rewritten_question` / `retrieval.evidence` / `retrieval.stages`，向后兼容。
- **Phase 4 检索评估集**：43 题文档级评估集（全部来自库内真实内容），`scripts/eval_retrieval.py` 跑四路对比——纯向量（cosine）/ 纯 BM25（BM25Plus）/ 原始跨库 RRF / 真实生产链路（`_federated_search`）。结果（`data/eval/results.json`）：

```text
Variant      Recall@1     Recall@3     Recall@5      MRR
vector       0.9070       0.9767       1.0000      0.9477
bm25         0.8372       0.9070       0.9302      0.8669
rrf          0.4186       0.7907       0.9535      0.6257
production   0.9302       0.9767       0.9767      0.9535
```

关键结论：原始跨库 RRF 有"榜首平局"缺陷（rank-1 分都≈2/61，单 chunk 小库被高估），生产链路的路由门控 + relevance 重排把 Recall@1 从 0.42 拉回 0.93。

- 评估脚本用法：`python scripts/eval_retrieval.py`（从仓库根，`--limit N` 冒烟，`--variants vector,bm25,rrf,production`）。指标纯函数在 `backend/app/rag/eval/metrics.py`，可无 Milvus 单测。
- **答案 groundedness 评估（后续补充，2026-08-08）**：检索指标只证明「召回对不对」，没证明「答案引没引对」。`scripts/eval_groundedness.py` 复用同一 43 题，用生产链路（`stage_intent` + `stage_react`，不落库、无画像、单轮）为每题生成答案与 top sources，人工逐题标 0/1/2（0=来源不支持/编造，1=部分支持，2=充分支持），聚合出平均分、fully_grounded(=2)、grounded(≥1) 占比，并与检索命中交叉（expected 文档是否进 top sources）——区分「没召回到」与「召回到了但答案没用对」。纯函数在 `backend/app/rag/eval/groundedness.py`（validate_score/distribution/aggregate/cross），可无模型单测。标注文件 `data/eval/groundedness.jsonl`（score 待填 → 人工填 0/1/2 → `--mode aggregate` 出 `data/eval/groundedness.json`）。**流程设计：答案生成与人工标注分离**——generate 只产出可审阅的标注文件（含答案、来源 text、引用编号），人审完再聚合，可迭代（改标注重聚合，无需重新生成）。

## 阶段九：前端会话持久化体验 + 历史会话列表 + 长期记忆可视化（已完成）

- **根因修复**：Phase 2 后端会话持久化早已完整，但前端 `ChatView` 把 turns/conversation_id 存在组件本地 ref，`<RouterView>` 无 KeepAlive → 切换栏目即丢。问答状态提升到 Pinia store（`frontend/src/stores/chat.ts`），只把 conversation_id/scope/model 落 localStorage（key 带 user_id 隔离），turns 始终从后端拉取，刷新/切栏目后 `restoreIfEmpty` 自动恢复。
- **历史会话列表**：`/chat/agent` 每轮已按用户落库，前端新增侧栏（`ChatView` + `GET /api/v1/conversations` 附带 `last_message`/`message_count` 预览），点开续聊、可删除、当前会话高亮；窄屏折叠为右上角「历史」按钮。会话按用户隔离，同一问题不会重复问。
- **长期记忆可视化**：画像页新增「长期记忆」面板（`GET /users/me/memory` 早已存在，此前无前端入口），展示画像进化自动写入的 user_memory（行为/薄弱点/风格观察），可删除。
- **真 bug（Phase 2 遗留，本次 live 冒烟抓出）**：MySQL `FLOAT` 在 epoch 量级（~1.75e9）丢亚秒精度，user/assistant 两条消息同秒写入 `created_at` 完全相同 → ORDER BY 平局 → 会话内消息顺序不稳定（实测整轮反转，LLM 的 chat_history 变成"回答在前、问题在后"）。修复：`messages/agent_traces/conversations` 的 created_at/updated_at 从 `Float` 改为 `Double`，`init_db` 启动时对仍为 FLOAT 的列幂等 `ALTER MODIFY DOUBLE`（`_ensure_precise_timestamps`，仅 MySQL，SQLite 不需）。存量整秒数据不受影响，新写入自动带亚秒。
- 测试：新增 `backend/tests/test_conversations_api.py`（列表预览/用户隔离），全套 **132 passed**（130 + 2）；前端 `npm run build` 通过；live 冒烟确认消息顺序 `user→assistant→user→assistant` 稳定。

**阶段九 live e2e 验证（Playwright，系统 Edge，真实前端 5173 → 后端 8504）**：17 项断言全通过——两轮问答 → 切「资料入库」再回「知识问答」会话还在 → 刷新从后端恢复 → 历史列表预览/点开/删除/新会话清空 → 登出后 `rag_chat_{userId}` 本地引用保留 → 学生登录无残留且看不到老师会话 → 老师重登历史可见 + 当前会话自动恢复。
- **用户明确要求：会话跨登录/登出必须保留**（登出**不**清会话）。实现：`AppHeader.logout` 只清 auth；store 的 `syncForCurrentUser()` 在进入问答页时比对当前登录用户，换人就重载该用户持久化的会话引用；localStorage key `rag_chat_{userId}` 按用户天然隔离。
- **live 抓出 2 个前端真 bug（均已修）**：
  1. ChatView `<template v-for="turn in turns">` 同时渲染 user 气泡与 assistant 气泡（未按 role 分流）→ 每条消息界面出现两次。修复：按 `turn.role` 加 `v-if`/`v-else`。
  2. 历史回放崩溃：`stage_persist` 落库的 `metadata_json.sources` 只有 `{document_id, filename, score}`，SourceList 渲染要 `content_type` → 刷新/点开历史时 `content_type.includes()` 抛 TypeError，Vue 渲染中断只显示 1 个 turn。修复：SourceList 防御（兼容旧数据）+ 后端改落 `_serialize_source(item)` 完整来源（try/except 降级最小来源）。
- 默认模型改为 `gpt-5.6-terra`（`.env` + workbench fallback）——gpt-5.6-luna 上游 503 `model_not_found`（外部问题）；terra 间歇 `Upstream service temporarily unavailable` 时端点 500，浏览器 fetch 能正常收到 500 并走错误轮次，不挂起。
- **顺序错乱后续（live 复测抓出）**：FLOAT→DOUBLE 只防**新**平局，存量旧数据仍是整秒并列；`list_messages` / `list_conversation_traces` 原本只 `ORDER BY created_at`，同秒平局时 MySQL 返回顺序任意（实测"回答在上、问题在下"，既乱历史回放也喂错 LLM 的 chat_history）。修复：两处查询加确定性次级键 `case(role=='user',0, role=='assistant',1, else 2)`——同秒时 user 在前（后端写入永远 user→assistant 交替，user-first 对成对平局是精确解）。存量 10 组平局中 9 组是 user/assistant 成对，次级键即精确恢复；唯一 1 组（导数问答）整段会话塌进同一秒、4 条消息时间戳全等，按 `metadata_json.router.rationale`（路由理由引用了问题原文）人工恢复 Q→A→Q→A 顺序。
- **登录会话语义修正（用户明确要求）**：原来 `restoreIfEmpty` 会在问答页挂载时**自动恢复上次会话**——用户登录后直接进入上次会话，与本意冲突。改为：**登录后永远是全新空会话，历史只能主动在列表点选进入**。实现：`chat.ts` 新增 `clearPersistedSession()`（登录成功、登出时清掉 `rag_chat_{userId}` 本地引用 → 下次进入无引用可恢复、必然空会话；历史仍完整在后端），`restoreIfEmpty` 保持同一次登录内切栏目/刷新自动恢复进行中的会话。坑：`clearPersistedSession` 必须在 `auth.logout()` 清 user_id **之前**调用并快照 uid，否则 key 算成 `rag_chat_guest` 漏清（已修，action 开头快照）。live 验证 11 项断言：登录空会话 → 点历史加载 → 切栏目保留 → 刷新恢复 → 登出清引用 → 重登又空会话。
