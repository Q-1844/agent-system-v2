<template>
  <div class="wf-studio">
    <!-- ============ 顶部工具条 ============ -->
    <header class="wf-topbar">
      <h2 class="wf-title">工作流</h2>
      <input
        v-model="draft.name"
        class="wf-name"
        placeholder="工作流名称"
        spellcheck="false"
        @input="markDirty"
      />
      <span class="wf-chip" :class="saveTone">
        <span class="dot" />{{ saveText }}
      </span>
      <span v-if="backendOnline === false" class="wf-chip chip-warn"
            title="后端未连接：注册项以仓库快照展示，无法核对 Agent 停用状态">
        后端未连接
      </span>

      <span class="wf-spacer" />

      <div class="wf-legend" title="两类连线的图例">
        <span class="lg-item"><span class="lg-line exec" />执行依赖</span>
        <span class="lg-item"><span class="lg-line bind" />Skill 绑定</span>
      </div>

      <button class="btn btn-outline btn-sm" :class="{ 'check-bad': validation.errors > 0, 'check-ok': validation.errors === 0 && validation.warnings === 0 }"
              @click="runCheckManually">
        结构检查{{ checkLabel }}
      </button>
      <button class="btn btn-quiet btn-sm" @click="loadSample">示例工作流</button>
      <button class="btn btn-quiet btn-sm" @click="exportJson">导出 JSON</button>
      <button class="btn btn-primary btn-sm" @click="saveNow">保存草稿</button>
      <button class="btn btn-outline btn-sm run-disabled" disabled title="待后端接入：第一版仅做前端草稿，不触发任何执行">
        <span class="run-full">运行（待后端接入）</span><span class="run-short" title="待后端接入">运行*</span>
      </button>
    </header>

    <div class="wf-main">
      <!-- ============ 左：组件库 ============ -->
      <aside class="wf-lib">
        <input v-model="libSearch" class="input lib-search" placeholder="搜索组件…" spellcheck="false" />
        <div class="lib-scroll">
          <div class="lib-group">
            <div class="lib-group-title">Agent 组件<span class="cnt">{{ filteredAgents.length }}</span></div>
            <div
              v-for="a in filteredAgents"
              :key="a.id"
              class="lib-item"
              draggable="true"
              :title="`${a.description}\n来源：${a.source}`"
              @dragstart="onLibDragStart($event, { kind: 'agent', refId: a.id })"
              @click="addNodeFromLib({ kind: 'agent', refId: a.id })"
            >
              <div class="li-row">
                <span class="li-name">{{ a.name }}</span>
                <span class="li-id mono-num">{{ a.id }}</span>
              </div>
              <div class="li-sub">{{ a.role }}</div>
            </div>
          </div>

          <div class="lib-group">
            <div class="lib-group-title">Skill 组件<span class="cnt">{{ filteredSkills.length }}</span></div>
            <div
              v-for="s in filteredSkills"
              :key="s.id"
              class="lib-item lib-skill"
              draggable="true"
              :title="`${s.description}\n来源：${s.manifestSource}`"
              @dragstart="onLibDragStart($event, { kind: 'skill', refId: s.id })"
              @click="addNodeFromLib({ kind: 'skill', refId: s.id })"
            >
              <div class="li-row">
                <span class="li-name">{{ s.name }}</span>
                <span class="li-id mono-num">v{{ s.version }}</span>
              </div>
              <div class="li-sub mono-num">{{ s.id }}</div>
            </div>
            <div class="lib-hint">Skill 需用绑定线连到 Agent，才会被该 Agent 使用</div>
          </div>

          <div class="lib-group">
            <div class="lib-group-title">人工审核<span class="cnt">1</span></div>
            <div
              class="lib-item lib-review"
              title="在 Agent 之间插入人工审核；可连续放置两道表达「先审一次，再审一次」"
              @click="addNodeFromLib({ kind: 'human_review' })"
            >
              <div class="li-row"><span class="li-name">人工审核</span></div>
              <div class="li-sub">通过 → 继续；驳回 → 停止并留意见</div>
            </div>
          </div>

          <div class="lib-group">
            <div class="lib-group-title">边界提示<span class="cnt">2</span></div>
            <div class="lib-boundary-row">
              <button class="btn btn-outline btn-sm" @click="addNodeFromLib({ kind: 'boundary_start' })">▶ 开始</button>
              <button class="btn btn-outline btn-sm" @click="addNodeFromLib({ kind: 'boundary_end' })">■ 结束</button>
            </div>
            <div class="lib-hint">开始 / 结束只是画布边界提示，不算组件类型</div>
          </div>
        </div>
        <div class="lib-foot">注册项来源：src/agents/registry.py · src/agents/skill_system/builtin（快照，详见契约为档）</div>
      </aside>

      <!-- ============ 中：画布 ============ -->
      <section ref="canvasWrap" class="wf-canvas-wrap" @dragover.prevent @drop="onDrop">
        <VueFlow
          :nodes="flowNodes"
          :edges="flowEdges"
          :delete-key-code="null"
          :min-zoom="0.25"
          :max-zoom="2"
          :connection-radius="30"
          class="wf-flow"
          @nodes-change="onNodesChange"
          @connect="onConnect"
          @node-click="onNodeClick"
          @edge-click="onEdgeClick"
          @pane-click="clearSelection"
        >
          <Background :gap="20" :size="1.4" pattern-color="#808080" />
          <template #node-wfAgent="p"><AgentNode v-bind="p" /></template>
          <template #node-wfSkill="p"><SkillNode v-bind="p" /></template>
          <template #node-wfReview="p"><ReviewNode v-bind="p" /></template>
          <template #node-wfBoundary="p"><BoundaryNode v-bind="p" /></template>
          <template #edge-wfEdge="p"><WfEdge v-bind="p" /></template>
        </VueFlow>

        <div class="wf-canvas-controls">
          <button class="ctrl-btn" title="缩小" @click="zoomOut()">−</button>
          <span class="ctrl-zoom mono-num">{{ zoomPct }}%</span>
          <button class="ctrl-btn" title="放大" @click="zoomIn()">+</button>
          <button class="ctrl-btn" title="适应画布" @click="fitToContent()">⤢</button>
        </div>

        <div v-if="!draft.nodes.length" class="wf-empty-guide">
          <p>画布为空。从左侧组件库添加 Agent / Skill / 人工审核，</p>
          <p>或 <a class="guide-link" @click.prevent="loadSample">载入示例工作流</a>，看一条由真实注册项组成的链。</p>
        </div>
      </section>

      <!-- ============ 右：配置面板 ============ -->
      <aside class="wf-panel">
        <!-- 概览（未选中） -->
        <template v-if="panelMode === 'overview'">
          <div class="panel-head">
            <span class="panel-title">工作流概览</span>
          </div>
          <div class="panel-body">
            <div class="kv"><span class="k">项目</span><span class="v">{{ projectName }}</span></div>
            <div class="kv"><span class="k">工作流</span><span class="v">{{ draft.name || '未命名' }}</span></div>
            <div class="kv"><span class="k">标识</span><span class="v mono-num">{{ draft.workflowId }}</span></div>
            <div class="stat-row">
              <span class="stat"><b>{{ countBy('agent') }}</b>Agent</span>
              <span class="stat"><b>{{ countBy('skill') }}</b>Skill</span>
              <span class="stat"><b>{{ countBy('human_review') }}</b>人工审核</span>
              <span class="stat"><b>{{ execCount }}</b>执行线</span>
              <span class="stat"><b>{{ bindCount }}</b>绑定线</span>
            </div>

            <div class="sec-label">结构检查</div>
            <div v-if="!validation.issues.length" class="check-ok-line">✓ 未发现问题</div>
            <div v-else class="issue-list">
              <button
                v-for="(iss, i) in validation.issues"
                :key="i"
                class="issue"
                :class="iss.level"
                @click="jumpToIssue(iss)"
              >
                <span class="iss-badge">{{ iss.level === 'error' ? '✕' : '⚠' }}</span>
                <span class="iss-text">{{ iss.message }}</span>
              </button>
            </div>

            <div class="sec-label">本页语义（第一版）</div>
            <p class="sem-note">
              本页为<b>浏览器草稿</b>：按「项目 + 工作流标识」存于本机 localStorage，刷新后可恢复；
              「保存草稿」不触发任何后端执行；「运行」待后端接入。
              执行依赖为无环顺序图；人工审核「通过→继续、驳回→停止」，自动返工待后端定义。
            </p>
            <button class="btn btn-quiet btn-sm danger-link" @click="clearCanvas">新建空白工作流</button>
          </div>
        </template>

        <!-- Agent 配置 -->
        <template v-else-if="panelMode === 'agent' && selectedAgentNode">
          <div class="panel-head">
            <span class="panel-title">{{ selAgent?.name ?? selectedAgentNode.refId }}</span>
            <span class="panel-id mono-num">{{ selectedAgentNode.refId }}</span>
          </div>
          <div class="panel-body">
            <p v-if="!selAgent" class="unset-hint">引用失效：注册表中找不到该 Agent。请删除该节点或改用正式 Agent。</p>
            <template v-else>
              <div class="kv"><span class="k">角色</span><span class="v">{{ selAgent.role }}</span></div>
              <p class="p-desc">{{ selAgent.description }}</p>
              <div class="kv"><span class="k">读取</span><span class="v">{{ selAgent.readScopes.join(' / ') }}</span></div>
              <div class="kv"><span class="k">写入</span><span class="v">{{ selAgent.writeScopes.join(' / ') }}</span></div>
              <div class="kv"><span class="k">产物</span><span class="v">{{ selAgent.artifacts.join('；') }}</span></div>
              <div class="kv"><span class="k">注册依据</span><span class="v mono-num tiny">{{ selAgent.source }}</span></div>

              <div v-if="selectedAgentNode && isAgentDisabled(selectedAgentNode.refId)" class="unset-hint warn">
                ◌ 后端已停用该 Agent（settings.enabled=false）：画布可编辑，运行不可用。
              </div>

              <div class="sec-label">工具（获准工具面 · 草稿勾选）</div>
              <div class="tool-actions">
                <span class="tool-count">{{ selectedTools.length }}/{{ selAgent.tools.length }}</span>
                <button class="btn btn-quiet btn-sm" @click="selectAllTools">全选</button>
                <button class="btn btn-quiet btn-sm" @click="clearTools">清空</button>
              </div>
              <div class="tool-list">
                <label v-for="t in selAgent.tools" :key="t" class="tool-item">
                  <input type="checkbox" :checked="selectedTools.includes(t)" @change="toggleTool(t)" />
                  <span class="mono-num">{{ t }}</span>
                </label>
              </div>
              <p class="sem-note tiny">仅编辑前端草稿，不扩大权限、不创建新工具类型；未勾选 = 使用该 Agent 全部默认工具面。</p>
            </template>
            <button class="btn btn-outline btn-sm danger-link" @click="removeNode(selectedAgentNode!.id)">删除此节点</button>
          </div>
        </template>

        <!-- Skill 配置 -->
        <template v-else-if="panelMode === 'skill' && selectedSkillNode">
          <div class="panel-head">
            <span class="panel-title">{{ selSkill?.name ?? selectedSkillNode.refId }}</span>
            <span class="panel-id mono-num">v{{ selectedSkillNode.refVersion }}</span>
          </div>
          <div class="panel-body">
            <p v-if="!selSkill" class="unset-hint">引用失效：正式 Skill 目录中找不到该 Skill。</p>
            <template v-else>
              <div class="chip-row">
                <span class="wf-chip chip-blue">正式 Skill</span>
                <span class="wf-chip mono-num">{{ selSkill.source }}</span>
                <span class="wf-chip">{{ selSkill.status }}</span>
              </div>
              <p class="p-desc">{{ selSkill.description }}</p>
              <div class="kv"><span class="k">适用</span><span class="v">{{ selSkill.applicability }}</span></div>
              <div v-if="selSkill.dependsOn.length" class="kv">
                <span class="k">依赖</span>
                <span class="v mono-num">{{ selSkill.dependsOn.join('、') }}</span>
              </div>
              <div class="kv"><span class="k">清单来源</span><span class="v mono-num tiny">{{ selSkill.manifestSource }}</span></div>

              <div class="sec-label">官方绑定（skill.json）</div>
              <div class="bind-table">
                <div v-for="b in selSkill.bindings" :key="b.agentId" class="bind-row">
                  <span class="bind-agent">{{ getAgent(b.agentId)?.name ?? b.agentId }}</span>
                  <span class="bind-role">{{ b.role }}</span>
                  <span class="mono-num bind-pri">P{{ b.routingPriority }}</span>
                </div>
              </div>
              <p class="sem-note tiny">Skill 绑定给 Agent 使用，不表示 Skill 会先独立运行或生成产物。</p>
            </template>
            <button class="btn btn-outline btn-sm danger-link" @click="removeNode(selectedSkillNode!.id)">删除此节点</button>
          </div>
        </template>

        <!-- 人工审核配置 -->
        <template v-else-if="panelMode === 'review' && selectedReviewNode">
          <div class="panel-head">
            <span class="panel-title">人工审核</span>
            <span class="panel-id">可连续放置两道</span>
          </div>
          <div class="panel-body">
            <div class="field">
              <span class="f-k">显示名</span>
              <input v-model="reviewLabel" class="input" placeholder="如：场景参考审定" spellcheck="false" />
            </div>

            <div class="field">
              <span class="f-k">审核对象 / 产物</span>
              <template v-if="reviewUpstreamOptions.length">
                <select v-model="reviewTargetSource" class="input">
                  <option v-for="opt in reviewUpstreamOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                  <option value="__custom__">自定义…</option>
                </select>
                <select v-if="reviewTargetSource !== '__custom__' && reviewArtifactOptions.length"
                        v-model="reviewTargetArtifact" class="input">
                  <option v-for="opt in reviewArtifactOptions" :key="opt" :value="opt">{{ opt }}</option>
                </select>
                <input v-else v-model="reviewTargetArtifact" class="input" placeholder="审核对象（草稿自由填写）" spellcheck="false" />
              </template>
              <template v-else>
                <input v-model="reviewTargetArtifact" class="input" placeholder="审核对象（尚无上游连线，自由填写）" spellcheck="false" />
                <span class="unset-hint">⚠ 无上游执行依赖：结构检查会提示「审核节点没有上游产物」</span>
              </template>
            </div>

            <div class="field">
              <span class="f-k">审核说明</span>
              <textarea v-model="reviewInstructions" class="input" rows="3" placeholder="审核要点、退回标准…" />
            </div>
            <div class="field">
              <span class="f-k">审核者</span>
              <input v-model="reviewReviewer" class="input" placeholder="如：制作人（草稿自由填写，仓库无审核者名册）" spellcheck="false" />
            </div>

            <div class="sec-label">首版语义</div>
            <p class="sem-note">
              通过 → 沿后续执行依赖继续；驳回 → 停止并留下意见，等待人工修改工作流或重新发起。
              <b>自动返工循环与暂停中的后端任务尚未实现（待后端定义）。</b>
            </p>
            <button class="btn btn-outline btn-sm danger-link" @click="removeNode(selectedReviewNode!.id)">删除此节点</button>
          </div>
        </template>

        <!-- 边界配置 -->
        <template v-else-if="panelMode === 'boundary' && selectedBoundaryNode">
          <div class="panel-head">
            <span class="panel-title">{{ selectedBoundaryNode.type === 'boundary_start' ? '开始（边界）' : '结束（边界）' }}</span>
          </div>
          <div class="panel-body">
            <p class="sem-note">开始 / 结束只是画布边界提示，不是组件类型，不参与执行语义。</p>
            <button class="btn btn-outline btn-sm danger-link" @click="removeNode(selectedBoundaryNode!.id)">删除此节点</button>
          </div>
        </template>

        <!-- 连线信息 -->
        <template v-else-if="panelMode === 'edge' && selectedEdge">
          <div class="panel-head">
            <span class="panel-title">{{ selectedEdge.type === 'skill_binding' ? 'Skill 绑定' : '执行依赖' }}</span>
            <span class="wf-chip" :class="selectedEdge.type === 'skill_binding' ? 'chip-blue' : ''">
              {{ selectedEdge.type === 'skill_binding' ? '绑定线' : '执行线' }}
            </span>
          </div>
          <div class="panel-body">
            <div class="kv">
              <span class="k">方向</span>
              <span class="v">{{ edgeSourceTitle }} <b class="arrow">→</b> {{ edgeTargetTitle }}</span>
            </div>
            <div class="kv">
              <span class="k">端口</span>
              <span class="v mono-num">{{ portLabel(selectedEdge.sourcePort) }} → {{ portLabel(selectedEdge.targetPort) }}</span>
            </div>

            <template v-if="selectedEdge.type === 'skill_binding'">
              <div class="kv">
                <span class="k">版本</span>
                <span class="v mono-num">v{{ selectedEdge.skillVersion ?? '?' }}</span>
              </div>
              <p class="sem-note">表示该 Agent 使用该 Skill 的指定版本；不表示 Skill 要先独立运行。</p>
            </template>
            <template v-else-if="selectedEdge.contract">
              <div class="chip-row">
                <span class="wf-chip" :class="contractChipClass">
                  {{ contractText }}
                </span>
              </div>
              <div class="sec-label">上游产物</div>
              <ul class="artifact-list">
                <li v-for="(a, i) in selectedEdge.contract.upstreamArtifacts" :key="i">{{ a }}</li>
              </ul>
              <div class="sec-label">下游读取</div>
              <ul class="artifact-list">
                <li v-for="(a, i) in selectedEdge.contract.downstreamReads" :key="i">{{ a }}</li>
              </ul>
              <div class="sec-label">依据</div>
              <p class="evidence mono-num">{{ selectedEdge.contract.evidence }}</p>
            </template>

            <button class="btn btn-outline btn-sm danger-link" @click="removeEdge(selectedEdge!.id)">删除连线</button>
          </div>
        </template>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { VueFlow, useVueFlow, MarkerType } from '@vue-flow/core';
