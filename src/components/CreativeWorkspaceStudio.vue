<!-- AI-6A Creative Workspace Studio（§二十三~§二十七）。
独立组件，不改 ShotBoard/Workbench。只编辑创作字段（description/characters/target_duration_s/
新增/删除/重排），不显示首帧/尾帧/视频/Autopilot/Production Plan。
核心不变量（§二）：修改 Workspace != 修改 canonical project。 -->
<template>
  <div class="cw-studio">
    <div class="cw-banner">这是实验版本：改动不会影响正式项目</div>

    <!-- Workspace Header -->
    <div class="cw-header">
      <div class="cw-selector">
        <select v-model="activeWid" @change="onSelect" class="cw-select">
          <option :value="null">— 选择创作版本 —</option>
          <option v-for="w in workspaces" :key="w.workspace_id" :value="w.workspace_id">
            {{ w.name || w.workspace_id }} · v{{ w.revision }}{{ w.status === 'archived' ? ' · 已归档' : '' }}
          </option>
        </select>
        <input v-model="newWsName" placeholder="新版本名（如：动作更紧凑版）" class="cw-input" @keyup.enter="doCreate" />
        <button class="cw-btn" @click="doCreate" :disabled="busy || !newWsName.trim()">新建创作版本</button>
      </div>
      <div v-if="meta" class="cw-meta">
        <span class="chip">版本 {{ meta.revision }}</span>
        <span class="chip" :class="{ stale: meta.base_is_stale }">
          {{ meta.base_is_stale ? '基于较早的正式项目快照' : '与正式项目同步' }}
        </span>
        <button class="cw-btn sm" @click="doArchive" :disabled="busy || meta.status === 'archived'">归档</button>
        <button class="cw-btn sm danger" @click="doDelete" :disabled="busy">删除</button>
      </div>
    </div>

    <div v-if="err" class="cw-err">{{ err }}</div>

    <div v-if="!meta" class="cw-empty">
      选择一个创作版本，或新建一个开始实验。新建会基于当前正式项目快照。
    </div>

    <div v-else class="cw-body">
      <!-- 左：场景/镜头编辑器 -->
      <div class="cw-left">
        <div v-for="sc in draft?.scenes || []" :key="sc.scene_id" class="cw-scene">
          <div class="cw-scene-head">
            <span class="cw-scene-id">{{ sc.scene_id }}</span>
            <span class="cw-scene-summary">{{ sc.summary }}</span>
          </div>
          <div v-for="(sh, i) in sc.shots" :key="sh.shot_id" class="cw-shot">
            <div class="cw-shot-head">
              <span class="cw-shot-id">{{ sh.shot_id }}</span>
              <span class="cw-shot-actions">
                <button class="cw-mini" :disabled="i === 0" @click="moveShot(sc.scene_id, i, -1)">↑</button>
                <button class="cw-mini" :disabled="i === sc.shots.length - 1" @click="moveShot(sc.scene_id, i, 1)">↓</button>
                <button class="cw-mini danger" @click="removeShot(sc.scene_id, sh.shot_id)">删除</button>
              </span>
            </div>
            <textarea v-model="sh.description" class="cw-textarea" rows="2" placeholder="本镜意图 / description"></textarea>
            <div class="cw-shot-fields">
              <label>角色 <input v-model="charsTextMap[sh.shot_id]" class="cw-input sm" @change="applyChars(sc.scene_id, sh.shot_id)"
                     placeholder="林夏、暗影" /></label>
              <label>目标时长(s) <input type="number" v-model.number="sh.target_duration_s" class="cw-input sm" /></label>
            </div>
          </div>
          <button class="cw-btn sm" @click="addShot(sc.scene_id)">＋ 新增镜头</button>
        </div>
      </div>

      <!-- 右：预览/Diff/Impact/History -->
      <div class="cw-right">
        <div class="cw-actions">
          <span class="cw-pending">待提交修改 <b>{{ pendingCount }}</b> 项</span>
          <button class="cw-btn primary" @click="doPreview" :disabled="busy || pendingCount === 0">预览 {{ pendingCount }} 项修改</button>
        </div>

        <div v-if="preview" class="cw-preview">
          <div class="cw-panel">
            <div class="cw-panel-title">修改（Diff）</div>
            <div v-for="(d, i) in preview.diff" :key="i" class="cw-diff-row" :class="d.kind">
              <span class="cw-kind">{{ diffLabel(d) }}</span>
              <span class="cw-diff-text">{{ diffText(d) }}</span>
            </div>
            <div v-if="!preview.diff.length" class="cw-muted">无差异</div>
          </div>
          <div class="cw-panel">
            <div class="cw-panel-title">影响（Impact）</div>
            <div class="cw-impact-summary">
              <span>故事段 {{ preview.impact.summary.scenes_changed }}</span>
              <span>新增 {{ preview.impact.summary.shots_added }}</span>
              <span>删除 {{ preview.impact.summary.shots_deleted }}</span>
              <span>修改 {{ preview.impact.summary.shots_modified }}</span>
              <span>移动 {{ preview.impact.summary.shots_moved }}</span>
              <span>规划重建 {{ preview.impact.summary.plans_to_rebuild }}</span>
              <span>建议重制 {{ preview.impact.summary.shots_recommended_for_regeneration }}</span>
              <span>连续性 {{ preview.impact.summary.continuity }}</span>
              <span>已有资产 {{ preview.impact.summary.existing_assets }}</span>
            </div>
            <details>
              <summary class="cw-muted">明细 ({{ preview.impact.items.length }})</summary>
              <div v-for="(it, i) in preview.impact.items" :key="i" class="cw-impact-item">
                <span class="cw-reason">{{ it.reason_code }}</span>
                <span class="cw-entity">{{ it.entity.entity_type }} {{ it.entity.entity_id }}</span>
                <span class="cw-action">{{ it.recommended_action }}</span>
              </div>
            </details>
            <div v-if="preview.warnings.length" class="cw-warn">⚠ {{ preview.warnings.join('；') }}</div>
          </div>
          <div class="cw-apply-row">
            <button class="cw-btn primary" @click="doApply" :disabled="busy">应用到实验版本</button>
            <button class="cw-btn" @click="doReject" :disabled="busy">放弃</button>
          </div>
        </div>

        <div class="cw-panel">
          <div class="cw-panel-title">修改历史</div>
          <div v-for="c in appliedHistory" :key="c.changeset_id" class="cw-hist">
            <span class="cw-hist-rev">#{{ c.base_workspace_revision + 1 }}</span>
            <span class="cw-hist-title">{{ c.title || '(未命名)' }}</span>
            <span class="cw-hist-status" :class="c.status">{{ c.status }}</span>
          </div>
          <div v-if="!appliedHistory.length" class="cw-muted">暂无已应用的修改</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive } from 'vue';
