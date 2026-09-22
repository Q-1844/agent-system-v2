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

import asyncio
import base64
import hashlib
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.store import ProjectStore

from .models import (
    ApprovalState,
    CharacterLook,
    CharacterReferenceComposition,
    Clothing,
    CompositionAnchor,
    CompositionPropBinding,
    CompositionRelation,
    GenerationStatus,
    ReferenceAsset,
    RequiredVisibility,
    document_revision,
    clothing_revision,
    look_revision,
    prop_revision,
    reference_asset_revision,
    composition_revision,
    new_clothing_id,
    new_composition_id,
    new_look_id,
    new_reference_asset_id,
)
from .store import ResourceStore
from .generation import (
    agnes_image_inputs,
    compute_generation_basis,
    compute_composition_basis,
    recompute_basis_still_holds,
    recompute_composition_basis_still_holds,
    save_candidate_files,
    save_composition_candidate_files,
    sha256_bytes,
)
from . import generation_records

# 本轮允许注册的本地图片扩展名（注册的是"已有图片"；其余一律拒绝，不猜内容）
_ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# Clothing 可 PATCH 字段（rename = 改 name；clothing_id 恒不变）
_CLOTHING_PATCH_FIELDS = {"name", "description", "design_traits", "materials", "colors", "tags"}
# Look 可 PATCH 字段（character_name 是 FK 身份，不可改；改外观语义直接改这些字段）
_LOOK_PATCH_FIELDS = {"label", "clothing_id", "appearance_description", "appearance_delta"}
# Composition 可 PATCH 字段（character_name/look_id 是 FK 身份，不可改）
_COMPOSITION_PATCH_FIELDS = {"label", "pose_note", "composition_description", "prop_bindings"}

