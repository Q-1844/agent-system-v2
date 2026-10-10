# src/workflow/repair_scope.py
# Phase51 C（用户定版）：修复轮提交范围服务端硬闸。
#
# 背景（run_36919eb5a073 实证）：驳回修复的范围约束此前只存在于提示词文本
# （repairNote），导演（LLM）读取后仍自行决定多生成——授权 4 镜、实际提交
# 20 镜（16 条越界 × 10s 配额 + ~80min 队列墙钟）。提示词约束无效，必须是
# 代码强制：修复 attempt 期间，清单外的镜头生成提交在预检阶段直接拒绝
# （不进队列、不耗配额、回执说明原因）。
#
# 生命周期：
#   set   = continue_run(rejected 修复) 时（显式 allowedShotIds），落 run 节点
#           repairScope（持久化，重启可恢复）+ 进程内注册表；
#           或 run 启动时（Phase51 E start_scope：显式 allowedShotIds 防导演
#           越界重生成，C-T03 att3/att4 两次实证提示词范围约束无效）——落
#           run.startScope（持久化）+ 进程内注册表；
#   check = director_visual 提交预检（任何生成 kind，按 shot_id 判定）；
#   clear = 该 run 任一审核 approve（修复通过闸门）、或 run worker 会话结束
#           （executor._drive finally——含终态/异常/停止；修复轮再次 continue
#           时由 _repair_rejected_run 重新设置）。
# 清单为空列表 = 不限制（未传 allowedShotIds 的修复不启用闸）。
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("workflow.repair_scope")

# project -> {"allowedShotIds": [...], "nodeId": ..., "runId": ..., "reason": ...}
_active: Dict[str, Dict[str, Any]] = {}


def set_scope(project: str, allowed_shot_ids: List[str], node_id: str,
              run_id: str, reason: str = "") -> None:
    _active[project] = {"allowedShotIds": [str(x) for x in (allowed_shot_ids or [])],
                        "nodeId": node_id, "runId": run_id,
                        "reason": (reason or "")[:200]}
    log.info("repair_scope SET project=%s node=%s n_allowed=%d",
             project, node_id, len(allowed_shot_ids or []))


def clear_scope(project: str, run_id: Optional[str] = None) -> None:
    cur = _active.get(project)
    if cur is None:
        return
    if run_id and cur.get("runId") and cur["runId"] != run_id:
        return   # 别的 run 的 scope 不误清
    _active.pop(project, None)
    log.info("repair_scope CLEAR project=%s run=%s", project, run_id)


def get_scope(project: str) -> Optional[Dict[str, Any]]:
    return _active.get(project)


def check_submission(project: str, shot_id: Optional[str]) -> Tuple[bool, str]:
    """生成提交的范围判定。返回 (allowed, reason)。
    无活动 scope → 放行；scope 激活且 shot_id 不在清单 → 拒绝（生成必须
    带 shot_id；无 shot_id 的生成任务不受 shot 范围约束，放行）。"""
    scope = _active.get(project)
    if not scope:
        return True, ""
    if not shot_id:
        return True, ""
    allowed = set(scope.get("allowedShotIds") or [])
    if shot_id in allowed:
        return True, ""
    reason = str(scope.get("reason") or "")
    return False, (
        f"repair_scope_excluded: 本 run 处于生成范围闸约束下（{reason or '驳回修复轮'}，"
        f"节点 {scope.get('nodeId')}），服务端只允许提交清单内镜头（{sorted(allowed)}）；"
        f"镜头 {shot_id} 在清单外。既有合格候选已计入完成判定，无需重新生成。")


def restore_from_run(project: str, run: dict) -> int:
    """后端启动恢复：扫 run 的 startScope（run 级生成范围闸，Phase51 E）与
    run 节点的 repairScope（pending/dispatching 修复节点），重新注册进程内
    scope。返回恢复的 scope 数（0/1）。"""
    if run is None or run.get("status") in ("completed", "stopped", "rejected",
                                            "failed"):
        return 0
    # 修复轮 repairScope（更严格的活性清单）优先于 run 级 startScope
    for nid, n in (run.get("nodes") or {}).items():
        rs = n.get("repairScope") or {}
        allowed = rs.get("allowedShotIds") or []
        if allowed and n.get("status") in ("pending", "dispatching", "running",
                                           "needs_reconciliation"):
            set_scope(project, allowed, nid, run.get("runId") or "",
                      str(rs.get("reason") or ""))
            return 1
    ss = run.get("startScope") or {}
    if ss.get("allowedShotIds"):
        set_scope(project, ss["allowedShotIds"], str(ss.get("nodeId") or ""),
                  run.get("runId") or "", str(ss.get("reason") or "start_scope"))
        return 1
    return 0
