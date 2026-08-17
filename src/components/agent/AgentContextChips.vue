<template>
  <!-- AI-6C §17：极简 context chips（避免巨大 capsule）。仅在业务上下文成立时显示。
       X 清除（回退项目级，不伪造）。hover 说明 Agent 将以此作为上下文（§17）。 -->
  <div v-if="chips.length" class="acc" :title="title">
    <span v-for="(c, i) in chips" :key="i" class="acc-chip">{{ c }}</span>
    <button class="acc-x" title="清除上下文" aria-label="清除上下文" @click="store.clearContext">×</button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useAgentUiStore } from '@/store/agentUi';

const store = useAgentUiStore();

const chips = computed<string[]>(() => {
  const ctx = store.currentContext;
  if (!ctx) return [];
  switch (ctx.type) {
    case 'project': return [];
    case 'scene': return [`Story ${ctx.sceneId}`];
    case 'shot': return [`Story ${ctx.sceneId}`, `Shot ${ctx.shotId}`];
    case 'shots': return [`Story ${ctx.sceneId}`, `${ctx.shotIds.length} 镜`];
    case 'character': return [`角色 ${ctx.name}`];
    default: return [];
  }
});

const title = computed(() => {
  const a = store.activeAgent?.name || store.activeAgentId;
  return store.contextAttachedToWorkspace
    ? `${a} 将以此作为上下文`
    : `来自 Storyboard 的历史会话上下文`;
});
</script>

<style lang="scss" scoped>
.acc {
  display: inline-flex; align-items: center; gap: 3px; flex-wrap: wrap;
}
/* AI-6D.1：极安静的内联 context——无独立 border，不读作 toolbar row。 */
.acc-chip {
  display: inline-flex; align-items: center;
  padding: 1px 6px;
  border: none;
  border-radius: 4px;
  font-size: 10px; color: var(--text-muted);
  background: transparent;
}
.acc-x {
  background: none; border: none;
  color: var(--text-faint);
  font-size: 12px; line-height: 1;
  cursor: pointer; padding: 0 2px; border-radius: 3px;
  &:hover { color: var(--text-primary); background: var(--bg-hover); }
}
</style>
