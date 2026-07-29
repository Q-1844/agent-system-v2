<template>
  <div class="dashboard">
    <div class="header">
      <div>
        <h1>Storyboard</h1>
        <p class="subtitle">AI-powered animation workflow</p>
      </div>
      <div class="header-actions">
        <button class="btn btn-primary" @click="startWorkflow" :disabled="isRunning">
          {{ isRunning ? '运行中...' : '开始生成' }}
        </button>
      </div>
    </div>

    <!-- 任务状态 -->
    <div v-if="taskStatus.status !== 'idle'" class="task-status">
      <div class="status-header">
        <span>任务状态</span>
        <span class="status-badge" :class="taskStatus.status">
          {{ statusText }}
        </span>
      </div>
      <p class="status-message">{{ taskStatus.message }}</p>
    </div>

    <!-- 统计卡片 -->
    <div class="stats-grid">
      <div class="stat-card">
        <span class="stat-value">{{ stats.total }}</span>
        <span class="stat-label">Total</span>
      </div>
      <div class="stat-card">
        <span class="stat-value done">{{ stats.completed }}</span>
        <span class="stat-label">Done</span>
      </div>
      <div class="stat-card">
        <span class="stat-value active">{{ stats.inProgress }}</span>
        <span class="stat-label">Active</span>
      </div>
      <div class="stat-card">
        <span class="stat-value muted">{{ stats.pending }}</span>
        <span class="stat-label">Pending</span>
      </div>
    </div>

    <!-- 进度条 -->
    <div class="progress-bar">
      <div class="progress-info">
        <span>Scene Generation</span>
        <span class="progress-pct">{{ progressPercent }}%</span>
      </div>
      <div class="progress-track">
        <div class="progress-fill" :style="{ width: progressPercent + '%' }"></div>
      </div>
      <span class="progress-sub">
        {{ stats.completed }} of {{ stats.total }} scenes completed
      </span>
    </div>

    <!-- 场景列表 -->
    <div class="scenes">
      <div
        v-for="scene in scenes"
        :key="scene.id"
        class="scene-card"
        :class="scene.status"
      >
        <div class="scene-thumb">
          <img
            v-if="scene.image_path"
            :src="getImageUrl(scene.image_path)"
            :alt="scene.description"
            @load="console.log('[IMG OK]', scene.id, getImageUrl(scene.image_path))"
            @error="(e) => { console.error('[IMG FAIL]', scene.id, getImageUrl(scene.image_path), e); e.target.style.display='none'; }"
          />
          <svg
            v-else
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.5"
          >
            <rect x="2" y="2" width="20" height="20" rx="2" />
            <circle cx="8.5" cy="8.5" r="1.5" />
            <polyline points="21 15 16 10 5 21" />
          </svg>
          <span class="scene-num">{{ scene.id }}</span>
        </div>
        <div class="scene-body">
          <p class="scene-desc">{{ scene.description }}</p>
          <div class="scene-meta">
            <div class="scene-tags">
              <span v-for="char in scene.characters" :key="char" class="tag">
                {{ char }}
              </span>
            </div>
            <span v-if="scene.score" class="scene-score">
              {{ scene.score.total }}
            </span>
            <span v-else class="scene-status">{{ getStatusLabel(scene.status) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 通知 -->
    <div v-if="store.notification.show" class="notification" :class="store.notification.type">
      {{ store.notification.message }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import { useProjectStore } from '@/store';
import { getImageUrl } from '@/utils/media';

const store = useProjectStore();
// 使用 storeToRefs 保持响应性
const { scenes, stats, taskStatus, projectName } = storeToRefs(store);

const isRunning = computed(() => taskStatus.value.status === 'running');

const progressPercent = computed(() => {
  if (stats.value.total === 0) return 0;
  return Math.round((stats.value.completed / stats.value.total) * 100);
});

const statusText = computed(() => {
  switch (taskStatus.value.status) {
    case 'running': return '运行中';
    case 'completed': return '已完成';
    case 'failed': return '失败';
    default: return '空闲';
  }
});

const getStatusLabel = (status: string) => {
  switch (status) {
    case 'completed': return 'Done';
    case 'confirmed': return 'Confirmed';
    case 'in_progress': return 'Active';
    case 'failed': return 'Failed';
    case 'skipped': return 'Skipped';
    default: return 'Pending';
  }
};

const startWorkflow = async () => {
  const scriptContent = `
project: "${projectName.value}"
style: "${store.style}"
size: "${store.size}"

characters:
${store.characters.map(c => `  - name: "${c.name}"
    description: "${c.description}"`).join('\n')}

scenes:
${store.scenes.map(s => `  - id: ${s.id}
    description: "${s.description}"
    characters: ${JSON.stringify(s.characters)}
    camera: "${s.camera}"
    lighting: "${s.lighting}"
    motion: "${s.motion}"${s.dialogue ? `\n    dialogue: "${s.dialogue}"` : ''}`).join('\n')}
  `;

  await store.runWorkflow(scriptContent);
};

onMounted(() => {
  // 尝试加载项目，如果失败则使用默认数据
  store.loadProject(projectName.value);
  store.refreshTaskStatus();
});
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

.task-status {
  background-color: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 24px;
}

.status-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

.status-badge {
  font-size: 12px;
  padding: 4px 8px;
  border-radius: 4px;

  &.running {
    background-color: #1e3a5f;
    color: #60a5fa;
  }

  &.completed {
    background-color: #14532d;
    color: #4ade80;
  }

  &.failed {
    background-color: #7f1d1d;
    color: #f87171;
  }
}

.status-message {
  font-size: 13px;
  color: var(--text-secondary);
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  background-color: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.stat-value {
  font-size: 24px;
  font-weight: 600;
  color: var(--text-primary);

  &.done { color: var(--success); }
  &.active { color: var(--warning); }
  &.muted { color: var(--text-muted); }
}

.stat-label {
  font-size: 11px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-top: 4px;
}

.progress-bar {
  background-color: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 24px;
}

.progress-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  font-size: 13px;
  color: var(--text-secondary);
}

.progress-pct {
  font-weight: 600;
  color: var(--text-primary);
}

.progress-track {
  height: 4px;
  background-color: var(--bg-hover);
  border-radius: 2px;
  overflow: hidden;
  margin-bottom: 8px;
}

.progress-fill {
  height: 100%;
  background-color: var(--text-primary);
  border-radius: 2px;
  transition: width 0.3s ease;
}

.progress-sub {
  font-size: 12px;
  color: var(--text-faint);
}

.scenes {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

.scene-card {
  display: flex;
  gap: 14px;
  padding: 14px;
  background-color: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s ease;

  &:hover {
    border-color: var(--border-hover);
  }

  &.completed {
    .scene-thumb {
      background-color: var(--bg-hover);
    }
  }

  &.confirmed {
    border-color: var(--success);
  }

  &.skipped {
    opacity: 0.5;
  }

  &.in_progress {
    border-color: var(--border-hover);
  }
}

.scene-thumb {
  width: 120px;
  height: 80px;
  background-color: var(--bg-root);
  border: 1px solid var(--border);
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  position: relative;
  color: var(--text-faint);
  overflow: hidden;

  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    border-radius: 5px;
  }
}

.scene-num {
  position: absolute;
  bottom: 4px;
  right: 6px;
  font-size: 10px;
  color: var(--text-faint);
  font-weight: 500;
}

.scene-body {
  flex: 1;
  min-width: 0;
}

.scene-desc {
  font-size: 13px;
  color: var(--text-primary);
  margin-bottom: 8px;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.scene-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.scene-tags {
  display: flex;
  gap: 4px;
}

.tag {
  font-size: 11px;
  color: var(--text-muted);
  background-color: var(--bg-hover);
  padding: 2px 6px;
  border-radius: 4px;
}

.scene-score {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.scene-status {
  font-size: 11px;
  color: var(--text-muted);
}

.notification {
  position: fixed;
  top: 20px;
  right: 20px;
  padding: 12px 20px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  z-index: 1000;
  animation: slideIn 0.3s ease;

  &.success {
    background-color: #14532d;
    color: #4ade80;
    border: 1px solid #166534;
  }

  &.error {
    background-color: #7f1d1d;
    color: #f87171;
    border: 1px solid #991b1b;
  }

  &.info {
    background-color: #1e3a5f;
    color: #60a5fa;
    border: 1px solid #1e40af;
  }
}

@keyframes slideIn {
  from {
    transform: translateX(100%);
    opacity: 0;
  }
  to {
    transform: translateX(0);
    opacity: 1;
  }
}
</style>
