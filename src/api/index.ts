/**
 * API客户端
 * 与后端API通信
 */

const API_BASE_URL = 'http://localhost:8000';

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

  async createProject(name: string): Promise<void> {
    await this.request(`/api/projects/create?name=${encodeURIComponent(name)}`, {
      method: 'POST',
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
}

export const apiClient = new ApiClient();
