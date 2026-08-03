<template>
  <div class="wb-overlay" @click.self="$emit('close')">
    <div class="wb">
      <header class="wb-head">
        <span class="wb-title">{{ shot.scene_id }} · {{ shot.shot_id }}</span>
        <span class="wb-save" :class="saveStatus">{{ saveStatusText }}</span>
        <span class="wb-task" :class="taskClass">{{ taskStatusText }}</span>
        <button class="wb-close" @click="$emit('close')">✕</button>
      </header>

      <div class="wb-body">
        <!-- 左：镜头设定 -->
        <div class="wb-left">
          <div class="field">
            <label>本镜出场角色</label>
            <div class="char-chips">
              <button v-for="name in sceneCharacters" :key="name" class="chip" :class="{ on: form.characters.includes(name) }" @click="toggleChar(name)">{{ name }}</button>
              <span v-if="!sceneCharacters.length" class="muted">故事段无角色</span>
            </div>
          </div>
          <div class="field">
            <label>景别</label>
            <select v-model="form.shot_size">
              <option>中景</option><option>近景</option><option>远景</option><option>特写</option><option>全景</option>
            </select>
          </div>
          <div class="field">
            <label>画面描述（中文）</label>
            <textarea v-model="form.description" rows="3" placeholder="写这条镜头的画面…"></textarea>
          </div>
          <div class="field">
            <label>英文提示词</label>
            <div class="prompt-row">
              <textarea v-model="form.prompt_en" rows="3" placeholder="可点翻译自动生成，也可手改"></textarea>
              <button class="mini-btn" :disabled="translating" @click="translate">{{ translating ? '译中…' : '翻译' }}</button>
            </div>
          </div>
          <div class="field"><label>台词</label><input v-model="form.dialogue" placeholder="台词（可选）" /></div>
          <div class="field">
            <label>接续方式</label>
            <select v-model="form.link_mode">
              <option value="i2v">图生视频（跨场景硬切）</option>
              <option value="keyframes">首尾帧过渡（同场景接力）</option>
              <option value="none">不接续</option>
            </select>
          </div>
          <div class="field">
            <label>配音</label>
            <div class="audio-row">
              <span class="audio-status">{{ audioStatusText }}</span>
              <button class="mini-btn" :disabled="audioBusy" @click="genAudio">{{ audioBusy ? '配音中…' : '生成配音' }}</button>
            </div>
          </div>
          <details class="advanced">
            <summary>高级（机位 / 光照 / 动作 / negative）</summary>
            <div class="field"><label>机位 / 运镜</label><input v-model="form.camera" /></div>
            <div class="field"><label>光照</label><input v-model="form.lighting" /></div>
            <div class="field"><label>画面动作</label><input v-model="form.motion" /></div>
            <div class="field"><label>Negative prompt</label><input v-model="form.negative_prompt" /></div>
          </details>
        </div>

        <!-- 右：三阶段标签 -->
        <div class="wb-right">
          <div class="tabs">
            <button v-for="t in tabs" :key="t" class="tab" :class="{ on: activeTab === t }" @click="activeTab = t">{{ tabLabel[t] }}</button>
          </div>
          <div class="tab-body">
            <!-- 首帧 -->
            <div v-if="activeTab === 'first'">
              <button class="gen-btn" :disabled="busy('first_frame')" @click="genFirstFrame">{{ busy('first_frame') ? '生成中…' : '生成新一批（5 候选）' }}</button>
              <div v-for="b in ffBatchesReversed" :key="b.batch_id" class="batch">
                <div class="batch-head" @click="toggleFFBatch(b.batch_id)">
                  <span class="batch-chevron">{{ isFFBatchExpanded(b.batch_id) ? '▾' : '▸' }}</span>
                  <span class="batch-title">批次 · {{ b.candidates.length }} 候选 · {{ b.status === 'done' ? '完成' : b.status }}</span>
                </div>
                <div v-if="isFFBatchExpanded(b.batch_id)" class="cands">
                  <div v-for="(c, i) in b.candidates" :key="c.candidate_id" class="cand" :class="{ sel: c.candidate_id === shot.selected_first_frame }" @click="chooseFirst(c.candidate_id)">
                    <img v-if="c.local" :src="imgUrl(c.local)" />
                    <SafeFrameOverlay />
                    <span class="cand-idx">v{{ i + 1 }}</span>
                  </div>
                </div>
              </div>
              <span v-if="!shot.first_frame_batches?.length" class="muted">尚无首帧，点上方生成</span>
              <button v-if="ffNeedsRehost" class="mini-btn rehost" @click="rehost">修复图片链接（本地图待托管）</button>
            </div>
            <!-- 尾帧 -->
            <div v-else-if="activeTab === 'last'">
              <div v-if="shot.link_mode !== 'keyframes'" class="muted pad">当前接续方式不需要尾帧</div>
              <template v-else>
                <button class="gen-btn" :disabled="!ffUrlReady || busy('last_frame')" @click="genLastFrame" :title="!ffUrlReady ? '需先选定首帧' : ''">{{ busy('last_frame') ? '生成中…' : '生成尾帧' }}</button>
                <div class="cands">
                  <div v-for="c in latestLFCands" :key="c.candidate_id" class="cand" :class="{ sel: c.candidate_id === shot.selected_last_frame }">
                    <img v-if="c.local" :src="imgUrl(c.local)" />
                    <SafeFrameOverlay />
                  </div>
                  <span v-if="!latestLFCands.length" class="muted">尚无尾帧</span>
                </div>
              </template>
            </div>
            <!-- 视频 -->
            <div v-else>
              <button class="gen-btn" :disabled="!canGenVideo || busy('video')" @click="genVideo" :title="videoBlockReason">{{ busy('video') ? jobMsg('video') : '再生成一个版本（1 RPM，约 70-90s）' }}</button>
              <div v-if="videoBlockReason && !busy('video')" class="warn">⚠ {{ videoBlockReason }}</div>
              <div v-if="videoVersionsReversed.length" class="video-versions">
                <div v-for="v in videoVersionsReversed" :key="v.version_id" class="video-version" :class="{ final: v.version_id === shot.final_video }">
                  <video v-if="v.local" :src="vidUrl(v.local)" controls preload="metadata"></video>
                  <div class="vv-meta">
                    <span class="vv-ret" v-if="v.seconds_returned">{{ v.seconds_returned }}s / {{ v.size_returned }}</span>
                    <span class="vv-final" v-if="v.version_id === shot.final_video">✓ 最终视频</span>
                    <button v-else class="mini-btn" @click="setFinalVideo(v.version_id)">设为最终</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue';
