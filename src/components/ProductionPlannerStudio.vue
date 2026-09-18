<template>
  <div class="pp-overlay" @click.self="$emit('close')">
    <div class="pp">
      <!-- 头部 -->
      <header class="pp-head">
        <span class="pp-title">AI 制作规划 · 故事段 {{ sceneId }}</span>
        <span class="pp-sub" v-if="plan">{{ plan.shots.length }} 镜头
          · run_seed {{ plan.run_seed }}</span>
        <span class="pp-status" v-if="plan" :class="statusClass">{{ statusText }}</span>
        <span class="pp-saving" v-if="saving">保存中…</span>
        <button class="pp-close" @click="$emit('close')">✕</button>
      </header>

      <div class="pp-body">
        <!-- 顶部：故事段 + 角色 + 规划按钮 -->
        <div class="scene-box">
          <div class="se-label">故事段</div>
          <div class="se-text">{{ sceneSummary || '（未填写故事）' }}</div>
          <div class="se-chars" v-if="sceneCharacters.length">
            <span class="se-clabel">出场角色：</span>
            <span class="se-char" v-for="name in sceneCharacters" :key="name">{{ name }}</span>
          </div>
          <div class="se-chars" v-else><span class="se-clabel">出场角色：无</span></div>
          <div class="se-shots">镜头数：{{ shots.length }}</div>
          <button class="btn primary plan-btn" :disabled="planning" @click="onPlan">
            {{ planning ? '规划中…' : (plan && plan.shots.length ? '重新规划' : '生成制作计划') }}
          </button>
        </div>

        <!-- stale 警告 -->
        <div v-if="plan && plan.is_stale" class="stale-warn">
          制作计划已过期（{{ plan.stale_reason }}）。故事段或镜头规划发生变化，请重新生成制作计划。
        </div>

        <!-- 镜头规划列表 -->
        <div v-if="plan && plan.shots.length" class="shots-section">
          <div class="shots-head" v-if="plan.overview">
            <span class="ov-label">AI 概述：</span>{{ plan.overview }}
          </div>

          <div class="shot-list">
            <div v-for="(sh, i) in plan.shots" :key="sh.shot_id" class="shot-card">
              <div class="shot-head">
                <span class="shot-no">{{ i + 1 }}</span>
                <span class="shot-id">{{ sh.shot_id }}</span>
                <span v-if="defaultedShots.has(sh.shot_id)" class="shot-default" title="模型未返回该镜头规划，使用系统默认">默认</span>
              </div>

              <label class="field-label">本镜意图</label>
              <div class="intent-text">{{ intentOf(sh.shot_id) }}</div>
              <div class="shot-meta-row">
                <span class="meta-chip" v-if="charsOf(sh.shot_id).length">{{ charsOf(sh.shot_id).join('、') }}</span>
                <span class="meta-chip dur">{{ durOf(sh.shot_id) }}s</span>
              </div>

              <!-- 开场来源（opening_source）-->
              <div class="control-group">
                <span class="cg-label">开场</span>
                <div class="radio-row">
                  <label class="radio" :class="{ on: sh.opening_source === 'independent' }">
                    <input type="radio" :name="`open-${sh.shot_id}`" value="independent"
                           :checked="sh.opening_source === 'independent'"
                           @change="onOpenChange(sh, i, 'independent')" />独立生成
                  </label>
                  <label class="radio" :class="{ on: sh.opening_source === 'reference_previous', disabled: i === 0 }"
                         :title="i === 0 ? '第一镜必须独立' : ''">
                    <input type="radio" :name="`open-${sh.shot_id}`" value="reference_previous"
                           :disabled="i === 0"
                           :checked="sh.opening_source === 'reference_previous'"
                           @change="onOpenChange(sh, i, 'reference_previous')" />参考上一镜
                  </label>
                  <label class="radio" :class="{ on: sh.opening_source === 'inherit_previous', disabled: i === 0 }"
                         :title="i === 0 ? '第一镜必须独立' : ''">
                    <input type="radio" :name="`open-${sh.shot_id}`" value="inherit_previous"
                           :disabled="i === 0"
                           :checked="sh.opening_source === 'inherit_previous'"
                           @change="onOpenChange(sh, i, 'inherit_previous')" />沿用上一镜末帧
                  </label>
                </div>
              </div>

              <!-- 视频方式（link_mode）-->
              <div class="control-group">
                <span class="cg-label">视频方式</span>
                <div class="radio-row">
                  <label class="radio" :class="{ on: sh.link_mode === 'i2v' }">
                    <input type="radio" :name="`link-${sh.shot_id}`" value="i2v"
                           :checked="sh.link_mode === 'i2v'"
                           @change="onLinkChange(sh, 'i2v')" />单图生成
                  </label>
                  <label class="radio" :class="{ on: sh.link_mode === 'keyframes' }">
                    <input type="radio" :name="`link-${sh.shot_id}`" value="keyframes"
                           :checked="sh.link_mode === 'keyframes'"
                           @change="onLinkChange(sh, 'keyframes')" />首尾帧过渡
                  </label>
                </div>
              </div>

              <!-- 依赖（文字，§十九）-->
              <div class="dep-row">
                <span class="dep-label">依赖：</span>
                <span v-if="sh.requires_previous_video_end && sh.source_shot_id" class="dep-val">
                  镜头 {{ sourceShotNo(sh.source_shot_id) }} · 实际末帧
                </span>
                <span v-else class="dep-val muted">无前置依赖</span>
              </div>

              <!-- 执行（派生显示，§十七）-->
              <div class="exec-row">
                <span class="exec-label">执行：</span>
                <span class="exec-item">{{ sh.generate_first_frame ? '生成首帧' : '沿用上一镜实际末帧' }}</span>
                <span class="exec-sep">·</span>
                <span class="exec-item">{{ sh.generate_last_frame ? '生成尾帧' : '跳过尾帧' }}</span>
                <span class="exec-sep">·</span>
                <span class="exec-item">生成视频</span>
              </div>

              <!-- 原因 -->
              <div class="reason-row">
                <span class="reason-label">原因：</span>
                <textarea class="reason-area" rows="2" :value="sh.reason"
                          @input="onReasonInput(sh, ($event.target as HTMLTextAreaElement).value)"
                          placeholder="为什么这样规划…"></textarea>
              </div>
            </div>
          </div>
        </div>

        <!-- 空状态 -->
        <div v-if="!plan || !plan.shots.length" class="empty-state">
          点击上方「生成制作计划」，让 AI 为每个镜头决定开场来源与视频方式。
        </div>
      </div>

      <!-- 底部 -->
      <footer class="pp-foot">
        <button class="btn ghost" @click="$emit('close')">关闭</button>
        <span class="foot-spacer"></span>
        <button v-if="plan && plan.shots.length && !plan.is_stale"
                class="btn primary" :disabled="approving || plan.status === 'approved'"
                @click="onApprove">
          {{ approving ? '确认中…' : (plan.status === 'approved' ? '已确认' : '确认制作计划') }}
        </button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';
