# src/workflow/models.py
# Phase 46 工作流数据模型（纯 dict 语义 + 少量枚举/常量；风格对齐 src/agents/models.py，
# 但工作流状态以 JSON 快照持久化，字段即契约——详见 docs/workflow/PHASE46 报告）。
#
# 状态语义（PHASE46_01 §7.1，逐条对应，不得把 waiting_human/needs_reconciliation/failed
# 合并进 running）：
#   节点：pending → dispatching → running → success
#                                  ├→ waiting_human（画布审核等待，仅审核节点）
#                                  ├→ needs_reconciliation（提交不明/输入过期）
#                                  ├→ failed（已确认不可继续）
#                                  └→ stopped（停止请求生效/驳回停止）
#   run：running / waiting_human / needs_reconciliation / failed / stopped / rejected / completed
from __future__ import annotations
from typing import Any, Dict, List, Optional

# ============ 常量 ============
DRAFT_SCHEMA_VERSION = "workflow_draft_v1"   # 沿用前端契约（§4.1 不擅自更名）
DEF_SCHEMA_VERSION = "workflow_def_v1"       # 后端持久化定义（= 草稿 + 服务端 revision）

WORKFLOW_AGENTS = ["producer", "script_writer", "character_designer",
                   "scene_designer", "director", "assembly"]

NODE_TYPES = ("agent", "skill", "human_review", "boundary_start", "boundary_end")

# 节点/run 状态（PHASE46_01 §7.1 表）
NODE_PENDING = "pending"                    # 待派发/就绪
NODE_DISPATCHING = "dispatching"            # 派发意图已持久化，正式任务尚未建立
NODE_RUNNING = "running"                    # 已关联真实任务（turn 进行中）
NODE_WAITING_HUMAN = "waiting_human"        # 等待人工（画布审核）
NODE_WAITING_EXTERNAL = "waiting_external"  # 等待外部（预留；v1 turn 内部轮询）
NODE_NEEDS_RECONCILIATION = "needs_reconciliation"  # 需要核对
NODE_FAILED = "failed"
NODE_STOPPING = "stopping"                  # 停止请求中
NODE_STOPPED = "stopped"                    # 已停止（含驳回停止路径的节点）
NODE_SUCCESS = "success"

RUN_RUNNING = "running"
RUN_WAITING_HUMAN = "waiting_human"
RUN_NEEDS_RECONCILIATION = "needs_reconciliation"
RUN_FAILED = "failed"
RUN_STOPPED = "stopped"
RUN_REJECTED = "rejected"                   # 驳回停止（区别于用户主动停止）
RUN_COMPLETED = "completed"

NODE_TERMINAL = {NODE_SUCCESS, NODE_FAILED, NODE_STOPPED}
RUN_TERMINAL = {RUN_FAILED, RUN_STOPPED, RUN_REJECTED, RUN_COMPLETED}

# 各 Agent 节点完成条件（§5.3 节点成功 = 真实结果 + receipt + 产物 + 完成校验）：
# expected_receipt_domains = 该 Agent 的写回执必须覆盖的 changed_refs 域（≥1 条 ok+verified）。
# 键名与 producer._derive_changed_refs 的键一致（真机探针校正：scene_designer 的
# 场景事实落在 locations/location_views/scene_instances/scene_instance_views；
# character_designer 的服装域键是 clothings；script.update 整篇写入计 story 域）。
# producer 是计划/协调节点（工作流上下文中禁止委派 Specialist），无写域要求。
AGENT_EXPECTED_DOMAINS: Dict[str, List[str]] = {
    "producer": [],
    "script_writer": ["scenes", "story"],
    "character_designer": ["characters", "looks", "props", "clothings",
                            "compositions", "reference_assets"],
    "scene_designer": ["locations", "location_views", "scene_instances",
                        "scene_instance_views"],
    "director": ["shots"],
    "assembly": ["sequences"],
}

# 事件类型（runs/{run_id}/events.jsonl；单调 seq）
EV_RUN_STARTED = "run.started"
EV_NODE_STATE = "node.state"                # 节点状态变更（含 from/to）
EV_ATTEMPT_DISPATCH = "attempt.dispatched"  # 已建立正式任务关联
EV_ATTEMPT_RESULT = "attempt.result"        # 收货（真实回执摘要）
EV_NODE_SUCCESS = "node.success"
EV_NODE_FAILED = "node.failed"
EV_REVIEW_CREATED = "review.created"
EV_REVIEW_DECIDED = "review.decided"
EV_RUN_STATE = "run.state"
EV_STOP_REQUESTED = "run.stop_requested"
EV_RECONCILE = "run.reconciled"
EV_HANDOFF = "node.handoff"
EV_SCOPE_SET = "run.scope_set"              # 生成范围闸激活（start/repair；清单外提交预检拒绝）


def new_state_transition(node_id: str, frm: str, to: str, **data) -> Dict[str, Any]:
    d = {"node_id": node_id, "from": frm, "to": to}
    d.update(data)
    return d


def is_node_terminal(status: str) -> bool:
    return status in NODE_TERMINAL


def run_status_of(node_statuses: List[str], reviews_pending: bool,
                  any_reconciling: bool) -> str:
    """run 总状态由节点状态与审核推导（§7.1：总状态必须推导，不合成）。"""
    if any_reconciling:
        return RUN_NEEDS_RECONCILIATION
    if reviews_pending:
        return RUN_WAITING_HUMAN
    if all(s in (NODE_SUCCESS,) for s in node_statuses):
        return RUN_COMPLETED
    if any(s == NODE_FAILED for s in node_statuses):
        return RUN_FAILED
    if any(s in (NODE_STOPPED,) for s in node_statuses):
        return RUN_STOPPED
    return RUN_RUNNING
