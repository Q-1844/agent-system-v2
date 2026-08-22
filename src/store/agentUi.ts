/**
 * Floating Agent UI store（Pinia，setup style）。
 * AI-6C §8：单一 UI 状态管理。位置持久化用归一化 viewport 坐标（localStorage），
 * 分辨率变化后不跑出屏幕（§6）。会话/消息数据继续来自 AI-6B Agent Session/Messages
 * API——本 store 不复制后端 Session，只缓存当前活跃会话的 messages 供 UI 渲染（§8/§29）。
 *
 * 复用（禁止重造）：apiClient.createAgentSession / sendAgentMessage / listAgents
 * （src/api/index.ts:1528-1588）。ScopeType 仅 project/scene/shot（src/agents/models.py:27-30）。
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiClient } from '@/api';
import type { AgentMessage, AgentInfo } from '@/api';
import { useProjectStore } from '@/store';
import { useChangeSessionStore } from '@/store/changeSession';

// AI-6C §9：前端轻量 context descriptor。本轮仅 project/scene/shot 是真实 scope；
// character / shots 在后端无对应 ScopeType，本轮降级（不伪造业务上下文，§9/§35）。
export type AgentUiContext =
  | { type: 'project' }
  | { type: 'scene'; sceneId: string; summary?: string }
  | { type: 'shot'; sceneId: string; shotId: string; description?: string }
  | { type: 'shots'; sceneId: string; shotIds: string[] }
  | { type: 'character'; name: string };

export type AgentUiState = 'collapsed' | 'compact' | 'expanded';

const POS_KEY = 'agnes:floating-agent:position:v1';
// §7：默认出生位置 = viewport 右下距边约 28px（归一化 {x,y}∈[0,1]，1=右下极限）。
const DEFAULT_POS = { x: 0.96, y: 0.92 };

/**
 * 把 UI context 解析成 AgentSession 的 (scope_type, scope_id)。
 * §9：前端只负责告诉系统用户指的是谁；真正上下文由后端 build_context 从 scope 读取。
 */
export function resolveScope(
  ctx: AgentUiContext | null,
  projectName: string,
): { scope_type: string; scope_id: string } {
  if (!ctx || ctx.type === 'project') return { scope_type: 'project', scope_id: projectName };
  if (ctx.type === 'scene') return { scope_type: 'scene', scope_id: ctx.sceneId };
  if (ctx.type === 'shot') return { scope_type: 'shot', scope_id: ctx.shotId };
  // 本轮降级：多镜头→场景级；角色→项目级（不伪造 character scope）。
  if (ctx.type === 'shots') return { scope_type: 'scene', scope_id: ctx.sceneId };
  if (ctx.type === 'character') return { scope_type: 'project', scope_id: projectName };
  return { scope_type: 'project', scope_id: projectName };
}

