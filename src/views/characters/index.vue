<template>
  <div class="characters-page">
    <!-- 2026-08-30：Character Designer 运行期只读提示（角色域资源锁） -->
    <div v-if="charactersLocked" class="agent-lock-banner">
      <span class="lock-spinner">◌</span> AI 正在修改角色设定，编辑暂时锁定，完成后自动恢复
    </div>
    <!-- 左侧角色列表 -->
    <aside class="char-list" :class="{ collapsed: listCollapsed }">
      <!-- 列表头部：标题 + ＋新建 + 收起 -->
      <div class="list-header">
        <span class="list-title">角色列表</span>
        <div class="list-header-actions">
          <button class="icon-btn" title="新建角色" @click="showAddDialog = true">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
          </button>
          <button class="icon-btn" title="收起角色列表" @click="listCollapsed = true">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6" /></svg>
          </button>
        </div>
      </div>

      <!-- 列表内容：文字优先，无头像 -->
      <div class="list-content">
        <button
          v-if="characters.length"
          class="ai-batch-entry"
          @click="openDesignStudio(null)"
        >✨ AI 角色设计</button>
        <div
          v-for="char in characters"
          :key="char.name"
          class="char-item"
          :class="{ active: selected?.name === char.name }"
          @click="selectChar(char)"
        >
          <span class="char-name">{{ char.name }}</span>
          <span class="ai-mark" v-if="statusClass(char) === 'none' || statusClass(char) === 'partial'" title="AI 可设计">AI</span>
          <span class="char-status" :class="statusClass(char)">{{ statusText(char) }}</span>
        </div>
        <div v-if="!characters.length" class="list-empty">暂无角色</div>
      </div>

      <!-- 底部：角色库入口（分隔） -->
      <div class="list-footer">
        <button class="lib-entry" @click="openLibrary">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg>
          <span>角色库</span>
        </button>
      </div>
    </aside>

    <!-- 右侧：展开按钮(收起时) + 详情/空状态 -->
    <section class="right-area">
      <button v-if="listCollapsed" class="expand-fab" title="展开角色列表" @click="listCollapsed = false">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6" /></svg>
        <span>角色</span>
      </button>

      <!-- 选中角色：工作区 -->
      <div class="workspace" v-if="selected">
        <div class="ws-header">
          <div class="header-row">
            <h2 v-if="!editingName" @dblclick="startEditName">{{ selected.name }}</h2>
            <input v-else v-model="editNameValue" class="edit-input edit-name" @blur="saveName" @keyup.enter="saveName" @keyup.escape="cancelEditName" ref="nameInput" />
            <span class="edit-hint" v-if="!editingName">双击编辑</span>
            <div class="ws-actions">
              <button class="btn-ghost" :title="selected.description ? 'AI 优化角色设定' : 'AI 设计角色形象'" @click="openDesignStudio(selected)">AI 设计角色</button>
              <button class="btn-ghost" title="保存到角色库" @click="saveCharacter(selected)">保存到角色库</button>
              <button class="btn-ghost danger" title="删除角色" @click="confirmDeleteChar(selected)">删除</button>
            </div>
          </div>
          <div class="desc-row">
            <p v-if="!editingDesc" @dblclick="startEditDesc" class="desc-text">{{ selected.description }}</p>
            <textarea v-else v-model="editDescValue" class="edit-input edit-desc" @blur="saveDesc" @keyup.escape="cancelEditDesc" ref="descInput" rows="2"></textarea>
            <span class="edit-hint" v-if="!editingDesc">双击编辑</span>
          </div>
        </div>

        <div class="tabs">
          <button v-for="t in tabs" :key="t.key" class="tab" :class="{ active: activeTab === t.key }" @click="activeTab = t.key">{{ t.label }}</button>
        </div>

        <div class="tab-content">
          <component :is="tabComponent" :char="selected" :key="selected.name" />
        </div>
      </div>

      <!-- 空项目引导 -->
      <div class="guide" v-else-if="!characters.length">
        <div class="guide-title">创建你的第一个角色</div>
        <div class="guide-desc">输入角色名称和外观描述，<br />生成正面、侧面、背面三视图。</div>
        <div class="guide-actions">
          <button class="btn-primary" @click="showAddDialog = true">＋ 新建角色</button>
          <button class="btn-ghost" @click="openLibrary">从角色库导入</button>
        </div>
      </div>

      <!-- 有角色但未选中 -->
      <div class="select-prompt" v-else>
        <div>选择左侧角色查看详情</div>
      </div>
    </section>

    <!-- 添加角色对话框 -->
    <div v-if="showAddDialog" class="dialog-overlay" @click.self="showAddDialog = false">
      <div class="dialog">
        <h3>新建角色</h3>
        <div class="form-group">
          <label>角色名称</label>
          <input v-model="newCharName" placeholder="输入角色名称" @keyup.enter="addCharacter" ref="addNameInput" />
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
          <h3>角色库</h3>
          <p class="lib-hint">已保存的角色，点击加载</p>
        </div>
        <div class="lib-list">
          <div v-for="char in store.savedCharacters" :key="char.id" class="lib-item" @click="loadFromLibrary(char.id)">
            <div class="lib-info">
              <div class="lib-name">{{ char.name }}</div>
              <div class="lib-desc">{{ char.description?.substring(0, 35) }}...</div>
              <div class="lib-time">{{ formatDate(char.created_at) }}</div>
            </div>
            <button class="btn-lib-delete" @click.stop="deleteFromLibrary(char.id)" title="删除">×</button>
          </div>
          <div v-if="store.savedCharacters.length === 0" class="lib-empty">
            <div>暂无保存的角色</div>
          </div>
        </div>
        <div class="dialog-btns">
          <button class="btn btn-cancel" @click="showLibrary = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- 删除确认 -->
    <div v-if="showDeleteConfirm" class="dialog-overlay" @click.self="showDeleteConfirm = false">
      <div class="dialog confirm-dialog">
        <h3>确认删除</h3>
        <p>确定删除角色「{{ deleteTarget?.name }}」吗？</p>
        <p class="confirm-hint">仅从当前列表删除，不影响角色库</p>
        <div class="dialog-btns">
          <button class="btn btn-cancel" @click="showDeleteConfirm = false">取消</button>
          <button class="btn btn-danger" @click="deleteChar">删除</button>
        </div>
      </div>
    </div>

    <!-- AI 角色设计工作区（Phase AI-2） -->
    <CharacterDesignStudio
      v-if="showDesignStudio"
      :project-name="projectName"
      :initial-character-name="designInitialName"
      @close="showDesignStudio = false"
      @applied="onDesignChanged"
      @generated="onDesignChanged"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick, watch, defineAsyncComponent } from 'vue';
