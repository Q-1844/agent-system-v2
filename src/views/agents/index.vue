<template>
  <div class="agent-center">
    <div class="toolbar">
      <h2>AI 团队 · Agent Center</h2>
      <span class="hint">当前项目：{{ projectName || '未选择' }}</span>
      <!-- AI-7D：变更会话状态 chip（点击开审查弹窗；blocked/ready/awaiting/approved 自动弹出）。
           之前审查卡内联在对话流且 blocked 态无拒绝按钮，会话卡死无逃生口。 -->
      <button v-if="cs.session && !cs.noAction" class="cs-chip" :class="'csst-' + cs.session.status"
              :title="cs.session.error || ''" @click="showReviewModal = true">
        变更方案 · {{ csStatusText }}
      </button>
      <!-- AI-7D-R · Script Writer 最小测试弹窗（隔离，单工具，不走审查流）-->
      <button class="swt-btn" @click="showSwTestModal = true">🧪 Script Writer 直接编辑测试</button>
      <button class="swt-btn" @click="showDirTestModal = true">🎬 Director 分镜测试</button>
    </div>

    <div class="body">
      <!-- 左：Team Chat 入口 + 8 个 Agent 卡 -->
      <aside class="agent-list">
        <div class="agent-card team-entry" :class="{ active: selectedId === '__team__' }"
             @click="selectTeam">
          <div class="agent-name">Team Chat</div>
          <div class="agent-role">项目级群聊 · Producer</div>
        </div>
        <div v-for="a in agents" :key="a.id"
             class="agent-card" :class="{ active: selectedId === a.id, disabled: !a.enabled }"
             @click="selectAgent(a.id)">
          <div class="agent-name">{{ a.name }}</div>
          <div class="agent-role">{{ a.role }}</div>
          <div class="agent-meta">
            <span class="profile">{{ a.model_profile?.name || '—' }}</span>
            <span class="dot" :class="{ on: a.enabled }"></span>
          </div>
        </div>
      </aside>

      <!-- 右：Team Chat 或 聊天/委派 -->
      <section v-if="selectedId === '__team__'" class="panel team-panel">
        <TeamChatPanel />
      </section>

      <section class="panel" v-else-if="selected">
        <div class="panel-head">
          <div>
            <span class="p-title">{{ selected.name }}</span>
            <span class="p-desc">{{ selected.description }}</span>
          </div>
          <div class="scope-row">
            <select v-model="scopeType" class="input">
              <option value="project">project</option>
              <option value="scene">scene</option>
              <option value="shot">shot</option>
            </select>
            <input v-model="scopeId" class="input scope-id" :placeholder="scopePlaceholder" />
            <button class="btn btn-ghost" @click="openSession">切换会话</button>
          </div>
        </div>

        <!-- Producer 委派区 -->
        <div v-if="selectedId === 'producer'" class="delegation">
          <div class="sub-title">Delegated Tasks</div>
          <div class="task-table">
            <div class="task-row task-head">
              <span>Target</span><span>Capability</span><span>Scope</span><span>Status</span><span>Result</span>
            </div>
            <div v-if="!tasks.length" class="empty">暂无委派任务</div>
            <div v-for="t in tasks" :key="t.task_id" class="task-row">
              <span>{{ t.target_agent }}</span>
              <span>{{ t.capability_id }}</span>
              <span>{{ t.scope_type }}:{{ t.scope_id }}</span>
              <span :class="'st-' + t.status">{{ t.status }}</span>
              <span class="result" :title="t.result_summary">{{ t.result_summary?.slice(0, 40) }}</span>
            </div>
          </div>

          <div class="sub-title">New Delegation</div>
          <div class="form-row">
            <select v-model="delegForm.target_agent" class="input" @change="onTargetChange">
              <option v-for="a in specialists" :key="a" :value="a">{{ a }}</option>
            </select>
            <select v-model="delegForm.capability_id" class="input">
              <option v-for="c in targetCapabilities" :key="c.id" :value="c.id">{{ c.id }}{{ c.can_execute ? '' : ' (无权)' }}</option>
            </select>
            <input v-model="delegForm.scope_id" class="input scope-id" placeholder="scope_id (如 s01)" />
            <button class="btn btn-primary" :disabled="delegating" @click="doDelegate">
              {{ delegating ? '执行中…' : '委派' }}
            </button>
          </div>
          <input v-model="delegForm.instruction" class="input full" placeholder="指令（如：检查 Scene 1 的分镜结构）" />
        </div>

        <!-- 聊天区 -->
        <div class="sub-title">Conversation（独立隔离）</div>
        <div class="chat">
          <div class="messages" ref="messagesEl">
            <div v-if="!session || !session.messages.length" class="empty">暂无消息。在下方输入开始对话。</div>
            <div v-for="(m, i) in session?.messages || []" :key="i"
                 class="msg" :class="m.role">
              <span class="msg-role">{{ m.role }}</span>
              <span class="msg-content">{{ m.content }}</span>
              <span v-if="m.status" class="msg-status" :class="'ms-' + m.status">{{ m.status }}</span>
            </div>
            <!-- AI-7D：变更会话审查移到 toolbar 弹窗（showReviewModal），不再内联在对话流。
                 queue/restart 二选一仍内联（执行中 + 新请求时）。 -->
            <div v-if="cs.pendingChangeChoice" class="change-choice">
              <div class="cc-q">当前计划正在执行，新修改请求如何处理？</div>
              <div class="cc-opts">
                <button class="btn btn-ghost" :disabled="sending" @click="onResolveChoice('queue')">让当前任务完成后处理</button>
                <button class="btn btn-ghost" :disabled="sending" @click="onResolveChoice('restart')">停止并重新规划</button>
              </div>
            </div>
          </div>
          <div class="composer">
            <input v-model="draft" class="input" placeholder="输入消息…"
                   @keyup.enter="send" :disabled="sending" />
            <button class="btn btn-primary" :disabled="sending || !draft.trim()" @click="send">
              {{ sending ? '…' : '发送' }}
            </button>
          </div>
          <div v-if="chatError" class="chat-err">{{ chatError }}</div>
        </div>
      </section>

      <section v-else class="panel empty-panel">
        <div class="empty">← 从左侧选择一个 Agent</div>
      </section>
    </div>

    <!-- AI-7D：变更方案审查弹窗（ChangePlanReview 内含拒绝/批准/请求修改；blocked 也能拒绝） -->
    <Teleport to="body">
      <div v-if="showReviewModal && cs.session" class="cpr-modal-overlay"
           @click.self="showReviewModal = false">
        <div class="cpr-modal">
          <div class="cpr-modal-head">
            <span class="cpr-modal-title">变更方案审查</span>
            <button class="cpr-modal-x" @click="showReviewModal = false">✕</button>
          </div>
          <div class="cpr-modal-body">
            <ChangePlanReview />
          </div>
        </div>
      </div>
    </Teleport>

    <!-- AI-7D-R · Script Writer 最小测试弹窗 -->
    <ScriptWriterTestModal :open="showSwTestModal" @close="showSwTestModal = false" />
    <!-- AI-7D-R · Director 分镜测试弹窗 -->
    <DirectorTestModal :open="showDirTestModal" @close="showDirTestModal = false" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue';
