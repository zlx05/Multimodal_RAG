<script setup lang="ts">
import { onMounted, ref } from "vue";

import { CheckCircle, LockKey, Sparkle, UserCircle } from "@/components/icons";
import { api } from "@/api/client";
import type { AnswerStyle, MemoryRecord, UserProfile } from "@/api/types";
import { useAuthStore } from "@/stores/auth";
import MemoryList from "@/components/MemoryList.vue";
import StudentProfiles from "@/components/StudentProfiles.vue";

const auth = useAuthStore();

// 我的画像（表单）
const profile = ref<UserProfile | null>(null);
const subjectsText = ref("");
const weakPointsText = ref("");
const preferredStyle = ref<AnswerStyle>("guiding");
const loading = ref(true);
const saving = ref(false);
const saved = ref(false);
const error = ref("");

// 我的长期记忆：画像进化自动写入的持久观察（行为/薄弱点/风格倾向）。
const memoryList = ref<MemoryRecord[]>([]);
const memoryLoading = ref(false);
const memoryError = ref("");

// 修改密码（Phase 1.1 真实鉴权）。
const oldPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const passwordSaving = ref(false);
const passwordError = ref("");
const passwordSaved = ref(false);

// 页签（仅老师可见本页）：我的画像 / 学生画像。
const activeTab = ref<"mine" | "students">("mine");
const tabs = [
  { value: "mine", label: "我的画像" },
  { value: "students", label: "学生画像" },
] as const;

/** 存量旧值映射到新三档，保证加载不丢选择。 */
const LEGACY_STYLE_MAP: Record<string, AnswerStyle> = {
  beginner: "guiding",
  standard: "guiding",
  advanced: "direct",
  direct: "direct",
  guiding: "guiding",
  socratic: "socratic",
};

const styleOptions = [
  { value: "direct", label: "直接给答案", hint: "结论先行、简明扼要，要点一次讲透" },
  { value: "guiding", label: "先给思路", hint: "先讲思路和关键提示，再展开细节" },
  { value: "socratic", label: "循循善诱", hint: "通过递进提问，引导你自己推出答案" },
] as const;

onMounted(() => {
  void load();
  void loadMemory();
});

async function loadMemory() {
  memoryLoading.value = true;
  memoryError.value = "";
  try {
    const result = await api.memory();
    memoryList.value = result.memory;
  } catch (err) {
    memoryError.value = err instanceof Error ? err.message : "加载长期记忆失败";
  } finally {
    memoryLoading.value = false;
  }
}

async function deleteMemory(id: string) {
  if (!window.confirm("删除这条长期记忆？")) return;
  try {
    await api.deleteMemory(id);
    memoryList.value = memoryList.value.filter((item) => item.id !== id);
  } catch (err) {
    memoryError.value = err instanceof Error ? err.message : "删除记忆失败";
  }
}

async function changePassword() {
  passwordError.value = "";
  passwordSaved.value = false;
  if (newPassword.value.length < 6) {
    passwordError.value = "新密码至少 6 位";
    return;
  }
  if (newPassword.value !== confirmPassword.value) {
    passwordError.value = "两次输入的密码不一致";
    return;
  }
  passwordSaving.value = true;
  try {
    await auth.changePassword(oldPassword.value, newPassword.value);
    passwordSaved.value = true;
    oldPassword.value = "";
    newPassword.value = "";
    confirmPassword.value = "";
    window.setTimeout(() => (passwordSaved.value = false), 2500);
  } catch (err) {
    passwordError.value = err instanceof Error ? err.message : "修改密码失败";
  } finally {
    passwordSaving.value = false;
  }
}

async function load() {
  loading.value = true;
  try {
    profile.value = await api.profile();
    subjectsText.value = profile.value.subjects.join("、");
    weakPointsText.value = profile.value.weak_points.join("、");
    preferredStyle.value = LEGACY_STYLE_MAP[profile.value.preferred_style] ?? "guiding";
  } catch (err) {
    error.value = err instanceof Error ? err.message : "加载画像失败";
  } finally {
    loading.value = false;
  }
}

