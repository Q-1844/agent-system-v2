<template>
  <div class="script-page">
    <!-- 无当前项目引导 -->
    <div v-if="!projectName" class="no-project">
      <p>还没有选择项目</p>
      <button class="btn btn-primary" @click="goProjects">前往项目序列</button>
    </div>

    <template v-else>
      <!-- 保存标志由 useTopbarStatus 上报，显示在顶栏最右上角；页面不再有工具条行 -->

      <!-- AI 后台修改剧本：只读锁横幅 -->
      <div v-if="pev.storyLocked" class="lock-banner">
        <span class="lock-spinner">◌</span>
        AI 助手正在修改剧本，编辑器已临时锁定为只读，改动会实时同步…
      </div>

      <div class="workbench">
        <!-- 左：完整故事（内联编辑） -->
        <aside class="story-panel">
          <div class="pane-head">
            <span class="section-label">完整故事</span>
            <span class="meta mono-num" v-if="storyWordCount">{{ storyWordCount }} 字</span>
          </div>
          <div class="sp-scroll">
            <textarea
              class="story-input"
              :value="storyText"
              @input="onStoryInput(($event.target as HTMLTextAreaElement).value)"
              placeholder="写下整个故事，作为编写分镜时的参考（不会被自动拆分成分镜）"
              spellcheck="false"
            ></textarea>
          </div>
          <div class="pane-foot">
            <button
              class="btn btn-outline btn-sm"
              :disabled="!storyText.trim()"
              :title="!storyText.trim() ? '请先写下完整故事，再让 AI 帮你整理。' : 'AI 把完整故事整理成故事段并识别人物'"
              @click="openOrganizer"
            >AI 整理故事</button>
            <button
              class="btn btn-outline btn-sm"
              :disabled="!storyText.trim()"
              :title="!storyText.trim() ? '请先写下完整故事，再续写。' : 'AI 在现有故事基础上继续创作，追加新故事段'"
              @click="openContinuation"
            >AI 续写</button>
          </div>
        </aside>

        <!-- 右：分镜故事列表 -->
        <section class="scene-pane">
          <div class="pane-head">
            <span class="section-label">分镜故事</span>
            <span class="meta mono-num" v-if="scenes.length">{{ scenes.length }} 条</span>
            <span class="spacer"></span>
            <span class="hint" v-if="scenes.length">拖拽调整顺序</span>
            <button class="btn btn-sm btn-quiet" :disabled="!scenes.length"
                    title="清空全部分镜故事（含其中镜头及已生成的画面/视频）"
                    @click="showClearConfirm = true">清空</button>
          </div>

          <div class="scene-list">
            <div
              v-for="(sc, i) in scenes"
              :key="sc.uid"
              class="scene"
              :class="{
                open: !sc.collapsed,
                dragging: dragIndex === i,
                'drag-over': dragOverIndex === i && dragIndex !== i,
                'is-new-flash': newSceneFlashId === sc.scene_id,
              }"
              :data-scene-id="sc.scene_id"
              draggable="true"
              @dragstart="onDragStart(i)"
              @dragover.prevent="onDragOver(i)"
              @dragleave="onDragLeave(i)"
              @drop="onDrop(i)"
              @dragend="onDragEnd"
            >
              <div class="scene-head" @click="toggleCollapse(i)">
                <span class="grip">⋮⋮</span>
                <span class="seq">{{ String(i + 1).padStart(2, '0') }}</span>
                <span class="scene-summary" :title="sc.summary.trim() || '未填写故事'">
                  {{ sc.summary.trim() || '未填写故事' }}
                </span>
                <span class="scene-chars" v-if="sc.characters.length">{{ sc.characters.join('、') }}</span>
                <span class="scene-shots">
                  <span class="dot" :class="{ live: sc.has_video }"></span>{{ sc.shot_count }} 镜
                </span>
                <button class="del" title="删除该分镜" @click.stop="removeScene(i)">×</button>
              </div>

              <div class="scene-body" v-if="!sc.collapsed">
                <textarea
                  class="scene-input"
                  :value="sc.summary"
                  @input="onSummaryInput(sc, ($event.target as HTMLTextAreaElement).value)"
                  placeholder="写这条分镜发生了什么…"
                  rows="3"
                  spellcheck="false"
                ></textarea>

                <div class="char-row">
                  <span class="label">出场角色</span>
                  <div class="chips">
                    <button
                      v-for="name in projectChars"
                      :key="name"
                      class="chip"
                      :class="{ on: sc.characters.includes(name) }"
                      @click="toggleChar(sc, name)"
                    >{{ name }}</button>
                    <span v-if="!projectChars.length" class="hint">当前项目无角色，请先在角色页创建</span>
                  </div>
                </div>

                <div class="video-row" v-if="sc.has_video">
                  <div class="thumb-ph" v-if="!sc.video_thumb"></div>
                  <img v-else :src="sc.video_thumb" class="thumb" alt="首帧缩略图" />
                  <span class="hint mono-num">已生成视频 · {{ sc.video_time }}</span>
                </div>
              </div>
            </div>

            <div v-if="!scenes.length" class="empty">
              暂无分镜故事，点击下方「+ 添加分镜」开始，或让 AI 从完整故事自动整理
            </div>
          </div>

          <div class="pane-foot-add">
            <button class="btn add-btn" @click="addScene">+ 添加分镜</button>
          </div>
        </section>
      </div>

      <!-- 清空确认 -->
      <div v-if="showClearConfirm" class="dialog-overlay" @click.self="showClearConfirm = false">
        <div class="confirm-dialog">
          <h3>清空分镜故事</h3>
          <p>即将删除 <b>{{ clearSceneCount }}</b> 条分镜故事。</p>
          <p v-if="clearShotCount > 0">其中含 <b>{{ clearShotCount }}</b> 个镜头，已生成的画面/视频将一并删除。</p>
          <div class="dialog-btns">
            <button class="btn btn-cancel" @click="showClearConfirm = false">取消</button>
            <button class="btn btn-danger" @click="doClear">确认清空</button>
          </div>
        </div>
      </div>

      <!-- 已有故事段时点 AI 整理的提示 -->
      <div v-if="showExistingScenesConfirm" class="dialog-overlay" @click.self="showExistingScenesConfirm = false">
        <div class="confirm-dialog">
          <h3>项目已有故事段</h3>
          <p>当前项目已经有 <b>{{ scenes.length }}</b> 个故事段。</p>
          <p>AI 可以重新整理完整故事，但不会自动覆盖现有故事段。</p>
          <div class="dialog-btns">
            <button class="btn btn-cancel" @click="showExistingScenesConfirm = false">取消</button>
            <button class="btn btn-confirm" @click="showExistingScenesConfirm = false; showOrganizer = true">查看新的整理方案</button>
          </div>
        </div>
      </div>

      <!-- AI 整理故事工作区 -->
      <StoryOrganizer
        v-if="showOrganizer"
        :project-name="projectName"
        @close="onOrganizerClose"
        @applied="onOrganizerApplied"
      />

      <!-- AI 续写故事工作区 -->
      <StoryContinuation
        v-if="showContinuation"
        :project-name="projectName"
        @close="onContinuationClose"
        @applied="onContinuationApplied"
      />

      <!-- 锁定遮罩：盖住编辑区，屏蔽所有指针事件 -->
      <div v-if="pev.storyLocked" class="lock-overlay"></div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { useRouter } from 'vue-router';
