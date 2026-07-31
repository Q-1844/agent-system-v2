<template>
  <div class="dashboard">
    <div class="header">
      <div>
        <h1>Storyboard</h1>
        <p class="subtitle">AI 动画分镜工作流</p>
      </div>
      <div class="header-actions">
        <button class="btn btn-primary" @click="goScriptEditor">从剧本开始</button>
      </div>
    </div>

    <!-- 队列概况（阶段 4，全局 SSE） -->
    <div class="queue-overview" v-if="queue.running || queue.video_queued || queue.failed">
      <span class="qo-title">任务队列</span>
      <span class="qo-stat">运行中 <b>{{ queue.running }}</b></span>
      <span class="qo-stat">视频排队 <b>{{ queue.video_queued }}</b>
        <span v-if="queue.video_queued" class="qo-eta">≈{{ Math.round(queue.video_eta_s / 60) }}min</span>
      </span>
      <span class="qo-stat fail" v-if="queue.failed">失败 <b>{{ queue.failed }}</b></span>
      <span class="qo-conn" :class="{ off: !jobConnected }">{{ jobConnected ? '● 实时' : '○ 轮询中' }}</span>
    </div>

    <!-- 空状态引导：新管线分步流程，非一键出片 -->
    <div v-if="!queue.running && !queue.video_queued && !queue.failed" class="empty-hint">
      <p>从剧本开始，依次完成角色、分镜、配音、视频，最后导出成片。</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { useJobStore } from '@/store/jobs';

const jobStore = useJobStore();
const router = useRouter();
const queue = computed(() => jobStore.queue);
const jobConnected = computed(() => jobStore.connected);

// 引导到剧本编辑器：新管线流程入口（剧本 -> 角色 -> 分镜 -> 导出，分步非一键）
const goScriptEditor = () => router.push('/script-editor');
</script>

<style lang="scss" scoped>
.dashboard {
  max-width: 960px;
  position: relative;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;

  h1 {
    font-size: 20px;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 4px;
  }
}

.subtitle {
  font-size: 13px;
  color: var(--text-muted);
}

.header-actions {
  display: flex;
  gap: 8px;
}

.btn {
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  border: none;

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  &-primary {
    background-color: var(--accent);
    color: var(--accent-text);

    &:hover:not(:disabled) {
      background-color: var(--accent-hover);
    }
  }
}

.empty-hint {
  margin-top: 32px;
  padding: 24px;
  background: var(--bg-surface);
  border: 1px dashed var(--border);
  border-radius: 8px;
  text-align: center;

  p {
    font-size: 13px;
    color: var(--text-muted);
    margin: 0;
  }
}

.queue-overview {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 12px 16px;
  background: var(--bg-root);
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 16px;
  font-size: 13px;
  color: var(--text-muted);
  .qo-title { font-weight: 600; color: var(--text-primary); }
  .qo-stat b { color: var(--text-primary); font-weight: 600; }
  .qo-stat.fail b { color: #f87171; }
  .qo-eta { color: var(--text-muted); margin-left: 4px; }
  .qo-conn { margin-left: auto; font-size: 12px; color: #4ade80; }
  .qo-conn.off { color: var(--text-muted); }
}
</style>
