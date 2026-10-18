# R2 B1：正式接纳协调器。
# 协议（03 文档 §B1 正式接纳与崩溃恢复）：
#   1. 前置校验（审核回执有效 / 项目实例匹配 / 输入基线 / 用户选版未变 / 候选未失效）
#   2. 分配 promotion_id，接纳意图持久化（SQLite）
#   3. 产物预备到不可变正式位置（正式写锁外复制 + 摘要校验）
#   4. entering committing → Project 原子写：只更新对应 shot 的版本与选版，
#      同一次写中携带 promotion_id 标识（generation_basis）；guard 内重核用户选版
#   5. 账本结算 accepted + 待投递事件
#   6. 事件由 deliver_events 投递（调度器消费）；崩溃恢复按 promotion_id 对账
# 不变量：绝不先标 accepted 再写正式项目；同一 promotion_id 只有一个正式生效结果。
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .ledger import CollabLedger, LedgerError

PromotionHook = Callable[[dict], None]


class PromotionBlocked(RuntimeError):
    """接纳前置条件不满足（保留候选并标明原因，不视为通过）。"""

    def __init__(self, reason: str, *, code: str = "blocked"):
        super().__init__(reason)
        self.code = code


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class PromotionCoordinator:
    """候选 → 正式选版的唯一入口。所有前置校验在服务端完成，不信任调用方自报。"""

    def __init__(self, ledger: CollabLedger,
                 crash_hooks: Optional[Dict[str, PromotionHook]] = None):
        self.ledger = ledger
        # 测试用崩溃注入点：after_intent / after_prepare / after_project_write
        self.crash_hooks = crash_hooks or {}

    def _hook(self, name: str):
        hook = self.crash_hooks.get(name)
        if hook is not None:
            hook({"hook": name})

    # ---- 前置校验 ----
    def _check_preconditions(self, pdir: Path, cand: dict, *,
                             project_instance_id: str,
                             expected_input_signature: Optional[str]) -> dict:
        from src.core.store import ProjectStore
        from src.generation.video_commit import video_input_signature

        if cand["invalid_reason"]:
            raise PromotionBlocked(f"candidate invalid: {cand['invalid_reason']}",
                                   code="invalid")
        if cand["acceptance_state"] != "pending":
            raise PromotionBlocked(
                f"candidate acceptance={cand['acceptance_state']} (expected pending)",
                code="state")
        if cand["execution_state"] != "done":
            raise PromotionBlocked(
                f"candidate execution={cand['execution_state']}", code="state")
        if cand["project_instance_id"] != project_instance_id:
            raise PromotionBlocked(
                f"project instance mismatch: candidate={cand['project_instance_id']} "
                f"target={project_instance_id}", code="identity")

        project = ProjectStore(pdir).load()
        if project.incarnation_id != project_instance_id:
            raise PromotionBlocked(
                f"project on-disk incarnation {project.incarnation_id!r} != "
                f"claimed {project_instance_id!r}", code="identity")

        receipt = self.ledger.latest_review(cand["candidate_id"])
        if receipt is None:
            raise PromotionBlocked("no review receipt", code="review")
        if receipt["overall"] != "pass":
            raise PromotionBlocked(f"review overall={receipt['overall']}", code="review")
        if receipt["content_sha256"] != cand["artifact_sha256"]:
            raise PromotionBlocked("review content sha != artifact sha", code="review")
        artifact = Path(cand["artifact_path"])
        if not artifact.is_file() or sha256_file(artifact) != cand["artifact_sha256"]:
            raise PromotionBlocked("artifact missing or hash drift", code="artifact")
        if expected_input_signature is not None and \
                cand["base_input_signature"] != expected_input_signature:
            raise PromotionBlocked("input signature drift vs expected", code="baseline")
        # 输入基线：候选登记时的签名应与当前项目重编译签名一致（输入变了=保留候选）
        try:
            shot = project.get_shot(cand["shot_id"])
            current_sig = video_input_signature(project, shot) if shot else None
        except Exception:
            current_sig = None
        if current_sig is not None and cand["base_input_signature"] and \
                current_sig != cand["base_input_signature"]:
            raise PromotionBlocked("project input signature changed since candidate",
                                   code="baseline")
        return {"project": project,
                "signature": project._store_signature}

    # ---- 主流程 ----
    def promote(self, pdir: Path, candidate_id: str, *,
                project_instance_id: str, run_id: Optional[str] = None,
                expected_input_signature: Optional[str] = None) -> dict:
        """候选正式接纳。返回 {promotion_id, state, version_id, ...}；
        幂等：同候选已 accepted → 直接返回既有结果。"""
        from src.core.store import ProjectStore, ProjectConflictError
        from src.core.models import VideoVersion

        cand = self.ledger.get_candidate(candidate_id)
        if cand is None:
            raise LedgerError(f"candidate not found: {candidate_id}")

        promo = self.ledger.begin_promotion(
            candidate_id=candidate_id, project_instance_id=project_instance_id,
            run_id=run_id or cand["run_id"], shot_id=cand["shot_id"],
            version_id=cand["candidate_id"], intent={"trigger": "api"})
        promotion_id = promo["promotion_id"]
        if promo["state"] == "accepted":
            return promo  # 幂等短路（B07 重复接纳命令）
        self._hook("after_intent")

        checks = self._check_preconditions(
            pdir, cand, project_instance_id=project_instance_id,
            expected_input_signature=expected_input_signature)

        # 3. 产物预备（正式写锁外）：不可变正式位置 + 摘要校验
        self.ledger.transition_promotion(promotion_id, to_state="preparing",
                                         from_states=("intent",))
        store = ProjectStore(pdir)
        formal_dir = pdir / "output" / "formal" / promotion_id
        formal_dir.mkdir(parents=True, exist_ok=True)
        formal_path = formal_dir / Path(cand["artifact_path"]).name
        if not formal_path.is_file() or sha256_file(formal_path) != cand["artifact_sha256"]:
            shutil.copyfile(cand["artifact_path"], formal_path)
            if sha256_file(formal_path) != cand["artifact_sha256"]:
                self.ledger.transition_promotion(
                    promotion_id, to_state="rejected",
                    from_states=("preparing",),
                    result={"error": "artifact copy hash mismatch"})
                self.ledger.set_candidate_states(
                    candidate_id, acceptance_state="rejected",
                    invalid_reason="artifact copy hash mismatch")
                raise PromotionBlocked("artifact copy hash mismatch", code="artifact")
        self._hook("after_prepare")

        # 4. committing → Project 原子写（guard 内重核用户选版）
        self.ledger.transition_promotion(promotion_id, to_state="committing",
                                         from_states=("preparing",))
        self.ledger.set_candidate_states(candidate_id, acceptance_state="committing",
                                         from_acceptance=("pending",))
        version_id = f"v_{promotion_id}"

        def mutator(shot):
            version = VideoVersion(
                version_id=version_id, created_at=time.time(), status="done",
                local=str(formal_path), origin_run_id=run_id or cand["run_id"],
                generation_basis={"promotion_id": promotion_id,
                                  "accepted_from_candidate": candidate_id,
                                  "artifact_sha256": cand["artifact_sha256"],
                                  "acceptance": "r2_promotion_v1"})
            shot.video_versions.append(version)
            shot.final_video = version_id

        def guard(project, shot):
            # 选版 CAS：生成时基线与当前正式选版必须一致（B04：不抢回用户选版）
            if (shot.final_video or None) != (cand["base_final_video"] or None):
                raise PromotionBlocked(
                    f"user selection changed: base={cand['base_final_video']!r} "
                    f"current={shot.final_video!r}", code="selection_changed")

        try:
            store.update_shot(cand["shot_id"], mutator, guard=guard)
        except PromotionBlocked as e:
            self.ledger.transition_promotion(
                promotion_id, to_state="rejected", from_states=("committing",),
                result={"error": str(e), "code": e.code})
            self.ledger.set_candidate_states(
                candidate_id, acceptance_state="rejected",
                invalid_reason=f"promotion blocked: {e.code}")
            raise
        self._hook("after_project_write")

        # 5. 结算 + 事件
        result = {"version_id": version_id, "local": str(formal_path),
                  "shot_id": cand["shot_id"]}
        self.ledger.transition_promotion(promotion_id, to_state="accepted",
                                         from_states=("committing",), result=result)
        self.ledger.set_candidate_states(candidate_id, acceptance_state="accepted",
                                         from_acceptance=("committing",))
        event_id = self.ledger.enqueue_event(
            kind="promotion.accepted", promotion_id=promotion_id,
            candidate_id=candidate_id,
            payload={"project_instance_id": project_instance_id,
                     "run_id": run_id or cand["run_id"], "shot_id": cand["shot_id"],
                     "version_id": version_id})
        self._hook("after_settle")
        return {**self.ledger.get_promotion(promotion_id), "event_id": event_id}  # type: ignore[return-value]

    # ---- 崩溃恢复 ----
    def recover(self, pdir_by_instance: Dict[str, Path]) -> dict:
        """重启对账：对每个未终态 promotion 按 promotion_id 检查正式项目 JSON。
        已落盘 → 补结算/补事件；未落盘 → 重新校验后幂等推进；歧义 → recovery_required。
        绝不先标 accepted 再写正式项目（顺序不可逆）。"""
        from src.core.store import ProjectStore
        out = {"settled": [], "redone": [], "rejected": [], "recovery_required": []}
        for promo in self.ledger.list_promotions():
            if promo["state"] in ("accepted", "rejected"):
                continue
            pid = promo["promotion_id"]
            inst = promo["project_instance_id"]
            pdir = pdir_by_instance.get(inst)
            if pdir is None:
                self.ledger.transition_promotion(
                    pid, to_state="recovery_required", result={
                        "error": "project dir unknown for instance"})
                out["recovery_required"].append(pid)
                continue
            try:
                found = self._find_promoted_version(pdir, pid)
            except Exception as e:  # 项目读失败=歧义，不猜
                self.ledger.transition_promotion(
                    pid, to_state="recovery_required",
                    result={"error": f"project read failed: {e}"})
                out["recovery_required"].append(pid)
                continue
            cand = self.ledger.get_candidate(promo["candidate_id"]) or {}
            if found:
                result = {"version_id": found["version_id"], "local": found["local"],
                          "shot_id": promo["shot_id"], "recovered": True}
                self.ledger.transition_promotion(
                    pid, to_state="accepted", from_states=("intent", "preparing",
                                                           "committing"),
                    result=result)
                if cand:
                    self.ledger.set_candidate_states(
                        cand["candidate_id"], acceptance_state="accepted")
                self.ledger.enqueue_event(
                    kind="promotion.accepted", promotion_id=pid,
                    candidate_id=promo["candidate_id"],
                    payload={"project_instance_id": inst,
                             "run_id": promo["run_id"], "shot_id": promo["shot_id"],
                             "version_id": found["version_id"], "recovered": True})
                out["settled"].append(pid)
                continue
            # 未落盘：从 intent/preparing 重做（幂等——formal 副本已存在则复用）
            if promo["state"] == "committing":
                self.ledger.transition_promotion(
                    pid, to_state="preparing", from_states=("committing",))
                if cand.get("acceptance_state") == "committing":
                    self.ledger.set_candidate_states(
                        cand["candidate_id"], acceptance_state="pending",
                        from_acceptance=("committing",))
            try:
                redone = self.promote(
                    pdir, promo["candidate_id"], project_instance_id=inst,
                    run_id=promo["run_id"])
                out["redone"].append(redone["promotion_id"])
            except PromotionBlocked as e:
                out["rejected"].append(
                    {"promotion_id": pid, "code": e.code, "reason": str(e)})
        return out

    @staticmethod
    def _find_promoted_version(pdir: Path, promotion_id: str) -> Optional[dict]:
        from src.core.store import ProjectStore
        project = ProjectStore(pdir).load()
        shot = project.get_shot  # noqa: F841（可读性）
        for s in project.all_shots():
            for v in s.video_versions:
                marker = (v.generation_basis or {}).get("promotion_id")
                if marker == promotion_id:
                    return {"shot_id": s.shot_id, "version_id": v.version_id,
                            "local": v.local or ""}
        return None

    # ---- 事件投递 ----
    def deliver_events(self, handler: Callable[[dict], None]) -> int:
        """投递待处理事件；handler 抛错的留在待投递（下次重试，幂等键=promotion_id
        由消费方保证）。返回成功投递数。"""
        delivered: List[str] = []
        for evt in self.ledger.pending_events():
            try:
                handler(evt)
            except Exception:
                continue
            delivered.append(evt["event_id"])
        return self.ledger.mark_events_delivered(delivered)
