<script setup lang="ts">
// Phase55 E（任务书 §9）：协作模式选择与 run 状态面板。
// 设计约束：沿用既有画布组件（不新增节点类型）；只展示有助决策的信息——
// 模式用途/可用性/运行步骤；高级字段（快照哈希/租约代次）折叠。
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { apiClient } from '@/api/index';

const props = defineProps<{ project: string }>();

const modes = ref<any[]>([]);
const selectedMode = ref<string>('');
const storyGoal = ref<string>('');
const starting = ref(false);
const startError = ref<string>('');
const runs = ref<any[]>([]);
const activeRun = ref<any>(null);
const pollTimer = ref<number | null>(null);
const showAdvanced = ref(false);

async function loadModes() {
  try {
    const data = await apiClient.collabModes(props.project);
    modes.value = data.modes || [];
    if (!selectedMode.value && modes.value.length) {
      selectedMode.value = modes.value[0].mode_id;
    }
  } catch { /* 静默：面板不可用不阻塞房间 */ }
}

async function loadRuns() {
  try {
    const data = await apiClient.collabRuns(props.project);
    runs.value = (data.runs || []).slice(0, 8);
    if (activeRun.value) {
      const cur = runs.value.find(r => r.run_id === activeRun.value.run_id);
      if (cur) activeRun.value = cur;
    }
  } catch { /* 静默 */ }
}

async function startRun() {
  startError.value = '';
  if (!selectedMode.value || !storyGoal.value.trim()) {
    startError.value = '请选择模式并填写故事目标';
    return;
  }
  starting.value = true;
  try {
    const data = await apiClient.collabRunStart(props.project, {
      mode_id: selectedMode.value,
      params: { story_goal: storyGoal.value.trim() },
    });
    activeRun.value = data.run;
    await loadRuns();
    schedulePoll();
  } catch (e: any) {
    startError.value = e?.message || '启动失败';
  } finally {
    starting.value = false;
  }
}

async function cancelRun() {
  if (!activeRun.value) return;
  try {
    await apiClient.collabRunCancel(props.project, activeRun.value.run_id, '用户停止');
    await loadRuns();
  } catch { /* 静默 */ }
}

function schedulePoll() {
  if (pollTimer.value) window.clearInterval(pollTimer.value);
  pollTimer.value = window.setInterval(async () => {
    await loadRuns();
    if (activeRun.value &&
        ['completed', 'completed_with_issues', 'failed', 'cancelled'].includes(activeRun.value.status)) {
      if (pollTimer.value) window.clearInterval(pollTimer.value);
      pollTimer.value = null;
    }
  }, 5000);
}

function stepLabel(s: string): string {
  return ({ pending: '待执行', running: '进行中', done: '完成', failed: '失败' } as Record<string, string>)[s] || s;
}

onMounted(() => { loadModes(); loadRuns(); });
onBeforeUnmount(() => { if (pollTimer.value) window.clearInterval(pollTimer.value); });
</script>

