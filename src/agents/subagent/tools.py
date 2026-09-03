# src/agents/subagent/tools.py
# MD 侧 4 工具（Part 2 §2.4）+ 挂载门控（bit-for-bit 保证）。
#
#   scene.delegate(scene_id, task_brief, signal)   长任务通道（仿 generate 分支）
#   proposal.read(proposal_id)                     只读全文
#   proposal.commit(proposal_id, decision_rationale, exclude_item_refs?, resume?)
#                                                   进单写 fence（DIRECTOR_WRITE_OPERATIONS）
#   proposal.reject(proposal_id, kind, reasons?, decision_rationale?)
#                                                   只写 proposal store，不进 fence
#
# 开关 subagent_enabled（settings.json 顶层键，默认 false）：关闭时【不调用
# mount_subagent_tools】→ DIRECTOR_TOOLS / TOOL_EXECUTORS / 写集合 / 系统提示
# 逐字节不变（主链路 bit-for-bit）。开启时 _build_system 追加 SUBAGENT_SYSTEM_SECTION。
#
# 资格闸门（§2.1 四检查，确定性 veto）：信号依据 / 预算（本 turn <3 且累计 <6
# 硬上限，用户显式超限也拒但向用户明示）/ liveness / 单段单飞。

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from src.agents.runtime import AgentRuntime, AgentError

from . import registry as reg
from . import fingerprints as fp

log = logging.getLogger("agents.subagent.tools")

# 单 turn delegation 预算（§2.1；进程内计数，director_turn 边界重置）
MAX_DELEGATIONS_PER_TURN = 3

SUBAGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "scene.delegate",
            "description": (
                "把某个故事段的【大规模局部设计/重做】委派给该段的 Scene Agent"
                "（独立一次性设计实例）：它读当前 canonical 后产出该段的目标端态"
                "提案（desired end state，不直接写 canonical）。适合：单段大改/"
                "扩写/重排/多轮返工压力大的段。不适合：单点小改（直接用写工具）、"
                "全片级任务（全局层自己做）。signal.kind：pressure=该段已积累多轮"
                "结构修改压力；declared=本轮任务明确是大规模局部重设计；"
                "user_explicit=用户明确要求委派。evidence 必须给出依据（压力来源"
                "或任务复杂度说明或用户原话）。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_id": {
                        "type": "string",
                        "description": "目标故事段（stable scene_id，非位置序号）"},
                    "task_brief": {
                        "type": "string",
                        "description": "任务书：目标、范围、风格口径、需要遵守的边界"},
                    "signal": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string",
                                     "enum": ["pressure", "declared", "user_explicit"]},
                            "evidence": {"type": "string"},
                        },
                        "required": ["kind", "evidence"],
                    },
                    "locked_border_constraints": {
                        "type": "array",
                        "description": (
                            "锁定边界约束（可选，≤4 条；Part 2 §2.3）：本次任务中"
                            "不可违反的出入口条件。每条 {applies_to: entry|exit|both,"
                            " statement}。系统自动编号 bc_1.. 并注入任务书；实例必须"
                            "逐条回应（constraint_responses），认为必须改边界走"
                            " border_change_request。"),
                        "items": {
                            "type": "object",
                            "properties": {
                                "applies_to": {"type": "string",
                                               "enum": ["entry", "exit", "both"]},
                                "statement": {"type": "string"},
                            },
                            "required": ["applies_to", "statement"],
                        },
                    },
                },
                "required": ["scene_id", "task_brief", "signal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proposal.read",
            "description": (
                "读取一份提案的完整内容（end_state 全文 + 理由 + 边界修改请求）。"
                "reconcile 需要细看时用；平时看 scene.delegate 返回的结构化摘要即可。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"},
                },
                "required": ["proposal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proposal.commit",
            "description": (
                "把你裁决通过的提案提交为 canonical（确定性 DiffEngine 执行："
                "pre-detach→delete→create→reorder→update，每步最新指纹）。commit 前"
                "请确认：end_state 与你的全局判断一致、每条 constraint_response 可信、"
                "border_change_requests 已裁决。decision_rationale 必填（进入全局决策"
                "记忆）。basis 过期会返回 md_rebase：走 proposal.reject(kind='rebase')"
                "后重新 scene.delegate，或用写工具手工完成。resume=true 仅用于恢复"
                "上次中断的 commit（对当前状态重算剩余变换）。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"},
                    "decision_rationale": {
                        "type": "string",
                        "description": "你的裁决理由（必填；为什么采纳/部分采纳）"},
                    "exclude_item_refs": {
                        "type": "array", "items": {"type": "integer"},
                        "description": "partial adoption：要排除的 end_state 数组下标"
                                       "（从 0 起）。排除项的后继若 chain=true 会被拒绝"},
                    "resume": {"type": "boolean",
                               "description": "恢复中断的 commit（basis 豁免，重算剩余）"},
                },
                "required": ["proposal_id", "decision_rationale"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proposal.reject",
            "description": (
                "否决提案（记录决策，不改 canonical）。kind：rejected=设计否决；"
                "rebase=端态基于过期版本，需重新委派。decision_rationale 必填。"
                "被否决的设计理由不会进入该段的决策记忆（只留提案存档）。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"},
                    "kind": {"type": "string", "enum": ["rejected", "rebase"]},
                    "reasons": {"type": "array", "items": {"type": "string"}},
                    "decision_rationale": {"type": "string"},
                },
                "required": ["proposal_id", "kind", "decision_rationale"],
            },
        },
    },
]

