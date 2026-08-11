import type {
  AgentChatResponse,
  AgentTrace,
  AnswerStyle,
  ChatResponse,
  ChunkProfileOption,
  ChunksResponse,
  Conversation,
  DocumentRecord,
  LoginResponse,
  MeResponse,
  MemberCreateResponse,
  MemoryResponse,
  Message,
  ModelOption,
  OnboardingResponse,
  SearchResponse,
  TaskRecord,
  UploadAudit,
  UploadResponse,
  UserProfile,
  UserWithRole,
} from "./types";

const API_ROOT = import.meta.env.VITE_API_ROOT ?? "";
const TOKEN_KEY = "rag_token";
const USER_ID_KEY = "rag_user_id";

// ---------------- token（Phase 1.1 真实鉴权；Authorization: Bearer 由 request 注入）

/** 读取 access token（auth store 与 client 共用同一个 localStorage key）。 */
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function saveToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/**
 * 读取当前身份 user_id（legacy：仅 chat.ts 用作会话本地存储命名空间）。
 * 鉴权已改由 token 承载，这里不再作为请求头；但登录/登出必须与 token 同步写/清，
 * 否则切换用户时聊天记录命名空间会串。
 */
export function currentUserId(): string | null {
  return localStorage.getItem(USER_ID_KEY);
}

export function saveUserId(userId: string): void {
  localStorage.setItem(USER_ID_KEY, userId);
}

