# src/generation/native_av_pack.py
# Native AV C1 — NativeAVGenerationPack（21.md §6/§8/§10-§12/§14/§16/§17）。
#
# 职责：把一个 Shot 的创作事实（台词真相 / speaker 解析 / 视觉生产输入 /
# provider 事实）组装成一次协调的 Agnes Native AV generation 所需的
# derived 控制对象，并派生 generation_basis（provenance/freshness 的对比键）。
#
# 🔴 第一原则（21.md）：
#   - §6 pack = derived / ephemeral / deterministic / provider-neutral /
#     无业务 ID / 不持久化——不是第二套 canonical；
#   - §8 dialogue truth 只有一份（Shot.dialogue）：pack.lines[].exact_text 只是
#     当前 canonical 的 derived slice——pack 不持久化第二份 dialogue；
#   - §7 视觉侧组合/引用现有 Visual V1 derived input（BOUND=ShotReferencePack
#     的 PackBasis 对比键；UNBOUND=实际消费的 boundary frame 身份），绝不复制
#     Visual ReferencePack、绝不重造视觉 dependency graph；
#   - §11 actual dependencies only：basis 的依赖键只覆盖真实消费的 canonical 事实；
#   - §20 AudioEventIntent 已撤回（上一版 C1 提前带入；21.md §2/§20 明确禁止）——
#     是否需要结构化声音 intent 由 C2 在真实创作证据上再决定，本层零 audio schema。
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.generation.dialogue_control import (
    DialogueControlError,
    NativeDialogueControl,
    resolve_dialogue_language,
    resolve_native_dialogue,
    split_dialogue_lines,
)
from src.resources.generation import fingerprint

NATIVE_AV_GENERATION_PACK_SCHEMA = "native_av_generation_pack_v1"
NATIVE_AV_GENERATION_BASIS_SCHEMA = "native_av_generation_basis_v1"
# 21.md §13 / 22.md §15：prompt compiler 版本是 actual generation dependency
# （升级即使 Shot 未改也可能改变行为）——进 basis；完整 compiled prompt 字符串
# 不 hash 当依赖。
# v1→v2（C1-R）：撤回 no-BGM/声音指令规则后生成语义已变。
# v2→v3（C2 22.md §15）：动作时序框架（§7）+ 镜内连续性编译（§9-§11）+
# 统一表演指令（§12-§14）——旧 v2 版本按既有机制诚实 STALE。
NATIVE_AV_PROMPT_SCHEMA = "native_av_prompt_v3"

# 21.md §14：freshness 对比键 = actual dependencies，明确包含 provider/model/
# provider options（规格 STALE 触发清单逐项列出）——provider 事实变化 = 用当前
# 事实重新生成会得到不同 generation，final 必须诚实暴露，绝不静默视为 fresh。
# 对比窗口对齐：generation 窗口内（pre-dispatch/pre-commit）携带本次请求的
# provider_options 同窗比较（与 language_override 同一机制）；freshness 读模型
# 不带请求上下文 → 与「默认 dispatch 投影」比较（当前 UI 不传 video_model，
# 默认投影即生产现实）。
# C2（22.md §23）：performance_intent_fingerprint 增为 actual dependency——
# 表演意图变化 = 生成语义变化；缺省 None（旧版本不误报）。
# C2：video_prompt_creative_fingerprint——视频 prompt 实际消费的创作文本字段
# （motion/action_sequence/pace/env/cam/transition/preserve/characters/overrides/
# link_mode）的指纹。C1 时代只跟踪 dialogue 侧文本，视觉侧创作文本未跟踪；
# C2 的动作顺序控制成为正式能力后，按 actual-dependencies 原则补齐（否则
# action_sequence 变更后旧 final 谎报 CURRENT）。无关字段（note/description 等
# 不进视频 prompt 者）不哈希——无关变更不误报（22.md §23）。
NATIVE_AV_DEPENDENCY_KEYS = (
    "dialogue_fingerprint",
    "speaker_map_fingerprint",
    "spoken_language",
    "visual_basis_fingerprint",
    "prompt_compiler_schema",
    "provider",
    "model",
    "provider_mode",
    "provider_options_fingerprint",
    "performance_intent_fingerprint",
    "video_prompt_creative_fingerprint",
)


