<template>
  <div class="res-dialog-overlay" @click.self="$emit('close')">
    <div class="res-dialog" role="dialog" aria-label="服装详情">
      <header class="rd-head">
        <span class="rd-title">服装详情</span>
        <button class="rd-close" @click="$emit('close')">✕</button>
      </header>
      <div class="rd-body" v-if="detail">
        <div class="rd-row">
          <h3 class="rd-name">{{ detail.name }}</h3>
          <span class="meta">被 {{ detail.used_by_looks?.length || 0 }} 个 Look 引用</span>
          <span class="spacer"></span>
          <button class="btn btn-quiet btn-sm" @click="renameAction">重命名</button>
          <button class="btn btn-quiet btn-sm" :disabled="busy" @click="publishAction">发布到共享库</button>
          <button class="btn btn-quiet btn-sm" :disabled="busy" @click="deleteAction">删除</button>
        </div>
        <p class="rd-desc">{{ detail.description }} {{ detail.design_traits }}</p>

        <section class="rd-sec">
          <div class="rd-sec-head">
            <span class="label">参考图</span>
            <span class="meta">Look 生成时作为服装一致性参考</span>
          </div>
          <div class="ref-grid" v-if="detail.reference_assets?.length">
            <div v-for="a in detail.reference_assets" :key="a.asset_id" class="ref-card">
              <img v-if="assetUrl(a)" :src="assetUrl(a)" @click="lightbox = assetUrl(a)" />
              <div v-else class="ref-ph">无图</div>
              <div class="ref-ops">
                <span v-if="detail.cover_asset_id === a.asset_id" class="star" title="封面">★</span>
                <span class="spacer"></span>
                <button class="mini danger" :disabled="busy" @click="detachRef(a)">解绑</button>
              </div>
            </div>
          </div>
          <p v-else class="rd-empty">还没有参考图。</p>
          <form class="attach-form" @submit.prevent="attachRef">
            <input v-model="newRef.local" placeholder="项目内相对路径（如 o/coat.png）或留空用 URL" />
            <input v-model="newRef.url" placeholder="或公网 URL" />
            <button class="btn btn-sm btn-primary" type="submit" :disabled="busy">注册并绑定</button>
          </form>
        </section>
      </div>
      <div v-else class="rd-body rd-loading">加载中…</div>
    </div>

    <div v-if="lightbox" class="rd-lightbox" @click="lightbox = ''">
      <img :src="lightbox" @click.stop />
      <button class="lb-close" @click="lightbox = ''">×</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue';
import { apiClient } from '@/api';

const props = defineProps<{ projectName: string; clothingId: string; docRev: string }>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'refresh'): void }>();

const detail = ref<any>(null);
const busy = ref(false);
const lightbox = ref('');
const newRef = ref({ local: '', url: '' });

function fail(e: any): string { return e?.detail ? `${e.error || '错误'}: ${e.detail || ''}` : (e?.message || String(e)); }
function assetUrl(a: any): string {
  if (!a || a.missing) return '';
  const local: string = a.local || '';
  if (local && !/^https?:/.test(local)) {
    if (local.startsWith('shared_library/')) return `/${local}`;
    return `/projects/${encodeURIComponent(props.projectName)}/${local.replace(/^\/+/, '')}`;
  }
  return a.url || '';
}
async function load() {
  try { detail.value = await apiClient.readClothing(props.projectName, props.clothingId); }
  catch (e) { alert(fail(e)); emit('close'); }
}
onMounted(load);
watch(() => props.clothingId, load);

async function renameAction() {
  const name = prompt('新名称', detail.value.name);
  if (!name) return;
  busy.value = true;
  try {
    await apiClient.updateClothing(props.projectName, detail.value.clothing_id, { name, expected_revision: detail.value.revision });
    await load(); emit('refresh');
  } catch (e) { alert(fail(e)); } finally { busy.value = false; }
}
async function publishAction() {
  busy.value = true;
  try { await apiClient.publishResource(props.projectName, detail.value.clothing_id); alert('已发布到共享库'); }
  catch (e) { alert(fail(e)); } finally { busy.value = false; }
}
async function deleteAction() {
  if (!confirm('删除该服装？（被 Look 引用时会被拒绝）')) return;
  busy.value = true;
  try {
    await apiClient.deleteClothing(props.projectName, detail.value.clothing_id, detail.value.revision);
    emit('refresh'); emit('close');
  } catch (e) { alert(fail(e)); } finally { busy.value = false; }
}
async function attachRef() {
  busy.value = true;
  try {
    const reg = await apiClient.registerReferenceAsset(props.projectName, {
      local: newRef.value.local || null, url: newRef.value.url || null,
      expected_document_revision: props.docRev,
    });
    await apiClient.attachClothingReference(props.projectName, detail.value.clothing_id, {
      asset_id: reg.target, expected_revision: detail.value.revision,
    });
    newRef.value = { local: '', url: '' };
    await load(); emit('refresh');
  } catch (e) { alert(fail(e)); } finally { busy.value = false; }
}
async function detachRef(a: any) {
  busy.value = true;
  try {
    await apiClient.detachClothingReference(props.projectName, detail.value.clothing_id, a.asset_id, detail.value.revision);
    await load(); emit('refresh');
  } catch (e) { alert(fail(e)); } finally { busy.value = false; }
}
</script>

