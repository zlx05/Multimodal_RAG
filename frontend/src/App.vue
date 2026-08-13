<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterView, useRoute, useRouter } from "vue-router";

import AppSidebar from "@/components/AppSidebar.vue";
import { useAuthStore } from "@/stores/auth";
import { useWorkbenchStore } from "@/stores/workbench";

const route = useRoute();
const router = useRouter();
const store = useWorkbenchStore();
const auth = useAuthStore();
const dark = ref(false);
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
  if ("scrollRestoration" in window.history) window.history.scrollRestoration = "manual";
  window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  const saved = localStorage.getItem("context-lab-theme");
  applyTheme(saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches);
  auth.bindUnauthorized(() => {
    if (router.currentRoute.value.path !== "/login") {
      void router.replace({ path: "/login", query: { redirect: router.currentRoute.value.fullPath } });
    }
  });
  void store.bootstrap();
});

watch(
  () => auth.identity,
  (identity) => {
    if (identity) void store.bootstrap();
  },
);

watch(() => route.path, () => window.scrollTo({ top: 0, left: 0, behavior: "auto" }));
</script>

<template>
  <a v-if="isShellPage" class="skip-link" href="#main-content">跳到主要内容</a>
  <div v-if="isShellPage" class="app-shell app-shell-platform">
    <AppSidebar :service="store.service" :dark="dark" @toggle-theme="toggleTheme" />
    <div v-if="store.error" class="connection-banner" role="alert">{{ store.error }}</div>
    <main id="main-content" class="page-content" tabindex="-1">
      <RouterView />
    </main>
  </div>
  <RouterView v-else />
</template>