import { apiClient } from '@/api';
import type { ShotData } from '@/api';
import { getImageUrl, getVideoUrl } from '@/utils/media';
import SafeFrameOverlay from './SafeFrameOverlay.vue';
import { useJobStore } from '@/store/jobs';
import { useTaskStream } from '@/composables/useTaskStream';
import { createDiscreteApi, darkTheme } from 'naive-ui';

const { message } = createDiscreteApi(['message'], { configProviderProps: { theme: darkTheme } });

const props = defineProps<{ shot: ShotData; projectName: string; sceneCharacters: string[]; sceneId: string }>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'refresh'): void }>();
const jobStore = useJobStore();

const tabs = ['first', 'last', 'video'] as const;
const tabLabel: Record<string, string> = { first: '首帧', last: '尾帧', video: '视频', audio: '配音' };
const activeTab = ref<'first' | 'last' | 'video'>('first');

// 镜头设定（本地编辑，debounce 自动保存）
const form = reactive({
  characters: [...props.shot.characters],
  shot_size: props.shot.shot_size || '中景',
  description: props.shot.description || '',
  prompt_en: props.shot.prompt_en || '',
  dialogue: props.shot.dialogue || '',
  link_mode: props.shot.link_mode || 'i2v',
  camera: props.shot.camera || '',
  lighting: props.shot.lighting || '',
  motion: props.shot.motion || '',
  negative_prompt: props.shot.negative_prompt || '',
});

const saveStatus = ref<'idle' | 'saving' | 'saved' | 'failed'>('idle');
const translating = ref(false);
let saveTimer: number | null = null;

function scheduleSave() {
  saveStatus.value = 'saving';
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => { saveTimer = null; doSave(); }, 700);
}
async function doSave() {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
  saveStatus.value = 'saving';
  try {
    await apiClient.updateShot(props.projectName, props.shot.shot_id, { ...form });
    saveStatus.value = 'saved';
  } catch {
    saveStatus.value = 'failed';
  }
}
const saveStatusText = computed(() => saveStatus.value === 'saving' ? '保存中…' : saveStatus.value === 'saved' ? '已保存' : saveStatus.value === 'failed' ? '保存失败' : '');

