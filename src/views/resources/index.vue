<template>
  <div class="resources-page">
    <!-- 顶部：scope + 类型 -->
    <header class="res-header">
      <div class="scope-toggle">
        <button :class="{ active: scope === 'project' }" @click="scope = 'project'">项目资源</button>
        <button :class="{ active: scope === 'shared' }" @click="switchShared">共享库</button>
      </div>
      <nav v-if="scope === 'project'" class="type-tabs">
        <button v-for="t in PROJECT_TABS" :key="t.key" :class="{ active: tab === t.key }" @click="tab = t.key">{{ t.label }}</button>
      </nav>
      <span v-else class="shared-hint">跨项目复用母版（Publish → Shared / Import → Project snapshot，修改互不污染）</span>
      <span class="spacer"></span>
      <span class="proj-name">{{ projectName }}</span>
    </header>

    <div v-if="errorMsg" class="res-error">
      <span>{{ errorMsg }}</span>
      <button class="dismiss" @click="errorMsg = ''">×</button>
    </div>

    <!-- Phase B：Resource Agent 正在修改角色/生产资源时的只读横幅（识别 character + resources 两类锁；
         改动经 SSE 实时同步进来，完成后自动恢复编辑） -->
    <div v-if="agentLockActive" class="agent-lock-banner">
      Resource Agent 正在修改{{ agentLockDomain === 'both' ? '角色与生产资源' : agentLockDomain === 'resources' ? '生产资源' : '角色' }}，页面暂为只读；改动会实时同步进来，完成后自动恢复编辑。
    </div>

    <!-- ================= 角色（卡片 → 角色工作台弹窗） ================= -->
    <section v-if="scope === 'project' && tab === 'characters'" class="tab-surface">
      <div class="surface-head">
        <h3>角色 <span class="count mono-num">{{ characters.length }}</span></h3>
        <span class="meta">点击卡片打开角色工作台（身份 · 三视图 · Looks · 组合参考）</span>
        <span class="spacer"></span>
        <button class="btn btn-quiet btn-sm" :disabled="!characters.length" @click="openDesignStudio(null)">AI 批量设计</button>
        <button class="btn btn-quiet btn-sm" @click="openLibrary">角色库</button>
        <button class="btn btn-primary btn-sm" :disabled="charactersLocked" @click="showAddDialog = true">＋ 新建角色</button>
      </div>

      <div class="card-grid" v-if="characters.length">
        <button v-for="c in characters" :key="c.name" class="char-card" @click="openWorkbench(c.name)">
          <span class="char-avatar">
            <img v-if="avatarUrl(c.name)" :src="avatarUrl(c.name)" alt="" />
            <span v-else class="char-avatar-ph">{{ c.name.slice(0, 1) }}</span>
          </span>
          <span class="char-info">
            <span class="char-name">{{ c.name }}<span class="ai-mark" v-if="designNeedsAi(c.name)" title="尚未应用 AI 设定">AI</span></span>
            <span class="char-status"><span class="dot" :class="statusDot(c)"></span>{{ statusText(c) }}</span>
            <span class="char-meta">视图 {{ (c.views && Object.keys(c.views).length) || 0 }} · {{ descSnippet(c) }}</span>
          </span>
        </button>
      </div>
      <div v-else class="guide">
        <div class="guide-title">创建第一个角色</div>
        <div class="guide-desc">输入名称与外观描述，或用 AI 从剧本整理出角色，<br />再由一张设定图裁剪出正 / 侧 / 背三视图。</div>
        <div class="guide-actions">
          <button class="btn btn-primary" @click="showAddDialog = true">+ 新建角色</button>
          <button class="btn btn-outline" @click="openLibrary">从角色库导入</button>
        </div>
      </div>
    </section>

    <!-- ================= 服装（卡片 → 详情弹窗） ================= -->
    <section v-if="scope === 'project' && tab === 'clothings'" class="tab-surface">
      <div class="surface-head">
        <h3>服装 <span class="count mono-num">{{ clothings.length }}</span></h3>
        <span class="meta">点击卡片查看详情、参考图与发布</span>
        <span class="spacer"></span>
        <button class="btn btn-primary btn-sm" :disabled="resourcesLocked" @click="showCreateClothing = !showCreateClothing">＋ 新建服装</button>
      </div>
      <form v-if="showCreateClothing" class="create-form" @submit.prevent="createClothingAction">
        <input v-model="newClothing.name" placeholder="名称（如 Black Coat）" required />
        <input v-model="newClothing.description" placeholder="描述（可选）" />
        <button class="btn btn-sm btn-primary" type="submit" :disabled="busy">创建</button>
      </form>
      <div class="card-grid" v-if="clothings.length">
        <button v-for="cl in clothings" :key="cl.id" class="asset-card" @click="openClothing(cl.id)">
          <span class="asset-name">{{ cl.name }}</span>
          <span class="asset-sub">{{ cl.relationship_summary || '未被引用' }}</span>
          <span class="asset-meta">被 {{ cl.used_by_looks?.length || 0 }} 个 Look 引用</span>
        </button>
      </div>
      <p v-else class="tab-empty">还没有服装——服装给 Look 提供一致性参考。</p>
    </section>

    <!-- ================= 道具（卡片 → 详情弹窗） ================= -->
    <section v-if="scope === 'project' && tab === 'props'" class="tab-surface">
      <div class="surface-head">
        <h3>道具 <span class="count mono-num">{{ props.length }}</span></h3>
        <span class="meta">点击卡片查看参考图、审查与批准</span>
        <span class="spacer"></span>
        <button class="btn btn-primary btn-sm" :disabled="resourcesLocked" @click="showCreateProp = !showCreateProp">＋ 新建道具</button>
      </div>
      <form v-if="showCreateProp" class="create-form" @submit.prevent="createPropAction">
        <input v-model="newProp.name" placeholder="名称（如 Lantern）" required />
        <input v-model="newProp.description" placeholder="描述（可选）" />
        <button class="btn btn-sm btn-primary" type="submit" :disabled="busy">创建</button>
      </form>
      <div class="card-grid" v-if="props.length">
        <button v-for="pt in props" :key="pt.id" class="asset-card" @click="openProp(pt.id)">
          <span class="asset-name">{{ pt.name }}</span>
          <span class="asset-sub">{{ pt.relationship_summary || pt.description || ' ' }}</span>
          <span class="asset-meta">参考图 {{ pt.reference_asset_ids?.length || 0 }} · 已批准 {{ pt.approved_reference_asset_ids?.length || 0 }}</span>
        </button>
      </div>
      <p v-else class="tab-empty">还没有道具——道具参与组合参考（Composition）。</p>
    </section>

    <!-- ================= 共享库 ================= -->
    <section v-if="scope === 'shared'" class="tab-surface">
      <div class="surface-head">
        <h3>共享库 <span class="count mono-num">{{ sharedList.length }}</span></h3>
        <span class="meta">导入会复制副本进当前项目「{{ projectName }}」，origin 记录来源与版本</span>
      </div>
      <div class="card-grid" v-if="sharedList.length">
        <div v-for="s in sharedList" :key="s.id" class="shared-card">
          <span class="badge kind">{{ s.type }}</span>
          <strong>{{ s.name }}</strong>
          <span class="asset-meta">v{{ s.version }} · 来自 {{ s.origin_project || '?' }}</span>
          <span v-if="s.character_snapshot" class="asset-meta">角色: {{ s.character_snapshot.name }}</span>
          <button class="btn btn-sm btn-primary" :disabled="busy" @click="importAction(s)">导入到项目</button>
        </div>
      </div>
      <p v-else class="tab-empty">共享库为空（在项目资源里点「发布到共享库」）</p>
    </section>

    <!-- ================= 角色工作台弹窗 ================= -->
    <CharacterWorkbenchDialog v-if="wbChar" :project-name="projectName" :character-name="wbChar"
                              :characters="characters" :clothings="clothings" :prop-options="props"
                              :doc-ret="docRev" @close="wbChar = ''" @refresh="onWorkbenchRefresh" />

    <!-- ================= 服装 / 道具详情弹窗 ================= -->
    <ClothingDialog v-if="clothingId" :project-name="projectName" :clothing-id="clothingId"
                    :doc-ret="docRev" @close="clothingId = ''" @refresh="refreshBase" />
    <PropDialog v-if="propId" :project-name="projectName" :prop-id="propId"
                :doc-ret="docRev" @close="propId = ''" @refresh="refreshBase" />

    <!-- ================= 新建角色 ================= -->
    <div v-if="showAddDialog" class="dlg-overlay" @click.self="showAddDialog = false">
      <div class="dlg">
        <div class="dlg-head"><h3>新建角色</h3><button class="dlg-close" @click="showAddDialog = false">✕</button></div>
        <div class="dlg-body">
          <label class="dlg-field"><span>名称</span>
            <input v-model="newCharName" placeholder="角色名" ref="addNameInput" @keyup.enter="addCharacter" />
          </label>
          <label class="dlg-field"><span>外观描述（生成用）</span>
            <textarea v-model="newCharDesc" rows="3" placeholder="如：短发女生，穿米色风衣…"></textarea>
          </label>
        </div>
        <div class="dlg-foot">
          <button class="btn btn-quiet btn-sm" @click="showAddDialog = false">取消</button>
          <button class="btn btn-primary btn-sm" @click="addCharacter">确认</button>
        </div>
      </div>
    </div>

    <!-- ================= 角色库 ================= -->
    <div v-if="showLibrary" class="dlg-overlay" @click.self="showLibrary = false">
      <div class="dlg">
        <div class="dlg-head"><h3>角色库</h3><button class="dlg-close" @click="showLibrary = false">✕</button></div>
        <div class="dlg-body">
          <p class="meta">已保存的角色，点击导入到当前项目</p>
          <div class="lib-list">
            <button v-for="char in store.savedCharacters" :key="char.id" class="lib-item" @click="loadFromLibrary(char.id)">
              <span class="lib-name">{{ char.name }}</span>
              <span class="meta">{{ formatDate(char.saved_at) }}</span>
            </button>
            <p v-if="!store.savedCharacters.length" class="tab-empty">角色库为空（工作台里「存入角色库」后出现）</p>
          </div>
        </div>
      </div>
    </div>

    <!-- ================= AI 角色设计工作室（批量/深链，全屏层） ================= -->
    <CharacterDesignStudio v-if="showDesignStudio" :project-name="projectName"
                           :initial-character-name="designInitialName"
                           @close="showDesignStudio = false" @applied="onDesignChanged" @generated="onDesignChanged" />

    <!-- ================= 图片放大预览 ================= -->
    <div v-if="previewImg" class="img-lightbox" @click="previewImg = ''">
      <img :src="previewImg" @click.stop />
      <button class="lightbox-close" @click="previewImg = ''">×</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useRoute } from 'vue-router';
