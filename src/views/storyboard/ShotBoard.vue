<template>
  <div class="shot-board">
    <!-- 无当前项目 -->
    <div v-if="!projectName" class="empty">
      请先选择项目
      <button class="go-btn" @click="goDashboard">前往项目页</button>
    </div>

    <!-- 无故事段 -->
    <div v-else-if="!segments.length" class="empty">
      当前项目还没有分镜故事，请先在剧本页添加分镜故事。
      <button class="go-btn" @click="goScript">前往剧本</button>
    </div>

    <!-- 主体：左故事段列表 + 右当前故事段 -->
    <div v-else class="board">
      <button v-if="listCollapsed" class="expand-fab" @click="listCollapsed = false" title="展开故事段列表">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6" /></svg>
        <span>故事段</span>
      </button>
      <aside class="seg-list" :class="{ collapsed: listCollapsed }">
        <div class="seg-list-head">
          <span class="seg-list-title">故事段</span>
          <button class="icon-btn" title="收起故事段列表" @click="listCollapsed = true">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6" /></svg>
          </button>
        </div>
        <div class="seg-list-content">
        <div
          v-for="(seg, i) in segments"
          :key="seg.scene_id"
          class="seg-item"
          :class="{ active: selected?.scene_id === seg.scene_id }"
          @click="selected = seg"
        >
          <img v-if="segThumb(seg)" :src="segThumb(seg)" class="seg-thumb" />
          <div class="seg-main">
            <div class="seg-row1">
              <span class="seg-seq">{{ i + 1 }}</span>
              <span class="seg-title">{{ seg.summary.trim() ? seg.summary.trim().slice(0, 14) : '未填写故事' }}</span>
            </div>
            <div class="seg-row2">
              <span class="seg-count">{{ shotDone(seg) }}/{{ seg.shots.length }} 镜</span>
            </div>
          </div>
        </div>
        </div>
        <div class="seg-list-foot">
          <button class="ai-full-btn primary" @click="openFullProduction">AI 完整制作</button>
          <button class="ai-full-btn" @click="openProduction">自动制作完整影片</button>
        </div>
      </aside>

      <section class="seg-detail" v-if="selected">
        <!-- 2026-08-31：Agent 正在修改分镜时的只读横幅（复用 Project SSE resource.locked） -->
        <div v-if="storyboardLocked" class="agent-lock-banner">
          Director 正在修改分镜，页面暂为只读；改动会实时同步进来，完成后自动恢复编辑。
        </div>
        <!-- 故事段顶部：收成一行（号 + 标题 + 展开箭头 + 操作），原文与角色默认隐藏 -->
        <div class="seg-head">
          <div class="seg-head-top">
            <span class="seg-no">故事段 {{ selected.scene_id }}</span>
            <span class="seg-head-title">{{ selected.summary.trim() || '未填写故事' }}</span>
            <button class="icon-btn story-toggle" :class="{ open: segHeadOpen }"
                    :title="segHeadOpen ? '收起故事原文与角色' : '展开故事原文与角色'"
                    @click="segHeadOpen = !segHeadOpen">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9" /></svg>
            </button>
            <div class="seg-head-actions">
              <button class="ai-direct-btn" @click="openDirector">AI 设计分镜</button>
              <div class="menu-wrap">
                <button class="ai-direct-btn menu-trigger" @click.stop="toggleMenu('prod')">
                  制作
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="6 9 12 15 18 9" /></svg>
                </button>
                <div v-if="openMenu === 'prod'" class="menu-pop" @click.stop>
                  <button class="menu-item"
                          :disabled="!selected.shots.length"
                          :title="selected.shots.length ? '' : '请先创建镜头或使用 AI 设计分镜。'"
                          @click="closeMenus(); openPlanner()">AI 制作规划</button>
                  <button class="menu-item"
                          :disabled="!selected.shots.length"
                          :title="selected.shots.length ? '' : '请先创建镜头。'"
                          @click="closeMenus(); openAutopilot()">自动制作本故事段</button>
                </div>
              </div>
              <div class="menu-wrap">
                <button class="icon-btn more-btn" title="更多" @click.stop="toggleMenu('more')">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></svg>
                </button>
                <div v-if="openMenu === 'more'" class="menu-pop" @click.stop>
                  <button class="menu-item" title="以当前故事段为上下文，打开 Director 对话" @click="closeMenus(); askDirectorStory()">Director 对话</button>
                  <button class="menu-item" title="以当前故事段为 scope 生成 Director 修改提案（经 Workspace/ChangeSet，官方分镜不变）" @click="closeMenus(); openPropose('scene')">Director 修改提案</button>
                  <div class="menu-sep"></div>
                  <button class="menu-item" @click="closeMenus(); goScript()">返回剧本修改</button>
                </div>
              </div>
            </div>
          </div>
          <div v-if="segHeadOpen" class="seg-head-body">
            <p class="seg-story">{{ selected.summary || '（未填写故事）' }}</p>
            <div class="seg-chars">
              <span class="seg-chars-label">出场角色：</span>
              <span v-if="!selected.characters.length" class="muted">无</span>
              <span v-for="name in selected.characters" :key="name" class="seg-char" :class="{ miss: !charCovered(name) }"
                    :title="charCovered(name) ? '已在镜头中出现' : '未出现在任何镜头'">{{ name }}</span>
            </div>
          </div>
        </div>

        <!-- AI-6D.1 §8/§19 Director 修改提案 触发（显式 instruction；生成态内聚）-->
        <div v-if="proposeOpen" class="propose-bar" :class="{ busy: proposing }">
          <div class="propose-bar-scope">{{ proposeScope === 'shot' ? '镜头' : '故事段' }} {{ proposeScopeId || '—' }} · Director 修改提案</div>
          <textarea v-model="proposeInstruction" rows="2" :disabled="proposing"
            placeholder="描述你想怎么改（如：把第 2 镜拆成反应镜头、压缩节奏、调整情绪）。Director 会给出可逐项选择应用的修改。" />
          <div class="propose-bar-actions">
            <button class="link-btn" @click="proposeOpen = false" :disabled="proposing">取消</button>
            <button class="ai-direct-btn primary" :disabled="proposing || !proposeInstruction.trim()" @click="runPropose">
              {{ proposing ? '生成中…' : '生成修改提案' }}
            </button>
          </div>
          <div v-if="proposeError" class="propose-error">{{ proposeError }}</div>
        </div>

        <!-- AI-6D.1 §9 真实 Director Proposal 呈现 + 选择性预览 + apply（官方分镜不变）-->
        <DirectorProposalOverlay
          :proposal="currentProposal"
          :project-name="projectName"
          @close="currentProposal = null"
          @updated="onProposalUpdated"
          @applied="onProposalApplied"
        />

        <!-- AI-6D.1 §14 apply 后：克制成功反馈，明确只改创作版本 -->
        <div v-if="appliedProposal" class="applied-proposal-banner">
          <span class="apb-ok">已应用到创作版本</span>
          <span class="apb-sub">官方分镜未变</span>
          <button class="link-btn" @click="openCreativeVersion(appliedProposal.workspace_id)">打开创作版本</button>
          <button class="link-btn" @click="appliedProposal = null">继续查看官方分镜</button>
        </div>

        <!-- AI 导演采用后提示（§三十六）-->
        <div v-if="appliedHint" class="applied-hint">
          <span>AI 已创建 {{ appliedHint.count }} 个镜头。接下来可打开镜头工作台，生成首帧、尾帧和视频提示词。</span>
          <button class="link-btn" v-if="appliedHint.firstShotId" @click="openFirstShot">打开镜头 1</button>
          <button class="link-btn" @click="appliedHint = null">关闭</button>
        </div>

        <!-- 镜头列表 -->
        <div class="shot-list">
          <template v-for="sh in selected.shots" :key="sh.shot_id">
          <div
            class="shot-item"
            :class="{ active: selectedShotId === sh.shot_id }"
            :data-shot-id="sh.shot_id"
            @click="selectShot(sh)"
            @dblclick="openWorkbench(sh)"
            @contextmenu.prevent="openCtxMenu($event, sh)"
          >
            <DirectorContextToolbar
              v-if="selectedShotId === sh.shot_id"
              @action="onDirectorAction"
            />
            <div class="shot-meta">
              <span class="shot-seq">{{ sh.shot_id }}</span>
              <span class="shot-size">{{ sh.shot_size || '中景' }}</span>
              <span class="shot-desc">{{ (sh.description || '').slice(0, 24) || '未填写画面' }}</span>
              <span class="shot-chars" v-if="sh.characters.length">{{ sh.characters.join('、') }}</span>
              <span v-if="sh.opening_source !== 'independent'" class="shot-cont" :class="connClass(sh)">{{ connLabel(sh) }}</span>
              <span v-if="boundShotIds.has(sh.shot_id)" class="shot-cont"
                    title="已绑定生产场景参考（生成时使用场景图与角色 Look）">场景</span>
              <span class="shot-status" :class="shotStatusClass(sh)">{{ shotStatusText(sh) }}</span>
              <button class="open-btn" @click="openWorkbench(sh)">打开工作台</button>
            </div>
            <div class="shot-previews">
              <div class="preview-box" @click="previewImage(ffUrl(sh))" :class="{ disabled: !ffUrl(sh) }">
                <img v-if="ffUrl(sh)" :src="ffUrl(sh)" />
                <SafeFrameOverlay v-if="ffUrl(sh)" />
                <span v-else class="ph">首帧</span>
              </div>
              <div class="preview-box" @click="lfUrl(sh) && previewImage(lfUrl(sh))" :class="{ disabled: !lfUrl(sh), na: sh.link_mode !== 'keyframes' }">
                <img v-if="lfUrl(sh)" :src="lfUrl(sh)" />
                <span v-else class="ph">{{ sh.link_mode === 'keyframes' ? '尾帧' : '不需要' }}</span>
              </div>
              <div class="preview-box" @click="vidUrl(sh) && previewVideo(vidUrl(sh))" :class="{ disabled: !vidUrl(sh) }">
                <video v-if="vidUrl(sh)" :src="vidUrl(sh)" preload="metadata" />
                <span v-else class="ph">视频</span>
              </div>
            </div>
          </div>
          </template>
          <button class="add-shot-btn" :disabled="storyboardLocked" @click="addShot">+ 添加镜头</button>
        </div>
      </section>
    </div>

    <!-- 工作台弹层 -->
    <Workbench
      v-if="workbenchShot"
      :key="workbenchShot.shot_id"
      :shot="workbenchShot"
      :project-name="projectName"
      :project-characters="projectCharacters"
      :project-style-label="projectStyleLabel"
      :project-ratio="projectRatio"
      :scene-story="selected?.summary || ''"
      :scene-id="selected?.scene_id || ''"
      :scene-shots="selected?.shots || []"
      @close="closeWorkbench"
      @refresh="load"
    />

    <!-- 大图/视频预览 -->
    <div v-if="previewUrl" class="preview-overlay" @click="previewUrl = ''">
      <img v-if="previewType === 'image'" :src="previewUrl" class="big-img" />
      <video v-else :src="previewUrl" controls autoplay class="big-video" />
    </div>

    <!-- AI 导演工作区（Phase AI-3） -->
    <DirectorStudio
      v-if="showDirector && selected"
      :project-name="projectName"
      :scene-id="selected.scene_id"
      :scene-summary="selected.summary"
      :scene-characters="selected.characters"
      :has-existing-shots="selected.shots.length > 0"
      @close="showDirector = false"
      @applied="onDirectorApplied"
    />

    <!-- AI 制作规划工作区（Phase AI-4A） -->
    <ProductionPlannerStudio
      v-if="showPlanner && selected"
      :project-name="projectName"
      :scene-id="selected.scene_id"
      :scene-summary="selected.summary"
      :scene-characters="selected.characters"
      :shots="selected.shots.map(s => ({ shot_id: s.shot_id, description: s.description, characters: s.characters, target_duration_s: s.target_duration_s }))"
      @close="showPlanner = false"
      @approved="onPlannerApproved"
    />

    <!-- 自动制作工作区（Phase AI-4B） -->
    <AutopilotRunMonitor
      v-if="showAutopilot && selected"
      :project-name="projectName"
      :scene-id="selected.scene_id"
      :scene-summary="selected.summary"
      :shots="selected.shots.map(s => ({ shot_id: s.shot_id, description: s.description }))"
      @close="onAutopilotClose"
    />

    <!-- 完整影片自动制作工作区（Phase AI-5A） -->
    <ProjectProductionStudio
      v-if="showProduction"
      :project-name="projectName"
      @close="showProduction = false"
    />

    <!-- AI 完整制作工作区（Phase AI-5B：缺分镜/制作规划自动补齐 -> 完整影片） -->
    <FullProductionStudio
      v-if="showFullProduction"
      :project-name="projectName"
      @close="onFullProductionClose"
    />

    <!-- 镜头右键菜单 -->
    <div v-if="ctxMenu" class="ctx-menu" :style="ctxMenuStyle" @click.stop>
      <div class="ctx-shot-id">{{ ctxMenu.shot.shot_id }}</div>
      <button class="ctx-item" @click="openWorkbench(ctxMenu.shot); closeCtxMenu()">打开工作台</button>
      <button class="ctx-item danger" @click="confirmDeleteShot">删除镜头</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { useRouter } from 'vue-router';
