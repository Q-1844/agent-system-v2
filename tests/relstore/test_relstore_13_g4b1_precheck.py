# tests/relstore/test_relstore_13_g4b1_precheck.py
# G4-B1 本地预检（评审决议两个 P0 补核，2026-10-22）：
# A. 冲突后内存污染：失败不被当成成功、候选保留、全新实例重载重判、禁止旧 JSON 换新 SHA 盲重试；
# B. 原始文档 revision：CAS 成功后直读原始序列化 JSON（不经修复性 load），
#    registry_revision 与真实 CAS 版本一致、operation_dedup 含最新 operation_id、
#    activity_seq 与项目排名同版本。
import pytest

from tools.relstore import project_registry as PR
from tools.relstore.project_registry import (FakeCAS, Registry,
                                              from_doc, new_operation_id)


def create_n(reg, n, prefix="P"):
    for i in range(1, n + 1):
        reg.create_project(project_id=f"pid_{prefix}{i}", alias=f"{prefix}{i}")


# ==================== P0-A: 冲突后内存卫生 ====================

class TestConflictMemoryHygiene:
    """同实例冲突后：异常浮出、权威未动、失败不当成功、重载重判、盲重试被拒。"""

    def _two_writers(self):
        cas = FakeCAS()
        reg_a = Registry(cas, now=lambda: 1000.0)
        create_n(reg_a, 2)
        reg_b = Registry(cas, now=lambda: 2000.0)  # 与 A 同一权威快照载入
        return cas, reg_a, reg_b

    def test_conflict_raises_out_of_public_api(self):
        """公开 API 内 CAS 冲突必须以 CasConflict 浮出，不得静默返回成功。"""
        cas, reg_a, reg_b = self._two_writers()
        reg_a.record_activity(project_id="pid_P1", kind="save_shot")
        with pytest.raises(PR.CasConflict):
            reg_b.record_activity(project_id="pid_P2", kind="save_shot",
                                  operation_id="op_b1")

    def test_authority_untouched_by_loser(self):
        """败者失败后：CAS 权威 revision/activity_seq/dedup 均未被败者改动。"""
        cas, reg_a, reg_b = self._two_writers()
        before = cas.read()
        seq_authority = before["doc"]["activity_seq"]
        rev_authority = before["revision"]
        reg_a.record_activity(project_id="pid_P1", kind="save_shot")
        rev_after_a = cas.read()["revision"]
        assert rev_after_a == rev_authority + 1
        with pytest.raises(PR.CasConflict):
            reg_b.record_activity(project_id="pid_P2", kind="save_shot",
                                  operation_id="op_b1")
        after = cas.read()
        assert after["revision"] == rev_after_a  # 未被静默覆盖
        # 败者内存非权威：同一 seq 数值在权威记在 P1 头上，败者内存记在 P2 头上
        # （毒化实例 get 已 fail-closed；事后取证走原始 state 字段）
        assert after["doc"]["activity_seq"] == reg_b.state.activity_seq  # 数值巧合相等
        assert after["doc"]["projects"]["pid_P2"]["last_activity_seq"] < \
            reg_b.state.projects["pid_P2"].last_activity_seq  # 权威里 P2 未刷新
        assert "op_b1" not in after["doc"]["operation_dedup"]

    def test_loser_memory_diverged_but_candidate_preserved(self):
        """失败实例内存确已分叉（记录在案，证明不可复用）；候选完整保留在异常中。"""
        cas, reg_a, reg_b = self._two_writers()
        seq_before_b = reg_b.state.activity_seq
        reg_a.record_activity(project_id="pid_P1", kind="save_shot")
        with pytest.raises(PR.CasConflict) as ei:
            reg_b.record_activity(project_id="pid_P2", kind="save_shot",
                                  operation_id="op_b1")
        # 失败实例内存已含未确认变更（这正是必须丢弃实例的原因）
        assert reg_b.state.activity_seq == seq_before_b + 1
        assert "op_b1" in reg_b.state.operation_dedup
        # 候选保留且含该操作意图
        cand = ei.value.candidate
        assert cand is not None
        assert "op_b1" in cand["operation_dedup"]
        assert ei.value.expected == reg_b._cas_revision  # 旧 revision，未伪造新 SHA

    def test_failed_instance_replay_not_treated_as_confirmed(self):
        """失败实例重放同 operation_id 不得返回“已确认成功”（毒化防护）。"""
        cas, reg_a, reg_b = self._two_writers()
        reg_a.record_activity(project_id="pid_P1", kind="save_shot")
        with pytest.raises(PR.CasConflict):
            reg_b.record_activity(project_id="pid_P2", kind="save_shot",
                                  operation_id="op_b1")
        with pytest.raises(PR.RegistryPoisoned):
            reg_b.record_activity(project_id="pid_P2", kind="save_shot",
                                  operation_id="op_b1")
        with pytest.raises(PR.RegistryPoisoned):
            reg_b.record_activity(project_id="pid_P2", kind="save_shot")  # 无 op 亦拒绝
        with pytest.raises(PR.RegistryPoisoned):
            reg_b.create_project(project_id="pid_NEW", alias="NEW")  # 一切写拒绝

    def test_fresh_reload_rejudges_and_replays_once(self):
        """全新 Registry 从权威重载：失败操作可安全重放且只执行一次。"""
        cas, reg_a, reg_b = self._two_writers()
        op = "op_b1"
        reg_a.record_activity(project_id="pid_P1", kind="save_shot")
        with pytest.raises(PR.CasConflict):
            reg_b.record_activity(project_id="pid_P2", kind="save_shot",
                                  operation_id=op)
        seq_authority = cas.read()["doc"]["activity_seq"]
        reg_c = Registry(cas, now=lambda: 3000.0)
        assert reg_c.state.activity_seq == seq_authority
        assert "op_b1" not in reg_c.state.operation_dedup
        r = reg_c.record_activity(project_id="pid_P2", kind="save_shot",
                                  operation_id=op)
        assert r["effective"] is True
        assert reg_c.state.activity_seq == seq_authority + 1  # 只执行一次
        # 幂等重放：不再执行
        reg_c.record_activity(project_id="pid_P2", kind="save_shot",
                              operation_id=op)
        assert reg_c.state.activity_seq == seq_authority + 1

    def test_blind_retry_cannot_overwrite_authority(self):
        """失败实例盲重试（旧 JSON 旧 revision）无法覆盖权威新版本。"""
        cas, reg_a, reg_b = self._two_writers()
        reg_a.record_activity(project_id="pid_P1", kind="save_shot")
        rev_authority = cas.read()["revision"]
        doc_authority = dict(cas.read()["doc"]["projects"]["pid_P1"])
        with pytest.raises(PR.CasConflict):
            reg_b.record_activity(project_id="pid_P2", kind="save_shot")
        with pytest.raises(PR.RegistryPoisoned):
            reg_b.record_activity(project_id="pid_P2", kind="save_shot",
                                  operation_id="op_b2")
        after = cas.read()
        assert after["revision"] == rev_authority
        assert after["doc"]["projects"]["pid_P1"] == doc_authority

    def test_conflict_then_win_via_fresh_judgment(self):
        """败者丢弃实例→重载→重判→提交成功：完整恢复路径。"""
        cas, reg_a, reg_b = self._two_writers()
        reg_a.record_activity(project_id="pid_P1", kind="save_shot")
        with pytest.raises(PR.CasConflict):
            reg_b.record_activity(project_id="pid_P2", kind="save_shot",
                                  operation_id="op_b1")
        reg_c = Registry(cas, now=lambda: 3000.0)
        r = reg_c.record_activity(project_id="pid_P2", kind="save_shot",
                                  operation_id="op_b1")
        assert r["effective"] is True
        cur = cas.read()
        assert cur["doc"]["registry_revision"] == cur["revision"]
        assert cur["doc"]["operation_dedup"]["op_b1"]["result"]["effective"] is True


