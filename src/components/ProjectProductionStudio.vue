<template>
  <div class="pp-overlay" @click.self="$emit('close')">
    <div class="pp">
      <header class="pp-head">
        <span class="pp-title">完整影片自动制作</span>
        <span class="pp-status" :class="statusClass" v-if="run">{{ statusText }}</span>
        <span class="pp-sub" v-if="run && run.stage === 'scenes'">{{ completedShots }}/{{ totalShots }} 镜头</span>
        <button class="pp-close" @click="$emit('close')">✕</button>
      </header>

      <div class="pp-body">
        <!-- 预检 / 确认页（无 Run 或查看 preflight）-->
        <div v-if="view === 'preflight'" class="preflight-view">
          <div v-if="loadingPreflight" class="empty">检查项目准备情况…</div>
          <template v-else-if="preflight">
            <div class="pf-summary">
              <span>故事段 {{ preflight.scene_count }}</span>
              <span>镜头 {{ preflight.shot_count }}</span>
              <span v-if="preflight.locked_shot_count">锁定 {{ preflight.locked_shot_count }}</span>
            </div>
            <div class="pf-scenes">
              <div v-for="(s, i) in preflight.scenes" :key="s.scene_id" class="pf-scene" :class="sceneReadyClass(s)">
                <span class="pf-icon">{{ sceneIcon(s) }}</span>
                <div class="pf-info">
                  <div class="pf-name">故事段 {{ i + 1 }} · {{ s.scene_id }}</div>
                  <div class="pf-detail">
                    <span v-if="s.shot_count">{{ s.shot_count }} 个镜头</span>
                    <span v-if="s.plan_status === 'approved'">制作计划已确认</span>
                    <span v-else-if="s.plan_status">计划状态：{{ s.plan_status }}</span>
                    <span v-for="iss in s.issues" :key="iss" class="pf-issue">{{ issueLabel(iss) }}</span>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="!preflight.ready" class="pf-notready">
              还有 {{ notReadyCount }} 个故事段没有准备完成。<br />
              本阶段完整制作只处理已完成镜头规划和制作规划的项目。
            </div>
            <template v-else>
              <div class="confirm-info">
                <p>本次会为每个未锁定镜头生成新的：提示词、首帧、尾帧（计划需要时）、视频。</p>
                <p>图片候选将自动采用，整个流程不会中途等待人工选择。</p>
                <p>第一版采用安全的逐镜、逐故事段执行方式。</p>
              </div>
              <label class="lock-check">
                <input type="checkbox" v-model="respectLocked" />
                保留已锁定镜头（沿用现有最终视频）
              </label>
            </template>
          </template>
        </div>

        <!-- 监控页（running / pausing / paused）-->
        <div v-else-if="view === 'monitor'" class="monitor-view">
          <div v-if="run!.error" class="run-error">{{ run!.error }}</div>

          <!-- 整体进度 -->
          <div class="overall-prog">
            <div class="op-bar"><div class="op-fill" :style="{ width: overallPct + '%' }"></div></div>
            <div class="op-text">{{ completedShots }} / {{ totalShots }} 镜头</div>
          </div>

          <!-- 各故事段进度 -->
          <div class="scene-list">
            <div v-for="(s, i) in run!.scenes" :key="s.scene_id" class="scene-row" :class="sceneRowClass(s)">
              <span class="sr-icon">{{ sceneRowIcon(s) }}</span>
              <div class="sr-info">
                <div class="sr-name">故事段 {{ i + 1 }} · {{ s.scene_id }}</div>
                <div class="sr-detail">
                  <span v-if="s.status === 'completed'">{{ s.shot_count }} / {{ s.shot_count }}</span>
                  <span v-else-if="s.scene_id === run!.current_scene_id && childProgress">{{ childProgress.done }} / {{ s.shot_count }}</span>
                  <span v-else-if="s.status === 'running'">0 / {{ s.shot_count }}</span>
                  <span v-else>{{ sceneStatusLabel(s.status) }}</span>
                  <span v-if="s.error" class="sr-err">{{ s.error }}</span>
                </div>
              </div>
            </div>
          </div>

          <div class="current-hint" v-if="run!.current_scene_id && childProgress">
            当前：故事段 {{ run!.current_scene_id }} · {{ childProgress.stageLabel || '处理中' }}
          </div>
        </div>

        <!-- 合成页 -->
        <div v-else-if="view === 'composing'" class="compose-view">
          <div class="compose-spin">⏳</div>
          <div class="compose-text">{{ totalShots }} / {{ totalShots }} 镜头完成</div>
          <div class="compose-sub">正在合成完整影片…</div>
        </div>

        <!-- 完成页 -->
        <div v-else-if="view === 'completed'" class="done-view">
          <div class="done-icon">✓</div>
          <div class="done-title">完整影片制作完成</div>
          <div class="done-stats">
            <span>{{ totalShots }} 个镜头</span>
            <span>{{ run!.scenes.length }} 个故事段</span>
            <span v-if="run!.final_duration_s">总时长 {{ formatDuration(run!.final_duration_s) }}</span>
          </div>
          <video v-if="run!.final_output" class="done-video" :src="finalVideoUrl(run!.final_output)" controls></video>
          <div class="done-actions">
            <a v-if="run!.final_output" class="btn primary" :href="finalVideoUrl(run!.final_output)" target="_blank">播放完整影片</a>
            <button class="btn ghost" @click="showHistory = !showHistory">{{ showHistory ? '隐藏' : '查看' }}制作记录</button>
            <button class="btn ghost" @click="onRestart">重新制作一个版本</button>
          </div>
        </div>

        <!-- 失败 / 阻塞 / 取消页 -->
        <div v-else-if="view === 'failed' || view === 'blocked' || view === 'cancelled'" class="err-view">
          <div class="err-icon" :class="view">{{ view === 'blocked' ? '⛔' : view === 'cancelled' ? '⊘' : '✗' }}</div>
          <div class="err-title">{{ view === 'blocked' ? '完整制作已阻止' : view === 'cancelled' ? '已停止' : '完整制作失败' }}</div>
          <div v-if="run!.error" class="err-msg">{{ run!.error }}</div>
          <div v-if="run!.error_code === 'SOURCE_STRUCTURE_CHANGED'" class="err-hint">
            项目的故事、角色或镜头规划发生变化。请停止本次任务并重新确认制作计划。
          </div>
          <div v-else-if="run!.error_code === 'COMPOSE_FAILED'" class="err-hint">
            所有镜头视频已经生成完成，重新尝试只会重新合成，不会重新生成视频。
          </div>
        </div>

        <!-- 历史记录（轻量）-->
        <div v-if="showHistory && history.length" class="history-list">
          <div class="hist-title">完整制作记录</div>
          <div v-for="h in history" :key="h.run_id" class="hist-row" :class="{ active: run && h.run_id === run.run_id }"
               @click="selectRun(h.run_id)">
            <span class="hist-no">#{{ history.indexOf(h) + 1 }}</span>
            <span class="hist-status" :class="h.status">{{ statusTextFor(h.status) }}</span>
            <span v-if="h.final_duration_s" class="hist-dur">{{ formatDuration(h.final_duration_s) }}</span>
            <span class="hist-time">{{ formatTime(h.created_at) }}</span>
          </div>
        </div>
      </div>

      <footer class="pp-foot">
        <button class="btn ghost" @click="$emit('close')">{{ run && isRunActive(run) ? '收起（后台继续）' : '关闭' }}</button>
        <span class="foot-spacer"></span>
        <!-- 预检页 -->
        <button v-if="view === 'preflight' && preflight && preflight.ready" class="btn primary" :disabled="starting" @click="onStart">
          {{ starting ? '启动中…' : '开始完整制作' }}
        </button>
        <!-- 监控页 -->
        <button v-if="run && (run.status === 'running' || run.status === 'pausing')" class="btn ghost" @click="onPause"
                :disabled="run.status === 'pausing'">{{ run.status === 'pausing' ? '暂停中…' : '暂停' }}</button>
        <button v-if="run && (run.status === 'paused' || run.status === 'failed')" class="btn primary" :disabled="resuming" @click="onResume">
          {{ resuming ? '继续中…' : run.status === 'failed' && run.stage === 'compose' ? '重新合成' : '继续执行' }}
        </button>
        <button v-if="run && isRunActive(run)" class="btn danger" @click="onCancel">停止自动制作</button>
        <!-- 完成页 -->
        <button v-if="run && run.status === 'completed'" class="btn primary" @click="onRestart">重新制作一个版本</button>
        <!-- 阻塞页：只能停止旧任务后新建 -->
        <button v-if="run && run.status === 'blocked'" class="btn danger" @click="onCancel">停止本次任务</button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';