import { apiClient } from '@/api';
import type { ShotData } from '@/api';
import { useProjectStore } from '@/store';
import { useJobStore } from '@/store/jobs';
import { getImageUrl, getVideoUrl } from '@/utils/media';
import SafeFrameOverlay from '@/components/SafeFrameOverlay.vue';
import Workbench from '@/components/Workbench.vue';
import DirectorStudio from '@/components/DirectorStudio.vue';
import ProductionPlannerStudio from '@/components/ProductionPlannerStudio.vue';
import AutopilotRunMonitor from '@/components/AutopilotRunMonitor.vue';
import ProjectProductionStudio from '@/components/ProjectProductionStudio.vue';
import FullProductionStudio from '@/components/FullProductionStudio.vue';
import DirectorContextToolbar from '@/components/storyboard/DirectorContextToolbar.vue';
import DirectorProposalOverlay from '@/components/storyboard/DirectorProposalOverlay.vue';
import type { DirectorProposal } from '@/components/storyboard/proposal';
import { useAgentUiStore } from '@/store/agentUi';
import { useProjectEventsStore } from '@/store/projectEvents';

const router = useRouter();
const projStore = useProjectStore();
const projectName = computed(() => projStore.projectName);
const jobStore = useJobStore();
const agentUi = useAgentUiStore();          // AI-6C Floating Agent
const selectedShotId = ref<string | null>(null);   // §13 shot 级 selection（原仅 scene 级）
const currentProposal = ref<DirectorProposal | null>(null);  // §19 Proposal 边界（无真实数据时不渲染）