import { useProjectStore } from '@/store';
import { useProjectEventsStore } from '@/store/projectEvents';
import { useTopbarStatus } from '@/store/topbarStatus';
import { apiClient } from '@/api';
import CharacterWorkbenchDialog from '@/components/resources/CharacterWorkbenchDialog.vue';
import ClothingDialog from '@/components/resources/ClothingDialog.vue';
import PropDialog from '@/components/resources/PropDialog.vue';
import CharacterDesignStudio from '@/components/CharacterDesignStudio.vue';

const route = useRoute();
const store = useProjectStore();
const pev = useProjectEventsStore();
const { characters, projectName } = storeToRefs(store);

const scope = ref<'project' | 'shared'>('project');
const tab = ref<'characters' | 'clothings' | 'props'>('characters');
const PROJECT_TABS = [
  { key: 'characters', label: '角色' },
  { key: 'clothings', label: '服装' },
  { key: 'props', label: '道具' },
] as const;

const previewImg = ref('');
const busy = ref(false);
const errorMsg = ref('');
function fail(e: any) {
  errorMsg.value = e?.detail ? `${e.error || '错误'}: ${e.detail || e.detail?.detail || ''}` : (e?.message || String(e));
}

const clothings = ref<any[]>([]);
const props = ref<any[]>([]);
const docRev = ref('');
const showCreateClothing = ref(false);
const showCreateProp = ref(false);
const newClothing = ref({ name: '', description: '' });
const newProp = ref({ name: '', description: '' });
const sharedList = ref<any[]>([]);

