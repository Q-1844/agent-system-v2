# src/resources/write_service.py
# Production Resources V1 — Phase 1C1 Work A：统一异步写 service。
#
# 🔴 职责边界（Phase 1C1 §3）：
#   ResourceStore 继续 load/save/serialization/typed storage（sync，纯存储）。
#   WriteService 负责 lock + OCC + validation + mutation + receipt。
#   网络调用（图片模型）不进 ResourceStore，也不在本文件的 mutation 临界区内等待
#   （generation 三阶段协议见 Work C/D：短锁 snapshot → 无锁生成 → 重锁验证提交）。
#
# 🔴 OCC 规则（§4）：
#   - 更新/删除单个资源：expected_revision = 当前 resource revision（deterministic 指纹）。
#   - 创建新资源：expected_document_revision = 当前 ResourceDocument revision。
#   - stale → 不写入、不自动替换 revision、不 silent retry，返回结构化：
#     {ok: False, error: "stale_revision", expected_revision, current_revision, current}
#
# 🔴 锁规则（§5）：所有 load→validate→mutate→save 必须在
#   `async with project_write_lock(project_name)` 短临界区内（同 project.json 写串行化）。
#
# receipt 语义（§28）：中性 ResourceMutationReceipt——字段兼容 _base_receipt
#   （ok/operation/target/before/after/revision/verified/error），但**不 import 任何
#   Agent 模块**（resources 基础设施不反向依赖 Agent runtime）。
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.store import ProjectStore

from .models import (
    ApprovalState,
    CharacterLook,
    Clothing,
    GenerationStatus,
    ReferenceAsset,
    document_revision,
    clothing_revision,
    look_revision,
    reference_asset_revision,
    new_clothing_id,
    new_look_id,
    new_reference_asset_id,
)
from .store import ResourceStore

# 本轮允许注册的本地图片扩展名（注册的是"已有图片"；其余一律拒绝，不猜内容）
_ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# Clothing 可 PATCH 字段（rename = 改 name；clothing_id 恒不变）
_CLOTHING_PATCH_FIELDS = {"name", "description", "design_traits", "materials", "colors", "tags"}
# Look 可 PATCH 字段（character_name 是 FK 身份，不可改；改外观语义直接改这些字段）
_LOOK_PATCH_FIELDS = {"label", "clothing_id", "appearance_description", "appearance_delta"}


def _type_name(v: Any) -> str:
    return type(v).__name__


