# src/agents/capabilities.py
# AI-6B Capability Registry —— 能力目录 + adapter dispatch（§5/§6）。
#
# 设计不变量（§6）：
#   Agent → Capability → Existing Service / Workflow（ProjectStore / production_plans）。
#   禁止重新写一份生成逻辑、禁止 Agent 直连 Agnes 做生成。
#   AI-6B v1 只接通 read-only inspect 能力；edit/execute 能力注册权限但 adapter 返回
#   not_implemented（预留，§18/§19 红线——Director 永不直接覆盖 shots）。
#
# Import 纪律（§修正2）：
#   本文件只 import src.core.* / src.agents.models / typing。
#   **严禁 import routers/***。production-plan 读取通过 CapabilityContext.plans_reader
#   这个 callable 注入（由 composition root routers/agents.py 提供），adapter 只调用、不 import。
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .models import (
    CapabilityDefinition, CapabilityCategory, AgentOp, RiskLevel,
    AgentStructuredResult, ResultStatus, ScopeType,
)
from ..core.models import Project


# ============ CapabilityContext（§6 注入边界）============
@dataclass
class CapabilityContext:
    """能力执行上下文。project_loader / plans_reader 均为 callable，由
    composition root（routers/agents.py）注入——adapter 只调用，绝不 import router。
    project_loader 零参 -> Project（对齐 WorkspaceStore.canonical_loader 模式，测试可注入 lambda）。"""
    project: str                                  # 项目名（写 artifact_refs 用）
    scope_type: str
    scope_id: str
    project_loader: Callable[[], Project]
    plans_reader: Optional[Callable[[str, str], Optional[dict]]] = None
    # plans_reader(project_name, scene_id) -> plan dict or None


# ============ 能力目录（§5 / §34 权限基线）============
# implemented=True -> v1 已接通 inspect adapter；False -> 注册权限但预留（not_implemented）。
# 注意 Producer 不在任何 domain-inspect 的 allowed_agents 中，且 Producer.allowed_capabilities
# 只有 project.inspect -> can_execute(producer, storyboard.inspect)==False（必须 delegate）。
_WIRED = {
    "project.inspect", "story.inspect", "character.inspect", "storyboard.inspect",
    "production_plan.inspect", "shot.inspect", "composition.inspect",
}


def _cap(cid, name, desc, cat, owner, agents, ops, risk, implemented):
    return CapabilityDefinition(
        id=cid, name=name, description=desc, category=cat, owner_agent=owner,
        allowed_agents=list(agents), operations=list(ops), risk_level=risk,
        implemented=implemented,
    )


