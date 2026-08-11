<script setup lang="ts">
import { Cpu } from "@/components/icons";
import type { ModelOption } from "@/api/types";

defineProps<{
  models: ModelOption[];
  model: string;
}>();

const emit = defineEmits<{ change: [model: string] }>();
</script>

<template>
  <label class="model-switcher">
    <span class="field-label"><Cpu :size="15" /> 回答模型</span>
    <select :value="model" aria-label="选择回答模型" @change="emit('change', ($event.target as HTMLSelectElement).value)">
      <option v-for="option in models" :key="option.id" :value="option.id" :disabled="!option.ready">
        {{ option.label }}{{ option.ready ? "" : "（待配置密钥）" }}
      </option>
    </select>
  </label>
</template>