watch(form, scheduleSave, { deep: true });

function toggleChar(name: string) {
  const i = form.characters.indexOf(name);
  if (i > -1) form.characters.splice(i, 1);
  else form.characters.push(name);
}

async function translate() {
  if (!form.description.trim()) { message.info('请先写画面描述'); return; }
  translating.value = true;
  try {
    const r = await apiClient.translateText(form.description);
    if (r.english) { form.prompt_en = r.english; message.success('已翻译，可手改'); }
  } catch (e: any) { message.error(`翻译失败: ${e.message || e}`); }
  finally { translating.value = false; }
}

// 任务状态（来自全局 JobQueue SSE）
function busy(kind: string) {
  return jobStore.byShot(props.shot.shot_id).some(j => j.kind === kind && (j.state === 'running' || j.state === 'queued'));
}
function jobMsg(kind: string) {
  const j = jobStore.byShot(props.shot.shot_id).find(j => j.kind === kind && (j.state === 'running' || j.state === 'queued'));
  return j?.message || '生成中…';
}
const taskStatusText = computed(() => {
  const js = jobStore.byShot(props.shot.shot_id).filter(j => j.state === 'running' || j.state === 'queued');
  if (!js.length) return '';
  const names = js.map(j => ({ first_frame: '首帧', last_frame: '尾帧', video: '视频', audio: '配音' } as any)[j.kind] || j.kind);
  return names.join('、') + (js.some(j => j.state === 'running') ? ' 生成中' : ' 排队中');
});
const taskClass = computed(() => taskStatusText.value ? 'busy' : '');

// 首帧批次（读 first_frame_batches，选择指针 selected_first_frame）
const latestFFBatch = computed(() => props.shot.first_frame_batches?.[props.shot.first_frame_batches.length - 1]);
const latestFFCands = computed(() => latestFFBatch.value?.candidates || []);
const selectedFFCand = computed(() => {
  const cid = props.shot.selected_first_frame;
  if (!cid) return null;
  for (const b of props.shot.first_frame_batches || []) {
    const c = (b.candidates || []).find(x => x.candidate_id === cid);
    if (c) return c;
  }
  return null;
});
const ffUrlReady = computed(() => !!selectedFFCand.value?.url);
const ffNeedsRehost = computed(() => !!selectedFFCand.value?.local && !selectedFFCand.value?.url);
async function genFirstFrame() {
  try { await apiClient.generateFirstFrame(props.projectName, props.shot.shot_id, { variants: 5 }); message.info('首帧生成已入队（5 候选）'); }
  catch (e: any) { message.error(`入队失败: ${e.message || e}`); }
}
async function chooseFirst(candidateId: string) {
  try { await apiClient.chooseFirstFrame(props.projectName, props.shot.shot_id, candidateId); emit('refresh'); }
  catch (e: any) { message.error(`选定失败: ${e.message || e}`); }
}

// 首帧批次历史（最新在前）+ 展开状态（默认展开最新批次）
const ffBatchesReversed = computed(() => [...(props.shot.first_frame_batches || [])].reverse());
const expandedFFBatches = ref<Set<string>>(new Set());
function isFFBatchExpanded(batchId: string): boolean {
  if (expandedFFBatches.value.size === 0) {
    const latest = props.shot.first_frame_batches?.[props.shot.first_frame_batches.length - 1];
    return latest?.batch_id === batchId;
  }
  return expandedFFBatches.value.has(batchId);
}
function toggleFFBatch(batchId: string) {
  if (expandedFFBatches.value.size === 0) {
    const latest = props.shot.first_frame_batches?.[props.shot.first_frame_batches.length - 1];
    if (latest) expandedFFBatches.value = new Set([latest.batch_id]);
  }
  const s = new Set(expandedFFBatches.value);
  if (s.has(batchId)) s.delete(batchId); else s.add(batchId);
  expandedFFBatches.value = s;
}

