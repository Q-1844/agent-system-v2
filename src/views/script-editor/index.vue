<template>
  <div class="scene-editor">
    <!-- 无当前项目引导（不自动选第一项，回 Dashboard 选） -->
    <div v-if="!projectName" class="no-project">
      <p>请先选择项目</p>
      <button class="btn-primary" @click="goDashboard">前往项目页</button>
    </div>

    <template v-else>
      <!-- 顶栏：项目名(只读) + 计数 + 保存状态（不提供项目切换，统一去 Dashboard） -->
      <div class="topbar">
        <span class="proj-name">{{ projectName }}</span>
        <span class="count">{{ scenes.length }}分镜 · {{ totalShots }}镜</span>
        <span class="save-status" :class="status">
          <span v-if="status === 'saving'">保存中…</span>
          <span v-else-if="status === 'saved'">已保存</span>
          <span v-else-if="status === 'failed'">保存失败·<a @click="doSave">重试</a></span>
        </span>
      </div>

      <!-- 完整故事摘要卡（始终保留，编写分镜时回看） -->
      <div class="story-card" @click="openStoryDialog">
        <div class="story-card-head">
          <span class="story-label">完整故事</span>
          <span class="story-word" v-if="storyWordCount">{{ storyWordCount }}字</span>
          <span class="story-edit">{{ storyText.trim() ? '编辑' : '开始编写' }}</span>
        </div>
        <div v-if="storyText.trim()" class="story-preview">{{ storyPreview }}</div>
        <div v-else class="story-empty">还没有完整故事</div>
      </div>

      <!-- 分镜故事标题区 + 清空 -->
      <div class="section-head">
        <span>分镜故事</span>
        <button class="link-btn danger" :disabled="!scenes.length" @click="showClearConfirm = true">清空</button>
      </div>

      <!-- 场景列表（可拖拽排序） -->
      <div class="scene-list">
        <div
          v-for="(sc, i) in scenes"
          :key="sc.uid"
          class="scene-card"
          :class="{ dragging: dragIndex === i, 'drag-over': dragOverIndex === i && dragIndex !== i }"
          draggable="true"
          @dragstart="onDragStart(i)"
          @dragover.prevent="onDragOver(i)"
          @dragleave="onDragLeave(i)"
          @drop="onDrop(i)"
          @dragend="onDragEnd"
        >
          <div class="card-head" @click="toggleCollapse(i)">
            <span class="seq-badge">{{ i + 1 }}</span>
            <span class="card-title">{{ sc.summary.trim() ? sc.summary.trim().slice(0, 18) : '未填写故事' }}</span>
            <span class="card-chars" v-if="sc.characters.length">{{ sc.characters.join('、') }}</span>
            <span class="card-chevron">{{ sc.collapsed ? '▸' : '▾' }}</span>
            <button class="del-btn" title="删除该分镜" @click.stop="removeScene(i)">×</button>
          </div>

          <div class="card-body" v-if="!sc.collapsed">
            <textarea
              :value="sc.summary"
              @input="onSummaryInput(sc, ($event.target as HTMLTextAreaElement).value)"
              placeholder="写这条分镜发生了什么…"
              rows="3"
            ></textarea>
            <div class="char-select">
              <span class="char-label">出场角色</span>
              <div class="char-chips">
                <button
                  v-for="name in projectChars"
                  :key="name"
                  class="chip"
                  :class="{ on: sc.characters.includes(name) }"
                  @click="toggleChar(sc, name)"
                >{{ name }}</button>
                <span v-if="!projectChars.length" class="no-chars">当前项目无角色，请先在角色页创建</span>
              </div>
            </div>
            <div class="card-video" v-if="sc.has_video">
              <img v-if="sc.video_thumb" :src="sc.video_thumb" class="thumb" alt="缩略图" />
              <span class="video-time">{{ sc.video_time }} 生成</span>
            </div>
          </div>
        </div>
        <div v-if="!scenes.length" class="empty">暂无分镜故事，点下方「+ 添加分镜」</div>
      </div>

      <button class="add-btn" @click="addScene">+ 添加分镜</button>

      <!-- 完整故事编辑弹窗（大尺寸，非窄确认框） -->
      <div v-if="showStoryDialog" class="dialog-overlay" @click.self="cancelStory">
        <div class="story-dialog">
          <div class="dialog-head">
            <span>完整故事</span>
            <span class="word-count">{{ storyDraft.trim().length }}字</span>
          </div>
          <textarea
            v-model="storyDraft"
            class="story-textarea"
            placeholder="写下整个故事，作为编写分镜时的参考（不会被解析或拆分）"
            rows="14"
          ></textarea>
          <div class="dialog-btns">
            <button class="btn btn-cancel" @click="cancelStory">取消</button>
            <button class="btn btn-confirm" @click="doneStory">完成</button>
          </div>
        </div>
      </div>

      <!-- 清空确认 -->
      <div v-if="showClearConfirm" class="dialog-overlay" @click.self="showClearConfirm = false">
        <div class="confirm-dialog">
          <h3>清空分镜故事</h3>
          <p>即将删除 <b>{{ clearSceneCount }}</b> 条分镜故事。</p>
          <p class="warn" v-if="clearShotCount > 0">其中含 <b>{{ clearShotCount }}</b> 个镜头，已生成的画面/视频将一并删除。</p>
          <div class="dialog-btns">
            <button class="btn btn-cancel" @click="showClearConfirm = false">取消</button>
            <button class="btn btn-danger" @click="doClear">确认清空</button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useProjectStore } from '@/store';
