<template>
  <!-- AI-6D.1 §8/§9/§12/§13：AI 修订层覆盖在 Storyboard 上（非侧栏 CRUD）。
       层级：创作版本说明 → title/intent → 语义 items（可点选）→ impact 摘要+warnings（渐进：diff 可展开）
       → 决策条（N of M / 状态 / 预览所选 / Apply）。状态内聚：dirty/stale/previewing/applying/error。
       复用 AI-6D 真实数据，不重算 diff/impact/warnings（§12）。-->
  <div v-if="proposal" class="dpo" :class="{ stale: proposal.stale }">
    <div class="dpo-head">
      <div class="dpo-head-txt">
        <div class="dpo-title">{{ proposal.title || 'Director Proposal' }}</div>
        <div v-if="proposal.summary" class="dpo-intent">{{ proposal.summary }}</div>
      </div>
      <button class="dpo-close" title="关闭" aria-label="关闭 Proposal" @click="$emit('close')">×</button>
    </div>

    <!-- §13：创作版本感知（安静、用户语）-->
    <div class="dpo-ws-note">编辑创作版本 · 应用只改创作版本，官方分镜不变</div>
    <div v-if="proposal.base_is_stale" class="dpo-warn">基于较早的官方分镜快照（仍可应用为历史分支）</div>
    <div v-if="proposal.stale" class="dpo-stale">该提案的创作版本自生成后已变化，请重新生成提案。</div>
    <div v-if="error" class="dpo-error">{{ error }}</div>

    <!-- 语义 items（§14：一张卡 = 一个语义修改，不暴露 ChangeOperation）-->
    <div class="dpo-cards">
      <ProposedShotCard
        v-for="it in proposal.items"
        :key="it.item_id"
        :item="it"
        :selected="selectedItemIds.includes(it.item_id)"
        @toggle="toggleItem(it.item_id)"
        @review="toggleDiff"
      />
      <div v-if="!proposal.items.length" class="dpo-empty">Director 没有提出修改项。</div>
    </div>

    <!-- §12：渐进披露——impact 摘要在前，diff 按需展开，warnings 在 Apply 前可见 -->
    <div v-if="proposal.changeset" class="dpo-meta">
      <div v-if="impactSummaryText" class="dpo-impact-summary">{{ impactSummaryText }}</div>
      <div v-if="hasWarnings" class="dpo-warnings">
        <span v-for="(w, i) in proposal.changeset.warnings" :key="i" class="dpo-warn-item">⚠ {{ w }}</span>
      </div>
      <button v-if="hasDiff" class="dpo-toggle" @click="toggleDiff">
        {{ showDiff ? '收起' : '查看' }} diff（{{ diffCount }}）
      </button>
      <div v-if="showDiff && hasDiff" class="dpo-diff">
        <div v-for="(d, i) in proposal.changeset.diff" :key="i" class="dpo-diff-row">
          <span class="dpo-diff-kind" :class="d.kind">{{ d.kind }}</span>
          <span class="dpo-diff-id">{{ d.id }}</span>
          <span v-if="d.field" class="dpo-diff-field">.{{ d.field }}</span>
        </div>
      </div>
    </div>

    <ProposalReviewBar
      :summary="summary"
      :selected-count="selectedCount"
      :total-count="totalCount"
      :can-apply="canApply"
      :previewing="previewing"
      :applying="applying"
      :status-text="statusText"
      :status-tone="statusTone"
      :has-warnings="hasWarnings"
      @dismiss-all="$emit('close')"
      @review-impact="toggleDiff"
      @preview-selected="onPreviewSelected"
      @apply-selected="onApplySelected"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { apiClient } from '@/api';
import type { DirectorProposal } from './proposal';
import { summarizeItems } from './proposal';
import ProposedShotCard from './ProposedShotCard.vue';
import ProposalReviewBar from './ProposalReviewBar.vue';

const props = defineProps<{ proposal: DirectorProposal | null; projectName: string }>();
const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'applied', p: DirectorProposal): void;
  (e: 'updated', p: DirectorProposal): void;
}>();

const selectedItemIds = ref<string[]>([]);
const previewedItemIds = ref<string[]>([]);   // 上次预览对应的选中集（§13：判断 selection 是否已变）
const showDiff = ref(false);
const previewing = ref(false);
const applying = ref(false);
const error = ref('');

// 新提案（proposal_id 变）→ 全选 + 标记已预览全量；preview-selected 同 id 只换 changeset，保留选择
watch(() => props.proposal?.proposal_id, () => {
  const all = props.proposal ? props.proposal.items.map(i => i.item_id) : [];
  selectedItemIds.value = all;
  previewedItemIds.value = all;
  error.value = '';
}, { immediate: true });

const summary = computed(() => summarizeItems(props.proposal?.items || []));
const totalCount = computed(() => props.proposal?.items.length ?? 0);
const selectedCount = computed(() => selectedItemIds.value.length);
const stale = computed(() => !!props.proposal?.stale);
const hasWarnings = computed(() => !!props.proposal?.changeset?.warnings?.length);
const hasDiff = computed(() => !!props.proposal?.changeset?.diff?.length);
const diffCount = computed(() => props.proposal?.changeset?.diff?.length ?? 0);

function setEq(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sb = new Set(b);
  return a.every(x => sb.has(x));
}
// §13：选择自上次预览后已变 → 需重新预览所选，否则 Apply 会误用旧 changeset
const selectionDirty = computed(() => !setEq(selectedItemIds.value, previewedItemIds.value));
const canApply = computed(() =>
  !!(props.proposal?.changeset && !stale.value && !selectionDirty.value && selectedCount.value > 0));