class NativeAVError(Exception):
    """Native AV pack 组装失败（fail closed，结构化诊断；零 provider 调用）。"""

    def __init__(self, code: str, detail: Any):
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")

    def to_dict(self) -> dict:
        return {"code": self.code, "detail": self.detail}


# ============ pack（§6：derived / ephemeral / 不持久化 / 无业务 ID）============
@dataclass(frozen=True)
class NativeAVGenerationPack:
    """一次 Native AV generation 的完整创作事实投影。"""
    schema: str
    shot_id: str
    scene_id: str
    visual: Dict[str, Any]                 # 视觉输入身份（BOUND=PackBasis 对比键；UNBOUND=frame 身份）
    dialogue: Optional[Dict[str, Any]]     # 无 spoken 台词 → None（§8：非第二真相）
    performance: Optional[Dict[str, Any]]  # C2 §13：表演意图投影（None=未表达）
    provider_binding: Dict[str, Any]       # §11：provider/model/mode/options_fingerprint
    compiler: Dict[str, Any]               # prompt_compiler_schema（§13：进 basis）
    generation_basis: Dict[str, Any]       # §10-§11 派生 basis（写入 VideoVersion）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "shot_id": self.shot_id,
            "scene_id": self.scene_id,
            "visual": dict(self.visual),
            "dialogue": dict(self.dialogue) if self.dialogue else None,
            "performance": dict(self.performance) if self.performance else None,
            "provider_binding": dict(self.provider_binding),
            "compiler": dict(self.compiler),
            "generation_basis": dict(self.generation_basis),
        }


# ============ visual basis（§6/§23：引用现有 Visual V1，不复制）============
def bound_visual_basis(pack_basis: Dict[str, Any]) -> Dict[str, Any]:
    """BOUND：从 ShotReferencePack 的 PackBasis 提取对比键子集（与 S3C.2
    _BOUNDARY_COMPARE_KEYS 先例同口径：binding_revision + resolved SIV + readiness；
    document_revision 不比——别的 shot refresh 不得误伤本镜，actual-deps-only）。"""
    return {
        "route": "bound",
        "binding_revision": str((pack_basis or {}).get("binding_revision") or ""),
        "binding_readiness": str((pack_basis or {}).get("binding_readiness") or ""),
        "resolved_scene_instance_view_id": (pack_basis or {}).get(
            "resolved_scene_instance_view_id"),
    }


def unbound_visual_basis(shot: Any, *, provider_mode: str) -> Dict[str, Any]:
    """UNBOUND：视频实际消费的视觉输入身份 = selected boundary frame 指针
    （candidate_id 决定像素来源；candidate 不变 = 视觉输入不变）。
    relay（inherit_previous）开场=来源镜头实际末帧——记 source 锚点。"""
    b: Dict[str, Any] = {
        "route": "unbound",
        "first_frame": getattr(shot, "selected_first_frame", None),
    }
    if provider_mode == "keyframes":
        b["last_frame"] = getattr(shot, "selected_last_frame", None)
    from src.core.models import OpeningSource
    if (getattr(shot, "opening_source", "")
            == OpeningSource.INHERIT_PREVIOUS.value and shot.source_shot_id):
        b["relay_source"] = {
            "shot_id": shot.source_shot_id,
            "version": getattr(shot, "source_version", None),
            "end_source_version": getattr(shot, "video_end_source_version", None),
        }
    return b


# ============ 表演意图（C2 22.md §13：最小投影；全空 = None）============
def _performance_dict(shot: Any) -> Optional[Dict[str, Any]]:
    """Shot.performance_intent → pack 投影。全空/缺失 → None（22.md §23：
    空 intent 不改变既有语义——basis 键为 None，编译零改动）。"""
    pi = getattr(shot, "performance_intent", None)
    if pi is None:
        return None
    d = {"emotion": str(getattr(pi, "emotion", "") or "").strip(),
         "intensity": str(getattr(pi, "intensity", "") or "").strip(),
         "note": str(getattr(pi, "note", "") or "").strip()}
    if not any(d.values()):
        return None
    return d


