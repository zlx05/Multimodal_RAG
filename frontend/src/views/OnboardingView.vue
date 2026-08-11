<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { ArrowRight, Check, CheckCircle, Sparkle } from "@/components/icons";
import { api } from "@/api/client";
import type { AnswerStyle } from "@/api/types";
import { useAuthStore } from "@/stores/auth";

const router = useRouter();
const auth = useAuthStore();

const step = ref(0);
const submitting = ref(false);
const error = ref("");

const SUBJECT_CHIPS = ["数学", "物理", "化学", "生物", "英语", "语文", "历史", "地理", "政治"] as const;
const WEAK_CHIPS = [
  "计算总出错",
  "概念容易混",
  "公式记不牢",
  "解题没思路",
  "作图题吃力",
  "实验题丢分",
  "阅读读不完",
  "作文没素材",
  "听力跟不上",
  "文言文读不懂",
] as const;

const weakPoints = ref<string[]>([]);
const subjects = ref<string[]>([]);
const answerStyle = ref<AnswerStyle | null>(null);
const customWeak = ref("");
const customSubject = ref("");

const styleOptions: { value: AnswerStyle; label: string; desc: string; point: string }[] = [
  { value: "direct", label: "直接给答案", desc: "结论先行、简明扼要，把要点一次讲透", point: "适合复习冲刺，想快速拿到结果" },
  { value: "guiding", label: "先给思路", desc: "先讲思路和关键提示，再展开细节", point: "适合平时练习，想保留自己思考的余地" },
  { value: "socratic", label: "循循善诱", desc: "通过递进提问，一步步引导你自己得出答案", point: "适合吃透概念，想真正理解原理" },
];

const canNext = computed(() => {
  if (step.value === 0) return weakPoints.value.length > 0;
  if (step.value === 1) return subjects.value.length > 0;
  return answerStyle.value !== null;
});
const customWeakTags = computed(() => weakPoints.value.filter((w) => !(WEAK_CHIPS as readonly string[]).includes(w)));
const customSubjectTags = computed(() => subjects.value.filter((s) => !(SUBJECT_CHIPS as readonly string[]).includes(s)));

function toggleWeak(item: string) {
  const index = weakPoints.value.indexOf(item);
  if (index >= 0) weakPoints.value.splice(index, 1);
  else weakPoints.value.push(item);
}

function toggleSubject(item: string) {
  const index = subjects.value.indexOf(item);
  if (index >= 0) subjects.value.splice(index, 1);
  else subjects.value.push(item);
}

function pushCustom(list: string[], input: string) {
  const text = input.trim();
  if (!text) return;
  for (const item of text.split(/[、,，\s]+/).map((s) => s.trim()).filter(Boolean)) {
    if (!list.includes(item)) list.push(item);
  }
}

function next() {
  if (step.value < 2) step.value += 1;
}

async function submit() {
  if (!answerStyle.value) return;
  submitting.value = true;
  error.value = "";
  try {
    await api.submitSurvey({
      subjects: subjects.value,
      weak_points: weakPoints.value,
      answer_style: answerStyle.value,
    });
    auth.needsOnboarding = false;
    await router.replace("/home");
  } catch (err) {
    error.value = err instanceof Error ? err.message : "提交失败，请重试";
    submitting.value = false;
  }
}
</script>

