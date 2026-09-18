<template>
  <div class="shot-card" :class="{ locked: shot.locked }">
    <div class="sc-head">
      <span class="sc-id">{{ shot.shot_id }}</span>
      <select class="sc-link" :value="shot.link_mode" @change="onLinkChange(($event.target as HTMLSelectElement).value)">
        <option value="i2v">i2v · 跨场景硬切</option>
        <option value="keyframes">keyframes · 同场景接力</option>
        <option value="none">none · 共用参考图</option>
      </select>
      <span class="sc-scene">{{ shot.scene_id }}</span>
      <button class="sc-lock" :title="shot.locked ? '已锁定' : '未锁定'" @click="toggleLock">{{ shot.locked ? '🔒' : '🔓' }}</button>
    </div>
    <div class="sc-dialogue" v-if="shot.dialogue">「{{ shot.dialogue }}」</div>

    <div class="sc-row">
      <div class="sc-section">
        <div class="sc-label">首帧 <span class="sc-status" :class="shot.first_frame.status">{{ ffLabel }}</span></div>
        <div class="sc-cands">
          <div v-for="(c, i) in shot.first_frame.candidates" :key="i"
               class="sc-cand"
               :class="{ sel: i === shot.first_frame.selected_index && shot.first_frame.status === 'done' }"
               @click="onChoose(i)">
            <img v-if="c.local" :src="imgUrl(c.local)" alt="candidate" />
            <SafeFrameOverlay />
            <span class="sc-cand-idx">v{{ i + 1 }}</span>
          </div>
          <button class="sc-gen" :disabled="busy('first_frame')" @click="genFirstFrame">
            {{ busy('first_frame') ? '生成中…' : (shot.first_frame.candidates.length ? '重生成' : '生成首帧') }}
          </button>
          <button class="sc-gen" v-if="ffNeedsRehost" :disabled="rehosting" @click="rehostFirstFrame"
                  title="上传到 imgbb 公开图床拿公网URL，不重新生成、不丢画面">
            {{ rehosting ? '托管中…' : '重新托管' }}
          </button>
        </div>
        <div class="sc-hint" v-if="shot.first_frame.candidates.length && !ffUrlReady && !ffNeedsRehost">👆 点缩略图选定首帧（选定后才能生成视频）</div>
        <div class="sc-hint" v-if="ffNeedsRehost">📦 本地图待托管：点「重新托管」上传到 imgbb 公开图床（图会公开可访问），或直接生成视频（自动托管）</div>
      </div>

      <div class="sc-section" v-if="shot.link_mode === 'keyframes'">
        <div class="sc-label">尾帧 <span class="sc-status" :class="shot.last_frame.status">{{ lfLabel }}</span></div>
        <div class="sc-cands">
          <div class="sc-cand still" v-if="shot.last_frame.candidates.length">
            <img v-if="shot.last_frame.candidates[0].local" :src="imgUrl(shot.last_frame.candidates[0].local)" alt="last" />
            <SafeFrameOverlay />
          </div>
          <button class="sc-gen" :disabled="!ffUrlReady || busy('last_frame')" @click="genLastFrame"
                  :title="!ffUrlReady ? '需先选定首帧' : ''">
            {{ busy('last_frame') ? '生成中…' : '生成尾帧' }}
          </button>
        </div>
      </div>
    </div>

    <div class="sc-row">
      <div class="sc-section">
        <div class="sc-label">配音 <span class="sc-status" :class="shot.audio.status">{{ audioLabel }}</span></div>
        <div class="sc-val" v-if="audioReady && shot.video.num_frames">{{ (shot.audio.duration_ms / 1000).toFixed(2) }}s · {{ shot.video.num_frames }}帧@{{ shot.video.frame_rate }}fps</div>
        <div class="sc-val muted" v-else-if="audioReady">已配音（时长待反推，刷新页面自动补）</div>
        <div class="sc-val muted" v-else>未配音（去音频页生成）</div>
      </div>

      <div class="sc-section">
        <div class="sc-label">视频 <span class="sc-status" :class="shot.video.status">{{ videoLabel }}</span></div>
        <div class="sc-video" v-if="shot.video.local">
          <video :src="vidUrl(shot.video.local)" controls preload="metadata"></video>
          <div class="sc-ret" v-if="shot.video.seconds_returned">
            实际 {{ shot.video.seconds_returned }}s / {{ shot.video.size_returned }}
            <span class="sc-planned" v-if="shot.video.num_frames">（提交 {{ (shot.video.num_frames / shot.video.frame_rate).toFixed(3) }}s）</span>
          </div>
        </div>
        <button class="sc-gen video" :disabled="!canGenVideo || busy('video')" @click="genVideo" :title="videoBlockReason">
          {{ busy('video') ? jobMsg('video') : '生成视频' }}
        </button>
        <div class="sc-hint" v-if="videoBlockReason && !busy('video')">⚠️ {{ videoBlockReason }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { apiClient } from '@/api';
import type { ShotData } from '@/api';
import { getImageUrl, getVideoUrl } from '@/utils/media';
import SafeFrameOverlay from './SafeFrameOverlay.vue';
import { useJobStore } from '@/store/jobs';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';

const { message } = createDiscreteApi(['message'], { configProviderProps: discreteConfigProviderProps });

const props = defineProps<{ shot: ShotData; projectName: string }>();
const emit = defineEmits<{ (e: 'refresh'): void }>();
const jobStore = useJobStore();

const ffUrlReady = computed(() =>
  props.shot.first_frame.status === 'done' &&
  !!props.shot.first_frame.candidates[props.shot.first_frame.selected_index]?.url);
// 🔴 本地图待托管：v1 迁移数据 local有/url空。不重新生成，走 rehost 补公网 URL。
const ffNeedsRehost = computed(() =>
  props.shot.first_frame.status === 'done' &&
  !!props.shot.first_frame.candidates[props.shot.first_frame.selected_index]?.local &&
  !props.shot.first_frame.candidates[props.shot.first_frame.selected_index]?.url);
// 首帧可用于视频：已选定(url_ready) 或 待托管(video handler 会自动 rehost)
const ffUsable = computed(() => ffUrlReady.value || ffNeedsRehost.value);
const lfUrlReady = computed(() =>
  props.shot.last_frame.status === 'done' &&
  !!props.shot.last_frame.candidates[props.shot.last_frame.selected_index]?.url);
const audioReady = computed(() => props.shot.audio.status === 'done' && !!props.shot.audio.local);
const videoReady = computed(() => props.shot.video.status === 'done' && !!props.shot.video.local);

const ffLabel = computed(() => labelFor(props.shot.first_frame.status, ffUrlReady.value, ffNeedsRehost.value));
const lfLabel = computed(() => labelFor(props.shot.last_frame.status, lfUrlReady.value));
const audioLabel = computed(() => props.shot.audio.status === 'done' ? '已配音' : '未配音');
const videoLabel = computed(() => {
  if (busy('video')) return '生成中';
  if (videoReady.value) return '已完成';
  if (props.shot.video.status === 'failed') return '失败';
  return '未生成';
});

const videoBlockReason = computed(() => {
  if (videoReady.value) return '';
  if (!props.shot.video.num_frames) return audioReady.value ? '配音时长待反推（刷新页面自动补）' : '尚未配音，无法确定时长';
  if (!ffUsable.value) return '请先生成并选择首帧';
  if (props.shot.link_mode === 'keyframes' && !lfUrlReady.value) return '请先生成尾帧';
  return '';
});
const canGenVideo = computed(() => !videoBlockReason.value);

function labelFor(status: string, urlReady: boolean, needsRehost = false): string {
  if (status === 'done') return urlReady ? '已选定' : (needsRehost ? '本地图待托管' : '待选定');
  if (status === 'pending') return '待选定';
  if (status === 'generating') return '生成中';
  if (status === 'failed') return '失败';
  return '未生成';
}
function busy(kind: string): boolean {
  return jobStore.byShot(props.shot.shot_id).some(j => j.kind === kind && (j.state === 'running' || j.state === 'queued'));
}
function jobMsg(kind: string): string {
  const j = jobStore.byShot(props.shot.shot_id).find(j => j.kind === kind && (j.state === 'running' || j.state === 'queued'));
  return j?.message || '生成中…';
}
function imgUrl(p: string) { return getImageUrl(p); }
function vidUrl(p: string) { return getVideoUrl(p); }

const rehosting = ref(false);
async function rehostFirstFrame() {
  rehosting.value = true;
  try {
    await apiClient.rehostFirstFrame(props.projectName, props.shot.shot_id);
    message.success('已托管，可生成视频');
    emit('refresh');
  } catch (e: any) { message.error(`托管失败: ${e.message || e}`); }
  finally { rehosting.value = false; }
}
async function genFirstFrame() {
  try {
    await apiClient.generateFirstFrame(props.projectName, props.shot.shot_id, { variants: 2 });
    message.info('首帧生成已入队');
  } catch (e: any) { message.error(`入队失败: ${e.message || e}`); }
}
async function onChoose(i: number) {
  try {
    await apiClient.chooseFirstFrame(props.projectName, props.shot.shot_id, i + 1);
    emit('refresh');
  } catch (e: any) { message.error(`选定失败: ${e.message || e}`); }
}
async function genLastFrame() {
  try {
    await apiClient.generateLastFrame(props.projectName, props.shot.shot_id);
    message.info('尾帧生成已入队');
  } catch (e: any) { message.error(`入队失败: ${e.message || e}`); }
}
async function genVideo() {
  try {
    await apiClient.generateShotVideo(props.projectName, props.shot.shot_id);
    message.info('视频生成已入队（1 RPM，约 70-90s）');
  } catch (e: any) { message.error(`入队失败: ${e.message || e}`); }
}
async function onLinkChange(v: string) {
  try {
    await apiClient.updateShot(props.projectName, props.shot.shot_id, { link_mode: v });
    emit('refresh');
  } catch (e: any) { message.error(`修改失败: ${e.message || e}`); }
}
async function toggleLock() {
  try {
    await apiClient.updateShot(props.projectName, props.shot.shot_id, { locked: !props.shot.locked });
    emit('refresh');
  } catch (e: any) { message.error(`修改失败: ${e.message || e}`); }
}
</script>

<style lang="scss" scoped>
.shot-card {
  background: var(--bg-root);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
  &.locked { opacity: 0.7; }
}
.sc-head {
  display: flex; align-items: center; gap: 10px; margin-bottom: 6px;
}
.sc-id { font-weight: 600; color: var(--text-primary); font-family: monospace; font-size: 13px; }
.sc-scene { color: var(--text-muted); font-size: 12px; margin-left: auto; }
.sc-link {
  padding: 2px 6px; border-radius: 4px; border: 1px solid var(--border);
  background: var(--bg-root); color: var(--text-secondary); font-size: 12px;
}
.sc-lock { background: none; border: none; cursor: pointer; font-size: 14px; }
.sc-dialogue { font-size: 12px; color: var(--text-secondary); margin-bottom: 10px; }
.sc-row { display: flex; gap: 16px; margin-bottom: 10px; }
.sc-section { flex: 1; min-width: 0; }
.sc-label { font-size: 12px; color: var(--text-muted); margin-bottom: 6px; display: flex; align-items: center; gap: 6px; }
.sc-status { font-size: 11px; padding: 1px 5px; border-radius: 3px; background: var(--bg-hover); color: var(--text-muted);
  &.done { background: rgba(74,222,128,0.2); color: #4ade80; }
  &.failed { background: rgba(248,113,113,0.2); color: #f87171; }
  &.generating { background: rgba(96,165,250,0.2); color: #60a5fa; }
}
.sc-cands { display: flex; gap: 8px; align-items: flex-start; flex-wrap: wrap; }
.sc-cand {
  position: relative; width: 120px; height: 67px; border-radius: 4px; overflow: hidden;
  border: 2px solid transparent; cursor: pointer; background: var(--bg-hover);
  &.sel { border-color: #4ade80; }
  &.still { cursor: default; }
  img { width: 100%; height: 100%; object-fit: cover; display: block; }
}
.sc-cand-idx { position: absolute; top: 2px; left: 2px; font-size: 10px; color: #fff; background: rgba(0,0,0,0.5); padding: 0 4px; border-radius: 2px; }
.sc-gen {
  padding: 4px 10px; border-radius: 4px; border: 1px solid var(--border);
  background: var(--bg-hover); color: var(--text-secondary); cursor: pointer; font-size: 12px;
  &:hover:not(:disabled) { background: var(--bg-root); border-color: var(--text-muted); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
  &.video { background: #60a5fa; color: #fff; border-color: #60a5fa;
    &:hover:not(:disabled) { background: #3b82f6; }
  }
}
.sc-hint { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
.sc-val { font-size: 12px; color: var(--text-secondary); &.muted { color: var(--text-muted); } }
.sc-video { margin-bottom: 6px;
  video { width: 100%; max-height: 180px; border-radius: 4px; background: #000; }
}
.sc-ret { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.sc-planned { color: var(--text-muted); opacity: 0.7; }
</style>
