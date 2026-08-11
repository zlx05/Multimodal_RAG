<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { Stack } from "@/components/icons";
import { useAuthStore } from "@/stores/auth";
import { useChatStore } from "@/stores/chat";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const chatStore = useChatStore();

// 第一步：用户名+密码登录。
const username = ref("");
const password = ref("");

// 第二步：账号没设过密码 → 引导式补设。
const step = ref<"login" | "setup">("login");
const setupToken = ref("");
const setupName = ref("");
const newPassword = ref("");
const confirmPassword = ref("");

const hint = ref("");

function enter() {
  chatStore.clearPersistedSession(); // 每次登录都是全新会话；历史只能主动从列表点选进入
  const redirect = typeof route.query.redirect === "string" ? route.query.redirect : "/home";
  void router.replace(redirect);
}

async function submit() {
  hint.value = "";
  try {
    const result = await auth.login(username.value, password.value);
    if (result.needsSetup) {
      // 首次登录：切到「设置密码」第二步，用 scope=setup 短效 token 补设。
      step.value = "setup";
      setupToken.value = result.setupToken;
      setupName.value = result.user.username;
      return;
    }
    enter();
  } catch (err) {
    hint.value = err instanceof Error ? err.message : "登录失败";
  }
}

async function setup() {
  hint.value = "";
  if (newPassword.value.length < 6) {
    hint.value = "密码至少 6 位";
    return;
  }
  if (newPassword.value !== confirmPassword.value) {
    hint.value = "两次输入的密码不一致";
    return;
  }
  try {
    await auth.setupPassword(setupToken.value, newPassword.value);
    enter();
  } catch (err) {
    hint.value = err instanceof Error ? err.message : "设置密码失败";
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand">
        <div class="brand-mark"><Stack :size="24" weight="bold" /></div>
        <strong>Context Lab</strong>
        <span>班级学习库</span>
      </div>

      <!-- 第一步：用户名+密码登录 -->
      <template v-if="step === 'login'">
        <h1>登录</h1>
        <p class="login-copy">输入老师给你的用户名和密码。<br />首次登录的账号会引导你设置密码。</p>

        <form class="login-form" @submit.prevent="submit">
          <label class="field">
            <span>用户名</span>
            <input
              v-model.trim="username"
              type="text"
              placeholder="例如 小明 / 老师"
              autocomplete="username"
              autofocus
              data-1p-ignore
            />
          </label>
          <label class="field">
            <span>密码</span>
            <input
              v-model="password"
              type="password"
              placeholder="密码"
              autocomplete="current-password"
              data-1p-ignore
            />
          </label>
          <button class="primary-button login-submit" type="submit" :disabled="auth.checking || !username || !password">
            {{ auth.checking ? "校验中…" : "进入知识库" }}
          </button>
        </form>
      </template>

      <!-- 第二步：引导式补设密码 -->
      <template v-else>
        <h1>设置密码</h1>
        <p class="login-copy">
          账号 <strong>{{ setupName }}</strong> 还没有密码，<br />请设置一个初始密码完成登录。
        </p>

        <form class="login-form" @submit.prevent="setup">
          <label class="field">
            <span>新密码</span>
            <input
              v-model="newPassword"
              type="password"
              placeholder="至少 6 位"
              autocomplete="new-password"
              autofocus
              data-1p-ignore
            />
          </label>
          <label class="field">
            <span>确认密码</span>
            <input
              v-model="confirmPassword"
              type="password"
              placeholder="再输一遍"
              autocomplete="new-password"
              data-1p-ignore
            />
          </label>
          <button class="primary-button login-submit" type="submit" :disabled="auth.checking || newPassword.length < 6 || !confirmPassword">
            {{ auth.checking ? "设置中…" : "设置密码并进入" }}
          </button>
        </form>
      </template>

      <p v-if="hint" class="login-hint" :class="{ error: auth.error }" role="alert">{{ hint }}</p>
      <p v-if="auth.error" class="login-hint error" role="alert">{{ auth.error }}</p>
    </div>
  </div>
</template>
