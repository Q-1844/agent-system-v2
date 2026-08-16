import { createRouter, createWebHistory } from 'vue-router';
import type { RouteRecordRaw } from 'vue-router';

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/index.vue'),
        meta: { title: '仪表盘', icon: 'dashboard' }
      },
      {
        path: 'script-editor',
        name: 'ScriptEditor',
        component: () => import('@/views/script-editor/index.vue'),
        meta: { title: '剧本编辑', icon: 'script' }
      },
      {
        path: 'characters',
        name: 'Characters',
        component: () => import('@/views/characters/index.vue'),
        meta: { title: '角色管理', icon: 'characters' }
      },
      {
        path: 'storyboard',
        name: 'Storyboard',
        component: () => import('@/views/storyboard/ShotBoard.vue'),
        meta: { title: '分镜画廊', icon: 'storyboard' }
      },
      {
        path: 'workspace',
        name: 'Workspace',
        component: () => import('@/views/workspace/index.vue'),
        meta: { title: '创作版本', icon: 'workspace' }
      },
      {
        path: 'agents',
        name: 'AgentCenter',
        component: () => import('@/views/agents/index.vue'),
        meta: { title: 'AI 团队', icon: 'agents' }
      },
      {
        path: 'settings',
        name: 'Settings',
        component: () => import('@/views/settings/index.vue'),
        meta: { title: '设置', icon: 'settings' }
      }
    ]
  }
];

const router = createRouter({
  history: createWebHistory(),
  routes
});

export default router;
