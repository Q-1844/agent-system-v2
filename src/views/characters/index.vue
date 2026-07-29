<template>
  <div class="characters-page">
    <!-- 左侧角色列表 -->
    <div class="char-list" :class="{ collapsed: listCollapsed }">
      <!-- 列表头部 -->
      <div class="list-header" @click="listCollapsed = !listCollapsed">
        <span class="list-title" v-if="!listCollapsed">角色列表</span>
        <span class="list-toggle">{{ listCollapsed ? '▶' : '◀' }}</span>
      </div>

      <!-- 列表内容 -->
      <template v-if="!listCollapsed">
        <div class="list-content">
          <div v-for="char in characters" :key="char.name" class="char-item" :class="{ active: selected?.name === char.name }" @click="selectChar(char)">
            <div class="char-avatar">{{ char.name.charAt(0) }}</div>
            <div class="char-info">
              <div class="char-name">{{ char.name }}</div>
              <div class="char-desc">{{ char.description?.substring(0, 25) }}...</div>
            </div>
            <div class="char-actions">
              <button class="btn-action" @click.stop="saveCharacter(char)" title="保存到角色库">
                <span class="action-icon">💾</span>
              </button>
              <button class="btn-action btn-delete" @click.stop="confirmDeleteChar(char)" title="从列表删除">
                <span class="action-icon">×</span>
              </button>
            </div>
          </div>
        </div>

        <!-- 底部操作区 -->
        <div class="list-footer">
          <button class="btn-footer btn-add" @click="showAddDialog = true">
            <span>+</span>
            <span>添加角色</span>
          </button>
          <button class="btn-footer btn-library" @click="openLibrary">
            <span>📚</span>
            <span>角色库</span>
          </button>
        </div>
      </template>
    </div>

    <!-- 右侧工作区 -->
    <div class="workspace" v-if="selected">
      <div class="ws-header">
        <div class="header-row">
          <h2 v-if="!editingName" @dblclick="startEditName">{{ selected.name }}</h2>
          <input v-else v-model="editNameValue" class="edit-input edit-name" @blur="saveName" @keyup.enter="saveName" @keyup.escape="cancelEditName" ref="nameInput" />
          <span class="edit-hint" v-if="!editingName">双击编辑</span>
        </div>
        <div class="desc-row">
          <p v-if="!editingDesc" @dblclick="startEditDesc" class="desc-text">{{ selected.description }}</p>
          <textarea v-else v-model="editDescValue" class="edit-input edit-desc" @blur="saveDesc" @keyup.escape="cancelEditDesc" ref="descInput" rows="2"></textarea>
          <span class="edit-hint" v-if="!editingDesc">双击编辑</span>
        </div>
        <div class="bind-row">
          <span class="proj-tag">当前项目: {{ projectName }}</span>
          <button class="btn-bind" @click="bindToProject" title="把三视图落盘到项目，供分镜生成引用">绑定到项目</button>
        </div>
      </div>

      <div class="tabs">
        <button v-for="t in tabs" :key="t.key" class="tab" :class="{ active: activeTab === t.key }" @click="activeTab = t.key">{{ t.label }}</button>
      </div>

      <div class="tab-content">
        <component :is="tabComponent" :char="selected" :key="selected.name" />
      </div>
    </div>

    <div v-else class="empty">
      <div class="empty-icon">👤</div>
      <div class="empty-text">← 请选择角色</div>
    </div>

    <!-- 添加角色对话框 -->
    <div v-if="showAddDialog" class="dialog-overlay" @click.self="showAddDialog = false">
      <div class="dialog">
        <h3>添加新角色</h3>
        <div class="form-group">
          <label>角色名称</label>
          <input v-model="newCharName" placeholder="输入角色名称" @keyup.enter="addCharacter" />
        </div>
        <div class="form-group">
          <label>角色描述 (提示词)</label>
          <textarea v-model="newCharDesc" placeholder="描述角色外观..." rows="3"></textarea>
        </div>
        <div class="dialog-btns">
          <button class="btn btn-cancel" @click="showAddDialog = false">取消</button>
          <button class="btn btn-confirm" @click="addCharacter">确认</button>
        </div>
      </div>
    </div>

    <!-- 角色库面板 -->
    <div v-if="showLibrary" class="dialog-overlay" @click.self="showLibrary = false">
      <div class="dialog library-dialog">
        <div class="lib-header">
          <h3>📚 角色库</h3>
          <p class="lib-hint">已保存的角色，点击加载</p>
        </div>
        <div class="lib-list">
          <div v-for="char in store.savedCharacters" :key="char.id" class="lib-item" @click="loadFromLibrary(char.id)">
            <div class="lib-avatar">{{ char.name.charAt(0) }}</div>
            <div class="lib-info">
              <div class="lib-name">{{ char.name }}</div>
              <div class="lib-desc">{{ char.description?.substring(0, 35) }}...</div>
              <div class="lib-time">{{ formatDate(char.created_at) }}</div>
            </div>
            <button class="btn-lib-delete" @click.stop="deleteFromLibrary(char.id)" title="删除">×</button>
          </div>
          <div v-if="store.savedCharacters.length === 0" class="lib-empty">
            <div class="lib-empty-icon">📭</div>
            <div>暂无保存的角色</div>
          </div>
        </div>
        <div class="dialog-btns">
          <button class="btn btn-cancel" @click="showLibrary = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- 删除确认对话框 -->
    <div v-if="showDeleteConfirm" class="dialog-overlay" @click.self="showDeleteConfirm = false">
      <div class="dialog confirm-dialog">
        <h3>确认删除</h3>
        <p>确定从列表中删除「{{ deleteTarget?.name }}」吗？</p>
        <p class="confirm-hint">此操作不会删除已保存到角色库的数据</p>
        <div class="dialog-btns">
          <button class="btn btn-cancel" @click="showDeleteConfirm = false">取消</button>
          <button class="btn btn-danger" @click="deleteChar">删除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick, defineAsyncComponent } from 'vue';
