<template>
  <aside class="wf-chat">
    <div class="chat-head">
      <span class="chat-title">搭建助手</span>
      <span class="chat-mode" :title="backendOnline === false ? '后端未连接：仅本地规则' : `后端 Producer 文本模型：${props.producerModel ?? '…'}`">
        {{ backendOnline === false ? '本地规则' : `规则 + Producer${props.producerModel ? ` · ${props.producerModel}` : ''}` }}
      </span>
    </div>
    <div ref="scroller" class="chat-scroll">
      <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
        <div class="bubble">{{ m.text }}</div>
        <span v-if="m.meta" class="msg-meta">{{ m.meta }}</span>
      </div>
      <div v-if="pending" class="msg assistant">
        <div class="bubble pending"><span class="dot" /><span class="dot" /><span class="dot" />{{ pendingText }}</div>
      </div>
    </div>
    <div class="chips">
      <button v-for="c in CHIPS" :key="c" class="chip" :disabled="pending" @click="send(c)">{{ c }}</button>
    </div>
    <div class="chat-input-row">
      <input
        v-model="input"
        class="input"
        placeholder="描述你想要的工作流…"
        spellcheck="false"
        :disabled="pending"
        @keydown.enter="send()"
      />
      <button class="btn btn-primary btn-sm" :disabled="pending || !input.trim()" @click="send()">发送</button>
    </div>
    <p class="chat-foot">只编辑浏览器草稿，不触发任何执行。简单指令走本地规则；复杂描述在后端在线时交给 Producer（真实大模型）解析意图，再由本地按注册表落图。</p>
  </aside>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue';
import { apiClient } from '@/api';
import {
  parseLocal, buildProducerPrompt, extractIntentJson, intentToActions,
  describePlan, HELP_TEXT,
} from './assistant';
import type { AssistantAction, WorkflowAssistantController } from './assistant';
import type { WorkflowDraft } from './types';

const props = defineProps<{
  draft: WorkflowDraft;
  controller: WorkflowAssistantController;
  backendOnline: boolean | null;
  /** Producer 当前文本模型（如 agnes-3.0-flash），来自后端 Profile 解析 */
  producerModel?: string | null;
}>();

interface ChatMsg { role: 'user' | 'assistant'; text: string; meta?: string }

const messages = ref<ChatMsg[]>([]);
const input = ref('');
const pending = ref(false);
const pendingText = ref('');
const scroller = ref<HTMLElement | null>(null);

const CHIPS = ['搭一条从剧本到成片的标准链', '在导演和成片之间插两道审核', '给导演绑定接力校准', '清空画布'];

onMounted(() => {
  messages.value.push({ role: 'assistant', text: HELP_TEXT, meta: '本地规则' });
});

function scrollBottom() {
  nextTick(() => {
    scroller.value?.scrollTo({ top: scroller.value.scrollHeight, behavior: 'smooth' });
  });
}

