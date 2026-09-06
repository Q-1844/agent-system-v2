/**
 * Producer V1 Phase 3 · Production Room store（Pinia）。
 *
 * 职责：把「持久快照（GET /agents/producer/room）」与「Producer SSE 实时事件」
 * 合并成群聊 timeline。设计红线（Phase 3 §三/§二十/§二十一）：
 *   - SSE 只是 live 体验，不是持久事实；刷新后的一切以 snapshot 重建；
 *   - 稳定 ID 去重：live 合成消息直接使用与投影一致的
 *     task:{task_id}:dispatch 键——snapshot 到达后同键覆盖，绝不产生重复 bubble；
 *   - 状态不伪造：working 只来自 AgentTask(live)/SSE delegation.started 等真实事件。
 *
 * 轮询对账：turn 后台续跑（客户端断开/刷新后无 SSE）时，以 3s 轮询 snapshot 兜底，
 * 条件 = turn 活跃 || workflow running || 存在 running 消息；全部终止后自动停。
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiClient } from '@/api';
import type { RoomMessage, RoomSnapshot, AgentStreamEvent } from '@/api';
import type { ProcessItem } from './agentTurn';
import { useAgentTurnStore } from './agentTurn';

/** SSE 期间由前端持有的进行中委派（task_id 键；stable id 与后端投影同构） */
export interface LiveDelegation {
  taskId: string;
  agent: string;
  objective: string;
  startedAt: number;
  items: ProcessItem[];          // 执行详情（ Specialist 工具事件，前端态，刷新即由快照接管）
  settleStatus: string | null;   // delegation.completed 到达但快照未落地时的过渡状态
}

const POLL_MS = 3000;
const MAX_STALE_POLLS = 100;     // 快照连续无变化上限（后端重启残留 running 的兜底停轮询）

const _KIND_ORDER: Record<string, number> = {
  user: 0, delegation: 1, specialist_result: 2, producer: 3,
};