// AI-6D Director Proposal（storyboard.propose_change，§11/§13/§16/§19/§20）
const proposeOpen = ref(false);
const proposeScope = ref<'shot' | 'scene'>('scene');
const proposeScopeId = ref('');
const proposeInstruction = ref('');
const proposing = ref(false);
const proposeError = ref('');
const appliedProposal = ref<{ workspace_id: string } | null>(null);

function openPropose(scope: 'shot' | 'scene') {
  if (!selected.value) return;
  proposeScope.value = scope;
  proposeScopeId.value = scope === 'shot' ? (selectedShotId.value || '') : selected.value.scene_id;
  proposeInstruction.value = scope === 'shot'
    ? `请重新设计镜头 ${proposeScopeId.value}，给出一个更具表现力的修改方案（可含更新/插入相邻镜头）。`
    : `请优化故事段 ${selected.value.scene_id} 的镜头节奏与表现力，给出修改方案。`;
  proposeError.value = '';
  proposeOpen.value = true;
}

async function runPropose() {
  if (!selected.value || !projectName.value) return;
  if (proposeScope.value === 'shot' && !proposeScopeId.value) { proposeError.value = '请先选中一个镜头'; return; }
  proposing.value = true; proposeError.value = '';
  try {
    const p = await apiClient.createDirectorProposal(projectName.value, {
      agent_id: 'director', scope_type: proposeScope.value, scope_id: proposeScopeId.value,
      instruction: proposeInstruction.value,
    });
    currentProposal.value = p;
    appliedProposal.value = null;
    proposeOpen.value = false;
  } catch (e: any) {
    proposeError.value = e.message || '生成提案失败';
  } finally { proposing.value = false; }
}

