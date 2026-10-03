<template>
  <div class="wf-boundary" :class="[d.kind, { selected }]">
    <Handle v-if="d.kind === 'start'" id="out" type="source" :position="Position.Right" class="wf-port">
      <span class="port-dot" />
    </Handle>
    <Handle v-else id="in" type="target" :position="Position.Left" class="wf-port">
      <span class="port-dot" />
    </Handle>

    <span class="bd-icon" v-html="d.kind === 'start' ? ICON_START : ICON_END" />
    <span class="bd-text">{{ d.kind === 'start' ? '开始' : '结束' }}</span>
    <span class="bd-hint">边界</span>
    <button v-if="controller" class="nd-del" title="删除节点" @pointerdown.stop @mousedown.stop @click.stop="onDelete">✕</button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { Handle, Position } from '@vue-flow/core';
import { useNodeDelete } from './useNodeDelete';

export interface BoundaryNodeData {
  kind: 'start' | 'end';
}

// 开始/结束只是画布边界提示，不算新增组件类型：不做任何执行语义
const props = defineProps<{ data: BoundaryNodeData; selected?: boolean; id?: string }>();
defineOptions({ inheritAttrs: false });
const d = computed(() => props.data);
const { controller, onDelete } = useNodeDelete(props);

const ICON_START =
  '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="6 4 20 12 6 20"/></svg>';
const ICON_END =
  '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>';
</script>

<style lang="scss" scoped>
.wf-boundary {
  position: relative;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 14px;
  background: var(--bg-surface);
  border: 1px dashed var(--border-hover);
  border-radius: 999px;
  font-size: 12px;
  color: var(--text-secondary);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
  animation: wf-pop-in 0.16s ease-out;

  &.selected {
    border-color: var(--text-secondary);
    box-shadow: 0 0 0 1.5px var(--text-secondary);
  }
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
.wf-boundary:hover .nd-del {
  opacity: 1;
}

.bd-icon {
  display: flex;
  color: var(--text-muted);
}
.bd-text {
  font-weight: 600;
  color: var(--text-primary);
}
.bd-hint {
  font-size: 10px;
  color: var(--text-faint);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0 6px;
  line-height: 15px;
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
    width: 8px;
    height: 8px;
    border-radius: 50%;
    border: 1.5px solid var(--bg-root);
    background: var(--text-muted);
    transition: transform 0.12s ease;
  }
  &:hover .port-dot {
    transform: scale(1.25);
  }
}
</style>
