<template>
  <div class="proom">
    <!-- 顶部：workflow 概要 + roster 开关 -->
    <div class="proom-head">
      <div class="ph-title">
        <span class="ph-emoji">🎛️</span>
        <span class="ph-name">Production Room</span>
        <span class="ph-sub">Producer 统一入口 · 专业 Agent 独立 Direct Chat 仍在左侧列表</span>
      </div>
      <div v-if="room.workflow" class="ph-wf" :title="room.workflow.goal">
        <span class="wf-dot" :class="room.workflow.status"></span>
        Workflow · {{ workflowStatusText }}
      </div>
      <button class="ph-roster-toggle" @click="rosterOpen = !rosterOpen">
        {{ rosterOpen ? '收起成员' : '团队成员' }} ▾
      </button>
    </div>

    <!-- Agent roster / team status（状态来自投影，不伪造） -->
    <div v-if="rosterOpen" class="proom-roster">
      <div v-for="p in room.participants" :key="p.agent_id" class="roster-item"
           :title="rosterTitle(p)">
        <span class="r-ava" :style="{ background: identityOf(p.agent_id).color }">{{ identityOf(p.agent_id).emoji }}</span>
        <span class="r-info">
          <span class="r-name">{{ identityOf(p.agent_id).name }}</span>
          <span class="r-role">{{ identityOf(p.agent_id).role }}</span>
        </span>
        <span class="r-status" :class="'s-' + p.status">
          <span v-if="p.status === 'working'" class="r-pulse"></span>{{ statusLabel(p.status, p.not_called) }}
        </span>
      </div>
    </div>

    <!-- Phase55 E：协作模式选择与 run 状态（运行级配置入口；状态来自真实 run 账本） -->
    <CollabRunPanel :project="projectName" />

    <!-- 消息时间线 -->
    <div class="proom-messages" ref="messagesEl">
      <div v-if="!room.timeline.length && !room.loading" class="pr-empty">
        <div class="pr-empty-emoji">🎬</div>
        <div>向 Producer 描述你的项目级目标，它会协调专业 Agent 完成。</div>
        <div class="pr-empty-sub">例如：「检查后半段剧情节奏，把真正有问题的地方处理好，并同步受影响的分镜。角色外貌不要改。」</div>
      </div>

      <template v-for="m in room.timeline" :key="m.id">
        <!-- 用户消息 -->
        <div v-if="m.kind === 'user'" class="pr-row user">
          <div class="pr-bubble user">
            <div class="pr-content">{{ m.content }}</div>
          </div>
          <span class="pr-ava" :style="{ background: identityOf('user').color }">👤</span>
        </div>

        <!-- Producer 公开消息（真实输出，不人为生成） -->
        <div v-else-if="m.kind === 'producer'" class="pr-row agent">
          <span class="pr-ava" :style="{ background: identityOf('producer').color }">🎛️</span>
          <div class="pr-bubble agent">
            <div class="pr-meta">
              <span class="pr-speaker">Producer</span>
              <span v-if="m.status && m.status !== 'completed'" class="pr-mstatus" :class="'ms-' + m.status">
                {{ producerStatusText(m.status) }}
              </span>
            </div>
            <div class="pr-content">{{ m.content }}</div>
          </div>
        </div>

        <!-- 委派卡：Producer → Specialist -->
        <div v-else-if="m.kind === 'delegation'" class="pr-row delegation">
          <div class="pr-card delegation" :class="{ live: m.live }">
            <div class="pc-head">
              <span class="pc-route">
                <span class="pc-ava" :style="{ background: identityOf('producer').color }">🎛️</span>
                <span class="pc-arrow">→</span>
                <span class="pc-ava" :style="{ background: identityOf(m.recipient).color }">{{ identityOf(m.recipient).emoji }}</span>
                <span class="pc-names">Producer → {{ identityOf(m.recipient).name }}</span>
              </span>
              <span class="pc-status" :class="'s-' + m.status">
                <span v-if="m.live" class="r-pulse"></span>{{ delegationStatusText(m.status) }}
              </span>
            </div>
            <div class="pc-objective">{{ m.content }}</div>
            <!-- 执行详情： Specialist 工具事件（仅 live；完成后由 Agent 各自 Direct Chat 承载完整时间线） -->
            <div v-if="liveItemsOf(m).length" class="pc-live">
              <div class="pc-live-head" @click="toggleLive(m.task_id || '')">
                <span class="chevron" :class="{ open: liveOpen[m.task_id || ''] }">▸</span>
                执行详情 · {{ liveDoneCount(m) }}/{{ liveItemsOf(m).length }}
              </div>
              <div v-if="liveOpen[m.task_id || '']" class="pc-live-list">
                <div v-for="(it, pi) in liveItemsOf(m)" :key="pi" class="pc-live-item">
                  <span class="pi-mark" :class="it.status">{{ it.status === 'done' ? '✓' : it.status === 'failed' ? '!' : '◌' }}</span>
                  <span class="pl-label">{{ it.label }}</span>
                  <span v-if="it.summary" class="pl-summary">{{ it.summary }}</span>
                </div>
              </div>
            </div>
            <div class="pc-foot">
              <button class="pc-detail-btn" @click="toggleDetail(m.id)">
                {{ detailOpen[m.id] ? '收起任务详情' : '任务详情' }}
              </button>
            </div>
            <div v-if="detailOpen[m.id]" class="pc-detail">
              <div v-if="m.situation" class="pcd-row"><span class="pcd-k">背景情况</span><span class="pcd-v">{{ m.situation }}</span></div>
              <div v-if="m.constraints?.length" class="pcd-row"><span class="pcd-k">约束</span>
                <span class="pcd-v"><span v-for="(c, ci) in m.constraints" :key="ci" class="pcd-c">· {{ c }}</span></span></div>
              <div v-if="scopeText(m)" class="pcd-row"><span class="pcd-k">作用范围</span><span class="pcd-v">{{ scopeText(m) }}</span></div>
              <div class="pcd-row"><span class="pcd-k">task_id</span><span class="pcd-v mono">{{ m.task_id }}</span></div>
              <div v-if="m.workflow_id" class="pcd-row"><span class="pcd-k">workflow</span><span class="pcd-v mono">{{ m.workflow_id }}</span></div>
              <div v-if="m.parent_task_id" class="pcd-row"><span class="pcd-k">上游任务</span><span class="pcd-v mono">{{ m.parent_task_id }}</span></div>
            </div>
          </div>
        </div>

        <!-- 结果卡：Specialist → Producer -->
        <div v-else-if="m.kind === 'specialist_result'" class="pr-row delegation">
          <div class="pr-card result" :class="resultTone(m)">
            <div class="pc-head">
              <span class="pc-route">
                <span class="pc-ava" :style="{ background: identityOf(m.sender).color }">{{ identityOf(m.sender).emoji }}</span>
                <span class="pc-arrow">→</span>
                <span class="pc-ava" :style="{ background: identityOf('producer').color }">🎛️</span>
                <span class="pc-names">{{ identityOf(m.sender).name }} → Producer</span>
              </span>
              <span class="pc-status" :class="'s-' + m.status">{{ delegationStatusText(m.status) }}</span>
            </div>
            <div class="pc-objective">{{ m.content }}</div>
            <!-- 事实徽标 footer（轻量，来自 receipts 派生的真实数据） -->
            <div class="pc-badges">
              <template v-if="m.status === 'completed'">
                <span v-if="m.verified" class="badge ok">✓ Verified</span>
                <span v-else class="badge neutral">○ 无 verified canonical 修改</span>
                <span v-if="changedRefsText(m.changed_refs)" class="badge refs">{{ changedRefsText(m.changed_refs) }}</span>
                <span v-if="m.n_tool_calls != null" class="badge tools">{{ m.n_tool_calls }} 次工具调用</span>
              </template>
              <template v-else-if="m.status === 'stopped'">
                <span class="badge stopped">■ 已停止（已完成的修改保留）</span>
              </template>
              <template v-else-if="m.status === 'not_configured'">
                <span class="badge warn">○ 未配置模型，未能执行</span>
              </template>
              <template v-else>
                <span class="badge fail">⚠ 失败</span>
              </template>
            </div>
            <div v-if="m.status === 'failed' && m.warnings?.length" class="pc-warn">
              <div v-for="(w, wi) in m.warnings.slice(0, 3)" :key="wi" class="pc-warn-item">⚠ {{ w }}</div>
            </div>
            <div class="pc-foot">
              <button v-if="m.full_reply" class="pc-detail-btn" @click="toggleDetail(m.id)">
                {{ detailOpen[m.id] ? '收起完整回复' : '查看完整回复' }}
              </button>
              <button class="pc-detail-btn" @click="toggleDebug(m.id)">
                {{ debugOpen[m.id] ? '收起调试信息' : '调试信息' }}
              </button>
            </div>
            <div v-if="detailOpen[m.id] && m.full_reply" class="pc-full" :class="{ clipped: m.full_reply_truncated }">
              {{ m.full_reply }}<span v-if="m.full_reply_truncated" class="pc-full-more">…（后端截断，完整内容见该 Agent 的 Direct Chat）</span>
            </div>
            <div v-if="debugOpen[m.id]" class="pc-detail">
              <div class="pcd-row"><span class="pcd-k">task_id</span><span class="pcd-v mono">{{ m.task_id }}</span></div>
              <div v-if="m.workflow_id" class="pcd-row"><span class="pcd-k">workflow</span><span class="pcd-v mono">{{ m.workflow_id }}</span></div>
              <div v-if="m.parent_task_id" class="pcd-row"><span class="pcd-k">上游任务</span><span class="pcd-v mono">{{ m.parent_task_id }}</span></div>
              <div v-if="m.execution_ref" class="pcd-row"><span class="pcd-k">execution</span><span class="pcd-v mono">{{ m.execution_ref }}</span></div>
              <div v-if="m.session_id" class="pcd-row"><span class="pcd-k">Agent session</span><span class="pcd-v mono">{{ m.session_id }}</span></div>
              <div v-if="m.task_created_at" class="pcd-row"><span class="pcd-k">时间</span><span class="pcd-v mono">{{ fmtTime(m.task_created_at) }} → {{ fmtTime(m.task_updated_at) }}</span></div>
            </div>
          </div>
        </div>
      </template>

      <!-- Producer 实时工作区（流式）：activity + streaming text + stop -->
      <div v-if="showStreaming" class="pr-row agent">
        <span class="pr-ava" :style="{ background: identityOf('producer').color }">🎛️</span>
        <div class="pr-bubble agent streaming">
          <div class="pr-meta">
            <span class="pr-speaker">Producer</span>
            <span class="pr-live-chip"><span class="r-pulse"></span>工作中</span>
            <button class="stop-btn" @click="onStop">■ 停止</button>
          </div>
          <div v-if="activeAgentName" class="pr-activity">
            <span class="spinner">◌</span>{{ activeAgentName }}
          </div>
          <div v-if="turn.streamingText" class="pr-content">{{ turn.streamingText }}</div>
        </div>
      </div>
      <div v-if="room.error || roomError" class="pr-error">{{ roomError || room.error }}</div>
    </div>

    <!-- 输入区：默认发 Producer（§二十九：不做 @Agent） -->
    <div class="proom-composer">
      <label class="thinking-toggle" :class="{ on: thinkingToggle }"
             :title="thinkingToggle ? '思考已开启（本次发送起生效）' : '思考已关闭'">
        <input type="checkbox" v-model="thinkingToggle" class="tt-sr-only" role="switch"
               :aria-checked="thinkingToggle" aria-label="思考模式" />
        <span class="tt-label">思考</span>
        <span class="tt-track"><span class="tt-thumb"></span></span>
      </label>
      <input v-model="draft" class="input" :placeholder="composerPlaceholder"
             @keyup.enter="send" :disabled="composerDisabled" />
      <button class="btn btn-primary" :disabled="composerDisabled || !draft.trim()" @click="send">
        {{ busy ? '…' : '发送' }}
      </button>
    </div>
    <div v-if="busy" class="pr-hint">◌ Producer 正在后台协调，可切换到其它模块，完成后这里会自动更新</div>
    <div v-else-if="anyLocked" class="pr-hint">🔒 委派的专业 Agent 正在修改项目（{{ lockedNames }}），输入暂时锁定</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import type { RoomMessage, RoomParticipant } from '@/api';
