<template>
  <div class="so-overlay" @click.self="$emit('close')">
    <div class="so">
      <!-- 头部 -->
      <header class="so-head">
        <span class="so-title">AI 整理故事</span>
        <span class="so-sub" v-if="draft">{{ draft.scenes.length }} 段 · {{ resolvedCount }}/{{ draft.characters.length }} 人物</span>
        <span class="so-saving" v-if="saving">保存中…</span>
        <button class="so-close" @click="$emit('close')">✕</button>
      </header>

      <!-- 步骤条 -->
      <div class="so-steps">
        <button
          v-for="s in steps" :key="s.n" class="step"
          :class="{ on: step === s.n, done: step > s.n }"
          @click="goStep(s.n)"
        >
          <span class="step-no">{{ step > s.n ? '✓' : s.n }}</span>
          <span class="step-label">{{ s.label }}</span>
        </button>
      </div>

      <!-- 主体 -->
      <div class="so-body">
        <div v-if="organizing && !draft" class="so-loading">
          <div class="spinner"></div>
          <span>AI 正在整理故事…</span>
        </div>

        <template v-else-if="draft">
          <!-- Step 1：故事段 -->
          <div v-if="step === 1" class="step-pane">
            <div class="pane-head">
              <span>AI 已将故事整理为 {{ draft.scenes.length }} 个故事段</span>
              <button class="link-btn" @click="confirmReOrganize">↻ 重新整理</button>
            </div>
            <div class="scene-list">
              <div
                v-for="(sc, i) in draft.scenes" :key="sc.draft_id"
                class="ds-card"
                :class="{ dragging: dragIdx === i }"
                draggable="true"
                @dragstart="onDragStart(i)"
                @dragover.prevent="onDragOver(i)"
                @drop="onDrop(i)"
                @dragend="onDragEnd"
              >
                <div class="ds-head">
                  <span class="ds-no">{{ i + 1 }}</span>
                  <span class="ds-grip" title="拖拽排序">⠿</span>
                  <button class="ds-del" title="删除该段" @click="removeScene(i)">×</button>
                </div>
                <textarea
                  class="ds-summary"
                  :value="sc.summary"
                  @input="onSummaryInput(sc, ($event.target as HTMLTextAreaElement).value)"
                  rows="3"
                  placeholder="该故事段发生了什么…"
                ></textarea>
                <div class="ds-mentions">
                  <span class="ds-mlabel">人物：</span>
                  <template v-if="sc.character_mentions.length">
                    <span class="ds-mention" v-for="m in sc.character_mentions" :key="m">{{ m }}</span>
                  </template>
                  <span v-else class="ds-mnone">无</span>
                </div>
              </div>
              <div v-if="!draft.scenes.length" class="empty">AI 未整理出故事段，可点「重新整理」再试。</div>
            </div>
          </div>

          <!-- Step 2：角色检查（复用 CharacterResolutionPanel）-->
          <CharacterResolutionPanel
            v-if="step === 2"
            :characters="draft.characters"
            :project-chars="projectChars"
            :on-resolve="onResolve"
            :on-gen-single="onGenSingle"
            :on-batch-fill="onBatchFill"
            :on-batch-create="onBatchCreate"
          />

          <!-- Step 3：确认 -->
          <div v-if="step === 3" class="step-pane">
            <div class="pane-head"><span>即将创建 {{ draft.scenes.length }} 个故事段</span></div>
            <div class="confirm-list">
              <div v-for="(sc, i) in draft.scenes" :key="sc.draft_id" class="cf-card">
                <span class="cf-no">{{ i + 1 }}</span>
                <div class="cf-body">
                  <div class="cf-summary">{{ sc.summary || '（空故事段）' }}</div>
                  <div class="cf-chips">
                    <template v-if="sceneChars(sc).length">
                      <span
                        v-for="cn in sceneChars(sc)" :key="cn"
                        class="chip" :class="{ pending: isPendingChar(cn) }"
                      >{{ cn }}{{ isPendingChar(cn) ? ' · 形象待生成' : '' }}</span>
                    </template>
                    <span v-else class="cf-nochar">无角色资产</span>
                  </div>
                </div>
              </div>
            </div>
            <div class="cf-pending" v-if="createdChars.length">
              <div class="cf-pending-title">新创建但尚未生成形象的角色：</div>
              <div class="cf-pending-names">{{ createdChars.map(c => c.mention).join('、') }}</div>
              <div class="cf-pending-hint">这些角色已经属于当前项目，可以稍后在角色页生成三视图。</div>
              <button class="btn ghost go-design-btn" :disabled="applying" @click="apply(firstCreatedChar)">
                采用后前往角色设计
              </button>
            </div>
          </div>
        </template>
      </div>

      <!-- 底部操作 -->
      <footer class="so-foot" v-if="draft">
        <button class="btn ghost" @click="$emit('close')">取消</button>
        <span class="foot-spacer"></span>
        <button class="btn ghost" v-if="step > 1" @click="back">返回修改</button>
        <button
          v-if="step < 3"
          class="btn primary"
          :disabled="step === 2 && unresolvedCount > 0"
          :title="step === 2 && unresolvedCount > 0 ? `还有 ${unresolvedCount} 个角色需要处理` : ''"
          @click="next"
        >{{ step === 2 && unresolvedCount > 0 ? `还有 ${unresolvedCount} 个角色未处理` : '下一步' }}</button>
        <button
          v-if="step === 3"
          class="btn primary"
          :disabled="applying"
          @click="apply(null)"
        >{{ applying ? '采用中…' : '采用故事整理' }}</button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';
