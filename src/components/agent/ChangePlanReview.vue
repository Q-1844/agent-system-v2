<!--
  AI-7B Change Plan Review（§95/§96/§97/§98/§77）。
  Team Chat 内的结构化方案审查卡（不把完整 Diff 塞进单条消息）。
  - Requested / Related / Will remain unchanged / Impact / Execution order（§73/§96）
  - Visual changes tabs：Story / Characters / Scenes / Storyboard（§67-§70，来自 preview.aggregate_diff）
  - Needs your decision：选项按钮（§35/§93）
  - [请求修改] [拒绝] [批准此方案]（§77 措辞：批准≠执行；helper 文案明示当前阶段不修改项目）
  批准仅持久化确认 + 置 approved_for_execution，后端 STOP（§76/§119）。
-->
<template>
  <div class="cpr" v-if="cs.session">
    <!-- 状态行 -->
    <div class="cpr-status">
      <span class="st-label" :class="'st-' + cs.session.status">{{ statusLabel }}</span>
      <span v-if="cs.session.error" class="st-err" :title="cs.session.error">{{ cs.session.error.slice(0, 120) }}</span>
      <button v-if="canResume" class="btn btn-ghost" :disabled="cs.busy" @click="cs.resume()">推进</button>
      <!-- AI-7D：proposal 阶段（blocked/awaiting/routing/consulting 等）也能放弃——
           之前拒绝按钮只在 ready_for_review 块内，blocked 会话无法拒绝，卡死无逃生口。 -->
      <button v-if="canReject" class="btn btn-ghost" :disabled="cs.busy" @click="cs.reject()">拒绝</button>
    </div>

    <!-- 需决策（§35）-->
    <div v-if="cs.needsDecision && cs.plan" class="cpr-decisions">
      <div class="pc-sec-t">需要你的决策</div>
      <div v-for="dec in pendingDecisions" :key="dec.decision_id" class="dec-block">
        <div class="dec-q">{{ dec.question }}</div>
        <div class="dec-opts">
          <button v-for="o in dec.options" :key="o.option_id"
                  class="btn btn-opt" :disabled="cs.busy"
                  @click="cs.submitDecision(dec.decision_id, o.option_id)">
            <span class="opt-label">{{ o.label }}</span>
            <span v-if="o.description" class="opt-desc">{{ o.description }}</span>
          </button>
        </div>
      </div>
    </div>

    <!-- 方案审查（ready_for_review）-->
    <template v-if="cs.readyForReview && cs.plan">
      <div class="cpr-card">
        <div class="cpr-title">变更方案 · Ready for review</div>

        <div class="pc-sec" v-if="directItems.length">
          <div class="pc-sec-t">你要求修改</div>
          <div v-for="(d, i) in directItems" :key="'d' + i" class="pc-item">
            • {{ d.summary }} <span class="pc-mod">[{{ ownerLabel(d.owner_agent_id) }}]</span>
          </div>
        </div>
        <div class="pc-sec" v-if="relatedItems.length">
          <div class="pc-sec-t">牵连修改</div>
          <div v-for="(d, i) in relatedItems" :key="'r' + i" class="pc-item">
            • {{ d.summary }} <span class="pc-mod">[{{ ownerLabel(d.owner_agent_id) }}]</span>
          </div>
        </div>
        <div class="pc-sec" v-if="cs.plan.unchanged_scope?.length">
          <div class="pc-sec-t">保持不变</div>
          <div v-for="(u, i) in cs.plan.unchanged_scope" :key="'u' + i" class="pc-item">
            • {{ u.entity_id || u.module }}
          </div>
        </div>

        <!-- Visual changes tabs（§67-§70）-->
        <div class="cpr-diff" v-if="diffEntries.length">
          <div class="pc-sec-t">视觉变化</div>
          <div class="diff-tabs">
            <button v-for="t in diffTabs" :key="t.key"
                    class="dt-btn" :class="{ on: activeTab === t.key }"
                    @click="activeTab = t.key">{{ t.label }} ({{ t.count }})</button>
          </div>
          <div class="diff-list">
            <div v-for="(d, i) in tabEntries" :key="i" class="diff-row" :class="'dk-' + d.kind">
              <span class="d-kind">{{ kindLabel(d.kind) }}</span>
              <span class="d-entity">{{ d.entity }} {{ d.id }}</span>
              <span v-if="d.field" class="d-field">.{{ d.field }}</span>
              <span v-if="d.before !== undefined" class="d-before">{{ truncate(d.before) }}</span>
              <span v-if="d.after !== undefined" class="d-after">→ {{ truncate(d.after) }}</span>
            </div>
          </div>
        </div>

        <!-- Impact（§71）-->
        <div class="cpr-impact" v-if="impactSummary">
          <div class="pc-sec-t">影响</div>
          <div class="imp-grid">
            <div v-for="(v, k) in impactSummary" :key="k" class="imp-cell">
              <span class="imp-k">{{ impactKeyLabel(k) }}</span><span class="imp-v">{{ v }}</span>
            </div>
          </div>
        </div>

        <!-- Execution order（§38/§98）-->
        <div class="cpr-steps" v-if="cs.plan.steps?.length">
          <div class="pc-sec-t">执行顺序</div>
          <div class="steps">
            <span v-for="(s, i) in cs.plan.steps" :key="s.step_id" class="step">
              <span v-if="i > 0" class="step-arrow">→</span>
              <span class="step-mod">{{ ownerLabel(s.owner_agent_id) }}</span>
              <span class="step-par" v-if="s.parallel_group">{{ s.parallel_group === cs.plan.steps[0]?.parallel_group ? '∥' : '' }}</span>
            </span>
          </div>
        </div>

        <div class="cpr-badge">preview_only · applyable=false（AI-7B 不修改项目）</div>
      </div>

      <!-- 决策按钮（§77）-->
      <div class="cpr-actions">
        <button class="btn btn-ghost" :disabled="cs.busy" @click="onRequestChanges">请求修改</button>
        <button class="btn btn-ghost" :disabled="cs.busy" @click="cs.reject()">拒绝</button>
        <button class="btn btn-primary" :disabled="cs.busy" @click="onApprove">批准此方案</button>
      </div>
      <div class="cpr-helper">批准只会保存你的确认。当前阶段不会立即修改项目或重新生成素材。</div>
    </template>

    <!-- 已批准（AI-7C 执行）-->
    <div v-if="cs.session.status === 'approved_for_execution'" class="cpr-approved">
      <div class="ap-title">方案已批准</div>

      <!-- 未执行：Execute 入口（§3/§15：批准 ≠ apply；执行需显式动作）-->
      <template v-if="!cs.execution">
        <div class="ap-sub">此批准尚未修改项目。执行需显式动作（§3：批准 ≠ apply）。</div>
        <!-- L6/§7.5：消费后端 eligibility（stale/blocked 预呈现，未点 Execute 即可知） -->
        <div v-if="execEligibilityReason" class="exec-eligibility"
             :class="{ stale: cs.eligibility?.eligibility === 'stale' }">
          {{ execEligibilityReason }}
        </div>
        <div class="exec-mode">
          <label class="exec-mode-label">制作模式</label>
          <select v-model="execMode" class="input exec-mode-select">
            <option value="affected_production">应用 + 再生受影响制作（默认）</option>
            <option value="creative_only">仅应用创作变更（不生成素材）</option>
          </select>
        </div>
        <div class="cpr-actions">
          <button class="btn btn-primary" :disabled="cs.busy || !canExecute" @click="onExecute">执行计划</button>
          <button v-if="cs.eligibility?.eligibility === 'stale'" class="btn btn-ghost" :disabled="cs.busy"
                  @click="onRequestChanges">重新提案</button>
        </div>
        <div class="cpr-helper">执行将重验批准 → 暂存已批准操作 → 原子提交创作变更。制作模式=仅创作时提交后即止（§66）。</div>
      </template>

      <!-- 执行中/终态：timeline + 前后提交措辞（§79）-->
      <template v-else>
        <div class="exec-timeline">
          <div v-for="s in stageTimeline" :key="s.key" class="exec-stage"
               :class="{ done: s.state === 'done', current: s.state === 'current', blocked: cs.executionBlocked }">
            <span class="exec-mark">{{ s.state === 'done' ? '✓' : s.state === 'current' ? '●' : '○' }}</span>
            <span class="exec-label">{{ s.label }}</span>
          </div>
        </div>
        <!-- §79 前后提交区分；AI-7C-H §十一：正在停止…/已取消 来自后端持久真相 -->
        <div v-if="cs.executionStopping" class="exec-state stopping">
          正在停止当前任务…（已提交的创作变更保留，生产不再继续）
        </div>
        <div v-else-if="cs.execution?.status === 'cancelled'" class="exec-state cancelled">
          已取消。{{ cs.creativeCommitted ? '已提交的创作变更保留（§38）。' : 'canonical 未动（§38）。' }}
        </div>
        <div v-else-if="!cs.creativeCommitted" class="exec-state pre-commit">
          尚未提交任何项目变更（§79）。{{ cs.executionBlocked ? '执行受阻：' + (cs.execution.blocker?.message || cs.execution.blocker?.code) : '' }}
        </div>
        <div v-else class="exec-state post-commit">
          创作变更已提交（§79）。{{ cs.executionDone ? '执行完成。' : '制作尚未完成。' }}
        </div>
        <!-- blocker 详情 -->
        <div v-if="cs.executionBlocked" class="exec-blocker">
          <strong>受阻：</strong>{{ cs.execution.blocker?.message || cs.execution.blocker?.code }}
          <span v-if="cs.execution.blocker?.code === 'PROJECT_CHANGED_AFTER_APPROVAL'"> — 需重新提案/审批（§81，不可 rebasing resume）</span>
        </div>
        <!-- resume / cancel（§38/§81）-->
        <div class="cpr-actions">
          <!-- M5/§81：stale-blocked（PROJECT_CHANGED_AFTER_APPROVAL）不可 rebasing resume，
               不显"尝试恢复"以免误导；改导向重新提案/审批。其余 blocked 类型仍可尝试恢复。 -->
          <button v-if="cs.executionBlocked && !staleBlocked" class="btn btn-ghost" :disabled="cs.busy"
                  @click="cs.resumeExecution()">尝试恢复</button>
          <button v-if="staleBlocked" class="btn btn-primary" :disabled="cs.busy"
                  @click="onRequestChanges">重新提案</button>
          <button class="btn btn-ghost" :disabled="cs.busy || cs.executionDone || cs.executionStopping" @click="cs.cancelExecution()">取消</button>
        </div>
        <div class="cpr-helper">
          {{ cs.creativeCommitted ? '取消不会回滚已提交的创作变更（§38）。' : '取消将安全停止，canonical 不动（§38）。' }}
        </div>
      </template>
    </div>

    <!-- 请求修改输入 -->
    <div v-if="showFeedback" class="cpr-feedback">
      <textarea v-model="feedbackText" class="input fb-input" rows="2"
                placeholder="如：保留长发，但不要改 Scene 55"></textarea>
      <div class="fb-actions">
        <button class="btn btn-ghost" @click="showFeedback = false">取消</button>
        <button class="btn btn-primary" :disabled="cs.busy || !feedbackText.trim()"
                @click="doRequestChanges">提交修改请求</button>
      </div>
    </div>

    <div v-if="cs.error" class="chat-err">{{ cs.error }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { useChangeSessionStore } from '@/store/changeSession';
import type { ChangePlanItem } from '@/api';

const cs = useChangeSessionStore();

const activeTab = ref('story');
const showFeedback = ref(false);
const feedbackText = ref('');
// AI-7D D2：execMode 默认从 cs.preferredExecMode（bridge 检测"只…不改"→creative_only），
// 避免"只改剧本却级联改视频"。用户仍可手动改下拉。
const execMode = ref<'affected_production' | 'creative_only'>(cs.preferredExecMode);
watch(() => cs.preferredExecMode, (m) => { execMode.value = m; });

// AI-7C §86 execution timeline：据 execution.stage 推导各阶段 done/current/pending
const STAGE_ORDER = [
  'revalidate', 'workspace_create', 'creative_replay', 'workspace_verify',
  'canonical_commit', 'character_sync', 'production_planning',
  'production_prepare', 'production', 'compose', 'done',
] as const;
const STAGE_LABELS: Record<string, string> = {
  revalidate: '重验批准', workspace_create: '建执行 Workspace', creative_replay: '暂存创作变更',
  workspace_verify: '校验预览一致', canonical_commit: '原子提交创作变更',
  character_sync: '同步角色参考图', production_planning: '准备制作计划',
  production_prepare: '冻结制作输入', production: '生成受影响素材',
  compose: '合成', done: '完成',
};
const stageTimeline = computed(() => {
  const cur = cs.execution?.stage;
  const idx = cur ? STAGE_ORDER.indexOf(cur as any) : -1;
  return STAGE_ORDER.map((k, i) => ({
    key: k,
    label: STAGE_LABELS[k] || k,
    state: i < idx ? 'done' : i === idx ? 'current' : 'pending',
  }));
});

function onExecute() { cs.execute(execMode.value); }

const statusLabel = computed(() => {
  const map: Record<string, string> = {
    routing: '路由中', consulting: '咨询中…', analyzing_dependencies: '分析依赖…',
    awaiting_user_decision: '需决策', synthesizing_plan: '综合计划…',
    validating_plan: '校验中…', building_preview: '构建预览…',
    ready_for_review: '待审查', approved_for_execution: '已批准',
    rejected: '已拒绝', stale: '已过期', blocked: '阻塞', failed: '失败', cancelled: '已取消',
  };
  return map[cs.session?.status || ''] || cs.session?.status || '';
});
const canResume = computed(() =>
  cs.session && ['blocked', 'awaiting_user_decision', 'routing', 'consulting'].includes(cs.session.status));
// AI-7D：proposal 阶段（非 ready/非 approved/非终态）可拒绝。ready 在下方审查块自有拒绝按钮；
// approved 走执行/取消（reject_plan 会与运行中 execution 冲突，故排除）。blocked 等靠此按钮放弃。
const canReject = computed(() =>
  cs.session && !cs.readyForReview && cs.session.status !== 'approved_for_execution'
  && !['rejected', 'stale', 'failed', 'cancelled'].includes(cs.session.status));
// M5/§81：stale-blocked（项目批准后变化，不可 rebasing resume）→ 不显"尝试恢复"，导向重新提案
const staleBlocked = computed(() =>
  cs.executionBlocked && cs.execution?.blocker?.code === 'PROJECT_CHANGED_AFTER_APPROVAL');
// L6/§7.5：消费后端权威 eligibility（server truth wins）；unknown(null) 不阻断——后端幂等会 block 兜底
const canExecute = computed(() =>
  cs.approved && !cs.execution && (cs.eligibility === null || cs.eligibility?.eligibility === 'eligible'));
const execEligibilityReason = computed(() => {
  const e = cs.eligibility?.eligibility;
  if (e === 'stale') return '计划已过期：项目在批准后已变化，需重新提案/审批';
  if (e === 'blocked') return cs.eligibility?.reason || '执行受阻';
  if (e === 'invalid_approval') return '批准无效，请重新审批';
  if (e === 'already_started') return '执行已启动（见下方进度）';
  if (e === 'already_completed') return '执行已完成';
  return '';
});

const directItems = computed<ChangePlanItem[]>(() => cs.plan?.direct_change_items || []);
const relatedItems = computed<ChangePlanItem[]>(() => cs.plan?.downstream_change_items || []);

// decisions：7B-5 从 plan 端点暂不可得完整 list；awaiting 时 session.pending_decision_ids 有 id，
// 此处展示占位（待 7B-6 补 decisions list 端点后填充 options）。当前用一个合成决策。
const pendingDecisions = computed(() => {
  if (!cs.needsDecision || !cs.session) return [];
  return cs.session.pending_decision_ids.map((id: string) => ({
    decision_id: id, question: '需要你确认（详见 Team Chat 分析）',
    options: [{ option_id: 'a', label: '方案 A', description: '' },
              { option_id: 'b', label: '方案 B', description: '' }],
  }));
});

// Visual diff tabs（§67-§70）
const diffEntries = computed<any[]>(() => cs.plan?.preview?.aggregate_diff?.diff || []);
const diffTabs = computed(() => [
  { key: 'story', label: '故事', count: diffEntries.value.filter(d => d.entity === 'story').length },
  { key: 'character', label: '角色', count: diffEntries.value.filter(d => d.entity === 'character').length },
  { key: 'scene', label: '场景', count: diffEntries.value.filter(d => d.entity === 'scene').length },
  { key: 'shot', label: '分镜', count: diffEntries.value.filter(d => d.entity === 'shot').length },
]);
const tabEntries = computed(() => {
  const map: Record<string, string> = { story: 'story', character: 'character', scene: 'scene', shot: 'shot' };
  const want = map[activeTab.value];
  return diffEntries.value.filter(d => d.entity === want);
});

const impactSummary = computed<any>(() => cs.plan?.preview?.aggregate_impact?.summary || null);
function impactKeyLabel(k: string | number): string {
  const key = String(k);
  const map: Record<string, string> = {
    scenes_changed: '场景变更', shots_added: '新增镜头', shots_deleted: '删除镜头',
    shots_modified: '修改镜头', shots_moved: '移动镜头', plans_to_rebuild: '需重规划',
    shots_recommended_for_regeneration: '建议重新生成', continuity: '连续性',
    production_plan: '制作计划', existing_assets: '历史资产',
  };
  return map[key] || key;
}

function ownerLabel(owner: string): string {
  const map: Record<string, string> = {
    script_writer: '剧本', character_designer: '角色', director: '分镜',
    production_planner: '规划', shot_producer: '制作',
  };
  return map[owner] || owner;
}
function kindLabel(k: string): string {
  return { insert: '新增', delete: '删除', update: '修改', reorder: '重排' }[k] || k;
}
function truncate(v: any): string {
  if (v == null) return '';
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return String(s).length > 60 ? String(s).slice(0, 60) + '…' : String(s);
}

function onRequestChanges() { showFeedback.value = true; feedbackText.value = ''; }
async function doRequestChanges() {
  if (!feedbackText.value.trim()) return;
  await cs.requestChanges(feedbackText.value);
  showFeedback.value = false; feedbackText.value = '';
}
async function onApprove() { await cs.approve(); }
</script>

<style lang="scss" scoped>
.cpr { display: flex; flex-direction: column; gap: 10px; }
.cpr-status { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.st-label { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; background: var(--bg-hover); color: var(--text-secondary); }
.st-ready_for_review, .st-approved_for_execution { background: rgba(74,222,128,.15); color: #4ade80; }
.st-consulting, .st-analyzing_dependencies, .st-validating_plan, .st-building_preview, .st-synthesizing_plan { background: rgba(96,165,250,.15); color: #60a5fa; }
.st-awaiting_user_decision { background: rgba(251,191,36,.15); color: #fbbf24; }
.st-blocked, .st-failed, .st-stale, .st-rejected { background: rgba(248,113,113,.15); color: #f87171; }
.st-err { font-size: 11px; color: #f87171; }

.cpr-decisions { background: rgba(251,191,36,.08); border: 1px solid rgba(251,191,36,.3); border-radius: 8px; padding: 10px; }
.dec-block { margin-top: 6px; }
.dec-q { font-size: 12px; color: var(--text-primary); margin-bottom: 4px; }
.dec-opts { display: flex; gap: 6px; flex-wrap: wrap; }
.btn-opt { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; background: var(--bg-surface); border: 1px solid var(--border); padding: 6px 10px; }
.opt-label { font-size: 12px; font-weight: 600; }
.opt-desc { font-size: 10px; color: var(--text-muted); }

.cpr-card { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.cpr-title { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.pc-sec { display: flex; flex-direction: column; gap: 2px; }
.pc-sec-t { font-size: 10px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: .4px; margin-top: 2px; }
.pc-item { font-size: 12px; color: var(--text-secondary); }
.pc-mod { color: var(--text-muted); font-size: 10px; }

.diff-tabs { display: flex; gap: 4px; margin: 4px 0; }
.dt-btn { padding: 3px 8px; font-size: 11px; border-radius: 4px; background: none; border: 1px solid var(--border); color: var(--text-muted); cursor: pointer; }
.dt-btn.on { background: var(--accent); color: #fff; border-color: var(--accent); }
.diff-list { display: flex; flex-direction: column; gap: 2px; max-height: 180px; overflow-y: auto; }
.diff-row { display: flex; gap: 4px; align-items: baseline; font-size: 11px; padding: 2px 4px; border-radius: 3px; }
.d-kind { font-size: 10px; font-weight: 600; padding: 1px 4px; border-radius: 3px; }
.dk-insert .d-kind { background: rgba(74,222,128,.2); color: #4ade80; }
.dk-delete .d-kind { background: rgba(248,113,113,.2); color: #f87171; }
.dk-update .d-kind { background: rgba(96,165,250,.2); color: #60a5fa; }
.dk-reorder .d-kind { background: rgba(251,191,36,.2); color: #fbbf24; }
.d-entity { color: var(--text-primary); }
.d-field { color: var(--text-muted); }
.d-before { color: var(--text-muted); text-decoration: line-through; }
.d-after { color: var(--text-secondary); }

.imp-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 4px; }
.imp-cell { display: flex; justify-content: space-between; font-size: 11px; padding: 3px 6px; background: var(--bg-hover); border-radius: 4px; }
.imp-k { color: var(--text-muted); }
.imp-v { color: var(--text-secondary); font-weight: 500; }

.steps { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.step { display: inline-flex; align-items: center; gap: 3px; font-size: 11px; padding: 3px 8px; background: var(--bg-hover); border-radius: 10px; }
.step-arrow { color: var(--text-muted); }
.step-mod { color: var(--text-primary); font-weight: 500; }

.cpr-badge { font-size: 10px; color: var(--text-muted); }
.cpr-actions { display: flex; gap: 8px; justify-content: flex-end; }
.cpr-helper { font-size: 10px; color: var(--text-muted); text-align: right; }
.cpr-approved { text-align: center; padding: 16px; }
.ap-title { font-size: 14px; font-weight: 600; color: #4ade80; }
.ap-sub { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
.exec-eligibility { font-size: 11px; padding: 4px 8px; border-radius: 4px; background: rgba(96,165,250,.12); color: #60a5fa; margin-top: 6px; }
.exec-eligibility.stale { background: rgba(248,113,113,.12); color: #f87171; }

.cpr-feedback { background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px; }
.fb-input { width: 100%; resize: vertical; }
.fb-actions { display: flex; gap: 6px; justify-content: flex-end; margin-top: 6px; }

.chat-err { color: #f87171; font-size: 11px; }
.btn { padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 500; cursor: pointer; border: none; transition: all .15s; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn-primary { background: var(--accent); color: var(--accent-text, #fff); }
.btn-primary:hover:not(:disabled) { filter: brightness(1.1); }
.btn-ghost { background: none; color: var(--text-muted); }
.btn-ghost:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-secondary); }
</style>