// ---- 角色工作台 / 详情弹窗 ----
const wbChar = ref('');
const clothingId = ref('');
const propId = ref('');
function openWorkbench(name: string) { wbChar.value = name; }
function openClothing(id: string) { clothingId.value = id; }
function openProp(id: string) { propId.value = id; }

// ---- 角色卡片（store 缓存）----
function avatarUrl(name: string): string {
  const views = store.getCharacterCache(name)?.views || {};
  const src = views.front?.seg || views.front?.raw || views.side?.seg || views.side?.raw || views.back?.seg || views.back?.raw;
  return src ? `data:image/png;base64,${src}` : '';
}
function doneCount(char: any): number {
  const views = store.getCharacterCache(char?.name)?.views || {};
  return (['front', 'side', 'back'] as const).filter(k => views[k]?.seg || views[k]?.raw).length;
}
function statusText(char: any): string {
  const gen = store.getGeneratingState(char.name);
  if (gen && Object.values(gen.busy || {}).some(Boolean)) return '生成中…';
  const done = doneCount(char);
  if (done === 0) return '未生成三视图';
  return `三视图 ${done}/3`;
}
function statusDot(char: any): string {
  const gen = store.getGeneratingState(char.name);
  if (gen && Object.values(gen.busy || {}).some(Boolean)) return '';
  const done = doneCount(char);
  if (done === 3) return 'live';
  if (done === 0) return '';
  return 'on';
}
function descSnippet(c: any): string { return (c.description || '').slice(0, 18) || ' '; }
const designs = ref<Record<string, any>>({});
function designNeedsAi(name: string): boolean {
  const d = designs.value[name];
  return !d || d.status !== 'applied';
}
async function loadDesigns() {
  if (!projectName.value) { designs.value = {}; return; }
  try { designs.value = (await apiClient.getCharacterDesigns(projectName.value)).designs || {}; }
  catch { designs.value = {}; }
}