import { apiClient } from '@/api';
import type { AgentInfo, AgentCapability, AgentSession, AgentTask } from '@/api';
import { useProjectStore } from '@/store';
import { useChangeSessionStore } from '@/store/changeSession';
import { useStickToBottom } from '@/composables/useStickToBottom';
import TeamChatPanel from '@/components/agent/TeamChatPanel.vue';
import ChangePlanReview from '@/components/agent/ChangePlanReview.vue';
import ScriptWriterTestModal from '@/components/agent/ScriptWriterTestModal.vue';
import DirectorTestModal from '@/components/agent/DirectorTestModal.vue';

const store = useProjectStore();
const projectName = computed(() => store.projectName);
const cs = useChangeSessionStore();
// AI-7D：变更方案审查弹窗。状态变 blocked/ready/awaiting/approved 时自动弹出（让用户立刻可见可操作）。
const showReviewModal = ref(false);
const showSwTestModal = ref(false);
const showDirTestModal = ref(false);
const csStatusText = computed(() => {
  const map: Record<string, string> = {
    routing: '路由中', consulting: '咨询中', analyzing_dependencies: '分析依赖',
    awaiting_user_decision: '需决策', synthesizing_plan: '综合计划', validating_plan: '校验中',
    building_preview: '构建预览', ready_for_review: '待审查', approved_for_execution: '已批准',
    blocked: '阻塞', rejected: '已拒绝', stale: '已过期', failed: '失败', cancelled: '已取消',
    no_action: '无需改动',
  };
  return map[cs.session?.status || ''] || cs.session?.status || '';
});
let lastNoActionSid = '';
watch(() => cs.session?.status, (s) => {
  // AI-7D-R：no_action 是诚实终态，不弹审查卡；其余需用户注意的状态才弹
  if (s && ['blocked', 'ready_for_review', 'awaiting_user_decision', 'approved_for_execution'].includes(s)) {
    showReviewModal.value = true;
  }
  // AI-7D-R §10：no_action 异步到达时把诚实结论补进本会话转录（去重）
  if (s === 'no_action' && cs.session && cs.session.session_id !== lastNoActionSid) {
    lastNoActionSid = cs.session.session_id;
    const reason = (cs.session as any).no_action_reason || '我核对了当前项目，这条请求无需产生实际修改。';
    session.value?.messages.push({
      role: 'assistant', content: '我核对了当前项目：' + reason,
      status: null, created_at: Date.now() / 1000,
    });
  }
  if (s && s !== 'no_action') lastNoActionSid = '';
});
// AI-7D D2：change 意图检测（与 agentUi.CHANGE_INTENT_RE 同词表）
const CHANGE_INTENT_RE = /改|修改|换成|变成|变得|变为|删除|删掉|去掉|调整|替换|重写|续写|换回|恢复到|恢复成|让.{1,20}更|加(强|快|深)|增强|弱化|放大|缩小|拓展|扩写|扩充|扩展|补充|补全|加场|加戏|make\b|change\b|remove\b|turn\b/i;

