<template>
  <div class="fp-overlay" @click.self="$emit('close')">
    <div class="fp">
      <header class="fp-head">
        <span class="fp-title">✨ AI 完整制作</span>
        <span class="fp-status" :class="statusClass" v-if="run">{{ statusText }}</span>
        <span class="fp-sub" v-if="run && run.stage === 'prepare'">阶段 1 / 2 · 准备分镜与制作方案</span>
        <span class="fp-sub" v-else-if="run && run.stage === 'production'">阶段 2 / 2 · 完整影片制作</span>
        <button class="fp-close" @click="$emit('close')">✕</button>
      </header>

      <div class="fp-body">
        <!-- Inspect / 确认页（无 Run 或查看准备情况）-->
        <div v-if="view === 'inspect'" class="inspect-view">
          <div v-if="loadingInspect" class="empty">检查项目准备情况…</div>
          <template v-else-if="inspect">
            <div class="pf-summary">
              <span>故事段 {{ inspect.scene_count }}</span>
              <span>现有镜头 {{ inspect.shot_count }}</span>
            </div>

            <!-- blockers（NO_SCENES / MISSING_PROJECT_CHARACTER / ACTIVE_*_RUN）-->
            <div v-if="inspect.blockers.length" class="pf-blockers">
              <div v-for="b in inspect.blockers" :key="b" class="blocker-row">✕ {{ blockerLabel(b) }}</div>
            </div>

            <!-- 各 Scene 状态 -->
            <div class="pf-scenes">
              <div v-for="(s, i) in inspect.scenes" :key="s.scene_id" class="pf-scene" :class="sceneStateClass(s)">
                <span class="pf-icon">{{ sceneIcon(s) }}</span>
                <div class="pf-info">
                  <div class="pf-name">故事段 {{ i + 1 }} · {{ s.scene_id }}</div>
                  <div class="pf-detail">
                    <span v-if="s.shot_count">{{ s.shot_count }} 个镜头</span>
                    <span v-if="s.state === 'READY'">已准备</span>
                    <span v-else-if="s.state === 'NEED_DIRECTOR'">{{ s.has_valid_director_draft ? '将采用当前分镜草稿' : 'AI 将设计分镜' }}</span>
                    <span v-else-if="s.state === 'NEED_PRODUCTION_PLAN'">{{ planStateLabel(s.plan_state) }}</span>
                    <span v-else-if="s.state === 'BLOCKED'">{{ s.detail }}</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- character warnings（§五十三）-->
            <div v-if="charWarnings.length" class="pf-warnings">
              <div v-for="w in charWarnings" :key="w" class="warn-row">⚠ {{ w }} 尚无三视图，本次仍会使用文字设定生成，人物一致性可能较低。</div>
            </div>

            <template v-if="!inspect.ready">
              <div class="pf-notready">
                存在阻塞项，暂时无法开始 AI 完整制作。请先解决上述问题。
              </div>
            </template>
            <template v-else>
              <div class="confirm-info">
                <p v-if="needDirectorCount">AI 将自动完成 {{ needDirectorCount }} 个故事段的分镜设计。</p>
                <p v-if="needPlannerCount">AI 将自动完成 {{ needPlannerCount }} 个故事段的制作规划。</p>
                <p>准备完成后将自动开始完整影片生成。</p>
                <p>已有且仍有效的 AI 草稿将优先采用。</p>
                <p>整个流程不会中途等待人工确认。</p>
              </div>
              <label class="lock-check">
                <input type="checkbox" v-model="respectLocked" />
                保留已锁定镜头（沿用现有最终视频）
              </label>
            </template>
          </template>
        </div>

        <!-- 准备中（阶段 1/2）-->
        <div v-else-if="view === 'preparing'" class="prep-view">
          <div v-if="run!.error" class="run-error">{{ run!.error }}</div>
          <div class="scene-list">
            <div v-for="(s, i) in run!.scenes" :key="s.scene_id" class="scene-row" :class="prepRowClass(s)">
              <span class="sr-icon">{{ prepRowIcon(s) }}</span>
              <div class="sr-info">
                <div class="sr-name">故事段 {{ i + 1 }} · {{ s.scene_id }}</div>
                <div class="sr-detail">
                  <span>{{ prepDetail(s) }}</span>
                  <span v-if="s.error" class="sr-err">{{ s.error }}</span>
                </div>
              </div>
            </div>
          </div>
          <div class="current-hint" v-if="run!.current_scene_id">
            当前：故事段 {{ run!.current_scene_id }} · {{ actionLabel(run!.current_action) }}
          </div>
        </div>

        <!-- 影片制作中（阶段 2/2，嵌入 production child 高层进度，§五十五）-->
        <div v-else-if="view === 'producing'" class="prod-view">
          <div v-if="run!.error" class="run-error">{{ run!.error }}</div>
          <div class="overall-prog">
            <div class="op-bar"><div class="op-fill" :style="{ width: overallPct + '%' }"></div></div>
            <div class="op-text">{{ completedShots }} / {{ totalShots }} 镜头</div>
          </div>
          <div class="scene-list">
            <div v-for="(s, i) in run!.scenes" :key="s.scene_id" class="scene-row" :class="prodRowClass(s)">
              <span class="sr-icon">{{ prodRowIcon(s) }}</span>
              <div class="sr-info">
                <div class="sr-name">故事段 {{ i + 1 }} · {{ s.scene_id }}（{{ sceneShotCount(s) || '—' }} 镜）</div>
                <div class="sr-detail">
                  <span v-if="s.scene_id === run!.current_scene_id && childProgress">{{ childProgress.done }} / {{ childProgress.total }} · {{ childProgress.stageLabel || '处理中' }}</span>
                  <span v-else>{{ prodSceneLabel(s) }}</span>
                </div>
              </div>
            </div>
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
          <div class="done-title">AI 完整制作完成</div>
          <div class="done-stats">
            <span>{{ totalShots }} 个镜头</span>
            <span>{{ run!.scenes.length }} 个故事段</span>
          </div>
          <video v-if="run!.final_output" class="done-video" :src="finalVideoUrl(run!.final_output)" controls></video>
          <div class="done-actions">
            <a v-if="run!.final_output" class="btn primary" :href="finalVideoUrl(run!.final_output)" target="_blank">播放完整影片</a>
            <button class="btn ghost" @click="onRestart">重新制作一个版本</button>
          </div>
        </div>

        <!-- 失败 / 阻塞 / 取消页 -->
        <div v-else-if="view === 'failed' || view === 'blocked' || view === 'cancelled'" class="err-view">
          <div class="err-icon" :class="view">{{ view === 'blocked' ? '⛔' : view === 'cancelled' ? '⊘' : '✗' }}</div>
          <div class="err-title">{{ view === 'blocked' ? 'AI 完整制作已阻止' : view === 'cancelled' ? '已停止' : 'AI 完整制作失败' }}</div>
          <div v-if="run!.current_scene_id && (run!.status === 'failed' || run!.status === 'blocked')" class="err-scene">
            故事段 {{ run!.current_scene_id }} · {{ actionLabel(run!.current_action) }}
          </div>
          <div v-if="run!.error" class="err-msg">{{ run!.error }}</div>
          <div v-if="run!.error_code === 'EXTERNAL_SOURCE_CHANGED' || run!.error_code === 'DIRECTOR_APPLY_CONFLICT'" class="err-hint">
            项目的故事、角色或镜头规划发生变化。请停止本次任务并重新确认后开始新的 AI 完整制作。
          </div>
          <div v-else-if="run!.error_code === 'PRODUCTION_PLAN_CHANGED_EXTERNALLY'" class="err-hint">
            制作计划在准备过程中被外部修改。请停止后重新开始。
          </div>
          <div v-else-if="run!.status === 'cancelled'" class="err-hint">
            已完成的分镜和制作计划已保留。
          </div>
        </div>
      </div>

      <footer class="fp-foot">
        <button class="btn ghost" @click="$emit('close')">{{ run && isRunActive(run) ? '收起（后台继续）' : '关闭' }}</button>
        <span class="foot-spacer"></span>
        <button v-if="view === 'inspect' && inspect && inspect.ready" class="btn primary" :disabled="starting" @click="onStart">
          {{ starting ? '启动中…' : '开始 AI 完整制作' }}
        </button>
        <button v-if="run && (isPreparing(run) || run.status === 'pausing')" class="btn ghost" @click="onPause"
                :disabled="run.status === 'pausing'">{{ run.status === 'pausing' ? '暂停中…' : '暂停' }}</button>
        <button v-if="run && (run.status === 'paused' || run.status === 'failed')" class="btn primary" :disabled="resuming" @click="onResume">
          {{ resuming ? '继续中…' : '继续执行' }}
        </button>
        <button v-if="run && isRunActive(run)" class="btn danger" @click="onCancel">停止</button>
        <button v-if="run && run.status === 'blocked'" class="btn danger" @click="onCancel">停止本次任务</button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';
