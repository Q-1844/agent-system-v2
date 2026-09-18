# src/agents/inquiry.py
# AI-7A Team Inquiry Runtime + Specialist Read Intelligence（§19~§21 / §33~§36 / §44~§47 / §50 / §52）。
#
# Producer 的询问运行时：分类请求 → 用 ProjectIntelligenceService 按需取有界上下文
# （不把整项目塞进 prompt，§5）→ 必要时经现有 delegate/AgentTask 机制咨询 Specialist
# （结构化 ConsultationResult）→ 综合出只读答案或 analysis-only 计划。
#
# 设计不变量：
#   - 纯读：只经 ProjectIntelligenceService 读 + Specialist 咨询（inspect cap + LLM chat）+
#     Producer LLM 综合。绝不 dispatch mutation 能力（§43/§96 硬闸：consult_specialist
#     对 capability_id 调 assert_read_only_capability）。
#   - 不造专业内容：Producer 只做 组合/排序/依赖/协调/沟通，不替 Specialist 重写专业意见（§87）。
#   - 无限循环免疫：max 2 轮咨询，无 free-form agent 辩论（§35/§36）；Specialist→Specialist
#     禁止（can_delegate 结构性阻止，runtime.py:281）。
#   - 复用不重写：AgentTask + AgentTaskStore（§32，不建第二套 task 引擎）；can_execute 五重校验；
#     _parse_json_object；AgnesClient.chat(json_mode)。
#
# 7A-2 范围：询问/咨询/综合引擎。7A-4 把变更路径包进持久化 ChangeAnalysisSession +
# UnifiedChangePlanDraft（execution_ready=False）。本模块的 run_inquiry 变更路径只产出
# analysis *内容*（execution_ready=False、0 ChangeOperation），不持久化为 session。
from __future__ import annotations
import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.models import Project
from src.ai.story_workflow import _parse_json_object
from .project_intelligence import (ProjectIntelligenceService, ProjectEvidenceRef,
                                    assert_read_only_capability)
from .models import AgentOp, ResultStatus, TaskStatus, AgentTask
from .runtime import AgentRuntime, AgentError


# ============ 模块→owner / inspect-cap / view 映射（确定性归属，§33）============
MODULE_OWNER: Dict[str, str] = {
    "script": "script_writer", "character": "character_designer",
    "storyboard": "director", "production_plan": "production_planner",
    "production": "shot_producer",
}
# LLM 常把 module 名写成复数/别名（project 字段名 characters）→ 归一为 MODULE_OWNER key
_MODULE_NORMALIZE: Dict[str, str] = {
    "characters": "character", "chars": "character", "character_design": "character",
    "scripts": "script", "story": "script",
    "storyboards": "storyboard", "scenes": "storyboard", "shots": "storyboard",
    "production_plans": "production_plan", "plans": "production_plan",
}


def normalize_module(mod: str) -> str:
    """LLM 产出的 module 名归一（characters→character 等）。

    AI-7C-H H0 正式化：LLM 综合分析/咨询常把 module 写成 project 字段名（characters）或
    复数（scripts/storyboards/plans）。所有 module 名消费点（_normalize_analysis、
    ReadOnlyPlanValidator、change_protocol 侧）统一经此归一，再做 MODULE_OWNER 查表，
    避免 UNKNOWN_MODULE / owner 解析失败等伪错误。"""
    return _MODULE_NORMALIZE.get((mod or "").strip().lower(), (mod or "").strip())


_AGENT_INSPECT_CAP: Dict[str, str] = {
    "script_writer": "story.inspect", "character_designer": "character.inspect",
    "director": "storyboard.inspect", "production_planner": "production_plan.inspect",
    "shot_producer": "shot.inspect", "continuity_reviewer": "shot.inspect",
    "editor": "composition.inspect",
}
_AGENT_VIEW: Dict[str, str] = {
    "script_writer": "script", "character_designer": "characters",
    "director": "storyboard", "production_planner": "production",
    "shot_producer": "production", "continuity_reviewer": "storyboard",
    "editor": "storyboard",
}
# 变更动词（§33：决定 question vs change）。H3：补「让…更…」「变得」「加强/增强/弱化」等
# 程度型变更意图（Probe 1「让这一段更紧张一点」），否则被误判为只读提问、进不了变更流。
_CHANGE_VERBS = re.compile(
    r"改|修改|换成|变成|变得|变为|删除|删掉|去掉|调整|替换|重写|续写|"
    r"换回|恢复到|恢复成|"
    r"让.{1,20}更|加(强|快|深)|增强|弱化|放大|缩小|"
    r"make\b|change\b|remove\b|turn\b", re.IGNORECASE)
_MODULE_KEYWORDS: List[Tuple[str, re.Pattern]] = [
    ("production_plan", re.compile(r"制作计划|production\s*plan|制作策略", re.IGNORECASE)),
    # AI-7C-H H3：外观变更词补强——「把 X 改成银色短发、黑色战斗装甲」类请求此前命中
    # 不了 character（“短发/装甲/造型”不在词表），被误路由到 script（“剧情”等词旁系命中），
    # 导致 update_character 缺失、char-sync/K1 链路断裂。注意语义边界（回归锚定）：
    # 仅**名字出现**不加 character（「让 Aran 离开城市」是剧本变更）；只有命中
    # 外观/造型语义词才路由 character_designer。
    ("character", re.compile(
        r"角色|角色设定|外貌|外观|造型|头发|发型|发色|短发|长发|发式|"
        r"服装|衣服|衣着|穿着|装束|战衣|制服|盔甲|装甲|铠甲|披风|"
        r"道具|资源准备|生产资源|"
        r"hair|appearance|outfit|armor\b|costume|look\b|character\b|prop\b", re.IGNORECASE)),
    ("script", re.compile(r"剧本|故事|结局|ending|剧情|台词|script", re.IGNORECASE)),
    ("storyboard", re.compile(r"分镜|场景|节奏|storyboard|镜头节奏", re.IGNORECASE)),
    ("production", re.compile(r"镜头生成|视频生成|首帧|尾帧|video|first\s*frame|生成视频", re.IGNORECASE)),
]


