import { defineStore } from "pinia";
import { api, currentUserId } from "@/api/client";
import type {
  AgentRetrievalInfo,
  AgentStepTrace,
  AgentTrace,
  SearchSource,
  UsedDocument,
} from "@/api/types";

export type ChatScope = "auto" | "all" | "selected";

/** 一轮问答（用户提问或助手回答），与 ChatView 展示结构一致。 */
export interface Turn {
  /** 后端消息 id：历史会话回放时用于挂 Agent 轨迹。 */
  messageId?: string;
  role: "user" | "assistant";
  content: string;
  model?: string;
  sources: SearchSource[];
  usedDocuments: UsedDocument[];
  trace: AgentStepTrace[];
  retrieval?: AgentRetrievalInfo;
  error?: boolean;
}

const STORAGE_PREFIX = "rag_chat_";

function storageKey(): string {
  return `${STORAGE_PREFIX}${currentUserId() ?? "guest"}`;
}

function loadPersisted(): { conversationId: string | null; scope: ChatScope; selectedModel: string; selectedDocument: string } {
  try {
    const raw = localStorage.getItem(storageKey());
    if (raw) {
      const parsed = JSON.parse(raw);
      return {
        conversationId: typeof parsed.conversationId === "string" ? parsed.conversationId : null,
        scope: (["auto", "all", "selected"] as const).includes(parsed.scope) ? parsed.scope : "auto",
        selectedModel: typeof parsed.selectedModel === "string" ? parsed.selectedModel : "",
        selectedDocument: typeof parsed.selectedDocument === "string" ? parsed.selectedDocument : "",
      };
    }
  } catch {
    // 损坏的 localStorage 忽略，按新会话处理。
  }
  return { conversationId: null, scope: "auto", selectedModel: "", selectedDocument: "" };
}

function parseJson(input: string): unknown {
  try {
    return JSON.parse(input);
  } catch {
    return input;
  }
}

function usedDocumentsFromSources(sources: SearchSource[]): UsedDocument[] {
  const seen = new Set<string>();
  const docs: UsedDocument[] = [];
  for (const source of sources) {
    if (seen.has(source.document_id)) continue;
    seen.add(source.document_id);
    docs.push({
      document_id: source.document_id,
      filename: source.filename,
      topic_label: source.topic_label,
      score: source.score,
      reason: "历史会话引用",
    });
  }
  return docs;
}

/**
 * 问答会话状态：跨栏目保留、刷新后从后端恢复、历史会话列表加载。
 *
 * 只把 conversation_id 等轻量信息写 localStorage（turns 始终以后端为准，
 * 避免本地存储膨胀）；切换用户时 key 带 user_id，天然隔离。
 */