function splitList(text: string): string[] {
  return text
    .split(/[、,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function save() {
  saving.value = true;
  saved.value = false;
  error.value = "";
  try {
    profile.value = await api.updateProfile({
      subjects: splitList(subjectsText.value),
      weak_points: splitList(weakPointsText.value),
      preferred_style: preferredStyle.value,
    });
    saved.value = true;
    window.setTimeout(() => (saved.value = false), 2500);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "保存失败";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section class="new-profile-layout">
    <div class="new-profile-header">
      <div class="profile-header-left">
        <h2>让回答<em>贴合你的学习情况</em></h2>
        <p>这里是最初填写的科目、薄弱点与回答风格。之后每次长回答，都会根据你的提问习惯自动微调画像。</p>
      </div>
      <div class="profile-user-chip">
        <UserCircle :size="20" />
        <span>{{ auth.identity?.username }} · {{ auth.roleLabel }}</span>
      </div>
    </div>

    <div v-if="auth.isAdmin" class="new-profile-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.value"
        type="button"
        class="profile-tab"
        :class="{ active: activeTab === tab.value }"
        @click="activeTab = tab.value"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- 我的画像 -->
    <div v-if="!auth.isAdmin || activeTab === 'mine'" class="new-profile-content">
      <div v-if="loading" class="profile-loading">加载中...</div>
      <form v-else class="new-profile-form" @submit.prevent="save">
        <div class="profile-grid">
          <div class="profile-card">
            <div class="profile-card-header">
              <h3>学习科目</h3>
            </div>
            <div class="profile-card-body">
              <p class="card-hint">用顿号或逗号分隔，例如「数学、物理、英语」</p>
              <input v-model="subjectsText" type="text" class="new-text-input" placeholder="数学、物理、英语" />
            </div>
          </div>

          <div class="profile-card">
            <div class="profile-card-header">
              <h3>薄弱点</h3>
            </div>
            <div class="profile-card-body">
              <p class="card-hint">问答会优先讲解这些内容</p>
              <input v-model="weakPointsText" type="text" class="new-text-input" placeholder="导数、电磁感应" />
            </div>
          </div>

          <div class="profile-card full-width">
            <div class="profile-card-header">
              <h3>回答风格</h3>
              <Sparkle :size="20" style="color: var(--accent);" />
            </div>
            <div class="profile-card-body">
              <p class="card-hint">选择回答的展开方式，长回答会根据你的画像自动微调</p>
              <div class="style-options-grid">
                <label v-for="option in styleOptions" :key="option.value" class="style-option-card" :class="{ selected: preferredStyle === option.value }">
                  <input v-model="preferredStyle" type="radio" name="preferred-style" :value="option.value" class="visually-hidden" />
                  <div class="style-option-content">
                    <strong>{{ option.label }}</strong>
                    <span>{{ option.hint }}</span>
                  </div>
                  <div class="style-option-check">
                    <CheckCircle v-if="preferredStyle === option.value" :size="20" />
                  </div>
                </label>
              </div>
            </div>
          </div>
        </div>

        <div class="profile-actions">
          <p v-if="error" class="form-error" role="alert">{{ error }}</p>
          <p v-else-if="saved" class="form-success" role="status"><CheckCircle :size="15" /> 已保存</p>
          <button class="new-save-btn" type="submit" :disabled="saving">
            {{ saving ? "保存中…" : "保存画像" }}
          </button>
        </div>
      </form>

      <!-- 长期记忆 -->
      <div class="profile-card full-width">
        <div class="profile-card-header">
          <h3>长期记忆</h3>
          <Sparkle :size="20" style="color: var(--accent);" />
        </div>
        <div class="profile-card-body">
          <p class="card-hint">来自画像进化的持久观察——每次长回答后自动记录学习行为、薄弱点与风格倾向，问答时会自动用于调整回答形式。</p>
          <MemoryList
            :items="memoryList"
            :loading="memoryLoading"
            :error="memoryError"
            empty-hint="还没有长期记忆，多问几次长回答后会自动积累。"
            :deletable="true"
            @delete="deleteMemory"
          />
        </div>
      </div>

      <!-- 修改密码 -->
      <div class="profile-card full-width">
        <div class="profile-card-header">
          <h3>修改密码</h3>
          <LockKey :size="20" style="color: var(--accent);" />
        </div>
        <div class="profile-card-body">
          <form class="password-form" @submit.prevent="changePassword">
            <label class="field">
              <span>原密码</span>
              <input v-model="oldPassword" type="password" placeholder="原密码" autocomplete="current-password" data-1p-ignore />
            </label>
            <label class="field">
              <span>新密码</span>
              <input v-model="newPassword" type="password" placeholder="至少 6 位" autocomplete="new-password" data-1p-ignore />
            </label>
            <label class="field">
              <span>确认新密码</span>
              <input v-model="confirmPassword" type="password" placeholder="再输一遍" autocomplete="new-password" data-1p-ignore />
            </label>
            <div class="profile-actions">
              <p v-if="passwordError" class="form-error" role="alert">{{ passwordError }}</p>
              <p v-else-if="passwordSaved" class="form-success" role="status"><CheckCircle :size="15" /> 密码已更新</p>
              <button class="new-save-btn" type="submit" :disabled="passwordSaving || !oldPassword || newPassword.length < 6 || !confirmPassword">
                {{ passwordSaving ? "提交中…" : "更新密码" }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <!-- 学生画像（老师视角） -->
    <StudentProfiles v-if="auth.isAdmin && activeTab === 'students'" />
  </section>
</template>