async function restoreProposal() {
  // §16 刷新恢复：取当前 scope（shot 优先，否则 scene）最近一个未 dismissed/applied 的 Proposal
  if (!selected.value || !projectName.value) { currentProposal.value = null; return; }
  const scope_type = selectedShotId.value ? 'shot' : 'scene';
  const scope_id = selectedShotId.value || selected.value.scene_id;
  try {
    const list = await apiClient.listDirectorProposals(projectName.value, scope_type, scope_id);
    const active = list.find(p => p.status !== 'applied' && p.status !== 'dismissed');
    currentProposal.value = active ? await apiClient.getDirectorProposal(projectName.value, active.proposal_id) : null;
  } catch { /* 静默：刷新恢复非关键 */ }
}

function onProposalUpdated(p: DirectorProposal) { currentProposal.value = p; }

async function onProposalApplied(p: DirectorProposal) {
  currentProposal.value = null;
  appliedProposal.value = { workspace_id: p.workspace_id };
  await load();   // canonical 未变（§21），reload 保持视图一致
}

function openCreativeVersion(wid: string) {
  router.push({ path: '/workspace', query: { workspace_id: wid } });
}

interface Segment {
  scene_id: string; summary: string; characters: string[]; shots: ShotData[];
}
const segments = ref<Segment[]>([]);
const selected = ref<Segment | null>(null);
const listCollapsed = ref(false);
const segHeadOpen = ref(false);                       // 故事原文与角色默认收起，点标题旁箭头展开
const openMenu = ref<'prod' | 'more' | null>(null);   // 故事段头「制作▾」「⋯」下拉菜单
function toggleMenu(m: 'prod' | 'more') { openMenu.value = openMenu.value === m ? null : m; }
function closeMenus() { openMenu.value = null; }
const workbenchShot = ref<ShotData | null>(null);
// 已绑定生产场景参考的镜头（分镜卡角标；Workbench 内绑定/解除后经 @refresh 重取）
const boundShotIds = ref<Set<string>>(new Set());
const previewUrl = ref('');
const previewType = ref<'image' | 'video'>('image');
const showDirector = ref(false);
const showPlanner = ref(false);
const showAutopilot = ref(false);
const showProduction = ref(false);
const showFullProduction = ref(false);
const appliedHint = ref<{ count: number; firstShotId: string | null } | null>(null);
// 项目级数据（传给 Workbench 作公共上下文：角色多选/风格/画幅徽标）
const projectCharacters = ref<Array<{ name: string; description_en: string; views: any }>>([]);
const projectStyleLabel = ref('');
const projectRatio = ref('');

function goDashboard() { router.push('/dashboard'); }
function goScript() { router.push('/script-editor'); }

async function load() {
  if (!projectName.value) { segments.value = []; return; }
  try {
    const data = await apiClient.getProjectShots(projectName.value);
    projectCharacters.value = data.characters || [];
    projectStyleLabel.value = (data.style && data.style.art_style) || '';
    projectRatio.value = (data.style && data.style.aspect_ratio) || '';
    // 绑定角标（非阻塞；失败静默——角标是辅助信息）
    apiClient.listShotBindings(projectName.value)
      .then((r: any) => { boundShotIds.value = new Set(Object.keys(r?.bindings || {})); })
      .catch(() => {});
    segments.value = (data.scenes || []).map((s: any) => ({
      scene_id: s.scene_id, summary: s.summary || '', characters: s.characters || [], shots: s.shots || [],
    }));
    // 保持选中（若仍存在），否则选第一个
    if (selected.value) {
      const cur = segments.value.find(s => s.scene_id === selected.value!.scene_id);
      selected.value = cur || segments.value[0] || null;
    } else {
      selected.value = segments.value[0] || null;
    }
    // 工作台打开时，刷新其 shot 引用（生成完成后工作台能看到新候选/视频）
    if (workbenchShot.value) {
      const wshot = segments.value.flatMap(s => s.shots).find(sh => sh.shot_id === workbenchShot.value!.shot_id);
      if (wshot) workbenchShot.value = wshot;
    }
    // AI-6D §16：刷新恢复当前 scope 的 active Proposal
    await restoreProposal();
  } catch (e: any) {
    console.error('[ShotBoard] 加载失败', e);
    segments.value = [];
    selected.value = null;
  }
}

// 镜头完成数（video done）
function shotDone(seg: Segment): number {
  return seg.shots.filter(sh => sh.video?.status === 'done' && sh.video?.local).length;
}
// 故事段首帧缩略图（第一个有首帧的镜头）
function segThumb(seg: Segment): string {
  for (const sh of seg.shots) {
    const u = ffUrl(sh);
    if (u) return u;
  }
  return '';
}
// 角色覆盖：该角色是否出现在故事段任一镜头
function charCovered(name: string): boolean {
  return !!selected.value?.shots.some(sh => sh.characters.includes(name));
}

