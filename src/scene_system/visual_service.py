# src/scene_system/visual_service.py
# Scene System V1 — Phase S2A：LocationView 视觉协议 service
# （generate §13-§17 / review §21-§24 / approve-revoke §19/§25）。
#
# 🔴 三阶段协议（§13，完全沿用 Resources 已验证模式）：
#   阶段1 短锁：view OCC（expected_revision=view revision）→ per-view single-flight
#          → 参考确定性选择 → GenerationBasis 快照 → generation_status=generating；
#   阶段2 无锁：AgnesClient.generate_image（绝不持 project_write_lock 等 provider）；
#   阶段3 短锁：对当前 canonical 重算 basis 语义指纹，成立才 commit
#          （generation_status=ready + active_generation_id），否则 stale 拒绝。
#
# 🔴 per-view single-flight（§14）：粒度 same view_id；同 view 并发第二请求
#    generation_in_progress（不调 provider、不写假 record、不覆盖状态）；
#    不同 view 可并发——即使同一 location，也不全局串行。
#
# 🔴 候选不进 Resources（§17）：未批准 candidate 只是 Scene 侧文件 + generation
#    record；只有 Review PASS + 显式 Approval 才经冻结 Resources service 注册
#    ReferenceAsset（cross-canonical saga 见 approve 方法）。
#
# 🔴 环境图绝不 rembg（§16）：保存原始生成图。
# 🔴 CancelledError：cleanup + 诚实终态 record + re-raise，绝不吞取消（§15）。
from __future__ import annotations

import asyncio
import base64
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.resources.generation import agnes_image_inputs, to_agnes_image_ref

from . import generation_records
from .generation_records import generation_records_path
from .models import (
    GenerationStatus,
    SceneInstanceView,
    document_revision,
    view_revision,
)
from .store import SceneSystemStore
from .visual_basis import (
    compute_location_visual_basis, load_resources_doc_or_none,
    recompute_location_visual_basis_still_holds, select_reference_views,
)


def _type_name(v: Any) -> str:
    return type(v).__name__


