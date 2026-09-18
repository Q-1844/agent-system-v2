<template>
  <div class="step-pane">
    <div class="pane-head">
      <span>角色检查 · {{ resolvedCount }}/{{ characters.length }} 已解决</span>
      <span class="pane-actions" v-if="unresolvedCount > 0">
        <button class="link-btn" :disabled="batchBusy" @click="onBatchFillClick">
          {{ batchBusy ? '生成中…' : 'AI 补全所有缺失角色设定' }}
        </button>
        <button class="link-btn" :disabled="batchBusy" @click="onBatchCreateClick">创建全部角色草稿</button>
      </span>
    </div>
    <div class="char-list">
      <div v-for="r in characters" :key="r.mention" class="rc-card" :class="r.status">
        <div class="rc-head">
          <span class="rc-mark">{{ statusMark(r.status) }}</span>
          <span class="rc-name">{{ r.mention }}</span>
          <span class="rc-status">{{ statusText(r) }}</span>
          <span class="rc-actions" v-if="r.status === 'unresolved'">
            <button class="mini-btn" @click="expand(r.mention, 'match')">匹配已有角色</button>
            <button class="mini-btn" @click="expand(r.mention, 'create')">创建角色草稿</button>
            <button class="mini-btn ghost" @click="ignoreMention(r.mention)">忽略角色资产</button>
          </span>
        </div>

        <!-- 匹配已有角色 -->
        <div class="rc-form" v-if="r.status === 'unresolved' && expanded[r.mention] === 'match'">
          <div class="form-label">可能对应：</div>
          <div class="radio-list">
            <label
              v-for="cn in projectChars" :key="cn"
              class="radio-item" :class="{ on: matchSelected[r.mention] === cn }"
            >
              <input type="radio" :value="cn" v-model="matchSelected[r.mention]" />
              <span>{{ cn }}</span>
            </label>
            <span v-if="!projectChars.length" class="empty-inline">项目无其他角色，请改用「创建角色草稿」</span>
          </div>
          <div class="form-btns">
            <button class="mini-btn ghost" @click="cancelExpand(r.mention)">取消</button>
            <button
              class="mini-btn primary"
              :disabled="!matchSelected[r.mention] || busyMention === r.mention"
              @click="confirmMatch(r.mention)"
            >{{ busyMention === r.mention ? '关联中…' : '关联' }}</button>
          </div>
        </div>

        <!-- 创建角色草稿 -->
        <div class="rc-form" v-if="r.status === 'unresolved' && expanded[r.mention] === 'create'">
          <div class="form-row">
            <label class="form-label">角色名称</label>
            <input class="text-input" v-model="createForm[r.mention].name" placeholder="角色名称" />
          </div>
          <div class="form-row">
            <label class="form-label">角色设定</label>
            <textarea
              class="text-area" rows="4"
              v-model="createForm[r.mention].description"
              placeholder="AI 根据故事生成的角色描述…"
            ></textarea>
          </div>
          <div class="form-btns">
            <button class="mini-btn" :disabled="busyMention === r.mention" @click="genSingleDesc(r.mention)">
              {{ busyMention === r.mention ? '生成中…' : '重新生成设定' }}
            </button>
            <button class="mini-btn ghost" @click="cancelExpand(r.mention)">取消</button>
            <button class="mini-btn primary" :disabled="busyMention === r.mention" @click="confirmCreate(r.mention)">创建到项目</button>
          </div>
        </div>
      </div>
      <div v-if="!characters.length" class="empty">故事段中未识别人物。</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';
import type { CharacterResolutionItem } from '@/api';

const { message } = createDiscreteApi(['message'], { configProviderProps: discreteConfigProviderProps });

interface ResolveParams {
  mention: string;
  action: 'match' | 'create' | 'ignore';
  matched_character_name?: string;
  name?: string;
  description?: string;
}

const props = defineProps<{
  characters: CharacterResolutionItem[];
  projectChars: string[];
  onResolve: (params: ResolveParams) => Promise<void>;
  onGenSingle: (mention: string) => Promise<{ description: string }>;
  onBatchFill: () => Promise<{ characters: Array<{ name: string; description: string }> }>;
  onBatchCreate: (targets: Array<{ mention: string; name: string; description: string }>) => Promise<void>;
}>();

const expanded = ref<Record<string, 'match' | 'create' | null>>({});
const createForm = ref<Record<string, { name: string; description: string }>>({});
const matchSelected = ref<Record<string, string>>({});
const busyMention = ref<string | null>(null);
const batchBusy = ref(false);

const unresolvedCount = computed(() => props.characters.filter(r => r.status === 'unresolved').length);
const resolvedCount = computed(() => props.characters.length - unresolvedCount.value);

function statusMark(s: string) {
  return s === 'matched' || s === 'created' ? '✓' : s === 'ignored' ? '○' : '⚠';
}
function statusText(r: CharacterResolutionItem) {
  if (r.status === 'matched') return `已自动匹配项目角色 ${r.matched_character_name}`;
  if (r.status === 'created') return '已创建到当前项目 · 形象待生成';
  if (r.status === 'ignored') return '已标记：不创建角色资产';
  return '项目中没有对应角色';
}

function expand(mention: string, kind: 'match' | 'create') {
  expanded.value[mention] = kind;
  if (kind === 'create' && !createForm.value[mention]) {
    createForm.value[mention] = { name: mention, description: '' };
  }
  if (kind === 'match' && !(mention in matchSelected.value)) {
    matchSelected.value[mention] = '';
  }
}
function cancelExpand(mention: string) { expanded.value[mention] = null; }

