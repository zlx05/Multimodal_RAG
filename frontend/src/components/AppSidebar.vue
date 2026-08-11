<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, useRoute } from "vue-router";
import {
  Books,
  ChatsCircle,
  CirclesFour,
  FileMagnifyingGlass,
  GearSix,
  Stack,
  UserCircle,
} from "@/components/icons";
import { useAuthStore } from "@/stores/auth";

const route = useRoute();
const auth = useAuthStore();

const navItems = computed(() => {
  // 学生只见 总览/资料入库/知识问答；切片检查、画像、班级管理仅老师可见。
  const items = [
    { to: "/home", label: "总览", hint: "工作台", icon: CirclesFour },
    { to: "/ingest", label: "资料入库", hint: "解析队列", icon: Books },
    { to: "/chat", label: "知识问答", hint: "混合检索", icon: ChatsCircle },
  ];
  if (auth.isAdmin) {
    items.push({ to: "/chunks", label: "切片检查", hint: "结构视图", icon: FileMagnifyingGlass });
    items.push({ to: "/profile", label: "我的画像", hint: "学习特征", icon: UserCircle });
    items.push({ to: "/admin", label: "班级管理", hint: "学生与审计", icon: GearSix });
  }
  return items;
});

const activePath = computed(() => route.path);
</script>

<template>
  <aside class="sidebar" aria-label="主导航">
    <div class="brand-lockup">
      <div class="brand-mark"><Stack :size="20" weight="bold" /></div>
      <div>
        <strong>Context Lab</strong>
        <span>班级学习库</span>
      </div>
    </div>

    <div class="sidebar-rule"></div>

    <nav class="nav-list">
      <RouterLink
        v-for="item in navItems"
        :key="item.to"
        :to="item.to"
        class="nav-item"
        :class="{ active: activePath === item.to }"
      >
        <component :is="item.icon" :size="19" weight="regular" />
        <span class="nav-copy">
          <strong>{{ item.label }}</strong>
          <small>{{ item.hint }}</small>
        </span>
      </RouterLink>
    </nav>

    <div class="sidebar-bottom"></div>
  </aside>
</template>
