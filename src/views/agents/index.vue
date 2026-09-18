<template>
  <div class="agent-center">
    <div class="toolbar">
      <h2>AI 团队 · Agent Center</h2>
      <span class="hint">当前项目：{{ projectName || '未选择' }}</span>
      <!-- AI-7D：变更会话状态 chip（点击开审查弹窗；blocked/ready/awaiting/approved 自动弹出）。
           之前审查卡内联在对话流且 blocked 态无拒绝按钮，会话卡死无逃生口。 -->
      <button v-if="cs.session && !cs.noAction" class="cs-chip" :class="'csst-' + cs.session.status"
              :title="cs.session.error || ''" @click="showReviewModal = true">
        变更方案 · {{ csStatusText }}
      </button>
      <!-- AI-7D-R · Script Writer 最小测试弹窗（隔离，单工具，不走审查流）-->
      <button class="swt-btn" @click="showSwTestModal = true">🧪 Script Writer 直接编辑测试</button>
      <button class="swt-btn" @click="showDirTestModal = true">🎬 Director 分镜测试</button>
    </div>

    <div class="body">
      <!-- 左：Team Chat 入口 + 8 个 Agent 卡 -->
      <aside class="agent-list">
        <div class="agent-card team-entry" :class="{ active: selectedId === '__team__' }"
             @click="selectTeam">
          <div class="agent-name">Team Chat</div>
          <div class="agent-role">项目级群聊 · Producer</div>
        </div>
        <div v-for="a in agents" :key="a.id"
             class="agent-card" :class="{ active: selectedId === a.id, disabled: !a.enabled }"
             @click="selectAgent(a.id)">
          <div class="agent-name">{{ a.name }}</div>
          <div class="agent-role">{{ a.role }}</div>
          <div class="agent-meta">
            <span class="profile">{{ a.model_profile?.name || '—' }}</span>
            <span class="dot" :class="{ on: a.enabled }"></span>
          </div>
        </div>
      </aside>

      <!-- 右：Team Chat 或 聊天/委派 -->
      <section v-if="selectedId === '__team__'" class="panel team-panel">
        <TeamChatPanel />
      </section>

      <!-- Producer V1 Phase 3 · Production Room：群聊式协作投影（Producer 统一入口；
           专业 Agent 的 Direct Chat 仍在左侧列表独立可用）。 -->
      <section class="panel proom-panel" v-else-if="selectedId === 'producer'">
        <ProductionRoom />
      </section>

      <section class="panel" v-else-if="selected">
        <div class="panel-head">
          <div>
            <span class="p-title">{{ selected.name }}</span>
            <span class="p-desc">{{ selected.description }}</span>
          </div>
          <div class="scope-row">
            <select v-model="scopeType" class="input">
              <option value="project">project</option>
              <option value="scene">scene</option>
              <option value="shot">shot</option>
            </select>
            <input v-model="scopeId" class="input scope-id" :placeholder="scopePlaceholder" />
            <button class="btn btn-ghost" @click="openSession">切换会话</button>
          </div>
        </div>

        <!-- （Producer V1 Phase 3）旧 AI-6B 手动委派表单已由 Production Room 取代：
             真实协作走 *.delegate tool-loop（群聊可见），不再走手工表单路径。 -->

        <!-- 聊天区 -->
        <div class="sub-title">Conversation（独立隔离）</div>
        <div class="chat">
          <div class="messages" ref="messagesEl">
            <div v-if="!session || !session.messages.length" class="empty">暂无消息。在下方输入开始对话。</div>
            <div v-for="(m, i) in session?.messages || []" :key="i"
                 class="msg" :class="m.role">
              <span class="msg-role">{{ m.role }}</span>
              <!-- AI-7D-R：完成后执行过程折叠保留——置于正文【上方】（先有工具执行后有回复，
                   与 ChatGPT/Claude 的 thinking/工具折叠位置惯例一致） -->
              <div v-if="m.role === 'assistant' && processItemsOf(m).length" class="process-toggle"
                   @click="toggleProcessOf(m)">
                <span class="chevron" :class="{ open: processExpandedOf(m) }">▸</span>
                执行过程 · 完成 {{ completedStepCount(processItemsOf(m)) }} 步
              </div>
              <div v-if="m.role === 'assistant' && processExpandedOf(m) && processItemsOf(m).length"
                   class="process-timeline">
                <div v-for="(p, pi) in processItemsOf(m)" :key="pi" class="process-item" :class="p.kind">
                  <template v-if="p.kind === 'reasoning' && p.reasoningText">
                    <div class="pi-reasoning-head">思考过程<span v-if="p.reasoningMs"> · {{ (p.reasoningMs / 1000).toFixed(1) }}s</span></div>
                    <div class="pi-reasoning-text">{{ p.reasoningText }}</div>
                  </template>
                  <template v-else-if="p.kind === 'tool'">
                    <div class="pi-tool-head">
                      <span class="pi-mark" :class="p.status">{{ p.status === 'done' ? '✓' : p.status === 'failed' ? '!' : '◌' }}</span>
                      <span class="pi-label">{{ p.label }}</span>
                    </div>
                    <div v-if="p.summary" class="pi-summary">{{ p.summary }}</div>
                    <div v-if="p.error" class="pi-error">! {{ p.error }}</div>
                  </template>
                </div>
              </div>
              <span class="msg-content">{{ m.content }}</span>
              <span v-if="m.status" class="msg-status" :class="'ms-' + m.status">{{ m.status }}</span>
              <!-- AI-7D-R §十五：本轮改动（只信 meta.receipts，不解析正文） -->
              <div v-if="realReceipts(m.meta).length" class="receipts">
                <div class="receipts-head">本轮改动</div>
                <div v-for="(r, ri) in realReceipts(m.meta)" :key="ri" class="receipt-item">✓ {{ receiptLabel(r) }}</div>
              </div>
              <!-- 2026-08-30：本轮真实生成的角色三视图（图片不进模型上下文，窗口渲染给用户） -->
              <div v-for="grp in groupGeneratedViews(m.meta)" :key="'gv-' + i + grp.name"
                   class="gen-views">
                <div class="gv-name">{{ grp.name }} · 三视图</div>
                <div class="gv-grid">
                  <img v-for="v in grp.items" :key="v.view" class="gv-img"
                       :src="getImageUrl(v.path)" :alt="grp.name + ' ' + v.view"
                       loading="lazy" />
                </div>
              </div>
              <!-- 2026-08-31：Director 本轮真实生成的分镜图/视频（meta.generated_assets，
                   canonical 路径渲染；视频用 <video> 预览，不展示 base64） -->
              <div v-if="generatedAssets(m.meta).length" class="gen-views">
                <div class="gv-name">分镜视觉资产 · {{ generatedAssets(m.meta).length }} 项</div>
                <div class="gv-grid">
                  <template v-for="(a, ai) in generatedAssets(m.meta)" :key="'ga-' + i + ai">
                    <video v-if="a.kind === 'video'" class="gv-img gv-video"
                           :src="getVideoUrl(a.path)" controls preload="metadata"
                           :title="a.shot_id + ' 视频'" />
                    <img v-else class="gv-img gv-land" :src="getImageUrl(a.path)"
                         :alt="a.shot_id + ' ' + a.kind" loading="lazy"
                         :title="a.shot_id + ' ' + assetKindLabel(a.kind)" />
                  </template>
                </div>
              </div>
              <!-- Web Search：资料来源折叠区（仅 title+url 链接，来自 meta.search_sources；不展示 snippet） -->
              <div v-if="searchSources(m.meta).length" class="sources">
                <div class="sources-head" @click="toggleSourcesOf(m)">
                  <span class="chevron" :class="{ open: sourcesExpandedOf(m) }">▸</span>
                  资料来源 · {{ searchSources(m.meta).length }}
                </div>
                <div v-if="sourcesExpandedOf(m)" class="sources-list">
                  <a v-for="(s, si) in searchSources(m.meta)" :key="si" class="source-item"
                     :href="s.url" target="_blank" rel="noopener noreferrer">{{ s.title || s.url }}</a>
                </div>
              </div>
            </div>
            <!-- AI-7D-R：流式执行过程（当前 turn）。状态归 app store，切走页面不丢；
                 仅当活跃 turn 属于当前会话时显示。 -->
            <div v-if="turn.running && isStreamed && turn.activeSessionId === session?.session_id"
                 class="stream-area">
              <div class="stream-head">
                <div v-if="currentActivity" class="activity">
                  <span class="spinner">◌</span>{{ currentActivity.label }}
                </div>
                <button class="btn btn-ghost stop-btn" @click="turn.cancelTurn()" title="在步骤边界安全停止，已完成的修改保留">
                  ■ 停止
                </button>
              </div>
              <!-- 折叠入口与 timeline 置于流式正文【上方】，与完成后消息内位置一致（完成瞬间不跳位） -->
              <div v-if="processItems.length" class="process-toggle" @click="onToggleProcess">
                <span class="chevron" :class="{ open: processExpanded }">▸</span>
                执行过程 · 已完成 {{ completedSteps }} 步
              </div>
              <div v-if="processItems.length && processExpanded" class="process-timeline">
                <div v-for="(p, pi) in processItems" :key="pi" class="process-item" :class="p.kind">
                  <template v-if="p.kind === 'reasoning' && p.reasoningText">
                    <div class="pi-reasoning-head">思考过程<span v-if="p.reasoningMs"> · {{ (p.reasoningMs / 1000).toFixed(1) }}s</span></div>
                    <div class="pi-reasoning-text">{{ p.reasoningText }}</div>
                  </template>
                  <template v-else-if="p.kind === 'tool'">
                    <div class="pi-tool-head">
                      <span class="pi-mark" :class="p.status">{{ p.status === 'done' ? '✓' : p.status === 'failed' ? '!' : '◌' }}</span>
                      <span class="pi-label">{{ p.label }}</span>
                    </div>
                    <div v-if="p.summary" class="pi-summary">{{ p.summary }}</div>
                    <div v-if="p.error" class="pi-error">! {{ p.error }}</div>
                  </template>
                </div>
              </div>
              <div v-if="assistantStreaming" class="msg assistant streaming">
                <span class="msg-content">{{ assistantStreaming }}</span>
              </div>
            </div>
            <!-- AI-7D：变更会话审查移到 toolbar 弹窗（showReviewModal），不再内联在对话流。
                 queue/restart 二选一仍内联（执行中 + 新请求时）。 -->
            <div v-if="cs.pendingChangeChoice" class="change-choice">
              <div class="cc-q">当前计划正在执行，新修改请求如何处理？</div>
              <div class="cc-opts">
                <button class="btn btn-ghost" :disabled="sending" @click="onResolveChoice('queue')">让当前任务完成后处理</button>
                <button class="btn btn-ghost" :disabled="sending" @click="onResolveChoice('restart')">停止并重新规划</button>
              </div>
            </div>
          </div>
          <div class="composer">
            <!-- AI-7D-R §三：思考开关（发送瞬间 snapshot）。滑动 toggle，视觉非 checkbox。
                 2026-08-30：流式 Agent（Script Writer / Character Designer）通用。 -->
            <label v-if="isStreamed" class="thinking-toggle" :class="{ on: thinkingToggle }"
                   :title="thinkingToggle ? '思考已开启（本次发送起生效）' : '思考已关闭'">
              <input type="checkbox" v-model="thinkingToggle" class="tt-sr-only" role="switch"
                     :aria-checked="thinkingToggle" aria-label="思考模式" />
              <span class="tt-label">思考</span>
              <span class="tt-track"><span class="tt-thumb"></span></span>
            </label>
            <!-- 2026-08-29：模型滑动开关（仅 Script Writer）。状态存 Agent 设置
                 （model_profile_id），下一次对话起生效；默认 Agnes。 -->
            <div v-if="selectedId === 'script_writer'" class="model-switch"
                 title="模型切换（保存在 Agent 设置，下一次对话起生效）">
              <span class="tt-label">模型</span>
              <span class="ms-opt" :class="{ on: !modelUseSensenova }"
                    @click="setModelUseSensenova(false)">Agnes</span>
              <label class="thinking-toggle ms-track" :class="{ on: modelUseSensenova }">
                <input type="checkbox" v-model="modelUseSensenova" class="tt-sr-only" role="switch"
                       :aria-checked="modelUseSensenova" aria-label="模型切换" @change="onModelSwitchChange" />
                <span class="tt-track"><span class="tt-thumb"></span></span>
              </label>
              <span class="ms-opt" :class="{ on: modelUseSensenova }"
                    @click="setModelUseSensenova(true)">SenseNova</span>
            </div>
            <input v-model="draft" class="input"
                   :placeholder="composerPlaceholder"
                   @keyup.enter="send" :disabled="composerDisabled" />
            <button class="btn btn-primary" :disabled="composerDisabled || !draft.trim()" @click="send">
              {{ busy ? '…' : '发送' }}
            </button>
          </div>
          <div v-if="turn.running && isStreamed" class="lock-hint">
            ◌ Agent 正在后台处理，可切换到其它模块，完成后这里会自动显示结果
          </div>
          <div v-else-if="currentLocked && !turn.running" class="lock-hint">
            🔒 {{ selectedId === 'producer' ? 'Producer 委派的专业 Agent 正在修改项目，输入暂时锁定'
                : selectedId === 'character_designer' ? 'Agent 正在修改本项目角色，输入暂时锁定'
                : 'Agent 正在修改本项目剧本，输入暂时锁定' }}
          </div>
          <div v-if="chatError" class="chat-err">{{ chatError }}</div>
        </div>
      </section>

      <section v-else class="panel empty-panel">
        <div class="empty">← 从左侧选择一个 Agent</div>
      </section>
    </div>

    <!-- AI-7D：变更方案审查弹窗（ChangePlanReview 内含拒绝/批准/请求修改；blocked 也能拒绝） -->
    <Teleport to="body">
      <div v-if="showReviewModal && cs.session" class="cpr-modal-overlay"
           @click.self="showReviewModal = false">
        <div class="cpr-modal">
          <div class="cpr-modal-head">
            <span class="cpr-modal-title">变更方案审查</span>
            <button class="cpr-modal-x" @click="showReviewModal = false">✕</button>
          </div>
          <div class="cpr-modal-body">
            <ChangePlanReview />
          </div>
        </div>
      </div>
    </Teleport>

    <!-- AI-7D-R · Script Writer 最小测试弹窗 -->
    <ScriptWriterTestModal :open="showSwTestModal" @close="showSwTestModal = false" />
    <!-- AI-7D-R · Director 分镜测试弹窗 -->
    <DirectorTestModal :open="showDirTestModal" @close="showDirTestModal = false" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { apiClient } from '@/api';
