<script setup lang="ts">
import { onMounted, ref } from "vue";

import { Sparkle, UserCircle } from "@/components/icons";
import { api } from "@/api/client";
import type { MemoryRecord, UserProfile, UserWithRole } from "@/api/types";
import MemoryList from "@/components/MemoryList.vue";

/** 教师端「学生画像」页签：只读查看每位学生的画像 + 长期记忆。无编辑/删除。 */

const students = ref<UserWithRole[]>([]);
const listLoading = ref(false);
const listError = ref("");

const selectedId = ref("");
const detail = ref<UserProfile | null>(null);
const detailLoading = ref(false);
const detailError = ref("");

const STYLE_LABEL: Record<string, string> = {
  direct: "直接给答案",
  guiding: "先给思路",
  socratic: "循循善诱",
  // 存量旧值兼容（Phase 2C 之前的画像）
  beginner: "初学者节奏",
  standard: "标准精炼",
  advanced: "进阶直给",
};

onMounted(() => void loadStudents());

async function loadStudents() {
  listLoading.value = true;
  listError.value = "";
  try {
    const result = await api.listUsers();
    // 只列学生；老师/班主任不进学生画像列表。
    students.value = result.users.filter((u) => u.role === "member");
  } catch (err) {
    listError.value = err instanceof Error ? err.message : "加载学生失败";
  } finally {
    listLoading.value = false;
  }
}

async function select(studentId: string) {
  selectedId.value = studentId;
  detail.value = null;
  detailLoading.value = true;
  detailError.value = "";
  try {
    const [profile, memory] = await Promise.all([
      api.profileOf(studentId),
      api.memoryOf(studentId),
    ]);
    detail.value = profile;
    memoryList.value = memory.memory;
  } catch (err) {
    detailError.value = err instanceof Error ? err.message : "加载学生详情失败";
  } finally {
    detailLoading.value = false;
  }
}

const memoryList = ref<MemoryRecord[]>([]);
</script>

<template>
  <div class="panel profile-panel">
    <div class="panel-heading">
      <div><h3>学生画像</h3></div>
      <UserCircle :size="21" class="heading-mark" />
    </div>
    <p class="memory-hint">查看每位学生的画像与长期记忆（只读，不写入）。点学生名字查看详情。</p>

    <p v-if="listError" class="form-error" role="alert">{{ listError }}</p>

    <div v-if="listLoading" class="empty-state compact"><span>加载中…</span></div>
    <div v-else-if="!students.length" class="empty-state compact muted">
      <strong>还没有学生</strong>
      <span>先在「班级管理」创建学生账号。</span>
    </div>
    <ul v-else class="student-list">
      <li v-for="s in students" :key="s.id">
        <button
          type="button"
          class="student-row"
          :class="{ active: s.id === selectedId }"
          @click="select(s.id)"
        >
          <span class="member-avatar">{{ s.username.slice(0, 1) }}</span>
          <span class="member-main">
            <strong>{{ s.username }}</strong>
            <span class="member-id">{{ s.id }}</span>
          </span>
          <span class="role-badge role-member">学生</span>
        </button>
      </li>
    </ul>

    <div v-if="selectedId" class="student-detail">
      <div v-if="detailLoading" class="empty-state compact"><span>加载中…</span></div>
      <p v-else-if="detailError" class="form-error" role="alert">{{ detailError }}</p>
      <template v-else-if="detail">
        <div class="student-profile-section">
          <span class="memory-type">学习科目</span>
          <div v-if="detail.subjects.length" class="chip-row">
            <span v-for="s in detail.subjects" :key="s" class="chip">{{ s }}</span>
          </div>
          <p v-else class="empty-inline">未填写</p>
        </div>
        <div class="student-profile-section">
          <span class="memory-type">薄弱点</span>
          <div v-if="detail.weak_points.length" class="chip-row">
            <span v-for="w in detail.weak_points" :key="w" class="chip chip-warn">{{ w }}</span>
          </div>
          <p v-else class="empty-inline">未填写</p>
        </div>
        <div class="student-profile-section">
          <span class="memory-type">回答风格</span>
          <span class="style-value">{{ STYLE_LABEL[detail.preferred_style] ?? detail.preferred_style }}</span>
        </div>

        <div class="student-memory">
          <div class="panel-heading">
            <div><h4>长期记忆</h4></div>
            <Sparkle :size="18" class="heading-mark" />
          </div>
          <MemoryList
            :items="memoryList"
            :loading="detailLoading"
            :error="detailError"
            empty-hint="该学生还没有长期记忆。"
          />
        </div>
      </template>
    </div>
  </div>
</template>
