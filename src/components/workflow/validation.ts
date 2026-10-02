// 前端结构校验：连线规则（连接时给具体理由）+ 保存/导出前的结构检查（定位到节点/连线）。
// 第一版执行关系为「无环、顺序可读」的简单图；不把驳回返工伪装成可运行回环。

import type {
  ValidationIssue,
  ValidationResult,
  WorkflowDraft,
  WorkflowEdge,
  WorkflowNode,
} from './types';
import { getAgent, getSkill } from './catalog';

/** 连线尝试的校验结果 */
export interface ConnCheck {
  ok: boolean;
  reason?: string;
}

function nodeById(draft: WorkflowDraft, id: string): WorkflowNode | undefined {
  return draft.nodes.find((n) => n.id === id);
}

function nodeTitle(n: WorkflowNode | undefined): string {
  if (!n) return '未知节点';
  if (n.label) return n.label;
  if (n.type === 'agent') return getAgent(n.refId)?.name ?? String(n.refId);
  if (n.type === 'skill') return getSkill(n.refId)?.name ?? String(n.refId);
  if (n.type === 'human_review') return '人工审核';
  if (n.type === 'boundary_start') return '开始';
  return '结束';
}

/** 执行依赖线允许的 (source.type → target.type) 组合 */
const EXEC_PAIRS: Record<string, Set<string>> = {
  boundary_start: new Set(['agent']),
  agent: new Set(['agent', 'human_review', 'boundary_end']),
  human_review: new Set(['agent', 'human_review', 'boundary_end']),
};

/**
 * 校验一次连线尝试（新增或重连）。给出具体的拒绝理由。
 * 端口约定：agent/review/start 的 out|in 是执行端口；skill 的 bind 出口与
 * agent 的 bind 入口是 Skill 绑定端口（颜色/线型与执行依赖区分）。
 */
