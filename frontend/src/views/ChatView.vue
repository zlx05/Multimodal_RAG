<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import {
  ArrowRight,
  ChatCenteredDots,
  ChatsCircle,
  CheckCircle,
  MagnifyingGlass,
  Sparkle,
  Trash,
  WarningCircle,
} from "@/components/icons";
import ModelSwitcher from "@/components/ModelSwitcher.vue";
import SourceList from "@/components/SourceList.vue";
import StateRail from "@/components/StateRail.vue";
import { renderMarkdown } from "@/utils/markdown";
import { api } from "@/api/client";
import type { AgentRetrievalInfo, Conversation, SearchSource, UsedDocument } from "@/api/types";
import { useAuthStore } from "@/stores/auth";
import { useChatStore } from "@/stores/chat";
import { useWorkbenchStore } from "@/stores/workbench";

const workbench = useWorkbenchStore();
const chatStore = useChatStore();
const auth = useAuthStore();

const question = ref("");
const status = ref<"idle" | "active" | "error">("idle");
const activeStep = ref(0);
const errorMessage = ref("");
const answerBody = ref<HTMLElement | null>(null);

// 历史会话列表（会话状态本身在 chatStore 里，跨栏目/刷新存活）。
const showHistory = ref(false);
const historyList = ref<Conversation[]>([]);
const historyLoading = ref(false);
const historyError = ref("");

const steps = [
  { key: "route", label: "意图路由", caption: "选择资料范围" },
  { key: "retrieve", label: "Agent 检索", caption: "工具调用循环" },
  { key: "generate", label: "生成回答", caption: "画像化输出" },
];

const completedDocuments = computed(() => workbench.documents);
const showManualScope = computed(() => chatStore.scope === "selected");
const canAsk = computed(() => Boolean(
  question.value.trim()
  && status.value !== "active"
  && (chatStore.scope !== "selected" || chatStore.selectedDocument),
));
/** 学生视角：不展示召回片段（SourceList 隐藏），只留「定位到整体原文件」链接。 */
const isMember = computed(() => auth.identity?.role === "member");

function updateModel(value: string) {
  chatStore.selectedModel = value;
  chatStore.persist();
}

function setScope(value: "auto" | "all" | "selected") {
  chatStore.scope = value;
  chatStore.persist();
}

function newConversation() {
  chatStore.clearActive();
  errorMessage.value = "";
  status.value = "idle";
}

/** 发送一条消息并驱动 /chat/agent 问答（输入框 / 澄清问题共用）。 */
async function sendMessage(text: string) {
  if (status.value === "active" || !text.trim()) return;
  errorMessage.value = "";
  status.value = "active";
  activeStep.value = 0;
  const documentIds = chatStore.scope === "selected" ? [chatStore.selectedDocument] : [];
  chatStore.turns.push({ role: "user", content: text.trim(), sources: [], usedDocuments: [], trace: [] });
  const questionText = text.trim();

  try {
    activeStep.value = 1;
    const result = await api.chatAgent(questionText, chatStore.selectedModel, {
      conversationId: chatStore.conversationId,
      scope: chatStore.scope,
      documentIds,
      topK: 5,
    });
    chatStore.conversationId = result.conversation_id ?? chatStore.conversationId;
    chatStore.persist();
    activeStep.value = 2;
    chatStore.turns.push({
      role: "assistant",
      content: result.answer,
      model: result.model,
      sources: result.sources,
      usedDocuments: result.used_documents ?? [],
      trace: result.trace ?? [],
      retrieval: result.retrieval,
    });
    activeStep.value = 3;
    status.value = "idle";
    await nextTick();
    answerBody.value?.scrollTo({ top: answerBody.value.scrollHeight, behavior: "smooth" });
    void loadHistory(false);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "问答失败，请稍后重试";
    status.value = "error";
    chatStore.turns.push({ role: "assistant", content: errorMessage.value, sources: [], usedDocuments: [], trace: [], error: true });
    activeStep.value = 0;
  }
}

async function ask() {
  if (!canAsk.value) return;
  const text = question.value.trim();
  question.value = "";
  void sendMessage(text);
}

/** 澄清门控（Phase 5）：证据不足时后端反问的澄清问题；正常回答为空列表。 */
function clarificationQuestionsOf(turn: { retrieval?: AgentRetrievalInfo }) {
  return turn.retrieval?.evidence?.clarification?.questions ?? [];
}

/** 点击澄清问题：直接作为新消息发出（复用发送路径，不污染输入框）。 */
function askClarification(q: string) {
  void sendMessage(q);
}