import type { Connection, EdgeChange, NodeChange } from '@vue-flow/core';
// vue-flow 基础布局样式（.vue-flow__node 绝对定位 / handle 定位 / viewport 层级）
import '@vue-flow/core/dist/style.css';
import { Background } from '@vue-flow/background';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';
import { apiClient } from '@/api';

import { WORKFLOW_AGENTS, WORKFLOW_SKILLS, getAgent, getSkill } from './catalog';
import type { AgentCatalogEntry, SkillCatalogEntry } from './catalog';
import { executionContract } from './contracts';
import { validateDraft, connectionRule } from './validation';
import { loadDraft, saveDraft, newEmptyDraft } from './storage';
import { buildSampleDraft } from './sample';
import {
  makeNode, genId,
} from './types';
import type { ValidationIssue, WorkflowDraft, WorkflowEdge, WorkflowNode, WorkflowNodeType } from './types';
import AgentNode from './nodes/AgentNode.vue';
import SkillNode from './nodes/SkillNode.vue';
import ReviewNode from './nodes/ReviewNode.vue';
import BoundaryNode from './nodes/BoundaryNode.vue';
import WfEdge from './edges/WfEdge.vue';

const { message, dialog } = createDiscreteApi(['message', 'dialog'], {
  configProviderProps: discreteConfigProviderProps,
});

