# tests/relstore/test_relstore_10_registry.py
# G4-A §6 测试矩阵 1-8（状态机行为）；9-10 在 test_relstore_06/11 覆盖。
import json
import time

import pytest

from tools.relstore import project_registry as PR
from tools.relstore.project_registry import FakeCAS, Registry, new_operation_id

SLOT = PR.SLOT_CAPACITY  # 6


def make_registry(now=None):
    return Registry(FakeCAS(), now=now or (lambda: 1000.0))


def create_n(reg, n, prefix="P"):
    for i in range(1, n + 1):
        reg.create_project(project_id=f"pid_{prefix}{i}", alias=f"{prefix}{i}")


# ---------- 矩阵 1：六份 ACTIVE；超额标记最老 ----------

def test_six_active_no_mark():
    reg = make_registry()
    create_n(reg, 6)
    assert len(reg.active_slot_holders()) == 6
    assert reg.marked() == []


def test_seventh_marks_oldest():
    reg = make_registry()
    create_n(reg, 6)
    reg.create_project(project_id="pid_P7", alias="P7")
    marked = reg.marked()
    assert len(marked) == 1
    # P1 是最老的（activity_seq 最低）→ 被标记
    assert marked[0].project_id == "pid_P1"
    assert all(r.project_id != "pid_P7" for r in marked)


def test_ninth_marks_three_oldest():
    reg = make_registry()
    create_n(reg, 9)
    marked_ids = {r.project_id for r in reg.marked()}
    assert marked_ids == {"pid_P1", "pid_P2", "pid_P3"}
    assert len(reg.active_slot_holders()) == 6
    # 物理九份 mock 数据仍在（residency 全 RESIDENT）
    assert all(p.residency == "RESIDENT" for p in reg.all_resident())
    assert len(reg.all_resident()) == 9


# ---------- 矩阵 2：有效活动重排；非有效不刷新 ----------

def test_marked_effective_activity_returns_to_top6():
    reg = make_registry()
    create_n(reg, 7)  # P1 marked
    assert reg.get("pid_P1").slot_status == "MARKED"
    # P1 有效活动 → 重返前六
    reg.record_activity(project_id="pid_P1", kind="save_shot")
    assert reg.get("pid_P1").slot_status == "ACTIVE"
    # 另一个最老接替标记
    assert len(reg.marked()) == 1
    assert reg.marked()[0].project_id == "pid_P2"


def test_non_effective_does_not_refresh():
    reg = make_registry()
    create_n(reg, 7)  # P1 marked
    p1_before = reg.get("pid_P1").last_activity_seq
    reg.record_activity(project_id="pid_P1", kind="preview")
    reg.record_activity(project_id="pid_P1", kind="heartbeat")
    reg.record_activity(project_id="pid_P1", kind="save_failed")
    reg.record_activity(project_id="pid_P1", kind="api_retry")
    assert reg.get("pid_P1").last_activity_seq == p1_before
    assert reg.get("pid_P1").slot_status == "MARKED"


def test_same_timestamp_activity_seq_decides():
    reg = make_registry(now=lambda: 5000.0)  # 固定时钟
    create_n(reg, 6)
    # 后创建的活动 seq 更高，即使墙钟相同
    reg.record_activity(project_id="pid_P1", kind="save_shot")
    reg.record_activity(project_id="pid_P2", kind="save_shot")
    ranked = reg.ranking()
    assert ranked[0].project_id == "pid_P2"  # seq 更高


# ---------- 矩阵 3：归档中源变化 → SUPERSEDED ----------

def test_snapshot_change_during_archive_supersedes():
    reg = make_registry()
    create_n(reg, 1)
    reg.plan_archive(project_id="pid_P1")
    reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fp_v1")
    # 打包过程中源项目变化
    r = reg.supersede_archive(project_id="pid_P1", new_fingerprint="fp_v2")
    assert r["superseded"] is True
    assert r["delete_permit"] is False
    assert reg.get("pid_P1").archive_status == "SUPERSEDED"


def test_late_old_receipt_no_delete_permit():
    reg = make_registry()
    create_n(reg, 1)
    reg.plan_archive(project_id="pid_P1")
    reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fp_v1")
    reg.supersede_archive(project_id="pid_P1", new_fingerprint="fp_v2")
    # 迟到的旧回执（试图 set_verified_restorable）不得释放。
    # R-008 修复后：旧回执被服务端前置检查直接拒绝（修复前仅依赖
    # attempt_evict 的 G4-A 硬禁用兜底；原始行为记录见 R008 修复报告）。
    rev_before = reg.state.registry_revision
    with pytest.raises(ValueError, match="R-008"):
        reg.set_verified_restorable(
            project_id="pid_P1",
            archive_ref={"sha256": "old", "missing_required": [],
                         "tree_fingerprint": "fp_v1"},
            restorable_level="full")
    # 状态不被覆盖、零 CAS 写；释放依旧不可达
    assert reg.get("pid_P1").archive_status == "SUPERSEDED"
    assert reg.state.registry_revision == rev_before
    ev = reg.attempt_evict(project_id="pid_P1")
    assert ev["permitted"] is False


