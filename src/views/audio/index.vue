<template>
  <div class="audio">
    <div class="toolbar">
      <h2>Audio</h2>
      <button class="btn btn-primary" @click="handleGenerateAll">Generate All</button>
    </div>

    <div class="workspace">
      <div class="list-section">
        <div class="list-header">Dialogues</div>
        <div class="list">
          <div v-for="scene in scenesWithDialogue" :key="scene.id" class="list-item">
            <div class="item-header">
              <span class="item-scene">Scene {{ scene.id }}</span>
              <span class="item-status" :class="{ done: scene.audio_path }">
                {{ scene.audio_path ? 'Done' : 'Pending' }}
              </span>
            </div>
            <p class="item-text">"{{ scene.dialogue }}"</p>
            <div class="item-meta">{{ scene.characters?.join(', ') || '' }}</div>
            <div class="item-actions">
              <button class="btn btn-ghost" @click="handlePlay(scene)">Play</button>
              <button class="btn btn-ghost" @click="handleRegenerateAudio(scene.id)">Regenerate</button>
            </div>
          </div>
          <div v-if="scenesWithDialogue.length === 0" class="empty-state">
            <p>No dialogues found</p>
          </div>
        </div>
      </div>

      <div class="settings-section">
        <div class="settings-card">
          <div class="settings-title">TTS Settings</div>
          <div class="settings-form">
            <div class="form-group">
              <label>音色</label>
              <select class="select" v-model="ttsSettings.voice">
                <option value="default">晓晓（女声·温柔）</option>
                <option value="male">云希（男声·沉稳）</option>
                <option value="xiaoyi">晓伊（女声·活泼）</option>
                <option value="yunyang">云扬（男声·新闻）</option>
                <option value="xiaochen">晓晨（女声）</option>
              </select>
            </div>
            <div class="form-group">
              <label>Speed</label>
              <input type="range" min="0.5" max="2" step="0.1" v-model.number="ttsSettings.speed" class="range" />
              <span class="range-value">{{ ttsSettings.speed.toFixed(1) }}x</span>
            </div>
          </div>
        </div>

        <div class="settings-card">
          <div class="settings-title">Subtitles</div>
          <p class="settings-desc">Auto-generated SRT files</p>
          <button class="btn" @click="handleDownloadSrt">Download SRT</button>
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
import { computed, reactive, onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import { useProjectStore } from '@/store';
import { getAudioUrl } from '@/utils/media';

const store = useProjectStore();
const { scenes, projectName } = storeToRefs(store);

const ttsSettings = reactive({
  voice: 'default',
  speed: 1.0,
});

const scenesWithDialogue = computed(() => scenes.value.filter(s => s.dialogue));

const handleGenerateAll = async () => {
  await store.generateAllAudio();
};

const handlePlay = (scene: any) => {
  if (scene.audio_path) {
    const audioUrl = getAudioUrl(scene.audio_path);
    const audio = new Audio(audioUrl);
    audio.play().catch((err: any) => {
      store.showNotification('播放失败: ' + err.message, 'error');
    });
  } else {
    store.showNotification('该场景还没有生成音频', 'info');
  }
};

const handleRegenerateAudio = async (sceneId: number) => {
  await store.regenerateAudio(sceneId, ttsSettings.voice, ttsSettings.speed);
};

const handleDownloadSrt = () => {
  // 下载所有字幕文件
  store.showNotification('字幕下载功能开发中', 'info');
};

onMounted(async () => {
  await store.loadProject(projectName.value);
});
</script>

<style lang="scss" scoped>
.audio {
  max-width: 960px;
  position: relative;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
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

.list-section {
  flex: 1;
  background-color: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
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
  max-height: 500px;
  overflow-y: auto;
}

.list-item {
  padding: 14px;
  border-bottom: 1px solid var(--border);

  &:last-child {
    border-bottom: none;
  }
}

.empty-state {
  padding: 40px 20px;
  text-align: center;
  color: var(--text-faint);
  font-size: 13px;
}

.item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.item-scene {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

.item-status {
  font-size: 11px;
  color: var(--text-muted);

  &.done {
    color: var(--success);
  }
}

.item-text {
  font-size: 14px;
  color: var(--text-secondary);
  font-style: italic;
  margin-bottom: 6px;
}

.item-meta {
  font-size: 11px;
  color: var(--text-muted);
  margin-bottom: 10px;
}

.item-actions {
  display: flex;
  gap: 6px;
}

.settings-section {
  width: 240px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.settings-card {
  background-color: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
}

.settings-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 12px;
}

.settings-desc {
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 12px;
}

.settings-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;

  label {
    font-size: 12px;
    color: var(--text-muted);
  }
}

.select {
  background-color: var(--bg-root);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 6px 8px;
  font-size: 12px;
  color: var(--text-primary);
  outline: none;

  &:focus {
    border-color: var(--border-hover);
  }
}

.range {
  width: 100%;
  accent-color: var(--text-primary);
}

.range-value {
  font-size: 11px;
  color: var(--text-muted);
  text-align: right;
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

  &-ghost {
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
