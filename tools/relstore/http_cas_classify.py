# -*- coding: utf-8 -*-
"""G4-B2.2 FINAL SAFETY PATCH：GitHub 写响应分类器（最小新增，不动状态机）。

背景：GitHub Contents API 的 422 不一定代表 CAS 冲突——也可能是
Validation Failed 或二级限流/滥用限制。因此：

  409          → 恒为 CAS 冲突（CasConflict）：实例毒化、弃实例重载权威。
  422          → 仅当存在**明确且经验证的 CAS 冲突证据**时才映射 CasConflict；
                 否则为验证失败（ValidationFailed）：不可自动重试，STOP 并留证。
  403 / 429    → RateLimited（限流）：记录状态码与脱敏后的 Retry-After；
                 禁止后续远端写入，不自动重试，不擅入下一场景。
  5xx / 无响应 → OutcomeUnknown（结果不明）：必须先 GET 权威对账。
  2xx          → ok。

"经验证的 CAS 冲突证据"（cas_conflict_evidence）定义为：执行器独立核实
服务端响应明确确认 expected-sha/branch 不匹配（如响应体明确指出 sha mismatch，
且请求是本执行器发出的 CAS PUT）。仅凭 422 状态码本身不构成证据。
本模块不改变 GET 对账与重复操作幂等逻辑（project_registry.py 原样）。
"""
from __future__ import annotations

__all__ = ["RateLimited", "ValidationFailed", "classify_write_response"]


class RateLimited(Exception):
    """GitHub 限流（403/429）。retry_after 已脱敏（仅数值/无凭据字段）。"""

    def __init__(self, status: int, retry_after: str | None = None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(
            f"HTTP {status} 限流（retry_after={retry_after!r}）；"
            "禁止后续远端写入，不自动重试，仅剩余 GET 预算内的"
            "权威对账与只读终验")


class ValidationFailed(Exception):
    """422 且无经验证的 CAS 冲突证据：不可自动重试，STOP 并留证。"""

    def __init__(self, status: int = 422, detail: str = ""):
        self.status = status
        super().__init__(
            f"HTTP {status} 验证失败（无经验证的 CAS 冲突证据）{detail}；"
            "不可自动重试，STOP 留证")


def classify_write_response(
        status: int | None, *,
        cas_conflict_evidence: bool = False,
        retry_after: str | None = None) -> tuple[str, int | None]:
    """把 GitHub 写响应状态映射为处置类别。

    返回 (kind, status)，kind ∈ {ok, cas_conflict, validation_failed,
    rate_limited, outcome_unknown}。本函数是纯分类器，不发起任何 IO。
    """
    if status in (200, 201):
        return "ok", status
    if status == 409:
        return "cas_conflict", status
    if status == 422:
        if cas_conflict_evidence:
            return "cas_conflict", status
        return "validation_failed", status
    if status in (403, 429):
        return "rate_limited", status
    # 5xx、None（无响应/传输层丢失）及其它未列状态：保守视为结果不明
    return "outcome_unknown", status
