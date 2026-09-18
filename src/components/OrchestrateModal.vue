<template>
  <transition name="fade">
    <div v-if="show" class="om-mask" @click.self="$emit('update:show', false)">
      <div class="om">
        <header class="om-head">
          <span>一键编排</span>
          <button class="om-close" @click="$emit('update:show', false)">✕</button>
        </header>
        <div class="om-body">
          <div class="om-field">
            <label class="om-label">阶段（顺序执行）</label>
            <div class="om-stages">
              <label v-for="s in stageOptions" :key="s.key" class="om-chip">
                <input type="checkbox" v-model="stages" :value="s.key" /> {{ s.label }}
              </label>
            </div>
            <div class="om-order">顺序：首帧 -> 尾帧 -> 视频 -> 合成</div>
          </div>

          <div class="om-field om-row">
            <div>
              <label class="om-label">范围</label>
              <select v-model="scope" class="om-select">
                <option value="all">全部镜头</option>
                <option value="pending">仅未完成</option>
              </select>
            </div>
            <label class="om-skip"><input type="checkbox" v-model="skipLocked" /> 跳过锁定</label>
          </div>

          <div class="om-preview">
            <button class="om-btn" @click="dryRun" :disabled="!stages.length || previewing">
              {{ previewing ? '预估中…' : '预估' }}
            </button>
            <div v-if="preview" class="om-result">
              <div class="om-big">🎬 {{ preview.video_count }} 个视频 · 约 {{ preview.eta_minutes }} 分钟</div>
              <div class="om-sub">共 {{ preview.plan_count }} 个任务 · 视频配额 {{ preview.video_seconds_needed }}s</div>
              <details>
                <summary>详细计划 ({{ preview.plan.length }})</summary>
                <pre class="om-plan">{{ planText }}</pre>
              </details>
            </div>
          </div>
        </div>
        <footer class="om-foot">
          <span class="om-tip" v-if="preview && preview.video_count > 0">⚠️ 视频 1 RPM，{{ preview.video_count }} 个约需 {{ preview.eta_minutes }} 分钟</span>
          <button class="om-btn primary" @click="execute" :disabled="!stages.length || executing">
            {{ executing ? '提交中…' : '执行' }}
          </button>
        </footer>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { apiClient } from '@/api';
import type { OrchestrateResult } from '@/api';
import { useProjectStore } from '@/store';
import { createDiscreteApi } from 'naive-ui';
import { discreteConfigProviderProps } from '@/styles/naiveTheme';

const { message } = createDiscreteApi(['message'], { configProviderProps: discreteConfigProviderProps });

defineProps<{ show: boolean }>();
const emit = defineEmits<{ (e: 'update:show', v: boolean): void }>();
const projStore = useProjectStore();

const stageOptions = [
  { key: 'first_frame', label: '首帧' },
  { key: 'last_frame', label: '尾帧' },
  { key: 'video', label: '视频' },
  { key: 'compose', label: '合成' },
];
const stages = ref<string[]>(['first_frame', 'last_frame', 'video', 'compose']);
const scope = ref('all');
const skipLocked = ref(true);
const preview = ref<OrchestrateResult | null>(null);
const previewing = ref(false);
const executing = ref(false);
const planText = computed(() =>
  preview.value ? preview.value.plan.map(([k, s]) => `${k}: ${s}`).join('\n') : '');

async function dryRun() {
  previewing.value = true;
  try {
    preview.value = await apiClient.orchestrate(projStore.projectName, {
      stages: stages.value, scope: scope.value, skip_locked: skipLocked.value, dry_run: true,
    });
  } catch (e: any) { message.error(`预估失败: ${e.message || e}`); }
  finally { previewing.value = false; }
}

async function execute() {
  executing.value = true;
  try {
    const r = await apiClient.orchestrate(projStore.projectName, {
      stages: stages.value, scope: scope.value, skip_locked: skipLocked.value, dry_run: false,
    });
    message.success(`已提交 ${r.plan_count} 个任务（视频 ${r.video_count}）`);
    emit('update:show', false);
  } catch (e: any) { message.error(`执行失败: ${e.message || e}`); }
  finally { executing.value = false; }
}
</script>

<style lang="scss" scoped>
.om-mask {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5); z-index: 200;
  display: flex; align-items: center; justify-content: center;
}
.om {
  width: 460px; max-width: 90vw; background: var(--bg-root);
  border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}
.om-head {
  display: flex; align-items: center; padding: 14px 16px;
  border-bottom: 1px solid var(--border); font-size: 14px; font-weight: 600; color: var(--text-primary);
}
.om-close { margin-left: auto; background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 14px; }
.om-body { padding: 16px; }
.om-field { margin-bottom: 14px; }
.om-label { display: block; font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }
.om-stages { display: flex; flex-wrap: wrap; gap: 8px; }
.om-chip { font-size: 13px; color: var(--text-secondary); display: flex; align-items: center; gap: 4px;
  padding: 4px 10px; border: 1px solid var(--border); border-radius: 14px; cursor: pointer; }
.om-order { font-size: 11px; color: var(--text-muted); margin-top: 6px; }
.om-row { display: flex; align-items: flex-end; gap: 16px; }
.om-select { padding: 4px 8px; border-radius: 4px; border: 1px solid var(--border); background: var(--bg-root); color: var(--text-secondary); font-size: 13px; }
.om-skip { font-size: 12px; color: var(--text-secondary); display: flex; align-items: center; gap: 4px; }
.om-preview { border-top: 1px solid var(--border); padding-top: 14px; }
.om-btn {
  padding: 6px 16px; border-radius: 5px; border: 1px solid var(--border);
  background: var(--bg-hover); color: var(--text-secondary); cursor: pointer; font-size: 13px;
  &:hover:not(:disabled) { background: var(--bg-root); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
  &.primary { background: #60a5fa; color: #fff; border-color: #60a5fa; &:hover:not(:disabled) { background: #3b82f6; } }
}
.om-result { margin-top: 12px; }
.om-big { font-size: 15px; color: var(--text-primary); font-weight: 600; }
.om-sub { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.om-plan { font-size: 11px; color: var(--text-muted); max-height: 160px; overflow: auto; white-space: pre-wrap; }
.om-foot {
  display: flex; align-items: center; gap: 12px; padding: 14px 16px;
  border-top: 1px solid var(--border); justify-content: flex-end;
}
.om-tip { font-size: 11px; color: #fbbf24; margin-right: auto; }
.fade-enter-active, .fade-leave-active { transition: opacity 0.15s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
