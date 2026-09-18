# src/agents/production_room.py
# Producer V1 Phase 3 — Production Room projection（只读投影）。
#
# Production Room 是协作记录的 View，不是新的事实来源（Phase 3 §三/§十五）：
#   Project canonical        = Story/Character/Shot 事实来源（本模块零写入）
#   AgentTask / Task Result  = Agent 执行事实（tasks/*.json 只读）
#   bridge_messages.jsonl    = Producer ↔ Specialist 协作事实（只读）
#   Producer session         = Producer 对话（sessions/*.json 只读）
#   workflows.json           = per-turn workflow 记录（只读）
#
# 投影保证（Phase 3 §十六/§十七）：
#   - 消息稳定 ID：user/producer 消息复用 session message_id；委派/结果用
#     "task:{task_id}:dispatch|result"（task_id 由 producer.py 生成且每委派唯一）。
#     snapshot / SSE live / refresh 三方用同一 ID 去重——绝不依赖 content 字符串相等。
#   - 顺序确定：全部消息按持久化时间戳 + kind 次序 + id 排序。所有 ts 均为落盘事实
#     （time.time() 单调时钟），同一 workflow 重载 N 次顺序一致。
#   - 纯派生：无缓存、无第二套存储；每次调用从盘上事实重建，删任何文件只会少显示
#     对应事实，不会产生平行事实。
#
# 状态来源纪律（§九：不伪造状态）：working 只来自 turn_registry（进程内活跃 turn）+
# status=running 的 AgentTask；后端重启导致 registry 清空时，running 任务投影为
# interrupted（已中断），绝不显示 working/completed。
#
# 并发就绪（§十）：消息/roster 数据模型按 task_id 索引、不假设单 activeAgent——
# 当前 Producer 串行委派（serial_delegate 闸门），未来并行调度无需改本投影。
from __future__ import annotations
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import turn_registry

log = logging.getLogger("production_room")

# Phase C2（28.md §26）：Scene Agent 加入顶层 Room Specialist roster。
# Director 内部 Scene Sub-Agent 不在此重复显示（它折叠在 Director 域）。
_SPECIALISTS = ("script_writer", "character_designer", "scene_designer", "director")

# 消息种类在同时刻 tie-break 时的确定次序：user → 委派 → 结果 → producer 汇报
_KIND_ORDER = {"user": 0, "delegation": 1, "specialist_result": 2, "producer": 3}

# structured_result.assistant_message 进 snapshot 的上限（§四十八：默认 summary，
# 完整回复展开在前端；超长截断并打 truncated 标记，绝不静默丢）
_FULL_REPLY_CAP = 6000


def _producer_dir(pdir: Path) -> Path:
    return Path(pdir) / "output" / "agents"


def _read_bridge_rows(pdir: Path) -> List[dict]:
    """bridge_messages.jsonl → 行 dict 列表（附 _seq=行号，从 1 起；坏行跳过）。
    行号即 append 序（文件只追加），是比 float ts 更硬的顺序事实。"""
    path = _producer_dir(pdir) / "producer" / "bridge_messages.jsonl"
    rows: List[dict] = []
    if not path.exists():
        return rows
    try:
        with path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    log.warning("bridge_messages 第 %d 行非法 JSON，投影跳过", i)
                    continue
                if isinstance(row, dict):
                    row["_seq"] = i
                    rows.append(row)
    except Exception as e:
        log.warning("bridge_messages 读取失败（%s），按空处理", e)
    return rows


def _load_delegate_tasks(pdir: Path, project: str) -> List[dict]:
    """tasks/*.json → Producer Bridge 委派 task（requester=producer 且 capability
    以 .delegate 结尾）。旧 AI-6B 手动委派表单（storyboard.inspect 等）不是 Bridge
    协作事实，不进 Production Room 时间线。"""
    root = _producer_dir(pdir) / "tasks"
    tasks: List[dict] = []
    if not root.exists():
        return tasks
    for f in sorted(root.glob("*.json")):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(t, dict):
            continue
        if t.get("requester_agent") != "producer":
            continue
        if not str(t.get("capability_id") or "").endswith(".delegate"):
            continue
        tasks.append(t)
    tasks.sort(key=lambda t: (t.get("created_at") or 0.0, t.get("task_id") or ""))
    return tasks


