# API 约定

## 当前 API 基线

当前代码保留以下同步文本 RAG 接口，用于验证核心链路；正式资料上传使用异步多模态任务接口：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | /api/health | 检查 FastAPI 和 Milvus 连接 |
| GET | /api/catalog | 获取资料和分块策略 |
| GET | /api/collections | 查询当前资料的已有 Collection |
| POST | /api/index | 创建、使用或重建索引 |
| POST | /api/query | 检索并生成答案 |

这些接口保留用于验证核心检索链路，最终接口统一使用 /api/v1 前缀。

## 升级后的接口分组

新接口统一使用 /api/v1 前缀：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | /api/v1/health | 服务和依赖健康状态 |
| GET | /api/v1/models | 返回可切换的问答模型及配置状态，不返回密钥 |
| GET | /api/v1/documents/profiles | 返回可用的资料分块 Profile |
| GET | /api/v1/documents | 资料列表 |
| POST | /api/v1/documents | 上传资料并创建解析任务，可通过 multipart 字段 `chunk_profile` 选择分块 Profile |
| GET | /api/v1/documents/{id}/chunks | 查看资料切片和来源元数据 |
| DELETE | /api/v1/documents/{id} | 删除资料及关联索引 |
| GET | /api/v1/tasks/{id} | 查询异步任务状态 |
| POST | /api/v1/tasks/{id}/retry | 重试失败任务 |
| POST | /api/v1/retrieval/search | 只检索，不生成答案 |
| POST | /api/v1/chat/ask | 检索增强问答，可提交白名单中的 model ID |
| POST | /api/v1/chat/agent | Agentic 问答：先意图路由，再让 LLM 在 Thought-Action-Observation 循环里检索并作答 |

### Phase 1.1 真实鉴权接口（用户名 + 密码 + JWT）

真实鉴权（安全设计）：`Authorization: Bearer <JWT>` 承载身份，HS256 签名、7 天有效；密码用 bcrypt 哈希入库，绝不出现在任何响应里。

- 无密码账号（老师建号时留空，或 u_admin 初始）首登走**引导式补设**：`/auth/login` 返回 `needs_password_setup=true` + 短效 `setup_token`（15 分钟，scope=setup），客户端用 `/auth/setup-password` 设密换取正式 token。
- 无 Authorization 头 / token 无效或过期 / scope 不符 → 一律 401（不再回退默认管理员）。
- 已删用户 → 401；role 以数据库为准（token 里的 role 仅作 DB 挂时的降级快照）。
- MySQL 不可用时：合法 token 降级为 `degraded` 身份（可继续个人问答），管理端点 fail-closed 返回 503。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | /api/v1/auth/login | 用户名+密码登录；未设密返回 setup_token，否则返回 access_token |
| POST | /api/v1/auth/setup-password | 用 setup_token 设密（已设过 → 409），成功后返回 access_token |
| POST | /api/v1/auth/change-password | 需正式 token，验证旧密码后改新密码 |

### Phase 2 班级学习库接口（身份 + 班级 + 审计 + 画像 + 会话）

身份由 Phase 1.1 的 Bearer JWT 承载（老师 admin / 班主任 head / 学生 member）。老师（admin）与学生（member）共享文档库、都能上传；上传经校验 agent 审核后进检索。

| 方法 | 路径 | 作用 | 权限 |
| --- | --- | --- | --- |
| POST | /api/v1/admin/members | 创建用户并授权进入默认班级，返回 user_id（可带初始密码） | admin |
| GET | /api/v1/classes | 班级列表 | 任意 |
| POST | /api/v1/classes | 创建班级 | admin |
| GET | /api/v1/classes/{id}/members | 班级成员 | 任意 |
| POST | /api/v1/classes/{id}/members | 授权用户入班 | admin |
| DELETE | /api/v1/classes/{id}/members/{user_id} | 移除成员 | admin |
| GET | /api/v1/admin/uploads | 审计台账：谁传了什么 + 校验结果 | admin |
| POST | /api/v1/admin/uploads/{id}/approve | 放行（被驳回的重新补索引） | admin |
| POST | /api/v1/admin/uploads/{id}/reject | 驳回（标记隐藏，不进检索） | admin |
| DELETE | /api/v1/admin/uploads/{id} | 删除上传（记录+文件+索引） | admin |
| GET/PUT | /api/v1/users/me/profile | 用户画像读写（subjects/weak_points/preferred_style） | 本人 |
| GET | /api/v1/users/me/memory | 长期记忆列表 | 本人 |
| DELETE | /api/v1/users/me/memory/{id} | 删除一条记忆 | 本人 |
| POST | /api/v1/conversations | 创建会话（可带 title） | 本人 |
| GET | /api/v1/conversations | 我的会话列表 | 本人 |
| GET | /api/v1/conversations/{id}/messages | 会话消息历史 | 本人 |
| GET | /api/v1/conversations/{id}/traces | 会话 Agent 工具链轨迹 | 本人 |
| DELETE | /api/v1/conversations/{id} | 删除会话 | 本人 |

`/chat/agent` 新增可选字段：

- `conversation_id`：带上则加载历史做多轮上下文，回答后把消息与 Agent 轨迹落库（不带则自动建新会话）。
- 画像自动注入：当前用户画像（科目/薄弱点/风格）拼进意图路由与 system prompt，beginner 给步骤化解释、advanced 给推导与反例。
- 可见性：有 upload 记录的文档只召回 approved；无 upload 记录（legacy）保持可见。

## 问答响应最小结构

~~~json
{
  "answer": "...",
  "model": "gpt-5.6-luna",
  "sources": [
    {
      "document_id": "doc_001",
      "filename": "高等数学.pdf",
      "page": 12,
      "text": "...",
      "score": 0.86,
      "rrf_score": 0.0325,
      "signals": {"bm25": 8.4, "vector": 0.78},
      "source_type": "pdf"
    }
  ],
  "retrieval": {
    "vector_top_k": 8,
    "bm25_top_k": 8,
    "rerank": true
  }
}
~~~

来源字段是多模态 RAG 的核心契约。图片来源还应补充 image_path 和可选的 bbox、confidence、metadata，不能只返回 OCR 后的文本；DOCX/PPTX 还需返回表格数据或幻灯片号。切片接口的 `metadata` 还包含 `parent_chunk_id`、`chunk_level` 和 `context_prefix`，用于父子块展开和切片检查。
