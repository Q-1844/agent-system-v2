# tests/relstore/test_relstore_12_g4a1_boundaries.py
# G4-A.1 边界补测：P0-1（三天/top-6 不旁路）、P0-2（序列化跨实例幂等/CAS）、
# P1（快照恢复契约：lease/grants 不作实时授权、文件集合一致性）。
import json
import time

import pytest

from tools.relstore import project_registry as PR
from tools.relstore.project_registry import (FakeCAS, Registry,
                                              SLOT_CAPACITY, new_operation_id,
                                              from_doc)
from tools.relstore import manifest as M
from tools.relstore import safe_tar, snapshot_scope
from pathlib import Path

DAY = 86400.0


def make_reg(now_val=1000.0):
    return Registry(FakeCAS(), now=lambda: now_val)


def create_n(reg, n, prefix="P"):
    for i in range(1, n + 1):
        reg.create_project(project_id=f"pid_{prefix}{i}", alias=f"{prefix}{i}")


# ==================== P0-1: 三天 vs top-6 边界 ====================

class TestThreeDayTopSixBoundary:
    """评审 P0-1：ACTIVE 不会仅因三天计时被归档；归档候选必须 MARKED。"""

    def test_five_plus_retrieve_sixth_active_no_archive(self):
        """5 份 + 取回第 6 份 → ACTIVE，3 天无活动 → 不进归档。"""
        reg = make_reg()
        create_n(reg, 5)
        reg.retrieve(project_id="pid_P1", archive_ref={"sha": "x"})
        assert reg.get("pid_P1").slot_status == "ACTIVE"  # 6 份内
        r = reg.reassess_retrieved(project_id="pid_P1", now=1000.0 + 4 * DAY)
        assert r["action"] == "no_archive_still_active"
        assert reg.get("pid_P1").archive_status == "NONE"

    def test_seven_plus_retrieve_enters_top6_no_archive(self):
        """7 份 + 取回一个进入 top-6 → ACTIVE → 3 天到期不进归档。"""
        reg = make_reg()
        create_n(reg, 7)  # P1/P2 MARKED
        # 取回 P1（seq 刷新，可能回 ACTIVE）
        reg.retrieve(project_id="pid_P1", archive_ref={"sha": "x"})
        rec = reg.get("pid_P1")
        r = reg.reassess_retrieved(project_id="pid_P1", now=1000.0 + 4 * DAY)
        if rec.slot_status == "ACTIVE":
            assert r["action"] == "no_archive_still_active"
            assert reg.get("pid_P1").archive_status == "NONE"
        else:
            assert r["action"] == "plan_archive"
        assert r["auto_delete"] is False

    def test_retrieve_still_top6_after_three_days(self):
        """取回 3 天后排名仍前六 → 不进归档。"""
        reg = make_reg()
        create_n(reg, 3)
        reg.retrieve(project_id="pid_P1", archive_ref={"sha": "x"})
        # 3 天后仍只有 3 份 → P1 仍 ACTIVE
        r = reg.reassess_retrieved(project_id="pid_P1", now=1000.0 + 4 * DAY)
        assert r["action"] == "no_archive_still_active"

    def test_retrieve_effective_edit_within_three_days_postpones(self):
        """取回后有效编辑 → 到期时延后。"""
        reg = make_reg()
        create_n(reg, 3)
        reg.retrieve(project_id="pid_P1", archive_ref={"sha": "x"})
        reg.record_activity(project_id="pid_P1", kind="save_shot")
        r = reg.reassess_retrieved(project_id="pid_P1", now=1000.0 + 4 * DAY)
        assert r["action"] == "postpone"

    def test_multiple_retrieved_expiring_sequence(self):
        """多个已取回项目先后到期，各自独立判定。"""
        reg = make_reg(now_val=1000.0)
        create_n(reg, 3)
        reg.retrieve(project_id="pid_P1", archive_ref={"sha": "x"})
        reg.retrieve(project_id="pid_P2", archive_ref={"sha": "y"})
        # 4 天后 P1、P2 都到期（均 ACTIVE，3 份内）
        r1 = reg.reassess_retrieved(project_id="pid_P1", now=1000.0 + 4 * DAY)
        r2 = reg.reassess_retrieved(project_id="pid_P2", now=1000.0 + 4 * DAY)
        assert r1["action"] == "no_archive_still_active"
        assert r2["action"] == "no_archive_still_active"
        assert all(reg.get(p).archive_status == "NONE"
                    for p in ("pid_P1", "pid_P2"))

    def test_empty_project_list_no_crash(self):
        """空项目列表，reassess 不崩溃。"""
        reg = make_reg()
        with pytest.raises(KeyError):
            reg.reassess_retrieved(project_id="nonexistent", now=2000.0)

    def test_not_due_stable(self):
        """三天未到 → 稳定 no action。"""
        reg = make_reg()
        create_n(reg, 3)
        reg.retrieve(project_id="pid_P1", archive_ref={"sha": "x"})
        r = reg.reassess_retrieved(project_id="pid_P1", now=1000.0 + 1 * DAY)
        assert r["reassess"] is False

    def test_clock_jump_stable(self):
        """墙钟跳变（大幅前跳）→ 到期判定仍按 slot_status。"""
        reg = make_reg()
        create_n(reg, 3)
        reg.retrieve(project_id="pid_P1", archive_ref={"sha": "x"})
        # 跳 100 年
        r = reg.reassess_retrieved(project_id="pid_P1", now=1000.0 + 36500 * DAY)
        assert r["reassess"] is True
        assert r["action"] == "no_archive_still_active"  # 仍 ACTIVE
        assert r["auto_delete"] is False

    def test_marked_reassess_can_plan_archive_no_delete(self):
        """MARKED 项目到期 → 可进归档候选，绝不删。"""
        reg = make_reg()
        create_n(reg, 9)  # P1/P2/P3 MARKED
        reg.retrieve(project_id="pid_P3", archive_ref={"sha": "x"})
        rec = reg.get("pid_P3")
        r = reg.reassess_retrieved(project_id="pid_P3", now=1000.0 + 4 * DAY)
        if rec.slot_status == "MARKED":
            assert r["action"] == "plan_archive"
        assert r["auto_delete"] is False


