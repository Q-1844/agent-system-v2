<template>
  <div class="sc-overlay" @click.self="$emit('close')">
    <div class="sc">
      <!-- 头部 -->
      <header class="sc-head">
        <span class="sc-title">AI 续写故事</span>
        <span class="sc-sub" v-if="draft">{{ draft.new_scene_drafts.length }} 新段 · {{ resolvedCount }}/{{ draft.character_resolution.length }} 人物</span>
        <span class="sc-saving" v-if="saving">保存中…</span>
        <button class="sc-close" @click="$emit('close')">✕</button>
      </header>

      <!-- 步骤条 -->
      <div class="sc-steps">
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
      <div class="sc-body">
        <div v-if="loadingInfo" class="sc-loading">
          <div class="spinner"></div><span>加载中…</span>
        </div>

        <template v-else>
          <!-- Step 1：续写 -->
          <div v-if="step === 1" class="step-pane">
            <div class="story-ending">
              <div class="se-label">当前故事结尾</div>
              <div class="se-text">{{ storyEnding || '（完整故事为空）' }}</div>
            </div>

            <div class="gen-controls">
              <label class="form-label">接下来希望发生什么？（可选，不填则由 AI 自然续写）</label>
              <textarea
                class="text-area" rows="2" v-model="direction"
                placeholder="例如：让赤焰守卫进入城市，但暂时不要揭示信号来源。"
              ></textarea>

              <div class="scale-row">
                <span class="form-label">续写规模</span>
                <div class="scale-btns">
                  <button
                    v-for="o in scaleOptions" :key="o.v"
                    class="scale-btn" :class="{ on: scale === o.v }"
                    @click="scale = o.v"
                  >{{ o.l }}<span class="scale-hint">{{ o.h }}</span></button>
                </div>
              </div>

              <button class="btn primary" :disabled="generating" @click="onGenerate">
                {{ generating ? '生成中…' : (draft && draft.continuation_text ? '重新续写' : '生成续写') }}
              </button>
            </div>

            <div v-if="draft && draft.continuation_text" class="cont-draft">
              <div class="pane-head"><span>AI 续写草稿（可编辑）</span></div>
              <textarea
                class="text-area cont-textarea" rows="10"
                :value="draft.continuation_text"
                @input="onContInput(($event.target as HTMLTextAreaElement).value)"
                placeholder="AI 续写正文…"
              ></textarea>
            </div>
          </div>

          <!-- Step 2：新故事段与角色 -->
          <div v-if="step === 2 && draft" class="step-pane">
            <div class="pane-head"><span>本次续写将新增 {{ draft.new_scene_drafts.length }} 个故事段</span></div>
            <div class="scene-list">
              <div v-for="(sc, i) in draft.new_scene_drafts" :key="sc.draft_id" class="ds-card">
                <div class="ds-head">
                  <span class="ds-no">{{ i + 1 }}</span>
                  <button class="ds-del" title="删除该段" @click="removeNewScene(i)">×</button>
                </div>
                <textarea
                  class="ds-summary" rows="2"
                  :value="sc.summary"
                  @input="onNewSceneInput(sc, ($event.target as HTMLTextAreaElement).value)"
                ></textarea>
                <div class="ds-mentions">
                  <span class="ds-mlabel">人物：</span>
                  <template v-if="sc.character_mentions.length">
                    <span class="ds-mention" v-for="m in sc.character_mentions" :key="m">{{ m }}</span>
                  </template>
                  <span v-else class="ds-mnone">无</span>
                </div>
              </div>
              <div v-if="!draft.new_scene_drafts.length" class="empty">未整理出新故事段。</div>
            </div>

            <CharacterResolutionPanel
              :characters="draft.character_resolution"
              :project-chars="projectChars"
              :on-resolve="onResolve"
              :on-gen-single="onGenSingle"
              :on-batch-fill="onBatchFill"
              :on-batch-create="onBatchCreate"
            />
          </div>

          <!-- Step 3：确认追加 -->
          <div v-if="step === 3 && draft" class="step-pane">
            <div class="pane-head"><span>本次将：</span></div>
            <div class="confirm-summary">
              <div class="cs-row">追加完整故事：<b>+{{ draft.continuation_text.trim().length }} 字</b></div>
              <div class="cs-row">新增故事段：<b>+{{ draft.new_scene_drafts.length }}</b></div>
              <div class="cs-row" v-if="createdChars.length">
                新增项目角色：<b>+{{ createdChars.length }}</b>
                <span class="cs-chars">{{ createdChars.join('、') }}（形象待生成）</span>
              </div>
            </div>
            <div class="cs-protect">
              <div class="cs-protect-title">不会修改：</div>
              <div class="cs-protect-item">✓ 已有 {{ existingSceneCount }} 个故事段</div>
              <div class="cs-protect-item" v-if="hasShots">✓ 已有 Shot</div>
              <div class="cs-protect-item" v-if="hasImages">✓ 已生成图片</div>
              <div class="cs-protect-item" v-if="hasVideos">✓ 已生成视频</div>
            </div>
            <div class="cs-go-design" v-if="createdChars.length">
              <span>其中 {{ createdChars.length }} 个新角色尚未生成形象</span>
              <button class="btn ghost go-design-btn" :disabled="applying" @click="onApply(true)">
                采用后前往角色设计
              </button>
            </div>
          </div>
        </template>
      </div>

      <!-- 底部 -->
      <footer class="sc-foot" v-if="!loadingInfo">
        <button class="btn ghost" @click="$emit('close')">取消</button>
        <span class="foot-spacer"></span>
        <template v-if="step === 1">
          <button
            v-if="draft && draft.continuation_text"
            class="btn ghost" :disabled="applying" @click="onAppendStoryOnly"
          >仅追加到完整故事</button>
          <button
            v-if="draft && draft.continuation_text"
            class="btn primary" :disabled="organizing" @click="onAdoptAndOrganize"
          >{{ organizing ? '整理中…' : '采用并整理新故事段' }}</button>
        </template>
        <template v-if="step === 2">
          <button class="btn ghost" @click="back">返回修改</button>
          <button
            class="btn primary"
            :disabled="unresolvedCount > 0"
            :title="unresolvedCount > 0 ? `还有 ${unresolvedCount} 个角色需要处理` : ''"
            @click="next"
          >{{ unresolvedCount > 0 ? `还有 ${unresolvedCount} 个角色未处理` : '下一步' }}</button>
        </template>
        <template v-if="step === 3">
          <button class="btn ghost" @click="back">返回修改</button>
          <button class="btn primary" :disabled="applying" @click="onApply(false)">
            {{ applying ? '追加中…' : '确认追加' }}
          </button>
        </template>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';
