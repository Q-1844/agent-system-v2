# src/agents/registry.py
# AI-6B AgentRegistry / CapabilityRegistry / ModelProfileRegistry / AgentSettingsStore +
# 8 个固定 Agent 定义与系统提示（§4/§5/§7/§13/§16/§34）。
#
# can_execute（§修正1）须同时满足：agent 存在 + capability 存在 +
#   capability_id ∈ AgentDefinition.allowed_capabilities + agent_id ∈ cap.allowed_agents +
#   operation ∈ cap.operations。allowed_capabilities 是真实授权检查，非展示元数据。
# enabled/disabled 由 AgentRuntime 在执行前单独检查（不在 can_execute 内）。
#
# Import 纪律：只 import deps(BASE_DIR/_APP_CFG) / src.core.atomic / 本包。不 import routers/*。
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, List, Optional

from deps import BASE_DIR, _APP_CFG
from src.core.atomic import atomic_write_json

from .models import AgentDefinition, CapabilityDefinition, ModelProfile
from .capabilities import build_default_capabilities


# Agent 稳定 ID（进入持久化数据，勿改既有 id；新增只做加法式扩展——
# 28.md §4：已注册 identity 是稳定的，允许 additive extension）。
# Phase C2：scene_designer（Scene Agent）作为第四个正式 Specialist 加入，
# 位置按生产流程排在 Resource Agent 之后、Director 之前。
SPECIALISTS = [
    "script_writer", "character_designer", "scene_designer", "director",
    "production_planner", "shot_producer", "continuity_reviewer", "editor",
    # Phase 44B：Assembly Agent（基础成片）加法式加入（勿改既有 id）。
    "assembly",
]
SPEC_AGENT_IDS = ["producer"] + SPECIALISTS   # spec 顺序