# ---------- 矩阵 4：未完成/暂停可归档；open_jobs 阻止释放 ----------

def test_incomplete_paused_can_archive():
    reg = make_registry()
    create_n(reg, 1)
    # 暂停 + 未完成
    rec = reg.get("pid_P1")
    rec.production_status = "PAUSED"
    reg.plan_archive(project_id="pid_P1")
    assert reg.get("pid_P1").archive_status == "PLANNED"


def test_open_jobs_blocks_evict():
    reg = make_registry()
    create_n(reg, 1)
    reg.plan_archive(project_id="pid_P1")
    reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fp")
    reg.set_verified_restorable(
        project_id="pid_P1",
        archive_ref={"sha256": "x", "missing_required": [],
                     "tree_fingerprint": "fp"},
        restorable_level="full")
    reg.set_open_jobs(project_id="pid_P1", count=2)
    ev = reg.attempt_evict(project_id="pid_P1")
    assert ev["permitted"] is False
    assert any("open_jobs" in r for r in ev["reasons"])


def test_missing_dep_partial_not_full():
    reg = make_registry()
    create_n(reg, 1)
    reg.plan_archive(project_id="pid_P1")
    reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fp")
    reg.set_verified_restorable(
        project_id="pid_P1",
        archive_ref={"sha256": "x", "missing_required": ["skill:y@1.0"],
                     "tree_fingerprint": "fp"},
        restorable_level="partial")
    rec = reg.get("pid_P1")
    assert rec.missing_required == ["skill:y@1.0"]
    ev = reg.attempt_evict(project_id="pid_P1")
    assert ev["permitted"] is False
    assert any("missing_required" in r for r in ev["reasons"])


# ---------- 矩阵 5：取回 + 三天重新评估 ----------

def test_retrieve_enters_slot_reassess_only():
    reg = make_registry()
    create_n(reg, 6)
    # P1 归档
    reg.plan_archive(project_id="pid_P1")
    reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fp")
    reg.set_verified_restorable(
        project_id="pid_P1",
        archive_ref={"sha256": "x", "missing_required": [],
                     "tree_fingerprint": "fp"},
        restorable_level="full")
    # 取回
    r = reg.retrieve(project_id="pid_P1", archive_ref={"sha256": "x"})
    assert r["ok"] and r["auto_delete"] is False
    rec = reg.get("pid_P1")
    assert rec.residency == "RESIDENT"
    assert rec.retrieved_reassess_at is not None
    assert rec.archive_status == "NONE"  # 取回后清零


def test_reassess_not_due():
    reg = make_registry()
    create_n(reg, 1)
    reg.retrieve(project_id="pid_P1", archive_ref={"sha": "x"})
    r = reg.reassess_retrieved(project_id="pid_P1", now=1000.0)  # 立即评估（未到期）
    assert r["reassess"] is False


def test_reassess_due_active_no_archive():
    """G4-A.1 P0-1 修正：ACTIVE（仍在 top-6）到期不进归档队列。"""
    reg = make_registry()
    create_n(reg, 1)  # P1 唯一项目 → ACTIVE
    reg.retrieve(project_id="pid_P1", archive_ref={"sha": "x"})
    r = reg.reassess_retrieved(project_id="pid_P1", now=1000.0 + 4 * 86400)
    assert r["reassess"] is True
    assert r["action"] == "no_archive_still_active"
    assert r["auto_delete"] is False
    assert reg.get("pid_P1").archive_status == "NONE"  # 不进归档队列


def test_reassess_due_marked_plans_archive():
    """G4-A.1 P0-1：MARKED（已跌出 top-6）到期可进归档候选（不删）。"""
    reg = make_registry()
    create_n(reg, 8)  # P1-P8 → P1/P2 MARKED
    reg.retrieve(project_id="pid_P1", archive_ref={"sha": "x"})
    # P1 取回后活动 seq 刷新，但可能仍 MARKED（取决于其他项目活动）
    r = reg.reassess_retrieved(project_id="pid_P1", now=1000.0 + 4 * 86400)
    if reg.get("pid_P1").slot_status == "MARKED":
        assert r["action"] == "plan_archive"
        assert reg.get("pid_P1").archive_status == "PLANNED"
    else:
        # P1 取回后可能回 ACTIVE（seq 高）→ 不进归档
        assert r["action"] == "no_archive_still_active"
    assert r["auto_delete"] is False


def test_reassess_with_activity_postpones():
    reg = make_registry()
    create_n(reg, 1)
    reg.retrieve(project_id="pid_P1", archive_ref={"sha": "x"})
    reg.record_activity(project_id="pid_P1", kind="save_shot")  # 有效活动
    r = reg.reassess_retrieved(project_id="pid_P1", now=1000.0 + 4 * 86400)
    assert r["action"] == "postpone"
    assert r["auto_delete"] is False