// 视频版本历史（最新在前）+ 设最终视频
const videoVersionsReversed = computed(() => [...(props.shot.video_versions || [])].reverse());
async function setFinalVideo(versionId: string) {
  try { await apiClient.setFinalVideo(props.projectName, props.shot.shot_id, versionId); message.success('已设为最终视频'); emit('refresh'); }
  catch (e: any) { message.error(`设置失败: ${e.message || e}`); }
}
async function rehost() {
  try { await apiClient.rehostFirstFrame(props.projectName, props.shot.shot_id); message.success('已托管'); emit('refresh'); }
  catch (e: any) { message.error(`失败: ${e.message || e}`); }
}

// 尾帧批次
const latestLFBatch = computed(() => props.shot.last_frame_batches?.[props.shot.last_frame_batches.length - 1]);
const latestLFCands = computed(() => latestLFBatch.value?.candidates || []);
const selectedLFCand = computed(() => {
  const cid = props.shot.selected_last_frame;
  if (!cid) return null;
  for (const b of props.shot.last_frame_batches || []) {
    const c = (b.candidates || []).find(x => x.candidate_id === cid);
    if (c) return c;
  }
  return null;
});
const lfUrlReady = computed(() => !!selectedLFCand.value?.url);
async function genLastFrame() {
  try { await apiClient.generateLastFrame(props.projectName, props.shot.shot_id); message.info('尾帧生成已入队'); }
  catch (e: any) { message.error(`入队失败: ${e.message || e}`); }
}

// 视频版本（final_video 指针）
const finalVideoVersion = computed(() => {
  const vid = props.shot.final_video;
  if (!vid) return null;
  return (props.shot.video_versions || []).find(v => v.version_id === vid) || null;
});
const videoBlockReason = computed(() => {
  if (finalVideoVersion.value?.local) return '';
  if (!props.shot.video?.num_frames) return props.shot.audio?.status === 'done' ? '配音时长待反推（刷新自动补）' : '尚未配音，无法确定时长';
  const ffUsable = ffUrlReady.value || ffNeedsRehost.value;
  if (!ffUsable) return '请先生成并选择首帧';
  if (props.shot.link_mode === 'keyframes' && !lfUrlReady.value) return '请先生成尾帧';
  return '';
});
const canGenVideo = computed(() => !videoBlockReason.value);
async function genVideo() {
  try { await apiClient.generateShotVideo(props.projectName, props.shot.shot_id); message.info('视频生成已入队（1 RPM，约 70-90s）'); }
  catch (e: any) { message.error(`入队失败: ${e.message || e}`); }
}

// 配音（现有 /shot/{id}/audio，background_task + useTaskStream）
const audioBusy = ref(false);
const audioStatusText = computed(() => {
  if (audioBusy.value) return '配音中…';
  if (props.shot.audio?.status === 'done' && props.shot.audio?.local) return `已配音 ${((props.shot.audio.duration_ms || 0) / 1000).toFixed(2)}s`;
  return '未配音';
});
const { subscribe: subscribeTask } = useTaskStream();
async function genAudio() {
  audioBusy.value = true;
  try {
    const r: any = await apiClient.generateShotAudio(props.projectName, props.shot.shot_id, {});
    subscribeTask(r.task_id, (s: any) => {
      if (s.status === 'completed') { audioBusy.value = false; message.success('配音完成'); emit('refresh'); }
      else if (s.status === 'failed') { audioBusy.value = false; message.error('配音失败'); }
    });
  } catch (e: any) { audioBusy.value = false; message.error(`配音失败: ${e.message || e}`); }
}

function imgUrl(p: string) { return getImageUrl(p); }
function vidUrl(p: string) { return getVideoUrl(p); }
</script>

