import { defineStore } from "pinia";
import { api } from "@/api/client";
import type { ChunkProfileOption, DocumentRecord, ModelOption, TaskRecord } from "@/api/types";

interface ActiveTask extends TaskRecord {
  polling?: boolean;
}

export const useWorkbenchStore = defineStore("workbench", {
  state: () => ({
    documents: [] as DocumentRecord[],
    models: [] as ModelOption[],
    defaultModel: "gpt-5.6-terra",
    chunkProfiles: [] as ChunkProfileOption[],
    activeTasks: [] as ActiveTask[],
    service: "checking" as "checking" | "online" | "offline",
    error: "",
  }),
  getters: {
    readyDocuments: (state) => state.documents.filter((item) => item.source_type !== ""),
    activeTaskCount: (state) => state.activeTasks.filter((task) => !["SUCCEEDED", "FAILED", "REJECTED"].includes(task.status)).length,
    indexedTaskCount: (state) => state.activeTasks.filter((task) => task.status === "SUCCEEDED").length,
  },
  actions: {
    async bootstrap() {
      this.error = "";
      const results = await Promise.allSettled([api.health(), api.documents(), api.models(), api.chunkProfiles()]);
      const health = results[0];
      const docs = results[1];
      const models = results[2];
      const profiles = results[3];
      this.service = health.status === "fulfilled" && health.value.status === "ok" ? "online" : "offline";
      if (docs.status === "fulfilled") this.documents = docs.value.documents;
      if (models.status === "fulfilled") {
        this.models = models.value.models;
        this.defaultModel = models.value.default_model;
      }
      if (profiles.status === "fulfilled") this.chunkProfiles = profiles.value.profiles;
      if (this.service === "offline") this.error = "无法连接 FastAPI，请先启动后端服务";
    },
    async upload(file: File, chunkProfile = "auto") {
      this.error = "";
      const created = await api.upload(file, chunkProfile);
      const task = await api.task(created.task_id);
      this.activeTasks.unshift(task);
      void this.pollTask(created.task_id);
    },
    async uploadUrl(url: string, chunkProfile = "auto") {
      this.error = "";
      const created = await api.uploadUrl(url, chunkProfile);
      const task = await api.task(created.task_id);
      this.activeTasks.unshift(task);
      void this.pollTask(created.task_id);
    },
    async pollTask(taskId: string) {
      const current = this.activeTasks.find((task) => task.task_id === taskId);
      if (current) current.polling = true;
      for (;;) {
        const latest = await api.task(taskId);
        const index = this.activeTasks.findIndex((task) => task.task_id === taskId);
        if (index >= 0) this.activeTasks[index] = { ...latest, polling: true };
        if (["SUCCEEDED", "FAILED", "REJECTED"].includes(latest.status)) {
          if (latest.status === "SUCCEEDED") await this.refreshDocuments();
          break;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 1200));
      }
    },
    async retry(taskId: string) {
      await api.retryTask(taskId);
      void this.pollTask(taskId);
    },
    async refreshDocuments() {
      const result = await api.documents();
      this.documents = result.documents;
    },
    /** 删除资料（失败残留清理 / 整份移除）。 */
    async deleteDocument(documentId: string) {
      await api.deleteDocument(documentId);
      await this.refreshDocuments();
    },
    /** 从服务器重新拉取全部任务状态（删除失败残留后刷新任务卡片）。 */
    async refreshActiveTasks() {
      try {
        const tasks = await api.tasks();
        this.activeTasks = tasks.tasks.map((task) => ({ ...task, polling: false }));
      } catch {
        // 拉取失败保持现状，避免清空任务列表
      }
    },
  },
});