import { apiClient } from '@/api';
import type { FullProjectRun, FullProjectInspect, FullProjectSceneState, FullProjectSceneInspect, ProjectProductionRun } from '@/api';

const { message, dialog } = createDiscreteApi(['message', 'dialog'], { configProviderProps: discreteConfigProviderProps });

const API_BASE = 'http://localhost:8000';

const props = defineProps<{ projectName: string }>();
const emit = defineEmits<{ (e: 'close'): void }>();

const run = ref<FullProjectRun | null>(null);
const inspect = ref<FullProjectInspect | null>(null);
const loadingInspect = ref(true);
const starting = ref(false);
const resuming = ref(false);
const respectLocked = ref(true);
const childProgress = ref<{ done: number; total: number; stageLabel: string } | null>(null);
const prodChild = ref<ProjectProductionRun | null>(null);
let pollTimer: number | null = null;
let childTimer: number | null = null;

// ---- 视图模式 ----
const view = computed(() => {
  if (!run.value) return 'inspect';
  const st = run.value.status;
  const stage = run.value.stage;
  if (stage === 'production') {
    // production 阶段：child composing -> composing；child completed -> completed；否则 producing
    if (st === 'completed') return 'completed';
    if (st === 'failed') return 'failed';
    if (st === 'blocked') return 'blocked';
    if (st === 'cancelled') return 'cancelled';
    if (st === 'paused') return 'producing';   // producing 但暂停
    if (prodChild.value?.status === 'composing') return 'composing';   // child 在合成 -> 合成页（§五十五）
    return 'producing';
  }
  if (st === 'completed') return 'completed';
  if (st === 'failed') return 'failed';
  if (st === 'blocked') return 'blocked';
  if (st === 'cancelled') return 'cancelled';
  return 'preparing';   // pending/preparing/pausing/paused（prepare 阶段）
});

