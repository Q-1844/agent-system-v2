<!--
  AI-7D-R · Director 最小测试弹窗（隔离）。
  工具：add_scenes_from_script（剧本→新增故事段）、add_shots_to_scene（段→追加镜头，可对已有镜头的段追加）、
        delete_scene/delete_shot（依赖感知级联删除，confirmed 两阶段：先说明依赖→用户确认→删）。
  不走 ChangeSession/提案/审查/执行流。默认直接改、不审核（删除经用户在对话中确认）。
-->
<template>
  <Teleport to="body">
    <div v-if="open" class="dt-overlay" @click.self="close">
      <div class="dt-modal">
        <header class="dt-head">
          <div>
            <span class="dt-title">Director · 分镜测试</span>
            <span class="dt-sub">增/删故事段与镜头 · 删除会先说明依赖并向你确认</span>
          </div>
          <button class="dt-x" @click="close">✕</button>
        </header>

        <div class="dt-project">当前项目：{{ projectName || '未选择' }}</div>

        <div class="dt-chat" ref="chatEl">
          <div v-if="!messages.length" class="dt-empty">
            直接对 Director 说，例如：<br/>
            「把剧本拆分成几个分镜故事段」/「把 s01 拆成镜头」<br/>
            「在 s02 后面插一段：…」/「在 s01_c01 后面加两个镜头…」<br/>
            「把 s03 的概要改成…」/「把 s03_c02 时长改成 8 秒」<br/>
            也可以说「删除故事段 s01」——它会先说明会连带删除什么，问你「确定删除吗？」。
          </div>
          <div v-for="(m, i) in messages" :key="i" class="dt-msg" :class="'r-' + m.role">
            <span class="dt-role">{{ m.role === 'user' ? '我' : 'Director' }}</span>
            <span class="dt-content">{{ m.content }}</span>
            <span v-if="m.edited" class="dt-tag">已落盘</span>
          </div>
          <div v-if="sending" class="dt-msg r-assistant dt-thinking">Director 思考中…</div>
        </div>

        <div v-if="lastAddedScenes?.length" class="dt-result">
          <div class="dt-result-head">新增的故事段（已写入项目）</div>
          <div v-for="s in lastAddedScenes" :key="s.scene_id" class="dt-scene">
            <span class="dt-sid">{{ s.scene_id }}</span>
            <span class="dt-summary">{{ s.summary }}</span>
            <span class="dt-chars">{{ (s.characters || []).join('、') || '无角色' }}</span>
          </div>
        </div>

        <div v-if="lastAddedShots?.length" class="dt-result">
          <div class="dt-result-head">新增的镜头（已写入项目）</div>
          <div v-for="s in lastAddedShots" :key="s.shot_id" class="dt-scene">
            <span class="dt-sid">{{ s.shot_id }}</span>
            <span class="dt-summary">{{ s.description }}（{{ s.target_duration_s }}s）</span>
            <span class="dt-chars">{{ (s.characters || []).join('、') || '无角色' }}</span>
          </div>
        </div>

        <div v-if="lastUpdated.length" class="dt-result">
          <div class="dt-result-head">已修改（已写入项目）</div>
          <div v-for="(u, i) in lastUpdated" :key="i" class="dt-scene">
            <span class="dt-sid">{{ u.kind }}</span>
            <span class="dt-summary">{{ u.id }} 的 {{ u.changed.join('、') }} 已更新</span>
          </div>
        </div>

        <div v-if="lastDeletedPreview" class="dt-result dt-result-warn">
          <div class="dt-result-head">⚠️ 待删除（请在对话框确认）</div>
          <div v-if="lastDeletedPreview.will_delete?.scenes?.length" class="dt-scene">
            <span class="dt-sid">故事段</span>
            <span class="dt-summary">{{ lastDeletedPreview.will_delete.scenes.join('、') }}</span>
          </div>
          <div v-if="lastDeletedPreview.will_delete?.shots?.length" class="dt-scene">
            <span class="dt-sid">镜头</span>
            <span class="dt-summary">
              {{ lastDeletedPreview.will_delete.shots.join('、') }}
              <span class="dt-files">（{{ lastDeletedPreview.will_delete.asset_files }} 个生成资产文件）</span>
            </span>
          </div>
          <div v-if="lastDeletedPreview.protected_characters?.length" class="dt-note">
            出场角色（{{ lastDeletedPreview.protected_characters.join('、') }}）不会被删除；其他故事段不受影响。
          </div>
          <div v-if="lastDeletedPreview.active_production" class="dt-note dt-note-err">
            ⚠️ {{ lastDeletedPreview.active_production }}：请等生成结束后再确认删除。
          </div>
        </div>

        <div v-if="(lastDeletedScenes?.length || lastDeletedShots?.length)" class="dt-result dt-result-del">
          <div class="dt-result-head">🗑 已删除</div>
          <div v-if="lastDeletedScenes?.length" class="dt-scene">
            <span class="dt-sid">故事段</span>
            <span class="dt-summary">{{ lastDeletedScenes.join('、') }}</span>
          </div>
          <div v-if="lastDeletedShots?.length" class="dt-scene">
            <span class="dt-sid">镜头</span>
            <span class="dt-summary">{{ lastDeletedShots.join('、') }}</span>
          </div>
          <div class="dt-note">
            清理 {{ removedFiles }} 个生成资产文件<span v-if="planRemoved">，该段制作计划已删除</span>。
          </div>
        </div>

        <div class="dt-input-row">
          <textarea v-model="draft" class="dt-input" rows="2"
            placeholder="对 Director 说…" :disabled="sending || !projectName"
            @keydown.enter.exact.prevent="send"></textarea>
          <button class="dt-send" :disabled="sending || !draft.trim() || !projectName" @click="send">
            {{ sending ? '…' : '发送' }}
          </button>
        </div>
        <div v-if="error" class="dt-err">{{ error }}</div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch } from 'vue';
