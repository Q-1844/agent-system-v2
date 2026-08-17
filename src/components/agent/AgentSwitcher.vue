<template>
  <!-- AI-6C §10：非常紧凑的 popover（非 8 张大卡）。数据来自 apiClient.listAgents()
       （store.agents 缓存），不硬编码第二份 Agent metadata。disabled agent 灰显。 -->
  <div class="as-popover" :style="posStyle" @click.stop>
    <div class="as-head">切换 Agent</div>
    <div class="as-list">
      <button
        v-for="a in store.agents"
        :key="a.id"
        class="as-item"
        :class="{ active: a.id === store.activeAgentId, disabled: !a.enabled }"
        :disabled="!a.enabled"
        :title="a.description"
        @click="onPick(a.id)"
      >
        <span class="as-name">{{ a.name }}</span>
        <span class="as-role">{{ a.role }}</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { useAgentUiStore } from '@/store/agentUi';

const store = useAgentUiStore();
const emit = defineEmits<{ (e: 'close'): void }>();

// 锚定 bar 左下方（bar position:fixed，故同样 fixed 定位）
const posStyle = computed(() => ({
  left: store.posLeft + 'px',
  top: (store.posTop + 48 + 8) + 'px',
}));

async function onPick(id: string) {
  await store.setAgent(id);
  emit('close');
}

onMounted(() => { void store.loadAgents(); });
</script>

<style lang="scss" scoped>
.as-popover {
  position: fixed;
  z-index: 120;
  width: 220px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.22);
  overflow: hidden;
}
.as-head {
  padding: 6px 10px;
  font-size: 10px; font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase; letter-spacing: .5px;
  border-bottom: 1px solid var(--border);
}
.as-list {
  max-height: 320px; overflow-y: auto;
  padding: 4px;
  display: flex; flex-direction: column; gap: 1px;
}
.as-item {
  display: flex; flex-direction: column; gap: 1px;
  text-align: left;
  background: none; border: none;
  padding: 6px 8px; border-radius: 5px;
  cursor: pointer;
  &:hover:not(:disabled) { background: var(--bg-hover); }
  &.active { background: var(--bg-active); }
  &.disabled { opacity: .4; cursor: not-allowed; }
}
.as-name { font-size: 12px; font-weight: 600; color: var(--text-primary); }
.as-role { font-size: 10px; color: var(--text-muted); }
</style>