def _video_creative_dict(shot: Any) -> Dict[str, Any]:
    """C2：视频 prompt 实际消费的创作字段集合（与 prompt_builder._video_prompt_core
    消费面 + AV 编译一致性严格对齐；actual dependencies only——description/note/
    首尾帧专用字段不进视频 prompt，不哈希）。"""
    return {
        "motion": str(getattr(shot, "motion", "") or ""),
        "environment_motion": str(getattr(shot, "environment_motion", "") or ""),
        "camera": str(getattr(shot, "camera", "") or ""),
        "camera_movement": str(getattr(shot, "camera_movement", "") or ""),
        "motion_pace": str(getattr(shot, "motion_pace", "") or ""),
        "action_sequence": str(getattr(shot, "action_sequence", "") or ""),
        "transition_description": str(getattr(shot, "transition_description", "") or ""),
        "video_preserve_elements": list(getattr(shot, "video_preserve_elements", None) or []),
        "video_preserve_custom": str(getattr(shot, "video_preserve_custom", "") or ""),
        # 34.md Human Gate：multi 按镜参考选择是实际生成输入——进 creative 指纹
        "video_reference_asset_id": str(getattr(shot, "video_reference_asset_id", "") or ""),
        "characters": list(getattr(shot, "characters", None) or []),
        "video_prompt_override": str(getattr(shot, "video_prompt_override", "") or ""),
        "video_prompt_en": str(getattr(shot, "video_prompt_en", "") or ""),
        "link_mode": str(getattr(shot, "link_mode", "") or ""),
    }


# ============ pack 组装（§6 schema；§17 两条路径唯一入口）============
def build_native_av_pack(project_dir, project: Any, shot: Any, *,
                         visual_route: str, visual_basis: Dict[str, Any],
                         provider_options: Optional[Dict[str, Any]] = None,
                         language_override: Optional[str] = None
                         ) -> NativeAVGenerationPack:
    """组装 NativeAVGenerationPack（BOUND / UNBOUND 共用——§17：差别只能来自
    visual input source，不能有两套 dialogue compiler）。
    - dialogue：非空 spoken 台词 → resolve（language precedence §5 + speaker
      fail closed）；无 → None（不触发 language 检查，§4）；
    - provider_binding：provider 事实 + options 指纹（provenance）；
    - generation_basis：§10-§11 逐键派生。"""
    if visual_route not in ("bound", "unbound"):
        raise NativeAVError("invalid_visual_route", f"visual_route={visual_route!r}")

    sid = str(getattr(shot, "shot_id", "") or "")
    dialogue_dict: Optional[Dict[str, Any]] = None
    control: Optional[NativeDialogueControl] = None
    language: Optional[str] = None
    if split_dialogue_lines(getattr(shot, "dialogue", "") or ""):
        language = resolve_dialogue_language(project, language_override)
        control = resolve_native_dialogue(project_dir, shot, language=language)
    if control is not None:
        dialogue_dict = {
            "dialogue_fingerprint": fingerprint(
                str(getattr(shot, "dialogue", "") or "")),
            "spoken_language": control.language,
            "lines": [
                {"index": ln.index, "exact_text": ln.text,
                 "speaker_character_name": ln.speaker_character_name,
                 "delivery_hints": list(ln.delivery_hints)}
                for ln in control.lines],
            "silent_characters": list(control.silent_characters),
            "speaker_map_fingerprint": fingerprint(
                {"speakers": [[ln.index, ln.speaker_character_name]
                              for ln in control.lines],
                 "silent": list(control.silent_characters)}),
            "directions": [
                {"index": d.index, "text": d.text,
                 "speaker_character_name": d.speaker_character_name}
                for d in control.directions],
            "resolution": control.resolution,
        }

    options = dict(provider_options or {})
    provider_binding = {
        "provider": "agnes",
        "model": options.get("model"),
        "mode": options.get("mode"),
        "options_fingerprint": fingerprint(options) if options else None,
    }

    performance = _performance_dict(shot)
    visual = dict(visual_basis or {})
    basis: Dict[str, Any] = {
        "schema": NATIVE_AV_GENERATION_BASIS_SCHEMA,
        "shot_id": sid,
        "visual_basis_fingerprint": fingerprint(visual),
        "dialogue_fingerprint": (dialogue_dict or {}).get("dialogue_fingerprint"),
        "speaker_map_fingerprint": (dialogue_dict or {}).get("speaker_map_fingerprint"),
        "spoken_language": (dialogue_dict or {}).get("spoken_language"),
        "prompt_compiler_schema": NATIVE_AV_PROMPT_SCHEMA,
        "performance_intent_fingerprint": fingerprint(performance) if performance else None,
        "video_prompt_creative_fingerprint": fingerprint(_video_creative_dict(shot)),
        # ---- per-generation provider 事实（provenance；不参与 freshness 对比）----
        "provider": provider_binding["provider"],
        "model": provider_binding["model"],
        "provider_mode": provider_binding["mode"],
        "provider_options_fingerprint": provider_binding["options_fingerprint"],
    }

    return NativeAVGenerationPack(
        schema=NATIVE_AV_GENERATION_PACK_SCHEMA,
        shot_id=sid,
        scene_id=str(getattr(shot, "scene_id", "") or ""),
        visual=visual,
        dialogue=dialogue_dict,
        performance=performance,
        provider_binding=provider_binding,
        compiler={"prompt_compiler_schema": NATIVE_AV_PROMPT_SCHEMA},
        generation_basis=basis)


