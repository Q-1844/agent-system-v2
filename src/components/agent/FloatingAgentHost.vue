<template>
  <!-- AI-6C §4/§36：挂在 MainLayout 全局层级，跨页面存在。
       子组件各自 position:fixed；Host 仅作逻辑容器。 -->
  <FloatingAgentBar />
  <AgentConversationSurface v-if="store.conversationOpen" />
</template>

<script setup lang="ts">
import { watch, onMounted, onBeforeUnmount } from 'vue';
import { useProjectStore } from '@/store';
import { useAgentUiStore } from '@/store/agentUi';
import { useChangeSessionStore } from '@/store/changeSession';
import FloatingAgentBar from './FloatingAgentBar.vue';
import AgentConversationSurface from './AgentConversationSurface.vue';

const projectStore = useProjectStore();
const store = useAgentUiStore();
const cs = useChangeSessionStore();

// §4：project 变化销毁当前聊天上下文（session/context/messages），位置保留。
watch(() => projectStore.projectName, () => store.onProjectChange());

// AI-7C-H §27/K3：F5 后从后端持久化对象重建运行真相（非旧聊天叙述）。
// 等待项目身份恢复完成（正确项目），再恢复活跃 ChangeSession/Execution（含轮询续接）。
onMounted(async () => {
  await projectStore.identityReady;
  await cs.restoreActive();
});

// §34：Esc 优先关闭 expanded conversation（不丢失 session）。
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && store.conversationOpen) {
    e.preventDefault();
    store.closeConversation();
  }
}
onMounted(() => window.addEventListener('keydown', onKeydown));
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown));
</script>