import { apiClient } from '@/api';
import type { StoryContinuationDraft, DraftScene, ContinuationScale } from '@/api';
import CharacterResolutionPanel from './CharacterResolutionPanel.vue';

const { message, dialog } = createDiscreteApi(['message', 'dialog'], {
  configProviderProps: discreteConfigProviderProps,
});

const props = defineProps<{ projectName: string }>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'applied', firstNewSceneId: string | null, goDesignChar?: string | null): void }>();

const step = ref<1 | 2 | 3>(1);
const draft = ref<StoryContinuationDraft | null>(null);
const projectChars = ref<string[]>([]);
const storyEnding = ref('');
const direction = ref('');
const scale = ref<ContinuationScale>('standard');
const loadingInfo = ref(false);
const generating = ref(false);
const organizing = ref(false);
const saving = ref(false);
const applying = ref(false);
// 已有内容统计（Step 3 "不会修改" 展示）
const existingSceneCount = ref(0);
const hasShots = ref(false);
const hasImages = ref(false);
const hasVideos = ref(false);

let saveTimer: number | null = null;
let loadSeq = 0;

const steps = [
  { n: 1 as const, label: '续写' },
  { n: 2 as const, label: '新故事段与角色' },
  { n: 3 as const, label: '确认追加' },
];
const scaleOptions = [
  { v: 'short' as const, l: '短篇', h: '约 1-2 段' },
  { v: 'standard' as const, l: '标准', h: '约 3-4 段' },
  { v: 'long' as const, l: '较长', h: '约 5-7 段' },
];