import { useProjectStore } from '@/store';
import { useProjectEventsStore } from '@/store/projectEvents';
import { useTopbarStatus } from '@/store/topbarStatus';
import { apiClient } from '@/api';
import { getImageUrl } from '@/utils/media';
import dayjs from 'dayjs';
import StoryOrganizer from '@/components/StoryOrganizer.vue';
import StoryContinuation from '@/components/StoryContinuation.vue';

const store = useProjectStore();
const router = useRouter();
const projectName = computed(() => store.projectName);

// AI-7D-R：Agent 后台改剧本时的只读锁 + 实时刷新（事件由 app 级 useProjectEvents 喂）
const pev = useProjectEventsStore();
watch(() => pev.storyLocked, (locked) => {
  if (!projectName.value) return;
  if (locked) {
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }   // 放弃未落盘的本地编辑（被锁覆盖）
    load();
  } else {
    load();   // 解锁：拉最终态
  }
});
watch(() => pev.storyVersion, () => {
  if (projectName.value && pev.storyLocked) load();
});

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
const projectChars = ref<string[]>([]);
const status = ref<'idle' | 'saving' | 'saved' | 'failed'>('idle');
const showClearConfirm = ref(false);
const showOrganizer = ref(false);
const showExistingScenesConfirm = ref(false);
const showContinuation = ref(false);
const newSceneFlashId = ref<string | null>(null);