SUBAGENT_SYSTEM_SECTION = """## 段级委派（Scene Agent，可选）
- 对【单段大规模设计/重做】，可调 scene.delegate 委派该段的 Scene Agent 产出目标端态提案（它没有写权限，只提案）。
- 何时委派：该段已经历多轮结构修改（pressure），或本轮明确是大规模局部重设计（declared），或用户明确要求（user_explicit）。单点小改、全片级任务（节奏曲线/对白风格统一等）不要委派——后者是你自己的全局职责。
- scene.delegate 返回结构化摘要（镜头增删移改计数 + border_change_requests + 提案 id）。需要细看再 proposal.read。
- 你是唯一的 reconcile 者：对照你的全局判断裁决（accept / partial=exclude_item_refs / reject）。commit 由确定性 DiffEngine 执行，你不需要手工展开操作。
- basis 过期（md_rebase）时：proposal.reject(kind="rebase") 后重新 scene.delegate（任务书里带上变更说明），或直接用写工具手工完成。
- 每条 border_change_request 由你裁决（接受=约束下轮生效；拒绝=说明理由），裁决理由会进入全局决策记忆。"""


# ============ 开关（单一读取入口）============
def is_subagent_enabled(rt: AgentRuntime) -> bool:
    """settings.json 顶层键 subagent_enabled（默认 false）。"""
    try:
        return bool(rt.settings.load().get("subagent_enabled"))
    except Exception:
        return False


# ============ 挂载 / 卸载（幂等；unmount 供测试还原）============
_MOUNTED = False


def mount_subagent_tools() -> bool:
    """向 director 挂载 4 工具（幂等）。返回是否发生了挂载。
    挂载物：DIRECTOR_TOOLS / TOOL_EXECUTORS / DIRECTOR_WRITE_OPERATIONS
    （proposal.commit）/ DIRECTOR_LONG_TASK_TOOLS（scene.delegate）/
    _TOOL_LABELS / _SUBAGENT_SYSTEM_SUFFIX（_build_system 追加段）。"""
    global _MOUNTED
    from src.agents import director as D
    if _MOUNTED:
        return False
    names = {t["function"]["name"] for t in D.DIRECTOR_TOOLS}
    for t in SUBAGENT_TOOLS:
        if t["function"]["name"] not in names:
            D.DIRECTOR_TOOLS.append(t)
    D.TOOL_EXECUTORS.update(_SUBAGENT_EXECUTORS)
    D.DIRECTOR_WRITE_OPERATIONS.add("proposal.commit")
    D.DIRECTOR_LONG_TASK_TOOLS.add("scene.delegate")
    D._TOOL_LABELS.update(_SUBAGENT_LABELS)
    D._SUBAGENT_SYSTEM_SUFFIX = SUBAGENT_SYSTEM_SECTION
    _MOUNTED = True
    return True


