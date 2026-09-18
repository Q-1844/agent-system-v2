/**
 * 顶栏保存/同步状态通道（页面 → MainLayout，显示在最右上角）。
 *
 * 为什么不用 Teleport：naive-ui 会创建独立的离散 Vue app，其挂载会提前冲刷
 * 共享的 post-flush 队列，导致 `defer` 的 teleport 目标解析发生在本应用 DOM
 * 插入 #app 之前（实测：查询时 #app 子节点为 0），目标定位失败。因此改为
 * 共享响应式状态，由 MainLayout 直接渲染，行为确定、无时序依赖。
 */
import { ref, watchEffect, onScopeDispose } from 'vue';

export type TopbarTone = 'idle' | 'busy' | 'fail';

export interface TopbarStatus {
  /** 页面标识：避免切换页面时旧页面的清理动作覆盖新页面状态 */
  owner: string;
  text: string;
  tone: TopbarTone;
  actionText?: string;
  action?: () => void;
}

export const topbarStatus = ref<TopbarStatus | null>(null);

/** 页面 setup 中调用：持续把页面状态同步到顶栏；组件卸载自动清除。 */
export function useTopbarStatus(
  owner: string,
  get: () => Omit<TopbarStatus, 'owner'>,
): void {
  watchEffect(() => {
    topbarStatus.value = { owner, ...get() };
  });
  onScopeDispose(() => {
    if (topbarStatus.value?.owner === owner) topbarStatus.value = null;
  });
}