// ---- 数据加载 ----
async function refreshBase() {
  const [chars, cls, prs, list] = await Promise.all([
    apiClient.getProjectCharactersFull(projectName.value).catch(() => ({ characters: [] })),
    apiClient.listClothings(projectName.value).catch(() => ({ clothings: [] })),
    apiClient.listProps(projectName.value).catch(() => ({ props: [] })),
    apiClient.resourcesList(projectName.value).catch(() => ({ resources: [], document_revision: '' })),
  ]);
  clothings.value = cls.clothings || [];
  props.value = prs.props || [];
  docRev.value = list.document_revision || '';
  if (!chars.characters?.length) return;
  // 与 store 角色表对齐（store 是工作台/卡片的数据源）
  const known = new Set(characters.value.map((c: any) => c.name));
  for (const c of chars.characters) {
    if (!known.has(c.name)) characters.value.push({ name: c.name, description: c.description || '', reference_images: {} });
  }
}

async function createClothingAction() {
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.createClothing(projectName.value, {
      name: newClothing.value.name, description: newClothing.value.description,
      expected_document_revision: docRev.value,
    });
    newClothing.value = { name: '', description: '' };
    showCreateClothing.value = false;
    await refreshBase();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function createPropAction() {
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.createProp(projectName.value, {
      name: newProp.value.name, description: newProp.value.description,
      expected_document_revision: docRev.value,
    });
    newProp.value = { name: '', description: '' };
    showCreateProp.value = false;
    await refreshBase();
  } catch (e) { fail(e); } finally { busy.value = false; }
}

async function switchShared() {
  scope.value = 'shared';
  busy.value = true;
  try { sharedList.value = (await apiClient.listSharedResources()).resources; }
  catch (e) { fail(e); } finally { busy.value = false; }
}
async function importAction(s: any) {
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.importSharedResource(projectName.value, {
      shared_resource_id: s.id, expected_document_revision: docRev.value,
    });
    alert(`已导入 ${s.name} 到项目 ${projectName.value}`);
    await refreshBase();
  } catch (e) { fail(e); } finally { busy.value = false; }
}

// ---- 新建角色 / 角色库（并入自原角色页）----
const showAddDialog = ref(false);
const showLibrary = ref(false);
const newCharName = ref('');
const newCharDesc = ref('');
const addNameInput = ref<HTMLInputElement | null>(null);
watch(showAddDialog, (v) => { if (v) nextTick(() => addNameInput.value?.focus()); });

