/**
 * AI-7B Change Session store（Pinia，setup style）。镜像 useTeamChatStore 风格。
 * polling-first（§84）：活跃会话每 POLL_MS 轮询 GET session + plan；终态停止。
 * 不新增 realtime bus（§84：persistence 即真相，轮询只是通知）。
 * 项目切换 → stopPoll + 清空（§91 项目隔离）。
 * 批准/拒绝/反馈均经此 store（§76/§80/§78）。刷新恢复：persisted session 重载（§87）。
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiClient } from '@/api';
import type { ChangeSession, UnifiedChangePlan, PlanPreviewArtifact, ChangeDecisionRequest, ChangeExecution } from '@/api';
import { useProjectStore } from '@/store';

const POLL_MS = 2000;
// 活跃（计算中）状态：轮询；终态/稳定态停止（§84）
const ACTIVE_STATUSES = new Set([
  'routing', 'consulting', 'analyzing_dependencies', 'synthesizing_plan',
  'validating_plan', 'building_preview', 'awaiting_user_decision',
]);
const TERMINAL_STATUSES = new Set([
  'approved_for_execution', 'rejected', 'stale', 'failed', 'cancelled',
]);
// AI-7C execution 活跃态：轮询 execution（§82 polling-first）
const EXECUTION_ACTIVE_STATUSES = new Set(['pending', 'running', 'waiting_user']);
const EXECUTION_TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);

export const useChangeSessionStore = defineStore('changeSession', () => {
  const projectStore = useProjectStore();
  const session = ref<ChangeSession | null>(null);
  const plan = ref<(UnifiedChangePlan & { preview?: PlanPreviewArtifact }) | null>(null);
  const decisions = ref<ChangeDecisionRequest[]>([]);
  const execution = ref<ChangeExecution | null>(null);
  const busy = ref(false);          // create/resume/decision/approve/reject/execute 进行中
  const error = ref('');
  const lastRequestText = ref('');
  let pollTimer: number | null = null;

  const isActive = computed(() => !!session.value && ACTIVE_STATUSES.has(session.value.status));
  const isTerminal = computed(() => !!session.value && TERMINAL_STATUSES.has(session.value.status));
  const readyForReview = computed(() => session.value?.status === 'ready_for_review');
  const needsDecision = computed(() => session.value?.status === 'awaiting_user_decision');
  // AI-7C execution 派生态（§79 pre/post-commit 区分）
  const approved = computed(() => session.value?.status === 'approved_for_execution');
  const executionActive = computed(() => !!execution.value && EXECUTION_ACTIVE_STATUSES.has(execution.value.status));
  const executionBlocked = computed(() => execution.value?.status === 'blocked');
  const executionDone = computed(() => !!execution.value && EXECUTION_TERMINAL_STATUSES.has(execution.value.status));
  const creativeCommitted = computed(() => !!execution.value?.canonical_commit && execution.value.canonical_commit.status === 'committed');
  const needsExecute = computed(() => approved.value && !execution.value);  // 已批准 + 无 execution -> 可 Execute

  function _set(s: ChangeSession | null) {
    session.value = s;
    if (s && ACTIVE_STATUSES.has(s.status)) startPoll();
    else stopPoll();
  }

  async function create(requestText: string, sourceMessageId?: string) {
    const p = projectStore.projectName;
    const text = requestText.trim();
    if (!p || !text || busy.value) return;
    busy.value = true; error.value = '';
    try {
      const res = await apiClient.createChangeSession(p, text, 'team_chat', sourceMessageId);
      lastRequestText.value = text;
      _set(res.session);
      // 立即触发首轮推进（后端 create 不自动后台推进）
      await resume();
    } catch (e: any) {
      error.value = '创建变更会话失败: ' + (e.message || e);
    } finally {
      busy.value = false;
    }
  }

  async function resume() {
    const p = projectStore.projectName;
    const sid = session.value?.session_id;
    if (!p || !sid) return;
    try {
      const s = await apiClient.resumeChangeSession(p, sid);
      _set(s);
      // ready_for_review → 拉 plan+preview；awaiting → 拉 decisions（plan 端点含 preview）
      if (s.status === 'ready_for_review' || s.status === 'awaiting_user_decision' || s.status === 'validating_plan') {
        await loadPlan();
      }
    } catch (e: any) {
      error.value = '推进失败: ' + (e.message || e);
    }
  }

  async function loadPlan() {
    const p = projectStore.projectName;
    const sid = session.value?.session_id;
    if (!p || !sid) return;
    try {
      const pl = await apiClient.getChangeSessionPlan(p, sid);
      plan.value = pl;
      // decisions 从 plan + session 推导：awaiting 时需用户回答（7B-6 可补 list 端点）
    } catch { /* plan 可能尚不存在 */ }
  }

  async function poll() {
    const p = projectStore.projectName;
    const sid = session.value?.session_id;
    if (!p || !sid) return;
    try {
      const s = await apiClient.getChangeSession(p, sid);
      const changed = s.status !== session.value?.status;
      _set(s);
      if (changed && (s.status === 'ready_for_review' || s.status === 'awaiting_user_decision' || s.status === 'validating_plan')) {
        await loadPlan();
      }
    } catch { /* 静默，下次再试 */ }
    // AI-7C：execution 活跃时轮询 execution（§82 polling-first）
    if (execution.value && EXECUTION_ACTIVE_STATUSES.has(execution.value.status)) {
      await loadExecution();
    }
  }

  /** AI-7C §15/§67：启动已批准 plan 的执行。幂等：重复调对终态 no-op。 */
  async function execute(productionMode: string = 'affected_production') {
    const p = projectStore.projectName;
    const sid = session.value?.session_id;
    const pl = plan.value;
    if (!p || !sid || !pl || busy.value) return;
    // 已有终态 execution -> 不重启（幂等）；blocked/resume 走 resumeExecution
    if (execution.value && executionActive.value) return;
    busy.value = true; error.value = '';
    try {
      const res = await apiClient.executeChangePlan(p, sid, pl.plan_id, pl.version, productionMode);
      execution.value = res.execution;
      if (executionActive.value) startPoll();
    } catch (e: any) {
      error.value = '启动执行失败: ' + (e.message || e);
    } finally {
      busy.value = false;
    }
  }

  async function loadExecution() {
    const p = projectStore.projectName;
    const sid = session.value?.session_id;
    const eid = execution.value?.execution_id;
    if (!p || !sid || !eid) return;
    try {
      execution.value = await apiClient.getChangeExecution(p, sid, eid);
    } catch { /* 静默 */ }
  }

  async function resumeExecution() {
    const p = projectStore.projectName;
    const sid = session.value?.session_id;
    const eid = execution.value?.execution_id;
    if (!p || !sid || !eid || busy.value) return;
    busy.value = true; error.value = '';
    try {
      const res = await apiClient.resumeChangeExecution(p, sid, eid);
      execution.value = res.execution;
      if (executionActive.value) startPoll();
    } catch (e: any) {
      error.value = '恢复执行失败: ' + (e.message || e);
    } finally {
      busy.value = false;
    }
  }

  async function cancelExecution() {
    const p = projectStore.projectName;
    const sid = session.value?.session_id;
    const eid = execution.value?.execution_id;
    if (!p || !sid || !eid || busy.value) return;
    busy.value = true; error.value = '';
    try {
      const res = await apiClient.cancelChangeExecution(p, sid, eid);
      execution.value = res.execution;
      stopPoll();
    } catch (e: any) {
      error.value = '取消执行失败: ' + (e.message || e);
    } finally {
      busy.value = false;
    }
  }

  function startPoll() {
    if (pollTimer != null) return;
    pollTimer = window.setInterval(poll, POLL_MS);
  }
  function stopPoll() {
    if (pollTimer != null) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function submitDecision(decisionId: string, optionId: string) {
    const p = projectStore.projectName;
    const sid = session.value?.session_id;
    if (!p || !sid || busy.value) return;
    busy.value = true; error.value = '';
    try {
      const res = await apiClient.submitChangeDecision(p, sid, decisionId, optionId);
      _set(res.session);
      await resume();   // §35：resume same ChangeSession → recompile
    } catch (e: any) {
      error.value = '提交决策失败: ' + (e.message || e);
    } finally {
      busy.value = false;
    }
  }

  async function approve() {
    const p = projectStore.projectName;
    const sid = session.value?.session_id;
    const pl = plan.value;
    if (!p || !sid || !pl || busy.value) return;
    busy.value = true; error.value = '';
    try {
      const res = await apiClient.approveChangeSession(p, sid, pl.plan_id, pl.version);
      if (session.value) _set({ ...session.value, status: 'approved_for_execution' });
      return res;
    } catch (e: any) {
      error.value = '批准失败: ' + (e.message || e);
    } finally {
      busy.value = false;
    }
  }

  async function requestChanges(feedback: string) {
    const p = projectStore.projectName;
    const sid = session.value?.session_id;
    const pl = plan.value;
    if (!p || !sid || !feedback.trim() || busy.value) return;
    busy.value = true; error.value = '';
    try {
      const res = await apiClient.requestChangeSessionChanges(p, sid, feedback.trim(), pl?.plan_id);
      plan.value = res.plan;
      if (session.value) _set({ ...session.value, status: 'routing', active_plan_id: res.plan.plan_id });
      await resume();
    } catch (e: any) {
      error.value = '请求修改失败: ' + (e.message || e);
    } finally {
      busy.value = false;
    }
  }

  async function reject() {
    const p = projectStore.projectName;
    const sid = session.value?.session_id;
    const pl = plan.value;
    if (!p || !sid || busy.value) return;
    busy.value = true; error.value = '';
    try {
      await apiClient.rejectChangeSession(p, sid, pl?.plan_id);
      if (session.value) _set({ ...session.value, status: 'rejected' });
    } catch (e: any) {
      error.value = '拒绝失败: ' + (e.message || e);
    } finally {
      busy.value = false;
    }
  }

  /** 刷新恢复（§87/§89）：重载 persisted active session + 其 execution（如有）。TeamChatPanel mount 调用。 */
  async function restoreActive() {
    const p = projectStore.projectName;
    if (!p) return;
    try {
      const sessions = await apiClient.listChangeSessions(p);
      const active = sessions.find(s => !TERMINAL_STATUSES.has(s.status));
      if (active) {
        _set(active);
        if (active.status === 'ready_for_review') await loadPlan();
        // AI-7C §89：approved session 可能挂 execution（refresh 恢复）
        if (active.status === 'approved_for_execution') {
          try {
            const exs = await apiClient.listChangeExecutions(p, active.session_id);
            if (exs.length) { execution.value = exs[0]; if (executionActive.value) startPoll(); }
          } catch { /* 无 execution */ }
        }
      } else { session.value = null; plan.value = null; execution.value = null; }
    } catch { /* 无会话或项目未选 */ }
  }

  function onProjectChange() {
    stopPoll();
    session.value = null;
    plan.value = null;
    decisions.value = [];
    execution.value = null;
    error.value = '';
    lastRequestText.value = '';
  }

  return {
    session, plan, decisions, execution, busy, error, lastRequestText,
    isActive, isTerminal, readyForReview, needsDecision,
    approved, executionActive, executionBlocked, executionDone, creativeCommitted, needsExecute,
    create, resume, loadPlan, submitDecision, approve, requestChanges, reject,
    execute, loadExecution, resumeExecution, cancelExecution,
    restoreActive, onProjectChange, stopPoll,
  };
});