# ============ ContextBudget / ProjectContextPack（§19/§21）============
@dataclass
class ContextBudget:
    """上下文预算（§21：数字为例，非神圣常量）。超预算 → 截断 + truncated_sections 标记。"""
    overview_chars: int = 8_000
    module_chars: int = 20_000
    history_chars: int = 12_000
    total_chars: int = 48_000


@dataclass
class ProjectContextPack:
    """一次 Agent run 所需的最小有界上下文（§19）。只含当前 Agent 需要的内容。
    TeamThread（用户所见）≠ ProjectContextPack（Agent 单次 run 所见），§25。"""
    project_name: str
    request: str
    snapshot_signature: Optional[str] = None
    current_scope: List[ProjectEvidenceRef] = field(default_factory=list)
    project_overview: Optional[dict] = None
    module_context: Dict[str, Any] = field(default_factory=dict)
    dependency_facts: List[dict] = field(default_factory=list)
    prior_summary_refs: List[str] = field(default_factory=list)
    evidence_refs: List[ProjectEvidenceRef] = field(default_factory=list)
    truncated_sections: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name, "request": self.request,
            "snapshot_signature": self.snapshot_signature,
            "current_scope": [r.to_dict() for r in self.current_scope],
            "project_overview": self.project_overview,
            "module_context": dict(self.module_context),
            "dependency_facts": list(self.dependency_facts),
            "prior_summary_refs": list(self.prior_summary_refs),
            "evidence_refs": [r.to_dict() for r in self.evidence_refs],
            "truncated_sections": list(self.truncated_sections),
        }


def _truncate_section(obj: Any, max_chars: int) -> Tuple[Any, bool]:
    """超 char 预算 → 保留预览 + 截断标记（§21：不静默丢信息）。"""
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if len(s) <= max_chars:
        return obj, False
    return {"_truncated": True, "preview": s[:max_chars]}, True


def _enforce_budget(pack: ProjectContextPack, budget: ContextBudget) -> None:
    """按预算截断各段，记录 truncated_sections（§21）。"""
    if pack.project_overview is not None:
        v, t = _truncate_section(pack.project_overview, budget.overview_chars)
        pack.project_overview = v
        if t:
            pack.truncated_sections.append("project_overview")
    for k, v in list(pack.module_context.items()):
        v2, t = _truncate_section(v, budget.module_chars)
        pack.module_context[k] = v2
        if t:
            pack.truncated_sections.append(f"module_context:{k}")
    if pack.dependency_facts:
        v, t = _truncate_section(pack.dependency_facts, budget.history_chars)
        pack.dependency_facts = v
        if t:
            pack.truncated_sections.append("dependency_facts")


def _pack_text(pack: ProjectContextPack) -> str:
    """把 pack 渲染为 LLM 可读文本。"""
    parts = [f"# 项目：{pack.project_name}",
             f"快照签名：{pack.snapshot_signature or 'n/a'}",
             f"用户请求：{pack.request}"]
    if pack.project_overview is not None:
        parts.append(f"## 项目概览\n{json.dumps(pack.project_overview, ensure_ascii=False, default=str)}")
    if pack.module_context:
        parts.append("## 相关模块上下文")
        for k, v in pack.module_context.items():
            parts.append(f"### {k}\n{json.dumps(v, ensure_ascii=False, default=str)}")
    if pack.dependency_facts:
        parts.append(f"## 依赖事实（确定性，代码计算）\n{json.dumps(pack.dependency_facts, ensure_ascii=False, default=str)}")
    if pack.truncated_sections:
        parts.append(f"## 已截断段落（如需更多请明确指定）\n{pack.truncated_sections}")
    return "\n\n".join(parts)


# ============ ConsultationResult（§46）============
@dataclass
class ConsultationResult:
    """Specialist 咨询结构化结果。不含可执行操作（§38/§46）。"""
    status: str            # completed | blocked | needs_user
    summary: str
    owner_module: str
    owner_agent: str
    direct_implications: List[str] = field(default_factory=list)
    downstream_facts: List[str] = field(default_factory=list)
    evidence_refs: List[ProjectEvidenceRef] = field(default_factory=list)
    user_decisions_needed: List[str] = field(default_factory=list)
    agent_task_id: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status, "summary": self.summary,
            "owner_module": self.owner_module, "owner_agent": self.owner_agent,
            "direct_implications": list(self.direct_implications),
            "downstream_facts": list(self.downstream_facts),
            "evidence_refs": [r.to_dict() for r in self.evidence_refs],
            "user_decisions_needed": list(self.user_decisions_needed),
            "agent_task_id": self.agent_task_id, "warnings": list(self.warnings),
        }


def _eref(module: str, etype: str, eid: str, **kw) -> ProjectEvidenceRef:
    return ProjectEvidenceRef(module=module, entity_type=etype, entity_id=eid, **kw)


# ============ 提示词（§44 Producer / §45 Consultation）============
PRODUCER_INQUIRY_SYSTEM = """\
你是 AGNES 某个项目的项目级 Producer。

你的职责：理解用户的问题或变更请求，基于系统已在下方为你检视好的项目上下文，给出精确的
项目级答案。**本阶段不修改任何项目内容。**

🔴 关键规则（AI-7D D1）：
- 系统已在下方「项目上下文」中为你检视好相关数据（概览+相关模块+依赖事实）。你**直接基于
  此回答**——检视已完成，数据就在上下文里。你**不能也不需要**调用任何工具去"再查看"。
- **绝不输出工具调用语法**：不要写 <function=...>、{"function":...}、capability id、
  <parameter=...> 等。这些是系统协议，不是你能执行的东西；你只输出给用户看的自然语言。
- **不要说"我来查看一下/让我先看看"然后停下**——上下文已就绪，必须**直接给出完整最终答案**。
- 项目级事实答案须来自下方检视结果；数据不足就明说「我无法核实 X」，绝不臆造。
- 结合上方对话历史保持多轮上下文承接（用户说的"刚才/那个"指前文）。
- 不替 Specialist 重写其专业内容（剧本/角色/分镜/计划/制作）。你只负责组合、排序、依赖、
  协调、沟通。
- 永不声称某变更已应用/已执行。
- 变更请求：只产出 analysis-only 计划（requested / related / unchanged / needs decision），
  禁止包含任何可执行操作（UpdateShot/InsertShot/ChangeOperation 等）。"""

