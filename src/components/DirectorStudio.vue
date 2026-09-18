<template>
  <div class="ds-overlay" @click.self="$emit('close')">
    <div class="ds">
      <!-- 头部 -->
      <header class="ds-head">
        <span class="ds-title">AI 导演 · 故事段 {{ sceneId }}</span>
        <span class="ds-sub" v-if="draft">{{ draft.shots.length }} 镜头 · 预计 {{ totalSeconds }}s</span>
        <span class="ds-saving" v-if="saving">保存中…</span>
        <button class="ds-close" @click="$emit('close')">✕</button>
      </header>

      <div class="ds-body">
        <!-- 顶部：故事段 + 角色 + 导演要求 + 规划按钮 -->
        <div class="scene-box">
          <div class="se-label">故事段</div>
          <div class="se-text">{{ sceneSummary || '（未填写故事）' }}</div>
          <div class="se-chars" v-if="sceneCharacters.length">
            <span class="se-clabel">出场角色：</span>
            <span class="se-char" v-for="name in sceneCharacters" :key="name">{{ name }}</span>
          </div>
          <div class="se-chars" v-else><span class="se-clabel">出场角色：无</span></div>

          <label class="se-label">导演要求（可选，不填则 AI 自己规划）</label>
          <textarea
            class="note-area" rows="2" v-model="directorNote"
            placeholder="例如：这段希望节奏慢一点，重点表现机甲重新启动的压迫感。"
          ></textarea>
          <button class="btn primary plan-btn" :disabled="planning" @click="onPlan">
            {{ planning ? '规划中…' : (draft && draft.shots.length ? '重新规划' : 'AI 规划镜头') }}
          </button>
        </div>

        <!-- 已有镜头提示（§十六：只查看，不覆盖）-->
        <div v-if="hasExistingShots && draft" class="existing-warn">
          这个故事段已经有镜头。AI 可以提供新的镜头规划建议，但本阶段不会自动覆盖或删除已有镜头。
        </div>

        <!-- 镜头草稿列表 -->
        <div v-if="draft && draft.shots.length" class="shots-section">
          <div class="shots-head">
            <span>AI 建议：{{ draft.shots.length }} 个镜头 · 预计 {{ totalSeconds }} 秒</span>
          </div>

          <!-- 角色覆盖 -->
          <div class="coverage" v-if="sceneCharacters.length">
            <span class="cov-label">角色覆盖</span>
            <span v-for="name in sceneCharacters" :key="name" class="cov-item" :class="{ ok: isCovered(name) }">
              {{ isCovered(name) ? '✓' : '⚠' }} {{ name }}
              <span class="cov-shots" v-if="isCovered(name)">：镜头 {{ coveredShots(name) }}</span>
              <span class="cov-miss" v-else>尚未安排</span>
            </span>
          </div>

          <div class="shot-list">
            <div
              v-for="(sh, i) in draft.shots" :key="sh.draft_id"
              class="shot-card" :class="{ dragging: dragIdx === i }"
              draggable="true"
              @dragstart="onDragStart(i)" @dragover.prevent="onDragOver(i)"
              @drop="onDrop(i)" @dragend="onDragEnd"
            >
              <div class="shot-head">
                <span class="shot-no">{{ i + 1 }}</span>
                <span class="shot-grip" title="拖拽排序">⠿</span>
                <label class="dur-label">时长</label>
                <input class="dur-input" type="number" min="1" max="30" step="1"
                       :value="sh.target_duration_s"
                       @input="onDurInput(sh, ($event.target as HTMLInputElement).value)" />
                <span class="dur-unit">秒</span>
                <button class="shot-del" title="删除该镜头" @click="removeShot(i)">×</button>
              </div>
              <label class="field-label">本镜意图</label>
              <textarea
                class="intent-area" rows="2"
                :value="sh.intent"
                @input="onIntentInput(sh, ($event.target as HTMLTextAreaElement).value)"
                placeholder="这一镜具体负责表现什么…"
              ></textarea>
              <div class="char-select" v-if="sceneCharacters.length">
                <span class="cs-label">本镜出场</span>
                <button
                  v-for="name in sceneCharacters" :key="name"
                  class="chip" :class="{ on: sh.characters.includes(name) }"
                  @click="toggleChar(sh, name)"
                >{{ name }}</button>
              </div>
              <div class="purpose" v-if="sh.purpose">
                <span class="pur-label">作用：</span>{{ sh.purpose }}
              </div>
            </div>
          </div>
          <button class="add-shot-btn" @click="addShot">+ 添加手工镜头</button>
        </div>
      </div>

      <!-- 底部 -->
      <footer class="ds-foot">
        <button class="btn ghost" @click="$emit('close')">{{ hasExistingShots ? '关闭' : '取消' }}</button>
        <span class="foot-spacer"></span>
        <button
          v-if="!hasExistingShots && draft && draft.shots.length"
          class="btn primary" :disabled="applying || !coverageOk"
          :title="!coverageOk ? '有角色尚未安排到任何镜头' : ''"
          @click="onApply"
        >{{ applying ? '采用中…' : '采用镜头方案' }}</button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';
