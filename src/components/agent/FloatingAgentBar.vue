<template>
  <!-- AI-6C §4/§5：viewport overlay，position:fixed，不占 document flow，不压缩 Storyboard。 -->
  <div
    class="fab"
    :class="[store.uiState, { dragging: store.isDragging }]"
    :style="{ left: store.posLeft + 'px', top: store.posTop + 'px', width: barW + 'px', height: barH + 'px' }"
  >
    <!-- 状态 A：Collapsed（§5）-->
    <button
      v-if="store.uiState === 'collapsed'"
      class="fab-collapsed"
      title="Ask AGNES"
      aria-label="Ask AGNES"
      @click="store.openBar()"
    >A</button>

    <!-- 状态 B/C：Compact / Expanded Bar（§5）-->
    <template v-else>
      <!-- 拖拽 handle（与输入区域分离，§6：输入文本时不触发 drag）-->
      <span
        class="fab-handle"
        title="拖动"
        aria-label="拖动 Floating Agent Bar"
        @pointerdown="onDragStart"
      >⋮⋮</span>

      <!-- Agent 名（点击 → Switcher，§10）-->
      <button class="fab-agent" :disabled="!store.agents.length" @click.stop="onAgentClick" :title="agentTooltip">
        <span class="fab-agent-name">{{ agentLabel }}</span>
        <span v-if="store.running" class="fab-running" :title="runningTitle" />
      </button>

      <!-- 输入（Enter 发送）-->
      <input
        v-model="store.draft"
        class="fab-input"
        :placeholder="inputPlaceholder"
        :disabled="store.sending"
        @keyup.enter="onSend"
        @pointerdown.stop
      />

      <!-- 发送箭头 -->
      <button
        class="fab-send"
        :disabled="store.sending || !store.draft.trim()"
        :title="store.sending ? '发送中…' : '发送（Enter）'"
        aria-label="发送"
        @click="onSend"
      >↑</button>

      <!-- 菜单（§7：Collapse / Reset position / Agent Center）-->
      <button class="fab-menu-btn" title="菜单" aria-label="Floating Agent 菜单" @click.stop="menuOpen = !menuOpen">⋯</button>
      <div v-if="menuOpen" class="fab-menu" @click.stop>
        <button class="fab-menu-item" @click="onCollapse">收起</button>
        <button class="fab-menu-item" @click="onResetPosition">重置位置</button>
        <button class="fab-menu-item" @click="onAgentCenter">Agent Center</button>
      </div>

      <!-- Agent Switcher popover（§10）-->
      <AgentSwitcher v-if="switcherOpen" @close="switcherOpen = false" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useAgentUiStore } from '@/store/agentUi';
import AgentSwitcher from './AgentSwitcher.vue';

const store = useAgentUiStore();
const router = useRouter();

const menuOpen = ref(false);
const switcherOpen = ref(false);

// collapsed 48×48；compact/expanded 360×48（§5）。bar 本身高度恒 48，conversation surface 单独展开。
const barW = computed(() => (store.uiState === 'collapsed' ? 48 : 360));
const barH = computed(() => 48);

const agentLabel = computed(() => store.activeAgent?.name || store.activeAgentId);
const agentTooltip = computed(() => store.activeAgent?.role || '切换 Agent');
const inputPlaceholder = computed(() => 'Ask anything…');
const runningTitle = computed(() => 'Agent 工作中');

// ---- 拖拽（Pointer Events，§6）----
const drag = { startX: 0, startY: 0, origLeft: 0, origTop: 0, active: false };

function onDragStart(e: PointerEvent) {
  if (e.button !== 0) return;                  // 仅左键
  drag.startX = e.clientX; drag.startY = e.clientY;
  drag.origLeft = store.posLeft; drag.origTop = store.posTop;
  drag.active = true;
  store.isDragging = true;
  window.addEventListener('pointermove', onDragMove);
  window.addEventListener('pointerup', onDragEnd);
  document.body.style.userSelect = 'none';     // §6 拖动期间禁止误选文本
  e.preventDefault();
}
function onDragMove(e: PointerEvent) {
  if (!drag.active) return;
  const dx = e.clientX - drag.startX;
  const dy = e.clientY - drag.startY;
  store.setPosition(drag.origLeft + dx, drag.origTop + dy, barW.value, barH.value);
}
function onDragEnd() {
  if (!drag.active) return;
  drag.active = false;
  store.isDragging = false;
  window.removeEventListener('pointermove', onDragMove);
  window.removeEventListener('pointerup', onDragEnd);
  document.body.style.userSelect = '';
  store.savePosition(barW.value, barH.value);
}