export const useProductionRoomStore = defineStore('productionRoom', () => {
  const project = ref('');
  const sessionId = ref<string | null>(null);
  const snapshot = ref<RoomSnapshot | null>(null);
  const loading = ref(false);
  const error = ref('');
  const liveDelegations = ref<Record<string, LiveDelegation>>({});
  /**
   * 乐观 user 回显（仅本地 placeholder）。注意：真实消息去重一律走稳定 ID；
   * 这里按 content+时间窗回收只是本地 echo 的清理（真实消息有服务端 message_id，
   * 不存在双份持久事实）。turn 结束后的 refresh 会无条件清空。
   */
  const optimisticUser = ref<RoomMessage[]>([]);

  let unsub: (() => void) | null = null;
  let pollTimer: number | null = null;
  let currentTaskId = '';
  let stalePolls = 0;
  let lastFingerprint = '';

  // ============ timeline：live 合成 + persisted，同键去重后按稳定序排列 ============
  const timeline = computed<RoomMessage[]>(() => {
    const byId = new Map<string, RoomMessage>();
    for (const m of optimisticUser.value) byId.set(m.id, m);
    for (const d of Object.values(liveDelegations.value)) {
      byId.set(`task:${d.taskId}:dispatch`, {
        id: `task:${d.taskId}:dispatch`,
        kind: 'delegation',
        timestamp: d.startedAt,
        sender: 'producer',
        recipient: d.agent,
        content: d.objective,
        situation: '',
        constraints: [],
        scope: {},
        task_id: d.taskId,
        status: d.settleStatus ?? 'running',
        live: !d.settleStatus,
        _liveItems: d.items,
      } as RoomMessage & { _liveItems?: ProcessItem[] });
    }
    for (const m of snapshot.value?.messages ?? []) byId.set(m.id, m);
    return [...byId.values()].sort((a, b) =>
      (a.timestamp ?? 0) - (b.timestamp ?? 0)
      || (_KIND_ORDER[a.kind] ?? 9) - (_KIND_ORDER[b.kind] ?? 9)
      || (a.id < b.id ? -1 : 1));
  });

  const participants = computed(() => {
    const list = (snapshot.value?.participants ?? []).map(p => ({ ...p }));
    // live 委派覆盖：SSE delegation.started 后立刻 working（不等下一次 snapshot）
    const byAgent = new Map(list.map(p => [p.agent_id, p]));
    for (const d of Object.values(liveDelegations.value)) {
      const p = byAgent.get(d.agent);
      if (p && !d.settleStatus) p.status = 'working';
    }
    const t = useAgentTurnStore();
    const prod = byAgent.get('producer');
    if (prod && t.running && t.activeSessionId === sessionId.value) prod.status = 'working';
    return list;
  });

  const workflow = computed(() => snapshot.value?.workflow ?? null);
  const turnActive = computed(() => !!snapshot.value?.turn_active);

  /** 是否需要轮询对账（SSE 不可用/丢失时的持久层兜底） */
  function needsPolling(): boolean {
    const t = useAgentTurnStore();
    if (t.running && t.activeSessionId === sessionId.value && t.activeProject === project.value) return true;
    const s = snapshot.value;
    if (!s) return false;
    if (s.turn_active) return true;
    if (s.workflow?.status === 'running') return true;
    if (s.messages.some(m => m.status === 'running' || m.live)) return true;
    return false;
  }

  // ============ 数据加载 ============
  async function ensureSession(projectName: string): Promise<void> {
    if (project.value !== projectName) reset();
    project.value = projectName;
    if (sessionId.value) return;
    // get_or_create：与 Agent Center 其余 Direct Chat 同语义（幂等，无副作用扩散）
    const s = await apiClient.createAgentSession(projectName, 'producer', 'project', projectName);
    sessionId.value = s.session_id;
    ensureSubscribed();
  }

  async function refresh(projectName?: string): Promise<void> {
    const p = projectName || project.value;
    if (!p) return;
    if (project.value !== p) { reset(); project.value = p; }
    loading.value = true;
    try {
      const snap = await apiClient.getProductionRoom(p);
      if (p !== project.value) return;   // 期间切了项目：丢弃过期快照
      snapshot.value = snap;
      sessionId.value = snap.session_id ?? sessionId.value;
      ensureSubscribed();
      reconcileOptimistic();
      // 已被快照覆盖的 live 委派回收（结果消息已持久化）
      const ids = new Set(snap.messages.map(m => m.id));
      for (const [tid, d] of Object.entries(liveDelegations.value)) {
        if (ids.has(`task:${tid}:result`) || ids.has(`task:${tid}:dispatch`)) {
          if (ids.has(`task:${tid}:result`)) delete liveDelegations.value[tid];
          else d.settleStatus = d.settleStatus ?? 'completed';
        }
      }
      // 轮询寿命管理：状态未变的 stale 轮询计满即停（后端重启残留 running 的兜底）
      const fp = JSON.stringify([snap.turn_active, snap.workflow?.status,
        snap.messages.map(m => [m.id, m.status ?? null, m.live ?? false])]);
      if (fp === lastFingerprint) stalePolls += 1; else stalePolls = 0;
      lastFingerprint = fp;
      error.value = '';
    } catch (e: any) {
      error.value = '加载 Production Room 失败: ' + (e?.message || e);
    } finally {
      loading.value = false;
    }
    syncPolling();
  }

  // ============ SSE 实时合并（仅 live 体验；持久事实以 snapshot 为准）============
  function ensureSubscribed(): void {
    if (unsub || !sessionId.value) return;
    unsub = useAgentTurnStore().onTurnEvent(absorbEvent);
  }

  function absorbEvent(ev: AgentStreamEvent): void {
    const t = useAgentTurnStore();
    if (t.activeSessionId !== sessionId.value || t.activeProject !== project.value) return;
    const e = ev as any;
    switch (e.type) {
      case 'turn.started':
        // user 消息此刻已在服务端落盘 → 立刻对账（乐观 echo 换真实 message_id）
        void refresh();
        break;
      case 'tool.progress':
        if (e.phase === 'delegation.started' && e.call_id) {
          currentTaskId = e.call_id;
          liveDelegations.value[e.call_id] = {
            taskId: e.call_id, agent: e.agent || '', objective: e.objective || '',
            startedAt: Date.now() / 1000, items: [], settleStatus: null,
          };
        } else if (e.phase === 'delegation.completed' && e.call_id) {
          const d = liveDelegations.value[e.call_id];
          if (d) d.settleStatus = e.status || 'completed';
          void refresh();   // 结果/完整 envelope 此刻已持久化
        }
        break;
      case 'tool.started':
      case 'tool.completed':
      case 'tool.failed': {
        if (!e.agent) break;   // Producer 自身工具事件不进 Specialist 执行详情
        const d = liveDelegations.value[currentTaskId];
        if (!d) break;
        upsertLiveItem(d.items, e);
        break;
      }
      case 'turn.completed':
      case 'turn.failed':
        void refresh();   // 终态：清乐观 echo + roster 收尾 + 停轮询判定
        break;
    }
  }

  function upsertLiveItem(items: ProcessItem[], e: any): void {
    if (e.type === 'tool.started') {
      items.push({ kind: 'tool', step_id: e.step_id ?? 0, call_id: e.call_id,
        name: e.name, label: e.label, status: 'running' });
      return;
    }
    const it = items.find(p => p.kind === 'tool' && p.call_id === e.call_id);
    if (!it) {
      items.push({ kind: 'tool', step_id: e.step_id ?? 0, call_id: e.call_id,
        name: e.name, label: e.label, status: e.type === 'tool.failed' ? 'failed' : 'done',
        summary: e.summary, error: e.error });
      return;
    }
    if (e.type === 'tool.completed') { it.status = e.ok ? 'done' : 'failed'; it.summary = e.summary; }
    if (e.type === 'tool.failed') { it.status = 'failed'; it.error = e.error; }
  }

  // ============ 乐观 user echo（本地 placeholder，真实去重靠服务端稳定 ID）============
  function pushOptimistic(content: string): void {
    optimisticUser.value.push({
      id: 'local:' + Date.now(), kind: 'user',
      timestamp: Date.now() / 1000, sender: 'user', content,
    });
  }

  function reconcileOptimistic(): void {
    if (!optimisticUser.value.length) return;
    const persistedUsers = (snapshot.value?.messages ?? []).filter(m => m.kind === 'user');
    optimisticUser.value = optimisticUser.value.filter(o => {
      // 同内容且时间窗吻合（±5s，同机时钟）→ 已有真实 message_id 承接，回收 echo
      return !persistedUsers.some(m =>
        m.content === o.content && Math.abs((m.timestamp ?? 0) - (o.timestamp ?? 0)) < 5);
    });
  }

  function clearOptimistic(): void {
    optimisticUser.value = [];
  }

  // ============ 轮询 ============
  function syncPolling(): void {
    const want = needsPolling() && stalePolls < MAX_STALE_POLLS;
    if (want && pollTimer == null) {
      pollTimer = window.setInterval(() => {
        if (needsPolling() && stalePolls < MAX_STALE_POLLS) void refresh();
        else stopPolling();
      }, POLL_MS);
    } else if (!want && pollTimer != null) {
      stopPolling();
    }
  }

  function stopPolling(): void {
    if (pollTimer != null) { window.clearInterval(pollTimer); pollTimer = null; }
  }

  function reset(): void {
    stopPolling();
    snapshot.value = null;
    sessionId.value = null;
    liveDelegations.value = {};
    optimisticUser.value = [];
    error.value = '';
    currentTaskId = '';
    stalePolls = 0;
    lastFingerprint = '';
  }

  return {
    project, sessionId, snapshot, loading, error, liveDelegations, optimisticUser,
    timeline, participants, workflow, turnActive,
    ensureSession, refresh, pushOptimistic, reconcileOptimistic, clearOptimistic,
    reset, syncPolling, stopPolling,
  };
});
