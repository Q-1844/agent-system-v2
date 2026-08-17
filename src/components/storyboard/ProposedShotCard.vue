<template>
  <!-- AI-6C §21-25：单条 Proposal 操作的卡片视图，按 kind 呈现。
       INSERT=Ghost Shot（dashed/低 surface/label，§21）；UPDATE=compact diff+Review（§22）；
       DELETE=降视觉权重+label，确认前不真删（§23）；REORDER=insertion line（§24）；
       KEEP=极小标记不噪音（§25）。纯展示，apply 由 ProposalReviewBar 走 ChangeSet（§27）。 -->
  <div class="psc" :class="kind" :title="title" @click="$emit('toggle')">
    <!-- INSERT：Ghost Shot -->
    <template v-if="kind === 'insert'">
      <div class="psc-label">DIRECTOR · INSERT</div>
      <div class="psc-body">
        <div class="psc-shot-id">Proposed Shot {{ op.new_shot?.shot_id || '（待分配）' }}</div>
        <div class="psc-desc">{{ op.new_shot?.description || '（未填写意图）' }}</div>
        <div v-if="op.new_shot?.characters?.length" class="psc-chars">{{ op.new_shot.characters.join('、') }}</div>
        <div v-if="op.new_shot?.target_duration_s" class="psc-dur">约 {{ op.new_shot.target_duration_s }}s</div>
      </div>
      <div class="psc-actions">
        <button class="psc-btn primary" @click.stop="$emit('accept')">Accept</button>
        <button class="psc-btn" @click.stop="$emit('dismiss')">Dismiss</button>
      </div>
    </template>

    <!-- UPDATE：compact diff -->
    <template v-else-if="kind === 'update'">
      <div class="psc-label">DIRECTOR · UPDATE · {{ op.shot_id }}</div>
      <div class="psc-body">
        <div class="psc-diff">
          <span class="psc-before">{{ beforeText || '—' }}</span>
          <span class="psc-arrow">→</span>
          <span class="psc-after">{{ afterText || '—' }}</span>
        </div>
      </div>
      <div class="psc-actions">
        <button class="psc-btn" @click.stop="$emit('review')">Review</button>
      </div>
    </template>

    <!-- DELETE：降权 + label，不真删 -->
    <template v-else-if="kind === 'delete'">
      <div class="psc-label danger">DIRECTOR · PROPOSED DELETE · {{ op.shot_id }}</div>
      <div class="psc-body muted">确认前不会删除</div>
    </template>

    <!-- REORDER：insertion line -->
    <template v-else-if="kind === 'reorder'">
      <div class="psc-label">DIRECTOR · REORDER</div>
      <div class="psc-body">
        <div class="psc-order">新顺序：{{ (op.ordered_shot_ids || []).join(' → ') }}</div>
      </div>
    </template>

    <!-- KEEP：极小标记 -->
    <template v-else>
      <span class="psc-keep">KEEP</span>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { ChangeOp } from '@/api';
import { opKind } from './proposal';

const props = defineProps<{ op: ChangeOp; selected?: boolean }>();
defineEmits<{ (e: 'toggle'): void; (e: 'accept'): void; (e: 'dismiss'): void; (e: 'review'): void }>();

const kind = computed(() => opKind(props.op));
const title = computed(() => `Proposal · ${kind.value}`);

// UPDATE 的 before/after（description / characters / target_duration_s 任一 BeforeAfter）
const beforeText = computed(() => {
  const d = props.op.description;
  return d && typeof d === 'object' ? d.before ?? '' : '';
});
const afterText = computed(() => {
  const d = props.op.description;
  return d && typeof d === 'object' ? d.after ?? '' : '';
});
</script>

<style lang="scss" scoped>
.psc {
  border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg-surface); padding: 8px 10px;
  font-size: 12px; color: var(--text-secondary);
  cursor: pointer; transition: border-color .15s;
  &:hover { border-color: var(--text-muted); }
}
/* §21 INSERT：Ghost Shot，dashed + 低一级 surface */
.psc.insert {
  border-style: dashed; border-color: var(--border-hover);
  background: var(--bg-root);
}
.psc.delete { opacity: .62; }
.psc.reorder { border-style: dashed; }

.psc-label {
  font-size: 10px; font-weight: 700; letter-spacing: .5px;
  color: var(--text-muted); margin-bottom: 4px;
  &.danger { color: var(--error); }
}
.psc-body { display: flex; flex-direction: column; gap: 3px; }
.psc-shot-id { font-size: 11px; color: var(--text-faint); font-family: monospace; }
.psc-desc { font-size: 12px; color: var(--text-primary); }
.psc-chars { font-size: 10px; color: var(--text-muted); }
.psc-dur { font-size: 10px; color: var(--text-faint); }
.psc-diff { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.psc-before { color: var(--text-muted); text-decoration: line-through; }
.psc-arrow { color: var(--text-faint); }
.psc-after { color: var(--text-primary); }
.psc-order { font-size: 11px; color: var(--text-secondary); font-family: monospace; }
.muted { color: var(--text-muted); font-size: 11px; }

.psc-actions { display: flex; gap: 6px; margin-top: 6px; }
.psc-btn {
  padding: 3px 9px; border-radius: 5px; border: 1px solid var(--border);
  background: var(--bg-root); color: var(--text-secondary);
  font-size: 11px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
  &.primary { border-color: var(--accent); color: var(--accent); &:hover { background: var(--accent); color: var(--accent-text); } }
}
.psc-keep {
  font-size: 10px; color: var(--text-faint); padding: 1px 6px;
  border: 1px solid var(--border); border-radius: 3px;
}
</style>