/** 应用动作并生成含结构检查结果的续播说明 */
function applyActions(actions: AssistantAction[], via: string): string {
  const plan = describePlan(actions);
  const results: string[] = [];
  let hadFail = false;
  for (const a of actions) {
    let r: { ok: boolean; reason?: string; detail?: string } = { ok: true };
    try {
      switch (a.kind) {
        case 'clear': props.controller.clearAll(); break;
        case 'sample': props.controller.loadSample(); break;
        case 'check': {
          const c = props.controller.runCheck();
          r = { ok: true, detail: c.errors ? `${c.errors} 个错误、${c.warnings} 个警告` : c.warnings ? `${c.warnings} 个警告` : '通过' };
          break;
        }
        case 'addAgent': r = props.controller.addAgent(a.refId); break;
        case 'addSkill': r = props.controller.addSkill(a.refId); break;
        case 'addReview': r = props.controller.addReview(a.label); break;
        case 'addBoundary': r = props.controller.addBoundary(a.which); break;
        case 'chain': {
          const c = props.controller.chain(a.refIds, !!a.withBoundary);
          r = { ok: c.ok, reason: c.reason, detail: `新建 ${c.created} 节点 / ${c.connected} 条连线` };
          break;
        }
        case 'insertReviews': r = props.controller.insertReviews(a.afterRef, a.beforeRef, a.count, a.label); break;
        case 'bind': r = props.controller.bind(a.skillRefId, a.agentRefId); break;
      }
    } catch (e) {
      r = { ok: false, reason: e instanceof Error ? e.message : String(e) };
    }
    if (!r.ok) {
      hadFail = true;
      results.push(`⚠ ${r.reason ?? '执行失败'}`);
    } else if (r.detail) {
      results.push(r.detail);
    }
  }
  props.controller.layoutRefit();

  // 结构检查汇总（check 动作本身已有 detail，这里对结构性改动做总览）
  const structural = actions.some((a) => a.kind !== 'check');
  let summary = '';
  if (structural && !hadFail) {
    const c = props.controller.runCheck();
    summary = c.errors
      ? `结构检查：${c.errors} 个错误、${c.warnings} 个警告——首位：${c.issues.find((i) => i.level === 'error')?.message ?? ''}（右侧概览可定位）`
      : c.warnings
        ? `结构检查通过，含 ${c.warnings} 个警告`
        : '结构检查：通过 ✓';
  }
  const lines = [plan ? `已执行：${plan}。` : '', ...results, summary].filter(Boolean);
  return `${lines.join('\n')}\n（${via}）`;
}

async function ensureProducerSession(): Promise<string> {
  // 助手专用 producer project 级会话（与悬浮条/AI 团队页的会话隔离）
  const key = `wf_assistant_sid::${props.draft.projectId}`;
  const cached = localStorage.getItem(key);
  if (cached) {
    try {
      const sessions = await apiClient.listAgentSessions(props.draft.projectId);
      if (sessions.some((s) => s.session_id === cached && s.agent_id === 'producer')) return cached;
    } catch { /* 列表失败则尝试直接用缓存 sid */ return cached; }
  }
  const s = await apiClient.createAgentSession(props.draft.projectId, 'producer', 'project', props.draft.projectId);
  localStorage.setItem(key, s.session_id);
  return s.session_id;
}

async function send(preset?: string) {
  const text = (preset ?? input.value).trim();
  if (!text || pending.value) return;
  input.value = '';
  messages.value.push({ role: 'user', text });
  scrollBottom();

  // 1) 本地规则层
  const actions = parseLocal(text, props.draft);
  if (actions.length) {
    messages.value.push({ role: 'assistant', text: applyActions(actions, '本地规则解析'), meta: '本地规则' });
    scrollBottom();
    return;
  }

  // 2) 后端 Producer 解析层（后端在线才可用）
  if (props.backendOnline !== false) {
    pending.value = true;
    pendingText.value = 'Producer 正在解析需求…';
    scrollBottom();
    try {
      const sid = await ensureProducerSession();
      const turn = await apiClient.agentTurn(props.draft.projectId, sid, buildProducerPrompt(text, props.draft));
      const reply = turn.assistant_message ?? '';
      const intent = extractIntentJson(reply);
      if (intent) {
        const { actions: iActions, problems } = intentToActions(intent);
        if (iActions.length) {
          let out = applyActions(iActions, '后端 Producer 解析');
          if (problems.length) out += `\n⚠ ${problems.join('；')}`;
          if (intent.note) out = `理解：${intent.note}\n${out}`;
          // 副作用透明：助手只应改草稿；若本轮出现真实委派任务引用必须明示
          if (turn.linked_task_ids?.length) {
            out += `\n⚠⚠ 本轮出现了 ${turn.linked_task_ids.length} 个真实后台任务引用（${turn.linked_task_ids.join('、')}）——搭建助手不应触发任何执行，请到「AI 团队」核实`;
          }
          const meta = `后端 Producer · ${turn.agent_id}${props.producerModel ? ` · ${props.producerModel}` : ''}${turn.events?.length ? ` · 后台事件 ${turn.events.length} 条` : ''}`;
          messages.value.push({ role: 'assistant', text: out, meta });
          scrollBottom();
          return;
        }
        messages.value.push({
          role: 'assistant',
          text: problems.length
            ? `Producer 的解析无法落图：${problems.join('；')}。换个说法试试，或用下方快捷指令。`
            : 'Producer 认为无需改动（chain/reviews/bindings 均为空）。换个说法试试。',
          meta: '后端 Producer',
        });
        scrollBottom();
        return;
      }
      // 模型没有给 JSON：把它的原话展示（可能是追问），不假装成功
      messages.value.push({
        role: 'assistant',
        text: reply.trim()
          ? `Producer 没有返回可执行的搭建意图。它的回复：\n${reply.trim().slice(0, 500)}`
          : 'Producer 返回为空。请换个说法，或使用下方快捷指令。',
        meta: '后端 Producer · 未解析出 JSON',
      });
      scrollBottom();
      return;
    } catch (e) {
      messages.value.push({
        role: 'assistant',
        text: `后端解析失败（${e instanceof Error ? e.message : String(e)}）。可改用本地规则指令，见「你能做什么」。`,
        meta: '后端不可用',
      });
      scrollBottom();
      return;
    } finally {
      pending.value = false;
    }
  }

  // 3) 兜底
  messages.value.push({
    role: 'assistant',
    text: `这条我暂时没看懂（后端未连接，无法转给 Producer 解析）。${HELP_TEXT}`,
    meta: '本地规则',
  });
  scrollBottom();
}

