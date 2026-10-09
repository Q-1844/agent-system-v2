# src/sequence/models.py
# Sequence S1 — Assembly Foundation canonical 模型（24.md §5/§6/§9/§11/§12/§16/§19）。
#
# 职责边界（24.md §5）：
#   SequenceBlock 只表达「哪些 Shot / 什么顺序 / 使用哪些裁切区间」。
#   不表达镜头里面发生什么 / 人物 / 台词 / prompt——这些都已经属于 Shot。
#
# 🔴 引用而非复制（24.md §6）：SequenceBlock 默认引用 Shot（shot_id），绝不复制
#    dialogue / character / video path / audio path / prompt。render 时才把
#    clip.shot_id → Shot.final_video → VideoVersion → authoritative source mp4
#    解析为文件事实（§7 Source Video Truth，24.md）。
# 🔴 revision / fingerprint = sha256(canonical_json(语义子集))[:8]（同指纹族：
#    src/resources/generation.fingerprint；不含时间戳，同语义 → 同指纹）。
#    sequence_revision 只覆盖 render-affecting 语义（clips 顺序 + shot + trim）——
#    title / scene_id 是 display/metadata，不进 revision（无关变更不误报 STALE）。
# 🔴 fail closed：坏 JSON / 未知 schema / 非法 trim → SequenceError，绝不静默修正
#    用户数字（24.md §12：非法 FAIL CLOSED，不自动修正）。
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.resources.generation import fingerprint

# 落盘 schema（output/sequences.json 根）；未知版本 fail closed，不静默迁移。
SEQUENCE_STORE_SCHEMA_VERSION = 1

# renderer schema（24.md §19：进 basis；升级 = 全部旧 render 诚实 STALE）。
# v1 = S1 hard-cut only（24.md §11：无 crossfade/dissolve/wipe/J-cut/L-cut）。
SEQUENCE_RENDERER_SCHEMA = "sequence_renderer_v1"

# generation basis schema（24.md §19 sequence_render_basis_v1）。
SEQUENCE_RENDER_BASIS_SCHEMA = "sequence_render_basis_v1"

# ---- 24.md §16 Production Media Spec ----
# 开工前实测（2026-09-15，ffprobe 全量）：当前生产现实 = Native AV Agnes 输出
# h264 / 1280x704（16:9 服务端实测档）/ 24fps / yuv420p / AAC 48000Hz 立体声。
# S1 固定此 profile（与 VideoComposer._make_manifest_segment 归一化配方同口径），
# 不同 provider/model 的源一律归一化进来。不做完整 codec configuration system（§16）。
SEQUENCE_RENDER_PROFILE: Dict[str, Any] = {
    "width": 1280,
    "height": 704,
    "fps": 24,
    "vcodec": "libx264",
    "pix_fmt": "yuv420p",
    "acodec": "aac",
    "sample_rate": 48000,
    "channels": 2,
    "container": "mp4",
}


def render_profile_fingerprint() -> str:
    """§19：render profile 指纹（进 basis；profile 变化 = 全部旧 render STALE）。"""
    return fingerprint(SEQUENCE_RENDER_PROFILE)


# ---- Phase 44B：per-sequence render profile（加法式扩展）----
# 背景：SEQUENCE_RENDER_PROFILE 冻结自 Native AV 时代源现实（1280x704）。video 2.5
# 迁移后生产现实以 1280x720 为主，且同一部片可能混代际素材（44A 雨灯归航：11 镜
# 720 + 2 镜 704）。全局常量按 S1 冻结纪律不动；SequenceBlock 增可选 render_profile
# 覆盖（None = 全局，S1 语义逐字节不变）。profile 是渲染依赖 → 进 basis 指纹，
# 变化 = 诚实 STALE（同 §19）。
RENDER_PROFILE_BASE_KEYS = ("width", "height", "fps", "vcodec", "pix_fmt",
                            "acodec", "sample_rate", "channels", "container")
