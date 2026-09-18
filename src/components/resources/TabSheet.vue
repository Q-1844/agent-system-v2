<template>
  <div class="tab-sheet">
    <div class="action-bar">
      <button class="btn btn-primary" :disabled="loading" @click="run">
        {{ loading ? '生成中...' : '一键设定图生成三视图' }}
      </button>
    </div>

    <div class="steps" v-if="steps.length">
      <span v-for="(s, i) in steps" :key="i" class="step" :class="s.status">
        {{ s.action }} {{ s.status === 'done' ? '✓' : s.status === 'failed' ? '✗' : '' }}
      </span>
    </div>

    <!-- 设定图原图 + 裁剪区 -->
    <div v-if="sheetRaw" class="section">
      <div class="section-header">
        <h4>设定图原图</h4>
        <div class="mode-switch">
          <button class="btn btn-sm" :class="{ active: mode === 'auto' }" @click="mode = 'auto'">自动裁剪</button>
          <button class="btn btn-sm" :class="{ active: mode === 'manual' }" @click="mode = 'manual'">手动裁剪</button>
        </div>
      </div>

      <!-- 自动裁剪结果 -->
      <div v-if="mode === 'auto' && hasCrops" class="img-row">
        <div class="img-box" v-for="v in viewKeys" :key="v">
          <div class="img-label">{{ v }}</div>
          <img v-if="crops[v]" :src="'data:image/png;base64,' + crops[v]" />
          <div v-else class="ph">-</div>
        </div>
      </div>

      <!-- 手动裁剪模式 -->
      <div v-if="mode === 'manual'" class="crop-area">
        <div class="crop-toolbar">
          <button class="btn btn-sm btn-primary" @click="startCrop">+ 添加裁剪框</button>
          <button class="btn btn-sm btn-outline" @click="clearCrops" :disabled="!cropRects.length">清空框选</button>
          <button class="btn btn-sm btn-primary" @click="applyCrops" :disabled="!cropRects.length">裁剪并抠图</button>
          <span class="crop-hint" v-if="cropRects.length">已框选 {{ cropRects.length }} 个区域</span>
        </div>
        <div class="crop-canvas-wrap" ref="canvasWrap">
          <img :src="'data:image/png;base64,' + sheetRaw" ref="baseImg" @load="onImgLoad" class="crop-base-img" />
          <canvas ref="cropCanvas" class="crop-canvas" @mousedown="onMouseDown" @mousemove="onMouseMove" @mouseup="onMouseUp"></canvas>
          <div v-for="(r, i) in cropRects" :key="i" class="crop-rect" :style="rectStyle(r)">
            <span class="crop-rect-label">{{ i + 1 }}</span>
            <button class="crop-rect-del" @click="removeCrop(i)">×</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 手动裁剪结果 -->
    <div v-if="manualCrops.length" class="section">
      <h4>手动裁剪结果</h4>
      <div class="img-row">
        <div class="img-box" v-for="(c, i) in manualCrops" :key="i">
          <div class="img-label">区域 {{ i + 1 }}</div>
          <img :src="'data:image/png;base64,' + c.raw" />
          <div class="img-actions">
            <button class="btn btn-sm" :disabled="c.busy" @click="segmentManual(i)">
              {{ c.busy ? '抠图中...' : '抠图' }}
            </button>
          </div>
          <div v-if="c.seg" class="checker">
            <img :src="'data:image/png;base64,' + c.seg" />
          </div>
        </div>
      </div>
    </div>

    <!-- 自动抠图结果 -->
    <div v-if="hasSeg" class="section">
      <h4>抠图结果（透明）</h4>
      <div class="img-row">
        <div class="img-box" v-for="v in viewKeys" :key="v">
          <div class="img-label">{{ v }}</div>
          <div class="checker">
            <img v-if="seg[v]" :src="'data:image/png;base64,' + seg[v]" />
            <div v-else class="ph">-</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, nextTick, onMounted, watch } from 'vue';
import { apiClient } from '@/api';
import { useProjectStore } from '@/store';

const props = defineProps<{ char: any }>();
const store = useProjectStore();
const viewKeys = ['front', 'side', 'back'];
const loading = ref(false);
const mode = ref<'auto' | 'manual'>('auto');
const steps = ref<any[]>([]);
const sheetRaw = ref('');
const crops = reactive<Record<string, string>>({});
const seg = reactive<Record<string, string>>({});
const hasCrops = computed(() => Object.keys(crops).length > 0);
const hasSeg = computed(() => Object.keys(seg).length > 0);

// 手动裁剪状态
const cropCanvas = ref<HTMLCanvasElement | null>(null);
const canvasWrap = ref<HTMLElement | null>(null);
const baseImg = ref<HTMLImageElement | null>(null);
const cropRects = ref<Array<{ x: number; y: number; w: number; h: number }>>([]);
const manualCrops = ref<Array<{ raw: string; seg: string | null; busy: boolean }>>([]);
let drawing = false;
let startX = 0, startY = 0;
let imgScale = 1;

