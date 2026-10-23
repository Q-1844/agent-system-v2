# tests/relstore/test_relstore_18_snapshot_round_boundary.py
# G4-B2.2 PRE-FLIGHT：快照轮次边界。
# 场景：begin(fpA) → SUPERSEDED → 新一轮 fpB → 源又变回 fpA → 重新 begin(fpA)
#       → 提交第一轮 fpA 的旧回执。
# 现行契约（R-008 修复后）= 内容寻址：
#   回执有效 ⟺ 归档进行中 ∧ 回执 tree_fingerprint == 权威 snapshot_fingerprint。
#   同指纹不同轮次 → 接受（内容相同即同一快照内容）。
# 本文件将该语义显式固化并验证其安全前提；「每轮回执独立」替代方案需新增
# 持久化 snapshot_epoch 字段，影响分析见 B2_2_PREFLIGHT_REPORT.md §3，
# 未经批准不实现。
import pytest

from tools.relstore.manifest import compute_snapshot_fingerprint
from tools.relstore.project_registry import (
    FakeCAS, Registry, ARC_SUPERSEDED, ARC_VERIFIED_RESTORABLE)
from tools.relstore.safe_tar import create_archive
from tools.relstore.manifest import canonical_dumps


def make_reg(now_val=1000.0):
    return Registry(FakeCAS(), now=lambda: now_val)


NS = "op_b22pf_"


def _full_round_scenario():
    """fpA → SUPERSEDED(fpB) → fpB 轮 → 源变回 → SUPERSEDED(fpA) → fpA 轮。"""
    reg = make_reg()
    reg.create_project(project_id="pid_P1", alias="P1",
                       operation_id=f"{NS}create")
    # 第 1 轮：fpA
    reg.plan_archive(project_id="pid_P1", operation_id=f"{NS}plan1")
    reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fpA",
                       operation_id=f"{NS}begin1")
    # 源变化 → SUPERSEDED，权威指纹前进为 fpB
    reg.supersede_archive(project_id="pid_P1", new_fingerprint="fpB",
                          operation_id=f"{NS}supersede1")
    # 第 2 轮：fpB
    reg.plan_archive(project_id="pid_P1", operation_id=f"{NS}plan2")
    reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fpB",
                       operation_id=f"{NS}begin2")
    # 源再次变化（内容回到 fpA）→ SUPERSEDED，权威指纹回到 fpA
    reg.supersede_archive(project_id="pid_P1", new_fingerprint="fpA",
                          operation_id=f"{NS}supersede2")
    # 第 3 轮：fpA
    reg.plan_archive(project_id="pid_P1", operation_id=f"{NS}plan3")
    reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fpA",
                       operation_id=f"{NS}begin3")
    return reg