RENDER_PROFILE_OPTIONAL_KEYS = ("fill", "audio")
# 第一版能力白名单（与 media_ops._normalize_vf / render_clip 实际实现一致；fail closed）。
_RENDER_PROFILE_VALUE_WHITELIST = {
    "vcodec": {"libx264"},
    "pix_fmt": {"yuv420p"},
    "acodec": {"aac"},
    "container": {"mp4"},
    "fill": {"pad", "crop"},
    "audio": {"native", "silent"},
}


def validate_render_profile(profile: Any) -> Dict[str, Any]:
    """校验 per-sequence render profile（fail closed，不静默修正）。
    必须是【完整】规格（与 SEQUENCE_RENDER_PROFILE 同键集 + 可选 fill/audio）；
    未知键 / 缺基键 / 类型错 / 白名单外值 / 非偶数宽高 / 非正 fps/采样率 →
    SequenceError.profile_invalid。返回规范化副本（int/str 归一）。"""
    err = SequenceError("profile_invalid", "")
    if not isinstance(profile, dict):
        err.message = f"render_profile 必须是对象，收到: {type(profile).__name__}"
        raise err
    unknown = sorted(set(profile) - set(RENDER_PROFILE_BASE_KEYS)
                     - set(RENDER_PROFILE_OPTIONAL_KEYS))
    if unknown:
        err.message = f"render_profile 未知键: {unknown}（fail closed）"
        raise err
    missing = [k for k in RENDER_PROFILE_BASE_KEYS if k not in profile]
    if missing:
        err.message = f"render_profile 缺少必填键: {missing}（必须是完整规格）"
        raise err
    out: Dict[str, Any] = {}
    for k in RENDER_PROFILE_BASE_KEYS:
        v = profile[k]
        if k in ("width", "height", "sample_rate", "channels"):
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                err.message = f"render_profile.{k} 必须是正整数，收到: {v!r}"
                raise err
            out[k] = v
        elif k == "fps":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                err.message = f"render_profile.fps 必须是正数，收到: {v!r}"
                raise err
            out[k] = v
        else:
            if not isinstance(v, str):
                err.message = f"render_profile.{k} 必须是字符串，收到: {v!r}"
                raise err
            if v not in _RENDER_PROFILE_VALUE_WHITELIST[k]:
                err.message = (f"render_profile.{k}={v!r} 不在第一版能力白名单 "
                               f"{sorted(_RENDER_PROFILE_VALUE_WHITELIST[k])}")
                raise err
            out[k] = v
    if out["width"] % 2 or out["height"] % 2:
        err.message = ("render_profile 宽高必须为偶数（yuv420p 要求）: "
                       f"{out['width']}x{out['height']}")
        raise err
    if out["channels"] not in (1, 2):
        err.message = f"render_profile.channels 必须 1/2，收到: {out['channels']}"
        raise err
    for k in RENDER_PROFILE_OPTIONAL_KEYS:
        if k in profile:
            v = profile[k]
            if not isinstance(v, str) or v not in _RENDER_PROFILE_VALUE_WHITELIST[k]:
                err.message = (f"render_profile.{k}={v!r} 非法"
                               f"（允许: {sorted(_RENDER_PROFILE_VALUE_WHITELIST[k])}）")
                raise err
            out[k] = v
    return out


def render_profile_fingerprint_for(profile: Dict[str, Any]) -> str:
    """任意 profile 的指纹（与 render_profile_fingerprint 同族；effective profile 用）。
    None/全局 profile 传 SEQUENCE_RENDER_PROFILE 本体 → 与旧指纹逐字节一致。"""
    return fingerprint(profile)


# ---- SequenceRenderVersion.status（24.md §9/§23）----
RSTATUS_QUEUED = "queued"                    # 已受理，待 Phase 1 prepare
RSTATUS_RENDERING = "rendering"              # Phase 2 ffmpeg 进行中
RSTATUS_DONE = "done"                        # Phase 3 通过，可被显式 select
RSTATUS_FAILED = "failed"                    # prepare/render/validate 失败（error 记因）
RSTATUS_RACED = "raced_superseded"           # §23 race guard：commit 前依赖已变，
                                             # 不得 attach 为 CURRENT；输出保留为 evidence