def build_default_capabilities() -> List[CapabilityDefinition]:
    I = AgentOp.INSPECT.value
    E = AgentOp.EXECUTE.value
    R = AgentOp.RETRY.value
    C = AgentOp.CANCEL.value
    P = AgentOp.PROPOSE.value   # AI-7B：语义提案操作（不落盘、不应用）
    CC = CapabilityCategory
    LO, ME, HI = RiskLevel.LOW.value, RiskLevel.MEDIUM.value, RiskLevel.HIGH.value
    return [
        # ---- project ----
        _cap("project.inspect", "项目概览", "读取项目整体结构与工作流状态",
             CC.PROJECT.value, "producer", ["producer"], [I], LO, True),
        # ---- story ----
        _cap("story.inspect", "故事查看", "读取故事文本与场景结构",
             CC.STORY.value, "script_writer", ["script_writer"], [I], LO, True),
        _cap("story.edit", "故事修改提案", "修改/续写故事（须经安全业务接口或 Workspace）",
             CC.STORY.value, "script_writer", ["script_writer"], [E], HI, False),
        # AI-7B：剧本语义提案能力（story.propose_change）。仅产出语义意图
        # （update_story_text/update_scene/insert_scene/delete_scene/reorder_scenes），
        # 不落盘 canonical、不建 Workspace、不应用——由 change_protocol 在 PROPOSAL_ONLY 模式调度。
        # 7B-1 仅注册+授权+硬闸；7B-2 经 change_protocol.service 接通真实 LLM 提案。
        _cap("story.propose_change", "故事语义提案(AI-7B)",
             "从剧本角度产出语义修改意图（不改 canonical/不建 Workspace/不应用）",
             CC.STORY.value, "script_writer", ["script_writer"], [P], ME, False),
        # ---- character ----
        _cap("character.inspect", "角色查看", "读取角色设定与参考图状态",
             CC.CHARACTER.value, "character_designer", ["character_designer"], [I], LO, True),
        _cap("character.design", "角色设计", "角色视觉设定/参考图生成（包装现有能力）",
             CC.CHARACTER.value, "character_designer", ["character_designer"], [E], HI, False),
        # AI-7B：角色语义提案能力（character.propose_change）。仅产出语义意图
        # （update_character/insert_character/delete_character；改名=needs_user_decision），
        # 不生成资产、不应用——由 change_protocol 在 PROPOSAL_ONLY 模式调度。
        # 7B-1 仅注册+授权+硬闸；7B-2 经 change_protocol.service 接通真实 LLM 提案。
        _cap("character.propose_change", "角色语义提案(AI-7B)",
             "从角色角度产出语义修改意图（不生成资产/不建 Workspace/不应用）",
             CC.CHARACTER.value, "character_designer", ["character_designer"], [P], ME, False),
        # ---- scene（Agent System V2 Phase C2：Scene Agent 原生 tool-loop；能力只登记
        #      语义 id 供 registry 完整性校验与查询展示，不走 _ADAPTERS dispatch）----
        _cap("scene.inspect", "场景系统查看",
             "读取 Scene System 生产事实（Location/SpatialAnchor/SetLayout/LocationView/"
             "SceneInstance/SceneState/SceneInstanceView）",
             CC.SCENE.value, "scene_designer", ["scene_designer"], [I], LO, False),
        _cap("scene.prepare", "场景生产准备",
             "Location/Anchor/Layout/View/Instance/State/Sciv 生产准备与审批"
             "（包装 SceneSystemService/SceneVisualService 正式链路）",
             CC.SCENE.value, "scene_designer", ["scene_designer"], [E], ME, False),
        # ---- storyboard ----
        _cap("storyboard.inspect", "分镜查看", "读取场景分镜结构（DEMO）",
             CC.STORYBOARD.value, "director",
             ["director", "continuity_reviewer", "editor"], [I], LO, True),
        # AI-6D：已接通为真实能力。经由 AgentRuntime.propose_storyboard_change（async，
        # 需 Agnes+Workspace），不经同步 _ADAPTERS dispatch——故不在 _WIRED/_ADAPTERS 中。
        # AI-7B：加 PROPOSE 操作——Team ChangeSession 路径在 PROPOSAL_ONLY 模式下经此提案
        # （复用 generate_semantic_proposal，不走 Apply）。既有 direct execute UI 流程不变。
        _cap("storyboard.propose_change", "分镜修改提案",
             "KEEP/UPDATE/INSERT/DELETE/REORDER——必须经 Workspace/ChangeSet（§18 红线）",
             CC.STORYBOARD.value, "director", ["director"], [E, P], HI, True),
        # ---- production_plan ----
        _cap("production_plan.inspect", "制作计划查看", "读取场景制作计划与镜头策略",
             CC.PRODUCTION_PLAN.value, "production_planner",
             ["production_planner", "shot_producer", "continuity_reviewer"], [I], LO, True),
        # AI-6E：已接通为真实能力。经由 AgentRuntime delegate 的 async 执行分支
        #（_execute_production_plan_create → plan_production_business），需 Agnes+落盘，
        # 不经同步 _ADAPTERS dispatch——故不在 _WIRED/_ADAPTERS 中。
        _cap("production_plan.create", "制作计划创建", "为镜头决定制作策略（包装现有 Planner）",
             CC.PRODUCTION_PLAN.value, "production_planner", ["production_planner"], [E], HI, True),
        # ---- shot ----
        _cap("shot.inspect", "镜头查看", "读取镜头字段与资产/计划状态",
             CC.SHOT.value, "shot_producer",
             ["shot_producer", "continuity_reviewer", "editor", "production_planner"],
             [I], LO, True),
        # AI-6F.0：已接通为真实（fake durable）能力。经由 AgentRuntime delegate 的长执行分支
        #（_delegate_long_running → execute_shot_production → 现有 JobQueue + fake handler），
        # 提交 durable Job 后 HTTP 先返回，settlement bridge 对账结算。不经同步 _ADAPTERS dispatch。
        # 注：当前用 deterministic fake handler（fake_shot_handlers.py），AI-6F 接通真实生成时
        # 把 kind 映射指向 routers/shots.py 的真实 JOB_HANDLERS，runtime/bridge 逻辑不变。
        _cap("shot.first_frame", "首帧生成", "按 Production Plan 执行首帧制作",
             CC.SHOT.value, "shot_producer", ["shot_producer"], [E, R, C], HI, True),
        _cap("shot.last_frame", "尾帧生成", "按 Production Plan 执行尾帧制作",
             CC.SHOT.value, "shot_producer", ["shot_producer"], [E, R, C], HI, True),
        _cap("shot.video", "视频生成", "按 Production Plan 执行视频制作",
             CC.SHOT.value, "shot_producer", ["shot_producer"], [E, R, C], HI, True),
        # AI-7C §49：shot.audio —— Shot Producer 执行能力，包既有 _handler_audio（JobQueue kind
        # "audio"），不另造 TTS pipeline、不直调 Edge TTS。AI-7C 受影响 shot 的配音再生用之。
        _cap("shot.audio", "镜配音", "按 shot dialogue 重新生成配音（包既有 audio business helper）",
             CC.SHOT.value, "shot_producer", ["shot_producer"], [E, R, C], HI, True),
        # AI-7C §45：character.views.generate —— Character Agent 执行能力。用**已批准** identity
        # 生成当前 production reference views（front/side/back），不重新设计角色/不改 description。
        # 复用既有 chain 生成（§44），durable via JobQueue（§46）。真实 handler 在 §103 acceptance 接通。
        _cap("character.views.generate", "角色参考图再生(AI-7C)",
             "用已批准角色身份再生当前 reference views（不改 identity，不重新设计）",
             CC.CHARACTER.value, "character_designer", ["character_designer"], [E, R, C], HI, True),
        # ---- review ----
        _cap("review.shot", "镜头审查", "角色/画面/空间/动作连续性检查与 retry 提案",
             CC.REVIEW.value, "continuity_reviewer", ["continuity_reviewer"], [I], ME, False),
        _cap("review.scene", "场景审查", "场景级连续性与质量审查",
             CC.REVIEW.value, "continuity_reviewer", ["continuity_reviewer"], [I], ME, False),
        # ---- composition ----
        _cap("composition.inspect", "合成查看", "读取镜头顺序/选中版本/合成状态",
             CC.COMPOSITION.value, "editor", ["editor"], [I], LO, True),
        _cap("composition.compose", "最终合成", "版本选择/排列/字幕/音轨/导出（包装现有能力）",
             CC.COMPOSITION.value, "editor", ["editor"], [E], HI, False),
        # ---- sequence（Phase 44B：Assembly Agent 原生 tool-loop；能力只登记语义 id
        #      供 registry 完整性校验与查询展示，同 scene.inspect/scene.prepare 先例——
        #      真实执行走 assembly.py 工具面，不经 _ADAPTERS dispatch）----
        _cap("assembly.inspect", "成片装配查看",
             "读取冻结快照/SequenceBlock/渲染版本/freshness/媒体探测与时间线预演"
             "（只读消费 Shot.final_video 源真相）",
             CC.SEQUENCE.value, "assembly", ["assembly"], [I], LO, False),
        _cap("assembly.assemble", "成片装配执行",
             "SequenceBlock 创建/更新（OCC）/渲染提交（JobQueue）/AssemblyManifest 发布/"
             "剪辑提案（包装 SequenceService 正式链路；不碰 Shot/生成/ffmpeg shell）",
             CC.SEQUENCE.value, "assembly", ["assembly"], [E], ME, False),
    ]


