/**
 * API客户端
 * 与后端API通信
 */

const API_BASE_URL = 'http://localhost:8000';

import type { DirectorProposal } from '@/components/storyboard/proposal';   // AI-6D（type-only，无运行时循环）

export interface Project {
  name: string;
  path: string;
}

export interface Scene {
  id: number;
  description: string;
  characters: string[];
  camera: string;
  lighting: string;
  motion: string;
  dialogue?: string;
  status: string;
  image_path?: string;
  video_path?: string;
  audio_path?: string;
  subtitle_path?: string;
  transitions?: {
    to_next?: string;
    from_prev?: string;
  };
  score?: {
    total: number;
    character: number;
    style: number;
    composition: number;
    clarity: number;
  };
}

export interface Character {
  name: string;
  description: string;
  reference_images: {
    front?: string;
    side?: string;
    full?: string;
  };
}

export interface TaskStatus {
  task_id: string;
  status: 'idle' | 'pending' | 'running' | 'completed' | 'failed';
  progress: Record<string, any>;
  message: string;
}

export interface ProjectOutputs {
  characters: Array<{ name: string; path: string; size: number }>;
  storyboards: Array<{ name: string; path: string; size: number }>;
  videos: Array<{ name: string; path: string; size: number }>;
  audio: Array<{ name: string; path: string; size: number }>;
  subtitles: Array<{ name: string; path: string; size: number }>;
  storyboard_html?: string;
  project_report?: string;
}

// ============ 阶段 3：shot 级管线 ============
export type JobKind = 'first_frame' | 'last_frame' | 'audio' | 'video' | 'compose';
export type JobState = 'queued' | 'running' | 'done' | 'failed' | 'cancelled';

export interface QueueStats {
  video_queued: number;
  video_eta_s: number;
  running: number;
  failed: number;
}

export interface Job {
  job_id: string;
  project: string;
  kind: JobKind;
  shot_id: string | null;
  params: Record<string, any>;
  state: JobState;
  progress: number;
  message: string;
  error: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  attempts: number;
  queue?: QueueStats;
}

export interface OrchestrateResult {
  dry_run: boolean;
  plan: Array<[string, string]>;   // [kind, shot_id]
  plan_count: number;
  video_count: number;
  eta_minutes?: number;
  video_seconds_needed?: number;
  job_ids?: string[];
  queue?: QueueStats;
}

// v2 shot 产物
export interface AssetData {
  local: string | null;
  url: string | null;
  status: string;
  error: string | null;
  updated_at: number;
}
export interface ImageAssetData {
  candidates: AssetData[];
  selected_index: number;
  status: string;
  error: string | null;
  updated_at: number;
}
export interface VideoAssetData extends AssetData {
  video_id: string | null;
  task_id: string | null;
  num_frames: number;
  frame_rate: number;
  seconds_returned: number | null;
  size_returned: string | null;
  attempts: number;
}
export interface AudioAssetData extends AssetData {
  duration_ms: number;
  words: Array<{ text: string; offset_ms: number; duration_ms: number }>;
  voice: string;
  rate: string;
}
// 批次历史（Step 2）
export interface CandidateData {
  candidate_id: string;
  local: string | null;
  url: string | null;
  status: string;
  error: string | null;
  created_at: number;
}
export interface ImageBatchData {
  batch_id: string;
  kind: string;            // first_frame | last_frame
  created_at: number;
  status: string;
  prompt_snapshot: string;
  char_ref_snapshot: Record<string, any>;
  based_on_first_frame: string | null;   // 尾帧批次：基于的首帧 candidate_id
  candidates: CandidateData[];
}
export interface VideoInputVisualData {
  slot: string;            // first | last | reference
  semantic_role: string;   // hard_start_boundary | hard_end_boundary | relay_start_boundary | reference_only
  url: string;
  source_type?: string;
  source_shot_id?: string;
  end_source_version?: string;
  reference_asset_id?: string;
  candidate_id?: string;
  batch_id?: string;
}
export interface VideoVersionData {
  version_id: string;
  created_at: number;
  status: string;
  prompt_snapshot: string;
  char_ref_snapshot: Record<string, any>;
  first_frame_candidate_id: string | null;
  last_frame_candidate_id: string | null;
  video_id: string | null;
  task_id: string | null;
  local: string | null;
  url: string | null;
  seconds_returned: number | null;
  size_returned: string | null;
  num_frames: number;
  frame_rate: number;
  input_visuals?: VideoInputVisualData[];
}
export interface ShotData {
  shot_id: string;
  scene_id: string;
  index: number;
  description: string;
  prompt_en: string;
  negative_prompt: string;
  shot_size: string;
  camera: string;
  lighting: string;
  motion: string;
  characters: string[];
  dialogue: string;
  link_mode: string;
  seed: number | null;
  // 结构化镜头字段（提示词.md 工作台）
  opening_state: string;
  camera_angle: string;
  composition: string;
  important_details: string;
  required_elements: string[];
  last_frame_description: string;
  end_state: string;
  frame_change: string;
  preserve_elements: string[];
  add_elements: string;
  remove_elements: string;
  end_composition: string;
  preserve_custom: string;
  camera_movement: string;
  motion_pace: string;
  environment_motion: string;
  action_sequence: string;
  camera_movement_detail: string;
  transition_description: string;
  video_preserve_elements: string[];
  video_preserve_custom: string;
  target_duration_s: number | null;
  // 三类独立提示词 + override + sig
  last_frame_prompt_en: string;
  video_prompt_en: string;
  first_frame_prompt_override: string;
  last_frame_prompt_override: string;
  video_prompt_override: string;
  first_frame_prompt_sig: string;
  last_frame_prompt_sig: string;
  video_prompt_sig: string;
  // 镜头接力 / 开场来源（Phase 6A）
  opening_source: string;            // independent | reference_previous | inherit_previous
  source_shot_id: string | null;
  source_version: string | null;
  video_end_frame: AssetData;        // 本镜最终视频的实际末帧（ffmpeg 提取）
  video_end_source_version: string | null;
  first_frame: ImageAssetData;
  last_frame: ImageAssetData;
  audio: AudioAssetData;
  subtitle_srt: string | null;
  video: VideoAssetData;
  // 批次历史（Step 2）+ 选择指针
  first_frame_batches: ImageBatchData[];
  last_frame_batches: ImageBatchData[];
  video_versions: VideoVersionData[];
  selected_first_frame: string | null;   // candidate_id
  selected_last_frame: string | null;
  final_video: string | null;            // version_id
  status: string;
  locked: boolean;
  note: string;
  split_next: string | null;
}
export interface ProjectShots {
  name: string;
  style: Record<string, any>;
  story_text: string;
  characters: Array<{ name: string; description_en: string; views: Record<string, AssetData> }>;
  scenes: Array<{
    scene_id: string; title: string; location: string; time_of_day: string; summary: string;
    characters: string[];
    shots: ShotData[];
  }>;
}
export interface TimingShot {
  shot_id: string;
  scene_id: string;
  audio_seconds: number;
  planned_seconds: number;
  num_frames: number;
  needs_split: boolean;
  dialogue: string;
}

// ============ AI 整理故事（Phase AI-1）============
export interface DraftScene {
  draft_id: string;
  summary: string;
  character_mentions: string[];
}

export type CharacterResolutionStatus = 'matched' | 'unresolved' | 'created' | 'ignored';

export interface CharacterResolutionItem {
  mention: string;
  status: CharacterResolutionStatus;
  matched_character_name: string | null;
  draft_description: string | null;
}

export interface StoryOrganizationDraft {
  draft_id: string;
  project_name: string;
  project_updated_at: number;
  story_text_snapshot: string;
  overview: string;
  scenes: DraftScene[];
  characters: CharacterResolutionItem[];
}

export interface ApplyStoryResult {
  ok: boolean;
  scenes: Array<{ scene_id: string; summary: string; characters: string[] }>;
}

// ============ AI 续写故事（Phase AI-1B）============
export type ContinuationScale = 'short' | 'standard' | 'long';

export interface StoryContinuationDraft {
  draft_id: string;
  project_name: string;
  base_story_text: string;
  base_scene_signature: Array<{ scene_id: string; summary: string; characters: string[] }>;
  direction: string;
  scale: ContinuationScale;
  continuation_text: string;
  new_scene_drafts: DraftScene[];
  character_resolution: CharacterResolutionItem[];
  stage: string;
  created_at: number;
}

export interface ApplyContinuationResult {
  ok: boolean;
  appended_story_chars: number;
  new_scenes: Array<{ scene_id: string; summary: string; characters: string[] }>;
  new_characters: string[];
  first_new_scene_id: string | null;
}

// ============ AI 角色设计（Phase AI-2）============
export interface CharacterDesignDraft {
  character_name: string;
  role_summary: string;
  visual_concept: string;
  appearance: string;
  clothing_or_body: string;
  distinctive_features: string;
  color_palette: string;
  consistency_rules: string[];
  generation_prompt: string;
}

export interface CharacterDesignDraftState {
  character_name: string;
  base_character_name: string;
  base_character_description: string;
  base_related_scene_signature: Array<{ scene_id: string; summary: string }>;
  base_project_style_signature: string;
  draft: CharacterDesignDraft;
  status: string;   // draft / applied / stale
  created_at: number;
}

// ============ AI 导演（Phase AI-3）============
export interface DirectorShotDraft {
  draft_id: string;
  intent: string;
  characters: string[];
  target_duration_s: number;
  purpose: string;
}

export interface DirectorDraft {
  draft_id: string;
  scene_id: string;
  overview: string;
  shots: DirectorShotDraft[];
  scene_summary_snapshot: string;
  scene_characters_snapshot: string[];
  director_note: string;
  created_at: number;
}

export interface DirectorApplyResult {
  ok: boolean;
  shots: Array<{ shot_id: string; scene_id: string; index: number; description: string; characters: string[]; target_duration_s: number }>;
  first_new_shot_id: string | null;
}

// ============ AI 制作规划（Phase AI-4A）============
// AI 只输出 opening_source/link_mode/reason；source_shot_id / generate_first_frame /
// generate_last_frame / requires_previous_video_end / auto_select_policy 由后端代码推导。
export interface PlannedShot {
  shot_id: string;
  opening_source: string;        // independent | reference_previous | inherit_previous
  link_mode: string;             // i2v | keyframes
  source_shot_id: string | null; // reference/inherit -> 紧邻上一镜（代码推导，不可用户选）
  generate_first_frame: boolean; // = opening_source != inherit_previous
  generate_last_frame: boolean;  // = link_mode == keyframes
  generate_video: boolean;       // 恒 true（第一版）
  requires_previous_video_end: boolean; // reference/inherit -> true
  auto_select_policy: string;    // seeded_random
  reason: string;
}

export interface SceneProductionPlan {
  plan_id: string;
  project_name: string;
  scene_id: string;
  scene_summary_snapshot: string;
  scene_characters_snapshot: string[];
  shot_structure_snapshot: Array<{ shot_id: string; description: string; characters: string[]; target_duration_s: number | null }>;
  run_seed: number;
  overview: string;
  shots: PlannedShot[];
  status: string;            // draft | approved
  created_at: number;
  approved_at: number | null;
  is_stale: boolean;         // 派生标志：scene/shots 结构已变 -> 需重新规划
  stale_reason: string | null;
}

// ============ AI 自动制作（Phase AI-4B Scene Autopilot Executor）============
export interface ShotExecutionState {
  shot_id: string;
  index: number;
  status: string;            // pending | waiting_dependency | running | skipped_locked | failed | completed
  stage: string | null;      // activate_plan/first_prompt/first_frame/.../video_end
  active_job_id: string | null;
  active_stage_key: string | null;
  completed_stages: string[];
  first_prompt: string | null;
  first_batch_id: string | null;
  selected_first_candidate_id: string | null;
  last_prompt: string | null;
  last_batch_id: string | null;
  selected_last_candidate_id: string | null;
  video_prompt: string | null;
  video_version_id: string | null;
  attempts: Record<string, number>;
  warnings: string[];
  error: string | null;
}

export interface SceneAutopilotRun {
  run_id: string;
  project_name: string;
  scene_id: string;
  plan_id: string;
  plan_snapshot: Record<string, any>;
  run_seed: number;
  status: string;            // pending|running|pausing|paused|blocked|failed|completed|cancelled
  respect_locked: boolean;
  pause_requested: boolean;
  cancel_requested: boolean;
  current_shot_id: string | null;
  current_stage: string | null;
  shots: ShotExecutionState[];
  error: string | null;
  error_code: string | null;
  created_at: number;
  updated_at: number;
}

// ============ 完整影片自动制作（Phase AI-5A Prepared Project Autopilot）============
export interface ProjectSceneExecutionState {
  scene_id: string;
  index: number;
  status: string;            // pending|running|paused|failed|blocked|completed|cancelled
  plan_id: string | null;
  child_run_id: string | null;
  shot_count: number;
  error: string | null;
}

export interface ProjectProductionRun {
  run_id: string;
  project_name: string;
  status: string;            // pending|running|pausing|paused|failed|blocked|composing|completed|cancelled
  stage: string;             // preflight|scenes|compose|done
  respect_locked: boolean;
  pause_requested: boolean;
  cancel_requested: boolean;
  current_scene_id: string | null;
  current_child_run_id: string | null;
  scenes: ProjectSceneExecutionState[];
  source_signature: Record<string, any>;
  source_snapshot_file: string | null;
  plans_snapshot_file: string | null;
  composition_manifest_file: string | null;
  final_output: string | null;
  final_duration_s: number | null;
  final_size_bytes: number | null;
  error: string | null;
  error_code: string | null;
  created_at: number;
  updated_at: number;
}

export interface CompositionClip {
  scene_id: string;
  shot_id: string;
  scene_run_id: string;
  video_version_id: string;
  local_path: string | null;
  url: string | null;
  duration_s: number | null;
}