export const useChatStore = defineStore("chat", {
  state: () => {
    const persisted = loadPersisted();
    return {
      turns: [] as Turn[],
      conversationId: persisted.conversationId,
      scope: persisted.scope as ChatScope,
      selectedDocument: persisted.selectedDocument,
      selectedModel: persisted.selectedModel,
      /** 这份状态属于哪个用户：切换登录用户时重载各自持久化的会话（每个用户登出/再登录都保留）。 */
      loadedForUser: currentUserId() ?? "guest",
    };
  },
  actions: {
    /** 当前登录用户变化后，把 store 重载为「该用户」持久化的会话状态。
     *
     * 登出不清任何东西（localStorage 与后端都保留）；只在进入问答页时比对用户，
     * 换了人就切到那个人的会话引用，避免把上一个用户的内存会话展示给下一个用户。
     */
    syncForCurrentUser() {
      const uid = currentUserId() ?? "guest";
      if (uid === this.loadedForUser) return;
      this.loadedForUser = uid;
      const persisted = loadPersisted();
      this.turns = [];
      this.conversationId = persisted.conversationId;
      this.scope = persisted.scope as ChatScope;
      this.selectedModel = persisted.selectedModel;
      this.selectedDocument = persisted.selectedDocument;
    },
    persist() {
      try {
        localStorage.setItem(
          storageKey(),
          JSON.stringify({
            conversationId: this.conversationId,
            scope: this.scope,
            selectedModel: this.selectedModel,
            selectedDocument: this.selectedDocument,
          }),
        );
      } catch {
        // 配额或隐私模式：忽略，会话仍在内存里，仅刷新后不自动恢复。
      }
    },
    /** 从后端重建一轮完整会话（历史列表点开 / 刷新恢复共用）。 */
    async loadConversation(conversationId: string) {
      const [{ messages }, { traces }] = await Promise.all([
        api.conversationMessages(conversationId),
        api.conversationTraces(conversationId),
      ]);
      const tracesByMessage = new Map<string, AgentStepTrace[]>();
      for (const trace of traces as AgentTrace[]) {
        const list = tracesByMessage.get(trace.message_id) ?? [];
        list.push({ tool: trace.tool, input: parseJson(trace.input), output: trace.output });
        tracesByMessage.set(trace.message_id, list);
      }
      const turns: Turn[] = messages.map((message) => {
        if (message.role === "user") {
          return { messageId: message.id, role: "user", content: message.content, sources: [], usedDocuments: [], trace: [] };
        }
        let sources: SearchSource[] = [];
        try {
          const meta = JSON.parse(message.metadata_json || "{}") as { sources?: SearchSource[] };
          sources = Array.isArray(meta.sources) ? meta.sources : [];
        } catch {
          // 无来源元数据的历史消息（旧版本）按空来源展示。
        }
        return {
          messageId: message.id,
          role: "assistant",
          content: message.content,
          model: message.model || undefined,
          sources,
          usedDocuments: usedDocumentsFromSources(sources),
          trace: tracesByMessage.get(message.id) ?? [],
        };
      });
      this.turns = turns;
      this.conversationId = conversationId;
      this.persist();
    },
    /**
     * 问答页挂载时调用：切到当前用户；同一次登录内（切栏目/刷新）从后端恢复进行中的会话。
     *
     * 「每次登录都是新会话」由 login 时 `clearPersistedSession` 保证（登录清掉本地引用后，
     * 这里没有 conversationId 可恢复 → 空会话）；历史只能主动点选进入。切栏目时 store 是
     * 应用级单例、turns 仍在内存 → 直接保留；刷新后 store 重建 → 用本地引用从后端恢复。
     */
    async restoreIfEmpty() {
      this.syncForCurrentUser();
      if (this.turns.length > 0 || !this.conversationId) return;
      try {
        await this.loadConversation(this.conversationId);
      } catch {
        // 会话已被删除或数据库不可用：清空本地引用，不阻塞进入问答页。
        this.conversationId = null;
        this.persist();
      }
    },
    /** 新会话：清空当前对话与本地引用。 */
    clearActive() {
      this.turns = [];
      this.conversationId = null;
      this.selectedDocument = "";
      try {
        localStorage.removeItem(storageKey());
      } catch {
        // ignore
      }
    },
    /** 登录/登出时清空当前用户的会话引用，保证下一次进入是全新会话。
     *
     * 用户要求「每次登录都是新会话」——只有把本地 conversation_id 引用也清掉，
     * 登录后才必然空会话（历史仍完整留在后端，可在历史列表主动点开）。
     * 后端数据不动，跨用户隔离仍由 key 里的 user_id 保证。
     * 注意：必须在 auth 清掉 user_id 之前调用（action 开头就快照 uid，顺序无关）。
     */
    clearPersistedSession() {
      const uid = currentUserId() ?? "guest";
      this.turns = [];
      this.conversationId = null;
      this.scope = "auto";
      this.selectedModel = "";
      this.selectedDocument = "";
      try {
        localStorage.removeItem(`${STORAGE_PREFIX}${uid}`);
      } catch {
        // ignore
      }
    },
  },
});
