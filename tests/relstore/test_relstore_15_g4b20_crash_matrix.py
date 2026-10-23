# tests/relstore/test_relstore_15_g4b20_crash_matrix.py
# G4-B2.0 P0 故障矩阵（零远端，离线模拟）：
#   M1 PUT 前死亡（服务端未收到）      → 未知结果，权威未变
#   M2 PUT 中死亡（在途）              → 结果必为 M1 或 M3 之一，语义相同
#   M3 服务端已提交但回执丢失          → 未知结果，权威已有效果
# 共同断言：
#   A. 未知结果期间旧实例读/写全部 fail-closed（RegistryOutcomeUnknown）
#   B. GET 对账后同一 operation_id 恰好一次有效效果
#   C. 重复重启/重放不双写
# 另含：真子进程"提交后立即死亡"持久化存活测试、并发同 op 无越权双写。
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tools.relstore.project_registry import (
    CasConflict, FakeCAS, Registry, RegistryOutcomeUnknown,
    from_doc, new_operation_id)


def make_reg(now_val=1000.0):
    return Registry(FakeCAS(), now=lambda: now_val)


def create_n(reg, n):
    for i in range(1, n + 1):
        reg.create_project(project_id=f"pid_P{i}", alias=f"P{i}")


class CasKillBeforeCommit:
    """M1：进程在 PUT 发出前死亡——服务端从未收到请求。
    armed 开关控制注入（Registry._persist 固定以 operation_id="registry"
    调 CAS，无法按业务 op 名注入，故用显式武装开关）。"""
    def __init__(self, inner):
        self.inner = inner
        self.armed = False

    def read(self):
        return self.inner.read()

    def commit(self, new_doc, expected_revision, *, operation_id):
        if self.armed:
            raise ConnectionError("simulated: killed before PUT left the host")
        return self.inner.commit(new_doc, expected_revision,
                                 operation_id=operation_id)


class CasKillAfterCommit:
    """M3：服务端已原子提交，进程在收到回执前死亡。"""
    def __init__(self, inner):
        self.inner = inner
        self.armed = False

    def read(self):
        return self.inner.read()

    def commit(self, new_doc, expected_revision, *, operation_id):
        res = self.inner.commit(new_doc, expected_revision,
                                operation_id=operation_id)  # 权威已落
        if self.armed:
            raise ConnectionError("simulated: killed before receipt arrived")
        return res


def reconcile_new_instance(cas, op_id):
    """GET 对账：从权威重建实例，返回 (reg, op是否已在权威生效)。"""
    reg = Registry(cas, now=lambda: 9999.0)
    return reg, op_id in reg.state.operation_dedup


# ==================== M1：PUT 前死亡 ====================

class TestKillBeforePut:

    def test_authority_untouched_and_writer_fenced(self):
        cas = FakeCAS()
        w = CasKillBeforeCommit(cas)
        reg = Registry(w, now=lambda: 1.0)
        create_n(reg, 3)
        seq_before, rev_before = reg.state.activity_seq, cas._revision
        w.armed = True
        with pytest.raises(RegistryOutcomeUnknown):
            reg.record_activity(project_id="pid_P1", kind="save_shot",
                                operation_id="op_x")
        # A. 旧实例读写全部禁用
        with pytest.raises(RegistryOutcomeUnknown):
            reg.record_activity(project_id="pid_P2", kind="save_shot")
        with pytest.raises(RegistryOutcomeUnknown):
            reg.ranking()
        # 权威未变
        assert cas._revision == rev_before
        reg2 = Registry(cas, now=lambda: 2.0)
        assert reg2.state.activity_seq == seq_before

    def test_reconcile_not_committed_then_replay_exactly_once(self):
        cas = FakeCAS()
        w = CasKillBeforeCommit(cas)
        reg = Registry(w, now=lambda: 1.0)
        create_n(reg, 3)
        w.armed = True
        with pytest.raises(RegistryOutcomeUnknown):
            reg.record_activity(project_id="pid_P1", kind="save_shot",
                                operation_id="op_x")
        # B. 对账：op 未生效 → 新实例重放恰一次
        reg2, committed = reconcile_new_instance(cas, "op_x")
        assert committed is False
        seq_before = reg2.state.activity_seq
        reg2.record_activity(project_id="pid_P1", kind="save_shot",
                             operation_id="op_x")
        assert reg2.state.activity_seq == seq_before + 1  # 效果恰一次
        rev_after_replay = cas._revision
        # C. 重放 N 次：零新增效果、零新增写
        for _ in range(5):
            reg3 = Registry(cas, now=lambda: 3.0)
            reg3.record_activity(project_id="pid_P1", kind="save_shot",
                                 operation_id="op_x")
        assert cas._revision == rev_after_replay
        reg4 = Registry(cas, now=lambda: 4.0)
        assert reg4.state.activity_seq == seq_before + 1


