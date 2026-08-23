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

    <!-- 状态 B/C：Compact / Expanded Bar（§5：单一 command surface，输入为视觉中心）-->
    <template v-else>
      <!-- 拖拽 handle（与输入区域分离，§6：输入文本时不触发 drag）-->
      <span
        class="fab-handle"
        title="拖动"
        aria-label="拖动 Floating Agent Bar"
        @pointerdown="onDragStart"
      >⋮⋮</span>

      <!-- Agent + 上下文（§6：identity secondary；点击 → Switcher）。
           context 比 agent 更醒目（用户关心"对谁做"，而非 Agent 分类）。-->
      <button
        class="fab-ctx"
        :disabled="!store.agents.length"
        :title="ctxTooltip"
        @click.stop="onAgentClick"
      >
        <span class="fab-ctx-agent">{{ agentLabel }}</span>
        <span v-if="contextSuffix" class="fab-ctx-sep">·</span>
        <span v-if="contextSuffix" class="fab-ctx-scope">{{ contextSuffix }}</span>
        <span v-if="store.running" class="fab-running" :title="runningTitle" />
      </button>

      <!-- 输入（Enter 发送）——透明无框，与 bar 同一 surface（§4：消除 double input box）-->
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
import { useChangeSessionStore } from '@/store/changeSession';
import AgentSwitcher from './AgentSwitcher.vue';

const store = useAgentUiStore();
const csStore = useChangeSessionStore();
const router = useRouter();

const menuOpen = ref(false);
const switcherOpen = ref(false);

// collapsed 48×48；compact/expanded 360×48（§5）。bar 本身高度恒 48，conversation surface 单独展开。
const barW = computed(() => (store.uiState === 'collapsed' ? 48 : 360));
const barH = computed(() => 48);

const agentLabel = computed(() => store.activeAgent?.name || store.activeAgentId);
const agentTooltip = computed(() => store.activeAgent?.role || '切换 Agent');
const inputPlaceholder = computed(() => 'Ask anything…');
// AI-7C-H §4/§12：running 点的 tooltip 来自 durable 真相（变更会话阶段 / chat 在途），
// 不由 LLM 话术推导。
const runningTitle = computed(() => {
  // AI-7C-H §十一：正在停止来自后端 running+cancel_requested 持久真相
  if (csStore.executionStopping) return '正在停止当前任务…';
  if (csStore.executionActive) return `执行中：${csStore.execution?.stage || ''}`;
  if (csStore.isActive) return `整理修改方案：${csStore.session?.status || ''}`;
  return '回复中…';
});

// AI-6D.1 §6：identity secondary。agent 名首词缩写（"Producer Supervisor"→"Producer"），
// context 更醒目。tooltip 保留全名（不丢信息）。
const shortAgent = computed(() => {
  const n = store.activeAgent?.name || store.activeAgentId;
  return n.split(/\s+/)[0];   // Producer / Script / Character / Production / Shot / Continuity / Director / Editor
});
const contextSuffix = computed(() => {
  const ctx = store.currentContext;
  if (!ctx || ctx.type === 'project') return '';
  if (ctx.type === 'shot') return `Shot ${ctx.shotId}`;
  if (ctx.type === 'scene' || ctx.type === 'shots') return `Story ${ctx.sceneId}`;
  if (ctx.type === 'character') return ctx.name;
  return '';
});
const ctxTooltip = computed(() => {
  const a = store.activeAgent?.name || store.activeAgentId;
  return contextSuffix.value ? `${a} · ${contextSuffix.value}（点击切换 Agent）` : `${a}（点击切换 Agent）`;
});

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
/* AI-6D.1：单一 command surface——软阴影、输入无内框、identity 次要。 */
.fab {
  position: fixed;
  z-index: 110;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 6px 0 2px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.16);
  font-size: 12px;
  color: var(--text-primary);
  user-select: none;
  transition: width 0.16s ease, box-shadow 0.16s ease;
  touch-action: none;

  &.dragging { transition: none; box-shadow: 0 4px 14px rgba(0, 0, 0, 0.22); }
  &.collapsed { box-shadow: 0 1px 6px rgba(0, 0, 0, 0.12); }
}

.fab-collapsed {
  width: 48px; height: 48px;
  margin: 0; padding: 0;
  background: var(--bg-surface);
  border: none;
  border-radius: 12px;
  color: var(--text-secondary);
  font-size: 15px; font-weight: 700;
  cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}

.fab-handle {
  flex-shrink: 0;
  padding: 0 2px;
  color: var(--text-faint);
  cursor: grab;
  letter-spacing: -2px;
  user-select: none;
  touch-action: none;
  &:hover { color: var(--text-muted); }
  .fab.dragging & { cursor: grabbing; }
}

/* §6：identity secondary——agent 名 faint + 缩写；context 更醒目。整体可点切 Agent。 */
.fab-ctx {
  flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 4px;
  max-width: 130px;
  background: none; border: none;
  padding: 3px 6px;
  border-radius: 6px;
  cursor: pointer;
  &:hover:not(:disabled) { background: var(--bg-hover); }
  &:disabled { cursor: default; opacity: 0.6; }
}
.fab-ctx-agent {
  font-size: 11px; font-weight: 500;
  color: var(--text-faint);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.fab-ctx-sep { color: var(--text-faint); font-size: 11px; }
.fab-ctx-scope {
  font-size: 11px; font-weight: 500;
  color: var(--text-secondary);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.fab-running {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--success);
  animation: fab-pulse 1.4s ease-in-out infinite;
}
@keyframes fab-pulse { 0%,100%{opacity:0.4} 50%{opacity:1} }
@media (prefers-reduced-motion: reduce) { .fab-running { animation: none; opacity: 0.7; } }

/* §4：输入无内框无独立底色，与 bar 同一 surface，是视觉中心。仅 focus 时极淡 ring。 */
.fab-input {
  flex: 1; min-width: 0;
  background: transparent;
  border: none;
  border-radius: 6px;
  padding: 6px 4px;
  font-size: 12px;
  color: var(--text-primary);
  outline: none;
  user-select: text;
  &::placeholder { color: var(--text-faint); }
  &:focus { background: var(--bg-root); box-shadow: 0 0 0 1px var(--border-hover) inset; }
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
.fab-send { color: var(--text-secondary); }

.fab-menu {
  position: absolute;
  top: calc(100% + 6px); right: 0;
  min-width: 140px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.22);
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
