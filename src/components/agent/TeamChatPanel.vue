<!--
  AI-7A Team Group Chat Panel（§22/§58/§60/§76）。
  与既有 direct Agent 对话共存（§22）；polling-first（不新增 realtime bus）。
  变更请求 → analysis_card，渲染 §60 Plan 卡：Requested / Related / Unchanged / Needs decision，
  无 Apply/Execute 按钮（§60：AI-7A 不可执行）。
-->
<template>
  <div class="team-chat">
    <div class="tc-head">
      <span class="sub-title">Team Chat</span>
      <span class="tc-hint">项目：{{ projectName || '未选择' }} · 只读</span>
    </div>
    <div class="messages" ref="el">
      <div v-if="!store.messages.length" class="empty">向 Producer 提项目级问题或变更请求。<br/>例：「Xiaoyu 出现在哪些场景？」「把 Xiaoyu 的头发改长并修改结局」</div>
      <template v-for="m in store.messages" :key="m.message_id">
        <div class="msg" :class="m.message_type">
          <span class="msg-role">{{ label(m) }}</span>
          <span v-if="m.text" class="msg-content">{{ m.text }}</span>
          <!-- §60 Plan 卡 -->
          <div v-if="m.message_type === 'analysis_card' && m.card_data" class="plan-card">
            <div class="pc-sec" v-if="m.card_data.direct_changes?.length">
              <div class="pc-sec-t">Requested changes</div>
              <div v-for="(d, i) in m.card_data.direct_changes" :key="'d' + i" class="pc-item">
                • {{ d.summary }} <span class="pc-mod">[{{ d.owner_agent || d.module }}]</span>
              </div>
            </div>
            <div class="pc-sec" v-if="m.card_data.downstream_changes?.length">
              <div class="pc-sec-t">Related changes</div>
              <div v-for="(d, i) in m.card_data.downstream_changes" :key="'r' + i" class="pc-item">
                • {{ d.summary }} <span class="pc-mod">[{{ d.owner_agent || d.module }}]</span>
              </div>
            </div>
            <div class="pc-sec" v-if="m.card_data.unchanged?.length">
              <div class="pc-sec-t">Will remain unchanged</div>
              <div v-for="(u, i) in m.card_data.unchanged" :key="'u' + i" class="pc-item">
                • {{ u.entity_id || u.module }}
              </div>
            </div>
            <div class="pc-sec" v-if="m.card_data.user_decisions_needed?.length">
              <div class="pc-sec-t">Needs your decision</div>
              <div v-for="(dec, i) in m.card_data.user_decisions_needed" :key="'dec' + i" class="pc-item">• {{ dec }}</div>
            </div>
            <div class="pc-badge">{{ m.card_data.formal ? 'analysis-only · 可恢复 · 未应用' : 'analysis-only · 未应用' }}（AI-7A 不修改项目）</div>
          </div>
          <span v-if="m.pending_inquiry" class="msg-status ms-processing">分析中…</span>
          <span v-else-if="m.status === 'failed'" class="msg-status ms-failed">failed</span>
          <span v-else-if="m.status === 'blocked'" class="msg-status ms-processing">blocked</span>
          <button v-if="m.source_change_session_id && m.status === 'blocked'"
                  class="btn btn-ghost resume-btn" :disabled="store.sending"
                  @click="store.resumeFormal(m.source_change_session_id, m.message_id)">
            恢复分析
          </button>
        </div>
      </template>
    </div>
    <!-- AI-7D D4：Team Chat = AI-only 协作观察窗，用户不能在此发消息。
         与某位 Agent 沟通/提修改请求请打开其私聊（Agent Center Direct Chat / Floating Bar）。 -->
    <div class="tc-aionly-hint">AI 制作团队协作记录 · 与某位 Agent 沟通请打开其私聊</div>
    <div v-if="store.error" class="chat-err">{{ store.error }}</div>

    <!-- 活跃 change-session 时显示方案（观察；批准在 Direct Chat 进行）。
         AI-7D-R：no_action/已落定会话不再显示审查卡（历史事务，不捕获后续）。-->
    <ChangePlanReview v-if="cs.session && !cs.noAction && (!cs.settled || cs.executionActive)" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue';
import { useTeamChatStore } from '@/store/teamChat';
import { useChangeSessionStore } from '@/store/changeSession';
import { useProjectStore } from '@/store';
import type { TeamMessage } from '@/api';
import { useStickToBottom } from '@/composables/useStickToBottom';
import ChangePlanReview from '@/components/agent/ChangePlanReview.vue';

const store = useTeamChatStore();
const cs = useChangeSessionStore();
const projectStore = useProjectStore();
const projectName = computed(() => projectStore.projectName);

