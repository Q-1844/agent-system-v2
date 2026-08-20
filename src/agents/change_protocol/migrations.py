# src/agents/change_protocol/migrations.py
# AI-7B schema 版本 + legacy 兼容 + 重启恢复（§8/§9/§87）。
#
# §9 不变量：AI-7A ChangeAnalysisSession（status=ready / plan_draft_id）必须作为
#   historical read-only analysis session 加载，**绝不**静默重判为可执行 AI-7B proposal。
#   机制：legacy 会话（无 schema_version / v1）→ normalize 时标 legacy_analysis_only=True +
#   schema_version=1；service 对 legacy 会话拒绝 resume/approve（不可执行）。
#
# §87 重启恢复：扫非终端会话；处于「计算中」子态（consulting/analyzing/...）的会话
#   视为中断 → blocked；stuck-running proposal → failed。**不自动重跑 LLM**（用户显式 /resume）。
from __future__ import annotations
import time
from typing import Optional

from .models import (SCHEMA_VERSION_CURRENT, SCHEMA_VERSION_LEGACY,
                     SESSION_TERMINAL)


# ============ schema 版本（§9）============
def is_legacy_analysis_session(d: dict) -> bool:
    """无 schema_version 或 v1 = AI-7A 历史只读分析会话（不可执行）。"""
    sv = (d or {}).get("schema_version")
    return sv is None or int(sv) < SCHEMA_VERSION_CURRENT


def session_schema_version(d: dict) -> int:
    return SCHEMA_VERSION_LEGACY if is_legacy_analysis_session(d) else SCHEMA_VERSION_CURRENT


def normalize_session_dict(d: dict) -> dict:
    """把任意会话 dict 归一为安全形态：
    - legacy（无 schema_version）→ schema_version=1, legacy_analysis_only=True（§9）
    - AI-7B → schema_version=2, legacy_analysis_only=False
    未知字段原样保留（ChangeSession.from_dict 再过滤）。"""
    d = dict(d or {})
    if is_legacy_analysis_session(d):
        d["schema_version"] = SCHEMA_VERSION_LEGACY
        d["legacy_analysis_only"] = True
    else:
        d["schema_version"] = SCHEMA_VERSION_CURRENT
        d.setdefault("legacy_analysis_only", False)
    return d


# ============ 重启恢复（§87）============
# 处于这些「计算中」子态的会话，重启视为中断 → blocked。
# routing/awaiting_user_decision/ready_for_review 是稳定态（无在途计算）→ 不动。
_INFLIGHT_SESSION_STATUSES = {
    "consulting", "analyzing_dependencies", "resolving_conflicts",
    "synthesizing_plan", "validating_plan", "building_preview",
}


def recover_change_protocol(project_dir, project_name: str) -> int:
    """startup 恢复：扫非终端会话，in-flight 子态 → blocked；stuck-running proposal → failed。
    不自动重跑 LLM（§57/§87）。返回处理的会话数。"""
    try:
        from .store import ChangeProtocolStore   # lazy：避免与 store 的模块级 import 循环
        store = ChangeProtocolStore(project_dir, project_name)
        sessions = store.list_sessions()
    except Exception:
        return 0
    n = 0
    now = time.time()
    for s in sessions:
        if s.status in SESSION_TERMINAL or s.legacy_analysis_only:
            continue
        touched = False
        # in-flight 会话 → blocked
        if s.status in _INFLIGHT_SESSION_STATUSES:
            s.status = "blocked"
            s.error = "分析因后端重启中断，请恢复后重试"
            s.updated_at = now
            touched = True
        # stuck-running proposals → failed
        for pid in list(s.module_proposal_ids):
            mp = store.get_proposal(pid)
            if mp and mp.status == "running":
                mp.status = "failed"
                mp.updated_at = now
                store.save_proposal(mp)
                touched = True
        if touched:
            store.save_session(s)
            n += 1
    return n