def _build_agents() -> List[AgentDefinition]:
    return [
        AgentDefinition(
            id="producer", name="Producer Supervisor",
            description="项目总协调者：理解需求、分派任务、汇总结果",
            role="总协调",
            allowed_capabilities=["project.inspect"],
            read_scopes=["project", "workflow", "agent_tasks", "workspaces"],
            write_scopes=["agent_task"],
            can_delegate_to=list(SPECIALISTS),
            default_model_profile="producer_profile"),
        AgentDefinition(
            id="script_writer", name="Script Writer",
            description="负责故事/场景/剧情结构与修改",
            role="剧本",
            allowed_capabilities=["story.inspect", "story.edit", "story.propose_change"],
            read_scopes=["story", "scenes", "characters"],
            write_scopes=["story_proposal", "scene_proposal"],
            can_delegate_to=[], default_model_profile="text_default"),
        AgentDefinition(
            id="character_designer", name="Resource Agent",
            description="资源设计师（内部兼容 id）：角色身份/三视图/服装/造型/道具/组合参考/共享库",
            role="角色 · 造型 · 道具 · 生产资源",
            allowed_capabilities=["character.inspect", "character.design",
                                  "character.propose_change", "character.views.generate"],
            read_scopes=["characters", "style", "scenes"],
            write_scopes=["character_proposal", "character_assets"],
            can_delegate_to=[], default_model_profile="character_profile"),
        AgentDefinition(
            id="scene_designer", name="Scene Agent",
            description="场景设计师（Scene Production Designer）：Location/SpatialAnchor/"
                        "SetLayout/LocationView/SceneInstance/SceneState/SceneInstanceView "
                        "生产事实与 approved 场景参考",
            role="地点 · 空间 · 场景状态 · 场景参考",
            # 原生 tool-loop Agent（同 script_writer/character_designer/director/producer），
            # 能力走工具面而非 capability dispatch；这里只登记语义 id 供查询展示。
            allowed_capabilities=["scene.inspect", "scene.prepare"],
            read_scopes=["scenes", "characters", "shots", "style", "scene_production"],
            write_scopes=["scene_production"],
            can_delegate_to=[], default_model_profile="scene_profile"),
        AgentDefinition(
            id="director", name="Director",
            description="场景→镜头、分镜结构/节奏/意图；修改须经 Workspace",
            role="导演",
            allowed_capabilities=["storyboard.inspect", "storyboard.propose_change"],
            read_scopes=["scenes", "characters", "shots", "style"],
            write_scopes=["storyboard_proposal"],
            can_delegate_to=[], default_model_profile="text_default"),
        AgentDefinition(
            id="production_planner", name="Production Planner",
            description="决定每个 Shot 的制作策略（只规划不执行生成）",
            role="规划",
            allowed_capabilities=["production_plan.inspect", "production_plan.create", "shot.inspect"],
            read_scopes=["shots", "assets", "continuity", "production_state"],
            write_scopes=["production_plan"],
            can_delegate_to=[], default_model_profile="text_default"),
        AgentDefinition(
            id="shot_producer", name="Shot Producer",
            description="按 Production Plan 执行镜头制作（首/尾/视频是不同 Capability）",
            role="制作",
            allowed_capabilities=["shot.inspect", "production_plan.inspect",
                                  "shot.first_frame", "shot.last_frame", "shot.video",
                                  "shot.audio"],
            read_scopes=["shots", "production_plan", "assets", "job_state"],
            write_scopes=["shot_production"],
            can_delegate_to=[], default_model_profile="shot_producer_profile"),
        AgentDefinition(
            id="continuity_reviewer", name="Continuity Reviewer",
            description="角色/画面/空间/动作连续性检查与 retry 提案（只审查+提案）",
            role="审查",
            allowed_capabilities=["storyboard.inspect", "shot.inspect",
                                  "production_plan.inspect", "review.shot", "review.scene"],
            read_scopes=["shots", "production_plan", "assets", "continuity"],
            write_scopes=["review_proposal", "retry_proposal"],
            can_delegate_to=[], default_model_profile="text_default"),
        AgentDefinition(
            id="editor", name="Editor",
            description="版本选择/排列检查/合成/字幕/音轨/导出",
            role="剪辑",
            allowed_capabilities=["composition.inspect", "storyboard.inspect",
                                  "shot.inspect", "composition.compose"],
            read_scopes=["shots", "versions", "audio", "subtitles", "composition"],
            write_scopes=["composition", "export"],
            can_delegate_to=[], default_model_profile="text_default"),
        # Phase 44B：Assembly Agent（基础成片）——原生 tool-loop Specialist。
        # 只写成片装配域（SequenceBlock/AssemblyManifest/提案），只读消费
        # Shot.final_video 源真相；无生成工具、无 select 工具、无 shell 入口。
        AgentDefinition(
            id="assembly", name="Assembly Agent",
            description="基础成片装配：冻结快照消费/时间线草案/渲染提交/导出诊断/"
                        "时间线契约发布/剪辑提案",
            role="成片装配",
            allowed_capabilities=["assembly.inspect", "assembly.assemble",
                                  "shot.inspect"],
            read_scopes=["shots", "versions", "sequences", "snapshot"],
            write_scopes=["sequence_assembly"],
            can_delegate_to=[], default_model_profile="text_default"),
    ]


# ============ ModelProfile 默认值（从 config 派生，保证与配置一致）============
_agnes_models = ((_APP_CFG.get("agnes") or {}).get("models") or {})
_sn_models = ((_APP_CFG.get("sensenova") or {}).get("models") or {})
KNOWN_MODEL_IDS = ({v for v in _agnes_models.values() if v}
                   | {v for v in _sn_models.values() if v})   # 文本/图片/视频模型 id 集合


def _default_profiles() -> List[ModelProfile]:
    """文本模型档案（2026-08-29 Agent 模型切换）：
    - text_default = Agnes（默认；Agent 未绑定/绑定它都用 Agnes 文本客户端）
    - text_sensenova_68fl = SenseNova 6.8 Flash Lite（Agent 输入区滑动开关绑定；
      u1-fast 接口暂不可调用，不提供 UI 档案，配置与代码保留）
    text_provider 字段供 runtime 按 Agent 选择文本客户端；图片/视频模型不受影响。"""
    t = _agnes_models.get("text")
    sn_text = _sn_models.get("text")
    im = _agnes_models.get("image")
    vi = _agnes_models.get("video")
    return [
        ModelProfile(id="text_default", name="Agnes（默认）", reasoning_model=t,
                     text_provider="agnes"),
        ModelProfile(id="text_sensenova_68fl", name="SenseNova 6.8 Flash Lite",
                     reasoning_model=sn_text, text_provider="sensenova"),
        ModelProfile(id="producer_profile", name="Producer 默认", reasoning_model=t,
                     text_provider="agnes"),
        ModelProfile(id="character_profile", name="角色设计默认",
                     reasoning_model=t, image_model=im, text_provider="agnes"),
        ModelProfile(id="scene_profile", name="场景设计默认",
                     reasoning_model=t, image_model=im, text_provider="agnes"),
        ModelProfile(id="shot_producer_profile", name="镜头制作默认",
                     reasoning_model=t, image_model=im, video_model=vi,
                     text_provider="agnes"),
    ]


