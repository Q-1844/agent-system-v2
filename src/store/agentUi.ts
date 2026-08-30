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
import { ref, computed, watch } from 'vue';
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
  const CHANGE_INTENT_RE = /改|修改|换成|变成|变得|变为|删除|删掉|去掉|调整|替换|重写|续写|换回|恢复到|恢复成|让.{1,20}更|加(强|快|深)|增强|弱化|放大|缩小|拓展|扩写|扩充|扩展|补充|补全|加场|加戏|make\b|change\b|remove\b|turn\b/i;

  /** §15：活跃执行 + 新变更请求 → 确定性二选一。AI-7D D2：状态搬到 csStore（共享），
   * agentUi 侧 computed 镜像，AgentConversationSurface 仍用 store.pendingChangeChoice。 */
  const pendingChangeChoice = computed(() => csStore.pendingChangeChoice);
  // 暂存的排队变更请求（queue 选择后保留）；镜像 csStore。
  const _queuedChangeRequests = computed(() => csStore.queuedChangeRequests);

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

  /**
   * AI-7D-R §9：解析该 agent 的长存 Direct Conversation（project 级，稳定 conversation_id）。
   * 页面 context（scene/shot/character）是 per-turn 元数据，用于变更范围定位（_contextScope），
   * 不再作为会话身份键——切换页面/选中实体不再重建、不再清空对话历史。
   * 失败软提示，不抛。
   */
  async function ensureSession(): Promise<string | null> {
    const p = projectStore.projectName;
    if (!p) { chatError.value = '请先选择项目'; return null; }
    try {
      const s = await apiClient.createAgentSession(p, activeAgentId.value, 'project', p);
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
    activeSessionId.value = null;                        // 换 agent 换成长存会话
    messages.value = [];
    await ensureSession();
  }

  /** 业务现场注入 context（§9/§15）。AI-7D-R：只更新 per-turn 上下文，不重建会话。 */
  async function setContext(ctx: AgentUiContext) {
    currentContext.value = ctx;
    contextAttachedToWorkspace.value = true;
    if (!activeSessionId.value) await ensureSession();
  }

  /** 一次设 agent + context（toolbar/Story-Director 用）。会话仍按 agent 长存。 */
  async function setAgentContext(agentId: string, ctx: AgentUiContext) {
    activeAgentId.value = agentId;
    currentContext.value = ctx;
    contextAttachedToWorkspace.value = true;
    if (!activeSessionId.value) {
      messages.value = [];
      await ensureSession();
    }
  }

  /** §17：清除 context（回退到项目级，不伪造业务上下文）。不清空对话。 */
  async function clearContext() {
    currentContext.value = null;
    contextAttachedToWorkspace.value = false;
    if (!activeSessionId.value) await ensureSession();
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

  /** §23/§24：写意图桥接——委托 csStore.bridgeFromDirectChat（共享逻辑，Floating Bar +
   * Agent Center 同语义），push 返回的 assistant 消息。活跃执行时 csStore.pendingChangeChoice
   * 置位供 AgentConversationSurface 渲染二选一。 */
  async function _bridgeChangeIntent(text: string) {
    const p = projectStore.projectName;
    if (!p) { chatError.value = '请先选择项目'; return; }
    _pushUserMsg(text);
    if (!conversationOpen.value) conversationOpen.value = true;
    const source = activeAgentId.value === 'producer' ? 'floating_bar' : 'direct_agent_chat';
    const msgs = await csStore.bridgeFromDirectChat(text, {
      source,
      sourceAgentId: activeAgentId.value,
      sourceSessionId: activeSessionId.value || undefined,
      contextScope: _contextScope(),
    });
    messages.value.push(...msgs);
  }

  /** §15 二选一：委托 csStore.resolveChangeChoice（queue/restart 逻辑集中），push 返回消息。 */
  async function resolveChangeChoice(choice: 'queue' | 'restart') {
    const msgs = await csStore.resolveChangeChoice(choice);
    messages.value.push(...msgs);
  }

  /** 发送消息（§5 路由）：变更意图 → ChangeSession 桥接；只读问题 → 普通 Agent 会话。
   *  AI-7D-R：具备直接编辑工具的 Agent（script_writer / character_designer / director）
   *  不走 ChangeSession 方案审核，直接发 /turn 由 agent 自己的 tool-loop 完成修改
   *  （LLM→tool→observation→回复）。 */
  const DIRECT_EDIT_AGENTS = new Set(['script_writer', 'character_designer', 'director']);
  async function send(content: string) {
    const p = projectStore.projectName;
    const text = content.trim();
    if (!text || sending.value) return;
    if (CHANGE_INTENT_RE.test(text) && !DIRECT_EDIT_AGENTS.has(activeAgentId.value)) {
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
      // AI-7D D1：只读/对话/查询经统一 /turn runtime（真 inspect 接地，非 legacy 盲聊）。
      // 变更意图已在上方 _bridgeChangeIntent 桥接进 ChangeSession（D2 细化）。
      const result = await apiClient.agentTurn(p, sid, text);
      messages.value.push(result.message);   // assistant AgentMessage（含 meta：intent/evidence_refs）
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
    // pendingChangeChoice/queuedChangeRequests 由 csStore.onProjectChange 重置（D2 搬到 csStore）
  }

  // AI-7D-R §10：变更会话收敛到 no_action 时，把诚实结论补进对话流（轮询异步到达，
  // 非 bridge 同步返回）。按 session_id 去重，避免重复追加。
  let lastNoActionSid = '';
  watch(() => csStore.session?.status, (s) => {
    if (s === 'no_action' && csStore.session && csStore.session.session_id !== lastNoActionSid) {
      lastNoActionSid = csStore.session.session_id;
      const reason = (csStore.session as any).no_action_reason || '我核对了当前项目，这条请求无需产生实际修改。';
      messages.value.push({
        role: 'assistant', content: '我核对了当前项目：' + reason,
        status: null, created_at: Date.now() / 1000,
      });
    }
    if (s && s !== 'no_action') lastNoActionSid = '';
  });

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