import { apiClient } from '@/api';
import { getImageUrl } from '@/utils/media';
import dayjs from 'dayjs';

const store = useProjectStore();
const router = useRouter();
const projectName = computed(() => store.projectName);

interface LocalScene {
  uid: number;
  scene_id: string | null;   // null = 新增，保存时后端分配
  summary: string;            // 分镜故事文本
  characters: string[];       // 出场角色名
  shot_count: number;         // 该场景已有镜头数（清空提示用）
  has_video: boolean;
  video_thumb: string;
  video_time: string;
  collapsed: boolean;
}

const scenes = ref<LocalScene[]>([]);
const storyText = ref('');
const projectChars = ref<string[]>([]);   // 当前项目角色名（多选选项，仅来自本项目）
const status = ref<'idle' | 'saving' | 'saved' | 'failed'>('idle');
const showStoryDialog = ref(false);
const storyDraft = ref('');
const showClearConfirm = ref(false);

let uidCounter = 1;
let saveTimer: number | null = null;
let loadSeq = 0;   // 加载竞态 token

const totalShots = computed(() => scenes.value.reduce((n, s) => n + s.shot_count, 0));
const storyPreview = computed(() => {
  const t = storyText.value.trim();
  return t ? t.split('\n')[0].slice(0, 40) : '';
});
const storyWordCount = computed(() => storyText.value.trim().length);
const clearSceneCount = computed(() => scenes.value.length);
const clearShotCount = computed(() => totalShots.value);

const dragIndex = ref<number | null>(null);
const dragOverIndex = ref<number | null>(null);

function goDashboard() { router.push('/dashboard'); }

async function load() {
  if (!projectName.value) { scenes.value = []; storyText.value = ''; return; }
  const mySeq = ++loadSeq;
  try {
    const data = await apiClient.getProjectShots(projectName.value);
    if (mySeq !== loadSeq) return;   // 旧响应丢弃（切项目后不覆盖新）
    storyText.value = data.story_text || '';
    projectChars.value = (data.characters || []).map(c => c.name);
    scenes.value = (data.scenes || []).map((s: any) => {
      const shots = s.shots || [];
      const shot = shots[0];
      const video = shot?.video;
      const has_video = !!(video && video.local);
      const ffCands = shot?.first_frame?.candidates || [];
      const ffSel = ffCands[shot?.first_frame?.selected_index ?? 0] || ffCands[0];
      const video_time = video?.updated_at ? dayjs(video.updated_at * 1000).format('HH:mm') : '';
      return {
        uid: uidCounter++,
        scene_id: s.scene_id,
        summary: s.summary || '',
        characters: s.characters || [],
        shot_count: shots.length,
        has_video,
        video_thumb: ffSel?.local ? getImageUrl(ffSel.local) : '',
        video_time,
        collapsed: true,
      } as LocalScene;
    });
    status.value = 'idle';
  } catch (e) {
    if (mySeq === loadSeq) { scenes.value = []; storyText.value = ''; }
  }
}

// ---- 自动保存（debounce）----
function scheduleSave() {
  status.value = 'saving';
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => { saveTimer = null; doSave(); }, 800);
}

