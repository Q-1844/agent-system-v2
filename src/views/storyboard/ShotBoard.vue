<template>
  <div class="shot-board">
    <!-- 无当前项目 -->
    <div v-if="!projectName" class="empty">
      请先选择项目
      <button class="go-btn" @click="goDashboard">前往项目页</button>
    </div>

    <!-- 无故事段 -->
    <div v-else-if="!segments.length" class="empty">
      当前项目还没有分镜故事，请先在剧本页添加分镜故事。
      <button class="go-btn" @click="goScript">前往剧本</button>
    </div>

    <!-- 主体：左故事段列表 + 右当前故事段 -->
    <div v-else class="board">
      <button v-if="listCollapsed" class="expand-fab" @click="listCollapsed = false" title="展开故事段列表">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6" /></svg>
        <span>故事段</span>
      </button>
      <aside class="seg-list" :class="{ collapsed: listCollapsed }">
        <div class="seg-list-head">
          <span class="seg-list-title">故事段</span>
          <button class="icon-btn" title="收起故事段列表" @click="listCollapsed = true">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6" /></svg>
          </button>
        </div>
        <div class="seg-list-content">
        <div
          v-for="(seg, i) in segments"
          :key="seg.scene_id"
          class="seg-item"
          :class="{ active: selected?.scene_id === seg.scene_id }"
          @click="selected = seg"
        >
          <div class="seg-row1">
            <span class="seg-seq">{{ i + 1 }}</span>
            <span class="seg-title">{{ seg.summary.trim() ? seg.summary.trim().slice(0, 14) : '未填写故事' }}</span>
          </div>
          <div class="seg-row2">
            <span class="seg-count">{{ shotDone(seg) }}/{{ seg.shots.length }} 镜</span>
            <img v-if="segThumb(seg)" :src="segThumb(seg)" class="seg-thumb" />
          </div>
        </div>
        </div>
      </aside>

      <section class="seg-detail" v-if="selected">
        <!-- 故事段顶部：原文(只读) + 角色 + 覆盖 + 返回剧本 -->
        <div class="seg-head">
          <div class="seg-head-top">
            <span class="seg-no">故事段 {{ selected.scene_id }}</span>
            <div class="seg-head-actions">
              <button class="ai-direct-btn" @click="openDirector">AI 设计分镜</button>
              <button class="ai-direct-btn"
                      :disabled="!selected.shots.length"
                      :title="selected.shots.length ? '' : '请先创建镜头或使用 AI 设计分镜。'"
                      @click="openPlanner">AI 制作规划</button>
              <button class="link-btn" @click="goScript">返回剧本修改</button>
            </div>
          </div>
          <p class="seg-story">{{ selected.summary || '（未填写故事）' }}</p>
          <div class="seg-chars">
            <span class="seg-chars-label">出场角色：</span>
            <span v-if="!selected.characters.length" class="muted">无</span>
            <span v-for="name in selected.characters" :key="name" class="seg-char" :class="{ miss: !charCovered(name) }">
              {{ name }} {{ charCovered(name) ? '✓' : '⚠' }}
            </span>
          </div>
        </div>

        <!-- AI 导演采用后提示（§三十六）-->
        <div v-if="appliedHint" class="applied-hint">
          <span>AI 已创建 {{ appliedHint.count }} 个镜头。接下来可打开镜头工作台，生成首帧、尾帧和视频提示词。</span>
          <button class="link-btn" v-if="appliedHint.firstShotId" @click="openFirstShot">打开镜头 1</button>
          <button class="link-btn" @click="appliedHint = null">关闭</button>
        </div>

        <!-- 镜头列表 -->
        <div class="shot-list">
          <template v-for="(sh, i) in selected.shots" :key="sh.shot_id">
            <div v-if="i > 0" class="shot-connector" :class="connClass(sh)">
              <span class="conn-line"></span>
              <span class="conn-label" v-if="sh.opening_source !== 'independent'">{{ connLabel(sh) }}</span>
              <span v-else class="conn-label muted">独立</span>
              <span class="conn-line"></span>
            </div>
          <div
            class="shot-item"
            :data-shot-id="sh.shot_id"
            @dblclick="openWorkbench(sh)"
            @contextmenu.prevent="openCtxMenu($event, sh)"
          >
            <div class="shot-meta">
              <span class="shot-seq">{{ sh.shot_id }}</span>
              <span class="shot-size">{{ sh.shot_size || '中景' }}</span>
              <span class="shot-desc">{{ (sh.description || '').slice(0, 24) || '未填写画面' }}</span>
              <span class="shot-chars" v-if="sh.characters.length">{{ sh.characters.join('、') }}</span>
              <span v-if="sh.opening_source !== 'independent'" class="shot-cont" :class="connClass(sh)">{{ connLabel(sh) }}</span>
              <span class="shot-status" :class="shotStatusClass(sh)">{{ shotStatusText(sh) }}</span>
              <button class="open-btn" @click="openWorkbench(sh)">打开工作台</button>
            </div>
            <div class="shot-previews">
              <div class="preview-box" @click="previewImage(ffUrl(sh))" :class="{ disabled: !ffUrl(sh) }">
                <img v-if="ffUrl(sh)" :src="ffUrl(sh)" />
                <SafeFrameOverlay v-if="ffUrl(sh)" />
                <span v-else class="ph">首帧</span>
              </div>
              <div class="preview-box" @click="lfUrl(sh) && previewImage(lfUrl(sh))" :class="{ disabled: !lfUrl(sh), na: sh.link_mode !== 'keyframes' }">
                <img v-if="lfUrl(sh)" :src="lfUrl(sh)" />
                <span v-else class="ph">{{ sh.link_mode === 'keyframes' ? '尾帧' : '不需要' }}</span>
              </div>
              <div class="preview-box" @click="vidUrl(sh) && previewVideo(vidUrl(sh))" :class="{ disabled: !vidUrl(sh) }">
                <video v-if="vidUrl(sh)" :src="vidUrl(sh)" preload="metadata" />
                <span v-else class="ph">视频</span>
              </div>
            </div>
          </div>
          </template>
          <button class="add-shot-btn" @click="addShot">+ 添加镜头</button>
        </div>
      </section>
    </div>

    <!-- 工作台弹层 -->
    <Workbench
      v-if="workbenchShot"
      :key="workbenchShot.shot_id"
      :shot="workbenchShot"
      :project-name="projectName"
      :project-characters="projectCharacters"
      :project-style-label="projectStyleLabel"
      :project-ratio="projectRatio"
      :scene-story="selected?.summary || ''"
      :scene-id="selected?.scene_id || ''"
      :scene-shots="selected?.shots || []"
      @close="closeWorkbench"
      @refresh="load"
    />

    <!-- 大图/视频预览 -->
    <div v-if="previewUrl" class="preview-overlay" @click="previewUrl = ''">
      <img v-if="previewType === 'image'" :src="previewUrl" class="big-img" />
      <video v-else :src="previewUrl" controls autoplay class="big-video" />
    </div>

    <!-- AI 导演工作区（Phase AI-3） -->
    <DirectorStudio
      v-if="showDirector && selected"
      :project-name="projectName"
      :scene-id="selected.scene_id"
      :scene-summary="selected.summary"
      :scene-characters="selected.characters"
      :has-existing-shots="selected.shots.length > 0"
      @close="showDirector = false"
      @applied="onDirectorApplied"
    />

    <!-- AI 制作规划工作区（Phase AI-4A） -->
    <ProductionPlannerStudio
      v-if="showPlanner && selected"
      :project-name="projectName"
      :scene-id="selected.scene_id"
      :scene-summary="selected.summary"
      :scene-characters="selected.characters"
      :shots="selected.shots.map(s => ({ shot_id: s.shot_id, description: s.description, characters: s.characters, target_duration_s: s.target_duration_s }))"
      @close="showPlanner = false"
      @approved="onPlannerApproved"
    />

    <!-- 镜头右键菜单 -->
    <div v-if="ctxMenu" class="ctx-menu" :style="ctxMenuStyle" @click.stop>
      <div class="ctx-shot-id">{{ ctxMenu.shot.shot_id }}</div>
      <button class="ctx-item" @click="openWorkbench(ctxMenu.shot); closeCtxMenu()">打开工作台</button>
      <button class="ctx-item danger" @click="confirmDeleteShot">删除镜头</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { useRouter } from 'vue-router';