import { apiClient } from '@/api';
import type { ProjectProductionRun, ProjectProductionPreflight, ScenePreflightResult } from '@/api';

const { message, dialog } = createDiscreteApi(['message', 'dialog'], { configProviderProps: discreteConfigProviderProps });

const API_BASE = 'http://localhost:8000';

const props = defineProps<{ projectName: string }>();
const emit = defineEmits<{ (e: 'close'): void }>();

const run = ref<ProjectProductionRun | null>(null);
const preflight = ref<ProjectProductionPreflight | null>(null);
const loadingPreflight = ref(true);
const starting = ref(false);
const resuming = ref(false);
const respectLocked = ref(true);
const history = ref<ProjectProductionRun[]>([]);
const showHistory = ref(false);
const childProgress = ref<{ done: number; total: number; stageLabel: string } | null>(null);
let pollTimer: number | null = null;
let childTimer: number | null = null;

// ---- 视图模式 ----
const view = computed(() => {
  if (!run.value) return 'preflight';
  const st = run.value.status;
  if (st === 'composing') return 'composing';
  if (st === 'completed') return 'completed';
  if (st === 'failed') return 'failed';
  if (st === 'blocked') return 'blocked';
  if (st === 'cancelled') return 'cancelled';
  return 'monitor';   // pending/running/pausing/paused
});

