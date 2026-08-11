<script setup lang="ts">
import { ArrowUpRight, FileText, ImageSquare, MapPinLine } from "@/components/icons";
import type { SearchSource } from "@/api/types";

defineProps<{ sources: SearchSource[] }>();

const sourceIcon = (type?: string) => (type && type.includes("image") ? ImageSquare : FileText);
const scoreLabel = (score?: number) => `${Math.round(Math.max(0, Math.min(1, score ?? 0)) * 100)}%`;
</script>

<template>
  <div class="source-list">
    <article v-for="(source, index) in sources" :key="`${source.document_id}-${index}`" class="source-item">
      <div class="source-index">0{{ index + 1 }}</div>
      <div class="source-body">
        <div class="source-meta">
          <component :is="sourceIcon(source.content_type)" :size="15" />
          <strong>{{ source.topic_label || source.filename }}</strong>
          <span v-if="source.topic_label">{{ source.filename }}</span>
          <span v-if="source.page">第 {{ source.page }} 页</span>
          <span v-if="source.heading_path">{{ source.heading_path }}</span>
        </div>
        <p>{{ source.text }}</p>
        <div v-if="source.asset_url" class="source-preview">
          <img :src="source.asset_url" :alt="`${source.topic_label || source.filename} 图片来源`" loading="lazy" />
        </div>
        <div class="source-footer">
          <span><MapPinLine :size="13" /> {{ source.origins?.join(" + ") || "混合召回" }}</span>
          <span class="source-score">匹配度 {{ scoreLabel(source.score) }}</span>
          <a v-if="source.original_url" class="text-button" :href="source.original_url" target="_blank" rel="noreferrer"><ArrowUpRight :size="14" /> 查看原文</a>
        </div>
      </div>
    </article>
  </div>
</template>