import { apiClient, type WorkspaceMeta, type ProjectShots, type PreviewResult, type ChangeOp } from '@/api';

const props = defineProps<{ projectName: string; initialWorkspaceId?: string }>();

const workspaces = ref<WorkspaceMeta[]>([]);
const activeWid = ref<string | null>(null);
const meta = ref<WorkspaceMeta | null>(null);
const working = ref<ProjectShots | null>(null);   // 最后已应用版本
const draft = ref<ProjectShots | null>(null);     // 可编辑草稿
const preview = ref<PreviewResult | null>(null);
const history = ref<any[]>([]);
const busy = ref(false);
const err = ref('');
const newWsName = ref('');

// 角色 <-> 文本 映射（编辑用）
const charsTextMap = reactive<Record<string, string>>({});

function clone<T>(o: T): T { return JSON.parse(JSON.stringify(o)); }

async function loadList() {
  try {
    workspaces.value = await apiClient.listWorkspaces(props.projectName);
  } catch (e: any) {
    err.value = '加载版本列表失败：' + (e.message || e);
  }
}

async function doCreate() {
  if (!newWsName.value.trim()) return;
  busy.value = true; err.value = '';
  try {
    const w = await apiClient.createWorkspace(props.projectName, newWsName.value.trim());
    newWsName.value = '';
    await loadList();
    activeWid.value = w.workspace_id;
    await loadWorkspace();
  } catch (e: any) { err.value = '新建失败：' + (e.message || e); }
  finally { busy.value = false; }
}

async function onSelect() {
  if (activeWid.value) await loadWorkspace();
  else { meta.value = null; working.value = null; draft.value = null; preview.value = null; }
}