import { apiClient } from '@/api';
import type { ShotData } from '@/api';
import { useProjectStore } from '@/store';
import { useJobStore } from '@/store/jobs';
import { getImageUrl, getVideoUrl } from '@/utils/media';
import SafeFrameOverlay from '@/components/SafeFrameOverlay.vue';
import Workbench from '@/components/Workbench.vue';
import DirectorStudio from '@/components/DirectorStudio.vue';
import ProductionPlannerStudio from '@/components/ProductionPlannerStudio.vue';

const router = useRouter();
const projStore = useProjectStore();
const projectName = computed(() => projStore.projectName);
const jobStore = useJobStore();

interface Segment {
  scene_id: string; summary: string; characters: string[]; shots: ShotData[];
}
const segments = ref<Segment[]>([]);
const selected = ref<Segment | null>(null);
const listCollapsed = ref(false);
const workbenchShot = ref<ShotData | null>(null);
const previewUrl = ref('');
const previewType = ref<'image' | 'video'>('image');
const showDirector = ref(false);
const showPlanner = ref(false);
const appliedHint = ref<{ count: number; firstShotId: string | null } | null>(null);
// 项目级数据（传给 Workbench 作公共上下文：角色多选/风格/画幅徽标）
const projectCharacters = ref<Array<{ name: string; description_en: string; views: any }>>([]);
const projectStyleLabel = ref('');
const projectRatio = ref('');

