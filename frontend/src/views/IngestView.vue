<script setup lang="ts">
import { computed, ref } from "vue";
import { ArrowRight, CheckCircle, FileArrowUp, FileText, ImageSquare, Presentation, WarningCircle } from "@/components/icons";
import StateRail from "@/components/StateRail.vue";
import type { StateStep } from "@/components/StateRail.vue";
import { useWorkbenchStore } from "@/stores/workbench";

const store = useWorkbenchStore();
const fileInput = ref<HTMLInputElement | null>(null);
const dragging = ref(false);
const uploading = ref(false);
const uploadError = ref("");
const pageUrl = ref("");
const urlUploading = ref(false);
const selectedProfile = ref("auto");
const deletingDoc = ref("");

const steps: StateStep[] = [
  { key: "receive", label: "接收资料", caption: "文件已保存" },
  { key: "parse", label: "解析结构", caption: "识别段落与版面" },
  { key: "vision", label: "视觉理解", caption: "OCR 与公式识别" },
  { key: "chunk", label: "知识点分块", caption: "保留语义边界" },
  { key: "index", label: "向量入库", caption: "Milvus + BM25" },
  { key: "done", label: "可以检索", caption: "完成溯源索引" },
];

const stageIndex: Record<string, number> = { "": 0, PARSING: 1, OCR: 2, CHUNKING: 3, INDEXING: 4, DONE: 5 };
const activeTasks = computed(() => store.activeTasks.slice(0, 5));
const iconFor = (name: string) => {
  const ext = name.split(".").pop()?.toLowerCase();
  if (["png", "jpg", "jpeg", "bmp", "webp"].includes(ext ?? "")) return ImageSquare;
  if (["ppt", "pptx"].includes(ext ?? "")) return Presentation;
  return FileText;
};

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

function taskRail(task: { stage: string; status: string }) {
  const failed = task.status === "FAILED" || task.status === "REJECTED";
  // 校验在解析/OCR 之后（视觉理解是 REVIEW 前最后一步），驳回在 rail 上标在这里。
  const active = task.status === "SUCCEEDED" ? 5 : task.status === "REJECTED" ? 2 : stageIndex[task.stage] ?? 0;
  return {
    active,
    status: task.status === "SUCCEEDED" ? "success" : failed ? "error" : "active",
  } as const;
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
</script>

<template>
  <section class="ingest-layout fade-up">
    <div class="ingest-main">
      <div class="section-heading">
        <div>
          <h2>把一份资料变成可复习的知识</h2>
          <p>支持 PDF、Word、PPT、Markdown、图片和手写笔记。原文件会保留，解析结果会携带页码、图片位置和公式信息。</p>
        </div>
      </div>

      <input ref="fileInput" class="visually-hidden" type="file" accept=".pdf,.md,.txt,.doc,.docx,.ppt,.pptx,.png,.jpg,.jpeg,.bmp,.webp,.xlsx,.csv" @change="onInput" />
      <button class="drop-zone" :class="{ dragging }" type="button" :disabled="uploading" @click="chooseFile" @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="onDrop">
        <span class="drop-symbol"><FileArrowUp :size="28" weight="regular" /></span>
        <strong>{{ uploading ? "正在提交资料" : "拖入资料，或点击选择" }}</strong>
        <span>PDF / DOCX / PPTX / MD / Excel / 图片，单文件不超过 50 MB</span>
        <span class="drop-action"><ArrowRight :size="15" /> 选择文件</span>
      </button>
      <div class="profile-picker">
        <label class="field-label" for="chunk-profile">分块策略</label>
        <select id="chunk-profile" v-model="selectedProfile" class="control-select">
          <option v-for="profile in store.chunkProfiles" :key="profile.id" :value="profile.id">{{ profile.label }}</option>
        </select>
        <span class="field-hint">{{ store.chunkProfiles.find((profile) => profile.id === selectedProfile)?.description || "按资料类型自动选择" }}</span>
      </div>
      <form class="url-import" @submit.prevent="handleUrl">
        <div>
          <label class="field-label" for="page-url">导入网页</label>
          <input id="page-url" v-model="pageUrl" class="control-input" type="url" placeholder="粘贴公开网页地址" />
        </div>
        <button class="secondary-button" type="submit" :disabled="urlUploading || !pageUrl.trim()"><ArrowRight :size="16" /> {{ urlUploading ? "导入中" : "导入网页" }}</button>
      </form>
      <p v-if="uploadError" class="inline-error"><WarningCircle :size="16" /> {{ uploadError }}</p>

      <div class="format-strip">
        <span><FileText :size="16" /> 文本</span>
        <span><ImageSquare :size="16" /> 图片</span>
        <span><Presentation :size="16" /> 演示文稿</span>
        <span><CheckCircle :size="16" /> 公式保留</span>
      </div>
    </div>

    <aside class="ingest-side panel">
      <div class="panel-heading">
        <div>
          <h3>处理状态</h3>
        </div>
        <span class="live-label">{{ store.activeTaskCount }} 个进行中</span>
      </div>
      <div v-if="activeTasks.length" class="task-stack">
        <article v-for="task in activeTasks" :key="task.task_id" class="task-card">
          <div class="task-card-head">
            <div class="task-file-icon"><component :is="iconFor(task.filename)" :size="18" /></div>
            <div>
              <strong>{{ task.filename }}</strong>
              <span>{{ task.status === "FAILED" ? "处理失败" : task.status === "REJECTED" ? "校验未通过" : task.status === "SUCCEEDED" ? `${task.chunks ?? 0} 个切片已入库` : task.stage || "排队中" }}</span>
            </div>
            <span class="task-percent">{{ task.progress }}%</span>
          </div>
          <StateRail :steps="steps" :active="taskRail(task).active" :status="taskRail(task).status" />
          <div v-if="task.status === 'FAILED'" class="task-error">
            <WarningCircle :size="15" /> {{ task.error_message || "任务处理失败" }}
            <button class="text-button" type="button" @click="store.retry(task.task_id)">重新处理</button>
            <button v-if="task.document_id" class="text-button" type="button" :disabled="deletingDoc === task.document_id" @click="deleteFailedTask(task)">{{ deletingDoc === task.document_id ? "删除中…" : "删除残留" }}</button>
          </div>
          <div v-else-if="task.status === 'REJECTED'" class="task-error">
            <WarningCircle :size="15" /> {{ task.error_message || "内容校验未通过，未入库" }}
            <span class="task-error-hint">可在「班级管理 → 上传审计」放行后重新入库</span>
            <button v-if="task.document_id" class="text-button" type="button" :disabled="deletingDoc === task.document_id" @click="deleteFailedTask(task)">{{ deletingDoc === task.document_id ? "删除中…" : "删除残留" }}</button>
          </div>
          <div v-else-if="task.status === 'SUCCEEDED'" class="task-success"><CheckCircle :size="15" /> 已完成，可以前往问答</div>
        </article>
      </div>
      <div v-else class="empty-state">
        <div class="empty-graphic"><FileArrowUp :size="23" /></div>
        <strong>还没有运行中的任务</strong>
        <span>上传资料后，这里会显示解析、OCR、分块和入库的实时状态。</span>
      </div>
    </aside>
  </section>
</template>