import { apiClient } from '@/api';
import type { AgentMessage } from '@/api';
import { useProjectStore } from '@/store';

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ (e: 'close'): void }>();

const projectStore = useProjectStore();
const projectName = computed(() => projectStore.projectName);

const messages = ref<Array<AgentMessage & { edited?: boolean }>>([]);
const draft = ref('');
const sending = ref(false);
const error = ref('');
const lastAddedScenes = ref<any[] | null>(null);
const lastAddedShots = ref<any[] | null>(null);
const lastUpdated = ref<Array<{ kind: string; id: string; changed: string[] }>>([]);
const lastDeletedScenes = ref<string[] | null>(null);
const lastDeletedShots = ref<string[] | null>(null);
const lastDeletedPreview = ref<any | null>(null);
const removedFiles = ref(0);
const planRemoved = ref(false);
const chatEl = ref<HTMLElement | null>(null);

async function loadSession() {
  if (!projectName.value) return;
  try {
    const s = await apiClient.directorTestSession(projectName.value);
    messages.value = (s.messages || []).map((m: any) => ({ ...m }));
    await scroll();
  } catch (e: any) { error.value = '加载会话失败: ' + (e.message || e); }
}

function close() { emit('close'); }

async function send() {
  const text = draft.value.trim();
  if (!text || sending.value || !projectName.value) return;
  draft.value = '';
  messages.value.push({ role: 'user', content: text, status: null, created_at: Date.now() / 1000 });
  sending.value = true; error.value = '';
  await scroll();
  try {
    const res = await apiClient.directorTestTurn(projectName.value, text);
    messages.value.push({ ...res.message, edited: res.edited });
    // 每轮按结果重置面板（新增/修改/删除确认/已删除互斥展示）
    lastAddedScenes.value = null;
    lastAddedShots.value = null;
    lastUpdated.value = [];
    lastDeletedPreview.value = null;
    lastDeletedScenes.value = null;
    lastDeletedShots.value = null;
    removedFiles.value = 0;
    planRemoved.value = false;
    if (res.edited) {
      lastAddedScenes.value = res.added_scenes || null;
      lastAddedShots.value = res.added_shots || null;
      for (const s of (res.updated_scenes || [])) {
        lastUpdated.value.push({ kind: '故事段', id: s.scene_id, changed: s.changed || [] });
      }
      for (const s of (res.updated_shots || [])) {
        lastUpdated.value.push({ kind: '镜头', id: s.shot_id, changed: s.changed || [] });
      }
      lastDeletedScenes.value = res.deleted_scenes || null;
      lastDeletedShots.value = res.deleted_shots || null;
      removedFiles.value = res.removed_files || 0;
      planRemoved.value = !!res.plan_removed;
    } else if (res.deleted_preview) {
      lastDeletedPreview.value = res.deleted_preview;
    }
  } catch (e: any) {
    error.value = '发送失败: ' + (e.message || e);
  } finally {
    sending.value = false;
    await scroll();
  }
}

