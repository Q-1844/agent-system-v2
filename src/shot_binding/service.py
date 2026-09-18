# src/shot_binding/service.py
# Shot Production Binding V1 — Phase S3B.1 read/update 服务层。
#
# 🔴 纯读原则（7.md S3B.1 §十二）：load/read 零副作用——文件不存在视为 no bindings，
#    绝不创建空文件，无 hidden mutation。
# 🔴 OCC（§十一）：per-binding revision = sha256(canonical_json(binding))[:8]；
#    replace/delete 必须带 expected_binding_revision，stale → 拒绝不 silent retry；
#    create 以"锁内 absence check"为 OCC（尚无 per-binding revision 可比）。
#    所有 mutation 的 check-then-write 全程持 project_write_lock（同 project_name key，
#    与 project.json / resources.json / scene_system.json / project_profile.json 同锁族）。
# 🔴 bind 时 fail closed（S3B.1 §五/§六/§七/§八/§十）：所有 FK / 审批 / 词表 /
#    cast 一致性 / composition×prop 冲突校验失败 → 明确 error receipt，绝不猜。
# 🔴 dependency_fingerprints 在 bind 成功时计算**当前值快照**（写侧一次性动作）。
#    S3B.2 的 validity read model（重算+对比+stale 检测）与显式 refresh 本阶段不实现。
# 🔴 Legacy / UNBOUND：无 binding 条目不是错误；不做任何自动创建/自动猜（§十四）。
#    orphan（Shot 被删后 binding 仍在）read 层原样返回，标记与 GC 属 S3B.2（§十五）。
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    ACTION_KINDS_REQUIRING_PROP,
    BindingAction,
    BindingActionKind,
    CharacterBinding,
    DependencyFingerprints,
    DialogueSpeakerBinding,
    DialogueSpeakerLine,
    PropBinding,
    SceneBinding,
    ShotProductionBinding,
    binding_revision,
    document_revision,
)
from .store import ShotBindingStore
from .reference_pack import PackBlockedError


class _ValidationError(Exception):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _issue_text(issue: dict) -> str:
    """validity issue → 可读错误文本（保留 stale_reasons 等结构化信息）。"""
    d = dict(issue.get("detail") or {})
    hint = str(d.pop("hint", "") or "")
    extra = "; ".join(f"{k}={v}" for k, v in d.items())
    return "; ".join(p for p in (hint, extra) if p)


def _s(value: Any) -> str:
    return str(value or "").strip()


def _require_id(value: Any, field: str) -> str:
    v = _s(value)
    if not v:
        raise _ValidationError("validation_error", f"{field} 必填且不能为空")
    return v


