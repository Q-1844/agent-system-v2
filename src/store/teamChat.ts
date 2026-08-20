/**
 * AI-7A Team Group Chat store（Pinia，setup style）。镜像 useAgentUiStore 风格。
 * polling-first：有 pending_inquiry 时每 POLL_MS 轮询 GET messages；producer 回复落盘后停止。
 * **不新增 realtime bus**（用户决策）。persistence 即真相（§77），轮询只是通知。
 * 项目切换 → stopPoll + 清空（§23/§79：team:{project_name} 项目隔离）。
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { apiClient } from '@/api';
import type { TeamMessage } from '@/api';
import { useProjectStore } from '@/store';

const POLL_MS = 2500;

export const useTeamChatStore = defineStore('teamChat', () => {
  const projectStore = useProjectStore();
  const messages = ref<TeamMessage[]>([]);
  const sending = ref(false);
  const error = ref('');
  const loaded = ref(false);
  let pollTimer: number | null = null;

  async function load() {
    const p = projectStore.projectName;
    if (!p) return;
    try {
      const data = await apiClient.getTeamThread(p);
      messages.value = data.messages || [];
      loaded.value = true;
      error.value = '';
      maybePoll();
    } catch (e: any) {
      error.value = '加载团队会话失败: ' + (e.message || e);
    }
  }

  async function poll() {
    const p = projectStore.projectName;
    if (!p) return;
    try {
      messages.value = await apiClient.getTeamMessages(p);
      if (!messages.value.some(m => m.pending_inquiry)) stopPoll();
    } catch { /* 静默，下次再试 */ }
  }

  function maybePoll() {
    if (messages.value.some(m => m.pending_inquiry)) startPoll();
    else stopPoll();
  }
  function startPoll() {
    if (pollTimer != null) return;
    pollTimer = window.setInterval(poll, POLL_MS);
  }
  function stopPoll() {
    if (pollTimer != null) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function send(content: string) {
    const p = projectStore.projectName;
    const text = content.trim();
    if (!p || !text || sending.value) return;
    sending.value = true;
    error.value = '';
    try {
      const res = await apiClient.sendTeamMessage(p, text);
      messages.value.push(res.message);   // user 消息（pending_inquiry=true）
      startPoll();                        // 轮询等 producer 回复落盘
    } catch (e: any) {
      error.value = '发送失败: ' + (e.message || e);
    } finally {
      sending.value = false;
    }
  }

  function onProjectChange() {
    stopPoll();
    messages.value = [];
    error.value = '';
    loaded.value = false;
  }

  /** §55/§57：显式恢复 blocked 的正式变更分析会话（后端重跑 failed 咨询，保留 completed）。
   * ready→把该消息更新为 analysis_card；否则更新 system 文本。 */
  async function resumeFormal(sessionId: string, messageId: string) {
    const p = projectStore.projectName;
    if (!p) return;
    sending.value = true;
    error.value = '';
    try {
      const sess = await apiClient.resumeChangeAnalysis(p, sessionId);
      const idx = messages.value.findIndex(m => m.message_id === messageId);
      if (idx < 0) return;
      if (sess.status === 'ready' && sess.plan_draft) {
        const pd = sess.plan_draft;
        const items = (rows: any[]) => (rows || []).map((d: any) => ({
          summary: d.summary, owner_agent: d.owner_agent_id, module: d.module }));
        messages.value[idx] = {
          ...messages.value[idx], message_type: 'analysis_card',
          text: '变更分析已就绪（analysis-only，可恢复/未应用）',
          card_data: {
            direct_changes: items(pd.direct_changes),
            downstream_changes: items(pd.downstream_changes),
            unchanged: pd.unchanged_scope || [],
            user_decisions_needed: pd.user_decisions_needed || [],
            execution_ready: pd.execution_ready,
            change_operations: [], formal: true,
            session_id: sessionId, status: sess.status,
          }, status: 'completed',
        } as TeamMessage;
      } else {
        messages.value[idx] = {
          ...messages.value[idx],
          text: `正式变更分析状态：${sess.status}${sess.error ? ' · ' + sess.error.slice(0, 160) : ''}`,
          status: sess.status === 'stale' || sess.status === 'failed' ? 'failed' : sess.status,
        } as TeamMessage;
      }
    } catch (e: any) {
      error.value = '恢复失败: ' + (e.message || e);
    } finally {
      sending.value = false;
    }
  }

  return { messages, sending, error, loaded, load, send, resumeFormal, onProjectChange, stopPoll };
});
