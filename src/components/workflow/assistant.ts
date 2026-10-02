// 工作流搭建助手（第一版·前端）：
//  - 本地规则层：中文关键词解析常用搭建指令（链/加节点/插审核/绑定/清空/检查），即时可用
//  - 后端解析层：复杂自由表述交给后端 Producer（真实大模型，经既有 agentTurn 端点），
//    模型只输出「意图 JSON」，由本地用真实注册表物化为节点/连线并跑同一套校验
// 诚实边界：助手只编辑浏览器草稿，不触发任何执行；解析失败会如实说明，不假装理解。

import { WORKFLOW_AGENTS, WORKFLOW_SKILLS, getAgent, getSkill } from './catalog';
import type { ValidationIssue } from './types';
import type { WorkflowDraft } from './types';

// ============ 动作模型（解析结果 → 控制器执行）============
export type AssistantAction =
  | { kind: 'clear' }
  | { kind: 'sample' }
  | { kind: 'check' }
  | { kind: 'addAgent'; refId: string }
  | { kind: 'addSkill'; refId: string }
  | { kind: 'addReview'; label?: string }
  | { kind: 'addBoundary'; which: 'start' | 'end' }
  /** 按顺序串一条执行链；ref 不在画布则创建 */
  | { kind: 'chain'; refIds: string[]; withBoundary?: boolean }
  /** 在 after 与 before 之间插 count 道审核；before 省略 = 追加在 after 之后 */
  | { kind: 'insertReviews'; afterRef: string; beforeRef?: string; count: number; label?: string }
  /** Skill 绑定到 Agent（缺 Skill/Agent 节点则创建） */
  | { kind: 'bind'; skillRefId: string; agentRefId: string };

export interface AssistantReply {
  text: string;
  actions: AssistantAction[];
  /** usedBackend=true 表示这条回复由后端 Producer（大模型）解析 */
  usedBackend: boolean;
}

// ============ 关键词表（唯一事实源 = catalog，别名仅供解析）============
const AGENT_ALIASES: Array<{ re: RegExp; refId: string; label: string }> = [
  { re: /总协调|producer|总控/i, refId: 'producer', label: 'Producer' },
  { re: /剧本|编剧|script/i, refId: 'script_writer', label: 'Script Writer' },
  { re: /角色|资源|造型|道具|character|resource/i, refId: 'character_designer', label: 'Resource Agent' },
  { re: /场景|scene/i, refId: 'scene_designer', label: 'Scene Agent' },
  { re: /导演|分镜|director/i, refId: 'director', label: 'Director' },
  { re: /成片|装配|剪辑|assembly/i, refId: 'assembly', label: 'Assembly Agent' },
];

const SKILL_ALIASES: Array<{ re: RegExp; refId: string; label: string }> = [
  { re: /接力|校准|连续性|relay|calibration/i, refId: 'full-story-relay-calibration', label: '全片尾帧接力连续性校准' },
  { re: /桥|接续|keyframes|bridge|过渡/i, refId: 'keyframes-bridge-repair', label: 'keyframes 接续桥' },
];

const NUM_WORDS: Record<string, number> = { 一: 1, 1: 1, 两: 2, 二: 2, 2: 2, 三: 3, 3: 3, 四: 4, 4: 4, 五: 5, 5: 5 };

/** 按出现位置抽取文本里的 Agent 引用（保持语序、去重） */
export function extractAgentRefs(text: string): string[] {
  const hits: Array<{ refId: string; idx: number }> = [];
  for (const a of AGENT_ALIASES) {
    const m = text.match(new RegExp(a.re.source, 'gi'));
    if (m) {
      let from = 0;
      for (const one of m) {
        const idx = text.toLowerCase().indexOf(one.toLowerCase(), from);
        hits.push({ refId: a.refId, idx: idx < 0 ? 0 : idx });
        from = idx + one.length;
      }
    }
  }
  hits.sort((x, y) => x.idx - y.idx);
  const out: string[] = [];
  for (const h of hits) if (!out.includes(h.refId)) out.push(h.refId);
  return out;
}

export function extractSkillRefs(text: string): string[] {
  const out: string[] = [];
  for (const s of SKILL_ALIASES) {
    if (s.re.test(text) && !out.includes(s.refId)) out.push(s.refId);
  }
  return out;
}

function parseCount(text: string, fallback = 1): number {
  const m = text.match(/([一二两三四五1-5])\s*道/);
  if (m) return NUM_WORDS[m[1]] ?? fallback;
  const m2 = text.match(/(一|两|二|三|四|五)?\s*次/);
  if (m2 && m2[1]) return NUM_WORDS[m2[1]] ?? fallback;
  return fallback;
}

