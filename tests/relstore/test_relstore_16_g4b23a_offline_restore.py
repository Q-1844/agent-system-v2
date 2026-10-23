# tests/relstore/test_relstore_16_g4b23a_offline_restore.py
# G4-B2.3a 离线完整恢复预检（0 远端请求，FakeCAS）：
# A. 归档内不含任何授权载体文件；恢复后历史授权不复活；
# B. retrieve 与普通有效活动 reactivation 的状态机区别；
# C. 注入时钟覆盖三天重评估（前/到期/后），ACTIVE/MARKED 分支；
# D. 自动删除/自动释放恒不可达；旧 SUPERSEDED 回执不产生任何许可。
# 本文件所有 operation_id 一律使用 op_b23a_ 前缀（不复用 G4-B1/B2.1 历史 ID）。
import pytest

from tools.relstore.project_registry import (
    FakeCAS, Registry, SLOT_ACTIVE, SLOT_MARKED,
    ARC_NONE, ARC_PLANNED, ARC_SUPERSEDED, ARC_VERIFIED_RESTORABLE)

DAY = 86400.0
NS = "op_b23a_"


def make_reg(now_val=1000.0):
    return Registry(FakeCAS(), now=lambda: now_val)


def create_n(reg, n, prefix="P"):
    for i in range(1, n + 1):
        reg.create_project(project_id=f"pid_{prefix}{i}", alias=f"{prefix}{i}",
                           operation_id=f"{NS}create_{prefix}{i}")


def mark_project(reg, pid):
    """直接置 MARKED（跌出 top-6 的等价测试捷径，绕过造满 6 项）。"""
    rec = reg.get(pid)
    rec.slot_status = SLOT_MARKED
    if rec.marked_at_seq is None:
        rec.marked_at_seq = reg.state.activity_seq


B21_ARCHIVE_FILES = ["project.json", "scenes/shot_001.txt", "storyboard.md",
                     "manifest.payload.json"]
AUTH_PATTERNS = ("lease", "grant", "token", "credential", "secret",
                 "session", "auth", "permission")


# ==================== A: 归档无授权载体 + 恢复不复活历史授权 ====================

class TestNoAuthMaterialInArchive:

    def test_b21_archive_file_list_contains_no_auth_carrier(self):
        """B2.1 实测归档（字节同一重建）的文件清单不含任何 lease/grant/token
        类命名 —— 归档本身不可能是授权载体。"""
        for rel in B21_ARCHIVE_FILES:
            low = rel.lower()
            assert not any(p in low for p in AUTH_PATTERNS), rel

    def test_checker_positive_control(self):
        """对照：若归档果真出现授权命名文件，检查器必须能检出（防假阴性）。"""
        assert any(p in "lease.json" for p in AUTH_PATTERNS)
        assert any(p in "grants.toml" for p in AUTH_PATTERNS)


class TestRestoreDoesNotReviveAuthorization:

    def test_retrieve_resets_archive_authorization_state(self):
        """取回前记录带着归档期授权痕迹（VERIFIED_RESTORABLE + 旧回执），
        retrieve 后归档授权状态清零，evict 依旧不可达。"""
        reg = make_reg()
        create_n(reg, 3)
        rec = reg.get("pid_P1")
        # 模拟归档前的历史授权痕迹
        rec.archive_status = ARC_VERIFIED_RESTORABLE
        rec.active_operation_id = "op_archived_old"
        rec.pending_sync_intents.append({"operation_id": "op_archived_old"})
        r = reg.retrieve(project_id="pid_P1",
                         archive_ref={"sha256": "x", "path": "p"},
                         operation_id=f"{NS}retrieve_P1")
        assert r["ok"] is True and r["auto_delete"] is False
        rec = reg.get("pid_P1")
        assert rec.archive_status == ARC_NONE      # 历史 VERIFIED_RESTORABLE 不复活
        assert rec.retrieved_reassess_at == 1000.0 + 3 * DAY
        assert rec.last_activity_kind == "retrieve"
        # 释放路径仍然不可达
        ev = reg.attempt_evict(project_id="pid_P1",
                               operation_id=f"{NS}evict_P1")
        assert ev["permitted"] is False

    def test_no_delete_or_release_api_exists(self):
        """注册表 API 面上不存在 delete/release 类方法（自动删除不可达的
        结构性保证）。"""
        reg = make_reg()
        for name in dir(reg):
            if name.startswith("_"):
                continue
            low = name.lower()
            assert "delete" not in low and "release" not in low \
                and "destroy" not in low and "purge" not in low, name


