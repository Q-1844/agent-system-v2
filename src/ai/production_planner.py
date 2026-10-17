# src/ai/production_planner.py
"""AI 制作规划师（Phase AI-4A，规格 提示词.md §一~§二十三）。

职责窄（受控 Workflow，不是开放式 Agent）：一个故事段已确定的 Shot[] -> 为每个 Shot 决定
opening_source + link_mode + reason -> 用户确认 -> 保存独立 Production Plan。
不生成首帧/尾帧/视频、不调度 JobQueue、不写正式 shot.opening_source（§六：plan 是未来执行意图，
正式 opening_source 由 AI-4B Executor 在真正执行当前 Shot 时应用）。

🔴 设计核心（§九/§十/§十三）：AI 只负责语义判断，输出 opening_source / link_mode / reason。
source_shot_id / requires_previous_video_end / generate_first_frame / generate_last_frame /
auto_select_policy 全部由普通代码 100% 确定，不让模型负责本来代码可以确定的字段。

opening_source 与 link_mode 是两个独立维度（§三，Phase 6A 设计）：
  opening_source = 这一镜从哪里开始（independent / reference_previous / inherit_previous）
  link_mode      = 这一镜视频怎样发展（i2v / keyframes）
  none 不作为 AI 默认（§二）：link_mode.py 里 none 有独立语义（全片共用一张参考图，各镜独立），
  但那是项目级模式，不是 per-shot 连续性判断，故 AI 只在 i2v/keyframes 间选择。
"""
from __future__ import annotations
import hashlib
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from src.core.atomic import atomic_write_json
from src.ai.story_workflow import _parse_json_object

_log = logging.getLogger(__name__)


# ============ 常量 ============
OPENING_SOURCES = ("independent", "reference_previous", "inherit_previous")
LINK_MODES = ("i2v", "keyframes")   # none 有独立语义但非 per-shot AI 默认（§二）


# ============ 数据结构（§七）============
class PlannedShot(BaseModel):
    """单个镜头的制作规划。opening_source/link_mode/reason 之外全是代码推导。"""
    shot_id: str
    opening_source: str
    link_mode: str
    source_shot_id: Optional[str] = None
    generate_first_frame: bool
    generate_last_frame: bool
    generate_video: bool = True
    requires_previous_video_end: bool
    auto_select_policy: str = "seeded_random"
    reason: str = ""
    # Phase56 A2（reference_policy v2）：independent opening 的显式切镜依据
    # （same_scene_recam/scene_change/time_jump）——activate 时随 opening_source
    # 落 shot.opening_cut_reason；v2 策略下未声明的独立首帧在提交前被拒绝。
    cut_reason: Optional[str] = None


class SceneProductionPlan(BaseModel):
    """一个故事段的制作计划。独立存储（§七/§二十一），不写回 Shot。"""
    plan_id: str
    project_name: str
    scene_id: str
    scene_summary_snapshot: str = ""
    scene_characters_snapshot: list[str] = Field(default_factory=list)
    shot_structure_snapshot: list[dict] = Field(default_factory=list)   # §八
    run_seed: int = 0
    overview: str = ""
    shots: list[PlannedShot] = Field(default_factory=list)
    status: str = "draft"            # draft | approved（stale 为派生标志 is_stale，不持久化）
    created_at: float = 0.0
    approved_at: Optional[float] = None


# ============ run_seed + seeded_random 候选选择（§十一）============
def new_run_seed() -> int:
    """Production Plan 创建时生成稳定 int（§十一）。不使用 Python hash()（跨进程不稳定）。"""
    return secrets.randbelow(2 ** 31)