export interface ScenePreflightResult {
  scene_id: string;
  ready: boolean;
  shot_count: number;
  plan_id: string | null;
  plan_status: string | null;
  issues: string[];          // NO_SHOTS|NO_PRODUCTION_PLAN|PLAN_NOT_APPROVED|PLAN_STALE|ACTIVE_SCENE_RUN|...
}

export interface ProjectProductionPreflight {
  ready: boolean;
  scene_count: number;
  shot_count: number;
  locked_shot_count: number;
  scenes: ScenePreflightResult[];
}

// ============ AI 完整制作（Phase AI-5B）============
export interface FullProjectSceneInspect {
  scene_id: string;
  state: string;              // READY|NEED_DIRECTOR|NEED_PRODUCTION_PLAN|BLOCKED
  shot_count: number;
  has_valid_director_draft: boolean;
  plan_state: string | null;  // none|draft|approved|stale
  detail: string;
}

export interface FullProjectInspect {
  ready: boolean;             // 无 blocker（缺 Shot/Plan 不是 blocker，AI-5B 自动补齐）
  scene_count: number;
  shot_count: number;
  estimated_new_shots: number | null;   // 始终 null（Director 没跑不能预估，§五十二）
  scenes: FullProjectSceneInspect[];
  warnings: string[];         // CHARACTER_WITHOUT_VIEWS:角色名
  blockers: string[];         // NO_SCENES|MISSING_PROJECT_CHARACTER|ACTIVE_*_RUN
}

export interface FullProjectSceneState {
  scene_id: string;
  index: number;
  initial_state: string;
  status: string;             // pending|preparing|ready|failed|blocked
  action: string | null;      // inspect|director_plan|director_apply|planner_plan|planner_approve|done
  used_existing_director_draft: boolean;
  created_shot_ids: string[];
  used_existing_production_plan: boolean;
  production_plan_id: string | null;
  production_plan_source: string | null;
  expected_plan_fingerprint: string | null;
  completed_actions: string[];
  attempts: Record<string, number>;
  warnings: string[];
  error: string | null;
}

export interface FullProjectRun {
  run_id: string;
  project_name: string;
  status: string;             // pending|preparing|pausing|paused|producing|failed|blocked|completed|cancelled
  stage: string;              // inspect|prepare|finalize_preparation|production|done
  respect_locked: boolean;
  pause_requested: boolean;
  cancel_requested: boolean;
  current_scene_id: string | null;
  current_action: string | null;
  scenes: FullProjectSceneState[];
  expected_source_signature: Record<string, any>;
  preparation_manifest_file: string | null;
  initial_source_snapshot_file: string | null;
  production_run_id: string | null;
  final_output: string | null;
  error: string | null;
  error_code: string | null;
  created_at: number;
  updated_at: number;
}

// ============ AI-6A Creative Workspace / Semantic ChangeSet ============
export interface WorkspaceMeta {
  workspace_id: string;
  project_name: string;
  name: string;
  status: string;            // active | archived
  revision: number;
  base_project_signature: string;
  working_project_signature: string;
  base_project_file: string;
  working_project_file: string;
  shot_seq: Record<string, number>;
  created_at: number;
  updated_at: number;
  archived_at: number | null;
  base_is_stale?: boolean;   // 派生：canonical 已变（GET 时后端补）
}
export type ChangeOp = Record<string, any>;   // 语义 operation dict（update_scene/update_shot/insert_shot/delete_shot/reorder_shots）
export interface PreviewResult {
  changeset_id: string;
  status: string;
  diff: any[];                // ChangeSet Diff（§十九）
  impact: { items: any[]; summary: Record<string, any> };
  warnings: string[];
  base_workspace_revision: number;
  after_signature: string;
}

// ============ AI-6B Multi-Agent Foundation ============
export interface AgentModelProfile {
  id: string; name: string;
  reasoning_model: string | null;
  fallback_reasoning_model: string | null;
  image_model: string | null;
  video_model: string | null;
  account_policy: string;
}
export interface AgentInfo {
  id: string; name: string; description: string; role: string;
  read_scopes: string[]; write_scopes: string[];
  can_delegate_to: string[];
  model_profile: AgentModelProfile | null;
  enabled: boolean;
}
export interface AgentCapability {
  id: string; name: string; description: string; category: string;
  owner_agent: string; allowed_agents: string[]; operations: string[];
  risk_level: string; implemented: boolean; can_execute: boolean;
}
export interface AgentMessage {
  role: string; content: string; status: string | null; created_at: number;
  /** AI-7D D1：unified_turn 结构化真相（intent/evidence_refs/events/linked_*）。
   * null=普通聊天消息。UI 只许据 meta 的真实 backend 记录表示「已查看」。 */
  meta?: AgentMessageMeta | null;
}
/** AI-7D D1：Agent assistant 消息携带的 unified_turn 真相（落盘，刷新可恢复）。 */
export interface AgentMessageMeta {
  intent: 'conversation' | 'project_inquiry' | 'runtime_query';
  evidence_refs: AgentEvidenceRef[];
  events: AgentTurnEvent[];
  linked_task_ids: string[];
  linked_change_session_id: string | null;
}
export interface AgentEvidenceRef {
  module: string; entity_type: string; entity_id: string;
  revision_id?: string; artifact_type?: string; artifact_id?: string; label?: string;
}
/** AI-7D D1：Direct Chat turn 的真后台事件（非模型叙述）。 */
export interface AgentTurnEvent {
  kind: string; summary: string; refs?: AgentEvidenceRef[];
}

// ============ Phase 46 · 可视化工作流（六员后端接入）类型 ============
export interface ValidationIssue2 {
  level: 'error' | 'warning'; code: string; message: string;
  nodeId: string | null; edgeId: string | null;
}
export interface WorkflowCatalogAgent {
  id: string; name: string; role: string; description: string;
  readScopes: string[]; writeScopes: string[]; canDelegateTo: string[];
  coreTools: string[]; optionalTools: string[]; toolCount: number;
  writeDomains: string[]; expectedArtifactDomains: string[];
  skillBindable: boolean; registryDigest: string; turnEntry: string;
  enabled: boolean; unavailableReason: string | null;
  modelProfile: AgentModelProfile | null;
}
export interface WorkflowCatalogSkill {
  id: string; version: string; name: string; description: string;
  applicability: string; category: string; source: string; status: string;
  dependsOn: string[];
  bindings: { agentId: string; role: string; routingPriority: number; activationMode: string }[];
  contentHash: string; registryError?: string;
}
export interface WorkflowCatalog {
  catalogVersion: string;
  agents: WorkflowCatalogAgent[];
  skills: WorkflowCatalogSkill[];
  notes: string[];
}
export interface WorkflowDefinitionDoc {
  schemaVersion: string; projectId: string; workflowId: string;
  name?: string; revision: number; createdAt?: string; updatedAt?: string;
  contentHash?: string; nodes: any[]; edges: any[];
}
export interface WorkflowSaveResult {
  workflowId: string; revision: number; updatedAt: string; contentHash: string;
  validation: { ok: boolean; errors: ValidationIssue2[]; warnings: ValidationIssue2[] };
}
export interface WorkflowAttempt {
  attemptId: string; status: string; startedAt: number; endedAt: number | null;
  taskRefs: string[]; result: any | null; error: string | null;
  inputs?: any; outputs?: any; skillEvidence?: any; thinking?: boolean;
  session?: string;
}
export interface WorkflowRunNode {
  nodeId: string; type: string; refId: string | null; status: string;
  attempts: WorkflowAttempt[]; currentAttemptId: string | null;
  handoff: any | null; error: string | null; upstreamNodeId?: string | null;
  statusAt?: number; currentReviewId?: string; lastReviewDecision?: string;
}
export interface WorkflowReviewRequest {
  requestId: string; nodeId: string; reviewIndex?: number; label?: string;
  targetNodeId: string | null; targetAttemptId: string | null;
  targetArtifact: string; instructions: string; reviewerLabel: string;
  artifactRefs: { kind: string; id: string; source?: string }[];
  artifactFingerprint: string;
  // Phase 47 §4.6：批准并选用（用户在审核卡片上显式确认；服务端走既有用户入口同层服务）
  selectionRequired?: boolean;
  selectionSpec?: { kind: string; entityId: string; versionId?: string } | null;
  selectionCandidates?: { versionId: string; status?: string; selected?: boolean; approved?: boolean }[];
  selectionState?: 'none' | 'pending' | 'done' | 'failed';
  selectionReceipt?: any | null;
  status: 'pending' | 'decided' | 'cancelled';
  createdAt: number; decidedAt: string | null; decision: string | null;
  decisionNote: string | null; decisionReceipt: any | null;
}
export interface WorkflowRun {
  runId: string; project: string; workflowId: string; status: string;
  idempotencyKey: string; startParamsHash: string;
  snapshot: any; plan: any;
  nodes: Record<string, WorkflowRunNode>;
  reviews: WorkflowReviewRequest[];
  cursor: number; stopRequested: boolean; error: string | null;
  createdAt?: string; updatedAt?: string; workerRunning?: boolean;
}

/** AI-7D D1 · 统一 Direct Chat turn 结果（POST /turn）。assistant_message 是用户唯一所见。 */
export interface AgentTurnResult {
  turn_id: string; session_id: string; agent_id: string;
  intent: 'conversation' | 'project_inquiry' | 'runtime_query';
  assistant_message: string;
  events: AgentTurnEvent[];
  linked_task_ids: string[];
  linked_change_session_id: string | null;
  evidence_refs: AgentEvidenceRef[];
  /** 落盘的 assistant AgentMessage（/messages wrapper 等价返回它）。 */
  message: AgentMessage;
}

// ============ AI-7D-R Script Writer /turn 结果（16 字段，与 backend script_writer_turn 对齐） ============
/** backend script_writer_turn 实际返回结构。/turn 的 JSON 即此形状。 */
export interface ScriptWriterTurnResult {
  assistant_message: string;
  edited: boolean; created: boolean; moved: boolean;
  scene_updated: boolean; deleted: boolean;
  events: AgentTurnEvent[];
  /** 落盘的 assistant AgentMessage（含 meta：receipts/trace/write_attempts/...）。 */
  message: AgentMessage;
  intent: ScriptWriterIntent;
  receipts: ScriptWriterReceipt[];
  trace: ScriptWriterTraceEntry[];
  reasonings?: ScriptWriterReasoningEntry[];
  /** 本轮 web.search 轻量来源（title+url；无 snippet）。 */
  search_sources?: ScriptWriterSearchSource[];
  evidence_refs: AgentEvidenceRef[];
  linked_task_ids: string[];
  linked_change_session_id: string | null;
  session_id: string;
  agent_id: string;
}
export type ScriptWriterIntent =
  | 'story_create' | 'story_move' | 'story_update' | 'story_delete'
  | 'script_update' | 'conversation'
  // Character Designer（2026-08-30）：同结构 turn 结果，intent 值不同
  | 'character_create' | 'character_update' | 'character_delete'
  | 'character_views'
  // Director（2026-08-30）：分镜域写操作
  | 'shot_create' | 'shot_update' | 'shot_delete' | 'shot_move';
/** meta.receipts 单条回执（写工具真实结果，UI「本轮改动」只信它）。 */
export interface ScriptWriterReceipt {
  operation: string; ok: boolean; verified: boolean; stale: boolean;
  unchanged: boolean; confirmed: boolean;
  error: string | null;
  scene_id: string | null; order: number | null; revision: string | null;
  /** Character Designer 回执：操作对象是角色名（scene_id 为 null）。 */
  character_name?: string | null;
  /** character.generate_views：真实生成的三视图（local 路径）。 */
  generated?: { character_name: string; views: Record<string, string> }[];
  character_names?: string[];
  elapsed_s?: number;
  /** Director 回执：操作对象是镜头 id（shot 域）。 */
  shot_id?: string | null;
  changed?: string[] | null;
  index?: number | null;
  count?: number | null;
  created?: { shot_id: string; index: number; description: string }[];
  results?: { shot_id: string; changed: string[]; scene_id?: string }[];
}
/** meta.trace 单条执行轨迹（step→tool→target→result→revision）。 */
export interface ScriptWriterTraceEntry {
  step: number; tool: string; target: string | null;
  result: string; revision: string | null;
  error: string | null; ok: boolean;
}
/** meta.reasonings 单条思考记录（每个 LLM step 的推理文本，持久化用于刷新后重建折叠条）。 */
export interface ScriptWriterReasoningEntry {
  step: number; text: string; duration_ms?: number;
}
/** meta.search_sources 单条轻量搜索来源（仅 title+url；snippet/正文不跨轮持久化）。 */
export interface ScriptWriterSearchSource {
  title: string; url: string;
}

// ============ AI-7D-R /turn/stream 流式事件（discriminated union） ============
export interface TurnStartedEvent { type: 'turn.started'; thinking: boolean }
export interface ReasoningStartedEvent { type: 'reasoning.started'; step_id: number }
export interface ReasoningDeltaEvent { type: 'reasoning.delta'; step_id: number; delta: string }
export interface ReasoningCompletedEvent { type: 'reasoning.completed'; step_id: number; duration_ms: number }
export interface ToolPendingEvent { type: 'tool.pending'; step_id: number; name: string; label: string }
export interface ToolStartedEvent { type: 'tool.started'; step_id: number; call_id: string; name: string; label: string }
export interface ToolCompletedEvent { type: 'tool.completed'; step_id: number; call_id: string; name: string; ok: boolean; summary: string }
export interface ToolFailedEvent { type: 'tool.failed'; step_id: number; call_id: string; name: string; error: string }
export interface AssistantDeltaEvent { type: 'assistant.delta'; delta: string }
/** Character Designer 三视图生成进度（长任务，百分比推进）。 */
export interface ViewsProgressEvent {
  type: 'views.progress'; character_name: string; view: string;
  message: string; pct: number;
}
/** Director 视觉生成等长任务的批次进度（tool.progress，如「已完成 3/8」）。 */
export interface ToolProgressEvent {
  type: 'tool.progress'; step_id: number; call_id: string; name: string;
  label?: string; message: string; done?: number; total?: number; pct?: number;
}
export interface TurnCompletedEvent { type: 'turn.completed'; result: ScriptWriterTurnResult }
export interface TurnFailedEvent { type: 'turn.failed'; error: string }
/** 前端据 event.type 安全分支处理。turn.completed.result 仍是现有完整最终结构。 */
export type AgentStreamEvent =
  | TurnStartedEvent | ReasoningStartedEvent | ReasoningDeltaEvent
  | ReasoningCompletedEvent | ToolPendingEvent | ToolStartedEvent | ToolCompletedEvent
  | ToolFailedEvent | AssistantDeltaEvent | ViewsProgressEvent | ToolProgressEvent
  | TurnCompletedEvent | TurnFailedEvent;
