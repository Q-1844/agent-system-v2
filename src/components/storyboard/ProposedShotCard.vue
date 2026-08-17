<template>
  <!-- AI-6D.1 §10：语义 item 卡——headline 人类可读（Change/Add/Remove/Move + shot_id），
       动作区分用极细左边框色（不喧宾夺主），选中态极明显（accent 边框 + 实心 check + 提亮底）。
       不暴露 item_id / ChangeOperation 机制。点击整卡 toggle 选中。 -->
  <div class="psc" :class="[kind, { sel: selected }]" @click="$emit('toggle')">
    <div class="psc-check" :class="{ on: selected }" aria-hidden="true">{{ selected ? '✓' : '' }}</div>
    <div class="psc-main">
      <div class="psc-headline">{{ headline }}</div>

      <!-- INSERT：新镜头内容 -->
      <div v-if="kind === 'insert'" class="psc-detail">
        <div v-if="item.description" class="psc-line">{{ item.description }}</div>
        <div class="psc-meta">
          <span v-if="item.characters?.length">{{ item.characters.join('、') }}</span>
          <span v-if="item.target_duration_s">· 约 {{ item.target_duration_s }}s</span>
        </div>
      </div>

      <!-- UPDATE：变更的新值（before→after 在 changeset diff 里看） -->
      <div v-else-if="kind === 'update'" class="psc-detail">
        <div v-if="item.description" class="psc-line"><span class="psc-k">新画面</span>{{ item.description }}</div>
        <div v-if="item.characters?.length" class="psc-meta"><span class="psc-k">角色</span>{{ item.characters.join('、') }}</div>
        <div v-if="item.target_duration_s != null" class="psc-meta"><span class="psc-k">时长</span>→ {{ item.target_duration_s }}s</div>
      </div>

      <!-- DELETE：温和提示，不真删 -->
      <div v-else-if="kind === 'delete'" class="psc-detail">
        <div class="psc-muted">应用前不会删除。</div>
      </div>

      <!-- REORDER：headline 已说明，无额外 detail -->
      <div v-if="item.reason" class="psc-reason">{{ item.reason }}</div>
    </div>
    <button v-if="kind === 'update'" class="psc-diff" title="查看 diff" @click.stop="$emit('review')">Diff</button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { DirectorProposalItem } from './proposal';
import { itemKind } from './proposal';

const props = defineProps<{ item: DirectorProposalItem; selected?: boolean }>();
defineEmits<{ (e: 'toggle'): void; (e: 'review'): void }>();

const kind = computed(() => itemKind(props.item));

// §10：人类可读主语。shot_id 是用户在 Storyboard 里看到的稳定引用，直接用。
const headline = computed(() => {
  const it = props.item;
  switch (it.action) {
    case 'update_shot':  return `Change ${it.shot_id}`;
    case 'insert_shot':  return it.after_shot_id ? `Add a shot after ${it.after_shot_id}` : 'Add a shot at the end';
    case 'delete_shot':  return `Remove ${it.shot_id}`;
    case 'reorder_shot': return it.after_shot_id ? `Move ${it.shot_id} after ${it.after_shot_id}` : `Move ${it.shot_id} to start`;
    default:             return it.shot_id || 'Change';
  }
});
</script>

<style lang="scss" scoped>
.psc {
  display: flex; align-items: flex-start; gap: 10px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-left: 3px solid var(--text-faint);   /* 极细左边框 = 动作区分色 */
  border-radius: 7px;
  padding: 9px 11px;
  cursor: pointer;
  transition: border-color .14s, background .14s;
  &:hover { border-color: var(--border-hover); }
}
/* 动作色（克制，仅左边框） */
.psc.insert  { border-left-color: var(--success); }
.psc.update  { border-left-color: var(--text-muted); }
.psc.delete  { border-left-color: var(--error); }
.psc.reorder { border-left-color: var(--warning); }

/* §10：选中态极明显 */
.psc.sel {
  border-color: var(--accent);
  border-left-color: var(--accent);
  background: var(--bg-active);
}

.psc-check {
  flex-shrink: 0;
  width: 16px; height: 16px; margin-top: 1px;
  border: 1px solid var(--border-hover);
  border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700;
  color: transparent;
  background: transparent;
  transition: background .14s, color .14s, border-color .14s;
  &.on { background: var(--accent); color: var(--accent-text); border-color: var(--accent); }
}

.psc-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.psc-headline { font-size: 12px; font-weight: 600; color: var(--text-primary); }
.psc-detail { display: flex; flex-direction: column; gap: 2px; }
.psc-line { font-size: 12px; color: var(--text-secondary); line-height: 1.4; }
.psc-meta { font-size: 10px; color: var(--text-muted); display: flex; gap: 6px; flex-wrap: wrap; }
.psc-k { color: var(--text-faint); margin-right: 4px; }
.psc-muted { font-size: 11px; color: var(--text-muted); }
.psc-reason { font-size: 10px; color: var(--text-faint); font-style: italic; margin-top: 2px; }

.psc-diff {
  flex-shrink: 0;
  padding: 2px 8px; border-radius: 4px; border: 1px solid var(--border);
  background: var(--bg-root); color: var(--text-muted);
  font-size: 10px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
</style>