const props = defineProps<{ projectName: string }>();

const {
  zoomIn, zoomOut, setTransform, screenToFlowCoordinate, vueFlowRef, viewport: vp,
} = useVueFlow();

// 自研「适应画布」：vue-flow 内置 fitView 在受控节点场景下会因 store 尺寸状态
// 静默失败，这里直接用 DOM 实测包围盒 + setTransform，结果确定可复现。
function fitToContent(duration = 220) {
  const el = vueFlowRef.value as HTMLElement | null;
  if (!el) return;
  const cont = el.getBoundingClientRect();
  const z = vp.value?.zoom ?? 1;
  const vx = vp.value?.x ?? 0;
  const vy = vp.value?.y ?? 0;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  let any = false;
  el.querySelectorAll<HTMLElement>('.vue-flow__node').forEach((n) => {
    const r = n.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const fx = (r.left - cont.left - vx) / z;
    const fy = (r.top - cont.top - vy) / z;
    minX = Math.min(minX, fx);
    minY = Math.min(minY, fy);
    maxX = Math.max(maxX, fx + r.width / z);
    maxY = Math.max(maxY, fy + r.height / z);
    any = true;
  });
  if (!any) return;
  const pad = 56;
  const bw = Math.max(1, maxX - minX);
  const bh = Math.max(1, maxY - minY);
  const zoom = Math.max(0.25, Math.min(1.5, Math.min((cont.width - pad * 2) / bw, (cont.height - pad * 2) / bh)));
  const tx = cont.width / 2 - ((minX + maxX) / 2) * zoom;
  const ty = cont.height / 2 - ((minY + maxY) / 2) * zoom;
  setTransform({ x: tx, y: ty, zoom }, { duration });
}

/** 把画布视图居中到某个流程坐标点（定位校验问题用） */
function centerOnFlowPoint(x: number, y: number, zoom?: number) {
  const el = vueFlowRef.value as HTMLElement | null;
  if (!el) return;
  const cont = el.getBoundingClientRect();
  const z = zoom ?? (vp.value?.zoom ?? 1);
  setTransform({ x: cont.width / 2 - x * z, y: cont.height / 2 - y * z, zoom: z }, { duration: 240 });
}

// ============ 草稿状态 ============
const draft = ref<WorkflowDraft>(newEmptyDraft(props.projectName, '未命名工作流'));
const dirty = ref(false);
const savedAt = ref<string | null>(null);
const libSearch = ref('');
const canvasWrap = ref<HTMLElement | null>(null);

const selectedNodeId = ref<string | null>(null);
const selectedEdgeId = ref<string | null>(null);

const backendOnline = ref<boolean | null>(null);
const disabledAgents = ref<Set<string>>(new Set());

const validation = computed(() => validateDraft(draft.value));

function markDirty() {
  dirty.value = true;
}

// ============ 生命周期 ============
onMounted(() => {
  const restored = loadDraft(props.projectName);
  if (restored) {
    draft.value = restored;
    dirty.value = false;
    savedAt.value = restored.updatedAt ?? null;
    scheduleFitView();
  }
  // 后端在线核对：拿到 Agent 停用状态（可选能力，失败不影响草稿编辑）
  apiClient
    .listAgents()
    .then((list) => {
      backendOnline.value = true;
      disabledAgents.value = new Set(list.filter((a) => !a.enabled).map((a) => a.id));
    })
    .catch(() => {
      backendOnline.value = false;
    });

  window.addEventListener('beforeunload', onBeforeUnload);
});
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', onBeforeUnload);
});
function onBeforeUnload(e: BeforeUnloadEvent) {
  if (dirty.value) {
    e.preventDefault();
    e.returnValue = '';
  }
}

// ============ 画布映射（draft → vue-flow）============
function vueType(t: WorkflowNodeType): string {
  switch (t) {
    case 'agent': return 'wfAgent';
    case 'skill': return 'wfSkill';
    case 'human_review': return 'wfReview';
    default: return 'wfBoundary';
  }
}

const issuesByNode = computed(() => {
  const m = new Map<string, ValidationIssue[]>();
  for (const iss of validation.value.issues) {
    if (!iss.nodeId) continue;
    if (!m.has(iss.nodeId)) m.set(iss.nodeId, []);
    m.get(iss.nodeId)!.push(iss);
  }
  return m;
});
const issuesByEdge = computed(() => {
  const m = new Map<string, ValidationIssue[]>();
  for (const iss of validation.value.issues) {
    if (!iss.edgeId) continue;
    if (!m.has(iss.edgeId)) m.set(iss.edgeId, []);
    m.get(iss.edgeId)!.push(iss);
  }
  return m;
});

function boundSkillCountOf(nodeId: string): number {
  return draft.value.edges.filter((e) => e.type === 'skill_binding' && e.target === nodeId).length;
}
function boundTargetsOf(nodeId: string): string[] {
  return draft.value.edges
    .filter((e) => e.type === 'skill_binding' && e.source === nodeId)
    .map((e) => {
      const t = draft.value.nodes.find((n) => n.id === e.target);
      return (t && getAgent(t.refId)?.name) || t?.refId || '未知';
    });
}
function upstreamAgentNameOf(nodeId: string): string | undefined {
  const e = draft.value.edges.find((x) => x.type === 'execution' && x.target === nodeId);
  if (!e) return undefined;
  const src = draft.value.nodes.find((n) => n.id === e.source);
  if (!src) return undefined;
  return src.label || (src.type === 'agent' ? getAgent(src.refId)?.name : undefined);
}
function isAgentDisabled(refId: string | null): boolean {
  return !!refId && disabledAgents.value.has(refId);
}

