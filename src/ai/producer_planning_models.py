# src/ai/producer_planning_models.py
"""Producer 规划契约模型（PR A）。

设计依据：《producer 自主规划与续作：执行指令 v2》§2（任务输入与输出契约）。
本模块是纯数据 + 确定性校验：零 I/O、零 AI 调用、零仓库状态读取。

三个层次：
1. TaskSpec        —— 任务身份（规划开始前确定，绑定初始 request digest）；
2. ProducerPlanningResult —— 受控规划器输出（PROPOSED；冻结与 plan_fingerprint 属 PR B1）；
3. ProjectProductionInspect 的投影类型在本模块定义（adapter 在
   src/ai/project_production_inspect.py，复用既有 inspect_project，只读）。

纪律（与执行指令对齐）：
- approximate 的容差必须由策略明确给出并写入证据，不得把"大约 120 秒"
  伪装成用户精确要求 120.000 秒；
- unspecified 不得自造 target_seconds；
- SceneBudget 可行性用确定性公式校验，不依赖 LLM 自检字段；
- execution_bounds 各项必须有可追溯来源，分镜前估算不得冒充已授权媒体额度。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 常量（枚举与物理约束）
# ---------------------------------------------------------------------------

TASK_SPEC_SCHEMA = "producer-task-v1"
PLAN_SCHEMA = "producer-plan-v1"

INTENTS = ("create", "resume_run", "complete_project", "add_assets",
           "extend_existing", "sequel")
#: 不携带 source_project 的意图（全新任务）。注意：sequel 虽是 new_output，
#: 但必须指明续谁的（continuity anchors 来源），所以要求 source_project。
NEW_OUTPUT_INTENTS = ("create",)

DURATION_MODES = ("exact", "range", "approximate", "unspecified")
DURATION_SCOPES = ("new_output", "additional_content", "final_total",
                   "not_applicable")
DURATION_SOURCES = ("user_explicit", "producer_inferred")

OUTPUT_KINDS = ("final_video", "asset_set")
PLAN_STATUSES = ("PROPOSED",)  # FROZEN 由 PR B1 冻结器写入，PR A 只产出 PROPOSED

#: 单镜时长物理窗口。来源：tools/full_project_cloud_acceptance.py
#: validate_planned_project 的 "4 <= d <= 11"（PR B1 统一引用，先数值对齐）
SHOT_SECONDS_MIN = 4
SHOT_SECONDS_MAX = 11

#: approximate 软目标的默认容差（±15%）。策略可按任务覆盖，必须写入证据。
APPROXIMATE_DEFAULT_TOLERANCE = 0.15

RESOURCE_KEYS = ("text_calls", "image_calls", "video_seconds")

_SCENE_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,31}$")


class PlanningContractError(ValueError):
    """契约校验失败（确定性、可审计；不用于吞掉业务错误）。"""


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def canonical_json(payload: Dict[str, Any]) -> str:
    """稳定 JSON 序列化（键序无关、紧凑、非 ASCII 不转义）。

    与 tools/full_project_cloud_acceptance.request_digest 的既有手法同构，
    保证将来冻结指纹/身份哈希可以互相验证。
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _positive_int(value: Optional[int], what: str) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PlanningContractError(f"{what} 必须是正整数, 得到: {value!r}")
    return value


