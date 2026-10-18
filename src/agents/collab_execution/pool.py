# R2 B1：候选过渡池域操作（查询 / 审核 / 失效 / 适用性）。
# 查/审/接纳的存储与校验分别在 ledger.py / promotion.py；本模块补齐：
# - 后代级联失效（B10：祖先被拒/过期 → 全部后代失效，且不得接纳到正式项目）
# - 候选适用性摘要（供审计与 UI）
from __future__ import annotations

from typing import List, Optional

from .ledger import CollabLedger


def invalidate_cascade(ledger: CollabLedger, candidate_id: str, reason: str) -> List[str]:
    """失效候选及其全部后代（parent_candidate_id 链）。返回失效的 candidate_id 列表。
    已终态（accepted/rejected）的不动——accepted 是既成事实，rejected 已终。"""
    out: List[str] = []
    frontier = [candidate_id]
    seen = set()
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        cand = ledger.get_candidate(current)
        if cand is None:
            continue
        if cand["acceptance_state"] not in ("accepted", "rejected"):
            ledger.set_candidate_states(current, acceptance_state="stale",
                                        invalid_reason=reason[:300])
            out.append(current)
        # 继续向后代传播（无论本节点是否终态——祖先失效即失去继续依据）
        children = ledger._conn().execute(
            "SELECT candidate_id FROM candidates WHERE parent_candidate_id=?",
            (current,)).fetchall()
        frontier.extend(r["candidate_id"] for r in children)
    return out


def candidate_summary(ledger: CollabLedger, candidate_id: str) -> Optional[dict]:
    """适用性摘要：候选 + 最新回执 + 接纳状态（证明该候选仍适用于当前项目的
    依据在 promotion 前置校验中重核；此处供审计展示）。"""
    cand = ledger.get_candidate(candidate_id)
    if cand is None:
        return None
    receipt = ledger.latest_review(candidate_id)
    return {"candidate": cand, "latest_review": receipt}
