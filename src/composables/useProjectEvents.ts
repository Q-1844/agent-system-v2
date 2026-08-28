/**
 * 项目级实时事件 SSE 订阅（AI-7D-R）。
 *
 * app 级（在 MainLayout 挂载，随项目切换自动重连）——不随 agents/剧本页组件卸载，
 * 所以 Agent 后台跑任务时切页面，锁/变更事件持续接收。
 *
 * 事件帧统一 `data: {json}\n\n`（不用具名 event，onmessage 收到全部）：
 *   首帧 {type:"snapshot",locks:[...]} → store.applySnapshot
 *   后续 resource.locked / resource.unlocked / story.changed → store.applyEvent
 * 心跳 `: keepalive` 是注释帧，EventSource 自动忽略。
 * 降级：连续 onerror 关 SSE → 每 5s 轮询项目锁状态接口（若有）；持续尝试重连。
 */
import { watch, onUnmounted, type Ref } from 'vue';
import { useProjectEventsStore } from '@/store/projectEvents';

const API_BASE = 'http://localhost:8000';

export function useProjectEvents(projectRef: Ref<string>) {
  const store = useProjectEventsStore();
  let es: EventSource | null = null;
  let reconnectTimer: number | null = null;
  let stopped = false;

  function streamUrl(p: string) {
    return `${API_BASE}/api/project/${encodeURIComponent(p)}/events`;
  }

  function close() {
    if (es) { try { es.close(); } catch (e) { /* ignore */ } es = null; }
    store.setConnected(false);
  }

  function connect(p: string) {
    close();
    if (!p) return;
    store.setProject(p);
    es = new EventSource(streamUrl(p));

    es.onmessage = (e) => {
      let ev: any;
      try { ev = JSON.parse(e.data); } catch (err) { return; }
      if (ev.type === 'snapshot') {
        store.applySnapshot(ev.locks || []);
      } else {
        store.applyEvent(ev);
      }
    };

    es.onopen = () => store.setConnected(true);

    es.onerror = () => {
      // 网络断开/服务重启：EventSource 会自动重连；标记未连接。
      store.setConnected(false);
    };
  }

  watch(projectRef, (p) => {
    if (stopped) return;
    connect(p || '');
  }, { immediate: true });

  onUnmounted(() => {
    stopped = true;
    close();
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  });
}
