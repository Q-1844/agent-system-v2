// 执行依赖线的产物契约（只引用仓库可核实的字段；查不到的标 pending_backend）。
// 依据表：docs/workflow/可视化工作流-依据表与草稿契约.md

import type { EdgeContract } from './types';
import { getAgent } from './catalog';

type PairKey = string; // `${sourceRefId}->${targetRefId}`

/** 仓库有明确契约的 Agent 对（逐对证据，禁止外推） */
const VERIFIED_PAIRS: Record<PairKey, EdgeContract> = {
  // Producer 委派：producer.py:237-258 _delegate_schema + :94-96 DELEGATE_TOOLS
  'producer->script_writer': {
    status: 'verified',
    evidence: 'src/agents/producer.py:94-96,237-258（delegate envelope）',
    upstreamArtifacts: ['AgentTask 委派（objective/situation/constraints/scope/user_intent）'],
    downstreamReads: ['Script Writer 按 owns 域（Story/剧本正文/故事段结构）执行'],
  },
  'producer->character_designer': {
    status: 'verified',
    evidence: 'src/agents/producer.py:94-96,237-258（delegate envelope）',
    upstreamArtifacts: ['AgentTask 委派（objective/situation/constraints/scope/user_intent）'],
    downstreamReads: ['Resource Agent 按 owns 域（角色/Look/道具/共享库）执行'],
  },
  'producer->scene_designer': {
    status: 'verified',
    evidence: 'src/agents/producer.py:94-96,237-258（delegate envelope）',
    upstreamArtifacts: ['AgentTask 委派（objective/situation/constraints/scope/user_intent）'],
    downstreamReads: ['Scene Agent 按 owns 域（场景系统生产事实）执行'],
  },
  'producer->director': {
    status: 'verified',
    evidence: 'src/agents/producer.py:94-96,237-258（delegate envelope）',
    upstreamArtifacts: ['AgentTask 委派（objective/situation/constraints/scope/user_intent）'],
    downstreamReads: ['Director 按 owns 域（分镜结构/镜头）执行'],
  },
  'producer->assembly': {
    status: 'verified',
    evidence: 'src/agents/producer.py:94-96,237-258（delegate envelope）',
    upstreamArtifacts: ['AgentTask 委派（objective/situation/constraints/scope/user_intent）'],
    downstreamReads: ['Assembly Agent 按 owns 域（成片装配，单写域 sequence）执行'],
  },
  // Specialist→Producer 汇总回执：src/agents/models.py:296-313
  'script_writer->producer': STRUCT_RESULT_CONTRACT(),
  'character_designer->producer': STRUCT_RESULT_CONTRACT(),
  'scene_designer->producer': STRUCT_RESULT_CONTRACT(),
  'director->producer': STRUCT_RESULT_CONTRACT(),
  'assembly->producer': STRUCT_RESULT_CONTRACT(),
  // script→director：director 工具面真实读取 story/script
  'script_writer->director': {
    status: 'verified',
    evidence: 'src/agents/director.py:905-923（STORY_LIST/STORY_READ/SCRIPT_READ 工具）',
    upstreamArtifacts: ['story 故事段', 'script 剧本正文'],
    downstreamReads: ['story.list / story.read / script.read'],
  },
  // 场景→导演：registry.py 明文「可供导演稳定使用」+ director read_scopes 含 scenes
  'scene_designer->director': {
    status: 'verified',
    evidence:
      'src/agents/registry.py:64-75（scene_designer 描述「可供导演稳定使用」）+ :76-83（director read_scopes=scenes/characters/shots/style）',
    upstreamArtifacts: ['scene_production 生产事实', 'approved 场景视觉参考'],
    downstreamReads: ['read_scopes: scenes / style'],
  },
  // 资源→导演：director 工具面真实读取 character
  'character_designer->director': {
    status: 'verified',
    evidence: 'src/agents/director.py:905-923（CHARACTER_LIST/CHARACTER_READ 工具）',
    upstreamArtifacts: ['character_assets（角色参考图/三视图/Look/道具）'],
    downstreamReads: ['character.list / character.read'],
  },
  // 导演→装配：冻结快照契约（44A §11）+ manifest v1 不变式
  'director->assembly': {
    status: 'verified',
    evidence:
      'src/agents/assembly.py:98-99（DEFAULT_SNAPSHOT_RELPATH）；src/sequence/assembly_manifest.py:30-56（assembly_manifest_v1）',
    upstreamArtifacts: [
      '冻结快照 shots（shot_id/video_version_id/media_sha256/approved_status）',
      'Shot.final_video 源真相',
    ],
    downstreamReads: ['assembly.snapshot_read', 'read_scopes: shots/versions/snapshot'],
  },
};

function STRUCT_RESULT_CONTRACT(): EdgeContract {
  return {
    status: 'verified',
    evidence: 'src/agents/models.py:296-313（AgentStructuredResult）',
    upstreamArtifacts: ['AgentStructuredResult（status/summary/artifact_refs/warnings）'],
    downstreamReads: ['Producer 汇总（artifact_refs 形状宽松：{type: scene|shot|character, id}）'],
  };
}

/**
 * 计算执行依赖线的契约。
 * verified=仓库有明确契约；pending_backend=该 Agent 对没有可核实的逐对产物字段，
 * 只展示双方 registry.py 声明的 write/read scopes（语义级，非 canonical 字段）。
 */
export function executionContract(
  sourceRefId: string | null,
  targetRefId: string | null,
  sourceIsReview: boolean,
  targetIsReview: boolean
): EdgeContract {
  // 人工审核线：首版语义，无 canonical 字段
  if (sourceIsReview || targetIsReview) {
    const up = getAgent(sourceRefId);
    return {
      status: 'human_gate',
      evidence:
        '本页首版语义：通过→沿执行依赖继续；驳回→停止并留意见，等待人工修改工作流或重新发起（自动返工待后端定义）',
      upstreamArtifacts: up
        ? up.artifacts
        : sourceIsReview
          ? ['上一道审核通过后的后续产物']
          : ['开始（画布边界）'],
      downstreamReads: ['人工审核确认后，下一环节方可开始'],
    };
  }
  const key = `${sourceRefId}->${targetRefId}`;
  const verified = VERIFIED_PAIRS[key];
  if (verified) return verified;

  const src = getAgent(sourceRefId);
  const dst = getAgent(targetRefId);
  return {
    status: 'pending_backend',
    evidence: '未在仓库找到该 Agent 对的逐对产物契约（read/write_scopes 为语义声明，非 canonical 字段）',
    upstreamArtifacts: src ? src.writeScopes.map((s) => `write_scopes: ${s}`) : [],
    downstreamReads: dst ? dst.readScopes.map((s) => `read_scopes: ${s}`) : [],
  };
}
