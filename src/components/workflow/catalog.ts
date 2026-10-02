// 仓库注册表快照（现行生产事实，供可视化工作流前端使用）。
//
// 数据来源（逐字核对，勿凭记忆增删）：
// - Agent 定义：src/agents/registry.py:36-133（AgentDefinition）。
//   正式名册注册 10 个；这里只收「现行生产的 6 个原生 tool-loop Agent」
//   （src/agents/runtime.py:349-407 unified_turn 实际分发的六个；
//   producer.py:94-96 DELEGATE_TOOLS 恰好 5 个 delegate）。
//   production_planner / shot_producer / continuity_reviewer / editor 注册在册
//   但走旧 capability-dispatch 路径，第一版组件库不收录。
// - 工具面：各 Agent 模块的工具 schema 定义（见每条 toolsSource）。
//   勾选工具仅编辑前端草稿，不改变运行时权限（权限=物理工具面+写域映射+五重校验）。
// - Skill：src/agents/skill_system/builtin/*/skill.json（fail-closed 校验通过的
//   builtin_core / active 包；后端无 Skill REST 端点，故内嵌快照，新增 Skill 需同步本文件）。
// - Skill 可绑定名册：src/agents/skill_system/models.py:45-49 VALID_AGENT_IDS
//   （共 9 个，不含 assembly——Skill 绑定 assembly 后端不支持）。

export interface AgentCatalogEntry {
  id: string;
  /** registry.py AgentDefinition.name */
  name: string;
  /** registry.py AgentDefinition.role（中文短标签） */
  role: string;
  /** registry.py AgentDefinition.description */
  description: string;
  readScopes: string[];
  writeScopes: string[];
  /** 该 Agent 获准使用的完整工具面（草稿勾选池，按注册序） */
  tools: string[];
  /** 工具面在仓库的定义位置 */
  toolsSource: string;
  /** registry.py 定义位置 */
  source: string;
  /** 是否在后端 Skill 可绑定名册（skill_system/models.py VALID_AGENT_IDS） */
  skillBindable: boolean;
  /** 已知产物（来自 write_scopes 与系统提示，未核实到 canonical 字段的注明） */
  artifacts: string[];
}

const SKILL_TOOLS = ['skill.list', 'skill.load', 'skill.read_resource'];