<style lang="scss" scoped>
.res-dialog-overlay {
  position: fixed; inset: 0; z-index: 1200; background: rgba(0, 0, 0, 0.6);
  display: flex; align-items: center; justify-content: center; backdrop-filter: blur(3px);
}
.res-dialog {
  width: min(720px, 92vw); max-height: 84vh;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 12px;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.5); display: flex; flex-direction: column; overflow: hidden;
}
.rd-head {
  flex-shrink: 0; display: flex; align-items: center; justify-content: space-between;
  padding: 12px 18px; border-bottom: 1px solid var(--border);
}
.rd-title { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.rd-close {
  border: none; background: none; color: var(--text-muted); cursor: pointer; font-size: 14px;
  width: 26px; height: 26px; border-radius: 6px;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
.rd-body { flex: 1; min-height: 0; overflow-y: auto; padding: 16px 18px 20px; display: flex; flex-direction: column; gap: 12px; }
.rd-loading { align-items: center; justify-content: center; color: var(--text-faint); font-size: 13px; }
.rd-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.rd-name { margin: 0; font-size: 16px; color: var(--text-primary); }
.meta { font-size: 11px; color: var(--text-faint); }
.spacer { flex: 1; }
.rd-desc { margin: 0; font-size: 13px; color: var(--text-secondary); line-height: 1.6; }
.rd-sec { border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; background: var(--bg-root); }
.rd-sec-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px;
  .label { font-size: 11px; font-weight: 600; letter-spacing: 0.08em; color: var(--text-muted); } }
.rd-empty { font-size: 12px; color: var(--text-faint); }
.ref-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; margin-bottom: 10px; }
.ref-card {
  border: 1px solid var(--border); border-radius: 8px; overflow: hidden; background: var(--bg-surface);
  img { display: block; width: 100%; aspect-ratio: 4 / 3; object-fit: cover; cursor: zoom-in; }
}
.ref-ph { aspect-ratio: 4 / 3; display: flex; align-items: center; justify-content: center; color: var(--text-faint); font-size: 12px; }
.ref-ops { display: flex; align-items: center; gap: 6px; padding: 6px 8px; }
.star { color: #d4d4d4; font-size: 12px; }
.mini {
  border: 1px solid var(--border); background: none; color: var(--text-secondary);
  font-size: 11px; border-radius: 4px; padding: 2px 8px; cursor: pointer;
  &:hover:not(:disabled) { color: var(--text-primary); border-color: var(--border-hover); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
  &.danger { color: #f87171; }
}
.attach-form { display: flex; gap: 8px; flex-wrap: wrap;
  input { flex: 1; min-width: 160px; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 5px;
    color: var(--text-primary); font-size: 12px; padding: 5px 8px; font-family: inherit;
    &:focus { outline: none; border-color: var(--border-hover); } } }
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 6px;
  background: none; color: var(--text-secondary); font-size: 12px; cursor: pointer;
  &:disabled { opacity: 0.4; cursor: not-allowed; }
  &.btn-primary { border: none; background: var(--accent); color: var(--accent-text, #fff); &:hover:not(:disabled) { background: var(--accent-hover); } }
  &.btn-quiet { border-color: transparent; color: var(--text-muted); &:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); } }
}
.rd-lightbox {
  position: fixed; inset: 0; z-index: 1400; background: rgba(0, 0, 0, 0.85);
  display: flex; align-items: center; justify-content: center; cursor: zoom-out;
  img { max-width: 90vw; max-height: 90vh; object-fit: contain; }
  .lb-close { position: absolute; right: 18px; top: 14px; border: none; background: none; color: #d4d4d4; font-size: 22px; cursor: pointer; }
}
</style>
