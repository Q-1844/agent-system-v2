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
      <span class="proj-name">{{ projectName }}</span>
    </header>

    <div v-if="errorMsg" class="res-error">
      <span>{{ errorMsg }}</span>
      <button class="dismiss" @click="errorMsg = ''">×</button>
    </div>

    <!-- ================= 项目 / 角色（+Look 工作区） ================= -->
    <section v-if="scope === 'project' && tab === 'characters'" class="split">
      <aside class="side-list">
        <div class="side-head">
          <span>角色（Click 查看 Look）</span>
          <button class="mini" @click="publishSelectedCharacter" :disabled="!selectedCharacter">发布此角色</button>
        </div>
        <button v-for="c in characters" :key="c.name" class="side-item" :class="{ active: selectedCharacter === c.name }" @click="selectCharacter(c.name)">
          {{ c.name }}<span class="sub">{{ (c.views && Object.keys(c.views).length) || 0 }} 视图</span>
        </button>
        <p v-if="!characters.length" class="empty">项目还没有角色（去「角色管理」页创建）</p>
      </aside>

      <div class="main-area">
        <div class="panel-head">
          <h3>{{ selectedCharacter ? `${selectedCharacter} · Looks` : 'Looks' }}</h3>
          <button class="mini primary" :disabled="!selectedCharacter" @click="showCreateLook = !showCreateLook">＋ 新建 Look</button>
        </div>
        <form v-if="showCreateLook && selectedCharacter" class="inline-form" @submit.prevent="createLookAction">
          <input v-model="newLook.label" placeholder="显示名（如 Alice · Black Coat）" required />
          <select v-model="newLook.clothing_id">
            <option :value="''">无服装</option>
            <option v-for="cl in clothings" :key="cl.id" :value="cl.id">{{ cl.name }}</option>
          </select>
          <input v-model="newLook.appearance_delta" placeholder="外观差异（可选）" />
          <button class="mini primary" type="submit">创建</button>
        </form>

        <div class="look-cards">
          <button v-for="lk in looks" :key="lk.id" class="look-card" :class="{ active: selectedLook?.look_id === lk.id }" @click="openLook(lk.id)">
            <img v-if="lookCoverUrl(lk)" :src="lookCoverUrl(lk)" class="cover" />
            <div v-else class="cover placeholder">无图</div>
            <span class="look-name">{{ lk.name || '(未命名)' }}</span>
            <span class="badges">
              <em :class="'gs-' + lk.generation_status">{{ lk.generation_status }}</em>
              <em :class="'as-' + lk.status">{{ lk.status }}</em>
            </span>
          </button>
          <p v-if="selectedCharacter && !looks.length" class="empty">该角色还没有 Look</p>
        </div>

        <!-- Look 详情 -->
        <div v-if="selectedLook" class="look-detail">
          <div class="detail-head">
            <h4>{{ selectedLook.label || selectedLook.look_id }}</h4>
            <span class="meta">服装: {{ selectedLook.clothing?.name || '-' }}</span>
            <span class="meta">候选 {{ selectedLook.reference_asset_ids?.length || 0 }}</span>
            <div class="actions">
              <select v-model="genCount"><option v-for="n in 4" :key="n" :value="n">{{ n }} 张</option></select>
              <button class="mini primary" :disabled="busy" @click="generateAction">生成候选</button>
              <button class="mini" :disabled="busy || !selectedLook.reference_asset_ids?.length" @click="reviewAction">视觉审查</button>
              <button class="mini" :disabled="busy" @click="publishLookAction">发布到共享库</button>
            </div>
          </div>
          <div class="cand-grid">
            <div v-for="a in selectedLook.reference_assets" :key="a.asset_id" class="cand">
              <img v-if="assetUrl(a)" :src="assetUrl(a)" />
              <div v-else-if="a.missing" class="img placeholder missing">缺失</div>
              <div v-else class="img placeholder">无图</div>
              <div class="cand-info">
                <span v-if="selectedLook.cover_asset_id === a.asset_id" class="star" title="封面">★</span>
                <span v-if="selectedLook.approved_reference_asset_ids?.includes(a.asset_id)" class="badge approved">已批准</span>
                <span v-else-if="reviewBadge(a) === 'pass'" class="badge pass">审查通过</span>
                <span v-else-if="reviewBadge(a)" class="badge" :class="reviewBadge(a)">{{ reviewBadge(a) }}</span>
              </div>
              <div class="cand-actions">
                <button v-if="!selectedLook.approved_reference_asset_ids?.includes(a.asset_id)"
                        class="mini primary" :disabled="busy" :title="reviewBadge(a) === 'pass' ? '' : '需要审查通过后才能批准'"
                        @click="approveAction(a)">批准</button>
                <button v-else class="mini" :disabled="busy" @click="revokeAction(a)">撤销批准</button>
              </div>
              <p v-if="reviewNote(a)" class="review-note">{{ reviewNote(a) }}</p>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ================= 项目 / 服装 ================= -->
    <section v-if="scope === 'project' && tab === 'clothings'" class="split">
      <aside class="side-list">
        <div class="side-head"><span>服装</span><button class="mini primary" @click="showCreateClothing = !showCreateClothing">＋ 新建</button></div>
        <form v-if="showCreateClothing" class="inline-form col" @submit.prevent="createClothingAction">
          <input v-model="newClothing.name" placeholder="名称（如 Black Coat）" required />
          <input v-model="newClothing.description" placeholder="描述" />
          <button class="mini primary" type="submit">创建</button>
        </form>
        <button v-for="cl in clothings" :key="cl.id" class="side-item" :class="{ active: selectedClothing?.clothing_id === cl.id }" @click="openClothing(cl.id)">
          {{ cl.name }}<span class="sub">{{ cl.relationship_summary }}</span>
        </button>
        <p v-if="!clothings.length" class="empty">还没有服装</p>
      </aside>
      <div v-if="selectedClothing" class="main-area">
        <div class="detail-head">
          <h4>{{ selectedClothing.name }}</h4>
          <span class="meta">被 {{ selectedClothing.used_by_looks?.length || 0 }} 个 Look 引用</span>
          <div class="actions">
            <button class="mini" @click="renameClothingAction">重命名</button>
            <button class="mini" :disabled="busy" @click="publishClothingAction">发布到共享库</button>
            <button class="mini danger" :disabled="busy" @click="deleteClothingAction">删除</button>
          </div>
        </div>
        <p class="desc">{{ selectedClothing.description }} {{ selectedClothing.design_traits }}</p>
        <div class="ref-row">
          <div v-for="a in selectedClothing.reference_assets" :key="a.asset_id" class="cand small">
            <img v-if="assetUrl(a)" :src="assetUrl(a)" />
            <div v-else class="img placeholder">无图</div>
            <button class="mini danger" :disabled="busy" @click="detachClothingRef(a)">解绑</button>
          </div>
        </div>
        <form class="inline-form" @submit.prevent="attachClothingRef">
          <input v-model="newRef.local" placeholder="项目内相对路径（如 o/coat.png）或留空用 URL" />
          <input v-model="newRef.url" placeholder="或公网 URL" />
          <button class="mini primary" type="submit" :disabled="busy">注册并绑定参考图</button>
        </form>
      </div>
      <p v-else class="empty main-area">选择左侧服装查看详情</p>
    </section>

    <!-- ================= 项目 / 其他（道具） ================= -->
    <section v-if="scope === 'project' && tab === 'props'" class="split">
      <aside class="side-list">
        <div class="side-head"><span>道具（Prop）</span><button class="mini primary" @click="showCreateProp = !showCreateProp">＋ 新建</button></div>
        <form v-if="showCreateProp" class="inline-form col" @submit.prevent="createPropAction">
          <input v-model="newProp.name" placeholder="名称（如 Lantern）" required />
          <input v-model="newProp.description" placeholder="描述" />
          <button class="mini primary" type="submit">创建</button>
        </form>
        <button v-for="p in props" :key="p.id" class="side-item" :class="{ active: selectedProp?.prop_id === p.id }" @click="openProp(p.id)">
          {{ p.name }}<span class="sub">{{ p.relationship_summary }}</span>
        </button>
        <p v-if="!props.length" class="empty">还没有道具</p>
      </aside>
      <div v-if="selectedProp" class="main-area">
        <div class="detail-head">
          <h4>{{ selectedProp.name }}</h4>
          <div class="actions">
            <button class="mini" @click="renamePropAction">重命名</button>
            <button class="mini danger" :disabled="busy" @click="deletePropAction">删除</button>
          </div>
        </div>
        <p class="desc">{{ selectedProp.description }}</p>
        <div class="ref-row">
          <div v-for="a in selectedProp.reference_assets" :key="a.asset_id" class="cand small">
            <img v-if="assetUrl(a)" :src="assetUrl(a)" />
            <div v-else class="img placeholder">无图</div>
            <button class="mini danger" :disabled="busy" @click="detachPropRef(a)">解绑</button>
          </div>
        </div>
        <form class="inline-form" @submit.prevent="attachPropRef">
          <input v-model="newRef.local" placeholder="项目内相对路径或留空用 URL" />
          <input v-model="newRef.url" placeholder="或公网 URL" />
          <button class="mini primary" type="submit" :disabled="busy">注册并绑定参考图</button>
        </form>
      </div>
      <p v-else class="empty main-area">选择左侧道具查看详情</p>
    </section>

    <!-- ================= 共享库 ================= -->
    <section v-if="scope === 'shared'" class="shared-grid">
      <div class="panel-head wide">
        <h3>共享库（{{ sharedList.length }}）</h3>
        <span class="meta">导入会复制副本进当前项目「{{ projectName }}」，origin 记录来源与版本</span>
      </div>
      <div class="card-grid">
        <div v-for="s in sharedList" :key="s.id" class="shared-card">
          <span class="badge kind">{{ s.type }}</span>
          <strong>{{ s.name }}</strong>
          <span class="meta">v{{ s.version }} · 来自 {{ s.origin_project || '?' }}</span>
          <span v-if="s.character_snapshot" class="meta">角色: {{ s.character_snapshot.name }}</span>
          <button class="mini primary" :disabled="busy" @click="importAction(s)">导入到项目</button>
        </div>
        <p v-if="!sharedList.length" class="empty">共享库为空（在项目资源里点「发布到共享库」）</p>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import { useProjectStore } from '@/store';