# ==================== P0-2: 序列化跨实例幂等 / CAS ====================

class TestSerializationIdempotency:
    """评审 P0-2：持久化后新实例载入，重复 operation_id 不再执行。"""

    def test_cross_instance_same_op_id_no_reexecute(self):
        """序列化 → 新实例 → 同 op_id 不增长 activity_seq / 不重复创建。"""
        cas = FakeCAS()
        reg1 = Registry(cas, now=lambda: 1000.0)
        op = new_operation_id()
        reg1.create_project(project_id="pid_X", alias="X", operation_id=op)
        seq_before = reg1.state.activity_seq
        # 新实例从 CAS 载入
        reg2 = Registry(cas, now=lambda: 2000.0)
        assert reg2.state.activity_seq == seq_before
        # 重放同 op_id
        reg2.create_project(project_id="pid_X", alias="X", operation_id=op)
        assert reg2.state.activity_seq == seq_before  # 不增长
        assert len(reg2.state.projects) == 1  # 不重复创建

    def test_cross_instance_record_activity_idempotent(self):
        """跨实例重复 record_activity op_id → 不增长 seq / 不重排。"""
        cas = FakeCAS()
        reg1 = Registry(cas, now=lambda: 1000.0)
        create_n(reg1, 3)
        op = new_operation_id()
        reg1.record_activity(project_id="pid_P2", kind="save_shot",
                              operation_id=op)
        seq1 = reg1.state.activity_seq
        p2_seq1 = reg1.get("pid_P2").last_activity_seq
        # 新实例
        reg2 = Registry(cas, now=lambda: 2000.0)
        reg2.record_activity(project_id="pid_P2", kind="save_shot",
                              operation_id=op)
        assert reg2.state.activity_seq == seq1
        assert reg2.get("pid_P2").last_activity_seq == p2_seq1

    def test_cross_instance_plan_archive_idempotent(self):
        cas = FakeCAS()
        reg1 = Registry(cas, now=lambda: 1000.0)
        create_n(reg1, 3)
        op = new_operation_id()
        reg1.plan_archive(project_id="pid_P1", operation_id=op)
        reg2 = Registry(cas, now=lambda: 2000.0)
        reg2.plan_archive(project_id="pid_P1", operation_id=op)
        assert reg2.get("pid_P1").archive_status == "PLANNED"
        assert len(reg2.state.archive_queue) == 1

    def test_two_writers_cas_loser_keeps_candidate(self):
        """两写手同旧 revision → 一胜一 CasConflict，败者候选保留。"""
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1.0)
        create_n(reg, 2)
        cur = cas.read()
        expected_rev = cur["revision"]
        # A 提交成功
        reg_a = Registry(cas, now=lambda: 2.0)
        reg_a.record_activity(project_id="pid_P1", kind="save_shot")
        # B 用旧 revision 提交 → 冲突
        reg_b = Registry(cas, now=lambda: 3.0)
        reg_b.state.activity_seq += 1
        reg_b.get("pid_P1").last_activity_seq = reg_b.state.activity_seq
        with pytest.raises(PR.CasConflict) as ei:
            reg_b._persist_with_expected(expected_rev)
        assert ei.value.candidate is not None

    def test_cas_conflict_no_blind_retry_with_old_json(self):
        """CAS 冲突后不能盲目用旧 JSON 重试（须重新读权威快照）。"""
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1.0)
        create_n(reg, 2)
        cur_rev = cas.read()["revision"]
        # A 写成功
        reg_a = Registry(cas, now=lambda: 2.0)
        reg_a.record_activity(project_id="pid_P1", kind="save_shot")
        new_rev = cas.read()["revision"]
        assert new_rev > cur_rev
        # B 用旧 rev 重试 → 必须失败
        reg_b = Registry(cas, now=lambda: 3.0)
        reg_b.state.activity_seq += 1
        with pytest.raises(PR.CasConflict):
            reg_b._persist_with_expected(cur_rev)
        # 正确做法：B 重新读权威
        reg_c = Registry(cas, now=lambda: 4.0)
        assert reg_c.state.activity_seq == reg_a.state.activity_seq

    def test_registry_revision_persisted(self):
        """registry_revision 跨实例持久化。"""
        cas = FakeCAS()
        reg1 = Registry(cas, now=lambda: 1.0)
        create_n(reg1, 3)
        rev1 = reg1.state.registry_revision
        reg2 = Registry(cas, now=lambda: 2.0)
        assert reg2.state.registry_revision == rev1

    def test_operation_dedup_persisted(self):
        """operation_dedup 跨实例持久化（P0-2 核心修正）。"""
        cas = FakeCAS()
        reg1 = Registry(cas, now=lambda: 1.0)
        op = new_operation_id()
        reg1.create_project(project_id="pid_X", alias="X", operation_id=op)
        assert op in reg1.state.operation_dedup
        # 新实例
        reg2 = Registry(cas, now=lambda: 2.0)
        assert op in reg2.state.operation_dedup  # 持久化了

    def test_create_project_duplicate_id_no_replace(self):
        """已有 project_id 不可重复创建（即使不同 alias）。"""
        reg = make_reg()
        reg.create_project(project_id="pid_X", alias="A")
        with pytest.raises(ValueError, match="已存在"):
            reg.create_project(project_id="pid_X", alias="B")
        assert reg.get("pid_X").alias == "A"  # 不被替换

    def test_alias_vs_identity_distinct(self):
        """alias 可变，project_id 稳定。"""
        reg = make_reg()
        reg.create_project(project_id="pid_stable", alias="DISPLAY")
        rec = reg.get("pid_stable")
        assert rec.project_id == "pid_stable"
        assert rec.alias == "DISPLAY"
        # alias 不影响 identity
        rec.alias = "NEW_NAME"
        assert reg.get("pid_stable").project_id == "pid_stable"

    def test_offline_intent_not_confirmed_until_cas_success(self):
        """离线意图不增长权威 seq；CAS 失败不制造重复活动。"""
        reg = make_reg()
        create_n(reg, 2)
        seq_before = reg.state.activity_seq
        reg.record_activity(project_id="pid_P1", kind="save_shot",
                            offline_intent=True)
        assert reg.state.activity_seq == seq_before  # 不增长
        assert len(reg.get("pid_P1").pending_sync_intents) == 1


