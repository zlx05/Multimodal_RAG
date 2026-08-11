<script setup lang="ts">
import { Trash } from "@/components/icons";
import type { MemoryRecord } from "@/api/types";

/** 长期记忆列表（我的画像与学生画像共用）。
 * 只渲染列表本身；标题/说明由父组件控制。
 */
defineProps<{
  items: MemoryRecord[];
  loading: boolean;
  error: string;
  emptyHint?: string;
  /** 是否渲染删除按钮（学生画像只读，不传/传 false）。 */
  deletable?: boolean;
}>();

const emit = defineEmits<{ delete: [id: string] }>();

function formatTime(ts: number): string {
  const seconds = Math.floor(Date.now() / 1000 - ts);
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  if (seconds < 172800) return "昨天";
  return new Date(ts * 1000).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}
</script>

<template>
  <div v-if="loading" class="empty-state compact"><span>加载中…</span></div>
  <div v-else-if="error && !items.length" class="empty-state compact"><span>{{ error }}</span></div>
  <div v-else-if="!items.length" class="empty-state compact">
    <span>{{ emptyHint ?? "还没有长期记忆。" }}</span>
  </div>
  <ul v-else class="memory-list">
    <li v-for="item in items" :key="item.id" class="memory-item">
      <div class="memory-item-main">
        <span class="memory-type">{{ item.memory_type }}</span>
        <p class="memory-content">{{ item.content }}</p>
        <p v-if="item.source_question" class="memory-source">来自提问：{{ item.source_question }}</p>
      </div>
      <div class="memory-item-side">
        <span class="memory-time">{{ formatTime(item.created_at) }}</span>
        <span v-if="item.confidence" class="memory-confidence">{{ Math.round(item.confidence * 100) }}%</span>
        <button v-if="deletable" class="memory-delete" type="button" title="删除" @click="emit('delete', item.id)">
          <Trash :size="14" />
        </button>
      </div>
    </li>
  </ul>
</template>
