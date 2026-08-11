import { defineStore } from "pinia";
import { api, clearToken, clearUserId, getToken, saveToken, saveUserId } from "@/api/client";
import type { UserIdentity, UserRole } from "@/api/types";

/**
 * login 的结果。账号没设过密码时返回 needsSetup=true + setupToken，
 * 由调用方（LoginView）切到「设置密码」第二步再调 setupPassword。
 */
export type LoginResult =
  | { needsSetup: false; identity: UserIdentity }
  | { needsSetup: true; setupToken: string; user: UserIdentity };

/** 身份持久化与登录/登出（Phase 1.1 真实鉴权：用户名+密码 → JWT）。 */
export const useAuthStore = defineStore("auth", {
  state: () => ({
    identity: null as UserIdentity | null,
    /** 学生是否还没做过首次调查报告（仅 member 有意义；null=未加载）。 */
    needsOnboarding: null as boolean | null,
    checking: false,
    error: "",
  }),
  getters: {
    isLoggedIn: (state) => state.identity !== null,
    role: (state): UserRole | null => state.identity?.role ?? null,
    /** 老师或班主任（可建学生、审计上传）。 */
    isAdmin: (state) => state.identity?.role === "admin" || state.identity?.role === "head",
    /** 班主任（可建老师、删老师）。 */
    isHead: (state) => state.identity?.role === "head",
    /** 身份中文名：班主任 / 老师 / 学生。 */
    roleLabel: (state) => ({ head: "班主任", admin: "老师", member: "学生" })[state.identity?.role ?? "member"] ?? "学生",
    userId: (state) => state.identity?.id ?? null,
  },
  actions: {
    /** 加载「是否要做调查报告」；非学生恒 false；失败不拦截用户。 */
    async loadOnboarding(): Promise<boolean> {
      if (this.identity?.role !== "member") {
        this.needsOnboarding = false;
        return false;
      }
      try {
        const resp = await api.onboarding();
        this.needsOnboarding = resp.needs_onboarding;
        return resp.needs_onboarding;
      } catch {
        this.needsOnboarding = false;
        return false;
      }
    },
    /** 用本地已存的 token 恢复身份（刷新页面用）。无 token 或校验失败则清除。 */
    async restore(): Promise<UserIdentity | null> {
      const token = getToken();
      if (!token) return null;
      this.error = "";
      try {
        const { user } = await api.me();
        this.identity = user;
        // 同步 legacy user_id key（chat.ts 会话命名空间仍用），保持与 token 身份一致。
        saveUserId(user.id);
        await this.loadOnboarding();
        return user;
      } catch {
        // 身份已失效（token 过期/伪造/用户被删）→ 清除登录态，回到登录页。
        this.identity = null;
        this.needsOnboarding = null;
        clearToken();
        clearUserId();
        return null;
      }
    },
    /** 用户名+密码登录。账号没设过密码 → 返回 needsSetup，由调用方走引导式补设。 */
    async login(username: string, password: string): Promise<LoginResult> {
      const trimmed = username.trim();
      if (!trimmed) throw new Error("请输入用户名");
      this.checking = true;
      this.error = "";
      try {
        const resp = await api.login(trimmed, password);
        if (resp.needs_password_setup && resp.setup_token) {
          // 引导式补设：还不发正式 token，等第二步设密后再登录。
          return { needsSetup: true, setupToken: resp.setup_token, user: resp.user };
        }
        saveToken(resp.access_token ?? "");
        saveUserId(resp.user.id);
        this.identity = resp.user;
        await this.loadOnboarding();
        return { needsSetup: false, identity: resp.user };
      } catch (err) {
        clearToken();
        clearUserId();
        this.identity = null;
        this.needsOnboarding = null;
        this.error = err instanceof Error ? err.message : "登录失败";
        throw err;
      } finally {
        this.checking = false;
      }
    },
    /** 引导式补设第二步：用 scope=setup 短效 token 设置密码，成功后返回正式身份。 */
    async setupPassword(setupToken: string, password: string): Promise<UserIdentity> {
      this.checking = true;
      this.error = "";
      try {
        const resp = await api.setupPassword(setupToken, password);
        saveToken(resp.access_token ?? "");
        saveUserId(resp.user.id);
        this.identity = resp.user;
        await this.loadOnboarding();
        return resp.user;
      } catch (err) {
        this.error = err instanceof Error ? err.message : "设置密码失败";
        throw err;
      } finally {
        this.checking = false;
      }
    },
    /** 修改密码（后端验证旧密码）。 */
    async changePassword(oldPassword: string, newPassword: string): Promise<void> {
      await api.changePassword(oldPassword, newPassword);
    },
    logout() {
      this.identity = null;
      this.needsOnboarding = null;
      clearToken();
      clearUserId();
    },
    /**
     * 监听全局 401（token 失效/用户被删）：清登录态，并回调跳登录页。
     * 回调由 App.vue 注入，避免 auth store 引 router 造成循环依赖。
     */
    bindUnauthorized(onUnauthorized?: () => void): void {
      window.addEventListener("auth:unauthorized", () => {
        this.identity = null;
        this.needsOnboarding = null;
        clearToken();
        clearUserId();
        onUnauthorized?.();
      });
    },
  },
});
