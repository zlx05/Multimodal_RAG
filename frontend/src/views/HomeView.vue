<script setup lang="ts">
import { computed } from "vue";
import { ArrowUpRight, Books, CheckCircle, Cube, Queue, TrendUp } from "@/components/icons";
import { useWorkbenchStore } from "@/stores/workbench";

const store = useWorkbenchStore();
const formatBytes = (value: number) => {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
};

const indexedDocuments = computed(() => store.activeTasks.filter((task) => task.status === "SUCCEEDED").length);
const latestTasks = computed(() => store.activeTasks.slice(0, 4));
const defaultModelLabel = computed(() => store.models.find((model) => model.default)?.label.replace("GPT-5.6 ", "") ?? "Luna");
</script>

<template>
  <section class="workspace-intro fade-up">
    <div>
      <h2>把每一份资料<em>变成能复习的上下文。</em></h2>
      <p class="intro-copy">教材、错题截图、手写笔记，统一解析成可以检索、核对和追溯的知识块。</p>
    </div>
    <RouterLink class="primary-button" to="/ingest"><Books :size="18" weight="bold" /> 添加资料 <ArrowUpRight :size="17" /></RouterLink>
  </section>

  <section class="metric-grid fade-up delay-1" aria-label="知识库概览">
    <article class="metric-card metric-primary">
      <div class="metric-icon"><Books :size="19" /></div>
      <span>资料总数</span>
      <strong>{{ store.documents.length }}</strong>
      <small>已保存到本地资料区</small>
    </article>
    <article class="metric-card">
      <div class="metric-icon"><Cube :size="19" /></div>
      <span>本次已入库</span>
      <strong>{{ indexedDocuments }}</strong>
      <small>当前会话完成的任务</small>
    </article>
    <article class="metric-card">
      <div class="metric-icon"><Queue :size="19" /></div>
      <span>解析队列</span>
      <strong>{{ store.activeTaskCount }}</strong>
      <small>Redis 后台任务</small>
    </article>
    <article class="metric-card">
      <div class="metric-icon"><TrendUp :size="19" /></div>
      <span>回答模型</span>
      <strong class="model-metric">{{ defaultModelLabel }}</strong>
      <small>服务端安全切换</small>
    </article>
  </section>

  <section class="content-grid home-grid fade-up delay-2">
    <div class="panel document-panel">
      <div class="panel-heading">
        <div>
          <h3>最近资料</h3>
        </div>
        <RouterLink class="quiet-link" to="/ingest">管理入库 <ArrowUpRight :size="15" /></RouterLink>
      </div>
      <div v-if="store.documents.length" class="document-list">
        <div v-for="document in store.documents.slice(0, 6)" :key="document.document_id" class="document-row">
          <div class="file-type">{{ document.source_type.toUpperCase() }}</div>
          <div class="document-name">
            <strong>{{ document.topic_label || document.filename }}</strong>
            <span>{{ document.filename }} · {{ document.document_id }}</span>
          </div>
          <span class="document-size">{{ formatBytes(document.size) }}</span>
          <RouterLink class="row-action" :to="`/chunks?document=${document.document_id}`" aria-label="查看切片"><ArrowUpRight :size="16" /></RouterLink>
        </div>
      </div>
      <div v-else class="empty-state compact">
        <Books :size="24" />
        <strong>知识库还没有资料</strong>
        <span>先上传一份教材或错题截图，开始建立自己的复习上下文。</span>
        <RouterLink class="text-button" to="/ingest">去添加资料 <ArrowUpRight :size="14" /></RouterLink>
      </div>
    </div>

    <div class="panel queue-panel">
      <div class="panel-heading">
        <div>
          <h3>最近任务</h3>
        </div>
        <CheckCircle :size="21" class="heading-mark" />
      </div>
      <div v-if="latestTasks.length" class="task-mini-list">
        <div v-for="task in latestTasks" :key="task.task_id" class="task-mini-row">
          <div class="task-mini-status" :class="`status-${task.status.toLowerCase()}`"></div>
          <div>
            <strong>{{ task.filename }}</strong>
            <span>{{ task.stage || "等待处理" }}</span>
          </div>
          <span class="task-progress">{{ task.progress }}%</span>
        </div>
      </div>
      <div v-else class="empty-state compact muted">
        <Queue :size="24" />
        <strong>解析队列为空</strong>
        <span>任务状态会在上传后持续更新。</span>
      </div>
    </div>
  </section>
</template>
