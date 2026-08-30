/**
 * 项目级实时事件 SSE 订阅（AI-7D-R）。
 *
 * app 级（在 MainLayout 挂载，随项目切换自动重连）——不随 agents/剧本页组件卸载，
 * 所以 Agent 后台跑任务时切页面，锁/变更事件持续接收。
 *
 * 事件帧统一 `data: {json}\n\n`（不用具名 event，onmessage 收到全部）：
 *   首帧 {type:"snapshot",locks:[...]} → store.applySnapshot
 *   后续 resource.locked / resource.unlocked / story.changed → store.applyEvent
 *   心跳 `: keepalive` 是注释帧，EventSource 自动忽略。
 *
 * 锁释放对账兜底（修复“Agent 已完成但剧本页仍锁定”）：SSE 是【一次一事件】的推送通道，
 * 单条 resource.unlocked 可能因连接静默中断/半开连接/后端重启而丢失，且 EventSource 若没
 * 及时重连就拉不到新的权威 snapshot，前端会永久卡在旧的锁定态。因此除 SSE 快推外叠加：
 *   1) 锁定期每 4s 拉一次 GET resource-locks（后端总线此刻真实持有的锁，与 SSE snapshot 同源）
 *      对账——后端已解锁则立即清锁，不依赖那条可能已丢的 unlock 事件；解锁即停轮询。
 *   2) 回到前台（visibilitychange）立即对账一次——Agent 在后台跑、用户切回时拿到真相。
 * SSE 负责实时（毫秒级反馈），HTTP 对账负责最终一致（秒级自愈）。
 */
import { watch, onUnmounted, type Ref } from 'vue';
import { useProjectEventsStore } from '@/store/projectEvents';
import { apiClient } from '@/api';

const API_BASE = 'http://localhost:8000';
/** 锁定期权威对账轮询间隔（ms）。SSE 仍是主通道，这只是秒级兜底。 */
const RESYNC_INTERVAL_MS = 4000;

export function useProjectEvents(projectRef: Ref<string>) {
  const store = useProjectEventsStore();
  let es: EventSource | null = null;
  let resyncTimer: number | null = null;

  function streamUrl(p: string) {
    return `${API_BASE}/api/project/${encodeURIComponent(p)}/events`;
  }

  function clearResync() {
    if (resyncTimer !== null) {
      window.clearInterval(resyncTimer);
      resyncTimer = null;
    }
  }

  /** 拉后端权威锁状态覆盖本地（与 SSE 首帧 snapshot 同源）。失败静默（下次轮询/重连再对）。 */
  async function resyncLocks() {
    const p = store.currentProject;
    if (!p) return;
    try {
      const { locks } = await apiClient.getResourceLocks(p);
      // 仅当仍停留在同一项目时应用，避免切项目竞态
      if (store.currentProject === p) store.applySnapshot(locks || []);
    } catch (e) {
      /* 网络/后端重启中：忽略，等下一次轮询或 SSE 重连 */
    }
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
      // 网络断开/服务重启：EventSource 会自动重连（重连首帧带权威 snapshot）；标记未连接。
      // 若此时正锁定，下面的轮询会兜底对账；否则回前台时也会对账。
      store.setConnected(false);
    };
  }

  // 锁定 → 开始权威对账轮询；解锁 → 停止（锁定期才轮询，零空闲开销）。
  // 2026-08-30：剧本域（story）、角色域（character）与分镜域（storyboard，Director）都参与对账。
  watch(() => store.storyLocked || store.charactersLocked || store.storyboardLocked, (locked) => {
    clearResync();
    if (locked) {
      void resyncLocks();                       // 立即先对一次
      resyncTimer = window.setInterval(resyncLocks, RESYNC_INTERVAL_MS);
    }
  });

  function onVisible() {
    if (document.visibilityState === 'visible') void resyncLocks();
  }
  document.addEventListener('visibilitychange', onVisible);

  watch(projectRef, (p) => {
    connect(p || '');
  }, { immediate: true });

  onUnmounted(() => {
    close();
    clearResync();
    document.removeEventListener('visibilitychange', onVisible);
  });
}
