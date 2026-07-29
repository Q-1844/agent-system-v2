<template>
  <div class="animation">
    <div class="toolbar">
      <h2>Animation</h2>
    </div>

    <div class="workspace">
      <div class="player-section">
        <div class="player" v-if="selectedAnimation">
          <video
            v-if="selectedAnimation.videoPath"
            :src="getVideoUrl(selectedAnimation.videoPath)"
            controls
            class="video-player"
          />
          <template v-else>
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
            <span>Preview</span>
          </template>
        </div>
        <div class="player" v-else>
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
            <polygon points="5 3 19 12 5 21 5 3" />
          </svg>
          <span>Select a transition</span>
        </div>

        <div class="controls" v-if="selectedAnimation">
          <button class="ctrl-btn" @click="togglePlay">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
          </button>
          <div class="timeline">
            <div class="timeline-fill" style="width: 0%"></div>
          </div>
          <span class="time">00:00 / {{ selectedAnimation.duration }}</span>
        </div>

        <div class="info-card" v-if="selectedAnimation">
          <div class="info-header">Transition: Scene {{ selectedAnimation.fromScene }} → {{ selectedAnimation.toScene }}</div>
          <div class="info-grid">
            <div class="info-row">
              <span>Duration</span>
              <span>{{ selectedAnimation.duration }}</span>
            </div>
            <div class="info-row">
              <span>FPS</span>
              <span>24</span>
            </div>
            <div class="info-row">
              <span>Resolution</span>
              <span>{{ store.size }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="list-section">
        <div class="list-header">Transitions</div>
        <div class="list">
          <div
            v-for="(anim, index) in animations"
            :key="index"
            class="list-item"
            :class="{ active: selectedAnimationIndex === index }"
            @click="selectAnimation(index)"
          >
            <div class="item-thumb">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
            </div>
            <div class="item-info">
              <span class="item-title">{{ anim.title }}</span>
              <span class="item-meta">{{ anim.duration }}</span>
            </div>
            <span class="item-status" :class="anim.status">
              {{ anim.status === 'completed' ? 'Done' : 'Pending' }}
            </span>
          </div>
        </div>

        <div class="actions">
          <button class="btn btn-primary" @click="handleRegenerate">Regenerate</button>
          <button class="btn" @click="handleDownload">Download</button>
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
import { ref, computed, onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import { useProjectStore } from '@/store';
import { getVideoUrl as _getVideoUrl } from '@/utils/media';

const store = useProjectStore();
const { scenes, projectName } = storeToRefs(store);

const selectedAnimationIndex = ref(0);

// 从场景数据生成动画列表
const animations = computed(() => {
  const result = [];
  for (let i = 0; i < scenes.value.length - 1; i++) {
    const from = scenes.value[i];
    const to = scenes.value[i + 1];
    result.push({
      title: `Scene ${from.id} → ${to.id}`,
      fromScene: from.id,
      toScene: to.id,
      duration: '5s',
      status: (from.status === 'completed' && to.status === 'completed') ? 'completed' : 'pending',
      videoPath: from.transitions?.to_next || null,
    });
  }
  return result;
});

const selectedAnimation = computed(() => {
  if (animations.value.length > 0 && selectedAnimationIndex.value < animations.value.length) {
    return animations.value[selectedAnimationIndex.value];
  }
  return null;
});

const getVideoUrl = (path: string) => _getVideoUrl(path);

const selectAnimation = (index: number) => {
  selectedAnimationIndex.value = index;
};

const togglePlay = () => {
  // 播放/暂停逻辑由 video 元素自带控件处理
};

const handleRegenerate = async () => {
  if (selectedAnimation.value) {
    await store.regenerateAnimation(
      selectedAnimation.value.fromScene,
      selectedAnimation.value.toScene
    );
  }
};

const handleDownload = () => {
  if (selectedAnimation.value?.videoPath) {
    const url = getVideoUrl(selectedAnimation.value.videoPath);
    const a = document.createElement('a');
    a.href = url;
    a.download = `transition_${selectedAnimation.value.fromScene}_${selectedAnimation.value.toScene}.mp4`;
    a.click();
    store.showNotification('下载已开始', 'success');
  } else {
    store.showNotification('没有可下载的视频', 'error');
  }
};

onMounted(async () => {
  await store.loadProject(projectName.value);
});
</script>

<style lang="scss" scoped>
.animation {
  max-width: 960px;
  position: relative;
}

.toolbar {
  margin-bottom: 20px;

  h2 {
    font-size: 16px;
    font-weight: 600;
    color: var(--text-primary);
  }
}

.workspace {
  display: flex;
  gap: 16px;
}

.player-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.player {
  aspect-ratio: 16/9;
  background-color: var(--bg-root);
  border: 1px solid var(--border);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-faint);
  overflow: hidden;

  span {
    font-size: 13px;
    color: var(--text-muted);
  }
}

.video-player {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.controls {
  display: flex;
  align-items: center;
  gap: 10px;
}

.ctrl-btn {
  width: 32px;
  height: 32px;
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;

  &:hover {
    background-color: var(--bg-hover);
    color: var(--text-secondary);
  }
}

.timeline {
  flex: 1;
  height: 4px;
  background-color: var(--bg-hover);
  border-radius: 2px;
  overflow: hidden;
}

.timeline-fill {
  height: 100%;
  background-color: var(--text-primary);
  border-radius: 2px;
}

.time {
  font-size: 11px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.info-card {
  background-color: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
}

.info-header {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 12px;
}

.info-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--text-muted);

  span:last-child {
    color: var(--text-secondary);
  }
}

.list-section {
  width: 240px;
  background-color: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
}

.list-header {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
}

.list {
  flex: 1;
  overflow-y: auto;
}

.list-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  cursor: pointer;
  transition: background-color 0.15s ease;

  &:hover {
    background-color: var(--bg-hover);
  }

  &.active {
    background-color: var(--bg-active);
  }
}

.item-thumb {
  width: 32px;
  height: 24px;
  background-color: var(--bg-root);
  border: 1px solid var(--border);
  border-radius: 3px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-faint);
  flex-shrink: 0;
}

.item-info {
  flex: 1;
  min-width: 0;
}

.item-title {
  display: block;
  font-size: 12px;
  color: var(--text-secondary);
}

.item-meta {
  font-size: 10px;
  color: var(--text-muted);
}

.item-status {
  font-size: 10px;
  color: var(--text-muted);

  &.completed {
    color: var(--success);
  }
}

.actions {
  padding: 12px 14px;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.btn {
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  border: none;

  &-primary {
    background-color: var(--accent);
    color: var(--accent-text);

    &:hover {
      background-color: var(--accent-hover);
    }
  }

  &:not(&-primary) {
    background: none;
    color: var(--text-muted);

    &:hover {
      background-color: var(--bg-hover);
      color: var(--text-secondary);
    }
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
