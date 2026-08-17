<!-- AI-6A Creative Workspace Studio 视图入口。复用全局当前项目（useProjectStore）。
     AI-6D §20：支持 ?workspace_id= 深链直达某 workspace（从 ShotBoard「打开创作版本」）。 -->
<template>
  <CreativeWorkspaceStudio v-if="projectName" :project-name="projectName" :initial-workspace-id="initialWid" />
  <div v-else class="ws-empty">请先在仪表盘选择一个项目，再进入创作版本。</div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { useProjectStore } from '@/store';
import CreativeWorkspaceStudio from '@/components/CreativeWorkspaceStudio.vue';

const store = useProjectStore();
const route = useRoute();
const projectName = computed(() => store.projectName);
const initialWid = computed(() => (route.query.workspace_id as string) || '');
</script>

<style scoped>
.ws-empty { color: var(--text-muted); padding: 40px; text-align: center; }
</style>