import { storeToRefs } from 'pinia';
import { useProjectStore } from '@/store';
import { useAgentUiStore } from '@/store/agentUi';
import { useProjectEventsStore } from '@/store/projectEvents';
import { apiClient } from '@/api';
import { useRoute } from 'vue-router';
import CharacterDesignStudio from '@/components/CharacterDesignStudio.vue';

const route = useRoute();
const store = useProjectStore();
const agentUi = useAgentUiStore();
const { characters, projectName } = storeToRefs(store);
const selected = ref<any>(null);
const activeTab = ref('single');
const listCollapsed = ref(false);

const showAddDialog = ref(false);
const showLibrary = ref(false);
const showDeleteConfirm = ref(false);
const deleteTarget = ref<any>(null);
const newCharName = ref('');
const newCharDesc = ref('');
const showDesignStudio = ref(false);
const designInitialName = ref<string | null>(null);

const editingName = ref(false);
const editingDesc = ref(false);
const editNameValue = ref('');
const editDescValue = ref('');
const nameInput = ref<HTMLInputElement | null>(null);
const descInput = ref<HTMLTextAreaElement | null>(null);
const addNameInput = ref<HTMLInputElement | null>(null);

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

// 三视图状态：从缓存统计 front/side/back 的完成数
function statusText(char: any): string {
  const cache = store.getCharacterCache(char.name);
  const views = cache?.views || {};
  const done = (['front', 'side', 'back'] as const).filter(k => views[k]?.seg || views[k]?.raw).length;
  const gen = store.getGeneratingState(char.name);
  const busy = !!(gen && Object.values(gen.busy || {}).some(Boolean));
  if (busy) return '生成中';
  if (done === 0) return '尚未生成';
  if (done === 3) return '三视图完成';
  return `已完成 ${done}/3`;
}
function statusClass(char: any): string {
  const cache = store.getCharacterCache(char.name);
  const views = cache?.views || {};
  const done = (['front', 'side', 'back'] as const).filter(k => views[k]?.seg || views[k]?.raw).length;
  const gen = store.getGeneratingState(char.name);
  if (gen && Object.values(gen.busy || {}).some(Boolean)) return 'busy';
  if (done === 3) return 'done';
  if (done === 0) return 'none';
  return 'partial';
}

