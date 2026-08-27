/**
 * AI-7D-R · Agent Center Direct Chat 流式 turn 消费器（POST /turn/stream）。
 *
 * 不用浏览器 EventSource（它只能 GET）。用 fetch + ReadableStream + TextDecoder 手写 SSE：
 *   - chunk 边界可能切在任意字符位置 → 用 buffer 累积，按 \n\n 分帧
 *   - 一个 reader chunk 可能含多条 SSE；一条 SSE 也可能跨多个 reader chunk
 *   - 处理 [DONE] / JSON parse error / 网络中断 / AbortController
 *
 * 事件 onEvent(ev: AgentStreamEvent)：turn.started → reasoning.* / tool.* / assistant.delta
 *   → turn.completed(result) / turn.failed(error)。turn.completed.result 是现有完整 16 键结构。
 *
 * 生命周期：组件卸载 / 用户离开会话时调 controller.abort() 取消。
 * 参考 useJobStream 的清理思想，但 POST streaming 自己用 fetch reader 实现。
 */
import type { AgentStreamEvent, ScriptWriterTurnResult } from '@/api';

const API_BASE = 'http://localhost:8000';

export interface AgentTurnStreamOptions {
  projectName: string;
  sessionId: string;
  content: string;
  thinking: boolean;   // 发送瞬间 snapshot：整个 turn 固定
  onEvent: (ev: AgentStreamEvent) => void;
  signal?: AbortSignal;
}

/**
 * 发起流式 turn。返回最终结果（turn.completed.result）；turn.failed 或中断时 reject。
 * 调用方负责 AbortController（离开页面/切换会话时 abort）。
 */
export async function streamAgentTurn(opts: AgentTurnStreamOptions): Promise<ScriptWriterTurnResult> {
  const { projectName, sessionId, content, thinking, onEvent, signal } = opts;
  const url = `${API_BASE}/api/project/${encodeURIComponent(projectName)}/agents/sessions/${encodeURIComponent(sessionId)}/turn/stream`;

  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, thinking }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    const txt = await resp.text().catch(() => '');
    throw new Error(`流式 turn 请求失败 (${resp.status}): ${txt.slice(0, 200)}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let finalResult: ScriptWriterTurnResult | null = null;
  let failed: string | null = null;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE 帧以空行（\n\n）分隔；可能一 chunk 多帧，也可能半帧
      let sep: number;
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const ev = parseSseFrame(frame);
        if (ev) {
          onEvent(ev);
          if (ev.type === 'turn.completed') finalResult = ev.result;
          else if (ev.type === 'turn.failed') failed = ev.error;
        }
      }
    }
    // 尾部残余 buffer（无 \n\n 收尾）也尝试解析一次
    if (buffer.trim()) {
      const ev = parseSseFrame(buffer);
      if (ev) {
        onEvent(ev);
        if (ev.type === 'turn.completed') finalResult = ev.result;
        else if (ev.type === 'turn.failed') failed = ev.error;
      }
    }
  } finally {
    try { reader.releaseLock(); } catch (e) { /* 已释放 */ }
  }

  if (failed) throw new Error(failed);
  if (!finalResult) throw new Error('流式 turn 未返回 turn.completed');
  return finalResult;
}

/** 解析单个 SSE 帧（可能含多行 data:，按规范拼成一条 JSON）。返回事件或 null。 */
function parseSseFrame(frame: string): AgentStreamEvent | null {
  const dataLines: string[] = [];
  for (const line of frame.split('\n')) {
    const s = line.trimStart();
    if (s.startsWith('data:')) {
      dataLines.push(s.slice(5).trim());
    }
    // 忽略 event:/id:/注释行(:)/空行
  }
  if (!dataLines.length) return null;
  const payload = dataLines.join('\n');
  if (payload === '[DONE]') return null;
  try {
    return JSON.parse(payload) as AgentStreamEvent;
  } catch (e) {
    // 畸形/半截 JSON：跳过不中断整条流
    return null;
  }
}