// ---- 进度（producing 阶段，从 production child 聚合，§五十四）----
// 🔴 必须用 child ProjectProductionRun 的 shot_count（镜头级，与 AI-5A 一致）。
// parent 的 created_shot_ids 只记 AI 新建镜头，已 ready 的旧镜头为 0，会让总镜头数和进度百分比失真。
const totalShots = computed(() => {
  if (prodChild.value) {
    return prodChild.value.scenes.reduce((a, s) => a + (s.shot_count || 0), 0);
  }
  // child 未就绪（刚进 producing 或仍处 prepare 阶段）：回落 preparation 已知镜头数
  return run.value ? run.value.scenes.reduce((a, s) => a + (s.created_shot_ids.length || 0), 0) : 0;
});
const completedShots = computed(() => {
  if (!prodChild.value) return 0;
  return prodChild.value.scenes.reduce((n, s) => s.status === 'completed' ? n + (s.shot_count || 0) : n, 0);
});
const overallPct = computed(() => totalShots.value ? Math.round(completedShots.value / totalShots.value * 100) : 0);

const needDirectorCount = computed(() => inspect.value ? inspect.value.scenes.filter(s => s.state === 'NEED_DIRECTOR').length : 0);
const needPlannerCount = computed(() => inspect.value ? inspect.value.scenes.filter(s => s.state === 'NEED_PRODUCTION_PLAN').length : 0);
const charWarnings = computed(() => {
  if (!inspect.value) return [];
  const names: string[] = [];
  for (const w of inspect.value.warnings) {
    const m = w.match(/CHARACTER_WITHOUT_VIEWS:(.+)/);
    if (m) names.push(m[1]);
  }
  return [...new Set(names)];
});

