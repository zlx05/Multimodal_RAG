export type TaskStatus = "PENDING" | "SUCCEEDED" | "FAILED" | string;

export interface DocumentRecord {
  document_id: string;
  filename: string;
  size: number;
  source_type: string;
  collection_name?: string;
  topic_label?: string;
  source_url?: string;
  original_url?: string;
  chunk_profile?: string;
  /** 该资料最近一次入库任务的状态（失败任务的残留资料没有 collection，看不了切片）。 */
  task_status?: TaskStatus;
}

export interface ChunkProfileOption {
  id: string;
  label: string;
  description: string;
  parent_child?: boolean;
  contextual_retrieval?: boolean;
}

export interface TaskRecord {
  task_id: string;
  document_id: string;
  filename: string;
  status: TaskStatus;
  stage: string;
  progress: number;
  error_message?: string;
  chunks?: number;
  collection_name?: string;
  retry_count?: number;
  chunk_profile?: string;
  created_at?: number;
  updated_at?: number;
}

export interface UploadResponse {
  document_id: string;
  filename: string;
  task_id: string;
  status: TaskStatus;
  content_hash: string;
}

export interface ModelOption {
  id: string;
  label: string;
  description: string;
  ready: boolean;
  default: boolean;
}

export interface SearchSource {
  text: string;
  document_id: string;
  filename: string;
  topic_label?: string;
  original_url?: string;
  asset_url?: string | null;
  page: number | null;
  heading_path: string;
  source_type: string;
  content_type: string;
  image_path?: string | null;
  bbox?: unknown;
  confidence?: number;
  metadata?: Record<string, unknown>;
  parent_chunk_id?: string;
  chunk_level?: number;
  score: number;
  rrf_score?: number;
  signals?: Record<string, number>;
  origins: string[];
}

export interface SearchResponse {
  query: string;
  results: SearchSource[];
  count: number;
  routing: RoutingInfo;
}

export interface UsedDocument {
  document_id: string;
  filename: string;
  topic_label?: string;
  score: number;
  reason: string;
}

export interface RoutingInfo {
  mode: string;
  candidate_documents: number;
  used_documents: UsedDocument[];
  skipped_collections: string[];
}

export interface ChatResponse {
  answer: string;
  model?: string;
  sources: SearchSource[];
  used_documents?: UsedDocument[];
  retrieval: {
    scope?: "auto" | "all" | "selected";
    mode?: string;
    candidate_documents?: number;
    used_documents?: UsedDocument[];
    skipped_collections?: string[];
    top_k?: number;
    rerank?: string;
  };
}

export interface ChunkRecord {
  chunk_index: number;
  content: string;
  heading_path?: string;
  page_number?: number;
  source_type?: string;
  content_type?: string;
  image_path?: string;
  image_url?: string | null;
  original_url?: string;
  bbox?: unknown;
  confidence?: number;
  metadata?: Record<string, unknown>;
  parent_chunk_id?: string;
  chunk_level?: number;
}

export interface AssetRecord {
  filename: string;
  url: string;
  content_type: string;
}

export interface ChunksResponse {
  document_id: string;
  collection: string;
  total: number;
  offset: number;
  limit: number;
  original_url?: string;
  chunks: ChunkRecord[];
}

// ---------------------------------------------------------------- Phase 1.1 真实鉴权

export interface LoginRequest {
  username: string;
  password: string;
}

export interface SetupPasswordRequest {
  setup_token: string;
  password: string;
}

export interface ChangePasswordRequest {
  old_password: string;
  new_password: string;
}

/** POST /api/v1/auth/login 与 /auth/setup-password 的响应。 */
export interface LoginResponse {
  access_token?: string;
  token_type?: string;
  /** 引导式补设：账号还没设过密码时返回 true + setup_token，不发 access_token。 */
  needs_password_setup?: boolean;
  setup_token?: string;
  user: UserIdentity;
}

// ---------------------------------------------------------------- Phase 2 班级学习库

export type UserRole = "head" | "admin" | "member";