import { storeToRefs } from 'pinia';
import { useProjectStore } from '@/store';
import { apiClient } from '@/api';

const store = useProjectStore();
const { characters, projectName } = storeToRefs(store);
const selected = ref<any>(null);
const activeTab = ref('single');
const listCollapsed = ref(false);

// 对话框状态
const showAddDialog = ref(false);
const showLibrary = ref(false);
const showDeleteConfirm = ref(false);
const deleteTarget = ref<any>(null);
const newCharName = ref('');
const newCharDesc = ref('');

// 编辑状态
const editingName = ref(false);
const editingDesc = ref(false);
const editNameValue = ref('');
const editDescValue = ref('');
const nameInput = ref<HTMLInputElement | null>(null);
const descInput = ref<HTMLTextAreaElement | null>(null);

const tabs = [
  { key: 'single', label: '单个生成' },
  { key: 'chain', label: '链式生成' },
  { key: 'sheet', label: '设定图生成' },
];

const tabComponent = computed(() => {
  if (activeTab.value === 'single') return defineAsyncComponent(() => import('./TabSingle.vue'));
  if (activeTab.value === 'chain') return defineAsyncComponent(() => import('./TabChain.vue'));
  return defineAsyncComponent(() => import('./TabSheet.vue'));
});

function selectChar(char: any) {
  selected.value = char;
}

function addCharacter() {
  if (!newCharName.value.trim()) {
    store.showNotification('请输入角色名称', 'error');
    return;
  }
  if (!newCharDesc.value.trim()) {
    store.showNotification('请输入角色描述', 'error');
    return;
  }

  const newChar = {
    name: newCharName.value.trim(),
    description: newCharDesc.value.trim(),
    reference_images: {}
  };
  characters.value.push(newChar);
  selected.value = newChar;
  newCharName.value = '';
  newCharDesc.value = '';
  showAddDialog.value = false;
  store.showNotification('角色已添加', 'success');
}

async function saveCharacter(char: any) {
  const charCache = store.getCharacterCache(char.name);
  if (charCache && charCache.views) {
    await store.saveCurrentCharacter(char.name, char.description, charCache.views);
  } else {
    store.showNotification('请先生成三视图后再保存', 'info');
  }
}

function confirmDeleteChar(char: any) {
  deleteTarget.value = char;
  showDeleteConfirm.value = true;
}

function deleteChar() {
  if (!deleteTarget.value) return;
  const index = characters.value.findIndex(c => c.name === deleteTarget.value.name);
  if (index > -1) {
    characters.value.splice(index, 1);
    if (selected.value?.name === deleteTarget.value.name) {
      selected.value = null;
    }
    store.showNotification('角色已从列表删除', 'success');
  }
  showDeleteConfirm.value = false;
  deleteTarget.value = null;
}

async function openLibrary() {
  await store.loadSavedCharacters();
  showLibrary.value = true;
}

async function loadFromLibrary(characterId: string) {
  const result = await store.loadSavedCharacter(characterId);
  if (result) {
    const existing = characters.value.find(c => c.name === result.name);
    if (!existing) {
      const newChar = {
        name: result.name,
        description: result.description,
        reference_images: {}
      };
      characters.value.push(newChar);
      selected.value = newChar;
    } else {
      selected.value = existing;
    }
    store.cacheCharacterState(result.name, result.description, result.views);
    showLibrary.value = false;
    store.showNotification(`角色 "${result.name}" 已加载`, 'success');
  }
}

async function deleteFromLibrary(characterId: string) {
  await store.deleteSavedCharacter(characterId);
}