# ==================== P0-B: 原始文档 revision 一致性 ====================

class TestRawDocumentRevision:
    """CAS 成功后直读原始序列化 JSON（cas.read()，不经修复性 load）验证版本。"""

    def test_first_create_raw_doc_revision_is_1(self):
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1000.0)
        reg.create_project(project_id="pid_X", alias="X", operation_id="op1")
        cur = cas.read()
        assert cur["revision"] == 1
        assert cur["doc"]["registry_revision"] == 1

    def test_update_raw_doc_revision_matches_cas(self):
        """更新后原始 JSON 的 registry_revision == 真实 CAS revision（不落后一步）。"""
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1000.0)
        create_n(reg, 2)
        op = new_operation_id()
        reg.record_activity(project_id="pid_P1", kind="save_shot",
                            operation_id=op)
        cur = cas.read()
        assert cur["doc"]["registry_revision"] == cur["revision"]

    def test_raw_doc_activity_seq_ranking_same_version(self):
        """同一提交内：activity_seq、项目 last_activity_seq、dedup 表同版本。"""
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1000.0)
        create_n(reg, 2)
        op = new_operation_id()
        reg.record_activity(project_id="pid_P1", kind="save_shot",
                            operation_id=op)
        raw = cas.read()["doc"]
        assert raw["operation_dedup"].get(op) is not None
        assert raw["activity_seq"] == raw["projects"]["pid_P1"]["last_activity_seq"]
        assert raw["activity_seq"] == reg.state.activity_seq

    def test_reload_uses_raw_revision_without_override(self):
        """from_doc 直读原始 JSON 已得正确版本——不再依赖加载器修正值。"""
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1.0)
        create_n(reg, 2)
        reg.record_activity(project_id="pid_P1", kind="save_shot")
        st = from_doc(cas.read()["doc"])
        assert st.registry_revision == cas.read()["revision"]

    def test_multi_update_chain_raw_revision_monotonic(self):
        """连续多次提交，每次原始 JSON 版本都与 CAS revision 一致且单调递增。"""
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1.0)
        create_n(reg, 2)
        for i in range(4):
            reg.record_activity(
                project_id="pid_P1" if i % 2 == 0 else "pid_P2",
                kind="save_shot", operation_id=f"op_chain_{i}")
            cur = cas.read()
            assert cur["doc"]["registry_revision"] == cur["revision"]
            assert cur["revision"] > i  # 单调

    def test_dual_writer_candidate_carries_expected_plus_one(self):
        """双写手候选文档在提交前即携带 expected+1（决议：PUT 前构造 N+1）。"""
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1.0)
        create_n(reg, 2)
        stale_expected = cas.read()["revision"] - 1  # 故意过期
        reg.state.activity_seq += 1
        with pytest.raises(PR.CasConflict) as ei:
            reg._persist_with_expected(stale_expected)
        assert ei.value.candidate["registry_revision"] == stale_expected + 1

    def test_explicit_expected_success_raw_doc_next_revision(self):
        """显式 expected 成功路径：原始 JSON 版本 = expected+1 = 新 CAS revision。"""
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1.0)
        create_n(reg, 2)
        expected = cas.read()["revision"]
        reg.state.activity_seq += 1
        result = reg._persist_with_expected(expected)
        assert result["ok"] is True
        cur = cas.read()
        assert cur["revision"] == expected + 1
        assert cur["doc"]["registry_revision"] == expected + 1


