# src/agents/handoff.py
# Phase55 D（任务书 §7）：Producer 会话接力——交接包与恢复。
#
# 语义（任务书原文约束）：
#   - 接近预算上限：停止发起新推理 → 持久化交接点 → 新会话恢复；在途媒体由
#     任务系统（JobQueue）继续管理，不重发；
#   - 交接包至少包含：run/配置快照、canonical revision 和清单引用、已选资产及
#     真实端点、未完成工作、所有在途/不明任务 ID、待审事项、已发生拒绝/失败、
#     当前授权来源、下一项可做的操作；
#   - 交接摘要是【索引】，新会话须回读真实状态，不把旧摘要当项目真相；
#   - 新会话接管后，旧会话晚返回不能再次主控写入（master_lease.transfer）。
from __future__ import annotations
import json
import secrets
import time
from pathlib import Path
from typing import Optional

from . import master_lease


def build_handoff_package(rt, project: str, session_id: str, *, reason: str,
                          budget: Optional[dict] = None,
                          run_id: Optional[str] = None) -> dict:
    """组装交接包（只读 canonical/任务/审核状态——不修改任何事实）。
    全部字段尽力而为、缺因如实标注（fail-open with evidence，不静默编造）。"""
    pdir = rt._resolve_project_dir(project)
    pkg: dict = {
        "schema": "producer_handoff_v1",
        "handoff_id": f"ho_{secrets.token_hex(4)}",
        "project": project,
        "from_session": session_id,
        "created_at": time.time(),
        "reason": (reason or "")[:300],
        "run_id": run_id,
        "budget_anchor": (budget or {}).get("usage_note"),
        "canonical": {},
        "inflight": {"agent_tasks": [], "media_jobs": []},
        "pending_reviews": [],
        "grants": {"active": [], "note": "grant 为项目级授权，新会话继续有效"
                                         "（未过期/未撤销时）"},
        "lease": {},
        "objective_excerpt": "",
        "next_action_hint": "",
    }
    # canonical 读模型（索引而非全文）
    try:
        from src.core.store import ProjectStore
        proj = ProjectStore(pdir).load()
        shots = proj.all_shots()
        pkg["canonical"] = {
            "scenes": len(proj.scenes or []),
            "shots": len(shots),
            "shots_with_final_video": sum(1 for s in shots if s.final_video),
            "shots_generating": [s.shot_id for s in shots
                                 if getattr(s.video, "status", "") == "generating"],
            "shots_pending_first_frame": [s.shot_id for s in shots
                                          if not s.final_video
                                          and not s.selected_first_frame][:30],
            "story_scenes": [sc.scene_id for sc in (proj.scenes or [])][:30],
        }
    except Exception as e:   # noqa: BLE001
        pkg["canonical"] = {"error": str(e)[:200]}
    # 在途任务（AgentTask running + 媒体 job 非终态）
    try:
        store = rt._task_store(project)
        for t in (store.list() or []):
            if getattr(t, "status", "") in ("running", "pending"):
                pkg["inflight"]["agent_tasks"].append(
                    {"task_id": t.task_id, "status": t.status,
                     "capability_id": getattr(t, "capability_id", None),
                     "scope_id": getattr(t, "scope_id", None)})
    except Exception as e:   # noqa: BLE001
        pkg["inflight"]["agent_tasks_error"] = str(e)[:160]
    try:
        q = rt._resolve_job_queue_or_none()
        if q is not None:
            for j in q.list_jobs(project=project, limit=100):
                if j.get("state") in ("queued", "running"):
                    pkg["inflight"]["media_jobs"].append(
                        {"job_id": j["job_id"], "kind": j["kind"],
                         "shot_id": j.get("shot_id"), "state": j["state"],
                         "provider_task_id": (j.get("params") or {})
                         .get("provider_task_id")})
    except Exception as e:   # noqa: BLE001
        pkg["inflight"]["media_jobs_error"] = str(e)[:160]
    # 待审事项（workflow run 的 pending 审核卡；只查活跃 run，避免全量展开）
    try:
        from src.workflow.store import RunStore
        rs = RunStore(pdir, project)
        for rid in (rs.active_run_ids() or [])[:10]:
            full = rs.load(rid) or {}
            for rv in (full.get("reviews") or []):
                if rv.get("status") == "pending":
                    pkg["pending_reviews"].append(
                        {"run_id": rid, "request_id": rv.get("requestId"),
                         "node": rv.get("targetNodeId")})
    except Exception as e:   # noqa: BLE001
        pkg["pending_reviews_error"] = str(e)[:160]
    # 授权来源
    try:
        from . import grant as grant_mod
        pkg["grants"]["active"] = [g.get("grantId") for g in
                                   grant_mod.active(pdir, "producer")]
    except Exception as e:   # noqa: BLE001
        pkg["grants"]["error"] = str(e)[:160]
    # 主控租约状态（Phase56 A3：损坏经 diagnose 如实落包，不冒充无租约）
    try:
        _diag = master_lease.diagnose(pdir)
        if _diag.get("state") == "ok":
            pkg["lease"] = {"holder": _diag.get("holder"),
                            "generation": _diag.get("generation"),
                            "expired": _diag.get("expired")}
        else:
            pkg["lease"] = {"state": _diag.get("state"),
                            "note": _diag.get("note", "")}
    except Exception:
        pkg["lease"] = {}
    # 最近用户目标摘录（索引；新会话回读 canonical，不把摘要当真相）
    try:
        sess = rt._session_store(project).load(session_id)
        if sess is not None:
            user_msgs = [m.content for m in sess.messages
                         if m.role == "user" and m.content]
            pkg["objective_excerpt"] = (user_msgs[-1] if user_msgs else "")[:1500]
    except Exception as e:   # noqa: BLE001
        pkg["objective_excerpt_error"] = str(e)[:160]
    pkg["next_action_hint"] = (
        "新会话第一步：project.inspect 回读 canonical 真实状态（不要信任本摘要的"
        "任何细节），对照【故事目标】与 canonical 的 shots/资产清单确定剩余工作，"
        "然后继续未完成步骤；在途媒体任务由 JobQueue 管理，收货即可，不重发。")
    return pkg