CONSULTATION_SYSTEM = """\
你正被 Producer 咨询。**不是**被要求修改你负责的模块（本阶段只读）。

返回 JSON（仅一个对象，无解释文本）：
{
  "conclusion": "你的专业结论",
  "affected_entities": [{"module": "...", "entity_id": "..."}],
  "downstream_facts": ["可能变陈旧/受影响的下游区域"],
  "blockers": ["阻塞或歧义项"],
  "user_decisions": ["需要用户决策的项"],
  "evidence": [{"module": "...", "entity_type": "...", "entity_id": "..."}]
}
不要编辑任何内容。不要替别的模块做决定。只回答被问的问题。

澄清纪律（重要）：user_decisions 只在以下情形才列——
① 两种以上合理解释会产生**明显不同**的创作结果（实质创作歧义）；
② 项目状态中不存在且无法安全默认的必需信息；
③ 与其他 Specialist 的有效提议冲突且规则无法裁决；
④ 超出已批准范围/项目边界的重大动作。
系统已知的信息（当前选中的场景/角色、项目风格、画幅、既有资产、依赖图、既有默认值）
一律**自行采用**，绝不要向用户索取（如 Which Scene / 用什么模型 / 什么分辨率）。"""

_ANALYSIS_SCHEMA_INSTR = """\
综合各 Specialist 咨询，产出 analysis-only 计划。返回 JSON（仅一个对象）：
{
  "direct_changes": [{"module": "...", "owner_agent": "...", "summary": "...", "reason": "..."}],
  "downstream_changes": [{"module": "...", "owner_agent": "...", "summary": "...", "reason": "..."}],
  "unchanged": [{"module": "...", "entity_id": "..."}],
  "user_decisions_needed": ["..."]
}
禁止包含任何可执行操作（op/update_scene/UpdateShot 等）。direct_changes=用户明确要改的；
downstream_changes=因依赖才需改的；unchanged=明确不受影响的。"""


# ============ 请求分类（§33/§47：确定性路由）============
def classify_request(request: str, project: Optional[Project] = None) -> dict:
    """确定性首 pass 路由：kind(question|change) + modules(owner agent 列表) + refs。
    LLM 可在其上细化；代码确定模块归属（§33/§47）。"""
    req = request or ""
    kind = "change" if _CHANGE_VERBS.search(req) else "question"
    modules: List[str] = []
    for mod, pat in _MODULE_KEYWORDS:
        if pat.search(req):
            owner = MODULE_OWNER.get(mod)
            if owner and owner not in modules:
                modules.append(owner)
    if not modules and kind == "change":
        modules = ["director"]   # 默认 storyboard
    character_refs: List[str] = []
    if project is not None:
        for c in project.characters:
            if c.name and c.name in req:
                character_refs.append(c.name)
    scene_refs = sorted(set(re.findall(r"\bs\d{2,}\b", req)))
    shot_refs = sorted(set(re.findall(r"\bs\d{2,}_c\d{2,}\b", req)))
    return {"kind": kind, "modules": modules, "character_refs": character_refs,
            "scene_refs": scene_refs, "shot_refs": shot_refs}


# ============ ProjectContextBuilder（§20）============
class ProjectContextBuilder:
    """确定性 builder：决定取哪些应用数据。LLM 不决定文件路径（§20）。"""

    def __init__(self, intel: ProjectIntelligenceService,
                 budget: ContextBudget = ContextBudget()):
        self._intel = intel
        self._budget = budget

    def build_for_producer(self, *, request: str, classification: dict) -> ProjectContextPack:
        """问题/变更路径的 Producer pack：概览 + 分类指向的相关 scope + 依赖事实。"""
        ov = self._intel.overview()
        overview = ov.data
        module_context: Dict[str, Any] = {}
        ev: List[ProjectEvidenceRef] = list(ov.evidence_refs)   # AI-7D D1：概览即真 inspect
        deps: List[dict] = []
        inspection_calls: List[str] = ["overview"]
        for name in classification.get("character_refs", []):
            r = self._intel.character_usage(name)
            module_context[f"character_usage:{name}"] = r.data
            ev += r.evidence_refs
            inspection_calls.append(f"character_usage {name}")
            d = self._intel.dependencies("character", name)
            deps += d.data.get("edges", [])
        for sid in classification.get("scene_refs", []):
            r = self._intel.scene(sid)
            module_context[f"scene:{sid}"] = r.data
            ev += r.evidence_refs
            inspection_calls.append(f"scene {sid}")
            d = self._intel.dependencies("scene", sid)
            deps += d.data.get("edges", [])
        for shid in classification.get("shot_refs", []):
            r = self._intel.shot(shid)
            module_context[f"shot:{shid}"] = r.data
            ev += r.evidence_refs
            inspection_calls.append(f"shot {shid}")
        # 分类命中模块的视图
        for agent_id in classification.get("modules", []):
            view = _AGENT_VIEW.get(agent_id)
            if view and view not in module_context:
                r = self._intel.inspect(view)
                module_context[view] = r.data
                ev += r.evidence_refs
                inspection_calls.append(view)
        pack = ProjectContextPack(
            project_name=self._intel.project_name, request=request,
            snapshot_signature=self._intel._signature(),
            project_overview=overview, module_context=module_context,
            dependency_facts=deps, evidence_refs=ev)
        _enforce_budget(pack, self._budget)
        pack._inspection_calls = inspection_calls  # type: ignore[attr-defined]  # observability
        return pack

    def build_for_specialist(self, *, agent_id: str, request: str,
                             scope: List[ProjectEvidenceRef],
                             consultation_question: str) -> ProjectContextPack:
        """Specialist 咨询 pack：概览 + 该模块视图 + scope 相关视图 + 依赖事实。"""
        overview = self._intel.overview().data
        module_context: Dict[str, Any] = {}
        ev: List[ProjectEvidenceRef] = []
        view = _AGENT_VIEW.get(agent_id)
        if view:
            r = self._intel.inspect(view)
            module_context[view] = r.data
            ev += r.evidence_refs
        for ref in scope:
            if ref.entity_type == "scene":
                r = self._intel.scene(ref.entity_id)
                module_context[f"scene:{ref.entity_id}"] = r.data
                ev += r.evidence_refs
            elif ref.entity_type == "shot":
                r = self._intel.shot(ref.entity_id)
                module_context[f"shot:{ref.entity_id}"] = r.data
                ev += r.evidence_refs
            elif ref.entity_type == "character":
                r = self._intel.character_usage(ref.entity_id)
                module_context[f"character_usage:{ref.entity_id}"] = r.data
                ev += r.evidence_refs
        pack = ProjectContextPack(
            project_name=self._intel.project_name, request=request,
            snapshot_signature=self._intel._signature(),
            current_scope=scope, project_overview=overview,
            module_context=module_context, evidence_refs=ev)
        _enforce_budget(pack, self._budget)
        return pack


