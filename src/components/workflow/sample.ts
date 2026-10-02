// 示例工作流：全部使用真实注册项（不虚构六 Agent 默认链之外的东西）。
// 链条：开始 → Script Writer → Resource Agent → Scene Agent → 人工审核① →
//       Director（绑定全片尾帧接力连续性校准 v1.3.1）→ 人工审核② → Assembly → 结束。
// Skill 绑定来自 full-story-relay-calibration/skill.json 的官方 bindings（director=primary_executor）。

import type { WorkflowDraft, WorkflowNode } from './types';
import { makeNode } from './types';
import { newEmptyDraft } from './storage';
import { executionContract } from './contracts';

export function buildSampleDraft(projectId: string): WorkflowDraft {
  const draft = newEmptyDraft(projectId, '示例：尾帧接力生产链');

  const start = makeNode({ type: 'boundary_start', position: { x: -40, y: 300 } });
  const script = makeNode({
    type: 'agent', refId: 'script_writer',
    position: { x: 160, y: 280 },
  });
  const resource = makeNode({
    type: 'agent', refId: 'character_designer',
    position: { x: 420, y: 280 },
  });
  const scene = makeNode({
    type: 'agent', refId: 'scene_designer',
    position: { x: 680, y: 280 },
  });
  const review1 = makeNode({
    type: 'human_review', label: '场景参考审定',
    position: { x: 690, y: 60 },
    config: {
      targetSourceNodeId: scene.id,
      targetArtifact: 'approved 场景视觉参考',
      instructions: '核对场景参考是否与剧本氛围、角色造型一致；不一致退回 Scene Agent 调整',
      reviewer: '制作人',
    },
  });
  const director = makeNode({
    type: 'agent', refId: 'director',
    position: { x: 950, y: 280 },
  });
  const skill = makeNode({
    type: 'skill', refId: 'full-story-relay-calibration', refVersion: '1.3.1',
    position: { x: 960, y: 540 },
  });
  const review2 = makeNode({
    type: 'human_review', label: '分镜与接缝抽检',
    position: { x: 1210, y: 60 },
    config: {
      targetSourceNodeId: director.id,
      targetArtifact: '分镜结构提案 + 逐镜视频（尾帧接力）',
      instructions: '按三档裁定（none/minor/major）抽检接缝；major 退回 Director 修复',
      reviewer: '制作人与导演',
    },
  });
  const assembly = makeNode({
    type: 'agent', refId: 'assembly',
    position: { x: 1230, y: 280 },
  });
  const end = makeNode({ type: 'boundary_end', position: { x: 1470, y: 300 } });

  const nodes: WorkflowNode[] = [
    start, script, resource, scene, review1, director, skill, review2, assembly, end,
  ];

  const exec = (source: WorkflowNode, target: WorkflowNode) => ({
    id: `e_${source.id}_${target.id}`,
    type: 'execution' as const,
    source: source.id,
    target: target.id,
    sourcePort: 'out' as const,
    targetPort: 'in' as const,
    contract: executionContract(
      source.type === 'agent' ? source.refId : null,
      target.type === 'agent' ? target.refId : null,
      source.type === 'human_review' || target.type === 'human_review',
      target.type === 'human_review' || source.type === 'human_review'
    ),
  });

  draft.nodes = nodes;
  draft.edges = [
    exec(start, script),
    exec(script, resource),
    exec(resource, scene),
    exec(scene, review1),
    exec(review1, director),
    exec(director, review2),
    exec(review2, assembly),
    exec(assembly, end),
    {
      id: 'e_bind_skill_dir',
      type: 'skill_binding',
      source: skill.id,
      target: director.id,
      sourcePort: 'bind',
      targetPort: 'bind',
      skillVersion: '1.3.1',
      contract: null,
    },
  ];
  return draft;
}