def _load_workflows(pdir: Path) -> Dict[str, dict]:
    path = _producer_dir(pdir) / "producer" / "workflows.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        wfs = data.get("workflows") if isinstance(data, dict) else None
        return wfs if isinstance(wfs, dict) else {}
    except Exception:
        return {}


def _pick_current_workflow(workflows: Dict[str, dict]) -> Optional[dict]:
    """当前 workflow：优先 running（最多一个活跃 turn；重启残留多个 running 时取最新），
    否则 created_at 最新的已结算 workflow。"""
    if not workflows:
        return None
    running = [w for w in workflows.values() if w.get("status") == "running"]
    pool = running or list(workflows.values())
    return max(pool, key=lambda w: (w.get("created_at") or 0.0, w.get("workflow_id") or ""))


def _producer_session(store) -> Optional[Any]:
    """Producer 的长存 project 级 conversation session（不创建）。"""
    try:
        sessions = [s for s in store.list()
                    if s.agent_id == "producer" and s.scope_type == "project"]
    except Exception:
        return None
    if not sessions:
        return None
    sessions.sort(key=lambda s: (s.updated_at or 0.0, s.session_id or ""))
    return sessions[-1]


def _norm_constraints(v: Any) -> List[str]:
    """bridge 行 constraints 容错：模型可能发 string（真实样例）或 list。"""
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, list):
        return [str(c) for c in v if str(c).strip()]
    return [str(v)]


def _result_status(t: dict) -> str:
    """task 的展示状态（structured_result.status 优先——它区分 stopped/not_configured，
    比 AgentTask.status（stopped→failed 粗化）更诚实）。"""
    sr = t.get("structured_result") or {}
    st = sr.get("status")
    if isinstance(st, str) and st:
        return st
    ts = t.get("status")
    return ts if isinstance(ts, str) and ts else "completed"


def _task_debug(t: Optional[dict]) -> dict:
    if not t:
        return {}
    return {
        "parent_task_id": t.get("parent_task_id"),
        "capability_id": t.get("capability_id"),
        "session_id": t.get("session_id"),
        "execution_ref": (t.get("structured_result") or {}).get("execution_ref")
                         or t.get("execution_ref"),
        "task_created_at": t.get("created_at"),
        "task_updated_at": t.get("updated_at"),
    }


def _task_workflow_id(t: dict, row: Optional[dict] = None) -> str:
    """workflow_id 来源优先级：bridge 行 > structured_result > task 顶层（旧数据兼容）。"""
    return ((row or {}).get("workflow_id")
            or (t.get("structured_result") or {}).get("workflow_id")
            or t.get("workflow_id") or "")


def _dispatch_message(t: dict, bridge_row: Optional[dict],
                      task_running: bool, live: bool) -> dict:
    """委派出向消息（Producer → Specialist）。内容源：bridge P→S 行（含 situation/
    constraints/scope 全 envelope）优先；运行中尚无 bridge 行时回退 AgentTask.instruction
    （dispatch 时已落盘，刷新恢复 §十八 依赖它）。
    状态：running 仅当 AgentTask=running 且 turn_registry 活跃（真实在跑）；
    AgentTask=running 但 registry 已空（后端重启）→ interrupted，绝不显示 working。"""
    task_id = t.get("task_id") or ""
    row = bridge_row or {}
    objective = (row.get("objective") or t.get("instruction") or "").strip()
    ts = t.get("created_at") or row.get("ts") or 0.0
    if task_running and live:
        status, live_flag = "running", True
    elif task_running:
        status, live_flag = "interrupted", False
    else:
        status, live_flag = _result_status(t), False
    msg = {
        "id": f"task:{task_id}:dispatch",
        "kind": "delegation",
        "timestamp": ts,
        "task_id": task_id,
        "workflow_id": _task_workflow_id(t, bridge_row),
        "sender": "producer",
        "recipient": t.get("target_agent") or row.get("receiver") or "",
        "content": objective,
        "situation": row.get("situation") or "",
        "constraints": _norm_constraints(row.get("constraints")),
        "scope": row.get("scope") or {},
        "status": status,
        "live": live_flag,
    }
    msg.update(_task_debug(t))
    return msg