function goDashboard() { router.push('/dashboard'); }
function goScript() { router.push('/script-editor'); }

async function load() {
  if (!projectName.value) { segments.value = []; return; }
  try {
    const data = await apiClient.getProjectShots(projectName.value);
    projectCharacters.value = data.characters || [];
    projectStyleLabel.value = (data.style && data.style.art_style) || '';
    projectRatio.value = (data.style && data.style.aspect_ratio) || '';
    segments.value = (data.scenes || []).map((s: any) => ({
      scene_id: s.scene_id, summary: s.summary || '', characters: s.characters || [], shots: s.shots || [],
    }));
    // 保持选中（若仍存在），否则选第一个
    if (selected.value) {
      const cur = segments.value.find(s => s.scene_id === selected.value!.scene_id);
      selected.value = cur || segments.value[0] || null;
    } else {
      selected.value = segments.value[0] || null;
    }
    // 工作台打开时，刷新其 shot 引用（生成完成后工作台能看到新候选/视频）
    if (workbenchShot.value) {
      const wshot = segments.value.flatMap(s => s.shots).find(sh => sh.shot_id === workbenchShot.value!.shot_id);
      if (wshot) workbenchShot.value = wshot;
    }
  } catch (e: any) {
    console.error('[ShotBoard] 加载失败', e);
    segments.value = [];
    selected.value = null;
  }
}

// 镜头完成数（video done）
function shotDone(seg: Segment): number {
  return seg.shots.filter(sh => sh.video?.status === 'done' && sh.video?.local).length;
}
// 故事段首帧缩略图（第一个有首帧的镜头）
function segThumb(seg: Segment): string {
  for (const sh of seg.shots) {
    const u = ffUrl(sh);
    if (u) return u;
  }
  return '';
}
// 角色覆盖：该角色是否出现在故事段任一镜头
function charCovered(name: string): boolean {
  return !!selected.value?.shots.some(sh => sh.characters.includes(name));
}

// 三预览框 URL（读批次选择指针 selected_first_frame / selected_last_frame / final_video）
function ffUrl(sh: ShotData): string {
  const cid = sh.selected_first_frame;
  if (!cid) return '';
  for (const b of sh.first_frame_batches || []) {
    const c = (b.candidates || []).find(x => x.candidate_id === cid);
    if (c?.local) return getImageUrl(c.local);
  }
  return '';
}
function lfUrl(sh: ShotData): string {
  if (sh.link_mode !== 'keyframes') return '';
  const cid = sh.selected_last_frame;
  if (!cid) return '';
  for (const b of sh.last_frame_batches || []) {
    const c = (b.candidates || []).find(x => x.candidate_id === cid);
    if (c?.local) return getImageUrl(c.local);
  }
  return '';
}
function vidUrl(sh: ShotData): string {
  const vid = sh.final_video;
  if (!vid) return '';
  const v = (sh.video_versions || []).find(x => x.version_id === vid);
  return v?.local ? getVideoUrl(v.local) : '';
}