# ---------------------------------------------------------------------------
# DurationIntent：显式时间意图 vs 内部执行估计
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DurationIntent:
    """用户/系统对"这个产出要多长"的原始意图。

    - exact        用户精确要求 target_seconds（如"必须 120 秒"）
    - range        用户给出 range_seconds=(lo, hi) 硬窗口
    - approximate  软目标（"大概/左右"）；内部可接受窗口由容差策略推导,
                   证据中必须标记为 policy_derived, 不得伪装用户精确值
    - unspecified  用户没提时长；target/range 必须为空, 由内容驱动规划
    """

    mode: str = "unspecified"
    scope: str = "new_output"
    target_seconds: Optional[int] = None
    range_seconds: Optional[Tuple[int, int]] = None
    source: str = "user_explicit"
    #: approximate 的容差覆盖（0, 1) 开区间浮点；None 用默认策略
    tolerance: Optional[float] = None

    def __post_init__(self) -> None:
        if self.mode not in DURATION_MODES:
            raise PlanningContractError(
                f"duration.mode 非法: {self.mode!r} (合法: {DURATION_MODES})")
        if self.scope not in DURATION_SCOPES:
            raise PlanningContractError(
                f"duration.scope 非法: {self.scope!r} (合法: {DURATION_SCOPES})")
        if self.source not in DURATION_SOURCES:
            raise PlanningContractError(
                f"duration.source 非法: {self.source!r} (合法: {DURATION_SOURCES})")
        if self.mode == "exact":
            if self.target_seconds is None:
                raise PlanningContractError("exact 模式必须给 target_seconds")
            if self.range_seconds is not None:
                raise PlanningContractError("exact 模式不得携带 range_seconds")
        elif self.mode == "range":
            if self.range_seconds is None:
                raise PlanningContractError("range 模式必须给 range_seconds")
            lo, hi = self.range_seconds
            if not (0 < lo <= hi):
                raise PlanningContractError(
                    f"range_seconds 必须满足 0 < lo <= hi, 得到: {self.range_seconds!r}")
            if self.target_seconds is not None:
                raise PlanningContractError("range 模式不得携带 target_seconds")
        elif self.mode == "approximate":
            if self.target_seconds is None:
                raise PlanningContractError("approximate 模式必须给 target_seconds")
            if self.range_seconds is not None:
                raise PlanningContractError(
                    "approximate 模式不得携带 range_seconds (容差由策略推导)")
            if self.tolerance is not None and not (
                    isinstance(self.tolerance, float) and 0.0 < self.tolerance < 1.0):
                raise PlanningContractError(
                    f"tolerance 必须在 (0, 1) 开区间, 得到: {self.tolerance!r}")
        else:  # unspecified
            if self.target_seconds is not None or self.range_seconds is not None:
                raise PlanningContractError(
                    "unspecified 模式不得自造 target_seconds / range_seconds")
        _positive_int(self.target_seconds, "duration.target_seconds")

    # ---- 窗口推导 ----

    def effective_window(self) -> Optional[Tuple[int, int]]:
        """返回内部可接受窗口 (lo, hi)；unspecified 返回 None。

        exact -> (t, t)；range -> 原窗口；approximate -> 容差推导窗口
        （floor(t*(1-tol)) / ceil(t*(1+tol))），来源是策略而非用户精确值。
        """
        if self.mode == "unspecified":
            return None
        if self.mode == "exact":
            return (self.target_seconds, self.target_seconds)
        if self.mode == "range":
            return tuple(self.range_seconds)  # type: ignore[return-value]
        tol = self.tolerance if self.tolerance is not None \
            else APPROXIMATE_DEFAULT_TOLERANCE
        t = self.target_seconds
        return (max(1, int(math.floor(t * (1.0 - tol)))),
                int(math.ceil(t * (1.0 + tol))))

    def to_evidence(self) -> Dict[str, Any]:
        """写入审计证据的形态：窗口 + 窗口来源声明，杜绝假精度。"""
        window = self.effective_window()
        if window is None:
            window_source = None
        elif self.mode in ("exact", "range"):
            window_source = "user_explicit"
        else:
            window_source = "policy_derived_not_user_precision"
        return {
            "mode": self.mode, "scope": self.scope, "source": self.source,
            "target_seconds": self.target_seconds,
            "range_seconds": list(self.range_seconds)
            if self.range_seconds else None,
            "tolerance_used": (
                self.tolerance if self.tolerance is not None
                else APPROXIMATE_DEFAULT_TOLERANCE)
            if self.mode == "approximate" else None,
            "effective_window_seconds": list(window) if window else None,
            "effective_window_source": window_source,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode, "scope": self.scope,
            "target_seconds": self.target_seconds,
            "range_seconds": list(self.range_seconds)
            if self.range_seconds else None,
            "source": self.source, "tolerance": self.tolerance,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DurationIntent":
        rng = d.get("range_seconds")
        return cls(
            mode=d.get("mode") or "unspecified",
            scope=d.get("scope") or "new_output",
            target_seconds=d.get("target_seconds"),
            range_seconds=tuple(rng) if rng else None,
            source=d.get("source") or "user_explicit",
            tolerance=d.get("tolerance"),
        )


# ---------------------------------------------------------------------------
# ResourceCeiling：三维资源上限
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResourceCeiling:
    """text / image / video_seconds 三维上限（分镜前的估算与获授权额度分离）。"""

    text_calls: Optional[int] = None
    image_calls: Optional[int] = None
    video_seconds: Optional[int] = None

    def __post_init__(self) -> None:
        _positive_int(self.text_calls, "resource_ceiling.text_calls")
        _positive_int(self.image_calls, "resource_ceiling.image_calls")
        _positive_int(self.video_seconds, "resource_ceiling.video_seconds")

    def to_dict(self) -> Dict[str, Any]:
        return {"text_calls": self.text_calls, "image_calls": self.image_calls,
                "video_seconds": self.video_seconds}


# ---------------------------------------------------------------------------
# TaskSpec：任务身份（规划前确定）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TaskSpec:
    """规划开始前的任务身份；task_spec_sha256 是阶段 0 的身份锚点。"""

    intent: str
    request_text: str
    source_project: Optional[str] = None
    duration: DurationIntent = field(
        default_factory=lambda: DurationIntent(mode="unspecified",
                                                scope="not_applicable"))
    style_hint: Optional[str] = None
    resource_ceiling: ResourceCeiling = field(default_factory=ResourceCeiling)

    def __post_init__(self) -> None:
        if self.intent not in INTENTS:
            raise PlanningContractError(
                f"intent 非法: {self.intent!r} (合法: {INTENTS})")
        text = self.request_text
        if not isinstance(text, str) or not text.strip() \
                or len(text) > 12000:
            raise PlanningContractError(
                "request_text 必须是 1-12000 字符的非空字符串")
        if self.intent in NEW_OUTPUT_INTENTS:
            if self.source_project is not None:
                raise PlanningContractError(
                    f"{self.intent} 意图不得携带 source_project")
        else:
            if not (isinstance(self.source_project, str)
                    and self.source_project.strip()):
                raise PlanningContractError(
                    f"{self.intent} 意图必须携带 source_project")

    def canonical_payload(self) -> Dict[str, Any]:
        return {
            "schema_version": TASK_SPEC_SCHEMA,
            "intent": self.intent,
            "request_text": self.request_text,
            "source_project": self.source_project,
            "duration": self.duration.to_dict(),
            "style_hint": self.style_hint,
            "resource_ceiling": self.resource_ceiling.to_dict(),
        }

    def sha256(self) -> str:
        """任务身份摘要（阶段 0 崩溃后重新规划仍复用同一身份）。"""
        return _sha256_hex(canonical_json(self.canonical_payload()))

    def to_dict(self) -> Dict[str, Any]:
        return self.canonical_payload()

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskSpec":
        return cls(
            intent=d["intent"],
            request_text=d["request_text"],
            source_project=d.get("source_project"),
            duration=DurationIntent.from_dict(d.get("duration") or {}),
            style_hint=d.get("style_hint"),
            resource_ceiling=ResourceCeiling(
                **{k: (d.get("resource_ceiling") or {}).get(k)
                   for k in RESOURCE_KEYS}),
        )


# ---------------------------------------------------------------------------
# SceneBudget：不改 Scene Schema 的场次约束（PR B1 注入 director_note）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SceneBudget:
    """单场预算：镜头数范围 + 秒窗。Director 在范围内设计镜头。

    可行性（确定性公式，不依赖 LLM 自检）：
      shot_min * per_shot_min_s <= max_s   （下界可达）
      shot_max * per_shot_max_s >= min_s   （上界可达）
      target_s（若给）落在 [min_s, max_s]
    """

    scene_id: str
    min_s: int
    max_s: int
    shot_min: int
    shot_max: int
    target_s: Optional[int] = None
    per_shot_min_s: int = SHOT_SECONDS_MIN
    per_shot_max_s: int = SHOT_SECONDS_MAX

    def __post_init__(self) -> None:
        if not _SCENE_ID_RE.match(self.scene_id or ""):
            raise PlanningContractError(f"scene_id 非法: {self.scene_id!r}")
        # 全零预算 = 本场无新增视频工作（asset_set 的纯图片/参考资产场）。
        # 除此之外的字段都必须为正整数并过可行性校验。
        _zeros = (self.min_s, self.max_s, self.shot_min, self.shot_max)
        if _zeros == (0, 0, 0, 0):
            if self.target_s is not None:
                raise PlanningContractError(
                    f"[{self.scene_id}] 全零预算（无新增视频）不得携带 target_s")
            return
        for name in ("min_s", "max_s", "shot_min", "shot_max",
                     "per_shot_min_s", "per_shot_max_s"):
            _positive_int(getattr(self, name), f"scene_budget.{name}")
        if self.min_s > self.max_s:
            raise PlanningContractError(
                f"[{self.scene_id}] min_s > max_s: {self.min_s} > {self.max_s}")
        if self.shot_min > self.shot_max:
            raise PlanningContractError(
                f"[{self.scene_id}] shot_min > shot_max: "
                f"{self.shot_min} > {self.shot_max}")
        if self.shot_min * self.per_shot_min_s > self.max_s:
            raise PlanningContractError(
                f"[{self.scene_id}] 不可行: shot_min({self.shot_min}) * "
                f"{self.per_shot_min_s}s > max_s({self.max_s})")
        if self.shot_max * self.per_shot_max_s < self.min_s:
            raise PlanningContractError(
                f"[{self.scene_id}] 不可行: shot_max({self.shot_max}) * "
                f"{self.per_shot_max_s}s < min_s({self.min_s})")
        if self.target_s is not None \
                and not (self.min_s <= self.target_s <= self.max_s):
            raise PlanningContractError(
                f"[{self.scene_id}] target_s({self.target_s}) 超出 "
                f"[{self.min_s}, {self.max_s}]")

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "scene_id": self.scene_id, "target_s": self.target_s,
            "min_s": self.min_s, "max_s": self.max_s,
            "shot_min": self.shot_min, "shot_max": self.shot_max,
            "per_shot_min_s": self.per_shot_min_s,
            "per_shot_max_s": self.per_shot_max_s,
        }