import { apiClient } from '@/api';
import type { StoryOrganizationDraft, DraftScene } from '@/api';
import CharacterResolutionPanel from './CharacterResolutionPanel.vue';

const { message, dialog } = createDiscreteApi(['message', 'dialog'], {
  configProviderProps: discreteConfigProviderProps,
});

const props = defineProps<{ projectName: string }>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'applied', goDesignChar?: string | null): void }>();

const step = ref<1 | 2 | 3>(1);
const draft = ref<StoryOrganizationDraft | null>(null);
const projectChars = ref<string[]>([]);
const organizing = ref(false);
const saving = ref(false);
const applying = ref(false);
const dragIdx = ref(-1);

let saveTimer: number | null = null;
let loadSeq = 0;

const steps = [
  { n: 1 as const, label: '故事段' },
  { n: 2 as const, label: '角色检查' },
  { n: 3 as const, label: '确认' },
];

const unresolvedCount = computed(() =>
  draft.value ? draft.value.characters.filter(r => r.status === 'unresolved').length : 0);
const resolvedCount = computed(() =>
  draft.value ? draft.value.characters.length - unresolvedCount.value : 0);
const createdChars = computed(() =>
  draft.value ? draft.value.characters.filter(r => r.status === 'created') : []);
const firstCreatedChar = computed(() => createdChars.value[0]?.matched_character_name || null);

function sceneChars(sc: DraftScene): string[] {
  if (!draft.value) return [];
  const map = new Map(draft.value.characters.map(r => [r.mention, r]));
  const out: string[] = [];
  for (const m of sc.character_mentions) {
    const r = map.get(m);
    if (r && (r.status === 'matched' || r.status === 'created') && r.matched_character_name) {
      if (!out.includes(r.matched_character_name)) out.push(r.matched_character_name);
    }
  }
  return out;
}
function isPendingChar(name: string): boolean {
  // created 状态的角色 = 形象待生成（无三视图）
  return !!draft.value?.characters.some(r => r.status === 'created' && r.matched_character_name === name);
}

// ---- 初始化 ----
async function init() {
  if (!props.projectName) return;
  const mySeq = ++loadSeq;
  organizing.value = true;
  try {
    await loadProjectChars();
    try {
      const d = await apiClient.getStoryDraft(props.projectName);
      if (mySeq !== loadSeq) return;
      draft.value = d;
      step.value = 1;
    } catch {
      if (mySeq !== loadSeq) return;
      await runOrganize(mySeq);   // 无草稿 -> 自动整理
    }
  } finally {
    if (mySeq === loadSeq) organizing.value = false;
  }
}

