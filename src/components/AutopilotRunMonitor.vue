<template>
  <div class="ap-overlay" @click.self="$emit('close')">
    <div class="ap">
      <header class="ap-head">
        <span class="ap-title">自动制作 · 故事段 {{ sceneId }}</span>
        <span class="ap-status" :class="statusClass" v-if="run">{{ statusText }}</span>
        <span class="ap-sub" v-if="run">{{ completedCount }}/{{ run.shots.length }} 镜头完成</span>
        <button class="ap-close" @click="$emit('close')">✕</button>
      </header>

      <div class="ap-body">
        <!-- 开始确认页（无 Run 时）-->
        <div v-if="!run" class="confirm-view">
          <div class="scene-box">
            <div class="se-label">故事段</div>
            <div class="se-text">{{ sceneSummary || '（未填写故事）' }}</div>
            <div class="se-shots">本次将处理 {{ shots.length }} 个镜头</div>
          </div>

          <div v-if="loadingPlan" class="empty">加载制作计划…</div>
          <div v-else-if="!plan" class="empty warn">
            没有已确认的制作计划。请先在「AI 制作规划」中确认计划后再开始自动制作。
          </div>
          <div v-else-if="plan.is_stale" class="empty warn">
            制作计划已过期（{{ plan.stale_reason }}）。请重新生成并确认计划。
          </div>
          <template v-else>
            <div class="shot-preview-list">
              <div v-for="(sh, i) in plan.shots" :key="sh.shot_id" class="shot-preview">
                <span class="sp-no">{{ i + 1 }}</span>
                <span class="sp-id">{{ sh.shot_id }}</span>
                <span class="sp-strat">{{ openLabel(sh.opening_source) }} · {{ linkLabel(sh.link_mode) }}</span>
              </div>
            </div>
            <div class="confirm-info">
              <p><b>自动选择：</b>系统将在成功候选中自动采用一个结果，整个流程不会中途等待人工选图。</p>
              <p><b>视频：</b>第一版采用安全的逐镜生成（不并发）。</p>
            </div>
            <label class="lock-check">
              <input type="checkbox" v-model="respectLocked" />
              保留已锁定镜头（跳过重新生成）
            </label>
          </template>
        </div>

        <!-- Run Monitor（有 Run 时）-->
        <div v-else class="monitor-view">
          <div v-if="run.error" class="run-error">
            {{ run.error }}
            <span v-if="run.error_code === 'STRUCTURE_CHANGED'" class="err-code">故事段或镜头规划已变化，请重新确认计划。</span>
          </div>

          <div class="shot-prog-list">
            <div v-for="st in run.shots" :key="st.shot_id" class="shot-prog" :class="shotClass(st)">
              <div class="sp-head">
                <span class="sp-icon">{{ shotIcon(st) }}</span>
                <span class="sp-no">{{ st.index + 1 }}</span>
                <span class="sp-id">{{ st.shot_id }}</span>
                <span class="sp-status">{{ shotStatusText(st) }}</span>
              </div>
              <div class="stage-row" v-if="!isSkipped(st)">
                <span v-for="s in stagesFor(st.shot_id)" :key="s" class="stage-chip"
                      :class="stageClass(st, s)">{{ stageLabel(s) }}</span>
              </div>
              <div v-else class="skipped-note">已锁定 · 跳过重新生成（沿用现有最终视频）</div>
              <div v-if="st.warnings.length" class="shot-warn">
                <span v-for="(w, i) in st.warnings" :key="i">⚠ {{ w }}</span>
              </div>
              <div v-if="st.error" class="shot-err">{{ st.error }}</div>
            </div>
          </div>
        </div>
      </div>

      <footer class="ap-foot">
        <button class="btn ghost" @click="$emit('close')">{{ run && !isRunActive(run) ? '关闭' : '收起（后台继续）' }}</button>
        <span class="foot-spacer"></span>
        <!-- 确认页：开始 -->
        <button v-if="!run && plan && !plan.is_stale" class="btn primary" :disabled="starting" @click="onStart">
          {{ starting ? '启动中…' : '开始自动制作' }}
        </button>
        <!-- Monitor：暂停/继续/停止 -->
        <button v-if="run && run.status === 'running'" class="btn ghost" @click="onPause">暂停</button>
        <button v-if="run && (run.status === 'paused' || run.status === 'failed' || run.status === 'blocked')"
                class="btn primary" :disabled="resuming" @click="onResume">
          {{ resuming ? '继续中…' : '继续执行' }}
        </button>
        <button v-if="run && isRunActive(run)" class="btn danger" @click="onCancel">停止自动制作</button>
        <button v-if="run && (run.status === 'completed' || run.status === 'cancelled')" class="btn primary" @click="onRestart">
          重新自动制作
        </button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';
