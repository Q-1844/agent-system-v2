<template>
  <!-- AI-6D.1 §5：Expanded Conversation = bar 的延伸（非 glued modal）。
       宽度对齐 bar、软阴影、无 dominant header；据 bar 位置向上/下展开。 -->
  <div
    class="acs"
    :class="expandDir"
    :style="surfaceStyle"
  >
    <!-- 极薄 meta 行：running + 关闭（agent identity 已在 bar 显示，不重复大标题） -->
    <div class="acs-meta">
      <span v-if="store.running" class="acs-running" :title="runningTitle" />
      <span class="acs-meta-label">{{ metaLabel }}</span>
      <button class="acs-close" title="收起会话（Esc）" aria-label="收起会话" @click="store.closeConversation">×</button>
    </div>

    <!-- context chips 行（§17：业务上下文成立时；含 X 清除） -->
    <div v-if="hasBusinessContext" class="acs-context">
      <AgentContextChips />
    </div>

    <!-- messages（user 右、assistant 左；status 徽标；无 role 文本标签，靠对齐+底色区分） -->
    <div ref="listEl" class="acs-messages">
      <div v-if="!store.messages.length" class="acs-empty">在下方输入开始对话。</div>
      <div
        v-for="(m, i) in store.messages"
        :key="i"
        class="acs-msg"
        :class="m.role"
      >
        <span class="acs-msg-content">{{ m.content }}</span>
        <span v-if="m.status" class="acs-msg-status" :class="'ms-' + m.status">{{ statusLabel(m.status) }}</span>
      </div>
    </div>

    <!-- 错误提示（软失败不阻塞） -->
    <div v-if="store.chatError" class="acs-err">{{ store.chatError }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import { useAgentUiStore } from '@/store/agentUi';
import AgentContextChips from './AgentContextChips.vue';

const store = useAgentUiStore();
const listEl = ref<HTMLElement | null>(null);

const vh = ref(typeof window === 'undefined' ? 900 : window.innerHeight);
function onResize() { vh.value = window.innerHeight; }

// AI-6D.1：贴近 bar（小 gap），让它读作 bar 的延伸而非悬浮 modal。
const GAP = 2;
const BAR_H = 48;

// §C：靠近顶部向下展开，靠近底部向上展开
const expandDir = computed<'up' | 'down'>(() =>
  store.posTop < vh.value / 2 ? 'down' : 'up',
);

const surfaceStyle = computed(() => {
  const left = store.posLeft;
  if (expandDir.value === 'down') {
    return { left: left + 'px', top: (store.posTop + BAR_H + GAP) + 'px' };
  }
  const bottomFromViewportBottom = vh.value - store.posTop + GAP;
  return { left: left + 'px', bottom: bottomFromViewportBottom + 'px' };
});

const agentLabel = computed(() => store.activeAgent?.name || store.activeAgentId);
const runningTitle = computed(() => `${agentLabel.value} · 工作中`);

// §6：meta 行极简——agent 短名 + context（faint），不重复 bar 的大标题。
const metaLabel = computed(() => {
  const a = agentLabel.value.split(/\s+/)[0];
  const ctx = store.currentContext;
  if (!ctx || ctx.type === 'project') return a;
  if (ctx.type === 'shot') return `${a} · ${ctx.shotId}`;
  if (ctx.type === 'scene' || ctx.type === 'shots') return `${a} · ${ctx.sceneId}`;
  if (ctx.type === 'character') return `${a} · ${ctx.name}`;
  return a;
});

// §17：业务上下文成立时才显示 chips 行（project scope 无 chips）
const hasBusinessContext = computed(() => {
  const ctx = store.currentContext;
  return !!ctx && ctx.type !== 'project';
});

function statusLabel(s: string) {
  if (s === 'not_configured') return 'AI 未配置';
  if (s === 'failed') return '失败';
  return s;
}

// 新消息自动滚到底
watch(() => store.messages.length, async () => {
  await nextTick();
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight;
}, { flush: 'post' });

onMounted(() => window.addEventListener('resize', onResize));
onBeforeUnmount(() => window.removeEventListener('resize', onResize));
</script>

<style lang="scss" scoped>
/* AI-6D.1：bar 的延伸——宽度对齐(360)、软阴影、贴边、无 dominant header。 */
.acs {
  position: fixed;
  z-index: 109;                       // 紧贴 bar(110) 下方
  width: 360px;                       // 与 bar 等宽（§5 continuity）
  max-height: 440px;
  display: flex; flex-direction: column;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.2);
  overflow: hidden;
  &.down { border-top-left-radius: 4px; border-top-right-radius: 4px; }   // 贴 bar 下沿，角更方
  &.up { border-bottom-left-radius: 4px; border-bottom-right-radius: 4px; }
}

.acs-meta {
  display: flex; align-items: center; gap: 6px;
  padding: 5px 8px 4px;
  flex-shrink: 0;
}
.acs-meta-label {
  flex: 1; min-width: 0;
  font-size: 10px; font-weight: 500;
  color: var(--text-faint);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  text-transform: none;
}
.acs-running {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--success);
  animation: acs-pulse 1.4s ease-in-out infinite;
  flex-shrink: 0;
}
@keyframes acs-pulse { 0%,100%{opacity:.4} 50%{opacity:1} }
@media (prefers-reduced-motion: reduce) { .acs-running { animation: none; opacity: .7; } }
.acs-close {
  background: none; border: none;
  color: var(--text-muted);
  font-size: 16px; line-height: 1;
  cursor: pointer; padding: 0 4px; border-radius: 4px;
  &:hover { color: var(--text-primary); background: var(--bg-hover); }
}

.acs-context {
  padding: 2px 8px 5px;
  flex-shrink: 0;
}

.acs-messages {
  flex: 1; min-height: 120px;
  overflow-y: auto;
  padding: 8px 10px 10px;
  display: flex; flex-direction: column; gap: 6px;
}
.acs-empty {
  color: var(--text-faint); font-size: 11px;
  text-align: center; padding: 20px 8px;
}
.acs-msg {
  display: flex; flex-direction: column; gap: 2px;
  max-width: 88%;
  padding: 6px 9px; border-radius: 9px;
  font-size: 12px; line-height: 1.5;
  word-break: break-word;
  &.user { align-self: flex-end; background: var(--bg-active); color: var(--text-primary); }
  &.assistant { align-self: flex-start; background: var(--bg-hover); color: var(--text-primary); }
}
.acs-msg-status { font-size: 10px; margin-top: 1px; }
.ms-not_configured { color: var(--warning); }
.ms-failed { color: var(--error); }

.acs-err {
  padding: 5px 10px;
  font-size: 11px; color: var(--error);
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}
</style>