def _result_message(t: dict, result_row: Optional[dict]) -> Optional[dict]:
    """Specialist → Producer 结果消息。真实源：Task Result（AgentTask.structured_result）
    优先（含 verified/changed_refs/n_tool_calls），bridge S→P 行补 summary（截 400）。"""
    task_id = t.get("task_id") or ""
    sr = t.get("structured_result") or {}
    row = result_row or {}
    status = _result_status(t)
    summary = (row.get("summary") or sr.get("summary") or t.get("result_summary") or "").strip()
    if not summary and status == "running":
        return None
    full = (sr.get("assistant_message") or "").strip()
    truncated = False
    if len(full) > _FULL_REPLY_CAP:
        full = full[:_FULL_REPLY_CAP]
        truncated = True
    ts = row.get("ts") or t.get("updated_at") or t.get("created_at") or 0.0
    changed = sr.get("changed_refs") or row.get("changed_refs") or {}
    msg = {
        "id": f"task:{task_id}:result",
        "kind": "specialist_result",
        "timestamp": ts,
        "task_id": task_id,
        "workflow_id": _task_workflow_id(t, result_row),
        "sender": t.get("target_agent") or "",
        "recipient": "producer",
        "content": summary,
        "full_reply": full or None,
        "full_reply_truncated": truncated,
        "status": status,
        "verified": sr.get("verified") if isinstance(sr, dict) else None,
        "changed_refs": changed if isinstance(changed, dict) else {},
        "warnings": sr.get("warnings") or row.get("warnings") or [],
        "n_tool_calls": sr.get("n_tool_calls"),
        "status_running": bool(t.get("status") == "running"),
    }
    msg.update(_task_debug(t))
    return msg


def _orphan_messages(row: dict) -> List[dict]:
    """bridge 行存在但 AgentTask 文件缺失（人为删档等）：仍投影（数据在即显示），
    用行号/ts 做时间，无 verified/debug。正常路径永远走 task 主干。"""
    task_id = row.get("task_id") or ""
    if not task_id:
        return []
    sender = row.get("sender") or ""
    ts = row.get("ts") or 0.0
    if sender == "producer":
        return [{
            "id": f"task:{task_id}:dispatch", "kind": "delegation",
            "timestamp": ts, "task_id": task_id,
            "workflow_id": row.get("workflow_id") or "",
            "sender": "producer", "recipient": row.get("receiver") or "",
            "content": (row.get("objective") or "").strip(),
            "situation": row.get("situation") or "",
            "constraints": _norm_constraints(row.get("constraints")),
            "scope": row.get("scope") or {},
            "status": "dispatched", "live": False,
        }]
    return [{
        "id": f"task:{task_id}:result", "kind": "specialist_result",
        "timestamp": ts, "task_id": task_id,
        "workflow_id": row.get("workflow_id") or "",
        "sender": sender, "recipient": "producer",
        "content": (row.get("summary") or "").strip(),
        "full_reply": None, "full_reply_truncated": False,
        "status": row.get("status") or "completed",
        "verified": None,
        "changed_refs": row.get("changed_refs") or {},
        "warnings": row.get("warnings") or [],
        "n_tool_calls": None,
        "status_running": False,
    }]


def _session_messages(session) -> List[dict]:
    """Producer session user/assistant 消息 → 群聊消息。message_id 是稳定 ID；
    旧消息（无 message_id，AI-7D-R 之前落盘）回退 msg:{created_at} 派生 ID。"""
    out: List[dict] = []
    for m in (session.messages or []):
        if m.role not in ("user", "assistant"):
            continue
        mid = m.message_id or f"msg:{m.created_at}"
        if m.role == "user":
            out.append({"id": mid, "kind": "user", "timestamp": m.created_at,
                        "sender": "user", "recipient": "producer",
                        "content": m.content or ""})
        else:
            meta = m.meta if isinstance(m.meta, dict) else {}
            status = m.status or "completed"
            out.append({
                "id": mid, "kind": "producer", "timestamp": m.created_at,
                "sender": "producer", "recipient": "user",
                "content": m.content or "", "status": status,
                "intent": meta.get("intent"),
                "linked_task_ids": meta.get("linked_task_ids") or [],
            })
    return out