import { apiClient } from '@/api';
import type { DirectorDraft, DirectorShotDraft } from '@/api';

const { message, dialog } = createDiscreteApi(['message', 'dialog'], { configProviderProps: discreteConfigProviderProps });

const props = defineProps<{
  projectName: string;
  sceneId: string;
  sceneSummary: string;
  sceneCharacters: string[];
  hasExistingShots: boolean;
}>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'applied', firstNewShotId: string | null): void }>();

const draft = ref<DirectorDraft | null>(null);
const directorNote = ref('');
const planning = ref(false);
const applying = ref(false);
const saving = ref(false);
const dragIdx = ref(-1);
let saveTimer: number | null = null;
let loadSeq = 0;

const totalSeconds = computed(() =>
  draft.value ? draft.value.shots.reduce((n, s) => n + (s.target_duration_s || 0), 0) : 0);

const coverageOk = computed(() =>
  props.sceneCharacters.length === 0 || props.sceneCharacters.every(n => isCovered(n)));

function isCovered(name: string): boolean {
  return !!draft.value?.shots.some(s => s.characters.includes(name));
}
function coveredShots(name: string): string {
  if (!draft.value) return '';
  const idxs = draft.value.shots.map((s, i) => s.characters.includes(name) ? i + 1 : null).filter(Boolean);
  return idxs.join('、');
}

// ---- 初始化：尝试恢复草稿 ----
async function init() {
  if (!props.projectName || !props.sceneId) return;
  const mySeq = ++loadSeq;
  try {
    const d = await apiClient.getDirectorDraft(props.projectName, props.sceneId);
    if (mySeq !== loadSeq) return;
    draft.value = d;
    directorNote.value = d.director_note || '';
  } catch {
    if (mySeq !== loadSeq) return;
    // 无草稿，留空等用户点 AI 规划镜头
  }
}

// ---- AI 规划 / 重新规划 ----
async function onPlan() {
  if (draft.value && draft.value.shots.length && isDirty()) {
    dialog.warning({
      title: '重新规划', content: '重新规划会覆盖当前尚未应用的镜头方案。',
      positiveText: '重新规划', negativeText: '取消',
      onPositiveClick: () => runPlan(),
    });
  } else {
    await runPlan();
  }
}
function isDirty(): boolean {
  // 简化：只要有过手工编辑就认为 dirty（实际由 save 标记，这里宽松判断）
  return true;
}

async function runPlan() {
  const mySeq = ++loadSeq;
  planning.value = true;
  try {
    const d = await apiClient.planDirector(props.projectName, props.sceneId, directorNote.value);
    if (mySeq !== loadSeq) return;
    draft.value = d;
    message.success(`AI 建议规划 ${d.shots.length} 个镜头`);
  } catch (e: any) {
    if (mySeq === loadSeq) message.error(`规划失败: ${e.message || e}`);
  } finally {
    if (mySeq === loadSeq) planning.value = false;
  }
}