class SequenceError(Exception):
    """Sequence 域错误（fail closed，结构化 code；service/router 层转 HTTP）。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


# ============ SequenceClip（24.md §6/§12）============

def _check_trim_int(value: Any, name: str) -> int:
    """trim 必须是整数毫秒（24.md §12 ms 语义）。bool 是 int 子类，显式排除。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SequenceError(
            "trim_invalid", f"{name} 必须是整数毫秒，收到: {value!r}")
    if value < 0:
        raise SequenceError("trim_invalid", f"{name} 不能为负数，收到: {value}")
    return value


@dataclass
class SequenceClip:
    """一个 clip = 一个 Shot 引用 + 裁切区间（毫秒）。
    trim 语义（24.md §12 推荐定义）：
      trim_in_ms  = 从源视频开头裁掉多少
      trim_out_ms = 从源视频结尾裁掉多少
    合法性：0 <= trim_in，0 <= trim_out（此处只做结构校验）；
    trim_in + trim_out < source_duration 属媒体事实，render Phase 1 用 ffprobe
    实测验证（§14：不根据数据库历史值猜）——非法 FAIL CLOSED，不自动修正。"""
    shot_id: str
    trim_in_ms: int = 0
    trim_out_ms: int = 0

    def __post_init__(self) -> None:
        if not str(self.shot_id or "").strip():
            raise SequenceError("clip_invalid", "clip.shot_id 不能为空")
        self.shot_id = str(self.shot_id).strip()
        self.trim_in_ms = _check_trim_int(self.trim_in_ms, "trim_in_ms")
        self.trim_out_ms = _check_trim_int(self.trim_out_ms, "trim_out_ms")

    def semantic_snapshot(self) -> Dict[str, Any]:
        """进 revision / basis 的语义子集（actual dependencies only）。"""
        return {"shot_id": self.shot_id,
                "trim_in_ms": self.trim_in_ms,
                "trim_out_ms": self.trim_out_ms}

    def to_dict(self) -> dict:
        return dict(self.semantic_snapshot())

    @classmethod
    def from_dict(cls, d: dict) -> "SequenceClip":
        if not isinstance(d, dict):
            raise SequenceError("clip_invalid", f"clip 必须是对象，收到: {type(d).__name__}")
        for required in ("shot_id",):
            if required not in d:
                raise SequenceError("clip_invalid", f"clip 缺少 {required}（fail closed）")
        return cls(shot_id=d.get("shot_id"),
                   trim_in_ms=d.get("trim_in_ms", 0),
                   trim_out_ms=d.get("trim_out_ms", 0))


# ============ SequenceBlock（24.md §5/§6，canonical）============