// 三预览框 URL（读批次选择指针 selected_first_frame / selected_last_frame / final_video）
function ffUrl(sh: ShotData): string {
  const cid = sh.selected_first_frame;
  if (!cid) return '';
  for (const b of sh.first_frame_batches || []) {
    const c = (b.candidates || []).find(x => x.candidate_id === cid);
    if (c?.local) return getImageUrl(c.local);
  }
  return '';
}
function lfUrl(sh: ShotData): string {
  if (sh.link_mode !== 'keyframes') return '';
  const cid = sh.selected_last_frame;
  if (!cid) return '';
  for (const b of sh.last_frame_batches || []) {
    const c = (b.candidates || []).find(x => x.candidate_id === cid);
    if (c?.local) return getImageUrl(c.local);
  }
  return '';
}
function vidUrl(sh: ShotData): string {
  const vid = sh.final_video;
  if (!vid) return '';
  const v = (sh.video_versions || []).find(x => x.version_id === vid);
  return v?.local ? getVideoUrl(v.local) : '';
}

function shotStatusText(sh: ShotData): string {
  const busy = jobStore.byShot(sh.shot_id).some(j => j.state === 'running' || j.state === 'queued');
  if (busy) {
    const j = jobStore.byShot(sh.shot_id).find(j => j.state === 'running' || j.state === 'queued');
    return j?.kind === 'video' ? '视频排队中' : '生成中';
  }
  if (sh.video?.status === 'done' && sh.video?.local) return '已完成';
  if (sh.first_frame?.status === 'failed' || sh.video?.status === 'failed') return '失败';
  return '未完成';
}
function shotStatusClass(sh: ShotData): string {
  const s = shotStatusText(sh);
  if (s === '已完成') return 'done';
  if (s === '失败') return 'failed';
  if (s === '未完成') return 'pending';
  return 'busy';
}

function openWorkbench(sh: ShotData) {
  if (storyboardLocked.value) return;   // Agent 修改分镜期间只读：不打开编辑工作台
  workbenchShot.value = sh;
}
function closeWorkbench() { workbenchShot.value = null; }

// ---- AI-6C：Shot 级 selection + Contextual Director（复用 Floating Bar）----
function selectShot(sh: ShotData) { selectedShotId.value = sh.shot_id; }

function directorPrompt(action: string, sh: ShotData): string {
  const intent = (sh.description || '').trim() || '（未填写画面意图）';
  if (action === 'analyze')  return `分析镜头 ${sh.shot_id}（${sh.shot_size || '中景'}）。画面意图：${intent}。请从叙事推进与视觉呈现两个角度评估这一镜。`;
  if (action === 'pace')     return `镜头 ${sh.shot_id} 的节奏需要优化。画面意图：${intent}。请给出节奏调整建议。`;
  if (action === 'compose') return `分析镜头 ${sh.shot_id} 的构图。画面意图：${intent}。请给出构图改进建议。`;
  if (action === 'redesign')return `请重新设计镜头 ${sh.shot_id}。当前画面意图：${intent}。给出一个更具表现力的新镜头方案。`;
  return '';
}

// §15：quick action 设 agent=director + context=shot + 复用 Floating Bar，不调 director.py、不开第二套聊天
// §19：'propose' 是显式的 storyboard.propose_change 触发（shot scope），不复用聊天。
async function onDirectorAction(action: string) {
  const sid = selectedShotId.value;
  if (!selected.value || !sid) return;
  if (action === 'propose') { openPropose('shot'); return; }
  const sh = selected.value.shots.find(s => s.shot_id === sid);
  if (!sh) return;
  agentUi.draft = action === 'ask' ? '' : directorPrompt(action, sh);
  await agentUi.setAgentContext('director', {
    type: 'shot', sceneId: selected.value.scene_id, shotId: sh.shot_id, description: sh.description,
  });
  agentUi.expandConversation();
}

// §16：Story 级 Director 入口，context=scene
async function askDirectorStory() {
  if (!selected.value) return;
  await agentUi.setAgentContext('director', {
    type: 'scene', sceneId: selected.value.scene_id, summary: selected.value.summary,
  });
  agentUi.expandConversation();
}