<template>
  <div class="onboarding-page">
    <div class="onboarding-card">
      <header class="onboarding-head">
        <div class="login-brand">
          <div class="brand-mark"><Sparkle :size="20" weight="bold" /></div>
          <div>
            <strong>先花一分钟认识你</strong>
            <span>之后画像会根据每次问答自动更新</span>
          </div>
        </div>

        <div class="step-rail" role="group" :aria-label="`第 ${step + 1} 步，共 3 步`">
          <span v-for="index in 3" :key="index" class="step-dot" :class="{ active: step >= index - 1 }"></span>
        </div>
      </header>

      <main class="onboarding-body">
        <transition name="step" mode="out-in">
          <section v-if="step === 0" key="weak" class="step-pane">
            <h1>常在哪里卡住？</h1>
            <p class="step-copy">点选经常丢分或学不透的地方，可以多选。</p>
            <div class="chip-grid">
              <button
                v-for="chip in WEAK_CHIPS"
                :key="chip"
                type="button"
                class="chip"
                :class="{ selected: weakPoints.includes(chip) }"
                @click="toggleWeak(chip)"
              >
                <Check v-if="weakPoints.includes(chip)" :size="13" weight="bold" />
                {{ chip }}
              </button>
            </div>
            <div class="chip-add">
              <input v-model.trim="customWeak" type="text" placeholder="还有别的，例如：三角函数" @keydown.enter.prevent="pushCustom(weakPoints, customWeak)" />
              <button class="chip-add-button" type="button" :disabled="!customWeak" @click="pushCustom(weakPoints, customWeak)">添加</button>
            </div>
            <div v-if="customWeakTags.length" class="custom-tags">
              <span v-for="item in customWeakTags" :key="item" class="custom-tag">
                {{ item }}
                <button type="button" aria-label="移除" @click="toggleWeak(item)">×</button>
              </span>
            </div>
          </section>

          <section v-else-if="step === 1" key="subject" class="step-pane">
            <h1>主要学哪些科目？</h1>
            <p class="step-copy">问答检索会优先照顾这些科目，之后也能在画像里改。</p>
            <div class="chip-grid">
              <button
                v-for="chip in SUBJECT_CHIPS"
                :key="chip"
                type="button"
                class="chip"
                :class="{ selected: subjects.includes(chip) }"
                @click="toggleSubject(chip)"
              >
                <Check v-if="subjects.includes(chip)" :size="13" weight="bold" />
                {{ chip }}
              </button>
            </div>
            <div class="chip-add">
              <input v-model.trim="customSubject" type="text" placeholder="还有别的，例如：编程" @keydown.enter.prevent="pushCustom(subjects, customSubject)" />
              <button class="chip-add-button" type="button" :disabled="!customSubject" @click="pushCustom(subjects, customSubject)">添加</button>
            </div>
            <div v-if="customSubjectTags.length" class="custom-tags">
              <span v-for="item in customSubjectTags" :key="item" class="custom-tag">
                {{ item }}
                <button type="button" aria-label="移除" @click="toggleSubject(item)">×</button>
              </span>
            </div>
          </section>

          <section v-else key="style" class="step-pane">
            <h1>喜欢什么样的回答？</h1>
            <p class="step-copy">选一个最贴近你的习惯，之后也会根据问答自动调整。</p>
            <div class="style-cards">
              <button
                v-for="option in styleOptions"
                :key="option.value"
                type="button"
                class="style-card"
                :class="{ selected: answerStyle === option.value }"
                @click="answerStyle = option.value"
              >
                <span class="style-check"><Check v-if="answerStyle === option.value" :size="14" weight="bold" /></span>
                <strong>{{ option.label }}</strong>
                <span class="style-desc">{{ option.desc }}</span>
                <span class="style-point">{{ option.point }}</span>
              </button>
            </div>
          </section>
        </transition>
      </main>

      <footer class="onboarding-foot">
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <div class="foot-actions">
          <button v-if="step > 0" class="text-button" type="button" @click="step -= 1">上一步</button>
          <button
            v-if="step < 2"
            class="primary-button onboarding-next"
            type="button"
            :disabled="!canNext"
            @click="next"
          >
            下一步 <ArrowRight :size="16" />
          </button>
          <button
            v-else
            class="primary-button onboarding-next"
            type="button"
            :disabled="!canNext || submitting"
            @click="submit"
          >
            {{ submitting ? "提交中…" : "开始学习" }} <CheckCircle :size="16" />
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.onboarding-page {
  display: grid;
  min-height: 100dvh;
  place-items: center;
  padding: 24px;
  background: radial-gradient(circle at 28% 18%, var(--accent-soft) 0, transparent 44%), var(--canvas);
}

