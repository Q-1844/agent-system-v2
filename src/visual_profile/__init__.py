# src/visual_profile/__init__.py
# Project Visual Profile V1 — Phase P1：项目级视觉定义 canonical。
#
# 🔴 定位（Phase P1 §3）：ProjectVisualProfile 表达**全片视觉介质/艺术方向**
#    （render_family + art_direction + style_tags + negative_style_constraints）。
#    与 Location.style_notes（稳定地点身份）、SceneState（时间/天气/灯光/损坏）
#    三层不得混淆（P1 §12）。
#
# 🔴 存储边界（P1 §5）：projects/{project}/output/project_profile.json，
#    schema_version=1。不修改 project.json。纯读绝不创建文件；
#    第一次真实设置才创建。旧项目 read → unspecified（None）。
#
# 🔴 分层（P1 §3）：视觉目标与模型实现分离——本 canonical 绝不存
#    provider/model_id/API config/seed/candidate_count。
from .models import (
    RENDER_FAMILIES,
    VISUAL_PROFILE_SCHEMA_VERSION,
    ProjectVisualProfile,
    VisualProfileSchemaError,
    parse_visual_profile,
    profile_revision,
    profile_snapshot,
    style_compatibility_of_basis,
    visual_profile_fingerprint,
)
from .store import VisualProfileStore, load_profile_or_none
from .compiler import compile_project_style_instruction, render_family_instruction
from .service import VisualProfileService

__all__ = [
    "RENDER_FAMILIES",
    "VISUAL_PROFILE_SCHEMA_VERSION",
    "ProjectVisualProfile",
    "VisualProfileSchemaError",
    "parse_visual_profile",
    "profile_revision",
    "profile_snapshot",
    "visual_profile_fingerprint",
    "style_compatibility_of_basis",
    "VisualProfileStore",
    "load_profile_or_none",
    "compile_project_style_instruction",
    "render_family_instruction",
    "VisualProfileService",
    "style_compatibility_of_basis",
]
