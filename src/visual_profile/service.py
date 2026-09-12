# src/visual_profile/service.py
# Project Visual Profile V1 — Phase P1 §7/§9/§20：read/update 服务层。
#
# 🔴 update OCC（P1 §8）：expected_revision == 当前语义指纹才写；unspecified 项目
#    当前 revision 约定为 ""（首次写入）。stale → reject，不 silent retry。
#    check-then-write 在 project_write_lock 临界区内（同 project_name key 与
#    project.json / resources.json 写路径同锁族）。
# 🔴 修改语义（P1 §9）：profile 允许修改；已有 approved visual assets 时**不自动
#    重新生成/删除/revoke**——derived stale 由 read 层自然涌现；本服务只诚实计算
#    has_approved_visual_assets 供 UI 强提示。
# 🔴 has_approved_visual_assets（只读派生）：resources.json 任一实体有 approved
#    参考/审批，或 scene_system.json 任一 view/scene_instance_view approved。
#    store 不可读 → False + check_error（advisory 字段，不阻塞 profile 读写）。
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from .models import (
    ProjectVisualProfile,
    VisualProfileSchemaError,
    parse_visual_profile,
    profile_revision,
    profile_snapshot,
    visual_profile_fingerprint,
)
from .store import VisualProfileStore


def _resources_has_approved(pdir: Path) -> tuple:
    try:
        from src.resources.store import ResourceStore
        doc = ResourceStore(pdir).load()
    except Exception as e:   # noqa: BLE001 - advisory 派生字段：诚实报错，不阻塞
        return False, f"resources.json unreadable: {e}"
    for lk in doc.looks.values():
        if lk.approved_reference_asset_ids:
            return True, ""
    for c in doc.clothings.values():
        if c.approved_reference_asset_ids:
            return True, ""
    for p in doc.props.values():
        if p.approved_reference_asset_ids:
            return True, ""
    for c in doc.compositions.values():
        if getattr(c, "approved_reference_asset_ids", None):
            return True, ""
    return False, ""


def _scene_has_approved(pdir: Path) -> tuple:
    try:
        from src.scene_system.store import SceneSystemStore
        doc = SceneSystemStore(pdir).load()
    except Exception as e:   # noqa: BLE001
        return False, f"scene_system.json unreadable: {e}"
    for v in doc.views.values():
        if v.approval_status == "approved":
            return True, ""
    for v in doc.scene_instance_views.values():
        if v.approval_status == "approved":
            return True, ""
    return False, ""


def has_approved_visual_assets(project_dir) -> dict:
    """跨域只读派生（P1 §9）：项目里是否已存在 approved visual assets。"""
    pdir = Path(project_dir)
    errors = []
    res_yes, res_err = _resources_has_approved(pdir)
    if res_err:
        errors.append(res_err)
    scene_yes, scene_err = _scene_has_approved(pdir)
    if scene_err:
        errors.append(scene_err)
    return {"has_approved_visual_assets": bool(res_yes or scene_yes),
            "check_error": "; ".join(errors) if errors else ""}


class VisualProfileService:
    """project_profile.json 读/写服务（HTTP 层只做 mapping）。"""

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.store = VisualProfileStore(project_dir, project_name)

    # ============ read（P1 §5/§20：纯读不创建；旧项目 unspecified）============
    def read(self) -> dict:
        profile = self.store.load_or_none()
        if profile is None:
            return {
                "specified": False,
                "visual_profile": None,
                "revision": "",
                **has_approved_visual_assets(self.store.dir),
            }
        return {
            "specified": True,
            "visual_profile": profile.to_dict(),
            "visual_profile_snapshot": profile_snapshot(profile),
            "visual_profile_fingerprint": visual_profile_fingerprint(profile),
            "revision": profile_revision(profile),
            **has_approved_visual_assets(self.store.dir),
        }

    # ============ update（P1 §8/§9：OCC，stale → reject，不 silent retry）============
    def update(self, *, render_family: str, art_direction: str = "",
               style_tags: Optional[list] = None,
               negative_style_constraints: Optional[list] = None,
               expected_revision: str = "") -> dict:
        op = "visual_profile.update"
        try:
            candidate = ProjectVisualProfile(
                render_family=str(render_family or ""),
                art_direction=str(art_direction or ""),
                style_tags=list(style_tags or []),
                negative_style_constraints=list(negative_style_constraints or []),
            )
        except VisualProfileSchemaError as e:
            return {"ok": False, "operation": op, "error": "validation_error",
                    "detail": str(e)}
        async_lock = self.store.write_lock()
        # 纯 sync 服务：锁是 asyncio.Lock——update 的写端点走 async router 时用
        # update_async；本方法供 sync 测试/工具调用（单写者上下文）。
        current = self.store.load_or_none()
        cur_rev = profile_revision(current) if current is not None else ""
        if expected_revision != cur_rev:
            return {"ok": False, "operation": op, "error": "stale_revision",
                    "detail": "expected_revision 与当前 visual profile revision 不一致"
                              "（unspecified 项目当前 revision 为 \"\"）",
                    "expected_revision": expected_revision, "current_revision": cur_rev}
        now = time.time()
        if current is None:
            candidate.created_at = now
            candidate.updated_at = now
        else:
            candidate.created_at = current.created_at
            candidate.updated_at = now
        self.store.save(candidate)
        return {"ok": True, "operation": op,
                "visual_profile": candidate.to_dict(),
                "revision": profile_revision(candidate),
                **has_approved_visual_assets(self.store.dir)}

    async def update_async(self, **kw) -> dict:
        """async 写端点用：OCC check-then-write 全程持 project_write_lock。"""
        async with self.store.write_lock():
            return self.update(**kw)