async function loadProjectChars() {
  try {
    const r = await apiClient.getProjectCharactersFull(props.projectName);
    projectChars.value = (r.characters || []).map(c => c.name);
  } catch {
    projectChars.value = [];
  }
}

async function runOrganize(mySeq?: number) {
  const seq = mySeq ?? ++loadSeq;
  organizing.value = true;
  try {
    const d = await apiClient.organizeStory(props.projectName);
    if (seq !== loadSeq) return;
    draft.value = d;
    step.value = 1;
  } catch (e: any) {
    if (seq === loadSeq) message.error(`整理故事失败: ${e.message || e}`);
  } finally {
    if (seq === loadSeq) organizing.value = false;
  }
}

// ---- 草稿自动保存 ----
function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => { saveTimer = null; doSaveDraft(); }, 800);
}
async function doSaveDraft() {
  if (!draft.value || !props.projectName) return;
  saving.value = true;
  try {
    await apiClient.saveStoryDraft(props.projectName, {
      overview: draft.value.overview,
      scenes: draft.value.scenes,
      characters: draft.value.characters,
    });
  } catch (e: any) {
    message.error(`草稿保存失败: ${e.message || e}`);
  } finally {
    saving.value = false;
  }
}

// ---- Step 1 ----
function onSummaryInput(sc: DraftScene, val: string) { sc.summary = val; scheduleSave(); }
function removeScene(i: number) {
  if (!draft.value) return;
  draft.value.scenes.splice(i, 1);
  scheduleSave();
}
function confirmReOrganize() {
  dialog.warning({
    title: '重新整理',
    content: '将根据当前完整故事重新整理，现有草稿（含已编辑文本和角色处理）会被替换。',
    positiveText: '重新整理', negativeText: '取消',
    onPositiveClick: () => runOrganize(),
  });
}
function onDragStart(i: number) { dragIdx.value = i; }
function onDragOver(_i: number) { /* dragover 仅需 prevent 默认行为，模板里已 .prevent */ }
function onDrop(i: number) {
  if (!draft.value || dragIdx.value === -1 || dragIdx.value === i) { dragIdx.value = -1; return; }
  const arr = draft.value.scenes.slice();
  const [m] = arr.splice(dragIdx.value, 1);
  arr.splice(i, 0, m);
  draft.value.scenes = arr;
  dragIdx.value = -1;
  scheduleSave();
}
function onDragEnd() { dragIdx.value = -1; }

// ---- Step 2：角色检查（委托 CharacterResolutionPanel，回调调 Phase AI-1 API）----
async function onResolve(params: {
  mention: string; action: 'match' | 'create' | 'ignore';
  matched_character_name?: string; name?: string; description?: string;
}) {
  if (!draft.value) return;
  const d = await apiClient.resolveStoryCharacter(props.projectName, params);
  draft.value = d;
  if (params.action === 'create') await loadProjectChars();   // 新建角色后刷新项目角色列表
}
async function onGenSingle(mention: string): Promise<{ description: string }> {
  return await apiClient.draftStoryCharacter(props.projectName, mention);
}
async function onBatchFill(): Promise<{ characters: Array<{ name: string; description: string }> }> {
  if (!draft.value) return { characters: [] };
  const names = draft.value.characters.filter(r => r.status === 'unresolved').map(r => r.mention);
  if (!names.length) return { characters: [] };
  return await apiClient.draftStoryCharactersBatch(props.projectName, names);
}
async function onBatchCreate(targets: Array<{ mention: string; name: string; description: string }>) {
  if (!draft.value) return;
  for (const t of targets) {
    const d = await apiClient.resolveStoryCharacter(props.projectName, {
      mention: t.mention, action: 'create', name: t.name, description: t.description,
    });
    draft.value = d;
  }
  await loadProjectChars();
}