const agents = ref<AgentInfo[]>([]);
const selectedId = ref<string>('');
const selected = computed(() => agents.value.find(a => a.id === selectedId.value) || null);

const scopeType = ref('project');
const scopeId = ref('');
const session = ref<AgentSession | null>(null);
const draft = ref('');
const sending = ref(false);
const chatError = ref('');
const messagesEl = ref<HTMLElement | null>(null);
// 智能贴底：打开会话跳最新；新消息时仅当用户原本贴底才跟随（查看历史时不强拉）。
const stick = useStickToBottom(messagesEl);
// messages 面板随选中 agent 切换而挂载；挂载后绑定 scroll 监听并跳到最新消息。
watch(messagesEl, (el) => { if (el) { stick.bind(); stick.pinToBottom(); } });
// 外部异步到达的消息（如变更结论 no_action 补录）：仅在用户贴底时跟随，看历史时不强拉。
watch(() => session.value?.messages?.length, () => { stick.followIfStuck(); });

// 委派
const tasks = ref<AgentTask[]>([]);
const delegating = ref(false);
const specialists = ['script_writer', 'character_designer', 'director', 'production_planner', 'shot_producer', 'continuity_reviewer', 'editor'];
const targetCaps = ref<AgentCapability[]>([]);
const delegForm = ref({
  target_agent: 'director', capability_id: 'storyboard.inspect',
  scope_id: 's01', instruction: '检查分镜结构',
});

const scopePlaceholder = computed(() =>
  scopeType.value === 'project' ? (projectName.value || 'project') :
  scopeType.value === 'scene' ? '如 s01' : '如 s01_c02');

