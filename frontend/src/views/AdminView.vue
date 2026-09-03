<script setup lang="ts">
import { onMounted, ref } from "vue";

import { Check, LockKey, Trash, User, UserPlus } from "@/components/icons";
import { api } from "@/api/client";
import type { UploadAudit, UploadStatus, UserRole, UserWithRole } from "@/api/types";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();

const username = ref("");
const password = ref("");
const creating = ref(false);
const createdId = ref("");
const createError = ref("");

const teacherName = ref("");
const teacherPassword = ref("");
const creatingTeacher = ref(false);
const teacherCreatedId = ref("");
const teacherError = ref("");

const users = ref<UserWithRole[]>([]);
const usersLoading = ref(false);
const usersError = ref("");
const deletingId = ref("");
const confirmingId = ref("");

const uploads = ref<UploadAudit[]>([]);
const filter = ref<"all" | UploadStatus>("all");
const loading = ref(false);
const uploadError = ref("");
const busyId = ref("");

const roleMeta: Record<UserRole, { label: string; cls: string }> = {
  head: { label: "班主任", cls: "role-head" },
  admin: { label: "老师", cls: "role-admin" },
  member: { label: "学生", cls: "role-member" },
};

const statusMeta: Record<UploadStatus, { label: string; cls: string }> = {
  pending: { label: "校验中", cls: "status-pending" },
  approved: { label: "已放行", cls: "status-approved" },
  rejected: { label: "已驳回", cls: "status-rejected" },
  hidden: { label: "已隐藏", cls: "status-hidden" },
};

const filteredUploads = () => (filter.value === "all" ? uploads.value : uploads.value.filter((item) => item.status === filter.value));

/** 状态角标文案：approved 但未入库（上次放行没生效）单独提示。 */
function statusLabel(item: { status: UploadStatus; indexed?: boolean }): string {
  if (item.status === "approved" && item.indexed === false) return "已放行·待入库";
  return statusMeta[item.status].label;
}

/** 放行按钮：pending/rejected 常规显示；approved 但未入库也能再次放行补索引。 */
function canApprove(item: { status: UploadStatus; indexed?: boolean }): boolean {
  return item.status === "pending" || item.status === "rejected" || (item.status === "approved" && item.indexed === false);
}

const filterTabs: { value: "all" | UploadStatus; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "pending", label: "校验中" },
  { value: "approved", label: "已放行" },
  { value: "rejected", label: "已驳回" },
];

onMounted(() => {
  if (auth.isAdmin) {
    void loadUploads();
    void loadUsers();
  }
});

async function createStudent() {
  const name = username.value.trim();
  if (!name) return;
  creating.value = true;
  createError.value = "";
  createdId.value = "";
  try {
    const result = await api.createStudent(name, password.value || undefined);
    createdId.value = result.user.id;
    username.value = "";
    password.value = "";
    await loadUsers();
  } catch (err) {
    createError.value = err instanceof Error ? err.message : "创建失败";
  } finally {
    creating.value = false;
  }
}

async function createTeacher() {
  const name = teacherName.value.trim();
  if (!name) return;
  creatingTeacher.value = true;
  teacherError.value = "";
  teacherCreatedId.value = "";
  try {
    const result = await api.createTeacher(name, teacherPassword.value || undefined);
    teacherCreatedId.value = result.user.id;
    teacherName.value = "";
    teacherPassword.value = "";
    await loadUsers();
  } catch (err) {
    teacherError.value = err instanceof Error ? err.message : "创建失败";
  } finally {
    creatingTeacher.value = false;
  }
}

async function loadUsers() {
  usersLoading.value = true;
  usersError.value = "";
  try {
    const result = await api.listUsers();
    users.value = result.users;
  } catch (err) {
    usersError.value = err instanceof Error ? err.message : "加载成员失败";
  } finally {
    usersLoading.value = false;
  }
}