// ---- Step 3 ----
async function apply(goDesignChar?: string | null) {
  if (!draft.value) return;
  applying.value = true;
  try {
    await apiClient.applyStoryOrganize(props.projectName);
    message.success('已采用故事整理');
    emit('applied', goDesignChar || null);
    emit('close');
  } catch (e: any) {
    const msg = e.message || '';
    if (msg.includes('发生变化')) {
      dialog.warning({
        title: '内容已变化',
        content: '完整故事或项目角色已经发生变化。请根据最新内容重新整理。',
        positiveText: '重新整理', negativeText: '关闭',
        onPositiveClick: () => { step.value = 1; runOrganize(); },
      });
    } else if (msg.includes('已有分镜内容')) {
      dialog.warning({
        title: '无法覆盖',
        content: '当前项目已有分镜内容。为避免删除已有镜头和生成结果，暂不支持 AI 一键覆盖现有故事段。你可以手动修改现有故事段，或在空项目中重新整理。',
        positiveText: '我知道了',
      });
    } else if (msg.includes('已不存在')) {
      dialog.warning({
        title: '角色缺失', content: msg, positiveText: '返回角色检查',
        onPositiveClick: () => { step.value = 2; },
      });
    } else {
      message.error(`采用失败: ${msg}`);
    }
  } finally {
    applying.value = false;
  }
}

// ---- 导航 ----
function next() {
  if (step.value === 1) {
    doSaveDraft();
    step.value = 2;
  } else if (step.value === 2) {
    if (unresolvedCount.value > 0) {
      message.warning(`还有 ${unresolvedCount.value} 个角色需要处理。`);
      return;
    }
    doSaveDraft();
    step.value = 3;
  }
}
function back() {
  if (step.value > 1) step.value = (step.value - 1) as 1 | 2 | 3;
}
function goStep(n: 1 | 2 | 3) {
  // 步骤条仅允许回退到已完成步骤；前进走底部「下一步」（带闸门）
  if (n < step.value) step.value = n;
}

// 切项目 -> 关闭（父级会 v-if 卸载；这里 emit 兜底）
watch(() => props.projectName, (nv, ov) => { if (nv !== ov) emit('close'); });

onMounted(() => init());
</script>

<style lang="scss" scoped>
.so-overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6); z-index: 1500;
  display: flex; align-items: center; justify-content: center; backdrop-filter: blur(4px);
}
.so {
  width: 82vw; height: 84vh; max-width: 1500px; min-width: 720px;
  background: var(--bg-root); border: 1px solid var(--border); border-radius: 10px;
  display: flex; flex-direction: column; overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
}

