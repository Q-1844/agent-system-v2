/**
 * 全局任务队列 SSE 订阅（阶段 4）
 *
 * 三铁律：
 *  - snapshot 事件 -> store.replaceAll（整体替换；刷新/重连/降级轮询都用这个）
 *  - job 事件     -> store.upsertJob（单条 UPSERT）
 *  - 具名事件用 addEventListener，不用 onmessage（否则漏掉所有数据）
 *
 * 心跳：后端每 15s 发 `: keepalive` 注释帧，EventSource 不触发任何监听器，自动忽略。
 *
 * 降级轮询：连续 3 次 onerror -> 关 SSE -> 每 5s GET /jobs 整体替换；
 * 轮询期持续尝试重连 SSE，一旦收到 snapshot 自动回切、停轮询。
 *
 * 切项目：watch projectRef，项目变化自动 close 旧连接 + 新 snapshot 替换。
 */
import { watch, onMounted, onUnmounted, type Ref } from 'vue';
import { apiClient } from '@/api';
import { useJobStore } from '@/store/jobs';

const API_BASE = 'http://localhost:8000';

export function useJobStream(projectRef: Ref<string>) {
  const store = useJobStore();
  let es: EventSource | null = null;
  let pollTimer: number | null = null;
  let reconnectTimer: number | null = null;
  let recoveryTimer: number | null = null;
  let heartbeatTimer: number | null = null;
  let failedStreak = 0;
  let currentProject = '';

  function streamUrl(p: string) {
    return `${API_BASE}/api/jobs/stream?project=${encodeURIComponent(p)}`;
  }

  function degradeToPolling(reason: string) {
    console.log(`[SSE] degrade -> polling (${reason})`);
    if (es) { try { es.close(); } catch (e) {} es = null; }
    if (recoveryTimer) { clearTimeout(recoveryTimer); recoveryTimer = null; }
    failedStreak = 0;
    store.setConnected(false);
    stopHeartbeat();
    startPolling();
  }

  /** 🔴 前端主动心跳：每 10s ping /api/health。物理断网时 SSE 的 TCP 不会立即断、
   * onerror 不触发，纯靠 SSE 检测不到断网。心跳 ping 失败 = 网络断 -> 降级轮询。 */
  function startHeartbeat() {
    if (heartbeatTimer != null) return;
    heartbeatTimer = window.setInterval(async () => {
      if (!es) return;  // 已降级轮询，不心跳（轮询自己探活）
      try {
        const r = await fetch(`${API_BASE}/api/health`);
        if (!r.ok) throw new Error(`health ${r.status}`);
      } catch (e) {
        console.log('[SSE] heartbeat ping 失败，判定网络断开', e);
        degradeToPolling('heartbeat ping failed');
      }
    }, 10000);
  }
  function stopHeartbeat() {
    if (heartbeatTimer != null) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
  }

  function connect() {
    close();
    currentProject = projectRef.value;
    if (!currentProject) return;
    console.log(`[SSE] connect project=${currentProject}`);
    store.setConnected(false);
    try {
      es = new EventSource(streamUrl(currentProject));
    } catch (e) {
      startPolling();
      return;
    }
    startHeartbeat();
    // 🔴 snapshot = REPLACE
    es.addEventListener('snapshot', (ev: MessageEvent) => {
      try {
        const arr = JSON.parse(ev.data);
        console.log(`[SSE] snapshot ${Array.isArray(arr) ? arr.length : '?'} jobs`);
        store.replaceAll(arr);
        store.setConnected(true);
        failedStreak = 0;
        if (recoveryTimer) { clearTimeout(recoveryTimer); recoveryTimer = null; }
      } catch (e) { /* ignore parse */ }
    });
    // 🔴 job = UPSERT
    es.addEventListener('job', (ev: MessageEvent) => {
      try {
        const j = JSON.parse(ev.data);
        store.upsertJob(j);
        if (j.queue) store.setQueue(j.queue);
      } catch (e) { /* ignore */ }
    });
    es.onerror = () => {
      failedStreak++;
      console.log(`[SSE] onerror failedStreak=${failedStreak} readyState=${es?.readyState}`);
      // 🔴 onerror 后 8s 没收到 snapshot（没恢复）-> 降级轮询。
      // 物理断网时 onerror 不触发（TCP 保持），靠 heartbeat 兜底；此处兜底 onerror 触发但未到 3 次的场景。
      if (recoveryTimer) clearTimeout(recoveryTimer);
      recoveryTimer = window.setTimeout(() => {
        recoveryTimer = null;
        if (es && es.readyState !== EventSource.OPEN) {
          degradeToPolling('recovery timeout 8s');
        }
      }, 8000);
      if (failedStreak >= 3) {
        degradeToPolling('onerror x3');
      }
    };
  }

  function startPolling() {
    if (pollTimer != null) return;
    store.setConnected(false);
    const poll = async () => {
      if (!currentProject) return;
      try {
        const r = await apiClient.listJobs(currentProject);
        store.replaceAll(r.jobs);          // 整体替换（同 snapshot 语义）
        if (r.queue) store.setQueue(r.queue);
      } catch (e) { /* ignore，下个周期再试 */ }
      tryReconnectSSE();                   // 轮询期持续探活 SSE
    };
    poll();
    pollTimer = window.setInterval(poll, 5000);
  }

  function stopPolling() {
    if (pollTimer != null) { clearInterval(pollTimer); pollTimer = null; }
  }

  /** 轮询期探活：开一个临时 EventSource，收到 snapshot 即回切 SSE */
  function tryReconnectSSE() {
    if (es || reconnectTimer || !currentProject) return;
    const test = new EventSource(streamUrl(currentProject));
    const onSnap = (ev: MessageEvent) => {
      try { store.replaceAll(JSON.parse(ev.data)); } catch (e) { /* ignore */ }
      console.log('[SSE] reconnect snapshot -> 回切 SSE');
      cleanup();
      test.close();
      stopPolling();
      failedStreak = 0;
      connect();                           // 回切 SSE（connect 里 startHeartbeat）
    };
    const cleanup = () => {
      test.removeEventListener('snapshot', onSnap);
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    };
    test.addEventListener('snapshot', onSnap);
    test.onerror = () => { cleanup(); try { test.close(); } catch (e) {} };
    // 5s 内没收到 snapshot 放弃本次（下个轮询周期再探）
    reconnectTimer = window.setTimeout(cleanup, 5000);
  }

  function close() {
    if (es) { es.close(); es = null; }
    stopPolling();
    stopHeartbeat();
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    if (recoveryTimer) { clearTimeout(recoveryTimer); recoveryTimer = null; }
  }

  // 切换项目自动重连
  watch(projectRef, () => { if (projectRef.value !== currentProject) connect(); });

  onMounted(connect);
  onUnmounted(close);

  return { reconnect: connect, close };
}
