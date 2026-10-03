<template>
  <div
    class="wf-node wf-skill"
    :class="{ selected, error: d.hasError }"
    :title="d.errorTitles.join('\n')"
  >
    <Handle id="bind" type="source" :position="Position.Right" class="wf-port port-bind">
      <span class="port-dot" />
    </Handle>

    <div class="nd-head">
      <span class="nd-icon" v-html="ICON_SKILL" />
      <div class="nd-titles">
        <span class="nd-name">{{ skill?.name ?? d.node.refId }}</span>
        <span class="nd-sub mono-num">{{ d.node.refId }} · v{{ d.node.refVersion ?? '?' }}</span>
      </div>
    </div>
    <button v-if="controller" class="nd-del" title="删除节点" @pointerdown.stop @mousedown.stop @click.stop="onDelete">✕</button>

    <div class="nd-body">
      <p class="nd-desc">{{ skill?.description ?? '引用失效：正式 Skill 目录中找不到该 Skill' }}</p>
    </div>

    <div class="nd-foot">
      <span v-if="d.hasError" class="nd-badge badge-error">⚠ 校验错误{{ d.errorCount > 1 ? ` ×${d.errorCount}` : '' }}</span>
      <span v-else-if="!d.boundTargets.length" class="nd-badge badge-warn">未绑定 Agent</span>
      <span v-else class="nd-badge badge-ok">◈ 绑定 {{ d.boundTargets.length }} 个 Agent</span>
      <span class="nd-badge">正式 · v{{ d.node.refVersion }}</span>
    </div>

    <span class="port-label label-bind">绑定 →</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { Handle, Position } from '@vue-flow/core';
import type { WorkflowNode } from '../types';
import { getSkill } from '../catalog';
import { useNodeDelete } from './useNodeDelete';

export interface SkillNodeData {
  node: WorkflowNode;
  hasError: boolean;
  errorCount: number;
  errorTitles: string[];
  /** 绑定到的 Agent 展示名 */
  boundTargets: string[];
}

const props = defineProps<{ data: SkillNodeData; selected?: boolean; id?: string }>();
defineOptions({ inheritAttrs: false });
const d = computed(() => props.data);
const { controller, onDelete } = useNodeDelete(props);

const skill = computed(() => getSkill(d.value.node.refId));

// Skill 是绑定给 Agent 使用的规则，不是会自行运行的任务——节点上不做出/入执行端口
const ICON_SKILL =
  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>';
</script>

<style lang="scss" scoped>
.wf-node {
  position: relative;
  width: 224px;
  background: var(--bg-surface);
  border: 1px solid rgba(96, 165, 250, 0.35);
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
.wf-skill {
  &.selected {
    border-color: #60a5fa;
    box-shadow: 0 0 0 1.5px rgba(96, 165, 250, 0.8), 0 4px 16px rgba(0, 0, 0, 0.4);
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
  color: #60a5fa;
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
}
.nd-desc {
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-secondary);
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
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

  &.badge-error {
    color: var(--error);
    border-color: rgba(239, 68, 68, 0.4);
    background: rgba(239, 68, 68, 0.12);
  }
  &.badge-warn {
    color: var(--warning);
    border-color: rgba(234, 179, 8, 0.35);
    background: rgba(234, 179, 8, 0.1);
  }
  &.badge-ok {
    color: #60a5fa;
    border-color: rgba(96, 165, 250, 0.35);
    background: rgba(96, 165, 250, 0.1);
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
    background: #60a5fa;
    transition: transform 0.12s ease, box-shadow 0.12s ease;
  }
  &:hover .port-dot {
    transform: scale(1.25);
    box-shadow: 0 0 6px rgba(96, 165, 250, 0.7);
  }
}
.port-label {
  position: absolute;
  font-size: 10px;
  color: rgba(96, 165, 250, 0.75);
  white-space: nowrap;
  pointer-events: none;
}
.label-bind {
  right: 12px;
  top: -16px;
}
</style>
