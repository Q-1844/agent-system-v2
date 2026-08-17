<template>
  <!-- AI-6D.1 §11：决策点。左=选择/状态，右=动作。
       N of M 显著；状态行（ok/warn/stale）即时反映：full/subset 预览就绪、选择已变需再预览、stale、有警告。
       Apply 仅在真正可应用时变强（accent），disabled 永不看似 enabled（§11）。预览/apply 复用 AI-6D 真实后端语义。 -->
  <div class="prb">
    <div class="prb-left">
      <span class="prb-count">{{ selectedCount }}<span class="prb-of">/ {{ totalCount }} selected</span></span>
      <span class="prb-status" :class="statusTone">{{ statusText }}</span>
    </div>
    <div class="prb-actions">
      <button class="prb-quiet" @click="$emit('dismissAll')">Dismiss</button>
      <button class="prb-quiet" :class="{ badge: hasWarnings }" @click="$emit('reviewImpact')">
        Impact{{ hasWarnings ? ' ⚠' : '' }}
      </button>
      <button class="prb-secondary" :disabled="previewing || !selectedCount" @click="$emit('previewSelected')">
        {{ previewing ? '预览中…' : '预览所选' }}
      </button>
      <button class="prb-primary" :disabled="!canApply || applying" :title="applyTitle" @click="$emit('applySelected')">
        {{ applying ? '应用中…' : 'Apply' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { ProposalSummary } from './proposal';

const props = defineProps<{
  summary: ProposalSummary;
  selectedCount: number;
  totalCount: number;
  canApply: boolean;          // 真正可应用（changeset 在、非 stale、非 selectionDirty、有选中）
  previewing: boolean;
  applying: boolean;
  statusText: string;
  statusTone: 'ok' | 'warn' | 'stale';
  hasWarnings: boolean;
}>();
defineEmits<{ (e: 'dismissAll'): void; (e: 'previewSelected'): void; (e: 'applySelected'): void; (e: 'reviewImpact'): void }>();

const applyTitle = computed(() => props.canApply
  ? '应用当前预览的 ChangeSet（按 changeset_id → AI-6A apply → 创作版本；官方分镜不变）'
  : '不可应用：先预览所选（或提案已 stale）');
</script>

<style lang="scss" scoped>
.prb {
  display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap;
  padding: 8px 10px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
}
.prb-left { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; min-width: 0; }
.prb-count { font-size: 13px; font-weight: 700; color: var(--text-primary); white-space: nowrap; }
.prb-of { font-size: 11px; font-weight: 400; color: var(--text-muted); }
.prb-status {
  font-size: 11px; color: var(--text-muted);
  &.ok { color: var(--success); }
  &.warn { color: var(--warning); }
  &.stale { color: var(--error); }
}

.prb-actions { display: flex; gap: 6px; align-items: center; }
.prb-quiet {
  padding: 4px 9px; border-radius: 5px; border: none;
  background: none; color: var(--text-muted);
  font-size: 11px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
  &.badge { color: var(--warning); }
}
.prb-secondary {
  padding: 5px 11px; border-radius: 5px; border: 1px solid var(--border);
  background: var(--bg-root); color: var(--text-secondary);
  font-size: 12px; cursor: pointer;
  &:hover:not(:disabled) { border-color: var(--border-hover); color: var(--text-primary); }
  &:disabled { opacity: .4; cursor: not-allowed; }
}
.prb-primary {
  padding: 5px 14px; border-radius: 5px; border: 1px solid var(--accent);
  background: var(--accent); color: var(--accent-text);
  font-size: 12px; font-weight: 600; cursor: pointer;
  &:hover:not(:disabled) { background: var(--accent-hover); border-color: var(--accent-hover); }
  &:disabled { background: var(--bg-root); border-color: var(--border); color: var(--text-faint); cursor: not-allowed; }   /* §11：disabled 永不看似 enabled */
}
</style>