// 从缓存恢复状态
function restoreFromCache() {
  const cache = store.getCharacterCache(props.char.name);
  if (cache) {
    if (cache.views.front?.raw) crops.front = cache.views.front.raw;
    if (cache.views.front?.seg) seg.front = cache.views.front.seg;
    if (cache.views.side?.raw) crops.side = cache.views.side.raw;
    if (cache.views.side?.seg) seg.side = cache.views.side.seg;
    if (cache.views.back?.raw) crops.back = cache.views.back.raw;
    if (cache.views.back?.seg) seg.back = cache.views.back.seg;
  }

  // 恢复步骤状态
  const genState = store.getGeneratingState(props.char.name);
  if (genState?.steps?.length) {
    steps.value = genState.steps;
  }
}

async function run() {
  loading.value = true;
  steps.value = [];
  sheetRaw.value = '';
  const proj = store.projectName;   // 捕获：生成开始时的项目
  store.updateGeneratingState(props.char.name, { steps: [] });

  try {
    const r = await apiClient.generateCharacterSheet({ description: props.char.description, style: 'anime' });
    const views: any = {};
    for (const k of ['front', 'side', 'back']) {
      views[k] = { raw: r.crops_raw?.[k], seg: r.seg_views?.[k] || undefined };
    }
    // auto-bind 到捕获项目（始终写回原项目，GPT §6/§7）
    if (r.success) {
      try { await apiClient.bindCharacterToProject(proj, { name: props.char.name, description: props.char.description, views }); }
      catch (e: any) { console.error('auto-bind 失败', e); }
    }
    // 仅当仍在原项目时更新当前项目 UI/缓存（GPT §7/§8）
    if (proj !== store.projectName) return;
    steps.value = r.steps || [];
    store.updateGeneratingState(props.char.name, { steps: r.steps || [] });

    if (r.sheet_raw) sheetRaw.value = r.sheet_raw;
    if (r.crops_raw) Object.entries(r.crops_raw).forEach(([k, v]) => { crops[k] = v as string; });
    if (r.seg_views) Object.entries(r.seg_views).forEach(([k, v]) => { if (v) seg[k] = v as string; });

    if (r.success) {
      store.showNotification('设定图生成完成', 'success');
      store.cacheCharacterState(props.char.name, props.char.description, views);
    } else {
      store.showNotification(`部分失败: ${r.error}`, 'error');
    }
  } catch (e: any) {
    if (proj === store.projectName) store.showNotification(`失败: ${e.message}`, 'error');
  } finally {
    if (proj === store.projectName) loading.value = false;
  }
}

function onImgLoad() {
  if (!baseImg.value || !cropCanvas.value) return;
  const img = baseImg.value;
  const canvas = cropCanvas.value;
  canvas.width = img.clientWidth;
  canvas.height = img.clientHeight;
  imgScale = img.naturalWidth / img.clientWidth;
}

function startCrop() {
  drawing = false;
  store.showNotification('在图片上拖拽框选区域', 'info');
}

function onMouseDown(e: MouseEvent) {
  if (mode.value !== 'manual') return;
  const rect = cropCanvas.value!.getBoundingClientRect();
  startX = e.clientX - rect.left;
  startY = e.clientY - rect.top;
  drawing = true;
}

function onMouseMove(e: MouseEvent) {
  if (!drawing || !cropCanvas.value) return;
  const ctx = cropCanvas.value.getContext('2d')!;
  const rect = cropCanvas.value.getBoundingClientRect();
  const curX = e.clientX - rect.left;
  const curY = e.clientY - rect.top;
  ctx.clearRect(0, 0, cropCanvas.value.width, cropCanvas.value.height);
  ctx.strokeStyle = '#6c63ff';
  ctx.lineWidth = 2;
  ctx.setLineDash([5, 5]);
  ctx.strokeRect(startX, startY, curX - startX, curY - startY);
}

function onMouseUp(e: MouseEvent) {
  if (!drawing || !cropCanvas.value) return;
  drawing = false;
  const rect = cropCanvas.value.getBoundingClientRect();
  const endX = e.clientX - rect.left;
  const endY = e.clientY - rect.top;
  const w = Math.abs(endX - startX);
  const h = Math.abs(endY - startY);
  if (w < 20 || h < 20) return;
  cropRects.value.push({
    x: Math.min(startX, endX) * imgScale,
    y: Math.min(startY, endY) * imgScale,
    w: w * imgScale,
    h: h * imgScale,
  });
  const ctx = cropCanvas.value.getContext('2d')!;
  ctx.clearRect(0, 0, cropCanvas.value.width, cropCanvas.value.height);
}