const flowNodes = computed(() =>
  draft.value.nodes.map((n) => {
    const issues = issuesByNode.value.get(n.id) ?? [];
    let data: Record<string, unknown>;
    if (n.type === 'agent') {
      data = {
        node: n,
        hasError: issues.some((i) => i.level === 'error'),
        errorCount: issues.length,
        errorTitles: issues.map((i) => i.message),
        unavailable: isAgentDisabled(n.refId),
        boundSkillCount: boundSkillCountOf(n.id),
      };
    } else if (n.type === 'skill') {
      data = {
        node: n,
        hasError: issues.some((i) => i.level === 'error'),
        errorCount: issues.length,
        errorTitles: issues.map((i) => i.message),
        boundTargets: boundTargetsOf(n.id),
      };
    } else if (n.type === 'human_review') {
      const reviewIdx = draft.value.nodes
        .filter((x) => x.type === 'human_review')
        .findIndex((x) => x.id === n.id);
      data = {
        node: n,
        hasError: issues.some((i) => i.level === 'error'),
        errorCount: issues.length,
        errorTitles: issues.map((i) => i.message),
        reviewSeq: reviewIdx + 1,
        upstreamAgentName: upstreamAgentNameOf(n.id),
      };
    } else {
      data = { kind: n.type === 'boundary_start' ? 'start' : 'end' };
    }
    return { id: n.id, type: vueType(n.type), position: { ...n.position }, data };
  })
);

const flowEdges = computed(() =>
  draft.value.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    sourceHandle: e.sourcePort,
    targetHandle: e.targetPort,
    type: 'wfEdge',
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: e.type === 'skill_binding' ? '#60a5fa' : '#7a7a7a',
      width: 16,
      height: 16,
    },
    data: {
      edgeType: e.type,
      contractStatus: e.contract?.status,
      skillVersion: e.skillVersion ?? null,
    },
  }))
);

function onNodesChange(changes: NodeChange[]) {
  let moved = false;
  for (const c of changes) {
    if (c.type === 'position' && c.position) {
      const n = draft.value.nodes.find((x) => x.id === c.id);
      if (n && (n.position.x !== c.position.x || n.position.y !== c.position.y)) {
        n.position = { ...c.position };
        moved = true;
      }
    }
  }
  if (moved) markDirty();
}

// ============ 连线 ============
function onConnect(conn: Connection) {
  const check = connectionRule(
    draft.value,
    conn.source,
    conn.target,
    conn.sourceHandle ?? 'out',
    conn.targetHandle ?? 'in'
  );
  if (!check.ok) {
    message.error(check.reason ?? '不允许的连接', { duration: 4200 });
    return;
  }
  const srcNode = draft.value.nodes.find((n) => n.id === conn.source);
  const dstNode = draft.value.nodes.find((n) => n.id === conn.target);
  if (!srcNode || !dstNode) return;
  const isBinding = srcNode.type === 'skill';
  const edge: WorkflowEdge = {
    id: genId('e'),
    type: isBinding ? 'skill_binding' : 'execution',
    source: conn.source,
    target: conn.target,
    sourcePort: (conn.sourceHandle ?? 'out') as WorkflowEdge['sourcePort'],
    targetPort: (conn.targetHandle ?? 'in') as WorkflowEdge['targetPort'],
  };
  if (isBinding) {
    edge.skillVersion = srcNode.refVersion ?? null;
    edge.contract = null;
  } else {
    edge.contract = executionContract(
      srcNode.type === 'agent' ? srcNode.refId : null,
      dstNode.type === 'agent' ? dstNode.refId : null,
      srcNode.type === 'human_review' || dstNode.type === 'human_review',
      dstNode.type === 'human_review'
    );
  }
  draft.value.edges.push(edge);
  markDirty();
  message.success(
    isBinding
      ? `已绑定：${getSkill(srcNode.refId)?.name ?? ''} → ${getAgent(dstNode.refId)?.name ?? ''}`
      : '已连接执行依赖',
    { duration: 1800 }
  );
}

// ============ 画布交互 ============
function onNodeClick(e: { node: { id: string } }) {
  selectedNodeId.value = e.node.id;
  selectedEdgeId.value = null;
}
function onEdgeClick(e: { edge: { id: string } }) {
  selectedEdgeId.value = e.edge.id;
  selectedNodeId.value = null;
}
function clearSelection() {
  selectedNodeId.value = null;
  selectedEdgeId.value = null;
}

// ============ 组件库 → 画布 ============
interface LibItem {
  kind: 'agent' | 'skill' | 'human_review' | 'boundary_start' | 'boundary_end';
  refId?: string;
}

function onLibDragStart(evt: DragEvent, item: LibItem) {
  evt.dataTransfer?.setData('application/x-wf-item', JSON.stringify(item));
  if (evt.dataTransfer) evt.dataTransfer.effectAllowed = 'copy';
}

function onDrop(evt: DragEvent) {
  const raw = evt.dataTransfer?.getData('application/x-wf-item');
  if (!raw) return;
  try {
    const item = JSON.parse(raw) as LibItem;
    const pos = screenToFlowCoordinate({ x: evt.clientX, y: evt.clientY });
    addNode(item, { x: pos.x - 110, y: pos.y - 40 });
  } catch {
    /* 忽略非法拖拽数据 */
  }
}

function addNodeFromLib(item: LibItem) {
  // 点击添加：放到当前视口中心附近（带少量错位，避免叠放）
  const rect = (vueFlowRef.value as HTMLElement | null)?.getBoundingClientRect();
  let pos = { x: 160, y: 160 };
  if (rect) {
    const c = screenToFlowCoordinate({
      x: rect.left + rect.width * 0.5,
      y: rect.top + rect.height * 0.5,
    });
    const jitter = draft.value.nodes.length % 5;
    pos = { x: c.x - 110 + jitter * 36, y: c.y - 40 + jitter * 28 };
  }
  addNode(item, pos);
}

function addNode(item: LibItem, position: { x: number; y: number }) {
  let node: WorkflowNode | null = null;
  if (item.kind === 'agent') {
    const a = getAgent(item.refId);
    if (!a) {
      message.error('未找到该 Agent');
      return;
    }
    node = makeNode({ type: 'agent', refId: a.id, position, config: { selectedTools: [] } });
  } else if (item.kind === 'skill') {
    const s = getSkill(item.refId);
    if (!s) {
      message.error('未找到该 Skill');
      return;
    }
    node = makeNode({ type: 'skill', refId: s.id, refVersion: s.version, position });
  } else if (item.kind === 'human_review') {
    node = makeNode({ type: 'human_review', position, config: {} });
  } else if (item.kind === 'boundary_start') {
    if (draft.value.nodes.some((n) => n.type === 'boundary_start')) {
      message.warning('开始（边界）最多一个');
      return;
    }
    node = makeNode({ type: 'boundary_start', position });
  } else if (item.kind === 'boundary_end') {
    if (draft.value.nodes.some((n) => n.type === 'boundary_end')) {
      message.warning('结束（边界）最多一个');
      return;
    }
    node = makeNode({ type: 'boundary_end', position });
  }
  if (!node) return;
  draft.value.nodes.push(node);
  markDirty();
  selectedNodeId.value = node.id;
  selectedEdgeId.value = null;
}

function removeNode(id: string) {
  draft.value.nodes = draft.value.nodes.filter((n) => n.id !== id);
  draft.value.edges = draft.value.edges.filter((e) => e.source !== id && e.target !== id);
  if (selectedNodeId.value === id) clearSelection();
  markDirty();
}
function removeEdge(id: string) {
  draft.value.edges = draft.value.edges.filter((e) => e.id !== id);
  if (selectedEdgeId.value === id) clearSelection();
  markDirty();
  message.info('连线已删除，依赖提示已更新', { duration: 1600 });
}

// ============ 右侧面板 ============
const panelMode = computed<'overview' | 'agent' | 'skill' | 'review' | 'boundary' | 'edge'>(() => {
  if (selectedEdgeId.value) return 'edge';
  const n = draft.value.nodes.find((x) => x.id === selectedNodeId.value);
  if (!n) return 'overview';
  if (n.type === 'agent') return 'agent';
  if (n.type === 'skill') return 'skill';
  if (n.type === 'human_review') return 'review';
  return 'boundary';
});

