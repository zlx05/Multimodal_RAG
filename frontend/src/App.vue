<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterView, useRoute, useRouter } from "vue-router";

import AppHeader from "@/components/AppHeader.vue";
import AppSidebar from "@/components/AppSidebar.vue";
import { useAuthStore } from "@/stores/auth";
import { useWorkbenchStore } from "@/stores/workbench";

const route = useRoute();
const router = useRouter();
const store = useWorkbenchStore();
const auth = useAuthStore();
const dark = ref(false);

const pageMeta: Record<string, { title: string }> = {
  "/home": { title: "学习资料总览" },
  "/ingest": { title: "资料入库" },
  "/chat": { title: "知识问答" },
  "/chunks": { title: "切片检查" },
  "/profile": { title: "我的画像" },
  "/admin": { title: "班级管理" },
};

const meta = computed(() => pageMeta[route.path] ?? pageMeta["/home"]);
// 登录页、首次调查报告独立全屏渲染，不套应用外壳。
const isShellPage = computed(() => route.meta.public !== true && route.meta.standalone !== true);

function applyTheme(value: boolean) {
  dark.value = value;
  document.documentElement.dataset.theme = value ? "dark" : "light";
  localStorage.setItem("context-lab-theme", value ? "dark" : "light");
}

function toggleTheme() {
  applyTheme(!dark.value);
}

onMounted(() => {
  const saved = localStorage.getItem("context-lab-theme");
  applyTheme(saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches);
  // 全局 401（token 失效/用户被删）→ 清登录态并跳登录页（带 redirect 回跳）。
  // 不监听 /login 本身，避免登录时的 401（密码错）触发跳转循环。
  auth.bindUnauthorized(() => {
    if (router.currentRoute.value.path !== "/login") {
      void router.replace({ path: "/login", query: { redirect: router.currentRoute.value.fullPath } });
    }
  });
  void store.bootstrap();
});

// 登录态变化（登录/引导设密/登出）后刷新工作台数据。初始加载若落在登录页，
// bootstrap 会在无 token 下跑过（documents 401 为空），登录成功必须重拉，
// 否则首页会一直显示"知识库还没有资料"。已登录刷新时 identity 在路由守卫里
// 先恢复好，watch 只在变化时触发，不会造成重复请求。
watch(
  () => auth.identity,
  (identity) => {
    if (identity) void store.bootstrap();
  },
);

watch(() => route.path, () => window.scrollTo({ top: 0, behavior: "smooth" }));
</script>

<template>
  <div v-if="isShellPage" class="app-shell">
    <AppSidebar />
    <div class="app-body">
      <AppHeader :title="meta.title" :service="store.service" :dark="dark" @toggle-theme="toggleTheme" />
      <div v-if="store.error" class="connection-banner" role="alert">{{ store.error }}</div>
      <main class="page-content">
        <RouterView />
      </main>
    </div>
  </div>
  <RouterView v-else />
</template>