# ============ inspect adapters（read-only，不调 Agnes、不写项目）============
def _project_inspect(ctx: CapabilityContext) -> AgentStructuredResult:
    p = ctx.project_loader()
    scene_n, shot_n, char_n = len(p.scenes), len(p.all_shots()), len(p.characters)
    summary = (f"项目「{p.name}」：{scene_n} 个场景，{shot_n} 个镜头，{char_n} 个角色。"
               f"风格：{p.style.suffix()[:80]}。工作流状态：稳定。")
    return AgentStructuredResult(
        status=ResultStatus.COMPLETED.value, summary=summary,
        artifact_refs=[{"type": "scene", "id": s.scene_id} for s in p.scenes],
        suggested_next_actions=["查看各场景分镜结构", "检查制作计划"],
        data={"project": p.name, "scene_count": scene_n,
              "shot_count": shot_n, "char_count": char_n},
    )


def _story_inspect(ctx: CapabilityContext) -> AgentStructuredResult:
    p = ctx.project_loader()
    scenes = [{"scene_id": s.scene_id, "summary": s.summary,
               "characters": list(s.characters), "shot_count": len(s.shots)}
              for s in p.scenes]
    summary = f"故事文本 {len(p.story_text)} 字；{len(p.scenes)} 个场景。"
    return AgentStructuredResult(
        status=ResultStatus.COMPLETED.value, summary=summary,
        artifact_refs=[{"type": "scene", "id": s["scene_id"]} for s in scenes],
        data={"story_text_head": p.story_text[:2000], "scenes": scenes},
    )