const selectedAgentNode = computed(() =>
  panelMode.value === 'agent'
    ? (draft.value.nodes.find((n) => n.id === selectedNodeId.value) ?? null)
    : null
);
const selectedSkillNode = computed(() =>
  panelMode.value === 'skill'
    ? (draft.value.nodes.find((n) => n.id === selectedNodeId.value) ?? null)
    : null
);
const selectedReviewNode = computed(() =>
  panelMode.value === 'review'
    ? (draft.value.nodes.find((n) => n.id === selectedNodeId.value) ?? null)
    : null
);
const selectedBoundaryNode = computed(() =>
  panelMode.value === 'boundary'
    ? (draft.value.nodes.find((n) => n.id === selectedNodeId.value) ?? null)
    : null
);
const selectedEdge = computed(
  () => draft.value.edges.find((e) => e.id === selectedEdgeId.value) ?? null
);

const selAgent = computed(() => getAgent(selectedAgentNode.value?.refId));
const selSkill = computed(() => getSkill(selectedSkillNode.value?.refId));

const selectedTools = computed(() => selectedAgentNode.value?.config.selectedTools ?? []);
function toggleTool(tool: string) {
  const n = selectedAgentNode.value;
  if (!n) return;
  const cur = n.config.selectedTools ?? [];
  n.config.selectedTools = cur.includes(tool) ? cur.filter((t) => t !== tool) : [...cur, tool];
  markDirty();
}
function selectAllTools() {
  const n = selectedAgentNode.value;
  if (!n || !selAgent.value) return;
  n.config.selectedTools = [...selAgent.value.tools];
  markDirty();
}
function clearTools() {
  const n = selectedAgentNode.value;
  if (!n) return;
  n.config.selectedTools = [];
  markDirty();
}

// —— 人工审核字段双向 ——
const reviewLabel = computed({
  get: () => selectedReviewNode.value?.label ?? '',
  set: (v: string) => {
    const n = selectedReviewNode.value;
    if (n) {
      n.label = v || null;
      markDirty();
    }
  },
});
const reviewInstructions = computed({
  get: () => selectedReviewNode.value?.config.instructions ?? '',
  set: (v: string) => {
    const n = selectedReviewNode.value;
    if (n) {
      n.config.instructions = v;
      markDirty();
    }
  },
});
const reviewReviewer = computed({
  get: () => selectedReviewNode.value?.config.reviewer ?? '',
  set: (v: string) => {
    const n = selectedReviewNode.value;
    if (n) {
      n.config.reviewer = v;
      markDirty();
    }
  },
});

/** 审核对象来源候选：所有执行入线的 source 节点 */
const reviewUpstreamOptions = computed(() => {
  const n = selectedReviewNode.value;
  if (!n) return [];
  return draft.value.edges
    .filter((e) => e.type === 'execution' && e.target === n.id)
    .map((e) => {
      const src = draft.value.nodes.find((x) => x.id === e.source);
      if (!src) return null;
      const title =
        src.label || (src.type === 'agent' ? getAgent(src.refId)?.name : undefined) ||
        (src.type === 'human_review' ? '上一道审核' : '开始');
      return { value: src.id, label: title };
    })
    .filter((x): x is { value: string; label: string } => !!x);
});

const reviewTargetSource = computed({
  get: () => {
    const n = selectedReviewNode.value;
    if (!n) return '';
    const src = n.config.targetSourceNodeId;
    if (src && reviewUpstreamOptions.value.some((o) => o.value === src)) return src;
    return reviewUpstreamOptions.value.length ? '__custom__' : '__custom__';
  },
  set: (v: string) => {
    const n = selectedReviewNode.value;
    if (!n) return;
    if (v === '__custom__') {
      n.config.targetSourceNodeId = null;
    } else {
      n.config.targetSourceNodeId = v;
      const src = draft.value.nodes.find((x) => x.id === v);
      const a = src && src.type === 'agent' ? getAgent(src.refId) : undefined;
      n.config.targetArtifact = a?.artifacts[0] ?? '上一道审核通过后的后续产物';
    }
    markDirty();
  },
});

const reviewArtifactOptions = computed(() => {
  const n = selectedReviewNode.value;
  if (!n) return [];
  const srcId = n.config.targetSourceNodeId;
  if (!srcId) return [];
  const src = draft.value.nodes.find((x) => x.id === srcId);
  if (!src) return [];
  if (src.type === 'agent') return getAgent(src.refId)?.artifacts ?? [];
  if (src.type === 'human_review') return ['上一道审核通过后的后续产物'];
  return [];
});

const reviewTargetArtifact = computed({
  get: () => selectedReviewNode.value?.config.targetArtifact ?? '',
  set: (v: string) => {
    const n = selectedReviewNode.value;
    if (n) {
      n.config.targetArtifact = v;
      markDirty();
    }
  },
});

// —— 连线面板 ——
function nodeTitle(id: string): string {
  const n = draft.value.nodes.find((x) => x.id === id);
  if (!n) return '未知节点';
  if (n.label) return n.label;
  if (n.type === 'agent') return getAgent(n.refId)?.name ?? String(n.refId);
  if (n.type === 'skill') return getSkill(n.refId)?.name ?? String(n.refId);
  if (n.type === 'human_review') return '人工审核';
  if (n.type === 'boundary_start') return '开始';
  return '结束';
}
const edgeSourceTitle = computed(() =>
  selectedEdge.value ? nodeTitle(selectedEdge.value.source) : ''
);
const edgeTargetTitle = computed(() =>
  selectedEdge.value ? nodeTitle(selectedEdge.value.target) : ''
);
function portLabel(p: WorkflowEdge['sourcePort']): string {
  return p === 'bind' ? 'bind（Skill 绑定）' : p === 'in' ? 'in（执行输入）' : 'out（执行输出）';
}
const contractChipClass = computed(() => {
  const s = selectedEdge.value?.contract?.status;
  return s === 'verified' ? 'chip-ok' : s === 'pending_backend' ? 'chip-warn' : '';
});
const contractText = computed(() => {
  const s = selectedEdge.value?.contract?.status;
  return s === 'verified'
    ? '✓ 已核实产物契约'
    : s === 'pending_backend'
      ? '待后端定义（未找到逐对产物契约）'
      : '人工审核语义（首版）';
});

// —— 概览 ——
function countBy(t: WorkflowNodeType): number {
  return draft.value.nodes.filter((n) => n.type === t).length;
}
const execCount = computed(() => draft.value.edges.filter((e) => e.type === 'execution').length);
const bindCount = computed(() => draft.value.edges.filter((e) => e.type === 'skill_binding').length);

function jumpToIssue(iss: ValidationIssue) {
  if (iss.nodeId) {
    selectedNodeId.value = iss.nodeId;
    selectedEdgeId.value = null;
    const n = draft.value.nodes.find((x) => x.id === iss.nodeId);
    if (n) centerOnFlowPoint(n.position.x + 116, n.position.y + 70, 0.9);
  } else if (iss.edgeId) {
    selectedEdgeId.value = iss.edgeId;
    selectedNodeId.value = null;
  }
}

// ============ 组件库过滤 ============
const filteredAgents = computed<AgentCatalogEntry[]>(() => {
  const q = libSearch.value.trim().toLowerCase();
  if (!q) return WORKFLOW_AGENTS;
  return WORKFLOW_AGENTS.filter(
    (a) =>
      a.name.toLowerCase().includes(q) ||
      a.id.includes(q) ||
      a.role.toLowerCase().includes(q) ||
      a.description.toLowerCase().includes(q)
  );
});
const filteredSkills = computed<SkillCatalogEntry[]>(() => {
  const q = libSearch.value.trim().toLowerCase();
  if (!q) return WORKFLOW_SKILLS;
  return WORKFLOW_SKILLS.filter(
    (s) =>
      s.name.toLowerCase().includes(q) ||
      s.id.includes(q) ||
      s.description.toLowerCase().includes(q)
  );
});

// ============ 保存 / 导出 / 检查 / 示例 ============
const saveText = computed(() => {
  if (dirty.value) return '未保存更改';
  return savedAt.value
    ? `草稿已保存 · ${formatTime(savedAt.value)}`
    : '草稿（未保存）';
});
const saveTone = computed(() => (dirty.value ? 'chip-warn' : 'chip-ok'));
function formatTime(iso: string): string {
  const d = new Date(iso);
  const p = (x: number) => String(x).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}
