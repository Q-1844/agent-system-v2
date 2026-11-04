# tests/relstore/test_relstore_14_g4b1_closeout.py
# G4-B1-CLOSEOUT 收口补测：
# A. 非有效活动零提交（preview 不再消耗 B1 PUT/Git commit）；
# B. 「已标记项目重新激活」与「从 B 仓完整恢复（retrieve）」的可执行区别。
import pytest

from tools.relstore.project_registry import (
    FakeCAS, Registry, SLOT_ACTIVE, SLOT_MARKED)


def make_reg(now_val=1000.0):
    return Registry(FakeCAS(), now=lambda: now_val)


def create_n(reg, n, prefix="P"):
    for i in range(1, n + 1):
        reg.create_project(project_id=f"pid_{prefix}{i}", alias=f"{prefix}{i}")


DAY = 86400.0


# ==================== A: 非有效活动零提交 ====================

class TestNonEffectiveZeroCommit:

    def test_preview_no_cas_revision_no_commit_no_dedup(self):
        """preview：CAS revision 不变、无 Git commit、dedup 无条目。"""
        reg = make_reg()
        create_n(reg, 3)
        rev_before = reg.cas._revision
        commits_before = len(reg.cas.history)
        dedup_before = len(reg.state.operation_dedup)
        r = reg.record_activity(project_id="pid_P1", kind="preview")
        assert r["effective"] is False and r["persisted"] is False
        assert reg.cas._revision == rev_before  # 零 CAS 提交
        assert len(reg.cas.history) == commits_before  # 零 Git commit
        assert len(reg.state.operation_dedup) == dedup_before  # dedup 不膨胀

    @pytest.mark.parametrize("kind", ["preview", "browse", "heartbeat",
                                       "save_failed", "api_retry"])
    def test_all_non_effective_kinds_zero_commit(self, kind):
        reg = make_reg()
        create_n(reg, 2)
        rev_before = reg.cas._revision
        reg.record_activity(project_id="pid_P1", kind=kind)
        assert reg.cas._revision == rev_before

    def test_effective_activity_still_persists(self):
        """对照组：有效活动仍走持久化（revision +1）。"""
        reg = make_reg()
        create_n(reg, 2)
        rev_before = reg.cas._revision
        r = reg.record_activity(project_id="pid_P1", kind="save_shot")
        assert r["effective"] is True
        assert reg.cas._revision == rev_before + 1

    def test_repeated_preview_same_instance_free(self):
        """同实例重复 preview：无 dedup 背书也安全（零效果可重放）。"""
        reg = make_reg()
        create_n(reg, 2)
        rev_before = reg.cas._revision
        for _ in range(5):
            reg.record_activity(project_id="pid_P1", kind="preview")
        assert reg.cas._revision == rev_before

    def test_preview_replay_after_reload_harmless(self):
        """跨实例重放 preview op：无状态变化、无写入（无需 dedup）。"""
        cas = FakeCAS()
        reg1 = Registry(cas, now=lambda: 1000.0)
        create_n(reg1, 2)
        reg1.record_activity(project_id="pid_P1", kind="preview",
                             operation_id="op_prev_1")
        seq_before = reg1.state.activity_seq
        rev_before = cas._revision
        reg2 = Registry(cas, now=lambda: 2000.0)
        reg2.record_activity(project_id="pid_P1", kind="preview",
                             operation_id="op_prev_1")
        assert reg2.state.activity_seq == seq_before
        assert cas._revision == rev_before  # 重放零提交

    def test_raw_doc_growth_only_from_effective(self):
        """连续 preview 后 raw doc 字节数不变（对比一次有效活动）。"""
        reg = make_reg()
        create_n(reg, 2)
        size_before = len(reg.to_doc_str()) if hasattr(reg, "to_doc_str") \
            else None
        if size_before is None:
            import json
            size_before = len(json.dumps(reg.to_doc(), ensure_ascii=False))
        reg.record_activity(project_id="pid_P1", kind="preview")
        reg.record_activity(project_id="pid_P2", kind="heartbeat")
        import json
        assert len(json.dumps(reg.to_doc(), ensure_ascii=False)) == size_before


# ==================== B: 重新激活 vs 完整取回 ====================

class TestReactivationVsRetrieve:

    def _marked_project(self, reg):
        create_n(reg, 7)  # pid_P1 跌出 top-6
        assert reg.get("pid_P1").slot_status == SLOT_MARKED
        return reg.get("pid_P1")

    def test_reactivation_leaves_no_archive_trace(self):
        """重新激活 = 仅 activity_seq 重排：无 archive_ref、无重评估计时器。"""
        reg = make_reg()
        rec = self._marked_project(reg)
        reg.record_activity(project_id="pid_P1", kind="save_shot")
        rec = reg.get("pid_P1")
        assert rec.slot_status == SLOT_ACTIVE
        assert rec.archive_ref is None          # 未声称来自 B 仓
        assert rec.retrieved_reassess_at is None  # 无三天重评估计时器
        assert rec.archive_status == "NONE"

    def test_reactivation_reassess_not_applicable(self):
        """重新激活的项目不作三天重评估（不是取回件）。"""
        reg = make_reg()
        self._marked_project(reg)
        reg.record_activity(project_id="pid_P1", kind="save_shot")
        r = reg.reassess_retrieved(project_id="pid_P1", now=9000.0)
        assert r["ok"] is False and r["reason"] == "not retrieved"

    def test_retrieve_requires_archive_ref_and_sets_timer(self):
        """完整取回 = 必须携带 B 仓归档引用 + 设三天计时器 + seq 重排。"""
        reg = make_reg()
        create_n(reg, 7)
        before_top6 = {p.project_id for p in reg.active_slot_holders()}
        reg.retrieve(project_id="pid_P1",
                     archive_ref={"sha256": "ab" * 32, "verified_by": "test"})
        rec = reg.get("pid_P1")
        assert rec.slot_status in (SLOT_ACTIVE, SLOT_MARKED)  # 取回即参与排名
        assert rec.archive_ref["sha256"] == "ab" * 32  # 归档证据在手
        assert rec.retrieved_reassess_at is not None    # 三天重评估已设
        assert rec.last_activity_kind == "retrieve"
        # 取回者占据 seq → 原 top-6 有一份被挤出
        now_top6 = {p.project_id for p in reg.active_slot_holders()}
        assert "pid_P1" in now_top6
        assert len(before_top6 - now_top6) == 1

    def test_reactivation_vs_retrieve_summary(self):
        """两者唯一共同点是「回到 ACTIVE 候选」，授权来源与后续义务不同。"""
        reg = make_reg()
        self._marked_project(reg)
        # 路径 1：重新激活（本地有效活动，无 B 仓参与）
        reg.record_activity(project_id="pid_P1", kind="save_shot")
        a = reg.get("pid_P1")
        # 路径 2：完整取回（须 B 仓归档验证，调用方负责完整恢复在先）
        reg.create_project(project_id="pid_P8", alias="P8")  # 新活动把 P1 再次挤出
        reg.record_activity(project_id="pid_P8", kind="save_shot")
        reg2rec = reg.get("pid_P1")
        if reg2rec.slot_status == SLOT_MARKED:
            reg.retrieve(project_id="pid_P1",
                         archive_ref={"sha256": "cd" * 32, "verified_by": "t"})
            b = reg.get("pid_P1")
            assert b.archive_ref is not None and a.archive_ref is None
            assert b.retrieved_reassess_at is not None \
                and a.retrieved_reassess_at is None
