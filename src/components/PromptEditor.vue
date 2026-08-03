<template>
  <div class="prompt-editor">
    <div class="pe-head">
      <span class="pe-label">英文提示词</span>
      <span class="pe-status" :class="status">{{ statusText }}</span>
      <div class="pe-actions">
        <button class="mini-btn" :disabled="busy" @click="$emit('generate')">
          {{ busy ? '生成中…' : (status === 'empty' ? '生成提示词' : '更新提示词') }}
        </button>
        <button v-if="status !== 'empty'" class="mini-btn" :disabled="busy" @click="$emit('regenerate')" title="按当前字段重新生成（覆盖手动版本）">重新生成</button>
        <button v-if="status === 'manual'" class="mini-btn" @click="$emit('reset-auto')" title="清除手动覆盖，回到自动生成版本">恢复自动</button>
        <button v-if="!showFull" class="mini-btn" @click="expanded = !expanded">{{ expanded ? '收起' : '展开' }}</button>
      </div>
    </div>
    <textarea v-if="showFull || expanded" :value="modelValue"
      @input="$emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
      rows="4" class="pe-text"
      placeholder="点上方生成，或手动编辑（手动编辑后标记为 override，生成资产时优先使用）"></textarea>
    <p v-else-if="modelValue" class="pe-preview" @click="expanded = true">
      {{ modelValue.slice(0, 90) }}{{ modelValue.length > 90 ? '…' : '' }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
type Status = 'empty' | 'auto' | 'manual' | 'stale';
const props = defineProps<{ modelValue: string; status: Status; busy: boolean; showFull: boolean }>();
defineEmits<{
  (e: 'update:modelValue', v: string): void;
  (e: 'generate'): void; (e: 'regenerate'): void; (e: 'reset-auto'): void;
}>();
const expanded = ref(false);
const statusText = computed(() => ({ empty: '尚未生成', auto: '已自动生成', manual: '已手动修改', stale: '字段已变化，待更新' } as const)[props.status]);
</script>

<style lang="scss" scoped>
.prompt-editor { margin-top: 16px; padding: 12px 14px; background: var(--bg-surface); border: 1px solid var(--border); border-top: 1px solid var(--border-hover); border-radius: 8px; }
.pe-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.pe-label { font-size: 12px; font-weight: 500; color: var(--text-secondary); white-space: nowrap; }
.pe-status { font-size: 11px; padding: 2px 8px; border-radius: 9px; border: 1px solid var(--border); color: var(--text-muted); white-space: nowrap;
  &.auto { color: #4ade80; border-color: rgba(74,222,128,0.4); }
  &.manual { color: #60a5fa; border-color: rgba(96,165,250,0.4); }
  &.stale { color: #fbbf24; border-color: rgba(251,191,36,0.4); }
  &.empty { color: var(--text-faint); }
}
.pe-actions { margin-left: auto; display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.mini-btn { padding: 6px 12px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg-root); color: var(--text-secondary); font-size: 12px; cursor: pointer; white-space: nowrap; transition: border-color .12s, color .12s; &:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); } &:disabled { opacity: 0.5; cursor: not-allowed; } }
.pe-text { width: 100%; margin-top: 10px; background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px; padding: 9px 12px; font-size: 13px; color: var(--text-primary); outline: none; resize: vertical; line-height: 1.55; font-family: inherit; min-height: 84px; &:focus { border-color: var(--accent); } }
.pe-preview { margin: 8px 0 0; font-size: 12px; color: var(--text-secondary); cursor: pointer; line-height: 1.5; &:hover { color: var(--accent); } }
</style>