import type { AgentInfo, AgentSession, ScriptWriterReceipt, ScriptWriterSearchSource } from '@/api';
import { useProjectStore } from '@/store';
import { useChangeSessionStore } from '@/store/changeSession';
import { useAgentTurnStore, type ProcessItem } from '@/store/agentTurn';
import { useProjectEventsStore } from '@/store/projectEvents';
import { useStickToBottom } from '@/composables/useStickToBottom';
import { getImageUrl, getVideoUrl } from '@/utils/media';
import TeamChatPanel from '@/components/agent/TeamChatPanel.vue';
import ChangePlanReview from '@/components/agent/ChangePlanReview.vue';
import ScriptWriterTestModal from '@/components/agent/ScriptWriterTestModal.vue';
import DirectorTestModal from '@/components/agent/DirectorTestModal.vue';
import ProductionRoom from '@/components/agent/ProductionRoom.vue';

// ============ AI-7D-R 流式 Direct Chat：turn 状态归 app 级 store（切页面不丢） ============
// 思考开关（发送瞬间 snapshot 固定，运行中切换不影响当前 turn）
const thinkingToggle = ref(false);
// ============ 模型滑动开关（2026-08-29）：Agnes（默认）/ SenseNova 6.8 Flash Lite ============
// 状态保存在 Agent 设置（model_profile_id: text_default | text_sensenova_68fl），
// 从下一次 Agent turn 起生效；运行中切换不影响当前 turn；Key A/B 热切换机制不受影响。
const MODEL_PROFILE_AGNES = 'text_default';
const MODEL_PROFILE_SENSENOVA = 'text_sensenova_68fl';
const modelUseSensenova = ref(false);
async function loadModelSwitch() {
  try {
    const s = await apiClient.getAgentSettings();
    modelUseSensenova.value =
      s?.agents?.script_writer?.model_profile_id === MODEL_PROFILE_SENSENOVA;
  } catch {
    modelUseSensenova.value = false;   // 读取失败按默认 Agnes
  }
}
async function onModelSwitchChange() {
  const want = modelUseSensenova.value;
  try {
    await apiClient.updateAgentSetting('script_writer',
      want ? MODEL_PROFILE_SENSENOVA : MODEL_PROFILE_AGNES);
  } catch (e: any) {
    modelUseSensenova.value = !want;   // 保存失败回滚 UI
    chatError.value = '模型切换失败: ' + (e.message || e);
  }
}
async function setModelUseSensenova(v: boolean) {
  if (modelUseSensenova.value === v) return;
  modelUseSensenova.value = v;
  await onModelSwitchChange();
}
// turn 流式连接/累积状态由 store 持有：组件卸载（切到剧本等模块）不中断后台 Agent，
// 切回来时执行过程/流式正文/最终结果都还在。
const turn = useAgentTurnStore();
const pev = useProjectEventsStore();
// 模板别名（store 的 processItems 是当前 turn 的执行过程 timeline）
const processItems = computed(() => turn.processItems);
const processExpanded = computed(() => turn.processExpanded);
const currentActivity = computed(() => turn.currentActivity);
const assistantStreaming = computed(() => turn.streamingText);
const completedSteps = computed(() => turn.completedSteps);
function onToggleProcess() { turn.toggleProcess(); }
// AI-7D-R：具备直接编辑工具、走流式 tool-loop 的 Agent（2026-08-30 加入 Character Designer、Director；
// 2026-09-04 Producer V1 Phase 1 加入——Producer 经 *.delegate 串行驱动 SW/CD/Director，多 Agent
// 委派分钟级，必须走 /turn/stream（后台续跑 + SSE 时间线），非流式 HTTP 会超时）。
const STREAMED_AGENTS = new Set(['script_writer', 'character_designer', 'scene_designer', 'director', 'producer']);
const isStreamed = computed(() => STREAMED_AGENTS.has(selectedId.value));
// 输入锁定按 Agent 写入域区分：Script Writer 写剧本域（story 锁），Character Designer 写角色域
// （character 锁），Scene Agent 写场景系统域（scene_system 锁，Phase C2），Director 写分镜域
// （storyboard 锁）。Producer 自身不锁资源，但委派的任一 Specialist 会锁其域——故 Producer
// 四域任一锁定即提示（忙时由 turn.running 兜底禁输入）。
const currentLocked = computed(() =>
  selectedId.value === 'character_designer' ? pev.charactersLocked
  : selectedId.value === 'scene_designer' ? pev.sceneSystemLocked
  : selectedId.value === 'director' ? pev.storyboardLocked
  : selectedId.value === 'producer' ? (pev.storyLocked || pev.charactersLocked || pev.sceneSystemLocked || pev.storyboardLocked)
  : pev.storyLocked);
