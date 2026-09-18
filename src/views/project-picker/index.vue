<template>
  <div class="picker">
    <div class="split">
      <!-- 左侧：黑洞主视觉 -->
      <aside class="hero">
        <BlackHole />
        <div class="hero-shade" aria-hidden="true"></div>
        <div class="hero-content">
          <div class="brand">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="2" y="2" width="20" height="20" rx="2" />
              <line x1="7" y1="2" x2="7" y2="22" /><line x1="17" y1="2" x2="17" y2="22" />
              <line x1="2" y1="12" x2="22" y2="12" />
              <line x1="2" y1="7" x2="7" y2="7" /><line x1="2" y1="17" x2="7" y2="17" />
              <line x1="17" y1="7" x2="22" y2="7" /><line x1="17" y1="17" x2="22" y2="17" />
            </svg>
            <span>StoryBoard</span>
          </div>

          <div class="hero-bottom">
            <span class="en-label">Project Library</span>
            <h1 class="title">选择项目</h1>
            <p class="subtitle">选择一个项目继续创作，或新建一个项目开始</p>
            <div class="queue-strip" v-if="queue.running || queue.video_queued || queue.failed">
              <span class="qs-conn" :class="{ off: !jobConnected }">{{ jobConnected ? '●' : '○' }}</span>
              <span>运行中 <b>{{ queue.running }}</b></span>
              <span class="qs-sep">·</span>
              <span>排队 <b>{{ queue.video_queued }}</b><template v-if="queue.video_queued">（约 {{ Math.round(queue.video_eta_s / 60) }} 分钟）</template></span>
              <template v-if="queue.failed">
                <span class="qs-sep">·</span>
                <span class="qs-fail">失败 <b>{{ queue.failed }}</b></span>
              </template>
            </div>
          </div>
        </div>
      </aside>

      <!-- 右侧：项目列表 -->
      <main class="list-pane">
        <div class="list-inner">
          <div class="command-bar">
            <div class="search">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <input v-model="keyword" class="search-input" placeholder="搜索项目…" />
            </div>
            <div class="filters">
              <button
                v-for="f in filterOptions"
                :key="f.value"
                class="filter-chip"
                :class="{ on: activeFilter === f.value }"
                @click="activeFilter = f.value"
              >{{ f.label }}</button>
            </div>
            <button class="btn btn-primary" @click="openAdd">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              新建项目
            </button>
            <button class="theme-btn" @click="toggleTheme" :title="theme === 'dark' ? '浅色模式' : '深色模式'">
              <svg v-if="theme === 'dark'" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="5" />
                <line x1="12" y1="1" x2="12" y2="3" /><line x1="12" y1="21" x2="12" y2="23" />
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" /><line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                <line x1="1" y1="12" x2="3" y2="12" /><line x1="21" y1="12" x2="23" y2="12" />
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" /><line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
              </svg>
              <svg v-else width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
              </svg>
            </button>
          </div>

          <div v-if="filteredProjects.length" class="grid">
            <article
              v-for="(p, i) in filteredProjects"
              :key="p"
              class="card"
              :class="{ current: p === projectName }"
              :style="{ '--i': i }"
              role="button"
              tabindex="0"
              @click="enterProject(p)"
              @keyup.enter="enterProject(p)"
              @mousemove="onTilt"
              @mouseleave="onTiltReset"
            >
              <div class="thumb">
                <div class="thumb-art" v-html="thumbFor(p)"></div>
                <div class="thumb-sheen"></div>
                <span v-if="p === projectName" class="badge-current">当前项目</span>
                <span class="frame-corner tl"></span>
                <span class="frame-corner tr"></span>
                <span class="frame-corner bl"></span>
                <span class="frame-corner br"></span>
              </div>
              <div class="card-body">
                <div class="card-title-row">
                  <h3 class="card-name" :title="p">{{ p }}</h3>
                  <svg class="card-arrow" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="5" y1="12" x2="19" y2="12" /><polyline points="12 5 19 12 12 19" />
                  </svg>
                </div>
                <p v-if="styleInfo[p]?.desc" class="card-desc" :title="styleInfo[p].desc">{{ styleInfo[p].desc }}</p>
                <div class="card-meta">
                  <span
                    class="chip"
                    :class="{ unset: !styleInfo[p]?.label }"
                    @click.stop="openStyle(p)"
                    @keyup.enter.stop="openStyle(p)"
                  >{{ styleInfo[p]?.label || '未设置风格' }}</span>
                  <span class="card-index">{{ String(i + 1).padStart(2, '0') }}</span>
                </div>
              </div>
            </article>
          </div>
          <div v-else-if="projects.length" class="empty-hint">没有匹配的项目，试试调整搜索或筛选</div>
          <div v-else class="empty-hint">
            <div class="empty-art" v-html="emptyArt"></div>
            <p>还没有项目，点击「新建项目」开始你的第一部作品</p>
          </div>
        </div>
      </main>
    </div>

    <!-- 新建项目对话框 -->
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
            {{ adding ? '创建中…' : '创建并进入' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 视觉风格设置对话框 -->
    <div v-if="showStyle" class="dialog-overlay" @click.self="showStyle = false">
      <div class="dialog">
        <h3>视觉风格 · {{ styleProjectName }}</h3>
        <div v-if="styleLoading" class="loading-hint">加载中…</div>
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
          <div class="warn-box info" v-else-if="!styleWasSpecified">
            ℹ 该项目尚未设置视觉风格。生成视觉素材前必须先设置（P1 fail closed）。
          </div>
        </template>
        <div class="dialog-btns">
          <button class="btn btn-cancel" @click="showStyle = false">取消</button>
          <button class="btn btn-confirm" :disabled="styleSaving || !styleRenderFamily" @click="confirmStyle">
            {{ styleSaving ? '保存中…' : '保存' }}
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
import { useTheme } from '@/composables/useTheme';
import { apiClient } from '@/api';
import BlackHole from '@/components/BlackHole.vue';

const jobStore = useJobStore();
const store = useProjectStore();
const router = useRouter();
const { theme, toggleTheme } = useTheme();
const queue = computed(() => jobStore.queue);
const jobConnected = computed(() => jobStore.connected);
const projectName = computed(() => store.projectName);

const projects = ref<string[]>([]);
const keyword = ref('');
const activeFilter = ref<'all' | 'current' | 'unstyled'>('all');
const filterOptions = [
  { value: 'all', label: '全部' },
  { value: 'current', label: '当前项目' },
  { value: 'unstyled', label: '未设置风格' },
] as const;

const filteredProjects = computed(() => {
  const k = keyword.value.trim().toLowerCase();
  return projects.value.filter((p) => {
    if (k && !p.toLowerCase().includes(k)) return false;
    if (activeFilter.value === 'current') return p === projectName.value;
    if (activeFilter.value === 'unstyled') return !styleInfo.value[p]?.label;
    return true;
  });
});

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

// 项目名 → 风格信息缓存（懒加载）：label = 介质标签，desc = 艺术方向（作为卡片描述）
const styleInfo = ref<Record<string, { label: string; desc: string }>>({});

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
  for (const p of projects.value) {
    if (styleInfo.value[p] !== undefined) continue;
    try {
      const r = await apiClient.getVisualProfile(p);
      styleInfo.value = {
        ...styleInfo.value,
        [p]: r.specified
          ? { label: familyLabel(r.visual_profile!.render_family), desc: r.visual_profile!.art_direction || '' }
          : { label: '', desc: '' },
      };
    } catch {
      styleInfo.value = { ...styleInfo.value, [p]: { label: '', desc: '' } };
    }
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
    styleInfo.value = {
      ...styleInfo.value,
      [styleProjectName.value]: { label: familyLabel(styleRenderFamily.value), desc: styleArtDirection.value.trim() },
    };
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
  catch (e: any) { console.error('[project-picker] 加载项目列表失败', e); }
}

function openAdd() {
  newName.value = '';
  newRenderFamily.value = '';
  newArtDirection.value = '';
  showAdd.value = true;
}

async function enterProject(name: string) {
  await store.loadProject(name);
  router.push({ path: '/resources', query: { tab: 'characters' } });
}

async function confirmAdd() {
  const name = newName.value.trim();
  if (!name) { store.showNotification('请输入项目名称', 'error'); return; }
  if (projects.value.includes(name)) { store.showNotification('项目已存在', 'error'); return; }
  if (!newRenderFamily.value) { store.showNotification('请选择视觉风格', 'error'); return; }
  adding.value = true;
  try {
    await apiClient.createProject(name, newRenderFamily.value, newArtDirection.value.trim());
    await store.loadProject(name);
    router.push({ path: '/resources', query: { tab: 'characters' } });
  } catch (e: any) {
    store.showNotification(`创建失败: ${e.message}`, 'error');
  } finally {
    adding.value = false;
  }
}

// ---- 生成式缩略图：项目名哈希 → 确定性抽象"分镜帧" ----
function hashSeed(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function prng(seed: number) {
  let a = seed;
  return () => {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const thumbCache = new Map<string, string>();

function thumbFor(name: string): string {
  const hit = thumbCache.get(name);
  if (hit) return hit;
  const svg = buildThumb(prng(hashSeed(name)));
  thumbCache.set(name, svg);
  return svg;
}

function buildThumb(rand: () => number): string {
  const type = Math.floor(rand() * 4);
  const line = 'var(--border-hover)';
  const soft = 'var(--border)';
  const hi = 'var(--text-faint)';
  let inner = '';

  if (type === 0) {
    // 胶片条：不等距竖线 + 一个取景框
    const n = 6 + Math.floor(rand() * 4);
    for (let i = 0; i < n; i++) {
      const x = 20 + rand() * 280;
      inner += `<line x1="${x.toFixed(1)}" y1="0" x2="${x.toFixed(1)}" y2="200" stroke="${i % 3 === 0 ? line : soft}" stroke-width="${(0.8 + rand() * 1.6).toFixed(1)}"/>`;
    }
    const fx = 40 + rand() * 140, fw = 70 + rand() * 80, fy = 40 + rand() * 40, fh = 60 + rand() * 50;
    inner += `<rect x="${fx.toFixed(1)}" y="${fy.toFixed(1)}" width="${fw.toFixed(1)}" height="${fh.toFixed(1)}" fill="none" stroke="${hi}" stroke-width="1.2"/>`;
  } else if (type === 1) {
    // 弧场：偏心同心圆
    const cx = 90 + rand() * 140, cy = 70 + rand() * 60;
    for (let r = 24; r <= 160; r += 22 + Math.floor(rand() * 10)) {
      inner += `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${r}" fill="none" stroke="${r > 100 ? soft : line}" stroke-width="1"/>`;
    }
    inner += `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${(8 + rand() * 6).toFixed(1)}" fill="none" stroke="${hi}" stroke-width="1.4"/>`;
  } else if (type === 2) {
    // 帧格：2×3 取景格 + 一格提亮
    const cols = 3, rows = 2, gw = 88, gh = 70, ox = 28, oy = 30;
    const lit = Math.floor(rand() * cols * rows);
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const x = ox + c * (gw + 8), y = oy + r * (gh + 8);
        const isLit = r * cols + c === lit;
        inner += `<rect x="${x}" y="${y}" width="${gw}" height="${gh}" fill="${isLit ? 'var(--bg-active)' : 'none'}" stroke="${isLit ? hi : soft}" stroke-width="1.1"/>`;
      }
    }
  } else {
    // 斜线场：平行斜线 + 准星圆
    for (let i = 0; i < 9; i++) {
      const x = i * 42 - 60 + rand() * 10;
      inner += `<line x1="${x.toFixed(1)}" y1="210" x2="${(x + 120).toFixed(1)}" y2="-10" stroke="${i % 4 === 0 ? line : soft}" stroke-width="1"/>`;
    }
    const cx = 120 + rand() * 120, cy = 60 + rand() * 80, r = 14 + rand() * 16;
    inner += `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${r.toFixed(1)}" fill="none" stroke="${hi}" stroke-width="1.3"/>`;
    inner += `<line x1="${(cx - r - 8).toFixed(1)}" y1="${cy.toFixed(1)}" x2="${(cx + r + 8).toFixed(1)}" y2="${cy.toFixed(1)}" stroke="${hi}" stroke-width="0.8"/>`;
  }

  return `<svg viewBox="0 0 320 200" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg">${inner}</svg>`;
}

const emptyArt = buildThumb(prng(hashSeed('__empty__')));

// ---- 卡片视差倾斜（轻量 3D，禁用动画偏好时自动失效） ----
const tiltOK = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function onTilt(e: MouseEvent) {
  if (!tiltOK) return;
  const el = e.currentTarget as HTMLElement;
  const r = el.getBoundingClientRect();
  const px = (e.clientX - r.left) / r.width - 0.5;
  const py = (e.clientY - r.top) / r.height - 0.5;
  el.style.setProperty('--ry', `${(px * 5).toFixed(2)}deg`);
  el.style.setProperty('--rx', `${(-py * 5).toFixed(2)}deg`);
}

function onTiltReset(e: MouseEvent) {
  const el = e.currentTarget as HTMLElement;
  el.style.setProperty('--rx', '0deg');
  el.style.setProperty('--ry', '0deg');
}

watch(showAdd, (v) => { if (v) nextTick(() => nameInput.value?.focus()); });
onMounted(() => { loadProjects(); });
</script>

<style lang="scss" scoped>
// ========== 设计系统快照（本页规范） ==========
// 间距阶梯：4 / 8 / 12 / 16 / 24 / 40 / 64
// 圆角：chip 10px · 输入/按钮 8px · 卡片 10px · 弹窗 12px
// 动效：入场 500ms stagger 45ms · 悬停 150-400ms cubic-bezier(0.22, 1, 0.36, 1)
// 阴影：仅悬停出现 0 16px 40px rgba(0,0,0,0.35)

$ease-out: cubic-bezier(0.22, 1, 0.36, 1);

.picker {
  height: 100vh;
  background: var(--bg-root);
  overflow: hidden;
}

.split {
  display: flex;
  height: 100vh;
}

// ========== 左侧：黑洞主视觉 ==========
.hero {
  position: relative;
  width: 44%;
  min-width: 420px;
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  overflow: hidden;
}

// 底部渐隐：保证标题在吸积盘亮带之上始终可读
.hero-shade {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 42%;
  z-index: 1;
  pointer-events: none;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.62), rgba(0, 0, 0, 0.18) 55%, transparent);
}

.hero-content {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 28px 40px 44px;
  pointer-events: none;   // 让鼠标视差穿透到画布
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  letter-spacing: 0.02em;
  animation: rise 0.6s $ease-out both;
}

.hero-bottom {
  animation: rise 0.6s $ease-out 0.1s both;
}

.en-label {
  display: block;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--text-faint);
  margin-bottom: 12px;
}