function formatDate(dateStr: string) {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// 编辑功能
function startEditName() {
  editNameValue.value = selected.value.name;
  editingName.value = true;
  nextTick(() => nameInput.value?.focus());
}

async function saveName() {
  const newName = editNameValue.value.trim();
  editingName.value = false;
  if (!newName) return;
  const oldName = selected.value.name;
  if (newName === oldName) return;
  try {
    const r = await apiClient.updateProjectCharacter(projectName.value, oldName, { new_name: newName });
    selected.value.name = newName;
    if (r.renamed) {
      store.showNotification('已改名并同步项目文件与场景角色名', 'success');
      await store.loadProject(projectName.value);
    } else {
      store.showNotification('名称已更新（未绑定到项目，仅本地）', 'info');
    }
  } catch (e: any) {
    selected.value.name = newName;
    store.showNotification(`改名未同步到项目: ${e.message}`, 'info');
  }
}

function cancelEditName() {
  editingName.value = false;
}

function startEditDesc() {
  editDescValue.value = selected.value.description;
  editingDesc.value = true;
  nextTick(() => descInput.value?.focus());
}

async function saveDesc() {
  const newDesc = editDescValue.value.trim();
  editingDesc.value = false;
  if (!newDesc) return;
  try {
    await apiClient.updateProjectCharacter(projectName.value, selected.value.name, { description: newDesc });
    selected.value.description = newDesc;
    store.showNotification('描述已更新并同步项目', 'success');
  } catch (e: any) {
    selected.value.description = newDesc;
    store.showNotification(`描述未同步到项目（仅本地）: ${e.message}`, 'info');
  }
}

function cancelEditDesc() {
  editingDesc.value = false;
}

// 绑定当前角色到项目（落盘三视图 + 写 parsed_script.json，修复数据断裂）
async function bindToProject() {
  const char = selected.value;
  if (!char) return;
  const cache = store.getCharacterCache(char.name);
  if (!cache?.views || Object.keys(cache.views).length === 0) {
    store.showNotification('请先生成三视图再绑定到项目', 'info');
    return;
  }
  try {
    const r = await apiClient.bindCharacterToProject(projectName.value, {
      name: char.name,
      description: char.description,
      views: cache.views,
    });
    if (r.success) {
      store.showNotification(`角色已绑定到项目「${projectName.value}」，分镜生成可引用`, 'success');
      await store.loadProject(projectName.value);
    }
  } catch (e: any) {
    store.showNotification(`绑定失败: ${e.message}`, 'error');
  }
}

onMounted(() => {
  store.loadProject(projectName.value);
  store.loadSavedCharacters();
});
</script>

<style lang="scss" scoped>
.characters-page {
  display: flex;
  gap: 16px;
  height: calc(100vh - 88px);
}

// 左侧角色列表
.char-list {
  width: 240px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-surface);
  border-radius: 10px;
  border: 1px solid var(--border);
  transition: width 0.3s;

  &.collapsed {
    width: 48px;
  }
}

.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  user-select: none;

  &:hover {
    background: var(--bg-hover);
  }
}

.list-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.list-toggle {
  font-size: 10px;
  color: var(--text-muted);
}

.list-content {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.char-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    background: var(--bg-hover);

    .char-actions {
      opacity: 1;
    }
  }

  &.active {
    background: var(--bg-hover);
    border-left: 3px solid var(--accent);
  }
}

.char-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  flex-shrink: 0;
}

.char-info {
  flex: 1;
  min-width: 0;
}

.char-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.char-desc {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.char-actions {
  display: flex;
  gap: 4px;
  opacity: 0;
  transition: opacity 0.2s;
}

.btn-action {
  width: 24px;
  height: 24px;
  border-radius: 4px;
  border: none;
  background: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;

  &:hover {
    background: var(--bg-hover);
  }
}

.btn-delete:hover {
  background: #fee2e2;
  .action-icon {
    color: #ef4444;
  }
}

.action-icon {
  font-size: 14px;
  color: var(--text-muted);
}

// 底部操作区
.list-footer {
  padding: 8px;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.btn-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  border: none;
}

.btn-add {
  background: var(--bg-root);
  color: var(--text-muted);
  border: 1px dashed var(--border);

  &:hover {
    border-color: var(--accent);
    color: var(--accent);
  }
}

.btn-library {
  background: var(--bg-root);
  color: var(--text-muted);
  border: 1px solid var(--border);

  &:hover {
    background: var(--bg-hover);
    color: var(--text-primary);
  }
}

// 右侧工作区
.workspace {
  flex: 1;
  overflow-y: auto;
  background: var(--bg-surface);
  border-radius: 10px;
  border: 1px solid var(--border);
  padding: 20px;
}

.ws-header {
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}

.header-row {
  display: flex;
  align-items: center;
  gap: 12px;

  h2 {
    font-size: 20px;
    font-weight: 600;
    color: var(--text-primary);
    margin: 0;
    cursor: text;
  }
}

.desc-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-top: 8px;
}