.onboarding-card {
  width: min(560px, 100%);
  padding: 30px 32px 26px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow);
}

.onboarding-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 26px;
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.login-brand strong,
.login-brand span {
  display: block;
}

.login-brand strong {
  font-size: 0.98rem;
  letter-spacing: -0.02em;
}

.login-brand span {
  margin-top: 2px;
  color: var(--ink-faint);
  font-size: 0.72rem;
}

.step-rail {
  display: flex;
  align-items: center;
  gap: 6px;
  padding-top: 6px;
}

.step-dot {
  width: 22px;
  height: 4px;
  border-radius: 999px;
  background: var(--line-strong);
  transition: background-color 200ms ease, width 200ms ease;
}

.step-dot.active {
  width: 34px;
  background: var(--accent);
}

.step-pane h1 {
  font-size: 1.5rem;
}

.step-copy {
  margin-top: 8px;
  color: var(--ink-soft);
  font-size: 0.86rem;
}

.chip-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 20px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 36px;
  padding: 0 13px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: var(--surface);
  color: var(--ink-soft);
  font-size: 0.8rem;
  font-weight: 650;
  transition: border-color 160ms ease, background-color 160ms ease, color 160ms ease, transform 160ms ease;
}

.chip:hover {
  border-color: var(--accent);
  color: var(--accent-deep);
}

.chip.selected {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent-deep);
}

.chip:active {
  transform: scale(0.97);
}

.chip-add {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}

.chip-add input {
  flex: 1;
  min-height: 38px;
  padding: 0 12px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-small);
  background: var(--surface-soft);
  color: var(--ink);
  font-size: 0.84rem;
  outline: none;
}

.chip-add input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.chip-add-button {
  min-height: 38px;
  padding: 0 14px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-small);
  background: var(--surface);
  color: var(--ink-soft);
  font-size: 0.78rem;
  font-weight: 700;
}

.chip-add-button:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent-deep);
}

.custom-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}

.custom-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--line));
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent-deep);
  font-size: 0.74rem;
  font-weight: 650;
}

.custom-tag button {
  border: 0;
  background: transparent;
  color: var(--accent);
  font-size: 0.9rem;
  line-height: 1;
  cursor: pointer;
}

.style-cards {
  display: grid;
  gap: 10px;
  margin-top: 20px;
}

.style-card {
  position: relative;
  display: grid;
  gap: 3px;
  padding: 15px 16px 14px 46px;
  border: 1px solid var(--line);
  border-radius: var(--radius-small);
  background: var(--surface);
  text-align: left;
  cursor: pointer;
  transition: border-color 160ms ease, background-color 160ms ease, transform 160ms ease;
}

.style-card:hover {
  border-color: var(--accent);
  transform: translateY(-1px);
}

.style-card.selected {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.style-check {
  position: absolute;
  top: 15px;
  left: 15px;
  display: grid;
  width: 21px;
  height: 21px;
  place-items: center;
  border: 1px solid var(--line-strong);
  border-radius: 50%;
  background: var(--surface);
  color: var(--accent);
}

.style-card.selected .style-check {
  border-color: var(--accent);
  background: var(--accent);
  color: #f6fbff;
}

.style-card strong {
  font-size: 0.92rem;
}

.style-desc {
  color: var(--ink-soft);
  font-size: 0.8rem;
}

.style-point {
  color: var(--ink-faint);
  font-size: 0.72rem;
}

.onboarding-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 26px;
  padding-top: 18px;
  border-top: 1px solid var(--line);
}

.foot-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  flex: 1;
}

.form-error {
  color: var(--danger);
  font-size: 0.8rem;
}

.onboarding-next {
  min-width: 122px;
}

.step-enter-active,
.step-leave-active {
  transition: opacity 200ms ease, transform 200ms ease;
}

.step-enter-from {
  opacity: 0;
  transform: translateX(14px);
}

.step-leave-to {
  opacity: 0;
  transform: translateX(-14px);
}

@media (max-width: 480px) {
  .onboarding-card {
    padding: 24px 20px 20px;
  }
}
</style>
