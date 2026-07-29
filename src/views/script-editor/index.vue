<template>
  <div class="script-editor">
    <div class="toolbar">
      <h2>剧本编辑</h2>
      <div class="toolbar-actions">
        <button class="btn btn-ghost" @click="addCharacter">+ 角色</button>
        <button class="btn btn-ghost" @click="addScene">+ 场景</button>
        <button class="btn btn-primary" :disabled="saving" @click="save">
          {{ saving ? '保存中...' : '保存并解析' }}
        </button>
      </div>
    </div>

    <!-- 项目信息 -->
    <div class="card">
      <div class="card-title">项目信息</div>
      <div class="form-row">
        <div class="form-group">
          <label>项目名称</label>
          <input v-model="projectName" class="input" />
        </div>
        <div class="form-group">
          <label>画风</label>
          <input v-model="style" class="input" />
        </div>
        <div class="form-group">
          <label>尺寸</label>
          <select v-model="size" class="input">
            <option>1152x768</option>
            <option>1024x768</option>
            <option>1280x720</option>
            <option>1920x1080</option>
            <option>1536x1024</option>
            <option>1024x1536</option>
          </select>
        </div>
      </div>
    </div>

    <!-- 角色列表 -->
    <div class="card">
      <div class="card-title">角色 ({{ characters.length }})</div>
      <div v-for="(c, i) in characters" :key="i" class="char-row">
        <input v-model="c.name" class="input char-name" placeholder="角色名" />
        <input v-model="c.description" class="input char-desc" placeholder="英文描述（生图提示词）" />
        <button class="btn-icon" title="删除" @click="characters.splice(i, 1)">×</button>
      </div>
      <div v-if="!characters.length" class="empty-hint">暂无角色，点「+ 角色」添加</div>
    </div>

    <!-- 场景列表 -->
    <div class="card">
      <div class="card-title">场景 ({{ scenes.length }})</div>
      <div v-for="(s, i) in scenes" :key="i" class="scene-block">
        <div class="scene-header">
          <span class="scene-id">场景 #{{ s.id }}</span>
          <button class="btn-icon" title="删除" @click="scenes.splice(i, 1)">×</button>
        </div>
        <div class="form-group">
          <label>描述（中文，会被翻译成英文提示词）</label>
          <textarea v-model="s.description" class="input" rows="2" placeholder="场景描述"></textarea>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>出场角色（逗号分隔）</label>
            <input :value="sceneCharsStr(s)" @input="setSceneChars(s, ($event.target as HTMLInputElement).value)" class="input" placeholder="小星, 暗影" />
          </div>
          <div class="form-group">
            <label>镜头</label>
            <input v-model="s.camera" class="input" placeholder="wide shot" />
          </div>
          <div class="form-group">
            <label>光照</label>
            <input v-model="s.lighting" class="input" placeholder="golden sunrise" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>动作</label>
            <input v-model="s.motion" class="input" placeholder="hair flowing in wind" />
          </div>
          <div class="form-group">
            <label>对白</label>
            <input v-model="s.dialogue" class="input" placeholder="台词（可选）" />
          </div>
        </div>
      </div>
      <div v-if="!scenes.length" class="empty-hint">暂无场景，点「+ 场景」添加</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import { useProjectStore } from '@/store';
import { apiClient } from '@/api';

const store = useProjectStore();
const { projectName, style, size, characters, scenes } = storeToRefs(store);
const saving = ref(false);

function addCharacter() {
  characters.value.push({ name: '', description: '', reference_images: {} });
}

function addScene() {
  const nextId = scenes.value.length
    ? Math.max(...scenes.value.map((s: any) => s.id || 0)) + 1
    : 1;
  scenes.value.push({
    id: nextId,
    description: '',
    characters: [],
    camera: '',
    lighting: '',
    motion: '',
    dialogue: '',
    status: 'pending',
  });
}

