# src/agents/change_protocol/__init__.py
# AI-7B Change Proposal Protocol（§6/§10/§11/§36/§65/§76）。
#
# 本包是 AI-7A ChangeAnalysis 的演进（§8：evolve drafts，不建并行的第二套）。
# 职责：ChangeRequest → ChangeSession → ModuleProposal → UnifiedChangePlan →
#   PlanValidator → 确定性 ChangeOperation[] → ProposalProjection（纯内存预览）→
#   PlanPreviewArtifact → 用户批准 → APPROVED_FOR_EXECUTION → STOP。
#
# 硬不变量（§2/§50/§61/§76/§119/L）：本包绝不 apply、绝不写 canonical / Workspace
#   working_project / Production Plan / 生成资产 / JobQueue。批准只持久化确认 + 置状态。
#
# Import 纪律（对齐 inquiry.py / change_analysis.py）：
#   - 本包单向 import ..runtime（AgentRuntime/AgentError），runtime 不反向 import 本包。
#   - ChangeSession 方法不挂在 AgentRuntime 上；由 composition root（routers/agents.py）
#     调用本包 service 并注入 rt——与 AI-7A create_change_analysis_session 模式一致。
#   - AgentError 用 lazy import（modes.assert_mode_allowed 内）避免循环依赖。
from .modes import AgentExecutionMode, assert_mode_allowed, is_mode_allowed

__all__ = ["AgentExecutionMode", "assert_mode_allowed", "is_mode_allowed"]