const targetCapabilities = computed(() => targetCaps.value);

async function loadAgents() {
  try {
    agents.value = await apiClient.listAgents();
    if (!selectedId.value && agents.value.length) selectAgent(agents.value[0].id);
  } catch (e: any) {
    chatError.value = '加载 Agent 失败: ' + (e.message || e);
  }
}

async function selectAgent(id: string) {
  selectedId.value = id;
  // AI-7D-R §9：Direct Conversation 默认长存于 project 级（不随页面 scope 重建/碎片化）。
  // 用户仍可用上方 scope 选择器显式切到 scene/shot 会话。
  scopeType.value = 'project';
  scopeId.value = projectName.value || '';
  session.value = null;
  chatError.value = '';
  if (id === 'producer') loadTasks();
  await openSession();
  if (id !== 'producer') await loadTargetCaps(id);
}

function selectTeam() {
  selectedId.value = '__team__';
  chatError.value = '';
}

async function loadTargetCaps(agentId: string) {
  try { targetCaps.value = await apiClient.getAgentCapabilities(agentId); }
  catch (e: any) { targetCaps.value = []; }
}

async function onTargetChange() {
  delegForm.value.capability_id = 'storyboard.inspect';
  await loadTargetCaps(delegForm.value.target_agent);
  const first = targetCaps.value.find(c => c.can_execute);
  if (first) delegForm.value.capability_id = first.id;
}

async function openSession() {
  if (!projectName.value) { chatError.value = '请先选择项目'; return; }
  try {
    const sid = scopeId.value || projectName.value;
    session.value = await apiClient.createAgentSession(projectName.value, selectedId.value, scopeType.value, sid);
    // 打开会话 → 无条件定位到最新消息。
    stick.pinToBottom();
  } catch (e: any) {
    chatError.value = '打开会话失败: ' + (e.message || e);
  }
}

async function send() {
  if (!session.value || !draft.value.trim() || sending.value) return;
  const content = draft.value.trim();
  draft.value = '';
  // 乐观追加 user
  session.value.messages.push({ role: 'user', content, status: null, created_at: Date.now() / 1000 });
  sending.value = true;
  chatError.value = '';
  try {
    // AI-7D D2：变更意图 → 真 ChangeSession 桥接（复用 csStore.bridgeFromDirectChat，与
    // Floating Bar 同语义）；否则只读/对话 → 统一 /turn runtime（D1 真 inspect 接地）。
    // AI-7D-R：script_writer 等具备直接编辑工具的 Agent 跳过 ChangeSession 方案审核，
    // 直接发 /turn 由 agent 自己的 tool-loop 完成修改（LLM→tool→observation→回复）。
    const DIRECT_EDIT_AGENTS = new Set(['script_writer']);
    const isDirectEdit = DIRECT_EDIT_AGENTS.has(selectedId.value);
    if (CHANGE_INTENT_RE.test(content) && !isDirectEdit) {
      const msgs = await cs.bridgeFromDirectChat(content, {
        source: 'direct_agent_chat',
        sourceAgentId: selectedId.value,
        sourceSessionId: session.value.session_id,
      });
      session.value.messages.push(...msgs);
    } else {
      const result = await apiClient.agentTurn(projectName.value, session.value.session_id, content);
      session.value.messages.push(result.message);   // assistant AgentMessage（含 meta）
    }
    // 用户主动发消息 → 视为贴底，跟随到最新。
    stick.pinToBottom();
  } catch (e: any) {
    chatError.value = '发送失败: ' + (e.message || e);
  } finally {
    sending.value = false;
  }
}

async function loadTasks() {
  if (!projectName.value) return;
  try {
    tasks.value = await apiClient.listAgentTasks(projectName.value, { requester_agent: 'producer' });
  } catch (e: any) { /* ignore */ }
}

