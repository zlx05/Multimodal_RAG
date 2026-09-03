<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ArrowRight, BracketsCurly, CheckCircle, FileMagnifyingGlass, Hash, WarningCircle } from "@/components/icons";
import { useRoute } from "vue-router";
import { api } from "@/api/client";
import type { AssetRecord, ChunkRecord } from "@/api/types";
import { useAuthStore } from "@/stores/auth";
import { useWorkbenchStore } from "@/stores/workbench";

const store = useWorkbenchStore();
const auth = useAuthStore();
const route = useRoute();
const selectedDocument = ref(String(route.query.document ?? ""));
const chunks = ref<ChunkRecord[]>([]);
const assets = ref<AssetRecord[]>([]);
const selectedIndex = ref(0);
const loading = ref(false);
const errorMessage = ref("");
const deleting = ref(false);

const selectedChunk = computed(() => chunks.value[selectedIndex.value]);
const totalChars = computed(() => chunks.value.reduce((sum, chunk) => sum + chunk.content.length, 0));
const maxChars = computed(() => Math.max(...chunks.value.map((chunk) => chunk.content.length), 1));
const htmlPreviewChunks = computed(() => chunks.value.filter((chunk, index) => {
  const previous = chunks.value[index - 1];
  return !(
    chunk.content_type === "heading"
    && previous?.content_type === "heading"
    && previous.content === chunk.content
  );
}));
const fileType = computed(() => store.documents.find((item) => item.document_id === selectedDocument.value)?.source_type ?? "");
const selectedDocumentRecord = computed(() => store.documents.find((item) => item.document_id === selectedDocument.value));
// original_url 由后端生成（签名临时 URL）；库内记录缺失时留空，避免拼出未签名链接。
const originalUrl = computed(() => selectedDocumentRecord.value?.original_url ?? "");
const originalKind = computed(() => {
  const filename = selectedDocumentRecord.value?.filename.toLowerCase() ?? "";
  if (filename.endsWith(".pdf")) return "pdf";
  if (filename.endsWith(".html") || filename.endsWith(".htm")) return "html";
  if (/\.(png|jpe?g|webp|bmp)$/.test(filename)) return "image";
  return "file";
});
/** 选中资料入库失败（失败残留没有 collection，切片不可用）。 */
const isFailedDoc = computed(() => selectedDocumentRecord.value?.task_status === "FAILED");
/** 页码范围："3-4"；单页或未知回退成单页/文档级。 */
function pageRangeOf(chunk: ChunkRecord): string {
  const start = chunk.metadata?.page_start;
  const end = chunk.metadata?.page_end;
  if (typeof start === "number") {
    return end && end !== start ? `${start}–${end}` : `第 ${start} 页`;
  }
  if (typeof chunk.page_number === "number") return `第 ${chunk.page_number} 页`;
  return "文档级";
}
async function deleteSelected() {
  if (!selectedDocument.value || !auth.isAdmin) return;
  if (!window.confirm("确定删除这份资料吗？原文件与已入库的切片都会被移除。")) return;
  deleting.value = true;
  errorMessage.value = "";
  try {
    await store.deleteDocument(selectedDocument.value);
    selectedDocument.value = "";
    chunks.value = [];
    assets.value = [];
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "删除失败";
  } finally {
    deleting.value = false;
  }
}

async function loadChunks() {
  if (!selectedDocument.value) {
    chunks.value = [];
    assets.value = [];
    return;
  }
  loading.value = true;
  errorMessage.value = "";
  try {
    const result = await api.chunks(selectedDocument.value);
    chunks.value = result.chunks;
    const firstContentIndex = chunks.value.findIndex((chunk) => chunk.content_type !== "heading");
    selectedIndex.value = firstContentIndex >= 0 ? firstContentIndex : 0;
    try {
      assets.value = (await api.assets(selectedDocument.value)).assets;
    } catch {
      assets.value = [];
    }
  } catch (error) {
    chunks.value = [];
    assets.value = [];
    errorMessage.value = error instanceof Error ? error.message : "切片加载失败";
  } finally {
    loading.value = false;
  }
}