function removeCrop(i: number) { cropRects.value.splice(i, 1); }
function clearCrops() { cropRects.value = []; manualCrops.value = []; }

function rectStyle(r: { x: number; y: number; w: number; h: number }) {
  return {
    left: (r.x / imgScale) + 'px',
    top: (r.y / imgScale) + 'px',
    width: (r.w / imgScale) + 'px',
    height: (r.h / imgScale) + 'px'
  };
}

async function applyCrops() {
  if (!sheetRaw.value || !cropRects.value.length) return;
  const img = new Image();
  img.src = 'data:image/png;base64,' + sheetRaw.value;
  await new Promise(resolve => { img.onload = resolve; });
  manualCrops.value = [];
  for (const r of cropRects.value) {
    const canvas = document.createElement('canvas');
    canvas.width = r.w;
    canvas.height = r.h;
    const ctx = canvas.getContext('2d')!;
    ctx.drawImage(img, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h);
    const rawB64 = canvas.toDataURL('image/png').split(',')[1];
    manualCrops.value.push({ raw: rawB64, seg: null, busy: false });
  }
  store.showNotification(`已裁剪 ${cropRects.value.length} 个区域`, 'success');
}

async function segmentManual(i: number) {
  const c = manualCrops.value[i];
  if (!c) return;
  c.busy = true;
  try {
    const r = await apiClient.segmentImage(c.raw);
    if (r.success) c.seg = r.seg_base64;
  } catch (e: any) {
    store.showNotification(`抠图失败: ${e.message}`, 'error');
  } finally {
    c.busy = false;
  }
}

onMounted(() => {
  restoreFromCache();
});

watch(() => props.char.name, () => {
  loading.value = false;
  steps.value = [];
  sheetRaw.value = '';
  Object.keys(crops).forEach(k => { delete crops[k]; });
  Object.keys(seg).forEach(k => { delete seg[k]; });
  cropRects.value = [];
  manualCrops.value = [];
  restoreFromCache();
});
</script>

<style lang="scss" scoped>
.action-bar {
  margin-bottom: 16px;
}

.btn {
  padding: 8px 20px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: none;
  transition: all 0.2s;

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}

.btn-primary {
  background: var(--accent);
  color: #fff;

  &:hover:not(:disabled) {
    filter: brightness(1.1);
  }
}

.btn-outline {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-muted);

  &:hover:not(:disabled) {
    border-color: var(--accent);
    color: var(--accent);
  }
}

.btn-sm {
  padding: 6px 14px;
  font-size: 12px;

  &.active {
    background: var(--accent);
    color: #fff;
  }
}

.steps {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.step {
  padding: 5px 10px;
  border-radius: 6px;
  font-size: 11px;
  background: var(--bg-hover);
  color: var(--text-muted);

  &.done {
    background: #14532d;
    color: #4ade80;
  }

  &.failed {
    background: #7f1d1d;
    color: #f87171;
  }
}

.section {
  margin-bottom: 20px;

  h4 {
    font-size: 14px;
    color: var(--text-primary);
    margin-bottom: 10px;
  }
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;

  h4 {
    margin: 0;
  }
}

.mode-switch {
  display: flex;
  gap: 4px;
}

.img-row {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}

.img-box {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}

.img-label {
  padding: 8px 10px;
  font-size: 11px;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  text-transform: capitalize;
}

.img-box img {
  width: 100%;
  display: block;
}

.checker {
  background: repeating-conic-gradient(#222 0% 25%, #2a2a2a 0% 50%) 50% / 16px 16px;

  img {
    width: 100%;
    display: block;
  }
}

.ph {
  padding: 40px;
  text-align: center;
  color: var(--text-faint);
  font-size: 12px;
}

.img-actions {
  padding: 8px;
  border-top: 1px solid var(--border);
}

.crop-area {
  margin-top: 12px;
}

.crop-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.crop-hint {
  font-size: 12px;
  color: var(--text-muted);
}

.crop-canvas-wrap {
  position: relative;
  display: inline-block;
  max-width: 100%;
}

.crop-base-img {
  max-width: 100%;
  display: block;
  border-radius: 8px;
  border: 1px solid var(--border);
}

.crop-canvas {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  cursor: crosshair;
}

.crop-rect {
  position: absolute;
  border: 2px solid var(--accent);
  background: rgba(108, 99, 255, 0.15);
  pointer-events: none;
}

.crop-rect-label {
  position: absolute;
  top: 2px;
  left: 6px;
  font-size: 11px;
  color: #fff;
  background: var(--accent);
  padding: 1px 6px;
  border-radius: 4px;
}

.crop-rect-del {
  position: absolute;
  top: 2px;
  right: 4px;
  font-size: 14px;
  color: #fff;
  background: #ef4444;
  border: none;
  border-radius: 50%;
  width: 20px;
  height: 20px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: auto;
}
</style>
