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

  const storyLocked = computed(() => !!lockedResources.value[RESOURCE_STORY]);

  function setProject(p: string) {
    if (currentProject.value !== p) {
      currentProject.value = p;
      lockedResources.value = {};
      lastStoryChange.value = null;
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
    }
  }

  return {
    currentProject, connected, lockedResources, storyVersion, lastStoryChange,
    storyLocked,
    setProject, setConnected, applySnapshot, applyEvent,
  };
});