async function doDelegate() {
  if (!projectName.value || delegating.value) return;
  delegating.value = true;
  chatError.value = '';
  try {
    // §21: operation 从选中 capability 的 metadata 派生（production_plan.create -> execute，
    // inspect 类 -> inspect），不硬编码 inspect，避免 create 用错 operation。
    const cap = targetCaps.value.find(c => c.id === delegForm.value.capability_id);
    const op = cap?.operations?.length ? cap.operations[0] : 'inspect';
    await apiClient.createAgentTask(projectName.value, {
      requester_agent: 'producer',
      target_agent: delegForm.value.target_agent,
      scope_type: 'scene',
      scope_id: delegForm.value.scope_id,
      instruction: delegForm.value.instruction,
      capability_id: delegForm.value.capability_id,
      operation: op,
    });
    await loadTasks();
  } catch (e: any) {
    chatError.value = '委派失败: ' + (e.message || e);
  } finally {
    delegating.value = false;
  }
}

// AI-7D D2：活跃执行 + 新变更请求的二选一（委托 csStore.resolveChangeChoice）
async function onResolveChoice(choice: 'queue' | 'restart') {
  if (sending.value) return;
  sending.value = true;
  chatError.value = '';
  try {
    const msgs = await cs.resolveChangeChoice(choice);
    session.value?.messages.push(...msgs);
    stick.pinToBottom();
  } catch (e: any) {
    chatError.value = '处理失败: ' + (e.message || e);
  } finally {
    sending.value = false;
  }
}

onMounted(async () => {
  loadAgents();
  // AI-7D D2：恢复进行中的变更会话（F5 不丢真 ChangeSession 真相）
  if (projectName.value) { await cs.restoreActive(); }
});
</script>

<style lang="scss" scoped>
.agent-center { max-width: 1200px; height: calc(100vh - 88px); display: flex; flex-direction: column; }
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;
  h2 { font-size: 16px; font-weight: 600; color: var(--text-primary); }
  .hint { font-size: 12px; color: var(--text-muted); } }
.body { flex: 1; display: flex; gap: 16px; min-height: 0; }

.agent-list { width: 220px; display: flex; flex-direction: column; gap: 8px; overflow-y: auto; padding-right: 4px; }
.agent-card { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; cursor: pointer; transition: all .15s;
  &:hover { border-color: var(--border-hover, var(--accent)); }
  &.active { border-color: var(--accent); background: var(--bg-hover); }
  &.disabled { opacity: .5; } }
.agent-card.team-entry { border-style: dashed; margin-bottom: 4px;
  .agent-role { color: var(--accent); } }
