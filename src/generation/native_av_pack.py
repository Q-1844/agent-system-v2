# src/generation/native_av_pack.py
# Native AV C1 — NativeAVGenerationPack（18.md §6/§8/§16/§21/§23）。
#
# 职责：把一个 Shot 的创作事实（台词真相 / speaker 解析 / shot-local audio intent /
# 视觉生产输入 / provider 事实）组装成一次协调的 Agnes Native AV generation 所需的
# derived 控制对象，并派生 generation_basis（provenance/freshness 的对比键）。
#
# 🔴 第一原则（18.md）：
#   - §6 pack = derived / ephemeral / deterministic / provider-neutral /
#     无业务 ID / 不持久化——不是第二套 canonical；
#   - §8 dialogue truth 只有一份（Shot.dialogue）：pack.lines[].exact_text 只是
#     当前 canonical 的 derived slice——pack 不持久化第二份 dialogue；
#   - §6/§23 视觉侧组合/引用现有 Visual V1 derived input（BOUND=ShotReferencePack
#     的 PackBasis 对比键；UNBOUND=实际消费的 boundary frame 身份），绝不复制
#     Visual ReferencePack、绝不重造视觉 dependency graph；
#   - §23 actual dependencies only：basis 的依赖键只覆盖真实消费的 canonical 事实；
#   - §16/§17 audio intent 极薄（music_policy + directives），挂在
#     ShotProductionBinding，Agnes 原生负责生成——不做 SFXAsset/Timeline。
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
# §24：prompt compiler 版本是 actual generation dependency（升级 v1→v2 即使
# Shot 未改也可能改变行为）——进 basis；完整 compiled prompt 字符串不 hash 当依赖。
NATIVE_AV_PROMPT_SCHEMA = "native_av_prompt_v1"