function shotStatusText(sh: ShotData): string {
  const busy = jobStore.byShot(sh.shot_id).some(j => j.state === 'running' || j.state === 'queued');
  if (busy) {
    const j = jobStore.byShot(sh.shot_id).find(j => j.state === 'running' || j.state === 'queued');
    return j?.kind === 'video' ? '视频排队中' : '生成中';
  }
  if (sh.video?.status === 'done' && sh.video?.local) return '已完成';
  if (sh.first_frame?.status === 'failed' || sh.video?.status === 'failed') return '失败';
  return '未完成';
}
function shotStatusClass(sh: ShotData): string {
  const s = shotStatusText(sh);
  if (s === '已完成') return 'done';
  if (s === '失败') return 'failed';
  if (s === '未完成') return 'pending';
  return 'busy';
}

function openWorkbench(sh: ShotData) { workbenchShot.value = sh; }
function closeWorkbench() { workbenchShot.value = null; }

// ---- AI 导演（Phase AI-3）----
function openDirector() {
  if (!selected.value) return;
  appliedHint.value = null;
  showDirector.value = true;
}
async function onDirectorApplied(firstNewShotId: string | null) {
  showDirector.value = false;
  await load();   // 刷新：新 Shot[] 出现在当前 Scene 下
  if (firstNewShotId) {
    await nextTick();
    const el = document.querySelector(`[data-shot-id="${firstNewShotId}"]`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  if (selected.value) {
    appliedHint.value = { count: selected.value.shots.length, firstShotId: firstNewShotId };
  }
}
function openFirstShot() {
  if (appliedHint.value?.firstShotId) {
    const sh = segments.value.flatMap(s => s.shots).find(s => s.shot_id === appliedHint.value!.firstShotId);
    if (sh) openWorkbench(sh);
  }
  appliedHint.value = null;
}

// ---- AI 制作规划（Phase AI-4A）----
function openPlanner() {
  if (!selected.value) return;
  if (!selected.value.shots.length) return;   // 无 Shot 禁用（按钮已 disabled，双保险）
  showPlanner.value = true;
}
function onPlannerApproved() {
  // 🔴 approve 不改 Shot（opening_source 仍默认 independent），无需 reload。
  // 正式 opening_source 由 AI-4B Executor 执行时生效。
  showPlanner.value = false;
}

// Phase 6A：镜头接力标识（开场来源）。仅展示，不打开工作台也能看懂关系。
function connLabel(sh: ShotData): string {
  if (sh.opening_source === 'inherit_previous') return '末帧接力';
  if (sh.opening_source === 'reference_previous') return '参考接续';
  return '独立';
}
function connClass(sh: ShotData): string {
  if (sh.opening_source === 'inherit_previous') return 'inherit';
  if (sh.opening_source === 'reference_previous') return 'ref';
  return 'none';
}

function previewImage(url: string) { if (!url) return; previewType.value = 'image'; previewUrl.value = url; }
function previewVideo(url: string) { if (!url) return; previewType.value = 'video'; previewUrl.value = url; }

async function addShot() {
  if (!selected.value || !projectName.value) return;
  try {
    await apiClient.addShot(projectName.value, selected.value.scene_id);
    await load();
  } catch (e: any) {
    console.error('[ShotBoard] 添加镜头失败', e);
  }
}

// ---------- 镜头右键菜单（删除镜头）----------
const ctxMenu = ref<{ x: number; y: number; shot: ShotData } | null>(null);
const ctxMenuStyle = computed(() => {
  const m = ctxMenu.value; if (!m) return {};
  const w = 168, h = 104;
  return {
    top: Math.min(m.y, (window.innerHeight || 9999) - h) + 'px',
    left: Math.min(m.x, (window.innerWidth || 9999) - w) + 'px',
  };
});
function openCtxMenu(e: MouseEvent, sh: ShotData) {
  ctxMenu.value = { x: e.clientX, y: e.clientY, shot: sh };
}
function closeCtxMenu() { ctxMenu.value = null; }
async function confirmDeleteShot() {
  const sh = ctxMenu.value?.shot;
  closeCtxMenu();
  if (!sh || !projectName.value) return;
  const hasArtifacts = sh.video?.local
    || (sh.first_frame?.candidates?.length ?? 0) > 0
    || (sh.last_frame?.candidates?.length ?? 0) > 0;
  const msg = hasArtifacts
    ? `确认删除镜头 ${sh.shot_id}？\n该镜头的首帧/尾帧/视频等产物文件会一并删除，无法恢复。`
    : `确认删除镜头 ${sh.shot_id}？`;
  if (!window.confirm(msg)) return;
  try {
    await apiClient.deleteShot(projectName.value, sh.shot_id);
    await load();
  } catch (e: any) {
    console.error('[ShotBoard] 删除镜头失败', e);
  }
}

// shot 级 job 完成 -> debounce 刷新（保持卡片/工作台与服务端同步）
let reloadTimer: number | null = null;
watch(() => jobStore.jobs.map(j => j.job_id + j.state).join('|'), () => {
  const finished = jobStore.jobs.some(j => (j.state === 'done' || j.state === 'failed') && j.shot_id);
  if (finished) {
    if (reloadTimer) clearTimeout(reloadTimer);
    reloadTimer = window.setTimeout(() => { load(); reloadTimer = null; }, 800);
  }
});

function onDocClick() { closeCtxMenu(); }
function onKey(e: KeyboardEvent) { if (e.key === 'Escape') closeCtxMenu(); }
onMounted(() => {
  load();
  document.addEventListener('click', onDocClick);
  document.addEventListener('keydown', onKey);
});
onUnmounted(() => {
  document.removeEventListener('click', onDocClick);
  document.removeEventListener('keydown', onKey);
});
watch(projectName, () => { selected.value = null; load(); });
</script>

<style lang="scss" scoped>
.shot-board { height: calc(100vh - 88px); }
.empty {
  height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 14px; color: var(--text-muted); font-size: 14px;
}
.go-btn {
  padding: 8px 18px; border-radius: 6px; border: none; background: var(--accent);
  color: var(--accent-text); font-size: 13px; cursor: pointer; &:hover { background: var(--accent-hover); }
}

.board { display: flex; gap: 16px; height: 100%; position: relative; }

.expand-fab {
  position: absolute; top: 10px; left: 10px; z-index: 10;
  display: flex; align-items: center; gap: 4px; padding: 5px 10px 5px 8px;
  border-radius: 6px; border: 1px solid var(--border); background: var(--bg-surface);
  color: var(--text-muted); font-size: 12px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-secondary); }
}