def save_handoff_package(pdir: Path, pkg: dict) -> Path:
    d = Path(pdir) / "output" / "agents" / "handoffs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{pkg['handoff_id']}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pkg, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return p


def relay_producer_session(rt, project: str, old_session_id: str, *,
                           reason: str = "预算接力", run_id: Optional[str] = None,
                           ttl_s: int = master_lease.DEFAULT_TTL_S) -> dict:
    """执行接力：交接包落盘 → 新 producer 会话 → 主控租约移交 → 确定性状态头。
    返回 {handoff_path, handoff, new_session_id, lease}。旧会话此后仅可读
    （写尝试在租约执行点被拒）。"""
    pdir = rt._resolve_project_dir(project)
    pkg = build_handoff_package(rt, project, old_session_id, reason=reason,
                                run_id=run_id)
    path = save_handoff_package(pdir, pkg)
    store = rt._session_store(project)
    new_sid = store.create_fresh_conversation("producer").session_id
    lease = master_lease.transfer(pdir, old_session_id, new_sid, ttl_s=ttl_s)
    if not lease.get("ok"):
        # 旧会话不持有租约（异常路径）——新会话直接获取（接管语义）
        lease = master_lease.acquire(pdir, new_sid, ttl_s=ttl_s)
    # 新会话首条消息 = 接力索引（确定性状态头；含归档指引，不复制旧历史）
    seed = (
        f"【会话接力】上一会话因 {reason} 触发自动交接（handoff_id="
        f"{pkg['handoff_id']}，文件 output/agents/handoffs/{pkg['handoff_id']}.json）。\n"
        f"- 主控租约已移交（generation={lease.get('generation')}）。\n"
        f"- 上会话目标摘录（仅索引，须回读 canonical 核实）：\n"
        f"{pkg['objective_excerpt'][:1200]}\n"
        f"- 在途任务 {len(pkg['inflight']['agent_tasks'])} + 媒体任务 "
        f"{len(pkg['inflight']['media_jobs'])}（由 JobQueue 管理，收货不重发）。\n"
        f"- 待审 {len(pkg['pending_reviews'])} 项；活跃 grant "
        f"{pkg['grants']['active']}。\n"
        f"{pkg['next_action_hint']}")
    from .models import AgentMessage
    rt._session_store(project).append_message(
        new_sid, AgentMessage(role="user", content=seed))
    return {"handoff_path": str(path), "handoff": pkg,
            "new_session_id": new_sid, "lease": lease}