# ==================== M3：提交后回执丢失 ====================

class TestKillAfterCommit:

    def test_authority_has_effect_and_writer_fenced(self):
        cas = FakeCAS()
        w = CasKillAfterCommit(cas)
        reg = Registry(w, now=lambda: 1.0)
        create_n(reg, 3)
        w.armed = True
        with pytest.raises(RegistryOutcomeUnknown):
            reg.record_activity(project_id="pid_P1", kind="save_shot",
                                operation_id="op_y")
        with pytest.raises(RegistryOutcomeUnknown):
            reg.record_activity(project_id="pid_P2", kind="save_shot")
        with pytest.raises(RegistryOutcomeUnknown):
            reg.get("pid_P1")
        # 权威已含效果（rev 已推进）
        assert cas._revision > 3  # 3 creates + 1 effect

    def test_reconcile_committed_replay_zero_effect(self):
        cas = FakeCAS()
        w = CasKillAfterCommit(cas)
        reg = Registry(w, now=lambda: 1.0)
        create_n(reg, 3)
        w.armed = True
        with pytest.raises(RegistryOutcomeUnknown):
            reg.record_activity(project_id="pid_P1", kind="save_shot",
                                operation_id="op_y")
        reg2, committed = reconcile_new_instance(cas, "op_y")
        assert committed is True
        seq_before, rev_before = reg2.state.activity_seq, cas._revision
        # 重放：dedup 命中，效果不重复
        r = reg2.record_activity(project_id="pid_P1", kind="save_shot",
                                 operation_id="op_y")
        assert reg2.state.activity_seq == seq_before
        assert cas._revision == rev_before  # 0 写
        for _ in range(3):
            Registry(cas, now=lambda: 5.0).record_activity(
                project_id="pid_P1", kind="save_shot", operation_id="op_y")
        assert cas._revision == rev_before
        reg3 = Registry(cas, now=lambda: 6.0)
        assert reg3.state.activity_seq == seq_before  # 权威恰一次效果


class TestKillDuringPut:
    """M2：在途死亡的结果必为 M1 或 M3——两种都已被 fencing+对账覆盖，
    此处参数化证明语义与死亡时点无关（调用方无需区分）。"""

    @pytest.mark.parametrize("wrapper", [CasKillBeforeCommit, CasKillAfterCommit])
    def test_semantics_identical_for_caller(self, wrapper):
        cas = FakeCAS()
        w = wrapper(cas)
        reg = Registry(w, now=lambda: 1.0)
        create_n(reg, 2)
        base_seq = reg.state.activity_seq
        w.armed = True
        with pytest.raises(RegistryOutcomeUnknown):
            reg.record_activity(project_id="pid_P1", kind="save_shot",
                                operation_id="op_z")
        # 无论时点：旧实例禁写，唯一出路是对账
        with pytest.raises(RegistryOutcomeUnknown):
            reg.record_activity(project_id="pid_P1", kind="save_shot")
        reg2, committed = reconcile_new_instance(cas, "op_z")
        reg2.record_activity(project_id="pid_P1", kind="save_shot",
                             operation_id="op_z")
        reg3 = Registry(cas, now=lambda: 2.0)
        # 时点无关的不变量：对账+重放后效果恰好一次（seq = base+1）——
        # M1 在重放时生效、M3 生效早已在权威，调用方语义相同
        assert reg3.state.activity_seq == base_seq + 1
        assert reg3.get("pid_P1").last_activity_kind == "save_shot"


# ==================== 真子进程：提交后立即死亡 ====================

WORKER_SRC = r"""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, {repo!r})
from tools.relstore.project_registry import Registry, from_doc, RegistryState

class FileShimCAS:
    # 单文件 CAS：read/commit 直接作用于 json 文件（离线，零网络）。
    def __init__(self, path):
        self.path = Path(path)
    def read(self):
        if not self.path.exists():
            return None
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        return {{"doc": doc, "revision": doc.get("registry_revision", 1)}}
    def commit(self, new_doc, expected_revision, *, operation_id):
        if not self.path.exists():
            if expected_revision is not None:
                raise RuntimeError("conflict")
            rev = 1
        else:
            cur = json.loads(self.path.read_text(encoding="utf-8"))
            if cur.get("registry_revision") != expected_revision:
                raise RuntimeError("conflict")
            rev = expected_revision + 1
        new_doc["registry_revision"] = rev
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(new_doc, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)  # 原子落盘
        return {{"ok": True, "revision": rev}}

import json
reg = Registry(FileShimCAS({state!r}), now=lambda: 1000.0)
reg.record_activity(project_id="pid_P1", kind="save_shot",
                    operation_id="op_crash")
os._exit(9)  # 提交已落盘、进程立即死亡、零回执
"""


