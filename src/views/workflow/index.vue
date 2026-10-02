<template>
  <div class="wf-view">
    <WorkflowStudio v-if="projectName" :key="projectName" :project-name="projectName" />
    <div v-else class="wf-empty">
      <p class="t">还没有选择项目</p>
      <p class="s">工作流按项目隔离保存，请先选择或创建一个项目。</p>
      <router-link to="/projects" class="go">前往项目列表</router-link>
    </div>
  </div>
</template>

<script setup lang="ts">
import { storeToRefs } from 'pinia';
import { useProjectStore } from '@/store';
import WorkflowStudio from '@/components/workflow/WorkflowStudio.vue';

const { projectName } = storeToRefs(useProjectStore());
</script>

<style lang="scss" scoped>
.wf-view {
  height: 100%;
  min-height: 0;
}
.wf-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;

  .t { font-size: 15px; font-weight: 600; color: var(--text-primary); margin: 0; }
  .s { font-size: 13px; color: var(--text-muted); margin: 0; }
  .go {
    margin-top: 6px;
    font-size: 13px;
    color: var(--text-primary);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 7px 14px;

    &:hover { border-color: var(--border-hover); background: var(--bg-hover); }
  }
}
</style>