# ==================== 补测 A: Poisoned 实例只读 fail-closed ====================

class TestPoisonedReadFailClosed:
    """G4B1_NEXT_STEP §3-1：冲突后旧实例的读取不得被前台误认为权威排名。"""

    def _conflicted_instance(self):
        cas = FakeCAS()
        reg_a = Registry(cas, now=lambda: 1000.0)
        create_n(reg_a, 2)
        reg_b = Registry(cas, now=lambda: 2000.0)
        reg_a.record_activity(project_id="pid_P1", kind="save_shot")
        with pytest.raises(PR.CasConflict):
            reg_b.record_activity(project_id="pid_P2", kind="save_shot",
                                  operation_id="op_b1")
        return cas, reg_a, reg_b

    def test_all_read_methods_fail_closed_after_conflict(self):
        """get/ranking/all_resident/active_slot_holders/marked 全部拒绝。"""
        cas, reg_a, reg_b = self._conflicted_instance()
        for call in (lambda: reg_b.get("pid_P2"),
                     lambda: reg_b.ranking(),
                     lambda: reg_b.all_resident(),
                     lambda: reg_b.active_slot_holders(),
                     lambda: reg_b.marked()):
            with pytest.raises(PR.RegistryPoisoned):
                call()

    def test_loser_ranking_cannot_reach_foreground_as_authoritative(self):
        """冲突后旧实例排名（内存里 P2 已刷新）不可能经任何公开读接口输出。"""
        cas, reg_a, reg_b = self._conflicted_instance()
        # 内存确实已分叉（P2 在败者内存里 seq 最高）
        assert reg_b.state.projects["pid_P2"].last_activity_seq == \
            reg_b.state.activity_seq
        # 但公开读取接口 fail-closed，分叉排名无法冒充权威
        with pytest.raises(PR.RegistryPoisoned):
            reg_b.ranking()
        # 前台只能从全新实例拿到权威排名
        reg_c = Registry(cas, now=lambda: 3000.0)
        ranking = [r.project_id for r in reg_c.ranking()]
        assert ranking[0] == "pid_P1"  # 权威：P1 最新，P2 未动
        assert reg_c.get("pid_P2").last_activity_seq == 2

    def test_raw_state_access_is_documented_forensics_hatch(self):
        """原始 state 字段仍可访问（仅限事后诊断），有注释标注不构成读取接口。"""
        cas, reg_a, reg_b = self._conflicted_instance()
        assert "op_b1" in reg_b.state.operation_dedup  # 取证：内存确曾登记
        assert reg_b._poisoned == "conflict"