import { apiClient } from '@/api';

const store = useProjectStore();
const { projectName } = storeToRefs(store);

const scope = ref<'project' | 'shared'>('project');
const tab = ref<'characters' | 'clothings' | 'props'>('characters');
const PROJECT_TABS = [
  { key: 'characters', label: '角色 / Look' },
  { key: 'clothings', label: '服装' },
  { key: 'props', label: '其他（道具）' },
] as const;

const busy = ref(false);
const errorMsg = ref('');
function fail(e: any) {
  errorMsg.value = e?.detail ? `${e.error || '错误'}: ${e.detail || e.detail?.detail || ''}` : (e?.message || String(e));
}

const characters = ref<any[]>([]);
const looks = ref<any[]>([]);
const clothings = ref<any[]>([]);
const props = ref<any[]>([]);
const selectedCharacter = ref('');
const selectedLook = ref<any>(null);
const selectedClothing = ref<any>(null);
const selectedProp = ref<any>(null);
const docRev = ref('');
const genCount = ref(1);
const showCreateLook = ref(false);
const showCreateClothing = ref(false);
const showCreateProp = ref(false);
const newLook = ref({ label: '', clothing_id: '', appearance_delta: '' });
const newClothing = ref({ name: '', description: '' });
const newProp = ref({ name: '', description: '' });
const newRef = ref({ local: '', url: '' });
const sharedList = ref<any[]>([]);