import { apiClient } from '@/api';
import type { SceneAutopilotRun, ShotExecutionState, SceneProductionPlan } from '@/api';

const { message, dialog } = createDiscreteApi(['message', 'dialog'], { configProviderProps: discreteConfigProviderProps });

interface ShotInfo { shot_id: string; description: string; }

const props = defineProps<{
  projectName: string;
  sceneId: string;
  sceneSummary: string;
  shots: ShotInfo[];
}>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'run-changed', runId: string | null): void }>();

const run = ref<SceneAutopilotRun | null>(null);
const plan = ref<SceneProductionPlan | null>(null);
const loadingPlan = ref(true);
const starting = ref(false);
const resuming = ref(false);
const respectLocked = ref(true);
let pollTimer: number | null = null;

const STAGES_ALL = ['activate_plan', 'first_prompt', 'first_frame', 'last_prompt', 'last_frame', 'video_prompt', 'video', 'video_end'];

const completedCount = computed(() => run.value ? run.value.shots.filter(s => s.status === 'completed' || s.status === 'skipped_locked').length : 0);

const statusClass = computed(() => run.value ? run.value.status : '');
const statusText = computed(() => {
  const m: Record<string, string> = {
    pending: '等待中', running: '进行中', pausing: '暂停中', paused: '已暂停',
    blocked: '已阻塞', failed: '失败', completed: '已完成', cancelled: '已取消',
  };
  return m[run.value?.status || ''] || run.value?.status || '';
});

function isRunActive(r: SceneAutopilotRun): boolean {
  return ['pending', 'running', 'pausing', 'paused'].includes(r.status);
}

// ---- 计划驱动的 stage 序列（与 build_stage_sequence 一致，§十一）----
function stagesFor(shotId: string): string[] {
  if (!plan.value) return STAGES_ALL;
  const sh = (plan.value.shots as any[]).find(s => s.shot_id === shotId);
  if (!sh) return STAGES_ALL;
  const stages = ['activate_plan'];
  if (sh.generate_first_frame) stages.push('first_prompt', 'first_frame');
  if (sh.generate_last_frame) stages.push('last_prompt', 'last_frame');
  stages.push('video_prompt', 'video', 'video_end');
  return stages;
}

function stageLabel(s: string): string {
  const m: Record<string, string> = {
    activate_plan: '激活', first_prompt: '首帧词', first_frame: '首帧',
    last_prompt: '尾帧词', last_frame: '尾帧', video_prompt: '视频词', video: '视频', video_end: '末帧',
  };
  return m[s] || s;
}
function stageClass(st: ShotExecutionState, s: string): string {
  if (st.completed_stages.includes(s)) return 'done';
  if (st.stage === s) return 'current';
  return 'pending';
}

function openLabel(o: string): string {
  return { independent: '独立生成', reference_previous: '参考上一镜', inherit_previous: '沿用上一镜末帧' }[o] || o;
}
function linkLabel(l: string): string {
  return { i2v: '单图视频', keyframes: '首尾帧过渡' }[l] || l;
}

function shotClass(st: ShotExecutionState): string {
  return { completed: 'st-done', skipped_locked: 'st-skip', failed: 'st-fail', running: 'st-run' }[st.status] || '';
}
function shotIcon(st: ShotExecutionState): string {
  return { completed: '✓', skipped_locked: '⊘', failed: '✗', running: '⏳' }[st.status] || '○';
}
function shotStatusText(st: ShotExecutionState): string {
  return { pending: '等待', waiting_dependency: '等待上一镜', running: '制作中',
           skipped_locked: '已锁定', failed: '失败', completed: '完成' }[st.status] || st.status;
}
function isSkipped(st: ShotExecutionState): boolean {
  return st.status === 'skipped_locked';
}

