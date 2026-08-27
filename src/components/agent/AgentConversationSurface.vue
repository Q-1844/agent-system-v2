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

    <!-- AI-7C-H §12：变更会话运行卡——durable 运行时真相（Layer B），非 LLM 话术。
         状态来自后端持久化 ChangeSession/ChangeExecution（changeSession store 轮询）；
         无伪造百分比，只显示有确定分母的真实计数。引用真实 session id。 -->
    <div v-if="cs.session" class="acs-runcard" :class="'rc-' + runPhase">
      <div class="rc-head">
        <span class="rc-dot" />
        <span class="rc-stage">{{ runStageLabel }}</span>
        <span class="rc-sid">{{ cs.session.session_id }}</span>
      </div>
      <!-- §15：活跃执行 + 新变更请求的确定性二选一 -->
      <div v-if="store.pendingChangeChoice" class="rc-choice">
        <div class="rc-choice-q">当前计划正在执行，新的修改请求如何处理？</div>
        <div class="rc-choice-opts">
          <button class="rc-btn" @click="store.resolveChangeChoice('queue')">让当前任务完成后处理</button>
          <button class="rc-btn rc-btn-warn" @click="store.resolveChangeChoice('restart')">停止当前任务并重新规划</button>
        </div>
      </div>
      <!-- 有确定分母的真实计数（§28） -->
      <div v-if="execCounts" class="rc-counts">{{ execCounts }}</div>
      <!-- blocker：真实失败边界 + 可行动作 -->
      <div v-if="cs.executionBlocked" class="rc-blocker">
        受阻：{{ cs.execution?.blocker?.message || cs.execution?.blocker?.code }}
      </div>
      <div class="rc-actions">
        <button class="rc-link" @click="openAgentCenter">
          {{ cs.needsDecision ? '去确认创作决策' : cs.readyForReview ? '去审查修改方案' : '在 AI 团队中查看' }}
        </button>
      </div>
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
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useAgentUiStore } from '@/store/agentUi';
import { useChangeSessionStore } from '@/store/changeSession';
import { useStickToBottom } from '@/composables/useStickToBottom';
import AgentContextChips from './AgentContextChips.vue';

const store = useAgentUiStore();
const cs = useChangeSessionStore();
const router = useRouter();
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

// ============ AI-7C-H §12：运行卡（durable 真相 → 用户可读阶段）============
// proposal 阶段标签映射（§12 建议）；execution 阶段沿用 ChangePlanReview 的 STAGE_LABELS 词表。
const SESSION_STAGE_LABELS: Record<string, string> = {
  routing: '理解你的要求', consulting: '协调相关专业 Agent',
  analyzing_dependencies: '分析影响', awaiting_user_decision: '需要你确认',
  synthesizing_plan: '整理修改方案', validating_plan: '校验方案',
  building_preview: '准备预览', ready_for_review: '等待你确认',
  approved_for_execution: '方案已批准', rejected: '已拒绝', stale: '已过期',
  failed: '失败', cancelled: '已取消', blocked: '受阻',
};
const EXEC_STAGE_LABELS: Record<string, string> = {
  revalidate: '重验批准', workspace_create: '建执行工作区', creative_replay: '暂存创作变更',
  workspace_verify: '校验预览一致', canonical_commit: '原子提交创作变更',
  character_sync: '同步角色参考图', production_planning: '准备制作计划',
  production_prepare: '冻结制作输入', production: '生成受影响素材',
  compose: '合成成片', done: '完成',
};
const runPhase = computed<'proposal' | 'execution' | 'terminal'>(() => {
  if (cs.execution) {
    if (cs.executionDone) return 'terminal';
    return 'execution';
  }
  if (cs.approved) return 'terminal';
  return 'proposal';
});
const runStageLabel = computed(() => {
  if (cs.execution) {
    // AI-7C-H §十一：执行中 / 正在停止… / 已取消 三态来自后端持久真相（非 LLM 文本）
    if (cs.executionStopping) return '正在停止…';
    if (cs.execution?.status === 'cancelled') return '已取消';
    if (cs.execution?.status === 'completed') return '已完成';
    if (cs.executionBlocked) return '执行受阻';
    return EXEC_STAGE_LABELS[cs.execution.stage] || cs.execution.stage;
  }
  if (cs.approved) return '已批准，等待执行';
  return SESSION_STAGE_LABELS[cs.session?.status || ''] || cs.session?.status || '';
});
// §28：只显示有确定分母的真实计数（角色参考图 N/M、制作计划 N/M 来自 manifest 分母）
const execCounts = computed(() => {
  const ex = cs.execution;
  if (!ex) return '';
  const parts: string[] = [];
  const affectedChars = cs.manifest?.affected_character_ids?.length ?? 0;
  if (affectedChars > 0) {
    parts.push(`角色参考图 ${(ex.character_sync || []).length}/${affectedChars}`);
  }
  const plannerScenes = cs.manifest?.planner_scene_ids?.length ?? 0;
  if (plannerScenes > 0) {
    parts.push(`制作计划 ${(ex.planning || []).length}/${plannerScenes}`);
  }
  return parts.join(' · ');
});
function openAgentCenter() { router.push('/agents'); }

