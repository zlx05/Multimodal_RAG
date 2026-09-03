<script setup lang="ts">
import { RouterLink } from "vue-router";
import { ChatsCircle, Cube, TrendUp } from "@/components/icons";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
</script>

<template>
  <section class="new-home-hero">
    <div class="new-hero-content">
      <h1 class="new-hero-title">从一份资料开始复习</h1>
      <p class="new-hero-subtitle">上传教材、错题本、笔记后，系统会自动解析、切片、检索，<br>并生成可追问的知识上下文。</p>
      <RouterLink class="new-hero-button" to="/ingest">上传资料</RouterLink>
    </div>
    <div class="new-hero-visual">
      <div class="new-hero-orb"></div>
      <div class="new-hero-document">
        <div class="doc-icon">
          <div class="doc-lines"></div>
          <div class="doc-lines"></div>
          <div class="doc-lines"></div>
        </div>
      </div>
      <div class="new-hero-dots">
        <span class="dot dot-1"></span>
        <span class="dot dot-2"></span>
        <span class="dot dot-3"></span>
      </div>
      <svg class="new-hero-lines" viewBox="0 0 400 300" xmlns="http://www.w3.org/2000/svg">
        <line x1="80" y1="100" x2="200" y2="150" stroke="currentColor" stroke-width="1.5" opacity="0.3"/>
        <line x1="320" y1="100" x2="200" y2="150" stroke="currentColor" stroke-width="1.5" opacity="0.3"/>
      </svg>
    </div>
  </section>

  <section class="new-feature-grid">
    <RouterLink class="new-feature-card" to="/ingest">
      <div class="feature-card-icon">
        <Cube :size="28" />
      </div>
      <h3>资料解析</h3>
      <p>保留正文、页码、图片和公式位置，解析结果自动入库。</p>
    </RouterLink>
    <RouterLink class="new-feature-card" to="/chat">
      <div class="feature-card-icon">
        <ChatsCircle :size="28" />
      </div>
      <h3>智能问答</h3>
      <p>基于资料检索回答，并展示可追溯来源。</p>
    </RouterLink>
    <RouterLink class="new-feature-card" :to="auth.isAdmin ? '/chunks' : '/profile'">
      <div class="feature-card-icon">
        <TrendUp :size="28" />
      </div>
      <h3>{{ auth.isAdmin ? '复习切片' : '学习画像' }}</h3>
      <p>{{ auth.isAdmin ? '检查知识点边界和原始资料，确保分块质量' : '追踪问题进展和薄弱点，提供最适合你的答案' }}</p>
    </RouterLink>
  </section>
</template>