_COMPOSITION_RELATIONS = {e.value for e in CompositionRelation}
_COMPOSITION_ANCHORS = {e.value for e in CompositionAnchor}
_COMPOSITION_VISIBILITY = {e.value for e in RequiredVisibility}
COMPOSITION_MAX_PROPS = 3   # 初版 1~3 个 props（§6：不开放无限多个 reference）


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

    # ==================================================================
    # CharacterLook candidate generation（§10-§21）—— 三阶段协议
    # ==================================================================
    MAX_CANDIDATE_COUNT = 4

    @staticmethod
    async def _download_default(url: str, proxy=None) -> bytes:
        """下载 provider 生成图（GCS 公开读，不带 Authorization——CLAUDE.md 实测）。"""
        import httpx
        async with httpx.AsyncClient(proxy=proxy, timeout=60) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.content

    @staticmethod
    def _gen_result(generation_id: str, look_id: str, *, ok: bool, status: str,
                    error: Optional[str] = None, detail: Optional[str] = None,
                    basis: Optional[dict] = None, candidate_asset_ids=None,
                    candidate_files=None, candidate_urls=None, verified: bool = False,
                    record: Optional[dict] = None, **extra) -> dict:
        out = {
            "ok": ok, "operation": "look.generate_candidates",
            "generation_id": generation_id, "look_id": look_id,
            "status": status, "error": error, "detail": detail,
            "basis": basis,
            "candidate_asset_ids": list(candidate_asset_ids or []),
            "candidate_files": list(candidate_files or []),
            "candidate_urls": list(candidate_urls or []),
            "verified": verified, "generation_record": record,
        }
        out.update(extra)
        return out

    async def generate_look_candidates(self, look_id: str, *, expected_revision: str,
                                       candidate_count: int = 1, view_purpose: str = "",
                                       seed: Optional[int] = None,
                                       agnes_client=None, downloader=None) -> dict:
        """Look 候选生成（§10）。三阶段协议（§5/§14/§19）：
          阶段1 短锁：entry OCC（expected_revision=look revision）→ 构造 GenerationBasis
                 → 无 identity ref 则 fail closed missing_character_reference
                 → generation_status=generating → 释放锁；
          阶段2 无锁：调 AgnesClient.generate_image（绝不持 project_write_lock 等图片模型）；
          阶段3 短锁：重算 basis 语义指纹，成立才 commit（ReferenceAsset source=generated +
                 追加 reference_asset_ids + status=ready + 重读验证），否则 stale_basis 拒绝。
        失败语义（§21）：provider 失败不伪造 asset；旧 reference_asset_ids 保留；
        record 诚实记录。candidate_count 默认 1、硬上限 4。"""
        op = "look.generate_candidates"
        generation_id = f"gen_{uuid.uuid4().hex[:12]}"
        # ---- 入参校验（锁外）----
        if not isinstance(candidate_count, int) or isinstance(candidate_count, bool) \
                or candidate_count < 1 or candidate_count > self.MAX_CANDIDATE_COUNT:
            return self._gen_result(generation_id, look_id, ok=False, status="failed",
                                    error="invalid_candidate_count",
                                    detail=f"candidate_count 必须是 1~{self.MAX_CANDIDATE_COUNT} 的整数")
        if seed is not None and not isinstance(seed, int):
            return self._gen_result(generation_id, look_id, ok=False, status="failed",
                                    error="validation_error", detail="seed 必须是整数或 null")

        started_at = time.time()
        record_base: Dict[str, Any] = {
            "generation_id": generation_id, "operation": op, "look_id": look_id,
            "basis": None, "prompt": None, "model": None, "seed": seed,
            "candidate_asset_ids": [], "candidate_files": [], "candidate_urls": [],
            "started_at": started_at, "completed_at": None, "status": None,
            "error": None, "verified": False, "resource_document_revision_after": None,
        }

        def _finish(**kw) -> dict:
            rec = dict(record_base)
            rec.update(kw.get("record_extra") or {})
            rec["completed_at"] = time.time()
            for k in ("status", "error", "basis", "candidate_asset_ids",
                      "candidate_files", "candidate_urls", "verified",
                      "resource_document_revision_after"):
                if k in kw:
                    rec[k] = kw[k]
            rec["detail"] = kw.get("detail")   # §21：真实错误信息必须进 record
            generation_records.append_record(self.project_dir, rec)
            kw.setdefault("record", rec)
            return self._gen_result(generation_id, look_id, ok=kw.get("ok", False),
                                    status=rec["status"], error=rec["error"],
                                    detail=kw.get("detail"), basis=rec["basis"],
                                    candidate_asset_ids=rec["candidate_asset_ids"],
                                    candidate_files=rec["candidate_files"],
                                    candidate_urls=rec["candidate_urls"],
                                    verified=rec["verified"], record=rec)

        # ---- 阶段 1：短锁 entry OCC + per-look single-flight + basis snapshot（§5 阶段1）----
        async with self.store.write_lock():
            doc = self.store.load()
            look = doc.looks.get(look_id)
            if look is None:
                return self._gen_result(generation_id, look_id, ok=False, status="failed",
                                        error="not_found", detail=f"look 不存在: {look_id}")
            cur = look_revision(look)
            if expected_revision != cur:
                return self._gen_result(
                    generation_id, look_id, ok=False, status="failed",
                    error="stale_revision", detail="entry OCC：expected_revision 与当前 look revision 不一致",
                    expected_revision=expected_revision, current_revision=cur,
                    current=look.to_dict())
            # per-look single-flight（Phase 1C1.1）：同一 look 任意时刻最多一个 active generation。
            # 检查在同一短锁内 → 与阶段1的 generating 翻转天然串行化，无 TOCTOU 窗口；
            # 粒度只限 same look_id（状态在 look 上），不同 look 仍可并发生成。
            # 拒绝语义：不调模型、不写 record、不改 canonical、不替换已有 generation 状态。
            if look.generation_status == GenerationStatus.GENERATING.value:
                return self._gen_result(
                    generation_id, look_id, ok=False, status="failed",
                    error="generation_in_progress",
                    detail="同一 Look 已有 active generation（per-look single-flight），"
                           "本次请求被拒绝；未调用模型、未写 record、canonical 未改动")
            if agnes_client is None:
                from deps import get_agnes_client   # 惰性 import（避免 app 上下文依赖进入 resources 测试）
                agnes_client = get_agnes_client()
            # P1 §7/§20 fail closed：visual_profile unspecified → 不猜风格，拒绝生成。
            # （仅 profile-aware 生成路径；Agent 路径本阶段不接，AGENT_INTEGRATION_DEFERRED）
            from src.visual_profile import load_profile_or_none
            from src.visual_profile.models import VisualProfileSchemaError
            try:
                visual_profile = load_profile_or_none(self.project_dir)
            except VisualProfileSchemaError as e:
                return _finish(ok=False, status="failed", error="visual_profile_unreadable",
                               detail=f"project_profile.json 损坏（fail closed）：{e}")
            if visual_profile is None:
                return _finish(ok=False, status="failed",
                               error="project_visual_profile_required",
                               detail="项目未设置 ProjectVisualProfile（P1 §7 fail closed，"
                                      "不猜测 realistic/anime/3D）；先设置项目视觉定义")
            model_id = agnes_client.models["image"]
            ratio = "2:3"   # 全身参考竖图（与既有角色三视图同口径，非视频画幅）
            project = ProjectStore(self.project_dir).load()
            # Phase D §2：图片质量档跟随项目生产设置（project.style.size_tier；缺省 1K）
            size_tier = project.style.size_tier or "1K"
            basis = compute_generation_basis(
                self.project_dir, doc, project, look,
                model_id=model_id, size_tier=size_tier, ratio=ratio,
                seed=seed, candidate_count=candidate_count, view_purpose=view_purpose,
                visual_profile=visual_profile)
            record_base["basis"] = basis
            record_base["prompt"] = basis["prompt"]
            record_base["model"] = basis["model"]
            # §12 fail closed：没有任何可读 Character reference → 不退化纯文字生成
            if not basis["character_reference_snapshot"]:
                return _finish(ok=False, status="failed", error="missing_character_reference",
                               detail=f"Character {look.character_name} 没有可用的 identity reference（fail closed，不退化纯文字生成）")
            prev_status = look.generation_status
            look.generation_status = GenerationStatus.GENERATING.value
            self.store.save(doc)

        # ---- 阶段 2：无锁调图片模型（§5 阶段2 / §14）----
        images: List[tuple] = []   # (bytes, url|None)
        seeds_used: List[Optional[int]] = []
        saved_files: List[str] = []

        async def _fail_close(err_code: str, detail_msg: str) -> dict:
            """异常终态收口（§21）：不伪造 asset、旧 refs 保留、status=failed、诚实 record。
            已到手的候选图保留在 generation 诊断目录（不注册、不 attach）。"""
            diag_files = save_candidate_files(self.project_dir, look_id, generation_id,
                                              [d for d, _ in images]) if images else []
            async with self.store.write_lock():
                doc = self.store.load()
                lk = doc.looks.get(look_id)
                if lk is not None:
                    lk.generation_status = GenerationStatus.FAILED.value
                    self.store.save(doc)
            return _finish(ok=False, status="failed", error=err_code, detail=detail_msg,
                           candidate_files=diag_files,
                           candidate_urls=[u for _, u in images if u])

        try:
            refs = agnes_image_inputs(self.project_dir, basis["character_reference_snapshot"]) + \
                agnes_image_inputs(self.project_dir, basis["clothing_reference_snapshot"])
            proxy = getattr(agnes_client, "_proxy", None) or getattr(agnes_client, "proxy", None)
            dl = downloader or self._download_default
            for i in range(candidate_count):
                use_seed = (seed + i) if seed is not None else None   # 多候选：seed+i 保证可复现且不重复
                seeds_used.append(use_seed)
                record_base["seeds_used"] = list(seeds_used)
                res = await agnes_client.generate_image(
                    prompt=basis["prompt"], image_refs=refs,
                    size_tier=size_tier, ratio=ratio, seed=use_seed)
                url = (res or {}).get("url")
                b64 = (res or {}).get("b64")
                if b64:
                    data = base64.b64decode(b64)
                elif url:
                    data = await dl(url, proxy=proxy)
                else:
                    raise RuntimeError("provider 未返回 url/b64（无图可保存，不伪造 asset）")
                images.append((data, url))
        except asyncio.CancelledError:
            # Phase 1C1.2 cancel cleanup：任务取消/进程关闭时尽力写诚实终态，然后
            # **re-raise，永不吞 cancel**（AI-6F 教训：吞 CancelledError 使 worker/shutdown
            # 永挂）。清理是 best-effort——若清理本身再被取消/进程被 kill，startup
            # orphan sweep（src/resources/recovery.py）兜底。
            await _fail_close("cancelled",
                              "生成被取消（任务取消/进程关闭）；已到手候选保留诊断目录，未注册未挂载")
            raise
        except Exception as e:   # §21 provider failure：不伪造、旧 refs 保留、status=failed
            return await _fail_close("provider_failed", f"{type(e).__name__}: {e}")

        # 候选图落盘（唯一 generation_id 目录，无需锁）
        saved_files = save_candidate_files(self.project_dir, look_id, generation_id,
                                           [d for d, _ in images])

        # ---- 阶段 3：短锁重验 basis + commit / stale 拒绝（§19/§20）----
        async with self.store.write_lock():
            doc = self.store.load()
            project = ProjectStore(self.project_dir).load()
            holds, why = recompute_basis_still_holds(self.project_dir, doc, project,
                                                     look_id, basis)
            if not holds:
                # §19：绝不把旧生成结果 attach 到已变化的 Look；图片保留供诊断
                lk = doc.looks.get(look_id)
                if lk is not None:
                    lk.generation_status = prev_status   # look 内容态未变（诚实还原）
                    self.store.save(doc)
                return _finish(ok=False, status="stale_basis",
                               error="stale_generation_basis",
                               detail=f"生成期间 generation basis 已变化（{why}），拒绝 commit；"
                                      f"候选图保留在 generation 目录供诊断，未成为 Look 资产",
                               candidate_files=saved_files,
                               candidate_urls=[u for _, u in images if u],
                               resource_document_revision_after=None)

            # ---- successful commit（§20）----
            candidate_asset_ids: List[str] = []
            for (data, url), rel in zip(images, saved_files):
                asset = ReferenceAsset(asset_id=new_reference_asset_id(),
                                       local=rel, url=url,
                                       checksum=sha256_bytes(data),
                                       view=view_purpose or "",
                                       purpose="look",
                                       background_type="",       # 原始生成图，不臆造背景类型
                                       source="generated")
                doc.reference_assets[asset.asset_id] = asset
                candidate_asset_ids.append(asset.asset_id)
            lk = doc.looks[look_id]
            lk.reference_asset_ids.extend(candidate_asset_ids)
            # 不追加 approved_reference_asset_ids；approval_status 保持 unreviewed（§23）
            lk.generation_status = GenerationStatus.READY.value
            self.store.save(doc)
            doc_rev = self._verify_after_save()

            # §20.7 重读验证：Look / ReferenceAssets / 文件 真实存在
            reloaded = self.store.load()
            lk2 = reloaded.looks.get(look_id)
            assets_ok = all(aid in reloaded.reference_assets for aid in candidate_asset_ids)
            files_ok = all((self.project_dir / f).is_file() for f in saved_files)
            verified = (lk2 is not None
                        and all(aid in lk2.reference_asset_ids for aid in candidate_asset_ids)
                        and lk2.generation_status == GenerationStatus.READY.value
                        and lk2.approval_status == ApprovalState.UNREVIEWED.value
                        and assets_ok and files_ok)
            return _finish(ok=True, status="committed",
                           candidate_asset_ids=candidate_asset_ids,
                           candidate_files=saved_files,
                           candidate_urls=[u for _, u in images if u],
                           verified=verified,
                           resource_document_revision_after=doc_rev)

    # ==================================================================
    # Approval（Phase 1C2）：review pass ≠ approved（双向严格分开）
    # ==================================================================
    # ==================================================================
    # Look ↔ ReferenceAsset 绑定（34.md §5/§9：规格指定的「正式注册校准资产经
    # provenance 合同进入本项目」路径的唯一缺口——register/reference-assets 与
    # review/approve 端点都在，唯独没有把【已注册外部资产】挂进 Look 候选集的
    # 正式入口（generate 才会追加）。镜像 attach_clothing_reference 全部语义。）
    # ==================================================================
    async def rehost_reference_asset(self, asset_id: str, *,
                                     expected_document_revision: str,
                                     url_override: Optional[str] = None,
                                     force: bool = False) -> dict:
        """为 local-only 的 ReferenceAsset 补公网 URL（34.md §5 最小修复）：
        视频接口只接受公网 URL（FACTS §3.3），而场景域资产以 local 登记（图片
        走 data URI 无碍）——视频消费前补公网 URL 并回写。两种来源：
        - url_override：直接提供该资产的原始 provider URL（如 Agnes 生成记录中的
          cos-platform-outputs URL）——必须先下载校验 sha256 == asset.checksum
          （同源证明，绝不把不同图片的 URL 写进资产）；
        - 缺省：经标准 ImageHost（imgbb，100% 保真）托管。
        幂等：已有 url 的资产 unchanged。OCC 同 register。"""
        op = "resource.rehost_reference_asset"
        async with self.store.write_lock():
            doc = self.store.load()
            a = doc.reference_assets.get(asset_id)
            if a is None:
                return self._receipt(op, ok=False, target=asset_id, error="not_found",
                                     detail=f"reference asset 不存在: {asset_id}")
            cur = document_revision(doc)
            if expected_document_revision != cur:
                return self._stale_receipt(op, expected_document_revision, cur)
            if getattr(a, "url", None) and not force:
                return self._receipt(op, ok=True, target=asset_id, after=a.to_dict(),
                                     revision=reference_asset_revision(a),
                                     verified=True, unchanged=True, error=None)
            local = getattr(a, "local", None)
            url: Optional[str] = None
            if url_override:
                if not str(url_override).startswith(("http://", "https://")):
                    return self._receipt(op, ok=False, target=asset_id,
                                         error="invalid_reference_input",
                                         detail="url_override 必须是 http(s) URL")
                import httpx as _httpx
                try:
                    resp = await _httpx.AsyncClient(timeout=60).get(str(url_override))
                    resp.raise_for_status()
                except Exception as e:
                    return self._receipt(op, ok=False, target=asset_id,
                                         error="rehost_failed",
                                         detail=f"url_override 下载校验失败: {str(e)[:200]}")
                digest = hashlib.sha256(resp.content).hexdigest()
                if digest != getattr(a, "checksum", None):
                    return self._receipt(op, ok=False, target=asset_id,
                                         error="checksum_mismatch",
                                         detail=("url_override 内容与资产 checksum 不一致"
                                                 f"（{digest[:12]} != {str(getattr(a, 'checksum', ''))[:12]}）"
                                                 "——拒绝把不同图片的 URL 写进资产"))
                url = str(url_override)
            else:
                if not local:
                    return self._receipt(op, ok=False, target=asset_id,
                                         error="no_local_media",
                                         detail="资产无 local 媒体，无法托管")
                from deps import get_rehost_host
                try:
                    url = await get_rehost_host().upload(
                        str(self.project_dir / local))
                except Exception as e:
                    return self._receipt(op, ok=False, target=asset_id,
                                         error="rehost_failed",
                                         detail=str(e)[:300])
                if not url:
                    return self._receipt(op, ok=False, target=asset_id,
                                         error="rehost_failed",
                                         detail="ImageHost 未返回 URL")
            a.url = url
            a.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().reference_assets.get(asset_id)
            verified = reloaded is not None and bool(reloaded.url)
            return self._receipt(op, ok=True, target=asset_id,
                                 before=None, after=a.to_dict(),
                                 revision=reference_asset_revision(a),
                                 verified=verified,
                                 document_revision_after=doc_rev)
    async def attach_look_reference(self, look_id: str, asset_id: str, *,
                                    expected_revision: str) -> dict:
        op = "resource.attach_look_reference"
        async with self.store.write_lock():
            doc = self.store.load()
            lk = doc.looks.get(look_id)
            if lk is None:
                return self._receipt(op, ok=False, target=look_id, error="not_found",
                                     detail=f"look 不存在: {look_id}")
            cur = look_revision(lk)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, lk.to_dict())
            if asset_id not in doc.reference_assets:
                return self._receipt(op, ok=False, target=look_id, error="asset_not_found",
                                     detail=f"reference asset 不存在: {asset_id}")
            if asset_id in lk.reference_asset_ids:
                # 幂等 attach：已在候选集 → 不改文档，诚实返回 unchanged
                return self._receipt(op, ok=True, target=look_id,
                                     after=lk.to_dict(), revision=cur, verified=True,
                                     unchanged=True, error=None)
            before = lk.to_dict()
            lk.reference_asset_ids.append(asset_id)
            lk.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().looks.get(look_id)
            verified = reloaded is not None and asset_id in reloaded.reference_asset_ids
            return self._receipt(op, ok=True, target=look_id,
                                 before=before, after=lk.to_dict(),
                                 revision=look_revision(lk), verified=verified,
                                 document_revision_after=doc_rev)

    # ==================================================================
    # Look 参考姿态/视角派生（34.md Human Gate 用户方向：同一只狐的多姿态参考库
    # ——按镜起始状态选用，消除"每镜开场都复刻同一张参考"的准首帧锁）。
    # i2i 基底必须是该 Look 的 approved 参考（身份构造性同源）；产物登记为候选
    # （不自动批准——vision 审查与人评门槛原样生效）。
    # ==================================================================
    async def derive_look_reference(self, look_id: str, *, base_asset_id: str,
                                    prompt: str, expected_revision: str,
                                    seed: Optional[int] = None) -> dict:
        op = "resource.derive_look_reference"
        if not str(prompt or "").strip():
            return self._receipt(op, ok=False, target=look_id, error="validation_error",
                                 detail="prompt（姿态/视角描述）必填")
        # ---- 阶段1：短锁验证 + 快照（OCC / base 资格 / 风格）----
        async with self.store.write_lock():
            doc = self.store.load()
            lk = doc.looks.get(look_id)
            if lk is None:
                return self._receipt(op, ok=False, target=look_id, error="not_found",
                                     detail=f"look 不存在: {look_id}")
            cur = look_revision(lk)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, lk.to_dict())
            if base_asset_id not in lk.approved_reference_asset_ids:
                return self._receipt(op, ok=False, target=look_id,
                                     error="base_not_approved",
                                     detail=(f"i2i 基底必须是该 Look 的 approved 参考"
                                             f"（{base_asset_id} 不在 approved 列表）——"
                                             "身份派生只从已批准资产出发"))
            base = doc.reference_assets.get(base_asset_id)
            if base is None:
                return self._receipt(op, ok=False, target=look_id,
                                     error="asset_not_found",
                                     detail=f"reference asset 不存在: {base_asset_id}")
            from src.visual_profile import load_profile_or_none
            try:
                visual_profile = load_profile_or_none(self.project_dir)
            except Exception as e:
                return self._receipt(op, ok=False, target=look_id,
                                     error="visual_profile_unreadable", detail=str(e)[:200])
            if visual_profile is None:
                return self._receipt(op, ok=False, target=look_id,
                                     error="project_visual_profile_required",
                                     detail="项目未设置 ProjectVisualProfile（fail closed）")
            from src.visual_profile.compiler import compile_project_style_instruction
            full_prompt = f"{str(prompt).strip()} {compile_project_style_instruction(visual_profile)}".strip()
            base_media = None
            if getattr(base, "local", None):
                from .generation import to_agnes_image_ref
                base_media = to_agnes_image_ref(self.project_dir, url=None,
                                                local=base.local)
            base_media = base_media or getattr(base, "url", None)
            if not base_media:
                return self._receipt(op, ok=False, target=look_id,
                                     error="base_media_unresolvable",
                                     detail="基底资产无可读媒体（local/url 皆缺）")
            project = ProjectStore(self.project_dir).load()
            size_tier = project.style.size_tier or "1K"
            ratio = "16:9"   # 视频参考与视频画幅同形（横版，避免竖版直立偏置）
            snapshot_doc_rev = document_revision(doc)

        # ---- 阶段2：无锁生成 ----
        from deps import get_agnes_client
        client = get_agnes_client()
        generation_id = f"gld_{uuid.uuid4().hex[:12]}"
        try:
            result = await client.generate_image(
                prompt=full_prompt, image_refs=[base_media],
                size_tier=size_tier, ratio=ratio, seed=seed)
        except Exception as e:
            return self._receipt(op, ok=False, target=look_id,
                                 error="provider_failed", detail=str(e)[:300])
        url = (result or {}).get("url")
        b64 = (result or {}).get("b64")
        if not url and not b64:
            return self._receipt(op, ok=False, target=look_id,
                                 error="provider_failed",
                                 detail="provider 未返回 url/b64（不伪造 asset）")
        import base64 as _b64mod
        import httpx as _httpx
        if b64:
            data = _b64mod.b64decode(b64)
        else:
            async with _httpx.AsyncClient(timeout=120) as hc:
                resp = await hc.get(url)
                resp.raise_for_status()
                data = resp.content

        # ---- 阶段3：短锁提交（重验证 + 注册 + attach + record）----
        async with self.store.write_lock():
            doc2 = self.store.load()
            lk2 = doc2.looks.get(look_id)
            if lk2 is None or look_revision(lk2) != cur:
                return self._receipt(op, ok=False, target=look_id,
                                     error="stale_revision",
                                     detail="生成窗口内 Look 已变化——候选不提交")
            files = save_candidate_files(self.project_dir, look_id, generation_id, [data])
            local_rel = Path(files[0]).as_posix() if files else None
            asset = ReferenceAsset(
                asset_id=new_reference_asset_id(),
                local=local_rel, url=url,
                checksum=sha256_bytes(data),
                view="", purpose="look_reference_derive",
                background_type="", source="look_reference_derive")
            doc2.reference_assets[asset.asset_id] = asset
            lk2.reference_asset_ids.append(asset.asset_id)
            lk2.updated_at = time.time()
            self.store.save(doc2)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load()
            verified = (asset.asset_id in reloaded.reference_assets
                        and asset.asset_id in reloaded.looks[look_id].reference_asset_ids)
            from . import generation_records as _gr
            _gr.append_record(self.project_dir, {
                "generation_id": generation_id, "operation": op, "look_id": look_id,
                "base_asset_id": base_asset_id, "prompt": full_prompt,
                "model": client.models.get("image"), "seed": seed,
                "candidate_asset_ids": [asset.asset_id],
                "candidate_files": files, "candidate_urls": [url] if url else [],
                "started_at": None, "completed_at": time.time(),
                "status": "committed", "error": None, "verified": verified,
                "resource_document_revision_after": doc_rev,
                "detail": None,
            })
            return self._receipt(op, ok=True, target=asset.asset_id,
                                 before=None, after=asset.to_dict(),
                                 revision=reference_asset_revision(asset),
                                 verified=verified,
                                 document_revision_after=doc_rev,
                                 generation_id=generation_id, look_id=look_id)

    async def approve_look_reference(self, look_id: str, asset_id: str, *,
                                     expected_revision: str,
                                     set_cover: Optional[bool] = None) -> dict:
        """显式审批：把某张候选 ReferenceAsset 放入 approved_reference_asset_ids。
        门槛（双向语义的另一半）：该 asset 在本 Look 下必须已有 overall=pass 的
        视觉审查证据（review pass 不自动 approve，但没有 pass 证据也不能 approve）。
        set_cover：None=首个 approved 自动成为封面（展示默认，非审批语义）；
        True=强制设为封面；False=不动封面。"""
        op = "resource.approve_look_reference"
        from .look_review import has_pass_evidence
        async with self.store.write_lock():
            doc = self.store.load()
            lk = doc.looks.get(look_id)
            if lk is None:
                return self._receipt(op, ok=False, target=look_id, error="not_found",
                                     detail=f"look 不存在: {look_id}")
            cur = look_revision(lk)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, lk.to_dict())
            if asset_id not in lk.reference_asset_ids:
                return self._receipt(op, ok=False, target=look_id, error="not_in_candidates",
                                     detail=f"asset 不在该 Look 候选集中: {asset_id}")
            if not has_pass_evidence(self.project_dir, look_id, asset_id):
                return self._receipt(op, ok=False, target=look_id,
                                     error="review_not_passed",
                                     detail=f"该候选缺少 overall=pass 的视觉审查证据，禁止 approve"
                                            f"（先 POST /looks/{look_id}/review）：{asset_id}")
            if asset_id in lk.approved_reference_asset_ids:
                return self._receipt(op, ok=True, target=look_id, after=lk.to_dict(),
                                     revision=cur, verified=True, unchanged=True, error=None)
            before = lk.to_dict()
            lk.approved_reference_asset_ids.append(asset_id)
            if set_cover or (set_cover is None and lk.cover_asset_id is None):
                lk.cover_asset_id = asset_id
            lk.approval_status = ApprovalState.APPROVED.value
            lk.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().looks.get(look_id)
            verified = (reloaded is not None
                        and asset_id in reloaded.approved_reference_asset_ids)
            return self._receipt(op, ok=True, target=look_id,
                                 before=before, after=lk.to_dict(),
                                 revision=look_revision(lk), verified=verified,
                                 document_revision_after=doc_rev)

    async def revoke_look_approval(self, look_id: str, asset_id: str, *,
                                   expected_revision: str) -> dict:
        """撤销审批：从 approved_reference_asset_ids 移除；cover 若指向该 asset 置 None；
        approved 变空则 approval_status 回 unreviewed。不动 reference_asset_ids（候选仍在）。"""
        op = "resource.revoke_look_approval"
        async with self.store.write_lock():
            doc = self.store.load()
            lk = doc.looks.get(look_id)
            if lk is None:
                return self._receipt(op, ok=False, target=look_id, error="not_found",
                                     detail=f"look 不存在: {look_id}")
            cur = look_revision(lk)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, lk.to_dict())
            if asset_id not in lk.approved_reference_asset_ids:
                return self._receipt(op, ok=True, target=look_id, after=lk.to_dict(),
                                     revision=cur, verified=True, unchanged=True, error=None)
            before = lk.to_dict()
            lk.approved_reference_asset_ids.remove(asset_id)
            if lk.cover_asset_id == asset_id:
                lk.cover_asset_id = None
            if not lk.approved_reference_asset_ids:
                lk.approval_status = ApprovalState.UNREVIEWED.value
            lk.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().looks.get(look_id)
            verified = (reloaded is not None
                        and asset_id not in reloaded.approved_reference_asset_ids)
            return self._receipt(op, ok=True, target=look_id,
                                 before=before, after=lk.to_dict(),
                                 revision=look_revision(lk), verified=verified,
                                 document_revision_after=doc_rev)

    # ==================================================================
    # Prop CRUD（Phase 1C4：基本 create/update/delete/reference；不做 Location）
    # ==================================================================
    async def create_prop(self, *, name: str, description: str = "",
                          tags: Optional[List[str]] = None,
                          expected_document_revision: str = "") -> dict:
        op = "resource.create_prop"
        try:
            name = self._require_str(name, "name")
        except ValueError as e:
            return self._receipt(op, ok=False, error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            cur = document_revision(doc)
            if expected_document_revision != cur:
                return self._stale_receipt(op, expected_document_revision, cur)
            from .models import Prop, new_prop_id, prop_revision
            p = Prop(prop_id=new_prop_id(), name=name, description=description or "",
                     tags=list(tags or []),
                     approval_status=ApprovalState.UNREVIEWED.value)
            doc.props[p.prop_id] = p
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().props.get(p.prop_id)
            verified = reloaded is not None and prop_revision(reloaded) == prop_revision(p)
            return self._receipt(op, ok=True, target=p.prop_id,
                                 before=None, after=p.to_dict(),
                                 revision=prop_revision(p), verified=verified,
                                 document_revision_after=doc_rev)

    async def update_prop(self, prop_id: str, patch: Dict[str, Any], *,
                          expected_revision: str) -> dict:
        op = "resource.update_prop"
        _PROP_FIELDS = {"name", "description", "tags"}
        try:
            prop_id = self._require_str(prop_id, "prop_id")
            unknown = set(patch) - _PROP_FIELDS
            if unknown:
                raise ValueError(f"未知/不可变字段: {sorted(unknown)}（可改: {sorted(_PROP_FIELDS)}）")
            if "tags" in patch and patch["tags"] is not None and not isinstance(patch["tags"], list):
                raise ValueError("tags 必须是 list")
        except ValueError as e:
            return self._receipt(op, ok=False, target=prop_id,
                                 error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            p = doc.props.get(prop_id)
            if p is None:
                return self._receipt(op, ok=False, target=prop_id, error="not_found",
                                     detail=f"prop 不存在: {prop_id}")
            from .models import prop_revision
            cur = prop_revision(p)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, p.to_dict())
            before = p.to_dict()
            for f in _PROP_FIELDS:
                if f in patch and patch[f] is not None:
                    setattr(p, f, patch[f])
            p.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().props.get(prop_id)
            verified = reloaded is not None and prop_revision(reloaded) == prop_revision(p)
            return self._receipt(op, ok=True, target=prop_id,
                                 before=before, after=p.to_dict(),
                                 revision=prop_revision(p), verified=verified,
                                 document_revision_after=doc_rev)

    async def delete_prop(self, prop_id: str, *, expected_revision: str) -> dict:
        """删除 Prop。Phase 1C5 扩展：Composition.prop_bindings 引用该 prop 时
        → resource_in_use（used_by_compositions，无级联——与 delete_clothing 同语义）。"""
        op = "resource.delete_prop"
        async with self.store.write_lock():
            doc = self.store.load()
            p = doc.props.get(prop_id)
            if p is None:
                return self._receipt(op, ok=False, target=prop_id, error="not_found",
                                     detail=f"prop 不存在: {prop_id}")
            from .models import prop_revision
            cur = prop_revision(p)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, p.to_dict())
            used_by = [c.composition_id for c in doc.compositions.values()
                       if any(b.prop_id == prop_id for b in c.prop_bindings)]
            if used_by:
                return self._receipt(op, ok=False, target=prop_id,
                                     error="resource_in_use",
                                     detail="prop 仍被 Composition binding 引用，禁止删除（无级联）",
                                     used_by_compositions=used_by)
            before = p.to_dict()
            del doc.props[prop_id]
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            verified = self.store.load().props.get(prop_id) is None
            return self._receipt(op, ok=True, target=prop_id,
                                 before=before, after=None,
                                 revision=None, verified=verified,
                                 document_revision_after=doc_rev)

    async def attach_prop_reference(self, prop_id: str, asset_id: str, *,
                                    expected_revision: str) -> dict:
        op = "resource.attach_prop_reference"
        async with self.store.write_lock():
            doc = self.store.load()
            p = doc.props.get(prop_id)
            if p is None:
                return self._receipt(op, ok=False, target=prop_id, error="not_found",
                                     detail=f"prop 不存在: {prop_id}")
            from .models import prop_revision
            cur = prop_revision(p)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, p.to_dict())
            if asset_id not in doc.reference_assets:
                return self._receipt(op, ok=False, target=prop_id, error="asset_not_found",
                                     detail=f"reference asset 不存在: {asset_id}")
            if asset_id in p.reference_asset_ids:
                return self._receipt(op, ok=True, target=prop_id, after=p.to_dict(),
                                     revision=cur, verified=True, unchanged=True, error=None)
            before = p.to_dict()
            p.reference_asset_ids.append(asset_id)
            p.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().props.get(prop_id)
            verified = reloaded is not None and asset_id in reloaded.reference_asset_ids
            return self._receipt(op, ok=True, target=prop_id,
                                 before=before, after=p.to_dict(),
                                 revision=prop_revision(p), verified=verified,
                                 document_revision_after=doc_rev)

    async def detach_prop_reference(self, prop_id: str, asset_id: str, *,
                                    expected_revision: str) -> dict:
        """解除 Prop ↔ ReferenceAsset 绑定（幂等）。
        Phase 1C5 扩展：同步从 approved_reference_asset_ids 移除（approved ⊆ candidates
        不变式）+ cover 指向该 asset 时清空 + approved 变空回 unreviewed。
        （Composition 生成只消费 approved prop refs，绝不允许出现"已解绑却仍 approved"
        的悬空审批——那是 prop_reference 语义漏洞。）"""
        op = "resource.detach_prop_reference"
        async with self.store.write_lock():
            doc = self.store.load()
            p = doc.props.get(prop_id)
            if p is None:
                return self._receipt(op, ok=False, target=prop_id, error="not_found",
                                     detail=f"prop 不存在: {prop_id}")
            from .models import prop_revision
            cur = prop_revision(p)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, p.to_dict())
            if asset_id not in p.reference_asset_ids:
                return self._receipt(op, ok=True, target=prop_id, after=p.to_dict(),
                                     revision=cur, verified=True, unchanged=True, error=None)
            before = p.to_dict()
            p.reference_asset_ids.remove(asset_id)
            if asset_id in p.approved_reference_asset_ids:
                p.approved_reference_asset_ids.remove(asset_id)
            if p.cover_asset_id == asset_id:
                p.cover_asset_id = None
            if not p.approved_reference_asset_ids:
                p.approval_status = ApprovalState.UNREVIEWED.value
            p.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().props.get(prop_id)
            verified = (reloaded is not None
                        and asset_id not in reloaded.reference_asset_ids
                        and asset_id not in reloaded.approved_reference_asset_ids)
            return self._receipt(op, ok=True, target=prop_id,
                                 before=before, after=p.to_dict(),
                                 revision=prop_revision(p), verified=verified,
                                 document_revision_after=doc_rev)

    # ==================================================================
    # Prop Reference Approval（Phase 1C5 §7：Composition 生成只消费 approved prop refs）
    # ==================================================================
    async def approve_prop_reference(self, prop_id: str, asset_id: str, *,
                                     expected_revision: str,
                                     set_cover: Optional[bool] = None) -> dict:
        """显式审批 Prop 参考图。门槛（与 look approve 同语义）：该 asset 在本 Prop 下
        必须有 overall=pass 的视觉审查证据（review pass 不自动 approve；
        无 pass 证据不能 approve）。set_cover 语义与 look 相同。"""
        op = "resource.approve_prop_reference"
        from .prop_review import has_prop_pass_evidence
        async with self.store.write_lock():
            doc = self.store.load()
            p = doc.props.get(prop_id)
            if p is None:
                return self._receipt(op, ok=False, target=prop_id, error="not_found",
                                     detail=f"prop 不存在: {prop_id}")
            from .models import prop_revision
            cur = prop_revision(p)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, p.to_dict())
            if asset_id not in p.reference_asset_ids:
                return self._receipt(op, ok=False, target=prop_id, error="not_in_candidates",
                                     detail=f"asset 不在该 Prop 的参考集中: {asset_id}")
            if not has_prop_pass_evidence(self.project_dir, prop_id, asset_id):
                return self._receipt(op, ok=False, target=prop_id,
                                     error="review_not_passed",
                                     detail=f"该参考缺少 overall=pass 的视觉审查证据，禁止 approve"
                                            f"（先 POST /props/{prop_id}/review）：{asset_id}")
            if asset_id in p.approved_reference_asset_ids:
                return self._receipt(op, ok=True, target=prop_id, after=p.to_dict(),
                                     revision=cur, verified=True, unchanged=True, error=None)
            before = p.to_dict()
            p.approved_reference_asset_ids.append(asset_id)
            if set_cover or (set_cover is None and p.cover_asset_id is None):
                p.cover_asset_id = asset_id
            p.approval_status = ApprovalState.APPROVED.value
            p.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().props.get(prop_id)
            verified = (reloaded is not None
                        and asset_id in reloaded.approved_reference_asset_ids)
            return self._receipt(op, ok=True, target=prop_id,
                                 before=before, after=p.to_dict(),
                                 revision=prop_revision(p), verified=verified,
                                 document_revision_after=doc_rev)

    async def revoke_prop_approval(self, prop_id: str, asset_id: str, *,
                                   expected_revision: str) -> dict:
        """撤销 Prop 参考审批（候选保留；approved 变空回 unreviewed；cover 指向则清空）。"""
        op = "resource.revoke_prop_approval"
        async with self.store.write_lock():
            doc = self.store.load()
            p = doc.props.get(prop_id)
            if p is None:
                return self._receipt(op, ok=False, target=prop_id, error="not_found",
                                     detail=f"prop 不存在: {prop_id}")
            from .models import prop_revision
            cur = prop_revision(p)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, p.to_dict())
            if asset_id not in p.approved_reference_asset_ids:
                return self._receipt(op, ok=True, target=prop_id, after=p.to_dict(),
                                     revision=cur, verified=True, unchanged=True, error=None)
            before = p.to_dict()
            p.approved_reference_asset_ids.remove(asset_id)
            if p.cover_asset_id == asset_id:
                p.cover_asset_id = None
            if not p.approved_reference_asset_ids:
                p.approval_status = ApprovalState.UNREVIEWED.value
            p.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().props.get(prop_id)
            verified = (reloaded is not None
                        and asset_id not in reloaded.approved_reference_asset_ids)
            return self._receipt(op, ok=True, target=prop_id,
                                 before=before, after=p.to_dict(),
                                 revision=prop_revision(p), verified=verified,
                                 document_revision_after=doc_rev)

    # ==================================================================
    # CharacterReferenceComposition CRUD（Phase 1C5 §6）
    # ==================================================================
    @staticmethod
    def _validate_bindings(doc, raw_bindings) -> tuple:
        """binding 校验（§6）：有限 enum / prop 真实存在 / 不重复同一 prop_id / 数量 1~3。
        返回 (error_receipt|None, bindings|None)。"""
        if not isinstance(raw_bindings, list) or not raw_bindings:
            return ({"ok": False, "error": "invalid_prop_count",
                     "detail": f"prop_bindings 必须是 1~{COMPOSITION_MAX_PROPS} 个的列表"}, None)
        if len(raw_bindings) > COMPOSITION_MAX_PROPS:
            return ({"ok": False, "error": "invalid_prop_count",
                     "detail": f"prop_bindings 最多 {COMPOSITION_MAX_PROPS} 个（初版不开放无限 reference），"
                               f"得到 {len(raw_bindings)}"}, None)
        seen = set()
        out: List[CompositionPropBinding] = []
        for raw in raw_bindings:
            if not isinstance(raw, dict) or not isinstance(raw.get("prop_id"), str) \
                    or not raw.get("prop_id", "").strip():
                return ({"ok": False, "error": "invalid_binding",
                         "detail": "每个 binding 必须是含非空 prop_id 字符串的 object"}, None)
            pid = raw["prop_id"].strip()
            relation = raw.get("relation", CompositionRelation.HELD.value)
            anchor = raw.get("anchor", CompositionAnchor.RIGHT_HAND.value)
            visibility = raw.get("required_visibility", RequiredVisibility.REQUIRED.value)
            if relation not in _COMPOSITION_RELATIONS:
                return ({"ok": False, "error": "invalid_binding",
                         "detail": f"relation 必须是 {sorted(_COMPOSITION_RELATIONS)}，得到: {relation}"}, None)
            if anchor not in _COMPOSITION_ANCHORS:
                return ({"ok": False, "error": "invalid_binding",
                         "detail": f"anchor 必须是 {sorted(_COMPOSITION_ANCHORS)}，得到: {anchor}"}, None)
            if visibility not in _COMPOSITION_VISIBILITY:
                return ({"ok": False, "error": "invalid_binding",
                         "detail": f"required_visibility 必须是 {sorted(_COMPOSITION_VISIBILITY)}，得到: {visibility}"}, None)
            if pid not in doc.props:
                return ({"ok": False, "error": "prop_not_found",
                         "detail": f"Prop 不存在: {pid}"}, None)
            if pid in seen:
                return ({"ok": False, "error": "duplicate_prop_binding",
                         "detail": f"同一 prop_id 不得重复绑定（V1 不支持多实例）: {pid}"}, None)
            seen.add(pid)
            out.append(CompositionPropBinding(
                prop_id=pid, relation=relation, anchor=anchor,
                visual_state_note=str(raw.get("visual_state_note") or ""),
                required_visibility=visibility))
        return (None, out)

    async def create_composition(self, *, character_name: str, look_id: str,
                                 prop_bindings: List[dict],
                                 label: str = "", pose_note: str = "",
                                 composition_description: str = "",
                                 expected_document_revision: str = "") -> dict:
        """创建 CharacterReferenceComposition（§6 全验证链）：
        Character 真实存在 / Look 真实存在且属于该 Character / Look 已 approved 且
        至少 1 张 approved reference / 每个绑定的 prop 真实存在 / bindings 合法
        （有限 enum、无重复 prop_id、1~3 个）。创建后不自动生成、approval_status=unreviewed。"""
        op = "resource.create_composition"
        try:
            character_name = self._require_str(character_name, "character_name").strip()
            look_id = self._require_str(look_id, "look_id").strip()
            if not character_name or not look_id:
                raise ValueError("character_name / look_id 不能为空")
        except ValueError as e:
            return self._receipt(op, ok=False, error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            cur = document_revision(doc)
            if expected_document_revision != cur:
                return self._stale_receipt(op, expected_document_revision, cur)
            try:
                project = ProjectStore(self.project_dir).load()
            except FileNotFoundError:
                project = None
            char = project.get_character(character_name) if project else None
            if char is None:
                return self._receipt(op, ok=False, error="character_not_found",
                                     detail=f"Character 不存在于项目 canonical: {character_name}")
            look = doc.looks.get(look_id)
            if look is None:
                return self._receipt(op, ok=False, error="look_not_found",
                                     detail=f"Look 不存在: {look_id}")
            if look.character_name != character_name:
                return self._receipt(op, ok=False, error="look_character_mismatch",
                                     detail=f"Look {look_id} 属于 Character '{look.character_name}'，"
                                            f"与传入 '{character_name}' 不一致")
            if look.approval_status != ApprovalState.APPROVED.value:
                return self._receipt(op, ok=False, error="look_not_approved",
                                     detail=f"Look {look_id} 未 approved（approval_status="
                                            f"{look.approval_status}），不能进入 Composition")
            if not look.approved_reference_asset_ids:
                return self._receipt(op, ok=False, error="look_missing_approved_reference",
                                     detail=f"Look {look_id} 没有任何 approved reference asset")
            err, bindings = self._validate_bindings(doc, prop_bindings)
            if err is not None:
                return self._receipt(op, **err)
            comp = CharacterReferenceComposition(
                composition_id=new_composition_id(), character_name=character_name,
                look_id=look_id, prop_bindings=bindings,
                label=label or "", pose_note=pose_note or "",
                composition_description=composition_description or "",
                generation_status=GenerationStatus.NONE.value,
                approval_status=ApprovalState.UNREVIEWED.value)
            doc.compositions[comp.composition_id] = comp
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().compositions.get(comp.composition_id)
            verified = (reloaded is not None
                        and composition_revision(reloaded) == composition_revision(comp))
            return self._receipt(op, ok=True, target=comp.composition_id,
                                 before=None, after=comp.to_dict(),
                                 revision=composition_revision(comp), verified=verified,
                                 document_revision_after=doc_rev)

    async def update_composition(self, composition_id: str, patch: Dict[str, Any], *,
                                 expected_revision: str) -> dict:
        """更新 Composition（label/pose_note/composition_description/prop_bindings 全量替换）。
        character_name/look_id 是身份 FK 不可改。bindings 更新走与 create 相同的验证链。"""
        op = "resource.update_composition"
        try:
            composition_id = self._require_str(composition_id, "composition_id")
            unknown = set(patch) - _COMPOSITION_PATCH_FIELDS
            if unknown:
                raise ValueError(
                    f"未知/不可变字段: {sorted(unknown)}"
                    f"（character_name/look_id 是身份 FK 不可改；可改: {sorted(_COMPOSITION_PATCH_FIELDS)}）")
        except ValueError as e:
            return self._receipt(op, ok=False, target=composition_id,
                                 error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            comp = doc.compositions.get(composition_id)
            if comp is None:
                return self._receipt(op, ok=False, target=composition_id, error="not_found",
                                     detail=f"composition 不存在: {composition_id}")
            cur = composition_revision(comp)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, comp.to_dict())
            if "prop_bindings" in patch:
                err, bindings = self._validate_bindings(doc, patch["prop_bindings"])
                if err is not None:
                    return self._receipt(op, **err)
            before = comp.to_dict()
            for f in _COMPOSITION_PATCH_FIELDS:
                if f not in patch:
                    continue
                if f == "prop_bindings":
                    comp.prop_bindings = bindings
                else:
                    setattr(comp, f, patch[f] or "")
            comp.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().compositions.get(composition_id)
            verified = (reloaded is not None
                        and composition_revision(reloaded) == composition_revision(comp))
            return self._receipt(op, ok=True, target=composition_id,
                                 before=before, after=comp.to_dict(),
                                 revision=composition_revision(comp), verified=verified,
                                 document_revision_after=doc_rev)

    # ==================================================================
    # Composition candidate generation（§8-§14）—— 三阶段协议 + per-composition single-flight
    # ==================================================================
    @staticmethod
    def _comp_gen_result(generation_id: str, composition_id: str, *, ok: bool, status: str,
                         error: Optional[str] = None, detail: Optional[str] = None,
                         basis: Optional[dict] = None, candidate_asset_ids=None,
                         candidate_files=None, candidate_urls=None, verified: bool = False,
                         record: Optional[dict] = None, **extra) -> dict:
        out = {
            "ok": ok, "operation": "composition.generate_candidates",
            "generation_id": generation_id, "composition_id": composition_id,
            "status": status, "error": error, "detail": detail,
            "basis": basis,
            "candidate_asset_ids": list(candidate_asset_ids or []),
            "candidate_files": list(candidate_files or []),
            "candidate_urls": list(candidate_urls or []),
            "verified": verified, "generation_record": record,
        }
        out.update(extra)
        return out

    async def generate_composition_candidates(self, composition_id: str, *,
                                              expected_revision: str,
                                              candidate_count: int = 1,
                                              seed: Optional[int] = None,
                                              agnes_client=None, downloader=None) -> dict:
        """Composition 候选生成（三阶段协议，镜像 look 生成但 basis/输入选择/落盘路径独立）：
          阶段1 短锁：entry OCC（expected_revision=composition revision）→ per-composition
                 single-flight → 生成前全量重验证（look 仍 approved / 每 binding 的 prop
                 仍有 approved reference）→ 构造 Composition GenerationBasis →
                 无可用参考图 fail closed → generation_status=generating → 释放锁；
          阶段2 无锁：调 AgnesClient.generate_image（绝不持 project_write_lock）；
          阶段3 短锁：重算 basis 语义指纹，成立才 commit，否则 stale_basis 拒绝 attach。
        失败语义与 look 生成同族：provider 失败不伪造 asset；record 诚实记录；
        cancel cleanup 后 re-raise（不吞取消）。"""
        op = "composition.generate_candidates"
        generation_id = f"gen_{uuid.uuid4().hex[:12]}"
        # ---- 入参校验（锁外）----
        if not isinstance(candidate_count, int) or isinstance(candidate_count, bool) \
                or candidate_count < 1 or candidate_count > self.MAX_CANDIDATE_COUNT:
            return self._comp_gen_result(generation_id, composition_id, ok=False, status="failed",
                                         error="invalid_candidate_count",
                                         detail=f"candidate_count 必须是 1~{self.MAX_CANDIDATE_COUNT} 的整数")
        if seed is not None and not isinstance(seed, int):
            return self._comp_gen_result(generation_id, composition_id, ok=False, status="failed",
                                         error="validation_error", detail="seed 必须是整数或 null")

        started_at = time.time()
        record_base: Dict[str, Any] = {
            "generation_id": generation_id, "operation": op, "composition_id": composition_id,
            "basis": None, "prompt": None, "model": None, "seed": seed,
            "candidate_asset_ids": [], "candidate_files": [], "candidate_urls": [],
            "started_at": started_at, "completed_at": None, "status": None,
            "error": None, "verified": False, "resource_document_revision_after": None,
        }

        def _finish(**kw) -> dict:
            rec = dict(record_base)
            rec.update(kw.get("record_extra") or {})
            rec["completed_at"] = time.time()
            for k in ("status", "error", "basis", "candidate_asset_ids",
                      "candidate_files", "candidate_urls", "verified",
                      "resource_document_revision_after"):
                if k in kw:
                    rec[k] = kw[k]
            rec["detail"] = kw.get("detail")
            generation_records.append_record(self.project_dir, rec)
            kw.setdefault("record", rec)
            return self._comp_gen_result(generation_id, composition_id,
                                         ok=kw.get("ok", False), status=rec["status"],
                                         error=rec["error"], detail=kw.get("detail"),
                                         basis=rec["basis"],
                                         candidate_asset_ids=rec["candidate_asset_ids"],
                                         candidate_files=rec["candidate_files"],
                                         candidate_urls=rec["candidate_urls"],
                                         verified=rec["verified"], record=rec)

        # ---- 阶段 1：短锁 entry OCC + single-flight + 重验证 + basis snapshot ----
        async with self.store.write_lock():
            doc = self.store.load()
            comp = doc.compositions.get(composition_id)
            if comp is None:
                return self._comp_gen_result(generation_id, composition_id, ok=False,
                                             status="failed", error="not_found",
                                             detail=f"composition 不存在: {composition_id}")
            cur = composition_revision(comp)
            if expected_revision != cur:
                return self._comp_gen_result(
                    generation_id, composition_id, ok=False, status="failed",
                    error="stale_revision",
                    detail="entry OCC：expected_revision 与当前 composition revision 不一致",
                    expected_revision=expected_revision, current_revision=cur,
                    current=comp.to_dict())
            # per-composition single-flight（与 look 同原则）：检查与 generating 翻转在同一
            # 短锁内串行化，无 TOCTOU；粒度只限 same composition_id，不同 composition 仍并发。
            if comp.generation_status == GenerationStatus.GENERATING.value:
                return self._comp_gen_result(
                    generation_id, composition_id, ok=False, status="failed",
                    error="generation_in_progress",
                    detail="同一 Composition 已有 active generation（single-flight），"
                           "本次请求被拒绝；未调用模型、未写 record、canonical 未改动")
            # 生成前全量重验证（§6/§7：canonical 可能在 create 之后被改）
            look = doc.looks.get(comp.look_id)
            if look is None:
                return self._comp_gen_result(generation_id, composition_id, ok=False,
                                             status="failed", error="look_not_found",
                                             detail=f"Look 不存在（生成前重验证失败）: {comp.look_id}")
            if look.approval_status != ApprovalState.APPROVED.value:
                return self._comp_gen_result(generation_id, composition_id, ok=False,
                                             status="failed", error="look_not_approved",
                                             detail=f"Look {comp.look_id} 已不再是 approved"
                                                    f"（生成前重验证失败）")
            if not look.approved_reference_asset_ids:
                return self._comp_gen_result(generation_id, composition_id, ok=False,
                                             status="failed", error="look_missing_approved_reference",
                                             detail=f"Look {comp.look_id} 没有 approved reference")
            for b in comp.prop_bindings:
                prop = doc.props.get(b.prop_id)
                if prop is None:
                    return self._comp_gen_result(generation_id, composition_id, ok=False,
                                                 status="failed", error="prop_not_found",
                                                 detail=f"Prop 不存在（生成前重验证失败）: {b.prop_id}")
                if not prop.approved_reference_asset_ids:
                    return self._comp_gen_result(
                        generation_id, composition_id, ok=False, status="failed",
                        error="prop_reference_not_approved",
                        detail=f"Prop '{prop.name}'（{b.prop_id}）没有 approved reference；"
                               f"Composition 生成只使用 approved prop references，不偷偷用未审核图片")
            if agnes_client is None:
                from deps import get_agnes_client
                agnes_client = get_agnes_client()
            # P1 §7/§20 fail closed：visual_profile unspecified → 不猜风格，拒绝生成。
            from src.visual_profile import load_profile_or_none
            from src.visual_profile.models import VisualProfileSchemaError
            try:
                visual_profile = load_profile_or_none(self.project_dir)
            except VisualProfileSchemaError as e:
                return _finish(ok=False, status="failed", error="visual_profile_unreadable",
                               detail=f"project_profile.json 损坏（fail closed）：{e}")
            if visual_profile is None:
                return _finish(ok=False, status="failed",
                               error="project_visual_profile_required",
                               detail="项目未设置 ProjectVisualProfile（P1 §7 fail closed，"
                                      "不猜测 realistic/anime/3D）；先设置项目视觉定义")
            model_id = agnes_client.models["image"]
            ratio = "2:3"
            project = ProjectStore(self.project_dir).load()
            # Phase D §2：图片质量档跟随项目生产设置（project.style.size_tier；缺省 1K）
            size_tier = project.style.size_tier or "1K"
            basis = compute_composition_basis(
                self.project_dir, doc, project, comp,
                model_id=model_id, size_tier=size_tier, ratio=ratio,
                seed=seed, candidate_count=candidate_count,
                visual_profile=visual_profile)
            basis["started_at"] = started_at   # 指纹之后追加（不参与重算比对）
            record_base["basis"] = basis
            record_base["prompt"] = basis["prompt"]
            record_base["model"] = basis["model"]
            # fail closed：look hero 或任一 binding 的 prop hero 实际不可读 → 不退化纯文字生成
            usable = [e for e in basis["selected_input_refs"]
                      if (e.get("local") and (self.project_dir / e["local"]).is_file())
                      or e.get("url")]
            has_look = any(e.get("role") in ("look_hero", "look_extra_angle") for e in usable)
            missing_props = [b["prop_id"] for b in basis["prop_bindings"]
                             if not any(e.get("role") == "prop_hero" and e.get("prop_id") == b["prop_id"]
                                        for e in usable)]
            if not has_look:
                return _finish(ok=False, status="failed", error="missing_look_reference",
                               detail="Look approved reference 均不可读（fail closed，不退化纯文字生成）")
            if missing_props:
                return _finish(ok=False, status="failed", error="missing_prop_reference",
                               detail=f"以下 prop 的 approved reference 均不可读: {missing_props}"
                                      f"（fail closed，不退化纯文字生成）")
            prev_status = comp.generation_status
            comp.generation_status = GenerationStatus.GENERATING.value
            self.store.save(doc)

        # ---- 阶段 2：无锁调图片模型 ----
        images: List[tuple] = []
        seeds_used: List[Optional[int]] = []

        async def _fail_close(err_code: str, detail_msg: str) -> dict:
            """异常终态收口（与 look _fail_close 同族）：不伪造 asset、诚实终态、已到手候选留诊断。"""
            diag_files = save_composition_candidate_files(
                self.project_dir, composition_id, generation_id,
                [d for d, _ in images]) if images else []
            async with self.store.write_lock():
                doc2 = self.store.load()
                c2 = doc2.compositions.get(composition_id)
                if c2 is not None:
                    c2.generation_status = GenerationStatus.FAILED.value
                    self.store.save(doc2)
            return _finish(ok=False, status="failed", error=err_code, detail=detail_msg,
                           candidate_files=diag_files,
                           candidate_urls=[u for _, u in images if u])

        try:
            refs = agnes_image_inputs(self.project_dir, basis["selected_input_refs"])
            proxy = getattr(agnes_client, "_proxy", None) or getattr(agnes_client, "proxy", None)
            dl = downloader or self._download_default
            for i in range(candidate_count):
                use_seed = (seed + i) if seed is not None else None
                seeds_used.append(use_seed)
                record_base["seeds_used"] = list(seeds_used)
                res = await agnes_client.generate_image(
                    prompt=basis["prompt"], image_refs=refs,
                    size_tier=size_tier, ratio=ratio, seed=use_seed)
                url = (res or {}).get("url")
                b64 = (res or {}).get("b64")
                if b64:
                    data = base64.b64decode(b64)
                elif url:
                    data = await dl(url, proxy=proxy)
                else:
                    raise RuntimeError("provider 未返回 url/b64（无图可保存，不伪造 asset）")
                images.append((data, url))
        except asyncio.CancelledError:
            # cancel cleanup（1C1.2 同原则）：尽力写诚实终态后 re-raise，绝不吞取消；
            # 若清理本身再被取消/进程死亡，startup orphan sweep 兜底。
            await _fail_close("cancelled",
                              "生成被取消（任务取消/进程关闭）；已到手候选保留诊断目录，未注册未挂载")
            raise
        except Exception as e:
            return await _fail_close("provider_failed", f"{type(e).__name__}: {e}")

        saved_files = save_composition_candidate_files(self.project_dir, composition_id,
                                                       generation_id, [d for d, _ in images])

        # ---- 阶段 3：短锁重验 basis + commit / stale 拒绝 ----
        async with self.store.write_lock():
            doc = self.store.load()
            project = ProjectStore(self.project_dir).load()
            holds, why = recompute_composition_basis_still_holds(self.project_dir, doc, project,
                                                                 composition_id, basis)
            if not holds:
                # §8：绝不把旧生成结果 attach 到已变化的 composition；图片保留供诊断
                c2 = doc.compositions.get(composition_id)
                if c2 is not None:
                    c2.generation_status = prev_status   # 内容态未变（诚实还原）
                    self.store.save(doc)
                return _finish(ok=False, status="stale_basis",
                               error="stale_generation_basis",
                               detail=f"生成期间 composition generation basis 已变化（{why}），拒绝 commit；"
                                      f"候选图保留在 generation 目录供诊断，未成为资产",
                               candidate_files=saved_files,
                               candidate_urls=[u for _, u in images if u],
                               resource_document_revision_after=None)

            candidate_asset_ids: List[str] = []
            for (data, url), rel in zip(images, saved_files):
                asset = ReferenceAsset(asset_id=new_reference_asset_id(),
                                       local=rel, url=url,
                                       checksum=sha256_bytes(data),
                                       view="composition",
                                       purpose="composition",
                                       background_type="",
                                       source="generated")
                doc.reference_assets[asset.asset_id] = asset
                candidate_asset_ids.append(asset.asset_id)
            c2 = doc.compositions[composition_id]
            c2.reference_asset_ids.extend(candidate_asset_ids)
            # 不追加 approved；approval_status 保持（§16：review pass ≠ 自动 approve）
            c2.generation_status = GenerationStatus.READY.value
            self.store.save(doc)
            doc_rev = self._verify_after_save()

            reloaded = self.store.load()
            c3 = reloaded.compositions.get(composition_id)
            assets_ok = all(aid in reloaded.reference_assets for aid in candidate_asset_ids)
            files_ok = all((self.project_dir / f).is_file() for f in saved_files)
            verified = (c3 is not None
                        and all(aid in c3.reference_asset_ids for aid in candidate_asset_ids)
                        and c3.generation_status == GenerationStatus.READY.value
                        and assets_ok and files_ok)
            return _finish(ok=True, status="committed",
                           candidate_asset_ids=candidate_asset_ids,
                           candidate_files=saved_files,
                           candidate_urls=[u for _, u in images if u],
                           verified=verified,
                           resource_document_revision_after=doc_rev)

    # ==================================================================
    # Composition Approval（Phase 1C5 §16：review pass ≠ 自动 approved，双向分开）
    # ==================================================================
    async def approve_composition_candidate(self, composition_id: str, asset_id: str, *,
                                            expected_revision: str,
                                            set_cover: Optional[bool] = None) -> dict:
        """显式审批 Composition 候选。门槛（§16）：对应当前候选存在最新有效的
        overall=pass 六维度审查证据（无 pass 证据不能 approve；pass 不自动 approve）。"""
        op = "resource.approve_composition_candidate"
        from .composition_review import has_composition_pass_evidence
        async with self.store.write_lock():
            doc = self.store.load()
            comp = doc.compositions.get(composition_id)
            if comp is None:
                return self._receipt(op, ok=False, target=composition_id, error="not_found",
                                     detail=f"composition 不存在: {composition_id}")
            cur = composition_revision(comp)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, comp.to_dict())
            if asset_id not in comp.reference_asset_ids:
                return self._receipt(op, ok=False, target=composition_id,
                                     error="not_in_candidates",
                                     detail=f"asset 不在该 Composition 候选集中: {asset_id}")
            if not has_composition_pass_evidence(self.project_dir, composition_id, asset_id):
                return self._receipt(op, ok=False, target=composition_id,
                                     error="review_not_passed",
                                     detail=f"该候选缺少 overall=pass 的六维度审查证据，禁止 approve"
                                            f"（先 POST /compositions/{composition_id}/review）：{asset_id}")
            if asset_id in comp.approved_reference_asset_ids:
                return self._receipt(op, ok=True, target=composition_id, after=comp.to_dict(),
                                     revision=cur, verified=True, unchanged=True, error=None)
            before = comp.to_dict()
            comp.approved_reference_asset_ids.append(asset_id)
            if set_cover or (set_cover is None and comp.cover_asset_id is None):
                comp.cover_asset_id = asset_id
            comp.approval_status = ApprovalState.APPROVED.value
            comp.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().compositions.get(composition_id)
            verified = (reloaded is not None
                        and asset_id in reloaded.approved_reference_asset_ids)
            return self._receipt(op, ok=True, target=composition_id,
                                 before=before, after=comp.to_dict(),
                                 revision=composition_revision(comp), verified=verified,
                                 document_revision_after=doc_rev)

    async def revoke_composition_approval(self, composition_id: str, asset_id: str, *,
                                          expected_revision: str) -> dict:
        """撤销 Composition 审批（候选保留；approved 变空回 unreviewed；cover 指向则清空）。"""
        op = "resource.revoke_composition_approval"
        async with self.store.write_lock():
            doc = self.store.load()
            comp = doc.compositions.get(composition_id)
            if comp is None:
                return self._receipt(op, ok=False, target=composition_id, error="not_found",
                                     detail=f"composition 不存在: {composition_id}")
            cur = composition_revision(comp)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, comp.to_dict())
            if asset_id not in comp.approved_reference_asset_ids:
                return self._receipt(op, ok=True, target=composition_id, after=comp.to_dict(),
                                     revision=cur, verified=True, unchanged=True, error=None)
            before = comp.to_dict()
            comp.approved_reference_asset_ids.remove(asset_id)
            if comp.cover_asset_id == asset_id:
                comp.cover_asset_id = None
            if not comp.approved_reference_asset_ids:
                comp.approval_status = ApprovalState.UNREVIEWED.value
            comp.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().compositions.get(composition_id)
            verified = (reloaded is not None
                        and asset_id not in reloaded.approved_reference_asset_ids)
            return self._receipt(op, ok=True, target=composition_id,
                                 before=before, after=comp.to_dict(),
                                 revision=composition_revision(comp), verified=verified,
                                 document_revision_after=doc_rev)