let uidCounter = 1;
let saveTimer: number | null = null;
let loadSeq = 0;

const totalShots = computed(() => scenes.value.reduce((n, s) => n + s.shot_count, 0));
const storyWordCount = computed(() => storyText.value.trim().length);
const clearSceneCount = computed(() => scenes.value.length);
const clearShotCount = computed(() => totalShots.value);

// 保存标志：上报到顶栏最右上角（替代原先的页面工具条位置）
useTopbarStatus('script-editor', () => {
  if (status.value === 'saving') return { text: '保存中…', tone: 'busy' as const };
  if (status.value === 'failed') return { text: '保存失败', tone: 'fail' as const, actionText: '重试', action: doSave };
  return { text: '已保存', tone: 'idle' as const };
});

const dragIndex = ref<number | null>(null);
const dragOverIndex = ref<number | null>(null);

function goProjects() { router.push('/projects'); }

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

// ---- 完整故事：内联编辑 ----
function onStoryInput(val: string) {
  storyText.value = val;
  scheduleSave();
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

// ---- AI 整理故事（Phase AI-1）----
function openOrganizer() {
  if (!storyText.value.trim()) return;   // 按钮已禁用，兜底
  if (scenes.value.length > 0) {
    showExistingScenesConfirm.value = true;
  } else {
    showOrganizer.value = true;
  }
}
function onOrganizerClose() { showOrganizer.value = false; }
function onOrganizerApplied(goDesignChar?: string | null) {
  showOrganizer.value = false;
  load();
  store.loadProjectCharacters(projectName.value);
  if (goDesignChar) {
    router.push({ path: '/resources', query: { tab: 'characters', design: goDesignChar } });
  }
}

// ---- AI 续写故事（Phase AI-1B）----
function openContinuation() {
  if (!storyText.value.trim()) return;   // 按钮已禁用，兜底
  showContinuation.value = true;
}
function onContinuationClose() { showContinuation.value = false; }
async function onContinuationApplied(firstNewSceneId: string | null, goDesignChar?: string | null) {
  showContinuation.value = false;
  await load();
  store.loadProjectCharacters(projectName.value);
  if (goDesignChar) {
    router.push({ path: '/resources', query: { tab: 'characters', design: goDesignChar } });
  } else if (firstNewSceneId) {
    await nextTick();
    flashNewScene(firstNewSceneId);
  }
}
function flashNewScene(sceneId: string) {
  const el = document.querySelector(`[data-scene-id="${sceneId}"]`);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  newSceneFlashId.value = sceneId;
  setTimeout(() => { newSceneFlashId.value = null; }, 3500);
}

// ---- 清空 ----
function doClear() {
  scenes.value = [];
  showClearConfirm.value = false;
  scheduleSave();
}

// 切项目：清旧状态 + 加载新
watch(projectName, () => {
  scenes.value = []; storyText.value = ''; status.value = 'idle';
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
  load();
});

onMounted(() => load());
onUnmounted(() => { if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; doSave(); } });
</script>

