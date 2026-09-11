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

from src.resources.generation import agnes_image_inputs

from . import generation_records
from .generation_records import generation_records_path
from .models import (
    GenerationStatus,
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
                                       if k in ("expected_revision", "current_revision")})

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
                seed=seed, candidate_count=candidate_count, reference_selection=selection)
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
            # approval_status 保持原状（generation 绝不自动 approve，T26）
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