// 工作中 = 本地 legacy 路径忙，或流式 turn 在跑（含后台续跑）
const busy = computed(() => sending.value || turn.running);
// 输入禁用：本地忙 / 流式 turn 在跑 / 本 Agent 写入域被 Agent 锁（含其它客户端）
const composerDisabled = computed(() => busy.value || currentLocked.value);
const composerPlaceholder = computed(() =>
  currentLocked.value
    ? (selectedId.value === 'producer'
        ? '🔒 Producer 委派的专业 Agent 正在工作，请稍候…'
        : selectedId.value === 'character_designer'
        ? '🔒 Agent 正在修改角色，请稍候…'
        : selectedId.value === 'scene_designer'
        ? '🔒 Scene Agent 正在准备场景系统，请稍候…'
        : selectedId.value === 'director'
          ? '🔒 Agent 正在修改分镜，请稍候…'
          : '🔒 Agent 正在修改剧本，请稍候…')
    : '输入消息…');

// receipts → 「本轮改动」轻量渲染（只信结构化 receipts，不解析正文）
function receiptLabel(r: ScriptWriterReceipt): string {
  const op = r.operation || '';
  // Character Designer 回执（操作对象是 character_name）
  if (op === 'character.create') return `创建角色 ${r.character_name || ''}`.trim();
  if (op === 'character.update') return `修改角色 ${r.character_name || ''}`.trim();
  if (op === 'character.delete') return `删除角色 ${r.character_name || ''}`.trim();
  if (op === 'character.generate_views') {
    const n = r.generated?.length || 0;
    const total = r.character_names?.length || 0;
    return `生成三视图（${n}/${total} 个角色完成）`;
  }
  // Director 回执（操作对象是 shot_id；批量看 count）
  if (op === 'shot.create') return `创建镜头 ${r.shot_id || ''}`.trim();
  if (op === 'shot.update') return `修改镜头 ${r.shot_id || ''}`.trim();
  if (op === 'shot.delete') return `删除镜头 ${r.shot_id || ''}`.trim();
  if (op === 'shot.move') return `移动镜头 ${r.shot_id || ''}`.trim();
  if (op === 'shot.create_many') return `批量创建镜头（${r.count ?? r.created?.length ?? 0} 个）`;
  if (op === 'shot.update_many') return `批量修改镜头（${r.count ?? r.results?.length ?? 0} 个）`;
  // Director Visual Production（2026-08-31）：视觉生成分项结果
  if (op === 'shot.generate_images') {
    const done = (r.results || []).filter((x: any) => x.ok).length;
    return `生成分镜图（${done}/${r.count ?? r.results?.length ?? 0} 完成）`;
  }
  if (op === 'shot.generate_videos') {
    const done = (r.results || []).filter((x: any) => x.ok).length;
    return `生成镜头视频（${done}/${r.count ?? r.results?.length ?? 0} 完成）`;
  }
  if (op === 'story.create') return `创建 ${r.scene_id || ''}`.trim();
  if (op === 'story.move') return `移动 ${r.scene_id || ''}`.trim();
  if (op === 'story.update') return `修改 ${r.scene_id || ''}`.trim();
  if (op === 'story.delete') return `删除 ${r.scene_id || ''}`.trim();
  if (op === 'story.insert_many') return '批量插入故事段';
  if (op === 'story.delete_many') return '批量删除故事段';
  if (op === 'script.update') return '更新剧本正文';
  return op;
}
function realReceipts(meta: any): ScriptWriterReceipt[] {
  if (!meta || !Array.isArray(meta.receipts)) return [];
  return meta.receipts.filter((r: ScriptWriterReceipt) => r.ok && r.verified && !r.unchanged);
}