@dataclass
class SequenceBlock:
    """Sequence canonical：哪些 Shot、什么顺序、什么 trim。引用 Shot，不复制 Shot。"""
    sequence_id: str                     # "seq_" + uuid8（身份证号语义，分配不改）
    clips: List[SequenceClip] = field(default_factory=list)
    title: str = ""                      # display only（不进 revision）
    scene_id: Optional[str] = None       # 可选归属场景（display/组织用，不进 revision）
    # Phase 44B：per-sequence 渲染 profile（None = 全局 SEQUENCE_RENDER_PROFILE，
    # S1 语义逐字节不变）。display/组织字段同样不进 revision——render 依赖走 basis
    # 的 profile 指纹（变化 = STALE），不混入剪辑语义指纹。
    render_profile: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not str(self.sequence_id or "").strip():
            raise SequenceError("sequence_invalid", "sequence_id 不能为空")
        self.sequence_id = str(self.sequence_id).strip()
        self.title = str(self.title or "").strip()
        self.clips = [c if isinstance(c, SequenceClip) else SequenceClip.from_dict(c)
                      for c in (self.clips or [])]
        if not self.clips:
            raise SequenceError(
                "sequence_invalid",
                "Sequence 至少需要 1 个 clip（空 Sequence 不构成「哪些 Shot 什么顺序」）")
        if self.render_profile is not None:
            self.render_profile = validate_render_profile(self.render_profile)

    def effective_render_profile(self) -> Dict[str, Any]:
        """渲染实际消费的 profile：显式覆盖（已校验）或全局 SEQUENCE_RENDER_PROFILE。
        🔴 None 时返回全局常量【本体】，保证旧项目 basis 指纹逐字节不变。"""
        return self.render_profile if self.render_profile is not None \
            else SEQUENCE_RENDER_PROFILE

    @staticmethod
    def new_sequence_id() -> str:
        return f"seq_{uuid.uuid4().hex[:8]}"

    def semantic_snapshot(self) -> Dict[str, Any]:
        """render-affecting 语义（24.md §19 sequence_revision）。顺序敏感：list 顺序
        就是播放顺序（§6「什么顺序」）；title/scene_id/时间戳不进（无关变更不 STALE）。"""
        return {"clips": [c.semantic_snapshot() for c in self.clips]}

    @property
    def revision(self) -> str:
        """sequence_revision：canonical render 语义指纹（OCC 对比键 + basis 组成项）。"""
        return fingerprint(self.semantic_snapshot())

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "sequence_id": self.sequence_id,
            "title": self.title,
            "scene_id": self.scene_id,
            "clips": [c.to_dict() for c in self.clips],
            "render_profile": (dict(self.render_profile)
                               if self.render_profile is not None else None),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SequenceBlock":
        if not isinstance(d, dict):
            raise SequenceError("sequence_invalid", "sequence block 必须是对象")
        for required in ("sequence_id", "clips"):
            if required not in d:
                raise SequenceError("sequence_invalid",
                                    f"sequence block 缺少 {required}（fail closed）")
        rp = d.get("render_profile")
        return cls(
            sequence_id=d.get("sequence_id"),
            clips=[SequenceClip.from_dict(c) for c in (d.get("clips") or [])],
            title=str(d.get("title") or ""),
            scene_id=d.get("scene_id"),
            render_profile=(dict(rp) if isinstance(rp, dict) and rp else None),
            created_at=float(d.get("created_at") or 0.0) or time.time(),
            updated_at=float(d.get("updated_at") or 0.0) or time.time(),
        )


# ============ SequenceRenderVersion（24.md §9，artifact version）============

