<template>
  <div class="prompt-editor">
    <div class="pe-head">
      <span class="pe-label">{{ label }}</span>
      <span v-if="hint" class="pe-hint">{{ hint }}</span>
    </div>
    <textarea :value="modelValue"
      @input="$emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
      rows="6" class="pe-text" :placeholder="placeholder"></textarea>
    <div class="pe-actions">
      <button class="mini-btn" :disabled="genBusy" @click="$emit('generate')">
        {{ genBusy ? '生成中…' : '根据上下文生成' }}
      </button>
      <button class="mini-btn" :disabled="optBusy || !modelValue.trim()" @click="$emit('optimize')" :title="!modelValue.trim() ? '先填写或生成提示词后再优化' : ''">
        {{ optBusy ? '优化中…' : '优化提示词' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
// 标准模式主提示词编辑器：大尺寸多行文本 + [根据上下文生成] [优化提示词] 两按钮。
// 两按钮只填写文本，不自动发起首帧/尾帧/视频生成。加载状态由父组件控制（genBusy/optBusy）。
defineProps<{
  modelValue: string;
  label: string;
  hint?: string;
  placeholder?: string;
  genBusy: boolean;
  optBusy: boolean;
}>();
defineEmits<{
  (e: 'update:modelValue', v: string): void;
  (e: 'generate'): void;
  (e: 'optimize'): void;
}>();
</script>

<style lang="scss" scoped>
.prompt-editor { padding: 0; }
.pe-head { display: flex; flex-direction: column; gap: 4px; margin-bottom: 8px; }
.pe-label { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.pe-hint { font-size: 12px; color: var(--text-muted); line-height: 1.5; }
.pe-text {
  width: 100%; background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px;
  padding: 10px 12px; font-size: 13px; color: var(--text-primary); outline: none; resize: vertical;
  line-height: 1.6; font-family: inherit; min-height: 180px;
  &:focus { border-color: var(--accent); }
}
.pe-actions { display: flex; gap: 8px; margin-top: 10px; }
.mini-btn {
  padding: 6px 12px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg-surface);
  color: var(--text-secondary); font-size: 12px; cursor: pointer; white-space: nowrap;
  transition: border-color .12s, color .12s;
  &:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  &:disabled { opacity: 0.5; cursor: not-allowed; }
}
</style>
