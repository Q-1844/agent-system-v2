<template>
  <div class="shot-board">
    <div class="toolbar">
      <h2>分镜（shot 级）</h2>
      <div class="toolbar-info" v-if="projectName">{{ projectName }} · {{ shotCount }} 镜</div>
      <button class="btn" @click="load">刷新</button>
    </div>
    <div class="legend">
      <span>图: 1312×736 · 视频: 1280×704</span>
      <span class="legend-safe">绿色虚线=安全框（框外上下细条会被裁掉）</span>
    </div>
    <div class="cards" v-if="shots.length">
      <ShotCard v-for="sh in shots" :key="sh.shot_id" :shot="sh" :project-name="projectName" @refresh="load" />
    </div>
    <div class="empty" v-else-if="!loading">项目无镜头。请先在「剧本编辑」保存剧本生成 shots，再来这里生成首帧/视频。</div>
    <div class="empty" v-else>加载中…</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue';
import { apiClient } from '@/api';
import type { ShotData } from '@/api';
import { useProjectStore } from '@/store';
import { useJobStore } from '@/store/jobs';
import ShotCard from '@/components/ShotCard.vue';

const projStore = useProjectStore();
const projectName = computed(() => projStore.projectName);
const jobStore = useJobStore();

const shots = ref<ShotData[]>([]);
const loading = ref(false);
const shotCount = computed(() => shots.value.length);

async function load() {
  if (!projectName.value) return;
  loading.value = true;
  try {
    const data = await apiClient.getProjectShots(projectName.value);
    shots.value = (data.scenes || []).flatMap(s => s.shots || []);
  } catch (e: any) {
    console.error('[ShotBoard] 加载 shots 失败', e);
  } finally {
    loading.value = false;
  }
}

// shot 级 job 完成（done/failed）时 debounce 刷新，保持卡片与服务端同步
let reloadTimer: number | null = null;
watch(() => jobStore.jobs.map(j => j.job_id + j.state).join('|'), () => {
  const finished = jobStore.jobs.some(j => (j.state === 'done' || j.state === 'failed') && j.shot_id);
  if (finished) {
    if (reloadTimer) clearTimeout(reloadTimer);
    reloadTimer = window.setTimeout(() => { load(); reloadTimer = null; }, 800);
  }
});

onMounted(load);
watch(projectName, load);
</script>

<style lang="scss" scoped>
.shot-board { max-width: 1200px; }
.toolbar {
  display: flex; align-items: center; gap: 12px; margin-bottom: 8px;
  h2 { font-size: 16px; color: var(--text-primary); margin: 0; }
  .toolbar-info { font-size: 12px; color: var(--text-muted); margin-left: auto; }
  .btn {
    padding: 4px 12px; border-radius: 5px; border: 1px solid var(--border);
    background: var(--bg-hover); color: var(--text-secondary); cursor: pointer; font-size: 12px;
    &:hover { background: var(--bg-root); }
  }
}
.legend {
  font-size: 11px; color: var(--text-muted); margin-bottom: 14px;
  display: flex; gap: 16px;
  .legend-safe { color: #4ade80; opacity: 0.8; }
}
.cards { display: flex; flex-direction: column; gap: 12px; }
.empty { text-align: center; color: var(--text-muted); padding: 60px 0; font-size: 14px; }
</style>