export function connectionRule(
  draft: WorkflowDraft,
  sourceId: string,
  targetId: string,
  sourceHandle = 'out',
  targetHandle = 'in'
): ConnCheck {
  if (sourceId === targetId) {
    return { ok: false, reason: '不能连接到节点自身' };
  }
  const src = nodeById(draft, sourceId);
  const dst = nodeById(draft, targetId);
  if (!src || !dst) return { ok: false, reason: '节点不存在（草稿数据异常）' };

  const srcName = nodeTitle(src);
  const dstName = nodeTitle(dst);

  // —— 端口级校验 ——
  const srcPortOk =
    (src.type === 'skill' && sourceHandle === 'bind') ||
    (src.type !== 'skill' && sourceHandle === 'out');
  const dstPortOk =
    (dst.type === 'agent' && (targetHandle === 'in' || targetHandle === 'bind')) ||
    ((dst.type === 'human_review' || dst.type === 'boundary_end') && targetHandle === 'in');
  if (!srcPortOk || !dstPortOk) {
    if (src.type === 'skill' && sourceHandle !== 'bind') {
      return { ok: false, reason: 'Skill 节点只有「绑定」出口（Skill 不参与执行依赖）' };
    }
    return { ok: false, reason: `端口不匹配：无法从「${srcName}」的 ${sourceHandle} 连到「${dstName}」的 ${targetHandle}` };
  }

  // —— Skill 绑定线：Skill → Agent ——
  if (src.type === 'skill') {
    if (dst.type !== 'agent') {
      return {
        ok: false,
        reason: `Skill 绑定线只能连到 Agent（「${dstName}」是${typeName(dst.type)}）。Skill 是绑定给 Agent 使用的规则，不是会自行运行的任务`,
      };
    }
    // 同一 Skill 节点重复绑定同一 Agent
    const dup = draft.edges.some(
      (e) =>
        e.type === 'skill_binding' && e.source === sourceId && e.target === targetId
    );
    if (dup) {
      return { ok: false, reason: `「${getSkill(src.refId)?.name ?? src.refId}」已绑定到「${dstName}」` };
    }
    const agent = getAgent(dst.refId);
    if (agent && !agent.skillBindable) {
      return {
        ok: false,
        reason: `后端 Skill 系统不支持绑定「${agent.name}」（skill_system/models.py VALID_AGENT_IDS 不含 ${agent.id}），待后端定义`,
      };
    }
    return { ok: true };
  }

  // —— 目标是 Skill：任何入线都不允许 ——
  if (dst.type === 'skill') {
    if (src.type === 'agent') {
      return {
        ok: false,
        reason: `Skill 不接收连线。要绑定 Skill，请从 Skill 节点的「绑定」端口连到「${srcName}」`,
      };
    }
    return { ok: false, reason: `Skill 节点不接收任何连线（绑定方向为 Skill → Agent）` };
  }

  // —— 执行依赖线 ——
  // bind 端口只服务 Skill 绑定线；执行线一律走 out → in
  if (targetHandle === 'bind') {
    return {
      ok: false,
      reason: `「bind」端口是 Skill 绑定入口：应从 Skill 节点的绑定出口连入（执行依赖请接「执行输入」端口）`,
    };
  }
  const allowedTargets = EXEC_PAIRS[src.type];
  if (!allowedTargets) {
    return { ok: false, reason: `「${srcName}」不能作为执行依赖的起点` };
  }
  if (!allowedTargets.has(dst.type)) {
    if (src.type === 'boundary_start') {
      return { ok: false, reason: `开始（边界）只能连到 Agent（收到的是「${dstName}」）` };
    }
    return {
      ok: false,
      reason: `执行依赖不允许「${typeName(src.type)} → ${typeName(dst.type)}」（「${srcName}」→「${dstName}」）`,
    };
  }

  // 重复同向边
  const dup = draft.edges.some(
    (e) => e.type === 'execution' && e.source === sourceId && e.target === targetId
  );
  if (dup) return { ok: false, reason: `这条执行依赖已存在（「${srcName}」→「${dstName}」）` };

  // 环检测：从 target 沿执行依赖能否回到 source
  const cycle = findCyclePath(draft.edges.filter((e) => e.type === 'execution'), targetId, sourceId);
  if (cycle) {
    const names = cycle
      .map((id) => nodeTitle(nodeById(draft, id)))
      .concat([srcName])
      .join(' → ');
    return { ok: false, reason: `执行依赖不能成环：${names}（第一版为无环顺序图，驳回返工不画回环线）` };
  }

  return { ok: true };
}

function typeName(t: WorkflowNode['type']): string {
  switch (t) {
    case 'agent': return 'Agent';
    case 'skill': return 'Skill';
    case 'human_review': return '人工审核';
    case 'boundary_start': return '开始（边界）';
    case 'boundary_end': return '结束（边界）';
  }
}

/** 从 from 沿执行边做 DFS，找一条到 to 的路径（含端点，不含 to 本身时返回 null） */
function findCyclePath(
  execEdges: Pick<WorkflowEdge, 'source' | 'target'>[],
  from: string,
  to: string
): string[] | null {
  const adj = new Map<string, string[]>();
  for (const e of execEdges) {
    if (!adj.has(e.source)) adj.set(e.source, []);
    adj.get(e.source)!.push(e.target);
  }
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const path: string[] = [];

  const dfs = (cur: string): string[] | null => {
    if (cur === to) return [...path];
    if (visited.has(cur)) return null;
    if (visiting.has(cur)) return null;
    visiting.add(cur);
    path.push(cur);
    for (const next of adj.get(cur) ?? []) {
      const found = dfs(next);
      if (found) return found;
    }
    path.pop();
    visiting.delete(cur);
    visited.add(cur);
    return null;
  };

  return dfs(from);
}

/**
 * 保存/导出前的结构检查。错误定位到对应节点或连线。
 */
