<template>
  <div class="storyboard">
    <div class="toolbar">
      <h2>Storyboard</h2>
      <div class="toolbar-actions">
        <button class="btn" :class="{ active: viewMode === 'grid' }" @click="viewMode = 'grid'">Grid</button>
        <button class="btn" :class="{ active: viewMode === 'timeline' }" @click="viewMode = 'timeline'">Timeline</button>
      </div>
    </div>

    <div class="workspace">
      <div class="preview">
        <div class="preview-header">
          <span class="preview-title">Scene {{ currentScene.id }}: {{ currentScene.description }}</span>
          <div class="preview-tags">
            <span v-for="char in currentScene.characters" :key="char" class="tag">{{ char }}</span>
          </div>
        </div>

        <div class="preview-image">
          <img
            v-if="currentScene.image_path"
            :src="getImageUrl(currentScene.image_path)"
            :alt="currentScene.description"
          />
          <template v-else>
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
              <rect x="2" y="2" width="20" height="20" rx="2" />
              <circle cx="8.5" cy="8.5" r="1.5" />
              <polyline points="21 15 16 10 5 21" />
            </svg>
            <span>Preview</span>
          </template>
        </div>

        <div class="scene-details">
          <div class="detail-row">
            <span class="detail-label">Camera</span>
            <span class="detail-value">{{ currentScene.camera }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">Lighting</span>
            <span class="detail-value">{{ currentScene.lighting }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">Motion</span>
            <span class="detail-value">{{ currentScene.motion }}</span>
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-section">
          <div class="panel-title">Quality</div>
          <div v-if="currentScene.score" class="score">
            <div class="score-main">
              <span class="score-value">{{ currentScene.score.total }}</span>
              <span class="score-max">/10</span>
            </div>
            <div class="score-bars">
              <div class="score-row">
                <span>Character</span>
                <span class="score-num">{{ currentScene.score.character }}</span>
              </div>
              <div class="score-row">
                <span>Style</span>
                <span class="score-num">{{ currentScene.score.style }}</span>
              </div>
              <div class="score-row">
                <span>Composition</span>
                <span class="score-num">{{ currentScene.score.composition }}</span>
              </div>
              <div class="score-row">
                <span>Clarity</span>
                <span class="score-num">{{ currentScene.score.clarity }}</span>
              </div>
            </div>
          </div>
          <div v-else class="no-score">Not scored yet</div>
        </div>

        <div class="panel-section">
          <div class="panel-title">Actions</div>
          <div class="actions">
            <button class="btn btn-primary" :disabled="generating" @click="handleGenerate">
              {{ generating ? '生成中...' : '生成分镜' }}
            </button>
            <button class="btn" @click="handleConfirm">Confirm</button>
            <button class="btn" @click="handleRegenerate">Regenerate</button>
            <button class="btn" @click="handleEditPrompt">Edit Prompt</button>
            <button class="btn" @click="handlePause">Pause</button>
            <button class="btn btn-ghost" @click="handleSkip">Skip</button>
          </div>
        </div>
      </div>
    </div>

    <div class="strip">
      <div
        v-for="scene in scenes"
        :key="scene.id"
        class="strip-item"
        :class="{ active: scene.id === currentScene.id, [scene.status]: true }"
        @click="selectScene(scene)"
      >
        <div class="strip-thumb">
          <img
            v-if="scene.image_path"
            :src="getImageUrl(scene.image_path)"
            :alt="scene.description"
          />
          <span v-else>{{ scene.id }}</span>
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
import { ref, computed, onMounted, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useProjectStore } from '@/store';
import { apiClient } from '@/api';
import { useTaskStream } from '@/composables/useTaskStream';
import { getImageUrl } from '@/utils/media';

const store = useProjectStore();
const { scenes, projectName } = storeToRefs(store);

const viewMode = ref('grid');

// 使用 computed 保持响应式 - 当 scenes 更新时 currentScene 自动更新
const currentScene = computed(() => {
  if (scenes.value.length > 0 && currentSceneIndex.value < scenes.value.length) {
    return scenes.value[currentSceneIndex.value];
  }
  return scenes.value[0] || {
    id: 0,
    description: 'No scenes loaded',
    characters: [],
    camera: '',
    lighting: '',
    motion: '',
    status: 'pending'
  };
});

const currentSceneIndex = ref(0);

// getImageUrl 来自 @/utils/media（见 import）

const selectScene = (scene: any) => {
  const index = scenes.value.findIndex(s => s.id === scene.id);
  if (index !== -1) {
    currentSceneIndex.value = index;
  }
};

// 按钮处理函数
const handleConfirm = async () => {
  if (currentScene.value.id) {
    await store.confirmScene(currentScene.value.id);
  }
};

const handleRegenerate = async () => {
  if (currentScene.value.id) {
    await store.regenerateScene(currentScene.value.id);
  }
};

const handleEditPrompt = () => {
  // 编辑提示词 - 弹出输入框
  const newPrompt = prompt('编辑场景描述:', currentScene.value.description);
  if (newPrompt !== null) {
    const scene = scenes.value.find(s => s.id === currentScene.value.id);
    if (scene) {
      scene.description = newPrompt;
      store.showNotification('描述已更新', 'success');
    }
  }
};

const handlePause = async () => {
  await store.pauseWorkflow();
};

const handleSkip = () => {
  if (currentScene.value.id) {
    store.skipScene(currentScene.value.id);
    // 自动跳到下一个场景
    if (currentSceneIndex.value < scenes.value.length - 1) {
      currentSceneIndex.value++;
    }
  }
};

// 生成当前场景分镜（调后端单场景生成，SSE 实时推送进度）
const generating = ref(false);
const { subscribe: subscribeTask } = useTaskStream();

async function handleGenerate() {
  if (!currentScene.value.id) return;
  generating.value = true;
  try {
    const r = await apiClient.generateSceneStoryboard(projectName.value, currentScene.value.id);
    store.showNotification('分镜生成已启动', 'info');
    subscribeTask(r.task_id, (s: any) => {
      if (s.status === 'completed') {
        store.showNotification('分镜生成完成', 'success');
        store.loadProject(projectName.value);
      } else if (s.status === 'failed') {
        store.showNotification(`生成失败: ${s.message || ''}`, 'error');
      }
    }, () => { generating.value = false; });
  } catch (e: any) {
    store.showNotification(`启动失败: ${e.message}`, 'error');
    generating.value = false;
  }
}

onMounted(async () => {
  await store.loadProject(projectName.value);
});
</script>

<style lang="scss" scoped>
.storyboard {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 88px);
  position: relative;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;

  h2 {
    font-size: 16px;
    font-weight: 600;
    color: var(--text-primary);
  }
}

.toolbar-actions {
  display: flex;
  gap: 4px;
  background-color: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 2px;
}

.btn {
  padding: 6px 12px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  border: none;
  background: none;
  color: var(--text-muted);

  &:hover {
    color: var(--text-secondary);
  }

  &.active {
    background-color: var(--bg-hover);
    color: var(--text-primary);
  }

  &-primary {
    background-color: var(--accent);
    color: var(--accent-text);

    &:hover {
      background-color: var(--accent-hover);
    }
  }

  &-ghost {
    background: none;
    color: var(--text-muted);

    &:hover {
      color: var(--text-secondary);
    }
  }
}

.workspace {
  display: flex;
  gap: 16px;
  flex: 1;
  overflow: hidden;
}

.preview {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.preview-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.preview-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

.preview-tags {
  display: flex;
  gap: 6px;
}

.tag {
  font-size: 11px;
  color: var(--text-muted);
  background-color: var(--bg-surface);
  border: 1px solid var(--border);
  padding: 2px 8px;
  border-radius: 4px;
}

.preview-image {
  flex: 1;
  background-color: var(--bg-root);
  border: 1px solid var(--border);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-faint);
  margin-bottom: 12px;
  overflow: hidden;

  img {
    width: 100%;
    height: 100%;
    object-fit: contain;
  }

  span {
    font-size: 13px;
    color: var(--text-muted);
  }
}

.scene-details {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.detail-row {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
}

.detail-label {
  color: var(--text-muted);
}

.detail-value {
  color: var(--text-secondary);
}

.panel {
  width: 260px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.panel-section {
  background-color: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
}

.panel-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 12px;
}

.score {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.score-main {
  text-align: center;
}

.score-value {
  font-size: 32px;
  font-weight: 700;
  color: var(--text-primary);
}

.score-max {
  font-size: 16px;
  color: var(--text-faint);
}

.score-bars {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.score-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--text-muted);
}

.score-num {
  font-weight: 600;
  color: var(--text-primary);
}

.no-score {
  font-size: 13px;
  color: var(--text-faint);
  text-align: center;
  padding: 20px 0;
}

.actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.strip {
  display: flex;
  gap: 8px;
  padding: 12px;
  background-color: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-top: 16px;
  overflow-x: auto;
}

.strip-item {
  width: 80px;
  height: 50px;
  background-color: var(--bg-root);
  border: 1px solid var(--border);
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  transition: border-color 0.15s ease;
  overflow: hidden;

  &.active {
    border-color: var(--text-primary);
  }

  &.completed {
    background-color: var(--bg-hover);
  }

  &.in_progress {
    border-color: var(--border-hover);
  }

  &.confirmed {
    border-color: var(--success);
  }

  &.skipped {
    opacity: 0.5;
  }

  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  span {
    font-size: 11px;
    color: var(--text-muted);
    font-weight: 500;
  }
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