export interface AgentSession {
  session_id: string; project: string; agent_id: string;
  scope_type: string; scope_id: string;
  messages: AgentMessage[]; created_at: number; updated_at: number;
}
export interface AgentTask {
  task_id: string; project: string; requester_agent: string; target_agent: string;
  capability_id: string; instruction: string; scope_type: string; scope_id: string;
  session_id: string; operation: string; status: string;
  result_summary: string; artifact_refs: any[];
  structured_result: any; warnings: string[]; suggested_next_actions: string[];
  error: string | null; created_at: number; updated_at: number;
}

// ============ Producer V1 Phase 3 · Production Room 投影（GET /agents/producer/room）============
/** changed_refs（receipts 派生的真实修改范围）。 */
export interface RoomChangedRefs { scenes: string[]; shots: string[]; characters: string[] }
/**
 * Production Room 单条消息（backend production_room.py 投影）。
 * 稳定 ID：user/producer 用 session message_id；delegation/result 用
 * task:{task_id}:dispatch|result —— snapshot 与 SSE live 同键去重，不依赖内容相等。
 */
export interface RoomMessage {
  id: string;
  kind: 'user' | 'producer' | 'delegation' | 'specialist_result';
  timestamp: number;
  sender: string;
  recipient?: string;
  content: string;
  // user / producer 消息
  status?: string | null;
  intent?: string | null;
  linked_task_ids?: string[];
  // delegation（Producer → Specialist）
  task_id?: string;
  workflow_id?: string;
  situation?: string;
  constraints?: string[];
  scope?: Record<string, any>;
  live?: boolean;
  parent_task_id?: string | null;
  capability_id?: string;
  session_id?: string;
  execution_ref?: string | null;
  task_created_at?: number;
  task_updated_at?: number;
  // specialist_result（Specialist → Producer）
  full_reply?: string | null;
  full_reply_truncated?: boolean;
  verified?: boolean | null;
  changed_refs?: RoomChangedRefs;
  warnings?: string[];
  n_tool_calls?: number | null;
}
/** roster 单个成员状态（只来自 AgentTask / turn_registry / session 真实事实）。 */
export interface RoomParticipant {
  agent_id: string;
  status: string;   // working | completed | cancelled | failed | interrupted | idle | not_configured
  not_called?: boolean;
  task_id?: string | null;
  workflow_id?: string | null;
  verified?: boolean | null;
  changed_refs?: RoomChangedRefs;
  n_tool_calls?: number | null;
  summary?: string;
  updated_at?: number;
}
export interface RoomWorkflow {
  workflow_id: string; goal: string; status: string;
  root_task_id?: string | null; task_ids: string[];
  created_at: number; updated_at: number;
  last_coordination_summary?: string;
}
export interface RoomSnapshot {
  room_id: string;
  project_id: string;
  session_id: string | null;
  turn_active: boolean;
  generated_at: number;
  workflow: RoomWorkflow | null;
  participants: RoomParticipant[];
  messages: RoomMessage[];
}

// ============ AI-7A Team Group Chat ============
export interface TeamThread {
  thread_id: string; project_name: string; created_at: number; updated_at: number;
}
export interface TeamMessage {
  message_id: string; thread_id: string;
  message_type: string;            // user | agent | summary_card | analysis_card | system
  agent_id: string | null; text: string | null;
  source_session_id: string | null; source_task_id: string | null;
  source_change_session_id: string | null;
  card_data: any; pending_inquiry: boolean; status: string | null; created_at: number;
}
export interface TeamThreadData {
  thread: TeamThread; messages: TeamMessage[];
}

// ============ AI-7A Change Analysis Session（analysis-only，§31/§37）============
export interface ChangePlanDraftItem {
  module: string; owner_agent_id: string;
  scope_refs: any[]; summary: string; reason: string; source: string;
}
export interface UnifiedChangePlanDraft {
  plan_draft_id: string; project_name: string; change_session_id: string;
  status: string;                       // 恒 analysis_only
  direct_changes: ChangePlanDraftItem[];
  downstream_changes: ChangePlanDraftItem[];
  unchanged_scope: any[];
  dependency_edges: any[];
  user_decisions_needed: string[];
  consulted_agents: string[];
  source_snapshot_signature: string | null;
  execution_ready: boolean;              // §37：恒 false
}
export interface ModuleConsultation {
  consultation_id: string; change_session_id: string;
  agent_id: string; module: string; question: string;
  status: string;                       // pending | running | completed | failed
  agent_task_id: string | null; result_summary: string | null;
  direct_implications: string[]; downstream_facts: string[];
  user_decisions_needed: string[];
  attempt_count: number; last_error: string | null;
  created_at: number; updated_at: number;
}
export interface ChangeRequestDraft {
  request_id: string; project_name: string; source: string;
  source_agent_id: string | null; source_session_id: string | null;
  original_user_request: string; primary_module: string | null;
  suspected_modules: string[]; entity_refs: any[];
  snapshot_signature: string | null; created_at: number;
}
export interface ChangeAnalysisSession {
  session_id: string; project_name: string;
  request: ChangeRequestDraft;
  status: string;                       // analyzing|consulting|synthesizing|ready|blocked|failed|cancelled|stale
  project_signature_at_start: string | null;
  consultations: ModuleConsultation[];
  plan_draft_id: string | null; plan_draft: UnifiedChangePlanDraft | null;
  error: string | null; created_at: number; updated_at: number;
}

// ============ AI-7B Change Session（proposal-only，§10/§11/§36/§65）============
export interface ChangeRequest {
  request_id: string; project_name: string; source: string;
  source_agent_id: string | null; source_session_id: string | null; source_message_id: string | null;
  original_user_request: string; primary_module: string | null;
  suspected_modules: string[]; entity_refs: any[];
  source_snapshot_signature: string; mode: string; created_at: number;
}
export interface ChangeSession {
  session_id: string; project_name: string; request_id: string;
  status: string;                       // routing|consulting|analyzing_dependencies|...|ready_for_review|approved_for_execution|...
  source_snapshot_signature: string; consultation_round: number;
  module_proposal_ids: string[]; impact_fact_ids: string[]; conflict_ids: string[];
  active_plan_id: string | null; plan_version: number;
  pending_decision_ids: string[]; error: string | null;
  schema_version: number; legacy_analysis_only: boolean;
  created_at: number; updated_at: number;
}
export interface ChangePlanItem {
  plan_item_id: string; module: string; owner_agent_id: string;
  proposal_item_ids: string[]; scope_refs: any[];
  summary: string; reason: string; source: string; required: boolean;
}
export interface ChangePlanStep {
  step_id: string; module: string; owner_agent_id: string;
  plan_item_ids: string[]; depends_on_step_ids: string[]; parallel_group: string | null;
}
export interface PlanApproval {
  plan_id: string; plan_version: number; decision: string;
  source_snapshot_signature: string; decided_at: number;
  user_feedback_message_id: string | null;
}
export interface UnifiedChangePlan {
  plan_id: string; change_session_id: string; project_name: string;
  version: number; source_snapshot_signature: string; status: string;
  direct_change_items: ChangePlanItem[]; downstream_change_items: ChangePlanItem[];
  unchanged_scope: any[]; steps: ChangePlanStep[]; dependency_edges: any[];
  user_decision_ids: string[]; module_proposal_ids: string[];
  validation_issues: any[]; preview_id: string | null; approval: PlanApproval | null;
  parent_plan_id: string | null; user_feedback_message_id: string | null; created_at: number;
}
export interface ModuleOperationBundle {
  step_id: string; module: string; owner_agent_id: string;
  proposal_item_ids: string[]; operations: any[];
}
export interface PlanPreviewArtifact {
  preview_id: string; change_session_id: string; plan_id: string; plan_version: number;
  source_snapshot_signature: string; module_operations: ModuleOperationBundle[];
  aggregate_diff: any; aggregate_impact: any; warnings: any[];
  projected_signature: string; status: string; applyable: boolean;   // §65：恒 false
  created_at: number;
}
export interface ModuleProposal {
  proposal_id: string; change_session_id: string; module: string;
  owner_agent_id: string; round: number; status: string;
  summary: string; items: any[]; dependency_notes: any[];
  blockers: string[]; user_decisions_needed: string[]; evidence_refs: any[];
  source_snapshot_signature: string; agent_task_id: string | null;
  created_at: number; updated_at: number;
}
export interface ChangeDecisionOption { option_id: string; label: string; description: string; }
export interface ChangeDecisionRequest {
  decision_id: string; change_session_id: string; question: string;
  options: ChangeDecisionOption[]; blocking: boolean; status: string;
  selected_option_id: string | null; created_at: number; updated_at: number;
}

// ============ AI-7C Change Execution（§10/§11/§40）============
export interface ExecutionStepRecord {
  step_id: string; module: string; owner_agent_id: string;
  operation_count: number; operation_digest: string; status: string;
  workspace_revision_before: number | null; workspace_revision_after: number | null;
  changeset_id: string | null; started_at: number | null; completed_at: number | null;
  error: Record<string, any> | null;
}
export interface CanonicalCommitRecord {
  status: string; before_signature: string; expected_after_signature: string;
  operation_digest: string; commit_intent_at: number | null; committed_at: number | null;
}
export interface ShotProductionActions {
  first_frame: string; audio: string; last_frame: string; video: string;
}
export interface ProductionExecutionManifest {
  execution_id: string; project_name: string; committed_source_signature: string;
  affected_character_ids: string[]; affected_scene_ids: string[]; affected_shot_ids: string[];
  character_actions: Record<string, string>; shot_actions: Record<string, ShotProductionActions>;
  planner_scene_ids: string[]; baseline_asset_refs: Record<string, any>;
  expected_video_requests: number; production_plan_ids: Record<string, string>; created_at: number;
}
export interface ChangeExecution {
  schema_version: number; execution_id: string; project_name: string;
  change_session_id: string; plan_id: string; plan_version: number; approval_id: string;
  source_signature: string; preview_artifact_id: string; execution_mode: string;
  production_mode: string; status: string; stage: string;
  execution_workspace_id: string | null; operation_digest: string;
  steps: ExecutionStepRecord[]; canonical_commit: CanonicalCommitRecord | null;
  character_sync: Record<string, any>[]; planning: Record<string, any>[];
  production_manifest_file: string | null; production_run_id: string | null;
  blocker: Record<string, any> | null; error: Record<string, any> | null;
  cancel_requested: boolean; production_skipped_by_user: boolean;
  created_at: number; updated_at: number;
}

class ApiClient {
  private baseUrl: string;