class TestSnapshotRoundBoundary:

    def test_same_fingerprint_new_round_accepts_content_addressed(self):
        """现行契约：第 1 轮 fpA 旧回执在第 3 轮（同为 fpA）被接受——
        内容寻址语义的显式固化（非疏漏）。安全前提与恢复义务见报告 §3。"""
        reg = _full_round_scenario()
        stale_round1_ref = {
            "sha256": "tar_sha_round1", "path": "b1_test/round1/synth.tar",
            "blob_sha": "blob_round1", "tree_fingerprint": "fpA",
            "verified_by": "round-1 download+unpack+tree-fp verify",
        }
        r = reg.set_verified_restorable(
            project_id="pid_P1", archive_ref=stale_round1_ref,
            restorable_level="full",
            operation_id=f"{NS}setvr_round1_receipt")
        assert r["ok"] is True and r["evict_permitted"] is False
        rec = reg.get("pid_P1")
        assert rec.archive_status == ARC_VERIFIED_RESTORABLE
        # 回执按原样落档（含第 1 轮包字节锚 sha256 与 verified_by），可审计
        assert rec.archive_ref["sha256"] == "tar_sha_round1"
        assert rec.archive_ref["blob_sha"] == "blob_round1"
        # 释放依旧不可达
        assert reg.attempt_evict(
            project_id="pid_P1",
            operation_id=f"{NS}evict_after")["permitted"] is False

    def test_different_fingerprint_receipt_still_rejected(self):
        """第 2 轮（fpB）的回执在第 3 轮（fpA）提交 → 指纹不符 → 拒绝。"""
        reg = _full_round_scenario()
        with pytest.raises(ValueError, match="R-008"):
            reg.set_verified_restorable(
                project_id="pid_P1",
                archive_ref={"sha256": "x", "tree_fingerprint": "fpB"},
                restorable_level="full",
                operation_id=f"{NS}setvr_fpB_receipt")

    def test_same_fp_does_not_imply_same_tar_bytes(self):
        """树指纹排除 manifest.payload.json，而 manifest 含 created_utc
        （打包时刻）→ 同树指纹的两轮 tar 字节不同。
        ⇒ 「同一快照」论断必须落在 树指纹（内容）+ 回执内 sha256（包字节）
        双锚点上，不能仅凭树指纹认定包同一。"""
        import tempfile, json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "src"
            (root / "scenes").mkdir(parents=True)
            (root / "project.json").write_text('{"project_id": "p"}',
                                               encoding="utf-8")
            (root / "scenes" / "shot.txt").write_text("shot\n",
                                                      encoding="utf-8")
            fp = compute_snapshot_fingerprint(root)
            t1 = create_archive(
                root, Path(td) / "r1.tar", archive_root_name="src",
                extra_files={"manifest.payload.json": canonical_dumps(
                    {"schema": "m", "created_utc": "2026-10-23T10:00:00Z"})})
            t2 = create_archive(
                root, Path(td) / "r2.tar", archive_root_name="src",
                extra_files={"manifest.payload.json": canonical_dumps(
                    {"schema": "m", "created_utc": "2026-10-23T11:00:00Z"})})
            assert t1["sha256"] != t2["sha256"]   # 包字节不同
            assert fp == compute_snapshot_fingerprint(root)  # 树指纹相同
            assert t1["sha256"] != fp and t2["sha256"] != fp

    def test_replay_same_op_id_across_rounds_at_most_once(self):
        """同一 operation_id 跨轮次重放：返回第一轮记录的原结果，
        不产生第二次写入（at-most-once 不随轮次重置）。"""
        reg = make_reg()
        reg.create_project(project_id="pid_P1", alias="P1",
                           operation_id=f"{NS}create2")
        reg.plan_archive(project_id="pid_P1", operation_id=f"{NS}plan_r1")
        reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fpA",
                           operation_id=f"{NS}begin_r1")
        ref = {"sha256": "x", "tree_fingerprint": "fpA"}
        r1 = reg.set_verified_restorable(
            project_id="pid_P1", archive_ref=ref, restorable_level="full",
            operation_id=f"{NS}setvr_shared")
        rev_after_first = reg.state.registry_revision
        # 项目取回复位后再走一轮归档（新的 begin），旧 op_id 重放
        reg.retrieve(project_id="pid_P1",
                     archive_ref={"sha256": "x", "path": "p"},
                     operation_id=f"{NS}retrieve")
        reg.plan_archive(project_id="pid_P1", operation_id=f"{NS}plan_r2")
        reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fpA",
                           operation_id=f"{NS}begin_r2")
        r2 = reg.set_verified_restorable(
            project_id="pid_P1", archive_ref=ref, restorable_level="full",
            operation_id=f"{NS}setvr_shared")
        assert r1 == r2
        # retrieve+plan+begin 各 +1；setvr 重放零写
        assert reg.state.registry_revision == rev_after_first + 3
        # dedup 中该 op 仅一条
        assert list(reg.state.operation_dedup).count(f"{NS}setvr_shared") == 1

    def test_round_boundary_state_transitions_hold(self):
        """场景中每一轮的状态机前置不被跨轮污染：
        SUPERSEDED 后可重新 plan；fpA 轮 begin 前状态为 PLANNED。"""
        reg = _full_round_scenario()
        rec = reg.get("pid_P1")
        assert rec.archive_status == "SNAPSHOTTING"
        assert rec.snapshot_fingerprint == "fpA"
        # 权威指纹历史可从 dedup 追溯（supersede1→fpB，supersede2→fpA）
        assert reg.state.operation_dedup[f"{NS}supersede1"] is not None
        assert reg.state.operation_dedup[f"{NS}supersede2"] is not None