/** 本地规则解析。返回空数组 = 规则层看不懂（交后端层或求助回复） */
export function parseLocal(message: string, draft: WorkflowDraft): AssistantAction[] {
  const t = message.trim();
  if (!t) return [];
  const compact = t.replace(/\s+/g, '');

  // —— 画布级操作 ——
  if (/^(帮我)?(清空|清掉|重画|推倒重来|重新开始)/.test(compact) || /清空(画布|工作流)/.test(compact)) {
    return [{ kind: 'clear' }];
  }
  if (/(载入|加载|给我|来个?|看看).{0,6}(示例|例子|样例)|示例工作流/.test(compact)) {
    return [{ kind: 'sample' }];
  }
  if (/^(跑一?遍?)?(结构)?(检查|校验)/.test(compact) || /检查(一下)?(结构|画布|工作流)/.test(compact)) {
    return [{ kind: 'check' }];
  }

  // —— 组合式解析：一条消息可同时表达 链 + 审核 + 绑定（各意图条款级提取，互不截胡）——
  const actions: AssistantAction[] = [];
  const refs = extractAgentRefs(compact);

  // 1) 执行链
  const stdChain = /标准|全链|完整|整个|从头|从零|流水线/.test(compact) || /从剧本到成片/.test(compact);
  const chainWords = /串|连(起来|接)|链|流水线|流程|搭|建|生成|从.+到/.test(compact);
  if (stdChain) {
    actions.push({ kind: 'chain', refIds: ['script_writer', 'character_designer', 'scene_designer', 'director', 'assembly'], withBoundary: true });
  } else if (chainWords && refs.length >= 2) {
    actions.push({ kind: 'chain', refIds: refs, withBoundary: /开始|结束|边界/.test(compact) });
  } else if (/(串起|连起|串起来|连起来|连接)/.test(compact) && draft.nodes.length >= 2) {
    // 「把画布上的节点串起来」：按 x 坐标串起全部 Agent 与审核
    actions.push({ kind: 'chain', refIds: [] });
  }

  // 2) 插入 n 道审核（支持「在 A 和 B 之间」「在 A 之后」「追加」）
  const between = compact.match(/在(.+?)(?:和|与|跟)(.+?)之间[^。]*?插入?([^。]*?)?(审核|人审)/);
  if (between) {
    const aRefs = extractAgentRefs(between[1]);
    const bRefs = extractAgentRefs(between[2]);
    const count = parseCount(between[3] || between[1] + between[2], 1);
    if (aRefs.length) {
      actions.push({ kind: 'insertReviews', afterRef: aRefs[0], beforeRef: bRefs[0], count, label: '人工审核' });
    }
  } else {
    const after = compact.match(/在(.+?)(?:之后|后面|以后)[^。]*?(审核|人审)/);
    if (after) {
      const aRefs = extractAgentRefs(after[1]);
      if (aRefs.length) {
        actions.push({ kind: 'insertReviews', afterRef: aRefs[0], count: parseCount(compact, 1) });
      }
    } else if (/(插入|加|放|需要|来)(一|两|二|三|四|五|[1-5])?道?.{0,4}(人工审核|人审|审核)/.test(compact)) {
      const count = parseCount(compact, 1);
      for (let i = 0; i < count; i++) actions.push({ kind: 'addReview', label: count > 1 ? `人工审核 #${i + 1}` : '人工审核' });
    }
  }

  // 3) Skill 绑定（条款级：只取含绑定动词的子句里的 Agent，避免跨句误绑）
  const bindVerb = /绑定|挂上|配上|启用|使用/;
  for (const clause of compact.split(/[；。；,，!！？?\n]/)) {
    if (!bindVerb.test(clause)) continue;
    const clauseSkills = extractSkillRefs(clause);
    const clauseAgents = extractAgentRefs(clause);
    for (const s of clauseSkills) {
      const skill = getSkill(s);
      actions.push({ kind: 'bind', skillRefId: s, agentRefId: clauseAgents[0] ?? skill?.bindings[0]?.agentId ?? 'director' });
    }
  }

  // 4) 添加单个节点（须为「动词+量词」形式；仅在没有任何组合意图时兜底）
  if (!actions.length) {
    const skillRefs2 = extractSkillRefs(compact);
    const wantsAdd = /(添加|加一个|加个|加一道|加一台|放一个|放个|放一道|来一个|来个|来一台|新增)/.test(compact);
    if (skillRefs2.length && wantsAdd) {
      return skillRefs2.map((s) => ({ kind: 'addSkill', refId: s } as AssistantAction));
    }
    if (wantsAdd) {
      if (/开始/.test(compact)) actions.push({ kind: 'addBoundary', which: 'start' });
      if (/结束/.test(compact)) actions.push({ kind: 'addBoundary', which: 'end' });
      for (const r of refs) actions.push({ kind: 'addAgent', refId: r });
    }
  }

  return actions;
}