def _character_inspect(ctx: CapabilityContext) -> AgentStructuredResult:
    p = ctx.project_loader()
    chars = []
    for c in p.characters:
        views = {}
        for v in ("front", "side", "back"):
            a = c.views.get(v)
            views[v] = bool(a and (a.local or a.url))
        # 关联场景：角色出现在哪些 scene
        related = [s.scene_id for s in p.scenes if c.name in s.characters]
        chars.append({"name": c.name, "description_en": c.description_en,
                       "has_views": views, "related_scenes": related})
    summary = f"{len(p.characters)} 个角色；风格 {p.style.suffix()[:60]}。"
    return AgentStructuredResult(
        status=ResultStatus.COMPLETED.value, summary=summary,
        artifact_refs=[{"type": "character", "id": c["name"]} for c in chars],
        data={"characters": chars, "style": p.style.suffix()[:120]},
    )


def _storyboard_inspect(ctx: CapabilityContext) -> AgentStructuredResult:
    """DEMO 能力（§11/§30）：Director 检查某场景的分镜结构。"""
    p = ctx.project_loader()
    scene = next((s for s in p.scenes if s.scene_id == ctx.scope_id), None)
    if scene is None:
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=f"未找到场景 {ctx.scope_id}",
            warnings=[f"unknown scene: {ctx.scope_id}"])
    shots = [{"shot_id": sh.shot_id, "description": sh.description,
              "characters": list(sh.characters), "status": sh.status,
              "target_duration_s": sh.target_duration_s}
             for sh in scene.shots]
    issues: List[str] = []
    if not scene.shots:
        issues.append("场景无镜头（建议 Director 规划）")
    # 覆盖检查：每个出场角色至少出现在一个镜头
    for cn in scene.characters:
        if not any(cn in sh.characters for sh in scene.shots):
            issues.append(f"角色 {cn} 在场景但未出现在任何镜头")
    summary = (f"场景 {scene.scene_id}「{scene.title or scene.summary[:24]}」"
               f"含 {len(scene.shots)} 个镜头："
               f"{[sh.shot_id for sh in scene.shots]}。")
    return AgentStructuredResult(
        status=ResultStatus.COMPLETED.value, summary=summary,
        artifact_refs=[{"type": "shot", "id": sh.shot_id} for sh in scene.shots],
        suggested_next_actions=["检查场景连续性", "审阅镜头节奏"],
        data={"scene_id": scene.scene_id, "scene_summary": scene.summary,
              "shots": shots, "issues": issues},
    )