function selectChar(char: any) {
  selected.value = char;
  // AI-7C-H §33 Probe 2：选中角色即成为 Floating Agent 的页面上下文
  // （contextScope.characterName → 后端 entity_refs，Agent 不反问 Which Character）。
  if (char?.name) {
    agentUi.setContext({ type: 'character', name: char.name });
  }
}

async function addCharacter() {
  if (charactersLocked.value) { store.showNotification('Agent 正在修改角色，请稍候', 'info'); return; }
  if (!newCharName.value.trim()) { store.showNotification('请输入角色名称', 'error'); return; }
  if (!newCharDesc.value.trim()) { store.showNotification('请输入角色描述', 'error'); return; }
  const name = newCharName.value.trim();
  const description = newCharDesc.value.trim();
  // 立即落盘到当前项目（v2 project.json），刷新不丢，且只属于当前项目
  try {
    await apiClient.bindCharacterToProject(projectName.value, { name, description, views: {} });
    const newChar = { name, description, reference_images: {} };
    characters.value.push(newChar);
    selected.value = newChar;
    newCharName.value = '';
    newCharDesc.value = '';
    showAddDialog.value = false;
  } catch (e: any) {
    store.showNotification(`创建失败: ${e.message}`, 'error');
  }
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

async function deleteChar() {
  if (!deleteTarget.value) return;
  if (charactersLocked.value) { store.showNotification('Agent 正在修改角色，请稍候', 'info'); showDeleteConfirm.value = false; return; }
  const target = deleteTarget.value;
  try {
    await apiClient.deleteProjectCharacter(projectName.value, target.name);
    const index = characters.value.findIndex(c => c.name === target.name);
    if (index > -1) characters.value.splice(index, 1);
    if (selected.value?.name === target.name) selected.value = null;
  } catch (e: any) {
    store.showNotification(`删除失败: ${e.message}`, 'error');
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
  if (!result) return;
  // 复制进当前项目（独立副本，GPT §库语义：库原件与其他项目不受影响）
  try {
    await apiClient.bindCharacterToProject(projectName.value, {
      name: result.name, description: result.description, views: result.views,
    });
    const existing = characters.value.find(c => c.name === result.name);
    if (!existing) {
      const newChar = { name: result.name, description: result.description, reference_images: {} };
      characters.value.push(newChar);
      selected.value = newChar;
    } else {
      selected.value = existing;
    }
    store.cacheCharacterState(result.name, result.description, result.views);
    showLibrary.value = false;
  } catch (e: any) {
    store.showNotification(`导入失败: ${e.message}`, 'error');
  }
}

async function deleteFromLibrary(characterId: string) {
  await store.deleteSavedCharacter(characterId);
}

function formatDate(dateStr: string) {
  if (!dateStr) return '';
  return new Date(dateStr).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function startEditName() {
  if (charactersLocked.value) { store.showNotification('Agent 正在修改角色，请稍候', 'info'); return; }
  editNameValue.value = selected.value.name;
  editingName.value = true;
  nextTick(() => nameInput.value?.focus());
}
async function saveName() {
  const newName = editNameValue.value.trim();
  editingName.value = false;
  if (!newName || newName === selected.value.name) return;
  if (charactersLocked.value) return;   // 锁定期不落盘（本地编辑已随锁定刷新被覆盖）
  const oldName = selected.value.name;
  selected.value.name = newName;   // 本地立即更新
  try {
    await apiClient.updateProjectCharacter(projectName.value, oldName, { new_name: newName });
  } catch (e: any) {
    // 角色尚未落盘到项目时同步会失败，静默处理（本地已更新）；数据接线批次后自然生效
  }
}
function cancelEditName() { editingName.value = false; }

function startEditDesc() {
  if (charactersLocked.value) { store.showNotification('Agent 正在修改角色，请稍候', 'info'); return; }
  editDescValue.value = selected.value.description;
  editingDesc.value = true;
  nextTick(() => descInput.value?.focus());
}
async function saveDesc() {
  const newDesc = editDescValue.value.trim();
  editingDesc.value = false;
  if (!newDesc) return;
  if (charactersLocked.value) return;   // 锁定期不落盘
  selected.value.description = newDesc;   // 本地立即更新
  try {
    await apiClient.updateProjectCharacter(projectName.value, selected.value.name, { description: newDesc });
  } catch (e: any) {
    // 同上，静默
  }
}
function cancelEditDesc() { editingDesc.value = false; }

onMounted(() => {
  store.loadProjectCharacters(projectName.value);
  store.loadSavedCharacters();
  // Phase AI-2：从剧本页跳转 ?design=charName -> 选中该角色 + 打开 AI 设计工作区
  const d = route.query.design;
  if (d && typeof d === 'string') {
    designInitialName.value = d;
    const stop = watch(characters, (list) => {
      const ch = list.find((c: any) => c.name === d);
      if (ch) {
        selected.value = ch;
        showDesignStudio.value = true;
        stop();
      }
    }, { immediate: true });
  }
});

// 切项目：清选中（右侧不显示旧项目角色）+ 加载新项目角色（GPT §3）
watch(projectName, () => {
  selected.value = null;
  showDesignStudio.value = false;
  store.loadProjectCharacters(projectName.value);
});

// ---- 2026-08-30：Character Designer 运行期的角色域只读锁 + 实时刷新 ----
// 事件由 app 级 useProjectEvents（MainLayout 挂载）喂进 store；本页只消费状态。
// 锁定/解锁瞬间同步基线数据并退出本地编辑态（锁定期用户不可编辑，覆盖安全）；
// Agent 每次落盘（character.changed）且仍在锁定期 → 静默刷新列表与选中角色。
const pev = useProjectEventsStore();
const charactersLocked = computed(() => pev.charactersLocked);
watch(charactersLocked, (locked) => {
  if (!projectName.value) return;
  if (locked) {
    editingName.value = false;
    editingDesc.value = false;
  }
  store.loadProjectCharacters(projectName.value);
});
watch(() => pev.charactersVersion, () => {
  if (projectName.value && pev.charactersLocked) store.loadProjectCharacters(projectName.value);
});

// ---- AI 角色设计（Phase AI-2）----
function openDesignStudio(char: any) {
  designInitialName.value = char?.name || null;
  showDesignStudio.value = true;
}
function onDesignChanged() {
  // AI 设计应用 / 三视图生成后刷新角色缓存（description_en / views 更新）
  store.loadProjectCharacters(projectName.value);
}

// 打开新建对话框时聚焦
watch(showAddDialog, (v) => { if (v) nextTick(() => addNameInput.value?.focus()); });
</script>

<style lang="scss" scoped>
.characters-page {
  display: flex;
  gap: 0;            // 收起时无缝，靠 char-list 自身 border 分隔
  height: calc(100vh - 88px);
  position: relative;
}

// 2026-08-30：Character Designer 运行期只读横幅（顶部居中，不挡操作只提示状态）
.agent-lock-banner {
  position: absolute;
  top: 10px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 14px;
  border-radius: 14px;
  font-size: 12px;
  color: #60a5fa;
  background: rgba(96, 165, 250, 0.12);
  border: 1px solid rgba(96, 165, 250, 0.4);
  pointer-events: none;
  .lock-spinner { display: inline-block; animation: char-lock-spin 1s linear infinite; }
}
@keyframes char-lock-spin { to { transform: rotate(360deg); } }

// ===== 左侧角色列表 =====
.char-list {
  width: 220px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-surface);
  border-right: 1px solid var(--border);
  transition: width 0.2s ease, border-color 0.2s ease;
  overflow: hidden;

  &.collapsed {
    width: 0;
    border-right: none;
    background: transparent;
  }
}

.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 12px 12px;
  flex-shrink: 0;
}

.list-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.list-header-actions {
  display: flex;
  gap: 2px;
}

.icon-btn {
  width: 26px;
  height: 26px;
  border-radius: 5px;
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  &:hover { background: var(--bg-hover); color: var(--text-secondary); }
}

.list-content {
  flex: 1;
  overflow-y: auto;
  padding: 2px 8px;
}

.ai-batch-entry {
  width: 100%; margin-bottom: 6px; padding: 7px; border-radius: 6px;
  border: 1px solid var(--accent); background: transparent; color: var(--accent);
  font-size: 12px; cursor: pointer;
  &:hover { background: var(--accent); color: var(--accent-text); }
}

.char-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  border-left: 2px solid transparent;
  cursor: pointer;
  transition: background 0.12s ease;

  &:hover { background: var(--bg-hover); }
  &.active {
    background: var(--bg-hover);
    border-left-color: var(--accent);
  }
}