// ============ 后端 Producer 意图 JSON ============
export interface WorkflowIntent {
  chain?: string[];
  reviews?: Array<{ after: string; before?: string; label?: string; count?: number }>;
  bindings?: Array<{ skill: string; agent: string }>;
  withBoundary?: boolean;
  note?: string;
}

const AGENT_ID_SET = new Set(WORKFLOW_AGENTS.map((a) => a.id));
const SKILL_ID_SET = new Set(WORKFLOW_SKILLS.map((s) => s.id));

export function buildProducerPrompt(message: string, draft: WorkflowDraft): string {
  const existing = draft.nodes
    .map((n) => (n.type === 'agent' ? n.refId : n.type === 'skill' ? `skill:${n.refId}` : n.type === 'human_review' ? 'review' : n.type === 'boundary_start' ? 'start' : 'end'))
    .filter(Boolean)
    .join(', ');
  return [
    '你是「工作流搭建助手」的需求解析器。这是纯文本生成任务：不要调用任何工具、不要执行任何修改、不要声称已执行任何操作；理解需求后只输出一个 JSON 对象，不要输出 JSON 以外的任何文字。',
    '',
    '可用注册项（ref 只能取自这里）：',
    'Agent: producer(总协调), script_writer(剧本/编剧), character_designer(角色/资源/造型/道具), scene_designer(场景), director(导演/分镜), assembly(成片/装配/剪辑)',
    'Skill: full-story-relay-calibration(接力/校准/连续性), keyframes-bridge-repair(桥/keyframes/过渡)',
    '',
    '输出 schema：',
    '{',
    '  "chain": ["script_writer", "character_designer"],  // 按执行顺序的 Agent ref 数组；用户没表达链就给 []',
    '  "reviews": [{"after": "director", "before": "assembly", "label": "分镜抽检", "count": 1}],  // 在 after 与 before 之间插 count 道人工审核；before 可省略=追加在 after 之后；无审核给 []',
    '  "bindings": [{"skill": "full-story-relay-calibration", "agent": "director"}],  // 无绑定给 []',
    '  "withBoundary": false,  // 仅当用户明确要求首尾「开始/结束」边界时才为 true，否则保持 false',
    '  "note": "一句话说明你的理解"',
    '}',
    '注意：ref 只能取自上面的注册项清单；用户提到清单外的新职责时，不要发明新 ref——在 note 里说明该职责应归入哪个现有 Agent（如旁白/配音归 assembly）。',
    '',
    `当前画布已有节点概要：${existing || '（空）'}`,
    `用户需求：${message}`,
    '',
    '只输出 JSON。',
  ].join('\n');
}

/** 从模型回复中提取意图 JSON（取首个 { 到末个 } 的片段） */
export function extractIntentJson(text: string): WorkflowIntent | null {
  if (!text) return null;
  const s = text.indexOf('{');
  const e = text.lastIndexOf('}');
  if (s < 0 || e <= s) return null;
  try {
    const raw = JSON.parse(text.slice(s, e + 1));
    if (typeof raw !== 'object' || raw === null) return null;
    return raw as WorkflowIntent;
  } catch {
    return null;
  }
}