<template>
  <div class="crp">
    <div class="crp-head" @click="showAdvanced = !showAdvanced">
      <span class="crp-title">协作模式</span>
      <span class="crp-sub">选择协作预设启动生产 run（配置在启动时固定快照）</span>
    </div>
    <div class="crp-modes">
      <label v-for="m in modes" :key="m.mode_id" class="crp-mode"
             :class="{ sel: selectedMode === m.mode_id, dis: !m.available }">
        <input type="radio" :value="m.mode_id" v-model="selectedMode" :disabled="!m.available" />
        <span class="cm-name">{{ m.preset_label }} {{ m.name }}</span>
        <span class="cm-desc" :title="m.description">{{ m.description }}</span>
        <span v-if="!m.available" class="cm-reason"
              :title="(m.unavailable_reasons || []).join('；')">不可用</span>
      </label>
    </div>
    <div class="crp-goal">
      <textarea v-model="storyGoal" class="crp-goal-input" rows="3"
                placeholder="故事目标（稳定制作简报：六段剧情/角色/场景要求——预铺与主执行共同输入）" />
    </div>
    <div class="crp-actions">
      <button class="btn btn-primary" :disabled="starting" @click="startRun">
        {{ starting ? '启动中…' : '启动 run' }}
      </button>
      <button v-if="activeRun && !['completed', 'completed_with_issues', 'failed', 'cancelled'].includes(activeRun.status)"
              class="btn btn-ghost" @click="cancelRun">停止</button>
      <span v-if="startError" class="crp-err">{{ startError }}</span>
    </div>
    <div v-if="activeRun" class="crp-run">
      <div class="crp-run-head">
        <span class="crp-run-id">{{ activeRun.run_id }}</span>
        <span class="crp-run-status" :class="activeRun.status">{{ activeRun.status === 'completed_with_issues' ? '执行结束，仍有问题' : activeRun.status }}</span>
        <span class="crp-run-hash" v-if="showAdvanced" :title="activeRun.snapshot_hash">
          {{ activeRun.snapshot_hash }}</span>
      </div>
      <div class="crp-steps">
        <div v-for="s in activeRun.steps || []" :key="s.step_id" class="crp-step">
          <span class="cs-dot" :class="s.status"></span>
          <span class="cs-name">{{ s.step_id }}</span>
          <span class="cs-status">{{ stepLabel(s.status) }}</span>
          <span v-if="showAdvanced && s.detail && Object.keys(s.detail).length"
                class="cs-detail">{{ JSON.stringify(s.detail).slice(0, 120) }}</span>
        </div>
      </div>
    </div>
    <div v-if="runs.length" class="crp-history">
      <div class="crp-h-title">近期 runs</div>
      <div v-for="r in runs" :key="r.run_id" class="crp-h-row"
           :class="{ sel: activeRun && activeRun.run_id === r.run_id }"
           @click="activeRun = r">
        <span>{{ r.run_id }}</span>
        <span class="crp-h-mode">{{ r.mode_id }}</span>
        <span class="crp-h-status" :class="r.status">{{ r.status }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.crp { border-top: 1px solid var(--border, #2a2a35); padding: 10px 12px; font-size: 12px; }
.crp-head { display: flex; align-items: baseline; gap: 8px; cursor: pointer; }
.crp-title { font-weight: 600; }
.crp-sub { opacity: .55; }
.crp-modes { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-top: 8px; }
.crp-mode { display: grid; grid-template-columns: auto 1fr; gap: 2px 6px; align-items: center;
  border: 1px solid var(--border, #2a2a35); border-radius: 6px; padding: 6px 8px; cursor: pointer; }
.crp-mode.sel { border-color: var(--accent, #6c8cff); }
.crp-mode.dis { opacity: .45; }
.crp-mode input { grid-row: span 2; }
.cm-name { font-weight: 600; }
.cm-desc { grid-column: 2; opacity: .6; font-size: 11px; }
.cm-reason { grid-column: 2; color: #e6a23c; font-size: 11px; }
.crp-goal { margin-top: 8px; }
.crp-goal-input { width: 100%; resize: vertical; background: transparent;
  color: inherit; border: 1px solid var(--border, #2a2a35); border-radius: 6px; padding: 6px 8px; }
.crp-actions { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
.crp-err { color: #e66; }
.crp-run { margin-top: 10px; border: 1px solid var(--border, #2a2a35); border-radius: 6px; padding: 8px; }
.crp-run-head { display: flex; gap: 8px; align-items: baseline; }
.crp-run-id { font-family: monospace; }
.crp-run-status.completed_with_issues { color: #e9aa55; }
.crp-run-status.completed { color: #6c6; }
.crp-run-status.failed { color: #e66; }
.crp-run-status.cancelled { color: #e6a23c; }
.crp-run-status.running { color: #6c8cff; }
.crp-run-hash { opacity: .45; font-family: monospace; }
.crp-steps { margin-top: 6px; display: grid; gap: 3px; }
.crp-step { display: flex; gap: 6px; align-items: baseline; }
.cs-dot { width: 7px; height: 7px; border-radius: 50%; background: #555; align-self: center; }
.cs-dot.done { background: #6c6; }
.cs-dot.running { background: #6c8cff; }
.cs-dot.failed { background: #e66; }
.cs-name { font-family: monospace; }
.cs-status { opacity: .6; }
.cs-detail { opacity: .4; font-family: monospace; overflow: hidden; text-overflow: ellipsis; }
.crp-history { margin-top: 8px; }
.crp-h-title { opacity: .55; margin-bottom: 4px; }
.crp-h-row { display: flex; gap: 10px; cursor: pointer; padding: 2px 4px; border-radius: 4px; }
.crp-h-row.sel { background: rgba(108, 140, 255, .12); }
.crp-h-mode { opacity: .6; }
.crp-h-status { margin-left: auto; opacity: .7; }
</style>