# ---------------------------------------------------------------------------
# ProducerPlanningResult：受控规划器输出（PR A 只产出 PROPOSED）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlannedScene:
    scene_id: str
    summary: str
    title: Optional[str] = None

    def __post_init__(self) -> None:
        if not _SCENE_ID_RE.match(self.scene_id or ""):
            raise PlanningContractError(f"scene_id 非法: {self.scene_id!r}")
        if not (isinstance(self.summary, str) and self.summary.strip()):
            raise PlanningContractError(
                f"[{self.scene_id}] summary 必须非空")


def auto_scene_ids(count: int) -> List[str]:
    """新建规划的顺序 scene_id 分配（s01..sNN，与仓库既有风格一致）。"""
    if not isinstance(count, int) or count <= 0 or count > 99:
        raise PlanningContractError(f"scene 数量非法: {count!r}")
    return [f"s{i:02d}" for i in range(1, count + 1)]


@dataclass(frozen=True)
class ProducerPlanningResult:
    """规划器输出契约 v1（schema: producer-plan-v1）。

    PlanningDecision 是同一概念的别名（SKILL 草案用语），保留两者等价。
    冻结（FROZEN + plan_fingerprint + CAS）属 PR B1；PR A 只产出 PROPOSED。
    """

    task_spec_sha256: str
    intent: str
    story_text: str
    scenes: Tuple[PlannedScene, ...]
    scene_budgets: Tuple[SceneBudget, ...]
    execution_bounds: Dict[str, Any]
    output_kind: str = "final_video"
    style: Dict[str, Any] = field(default_factory=dict)
    #: 继续类意图的增量动作（reuse_asset / generate_missing / verify_existing）
    incremental_actions: Tuple[Dict[str, Any], ...] = ()
    status: str = "PROPOSED"

    def __post_init__(self) -> None:
        if self.status not in PLAN_STATUSES:
            raise PlanningContractError(
                f"status 非法: {self.status!r} (PR A 只允许 PROPOSED)")
        if self.intent not in INTENTS:
            raise PlanningContractError(f"intent 非法: {self.intent!r}")
        if self.output_kind not in OUTPUT_KINDS:
            raise PlanningContractError(
                f"output_kind 非法: {self.output_kind!r}")
        if not (isinstance(self.story_text, str) and self.story_text.strip()):
            raise PlanningContractError("story_text 必须非空")
        if not self.scenes:
            raise PlanningContractError("scenes 不得为空")
        ids = [s.scene_id for s in self.scenes]
        if len(set(ids)) != len(ids):
            raise PlanningContractError(f"scene_id 重复: {ids}")
        # 预算与场次一一对应（无孤儿、无缺失）
        budget_ids = [b.scene_id for b in self.scene_budgets]
        if set(ids) != set(budget_ids):
            missing = sorted(set(ids) - set(budget_ids))
            orphan = sorted(set(budget_ids) - set(ids))
            raise PlanningContractError(
                f"scene 预算不一一对应 (缺预算: {missing}, 孤儿预算: {orphan})")
        self._validate_execution_bounds()
        self._validate_incremental_actions()

    def _validate_execution_bounds(self) -> None:
        bounds = self.execution_bounds
        if not isinstance(bounds, dict):
            raise PlanningContractError("execution_bounds 必须是 dict")
        for key in ("text_calls", "image_calls"):
            if key not in bounds:
                raise PlanningContractError(
                    f"execution_bounds 缺少 {key}（规划阶段自身消耗必须有上界）")
        _positive_int(bounds.get("text_calls"),
                      "execution_bounds.text_calls")
        _positive_int(bounds.get("image_calls"),
                      "execution_bounds.image_calls")
        video_cap = bounds.get("video_seconds")
        _positive_int(video_cap, "execution_bounds.video_seconds")
        if self.output_kind == "final_video" and not video_cap:
            raise PlanningContractError(
                "final_video 必须给出 video_seconds 资源上限")
        # 已知全部 target_s 时：计划总量不得超过媒体额度（未齐则留给冻结期校验）
        if video_cap and all(b.target_s is not None
                             for b in self.scene_budgets):
            planned = sum(b.target_s for b in self.scene_budgets)  # type: ignore[misc]
            if planned > video_cap:
                raise PlanningContractError(
                    f"planned video {planned}s 超过 execution_bounds."
                    f"video_seconds({video_cap})")

    def _validate_incremental_actions(self) -> None:
        known = ("reuse_asset", "generate_missing", "verify_existing")
        for i, act in enumerate(self.incremental_actions):
            if not isinstance(act, dict) or act.get("action_kind") not in known:
                raise PlanningContractError(
                    f"incremental_actions[{i}] 非法: {act!r}")
            if not (isinstance(act.get("target"), str) and act["target"].strip()):
                raise PlanningContractError(
                    f"incremental_actions[{i}] 缺少非空 target")
            # 红线（执行指令 §8）：被标记复用的素材必须有可核验来源，
            # 不接受自然语言"已存在"。
            if act["action_kind"] == "reuse_asset":
                ev = act.get("evidence")
                if not ev or (isinstance(ev, dict) and not ev):
                    raise PlanningContractError(
                        f"incremental_actions[{i}] reuse_asset 缺少可核验"
                        f" evidence（ID/SHA/检查点引用）")

    def canonical_payload(self) -> Dict[str, Any]:
        return {
            "schema_version": PLAN_SCHEMA,
            "task_spec_sha256": self.task_spec_sha256,
            "intent": self.intent,
            "output_kind": self.output_kind,
            "status": self.status,
            "story_text": self.story_text,
            "style": self.style,
            "scenes": [{"scene_id": s.scene_id, "title": s.title,
                        "summary": s.summary} for s in self.scenes],
            "scene_budgets": [b.to_evidence() for b in self.scene_budgets],
            "incremental_actions": [dict(a) for a in self.incremental_actions],
            "execution_bounds": dict(self.execution_bounds),
        }

    def plan_sha256(self) -> str:
        """规划内容摘要（PR B1 冻结器据此计算 plan_fingerprint 并 CAS）。"""
        return _sha256_hex(canonical_json(self.canonical_payload()))

    def to_evidence(self) -> Dict[str, Any]:
        ev = self.canonical_payload()
        ev["plan_sha256"] = self.plan_sha256()
        return ev


