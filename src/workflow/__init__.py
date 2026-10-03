# src/workflow/__init__.py
# Phase 46：六个正式 Agent 可视化工作流后端（定义/校验/编译/顺序执行/审核/恢复）。
#
# 职责边界（PHASE46_01 §2）：
#   - 本包是「顺序调度权威」：决定何时派发下一个节点，并通过既有正式任务入口
#     （producer._delegate_specialist / 各 *_turn）派发；不重新实现任何专业 Agent
#     的制作策略，不直接写 story/resources/shots/sequence canonical。
#   - 复用既有底座：AgentRegistry / AgentTaskStore / turn_registry / ProjectEventBus /
#     atomic_write_json / OCC 指纹 / JobQueue / truth gates。无数据库，落盘
#     projects/{p}/output/workflows/ 下（definitions / runs / events.jsonl）。
#   - 首版范围：单条连通顺序链；Agent / Skill（只绑定）/ 人工审核三类组件；
#     通过→继续，驳回→停止。不做条件分支/并行/自动返工/子工作流。
