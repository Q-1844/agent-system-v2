<template>
  <div
    class="wf-node wf-agent"
    :class="{ selected, error: d.hasError, unavailable: d.unavailable }"
    :title="d.errorTitles.join('\n')"
  >
    <Handle
      id="bind"
      type="target"
      :position="Position.Top"
      class="wf-port port-bind"
    >
      <span class="port-dot" />
    </Handle>
    <Handle id="in" type="target" :position="Position.Left" class="wf-port port-exec">
      <span class="port-dot" />
    </Handle>
    <Handle id="out" type="source" :position="Position.Right" class="wf-port port-exec">
      <span class="port-dot" />
    </Handle>

    <div class="nd-head">
      <span class="nd-icon" v-html="ICON_AGENT" />
      <div class="nd-titles">
        <span class="nd-name">{{ agent?.name ?? d.node.refId }}</span>
        <span class="nd-sub">{{ agent?.role ?? '未知 Agent' }}</span>
      </div>
      <span class="nd-id mono-num">{{ d.node.refId }}</span>
    </div>
    <button v-if="controller" class="nd-del" title="删除节点" @pointerdown.stop @mousedown.stop @click.stop="onDelete">✕</button>

    <div class="nd-body">
      <p class="nd-desc">{{ agent?.description ?? '引用失效：注册表中找不到该 Agent' }}</p>
      <div class="nd-io">
        <span class="io-k">输入</span>
        <span class="io-v">{{ agent?.readScopes.join(' / ') ?? '—' }}</span>
      </div>
      <div class="nd-io">
        <span class="io-k">产物</span>
        <span class="io-v">{{ artifactText }}</span>
      </div>
    </div>

    <div class="nd-foot">
      <span v-if="d.hasError" class="nd-badge badge-error">⚠ 校验错误{{ d.errorCount > 1 ? ` ×${d.errorCount}` : '' }}</span>
      <span v-else-if="d.unavailable" class="nd-badge badge-off">◌ 不可用</span>
      <span v-else-if="d.boundSkillCount" class="nd-badge badge-skill">◈ 已绑 {{ d.boundSkillCount }} 个 Skill</span>
      <span v-else class="nd-badge">Agent</span>
      <span class="nd-badge badge-tool">工具 {{ toolCount }}<template v-if="selectedToolsCount"> · 额外 {{ selectedToolsCount }}</template></span>
    </div>

    <span class="port-label label-in">执行输入</span>
    <span class="port-label label-out">执行输出</span>
    <span class="port-label label-bind">Skill 绑定</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { Handle, Position } from '@vue-flow/core';
import type { WorkflowNode } from '../types';
import { getAgent } from '../catalog';
import { useNodeDelete } from './useNodeDelete';

export interface AgentNodeData {
  node: WorkflowNode;
  hasError: boolean;
  errorCount: number;
  errorTitles: string[];
  /** 后端 settings 已停用该 Agent */
  unavailable?: boolean;
  boundSkillCount?: number;
}

const props = defineProps<{ data: AgentNodeData; selected?: boolean; id?: string }>();
// vue-flow 会传入大量标准 NodeProps，未声明的不要落到根元素上
defineOptions({ inheritAttrs: false });
const d = computed(() => props.data);
const { controller, onDelete } = useNodeDelete(props);

const agent = computed(() => getAgent(d.value.node.refId));
const artifactText = computed(() => {
  const a = agent.value;
  if (!a) return '—';
  const first = a.artifacts[0] ?? a.writeScopes.join(' / ');
  return first.length > 44 ? `${first.slice(0, 44)}…` : first;
});
const selectedToolsCount = computed(() => d.value.node.config.selectedTools?.length ?? 0);
/** 核心工具=注册表自带必有；selectedTools 只存以后可能有的额外工具 */
const toolCount = computed(() => getAgent(d.value.node.refId)?.tools.length ?? 0);

const ICON_AGENT =
  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><line x1="8" y1="16" x2="8" y2="16"/><line x1="16" y1="16" x2="16" y2="16"/></svg>';
</script>

<style lang="scss" scoped>
.wf-node {
  position: relative;
  width: 232px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
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
.wf-agent {
  &.selected {
    border-color: var(--text-secondary);
    box-shadow: 0 0 0 1.5px var(--text-secondary), 0 4px 16px rgba(0, 0, 0, 0.4);
  }
  &.error {
    border-color: var(--error);
    box-shadow: 0 0 0 1.5px var(--error);
  }
  &.unavailable {
    border-style: dashed;
    opacity: 0.75;
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
  color: var(--text-secondary);
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
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.nd-id {
  font-size: 10px;
  color: var(--text-faint);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 5px;
  flex-shrink: 0;
  max-width: 88px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nd-body {
  padding: 7px 10px 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
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
  width: 24px;
}
.io-v {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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
  &.badge-off {
    color: var(--text-muted);
    border-style: dashed;
  }
  &.badge-skill {
    color: #60a5fa;
    border-color: rgba(96, 165, 250, 0.35);
    background: rgba(96, 165, 250, 0.1);
  }
  &.badge-tool {
    color: var(--text-secondary);
  }
}

/* ---------- 端口 ---------- */
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
    transition: transform 0.12s ease, background 0.12s ease, box-shadow 0.12s ease;
  }
  &.port-bind .port-dot {
    background: #60a5fa;
  }
  &:hover .port-dot {
    transform: scale(1.25);
    box-shadow: 0 0 6px rgba(229, 229, 229, 0.5);
  }
  &.port-bind:hover .port-dot {
    box-shadow: 0 0 6px rgba(96, 165, 250, 0.7);
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
.label-bind {
  top: -16px;
  left: 50%;
  transform: translateX(-50%);
  color: rgba(96, 165, 250, 0.75);
}
</style>