# ============ AgentRegistry ============
class AgentRegistry:
    def __init__(self, agents: List[AgentDefinition]):
        ids = [a.id for a in agents]
        if len(ids) != len(set(ids)):
            raise ValueError("Agent ID 不唯一")
        self._by_id: Dict[str, AgentDefinition] = {a.id: a for a in agents}

    def get(self, agent_id: str) -> Optional[AgentDefinition]:
        return self._by_id.get(agent_id)

    def exists(self, agent_id: str) -> bool:
        return agent_id in self._by_id

    def list(self) -> List[AgentDefinition]:
        return [self._by_id[i] for i in SPEC_AGENT_IDS if i in self._by_id]

    def is_enabled(self, agent_id: str, settings_store) -> bool:
        """enabled 由 AgentRuntime 在执行前单独检查（§修正1：不在 can_execute 内）。
        settings 覆盖优先，否则用 AgentDefinition.enabled 默认。"""
        a = self.get(agent_id)
        if a is None:
            return False
        s = settings_store.get_agent_setting(agent_id)
        if "enabled" in s:
            return bool(s["enabled"])
        return a.enabled

    def can_delegate(self, requester_id: str, target_id: str) -> bool:
        r = self.get(requester_id)
        return bool(r) and target_id in r.can_delegate_to


# ============ CapabilityRegistry ============
class CapabilityRegistry:
    def __init__(self, caps: List[CapabilityDefinition], agent_registry: AgentRegistry):
        self._by_id: Dict[str, CapabilityDefinition] = {c.id: c for c in caps}
        self._agents = agent_registry

    def get(self, cap_id: str) -> Optional[CapabilityDefinition]:
        return self._by_id.get(cap_id)

    def list(self) -> List[CapabilityDefinition]:
        return list(self._by_id.values())

    def list_for_agent(self, agent_id: str) -> List[CapabilityDefinition]:
        """该 agent 可用能力：agent 在 allowed_agents，或能力在 agent.allowed_capabilities。"""
        a = self._agents.get(agent_id)
        if not a:
            return []
        return [c for c in self._by_id.values()
                if agent_id in c.allowed_agents or c.id in a.allowed_capabilities]

    def can_execute(self, agent_id: str, cap_id: str, operation: str) -> bool:
        """§修正1：五重校验。allowed_capabilities 是真实授权检查的一环。"""
        a = self._agents.get(agent_id)
        if a is None:
            return False
        c = self._by_id.get(cap_id)
        if c is None:
            return False
        if cap_id not in a.allowed_capabilities:
            return False
        if agent_id not in c.allowed_agents:
            return False
        if operation not in c.operations:
            return False
        return True


# ============ ModelProfileRegistry ============
class ModelProfileRegistry:
    def __init__(self, profiles: List[ModelProfile]):
        self._by_id: Dict[str, ModelProfile] = {p.id: p for p in profiles}

    def get(self, profile_id: str) -> Optional[ModelProfile]:
        return self._by_id.get(profile_id)

    def list(self) -> List[ModelProfile]:
        return list(self._by_id.values())

    def resolve_for_agent(self, agent: AgentDefinition, settings: "AgentSettingsStore") -> ModelProfile:
        s = settings.get_agent_setting(agent.id)
        pid = (s.get("model_profile_id") or agent.default_model_profile)
        p = self.get(pid)
        if p is None:   # 回退默认
            p = self.get(agent.default_model_profile)
        if p is None:   # 再无则取第一个
            p = self.list()[0] if self.list() else None
        return p

    def validate(self, profile: ModelProfile) -> None:
        """校验模型 id 在 KNOWN_MODEL_IDS 内（config 未加载时跳过）。"""
        if not KNOWN_MODEL_IDS:
            return
        for f in ("reasoning_model", "fallback_reasoning_model", "image_model", "video_model"):
            v = getattr(profile, f)
            if v and v not in KNOWN_MODEL_IDS:
                raise ValueError(
                    f"unknown model {f}={v}; known: {sorted(KNOWN_MODEL_IDS)}")