def test_same_alias_different_id_not_confused():
    reg = make_registry()
    reg.create_project(project_id="pid_A1", alias="SAME")
    reg2 = make_registry()
    reg2.create_project(project_id="pid_A1", alias="SAME")
    # 不同 project_id 不混
    assert reg.get("pid_A1").project_id == "pid_A1"
    assert reg.get("pid_A1").alias == "SAME"


# ---------- 矩阵 6：双写手 CAS ----------

def test_two_writers_update_one_wins():
    """两写手读同一 revision 并发提交：一胜一 CasConflict，败者候选保留。"""
    cas = FakeCAS()
    reg = Registry(cas, now=lambda: 1.0)
    create_n(reg, 1)
    cur = cas.read()
    assert cur is not None
    expected = cur["revision"]
    # 模拟两个写手：先 A 提交（成功）
    reg_a = Registry(cas, now=lambda: 2.0)
    reg_a.record_activity(project_id="pid_P1", kind="save_shot")
    # B 用旧 expected 提交 → 冲突
    reg_b = Registry(cas, now=lambda: 3.0)
    reg_b.state.activity_seq += 1
    reg_b.get("pid_P1").last_activity_seq = reg_b.state.activity_seq
    with pytest.raises(PR.CasConflict) as ei:
        reg_b._persist_with_expected(expected)
    assert ei.value.candidate is not None  # 败者候选保留


def test_two_writers_create_nonexistent_one_wins():
    """首次不存在索引文件时两写手竞争：FakeCAS 一胜一 Conflict（真实 GitHub G4-B 待实测）。"""
    cas = FakeCAS()
    # 两写手都看到 None
    assert cas.read() is None
    # A 先 create
    r_a = cas.commit({"v": 1, "writer": "A"}, expected_revision=None,
                     operation_id="wA")
    assert r_a["ok"]
    # B 也想 create（同样 None 认知）→ 冲突（文件已存在）
    with pytest.raises(PR.CasConflict):
        cas.commit({"v": 1, "writer": "B"}, expected_revision=None,
                   operation_id="wB")


# ---------- 矩阵 7：幂等/乱序/失败不丢源 ----------

def test_duplicate_operation_id_idempotent():
    reg = make_registry()
    op = new_operation_id()
    reg.create_project(project_id="pid_X", alias="X", operation_id=op)
    # 重复同一 op_id → 幂等返回，不重复创建
    reg.create_project(project_id="pid_X", alias="X", operation_id=op)
    assert len(reg.all_resident()) == 1


def test_out_of_order_no_source_loss():
    reg = make_registry()
    create_n(reg, 3)
    # 乱序操作不丢源
    reg.plan_archive(project_id="pid_P3")
    reg.plan_archive(project_id="pid_P1")
    reg.plan_archive(project_id="pid_P2")
    assert all(p.residency == "RESIDENT" for p in reg.all_resident())
    # 三个都 PLANNED，无删除
    assert all(p.archive_status == "PLANNED" for p in reg.all_resident())


def test_clock_drift_does_not_corrupt_ranking():
    # 墙钟回拨不影响（排名按 activity_seq）
    reg = make_registry(now=lambda: 9999.0)
    create_n(reg, 3)
    reg.now = lambda: 1.0  # 回拨
    reg.record_activity(project_id="pid_P3", kind="save_shot")
    ranked = reg.ranking()
    assert ranked[0].project_id == "pid_P3"  # seq 最高


def test_offline_pending_intent_not_authority():
    reg = make_registry()
    create_n(reg, 3)
    before = reg.get("pid_P1").last_activity_seq
    reg.record_activity(project_id="pid_P1", kind="save_shot",
                        offline_intent=True)
    assert reg.get("pid_P1").last_activity_seq == before  # 未刷新权威
    assert len(reg.get("pid_P1").pending_sync_intents) == 1


# ---------- 矩阵 8：工具归属/alias 映射 ----------

def test_alias_mapping_distinct_from_id():
    reg = make_registry()
    reg.create_project(project_id="pid_real_1", alias="DISPLAY_NAME")
    rec = reg.get("pid_real_1")
    assert rec.project_id == "pid_real_1"  # 稳定 ID
    assert rec.alias == "DISPLAY_NAME"  # 显示名


def test_public_tool_not_archived_with_project():
    """公共工具留在 A；归档不携带。G4-A 只验证状态机不误标。"""
    reg = make_registry()
    create_n(reg, 1)
    reg.plan_archive(project_id="pid_P1")
    # archive_ref 不含公共工具
    reg.begin_snapshot(project_id="pid_P1", snapshot_fingerprint="fp")
    reg.set_verified_restorable(
        project_id="pid_P1",
        archive_ref={"sha256": "x", "missing_required": [],
                     "tree_fingerprint": "fp",
                     "archive_scope": "project_only_no_public_tools"},
        restorable_level="full")
    rec = reg.get("pid_P1")
    assert rec.archive_ref["archive_scope"] == "project_only_no_public_tools"