class SceneVisualService:
    """LocationView 视觉协议 service（generate/review/approve/revoke）。
    期望失败返回结构化 receipt（ok=False + error 码）；只有意外内部错误上抛。"""

    MAX_CANDIDATE_COUNT = 4   # 与 Resources 同口径（红线 12：默认 2 张点选）

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.project_dir = Path(project_dir)
        self.project_name = project_name or self.project_dir.name
        self.store = SceneSystemStore(self.project_dir, self.project_name)

    # ============ 保存后重读验证（truth gate）============
    def _verify_after_save(self) -> str:
        reloaded = self.store.load()
        from .models import document_revision
        return document_revision(reloaded)

    @staticmethod
    def _require_str(value: Any, field: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} 必须是 string，得到 {_type_name(value)}")
        return value

    @staticmethod
    def _stale_receipt(operation: str, expected: str, current: str,
                       current_dict: Any = None) -> dict:
        return {"ok": False, "operation": operation, "error": "stale_revision",
                "expected_revision": expected, "current_revision": current,
                "current": current_dict}

    async def _download_default(self, url: str, proxy=None) -> bytes:
        """下载 provider 生成图（GCS 公开读，不带 Authorization——CLAUDE.md 实测）。"""
        import httpx
        async with httpx.AsyncClient(proxy=proxy, timeout=60) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.content

    # ============ 候选落盘（§16：stable id 路径；rename 不搬目录；不 rembg）============
    def _save_candidate_files(self, location_id: str, view_id: str, generation_id: str,
                              images: List[bytes]) -> List[str]:
        root = (self.project_dir / "output" / "scene_system" / "locations" / location_id
                / "views" / view_id / "generations" / generation_id)
        root.mkdir(parents=True, exist_ok=True)
        out: List[str] = []
        for i, data in enumerate(images, start=1):
            name = f"candidate_{i:02d}.png"
            (root / name).write_bytes(data)   # 原始生成图，绝不 rembg（§16）
            out.append(f"output/scene_system/locations/{location_id}/views/{view_id}"
                       f"/generations/{generation_id}/{name}")
        return out

    # ==================================================================
    # 生成（§13-§17/§20）
    # ==================================================================
    async def generate_location_view(self, view_id: str, *, expected_revision: str,
                                     candidate_count: int = 2, seed: Optional[int] = None,
                                     reference_view_ids: Optional[List[str]] = None,
                                     size_tier: str = "1K", ratio: str = "16:9",
                                     agnes_client=None, downloader=None) -> dict:
        """LocationView 候选生成（三阶段）。Base Location identity——不含 SceneState。"""
        op = "scene.generate_location_view"
        generation_id = f"gen_{uuid.uuid4().hex[:12]}"
        # ---- 入参校验（锁外）----
        if not isinstance(candidate_count, int) or isinstance(candidate_count, bool) \
                or candidate_count < 1 or candidate_count > self.MAX_CANDIDATE_COUNT:
            return self._gen_result(generation_id, view_id, ok=False, status="failed",
                                    error="invalid_candidate_count",
                                    detail=f"candidate_count 必须是 1~{self.MAX_CANDIDATE_COUNT} 的整数")
        if seed is not None and not isinstance(seed, int):
            return self._gen_result(generation_id, view_id, ok=False, status="failed",
                                    error="validation_error", detail="seed 必须是整数或 null")

        started_at = time.time()
        record_base: Dict[str, Any] = {
            "generation_id": generation_id, "operation": op,
            "view_id": view_id, "location_id": None,
            "basis": None, "selected_reference_ids": [], "prompt": None, "model": None,
            "seed": seed, "seeds_used": [],
            "candidate_files": [], "candidate_urls": [],
            "started_at": started_at, "completed_at": None, "status": None,
            "error": None, "verified": False, "revision_after": None,
        }

        def _finish(**kw) -> dict:
            rec = dict(record_base)
            rec.update(kw.get("record_extra") or {})
            rec["completed_at"] = time.time()
            for k in ("status", "error", "basis", "selected_reference_ids",
                      "candidate_files", "candidate_urls", "verified", "revision_after"):
                if k in kw:
                    rec[k] = kw[k]
            rec["detail"] = kw.get("detail")   # 真实错误信息必须进 record
            generation_records.append_record(self.project_dir, rec,
                                             path=generation_records_path(self.project_dir))
            kw.setdefault("record", rec)
            return self._gen_result(generation_id, view_id, ok=kw.get("ok", False),
                                    status=rec["status"], error=rec["error"],
                                    detail=kw.get("detail"), basis=rec["basis"],
                                    candidate_files=rec["candidate_files"],
                                    candidate_urls=rec["candidate_urls"],
                                    verified=rec["verified"], record=rec,
                                    **{k: v for k, v in kw.items()
                                       if k in ("expected_revision", "current_revision",
                                                "revision_after")})

        # ---- 阶段 1：短锁 entry OCC + per-view single-flight + basis snapshot ----
        async with self.store.write_lock():
            doc = self.store.load()
            view = doc.views.get(view_id)
            if view is None:
                return self._gen_result(generation_id, view_id, ok=False, status="failed",
                                        error="view_not_found", detail=f"LocationView 不存在: {view_id}")
            location = doc.locations.get(view.location_id)
            if location is None:
                return self._gen_result(generation_id, view_id, ok=False, status="failed",
                                        error="location_not_found",
                                        detail=f"Location 不存在: {view.location_id}")
            # per-view single-flight（§14）：同一 view 任意时刻最多一个 active generation。
            # 🔒 检查先于 entry OCC：飞行态是状态检查而非内容检查——并发第二请求无论携带
            # 什么 revision 都必须得到 generation_in_progress（§14 明确语义），而不是误导性
            # 的 stale_revision；检查在同一短锁内 → 与 generating 翻转串行化，无 TOCTOU。
            # 粒度只限 same view_id（状态在 view 上），不同 view 可并发。
            if view.generation_status == GenerationStatus.GENERATING.value:
                return self._gen_result(
                    generation_id, view_id, ok=False, status="failed",
                    error="generation_in_progress",
                    detail="同一 LocationView 已有 active generation（per-view single-flight），"
                           "本次请求被拒绝；未调用模型、未写 record、canonical 未改动")
            cur = view_revision(view)
            if expected_revision != cur:
                return self._gen_result(
                    generation_id, view_id, ok=False, status="failed",
                    error="stale_revision",
                    detail="entry OCC：expected_revision 与当前 view revision 不一致",
                    expected_revision=expected_revision, current_revision=cur,
                    current=view.to_dict())
            if agnes_client is None:
                from deps import get_agnes_client   # 惰性 import（测试可注入 fake）
                agnes_client = get_agnes_client()
            # P1 §7/§20 fail closed：visual_profile unspecified → 不猜风格，拒绝生成。
            from src.visual_profile import load_profile_or_none
            from src.visual_profile.models import VisualProfileSchemaError
            try:
                visual_profile = load_profile_or_none(self.project_dir)
            except VisualProfileSchemaError as e:
                return self._gen_result(generation_id, view_id, ok=False, status="failed",
                                        error="visual_profile_unreadable",
                                        detail=f"project_profile.json 损坏（fail closed）：{e}")
            if visual_profile is None:
                return self._gen_result(generation_id, view_id, ok=False, status="failed",
                                        error="project_visual_profile_required",
                                        detail="项目未设置 ProjectVisualProfile（P1 §7 fail closed，"
                                               "不猜测 realistic/anime/3D）；先设置项目视觉定义")
            model_id = agnes_client.models["image"]
            rdoc = load_resources_doc_or_none(self.project_dir)
            selection = select_reference_views(self.project_dir, doc, rdoc, view,
                                               explicit_reference_view_ids=reference_view_ids)
            if selection.get("error"):
                return self._gen_result(generation_id, view_id, ok=False, status="failed",
                                        error=selection["error"],
                                        detail=selection.get("detail") or selection.get("notes"))
            basis = compute_location_visual_basis(
                self.project_dir, doc, rdoc, location, view,
                model_id=model_id, size_tier=size_tier, ratio=ratio,
                seed=seed, candidate_count=candidate_count, reference_selection=selection,
                visual_profile=visual_profile)
            record_base["location_id"] = location.location_id
            record_base["basis"] = basis
            record_base["selected_reference_ids"] = list(basis["reference_view_ids"])
            record_base["prompt"] = basis["prompt"]
            record_base["model"] = basis["model"]
            prev_status = view.generation_status
            view.generation_status = GenerationStatus.GENERATING.value
            self.store.save(doc)   # 首次写会把 v1 文件升级 v2（惰性迁移契约）

        # ---- 阶段 2：无锁调图片模型（§13 阶段2）----
        images: List[tuple] = []   # (bytes, url|None)
        seeds_used: List[Optional[int]] = []

        async def _fail_close(err_code: str, detail_msg: str) -> dict:
            """异常终态收口：不伪造候选、status=failed、诚实 record。
            已到手候选图保留在 generation 诊断目录（未批准不进 Resources，§17）。"""
            diag_files = self._save_candidate_files(
                location.location_id, view_id, generation_id,
                [d for d, _ in images]) if images else []
            async with self.store.write_lock():
                doc2 = self.store.load()
                v2 = doc2.views.get(view_id)
                if v2 is not None:
                    v2.generation_status = GenerationStatus.FAILED.value
                    self.store.save(doc2)
            return _finish(ok=False, status="failed", error=err_code, detail=detail_msg,
                           candidate_files=diag_files,
                           candidate_urls=[u for _, u in images if u])

        try:
            refs = agnes_image_inputs(self.project_dir, basis["selected_references"])
            proxy = getattr(agnes_client, "_proxy", None) or getattr(agnes_client, "proxy", None)
            dl = downloader or self._download_default
            for i in range(candidate_count):
                use_seed = (seed + i) if seed is not None else None
                seeds_used.append(use_seed)
                record_base["seeds_used"] = list(seeds_used)
                res = await agnes_client.generate_image(
                    prompt=basis["prompt"], image_refs=refs or None,
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
            # §15 cancel cleanup：尽力写诚实终态，然后 re-raise，永不吞取消。
            # 若清理本身再被取消/进程被 kill，startup orphan sweep（recovery.py）兜底。
            await _fail_close("cancelled",
                              "生成被取消（任务取消/进程关闭）；已到手候选保留诊断目录，未批准未注册")
            raise
        except Exception as e:
            return await _fail_close("provider_failed", f"{type(e).__name__}: {e}")

        # 候选图落盘（唯一 generation_id 目录，无需锁；原始图不 rembg）
        saved_files = self._save_candidate_files(location.location_id, view_id,
                                                 generation_id, [d for d, _ in images])

        # ---- 阶段 3：短锁重验 basis + commit / stale 拒绝（§13 阶段3）----
        async with self.store.write_lock():
            doc = self.store.load()
            view3 = doc.views.get(view_id)
            rdoc3 = load_resources_doc_or_none(self.project_dir)
            holds, why = recompute_location_visual_basis_still_holds(
                self.project_dir, doc, rdoc3, view3, basis) if view3 is not None else (False, "view_missing")
            if not holds:
                # §9：绝不把旧生成结果 attach 到已变化的空间语义；图片保留供诊断
                if view3 is not None:
                    view3.generation_status = prev_status   # view 内容态诚实还原
                    self.store.save(doc)
                return _finish(ok=False, status="stale_basis",
                               error="stale_generation_basis",
                               detail=f"生成期间 location visual basis 已变化（{why}），拒绝 commit；"
                                      f"候选图保留在 generation 目录供诊断，未成为批准参考",
                               candidate_files=saved_files,
                               candidate_urls=[u for _, u in images if u],
                               revision_after=None)
            # ---- successful commit（§17：不注册 Resources；Scene 侧指针）----
            v3 = doc.views[view_id]
            v3.generation_status = GenerationStatus.READY.value
            v3.active_generation_id = generation_id
            # 重新生成使旧批准失效（批准针对旧候选；历史在 approval_records 保留）。
            # generation 绝不自动 approve（T26），但旧候选被替换后旧批准不再描述当前候选。
            _regen_reset = v3.approval_status == "approved"
            if _regen_reset:
                v3.approval_status = "unreviewed"
            self.store.save(doc)
            revision_after = view_revision(v3)
            doc_rev = self._verify_after_save()

            # 重读验证：view 状态 / 文件 真实存在
            reloaded = self.store.load()
            v4 = reloaded.views.get(view_id)
            files_ok = all((self.project_dir / f).is_file() for f in saved_files)
            verified = bool(v4 is not None
                            and v4.generation_status == GenerationStatus.READY.value
                            and v4.active_generation_id == generation_id
                            and files_ok)
            return _finish(ok=True, status="committed",
                           candidate_files=saved_files,
                           candidate_urls=[u for _, u in images if u],
                           verified=verified, revision_after=revision_after,
                           record_extra={"scene_document_revision_after": doc_rev})

    @staticmethod
    def _gen_result(generation_id: str, view_id: str, *, ok: bool, status: str,
                    error: Optional[str] = None, detail: Optional[str] = None,
                    basis: Optional[dict] = None, candidate_files=None,
                    candidate_urls=None, verified: bool = False,
                    record: Optional[dict] = None, **extra) -> dict:
        out = {
            "ok": ok, "operation": "scene.generate_location_view",
            "generation_id": generation_id, "view_id": view_id,
            "status": status, "error": error, "detail": detail,
            "basis": basis,
            "candidate_files": list(candidate_files or []),
            "candidate_urls": list(candidate_urls or []),
            "verified": verified, "record": record,
        }
        out.update(extra)
        return out

    # ============ Review（§21-§24；实现在 view_review.py，此处委托保持 router→service）============
    async def review_location_view_candidates(self, view_id: str, *,
                                              agnes_client=None) -> dict:
        from .view_review import review_location_view_candidates as _impl
        return await _impl(self.project_dir, view_id, agnes_client=agnes_client)

    # ==================================================================
    # S2B：SceneInstanceView 生成（§12/§20-§23）
    # ==================================================================
    async def generate_scene_instance_view(self, scene_instance_view_id: str, *,
                                           expected_revision: str,
                                           candidate_count: int = 2,
                                           seed: Optional[int] = None,
                                           include_peer: bool = True,
                                           size_tier: str = "1K", ratio: str = "16:9",
                                           agnes_client=None, downloader=None) -> dict:
        """SceneInstanceView 候选生成（三阶段，完整复用 S2A 已证明协议 §20）。
        前置 fail closed（§12/§2/§9）：base approved+参考可读+审批证据当前
        （check_base_location_view）+ 可见 prop 完整（缺→prop_missing；resources
        不可读→resources_lookup_failed——§2.D 不假装 Prop 输入完整）。"""
        op = "scene_instance_view.generate"
        generation_id = f"gen_{uuid.uuid4().hex[:12]}"
        if not isinstance(candidate_count, int) or isinstance(candidate_count, bool) \
                or candidate_count < 1 or candidate_count > self.MAX_CANDIDATE_COUNT:
            return self._gen_result(generation_id, scene_instance_view_id, ok=False,
                                    status="failed", error="invalid_candidate_count",
                                    detail=f"candidate_count 必须是 1~{self.MAX_CANDIDATE_COUNT} 的整数")
        if seed is not None and not isinstance(seed, int):
            return self._gen_result(generation_id, scene_instance_view_id, ok=False,
                                    status="failed", error="validation_error",
                                    detail="seed 必须是整数或 null")

        started_at = time.time()
        record_base: Dict[str, Any] = {
            "generation_id": generation_id, "operation": op,
            "scene_instance_view_id": scene_instance_view_id,
            "scene_instance_id": None, "location_id": None, "view_id": None,
            "basis": None, "selected_reference_ids": [], "prop_refs_selected": [],
            "prop_refs_omitted_due_to_budget": [], "prompt": None, "model": None,
            "seed": seed, "seeds_used": [], "candidate_files": [], "candidate_urls": [],
            "started_at": started_at, "completed_at": None, "status": None,
            "error": None, "verified": False, "revision_after": None,
        }

        def _finish(**kw) -> dict:
            rec = dict(record_base)
            rec.update(kw.get("record_extra") or {})
            rec["completed_at"] = time.time()
            for k in ("status", "error", "basis", "selected_reference_ids",
                      "prop_refs_selected", "prop_refs_omitted_due_to_budget",
                      "candidate_files", "candidate_urls", "verified", "revision_after"):
                if k in kw:
                    rec[k] = kw[k]
            rec["detail"] = kw.get("detail")
            generation_records.append_record(self.project_dir, rec,
                                             path=generation_records_path(self.project_dir))
            kw.setdefault("record", rec)
            return self._gen_result(generation_id, scene_instance_view_id,
                                    ok=kw.get("ok", False), status=rec["status"],
                                    error=rec["error"], detail=kw.get("detail"),
                                    basis=rec["basis"],
                                    candidate_files=rec["candidate_files"],
                                    candidate_urls=rec["candidate_urls"],
                                    verified=rec["verified"], record=rec,
                                    **{k: v for k, v in kw.items()
                                       if k in ("expected_revision", "current_revision",
                                                "revision_after")})

        # ---- 阶段 1：短锁（single-flight 先于 OCC——S2A 同决策）----
        async with self.store.write_lock():
            doc = self.store.load()
            sciv = doc.scene_instance_views.get(scene_instance_view_id)
            if sciv is None:
                return self._gen_result(generation_id, scene_instance_view_id, ok=False,
                                        status="failed", error="instance_view_not_found",
                                        detail=f"SceneInstanceView 不存在: {scene_instance_view_id}")
            if sciv.generation_status == GenerationStatus.GENERATING.value:
                return self._gen_result(
                    generation_id, scene_instance_view_id, ok=False, status="failed",
                    error="generation_in_progress",
                    detail="同一 SceneInstanceView 已有 active generation（per-view single-flight，"
                           "§21）；未调用模型、未写 record、canonical 未改动")
            cur = view_revision(sciv)
            if expected_revision != cur:
                return self._gen_result(
                    generation_id, scene_instance_view_id, ok=False, status="failed",
                    error="stale_revision",
                    detail="entry OCC：expected_revision 与当前 scene_instance_view revision 不一致",
                    expected_revision=expected_revision, current_revision=cur,
                    current=sciv.to_dict())
            inst = doc.scene_instances.get(sciv.scene_instance_id)
            if inst is None:
                return self._gen_result(generation_id, scene_instance_view_id, ok=False,
                                        status="failed", error="scene_instance_not_found",
                                        detail=f"SceneInstance 不存在: {sciv.scene_instance_id}")
            base_location = doc.locations.get(inst.location_id)
            if base_location is None:
                return self._gen_result(generation_id, scene_instance_view_id, ok=False,
                                        status="failed", error="location_not_found",
                                        detail=f"Location 不存在: {inst.location_id}")
            base_view = doc.views.get(sciv.location_view_id)
            if base_view is None:
                return self._gen_result(generation_id, scene_instance_view_id, ok=False,
                                        status="failed", error="location_view_not_found",
                                        detail=f"LocationView 不存在: {sciv.location_view_id}")
            # §12/§2：base 母版门槛（approved+参考可读+审批证据当前）
            rdoc = load_resources_doc_or_none(self.project_dir)
            gate = self.check_base_location_view(doc, rdoc, base_view)
            if not gate.get("ok"):
                return self._gen_result(generation_id, scene_instance_view_id, ok=False,
                                        status="failed", error=gate["error"],
                                        detail=gate.get("detail"))
            if agnes_client is None:
                from deps import get_agnes_client
                agnes_client = get_agnes_client()
            # P1 §7/§20 fail closed：visual_profile unspecified → 不猜风格，拒绝生成。
            from src.visual_profile import load_profile_or_none
            from src.visual_profile.models import VisualProfileSchemaError
            try:
                visual_profile = load_profile_or_none(self.project_dir)
            except VisualProfileSchemaError as e:
                return self._gen_result(generation_id, scene_instance_view_id, ok=False,
                                        status="failed", error="visual_profile_unreadable",
                                        detail=f"project_profile.json 损坏（fail closed）：{e}")
            if visual_profile is None:
                return self._gen_result(generation_id, scene_instance_view_id, ok=False,
                                        status="failed", error="project_visual_profile_required",
                                        detail="项目未设置 ProjectVisualProfile（P1 §7 fail closed，"
                                               "不猜测 realistic/anime/3D）；先设置项目视觉定义")
            model_id = agnes_client.models["image"]
            # §15/§9：参考选择（内含可见 prop fail closed）
            from .instance_view_basis import (
                _BasisInputError, compute_scene_instance_visual_basis,
                select_state_view_visual_refs)
            try:
                ref_selection = select_state_view_visual_refs(
                    self.project_dir, doc, rdoc, base_view, sciv, inst,
                    include_peer=include_peer)
            except _BasisInputError as e:
                return self._gen_result(generation_id, scene_instance_view_id, ok=False,
                                        status="failed", error=e.code, detail=e.detail)
            basis = compute_scene_instance_visual_basis(
                self.project_dir, doc, rdoc, inst, base_location, base_view, sciv,
                model_id=model_id, size_tier=size_tier, ratio=ratio, seed=seed,
                candidate_count=candidate_count, ref_selection=ref_selection,
                include_peer=include_peer, visual_profile=visual_profile)
            record_base["scene_instance_id"] = inst.scene_instance_id
            record_base["location_id"] = base_location.location_id
            record_base["view_id"] = base_view.view_id
            record_base["basis"] = basis
            record_base["selected_reference_ids"] = [e.get("asset_id")
                                                     for e in ref_selection["selected"]]
            record_base["prop_refs_selected"] = list(ref_selection.get("prop_refs_selected", []))
            record_base["prop_refs_omitted_due_to_budget"] = list(
                ref_selection.get("prop_refs_omitted_due_to_budget", []))
            record_base["prompt"] = basis["prompt"]
            record_base["model"] = basis["model"]
            prev_status = sciv.generation_status
            sciv.generation_status = GenerationStatus.GENERATING.value
            self.store.save(doc)

        # ---- 阶段 2：无锁 provider（§20：绝不持 project lock 等模型）----
        images: List[tuple] = []
        seeds_used: List[Optional[int]] = []

        async def _fail_close(err_code: str, detail_msg: str) -> dict:
            diag_files = self._save_instance_view_candidate_files(
                inst.scene_instance_id, scene_instance_view_id, generation_id,
                [d for d, _ in images]) if images else []
            async with self.store.write_lock():
                doc2 = self.store.load()
                v2 = doc2.scene_instance_views.get(scene_instance_view_id)
                if v2 is not None:
                    v2.generation_status = GenerationStatus.FAILED.value
                    self.store.save(doc2)
            return _finish(ok=False, status="failed", error=err_code, detail=detail_msg,
                           candidate_files=diag_files,
                           candidate_urls=[u for _, u in images if u])

        try:
            refs = agnes_image_inputs(self.project_dir, ref_selection["selected"])
            proxy = getattr(agnes_client, "_proxy", None) or getattr(agnes_client, "proxy", None)
            dl = downloader or self._download_default
            for i in range(candidate_count):
                use_seed = (seed + i) if seed is not None else None
                seeds_used.append(use_seed)
                record_base["seeds_used"] = list(seeds_used)
                res = await agnes_client.generate_image(
                    prompt=basis["prompt"], image_refs=refs or None,
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
            await _fail_close("cancelled",
                              "生成被取消（任务取消/进程关闭）；已到手候选保留诊断目录")
            raise
        except Exception as e:
            return await _fail_close("provider_failed", f"{type(e).__name__}: {e}")

        saved_files = self._save_instance_view_candidate_files(
            inst.scene_instance_id, scene_instance_view_id, generation_id,
            [d for d, _ in images])

        # ---- 阶段 3：短锁重验 basis + commit / stale 拒绝（§14）----
        async with self.store.write_lock():
            doc = self.store.load()
            sciv3 = doc.scene_instance_views.get(scene_instance_view_id)
            inst3 = doc.scene_instances.get(record_base["scene_instance_id"])
            loc3 = doc.locations.get(record_base["location_id"])
            bv3 = doc.views.get(record_base["view_id"])
            rdoc3 = load_resources_doc_or_none(self.project_dir)
            from .instance_view_basis import (
                recompute_scene_instance_visual_basis_still_holds)
            holds, why = (recompute_scene_instance_visual_basis_still_holds(
                self.project_dir, doc, rdoc3, inst3, loc3, bv3, sciv3, basis,
                include_peer=include_peer)
                if all(x is not None for x in (sciv3, inst3, loc3, bv3))
                else (False, "instance_view_missing"))
            if not holds:
                if sciv3 is not None:
                    sciv3.generation_status = prev_status   # 诚实还原
                    self.store.save(doc)
                return _finish(ok=False, status="stale_basis",
                               error="stale_generation_basis",
                               detail=f"生成期间 scene instance visual basis 已变化（{why}），"
                                      f"拒绝 commit；候选图保留在 generation 目录供诊断",
                               candidate_files=saved_files,
                               candidate_urls=[u for _, u in images if u],
                               revision_after=None)
            v3 = doc.scene_instance_views[scene_instance_view_id]
            v3.generation_status = GenerationStatus.READY.value
            v3.active_generation_id = generation_id
            # 重新生成使旧批准失效（§37 lifecycle：regen 后可重新 review+approve；
            # 批准针对旧候选，历史在 approval_records 保留）。generation 绝不自动 approve（T31）。
            if v3.approval_status == "approved":
                v3.approval_status = "unreviewed"
            self.store.save(doc)
            revision_after = view_revision(v3)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load()
            v4 = reloaded.scene_instance_views.get(scene_instance_view_id)
            files_ok = all((self.project_dir / f).is_file() for f in saved_files)
            verified = bool(v4 is not None
                            and v4.generation_status == GenerationStatus.READY.value
                            and v4.active_generation_id == generation_id
                            and files_ok)
            return _finish(ok=True, status="committed",
                           candidate_files=saved_files,
                           candidate_urls=[u for _, u in images if u],
                           verified=verified, revision_after=revision_after,
                           record_extra={"scene_document_revision_after": doc_rev})

    def _save_instance_view_candidate_files(self, scene_instance_id: str,
                                            scene_instance_view_id: str,
                                            generation_id: str,
                                            images: List[bytes]) -> List[str]:
        """§23：stable id 路径；原图不 rembg。"""
        root = (self.project_dir / "output" / "scene_system" / "scene_instances"
                / scene_instance_id / "views" / scene_instance_view_id
                / "generations" / generation_id)
        root.mkdir(parents=True, exist_ok=True)
        out: List[str] = []
        for i, data in enumerate(images, start=1):
            name = f"candidate_{i:02d}.png"
            (root / name).write_bytes(data)
            out.append(f"output/scene_system/scene_instances/{scene_instance_id}/views/"
                       f"{scene_instance_view_id}/generations/{generation_id}/{name}")
        return out

    # ============ approval 事件流（§19/§25）============
    def check_base_location_view(self, doc, rdoc, base_view) -> Dict[str, Any]:
        """S2B §2/§12：LocationView 作为 SceneInstanceView 生成母版的门槛（fail closed）。
        - approval_status == approved 且 reference_asset_id 非空且 ReferenceAsset 可读
          （revoked 即使 reference_asset_id 保留为历史也不可用——§2.A）；
        - base 的审批证据仍当前：最新 approved approval record 的期望指纹 == 当前重算值
          （approved 之后 view/location/anchor/layout/参考语义变了 → location_view_stale，
          §2.B——批准的是某个具体语义状态，语义漂移后旧批准不再背书）。"""
        from .models import ApprovalState
        from .view_review import build_review_expectations, expectations_fingerprint
        from .generation_records import approval_records_path

        if base_view is None:
            return {"ok": False, "error": "location_view_not_found"}
        if base_view.approval_status != ApprovalState.APPROVED.value:
            return {"ok": False, "error": "location_view_not_approved",
                    "detail": f"base LocationView approval_status={base_view.approval_status}"
                              f"（只有 approved 可作 SceneInstanceView 母版；revoked 亦不可）"}
        if not base_view.reference_asset_id:
            return {"ok": False, "error": "location_view_not_approved",
                    "detail": "base LocationView 无 reference_asset_id（无批准视觉母版）"}
        if rdoc is None:
            return {"ok": False, "error": "resources_lookup_failed",
                    "detail": "resources.json 不可读（base ReferenceAsset 可用性无法判定）"}
        asset = rdoc.reference_assets.get(base_view.reference_asset_id)
        if asset is None or to_agnes_image_ref(self.project_dir, url=asset.url,
                                               local=asset.local) is None:
            return {"ok": False, "error": "base_reference_missing",
                    "detail": f"base ReferenceAsset 不可读: {base_view.reference_asset_id}"}
        latest_appr = None
        latest_event = None   # 该 view 的最新审批事件（approve 或 revoke 都算——revoke 取消证据）
        for rec in generation_records.read_records(
                self.project_dir, path=approval_records_path(self.project_dir)):
            if rec.get("view_id") != base_view.view_id:
                continue
            op = rec.get("operation")
            if op in ("scene.approve_location_view", "scene.revoke_view_approval"):
                latest_event = rec
                if op == "scene.approve_location_view" and rec.get("status") == "approved":
                    latest_appr = rec   # 记最后一个 approved（被更新的 revoke 覆盖时作废）
        if latest_event is not None and latest_event.get("operation") == "scene.revoke_view_approval":
            return {"ok": False, "error": "location_view_not_approved",
                    "detail": ("base LocationView 最新审批事件是 revoke（批准证据已被取消）；"
                               "重新 review+approve 后才能作 SceneInstanceView 母版")}
        if latest_appr is None or not latest_appr.get("reviewed_basis_fingerprint"):
            return {"ok": False, "error": "location_view_not_approved",
                    "detail": "base LocationView 无 approved approval record（审批证据缺失）"}
        expectations = build_review_expectations(doc, base_view)
        # 按批准时审查实际使用的参考组重算。🔴 用 `is not None` 判空——首张 view 审查时
        # 参考组是空列表 []（falsy！`or` fallback 会把它替换成当前 ref 造成永远 mismatch，
        # E2E 两次实证后才抓到）。
        appr_ref_ids = latest_appr.get("reference_asset_ids_used")
        # P1 §15 同口径：review/approve 记录的期望指纹含 visual_profile_fingerprint——
        # 本门槛重算必须传同一维度（Golden Path 实证：漏传会让有 Profile 项目的已批准
        # base view 永远误判 location_view_stale）。与 approve 侧 stale_approval_basis
        # 校验（本文件 approve_location_view）逐字同模式；profile 变化 → 重新 review+approve。
        from src.visual_profile import load_profile_or_none
        from src.visual_profile.models import (visual_profile_fingerprint,
                                               VisualProfileSchemaError)
        try:
            _vp = load_profile_or_none(self.project_dir)
        except VisualProfileSchemaError as e:
            return {"ok": False, "error": "visual_profile_unreadable",
                    "detail": f"project_profile.json 损坏（fail closed）：{e}"}
        fp = expectations_fingerprint(
            expectations, list(appr_ref_ids) if appr_ref_ids is not None
            else [base_view.reference_asset_id],
            visual_profile_fingerprint=(visual_profile_fingerprint(_vp)
                                        if _vp is not None else None))
        if fp != latest_appr["reviewed_basis_fingerprint"]:
            return {"ok": False, "error": "location_view_stale",
                    "detail": ("base LocationView 批准后语义已变化（期望指纹不一致，"
                               "含项目风格维度）；重新 review+approve 后才能再作 "
                               "SceneInstanceView 母版")}
        return {"ok": True}

    async def create_scene_instance_view(self, *, scene_instance_id: str,
                                         location_view_id: str, label: str = "",
                                         expected_document_revision: str = "") -> dict:
        """创建 SceneInstanceView（身份 = (scene_instance_id, location_view_id) 对，§4）。
        §2.C：instance.location_id 必须与 LocationView.location_id 相同。"""
        op = "scene.create_scene_instance_view"
        try:
            scene_instance_id = self._require_str(scene_instance_id, "scene_instance_id")
            location_view_id = self._require_str(location_view_id, "location_view_id")
        except ValueError as e:
            return {"ok": False, "operation": op, "error": "validation_error", "detail": str(e)}
        async with self.store.write_lock():
            doc = self.store.load()
            inst = doc.scene_instances.get(scene_instance_id)
            if inst is None:
                return {"ok": False, "operation": op, "error": "scene_instance_not_found",
                        "detail": f"SceneInstance 不存在: {scene_instance_id}"}
            base_view = doc.views.get(location_view_id)
            if base_view is None:
                return {"ok": False, "operation": op, "error": "location_view_not_found",
                        "detail": f"LocationView 不存在: {location_view_id}"}
            if inst.location_id != base_view.location_id:
                return {"ok": False, "operation": op, "error": "location_mismatch",
                        "detail": (f"SceneInstance.location_id={inst.location_id} 与 "
                                   f"LocationView.location_id={base_view.location_id} 不一致（§2.C）")}
            existing = next((v for v in doc.scene_instance_views.values()
                             if v.scene_instance_id == scene_instance_id
                             and v.location_view_id == location_view_id), None)
            if existing is not None:
                return {"ok": False, "operation": op, "error": "scene_instance_view_already_exists",
                        "detail": ("该 (scene_instance, location_view) 对已有 SceneInstanceView"
                                   "（§4 稳定身份：重新生成请复用现有对象）"),
                        "scene_instance_view_id": existing.scene_instance_view_id}
            cur = document_revision(doc)
            if expected_document_revision != cur:
                return self._stale_receipt(op, expected_document_revision, cur)
            from .models import new_scene_instance_view_id, scene_instance_view_revision
            v = SceneInstanceView(scene_instance_view_id=new_scene_instance_view_id(),
                                  scene_instance_id=scene_instance_id,
                                  location_view_id=location_view_id,
                                  label=label or "")
            doc.scene_instance_views[v.scene_instance_view_id] = v
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().scene_instance_views.get(v.scene_instance_view_id)
            verified = (reloaded is not None and
                        scene_instance_view_revision(reloaded) == scene_instance_view_revision(v))
            return {"ok": True, "operation": op, "target": v.scene_instance_view_id,
                    "after": v.to_dict(), "revision": scene_instance_view_revision(v),
                    "verified": verified, "document_revision_after": doc_rev}

    def list_scene_instance_views(self, scene_instance_id: Optional[str] = None,
                                  location_view_id: Optional[str] = None) -> List[dict]:
        doc = self.store.load()
        out = []
        for v in doc.scene_instance_views.values():
            if scene_instance_id is not None and v.scene_instance_id != scene_instance_id:
                continue
            if location_view_id is not None and v.location_view_id != location_view_id:
                continue
            out.append(self._instance_view_read(doc, v))
        return out

    @staticmethod
    def _instance_view_read(doc, v: "SceneInstanceView") -> dict:
        from .models import scene_instance_view_revision
        d = v.to_dict()
        d["revision"] = scene_instance_view_revision(v)
        return d

    def read_scene_instance_view(self, scene_instance_view_id: str) -> Optional[dict]:
        doc = self.store.load()
        v = doc.scene_instance_views.get(scene_instance_view_id)
        if v is None:
            return None
        return self._instance_view_read(doc, v)

    def read_scene_instance_view_status(self, scene_instance_view_id: str) -> Optional[dict]:
        """S2B §32/§34：read 模型——base location view 摘要 + 当前 ViewRelevantSceneState
        投影 + 审批/审查摘要 + derived stale（is_stale/stale_reasons；绝不自动 mutation）。
        stale 锚点 = approved_basis_fingerprint vs 当前重算 generation basis 指纹。"""
        doc = self.store.load()
        v = doc.scene_instance_views.get(scene_instance_view_id)
        if v is None:
            return None
        d = self._instance_view_read(doc, v)
        base_view = doc.views.get(v.location_view_id)
        d["base_location_view"] = base_view.to_dict() if base_view else None
        inst = doc.scene_instances.get(v.scene_instance_id)
        # 当前投影（derived，只读展示）
        projection = None
        if inst is not None and base_view is not None:
            from .state_projection import project_scene_state_for_view
            projection = project_scene_state_for_view(
                inst, base_view,
                [a for a in doc.anchors.values() if a.location_id == inst.location_id])
        d["view_relevant_scene_state"] = projection
        # 审查摘要（最新 pass 证据是否存在）
        from .instance_view_review import latest_pass_review_for_instance_view
        ev = (latest_pass_review_for_instance_view(
            self.project_dir, scene_instance_view_id=v.scene_instance_view_id,
            generation_id=v.active_generation_id, candidate_index=0)
            if v.active_generation_id else None)
        d["has_pass_review_evidence"] = ev is not None
        d["latest_review_fingerprint"] = (ev["record"].get("reviewed_basis_fingerprint")
                                          if ev else None)
        # derived stale（§32：approved 才有意义；绝不自动 mutation）
        if v.approval_status == "approved" and v.approved_basis_fingerprint \
                and inst is not None and base_view is not None:
            from .instance_view_basis import compute_state_view_staleness
            from .visual_basis import load_resources_doc_or_none
            gen_recs = [r for r in generation_records.read_records(
                self.project_dir, generation_id=v.active_generation_id)
                if r.get("operation") == "scene_instance_view.generate"]
            gen_basis = (gen_recs[-1].get("basis") or {}) if gen_recs else {}
            rdoc = load_resources_doc_or_none(self.project_dir)
            st = compute_state_view_staleness(
                self.project_dir, doc, rdoc, inst, doc.locations.get(inst.location_id),
                base_view, v, gen_basis)
            d["is_stale"] = st["is_stale"]
            d["stale_reasons"] = st["stale_reasons"]
        else:
            d["is_stale"] = False
            d["stale_reasons"] = []
        return d

    # ==================================================================
    # S2B：SceneInstanceView 显式 Approval（§30/§31 cross-canonical saga）
    # ==================================================================
    async def approve_scene_instance_view(self, scene_instance_view_id: str, *,
                                          candidate_index: int = 0,
                                          expected_revision: str,
                                          expected_resources_document_revision: str) -> dict:
        """显式审批（review pass ≠ 自动 approve）。Saga 三阶段同 S2A（§31）：
        A 短锁验证（candidate/PASS 证据/期望指纹当前/文件在盘/OCC + scene basis 未 stale）
        → B 无 scene 锁经冻结 Resources 注册 ReferenceAsset（purpose=scene_instance_view）
        → C 重验 attach；B→C 间变化 → orphan 不 attach 不偷删。"""
        op = "scene.approve_scene_instance_view"
        from .instance_view_review import (
            build_scene_instance_review_expectations,
            expectations_fingerprint as siv_expectations_fingerprint,
            latest_pass_review_for_instance_view)
        from .instance_view_basis import (
            recompute_scene_instance_visual_basis_still_holds)

        # ---- 阶段 A：scene 短锁验证 ----
        async with self.store.write_lock():
            doc = self.store.load()
            sciv = doc.scene_instance_views.get(scene_instance_view_id)
            if sciv is None:
                return {"ok": False, "operation": op, "error": "instance_view_not_found",
                        "detail": f"SceneInstanceView 不存在: {scene_instance_view_id}"}
            if sciv.approval_status == "approved":
                return {"ok": False, "operation": op, "error": "already_approved",
                        "detail": "该 SceneInstanceView 已 approved（先 revoke 再重新审批）",
                        "reference_asset_id": sciv.reference_asset_id}
            generation_id = sciv.active_generation_id
            if not generation_id or sciv.generation_status != GenerationStatus.READY.value:
                return {"ok": False, "operation": op, "error": "no_candidates",
                        "detail": "没有 committed 候选（先 generate；review pass 不自动 approve）"}
            gen_recs = [r for r in generation_records.read_records(
                self.project_dir, generation_id=generation_id)
                if r.get("operation") == "scene_instance_view.generate"
                and r.get("status") == "committed"]
            if not gen_recs:
                return {"ok": False, "operation": op, "error": "generation_record_missing",
                        "detail": f"committed generation record 不存在: {generation_id}"}
            gen_rec = gen_recs[-1]
            candidate_files = list(gen_rec.get("candidate_files") or [])
            if not isinstance(candidate_index, int) or isinstance(candidate_index, bool) \
                    or not (0 <= candidate_index < len(candidate_files)):
                return {"ok": False, "operation": op, "error": "invalid_candidate_index",
                        "detail": f"candidate_index 必须是 0~{len(candidate_files) - 1}"}
            candidate_file = candidate_files[candidate_index]
            ev = latest_pass_review_for_instance_view(
                self.project_dir, scene_instance_view_id=scene_instance_view_id,
                generation_id=generation_id, candidate_index=candidate_index)
            if ev is None:
                return {"ok": False, "operation": op, "error": "review_pass_required",
                        "detail": (f"candidate {candidate_index} 没有针对当前 generation 的 "
                                   f"overall=pass 审查证据（approve 双向硬门槛）")}
            # 审查基准仍当前（scene state / view / location / 参考组）
            inst = doc.scene_instances.get(sciv.scene_instance_id)
            base_view = doc.views.get(sciv.location_view_id)
            rdoc = load_resources_doc_or_none(self.project_dir)
            if inst is None or base_view is None:
                return {"ok": False, "operation": op, "error": "canonical_changed",
                        "detail": "SceneInstance 或 base LocationView 已不存在"}
            expectations = build_scene_instance_review_expectations(doc, base_view, inst, rdoc)
            # P1 §15：approve 与 review 同口径（期望指纹含项目风格；profile 变更 → 重新 review）
            from src.visual_profile import load_profile_or_none
            from src.visual_profile.models import (visual_profile_fingerprint,
                                                   VisualProfileSchemaError)
            try:
                _vp = load_profile_or_none(self.project_dir)
            except VisualProfileSchemaError as e:
                return {"ok": False, "operation": op, "error": "visual_profile_unreadable",
                        "detail": f"project_profile.json 损坏（fail closed）：{e}"}
            _vp_fp = visual_profile_fingerprint(_vp) if _vp is not None else None
            fp = siv_expectations_fingerprint(
                expectations, list(ev["record"].get("reference_asset_ids_used") or []),
                visual_profile_fingerprint=_vp_fp)
            if fp != ev["record"].get("reviewed_basis_fingerprint"):
                return {"ok": False, "operation": op, "error": "stale_approval_basis",
                        "detail": "审查之后 scene basis/参考组/项目风格已变化（期望指纹不一致）；重新 review"}
            if not (self.project_dir / candidate_file).is_file():
                return {"ok": False, "operation": op, "error": "candidate_file_missing",
                        "detail": f"候选文件丢失: {candidate_file}"}
            # scene basis 未 stale（§30/§14；S2B.1-③ no-peer 口径：peer 是可选生成增强，
            # 不属于批准语义——peer 变化不阻断 approve，也不追溯 stale）。组件级对比。
            from .instance_view_basis import _BasisInputError, compute_no_peer_basis
            loc = doc.locations.get(inst.location_id)
            gb = gen_rec.get("basis") or {}
            try:
                cur_np = compute_no_peer_basis(
                    self.project_dir, doc, rdoc, inst, loc, base_view, sciv,
                    model_id=(gb.get("model") or {}).get("model_id", ""),
                    size_tier=(gb.get("model") or {}).get("size_tier", "1K"),
                    ratio=(gb.get("model") or {}).get("ratio", "16:9"),
                    seed=gb.get("seed"),
                    candidate_count=gb.get("candidate_count", 2),
                    visual_profile=_vp)
            except _BasisInputError as e:
                return {"ok": False, "operation": op, "error": e.code, "detail": e.detail}
            _stale_field = None
            for _field in ("location_snapshot", "location_view_snapshot",
                           "base_location_reference", "view_relevant_scene_state",
                           "selected_prop_references"):
                if cur_np.get(_field) != gb.get(_field):
                    _stale_field = _field
                    break
            if _stale_field:
                return {"ok": False, "operation": op, "error": "stale_approval_basis",
                        "detail": (f"生成之后 {_stale_field} 已变化（no-peer 口径）；"
                                   f"重新 generate+review 再 approve")}
            approved_fp = cur_np["basis_fingerprint"]
            cur = view_revision(sciv)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, sciv.to_dict())

        # ---- 阶段 B：冻结 Resources 注册（无 scene 锁）----
        from src.resources.write_service import ResourceWriteService
        rws = ResourceWriteService(self.project_dir, self.project_name)
        reg = await rws.register_reference_asset(
            local=candidate_file, view="", purpose="scene_instance_view",
            source="scene_system",
            expected_document_revision=expected_resources_document_revision)
        if not reg.get("ok"):
            err = reg.get("error")
            mapped = ("resources_stale_revision" if err == "stale_revision"
                      else (err or "resources_register_failed"))
            self._approval_record(self.project_dir, {
                "approval_id": f"apv_{uuid.uuid4().hex[:12]}",
                "operation": op, "status": "aborted",
                "scene_instance_view_id": scene_instance_view_id,
                "scene_instance_id": sciv.scene_instance_id,
                "generation_id": generation_id, "candidate_index": candidate_index,
                "asset_id": None, "error": mapped, "detail": reg.get("detail"),
                "reviewed_basis_fingerprint": ev["record"].get("reviewed_basis_fingerprint"),
                "completed_at": time.time(),
            })
            return {"ok": False, "operation": op, "error": mapped,
                    "detail": f"Resources 注册失败（未 attach，无 orphan）：{reg.get('detail')}"}
        asset_id = reg["target"]

        # ---- 阶段 C：scene 短锁重验 + attach / orphan ----
        async with self.store.write_lock():
            doc = self.store.load()
            sciv2 = doc.scene_instance_views.get(scene_instance_view_id)
            still_valid = (sciv2 is not None
                           and view_revision(sciv2) == cur
                           and sciv2.active_generation_id == generation_id)
            if not still_valid:
                self._approval_record(self.project_dir, {
                    "approval_id": f"apv_{uuid.uuid4().hex[:12]}",
                    "operation": op, "status": "orphaned_reference_asset",
                    "scene_instance_view_id": scene_instance_view_id,
                    "scene_instance_id": sciv.scene_instance_id,
                    "generation_id": generation_id, "candidate_index": candidate_index,
                    "asset_id": asset_id, "error": "stale_approval_basis",
                    "detail": ("阶段 B 注册后 SceneInstanceView 已变化；ReferenceAsset 已注册"
                               "但未绑定（orphan，以后可 GC），approval_status 未变更"),
                    "reviewed_basis_fingerprint": ev["record"].get("reviewed_basis_fingerprint"),
                    "completed_at": time.time(),
                })
                return {"ok": False, "operation": op, "error": "stale_approval_basis",
                        "detail": "approve 期间 SceneInstanceView 已变化；ReferenceAsset 已注册"
                                  "但未绑定（orphaned approval asset，记录在 approval_records）",
                        "orphaned_asset_id": asset_id}
            # attach + 记录 approved_basis_fingerprint（read 层 stale 检测锚点，§3/§32；
            # S2B.1-③：no-peer 口径——peer 不属于批准语义）
            sciv2.reference_asset_id = asset_id
            sciv2.approval_status = "approved"
            sciv2.approved_basis_fingerprint = approved_fp
            self.store.save(doc)
            revision_after = view_revision(sciv2)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load()
            v4 = reloaded.scene_instance_views.get(scene_instance_view_id)
            verified = bool(v4 is not None and v4.approval_status == "approved"
                            and v4.reference_asset_id == asset_id
                            and v4.approved_basis_fingerprint)
            self._approval_record(self.project_dir, {
                "approval_id": f"apv_{uuid.uuid4().hex[:12]}",
                "operation": op, "status": "approved",
                "scene_instance_view_id": scene_instance_view_id,
                "scene_instance_id": sciv.scene_instance_id,
                "generation_id": generation_id, "candidate_index": candidate_index,
                "asset_id": asset_id, "candidate_file": candidate_file,
                "approved_basis_fingerprint": sciv2.approved_basis_fingerprint,
                "reviewed_basis_fingerprint": ev["record"].get("reviewed_basis_fingerprint"),
                "revision_after": revision_after,
                "scene_document_revision_after": doc_rev,
                "completed_at": time.time(), "verified": verified,
            })
            return {"ok": True, "operation": op, "scene_instance_view_id": scene_instance_view_id,
                    "asset_id": asset_id, "candidate_index": candidate_index,
                    "candidate_file": candidate_file,
                    "approval_status": sciv2.approval_status,
                    "approved_basis_fingerprint": sciv2.approved_basis_fingerprint,
                    "revision_after": revision_after, "verified": verified}

    async def revoke_scene_instance_view_approval(self, scene_instance_view_id: str, *,
                                                  expected_revision: str,
                                                  reason: str = "") -> dict:
        """解除 approved（§39 T39）：approval_status → rejected；历史全保留
        （approved_basis_fingerprint 保留——它仍是历史事实）。"""
        op = "scene.revoke_scene_instance_view_approval"
        async with self.store.write_lock():
            doc = self.store.load()
            sciv = doc.scene_instance_views.get(scene_instance_view_id)
            if sciv is None:
                return {"ok": False, "operation": op, "error": "instance_view_not_found",
                        "detail": f"SceneInstanceView 不存在: {scene_instance_view_id}"}
            cur = view_revision(sciv)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, sciv.to_dict())
            if sciv.approval_status != "approved":
                return {"ok": False, "operation": op, "error": "not_approved",
                        "detail": f"当前 approval_status={sciv.approval_status}"}
            before = sciv.to_dict()
            sciv.approval_status = "rejected"
            self.store.save(doc)
            revision_after = view_revision(sciv)
            self._approval_record(self.project_dir, {
                "approval_id": f"apv_{uuid.uuid4().hex[:12]}",
                "operation": op, "status": "revoked",
                "scene_instance_view_id": scene_instance_view_id,
                "scene_instance_id": sciv.scene_instance_id,
                "generation_id": sciv.active_generation_id,
                "asset_id": sciv.reference_asset_id,
                "reason": reason or "", "revision_after": revision_after,
                "completed_at": time.time(),
            })
            return {"ok": True, "operation": op,
                    "scene_instance_view_id": scene_instance_view_id,
                    "approval_status": sciv.approval_status,
                    "reference_asset_id_kept": sciv.reference_asset_id,
                    "revision_after": revision_after, "before": before}

    # ============ approval 事件流（§19/§25）============
    @staticmethod
    def _approval_record(project_dir, record: dict) -> None:
        from .generation_records import approval_records_path
        generation_records.append_record(project_dir, record,
                                         path=approval_records_path(project_dir))

    # ==================================================================
    # 显式 Approval（§25）+ cross-canonical saga（§19）
    # ==================================================================
    async def approve_location_view(self, view_id: str, *, candidate_index: int = 0,
                                    expected_revision: str,
                                    expected_resources_document_revision: str) -> dict:
        """显式审批（review pass ≠ 自动 approve；必须显式 mutation，T30）。

        Cross-canonical saga（§19）——scene_system.json 与 resources.json 是两个
        canonical，绝不假装两个 JSON 文件可以 magically atomic transaction：
          阶段 A（scene 短锁）：验证审批前提——view 存在/approval 未定/候选 committed/
                 该候选有针对当前期望指纹的最新有效 PASS review/候选文件在盘。
          阶段 B（无 scene 锁）：经冻结 Resources service 注册 ReferenceAsset
                 （resources OCC：expected_resources_document_revision）。
          阶段 C（scene 短锁）：重读 scene view，重验 revision 与 active_generation
                 未变 → attach（reference_asset_id + approval_status=approved）；
                 已变 → 绝不 attach，ReferenceAsset 记为 orphaned approval asset
                 （不偷删——写入 approval record，以后可 GC）。
        最重要：不能为了避免 orphan 牺牲 canonical 正确性（§19）。"""
        op = "scene.approve_location_view"
        from .view_review import latest_pass_review, build_review_expectations, \
            expectations_fingerprint

        # ---- 阶段 A：scene 短锁验证审批前提 ----
        async with self.store.write_lock():
            doc = self.store.load()
            view = doc.views.get(view_id)
            if view is None:
                return {"ok": False, "operation": op, "error": "view_not_found",
                        "detail": f"LocationView 不存在: {view_id}"}
            if view.approval_status == "approved":
                return {"ok": False, "operation": op, "error": "already_approved",
                        "detail": "该 View 已 approved（先 revoke 再重新审批）",
                        "reference_asset_id": view.reference_asset_id}
            generation_id = view.active_generation_id
            if not generation_id or view.generation_status != GenerationStatus.READY.value:
                return {"ok": False, "operation": op, "error": "no_candidates",
                        "detail": "没有 committed 候选（先 generate；generation 绝不自动 approve）"}
            gen_recs = [r for r in generation_records.read_records(
                self.project_dir, generation_id=generation_id)
                if r.get("operation") == "scene.generate_location_view"
                and r.get("status") == "committed"]
            if not gen_recs:
                return {"ok": False, "operation": op, "error": "generation_record_missing",
                        "detail": f"committed generation record 不存在: {generation_id}"}
            candidate_files = list(gen_recs[-1].get("candidate_files") or [])
            if not isinstance(candidate_index, int) or isinstance(candidate_index, bool) \
                    or not (0 <= candidate_index < len(candidate_files)):
                return {"ok": False, "operation": op, "error": "invalid_candidate_index",
                        "detail": f"candidate_index 必须是 0~{len(candidate_files) - 1}"}
            candidate_file = candidate_files[candidate_index]
            ev = latest_pass_review(self.project_dir, view_id=view_id,
                                    generation_id=generation_id,
                                    candidate_index=candidate_index)
            if ev is None:
                return {"ok": False, "operation": op, "error": "review_pass_required",
                        "detail": (f"candidate {candidate_index} 没有针对当前 generation 的 "
                                   f"overall=pass 审查证据（approve 双向硬门槛，T29）")}
            # 审查基准仍当前：期望指纹必须与审查时一致（view/location/anchor/layout/参考组
            # + 项目风格——P1 §15：review 记录的指纹含 visual_profile_fingerprint，approve
            # 用同口径重算，profile 变更 → stale_approval_basis，重新 review 后再 approve）
            from src.visual_profile import load_profile_or_none
            from src.visual_profile.models import (visual_profile_fingerprint,
                                                   VisualProfileSchemaError)
            try:
                _vp = load_profile_or_none(self.project_dir)
            except VisualProfileSchemaError as e:
                return {"ok": False, "operation": op, "error": "visual_profile_unreadable",
                        "detail": f"project_profile.json 损坏（fail closed）：{e}"}
            expectations = build_review_expectations(doc, view)
            fp = expectations_fingerprint(
                expectations, list(ev["record"].get("reference_asset_ids_used") or []),
                visual_profile_fingerprint=(visual_profile_fingerprint(_vp)
                                            if _vp is not None else None))
            if fp != ev["record"].get("reviewed_basis_fingerprint"):
                return {"ok": False, "operation": op, "error": "stale_approval_basis",
                        "detail": "审查之后空间语义/参考组/项目风格已变化（期望指纹不一致）；重新 review 后再 approve"}
            if not (self.project_dir / candidate_file).is_file():
                return {"ok": False, "operation": op, "error": "candidate_file_missing",
                        "detail": f"候选文件丢失: {candidate_file}"}
            cur = view_revision(view)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, view.to_dict())

        # ---- 阶段 B：经冻结 Resources service 注册 ReferenceAsset（无 scene 锁）----
        from src.resources.write_service import ResourceWriteService
        rws = ResourceWriteService(self.project_dir, self.project_name)
        reg = await rws.register_reference_asset(
            local=candidate_file, view="", purpose="location_view",
            source="scene_system",
            expected_document_revision=expected_resources_document_revision)
        if not reg.get("ok"):
            err = reg.get("error")
            mapped = "resources_stale_revision" if err == "stale_revision" else (err or "resources_register_failed")
            self._approval_record(self.project_dir, {
                "approval_id": f"apv_{uuid.uuid4().hex[:12]}",
                "operation": op, "status": "aborted",
                "location_id": view.location_id, "view_id": view_id,
                "generation_id": generation_id, "candidate_index": candidate_index,
                "asset_id": None, "error": mapped, "detail": reg.get("detail"),
                "reviewed_basis_fingerprint": ev["record"].get("reviewed_basis_fingerprint"),
                "started_at": reg.get("record", {}).get("started_at"),
                "completed_at": time.time(),
            })
            return {"ok": False, "operation": op, "error": mapped,
                    "detail": f"Resources 注册失败（未 attach，无 orphan）：{reg.get('detail')}"}
        asset_id = reg["target"]

        # ---- 阶段 C：scene 短锁重验 + attach / orphan（§19 阶段 C）----
        async with self.store.write_lock():
            doc = self.store.load()
            view2 = doc.views.get(view_id)
            still_valid = (view2 is not None
                           and view_revision(view2) == cur
                           and view2.active_generation_id == generation_id)
            if not still_valid:
                # §19：绝不 attach；新注册但未绑定的 ReferenceAsset 记为 orphan，
                # 不偷偷删除（以后可 GC）。不能为了避免 orphan 牺牲 canonical 正确性。
                self._approval_record(self.project_dir, {
                    "approval_id": f"apv_{uuid.uuid4().hex[:12]}",
                    "operation": op, "status": "orphaned_reference_asset",
                    "location_id": view.location_id, "view_id": view_id,
                    "generation_id": generation_id, "candidate_index": candidate_index,
                    "asset_id": asset_id, "error": "stale_approval_basis",
                    "detail": "阶段 B 注册后 scene view 已变化（revision/active_generation "
                              "不一致）；ReferenceAsset 已注册但未绑定（orphan，以后可 GC），"
                              "approval_status 未变更",
                    "reviewed_basis_fingerprint": ev["record"].get("reviewed_basis_fingerprint"),
                    "started_at": None, "completed_at": time.time(),
                })
                return {"ok": False, "operation": op, "error": "stale_approval_basis",
                        "detail": "approve 期间 view 已变化；ReferenceAsset 已注册但未绑定"
                                  "（orphaned approval asset，记录在 approval_records，以后可 GC）",
                        "orphaned_asset_id": asset_id}
            view2.reference_asset_id = asset_id
            view2.approval_status = "approved"
            self.store.save(doc)
            revision_after = view_revision(view2)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load()
            v4 = reloaded.views.get(view_id)
            verified = bool(v4 is not None and v4.approval_status == "approved"
                            and v4.reference_asset_id == asset_id)
            self._approval_record(self.project_dir, {
                "approval_id": f"apv_{uuid.uuid4().hex[:12]}",
                "operation": op, "status": "approved",
                "location_id": view.location_id, "view_id": view_id,
                "generation_id": generation_id, "candidate_index": candidate_index,
                "asset_id": asset_id, "candidate_file": candidate_file,
                "reviewed_basis_fingerprint": ev["record"].get("reviewed_basis_fingerprint"),
                "reference_asset_ids_used": list(ev["record"].get("reference_asset_ids_used") or []),
                "revision_after": revision_after,
                "scene_document_revision_after": doc_rev,
                "started_at": None, "completed_at": time.time(), "verified": verified,
            })
            return {"ok": True, "operation": op, "view_id": view_id,
                    "asset_id": asset_id, "candidate_index": candidate_index,
                    "candidate_file": candidate_file,
                    "approval_status": view2.approval_status,
                    "revision_after": revision_after, "verified": verified}

    async def revoke_view_approval(self, view_id: str, *, expected_revision: str,
                                   reason: str = "") -> dict:
        """解除 approved 状态（§25 Revoke）：approval_status → rejected。
        不删除历史：generation record / review record / ReferenceAsset / 之前的
        approval record 全部保留（reference_asset_id 字段保留指向历史资产——
        它只是历史事实，未来消费方以 approval_status 为准）。"""
        op = "scene.revoke_view_approval"
        async with self.store.write_lock():
            doc = self.store.load()
            view = doc.views.get(view_id)
            if view is None:
                return {"ok": False, "operation": op, "error": "view_not_found",
                        "detail": f"LocationView 不存在: {view_id}"}
            cur = view_revision(view)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, view.to_dict())
            if view.approval_status != "approved":
                return {"ok": False, "operation": op, "error": "not_approved",
                        "detail": f"当前 approval_status={view.approval_status}（只有 approved 可 revoke）"}
            before = view.to_dict()
            view.approval_status = "rejected"
            self.store.save(doc)
            revision_after = view_revision(view)
            self._approval_record(self.project_dir, {
                "approval_id": f"apv_{uuid.uuid4().hex[:12]}",
                "operation": op, "status": "revoked",
                "location_id": view.location_id, "view_id": view_id,
                "generation_id": view.active_generation_id,
                "asset_id": view.reference_asset_id,   # 历史保留（不删）
                "reason": reason or "", "revision_after": revision_after,
                "completed_at": time.time(),
            })
            return {"ok": True, "operation": op, "view_id": view_id,
                    "approval_status": view.approval_status,
                    "reference_asset_id_kept": view.reference_asset_id,
                    "revision_after": revision_after, "before": before}