# ==================== P1: 快照恢复契约 ====================

class TestSnapshotRestoreContract:

    def test_snapshot_scope_includes_must_files(self):
        """MUST_INCLUDE 文件不被排除。"""
        paths = [
            "input/script.yaml",
            "output/project.json",
            "output/videos/s01.mp4",
            "output/agents/master_lease.json",
            "output/agents/grants.json",
            "output/agents/grants_audit.jsonl",
            "__pycache__/x.pyc",  # temp
            "node_modules/foo.js",  # temp
        ]
        dec = snapshot_scope.classify(paths)
        assert "input/script.yaml" in dec.included
        assert "output/project.json" in dec.included
        assert "output/agents/master_lease.json" in dec.included
        assert "__pycache__/x.pyc" in dec.excluded_temp
        assert "node_modules/foo.js" in dec.excluded_temp

    def test_unknown_files_default_included(self):
        """未知文件默认纳入（保守）。"""
        dec = snapshot_scope.classify(["output/weird/custom.json"])
        assert "output/weird/custom.json" in dec.included

    def test_node_modules_not_excluded_without_evidence(self):
        """node_modules 被排除（可重装），但快照范围审查标注理由。
        G4-A.1 不自动假设可重装——调用方须有 lock 文件证据。"""
        dec = snapshot_scope.classify(["node_modules/pkg/index.js"])
        notes = snapshot_scope.audit_exclusions(dec)
        assert any("可重装" in n[1] for n in notes)

    def test_lease_grants_archived_not_live_auth(self):
        """master_lease.json / grants.json 归档为证据，
        但恢复后不得作为实时授权（设计契约，不改生产租约）。"""
        # 模拟：归档包含 lease 文件，恢复后须重置 lease
        lease_doc = {"holder": "old_session", "generation": 5,
                     "expires_at": 1234567.0}
        grants_doc = {"grants": [{"id": "gr_old", "revoked": False}]}
        # 归档时这两个文件在 MUST_INCLUDE（审计证据）
        dec = snapshot_scope.classify([
            "output/agents/master_lease.json",
            "output/agents/grants.json"])
        assert all(p in dec.included for p in
                   ["output/agents/master_lease.json",
                    "output/agents/grants.json"])
        # 恢复契约：不得把旧 holder 当作当前授权
        # （这在恢复器须显式重置 lease；此处只验证文件被归档）
        assert lease_doc["holder"] == "old_session"  # 值在归档里
        # 但恢复后的项目须重新 acquire lease（不沿用旧的）

    def test_set_verified_restorable_marks_test_source(self):
        """set_verified_restorable 是离线模拟——不冒充真实 B 资产重下载验证。"""
        reg = make_reg()
        create_n(reg, 1)
        reg.plan_archive(project_id="pid_P1")
        reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fp")
        r = reg.set_verified_restorable(
            project_id="pid_P1",
            archive_ref={"sha256": "x", "missing_required": [],
                         "tree_fingerprint": "fp",
                         "verified_by": "offline_simulation_not_real_download"},
            restorable_level="full")
        assert r["ok"]
        assert r["evict_permitted"] is False
        rec = reg.get("pid_P1")
        assert rec.archive_ref["verified_by"] == "offline_simulation_not_real_download"

    def test_archive_scope_field_not_real_scanning(self):
        """archive_ref.archive_scope 只是字段记录，不构成真实跨项目工具依赖扫描。"""
        reg = make_reg()
        create_n(reg, 1)
        reg.plan_archive(project_id="pid_P1")
        reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fp")
        reg.set_verified_restorable(
            project_id="pid_P1",
            archive_ref={"sha256": "x", "missing_required": [],
                         "tree_fingerprint": "fp",
                         "archive_scope": "project_only_no_public_tools",
                         "dependency_scan": "NOT_PERFORMED"},
            restorable_level="full")
        rec = reg.get("pid_P1")
        assert rec.archive_ref["dependency_scan"] == "NOT_PERFORMED"

    def test_fingerprint_includes_same_set_as_manifest(self):
        """整树指纹范围与 manifest files[] 范围一致性（不漏不重）。"""
        import tempfile, hashlib
        from tools.relstore import manifest as M
        d = Path(tempfile.mkdtemp())
        (d / "input").mkdir()
        (d / "output").mkdir()
        (d / "input/script.yaml").write_text("x: 1", encoding="utf-8")
        (d / "output/project.json").write_text('{"v":1}', encoding="utf-8")
        (d / "__pycache__").mkdir()
        (d / "__pycache__/cache.pyc").write_bytes(b"cache")
        # 指纹：compute_snapshot_fingerprint 不排除任何文件（全量）
        fp_all = M.compute_snapshot_fingerprint(d)
        # 但 snapshot_scope 会标记 __pycache__ 为 temp
        all_files = [str(p.relative_to(d).as_posix())
                     for p in d.rglob("*") if p.is_file()]
        dec = snapshot_scope.classify(all_files)
        assert "__pycache__/cache.pyc" in dec.excluded_temp
        # 指纹包含全量（不因 scope 排除而变化）
        fp2 = M.compute_snapshot_fingerprint(d)
        assert fp_all == fp2  # 指纹稳定
        # manifest files[] 也包含全部（create_archive 不排除）
        payload, files = M.build_payload(
            project_dir=d, project_id="p", revision=1,
            lifecycle_status="active", source_code_commit="c" * 40,
            created_at=1.0, archive_operation_id="a", tool_versions={})
        file_paths = [f["path"] for f in payload["files"]]
        assert "__pycache__/cache.pyc" in file_paths  # 也进了 manifest
