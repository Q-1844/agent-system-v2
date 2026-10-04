// 可视化工作流（第一版·前端草稿）类型定义。
// 契约文档：docs/workflow/可视化工作流-依据表与草稿契约.md

export const DRAFT_SCHEMA_VERSION = 'workflow_draft_v1';

/** 第一版每项目固定一个浏览器草稿工作流 */
export const DEFAULT_WORKFLOW_ID = 'wf_default';

export type WorkflowNodeType =
  | 'agent'
  | 'skill'
  | 'human_review'
  | 'boundary_start'
  | 'boundary_end';

export type WorkflowEdgeType = 'execution' | 'skill_binding';

export type NodePort = 'out' | 'in' | 'bind';

/** 人工审核节点首版语义：通过→沿执行依赖继续；驳回→停止并留意见 */
export interface WorkflowNodeConfig {
  /** agent：从该 Agent 获准工具面中勾选的子集（仅草稿，不改变运行时权限） */
  selectedTools?: string[];
  /** agent：本节点任务目标与范围（Phase 46 运行必需字段；留空可存草稿，启动被后端阻止） */
  objective?: string;
  /** Phase 47 §4.2/§4.3：目标级完成契约（输出 spec + 完成策略） */
  completion?: {
    strategy?: 'require_new' | 'allow_verified_reuse';
    outputs?: Array<{
      kind: string;
      id?: string;
      minCount?: number;
      withImage?: boolean;
      withApprovedRef?: boolean;
      playable?: boolean;
      withCompletedRender?: boolean;
      allowReuse?: boolean;
    }>;
  };
  /** agent：节点任务范围（如 W08 的 videoVersions 显式素材声明） */
  scope?: Record<string, any>;
  /** human_review：审核对象来源节点（上游产物） */
  targetSourceNodeId?: string | null;
  /** human_review：审核对象/产物（可从上游产物受约束选择，也可草稿自由填写） */
  targetArtifact?: string;
  /** human_review：审核说明 */
  instructions?: string;
  /** human_review：审核者（草稿自由文本，仓库无正式审核者名册） */
  reviewer?: string;
  /** Phase 47 §4.6：审核「批准并选用」目标（versionId 运行期在卡片选定） */
  selection?: { kind: string; entityId: string; versionId?: string };
  /** 通用备注 */
  note?: string;
}

export interface WorkflowNode {
  id: string;
  type: WorkflowNodeType;
  /** agent=Agent ID；skill=skill_id；human_review/boundary=null */
  refId: string | null;
  /** skill=skill.json 的 version；其余 null */
  refVersion: string | null;
  /** 可选显示别名（人工审核常用，如「场景参考审定」） */
  label: string | null;
  config: WorkflowNodeConfig;
  position: { x: number; y: number };
}

export interface EdgeContract {
  /** verified=仓库有明确契约；pending_backend=待后端定义；human_gate=人工审核首版语义 */
  status: 'verified' | 'pending_backend' | 'human_gate';
  /** 依据（文件:行）或说明 */
  evidence: string;
  upstreamArtifacts: string[];
  downstreamReads: string[];
}

export interface WorkflowEdge {
  id: string;
  type: WorkflowEdgeType;
  source: string;
  target: string;
  sourcePort: NodePort;
  targetPort: NodePort;
  /** skill_binding：创建时从 Skill 节点钉住的版本 */
  skillVersion?: string | null;
  /** execution：产物依据 */
  contract?: EdgeContract | null;
}

export interface WorkflowDraft {
  schemaVersion: string;
  projectId: string;
  workflowId: string;
  name: string;
  status: 'draft';
  createdAt: string;
  updatedAt: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

export interface ValidationIssue {
  level: 'error' | 'warning';
  code: string;
  message: string;
  nodeId?: string | null;
  edgeId?: string | null;
}

export interface ValidationResult {
  ranAt: string;
  errors: number;
  warnings: number;
  issues: ValidationIssue[];
}

let seq = 0;
export function genId(prefix: string): string {
  seq = (seq + 1) % 1000;
  return `${prefix}_${Date.now().toString(36)}${seq.toString(36)}`;
}

export function makeNode(
  partial: Pick<WorkflowNode, 'type'> &
    Partial<Pick<WorkflowNode, 'refId' | 'refVersion' | 'label' | 'config' | 'position'>>
): WorkflowNode {
  const prefixByType: Record<WorkflowNodeType, string> = {
    agent: 'nag',
    skill: 'nsk',
    human_review: 'nrv',
    boundary_start: 'nst',
    boundary_end: 'nen',
  };
  return {
    id: genId(prefixByType[partial.type]),
    type: partial.type,
    refId: partial.refId ?? null,
    refVersion: partial.refVersion ?? null,
    label: partial.label ?? null,
    config: partial.config ?? {},
    position: partial.position ?? { x: 0, y: 0 },
  };
}