defineExpose({ send });
</script>

<style lang="scss" scoped>
.wf-chat {
  width: 296px;
  flex-shrink: 0;
  border-left: 1px solid var(--border);
  background: var(--bg-root);
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.chat-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px 10px;
  border-bottom: 1px solid var(--border);

  .chat-title { font-size: 13px; font-weight: 600; color: var(--text-primary); }
  .chat-mode {
    margin-left: auto;
    font-size: 10px;
    color: var(--text-faint);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1px 6px;
  }
}
.chat-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.msg {
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-width: 100%;

  &.user { align-items: flex-end; }
  &.assistant { align-items: flex-start; }
}
.bubble {
  font-size: 12px;
  line-height: 1.7;
  padding: 8px 11px;
  border-radius: 8px;
  white-space: pre-wrap;
  word-break: break-word;

  .user & { background: var(--bg-active); color: var(--text-primary); }
  .assistant & { background: var(--bg-surface); border: 1px solid var(--border); color: var(--text-secondary); }
}
.bubble.pending {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--text-muted);

  .dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--text-faint);
    animation: wfBlink 1.2s infinite;

    &:nth-child(2) { animation-delay: 0.2s; }
    &:nth-child(3) { animation-delay: 0.4s; }
  }
}
@keyframes wfBlink {
  0%, 60%, 100% { opacity: 0.3; }
  30% { opacity: 1; }
}
.msg-meta {
  font-size: 10px;
  color: var(--text-faint);
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  padding: 8px 14px;
  border-top: 1px solid var(--border);

  .chip {
    font-size: 11px;
    line-height: 20px;
    padding: 0 9px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: none;
    color: var(--text-muted);
    cursor: pointer;
    transition: border-color 0.15s ease, color 0.15s ease;

    &:hover:not(:disabled) { border-color: var(--border-hover); color: var(--text-primary); }
    &:disabled { opacity: 0.5; cursor: not-allowed; }
  }
}
.chat-input-row {
  display: flex;
  gap: 6px;
  padding: 8px 14px 6px;

  .input { flex: 1; min-width: 0; }
}
.chat-foot {
  margin: 0;
  padding: 0 14px 10px;
  font-size: 10px;
  line-height: 1.6;
  color: var(--text-faint);
}
.input {
  height: 30px;
  padding: 0 10px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text-primary);
  font-size: 12px;
  outline: none;

  &:focus { border-color: var(--border-hover); }
  &::placeholder { color: var(--text-faint); }
  &:disabled { opacity: 0.55; }
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border: none;
  border-radius: 6px;
  background: none;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;

  &:disabled { opacity: 0.45; cursor: not-allowed; }
}
.btn-sm { height: 30px; padding: 0 10px; font-size: 12px; }
.btn-primary {
  background: var(--accent);
  color: var(--accent-text);

  &:hover:not(:disabled) { background: var(--accent-hover); color: var(--accent-text); }
}
</style>