# ==================== 补测 B: 未知提交结果（回执丢失）====================

class ReceiptLostCAS:
    """包装 FakeCAS：commit 真实生效后丢弃响应（模拟网络回执丢失）。"""

    def __init__(self, inner, drop_after_success=True):
        self._inner = inner
        self._drop_after_success = drop_after_success

    def read(self):
        return self._inner.read()

    def commit(self, new_doc, expected_revision, *, operation_id):
        if self._drop_after_success:
            result = self._inner.commit(new_doc, expected_revision,
                                        operation_id=operation_id)
            raise TimeoutError("网络回执丢失（提交可能已生效）")
        raise TimeoutError("网络中断（提交未生效）")


class TestUnknownCommitOutcome:
    """G4B1_NEXT_STEP §3-2：回执丢失 ≠ 409；对账前不得显示成功、不得继续写。"""

    def _committed_but_receipt_lost(self):
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1000.0)
        create_n(reg, 2)
        flaky = ReceiptLostCAS(cas, drop_after_success=True)
        reg2 = Registry(flaky, now=lambda: 2000.0)
        return cas, reg, reg2

    def test_outcome_unknown_is_distinct_exception(self):
        """回执丢失抛 RegistryOutcomeUnknown，不是 CasConflict/RegistryPoisoned。"""
        cas, reg, reg2 = self._committed_but_receipt_lost()
        with pytest.raises(PR.RegistryOutcomeUnknown) as ei:
            reg2.record_activity(project_id="pid_P2", kind="save_shot",
                                 operation_id="op_lost")
        assert not isinstance(ei.value, PR.CasConflict)
        assert not isinstance(ei.value, PR.RegistryPoisoned)

    def test_unknown_outcome_not_displayed_as_success(self):
        """调用方拿到的是异常，不是 ok 结果；操作不得显示为已确认成功。"""
        cas, reg, reg2 = self._committed_but_receipt_lost()
        with pytest.raises(PR.RegistryOutcomeUnknown):
            reg2.record_activity(project_id="pid_P2", kind="save_shot",
                                 operation_id="op_lost")

    def test_unknown_outcome_instance_blocks_write_and_read(self):
        """结果未知实例：继续写抛 RegistryOutcomeUnknown（区别于 409），读也拒绝。"""
        cas, reg, reg2 = self._committed_but_receipt_lost()
        with pytest.raises(PR.RegistryOutcomeUnknown):
            reg2.record_activity(project_id="pid_P2", kind="save_shot",
                                 operation_id="op_lost")
        with pytest.raises(PR.RegistryOutcomeUnknown):
            reg2.record_activity(project_id="pid_P2", kind="save_shot",
                                 operation_id="op_next")
        with pytest.raises(PR.RegistryOutcomeUnknown):
            reg2.ranking()

    def test_reconcile_committed_op_no_double_count(self):
        """对账：操作其实已提交 → GET 发现 op_id 在 dedup，重放不重复计序。"""
        cas, reg, reg2 = self._committed_but_receipt_lost()
        with pytest.raises(PR.RegistryOutcomeUnknown):
            reg2.record_activity(project_id="pid_P2", kind="save_shot",
                                 operation_id="op_lost")
        seq_after_loss = cas.read()["doc"]["activity_seq"]  # 已 +1
        reg_fresh = Registry(cas, now=lambda: 3000.0)  # GET 对账
        assert "op_lost" in reg_fresh.state.operation_dedup  # 已提交
        r = reg_fresh.record_activity(project_id="pid_P2", kind="save_shot",
                                      operation_id="op_lost")  # 重放
        assert r["effective"] is True
        assert reg_fresh.state.activity_seq == seq_after_loss  # 不再 +1

    def test_reconcile_uncommitted_op_replays_once(self):
        """对账：操作未提交（提交前断网）→ GET 无 op_id，重放恰执行一次。"""
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1000.0)
        create_n(reg, 2)
        flaky = ReceiptLostCAS(cas, drop_after_success=False)
        reg2 = Registry(flaky, now=lambda: 2000.0)
        with pytest.raises(PR.RegistryOutcomeUnknown):
            reg2.record_activity(project_id="pid_P2", kind="save_shot",
                                 operation_id="op_lost")
        assert "op_lost" not in cas.read()["doc"]["operation_dedup"]
        seq_before = cas.read()["doc"]["activity_seq"]
        reg_fresh = Registry(cas, now=lambda: 3000.0)
        r = reg_fresh.record_activity(project_id="pid_P2", kind="save_shot",
                                      operation_id="op_lost")
        assert r["effective"] is True
        assert reg_fresh.state.activity_seq == seq_before + 1  # 只执行一次

    def test_timeout_error_classified_not_conflict(self):
        """TimeoutError 这类任意非冲突异常都归类为结果未知，不与 409 混淆。"""
        cas, reg, reg2 = self._committed_but_receipt_lost()
        with pytest.raises(PR.RegistryOutcomeUnknown):
            reg2._persist()