class ShotBindingService:
    """shot_production_bindings.json 读/写服务（HTTP 层见 routers/shot_binding.py）。"""

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.dir = Path(project_dir)
        self.store = ShotBindingStore(self.dir, project_name)

    # ==================================================================
    # read（纯读，零副作用）
    # ==================================================================
    def read_document(self) -> dict:
        doc = self.store.load()
        return {"schema_version": doc.schema_version,
                "document_revision": document_revision(doc),
                "bindings": {k: v.to_dict() for k, v in doc.bindings.items()}}

    def read_binding(self, shot_id: str) -> Optional[dict]:
        """单条 binding read。无条目 → None（UNBOUND 不是错误，§十四）。
        Shot 已被删除时本阶段原样返回（orphan 标记属 S3B.2，绝不自动清理，§十五）。"""
        doc = self.store.load()
        b = doc.bindings.get(_s(shot_id))
        if b is None:
            return None
        return {"binding": b.to_dict(),
                "binding_revision": binding_revision(b),
                "document_revision": document_revision(doc)}

    # ==================================================================
    # mutation（sync 核心假定单写者/锁已持；async 包装持 project_write_lock）
    # ==================================================================
    async def create_binding_async(self, payload: dict) -> dict:
        async with self.store.write_lock():
            return self.create_binding(payload)

    async def replace_binding_async(self, shot_id: str, payload: dict, *,
                                    expected_binding_revision: str) -> dict:
        async with self.store.write_lock():
            return self.replace_binding(shot_id, payload,
                                        expected_binding_revision=expected_binding_revision)

    async def delete_binding_async(self, shot_id: str, *,
                                   expected_binding_revision: str) -> dict:
        async with self.store.write_lock():
            return self.delete_binding(shot_id,
                                       expected_binding_revision=expected_binding_revision)

    # ------------------------------------------------------------------
    def create_binding(self, payload: dict) -> dict:
        op = "shot_binding.create"
        try:
            return self._mutate(op, payload, expect_existing=False)
        except _ValidationError as e:
            return {"ok": False, "operation": op, "error": e.code, "detail": e.detail}

    def replace_binding(self, shot_id: str, payload: dict, *,
                        expected_binding_revision: str) -> dict:
        op = "shot_binding.replace"
        try:
            return self._mutate(op, payload, expect_existing=True,
                                target_shot_id=_s(shot_id),
                                expected_binding_revision=_s(expected_binding_revision))
        except _ValidationError as e:
            return {"ok": False, "operation": op, "error": e.code, "detail": e.detail}

    def delete_binding(self, shot_id: str, *, expected_binding_revision: str) -> dict:
        op = "shot_binding.delete"
        try:
            doc = self.store.load()
            sid = _require_id(shot_id, "shot_id")
            b = doc.bindings.get(sid)
            if b is None:
                return {"ok": False, "operation": op, "error": "binding_not_found",
                        "detail": f"binding 不存在: {sid}"}
            cur = binding_revision(b)
            if _s(expected_binding_revision) != cur:
                return {"ok": False, "operation": op, "error": "stale_binding_revision",
                        "detail": "expected_binding_revision 与当前 binding revision 不一致",
                        "expected_binding_revision": _s(expected_binding_revision),
                        "current_binding_revision": cur}
            del doc.bindings[sid]
            self.store.save(doc)
            return {"ok": True, "operation": op, "shot_id": sid,
                    "document_revision": document_revision(doc)}
        except _ValidationError as e:
            return {"ok": False, "operation": op, "error": e.code, "detail": e.detail}

    # ==================================================================
    # S3B.2：Derived Validity read model（纯读零副作用——read 永不 refresh）
    # ==================================================================
    def read_validity(self, shot_id: str) -> dict:
        """§4 read model：READY / NOT_READY / UNBOUND + 结构化 issues。
        read → derive → return；绝不创建/修改 binding、绝不更新快照（§3）。"""
        from .validity import compute_binding_validity
        return compute_binding_validity(self.dir, self.store.project_name, shot_id)

    # ==================================================================
    # S3B.2：Explicit Refresh——本阶段唯一新增写行为（§20-§24）
    # ==================================================================
    async def refresh_binding_async(self, shot_id: str, *,
                                    expected_binding_revision: str) -> dict:
        async with self.store.write_lock():
            return self.refresh_binding(shot_id,
                                        expected_binding_revision=expected_binding_revision)

    def refresh_binding(self, shot_id: str, *, expected_binding_revision: str) -> dict:
        """唯一允许更新 dependency_fingerprints snapshot 的操作（§20）。
        语义 =「确认保留当前 Binding Intent，接受当前依赖作为新 freshness basis」。
        不是 repair / rebind / regenerate / approve / migrate（§20）：
        - 只写 dependency_fingerprints + updated_at（§23 Intent 字段结构性不可变——
          payload 直接取自现有 binding，不经用户输入）；
        - unpinned 保持 unpinned（§24，仅把解析到的 SIV basis 记入快照）；
        - 真正 invalid 的依赖（missing/revoked/unapproved/conflict/cast/orphan/
          pinned SIV missing/Profile unspecified）→ fail closed 拒绝（§22：refresh
          不能掩盖 invalid）；SIV stale 属"stale but currently valid"→ 可 refresh，
          但 validity 诚实重报 stale_dependency（Scene 侧修复 + 再次 refresh 才 READY）；
        - 复用 S3B.1 store/OCC/锁/原子写，无第二套写路径（§21）。"""
        op = "shot_binding.refresh"
        try:
            from .validity import (resolve_scene_instance_view, siv_freshness_issue)
            from src.visual_profile import load_profile_or_none
            doc = self.store.load()
            sid = _require_id(shot_id, "shot_id")
            b = doc.bindings.get(sid)
            if b is None:
                return {"ok": False, "operation": op, "error": "binding_not_found",
                        "detail": f"binding 不存在: {sid}"}
            cur = binding_revision(b)
            if _s(expected_binding_revision) != cur:
                return {"ok": False, "operation": op, "error": "stale_binding_revision",
                        "detail": "expected_binding_revision 与当前 binding revision 不一致",
                        "expected_binding_revision": _s(expected_binding_revision),
                        "current_binding_revision": cur}

            project, rdoc, sdoc = self._load_canonicals()

            # ---- §6：Binding 自身仍须与当前 Shot 一致（orphan / scene / cast）----
            shot = project.get_shot(sid)
            if shot is None:
                return {"ok": False, "operation": op, "error": "orphan_binding",
                        "detail": f"binding 存在但 Shot 已删除: {sid}（不 refresh orphan）"}
            if b.scene_id != shot.scene_id:
                raise _ValidationError(
                    "scene_mismatch",
                    f"binding.scene_id ({b.scene_id}) 与 Shot.scene_id "
                    f"({shot.scene_id}) 不一致")
            # Intent 字段直接取自现有 binding（§23：结构性保证 refresh 不改 Intent）
            char_bindings = list(b.character_bindings)
            prop_bindings = list(b.prop_bindings)
            self._check_cast(char_bindings, shot.characters)
            self._validate_resources(project, char_bindings, prop_bindings, rdoc)

            # ---- Scene 结构校验（复用 S3B.1 管线，按当前 canonical 重查）----
            scene_instance_rev = None
            sciv_fp = None
            if b.scene_binding is not None:
                scene_instance_rev, _ = self._validate_scene(
                    b.scene_binding, shot.scene_id, sdoc)
                if b.scene_binding.pinned_scene_instance_view_id:
                    sciv = sdoc.scene_instance_views[
                        b.scene_binding.pinned_scene_instance_view_id]
                else:
                    sciv, issue = resolve_scene_instance_view(sdoc, b.scene_binding)
                    if issue is not None:
                        raise _ValidationError(issue["reason"], _issue_text(issue))
                # §22 current-usability：approved + 指纹可得。🔴 SIV stale 不在拒绝清单
                # （stale-but-valid 正是 refresh 的适用场景）；refresh 后 validity 诚实
                # 重报 stale_dependency——Scene 侧重新生成/批准 + 再次 refresh 才 READY。
                fresh_issue = siv_freshness_issue(
                    self.dir, self.store.project_name, sciv,
                    b.dependency_fingerprints.scene_instance_view,
                    compare_snapshot=False, include_staleness=False)
                if fresh_issue is not None:
                    raise _ValidationError(fresh_issue["reason"],
                                           _issue_text(fresh_issue))
                sciv_fp = sciv.approved_basis_fingerprint

            # ---- §19/§22：Profile unspecified / 不可读 → refresh 拒绝 ----
            try:
                profile = load_profile_or_none(self.dir)
            except Exception as e:   # noqa: BLE001
                raise _ValidationError("visual_profile_unreadable",
                                       f"project_profile.json 不可读: {e}") from e
            if profile is None:
                raise _ValidationError(
                    "project_visual_profile_unspecified",
                    "项目未设置 ProjectVisualProfile（bound shot 必需，不 fallback）")

            # ---- 计算当前指纹（含 unpinned 解析到的 SIV basis，§24）----
            fps = self._compute_fingerprints(project, rdoc, char_bindings,
                                             prop_bindings, b.scene_binding,
                                             scene_instance_rev, sciv_fp)

            # ---- 只写 freshness basis（§23；原子写，OCC 已在锁内通过）----
            b.dependency_fingerprints = fps
            b.updated_at = time.time()
            self.store.save(doc)
            return {"ok": True, "operation": op,
                    "binding": b.to_dict(),
                    "binding_revision": binding_revision(b),
                    "document_revision": document_revision(doc)}
        except _ValidationError as e:
            return {"ok": False, "operation": op, "error": e.code, "detail": e.detail}

    # ==================================================================
    # S3B.3：ShotReferencePack Compiler——derived / ephemeral / 只读（§6/§35/§36）
    # ==================================================================
    def compile_reference_pack(self, shot_id: str) -> dict:
        """READY Binding + 当前 canonical → model-neutral ShotReferencePack。
        Gate（§5，硬性）：复用 S3B.2 validity——UNBOUND→BLOCKED(binding_unbound)；
        NOT_READY→BLOCKED（转发结构化 issues）；只有 READY 进入编译。
        Compile 永不 refresh / 不写回 / 不落盘（无 pack store 文件，§36）。
        一致性守卫（§37）：pre/post validity 对比（readiness + binding_revision +
        document_revision + resolved SIV）——不一致 → BLOCKED
        dependency_changed_during_compile，绝不输出 mixed-state Pack。
        canonical 损坏（坏 JSON/未知 schema）按 store 规则显式异常，不伪装 BLOCKED（§39）。"""
        sid = _s(shot_id)
        pre = self.read_validity(sid)
        if pre["readiness"] == "UNBOUND":
            return {"shot_id": sid, "status": "BLOCKED", "ok": False,
                    "binding_readiness": "UNBOUND", "pack": None,
                    "issues": [{"dependency_type": "pack", "dependency_id": sid,
                                "reason": "binding_unbound",
                                "detail": {"hint": "无 binding——不猜、不自动建、不使用 legacy 参考（§5 Case A）"}}]}
        if pre["readiness"] == "NOT_READY":
            return {"shot_id": sid, "status": "BLOCKED", "ok": False,
                    "binding_readiness": "NOT_READY", "pack": None,
                    "issues": pre["issues"]}
        # READY → compile（pre==READY 已由 validity 门保证）
        try:
            material = self._collect_pack_material(sid, pre)
            from .reference_pack import build_pack
            pack = build_pack(**material)
        except PackBlockedError as e:
            return {"shot_id": sid, "status": "BLOCKED", "ok": False,
                    "binding_readiness": "READY", "pack": None,
                    "issues": [e.issue()]}
        post = self.read_validity(sid)
        if post["readiness"] != "READY" \
                or post["binding_revision"] != pre["binding_revision"] \
                or post["document_revision"] != pre["document_revision"] \
                or post["resolved_dependencies"] != pre["resolved_dependencies"]:
            return {"shot_id": sid, "status": "BLOCKED", "ok": False,
                    "binding_readiness": post["readiness"], "pack": None,
                    "issues": post["issues"] + [
                        {"dependency_type": "pack", "dependency_id": sid,
                         "reason": "dependency_changed_during_compile",
                         "detail": {"hint": "pre/post validity 基准不一致——拒绝 mixed-state Pack"}}]}
        return {"shot_id": sid, "status": "COMPILED", "ok": True,
                "binding_readiness": "READY",
                "pack": pack.to_dict(), "issues": []}

    def _collect_pack_material(self, shot_id: str, pre_validity: dict) -> dict:
        """收集 build_pack 所需 canonical 实体（只读）。独立方法=确定性 race 测试的
        patch seam（§61/§63/§64：在 pre 与 post 之间注入依赖变更）。
        任何解析失败 → PackBlockedError（§38：绝不返回半完整材料）。"""
        from .reference_pack import PackBlockedError
        from src.core.store import ProjectStore
        from src.resources.store import ResourceStore
        from src.scene_system.store import SceneSystemStore
        from src.visual_profile import load_profile_or_none
        from src.visual_profile.models import visual_profile_fingerprint
        from src.scene_system.visual_service import SceneVisualService

        try:
            project = ProjectStore(self.dir).load()
            rdoc = ResourceStore(self.dir, self.store.project_name).load()
            sdoc = SceneSystemStore(self.dir, self.store.project_name).load()
        except Exception as e:   # noqa: BLE001 —— race 中 canonical 不可读 → fail closed
            raise PackBlockedError("reference_asset_unresolvable",
                                   {"hint": f"canonical 读取失败: {e}"}) from e
        shot = project.get_shot(shot_id)
        if shot is None:
            raise PackBlockedError("reference_asset_unresolvable",
                                   {"hint": "Shot 在 compile 期间消失"})
        doc = self.store.load()
        binding = doc.bindings.get(shot_id)
        if binding is None:
            raise PackBlockedError("reference_asset_unresolvable",
                                   {"hint": "binding 在 compile 期间消失"})
        profile = None
        try:
            profile = load_profile_or_none(self.dir)
        except Exception as e:   # noqa: BLE001
            raise PackBlockedError("project_visual_profile_unspecified",
                                   {"hint": f"project_profile.json 不可读: {e}"}) from e
        profile_fp = None
        if profile is not None:
            profile_fp = visual_profile_fingerprint(profile)

        # SIV 解析（镜像 S3B.2 read model 规则；pinned 精确 / unpinned 按对唯一）
        siv = None
        base_view = None
        location = None
        siv_status = None
        if binding.scene_binding is not None:
            sb = binding.scene_binding
            inst = sdoc.scene_instances.get(sb.scene_instance_id)
            if sb.pinned_scene_instance_view_id:
                siv = sdoc.scene_instance_views.get(sb.pinned_scene_instance_view_id)
            else:
                from .validity import resolve_scene_instance_view
                siv, issue = resolve_scene_instance_view(sdoc, sb)
                if issue is not None:
                    raise PackBlockedError(issue["reason"], issue["detail"])
            if siv is None:
                raise PackBlockedError("reference_asset_unresolvable",
                                       {"scene_instance_view_id": sb.pinned_scene_instance_view_id,
                                        "hint": "SIV 在 compile 期间消失"})
            base_view = sdoc.views.get(sb.location_view_id)
            location = sdoc.locations.get(inst.location_id) if inst else None
            siv_status = SceneVisualService(self.dir, self.store.project_name) \
                .read_scene_instance_view_status(siv.scene_instance_view_id)
            if siv_status is None:
                raise PackBlockedError("reference_asset_unresolvable",
                                       {"scene_instance_view_id": siv.scene_instance_view_id,
                                        "hint": "Scene System read model 无法返回 SIV 状态"})

        return {"shot": shot, "binding": binding, "rdoc": rdoc, "sdoc": sdoc,
                "profile": profile, "profile_fingerprint": profile_fp,
                "siv": siv, "base_view": base_view, "location": location,
                "siv_status": siv_status,
                "binding_revision": pre_validity["binding_revision"],
                "document_revision": pre_validity["document_revision"],
                "project_dir": self.dir,
                "character_descriptions": {c.name: (c.description_en or "")
                                           for c in (project.characters or [])}}

    # ------------------------------------------------------------------
    def _mutate(self, op: str, payload: dict, *, expect_existing: bool,
                target_shot_id: Optional[str] = None,
                expected_binding_revision: Optional[str] = None) -> dict:
        """create / replace 共用主体：load → OCC → 校验 → 快照 → save。
        所有读校验与指纹快照在同一临界区内完成（async 包装持锁）。"""
        payload = dict(payload or {})
        doc = self.store.load()

        shot_id = _require_id(payload.get("shot_id"), "shot_id")
        if target_shot_id is not None and shot_id != target_shot_id:
            raise _ValidationError(
                "validation_error",
                f"payload.shot_id ({shot_id}) 与目标 ({target_shot_id}) 不一致")

        existing = doc.bindings.get(shot_id)
        if expect_existing:
            if existing is None:
                return {"ok": False, "operation": op, "error": "binding_not_found",
                        "detail": f"binding 不存在: {shot_id}"}
            cur = binding_revision(existing)
            if _s(expected_binding_revision) != cur:
                return {"ok": False, "operation": op, "error": "stale_binding_revision",
                        "detail": "expected_binding_revision 与当前 binding revision 不一致",
                        "expected_binding_revision": _s(expected_binding_revision),
                        "current_binding_revision": cur}
        else:
            if existing is not None:
                return {"ok": False, "operation": op, "error": "binding_already_exists",
                        "detail": f"binding 已存在: {shot_id}（replace 请走 replace_binding）"}

        # ---- canonical 依赖读取（只读，fail closed）----
        project, rdoc, sdoc = self._load_canonicals()

        # ---- 结构解析 + 校验（确定性顺序，逐项 fail closed）----
        scene_id = _require_id(payload.get("scene_id"), "scene_id")
        shot = project.get_shot(shot_id)
        if shot is None:
            raise _ValidationError("shot_missing", f"Shot 不存在: {shot_id}")
        if shot.scene_id != scene_id:
            raise _ValidationError(
                "scene_mismatch",
                f"binding.scene_id ({scene_id}) 与 Shot.scene_id ({shot.scene_id}) 不一致")

        char_bindings = self._parse_character_bindings(payload.get("character_bindings"))
        self._check_cast(char_bindings, shot.characters)
        prop_bindings = self._parse_prop_bindings(payload.get("prop_bindings"))
        action = self._parse_action(payload.get("action"))
        scene_binding = self._parse_scene_binding(payload.get("scene_binding"))
        # Native AV 可选块（B1 §6：只表达 intent/裁决，不复制正文）
        dialogue_speaker_binding = self._parse_dialogue_speaker_binding(
            payload.get("dialogue_speaker_binding"))

        # ---- 跨域校验：Resources（角色/Look/Prop/Composition + 冲突规则）----
        self._validate_resources(project, char_bindings, prop_bindings, rdoc)

        # ---- action 校验（最小 V1；prop 只查 FK 存在性——approved 与否属 S3B.2
        #      validity 口径，action 是 temporal intent 不是视觉绑定）----
        if action is not None:
            self._validate_action(action,
                                  {cb.character_name for cb in char_bindings}, rdoc)

        # ---- 跨域校验：Scene System ----
        sciv_fingerprint = None
        scene_instance_rev = None
        if scene_binding is not None:
            scene_instance_rev, sciv_fingerprint = self._validate_scene(
                scene_binding, shot.scene_id, sdoc)

        # ---- 组装 + 时间戳 ----
        now = time.time()
        binding = ShotProductionBinding(
            shot_id=shot_id,
            scene_id=scene_id,
            scene_binding=scene_binding,
            character_bindings=char_bindings,
            prop_bindings=prop_bindings,
            action=action,
            dialogue_speaker_binding=dialogue_speaker_binding,
            production_notes=_s(payload.get("production_notes")),
            created_at=existing.created_at if (expect_existing and existing) else now,
            updated_at=now,
        )
        binding.dependency_fingerprints = self._compute_fingerprints(
            project, rdoc, char_bindings, prop_bindings,
            scene_binding, scene_instance_rev, sciv_fingerprint)

        doc.bindings[shot_id] = binding
        self.store.save(doc)
        return {"ok": True, "operation": op,
                "binding": binding.to_dict(),
                "binding_revision": binding_revision(binding),
                "document_revision": document_revision(doc)}

    # ==================================================================
    # canonical 依赖加载（只读）
    # ==================================================================
    def _load_canonicals(self) -> Tuple[Any, Any, Any]:
        from src.core.store import ProjectStore
        from src.resources.store import ResourceStore
        from src.scene_system.store import SceneSystemStore
        try:
            project = ProjectStore(self.dir).load()
        except FileNotFoundError as e:
            raise _ValidationError("project_unreadable", f"project.json 不存在: {e}") from e
        try:
            rdoc = ResourceStore(self.dir, self.store.project_name).load()
        except Exception as e:   # noqa: BLE001 —— 坏 canonical 一律 fail closed，不猜
            raise _ValidationError("resources_unreadable",
                                   f"resources.json 不可读: {e}") from e
        try:
            sdoc = SceneSystemStore(self.dir, self.store.project_name).load()
        except Exception as e:   # noqa: BLE001
            raise _ValidationError("scene_system_unreadable",
                                   f"scene_system.json 不可读: {e}") from e
        return project, rdoc, sdoc

    # ==================================================================
    # payload 结构解析（shape 层）
    # ==================================================================
    @staticmethod
    def _parse_character_bindings(raw: Any) -> List[CharacterBinding]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise _ValidationError("validation_error", "character_bindings 必须是列表")
        out: List[CharacterBinding] = []
        for item in raw:
            if not isinstance(item, dict):
                raise _ValidationError("validation_error",
                                       "character_bindings 元素必须是对象")
            out.append(CharacterBinding(
                character_name=_require_id(item.get("character_name"), "character_name"),
                look_id=_require_id(item.get("look_id"), "look_id"),
                composition_id=_s(item.get("composition_id")) or None))
        seen = set()
        for cb in out:
            if cb.character_name in seen:
                raise _ValidationError("duplicate_character_binding",
                                       f"同一角色重复绑定: {cb.character_name}")
            seen.add(cb.character_name)
        return out

    @staticmethod
    def _parse_prop_bindings(raw: Any) -> List[PropBinding]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise _ValidationError("validation_error", "prop_bindings 必须是列表")
        out: List[PropBinding] = []
        for item in raw:
            if not isinstance(item, dict):
                raise _ValidationError("validation_error", "prop_bindings 元素必须是对象")
            out.append(PropBinding(
                prop_id=_require_id(item.get("prop_id"), "prop_id"),
                relation=_s(item.get("relation")) or "held",
                anchor=_s(item.get("anchor")) or "right_hand",
                visual_state_note=_s(item.get("visual_state_note"))))
        seen = set()
        for pb in out:
            if pb.prop_id in seen:
                raise _ValidationError("duplicate_prop_binding",
                                       f"同一 prop 重复绑定: {pb.prop_id}")
            seen.add(pb.prop_id)
        return out

    @staticmethod
    def _parse_action(raw: Any) -> Optional[BindingAction]:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise _ValidationError("validation_error", "action 必须是对象")
        kind = _require_id(raw.get("kind"), "action.kind")
        if kind not in {k.value for k in BindingActionKind}:
            raise _ValidationError(
                "invalid_action_kind",
                f"action.kind 必须是 {[k.value for k in BindingActionKind]} 之一，收到: {kind!r}")
        return BindingAction(kind=kind,
                             subject_character=_s(raw.get("subject_character")),
                             prop_id=_s(raw.get("prop_id")),
                             start_state=_s(raw.get("start_state")),
                             end_state=_s(raw.get("end_state")),
                             note=_s(raw.get("note")))

    @staticmethod
    def _parse_scene_binding(raw: Any) -> Optional[SceneBinding]:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise _ValidationError("validation_error", "scene_binding 必须是对象")
        return SceneBinding(
            scene_instance_id=_require_id(raw.get("scene_instance_id"),
                                          "scene_binding.scene_instance_id"),
            location_view_id=_require_id(raw.get("location_view_id"),
                                         "scene_binding.location_view_id"),
            pinned_scene_instance_view_id=
                _s(raw.get("pinned_scene_instance_view_id")) or None)

    @staticmethod
    def _parse_dialogue_speaker_binding(raw: Any
                                        ) -> Optional[DialogueSpeakerBinding]:
        """B1 §6 可选块（C1 productionize 17.md §9）：payload → 结构化块。
        只表达 index→speaker 映射（不复制台词正文）；speaker ∈ Shot.characters
        由消费侧（dialogue_control 解析）fail-closed 校验。"""
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise _ValidationError("validation_error",
                                   "dialogue_speaker_binding 必须是对象")
        raw_lines = raw.get("lines")
        if raw_lines is not None and not isinstance(raw_lines, list):
            raise _ValidationError("validation_error",
                                   "dialogue_speaker_binding.lines 必须是数组")
        lines: List[DialogueSpeakerLine] = []
        for ln in (raw_lines or []):
            if not isinstance(ln, dict):
                raise _ValidationError(
                    "validation_error", "dialogue_speaker_binding.lines[] 必须是对象")
            idx = ln.get("index")
            if isinstance(idx, bool) or not isinstance(idx, int):
                raise _ValidationError(
                    "invalid_binding",
                    f"dialogue_speaker_binding.lines[].index 必须是整数，得到 {idx!r}")
            speaker = _s(ln.get("speaker_character_name"))
            if not speaker:
                raise _ValidationError(
                    "invalid_binding",
                    f"dialogue_speaker_binding index {idx} speaker_character_name 为空")
            lines.append(DialogueSpeakerLine(index=idx,
                                             speaker_character_name=speaker))
        return DialogueSpeakerBinding(lines=lines,
                                      resolution=_s(raw.get("resolution")))

    # ==================================================================
    # 语义校验（fail closed）
    # ==================================================================
    @staticmethod
    def _check_cast(char_bindings: List[CharacterBinding],
                    shot_characters: List[str]) -> None:
        """7.md S3B.1 §十：binding character set == shot.characters，否则
        cast_mismatch。不自动添加/删除角色，不从 Shot.characters 推导 binding。"""
        binding_set = {cb.character_name for cb in char_bindings}
        shot_set = {str(c or "").strip() for c in (shot_characters or []) if str(c or "").strip()}
        if binding_set != shot_set:
            raise _ValidationError(
                "cast_mismatch",
                f"binding 角色集合 {sorted(binding_set)} != shot.characters "
                f"{sorted(shot_set)}（不自动添加/删除角色）")

    def _validate_resources(self, project, char_bindings: List[CharacterBinding],
                            prop_bindings: List[PropBinding], rdoc: Any) -> None:
        from src.resources.models import CompositionAnchor, CompositionRelation
        valid_relations = {r.value for r in CompositionRelation}
        valid_anchors = {a.value for a in CompositionAnchor}

        for pb in prop_bindings:
            prop = rdoc.props.get(pb.prop_id)
            if prop is None:
                raise _ValidationError("prop_missing", f"Prop 不存在: {pb.prop_id}")
            if not self._has_usable_approved(rdoc, prop.approved_reference_asset_ids):
                raise _ValidationError(
                    "prop_not_approved",
                    f"Prop 无可用 approved reference: {pb.prop_id}")
            if pb.relation not in valid_relations:
                raise _ValidationError(
                    "invalid_relation",
                    f"relation 必须是 Resources CompositionRelation 词表 {sorted(valid_relations)} 之一，"
                    f"收到: {pb.relation!r}")
            if pb.anchor not in valid_anchors:
                raise _ValidationError(
                    "invalid_anchor",
                    f"anchor 必须是 Resources CompositionAnchor 词表 {sorted(valid_anchors)} 之一，"
                    f"收到: {pb.anchor!r}")

        composition_prop_ids: set = set()
        for cb in char_bindings:
            # Character 存在性（project.json 侧；cast 校验只保证集合一致，
            # 不保证角色实体存在——shot.characters 可能含未定义名字）
            if project.get_character(cb.character_name) is None:
                raise _ValidationError("character_missing",
                                       f"Character 不存在于 project.json: {cb.character_name}")
            look = rdoc.looks.get(cb.look_id)
            if look is None:
                raise _ValidationError("look_missing", f"CharacterLook 不存在: {cb.look_id}")
            if look.character_name != cb.character_name:
                raise _ValidationError(
                    "look_character_mismatch",
                    f"Look {cb.look_id} 属于 {look.character_name}，"
                    f"与 binding 角色 {cb.character_name} 不匹配")
            if not self._has_usable_approved(rdoc, look.approved_reference_asset_ids):
                raise _ValidationError(
                    "look_not_approved",
                    f"Look 无可用 approved reference: {cb.look_id}")
            if cb.composition_id:
                comp = rdoc.compositions.get(cb.composition_id)
                if comp is None:
                    raise _ValidationError("composition_missing",
                                           f"Composition 不存在: {cb.composition_id}")
                if comp.character_name != cb.character_name:
                    raise _ValidationError(
                        "composition_character_mismatch",
                        f"Composition {cb.composition_id} 属于 {comp.character_name}，"
                        f"与 binding 角色 {cb.character_name} 不匹配")
                if comp.look_id != cb.look_id:
                    raise _ValidationError(
                        "composition_look_mismatch",
                        f"Composition {cb.composition_id} 的 look_id ({comp.look_id}) "
                        f"与 binding look_id ({cb.look_id}) 不一致")
                if comp.approval_status != "approved":
                    raise _ValidationError(
                        "composition_not_approved",
                        f"Composition 未 approved: {cb.composition_id}")
                composition_prop_ids |= {b.prop_id for b in comp.prop_bindings}

        # ---- §七 Composition × direct prop 冲突硬规则 ----
        direct_ids = {pb.prop_id for pb in prop_bindings}
        conflict = sorted(direct_ids & composition_prop_ids)
        if conflict:
            raise _ValidationError(
                "binding_conflict",
                f"prop 同时被 composition 覆盖与 direct prop_bindings 声明: {conflict}"
                "（不自动猜哪一个正确；请改绑或移除 direct 声明）")

    @staticmethod
    def _has_usable_approved(rdoc: Any, approved_ids: List[str]) -> bool:
        """存在至少一个 approved 且真实在 reference_assets 里的参考
        （指向已删除 asset 的 approved id 不算可用——诚实 fail closed）。
        S3B.2：实现移入 validity.has_usable_approved（与 validity read 共享）。"""
        from .validity import has_usable_approved
        return has_usable_approved(rdoc, approved_ids)

    @staticmethod
    def _validate_action(action: BindingAction, cast_names: set, rdoc: Any) -> None:
        """action 最小 V1 校验（7.md S3B.1 §九）。不建 Timeline/ActionGraph/
        状态机/多动作序列；不修改 Shot.action_sequence。"""
        if action.kind in ACTION_KINDS_REQUIRING_PROP:
            if not action.prop_id:
                raise _ValidationError("action_prop_required",
                                       f"action.kind={action.kind} 必须提供 prop_id")
            if not action.subject_character:
                raise _ValidationError("action_subject_required",
                                       f"action.kind={action.kind} 必须提供 subject_character")
        if action.subject_character and action.subject_character not in cast_names:
            raise _ValidationError(
                "action_subject_unknown",
                f"action.subject_character ({action.subject_character}) "
                f"不在 binding 角色集合 {sorted(cast_names)} 中")
        if action.prop_id and action.prop_id not in rdoc.props:
            raise _ValidationError("action_prop_missing",
                                   f"action.prop_id 不存在于 resources.json: {action.prop_id}")

    def _validate_scene(self, scene_binding: SceneBinding, scene_id: str,
                        sdoc: Any) -> Tuple[str, Optional[str]]:
        """SceneBinding 校验（7.md S3B.1 §八）。返回 (scene_instance_revision,
        pinned SIV 的 approved_basis_fingerprint 或 None)。
        本阶段不做 unpinned SIV 自动解析（fingerprint 记 None，解析属 S3B.2/S3B.3）。"""
        from src.scene_system.models import SceneInstanceStatus, scene_instance_revision
        inst = sdoc.scene_instances.get(scene_binding.scene_instance_id)
        if inst is None:
            raise _ValidationError("scene_instance_missing",
                                   f"SceneInstance 不存在: {scene_binding.scene_instance_id}")
        if inst.status != SceneInstanceStatus.ACTIVE.value:
            raise _ValidationError("scene_instance_not_active",
                                   f"SceneInstance 非 active: {inst.status}")
        if inst.story_scene_id != scene_id:
            raise _ValidationError(
                "story_scene_mismatch",
                f"SceneInstance.story_scene_id ({inst.story_scene_id}) "
                f"!= Shot.scene_id ({scene_id})")
        view = sdoc.views.get(scene_binding.location_view_id)
        if view is None:
            raise _ValidationError("location_view_missing",
                                   f"LocationView 不存在: {scene_binding.location_view_id}")
        if view.location_id != inst.location_id:
            raise _ValidationError(
                "location_view_not_in_location",
                f"LocationView {view.location_id} 不属于 instance 的 location "
                f"{inst.location_id}")
        sciv_fp: Optional[str] = None
        if scene_binding.pinned_scene_instance_view_id:
            sciv = sdoc.scene_instance_views.get(
                scene_binding.pinned_scene_instance_view_id)
            if sciv is None:
                raise _ValidationError(
                    "scene_instance_view_missing",
                    f"SceneInstanceView 不存在: "
                    f"{scene_binding.pinned_scene_instance_view_id}")
            if sciv.scene_instance_id != scene_binding.scene_instance_id:
                raise _ValidationError(
                    "scene_instance_view_instance_mismatch",
                    f"pinned SIV 属于 instance {sciv.scene_instance_id}，"
                    f"与 scene_binding 的 {scene_binding.scene_instance_id} 不一致")
            if sciv.location_view_id != scene_binding.location_view_id:
                raise _ValidationError(
                    "scene_instance_view_view_mismatch",
                    f"pinned SIV 属于 view {sciv.location_view_id}，"
                    f"与 scene_binding 的 {scene_binding.location_view_id} 不一致")
            if sciv.approval_status != "approved":
                raise _ValidationError(
                    "scene_instance_view_not_approved",
                    f"pinned SIV 未 approved: {sciv.scene_instance_view_id}")
            sciv_fp = sciv.approved_basis_fingerprint  # 可能为 None——诚实记录
        return scene_instance_revision(inst), sciv_fp

    # ==================================================================
    # dependency fingerprint 快照（bind/refresh 时刻一次性计算；比对在 validity.py）
    # ==================================================================
    def _compute_fingerprints(self, project, rdoc, char_bindings, prop_bindings,
                              scene_binding, scene_instance_rev,
                              sciv_fingerprint) -> DependencyFingerprints:
        # S3B.2：实现移入 validity.compute_fingerprints（create/replace/refresh 共享，
        # 行为逐字节不变——S3B.1 回归保护）。
        from .validity import compute_fingerprints
        return compute_fingerprints(self.dir, project, rdoc, char_bindings,
                                    prop_bindings, scene_binding,
                                    scene_instance_rev, sciv_fingerprint)