<style lang="scss" scoped>
// ============================================================
// 剧本编辑 · 布局 A：双栏工作台
// 左：完整故事（340px 固定，内联编辑）
// 右：分镜故事（行式卡片，点开就地展开）
// 排版规范：区块标签 11px/字距 .08em · 正文 13px/行高 1.8
//          行卡栅格 14/26/1fr/auto/auto/24 · 展开内容对齐摘要（62px）
// ============================================================

$row-grid: 14px 26px minmax(0, 1fr) auto auto 24px;
$row-gap: 10px;
$row-pad-x: 12px;
$expand-indent: 62px;

.script-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
  position: relative;
}

.no-project {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  color: var(--text-muted);
}

.mono-num { font-variant-numeric: tabular-nums; }

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-faint);
  flex-shrink: 0;

  &.on { background: var(--text-secondary); }
  &.live { background: var(--text-primary); }
  &.fail { background: var(--error); }
}

.hint { font-size: 12px; color: var(--text-faint); }

/* ---------- 只读锁 ---------- */
.lock-banner {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-hover);
  color: var(--text-secondary);
  font-size: 12px;
}

.lock-spinner {
  display: inline-block;
  animation: lock-spin 1s linear infinite;
  color: var(--text-muted);
}

@keyframes lock-spin { to { transform: rotate(360deg); } }

.lock-overlay {
  position: absolute;
  inset: 0;
  z-index: 20;
  background: transparent;
  cursor: not-allowed;
}

/* ---------- 工作台 ---------- */
.workbench {
  flex: 1;
  min-height: 0;
  display: flex;
}

.pane-head {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  /* 固定最小高度：两侧面板标题栏等高，分隔线严格对齐（右侧含 28px 按钮） */
  min-height: 52px;
  padding: 8px 20px;
  border-bottom: 1px solid var(--border);

  .spacer { flex: 1; }
}

.section-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--text-faint);
}

.pane-foot {
  flex-shrink: 0;
  display: flex;
  gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid var(--border);
}

/* ---------- 左：完整故事 ---------- */
.story-panel {
  width: 340px;
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.sp-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 20px;
}

.story-input {
  width: 100%;
  min-height: 100%;
  border: none;
  outline: none;
  background: transparent;
  resize: none;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.85;
  transition: color 0.15s ease;

  &::placeholder { color: var(--text-faint); }
  &:focus { color: var(--text-primary); }
}

/* ---------- 右：分镜列表 ---------- */
.scene-pane {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.scene-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px 20px 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.scene {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-surface);
  transition: border-color 0.15s ease, opacity 0.15s ease;

  &:hover { border-color: var(--border-hover); }
  &.open { border-color: var(--border-hover); }
  &.dragging { opacity: 0.4; }
  &.drag-over { border-style: dashed; border-color: var(--text-muted); }

  &.is-new-flash {
    position: relative;
    animation: new-flash-pulse 3.5s ease-out;

    &::after {
      content: 'AI 新增';
      position: absolute;
      top: -8px;
      right: 12px;
      padding: 1px 8px;
      border-radius: 8px;
      background: var(--accent);
      color: var(--accent-text);
      font-size: 10px;
      font-weight: 600;
    }
  }
}

@keyframes new-flash-pulse {
  0%, 30% { background: var(--bg-active); }
  100% { background: var(--bg-surface); }
}

.scene-head {
  display: grid;
  grid-template-columns: $row-grid;
  align-items: center;
  gap: $row-gap;
  padding: 11px $row-pad-x;
  cursor: pointer;
}

.grip {
  color: var(--text-faint);
  font-size: 10px;
  letter-spacing: -1px;
  opacity: 0;
  transition: opacity 0.15s ease;
  user-select: none;

  .scene:hover & { opacity: 0.8; }
}

.seq {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.04em;
}

.scene-summary {
  font-size: 13px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;

  .scene.open & { color: var(--text-primary); }
}