#: SKILL 草案用语别名
PlanningDecision = ProducerPlanningResult


# ---------------------------------------------------------------------------
# suggest_intent：自然语言 -> 意图的确定性启发（供 mock 测试与引导）
# ---------------------------------------------------------------------------
# 生产中由 Producer LLM 直接确定 TaskSpec.intent；本启发式保证四类核心任务在
# 无 LLM 环境下也能正确分流（测试矩阵 T01-T04 的 mock 基础），且遵守铁律：
# 命中任何"继续类"信号时绝不下沉为 create。

_RESUME_PATTERNS = ("恢复运行", "继续运行", "恢复上次运行", "恢复这个run",
                    "恢复这个 run", "resume")
_ADD_ASSET_PATTERNS = ("补齐", "补素材", "补充素材", "补镜头", "补图",
                       "缺失素材", "没生成完", "没做完的素材", "把素材补")
_EXTEND_PATTERNS = ("延长", "加长", "再长一点", "多加一段", "追加")
_SEQUEL_PATTERNS = ("续集", "续作", "下一部", "拍续")
_COMPLETE_PATTERNS = ("把项目做完", "完成项目", "做完这个项目", "继续项目",
                      "继续这个项目", "继续上次项目", "把没做完的做完")
_CONTINUATION_MARKERS = ("继续", "接着", "做完", "补", "完成")


