<template>
  <div class="dashboard">
    <div class="header">
      <div>
        <h1>Storyboard</h1>
        <p class="subtitle">AI 动画分镜工作流</p>
      </div>
      <div class="header-actions">
        <button class="btn btn-primary" @click="openAdd">+ 添加项目</button>
      </div>
    </div>

    <!-- 队列概况（全局，保留） -->
    <div class="queue-overview" v-if="queue.running || queue.video_queued || queue.failed">
      <span class="qo-title">任务队列</span>
      <span class="qo-stat">运行中 <b>{{ queue.running }}</b></span>
      <span class="qo-stat">视频排队 <b>{{ queue.video_queued }}</b>
        <span v-if="queue.video_queued" class="qo-eta">≈{{ Math.round(queue.video_eta_s / 60) }}min</span>
      </span>
      <span class="qo-stat fail" v-if="queue.failed">失败 <b>{{ queue.failed }}</b></span>
      <span class="qo-conn" :class="{ off: !jobConnected }">{{ jobConnected ? '● 实时' : '○ 轮询中' }}</span>
    </div>

    <!-- 项目列表 -->
    <div class="proj-section">
      <div class="section-title">项目</div>
      <div class="proj-grid" v-if="projects.length">
        <div
          v-for="p in projects"
          :key="p"
          class="proj-card"
          :class="{ current: p === projectName }"
          @click="enterProject(p)"
        >
          <span class="proj-name">{{ p }}</span>
          <span class="proj-enter">进入 →</span>
        </div>
      </div>
      <div v-else class="empty-hint">还没有项目，点「+ 添加项目」开始</div>
    </div>

    <!-- 添加项目对话框 -->
    <div v-if="showAdd" class="dialog-overlay" @click.self="showAdd = false">
      <div class="dialog">
        <h3>新建项目</h3>
        <div class="form-group">
          <label>项目名称</label>
          <input
            v-model="newName"
            class="input"
            placeholder="输入项目名称"
            @keyup.enter="confirmAdd"
            ref="nameInput"
          />
        </div>
        <div class="dialog-btns">
          <button class="btn btn-cancel" @click="showAdd = false">取消</button>
          <button class="btn btn-confirm" :disabled="adding" @click="confirmAdd">
            {{ adding ? '创建中...' : '创建并进入' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useJobStore } from '@/store/jobs';
import { useProjectStore } from '@/store';
import { apiClient } from '@/api';

const jobStore = useJobStore();
const store = useProjectStore();
const router = useRouter();
const queue = computed(() => jobStore.queue);
const jobConnected = computed(() => jobStore.connected);
const projectName = computed(() => store.projectName);

const projects = ref<string[]>([]);
const showAdd = ref(false);
const newName = ref('');
const adding = ref(false);
const nameInput = ref<HTMLInputElement | null>(null);

async function loadProjects() {
  try { projects.value = await apiClient.listProjects(); }
  catch (e: any) { console.error('[dashboard] 加载项目列表失败', e); }
}

function openAdd() {
  newName.value = '';
  showAdd.value = true;
}

async function enterProject(name: string) {
  await store.loadProject(name);   // 设为当前项目
  router.push('/characters');      // 直接进角色页
}

async function confirmAdd() {
  const name = newName.value.trim();
  if (!name) { store.showNotification('请输入项目名称', 'error'); return; }
  // 前端预检（即时反馈）；后端 409 是权威保障
  if (projects.value.includes(name)) { store.showNotification('项目已存在', 'error'); return; }
  adding.value = true;
  try {
    await apiClient.createProject(name);
    await store.loadProject(name);
    router.push('/characters');    // 创建后直接进角色页
  } catch (e: any) {
    store.showNotification(`创建失败: ${e.message}`, 'error');
  } finally {
    adding.value = false;
  }
}

watch(showAdd, (v) => { if (v) nextTick(() => nameInput.value?.focus()); });
onMounted(() => { loadProjects(); });
</script>

<style lang="scss" scoped>
.dashboard {
  max-width: 960px;
  position: relative;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;

  h1 {
    font-size: 20px;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 4px;
  }
}

.subtitle {
  font-size: 13px;
  color: var(--text-muted);
}

.header-actions {
  display: flex;
  gap: 8px;
}

.btn {
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  border: none;

  &:disabled { opacity: 0.5; cursor: not-allowed; }
}

.btn-primary {
  background-color: var(--accent);
  color: var(--accent-text);
  &:hover:not(:disabled) { background-color: var(--accent-hover); }
}

.queue-overview {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 12px 16px;
  background: var(--bg-root);
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 20px;
  font-size: 13px;
  color: var(--text-muted);
  .qo-title { font-weight: 600; color: var(--text-primary); }
  .qo-stat b { color: var(--text-primary); font-weight: 600; }
  .qo-stat.fail b { color: #f87171; }
  .qo-eta { color: var(--text-muted); margin-left: 4px; }
  .qo-conn { margin-left: auto; font-size: 12px; color: #4ade80; }
  .qo-conn.off { color: var(--text-muted); }
}

.proj-section {
  margin-top: 8px;
}

.section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 14px;
}

.proj-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}

.proj-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 18px 16px;
  cursor: pointer;
  transition: all 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: space-between;

  &:hover {
    border-color: var(--accent);
    background: var(--bg-hover);
  }
  &.current {
    border-color: var(--accent);
  }
}

.proj-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.proj-enter {
  font-size: 12px;
  color: var(--text-muted);
}

.empty-hint {
  padding: 32px;
  background: var(--bg-surface);
  border: 1px dashed var(--border);
  border-radius: 8px;
  text-align: center;
  font-size: 13px;
  color: var(--text-muted);
}

// 对话框
.dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(4px);
}

.dialog {
  background: var(--bg-surface);
  border-radius: 12px;
  padding: 24px;
  width: 380px;
  max-width: 90vw;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);

  h3 { margin: 0 0 18px; font-size: 16px; font-weight: 600; color: var(--text-primary); }
}

.form-group {
  margin-bottom: 18px;
  label { display: block; font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }
  .input {
    width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 6px;
    font-size: 14px; background: var(--bg-root); color: var(--text-primary); outline: none;
    &:focus { border-color: var(--accent); }
  }
}

.dialog-btns {
  display: flex; gap: 8px; justify-content: flex-end;
}

.btn-cancel {
  background: var(--bg-root); color: var(--text-muted); border: 1px solid var(--border);
  &:hover { background: var(--bg-hover); }
}

.btn-confirm {
  background: var(--accent); color: var(--accent-text);
  &:hover:not(:disabled) { background: var(--accent-hover); }
}
</style>