async function loadWorkspace() {
  if (!activeWid.value) return;
  busy.value = true; err.value = ''; preview.value = null;
  try {
    meta.value = await apiClient.getWorkspace(props.projectName, activeWid.value);
    working.value = await apiClient.getWorkspaceProject(props.projectName, activeWid.value);
    draft.value = clone(working.value);
    history.value = await apiClient.listChangesets(props.projectName, activeWid.value);
    // 初始化角色文本
    for (const sc of draft.value.scenes)
      for (const sh of sc.shots)
        charsTextMap[sh.shot_id] = (sh.characters || []).join('、');
  } catch (e: any) { err.value = '加载版本失败：' + (e.message || e); }
  finally { busy.value = false; }
}

function applyChars(sceneId: string, shotId: string) {
  const sc = draft.value?.scenes.find(s => s.scene_id === sceneId);
  const sh = sc?.shots.find(s => s.shot_id === shotId);
  if (sh) sh.characters = (charsTextMap[shotId] || '').split(/[、,，]/).map(t => t.trim()).filter(Boolean);
}

function addShot(sceneId: string) {
  const sc = draft.value?.scenes.find(s => s.scene_id === sceneId);
  if (!sc) return;
  const tempId = `__new_${Date.now() % 100000}`;
  sc.shots.push({
    shot_id: tempId, scene_id: sceneId, index: sc.shots.length + 1,
    description: '', characters: [...(sc.characters || [])], target_duration_s: 5,
    // ShotData 其余字段给最小默认（编辑器只用上面几个）
    } as any);
  charsTextMap[tempId] = (sc.characters || []).join('、');
}

function removeShot(sceneId: string, shotId: string) {
  const sc = draft.value?.scenes.find(s => s.scene_id === sceneId);
  if (sc) sc.shots = sc.shots.filter(s => s.shot_id !== shotId);
}

function moveShot(sceneId: string, idx: number, dir: number) {
  const sc = draft.value?.scenes.find(s => s.scene_id === sceneId);
  if (!sc) return;
  const j = idx + dir;
  if (j < 0 || j >= sc.shots.length) return;
  const arr = sc.shots;
  [arr[idx], arr[j]] = [arr[j], arr[idx]];
}

// ============ 由 (working, draft) 计算语义 operations（§十）============
function computeOps(w: ProjectShots | null, d: ProjectShots | null): ChangeOp[] {
  if (!w || !d) return [];
  const ops: ChangeOp[] = [];
  const wScenes = new Map(w.scenes.map(s => [s.scene_id, s]));
  for (const ds of d.scenes) {
    const ws = wScenes.get(ds.scene_id);
    if (!ws) continue;   // v1 不支持新增 scene
    // scene 级
    const sop: ChangeOp = { type: 'update_scene', scene_id: ds.scene_id };
    if (ws.summary !== ds.summary) sop.summary = { before: ws.summary, after: ds.summary };
    if (JSON.stringify(ws.characters) !== JSON.stringify(ds.characters))
      sop.characters = { before: ws.characters, after: ds.characters };
    if (sop.summary || sop.characters) ops.push(sop);
    // shot 级
    const wShots = new Map(ws.shots.map(s => [s.shot_id, s]));
    const dExisting = ds.shots.filter(s => wShots.has(s.shot_id));
    const dInserted = ds.shots.filter(s => !wShots.has(s.shot_id));
    const dIds = new Set(ds.shots.map(s => s.shot_id));
    for (const wshot of ws.shots)
      if (!dIds.has(wshot.shot_id)) ops.push({ type: 'delete_shot', scene_id: ds.scene_id, shot_id: wshot.shot_id });
    for (const dsh of dExisting) {
      const wsh = wShots.get(dsh.shot_id)!;
      const op: ChangeOp = { type: 'update_shot', scene_id: ds.scene_id, shot_id: dsh.shot_id };
      if (wsh.description !== dsh.description) op.description = { before: wsh.description, after: dsh.description };
      if (JSON.stringify(wsh.characters) !== JSON.stringify(dsh.characters)) op.characters = { before: wsh.characters, after: dsh.characters };
      if (wsh.target_duration_s !== dsh.target_duration_s) op.target_duration_s = { before: wsh.target_duration_s, after: dsh.target_duration_s };
      if (op.description || op.characters || op.target_duration_s) ops.push(op);
    }
    for (const dsh of dInserted)
      ops.push({ type: 'insert_shot', scene_id: ds.scene_id, new_shot: { description: dsh.description, characters: dsh.characters, target_duration_s: dsh.target_duration_s } });
    const wOrder = ws.shots.map(s => s.shot_id);
    const dOrder = dExisting.map(s => s.shot_id);
    if (JSON.stringify(wOrder) !== JSON.stringify(dOrder))
      ops.push({ type: 'reorder_shots', scene_id: ds.scene_id, ordered_shot_ids: dOrder });
  }
  return ops;
}

