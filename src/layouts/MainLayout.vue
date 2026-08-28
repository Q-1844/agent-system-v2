<template>
  <div class="layout" :class="{ collapsed: sidebarCollapsed }">
    <aside class="sidebar">
      <div class="sidebar-header">
        <div class="logo">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="2" y="2" width="20" height="20" rx="2" />
            <line x1="7" y1="2" x2="7" y2="22" />
            <line x1="17" y1="2" x2="17" y2="22" />
            <line x1="2" y1="12" x2="22" y2="12" />
            <line x1="2" y1="7" x2="7" y2="7" />
            <line x1="2" y1="17" x2="7" y2="17" />
            <line x1="17" y1="7" x2="22" y2="7" />
            <line x1="17" y1="17" x2="22" y2="17" />
          </svg>
        </div>
        <span v-if="!sidebarCollapsed" class="logo-text">StoryBoard</span>
        <button class="icon-btn" @click="toggleSidebar">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
      </div>

      <nav class="nav">
        <router-link
          v-for="item in menuItems"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ active: $route.path === item.path }"
        >
          <span class="nav-icon" v-html="item.icon"></span>
          <span v-if="!sidebarCollapsed" class="nav-label">{{ item.title }}</span>
        </router-link>
      </nav>

      <div class="sidebar-footer">
        <button class="nav-item theme-toggle" @click="toggleTheme">
          <span class="nav-icon">
            <svg v-if="theme === 'dark'" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="5" />
              <line x1="12" y1="1" x2="12" y2="3" />
              <line x1="12" y1="21" x2="12" y2="23" />
              <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
              <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
              <line x1="1" y1="12" x2="3" y2="12" />
              <line x1="21" y1="12" x2="23" y2="12" />
              <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
              <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
            </svg>
            <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
            </svg>
          </span>
          <span v-if="!sidebarCollapsed" class="nav-label">
            {{ theme === 'dark' ? 'Light Mode' : 'Dark Mode' }}
          </span>
        </button>

        <router-link to="/settings" class="nav-item">
          <span class="nav-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </span>
          <span v-if="!sidebarCollapsed" class="nav-label">Settings</span>
        </router-link>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="topbar-left">
          <span class="page-title">{{ currentRoute?.meta?.title || 'Dashboard' }}</span>
        </div>
        <div class="topbar-right">
          <button class="btn btn-ghost" @click="orchestrateShow = true">编排</button>
          <button class="btn btn-ghost queue-btn" @click="queueShow = !queueShow">
            队列
            <span v-if="runningCount" class="badge">{{ runningCount }}</span>
          </button>
          <button class="btn btn-ghost" :disabled="exporting" @click="handleExport">
            {{ exporting ? '合成中...' : '导出视频' }}
          </button>
          <div class="avatar">{{ (projectName || 'U').charAt(0) }}</div>
        </div>
      </header>

      <div class="content">
        <router-view />
      </div>
    </main>

    <QueuePanel v-model:show="queueShow" :project-name="projectName" />
    <OrchestrateModal v-model:show="orchestrateShow" />
    <FloatingAgentHost />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRoute } from 'vue-router';
import { useTheme } from '@/composables/useTheme';
import { useProjectStore } from '@/store';
import { apiClient } from '@/api';
import { useTaskStream } from '@/composables/useTaskStream';
import { useJobStream } from '@/composables/useJobStream';
import { useProjectEvents } from '@/composables/useProjectEvents';
import { useJobStore } from '@/store/jobs';
import QueuePanel from '@/components/QueuePanel.vue';
import OrchestrateModal from '@/components/OrchestrateModal.vue';
import FloatingAgentHost from '@/components/agent/FloatingAgentHost.vue';

const route = useRoute();
const currentRoute = computed(() => route);
const sidebarCollapsed = ref(false);

const { theme, toggleTheme } = useTheme();

const toggleSidebar = () => {
  sidebarCollapsed.value = !sidebarCollapsed.value;
};

const store = useProjectStore();
const projectName = computed(() => store.projectName);
const { subscribe: subscribeTask } = useTaskStream();
const exporting = ref(false);

// AI-7C-H K3：应用挂载即恢复持久化的项目身份（在任何 Agent/ChangeSession 真相恢复
// 之前完成——它们 await store.identityReady）。无效/已删项目回退默认，无崩溃循环。
void store.restoreSelectedProject();

