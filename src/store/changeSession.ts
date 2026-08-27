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
import type { ChangeSession, UnifiedChangePlan, PlanPreviewArtifact, ChangeDecisionRequest, ChangeExecution, ProductionExecutionManifest, AgentMessage } from '@/api';
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
// §3/§89：session 真终态（不可恢复）。approved_for_execution **不**属此列——它是"提议已定但可执行/执行中"，
// 刷新时需恢复其 plan/execution（H2/F-Extra1）。与 TERMINAL_STATUSES（含 approved，表"提议已定"语义）分离：
// approved 既是 proposal-settled（isTerminal 仍 true）又是 restorable（恢复 execution）。两概念不可混用（§7.1）。
const SESSION_NOT_RESTORABLE = new Set(['rejected', 'stale', 'failed', 'cancelled']);
// AI-7D-R §7/§8：已“落定”的会话——历史事务，不得捕获后续新变更。新请求一律基于最新 canonical
// 新建会话。与后端 SESSION_SETTLED 对齐（含 blocked 僵尸态与 no_action 诚实终态）。
const SESSION_SETTLED = new Set([
  'approved_for_execution', 'rejected', 'stale', 'failed', 'cancelled',
  'no_action', 'blocked',
]);
// 仍开放、可接收修订反馈的会话状态（用户正在审查/决策，新消息作为对该方案的 revise）。
const SESSION_OPEN_FOR_FEEDBACK = new Set([
  'awaiting_user_decision', 'ready_for_review',
  'routing', 'consulting', 'analyzing_dependencies', 'resolving_conflicts',
  'synthesizing_plan', 'validating_plan', 'building_preview',
]);

