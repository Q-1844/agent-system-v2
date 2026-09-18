<template>
  <div class="seq-page">
    <!-- 无当前项目 -->
    <div v-if="!projectName" class="empty">
      请先选择项目
      <button class="go-btn" @click="goDashboard">前往项目页</button>
    </div>

    <div v-else class="seq-layout">
      <button v-if="railCollapsed" class="expand-fab" @click="railCollapsed = false" title="展开侧栏">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6" /></svg>
        <span>合成</span>
      </button>

      <!-- ============ 左栏：合成列表 + 素材池 ============ -->
      <aside class="rail" :class="{ collapsed: railCollapsed }">
        <div class="rail-sec">
          <div class="rail-head">
            <span class="rail-title">合成</span>
            <button class="icon-btn" title="收起侧栏" @click="railCollapsed = true">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6" /></svg>
            </button>
          </div>
          <div class="rail-scroll">
            <div v-for="s in sequences" :key="s.sequence_id" class="seq-item"
                 :class="{ active: selectedId === s.sequence_id }" @click="selectSequence(s.sequence_id)">
              <div class="seq-main">
                <div class="seq-title">{{ s.title || s.sequence_id }}</div>
                <div class="seq-sub">
                  <span>{{ s.clip_count }} 段 · {{ s.render_count }} 版</span>
                  <span v-if="s.final_render" class="mini-badge">已出片</span>
                </div>
              </div>
            </div>
            <div v-if="!sequences.length" class="empty-mini">还没有合成——点击下方新建，把已完成镜头按顺序拼成完整影片。</div>
          </div>
          <div class="rail-foot">
            <button class="new-btn primary" :disabled="!doneShots.length"
                    :title="doneShots.length ? '以全部已完成视频的镜头新建合成' : '请先在分镜页完成至少一个镜头的视频'"
                    @click="createFromAll">新建合成</button>
          </div>
        </div>

        <div class="rail-sec pool-sec">
          <div class="rail-head">
            <span class="rail-title">素材池</span>
            <span class="rail-hint">点击追加 · 可拖入时间线</span>
          </div>
          <div class="rail-scroll">
            <div v-for="p in pool" :key="p.shot_id" class="pool-item" draggable="true"
                 :class="{ used: usedCount(p.shot_id) > 0 }"
                 :title="p.description || '未填写画面'"
                 @dragstart="onPoolDragStart($event, p.shot_id)" @dragend="resetDrag"
                 @click="addClip(p.shot_id)">
              <img v-if="p.thumbUrl" :src="p.thumbUrl" class="pool-thumb" />
              <div v-else class="pool-thumb pool-thumb-empty"></div>
              <div class="pool-main">
                <div class="pool-id mono">{{ p.shot_id }}</div>
                <div class="pool-dur">{{ p.durationMs != null ? fmtDur(p.durationMs) : '时长未知' }}</div>
              </div>
              <span v-if="usedCount(p.shot_id)" class="pool-used">×{{ usedCount(p.shot_id) }}</span>
              <span class="pool-add">＋</span>
            </div>
            <div v-if="!pool.length" class="empty-mini">还没有已完成视频的镜头——先去分镜页生成。</div>
          </div>
        </div>
      </aside>

      <!-- ============ 右侧：编辑舞台 ============ -->
      <section class="stage-col">
        <div v-if="detail" class="stage-inner">
          <!-- 头部：标题 + 操作 -->
          <header class="stage-head">
            <input v-model="editTitle" class="title-input" placeholder="未命名合成" @input="markDirty" />
            <span class="muted mono">{{ detail.sequence_id }}</span>
            <div class="head-actions">
              <button class="act-btn" :disabled="!dirty || saving" @click="save">{{ saving ? '保存中…' : '保存' }}</button>
              <button class="act-btn primary" :disabled="!editClips.length || rendering" @click="doRender">
                {{ rendering ? '渲染中…' : dirty ? '保存并渲染' : '渲染出片' }}
              </button>
              <div class="menu-wrap">
                <button class="icon-btn more-btn" title="更多" @click.stop="menuOpen = !menuOpen">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></svg>
                </button>
                <div v-if="menuOpen" class="menu-pop" @click.stop>
                  <button class="menu-item danger" @click="removeSequence">删除合成</button>
                </div>
              </div>
            </div>
          </header>

          <!-- 预览舞台 -->
          <div class="stage">
            <div class="player-wrap">
              <video v-if="playerSrc" :key="playerSrc" :src="playerSrc" controls preload="metadata" class="player" />
              <div v-else class="player player-empty">
                <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <circle cx="12" cy="12" r="10" /><polygon points="10 8 16 12 10 16 10 8" fill="currentColor" stroke="none" />
                </svg>
                <p>渲染出片后，成片在这里预览</p>
              </div>
              <div v-if="playerSrc && previewShotId" class="player-badge">
                片段预览 · {{ previewShotId }}
                <button class="badge-btn" @click="backToFinal">返回成片</button>
              </div>
              <div v-else-if="playerSrc && finalVersion" class="player-badge">
                正式成片 · <span class="mono">{{ finalVersion.version_id }}</span>
              </div>
            </div>
            <div class="stage-meta">
              <span>{{ editClips.length }} 段</span>
              <span class="sep-dot"></span>
              <span>计划 ≈ {{ fmtDur(plannedMs) }}</span>
              <template v-if="finalVersion">
                <span class="sep-dot"></span>
                <span>成片实测 {{ fmtDur(finalVersion.duration_ms) }}</span>
              </template>
              <template v-if="freshText">
                <span class="sep-dot"></span>
                <span class="fresh" :class="{ stale: freshness === 'stale' }">{{ freshText }}</span>
              </template>
            </div>
          </div>

          <!-- 时间线 -->
          <div class="timeline">
            <div class="tl-head">
              <span class="tl-title">时间线</span>
              <span class="tl-sub">顺序即播放顺序 · 硬切拼接</span>
              <span class="spacer"></span>
              <span v-if="dirty" class="tl-dirty">有未保存修改</span>
            </div>
            <div class="tl-strip" @dragover.prevent @drop.prevent="onDropEnd">
              <div v-for="(c, i) in editClips" :key="c.shot_id + '-' + i" class="tl-clip"
                   :class="{ sel: selectedClip === i, drop: dragOverIndex === i }"
                   draggable="true"
                   @dragstart="onClipDragStart($event, i)" @dragover.prevent="dragOverIndex = i"
                   @drop.prevent="onDropAt(i)" @dragend="resetDrag"
                   @click="selectClip(i)">
                <div class="tl-thumb">
                  <img v-if="thumbUrl(c.shot_id)" :src="thumbUrl(c.shot_id)" />
                  <span v-else class="tl-thumb-empty"></span>
                  <span class="tl-idx">{{ i + 1 }}</span>
                  <span class="tl-dur mono">{{ clipEffDur(c) }}</span>
                </div>
                <div class="tl-meta">
                  <span class="mono tl-shot">{{ c.shot_id }}</span>
                  <span class="tl-ops" @click.stop>
                    <button class="icon-btn" :disabled="i === 0" title="前移" @click="moveClip(i, -1)">
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="15 18 9 12 15 6" /></svg>
                    </button>
                    <button class="icon-btn" :disabled="i === editClips.length - 1" title="后移" @click="moveClip(i, 1)">
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="9 18 15 12 9 6" /></svg>
                    </button>
                    <button class="icon-btn" title="移除" @click="removeClip(i)">
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
                    </button>
                  </span>
                </div>
              </div>

              <div class="menu-wrap tl-add-wrap">
                <button class="tl-add" title="追加镜头片段" @click.stop="addOpen = !addOpen">＋</button>
                <div v-if="addOpen" class="menu-pop add-pop" @click.stop>
                  <button v-for="pt in pool" :key="pt.shot_id" class="menu-item pool-row" @click="addClip(pt.shot_id); addOpen = false">
                    <img v-if="pt.thumbUrl" :src="pt.thumbUrl" class="pool-thumb sm" />
                    <span class="mono">{{ pt.shot_id }}</span>
                    <span class="pool-used" v-if="usedCount(pt.shot_id)">已用 ×{{ usedCount(pt.shot_id) }}</span>
                  </button>
                  <div v-if="!pool.length" class="menu-empty">没有已完成视频的镜头</div>
                </div>
              </div>
            </div>

            <!-- 修剪条（选中片段） -->
            <div v-if="selectedRow" class="tl-inspector">
              <span class="mono insp-id">{{ selectedRow.shot_id }}</span>
              <label class="trim">入 <input type="number" v-model.number="selectedRow.trim_in_ms" min="0" step="100" @change="markDirty" /> ms</label>
              <label class="trim">出 <input type="number" v-model.number="selectedRow.trim_out_ms" min="0" step="100" @change="markDirty" /> ms</label>
              <span class="insp-eff">有效 ≈ {{ clipEffDur(selectedRow) }}<template v-if="shotDurationMs(selectedRow.shot_id) != null"> / 源 {{ fmtDur(shotDurationMs(selectedRow.shot_id)) }}</template></span>
              <span class="spacer"></span>
              <button class="link-btn danger" @click="removeClip(selectedClip!)">删除该段</button>
            </div>
          </div>

          <!-- 渲染版本轨 -->
          <div class="renders">
            <div class="rd-head">
              <span class="tl-title">渲染版本</span>
              <span class="tl-sub">显式渲染 · 生成绝不自动覆盖正式结果</span>
            </div>
            <div class="rd-strip">
              <div v-for="rv in sortedRenders" :key="rv.version_id" class="rd-card"
                   :class="{ final: detail.final_render === rv.version_id }">
                <div class="rd-row1">
                  <span class="r-status" :class="rv.status">{{ statusText(rv.status) }}</span>
                  <span class="mono r-id">{{ rv.version_id }}</span>
                  <span v-if="detail.final_render === rv.version_id" class="mini-badge">正式</span>
                </div>
                <div class="rd-row2">
                  <span>{{ fmtTime(rv.created_at) }}</span>
                  <span v-if="rv.duration_ms != null">{{ fmtDur(rv.duration_ms) }}</span>
                  <button v-if="rv.status === 'done' && detail.final_render !== rv.version_id" class="link-btn"
                          @click="selectRender(rv.version_id)">选为正式</button>
                </div>
                <div v-if="rv.error" class="r-error">{{ rv.error }}</div>
              </div>
              <div v-if="!sortedRenders.length" class="empty-mini">还没有渲染版本——点「渲染出片」生成第一版。</div>
            </div>
          </div>
        </div>

        <div v-else class="stage stage-solo">
          <div class="player player-empty">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <rect x="2" y="4" width="20" height="16" rx="2" /><polygon points="10 9 15 12 10 15 10 9" fill="currentColor" stroke="none" />
            </svg>
            <p>{{ sequences.length ? '从左侧选择一个合成' : '新建一个合成，开始拼片' }}</p>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useRouter } from 'vue-router';