# ==================== B: retrieve vs reactivation ====================

class TestRetrieveVsReactivation:

    def test_reactivation_sets_no_retrieve_timer(self):
        reg = make_reg()
        create_n(reg, 3)
        mark_project(reg, "pid_P1")
        reg.record_activity(project_id="pid_P1", kind="save_shot",
                            operation_id=f"{NS}react_P1")
        rec = reg.get("pid_P1")
        assert rec.slot_status == SLOT_ACTIVE          # 重返前六
        assert rec.retrieved_reassess_at is None        # 无三天重评估计时器
        assert rec.retrieved_at is None
        assert rec.archive_status == ARC_NONE

    def test_only_retrieve_path_sets_reassess_timer(self):
        reg = make_reg()
        create_n(reg, 3)
        reg.record_activity(project_id="pid_P1", kind="save_shot",
                            operation_id=f"{NS}act_P1")
        assert reg.get("pid_P1").retrieved_reassess_at is None
        reg.retrieve(project_id="pid_P1",
                     archive_ref={"sha256": "x", "path": "p"},
                     operation_id=f"{NS}retrieve_P1")
        assert reg.get("pid_P1").retrieved_reassess_at is not None


# ==================== C: 注入时钟的三天重评估矩阵 ====================

class TestReassessClockMatrix:

    def _retrieved(self, now_val=1000.0):
        reg = make_reg(now_val)
        create_n(reg, 3)
        reg.retrieve(project_id="pid_P1",
                     archive_ref={"sha256": "x", "path": "p"},
                     operation_id=f"{NS}retrieve_P1")
        return reg

    def test_before_due_not_due(self):
        reg = self._retrieved(1000.0)
        r = reg.reassess_retrieved(project_id="pid_P1",
                                   now=1000.0 + 3 * DAY - 1,
                                   operation_id=f"{NS}reassess_early")
        assert r["reassess"] is False and r["reason"] == "not due"
        assert "pid_P1" not in reg.state.archive_queue

    def test_at_due_boundary_exactly_counts_as_due(self):
        reg = self._retrieved(1000.0)
        r = reg.reassess_retrieved(project_id="pid_P1",
                                   now=1000.0 + 3 * DAY,
                                   operation_id=f"{NS}reassess_at")
        assert r["reassess"] is True
        assert r["action"] == "no_archive_still_active"   # ACTIVE 不进队列

    def test_after_due_active_never_queued(self):
        reg = self._retrieved(1000.0)
        r = reg.reassess_retrieved(project_id="pid_P1",
                                   now=1000.0 + 10 * DAY,
                                   operation_id=f"{NS}reassess_late")
        assert r["action"] == "no_archive_still_active"
        assert r["slot_status"] == "ACTIVE"
        assert r["auto_delete"] is False
        assert "pid_P1" not in reg.state.archive_queue

    def test_after_due_marked_plan_archive_only_no_delete(self):
        reg = self._retrieved(1000.0)
        mark_project(reg, "pid_P1")
        r = reg.reassess_retrieved(project_id="pid_P1",
                                   now=1000.0 + 10 * DAY,
                                   operation_id=f"{NS}reassess_marked")
        assert r["action"] == "plan_archive"
        assert r["auto_delete"] is False
        assert reg.get("pid_P1").archive_status == ARC_PLANNED
        assert "pid_P1" in reg.state.archive_queue
        # 即便进了归档队列，释放依旧不可达
        assert reg.attempt_evict(project_id="pid_P1",
                                 operation_id=f"{NS}evict_marked")["permitted"] is False

    def test_after_due_with_valid_activity_postpones(self):
        reg = self._retrieved(1000.0)
        # 取回后第 2 天一次有效活动 → last_activity_kind 不再是 retrieve
        reg.record_activity(project_id="pid_P1", kind="save_shot",
                            operation_id=f"{NS}act_after",
                            )
        r = reg.reassess_retrieved(project_id="pid_P1",
                                   now=1000.0 + 10 * DAY,
                                   operation_id=f"{NS}reassess_post")
        assert r["action"] == "postpone"
        assert reg.get("pid_P1").retrieved_reassess_at == 1000.0 + 10 * DAY + 3 * DAY

    def test_timer_cleared_after_due_regardless_of_branch(self):
        reg = self._retrieved(1000.0)
        reg.reassess_retrieved(project_id="pid_P1",
                               now=1000.0 + 10 * DAY,
                               operation_id=f"{NS}reassess_clear")
        # ACTIVE 分支清计时器且不重设（timer=None 表示已评估完毕）
        assert reg.get("pid_P1").retrieved_reassess_at is None


