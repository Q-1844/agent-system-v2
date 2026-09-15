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
# 21.md §13：prompt compiler 版本是 actual generation dependency（升级 v1→v2 即使
# Shot 未改也可能改变行为）——进 basis；完整 compiled prompt 字符串不 hash 当依赖。
NATIVE_AV_PROMPT_SCHEMA = "native_av_prompt_v1"

# 21.md §14：freshness 对比键 = canonical 依赖键（actual dependencies only）。
# provider/model/provider_options 是 per-generation 事实（provenance）——
# 记录进 basis 但不参与 freshness 对比（与 ImageBatch/PackBasis 先例同口径：
# 模型切换不追溯作废旧产物；重生成即新版本）。
NATIVE_AV_DEPENDENCY_KEYS = (
    "dialogue_fingerprint",
    "speaker_map_fingerprint",
    "spoken_language",
    "visual_basis_fingerprint",
    "prompt_compiler_schema",
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

    visual = dict(visual_basis or {})
    basis: Dict[str, Any] = {
        "schema": NATIVE_AV_GENERATION_BASIS_SCHEMA,
        "shot_id": sid,
        "visual_basis_fingerprint": fingerprint(visual),
        "dialogue_fingerprint": (dialogue_dict or {}).get("dialogue_fingerprint"),
        "speaker_map_fingerprint": (dialogue_dict or {}).get("speaker_map_fingerprint"),
        "spoken_language": (dialogue_dict or {}).get("spoken_language"),
        "prompt_compiler_schema": NATIVE_AV_PROMPT_SCHEMA,
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


def compute_current_dependency_keys(project_dir, project: Any, shot: Any, *,
                                    language_override: Optional[str] = None
                                    ) -> Dict[str, Any]:
    """重算当前 canonical 的 Native AV 依赖键（freshness / race guard 共用）。
    只重算依赖键——不触碰 provider 事实键（per-generation provenance）。
    language_override：§5 precedence 的一部分——pre-dispatch/pre-commit 与
    generation 同窗，必须携带同一 override（否则 override 生成会被误判变化）；
    freshness 读模型传 None（override 是 ephemeral request 事实，非 canonical）。
    language 不可确定（皆缺）→ spoken_language=None（诚实缺省，不 raise——
    与 stored 值对比即 STALE）。"""
    from src.generation.image_adapter import shot_has_visual_production_binding

    has_spoken = bool(split_dialogue_lines(getattr(shot, "dialogue", "") or ""))
    keys: Dict[str, Any] = {
        "dialogue_fingerprint": None,
        "speaker_map_fingerprint": None,
        "spoken_language": None,
        "prompt_compiler_schema": NATIVE_AV_PROMPT_SCHEMA,
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
    else:
        mode = "keyframes" if shot.link_mode == "keyframes" else "i2v"
        visual = unbound_visual_basis(shot, provider_mode=mode)
    keys["visual_basis_fingerprint"] = fingerprint(visual)
    return keys


class NativeAVDependencyChangedError(NativeAVError):
    """§27 race guard：provider 生成窗口内 Native AV 依赖已变化——
    生成结果不得静默作为 CURRENT attach（A2 av_service 同口径）。"""


def native_av_pre_dispatch_check(project_dir, project: Any, shot: Any,
                                 basis: Dict[str, Any], *,
                                 language_override: Optional[str] = None) -> None:
    """§27 pre-dispatch：provider 调用前重算依赖键，与 pack basis 对齐比较。
    不一致 → provider 不调用（零 provider 调用浪费）。language_override 必须
    与本次 generation 编译时一致（§5 precedence 同窗对齐）。"""
    current = compute_current_dependency_keys(project_dir, project, shot,
                                              language_override=language_override)
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
                               language_override: Optional[str] = None) -> None:
    """§27 pre-commit：provider 完成后、attach 前重算依赖键。不一致 →
    拒绝 attach（不建 VideoVersion、不设 final_video、不冒充 CURRENT）。
    language_override 必须 与本次 generation 编译时一致（§5 同窗对齐）。"""
    current = compute_current_dependency_keys(project_dir, project, shot,
                                              language_override=language_override)
    mismatched = [k for k in NATIVE_AV_DEPENDENCY_KEYS
                  if (basis or {}).get(k) != current.get(k)]
    if mismatched:
        raise NativeAVDependencyChangedError(
            "native_dependency_changed_before_commit",
            {"shot_id": getattr(shot, "shot_id", ""),
             "mismatched_keys": mismatched,
             "hint": "provider 输出不 attach、不设 final（§27：绝不静默作为 "
                     "CURRENT attach）——显式重新生成"})
