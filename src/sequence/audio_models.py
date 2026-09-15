# src/sequence/audio_models.py
# Sequence S2 — Audio Layer Foundation canonical 模型（25.md §7-§17/§26/§29）。
#
# 职责边界（25.md §2/§6）：
#   SequenceAudioPlan = Sequence Assembly 的声音侧 canonical，独立 revision domain
#   （§29：与 SequenceBlock revision 互相独立）。只表达「哪段声音、在 Sequence 时间轴
#   哪里、用源音频的哪一段、多大音量、怎么淡入淡出」。
#   不表达音频怎么生成（§2/§3：Audio Source Generation 不属于 S2）。
#
# 🔴 role 第一版严格限制（§8）：music / narration / ambience。Shot 内 dialogue/
#    lip-sync/local SFX/local ambience 已属 native AV，Sequence 层绝不重新拥有。
# 🔴 source 引用而非路径复制（§9/§10）：SequenceAudioSourceRef 只能引用既有 artifact
#    （A1 DialogueAudioVersion 或项目 imported audio）；解析不到 FAIL CLOSED；
#    禁止 glob/mtime/数组末位。
# 🔴 revision / fingerprint = sha256(canonical_json(语义子集))[:8]（同指纹族）。
#    layer_id 是稳定句柄不进 revision（actual deps only：同样语义的两层 = 同一混音）。
# 🔴 fail closed：非法 role / 负值 / fade 超长 / gain 越界 → SequenceError，不静默修正。
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.resources.generation import fingerprint

from .models import SequenceError

# 落盘 schema（output/sequence_audio_plans.json 根）
SEQUENCE_AUDIO_PLAN_STORE_SCHEMA_VERSION = 1

# mixer schema（25.md §26/§31-§32：进 basis；升级 = 带 plan 的旧 render 诚实 STALE）
SEQUENCE_AUDIO_MIXER_SCHEMA = "sequence_audio_mixer_v1"

# basis v2（25.md §26：plan 存在时的实际依赖 schema；命名按项目风格）
SEQUENCE_RENDER_BASIS_SCHEMA_V2 = "sequence_render_basis_v2"

# §8 role 第一版严格限制
AUDIO_LAYER_ROLES: Tuple[str, ...] = ("music", "narration", "ambience")

# source ref kind（§9 审计结论：仓库无通用 audio resource → 薄 ref 两形态）
AUDIO_SOURCE_KIND_A1 = "a1_dialogue_audio"       # 引用 A1 DialogueAudioVersion（只读）
AUDIO_SOURCE_KIND_IMPORTED = "imported_audio"    # 项目 output/audio_imports/ 下规范相对路径
AUDIO_IMPORTS_DIR = "audio_imports"              # imported artifact 规范目录（output/ 下）

# §16 gain 边界（fail closed 防手误；默认 0 dB，无魔法默认值）
GAIN_MIN_DB = -60.0
GAIN_MAX_DB = 24.0


def _check_nonneg_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SequenceError("audio_layer_invalid", f"{name} 必须是整数毫秒，收到: {value!r}")
    if value < 0:
        raise SequenceError("audio_layer_invalid", f"{name} 不能为负数，收到: {value}")
    return value