<style lang="scss" scoped>
.wb-overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6); z-index: 1500;
  display: flex; align-items: center; justify-content: center;
}
.wb {
  width: 92vw; height: 90vh; background: var(--bg-root); border: 1px solid var(--border);
  border-radius: 10px; display: flex; flex-direction: column; overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
}
.wb-head {
  display: flex; align-items: center; gap: 14px; padding: 12px 18px;
  border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.wb-title { font-size: 14px; font-weight: 600; color: var(--text-primary); font-family: monospace; }
.wb-save { font-size: 12px; color: var(--text-muted); &.saving { color: var(--text-muted); } &.saved { color: #4ade80; } &.failed { color: #f87171; } }
.wb-task { font-size: 12px; color: var(--text-muted); &.busy { color: #60a5fa; } }
.wb-close { margin-left: auto; background: none; border: none; color: var(--text-muted); font-size: 16px; cursor: pointer; &:hover { color: var(--text-primary); } }

.wb-body { flex: 1; display: flex; min-height: 0; }
.wb-left {
  width: 380px; flex-shrink: 0; overflow-y: auto; padding: 16px 18px;
  border-right: 1px solid var(--border);
}
.field { margin-bottom: 14px; label { display: block; font-size: 12px; color: var(--text-muted); margin-bottom: 5px; } }
.field input, .field select, .field textarea {
  width: 100%; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 5px;
  padding: 7px 10px; font-size: 13px; color: var(--text-primary); outline: none; font-family: inherit;
  &:focus { border-color: var(--accent); }
}
.field textarea { resize: vertical; line-height: 1.5; }
.prompt-row { display: flex; gap: 6px; align-items: flex-start; textarea { flex: 1; } }
.audio-row { display: flex; align-items: center; gap: 10px; }
.audio-status { font-size: 12px; color: var(--text-secondary); }
.mini-btn {
  padding: 6px 12px; border-radius: 5px; border: 1px solid var(--border); background: var(--bg-surface);
  color: var(--text-secondary); font-size: 12px; cursor: pointer; white-space: nowrap;
  &:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  &:disabled { opacity: 0.5; cursor: not-allowed; }
  &.rehost { margin-top: 10px; }
}
.char-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip {
  padding: 3px 10px; border-radius: 12px; border: 1px solid var(--border); background: none;
  color: var(--text-muted); font-size: 12px; cursor: pointer;
  &.on { background: var(--accent); border-color: var(--accent); color: #fff; }
}
.advanced { margin-top: 8px; summary { font-size: 12px; color: var(--text-muted); cursor: pointer; padding: 6px 0; } }
.muted { font-size: 12px; color: var(--text-faint); }
.pad { padding: 14px 0; }

.wb-right { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.tabs { display: flex; gap: 4px; padding: 12px 18px 0; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.tab {
  padding: 7px 18px; border: none; background: none; color: var(--text-muted); font-size: 13px;
  cursor: pointer; border-bottom: 2px solid transparent;
  &.on { color: var(--text-primary); border-bottom-color: var(--accent); }
}
.tab-body { flex: 1; overflow-y: auto; padding: 18px; }
.gen-btn {
  padding: 8px 18px; border-radius: 6px; border: none; background: var(--accent); color: var(--accent-text);
  font-size: 13px; cursor: pointer; margin-bottom: 14px;
  &:hover:not(:disabled) { background: var(--accent-hover); }
  &:disabled { opacity: 0.5; cursor: not-allowed; }
}
.cands { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; }
.cand {
  position: relative; aspect-ratio: 16/9; border-radius: 6px; overflow: hidden; cursor: pointer;
  border: 2px solid transparent; background: var(--bg-surface);
  &.sel { border-color: #4ade80; }
  &.still { cursor: default; }
  img { width: 100%; height: 100%; object-fit: cover; }
}
.cand-idx { position: absolute; top: 3px; left: 4px; font-size: 10px; color: #fff; background: rgba(0,0,0,0.5); padding: 0 4px; border-radius: 2px; }

.batch { margin-bottom: 14px; }
.batch-head { display: flex; align-items: center; gap: 6px; padding: 6px 10px; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 5px; cursor: pointer; margin-bottom: 8px; &:hover { background: var(--bg-hover); } }
.batch-chevron { font-size: 12px; color: var(--text-muted); }
.batch-title { font-size: 12px; color: var(--text-secondary); }
.video-versions { display: flex; flex-direction: column; gap: 12px; margin-top: 14px; }
.video-version { border: 1px solid var(--border); border-radius: 6px; padding: 10px; &.final { border-color: #4ade80; } video { width: 100%; max-height: 40vh; border-radius: 4px; background: #000; } }
.vv-meta { display: flex; align-items: center; gap: 10px; margin-top: 6px; }
.vv-ret { font-size: 11px; color: var(--text-muted); }
.vv-final { font-size: 12px; color: #4ade80; font-weight: 600; }
.warn { font-size: 12px; color: #fbbf24; margin-top: 8px; }
.video-preview { margin-top: 14px; video { width: 100%; max-height: 50vh; border-radius: 6px; background: #000; } }
.video-ret { font-size: 11px; color: var(--text-muted); margin-top: 6px; }
</style>
