<template>
  <div class="run-panel">
    <div class="panel-head">
      <span class="panel-title">运行状态</span>
      <span v-if="run" class="panel-id mono-num">{{ shortRunId }}</span>
    </div>

    <div v-if="!run" class="panel-body">
      <p class="unset-hint">当前没有运行中的工作流。保存草稿后点「运行」启动一条生产链。</p>
    </div>

    <div v-else class="panel-body">
      <!-- run 总状态 -->
      <div class="kv">
        <span class="k">run 状态</span>
        <span class="v"><span class="run-chip" :class="runTone">{{ runStatusText }}</span></span>
      </div>
      <div class="kv"><span class="k">定义版本</span>
        <span class="v mono-num tiny">r{{ run.snapshot?.definitionRevision }} · {{ shortHash }}</span>
      </div>
      <div class="kv"><span class="k">文本模型</span>
        <span class="v tiny">{{ run.snapshot?.textModel || '?' }} · 思考{{ run.snapshot?.thinking ? '开启' : '未开启' }}</span>
      </div>
      <div v-if="run.error" class="unset-hint warn">{{ run.error }}</div>

      <div class="sec-label">执行链（按顺序）</div>
      <div class="run-nodes">
        <button
          v-for="nid in run.plan?.order || []"
          :key="nid"
          class="run-node"
          :class="nodeTone(run.nodes?.[nid])"
          :title="nodeTooltip(run.nodes?.[nid])"
          @click="$emit('locate', nid)"
        >
          <span class="rn-dot" />
          <span class="rn-main">
            <span class="rn-name">{{ nodeName(nid) }}</span>
            <span class="rn-status">{{ nodeStatusText(run.nodes?.[nid]) }}</span>
          </span>
          <span v-if="run.nodes?.[nid]?.error" class="rn-err">{{ run.nodes[nid].error }}</span>
        </button>
      </div>

      <!-- 待审核 -->
      <template v-for="rv in pendingReviews" :key="rv.requestId">
        <div class="sec-label">待人工审核 · 第 {{ rv.reviewIndex || '?' }} 道</div>
        <div class="review-card">
          <div class="rv-name">{{ rv.label || '人工审核' }}</div>
          <div class="kv"><span class="k">审核对象</span>
            <span class="v tiny">{{ rv.targetArtifact || rv.targetNodeId || '（上游产物）' }}</span>
          </div>
          <div v-if="rv.instructions" class="kv"><span class="k">审核说明</span>
            <span class="v tiny">{{ rv.instructions }}</span>
          </div>
          <div v-if="rv.reviewerLabel" class="kv"><span class="k">审核者</span>
            <span class="v tiny">{{ rv.reviewerLabel }}</span>
          </div>
          <div v-if="rv.artifactRefs?.length" class="kv">
            <span class="k">待审产物</span>
            <span class="v tiny">{{ artifactText(rv.artifactRefs) }}</span>
          </div>
          <!-- Phase 47 §4.6：需要明确选用时，从真实候选中选定精确版本 -->
          <template v-if="rv.selectionRequired">
            <div class="kv"><span class="k">需选用</span>
              <span class="v tiny">
                {{ selKindText(rv.selectionSpec?.kind) }}
                <template v-if="rv.selectionSpec?.versionId">（默认 {{ rv.selectionSpec.versionId }}）</template>
              </span>
            </div>
            <div v-if="rv.selectionCandidates?.length" class="sel-cands">
              <label v-for="c in rv.selectionCandidates" :key="c.versionId" class="sel-cand">
                <input
                  type="radio"
                  :name="`sel-${rv.requestId}`"
                  :value="c.versionId"
                  v-model="selDrafts[rv.requestId]"
                />
                <span class="mono-num tiny">{{ c.versionId }}</span>
                <span v-if="c.selected" class="run-chip ok tiny">当前已选</span>
                <span v-else-if="c.status" class="tiny muted">{{ c.status }}</span>
              </label>
            </div>
            <p v-else class="sem-note tiny">未解析到候选——可在决策时以 selectionOverride 指定精确版本。</p>
          </template>
          <textarea
            v-model="noteDrafts[rv.requestId]"
            class="input rv-note"
            rows="2"
            placeholder="审核意见（可留空）"
          />
          <div class="rv-actions">
            <button v-if="!rv.selectionRequired" class="btn btn-primary btn-sm" @click="$emit('decide', rv, 'approve', noteDrafts[rv.requestId] || '', {})">通过 · 继续</button>
            <button
              v-else
              class="btn btn-primary btn-sm"
              :disabled="!selDrafts[rv.requestId] && !(rv.selectionSpec?.versionId)"
              @click="approveAndSelect(rv)"
            >通过并选用 · 继续</button>
            <button class="btn btn-outline btn-sm danger-link" @click="$emit('decide', rv, 'reject', noteDrafts[rv.requestId] || '', {})">驳回 · 停止</button>
          </div>
          <p class="sem-note tiny">
            <template v-if="rv.selectionRequired">
              「通过并选用」会把上面选定的精确版本设为正式选片指针（与项目页选片入口同一服务）；
              两步各自落账，失败时可「继续」幂等重试。
            </template>
            <template v-else>
              审核通过仅许可流程继续；资产批准/选片/定版仍走项目页的正式入口。
            </template>
          </p>
        </div>
      </template>

      <!-- 已决审核历史 -->
      <template v-if="decidedReviews.length">
        <div class="sec-label">审核记录</div>
        <div v-for="rv in decidedReviews" :key="rv.requestId" class="review-done">
          <span class="rd-name">{{ rv.label || '人工审核' }}</span>
          <span class="run-chip" :class="rv.decision === 'approve' ? 'ok' : 'bad'">
            {{ rv.decision === 'approve' ? '已通过' : '已驳回' }}
          </span>
          <span v-if="rv.selectionRequired" class="run-chip tiny"
                :class="rv.selectionState === 'done' ? 'ok' : (rv.selectionState === 'failed' ? 'bad' : 'warn')">
            选用{{ selStateText(rv.selectionState) }}
          </span>
          <span v-if="rv.decisionNote" class="rd-note tiny">{{ rv.decisionNote }}</span>
        </div>
      </template>

      <div class="sec-label">操作</div>
      <div class="rv-actions">
        <button
          v-if="canContinue"
          class="btn btn-outline btn-sm"
          @click="$emit('continue', failedNodeId)"
        >{{ continueLabel }}</button>
        <button
          class="btn btn-outline btn-sm danger-link"
          :disabled="isTerminal"
          @click="$emit('stop')"
        >停止流程</button>
      </div>
      <p v-if="run.stopRequested && !isTerminal" class="sem-note tiny">
        已请求停止：不再派发下游；在途任务将按其真实能力收尾。
      </p>
      <p class="sem-note tiny">
        刷新页面后从这里恢复观察同一 run（后端为准）；草稿编辑不影响本运行使用的冻结定义。
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive } from 'vue';
import type { WorkflowRun, WorkflowRunNode, WorkflowReviewRequest } from '@/api';
import { shortAgentName } from './assistant';

