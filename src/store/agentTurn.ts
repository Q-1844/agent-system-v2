/**
 * Script Writer 流式 turn 的 app 级状态（Pinia）。
 *
 * 关键：turn 的流式连接与累积状态归【store】持有，不归 agents 页组件持有。
 * 这样用户在 Agent 回复中途切到剧本/其它模块（组件卸载），fetch 流不会中断，
 * Agent 在后台继续跑；切回来时 processItems / 流式正文 / 最终结果都还在。
 *
 * 生命周期：
 *   runTurn() → running=true，累积 activity/processItems/streamingText；
 *   turn.completed/failed → pendingMessage（含最终消息 + 执行过程快照），running=false；
 *   组件（agents 页）在可见时消费 pendingMessage 追加到会话并清除。
 * 停止：cancelTurn() 调后端 /turn/cancel（协作式，步骤边界停下），本地流保持连接
 *   接收 stopped 收尾消息，不本地 abort。
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiClient } from '@/api';
import type { AgentStreamEvent, ScriptWriterTurnResult } from '@/api';
import { streamAgentTurn } from '@/composables/useAgentTurnStream';

export interface ProcessItem {
  kind: 'reasoning' | 'tool';
  step_id: number;
  reasoningText?: string;
  reasoningMs?: number;
  call_id?: string;
  name?: string;
  label?: string;
  status?: 'running' | 'done' | 'failed';
  summary?: string;
  error?: string;
}

/** 完成时交给组件的挂账消息（最终 assistant 消息 + 本轮执行过程快照） */
export interface PendingMessage {
  message: any;
  processItems: ProcessItem[];
}

