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
  first_frame: ImageAssetData;
  last_frame: ImageAssetData;
  audio: AudioAssetData;
  subtitle_srt: string | null;
  video: VideoAssetData;
  status: string;
  locked: boolean;
  note: string;
  split_next: string | null;
}
export interface ProjectShots {
  name: string;
  style: Record<string, any>;
  characters: Array<{ name: string; description_en: string; views: Record<string, AssetData> }>;
  scenes: Array<{
    scene_id: string; title: string; location: string; time_of_day: string; summary: string;
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

  async getProjectProgress(projectName: string): Promise<Record<string, any>> {
    return this.request(`/api/projects/${projectName}/progress`);
  }

  async getProjectScenes(projectName: string): Promise<Scene[]> {
    const data = await this.request<{ scenes: Scene[] }>(
      `/api/projects/${projectName}/scenes`
    );
    return data.scenes;
  }

  async getProjectCharacters(projectName: string): Promise<Character[]> {
    const data = await this.request<{ characters: Character[] }>(
      `/api/projects/${projectName}/characters`
    );
    return data.characters;
  }

  async getProjectOutputs(projectName: string): Promise<ProjectOutputs> {
    return this.request(`/api/projects/${projectName}/outputs`);
  }

  // 工作流相关
  async runWorkflow(
    projectName: string,
    scriptContent: string,
    mode: string = 'interactive'
  ): Promise<{ task_id: string }> {
    return this.request('/api/run', {
      method: 'POST',
      body: JSON.stringify({
        project_name: projectName,
        script_content: scriptContent,
        mode,
      }),
    });
  }

  async getCurrentTask(): Promise<TaskStatus> {
    return this.request('/api/task/current');
  }

  async getTaskStatus(taskId: string): Promise<TaskStatus> {
    return this.request(`/api/task/${taskId}`);
  }

  // 场景相关
  async regenerateScene(projectName: string, sceneId: number): Promise<void> {
    await this.request(`/api/projects/${projectName}/scenes/${sceneId}/regenerate`, {
      method: 'POST',
    });
  }

  async confirmScene(projectName: string, sceneId: number): Promise<void> {
    await this.request(`/api/projects/${projectName}/scenes/${sceneId}/confirm`, {
      method: 'POST',
    });
  }

  // 角色相关
  async regenerateCharacter(projectName: string, characterName: string): Promise<void> {
    await this.request(`/api/projects/${projectName}/characters/${encodeURIComponent(characterName)}/regenerate`, {
      method: 'POST',
    });
  }

  // ============ 角色生成（独立操作） ============

  // 单视角生成 + 抠图
  async generateCharacterView(params: {
    description: string;
    view: string;
    style?: string;
    size?: string;
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
    size?: string;
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
    size?: string;
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
    size?: string;
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

  // 动画相关
  async regenerateAnimation(projectName: string, fromScene: number, toScene: number): Promise<void> {
    await this.request(`/api/projects/${projectName}/animations/${fromScene}/${toScene}/regenerate`, {
      method: 'POST',
    });
  }

  // 音频相关
  async generateAllAudio(projectName: string): Promise<void> {
    await this.request(`/api/projects/${projectName}/audio/generate`, {
      method: 'POST',
    });
  }

  async regenerateAudio(projectName: string, sceneId: number): Promise<void> {
    await this.request(`/api/projects/${projectName}/audio/${sceneId}/regenerate`, {
      method: 'POST',
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

  // 保存剧本（YAML + 解析 -> parsed_script.json，保留已有生成结果）
  async saveProjectScript(projectName: string, scriptContent: string, enhance: boolean = false): Promise<{ success: boolean; characters_count: number; scenes_count: number; message: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/script`, {
      method: 'POST',
      body: JSON.stringify({ script_content: scriptContent, enhance }),
    });
  }

  // 单场景分镜生成（自动取场景角色 front 图作参考，后台任务）
  async generateSceneStoryboard(projectName: string, sceneId: number): Promise<{ task_id: string; message: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/scene/${sceneId}/generate-storyboard`, {
      method: 'POST',
    });
  }

  // 单场景音频生成（支持 voice/speed，让前端 TTS 设置生效）
  async generateSceneAudio(projectName: string, sceneId: number, params: { voice?: string; speed?: number } = {}): Promise<{ task_id: string; message: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/audio/${sceneId}/generate`, {
      method: 'POST',
      body: JSON.stringify(params),
    });
  }

  // 合成最终视频（ffmpeg，后台任务）
  async composeVideo(projectName: string, params: { scene_duration?: number; width?: number; height?: number; fps?: number } = {}): Promise<{ task_id: string; message: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/compose`, {
      method: 'POST',
      body: JSON.stringify(params),
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

  // 选择首帧候选（设 selected_index + status=done -> url_ready=true）
  async chooseFirstFrame(projectName: string, shotId: string, variant: number): Promise<{ shot_id: string; selected_index: number; url: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/first-frame/choose`, {
      method: 'POST', body: JSON.stringify({ variant }),
    });
  }

  // 尾帧生成（仅 keyframes）
  async generateLastFrame(projectName: string, shotId: string): Promise<{ job_id: string; shot_id: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/last-frame`, { method: 'POST' });
  }

  // 镜头视频生成（入队 video 任务，串行 1 RPM）
  async generateShotVideo(projectName: string, shotId: string): Promise<{ job_id: string; shot_id: string }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}/video`, { method: 'POST' });
  }

  // 更新镜头字段（link_mode / locked）
  async updateShot(projectName: string, shotId: string, params: { link_mode?: string; locked?: boolean }): Promise<{ shot_id: string; link_mode?: string; locked?: boolean }> {
    return this.request(`/api/project/${encodeURIComponent(projectName)}/shot/${encodeURIComponent(shotId)}`, {
      method: 'PUT', body: JSON.stringify(params),
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
  async cancelJob(jobId: string): Promise<{ job_id: string; state: string }> {
    return this.request(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' });
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