import { useProjectStore } from '@/store';
import { useAgentTurnStore } from '@/store/agentTurn';
import { useProjectEventsStore } from '@/store/projectEvents';
import { useProductionRoomStore } from '@/store/productionRoom';
import { useStickToBottom } from '@/composables/useStickToBottom';
import { identityOf, statusLabel, changedRefsText } from './roomIdentity';
import type { ProcessItem } from '@/store/agentTurn';
import CollabRunPanel from './CollabRunPanel.vue';

const store = useProjectStore();
const projectName = computed(() => store.projectName);
const turn = useAgentTurnStore();
const pev = useProjectEventsStore();
const room = useProductionRoomStore();

const draft = ref('');
const thinkingToggle = ref(false);
const rosterOpen = ref(true);
const detailOpen = ref<Record<string, boolean>>({});
const debugOpen = ref<Record<string, boolean>>({});
const liveOpen = ref<Record<string, boolean>>({});
const roomError = ref('');

const messagesEl = ref<HTMLElement | null>(null);
const stick = useStickToBottom(messagesEl);
watch(messagesEl, (el) => { if (el) { stick.bind(); stick.pinToBottom(); } });
// 新消息：仅当用户本来贴底才跟随；打开/刷新时 pinToBottom
watch(() => room.timeline.length, () => { stick.followIfStuck(); });