import { apiClient } from '@/api';
import { useProjectStore } from '@/store';
import { useTopbarStatus } from '@/store/topbarStatus';
import { getVideoUrl, getImageUrl } from '@/utils/media';

interface SeqClip { shot_id: string; trim_in_ms: number; trim_out_ms: number; }
interface SeqListItem {
  sequence_id: string; title: string; scene_id: string | null;
  revision: string; clip_count: number; render_count: number; final_render: string | null;
}
interface PoolShot {
  shot_id: string; description: string; thumbUrl: string; videoUrl: string; durationMs: number | null;
}

const router = useRouter();
const projStore = useProjectStore();
const projectName = computed(() => projStore.projectName);

const sequences = ref<SeqListItem[]>([]);
const selectedId = ref<string | null>(null);
const detail = ref<any | null>(null);
const railCollapsed = ref(false);
const menuOpen = ref(false);
const addOpen = ref(false);
const saving = ref(false);
const rendering = ref(false);
const freshness = ref('');

const shotMap = ref<Record<string, PoolShot>>({});
const doneShots = computed(() => Object.values(shotMap.value).filter(s => s.videoUrl));

const editTitle = ref('');
const editClips = ref<SeqClip[]>([]);
const selectedClip = ref<number | null>(null);
let savedSnapshot = '';
const dirty = ref(false);