  constructor(baseUrl: string = API_BASE_URL) {
    this.baseUrl = baseUrl;
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${this.baseUrl}${endpoint}`;

    const response = await fetch(url, {
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
      ...options,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    const data = await response.json();
    return data;
  }

  // 项目相关
  async listProjects(): Promise<string[]> {
    const data = await this.request<{ projects: string[] }>('/api/projects');
    return data.projects;
  }

  async createProject(name: string, renderFamily: string, artDirection = ''): Promise<void> {
    // P1 §6：render_family 必选（后端契约校验，不静默默认）
    let url = `/api/projects/create?name=${encodeURIComponent(name)}`
      + `&render_family=${encodeURIComponent(renderFamily)}`;
    if (artDirection) url += `&art_direction=${encodeURIComponent(artDirection)}`;
    await this.request(url, { method: 'POST' });
  }

  // ============ Project Visual Profile（P1 §20） ============

  async getVisualProfile(project: string): Promise<{
    specified: boolean;
    visual_profile: {
      render_family: string; art_direction: string; style_tags: string[];
      negative_style_constraints: string[];
    } | null;
    revision: string;
    has_approved_visual_assets: boolean;
    check_error?: string;
  }> {
    return this.request(`/api/project/${encodeURIComponent(project)}/visual-profile`);
  }

  async updateVisualProfile(project: string, body: {
    render_family: string; art_direction?: string;
    style_tags?: string[]; negative_style_constraints?: string[];
    expected_revision: string;
  }): Promise<{ ok: boolean; revision: string; has_approved_visual_assets: boolean }> {
    return this.request(`/api/project/${encodeURIComponent(project)}/visual-profile`, {
      method: 'PUT',
      body: JSON.stringify(body),
    });
  }

  // ============ 角色生成（独立操作） ============

  // 单视角生成 + 抠图
  async generateCharacterView(params: {
    description: string;
    view: string;
    style?: string;
    size_tier?: string;
    ratio?: string;
  }): Promise<{
    success: boolean;
    raw_base64: string;
    seg_base64: string | null;
    prompt: string;
    metadata: any;
  }> {
    return this.request('/api/character/generate-view', {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  // 链式三视图生成
  async generateCharacterChain(params: {
    description: string;
    style?: string;
    size_tier?: string;
    ratio?: string;
  }): Promise<{
    success: boolean;
    raw_views: Record<string, string>;
    seg_views: Record<string, string>;
    metadata: Record<string, any>;
    steps: any[];
    error?: string;
  }> {
    return this.request('/api/character/generate-chain', {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  // 设定图三视图生成
  async generateCharacterSheet(params: {
    description: string;
    style?: string;
    size_tier?: string;
    ratio?: string;
  }): Promise<{
    success: boolean;
    sheet_raw: string;
    crops_raw: Record<string, string>;
    seg_views: Record<string, string>;
    metadata: Record<string, any>;
    steps: any[];
    prompt?: string;
    error?: string;
  }> {
    return this.request('/api/character/generate-sheet', {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  // 单独抠图
  async segmentImage(imageBase64: string): Promise<{
    success: boolean;
    seg_base64: string;
    metadata: any;
  }> {
    return this.request('/api/character/segment', {
      method: 'POST',
      body: JSON.stringify({ image_base64: imageBase64 }),
    });
  }

  // 带参考图重新生成单视角
  async regenerateView(params: {
    description: string;
    view: string;
    style?: string;
    size_tier?: string;
    ratio?: string;
    ref_images?: string[];
  }): Promise<{
    success: boolean;
    raw_base64: string;
    seg_base64: string | null;
    prompt: string;
    metadata: any;
    ref_count: number;
  }> {
    return this.request('/api/character/regenerate-view', {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  // 设置相关
  async updateSettings(settings: Record<string, any>): Promise<void> {
    await this.request('/api/settings', {
      method: 'PUT',
      body: JSON.stringify(settings),
    });
  }

  // ============ 角色保存系统 ============

  // 保存角色
  async saveCharacter(params: {
    name: string;
    description: string;
    views: {
      front?: { raw?: string; seg?: string };
      side?: { raw?: string; seg?: string };
      back?: { raw?: string; seg?: string };
    };
  }): Promise<{ success: boolean; character_id: string; message: string }> {
    return this.request('/api/character/save', {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  // 获取已保存角色列表
  async listSavedCharacters(): Promise<{
    characters: Array<{
      id: string;
      name: string;
      description: string;
      created_at: string;
      updated_at: string;
      has_views: {
        front: { raw: boolean; seg: boolean };
        side: { raw: boolean; seg: boolean };
        back: { raw: boolean; seg: boolean };
      };
    }>;
  }> {
    return this.request('/api/character/list');
  }

  // 加载已保存角色
  async loadCharacter(characterId: string): Promise<{
    success: boolean;
    character: {
      id: string;
      name: string;
      description: string;
      created_at: string;
      views: {
        front?: { raw?: string; seg?: string };
        side?: { raw?: string; seg?: string };
        back?: { raw?: string; seg?: string };
      };
    };
  }> {
    return this.request(`/api/character/load/${characterId}`);
  }

  // 删除角色
  async deleteCharacter(characterId: string): Promise<{ success: boolean; message: string }> {
    return this.request(`/api/character/${characterId}`, {
      method: 'DELETE',
    });
  }

  // 更新角色信息
  async updateCharacter(characterId: string, params: {
    name?: string;
    description?: string;
  }): Promise<{ success: boolean; message: string }> {
    return this.request(`/api/character/${characterId}`, {
      method: 'PUT',
      body: JSON.stringify(params),
    });
  }

  // 更新角色三视图
  async updateCharacterViews(characterId: string, views: {
    front?: { raw?: string; seg?: string };
    side?: { raw?: string; seg?: string };
    back?: { raw?: string; seg?: string };
  }): Promise<{ success: boolean; message: string }> {
    return this.request(`/api/character/${characterId}/views`, {
      method: 'PUT',
      body: JSON.stringify(views),
    });
  }

  // ============ 项目角色绑定（角色页结果落盘到项目） ============

  // 绑定角色到项目（落盘三视图 + 写 parsed_script.json 的 reference_images）
  async bindCharacterToProject(projectName: string, params: {
    name: string;
    description?: string;
    views: {
      front?: { raw?: string; seg?: string };
      side?: { raw?: string; seg?: string };
      back?: { raw?: string; seg?: string };
    };
  }): Promise<{ success: boolean; reference_images: Record<string, string>; message: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/character/bind`, {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  // 更新项目角色（名称/描述，写回 parsed_script.json）
  async updateProjectCharacter(projectName: string, characterName: string, params: {
    description?: string;
    new_name?: string;
  }): Promise<{ success: boolean; renamed: boolean; message: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/character/${encodeURIComponent(characterName)}`, {
      method: 'PUT',
      body: JSON.stringify(params),
    });
  }

  // 获取项目角色（含 reference_images 路径）
  async getProjectCharactersFull(projectName: string): Promise<{ characters: any[] }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/characters/full`);
  }

  // 从当前项目删除角色（v2 project.json + 清理三视图文件；不影响角色库）
  async deleteProjectCharacter(projectName: string, characterName: string): Promise<{ success: boolean; message: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/character/${encodeURIComponent(characterName)}`, {
      method: 'DELETE',
    });
  }

  // 保存剧本（YAML + 解析 -> parsed_script.json，保留已有生成结果）
  async saveProjectScript(projectName: string, scriptContent: string, enhance: boolean = false): Promise<{ success: boolean; characters_count: number; scenes_count: number; message: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/script`, {
      method: 'POST',
      body: JSON.stringify({ script_content: scriptContent, enhance }),
    });
  }

