// AI-6D Director Proposal 前端 domain 类型边界（接通真实后端）。
// 复用 AI-6A ChangeOperation（ChangeOp = Record<string,any>，src/api/index.ts:582），
// 禁止重新创造竞争数据模型。语义层 item 由后端 storyboard.propose_change 产出
// （Director LLM → 服务端校验 → item_id），编译成 ChangeOperation 由后端 deterministic 完成（§2/§7）。
// 前端只展示语义 item + 真实 changeset diff/impact/warnings，不伪造 apply（§13/§14）。
import type { ChangeOp } from '@/api';

/** AI-6A 预览结果（cs_engine.preview）压成的前端 changeset 形态。 */
export interface ProposalChangeset {
  changeset_id: string;
  status?: string;
  diff: any[];                 // ChangeSet Diff（§十九，按稳定 shot_id 对齐）
  impact: { items: any[]; summary: Record<string, any> };
  warnings: string[];
  base_workspace_revision: number;
  after_signature?: string | null;
}

/** 一个语义修改意图（≠ 一个 ChangeOperation；§7 一个 item 可编译为多个 op）。
 *  item_id / shot_id(stable) 全部由后端产出，LLM 不发明（§2/§6）。 */
export interface DirectorProposalItem {
  item_id: string;
  action: 'update_shot' | 'insert_shot' | 'delete_shot' | 'reorder_shot';
  scene_id: string;
  shot_id?: string | null;           // update/delete/reorder 目标；insert 无（后端 allocate）
  description?: string | null;      // 新描述（update/insert 的 after）
  characters?: string[] | null;    // 新角色列表（update/insert 的 after）
  target_duration_s?: number | null;
  after_shot_id?: string | null;    // insert/reorder 位置锚点（插/移到谁之后；null=末尾/段首）
  reason?: string;
}

/** 后端 proposal_to_api 返回的 currentProposal 形态（snake_case，对齐后端）。 */
export interface DirectorProposal {
  proposal_id: string;
  project?: string;
  agent_id?: string;
  scope_type: 'shot' | 'scene';
  scope_id: string;
  instruction?: string;
  title: string;
  summary: string;                  // Director 一句话意图（subtitle）
  items: DirectorProposalItem[];
  workspace_id: string;
  base_workspace_revision: number;
  full_preview_changeset_id?: string;
  selected_preview_changeset_id?: string | null;
  applied_changeset_id?: string | null;
  status: string;                  // created | selected_previewed | applied | dismissed | stale
  base_is_stale?: boolean;         // 派生：canonical 自 workspace 创建后已变（§17）
  changeset?: ProposalChangeset | null;   // 当前预览（full 或 selected），apply 用它的 changeset_id
  created_at?: number;
  updated_at?: number;
  /** 兼容：stale 时后端返回 {stale:true,...}，无 changeset。 */
  stale?: boolean;
}

export type ProposalKind = 'insert' | 'update' | 'delete' | 'reorder' | 'keep';

/** 语义 item → 卡片类型（§14：按 item 展示，不按 raw op）。KEEP = 该 shot 不在任何 item 里（UI 概念，无 op，§15）。 */
export function itemKind(item: DirectorProposalItem): ProposalKind {
  switch (item?.action) {
    case 'insert_shot':   return 'insert';
    case 'update_shot':   return 'update';
    case 'delete_shot':   return 'delete';
    case 'reorder_shot':  return 'reorder';
    default:              return 'keep';
  }
}

/** ChangeOperation.op_type → 卡片类型（diff 卡片用，operations 级；保留兼容）。 */
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

export function summarizeItems(items: DirectorProposalItem[]): ProposalSummary {
  const s = { insert: 0, update: 0, delete: 0, reorder: 0, total: 0 };
  for (const it of items) {
    const k = itemKind(it);
    if (k !== 'keep') { (s as any)[k]++; s.total++; }
  }
  return s;
}

export function summarize(ops: ChangeOp[]): ProposalSummary {
  const s = { insert: 0, update: 0, delete: 0, reorder: 0, total: 0 };
  for (const op of ops) {
    const k = opKind(op);
    if (k !== 'keep') { (s as any)[k]++; s.total++; }
  }
  return s;
}
