<template>
  <!-- AI-6C §26/§27：Proposal review control（非 global footer / 非 Director sidebar）。
       支持接受全部 / 接受部分 / 忽略某项（§27）。Apply 必须走 ChangeSet（不前端直接改 project.json）。
       canApply=false（无真实 workspace+changeset）时 Apply 禁用并提示未接通，不伪造。 -->
  <div class="prb">
    <div class="prb-info">
      <span class="prb-title">Director Proposal</span>
      <span class="prb-summary">
        {{ summary.total }} changes · {{ summary.insert }} insert · {{ summary.update }} update · {{ summary.reorder }} reorder
      </span>
      <span v-if="selectedCount !== summary.total" class="prb-selected">已选 {{ selectedCount }}/{{ summary.total }}</span>
    </div>
    <div class="prb-actions">
      <button class="prb-btn" @click="$emit('dismissAll')">Dismiss</button>
      <button class="prb-btn" @click="$emit('reviewImpact')">Review impact</button>
      <button
        class="prb-btn primary"
        :disabled="!canApply || !selectedCount"
        :title="applyTitle"
        @click="$emit('applySelected')"
      >Apply selected</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { ProposalSummary } from './proposal';

const props = defineProps<{
  summary: ProposalSummary;
  selectedCount: number;
  canApply: boolean;     // 真实 workspace + changeset 就绪才允许 apply
}>();
defineEmits<{ (e: 'dismissAll'): void; (e: 'applySelected'): void; (e: 'reviewImpact'): void }>();

const applyTitle = computed(() =>
  props.canApply
    ? '应用所选修改（走 ChangeSet → Validation → Impact → Workspace）'
    : 'Director `storyboard.propose_change` 尚未接通，暂不能应用',
);
</script>

<style lang="scss" scoped>
.prb {
  display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;
  padding: 10px 14px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
}
.prb-info { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.prb-title { font-size: 12px; font-weight: 600; color: var(--text-primary); }
.prb-summary { font-size: 11px; color: var(--text-secondary); }
.prb-selected { font-size: 11px; color: var(--accent); }
.prb-actions { display: flex; gap: 8px; }
.prb-btn {
  padding: 5px 12px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--bg-root); color: var(--text-secondary);
  font-size: 12px; cursor: pointer;
  &:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); }
  &:disabled { opacity: .4; cursor: not-allowed; }
  &.primary { border-color: var(--accent); color: var(--accent); &:hover:not(:disabled) { background: var(--accent); color: var(--accent-text); } }
}
</style>