const props = defineProps<{ run: WorkflowRun | null; getRefId: (nodeId: string) => string | null }>();
const emit = defineEmits<{
  (e: 'decide', rv: WorkflowReviewRequest, decision: 'approve' | 'reject', note: string,
   opts?: { withSelection?: boolean; selectionOverride?: { kind: string; entityId: string; versionId: string } | null }): void;
  (e: 'stop'): void;
  (e: 'continue', nodeId: string | null): void;
  (e: 'locate', nodeId: string): void;
}>();

const noteDrafts = reactive<Record<string, string>>({});
const selDrafts = reactive<Record<string, string>>({});

const SEL_KIND: Record<string, string> = {
  video_version: '视频版本（shot final_video）',
  sequence_render: '成片 render（final_render）',
  look_reference: 'Look 参考图批准',
  prop_reference: '道具参考图批准',
  composition_candidate: '构图候选批准',
  first_frame: '分镜首帧候选',
};
function selKindText(k?: string): string { return SEL_KIND[k || ''] || k || '—'; }
function selStateText(s?: string): string {
  return ({ none: '不适用', pending: '待执行', done: '完成', failed: '失败' } as Record<string, string>)[s || ''] || s || '—';
}
function approveAndSelect(rv: WorkflowReviewRequest) {
  const versionId = selDrafts[rv.requestId] || rv.selectionSpec?.versionId || '';
  if (!versionId) return;
  const entityId = rv.selectionSpec?.entityId || '';
  const kind = rv.selectionSpec?.kind || '';
  // entityId 为占位符（模板生成）时从候选所属对象推断：video_version 的 entityId 即 shotId，
  // 已在候选解析时按真实对象展开——这里仅透传用户选择的精确版本。
  emit('decide', rv, 'approve', noteDrafts[rv.requestId] || '',
       { withSelection: true, selectionOverride: { kind, entityId, versionId } });
}

