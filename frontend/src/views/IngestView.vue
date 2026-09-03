<script setup lang="ts">
import { computed, ref } from "vue";
import { ArrowRight, FileArrowUp, WarningCircle } from "@/components/icons";
import { useWorkbenchStore } from "@/stores/workbench";

const store = useWorkbenchStore();
const fileInput = ref<HTMLInputElement | null>(null);
const dragging = ref(false);
const uploading = ref(false);
const uploadError = ref("");
const pageUrl = ref("");
const urlUploading = ref(false);
const showUrlInput = ref(false);
const selectedProfile = ref("auto");
const deletingDoc = ref("");

const activeTasks = computed(() => store.activeTasks.slice(0, 5));

function chooseFile() {
  fileInput.value?.click();
}

async function handleFile(file?: File) {
  if (!file) return;
  uploadError.value = "";
  uploading.value = true;
  try {
    await store.upload(file, selectedProfile.value);
  } catch (error) {
    uploadError.value = error instanceof Error ? error.message : "上传失败，请稍后重试";
  } finally {
    uploading.value = false;
  }
}

async function handleUrl() {
  const url = pageUrl.value.trim();
  if (!url || urlUploading.value) return;
  uploadError.value = "";
  urlUploading.value = true;
  try {
    await store.uploadUrl(url, selectedProfile.value);
    pageUrl.value = "";
  } catch (error) {
    uploadError.value = error instanceof Error ? error.message : "网页导入失败，请稍后重试";
  } finally {
    urlUploading.value = false;
  }
}

function onInput(event: Event) {
  const target = event.target as HTMLInputElement;
  void handleFile(target.files?.[0]);
  target.value = "";
}

function onDrop(event: DragEvent) {
  dragging.value = false;
  void handleFile(event.dataTransfer?.files[0]);
}

async function deleteFailedTask(task: { task_id: string; document_id?: string }) {
  if (!task.document_id) return;
  if (!window.confirm("确定删除这份失败残留的资料吗？原文件会一并移除。")) return;
  deletingDoc.value = task.document_id;
  try {
    await store.deleteDocument(task.document_id);
    // 已从 activeTasks 移除失败项，重新拉取任务列表
    await store.refreshActiveTasks();
  } catch (error) {
    // 任务卡片区域没有独立错误位，静默失败；重新拉取状态让用户看到真实情况
    await store.refreshActiveTasks();
  } finally {
    deletingDoc.value = "";
  }
}

/** 状态词：任务卡顶部的短标签。 */
function statusWord(status: string): string {
  if (status === "SUCCEEDED") return "已完成";
  if (status === "FAILED") return "失败";
  if (status === "REJECTED") return "已驳回";
  return "处理中";
}

/** 状态描述：任务卡主信息行。 */
function taskStatusText(task: { status: string; stage?: string; progress?: number; chunks?: number; error_message?: string }): string {
  if (task.status === "FAILED") return task.error_message || "处理失败";
  if (task.status === "REJECTED") return task.error_message || "内容校验未通过";
  if (task.status === "SUCCEEDED") return `${task.chunks ?? 0} 个切片已入库`;
  return `${task.stage || "排队中"}${typeof task.progress === "number" ? ` · ${task.progress}%` : ""}`;
}

function formatTaskTime(ts?: number): string {
  if (!ts) return "";
  const seconds = Math.floor(Date.now() / 1000 - ts);
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return new Date(ts * 1000).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}
</script>

<template>
  <section class="new-ingest-layout">
    <div class="new-ingest-main">
      <div class="new-ingest-header">
        <h2>上传资料，生成可复习的知识</h2>
        <p>支持 PDF、Word、PPT、Markdown、Excel、图片和手写笔记。原文件会保留，解析结果会携带页码、图片位置和公式信息。</p>
      </div>

      <input ref="fileInput" class="visually-hidden" type="file" accept=".pdf,.md,.txt,.doc,.docx,.ppt,.pptx,.png,.jpg,.jpeg,.bmp,.webp,.xlsx,.csv" @change="onInput" />

      <div class="new-upload-zone" :class="{ dragging, uploading }" @click="chooseFile" @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="onDrop">
        <div class="upload-icon">
          <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="32" cy="32" r="30" stroke="currentColor" stroke-width="2" stroke-dasharray="4 4" opacity="0.3"/>
            <path d="M32 20V44M20 32H44" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
          </svg>
        </div>
        <strong>{{ uploading ? "正在上传..." : "拖入文件，或点击选择" }}</strong>
        <div class="new-upload-options" @click.stop>
          <button class="upload-option-btn" type="button" :disabled="uploading" @click="chooseFile">选择文件</button>
          <button class="upload-option-btn" type="button" :disabled="uploading" @click="showUrlInput = !showUrlInput">导入网页</button>
        </div>
        <span class="upload-hint">推荐单文件不超过 50 MB</span>
      </div>

      <form v-if="showUrlInput" class="url-import" @submit.prevent="handleUrl">
        <div>
          <label class="field-label" for="page-url">导入网页</label>
          <input id="page-url" v-model="pageUrl" class="control-input" type="url" placeholder="粘贴公开网页地址" />
        </div>
        <button class="secondary-button" type="submit" :disabled="urlUploading || !pageUrl.trim()">
          <ArrowRight :size="16" /> {{ urlUploading ? "导入中" : "导入网页" }}
        </button>
      </form>

      <p v-if="uploadError" class="inline-error"><WarningCircle :size="16" /> {{ uploadError }}</p>
    </div>

    <aside class="new-ingest-sidebar">
      <div class="sidebar-header">
        <h3>处理状态</h3>
        <span class="status-badge">{{ store.activeTaskCount }} 个进行中</span>
      </div>
      <div class="sidebar-status">
        <div class="status-card">
          <h4>最近任务</h4>
          <div v-if="activeTasks.length" class="task-list-new">
            <article v-for="task in activeTasks" :key="task.task_id" class="task-item-new">
              <div class="task-header-new">
                <span class="task-label">{{ statusWord(task.status) }}</span>
                <span class="task-date">{{ formatTaskTime(task.updated_at ?? task.created_at) }}</span>
              </div>
              <div class="task-info-new">
                <strong>{{ task.filename }}</strong>
                <span>{{ taskStatusText(task) }}</span>
              </div>
              <div v-if="task.status === 'FAILED' || task.status === 'REJECTED'" class="task-actions-new">
                <button v-if="task.status === 'FAILED'" class="text-button" type="button" @click="store.retry(task.task_id)">重新处理</button>
                <button v-if="task.document_id" class="text-button" type="button" :disabled="deletingDoc === task.document_id" @click="deleteFailedTask(task)">{{ deletingDoc === task.document_id ? "删除中…" : "删除残留" }}</button>
              </div>
            </article>
          </div>
          <div v-else class="empty-task-state">
            <FileArrowUp :size="32" style="opacity: 0.3;" />
            <p>还没有运行中的任务</p>
          </div>
        </div>
      </div>
    </aside>
  </section>
</template>