class TestSubprocessDeathAfterCommit:
    """真子进程在 CAS 提交落盘后 os._exit(9)：效果必须已在权威、
    父进程对账后重放恰一次、旧内存视图不得越权。"""

    def test_effect_survives_death_and_replay_is_once(self, tmp_path):
        state = tmp_path / "state.json"
        repo = str(Path(__file__).resolve().parents[2])
        reg0 = Registry(_FileShimCAS(state), now=lambda: 1.0)
        create_n(reg0, 3)
        src = WORKER_SRC.format(repo=repo, state=str(state))
        p = subprocess.run([sys.executable, "-c", src],
                           capture_output=True, timeout=60)
        assert p.returncode == 9  # 死于 os._exit(9)，且此前提交成功
        # 对账：权威已有 op_crash 与其效果
        reg1, committed = reconcile_new_instance(_FileShimCAS(state), "op_crash")
        assert committed is True
        seq_before, raw_before = reg1.state.activity_seq, state.read_bytes()
        reg1.record_activity(project_id="pid_P1", kind="save_shot",
                             operation_id="op_crash")
        assert reg1.state.activity_seq == seq_before
        assert state.read_bytes() == raw_before  # 重放零写
        # 3 次全新"重启"重放：永不双写
        for _ in range(3):
            r = Registry(_FileShimCAS(state), now=lambda: 2.0)
            r.record_activity(project_id="pid_P1", kind="save_shot",
                              operation_id="op_crash")
        reg2 = Registry(_FileShimCAS(state), now=lambda: 3.0)
        assert reg2.state.activity_seq == seq_before
        assert reg2.state.projects["pid_P1"].last_activity_seq == seq_before


class _FileShimCAS:
    """父进程侧同款文件 CAS（与 WORKER_SRC 内定义保持一致）。"""
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        if not self.path.exists():
            return None
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        return {"doc": doc, "revision": doc.get("registry_revision", 1)}

    def commit(self, new_doc, expected_revision, *, operation_id):
        if not self.path.exists():
            if expected_revision is not None:
                raise CasConflict("create 冲突", expected=expected_revision,
                                  actual=None, candidate=new_doc)
            rev = 1
        else:
            cur = json.loads(self.path.read_text(encoding="utf-8"))
            if cur.get("registry_revision") != expected_revision:
                raise CasConflict("rev 冲突", expected=expected_revision,
                                  actual=cur.get("registry_revision"),
                                  candidate=new_doc)
            rev = expected_revision + 1
        new_doc["registry_revision"] = rev
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(new_doc, ensure_ascii=False),
                       encoding="utf-8")
        import os
        os.replace(tmp, self.path)
        return {"ok": True, "revision": rev}


# ==================== 并发：同 op 双写手无越权双写 ====================

class TestConcurrentSameOp:

    def test_stale_replayer_conflicts_not_duplicates(self):
        """两实例同 op：胜者生效，败者 CAS 冲突毒化，权威恰一次。"""
        cas = FakeCAS()
        base = Registry(cas, now=lambda: 1.0)
        create_n(base, 3)
        # 两个实例都从 rev3 载入（都不知道对方）
        a = Registry(cas, now=lambda: 2.0)
        b = Registry(cas, now=lambda: 2.0)
        a.record_activity(project_id="pid_P1", kind="save_shot",
                          operation_id="op_same")
        with pytest.raises(CasConflict):
            b.record_activity(project_id="pid_P1", kind="save_shot",
                              operation_id="op_same")
        assert b._poisoned == "conflict"
        with pytest.raises(Exception):
            b.ranking()  # 毒化实例不得读
        final = Registry(cas, now=lambda: 3.0)
        assert final.state.activity_seq == 4  # 3 creates + 1 效果（不重复）
        assert final.state.projects["pid_P1"].last_activity_kind == "save_shot"

    def test_unknown_outcome_writer_cannot_overwrite_winner(self):
        """M3 写手 + 另一写手已推进：未知者对账后重放 dedup 命中，不抢写。"""
        cas = FakeCAS()
        w = CasKillAfterCommit(cas)
        reg = Registry(w, now=lambda: 1.0)
        create_n(reg, 3)
        w.armed = True
        with pytest.raises(RegistryOutcomeUnknown):
            reg.record_activity(project_id="pid_P1", kind="save_shot",
                                operation_id="op_u")
        # 期间其他写手推进权威
        other = Registry(cas, now=lambda: 2.0)
        other.record_activity(project_id="pid_P2", kind="save_shot",
                              operation_id="op_other")
        # 未知者重放：dedup 命中（效果在 M3 时已落权威），0 效果 0 写
        rev_before = cas._revision
        reg2, committed = reconcile_new_instance(cas, "op_u")
        assert committed is True
        reg2.record_activity(project_id="pid_P1", kind="save_shot",
                             operation_id="op_u")
        assert cas._revision == rev_before