async function genSingleDesc(mention: string) {
  busyMention.value = mention;
  try {
    const r = await props.onGenSingle(mention);
    if (!createForm.value[mention]) createForm.value[mention] = { name: mention, description: '' };
    createForm.value[mention].description = r.description;
  } catch (e: any) {
    message.error(`生成设定失败: ${e.message || e}`);
  } finally {
    busyMention.value = null;
  }
}

async function confirmMatch(mention: string) {
  const sel = matchSelected.value[mention];
  if (!sel) return;
  busyMention.value = mention;
  try {
    await props.onResolve({ mention, action: 'match', matched_character_name: sel });
    cancelExpand(mention);
    message.success(`已关联到项目角色 ${sel}`);
  } catch (e: any) {
    message.error(`关联失败: ${e.message || e}`);
  } finally {
    busyMention.value = null;
  }
}

async function confirmCreate(mention: string) {
  const f = createForm.value[mention];
  if (!f) return;
  busyMention.value = mention;
  try {
    await props.onResolve({ mention, action: 'create', name: f.name, description: f.description });
    cancelExpand(mention);
    message.success('已创建到当前项目');
  } catch (e: any) {
    message.error(`创建失败: ${e.message || e}`);
  } finally {
    busyMention.value = null;
  }
}

async function ignoreMention(mention: string) {
  try {
    await props.onResolve({ mention, action: 'ignore' });
  } catch (e: any) {
    message.error(`操作失败: ${e.message || e}`);
  }
}

async function onBatchFillClick() {
  batchBusy.value = true;
  try {
    const names = props.characters.filter(r => r.status === 'unresolved').map(r => r.mention);
    if (!names.length) { message.info('没有需要补全的缺失角色'); return; }
    const r = await props.onBatchFill();
    for (const c of r.characters) {
      if (!createForm.value[c.name]) createForm.value[c.name] = { name: c.name, description: '' };
      createForm.value[c.name].description = c.description;
      expanded.value[c.name] = 'create';
    }
    message.success(`已补全 ${r.characters.length} 个角色设定`);
  } catch (e: any) {
    message.error(`批量补全失败: ${e.message || e}`);
  } finally {
    batchBusy.value = false;
  }
}

async function onBatchCreateClick() {
  const targets = props.characters
    .filter(r => r.status === 'unresolved')
    .map(r => {
      const f = createForm.value[r.mention] || { name: r.mention, description: '' };
      return { mention: r.mention, name: f.name, description: f.description };
    });
  if (!targets.length) { message.info('没有需要创建的角色'); return; }
  batchBusy.value = true;
  try {
    await props.onBatchCreate(targets);
    message.success(`已创建 ${targets.length} 个角色到项目`);
  } catch (e: any) {
    message.error(`批量创建失败: ${e.message || e}`);
  } finally {
    batchBusy.value = false;
  }
}

defineExpose({ unresolvedCount });
</script>

<style lang="scss" scoped>
.step-pane { }
.pane-head {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 13px; color: var(--text-secondary); margin-bottom: 14px;
}
.pane-actions { display: flex; gap: 12px; }
.link-btn {
  background: none; border: none; color: var(--accent); font-size: 12px; cursor: pointer;
  padding: 2px 4px; border-radius: 4px;
  &:hover { background: var(--bg-hover); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
}
.char-list { display: flex; flex-direction: column; gap: 8px; }
.rc-card {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px;
  &.unresolved { border-left: 3px solid #fbbf24; }
  &.matched, &.created { border-left: 3px solid #4ade80; }
  &.ignored { border-left: 3px solid var(--text-faint); opacity: 0.7; }
}
.rc-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.rc-mark { font-size: 14px; width: 18px; text-align: center; }
.rc-card.matched .rc-mark, .rc-card.created .rc-mark { color: #4ade80; }
.rc-card.unresolved .rc-mark { color: #fbbf24; }
.rc-card.ignored .rc-mark { color: var(--text-faint); }
.rc-name { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.rc-status { font-size: 12px; color: var(--text-muted); }
.rc-actions { margin-left: auto; display: flex; gap: 6px; flex-wrap: wrap; }
.mini-btn {
  padding: 4px 10px; border-radius: 5px; border: 1px solid var(--border); background: var(--bg-root);
  color: var(--text-secondary); font-size: 12px; cursor: pointer;
  &:hover { border-color: var(--accent); color: var(--text-primary); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
  &.ghost { border-color: transparent; color: var(--text-muted); &:hover { color: var(--text-primary); } }
  &.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-text); &:hover { background: var(--accent-hover); } }
}
.rc-form {
  margin-top: 12px; padding: 12px; background: var(--bg-root); border-radius: 6px;
  border: 1px solid var(--border);
}
.form-row { margin-bottom: 10px; }
.form-label { display: block; font-size: 11px; color: var(--text-muted); margin-bottom: 6px; }
.text-input, .text-area {
  width: 100%; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 5px;
  padding: 8px 10px; font-size: 13px; color: var(--text-primary); outline: none; font-family: inherit;
  &:focus { border-color: var(--accent); }
}
.text-area { resize: vertical; line-height: 1.5; }
.radio-list { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.radio-item {
  display: flex; align-items: center; gap: 6px; padding: 5px 12px; border-radius: 16px;
  border: 1px solid var(--border); cursor: pointer; font-size: 12px; color: var(--text-secondary);
  input { accent-color: var(--accent); }
  &.on { border-color: var(--accent); color: var(--text-primary); background: var(--bg-hover); }
}
.empty-inline { font-size: 11px; color: var(--text-faint); }
.form-btns { display: flex; gap: 8px; justify-content: flex-end; }
.empty { text-align: center; color: var(--text-muted); font-size: 13px; padding: 32px 0; }
</style>
