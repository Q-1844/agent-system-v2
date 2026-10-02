<template>
  <BaseEdge
    :id="id"
    :path="path"
    :style="strokeStyle"
    :marker-end="markerEnd"
  />
  <EdgeLabelRenderer>
    <div
      class="wf-edge-chip"
      :class="[data?.edgeType, { selected }]"
      :style="chipStyle"
    >
      {{ data?.edgeType === 'skill_binding' ? `Skill 绑定${versionText}` : '执行依赖' }}
      <span v-if="data?.contractStatus === 'pending_backend'" class="chip-flag">待后端定义</span>
      <span v-else-if="data?.contractStatus === 'human_gate'" class="chip-flag">人工</span>
    </div>
  </EdgeLabelRenderer>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { BaseEdge, EdgeLabelRenderer, getBezierPath } from '@vue-flow/core';
import type { Position } from '@vue-flow/core';

export interface WfEdgeData {
  edgeType: 'execution' | 'skill_binding';
  contractStatus?: 'verified' | 'pending_backend' | 'human_gate';
  skillVersion?: string | null;
}

// 手动声明渲染所需的最小 props（vue-flow 会把完整 EdgeProps 传进来，多余项忽略）
const props = defineProps<{
  id: string;
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  sourcePosition?: Position;
  targetPosition?: Position;
  data?: WfEdgeData;
  selected?: boolean;
  markerEnd?: string;
}>();

const pathData = computed(() =>
  getBezierPath({
    sourceX: props.sourceX,
    sourceY: props.sourceY,
    targetX: props.targetX,
    targetY: props.targetY,
    sourcePosition: props.sourcePosition,
    targetPosition: props.targetPosition,
  })
);
const path = computed(() => pathData.value[0]);
const chipStyle = computed(() => ({
  transform: `translate(-50%, -50%) translate(${pathData.value[1]}px, ${pathData.value[2]}px)`,
}));

const isBinding = computed(() => props.data?.edgeType === 'skill_binding');

const strokeStyle = computed(() =>
  isBinding.value
    ? {
        stroke: props.selected ? '#93c5fd' : '#60a5fa',
        strokeWidth: props.selected ? 2 : 1.5,
        strokeDasharray: '6 4',
      }
    : {
        stroke: props.selected ? '#e5e5e5' : '#7a7a7a',
        strokeWidth: props.selected ? 2 : 1.5,
      }
);

const versionText = computed(() => (props.data?.skillVersion ? ` v${props.data.skillVersion}` : ''));
</script>

<style lang="scss" scoped>
.wf-edge-chip {
  position: absolute;
  pointer-events: none;
  font-size: 10px;
  line-height: 16px;
  padding: 0 7px;
  border-radius: 8px;
  background: var(--bg-root);
  border: 1px solid var(--border-hover);
  color: var(--text-muted);
  white-space: nowrap;
  display: flex;
  gap: 4px;
  align-items: center;
  z-index: 1;

  &.skill_binding {
    color: #60a5fa;
    border-color: rgba(96, 165, 250, 0.4);
  }
  &.selected {
    border-color: var(--text-secondary);
    color: var(--text-primary);
  }
}
.chip-flag {
  font-size: 9px;
  color: var(--warning);
}
</style>