.seg-list {
  width: 220px; flex-shrink: 0; overflow: hidden; background: var(--bg-surface);
  border: 1px solid var(--border); border-radius: 8px; padding: 6px;
  display: flex; flex-direction: column;
  transition: width 0.2s ease, border-color 0.2s ease, padding 0.2s ease;
  &.collapsed { width: 0; border: none; padding: 0; background: transparent; }
}
.seg-list-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 4px 8px 8px; flex-shrink: 0;
}
.seg-list-title { font-size: 12px; font-weight: 600; color: var(--text-muted); }
.seg-list-content { flex: 1; overflow-y: auto; }
.icon-btn {
  width: 24px; height: 24px; border-radius: 4px; border: none; background: none;
  color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center;
  &:hover { background: var(--bg-hover); color: var(--text-secondary); }
}
.seg-item {
  padding: 10px 12px; border-radius: 6px; cursor: pointer; border-left: 2px solid transparent;
  &:hover { background: var(--bg-hover); }
  &.active { background: var(--bg-hover); border-left-color: var(--accent); }
}
.seg-row1 { display: flex; align-items: center; gap: 8px; }
.seg-seq { font-size: 11px; color: var(--text-faint); }
.seg-title { font-size: 13px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.seg-row2 { display: flex; align-items: center; justify-content: space-between; margin-top: 6px; }
.seg-count { font-size: 11px; color: var(--text-muted); }
.seg-thumb { width: 40px; height: 22px; object-fit: cover; border-radius: 3px; background: var(--bg-root); }

.seg-detail { flex: 1; min-width: 0; overflow-y: auto; }
.seg-head { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; margin-bottom: 14px; }
.seg-head-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.seg-head-actions { display: flex; align-items: center; gap: 10px; }
.seg-no { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.ai-direct-btn {
  padding: 4px 12px; border-radius: 6px; border: 1px solid var(--accent);
  background: transparent; color: var(--accent); font-size: 12px; cursor: pointer;
  &:hover:not(:disabled) { background: var(--accent); color: var(--accent-text); }
  &:disabled { opacity: 0.4; cursor: not-allowed; border-color: var(--border); color: var(--text-muted); }
}
.link-btn { background: none; border: none; color: var(--text-muted); font-size: 12px; cursor: pointer; &:hover { color: var(--accent); } }
.applied-hint {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  background: rgba(74, 222, 128, 0.1); border: 1px solid #4ade80; border-radius: 6px;
  padding: 8px 12px; margin-bottom: 12px; font-size: 12px; color: #4ade80;
  .link-btn { color: var(--accent); margin-left: auto; }
}
.seg-story { font-size: 13px; color: var(--text-secondary); line-height: 1.6; margin: 0 0 10px; }
.seg-chars { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.seg-chars-label { font-size: 11px; color: var(--text-muted); }
.seg-char { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: rgba(74,222,128,0.15); color: #4ade80;
  &.miss { background: rgba(251,191,36,0.15); color: #fbbf24; } }
.muted { font-size: 11px; color: var(--text-faint); }

.shot-list { display: flex; flex-direction: column; gap: 10px; }
.shot-item {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px;
  &:hover { border-color: var(--text-muted); }
}
.shot-connector { display: flex; align-items: center; gap: 8px; padding: 2px 0 2px 14px; }
.conn-line { flex: 0 0 18px; height: 1px; background: var(--border); }
.conn-label { font-size: 10px; color: var(--text-muted); padding: 1px 7px; border-radius: 8px; border: 1px solid var(--border); white-space: nowrap;
  &.muted { color: var(--text-faint); } }
.shot-connector.ref .conn-label, .shot-cont.ref { color: #60a5fa; border-color: rgba(96,165,250,0.4); }
.shot-connector.inherit .conn-label, .shot-cont.inherit { color: #4ade80; border-color: rgba(74,222,128,0.4); }
.shot-cont { font-size: 10px; padding: 1px 7px; border-radius: 8px; border: 1px solid var(--border); white-space: nowrap; }
.shot-meta { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.shot-seq { font-size: 12px; font-weight: 600; color: var(--text-primary); font-family: monospace; }
.shot-size { font-size: 11px; color: var(--text-muted); padding: 1px 6px; border-radius: 3px; background: var(--bg-hover); }
.shot-desc { font-size: 12px; color: var(--text-secondary); flex: 1; min-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.shot-chars { font-size: 11px; color: var(--text-faint); }
.shot-status { font-size: 11px; padding: 1px 6px; border-radius: 3px;
  &.done { background: rgba(74,222,128,0.15); color: #4ade80; }
  &.failed { background: rgba(248,113,113,0.15); color: #f87171; }
  &.busy { background: rgba(96,165,250,0.15); color: #60a5fa; }
  &.pending { background: var(--bg-hover); color: var(--text-muted); }
}
.open-btn {
  margin-left: auto; padding: 4px 12px; border-radius: 5px; border: 1px solid var(--accent);
  background: transparent; color: var(--accent); font-size: 12px; cursor: pointer;
  &:hover { background: var(--accent); color: var(--accent-text); }
}

.shot-previews { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }
.preview-box {
  position: relative; aspect-ratio: 16/9; background: var(--bg-root); border: 1px solid var(--border);
  border-radius: 5px; overflow: hidden; cursor: pointer; display: flex; align-items: center; justify-content: center;
  &:hover { border-color: var(--accent); }
  &.disabled { cursor: default; &:hover { border-color: var(--border); } }
  &.na .ph { color: var(--text-faint); }
  img, video { width: 100%; height: 100%; object-fit: cover; }
  .ph { font-size: 11px; color: var(--text-faint); }
}
.add-shot-btn {
  margin-top: 4px; padding: 9px; border-radius: 6px; border: 1px dashed var(--border);
  background: transparent; color: var(--text-muted); font-size: 13px; cursor: pointer;
  &:hover { border-color: var(--accent); color: var(--accent); }
}

.preview-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.85); z-index: 2000;
  display: flex; align-items: center; justify-content: center; cursor: zoom-out;
  .big-img { max-width: 92vw; max-height: 92vh; object-fit: contain; }
  .big-video { max-width: 92vw; max-height: 92vh; }
}

.ctx-menu {
  position: fixed; z-index: 3000; min-width: 140px;
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: 6px; box-shadow: 0 6px 20px rgba(0,0,0,0.25); padding: 4px;
}
.ctx-shot-id { font-size: 11px; color: var(--text-faint); padding: 4px 10px; font-family: monospace; }
.ctx-item {
  display: block; width: 100%; text-align: left; padding: 7px 10px;
  border: none; background: none; color: var(--text-secondary); font-size: 13px;
  border-radius: 4px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
  &.danger { color: #f87171; &:hover { background: rgba(248,113,113,0.12); color: #f87171; } }
}
</style>