def _check_gain(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SequenceError("audio_layer_invalid", f"gain_db 必须是数值，收到: {value!r}")
    g = float(value)
    if not (GAIN_MIN_DB <= g <= GAIN_MAX_DB):
        raise SequenceError(
            "gain_out_of_range",
            f"gain_db={g} 超出 [{GAIN_MIN_DB}, {GAIN_MAX_DB}]（fail closed，防手误）")
    return g


# ============ SequenceAudioSourceRef（25.md §9/§10）============

@dataclass
class SequenceAudioSourceRef:
    """音频源引用：只能指向既有 artifact，绝不复制音频文件本身（§9）。
    - a1_dialogue_audio：本项目 A1 store（dialogue_audio.json）中的显式版本
      （dialogue_id + audio_version_id 双键，绝不默认 selected——§10 禁止隐式取值）；
    - imported_audio：用户/测试显式放入项目 output/audio_imports/ 的音频文件
      （仓库内规范相对路径；拒绝绝对路径 / .. / 越出 audio_imports/）。"""
    kind: str
    dialogue_id: Optional[str] = None
    audio_version_id: Optional[str] = None
    file: Optional[str] = None

    def __post_init__(self) -> None:
        self.kind = str(self.kind or "").strip()
        if self.kind == AUDIO_SOURCE_KIND_A1:
            self.dialogue_id = str(self.dialogue_id or "").strip()
            self.audio_version_id = str(self.audio_version_id or "").strip()
            if not self.dialogue_id or not self.audio_version_id:
                raise SequenceError(
                    "source_ref_invalid",
                    "a1_dialogue_audio 引用必须显式提供 dialogue_id + audio_version_id"
                    "（§10：绝不默认 selected/最后一位）")
            if self.file is not None:
                raise SequenceError("source_ref_invalid",
                                    "a1_dialogue_audio 引用不接受 file 字段")
        elif self.kind == AUDIO_SOURCE_KIND_IMPORTED:
            f = str(self.file or "").strip().replace("\\", "/")
            if not f:
                raise SequenceError("source_ref_invalid", "imported_audio 引用缺少 file")
            if f.startswith("/") or ":" in f.split("/", 1)[0] or ".." in f.split("/"):
                raise SequenceError(
                    "source_ref_invalid",
                    f"imported_audio.file 必须是仓库内 audio_imports/ 相对路径，收到: {f!r}")
            if not f.startswith(f"{AUDIO_IMPORTS_DIR}/"):
                raise SequenceError(
                    "source_ref_invalid",
                    f"imported_audio.file 必须位于 {AUDIO_IMPORTS_DIR}/ 下，收到: {f!r}")
            self.file = f
            self.dialogue_id = None
            self.audio_version_id = None
        else:
            raise SequenceError("source_ref_invalid",
                                f"未知 audio source kind: {self.kind!r}"
                                f"（合法: {AUDIO_SOURCE_KIND_A1}, {AUDIO_SOURCE_KIND_IMPORTED}）")

    def semantic_snapshot(self) -> Dict[str, Any]:
        """进 revision / basis 的语义子集（artifact 身份；不含文件内容——内容
        由 render Phase 1 的 sha256 现场指纹捕获，§28）。"""
        if self.kind == AUDIO_SOURCE_KIND_A1:
            return {"kind": self.kind, "dialogue_id": self.dialogue_id,
                    "audio_version_id": self.audio_version_id}
        return {"kind": self.kind, "file": self.file}

    def to_dict(self) -> dict:
        return dict(self.semantic_snapshot())

    @classmethod
    def from_dict(cls, d: dict) -> "SequenceAudioSourceRef":
        if not isinstance(d, dict):
            raise SequenceError("source_ref_invalid", "audio source ref 必须是对象")
        if "kind" not in d:
            raise SequenceError("source_ref_invalid", "audio source ref 缺少 kind（fail closed）")
        return cls(kind=d.get("kind"), dialogue_id=d.get("dialogue_id"),
                   audio_version_id=d.get("audio_version_id"), file=d.get("file"))


# ============ SequenceAudioLayer（25.md §7/§11-§17）============

@dataclass
class SequenceAudioLayer:
    """一个声音层。时间坐标全部是 Sequence 时间（§11：0ms = Sequence 渲染第一帧）。
    - start_ms：层在 Sequence timeline 上的起点（§12；>=0；越界 render 时 fail closed）；
    - source_in_ms：从源音频内部第几毫秒开始用（§13）；
    - duration_ms：本层在 Sequence 中使用的显式时长（§14 第一版；不自动 loop §15）；
    - gain_db：层增益（§16；默认 0 dB，无魔法默认）；
    - fade_in_ms / fade_out_ms：线性淡入淡出（§17；两者之和 <= duration_ms）。
    结构校验在此处；媒体事实校验（源时长 / Sequence 时长 / 音频流存在）在 render
    Phase 1 以 ffprobe/文件为准（§5/§10/§14/§33——不根据历史值猜）。"""
    layer_id: str
    role: str
    source: SequenceAudioSourceRef
    start_ms: int
    duration_ms: int
    source_in_ms: int = 0
    gain_db: float = 0.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0

    def __post_init__(self) -> None:
        self.layer_id = str(self.layer_id or "").strip()
        if not self.layer_id:
            raise SequenceError("audio_layer_invalid", "layer_id 不能为空")
        self.role = str(self.role or "").strip()
        if self.role not in AUDIO_LAYER_ROLES:
            raise SequenceError("audio_layer_invalid",
                                f"role 必须是 {AUDIO_LAYER_ROLES} 之一（§8 严格限制），"
                                f"收到: {self.role!r}")
        if isinstance(self.source, SequenceAudioSourceRef):
            self.source.__post_init__()
        else:
            self.source = SequenceAudioSourceRef.from_dict(self.source)
        self.start_ms = _check_nonneg_int(self.start_ms, "start_ms")
        self.source_in_ms = _check_nonneg_int(self.source_in_ms, "source_in_ms")
        self.duration_ms = _check_nonneg_int(self.duration_ms, "duration_ms")
        if self.duration_ms <= 0:
            raise SequenceError("audio_layer_invalid", "duration_ms 必须大于 0")
        self.gain_db = _check_gain(self.gain_db)
        self.fade_in_ms = _check_nonneg_int(self.fade_in_ms, "fade_in_ms")
        self.fade_out_ms = _check_nonneg_int(self.fade_out_ms, "fade_out_ms")
        if self.fade_in_ms + self.fade_out_ms > self.duration_ms:
            raise SequenceError(
                "audio_layer_invalid",
                f"fade_in({self.fade_in_ms}) + fade_out({self.fade_out_ms}) "
                f"> duration_ms({self.duration_ms})（§17 fail closed）")

    @staticmethod
    def new_layer_id() -> str:
        return f"layer_{uuid.uuid4().hex[:8]}"

    def semantic_snapshot(self) -> Dict[str, Any]:
        """render-affecting 语义（plan revision / basis 组成项）。layer_id 是句柄
        不进指纹（同语义 = 同混音）。"""
        return {"role": self.role, "source": self.source.semantic_snapshot(),
                "start_ms": self.start_ms, "source_in_ms": self.source_in_ms,
                "duration_ms": self.duration_ms, "gain_db": self.gain_db,
                "fade_in_ms": self.fade_in_ms, "fade_out_ms": self.fade_out_ms}

    def to_dict(self) -> dict:
        return {"layer_id": self.layer_id, **self.semantic_snapshot()}

    @classmethod
    def from_dict(cls, d: dict) -> "SequenceAudioLayer":
        if not isinstance(d, dict):
            raise SequenceError("audio_layer_invalid", "audio layer 必须是对象")
        for required in ("role", "source", "start_ms", "duration_ms"):
            if required not in d:
                raise SequenceError("audio_layer_invalid",
                                    f"audio layer 缺少 {required}（fail closed）")
        known = ("layer_id", "role", "source", "start_ms", "source_in_ms",
                 "duration_ms", "gain_db", "fade_in_ms", "fade_out_ms")
        d = {k: v for k, v in d.items() if k in known}
        return cls(layer_id=d.get("layer_id") or SequenceAudioLayer.new_layer_id(),
                   **{k: v for k, v in d.items() if k != "layer_id"})


# ============ SequenceAudioPlan（25.md §7，声音侧 canonical）============

@dataclass
class SequenceAudioPlan:
    """一个 Sequence 的声音编排 canonical（与 SequenceBlock 独立 revision，§29）。
    每 sequence 至多一个 plan；layers 为空 = 不存在 plan（store 层归一化，
    避免「空 plan」与「无 plan」双真相）。"""
    sequence_id: str
    layers: List[SequenceAudioLayer] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not str(self.sequence_id or "").strip():
            raise SequenceError("audio_plan_invalid", "audio plan.sequence_id 不能为空")
        self.sequence_id = str(self.sequence_id).strip()
        self.layers = [l if isinstance(l, SequenceAudioLayer) else SequenceAudioLayer.from_dict(l)
                       for l in (self.layers or [])]

    def semantic_snapshot(self) -> Dict[str, Any]:
        """plan revision = layers 语义指纹（列表顺序敏感——编辑顺序视为编辑）。"""
        return {"layers": [l.semantic_snapshot() for l in self.layers]}

    @property
    def revision(self) -> str:
        return fingerprint(self.semantic_snapshot())

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {"sequence_id": self.sequence_id,
                "revision": self.revision,
                "layers": [l.to_dict() for l in self.layers],
                "created_at": self.created_at, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, d: dict) -> "SequenceAudioPlan":
        if not isinstance(d, dict):
            raise SequenceError("audio_plan_invalid", "audio plan 必须是对象")
        for required in ("sequence_id", "layers"):
            if required not in d:
                raise SequenceError("audio_plan_invalid",
                                    f"audio plan 缺少 {required}（fail closed）")
        return cls(sequence_id=d.get("sequence_id"),
                   layers=[SequenceAudioLayer.from_dict(l) for l in (d.get("layers") or [])],
                   created_at=float(d.get("created_at") or 0.0) or time.time(),
                   updated_at=float(d.get("updated_at") or 0.0) or time.time())
