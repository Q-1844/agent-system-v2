# src/workflow/dry_run.py
# Phase51 C（用户裁定）：启动前干跑——零 LLM / 零生成的定义可运行性分析。
#
# 背景（run_36919eb5a073 实证的三类"开跑后才撞"的问题）：
#   ① n_rv_board 审核卡 0 候选（selection 解析结构缺陷，当时被 except 吞成空）；
#   ② 完成判定规格与项目事实脱节（n_dir_video minCount=6 vs 真实 20 镜）；
#   ③ i2v 镜首帧不合格要等跑到视频阶段才发现（boundary_frame_stale）。
# 干跑在 start 之前把这三类暴露出来：逐节点回答"现在派发会发生什么"。
#
# 性质：只读（verify_node_outputs / resolve_selection_candidates 均只读）；
# 无 completion 的节点标 "no_spec"（域命中语义，不假装能预测）。
from __future__ import annotations
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("workflow.dry_run")


def dry_run(pdir: Path, project: str, plan: dict) -> dict:
    """对编译后的 plan 逐节点做"若现在派发"分析。返回：
    {ok, nodes: [{nodeId, refId, kind, status, detail}], blockers: [...]}。
    ok=False 表示存在 blocker 级发现（启动大概率卡在该节点）——不阻止启动
    （人工 gate/创意工作本来就要跑），但把可预见的问题前置。"""
    from .outputs import verify_node_outputs
    from .selection import resolve_selection_candidates
    nodes_out: List[Dict[str, Any]] = []
    blockers: List[Dict[str, Any]] = []

    for nid in plan.get("order") or []:
        pn = (plan.get("nodes") or {}).get(nid) or {}
        ntype = pn.get("type")
        row: Dict[str, Any] = {"nodeId": nid, "refId": pn.get("refId"),
                               "kind": ntype}
        if ntype == "human_review":
            sel = pn.get("selection") or None
            if sel:
                try:
                    cands = resolve_selection_candidates(pdir, project, sel)
                    n = len(cands or [])
                    row["status"] = "selection_ok" if n else "selection_empty"
                    row["detail"] = f"选用候选 {n} 个"
                    if n == 0:
                        # 已知模式（P50/P51 实证）：selection spec 的 entityId 是
                        # 运行期占位符，开卡时按此解析必为空——选用实际经决策 API
                        # 的 selectionOverride 完成。降级为提示，不是 blocker。
                        row["detail"] += ("——占位符 spec 开卡即空（正常）；选用经"
                                          "决策 API selectionOverride 完成")
                except Exception as e:
                    row["status"] = "selection_error"
                    row["detail"] = f"选用候选解析异常: {type(e).__name__}: {str(e)[:120]}"
                    blockers.append({"nodeId": nid, "reason": "selection_error"})
            else:
                row["status"] = "review_gate"
                row["detail"] = "人工审核门（无选用）"
            nodes_out.append(row)
            continue
        if ntype != "agent":
            row["status"] = "structural"
            row["detail"] = "边界节点"
            nodes_out.append(row)
            continue
        comp = pn.get("completion") or {}
        specs = comp.get("outputs") or []
        if not specs:
            row["status"] = "no_spec"
            row["detail"] = "无完成规格（域命中语义，无法预测）"
            nodes_out.append(row)
            continue
        try:
            ov = verify_node_outputs(pdir, project, pn, {}, prior_changed={},
                                     window_start=None)
        except Exception as e:
            row["status"] = "verify_error"
            row["detail"] = f"规格核对异常: {type(e).__name__}: {str(e)[:120]}"
            blockers.append({"nodeId": nid, "reason": "verify_error"})
            nodes_out.append(row)
            continue
        # verify_node_outputs 以"本 attempt 零写入 + 复用允许"评估：ok=True 表示
        # 项目现有事实已满足 specs（allow_verified_reuse 语义下开跑即过；
        # require_new 语义下真实运行仍需新写入——用 strategy 区分表述）。
        strategy = comp.get("strategy") or "require_new"
        n_created = len(ov.get("outputs") or [])
        n_missing = len(ov.get("missing") or [])
        if ov.get("ok"):
            if strategy == "allow_verified_reuse":
                row["status"] = "already_satisfied"
                row["detail"] = (f"现有项目事实已满足规格（created={n_created}）——"
                                 "该节点预计核验复用即过")
            else:
                row["status"] = "needs_new_writes"
                row["detail"] = (f"现有事实满足规格但策略=require_new——运行需真实"
                                 f"新写入 {n_created}+ 项")
        else:
            row["status"] = "will_need_work"
            row["detail"] = f"缺项 {n_missing} 项：{str((ov.get('missing') or [{}])[0].get('reason',''))[:150]}"
            # require_new + 现状缺项 = 启动后该节点必然先补缺（不算 blocker，
            # 但 plan 的创作者应知道工作量）
        # 视频规格的额外提示：可用可播放版本总数 vs minCount
        for spec in specs:
            if spec.get("kind") == "video_version":
                from .outputs import _resolve_kind
                try:
                    matches = _resolve_kind(pdir, project, spec)
                    playable = sum(1 for m in matches if m.get("fileOk"))
                    row["detail"] += (f"；项目现有可播放视频 {playable} 条 / 规格要求 "
                                      f"{spec.get('minCount')} 条")
                except Exception:
                    pass
        nodes_out.append(row)

    return {"ok": not blockers, "nodes": nodes_out, "blockers": blockers}