const unresolvedCount = computed(() =>
  draft.value ? draft.value.character_resolution.filter(r => r.status === 'unresolved').length : 0);
const resolvedCount = computed(() =>
  draft.value ? draft.value.character_resolution.length - unresolvedCount.value : 0);
const createdChars = computed(() =>
  draft.value
    ? draft.value.character_resolution.filter(r => r.status === 'created' && r.matched_character_name).map(r => r.matched_character_name!)
    : []);

// ---- 初始化 ----
async function init() {
  if (!props.projectName) return;
  const mySeq = ++loadSeq;
  loadingInfo.value = true;
  try {
    await loadProjectInfo();
    try {
      const d = await apiClient.getContinuationDraft(props.projectName);
      if (mySeq !== loadSeq) return;
      draft.value = d;
      direction.value = d.direction;
      scale.value = d.scale;
      step.value = d.stage === 'scenes_ready' ? 2 : (d.stage === 'ready_to_apply' ? 3 : 1);
    } catch {
      if (mySeq !== loadSeq) return;
      // 无草稿，留在 step 1
    }
  } finally {
    if (mySeq === loadSeq) loadingInfo.value = false;
  }
}

async function loadProjectInfo() {
  try {
    const data = await apiClient.getProjectShots(props.projectName);
    projectChars.value = (data.characters || []).map(c => c.name);
    const story = data.story_text || '';
    storyEnding.value = story.length > 500 ? story.slice(-500) : story;
    const scenes = data.scenes || [];
    existingSceneCount.value = scenes.length;
    hasShots.value = scenes.some((s: any) => (s.shots || []).length > 0);
    hasImages.value = scenes.some((s: any) => (s.shots || []).some((sh: any) => sh?.first_frame?.candidates?.length));
    hasVideos.value = scenes.some((s: any) => (s.shots || []).some((sh: any) => sh?.video?.local));
  } catch {
    projectChars.value = [];
  }
}

async function loadProjectChars() {
  try {
    const r = await apiClient.getProjectCharactersFull(props.projectName);
    projectChars.value = (r.characters || []).map(c => c.name);
  } catch { /* keep */ }
}

// ---- 生成续写 ----
async function onGenerate() {
  const mySeq = ++loadSeq;
  generating.value = true;
  try {
    const d = await apiClient.continueStory(props.projectName, { direction: direction.value, scale: scale.value });
    if (mySeq !== loadSeq) return;
    draft.value = d;
    step.value = 1;
    message.success('续写已生成');
  } catch (e: any) {
    if (mySeq === loadSeq) message.error(`续写失败: ${e.message || e}`);
  } finally {
    if (mySeq === loadSeq) generating.value = false;
  }
}

// ---- 续写正文编辑 ----
function onContInput(val: string) {
  if (!draft.value) return;
  draft.value.continuation_text = val;
  scheduleSave();
}
function onNewSceneInput(sc: DraftScene, val: string) { sc.summary = val; scheduleSave(); }
function removeNewScene(i: number) {
  if (!draft.value) return;
  draft.value.new_scene_drafts.splice(i, 1);
  scheduleSave();
}

// ---- 采用并整理新故事段 ----
async function onAdoptAndOrganize() {
  if (!draft.value || !draft.value.continuation_text.trim()) {
    message.warning('续写内容为空');
    return;
  }
  organizing.value = true;
  try {
    await doSaveDraft();
    const d = await apiClient.organizeContinuation(props.projectName);
    draft.value = d;
    step.value = 2;
  } catch (e: any) {
    message.error(`整理失败: ${e.message || e}`);
  } finally {
    organizing.value = false;
  }
}