def choose_candidate(run_seed: int, shot_id: str, batch_id: str, candidates: list):
    """seeded_random 自动候选选择（§十一）。同一 run_seed+shot_id+batch_id -> 同一选择，可复现。
    🔴 本阶段不调用（AI-4A 不做候选选择）；AI-4B Executor 在多成功候选时使用。

    - 0 成功候选 -> raise ValueError("NoCandidateAvailable")
    - 1 成功候选 -> 直接采用
    - 多成功候选 -> sha256(run_seed:shot_id:batch_id) 取前 8 字节 mod len 确定性选择
    """
    successful = [c for c in candidates
                  if (getattr(c, "status", None) == "done")
                  or (isinstance(c, dict) and c.get("status") == "done")]
    if not successful:
        raise ValueError("NoCandidateAvailable")
    if len(successful) == 1:
        return successful[0]
    raw = f"{run_seed}:{shot_id}:{batch_id}".encode()
    digest = hashlib.sha256(raw).digest()
    index = int.from_bytes(digest[:8], "big") % len(successful)
    return successful[index]


# ============ System Prompt（§十五）============
_PLANNER_SYSTEM = """你是一名 AI 视频制作规划师。你不是编剧，也不是摄影参数编辑器。

你的任务是根据一个故事段中已经确定的 Shot，决定每个 Shot 最适合怎样开始，以及是否值得使用明确尾帧。

你负责：
1. 判断 Shot 与上一 Shot 的视觉连续关系。
2. 选择 opening_source：independent / reference_previous / inherit_previous
3. 选择 link_mode：i2v / keyframes
4. 给出简短理由。

你不负责：改写 Shot、创建 Shot、修改角色、写图片 prompt、写视频 prompt、生成图片、生成视频。

连续关系原则：
- independent：没有必要直接继承上一镜。场景第一镜必须 independent。时间明显跳跃、地点或空间状态明显改变、人物状态发生无法从上一镜自然连续得到的大跳跃、新的建立镜头、与上一镜无视觉连续需求 -> independent。
- reference_previous：需要保持上一镜的人物/环境/状态连续，但本镜需要新的构图或视觉表达。同地点、时间连续、主要人物连续、需要保持角色环境状态一致但需要新构图或切换观察重点 -> reference_previous。
- inherit_previous：当前镜头真正从上一镜动作或状态直接继续，第一帧应使用上一镜实际视频末帧。真正的动作接力（例如「角色开始抬起扫描装置」->「扫描装置继续展开并完成启动」；「角色向门口行走」->「从上一镜结束姿态直接继续前进」）。如果明显需要切机位/重构图，不要用 inherit_previous，优先 reference_previous。不要因为「两个镜头都有同一个角色」就随便 inherit。

视频方式：
- i2v：结束画面不需要精确控制。简短动作、表情变化、环境运动、转头、轻微移动、target_duration_s 较短且动作简单、不需要严格指定结束姿态。
- keyframes：镜头有明确且重要的结束状态，控制结束画面对叙事连续性有明显帮助。起身/坐下/门从关闭变为完全打开/手臂从下垂变为完全抬起/装置从关闭变成完全展开/较长 Shot 有明确结束画面要求/下一镜连续性需要当前镜结束状态更可控。
- 不要为了显得复杂而大量使用 keyframes。时长只是参考，核心判断是「这个 Shot 是否有值得显式控制的结束画面」。

规则：
- Scene 第一镜必须 independent。
- 一个 Shot = 一个连续镜头。
- 不要跨 Scene 建立连续关系（本任务只在当前 Scene 内判断）。

只返回一个 JSON 对象，不要有任何解释文字：
{"overview":"一句话概述本次制作规划","shots":[{"shot_id":"s02_c01","opening_source":"independent","link_mode":"keyframes","reason":"这是故事段第一镜，且存在明确的结束站立状态。"},{"shot_id":"s02_c02","opening_source":"reference_previous","link_mode":"i2v","reason":"人物与环境连续，但这一镜需要新的画面构图。"}]}
shot_id 必须与输入中的 shot_id 完全一致，每个 Shot 都要返回。"""