/** 班主任能删任意非本人；老师只能删学生；不能删自己。 */
function canDelete(user: UserWithRole) {
  if (user.id === auth.userId) return false;
  return auth.isHead || user.role === "member";
}

/** 二次确认：点一次进入「确认删除」状态，超时自动恢复。 */
function requestDelete(userId: string) {
  confirmingId.value = userId;
  window.setTimeout(() => {
    if (confirmingId.value === userId) confirmingId.value = "";
  }, 4000);
}

async function removeUser(userId: string) {
  deletingId.value = userId;
  try {
    await api.deleteUser(userId);
    users.value = users.value.filter((u) => u.id !== userId);
  } catch (err) {
    usersError.value = err instanceof Error ? err.message : "删除失败";
  } finally {
    deletingId.value = "";
    confirmingId.value = "";
  }
}

async function loadUploads() {
  loading.value = true;
  uploadError.value = "";
  try {
    const result = await api.adminUploads();
    uploads.value = result.uploads;
  } catch (err) {
    uploadError.value = err instanceof Error ? err.message : "加载审计失败";
  } finally {
    loading.value = false;
  }
}

async function act(uploadId: string, action: "approve" | "reject" | "delete") {
  busyId.value = uploadId;
  try {
    if (action === "approve") await api.approveUpload(uploadId);
    else if (action === "reject") await api.rejectUpload(uploadId);
    else await api.deleteUpload(uploadId);
    await loadUploads();
  } catch (err) {
    uploadError.value = err instanceof Error ? err.message : "操作失败";
  } finally {
    busyId.value = "";
  }
}
</script>