// ---- Step 2：角色检查回调（调续写 API）----
async function onResolve(params: {
  mention: string; action: 'match' | 'create' | 'ignore';
  matched_character_name?: string; name?: string; description?: string;
}) {
  if (!draft.value) return;
  const d = await apiClient.resolveContinuationCharacter(props.projectName, params);
  draft.value = d;
  if (params.action === 'create') await loadProjectChars();
}
async function onGenSingle(mention: string): Promise<{ description: string }> {
  return await apiClient.draftContinuationCharacter(props.projectName, mention);
}
async function onBatchFill(): Promise<{ characters: Array<{ name: string; description: string }> }> {
  if (!draft.value) return { characters: [] };
  const names = draft.value.character_resolution.filter(r => r.status === 'unresolved').map(r => r.mention);
  if (!names.length) return { characters: [] };
  return await apiClient.draftContinuationCharactersBatch(props.projectName, names);
}
async function onBatchCreate(targets: Array<{ mention: string; name: string; description: string }>) {
  if (!draft.value) return;
  for (const t of targets) {
    const d = await apiClient.resolveContinuationCharacter(props.projectName, {
      mention: t.mention, action: 'create', name: t.name, description: t.description,
    });
    draft.value = d;
  }
  await loadProjectChars();
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
    await apiClient.saveContinuationDraft(props.projectName, {
      direction: direction.value,
      scale: scale.value,
      continuation_text: draft.value.continuation_text,
      new_scene_drafts: draft.value.new_scene_drafts,
      character_resolution: draft.value.character_resolution,
      stage: stageForStep(),
    });
  } catch (e: any) {
    message.error(`草稿保存失败: ${e.message || e}`);
  } finally {
    saving.value = false;
  }
}
function stageForStep(): string {
  if (step.value === 1) return 'continuation_ready';
  if (step.value === 2) return 'scenes_ready';
  return 'ready_to_apply';
}

