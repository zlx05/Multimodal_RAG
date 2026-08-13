import { createRouter, createWebHistory } from "vue-router";

import HomeView from "@/views/HomeView.vue";
import IngestView from "@/views/IngestView.vue";
import ChatView from "@/views/ChatView.vue";
import ChunksView from "@/views/ChunksView.vue";
import LoginView from "@/views/LoginView.vue";
import ProfileView from "@/views/ProfileView.vue";
import AdminView from "@/views/AdminView.vue";
import OnboardingView from "@/views/OnboardingView.vue";
import { useAuthStore } from "@/stores/auth";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", component: LoginView, meta: { public: true } },
    // 首次调查报告：登录后单独一屏（standalone 表示不套 app 壳），提交后不再进入
    { path: "/onboarding", component: OnboardingView, meta: { standalone: true } },
    { path: "/", redirect: "/home" },
    { path: "/home", component: HomeView },
    { path: "/ingest", component: IngestView },
    { path: "/chat", component: ChatView },
    { path: "/chunks", component: ChunksView, meta: { requiresAdmin: true } },
    { path: "/profile", component: ProfileView },
    { path: "/admin", component: AdminView, meta: { requiresAdmin: true } },
  ],
});

// 未登录一律先恢复本地身份；恢复不了就跳登录页。
// 学生首次登录必须先完成调查报告（needs_onboarding），否则进不了其他页面。
router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (to.meta.public) {
    if (to.path === "/login" && auth.identity) return "/home";
    return true;
  }
  if (!auth.identity) {
    const restored = await auth.restore();
    if (!restored) return { path: "/login", query: { redirect: to.fullPath } };
  }
  // 切片检查/画像/班级管理仅老师（admin/head）可见；学生直接输入路径也拦回首页。
  if (
    to.meta.requiresAdmin &&
    auth.identity?.role !== "admin" &&
    auth.identity?.role !== "head"
  ) {
    return "/home";
  }
  if (auth.identity?.role === "member") {
    if (auth.needsOnboarding === null) await auth.loadOnboarding();
    if (auth.needsOnboarding && to.path !== "/onboarding") return "/onboarding";
  }
  // /onboarding 只给未调查的学生；非学生或已调查一律回首页。
  if (to.path === "/onboarding") {
    if (auth.identity?.role !== "member") return "/home";
    if (auth.needsOnboarding === null) await auth.loadOnboarding();
    if (!auth.needsOnboarding) return "/home";
  }
  return true;
});

export default router;