# ==================== 补测 C: reassess_retrieved dedup 登记 ====================
# 真实 B1 联调发现（nonce 20261022-77a531）：reassess 三条状态变更路径均不登记
# operation_id，远端 dedup 校验停机。最小修复：状态变更路径 _record_op 后再
# _persist；not retrieved / not due 早退保持不登记（无状态变更）。

class TestReassessDedupRecorded:
    """reassess_retrieved 状态变更必须登记 operation_id（真实联调发现项）。"""

    def test_reassess_active_op_in_dedup_and_replay_idempotent(self):
        reg = Registry(FakeCAS(), now=lambda: 1000.0)
        create_n(reg, 1)
        reg.retrieve(project_id="pid_P1", archive_ref={"sha": "x"})
        r = reg.reassess_retrieved(project_id="pid_P1", now=1000.0 + 4 * 86400.0,
                                   operation_id="op_re_assess")
        assert r["action"] == "no_archive_still_active"
        assert "op_re_assess" in reg.state.operation_dedup  # 修复点
        seq = reg.state.activity_seq
        # 重放同 op：返回已登记结果，不再执行、不再持久化新版本
        r2 = reg.reassess_retrieved(project_id="pid_P1", now=1000.0 + 5 * 86400.0,
                                    operation_id="op_re_assess")
        assert r2 == reg.state.operation_dedup["op_re_assess"]["result"]
        assert reg.state.activity_seq == seq

    def test_reassess_postpone_op_in_dedup(self):
        reg = Registry(FakeCAS(), now=lambda: 1000.0)
        create_n(reg, 1)
        reg.retrieve(project_id="pid_P1", archive_ref={"sha": "x"})
        reg.record_activity(project_id="pid_P1", kind="save_shot")
        r = reg.reassess_retrieved(project_id="pid_P1", now=1000.0 + 4 * 86400.0,
                                   operation_id="op_re_postpone")
        assert r["action"] == "postpone"
        assert "op_re_postpone" in reg.state.operation_dedup

    def test_reassess_marked_op_in_dedup(self):
        reg = Registry(FakeCAS(), now=lambda: 1000.0)
        create_n(reg, 9)
        reg.retrieve(project_id="pid_P3", archive_ref={"sha": "x"})
        for i in range(10, 17):  # 7 个新项目把 P3（取回后 seq 第 3）挤出 top-6
            reg.create_project(project_id=f"pid_P{i}", alias=f"P{i}")
        if reg.get("pid_P3").slot_status != "MARKED":
            import pytest
            pytest.skip("P3 未被挤出 top-6，本用例不适用")
        r = reg.reassess_retrieved(project_id="pid_P3", now=1000.0 + 4 * 86400.0,
                                   operation_id="op_re_marked")
        assert r["action"] == "plan_archive"
        assert "op_re_marked" in reg.state.operation_dedup