// ---- 导航 ----
function next() {
  if (step.value === 2) {
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
  if (n < step.value) step.value = n;
}

// ---- 仅追加到完整故事 ----
async function onAppendStoryOnly() {
  applying.value = true;
  try {
    await apiClient.appendStoryOnly(props.projectName);
    message.success('已追加到完整故事');
    emit('applied', null);
    emit('close');
  } catch (e: any) {
    const msg = e.message || '';
    if (msg.includes('发生变化')) {
      dialog.warning({
        title: '内容已变化', content: '故事在 AI 续写期间已经发生变化。请根据最新故事重新生成续写。',
        positiveText: '我知道了',
      });
    } else {
      message.error(`追加失败: ${msg}`);
    }
  } finally {
    applying.value = false;
  }
}

// ---- 确认追加（append-only apply）----
async function onApply(goDesign = false) {
  applying.value = true;
  try {
    const r = await apiClient.applyContinuation(props.projectName);
    message.success(`已追加 ${r.new_scenes.length} 个故事段`);
    const goDesignChar = goDesign ? (r.new_characters[0] || null) : null;
    emit('applied', r.first_new_scene_id, goDesignChar);
    emit('close');
  } catch (e: any) {
    const msg = e.message || '';
    if (msg.includes('发生变化') || msg.includes('故事段结构')) {
      dialog.warning({
        title: '内容已变化', content: '故事或故事段在 AI 续写期间已经发生变化。请根据最新故事重新生成续写。',
        positiveText: '重新续写', negativeText: '关闭',
        onPositiveClick: () => { step.value = 1; },
      });
    } else if (msg.includes('已不存在')) {
      dialog.warning({
        title: '角色缺失', content: msg, positiveText: '返回角色检查',
        onPositiveClick: () => { step.value = 2; },
      });
    } else if (msg.includes('个角色')) {
      message.warning(msg);
      step.value = 2;
    } else {
      message.error(`追加失败: ${msg}`);
    }
  } finally {
    applying.value = false;
  }
}

// 切项目 -> 关闭
watch(() => props.projectName, (nv, ov) => { if (nv !== ov) emit('close'); });

onMounted(() => init());
</script>

<style lang="scss" scoped>
.sc-overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6); z-index: 1500;
  display: flex; align-items: center; justify-content: center; backdrop-filter: blur(4px);
}
.sc {
  width: 80vw; height: 82vh; max-width: 1400px; min-width: 720px;
  background: var(--bg-root); border: 1px solid var(--border); border-radius: 10px;
  display: flex; flex-direction: column; overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
}
.sc-head {
  display: flex; align-items: center; gap: 12px; padding: 14px 20px;
  border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.sc-title { font-size: 15px; font-weight: 600; color: var(--text-primary); }
.sc-sub { font-size: 12px; color: var(--text-muted); }
.sc-saving { font-size: 11px; color: var(--text-faint); }
.sc-close {
  margin-left: auto; width: 28px; height: 28px; border-radius: 6px; border: none;
  background: none; color: var(--text-muted); font-size: 16px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
.sc-steps {
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
.sc-body { flex: 1; overflow-y: auto; padding: 18px 20px; }
.sc-loading { display: flex; flex-direction: column; align-items: center; gap: 14px; padding: 60px 0; color: var(--text-muted); font-size: 13px; }
.spinner {
  width: 26px; height: 26px; border-radius: 50%;
  border: 2px solid var(--border); border-top-color: var(--accent);
  animation: sc-spin 0.8s linear infinite;
}
@keyframes sc-spin { to { transform: rotate(360deg); } }

// Step 1
.story-ending {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px;
  margin-bottom: 16px;
}
.se-label { font-size: 11px; color: var(--text-muted); margin-bottom: 6px; }
.se-text { font-size: 13px; color: var(--text-secondary); line-height: 1.6; white-space: pre-wrap; max-height: 140px; overflow-y: auto; }
.gen-controls {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 14px;
  margin-bottom: 16px;
}
.form-label { display: block; font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }
.text-area {
  width: 100%; background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px;
  padding: 8px 10px; font-size: 13px; color: var(--text-primary); outline: none; font-family: inherit;
  resize: vertical; line-height: 1.5; &:focus { border-color: var(--accent); }
}
.scale-row { margin: 14px 0; }
.scale-btns { display: flex; gap: 8px; flex-wrap: wrap; }
.scale-btn {
  padding: 8px 16px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg-root);
  color: var(--text-secondary); font-size: 13px; cursor: pointer; display: flex; flex-direction: column; align-items: center; gap: 2px;
  .scale-hint { font-size: 10px; color: var(--text-faint); }
  &.on { border-color: var(--accent); color: var(--text-primary); background: var(--bg-hover); }
}
.cont-draft { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
.cont-textarea { background: var(--bg-root); min-height: 180px; }

.pane-head {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 13px; color: var(--text-secondary); margin-bottom: 14px;
}

// Step 2 scene list
.scene-list { display: flex; flex-direction: column; gap: 10px; margin-bottom: 18px; }
.ds-card {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px;
}
.ds-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.ds-no {
  flex-shrink: 0; width: 22px; height: 22px; border-radius: 50%; background: var(--accent);
  color: #fff; font-size: 11px; font-weight: 600; display: flex; align-items: center; justify-content: center;
}
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
.ds-mention { padding: 2px 8px; border-radius: 10px; background: var(--bg-hover); color: var(--text-secondary); font-size: 11px; }
.ds-mnone { font-size: 11px; color: var(--text-faint); }
.empty { text-align: center; color: var(--text-muted); font-size: 13px; padding: 24px 0; }

// Step 3
.confirm-summary {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px;
  margin-bottom: 14px;
}
.cs-row { font-size: 13px; color: var(--text-secondary); margin-bottom: 8px; b { color: var(--text-primary); } }
.cs-chars { margin-left: 8px; font-size: 12px; color: var(--text-muted); }
.cs-protect {
  background: var(--bg-surface); border: 1px dashed var(--border); border-radius: 8px; padding: 14px 16px;
}
.cs-protect-title { font-size: 12px; color: var(--text-secondary); margin-bottom: 8px; }
.cs-protect-item { font-size: 13px; color: #4ade80; margin-bottom: 4px; }
.cs-go-design {
  margin-top: 14px; padding: 12px 16px; background: var(--bg-surface);
  border: 1px solid var(--border); border-radius: 8px;
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  font-size: 13px; color: var(--text-secondary);
}
.go-design-btn { border-color: var(--accent); color: var(--accent); }

// 底部
.sc-foot {
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
