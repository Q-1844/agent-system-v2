/**
 * 队列状态 store（Pinia）
 * 由 useJobStream 喂数据（snapshot REPLACE / job UPSERT / 降级轮询）。
 * store 本身不连 SSE，解耦：测试时可手动喂。
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiClient } from '@/api';
import type { Job, QueueStats } from '@/api';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';

const { message: naiveMessage } = createDiscreteApi(['message'], {
  configProviderProps: discreteConfigProviderProps,
});

export const useJobStore = defineStore('jobs', () => {
  const jobs = ref<Job[]>([]);
  const queue = ref<QueueStats>({ video_queued: 0, video_eta_s: 0, running: 0, failed: 0 });
  const connected = ref(false);   // SSE 实时连接中；false = 已降级轮询

  // ---- 由 useJobStream 喂 ----
  /** snapshot 整体替换（刷新/重连/降级轮询都用这个，保证与服务端一致） */
  function replaceAll(list: Job[]) { jobs.value = Array.isArray(list) ? list : []; }
  /** 单条增量 UPSERT */
  function upsertJob(j: Job) {
    const i = jobs.value.findIndex(x => x.job_id === j.job_id);
    if (i >= 0) jobs.value[i] = j; else jobs.value.unshift(j);
    if (j.queue) queue.value = j.queue;
  }
  function setQueue(q: QueueStats) { if (q) queue.value = q; }
  function setConnected(c: boolean) { connected.value = c; }

  // ---- 调 API ----
  async function cancel(jobId: string, force = false) {
    try { await apiClient.cancelJob(jobId, force); }
    catch (e: any) { naiveMessage.error(`取消失败: ${e.message || e}`); }
  }
  async function retry(jobId: string) {
    try { await apiClient.retryJob(jobId); naiveMessage.success('已重新入队'); }
    catch (e: any) { naiveMessage.error(`重试失败: ${e.message || e}`); }
  }
  async function retryFailed(projectName: string) {
    try {
      const r = await apiClient.retryFailedJobs(projectName);
      naiveMessage.success(`已重试 ${r.count} 个失败任务`);
    } catch (e: any) { naiveMessage.error(`重试失败: ${e.message || e}`); }
  }
  async function clearFinished(projectName: string) {
    try {
      const r = await apiClient.clearFinishedJobs(projectName);
      jobs.value = jobs.value.filter(j => j.state !== 'done' && j.state !== 'failed' && j.state !== 'cancelled');
      naiveMessage.success(`已清理 ${r.cleared} 条`);
    } catch (e: any) { naiveMessage.error(`清理失败: ${e.message || e}`); }
  }

  // ---- getters ----
  const byShot = (shotId: string) => jobs.value.filter(j => j.shot_id === shotId);
  const running = computed(() => jobs.value.filter(j => j.state === 'running'));
  const failed = computed(() => jobs.value.filter(j => j.state === 'failed'));
  const videoQueued = computed(() => jobs.value.filter(j => j.kind === 'video' && j.state === 'queued'));

  return {
    jobs, queue, connected,
    replaceAll, upsertJob, setQueue, setConnected,
    cancel, retry, retryFailed, clearFinished,
    byShot, running, failed, videoQueued,
  };
});