async function doSave() {
  if (!projectName.value) return;
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
  status.value = 'saving';
  try {
    const r = await apiClient.saveScenes(projectName.value, {
      story_text: storyText.value,
      scenes: scenes.value.map(s => ({ scene_id: s.scene_id, summary: s.summary, characters: s.characters })),
    });
    // 更新新分镜的 scene_id（按顺序匹配，不触碰 summary/characters 以免覆盖正在编辑的内容）
    if (r.scenes) {
      for (let i = 0; i < scenes.value.length && i < r.scenes.length; i++) {
        if (!scenes.value[i].scene_id) scenes.value[i].scene_id = r.scenes[i].scene_id;
      }
    }
    status.value = 'saved';
  } catch (e) {
    status.value = 'failed';
  }
}

// ---- 场景操作 ----
function addScene() {
  scenes.value.push({
    uid: uidCounter++, scene_id: null, summary: '', characters: [],
    shot_count: 0, has_video: false, video_thumb: '', video_time: '', collapsed: false,
  });
  scheduleSave();
}
function removeScene(i: number) { scenes.value.splice(i, 1); scheduleSave(); }
function toggleCollapse(i: number) { scenes.value[i].collapsed = !scenes.value[i].collapsed; }
function onSummaryInput(sc: LocalScene, val: string) { sc.summary = val; scheduleSave(); }
function toggleChar(sc: LocalScene, name: string) {
  const idx = sc.characters.indexOf(name);
  if (idx > -1) sc.characters.splice(idx, 1);
  else sc.characters.push(name);
  scheduleSave();
}

// ---- 拖拽排序 ----
function onDragStart(i: number) { dragIndex.value = i; }
function onDragOver(i: number) { if (dragIndex.value !== null && dragIndex.value !== i) dragOverIndex.value = i; }
function onDragLeave(i: number) { if (dragOverIndex.value === i) dragOverIndex.value = null; }
function onDrop(i: number) {
  if (dragIndex.value === null || dragIndex.value === i) { dragIndex.value = null; dragOverIndex.value = null; return; }
  const arr = scenes.value.slice();
  const [moved] = arr.splice(dragIndex.value, 1);
  arr.splice(i, 0, moved);
  scenes.value = arr;
  dragIndex.value = null; dragOverIndex.value = null;
  scheduleSave();
}
function onDragEnd() { dragIndex.value = null; dragOverIndex.value = null; }

// ---- 完整故事 ----
function openStoryDialog() { storyDraft.value = storyText.value; showStoryDialog.value = true; }
function cancelStory() { showStoryDialog.value = false; }
function doneStory() { storyText.value = storyDraft.value; showStoryDialog.value = false; scheduleSave(); }

// ---- 清空 ----
function doClear() {
  scenes.value = [];
  showClearConfirm.value = false;
  scheduleSave();
}

// 切项目：清旧状态 + 加载新（GPT §一，避免显示上一个项目内容）
watch(projectName, () => {
  scenes.value = []; storyText.value = ''; status.value = 'idle';
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
  load();
});

onMounted(() => load());
// 离开页面：处理尚未提交的保存（写回当前项目）
onUnmounted(() => { if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; doSave(); } });
</script>

<style lang="scss" scoped>
.scene-editor { width: 100%; }

.no-project {
  text-align: center; padding: 80px 20px; color: var(--text-muted);
  p { margin-bottom: 16px; }
}