// Web Search：资料来源（meta.search_sources，仅 title+url，无 snippet）。默认折叠，点击展开。
function searchSources(meta: any): ScriptWriterSearchSource[] {
  if (!meta || !Array.isArray(meta.search_sources)) return [];
  return meta.search_sources.filter((s: ScriptWriterSearchSource) => s && (s.title || s.url));
}
function sourcesExpandedOf(m: any): boolean { return !!m._sourcesExpanded; }
function toggleSourcesOf(m: any) { m._sourcesExpanded = !m._sourcesExpanded; }

// 2026-08-30：本轮真实生成的角色三视图（meta.generated_views，local 路径）→ 图片网格。
// 按 character_name 分组，视图固定 front/side/back 顺序；图片不进模型上下文，仅窗口展示。
const VIEW_ORDER: Record<string, number> = { front: 0, side: 1, back: 2 };
function groupGeneratedViews(meta: any): { name: string; items: { view: string; path: string }[] }[] {
  const list: any[] = Array.isArray(meta?.generated_views) ? meta.generated_views : [];
  const byName = new Map<string, { view: string; path: string }[]>();
  for (const g of list) {
    if (!g || !g.character_name || !g.path) continue;
    if (!byName.has(g.character_name)) byName.set(g.character_name, []);
    byName.get(g.character_name)!.push({ view: g.view, path: g.path });
  }
  return [...byName.entries()].map(([name, items]) => ({
    name,
    items: items.sort((a, b) => (VIEW_ORDER[a.view] ?? 9) - (VIEW_ORDER[b.view] ?? 9)),
  }));
}