.scene-chars {
  font-size: 11px;
  color: var(--text-faint);
  white-space: nowrap;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.scene-shots {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.del {
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 4px;
  background: none;
  color: var(--text-faint);
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s ease, color 0.15s ease;

  .scene:hover & { opacity: 1; }
  &:hover { color: var(--text-primary); background: var(--bg-hover); }
}

/* 展开区：与摘要左对齐，正文限宽保证可读性 */
.scene-body {
  padding: 0 $row-pad-x 14px $expand-indent;
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 780px;
}

.scene-input {
  width: 100%;
  padding: 9px 11px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-root);
  color: var(--text-primary);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.8;
  resize: vertical;
  outline: none;
  transition: border-color 0.15s ease;

  &::placeholder { color: var(--text-faint); }
  &:focus { border-color: var(--border-hover); }
}

.char-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;

  .label {
    flex-shrink: 0;
    font-size: 11px;
    color: var(--text-faint);
    line-height: 22px;
  }
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chip {
  height: 22px;
  padding: 0 9px;
  border: 1px solid var(--border);
  border-radius: 11px;
  background: none;
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s ease;

  &:hover { border-color: var(--border-hover); color: var(--text-secondary); }
  &.on {
    background: var(--bg-active);
    border-color: var(--border-hover);
    color: var(--text-primary);
  }
}

.video-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.thumb {
  width: 96px;
  height: 54px;
  object-fit: cover;
  border-radius: 6px;
  border: 1px solid var(--border);
}

.thumb-ph {
  width: 96px;
  height: 54px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: linear-gradient(160deg, var(--bg-root), var(--bg-surface));
}

.empty {
  padding: 40px 24px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  text-align: center;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-muted);
}

.pane-foot-add {
  flex-shrink: 0;
  padding: 8px 20px 16px;
}

.add-btn {
  width: 100%;
  height: 38px;
  justify-content: center;
  border: 1px dashed var(--border);
  color: var(--text-muted);

  &:hover { border-color: var(--border-hover); color: var(--text-primary); background: none; }
}

/* ---------- 按钮 ---------- */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border: none;
  border-radius: 6px;
  background: none;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;

  &:disabled { opacity: 0.45; cursor: not-allowed; }
}

.btn-sm { height: 28px; padding: 0 10px; font-size: 12px; }

.btn-outline {
  border: 1px solid var(--border);
  color: var(--text-secondary);

  &:hover:not(:disabled) { border-color: var(--border-hover); color: var(--text-primary); background: none; }
}

.btn-quiet {
  color: var(--text-faint);

  &:hover:not(:disabled) { color: var(--text-secondary); background: var(--bg-hover); }
}

.btn-primary {
  background: var(--accent);
  color: var(--accent-text);

  &:hover:not(:disabled) { background: var(--accent-hover); color: var(--accent-text); }
}

/* ---------- 对话框 ---------- */
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
}

.confirm-dialog {
  width: 380px;
  max-width: 90vw;
  padding: 22px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-surface);
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);

  h3 {
    margin-bottom: 14px;
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary);
  }

  p {
    margin-bottom: 8px;
    font-size: 13px;
    line-height: 1.7;
    color: var(--text-muted);

    b { color: var(--text-primary); font-weight: 600; }
  }
}

.dialog-btns {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
}

.btn-cancel {
  border: 1px solid var(--border);
  color: var(--text-muted);

  &:hover { background: var(--bg-hover); color: var(--text-secondary); }
}

.btn-confirm {
  background: var(--accent);
  color: var(--accent-text);

  &:hover { background: var(--accent-hover); }
}

.btn-danger {
  border: 1px solid rgba(239, 68, 68, 0.4);
  color: var(--error);

  &:hover { background: rgba(239, 68, 68, 0.1); }
}

/* ---------- 窄屏 ---------- */
@media (max-width: 1080px) {
  .workbench { flex-direction: column; }

  .story-panel {
    width: 100%;
    border-right: none;
    border-bottom: 1px solid var(--border);
    max-height: 40%;
  }

  .story-input { min-height: 120px; }
}
</style>