const busy = computed(() => turn.running && turn.activeSessionId === room.sessionId);
const composerDisabled = computed(() => busy.value || anyLocked.value || !projectName.value);
const anyLocked = computed(() => pev.storyLocked || pev.charactersLocked || pev.storyboardLocked);
const lockedNames = computed(() => {
  const names: string[] = [];
  const by = (r?: { by?: string }) => identityOf(r?.by).name || r?.by || '';
  if (pev.lockedResources['story']) names.push(`剧本 · ${by(pev.lockedResources['story'])}`);
  if (pev.lockedResources['character']) names.push(`角色 · ${by(pev.lockedResources['character'])}`);
  if (pev.lockedResources['storyboard']) names.push(`分镜 · ${by(pev.lockedResources['storyboard'])}`);
  return names.join('，');
});
const composerPlaceholder = computed(() =>
  anyLocked.value ? '🔒 专业 Agent 正在工作，请稍候…'
  : busy.value ? 'Producer 正在协调…'
  : '向 Producer 下达项目级目标…');

const showStreaming = computed(() =>
  turn.running && turn.activeSessionId === room.sessionId
  && turn.activeProject === projectName.value);

/** 当前活跃委派的活动文案（真实 SSE 事件，不含私有思维链） */
const activeAgentName = computed(() => {
  if (turn.currentActivity) return turn.currentActivity.label;
  return null;
});