// 2026-08-31：Director 本轮真实生成的分镜图/视频（meta.generated_assets，canonical 路径）。
// 图片走 getImageUrl（/api/image/...），视频走 getVideoUrl（/projects/... 静态挂载）。
function generatedAssets(meta: any): { shot_id: string; kind: string; path: string }[] {
  const list: any[] = Array.isArray(meta?.generated_assets) ? meta.generated_assets : [];
  return list.filter((a: any) => a && a.shot_id && a.path);
}
function assetKindLabel(kind: string): string {
  return kind === 'last_frame' ? '尾帧' : kind === 'video' ? '视频' : '首帧';
}

// AI-7D-R：完成后执行过程附在 message 上保留（折叠态），事后可展开查看。
// reasoning 文本不持久化（spec §一），刷新后丢失。两处来源：
//   1) m._processItems：不切页面时 consumePending 即时挂到消息上；
//   2) turn.processItemsFor(message_id)：切页面回来消息由 openSession 从后端重载（
//      无 _processItems 前端字段），按 message_id 从 app 级 store 找回（路由切换不丢）。
function processItemsOf(m: any): ProcessItem[] {
  if (m._processItems && m._processItems.length) return m._processItems;
  return m.message_id ? turn.processItemsFor(m.message_id) : [];
}
function processExpandedOf(m: any): boolean { return !!m._processExpanded; }
function toggleProcessOf(m: any) { m._processExpanded = !m._processExpanded; }
function completedStepCount(items: ProcessItem[]): number {
  return items.filter(p => p.kind === 'tool' && p.status !== 'running').length;
}

const store = useProjectStore();
const projectName = computed(() => store.projectName);
const cs = useChangeSessionStore();
// AI-7D：变更方案审查弹窗。状态变 blocked/ready/awaiting/approved 时自动弹出（让用户立刻可见可操作）。
const showReviewModal = ref(false);
const showSwTestModal = ref(false);
const showDirTestModal = ref(false);
const csStatusText = computed(() => {
  const map: Record<string, string> = {
    routing: '路由中', consulting: '咨询中', analyzing_dependencies: '分析依赖',
    awaiting_user_decision: '需决策', synthesizing_plan: '综合计划', validating_plan: '校验中',
    building_preview: '构建预览', ready_for_review: '待审查', approved_for_execution: '已批准',
    blocked: '阻塞', rejected: '已拒绝', stale: '已过期', failed: '失败', cancelled: '已取消',
    no_action: '无需改动',
  };
  return map[cs.session?.status || ''] || cs.session?.status || '';
});
let lastNoActionSid = '';
watch(() => cs.session?.status, (s) => {
  // AI-7D-R：no_action 是诚实终态，不弹审查卡；其余需用户注意的状态才弹
  if (s && ['blocked', 'ready_for_review', 'awaiting_user_decision', 'approved_for_execution'].includes(s)) {
    showReviewModal.value = true;
  }
  // AI-7D-R §10：no_action 异步到达时把诚实结论补进本会话转录（去重）
  if (s === 'no_action' && cs.session && cs.session.session_id !== lastNoActionSid) {
    lastNoActionSid = cs.session.session_id;
    const reason = (cs.session as any).no_action_reason || '我核对了当前项目，这条请求无需产生实际修改。';
    session.value?.messages.push({
      role: 'assistant', content: '我核对了当前项目：' + reason,
      status: null, created_at: Date.now() / 1000,
    });
  }
  if (s && s !== 'no_action') lastNoActionSid = '';
});
// AI-7D D2：change 意图检测（与 agentUi.CHANGE_INTENT_RE 同词表）
const CHANGE_INTENT_RE = /改|修改|换成|变成|变得|变为|删除|删掉|去掉|调整|替换|重写|续写|换回|恢复到|恢复成|让.{1,20}更|加(强|快|深)|增强|弱化|放大|缩小|拓展|扩写|扩充|扩展|补充|补全|加场|加戏|make\b|change\b|remove\b|turn\b/i;

const agents = ref<AgentInfo[]>([]);
const selectedId = ref<string>('');
const selected = computed(() => agents.value.find(a => a.id === selectedId.value) || null);

const scopeType = ref('project');
const scopeId = ref('');
const session = ref<AgentSession | null>(null);
const draft = ref('');
const sending = ref(false);
const chatError = ref('');
const messagesEl = ref<HTMLElement | null>(null);
// 智能贴底：打开会话跳最新；新消息时仅当用户原本贴底才跟随（查看历史时不强拉）。
const stick = useStickToBottom(messagesEl);
// messages 面板随选中 agent 切换而挂载；挂载后绑定 scroll 监听并跳到最新消息。
watch(messagesEl, (el) => { if (el) { stick.bind(); stick.pinToBottom(); } });
// 外部异步到达的消息（如变更结论 no_action 补录）：仅在用户贴底时跟随，看历史时不强拉。
watch(() => session.value?.messages?.length, () => { stick.followIfStuck(); });

// （Producer V1 Phase 3）旧 AI-6B 手动委派表单的 tasks/delegForm/doDelegate 已随
// Production Room 上线移除；真实协作由 Producer 的 *.delegate tool-loop 承载。

const scopePlaceholder = computed(() =>
  scopeType.value === 'project' ? (projectName.value || 'project') :
  scopeType.value === 'scene' ? '如 s01' : '如 s01_c02');