.desc-text {
  font-size: 13px;
  color: var(--text-muted);
  margin: 0;
  cursor: text;
  flex: 1;
  line-height: 1.5;
}

.edit-hint {
  font-size: 10px;
  color: var(--text-faint);
  opacity: 0;
  transition: opacity 0.2s;

  .header-row:hover &,
  .desc-row:hover & {
    opacity: 1;
  }
}

.edit-input {
  padding: 8px 12px;
  border: 1px solid var(--accent);
  border-radius: 6px;
  font-size: 14px;
  background: var(--bg-root);
  color: var(--text-primary);
  width: 100%;

  &:focus {
    outline: none;
    box-shadow: 0 0 0 2px rgba(108, 99, 255, 0.2);
  }
}

.edit-name {
  font-size: 20px;
  font-weight: 600;
}

.edit-desc {
  font-size: 13px;
  resize: vertical;
  min-height: 50px;
}

.bind-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
}

.proj-tag {
  font-size: 12px;
  color: var(--text-muted);
  background: var(--bg-root);
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid var(--border);
}

.btn-bind {
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid var(--accent);
  background: transparent;
  color: var(--accent);
  transition: all 0.2s;

  &:hover {
    background: var(--accent);
    color: var(--accent-text);
  }
}

// 标签页
.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 20px;
}

.tab {
  padding: 8px 20px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: none;
  background: none;
  color: var(--text-muted);
  transition: all 0.2s;

  &:hover {
    background: var(--bg-hover);
  }

  &.active {
    background: var(--accent);
    color: #fff;
  }
}

.tab-content {
  min-height: 300px;
}

// 空状态
.empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--text-faint);
}

.empty-icon {
  font-size: 48px;
  opacity: 0.5;
}

.empty-text {
  font-size: 14px;
}

// 对话框
.dialog-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(4px);
}

.dialog {
  background: var(--bg-surface);
  border-radius: 12px;
  padding: 24px;
  width: 400px;
  max-width: 90vw;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);

  h3 {
    margin: 0 0 20px;
    font-size: 18px;
    font-weight: 600;
    color: var(--text-primary);
  }
}

.form-group {
  margin-bottom: 16px;

  label {
    display: block;
    font-size: 13px;
    font-weight: 500;
    color: var(--text-muted);
    margin-bottom: 6px;
  }

  input, textarea {
    width: 100%;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 14px;
    background: var(--bg-root);
    color: var(--text-primary);
    transition: border-color 0.2s;

    &:focus {
      outline: none;
      border-color: var(--accent);
    }
  }
}

.dialog-btns {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 20px;
}

// 按钮样式
.btn {
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: none;
  transition: all 0.2s;
}

.btn-cancel {
  background: var(--bg-root);
  color: var(--text-muted);
  border: 1px solid var(--border);

  &:hover {
    background: var(--bg-hover);
  }
}

.btn-confirm {
  background: var(--accent);
  color: #fff;

  &:hover {
    filter: brightness(1.1);
  }
}

.btn-danger {
  background: #ef4444;
  color: #fff;

  &:hover {
    background: #dc2626;
  }
}

// 角色库面板
.library-dialog {
  width: 480px;
  max-height: 70vh;
  display: flex;
  flex-direction: column;
}

.lib-header {
  margin-bottom: 16px;

  h3 {
    margin: 0 0 4px;
  }
}

.lib-hint {
  font-size: 12px;
  color: var(--text-muted);
  margin: 0;
}

.lib-list {
  flex: 1;
  overflow-y: auto;
  max-height: 400px;
}

.lib-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    background: var(--bg-hover);
    border-color: var(--accent);
  }
}

.lib-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  flex-shrink: 0;
}

.lib-info {
  flex: 1;
  min-width: 0;
}

.lib-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.lib-desc {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 2px;
}

.lib-time {
  font-size: 11px;
  color: var(--text-faint);
  margin-top: 4px;
}

.btn-lib-delete {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: none;
  background: none;
  color: var(--text-faint);
  font-size: 18px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;

  &:hover {
    background: #fee2e2;
    color: #ef4444;
  }
}

.lib-empty {
  text-align: center;
  padding: 40px 20px;
  color: var(--text-faint);
}

.lib-empty-icon {
  font-size: 32px;
  margin-bottom: 8px;
}

// 确认对话框
.confirm-dialog {
  p {
    font-size: 14px;
    color: var(--text-primary);
    margin: 0 0 8px;
  }
}

.confirm-hint {
  font-size: 12px;
  color: var(--text-muted);
}
</style>
