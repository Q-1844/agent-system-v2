// 浏览器草稿存取：按「项目标识 + 工作流标识」隔离，刷新后可恢复。
// 第一版不接后端——保存即写 localStorage，键形如 wf_draft::{projectId}::{workflowId}。

import { DRAFT_SCHEMA_VERSION, DEFAULT_WORKFLOW_ID } from './types';
import type { WorkflowDraft } from './types';

export function draftStorageKey(projectId: string, workflowId: string): string {
  return `wf_draft::${projectId}::${workflowId}`;
}

export function loadDraft(projectId: string, workflowId = DEFAULT_WORKFLOW_ID): WorkflowDraft | null {
  try {
    const raw = localStorage.getItem(draftStorageKey(projectId, workflowId));
    if (!raw) return null;
    const data = JSON.parse(raw) as WorkflowDraft;
    if (data?.schemaVersion !== DRAFT_SCHEMA_VERSION) return null; // 版本不符：不恢复
    if (!Array.isArray(data.nodes) || !Array.isArray(data.edges)) return null;
    return data;
  } catch {
    return null;
  }
}

export function saveDraft(draft: WorkflowDraft): void {
  const payload: WorkflowDraft = { ...draft, updatedAt: new Date().toISOString() };
  localStorage.setItem(draftStorageKey(draft.projectId, draft.workflowId), JSON.stringify(payload));
}

export function clearDraft(projectId: string, workflowId = DEFAULT_WORKFLOW_ID): void {
  localStorage.removeItem(draftStorageKey(projectId, workflowId));
}

export function newEmptyDraft(projectId: string, name: string): WorkflowDraft {
  const now = new Date().toISOString();
  return {
    schemaVersion: DRAFT_SCHEMA_VERSION,
    projectId,
    workflowId: DEFAULT_WORKFLOW_ID,
    name,
    status: 'draft',
    createdAt: now,
    updatedAt: now,
    nodes: [],
    edges: [],
  };
}
