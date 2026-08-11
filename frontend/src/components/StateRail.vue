<script setup lang="ts">
import { Check, CircleNotch, WarningCircle } from "@/components/icons";

export interface StateStep {
  key: string;
  label: string;
  caption: string;
}

const props = defineProps<{
  steps: StateStep[];
  active: number;
  status?: "idle" | "active" | "success" | "error";
}>();
</script>

<template>
  <div class="state-rail" :class="`rail-${props.status ?? 'active'}`">
    <div v-for="(step, index) in steps" :key="step.key" class="state-step" :class="{
      done: index < active || status === 'success',
      current: index === active && status !== 'success' && status !== 'error',
      failed: index === active && status === 'error',
    }">
      <div class="state-node">
        <Check v-if="index < active || status === 'success'" :size="15" weight="bold" />
        <WarningCircle v-else-if="index === active && status === 'error'" :size="16" weight="bold" />
        <CircleNotch v-else-if="index === active && status === 'active'" :size="16" class="spin" />
        <span v-else>{{ index + 1 }}</span>
      </div>
      <div class="state-copy">
        <strong>{{ step.label }}</strong>
        <small>{{ step.caption }}</small>
      </div>
      <div v-if="index < steps.length - 1" class="state-connector" :class="{ filled: index < active || status === 'success' }"></div>
    </div>
  </div>
</template>