export function clearUserId(): void {
  localStorage.removeItem(USER_ID_KEY);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const token = getToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers });
  if (response.status === 401) {
    // token 缺失/失效/伪造/用户已删 → 清登录态并广播，让监听方（App.vue）跳登录页。
    // 不在 request 里直接 push 路由，避免与 router 守卫互相循环。
    clearToken();
    clearUserId();
    window.dispatchEvent(new Event("auth:unauthorized"));
  }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = (await response.json()) as { detail?: string };
      message = body.detail ?? message;
    } catch {
      // Keep the status message when the server did not return JSON.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

function jsonInit(body: unknown, method = "POST"): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const api = {
  health: () => request<{ status: string; milvus: string }>("/api/v1/health"),
  documents: () => request<{ documents: DocumentRecord[] }>("/api/v1/documents"),
  upload: (file: File, chunkProfile = "auto") => {
    const data = new FormData();
    data.append("file", file);
    data.append("chunk_profile", chunkProfile);
    return request<UploadResponse>("/api/v1/documents", { method: "POST", body: data });
  },
  uploadUrl: (url: string, chunkProfile = "auto") => request<UploadResponse>("/api/v1/documents/url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, chunk_profile: chunkProfile }),
  }),
  task: (taskId: string) => request<TaskRecord>(`/api/v1/tasks/${taskId}`),
  tasks: () => request<{ tasks: TaskRecord[] }>("/api/v1/tasks"),
  retryTask: (taskId: string) => request<TaskRecord>(`/api/v1/tasks/${taskId}/retry`, { method: "POST" }),
  models: () => request<{ models: ModelOption[]; default_model: string }>("/api/v1/models"),
  chunkProfiles: () => request<{ profiles: ChunkProfileOption[] }>("/api/v1/documents/profiles"),
  chunks: (documentId: string) => request<ChunksResponse>(`/api/v1/documents/${documentId}/chunks?limit=200`),
  deleteDocument: (documentId: string) =>
    request<{ deleted: string }>(`/api/v1/documents/${documentId}`, { method: "DELETE" }),
  assets: (documentId: string) => request<{ document_id: string; assets: import("./types").AssetRecord[] }>(`/api/v1/documents/${documentId}/assets`),
  search: (question: string, options: { scope?: "auto" | "all" | "selected"; documentIds?: string[]; topK?: number } = {}) =>
    request<SearchResponse>("/api/v1/retrieval/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        scope: options.scope ?? "auto",
        document_ids: options.documentIds ?? [],
        top_k: options.topK ?? 5,
      }),
    }),
  ask: (question: string, model: string, options: { scope?: "auto" | "all" | "selected"; documentIds?: string[]; topK?: number } = {}) =>
    request<ChatResponse>("/api/v1/chat/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        model,
        scope: options.scope ?? "auto",
        document_ids: options.documentIds ?? [],
        top_k: options.topK ?? 5,
      }),
    }),

  // ---------------------------------------------------------------- Phase 1.1 真实鉴权

  /** 用户名+密码登录。账号没设过密码时返回 needs_password_setup + setup_token。 */
  login: (username: string, password: string) =>
    request<LoginResponse>("/api/v1/auth/login", jsonInit({ username, password })),
  /** 引导式补设密码（scope=setup 短效 token），成功后返回正式 access_token。 */
  setupPassword: (setupToken: string, password: string) =>
    request<LoginResponse>("/api/v1/auth/setup-password", jsonInit({ setup_token: setupToken, password })),
  /** 修改密码（需正式 token，验证旧密码）。 */
  changePassword: (oldPassword: string, newPassword: string) =>
    request<{ ok: boolean }>("/api/v1/auth/change-password", jsonInit({ old_password: oldPassword, new_password: newPassword })),

  // ---------------------------------------------------------------- Phase 2 班级学习库

  /** 当前身份（登录页校验：管理员/学生）。 */
  me: () => request<MeResponse>("/api/v1/users/me"),

  /** Agentic 问答：带 conversation_id 时多轮续聊。 */
  chatAgent: (question: string, model: string, options: { conversationId?: string | null; scope?: "auto" | "all" | "selected"; documentIds?: string[]; topK?: number } = {}) =>
    request<AgentChatResponse>("/api/v1/chat/agent", jsonInit({
      question,
      model,
      conversation_id: options.conversationId ?? null,
      scope: options.scope ?? "auto",
      document_ids: options.documentIds ?? [],
      top_k: options.topK ?? 5,
    })),

  // 画像
  profile: () => request<UserProfile>("/api/v1/users/me/profile"),
  updateProfile: (body: { subjects?: string[]; weak_points?: string[]; preferred_style?: AnswerStyle }) =>
    request<UserProfile>("/api/v1/users/me/profile", jsonInit(body, "PUT")),

  // 首次调查报告
  onboarding: () => request<OnboardingResponse>("/api/v1/users/me/onboarding"),
  submitSurvey: (body: { subjects: string[]; weak_points: string[]; answer_style: AnswerStyle }) =>
    request<OnboardingResponse>("/api/v1/users/me/survey", jsonInit(body)),

  // 会话
  conversations: () => request<{ conversations: Conversation[] }>("/api/v1/conversations"),
  conversationMessages: (id: string) => request<{ messages: Message[] }>(`/api/v1/conversations/${id}/messages`),
  conversationTraces: (id: string) => request<{ traces: AgentTrace[] }>(`/api/v1/conversations/${id}/traces`),
  deleteConversation: (id: string) =>
    request<{ deleted: string }>(`/api/v1/conversations/${id}`, { method: "DELETE" }),

  // 长期记忆（画像进化自动写入的持久观察）
  memory: () => request<MemoryResponse>("/api/v1/users/me/memory"),
  deleteMemory: (memoryId: string) =>
    request<{ deleted: string }>(`/api/v1/users/me/memory/${memoryId}`, { method: "DELETE" }),

  // 管理员：学生/老师/成员管理
  createStudent: (username: string, password?: string) =>
    request<MemberCreateResponse>("/api/v1/admin/members", jsonInit({ username, password: password ?? undefined })),
  createTeacher: (username: string, password?: string) =>
    request<MemberCreateResponse>("/api/v1/admin/teachers", jsonInit({ username, password: password ?? undefined })),
  listUsers: () => request<{ users: UserWithRole[] }>("/api/v1/admin/users"),
  deleteUser: (userId: string) =>
    request<{ deleted: string }>(`/api/v1/admin/users/${userId}`, { method: "DELETE" }),

  // 管理员：查看某学生的画像/长期记忆（只读，教师端学生画像页签）
  profileOf: (userId: string) => request<UserProfile>(`/api/v1/admin/users/${userId}/profile`),
  memoryOf: (userId: string) => request<MemoryResponse>(`/api/v1/admin/users/${userId}/memory`),

  adminUploads: (status?: string) =>
    request<{ uploads: UploadAudit[] }>(`/api/v1/admin/uploads${status ? `?status=${status}` : ""}`),
  approveUpload: (uploadId: string) =>
    request<{ status: string; task_id?: string }>(`/api/v1/admin/uploads/${uploadId}/approve`, { method: "POST" }),
  rejectUpload: (uploadId: string) =>
    request<{ status: string }>(`/api/v1/admin/uploads/${uploadId}/reject`, { method: "POST" }),
  deleteUpload: (uploadId: string) =>
    request<{ deleted: string; document_id: string }>(`/api/v1/admin/uploads/${uploadId}`, { method: "DELETE" }),
};