# §25：freshness 对比键 = canonical 依赖键（actual dependencies only）。
# provider/model/provider_options 是 per-generation 事实（provenance）——
# 记录进 basis 但不参与 freshness 对比（与 ImageBatch/PackBasis 先例同口径：
# 模型切换不追溯作废旧产物；重生成即新版本）。
NATIVE_AV_DEPENDENCY_KEYS = (
    "dialogue_fingerprint",
    "speaker_map_fingerprint",
    "spoken_language",
    "audio_event_intent_fingerprint",
    "music_policy",
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
    audio_intent: Dict[str, Any]           # §16：music_policy + directives（极薄）
    provider_binding: Dict[str, Any]       # §23：provider/model/mode/options_fingerprint
    compiler: Dict[str, Any]               # prompt_compiler_schema（§24：进 basis）
    generation_basis: Dict[str, Any]       # §23 派生 basis（写入 VideoVersion）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "shot_id": self.shot_id,
            "scene_id": self.scene_id,
            "visual": dict(self.visual),
            "dialogue": dict(self.dialogue) if self.dialogue else None,
            "audio_intent": dict(self.audio_intent),
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


# ============ audio intent（§16/§17：极薄；C 语义在此一并落地）============
_MUSIC_POLICIES = ("none", "allow_native")


def read_audio_intent(project_dir, shot: Any, *, has_spoken_lines: bool) -> Dict[str, Any]:
    """读一镜的 shot-local audio intent。
    - binding.audio_event_intent 存在 → 校验后使用（music_policy ∈
      {none, allow_native}，directives 非空字符串列表）；
    - 否则派生默认（§17）：Dialogue Shot → music_policy="none"（去随机 BGM，
      保留门声/脚步/环境声）；无台词 → "allow_native"（visual-only 行为不变，
      不新增 prompt 约束）。
    非法 music_policy / directives → NativeAVError fail closed（绝不静默修正）。"""
    block = None
    if project_dir is not None:
        from src.shot_binding.store import ShotBindingStore
        binding = ShotBindingStore(project_dir).load().bindings.get(
            str(getattr(shot, "shot_id", "") or "").strip())
        block = getattr(binding, "audio_event_intent", None) if binding else None
    if block is not None:
        policy = str(getattr(block, "music_policy", "") or "").strip()
        if policy not in _MUSIC_POLICIES:
            raise NativeAVError(
                "invalid_audio_intent",
                f"audio_event_intent.music_policy 必须是 {_MUSIC_POLICIES} 之一，"
                f"得到 {policy!r}（18.md §17 fail closed）")
        directives: List[str] = []
        for d in (getattr(block, "directives", None) or []):
            s = str(d or "").strip()
            if not s:
                raise NativeAVError(
                    "invalid_audio_intent",
                    "audio_event_intent.directives 含空项（18.md §16：意图列表，"
                    "每项必须是非空文本）")
            directives.append(s)
        return {"music_policy": policy, "directives": directives,
                "source": "binding"}
    return {"music_policy": "none" if has_spoken_lines else "allow_native",
            "directives": [], "source": "default"}


# ============ pack 组装（§6 schema；§21 两条路径唯一入口）============
def build_native_av_pack(project_dir, project: Any, shot: Any, *,
                         visual_route: str, visual_basis: Dict[str, Any],
                         provider_options: Optional[Dict[str, Any]] = None,
                         language_override: Optional[str] = None
                         ) -> NativeAVGenerationPack:
    """组装 NativeAVGenerationPack（BOUND / UNBOUND 共用——§21：差别只能来自
    visual input source，不能有两套 dialogue compiler）。
    - dialogue：非空 spoken 台词 → resolve（language precedence §5 + speaker
      fail closed）；无 → None（不触发 language 检查，§4）；
    - audio intent：binding 块或 §17 默认；
    - provider_binding：provider 事实 + options 指纹（provenance，不参与对比）；
    - generation_basis：§23 逐键派生。"""
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

    audio_intent = read_audio_intent(project_dir, shot,
                                     has_spoken_lines=dialogue_dict is not None)

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
        "audio_event_intent_fingerprint": fingerprint(
            {"music_policy": audio_intent["music_policy"],
             "directives": audio_intent["directives"]}),
        "music_policy": audio_intent["music_policy"],
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
        audio_intent=audio_intent,
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


def compute_current_dependency_keys(project_dir, project: Any, shot: Any
                                    ) -> Dict[str, Any]:
    """重算当前 canonical 的 Native AV 依赖键（freshness / race guard 共用）。
    只重算依赖键——不触碰 provider 事实键（per-generation provenance）。"""
    from src.generation.image_adapter import shot_has_visual_production_binding

    audio_intent = read_audio_intent(project_dir, shot,
                                     has_spoken_lines=bool(
                                         split_dialogue_lines(
                                             getattr(shot, "dialogue", "") or "")))
    keys: Dict[str, Any] = {
        "dialogue_fingerprint": None,
        "speaker_map_fingerprint": None,
        "spoken_language": None,
        "audio_event_intent_fingerprint": fingerprint(
            {"music_policy": audio_intent["music_policy"],
             "directives": audio_intent["directives"]}),
        "music_policy": audio_intent["music_policy"],
        "prompt_compiler_schema": NATIVE_AV_PROMPT_SCHEMA,
    }
    if split_dialogue_lines(getattr(shot, "dialogue", "") or ""):
        language = resolve_dialogue_language(project, None)
        control = resolve_native_dialogue(project_dir, shot, language=language)
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
                                 basis: Dict[str, Any]) -> None:
    """§27 pre-dispatch：provider 调用前重算依赖键，与 pack basis 对齐比较。
    不一致 → provider 不调用（零 provider 调用浪费）。"""
    current = compute_current_dependency_keys(project_dir, project, shot)
    mismatched = [k for k in NATIVE_AV_DEPENDENCY_KEYS
                  if (basis or {}).get(k) != current.get(k)]
    if mismatched:
        raise NativeAVDependencyChangedError(
            "native_dependency_changed_before_generation",
            {"shot_id": getattr(shot, "shot_id", ""),
             "mismatched_keys": mismatched,
             "hint": "dispatch 前 canonical 已变化——显式重试整个 generation"})


def native_av_pre_commit_check(project_dir, project: Any, shot: Any,
                               basis: Dict[str, Any]) -> None:
    """§27 pre-commit：provider 完成后、attach 前重算依赖键。不一致 →
    拒绝 attach（不建 VideoVersion、不设 final_video、不冒充 CURRENT）。"""
    current = compute_current_dependency_keys(project_dir, project, shot)
    mismatched = [k for k in NATIVE_AV_DEPENDENCY_KEYS
                  if (basis or {}).get(k) != current.get(k)]
    if mismatched:
        raise NativeAVDependencyChangedError(
            "native_dependency_changed_before_commit",
            {"shot_id": getattr(shot, "shot_id", ""),
             "mismatched_keys": mismatched,
             "hint": "provider 输出不 attach、不设 final（§27：绝不静默作为 "
                     "CURRENT attach）——显式重新生成"})