<template>
  <section v-if="!auth.isAdmin" class="empty-state compact">
    <LockKey :size="28" />
    <strong>仅老师可访问</strong>
    <span>班级管理与审计需要老师身份。</span>
  </section>

  <template v-else>
    <div class="new-admin-layout">
      <div class="new-admin-header">
        <h2>管理班级成员<em>与上传审计</em></h2>
        <p>创建学生账号（建号即入班），把 user_id 发给对方登录；上传的资料先自动校验，老师可以放行、驳回或删除。</p>
      </div>

      <div class="new-admin-grid">
        <!-- 左侧：创建账号 -->
        <div class="admin-left-panel">
          <div class="new-admin-card">
            <div class="admin-card-header">
              <h3>创建学生账号</h3>
              <UserPlus :size="24" style="color: var(--accent);" />
            </div>
            <p class="admin-card-hint">学生用生成的 user_id 登录；可填初始密码（留空则首次登录引导设密）。账号建立即加入默认班级。</p>
            <form class="new-admin-form" @submit.prevent="createStudent">
              <input v-model="username" type="text" class="new-admin-input" placeholder="学生姓名，例如 小明" autocomplete="off" data-1p-ignore />
              <input v-model="password" type="password" class="new-admin-input" placeholder="初始密码（可选）" autocomplete="new-password" data-1p-ignore />
              <button class="new-admin-submit" type="submit" :disabled="creating || !username.trim()">
                {{ creating ? "创建中…" : "创建账号" }}
              </button>
            </form>
            <p v-if="createdId" class="admin-success">
              创建成功，user_id: <code>{{ createdId }}</code>
            </p>
            <p v-if="createError" class="form-error">{{ createError }}</p>
          </div>

          <div v-if="auth.isHead" class="new-admin-card">
            <div class="admin-card-header">
              <h3>添加老师</h3>
              <UserPlus :size="24" style="color: var(--accent);" />
            </div>
            <p class="admin-card-hint">老师与你有同等的班级管理权限，能建学生、审计上传，但不能添加或删除老师。可填初始密码（留空则首登引导设密）。</p>
            <form class="new-admin-form" @submit.prevent="createTeacher">
              <input v-model="teacherName" type="text" class="new-admin-input" placeholder="老师姓名，例如 王老师" autocomplete="off" data-1p-ignore />
              <input v-model="teacherPassword" type="password" class="new-admin-input" placeholder="初始密码（可选）" autocomplete="new-password" data-1p-ignore />
              <button class="new-admin-submit" type="submit" :disabled="creatingTeacher || !teacherName.trim()">
                {{ creatingTeacher ? "添加中…" : "添加老师" }}
              </button>
            </form>
            <p v-if="teacherCreatedId" class="admin-success">
              添加成功，user_id: <code>{{ teacherCreatedId }}</code>
            </p>
            <p v-if="teacherError" class="form-error">{{ teacherError }}</p>
          </div>
        </div>

        <!-- 右侧：班级成员和上传审计 -->
        <div class="admin-right-panel">
          <div class="new-admin-card">
            <div class="admin-card-header">
              <h3>班级成员</h3>
              <span class="member-badge">班主任/学生</span>
            </div>

            <p v-if="usersError" class="form-error" role="alert">{{ usersError }}</p>
            <div v-if="usersLoading" class="admin-loading">加载中...</div>
            <div v-else-if="!users.length" class="admin-empty">还没有成员</div>
            <div v-else class="new-member-grid">
              <div v-for="user in users" :key="user.id" class="new-member-card">
                <div class="member-avatar">
                  <User :size="32" />
                </div>
                <div class="member-info">
                  <strong>{{ user.username }}</strong>
                  <span :title="user.id">{{ user.id }}</span>
                </div>
                <span class="role-tag" :class="roleMeta[user.role].cls">{{ roleMeta[user.role].label }}</span>
                <button
                  v-if="canDelete(user)"
                  class="member-delete-btn"
                  type="button"
                  @click="confirmingId === user.id ? removeUser(user.id) : requestDelete(user.id)"
                >
                  <Trash :size="16" />
                </button>
              </div>
            </div>
          </div>

          <div class="new-admin-card">
            <div class="admin-card-header">
              <h3>上传审计</h3>
              <div class="audit-tabs">
                <button
                  v-for="tab in filterTabs"
                  :key="tab.value"
                  type="button"
                  class="audit-tab"
                  :class="{ active: filter === tab.value }"
                  @click="filter = tab.value"
                >
                  {{ tab.label }}
                </button>
              </div>
            </div>

            <p v-if="uploadError" class="form-error" role="alert">{{ uploadError }}</p>
            <div v-if="loading" class="admin-loading">加载中...</div>
            <div v-else-if="!filteredUploads().length" class="admin-empty">没有记录</div>
            <div v-else class="new-audit-list">
              <div v-for="item in filteredUploads()" :key="item.id" class="new-audit-item">
                <div class="audit-item-header">
                  <strong>{{ item.document.topic_label || item.filename }}</strong>
                  <span class="audit-status" :class="statusMeta[item.status].cls">{{ statusLabel(item) }}</span>
                </div>
                <p v-if="item.status === 'approved' && item.indexed === false" class="audit-note">上次放行未入库，请再次放行补索引</p>
                <p v-if="item.review_note" class="audit-note">校验说明：{{ item.review_note }}</p>
                <div class="audit-item-meta">
                  <span>{{ item.uploader.username || item.uploader_user_id }} 上传</span>
                  <span>{{ item.filename }}</span>
                  <span>{{ new Date(item.created_at * 1000).toLocaleString() }}</span>
                </div>
                <div class="audit-item-actions">
                  <button v-if="canApprove(item)" class="audit-action-btn approve" type="button" @click="act(item.id, 'approve')">
                    <Check :size="14" /> 放行
                  </button>
                  <button v-if="item.status === 'pending' || item.status === 'approved'" class="audit-action-btn reject" type="button" @click="act(item.id, 'reject')">
                    驳回
                  </button>
                  <button class="audit-action-btn delete" type="button" @click="act(item.id, 'delete')">
                    <Trash :size="14" />
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </template>
</template>