const statusClass = computed(() => run.value ? run.value.status : '');
const statusText = computed(() => statusTextFor(run.value?.status));

function statusTextFor(st?: string): string {
  if (!st) return '';
  const m: Record<string, string> = {
    pending: '等待中', preparing: '准备中', pausing: '暂停中', paused: '已暂停',
    producing: '制作中', completed: '已完成', failed: '失败', blocked: '已阻塞', cancelled: '已取消',
  };
  return m[st] || st;
}

function isRunActive(r: FullProjectRun): boolean {
  return ['pending', 'preparing', 'pausing', 'paused', 'producing'].includes(r.status);
}
function isPreparing(r: FullProjectRun): boolean {
  return ['preparing', 'paused'].includes(r.status) && r.stage !== 'production';
}

// ---- inspect 辅助 ----
function sceneStateClass(s: FullProjectSceneInspect): string {
  return { READY: 'ready', NEED_DIRECTOR: 'need', NEED_PRODUCTION_PLAN: 'need', BLOCKED: 'block' }[s.state] || '';
}
function sceneIcon(s: FullProjectSceneInspect): string {
  return { READY: '✓', NEED_DIRECTOR: '○', NEED_PRODUCTION_PLAN: '○', BLOCKED: '✕' }[s.state] || '○';
}
function planStateLabel(ps: string | null): string {
  if (ps === 'draft') return '将采用当前未确认制作计划';
  if (ps === 'stale') return '制作计划已过期，AI 将重新生成';
  if (ps === 'none') return 'AI 将生成制作计划';
  return 'AI 将生成制作计划';
}
function blockerLabel(b: string): string {
  if (b === 'NO_SCENES') return '当前项目还没有故事段。请先使用 AI 整理故事。';
  if (b.startsWith('MISSING_PROJECT_CHARACTER')) return `故事段引用了不存在的角色：${b.split(':')[1]}`;
  if (b === 'ACTIVE_FULL_PROJECT_RUN') return '已有进行中的 AI 完整制作任务';
  if (b === 'ACTIVE_PROJECT_PRODUCTION_RUN') return '已有进行中的完整影片制作任务（AI-5A）';
  if (b.startsWith('ACTIVE_SCENE_AUTOPILOT_RUN')) return `故事段 ${b.split(':')[1]} 有进行中的自动制作`;
  return b;
}

// ---- preparing 辅助 ----
function prepRowClass(s: FullProjectSceneState): string {
  return { ready: 'sr-done', preparing: 'sr-run', failed: 'sr-fail', blocked: 'sr-block' }[s.status] || '';
}
function prepRowIcon(s: FullProjectSceneState): string {
  return { ready: '✓', preparing: '⏳', failed: '✗', blocked: '⛔' }[s.status] || '○';
}
function prepDetail(s: FullProjectSceneState): string {
  if (s.status === 'ready') return s.used_existing_director_draft ? '已准备（复用分镜草稿）' : '已准备';
  if (s.status === 'preparing') return actionLabel(s.action);
  if (s.status === 'failed') return '失败';
  if (s.status === 'blocked') return '已阻塞';
  return '等待';
}
function actionLabel(a: string | null): string {
  if (!a) return '';
  const m: Record<string, string> = {
    director_plan: 'AI 设计分镜', director_apply: '应用分镜方案',
    planner_plan: 'AI 生成制作计划', planner_approve: '确认制作计划',
    done: '已完成',
  };
  return m[a] || a;
}