# ============ Specialist 咨询（§32 复用 AgentTask / §34/§46）============
def _create_consultation_task(rt: AgentRuntime, project: str, agent_id: str,
                              question: str, cap: str) -> AgentTask:
    """先持久化 AgentTask（§34：gather 前先 persist）。复用 task 引擎，不建第二套。"""
    import time as _t
    now = _t.time()
    store = rt._task_store(project)
    task = AgentTask(
        task_id="", project=project, requester_agent="producer", target_agent=agent_id,
        capability_id=cap, instruction=question, scope_type="project", scope_id=project,
        operation=AgentOp.INSPECT.value, status=TaskStatus.RUNNING.value,
        created_at=now, updated_at=now)
    return store.create(task)


def _settle_consultation_task(rt: AgentRuntime, project: str,
                              task: AgentTask, result: ConsultationResult,
                              failed: bool = False) -> None:
    task.structured_result = result.to_dict()
    task.result_summary = result.summary
    task.warnings = list(result.warnings)
    task.status = TaskStatus.FAILED.value if failed else TaskStatus.COMPLETED.value
    if failed:
        task.error = result.summary
    rt._task_store(project).update_task(task)


def _parse_consultation(text: str, agent_id: str, module: str) -> ConsultationResult:
    obj = _parse_json_object(text)
    if not isinstance(obj, dict):
        return ConsultationResult(
            status="blocked", summary="咨询返回无法解析为 JSON",
            owner_module=module, owner_agent=agent_id,
            warnings=[(text or "")[:200]])
    refs = [ProjectEvidenceRef.from_dict(e) for e in (obj.get("evidence") or []) if isinstance(e, dict)]
    status = "completed"
    if obj.get("user_decisions") or obj.get("blockers"):
        status = "needs_user" if obj.get("user_decisions") else "completed"
    return ConsultationResult(
        status=status, summary=str(obj.get("conclusion", ""))[:1000],
        owner_module=module, owner_agent=agent_id,
        direct_implications=[str(x) for x in (obj.get("affected_entities") or [])][:50],
        downstream_facts=[str(x) for x in (obj.get("downstream_facts") or [])][:50],
        evidence_refs=refs,
        user_decisions_needed=[str(x) for x in (obj.get("user_decisions") or [])][:20],
        warnings=[str(x) for x in (obj.get("blockers") or [])][:20])


async def _consult_llm(rt: AgentRuntime, project: str, task: AgentTask,
                       agent_id: str, question: str, pack: ProjectContextPack,
                       client) -> ConsultationResult:
    """给定已 persist 的 task，跑咨询 LLM 调用 + 解析（一次修复）。"""
    from .registry import agent_system_prompt
    module = _module_for_agent(agent_id)
    messages = [
        {"role": "system", "content": agent_system_prompt(agent_id) + "\n\n" + CONSULTATION_SYSTEM
         + "\n\n" + _pack_text(pack)},
        {"role": "user", "content": f"咨询问题：{question}\n\n仅返回上面要求的 JSON 对象。"},
    ]
    try:
        text = await client.chat(messages, json_mode=True, temperature=0.3, max_tokens=1024)
    except Exception as e:   # 网络/配额失败 → blocked（不把整轮拖垮）
        return ConsultationResult(status="blocked", summary=f"咨询调用失败: {str(e)[:200]}",
                                  owner_module=module, owner_agent=agent_id,
                                  agent_task_id=task.task_id, warnings=[str(e)[:200]])
    result = _parse_consultation(text, agent_id, module)
    if result.status == "blocked" and text:   # 一次修复（mirror director_proposal）
        try:
            text2 = await client.chat(
                [{"role": "system", "content": CONSULTATION_SYSTEM},
                 {"role": "user", "content": f"上一次返回无法解析，请只返回合法 JSON 对象。\n{question}"}],
                json_mode=True, temperature=0.2, max_tokens=1024)
            r2 = _parse_consultation(text2, agent_id, module)
            if r2.status != "blocked":
                result = r2
        except Exception:
            pass
    result.agent_task_id = task.task_id
    return result


def _module_for_agent(agent_id: str) -> str:
    for m, a in MODULE_OWNER.items():
        if a == agent_id:
            return m
    return agent_id


async def consult_specialist(rt: AgentRuntime, project: str, agent_id: str,
                              question: str, pack: ProjectContextPack,
                              *, client=None) -> ConsultationResult:
    """咨询一个 Specialist（§45）。复用 AgentTask 持久化 + can_execute 权限；
    对 inspect cap 调 assert_read_only_capability（§43/§96 硬闸）。LLM json 咨询 →
    结构化 ConsultationResult。"""
    a = rt._resolve_agent(agent_id)
    if not rt._is_enabled(agent_id):
        raise AgentError("disabled_agent", f"Agent 已禁用: {a.name}")
    if not rt.agents.can_delegate("producer", agent_id):
        raise AgentError("permission_denied", f"Producer 无权咨询 {a.name}")
    cap = _AGENT_INSPECT_CAP.get(agent_id)
    if not cap:
        raise AgentError("unknown_capability", f"{agent_id} 无 inspect 能力映射")
    assert_read_only_capability(cap)   # §96：mutation cap 在此被拒
    if not rt.capabilities.can_execute(agent_id, cap, AgentOp.INSPECT.value):
        raise AgentError("permission_denied", f"{a.name} 无权执行 {cap}/inspect")
    task = _create_consultation_task(rt, project, agent_id, question, cap)
    cli = client if client is not None else rt._resolve_client_or_none()
    if cli is None:
        result = ConsultationResult(status="blocked", summary="Agnes 未配置，无法咨询",
                                    owner_module=_module_for_agent(agent_id),
                                    owner_agent=agent_id, agent_task_id=task.task_id)
        _settle_consultation_task(rt, project, task, result, failed=True)
        return result
    result = await _consult_llm(rt, project, task, agent_id, question, pack, cli)
    _settle_consultation_task(rt, project, task, result, failed=(result.status == "blocked"))
    return result