def unmount_subagent_tools() -> bool:
    """测试用还原（幂等）。卸载后 director 模块状态与未挂载基线一致。"""
    global _MOUNTED
    from src.agents import director as D
    if not _MOUNTED:
        return False
    names = {t["function"]["name"] for t in SUBAGENT_TOOLS}
    D.DIRECTOR_TOOLS[:] = [t for t in D.DIRECTOR_TOOLS
                           if t["function"]["name"] not in names]
    for n in names:
        D.TOOL_EXECUTORS.pop(n, None)
    D.DIRECTOR_WRITE_OPERATIONS.discard("proposal.commit")
    D.DIRECTOR_LONG_TASK_TOOLS.discard("scene.delegate")
    for n in _SUBAGENT_LABELS:
        D._TOOL_LABELS.pop(n, None)
    D._SUBAGENT_SYSTEM_SUFFIX = None
    _MOUNTED = False
    return True


def mounted() -> bool:
    return _MOUNTED


_SUBAGENT_LABELS = {
    "scene.delegate": "委派段设计", "proposal.read": "读取提案",
    "proposal.commit": "提交提案", "proposal.reject": "否决提案",
}


# ============ executors ============
def _parse(arguments: str) -> dict:
    try:
        data = json.loads(arguments or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _gate_fail(code: str, message: str, fix_hint: str) -> dict:
    return {"ok": False, "operation": "scene.delegate", "code": code,
            "error": message, "fix_hint": fix_hint}


async def _execute_scene_delegate(rt: AgentRuntime, project: str,
                                  arguments: str, instruction, message_id,
                                  event_cb=None, cancel_event=None) -> dict:
    """资格闸门（§2.1）→ contract 构造 → 一次性实例（instance.py）→ 摘要。

    长任务通道：event_cb 包装（tool.progress）/ cancel_event 协作取消。
    同步模型（Part 2 §0.1 Model A）：delegation 在 MD turn 内同步完成。
    instance 的 import 放在闸门之后——拒绝路径不依赖实例模块。"""
    args = _parse(arguments)
    scene_id = str(args.get("scene_id") or "").strip()
    task_brief = str(args.get("task_brief") or "").strip()
    signal = args.get("signal") or {}
    kind = str(signal.get("kind") or "").strip()
    evidence = str(signal.get("evidence") or "").strip()

    pdir = rt._resolve_project_dir(project)
    store = reg.SubagentStore(pdir, project)

    # ---- 闸门 1：信号依据 ----
    if kind not in ("pressure", "declared", "user_explicit"):
        return _gate_fail(
            "bad_signal", f"signal.kind 非法：{kind!r}",
            "kind ∈ {pressure, declared, user_explicit}，且 evidence 必填")
    if kind == "pressure":
        if not store.pressure_ready(scene_id):
            rec = store.scene_record(scene_id) or {}
            return _gate_fail(
                "pressure_not_ready",
                f"段 {scene_id} 的结构写压力 {rec.get('structural_since_delegation', 0)}"
                f" 未达阈值（{reg.SPAWN_PRESSURE_THRESHOLD}）",
                "pressure 信号需要该段真实积累的结构修改压力；小改请直接用写工具，"
                "或改用 declared 并给出任务复杂度说明")
    elif not evidence:
        return _gate_fail(
            "missing_evidence", f"{kind} 信号缺 evidence",
            "给出依据：任务复杂度说明（declared）或用户原话（user_explicit）")

    # ---- 闸门 2：预算（本 turn <3；累计 <6 硬上限，用户显式超限也拒但明示）----
    if reg.turn_budget_used() >= MAX_DELEGATIONS_PER_TURN:
        return _gate_fail(
            "turn_budget_exhausted",
            f"本 turn 委派已达上限（{MAX_DELEGATIONS_PER_TURN}）",
            "剩余工作请在主会话直接执行，或下轮再委派")
    reg_record = store._load_registry()
    total = int(reg_record.get("total_delegations", 0))
    if total >= reg.HARD_DELEGATION_CAP:
        extra = ("（注意：这是用户明确要求的委派，但硬上限同样生效——请向用户"
                 "如实说明）" if kind == "user_explicit" else "")
        return _gate_fail(
            "hard_cap_reached",
            f"项目累计委派已达硬上限（{reg.HARD_DELEGATION_CAP}）{extra}",
            "硬上限保护：请在主会话直接执行，或清理后由用户决定是否提高上限")

    # ---- 闸门 3：liveness ----
    from src.core.store import ProjectStore
    proj = ProjectStore(pdir).load()
    scene = next((s for s in proj.scenes if s.scene_id == scene_id), None)
    if scene is None:
        return _gate_fail(
            "scene_not_found", f"段 {scene_id} 不在当前 canonical",
            "用 story.list 核对当前段集（可能刚被 Script Writer 删除或改名）")

    # ---- 闸门 4：单段单飞 ----
    delegation_id = reg.new_delegation_id()
    if not reg.acquire_handle(scene_id, delegation_id):
        return _gate_fail(
            "scene_busy", f"段 {scene_id} 已有在飞 delegation",
            "等本次返回后再委派同段；其它段不受影响")
    reg.bump_turn_budget()
    store.ensure_scene(scene_id)
    store.bump_delegation_count(scene_id)

    basis = fp.compute_scene_basis(scene, proj.scenes)
    # locked_border_constraints（Part 2 §2.3）：确定性校验 + 自动编号 bc_1..
    locked = []
    for i, raw in enumerate(args.get("locked_border_constraints") or []):
        if not isinstance(raw, dict):
            return _gate_fail(
                "bad_constraint", f"locked_border_constraints[{i}] 不是对象",
                "每条约束是 {applies_to: entry|exit|both, statement}")
        applies = str(raw.get("applies_to") or "").strip()
        statement = str(raw.get("statement") or "").strip()
        if applies not in ("entry", "exit", "both"):
            return _gate_fail(
                "bad_constraint",
                f"locked_border_constraints[{i}].applies_to 非法：{applies!r}",
                "applies_to ∈ {entry（入口）, exit（出口）, both}")
        if not statement:
            return _gate_fail(
                "bad_constraint",
                f"locked_border_constraints[{i}].statement 为空",
                "写出具体不可违反的出入口条件（如：出口必须停在暴雨未歇）")
        locked.append({"constraint_id": f"bc_{i + 1}",
                       "applies_to": applies, "statement": statement})
    if len(locked) > 4:
        return _gate_fail(
            "bad_constraint", f"锁定边界约束 {len(locked)} 条超上限（≤4）",
            "合并同类约束或拆分 delegation")
    delegation = {
        "delegation_id": delegation_id, "scene_id": scene_id, "project": project,
        "status": "dispatched", "proposal_id": None,
        "origin": {"kind": kind, "evidence": evidence},
        "task_brief": task_brief,
        "locked_border_constraints": locked,
        "basis": {**basis, "computed_at": time.time()},
        "created_at": time.time(),
    }
    store.save_delegation(delegation)
    try:
        from .instance import run_scene_agent_instance
        result = await run_scene_agent_instance(
            rt, project, delegation, event_cb=event_cb,
            cancel_event=cancel_event)
    except Exception as e:
        log.exception("scene.delegate 实例异常（%s）", delegation_id)
        delegation["status"] = "aborted"
        delegation["aborted_reason"] = f"instance_error: {type(e).__name__}"
        store.save_delegation(delegation)
        reg.release_handle(scene_id, delegation_id)
        return {"ok": False, "operation": "scene.delegate", "code": "instance_error",
                "error": f"委派实例执行异常：{type(e).__name__}: {e}",
                "fix_hint": "可重试一次；持续失败请在主会话直接执行",
                "delegation_id": delegation_id, "scene_id": scene_id}
    finally:
        reg.release_handle(scene_id, delegation_id)

    result.setdefault("operation", "scene.delegate")
    result.setdefault("scene_id", scene_id)
    return result


def _execute_proposal_read(rt: AgentRuntime, project: str, arguments: str,
                           instruction, message_id) -> dict:
    args = _parse(arguments)
    pid = str(args.get("proposal_id") or "").strip()
    pdir = rt._resolve_project_dir(project)
    store = reg.SubagentStore(pdir, project)
    p = store.load_proposal(pid)
    if p is None:
        return {"ok": False, "operation": "proposal.read", "code": "not_found",
                "error": f"提案不存在：{pid}",
                "fix_hint": "proposal_id 来自 scene.delegate 的返回"}
    return {"ok": True, "operation": "proposal.read", "proposal": p,
            "note": "提案全文（end_state + 理由 + 边界请求）。reconcile 后用 "
                    "proposal.commit / proposal.reject 裁决。"}


def _execute_proposal_commit(rt: AgentRuntime, project: str, arguments: str,
                             instruction, message_id) -> dict:
    """commit = DiffEngine 确定性执行（零 LLM）。成功后：E2 记忆入库 +
    全局决策记忆 + work_memory 摘要 + registry 结构计数（治理扁平化的
    executed_ops 由 director_turn 收尾统一计入项目级双计数器——勿双计）。"""
    from .diff_engine import run_diff_commit
    args = _parse(arguments)
    pid = str(args.get("proposal_id") or "").strip()
    rationale = str(args.get("decision_rationale") or "").strip()
    exclude = args.get("exclude_item_refs")
    resume = bool(args.get("resume"))
    if not rationale:
        return {"ok": False, "operation": "proposal.commit",
                "code": "missing_decision_rationale",
                "error": "decision_rationale 必填（裁决理由进入全局决策记忆）",
                "fix_hint": "补上你为什么采纳/部分采纳这份提案的理由后重试"}
    pdir = rt._resolve_project_dir(project)
    store = reg.SubagentStore(pdir, project)
    p = store.load_proposal(pid)
    if p is None:
        return {"ok": False, "operation": "proposal.commit", "code": "not_found",
                "error": f"提案不存在：{pid}",
                "fix_hint": "proposal_id 来自 scene.delegate 的返回"}
    delegation = store.load_delegation(p.get("delegation_id") or "")
    if delegation is None:
        return {"ok": False, "operation": "proposal.commit",
                "code": "orphan_proposal", "error": "提案无对应 delegation 记录",
                "fix_hint": "提案存档损坏；请在主会话直接执行修改"}
    status = delegation.get("status")
    if status not in ("proposed", "committing", "interrupted_commit", "partial"):
        if status == "committed":
            return {"ok": False, "operation": "proposal.commit",
                    "code": "already_committed",
                    "error": "该提案已 commit（不可重复提交）",
                    "fix_hint": "需要再改请发起新的 scene.delegate 或用写工具"}
        return {"ok": False, "operation": "proposal.commit",
                "code": "bad_delegation_status",
                "error": f"delegation 状态为 {status}，不可 commit",
                "fix_hint": "只处理已产出提案（proposed）或中断恢复"
                            "（interrupted_commit + resume=true）的委派"}
    if status == "interrupted_commit" and not resume:
        return {"ok": False, "operation": "proposal.commit",
                "code": "resume_required",
                "error": "上次 commit 中途中断（canonical 停在合法中间态）",
                "fix_hint": "恢复用 proposal.commit(resume=true)（对当前状态重算"
                            "剩余变换）；或用写工具手工收尾"}
    scene_id = p.get("scene_id")

    # journal 回调：三时点更新 delegation 记录（崩溃窗口可见）
    def _journal(phase: str, payload: dict) -> None:
        d = store.load_delegation(delegation_id) or delegation
        if phase == "committing":
            d["status"] = "committing"
            d["journal"] = {"ops_plan": payload.get("ops_plan"),
                            "started_at": time.time()}
        elif phase == "committed":
            d["status"] = "committed"
            d["journal"] = {"executed": payload.get("executed")}
        elif phase == "interrupted":
            d["status"] = "interrupted_commit"
            d["journal"] = {"reason": payload.get("reason"),
                            "executed": payload.get("executed")}
        store.save_delegation(d)

    delegation_id = delegation["delegation_id"]
    receipt = run_diff_commit(
        rt, project, scene_id, p,
        exclude_item_refs=exclude, resume=resume,
        instruction=f"proposal.commit({pid}) via Main Director",
        message_id=message_id, journal_cb=_journal)
    receipt["proposal_id"] = pid
    receipt["decision_rationale"] = rationale
    if not receipt.get("ok"):
        return receipt

    # ---- 成功后处理（E2 入库 / 全局记忆 / work_memory / registry 计数）----
    try:
        from src.core.store import ProjectStore as _PS
        proj = _PS(pdir).load()
        scene = next((s for s in proj.scenes if s.scene_id == scene_id), None)
        if scene is not None:
            from .diff_engine import normalize_end_state, effective_end_state
            eff = effective_end_state(normalize_end_state(p.get("end_state")),
                                       exclude)
            basis_fp = (p.get("basis") or {}).get("scene_slice_fingerprint") or ""
            entries = reg.make_memory_entries(
                scene_id, delegation_id, pid, basis_fp, eff,
                str(p.get("design_notes") or ""), scene)
            store.append_memory(scene_id, entries)
    except Exception:
        log.exception("decision memory 入库失败（不阻断 commit）")
    try:
        store.append_global_memory(reg.make_global_entry(
            f"[{scene_id}] 采纳提案 {pid}：{rationale}", proposal_id=pid))
    except Exception:
        log.exception("全局决策记忆写入失败（不阻断 commit）")
    try:
        n = receipt.get("n_created", 0) + receipt.get("n_deleted", 0) \
            + receipt.get("n_moved", 0) + receipt.get("n_updated", 0)
        rt.record_work(project, "change",
                       f"Director 通过 Scene Agent 提案改写了 {scene_id}"
                       f"（{n} 项操作，提案 {pid}）",
                       participants=["director", "scene_agent"],
                       affected_domains=["storyboard"],
                       references={"proposal_id": pid, "scene_id": scene_id},
                       status="completed")
    except Exception:
        pass
    try:
        rec = store.scene_record(scene_id) or {}
        store.update_scene_record(
            scene_id,
            accepted_proposals=int(rec.get("accepted_proposals", 0)) + 1)
        # 注意：structural_since_delegation 不在此累加——turn 收尾的扁平化
        # 提取（receipts + executed_ops）是唯一计费点，此处再加会双计。
    except Exception:
        log.exception("registry 计数更新失败（不阻断 commit）")
    return receipt


def _execute_proposal_reject(rt: AgentRuntime, project: str, arguments: str,
                             instruction, message_id) -> dict:
    """reject 只写 proposal store（不进单写 fence、不碰 canonical）；
    rationale 不入库（E2：rejected/stale 永不入记忆）。"""
    args = _parse(arguments)
    pid = str(args.get("proposal_id") or "").strip()
    kind = str(args.get("kind") or "").strip()
    rationale = str(args.get("decision_rationale") or "").strip()
    reasons = args.get("reasons") or []
    if kind not in ("rejected", "rebase"):
        return {"ok": False, "operation": "proposal.reject", "code": "bad_kind",
                "error": f"kind 非法：{kind!r}",
                "fix_hint": "kind ∈ {rejected（设计否决）, rebase（basis 过期需重委派）}"}
    if not rationale:
        return {"ok": False, "operation": "proposal.reject",
                "code": "missing_decision_rationale",
                "error": "decision_rationale 必填",
                "fix_hint": "补上否决理由（只留提案存档，不入段决策记忆）"}
    pdir = rt._resolve_project_dir(project)
    store = reg.SubagentStore(pdir, project)
    p = store.load_proposal(pid)
    if p is None:
        return {"ok": False, "operation": "proposal.reject", "code": "not_found",
                "error": f"提案不存在：{pid}", "fix_hint": "核对 proposal_id"}
    p["decision"] = {"kind": kind, "reasons": reasons,
                     "decision_rationale": rationale,
                     "decided_at": time.time()}
    store.save_proposal(p)
    d = store.load_delegation(p.get("delegation_id") or "")
    if d is not None:
        d["status"] = "rejected"
        store.save_delegation(d)
    try:
        store.append_global_memory(reg.make_global_entry(
            f"[{p.get('scene_id')}] 否决提案 {pid}（{kind}）：{rationale}",
            proposal_id=pid, kind=f"reject_{kind}"))
    except Exception:
        log.exception("全局决策记忆写入失败（不阻断 reject）")
    note = ("已否决（设计理由只留提案存档，不进入该段决策记忆）。"
            if kind == "rejected" else
            "已按 rebase 否决——请重新 scene.delegate（任务书带上变更说明），"
            "或用写工具手工完成。")
    return {"ok": True, "operation": "proposal.reject", "proposal_id": pid,
            "kind": kind, "note": note}


_SUBAGENT_EXECUTORS = {
    "scene.delegate": _execute_scene_delegate,
    "proposal.read": _execute_proposal_read,
    "proposal.commit": _execute_proposal_commit,
    "proposal.reject": _execute_proposal_reject,
}