const pendingCount = computed(() => computeOps(working.value, draft.value).length);
const appliedHistory = computed(() => history.value.filter((c: any) => c.status === 'applied').reverse());

async function doPreview() {
  if (!meta.value || !draft.value) return;
  const ops = computeOps(working.value, draft.value);
  if (!ops.length) return;
  busy.value = true; err.value = '';
  try {
    preview.value = await apiClient.previewChangeset(props.projectName, meta.value.workspace_id, {
      title: `编辑 ${ops.length} 项`, intent: '', base_workspace_revision: meta.value.revision, operations: ops,
    });
  } catch (e: any) {
    err.value = '预览失败：' + (e.message || e);
    preview.value = null;
  } finally { busy.value = false; }
}

async function doApply() {
  if (!preview.value || !meta.value) return;
  busy.value = true; err.value = '';
  try {
    await apiClient.applyChangeset(props.projectName, meta.value.workspace_id, preview.value.changeset_id);
    preview.value = null;
    await loadWorkspace();   // 重新拉 working/draft/history
  } catch (e: any) {
    err.value = '应用失败：' + (e.message || e);
  } finally { busy.value = false; }
}

async function doReject() {
  if (!preview.value || !meta.value) return;
  busy.value = true; err.value = '';
  try {
    await apiClient.rejectChangeset(props.projectName, meta.value.workspace_id, preview.value.changeset_id);
    preview.value = null;
    draft.value = clone(working.value);   // 丢弃草稿编辑
    for (const sc of draft.value.scenes) for (const sh of sc.shots)
      charsTextMap[sh.shot_id] = (sh.characters || []).join('、');
  } catch (e: any) { err.value = '放弃失败：' + (e.message || e); }
  finally { busy.value = false; }
}

async function doArchive() {
  if (!meta.value) return;
  busy.value = true; err.value = '';
  try {
    await apiClient.archiveWorkspace(props.projectName, meta.value.workspace_id);
    await loadList(); await loadWorkspace();
  } catch (e: any) { err.value = '归档失败：' + (e.message || e); }
  finally { busy.value = false; }
}

async function doDelete() {
  if (!meta.value) return;
  if (!confirm('删除实验版本不会影响正式项目，但该实验版本中的修改记录将被删除。')) return;
  busy.value = true; err.value = '';
  try {
    await apiClient.deleteWorkspace(props.projectName, meta.value.workspace_id);
    activeWid.value = null; meta.value = null; working.value = null; draft.value = null; preview.value = null;
    await loadList();
  } catch (e: any) { err.value = '删除失败：' + (e.message || e); }
  finally { busy.value = false; }
}

// diff 渲染辅助
function diffLabel(d: any): string {
  return { update: '改', insert: '＋', delete: '－', reorder: '⇄' }[d.kind as string] || d.kind;
}
function diffText(d: any): string {
  if (d.kind === 'reorder') return `${d.id}: ${d.before_order.join(',')} → ${d.after_order.join(',')}`;
  if (d.kind === 'insert') return `${d.id}: ${d.after?.description || ''}`;
  if (d.kind === 'delete') return `${d.id}: ${d.before?.description || ''}`;
  // update
  const v = (x: any) => (Array.isArray(x) ? x.join('、') : String(x));
  return `${d.id}.${d.field}: ${v(d.before)} → ${v(d.after)}`;
}

loadList().then(() => {
  // AI-6D §20 deep-link：从 ShotBoard「打开创作版本」带 workspace_id 直达该 workspace。
  const init = props.initialWorkspaceId;
  if (init && workspaces.value.some(w => w.workspace_id === init)) {
    activeWid.value = init;
    loadWorkspace();
  }
});
</script>

<style lang="scss" scoped>
.cw-studio { display: flex; flex-direction: column; gap: 12px; max-width: 1200px; }
.cw-banner { background: var(--bg-hover); border: 1px dashed var(--border); color: var(--text-secondary);
  padding: 6px 12px; border-radius: 6px; font-size: 12px; text-align: center; }
