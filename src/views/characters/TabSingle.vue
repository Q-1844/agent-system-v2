<template>
  <div class="tab-single">
    <!-- 提示词预览 -->
    <div class="prompt-preview">
      <div class="prompt-label">当前提示词（自动拼接视角关键词）：</div>
      <div class="prompt-text">{{ getPromptPreview() }}</div>
    </div>

    <div class="views-row">
      <div v-for="v in views" :key="v.key" class="view-card">
        <div class="view-title">{{ v.label }}</div>
        <div class="view-img">
          <img v-if="getImage(v.key)" :src="getImage(v.key)" />
          <div v-else-if="isBusy(v.key)" class="generating">
            <div class="spinner"></div>
            <span>生成中...</span>
          </div>
          <div v-else class="placeholder">未生成</div>
        </div>
        <div class="view-steps" v-if="raw[v.key] || seg[v.key]">
          <div class="step-item" v-if="raw[v.key]">
            <span class="step-label">原图</span>
            <img :src="'data:image/png;base64,' + raw[v.key]" class="step-thumb" />
          </div>
          <div class="step-item" v-if="seg[v.key]">
            <span class="step-label">抠图</span>
            <img :src="'data:image/png;base64,' + seg[v.key]" class="step-thumb" />
          </div>
        </div>
        <div class="view-meta" v-if="meta[v.key]">耗时 {{ meta[v.key].elapsed_seconds }}s</div>
        <div class="view-btns">
          <button class="btn btn-primary" :disabled="isBusy(v.key)" @click="generate(v.key)">
            {{ isBusy(v.key) ? '生成中...' : '生成' }}
          </button>
          <button class="btn btn-outline" :disabled="!raw[v.key] || isBusySeg(v.key)" @click="segment(v.key)">
            {{ isBusySeg(v.key) ? '抠图中...' : '抠图' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, computed, onMounted, watch } from 'vue';
import { useProjectStore } from '@/store';

const props = defineProps<{ char: any }>();
const store = useProjectStore();
const views = [{ key: 'front', label: '正面' }, { key: 'side', label: '侧面' }, { key: 'back', label: '背面' }];
const raw = reactive<Record<string, string>>({});
const seg = reactive<Record<string, string>>({});
const meta = reactive<Record<string, any>>({});

// 从 Store 获取生成状态
const genState = computed(() => store.getGeneratingState(props.char.name));

function isBusy(viewKey: string): boolean {
  return genState.value?.busy?.[viewKey] ?? false;
}

function isBusySeg(viewKey: string): boolean {
  return genState.value?.busySeg?.[viewKey] ?? false;
}

// 视角关键词映射
const viewKeywords: Record<string, string> = {
  front: 'facing the camera directly, straight-on front view, symmetrical pose, looking at the viewer',
  side: 'facing left, perfect side profile view, 90 degree angle, looking to the left',
  back: 'back view, from behind, character facing away from camera, showing back to viewer',
};

// 获取提示词预览
function getPromptPreview(): string {
  const base = props.char.description || '';
  return `${base}, [视角关键词], anime illustration, single character, pure white background, full body...`;
}

function getImage(key: string): string {
  if (seg[key]) return `data:image/png;base64,${seg[key]}`;
  if (raw[key]) return `data:image/png;base64,${raw[key]}`;
  return '';
}

// 从缓存恢复状态
function restoreFromCache() {
  const cache = store.getCharacterCache(props.char.name);
  if (cache) {
    if (cache.views.front?.raw) raw.front = cache.views.front.raw;
    if (cache.views.front?.seg) seg.front = cache.views.front.seg;
    if (cache.views.side?.raw) raw.side = cache.views.side.raw;
    if (cache.views.side?.seg) seg.side = cache.views.side.seg;
    if (cache.views.back?.raw) raw.back = cache.views.back.raw;
    if (cache.views.back?.seg) seg.back = cache.views.back.seg;
  }
}

async function generate(viewKey: string) {
  try {
    const r = await store.generateView(props.char.name, props.char.description, viewKey);
    if (r.success && r.raw_base64) {
      raw[viewKey] = r.raw_base64;
      if (r.seg_base64) {
        seg[viewKey] = r.seg_base64;
        meta[viewKey] = r.metadata;
      }
    }
  } catch (e: any) {
    store.showNotification(`生成失败: ${e.message}`, 'error');
  }
}

async function segment(viewKey: string) {
  if (!raw[viewKey]) return;
  try {
    const r = await store.segmentView(props.char.name, raw[viewKey], viewKey);
    if (r.success && r.seg_base64) {
      seg[viewKey] = r.seg_base64;
      meta[viewKey] = r.metadata;
    }
  } catch (e: any) {
    store.showNotification(`抠图失败: ${e.message}`, 'error');
  }
}

onMounted(() => {
  restoreFromCache();
});

watch(() => props.char.name, () => {
  // 重置当前状态
  Object.keys(raw).forEach(k => { delete raw[k]; });
  Object.keys(seg).forEach(k => { delete seg[k]; });
  // 恢复新角色的状态
  restoreFromCache();
});
</script>

<style lang="scss" scoped>
.prompt-preview {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 16px;
}

.prompt-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-muted);
  margin-bottom: 6px;
}

.prompt-text {
  font-size: 12px;
  color: var(--text-primary);
  line-height: 1.5;
  word-break: break-all;
}

.views-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}

.view-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}

.view-title {
  padding: 10px 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  border-bottom: 1px solid var(--border);
}

.view-img {
  aspect-ratio: 1;
  background: var(--bg-root);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;

  img {
    width: 100%;
    height: 100%;
    object-fit: contain;
  }
}

.placeholder {
  font-size: 12px;
  color: var(--text-faint);
}

.generating {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: var(--text-muted);
  font-size: 12px;
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.view-steps {
  display: flex;
  gap: 8px;
  padding: 8px 12px;
  border-top: 1px solid var(--border);
}

.step-item {
  flex: 1;
  text-align: center;
}

.step-label {
  font-size: 10px;
  color: var(--text-faint);
  display: block;
  margin-bottom: 4px;
}

.step-thumb {
  width: 100%;
  aspect-ratio: 1;
  object-fit: contain;
  border-radius: 4px;
  background: var(--bg-root);
}

.view-meta {
  padding: 4px 12px;
  font-size: 11px;
  color: var(--text-faint);
}

.view-btns {
  padding: 10px 12px;
  display: flex;
  gap: 8px;
  border-top: 1px solid var(--border);
}

.btn {
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  border: none;
  flex: 1;
  transition: all 0.2s;

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}

.btn-primary {
  background: var(--accent);
  color: #fff;

  &:hover:not(:disabled) {
    filter: brightness(1.1);
  }
}

.btn-outline {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-muted);

  &:hover:not(:disabled) {
    border-color: var(--accent);
    color: var(--accent);
  }
}
</style>