async def consult_specialists_parallel(rt: AgentRuntime, project: str,
                                        items: List[Tuple[str, str]],
                                        pack: ProjectContextPack,
                                        *, client=None) -> List[ConsultationResult]:
    """独立咨询并行（§34）。先 persist 所有 task，再 gather LLM 调用，独立结算——
    一个失败不拖垮其他。"""
    if not items:
        return []
    prepared: List[Tuple[AgentTask, str, str]] = []
    for agent_id, question in items:
        cap = _AGENT_INSPECT_CAP.get(agent_id)
        if not cap:
            continue
        assert_read_only_capability(cap)
        task = _create_consultation_task(rt, project, agent_id, question, cap)
        prepared.append((task, agent_id, question))
    cli = client if client is not None else rt._resolve_client_or_none()
    results: List[ConsultationResult] = []
    if cli is None:
        for task, agent_id, _q in prepared:
            r = ConsultationResult(status="blocked", summary="Agnes 未配置",
                                   owner_module=_module_for_agent(agent_id),
                                   owner_agent=agent_id, agent_task_id=task.task_id)
            _settle_consultation_task(rt, project, task, r, failed=True)
            results.append(r)
        return results
    raw = await asyncio.gather(*[
        _consult_llm(rt, project, task, agent_id, question, pack, cli)
        for task, agent_id, question in prepared
    ], return_exceptions=True)
    for (task, agent_id, _q), res in zip(prepared, raw):
        if isinstance(res, Exception):
            r = ConsultationResult(status="blocked", summary=f"咨询异常: {str(res)[:200]}",
                                   owner_module=_module_for_agent(agent_id),
                                   owner_agent=agent_id, agent_task_id=task.task_id)
            _settle_consultation_task(rt, project, task, r, failed=True)
            results.append(r)
        else:
            _settle_consultation_task(rt, project, task, res, failed=(res.status == "blocked"))
            results.append(res)
    return results


# ============ run_inquiry（§50/§52，7A-2 引擎入口）============
async def run_inquiry(rt: AgentRuntime, project: str, user_request: str,
                      *, client=None, max_rounds: int = 1,
                      workflow_state: Optional[List[dict]] = None,
                      force_kind: Optional[str] = None,
                      history: Optional[List[dict]] = None) -> dict:
    """Producer 询问运行时入口（7A-3 team-chat 调此）。返回只读答案 / analysis-only 计划。
    纯读：不写 canonical、不建 Workspace/ChangeSet、不触发生产（§43/§96）。

    AI-7C-H §13：workflow_state = 持久化工作流快照（active_workflow_snapshot 产出）。
    注入 pack 后 Producer 回答「现在做到哪了」时有 Layer B 真相可引用，而非凭对话臆测。

    AI-7D D1：force_kind 覆盖 classify_request 的 kind。供 unified_turn 在已分类为
    project_inquiry/runtime_query 时强制走 question 路径——避免 negated-change 文本
    （如「先不要修改」）被 run_inquiry 内部 classify_request 误判为 change 而走咨询/综合路径。"""
    intel = rt.project_intelligence(project)
    proj = intel._project()
    classification = classify_request(user_request, proj)
    kind = force_kind or classification["kind"]
    builder = ProjectContextBuilder(intel)
    cli = client if client is not None else rt._resolve_client_or_none()
    observability: Dict[str, Any] = {"classification": classification, "inspection_calls": [],
                                     "specialists_consulted": [], "pack_chars": 0,
                                     "truncated": False}

    if kind == "question":
        pack = builder.build_for_producer(request=user_request, classification=classification)
        if workflow_state:
            pack.module_context["active_workflows"] = workflow_state   # §13 进度真相
        obs_calls = getattr(pack, "_inspection_calls", [])
        observability.update({"inspection_calls": obs_calls,
                              "pack_chars": len(_pack_text(pack)),
                              "truncated": bool(pack.truncated_sections)})
        answer = await _producer_answer(rt, project, user_request, pack, cli, history=history)
        return {"kind": "question", "answer": answer,
                "evidence_refs": [r.to_dict() for r in pack.evidence_refs],
                "context_observability": observability}

    # 变更路径
    modules = classification["modules"] or ["director"]
    pack = builder.build_for_producer(request=user_request, classification=classification)
    obs_calls = getattr(pack, "_inspection_calls", [])
    scope = pack.evidence_refs[:50]
    round1 = [(m, _build_consultation_question(user_request, m)) for m in modules]
    consultations = await consult_specialists_parallel(rt, project, round1, pack, client=cli)
    observability["specialists_consulted"] = [c.owner_agent for c in consultations]

    # §35：max 2 轮，仅当 round1 有可重试的缺失且非 needs_user 时跑一轮定向二问
    if max_rounds >= 2 and consultations and any(
            c.status == "completed" and not c.user_decisions_needed for c in consultations):
        # 启发式：若任一下游事实提到另一模块尚未确认 → 一次定向二问（不无限循环）
        need_follow = any(_mentions_other_module(c, modules) for c in consultations)
        if need_follow:
            target = next((c.owner_agent for c in consultations
                           if _mentions_other_module(c, modules)), None)
            if target:
                r2 = await consult_specialist(
                    rt, project, target,
                    "第二轮定向确认：请补充上一轮未明确、且影响其他模块的依赖事实。",
                    pack, client=cli)
                consultations.append(r2)
                observability["specialists_consulted"].append(r2.owner_agent)

    analysis = await _producer_synthesize_analysis(rt, project, user_request, pack,
                                                    consultations, cli)
    observability.update({"inspection_calls": obs_calls,
                          "pack_chars": len(_pack_text(pack)),
                          "truncated": bool(pack.truncated_sections),
                          "consultations": len(consultations)})
    return {"kind": "change", "analysis": analysis,
            "consultations": [c.to_dict() for c in consultations],
            "evidence_refs": [r.to_dict() for r in pack.evidence_refs],
            "context_observability": observability}