import { apiClient } from '@/api';
import type { SceneProductionPlan, PlannedShot } from '@/api';

const { message, dialog } = createDiscreteApi(['message', 'dialog'], { configProviderProps: discreteConfigProviderProps });

interface ShotInfo { shot_id: string; description: string; characters: string[]; target_duration_s: number | null; }

const props = defineProps<{
  projectName: string;
  sceneId: string;
  sceneSummary: string;
  sceneCharacters: string[];
  shots: ShotInfo[];
}>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'approved'): void }>();

const plan = ref<SceneProductionPlan | null>(null);
const planning = ref(false);
const approving = ref(false);
const saving = ref(false);
// 标记使用系统默认的镜头（AI 漏返回）--首次 load 后固定，编辑不再标记
const defaultedShots = ref<Set<string>>(new Set());
let saveTimer: number | null = null;
let loadSeq = 0;

const statusClass = computed(() => {
  if (!plan.value) return '';
  if (plan.value.is_stale) return 'stale';
  return plan.value.status === 'approved' ? 'approved' : 'draft';
});
const statusText = computed(() => {
  if (!plan.value) return '';
  if (plan.value.is_stale) return '已过期';
  return plan.value.status === 'approved' ? '已确认' : '草稿';
});

// ---- 当前 scene 镜头信息查表 ----
function shotInfo(shotId: string): ShotInfo | undefined {
  return props.shots.find(s => s.shot_id === shotId);
}
function intentOf(shotId: string): string {
  return shotInfo(shotId)?.description || '（未填写意图）';
}
function charsOf(shotId: string): string[] {
  return shotInfo(shotId)?.characters || [];
}
function durOf(shotId: string): string {
  const d = shotInfo(shotId)?.target_duration_s;
  return d == null ? '—' : String(d);
}
function sourceShotNo(sourceShotId: string): string {
  if (!plan.value) return sourceShotId;
  const idx = plan.value.shots.findIndex(s => s.shot_id === sourceShotId);
  return idx >= 0 ? String(idx + 1) : sourceShotId;
}