// ---- 草稿编辑 + 自动保存 ----
function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => { saveTimer = null; doSave(); }, 800);
}
async function doSave() {
  if (!draft.value) return;
  saving.value = true;
  try {
    await apiClient.saveDirectorDraft(props.projectName, props.sceneId, {
      overview: draft.value.overview, shots: draft.value.shots, director_note: directorNote.value,
    });
  } catch (e: any) {
    message.error(`保存失败: ${e.message || e}`);
  } finally {
    saving.value = false;
  }
}

function onIntentInput(sh: DirectorShotDraft, val: string) { sh.intent = val; scheduleSave(); }
function onDurInput(sh: DirectorShotDraft, val: string) {
  const n = parseFloat(val);
  sh.target_duration_s = (!isNaN(n) && n > 0) ? n : 5;
  scheduleSave();
}
function toggleChar(sh: DirectorShotDraft, name: string) {
  const idx = sh.characters.indexOf(name);
  if (idx > -1) sh.characters.splice(idx, 1);
  else sh.characters.push(name);
  scheduleSave();
}
function removeShot(i: number) {
  if (!draft.value) return;
  draft.value.shots.splice(i, 1);
  scheduleSave();
}
function addShot() {
  if (!draft.value) return;
  const n = draft.value.shots.length + 1;
  draft.value.shots.push({
    draft_id: `manual_${Date.now()}`, intent: '', characters: [], target_duration_s: 5, purpose: '',
  });
  scheduleSave();
}

// ---- 拖拽排序 ----
function onDragStart(i: number) { dragIdx.value = i; }
function onDragOver(_i: number) {}
function onDrop(i: number) {
  if (!draft.value || dragIdx.value === -1 || dragIdx.value === i) { dragIdx.value = -1; return; }
  const arr = draft.value.shots.slice();
  const [m] = arr.splice(dragIdx.value, 1);
  arr.splice(i, 0, m);
  draft.value.shots = arr;
  dragIdx.value = -1;
  scheduleSave();
}
function onDragEnd() { dragIdx.value = -1; }

// ---- 采用镜头方案 ----
async function onApply() {
  if (!coverageOk.value) {
    message.warning('有角色尚未安排到任何镜头。');
    return;
  }
  applying.value = true;
  try {
    await doSave();   // 先保存编辑
    const r = await apiClient.applyDirector(props.projectName, props.sceneId);
    message.success(`已创建 ${r.shots.length} 个镜头`);
    emit('applied', r.first_new_shot_id);
    emit('close');
  } catch (e: any) {
    const msg = e.message || '';
    if (msg.includes('发生变化')) {
      dialog.warning({ title: '故事段已变化', content: msg, positiveText: '重新规划',
        onPositiveClick: () => runPlan() });
    } else if (msg.includes('已有镜头')) {
      dialog.warning({ title: '无法覆盖', content: msg, positiveText: '我知道了' });
    } else if (msg.includes('尚未安排')) {
      message.warning(msg);
    } else {
      message.error(`采用失败: ${msg}`);
    }
  } finally {
    applying.value = false;
  }
}

watch(() => props.projectName, (nv, ov) => { if (nv !== ov) emit('close'); });
watch(() => props.sceneId, (nv, ov) => { if (nv !== ov) emit('close'); });
onMounted(() => init());
</script>

