# tests/relstore/test_relstore_19_g4b22_http_classify.py
# G4-B2.2 FINAL SAFETY PATCH：GitHub 写响应分类差异化单元测试。
# 422 ≠ 无条件 CasConflict；409 恒 CasConflict；403/429 = RateLimited 停机。
import pytest

from tools.relstore.http_cas_classify import (
    RateLimited, ValidationFailed, classify_write_response)
from tools.relstore.project_registry import (
    FakeCAS, Registry, RegistryOutcomeUnknown)


# ==================== 分类器差异化 ====================

class TestClassifyDifferentiation:

    def test_409_is_always_cas_conflict(self):
        assert classify_write_response(409) == ("cas_conflict", 409)
        # 409 不需要、也不看证据标志
        assert classify_write_response(409, cas_conflict_evidence=False)[0] \
            == "cas_conflict"

    def test_422_without_evidence_is_validation_failed_not_conflict(self):
        kind, status = classify_write_response(422)
        assert (kind, status) == ("validation_failed", 422)
        assert kind != "cas_conflict"

    def test_422_with_verified_evidence_is_cas_conflict(self):
        kind, _ = classify_write_response(422, cas_conflict_evidence=True)
        assert kind == "cas_conflict"

    def test_403_429_are_rate_limited(self):
        assert classify_write_response(403)[0] == "rate_limited"
        assert classify_write_response(429)[0] == "rate_limited"

    def test_5xx_and_none_are_outcome_unknown(self):
        for s in (500, 502, 503, None):
            assert classify_write_response(s)[0] == "outcome_unknown"

    def test_success_statuses(self):
        assert classify_write_response(200) == ("ok", 200)
        assert classify_write_response(201) == ("ok", 201)

    def test_rate_limited_exception_carries_sanitized_retry_after(self):
        e = RateLimited(429, retry_after="30")
        assert e.status == 429 and e.retry_after == "30"
        assert "github_pat" not in str(e)
        # None 时也不炸
        assert RateLimited(403).retry_after is None


# ==================== 执行侧语义（经 Registry 全链）====================

class _StubCAS:
    """把 classify_write_response 接进 Registry 全链的最小 CAS 桩。"""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.doc = None
        self.revision = 0

    def read(self):
        if self.doc is None:
            return None
        return {"doc": self.doc, "revision": self.revision}

    def commit(self, new_doc, expected, *, operation_id="registry"):
        st = self.statuses.pop(0)
        kind, _ = classify_write_response(st)
        if kind == "ok":
            self.revision += 1
            self.doc = new_doc
            return {"ok": True, "http": st, "revision": self.revision}
        if kind == "cas_conflict":
            from tools.relstore.project_registry import CasConflict
            raise CasConflict(f"HTTP {st}", expected=expected,
                              actual=self.revision, candidate=new_doc)
        if kind == "rate_limited":
            raise RateLimited(st, retry_after="30")
        if kind == "validation_failed":
            raise ValidationFailed(st)
        raise RegistryOutcomeUnknown(f"HTTP {st}: 结果不明")


def _make(statuses):
    return Registry(_StubCAS(statuses), now=lambda: 1000.0)


class TestExecutorSideSemantics:
    """Registry._persist 对非 CasConflict 异常统一包装 OutcomeUnknown 并毒化
    （fail-closed，最小改动不修改状态机）。执行器经 __cause__ 识别真实成因。"""

    @staticmethod
    def _cause_of_outcome_unknown(exc):
        assert isinstance(exc, RegistryOutcomeUnknown)
        return exc.__cause__

    def test_422_validation_failed_wrapped_but_identifiable(self):
        reg2 = _make([200, 422])
        reg2.create_project(project_id="p1", alias="p1", operation_id="op1")
        with pytest.raises(RegistryOutcomeUnknown) as ei:
            reg2.record_activity(project_id="p1", kind="save_shot",
                                 operation_id="op2")
        cause = self._cause_of_outcome_unknown(ei.value)
        assert isinstance(cause, ValidationFailed)
        # 不可被误当 CasConflict（类型区分）
        from tools.relstore.project_registry import CasConflict
        assert not isinstance(cause, CasConflict)
        # fail-closed：毒化为 outcome_unknown，权威读拒绝
        with pytest.raises(RegistryOutcomeUnknown):
            reg2.ranking()

    def test_403_rate_limited_wrapped_but_identifiable(self):
        reg2 = _make([200, 403])
        reg2.create_project(project_id="p1", alias="p1", operation_id="op1")
        with pytest.raises(RegistryOutcomeUnknown) as ei:
            reg2.record_activity(project_id="p1", kind="save_shot",
                                 operation_id="op2")
        cause = self._cause_of_outcome_unknown(ei.value)
        assert isinstance(cause, RateLimited)
        assert cause.status == 403
        # fail-closed：毒化为 outcome_unknown，权威须经全新实例/GET 对账获得
        with pytest.raises(RegistryOutcomeUnknown):
            reg2.ranking()
        fresh_cas = _StubCAS([])
        fresh_cas.doc, fresh_cas.revision = reg2.cas.doc, reg2.cas.revision
        fresh = Registry(fresh_cas, now=lambda: 1001.0)
        assert [r.project_id for r in fresh.ranking()] == ["p1"]

    def test_429_rate_limited_metadata_no_auto_retry(self):
        reg2 = _make([200, 429])
        reg2.create_project(project_id="p1", alias="p1", operation_id="op1")
        with pytest.raises(RegistryOutcomeUnknown) as ei:
            reg2.record_activity(project_id="p1", kind="save_shot",
                                 operation_id="op2")
        cause = self._cause_of_outcome_unknown(ei.value)
        assert isinstance(cause, RateLimited)
        assert cause.status == 429 and cause.retry_after == "30"

    def test_409_still_cas_conflict_poisoning(self):
        from tools.relstore.project_registry import CasConflict
        reg2 = _make([200, 409])
        reg2.create_project(project_id="p1", alias="p1", operation_id="op1")
        with pytest.raises(CasConflict):      # 409 直抛，不包装
            reg2.record_activity(project_id="p1", kind="save_shot",
                                 operation_id="op2")
        from tools.relstore.project_registry import RegistryPoisoned
        with pytest.raises(RegistryPoisoned):
            reg2.ranking()

    def test_5xx_still_outcome_unknown_reconcile_path(self):
        reg2 = _make([200, 503])
        reg2.create_project(project_id="p1", alias="p1", operation_id="op1")
        with pytest.raises(RegistryOutcomeUnknown) as ei:
            reg2.record_activity(project_id="p1", kind="save_shot",
                                 operation_id="op2")
        assert self._cause_of_outcome_unknown(ei.value) is None or not isinstance(
            ei.value.__cause__, (RateLimited, ValidationFailed))

    def test_get_reconcile_and_idempotency_unchanged(self):
        """补丁不改 GET 对账/幂等：正常链 dedup 仍生效。"""
        reg = _make([200, 200])
        reg.create_project(project_id="p1", alias="p1", operation_id="op1")
        r1 = reg.record_activity(project_id="p1", kind="save_shot",
                                 operation_id="opX")
        r2 = reg.record_activity(project_id="p1", kind="save_shot",
                                 operation_id="opX")
        assert r1 == r2                     # 幂等零重复写
        assert len(reg.ranking()) == 1      # GET 读正常