# ============ freshness（§25；Commit D 消费）============
def native_av_freshness(project_dir, project: Any, shot: Any) -> Dict[str, Any]:
    """派生读模型：selected/final video 的 Native AV freshness。
    - missing：无 selected/final video（或版本不存在/未完成）；
    - untracked_legacy：generation_basis={}（旧版本——旧视频仍可播放/保留/手工
      选择，但系统诚实表示无法证明其 Native AV freshness）；
    - current：stored basis 与当前重算依赖键一致；
    - stale：任一依赖键不一致（detail.mismatched_keys）。
    🔴 §26 No Auto Regeneration：本函数是纯读模型——绝不触发任何重生成。"""
    from src.core.models import ShotStatus

    def _result(status: str, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"status": status, "shot_id": getattr(shot, "shot_id", ""),
                "detail": detail or {}}

    final_id = getattr(shot, "final_video", None)
    version = None
    for vv in (getattr(shot, "video_versions", None) or []):
        if vv.version_id == final_id:
            version = vv
            break
    if not final_id or version is None:
        return _result("missing", {"reason": "no_final_video"})
    if version.status != ShotStatus.DONE.value:
        return _result("missing", {"reason": "final_video_not_done",
                                   "status": version.status})
    recorded = dict(getattr(version, "generation_basis", None) or {})
    if not recorded:
        return _result("untracked_legacy",
                       {"reason": "generation_basis_missing",
                        "version_id": version.version_id,
                        "hint": "旧版本无 generation provenance——可继续播放/保留/"
                                "手工选择，但无法证明其 Native AV freshness（§25）"})

    try:
        current = compute_current_dependency_keys(project_dir, project, shot)
    except (DialogueControlError, NativeAVError) as e:
        return _result("stale", {"reason": "recompute_failed",
                                 "code": e.code, "detail": e.detail})
    mismatched = [k for k in NATIVE_AV_DEPENDENCY_KEYS
                  if recorded.get(k) != current.get(k)]
    if mismatched:
        return _result("stale", {
            "reason": "dependency_keys_mismatch",
            "version_id": version.version_id,
            "mismatched_keys": mismatched,
            "recorded": {k: recorded.get(k) for k in mismatched},
            "current": {k: current.get(k) for k in mismatched},
            "hint": "显式重新生成（§26：detect → inform → explicit regenerate，"
                    "绝不 auto regeneration）"})
    return _result("current", {"version_id": version.version_id})


