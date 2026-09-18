<template>
  <div class="cds-overlay" @click.self="$emit('close')">
    <div class="cds">
      <!-- 头部 -->
      <header class="cds-head">
        <span class="cds-title">AI 角色设计</span>
        <span class="cds-sub" v-if="chars.length">{{ viewsDoneCount }}/{{ chars.length }} 角色完成 · {{ draftCount }} 草稿</span>
        <button class="cds-close" @click="$emit('close')">✕</button>
      </header>

      <div class="cds-main">
        <!-- 左：角色列表 -->
        <aside class="cds-list">
          <button
            class="batch-btn" :disabled="batchBusy || !chars.length"
            @click="onBatchDesign"
          >{{ batchBusy ? '设计中…' : 'AI 补全全部角色设定' }}</button>

          <div class="char-items">
            <div
              v-for="ch in chars" :key="ch.name"
              class="char-item" :class="{ active: currentName === ch.name }"
              @click="selectChar(ch.name)"
            >
              <span class="ci-mark">{{ ch.viewsDone === 3 ? '✓' : '○' }}</span>
              <span class="ci-name">{{ ch.name }}</span>
              <span class="ci-status" :class="ch.statusClass">{{ ch.statusText }}</span>
              <span class="ci-draft" v-if="ch.hasDraft" title="有 AI 设计草稿">✎</span>
            </div>
            <div v-if="!chars.length" class="empty">项目无角色</div>
          </div>
        </aside>

        <!-- 右：当前角色 -->
        <section class="cds-detail" v-if="currentChar">
          <div class="detail-head">
            <span class="dh-name">{{ currentChar.name }}</span>
            <span class="dh-status" :class="currentChar.statusClass">{{ currentChar.statusText }}</span>
          </div>

          <!-- 无草稿 -->
          <div v-if="!editDraft" class="no-draft">
            <p>该角色还没有 AI 视觉设计。</p>
            <button class="btn primary" :disabled="designing" @click="onDesign">
              {{ designing ? '设计中…' : 'AI 设计角色' }}
            </button>
          </div>

          <!-- 有草稿 -->
          <div v-else class="draft-area">
            <div class="field-group">
              <div class="field-label">故事中的作用</div>
              <div class="field-text">{{ editDraft.role_summary || '（空）' }}</div>
            </div>

            <div class="field-grid">
              <div class="field"><span class="f-label">视觉概念</span><span class="f-val">{{ editDraft.visual_concept || '—' }}</span></div>
              <div class="field"><span class="f-label">外貌</span><span class="f-val">{{ editDraft.appearance || '—' }}</span></div>
              <div class="field"><span class="f-label">服装/身体</span><span class="f-val">{{ editDraft.clothing_or_body || '—' }}</span></div>
              <div class="field"><span class="f-label">识别特征</span><span class="f-val">{{ editDraft.distinctive_features || '—' }}</span></div>
              <div class="field"><span class="f-label">配色</span><span class="f-val">{{ editDraft.color_palette || '—' }}</span></div>
            </div>

            <div class="field" v-if="editDraft.consistency_rules.length">
              <span class="f-label">一致性规则</span>
              <ul class="rules"><li v-for="(r, i) in editDraft.consistency_rules" :key="i">{{ r }}</li></ul>
            </div>

            <div class="field-group">
              <div class="field-label">最终生成描述（generation_prompt，可编辑）</div>
              <textarea
                class="prompt-area" rows="5"
                v-model="editDraft.generation_prompt"
                @input="scheduleSaveDraft"
                placeholder="英文生成 prompt…"
              ></textarea>
            </div>

            <div class="actions">
              <button class="btn ghost" :disabled="designing" @click="onRedesign">
                {{ designing ? '设计中…' : '重新设计' }}
              </button>
              <button class="btn primary" :disabled="applying || !editDraft.generation_prompt.trim()" @click="onApply">
                {{ applying ? '应用中…' : '应用设定' }}
              </button>
            </div>

            <!-- 三视图 -->
            <div class="views-block">
              <div class="field-label">三视图</div>
              <div class="views-row">
                <div class="view-cell" v-for="v in ['front','side','back']" :key="v">
                  <span class="vc-mark">{{ currentChar.viewsDone >= viewIndex(v) ? '✓' : '○' }}</span>
                  <span class="vc-name">{{ viewName(v) }}</span>
                </div>
              </div>
              <button class="btn primary gen-btn" :disabled="generating || !currentChar.description_en" @click="onGenerateViews">
                {{ generating ? '生成中…' : (currentChar.viewsDone === 3 ? '重新生成三视图' : '生成三视图') }}
              </button>
              <div class="gen-hint" v-if="currentChar.viewsDone < 3 && !currentChar.description_en">请先「应用设定」生成描述</div>
            </div>
          </div>
        </section>
        <section class="cds-detail empty-detail" v-else>
          <div class="empty">选择左侧角色开始设计</div>
        </section>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';
