<!-- AI-6A Creative Workspace Studio 视图入口。复用全局当前项目（useProjectStore）。 -->
<template>
  <CreativeWorkspaceStudio v-if="projectName" :project-name="projectName" />
  <div v-else class="ws-empty">请先在仪表盘选择一个项目，再进入创作版本。</div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useProjectStore } from '@/store';
import CreativeWorkspaceStudio from '@/components/CreativeWorkspaceStudio.vue';

const store = useProjectStore();
const projectName = computed(() => store.projectName);
</script>

<style scoped>
.ws-empty { color: var(--text-muted); padding: 40px; text-align: center; }
</style>