def _default_provider_options(project: Any, shot: Any) -> Dict[str, Any]:
    """freshness 读模型（无请求上下文）的「当前 provider 事实」= 默认 dispatch
    投影：与 _handler_video 既有默认路径逐字段同构（video_model 不传 → None、
    不加 duration_s；16:9 走服务端实测 1280x704，与 handler 同一口径）。
    当前 UI 不传 video_model——默认投影即生产现实（21.md §14）。"""
    from src.api.agnes_client import resolve_size, DEFAULT_VIDEO_MODEL

    style = getattr(project, "style", None)
    ratio = getattr(style, "aspect_ratio", None) or "16:9"
    if ratio == "16:9":
        w, h = 1280, 704
    else:
        w, h = map(int, resolve_size(
            getattr(style, "size_tier", None) or "1K", ratio).split("x"))
    video = getattr(shot, "video", None)
    return {"mode": ("keyframes" if getattr(shot, "link_mode", "") == "keyframes"
                     else "multi" if getattr(shot, "link_mode", "") == "multi"
                     else "i2v"),
            "model": DEFAULT_VIDEO_MODEL,   # 2026-09-28：唯一视频模型（与 dispatch 解析同源）
            "num_frames": getattr(video, "num_frames", None),
            "frame_rate": getattr(video, "frame_rate", None),
            "width": w, "height": h,
            "seed": getattr(shot, "seed", None),
            "negative_prompt": getattr(shot, "negative_prompt", None),
            # 2026-09-28：dispatch 必带 duration_s（2.5 契约秒数）——投影同构，指纹一致
            "duration_s": (max(4, min(12, round(getattr(video, "num_frames", 121) /
                                max(getattr(video, "frame_rate", 24), 1))))
                           if getattr(video, "num_frames", None) else 5)}


def compute_current_dependency_keys(project_dir, project: Any, shot: Any, *,
                                    language_override: Optional[str] = None,
                                    provider_options: Optional[Dict[str, Any]] = None
                                    ) -> Dict[str, Any]:
    """重算当前 canonical 的 Native AV 依赖键（freshness / race guard 共用）。
    language_override：§5 precedence 的一部分——pre-dispatch/pre-commit 与
    generation 同窗，必须携带同一 override（否则 override 生成会被误判变化）；
    freshness 读模型传 None（override 是 ephemeral request 事实，非 canonical）。
    provider_options：generation 窗口内传本次请求完整 options（与 pack basis
    同窗对齐，21.md §14）；None → 默认 dispatch 投影。
    language 不可确定（皆缺）→ spoken_language=None（诚实缺省，不 raise——
    与 stored 值对比即 STALE）。"""
    from src.generation.image_adapter import shot_has_visual_production_binding

    has_spoken = bool(split_dialogue_lines(getattr(shot, "dialogue", "") or ""))
    performance = _performance_dict(shot)
    keys: Dict[str, Any] = {
        "dialogue_fingerprint": None,
        "speaker_map_fingerprint": None,
        "spoken_language": None,
        "prompt_compiler_schema": NATIVE_AV_PROMPT_SCHEMA,
        "performance_intent_fingerprint": fingerprint(performance) if performance else None,
        "video_prompt_creative_fingerprint": fingerprint(_video_creative_dict(shot)),
    }
    if has_spoken:
        try:
            language = resolve_dialogue_language(project, language_override)
        except DialogueControlError:
            language = None
        control = (resolve_native_dialogue(project_dir, shot, language=language)
                   if language else None)
        if control is not None:
            keys["dialogue_fingerprint"] = fingerprint(
                str(getattr(shot, "dialogue", "") or ""))
            keys["speaker_map_fingerprint"] = fingerprint(
                {"speakers": [[ln.index, ln.speaker_character_name]
                              for ln in control.lines],
                 "silent": list(control.silent_characters)})
            keys["spoken_language"] = control.language

    if shot_has_visual_production_binding(project_dir, getattr(shot, "shot_id", "")):
        from src.shot_binding import ShotBindingService
        svc = ShotBindingService(project_dir, project_dir.name)
        validity = svc.read_validity(str(getattr(shot, "shot_id", "") or ""))
        visual = {
            "route": "bound",
            "binding_revision": str(validity.get("binding_revision") or ""),
            "binding_readiness": str(validity.get("readiness") or ""),
            "resolved_scene_instance_view_id": (
                validity.get("resolved_dependencies") or {}).get(
                    "scene_instance_view_id"),
        }
        # 34.md V1C：multi 尾帧接力是实际生成依赖——与 video_adapter build 侧
        # 同规则重算（resolve_multi_relay_source），relay 信息进 visual 指纹
        # （Phase 3：cross_segment 一并参与，两侧对称）。
        if str(getattr(shot, "link_mode", "") or "") == "multi":
            from src.generation.video_adapter import resolve_multi_relay_source
            relay = resolve_multi_relay_source(project, shot)
            if relay is not None:
                visual = dict(visual)
                visual["relay"] = {"source_shot_id": relay["source_shot_id"],
                                   "end_source_version": relay.get("end_source_version"),
                                   "cross_segment": bool(relay.get("cross_segment")),
                                   # 42.md 缺陷⑤：Look+尾帧双参考开关（canonical
                                   # 哨兵 prev_tail_look）——build 侧同键对称
                                   "identity": bool(relay.get("identity")),
                                   # 43.md §二：场景实例视图第三参考开关（哨兵
                                   # prev_tail_look_scene）——build 侧同键对称
                                   "scene": bool(relay.get("scene"))}
    else:
        mode = ("keyframes" if shot.link_mode == "keyframes"
                else "multi" if shot.link_mode == "multi" else "i2v")
        visual = unbound_visual_basis(shot, provider_mode=mode)
    keys["visual_basis_fingerprint"] = fingerprint(visual)

    # ---- provider 事实（21.md §14：provider/model/options ∈ STALE 触发集）----
    options = (dict(provider_options) if provider_options is not None
               else _default_provider_options(project, shot))
    keys["provider"] = "agnes"
    keys["model"] = options.get("model")
    keys["provider_mode"] = options.get("mode")
    keys["provider_options_fingerprint"] = fingerprint(options) if options else None
    return keys