# ============ Workflow ============
class ProductionPlannerWorkflow:
    """AI 制作规划师。client 为 AgnesClient 实例（测试可注入 fake）。"""

    def __init__(self, client):
        self._client = client

    async def _chat_json(self, messages: list[dict], **kw) -> Optional[dict]:
        """调 chat(json_mode=True) + 解析 JSON。parse 失败自动重试一次（同 director）。"""
        raw = await self._client.chat(messages, json_mode=True, **kw)
        obj = _parse_json_object(raw)
        if obj is not None:
            return obj
        raw2 = await self._client.chat(
            messages + [{"role": "system",
                         "content": "重要：只返回一个合法的 JSON 对象，以 { 开头，不要任何说明文字、代码块标记或其他内容。"}],
            json_mode=True, **kw)
        return _parse_json_object(raw2)

    async def plan_scene(self, project, scene) -> dict:
        """一个故事段 -> AI 原始规划（{overview, shots:[{shot_id, opening_source, link_mode, reason}]}）。
        不在此 derive；derive 由 sanitize_plan 完成（§十三：AI 负责语义，代码负责执行计划）。
        输入上下文严格按 §十二：只叙事字段，不含 base64/批次/video_id/路径。"""
        sid = scene.scene_id
        scene_chars = list(scene.characters)

        # 角色描述（只 name + description_en，§十二）
        char_descs = []
        for c in project.characters:
            if c.name in scene_chars:
                char_descs.append(f"- {c.name}：{c.description_en}" if c.description_en else f"- {c.name}")

        # 当前 Scene 全部 Shot（按顺序，只叙事字段，§十二）
        shots_block = ""
        if scene.shots:
            lines = []
            for sh in scene.shots:
                dur = sh.target_duration_s if sh.target_duration_s is not None else "未指定"
                chars = "、".join(sh.characters) if sh.characters else "无"
                lines.append(f"- {sh.shot_id}：{sh.description or '（未填写意图）'}｜出场：{chars}｜目标时长：{dur}s")
            shots_block = "\n当前故事段的镜头（按顺序，判断每个镜头与上一镜的关系）：\n" + "\n".join(lines)

        style = project.style.suffix() if project.style else ""
        ratio = project.style.aspect_ratio if project.style else "16:9"

        user = (
            f"当前故事段 {sid}：\n{scene.summary or '（未填写故事）'}\n\n"
            f"出场角色：{('、'.join(scene_chars)) if scene_chars else '无'}"
            + (f"\n角色设定：\n" + "\n".join(char_descs) if char_descs else "")
            + shots_block
            + f"\n\n项目视觉风格（弱参考）：{style or 'anime, cinematic'}｜宽高比：{ratio}"
            + "\n\n请为每个镜头决定 opening_source 与 link_mode，并给出简短理由。"
        )
        obj = await self._chat_json(
            [{"role": "system", "content": _PLANNER_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.5, max_tokens=2048)
        if obj is None:
            raise ValueError("AI 未返回可解析的 JSON")
        return obj


# ============ snapshot（§八）============
def shot_structure_snapshot(scene) -> list[dict]:
    """当前 Scene 的镜头结构快照（stale 比对用，§八）。只含叙事字段，不含资产状态。"""
    return [
        {"shot_id": sh.shot_id,
         "description": sh.description or "",
         "characters": list(sh.characters),
         "target_duration_s": sh.target_duration_s}
        for sh in scene.shots
    ]


# ============ derivation（§九/§十）============
def _build_planned_shots(scene, decisions: dict) -> list[PlannedShot]:
    """根据 (shot_id -> (opening_source_raw, link_mode_raw, reason)) 构建 PlannedShot[]。
    应用 sanitizer 规则（§十四）+ derivation（§九/§十）：
      - 第一镜强制 independent
      - 无效 opening_source -> independent；无效 link_mode -> i2v
      - source_shot_id = 紧邻上一镜（reference/inherit）；requires_previous_video_end 同步
      - generate_first_frame = opening_source != inherit_previous
      - generate_last_frame = link_mode == keyframes
      - generate_video = True
      - auto_select_policy = seeded_random
    decisions 缺失的 Shot（AI 漏返回）-> 安全默认 independent + i2v（§十四）。
    """
    shots = scene.shots
    planned: list[PlannedShot] = []
    for i, sh in enumerate(shots):
        raw = decisions.get(sh.shot_id)
        used_default = raw is None
        if raw is not None:
            opening, mode, reason = raw
            opening = str(opening or "").strip()
            mode = str(mode or "").strip()
            reason = str(reason or "").strip()
        else:
            opening, mode, reason = "", "", ""

        # 第一镜强制 independent（§十四）
        if i == 0:
            opening = "independent"
        if opening not in OPENING_SOURCES:
            opening = "independent"
        if mode not in LINK_MODES:
            mode = "i2v"

        # derivation（§九）：reference/inherit 只能来源于紧邻上一镜；第一镜已强制 independent
        if opening in ("reference_previous", "inherit_previous") and i > 0:
            source_shot_id = shots[i - 1].shot_id
            requires_prev = True
        else:
            source_shot_id = None
            requires_prev = False

        reason_text = reason
        if used_default and not reason_text:
            reason_text = "（系统默认策略：模型未返回该镜头规划）"

        planned.append(PlannedShot(
            shot_id=sh.shot_id,
            opening_source=opening,
            link_mode=mode,
            source_shot_id=source_shot_id,
            generate_first_frame=(opening != "inherit_previous"),
            generate_last_frame=(mode == "keyframes"),
            generate_video=True,
            requires_previous_video_end=requires_prev,
            auto_select_policy="seeded_random",
            reason=reason_text,
        ))
    return planned


def _decisions_from_ai(ai_result: dict) -> dict:
    """从 AI 原始输出抽 {shot_id: (opening_source, link_mode, reason)}。
    AI 返回不存在的 shot_id 在此不过滤（sanitize_plan 按 scene.shots 索引，自动忽略多余）。"""
    out = {}
    for s in (ai_result.get("shots") or []):
        if not isinstance(s, dict):
            continue
        sid = str(s.get("shot_id") or "").strip()
        if not sid:
            continue
        out[sid] = (s.get("opening_source"), s.get("link_mode"), s.get("reason"))
    return out


def sanitize_plan(scene, ai_result: dict, *, run_seed: int, project_name: str) -> SceneProductionPlan:
    """deterministic sanitizer（§十四）：AI 原始输出 -> 合法 SceneProductionPlan。
    AI 返回不存在的 shot_id -> 忽略；AI 漏 Shot -> 安全默认。derive 全由代码完成。"""
    decisions = _decisions_from_ai(ai_result)
    planned = _build_planned_shots(scene, decisions)
    overview = str(ai_result.get("overview") or "").strip()
    return SceneProductionPlan(
        plan_id=f"plan_{scene.scene_id}",
        project_name=project_name,
        scene_id=scene.scene_id,
        scene_summary_snapshot=scene.summary or "",
        scene_characters_snapshot=list(scene.characters),
        shot_structure_snapshot=shot_structure_snapshot(scene),
        run_seed=run_seed,
        overview=overview,
        shots=planned,
        status="draft",
        created_at=0.0,   # 由 router 填 time.time()
    )


def rederive_from_edits(scene, plan: SceneProductionPlan,
                        edits: list[dict]) -> SceneProductionPlan:
    """用户手动改 opening_source/link_mode 后重建 PlannedShot[]（§十八）。
    edits: [{shot_id, opening_source, link_mode, reason}]（来自 UI）。
    保留 run_seed / snapshot / status（若原 approved 则降级 draft，需重新确认）。
    不刷新 shot_structure_snapshot（snapshot 反映创建时 Scene 结构；若 Scene 已变 -> stale，由调用方拦截）。
    """
    decisions = {}
    for e in edits:
        sid = str(e.get("shot_id") or "").strip()
        if sid:
            decisions[sid] = (e.get("opening_source"), e.get("link_mode"), e.get("reason"))
    planned = _build_planned_shots(scene, decisions)
    plan.shots = planned
    if plan.status == "approved":
        plan.status = "draft"     # 编辑后需重新确认
    return plan


# ============ stale 检测（§八/§二十二）============
def is_plan_stale(plan: SceneProductionPlan, scene) -> Optional[str]:
    """返回 stale 原因（None = 未过期）。
    stale 触发：Scene.summary / Scene.characters / Shot 增删/顺序 / Shot.description /
    Shot.characters / target_duration_s 明显变化（>0.5s）。
    不触发：生成资产状态 / video_end_frame / Character.views / JobQueue / 候选变化（§八）。"""
    if (scene.summary or "") != (plan.scene_summary_snapshot or ""):
        return "故事段文本已变化"
    if list(scene.characters) != list(plan.scene_characters_snapshot):
        return "故事段出场角色已变化"

    cur = shot_structure_snapshot(scene)
    snap = plan.shot_structure_snapshot or []
    if len(cur) != len(snap):
        return "镜头数量已变化"
    for c, s in zip(cur, snap):
        if c["shot_id"] != s.get("shot_id"):
            return "镜头顺序或编号已变化"
        if c["description"] != s.get("description", ""):
            return f"镜头 {c['shot_id']} 描述已变化"
        if c["characters"] != (s.get("characters") or []):
            return f"镜头 {c['shot_id']} 出场角色已变化"
        cur_d = c["target_duration_s"]
        snap_d = s.get("target_duration_s")
        if (cur_d is None) != (snap_d is None):
            return f"镜头 {c['shot_id']} 时长已变化"
        if cur_d is not None and snap_d is not None and abs(cur_d - snap_d) > 0.5:
            return f"镜头 {c['shot_id']} 时长已变化"
    return None


# ============ 共享业务 + 计划 IO（AI-6E §8 抽取，router 与 Agent capability 共用）============
# 原物理位置 routers/production_planner.py。AI-6E 为满足 src/agents/* 不 import router
# 的纪律（§8），把 plan_production_business + 计划 IO 抽到本中性位置（src/ai，不依赖
# router/deps，纯靠参数）。router 与 Agent capability 调同一函数，不复制业务逻辑。

async def plan_production_business(project, scene, project_name: str, client) -> SceneProductionPlan:
    """AI 制作规划共享 helper。包 ProductionPlannerWorkflow.plan_scene + sanitize_plan +
    填 created_at。不落盘 plan（落盘由调用方决定）。
    供 routers/production_planner.plan 端点与 AI-6E Agent production_plan.create 共用。
    raises ValueError（无 Shot）。"""
    if not scene.shots:
        raise ValueError("请先创建镜头或使用 AI 设计分镜。")
    wf = ProductionPlannerWorkflow(client)
    ai_result = await wf.plan_scene(project, scene)
    plan = sanitize_plan(scene, ai_result,
                         run_seed=new_run_seed(), project_name=project_name)
    plan.created_at = time.time()
    return plan


def plans_path(project_dir) -> Path:
    """production_plans.json 路径（projects/{p}/output/ai/，by scene_id）。"""
    return Path(project_dir) / "output" / "ai" / "production_plans.json"


def read_plans(project_dir) -> dict:
    """读全部计划 {scene_id: SceneProductionPlan}。解析失败条目跳过（warn）。"""
    p = plans_path(project_dir)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        out = {}
        for sid, d in (raw or {}).items():
            try:
                out[sid] = SceneProductionPlan.model_validate(d)
            except Exception as e:
                _log.warning("制作计划 %s 解析失败: %s", sid, e)
        return out
    except Exception as e:
        _log.warning("读取制作计划失败: %s", e)
        return {}


def write_plans(project_dir, plans: dict) -> None:
    """原子写全部计划。plans: {scene_id: SceneProductionPlan}。"""
    atomic_write_json(plans_path(project_dir),
                      {sid: p.model_dump() for sid, p in plans.items()})