.ai-mark {
  font-size: 9px; font-weight: 600; color: var(--accent);
  border: 1px solid var(--accent); border-radius: 3px; padding: 0 3px;
}

.char-name {
  font-size: 13px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  .active & { color: var(--text-primary); font-weight: 500; }
}

.char-status {
  font-size: 11px;
  color: var(--text-faint);
  flex-shrink: 0;
  &.done { color: #4ade80; }
  &.partial { color: var(--text-muted); }
  &.busy { color: #60a5fa; }
  &.none { color: var(--text-faint); }
}

.list-empty {
  padding: 24px 10px;
  text-align: center;
  font-size: 12px;
  color: var(--text-faint);
}

.list-footer {
  padding: 8px;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

.lib-entry {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  border: none;
  background: none;
  color: var(--text-muted);
  font-size: 13px;
  cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-secondary); }
}

// ===== 右侧 =====
.right-area {
  flex: 1;
  min-width: 0;
  position: relative;
  overflow-y: auto;
}

.expand-fab {
  position: absolute;
  top: 14px;
  left: 12px;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px 5px 8px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-secondary); }
}

.workspace {
  padding: 20px 24px;
}

.ws-header {
  margin-bottom: 18px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}

.header-row {
  display: flex;
  align-items: center;
  gap: 10px;
  h2 { font-size: 20px; font-weight: 600; color: var(--text-primary); margin: 0; cursor: text; }
}