const shortRunId = computed(() => (props.run ? props.run.runId.replace('run_', 'run ') : ''));
const shortHash = computed(() => {
  const h = props.run?.snapshot?.definitionHash as string | undefined;
  return h ? h.replace('sha256:', '').slice(0, 8) : '—';
});

const RUN_STATUS: Record<string, { text: string; tone: string }> = {
  running: { text: '运行中', tone: 'blue' },
  waiting_human: { text: '等待人工审核', tone: 'warn' },
  needs_reconciliation: { text: '需要核对', tone: 'warn' },
  failed: { text: '失败', tone: 'bad' },
  stopped: { text: '已停止', tone: 'gray' },
  rejected: { text: '审核驳回停止', tone: 'bad' },
  completed: { text: '已完成', tone: 'ok' },
};
const runStatusText = computed(() => RUN_STATUS[props.run?.status ?? '']?.text ?? props.run?.status);
const runTone = computed(() => RUN_STATUS[props.run?.status ?? '']?.tone ?? 'gray');
const isTerminal = computed(() => ['failed', 'stopped', 'rejected', 'completed'].includes(props.run?.status ?? ''));

const pendingReviews = computed(() => (props.run?.reviews || []).filter((r) => r.status === 'pending'));
const decidedReviews = computed(() => (props.run?.reviews || []).filter((r) => r.status !== 'pending'));

const NODE_STATUS: Record<string, string> = {
  pending: '待派发',
  dispatching: '派发中',
  running: '运行中',
  waiting_human: '等待人工',
  waiting_external: '等待外部任务',
  needs_reconciliation: '需要核对',
  failed: '失败',
  stopping: '停止请求中',
  stopped: '已停止',
  success: '已完成',
};
function nodeStatusText(n?: WorkflowRunNode): string {
  if (!n) return '—';
  return NODE_STATUS[n.status] ?? n.status;
}
function nodeTone(n?: WorkflowRunNode): string {
  if (!n) return '';
  const s = n.status;
  if (s === 'success') return 'ok';
  if (s === 'running' || s === 'dispatching') return 'blue';
  if (s === 'waiting_human' || s === 'needs_reconciliation') return 'warn';
  if (s === 'failed' || s === 'stopped') return 'bad';
  return '';
}
function nodeTooltip(n?: WorkflowRunNode): string {
  if (!n) return '';
  const parts = [`${NODE_STATUS[n.status] ?? n.status}`];
  for (const a of n.attempts || []) {
    if (a.taskRefs?.length) parts.push(`task: ${a.taskRefs.join(', ')}`);
  }
  const refs = n.handoff?.artifactRefs || [];
  if (refs.length) parts.push(`产物: ${refs.map((r: any) => `${r.kind}:${r.id}`).join(', ')}`);
  // Phase 47：输出（created/reused/selected）与缺项（诚实缺项不假成功）
  const outs = n.handoff?.outputs || [];
  if (outs.length) {
    parts.push(`输出: ${outs.slice(0, 6).map((o: any) =>
      `${o.kind}:${o.entityId}${o.versionId ? `@${o.versionId}` : ''}${o.disposition ? `(${o.disposition})` : ''}`).join(', ')}`);
  }
  const reused = n.handoff?.reusedRefs || [];
  if (reused.length) parts.push(`复用: ${reused.map((r: any) => `${r.kind}:${r.entityId}`).join(', ')}`);
  const missing = n.handoff?.missingRequirements || [];
  if (missing.length) parts.push(`缺项: ${missing.map((m: any) => m.reason || m.kind).join('；').slice(0, 200)}`);
  return parts.join('\n');
}
function nodeName(nid: string): string {
  const refId = props.getRefId(nid);
  if (!refId) {
    // 审核节点显示 label
    const rv = (props.run?.reviews || []).find((r) => r.nodeId === nid);
    const pn = props.run?.plan?.nodes?.[nid];
    return rv?.label || pn?.label || nid;
  }
  return shortAgentName(refId);
}
function artifactText(refs: { kind: string; id: string }[]): string {
  return refs.slice(0, 8).map((r) => `${r.kind}:${r.id}`).join('、') + (refs.length > 8 ? ` 等 ${refs.length} 项` : '');
}