const checkLabel = computed(() => {
  const v = validation.value;
  if (v.errors) return ` · ${v.errors} 错${v.warnings ? ` ${v.warnings} 警` : ''}`;
  if (v.warnings) return ` · ${v.warnings} 警`;
  return ' · 通过';
});

function runCheckManually() {
  const r = validateDraft(draft.value);
  if (!r.issues.length) {
    message.success('结构检查通过：无错误、无警告', { duration: 2400 });
  } else {
    clearSelection();
    message.warning(
      `结构检查：${r.errors} 个错误、${r.warnings} 个警告（右侧面板可定位）`,
      { duration: 3200 }
    );
  }
}

function saveNow() {
  // 保存草稿前运行前端结构检查（错误不阻止保存草稿，但明确报告）
  const r = validateDraft(draft.value);
  saveDraft(draft.value);
  dirty.value = false;
  savedAt.value = draft.value.updatedAt;
  if (r.errors) {
    message.warning(
      `草稿已保存到浏览器，但存在 ${r.errors} 个结构错误、${r.warnings} 个警告（点「结构检查」定位）`,
      { duration: 4200 }
    );
  } else if (r.warnings) {
    message.success(`草稿已保存（${r.warnings} 个警告，见结构检查）`, { duration: 2600 });
  } else {
    message.success('草稿已保存（浏览器草稿 · 结构检查通过）', { duration: 2400 });
  }
}

