<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";
import { ArrowRight, ArrowUpRight, Books, CheckCircle, ChatsCircle, Cube, FileMagnifyingGlass, TrendUp } from "@/components/icons";
import { useAuthStore } from "@/stores/auth";
import { useWorkbenchStore } from "@/stores/workbench";

const store = useWorkbenchStore();
const auth = useAuthStore();
const latestTasks = computed(() => store.activeTasks.slice(0, 4));
const formatBytes = (value: number) => value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`;
</script>

<template>
  <section class="home-hero fade-up">
    <div class="home-hero-copy">
      <p class="hero-kicker">个人学习知识库</p>
      <h1>从一份资料开始<em>复习</em></h1>
      <p>上传教材、错题本或笔记后，系统会自动解析、切片、检索，并生成可追问的知识上下文。</p>
      <div class="hero-actions">
        <RouterLink class="primary-button hero-upload" to="/ingest"><Books :size="18" weight="bold" /> 上传资料</RouterLink>
        <RouterLink class="text-button" to="/chat">开始问答 <ArrowRight :size="16" /></RouterLink>
      </div>
    </div>
    <div class="hero-art" aria-hidden="true">
      <div class="hero-orb"></div>
      <div class="hero-document hero-document-main"><FileMagnifyingGlass :size="54" /><span></span><span></span><span></span></div>
      <div class="hero-document hero-document-left"><Cube :size="26" /></div>
      <div class="hero-document hero-document-right"><ChatsCircle :size="27" /></div>
      <i class="hero-line hero-line-one"></i><i class="hero-line hero-line-two"></i><i class="hero-line hero-line-three"></i>
    </div>
  </section>

  <section class="feature-launches fade-up delay-1" aria-label="主要功能">
    <RouterLink class="feature-launch" to="/ingest"><span class="feature-icon"><Books :size="24" /></span><strong>资料解析</strong><p>保留正文、页码、图片和公式位置。</p><ArrowUpRight :size="18" /></RouterLink>
    <RouterLink class="feature-launch" to="/chat"><span class="feature-icon"><ChatsCircle :size="24" /></span><strong>智能问答</strong><p>基于资料检索回答，并展示可追溯来源。</p><ArrowUpRight :size="18" /></RouterLink>
    <RouterLink class="feature-launch" :to="auth.isAdmin ? '/chunks' : '/profile'"><span class="feature-icon"><FileMagnifyingGlass :size="24" /></span><strong>{{ auth.isAdmin ? '复习切片' : '学习画像' }}</strong><p>{{ auth.isAdmin ? '检查知识点边界和原始资料。' : '调整学习科目与回答方式。' }}</p><ArrowUpRight :size="18" /></RouterLink>
  </section>

  <section class="home-section-title">
    <div><p class="hero-kicker">学习空间</p><h2>你的资料与进度</h2></div>
    <RouterLink class="text-button" to="/ingest">管理资料 <ArrowUpRight :size="15" /></RouterLink>
  </section>

  <section class="home-lower-grid fade-up delay-2">
    <section class="library-surface">
      <div class="library-summary"><span><Books :size="18" /> 已收录资料</span><strong>{{ store.documents.length }}</strong></div>
      <div v-if="store.documents.length" class="document-list library-list">
        <div v-for="document in store.documents.slice(0, 5)" :key="document.document_id" class="document-row">
          <div class="file-type">{{ document.source_type.toUpperCase() }}</div>
          <div class="document-name"><strong>{{ document.topic_label || document.filename }}</strong><span>{{ document.filename }}</span></div>
          <span class="document-size">{{ formatBytes(document.size) }}</span>
          <RouterLink v-if="auth.isAdmin" class="row-action" :to="`/chunks?document=${document.document_id}`" title="检查切片"><ArrowUpRight :size="16" /></RouterLink>
        </div>
      </div>
      <div v-else class="empty-state compact"><Books :size="25" /><strong>还没有资料</strong><span>先上传一份教材、讲义或错题开始建立知识库。</span></div>
    </section>
    <aside class="progress-surface">
      <div class="progress-orb"><TrendUp :size="30" /></div>
      <h3>解析任务</h3>
      <p v-if="store.activeTaskCount">{{ store.activeTaskCount }} 个任务正在处理，完成后会自动进入资料库。</p>
      <p v-else>资料上传后会在这里显示解析、切片和索引进度。</p>
      <div v-if="latestTasks.length" class="task-mini-list compact-tasks"><div v-for="task in latestTasks" :key="task.task_id" class="task-mini-row"><span class="task-mini-status" :class="`status-${task.status.toLowerCase()}`"></span><div><strong>{{ task.filename }}</strong><span>{{ task.stage || '等待处理' }}</span></div><span class="task-progress">{{ task.progress }}%</span></div></div>
      <RouterLink class="text-button" to="/ingest">查看资料入库 <ArrowRight :size="15" /></RouterLink>
    </aside>
  </section>

  <section v-if="auth.isAdmin" class="teacher-strip fade-up delay-2">
    <div><CheckCircle :size="20" /><span><strong>教师工作区</strong><small>可检查切片质量、查看学生画像，并审核班级资料。</small></span></div>
    <RouterLink class="secondary-button" to="/admin">进入班级管理 <ArrowRight :size="15" /></RouterLink>
  </section>
</template>
