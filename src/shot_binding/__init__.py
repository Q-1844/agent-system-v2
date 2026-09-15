# src/shot_binding/__init__.py
# Shot Production Binding V1 — 独立 canonical companion 包（Phase S3B.1）。
#
# output/shot_production_bindings.json：Shot 的生产绑定（语义资源引用 + 依赖指纹快照）。
# 🔴 Shot schema / project.json / Resources / Scene System 零改动（S3A §7.1 裁决）。
# 🔴 binding 只存语义 ID，绝不存图片路径（S3A §5：raw image path 不是 creative truth）。
# 范围：S3B.1 = canonical + store + service(CRUD/OCC)。以下明确**不在本包**：
#   pack_compiler（S3B.3）/ validity read model + refresh（S3B.2）/ HTTP router（S3B.4）。
from .models import (
    ACTION_KINDS_REQUIRING_PROP,
    SHOT_BINDING_SCHEMA_VERSION,
    BindingAction,
    BindingActionKind,
    CharacterBinding,
    DependencyFingerprints,
    DialogueSpeakerBinding,
    DialogueSpeakerLine,
    PropBinding,
    SceneBinding,
    ShotBindingDocument,
    ShotBindingSchemaError,
    ShotProductionBinding,
    binding_revision,
    document_revision,
)
from .store import ShotBindingStore
from .service import ShotBindingService
from .reference_pack import (
    PACK_VERSION,
    ActionProjection,
    CharacterPackEntry,
    PackBasis,
    PackBlockedError,
    ProfileProjection,
    PropPackEntry,
    ReferenceItem,
    SceneProjection,
    ShotReferencePack,
)

__all__ = [
    "ACTION_KINDS_REQUIRING_PROP",
    "SHOT_BINDING_SCHEMA_VERSION",
    "BindingAction",
    "BindingActionKind",
    "CharacterBinding",
    "DependencyFingerprints",
    "DialogueSpeakerBinding",
    "DialogueSpeakerLine",
    "PropBinding",
    "SceneBinding",
    "ShotBindingDocument",
    "ShotBindingSchemaError",
    "ShotProductionBinding",
    "ShotBindingStore",
    "ShotBindingService",
    "binding_revision",
    "document_revision",
    "PACK_VERSION",
    "PackBasis",
    "PackBlockedError",
    "ProfileProjection",
    "SceneProjection",
    "CharacterPackEntry",
    "PropPackEntry",
    "ActionProjection",
    "ReferenceItem",
    "ShotReferencePack",
]