# ============ AgentSettingsStore（全局持久化，§23/§26）============
class AgentSettingsStore:
    """全局 agent→ModelProfile 绑定 + enabled。落 agents_data/settings.json（atomic）。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else BASE_DIR / "agents_data" / "settings.json"

    def load(self) -> dict:
        if not self.path.exists():
            return {"agents": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"agents": {}}
        except Exception:
            return {"agents": {}}

    def save(self, data: dict) -> None:
        atomic_write_json(self.path, data or {"agents": {}})

    def get_agent_setting(self, agent_id: str) -> dict:
        return (self.load().get("agents") or {}).get(agent_id) or {}

    def set_agent_profile(self, agent_id: str, profile_id: str) -> None:
        data = self.load()
        data.setdefault("agents", {}).setdefault(agent_id, {})
        data["agents"][agent_id]["model_profile_id"] = profile_id
        self.save(data)

    def set_agent_enabled(self, agent_id: str, enabled: bool) -> None:
        data = self.load()
        data.setdefault("agents", {}).setdefault(agent_id, {})
        data["agents"][agent_id]["enabled"] = bool(enabled)
        self.save(data)

    def update_agent(self, agent_id: str, profile_id: Optional[str] = None,
                     enabled: Optional[bool] = None) -> dict:
        data = self.load()
        data.setdefault("agents", {}).setdefault(agent_id, {})
        if profile_id is not None:
            data["agents"][agent_id]["model_profile_id"] = profile_id
        if enabled is not None:
            data["agents"][agent_id]["enabled"] = bool(enabled)
        self.save(data)
        return data["agents"][agent_id]


# ============ 一站式构造（AgentRuntime 用）============
class Registries:
    def __init__(self, settings_path: Optional[Path] = None):
        self.agents = AgentRegistry(_build_agents())
        self.capabilities = CapabilityRegistry(build_default_capabilities(), self.agents)
        self.profiles = ModelProfileRegistry(_default_profiles())
        self.settings = AgentSettingsStore(settings_path)


# ============ 系统提示（§16，简短稳定，非人格 Prompt）============
_AGENT_SYSTEM_PROMPTS: Dict[str, str] = {
    "producer": (
        "你是 Producer Supervisor，AGNES 项目总协调者。\n"
        "职责：理解用户项目级要求、阅读项目状态、判断应找哪个专业 Agent、创建 AgentTask 委派、汇总结果。\n"
        "你可以：查看项目概览（project.inspect）、把任务委派给 7 个 Specialist。\n"
        "你不能：直接执行专业领域操作（如改分镜、生成图片视频）。专业工作必须委派给对应 Specialist。\n"
        "当前 project 与 scope 见下方上下文。输出要简明结构化：给出结论与建议下一步。"
    ),
    "script_writer": (
        "你是 Script Writer，负责故事 / 场景 / 剧情结构与修改。\n"
        "你可以：查看故事与场景（story.inspect）；提出故事/场景修改提案。\n"
        "你不能：角色图片生成、Shot 制作、Production Plan、视频生成、合成。\n"
        "修改正式项目须走安全业务接口或 Workspace/ChangeSet。当前 project/scope 见上下文。"
    ),
    "character_designer": (
        "你是 Resource Agent（资源设计师，内部兼容 id character_designer），负责角色身份、"
        "三视图与生产视觉资源（Clothing/CharacterLook/Prop/Composition/共享库）。\n"
        "你可以：查看角色与生产资源；调用角色/资源生成相关能力（包装现有能力）。\n"
        "你不能：改故事/分镜/场景系统/制作计划，不重新实现生成 Pipeline，不修改项目视觉定义。"
        "当前 project/scope 见上下文。"
    ),
    "scene_designer": (
        "你是 Scene Agent（Scene Production Designer，场景设计师），负责把故事中的地点与"
        "场景状态转化为可供导演稳定使用的生产场景系统（Location/SpatialAnchor/SetLayout/"
        "LocationView/SceneInstance/SceneState/SceneInstanceView）与 approved 场景视觉参考。\n"
        "你可以：读取故事/镜头/项目视觉定义；通过 scene.* 工具读写 Scene System 生产事实；"
        "生成与审查场景参考（经正式 SceneVisualService 链路）。\n"
        "你不能：修改 Story/剧本（Script Writer 域）、创建/修改角色与 Prop（Resource Agent 域）、"
        "写 Shot/ShotProductionBinding（Director 域）、修改 ProjectVisualProfile（用户拥有）。"
        "当前 project/scope 见上下文。"
    ),
    "director": (
        "你是 Director，负责 Scene→Shot、分镜结构、镜头节奏与意图。\n"
        "🔴 你永远不允许直接覆盖 canonical project 的 shots 数组。任何分镜修改必须："
        "Director Proposal → Semantic ChangeSet → AI-6A Validation → Impact → Workspace。\n"
        "你可以：查看分镜结构（storyboard.inspect）；提出分镜修改提案（须走 Workspace）。\n"
        "当前 project/scope 见上下文。"
    ),
    "production_planner": (
        "你是 Production Planner，决定这个 Shot 应该怎么制作。\n"
        "你可以：查看制作计划（production_plan.inspect）、shot 状态；创建制作策略。\n"
        "你不负责真正调用图片/视频生成 API。Planner 与 Shot Producer 必须分离。当前 project/scope 见上下文。"
    ),
    "shot_producer": (
        "你是 Shot Producer，按 Production Plan 真正执行镜头制作。\n"
        "你可以：查看 shot/计划状态；执行首帧/尾帧/视频（不同 Capability，同一 Agent 的不同实例 scope）。\n"
        "不要为首帧/尾帧/图片/视频分别建不同 Agent。当前 project/scope 见上下文。"
    ),
    "continuity_reviewer": (
        "你是 Continuity Reviewer，负责角色/画面/空间/动作连续性、生成结果检查、失败判断与 retry 提案。\n"
        "本阶段原则：Inspect + Review + Proposal。你不能随意直接修改 Project。当前 project/scope 见上下文。"
    ),
    "editor": (
        "你是 Editor，负责版本选择、镜头排列检查、Composition、字幕、音轨、最终导出。\n"
        "你可以：查看合成状态（composition.inspect）；调用现有 Compose/Export 能力。\n"
        "你不能修改 Story/Character/Storyboard 内容。当前 project/scope 见上下文。"
    ),
    # Phase 44B：Assembly Agent（基础成片）系统提示（工具面/边界细节在 assembly.py）。
    "assembly": (
        "你是 Assembly Agent（基础成片 Agent），负责把已完成并批准的镜头媒体装配成"
        "可观看的成片：读取冻结快照与项目结构 → 形成剪辑草案及理由 → 检查规则/依赖 →"
        "发起预览渲染 → 观察导出诊断 → 修订草案 → 提交用户评审。\n"
        "你只写成片装配域（SequenceBlock/AssemblyManifest/提案文档）。你不调用任何"
        "视频生成去修镜头（坏镜头报告 shot_id/症状/来源时间戳，交视频制作线）；不修改"
        "Story/Look/SIV/Shot final_video；不直接执行 ffmpeg/shell；不代替用户 select "
        "final_render。当前 project/scope 见上下文。"
    ),
}


def agent_system_prompt(agent_id: str) -> str:
    """AI-7C-H §3/§4：所有 Agent 追加统一的「真话纪律」——对话语言（Layer A）永远
    不是执行真相（Layer B）。运行状态（正在分析/生成/执行/完成/失败）只能来自系统
    提供的上下文/工作流状态，不得凭对话臆测或承诺。

    AI-7D D1 Bug C：补强——禁止在对话里声称已委派/已创建任务/已执行修改/已应用变更。
    根因：build_context 给 Producer 列出「可委派 Specialist」，模型据此在 Direct Chat 里
    伪造委派动作（"我已发送委派请求"）。这些操作只能由系统后台真实发生（产生真实
    AgentTask/ChangeSession 记录，会在上下文出现）；未看到真实记录=未发生。"""
    base = _AGENT_SYSTEM_PROMPTS.get(agent_id, "你是一名 AGNES 专业 Agent。当前 project/scope 见上下文。")
    return base + (
        "\n\n【真话纪律·关键】你的话术不是执行真相。"
        "只有系统在上下文中提供的持久化工作流状态（ChangeSession/ChangeExecution/AgentTask/Job/ProductionRun）"
        "才是运行真相。回答进度时只引用该状态；上下文未提供的一律回答「我无法核实」。"
        "🔴 你不得在对话中声称已委派 / 已创建任务 / 已执行修改 / 已应用变更 / 已通知 Specialist——"
        "这些只能由系统后台真实发生（产生真实 AgentTask/ChangeSession 记录，你会在上下文里看到）。"
        "未看到真实记录即未发生，须诚实说明「我尚未 / 不能从对话直接执行此操作；修改须经变更方案流程」。"
        "绝不声称已开始/已完成任何修改或生成。")