const statusText = computed(() => {
  if (stale.value) return '创作版本已变化，需重新生成';
  if (selectionDirty.value) return '选择已更改，请重新预览所选';
  if (hasWarnings.value) return '有警告，请先查看';
  if (props.proposal?.changeset) return '预览就绪，可应用';
  return '无预览';
});
const statusTone = computed<'ok' | 'warn' | 'stale'>(() =>
  stale.value ? 'stale' : (selectionDirty.value || hasWarnings.value ? 'warn' : 'ok'));

const impactSummaryText = computed(() => {
  const s = props.proposal?.changeset?.impact?.summary as Record<string, any> | undefined;
  if (!s) return '';
  const parts: string[] = [];
  if (s.shots_added) parts.push(`+${s.shots_added} 新增`);
  if (s.shots_deleted) parts.push(`−${s.shots_deleted} 删除`);
  if (s.shots_modified) parts.push(`${s.shots_modified} 修改`);
  if (s.shots_moved) parts.push(`${s.shots_moved} 移动`);
  if (s.continuity === 'needs review') parts.push('连续性需复查');
  return parts.length ? parts.join(' · ') : '无结构变化';
});

function toggleItem(id: string) {
  const idx = selectedItemIds.value.indexOf(id);
  if (idx >= 0) selectedItemIds.value.splice(idx, 1);
  else selectedItemIds.value.push(id);
}
function toggleDiff() { showDiff.value = !showDiff.value; }

// §13/§14：重编译选中子集 → 后端新 changeset + 新 diff/impact/warnings
async function onPreviewSelected() {
  const p = props.proposal;
  if (!p || !p.workspace_id || previewing.value) return;
  if (!selectedCount.value) { error.value = '请至少选择一个修改项'; return; }
  previewing.value = true; error.value = '';
  try {
    const updated = await apiClient.previewSelectedProposal(
      props.projectName, p.proposal_id, [...selectedItemIds.value]);
    previewedItemIds.value = [...selectedItemIds.value];   // 标记当前选择已对应新 changeset
    emit('updated', updated);
  } catch (e: any) {
    error.value = e.message || '预览失败';
  } finally {
    previewing.value = false;
  }
}

// §13/§21：应用当前 changeset（按 changeset_id，复用 AI-6A apply → 创作版本；canonical 不变）
async function onApplySelected() {
  const p = props.proposal;
  if (!p || !p.changeset || applying.value) return;
  applying.value = true; error.value = '';
  try {
    await apiClient.applyDirectorProposal(props.projectName, p.proposal_id, p.changeset.changeset_id);
    emit('applied', p);
    emit('close');
  } catch (e: any) {
    error.value = e.message || '应用失败（创作版本可能已变化，请重新预览）';
  } finally {
    applying.value = false;
  }
}
</script>

<style lang="scss" scoped>
.dpo {
  display: flex; flex-direction: column; gap: 9px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 8px;
  padding: 11px 13px;
  margin-bottom: 14px;
  &.stale { border-left-color: var(--error); opacity: .92; }
}
.dpo-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.dpo-head-txt { min-width: 0; }
.dpo-title { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.dpo-intent { font-size: 11px; color: var(--text-muted); margin-top: 2px; line-height: 1.4; }
.dpo-close {
  background: none; border: none; color: var(--text-muted);
  font-size: 16px; line-height: 1; cursor: pointer; padding: 0 4px; border-radius: 4px; flex-shrink: 0;
  &:hover { color: var(--text-primary); background: var(--bg-hover); }
}
.dpo-ws-note { font-size: 10px; color: var(--text-faint); }
.dpo-warn { font-size: 11px; color: var(--warning); }
.dpo-stale { font-size: 11px; color: var(--error); padding: 5px 9px; background: rgba(239,68,68,.08); border-radius: 5px; }
.dpo-error { font-size: 11px; color: var(--error); padding: 5px 9px; background: rgba(239,68,68,.08); border-radius: 5px; }
.dpo-cards { display: flex; flex-direction: column; gap: 6px; }
.dpo-empty { font-size: 11px; color: var(--text-faint); }
.dpo-meta { display: flex; flex-direction: column; gap: 5px; font-size: 11px; }
.dpo-impact-summary { color: var(--text-secondary); }
.dpo-warnings { display: flex; flex-direction: column; gap: 2px; }
.dpo-warn-item { color: var(--warning); font-size: 10px; }
.dpo-toggle {
  align-self: flex-start; padding: 2px 8px; border-radius: 4px; border: 1px solid var(--border);
  background: var(--bg-root); color: var(--text-secondary); font-size: 11px; cursor: pointer;
  &:hover { background: var(--bg-hover); }
}
.dpo-diff {
  display: flex; flex-direction: column; gap: 2px; max-height: 150px; overflow-y: auto;
  padding: 5px 8px; background: var(--bg-root); border-radius: 5px; border: 1px solid var(--border);
}
.dpo-diff-row { display: flex; align-items: center; gap: 5px; font-size: 10px; }
.dpo-diff-kind {
  font-weight: 700; padding: 1px 5px; border-radius: 3px; font-size: 9px;
  &.insert { color: var(--success); background: rgba(34,197,94,.12); }
  &.delete { color: var(--error); background: rgba(239,68,68,.12); }
  &.update { color: var(--text-secondary); background: var(--bg-active); }
  &.reorder { color: var(--warning); background: rgba(234,179,8,.12); }
}
.dpo-diff-id { color: var(--text-secondary); font-family: monospace; }
.dpo-diff-field { color: var(--text-faint); }
</style>