export const useChangeSessionStore = defineStore('changeSession', () => {
  const projectStore = useProjectStore();
  const session = ref<ChangeSession | null>(null);
  const plan = ref<(UnifiedChangePlan & { preview?: PlanPreviewArtifact }) | null>(null);
  const decisions = ref<ChangeDecisionRequest[]>([]);
  const execution = ref<ChangeExecution | null>(null);
  // AI-7C-H §28：execution 的 production manifest（真实计数分母：受影响 char/scene）
  const manifest = ref<ProductionExecutionManifest | null>(null);
  // L6/§7.5：approved+未执行时的权威 eligibility（stale/blocked/invalid_approval 预呈现，不前端复制规则）
  const eligibility = ref<{ eligibility: string; execution?: ChangeExecution; reason?: string; blocker_code?: string } | null>(null);
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
  // AI-7C-H §三/§十一：正在停止（后端权威 running + cancel_requested），UI 显示「正在停止…」；
  // 不提前宣称已取消——cancelled 只来自后端持久终态。
  const executionStopping = computed(() =>
    !!execution.value && execution.value.status === 'running' && !!execution.value.cancel_requested);
  const creativeCommitted = computed(() => !!execution.value?.canonical_commit && execution.value.canonical_commit.status === 'committed');
  const needsExecute = computed(() => approved.value && !execution.value);  // 已批准 + 无 execution -> 可 Execute
  // AI-7D-R §10：诚实的“无需改动”终态（不伪造 A/B 方案）
  const noAction = computed(() => session.value?.status === 'no_action');
  const noActionReason = computed(() => {
    const s = session.value as any;
    return (s?.no_action_reason) || '';
  });
  // AI-7D-R §7：会话是否已落定（历史事务，后续新变更须新建会话）
  const settled = computed(() => !!session.value && SESSION_SETTLED.has(session.value.status));

  // §7.1/§7.2：轮询存活取决于 (proposal 活跃) OR (approved 且 execution 活跃)。
  // 关键修复 H1：approved_for_execution 期间后端不改 session 状态，故 session 非 ACTIVE；
  // 旧逻辑据此 stopPoll 杀掉 execution 轮询。现显式把"execution 活跃"作为继续轮询的独立判据，
  // proposal 生命周期与 execution 生命周期分离（§7.1）。execution 终态/阻塞→ shouldPoll false→ 停。
  function shouldPoll(): boolean {
    const s = session.value;
    if (!s) return false;
    if (ACTIVE_STATUSES.has(s.status)) return true;                 // 提议仍在推进
    if (s.status === 'approved_for_execution' && executionActive.value) return true;  // 执行活跃
    return false;
  }

  function _set(s: ChangeSession | null) {
    session.value = s;
    if (s && s.status !== 'approved_for_execution') eligibility.value = null;  // L6：离开 approved 清 eligibility
    if (s && shouldPoll()) startPoll();
    else stopPoll();
  }

  /** 创建变更会话。AI-7C-H §23/§24：Floating Bar / Specialist 直聊的写意图经此桥接进
   * 真实 ChangeSession 工作流（durable），不再只是「谈论」修改的普通 AgentMessage。
   * options.sourceAgentId/sourceSessionId 标注来源；contextScope 携带页面上下文。 */
  async function create(requestText: string, sourceMessageId?: string, options?: {
    source?: 'team_chat' | 'floating_bar' | 'direct_agent_chat';
    sourceAgentId?: string;
    sourceSessionId?: string;
    contextScope?: { sceneId?: string; shotId?: string; characterName?: string };
    clientRequestId?: string;
  }) {
    const p = projectStore.projectName;
    const text = requestText.trim();
    if (!p || !text || busy.value) return;
    busy.value = true; error.value = '';
    try {
      const res = await apiClient.createChangeSession(
        p, text, options?.source || 'team_chat', sourceMessageId, {
          sourceAgentId: options?.sourceAgentId,
          sourceSessionId: options?.sourceSessionId,
          contextScope: options?.contextScope,
          clientRequestId: options?.clientRequestId,
        });
      lastRequestText.value = text;
      // AI-7C-H §十：新会话与旧 execution 解绑（restart 场景旧 cancelled execution 不残留显示）
      execution.value = null; manifest.value = null; eligibility.value = null;
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
      // L6/§7.5：approved 且未执行时查权威 eligibility（stale/blocked 预呈现）；execution 已存在则跳过
      await loadEligibility();
      // decisions 从 plan + session 推导：awaiting 时需用户回答（7B-6 可补 list 端点）
    } catch { /* plan 可能尚不存在 */ }
  }

  /** §4/§7.5：approved+无 execution 时查后端 eligibility（不前端复制规则，server truth wins）。 */
  async function loadEligibility() {
    const p = projectStore.projectName;
    const sid = session.value?.session_id;
    const pl = plan.value;
    if (!p || !sid || !pl || session.value?.status !== 'approved_for_execution' || execution.value) {
      eligibility.value = null;
      return;
    }
    try {
      eligibility.value = await apiClient.getExecutionEligibility(p, sid, pl.plan_id, pl.version);
    } catch { eligibility.value = null; }
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
      // AI-7B: 状态变化到活跃态时自动 resume 推进流水线（routing→consulting→analyzing→…→ready_for_review）
      if (changed && ACTIVE_STATUSES.has(s.status)) {
        await resume();
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
      manifest.value = null;      // 新 execution：manifest 分母随之更换（§28）
      eligibility.value = null;   // L6：已启动 execution，eligibility 让位 execution 真相
      // M1：execute 停在 character_sync（pre-commit 由 start_replay_commit 完成提交）；
      // 若到 post-commit 阶段且未 blocked，自动 resume 触发后台 kickoff（§3 polling-first，前端轮询推进）
      const ex = res.execution;
      if (ex && !res.blocked && ex.status !== 'blocked'
          && (ex.stage === 'character_sync' || ex.stage === 'production_planning')) {
        try {
          const r2 = await apiClient.resumeChangeExecution(p, sid, ex.execution_id);
          if (r2.execution) execution.value = r2.execution;
        } catch { /* kickoff 失败不致命：轮询/手动 resume 可重试 */ }
      }
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
    if (!p || !sid || !eid || !execution.value) return;
    const wasActive = EXECUTION_ACTIVE_STATUSES.has(execution.value.status);
    try {
      execution.value = await apiClient.getChangeExecution(p, sid, eid);
      // AI-7C-H §28：manifest（受影响 char/scene 分母）一次加载——运行卡真实计数用
      await loadManifest();
    } catch { /* 静默 */ }
    // AI-7D-R §7：执行收敛到终态 → 该会话已落定。停止把它当作新变更的目标，并处理排队请求。
    if (wasActive && execution.value && EXECUTION_TERMINAL_STATUSES.has(execution.value.status)) {
      stopPoll();
      // 给 UI 一点时间显示完成态后释放（历史事务不再捕获后续变更）。
      const queued = [..._queuedChangeRequests.value];
      _queuedChangeRequests.value = [];
      setTimeout(() => {
        if (session.value?.status === 'approved_for_execution'
            && execution.value && EXECUTION_TERMINAL_STATUSES.has(execution.value.status)) {
          session.value = null; plan.value = null; execution.value = null;
          manifest.value = null; eligibility.value = null;
        }
      }, 1500);
      // 排队中的变更请求：执行已结束 → 基于最新 canonical 新建会话处理
      for (const text of queued) {
        try { await _startFreshSession(text, { source: 'floating_bar' }); } catch { /* ignore */ }
      }
    }
  }

  /** §28：execution 的 production manifest（分母真相）。每个 execution_id 只取一次。 */
  async function loadManifest() {
    const p = projectStore.projectName;
    const sid = session.value?.session_id;
    const ex = execution.value;
    if (!p || !sid || !ex) return;
    if (manifest.value && manifest.value.execution_id === ex.execution_id) return;
    try {
      manifest.value = await apiClient.getExecutionManifest(p, sid, ex.execution_id);
    } catch { manifest.value = null; }
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
      // AI-7C-H §三/§十：取消是请求不是完成——正在停止（running+cancel_requested）时继续
      // 轮询，直到后端收敛为 cancelled 终态；已终态（提交前立即取消）则停轮询。
      if (executionActive.value) startPoll();
      else stopPoll();
    } catch (e: any) {
      error.value = '取消执行失败: ' + (e.message || e);
    } finally {
      busy.value = false;
    }
  }

  /** AI-7C-H §十：等待旧 execution 安全停止（终态 cancelled/completed/failed）。
   * 持续读后端权威状态（非本地推断）；超时返回 false（调用方不创建新会话，避免撞
   * PROJECT_PRODUCTION_ACTIVE）。幂等：无 execution 时立即 true。 */
  async function waitForExecutionStopped(timeoutMs = 30000): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const ex = execution.value;
      if (!ex || EXECUTION_TERMINAL_STATUSES.has(ex.status)) return true;
      await loadExecution();
      await new Promise(r => setTimeout(r, 500));
    }
    const ex = execution.value;
    return !ex || EXECUTION_TERMINAL_STATUSES.has(ex.status);
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
      throw e;   // AI-7D-R：让桥接方能据此回退到“新建会话”路径
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

  /** 刷新恢复（§87/§89 / AI-7D-R §7）：重载仍在推进中或仍可执行/在执行的会话 + 其 execution。
   * 已彻底落定（blocked、no_action、rejected/stale/failed/cancelled、执行已完成的 approved）
   * 是历史事务，不在刷新时重新领养——否则会捕获后续新变更（根因 #1）。
   * 注意：approved 但**尚未执行或执行仍在进行**的会话仍恢复（保留 AI-7C F5 恢复待执行/在执行
   * 计划的能力）；只是对“新变更”而言它算 settled（bridge 会另开新会话）。 */
  async function restoreActive() {
    const p = projectStore.projectName;
    if (!p) return;
    try {
      const sessions = await apiClient.listChangeSessions(p);
      // 1. 构建中/待审查/待决策的会话 → 恢复
      const active = sessions.find(s => SESSION_OPEN_FOR_FEEDBACK.has(s.status)
        || ACTIVE_STATUSES.has(s.status));
      if (active) {
        _set(active);
        if (active.status === 'ready_for_review' || active.status === 'awaiting_user_decision') {
          await loadPlan();
        }
        return;
      }
      // 2. approved：若尚无 execution 或 execution 仍活跃 → 恢复（待执行/执行中）；
      //    执行已终态 → 不恢复（历史事务）。
      const approved = sessions.find(s => s.status === 'approved_for_execution');
      if (approved) {
        let running: ChangeExecution | null = null;
        try {
          const exs = await apiClient.listChangeExecutions(p, approved.session_id);
          running = exs.find(e => EXECUTION_ACTIVE_STATUSES.has(e.status)) || null;
          if (!running && exs.length === 0) {
            // 已批准但还没启动执行 → 恢复待执行卡
            _set(approved);
            await loadPlan();
            return;
          }
        } catch { /* 无 execution */ }
        if (running) {
          _set(approved);
          await loadPlan();
          execution.value = running;
          await loadManifest();
          if (shouldPoll()) startPoll();
          return;
        }
      }
      session.value = null; plan.value = null; execution.value = null; manifest.value = null;
    } catch { /* 无会话或项目未选 */ }
  }

  function onProjectChange() {
    stopPoll();
    session.value = null;
    plan.value = null;
    decisions.value = [];
    execution.value = null;
    manifest.value = null;
    eligibility.value = null;
    error.value = '';
    lastRequestText.value = '';
    // AI-7D D2：重置 Direct Chat change-bridge 状态
    pendingChangeChoice.value = null;
    pendingChangeOpts.value = null;
    _queuedChangeRequests.value = [];
    preferredExecMode.value = 'affected_production';
  }

  // ============ AI-7D D2 · Direct Chat change 桥接（共享：Floating Bar + Agent Center）============
  // 镜像 agentUi._bridgeChangeIntent + resolveChangeChoice，逻辑集中到 store。返回 assistant
  // 消息数组供调用方 push 到各自消息列表；活跃执行时置 pendingChangeChoice 供 UI 渲染二选一。
  const pendingChangeChoice = ref<string | null>(null);
  const pendingChangeOpts = ref<{ source?: 'team_chat' | 'floating_bar' | 'direct_agent_chat'; sourceAgentId?: string; sourceSessionId?: string; contextScope?: { sceneId?: string; shotId?: string; characterName?: string } } | null>(null);
  const _queuedChangeRequests = ref<string[]>([]);
  let _creqCounter = 0;
  // AI-7D D2：检测"只…其它不改"语义 → execute 默认 creative_only（只提交创作，不级联生产/视频）。
  const preferredExecMode = ref<'affected_production' | 'creative_only'>('affected_production');
  const CREATIVE_ONLY_RE = /(只|仅)[^，。]{0,15}(改|拓展|扩写|修改|动)|(其它|其他|别的|别的)(不|勿|别碰)|不要.{0,4}(改|碰)?(其它|其他|别的)/;

  function _msg(role: string, content: string, status: string | null = null): AgentMessage {
    return { role, content, status, created_at: Date.now() / 1000 };
  }

  /** AI-7D-R §7/§8：基于最新 canonical 开启全新变更会话（不复用旧会话/旧 plan）。 */
  async function _startFreshSession(text: string, opts: {
    source?: 'team_chat' | 'floating_bar' | 'direct_agent_chat'; sourceAgentId?: string; sourceSessionId?: string;
    contextScope?: { sceneId?: string; shotId?: string; characterName?: string };
  }): Promise<void> {
    // 清掉已落定的旧会话引用，确保新会话不携带旧 plan/execution
    session.value = null; plan.value = null; decisions.value = [];
    execution.value = null; manifest.value = null; eligibility.value = null;
    error.value = '';
    preferredExecMode.value = CREATIVE_ONLY_RE.test(text) ? 'creative_only' : 'affected_production';
    _creqCounter += 1;
    await create(text, undefined, {
      source: opts.source || 'direct_agent_chat',
      sourceAgentId: opts.sourceAgentId,
      sourceSessionId: opts.sourceSessionId,
      contextScope: opts.contextScope,
      clientRequestId: 'creq_' + Date.now().toString(36) + '_' + _creqCounter,
    });
  }

  /** §23/§24：写意图桥接进真实 ChangeSession（durable）。返回 assistant 消息（调用方 push）。
   *
   * AI-7D-R §7/§8 纠正（根因 #1）：
   * - 已落定（approved/blocked/no_action/rejected/stale/failed/cancelled）的会话**不再捕获**
   *   后续新变更——一律基于最新 canonical 新建会话，不再撞 PLAN_SOURCE_STALE、不吞消息。
   * - 仅对仍开放、正在审查/构建中的方案，把新文本作为真实修订反馈并入（后端会真重跑 agent）。
   * - requestChanges 若被告知会话已落定/过期，自动回退为新建会话。 */
  async function bridgeFromDirectChat(text: string, opts: {
    source?: 'team_chat' | 'floating_bar' | 'direct_agent_chat'; sourceAgentId?: string; sourceSessionId?: string;
    contextScope?: { sceneId?: string; shotId?: string; characterName?: string };
  }): Promise<AgentMessage[]> {
    const out: AgentMessage[] = [];
    // §15：活跃执行 + 新变更请求 → 确定性二选一，不静默改已批准计划
    if (executionActive.value) {
      pendingChangeChoice.value = text;
      pendingChangeOpts.value = opts;
      out.push(_msg('assistant',
        '当前计划正在执行（' + (execution.value?.stage || '') +
        '）。这条新修改请求如何处理？请在下方选择：等当前任务完成后处理，或停止当前任务并重新规划。'));
      return out;
    }
    const cur = session.value;
    // 开放中的方案（构建中或待审/待决）→ 真实修订反馈；落定/无会话 → 新建。
    const isOpen = !!cur && SESSION_OPEN_FOR_FEEDBACK.has(cur.status) && !SESSION_SETTLED.has(cur.status);
    if (isOpen) {
      try {
        await requestChanges(text);
        out.push(_msg('assistant',
          '已把这条修订反馈并入当前变更会话，正在重新整理方案。进度见下方运行卡。'));
        return out;
      } catch (e: any) {
        // 后端判定会话已落定/过期（CHANGE_SESSION_SETTLED / PLAN_SOURCE_STALE）→ 落到下方新建
        const code = e?.code || e?.message || '';
        if (!/SETTLED|STALE/i.test(String(code))) {
          throw e;
        }
        // 落到新建分支
      }
    }
    // 新变更流：基于最新 canonical 的全新会话
    await _startFreshSession(text, opts);
    if (noAction.value) {
      out.push(_msg('assistant',
        '我核对了当前项目：' + (noActionReason.value || '这条请求无需产生实际修改。')));
    } else if (session.value) {
      out.push(_msg('assistant',
        '正在由制作团队整理修改方案（变更会话 ' + session.value.session_id +
        '）。完成后会在这里呈现方案供你审查。'));
    } else if (error.value) {
      out.push(_msg('assistant', '创建变更会话失败：' + error.value, 'failed'));
    }
    return out;
  }

  /** §15 选择：queue（暂存待执行后）/ restart（停止当前 + 重新规划）。返回 assistant 消息。 */
  async function resolveChangeChoice(choice: 'queue' | 'restart'): Promise<AgentMessage[]> {
    const text = pendingChangeChoice.value;
    const opts = pendingChangeOpts.value;
    pendingChangeChoice.value = null;
    pendingChangeOpts.value = null;
    const out: AgentMessage[] = [];
    if (!text) return out;
    if (choice === 'restart') {
      out.push(_msg('assistant', '正在停止当前任务…'));
      await cancelExecution();
      const stopped = await waitForExecutionStopped(240000);
      if (!stopped) {
        out.push(_msg('assistant',
          '旧任务仍在停止中（生产任务收尾需要一点时间）。这条修改请求已暂存，请稍后重试或手动恢复。',
          'failed'));
        _queuedChangeRequests.value.push(text);
        return out;
      }
      preferredExecMode.value = CREATIVE_ONLY_RE.test(text) ? 'creative_only' : 'affected_production';
      _creqCounter += 1;
      await create(text, undefined, {
        source: opts?.source || 'direct_agent_chat',
        sourceAgentId: opts?.sourceAgentId,
        sourceSessionId: opts?.sourceSessionId,
        contextScope: opts?.contextScope,
        clientRequestId: 'creq_' + Date.now().toString(36) + '_' + _creqCounter,
      });
      out.push(_msg('assistant',
        '已停止当前任务，基于这条请求重新规划（新会话 ' + (session.value?.session_id || '') + '）。'));
    } else {
      out.push(_msg('assistant',
        '好的：当前任务继续执行，这条修改请求将在其完成后处理（已记录）。'));
      _queuedChangeRequests.value.push(text);
    }
    return out;
  }

  return {
    session, plan, decisions, execution, manifest, eligibility, busy, error, lastRequestText,
    isActive, isTerminal, readyForReview, needsDecision,
    approved, executionActive, executionBlocked, executionDone, executionStopping, creativeCommitted, needsExecute,
    noAction, noActionReason, settled,
    create, resume, loadPlan, submitDecision, approve, requestChanges, reject,
    execute, loadExecution, resumeExecution, cancelExecution, waitForExecutionStopped,
    restoreActive, onProjectChange, stopPoll,
    // AI-7D D2 / R
    pendingChangeChoice, queuedChangeRequests: _queuedChangeRequests,
    preferredExecMode, bridgeFromDirectChat, resolveChangeChoice,
  };
});
