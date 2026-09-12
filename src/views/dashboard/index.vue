<template>
  <div class="dashboard">
    <div class="header">
      <div>
        <h1>Storyboard</h1>
        <p class="subtitle">AI 动画分镜工作流</p>
      </div>
      <div class="header-actions">
        <button class="btn btn-primary" @click="openAdd">+ 添加项目</button>
      </div>
    </div>

    <!-- 队列概况（全局，保留） -->
    <div class="queue-overview" v-if="queue.running || queue.video_queued || queue.failed">
      <span class="qo-title">任务队列</span>
      <span class="qo-stat">运行中 <b>{{ queue.running }}</b></span>
      <span class="qo-stat">视频排队 <b>{{ queue.video_queued }}</b>
        <span v-if="queue.video_queued" class="qo-eta">≈{{ Math.round(queue.video_eta_s / 60) }}min</span>
      </span>
      <span class="qo-stat fail" v-if="queue.failed">失败 <b>{{ queue.failed }}</b></span>
      <span class="qo-conn" :class="{ off: !jobConnected }">{{ jobConnected ? '● 实时' : '○ 轮询中' }}</span>
    </div>

    <!-- 项目列表 -->
    <div class="proj-section">
      <div class="section-title">项目</div>
      <div class="proj-grid" v-if="projects.length">
        <div
          v-for="p in projects"
          :key="p"
          class="proj-card"
          :class="{ current: p === projectName }"
          @click="enterProject(p)"
        >
          <span class="proj-name">{{ p }}</span>
          <span class="proj-card-right">
            <span class="proj-style" v-if="projectStyleLabel(p)" @click.stop="openStyle(p)">{{ projectStyleLabel(p) }}</span>
            <span class="proj-style unset" v-else @click.stop="openStyle(p)">风格未设置</span>
            <span class="proj-enter">进入 →</span>
          </span>
        </div>
      </div>
      <div v-else class="empty-hint">还没有项目，点「+ 添加项目」开始</div>
    </div>

    <!-- 添加项目对话框 -->
    <div v-if="showAdd" class="dialog-overlay" @click.self="showAdd = false">
      <div class="dialog">
        <h3>新建项目</h3>
        <div class="form-group">
          <label>项目名称</label>
          <input
            v-model="newName"
            class="input"
            placeholder="输入项目名称"
            @keyup.enter="confirmAdd"
            ref="nameInput"
          />
        </div>
        <div class="form-group">
          <label>视觉风格（必选）</label>
          <div class="style-cards">
            <div
              v-for="opt in RENDER_FAMILY_OPTIONS"
              :key="opt.value"
              class="style-card"
              :class="{ selected: newRenderFamily === opt.value }"
              @click="newRenderFamily = opt.value"
            >
              <span class="sc-name">{{ opt.label }}</span>
              <span class="sc-desc">{{ opt.desc }}</span>
            </div>
          </div>
        </div>
        <div class="form-group" v-if="newRenderFamily">
          <label>艺术方向（可选）</label>
          <input
            v-model="newArtDirection"
            class="input"
            :placeholder="`例如：${artDirectionPlaceholder}`"
          />
        </div>
        <div class="dialog-btns">
          <button class="btn btn-cancel" @click="showAdd = false">取消</button>
          <button class="btn btn-confirm" :disabled="adding || !newRenderFamily" @click="confirmAdd">
            {{ adding ? '创建中...' : '创建并进入' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 视觉风格设置对话框（P1 §19 最小入口） -->
    <div v-if="showStyle" class="dialog-overlay" @click.self="showStyle = false">
      <div class="dialog">
        <h3>视觉风格 · {{ styleProjectName }}</h3>
        <div v-if="styleLoading" class="empty-hint">加载中...</div>
        <template v-else>
          <div class="form-group">
            <label>视觉介质 / Render Family</label>
            <div class="style-cards">
              <div
                v-for="opt in RENDER_FAMILY_OPTIONS"
                :key="opt.value"
                class="style-card"
                :class="{ selected: styleRenderFamily === opt.value }"
                @click="styleRenderFamily = opt.value"
              >
                <span class="sc-name">{{ opt.label }}</span>
                <span class="sc-desc">{{ opt.desc }}</span>
              </div>
            </div>
          </div>
          <div class="form-group">
            <label>艺术方向（可选）</label>
            <input v-model="styleArtDirection" class="input" placeholder="例如：cinematic anime with detailed backgrounds" />
          </div>
          <div class="warn-box" v-if="styleHasApprovedAssets">
            ⚠ 更改项目视觉风格可能使已生成的资产过期（existing generated assets may become stale）。
          </div>
          <div class="warn-box" v-else-if="!styleWasSpecified">
            ℹ 该项目尚未设置视觉风格。生成视觉素材前必须先设置（P1 fail closed）。
          </div>
        </template>
        <div class="dialog-btns">
          <button class="btn btn-cancel" @click="showStyle = false">取消</button>
          <button class="btn btn-confirm" :disabled="styleSaving || !styleRenderFamily" @click="confirmStyle">
            {{ styleSaving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useJobStore } from '@/store/jobs';
import { useProjectStore } from '@/store';
import { apiClient } from '@/api';

const jobStore = useJobStore();
const store = useProjectStore();
const router = useRouter();
const queue = computed(() => jobStore.queue);
const jobConnected = computed(() => jobStore.connected);
const projectName = computed(() => store.projectName);

const projects = ref<string[]>([]);
const showAdd = ref(false);
const newName = ref('');
const adding = ref(false);
const nameInput = ref<HTMLInputElement | null>(null);

// ---- P1：视觉风格 ----
const RENDER_FAMILY_OPTIONS = [
  { value: 'photorealistic', label: 'Photorealistic', desc: '写实实拍质感' },
  { value: '2d_animation', label: '2D Animation', desc: '二维动画 / 赛璐璐' },
  { value: '3d_animation', label: '3D Animation', desc: '三维动画 / CGI' },
  { value: 'stylized', label: 'Stylized', desc: '风格化插画' },
  { value: 'custom', label: 'Custom', desc: '自定义（由艺术方向定义）' },
] as const;
const newRenderFamily = ref('');
const newArtDirection = ref('');
const artDirectionPlaceholder = computed(() =>
  newRenderFamily.value === '2d_animation'
    ? 'cinematic Japanese anime, clean cel shading, detailed environment'
    : 'cinematic anime with detailed backgrounds');

// 项目名 → 视觉风格标签缓存（进入项目卡片展示用，懒加载）
const styleLabels = ref<Record<string, string>>({});
function projectStyleLabel(p: string): string {
  return styleLabels.value[p] || '';
}

const showStyle = ref(false);
const styleProjectName = ref('');
const styleLoading = ref(false);
const styleSaving = ref(false);
const styleRenderFamily = ref('');
const styleArtDirection = ref('');
const styleRevision = ref('');
const styleHasApprovedAssets = ref(false);
const styleWasSpecified = ref(false);

async function loadStyleLabels() {
  // 只为未缓存的项目懒加载一次（旧项目显示「风格未设置」）
  for (const p of projects.value) {
    if (styleLabels.value[p] !== undefined) continue;
    try {
      const r = await apiClient.getVisualProfile(p);
      styleLabels.value = { ...styleLabels.value, [p]: r.specified ? familyLabel(r.visual_profile!.render_family) : '' };
    } catch { styleLabels.value = { ...styleLabels.value, [p]: '' }; }
  }
}

function familyLabel(v: string): string {
  return RENDER_FAMILY_OPTIONS.find((o) => o.value === v)?.label || v;
}

function openStyle(p: string) {
  styleProjectName.value = p;
  showStyle.value = true;
  styleLoading.value = true;
  apiClient.getVisualProfile(p).then((r) => {
    styleRenderFamily.value = r.specified ? r.visual_profile!.render_family : '';
    styleArtDirection.value = r.specified ? r.visual_profile!.art_direction : '';
    styleRevision.value = r.revision;
    styleHasApprovedAssets.value = r.has_approved_visual_assets;
    styleWasSpecified.value = r.specified;
  }).catch((e: any) => {
    store.showNotification(`加载视觉风格失败: ${e.message}`, 'error');
    showStyle.value = false;
  }).finally(() => { styleLoading.value = false; });
}

async function confirmStyle() {
  styleSaving.value = true;
  try {
    await apiClient.updateVisualProfile(styleProjectName.value, {
      render_family: styleRenderFamily.value,
      art_direction: styleArtDirection.value.trim(),
      expected_revision: styleRevision.value,
    });
    store.showNotification('视觉风格已更新', 'success');
    styleLabels.value = { ...styleLabels.value, [styleProjectName.value]: familyLabel(styleRenderFamily.value) };
    showStyle.value = false;
  } catch (e: any) {
    store.showNotification(`保存失败: ${e.message}`, 'error');
  } finally {
    styleSaving.value = false;
  }
}

async function loadProjects() {
  try {
    projects.value = await apiClient.listProjects();
    loadStyleLabels();
  }
  catch (e: any) { console.error('[dashboard] 加载项目列表失败', e); }
}

function openAdd() {
  newName.value = '';
  newRenderFamily.value = '';
  newArtDirection.value = '';
  showAdd.value = true;
}

async function enterProject(name: string) {
  await store.loadProject(name);   // 设为当前项目
  router.push('/characters');      // 直接进角色页
}

async function confirmAdd() {
  const name = newName.value.trim();
  if (!name) { store.showNotification('请输入项目名称', 'error'); return; }
  // 前端预检（即时反馈）；后端 409 是权威保障
  if (projects.value.includes(name)) { store.showNotification('项目已存在', 'error'); return; }
  if (!newRenderFamily.value) { store.showNotification('请选择视觉风格', 'error'); return; }
  adding.value = true;
  try {
    await apiClient.createProject(name, newRenderFamily.value, newArtDirection.value.trim());
    await store.loadProject(name);
    router.push('/characters');    // 创建后直接进角色页
  } catch (e: any) {
    store.showNotification(`创建失败: ${e.message}`, 'error');
  } finally {
    adding.value = false;
  }
}

watch(showAdd, (v) => { if (v) nextTick(() => nameInput.value?.focus()); });
onMounted(() => { loadProjects(); });
</script>

<style lang="scss" scoped>
.dashboard {
  max-width: 960px;
  position: relative;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;

  h1 {
    font-size: 20px;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 4px;
  }
}

.subtitle {
  font-size: 13px;
  color: var(--text-muted);
}

.header-actions {
  display: flex;
  gap: 8px;
}

.btn {
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  border: none;

  &:disabled { opacity: 0.5; cursor: not-allowed; }
}

.btn-primary {
  background-color: var(--accent);
  color: var(--accent-text);
  &:hover:not(:disabled) { background-color: var(--accent-hover); }
}

.queue-overview {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 12px 16px;
  background: var(--bg-root);
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 20px;
  font-size: 13px;
  color: var(--text-muted);
  .qo-title { font-weight: 600; color: var(--text-primary); }
  .qo-stat b { color: var(--text-primary); font-weight: 600; }
  .qo-stat.fail b { color: #f87171; }
  .qo-eta { color: var(--text-muted); margin-left: 4px; }
  .qo-conn { margin-left: auto; font-size: 12px; color: #4ade80; }
  .qo-conn.off { color: var(--text-muted); }
}

.proj-section {
  margin-top: 8px;
}

.section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 14px;
}

.proj-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}

.proj-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 18px 16px;
  cursor: pointer;
  transition: all 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: space-between;

  &:hover {
    border-color: var(--accent);
    background: var(--bg-hover);
  }
  &.current {
    border-color: var(--accent);
  }
}

.proj-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.proj-enter {
  font-size: 12px;
  color: var(--text-muted);
}

.proj-card-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.proj-style {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  background: rgba(99, 102, 241, 0.15);
  color: var(--accent);
  cursor: pointer;
  &:hover { filter: brightness(1.2); }
  &.unset {
    background: transparent;
    border: 1px dashed var(--border);
    color: var(--text-muted);
  }
}

// 视觉风格卡片（P1 §18）
.style-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
}

.style-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
  transition: all 0.15s ease;

  &:hover { border-color: var(--accent); }
  &.selected {
    border-color: var(--accent);
    background: rgba(99, 102, 241, 0.1);
  }

  .sc-name { font-size: 12px; font-weight: 600; color: var(--text-primary); }
  .sc-desc { font-size: 11px; color: var(--text-muted); }
}