def _build_consultation_question(user_request: str, agent_id: str) -> str:
    module = _module_for_agent(agent_id)
    return (f"用户变更请求：「{user_request}」\n"
            f"请从你负责的「{module}」模块角度回答：① 直接需要改什么；"
            f"② 哪些下游区域可能因此变陈旧；③ 是否有阻塞或需用户决策的歧义。只返回 JSON。")


def _mentions_other_module(c: ConsultationResult, modules: List[str]) -> bool:
    text = " ".join(c.downstream_facts).lower()
    for m, a in MODULE_OWNER.items():
        if a in modules:
            continue
        if m in text or a in text:
            return True
    return False


async def _producer_answer(rt: AgentRuntime, project: str, request: str,
                            pack: ProjectContextPack, client, *,
                            history: Optional[List[dict]] = None) -> str:
    from .registry import agent_system_prompt
    if client is None:
        return "[chat 未配置：Agnes 推理模型不可用，无法综合答案。检视数据已就绪。]"
    mem = rt._memory_supplement(project, "producer")
    messages = [
        {"role": "system", "content": agent_system_prompt("producer") + "\n\n" + PRODUCER_INQUIRY_SYSTEM
         + "\n\n" + _pack_text(pack) + ("\n\n" + mem if mem else "")},
    ]
    # AI-7D D1 Bug B 修复：注入对话历史，使多轮 inquiry 有上下文承接（D1.3）。
    if history:
        for m in history:
            messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
    messages.append({"role": "user",
        "content": f"基于以上项目上下文与对话历史，直接回答用户。数据不足请明说，不要臆造。"
                   f"不要输出工具调用语法（<function=...>、{{\"function\":...}}）；不要说'我来查看'然后"
                   f"停下——上下文已就绪，直接给完整最终答案。不要声称已修改/已生成。\n问题：{request}"})
    try:
        return await client.chat(messages, temperature=0.7, max_tokens=1024)
    except Exception as e:
        return f"[综合失败: {str(e)[:200]}]"


async def _producer_synthesize_analysis(rt: AgentRuntime, project: str, request: str,
                                         pack: ProjectContextPack,
                                         consultations: List[ConsultationResult],
                                         client) -> dict:
    """Producer 综合 analysis-only 计划（§37/§38/§39）。execution_ready 恒 False；
    0 ChangeOperation（绝不产出可执行 op）。"""
    fallback = _fallback_analysis(request, consultations)
    if client is None:
        fallback["synthesis_note"] = "Agnes 未配置，使用确定性兜底综合。"
        return fallback
    from .registry import agent_system_prompt
    cons_json = json.dumps([c.to_dict() for c in consultations], ensure_ascii=False)
    messages = [
        {"role": "system", "content": agent_system_prompt("producer") + "\n\n" + PRODUCER_INQUIRY_SYSTEM
         + "\n\n" + _ANALYSIS_SCHEMA_INSTR + "\n\n" + _pack_text(pack)},
        {"role": "user", "content": f"用户变更请求：{request}\n\n咨询结果：{cons_json}\n\n仅返回要求的 JSON。"},
    ]
    try:
        text = await client.chat(messages, json_mode=True, temperature=0.3, max_tokens=2048)
    except Exception:
        return fallback
    obj = _parse_json_object(text)
    if not isinstance(obj, dict):
        return fallback
    return _normalize_analysis(obj, request, consultations)


def _fallback_analysis(request: str, consultations: List[ConsultationResult]) -> dict:
    direct = [{"module": _module_for_agent(c.owner_agent), "owner_agent": c.owner_agent,
               "summary": c.summary, "reason": "user_requested"}
              for c in consultations if c.status in ("completed", "needs_user")]
    downstream = [{"module": _module_for_agent(c.owner_agent), "owner_agent": c.owner_agent,
                   "summary": " / ".join(c.downstream_facts) or "见咨询结论",
                   "reason": "downstream_impact"}
                  for c in consultations if c.downstream_facts]
    decisions = []
    for c in consultations:
        decisions += c.user_decisions_needed
    return {"direct_changes": direct, "downstream_changes": downstream,
            "unchanged": [], "user_decisions_needed": decisions,
            "execution_ready": False, "change_operations": []}


def _normalize_analysis(obj: dict, request: str,
                         consultations: List[ConsultationResult]) -> dict:
    """把 LLM 输出归一为安全形态：execution_ready 恒 False；剥除任何 op 字段（§38）。
    H0：module 名经 normalize_module 归一（characters→character 等）。"""
    def _items(key, src_key):
        out = []
        for it in (obj.get(src_key) or []):
            if not isinstance(it, dict):
                continue
            # §38：若 LLM 误塞 op/update_scene 等，丢弃
            if any(k in it for k in ("op", "op_type", "update_scene", "update_shot",
                                     "insert_shot", "delete_shot", "after", "before")):
                it = {k: v for k, v in it.items() if k not in
                      ("op", "op_type", "after", "before", "operations")}
            out.append({"module": normalize_module(str(it.get("module", ""))),
                        "owner_agent": str(it.get("owner_agent", "")),
                        "summary": str(it.get("summary", ""))[:500],
                        "reason": str(it.get("reason", src_key))})
        return out
    direct = _items("direct_changes", "direct_changes")
    downstream = _items("downstream_changes", "downstream_changes")
    unchanged = [{"module": normalize_module(str(it.get("module", ""))),
                  "entity_id": str(it.get("entity_id", ""))}
                 for it in (obj.get("unchanged") or []) if isinstance(it, dict)]
    decisions = [str(x) for x in (obj.get("user_decisions_needed") or [])][:20]
    return {"direct_changes": direct, "downstream_changes": downstream,
            "unchanged": unchanged, "user_decisions_needed": decisions,
            "execution_ready": False, "change_operations": []}