async function scroll() {
  await nextTick();
  if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight;
}

watch(() => props.open, (o) => { if (o) loadSession(); }, { immediate: true });
</script>

<style lang="scss" scoped>
.dt-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.5); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.dt-modal { width: min(680px, 94vw); max-height: 88vh; display: flex; flex-direction: column; background: var(--bg, #1e1e2e); color: var(--text, #cdd6f4); border-radius: 12px; box-shadow: 0 12px 40px rgba(0,0,0,.4); overflow: hidden; }
.dt-head { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,.08); }
.dt-title { font-weight: 600; font-size: 14px; }
.dt-sub { display: block; font-size: 11px; color: var(--text-secondary, #9399b2); margin-top: 2px; }
.dt-x { background: none; border: none; color: inherit; font-size: 18px; cursor: pointer; }
.dt-project { padding: 6px 16px; font-size: 12px; color: var(--text-secondary, #9399b2); border-bottom: 1px solid rgba(255,255,255,.05); }
.dt-chat { flex: 1; overflow-y: auto; padding: 12px 16px; min-height: 200px; max-height: 42vh; display: flex; flex-direction: column; gap: 8px; }
.dt-empty { color: var(--text-secondary, #9399b2); font-size: 13px; line-height: 1.6; }
.dt-msg { display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; border-radius: 8px; max-width: 92%; font-size: 13px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
.r-user { align-self: flex-end; background: rgba(96,165,250,.15); }
.r-assistant { align-self: flex-start; background: rgba(255,255,255,.05); }
.dt-role { font-size: 11px; color: var(--text-secondary, #9399b2); }
.dt-tag { display: inline-block; font-size: 10px; color: #4ade80; border: 1px solid rgba(74,222,128,.4); border-radius: 6px; padding: 0 4px; margin-top: 2px; align-self: flex-start; }
.dt-thinking { color: var(--text-secondary, #9399b2); font-style: italic; }
.dt-result { border-top: 1px solid rgba(255,255,255,.08); padding: 10px 16px; max-height: 32vh; overflow-y: auto; }
.dt-result-head { font-size: 11px; color: #4ade80; margin-bottom: 6px; }
.dt-result-warn .dt-result-head { color: #fbbf24; }
.dt-result-warn { border-top-color: rgba(251,191,36,.25); }
.dt-result-del .dt-result-head { color: #f87171; }
.dt-result-del { border-top-color: rgba(248,113,113,.25); }
.dt-files { color: var(--text-secondary, #9399b2); font-size: 12px; }
.dt-note { font-size: 12px; color: var(--text-secondary, #9399b2); margin-top: 6px; line-height: 1.5; }
.dt-note-err { color: #f87171; }
.dt-scene { display: flex; gap: 8px; align-items: flex-start; padding: 6px 8px; background: rgba(255,255,255,.03); border-radius: 6px; margin-bottom: 4px; font-size: 13px; }
.dt-sid { color: #60a5fa; font-weight: 600; min-width: 36px; }
.dt-summary { flex: 1; line-height: 1.5; }
.dt-chars { color: var(--text-secondary, #9399b2); font-size: 12px; }
.dt-input-row { display: flex; gap: 8px; padding: 10px 16px; border-top: 1px solid rgba(255,255,255,.08); }
.dt-input { flex: 1; resize: none; background: rgba(255,255,255,.05); border: 1px solid rgba(255,255,255,.1); border-radius: 8px; color: inherit; padding: 8px; font-size: 13px; font-family: inherit; }
.dt-send { background: rgba(96,165,250,.9); color: #fff; border: none; border-radius: 8px; padding: 0 18px; font-size: 13px; cursor: pointer; }
.dt-send:disabled { opacity: .5; cursor: not-allowed; }
.dt-err { padding: 0 16px 10px; font-size: 12px; color: #f87171; }
</style>