/** 回答风格：直接给答案 / 给思路 / 循循善诱。存量旧值 beginner/standard/advanced 兼容。 */
export type AnswerStyle = "direct" | "guiding" | "socratic";
export type LegacyStyle = "beginner" | "standard" | "advanced";
export type PreferredStyle = AnswerStyle | LegacyStyle;

export interface UserIdentity {
  id: string;
  username: string;
  role: UserRole;
}

/** GET /api/v1/users/me 的响应（登录页校验身份用）。 */
export interface MeResponse {
  user: UserIdentity;
}

export interface UserProfile {
  user_id: string;
  subjects: string[];
  weak_points: string[];
  preferred_style: PreferredStyle;
  profile_version: number;
}

/** 首次调查报告（GET /users/me/onboarding 与 POST /users/me/survey）。 */
export interface Survey {
  user_id: string;
  subjects: string[];
  weak_points: string[];
  answer_style: AnswerStyle;
}

export interface OnboardingResponse {
  needs_onboarding: boolean;
  survey: Survey | null;
}

/** GET /api/v1/admin/users 的成员管理条目。 */
export interface UserWithRole extends UserIdentity {
  created_at: number;
}

export type UploadStatus = "pending" | "approved" | "rejected" | "hidden";

/** POST /api/v1/admin/members 的响应（老师建号即入班）。 */
export interface MemberCreateResponse {
  user: UserIdentity;
  class_id: string;
}

/** GET /api/v1/admin/uploads 的审计台账条目。 */
export interface UploadAudit {
  id: string;
  document_id: string;
  class_id: string;
  uploader_user_id: string;
  filename: string;
  source_type: string;
  status: UploadStatus;
  review_payload: string;
  review_note: string;
  reviewed_by: string;
  created_at: number;
  reviewed_at?: number;
  /** 是否已真正入库（有非空 Milvus collection）。approved 但未入库=上次放行未生效。 */
  indexed?: boolean;
  uploader: {
    user_id: string;
    username: string;
  };
  document: {
    topic_label: string;
    collection_name: string;
  };
}

export interface Conversation {
  id: string;
  user_id: string;
  class_id: string;
  title: string;
  created_at: number;
  updated_at: number;
  /** 历史列表预览：最后一条真实问答的内容摘要（后端丰富）。 */
  last_message?: string;
  message_count?: number;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  model: string;
  metadata_json: string;
  created_at: number;
}

/** 长期记忆条目（user_memory，画像进化自动写入的持久行为/偏好观察）。 */
export interface MemoryRecord {
  id: string;
  user_id: string;
  memory_type: string;
  content: string;
  source_question: string;
  confidence: number;
  created_at: number;
}

export interface MemoryResponse {
  memory: MemoryRecord[];
}

export interface AgentTrace {
  id: string;
  message_id: string;
  step_index: number;
  tool: string;
  input: string;
  output: string;
  created_at: number;
}

/** /chat/agent 的单步工具调用（响应内联 trace）。 */
export interface AgentStepTrace {
  tool: string;
  input: unknown;
  output: string;
}

/** /chat/agent 的检索诊断信息（Phase 3 新增字段，旧版本后端可能缺失，全部可选）。 */
export interface AgentRetrievalInfo {
  strategy?: string;
  router?: Record<string, unknown>;
  tool_calls?: unknown[];
  top_k?: number;
  max_iterations?: number;
  /** 显式证据充分性判定（Phase 3 门控）。 */
  evidence?: {
    sufficient: boolean;
    reason: "no_evidence" | "weak_evidence" | "sufficient";
    escalated: boolean;
  };
  /** 检索前改写后的问题；与原问题相同时为 null（Phase 3）。 */
  rewritten_question?: string | null;
  /** 四段 workflow 各阶段耗时（毫秒，Phase 3）。 */
  stages?: {
    intent_ms: number;
    react_ms: number;
    persist_ms: number;
    profile_ms: number;
  };
}

/** POST /api/v1/chat/agent 的响应。 */
export interface AgentChatResponse {
  conversation_id: string | null;
  answer: string;
  model?: string;
  sources: SearchSource[];
  used_documents?: UsedDocument[];
  retrieval: AgentRetrievalInfo;
  trace: AgentStepTrace[];
}