# ============ AI-7D D1 · Direct Chat Turn 意图分类（复用 classify_request）============
# D1 只实现 conversation / project_inquiry / runtime_query；creative_change 由调用方
# （base.kind==change）在 D1 降为 conversation（D2 接桥 ChangeSession）。
_RUNTIME_QUERY_RE = re.compile(
    r"做到哪|做到哪了|进度|完成了吗|完成了|停了|停止|正在|还剩|还差|刚才那个|刚才那个任务|"
    r"状态|现在在|到哪|哪一步|几步|现在做到|现在生成到|还在|还几个|做完了吗|做完没",
    re.IGNORECASE)
# 项目事实/结构/疑问词——命中任一即非纯闲聊，需真 inspect 接地回答（D1.3：依赖项目事实必须 inspect）。
# 含"开头/中段/结尾/节奏/叙事"等结构词，使"你更担心开头还是中段"也进 inquiry 而非盲聊。
_PROJECT_FACT_RE = re.compile(
    r"剧本|故事|场|角色|镜头|分镜|多少|哪些|什么|在哪|有没有|几场|几镜|开头|中段|结尾|"
    r"节奏|叙事|剧情|制作|首帧|尾帧|视频|scene|shot|character|script|storyboard|"
    r"production|plan|版本|草稿|s\d|c\d", re.IGNORECASE)


def _is_trivial_chatter(req: str) -> bool:
    """无项目实体/结构/疑问引用 → 纯闲聊（问候/泛泛元观点）。命中项目事实词即非 trivial。"""
    return not _PROJECT_FACT_RE.search(req or "")


# 否定 + change 动词模式（"先不要修改"/"先别改"/"不要改" → 实为只读意图，非 change）。
# D1-A/B gate 消息含"先不要修改"/"先别改"——classify_request 的 _CHANGE_VERBS 会误命中"修改"，
# 需在此识别：change 动词若全部被否定，当作 question 路由（真 inspect 回答），不降为 conversation。
_NEG_CHANGE_RE = re.compile(
    r"(不要|先不|先别|暂不|暂别|别|无需|不用|莫|勿)\s*(?:.{0,4}?)\s*"
    r"(改|修改|换成|变成|变得|变为|删除|删掉|去掉|调整|替换|重写|续写|"
    r"换回|恢复到|恢复成|变|make|change|remove|turn)", re.IGNORECASE)


def _is_negated_change(req: str) -> bool:
    """change 动词是否全部被否定（"先不要修改" → 只读意图）。
    判定：存在否定+change 模式，且移除所有否定change 后不再有裸 change 动词。"""
    if not _NEG_CHANGE_RE.search(req or ""):
        return False
    stripped = _NEG_CHANGE_RE.sub("", req or "")
    return not _CHANGE_VERBS.search(stripped)


# ============ AI-7D D3 · runtime_control（自然语言控制命令）============
# D3.2：停止/恢复/重新规划/批准/拒绝 → unified_turn 分派到既有后端 action（cancel/resume_execution、
# approve/reject_plan、request_changes、resume_session）。模型只理解意图，代码决定执行边界。
#
# 关键防误触（D3 gate 第 3 题）："如果我现在停止，会停掉什么？"是**查询**，不是停止命令。
# _CONTROL_QUERY_GUARD 命中假设/问句标记 → 不作控制命令（落 runtime_query 由 inquiry 接地回答）。
_CONTROL_QUERY_GUARD = re.compile(
    r"如果|假设|假如|要是|万一|会怎样|会怎么样|会停掉|会取消|会完成|能不能|可不可以|"
    r"是不是|行不行|会不会|吗|呢|\?|？|完成了吗|怎么样了|怎样了", re.IGNORECASE)
_CONTROL_STOP_RE = re.compile(
    r"停止|停下|停掉|取消|先停|中断|abort|stop|cancel", re.IGNORECASE)
# reject 先于 approve："不批准/不采纳/不通过"含 approve 词，须先判 reject。
_CONTROL_REJECT_RE = re.compile(
    r"拒绝|否掉|不采纳|不批准|不要这个方案|不通过|驳回|放弃", re.IGNORECASE)
_CONTROL_APPROVE_RE = re.compile(
    r"批准|同意|采纳|认可|approve|confirm", re.IGNORECASE)
_CONTROL_REPLAN_RE = re.compile(
    r"重新规划|重新做方案|重新提案|重新设计|重新整理方案|重新来过|re-?plan", re.IGNORECASE)
# bare "继续" 不入（避"继续聊聊"误判）；须"继续执行/继续跑/接着跑/恢复"等强恢复词。
_CONTROL_RESUME_RE = re.compile(
    r"恢复|接着跑|继续执行|继续生成|继续跑|接着做|重新开始|resume|continue", re.IGNORECASE)


def _classify_control(req: str) -> Optional[str]:
    """识别控制命令子类（stop/reject/approve/replan/resume），非命令返回 None。
    guard 命中假设/问句 → None（由调用方落 runtime_query/project_inquiry 接地回答）。"""
    r = req or ""
    if _CONTROL_QUERY_GUARD.search(r):
        return None
    if _CONTROL_STOP_RE.search(r):
        return "stop"
    if _CONTROL_REJECT_RE.search(r):
        return "reject"
    if _CONTROL_APPROVE_RE.search(r):
        return "approve"
    if _CONTROL_REPLAN_RE.search(r):
        return "replan"
    if _CONTROL_RESUME_RE.search(r):
        return "resume"
    return None


