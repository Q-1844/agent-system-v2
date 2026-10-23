# tests/relstore/test_relstore_17_g4b23a_r008.py
# G4-B2.3a-R008：归档回执状态机修复。
# 缺陷（修复前实测）：set_verified_restorable 无归档状态前置检查，
# SUPERSEDED 后的旧 fp1 回执仍可被记录为 VERIFIED_RESTORABLE。
# 本文件先写「明确失败」的回归测试（对未修复代码为红），修复后转绿；
# 原始失败复现证据见 测试20/evidence/r008_red_run.txt。
import pytest

from tools.relstore.project_registry import (
    FakeCAS, Registry, CasConflict, RegistryOutcomeUnknown, RegistryPoisoned,
    ARC_SUPERSEDED, ARC_VERIFIED_RESTORABLE)


def make_reg(now_val=1000.0):
    return Registry(FakeCAS(), now=lambda: now_val)


# ==================== 红灯回归：旧回执必须被拒绝 ====================

class TestStaleReceiptMustBeRejected:
    """R-008 主回归：begin_snapshot(fp1) → SUPERSEDED → 提交旧 fp1 回执。
    修复前：回执被接受，archive_status 被覆盖为 VERIFIED_RESTORABLE（缺陷）。
    修复后：ValueError 拒绝，且不改变任何权威数据、不产生 CAS PUT。"""

    def _superseded(self):
        reg = make_reg()
        reg.create_project(project_id="pid_P1", alias="P1",
                           operation_id="op_r008_create")
        reg.plan_archive(project_id="pid_P1", operation_id="op_r008_plan")
        reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fp1",
                           operation_id="op_r008_begin1")
        reg.supersede_archive(project_id="pid_P1", new_fingerprint="fp2",
                              operation_id="op_r008_supersede")
        return reg

    def test_stale_receipt_rejected(self):
        reg = self._superseded()
        rev_before = reg.state.registry_revision
        history_before = list(reg.cas.history)
        ref_before = reg.get("pid_P1").archive_ref
        with pytest.raises(ValueError, match="R-008"):
            reg.set_verified_restorable(
                project_id="pid_P1",
                archive_ref={"sha256": "old", "path": "p",
                             "tree_fingerprint": "fp1"},
                restorable_level="full",
                operation_id="op_r008_setvr_stale")
        # 权威数据零变化：状态、ref、revision、CAS 历史
        rec = reg.get("pid_P1")
        assert rec.archive_status == ARC_SUPERSEDED
        assert rec.archive_ref == ref_before
        assert reg.state.registry_revision == rev_before
        assert list(reg.cas.history) == history_before   # 无 CAS PUT

    def test_fingerprint_mismatch_rejected(self):
        """状态合法（SNAPSHOTTING）但回执指纹与权威快照不符 → 拒绝。"""
        reg = make_reg()
        reg.create_project(project_id="pid_P1", alias="P1",
                           operation_id="op_r008_create2")
        reg.plan_archive(project_id="pid_P1", operation_id="op_r008_plan2")
        reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fp_real",
                           operation_id="op_r008_begin2")
        rev_before = reg.state.registry_revision
        with pytest.raises(ValueError, match="R-008"):
            reg.set_verified_restorable(
                project_id="pid_P1",
                archive_ref={"sha256": "x", "tree_fingerprint": "fp_other"},
                restorable_level="full",
                operation_id="op_r008_setvr_mismatch")
        assert reg.state.registry_revision == rev_before
        assert reg.get("pid_P1").archive_status == "SNAPSHOTTING"

    def test_receipt_without_valid_snapshot_rejected(self):
        """无有效快照（未 begin_snapshot，snapshot_fingerprint 为 None）→ 拒绝。"""
        reg = make_reg()
        reg.create_project(project_id="pid_P1", alias="P1",
                           operation_id="op_r008_create3")
        reg.plan_archive(project_id="pid_P1", operation_id="op_r008_plan3")
        with pytest.raises(ValueError, match="R-008"):
            reg.set_verified_restorable(
                project_id="pid_P1",
                archive_ref={"sha256": "x", "tree_fingerprint": "fp"},
                restorable_level="full",
                operation_id="op_r008_setvr_nosnap")

    def test_rejection_does_not_poison_instance(self):
        """拒绝是本地校验，不触碰 CAS，实例不得被毒化，后续合法流程可用。"""
        reg = self._superseded()
        with pytest.raises(ValueError):
            reg.set_verified_restorable(
                project_id="pid_P1",
                archive_ref={"sha256": "old", "tree_fingerprint": "fp1"},
                restorable_level="full",
                operation_id="op_r008_setvr_stale2")
        # 实例仍可正常读/写（未毒化）
        reg.record_activity(project_id="pid_P1", kind="save_shot",
                            operation_id="op_r008_act_after_reject")
        assert reg.get("pid_P1").last_activity_kind == "save_shot"