@dataclass
class SequenceRenderVersion:
    """一次 Sequence render 的 artifact metadata（24.md §9）。
    是 artifact version，不是新的故事 canonical。status 生命周期：
    queued → rendering → done | failed | raced_superseded（§23）。"""
    version_id: str                      # "srender_" + uuid8
    sequence_id: str
    created_at: float = field(default_factory=time.time)
    # Phase 1（prepare）填充；Phase 3（commit）核对。"" = 尚未 prepare。
    sequence_revision: str = ""
    status: str = RSTATUS_QUEUED
    output_file: Optional[str] = None
    planned_duration_ms: Optional[int] = None    # TimelinePlan 总时长（§13 纯派生）
    duration_ms: Optional[int] = None            # 输出实测（ffprobe，§14）
    # §19 sequence_render_basis_v1（actual dependencies only；Phase 1 快照）
    generation_basis: Dict[str, Any] = field(default_factory=dict)
    # 实际消费的 source video versions（§9：[{shot_id, version_id}]）
    source_video_versions: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    finished_at: Optional[float] = None
    # ---- S2（25.md）----
    # §21 clipping advisory（混音峰值；None=未混音或未检出）。只报告不自动改 gain。
    audio_peak_warning: Optional[Dict[str, Any]] = None
    # ---- Phase51 B（B4）----
    # 接力端点 vs 实际保留端点一致性 advisory（relay_audit.audit_relay_endpoints；
    # None=无风险未检出）。只报告不阻断——处理选项（复核切点/重提取端点/保留并
    # 记录）由装配复核人工决定，不自动改 trim 或重生成。
    relay_endpoint_warning: Optional[Dict[str, Any]] = None
    # 方案 A 溯源：混音所用的 base render version（video stream copy 来源）；
    # None = 本 render 自行重建视频（无可用 base 或无 AudioPlan）。provenance 非
    # freshness 依赖（base 换了但 S1 键相同 → 视频比特一致 → 仍 CURRENT）。
    base_render_version_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not str(self.version_id or "").strip():
            raise SequenceError("render_version_invalid", "render version_id 不能为空")
        if not str(self.sequence_id or "").strip():
            raise SequenceError("render_version_invalid", "render version.sequence_id 不能为空")
        if self.status not in (RSTATUS_QUEUED, RSTATUS_RENDERING, RSTATUS_DONE,
                               RSTATUS_FAILED, RSTATUS_RACED):
            raise SequenceError("render_version_invalid",
                                f"未知 render status: {self.status!r}")

    @staticmethod
    def new_version_id() -> str:
        return f"srender_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict:
        return {
            "version_id": self.version_id,
            "sequence_id": self.sequence_id,
            "created_at": self.created_at,
            "sequence_revision": self.sequence_revision,
            "status": self.status,
            "output_file": self.output_file,
            "planned_duration_ms": self.planned_duration_ms,
            "duration_ms": self.duration_ms,
            "generation_basis": dict(self.generation_basis),
            "source_video_versions": list(self.source_video_versions),
            "error": self.error,
            "finished_at": self.finished_at,
            "audio_peak_warning": self.audio_peak_warning,
            "base_render_version_id": self.base_render_version_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SequenceRenderVersion":
        """known-field 过滤前向兼容（同 core/models parse 风格）。
        身份字段缺失 → fail closed（artifact 没有 identity 就不能作消费依据）。"""
        if not isinstance(d, dict):
            raise SequenceError("render_version_invalid", "render version 必须是对象")
        for required in ("version_id", "sequence_id"):
            if required not in d:
                raise SequenceError("render_version_invalid",
                                    f"render version 缺少 {required}（fail closed）")
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ============ Store 文档条目（block + render_versions + final_render 指针）============

@dataclass
class SequenceEntry:
    """output/sequences.json 里一个 sequence 的完整条目。
    render_versions[] 追加式（24.md §10）；final_render 是显式 select 指针——
    🔴 生成绝不自动覆盖正式结果（§10），指针只能由显式 select 设置。"""
    block: SequenceBlock
    render_versions: List[SequenceRenderVersion] = field(default_factory=list)
    final_render: Optional[str] = None

    def get_render(self, version_id: str) -> Optional[SequenceRenderVersion]:
        return next((r for r in self.render_versions if r.version_id == version_id), None)

    def final_render_version(self) -> Optional[SequenceRenderVersion]:
        if not self.final_render:
            return None
        r = self.get_render(self.final_render)
        if r is None or r.status != RSTATUS_DONE:
            # 指针悬空/指向非 done（不应发生——select 只收 done；防御性诚实处理）
            return None
        return r

    def to_dict(self) -> dict:
        return {
            "block": self.block.to_dict(),
            "render_versions": [r.to_dict() for r in self.render_versions],
            "final_render": self.final_render,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SequenceEntry":
        if not isinstance(d, dict):
            raise SequenceError("sequence_invalid", "sequence entry 必须是对象")
        for required in ("block",):
            if required not in d:
                raise SequenceError("sequence_invalid",
                                    f"sequence entry 缺少 {required}（fail closed）")
        return cls(
            block=SequenceBlock.from_dict(d.get("block") or {}),
            render_versions=[SequenceRenderVersion.from_dict(r)
                             for r in (d.get("render_versions") or [])],
            final_render=d.get("final_render"),
        )