// ---- 位置生命周期 ----
function onResize() { store.clampToViewport(barW.value, barH.value); }
watch(() => store.uiState, () => { store.clampToViewport(barW.value, barH.value); });

function closePopovers() { menuOpen.value = false; switcherOpen.value = false; }

onMounted(() => {
  store.loadPosition(barW.value, barH.value);
  window.addEventListener('resize', onResize);
  window.addEventListener('click', closePopovers);
  void store.loadAgents();
});
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize);
  window.removeEventListener('click', closePopovers);
  window.removeEventListener('pointermove', onDragMove);
  window.removeEventListener('pointerup', onDragEnd);
});

// ---- 动作 ----
function onSend() {
  const t = store.draft;
  if (!t || !t.trim() || store.sending) return;
  store.send(t);
  store.draft = '';
}
function onAgentClick() { menuOpen.value = false; switcherOpen.value = !switcherOpen.value; }
function onCollapse() { menuOpen.value = false; store.collapse(); }
function onResetPosition() {
  menuOpen.value = false;
  store.resetPosition(barW.value, barH.value);
}
function onAgentCenter() { menuOpen.value = false; router.push('/agents'); }
</script>

<style lang="scss" scoped>
.fab {
  position: fixed;
  z-index: 110;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 8px 0 4px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.28);
  font-size: 12px;
  color: var(--text-primary);
  user-select: none;
  transition: width 0.16s ease, height 0.16s ease;
  touch-action: none;

  &.dragging { transition: none; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.36); }
}

.fab-collapsed {
  width: 48px; height: 48px;
  margin: 0; padding: 0;
  background: var(--bg-surface);
  border: none;
  border-radius: 10px;
  color: var(--text-primary);
  font-size: 16px; font-weight: 700;
  cursor: pointer;
  &:hover { background: var(--bg-hover); }
}

.fab-handle {
  flex-shrink: 0;
  padding: 0 4px;
  color: var(--text-faint);
  cursor: grab;
  letter-spacing: -2px;
  user-select: none;
  touch-action: none;
  &:hover { color: var(--text-muted); }
  .fab.dragging & { cursor: grabbing; }
}

.fab-agent {
  flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 4px;
  background: none; border: none;
  padding: 2px 6px;
  border-radius: 6px;
  color: var(--text-secondary);
  font-size: 12px; font-weight: 600;
  cursor: pointer;
  &:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); }
  &:disabled { cursor: default; opacity: 0.7; }
}
.fab-agent-name { white-space: nowrap; }
.fab-running {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--success);
  animation: fab-pulse 1.4s ease-in-out infinite;
}
@keyframes fab-pulse { 0%,100%{opacity:0.4} 50%{opacity:1} }
@media (prefers-reduced-motion: reduce) { .fab-running { animation: none; opacity: 0.7; } }

.fab-input {
  flex: 1; min-width: 0;
  background: var(--bg-root);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 12px;
  color: var(--text-primary);
  outline: none;
  user-select: text;
  &:focus { border-color: var(--border-hover); }
}

.fab-send, .fab-menu-btn {
  flex-shrink: 0;
  width: 26px; height: 26px;
  border-radius: 6px;
  border: none;
  background: none;
  color: var(--text-muted);
  font-size: 14px;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  &:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); }
  &:disabled { opacity: 0.4; cursor: default; }
}

.fab-menu {
  position: absolute;
  top: calc(100% + 6px); right: 0;
  min-width: 140px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.3);
  padding: 4px;
  display: flex; flex-direction: column;
  gap: 2px;
  z-index: 120;
}
.fab-menu-item {
  text-align: left;
  background: none; border: none;
  padding: 6px 10px;
  border-radius: 5px;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
</style>