# ==================== CAS 并发：另一实例使快照 SUPERSEDED ====================

class TestCasConcurrencyStaleReceipt:

    def test_concurrent_supersede_blocks_stale_receipt(self):
        """实例 A begin_snapshot 后，实例 B（权威）supersede；A 持旧内存
        提交旧回执——即使 A 本地前置检查被绕过/通过，也必须被权威 CAS
        版本检查拦下（CasConflict），权威状态保持 SUPERSEDED。"""
        cas = FakeCAS()
        a = Registry(cas, now=lambda: 1000.0)
        a.create_project(project_id="pid_P1", alias="P1",
                         operation_id="op_r008_c_create")
        a.plan_archive(project_id="pid_P1", operation_id="op_r008_c_plan")
        a.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fp1",
                         operation_id="op_r008_c_begin")
        # B 从权威 CAS 重载并 supersede（权威前进）
        b = Registry(cas, now=lambda: 1001.0)
        b.supersede_archive(project_id="pid_P1", new_fingerprint="fp2",
                           operation_id="op_r008_c_supersede_b")
        # A（内存仍自以为 SNAPSHOTTING）提交旧回执 → CAS 版本检查拦截
        with pytest.raises(CasConflict):
            a.set_verified_restorable(
                project_id="pid_P1",
                archive_ref={"sha256": "old", "tree_fingerprint": "fp1"},
                restorable_level="full",
                operation_id="op_r008_c_setvr_a")
        # A 毒化（fail-closed）；权威仍 SUPERSEDED、VERIFIED 未达成
        with pytest.raises(RegistryPoisoned):
            a.ranking()
        fresh = Registry(cas, now=lambda: 1002.0)
        assert fresh.get("pid_P1").archive_status == ARC_SUPERSEDED


# ==================== 正确回执不退化 + 幂等 ====================

class TestValidReceiptStillWorks:

    def _snapshotted(self):
        reg = make_reg()
        reg.create_project(project_id="pid_P1", alias="P1",
                           operation_id="op_r008_v_create")
        reg.plan_archive(project_id="pid_P1", operation_id="op_r008_v_plan")
        reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fp_now",
                           operation_id="op_r008_v_begin")
        return reg

    def test_valid_receipt_verified(self):
        reg = self._snapshotted()
        r = reg.set_verified_restorable(
            project_id="pid_P1",
            archive_ref={"sha256": "x", "tree_fingerprint": "fp_now"},
            restorable_level="full",
            operation_id="op_r008_v_setvr")
        assert r["ok"] is True and r["evict_permitted"] is False
        assert reg.get("pid_P1").archive_status == ARC_VERIFIED_RESTORABLE

    def test_valid_receipt_replay_idempotent_zero_write(self):
        reg = self._snapshotted()
        ref = {"sha256": "x", "tree_fingerprint": "fp_now"}
        reg.set_verified_restorable(project_id="pid_P1", archive_ref=ref,
                                    restorable_level="full",
                                    operation_id="op_r008_v_setvr")
        rev_before = reg.state.registry_revision
        history_before = list(reg.cas.history)
        r2 = reg.set_verified_restorable(project_id="pid_P1", archive_ref=ref,
                                         restorable_level="full",
                                         operation_id="op_r008_v_setvr")
        assert r2["ok"] is True
        assert reg.state.registry_revision == rev_before
        assert list(reg.cas.history) == history_before   # 零重复写入


# ==================== 既有恢复链不退化（紧凑复验） ====================

class TestRecoveryPathsNoRegression:

    def test_unknown_outcome_and_conflict_still_fail_closed(self):
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1000.0)
        reg.create_project(project_id="pid_P1", alias="P1",
                           operation_id="op_r008_r_create")
        # 未知结果：非冲突异常 → RegistryOutcomeUnknown，读 fail-closed
        def boom(new_doc, expected_revision, *, operation_id):
            raise RuntimeError("timeout-like")
        reg.cas.commit = boom
        with pytest.raises(RegistryOutcomeUnknown):
            reg.record_activity(project_id="pid_P1", kind="save_shot",
                                operation_id="op_r008_r_act")
        with pytest.raises(RegistryOutcomeUnknown):
            reg.ranking()
        # CAS 冲突路径：另一实例前进后旧实例提交 → CasConflict → 毒化
        cas2 = FakeCAS()
        x = Registry(cas2, now=lambda: 1000.0)
        x.create_project(project_id="pid_X", alias="X",
                         operation_id="op_r008_r_createx")
        y = Registry(cas2, now=lambda: 1001.0)
        y.record_activity(project_id="pid_X", kind="save_shot",
                          operation_id="op_r008_r_acty")
        with pytest.raises(CasConflict):
            x.record_activity(project_id="pid_X", kind="save_shot",
                              operation_id="op_r008_r_actx")
        with pytest.raises(RegistryPoisoned):
            x.ranking()