export function validateDraft(draft: WorkflowDraft): ValidationResult {
  const issues: ValidationIssue[] = [];
  const execEdges = draft.edges.filter((e) => e.type === 'execution');
  const bindEdges = draft.edges.filter((e) => e.type === 'skill_binding');

  const inExec = new Map<string, number>();
  const outExec = new Map<string, number>();
  for (const e of execEdges) {
    inExec.set(e.target, (inExec.get(e.target) ?? 0) + 1);
    outExec.set(e.source, (outExec.get(e.source) ?? 0) + 1);
  }

  // —— 引用有效性 ——
  for (const n of draft.nodes) {
    if (n.type === 'agent') {
      const a = getAgent(n.refId);
      if (!a) {
        issues.push({
          level: 'error', code: 'REF_MISSING',
          message: `Agent 节点引用失效：${n.refId ?? '(空)'} 不在当前注册表中`,
          nodeId: n.id,
        });
      }
    } else if (n.type === 'skill') {
      const s = getSkill(n.refId);
      if (!s) {
        issues.push({
          level: 'error', code: 'REF_MISSING',
          message: `Skill 节点引用失效：${n.refId ?? '(空)'} 不在正式 Skill 目录中`,
          nodeId: n.id,
        });
      } else if (s.status !== 'active') {
        issues.push({
          level: 'error', code: 'SKILL_INACTIVE',
          message: `Skill「${s.name}」状态为 ${s.status}，不可使用`,
          nodeId: n.id,
        });
      }
    }
  }

  // —— 边端点存在性 ——
  for (const e of draft.edges) {
    if (!nodeById(draft, e.source) || !nodeById(draft, e.target)) {
      issues.push({
        level: 'error', code: 'EDGE_DANGLING',
        message: '连线存在悬空端点（草稿数据异常，请删除该连线）',
        edgeId: e.id,
      });
    }
  }

  // —— 执行线的端口约定（草稿导入可能绕过连线校验）——
  for (const e of execEdges) {
    if (e.sourcePort === 'bind' || e.targetPort === 'bind') {
      issues.push({
        level: 'error', code: 'EXEC_PORT_MISMATCH',
        message: '执行依赖线不能使用 bind 端口（bind 仅用于 Skill 绑定线）',
        edgeId: e.id,
      });
    }
  }

  // —— Skill 绑定线的目标合法性（草稿导入可能绕过连线校验）——
  for (const e of bindEdges) {
    const src = nodeById(draft, e.source);
    const dst = nodeById(draft, e.target);
    if (!src || !dst) continue;
    if (src.type !== 'skill' || dst.type !== 'agent') {
      issues.push({
        level: 'error', code: 'BIND_WRONG_TARGET',
        message: `Skill 绑定线必须是 Skill → Agent（当前为 ${typeName(src.type)} → ${typeName(dst.type)}）`,
        edgeId: e.id,
      });
      continue;
    }
    const agent = getAgent(dst.refId);
    if (agent && !agent.skillBindable) {
      issues.push({
        level: 'error', code: 'BIND_AGENT_UNSUPPORTED',
        message: `后端 Skill 系统不支持绑定「${agent.name}」（VALID_AGENT_IDS 不含 ${agent.id}），待后端定义`,
        edgeId: e.id,
      });
    }
    if (!e.skillVersion) {
      issues.push({
        level: 'error', code: 'BIND_NO_VERSION',
        message: 'Skill 绑定线缺少版本（应钉住 skill.json 的 version）',
        edgeId: e.id,
      });
    }
  }

  // —— 环检测（执行子图）——
  const cycle = findAnyCycle(execEdges);
  if (cycle) {
    const names = cycle.map((id) => nodeTitle(nodeById(draft, id))).join(' → ');
    issues.push({
      level: 'error', code: 'EXEC_CYCLE',
      message: `执行依赖成环：${names}。第一版为无环顺序图，驳回返工请用人工审核的「驳回即停止」语义表达`,
      nodeId: cycle[0],
    });
  }

  // —— 审核节点必须有上游产物 ——
  for (const n of draft.nodes) {
    if (n.type !== 'human_review') continue;
    const upstream = execEdges.filter((e) => e.target === n.id);
    if (upstream.length === 0) {
      issues.push({
        level: 'error', code: 'REVIEW_NO_UPSTREAM',
        message: `审核节点「${nodeTitle(n)}」没有上游产物：请从 Agent 或上一道审核连一条执行依赖进来`,
        nodeId: n.id,
      });
    }
  }

  // —— 执行节点孤立 ——
  for (const n of draft.nodes) {
    if (n.type !== 'agent' && n.type !== 'human_review') continue;
    const deg =
      (inExec.get(n.id) ?? 0) + (outExec.get(n.id) ?? 0);
    if (deg === 0) {
      issues.push({
        level: 'error', code: 'EXEC_ISOLATED',
        message:
          n.type === 'agent'
            ? `Agent「${nodeTitle(n)}」完全孤立：未接入任何执行依赖`
            : `审核节点「${nodeTitle(n)}」完全孤立：未接入任何执行依赖`,
        nodeId: n.id,
      });
    }
  }

  // —— 开始/结束边界 ——
  const starts = draft.nodes.filter((n) => n.type === 'boundary_start');
  const ends = draft.nodes.filter((n) => n.type === 'boundary_end');
  if (starts.length > 1) {
    issues.push({
      level: 'error', code: 'START_DUP',
      message: `开始（边界）最多一个，当前 ${starts.length} 个`,
      nodeId: starts[1].id,
    });
  }
  if (ends.length > 1) {
    issues.push({
      level: 'error', code: 'END_DUP',
      message: `结束（边界）最多一个，当前 ${ends.length} 个`,
      nodeId: ends[1].id,
    });
  }
  for (const s of starts) {
    if (!outExec.get(s.id)) {
      issues.push({
        level: 'warning', code: 'START_NO_OUT',
        message: '开始（边界）未连到任何 Agent',
        nodeId: s.id,
      });
    }
  }
  for (const s of ends) {
    if (!inExec.get(s.id)) {
      issues.push({
        level: 'warning', code: 'END_NO_IN',
        message: '结束（边界）没有任何上游连线',
        nodeId: s.id,
      });
    }
  }

  // —— Skill 未绑定（仅提示）——
  for (const n of draft.nodes) {
    if (n.type !== 'skill') continue;
    const bound = bindEdges.some((e) => e.source === n.id);
    if (!bound) {
      issues.push({
        level: 'warning', code: 'SKILL_UNBOUND',
        message: `Skill「${nodeTitle(n)}」未绑定到任何 Agent（Skill 需经绑定线连到 Agent 才会被使用）`,
        nodeId: n.id,
      });
    }
  }

  const errors = issues.filter((i) => i.level === 'error').length;
  const warnings = issues.length - errors;
  return { ranAt: new Date().toISOString(), errors, warnings, issues };
}

/** 在执行子图上找任意环（返回环节点序列） */
function findAnyCycle(
  execEdges: Pick<WorkflowEdge, 'source' | 'target'>[]
): string[] | null {
  const adj = new Map<string, string[]>();
  for (const e of execEdges) {
    if (!adj.has(e.source)) adj.set(e.source, []);
    adj.get(e.source)!.push(e.target);
  }
  const color = new Map<string, 1 | 2>(); // 1=visiting 2=done
  const stack: string[] = [];

  const dfs = (cur: string): string[] | null => {
    color.set(cur, 1);
    stack.push(cur);
    for (const next of adj.get(cur) ?? []) {
      const c = color.get(next);
      if (c === 1) {
        const idx = stack.indexOf(next);
        return stack.slice(idx);
      }
      if (!c) {
        const found = dfs(next);
        if (found) return found;
      }
    }
    stack.pop();
    color.set(cur, 2);
    return null;
  };

  for (const id of adj.keys()) {
    if (!color.get(id)) {
      const found = dfs(id);
      if (found) return found;
    }
  }
  return null;
}