// ---- AI 导演（Phase AI-3）----
function openDirector() {
  if (!selected.value) return;
  appliedHint.value = null;
  showDirector.value = true;
}
async function onDirectorApplied(firstNewShotId: string | null) {
  showDirector.value = false;
  await load();   // 刷新：新 Shot[] 出现在当前 Scene 下
  if (firstNewShotId) {
    await nextTick();
    const el = document.querySelector(`[data-shot-id="${firstNewShotId}"]`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  if (selected.value) {
    appliedHint.value = { count: selected.value.shots.length, firstShotId: firstNewShotId };
  }
}
function openFirstShot() {
  if (appliedHint.value?.firstShotId) {
    const sh = segments.value.flatMap(s => s.shots).find(s => s.shot_id === appliedHint.value!.firstShotId);
    if (sh) openWorkbench(sh);
  }
  appliedHint.value = null;
}

// ---- AI 制作规划（Phase AI-4A）----
function openPlanner() {
  if (!selected.value) return;
  if (!selected.value.shots.length) return;   // 无 Shot 禁用（按钮已 disabled，双保险）
  showPlanner.value = true;
}
function onPlannerApproved() {
  // 注意：approve 不改 Shot（opening_source 仍默认 independent），无需 reload。
  // 正式 opening_source 由 AI-4B Executor 执行时生效。
  showPlanner.value = false;
}

// ---- 自动制作（Phase AI-4B）----
function openAutopilot() {
  if (!selected.value) return;
  if (!selected.value.shots.length) return;
  showAutopilot.value = true;
}
async function onAutopilotClose() {
  showAutopilot.value = false;
  // Run 可能已生成新首帧/尾帧/视频 -> 刷新镜头列表展示
  await load();
}

// Phase AI-5A：完整影片自动制作（项目级）
function openProduction() {
  showProduction.value = true;
}

// Phase AI-5B：AI 完整制作（缺分镜/制作规划自动补齐 -> 完整影片）
function openFullProduction() {
  showFullProduction.value = true;
}
async function onFullProductionClose() {
  showFullProduction.value = false;
  // Run 可能已自动补齐 Shot / 生成视频 -> 刷新镜头列表展示
  await load();
}

// Phase 6A：镜头接力标识（开场来源）。仅展示，不打开工作台也能看懂关系。
function connLabel(sh: ShotData): string {
  if (sh.opening_source === 'inherit_previous') return '末帧接力';
  if (sh.opening_source === 'reference_previous') return '参考接续';
  return '独立';
}
function connClass(sh: ShotData): string {
  if (sh.opening_source === 'inherit_previous') return 'inherit';
  if (sh.opening_source === 'reference_previous') return 'ref';
  return 'none';
}

function previewImage(url: string) { if (!url) return; previewType.value = 'image'; previewUrl.value = url; }
function previewVideo(url: string) { if (!url) return; previewType.value = 'video'; previewUrl.value = url; }

async function addShot() {
  if (!selected.value || !projectName.value) return;
  if (storyboardLocked.value) return;   // Agent 修改分镜期间只读
  try {
    await apiClient.addShot(projectName.value, selected.value.scene_id);
    await load();
  } catch (e: any) {
    console.error('[ShotBoard] 添加镜头失败', e);
  }
}

// ---------- 镜头右键菜单（删除镜头）----------
const ctxMenu = ref<{ x: number; y: number; shot: ShotData } | null>(null);
const ctxMenuStyle = computed(() => {
  const m = ctxMenu.value; if (!m) return {};
  const w = 168, h = 104;
  return {
    top: Math.min(m.y, (window.innerHeight || 9999) - h) + 'px',
    left: Math.min(m.x, (window.innerWidth || 9999) - w) + 'px',
  };
});
function openCtxMenu(e: MouseEvent, sh: ShotData) {
  ctxMenu.value = { x: e.clientX, y: e.clientY, shot: sh };
}
function closeCtxMenu() { ctxMenu.value = null; }
async function confirmDeleteShot() {
  const sh = ctxMenu.value?.shot;
  closeCtxMenu();
  if (!sh || !projectName.value) return;
  if (storyboardLocked.value) return;   // Agent 修改分镜期间只读
  const hasArtifacts = sh.video?.local
    || (sh.first_frame?.candidates?.length ?? 0) > 0
    || (sh.last_frame?.candidates?.length ?? 0) > 0;
  const msg = hasArtifacts
    ? `确认删除镜头 ${sh.shot_id}？\n该镜头的首帧/尾帧/视频等产物文件会一并删除，无法恢复。`
    : `确认删除镜头 ${sh.shot_id}？`;
  if (!window.confirm(msg)) return;
  try {
    await apiClient.deleteShot(projectName.value, sh.shot_id);
    await load();
  } catch (e: any) {
    console.error('[ShotBoard] 删除镜头失败', e);
  }
}

// shot 级 job 完成 -> debounce 刷新（保持卡片/工作台与服务端同步）
let reloadTimer: number | null = null;
watch(() => jobStore.jobs.map(j => j.job_id + j.state).join('|'), () => {
  const finished = jobStore.jobs.some(j => (j.state === 'done' || j.state === 'failed') && j.shot_id);
  if (finished) {
    if (reloadTimer) clearTimeout(reloadTimer);
    reloadTimer = window.setTimeout(() => { load(); reloadTimer = null; }, 800);
  }
});

// ---- 2026-08-31：Agent 修改分镜时的实时刷新 + 只读锁 ----
// 复用 app 级 Project SSE（MainLayout 挂载 useProjectEvents 喂 store），不另起轮询：
//   resource.locked/unlocked（storyboard）→ 页面只读/恢复，锁定与解锁瞬间各 load() 一次
//   同步基线与最终态；storyboard.changed 且仍在锁定期 → 静默 load() 拉 Agent 落盘的
//   最新 canonical（锁定期用户不可编辑，覆盖本地安全）。锁释放覆盖完成/失败/停止三种
//   结局（后端 turn finally 统一 unlock），前端无需区分。另有 4s resource-locks 对账兜底。
const pev = useProjectEventsStore();
const storyboardLocked = computed(() => pev.storyboardLocked);
watch(storyboardLocked, (locked) => {
  if (!projectName.value) return;
  load();
  if (locked) {
    workbenchShot.value = null;   // 锁定期关闭编辑工作台（只读）
    closeCtxMenu();
  }
});
watch(() => pev.storyboardVersion, () => {
  if (projectName.value && storyboardLocked.value) load();
});

function onDocClick() { closeCtxMenu(); closeMenus(); }
function onKey(e: KeyboardEvent) { if (e.key === 'Escape') closeCtxMenu(); }
onMounted(() => {
  load();
  document.addEventListener('click', onDocClick);
  document.addEventListener('keydown', onKey);
});
onUnmounted(() => {
  document.removeEventListener('click', onDocClick);
  document.removeEventListener('keydown', onKey);
});
watch(projectName, () => { selected.value = null; load(); });
// AI-6C：切故事段时清空 shot 级 selection；AI-6D：并恢复该故事段的 active Proposal
watch(selected, () => { selectedShotId.value = null; segHeadOpen.value = false; closeMenus(); restoreProposal(); });
</script>

<style lang="scss" scoped>
.shot-board { height: calc(100vh - 88px); }
.empty {
  height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 14px; color: var(--text-muted); font-size: 14px;
}
.go-btn {
  padding: 8px 18px; border-radius: 6px; border: none; background: var(--accent);
  color: var(--accent-text); font-size: 13px; cursor: pointer; &:hover { background: var(--accent-hover); }
}

.board { display: flex; gap: 16px; height: 100%; position: relative; }

.expand-fab {
  position: absolute; top: 10px; left: 10px; z-index: 10;
  display: flex; align-items: center; gap: 4px; padding: 5px 10px 5px 8px;
  border-radius: 6px; border: 1px solid var(--border); background: var(--bg-surface);
  color: var(--text-muted); font-size: 12px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-secondary); }
}