  // 保存分镜列表（batch 0：按 scene_id 对账--更新 summary/characters/新增/删除/重排，镜头产物保留）
  // scene_id 为 null 表示新增（后端按 next_scene_seq 分配）；story_text=null 表示不改动完整故事
  async saveScenes(projectName: string, payload: {
    story_text?: string | null;
    scenes: Array<{ scene_id: string | null; summary: string; characters: string[] }>;
  }): Promise<{ ok: boolean; scenes: Array<{ scene_id: string; summary: string; characters: string[] }> }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scenes`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  // ============ AI 整理故事（Phase AI-1）============

  // AI 整理故事段 + 人物识别 + 确定性匹配，落草稿。可重复调用（重新整理）
  async organizeStory(projectName: string): Promise<StoryOrganizationDraft> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/organize`, {
      method: 'POST',
    });
  }

  // 读现有草稿（刷新恢复）。无草稿 404
  async getStoryDraft(projectName: string): Promise<StoryOrganizationDraft> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/draft`);
  }

  // 保存草稿编辑（场景文本/排序/删除/解决状态）。不动 project.json
  async saveStoryDraft(projectName: string, payload: {
    overview: string;
    scenes: DraftScene[];
    characters: CharacterResolutionItem[];
  }): Promise<StoryOrganizationDraft> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/draft`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  // AI 生成单个角色文字设定（只返回文本，不建角色）
  async draftStoryCharacter(projectName: string, mention: string): Promise<{ description: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/draft-character`, {
      method: 'POST',
      body: JSON.stringify({ mention }),
    });
  }

  // AI 批量生成缺失角色文字设定（mentions 缺省=所有 unresolved）
  async draftStoryCharactersBatch(projectName: string, mentions?: string[]): Promise<{ characters: Array<{ name: string; description: string }> }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/draft-characters-batch`, {
      method: 'POST',
      body: JSON.stringify(mentions ? { mentions } : {}),
    });
  }

  // 解决一个被提及人物：match / create / ignore
  async resolveStoryCharacter(projectName: string, params: {
    mention: string;
    action: 'match' | 'create' | 'ignore';
    matched_character_name?: string;
    name?: string;                 // create 时的角色名（默认=mention）
    description?: string;
  }): Promise<StoryOrganizationDraft> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/resolve-character`, {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  // 采用故事整理 -> 写入 Scene[]（stale + 已有 Scene 闸门 + 未解决拦截 + 角色去重）
  async applyStoryOrganize(projectName: string): Promise<ApplyStoryResult> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/apply`, {
      method: 'POST',
    });
  }

  // ============ AI 续写故事（Phase AI-1B，append-only）============

  // 生成续写正文（json_mode=False 纯文本），落续写草稿。可重复调用（重新续写）
  async continueStory(projectName: string, params: {
    direction?: string;
    scale?: ContinuationScale;
  }): Promise<StoryContinuationDraft> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/continue`, {
      method: 'POST', body: JSON.stringify(params),
    });
  }

  // 读续写草稿（刷新恢复）。无草稿 404
  async getContinuationDraft(projectName: string): Promise<StoryContinuationDraft> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/continue/draft`);
  }

  // 保存续写草稿编辑（continuation_text/新段/解决状态/stage）
  async saveContinuationDraft(projectName: string, payload: {
    direction: string;
    scale: ContinuationScale;
    continuation_text: string;
    new_scene_drafts: DraftScene[];
    character_resolution: CharacterResolutionItem[];
    stage: string;
  }): Promise<StoryContinuationDraft> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/continue/draft`, {
      method: 'PUT', body: JSON.stringify(payload),
    });
  }

  // 整理新增正文 -> 新故事段 + 人物识别 + 匹配（只拆 continuation_text）
  async organizeContinuation(projectName: string): Promise<StoryContinuationDraft> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/continue/organize`, {
      method: 'POST',
    });
  }

  // 解决一个被提及人物（match / create / ignore），复用 Phase AI-1 语义
  async resolveContinuationCharacter(projectName: string, params: {
    mention: string;
    action: 'match' | 'create' | 'ignore';
    matched_character_name?: string;
    name?: string;
    description?: string;
  }): Promise<StoryContinuationDraft> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/continue/resolve-character`, {
      method: 'POST', body: JSON.stringify(params),
    });
  }

  // AI 生成单个角色文字设定
  async draftContinuationCharacter(projectName: string, mention: string): Promise<{ description: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/continue/draft-character`, {
      method: 'POST', body: JSON.stringify({ mention }),
    });
  }

  // AI 批量生成缺失角色文字设定（mentions 缺省=所有 unresolved）
  async draftContinuationCharactersBatch(projectName: string, mentions?: string[]): Promise<{ characters: Array<{ name: string; description: string }> }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/continue/draft-characters-batch`, {
      method: 'POST', body: JSON.stringify(mentions ? { mentions } : {}),
    });
  }

  // 采用续写 -> append-only 写入（追加 story_text + 新 Scene，不动已有）
  async applyContinuation(projectName: string): Promise<ApplyContinuationResult> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/continue/apply`, {
      method: 'POST',
    });
  }

  // 仅追加到完整故事（不创建 Scene）
  async appendStoryOnly(projectName: string): Promise<{ ok: boolean; appended_story_chars: number }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/story/continue/append-story-only`, {
      method: 'POST',
    });
  }

  // ============ AI 角色设计（Phase AI-2）============

  // AI 设计一个角色，落草稿。可重复调用（重新设计）
  async designCharacter(projectName: string, characterName: string): Promise<CharacterDesignDraftState> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/character/${encodeURIComponent(characterName)}/design`, {
      method: 'POST',
    });
  }

  // 保存草稿编辑（用户改 generation_prompt 等）
  async saveCharacterDesign(projectName: string, characterName: string, draft: CharacterDesignDraft): Promise<CharacterDesignDraftState> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/character/${encodeURIComponent(characterName)}/design`, {
      method: 'PUT', body: JSON.stringify({ draft }),
    });
  }

  // 应用 generation_prompt -> Character.description_en（stale 检查）
  async applyCharacterDesign(projectName: string, characterName: string): Promise<{ ok: boolean; description_en: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/character/${encodeURIComponent(characterName)}/design/apply`, {
      method: 'POST',
    });
  }

  // AI 批量设计无草稿角色（逐角色，部分失败不整批失败）
  async designAllCharacters(projectName: string, names?: string[]): Promise<{ results: Array<{ name: string; ok: boolean; error?: string }> }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/character/design-batch`, {
      method: 'POST', body: JSON.stringify(names ? { names } : {}),
    });
  }

  // 读所有角色设计草稿（刷新恢复）
  async getCharacterDesigns(projectName: string): Promise<{ designs: Record<string, CharacterDesignDraftState> }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/character/designs`);
  }

  // ============ AI 导演（Phase AI-3）============

  // AI 规划一个故事段的镜头，落草稿。可重复调用（重新规划）
  async planDirector(projectName: string, sceneId: string, directorNote: string): Promise<DirectorDraft> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scene/${encodeURIComponent(sceneId)}/director/plan`, {
      method: 'POST', body: JSON.stringify({ director_note: directorNote }),
    });
  }

  // 读导演草稿（刷新恢复）。无草稿 404
  async getDirectorDraft(projectName: string, sceneId: string): Promise<DirectorDraft> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scene/${encodeURIComponent(sceneId)}/director/draft`);
  }

  // 保存草稿编辑（intent/characters/时长/排序/删除/添加）
  async saveDirectorDraft(projectName: string, sceneId: string, payload: {
    overview: string; shots: DirectorShotDraft[]; director_note: string;
  }): Promise<DirectorDraft> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scene/${encodeURIComponent(sceneId)}/director/draft`, {
      method: 'PUT', body: JSON.stringify(payload),
    });
  }

  // 删草稿
  async deleteDirectorDraft(projectName: string, sceneId: string): Promise<{ ok: boolean }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scene/${encodeURIComponent(sceneId)}/director/draft`, {
      method: 'DELETE',
    });
  }

  // 采用镜头方案 -> 创建 Shot[]（stale + 已有 Shot 闸门 + 角色覆盖 + 一次 save）
  async applyDirector(projectName: string, sceneId: string): Promise<DirectorApplyResult> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scene/${encodeURIComponent(sceneId)}/director/apply`, {
      method: 'POST',
    });
  }

  // ============ AI 制作规划（Phase AI-4A）============

  // AI 为一个故事段的 Shot[] 制定制作规划，落计划（生成 run_seed）
  async planProductionPlan(projectName: string, sceneId: string): Promise<SceneProductionPlan> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scene/${encodeURIComponent(sceneId)}/production-plan/plan`, {
      method: 'POST',
    });
  }

  // 读当前制作计划（含 is_stale 派生标志）。无计划 404
  async getProductionPlan(projectName: string, sceneId: string): Promise<SceneProductionPlan> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scene/${encodeURIComponent(sceneId)}/production-plan`);
  }

  // 保存用户编辑（opening_source/link_mode），后端重算派生字段。source_shot_id 不可用户选
  async saveProductionPlan(projectName: string, sceneId: string, payload: {
    overview: string;
    shots: Array<{ shot_id: string; opening_source: string; link_mode: string; reason: string }>;
  }): Promise<SceneProductionPlan> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scene/${encodeURIComponent(sceneId)}/production-plan`, {
      method: 'PUT', body: JSON.stringify(payload),
    });
  }

  // 删计划
  async deleteProductionPlan(projectName: string, sceneId: string): Promise<{ ok: boolean }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scene/${encodeURIComponent(sceneId)}/production-plan`, {
      method: 'DELETE',
    });
  }

  // 确认制作计划（status=approved）。🔴 不写正式 Shot.opening_source（AI-4B Executor 生效）
  async approveProductionPlan(projectName: string, sceneId: string): Promise<SceneProductionPlan> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scene/${encodeURIComponent(sceneId)}/production-plan/approve`, {
      method: 'POST',
    });
  }

  // ============ AI 自动制作（Phase AI-4B）============

  // 开始自动制作本故事段（后端自己读 plan / 生成 run_seed / 冻结 plan_snapshot）
  async startAutopilot(projectName: string, sceneId: string, respectLocked: boolean = true): Promise<SceneAutopilotRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scene/${encodeURIComponent(sceneId)}/autopilot/start`, {
      method: 'POST', body: JSON.stringify({ respect_locked: respectLocked }),
    });
  }

  // 列出本故事段历史 Run
  async listAutopilotRuns(projectName: string, sceneId: string): Promise<SceneAutopilotRun[]> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scene/${encodeURIComponent(sceneId)}/autopilot/runs`);
  }

  // 读单个 Run（Run Monitor 轮询用）
  async getAutopilotRun(projectName: string, runId: string): Promise<SceneAutopilotRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/autopilot/run/${encodeURIComponent(runId)}`);
  }

  // 暂停（当前 job 完成后停，§二十九）
  async pauseAutopilot(projectName: string, runId: string): Promise<SceneAutopilotRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/autopilot/run/${encodeURIComponent(runId)}/pause`, {
      method: 'POST',
    });
  }

  // 继续（从 checkpoint，§三十）
  async resumeAutopilot(projectName: string, runId: string): Promise<SceneAutopilotRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/autopilot/run/${encodeURIComponent(runId)}/resume`, {
      method: 'POST',
    });
  }

  // 停止（保留已生成资产，§三十一）
  async cancelAutopilot(projectName: string, runId: string): Promise<SceneAutopilotRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/autopilot/run/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST',
    });
  }

  // ============ 完整影片自动制作（Phase AI-5A）============

  // 项目就绪检查（§十七）
  async getProductionPreflight(projectName: string, respectLocked: boolean = true): Promise<ProjectProductionPreflight> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/production/preflight?respect_locked=${respectLocked}`);
  }

  // 开始完整制作（后端自己 load + preflight + 冻结 snapshot，§十九）
  async startProduction(projectName: string, respectLocked: boolean = true): Promise<ProjectProductionRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/production/start`, {
      method: 'POST', body: JSON.stringify({ respect_locked: respectLocked }),
    });
  }

  // 列出历史 ProjectRun（§五十七）
  async listProductionRuns(projectName: string): Promise<ProjectProductionRun[]> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/production/runs`);
  }

  // 读单个 ProjectRun（Monitor 轮询用）
  async getProductionRun(projectName: string, runId: string): Promise<ProjectProductionRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/production/run/${encodeURIComponent(runId)}`);
  }

  // 暂停完整制作（传给当前 child，§二十六）
  async pauseProduction(projectName: string, runId: string): Promise<ProjectProductionRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/production/run/${encodeURIComponent(runId)}/pause`, {
      method: 'POST',
    });
  }

  // 继续完整制作（同 source/plans snapshot，§二十七）
  async resumeProduction(projectName: string, runId: string): Promise<ProjectProductionRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/production/run/${encodeURIComponent(runId)}/resume`, {
      method: 'POST',
    });
  }

  // 停止完整制作（保留已生成资产，§二十八）
  async cancelProduction(projectName: string, runId: string): Promise<ProjectProductionRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/production/run/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST',
    });
  }

  // ============ AI 完整制作（Phase AI-5B）============

  // 纯代码 Inspect（§二十七，不调 AI，不创建 Run）
  async getFullProductionInspect(projectName: string): Promise<FullProjectInspect> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/full-production/inspect`);
  }

  // 开始 AI 完整制作（后端 load + inspect + 冻结 snapshot + 启动，§三十）
  async startFullProduction(projectName: string, respectLocked: boolean = true): Promise<FullProjectRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/full-production/start`, {
      method: 'POST', body: JSON.stringify({ respect_locked: respectLocked }),
    });
  }

  // 列出历史 FullProjectRun
  async listFullProductionRuns(projectName: string): Promise<FullProjectRun[]> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/full-production/runs`);
  }

  // 读单个 FullProjectRun（Monitor 轮询用）
  async getFullProductionRun(projectName: string, runId: string): Promise<FullProjectRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/full-production/run/${encodeURIComponent(runId)}`);
  }

  // 暂停（prepare 阶段下一 checkpoint 暂停；production 阶段传给 child，§三十六/§四十五）
  async pauseFullProduction(projectName: string, runId: string): Promise<FullProjectRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/full-production/run/${encodeURIComponent(runId)}/pause`, {
      method: 'POST',
    });
  }

  // 继续（不重处理已 ready Scene / 已 apply Director / 已 approve Plan，§三十四）
  async resumeFullProduction(projectName: string, runId: string): Promise<FullProjectRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/full-production/run/${encodeURIComponent(runId)}/resume`, {
      method: 'POST',
    });
  }

  // 停止（已创建 Shot/Plan/视频保留，不回滚，§三十七/§四十六）
  async cancelFullProduction(projectName: string, runId: string): Promise<FullProjectRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/full-production/run/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST',
    });
  }

  // ============ 阶段 3：shot 级管线 ============

  // 加载 v2 项目结构（scenes/shots 含首帧/尾帧/音频/视频产物）
  async getProjectShots(projectName: string): Promise<ProjectShots> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shots`);
  }

  // 时长预览（每镜 audio/planned/needs_split + 全片总时长）
  async getTiming(projectName: string): Promise<{ shots: TimingShot[]; total_seconds: number; fps: number }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/timing`);
  }

  // 首帧候选生成（入队 first_frame 任务）
  async generateFirstFrame(projectName: string, shotId: string, params: { variants?: number; force?: boolean } = {}): Promise<{ job_id: string; shot_id: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/first-frame`, {
      method: 'POST', body: JSON.stringify({ variants: params.variants ?? 2, force: params.force ?? false }),
    });
  }

  // 选择首帧候选（按 candidate_id 跨批次选择，设 selected_first_frame 指针）
  async chooseFirstFrame(projectName: string, shotId: string, candidateId: string): Promise<{ shot_id: string; candidate_id: string; url: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/first-frame/choose`, {
      method: 'POST', body: JSON.stringify({ candidate_id: candidateId }),
    });
  }
  // 选择尾帧候选（跨批次切回，设 selected_last_frame + 同步旧 last_frame）
  async chooseLastFrame(projectName: string, shotId: string, candidateId: string): Promise<{ shot_id: string; candidate_id: string; url: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/last-frame/choose`, {
      method: 'POST', body: JSON.stringify({ candidate_id: candidateId }),
    });
  }

  // 设最终视频版本（final_video 指针，多版本选最终）
  async setFinalVideo(projectName: string, shotId: string, versionId: string): Promise<{ shot_id: string; final_video: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/video/set-final`, {
      method: 'POST', body: JSON.stringify({ version_id: versionId }),
    });
  }

  // Phase 6A：镜头接力 / 开场来源
  async setOpeningSource(projectName: string, shotId: string, source: string): Promise<{ opening_source: string; source_shot_id: string | null; source_version: string | null; status: string; message: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/opening-source`, {
      method: 'POST', body: JSON.stringify({ source }),
    });
  }
  // 更新为最新末帧（同步 source_version，清除 stale）
  async syncSource(projectName: string, shotId: string): Promise<{ status: string; message: string; source_version: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/sync-source`, { method: 'POST' });
  }
  // 重新提取/托管本镜视频末帧（retry）
  async refreshVideoEnd(projectName: string, shotId: string): Promise<{ status: string; url: string | null; error: string | null }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/video-end/refresh`, { method: 'POST' });
  }

  // 重新托管首帧（local有/url空 -> Agnes i2i 'keep as is' 洗公网URL，不重新生成、不丢画面）
  async rehostFirstFrame(projectName: string, shotId: string): Promise<{ shot_id: string; url: string; rehosted: boolean }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/first-frame/rehost`, { method: 'POST' });
  }

  // 尾帧生成（仅 keyframes）
  async generateLastFrame(projectName: string, shotId: string): Promise<{ job_id: string; shot_id: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/last-frame`, { method: 'POST' });
  }

  // 镜头视频生成（入队 video 任务，串行 1 RPM）
  async generateShotVideo(projectName: string, shotId: string): Promise<{ job_id: string; shot_id: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/video`, { method: 'POST' });
  }

  // 更新镜头字段（工作台编辑：link_mode/locked + 创作字段 + 结构化字段，None 字段不改动）
  async updateShot(projectName: string, shotId: string, params: {
    link_mode?: string; locked?: boolean;
    description?: string; prompt_en?: string; negative_prompt?: string;
    shot_size?: string; camera?: string; lighting?: string; motion?: string;
    dialogue?: string; characters?: string[];
    // 结构化字段（提示词.md）
    opening_state?: string; camera_angle?: string; composition?: string; important_details?: string;
    required_elements?: string[]; last_frame_description?: string; end_state?: string; frame_change?: string;
    preserve_elements?: string[]; add_elements?: string; remove_elements?: string; end_composition?: string;
    preserve_custom?: string; camera_movement?: string; motion_pace?: string; environment_motion?: string;
    action_sequence?: string; camera_movement_detail?: string; transition_description?: string;
    video_preserve_elements?: string[]; video_preserve_custom?: string; target_duration_s?: number | null;
    last_frame_prompt_en?: string; video_prompt_en?: string;
    first_frame_prompt_override?: string; last_frame_prompt_override?: string; video_prompt_override?: string;
    first_frame_prompt_sig?: string; last_frame_prompt_sig?: string; video_prompt_sig?: string;
    opening_source?: string; source_shot_id?: string; source_version?: string;
    seed?: number | null;
  }): Promise<{ shot_id: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}`, {
      method: 'PUT', body: JSON.stringify(params),
    });
  }

  // 在故事段下添加镜头（默认继承故事段角色，立即持久化）
  async addShot(projectName: string, sceneId: string, characters?: string[] | null): Promise<{ shot_id: string; scene_id: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scene/${encodeURIComponent(sceneId)}/shot`, {
      method: 'POST', body: JSON.stringify({ characters: characters ?? null }),
    });
  }

  // 删除镜头 + 清理其产物文件
  async deleteShot(projectName: string, shotId: string): Promise<{ shot_id: string; deleted: boolean }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}`, {
      method: 'DELETE',
    });
  }

  // 中文画面描述 -> 英文提示词（无副作用）
  async translateText(text: string): Promise<{ english: string }> {
    return this.request('/api/translate', {
      method: 'POST', body: JSON.stringify({ text }),
    });
  }

  // 根据上下文生成英文提示词（读项目/镜头上下文，调文本模型，只返回文本不生成资产）
  async generatePromptContext(projectName: string, shotId: string, stage: 'first' | 'last' | 'video'): Promise<{ prompt: string; inferred_shot_intent?: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/prompt/generate`, {
      method: 'POST', body: JSON.stringify({ stage }),
    });
  }

  // 优化当前英文提示词（以当前文本为核心，调文本模型，只返回文本不生成资产）
  async optimizePrompt(projectName: string, shotId: string, stage: 'first' | 'last' | 'video', text: string): Promise<{ prompt: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/prompt/optimize`, {
      method: 'POST', body: JSON.stringify({ stage, text }),
    });
  }

  // 单镜配音（TTS -> 反推 num_frames）
  async generateShotAudio(projectName: string, shotId: string, params: { voice?: string; rate?: string; volume?: string; regenerate?: boolean } = {}): Promise<{ task_id: string; shot_id: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/audio`, {
      method: 'POST', body: JSON.stringify(params),
    });
  }

  // 拆分超长台词（硬编码 keyframes + split_next 桥接）
  async splitDialogue(projectName: string, shotId: string, params: { mode?: string; segments?: string[] } = {}): Promise<{ split_into: number; shot_ids: string[]; link_mode?: string; enqueued_last_frame?: string | null }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/split-dialogue`, {
      method: 'POST', body: JSON.stringify(params),
    });
  }

  // 编排（dry_run 预估 / 执行入队）
  async orchestrate(projectName: string, params: {
    stages?: string[]; scope?: string | string[]; skip_locked?: boolean; dry_run?: boolean;
    voice?: string; rate?: string; volume?: string; width?: number; height?: number; fps?: number; burn_subtitles?: boolean;
  }): Promise<OrchestrateResult> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/orchestrate`, {
      method: 'POST', body: JSON.stringify(params),
    });
  }

  // shot 级合成（concat->整音轨->烧SRT，Ken Burns 兜底）
  async composeShots(projectName: string, params: { width?: number; height?: number; fps?: number; burn_subtitles?: boolean } = {}): Promise<{ task_id: string; message: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/compose-shots`, {
      method: 'POST', body: JSON.stringify(params),
    });
  }

  // 队列管理
  async listJobs(projectName: string, state?: string): Promise<{ jobs: Job[]; queue: QueueStats }> {
    const q = state ? `?state=${encodeURIComponent(state)}` : '';
    return this.request(`/api/project/${encodeURIComponent(projectName)}/jobs${q}`);
  }
  async cancelJob(jobId: string, force = false): Promise<{ job_id: string; state: string }> {
    return this.request(`/api/jobs/${encodeURIComponent(jobId)}/cancel${force ? '?force=true' : ''}`, { method: 'POST' });
  }
  async retryJob(jobId: string): Promise<{ job_id: string; state: string }> {
    return this.request(`/api/jobs/${encodeURIComponent(jobId)}/retry`, { method: 'POST' });
  }
  async retryFailedJobs(projectName: string): Promise<{ retried: string[]; count: number }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/jobs/retry-failed`, { method: 'POST' });
  }
  async clearFinishedJobs(projectName: string): Promise<{ cleared: number }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/jobs/finished`, { method: 'DELETE' });
  }

  // ============ AI-6A Creative Workspace / ChangeSet（§二十~§二十二）============
  async listWorkspaces(projectName: string): Promise<WorkspaceMeta[]> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workspaces`);
  }
  async createWorkspace(projectName: string, name: string): Promise<WorkspaceMeta> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workspaces`, {
      method: 'POST', body: JSON.stringify({ name }),
    });
  }
  async getWorkspace(projectName: string, wid: string): Promise<WorkspaceMeta> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workspace/${encodeURIComponent(wid)}`);
  }
  async getWorkspaceProject(projectName: string, wid: string): Promise<ProjectShots> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workspace/${encodeURIComponent(wid)}/project`);
  }
  async archiveWorkspace(projectName: string, wid: string): Promise<WorkspaceMeta> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workspace/${encodeURIComponent(wid)}/archive`, { method: 'POST' });
  }
  async deleteWorkspace(projectName: string, wid: string): Promise<{ deleted: boolean }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workspace/${encodeURIComponent(wid)}`, { method: 'DELETE' });
  }
  async getWorkspaceDiff(projectName: string, wid: string): Promise<{ diff: any[] }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workspace/${encodeURIComponent(wid)}/diff`);
  }
  async getWorkspaceImpact(projectName: string, wid: string): Promise<{ diff: any[]; summary: Record<string, any> }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workspace/${encodeURIComponent(wid)}/impact`);
  }
  async previewChangeset(projectName: string, wid: string, payload: {
    title?: string; intent?: string; base_workspace_revision: number; operations: ChangeOp[];
  }): Promise<PreviewResult> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workspace/${encodeURIComponent(wid)}/changeset/preview`, {
      method: 'POST', body: JSON.stringify(payload),
    });
  }
  async applyChangeset(projectName: string, wid: string, cid: string): Promise<{ applied: boolean; revision: number; changeset_id: string; idempotent?: boolean }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workspace/${encodeURIComponent(wid)}/changeset/${encodeURIComponent(cid)}/apply`, { method: 'POST' });
  }
  async rejectChangeset(projectName: string, wid: string, cid: string): Promise<{ rejected: boolean }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workspace/${encodeURIComponent(wid)}/changeset/${encodeURIComponent(cid)}/reject`, { method: 'POST' });
  }
  async listChangesets(projectName: string, wid: string): Promise<any[]> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workspace/${encodeURIComponent(wid)}/changesets`);
  }

  // ============ AI-6B Multi-Agent Foundation ============
  async listAgents(): Promise<AgentInfo[]> {
    const data = await this.request<{ agents: AgentInfo[] }>('/api/agents');
    return data.agents;
  }
  async listAgentProfiles(): Promise<AgentModelProfile[]> {
    const data = await this.request<{ profiles: AgentModelProfile[] }>('/api/agents/profiles');
    return data.profiles;
  }
  async getAgentCapabilities(agentId: string): Promise<AgentCapability[]> {
    const data = await this.request<{ capabilities: AgentCapability[] }>(`/api/agents/${encodeURIComponent(agentId)}/capabilities`);
    return data.capabilities;
  }
  async getAgentSettings(): Promise<{ agents: Record<string, any> }> {
    return this.request('/api/agents/settings');
  }
  async updateAgentSetting(agentId: string, modelProfileId?: string, enabled?: boolean): Promise<any> {
    return this.request('/api/agents/settings', {
      method: 'PUT',
      body: JSON.stringify({ agent_id: agentId, model_profile_id: modelProfileId, enabled }),
    });
  }
  async listAgentSessions(projectName: string): Promise<AgentSession[]> {
    const data = await this.request<{ sessions: AgentSession[] }>(`/api/project/${encodeURIComponent(projectName)}/agents/sessions`);
    return data.sessions;
  }
  async createAgentSession(projectName: string, agentId: string, scopeType: string, scopeId: string): Promise<AgentSession> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/sessions`, {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, scope_type: scopeType, scope_id: scopeId }),
    });
  }
  async getAgentSession(projectName: string, sid: string): Promise<AgentSession> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/sessions/${encodeURIComponent(sid)}`);
  }
  async sendAgentMessage(projectName: string, sid: string, content: string): Promise<AgentMessage> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/sessions/${encodeURIComponent(sid)}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    });
  }
  /** AI-7D D1 · 统一 Direct Chat turn。返回 AgentTurnResult（intent + assistant_message +
   * events + evidence_refs）。assistant_message 是用户唯一所见；events 是真后台事件。
   * Phase 46（M02）：thinking=true 时后端以 enable_thinking 出站（工作流助手 Producer 解析用）。 */
  async agentTurn(projectName: string, sid: string, content: string, thinking = false): Promise<AgentTurnResult> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/sessions/${encodeURIComponent(sid)}/turn`, {
      method: 'POST',
      body: JSON.stringify({ content, thinking }),
    });
  }
  /** AI-7D-R：协作式停止进行中的流式 turn（步骤边界安全停下）。 */
  async cancelAgentTurn(projectName: string, sid: string): Promise<{ stopped: boolean; message?: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/sessions/${encodeURIComponent(sid)}/turn/cancel`, {
      method: 'POST',
    });
  }
  /** AI-7D-R：查询后端此刻真实持有的资源锁（SSE unlock 事件丢失/半开连接时对账兜底）。 */
  async getResourceLocks(projectName: string): Promise<{ locks: any[] }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/resource-locks`);
  }

  /** Producer V1 Phase 3 · Production Room 只读投影（稳定 ID / 确定顺序 / 真实状态）。 */
  async getProductionRoom(projectName: string): Promise<RoomSnapshot> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/producer/room`);
  }

  // ============ Phase 46 · 可视化工作流（六员后端接入）============
  async getWorkflowCatalog(projectName: string): Promise<WorkflowCatalog> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workflow/catalog`);
  }
  async getWorkflowDefinition(projectName: string, workflowId: string): Promise<WorkflowDefinitionDoc> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workflow/definitions/${encodeURIComponent(workflowId)}`);
  }
  async saveWorkflowDefinition(projectName: string, workflowId: string, definition: any, expectedRevision: number | null): Promise<WorkflowSaveResult> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workflow/definitions/${encodeURIComponent(workflowId)}`, {
      method: 'PUT', body: JSON.stringify({ definition, expectedRevision }),
    });
  }
  async validateWorkflow(projectName: string, workflowId: string, definition: any): Promise<{ ok: boolean; errors: ValidationIssue2[]; warnings: ValidationIssue2[] }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workflow/definitions/${encodeURIComponent(workflowId)}/validate`, {
      method: 'POST', body: JSON.stringify({ definition }),
    });
  }
  async compileWorkflow(projectName: string, workflowId: string, definition: any): Promise<{ ok: boolean; plan: any | null; blockers: ValidationIssue2[] }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workflow/definitions/${encodeURIComponent(workflowId)}/compile`, {
      method: 'POST', body: JSON.stringify({ definition }),
    });
  }
  async startWorkflowRun(projectName: string, workflowId: string, payload: {
    definition?: any; idempotencyKey: string; expectedRevision?: number | null; saveIfChanged?: boolean;
  }): Promise<{ run: WorkflowRun; reused: boolean }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workflow/definitions/${encodeURIComponent(workflowId)}/runs`, {
      method: 'POST', body: JSON.stringify(payload),
    });
  }
  async listWorkflowRuns(projectName: string): Promise<{ runs: any[] }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workflow/runs`);
  }
  // Phase 47 C批：模板（从模板新建 → 走保存/校验同一路径的新工作流身份）
  async listWorkflowTemplates(projectName: string): Promise<{ templates: any[]; templateVersion: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workflow/templates`);
  }
  async createWorkflowFromTemplate(projectName: string, templateId: string, workflowId: string, params?: Record<string, any>): Promise<any> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workflow/templates/${encodeURIComponent(templateId)}/create`, {
      method: 'POST', body: JSON.stringify({ workflowId, params: params ?? {} }),
    });
  }
  async getWorkflowRun(projectName: string, runId: string): Promise<WorkflowRun> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workflow/runs/${encodeURIComponent(runId)}`);
  }
  async getWorkflowRunEvents(projectName: string, runId: string, after = 0): Promise<{ events: any[]; lastSeq: number }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workflow/runs/${encodeURIComponent(runId)}/events?after=${after}`);
  }
  async decideWorkflowReview(projectName: string, runId: string, requestId: string, decision: 'approve' | 'reject', note = '', expectedFingerprint?: string | null,
                             withSelection = false, selectionOverride?: { kind: string; entityId: string; versionId: string } | null): Promise<any> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workflow/runs/${encodeURIComponent(runId)}/reviews/${encodeURIComponent(requestId)}/decision`, {
      method: 'POST', body: JSON.stringify({
        decision, note, expectedFingerprint: expectedFingerprint ?? null,
        withSelection, selectionOverride: selectionOverride ?? null,
      }),
    });
  }
  async continueWorkflowRun(projectName: string, runId: string, nodeId?: string | null): Promise<{ run: WorkflowRun }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workflow/runs/${encodeURIComponent(runId)}/continue`, {
      method: 'POST', body: JSON.stringify({ nodeId: nodeId ?? null }),
    });
  }
  async stopWorkflowRun(projectName: string, runId: string): Promise<{ run: WorkflowRun; note: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/workflow/runs/${encodeURIComponent(runId)}/stop`, { method: 'POST' });
  }

  // ============ AI-7D-R · Script Writer 最小测试弹窗（隔离，单工具 edit_story_text）============
  async scriptWriterTestTurn(projectName: string, content: string): Promise<{
    assistant_message: string; edited: boolean; new_story_text?: string | null;
    old_story_text?: string | null; message: AgentMessage;
  }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/script-writer-test/turn`, {
      method: 'POST', body: JSON.stringify({ content }),
    });
  }
  async scriptWriterTestSession(projectName: string): Promise<AgentSession> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/script-writer-test/session`);
  }

  // ============ AI-7D-R · Director 最小测试弹窗（隔离，增/删/改故事段与镜头）============
  async directorTestTurn(projectName: string, content: string): Promise<{
    assistant_message: string; edited: boolean;
    added_scenes?: any[] | null; added_shots?: any[] | null;
    updated_scenes?: any[] | null; updated_shots?: any[] | null;
    deleted_scenes?: string[] | null; deleted_shots?: string[] | null;
    deleted_preview?: any | null;
    removed_files?: number | null; plan_removed?: boolean | null;
    message: AgentMessage;
  }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/director-test/turn`, {
      method: 'POST', body: JSON.stringify({ content }),
    });
  }
  async directorTestSession(projectName: string): Promise<AgentSession> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/director-test/session`);
  }

  // ============ AI-7A Team Group Chat（polling-first，不新增 realtime bus）============
  async getTeamThread(projectName: string): Promise<TeamThreadData> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/team-chat/thread`);
  }
  async sendTeamMessage(projectName: string, content: string): Promise<{ message: TeamMessage; status: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/team-chat/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    });
  }
  async getTeamMessages(projectName: string): Promise<TeamMessage[]> {
    const data = await this.request<{ messages: TeamMessage[] }>(`/api/project/${encodeURIComponent(projectName)}/team-chat/messages`);
    return data.messages;
  }

  // ============ AI-7A Change Analysis Session（analysis-only，§31/§37/§55）============
  async createChangeAnalysis(projectName: string, request: string, source = 'team_chat'): Promise<ChangeAnalysisSession> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-analysis`, {
      method: 'POST', body: JSON.stringify({ request, source }),
    });
  }
  async listChangeAnalysis(projectName: string): Promise<ChangeAnalysisSession[]> {
    const data = await this.request<{ sessions: ChangeAnalysisSession[] }>(`/api/project/${encodeURIComponent(projectName)}/change-analysis`);
    return data.sessions;
  }
  async getChangeAnalysis(projectName: string, sessionId: string): Promise<ChangeAnalysisSession> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-analysis/${encodeURIComponent(sessionId)}`);
  }
  async resumeChangeAnalysis(projectName: string, sessionId: string): Promise<ChangeAnalysisSession> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-analysis/${encodeURIComponent(sessionId)}/resume`, {
      method: 'POST',
    });
  }

  // ============ AI-7B Change Sessions（proposal-only，§92/§94/§93）============
  /** AI-7C-H §23/§24：Floating Bar / Specialist 直聊的写意图桥接——source 支持
   * floating_bar | direct_agent_chat | team_chat；contextScope 携带页面上下文
   * （sceneId/shotId/characterName），后端持久化为 entity_refs 供分类合并（§9 不反问）。 */
  async createChangeSession(projectName: string, request: string, source = 'team_chat',
      sourceMessageId?: string, options?: {
        sourceAgentId?: string; sourceSessionId?: string;
        contextScope?: { sceneId?: string; shotId?: string; characterName?: string };
        clientRequestId?: string;
      }): Promise<{ session: ChangeSession; request: ChangeRequest; idempotent?: boolean }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions`, {
      method: 'POST',
      body: JSON.stringify({
        request, source, source_message_id: sourceMessageId,
        source_agent_id: options?.sourceAgentId,
        source_session_id: options?.sourceSessionId,
        context_scope: options?.contextScope ? {
          scene_id: options.contextScope.sceneId,
          shot_id: options.contextScope.shotId,
          character_name: options.contextScope.characterName,
        } : undefined,
        client_request_id: options?.clientRequestId,
      }),
    });
  }
  async listChangeSessions(projectName: string): Promise<ChangeSession[]> {
    const data = await this.request<{ sessions: ChangeSession[] }>(
      `/api/project/${encodeURIComponent(projectName)}/change-sessions`);
    return data.sessions;
  }
  async getChangeSession(projectName: string, sessionId: string): Promise<ChangeSession> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}`);
  }
  async resumeChangeSession(projectName: string, sessionId: string): Promise<ChangeSession> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}/resume`, {
      method: 'POST',
    });
  }
  async getChangeSessionPlan(projectName: string, sessionId: string): Promise<UnifiedChangePlan & { preview?: PlanPreviewArtifact } | null> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}/plan`);
  }
  async getChangeSessionPreview(projectName: string, sessionId: string): Promise<PlanPreviewArtifact | null> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}/preview`);
  }
  async submitChangeDecision(projectName: string, sessionId: string, decisionId: string, optionId: string): Promise<any> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}/decision`, {
      method: 'POST', body: JSON.stringify({ decision_id: decisionId, option_id: optionId }),
    });
  }
  async requestChangeSessionChanges(projectName: string, sessionId: string, feedback: string, planId?: string): Promise<{ plan: UnifiedChangePlan; superseded_plan_id: string | null }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}/plan-feedback`, {
      method: 'POST', body: JSON.stringify({ feedback, plan_id: planId }),
    });
  }
  async approveChangeSession(projectName: string, sessionId: string, planId: string, planVersion: number): Promise<any> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}/approve`, {
      method: 'POST', body: JSON.stringify({ plan_id: planId, plan_version: planVersion }),
    });
  }
  async rejectChangeSession(projectName: string, sessionId: string, planId?: string): Promise<any> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}/reject`, {
      method: 'POST', body: JSON.stringify({ plan_id: planId }),
    });
  }
  // ============ AI-7C Execution（§15/§83）============
  async executeChangePlan(projectName: string, sessionId: string, planId: string,
      planVersion: number, productionMode: string = 'affected_production'): Promise<{ execution: ChangeExecution; [k: string]: any }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}/execute`, {
      method: 'POST', body: JSON.stringify({ plan_id: planId, plan_version: planVersion, production_mode: productionMode }),
    });
  }
  async listChangeExecutions(projectName: string, sessionId: string): Promise<ChangeExecution[]> {
    const data = await this.request<{ executions: ChangeExecution[] }>(
      `/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}/executions`);
    return data.executions;
  }
  async getChangeExecution(projectName: string, sessionId: string, executionId: string): Promise<ChangeExecution> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}/executions/${encodeURIComponent(executionId)}`);
  }
  async getExecutionManifest(projectName: string, sessionId: string, executionId: string): Promise<ProductionExecutionManifest | null> {
    try {
      return await this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}/executions/${encodeURIComponent(executionId)}/manifest`);
    } catch { return null; }
  }
  async resumeChangeExecution(projectName: string, sessionId: string, executionId: string): Promise<{ execution: ChangeExecution; [k: string]: any }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}/executions/${encodeURIComponent(executionId)}/resume`, { method: 'POST' });
  }
  async cancelChangeExecution(projectName: string, sessionId: string, executionId: string): Promise<{ execution: ChangeExecution; cancelled: boolean; creative_committed: boolean }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}/executions/${encodeURIComponent(executionId)}/cancel`, { method: 'POST' });
  }
  /** §4/§7.5：execution eligibility（权威后端判定，不前端复制规则）。供 Execute 按钮态（stale/blocked/invalid_approval 预呈现）。 */
  async getExecutionEligibility(projectName: string, sessionId: string, planId: string,
      planVersion: number): Promise<{ eligibility: string; execution?: ChangeExecution; reason?: string; blocker_code?: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/change-sessions/${encodeURIComponent(sessionId)}/plans/${encodeURIComponent(planVersion)}/eligibility?plan_id=${encodeURIComponent(planId)}`);
  }
  async listChangeSessionProposals(projectName: string, sessionId: string): Promise<ModuleProposal[]> {
    // 复用 plan 端点不返回 proposals；用 intelligence 旁路非必须——此处留扩展点，
    // 当前前端从 plan.module_proposal_ids 已足够展示 owner 分组。
    return [];
  }
  async listChangeSessionDecisions(projectName: string, sessionId: string): Promise<ChangeDecisionRequest[]> {
    // decisions 暂无独立 list 端点；从 session.pending_decision_ids + plan 推导（7B-6 可补端点）。
    return [];
  }
  async listAgentTasks(projectName: string, filters: { requester_agent?: string; target_agent?: string; status?: string } = {}): Promise<AgentTask[]> {
    const q = new URLSearchParams();
    if (filters.requester_agent) q.set('requester_agent', filters.requester_agent);
    if (filters.target_agent) q.set('target_agent', filters.target_agent);
    if (filters.status) q.set('status', filters.status);
    const qs = q.toString();
    const data = await this.request<{ tasks: AgentTask[] }>(`/api/project/${encodeURIComponent(projectName)}/agents/tasks${qs ? '?' + qs : ''}`);
    return data.tasks;
  }
  async createAgentTask(projectName: string, payload: {
    requester_agent?: string; target_agent: string; scope_type: string;
    scope_id: string; instruction: string; capability_id: string; operation?: string;
  }): Promise<AgentTask> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/tasks`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }
  async getAgentTask(projectName: string, tid: string): Promise<AgentTask> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/tasks/${encodeURIComponent(tid)}`);
  }

  // ============ AI-6D Director Proposal（storyboard.propose_change，§11/§13/§16）============
  async createDirectorProposal(projectName: string, payload: {
    agent_id?: string; scope_type: 'shot' | 'scene'; scope_id: string; instruction?: string;
  }): Promise<DirectorProposal> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/director/proposals`, {
      method: 'POST',
      body: JSON.stringify({
        agent_id: payload.agent_id ?? 'director',
        scope_type: payload.scope_type, scope_id: payload.scope_id,
        instruction: payload.instruction ?? '',
      }),
    });
  }
  async getDirectorProposal(projectName: string, proposalId: string): Promise<DirectorProposal> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/director/proposals/${encodeURIComponent(proposalId)}`);
  }
  async listDirectorProposals(projectName: string, scopeType: string, scopeId: string): Promise<DirectorProposal[]> {
    const data = await this.request<{ proposals: DirectorProposal[] }>(
      `/api/project/${encodeURIComponent(projectName)}/agents/director/proposals?scope_type=${encodeURIComponent(scopeType)}&scope_id=${encodeURIComponent(scopeId)}`);
    return data.proposals;
  }
  /** §13/§14：选择性子集再预览 -> 后端重编译被选 item -> 全新 changeset（返回新 currentProposal）。 */
  async previewSelectedProposal(projectName: string, proposalId: string, selectedItemIds: string[]): Promise<DirectorProposal> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/director/proposals/${encodeURIComponent(proposalId)}/preview-selected`, {
      method: 'POST', body: JSON.stringify({ selected_item_ids: selectedItemIds }),
    });
  }
  /** §13：应用当前 changeset（按 changeset_id，复用 AI-6A apply）。 */
  async applyDirectorProposal(projectName: string, proposalId: string, changesetId: string): Promise<{ applied: boolean; revision: number; changeset_id: string; idempotent?: boolean; base_is_stale?: boolean; workspace_id: string; status: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/director/proposals/${encodeURIComponent(proposalId)}/apply`, {
      method: 'POST', body: JSON.stringify({ changeset_id: changesetId }),
    });
  }
  async dismissDirectorProposal(projectName: string, proposalId: string): Promise<{ dismissed: boolean; proposal_id: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/agents/director/proposals/${encodeURIComponent(proposalId)}/dismiss`, { method: 'POST' });
  }

  // ==================== Production Resources V1（Phase 1C1~1C4）====================
  // receipt/result 里 ok=false 的结构化错误直接抛给调用方（页面统一 catch 展示 detail）。
  private async resourcesRequest<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    try {
      return await this.request<T>(endpoint, options);
    } catch (e: any) {
      // request() 抛的是 Error(message)；FastAPI HTTPException detail=receipt 结构化对象
      // 时 message 是 JSON 文本——尝试解析回对象供页面判 error 码。
      const msg = e?.message ?? String(e);
      try {
        const parsed = JSON.parse(msg);
        if (parsed && typeof parsed === 'object' && 'error' in parsed) throw parsed;
      } catch (parseErr) {
        if (parseErr && typeof parseErr === 'object' && 'error' in (parseErr as any)) throw parseErr;
      }
      throw e;
    }
  }
  async resourcesList(projectName: string, type: string = 'all'): Promise<{ resources: any[]; document_revision: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/resources?type=${encodeURIComponent(type)}`);
  }
  async readLook(projectName: string, lookId: string): Promise<any> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/looks/${encodeURIComponent(lookId)}`);
  }
  async listLooks(projectName: string, characterName?: string): Promise<{ looks: any[]; document_revision: string }> {
    const q = characterName ? `?character_name=${encodeURIComponent(characterName)}` : '';
    return this.request(`/api/project/${encodeURIComponent(projectName)}/looks${q}`);
  }
  async readClothing(projectName: string, clothingId: string): Promise<any> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/clothings/${encodeURIComponent(clothingId)}`);
  }
  async listClothings(projectName: string): Promise<{ clothings: any[]; document_revision: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/clothings`);
  }
  async createClothing(projectName: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/clothings`, { method: 'POST', body: JSON.stringify(body) });
  }
  async updateClothing(projectName: string, clothingId: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/clothings/${encodeURIComponent(clothingId)}`, { method: 'PATCH', body: JSON.stringify(body) });
  }
  async deleteClothing(projectName: string, clothingId: string, expectedRevision: string): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/clothings/${encodeURIComponent(clothingId)}?expected_revision=${encodeURIComponent(expectedRevision)}`, { method: 'DELETE' });
  }
  async attachClothingReference(projectName: string, clothingId: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/clothings/${encodeURIComponent(clothingId)}/references`, { method: 'POST', body: JSON.stringify(body) });
  }
  async detachClothingReference(projectName: string, clothingId: string, assetId: string, expectedRevision: string): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/clothings/${encodeURIComponent(clothingId)}/references/${encodeURIComponent(assetId)}?expected_revision=${encodeURIComponent(expectedRevision)}`, { method: 'DELETE' });
  }
  async registerReferenceAsset(projectName: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/reference-assets`, { method: 'POST', body: JSON.stringify(body) });
  }
  async createLook(projectName: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/looks`, { method: 'POST', body: JSON.stringify(body) });
  }
  async updateLook(projectName: string, lookId: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/looks/${encodeURIComponent(lookId)}`, { method: 'PATCH', body: JSON.stringify(body) });
  }
  async generateLookCandidates(projectName: string, lookId: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/looks/${encodeURIComponent(lookId)}/generate`, { method: 'POST', body: JSON.stringify(body) });
  }
  async reviewLookCandidates(projectName: string, lookId: string, body: any = {}): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/looks/${encodeURIComponent(lookId)}/review`, { method: 'POST', body: JSON.stringify(body) });
  }
  async approveLookReference(projectName: string, lookId: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/looks/${encodeURIComponent(lookId)}/approve`, { method: 'POST', body: JSON.stringify(body) });
  }
  async revokeLookApproval(projectName: string, lookId: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/looks/${encodeURIComponent(lookId)}/revoke-approval`, { method: 'POST', body: JSON.stringify(body) });
  }
  async listProps(projectName: string): Promise<{ props: any[]; document_revision: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/props`);
  }
  async readProp(projectName: string, propId: string): Promise<any> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/props/${encodeURIComponent(propId)}`);
  }
  async createProp(projectName: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/props`, { method: 'POST', body: JSON.stringify(body) });
  }
  async updateProp(projectName: string, propId: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/props/${encodeURIComponent(propId)}`, { method: 'PATCH', body: JSON.stringify(body) });
  }
  async deleteProp(projectName: string, propId: string, expectedRevision: string): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/props/${encodeURIComponent(propId)}?expected_revision=${encodeURIComponent(expectedRevision)}`, { method: 'DELETE' });
  }
  async attachPropReference(projectName: string, propId: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/props/${encodeURIComponent(propId)}/references`, { method: 'POST', body: JSON.stringify(body) });
  }
  async detachPropReference(projectName: string, propId: string, assetId: string, expectedRevision: string): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/props/${encodeURIComponent(propId)}/references/${encodeURIComponent(assetId)}?expected_revision=${encodeURIComponent(expectedRevision)}`, { method: 'DELETE' });
  }
  async publishResource(projectName: string, resourceId: string): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/resources/publish`, { method: 'POST', body: JSON.stringify({ resource_id: resourceId }) });
  }
  async publishCharacter(projectName: string, characterName: string): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/resources/publish-character`, { method: 'POST', body: JSON.stringify({ character_name: characterName }) });
  }
  async importSharedResource(projectName: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/resources/import`, { method: 'POST', body: JSON.stringify(body) });
  }
  async listSharedResources(type: string = 'all'): Promise<{ resources: any[] }> {
    return this.request(`/api/shared/resources?type=${encodeURIComponent(type)}`);
  }
  async readSharedResource(sharedId: string): Promise<any> {
    return this.request(`/api/shared/resources/${encodeURIComponent(sharedId)}`);
  }
  // ===== Phase 1C5：Prop Reference Review + Approval + Composition =====
  async reviewPropReference(projectName: string, propId: string, body: any = {}): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/props/${encodeURIComponent(propId)}/review`, { method: 'POST', body: JSON.stringify(body) });
  }
  async approvePropReference(projectName: string, propId: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/props/${encodeURIComponent(propId)}/approve`, { method: 'POST', body: JSON.stringify(body) });
  }
  async revokePropApproval(projectName: string, propId: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/props/${encodeURIComponent(propId)}/revoke-approval`, { method: 'POST', body: JSON.stringify(body) });
  }
  async listCompositions(projectName: string, characterName?: string): Promise<{ compositions: any[]; document_revision: string }> {
    const q = characterName ? `?character_name=${encodeURIComponent(characterName)}` : '';
    return this.request(`/api/project/${encodeURIComponent(projectName)}/compositions${q}`);
  }
  async readComposition(projectName: string, compositionId: string): Promise<any> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/compositions/${encodeURIComponent(compositionId)}`);
  }
  async createComposition(projectName: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/compositions`, { method: 'POST', body: JSON.stringify(body) });
  }
  async updateComposition(projectName: string, compositionId: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/compositions/${encodeURIComponent(compositionId)}`, { method: 'PATCH', body: JSON.stringify(body) });
  }
  async generateCompositionCandidates(projectName: string, compositionId: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/compositions/${encodeURIComponent(compositionId)}/generate`, { method: 'POST', body: JSON.stringify(body) });
  }
  async reviewCompositionCandidates(projectName: string, compositionId: string, body: any = {}): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/compositions/${encodeURIComponent(compositionId)}/review`, { method: 'POST', body: JSON.stringify(body) });
  }
  async approveCompositionCandidate(projectName: string, compositionId: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/compositions/${encodeURIComponent(compositionId)}/approve`, { method: 'POST', body: JSON.stringify(body) });
  }
  async revokeCompositionApproval(projectName: string, compositionId: string, body: any): Promise<any> {
    return this.resourcesRequest(`/api/project/${encodeURIComponent(projectName)}/compositions/${encodeURIComponent(compositionId)}/revoke-approval`, { method: 'POST', body: JSON.stringify(body) });
  }

  // ================= Scene System V1（空间与场景事实系统） =================
  // receipt 结构化错误直接抛对象（页面 fail(e) 读 e.error/e.detail）
  private async sceneRequest<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const url = `${this.baseUrl}${endpoint}`;
    const response = await fetch(url, {
      headers: { 'Content-Type': 'application/json', ...options.headers },
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const d = (data as any).detail;
      if (d && typeof d === 'object' && 'error' in d) throw d;
      throw { error: 'http_error', detail: (data as any).detail ?? `HTTP ${response.status}` } as any;
    }
    return data as T;
  }

  // ---- 读 ----
  async sceneOverview(projectName: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/overview`);
  }
  async sceneListLocations(projectName: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/locations`);
  }
  async sceneReadLocation(projectName: string, locationId: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/locations/${encodeURIComponent(locationId)}`);
  }
  async sceneListAnchors(projectName: string, locationId: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/anchors?location_id=${encodeURIComponent(locationId)}`);
  }
  async sceneReadLayoutByLocation(projectName: string, locationId: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/locations/${encodeURIComponent(locationId)}/layout`).catch(() => null);
  }
  async sceneListViews(projectName: string, locationId: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/views?location_id=${encodeURIComponent(locationId)}`);
  }
  async sceneListSceneInstances(projectName: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/scene-instances`);
  }
  async sceneReadSceneInstance(projectName: string, instanceId: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/scene-instances/${encodeURIComponent(instanceId)}`);
  }
  async sceneListInstanceViews(projectName: string, instanceId: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/scene-instance-views?scene_instance_id=${encodeURIComponent(instanceId)}`);
  }
  async sceneReadInstanceViewStatus(projectName: string, scivId: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/scene-instance-views/${encodeURIComponent(scivId)}`);
  }
  async sceneViewGenerations(projectName: string, viewId: string, limit = 6): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/views/${encodeURIComponent(viewId)}/generations?limit=${limit}`);
  }
  async sceneInstanceViewGenerations(projectName: string, scivId: string, limit = 6): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/scene-instance-views/${encodeURIComponent(scivId)}/generations?limit=${limit}`).catch(() => ({ generation_records: [] }));
  }
  async sceneInstanceViewReviews(projectName: string, scivId: string, limit = 5): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/scene-instance-views/${encodeURIComponent(scivId)}/reviews?limit=${limit}`).catch(() => ({ review_records: [] }));
  }
  async sceneViewApprovals(projectName: string, viewId: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/views/${encodeURIComponent(viewId)}/approvals`).catch(() => ({ approval_records: [] }));
  }
  // 历史审查记录（用于刷新后恢复"哪些候选已审查通过"→ 决定批准按钮可用性）
  async sceneViewReviews(projectName: string, viewId: string, limit = 5): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/views/${encodeURIComponent(viewId)}/reviews?limit=${limit}`).catch(() => ({ review_records: [] }));
  }
  async sceneInstanceViewApprovals(projectName: string, scivId: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/scene-instance-views/${encodeURIComponent(scivId)}/approvals`).catch(() => ({ approval_records: [] }));
  }

  // ---- Location ----
  async sceneCreateLocation(projectName: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/locations`, { method: 'POST', body: JSON.stringify(body) });
  }
  async sceneUpdateLocation(projectName: string, locationId: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/locations/${encodeURIComponent(locationId)}`, { method: 'PATCH', body: JSON.stringify(body) });
  }
  // ---- Anchor ----
  async sceneCreateAnchor(projectName: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/anchors`, { method: 'POST', body: JSON.stringify(body) });
  }
  async sceneUpdateAnchor(projectName: string, anchorId: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/anchors/${encodeURIComponent(anchorId)}`, { method: 'PATCH', body: JSON.stringify(body) });
  }
  async sceneDeleteAnchor(projectName: string, anchorId: string, expectedRevision: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/anchors/${encodeURIComponent(anchorId)}?expected_revision=${encodeURIComponent(expectedRevision)}`, { method: 'DELETE' });
  }
  // ---- Layout ----
  async sceneCreateLayout(projectName: string, locationId: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/locations/${encodeURIComponent(locationId)}/layout`, { method: 'POST', body: JSON.stringify(body) });
  }
  async sceneUpdateLayout(projectName: string, layoutId: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/layouts/${encodeURIComponent(layoutId)}`, { method: 'PATCH', body: JSON.stringify(body) });
  }
  // ---- LocationView ----
  async sceneCreateView(projectName: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/views`, { method: 'POST', body: JSON.stringify(body) });
  }
  async sceneGenerateLocationView(projectName: string, viewId: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/views/${encodeURIComponent(viewId)}/generate`, { method: 'POST', body: JSON.stringify(body) });
  }
  async sceneReviewLocationView(projectName: string, viewId: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/views/${encodeURIComponent(viewId)}/review`, { method: 'POST', body: JSON.stringify({}) });
  }
  async sceneApproveLocationView(projectName: string, viewId: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/views/${encodeURIComponent(viewId)}/approve`, { method: 'POST', body: JSON.stringify(body) });
  }
  async sceneRevokeLocationView(projectName: string, viewId: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/views/${encodeURIComponent(viewId)}/revoke`, { method: 'POST', body: JSON.stringify(body) });
  }
  // ---- SceneInstance ----
  async sceneCreateSceneInstance(projectName: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/scene-instances`, { method: 'POST', body: JSON.stringify(body) });
  }
  async scenePatchSceneState(projectName: string, instanceId: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/scene-instances/${encodeURIComponent(instanceId)}/state`, { method: 'PATCH', body: JSON.stringify(body) });
  }
  // ---- SceneInstanceView ----
  async sceneCreateInstanceView(projectName: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/scene-instance-views`, { method: 'POST', body: JSON.stringify(body) });
  }
  async sceneGenerateInstanceView(projectName: string, scivId: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/scene-instance-views/${encodeURIComponent(scivId)}/generate`, { method: 'POST', body: JSON.stringify(body) });
  }
  async sceneReviewInstanceView(projectName: string, scivId: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/scene-instance-views/${encodeURIComponent(scivId)}/review`, { method: 'POST', body: JSON.stringify({}) });
  }
  async sceneApproveInstanceView(projectName: string, scivId: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/scene-instance-views/${encodeURIComponent(scivId)}/approve`, { method: 'POST', body: JSON.stringify(body) });
  }
  async sceneRevokeInstanceView(projectName: string, scivId: string, body: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/scene-system/scene-instance-views/${encodeURIComponent(scivId)}/revoke`, { method: 'POST', body: JSON.stringify(body) });
  }

  // ==================== Sequence Assembly（镜头合成出片；后端 routers/sequences.py）====================
  async listSequences(projectName: string): Promise<{ sequences: any[] }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/sequences`);
  }
  async createSequence(projectName: string, body: {
    title?: string; scene_id?: string | null;
    clips: Array<{ shot_id: string; trim_in_ms?: number; trim_out_ms?: number }>;
  }): Promise<any> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/sequences`, { method: 'POST', body: JSON.stringify(body) });
  }
  async getSequence(projectName: string, sequenceId: string): Promise<{ sequence: any }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/sequences/${encodeURIComponent(sequenceId)}`);
  }
  async updateSequence(projectName: string, sequenceId: string, body: {
    title?: string; scene_id?: string | null;
    clips?: Array<{ shot_id: string; trim_in_ms?: number; trim_out_ms?: number }>;
    expected_revision?: string;
  }): Promise<any> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/sequences/${encodeURIComponent(sequenceId)}`, { method: 'PUT', body: JSON.stringify(body) });
  }
  async deleteSequence(projectName: string, sequenceId: string): Promise<any> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/sequences/${encodeURIComponent(sequenceId)}`, { method: 'DELETE' });
  }
  async renderSequence(projectName: string, sequenceId: string): Promise<{ job_id: string; render_version_id: string; status: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/sequences/${encodeURIComponent(sequenceId)}/render`, { method: 'POST' });
  }
  async listSequenceRenders(projectName: string, sequenceId: string): Promise<{ final_render: string | null; render_versions: any[] }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/sequences/${encodeURIComponent(sequenceId)}/renders`);
  }
  async selectSequenceRender(projectName: string, sequenceId: string, versionId: string): Promise<any> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/sequences/${encodeURIComponent(sequenceId)}/renders/select`, { method: 'POST', body: JSON.stringify({ version_id: versionId }) });
  }
  async getSequenceFreshness(projectName: string, sequenceId: string, versionId?: string): Promise<any> {
    const q = versionId ? `?version_id=${encodeURIComponent(versionId)}` : '';
    return this.request(`/api/project/${encodeURIComponent(projectName)}/sequences/${encodeURIComponent(sequenceId)}/freshness${q}`);
  }

  // ==================== Shot Production Binding（镜头生产绑定；后端 routers/shot_binding.py）====================
  // 复用 sceneRequest 的 receipt 解析（transport-only）：ok=False 时抛 {error, detail} 对象
  async listShotBindings(projectName: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/shot-bindings`);
  }
  async getShotBinding(projectName: string, shotId: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/shot-bindings/${encodeURIComponent(shotId)}`);
  }
  async getShotBindingValidity(projectName: string, shotId: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/shot-bindings/${encodeURIComponent(shotId)}/validity`);
  }
  async createShotBinding(projectName: string, payload: any): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/shot-bindings`, { method: 'POST', body: JSON.stringify(payload) });
  }
  async replaceShotBinding(projectName: string, shotId: string, payload: any, expectedBindingRevision: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/shot-bindings/${encodeURIComponent(shotId)}`,
      { method: 'PUT', body: JSON.stringify({ ...payload, expected_binding_revision: expectedBindingRevision }) });
  }
  async deleteShotBinding(projectName: string, shotId: string, expectedBindingRevision: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/shot-bindings/${encodeURIComponent(shotId)}?expected_binding_revision=${encodeURIComponent(expectedBindingRevision)}`, { method: 'DELETE' });
  }
  async refreshShotBinding(projectName: string, shotId: string, expectedBindingRevision: string): Promise<any> {
    return this.sceneRequest(`/api/project/${encodeURIComponent(projectName)}/shot-bindings/${encodeURIComponent(shotId)}/refresh`,
      { method: 'POST', body: JSON.stringify({ expected_binding_revision: expectedBindingRevision }) });
  }
  // ============ Phase55：协作模式注册表与 run（批次 E 产品入口） ============

  async collabModes(project: string): Promise<{ modes: any[] }> {
    return this.request(`/api/project/${encodeURIComponent(project)}/collab-modes`);
  }

  async collabRunStart(
    project: string,
    body: { mode_id: string; params: Record<string, unknown>; granted_by?: string; thinking?: boolean },
  ): Promise<{ run: any }> {
    return this.request(`/api/project/${encodeURIComponent(project)}/collab-runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  async collabRuns(project: string): Promise<{ runs: any[] }> {
    return this.request(`/api/project/${encodeURIComponent(project)}/collab-runs`);
  }

  async collabRunGet(project: string, runId: string): Promise<{ run: any }> {
    return this.request(`/api/project/${encodeURIComponent(project)}/collab-runs/${encodeURIComponent(runId)}`);
  }

  async collabRunCancel(project: string, runId: string, reason = ''): Promise<{ stopped: boolean; status: string }> {
    return this.request(`/api/project/${encodeURIComponent(project)}/collab-runs/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
  }

}

export const apiClient = new ApiClient();