function exportJson() {
  const r = validateDraft(draft.value);
  const payload = {
    ...draft.value,
    updatedAt: new Date().toISOString(),
    validation: r,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  const safeName = (draft.value.name || 'draft').replace(/[\\/:*?"<>|]/g, '_');
  a.href = url;
  a.download = `workflow-${props.projectName}-${safeName}.json`;
  a.click();
  URL.revokeObjectURL(url);
  if (r.errors) {
    message.warning(`JSON 已导出（含 ${r.errors} 个结构错误的检查结果）`, { duration: 3200 });
  } else {
    message.success('JSON 已导出（含结构检查结果）', { duration: 2400 });
  }
}

// fitView 需要节点尺寸就绪；节点刚注入时未必完成布局，重试几次
function scheduleFitView() {
  const delays = [80, 300, 700, 1400];
  for (const d of delays) {
    setTimeout(() => {
      if (!draft.value.nodes.length) return;
      fitToContent();
    }, d);
  }
}

function loadSample() {
  if (draft.value.nodes.length && dirty.value) {
    dialog.warning({
      title: '载入示例工作流',
      content: '当前画布有未保存的更改，载入示例将覆盖它们（可先「保存草稿」）。确定继续？',
      positiveText: '覆盖并载入示例',
      negativeText: '取消',
      onPositiveClick: () => applySample(),
    });
  } else {
    applySample();
  }
}
function applySample() {
  draft.value = buildSampleDraft(props.projectName);
  dirty.value = true;
  clearSelection();
  scheduleFitView();
  message.success('示例已载入：全部条目来自真实注册表（Agent×5 · Skill×1 · 人工审核×2）', {
    duration: 3600,
  });
}

function clearCanvas() {
  dialog.warning({
    title: '新建空白工作流',
    content: '将清空当前画布（浏览器草稿需重新保存后才生效）。确定继续？',
    positiveText: '清空',
    negativeText: '取消',
    onPositiveClick: () => {
      draft.value = newEmptyDraft(props.projectName, '未命名工作流');
      dirty.value = true;
      clearSelection();
    },
  });
}

const zoomPct = computed(() => Math.round((vp.value?.zoom ?? 1) * 100));

// ============ 搭建助手（右侧对话框）============
const assistantOpen = ref(window.innerWidth >= 1280);

function findAgentNodeByRef(refId: string): WorkflowNode | undefined {
  return draft.value.nodes.find((n) => n.type === 'agent' && n.refId === refId);
}
function findSkillNodeByRef(refId: string): WorkflowNode | undefined {
  return draft.value.nodes.find((n) => n.type === 'skill' && n.refId === refId);
}

/** 静默连线（不走 toast；重复边视为 skipped 而非失败） */
function connectNodes(
  sourceId: string,
  targetId: string,
  kind: 'execution' | 'skill_binding',
  sourcePort: 'out' | 'bind' = kind === 'skill_binding' ? 'bind' : 'out',
  targetPort: 'in' | 'bind' = kind === 'skill_binding' ? 'bind' : 'in'
): ApplyResult & { skipped?: boolean } {
  const check = connectionRule(draft.value, sourceId, targetId, sourcePort, targetPort);
  if (!check.ok) {
    const dup = /已存在/.test(check.reason ?? '');
    return dup
      ? { ok: true, skipped: true }
      : { ok: false, reason: check.reason };
  }
  const src = draft.value.nodes.find((n) => n.id === sourceId);
  const dst = draft.value.nodes.find((n) => n.id === targetId);
  if (!src || !dst) return { ok: false, reason: '节点不存在' };
  const edge: WorkflowEdge = {
    id: genId('e'),
    type: kind,
    source: sourceId,
    target: targetId,
    sourcePort,
    targetPort,
  };
  if (kind === 'skill_binding') {
    edge.skillVersion = src.refVersion ?? null;
    edge.contract = null;
  } else {
    edge.contract = executionContract(
      src.type === 'agent' ? src.refId : null,
      dst.type === 'agent' ? dst.refId : null,
      src.type === 'human_review' || dst.type === 'human_review',
      dst.type === 'human_review'
    );
  }
  draft.value.edges.push(edge);
  markDirty();
  return { ok: true };
}

/** 为新节点找一排空闲 y（与现有节点保持距离） */
function findFreeY(x: number, height = 0): number {
  const base = 280;
  for (let row = 0; row < 6; row++) {
    const y = base + row * 240;
    const clash = draft.value.nodes.some(
      (n) => Math.abs(n.position.x - x) < 150 && Math.abs(n.position.y + height / 2 - y) < 140
    );
    if (!clash) return y;
  }
  return base + 6 * 240;
}

function createAgentSilent(refId: string, x?: number, y?: number): ApplyResult & { nodeId?: string } {
  const a = getAgent(refId);
  if (!a) return { ok: false, reason: `未找到 Agent：${refId}` };
  const node = makeNode({
    type: 'agent', refId,
    position: { x: x ?? 160, y: y ?? findFreeY(x ?? 160) },
    config: { selectedTools: [] },
  });
  draft.value.nodes.push(node);
  markDirty();
  return { ok: true, nodeId: node.id };
}

const wfController: WorkflowAssistantController = {
  clearAll() {
    draft.value = newEmptyDraft(props.projectName, draft.value.name);
    dirty.value = true;
    clearSelection();
  },
  loadSample() {
    applySample();
  },
  runCheck() {
    return validateDraft(draft.value);
  },
  addAgent(refId) {
    const existing = findAgentNodeByRef(refId);
    if (existing) return { ok: true, nodeId: existing.id, detail: '画布上已有该 Agent 的节点，直接复用' };
    return createAgentSilent(refId);
  },
  addSkill(refId) {
    const s = getSkill(refId);
    if (!s) return { ok: false, reason: `未找到 Skill：${refId}` };
    const existing = findSkillNodeByRef(refId);
    if (existing) return { ok: true, nodeId: existing.id, detail: '画布上已有该 Skill 的节点，直接复用' };
    const node = makeNode({
      type: 'skill', refId, refVersion: s.version,
      position: { x: 420, y: findFreeY(420) + 220 },
    });
    draft.value.nodes.push(node);
    markDirty();
    return { ok: true, nodeId: node.id };
  },
  addReview(label) {
    const node = makeNode({
      type: 'human_review', label: label ?? null,
      position: { x: 640, y: findFreeY(640) },
      config: {},
    });
    draft.value.nodes.push(node);
    markDirty();
    return { ok: true, nodeId: node.id };
  },
  addBoundary(which) {
    const type = which === 'start' ? 'boundary_start' : 'boundary_end';
    if (draft.value.nodes.some((n) => n.type === type)) {
      return { ok: false, reason: `${which === 'start' ? '开始' : '结束'}（边界）已存在，最多一个` };
    }
    const node = makeNode({ type, position: { x: which === 'start' ? -40 : 1240, y: 300 } });
    draft.value.nodes.push(node);
    markDirty();
    return { ok: true };
  },
  chain(refIds, withBoundary) {
    let created = 0;
    // 空数组：把画布上现有 Agent/审核按 x 序串起来
    const ids: string[] = refIds.length
      ? refIds.map((r) => {
          const found = findAgentNodeByRef(r);
          if (found) return found.id;
          const res = createAgentSilent(r, 160 + created * 280);
          if (!res.ok) return '';
          created++;
          return res.nodeId!;
        }).filter(Boolean)
      : draft.value.nodes
          .filter((n) => n.type === 'agent' || n.type === 'human_review')
          .sort((a, b) => a.position.x - b.position.x)
          .map((n) => n.id);
    if (ids.length < 2) {
      return { ok: false, created: 0, connected: 0, reason: refIds.length ? '链条至少需要两个有效 Agent' : '画布上可串联的 Agent/审核不足两个' };
    }
    let connected = 0;
    let failed: string | undefined;
    for (let i = 0; i + 1 < ids.length; i++) {
      const r = connectNodes(ids[i], ids[i + 1], 'execution');
      if (!r.ok) { failed = r.reason; break; }
      if (!r.skipped) connected++;
    }
    if (failed) return { ok: false, created, connected, reason: failed, detail: `已连 ${connected} 条后中断` };
    // 边界：开始→首节点，末节点→结束
    if (withBoundary) {
      const first = draft.value.nodes.find((n) => n.id === ids[0]);
      const last = draft.value.nodes.find((n) => n.id === ids[ids.length - 1]);
      if (first && !draft.value.edges.some((e) => e.type === 'execution' && e.target === first.id)) {
        let start = draft.value.nodes.find((n) => n.type === 'boundary_start');
        if (!start) {
          start = makeNode({ type: 'boundary_start', position: { x: first.position.x - 200, y: first.position.y } });
          draft.value.nodes.push(start);
          created++;
        }
        connectNodes(start.id, first.id, 'execution');
      }
      if (last && !draft.value.edges.some((e) => e.type === 'execution' && e.source === last.id)) {
        let end = draft.value.nodes.find((n) => n.type === 'boundary_end');
        if (!end) {
          end = makeNode({ type: 'boundary_end', position: { x: last.position.x + 240, y: last.position.y } });
          draft.value.nodes.push(end);
          created++;
        }
        connectNodes(last.id, end.id, 'execution');
      }
    }
    markDirty();
    return { ok: true, created, connected };
  },
  insertReviews(afterRef, beforeRef, count, label) {
    const afterNode = findAgentNodeByRef(afterRef);
    if (!afterNode) return { ok: false, reason: `画布上没有「${getAgent(afterRef)?.name ?? afterRef}」的节点，先把它加进画布` };
    const beforeNode = beforeRef ? findAgentNodeByRef(beforeRef) : undefined;
    if (beforeRef && !beforeNode) return { ok: false, reason: `画布上没有「${getAgent(beforeRef)?.name ?? beforeRef}」的节点` };
    // 插入点：before 节点，或 after 当前执行出线的目标
    let targetNode = beforeNode;
    if (!targetNode) {
      const outEdge = draft.value.edges.find((e) => e.type === 'execution' && e.source === afterNode.id);
      targetNode = outEdge ? draft.value.nodes.find((n) => n.id === outEdge.target) : undefined;
      if (targetNode && (targetNode.type === 'boundary_end' || targetNode.type === 'human_review' || targetNode.type === 'agent')) {
        // 可插入；先摘掉 after→target 的旧边
        draft.value.edges = draft.value.edges.filter((e) => !(e.type === 'execution' && e.source === afterNode.id && e.target === targetNode!.id));
      } else {
        targetNode = undefined;
      }
    } else {
      // after→before 旧边（如存在）摘除
      draft.value.edges = draft.value.edges.filter((e) => !(e.type === 'execution' && e.source === afterNode.id && e.target === targetNode!.id));
    }
    // 摆放：after 与 target 之间（无 target 则排在其右侧）
    const x0 = afterNode.position.x;
    const x1 = targetNode?.position.x ?? afterNode.position.x + 280 * (count + 1);
    const y = targetNode ? Math.min(afterNode.position.y, targetNode.position.y) - 200 : afterNode.position.y - 200;
    const ids: string[] = [];
    let lastFailure: string | undefined;
    for (let i = 0; i < count; i++) {
      const node = makeNode({
        type: 'human_review',
        label: count > 1 ? `${label ?? '人工审核'} #${i + 1}` : label ?? null,
        position: { x: Math.round(x0 + ((x1 - x0) * (i + 1)) / (count + 1)), y },
        config: {
          targetSourceNodeId: null,
          targetArtifact: afterNode.refId ? (getAgent(afterNode.refId)?.artifacts[0] ?? '') : '',
        },
      });
      draft.value.nodes.push(node);
      ids.push(node.id);
    }
    // 连线：after→r1→…→rN→target
    const seq = [afterNode.id, ...ids, ...(targetNode ? [targetNode.id] : [])];
    let connected = 0;
    for (let i = 0; i + 1 < seq.length; i++) {
      const r = connectNodes(seq[i], seq[i + 1], 'execution');
      if (!r.ok) { lastFailure = r.reason; break; }
      if (!r.skipped) connected++;
    }
    markDirty();
    if (lastFailure) return { ok: false, reason: lastFailure, detail: `审核节点已加入但连线中断（已连 ${connected} 条）` };
    return { ok: true, nodeId: ids[ids.length - 1], detail: `新建 ${count} 道审核 / ${connected} 条连线` };
  },
  bind(skillRefId, agentRefId) {
    const agentNode = findAgentNodeByRef(agentRefId)
      ?? (() => { const r = createAgentSilent(agentRefId); return r.ok ? draft.value.nodes.find((n) => n.id === r.nodeId) : undefined; })();
    if (!agentNode) return { ok: false, reason: `无法创建 Agent 节点：${agentRefId}` };
    const skill = getSkill(skillRefId);
    if (!skill) return { ok: false, reason: `未找到 Skill：${skillRefId}` };
    let skillNode = findSkillNodeByRef(skillRefId);
    if (!skillNode) {
      const r = wfController.addSkill(skillRefId);
      if (!r.ok) return r;
      skillNode = draft.value.nodes.find((n) => n.id === r.nodeId);
    }
    if (!skillNode) return { ok: false, reason: 'Skill 节点创建失败' };
    const r = connectNodes(skillNode.id, agentNode.id, 'skill_binding');
    if (!r.ok) return r;
    return {
      ok: true,
      detail: r.skipped
        ? '该绑定已存在'
        : `已绑定 ${skill.name} v${skill.version} → ${getAgent(agentRefId)?.name ?? agentRefId}`,
    };
  },
  layoutRefit() {
    scheduleFitView();
  },
};
</script>

<style lang="scss" scoped>
.wf-studio {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--bg-root);
  min-width: 0;
}

/* ---------- 顶栏 ---------- */
.wf-topbar {
  height: 52px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-root);
  overflow-x: auto;
  overflow-y: hidden;

  &::-webkit-scrollbar { height: 0; }
}
.wf-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}
.wf-name {
  width: 200px;
  height: 30px;
  padding: 0 10px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text-primary);
  font-size: 13px;
  outline: none;

  &:focus { border-color: var(--border-hover); }
}
.wf-spacer { flex: 1; min-width: 8px; }

