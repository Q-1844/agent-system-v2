import { createRouter, createWebHistory } from 'vue-router';
import type { RouteRecordRaw } from 'vue-router';

const routes: RouteRecordRaw[] = [
  {
    // 全屏项目选择页（无侧边栏布局）
    path: '/projects',
    name: 'ProjectPicker',
    component: () => import('@/views/project-picker/index.vue'),
    meta: { title: '选择项目' }
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/projects',
    children: [
      {
        path: 'script-editor',
        name: 'ScriptEditor',
        component: () => import('@/views/script-editor/index.vue'),
        meta: { title: '剧本编辑', icon: 'script', fullBleed: true }
      },
      {
        // 角色模块已并入「生产资源」（角色卡片 → 角色工作台弹窗）；旧链重定向保兼容
        path: 'characters',
        redirect: (to) => ({ path: '/resources', query: { ...(to.query as Record<string, string>), tab: 'characters' } })
      },
      {
        path: 'resources',
        name: 'Resources',
        component: () => import('@/views/resources/index.vue'),
        meta: { title: '生产资源', icon: 'resources' }
      },
      {
        path: 'scene-system',
        name: 'SceneSystem',
        component: () => import('@/views/scene-system/index.vue'),
        meta: { title: '场景系统', icon: 'scene' }
      },
      {
        path: 'storyboard',
        name: 'Storyboard',
        component: () => import('@/views/storyboard/ShotBoard.vue'),
        meta: { title: '分镜画廊', icon: 'storyboard' }
      },
      {
        path: 'sequences',
        name: 'Sequences',
        component: () => import('@/views/sequences/index.vue'),
        meta: { title: '影片合成', icon: 'sequences' }
      },
      {
        path: 'workspace',
        name: 'Workspace',
        component: () => import('@/views/workspace/index.vue'),
        meta: { title: '创作版本', icon: 'workspace' }
      },
      {
        // 可视化工作流（第一版前端草稿）：三类画布组件 + 两类连线
        path: 'workflow',
        name: 'Workflow',
        component: () => import('@/views/workflow/index.vue'),
        meta: { title: '工作流', icon: 'workflow', fullBleed: true }
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
  },
  {
    // 旧仪表盘路径兼容
    path: '/dashboard',
    redirect: '/projects'
  }
];

const router = createRouter({
  history: createWebHistory(),
  routes
});

export default router;
