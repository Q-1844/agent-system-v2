<template>
  <!-- AI-6C §19/§26：Director Proposal 呈现 + review。无真实 Proposal 时不渲染（§38 场景10）。
       跟随 Story Segment 工作区（非 global footer / 非 sidebar，§26）。
       apply 必须走 ChangeSet（§27）：仅当 proposal 携带真实 workspaceId + changeset 时调用 applyChangeset，
       否则 Apply 禁用并 emit requestApply（不伪造后端调用）。 -->
  <div v-if="proposal" class="dpo">
    <div class="dpo-head">
      <div>
        <div class="dpo-title">{{ proposal.title || 'Director Proposal' }}</div>
        <div v-if="proposal.intent" class="dpo-intent">{{ proposal.intent }}</div>
      </div>
      <button class="dpo-close" title="关闭" aria-label="关闭 Proposal" @click="$emit('close')">×</button>
    </div>

    <div class="dpo-cards">
      <ProposedShotCard
        v-for="(op, i) in proposal.operations"
        :key="i"
        :op="op"
        :selected="selectedIndices.includes(i)"
        @toggle="toggle(i)"
        @accept="select(i)"
        @dismiss="deselect(i)"
        @review="review(i)"
      />
    </div>

    <ProposalReviewBar
      :summary="summary"
      :selected-count="selectedIndices.length"
      :can-apply="canApply"
      @dismiss-all="$emit('close')"
      @review-impact="$emit('reviewImpact', proposal)"
      @apply-selected="onApplySelected"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { apiClient } from '@/api';
import type { DirectorProposal } from './proposal';
import { summarize } from './proposal';
import ProposedShotCard from './ProposedShotCard.vue';
import ProposalReviewBar from './ProposalReviewBar.vue';

const props = defineProps<{ proposal: DirectorProposal | null; projectName: string }>();
const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'applied', p: DirectorProposal): void;
  (e: 'requestApply', payload: { proposal: DirectorProposal; selectedIndices: number[] }): void;
  (e: 'reviewImpact', p: DirectorProposal): void;
}>();

const selectedIndices = ref<number[]>([]);

// proposal 变化时默认全选
watch(() => props.proposal, (p) => {
  selectedIndices.value = p ? p.operations.map((_, i) => i) : [];
}, { immediate: true });

const summary = computed(() => summarize(props.proposal?.operations || []));
const canApply = computed(() => !!(props.proposal?.workspaceId && props.proposal?.changeset));

function toggle(i: number) {
  const idx = selectedIndices.value.indexOf(i);
  if (idx >= 0) selectedIndices.value.splice(idx, 1);
  else selectedIndices.value.push(i);
}
function select(i: number) { if (!selectedIndices.value.includes(i)) selectedIndices.value.push(i); }
function deselect(i: number) {
  const idx = selectedIndices.value.indexOf(i);
  if (idx >= 0) selectedIndices.value.splice(idx, 1);
}
function review(_i: number) { /* 展开单条 diff 细节；边界阶段不展开结构，后续打磨 */ }

async function onApplySelected() {
  const p = props.proposal;
  if (!p) return;
  // §27：真实 apply 走 ChangeSet。subset 与 all 不同时需 re-preview（未来）；本轮 all → applyChangeset。
  if (canApply.value && p.workspaceId && p.changeset) {
    try {
      await apiClient.applyChangeset(props.projectName, p.workspaceId, p.changeset.changeset_id);
      emit('applied', p);
      emit('close');
    } catch (e) {
      // apply 失败（冲突/stale 等）由 ChangeSet 映射 HTTP 错；此处不伪造，向上抛
      console.error('[DirectorProposal] apply 失败', e);
    }
  } else {
    // storyboard.propose_change 未接通：不伪造 apply，交父组件处理（提示/留接口）
    emit('requestApply', { proposal: p, selectedIndices: [...selectedIndices.value] });
  }
}
</script>

<style lang="scss" scoped>
.dpo {
  display: flex; flex-direction: column; gap: 10px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 14px;
}
.dpo-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.dpo-title { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.dpo-intent { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.dpo-close {
  background: none; border: none; color: var(--text-muted);
  font-size: 18px; line-height: 1; cursor: pointer; padding: 0 4px; border-radius: 4px;
  &:hover { color: var(--text-primary); background: var(--bg-hover); }
}
.dpo-cards { display: flex; flex-direction: column; gap: 8px; }
</style>