class NativeAVDependencyChangedError(NativeAVError):
    """§27 race guard：provider 生成窗口内 Native AV 依赖已变化——
    生成结果不得静默作为 CURRENT attach（A2 av_service 同口径）。"""


def native_av_pre_dispatch_check(project_dir, project: Any, shot: Any,
                                 basis: Dict[str, Any], *,
                                 language_override: Optional[str] = None,
                                 provider_options: Optional[Dict[str, Any]] = None
                                 ) -> None:
    """21.md §16 pre-dispatch：provider 调用前重算依赖键，与 pack basis 对齐比较。
    不一致 → provider 不调用（零 provider 调用浪费）。language_override /
    provider_options 必须与本次 generation 编译时一致（同窗对齐）。"""
    current = compute_current_dependency_keys(project_dir, project, shot,
                                              language_override=language_override,
                                              provider_options=provider_options)
    mismatched = [k for k in NATIVE_AV_DEPENDENCY_KEYS
                  if (basis or {}).get(k) != current.get(k)]
    if mismatched:
        raise NativeAVDependencyChangedError(
            "native_dependency_changed_before_generation",
            {"shot_id": getattr(shot, "shot_id", ""),
             "mismatched_keys": mismatched,
             "hint": "dispatch 前 canonical 已变化——显式重试整个 generation"})


def native_av_pre_commit_check(project_dir, project: Any, shot: Any,
                               basis: Dict[str, Any], *,
                               language_override: Optional[str] = None,
                               provider_options: Optional[Dict[str, Any]] = None
                               ) -> None:
    """21.md §16 pre-commit：provider 完成后、attach 前重算依赖键。不一致 →
    拒绝 attach（不建 VideoVersion、不设 final_video、不冒充 CURRENT）。
    language_override / provider_options 必须与本次 generation 编译时一致
    （同窗对齐）。"""
    current = compute_current_dependency_keys(project_dir, project, shot,
                                              language_override=language_override,
                                              provider_options=provider_options)
    mismatched = [k for k in NATIVE_AV_DEPENDENCY_KEYS
                  if (basis or {}).get(k) != current.get(k)]
    if mismatched:
        raise NativeAVDependencyChangedError(
            "native_dependency_changed_before_commit",
            {"shot_id": getattr(shot, "shot_id", ""),
             "mismatched_keys": mismatched,
             "hint": "provider 输出不 attach、不设 final（21.md §16：绝不静默作为 "
                     "CURRENT attach）——显式重新生成"})