function sceneCharsStr(s: any) {
  return (s.characters || []).join(', ');
}

function setSceneChars(s: any, val: string) {
  s.characters = val.split(/[,，]/).map((x: string) => x.trim()).filter(Boolean);
}

function buildYaml(): string {
  const L: string[] = [];
  L.push(`project: "${projectName.value}"`);
  L.push(`style: "${style.value}"`);
  L.push(`size: "${size.value}"`);
  L.push('');
  L.push('characters:');
  if (!characters.value.length) {
    L.push('  []');
  } else {
    for (const c of characters.value) {
      L.push(`  - name: "${(c.name || '').replace(/"/g, '\\"')}"`);
      L.push(`    description: "${(c.description || '').replace(/"/g, '\\"')}"`);
    }
  }
  L.push('');
  L.push('scenes:');
  if (!scenes.value.length) {
    L.push('  []');
  } else {
    for (const s of scenes.value as any[]) {
      L.push(`  - id: ${s.id}`);
      L.push(`    description: "${(s.description || '').replace(/"/g, '\\"')}"`);
      L.push(`    characters: ${JSON.stringify(s.characters || [])}`);
      L.push(`    camera: "${(s.camera || '').replace(/"/g, '\\"')}"`);
      L.push(`    lighting: "${(s.lighting || '').replace(/"/g, '\\"')}"`);
      L.push(`    motion: "${(s.motion || '').replace(/"/g, '\\"')}"`);
      if (s.dialogue) {
        L.push(`    dialogue: "${(s.dialogue || '').replace(/"/g, '\\"')}"`);
      }
    }
  }
  return L.join('\n');
}

async function save() {
  if (!projectName.value.trim()) {
    store.showNotification('请填写项目名称', 'error');
    return;
  }
  if (!scenes.value.length) {
    store.showNotification('至少需要一个场景', 'error');
    return;
  }
  saving.value = true;
  try {
    const yaml = buildYaml();
    const r = await apiClient.saveProjectScript(projectName.value, yaml, false);
    store.showNotification(`已保存：${r.characters_count} 角色，${r.scenes_count} 场景`, 'success');
    await store.loadProject(projectName.value);
  } catch (e: any) {
    store.showNotification(`保存失败: ${e.message}`, 'error');
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  await store.loadProject(projectName.value);
});
</script>

<style lang="scss" scoped>
.script-editor {
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

.toolbar-actions {
  display: flex;
  gap: 8px;
}

.card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}

.card-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 14px;
}

.form-row {
  display: flex;
  gap: 12px;
}

.form-group {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;

  label {
    font-size: 12px;
    color: var(--text-muted);
  }
}

.input {
  background: var(--bg-root);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 8px 10px;
  font-size: 13px;
  color: var(--text-primary);
  outline: none;
  transition: border-color 0.15s ease;
  width: 100%;

  &:focus {
    border-color: var(--accent);
  }
}

.char-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  align-items: center;
}

.char-name {
  flex: 0 0 140px;
}

.char-desc {
  flex: 1;
}

.scene-block {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 12px;
  background: var(--bg-root);
}

.scene-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.scene-id {
  font-size: 13px;
  font-weight: 600;
  color: var(--accent);
}

.btn-icon {
  width: 26px;
  height: 26px;
  border-radius: 4px;
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;

  &:hover {
    background: #fee2e2;
    color: #ef4444;
  }
}

.empty-hint {
  color: var(--text-faint);
  font-size: 13px;
  padding: 16px 0;
  text-align: center;
}

.btn {
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: none;
  transition: all 0.15s ease;

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}

.btn-primary {
  background: var(--accent);
  color: var(--accent-text);

  &:hover:not(:disabled) {
    background: var(--accent-hover);
  }
}

.btn-ghost {
  background: none;
  color: var(--text-muted);
  border: 1px solid var(--border);

  &:hover {
    background: var(--bg-hover);
    color: var(--text-secondary);
  }
}
</style>