.seg-list {
  width: 220px; flex-shrink: 0; overflow: hidden; background: var(--bg-surface);
  border: 1px solid var(--border); border-radius: 8px; padding: 6px;
  display: flex; flex-direction: column;
  transition: width 0.2s ease, border-color 0.2s ease, padding 0.2s ease;
  &.collapsed { width: 0; border: none; padding: 0; background: transparent; }
}
.seg-list-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 4px 8px 8px; flex-shrink: 0;
}
.seg-list-title { font-size: 12px; font-weight: 600; color: var(--text-muted); }
.seg-list-content { flex: 1; overflow-y: auto; }
.icon-btn {
  width: 24px; height: 24px; border-radius: 4px; border: none; background: none;
  color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center;
  &:hover { background: var(--bg-hover); color: var(--text-secondary); }
}
.seg-item {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px; border-radius: 6px; cursor: pointer; border-left: 2px solid transparent;
  &:hover { background: var(--bg-hover); }
  &.active { background: var(--bg-hover); border-left-color: var(--accent); }
}
.seg-thumb { width: 56px; height: 32px; flex-shrink: 0; object-fit: cover; border-radius: 4px; background: var(--bg-root); border: 1px solid var(--border); }
.seg-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.seg-row1 { display: flex; align-items: center; gap: 8px; }
.seg-seq { font-size: 11px; color: var(--text-faint); }
.seg-title { font-size: 13px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.seg-row2 { display: flex; align-items: center; }
.seg-count { font-size: 11px; color: var(--text-muted); }
.seg-list-foot {
  flex-shrink: 0; display: flex; flex-direction: column; gap: 6px;
  border-top: 1px solid var(--border); padding: 8px 2px 2px;
  .ai-full-btn { width: 100%; }
}

.seg-detail { flex: 1; min-width: 0; overflow-y: auto; }
/* Agent 修改分镜期只读横幅（与 script-editor/characters 页的 lock 提示同风格） */
.agent-lock-banner {
  display: flex; align-items: center; gap: 8px;
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: 6px; padding: 8px 12px; margin-bottom: 12px;
  font-size: 12px; color: var(--text-muted);
}
.ai-full-btn {
  padding: 6px 16px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--bg-surface); color: var(--text-secondary); font-size: 13px; cursor: pointer;
  &:hover { background: var(--bg-hover, var(--bg-surface)); }
}
.ai-full-btn.primary {
  border: none; background: var(--accent); color: var(--accent-text, #fff);
  &:hover { background: var(--accent-hover); }
}
.seg-head { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; margin-bottom: 14px; }
.seg-head-top { display: flex; align-items: center; gap: 10px; }
.seg-head-title { font-size: 13px; color: var(--text-secondary); max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.story-toggle svg { transition: transform 0.15s ease; }
.story-toggle.open svg { transform: rotate(180deg); }
.seg-head-actions { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.seg-head-body { margin-top: 10px; }
.seg-no { font-size: 13px; font-weight: 600; color: var(--text-primary); flex-shrink: 0; }
.ai-direct-btn {
  padding: 4px 12px; border-radius: 6px; border: 1px solid var(--accent);
  background: transparent; color: var(--accent); font-size: 12px; cursor: pointer;
  &:hover:not(:disabled) { background: var(--accent); color: var(--accent-text); }
  &:disabled { opacity: 0.4; cursor: not-allowed; border-color: var(--border); color: var(--text-muted); }
}
.link-btn { background: none; border: none; color: var(--text-muted); font-size: 12px; cursor: pointer; &:hover { color: var(--accent); } }
/* 故事段头「制作▾」「⋯」下拉菜单（沿用系统卡片/右键菜单风格） */
.menu-wrap { position: relative; }
.menu-trigger { display: inline-flex; align-items: center; gap: 4px; }
.more-btn { width: 26px; height: 26px; }
.menu-pop {
  position: absolute; right: 0; top: calc(100% + 6px); z-index: 100; min-width: 176px;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px;
  box-shadow: 0 6px 20px rgba(0,0,0,0.25); padding: 4px;
}
.menu-item {
  display: block; width: 100%; text-align: left; padding: 7px 10px;
  border: none; background: none; color: var(--text-secondary); font-size: 12px;
  border-radius: 4px; cursor: pointer;
  &:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
}
.menu-sep { height: 1px; background: var(--border); margin: 4px 2px; }
.applied-hint {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px;
  padding: 8px 12px; margin-bottom: 12px; font-size: 12px; color: var(--text-secondary);
  .link-btn { color: var(--accent); margin-left: auto; }
}
/* AI-6D.1 Director 修改提案 触发条（revision-layer 感：accent 左边框）+ apply 后提示 */
.propose-bar {
  display: flex; flex-direction: column; gap: 6px;
  background: var(--bg-surface); border: 1px solid var(--border); border-left: 3px solid var(--accent);
  border-radius: 8px; padding: 9px 12px; margin-bottom: 12px;
  &.busy { opacity: .85; }
  textarea {
    width: 100%; resize: vertical; box-sizing: border-box;
    background: var(--bg-root); border: 1px solid var(--border); border-radius: 5px;
    color: var(--text-primary); font-size: 12px; padding: 6px 8px; font-family: inherit;
    &:focus { border-color: var(--border-hover); outline: none; }
    &:disabled { opacity: .6; }
  }
}
.propose-bar-scope { font-size: 10px; color: var(--text-faint); }
.propose-bar-actions { display: flex; align-items: center; gap: 10px; justify-content: flex-end; }
.propose-error { font-size: 11px; color: var(--error); }
.applied-proposal-banner {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 6px;
  padding: 7px 12px; margin-bottom: 12px; font-size: 12px;
  .apb-ok { color: var(--text-primary); font-weight: 600; }
  .apb-sub { color: var(--text-muted); font-size: 11px; }
  .link-btn { color: var(--accent); }
  .link-btn:last-child { margin-left: auto; }
}
.seg-story { font-size: 13px; color: var(--text-secondary); line-height: 1.6; margin: 0 0 10px; }
.seg-chars { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.seg-chars-label { font-size: 11px; color: var(--text-muted); }
.seg-char { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: var(--bg-hover); color: var(--text-secondary);
  &.miss { background: transparent; border: 1px dashed var(--border); color: var(--text-faint); } }
.muted { font-size: 11px; color: var(--text-faint); }

.shot-list { display: flex; flex-direction: column; gap: 10px; }
.shot-item {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px;
  &:hover { border-color: var(--text-muted); }
  &.active { border-color: var(--accent); background: var(--bg-hover); }
}
.shot-cont { font-size: 10px; padding: 1px 7px; border-radius: 8px; border: 1px solid var(--border); white-space: nowrap; color: var(--text-muted); }
.shot-meta { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.shot-seq { font-size: 12px; font-weight: 600; color: var(--text-primary); font-family: monospace; }
.shot-size { font-size: 11px; color: var(--text-muted); padding: 1px 6px; border-radius: 3px; background: var(--bg-hover); }
.shot-desc { font-size: 12px; color: var(--text-secondary); flex: 1; min-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.shot-chars { font-size: 11px; color: var(--text-faint); }
.shot-status { font-size: 11px; padding: 1px 6px; border-radius: 3px;
  &.done { background: var(--bg-hover); color: var(--text-secondary); }
  &.failed { background: rgba(248,113,113,0.15); color: #f87171; }
  &.busy { background: var(--bg-hover); color: var(--text-muted); }
  &.pending { background: var(--bg-hover); color: var(--text-muted); }
}
.open-btn {
  margin-left: auto; padding: 4px 12px; border-radius: 5px; border: 1px solid var(--accent);
  background: transparent; color: var(--accent); font-size: 12px; cursor: pointer;
  &:hover { background: var(--accent); color: var(--accent-text); }
}

.shot-previews { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }
.preview-box {
  position: relative; aspect-ratio: 16/9; background: var(--bg-root); border: 1px solid var(--border);
  border-radius: 5px; overflow: hidden; cursor: pointer; display: flex; align-items: center; justify-content: center;
  &:hover { border-color: var(--accent); }
  &.disabled { cursor: default; &:hover { border-color: var(--border); } }
  &.na .ph { color: var(--text-faint); }
  img, video { width: 100%; height: 100%; object-fit: cover; }
  .ph { font-size: 11px; color: var(--text-faint); }
}
.add-shot-btn {
  margin-top: 4px; padding: 9px; border-radius: 6px; border: 1px dashed var(--border);
  background: transparent; color: var(--text-muted); font-size: 13px; cursor: pointer;
  &:hover { border-color: var(--accent); color: var(--accent); }
}

.preview-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.85); z-index: 2000;
  display: flex; align-items: center; justify-content: center; cursor: zoom-out;
  .big-img { max-width: 92vw; max-height: 92vh; object-fit: contain; }
  .big-video { max-width: 92vw; max-height: 92vh; }
}

.ctx-menu {
  position: fixed; z-index: 3000; min-width: 140px;
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: 6px; box-shadow: 0 6px 20px rgba(0,0,0,0.25); padding: 4px;
}
.ctx-shot-id { font-size: 11px; color: var(--text-faint); padding: 4px 10px; font-family: monospace; }
.ctx-item {
  display: block; width: 100%; text-align: left; padding: 7px 10px;
  border: none; background: none; color: var(--text-secondary); font-size: 13px;
  border-radius: 4px; cursor: pointer;
  &:hover { background: var(--bg-hover); color: var(--text-primary); }
  &.danger { color: #f87171; &:hover { background: rgba(248,113,113,0.12); color: #f87171; } }
}
</style>