export const useAgentUiStore = defineStore('agentUi', () => {
  const projectStore = useProjectStore();
  const csStore = useChangeSessionStore();

  // ---- UI state（§5）----
  const uiState = ref<AgentUiState>('compact');
  const activeAgentId = ref('producer');                 // §5 默认 Producer
  const activeSessionId = ref<string | null>(null);
  const currentContext = ref<AgentUiContext | null>(null);
  // context 是否仍绑定当前 workspace（§30：切页面后 context 脱离时为 false，需视觉提示）
  const contextAttachedToWorkspace = ref(false);

  // ---- 位置（绝对 px，bar 绑定；持久化时归一化）----
  const posLeft = ref(0);
  const posTop = ref(0);
  const isDragging = ref(false);

  // ---- conversation ----
  const conversationOpen = ref(false);
  const draft = ref('');                 // composer 文本（quick action 预填用）
  const messages = ref<AgentMessage[]>([]);
  const sending = ref(false);
  const chatError = ref('');
  // §28 + AI-7C-H §4：running 是**派生态**——只由 (a) 本轮 chat HTTP 在途（sending）或
  // (b) 后端持久化的 ChangeSession/ChangeExecution 活跃态（changeSession store 轮询所得）
  // 推导。前端布尔不发明执行真相；LLM 话术（「我开始处理了」）永远不进入此判定。
  const running = computed(() =>
    sending.value || csStore.isActive || csStore.executionActive);

  // AI-7C-H §5：确定性变更意图检测（与后端 classify_request._CHANGE_VERBS 同族词表）。
  // 写意图经 Floating Bar 必须桥接进真实 ChangeSession 工作流（durable），
  // 而非止于只「谈论」修改的普通 AgentMessage（§23）。只读问答继续走普通 Agent 会话。
  const CHANGE_INTENT_RE = /改|修改|换成|变成|变得|变为|删除|删掉|去掉|调整|替换|重写|续写|让.{1,20}更|加(强|快|深)|增强|弱化|放大|缩小|make\b|change\b|remove\b|turn\b/i;

  /** §15：活跃执行 + 新变更请求 → 确定性二选一（不静默吞并进运行中的已批准计划）。 */
  const pendingChangeChoice = ref<string | null>(null);
  // 暂存的排队变更请求（queue 选择后保留；执行终态时提示用户提交）
  const _queuedChangeRequests = ref<string[]>([]);

  // ---- agent 元数据（§10 复用 Registry/API，不硬编码第二份）----
  const agents = ref<AgentInfo[]>([]);
  const activeAgent = computed(
    () => agents.value.find(a => a.id === activeAgentId.value) || null,
  );
  const currentScope = computed(
    () => resolveScope(currentContext.value, projectStore.projectName),
  );

  // ============ 位置（§6/§7）============
  function loadPosition(barW: number, barH: number) {
    let norm = DEFAULT_POS;
    try {
      const raw = localStorage.getItem(POS_KEY);
      if (raw) {
        const p = JSON.parse(raw);
        if (typeof p.x === 'number' && typeof p.y === 'number') norm = { x: p.x, y: p.y };
      }
    } catch { /* 坏数据忽略 */ }
    const w = Math.max(1, window.innerWidth - barW);
    const h = Math.max(1, window.innerHeight - barH);
    setPosition(norm.x * w, norm.y * h, barW, barH);
  }

  function savePosition(barW: number, barH: number) {
    const w = window.innerWidth - barW, h = window.innerHeight - barH;
    const x = w > 0 ? posLeft.value / w : 0;
    const y = h > 0 ? posTop.value / h : 0;
    try { localStorage.setItem(POS_KEY, JSON.stringify({ x, y })); } catch { /* ignore */ }
  }

  function setPosition(left: number, top: number, barW: number, barH: number) {
    const margin = 14;                                   // 12–16px safe margin（§6）
    const maxLeft = Math.max(margin, window.innerWidth - barW - margin);
    const maxTop = Math.max(margin, window.innerHeight - barH - margin);
    posLeft.value = Math.max(margin, Math.min(left, maxLeft));
    posTop.value = Math.max(margin, Math.min(top, maxTop));
  }

  function clampToViewport(barW: number, barH: number) {
    setPosition(posLeft.value, posTop.value, barW, barH);
  }

  function resetPosition(barW: number, barH: number) {
    const margin = 28;                                   // 24–32px（§7）
    setPosition(
      window.innerWidth - barW - margin,
      window.innerHeight - barH - margin,
      barW, barH,
    );
    savePosition(barW, barH);
  }

  // ============ UI state 切换 ============
  function openBar() {
    if (uiState.value === 'collapsed') uiState.value = 'compact';
    void loadAgents();
  }
  function collapse() { uiState.value = 'collapsed'; conversationOpen.value = false; }
  function setUiState(s: AgentUiState) { uiState.value = s; }
  function expandConversation() {
    conversationOpen.value = true;
    if (uiState.value !== 'expanded') uiState.value = 'expanded';
  }
  function closeConversation() {
    conversationOpen.value = false;
    if (uiState.value === 'expanded') uiState.value = 'compact';
  }
  function toggleConversation() {
    if (conversationOpen.value) closeConversation(); else expandConversation();
  }

  // ============ agent / context / session（§29 复用 get_or_create）============
  async function loadAgents() {
    if (agents.value.length) return;
    try { agents.value = await apiClient.listAgents(); }
    catch { /* 服务未就绪时静默，UI 仍可用 */ }
  }

  /** 按 (agent, scope) get_or_create 复用 session（§29）。失败软提示，不抛。 */
  async function ensureSession(): Promise<string | null> {
    const p = projectStore.projectName;
    if (!p) { chatError.value = '请先选择项目'; return null; }
    try {
      const { scope_type, scope_id } = currentScope.value;
      const s = await apiClient.createAgentSession(p, activeAgentId.value, scope_type, scope_id);
      activeSessionId.value = s.session_id;
      messages.value = s.messages || [];
      chatError.value = '';
      return s.session_id;
    } catch (e: any) {
      chatError.value = '打开会话失败: ' + (e.message || e);
      return null;
    }
  }

  async function setAgent(id: string) {
    if (activeAgentId.value === id) return;
    activeAgentId.value = id;
    activeSessionId.value = null;                        // 换 agent 重建会话
    messages.value = [];
    await ensureSession();
  }

  /** 业务现场注入 context（§9/§15）。切 context 重建会话。 */
  async function setContext(ctx: AgentUiContext) {
    currentContext.value = ctx;
    contextAttachedToWorkspace.value = true;
    activeSessionId.value = null;
    messages.value = [];
    await ensureSession();
  }

  /** 一次设 agent + context，只建一个 session（toolbar/Story-Director 用，避免两次 ensureSession）。 */
  async function setAgentContext(agentId: string, ctx: AgentUiContext) {
    activeAgentId.value = agentId;
    currentContext.value = ctx;
    contextAttachedToWorkspace.value = true;
    activeSessionId.value = null;
    messages.value = [];
    await ensureSession();
  }

  /** §17：清除 context（回退到项目级 scope，不伪造业务上下文）。 */
  async function clearContext() {
    currentContext.value = null;
    contextAttachedToWorkspace.value = false;
    activeSessionId.value = null;
    messages.value = [];
    await ensureSession();
  }

  /** 当前 UI context → ChangeSession context_scope（§9：页面上下文即解析指向）。
   * scene/shot/character 上下文分别映射；project 上下文无 scope。 */
  function _contextScope(): { sceneId?: string; shotId?: string; characterName?: string } | undefined {
    const ctx = currentContext.value;
    if (!ctx || ctx.type === 'project') return undefined;
    if (ctx.type === 'scene' || ctx.type === 'shots') return { sceneId: ctx.sceneId };
    if (ctx.type === 'shot') return { sceneId: ctx.sceneId, shotId: ctx.shotId };
    if (ctx.type === 'character') return { characterName: ctx.name };
    return undefined;
  }

  function _pushUserMsg(text: string) {
    messages.value.push({
      role: 'user', content: text, status: null, created_at: Date.now() / 1000,
    });
  }

  /** §23/§24：写意图桥接——进入真实 ChangeSession 工作流（durable），引用真实 session id。 */
  async function _bridgeChangeIntent(text: string) {
    const p = projectStore.projectName;
    if (!p) { chatError.value = '请先选择项目'; return; }
    _pushUserMsg(text);
    if (!conversationOpen.value) conversationOpen.value = true;
    // §15：已批准执行正在跑 → 确定性二选一，不静默改已批准计划
    if (csStore.executionActive) {
      pendingChangeChoice.value = text;
      messages.value.push({
        role: 'assistant', status: null, created_at: Date.now() / 1000,
        content: '当前计划正在执行（' + (csStore.execution?.stage || '') +
                 '）。这条新修改请求如何处理？请在下方选择：等当前任务完成后处理，或停止当前任务并重新规划。',
      });
      return;
    }
    const sessionAlive = !!csStore.session &&
      !['rejected', 'stale', 'failed', 'cancelled'].includes(csStore.session.status);
    if (sessionAlive) {
      // 提案阶段的活跃会话：新请求作为修改反馈并入同一会话（不开第二条并行变更流）
      await csStore.requestChanges(text);
      messages.value.push({
        role: 'assistant', status: null, created_at: Date.now() / 1000,
        content: '已把这条请求并入当前变更会话 ' + (csStore.session?.session_id || '') +
                 '，作为修改反馈重新整理方案。进度见下方运行卡。',
      });
      return;
    }
    // 新变更流：Producer=通用入口(floating_bar)；Specialist=专业上下文(direct_agent_chat)
    const source = activeAgentId.value === 'producer' ? 'floating_bar' : 'direct_agent_chat';
    await csStore.create(text, undefined, {
      source: source as 'floating_bar' | 'direct_agent_chat',
      sourceAgentId: activeAgentId.value,
      sourceSessionId: activeSessionId.value || undefined,
      contextScope: _contextScope(),
    });
    if (csStore.session) {
      messages.value.push({
        role: 'assistant', status: null, created_at: Date.now() / 1000,
        content: '正在由制作团队整理修改方案（变更会话 ' + csStore.session.session_id +
                 '）。完成后会在这里呈现方案供你审查。',
      });
    } else if (csStore.error) {
      messages.value.push({
        role: 'assistant', status: 'failed', created_at: Date.now() / 1000,
        content: '创建变更会话失败：' + csStore.error,
      });
    }
  }

  /** §15 选择：让当前执行完成后处理（请求暂存，执行结束后并入/重开）。 */
  async function resolveChangeChoice(choice: 'queue' | 'restart') {
    const text = pendingChangeChoice.value;
    pendingChangeChoice.value = null;
    if (!text) return;
    if (choice === 'restart') {
      await csStore.cancelExecution();
      await csStore.create(text, undefined, {
        source: activeAgentId.value === 'producer' ? 'floating_bar' : 'direct_agent_chat',
        sourceAgentId: activeAgentId.value,
        contextScope: _contextScope(),
      });
      messages.value.push({
        role: 'assistant', status: null, created_at: Date.now() / 1000,
        content: '已停止当前任务，基于这条请求重新规划（新会话 ' +
                 (csStore.session?.session_id || '') + '）。',
      });
    } else {
      // queue：保留当前执行到完成；新请求暂存为该会话的后续反馈（执行终态后由用户提交）
      messages.value.push({
        role: 'assistant', status: null, created_at: Date.now() / 1000,
        content: '好的：当前任务继续执行，这条修改请求将在其完成后处理（已记录）。',
      });
      _queuedChangeRequests.value.push(text);
    }
  }

  /** 发送消息（§5 路由）：变更意图 → ChangeSession 桥接；只读问题 → 普通 Agent 会话。 */
  async function send(content: string) {
    const p = projectStore.projectName;
    const text = content.trim();
    if (!text || sending.value) return;
    if (CHANGE_INTENT_RE.test(text)) {
      await _bridgeChangeIntent(text);
      return;
    }
    let sid = activeSessionId.value;
    if (!sid) {
      sid = await ensureSession();
      if (!sid) return;
    }
    _pushUserMsg(text);
    sending.value = true; chatError.value = '';
    try {
      const reply = await apiClient.sendAgentMessage(p, sid, text);
      messages.value.push(reply);
      if (uiState.value === 'collapsed') uiState.value = 'compact';
      if (!conversationOpen.value) conversationOpen.value = true;   // 有回复自动展开
    } catch (e: any) {
      chatError.value = '发送失败: ' + (e.message || e);
    } finally {
      sending.value = false;
    }
  }

  // ============ project 切换（§4：project 变化销毁当前聊天上下文）============
  function onProjectChange() {
    activeSessionId.value = null;
    messages.value = [];
    currentContext.value = null;
    contextAttachedToWorkspace.value = false;
    conversationOpen.value = false;
    chatError.value = '';
    pendingChangeChoice.value = null;
    _queuedChangeRequests.value = [];
  }

  return {
    // state
    uiState, activeAgentId, activeSessionId, currentContext, contextAttachedToWorkspace,
    posLeft, posTop, isDragging, conversationOpen, messages, sending, chatError, running,
    agents, draft, pendingChangeChoice, queuedChangeRequests: _queuedChangeRequests,
    // computed
    activeAgent, currentScope,
    // position
    loadPosition, savePosition, setPosition, clampToViewport, resetPosition,
    // ui state
    openBar, collapse, setUiState, expandConversation, closeConversation, toggleConversation,
    // agent/session
    loadAgents, ensureSession, setAgent, setContext, setAgentContext, clearContext,
    send, resolveChangeChoice, onProjectChange,
  };
});