.wf-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  line-height: 20px;
  padding: 0 8px;
  border-radius: 10px;
  border: 1px solid var(--border);
  color: var(--text-muted);
  white-space: nowrap;

  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--text-faint);
    flex-shrink: 0;
  }
  &.chip-warn { color: var(--warning); border-color: rgba(234, 179, 8, 0.35);
    .dot { background: var(--warning); } }
  &.chip-ok { color: #4ade80; border-color: rgba(74, 222, 128, 0.3);
    .dot { background: #4ade80; } }
  &.chip-blue { color: #60a5fa; border-color: rgba(96, 165, 250, 0.35); }
}

.check-ok { border-color: rgba(74, 222, 128, 0.35) !important; color: #4ade80 !important; }
.check-bad { border-color: rgba(239, 68, 68, 0.45) !important; color: var(--error) !important; }
.run-disabled { opacity: 0.55; }
.assistant-toggle.assistant-on {
  color: var(--text-primary);
  background: var(--bg-hover);
}
.run-short { display: none; }

.wf-legend {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;

  .lg-item { display: inline-flex; align-items: center; gap: 5px; }
  .lg-line {
    display: inline-block;
    width: 22px;
    height: 0;
    &.exec { border-top: 2px solid #7a7a7a; }
    &.bind { border-top: 2px dashed #60a5fa; }
  }
}

/* ---------- 主区 ---------- */
.wf-main {
  flex: 1;
  display: flex;
  min-height: 0;
}

/* ---------- 左：组件库 ---------- */
.wf-lib {
  width: 226px;
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--bg-root);
}
.lib-search {
  margin: 10px 10px 6px;
  width: auto;
}
.input {
  height: 30px;
  padding: 0 10px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text-primary);
  font-size: 12px;
  outline: none;
  width: 100%;

  &:focus { border-color: var(--border-hover); }
  &::placeholder { color: var(--text-faint); }
}
textarea.input { height: auto; padding: 7px 10px; resize: vertical; font-family: inherit; line-height: 1.5; }
select.input { appearance: auto; }

.lib-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 0 10px 10px;
}
.lib-group { margin-top: 10px; }
.lib-group-title {
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.06em;
  color: var(--text-faint);
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 2px 6px;

  .cnt {
    font-size: 10px;
    color: var(--text-faint);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0 5px;
    line-height: 14px;
  }
}
.lib-item {
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  padding: 7px 9px;
  margin-bottom: 6px;
  cursor: grab;
  transition: border-color 0.15s ease, background-color 0.15s ease;

  &:hover { border-color: var(--border-hover); background: var(--bg-hover); }
  &:active { cursor: grabbing; }
}
.lib-skill { border-color: rgba(96, 165, 250, 0.25); }
.lib-review { border-color: rgba(234, 179, 8, 0.25); }
.li-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}
.li-name {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.li-id {
  font-size: 10px;
  color: var(--text-faint);
  flex-shrink: 0;
}
.li-sub {
  font-size: 10px;
  color: var(--text-muted);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.lib-hint {
  font-size: 10px;
  color: var(--text-faint);
  line-height: 1.5;
  padding: 2px 2px 0;
}
.lib-boundary-row {
  display: flex;
  gap: 6px;
  margin-bottom: 4px;

  .btn { flex: 1; }
}
.lib-foot {
  flex-shrink: 0;
  border-top: 1px solid var(--border);
  padding: 8px 10px;
  font-size: 10px;
  color: var(--text-faint);
  line-height: 1.5;
}

/* ---------- 中：画布 ---------- */
.wf-canvas-wrap {
  flex: 1;
  position: relative;
  min-width: 0;
  background: var(--bg-root);
}
.wf-flow { width: 100%; height: 100%; }

/* 背景点阵颜色随主题（SVG 属性不支持 CSS var，经样式表覆盖） */
.wf-flow :deep(.vue-flow__background circle) {
  fill: var(--border-hover);
}

.wf-canvas-controls {
  position: absolute;
  right: 14px;
  bottom: 14px;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 2px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 3px;
}
.ctrl-btn {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  border-radius: 4px;
  color: var(--text-muted);
  font-size: 13px;
  cursor: pointer;

  &:hover { background: var(--bg-hover); color: var(--text-primary); }
}
.ctrl-zoom {
  font-size: 11px;
  color: var(--text-muted);
  padding: 0 4px;
  min-width: 38px;
  text-align: center;
}

.wf-empty-guide {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  pointer-events: none;
  font-size: 13px;
  color: var(--text-muted);
  text-align: center;

  p { margin: 0; }
  .guide-link {
    pointer-events: auto;
    color: var(--text-primary);
    text-decoration: underline;
    cursor: pointer;
  }
}

/* ---------- 右：配置面板 ---------- */
.wf-panel {
  width: 288px;
  flex-shrink: 0;
  border-left: 1px solid var(--border);
  background: var(--bg-root);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
}
.panel-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px 10px;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--bg-root);
  z-index: 2;

  .panel-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .panel-id {
    font-size: 10px;
    color: var(--text-faint);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1px 5px;
    margin-left: auto;
    flex-shrink: 0;
  }
}
.panel-body {
  padding: 12px 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 9px;
}
.kv {
  display: flex;
  gap: 8px;
  align-items: baseline;

  .k {
    width: 56px;
    flex-shrink: 0;
    font-size: 11px;
    color: var(--text-faint);
  }
  .v {
    font-size: 12px;
    color: var(--text-secondary);
    min-width: 0;
    word-break: break-all;

    &.tiny { font-size: 10px; color: var(--text-faint); }
  }
  .arrow { color: var(--text-muted); }
}
.p-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary);
}
.chip-row { display: flex; gap: 6px; flex-wrap: wrap; }
.sec-label {
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.06em;
  color: var(--text-faint);
  padding-top: 6px;
  border-top: 1px solid var(--border);
  margin-top: 4px;
}
.stat-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  padding: 2px 0;

  .stat {
    font-size: 11px;
    color: var(--text-muted);

    b {
      font-size: 14px;
      color: var(--text-primary);
      margin-right: 4px;
      font-variant-numeric: tabular-nums;
    }
  }
}
.check-ok-line {
  font-size: 12px;
  color: #4ade80;
}
.issue-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  max-height: 260px;
  overflow-y: auto;
}
.issue {
  display: flex;
  gap: 7px;
  align-items: flex-start;
  text-align: left;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 7px 9px;
  cursor: pointer;
  transition: border-color 0.15s ease;

  &:hover { border-color: var(--border-hover); }

  .iss-badge { font-size: 11px; line-height: 1.5; flex-shrink: 0; }
  .iss-text { font-size: 11px; line-height: 1.5; color: var(--text-secondary); }

  &.error .iss-badge { color: var(--error); }
  &.warning .iss-badge { color: var(--warning); }
}
.sem-note {
  margin: 0;
  font-size: 11px;
  line-height: 1.7;
  color: var(--text-muted);

  b { color: var(--text-secondary); }
  &.tiny { font-size: 10px; }
}
.danger-link {
  align-self: flex-start;

  &:hover:not(:disabled) { color: var(--error); border-color: rgba(239, 68, 68, 0.4); }
}
.unset-hint {
  margin: 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-faint);

  &.warn { color: var(--warning); }
}

.tool-actions {
  display: flex;
  align-items: center;
  gap: 6px;

  .tool-count { font-size: 11px; color: var(--text-muted); margin-right: auto; }
}
.tool-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 240px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  background: var(--bg-surface);
}
.tool-item {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 11px;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 2px 0;

  input { accent-color: var(--text-primary); }
  span { word-break: break-all; }

  &:hover { color: var(--text-primary); }
}

.bind-table {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.bind-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  padding: 5px 8px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;

  .bind-agent { color: var(--text-primary); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bind-role { color: #60a5fa; }
  .bind-pri { color: var(--text-faint); }
}

.field {
  display: flex;
  flex-direction: column;
  gap: 5px;

  .f-k { font-size: 11px; color: var(--text-faint); }
}

.artifact-list {
  margin: 0;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 3px;

  li { font-size: 11px; line-height: 1.6; color: var(--text-secondary); word-break: break-all; }
}
.evidence {
  margin: 0;
  font-size: 10px;
  line-height: 1.6;
  color: var(--text-faint);
  word-break: break-all;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 7px 9px;
}

/* ---------- 按钮（与 scene-system 同规格）---------- */
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
  white-space: nowrap;
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

/* ---------- 响应式（窄窗口）---------- */
@media (max-width: 1080px) {
  .wf-lib { width: 190px; }
  .wf-panel { width: 250px; }
  .wf-chat { width: 252px; }
  .wf-name { width: 140px; }
  .wf-legend { display: none; }
  // 顶栏允许换行，避免按钮被视口截断
  .wf-topbar {
    height: auto;
    min-height: 52px;
    flex-wrap: wrap;
    padding: 6px 16px;
    row-gap: 6px;
    overflow: visible;
  }
  .run-full { display: none; }
  .run-short { display: inline; }
}
@media (max-width: 860px) {
  .wf-lib { width: 168px; }
  .wf-panel { width: 224px; }
}
</style>
