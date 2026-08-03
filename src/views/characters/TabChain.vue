<template>
  <div class="tab-chain">
    <div class="action-bar">
      <button class="btn btn-primary" :disabled="loading" @click="run">
        {{ loading ? '生成中...' : '一键链式生成三视图' }}
      </button>
    </div>

    <div class="steps" v-if="steps.length">
      <span v-for="(s, i) in steps" :key="i" class="step" :class="s.status">
        {{ s.action }} {{ s.status === 'done' ? '✓' : s.status === 'failed' ? '✗' : '' }}
      </span>
    </div>

    <div class="result-grid" v-if="hasResult">
      <div class="col">
        <h4>原始生成（白底）</h4>
        <div class="img-row">
          <div class="img-box" v-for="v in viewKeys" :key="v">
            <div class="img-label">
              {{ v }}
              <span v-if="refCount[v]" class="ref-badge">参考{{ refCount[v] }}张</span>
            </div>
            <img v-if="raw[v]" :src="'data:image/png;base64,' + raw[v]" />
            <div v-else-if="isRegenBusy(v)" class="generating">
              <div class="spinner"></div>
            </div>
            <div v-else class="ph">-</div>
            <div class="img-actions">
              <button class="btn btn-sm" :disabled="isRegenBusy(v)" @click="regenerate(v)">
                {{ isRegenBusy(v) ? '重生成中...' : '重新生成' }}
              </button>
            </div>
          </div>
        </div>
      </div>
      <div class="col">
        <h4>抠图结果（透明）</h4>
        <div class="img-row">
          <div class="img-box" v-for="v in viewKeys" :key="v">
            <div class="img-label">{{ v }}</div>
            <div class="checker">
              <img v-if="seg[v]" :src="'data:image/png;base64,' + seg[v]" />
              <div v-else class="ph">-</div>
            </div>
            <div class="img-actions">
              <button class="btn btn-sm btn-outline" :disabled="!raw[v] || isSegBusy(v)" @click="doSegment(v)">
                {{ isSegBusy(v) ? '抠图中...' : '单独抠图' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue';
import { apiClient } from '@/api';
import { useProjectStore } from '@/store';

const props = defineProps<{ char: any }>();
const store = useProjectStore();
const viewKeys = ['front', 'side', 'back'];
const loading = ref(false);
const steps = ref<any[]>([]);
const raw = reactive<Record<string, string>>({});
const seg = reactive<Record<string, string>>({});
const refCount = reactive<Record<string, number>>({});
const hasResult = computed(() => Object.keys(raw).length > 0);

// 从 Store 获取生成状态
const genState = computed(() => store.getGeneratingState(props.char.name));

function isRegenBusy(viewKey: string): boolean {
  return genState.value?.busy?.[viewKey] ?? false;
}

function isSegBusy(viewKey: string): boolean {
  return genState.value?.busySeg?.[viewKey] ?? false;
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

  // 恢复步骤状态
  const gen = genState.value;
  if (gen?.steps?.length) {
    steps.value = gen.steps;
  }
}

async function run() {
  loading.value = true;
  steps.value = [];
  const proj = store.projectName;   // 捕获：生成开始时的项目（切项目后结果仍写回它）
  store.updateGeneratingState(props.char.name, { steps: [] });

  try {
    const r = await apiClient.generateCharacterChain({ description: props.char.description, style: 'anime' });
    const views: any = {};
    for (const k of ['front', 'side', 'back']) {
      views[k] = { raw: r.raw_views?.[k], seg: r.seg_views?.[k] || undefined };
    }
    // auto-bind 到捕获项目（始终写回原项目，GPT §6/§7）
    if (r.success) {
      try { await apiClient.bindCharacterToProject(proj, { name: props.char.name, description: props.char.description, views }); }
      catch (e: any) { console.error('auto-bind 失败', e); }
    }
    // 仅当仍在原项目时更新当前项目 UI/缓存（GPT §7/§8，切项目后不污染新项目）
    if (proj !== store.projectName) return;
    steps.value = r.steps || [];
    store.updateGeneratingState(props.char.name, { steps: r.steps || [] });

    if (r.raw_views) Object.entries(r.raw_views).forEach(([k, v]) => { raw[k] = v as string; });
    if (r.seg_views) Object.entries(r.seg_views).forEach(([k, v]) => { if (v) seg[k] = v as string; });

    if (r.success) {
      store.showNotification('链式生成完成', 'success');
      store.cacheCharacterState(props.char.name, props.char.description, views);
    } else {
      store.showNotification(`部分失败: ${r.error}`, 'error');
    }
  } catch (e: any) {
    if (proj === store.projectName) store.showNotification(`失败: ${e.message}`, 'error');
  } finally {
    if (proj === store.projectName) loading.value = false;
  }
}

function getRefImages(viewKey: string): string[] {
  return viewKeys.filter(v => v !== viewKey && raw[v]).map(v => raw[v]);
}

async function regenerate(viewKey: string) {
  const refs = getRefImages(viewKey);
  if (refs.length === 0) {
    store.showNotification('没有可用的参考图，请先生成其他视角', 'error');
    return;
  }

  const proj = store.projectName;   // 捕获：生成开始时的项目
  store.updateGeneratingState(props.char.name, {
    busy: { [viewKey]: true },
  });

  try {
    const r = await apiClient.regenerateView({
      description: props.char.description,
      view: viewKey,
      style: 'anime',
      ref_images: refs,
    });
    if (r.success) {
      // auto-bind 该视角到捕获项目（合并，其余视角保留，GPT §6/§7）
      try {
        await apiClient.bindCharacterToProject(proj, {
          name: props.char.name, description: props.char.description,
          views: { [viewKey]: { raw: r.raw_base64, seg: r.seg_base64 || undefined } },
        });
      } catch (e: any) { console.error('auto-bind 失败', e); }
      // 仅当仍在原项目时更新当前项目 UI/缓存
      if (proj === store.projectName) {
        raw[viewKey] = r.raw_base64;
        if (r.seg_base64) seg[viewKey] = r.seg_base64;
        refCount[viewKey] = r.ref_count;
        store.showNotification(`${viewKey} 重新生成完成`, 'success');
        store.cacheCharacterState(props.char.name, props.char.description, {
          front: { raw: raw.front, seg: seg.front },
          side: { raw: raw.side, seg: seg.side },
          back: { raw: raw.back, seg: seg.back },
        });
      }
    }
  } catch (e: any) {
    if (proj === store.projectName) store.showNotification(`重生成失败: ${e.message}`, 'error');
  } finally {
    if (proj === store.projectName) store.updateGeneratingState(props.char.name, {
      busy: { [viewKey]: false },
    });
  }
}

async function doSegment(viewKey: string) {
  if (!raw[viewKey]) return;

  const proj = store.projectName;   // 捕获：抠图开始时的项目
  store.updateGeneratingState(props.char.name, {
    busySeg: { [viewKey]: true },
  });

  try {
    const r = await apiClient.segmentImage(raw[viewKey]);
    if (r.success && r.seg_base64) {
      // auto-bind 该视角原图+新抠图到捕获项目（合并，不覆盖其他视角，补缺口）
      const ok = await store.persistCharacterViews(proj, props.char.name, props.char.description, {
        [viewKey]: { raw: raw[viewKey], seg: r.seg_base64 },
      });
      if (!ok) store.showNotification('抠图已完成，但保存到项目失败', 'error');
      // 仅当仍在原项目时更新当前项目缓存
      if (proj === store.projectName) {
        seg[viewKey] = r.seg_base64;
        store.cacheCharacterState(props.char.name, props.char.description, {
          front: { raw: raw.front, seg: seg.front },
          side: { raw: raw.side, seg: seg.side },
          back: { raw: raw.back, seg: seg.back },
        });
      }
    }
  } catch (e: any) {
    if (proj === store.projectName) store.showNotification(`抠图失败: ${e.message}`, 'error');
  } finally {
    if (proj === store.projectName) store.updateGeneratingState(props.char.name, {
      busySeg: { [viewKey]: false },
    });
  }
}

onMounted(() => {
  restoreFromCache();
});

watch(() => props.char.name, () => {
  Object.keys(raw).forEach(k => { delete raw[k]; });
  Object.keys(seg).forEach(k => { delete seg[k]; });
  steps.value = [];
  restoreFromCache();
});
</script>

<style lang="scss" scoped>
.action-bar {
  margin-bottom: 16px;
}

.btn {
  padding: 8px 20px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: none;
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

.steps {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.step {
  padding: 5px 10px;
  border-radius: 6px;
  font-size: 11px;
  background: var(--bg-hover);
  color: var(--text-muted);

  &.done {
    background: #14532d;
    color: #4ade80;
  }

  &.failed {
    background: #7f1d1d;
    color: #f87171;
  }
}

.result-grid {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.col h4 {
  font-size: 14px;
  color: var(--text-primary);
  margin-bottom: 10px;
}

.img-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.img-box {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}

.img-label {
  padding: 8px 10px;
  font-size: 11px;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  text-transform: capitalize;
}

.img-box img {
  width: 100%;
  display: block;
}

.checker {
  background: repeating-conic-gradient(#222 0% 25%, #2a2a2a 0% 50%) 50% / 16px 16px;

  img {
    width: 100%;
    display: block;
  }
}

.ph {
  padding: 40px;
  text-align: center;
  color: var(--text-faint);
  font-size: 12px;
}

.generating {
  padding: 40px;
  display: flex;
  justify-content: center;
}

.spinner {
  width: 24px;
  height: 24px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.img-actions {
  padding: 8px;
  border-top: 1px solid var(--border);
}

.btn-sm {
  padding: 6px 12px;
  font-size: 11px;
  width: 100%;
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

.ref-badge {
  font-size: 10px;
  background: var(--accent);
  color: #fff;
  padding: 2px 6px;
  border-radius: 10px;
  margin-left: 4px;
}
</style>