function statusLabel(s: string) {
  if (s === 'not_configured') return 'AI 未配置';
  if (s === 'failed') return '失败';
  return s;
}

// 智能贴底：打开会话跳最新；新消息时仅当用户原本贴底才跟随（查看历史时不强拉）。
const stick = useStickToBottom(listEl);
onMounted(() => {
  window.addEventListener('resize', onResize);
  stick.bind();
  if (store.conversationOpen) stick.pinToBottom();
});
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize);
  stick.unbind();
});
// 展开会话（含切换/载入历史消息）→ 定位最新。
watch(() => store.conversationOpen, (open) => { if (open) stick.pinToBottom(); });
// 新消息到达 → 贴底才跟随，不打断用户查看历史。
watch(() => store.messages.length, () => { stick.followIfStuck(); });
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

/* AI-7C-H §12 运行卡：紧凑、结构化、无日志倾倒 */
.acs-runcard {
  margin: 0 8px 6px;
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-root);
  display: flex;
  flex-direction: column;
  gap: 5px;
  flex-shrink: 0;
}
.rc-head { display: flex; align-items: center; gap: 6px; min-width: 0; }
.rc-dot {
  width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
  background: var(--success);
  animation: acs-pulse 1.4s ease-in-out infinite;
}
.rc-execution .rc-dot { background: #60a5fa; }
.rc-terminal .rc-dot { background: var(--text-faint); animation: none; }
.rc-stage { font-size: 11px; font-weight: 600; color: var(--text-secondary); white-space: nowrap; }
.rc-sid {
  margin-left: auto; font-size: 10px; color: var(--text-faint);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.rc-counts { font-size: 11px; color: var(--text-secondary); }
.rc-blocker { font-size: 11px; color: #f87171; }
.rc-actions { display: flex; justify-content: flex-end; }
.rc-link {
  background: none; border: none; padding: 2px 4px;
  font-size: 11px; color: var(--accent, #60a5fa); cursor: pointer;
  &:hover { text-decoration: underline; }
}
.rc-choice { display: flex; flex-direction: column; gap: 5px; }
.rc-choice-q { font-size: 11px; color: var(--text-primary); }
.rc-choice-opts { display: flex; gap: 6px; flex-wrap: wrap; }
.rc-btn {
  padding: 4px 8px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--bg-surface); color: var(--text-secondary);
  font-size: 11px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
.rc-btn-warn { border-color: rgba(248, 113, 113, 0.4); color: #f87171; }

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
/* 复用测试弹窗的正文渲染：保留换行、长词折行；不引入 v-html/markdown 解析，不改消息正文。 */
.acs-msg-content { white-space: pre-wrap; word-break: break-word; }
.ms-not_configured { color: var(--warning); }
.ms-failed { color: var(--error); }

.acs-err {
  padding: 5px 10px;
  font-size: 11px; color: var(--error);
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}
</style>
