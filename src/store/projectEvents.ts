/**
 * 项目级实时事件 store（Pinia）。
 *
 * 由 useProjectEvents composable 喂数据（SSE /api/project/{p}/events）：
 *  - snapshot / resource.locked / resource.unlocked → lockedResources
 *  - story.changed → storyVersion（+1 即剧本域内容已被 Agent 改动，订阅方据此静默刷新）
 * store 本身不连 SSE，解耦（与 jobs store 同思想）。
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';

export const RESOURCE_STORY = 'story';
export const RESOURCE_CHARACTER = 'character';
export const RESOURCE_STORYBOARD = 'storyboard';
/** Agent System V2 Phase B：生产资源域（resources.json——Look/Clothing/Prop/Composition） */
export const RESOURCE_RESOURCES = 'resources';
/** Agent System V2 Phase C2：场景系统域（scene_system.json——Location/View/Instance/State） */
export const RESOURCE_SCENE_SYSTEM = 'scene_system';

export interface ProjectEvent {
  type: string;
  resource?: string;
  project?: string;
  by?: string;
  session_id?: string;
  operation?: string;
  at?: number;
}

export const useProjectEventsStore = defineStore('projectEvents', () => {
  const currentProject = ref('');
  const connected = ref(false);
  /** 当前项目被锁定的资源 → {by, session_id} */
  const lockedResources = ref<Record<string, { by?: string; session_id?: string }>>({});
  /** story.changed 自增版本号（剧本域内容变化信号） */
  const storyVersion = ref(0);
  /** 最近一次变更事件（供需要时取 operation 等） */
  const lastStoryChange = ref<ProjectEvent | null>(null);
  /** character.changed 自增版本号（角色域内容变化信号，Character Designer 写入后 +1） */
  const charactersVersion = ref(0);
  /** storyboard.changed 自增版本号（分镜域内容变化信号，Director 写入后 +1） */
  const storyboardVersion = ref(0);
  /** resources.changed 自增版本号（生产资源域内容变化信号，Resource Agent 写入后 +1） */
  const resourcesVersion = ref(0);
  /** scene_system.changed 自增版本号（场景系统域内容变化信号，Scene Agent 写入后 +1） */
  const sceneSystemVersion = ref(0);

  const storyLocked = computed(() => !!lockedResources.value[RESOURCE_STORY]);
  const charactersLocked = computed(() => !!lockedResources.value[RESOURCE_CHARACTER]);
  const storyboardLocked = computed(() => !!lockedResources.value[RESOURCE_STORYBOARD]);
  const resourcesLocked = computed(() => !!lockedResources.value[RESOURCE_RESOURCES]);
  const sceneSystemLocked = computed(() => !!lockedResources.value[RESOURCE_SCENE_SYSTEM]);

  function setProject(p: string) {
    if (currentProject.value !== p) {
      currentProject.value = p;
      lockedResources.value = {};
      lastStoryChange.value = null;
      charactersVersion.value = 0;
      // 版本号同样按项目隔离：切项目清零，避免旧项目的变化信号触发新项目页面刷新
      storyVersion.value = 0;
      storyboardVersion.value = 0;
      resourcesVersion.value = 0;
      sceneSystemVersion.value = 0;
    }
  }
  function setConnected(c: boolean) { connected.value = c; }

  /** snapshot.locks 整体替换（重连/切项目） */
  function applySnapshot(locks: ProjectEvent[]) {
    const next: Record<string, { by?: string; session_id?: string }> = {};
    for (const l of Array.isArray(locks) ? locks : []) {
      if (l.resource) next[l.resource] = { by: l.by, session_id: l.session_id };
    }
    lockedResources.value = next;
  }
  function applyEvent(ev: ProjectEvent) {
    if (ev.type === 'resource.locked' && ev.resource) {
      lockedResources.value = { ...lockedResources.value,
        [ev.resource]: { by: ev.by, session_id: ev.session_id } };
    } else if (ev.type === 'resource.unlocked' && ev.resource) {
      const next = { ...lockedResources.value };
      delete next[ev.resource];
      lockedResources.value = next;
    } else if (ev.type === 'story.changed') {
      storyVersion.value += 1;
      lastStoryChange.value = ev;
    } else if (ev.type === 'character.changed') {
      charactersVersion.value += 1;
    } else if (ev.type === 'storyboard.changed') {
      storyboardVersion.value += 1;
    } else if (ev.type === 'resources.changed') {
      resourcesVersion.value += 1;
    } else if (ev.type === 'scene_system.changed') {
      sceneSystemVersion.value += 1;
    }
  }

  return {
    currentProject, connected, lockedResources, storyVersion, lastStoryChange,
    charactersVersion, storyboardVersion, resourcesVersion, sceneSystemVersion,
    storyLocked, charactersLocked, storyboardLocked, resourcesLocked, sceneSystemLocked,
    setProject, setConnected, applySnapshot, applyEvent,
  };
});