# ==================== B20-07：历史缺口 op_reassess_P4 重放 ====================

class TestLegacyGapOpReassessP4:
    """复现 rev15 历史状态（op_reassess_P4 已执行但不 dedup），
    单次与多次重放均不得产生有效副作用（台账 R-002 复核）。"""

    def _rev15_like_state(self):
        """构造 rev15 时点权威：P1 已 retrieve 并 reassess（计时器已清、
        仍 ACTIVE、kind 仍 retrieve），但 op_reassess 不在 dedup。"""
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1.0)
        create_n(reg, 3)
        reg.retrieve(project_id="pid_P1", archive_ref={"sha256": "ab" * 32})
        op = "op_reassess_P4"
        reg.reassess_retrieved(project_id="pid_P1", now=1.0 + 4 * 86400.0,
                               operation_id=op)
        assert op in reg.state.operation_dedup  # 固定代码正常登记
        # 模拟 rev15 缺口：效果已在权威，但 dedup 条目被剥离（修复前行为）
        del reg.state.operation_dedup[op]
        reg._persist()
        assert cas.read()["doc"]["operation_dedup"].get(op) is None
        return cas, op

    def test_single_replay_no_effect(self):
        cas, op = self._rev15_like_state()
        reg = Registry(cas, now=lambda: 2.0)
        rec = reg.get("pid_P1")
        assert rec.retrieved_reassess_at is None      # 计时器已清
        assert rec.last_activity_kind == "retrieve"
        seq_before, rev_before = reg.state.activity_seq, cas._revision
        r = reg.reassess_retrieved(project_id="pid_P1", now=9e9,
                                   operation_id=op)
        assert r == {"ok": False, "reason": "not retrieved"}  # 0 效果
        assert reg.state.activity_seq == seq_before
        assert cas._revision == rev_before            # 0 写
        assert op not in reg.state.operation_dedup    # 缺口保持原样

    def test_many_replays_no_effect_no_write(self):
        cas, op = self._rev15_like_state()
        seq_before = Registry(cas, now=lambda: 2.0).state.activity_seq
        rev_before = cas._revision
        for i in range(10):
            r = Registry(cas, now=lambda: 2.0 + i).reassess_retrieved(
                project_id="pid_P1", now=9e9, operation_id=op)
            assert r["ok"] is False
        assert cas._revision == rev_before            # 10 次重放 0 写
        final = Registry(cas, now=lambda: 3.0)
        assert final.state.activity_seq == seq_before
        assert final.get("pid_P1").slot_status == "ACTIVE"   # 排名未动
        assert final.get("pid_P1").archive_status == "NONE"  # 未误入归档
        assert final.state.archive_queue == []

    def test_replay_before_timer_cleared_executes_once(self):
        """若缺口发生在计时器未清的时点（reassess 从未成功）：
        固定代码重放恰执行一次并登记 dedup，之后重放 dedup 命中 0 写。"""
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1.0)
        create_n(reg, 3)
        reg.retrieve(project_id="pid_P1", archive_ref={"sha256": "cd" * 32})
        op = "op_reassess_P4"
        # 模拟：reassess 从未落权威（无效果、无 dedup），计时器保持 retrieve 时
        # 设定的 now+3d；rev_before 即 retrieve 后的权威版本
        reg2 = Registry(cas, now=lambda: 2.0)
        assert "op_reassess_P4" not in reg2.state.operation_dedup
        assert reg2.get("pid_P1").retrieved_reassess_at is not None
        rev_before = cas._revision
        r1 = reg2.reassess_retrieved(project_id="pid_P1", now=1.0 + 4 * 86400.0,
                                     operation_id=op)
        assert r1["action"] == "no_archive_still_active"
        assert op in reg2.state.operation_dedup
        rev_after_1 = cas._revision
        assert rev_after_1 == rev_before + 1          # 恰 1 写
        seq_after_1 = Registry(cas, now=lambda: 3.0).state.activity_seq
        # 重放 #2..N：dedup 命中，0 效果 0 写
        for _ in range(5):
            reg3 = Registry(cas, now=lambda: 4.0)
            reg3.reassess_retrieved(project_id="pid_P1", now=9e9,
                                    operation_id=op)
        assert cas._revision == rev_after_1
        final = Registry(cas, now=lambda: 5.0)
        assert final.state.activity_seq == seq_after_1
        assert final.get("pid_P1").slot_status == "ACTIVE"
        assert final.state.archive_queue == []