// ---- 初始化：查现有 Run，没有则加载 plan 进确认页 ----
async function init() {
  if (!props.projectName || !props.sceneId) return;
  try {
    const runs = await apiClient.listAutopilotRuns(props.projectName, props.sceneId);
    const active = runs.find(r => isRunActive(r));
    if (active) { run.value = active; startPolling(); }
    else if (runs.length) { run.value = runs[0]; }   // 显示最近的（completed/cancelled）
  } catch { /* ignore */ }
  try {
    plan.value = await apiClient.getProductionPlan(props.projectName, props.sceneId);
  } catch { plan.value = null; }
  loadingPlan.value = false;
}

// ---- 轮询 Run（§四十五：1-2s GET）----
function startPolling() {
  stopPolling();
  pollTimer = window.setInterval(async () => {
    if (!run.value) return;
    try {
      const r = await apiClient.getAutopilotRun(props.projectName, run.value.run_id);
      run.value = r;
      if (!isRunActive(r)) stopPolling();
    } catch { /* ignore transient */ }
  }, 1500);
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// ---- 开始 ----
async function onStart() {
  starting.value = true;
  try {
    const r = await apiClient.startAutopilot(props.projectName, props.sceneId, respectLocked.value);
    run.value = r;
    startPolling();
    message.success('自动制作已启动');
    emit('run-changed', r.run_id);
  } catch (e: any) {
    const msg = e.message || '';
    if (msg.includes('已有')) {
      message.warning('这个故事段已有进行中的自动制作任务。');
      const runs = await apiClient.listAutopilotRuns(props.projectName, props.sceneId);
      const active = runs.find(r => isRunActive(r));
      if (active) { run.value = active; startPolling(); }
    } else {
      message.error(`启动失败: ${msg}`);
    }
  } finally {
    starting.value = false;
  }
}

async function onPause() {
  if (!run.value) return;
  try { run.value = await apiClient.pauseAutopilot(props.projectName, run.value.run_id); }
  catch (e: any) { message.error(`暂停失败: ${e.message || e}`); }
}
async function onResume() {
  if (!run.value) return;
  resuming.value = true;
  try {
    run.value = await apiClient.resumeAutopilot(props.projectName, run.value.run_id);
    startPolling();
  } catch (e: any) { message.error(`继续失败: ${e.message || e}`); }
  finally { resuming.value = false; }
}
async function onCancel() {
  if (!run.value) return;
  dialog.warning({
    title: '停止自动制作', content: '将停止自动制作。已生成的图片/视频会保留，不会删除。',
    positiveText: '停止', negativeText: '取消',
    onPositiveClick: async () => {
      if (!run.value) return;
      try { run.value = await apiClient.cancelAutopilot(props.projectName, run.value.run_id); }
      catch (e: any) { message.error(`停止失败: ${e.message || e}`); }
    },
  });
}
async function onRestart() {
  // 重新自动制作：创建新 Run（不 resume 旧 completed）
  run.value = null;
  starting.value = true;
  try {
    const r = await apiClient.startAutopilot(props.projectName, props.sceneId, respectLocked.value);
    run.value = r; startPolling();
    emit('run-changed', r.run_id);
  } catch (e: any) {
    message.error(`启动失败: ${e.message || e}`);
  } finally { starting.value = false; }
}

watch(() => props.projectName, (nv, ov) => { if (nv !== ov) emit('close'); });
watch(() => props.sceneId, (nv, ov) => { if (nv !== ov) emit('close'); });
onMounted(() => init());
onUnmounted(() => stopPolling());
</script>

<style lang="scss" scoped>
.ap-overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6); z-index: 1500;
  display: flex; align-items: center; justify-content: center; backdrop-filter: blur(4px);
}
.ap {
  width: 78vw; height: 84vh; max-width: 1300px; min-width: 680px;
  background: var(--bg-root); border: 1px solid var(--border); border-radius: 10px;
  display: flex; flex-direction: column; overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
}
.ap-head {
  display: flex; align-items: center; gap: 12px; padding: 14px 20px;
  border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.ap-title { font-size: 15px; font-weight: 600; color: var(--text-primary); }
.ap-status {
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  &.running { background: rgba(59, 130, 246, 0.15); color: #60a5fa; }
  &.completed { background: rgba(74, 222, 128, 0.15); color: #4ade80; }
  &.paused, &.pausing { background: rgba(251, 191, 36, 0.15); color: #fbbf24; }
  &.failed, &.blocked, &.cancelled { background: rgba(239, 68, 68, 0.15); color: #ef4444; }
}
.ap-sub { font-size: 12px; color: var(--text-muted); }
.ap-close {
  margin-left: auto; width: 28px; height: 28px; border-radius: 6px; border: none;
  background: none; color: var(--text-muted); font-size: 16px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
.ap-body { flex: 1; overflow-y: auto; padding: 18px 20px; }

.scene-box {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin-bottom: 16px;
}
.se-label { font-size: 11px; color: var(--text-muted); margin-bottom: 6px; }
.se-text { font-size: 13px; color: var(--text-secondary); line-height: 1.6; margin-bottom: 8px; }
.se-shots { font-size: 12px; color: var(--text-muted); }

.shot-preview-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }
.shot-preview {
  display: flex; align-items: center; gap: 10px; padding: 8px 12px;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px;
}
.sp-no { width: 20px; height: 20px; border-radius: 50%; background: var(--accent); color: #fff; font-size: 10px; font-weight: 600; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.sp-id { font-size: 12px; color: var(--text-muted); font-family: monospace; }
.sp-strat { font-size: 12px; color: var(--text-secondary); margin-left: auto; }

.confirm-info {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px; padding: 10px 14px;
  font-size: 12px; color: var(--text-muted); margin-bottom: 14px;
  p { margin: 4px 0; }
}
.lock-check { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary); }

.empty { text-align: center; padding: 30px; color: var(--text-muted); font-size: 13px; }
.empty.warn { color: #fbbf24; }

.run-error {
  background: rgba(239, 68, 68, 0.1); border: 1px solid #ef4444; border-radius: 8px;
  padding: 10px 14px; font-size: 12px; color: #ef4444; margin-bottom: 14px;
  .err-code { display: block; margin-top: 4px; color: #fbbf24; }
}

.shot-prog-list { display: flex; flex-direction: column; gap: 10px; }
.shot-prog {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px;
  &.st-done { border-left: 3px solid #4ade80; }
  &.st-skip { border-left: 3px solid #fbbf24; opacity: 0.7; }
  &.st-fail { border-left: 3px solid #ef4444; }
  &.st-run { border-left: 3px solid #60a5fa; }
}
.sp-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.sp-icon { font-size: 14px; }
.sp-status { margin-left: auto; font-size: 11px; color: var(--text-muted); }
.stage-row { display: flex; flex-wrap: wrap; gap: 6px; }
.stage-chip {
  font-size: 11px; padding: 2px 8px; border-radius: 8px; border: 1px solid var(--border);
  &.done { background: rgba(74, 222, 128, 0.12); border-color: #4ade80; color: #4ade80; }
  &.current { background: rgba(59, 130, 246, 0.12); border-color: #60a5fa; color: #60a5fa; }
  &.pending { color: var(--text-faint); }
}
.skipped-note { font-size: 11px; color: #fbbf24; }
.shot-warn { margin-top: 6px; font-size: 11px; color: #fbbf24; span { display: block; } }
.shot-err { margin-top: 6px; font-size: 11px; color: #ef4444; }

.ap-foot {
  display: flex; align-items: center; gap: 8px; padding: 14px 20px;
  border-top: 1px solid var(--border); flex-shrink: 0;
}
.foot-spacer { flex: 1; }
.btn {
  padding: 8px 18px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none;
  &:disabled { opacity: 0.4; cursor: not-allowed; }
}
.btn.ghost { background: var(--bg-surface); color: var(--text-muted); border: 1px solid var(--border); &:hover:not(:disabled) { background: var(--bg-hover); } }
.btn.primary { background: var(--accent); color: var(--accent-text, #fff); &:hover:not(:disabled) { background: var(--accent-hover); } }
.btn.danger { background: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid #ef4444; &:hover:not(:disabled) { background: rgba(239, 68, 68, 0.25); } }
</style>
