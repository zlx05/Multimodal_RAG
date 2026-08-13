<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import {
  Books,
  ChatsCircle,
  CirclesFour,
  FileMagnifyingGlass,
  GearSix,
  Moon,
  SignOut,
  Stack,
  Sun,
  User,
  UserCircle,
  WarningCircle,
  WifiHigh,
} from "@/components/icons";
import { useAuthStore } from "@/stores/auth";
import { useChatStore } from "@/stores/chat";

const props = defineProps<{
  service: "checking" | "online" | "offline";
  dark: boolean;
}>();

const emit = defineEmits<{ toggleTheme: [] }>();
const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const chatStore = useChatStore();

const navItems = computed(() => {
  const items = [
    { to: "/home", label: "首页", icon: CirclesFour },
    { to: "/ingest", label: "资料", icon: Books },
    { to: "/chat", label: "问答", icon: ChatsCircle },
  ];
  if (auth.isAdmin) {
    items.push({ to: "/chunks", label: "切片检查", icon: FileMagnifyingGlass });
    items.push({ to: "/profile", label: "学习画像", icon: UserCircle });
    items.push({ to: "/admin", label: "班级管理", icon: GearSix });
  }
  return items;
});

const activePath = computed(() => route.path);
const roleLabel = computed(() => auth.roleLabel);
const serviceLabel = computed(() => props.service === "online" ? "服务在线" : props.service === "offline" ? "服务离线" : "检查连接");

function logout() {
  chatStore.clearPersistedSession();
  auth.logout();
  void router.replace("/login");
}
</script>

<template>
  <header class="sidebar platform-nav" aria-label="主导航">
    <RouterLink class="brand-lockup" to="/home">
      <span class="brand-mark"><Stack :size="20" weight="bold" /></span>
      <span>
        <strong>知识库问答平台</strong>
        <small>Context Lab</small>
      </span>
    </RouterLink>

    <nav class="nav-list">
      <RouterLink
        v-for="item in navItems"
        :key="item.to"
        :to="item.to"
        class="nav-item"
        :class="{ active: activePath === item.to }"
      >
        <component :is="item.icon" :size="18" weight="regular" />
        <span>{{ item.label }}</span>
      </RouterLink>
    </nav>

    <div class="platform-actions">
      <span class="service-state" :class="`is-${service}`" :title="serviceLabel">
        <component :is="service === 'offline' ? WarningCircle : WifiHigh" :size="15" />
        <span>{{ serviceLabel }}</span>
      </span>
      <RouterLink to="/profile" class="profile-link" :title="`${auth.identity?.username ?? ''} · ${roleLabel}`">
        <User :size="18" />
        <span>{{ auth.identity?.username }}</span>
      </RouterLink>
      <button class="icon-button nav-icon-button" type="button" :title="dark ? '切换浅色模式' : '切换深色模式'" :aria-label="dark ? '切换浅色模式' : '切换深色模式'" @click="emit('toggleTheme')">
        <Sun v-if="dark" :size="18" />
        <Moon v-else :size="18" />
      </button>
      <button class="icon-button nav-icon-button" type="button" title="退出登录" aria-label="退出登录" @click="logout"><SignOut :size="18" /></button>
    </div>
  </header>
</template>