import { apiClient } from '@/api';
import type { CharacterDesignDraft, CharacterDesignDraftState } from '@/api';
import { useProjectStore } from '@/store';

const { message, dialog } = createDiscreteApi(['message', 'dialog'], { configProviderProps: discreteConfigProviderProps });
const store = useProjectStore();

const props = defineProps<{ projectName: string; initialCharacterName?: string | null }>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'applied'): void; (e: 'generated'): void }>();

interface CharInfo {
  name: string;
  description_en: string;
  viewsDone: number;          // 0-3
  statusText: string;
  statusClass: string;
  hasDraft: boolean;
}

const chars = ref<CharInfo[]>([]);
const designs = ref<Record<string, CharacterDesignDraftState>>({});
const currentName = ref<string | null>(null);
const editDraft = ref<CharacterDesignDraft | null>(null);
const designing = ref(false);
const applying = ref(false);
const batchBusy = ref(false);
const generating = ref(false);
let saveTimer: number | null = null;
let loadSeq = 0;

const currentChar = computed(() => chars.value.find(c => c.name === currentName.value) || null);
const viewsDoneCount = computed(() => chars.value.filter(c => c.viewsDone === 3).length);
const draftCount = computed(() => chars.value.filter(c => c.hasDraft).length);

function viewIndex(v: string) { return v === 'front' ? 1 : v === 'side' ? 2 : 3; }
function viewName(v: string) { return v === 'front' ? '正面' : v === 'side' ? '侧面' : '背面'; }

function statusFromDone(done: number) {
  if (done === 3) return { text: '三视图完成', cls: 'done' };
  if (done === 0) return { text: '尚未生成', cls: 'none' };
  return { text: `已完成 ${done}/3`, cls: 'partial' };
}

async function loadAll() {
  if (!props.projectName) return;
  const mySeq = ++loadSeq;
  try {
    const [fullRes, draftRes] = await Promise.all([
      apiClient.getProjectCharactersFull(props.projectName),
      apiClient.getCharacterDesigns(props.projectName).catch(() => ({ designs: {} })),
    ]);
    if (mySeq !== loadSeq) return;
    designs.value = draftRes.designs || {};
    chars.value = (fullRes.characters || []).map(c => {
      const views = c.views || {};
      const done = (['front', 'side', 'back'] as const).filter(k => views[k]?.seg || views[k]?.raw).length;
      const st = statusFromDone(done);
      return {
        name: c.name,
        description_en: c.description_en || '',
        viewsDone: done,
        statusText: st.text,
        statusClass: st.cls,
        hasDraft: c.name in designs.value,
      };
    });
    // 选中
    if (!currentName.value || !chars.value.find(c => c.name === currentName.value)) {
      const initial = props.initialCharacterName && chars.value.find(c => c.name === props.initialCharacterName)
        ? props.initialCharacterName : (chars.value[0]?.name || null);
      currentName.value = initial;
    }
    syncEditDraft();
  } catch (e: any) {
    message.error(`加载失败: ${e.message || e}`);
  }
}

function syncEditDraft() {
  if (!currentName.value) { editDraft.value = null; return; }
  const state = designs.value[currentName.value];
  editDraft.value = state ? { ...state.draft, consistency_rules: [...state.draft.consistency_rules] } : null;
}

function selectChar(name: string) {
  currentName.value = name;
  syncEditDraft();
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
}

// ---- AI 设计 / 重新设计 ----
async function onDesign() {
  if (!currentName.value) return;
  await runDesign(currentName.value);
}
async function onRedesign() {
  if (!currentName.value) return;
  // 草稿已被手动改过 -> 确认（§13）
  if (editDraft.value && isDraftDirty()) {
    dialog.warning({
      title: '重新设计', content: '重新设计会覆盖你对当前草稿的修改。',
      positiveText: '重新设计', negativeText: '取消',
      onPositiveClick: () => runDesign(currentName.value!),
    });
  } else {
    await runDesign(currentName.value);
  }
}
function isDraftDirty(): boolean {
  if (!currentName.value) return false;
  const saved = designs.value[currentName.value]?.draft;
  if (!saved || !editDraft.value) return false;
  return editDraft.value.generation_prompt !== saved.generation_prompt;
}

