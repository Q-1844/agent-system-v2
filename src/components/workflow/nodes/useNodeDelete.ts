// 节点快捷删除（画布管理基本操作）：
// WorkflowStudio provide 控制器，各节点组件 inject 后由悬停 ✕ 直接触发，
// 与右侧面板「删除此节点」等效（节点与相关连线一并移除）。
import { inject } from 'vue';
import type { WorkflowAssistantController } from '../assistant';

export function useNodeDelete(props: { id?: string }) {
  const controller = inject<WorkflowAssistantController | null>('wfController', null);
  function onDelete() {
    if (props.id && controller) controller.removeNodeById(props.id);
  }
  return { controller, onDelete };
}