function assetUrl(a: any): string {
  if (!a || a.missing) return '';
  const local: string = a.local || '';
  if (local && !/^https?:/.test(local)) {
    if (local.startsWith('shared_library/')) return `/${local}`;           // /shared_library 静态挂载
    return `/projects/${encodeURIComponent(projectName.value)}/${local.replace(/^\/+/, '')}`;
  }
  return a.url || '';
}
function lookCoverUrl(lk: any): string {
  if (!lk.cover_asset_id) return '';
  const hit = (selectedLook.value?.reference_assets || []).find((x: any) => x.asset_id === lk.cover_asset_id);
  return hit ? assetUrl(hit) : '';
}
function reviewBadge(a: any): string {
  const r = selectedLook.value?.latest_reviews?.[a.asset_id];
  return r?.overall || '';
}
function reviewNote(a: any): string {
  const r = selectedLook.value?.latest_reviews?.[a.asset_id];
  return r?.summary || '';
}

async function refreshBase() {
  const [chars, cls, prs, list] = await Promise.all([
    apiClient.getProjectCharactersFull(projectName.value).catch(() => ({ characters: [] })),
    apiClient.listClothings(projectName.value).catch(() => ({ clothings: [] })),
    apiClient.listProps(projectName.value).catch(() => ({ props: [] })),
    apiClient.resourcesList(projectName.value).catch(() => ({ resources: [], document_revision: '' })),
  ]);
  characters.value = chars.characters || [];
  clothings.value = cls.clothings || [];
  props.value = prs.props || [];
  docRev.value = list.document_revision || '';
}
async function refreshLooks() {
  if (!selectedCharacter.value) { looks.value = []; return; }
  const r = await apiClient.listLooks(projectName.value, selectedCharacter.value);
  looks.value = r.looks || [];
}