.cw-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.cw-selector { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.cw-select, .cw-input { background: var(--bg-root); border: 1px solid var(--border); color: var(--text-primary);
  padding: 6px 10px; border-radius: 6px; font-size: 13px; }
.cw-input.sm { width: 140px; }
.cw-btn { background: var(--bg-hover); border: 1px solid var(--border); color: var(--text-primary);
  padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.cw-btn.primary { background: var(--accent, #60a5fa); color: #fff; border: none; }
.cw-btn.sm { padding: 4px 8px; font-size: 12px; }
.cw-btn:disabled { opacity: .5; cursor: not-allowed; }
.cw-meta { display: flex; gap: 6px; }
.chip { background: var(--bg-hover); padding: 3px 8px; border-radius: 10px; font-size: 11px; color: var(--text-muted); }
.chip.stale { background: #f59e0b33; color: #f59e0b; }
.cw-err { color: #ef4444; font-size: 12px; }
.cw-empty { color: var(--text-muted); padding: 24px; text-align: center; }
.cw-body { display: grid; grid-template-columns: 1.4fr 1fr; gap: 16px; }
.cw-left, .cw-right { display: flex; flex-direction: column; gap: 12px; }
.cw-scene { border: 1px solid var(--border); border-radius: 8px; padding: 10px; background: var(--bg-root); }
.cw-scene-head { display: flex; gap: 8px; align-items: baseline; margin-bottom: 8px; }
.cw-scene-id { font-weight: 600; color: var(--text-primary); font-size: 13px; }
.cw-scene-summary { color: var(--text-muted); font-size: 12px; }
.cw-shot { border-top: 1px solid var(--border); padding: 8px 0; }
.cw-shot:first-of-type { border-top: none; }
.cw-shot-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.cw-shot-id { font-size: 12px; color: var(--text-muted); font-family: monospace; }
.cw-shot-actions { display: flex; gap: 4px; }
.cw-mini { background: none; border: 1px solid var(--border); color: var(--text-muted); padding: 2px 6px;
  border-radius: 4px; cursor: pointer; font-size: 11px; }
.cw-mini.danger { color: #ef4444; }
.cw-mini:disabled { opacity: .4; cursor: not-allowed; }
.cw-textarea { width: 100%; background: var(--bg-root); border: 1px solid var(--border); color: var(--text-primary);
  border-radius: 6px; padding: 6px; font-size: 13px; resize: vertical; box-sizing: border-box; }
.cw-shot-fields { display: flex; gap: 12px; margin-top: 4px; font-size: 12px; color: var(--text-muted); }
.cw-actions { display: flex; justify-content: space-between; align-items: center; }
.cw-pending { font-size: 12px; color: var(--text-muted); }
.cw-pending b { color: var(--text-primary); }
.cw-preview { display: flex; flex-direction: column; gap: 10px; }
.cw-panel { border: 1px solid var(--border); border-radius: 8px; padding: 10px; background: var(--bg-root); }
.cw-panel-title { font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-bottom: 6px; }
.cw-diff-row { display: flex; gap: 8px; font-size: 12px; padding: 2px 0; }
.cw-kind { width: 18px; font-weight: 700; }
.cw-diff-row.update .cw-kind { color: #60a5fa; }
.cw-diff-row.insert .cw-kind { color: #34d399; }
.cw-diff-row.delete .cw-kind { color: #ef4444; }
.cw-diff-row.reorder .cw-kind { color: #f59e0b; }
.cw-impact-summary { display: flex; flex-wrap: wrap; gap: 8px; font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }
.cw-impact-summary span { background: var(--bg-hover); padding: 2px 8px; border-radius: 8px; }
.cw-impact-item { display: flex; gap: 6px; font-size: 11px; padding: 2px 0; color: var(--text-muted); flex-wrap: wrap; }
.cw-reason { font-family: monospace; color: var(--text-secondary); }
.cw-warn { color: #f59e0b; font-size: 12px; margin-top: 4px; }
.cw-apply-row { display: flex; gap: 8px; }
.cw-hist { display: flex; gap: 8px; align-items: center; font-size: 12px; padding: 4px 0; }
.cw-hist-rev { font-weight: 700; color: var(--text-primary); }
.cw-hist-title { color: var(--text-secondary); flex: 1; }
.cw-hist-status { font-size: 10px; padding: 1px 6px; border-radius: 8px; background: var(--bg-hover); color: var(--text-muted); }
.cw-muted { color: var(--text-muted); font-size: 12px; }
</style>