.so-head {
  display: flex; align-items: center; gap: 12px; padding: 14px 20px;
  border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.so-title { font-size: 15px; font-weight: 600; color: var(--text-primary); }
.so-sub { font-size: 12px; color: var(--text-muted); }
.so-saving { font-size: 11px; color: var(--text-faint); }
.so-close {
  margin-left: auto; width: 28px; height: 28px; border-radius: 6px; border: none;
  background: none; color: var(--text-muted); font-size: 16px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}

.so-steps {
  display: flex; gap: 4px; padding: 12px 20px; border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.step {
  display: flex; align-items: center; gap: 8px; padding: 6px 14px; border-radius: 6px;
  border: 1px solid transparent; background: none; color: var(--text-muted);
  font-size: 13px; cursor: pointer;
  .step-no {
    width: 20px; height: 20px; border-radius: 50%; border: 1px solid var(--border);
    display: flex; align-items: center; justify-content: center; font-size: 11px;
  }
  &.on { color: var(--text-primary); .step-no { background: var(--accent); border-color: var(--accent); color: #fff; } }
  &.done { color: var(--text-secondary); .step-no { background: #4ade80; border-color: #4ade80; color: #fff; } }
  &:not(.on):not(.done):hover { background: var(--bg-hover); }
}

.so-body { flex: 1; overflow-y: auto; padding: 18px 20px; }
.so-loading { display: flex; flex-direction: column; align-items: center; gap: 14px; padding: 60px 0; color: var(--text-muted); font-size: 13px; }
.spinner {
  width: 26px; height: 26px; border-radius: 50%;
  border: 2px solid var(--border); border-top-color: var(--accent);
  animation: so-spin 0.8s linear infinite;
}
@keyframes so-spin { to { transform: rotate(360deg); } }

.pane-head {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 13px; color: var(--text-secondary); margin-bottom: 14px;
}
.link-btn {
  background: none; border: none; color: var(--accent); font-size: 12px; cursor: pointer;
  padding: 2px 4px; border-radius: 4px;
  &:hover { background: var(--bg-hover); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
}

// Step 1
.scene-list { display: flex; flex-direction: column; gap: 10px; }
.ds-card {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px;
  &.dragging { opacity: 0.4; }
}
.ds-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.ds-no {
  flex-shrink: 0; width: 22px; height: 22px; border-radius: 50%; background: var(--accent);
  color: #fff; font-size: 11px; font-weight: 600; display: flex; align-items: center; justify-content: center;
}
.ds-grip { color: var(--text-faint); cursor: grab; font-size: 14px; }
.ds-del {
  margin-left: auto; width: 22px; height: 22px; border-radius: 4px; border: none; background: none;
  color: var(--text-muted); font-size: 15px; cursor: pointer;
  &:hover { background: #fee2e2; color: #ef4444; }
}
.ds-summary {
  width: 100%; background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px;
  padding: 8px 10px; font-size: 13px; color: var(--text-primary); outline: none; resize: vertical;
  font-family: inherit; line-height: 1.5; &:focus { border-color: var(--accent); }
}
.ds-mentions { margin-top: 8px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.ds-mlabel { font-size: 11px; color: var(--text-muted); }
.ds-mention {
  padding: 2px 8px; border-radius: 10px; background: var(--bg-hover); color: var(--text-secondary);
  font-size: 11px;
}
.ds-mnone { font-size: 11px; color: var(--text-faint); }

// Step 3
.confirm-list { display: flex; flex-direction: column; gap: 8px; }
.cf-card {
  display: flex; gap: 12px; background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 12px 14px;
}
.cf-no {
  flex-shrink: 0; width: 22px; height: 22px; border-radius: 50%; background: var(--accent);
  color: #fff; font-size: 11px; font-weight: 600; display: flex; align-items: center; justify-content: center;
}
.cf-body { flex: 1; min-width: 0; }
.cf-summary { font-size: 13px; color: var(--text-secondary); line-height: 1.5; margin-bottom: 8px; }
.cf-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip {
  padding: 2px 10px; border-radius: 12px; border: 1px solid var(--border);
  background: var(--bg-hover); color: var(--text-secondary); font-size: 11px;
  &.pending { border-style: dashed; color: var(--text-muted); }
}
.cf-nochar { font-size: 11px; color: var(--text-faint); }
.cf-pending {
  margin-top: 16px; padding: 12px 14px; background: var(--bg-surface); border: 1px dashed var(--border);
  border-radius: 8px;
}
.cf-pending-title { font-size: 12px; color: var(--text-secondary); margin-bottom: 6px; }
.cf-pending-names { font-size: 13px; color: var(--text-primary); margin-bottom: 6px; }
.cf-pending-hint { font-size: 11px; color: var(--text-muted); }
.go-design-btn { margin-top: 10px; border-color: var(--accent); color: var(--accent); }

.empty { text-align: center; color: var(--text-muted); font-size: 13px; padding: 32px 0; }

// 底部
.so-foot {
  display: flex; align-items: center; gap: 8px; padding: 14px 20px;
  border-top: 1px solid var(--border); flex-shrink: 0;
}
.foot-spacer { flex: 1; }
.btn {
  padding: 8px 18px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none;
  &:disabled { opacity: 0.4; cursor: not-allowed; }
}
.btn.ghost { background: var(--bg-surface); color: var(--text-muted); border: 1px solid var(--border); &:hover:not(:disabled) { background: var(--bg-hover); } }
.btn.primary { background: var(--accent); color: var(--accent-text); &:hover:not(:disabled) { background: var(--accent-hover); } }
</style>