// ---- producing 辅助 ----
function prodRowClass(s: FullProjectSceneState): string {
  // producing 阶段用 child 的 scene 状态
  const cs = prodChild.value?.scenes.find(p => p.scene_id === s.scene_id);
  if (!cs) return s.status === 'ready' ? 'sr-done' : '';
  return { completed: 'sr-done', running: 'sr-run', failed: 'sr-fail', blocked: 'sr-block', paused: 'sr-pause' }[cs.status] || '';
}
function prodRowIcon(s: FullProjectSceneState): string {
  const cs = prodChild.value?.scenes.find(p => p.scene_id === s.scene_id);
  if (!cs) return s.status === 'ready' ? '✓' : '○';
  return { completed: '✓', running: '⏳', failed: '✗', blocked: '⛔', paused: '⏸', cancelled: '⊘' }[cs.status] || '○';
}
function prodSceneLabel(s: FullProjectSceneState): string {
  const cs = prodChild.value?.scenes.find(p => p.scene_id === s.scene_id);
  if (!cs) return s.status === 'ready' ? '待制作' : '';
  return { completed: `${cs.shot_count} / ${cs.shot_count}`, running: '制作中', failed: '失败', blocked: '已阻塞', paused: '已暂停', cancelled: '已取消' }[cs.status] || '';
}
function sceneShotCount(s: FullProjectSceneState): number {
  // producing 阶段优先用 child 的 shot_count（含已 ready 的旧镜头），否则回落 AI 新建镜头数
  const cs = prodChild.value?.scenes.find(p => p.scene_id === s.scene_id);
  if (cs) return cs.shot_count || 0;
  return s.created_shot_ids.length || 0;
}

function finalVideoUrl(path: string): string {
  if (!path) return '';
  const norm = path.replace(/\\/g, '/');
  return `${API_BASE}/${norm.replace(/^.*projects\//, 'projects/')}`;
}

// ---- 初始化 ----
async function init() {
  if (!props.projectName) return;
  try {
    const history = await apiClient.listFullProductionRuns(props.projectName);
    const active = history.find(r => isRunActive(r));
    if (active) { run.value = active; startPolling(); startChildPolling(); }
    else if (history.length) { run.value = history[0]; }
  } catch { /* ignore */ }
  await refreshInspect();
}

async function refreshInspect() {
  loadingInspect.value = true;
  try {
    inspect.value = await apiClient.getFullProductionInspect(props.projectName);
  } catch { inspect.value = null; }
  loadingInspect.value = false;
}