// ---- 初始化：恢复计划 ----
async function init() {
  if (!props.projectName || !props.sceneId) return;
  const mySeq = ++loadSeq;
  try {
    const p = await apiClient.getProductionPlan(props.projectName, props.sceneId);
    if (mySeq !== loadSeq) return;
    plan.value = p;
    markDefaults(p);
  } catch {
    if (mySeq !== loadSeq) return;
    // 无计划，留空等用户点生成
  }
}
function markDefaults(p: SceneProductionPlan) {
  // reason 含「系统默认策略」标记的视为默认
  const s = new Set<string>();
  for (const sh of p.shots) {
    if (sh.reason.includes('系统默认策略')) s.add(sh.shot_id);
  }
  defaultedShots.value = s;
}

// ---- AI 规划 / 重新规划 ----
async function onPlan() {
  if (plan.value && plan.value.shots.length) {
    dialog.warning({
      title: '重新规划', content: '重新规划会覆盖当前制作计划（run_seed 会重新生成）。',
      positiveText: '重新规划', negativeText: '取消',
      onPositiveClick: () => runPlan(),
    });
  } else {
    await runPlan();
  }
}
async function runPlan() {
  const mySeq = ++loadSeq;
  planning.value = true;
  try {
    const p = await apiClient.planProductionPlan(props.projectName, props.sceneId);
    if (mySeq !== loadSeq) return;
    plan.value = p;
    markDefaults(p);
    message.success(`AI 已规划 ${p.shots.length} 个镜头`);
  } catch (e: any) {
    if (mySeq === loadSeq) message.error(`规划失败: ${e.message || e}`);
  } finally {
    if (mySeq === loadSeq) planning.value = false;
  }
}

// ---- 本地即时重算派生字段（§十八：工作区立即重新计算）----
function recomputeDerived(sh: PlannedShot, idx: number) {
  if (sh.opening_source !== 'independent' && idx > 0) {
    sh.source_shot_id = plan.value!.shots[idx - 1].shot_id;
    sh.requires_previous_video_end = true;
  } else {
    sh.source_shot_id = null;
    sh.requires_previous_video_end = false;
  }
  sh.generate_first_frame = sh.opening_source !== 'inherit_previous';
  sh.generate_last_frame = sh.link_mode === 'keyframes';
  sh.generate_video = true;
  sh.auto_select_policy = 'seeded_random';
}

function onOpenChange(sh: PlannedShot, idx: number, val: string) {
  sh.opening_source = val;
  recomputeDerived(sh, idx);
  scheduleSave();
}
function onLinkChange(sh: PlannedShot, val: string) {
  sh.link_mode = val;
  // link_mode 不影响 source，只影响 generate_last_frame
  sh.generate_last_frame = val === 'keyframes';
  scheduleSave();
}
function onReasonInput(sh: PlannedShot, val: string) {
  sh.reason = val;
  // 改了默认镜头的 reason -> 不再算默认
  if (defaultedShots.value.has(sh.shot_id)) {
    defaultedShots.value.delete(sh.shot_id);
  }
  scheduleSave();
}

// ---- 保存（debounced PUT，后端权威重算）----
function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => { saveTimer = null; doSave(); }, 700);
}
async function doSave() {
  if (!plan.value) return;
  saving.value = true;
  try {
    const p = await apiClient.saveProductionPlan(props.projectName, props.sceneId, {
      overview: plan.value.overview,
      shots: plan.value.shots.map(s => ({
        shot_id: s.shot_id, opening_source: s.opening_source,
        link_mode: s.link_mode, reason: s.reason,
      })),
    });
    plan.value = p;   // 后端权威结果（派生字段以 backend 为准）
  } catch (e: any) {
    const msg = e.message || '';
    if (msg.includes('过期')) {
      message.warning('制作计划已过期，请重新生成。');
      if (plan.value) plan.value.is_stale = true;
    } else {
      message.error(`保存失败: ${msg}`);
    }
  } finally {
    saving.value = false;
  }
}

// ---- 确认制作计划（§二十）----
async function onApprove() {
  if (!plan.value) return;
  approving.value = true;
  try {
    await doSave();   // 先保存未提交的编辑
    const p = await apiClient.approveProductionPlan(props.projectName, props.sceneId);
    plan.value = p;
    message.success('制作计划已确认（未写入正式 Shot，AI-4B 执行时生效）');
    emit('approved');
    emit('close');
  } catch (e: any) {
    const msg = e.message || '';
    if (msg.includes('过期')) {
      message.warning('制作计划已过期，请重新生成。');
      if (plan.value) plan.value.is_stale = true;
    } else {
      message.error(`确认失败: ${msg}`);
    }
  } finally {
    approving.value = false;
  }
}

