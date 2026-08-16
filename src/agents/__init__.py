# src/agents/ —— AI-6B Multi-Agent Foundation
# 8-Agent Runtime 骨架：AgentRegistry / CapabilityRegistry / AgentSession / AgentTask /
# ModelProfile / AgentRuntime。
#
# 不写 canonical project（§18/§19 红线）：v1 只接通 read-only inspect 能力；
# edit/execute 能力注册权限但 adapter 返回 not_implemented（预留）。
# 一层 delegation：Producer -> Specialist 允许；Specialist -> Specialist 禁止（§12）。
# Agent = 决策层；不重写 Production Pipeline / JobQueue / Planner / Autopilot / Workspace（§24）。
