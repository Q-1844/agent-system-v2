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
  | { kind: 'bind'; skillRefId: string; agentRefId: string }
  // —— 删除 / 解绑 / 断开 / 填内容（模块基本管理操作）——
  | { kind: 'removeAgent'; refId: string }
  | { kind: 'removeSkill'; refId: string }
  /** 删审核：无参=全部；afterRef/beforeRef 用于定位某两站之间的审核 */
  | { kind: 'removeReviews'; afterRef?: string; beforeRef?: string }
  | { kind: 'removeBoundary'; which: 'start' | 'end' }
  /** 解除 Skill 绑定；agentRefId 省略 = 解绑该 Skill 的全部绑定 */
  | { kind: 'unbind'; skillRefId: string; agentRefId?: string }
  /** 断开两个 Agent 之间的执行连线 */
  | { kind: 'disconnect'; fromRef: string; toRef: string }
  /** 给审核填内容（label/审核说明/审核者） */
  | { kind: 'fillReview'; afterRef?: string; label?: string; instructions?: string; reviewer?: string }
  /** 只读：汇报当前画布结构（不改动） */
  | { kind: 'outline' };

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

  // 多意图组合（如「删掉后重建」「插入并填内容」）：本地单层解析只会做一半、
  // 悄悄漏掉其余意图。整句交给后端 Producer 解析（离线时面板会如实说看不懂）。
  const REMOVE_SRC = '删掉|删除|去掉|移除|拿掉|下掉|撤掉|清除|解绑|断开|清空|清掉|重画|推倒|重来|重新开始|重新(搭|画|建)';
  const BUILD_SRC = '插入|新增|添加|加一个|加一道|加一台|串|挂上|配上|绑定|搭|生成|连起|链';
  const FILL_SRC = '说明写|写说明|审核者写|审核说明|审核者设|设置审核者';
  const clauseCount = compact.split(/[；。；,，!！？?\n]/).filter((c) => c.trim()).length;
  const intentKinds =
    (new RegExp(REMOVE_SRC).test(compact) ? 1 : 0) +
    (new RegExp(BUILD_SRC).test(compact) ? 1 : 0) +
    (new RegExp(FILL_SRC).test(compact) ? 1 : 0);
  const hasRemoveIntent = new RegExp(REMOVE_SRC).test(compact);
  if (hasRemoveIntent && (intentKinds >= 2)) {
    // 组合判定：解绑短语先挖掉（其中的「绑定」是名词不是搭建动词），
    // 若搭建/填内容动词出现在删除动词之后，无论有无标点都视为组合语义让位 Producer。
    const stripped = compact.replace(/解绑|解除.{0,10}?绑定|取消.{0,10}?绑定|去掉.{0,10}?绑定|移除.{0,10}?绑定/g, '＊');
    const firstRemove = stripped.search(new RegExp(REMOVE_SRC));
    const firstBuild = stripped.search(new RegExp(BUILD_SRC));
    const firstFill = stripped.search(new RegExp(FILL_SRC));
    const later = (i: number) => i >= 0 && (firstRemove < 0 || i > firstRemove);
    if (firstRemove >= 0 && (later(firstBuild) || later(firstFill))) return [];
    if (clauseCount >= 2 && intentKinds >= 2) return [];
  }

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

  // —— 查看当前流程（只读，放前面避免被添加/删除误吃）——
  if (/^(看看|查看|看一下|看下|说说|介绍).{0,8}(画布|流程|结构|工作流|现状)|^(当前|现在)(画布|流程|结构)|画布上(现在)?(有|是)什么|^总结(一下)?(画布|流程|工作流)?$|^现在有什么/.test(compact)) {
    return [{ kind: 'outline' }];
  }

  // —— 删除 / 解绑 / 断开（条款级解析，可与其它指令混排）——
  const REMOVE_VERB = /删掉|删除|去掉|移除|拿掉|下掉|撤掉|清除/;
  const UNBIND_VERB = /解绑|解除.{0,10}?绑定|取消.{0,10}?绑定|去掉.{0,10}?绑定|移除.{0,10}?绑定/;
  const DISCONNECT_VERB = /断开|取消连线|删除连线|移除连线|去掉连线/;
  if (REMOVE_VERB.test(compact) || UNBIND_VERB.test(compact) || DISCONNECT_VERB.test(compact)) {
    const actions: AssistantAction[] = [];
    const seen = new Set<string>();
    for (const clause of compact.split(/[；。；,，!！？?\n]/)) {
      if (!clause) continue;
      const hasRemove = REMOVE_VERB.test(clause);
      const hasUnbind = UNBIND_VERB.test(clause);
      const hasDisc = DISCONNECT_VERB.test(clause);
      if (!hasRemove && !hasUnbind && !hasDisc) continue;
      // 1) 解绑：Skill(+可选 Agent)
      if (hasUnbind || (hasRemove && /绑定/.test(clause))) {
        for (const s of extractSkillRefs(clause)) {
          const a = extractAgentRefs(clause)[0];
          const key = `unbind:${s}:${a ?? '*'}`;
          if (!seen.has(key)) { seen.add(key); actions.push({ kind: 'unbind', skillRefId: s, agentRefId: a }); }
        }
        if (extractSkillRefs(clause).length) continue;
      }
      // 2) 断开连线：A 与 B 之间
      if (hasDisc) {
        const refs = extractAgentRefs(clause);
        if (refs.length >= 2) {
          const key = `disc:${refs[0]}>${refs[1]}`;
          if (!seen.has(key)) { seen.add(key); actions.push({ kind: 'disconnect', fromRef: refs[0], toRef: refs[1] }); }
          continue;
        }
      }
      if (!hasRemove) continue;
      // 3) 删审核（含「之间」定位；无定位=全部）
      if (/(人工)?审核|人审/.test(clause)) {
        const betweenM = clause.match(/(.+?)(?:和|与|跟)(.+?)之间/);
        if (betweenM) {
          const aRefs = extractAgentRefs(betweenM[1]);
          const bRefs = extractAgentRefs(betweenM[2]);
          if (aRefs.length || bRefs.length) {
            const key = `rmrev:${aRefs[0] ?? ''}>${bRefs[0] ?? ''}`;
            if (!seen.has(key)) { seen.add(key); actions.push({ kind: 'removeReviews', afterRef: aRefs[0], beforeRef: bRefs[0] }); }
            continue;
          }
        }
        if (!seen.has('rmrev:all')) { seen.add('rmrev:all'); actions.push({ kind: 'removeReviews' }); }
        continue;
      }
      // 4) 删边界
      if (/开始/.test(clause)) { if (!seen.has('rmb:start')) { seen.add('rmb:start'); actions.push({ kind: 'removeBoundary', which: 'start' }); } continue; }
      if (/结束/.test(clause)) { if (!seen.has('rmb:end')) { seen.add('rmb:end'); actions.push({ kind: 'removeBoundary', which: 'end' }); } continue; }
      // 5) 删 Skill / 删 Agent
      const clauseSkills = extractSkillRefs(clause);
      if (clauseSkills.length) {
        for (const s of clauseSkills) {
          const key = `rmskill:${s}`;
          if (!seen.has(key)) { seen.add(key); actions.push({ kind: 'removeSkill', refId: s }); }
        }
        continue;
      }
      const clauseAgents = extractAgentRefs(clause);
      for (const a of clauseAgents) {
        const key = `rmagent:${a}`;
        if (!seen.has(key)) { seen.add(key); actions.push({ kind: 'removeAgent', refId: a }); }
      }
      // 「删掉所有/全部」且没有指明对象 → 清空画布
      if (!clauseSkills.length && !clauseAgents.length && !/审核|人审|开始|结束|绑定|连线/.test(clause) && /(所有|全部|都|画布|节点)/.test(clause)) {
        if (!seen.has('clear')) { seen.add('clear'); actions.push({ kind: 'clear' }); }
      }
    }
    if (actions.length) return actions;
  }

  // —— 给审核填内容（说明/审核者/名称）——
  // 语序宽容：「给审核写说明：X」「给导演后的审核的说明写成X」「给审核设置说明：X」
  const cleanFill = (s: string) => s.replace(/^[：:，,、\s「"']+/, '').replace(/[」"']+$/, '').trim();
  const fillM = compact.match(/给(.{0,12}?)的?审核(?:的)?(?:写|设置|填)?(说明|意见|备注|要求)(?:写成?|设置成?|[:：为])?(.+)/);
  if (fillM && fillM[3].trim()) {
    const aRefs = extractAgentRefs(fillM[1]);
    return [{ kind: 'fillReview', afterRef: aRefs[0], instructions: cleanFill(fillM[3]) }];
  }
  const fillReviewer = compact.match(/给(.{0,12}?)的?审核(?:的)?审?核者(?:写成?|设置成?|[:：为])?(.+)/);
  if (fillReviewer && /审核者/.test(compact) && fillReviewer[2].trim()) {
    const aRefs = extractAgentRefs(fillReviewer[1]);
    return [{ kind: 'fillReview', afterRef: aRefs[0], reviewer: cleanFill(fillReviewer[2]) }];
  }
  const fillName = compact.match(/(?:把|将)(.{0,12}?)的?审核(?:名称|名字|标题)?(?:改|设|换)成[:：]?(.+)/);
  if (fillName && fillName[2].trim() && /审核/.test(compact)) {
    const aRefs = extractAgentRefs(fillName[1]);
    return [{ kind: 'fillReview', afterRef: aRefs[0], label: cleanFill(fillName[2]) }];
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
  reviews?: Array<{ after: string; before?: string; label?: string; count?: number; instructions?: string; reviewer?: string }>;
  bindings?: Array<{ skill: string; agent: string }>;
  withBoundary?: boolean;
  /** 删除类操作（与上面的搭建操作可共存） */
  remove?: {
    /** 清空整个画布（仅当用户明确要求清空/推倒重来） */
    all?: boolean;
    agents?: string[];
    skills?: string[];
    /** 'all' = 删掉全部审核；或用 after/before 定位某两站之间的审核 */
    reviews?: 'all' | Array<{ after?: string; before?: string }>;
    boundaries?: Array<'start' | 'end'>;
    /** 解绑（不删节点）：agent 省略 = 解绑该 Skill 的全部绑定 */
    bindings?: Array<{ skill: string; agent?: string }>;
    /** 断开两条 Agent 之间的执行连线 */
    connections?: Array<{ from: string; to: string }>;
  };
  /** 给已有审核填内容（定位不到就落在第一道审核） */
  fill?: Array<{
    target: 'review';
    after?: string;
    label?: string;
    instructions?: string;
    reviewer?: string;
  }>;
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
    '  "reviews": [{"after": "director", "before": "assembly", "label": "分镜抽检", "count": 1, "instructions": "审核说明", "reviewer": "审核者"}],  // 在 after 与 before 之间插 count 道人工审核；before 可省略=追加在 after 之后；需要内容时带 instructions/reviewer；无审核给 []',
    '  "bindings": [{"skill": "full-story-relay-calibration", "agent": "director"}],  // 无绑定给 []',
    '  "withBoundary": false,  // 仅当用户明确要求首尾「开始/结束」边界时才为 true，否则保持 false',
    '  "remove": {  // 删除/解绑/断开类操作；用户没表达删除就整个省略',
    '    "agents": ["director"],',
    '    "skills": ["keyframes-bridge-repair"],',
    '    "reviews": "all",  // "all"=删全部审核；或 [{"after": "director", "before": "assembly"}] 只删这两站之间的',
    '    "boundaries": ["start"],',
    '    "bindings": [{"skill": "full-story-relay-calibration", "agent": "director"}],  // 解绑（节点保留）；agent 省略=解绑全部',
    '    "connections": [{"from": "script_writer", "to": "character_designer"}],  // 断开执行连线',
    '    "all": true  // 清空整个画布；仅当用户明确要求清空/推倒重来/重新开始时为 true，否则省略',
    '  },',
    '  "fill": [{"target": "review", "after": "director", "label": "分镜抽检", "instructions": "重点查运镜与角色一致性", "reviewer": "制作人"}],  // 给已有审核填内容；after=定位某 Agent 之后的审核，缺省=第一道；没填内容就省略',
    '  "note": "一句话说明你的理解"',
    '}',
    '注意：ref 只能取自上面的注册项清单；用户提到清单外的新职责时，不要发明新 ref——在 note 里说明该职责应归入哪个现有 Agent（如旁白/配音归 assembly）。',
    '当用户要求清空/重画/推倒重来时，用 remove.all=true 表达清空，不要逐个枚举删除各节点。',
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

/** 意图 → 动作（ref 校验失败收集到 problems，不静默丢弃）。
 * 执行顺序固定为：先删除/解绑/断开，再搭建（链/审核/绑定/边界），最后填内容——
 * 与口语「先把 X 删掉，再重新搭…」的自然语义一致，也避免「删全部审核」误伤本轮新插的审核。 */
export function intentToActions(intent: WorkflowIntent): { actions: AssistantAction[]; problems: string[] } {
  const problems: string[] = [];
  const removePhase: AssistantAction[] = [];
  const buildPhase: AssistantAction[] = [];
  const fillPhase: AssistantAction[] = [];

  // —— 1) 删除 / 解绑 / 断开 ——
  const rm = intent.remove;
  if (rm && typeof rm === 'object') {
    if (rm.all === true) {
      // 清空即全部删除：忽略同句里枚举的 agents/skills/reviews/... （全是冗余，
      // 且先枚举删除会让同句的 disconnect/解绑 全部空转、刷一排无意义的 ⚠）
      removePhase.push({ kind: 'clear' });
    } else {
      for (const ref of rm.agents ?? []) {
        if (AGENT_ID_SET.has(ref)) removePhase.push({ kind: 'removeAgent', refId: ref });
        else problems.push(`remove.agents 是未知 Agent ref：${ref}（已跳过）`);
      }
      for (const ref of rm.skills ?? []) {
        if (SKILL_ID_SET.has(ref)) removePhase.push({ kind: 'removeSkill', refId: ref });
        else problems.push(`remove.skills 是未知 Skill ref：${ref}（已跳过）`);
      }
      if (rm.reviews === 'all') {
        removePhase.push({ kind: 'removeReviews' });
      } else if (Array.isArray(rm.reviews)) {
        for (const r of rm.reviews) {
          const after = r?.after && AGENT_ID_SET.has(r.after) ? r.after : undefined;
          const before = r?.before && AGENT_ID_SET.has(r.before) ? r.before : undefined;
          if (after || before) removePhase.push({ kind: 'removeReviews', afterRef: after, beforeRef: before });
          else removePhase.push({ kind: 'removeReviews' });
        }
      }
      for (const b of rm.boundaries ?? []) {
        if (b === 'start' || b === 'end') removePhase.push({ kind: 'removeBoundary', which: b });
      }
      for (const b of rm.bindings ?? []) {
        if (!SKILL_ID_SET.has(b.skill)) {
          problems.push(`remove.bindings.skill 是未知 Skill ref：${b.skill}（已跳过）`);
          continue;
        }
        const agent = b.agent && AGENT_ID_SET.has(b.agent) ? b.agent : undefined;
        removePhase.push({ kind: 'unbind', skillRefId: b.skill, agentRefId: agent });
      }
      for (const c of rm.connections ?? []) {
        if (AGENT_ID_SET.has(c.from) && AGENT_ID_SET.has(c.to)) {
          removePhase.push({ kind: 'disconnect', fromRef: c.from, toRef: c.to });
        } else {
          problems.push(`remove.connections 有未知 ref：${c.from}/${c.to}（已跳过）`);
        }
      }
    }
  }

  // —— 2) 搭建（链 → 审核 → 绑定）——
  const chain = Array.isArray(intent.chain) ? intent.chain : [];
  for (const ref of chain) {
    if (!AGENT_ID_SET.has(ref)) problems.push(`chain 里有未知 Agent ref：${ref}（已跳过）`);
  }
  const validChain = chain.filter((r) => AGENT_ID_SET.has(r));
  if (validChain.length >= 2) {
    buildPhase.push({ kind: 'chain', refIds: validChain, withBoundary: !!intent.withBoundary });
  } else if (validChain.length === 1 && !intent.reviews?.length && !intent.bindings?.length) {
    buildPhase.push({ kind: 'addAgent', refId: validChain[0] });
  }
  for (const r of intent.reviews ?? []) {
    if (!AGENT_ID_SET.has(r.after)) {
      problems.push(`reviews.after 是未知 Agent ref：${r.after}（已跳过）`);
      continue;
    }
    if (r.before && !AGENT_ID_SET.has(r.before)) {
      problems.push(`reviews.before 是未知 Agent ref：${r.before}（按「追加在 after 之后」处理）`);
    }
    buildPhase.push({
      kind: 'insertReviews',
      afterRef: r.after,
      beforeRef: AGENT_ID_SET.has(r.before ?? '') ? r.before : undefined,
      count: Math.max(1, Math.min(5, Number(r.count) || 1)),
      label: r.label || '人工审核',
    });
    // 模型常把审核说明/审核者直接放在 review 项里：有就顺带填入（落在 after 之后的第一道）
    if (r.instructions?.trim() || r.reviewer?.trim()) {
      fillPhase.push({
        kind: 'fillReview',
        afterRef: r.after,
        instructions: r.instructions?.trim() || undefined,
        reviewer: r.reviewer?.trim() || undefined,
      });
    }
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
    buildPhase.push({ kind: 'bind', skillRefId: b.skill, agentRefId: b.agent });
  }
  if (intent.withBoundary && !(validChain.length >= 2)) {
    // chain 动作已自带 withBoundary；没有 chain 时（含纯边界意图「加上开始和结束」）在此补边界
    buildPhase.push({ kind: 'addBoundary', which: 'start' });
    buildPhase.push({ kind: 'addBoundary', which: 'end' });
  }

  // —— 3) 填内容 ——
  for (const f of intent.fill ?? []) {
    if (f?.target !== 'review') continue;
    const patch: { label?: string; instructions?: string; reviewer?: string } = {};
    if (f.label?.trim()) patch.label = f.label.trim();
    if (f.instructions?.trim()) patch.instructions = f.instructions.trim();
    if (f.reviewer?.trim()) patch.reviewer = f.reviewer.trim();
    if (!Object.keys(patch).length) continue;
    fillPhase.push({
      kind: 'fillReview',
      afterRef: f.after && AGENT_ID_SET.has(f.after) ? f.after : undefined,
      ...patch,
    });
  }

  return { actions: [...removePhase, ...buildPhase, ...fillPhase], problems };
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
  // —— 删除 / 解绑 / 断开 / 填内容 / 只读汇报 ——
  removeAgent(refId: string): ApplyResult;
  removeSkill(refId: string): ApplyResult;
  removeReviews(afterRef?: string, beforeRef?: string): ApplyResult & { removed: number };
  removeBoundary(which: 'start' | 'end'): ApplyResult;
  unbind(skillRefId: string, agentRefId?: string): ApplyResult & { removed: number };
  disconnect(fromRef: string, toRef: string): ApplyResult;
  fillReview(match: { afterRef?: string }, patch: { label?: string; instructions?: string; reviewer?: string }): ApplyResult;
  /** 只读：当前画布结构的一句话汇报（多行） */
  outline(): string;
  /** 画布快捷删除（节点 ✕ / 连线双击）由画布直接调用 */
  removeNodeById(nodeId: string): void;
  removeEdgeById(edgeId: string): void;
  /** 应用动作后让画布重新适配视野 */
  layoutRefit(): void;
}

/** 把动作应用到画布（控制器执行），并生成一条助手续播说明 */
export const HELP_TEXT = [
  '试试：「从剧本到成片搭一条标准链」「在导演和装配之间插两道人工审核」「给导演绑定接力校准」。',
  '也能改：「删掉两道审核」「解除导演的接力校准绑定」「断开编剧和角色资源的连线」「给审核写说明：重点查运镜」「看看现在画布」。',
  '我按你的描述直接修改画布草稿；只编辑草稿，不会触发生成。',
].join('\n');

// ============ 用户友好短标签（界面回复用，避免英文 Agent 名与长 Skill 名）============
const AGENT_SHORT: Record<string, string> = {
  producer: '总协调',
  script_writer: '编剧',
  character_designer: '角色资源',
  scene_designer: '场景',
  director: '导演',
  assembly: '装配',
};

export function shortAgentName(refId: string | null | undefined): string {
  if (!refId) return '审核';
  return AGENT_SHORT[refId] ?? getAgent(refId)?.role.split(' ·')[0] ?? refId;
}

export function shortSkillName(refId: string | null | undefined): string {
  if (refId === 'full-story-relay-calibration') return '接力校准';
  if (refId === 'keyframes-bridge-repair') return 'keyframes 接续桥';
  return getSkill(refId)?.name ?? String(refId);
}

/** 组合式解析（新）: 把动作列表转成一句话用户摘要（简短、口语、去术语） */
export function summarizeActions(actions: AssistantAction[]): string {
  const parts: string[] = [];
  for (const a of actions) {
    switch (a.kind) {
      case 'clear': parts.push('画布已清空'); break;
      case 'sample': parts.push('已载入示例工作流'); break;
      case 'check': break;
      case 'addAgent': parts.push(`已添加 ${shortAgentName(a.refId)}`); break;
      case 'addSkill': parts.push(`已添加「${shortSkillName(a.refId)}」`); break;
      case 'addReview': parts.push(`已添加人工审核${a.label && a.label !== '人工审核' ? `「${a.label}」` : ''}`); break;
      case 'addBoundary': parts.push(a.which === 'start' ? '已添加开始边界' : '已添加结束边界'); break;
      case 'chain': parts.push(a.refIds.length
        ? `已搭好链：${a.refIds.map(shortAgentName).join(' → ')}${a.withBoundary ? '（含开始/结束）' : ''}`
        : '已把画布上的节点按顺序连好'); break;
      case 'insertReviews': parts.push(a.beforeRef
        ? `已在${shortAgentName(a.afterRef)}和${shortAgentName(a.beforeRef)}之间插入 ${a.count} 道审核`
        : `已在${shortAgentName(a.afterRef)}之后插入 ${a.count} 道审核`); break;
      case 'bind': parts.push(`已把「${shortSkillName(a.skillRefId)}」绑给${shortAgentName(a.agentRefId)}`); break;
      case 'removeAgent': parts.push(`已删掉${shortAgentName(a.refId)}（相关连线一并移除）`); break;
      case 'removeSkill': parts.push(`已删掉「${shortSkillName(a.refId)}」`); break;
      case 'removeReviews': parts.push(a.afterRef || a.beforeRef
        ? `已删掉${a.afterRef ? `${shortAgentName(a.afterRef)}之后` : ''}${a.afterRef && a.beforeRef ? '和' : ''}${a.beforeRef ? `${shortAgentName(a.beforeRef)}之前` : ''}的审核`
        : '已删掉全部人工审核'); break;
      case 'removeBoundary': parts.push(a.which === 'start' ? '已删掉开始边界' : '已删掉结束边界'); break;
      case 'unbind': parts.push(`已解除「${shortSkillName(a.skillRefId)}」${a.agentRefId ? `和${shortAgentName(a.agentRefId)}` : '的全部'}绑定`); break;
      case 'disconnect': parts.push(`已断开${shortAgentName(a.fromRef)}和${shortAgentName(a.toRef)}的连线`); break;
      case 'fillReview': {
        const bits: string[] = [];
        if (a.label) bits.push(`名称「${a.label}」`);
        if (a.instructions) bits.push('审核说明');
        if (a.reviewer) bits.push(`审核者「${a.reviewer}」`);
        const where = a.afterRef ? `${shortAgentName(a.afterRef)}之后的` : '';
        parts.push(`已给${where}审核填入 ${bits.join('、') || '内容'}`);
        break;
      }
      case 'outline': break;
    }
  }
  return parts.join('；');
}