<style lang="scss" scoped>
.ds-overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6); z-index: 1500;
  display: flex; align-items: center; justify-content: center; backdrop-filter: blur(4px);
}
.ds {
  width: 82vw; height: 84vh; max-width: 1400px; min-width: 720px;
  background: var(--bg-root); border: 1px solid var(--border); border-radius: 10px;
  display: flex; flex-direction: column; overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
}
.ds-head {
  display: flex; align-items: center; gap: 12px; padding: 14px 20px;
  border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.ds-title { font-size: 15px; font-weight: 600; color: var(--text-primary); }
.ds-sub { font-size: 12px; color: var(--text-muted); }
.ds-saving { font-size: 11px; color: var(--text-faint); }
.ds-close {
  margin-left: auto; width: 28px; height: 28px; border-radius: 6px; border: none;
  background: none; color: var(--text-muted); font-size: 16px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
.ds-body { flex: 1; overflow-y: auto; padding: 18px 20px; }

.scene-box {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 14px;
  margin-bottom: 16px;
}
.se-label { font-size: 11px; color: var(--text-muted); margin-bottom: 6px; margin-top: 10px; }
.se-label:first-child { margin-top: 0; }
.se-text { font-size: 13px; color: var(--text-secondary); line-height: 1.6; margin-bottom: 10px; }
.se-chars { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 6px; }
.se-clabel { font-size: 11px; color: var(--text-muted); }
.se-char { padding: 2px 10px; border-radius: 10px; background: var(--bg-hover); color: var(--text-secondary); font-size: 11px; }
.note-area {
  width: 100%; background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px;
  padding: 8px 10px; font-size: 13px; color: var(--text-primary); outline: none; font-family: inherit;
  resize: vertical; line-height: 1.5; &:focus { border-color: var(--accent); }
}
.plan-btn { margin-top: 10px; }

.existing-warn {
  background: rgba(251, 191, 36, 0.1); border: 1px solid #fbbf24; border-radius: 8px;
  padding: 10px 14px; font-size: 12px; color: #fbbf24; margin-bottom: 16px;
}

.shots-section { }
.shots-head { font-size: 13px; color: var(--text-secondary); margin-bottom: 10px; font-weight: 600; }
.coverage {
  display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px; padding: 10px 12px; margin-bottom: 14px;
}
.cov-label { font-size: 11px; color: var(--text-muted); }
.cov-item { font-size: 12px; color: var(--text-secondary); &.ok { color: #4ade80; } }
.cov-shots { color: var(--text-faint); }
.cov-miss { color: #fbbf24; }

.shot-list { display: flex; flex-direction: column; gap: 10px; }
.shot-card {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px;
  &.dragging { opacity: 0.4; }
}
.shot-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.shot-no {
  flex-shrink: 0; width: 22px; height: 22px; border-radius: 50%; background: var(--accent);
  color: #fff; font-size: 11px; font-weight: 600; display: flex; align-items: center; justify-content: center;
}
.shot-grip { color: var(--text-faint); cursor: grab; font-size: 14px; }
.dur-label { font-size: 11px; color: var(--text-muted); margin-left: auto; }
.dur-input {
  width: 50px; background: var(--bg-root); border: 1px solid var(--border); border-radius: 4px;
  padding: 3px 6px; font-size: 12px; color: var(--text-primary); outline: none; text-align: center;
  &:focus { border-color: var(--accent); }
}
.dur-unit { font-size: 11px; color: var(--text-muted); }
.shot-del {
  width: 22px; height: 22px; border-radius: 4px; border: none; background: none;
  color: var(--text-muted); font-size: 15px; cursor: pointer;
  &:hover { background: #fee2e2; color: #ef4444; }
}
.field-label { display: block; font-size: 11px; color: var(--text-muted); margin-bottom: 4px; }
.intent-area {
  width: 100%; background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px;
  padding: 8px 10px; font-size: 13px; color: var(--text-primary); outline: none; font-family: inherit;
  resize: vertical; line-height: 1.5; &:focus { border-color: var(--accent); }
}
.char-select { margin-top: 8px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.cs-label { font-size: 11px; color: var(--text-muted); margin-right: 4px; }
.chip {
  padding: 3px 10px; border-radius: 12px; border: 1px solid var(--border); background: none;
  color: var(--text-muted); font-size: 12px; cursor: pointer;
  &:hover { border-color: var(--accent); color: var(--text-secondary); }
  &.on { background: var(--accent); border-color: var(--accent); color: #fff; }
}
.purpose { margin-top: 8px; font-size: 12px; color: var(--text-muted); }
.pur-label { color: var(--text-faint); }
.add-shot-btn {
  margin-top: 12px; width: 100%; padding: 9px; border-radius: 6px;
  border: 1px dashed var(--border); background: transparent; color: var(--text-muted);
  font-size: 13px; cursor: pointer; &:hover { border-color: var(--accent); color: var(--accent); }
}

.ds-foot {
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