// 预览：null = 正式成片；数字 = 时间线上第 n 段（源片预览）
const previewClipIndex = ref<number | null>(null);

const dragIndex = ref<number | null>(null);       // 正在拖动的时间线片段
const poolDragShot = ref<string | null>(null);    // 正在拖动的素材池镜头
const dragOverIndex = ref<number | null>(null);

function snapshot(): string { return JSON.stringify({ t: editTitle.value, c: editClips.value }); }
function markDirty() { dirty.value = snapshot() !== savedSnapshot; }

const sortedRenders = computed(() =>
  [...(detail.value?.render_versions || [])].sort((a, b) => (b.created_at || 0) - (a.created_at || 0)));
const finalVersion = computed(() =>
  (detail.value?.render_versions || []).find((r: any) => r.version_id === detail.value?.final_render && r.status === 'done') || null);
const selectedRow = computed(() =>
  selectedClip.value != null ? editClips.value[selectedClip.value] || null : null);

const previewShotId = computed(() =>
  previewClipIndex.value != null ? editClips.value[previewClipIndex.value]?.shot_id || null : null);
const playerSrc = computed(() => {
  if (previewShotId.value) return shotMap.value[previewShotId.value]?.videoUrl || '';
  return finalVersion.value ? videoUrl(finalVersion.value.output_file) : '';
});

const plannedMs = computed(() => editClips.value.reduce((n, c) => {
  const src = shotDurationMs(c.shot_id);
  return n + (src != null ? Math.max(0, src - c.trim_in_ms - c.trim_out_ms) : 0);
}, 0));

const freshText = computed(() => {
  if (freshness.value === 'current') return '与当前分镜一致';
  if (freshness.value === 'stale') return '分镜已变更，建议重新渲染';
  return '';
});

// 顶栏状态：渲染中 / 有未保存修改 / 已保存
useTopbarStatus('sequences', () => {
  const busy = (detail.value?.render_versions || [])
    .some((r: any) => r.status === 'queued' || r.status === 'rendering');
  if (busy) return { text: '影片合成渲染中…', tone: 'busy' as const };
  if (dirty.value) return { text: '合成有未保存修改', tone: 'busy' as const };
  return { text: '已保存', tone: 'idle' as const };
});