def _participant_specialist(agent_id: str, tasks: List[dict],
                            active_sids: set) -> dict:
    """单个 Specialist 的 roster 状态（只来自 AgentTask + turn_registry 真实事实）。"""
    if not tasks:
        return {"agent_id": agent_id, "status": "idle", "not_called": True}
    last = tasks[-1]   # replan 重复委派同一 agent 时取最后一次
    live = bool(last.get("session_id")) and last.get("status") == "running" \
        and last["session_id"] in active_sids
    sr = last.get("structured_result") or {}
    if last.get("status") == "running":
        status = "working" if live else "interrupted"
    else:
        # stopped→cancelled；completed/failed/not_configured 原样透传（诚实状态）
        st = _result_status(last)
        status = "cancelled" if st == "stopped" else st
    p = {
        "agent_id": agent_id, "status": status,
        "task_id": last.get("task_id"),
        "workflow_id": _task_workflow_id(last),
        "verified": sr.get("verified"),
        "changed_refs": sr.get("changed_refs") or {},
        "n_tool_calls": sr.get("n_tool_calls"),
        "summary": (last.get("result_summary") or "")[:200],
        "updated_at": last.get("updated_at"),
    }
    return p


def build_room_projection(rt, project: str) -> dict:
    """从持久事实构建 Production Room 投影（纯只读，零写入）。"""
    pdir = Path(rt._resolve_project_dir(project))
    store = rt._session_store(project)

    session = _producer_session(store)
    session_id = session.session_id if session else None
    producer_active = bool(session_id and turn_registry.is_active(project, session_id))

    tasks = _load_delegate_tasks(pdir, project)
    rows = _read_bridge_rows(pdir)
    workflows = _load_workflows(pdir)
    current_wf = _pick_current_workflow(workflows)

    # registry：活跃 specialist session 集合（working 判定的唯一 live 源）
    active_sids = set()
    for t in tasks:
        sid = t.get("session_id")
        if sid and t.get("status") == "running" and turn_registry.is_active(project, sid):
            active_sids.add(sid)

    # bridge 行按 task_id 分组（P→S / S→P 各取第一条；每委派恰两行，同 (task_id,sender) 唯一）
    by_task: Dict[str, Dict[str, dict]] = {}
    seen_rows: set = set()
    for row in rows:
        tid = row.get("task_id") or ""
        if not tid:
            continue
        slot = by_task.setdefault(tid, {})
        key = "dispatch" if row.get("sender") == "producer" else "result"
        if key not in slot:
            slot[key] = row
        seen_rows.add((tid, key))

    messages: List[dict] = []
    if session is not None:
        messages.extend(_session_messages(session))

    covered: set = set()
    for t in tasks:
        tid = t.get("task_id") or ""
        covered.add(tid)
        slot = by_task.get(tid) or {}
        running = t.get("status") == "running"
        live = running and bool(t.get("session_id")) and t["session_id"] in active_sids
        messages.append(_dispatch_message(t, slot.get("dispatch"),
                                          task_running=running, live=live))
        result = _result_message(t, slot.get("result"))
        if result is not None:
            messages.append(result)

    # 孤儿 bridge 行（task 文件缺失）：仍投影，不丢协作事实
    for tid, slot in by_task.items():
        if tid in covered:
            continue
        if "dispatch" in slot:
            messages.extend(_orphan_messages(slot["dispatch"]))
        if "result" in slot:
            messages.extend(_orphan_messages(slot["result"]))

    messages.sort(key=lambda m: (m.get("timestamp") or 0.0,
                                 _KIND_ORDER.get(m.get("kind"), 9),
                                 m.get("id") or ""))

    # participants：Producer + 三 Specialist；状态只来自 AgentTask/registry/session
    participants: List[dict] = [{
        "agent_id": "producer",
        "status": ("working" if producer_active
                   else "interrupted" if (current_wf or {}).get("status") == "running"
                   else ("idle" if session is None or not (session.messages)
                         else _producer_idle_status(session))),
    }]
    for aid in _SPECIALISTS:
        participants.append(_participant_specialist(
            aid, [t for t in tasks if t.get("target_agent") == aid], active_sids))

    return {
        "room_id": "producer",
        "project_id": project,
        "session_id": session_id,
        "turn_active": producer_active,
        "generated_at": time.time(),
        "workflow": current_wf,
        "participants": participants,
        "messages": messages,
    }


def _producer_idle_status(session) -> str:
    """Producer 非 working 时的诚实状态：最后一条 assistant 消息的真实 status。"""
    for m in reversed(session.messages or []):
        if m.role == "assistant":
            st = m.status
            if st is None:
                return "completed"
            return {"stopped": "cancelled", "not_configured": "idle"}.get(st, st)
    return "idle"