function chunkHeight(chunk: ChunkRecord) {
  return `${Math.max(24, Math.round((chunk.content.length / maxChars.value) * 78))}px`;
}

/** 表格块：从 metadata.table 还原嵌套行；无结构化数据时回退为纯文本展示。 */
function tableRows(chunk: ChunkRecord): unknown[][] {
  const raw = chunk.metadata?.table;
  if (Array.isArray(raw) && raw.length > 0 && Array.isArray(raw[0])) return raw as unknown[][];
  return [];
}

watch(selectedDocument, () => void loadChunks());
if (selectedDocument.value) void loadChunks();
</script>

<template>
  <section class="new-chunks-layout">
    <div class="new-chunks-header">
      <h2>切片检查</h2>
      <div class="chunks-controls">
        <select v-model="selectedDocument" class="new-document-select">
          <option value="">选择已入库资料</option>
          <option v-for="document in store.documents" :key="document.document_id" :value="document.document_id">
            {{ document.topic_label || document.filename }} · {{ document.filename }}{{ document.task_status === "FAILED" ? "（处理失败）" : "" }}
          </option>
        </select>
        <button v-if="isFailedDoc && auth.isAdmin" class="secondary-button" type="button" :disabled="deleting" @click="deleteSelected">{{ deleting ? "删除中…" : "删除失败残留" }}</button>
      </div>
    </div>

    <div v-if="isFailedDoc" class="empty-state large">
      <WarningCircle :size="28" />
      <strong>这份资料处理失败了</strong>
      <span>它没有可查看的切片。可删除残留后重新上传，或直接重试原任务。</span>
    </div>
    <div v-else-if="loading" class="skeleton-panel">
      <div class="skeleton-line wide"></div>
      <div class="skeleton-line"></div>
      <div class="skeleton-line short"></div>
    </div>
    <div v-else-if="errorMessage" class="empty-state large">
      <WarningCircle :size="28" />
      <strong>暂时无法展示切片</strong>
      <span>{{ errorMessage }}</span>
    </div>
    <div v-else-if="!chunks.length" class="new-chunks-empty">
      <FileMagnifyingGlass :size="64" />
      <h3>选择一份资料开始检查</h3>
      <p>切片会按原始顺序排列，点击任意区块查看完整内容</p>
    </div>
    <template v-else>
      <div class="chunks-layout fade-up">
        <div class="chunks-main">
          <section class="original-document panel">
            <div class="panel-heading">
              <div><h3>{{ selectedDocumentRecord?.topic_label || selectedDocumentRecord?.filename }}</h3></div>
              <a class="text-button" :href="originalUrl" target="_blank" rel="noreferrer"><FileMagnifyingGlass :size="15" /> 打开原文</a>
            </div>
            <iframe v-if="originalKind === 'pdf'" class="original-frame" :src="originalUrl" :title="selectedDocumentRecord?.filename"></iframe>
            <article v-else-if="originalKind === 'html'" class="html-preview" aria-label="网页正文预览">
              <div class="html-preview-content">
                <template v-for="chunk in htmlPreviewChunks" :key="chunk.chunk_index">
                  <h2 v-if="chunk.content_type === 'heading'">{{ chunk.content }}</h2>
                  <figure v-else-if="chunk.image_url" class="html-preview-image">
                    <img :src="chunk.image_url" :alt="chunk.content" />
                    <figcaption>{{ chunk.content }}</figcaption>
                  </figure>
                  <pre v-else-if="chunk.content_type === 'code'">{{ chunk.content }}</pre>
                  <p v-else>{{ chunk.content }}</p>
                </template>
              </div>
            </article>
            <img v-else-if="originalKind === 'image'" class="original-image" :src="originalUrl" :alt="selectedDocumentRecord?.filename" />
            <div v-else class="original-file-card"><FileMagnifyingGlass :size="22" /><strong>{{ selectedDocumentRecord?.filename }}</strong><span>浏览器不直接渲染此格式，可打开原文件查看。</span><a class="primary-button" :href="originalUrl" target="_blank" rel="noreferrer">打开文件 <ArrowRight :size="15" /></a></div>
            <div v-if="assets.length" class="asset-strip"><span class="field-label">解析出的图片</span><div class="asset-grid"><a v-for="asset in assets" :key="asset.url" :href="asset.url" target="_blank" rel="noreferrer"><img :src="asset.url" :alt="asset.filename" loading="lazy" /></a></div></div>
          </section>

          <div class="chunk-summary panel">
            <div class="summary-item"><span>切片数量</span><strong>{{ chunks.length }}</strong></div>
            <div class="summary-item"><span>平均长度</span><strong>{{ Math.round(totalChars / chunks.length) }} <small>chars</small></strong></div>
            <div class="summary-item"><span>资料类型</span><strong>{{ fileType.toUpperCase() }}</strong></div>
            <div class="summary-legend"><CheckCircle :size="15" /> 已保留知识点边界</div>
          </div>

          <div class="chunk-map panel">
            <div class="panel-heading"><div><h3>知识块分布</h3></div><span class="source-note">按原始顺序</span></div>
            <div class="chunk-bars" aria-label="切片长度分布">
              <button v-for="(chunk, index) in chunks" :key="chunk.chunk_index" class="chunk-bar" :class="{ selected: index === selectedIndex }" :style="{ height: chunkHeight(chunk) }" :title="`第 ${index + 1} 个切片`" type="button" @click="selectedIndex = index"><span>{{ index + 1 }}</span></button>
            </div>
            <div class="chunk-axis"><span>开头</span><span>结尾</span></div>
          </div>

          <div class="chunk-inspector panel">
            <div class="inspector-heading"><div class="chunk-number"><Hash :size="16" /> {{ String(selectedChunk?.chunk_index ?? 0).padStart(2, "0") }}</div><div><h3>{{ selectedChunk?.heading_path || "未命名知识段" }}</h3></div><span class="type-tag">{{ selectedChunk?.content_type || "text" }}</span></div>
            <div v-if="selectedChunk?.content_type === 'table' && tableRows(selectedChunk).length" class="chunk-content chunk-content-table">
              <table class="chunk-table">
                <tbody>
                  <tr v-for="(row, i) in tableRows(selectedChunk)" :key="i">
                    <td v-for="(cell, j) in row" :key="j">{{ cell }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="chunk-content">{{ selectedChunk?.content }}</div>
            <div class="chunk-meta-grid">
              <div><span>来源页码</span><strong>{{ selectedChunk ? pageRangeOf(selectedChunk) : "—" }}</strong></div>
              <div><span>来源类型</span><strong>{{ selectedChunk?.source_type || "text" }}</strong></div>
              <div><span>OCR 置信度</span><strong>{{ selectedChunk?.confidence && selectedChunk.confidence > 0 ? selectedChunk.confidence.toFixed(2) : "原生文本" }}</strong></div>
              <div><span>图片区域</span><strong>{{ selectedChunk?.bbox ? "已记录" : "无" }}</strong></div>
            </div>
            <details v-if="selectedChunk?.metadata" class="metadata-details"><summary><BracketsCurly :size="15" /> 查看 metadata</summary><pre>{{ JSON.stringify(selectedChunk.metadata, null, 2) }}</pre></details>
          </div>
        </div>

        <aside class="chunks-aside">
          <div class="aside-callout green-callout">
            <h3>好切片让检索更像复习</h3>
            <p>标题、定义、公式和例题尽量保持在同一个知识块里。发现边界不合理时，可以回到解析策略继续调整。</p>
          </div>
          <div class="aside-facts">
            <div><span>当前选中</span><strong>{{ selectedIndex + 1 }} / {{ chunks.length || 0 }}</strong></div>
            <div><span>资料主题</span><strong>{{ store.documents.find((item) => item.document_id === selectedDocument)?.topic_label || "未选择" }}</strong></div>
          </div>
        </aside>
      </div>
    </template>
  </section>
</template>