async function runDesign(name: string) {
  designing.value = true;
  try {
    const state = await apiClient.designCharacter(props.projectName, name);
    designs.value[name] = state;
    syncEditDraft();
    refreshCharMeta(name);
    message.success('AI 设计已生成');
  } catch (e: any) {
    message.error(`设计失败: ${e.message || e}`);
  } finally {
    designing.value = false;
  }
}

// ---- 保存草稿编辑 ----
function scheduleSaveDraft() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => { saveTimer = null; doSaveDraft(); }, 800);
}
async function doSaveDraft() {
  if (!currentName.value || !editDraft.value) return;
  try {
    const state = await apiClient.saveCharacterDesign(props.projectName, currentName.value, editDraft.value);
    designs.value[currentName.value] = state;
  } catch (e: any) {
    message.error(`保存失败: ${e.message || e}`);
  }
}

// ---- 应用设定 ----
async function onApply() {
  if (!currentName.value || !editDraft.value) return;
  // 先保存编辑
  await doSaveDraft();
  // 已有三视图 -> 提示（§15）
  if (currentChar.value && currentChar.value.viewsDone > 0) {
    dialog.warning({
      title: '角色已有三视图',
      content: '这个角色已经有生成好的三视图。修改角色文字设定不会自动改变已有图片，但之后重新生成角色或新建参考时可能使用新的设定。',
      positiveText: '只更新文字设定', negativeText: '取消',
      onPositiveClick: () => doApply(),
    });
  } else {
    await doApply();
  }
}
async function doApply() {
  if (!currentName.value) return;
  applying.value = true;
  try {
    const r = await apiClient.applyCharacterDesign(props.projectName, currentName.value);
    // 更新本地 description_en
    const ch = chars.value.find(c => c.name === currentName.value);
    if (ch) ch.description_en = r.description_en;
    // draft 状态更新（重新读 designs）
    await apiClient.getCharacterDesigns(props.projectName).then(d => { designs.value = d.designs || {}; refreshCharMeta(currentName.value!); });
    message.success('设定已应用到角色');
    emit('applied');
  } catch (e: any) {
    const msg = e.message || '';
    if (msg.includes('变化')) {
      dialog.warning({ title: '内容已变化', content: msg + ' 建议根据最新内容重新设计。', positiveText: '我知道了' });
    } else {
      message.error(`应用失败: ${msg}`);
    }
  } finally {
    applying.value = false;
  }
}

// ---- 批量 ----
async function onBatchDesign() {
  batchBusy.value = true;
  try {
    const r = await apiClient.designAllCharacters(props.projectName);
    const ok = r.results.filter(x => x.ok).length;
    const fail = r.results.filter(x => !x.ok);
    await apiClient.getCharacterDesigns(props.projectName).then(d => { designs.value = d.designs || {}; });
    chars.value.forEach(c => { c.hasDraft = c.name in designs.value; });
    syncEditDraft();
    if (fail.length) {
      message.warning(`已设计 ${ok} 个，失败 ${fail.length} 个：${fail.map(f => f.name).join('、')}`);
    } else {
      message.success(`已设计 ${ok} 个角色`);
    }
  } catch (e: any) {
    message.error(`批量设计失败: ${e.message || e}`);
  } finally {
    batchBusy.value = false;
  }
}

// ---- 生成三视图（复用现有 chain 生成 API，§16）----
async function onGenerateViews() {
  if (!currentName.value || !currentChar.value) return;
  if (!currentChar.value.description_en) { message.warning('请先应用设定生成描述'); return; }
  generating.value = true;
  try {
    const r = await apiClient.generateCharacterChain({
      description: currentChar.value.description_en, style: 'anime',
    });
    const views: Record<string, { raw?: string; seg?: string }> = {};
    for (const v of ['front', 'side', 'back'] as const) {
      views[v] = { raw: r.raw_views[v], seg: r.seg_views[v] };
    }
    await apiClient.bindCharacterToProject(props.projectName, {
      name: currentName.value, description: currentChar.value.description_en, views,
    });
    store.cacheCharacterState(currentName.value, currentChar.value.description_en, views);
    await loadAll();   // 刷新 views 状态
    message.success('三视图已生成');
    emit('generated');
    maybeAutoJump();
  } catch (e: any) {
    message.error(`三视图生成失败: ${e.message || e}`);
  } finally {
    generating.value = false;
  }
}

function maybeAutoJump() {
  // §17：生成完成后自动跳到下一个待处理（未完成三视图）角色，便捷但不强制
  if (!currentName.value) return;
  const idx = chars.value.findIndex(c => c.name === currentName.value);
  const next = chars.value.slice(idx + 1).find(c => c.viewsDone < 3)
    || chars.value.slice(0, idx).find(c => c.viewsDone < 3);
  if (next) selectChar(next.name);
}