export const WORKFLOW_AGENTS: AgentCatalogEntry[] = [
  {
    id: 'producer',
    name: 'Producer Supervisor',
    role: '总协调',
    description: '项目总协调者：理解需求、分派任务、汇总结果',
    readScopes: ['project', 'workflow', 'agent_tasks', 'workspaces'],
    writeScopes: ['agent_task'],
    tools: [
      // src/agents/producer.py:204-300（读工具过滤复用 + delegate 内联 schema）
      'project.inspect',
      'project.quota',
      'story.list',
      'story.read',
      'script.read',
      'character.list',
      'character.read',
      'shot.list',
      'shot.read',
      'assembly.sequence_read',
      'assembly.render_status',
      'script_writer.delegate',
      'character_designer.delegate',
      'director.delegate',
      'scene_designer.delegate',
      'assembly.delegate',
    ],
    toolsSource: 'src/agents/producer.py:204-300',
    source: 'src/agents/registry.py:38-46',
    skillBindable: true,
    artifacts: [
      'agent_task（委派 envelope：objective/situation/constraints/scope）',
      'AgentStructuredResult 汇总（status/summary/artifact_refs）',
    ],
  },
  {
    id: 'script_writer',
    name: 'Script Writer',
    role: '剧本',
    description: '负责故事/场景/剧情结构与修改',
    readScopes: ['story', 'scenes', 'characters'],
    writeScopes: ['story_proposal', 'scene_proposal'],
    tools: [
      // src/agents/script_writer.py（SCRIPT_WRITER_TOOLS）
      'script.read',
      'script.update',
      'story.create',
      'story.create_many',
      'story.delete',
      'story.delete_many',
      'story.insert_many',
      'story.list',
      'story.move',
      'story.read',
      'story.update',
      'story.update_many',
      'web.search',
    ],
    toolsSource: 'src/agents/script_writer.py',
    source: 'src/agents/registry.py:47-54',
    skillBindable: true,
    artifacts: ['story_proposal / story 故事段', 'scene_proposal / 剧本正文 script'],
  },
  {
    id: 'character_designer',
    name: 'Resource Agent',
    role: '角色 · 造型 · 道具 · 生产资源',
    description:
      '资源设计师（内部兼容 id）：角色身份/三视图/服装/造型/道具/组合参考/共享库',
    readScopes: ['characters', 'style', 'scenes'],
    writeScopes: ['character_proposal', 'character_assets'],
    tools: [
      // src/agents/character_designer.py（CHARACTER_DESIGNER_TOOLS）
      'character.list',
      'character.read',
      'character.create',
      'character.update',
      'character.delete',
      'character.generate_views',
      'resource.inventory',
      'resource.read',
      'resource.clothing.upsert',
      'resource.look.prepare',
      'resource.look.approve',
      'resource.prop.register',
      'resource.composition.prepare',
      'resource.library.publish',
      'resource.library.import',
      'project.visual_profile.read',
      'web.search',
    ],
    toolsSource: 'src/agents/character_designer.py',
    source: 'src/agents/registry.py:55-63',
    skillBindable: true,
    artifacts: [
      'character_assets（角色设定/三视图/服装 Look/道具/组合参考）',
      'character_proposal',
    ],
  },
  {
    id: 'scene_designer',
    name: 'Scene Agent',
    role: '地点 · 空间 · 场景状态 · 场景参考',
    description:
      '场景设计师：Location/SpatialAnchor/SetLayout/LocationView/SceneInstance/SceneState/SceneInstanceView 生产事实与 approved 场景参考',
    readScopes: ['scenes', 'characters', 'shots', 'style', 'scene_production'],
    writeScopes: ['scene_production'],
    tools: [
      // src/agents/scene_designer.py（SCENE_DESIGNER_TOOLS）
      'scene.inventory',
      'scene.production.inspect',
      'scene.location.upsert',
      'scene.anchor.upsert',
      'scene.layout.set',
      'scene.view.prepare',
      'scene.instance.prepare',
      'scene.instance.delete',
      'scene.state.update',
      'scene.state_view.prepare',
    ],
    toolsSource: 'src/agents/scene_designer.py',
    source: 'src/agents/registry.py:64-75',
    skillBindable: true,
    artifacts: [
      'scene_production（Location/SpatialAnchor/SetLayout/LocationView/SceneInstance/SceneState/SceneInstanceView）',
      'approved 场景视觉参考',
    ],
  },
  {
    id: 'director',
    name: 'Director',
    role: '导演',
    description: '场景→镜头、分镜结构/节奏/意图；修改须经 Workspace',
    readScopes: ['scenes', 'characters', 'shots', 'style'],
    writeScopes: ['storyboard_proposal'],
    tools: [
      // src/agents/director.py:905-923 DIRECTOR_TOOLS（含 director_visual/_calibration/_bridge/_frames）
      'shot.list',
      'shot.read',
      'shot.create',
      'shot.create_many',
      'shot.update',
      'shot.update_many',
      'shot.delete',
      'shot.move',
      'shot.select_frame',
      'shot.generate_images',
      'shot.generate_videos',
      'story.list',
      'story.read',
      'script.read',
      'character.list',
      'character.read',
      'project.visual_profile.read',
      'shot.production.inspect',
      'shot.production.bind',
      'shot.production.refresh',
      'visual.review_image',
      'visual.review_shot',
      'calibration.seam_measure',
      'calibration.run_create',
      'calibration.trace_read',
      'calibration.trace_upsert',
      'video.extract_frame',
      'visual.sample_frames',
      'shot.bridge_create',
      ...SKILL_TOOLS,
      'web.search',
    ],
    toolsSource: 'src/agents/director.py:905-923 等',
    source: 'src/agents/registry.py:76-83',
    skillBindable: true,
    artifacts: [
      'storyboard_proposal（分镜结构提案；shot 媒体经 shot.* 工具，改正式项目须走 Workspace）',
    ],
  },
  {
    id: 'assembly',
    name: 'Assembly Agent',
    role: '成片装配',
    description:
      '基础成片装配：冻结快照消费/时间线草案/渲染提交/导出诊断/时间线契约发布/剪辑提案',
    readScopes: ['shots', 'versions', 'sequences', 'snapshot'],
    writeScopes: ['sequence_assembly'],
    tools: [
      // src/agents/assembly.py:76-96（ASSEMBLY_READ_TOOLS + WRITE_TOOL_DOMAIN）
      'assembly.briefing',
      'assembly.snapshot_read',
      'assembly.sequence_read',
      'assembly.media_probe',
      'assembly.timeline_preview',
      'assembly.render_status',
      'assembly.narration_read',
      'assembly.audio_plan_read',
      'assembly.audio_assets_read',
      'assembly.sequence_create',
      'assembly.sequence_update',
      'assembly.render_submit',
      'assembly.manifest_publish',
      'assembly.proposal_write',
      'assembly.tts_synthesize',
      'assembly.audio_plan_put',
      'assembly.stem_render',
      'web.search',
    ],
    toolsSource: 'src/agents/assembly.py:76-96',
    source: 'src/agents/registry.py:123-132',
    // skill_system/models.py:45-49 VALID_AGENT_IDS 不含 assembly
    skillBindable: false,
    artifacts: [
      'sequence_assembly（AssemblyManifest assembly_manifest_v1 / 时间线草案 / 渲染提交）',
    ],
  },
];

