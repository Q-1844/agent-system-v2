<template>
  <transition name="slide">
    <aside v-if="show" class="queue-panel">
      <header class="qp-header">
        <span class="qp-title">任务队列</span>
        <span class="qp-conn" :class="{ off: !connected }">
          {{ connected ? '● 实时' : '○ 轮询中' }}
        </span>
        <button class="qp-close" @click="$emit('update:show', false)">✕</button>
      </header>

      <div class="qp-stats">
        <span class="qp-stat">运行中 <b>{{ queue.running }}</b></span>
        <span class="qp-stat">视频排队 <b>{{ queue.video_queued }}</b>
          <span v-if="queue.video_queued" class="qp-eta">≈{{ Math.round(queue.video_eta_s / 60) }}min</span>
        </span>
        <span v-if="queue.failed" class="qp-stat fail">失败 <b>{{ queue.failed }}</b></span>
      </div>

      <div class="qp-actions">
        <button class="qp-btn" :disabled="!failed.length" @click="onRetryFailed">重试所有失败</button>
        <button class="qp-btn" :disabled="!jobs.length" @click="onClear">清理已完成</button>
      </div>

      <div class="qp-list">
        <div v-for="j in jobs" :key="j.job_id" class="qp-job" :class="j.state">
          <div class="qp-job-head">
            <span class="qp-kind">{{ kindLabel(j.kind) }}</span>
            <span class="qp-shot">{{ j.shot_id || '—' }}</span>
            <span class="qp-state" :class="j.state">{{ stateLabel(j.state) }}</span>
          </div>
          <div class="qp-msg">{{ j.message || '' }}</div>
          <div v-if="j.state === 'running'" class="qp-bar">
            <div class="qp-bar-fill" :style="{ width: (j.progress || 0) + '%' }"></div>
          </div>
          <div v-if="j.error" class="qp-err">{{ j.error }}</div>
          <div class="qp-job-actions">
            <button v-if="j.state === 'failed'" class="qp-btn sm" @click="store.retry(j.job_id)">重试</button>
            <button v-if="j.state === 'queued'" class="qp-btn sm" @click="store.cancel(j.job_id)">取消</button>
            <button v-if="j.state === 'running'" class="qp-btn sm" @click="onForceCancel(j.job_id)" title="中断 handler 释放 worker，已生成部分可能丢失">强制取消</button>
          </div>
        </div>
        <div v-if="!jobs.length" class="qp-empty">暂无任务</div>
      </div>
    </aside>
  </transition>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useJobStore } from '@/store/jobs';
import type { JobKind, JobState } from '@/api';

const props = defineProps<{ show: boolean; projectName: string }>();
defineEmits<{ (e: 'update:show', v: boolean): void }>();

const store = useJobStore();
const jobs = computed(() => store.jobs);
const queue = computed(() => store.queue);
const connected = computed(() => store.connected);
const failed = computed(() => store.failed);

function kindLabel(k: JobKind): string {
  return ({ first_frame: '首帧', last_frame: '尾帧', audio: '配音', video: '视频', compose: '合成' } as Record<JobKind, string>)[k] || k;
}
function stateLabel(s: JobState): string {
  return ({ queued: '排队', running: '运行', done: '完成', failed: '失败', cancelled: '已取消' } as Record<JobState, string>)[s] || s;
}
function onRetryFailed() { store.retryFailed(props.projectName); }
function onClear() { store.clearFinished(props.projectName); }
function onForceCancel(jobId: string) {
  if (window.confirm('强制取消运行中的任务？handler 会被中断，已生成部分可能丢失。')) {
    store.cancel(jobId, true);
  }
}
</script>

<style lang="scss" scoped>
.queue-panel {
  position: fixed;
  top: 0; right: 0;
  width: 360px;
  height: 100vh;
  background: var(--bg-root);
  border-left: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  z-index: 100;
  box-shadow: -4px 0 16px rgba(0, 0, 0, 0.2);
}
.qp-header {
  display: flex; align-items: center; gap: 8px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
}
.qp-title { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.qp-conn { font-size: 11px; color: #4ade80; margin-left: auto; }
.qp-conn.off { color: var(--text-muted); }
.qp-close {
  background: none; border: none; color: var(--text-muted);
  cursor: pointer; font-size: 14px; padding: 2px 6px; border-radius: 4px;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
.qp-stats {
  display: flex; flex-wrap: wrap; gap: 12px;
  padding: 10px 16px; border-bottom: 1px solid var(--border);
  font-size: 12px; color: var(--text-muted);
  b { color: var(--text-primary); font-weight: 600; }
  .fail b { color: #f87171; }
  .qp-eta { color: var(--text-muted); margin-left: 4px; }
}
.qp-actions {
  display: flex; gap: 8px; padding: 10px 16px;
  border-bottom: 1px solid var(--border);
}
.qp-btn {
  padding: 4px 10px; border-radius: 5px; font-size: 12px;
  border: 1px solid var(--border); background: var(--bg-root);
  color: var(--text-secondary); cursor: pointer;
  &:hover:not(:disabled) { background: var(--bg-hover); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
  &.sm { padding: 2px 8px; font-size: 11px; }
}
.qp-list { flex: 1; overflow-y: auto; padding: 8px; }
.qp-job {
  padding: 10px; border-radius: 6px; margin-bottom: 6px;
  background: var(--bg-hover); border-left: 3px solid transparent;
  &.running { border-left-color: #60a5fa; }
  &.failed { border-left-color: #f87171; }
  &.done { border-left-color: #4ade80; opacity: 0.7; }
  &.queued { border-left-color: var(--text-muted); }
}
.qp-job-head {
  display: flex; align-items: center; gap: 8px; margin-bottom: 4px;
  font-size: 12px;
}
.qp-kind { font-weight: 600; color: var(--text-primary); }
.qp-shot { color: var(--text-muted); font-family: monospace; }
.qp-state {
  margin-left: auto; padding: 1px 6px; border-radius: 3px; font-size: 11px;
  &.running { background: rgba(96,165,250,0.2); color: #60a5fa; }
  &.failed { background: rgba(248,113,113,0.2); color: #f87171; }
  &.done { background: rgba(74,222,128,0.2); color: #4ade80; }
  &.queued { background: var(--bg-root); color: var(--text-muted); }
}
.qp-msg { font-size: 12px; color: var(--text-secondary); word-break: break-word; }
.qp-bar {
  height: 3px; background: var(--bg-root); border-radius: 2px; margin-top: 6px; overflow: hidden;
  .qp-bar-fill { height: 100%; background: #60a5fa; transition: width 0.3s; }
}
.qp-err { font-size: 11px; color: #f87171; margin-top: 4px; word-break: break-word; }
.qp-job-actions { display: flex; gap: 6px; margin-top: 6px; }
.qp-empty { text-align: center; color: var(--text-muted); padding: 40px 0; font-size: 13px; }

.slide-enter-active, .slide-leave-active { transition: transform 0.2s ease; }
.slide-enter-from, .slide-leave-to { transform: translateX(100%); }
</style>