# ==================== B2.0a：快照隔离（嵌套可变性回归） ====================

class TestSnapshotIsolation:
    """80e0665 只做了外层 dict 拷贝；B2.0a 实证发现嵌套引用仍可渗透权威：
    dedup value（{kind,...,result}）会被返回给调用方、from_doc 加载的
    ProjectRecord 可变字段与权威共享。本组测试把"已提交权威快照不可被
    实例侧/调用方后续修改污染"固化为回归（B2_0A_GATE_CORRECTION 依据）。"""

    @staticmethod
    def _committed_doc(cas):
        import copy as _copy
        return _copy.deepcopy(cas.read()["doc"])  # 基线快照

    def test_nested_dedup_value_mutation_cannot_reach_authority(self):
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1.0)
        reg.create_project(project_id="p1", alias="a1", operation_id="op1")
        r2 = reg.record_activity(project_id="p1", kind="save_shot",
                                 operation_id="op2")
        baseline = self._committed_doc(cas)
        # 调用方改返回的 result dict + 实例侧改 state 中 dedup 嵌套值
        r2["effective"] = "MUTATED"
        reg.state.operation_dedup["op1"]["kind"] = "MUTATED"
        reg.state.operation_dedup["op2"]["at"] = 999.0
        now_doc = cas.read()["doc"]
        assert now_doc == baseline  # 权威快照纹丝不动

    def test_post_commit_dedup_addition_cannot_reach_authority(self):
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1.0)
        reg.create_project(project_id="p1", alias="a1")
        baseline = self._committed_doc(cas)
        # 提交失败路径之外：直接向实例 dedup 追加（模拟 _record_op 渗透方向）
        reg.state.operation_dedup["op_ghost"] = {"kind": "ghost"}
        assert "op_ghost" not in cas.read()["doc"]["operation_dedup"]
        assert cas.read()["doc"] == baseline

    def test_loaded_record_mutable_fields_cannot_reach_authority(self):
        cas = FakeCAS()
        reg = Registry(cas, now=lambda: 1.0)
        reg.create_project(project_id="p1", alias="a1")
        reg.record_activity(project_id="p1", kind="save_shot",
                            offline_intent=True, project_revision=1)
        baseline = self._committed_doc(cas)
        reg2 = Registry(cas, now=lambda: 2.0)  # 从权威加载
        rec = reg2.get("p1")
        rec.pending_sync_intents.append({"hacked": True})
        rec.missing_required.append("hacked_too")
        rec.archive_ref = {"hacked": "ref"}
        rec.active_operation_id = "op_hacked"
        now_doc = cas.read()["doc"]
        assert now_doc == baseline  # 权威快照纹丝不动
        # 且新实例自身的修改仍然生效（不是把实例冻结成只读）
        assert rec.pending_sync_intents[-1] == {"hacked": True}

    def test_reconcile_after_unknown_outcome_still_exactly_once(self):
        """隔离修正不得退化 80e0665 的原目标：提交失败后 _record_op 追加
        不渗透权威，对账后重放恰一次。"""
        cas = FakeCAS()
        w = CasKillBeforeCommit(cas)
        reg = Registry(w, now=lambda: 1.0)
        create_n(reg, 2)
        w.armed = True
        with pytest.raises(RegistryOutcomeUnknown):
            reg.record_activity(project_id="pid_P1", kind="save_shot",
                                operation_id="op_x")
        assert "op_x" not in cas.read()["doc"]["operation_dedup"]
        reg2, committed = reconcile_new_instance(cas, "op_x")
        assert committed is False
        reg2.record_activity(project_id="pid_P1", kind="save_shot",
                             operation_id="op_x")
        reg3 = Registry(cas, now=lambda: 2.0)
        assert "op_x" in reg3.state.operation_dedup  # 恰一次