// ---- 进度 ----
const totalShots = computed(() => run.value ? run.value.scenes.reduce((a, s) => a + s.shot_count, 0) : 0);
const completedShots = computed(() => {
  if (!run.value) return 0;
  let n = 0;
  for (const s of run.value.scenes) {
    if (s.status === 'completed') n += s.shot_count;
    else if (s.scene_id === run.value!.current_scene_id && childProgress.value) n += childProgress.value.done;
  }
  return n;
});
const overallPct = computed(() => totalShots.value ? Math.round(completedShots.value / totalShots.value * 100) : 0);

const notReadyCount = computed(() => preflight.value ? preflight.value.scenes.filter(s => !s.ready).length : 0);

const statusClass = computed(() => run.value ? run.value.status : '');
const statusText = computed(() => statusTextFor(run.value?.status));

function statusTextFor(st?: string): string {
  if (!st) return '';
  const m: Record<string, string> = {
    pending: '等待中', running: '制作中', pausing: '暂停中', paused: '已暂停',
    composing: '合成中', completed: '已完成', failed: '失败', blocked: '已阻塞', cancelled: '已取消',
  };
  return m[st] || st;
}

function isRunActive(r: ProjectProductionRun): boolean {
  return ['pending', 'running', 'pausing', 'paused', 'composing'].includes(r.status);
}

// ---- preflight 辅助 ----
function sceneReadyClass(s: ScenePreflightResult): string {
  return s.ready ? 'ready' : 'notready';
}
function sceneIcon(s: ScenePreflightResult): string {
  return s.ready ? '✓' : s.issues.includes('NO_SHOTS') ? '✕' : '⚠';
}
function issueLabel(iss: string): string {
  const m: Record<string, string> = {
    NO_SHOTS: '尚未创建镜头', NO_PRODUCTION_PLAN: '尚未制作规划', PLAN_NOT_APPROVED: '计划未确认',
    PLAN_STALE: '计划已过期', ACTIVE_SCENE_RUN: '该段有进行中的自动制作',
    LOCKED_SHOT_NO_FINAL: '锁定镜头无最终视频', LOCKED_SOURCE_NO_VIDEO_END: '锁定镜头无末帧',
    SHOT_NOT_IN_PLAN: '镜头不在计划中',
  };
  return m[iss] || iss;
}

// ---- monitor 辅助 ----
function sceneRowClass(s: { status: string }): string {
  return { completed: 'sr-done', running: 'sr-run', failed: 'sr-fail', blocked: 'sr-block', paused: 'sr-pause' }[s.status] || '';
}
function sceneRowIcon(s: { status: string }): string {
  return { completed: '✓', running: '⏳', failed: '✗', blocked: '⛔', paused: '⏸', cancelled: '⊘' }[s.status] || '○';
}
function sceneStatusLabel(st: string): string {
  return { pending: '等待', running: '制作中', paused: '已暂停', failed: '失败', blocked: '已阻塞', cancelled: '已取消' }[st] || st;
}