.warn-box {
  margin-bottom: 16px;
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  background: rgba(248, 113, 113, 0.1);
  border: 1px solid rgba(248, 113, 113, 0.35);
  color: #f87171;

  &:has(+ .warn-box) { margin-bottom: 8px; }
}

.empty-hint {
  padding: 32px;
  background: var(--bg-surface);
  border: 1px dashed var(--border);
  border-radius: 8px;
  text-align: center;
  font-size: 13px;
  color: var(--text-muted);
}

// 对话框
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
  width: 380px;
  max-width: 90vw;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);

  h3 { margin: 0 0 18px; font-size: 16px; font-weight: 600; color: var(--text-primary); }
}

.form-group {
  margin-bottom: 18px;
  label { display: block; font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }
  .input {
    width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 6px;
    font-size: 14px; background: var(--bg-root); color: var(--text-primary); outline: none;
    &:focus { border-color: var(--accent); }
  }
}

.dialog-btns {
  display: flex; gap: 8px; justify-content: flex-end;
}

.btn-cancel {
  background: var(--bg-root); color: var(--text-muted); border: 1px solid var(--border);
  &:hover { background: var(--bg-hover); }
}

.btn-confirm {
  background: var(--accent); color: var(--accent-text);
  &:hover:not(:disabled) { background: var(--accent-hover); }
}
</style>