export interface SkillBindingEntry {
  agentId: string;
  role: string;
  routingPriority: number;
  activationMode: string;
}

export interface SkillCatalogEntry {
  id: string;
  version: string;
  name: string;
  description: string;
  category: string;
  source: string;
  status: string;
  applicability: string;
  dependsOn: string[];
  /** 官方绑定（skill.json bindings，供配置面板展示与示例使用） */
  bindings: SkillBindingEntry[];
  manifestSource: string;
}

export const WORKFLOW_SKILLS: SkillCatalogEntry[] = [
  {
    id: 'full-story-relay-calibration',
    version: '1.3.1',
    name: '全片尾帧接力连续性校准',
    description:
      '整条故事镜头链的尾帧接力连续性生产协作：预编剧冻结→逐镜生成→接缝测量→Vision 证据→三档裁定（none/minor/major）→trace 落档→按档继续/修复/升级。',
    category: 'workflow',
    source: 'builtin_core',
    status: 'active',
    applicability:
      '整条故事镜头链按尾帧接力逐镜生成视频、需要保持跨镜头连续性时；或用户要求进行全片连续性校准/检查。',
    dependsOn: [],
    bindings: [
      { agentId: 'director', role: 'primary_executor', routingPriority: 100, activationMode: 'hinted' },
      { agentId: 'producer', role: 'coordinator', routingPriority: 80, activationMode: 'hinted' },
      { agentId: 'script_writer', role: 'contributor', routingPriority: 60, activationMode: 'hinted' },
      { agentId: 'character_designer', role: 'conditional', routingPriority: 40, activationMode: 'hinted' },
      { agentId: 'scene_designer', role: 'conditional', routingPriority: 40, activationMode: 'hinted' },
    ],
    manifestSource:
      'src/agents/skill_system/builtin/full-story-relay-calibration/skill.json',
  },
  {
    id: 'keyframes-bridge-repair',
    version: '1.3.1',
    name: 'keyframes 接续桥：接缝修复的标准手段',
    description:
      '当需要同时固定两侧边界（首尾都对齐）的过渡时，用 keyframes 桥插镜修复接缝：双端钉死+provider 插值，级联影响=0。',
    category: 'workflow',
    source: 'builtin_core',
    status: 'active',
    applicability:
      '重生成级联造成的下游断缝、跨 Location 且需要连续过渡、大姿态反转、任何需要「首尾都对齐」的过渡。',
    dependsOn: ['full-story-relay-calibration'],
    bindings: [
      { agentId: 'director', role: 'primary_executor', routingPriority: 90, activationMode: 'hinted' },
      { agentId: 'producer', role: 'coordinator', routingPriority: 40, activationMode: 'hinted' },
    ],
    manifestSource:
      'src/agents/skill_system/builtin/keyframes-bridge-repair/skill.json',
  },
];

export function getAgent(id: string | null | undefined): AgentCatalogEntry | undefined {
  if (!id) return undefined;
  return WORKFLOW_AGENTS.find((a) => a.id === id);
}

export function getSkill(id: string | null | undefined): SkillCatalogEntry | undefined {
  if (!id) return undefined;
  return WORKFLOW_SKILLS.find((s) => s.id === id);
}