.title {
  font-size: 44px;
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1.1;
  color: var(--text-primary);
  margin-bottom: 12px;
  text-shadow: 0 2px 24px rgba(0, 0, 0, 0.6);
}

.subtitle {
  font-size: 14px;
  color: var(--text-muted);
  text-shadow: 0 1px 12px rgba(0, 0, 0, 0.6);
}

.queue-strip {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 20px;

  b { color: var(--text-primary); font-weight: 600; }
  .qs-sep { color: var(--text-faint); }
  .qs-fail b { color: var(--error); }
  .qs-conn { color: var(--success); }
  .qs-conn.off { color: var(--text-muted); }
}

// ========== 右侧：项目列表 ==========
.list-pane {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
}

.list-inner {
  max-width: 780px;
  margin: 0 auto;
  padding: 0 32px 80px;
}

// 指令条：吸顶，滚动时保持可用
.command-bar {
  position: sticky;
  top: 0;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 0 16px;
  margin-bottom: 16px;
  background: color-mix(in srgb, var(--bg-root) 84%, transparent);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--border);
  animation: rise 0.5s $ease-out 0.05s both;
}

.search {
  flex: 1;
  min-width: 140px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 14px;
  height: 38px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--icon);
  transition: border-color 0.2s $ease-out;

  &:focus-within {
    border-color: var(--border-hover);
  }
}