function refreshCharMeta(name: string) {
  const ch = chars.value.find(c => c.name === name);
  if (ch) ch.hasDraft = name in designs.value;
}

watch(() => props.projectName, (nv, ov) => { if (nv !== ov) emit('close'); });
onMounted(() => loadAll());
</script>

<style lang="scss" scoped>
.cds-overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6); z-index: 1500;
  display: flex; align-items: center; justify-content: center; backdrop-filter: blur(4px);
}
.cds {
  width: 86vw; height: 84vh; max-width: 1500px; min-width: 760px;
  background: var(--bg-root); border: 1px solid var(--border); border-radius: 10px;
  display: flex; flex-direction: column; overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
}
.cds-head {
  display: flex; align-items: center; gap: 12px; padding: 14px 20px;
  border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.cds-title { font-size: 15px; font-weight: 600; color: var(--text-primary); }
.cds-sub { font-size: 12px; color: var(--text-muted); }
.cds-close {
  margin-left: auto; width: 28px; height: 28px; border-radius: 6px; border: none;
  background: none; color: var(--text-muted); font-size: 16px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
.cds-main { flex: 1; display: flex; overflow: hidden; }

/* 左列表 */
.cds-list {
  width: 260px; flex-shrink: 0; border-right: 1px solid var(--border);
  display: flex; flex-direction: column; overflow: hidden;
}
.batch-btn {
  margin: 12px; padding: 8px; border-radius: 6px; border: 1px solid var(--accent);
  background: transparent; color: var(--accent); font-size: 12px; cursor: pointer;
  &:hover:not(:disabled) { background: var(--accent); color: var(--accent-text); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
}
.char-items { flex: 1; overflow-y: auto; padding: 0 8px 12px; }
.char-item {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 6px;
  cursor: pointer; font-size: 13px;
  &:hover { background: var(--bg-hover); }
  &.active { background: var(--bg-hover); .ci-name { color: var(--accent); } }
}
.ci-mark { width: 16px; text-align: center; color: var(--text-faint); }
.char-item.active .ci-mark { color: var(--accent); }
.ci-name { flex: 1; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ci-status { font-size: 10px; color: var(--text-faint); &.done { color: #4ade80; } &.partial { color: var(--text-muted); } }
.ci-draft { color: var(--accent); font-size: 12px; }
.empty { text-align: center; color: var(--text-muted); font-size: 13px; padding: 32px 0; }

/* 右详情 */
.cds-detail { flex: 1; overflow-y: auto; padding: 18px 22px; }
.empty-detail { display: flex; align-items: center; justify-content: center; }
.detail-head { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.dh-name { font-size: 16px; font-weight: 600; color: var(--text-primary); }
.dh-status { font-size: 11px; color: var(--text-muted); &.done { color: #4ade80; } }

.no-draft {
  text-align: center; padding: 40px 0; color: var(--text-muted); font-size: 13px;
  button { margin-top: 14px; }
}
.draft-area { display: flex; flex-direction: column; gap: 14px; }
.field-group { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }
.field-label { font-size: 11px; color: var(--text-muted); margin-bottom: 6px; }
.field-text { font-size: 13px; color: var(--text-secondary); line-height: 1.5; }
.field-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px;
}
.field { font-size: 12px; }
.f-label { display: block; color: var(--text-muted); margin-bottom: 3px; }
.f-val { color: var(--text-secondary); }
.rules { margin: 4px 0 0; padding-left: 18px; font-size: 12px; color: var(--text-secondary); li { margin-bottom: 2px; } }
.prompt-area {
  width: 100%; background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px;
  padding: 10px; font-size: 13px; color: var(--text-primary); outline: none; font-family: inherit;
  resize: vertical; line-height: 1.5; &:focus { border-color: var(--accent); }
}
.actions { display: flex; gap: 8px; }
.views-block {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px;
}
.views-row { display: flex; gap: 14px; margin: 8px 0 12px; }
.view-cell { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text-secondary); }
.vc-mark { color: var(--text-faint); }
.view-cell:has(.vc-mark:empty) { /* noop */ }
.gen-btn { width: 100%; }
.gen-hint { font-size: 11px; color: var(--text-faint); margin-top: 6px; text-align: center; }

.btn {
  padding: 7px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none;
  &:disabled { opacity: 0.4; cursor: not-allowed; }
}
.btn.ghost { background: var(--bg-surface); color: var(--text-muted); border: 1px solid var(--border); &:hover:not(:disabled) { background: var(--bg-hover); } }
.btn.primary { background: var(--accent); color: var(--accent-text); &:hover:not(:disabled) { background: var(--accent-hover); } }
</style>