async function loadAgents() {
  try {
    agents.value = await apiClient.listAgents();
    if (!selectedId.value && agents.value.length) selectAgent(agents.value[0].id);
  } catch (e: any) {
    chatError.value = '加载 Agent 失败: ' + (e.message || e);
  }
}

async function selectAgent(id: string) {
  selectedId.value = id;
  chatError.value = '';
  // Producer V1 Phase 3：Producer 面板 = Production Room（自管 session/快照/实时合并），
  // 不再走通用 openSession 聊天面板。
  if (id === 'producer') { session.value = null; return; }
  // AI-7D-R §9：Direct Conversation 默认长存于 project 级（不随页面 scope 重建/碎片化）。
  // 用户仍可用上方 scope 选择器显式切到 scene/shot 会话。
  scopeType.value = 'project';
  scopeId.value = projectName.value || '';
  session.value = null;
  if (id === 'script_writer') loadModelSwitch();   // 模型开关初值跟随 Agent 设置
  await openSession();
}

function selectTeam() {
  selectedId.value = '__team__';
  chatError.value = '';
}

async function openSession() {
  if (!projectName.value) { chatError.value = '请先选择项目'; return; }
  try {
    const sid = scopeId.value || projectName.value;
    session.value = await apiClient.createAgentSession(projectName.value, selectedId.value, scopeType.value, sid);
    // 刷新/重载后执行过程折叠条会丢（_processItems 不持久化、pinia 清空）。
    // 后端 meta.trace（tool 轨迹）+ meta.reasonings（每步思考文本）都是落盘的，
    // 用它们重建折叠条（tool 步骤 + reasoning 思考内容交错）。流式 Agent 均带 meta。
    if (STREAMED_AGENTS.has(selectedId.value) && session.value?.messages) {
      for (const m of session.value.messages) {
        const meta = (m as any).meta;
        if (m.role === 'assistant' && (m as any).message_id
            && (meta?.trace?.length || meta?.reasonings?.length)) {
          turn.hydrateProcessItemsFromMeta((m as any).message_id, meta);
        }
      }
    }
    // 打开会话 → 无条件定位到最新消息。
    stick.pinToBottom();
  } catch (e: any) {
    chatError.value = '打开会话失败: ' + (e.message || e);
  }
}

async function send() {
  if (!session.value || !draft.value.trim() || sending.value) return;
  const content = draft.value.trim();
  draft.value = '';
  // 乐观追加 user
  session.value.messages.push({ role: 'user', content, status: null, created_at: Date.now() / 1000 });
  sending.value = true;
  chatError.value = '';
  try {
    // AI-7D D2：变更意图 → 真 ChangeSession 桥接（复用 csStore.bridgeFromDirectChat，与
    // Floating Bar 同语义）；否则只读/对话 → 统一 /turn runtime（D1 真 inspect 接地）。
    // AI-7D-R：script_writer / character_designer 具备直接编辑工具，跳过 ChangeSession
    // 方案审核，走 /turn/stream 由各自 tool-loop 完成修改（LLM→tool→observation→回复）。
    const DIRECT_EDIT_AGENTS = STREAMED_AGENTS;
    const isDirectEdit = DIRECT_EDIT_AGENTS.has(selectedId.value);
    if (CHANGE_INTENT_RE.test(content) && !isDirectEdit) {
      const msgs = await cs.bridgeFromDirectChat(content, {
        source: 'direct_agent_chat',
        sourceAgentId: selectedId.value,
        sourceSessionId: session.value.session_id,
      });
      session.value.messages.push(...msgs);
    } else if (isDirectEdit) {
      // AI-7D-R 流式：script_writer 走 /turn/stream，由 app 级 store 持有连接与状态
      // （切走页面不中断后台 Agent）。发送瞬间 snapshot 固定 thinking。
      const sid0 = session.value.session_id;
      await turn.runTurn({
        projectName: projectName.value,
        sessionId: sid0,
        content, thinking: thinkingToggle.value,
      });
      // 最终消息挂账在 store；取出追加到会话（附本轮执行过程，前端字段不持久化）
      consumePendingIntoSession();
    } else {
      const result = await apiClient.agentTurn(projectName.value, session.value.session_id, content);
      session.value.messages.push(result.message);   // assistant AgentMessage（含 meta）
    }
    // 用户主动发消息 → 视为贴底，跟随到最新。
    stick.pinToBottom();
  } catch (e: any) {
    chatError.value = '发送失败: ' + (e.message || e);
  } finally {
    sending.value = false;
  }
}

/**
 * 把 store 挂账的最终消息追加到当前会话（去重）。
 * 既在 send() await 后调用，也在组件 onMounted / turn 结束 watcher 里调用——
 * 覆盖"回复中途切走，完成后切回来"的场景。
 */
function consumePendingIntoSession() {
  const p = turn.consumePending();
  if (!p || !p.message) return;
  if (!session.value) return;
  const mid = p.message.message_id;
  const exists = mid && session.value.messages.some((m: any) => m.message_id === mid);
  if (!exists) {
    session.value.messages.push({ ...p.message, _processItems: p.processItems });
  }
  stick.pinToBottom();
}

// AI-7D D2：活跃执行 + 新变更请求的二选一（委托 csStore.resolveChangeChoice）
async function onResolveChoice(choice: 'queue' | 'restart') {
  if (sending.value) return;
  sending.value = true;
  chatError.value = '';
  try {
    const msgs = await cs.resolveChangeChoice(choice);
    session.value?.messages.push(...msgs);
    stick.pinToBottom();
  } catch (e: any) {
    chatError.value = '处理失败: ' + (e.message || e);
  } finally {
    sending.value = false;
  }
}

onMounted(async () => {
  loadAgents();
  loadModelSwitch();   // 模型开关初值（Agent 设置持久化，默认 Agnes）
  // AI-7D D2：恢复进行中的变更会话（F5 不丢真 ChangeSession 真相）
  if (projectName.value) { await cs.restoreActive(); }
  // AI-7D-R：若上一个 turn 在切走期间完成，把挂账最终消息补进会话
  consumePendingIntoSession();
});