export const useAgentTurnStore = defineStore('agentTurn', () => {
  const running = ref(false);
  const activeProject = ref('');
  const activeSessionId = ref('');
  // Phase 3 · Production Room：事件订阅 tap（store 生命周期与流一致，切页面不丢）
  type TurnListener = (ev: AgentStreamEvent) => void;
  const turnListeners = new Set<TurnListener>();
  function onTurnEvent(fn: TurnListener): () => void {
    turnListeners.add(fn);
    return () => { turnListeners.delete(fn); };
  }
  const processItems = ref<ProcessItem[]>([]);
  const processExpanded = ref(false);
  const streamingText = ref('');
  const currentActivity = ref<{ type: string; label: string } | null>(null);
  const error = ref('');
  /** turn 完成后挂账：agents 页可见时取走追加到会话（切走期间完成也不丢） */
  const pendingMessage = ref<PendingMessage | null>(null);
  /**
   * 执行过程按 message_id 留存（app 级，路由切换/组件卸载不丢）。
   * 关键：_processItems 是纯前端字段、不持久化，切页面回来 openSession 从后端重载
   * 消息时会丢。这里以 message_id 为 key 单独留存，消息（无论挂账 push 还是后端重载）
   * 只要 id 一致就能渲染折叠条，不依赖消息对象身份。刷新页面才丢（与 reasoning 不持久化一致）。
   */
  const processItemsByMid = ref<Record<string, ProcessItem[]>>({});

  const completedSteps = computed(() =>
    processItems.value.filter(p => p.kind === 'tool' && (p.status === 'done' || p.status === 'failed')).length);

  function resetTurn() {
    processItems.value = [];
    processExpanded.value = false;
    streamingText.value = '';
    currentActivity.value = null;
    error.value = '';
  }

  function onEvent(ev: AgentStreamEvent) {
    // Phase 3 订阅 tap：Production Room store 等消费同一事件流（监听器异常不影响主流程）。
    for (const fn of turnListeners) {
      try { fn(ev); } catch { /* 订阅者自负 */ }
    }
    switch (ev.type) {
      case 'turn.started':
        resetTurn();
        currentActivity.value = { type: 'thinking', label: '思考中…' };
        break;
      case 'reasoning.started':
        currentActivity.value = { type: 'thinking', label: '思考中…' };
        processItems.value.push({ kind: 'reasoning', step_id: ev.step_id, reasoningText: '' });
        break;
      case 'reasoning.delta': {
        const it = processItems.value.find(p => p.kind === 'reasoning' && p.step_id === ev.step_id);
        if (it) it.reasoningText = (it.reasoningText || '') + ev.delta;
        break;
      }
      case 'reasoning.completed': {
        const it = processItems.value.find(p => p.kind === 'reasoning' && p.step_id === ev.step_id);
        if (it) it.reasoningMs = ev.duration_ms;
        break;
      }
      case 'tool.pending':
        currentActivity.value = { type: 'tool', label: `正在调用 · ${ev.label}…` };
        break;
      case 'tool.started':
        currentActivity.value = { type: 'tool', label: `正在${ev.label}…` };
        processItems.value.push({ kind: 'tool', step_id: ev.step_id, call_id: ev.call_id,
          name: ev.name, label: ev.label, status: 'running' });
        break;
      case 'tool.completed': {
        const it = processItems.value.find(p => p.kind === 'tool' && p.call_id === ev.call_id);
        if (it) { it.status = ev.ok ? 'done' : 'failed'; it.summary = ev.summary; }
        break;
      }
      case 'tool.failed': {
        const it = processItems.value.find(p => p.kind === 'tool' && p.call_id === ev.call_id);
        if (it) { it.status = 'failed'; it.error = ev.error; }
        break;
      }
      case 'assistant.delta':
        streamingText.value += ev.delta;
        currentActivity.value = null;
        break;
      case 'views.progress':
        // Character Designer 三视图长任务进度（6-20 分钟级）
        currentActivity.value = {
          type: 'tool',
          label: `正在生成 ${ev.character_name} 三视图 · ${ev.view}${ev.pct ? ` (${ev.pct}%)` : ''}…`,
        };
        break;
      case 'tool.progress':
        // Director 视觉生成等长任务进度（批次计数推进，如「已完成 3/8」）
        currentActivity.value = {
          type: 'tool',
          label: ev.message || `正在${ev.label ?? '执行任务'}…`,
        };
        break;
      case 'turn.completed':
      case 'turn.failed':
        currentActivity.value = null;
        break;
    }
  }

  async function runTurn(opts: {
    projectName: string; sessionId: string; content: string; thinking: boolean;
  }): Promise<void> {
    if (running.value) return;   // 互斥：同一时刻只跑一个 turn
    running.value = true;
    activeProject.value = opts.projectName;
    activeSessionId.value = opts.sessionId;
    resetTurn();
    try {
      const result: ScriptWriterTurnResult = await streamAgentTurn({
        projectName: opts.projectName,
        sessionId: opts.sessionId,
        content: opts.content,
        thinking: opts.thinking,
        onEvent,
      });
      // 最终消息挂账 + 附本轮执行过程（前端字段，不持久化）
      const itemsSnapshot = JSON.parse(JSON.stringify(processItems.value)) as ProcessItem[];
      pendingMessage.value = {
        message: result.message,
        processItems: itemsSnapshot,
      };
      // 按 message_id 留存执行过程：切页面回来消息从后端重载（无 _processItems）时，
      // 渲染层据此 id 找回过程折叠条。
      const mid = (result.message as any)?.message_id;
      if (mid) processItemsByMid.value[mid] = itemsSnapshot;
      streamingText.value = '';
    } catch (e: any) {
      if (e?.name === 'AbortError') { /* 用户取消本地流（非后台停止），静默 */ }
      else error.value = e?.message || String(e);
    } finally {
      running.value = false;
      currentActivity.value = null;
    }
  }

  /** 协作式停止：通知后端在步骤边界停下；本地流保持连接接收 stopped 收尾。 */
  async function cancelTurn(): Promise<void> {
    if (!running.value) return;
    try {
      await apiClient.cancelAgentTurn(activeProject.value, activeSessionId.value);
    } catch (e: any) {
      error.value = '停止失败: ' + (e?.message || e);
    }
  }

  /** 组件取走挂账消息后清除 */
  function consumePending(): PendingMessage | null {
    const p = pendingMessage.value;
    pendingMessage.value = null;
    return p;
  }

  /** 按 message_id 取本轮留存的执行过程（消息从后端重载后仍能找回折叠条）。 */
  function processItemsFor(mid: string): ProcessItem[] {
    return (mid && processItemsByMid.value[mid]) || [];
  }

  // 工具名→人类可读 label（与后端 _TOOL_LABELS 一致；trace 重建折叠条用）。
  // 2026-08-30：加入 Character Designer 的角色工具。
  const TOOL_LABELS: Record<string, string> = {
    'script.read': '读取剧本', 'script.update': '更新剧本',
    'story.list': '读取故事段', 'story.read': '读取故事段',
    'story.create': '创建故事段', 'story.move': '移动故事段',
    'story.update': '修改故事段', 'story.delete': '删除故事段',
    'story.create_many': '批量创建故事段', 'story.update_many': '批量修改故事段',
    'story.delete_many': '批量删除故事段', 'story.insert_many': '批量插入故事段',
    'character.list': '读取角色列表', 'character.read': '读取角色',
    'character.create': '创建角色', 'character.update': '修改角色',
    'character.delete': '删除角色', 'character.generate_views': '生成角色三视图',
    // Director（2026-08-30）：分镜工具
    'shot.list': '读取分镜', 'shot.read': '读取镜头',
    'shot.create': '创建镜头', 'shot.create_many': '批量创建镜头',
    'shot.update': '修改镜头', 'shot.update_many': '批量修改镜头',
    'shot.delete': '删除镜头', 'shot.move': '移动镜头',
    // Director Visual Production（2026-08-31）：视觉生成长任务
    'shot.generate_images': '生成分镜图', 'shot.generate_videos': '生成镜头视频',
    // Visual Review Tool V1（2026-09-06）：只读视觉审查
    'visual.review_image': '视觉审查',
    'web.search': '搜索资料',
    // Producer V1（Phase 1 delegate 工具；Phase 3：刷新后从 meta.trace 重建折叠条不再显示裸名）
    'project.inspect': '查看项目概览',
    'script_writer.delegate': '委派 Script Writer',
    'character_designer.delegate': '委派 Resource Agent（资源设计师）',
    'director.delegate': '委派 Director',
  };

  /**
   * 从后端持久化的 meta 重建执行过程折叠条（刷新页面后 pinia 清空、实时快照丢失，但
   * meta.trace（tool 轨迹）和 meta.reasonings（每步思考文本）都是落盘的）。
   * tool 从 trace 重建、reasoning 从 reasonings 重建，按 step 交错；
   * 实时快照（含 reasoning.delta 流式细节）优先保留，不覆盖。 */
  function hydrateProcessItemsFromMeta(mid: string, meta: any): void {
    if (!mid) return;
    if (processItemsByMid.value[mid]) return;        // 实时快照优先
    const trace: any[] = Array.isArray(meta?.trace) ? meta.trace : [];
    const reasonings: any[] = Array.isArray(meta?.reasonings) ? meta.reasonings : [];
    if (!trace.length && !reasonings.length) return;
    // 按 step 交错：同 step 的 reasoning 在前（先思考后调工具）
    const byStep = new Map<number, { reasoning?: any; tools: any[] }>();
    for (const r of reasonings) {
      const s = r.step ?? 0;
      const entry = byStep.get(s) || { tools: [] };
      entry.reasoning = r;
      byStep.set(s, entry);
    }
    for (const t of trace) {
      const s = t.step ?? 0;
      const entry = byStep.get(s) || { tools: [] };
      entry.tools.push(t);
      byStep.set(s, entry);
    }
    const items: ProcessItem[] = [];
    for (const step of [...byStep.keys()].sort((a, b) => a - b)) {
      const entry = byStep.get(step)!;
      if (entry.reasoning?.text) {
        items.push({ kind: 'reasoning', step_id: step,
          reasoningText: entry.reasoning.text,
          reasoningMs: entry.reasoning.duration_ms });
      }
      for (const t of entry.tools) {
        const label = TOOL_LABELS[t.tool] || t.tool;
        const failed = !t.ok || t.result === 'stale' || t.result === 'error';
        items.push({
          kind: 'tool', step_id: step, name: t.tool, label,
          status: failed ? 'failed' : 'done',
          summary: t.target ? `${label} · ${t.target}` : label,
          error: failed ? (t.error || undefined) : undefined,
        });
      }
    }
    processItemsByMid.value[mid] = items;
  }

  /** 兼容旧名（meta.trace 重建）。 */
  function hydrateProcessItemsFromTrace(mid: string, trace: any[]): void {
    hydrateProcessItemsFromMeta(mid, { trace });
  }

  function toggleProcess() { processExpanded.value = !processExpanded.value; }

  return {
    running, activeProject, activeSessionId, processItems, processExpanded,
    streamingText, currentActivity, error, pendingMessage, completedSteps,
    resetTurn, runTurn, cancelTurn, consumePending, processItemsFor, hydrateProcessItemsFromMeta, hydrateProcessItemsFromTrace, toggleProcess, onEvent, onTurnEvent,
  };
});