function goDashboard() { router.push('/dashboard'); }
function videoUrl(p: string): string { return p ? getVideoUrl(p) : ''; }
function thumbUrl(id: string): string { return shotMap.value[id]?.thumbUrl || ''; }
function shotDurationMs(id: string): number | null { return shotMap.value[id]?.durationMs ?? null; }
function usedCount(id: string): number { return editClips.value.filter(c => c.shot_id === id).length; }
function clipEffDur(c: SeqClip): string {
  const src = shotDurationMs(c.shot_id);
  if (src == null) return '—';
  return fmtDur(Math.max(0, src - c.trim_in_ms - c.trim_out_ms));
}
function fmtDur(ms: number | null | undefined): string {
  if (ms == null) return '—';
  const s = ms / 1000;
  return s >= 60 ? `${Math.floor(s / 60)}m${(s % 60).toFixed(1)}s` : `${s.toFixed(1)}s`;
}
function fmtTime(ts: number): string {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
function statusText(s: string): string {
  if (s === 'done') return '完成';
  if (s === 'failed') return '失败';
  if (s === 'rendering') return '渲染中';
  if (s === 'queued') return '排队中';
  if (s === 'raced_superseded') return '已被取代';
  return s;
}

// 素材池：全部已完成视频的镜头（已在时间线中的会标 ×n，可重复使用）
const pool = computed<PoolShot[]>(() => doneShots.value);

async function loadShots() {
  if (!projectName.value) { shotMap.value = {}; return; }
  try {
    const data = await apiClient.getProjectShots(projectName.value);
    const map: Record<string, PoolShot> = {};
    for (const sc of data.scenes || []) {
      for (const sh of sc.shots || []) {
        const vid = sh.final_video;
        const v = vid ? (sh.video_versions || []).find(x => x.version_id === vid) : null;
        const done = !!(sh.video?.status === 'done' && sh.video?.local);
        let thumbUrl = '';
        const cid = sh.selected_first_frame;
        if (cid) {
          for (const batch of sh.first_frame_batches || []) {
            const cand = (batch.candidates || []).find(x => x.candidate_id === cid);
            if (cand?.local) { thumbUrl = getImageUrl(cand.local); break; }
          }
        }
        map[sh.shot_id] = {
          shot_id: sh.shot_id,
          description: sh.description || '',
          thumbUrl,
          videoUrl: done && v?.local ? getVideoUrl(v.local) : '',
          durationMs: v?.seconds_returned != null ? Math.round(v.seconds_returned * 1000) : null,
        };
      }
    }
    shotMap.value = map;
  } catch { shotMap.value = {}; }
}

async function loadList(keepSelection = true) {
  if (!projectName.value) { sequences.value = []; return; }
  try {
    const data = await apiClient.listSequences(projectName.value);
    sequences.value = data.sequences || [];
    if (!keepSelection || !sequences.value.some(s => s.sequence_id === selectedId.value)) {
      selectedId.value = sequences.value[0]?.sequence_id || null;
    }
  } catch (e) {
    console.error('[Sequences] 列表加载失败', e);
    sequences.value = [];
  }
}

async function loadDetail() {
  if (!projectName.value || !selectedId.value) { detail.value = null; return; }
  try {
    const data = await apiClient.getSequence(projectName.value, selectedId.value);
    detail.value = data.sequence;
    editTitle.value = detail.value.title || '';
    editClips.value = (detail.value.clips || []).map((c: any) => ({
      shot_id: c.shot_id, trim_in_ms: c.trim_in_ms || 0, trim_out_ms: c.trim_out_ms || 0,
    }));
    if (selectedClip.value != null && selectedClip.value >= editClips.value.length) selectedClip.value = null;
    savedSnapshot = snapshot();
    dirty.value = false;
    await loadFreshness();
    syncPolling();
  } catch (e) {
    console.error('[Sequences] 详情加载失败', e);
    detail.value = null;
  }
}

async function loadFreshness() {
  freshness.value = '';
  if (!projectName.value || !selectedId.value || !detail.value?.final_render) return;
  try {
    const f = await apiClient.getSequenceFreshness(projectName.value, selectedId.value);
    freshness.value = f?.status || '';
  } catch { /* 静默：freshness 非关键 */ }
}

async function selectSequence(id: string) {
  if (id === selectedId.value) return;
  selectedId.value = id;
  menuOpen.value = false;
  addOpen.value = false;
  selectedClip.value = null;
  previewClipIndex.value = null;
  await loadDetail();
}

async function createFromAll() {
  if (!projectName.value || !doneShots.value.length) return;
  try {
    const r = await apiClient.createSequence(projectName.value, {
      title: `合成 ${sequences.value.length + 1}`,
      clips: doneShots.value.map(s => ({ shot_id: s.shot_id })),
    });
    await loadList(false);
    selectedId.value = r.sequence_id;
    await loadDetail();
  } catch (e: any) {
    console.error('[Sequences] 创建失败', e);
    window.alert(e.message || '创建合成失败');
  }
}

async function save(): Promise<boolean> {
  if (!projectName.value || !selectedId.value || saving.value) return false;
  saving.value = true;
  try {
    await apiClient.updateSequence(projectName.value, selectedId.value, {
      title: editTitle.value,
      clips: editClips.value.map(c => ({ shot_id: c.shot_id, trim_in_ms: c.trim_in_ms || 0, trim_out_ms: c.trim_out_ms || 0 })),
    });
    savedSnapshot = snapshot();
    dirty.value = false;
    return true;
  } catch (e: any) {
    console.error('[Sequences] 保存失败', e);
    window.alert(e.message || '保存失败');
    return false;
  } finally { saving.value = false; }
}

async function removeSequence() {
  menuOpen.value = false;
  if (!projectName.value || !selectedId.value) return;
  if (!window.confirm(`确认删除合成 ${editTitle.value || selectedId.value}？已渲染的版本文件会一并删除。`)) return;
  try {
    await apiClient.deleteSequence(projectName.value, selectedId.value);
    selectedId.value = null;
    detail.value = null;
    await loadList(false);
    if (selectedId.value) await loadDetail();
  } catch (e: any) {
    console.error('[Sequences] 删除失败', e);
    window.alert(e.message || '删除失败');
  }
}

/** 渲染只按已保存内容出片（§22 No Auto Render）：有未保存修改时先保存再渲染。 */
async function doRender() {
  if (!projectName.value || !selectedId.value || rendering.value) return;
  if (dirty.value && !(await save())) return;
  rendering.value = true;
  try {
    await apiClient.renderSequence(projectName.value, selectedId.value);
    previewClipIndex.value = null;   // 回到成片预览
    await loadDetail();
  } catch (e: any) {
    console.error('[Sequences] 渲染触发失败', e);
    window.alert(e.message || '渲染触发失败');
    rendering.value = false;
  }
}

async function selectRender(versionId: string) {
  if (!projectName.value || !selectedId.value) return;
  try {
    await apiClient.selectSequenceRender(projectName.value, selectedId.value, versionId);
    previewClipIndex.value = null;
    await Promise.all([loadList(), loadDetail()]);
  } catch (e: any) {
    console.error('[Sequences] 选择版本失败', e);
    window.alert(e.message || '选择失败');
  }
}

// ---- 时间线编辑 ----
function selectClip(i: number) {
  selectedClip.value = selectedClip.value === i ? null : i;
  previewClipIndex.value = selectedClip.value;   // 点片段 → 预览该段源片
}
function backToFinal() { previewClipIndex.value = null; selectedClip.value = null; }
function addClip(shotId: string) {
  editClips.value = [...editClips.value, { shot_id: shotId, trim_in_ms: 0, trim_out_ms: 0 }];
  markDirty();
}
function removeClip(i: number) {
  editClips.value = editClips.value.filter((_, idx) => idx !== i);
  if (selectedClip.value === i) selectedClip.value = null;
  else if (selectedClip.value != null && selectedClip.value > i) selectedClip.value -= 1;
  if (previewClipIndex.value === i) previewClipIndex.value = null;
  markDirty();
}
function moveClip(i: number, dir: number) {
  const j = i + dir;
  if (j < 0 || j >= editClips.value.length) return;
  const arr = [...editClips.value];
  [arr[i], arr[j]] = [arr[j], arr[i]];
  editClips.value = arr;
  markDirty();
}

// ---- 拖拽：时间线内重排 / 素材池拖入插入 ----
function onClipDragStart(e: DragEvent, i: number) {
  dragIndex.value = i; poolDragShot.value = null;
  e.dataTransfer?.setData('text/plain', `clip:${i}`);
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
}
function onPoolDragStart(e: DragEvent, shotId: string) {
  poolDragShot.value = shotId; dragIndex.value = null;
  e.dataTransfer?.setData('text/plain', `pool:${shotId}`);
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'copy';
}
function onDropAt(i: number) {
  const at = dragOverIndex.value ?? i;
  if (dragIndex.value != null) {
    const arr = [...editClips.value];
    const [moved] = arr.splice(dragIndex.value, 1);
    arr.splice(dragIndex.value < at ? at - 1 : at, 0, moved);
    editClips.value = arr;
    markDirty();
  } else if (poolDragShot.value) {
    const arr = [...editClips.value];
    arr.splice(at, 0, { shot_id: poolDragShot.value, trim_in_ms: 0, trim_out_ms: 0 });
    editClips.value = arr;
    markDirty();
  }
  resetDrag();
}
function onDropEnd() {
  if (dragIndex.value != null) moveClip(dragIndex.value, editClips.value.length - 1 - dragIndex.value);
  else if (poolDragShot.value) addClip(poolDragShot.value);
  resetDrag();
}
function resetDrag() { dragIndex.value = null; poolDragShot.value = null; dragOverIndex.value = null; }

// ---- 渲染轮询：存在 queued/rendering 版本时 2.5s 拉一次（编辑中跳过，避免覆盖本地修改）----
let pollTimer: number | null = null;
function syncPolling() {
  const busy = (detail.value?.render_versions || [])
    .some((r: any) => r.status === 'queued' || r.status === 'rendering');
  if (busy && pollTimer === null) {
    pollTimer = window.setInterval(async () => {
      if (dirty.value) return;
      await loadDetail();
      await loadList();
    }, 2500);
  } else if (!busy && pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
    rendering.value = false;
  }
}

function onDocClick() { menuOpen.value = false; addOpen.value = false; }
onMounted(async () => {
  document.addEventListener('click', onDocClick);
  await loadShots();
  await loadList();
  if (selectedId.value) await loadDetail();
});
onUnmounted(() => {
  document.removeEventListener('click', onDocClick);
  if (pollTimer !== null) { clearInterval(pollTimer); pollTimer = null; }
});
watch(projectName, async () => {
  selectedId.value = null;
  detail.value = null;
  selectedClip.value = null;
  previewClipIndex.value = null;
  await loadShots();
  await loadList(false);
  if (selectedId.value) await loadDetail();
});
</script>

<style lang="scss" scoped>
.seq-page { height: calc(100vh - 52px); }
.empty {
  height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 14px; color: var(--text-muted); font-size: 14px;
}
.go-btn {
  padding: 8px 18px; border-radius: 6px; border: none; background: var(--accent);
  color: var(--accent-text); font-size: 13px; cursor: pointer; &:hover { background: var(--accent-hover); }
}

.seq-layout { display: flex; height: 100%; position: relative; }

.expand-fab {
  position: absolute; top: 10px; left: 10px; z-index: 10;
  display: flex; align-items: center; gap: 4px; padding: 5px 10px 5px 8px;
  border-radius: 6px; border: 1px solid var(--border); background: var(--bg-surface);
  color: var(--text-muted); font-size: 12px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-secondary); }
}