def suggest_intent(request_text: str,
                   *, source_project: Optional[str] = None) -> Tuple[str, str]:
    """按优先级返回 (intent, reason)。确定性、无 I/O。

    优先级（从高到低）：resume_run > add_assets > extend_existing >
    sequel > complete_project > create。带 source_project 时，含"继续"
    类标记的文本绝不下沉为 create。
    """
    text = (request_text or "").strip().lower()
    has_source = bool(source_project)
    for pat in _RESUME_PATTERNS:
        if pat in text:
            return "resume_run", f"命中恢复信号: {pat}"
    for pat in _ADD_ASSET_PATTERNS:
        if pat in text:
            return "add_assets", f"命中补素材信号: {pat}"
    for pat in _EXTEND_PATTERNS:
        if pat in text:
            return "extend_existing", f"命中延长信号: {pat}"
    for pat in _SEQUEL_PATTERNS:
        if pat in text:
            return "sequel", f"命中续集信号: {pat}"
    for pat in _COMPLETE_PATTERNS:
        if pat in text:
            return "complete_project", f"命中完成项目信号: {pat}"
    # 铁律：已有 source_project 且文本含继续类标记 -> 永不 create
    if has_source and any(m in text for m in _CONTINUATION_MARKERS):
        return "complete_project", "source_project + 继续类标记（保守分流）"
    return "create", "未命中继续类信号，默认新建"