function formatTime(ts: number): string {
  const seconds = Math.floor(Date.now() / 1000 - ts);
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  if (seconds < 172800) return "昨天";
  return new Date(ts * 1000).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

/** 每轮回答命中的文档，按 document_id 去重。
 *
 * 优先从 turn.sources 取（实时与历史回放都带 original_url，比 usedDocuments 稳）；
 * sources 为空（旧版本历史）时退回 usedDocuments。
 */
function matchedDocsOf(turn: { sources: SearchSource[]; usedDocuments: UsedDocument[] }) {
  const seen = new Set<string>();
  const docs: { document_id: string; filename: string; topic_label?: string; original_url?: string }[] = [];
  for (const source of turn.sources) {
    if (!source.document_id || seen.has(source.document_id)) continue;
    seen.add(source.document_id);
    docs.push({
      document_id: source.document_id,
      filename: source.filename,
      topic_label: source.topic_label,
      original_url: source.original_url,
    });
  }
  for (const doc of turn.usedDocuments ?? []) {
    if (seen.has(doc.document_id)) continue;
    seen.add(doc.document_id);
    docs.push({ document_id: doc.document_id, filename: doc.filename, topic_label: doc.topic_label });
  }
  return docs;
}

async function loadHistory(showSpinner = true) {
  if (showSpinner) historyLoading.value = true;
  historyError.value = "";
  try {
    const result = await api.conversations();
    historyList.value = result.conversations;
  } catch (err) {
    historyError.value = err instanceof Error ? err.message : "加载历史会话失败";
  } finally {
    if (showSpinner) historyLoading.value = false;
  }
}

async function openConversation(id: string) {
  errorMessage.value = "";
  try {
    await chatStore.loadConversation(id);
    await nextTick();
    answerBody.value?.scrollTo({ top: 0 });
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : "加载会话失败";
  }
}

async function removeConversation(id: string) {
  if (!window.confirm("删除这个历史会话？该操作不可恢复。")) return;
  try {
    await api.deleteConversation(id);
    if (chatStore.conversationId === id) chatStore.clearActive();
    historyList.value = historyList.value.filter((conversation) => conversation.id !== id);
  } catch (err) {
    errorMessage.value = err instanceof Error ? err.message : "删除会话失败";
  }
}

onMounted(async () => {
  await chatStore.restoreIfEmpty();
  await loadHistory();
});

watch(() => workbench.defaultModel, (value) => {
  if (!chatStore.selectedModel || ["gpt-5.6-terra", "gpt-5.6-luna"].includes(chatStore.selectedModel)) chatStore.selectedModel = value;
}, { immediate: true });
</script>

<template>
  <section class="chat-layout chat-with-history fade-up">
    <aside class="history-panel panel" :class="{ open: showHistory }">
      <div class="history-head">
        <div class="history-title"><ChatsCircle :size="16" /> 历史会话</div>
        <button class="quiet-link" type="button" @click="newConversation">新会话</button>
      </div>

      <div v-if="historyLoading && !historyList.length" class="history-note">加载中…</div>
      <div v-else-if="historyError && !historyList.length" class="history-note">{{ historyError }}</div>
      <ul v-else-if="historyList.length" class="history-list">
        <li
          v-for="conversation in historyList"
          :key="conversation.id"
          class="history-item"
          :class="{ active: conversation.id === chatStore.conversationId }"
          @click="openConversation(conversation.id)"
        >
          <div class="history-item-main">
            <strong class="history-title-text">{{ conversation.title || "未命名会话" }}</strong>
            <span v-if="conversation.last_message" class="history-preview">{{ conversation.last_message }}</span>
            <span v-else-if="conversation.message_count" class="history-preview">{{ conversation.message_count }} 条消息</span>
          </div>
          <span class="history-time">{{ formatTime(conversation.updated_at) }}</span>
          <button class="history-delete" type="button" title="删除" @click.stop="removeConversation(conversation.id)">
            <Trash :size="14" />
          </button>
        </li>
      </ul>
      <div v-else class="history-note">还没有历史会话，先问一个问题试试</div>
    </aside>

    <div class="chat-main">
      <div class="chat-toolbar">
        <div class="scope-control" role="group" aria-label="资料范围">
          <div class="scope-options">
            <button v-for="option in ([['auto', '自动选择'], ['all', '全部资料'], ['selected', '指定资料']] as const)" :key="option[0]" class="scope-option" :class="{ active: chatStore.scope === option[0] }" type="button" @click="setScope(option[0])">
              {{ option[1] }}
            </button>
          </div>
        </div>
        <div v-if="showManualScope" class="manual-scope">
          <select id="document-select" v-model="chatStore.selectedDocument" class="control-select" @change="chatStore.persist()">
            <option value="" disabled>选择资料</option>
            <option v-for="document in completedDocuments" :key="document.document_id" :value="document.document_id">{{ document.topic_label || document.filename }} · {{ document.filename }}</option>
          </select>
        </div>
        <ModelSwitcher :models="workbench.models" :model="chatStore.selectedModel" @change="updateModel" />
        <span class="toolbar-spacer"></span>
        <button class="quiet-link history-toggle" type="button" @click="showHistory = !showHistory"><ChatsCircle :size="15" /> 历史</button>
        <button v-if="chatStore.conversationId" class="quiet-link" type="button" title="清空本轮对话" @click="newConversation">新会话</button>
      </div>

      <div v-if="status === 'active' || status === 'error'" class="answer-progress panel">
        <div class="progress-heading">
          <div><h3>{{ status === "error" ? "处理未完成" : "正在回答" }}</h3></div>
          <span v-if="status === 'error'" class="error-label"><WarningCircle :size="16" /> 失败</span>
          <span v-else class="processing-label">处理中</span>
        </div>
        <StateRail :steps="steps" :active="activeStep" :status="status" />
        <p v-if="errorMessage" class="inline-error"><WarningCircle :size="16" /> {{ errorMessage }}</p>
      </div>

      <div ref="answerBody" class="conversation">
        <div v-if="!chatStore.turns.length" class="empty-state large">
          <div class="empty-graphic"><ChatCenteredDots :size="27" /></div>
          <strong>输入问题开始检索</strong>
        </div>

        <template v-for="(turn, index) in chatStore.turns" :key="index">
          <div v-if="turn.role === 'user'" class="turn turn-user">
            <div class="turn-bubble">
              <p>{{ turn.content }}</p>
            </div>
          </div>

          <div v-else class="turn turn-assistant">
            <div class="turn-bubble" :class="{ error: turn.error }">
              <div class="turn-heading">
                <div class="answer-icon"><ChatCenteredDots :size="17" /></div>
                <span v-if="turn.model" class="model-stamp">{{ turn.model }}</span>
              </div>
              <div class="answer-copy" v-html="renderMarkdown(turn.content)"></div>
              <div v-if="matchedDocsOf(turn).length" class="answer-trace" :class="{ 'original-links': isMember }">
                <CheckCircle :size="15" />
                <span>已从 {{ matchedDocsOf(turn).length }} 份资料中找到相关内容</span>
                <!-- 学生视角：不展示召回片段，每个命中文档一个「定位到整体原文件」链接，点击打开整文件 -->
                <template v-if="isMember">
                  <a
                    v-for="doc in matchedDocsOf(turn)"
                    :key="doc.document_id"
                    :href="doc.original_url ?? ''"
                    target="_blank"
                    rel="noopener"
                    class="original-file-link"
                    :title="doc.filename"
                  >定位到整体原文件：{{ doc.topic_label || doc.filename }}</a>
                </template>
                <!-- 老师视角：保留现有不可点芯片 -->
                <template v-else>
                  <span v-for="document in turn.usedDocuments" :key="document.document_id" class="used-document">{{ document.topic_label || document.filename }}</span>
                </template>
              </div>
              <div v-if="turn.retrieval && (turn.retrieval.rewritten_question || turn.retrieval.evidence)" class="answer-evidence">
                <span v-if="turn.retrieval.rewritten_question" class="rewritten-note"><MagnifyingGlass :size="14" /> 已改写问题：{{ turn.retrieval.rewritten_question }}</span>
                <span v-if="turn.retrieval.evidence" class="evidence-chip" :class="turn.retrieval.evidence.sufficient ? 'ok' : 'weak'">
                  <CheckCircle v-if="turn.retrieval.evidence.sufficient" :size="14" />
                  <WarningCircle v-else :size="14" />
                  {{ turn.retrieval.evidence.sufficient ? "证据充分" : "证据不足" }}
                  <span v-if="turn.retrieval.evidence.escalated" class="escalated-mark">已扩全库</span>
                </span>
              </div>
              <div v-if="clarificationQuestionsOf(turn).length" class="clarification-row">
                <span
                  v-for="(q, qi) in clarificationQuestionsOf(turn)"
                  :key="qi"
                  class="clarification-chip"
                  @click="askClarification(q)"
                >{{ q }}</span>
              </div>
              <details v-if="turn.trace.length" class="trace-details">
                <summary>Agent 轨迹（{{ turn.trace.length }} 次工具调用）</summary>
                <div v-for="(step, stepIndex) in turn.trace" :key="stepIndex" class="trace-step">
                  <span class="trace-tool">{{ step.tool }}</span>
                  <pre class="trace-input">{{ JSON.stringify(step.input, null, 2) }}</pre>
                  <p class="trace-output">{{ step.output }}</p>
                </div>
              </details>
              <SourceList v-if="turn.sources.length && !isMember" :sources="turn.sources" />
            </div>
          </div>
        </template>
      </div>

      <div class="chat-composer">
        <textarea
          id="question-input"
          v-model="question"
          class="chat-composer-input"
          rows="2"
          placeholder="输入你的问题，Enter 发送，Shift+Enter 换行…"
          @keydown.enter.exact.prevent="ask"
        ></textarea>
        <button class="primary-button" type="button" :disabled="!canAsk" @click="ask"><Sparkle :size="18" weight="bold" /> {{ status === "active" ? "处理中" : "发送" }} <ArrowRight :size="16" /></button>
      </div>
    </div>
  </section>
</template>
