// AI-6C §18/§19：Director Proposal 前端 domain 类型边界。
// 复用 AI-6A ChangeOperation（ChangeOp = Record<string,any>，src/api/index.ts:582），
// 禁止重新创造竞争数据模型。Proposal 的真正产生（Director LLM→operations）由
// `storyboard.propose_change` capability adapter 接通（本轮 implemented=False，未接通），
// 故正式产品路径不会有 Proposal 数据注入，UI 不显示 mock（§19/§38 场景10）。
import type { ChangeOp } from '@/api';

export interface ProposalChangeset {
  changeset_id: string;
  diff: any[];
  impact: { items: any[]; summary: Record<string, any> };
  warnings: string[];
  base_workspace_revision: number;
  after_signature: string;
}

export interface DirectorProposal {
  title: string;
  intent: string;
  operations: ChangeOp[];
  /** 真实 apply 需要绑定 workspace + 已 preview 的 changeset；缺则 UI 仅展示，不伪造 apply。 */
  workspaceId?: string;
  changeset?: ProposalChangeset | null;
}

export type ProposalKind = 'insert' | 'update' | 'delete' | 'reorder' | 'keep';

/** 把 ChangeOperation.op_type 映射成 Proposal 卡片类型。KEEP = 该 shot 不在 operations 里（UI 概念，无 op）。 */
export function opKind(op: ChangeOp): ProposalKind {
  switch (op?.op_type) {
    case 'insert_shot':   return 'insert';
    case 'update_shot':   return 'update';
    case 'delete_shot':   return 'delete';
    case 'reorder_shots': return 'reorder';
    default:              return 'keep';
  }
}

export interface ProposalSummary { insert: number; update: number; delete: number; reorder: number; total: number; }

export function summarize(ops: ChangeOp[]): ProposalSummary {
  const s = { insert: 0, update: 0, delete: 0, reorder: 0, total: 0 };
  for (const op of ops) {
    const k = opKind(op);
    if (k !== 'keep') { (s as any)[k]++; s.total++; }
  }
  return s;
}

/** InsertShot 定位 gap（agent5 调研）：NewShotSpec 无 after_shot_id；
 *  真要在某 shot 后插入，组合 INSERT_SHOT(append) + REORDER_SHOTS(ordered_shot_ids)。 */
export function buildInsertAfterOps(
  sceneId: string,
  afterShotId: string,
  newShot: { description: string; characters: string[]; target_duration_s?: number | null },
  orderedShotIds: string[],   // 含新 shot placeholder id 的完整期望顺序
): ChangeOp[] {
  return [
    { op_type: 'insert_shot', scene_id: sceneId, new_shot: { ...newShot } },
    { op_type: 'reorder_shots', scene_id: sceneId, ordered_shot_ids: orderedShotIds },
  ];
}