/** 意图 → 动作（ref 校验失败收集到 problems，不静默丢弃） */
export function intentToActions(intent: WorkflowIntent): { actions: AssistantAction[]; problems: string[] } {
  const actions: AssistantAction[] = [];
  const problems: string[] = [];
  const chain = Array.isArray(intent.chain) ? intent.chain : [];
  for (const ref of chain) {
    if (!AGENT_ID_SET.has(ref)) problems.push(`chain 里有未知 Agent ref：${ref}（已跳过）`);
  }
  const validChain = chain.filter((r) => AGENT_ID_SET.has(r));
  if (validChain.length >= 2) {
    actions.push({ kind: 'chain', refIds: validChain, withBoundary: !!intent.withBoundary });
  } else if (validChain.length === 1 && !intent.reviews?.length && !intent.bindings?.length) {
    actions.push({ kind: 'addAgent', refId: validChain[0] });
  }
  for (const r of intent.reviews ?? []) {
    if (!AGENT_ID_SET.has(r.after)) {
      problems.push(`reviews.after 是未知 Agent ref：${r.after}（已跳过）`);
      continue;
    }
    if (r.before && !AGENT_ID_SET.has(r.before)) {
      problems.push(`reviews.before 是未知 Agent ref：${r.before}（按「追加在 after 之后」处理）`);
    }
    actions.push({
      kind: 'insertReviews',
      afterRef: r.after,
      beforeRef: AGENT_ID_SET.has(r.before ?? '') ? r.before : undefined,
      count: Math.max(1, Math.min(5, Number(r.count) || 1)),
      label: r.label || '人工审核',
    });
  }
  for (const b of intent.bindings ?? []) {
    if (!SKILL_ID_SET.has(b.skill)) {
      problems.push(`bindings.skill 是未知 Skill ref：${b.skill}（已跳过）`);
      continue;
    }
    if (!AGENT_ID_SET.has(b.agent)) {
      problems.push(`bindings.agent 是未知 Agent ref：${b.agent}（已跳过）`);
      continue;
    }
    actions.push({ kind: 'bind', skillRefId: b.skill, agentRefId: b.agent });
  }
  if (intent.withBoundary && (validChain.length >= 2 || actions.length)) {
    // chain 动作已自带 withBoundary；这里只在没有 chain 时补边界
    if (!(validChain.length >= 2)) {
      actions.push({ kind: 'addBoundary', which: 'start' });
      actions.push({ kind: 'addBoundary', which: 'end' });
    }
  }
  return { actions, problems };
}

// ============ 控制器接口（WorkflowStudio 实现，助手只依赖此接口）============
export interface ApplyResult {
  ok: boolean;
  reason?: string;
  detail?: string;
}

export interface WorkflowAssistantController {
  clearAll(): void;
  loadSample(): void;
  runCheck(): { errors: number; warnings: number; issues: ValidationIssue[] };
  addAgent(refId: string): ApplyResult & { nodeId?: string };
  addSkill(refId: string): ApplyResult & { nodeId?: string };
  addReview(label?: string): ApplyResult & { nodeId?: string };
  addBoundary(which: 'start' | 'end'): ApplyResult;
  /** 空数组 = 按画布现状把 Agent/审核按 x 序连成执行链 */
  chain(refIds: string[], withBoundary: boolean): ApplyResult & { created: number; connected: number };
  insertReviews(afterRef: string, beforeRef: string | undefined, count: number, label?: string): ApplyResult & { nodeId?: string };
  bind(skillRefId: string, agentRefId: string): ApplyResult;
  /** 应用动作后让画布重新适配视野 */
  layoutRefit(): void;
}

/** 把动作应用到画布（控制器执行），并生成一条助手续播说明 */
export function describePlan(actions: AssistantAction[]): string {
  const parts: string[] = [];
  for (const a of actions) {
    switch (a.kind) {
      case 'clear': parts.push('清空画布'); break;
      case 'sample': parts.push('载入示例工作流'); break;
      case 'check': parts.push('运行结构检查'); break;
      case 'addAgent': parts.push(`添加 Agent「${getAgent(a.refId)?.name ?? a.refId}」`); break;
      case 'addSkill': parts.push(`添加 Skill「${getSkill(a.refId)?.name ?? a.refId}」`); break;
      case 'addReview': parts.push(`添加人工审核${a.label ? `「${a.label}」` : ''}`); break;
      case 'addBoundary': parts.push(a.which === 'start' ? '添加「开始」边界' : '添加「结束」边界'); break;
      case 'chain': parts.push(a.refIds.length
        ? `串接执行链：${a.refIds.map((r) => getAgent(r)?.name ?? r).join(' → ')}`
        : '把画布上现有 Agent/审核按位置串成执行链'); break;
      case 'insertReviews': parts.push(`在「${getAgent(a.afterRef)?.name ?? a.afterRef}」${a.beforeRef ? `与「${getAgent(a.beforeRef)?.name ?? a.beforeRef}」之间` : '之后'}插入 ${a.count} 道审核`); break;
      case 'bind': parts.push(`把 Skill「${getSkill(a.skillRefId)?.name ?? a.skillRefId}」绑定给「${getAgent(a.agentRefId)?.name ?? a.agentRefId}」`); break;
    }
  }
  return parts.join('；');
}

export const HELP_TEXT = [
  '我可以按你的描述搭建工作流草稿。试试：',
  '· 「搭一条从剧本到成片的标准链」',
  '· 「在导演和成片之间插两道审核」',
  '· 「给导演绑定接力校准」',
  '· 「加一个场景 Agent」「串起画布上的节点」「清空画布」「检查结构」',
  '简单指令由本地规则即时完成；复杂描述会转给后端 Producer（大模型）解析成搭建意图，再由本地用真实注册表落图。所有操作只编辑浏览器草稿，不会触发任何执行。',
].join('\n');
