# src/agents/change_protocol/modes.py
# AI-7B 运行模式 + 代码级执行硬闸（§6）。
#
# 设计不变量（§6）：
#   - 守卫必须是代码，不是 prompt（"Please do not modify anything" 不算）。
#   - READ_ONLY：可 inspect；不可 propose/mutate/produce。
#   - PROPOSAL_ONLY：可 inspect + propose；不可 apply/commit/生成资产/触发生产。
#   - 与既有 CapabilityRegistry.can_execute 复合（不是替换）：can_execute 管
#     "agent 是否被授权该 cap+operation"；assert_mode_allowed 管 "该运行模式下
#     该 operation 是否被允许"。两者都过才放行。
#
# AI-7A 的只读边界是 capability-based（_AGENT_INSPECT_CAP + assert_read_only_capability
# + can_execute）。AI-7B 在其上叠加显式 AgentExecutionMode 枚举，覆盖 propose 这一
# 新操作类别（propose 既非 inspect 也非 mutate，§19/§20/§21 的语义提案）。
from __future__ import annotations
from enum import Enum
from typing import Set


class AgentExecutionMode(str, Enum):
    """AI-7B 运行模式（§6）。既有正常生产模式（capability 的 execute 路径）不受影响。
    AI-7C（§7）：新增 APPROVED_PLAN_EXECUTION——执行引擎应用**已批准**的 ChangeOperation，
    不走 agent capability dispatch（§9/§26：0 创意 LLM 调用、0 重新决策）。该模式仅可经
    execute 端点全量校验（approved_plan_id + exact plan_version + valid PlanApproval +
    AI-7C preflight + ChangeExecutionSession）获得，本身不授予任何 agent 新写权限。"""
    READ_ONLY = "read_only"           # AI-7A：只读检视
    PROPOSAL_ONLY = "proposal_only"   # AI-7B：只读 + 语义提案
    APPROVED_PLAN_EXECUTION = "approved_plan_execution"   # AI-7C：执行已批准计划


# 每种模式允许的 operation 集合（§6）。mutate/produce（execute/retry/cancel 及
# 任何未列出项）一律拒绝。
# AI-7C：APPROVED_PLAN_EXECUTION 只允许 inspect——执行引擎本身是独立代码路径（非 agent op），
# 不经此闸；此模式仅作为"已批准执行进行中"的标记，且 fail-closed（agent 不得借此 propose/mutate）。
_ALLOWED_OPS: dict = {
    AgentExecutionMode.READ_ONLY.value: {"inspect"},
    AgentExecutionMode.PROPOSAL_ONLY.value: {"inspect", "propose"},
    AgentExecutionMode.APPROVED_PLAN_EXECUTION.value: {"inspect"},
}


def allowed_operations(mode: str) -> Set[str]:
    return set(_ALLOWED_OPS.get(mode, set()))


def assert_mode_allowed(mode: str, operation: str) -> None:
    """代码级硬闸（§6）。mode 下不允许的 operation -> AgentError(permission_denied)。
    AgentError 用 lazy import 避免与 runtime.py 循环依赖（对齐 project_intelligence 写法）。"""
    allowed = _ALLOWED_OPS.get(mode)
    if allowed is None:
        from ..runtime import AgentError   # lazy：避免循环依赖
        raise AgentError("permission_denied", f"未知运行模式: {mode}")
    if operation not in allowed:
        from ..runtime import AgentError   # lazy：避免循环依赖
        raise AgentError(
            "permission_denied",
            f"模式 {mode} 禁止操作 {operation!r}（仅允许 {sorted(allowed)}）")


def is_mode_allowed(mode: str, operation: str) -> bool:
    """断言式查询，不抛。"""
    try:
        assert_mode_allowed(mode, operation)
        return True
    except Exception:
        return False