.ws-actions {
  margin-left: auto;
  display: flex;
  gap: 6px;
}

.btn-ghost {
  padding: 5px 12px;
  border-radius: 5px;
  border: 1px solid var(--border);
  background: none;
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-secondary); }
  &.danger:hover { background: #fee2e2; color: #ef4444; border-color: #ef4444; }
}

.desc-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
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
  transition: opacity 0.15s;
  .header-row:hover &, .desc-row:hover & { opacity: 1; }
}

.edit-input {
  padding: 6px 10px;
  border: 1px solid var(--accent);
  border-radius: 5px;
  font-size: 14px;
  background: var(--bg-root);
  color: var(--text-primary);
  width: 100%;
  &:focus { outline: none; box-shadow: 0 0 0 2px rgba(108, 99, 255, 0.2); }
}

.edit-name { font-size: 18px; font-weight: 600; }
.edit-desc { font-size: 13px; resize: vertical; min-height: 44px; }

.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 18px;
}

.tab {
  padding: 7px 18px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: none;
  background: none;
  color: var(--text-muted);
  &:hover { background: var(--bg-hover); }
  &.active { background: var(--accent); color: #fff; }
}

.tab-content { min-height: 300px; }

// ===== 空状态引导 =====
.guide {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  text-align: center;
  padding: 40px;
}

.guide-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.guide-desc {
  font-size: 13px;
  color: var(--text-muted);
  line-height: 1.7;
}

.guide-actions {
  display: flex;
  gap: 10px;
  margin-top: 8px;
}

.btn-primary {
  padding: 8px 16px;
  border-radius: 6px;
  border: none;
  background: var(--accent);
  color: var(--accent-text);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  &:hover { background: var(--accent-hover); }
}

.select-prompt {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-faint);
  font-size: 13px;
}