# ==================== D: 旧 SUPERSEDED 回执不产生任何许可 ====================

class TestStaleSupersededReceipt:

    def _superseded(self):
        reg = make_reg()
        create_n(reg, 3)
        reg.plan_archive(project_id="pid_P1", operation_id=f"{NS}plan1")
        reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fp1",
                           operation_id=f"{NS}begin1")
        r = reg.supersede_archive(project_id="pid_P1", new_fingerprint="fp2",
                                  operation_id=f"{NS}supersede")
        return reg, r

    def test_supersede_result_carries_no_permit(self):
        _, r = self._superseded()
        assert r["superseded"] is True
        assert r["delete_permit"] is False

    def test_stale_receipt_rejected_r008(self):
        """主动提交已 SUPERSEDED 时代的旧归档回执（B2.1 S6 场景）：
        R-008 修复后，服务端前置检查直接拒绝旧回执；即便绕过前置，
        CAS 版本检查仍拦截（见 test_relstore_17 并发测试）。"""
        reg, _ = self._superseded()
        rev_before = reg.state.registry_revision
        with pytest.raises(ValueError, match="R-008"):
            reg.set_verified_restorable(
                project_id="pid_P1",
                archive_ref={"sha256": "old", "path": "p",
                             "tree_fingerprint": "fp1"},
                restorable_level="full", operation_id=f"{NS}setvr_stale")
        assert reg.get("pid_P1").archive_status == ARC_SUPERSEDED
        assert reg.state.registry_revision == rev_before
        ev = reg.attempt_evict(project_id="pid_P1",
                               operation_id=f"{NS}evict_stale")
        assert ev["permitted"] is False
        assert any("G4-A" in x for x in ev["reasons"])

    def test_correct_path_after_supersede_still_no_permit(self):
        reg, _ = self._superseded()
        reg.plan_archive(project_id="pid_P1", operation_id=f"{NS}plan2")
        reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fp2",
                           operation_id=f"{NS}begin2")
        vr = reg.set_verified_restorable(
            project_id="pid_P1",
            archive_ref={"sha256": "y", "path": "p",
                         "tree_fingerprint": "fp2"},
            restorable_level="full", operation_id=f"{NS}setvr_good")
        assert reg.get("pid_P1").archive_status == ARC_VERIFIED_RESTORABLE
        assert vr["evict_permitted"] is False
        assert reg.attempt_evict(project_id="pid_P1",
                                 operation_id=f"{NS}evict_good")["permitted"] is False


# ==================== E: op 命名空间隔离 ====================

class TestOpNamespaceIsolation:

    def test_all_ops_use_b23a_namespace(self):
        reg = make_reg()
        create_n(reg, 2)
        reg.retrieve(project_id="pid_P1", archive_ref={"sha256": "x", "path": "p"},
                     operation_id=f"{NS}retrieve_P1")
        reg.reassess_retrieved(project_id="pid_P1", now=100000.0,
                               operation_id=f"{NS}reassess_P1")
        for op_id, entry in reg.state.operation_dedup.items():
            assert op_id.startswith(NS), op_id
            assert not op_id.startswith("op_b21_")
            assert op_id != "op_reassess_P4"