.search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;

  &::placeholder { color: var(--text-faint); }
}

.filters {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
}

.filter-chip {
  padding: 5px 12px;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s $ease-out;

  &:hover { color: var(--text-secondary); }

  &.on {
    background: var(--bg-active);
    color: var(--text-primary);
    font-weight: 500;
  }
}

.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 16px;
  height: 38px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s $ease-out;
  border: none;
  white-space: nowrap;

  &:disabled { opacity: 0.5; cursor: not-allowed; }
}

.btn-primary {
  background: var(--accent);
  color: var(--accent-text);

  &:hover:not(:disabled) {
    background: var(--accent-hover);
    transform: translateY(-1px);
  }
}

.theme-btn {
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--icon);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s $ease-out;

  &:hover {
    color: var(--text-primary);
    border-color: var(--border-hover);
    transform: rotate(15deg);
  }
}

// ========== 项目网格 ==========
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 14px;
  perspective: 1200px;
}

.card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  cursor: pointer;
  outline: none;
  transform: perspective(1200px) rotateX(var(--rx, 0deg)) rotateY(var(--ry, 0deg)) translateY(var(--ty, 0));
  transition: transform 0.4s $ease-out, border-color 0.2s $ease-out, box-shadow 0.3s $ease-out;
  animation: rise 0.5s $ease-out both;
  animation-delay: calc(0.1s + var(--i) * 0.045s);

  &:hover,
  &:focus-visible {
    --ty: -4px;
    border-color: var(--border-hover);
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.35);
  }

  &.current {
    border-color: var(--border-hover);
  }
}