const workflowStatusText = computed(() => {
  const s = room.workflow?.status;
  return s === 'running' ? '进行中' : s === 'completed' ? '已完成'
    : s === 'failed' ? '失败' : s === 'stopped' ? '已停止' : (s || '');
});

function rosterTitle(p: RoomParticipant): string {
  if (p.summary) return p.summary;
  if (p.status === 'idle') return '当前 workflow 未调用该 Agent';
  return `${identityOf(p.agent_id).name} · ${statusLabel(p.status, p.not_called)}`;
}
function producerStatusText(s: string): string {
  return s === 'failed' ? '执行失败' : s === 'stopped' ? '已停止'
    : s === 'not_configured' ? '未配置模型' : s;
}
function delegationStatusText(s: string | null | undefined): string {
  switch (s) {
    case 'running': return '工作中';
    case 'completed': return '已完成';
    case 'stopped': return '已停止';
    case 'failed': return '失败';
    case 'interrupted': return '已中断';
    case 'not_configured': return '未配置';
    case 'dispatched': return '已委派';
    default: return s || '';
  }
}
function resultTone(m: RoomMessage): string {
  if (m.status === 'failed') return 'tone-fail';
  if (m.status === 'stopped') return 'tone-stopped';
  if (m.status === 'completed' && m.verified) return 'tone-ok';
  return 'tone-neutral';
}
function liveItemsOf(m: RoomMessage): ProcessItem[] {
  return ((m as any)._liveItems as ProcessItem[]) || [];
}
function liveDoneCount(m: RoomMessage): number {
  return liveItemsOf(m).filter(p => p.kind === 'tool' && p.status !== 'running').length;
}
function toggleDetail(id: string) { detailOpen.value[id] = !detailOpen.value[id]; }
function toggleDebug(id: string) { debugOpen.value[id] = !debugOpen.value[id]; }
function toggleLive(tid: string) { liveOpen.value[tid] = !liveOpen.value[tid]; }
function scopeText(m: RoomMessage): string {
  const s = m.scope || {};
  const parts: string[] = [];
  if (s.scene_ids?.length) parts.push('场景: ' + s.scene_ids.join(', '));
  if (s.character_names?.length) parts.push('角色: ' + s.character_names.join(', '));
  if (s.free) parts.push(String(s.free));
  return parts.join('；');
}
function fmtTime(ts?: number): string {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

async function send() {
  const content = draft.value.trim();
  if (!content || composerDisabled.value) return;
  if (!projectName.value) { roomError.value = '请先选择项目'; return; }
  draft.value = '';
  roomError.value = '';
  room.pushOptimistic(content);
  try {
    await nextTick();
    stick.pinToBottom();
    await turn.runTurn({
      projectName: projectName.value,
      sessionId: room.sessionId!,
      content,
      thinking: thinkingToggle.value,
    });
  } catch (e: any) {
    roomError.value = '发送失败: ' + (e?.message || e);
    room.clearOptimistic();
  } finally {
    // 终态兜底：SSE tap 已触发 refresh，这里再 reconcile 一次（覆盖断流等边缘）
    room.clearOptimistic();
    await room.refresh();
  }
  stick.pinToBottom();
}

async function onStop() {
  await turn.cancelTurn();
}

onMounted(async () => {
  if (!projectName.value) { roomError.value = '请先选择项目'; return; }
  roomError.value = '';
  try {
    await room.ensureSession(projectName.value);
    await room.refresh();
    await nextTick();
    stick.pinToBottom();
  } catch (e: any) {
    roomError.value = '打开 Production Room 失败: ' + (e?.message || e);
  }
});

onUnmounted(() => {
  // 不停轮询/不重置 store：app 级持有，切页面回来状态还在（与 agentTurn 同思想）。
  // 项目切换由 ensureSession 的 reset 处理。
});

watch(projectName, async (p, old) => {
  if (p && p !== old) {
    await room.ensureSession(p);
    await room.refresh();
    await nextTick();
    stick.pinToBottom();
  }
});
</script>

<style lang="scss" scoped>
.proom { height: 100%; display: flex; flex-direction: column; min-height: 0; }

/* ---- 头部 ---- */
.proom-head { display: flex; align-items: center; gap: 12px; padding: 0 2px 8px; flex-wrap: wrap; }
.ph-title { display: flex; align-items: baseline; gap: 8px; }
.ph-emoji { font-size: 15px; }
.ph-name { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.ph-sub { font-size: 11px; color: var(--text-muted); }
.ph-wf { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary);
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 12px; padding: 3px 10px;
  max-width: 340px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wf-dot { width: 7px; height: 7px; border-radius: 50%; background: #6b7280; flex-shrink: 0;
  &.running { background: #60a5fa; animation: pr-pulse 1.4s ease infinite; }
  &.completed { background: #4ade80; }
  &.failed { background: #f87171; }
  &.stopped { background: #9ca3af; } }
.ph-roster-toggle { margin-left: auto; font-size: 11px; color: var(--text-muted); background: none;
  border: 1px solid var(--border); border-radius: 10px; padding: 3px 10px; cursor: pointer;
  &:hover { color: var(--text-primary); border-color: var(--accent); } }

/* ---- roster ---- */
.proom-roster { display: flex; gap: 8px; flex-wrap: wrap; padding: 0 0 10px; }
.roster-item { display: flex; align-items: center; gap: 8px; background: var(--bg-surface);
  border: 1px solid var(--border); border-radius: 10px; padding: 6px 10px; min-width: 0; }
.r-ava { width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center;
  justify-content: center; font-size: 12px; flex-shrink: 0; filter: saturate(.85); }
.r-info { display: flex; flex-direction: column; line-height: 1.2; }
.r-name { font-size: 12px; font-weight: 600; color: var(--text-primary); }
.r-role { font-size: 10px; color: var(--text-muted); }
.r-status { margin-left: 4px; font-size: 11px; display: flex; align-items: center; gap: 4px; color: var(--text-muted);
  &.s-working { color: #60a5fa; font-weight: 600; }
  &.s-running { color: #60a5fa; font-weight: 600; }
  &.s-completed { color: #4ade80; }
  &.s-cancelled, &.s-stopped { color: var(--text-muted); }
  &.s-failed { color: #f87171; font-weight: 600; }
  &.s-interrupted { color: #fbbf24; } }
.r-pulse { width: 6px; height: 6px; border-radius: 50%; background: #60a5fa; display: inline-block;
  animation: pr-pulse 1.4s ease infinite; }
@keyframes pr-pulse { 0%,100% { opacity: 1; } 50% { opacity: .25; } }

/* ---- 消息区 ---- */
.proom-messages { flex: 1; overflow-y: auto; padding: 10px 6px; display: flex; flex-direction: column; gap: 12px; min-height: 200px; }
.pr-empty { text-align: center; color: var(--text-muted); font-size: 13px; padding: 48px 24px; line-height: 1.7; }
.pr-empty-emoji { font-size: 28px; margin-bottom: 8px; }
.pr-empty-sub { font-size: 11px; color: var(--text-faint, var(--text-muted)); margin-top: 4px; }
.pr-row { display: flex; gap: 8px; align-items: flex-start;
  &.user { justify-content: flex-end; }
  &.agent { justify-content: flex-start; }
  &.delegation { justify-content: center; } }
.pr-ava { width: 26px; height: 26px; border-radius: 50%; display: flex; align-items: center;
  justify-content: center; font-size: 13px; flex-shrink: 0; margin-top: 2px; filter: saturate(.85); }
.pr-bubble { max-width: 78%; padding: 8px 12px; border-radius: 10px; font-size: 13px;
  &.user { background: var(--accent, #3b82f6); color: var(--accent-text, #fff); border-bottom-right-radius: 3px; }
  &.agent { background: var(--bg-surface); border: 1px solid var(--border); color: var(--text-primary); border-top-left-radius: 3px; }
  &.streaming { border-color: rgba(96,165,250,.45); } }
.pr-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.pr-speaker { font-size: 11px; font-weight: 600; color: var(--text-secondary); }
.pr-mstatus { font-size: 10px;
  &.ms-failed { color: #f87171; }
  &.ms-stopped { color: var(--text-muted); }
  &.ms-not_configured { color: #fbbf24; } }
.pr-live-chip { display: inline-flex; align-items: center; gap: 4px; font-size: 10px; color: #60a5fa; }
.pr-activity { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary, #9399b2); padding: 2px 0 4px; }
.spinner { display: inline-block; animation: pr-spin 1s linear infinite; }
@keyframes pr-spin { to { transform: rotate(360deg); } }
.pr-content { white-space: pre-wrap; word-break: break-word; line-height: 1.6; }
.stop-btn { margin-left: auto; font-size: 11px; padding: 1px 8px; border-radius: 8px; cursor: pointer;
  color: var(--text-secondary, #f87171); background: none; border: 1px solid var(--border);
  &:hover { color: #f87171; border-color: rgba(248,113,113,.4); } }
.pr-error { color: #f87171; font-size: 11px; padding: 4px 8px; }

/* ---- 协作卡（委派 / 结果）---- */
.pr-card { width: min(640px, 92%); background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px;
  &.delegation { margin-left: 34px; }
  &.live { border-color: rgba(96,165,250,.45); }
  &.result.tone-ok { border-left: 3px solid #4ade80; }
  &.result.tone-fail { border-left: 3px solid #f87171; }
  &.result.tone-stopped { border-left: 3px solid #9ca3af; }
  &.result.tone-neutral { border-left: 3px solid var(--border); } }
.pc-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.pc-route { display: flex; align-items: center; gap: 5px; }
.pc-ava { width: 20px; height: 20px; border-radius: 50%; display: flex; align-items: center;
  justify-content: center; font-size: 10px; filter: saturate(.85); }
.pc-arrow { color: var(--text-muted); font-size: 11px; }
.pc-names { font-size: 12px; font-weight: 600; color: var(--text-primary); }
.pc-status { font-size: 11px; display: inline-flex; align-items: center; gap: 4px; color: var(--text-muted);
  &.s-running, &.s-working { color: #60a5fa; font-weight: 600; }
  &.s-completed { color: #4ade80; }
  &.s-stopped, &.s-cancelled { color: var(--text-muted); }
  &.s-failed { color: #f87171; font-weight: 600; }
  &.s-interrupted { color: #fbbf24; } }
.pc-objective { font-size: 13px; color: var(--text-primary); white-space: pre-wrap; word-break: break-word; line-height: 1.6; }
.pc-foot { display: flex; gap: 10px; align-items: center; }
.pc-detail-btn { background: none; border: none; color: var(--text-muted); font-size: 11px; cursor: pointer; padding: 0;
  &:hover { color: var(--text-primary); } }
.pc-detail { border-top: 1px dashed var(--border); padding-top: 6px; display: flex; flex-direction: column; gap: 4px; }
.pcd-row { display: flex; gap: 8px; font-size: 11px; }
.pcd-k { color: var(--text-muted); flex-shrink: 0; width: 64px; }
.pcd-v { color: var(--text-secondary); white-space: pre-wrap; word-break: break-word; }
.pcd-v.mono { font-family: ui-monospace, monospace; font-size: 10px; word-break: break-all; }
.pcd-c { display: block; }
.pc-warn { border-top: 1px dashed rgba(248,113,113,.35); padding-top: 6px; }
.pc-warn-item { font-size: 11px; color: #f87171; }
.pc-full { border-top: 1px dashed var(--border); padding-top: 6px; font-size: 12px; color: var(--text-secondary);
  white-space: pre-wrap; word-break: break-word; line-height: 1.6; max-height: 320px; overflow-y: auto; }
.pc-full-more { color: var(--text-muted); font-size: 11px; }

/* live 执行详情（ Specialist 工具事件，collapsible） */
.pc-live { border-top: 1px dashed var(--border); padding-top: 6px; }
.pc-live-head { font-size: 11px; color: var(--text-secondary); cursor: pointer; user-select: none;
  &:hover { color: var(--text-primary); } }
.chevron { display: inline-block; transition: transform .15s; }
.chevron.open { transform: rotate(90deg); }
.pc-live-list { padding: 4px 0 2px; display: flex; flex-direction: column; gap: 4px; }
.pc-live-item { display: flex; align-items: baseline; gap: 6px; font-size: 11px; }
.pi-mark { flex-shrink: 0;
  &.done { color: #4ade80; } &.failed { color: #f87171; } &.running { color: var(--text-muted); } }
.pl-label { color: var(--text-primary); }
.pl-summary { color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 事实徽标 */
.pc-badges { display: flex; gap: 6px; flex-wrap: wrap; }
.badge { font-size: 10px; border-radius: 8px; padding: 2px 8px; border: 1px solid transparent;
  &.ok { color: #4ade80; border-color: rgba(74,222,128,.4); background: rgba(74,222,128,.07); }
  &.refs { color: var(--text-secondary); border-color: var(--border); }
  &.tools { color: var(--text-muted); border-color: var(--border); }
  &.neutral { color: var(--text-secondary); border-color: var(--border); }
  &.fail { color: #f87171; border-color: rgba(248,113,113,.4); background: rgba(248,113,113,.07); }
  &.warn { color: #fbbf24; border-color: rgba(251,191,36,.4); }
  &.stopped { color: var(--text-muted); border-color: var(--border); } }

/* ---- 输入区 ---- */
.proom-composer { display: flex; gap: 8px; padding: 10px 2px 4px; align-items: center;
  border-top: 1px solid var(--border); margin-top: 6px; }
.proom-composer .input { flex: 1; }
.input { background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px;
  font-size: 13px; color: var(--text-primary); outline: none;
  &:focus { border-color: var(--accent); } }
.btn { padding: 7px 14px; border-radius: 6px; font-size: 12px; font-weight: 500; cursor: pointer; border: none;
  &:disabled { opacity: .5; cursor: not-allowed; } }
.btn-primary { background: var(--accent); color: var(--accent-text, #fff);
  &:hover:not(:disabled) { filter: brightness(1.1); } }
.pr-hint { font-size: 11px; color: var(--text-secondary, #9399b2); padding: 4px 2px; }

/* 思考 toggle（与 agents 页同款滑动开关） */
.thinking-toggle { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary, #9399b2);
  cursor: pointer; user-select: none; padding: 0 2px; flex-shrink: 0;
  &.on { color: var(--text-primary, #cdd6f4); } }
.tt-sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden;
  clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
.tt-track { position: relative; width: 30px; height: 16px; border-radius: 8px; background: rgba(255,255,255,.16);
  transition: background .18s ease; flex-shrink: 0; }
.thinking-toggle.on .tt-track { background: #60a5fa; }
.tt-thumb { position: absolute; top: 2px; left: 2px; width: 12px; height: 12px; border-radius: 50%;
  background: #fff; transition: transform .18s ease; }
.thinking-toggle.on .tt-thumb { transform: translateX(14px); }
.thinking-toggle:focus-within .tt-track { outline: 2px solid rgba(96,165,250,.4); outline-offset: 1px; }

/* 小屏：roster 可折叠 + 卡片占满 */
@media (max-width: 720px) {
  .pr-bubble { max-width: 88%; }
  .pr-card.delegation { width: 100%; margin-left: 0; }
  .ph-sub { display: none; }
}
</style>
