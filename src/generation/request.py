# src/generation/request.py
# GenerationRequest — provider-neutral 生成请求 schema（31.md §10/§11）。
#
# 职责：Bound*Adapter 产物 → 中立请求描述 → per-provider compiler（agnes_adapter 等）
#       → 真实 provider client。未来新增 provider 只需新增 compiler，不改 Binding/Pack/
#       adapter（31.md §20：如果未来必须改 Binding/Pack 才能支持新 provider，说明抽象做错）。
#
# 纪律：
#   - 本 schema 属 generation 边界包，**绝不写回 canonical / Binding / Pack**（§17 冻结）；
#     ephemeral、无 ID、不落盘。
#   - 不携带 provider-specific 字段形状（image 数组位置 / extra_body / size 桶解析属
#     agnes_adapter 的知识，§10：不要把 provider request schema 写进 canonical 层）。
#   - media 已由权威 owner 解析（image_adapter §23：ReferenceAsset→Resources→URL/data URI）；
#     本层不 glob / 不猜路径 / 不再解析。
#   - derivation 记录 adapter 级派生物（如 identity crop）的来源与规则——派生物不是
#     canonical asset，provenance 必须能回答"这张参考图从哪来"（31.md §12）。
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

GENERATION_TYPES = ("first_frame", "last_frame", "video")


@dataclass
class ReferenceInput:
    """单个参考输入（neutral）。identity = asset_id（ReferenceAsset id / boundary
    candidate id / continuity 描述）；media 为已解析的 provider 媒体（URL 或 data URI）。
    顺序即发送顺序（pack 确定性序 + layout 语义，见 BoundImageRequest.ref_layout）。"""
    asset_id: str = ""
    source_type: str = ""
    semantic_role: str = ""
    priority: str = ""
    temporal_scope: str = ""
    subject_character: Optional[str] = None
    prop_id: Optional[str] = None
    media: str = ""
    checksum: Optional[str] = None
    derivation: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "source_type": self.source_type,
            "semantic_role": self.semantic_role,
            "priority": self.priority,
            "temporal_scope": self.temporal_scope,
            "subject_character": self.subject_character,
            "prop_id": self.prop_id,
            "media": self.media,
            "checksum": self.checksum,
            "derivation": dict(self.derivation) if self.derivation else None,
        }


@dataclass
class GenerationRequest:
    """一次生成的中立请求。diagnostics/provenance 随请求携带（31.md §12：一次生成
    究竟用了什么 reference 可以被完整追溯——compiled prompt + 发送清单 + 舍弃清单 +
    basis）。negative_prompt/seed/model 是 schema 槽位：当前图片 provider 不支持
    negative/seed（编译层诚实拒绝或丢弃并记 diagnostic，绝不伪造支持）；视频 seed/
    negative 属 Shot creative 字段，由 handler 既有 kwargs 通道传递。"""
    generation_type: str = ""
    shot_id: str = ""
    prompt: str = ""
    negative_prompt: Optional[str] = None
    reference_inputs: List[ReferenceInput] = field(default_factory=list)
    first_frame: Optional[str] = None          # video 槽（i2v/keyframes first）
    last_frame: Optional[str] = None           # video 槽（仅 keyframes）
    ratio: str = ""
    size_tier: str = ""
    seed: Optional[int] = None
    model: Optional[str] = None
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "generation_type": self.generation_type,
            "shot_id": self.shot_id,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "reference_inputs": [r.to_dict() for r in self.reference_inputs],
            "first_frame": self.first_frame,
            "last_frame": self.last_frame,
            "ratio": self.ratio,
            "size_tier": self.size_tier,
            "seed": self.seed,
            "model": self.model,
            "diagnostics": list(self.diagnostics),
            "provenance": dict(self.provenance),
        }