def classify_turn_intent(request: str, project: Optional[Project] = None) -> dict:
    """AI-7D D1 Direct Chat turn 意图分类。

    复用 classify_request（question|change + modules + refs）。在 question 内进一步分：
      runtime_query   进度/状态词 → run_inquiry/run_specialist_read_answer(workflow_state=snapshot)
      runtime_control 控制命令（停止/恢复/批准/拒绝/重规划；guard 排除假设问句）→ unified_turn
                     分派既有后端 action（D3.2：cancel/resume_execution、approve/reject_plan、
                     request_changes、resume_session）。先于 runtime_query 判定。
      conversation    纯闲聊（无项目事实词）→ send_message 盲聊
      project_inquiry 默认（拿不准就真 inspect，更安全；D1.4）
    change（base.kind==change 且未被全部否定）→ D1 降为 conversation（不建 session、不输出
                         半截 holding；D2 接 ChangeSession 桥接）。change 动词被全部否定
                         （"先不要修改"）→ 当作 question 继续判定（路由 inquiry，真 inspect 回答）。

    返回 {"intent", "base"}。base=classify_request 原始结果。
    """
    base = classify_request(request, project)
    req = request or ""
    if base["kind"] == "change" and not _is_negated_change(req):
        return {"intent": "conversation", "base": base,
                "note": "D1: change→conversation (D2 bridges to ChangeSession)"}
    # AI-7D D3：runtime_control（控制命令）——先于 runtime_query 判定，使"停止刚才那个"→控制
    # 而非查询（_RUNTIME_QUERY_RE 也含"停止"）。_CONTROL_QUERY_GUARD 已排除假设/问句
    # （"如果我现在停止会停掉什么"→ control=None → 落 runtime_query 接地回答，不误执行）。
    # 控制动词非 _CHANGE_VERBS，与 change 分支无冲突。
    control = _classify_control(req)
    if control:
        return {"intent": "runtime_control", "control": control, "base": base}
    # change 被全部否定 → 落到 question 判定
    if _RUNTIME_QUERY_RE.search(req):
        return {"intent": "runtime_query", "base": base}
    if _is_trivial_chatter(req):
        return {"intent": "conversation", "base": base}
    return {"intent": "project_inquiry", "base": base}


def _scope_refs(intel: "ProjectIntelligenceService", scope_type: str, scope_id: str,
                classification: dict) -> List[ProjectEvidenceRef]:
    """从 session scope + 分类 refs 派生 specialist pack 的 scope 证据引用。"""
    refs: List[ProjectEvidenceRef] = []
    if scope_type == "scene" and scope_id:
        refs.append(_eref("storyboard", "scene", scope_id))
    elif scope_type == "shot" and scope_id:
        refs.append(_eref("storyboard", "shot", scope_id))
    for sid in classification.get("scene_refs", []):
        r = _eref("storyboard", "scene", sid)
        if r not in refs:
            refs.append(r)
    for shid in classification.get("shot_refs", []):
        refs.append(_eref("storyboard", "shot", shid))
    for name in classification.get("character_refs", []):
        refs.append(_eref("character", "character", name))
    return refs[:50]


async def run_specialist_read_answer(rt: "AgentRuntime", project: str, agent_id: str,
                                      scope_type: str, scope_id: str, request: str,
                                      *, client=None,
                                      workflow_state: Optional[List[dict]] = None,
                                      history: Optional[List[dict]] = None) -> dict:
    """AI-7D D1 · Specialist Direct Chat 只读查询/对话回答。

    复用 ProjectContextBuilder.build_for_specialist 构造该 specialist 有界 pack（overview +
    该 agent 的 _AGENT_VIEW 视图 + scope 相关 scene/shot/character 视图）——**真读 canonical**，
    不臆造。用 agent_system_prompt（含真话纪律）+ pack → 一次 LLM 接地自然语言回答。

    纯读：不写 canonical、不建 ChangeSession、不 dispatch mutation（D1 边界）。
    返回 {answer, evidence_refs, inspection_calls, status}。
    AI-7C-H §13：workflow_state=持久化工作流快照，runtime_query 时注入 pack 使进度回答有
    Layer B 真相可引用（非对话臆测）。
    """
    intel = rt.project_intelligence(project)
    proj = intel._project()
    classification = classify_request(request, proj)
    scope = _scope_refs(intel, scope_type, scope_id, classification)
    builder = ProjectContextBuilder(intel)
    pack = builder.build_for_specialist(agent_id=agent_id, request=request,
                                         scope=scope, consultation_question=request)
    if workflow_state:
        pack.module_context["active_workflows"] = workflow_state
    obs_calls = getattr(pack, "_inspection_calls", [])
    ev = [r.to_dict() for r in pack.evidence_refs]
    cli = client if client is not None else rt._resolve_client_or_none()
    if cli is None:
        return {"answer": "[chat 未配置：Agnes 推理模型不可用，无法基于项目上下文回答。请配置后重试。]",
                "evidence_refs": ev, "inspection_calls": obs_calls,
                "status": ResultStatus.NOT_CONFIGURED.value}
    from .registry import agent_system_prompt
    mem = rt._memory_supplement(project, agent_id)
    messages = [
        {"role": "system",
         "content": agent_system_prompt(agent_id) + "\n\n" + _pack_text(pack)
         + ("\n\n" + mem if mem else "")
         + "\n\n🔴 系统已为你检视好以上项目上下文，你直接基于此回答。不能也不需要调用工具去"
           "再查看。绝不输出工具调用语法（<function=...>、{\"function\":...}）。不要说"
           "'我来查看'然后停下——直接给完整最终答案。结合对话历史保持多轮上下文承接。"},
    ]
    # AI-7D D1 Bug B 修复：注入对话历史，使 specialist 多轮 inquiry 有上下文承接（D1.3）。
    if history:
        for m in history:
            messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
    messages.append({"role": "user",
        "content": f"基于以上项目上下文与对话历史，直接回答用户。数据不足请明说，不要臆造。"
                   f"不要声称已修改/已生成/已执行任何变更或制作。\n问题：{request}"})
    try:
        text = await cli.chat(messages, temperature=0.7, max_tokens=1024)
    except Exception as e:
        return {"answer": f"[回答失败: {str(e)[:200]}]",
                "evidence_refs": ev, "inspection_calls": obs_calls,
                "status": ResultStatus.FAILED.value}
    return {"answer": text, "evidence_refs": ev, "inspection_calls": obs_calls,
            "status": ResultStatus.COMPLETED.value}
