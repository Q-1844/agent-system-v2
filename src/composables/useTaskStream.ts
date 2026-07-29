/**
 * 任务实时进度订阅（SSE）
 * 替代 setTimeout 轮询，服务端主动推送任务状态
 */
import { ref, onUnmounted } from 'vue';

const API_BASE = 'http://localhost:8000';

export function useTaskStream() {
  const status = ref<any>(null);
  let es: EventSource | null = null;

  /**
   * 订阅任务状态
   * @param taskId 任务ID
   * @param onUpdate 每次状态更新回调
   * @param onDone 任务完成（completed/failed）回调
   */
  function subscribe(taskId: string, onUpdate?: (s: any) => void, onDone?: () => void) {
    close();
    try {
      es = new EventSource(`${API_BASE}/api/task/${taskId}/stream`);
    } catch (e) {
      console.error('[TaskStream] EventSource 创建失败:', e);
      return;
    }

    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        status.value = data;
        if (onUpdate) onUpdate(data);
        if (data.status === 'completed' || data.status === 'failed') {
          es?.close();
          es = null;
          if (onDone) onDone();
        }
      } catch (e) {
        console.error('[TaskStream] 解析失败:', e);
      }
    };

    es.onerror = (e) => {
      console.warn('[TaskStream] 连接错误，关闭');
      es?.close();
      es = null;
    };
  }

  function close() {
    if (es) {
      es.close();
      es = null;
    }
  }

  onUnmounted(close);

  return { status, subscribe, close };
}