watch(() => props.projectName, (nv, ov) => { if (nv !== ov) emit('close'); });
watch(() => props.sceneId, (nv, ov) => { if (nv !== ov) emit('close'); });
onMounted(() => init());
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
.pp-sub { font-size: 12px; color: var(--text-muted); }
.pp-status {
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  &.draft { background: rgba(251, 191, 36, 0.15); color: #fbbf24; }
  &.approved { background: rgba(74, 222, 128, 0.15); color: #4ade80; }
  &.stale { background: rgba(239, 68, 68, 0.15); color: #ef4444; }
}
.pp-saving { font-size: 11px; color: var(--text-faint); }
.pp-close {
  margin-left: auto; width: 28px; height: 28px; border-radius: 6px; border: none;
  background: none; color: var(--text-muted); font-size: 16px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
.pp-body { flex: 1; overflow-y: auto; padding: 18px 20px; }

.scene-box {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 14px;
  margin-bottom: 16px;
}
.se-label { font-size: 11px; color: var(--text-muted); margin-bottom: 6px; }
.se-text { font-size: 13px; color: var(--text-secondary); line-height: 1.6; margin-bottom: 10px; }
.se-chars { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 6px; }
.se-clabel { font-size: 11px; color: var(--text-muted); }
.se-char { padding: 2px 10px; border-radius: 10px; background: var(--bg-hover); color: var(--text-secondary); font-size: 11px; }
.se-shots { font-size: 11px; color: var(--text-muted); margin-bottom: 8px; }
.plan-btn { margin-top: 4px; }

.stale-warn {
  background: rgba(239, 68, 68, 0.1); border: 1px solid #ef4444; border-radius: 8px;
  padding: 10px 14px; font-size: 12px; color: #ef4444; margin-bottom: 16px;
}

.shots-head {
  font-size: 12px; color: var(--text-secondary); margin-bottom: 12px;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px;
}
.ov-label { color: var(--text-muted); }

.shot-list { display: flex; flex-direction: column; gap: 12px; }
.shot-card {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 14px;
}
.shot-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.shot-no {
  flex-shrink: 0; width: 22px; height: 22px; border-radius: 50%; background: var(--accent);
  color: #fff; font-size: 11px; font-weight: 600; display: flex; align-items: center; justify-content: center;
}
.shot-id { font-size: 12px; color: var(--text-muted); font-family: monospace; }
.shot-default {
  font-size: 10px; padding: 1px 6px; border-radius: 8px; background: rgba(251, 191, 36, 0.15);
  color: #fbbf24; cursor: help;
}
.field-label { display: block; font-size: 11px; color: var(--text-muted); margin-bottom: 4px; }
.intent-text { font-size: 13px; color: var(--text-primary); line-height: 1.5; margin-bottom: 6px; }
.shot-meta-row { display: flex; gap: 6px; margin-bottom: 10px; }
.meta-chip {
  font-size: 11px; padding: 1px 8px; border-radius: 8px; background: var(--bg-hover); color: var(--text-muted);
  &.dur { color: var(--text-secondary); }
}

.control-group { margin-bottom: 10px; }
.cg-label { display: inline-block; font-size: 11px; color: var(--text-muted); width: 56px; vertical-align: middle; }
.radio-row { display: inline-flex; flex-wrap: wrap; gap: 8px; }
.radio {
  display: inline-flex; align-items: center; gap: 4px; padding: 4px 12px; border-radius: 6px;
  border: 1px solid var(--border); font-size: 12px; color: var(--text-muted); cursor: pointer;
  input { margin: 0; cursor: pointer; }
  &:hover:not(.disabled) { border-color: var(--accent); color: var(--text-secondary); }
  &.on { background: var(--accent); border-color: var(--accent); color: var(--accent-text, #fff); }
  &.disabled { opacity: 0.4; cursor: not-allowed; input { cursor: not-allowed; } }
}

.dep-row, .exec-row, .reason-row {
  font-size: 12px; margin-top: 8px; display: flex; align-items: flex-start; gap: 6px;
}
.dep-label, .exec-label, .reason-label { color: var(--text-muted); flex-shrink: 0; }
.dep-val { color: var(--text-secondary); &.muted { color: var(--text-faint); } }
.exec-item { color: var(--text-secondary); }
.exec-sep { color: var(--text-faint); }
.reason-area {
  flex: 1; background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px;
  padding: 6px 10px; font-size: 12px; color: var(--text-primary); outline: none; font-family: inherit;
  resize: vertical; line-height: 1.5; &:focus { border-color: var(--accent); }
}

.empty-state {
  text-align: center; padding: 40px; color: var(--text-muted); font-size: 13px;
}

.pp-foot {
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
</style>