// AI-7D-R：turn 由 app 级 store 持有，组件卸载【不取消】后台 Agent（任务继续跑、
// 消息照常持久化）。这里只在 turn 结束瞬间把挂账消息补进会话（页面可见时）。
watch(() => turn.running, (r, old) => {
  if (old && !r) consumePendingIntoSession();
});

onUnmounted(() => {
  // 无本地 turn 资源要清理：stream/abort 归 store（切走不断流）。
});
</script>

<style lang="scss" scoped>
.agent-center { max-width: 1200px; height: calc(100vh - 88px); display: flex; flex-direction: column; }
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;
  h2 { font-size: 16px; font-weight: 600; color: var(--text-primary); }
  .hint { font-size: 12px; color: var(--text-muted); } }
.body { flex: 1; display: flex; gap: 16px; min-height: 0; }

.agent-list { width: 220px; display: flex; flex-direction: column; gap: 8px; overflow-y: auto; padding-right: 4px; }
.agent-card { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; cursor: pointer; transition: all .15s;
  &:hover { border-color: var(--border-hover, var(--accent)); }
  &.active { border-color: var(--accent); background: var(--bg-hover); }
  &.disabled { opacity: .5; } }
.agent-card.team-entry { border-style: dashed; margin-bottom: 4px;
  .agent-role { color: var(--accent); } }
