<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";
import { Moon, SignOut, Sun, UserCircle, WifiHigh, WarningCircle } from "@/components/icons";
import { useAuthStore } from "@/stores/auth";
import { useChatStore } from "@/stores/chat";

const props = defineProps<{
  title: string;
  service: "checking" | "online" | "offline";
  dark: boolean;
}>();

const emit = defineEmits<{ toggleTheme: [] }>();
const router = useRouter();
const auth = useAuthStore();
const chatStore = useChatStore();

const serviceLabel = computed(() => {
  if (props.service === "online") return "API 在线";
  if (props.service === "offline") return "API 离线";
  return "检查连接";
});
const roleLabel = computed(() => auth.roleLabel);

function logout() {
  chatStore.clearPersistedSession(); // 先清本地会话引用（此时 user_id 还在，能算对 key）
  auth.logout();
  void router.replace("/login");
}
</script>

<template>
  <header class="topbar">
    <div>
      <p class="breadcrumb">班级学习库</p>
      <h1>{{ title }}</h1>
    </div>
    <div class="topbar-actions">
      <div class="service-state" :class="`is-${service}`">
        <component :is="service === 'offline' ? WarningCircle : WifiHigh" :size="16" />
        <span>{{ serviceLabel }}</span>
      </div>
      <div v-if="auth.identity" class="identity-pill" :title="`user_id: ${auth.userId}`">
        <UserCircle :size="17" />
        <span>{{ auth.identity.username }} · {{ roleLabel }}</span>
      </div>
      <button
        v-if="auth.identity"
        class="icon-button"
        type="button"
        aria-label="退出登录"
        title="退出登录"
        @click="logout"
      >
        <SignOut :size="18" />
      </button>
      <button class="icon-button" type="button" :aria-label="dark ? '切换浅色模式' : '切换深色模式'" @click="emit('toggleTheme')">
        <Sun v-if="dark" :size="18" />
        <Moon v-else :size="18" />
      </button>
    </div>
  </header>
</template>