async function addCharacter() {
  if (charactersLocked.value) { store.showNotification('Agent 正在修改角色，请稍候', 'info'); return; }
  if (!newCharName.value.trim()) { store.showNotification('请输入角色名称', 'error'); return; }
  if (!newCharDesc.value.trim()) { store.showNotification('请输入角色描述', 'error'); return; }
  const name = newCharName.value.trim();
  const description = newCharDesc.value.trim();
  try {
    await apiClient.bindCharacterToProject(projectName.value, { name, description, views: {} });
    characters.value.push({ name, description, reference_images: {} });
    newCharName.value = ''; newCharDesc.value = '';
    showAddDialog.value = false;
    openWorkbench(name);
  } catch (e: any) { store.showNotification(`创建失败: ${e.message}`, 'error'); }
}

async function openLibrary() { await store.loadSavedCharacters(); showLibrary.value = true; }
async function loadFromLibrary(characterId: string) {
  const result = await store.loadSavedCharacter(characterId);
  if (!result) return;
  try {
    await apiClient.bindCharacterToProject(projectName.value, {
      name: result.name, description: result.description, views: result.views,
    });
    const existing = characters.value.find((c: any) => c.name === result.name);
    if (!existing) characters.value.push({ name: result.name, description: result.description, reference_images: {} });
    store.cacheCharacterState(result.name, result.description, result.views);
    showLibrary.value = false;
    openWorkbench(result.name);
  } catch (e: any) { store.showNotification(`导入失败: ${e.message}`, 'error'); }
}
function formatDate(dateStr: string) {
  if (!dateStr) return '';
  return new Date(dateStr).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// ---- AI 设计工作室（批量 / 深链）----
const showDesignStudio = ref(false);
const designInitialName = ref<string | null>(null);
function openDesignStudio(char: any) {
  designInitialName.value = char?.name || null;
  showDesignStudio.value = true;
}
function onDesignChanged() {
  store.loadProjectCharacters(projectName.value);
  loadDesigns();
  refreshBase();
}

function onWorkbenchRefresh() {
  store.loadProjectCharacters(projectName.value);
  loadDesigns();
  refreshBase();
}

// ---- 角色域只读锁 + 顶栏状态 ----
const charactersLocked = computed(() => pev.charactersLocked);
// Phase B：生产资源域锁（resource.look.prepare 等期间 resources 只读，实时刷新）
const resourcesLocked = computed(() => pev.resourcesLocked);
// 页面同时承载角色与生产资源两类域 → 两类锁都识别（§15）
const agentLockActive = computed(() => charactersLocked.value || resourcesLocked.value);
const agentLockDomain = computed<'characters' | 'resources' | 'both'>(() =>
  (charactersLocked.value && resourcesLocked.value) ? 'both'
    : resourcesLocked.value ? 'resources' : 'characters');
watch(charactersLocked, () => { if (projectName.value) store.loadProjectCharacters(projectName.value); });
watch(resourcesLocked, () => { if (projectName.value) refreshBase(); });
// Agent 改生产资源落盘 → 静默刷新（resources.changed）
watch(() => pev.resourcesVersion, () => { if (projectName.value) refreshBase(); });
watch(() => pev.charactersVersion, () => {
  if (projectName.value && pev.charactersLocked) { store.loadProjectCharacters(projectName.value); loadDesigns(); }
});
useTopbarStatus('resources', () => (agentLockActive.value
  ? { text: agentLockDomain.value === 'both' ? 'AI 正在修改角色与生产资源'
        : agentLockDomain.value === 'resources' ? 'AI 正在修改生产资源' : 'AI 正在修改角色',
      tone: 'fail' as const }
  : { text: '已同步', tone: 'idle' as const }));

// ---- 深链：/characters?xxx → /resources?tab=characters&...；?design= 直接开工作台+工作室 ----
onMounted(async () => {
  await refreshBase();
  store.loadProjectCharacters(projectName.value);
  store.loadSavedCharacters();
  loadDesigns();
  const q: any = route.query || {};
  if (q.tab === 'clothings' || q.tab === 'props') tab.value = q.tab;
  else tab.value = 'characters';
  if (q.char && typeof q.char === 'string') {
    const stop = watch(characters, (list) => {
      if (list.some((c: any) => c.name === q.char)) { openWorkbench(q.char); stop(); }
    }, { immediate: true });
  }
  if (q.design && typeof q.design === 'string') {
    designInitialName.value = q.design;
    const stop = watch(characters, (list) => {
      if (list.some((c: any) => c.name === q.design)) {
        openWorkbench(q.design);
        openDesignStudio({ name: q.design });
        stop();
      }
    }, { immediate: true });
  }
});

// 切项目：重载
watch(projectName, () => {
  wbChar.value = ''; clothingId.value = ''; propId.value = '';
  refreshBase();
  store.loadProjectCharacters(projectName.value);
  loadDesigns();
});
</script>

<style lang="scss" scoped>
.resources-page { height: calc(100vh - 52px); display: flex; flex-direction: column; padding: 16px 24px 20px; gap: 14px; }
.res-header { flex-shrink: 0; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.scope-toggle, .type-tabs { display: flex; gap: 4px; background: var(--bg-hover); border-radius: 8px; padding: 3px; }
.scope-toggle button, .type-tabs button { border: 0; background: transparent; color: var(--text-secondary); padding: 5px 14px; border-radius: 6px; cursor: pointer; opacity: 0.7;
  &:hover { opacity: 1; } }
.scope-toggle button.active, .type-tabs button.active { background: var(--bg-active, var(--bg-hover)); opacity: 1; font-weight: 600; color: var(--text-primary); }
.shared-hint { font-size: 12px; color: var(--text-faint); }
.spacer { flex: 1; }
.proj-name { font-size: 13px; color: var(--text-muted); }
.res-error { background: rgba(248, 113, 113, 0.12); border: 1px solid rgba(248, 113, 113, 0.4); border-radius: 8px;
  padding: 8px 12px; display: flex; justify-content: space-between; gap: 12px; font-size: 13px; color: #f87171;
  .dismiss { border: 0; background: none; color: inherit; cursor: pointer; font-size: 15px; } }
.agent-lock-banner { flex-shrink: 0; display: flex; align-items: center; gap: 8px;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px;
  padding: 8px 12px; font-size: 12px; color: var(--text-muted); }

.tab-surface { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 12px; }
.surface-head { flex-shrink: 0; display: flex; align-items: center; gap: 12px;
  h3 { margin: 0; font-size: 15px; color: var(--text-primary); }
  .count { color: var(--text-faint); font-weight: 500; } }
.meta { font-size: 11px; color: var(--text-faint); }
.mono-num { font-variant-numeric: tabular-nums; font-family: monospace; }

.card-grid { flex: 1; min-height: 0; overflow-y: auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(216px, 1fr)); gap: 12px; align-content: start; padding-bottom: 8px; }

.char-card {
  display: flex; align-items: center; gap: 12px; text-align: left;
  border: 1px solid var(--border); border-radius: 10px; background: var(--bg-surface);
  padding: 12px; cursor: pointer; transition: border-color 0.15s ease;
  &:hover { border-color: var(--text-muted); }
}
.char-avatar {
  width: 56px; height: 56px; flex-shrink: 0; border-radius: 10px; overflow: hidden;
  background: var(--bg-root); border: 1px solid var(--border);
  img { width: 100%; height: 100%; object-fit: cover; display: block; }
}
.char-avatar-ph { display: flex; width: 100%; height: 100%; align-items: center; justify-content: center; font-size: 20px; color: var(--text-faint); }
.char-info { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.char-name { font-size: 14px; font-weight: 600; color: var(--text-primary); display: flex; align-items: center; gap: 6px; }
.ai-mark { font-size: 10px; border: 1px solid var(--border); border-radius: 4px; padding: 0 4px; color: var(--text-faint); }
.char-status { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-muted); }
.char-meta { font-size: 11px; color: var(--text-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dot { width: 6px; height: 6px; border-radius: 50%; background: var(--text-faint); flex-shrink: 0;
  &.live { background: var(--text-primary); } &.on { background: var(--text-secondary); } }

.asset-card {
  display: flex; flex-direction: column; gap: 6px; text-align: left; align-items: flex-start;
  border: 1px solid var(--border); border-radius: 10px; background: var(--bg-surface);
  padding: 14px; cursor: pointer; transition: border-color 0.15s ease;
  &:hover { border-color: var(--text-muted); }
}
.asset-name { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.asset-sub { font-size: 12px; color: var(--text-secondary); line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.asset-meta { font-size: 11px; color: var(--text-faint); }

.shared-card {
  display: flex; flex-direction: column; gap: 6px; align-items: flex-start;
  border: 1px solid var(--border); border-radius: 10px; background: var(--bg-surface); padding: 14px;
  strong { font-size: 14px; color: var(--text-primary); } }
.badge.kind { font-size: 10px; border: 1px solid var(--border); border-radius: 4px; padding: 0 5px; color: var(--text-muted); }

.create-form { flex-shrink: 0; display: flex; gap: 8px; flex-wrap: wrap;
  input { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px;
    color: var(--text-primary); font-size: 12px; padding: 6px 10px; font-family: inherit;
    &:focus { outline: none; border-color: var(--border-hover); } } }
.tab-empty { font-size: 13px; color: var(--text-faint); padding: 20px 4px; }

.guide { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; text-align: center; }
.guide-title { font-size: 16px; font-weight: 600; color: var(--text-primary); }
.guide-desc { font-size: 13px; color: var(--text-muted); line-height: 1.8; }
.guide-actions { display: flex; gap: 10px; }

.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 6px;
  background: none; color: var(--text-secondary); font-size: 12px; cursor: pointer;
  &:disabled { opacity: 0.4; cursor: not-allowed; }
  &.btn-sm { height: 26px; padding: 0 10px; font-size: 12px; }
  &.btn-primary { border: none; background: var(--accent); color: var(--accent-text, #fff); &:hover:not(:disabled) { background: var(--accent-hover); } }
  &.btn-outline { &:hover:not(:disabled) { border-color: var(--border-hover); color: var(--text-primary); } }
  &.btn-quiet { border-color: transparent; color: var(--text-muted); &:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); } }
}

/* 通用小弹窗（新建角色 / 角色库） */
.dlg-overlay { position: fixed; inset: 0; z-index: 1200; background: rgba(0, 0, 0, 0.6);
  display: flex; align-items: center; justify-content: center; backdrop-filter: blur(3px); }
.dlg {
  width: min(460px, 92vw); max-height: 80vh; background: var(--bg-surface);
  border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 24px 70px rgba(0, 0, 0, 0.5);
  display: flex; flex-direction: column; overflow: hidden;
}
.dlg-head { flex-shrink: 0; display: flex; align-items: center; justify-content: space-between;
  padding: 12px 18px; border-bottom: 1px solid var(--border);
  h3 { margin: 0; font-size: 14px; color: var(--text-primary); } }
.dlg-close { border: none; background: none; color: var(--text-muted); cursor: pointer; font-size: 14px;
  width: 26px; height: 26px; border-radius: 6px;
  &:hover { background: var(--bg-hover); color: var(--text-primary); } }
.dlg-body { flex: 1; min-height: 0; overflow-y: auto; padding: 14px 18px; display: flex; flex-direction: column; gap: 12px; }
.dlg-field { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--text-muted);
  input, textarea { background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px;
    color: var(--text-primary); font-size: 13px; padding: 7px 10px; font-family: inherit; resize: vertical;
    &:focus { outline: none; border-color: var(--border-hover); } } }
.dlg-foot { flex-shrink: 0; display: flex; justify-content: flex-end; gap: 8px; padding: 12px 18px; border-top: 1px solid var(--border); }
.lib-list { display: flex; flex-direction: column; gap: 6px; }
.lib-item { display: flex; align-items: center; justify-content: space-between; text-align: left;
  border: 1px solid var(--border); border-radius: 8px; background: none;
  padding: 9px 12px; cursor: pointer; color: var(--text-secondary); font-size: 13px;
  &:hover { background: var(--bg-hover); color: var(--text-primary); } }
.lib-name { font-weight: 600; color: var(--text-primary); }

.img-lightbox { position: fixed; inset: 0; z-index: 1400; background: rgba(0, 0, 0, 0.85);
  display: flex; align-items: center; justify-content: center; cursor: zoom-out;
  img { max-width: 90vw; max-height: 90vh; object-fit: contain; }
  .lightbox-close { position: absolute; right: 18px; top: 14px; border: none; background: none; color: #d4d4d4; font-size: 22px; cursor: pointer; } }
</style>