def _production_plan_inspect(ctx: CapabilityContext) -> AgentStructuredResult:
    if ctx.plans_reader is None:
        return AgentStructuredResult(
            status=ResultStatus.NOT_CONFIGURED.value,
            summary="production_plan 读取器未注入（composition root 未提供 plans_reader）")
    plan = ctx.plans_reader(ctx.project, ctx.scope_id)
    if not plan:
        return AgentStructuredResult(
            status=ResultStatus.COMPLETED.value,
            summary=f"场景 {ctx.scope_id} 暂无制作计划。",
            data={"scene_id": ctx.scope_id, "has_plan": False})
    shots = plan.get("shots") or []
    summary = (f"场景 {ctx.scope_id} 制作计划：状态 {plan.get('status', 'draft')}，"
               f"{len(shots)} 个镜头策略。")
    return AgentStructuredResult(
        status=ResultStatus.COMPLETED.value, summary=summary,
        artifact_refs=[{"type": "shot", "id": s.get("shot_id")} for s in shots if s.get("shot_id")],
        data={"scene_id": ctx.scope_id, "has_plan": True,
              "plan_status": plan.get("status"),
              "shots": [{"shot_id": s.get("shot_id"),
                         "opening_source": s.get("opening_source"),
                         "link_mode": s.get("link_mode")}
                        for s in shots]})


def _shot_inspect(ctx: CapabilityContext) -> AgentStructuredResult:
    p = ctx.project_loader()
    sh = p.get_shot(ctx.scope_id)
    if sh is None:
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=f"未找到镜头 {ctx.scope_id}",
            warnings=[f"unknown shot: {ctx.scope_id}"])
    summary = (f"镜头 {sh.shot_id}：{sh.description[:60]}；"
               f"首帧 {sh.first_frame.status}、尾帧 {sh.last_frame.status}、"
               f"视频 {sh.video.status}、音频 {sh.audio.status}。")
    return AgentStructuredResult(
        status=ResultStatus.COMPLETED.value, summary=summary,
        artifact_refs=[{"type": "shot", "id": sh.shot_id}],
        data={"shot_id": sh.shot_id, "description": sh.description,
              "first_frame_status": sh.first_frame.status,
              "last_frame_status": sh.last_frame.status,
              "video_status": sh.video.status,
              "audio_status": sh.audio.status,
              "link_mode": sh.link_mode,
              "opening_source": sh.opening_source,
              "final_video": sh.final_video})


def _composition_inspect(ctx: CapabilityContext) -> AgentStructuredResult:
    p = ctx.project_loader()
    shots = p.all_shots()
    order = [{"shot_id": sh.shot_id, "final_video": sh.final_video,
              "video_status": sh.video.status} for sh in shots]
    with_final = sum(1 for s in order if s["final_video"])
    summary = f"镜头顺序 {len(order)} 个；{with_final} 个已选最终视频版本。"
    return AgentStructuredResult(
        status=ResultStatus.COMPLETED.value, summary=summary,
        artifact_refs=[{"type": "shot", "id": s["shot_id"]} for s in order],
        data={"shot_order": order, "with_final_video": with_final})


# ============ dispatch ============
_ADAPTERS: Dict[str, Callable[[CapabilityContext], AgentStructuredResult]] = {
    "project.inspect": _project_inspect,
    "story.inspect": _story_inspect,
    "character.inspect": _character_inspect,
    "storyboard.inspect": _storyboard_inspect,
    "production_plan.inspect": _production_plan_inspect,
    "shot.inspect": _shot_inspect,
    "composition.inspect": _composition_inspect,
}


def dispatch(capability_id: str, ctx: CapabilityContext) -> AgentStructuredResult:
    """按 capability_id 派发到 adapter。未注册 / 预留能力 -> not_implemented。"""
    fn = _ADAPTERS.get(capability_id)
    if fn is None:
        return AgentStructuredResult(
            status=ResultStatus.NOT_IMPLEMENTED.value,
            summary=f"能力 {capability_id} 在 AI-6B v1 未接通或未注册。")
    try:
        return fn(ctx)
    except Exception as e:   # adapter 失败不影响现有 Workflow（§29）
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=f"能力 {capability_id} 执行失败：{e}",
            warnings=[str(e)[:500]])


def is_wired(capability_id: str) -> bool:
    return capability_id in _ADAPTERS