const draft = ref('');
const el = ref<HTMLElement | null>(null);
const stick = useStickToBottom(el);

const placeholder = '问 Producer（如：Xiaoyu 出现在哪些场景？/ 把 Xiaoyu 头发改长）';

function label(m: TeamMessage): string {
  if (m.message_type === 'user') return 'You';
  if (m.message_type === 'system') return 'System';
  if (m.message_type === 'analysis_card') return 'Producer · 变更分析';
  return m.agent_id === 'producer' ? 'Producer' : (m.agent_id || 'Agent');
}

async function send() {
  if (!draft.value.trim() || store.sending) return;
  const text = draft.value;
  draft.value = '';
  await store.send(text);
  // AI-7B：变更类请求 → 创建 ChangeSession（producer 经 team-chat 收到后启动提案流）
  // 检测变更动词（与后端 classify_request 同义）：改/修改/换成/删除/去掉/调整/替换/make/change/remove
  if (/改|修改|换成|变成|删除|删掉|去掉|调整|替换|重写|续写|make|change|remove|turn/i.test(text) && !cs.session) {
    await cs.create(text);
  }
  // 用户主动发送 → 贴底跟随。
  stick.pinToBottom();
}

// 新消息（轮询/异步到达）→ 仅在用户原本贴底时跟随，看历史时不强拉。
watch(() => store.messages.length, () => stick.followIfStuck());
// 项目切换：清状态 + 重载（§23/§79）
watch(projectName, async (n, o) => {
  if (n !== o) {
    store.onProjectChange();
    cs.onProjectChange();
    if (n) { await store.load(); await cs.restoreActive(); stick.pinToBottom(); }
  }
});

onMounted(async () => {
  stick.bind();
  // K3：先等项目身份恢复完成（F5 后正确项目），再恢复后端持久化的会话/执行真相
  await projectStore.identityReady;
  if (projectName.value) { await store.load(); await cs.restoreActive(); }
  stick.pinToBottom();
});
onBeforeUnmount(() => { stick.unbind(); store.stopPoll(); cs.stopPoll(); });
</script>

<style lang="scss" scoped>
.team-chat { flex: 1; display: flex; flex-direction: column; min-height: 320px;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; }
.tc-head { display: flex; justify-content: space-between; align-items: center; padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  .tc-hint { font-size: 11px; color: var(--text-muted); } }
.sub-title { font-size: 12px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: .5px; }

.messages { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.empty { color: var(--text-muted); font-size: 12px; padding: 24px; text-align: center; line-height: 1.6; }
.msg { display: flex; flex-direction: column; gap: 3px; max-width: 88%; padding: 8px 10px; border-radius: 8px; font-size: 13px; }
.msg.user { align-self: flex-end; background: var(--accent, #3b82f6); color: #fff; }
.msg.agent, .msg.analysis_card { align-self: flex-start; background: var(--bg-hover); color: var(--text-primary); }
.msg.system { align-self: center; background: transparent; color: var(--text-muted); font-size: 11px; font-style: italic; border: 1px dashed var(--border); }
.msg-role { font-size: 10px; opacity: .7; }
.msg-content { white-space: pre-wrap; }
.msg-status { font-size: 10px; margin-top: 2px; }
.ms-processing { color: #60a5fa; }
.ms-failed { color: #f87171; }

.plan-card { margin-top: 6px; display: flex; flex-direction: column; gap: 6px;
  border-top: 1px solid var(--border); padding-top: 6px; }
.pc-sec { display: flex; flex-direction: column; gap: 2px; }
.pc-sec-t { font-size: 10px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: .4px; margin-top: 2px; }
.pc-item { font-size: 12px; color: var(--text-secondary); }
.pc-mod { color: var(--text-muted); font-size: 10px; }
.pc-badge { font-size: 10px; color: var(--text-muted); margin-top: 4px; }

.composer { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--border); }
.tc-aionly-hint { padding: 10px 12px; border-top: 1px solid var(--border); font-size: 11px;
  color: var(--text-muted); text-align: center; background: var(--bg-root); }
.composer .input { flex: 1; }
.chat-err { color: #f87171; font-size: 11px; padding: 0 10px 8px; }
.resume-btn { align-self: flex-start; margin-top: 4px; padding: 4px 10px; }
.btn { padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 500; cursor: pointer; border: none; transition: all .15s; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn-primary { background: var(--accent); color: var(--accent-text, #fff); }
.btn-primary:hover:not(:disabled) { filter: brightness(1.1); }
.btn-ghost { background: none; color: var(--text-muted); }
.btn-ghost:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-secondary); }
</style>
