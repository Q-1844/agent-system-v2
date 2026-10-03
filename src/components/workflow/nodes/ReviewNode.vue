<template>
  <div
    class="wf-node wf-review"
    :class="{ selected, error: d.hasError }"
    :title="d.errorTitles.join('\n')"
  >
    <Handle id="in" type="target" :position="Position.Left" class="wf-port port-exec">
      <span class="port-dot" />
    </Handle>
    <Handle id="out" type="source" :position="Position.Right" class="wf-port port-exec">
      <span class="port-dot" />
    </Handle>

    <div class="nd-head">
      <span class="nd-icon" v-html="ICON_REVIEW" />
      <div class="nd-titles">
        <span class="nd-name">{{ d.node.label || '人工审核' }}</span>
        <span class="nd-sub">人工审核 · 通过继续 / 驳回停止</span>
      </div>
    </div>

    <div class="nd-body">
      <div class="nd-io">
        <span class="io-k">对象</span>
        <span class="io-v" :class="{ unset: !targetArtifact }">{{ targetArtifact || '未指定审核对象' }}</span>
      </div>
      <div class="nd-io">
        <span class="io-k">审核者</span>
        <span class="io-v" :class="{ unset: !d.node.config.reviewer }">{{ d.node.config.reviewer || '未设置（草稿）' }}</span>
      </div>
      <div v-if="d.node.config.instructions" class="nd-io">
        <span class="io-k">说明</span>
        <span class="io-v" :title="d.node.config.instructions">{{ d.node.config.instructions }}</span>
      </div>
    </div>

    <div class="nd-foot">
      <span v-if="d.hasError" class="nd-badge badge-error">⚠ 校验错误{{ d.errorCount > 1 ? ` ×${d.errorCount}` : '' }}</span>
      <span v-else class="nd-badge badge-human">人审 {{ seqLabel }}</span>
      <span v-if="d.upstreamAgentName" class="nd-badge">上游：{{ d.upstreamAgentName }}</span>
    </div>

    <span class="port-label label-in">送审</span>
    <span class="port-label label-out">通过后</span>
    <button v-if="controller" class="nd-del" title="删除节点" @pointerdown.stop @mousedown.stop @click.stop="onDelete">✕</button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { Handle, Position } from '@vue-flow/core';
import type { WorkflowNode } from '../types';
import { useNodeDelete } from './useNodeDelete';

export interface ReviewNodeData {
  node: WorkflowNode;
  hasError: boolean;
  errorCount: number;
  errorTitles: string[];
  /** 执行链上的第几道审核（展示用） */
  reviewSeq?: number;
  /** 上游 Agent 展示名 */
  upstreamAgentName?: string;
}

const props = defineProps<{ data: ReviewNodeData; selected?: boolean; id?: string }>();
defineOptions({ inheritAttrs: false });
const d = computed(() => props.data);
const { controller, onDelete } = useNodeDelete(props);

const targetArtifact = computed(() => d.value.node.config.targetArtifact || '');
const seqLabel = computed(() => (d.value.reviewSeq ? `#${d.value.reviewSeq}` : ''));

const ICON_REVIEW =
  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
</script>

<style lang="scss" scoped>
.wf-node {
  position: relative;
  width: 224px;
  background: var(--bg-surface);
  border: 1px solid rgba(234, 179, 8, 0.35);
  border-radius: 8px;
  font-size: 12px;
  color: var(--text-primary);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
  animation: wf-pop-in 0.16s ease-out;
}

/* 新节点落场（点击添加/拖放入画布时的一次性入场） */
@keyframes wf-pop-in {
  from {
    opacity: 0;
    transform: scale(0.94);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

/* 悬停快捷删除（画布管理基本操作） */
.nd-del {
  position: absolute;
  top: -8px;
  right: -8px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1px solid var(--border);
  background: var(--bg-surface);
  color: var(--text-faint);
  font-size: 10px;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s ease, color 0.12s ease, border-color 0.12s ease;
  z-index: 3;

  &:hover {
    color: var(--error);
    border-color: var(--error);
  }
}
.wf-node:hover .nd-del {
  opacity: 1;
}
.wf-review {
  &.selected {
    border-color: var(--warning);
    box-shadow: 0 0 0 1.5px rgba(234, 179, 8, 0.7), 0 4px 16px rgba(0, 0, 0, 0.4);
  }
  &.error {
    border-color: var(--error);
    box-shadow: 0 0 0 1.5px var(--error);
  }
}

.nd-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px 7px;
  border-bottom: 1px solid var(--border);
}
.nd-icon {
  color: var(--warning);
  display: flex;
  flex-shrink: 0;
}
.nd-titles {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.nd-name {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.nd-sub {
  font-size: 10px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.nd-body {
  padding: 7px 10px 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.nd-io {
  display: flex;
  gap: 6px;
  align-items: baseline;
  min-width: 0;
}
.io-k {
  font-size: 10px;
  color: var(--text-faint);
  flex-shrink: 0;
  width: 30px;
}
.io-v {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;

  &.unset {
    color: var(--text-faint);
    font-style: italic;
  }
}

.nd-foot {
  display: flex;
  gap: 4px;
  padding: 0 10px 9px;
  flex-wrap: wrap;
}
.nd-badge {
  font-size: 10px;
  line-height: 16px;
  padding: 0 6px;
  border-radius: 8px;
  border: 1px solid var(--border);
  color: var(--text-faint);
  white-space: nowrap;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;

  &.badge-error {
    color: var(--error);
    border-color: rgba(239, 68, 68, 0.4);
    background: rgba(239, 68, 68, 0.12);
  }
  &.badge-human {
    color: var(--warning);
    border-color: rgba(234, 179, 8, 0.35);
    background: rgba(234, 179, 8, 0.1);
  }
}

.wf-port {
  width: 12px;
  height: 12px;
  border: none;
  background: transparent;
  display: flex;
  align-items: center;
  justify-content: center;

  .port-dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    border: 1.5px solid var(--bg-root);
    background: var(--text-muted);
    transition: transform 0.12s ease, box-shadow 0.12s ease;
  }
  &:hover .port-dot {
    transform: scale(1.25);
    box-shadow: 0 0 6px rgba(229, 229, 229, 0.5);
  }
}
.port-label {
  position: absolute;
  font-size: 10px;
  color: var(--text-faint);
  white-space: nowrap;
  pointer-events: none;
}
.label-in {
  left: 12px;
  bottom: -14px;
}
.label-out {
  right: 12px;
  bottom: -14px;
}
</style>