// 阶段 4：全局任务队列 SSE（随 projectName 变化自动重连）
const jobStore = useJobStore();
const queueShow = ref(false);
const orchestrateShow = ref(false);
const runningCount = computed(() => jobStore.queue.running);
useJobStream(projectName);
// AI-7D-R：项目级资源锁/内容变更 SSE（app 级，切页面不断线；剧本页据此锁定/刷新）
useProjectEvents(projectName);

async function handleExport() {
  if (exporting.value) return;
  exporting.value = true;
  try {
    const r: any = await apiClient.composeShots(projectName.value, {});
    subscribeTask(r.task_id, (s: any) => {
      if (s.status === 'completed') {
        exporting.value = false;
        const url = `http://localhost:8000/projects/${encodeURIComponent(projectName.value)}/output/final.mp4`;
        const a = document.createElement('a');
        a.href = url;
        a.download = 'final.mp4';
        a.click();
      } else if (s.status === 'failed') {
        exporting.value = false;
      }
    });
  } catch (e: any) {
    exporting.value = false;
  }
}

const menuItems = [
  {
    path: '/dashboard',
    title: 'Dashboard',
    icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="14" y="14" width="7" height="7" /><rect x="3" y="14" width="7" height="7" /></svg>'
  },
  {
    path: '/characters',
    title: 'Characters',
    icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg>'
  },
  {
    path: '/script-editor',
    title: '剧本',
    icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="8" y1="13" x2="16" y2="13" /><line x1="8" y1="17" x2="16" y2="17" /></svg>'
  },
  {
    path: '/storyboard',
    title: 'Storyboard',
    icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" /><line x1="3" y1="9" x2="21" y2="9" /><line x1="3" y1="15" x2="21" y2="15" /><line x1="9" y1="3" x2="9" y2="21" /></svg>'
  },
  {
    path: '/workspace',
    title: '创作版本',
    icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" /></svg>'
  },
  {
    path: '/agents',
    title: 'AI 团队',
    icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="10" rx="2" /><circle cx="12" cy="5" r="2" /><path d="M12 7v4" /><line x1="8" y1="16" x2="8" y2="16" /><line x1="16" y1="16" x2="16" y2="16" /></svg>'
  }
];
</script>

<style lang="scss" scoped>
.layout {
  display: flex;
  height: 100vh;
  background-color: var(--bg-root);
}

.sidebar {
  width: 200px;
  background-color: var(--bg-root);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  transition: width 0.2s ease;

  .collapsed & {
    width: 52px;
  }
}

.sidebar-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);

  .collapsed & {
    padding: 14px 12px;
    justify-content: center;
  }
}

.logo {
  color: var(--text-primary);
  flex-shrink: 0;

  .collapsed & {
    display: none;
  }
}

.logo-text {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.icon-btn {
  margin-left: auto;
  background: none;
  border: none;
  color: var(--icon);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;

  &:hover {
    color: var(--icon-hover);
    background-color: var(--bg-hover);
  }

  .collapsed & {
    margin-left: 0;
  }
}

.nav {
  flex: 1;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  color: var(--text-muted);
  font-size: 13px;
  transition: all 0.15s ease;
  text-decoration: none;

  &:hover {
    background-color: var(--bg-hover);
    color: var(--text-secondary);
  }

  &.active {
    background-color: var(--bg-hover);
    color: var(--text-primary);
  }

  .collapsed & {
    justify-content: center;
    padding: 10px;
  }
}

.nav-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.nav-label {
  white-space: nowrap;
}

.sidebar-footer {
  padding: 8px;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.theme-toggle {
  cursor: pointer;
  width: 100%;
  background: none;
  border: none;
  text-align: left;
}

.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.topbar {
  height: 48px;
  border-bottom: 1px solid var(--border);
  padding: 0 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.topbar-left {
  display: flex;
  align-items: center;
}

.page-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.btn {
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  border: none;

  &-ghost {
    background: none;
    color: var(--text-muted);

    &:hover {
      background-color: var(--bg-hover);
      color: var(--text-secondary);
    }
  }
}

.avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background-color: var(--bg-hover);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--text-muted);
}

.queue-btn {
  position: relative;
}
.badge {
  position: absolute;
  top: -2px;
  right: -6px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: #60a5fa;
  color: #fff;
  font-size: 10px;
  line-height: 16px;
  text-align: center;
  font-weight: 600;
}

.content {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
}
</style>