.topbar {
  display: flex; align-items: center; gap: 12px; margin-bottom: 18px;
}
.proj-name { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.link-btn {
  background: none; border: none; color: var(--text-muted); font-size: 12px; cursor: pointer;
  padding: 2px 4px; border-radius: 4px;
  &:hover { color: var(--accent); background: var(--bg-hover); }
  &.danger { color: #f87171; &:hover { color: #ef4444; } }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
}
.count { font-size: 12px; color: var(--text-muted); }
.save-status {
  margin-left: auto; font-size: 12px;
  &.saving { color: var(--text-muted); }
  &.saved { color: #4ade80; }
  &.failed { color: #f87171; a { text-decoration: underline; cursor: pointer; } }
}

// 完整故事卡
.story-card {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 14px 16px; margin-bottom: 18px; cursor: pointer; transition: border-color 0.15s;
  &:hover { border-color: var(--accent); }
}
.story-card-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.story-label { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.story-word { font-size: 11px; color: var(--text-faint); }
.story-edit { margin-left: auto; font-size: 12px; color: var(--accent); }
.story-preview { font-size: 13px; color: var(--text-secondary); line-height: 1.5; }
.story-empty { font-size: 13px; color: var(--text-faint); }

.section-head {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 12px; font-weight: 600; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px;
}

.scene-list { display: flex; flex-direction: column; gap: 8px; }

.scene-card {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px;
  transition: border-color 0.12s, opacity 0.12s;
  &.dragging { opacity: 0.4; }
  &.drag-over { border-color: var(--accent); border-style: dashed; }
}

.card-head {
  display: flex; align-items: center; gap: 10px; padding: 10px 12px; cursor: pointer;
}
.seq-badge {
  flex-shrink: 0; width: 22px; height: 22px; border-radius: 50%; background: var(--accent);
  color: #fff; font-size: 11px; font-weight: 600; display: flex; align-items: center; justify-content: center;
}
.card-title {
  font-size: 13px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  flex: 1; min-width: 0;
}
.card-chars { font-size: 11px; color: var(--text-faint); flex-shrink: 0; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-chevron { font-size: 12px; color: var(--text-muted); }
.del-btn {
  width: 22px; height: 22px; border-radius: 4px; border: none; background: none;
  color: var(--text-muted); font-size: 15px; cursor: pointer; display: flex; align-items: center; justify-content: center;
  &:hover { background: #fee2e2; color: #ef4444; }
}

.card-body { padding: 0 12px 12px; }
.card-body textarea {
  width: 100%; background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px;
  padding: 8px 10px; font-size: 13px; color: var(--text-primary); outline: none; resize: vertical;
  font-family: inherit; &:focus { border-color: var(--accent); }
}
.char-select { margin-top: 10px; }
.char-label { font-size: 11px; color: var(--text-muted); display: block; margin-bottom: 6px; }
.char-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip {
  padding: 3px 10px; border-radius: 12px; border: 1px solid var(--border); background: none;
  color: var(--text-muted); font-size: 12px; cursor: pointer; transition: all 0.12s;
  &:hover { border-color: var(--accent); color: var(--text-secondary); }
  &.on { background: var(--accent); border-color: var(--accent); color: #fff; }
}
.no-chars { font-size: 11px; color: var(--text-faint); }
.card-video { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.thumb { width: 80px; height: 45px; object-fit: cover; border-radius: 4px; background: var(--bg-root); }
.video-time { font-size: 11px; color: var(--text-muted); }

.empty { text-align: center; color: var(--text-muted); font-size: 13px; padding: 32px 0; }

.add-btn {
  margin-top: 12px; width: 100%; padding: 9px; border-radius: 6px;
  border: 1px dashed var(--border); background: transparent; color: var(--text-muted);
  font-size: 13px; cursor: pointer; &:hover { border-color: var(--accent); color: var(--accent); }
}

// 对话框
.dialog-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: flex;
  align-items: center; justify-content: center; z-index: 1000; backdrop-filter: blur(4px);
}
.story-dialog {
  background: var(--bg-surface); border-radius: 12px; padding: 20px;
  width: 640px; max-width: 92vw; display: flex; flex-direction: column; gap: 12px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.3);
}
.dialog-head { display: flex; align-items: center; justify-content: space-between; font-size: 14px; font-weight: 600; color: var(--text-primary); }
.word-count { font-size: 11px; color: var(--text-faint); font-weight: 400; }
.story-textarea {
  width: 100%; background: var(--bg-root); border: 1px solid var(--border); border-radius: 6px;
  padding: 12px; font-size: 14px; color: var(--text-primary); outline: none; resize: vertical;
  font-family: inherit; line-height: 1.6; &:focus { border-color: var(--accent); }
}
.confirm-dialog {
  background: var(--bg-surface); border-radius: 12px; padding: 22px; width: 380px; max-width: 90vw;
  h3 { margin: 0 0 14px; font-size: 15px; color: var(--text-primary); }
  p { font-size: 13px; color: var(--text-secondary); margin: 0 0 6px; }
  .warn { color: #fbbf24; }
}
.dialog-btns { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
.btn { padding: 7px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none; }
.btn-cancel { background: var(--bg-root); color: var(--text-muted); border: 1px solid var(--border); &:hover { background: var(--bg-hover); } }
.btn-confirm { background: var(--accent); color: var(--accent-text); &:hover { background: var(--accent-hover); } }
.btn-danger { background: #ef4444; color: #fff; &:hover { background: #dc2626; } }
.btn-primary { padding: 8px 16px; border-radius: 6px; border: none; background: var(--accent); color: var(--accent-text); font-size: 13px; cursor: pointer; }
</style>