async function selectCharacter(name: string) {
  selectedCharacter.value = name;
  selectedLook.value = null;
  await refreshLooks();
}
async function openLook(id: string) {
  selectedLook.value = await apiClient.readLook(projectName.value, id);
}
async function openClothing(id: string) {
  selectedClothing.value = await apiClient.readClothing(projectName.value, id);
}
async function openProp(id: string) {
  selectedProp.value = await apiClient.readProp(projectName.value, id);
}

async function createLookAction() {
  busy.value = true; errorMsg.value = '';
  try {
    const r = await apiClient.createLook(projectName.value, {
      character_name: selectedCharacter.value,
      label: newLook.value.label,
      clothing_id: newLook.value.clothing_id || null,
      appearance_delta: newLook.value.appearance_delta,
      expected_document_revision: docRev.value,
    });
    newLook.value = { label: '', clothing_id: '', appearance_delta: '' };
    showCreateLook.value = false;
    await Promise.all([refreshBase(), refreshLooks()]);
    await openLook(r.target);
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function generateAction() {
  if (!selectedLook.value) return;
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.generateLookCandidates(projectName.value, selectedLook.value.look_id, {
      expected_revision: selectedLook.value.revision,
      candidate_count: genCount.value,
    });
    await openLook(selectedLook.value.look_id);
    await refreshLooks();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function reviewAction() {
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.reviewLookCandidates(projectName.value, selectedLook.value.look_id, {});
    await openLook(selectedLook.value.look_id);
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function approveAction(a: any) {
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.approveLookReference(projectName.value, selectedLook.value.look_id, {
      asset_id: a.asset_id, expected_revision: selectedLook.value.revision,
    });
    await openLook(selectedLook.value.look_id);
    await refreshLooks();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function revokeAction(a: any) {
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.revokeLookApproval(projectName.value, selectedLook.value.look_id, {
      asset_id: a.asset_id, expected_revision: selectedLook.value.revision,
    });
    await openLook(selectedLook.value.look_id);
    await refreshLooks();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function publishLookAction() {
  busy.value = true; errorMsg.value = '';
  try {
    await apiClient.publishResource(projectName.value, selectedLook.value.look_id);
  } catch (e: any) {
    if (e?.error === 'clothing_not_published' && selectedLook.value.clothing_id) {
      if (confirm('Look 依赖的服装尚未发布，先发布服装再发布 Look？')) {
        try {
          await apiClient.publishResource(projectName.value, selectedLook.value.clothing_id);
          await apiClient.publishResource(projectName.value, selectedLook.value.look_id);
          errorMsg.value = '';
        } catch (e2) { fail(e2); }
      }
    } else fail(e);
  } finally { busy.value = false; }
}
async function publishSelectedCharacter() {
  if (!selectedCharacter.value) return;
  busy.value = true; errorMsg.value = '';
  try { await apiClient.publishCharacter(projectName.value, selectedCharacter.value); }
  catch (e) { fail(e); } finally { busy.value = false; }
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
async function renameClothingAction() {
  const name = prompt('新名称', selectedClothing.value.name);
  if (!name) return;
  busy.value = true;
  try {
    await apiClient.updateClothing(projectName.value, selectedClothing.value.clothing_id,
      { name, expected_revision: selectedClothing.value.revision });
    await refreshBase();
    await openClothing(selectedClothing.value.clothing_id);
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function deleteClothingAction() {
  if (!confirm('删除该服装？（被 Look 引用时会被拒绝）')) return;
  busy.value = true;
  try {
    await apiClient.deleteClothing(projectName.value, selectedClothing.value.clothing_id, selectedClothing.value.revision);
    selectedClothing.value = null;
    await refreshBase();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function attachClothingRef() {
  busy.value = true; errorMsg.value = '';
  try {
    const reg = await apiClient.registerReferenceAsset(projectName.value, {
      local: newRef.value.local || null, url: newRef.value.url || null,
      expected_document_revision: docRev.value,
    });
    await apiClient.attachClothingReference(projectName.value, selectedClothing.value.clothing_id, {
      asset_id: reg.target, expected_revision: selectedClothing.value.revision,
    });
    newRef.value = { local: '', url: '' };
    await refreshBase();
    await openClothing(selectedClothing.value.clothing_id);
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function detachClothingRef(a: any) {
  busy.value = true;
  try {
    await apiClient.detachClothingReference(projectName.value, selectedClothing.value.clothing_id,
      a.asset_id, selectedClothing.value.revision);
    await refreshBase();
    await openClothing(selectedClothing.value.clothing_id);
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function publishClothingAction() {
  busy.value = true; errorMsg.value = '';
  try { await apiClient.publishResource(projectName.value, selectedClothing.value.clothing_id); }
  catch (e) { fail(e); } finally { busy.value = false; }
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
async function renamePropAction() {
  const name = prompt('新名称', selectedProp.value.name);
  if (!name) return;
  busy.value = true;
  try {
    await apiClient.updateProp(projectName.value, selectedProp.value.prop_id,
      { name, expected_revision: selectedProp.value.revision });
    await refreshBase();
    await openProp(selectedProp.value.prop_id);
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function deletePropAction() {
  if (!confirm('删除该道具？')) return;
  busy.value = true;
  try {
    await apiClient.deleteProp(projectName.value, selectedProp.value.prop_id, selectedProp.value.revision);
    selectedProp.value = null;
    await refreshBase();
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function attachPropRef() {
  busy.value = true; errorMsg.value = '';
  try {
    const reg = await apiClient.registerReferenceAsset(projectName.value, {
      local: newRef.value.local || null, url: newRef.value.url || null,
      expected_document_revision: docRev.value,
    });
    await apiClient.attachPropReference(projectName.value, selectedProp.value.prop_id, {
      asset_id: reg.target, expected_revision: selectedProp.value.revision,
    });
    newRef.value = { local: '', url: '' };
    await refreshBase();
    await openProp(selectedProp.value.prop_id);
  } catch (e) { fail(e); } finally { busy.value = false; }
}
async function detachPropRef(a: any) {
  busy.value = true;
  try {
    await apiClient.detachPropReference(projectName.value, selectedProp.value.prop_id,
      a.asset_id, selectedProp.value.revision);
    await refreshBase();
    await openProp(selectedProp.value.prop_id);
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

onMounted(refreshBase);
</script>

<style scoped>
.resources-page { padding: 20px 24px; color: var(--text-1, #e8e8ea); display: flex; flex-direction: column; gap: 14px; }
.res-header { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.scope-toggle, .type-tabs { display: flex; gap: 4px; background: rgba(128,128,128,.12); border-radius: 8px; padding: 3px; }
.scope-toggle button, .type-tabs button { border: 0; background: transparent; color: inherit; padding: 5px 14px; border-radius: 6px; cursor: pointer; opacity: .7; }
.scope-toggle button.active, .type-tabs button.active { background: var(--bg-3, rgba(255,255,255,.14)); opacity: 1; font-weight: 600; }
.shared-hint { opacity: .6; font-size: 12px; }
.proj-name { margin-left: auto; opacity: .55; font-size: 13px; }
.res-error { background: rgba(220,80,80,.14); border: 1px solid rgba(220,80,80,.4); border-radius: 8px; padding: 8px 12px; display: flex; justify-content: space-between; gap: 12px; font-size: 13px; }
.res-error .dismiss { border: 0; background: none; color: inherit; cursor: pointer; font-size: 15px; }
.split { display: flex; gap: 18px; align-items: flex-start; min-height: 300px; }
.side-list { width: 260px; flex-shrink: 0; display: flex; flex-direction: column; gap: 6px; }
.side-head { display: flex; justify-content: space-between; align-items: center; font-size: 13px; opacity: .8; margin-bottom: 4px; }
.side-item { text-align: left; border: 1px solid rgba(128,128,128,.25); background: transparent; color: inherit; border-radius: 8px; padding: 8px 10px; cursor: pointer; display: flex; flex-direction: column; gap: 2px; }
.side-item.active, .look-card.active { border-color: #6ea8ff; box-shadow: 0 0 0 1px #6ea8ff inset; }
.side-item .sub { font-size: 11px; opacity: .55; }
.main-area { flex: 1; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.panel-head { display: flex; align-items: center; gap: 12px; }
.panel-head.wide { justify-content: space-between; }
.panel-head h3, .detail-head h4 { margin: 0; font-size: 15px; }
.inline-form { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; background: rgba(128,128,128,.08); padding: 10px; border-radius: 8px; }
.inline-form.col { flex-direction: column; align-items: stretch; }
.inline-form input, .inline-form select, .detail-head select { background: rgba(128,128,128,.12); border: 1px solid rgba(128,128,128,.3); color: inherit; border-radius: 6px; padding: 6px 9px; font-size: 13px; min-width: 0; }
.inline-form input { flex: 1; }
.mini { border: 1px solid rgba(128,128,128,.35); background: transparent; color: inherit; border-radius: 6px; padding: 4px 10px; font-size: 12px; cursor: pointer; }
.mini.primary { border-color: #5a8dee; color: #9ec2ff; }
.mini.danger { border-color: rgba(220,90,90,.6); color: #ff9d9d; }
.mini:disabled { opacity: .4; cursor: not-allowed; }
.look-cards { display: flex; gap: 12px; flex-wrap: wrap; }
.look-card { width: 150px; border: 1px solid rgba(128,128,128,.25); background: transparent; color: inherit; border-radius: 10px; padding: 8px; cursor: pointer; display: flex; flex-direction: column; gap: 6px; }
.look-card .cover { width: 100%; height: 110px; object-fit: cover; border-radius: 6px; background: rgba(128,128,128,.15); }
.cover.placeholder, .img.placeholder { display: flex; align-items: center; justify-content: center; opacity: .4; font-size: 12px; }
.img.placeholder.missing { color: #ff9d9d; }
.look-name { font-size: 13px; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.badges { display: flex; gap: 4px; }
.badges em, .badge { font-style: normal; font-size: 10px; padding: 1px 6px; border-radius: 8px; background: rgba(128,128,128,.2); }
.badge.approved { background: rgba(90,180,90,.25); color: #9fe0a8; }
.badge.pass { background: rgba(90,160,255,.2); color: #a9c9ff; }
.badge.needs_fix { background: rgba(230,120,90,.25); color: #ffb59d; }
.badge.uncertain { background: rgba(220,190,90,.2); color: #ffe6a1; }
.badge.kind { background: rgba(140,120,255,.2); color: #c9bcff; }
.gs-generating { color: #ffe6a1; } .gs-failed { color: #ff9d9d; } .gs-ready { color: #9fe0a8; }
.look-detail { border-top: 1px solid rgba(128,128,128,.2); padding-top: 12px; }
.detail-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.detail-head .meta { font-size: 12px; opacity: .6; }
.detail-head .actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.cand-grid, .card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px; }
.cand { border: 1px solid rgba(128,128,128,.25); border-radius: 10px; padding: 8px; display: flex; flex-direction: column; gap: 6px; }
.cand img, .cand .img { width: 100%; height: 130px; object-fit: cover; border-radius: 6px; background: rgba(128,128,128,.15); }
.cand.small img, .cand.small .img { height: 80px; }
.cand-info { display: flex; gap: 6px; align-items: center; min-height: 18px; }
.star { color: #ffd75e; }
.cand-actions { display: flex; gap: 6px; }
.review-note { margin: 0; font-size: 11px; opacity: .6; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.desc { margin: 0; font-size: 13px; opacity: .7; }
.ref-row { display: flex; gap: 10px; flex-wrap: wrap; }
.empty { opacity: .5; font-size: 13px; padding: 12px 4px; }
.shared-card { border: 1px solid rgba(128,128,128,.25); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 6px; align-items: flex-start; }
</style>