function finalVideoUrl(path: string): string {
  if (!path) return '';
  const norm = path.replace(/\\/g, '/');
  return `${API_BASE}/${norm.replace(/^.*projects\//, 'projects/')}`;
}
function formatDuration(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return `${m}:${sec.toString().padStart(2, '0')}`;
}
function formatTime(t: number): string {
  if (!t) return '';
  return new Date(t * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

// ---- 初始化 ----
async function init() {
  if (!props.projectName) return;
  try {
    history.value = await apiClient.listProductionRuns(props.projectName);
    const active = history.value.find(r => isRunActive(r));
    if (active) { run.value = active; startPolling(); startChildPolling(); }
    else if (history.value.length) {
      run.value = history.value[0];   // 显示最近的（completed/failed/...）
      if (run.value.status === 'completed') showHistory.value = true;
    }
  } catch { /* ignore */ }
  await refreshPreflight();
}

async function refreshPreflight() {
  loadingPreflight.value = true;
  try {
    preflight.value = await apiClient.getProductionPreflight(props.projectName, respectLocked.value);
  } catch { preflight.value = null; }
  loadingPreflight.value = false;
}

// ---- 轮询 ProjectRun ----
function startPolling() {
  stopPolling();
  pollTimer = window.setInterval(async () => {
    if (!run.value) return;
    try {
      const r = await apiClient.getProductionRun(props.projectName, run.value.run_id);
      run.value = r;
      if (!isRunActive(r)) { stopPolling(); stopChildPolling(); }
    } catch { /* ignore */ }
  }, 1500);
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// ---- 轮询当前 child（镜头级进度，§五十三）----
function startChildPolling() {
  stopChildPolling();
  childTimer = window.setInterval(async () => {
    if (!run.value || !run.value.current_child_run_id) { childProgress.value = null; return; }
    try {
      const child = await apiClient.getAutopilotRun(props.projectName, run.value.current_child_run_id);
      const done = child.shots.filter(s => s.status === 'completed' || s.status === 'skipped_locked').length;
      const cur = child.shots.find(s => s.status === 'running');
      const stageLabel = cur ? stageText(cur.stage) : '';
      childProgress.value = { done, total: child.shots.length, stageLabel };
    } catch { /* child 可能已结束 */ }
  }, 1500);
}
function stopChildPolling() {
  if (childTimer) { clearInterval(childTimer); childTimer = null; }
  childProgress.value = null;
}
function stageText(stage: string | null): string {
  if (!stage) return '';
  const m: Record<string, string> = {
    activate_plan: '激活计划', first_prompt: '生成首帧词', first_frame: '生成首帧',
    last_prompt: '生成尾帧词', last_frame: '生成尾帧', video_prompt: '生成视频词', video: '视频生成中', video_end: '提取末帧',
  };
  return m[stage] || stage;
}

// ---- 操作 ----
async function onStart() {
  starting.value = true;
  try {
    const r = await apiClient.startProduction(props.projectName, respectLocked.value);
    run.value = r; startPolling(); startChildPolling();
    message.success('完整制作已启动');
  } catch (e: any) {
    const detail = e?.message || '';
    if (detail.includes('已有') || e?.status === 409) {
      message.warning('这个项目已有进行中的完整制作任务。');
      history.value = await apiClient.listProductionRuns(props.projectName);
      const active = history.value.find(r => isRunActive(r));
      if (active) { run.value = active; startPolling(); startChildPolling(); }
    } else {
      message.error(`启动失败: ${detail}`);
    }
  } finally { starting.value = false; }
}

async function onPause() {
  if (!run.value) return;
  try { run.value = await apiClient.pauseProduction(props.projectName, run.value.run_id); }
  catch (e: any) { message.error(`暂停失败: ${e.message || e}`); }
}
async function onResume() {
  if (!run.value) return;
  resuming.value = true;
  try {
    run.value = await apiClient.resumeProduction(props.projectName, run.value.run_id);
    startPolling(); startChildPolling();
  } catch (e: any) { message.error(`继续失败: ${e.message || e}`); }
  finally { resuming.value = false; }
}
function onCancel() {
  if (!run.value) return;
  dialog.warning({
    title: '停止完整制作', content: '将停止完整制作。已生成的图片/视频会保留，不会删除。',
    positiveText: '停止', negativeText: '取消',
    onPositiveClick: async () => {
      if (!run.value) return;
      try { run.value = await apiClient.cancelProduction(props.projectName, run.value.run_id); }
      catch (e: any) { message.error(`停止失败: ${e.message || e}`); }
    },
  });
}
async function onRestart() {
  // 重新制作一个版本：创建新 ProjectRun（用当前 canonical Shot + approved Plans，§五十六）
  run.value = null;
  starting.value = true;
  try {
    const r = await apiClient.startProduction(props.projectName, respectLocked.value);
    run.value = r; startPolling(); startChildPolling();
  } catch (e: any) {
    message.error(`启动失败: ${e.message || e}`);
    await init();   // 恢复显示
  } finally { starting.value = false; }
}

async function selectRun(runId: string) {
  try {
    const r = await apiClient.getProductionRun(props.projectName, runId);
    run.value = r;
    if (isRunActive(r)) { startPolling(); startChildPolling(); }
    else { stopPolling(); stopChildPolling(); }
  } catch { /* ignore */ }
}

watch(() => props.projectName, (nv, ov) => { if (nv !== ov) emit('close'); });
onMounted(() => init());
onUnmounted(() => { stopPolling(); stopChildPolling(); });
</script>

<style lang="scss" scoped>
.pp-overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6); z-index: 1500;
  display: flex; align-items: center; justify-content: center; backdrop-filter: blur(4px);
}
.pp {
  width: 82vw; height: 84vh; max-width: 1400px; min-width: 720px;
  background: var(--bg-root); border: 1px solid var(--border); border-radius: 10px;
  display: flex; flex-direction: column; overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
}
.pp-head {
  display: flex; align-items: center; gap: 12px; padding: 14px 20px;
  border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.pp-title { font-size: 15px; font-weight: 600; color: var(--text-primary); }
.pp-status {
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  &.running { background: rgba(59, 130, 246, 0.15); color: #60a5fa; }
  &.composing { background: rgba(167, 139, 250, 0.15); color: #a78bfa; }
  &.completed { background: rgba(74, 222, 128, 0.15); color: #4ade80; }
  &.paused, &.pausing { background: rgba(251, 191, 36, 0.15); color: #fbbf24; }
  &.failed, &.blocked, &.cancelled { background: rgba(239, 68, 68, 0.15); color: #ef4444; }
}
.pp-sub { font-size: 12px; color: var(--text-muted); }
.pp-close {
  margin-left: auto; width: 28px; height: 28px; border-radius: 6px; border: none;
  background: none; color: var(--text-muted); font-size: 16px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
.pp-body { flex: 1; overflow-y: auto; padding: 18px 20px; }

/* preflight */
.pf-summary { display: flex; gap: 18px; font-size: 13px; color: var(--text-secondary); margin-bottom: 14px; padding: 10px 14px; background: var(--bg-surface); border-radius: 8px; }
.pf-scenes { display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px; }
.pf-scene {
  display: flex; align-items: flex-start; gap: 10px; padding: 10px 14px;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px;
  &.ready { border-left: 3px solid #4ade80; }
  &.notready { border-left: 3px solid #fbbf24; }
}
.pf-icon { font-size: 14px; flex-shrink: 0; }
.pf-info { flex: 1; }
.pf-name { font-size: 13px; color: var(--text-primary); margin-bottom: 3px; }
.pf-detail { font-size: 12px; color: var(--text-muted); display: flex; flex-wrap: wrap; gap: 8px; }
.pf-issue { color: #fbbf24; }
.pf-notready {
  background: rgba(251, 191, 36, 0.08); border: 1px solid #fbbf24; border-radius: 8px;
  padding: 12px 14px; font-size: 12px; color: #fbbf24; line-height: 1.6;
}
.confirm-info {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px; padding: 10px 14px;
  font-size: 12px; color: var(--text-muted); margin-bottom: 14px; p { margin: 4px 0; }
}
.lock-check { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary); }
.empty { text-align: center; padding: 30px; color: var(--text-muted); font-size: 13px; }

/* monitor */
.overall-prog { margin-bottom: 16px; }
.op-bar { height: 8px; background: var(--bg-surface); border-radius: 4px; overflow: hidden; border: 1px solid var(--border); }
.op-fill { height: 100%; background: var(--accent); transition: width 0.4s; }
.op-text { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
.scene-list { display: flex; flex-direction: column; gap: 8px; }
.scene-row {
  display: flex; align-items: flex-start; gap: 10px; padding: 10px 14px;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px;
  &.sr-done { border-left: 3px solid #4ade80; }
  &.sr-run { border-left: 3px solid #60a5fa; }
  &.sr-fail { border-left: 3px solid #ef4444; }
  &.sr-block { border-left: 3px solid #ef4444; opacity: 0.7; }
  &.sr-pause { border-left: 3px solid #fbbf24; }
}
.sr-icon { font-size: 14px; flex-shrink: 0; }
.sr-info { flex: 1; }
.sr-name { font-size: 13px; color: var(--text-primary); margin-bottom: 3px; }
.sr-detail { font-size: 12px; color: var(--text-muted); display: flex; flex-wrap: wrap; gap: 8px; }
.sr-err { color: #ef4444; }
.current-hint { margin-top: 14px; font-size: 12px; color: #60a5fa; }
.run-error {
  background: rgba(239, 68, 68, 0.1); border: 1px solid #ef4444; border-radius: 8px;
  padding: 10px 14px; font-size: 12px; color: #ef4444; margin-bottom: 14px;
}

/* compose */
.compose-view { text-align: center; padding: 50px 20px; }
.compose-spin { font-size: 40px; animation: spin 1.5s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.compose-text { font-size: 16px; color: var(--text-primary); margin-top: 14px; }
.compose-sub { font-size: 13px; color: var(--text-muted); margin-top: 6px; }

/* done */
.done-view { text-align: center; padding: 30px 20px; }
.done-icon { font-size: 40px; color: #4ade80; }
.done-title { font-size: 18px; color: var(--text-primary); margin: 10px 0; }
.done-stats { display: flex; justify-content: center; gap: 18px; font-size: 13px; color: var(--text-muted); margin-bottom: 16px; }
.done-video { max-width: 100%; max-height: 320px; border-radius: 8px; margin: 0 auto 16px; display: block; }
.done-actions { display: flex; justify-content: center; gap: 8px; flex-wrap: wrap; }

/* error views */
.err-view { text-align: center; padding: 50px 20px; }
.err-icon { font-size: 40px; &.blocked { color: #ef4444; } &.failed { color: #ef4444; } &.cancelled { color: var(--text-muted); } }
.err-title { font-size: 18px; color: var(--text-primary); margin: 10px 0; }
.err-msg { font-size: 13px; color: #ef4444; margin: 10px 20px; line-height: 1.6; }
.err-hint { font-size: 12px; color: #fbbf24; margin-top: 8px; line-height: 1.6; }

/* history */
.history-list { margin-top: 18px; border-top: 1px solid var(--border); padding-top: 14px; }
.hist-title { font-size: 12px; color: var(--text-muted); margin-bottom: 8px; }
.hist-row {
  display: flex; align-items: center; gap: 10px; padding: 6px 10px; border-radius: 6px; cursor: pointer;
  font-size: 12px; &:hover { background: var(--bg-hover); } &.active { background: var(--bg-surface); }
}
.hist-status {
  font-size: 11px; padding: 1px 6px; border-radius: 8px;
  &.completed { background: rgba(74, 222, 128, 0.15); color: #4ade80; }
  &.running, &.composing { background: rgba(59, 130, 246, 0.15); color: #60a5fa; }
  &.failed, &.blocked { background: rgba(239, 68, 68, 0.15); color: #ef4444; }
  &.cancelled { background: var(--bg-surface); color: var(--text-muted); }
}
.hist-dur { color: var(--text-muted); }
.hist-time { margin-left: auto; color: var(--text-faint); }

.pp-foot {
  display: flex; align-items: center; gap: 8px; padding: 14px 20px;
  border-top: 1px solid var(--border); flex-shrink: 0;
}
.foot-spacer { flex: 1; }
.btn {
  padding: 8px 18px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none; text-decoration: none;
  display: inline-flex; align-items: center; justify-content: center;
  &:disabled { opacity: 0.4; cursor: not-allowed; }
}
.btn.ghost { background: var(--bg-surface); color: var(--text-muted); border: 1px solid var(--border); &:hover:not(:disabled) { background: var(--bg-hover); } }
.btn.primary { background: var(--accent); color: var(--accent-text, #fff); &:hover:not(:disabled) { background: var(--accent-hover); } }
.btn.danger { background: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid #ef4444; &:hover:not(:disabled) { background: rgba(239, 68, 68, 0.25); } }
</style>