/* ---------- 左栏 ---------- */
.rail {
  width: 232px; flex-shrink: 0; display: flex; flex-direction: column; min-height: 0;
  border-right: 1px solid var(--border); background: var(--bg-surface);
  transition: width 0.2s ease, border-color 0.2s ease;
  &.collapsed { width: 0; border-right: none; overflow: hidden; }
}
.rail-sec { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.pool-sec { flex: 1.2; border-top: 1px solid var(--border); }
.rail-head {
  flex-shrink: 0; display: flex; align-items: center; gap: 8px;
  padding: 10px 12px 8px;
}
.rail-title { font-size: 11px; font-weight: 600; letter-spacing: 0.08em; color: var(--text-muted); }
.rail-hint { font-size: 10px; color: var(--text-faint); margin-left: auto; }
.rail-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 0 8px 8px; }
.rail-foot {
  flex-shrink: 0; padding: 10px 8px; border-top: 1px solid var(--border);
  .new-btn { width: 100%; }
}
.icon-btn {
  width: 24px; height: 24px; border-radius: 4px; border: none; background: none;
  color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center;
  &:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-secondary); }
  &:disabled { opacity: 0.35; cursor: not-allowed; }
}

.seq-item {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px; border-radius: 6px; cursor: pointer; border-left: 2px solid transparent;
  &:hover { background: var(--bg-hover); }
  &.active { background: var(--bg-hover); border-left-color: var(--accent); }
}
.seq-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.seq-title { font-size: 13px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.seq-sub { display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--text-muted); }
.mini-badge {
  font-size: 10px; padding: 0 6px; border-radius: 8px; line-height: 16px;
  border: 1px solid var(--border); color: var(--text-muted); white-space: nowrap;
}
.new-btn {
  padding: 7px 12px; border-radius: 6px; border: 1px solid var(--border);
  background: none; color: var(--text-secondary); font-size: 12px; cursor: pointer;
  &:hover:not(:disabled) { border-color: var(--border-hover); color: var(--text-primary); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
  &.primary { border: none; background: var(--accent); color: var(--accent-text, #fff); &:hover:not(:disabled) { background: var(--accent-hover); color: var(--accent-text, #fff); } }
}

.pool-item {
  position: relative; display: flex; align-items: center; gap: 8px;
  padding: 6px; border-radius: 6px; cursor: grab; border: 1px solid transparent;
  &:hover { background: var(--bg-hover); border-color: var(--border);
    .pool-add { opacity: 1; } }
  &.used .pool-thumb { opacity: 0.55; }
}
.pool-thumb {
  width: 64px; height: 36px; flex-shrink: 0; object-fit: cover;
  border-radius: 4px; background: var(--bg-root); border: 1px solid var(--border);
  &.sm { width: 44px; height: 25px; }
}
.pool-thumb-empty { display: block; }
.pool-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.pool-id { font-size: 12px; color: var(--text-secondary); }
.pool-dur { font-size: 10px; color: var(--text-faint); }
.pool-used {
  font-size: 10px; color: var(--text-faint); border: 1px solid var(--border);
  border-radius: 8px; padding: 0 5px; line-height: 14px; flex-shrink: 0;
}
.pool-add {
  position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
  font-size: 13px; color: var(--accent); opacity: 0; transition: opacity 0.12s ease;
  pointer-events: none;
}
.empty-mini { padding: 10px 6px; font-size: 12px; color: var(--text-faint); line-height: 1.6; }

/* ---------- 舞台 ---------- */
.stage-col { flex: 1; min-width: 0; display: flex; flex-direction: column; min-height: 0; }
.stage-inner { flex: 1; min-height: 0; display: flex; flex-direction: column; }

.stage-head {
  flex-shrink: 0; display: flex; align-items: center; gap: 10px;
  padding: 10px 20px; border-bottom: 1px solid var(--border);
}
.title-input {
  width: 240px; background: transparent; border: none; border-bottom: 1px dashed transparent;
  color: var(--text-primary); font-size: 15px; font-weight: 600; padding: 4px 2px; font-family: inherit;
  &:hover { border-bottom-color: var(--border); }
  &:focus { outline: none; border-bottom-color: var(--accent); }
  &::placeholder { color: var(--text-faint); }
}
.muted { font-size: 11px; color: var(--text-faint); }
.mono { font-family: monospace; }
.head-actions { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.act-btn {
  padding: 5px 14px; border-radius: 6px; border: 1px solid var(--border);
  background: none; color: var(--text-secondary); font-size: 12px; cursor: pointer;
  &:hover:not(:disabled) { border-color: var(--border-hover); color: var(--text-primary); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
  &.primary { border: none; background: var(--accent); color: var(--accent-text, #fff);
    &:hover:not(:disabled) { background: var(--accent-hover); } }
}
.link-btn {
  background: none; border: none; color: var(--text-muted); font-size: 12px; cursor: pointer;
  &:hover { color: var(--accent); }
  &.danger { color: #f87171; &:hover { color: #f87171; } }
}

/* 预览播放器（居中舞台，微影院氛围） */
.stage {
  flex: 1; min-height: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 10px;
  padding: 18px 24px; overflow: auto;
  background: var(--bg-root);
}
.player-wrap { position: relative; width: 100%; max-width: 760px; }
.player {
  display: block; width: 100%; aspect-ratio: 16 / 9;
  background: #000; border: 1px solid var(--border); border-radius: 10px;
  box-shadow: 0 10px 34px rgba(0, 0, 0, 0.45);
}
.player-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 10px; color: var(--text-faint); font-size: 13px;
  p { margin: 0; }
}
.player-badge {
  position: absolute; left: 12px; top: 12px; z-index: 3;
  display: flex; align-items: center; gap: 8px;
  font-size: 11px; color: #d4d4d4;
  background: rgba(0, 0, 0, 0.55); border: 1px solid var(--border); border-radius: 6px;
  padding: 3px 8px;
  .badge-btn {
    border: none; background: none; color: var(--accent); font-size: 11px; cursor: pointer;
    padding: 0; &:hover { text-decoration: underline; }
  }
}
.stage-meta {
  display: flex; align-items: center; gap: 10px;
  font-size: 12px; color: var(--text-muted);
}
.sep-dot { width: 3px; height: 3px; border-radius: 50%; background: var(--text-faint); }
.fresh { color: var(--text-secondary);
  &.stale { border: 1px dashed var(--border); border-radius: 8px; padding: 0 6px; color: var(--text-muted); } }

/* ---------- 时间线（胶片条） ---------- */
.timeline { flex-shrink: 0; border-top: 1px solid var(--border); background: var(--bg-surface); }
.tl-head {
  display: flex; align-items: center; gap: 10px; padding: 9px 20px 0;
}
.tl-title { font-size: 11px; font-weight: 600; letter-spacing: 0.08em; color: var(--text-muted); }
.tl-sub { font-size: 10px; color: var(--text-faint); }
.tl-dirty { font-size: 11px; color: var(--text-muted); border: 1px dashed var(--border); border-radius: 8px; padding: 0 7px; line-height: 18px; }
.spacer { flex: 1; }
.tl-strip {
  display: flex; align-items: stretch; gap: 8px;
  padding: 8px 20px 6px; overflow-x: auto; min-height: 96px;
}
.tl-clip {
  width: 132px; flex-shrink: 0; cursor: pointer; border-radius: 8px;
  border: 1px solid var(--border); background: var(--bg-root); padding: 5px;
  transition: border-color 0.12s ease, box-shadow 0.12s ease;
  &:hover { border-color: var(--border-hover); }
  &.sel { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent), 0 0 14px rgba(255, 255, 255, 0.06); }
  &.drop { border-color: var(--accent); border-style: dashed; }
}
.tl-thumb {
  position: relative; aspect-ratio: 16 / 9; border-radius: 5px; overflow: hidden;
  background: #000; border: 1px solid var(--border);
  img { display: block; width: 100%; height: 100%; object-fit: cover; }
}
.tl-thumb-empty { display: block; width: 100%; height: 100%; }
.tl-idx {
  position: absolute; left: 4px; top: 4px; font-size: 10px; font-family: monospace;
  color: #d4d4d4; background: rgba(0, 0, 0, 0.55); border-radius: 3px; padding: 0 4px; line-height: 14px;
}
.tl-dur {
  position: absolute; right: 4px; bottom: 4px; font-size: 10px;
  color: #d4d4d4; background: rgba(0, 0, 0, 0.55); border-radius: 3px; padding: 0 4px; line-height: 14px;
}
.tl-meta {
  display: flex; align-items: center; gap: 4px; margin-top: 5px; height: 20px;
}
.tl-shot { font-size: 11px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tl-ops { display: flex; align-items: center; gap: 0; margin-left: auto; opacity: 0; transition: opacity 0.12s ease;
  .tl-clip:hover & { opacity: 1; }
  .icon-btn { width: 20px; height: 20px; }
}
.tl-add-wrap { flex-shrink: 0; display: flex; }
.tl-add {
  width: 44px; border-radius: 8px; border: 1px dashed var(--border); background: none;
  color: var(--text-faint); font-size: 18px; cursor: pointer; align-self: stretch;
  &:hover { border-color: var(--accent); color: var(--accent); }
}

.tl-inspector {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  margin: 0 20px 10px; padding: 8px 12px;
  border: 1px solid var(--border); border-radius: 8px; background: var(--bg-root);
}
.insp-id { font-size: 12px; font-weight: 600; color: var(--text-primary); }
.trim {
  display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--text-muted);
  input {
    width: 76px; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 4px;
    color: var(--text-primary); font-size: 11px; padding: 3px 6px; font-family: inherit;
    &:focus { border-color: var(--border-hover); outline: none; }
  }
}
.insp-eff { font-size: 11px; color: var(--text-muted); }

/* ---------- 下拉 ---------- */
.menu-wrap { position: relative; }
.more-btn { width: 26px; height: 26px; }
.menu-pop {
  position: absolute; right: 0; top: calc(100% + 6px); z-index: 100; min-width: 176px;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25); padding: 4px;
}
.menu-item {
  display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; padding: 7px 10px;
  border: none; background: none; color: var(--text-secondary); font-size: 12px;
  border-radius: 4px; cursor: pointer;
  &:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
  &.danger { color: #f87171; &:hover { background: rgba(248,113,113,0.12); color: #f87171; } }
}
.menu-empty { padding: 8px 10px; font-size: 11px; color: var(--text-faint); }
.add-pop { left: 0; right: auto; max-height: 240px; overflow-y: auto; min-width: 220px; }
.pool-row { .pool-used { margin-left: auto; } }

/* ---------- 渲染版本轨 ---------- */
.renders { flex-shrink: 0; border-top: 1px solid var(--border); background: var(--bg-surface); }
.rd-head { display: flex; align-items: center; gap: 10px; padding: 9px 20px 0; }
.rd-strip {
  display: flex; gap: 8px; padding: 8px 20px 12px; overflow-x: auto;
}
.rd-card {
  min-width: 208px; flex-shrink: 0; border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg-root); padding: 8px 10px; font-size: 12px;
  &.final { border-color: var(--accent); }
}
.rd-row1 { display: flex; align-items: center; gap: 8px; }
.rd-row2 { display: flex; align-items: center; gap: 10px; margin-top: 5px; color: var(--text-muted); font-size: 11px; }
.r-id { color: var(--text-secondary); font-size: 11px; }
.r-status {
  font-size: 10px; padding: 0 6px; border-radius: 3px; line-height: 16px;
  background: var(--bg-hover); color: var(--text-muted);
  &.done { color: var(--text-secondary); }
  &.failed { background: rgba(248, 113, 113, 0.15); color: #f87171; }
}
.r-error { margin-top: 5px; font-size: 11px; color: #f87171; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 220px; }

.stage-solo { flex: 1; }
</style>