class ResourceWriteService:
    """项目资源统一异步写 service（Phase 1C1）。

    所有方法：
      - 持 `project_write_lock(project_name)` 短临界区（load→validate→OCC→mutate→save）；
      - OCC 失败 → 结构化 stale receipt（不写入）；
      - 期望失败一律返回 receipt（ok=False + error 码），不抛异常；
      - 只有意外内部错误（磁盘 IO / schema 损坏）向上抛。
      - save 后重读磁盘验证 → receipt.verified（与 Agent 域 truth-gate 同语义）。
    """

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.project_dir = Path(project_dir)
        self.project_name = project_name or self.project_dir.name
        self.store = ResourceStore(self.project_dir, self.project_name)

    # ============ receipt（中性，字段兼容 _base_receipt）============
    @staticmethod
    def _receipt(operation: str, *, ok: bool, target: Any = None,
                 before: Any = None, after: Any = None,
                 revision: Optional[str] = None, verified: bool = False,
                 error: Optional[str] = None, **extra) -> dict:
        rec: Dict[str, Any] = {
            "ok": ok, "operation": operation, "target": target,
            "before": before, "after": after, "revision": revision,
            "verified": verified, "error": error,
        }
        rec.update(extra)
        return rec

    @staticmethod
    def _stale_receipt(operation: str, expected: str, current: str,
                       current_dict: Any = None) -> dict:
        """§4 固定 stale 形状。不自动替换 revision、不 silent retry。
        单资源操作附 current（当前资源序列化）；document 级操作 current=None
        （文档整体可经 read API 取回，避免在错误里重复携带全量）。"""
        return {
            "ok": False, "operation": operation, "error": "stale_revision",
            "expected_revision": expected, "current_revision": current,
            "current": current_dict,
        }

    # ============ OCC 期望值解析 ============
    @staticmethod
    def _require_str(value: Any, field: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} 必须是 string，得到 {_type_name(value)}")
        return value

    # ============ 本地图片路径验证（§8）============
    def _validate_local_image(self, local: str) -> Path:
        """仅接受项目根内相对路径；拒绝绝对路径、.. 穿越、解析后逃出项目根、
        非图片扩展名、文件不存在。返回解析后的绝对 Path。"""
        if not isinstance(local, str) or not local.strip():
            raise ValueError("local 必须是非空相对路径字符串")
        p = Path(local.strip())
        if p.is_absolute():
            raise ValueError(f"拒绝绝对路径（只接受项目内相对路径）: {local}")
        if ".." in p.parts:
            raise ValueError(f"拒绝包含 '..' 的路径: {local}")
        if p.suffix.lower() not in _ALLOWED_IMAGE_SUFFIXES:
            raise ValueError(f"不支持的图片扩展名: {p.suffix or '(无)'} ({local})")
        root = self.project_dir.resolve()
        resolved = (root / p).resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"路径解析后逃出项目根: {local}")
        if not resolved.is_file():
            raise ValueError(f"本地文件不存在: {local}")
        return resolved

    @staticmethod
    def _sha256_file(path: Path) -> str:
        h = hashlib.sha256()
        h.update(path.read_bytes())
        return h.hexdigest()

    # ============ 保存后重读验证（truth gate）============
    def _verify_after_save(self) -> str:
        """save 后从磁盘重读，返回 document revision（同时确认文件真实落盘可读）。"""
        reloaded = self.store.load()   # 每次从磁盘读（ResourceStore 无缓存）
        return document_revision(reloaded)

    # ==================================================================
    # Clothing CRUD（§7）
    # ==================================================================
    async def create_clothing(self, *, name: str, description: str = "",
                              design_traits: str = "", materials: Optional[List[str]] = None,
                              colors: Optional[List[str]] = None,
                              tags: Optional[List[str]] = None,
                              expected_document_revision: str = "") -> dict:
        op = "resource.create_clothing"
        try:
            name = self._require_str(name, "name")
        except ValueError as e:
            return self._receipt(op, ok=False, error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            cur = document_revision(doc)
            if expected_document_revision != cur:
                return self._stale_receipt(op, expected_document_revision, cur)
            c = Clothing(clothing_id=new_clothing_id(), name=name,
                         description=description or "", design_traits=design_traits or "",
                         materials=list(materials or []), colors=list(colors or []),
                         tags=list(tags or []),
                         approval_status=ApprovalState.UNREVIEWED.value)
            doc.clothings[c.clothing_id] = c
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().clothings.get(c.clothing_id)
            verified = reloaded is not None and clothing_revision(reloaded) == clothing_revision(c)
            return self._receipt(op, ok=True, target=c.clothing_id,
                                 before=None, after=c.to_dict(),
                                 revision=clothing_revision(c), verified=verified,
                                 document_revision_after=doc_rev)

    async def update_clothing(self, clothing_id: str, patch: Dict[str, Any], *,
                              expected_revision: str) -> dict:
        op = "resource.update_clothing"
        try:
            clothing_id = self._require_str(clothing_id, "clothing_id")
            unknown = set(patch) - _CLOTHING_PATCH_FIELDS
            if unknown:
                raise ValueError(f"未知/不可变字段: {sorted(unknown)}（可改: {sorted(_CLOTHING_PATCH_FIELDS)}）")
            for f in ("materials", "colors", "tags"):
                if f in patch and patch[f] is not None and not isinstance(patch[f], list):
                    raise ValueError(f"{f} 必须是 list")
        except ValueError as e:
            return self._receipt(op, ok=False, target=clothing_id,
                                 error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            c = doc.clothings.get(clothing_id)
            if c is None:
                return self._receipt(op, ok=False, target=clothing_id, error="not_found",
                                     detail=f"clothing 不存在: {clothing_id}")
            cur = clothing_revision(c)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, c.to_dict())
            before = c.to_dict()
            for f in _CLOTHING_PATCH_FIELDS:
                if f in patch and patch[f] is not None:
                    setattr(c, f, patch[f])
            c.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().clothings.get(clothing_id)
            verified = reloaded is not None and clothing_revision(reloaded) == clothing_revision(c)
            return self._receipt(op, ok=True, target=clothing_id,
                                 before=before, after=c.to_dict(),
                                 revision=clothing_revision(c), verified=verified,
                                 document_revision_after=doc_rev)

    async def delete_clothing(self, clothing_id: str, *, expected_revision: str) -> dict:
        op = "resource.delete_clothing"
        async with self.store.write_lock():
            doc = self.store.load()
            c = doc.clothings.get(clothing_id)
            if c is None:
                return self._receipt(op, ok=False, target=clothing_id, error="not_found",
                                     detail=f"clothing 不存在: {clothing_id}")
            cur = clothing_revision(c)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, c.to_dict())
            # §7 reference check：仍有 Look.clothing_id == clothing_id → 禁止删除（无级联）
            used_by = [lk.look_id for lk in doc.looks.values() if lk.clothing_id == clothing_id]
            if used_by:
                return self._receipt(op, ok=False, target=clothing_id,
                                     error="resource_in_use",
                                     detail="clothing 仍被 Look 引用，禁止删除（本轮不做级联）",
                                     used_by_looks=used_by)
            before = c.to_dict()
            del doc.clothings[clothing_id]
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            verified = self.store.load().clothings.get(clothing_id) is None
            return self._receipt(op, ok=True, target=clothing_id,
                                 before=before, after=None,
                                 revision=None, verified=verified,
                                 document_revision_after=doc_rev)

    # ==================================================================
    # ReferenceAsset registration（§8）
    # ==================================================================
    async def register_reference_asset(self, *, local: Optional[str] = None,
                                       url: Optional[str] = None,
                                       view: str = "", purpose: str = "",
                                       source: str = "manual",
                                       background_type: str = "",
                                       expected_document_revision: str = "") -> dict:
        """注册已有 URL 或项目内已有本地图片为 ReferenceAsset。
        - local：项目内相对路径，验证后计算真实 sha256 checksum。
        - url：必须 http(s)。
        - view/purpose/background_type：只在调用方明确提供时才写入，不从文件名猜。
        - 绝不把 base64 写进 resources.json。"""
        op = "resource.register_reference_asset"
        try:
            if (local is None) == (url is None):
                raise ValueError("local 与 url 必须二选一")
            if url is not None and not (isinstance(url, str) and url.startswith(("http://", "https://"))):
                raise ValueError("url 必须以 http:// 或 https:// 开头")
            checksum: Optional[str] = None
            stored_local: Optional[str] = None
            if local is not None:
                resolved = self._validate_local_image(local)
                checksum = self._sha256_file(resolved)
                stored_local = Path(local.strip()).as_posix()
        except ValueError as e:
            return self._receipt(op, ok=False, error="invalid_reference_input", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            cur = document_revision(doc)
            if expected_document_revision != cur:
                return self._stale_receipt(op, expected_document_revision, cur)
            a = ReferenceAsset(asset_id=new_reference_asset_id(),
                               local=stored_local, url=url,
                               checksum=checksum,
                               view=view or "", purpose=purpose or "",
                               background_type=background_type or "",
                               source=source or "manual")
            doc.reference_assets[a.asset_id] = a
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().reference_assets.get(a.asset_id)
            verified = (reloaded is not None
                        and reference_asset_revision(reloaded) == reference_asset_revision(a))
            return self._receipt(op, ok=True, target=a.asset_id,
                                 before=None, after=a.to_dict(),
                                 revision=reference_asset_revision(a), verified=verified,
                                 document_revision_after=doc_rev)

    # ==================================================================
    # Clothing ↔ ReferenceAsset 绑定（§9）
    # ==================================================================
    async def attach_clothing_reference(self, clothing_id: str, asset_id: str, *,
                                        expected_revision: str) -> dict:
        op = "resource.attach_clothing_reference"
        async with self.store.write_lock():
            doc = self.store.load()
            c = doc.clothings.get(clothing_id)
            if c is None:
                return self._receipt(op, ok=False, target=clothing_id, error="not_found",
                                     detail=f"clothing 不存在: {clothing_id}")
            cur = clothing_revision(c)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, c.to_dict())
            if asset_id not in doc.reference_assets:
                return self._receipt(op, ok=False, target=clothing_id, error="asset_not_found",
                                     detail=f"reference asset 不存在: {asset_id}")
            if asset_id in c.reference_asset_ids:
                # 幂等 attach：已在列表 → 不改文档，诚实返回 unchanged
                return self._receipt(op, ok=True, target=clothing_id,
                                     after=c.to_dict(), revision=cur, verified=True,
                                     unchanged=True, error=None)
            before = c.to_dict()
            c.reference_asset_ids.append(asset_id)
            c.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().clothings.get(clothing_id)
            verified = reloaded is not None and asset_id in reloaded.reference_asset_ids
            return self._receipt(op, ok=True, target=clothing_id,
                                 before=before, after=c.to_dict(),
                                 revision=clothing_revision(c), verified=verified,
                                 document_revision_after=doc_rev)

    async def detach_clothing_reference(self, clothing_id: str, asset_id: str, *,
                                        expected_revision: str) -> dict:
        op = "resource.detach_clothing_reference"
        async with self.store.write_lock():
            doc = self.store.load()
            c = doc.clothings.get(clothing_id)
            if c is None:
                return self._receipt(op, ok=False, target=clothing_id, error="not_found",
                                     detail=f"clothing 不存在: {clothing_id}")
            cur = clothing_revision(c)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, c.to_dict())
            if asset_id not in c.reference_asset_ids:
                return self._receipt(op, ok=True, target=clothing_id,
                                     after=c.to_dict(), revision=cur, verified=True,
                                     unchanged=True, error=None)
            before = c.to_dict()
            c.reference_asset_ids.remove(asset_id)
            c.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().clothings.get(clothing_id)
            verified = reloaded is not None and asset_id not in reloaded.reference_asset_ids
            return self._receipt(op, ok=True, target=clothing_id,
                                 before=before, after=c.to_dict(),
                                 revision=clothing_revision(c), verified=verified,
                                 document_revision_after=doc_rev)

    # ==================================================================
    # CharacterLook create / update（§6）
    # ==================================================================
    async def create_look(self, *, character_name: str, label: str = "",
                          clothing_id: Optional[str] = None,
                          appearance_description: str = "", appearance_delta: str = "",
                          expected_document_revision: str = "") -> dict:
        """创建 Look。验证：Character 真实存在于 Project canonical；clothing_id
        非空时 Clothing 真实存在于 resources.json。创建后 generation_status=none /
        approval_status=unreviewed；不自动生成图片、不自动建 Base Look、不改 Character。"""
        op = "resource.create_look"
        try:
            character_name = self._require_str(character_name, "character_name")
            if not character_name.strip():
                raise ValueError("character_name 不能为空")
        except ValueError as e:
            return self._receipt(op, ok=False, error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            cur = document_revision(doc)
            if expected_document_revision != cur:
                return self._stale_receipt(op, expected_document_revision, cur)
            # Character 必须真实存在于 project.json canonical（只 load，绝不写 project.json）
            try:
                project = ProjectStore(self.project_dir).load()
            except FileNotFoundError:
                project = None
            char = project.get_character(character_name) if project else None
            if char is None:
                return self._receipt(op, ok=False, error="character_not_found",
                                     detail=f"Character 不存在于项目 canonical: {character_name}")
            if clothing_id is not None and clothing_id not in doc.clothings:
                return self._receipt(op, ok=False, error="clothing_not_found",
                                     detail=f"Clothing 不存在: {clothing_id}")
            lk = CharacterLook(look_id=new_look_id(),
                               character_name=character_name,
                               label=label or "",
                               clothing_id=clothing_id,
                               appearance_description=appearance_description or "",
                               appearance_delta=appearance_delta or "",
                               generation_status=GenerationStatus.NONE.value,
                               approval_status=ApprovalState.UNREVIEWED.value)
            doc.looks[lk.look_id] = lk
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().looks.get(lk.look_id)
            verified = reloaded is not None and look_revision(reloaded) == look_revision(lk)
            return self._receipt(op, ok=True, target=lk.look_id,
                                 before=None, after=lk.to_dict(),
                                 revision=look_revision(lk), verified=verified,
                                 document_revision_after=doc_rev)

    async def update_look(self, look_id: str, patch: Dict[str, Any], *,
                          expected_revision: str) -> dict:
        op = "resource.update_look"
        try:
            look_id = self._require_str(look_id, "look_id")
            unknown = set(patch) - _LOOK_PATCH_FIELDS
            if unknown:
                raise ValueError(
                    f"未知/不可变字段: {sorted(unknown)}"
                    f"（character_name 是身份 FK 不可改；可改: {sorted(_LOOK_PATCH_FIELDS)}）")
            if "clothing_id" in patch and patch["clothing_id"] is not None \
                    and not isinstance(patch["clothing_id"], str):
                raise ValueError("clothing_id 必须是 string 或 null")
        except ValueError as e:
            return self._receipt(op, ok=False, target=look_id,
                                 error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            lk = doc.looks.get(look_id)
            if lk is None:
                return self._receipt(op, ok=False, target=look_id, error="not_found",
                                     detail=f"look 不存在: {look_id}")
            cur = look_revision(lk)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, lk.to_dict())
            if "clothing_id" in patch and patch["clothing_id"] is not None \
                    and patch["clothing_id"] not in doc.clothings:
                return self._receipt(op, ok=False, target=look_id, error="clothing_not_found",
                                     detail=f"Clothing 不存在: {patch['clothing_id']}")
            before = lk.to_dict()
            for f in _LOOK_PATCH_FIELDS:
                if f in patch:
                    setattr(lk, f, patch[f])
            lk.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().looks.get(look_id)
            verified = reloaded is not None and look_revision(reloaded) == look_revision(lk)
            return self._receipt(op, ok=True, target=look_id,
                                 before=before, after=lk.to_dict(),
                                 revision=look_revision(lk), verified=verified,
                                 document_revision_after=doc_rev)
