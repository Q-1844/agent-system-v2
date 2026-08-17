<template>
  <!-- AI-6C §5 状态 C：Expanded Conversation Surface。
       不改变 Floating Bar 本身位置；据 bar 当前 viewport 位置向上/向下展开（§C）。 -->
  <div
    class="acs"
    :class="expandDir"
    :style="surfaceStyle"
  >
    <!-- header：agent 名 + 关闭 -->
    <div class="acs-head">
      <span class="acs-title">{{ agentLabel }}</span>
      <span v-if="store.running" class="acs-running" :title="runningTitle" />
      <button class="acs-close" title="收起会话（Esc）" aria-label="收起会话" @click="store.closeConversation">×</button>
    </div>

    <!-- context 行（§17：context chips；业务上下文成立时显示） -->
    <div v-if="hasBusinessContext" class="acs-context">
      <AgentContextChips />
    </div>

    <!-- messages（§agents/index.vue 同款渲染：user 右、assistant 左、status 徽标） -->
    <div ref="listEl" class="acs-messages">
      <div v-if="!store.messages.length" class="acs-empty">暂无消息。在下方输入开始对话。</div>
      <div
        v-for="(m, i) in store.messages"
        :key="i"
        class="acs-msg"
        :class="m.role"
      >
        <span class="acs-msg-role">{{ m.role }}</span>
        <span class="acs-msg-content">{{ m.content }}</span>
        <span v-if="m.status" class="acs-msg-status" :class="'ms-' + m.status">{{ statusLabel(m.status) }}</span>
      </div>
    </div>

    <!-- 错误提示（软失败不阻塞，§agents/index.vue 同款） -->
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

const GAP = 8;
const BAR_H = 48;

// §C：靠近顶部向下展开，靠近底部向上展开
const expandDir = computed<'up' | 'down'>(() =>
  store.posTop < vh.value / 2 ? 'down' : 'up',
);

const surfaceStyle = computed(() => {
  const left = store.posLeft;
  if (expandDir.value === 'down') {
    // 顶部贴近 bar 下沿
    return { left: left + 'px', top: (store.posTop + BAR_H + GAP) + 'px' };
  }
  // 向上展开：用 bottom 让 surface 贴 bar 上沿
  const bottomFromViewportBottom = vh.value - store.posTop + GAP;
  return { left: left + 'px', bottom: bottomFromViewportBottom + 'px' };
});

const agentLabel = computed(() => store.activeAgent?.name || store.activeAgentId);
const runningTitle = computed(() => `${agentLabel.value} · 工作中`);

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
.acs {
  position: fixed;
  z-index: 109;                       // 紧贴 bar(110) 下方，不盖 bar
  width: 380px;                       // 与 bar 相同或略宽（§C）
  max-height: 480px;                  // 420–520px（§C）
  display: flex; flex-direction: column;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.32);
  overflow: hidden;
}

.acs-head {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.acs-title {
  flex: 1;
  font-size: 13px; font-weight: 600;
  color: var(--text-primary);
  text-transform: uppercase; letter-spacing: .4px;
}
.acs-running {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--success);
  animation: acs-pulse 1.4s ease-in-out infinite;
}
@keyframes acs-pulse { 0%,100%{opacity:.4} 50%{opacity:1} }
@media (prefers-reduced-motion: reduce) { .acs-running { animation: none; opacity: .7; } }
.acs-close {
  background: none; border: none;
  color: var(--text-muted);
  font-size: 18px; line-height: 1;
  cursor: pointer; padding: 0 4px; border-radius: 4px;
  &:hover { color: var(--text-primary); background: var(--bg-hover); }
}

.acs-context {
  padding: 4px 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.acs-context-label {
  font-size: 11px; color: var(--text-muted);
}

.acs-messages {
  flex: 1; min-height: 120px;
  overflow-y: auto;
  padding: 10px 12px;
  display: flex; flex-direction: column; gap: 8px;
}
.acs-empty {
  color: var(--text-muted); font-size: 12px;
  text-align: center; padding: 24px 8px;
}
.acs-msg {
  display: flex; flex-direction: column; gap: 2px;
  max-width: 85%;
  padding: 7px 10px; border-radius: 8px;
  font-size: 12px; line-height: 1.5;
  word-break: break-word;
  &.user { align-self: flex-end; background: var(--bg-active); color: var(--text-primary); }
  &.assistant { align-self: flex-start; background: var(--bg-hover); color: var(--text-primary); }
}
.acs-msg-role { font-size: 10px; opacity: .55; }
.acs-msg-status { font-size: 10px; margin-top: 2px; }
.ms-not_configured { color: var(--warning); }
.ms-failed { color: var(--error); }

.acs-err {
  padding: 6px 12px;
  font-size: 11px; color: var(--error);
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}
</style>