const failedNodeId = computed<string | null>(() => {
  const order: string[] = props.run?.plan?.order || [];
  for (const nid of order) {
    const s = props.run?.nodes?.[nid]?.status;
    if (s === 'needs_reconciliation' || s === 'failed') return nid;
  }
  return null;
});
const canContinue = computed(() =>
  !isTerminal.value &&
  props.run?.status !== 'waiting_human' &&
  !!failedNodeId.value);
const continueLabel = computed(() =>
  props.run?.nodes?.[failedNodeId.value || '']?.status === 'needs_reconciliation'
    ? '核对后继续（新尝试）'
    : '重试该节点（新尝试）');
</script>

<style scoped lang="scss">
.run-panel { display: flex; flex-direction: column; min-height: 0; height: 100%; }
.panel-body { overflow-y: auto; min-height: 0; }
.run-chip {
  display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px;
  &.ok { background: rgba(74, 222, 128, 0.16); color: #4ade80; }
  &.bad { background: rgba(248, 113, 113, 0.16); color: #f87171; }
  &.warn { background: rgba(251, 191, 36, 0.16); color: #fbbf24; }
  &.blue { background: rgba(96, 165, 250, 0.16); color: #60a5fa; }
  &.gray { background: rgba(154, 154, 154, 0.16); color: #9a9a9a; }
}
.run-nodes { display: flex; flex-direction: column; gap: 6px; }
.run-node {
  display: flex; align-items: flex-start; gap: 8px; text-align: left;
  background: transparent; border: 1px solid var(--wf-border, #2a2a2a); border-radius: 8px;
  padding: 6px 8px; color: inherit; cursor: pointer; width: 100%;
  &:hover { border-color: #555; }
  .rn-dot { width: 8px; height: 8px; border-radius: 50%; background: #666; margin-top: 5px; flex: none; }
  .rn-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .rn-name { font-size: 13px; }
  .rn-status { font-size: 11px; color: #9a9a9a; }
  .rn-err { font-size: 11px; color: #f87171; margin-top: 2px; word-break: break-all; }
  &.ok .rn-dot { background: #4ade80; }
  &.ok .rn-status { color: #4ade80; }
  &.blue .rn-dot { background: #60a5fa; }
  &.blue .rn-status { color: #60a5fa; }
  &.warn .rn-dot { background: #fbbf24; }
  &.warn .rn-status { color: #fbbf24; }
  &.bad .rn-dot { background: #f87171; }
  &.bad .rn-status { color: #f87171; }
}
.review-card {
  border: 1px solid rgba(251, 191, 36, 0.4); border-radius: 8px; padding: 8px;
  display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px;
  .rv-name { font-size: 13px; font-weight: 600; }
  .rv-note { width: 100%; resize: vertical; }
  .rv-actions { display: flex; gap: 8px; }
  .sel-cands {
    display: flex; flex-direction: column; gap: 3px; max-height: 150px; overflow-y: auto;
    border: 1px solid var(--wf-border, #2a2a2a); border-radius: 6px; padding: 5px 7px;
    .sel-cand { display: flex; align-items: center; gap: 7px; cursor: pointer; font-size: 12px;
      input { accent-color: #60a5fa; } }
  }
  .muted { color: #9a9a9a; }
}
.review-done { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; flex-wrap: wrap;
  .rd-name { font-size: 12px; }
  .rd-note { color: #9a9a9a; flex-basis: 100%; }
}
.tiny { font-size: 11px; }
</style>