// ===== 对话框（复用原样式） =====
.dialog-overlay {
  position: fixed;
  inset: 0;
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
  h3 { margin: 0 0 18px; font-size: 16px; font-weight: 600; color: var(--text-primary); }
}

.form-group {
  margin-bottom: 16px;
  label { display: block; font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }
  input, textarea {
    width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 6px;
    font-size: 14px; background: var(--bg-root); color: var(--text-primary); outline: none;
    &:focus { border-color: var(--accent); }
  }
}

.dialog-btns { display: flex; gap: 8px; justify-content: flex-end; }

.btn {
  padding: 8px 16px; border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer; border: none;
}
.btn-cancel { background: var(--bg-root); color: var(--text-muted); border: 1px solid var(--border); &:hover { background: var(--bg-hover); } }
.btn-confirm { background: var(--accent); color: var(--accent-text); &:hover { filter: brightness(1.1); } }
.btn-danger { background: #ef4444; color: #fff; &:hover { background: #dc2626; } }

.library-dialog { width: 460px; max-height: 70vh; display: flex; flex-direction: column; }
.lib-header { margin-bottom: 14px; h3 { margin: 0 0 4px; } p { font-size: 12px; color: var(--text-muted); margin: 0; } }
.lib-list { flex: 1; overflow-y: auto; max-height: 400px; }
.lib-item {
  display: flex; align-items: center; gap: 12px; padding: 12px; border: 1px solid var(--border);
  border-radius: 8px; margin-bottom: 8px; cursor: pointer;
  &:hover { background: var(--bg-hover); border-color: var(--accent); }
}
.lib-info { flex: 1; min-width: 0; }
.lib-name { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.lib-desc { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.lib-time { font-size: 11px; color: var(--text-faint); margin-top: 4px; }
.btn-lib-delete {
  width: 26px; height: 26px; border-radius: 50%; border: none; background: none;
  color: var(--text-faint); font-size: 16px; cursor: pointer;
  &:hover { background: #fee2e2; color: #ef4444; }
}
.lib-empty { text-align: center; padding: 36px 20px; color: var(--text-faint); font-size: 13px; }
.confirm-dialog p { font-size: 14px; color: var(--text-primary); margin: 0 0 8px; }
.confirm-hint { font-size: 12px; color: var(--text-muted); }
</style>