.team-panel { padding: 0; }
.agent-name { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.agent-role { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.agent-meta { display: flex; justify-content: space-between; align-items: center; margin-top: 6px; }
.profile { font-size: 10px; color: var(--text-muted); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #6b7280; }
.dot.on { background: #4ade80; }

.panel { flex: 1; display: flex; flex-direction: column; gap: 12px; min-width: 0; overflow-y: auto; }
.empty-panel { align-items: center; justify-content: center; }
.empty { color: var(--text-muted); font-size: 13px; padding: 24px; text-align: center; }
.panel-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap;
  .p-title { font-size: 14px; font-weight: 600; color: var(--text-primary); margin-right: 8px; }
  .p-desc { font-size: 12px; color: var(--text-muted); } }
.scope-row { display: flex; gap: 6px; align-items: center; }

.sub-title { font-size: 12px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: .5px; }
/* （Producer V1 Phase 3）旧委派表单样式块（delegation/task-table/task-row/状态色/form-row）
   已随 Production Room 取代手动委派而移除。 */

/* Producer V1 Phase 3 · Production Room 面板：内部自滚动（messages 区管滚动） */
.proom-panel { overflow: hidden; }

.input { background: var(--bg-root); border: 1px solid var(--border); border-radius: 4px; padding: 6px 8px; font-size: 12px; color: var(--text-primary); outline: none; }
.input:focus { border-color: var(--accent); }
.scope-id { flex: 1; min-width: 100px; }
.full { width: 100%; }

.chat { flex: 1; display: flex; flex-direction: column; min-height: 240px; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; }
.messages { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.msg { display: flex; flex-direction: column; gap: 2px; max-width: 80%; padding: 8px 10px; border-radius: 8px; font-size: 13px; }
.msg.user { align-self: flex-end; background: var(--accent, #3b82f6); color: #fff; }
.msg.assistant { align-self: flex-start; background: var(--bg-hover); color: var(--text-primary); }
.msg-role { font-size: 10px; opacity: .7; }
/* 复用 ScriptWriterTestModal 的正文渲染：保留换行、长词折行，不引入 v-html/markdown 解析。
   不修改消息正文，只修渲染。 */
.msg-content { white-space: pre-wrap; word-break: break-word; line-height: 1.55; }
.msg-status { font-size: 10px; margin-top: 2px; }
.ms-not_configured { color: #fbbf24; }
.ms-failed { color: #f87171; }
.composer { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--border); align-items: center; }
.composer .input { flex: 1; }
.chat-err { color: #f87171; font-size: 11px; padding: 0 10px 8px; }

/* AI-7D-R · 思考滑动 toggle（§二十五：克制视觉；纯样式，逻辑仍 checkbox+v-model） */
.thinking-toggle { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary, #9399b2); cursor: pointer; user-select: none; padding: 0 2px; }
.thinking-toggle.on { color: var(--text-primary, #cdd6f4); }
.tt-sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
.tt-track { position: relative; width: 30px; height: 16px; border-radius: 8px; background: rgba(255,255,255,.16); transition: background .18s ease; flex-shrink: 0; }
.thinking-toggle.on .tt-track { background: #60a5fa; }
.tt-thumb { position: absolute; top: 2px; left: 2px; width: 12px; height: 12px; border-radius: 50%; background: #fff; transition: transform .18s ease; }
.thinking-toggle.on .tt-thumb { transform: translateX(14px); }
.thinking-toggle:focus-within .tt-track { outline: 2px solid rgba(96,165,250,.4); outline-offset: 1px; }
/* 2026-08-29：模型滑动开关（Agnes / SenseNova，仅 Script Writer，存 Agent 设置） */
.model-switch { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary, #9399b2); user-select: none; padding: 0 2px; }
.ms-opt { cursor: pointer; padding: 0 1px; }
.ms-opt.on { color: var(--text-primary, #cdd6f4); font-weight: 600; }
.ms-track { padding: 0; }

/* AI-7D-R · 流式执行过程 UI */
.stream-area { margin: 6px 0; }
.stream-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.stop-btn { font-size: 11px; padding: 2px 10px; flex: none; color: var(--text-secondary, #f87171); }
.stop-btn:hover { color: #f87171; border-color: rgba(248,113,113,.4); }
.lock-hint { font-size: 11px; color: var(--text-secondary, #9399b2); padding: 4px 10px; }
.activity { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary, #9399b2); padding: 4px 2px; }
.spinner { display: inline-block; animation: sw-spin 1s linear infinite; }
@keyframes sw-spin { to { transform: rotate(360deg); } }
.msg.streaming { align-self: flex-start; background: rgba(255,255,255,.05); }
.process-toggle { font-size: 11px; color: var(--text-secondary, #9399b2); cursor: pointer; padding: 4px 2px; user-select: none; }
.process-toggle:hover { color: var(--text-primary, #cdd6f4); }
.chevron { display: inline-block; transition: transform .15s; }
.chevron.open { transform: rotate(90deg); }
.process-timeline { margin: 4px 0 8px; padding: 8px 10px; border-left: 2px solid var(--border, rgba(255,255,255,.08)); display: flex; flex-direction: column; gap: 10px; }
.process-item { font-size: 12px; }
.pi-reasoning-head { color: var(--text-secondary, #9399b2); font-size: 11px; margin-bottom: 2px; }
.pi-reasoning-text { white-space: pre-wrap; word-break: break-word; line-height: 1.5; color: var(--text-secondary, #9399b2); font-size: 12px; padding: 2px 0; }
.pi-tool-head { display: flex; align-items: center; gap: 6px; }
.pi-mark { font-size: 12px; }
.pi-mark.done { color: #4ade80; }
.pi-mark.failed { color: #f87171; }
.pi-mark.running { color: var(--text-secondary, #9399b2); }
.pi-label { color: var(--text-primary, #cdd6f4); }
.pi-summary { font-size: 11px; color: var(--text-secondary, #9399b2); padding-left: 18px; }
.pi-error { font-size: 11px; color: #f87171; padding-left: 18px; }

/* AI-7D-R · 本轮改动（receipts 结构化） */
.receipts { margin-top: 6px; padding: 6px 8px; border-left: 2px solid #4ade80; background: rgba(74,222,128,.06); border-radius: 0 6px 6px 0; }
.receipts-head { font-size: 11px; color: #4ade80; margin-bottom: 2px; }
.receipt-item { font-size: 11px; color: var(--text-secondary, #9399b2); }
/* 2026-08-30 · 本轮生成的角色三视图图片网格 */
.gen-views { margin-top: 6px; padding: 6px 8px; border-left: 2px solid #4ade80; background: rgba(74,222,128,.04); border-radius: 0 6px 6px 0; }
.gv-name { font-size: 11px; color: #4ade80; margin-bottom: 4px; }
.gv-grid { display: flex; gap: 6px; flex-wrap: wrap; }
.gv-img { width: 72px; height: 108px; object-fit: cover; border-radius: 4px; background: rgba(255,255,255,.06); border: 1px solid var(--border); cursor: pointer; }
.gv-img:hover { border-color: var(--accent); }
.gv-video { width: 128px; height: 72px; }
.gv-land { width: 128px; height: 72px; }   /* 分镜帧是 16:9，横版缩略避免裁切 */
/* Web Search 资料来源折叠区（与 receipts 同级同风格） */
.sources { margin-top: 6px; padding: 6px 8px; border-left: 2px solid #60a5fa; background: rgba(96,165,250,.06); border-radius: 0 6px 6px 0; }
.sources-head { font-size: 11px; color: #60a5fa; cursor: pointer; user-select: none; }
.sources-head:hover { color: #93c5fd; }
.sources-head .chevron { display: inline-block; transition: transform .15s; }
.sources-head .chevron.open { transform: rotate(90deg); }
.sources-list { display: flex; flex-direction: column; gap: 2px; margin-top: 4px; }
.source-item { font-size: 11px; color: #93c5fd; text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.source-item:hover { text-decoration: underline; }

.change-choice { margin: 8px 0; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-surface); }
.cc-q { font-size: 12px; color: var(--text-primary); margin-bottom: 6px; }
.cc-opts { display: flex; gap: 8px; flex-wrap: wrap; }

.btn { padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 500; cursor: pointer; border: none; transition: all .15s; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn-primary { background: var(--accent); color: var(--accent-text, #fff); }
.btn-primary:hover:not(:disabled) { filter: brightness(1.1); }
.btn-ghost { background: none; color: var(--text-muted); }
.btn-ghost:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-secondary); }

/* AI-7D：变更会话状态 chip（toolbar，点击开审查弹窗） */
.cs-chip { margin-left: auto; padding: 4px 12px; font-size: 12px; font-weight: 600; border-radius: 12px;
  border: 1px solid var(--border); background: var(--bg-surface); color: var(--text-secondary); cursor: pointer; transition: all .15s; }
.cs-chip:hover { background: var(--bg-hover); }
.csst-blocked, .csst-failed, .csst-stale, .csst-rejected, .csst-cancelled { border-color: rgba(248,113,113,.5); color: #f87171; }
.csst-ready_for_review, .csst-approved_for_execution { border-color: rgba(74,222,128,.5); color: #4ade80; }
.csst-awaiting_user_decision { border-color: rgba(251,191,36,.5); color: #fbbf24; }
.csst-routing, .csst-consulting, .csst-analyzing_dependencies, .csst-synthesizing_plan, .csst-validating_plan, .csst-building_preview { border-color: rgba(96,165,250,.5); color: #60a5fa; }

/* AI-7D：变更方案审查弹窗（Teleport to body） */
.cpr-modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 1000;
  display: flex; align-items: center; justify-content: center; padding: 24px; }
.cpr-modal { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 12px;
  width: min(720px, 95vw); max-height: 88vh; display: flex; flex-direction: column; box-shadow: 0 20px 60px rgba(0,0,0,.4); }
.cpr-modal-head { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px;
  border-bottom: 1px solid var(--border); }
.cpr-modal-title { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.cpr-modal-x { background: none; border: none; color: var(--text-muted); font-size: 18px; cursor: pointer; padding: 4px 8px; border-radius: 4px; }
.cpr-modal-x:hover { background: var(--bg-hover); color: var(--text-primary); }
.cpr-modal-body { padding: 16px; overflow-y: auto; }
.swt-btn { margin-left: 8px; padding: 4px 12px; font-size: 12px; border-radius: 10px; border: 1px solid rgba(74,222,128,.5); color: #4ade80; background: rgba(74,222,128,.08); cursor: pointer; }
.swt-btn:hover { background: rgba(74,222,128,.16); }
</style>
