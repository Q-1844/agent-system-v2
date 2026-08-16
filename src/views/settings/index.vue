<template>
  <div class="settings">
    <div class="toolbar">
      <h2>Settings</h2>
      <button class="btn btn-primary" @click="handleSave">Save Settings</button>
    </div>

    <div class="grid">
      <div class="card">
        <div class="card-title">Project</div>
        <div class="form">
          <div class="form-group">
            <label>Name</label>
            <input type="text" v-model="form.projectName" class="input" />
          </div>
          <div class="form-group">
            <label>Style</label>
            <input type="text" v-model="form.style" class="input" />
          </div>
          <div class="form-group">
            <label>Size</label>
            <select class="input" v-model="form.size">
              <option>1152x768</option>
              <option>1024x768</option>
              <option>1280x720</option>
              <option>1920x1080</option>
            </select>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">API Keys</div>
        <div class="form">
          <div class="form-group">
            <label>Agnes API Key</label>
            <input type="password" v-model="form.agnesApiKey" class="input" placeholder="sk-..." />
          </div>
          <div class="form-group">
            <label>Mimo TTS API Key</label>
            <input type="password" v-model="form.mimoApiKey" class="input" placeholder="tp-..." />
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Generation</div>
        <div class="form">
          <div class="form-group">
            <label>Candidates</label>
            <input type="number" v-model.number="form.candidates" min="1" max="10" class="input" />
          </div>
          <div class="form-group">
            <label>Quality Threshold</label>
            <input type="range" min="0" max="10" step="0.5" v-model.number="form.qualityThreshold" class="range" />
            <span class="range-value">{{ form.qualityThreshold }}</span>
          </div>
          <div class="form-group">
            <label>FPS</label>
            <select class="input" v-model.number="form.fps">
              <option :value="24">24</option>
              <option :value="30">30</option>
              <option :value="60">60</option>
            </select>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Output</div>
        <div class="form">
          <div class="form-group">
            <label>Directory</label>
            <input type="text" v-model="form.outputDir" class="input" />
          </div>
          <div class="form-group">
            <label class="checkbox-label">
              <input type="checkbox" v-model="form.saveIntermediate" />
              <span>Save intermediate files</span>
            </label>
          </div>
          <div class="form-group">
            <label class="checkbox-label">
              <input type="checkbox" v-model="form.autoPreview" />
              <span>Auto-generate preview</span>
            </label>
          </div>
        </div>
      </div>

      <!-- AI-6B Agents & Models -->
      <div class="card">
        <div class="card-title">Agents &amp; Models</div>
        <div class="form">
          <div v-if="!agentRows.length" class="form-group"><span class="hint">加载中…</span></div>
          <div v-for="a in agentRows" :key="a.id" class="agent-row">
            <div class="agent-info">
              <span class="agent-name">{{ a.name }}</span>
              <span class="agent-desc">{{ a.description }}</span>
            </div>
            <select class="input" v-model="a.profile_id" @change="saveAgent(a)">
              <option v-for="p in profiles" :key="p.id" :value="p.id">{{ p.name }}</option>
            </select>
            <label class="checkbox-label">
              <input type="checkbox" v-model="a.enabled" @change="saveAgent(a)" />
              <span>Enabled</span>
            </label>
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
import { reactive, ref, onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import { useProjectStore } from '@/store';
import { apiClient } from '@/api';
import type { AgentInfo, AgentModelProfile } from '@/api';

const store = useProjectStore();
const { projectName, style, size } = storeToRefs(store);

const form = reactive({
  projectName: projectName.value,
  style: style.value,
  size: size.value,
  agnesApiKey: '',
  mimoApiKey: '',
  candidates: 3,
  qualityThreshold: 7,
  fps: 24,
  outputDir: './output',
  saveIntermediate: true,
  autoPreview: true,
});

// AI-6B Agents & Models
const profiles = ref<AgentModelProfile[]>([]);
const agentRows = ref<Array<{ id: string; name: string; description: string; profile_id: string; enabled: boolean }>>([]);

async function loadAgents() {
  try {
    const [list, profs, settings] = await Promise.all([
      apiClient.listAgents(),
      apiClient.listAgentProfiles(),
      apiClient.getAgentSettings(),
    ]);
    profiles.value = profs;
    const cfg = (settings.agents || {});
    agentRows.value = list.map(a => {
      const s = cfg[a.id] || {};
      return {
        id: a.id, name: a.name, description: a.description,
        profile_id: s.model_profile_id || a.model_profile?.id || (profs[0]?.id ?? ''),
        enabled: s.enabled !== undefined ? s.enabled : a.enabled,
      };
    });
  } catch (e: any) {
    console.error('加载 Agents 设置失败', e);
  }
}

async function saveAgent(a: { id: string; name: string; profile_id: string; enabled: boolean }) {
  try {
    await apiClient.updateAgentSetting(a.id, a.profile_id, a.enabled);
    store.showNotification(`已保存 ${a.name} 设置`, 'success');
  } catch (e: any) {
    store.showNotification(`保存失败: ${e.message || e}`, 'error');
  }
}

const handleSave = async () => {
  await store.updateSettings({
    projectName: form.projectName,
    style: form.style,
    size: form.size,
  });
};

onMounted(() => {
  // 从 store 加载当前值
  form.projectName = projectName.value;
  form.style = style.value;
  form.size = size.value;
  loadAgents();
});
</script>

<style lang="scss" scoped>
.settings {
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

.grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}

.card {
  background-color: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
}

.card-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 14px;
}

.form {
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

.input {
  background-color: var(--bg-root);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 8px 10px;
  font-size: 13px;
  color: var(--text-primary);
  outline: none;
  transition: border-color 0.15s ease;

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

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-primary);
  cursor: pointer;

  input[type="checkbox"] {
    accent-color: var(--text-primary);
  }
}

.agent-row {
  display: grid;
  grid-template-columns: 1.4fr 1fr auto;
  gap: 10px;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}
.agent-info { display: flex; flex-direction: column; gap: 2px; }
.agent-name { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.agent-desc { font-size: 11px; color: var(--text-muted); }
.hint { font-size: 12px; color: var(--text-muted); }

.btn {
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 13px;
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