.team-panel { padding: 0; }
.agent-name { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.agent-role { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.agent-meta { display: flex; justify-content: space-between; align-items: center; margin-top: 6px; }
.profile { font-size: 10px; color: var(--text-muted); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #6b7280; }
.dot.on { background: #4ade80; }

.panel { flex: 1; display: flex; flex-direction: column; gap: 12px; min-width: 0; overflow-y: auto; }
.empty-panel { align-items: center; justify-content: center; }
.empty { color: var(--text-muted); font-size: 13px; padding: 24px; text-align: center; }
.panel-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap;
  .p-title { font-size: 14px; font-weight: 600; color: var(--text-primary); margin-right: 8px; }
  .p-desc { font-size: 12px; color: var(--text-muted); } }
.scope-row { display: flex; gap: 6px; align-items: center; }

.sub-title { font-size: 12px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: .5px; }
.delegation { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.task-table { display: flex; flex-direction: column; font-size: 12px; }
.task-row { display: grid; grid-template-columns: 1.2fr 1.4fr 1.2fr 0.9fr 2fr; gap: 6px; padding: 6px 4px; border-bottom: 1px solid var(--border); color: var(--text-secondary); }
.task-row.task-head { color: var(--text-muted); font-weight: 600; }
.result { color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.st-completed { color: #4ade80; }
.st-failed { color: #f87171; }
.st-running { color: #60a5fa; }

.form-row { display: flex; gap: 6px; flex-wrap: wrap; }
.input { background: var(--bg-root); border: 1px solid var(--border); border-radius: 4px; padding: 6px 8px; font-size: 12px; color: var(--text-primary); outline: none; }
.input:focus { border-color: var(--accent); }
.scope-id { flex: 1; min-width: 100px; }
.full { width: 100%; }

.chat { flex: 1; display: flex; flex-direction: column; min-height: 240px; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; }
.messages { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.msg { display: flex; flex-direction: column; gap: 2px; max-width: 80%; padding: 8px 10px; border-radius: 8px; font-size: 13px; }
.msg.user { align-self: flex-end; background: var(--accent, #3b82f6); color: #fff; }
.msg.assistant { align-self: flex-start; background: var(--bg-hover); color: var(--text-primary); }
.msg-role { font-size: 10px; opacity: .7; }
/* 复用 ScriptWriterTestModal 的正文渲染：保留换行、长词折行，不引入 v-html/markdown 解析。
   不修改消息正文，只修渲染。 */
.msg-content { white-space: pre-wrap; word-break: break-word; line-height: 1.55; }
.msg-status { font-size: 10px; margin-top: 2px; }
.ms-not_configured { color: #fbbf24; }
.ms-failed { color: #f87171; }
.composer { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--border); }
.composer .input { flex: 1; }
.chat-err { color: #f87171; font-size: 11px; padding: 0 10px 8px; }

.change-choice { margin: 8px 0; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-surface); }
.cc-q { font-size: 12px; color: var(--text-primary); margin-bottom: 6px; }
.cc-opts { display: flex; gap: 8px; flex-wrap: wrap; }

.btn { padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 500; cursor: pointer; border: none; transition: all .15s; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn-primary { background: var(--accent); color: var(--accent-text, #fff); }
.btn-primary:hover:not(:disabled) { filter: brightness(1.1); }
.btn-ghost { background: none; color: var(--text-muted); }
.btn-ghost:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-secondary); }

/* AI-7D：变更会话状态 chip（toolbar，点击开审查弹窗） */
.cs-chip { margin-left: auto; padding: 4px 12px; font-size: 12px; font-weight: 600; border-radius: 12px;
  border: 1px solid var(--border); background: var(--bg-surface); color: var(--text-secondary); cursor: pointer; transition: all .15s; }
.cs-chip:hover { background: var(--bg-hover); }
.csst-blocked, .csst-failed, .csst-stale, .csst-rejected, .csst-cancelled { border-color: rgba(248,113,113,.5); color: #f87171; }
.csst-ready_for_review, .csst-approved_for_execution { border-color: rgba(74,222,128,.5); color: #4ade80; }
.csst-awaiting_user_decision { border-color: rgba(251,191,36,.5); color: #fbbf24; }
.csst-routing, .csst-consulting, .csst-analyzing_dependencies, .csst-synthesizing_plan, .csst-validating_plan, .csst-building_preview { border-color: rgba(96,165,250,.5); color: #60a5fa; }

/* AI-7D：变更方案审查弹窗（Teleport to body） */
.cpr-modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 1000;
  display: flex; align-items: center; justify-content: center; padding: 24px; }
.cpr-modal { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 12px;
  width: min(720px, 95vw); max-height: 88vh; display: flex; flex-direction: column; box-shadow: 0 20px 60px rgba(0,0,0,.4); }
.cpr-modal-head { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px;
  border-bottom: 1px solid var(--border); }
.cpr-modal-title { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.cpr-modal-x { background: none; border: none; color: var(--text-muted); font-size: 18px; cursor: pointer; padding: 4px 8px; border-radius: 4px; }
.cpr-modal-x:hover { background: var(--bg-hover); color: var(--text-primary); }
.cpr-modal-body { padding: 16px; overflow-y: auto; }
.swt-btn { margin-left: 8px; padding: 4px 12px; font-size: 12px; border-radius: 10px; border: 1px solid rgba(74,222,128,.5); color: #4ade80; background: rgba(74,222,128,.08); cursor: pointer; }
.swt-btn:hover { background: rgba(74,222,128,.16); }
</style>