// 缩略图：生成式分镜帧 + 光泽扫过 + 取景框角标
.thumb {
  position: relative;
  aspect-ratio: 16 / 10;
  background: linear-gradient(160deg, var(--bg-root), var(--bg-surface));
  border-bottom: 1px solid var(--border);
  overflow: hidden;
}

.thumb-art {
  position: absolute;
  inset: 0;
  opacity: 0.85;
  transition: transform 0.6s $ease-out, opacity 0.4s $ease-out;

  :deep(svg) {
    width: 100%;
    height: 100%;
    display: block;
  }

  .card:hover & {
    transform: scale(1.05);
    opacity: 1;
  }
}

.thumb-sheen {
  position: absolute;
  inset: 0;
  background: linear-gradient(115deg, transparent 30%, rgba(255, 255, 255, 0.06) 48%, transparent 62%);
  transform: translateX(-120%);
  transition: transform 0.9s $ease-out;
  pointer-events: none;

  .card:hover & {
    transform: translateX(120%);
  }
}

.badge-current {
  position: absolute;
  top: 10px;
  left: 10px;
  padding: 3px 8px;
  border-radius: 10px;
  background: var(--accent);
  color: var(--accent-text);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

// 取景框四角标记：悬停浮现（呼应分镜安全框）
.frame-corner {
  position: absolute;
  width: 14px;
  height: 14px;
  border: 1.5px solid var(--text-muted);
  opacity: 0;
  transition: opacity 0.25s $ease-out;
  pointer-events: none;

  .card:hover & { opacity: 1; }

  &.tl { top: 8px; left: 8px; border-right: none; border-bottom: none; }
  &.tr { top: 8px; right: 8px; border-left: none; border-bottom: none; }
  &.bl { bottom: 8px; left: 8px; border-right: none; border-top: none; }
  &.br { bottom: 8px; right: 8px; border-left: none; border-top: none; }
}

// 卡片信息区
.card-body {
  padding: 13px 14px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.card-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.card-name {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-arrow {
  flex-shrink: 0;
  color: var(--text-faint);
  transition: all 0.25s $ease-out;

  .card:hover & {
    color: var(--text-primary);
    transform: translateX(3px);
  }
}

.card-desc {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.chip {
  font-size: 11px;
  padding: 3px 9px;
  border-radius: 10px;
  border: 1px solid var(--border);
  color: var(--text-muted);
  cursor: pointer;
  max-width: 70%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: all 0.15s $ease-out;

  &:hover {
    border-color: var(--border-hover);
    color: var(--text-primary);
  }

  &.unset {
    border-style: dashed;
    color: var(--text-faint);
  }
}

.card-index {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.08em;
}

// ========== 空状态 ==========
.empty-hint {
  padding: 48px 32px;
  border: 1px dashed var(--border);
  border-radius: 10px;
  text-align: center;
  font-size: 13px;
  color: var(--text-muted);
  animation: rise 0.5s $ease-out 0.12s both;

  p { margin-top: 16px; }
}

.empty-art {
  width: 200px;
  margin: 0 auto;
  opacity: 0.6;

  :deep(svg) {
    width: 100%;
    height: auto;
    display: block;
  }
}

// ========== 对话框 ==========
.dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(4px);
  animation: fade 0.2s $ease-out both;
}

.dialog {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px;
  width: 400px;
  max-width: 90vw;
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
  animation: rise 0.3s $ease-out both;

  h3 {
    margin: 0 0 18px;
    font-size: 15px;
    font-weight: 600;
    color: var(--text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.form-group {
  margin-bottom: 18px;

  label {
    display: block;
    font-size: 12px;
    font-weight: 500;
    color: var(--text-muted);
    margin-bottom: 8px;
  }

  .input {
    width: 100%;
    padding: 9px 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 13px;
    background: var(--bg-root);
    color: var(--text-primary);
    outline: none;
    transition: border-color 0.15s $ease-out;

    &:focus { border-color: var(--border-hover); }
    &::placeholder { color: var(--text-faint); }
  }
}

.style-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
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
  transition: all 0.15s $ease-out;

  &:hover { border-color: var(--border-hover); }
  &.selected {
    border-color: var(--accent);
    background: var(--bg-hover);
  }

  .sc-name { font-size: 12px; font-weight: 600; color: var(--text-primary); }
  .sc-desc { font-size: 11px; color: var(--text-muted); line-height: 1.4; }
}

.warn-box {
  margin-bottom: 16px;
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: var(--error);

  &.info {
    background: var(--bg-hover);
    border-color: var(--border);
    color: var(--text-muted);
  }
}

.loading-hint {
  padding: 24px 0;
  text-align: center;
  font-size: 13px;
  color: var(--text-muted);
}

.dialog-btns {
  display: flex;
  gap: 8px;
  justify-content: flex-end;

  .btn {
    height: 34px;
    padding: 0 14px;
  }
}

.btn-cancel {
  background: transparent;
  color: var(--text-muted);
  border: 1px solid var(--border);
  &:hover { background: var(--bg-hover); color: var(--text-secondary); }
}

.btn-confirm {
  background: var(--accent);
  color: var(--accent-text);
  &:hover:not(:disabled) { background: var(--accent-hover); }
}

// ========== 动效 ==========
@keyframes rise {
  from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes fade {
  from { opacity: 0; }
  to { opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}

// 窄屏：上下堆叠，黑洞降为顶部横幅
@media (max-width: 900px) {
  .picker { overflow-y: auto; }
  .split { flex-direction: column; height: auto; min-height: 100vh; }
  .hero {
    width: 100%;
    min-width: 0;
    height: 44vh;
    min-height: 320px;
    border-right: none;
    border-bottom: 1px solid var(--border);
  }
  .hero-content { padding: 20px 24px 28px; }
  .title { font-size: 32px; }
  .list-pane { overflow-y: visible; }
  .list-inner { padding: 0 20px 64px; }
  .command-bar { flex-wrap: wrap; }
  .search { flex: 1 1 100%; }
}
</style>