// ---- 轮询 FullProjectRun ----
function startPolling() {
  stopPolling();
  pollTimer = window.setInterval(async () => {
    if (!run.value) return;
    try {
      const r = await apiClient.getFullProductionRun(props.projectName, run.value.run_id);
      run.value = r;
      if (!isRunActive(r)) { stopPolling(); stopChildPolling(); }
    } catch { /* ignore */ }
  }, 1500);
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// ---- 轮询 production child（producing 阶段高层进度，§五十五）----
function startChildPolling() {
  stopChildPolling();
  childTimer = window.setInterval(async () => {
    if (!run.value || !run.value.production_run_id) { childProgress.value = null; return; }
    try {
      const child = await apiClient.getProductionRun(props.projectName, run.value.production_run_id);
      prodChild.value = child;
      const cur = child.scenes.find(s => s.status === 'running');
      const done = child.scenes.filter(s => s.status === 'completed').reduce((a, s) => a + s.shot_count, 0);
      const total = child.scenes.reduce((a, s) => a + s.shot_count, 0);
      childProgress.value = { done, total, stageLabel: cur ? '制作中' : '' };
    } catch { /* child 可能已结束 */ }
  }, 1500);
}
function stopChildPolling() {
  if (childTimer) { clearInterval(childTimer); childTimer = null; }
  childProgress.value = null;
}

// ---- 操作 ----
async function onStart() {
  starting.value = true;
  try {
    const r = await apiClient.startFullProduction(props.projectName, respectLocked.value);
    run.value = r; startPolling(); startChildPolling();
    message.success('AI 完整制作已启动');
  } catch (e: any) {
    const detail = e?.message || '';
    if (detail.includes('已有') || e?.status === 409) {
      message.warning('这个项目已有进行中的 AI 完整制作任务。');
      const history = await apiClient.listFullProductionRuns(props.projectName);
      const active = history.find(r => isRunActive(r));
      if (active) { run.value = active; startPolling(); startChildPolling(); }
    } else {
      message.error(`启动失败: ${detail}`);
    }
  } finally { starting.value = false; }
}

async function onPause() {
  if (!run.value) return;
  try { run.value = await apiClient.pauseFullProduction(props.projectName, run.value.run_id); }
  catch (e: any) { message.error(`暂停失败: ${e.message || e}`); }
}
async function onResume() {
  if (!run.value) return;
  resuming.value = true;
  try {
    run.value = await apiClient.resumeFullProduction(props.projectName, run.value.run_id);
    startPolling(); startChildPolling();
  } catch (e: any) { message.error(`继续失败: ${e.message || e}`); }
  finally { resuming.value = false; }
}
function onCancel() {
  if (!run.value) return;
  dialog.warning({
    title: '停止 AI 完整制作', content: '将停止 AI 完整制作。已生成的分镜、制作计划和视频会保留，不会删除。',
    positiveText: '停止', negativeText: '取消',
    onPositiveClick: async () => {
      if (!run.value) return;
      try { run.value = await apiClient.cancelFullProduction(props.projectName, run.value.run_id); }
      catch (e: any) { message.error(`停止失败: ${e.message || e}`); }
    },
  });
}
async function onRestart() {
  run.value = null;
  prodChild.value = null;
  stopPolling(); stopChildPolling();
  await refreshInspect();
}

onMounted(init);
onUnmounted(() => { stopPolling(); stopChildPolling(); });
</script>

<style scoped>
.fp-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.fp { width: 640px; max-width: 92vw; max-height: 88vh; display: flex; flex-direction: column; background: #1e1e1e; border-radius: 12px; color: #e0e0e0; box-shadow: 0 8px 32px rgba(0,0,0,.5); }
.fp-head { display: flex; align-items: center; gap: 10px; padding: 14px 18px; border-bottom: 1px solid #333; }
.fp-title { font-size: 16px; font-weight: 600; }
.fp-status { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: #333; }
.fp-status.preparing, .fp-status.producing { background: #2d4a2d; color: #8f8; }
.fp-status.paused { background: #4a4a2d; color: #dd8; }
.fp-status.failed { background: #4a2d2d; color: #f88; }
.fp-status.blocked { background: #4a2d2d; color: #f88; }
.fp-status.completed { background: #2d3a4a; color: #8cf; }
.fp-sub { font-size: 12px; color: #999; margin-left: auto; }
.fp-close { margin-left: 8px; background: none; border: none; color: #999; cursor: pointer; font-size: 16px; }
.fp-body { padding: 16px 18px; overflow-y: auto; flex: 1; }
.empty { color: #888; padding: 20px; text-align: center; }
.pf-summary { display: flex; gap: 16px; margin-bottom: 12px; font-size: 13px; color: #bbb; }
.pf-blockers { margin-bottom: 12px; }
.blocker-row { color: #f88; font-size: 13px; padding: 4px 0; }
.pf-scenes { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.pf-scene { display: flex; gap: 10px; padding: 8px 10px; background: #262626; border-radius: 6px; }
.pf-scene.ready { border-left: 3px solid #4a4; }
.pf-scene.need { border-left: 3px solid #aaa; }
.pf-scene.block { border-left: 3px solid #f88; }
.pf-icon { font-size: 14px; }
.pf-info { flex: 1; }
.pf-name { font-size: 13px; font-weight: 500; }
.pf-detail { font-size: 12px; color: #999; display: flex; gap: 8px; flex-wrap: wrap; }
.pf-warnings { margin-bottom: 12px; }
.warn-row { color: #dd8; font-size: 12px; padding: 3px 0; }
.pf-notready { color: #f88; font-size: 13px; padding: 10px; background: #3a2222; border-radius: 6px; }
.confirm-info { font-size: 13px; color: #bbb; line-height: 1.7; padding: 10px; background: #262626; border-radius: 6px; margin-bottom: 10px; }
.confirm-info p { margin: 2px 0; }
.lock-check { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #bbb; cursor: pointer; }
.scene-list { display: flex; flex-direction: column; gap: 6px; }
.scene-row { display: flex; gap: 10px; padding: 8px 10px; background: #262626; border-radius: 6px; }
.scene-row.sr-done { border-left: 3px solid #4a4; }
.scene-row.sr-run { border-left: 3px solid #48a; }
.scene-row.sr-fail { border-left: 3px solid #f88; }
.scene-row.sr-block { border-left: 3px solid #f88; }
.scene-row.sr-pause { border-left: 3px solid #dd8; }
.sr-icon { font-size: 14px; }
.sr-info { flex: 1; }
.sr-name { font-size: 13px; font-weight: 500; }
.sr-detail { font-size: 12px; color: #999; }
.sr-err { color: #f88; }
.current-hint { margin-top: 12px; font-size: 13px; color: #8cf; }
.overall-prog { margin-bottom: 12px; }
.op-bar { height: 8px; background: #333; border-radius: 4px; overflow: hidden; }
.op-fill { height: 100%; background: #48a; transition: width .3s; }
.op-text { font-size: 12px; color: #999; margin-top: 4px; }
.compose-view, .done-view, .err-view { text-align: center; padding: 24px 10px; }
.compose-spin { font-size: 32px; }
.compose-text { font-size: 15px; margin-top: 8px; }
.compose-sub { font-size: 13px; color: #999; margin-top: 4px; }
.done-icon { font-size: 40px; color: #4a4; }
.done-title { font-size: 17px; font-weight: 600; margin-top: 6px; }
.done-stats { font-size: 13px; color: #999; margin-top: 6px; display: flex; gap: 14px; justify-content: center; }
.done-video { width: 100%; max-height: 240px; margin: 12px 0; border-radius: 6px; background: #000; }
.done-actions { display: flex; gap: 8px; justify-content: center; }
.err-icon { font-size: 40px; }
.err-icon.failed { color: #f88; }
.err-icon.blocked { color: #f88; }
.err-icon.cancelled { color: #999; }
.err-title { font-size: 17px; font-weight: 600; margin-top: 6px; }
.err-scene { font-size: 13px; color: #dd8; margin-top: 6px; }
.err-msg { font-size: 13px; color: #bbb; margin-top: 8px; max-width: 480px; margin-left: auto; margin-right: auto; }
.err-hint { font-size: 12px; color: #999; margin-top: 8px; max-width: 480px; margin-left: auto; margin-right: auto; }
.run-error { color: #f88; font-size: 13px; padding: 8px; background: #3a2222; border-radius: 6px; margin-bottom: 10px; }
.fp-foot { display: flex; align-items: center; gap: 8px; padding: 12px 18px; border-top: 1px solid #333; }
.foot-spacer { flex: 1; }
.btn { padding: 6px 14px; border-radius: 6px; border: 1px solid #444; background: #333; color: #ddd; cursor: pointer; font-size: 13px; }
.btn:hover { background: #3a3a3a; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn.primary { background: #2d5a8e; border-color: #3a7ac9; color: #fff; }
.btn.primary:hover { background: #3a6a9e; }
.btn.danger { background: #6e2d2d; border-color: #9e3a3a; color: #fff; }
.btn.danger:hover { background: #7e3a3a; }
.btn.ghost { background: transparent; }
</style>
