# src/agents/subagent/instance.py
# Scene Agent 一次性工作实例执行链（Part 2 §3）。
#
#   create_ephemeral("director", "scene_agent", f"{scene_id}:{delegation_id}")
#   → 校准注入包（§3.2：preamble + 段状态头[含接力边与悬空标注] + BorderPack
#     渲染[window_k=2] + locked constraints + brief + 有效 decision memory[≤K，
#     含隔离计数] + 任务书 + 提案格式说明）
#   → _run_tool_loop(tools_whitelist=SCENE_AGENT_TOOLS, extra_executors=
#     {"proposal.submit": 闭包}, max_steps=INSTANCE_MAX_STEPS)
#     rev3（2026-09-04）：wall_clock_s 不再传递（renewable window 取代墙钟硬停）；
#     max_steps 语义改为 renewable window（默认 1024，窗口用满续期，次数不限）。
#   → proposal.submit 的 E1–E6 确定性校验（§4.4）→ proposal store 落盘
#   → 返回给 MD 的结构化摘要（≤500 字量级，Part 1 §10.2）
#
# SCENE_AGENT_TOOLS = DIRECTOR_READ_TOOLS ∪ {"proposal.submit"}（不含
# web.search——有意设计：设计实例需要的是 canonical 不是外部资料；与
# REVIEW_READ_TOOLS 的差异是设计决策非遗漏）。物理无写工具（硬约束）。
#
# basis 防伪（E4 实现口径）：scene_slice_fingerprint 是派生值、不出现在任何
# 工具返回中——防伪由其组件承担：basis.scene_content_token 必须 ∈ 本实例
# story.read 真实返回过的 revision 集合，basis.shots_structure_token 必须 ∈
# shot.list 真实返回过的 revision 集合；且 scene_slice == 当前现算值（防过期）。
#
# 零 read 证据（E5）：trace 中 ≥1 次段级读（shot.list 或本段 shot.read）；
# n_read_calls 从 trace 统计，不信 LLM 自报。

from __future__ import annotations

import json
import logging
from typing import Optional

from src.agents.runtime import AgentRuntime
from src.core.store import ProjectStore
from src.agents.models import AgentMessage

from . import registry as reg
from . import fingerprints as fp
from .diff_engine import (normalize_end_state, normalize_removed_rationales,
                          normalize_basis, validate_end_state, DiffError)

log = logging.getLogger("agents.subagent.instance")

# 实例预算（Part 2 §11 建议值；rev3：max_steps 改为 renewable window 语义）
INSTANCE_MAX_STEPS = 1024
# rev3（2026-09-04）：wall_clock_s 不再作为实例硬停——取消传递，由 renewable
# window + self-check 承载长任务。director 的 _WallClockExceeded 机制保留（默认
# None），下方 except 分支降为防御性不可达。常量已删除（无引用）。
BORDER_WINDOW_K = 2

SCENE_AGENT_TOOLS = None   # 懒初始化（import director 时避免环）


def _scene_agent_tools() -> set:
    global SCENE_AGENT_TOOLS
    if SCENE_AGENT_TOOLS is None:
        from src.agents.director import DIRECTOR_READ_TOOLS
        # Phase 39（39.md §6；用户 2026-09-25 名单定版）：校准与帧级工具是 Director
        # 连续性裁定链的专属工具——即便 Scene Agent 作为第五 Specialist 条件参与
        # 校准协作，也不拿 seam/trace/帧工具（上游场景 owner 只修场景真相）。
        _P39_EXCLUDED = {"calibration.seam_measure", "calibration.trace_read",
                         "video.extract_frame", "visual.sample_frames"}
        SCENE_AGENT_TOOLS = (set(DIRECTOR_READ_TOOLS) - _P39_EXCLUDED
                             | {"proposal.submit"})
    return SCENE_AGENT_TOOLS


# ============ 注入包渲染（§3.2 模板）============
def _render_scene_state_header(scene) -> str:
    """段状态头：内容字段 + 有序镜头（id/描述摘要/接力边/悬空标注/每镜指纹）。"""
    from src.agents.director import _shot_token
    lines = [f"# 段状态头（系统生成；当前状态以此为准，不是对话回忆）",
             f"## 本段内容（scene_id={scene.scene_id}）",
             f"- 标题：{scene.title or '（无）'}｜地点：{scene.location or '（无）'}"
             f"｜时段：{scene.time_of_day or '（无）'}",
             f"- 摘要：{scene.summary or '（无）'}",
             f"- 角色：{'、'.join(scene.characters or []) or '（无）'}",
             f"## 本段镜头（有序，{len(scene.shots or [])} 个）"]
    ids = [sh.shot_id for sh in (scene.shots or [])]
    for i, sh in enumerate(scene.shots or []):
        prev = ids[i - 1] if i > 0 else None
        relay = ""
        if sh.source_shot_id:
            dangling = (sh.source_shot_id != prev)
            relay = (f"；接力 {sh.source_shot_id}"
                     + ("（⚠ 悬空：来源不在紧邻前位，端态中必须显式决定连链或断链）"
                        if dangling else ""))
        desc = (sh.description or "")[:40]
        lines.append(f"- {sh.shot_id}「{desc}」指纹 {_shot_token(sh)}{relay}")
    return "\n".join(lines)


def _render_border_pack(proj, scene) -> str:
    """BorderPack 渲染：window_k=2；prev/next 为 null 时显式说明。"""
    scenes = proj.scenes or []
    idx = next((i for i, s in enumerate(scenes) if s.scene_id == scene.scene_id), -1)

    def _view(s, tail: bool) -> str:
        shots = s.shots or []
        window = shots[-BORDER_WINDOW_K:] if tail else shots[:BORDER_WINDOW_K]
        wl = "、".join(
            f"{sh.shot_id}「{(sh.description or '')[:24]}」"
            + (f"（接力 {sh.source_shot_id}）" if sh.source_shot_id else "")
            for sh in window) or "（无镜头）"
        return (f"- {s.scene_id}「{(s.summary or s.title or '')[:40]}」"
                f"共 {len(shots)} 镜；{'出口' if tail else '入口'}窗口：{wl}")

    prev_s = scenes[idx - 1] if idx > 0 else None
    next_s = scenes[idx + 1] if 0 <= idx < len(scenes) - 1 else None
    lines = ["# 邻接与边界（本次任务快照，不作为持久依据）"]
    lines.append(("上游段（前一段出口）\n" + _view(prev_s, tail=True))
                 if prev_s else "上游段：无（本段是第一段）")
    lines.append(("下游段（后一段入口）\n" + _view(next_s, tail=False))
                 if next_s else "下游段：无（本段是最后一段）")
    return "\n".join(lines)


_INSTANCE_PREAMBLE = """【Scene Agent 任务开启】你是段 {scene_id} 的分镜设计实例（同一 Director
定义、独立一次性会话）。规则：
1. 你必须用读工具（story.list / story.read / script.read / character.list /
   character.read / shot.list / shot.read）自主读取当前真实状态后再设计。除了
   本消息附带的信息，你没有任何历史上下文；禁止凭空推断任何镜头内容。
2. 你没有 canonical 写权限：任何写工具都会被拒绝。你的唯一产出是
   proposal.submit——提交本段的目标端态（desired end state）。
3. 端态 = 本段目标镜头的【完备有序枚举】：不在端态里的现有镜头 = 删除（必须
   在 removed_shot_rationales 里给理由）；字段省略 = 保持现状。
4. 接力用 chain 表达：chain=true 表示本镜接力紧邻前一项（沿用其末帧）；段首
   项 chain 必须为 false。段内若存在悬空接力（见状态头标注），端态中必须
   显式决定（连链/断链）。
5. 下方"先验设计决策"是带前提的历史参考，不是证据——事实必须用读工具重新
   确认；你可以推翻它们（说明理由即可）。
6. 提交前用 proposal.submit；校验不合格会返回具体错误与修复指引，修正后可
   重交（后者自动取代前者）。"""

_PROPOSAL_FORMAT = """# 提案格式（proposal.submit 参数）
{{
  "basis": {{"scene_slice_fingerprint": "<状态头里给出的段指纹>"}},
  "end_state": [
    {{"shot_id": "<保留/修改的现有镜 id；新建镜头不填>",
      "chain": false,
      "fields": {{"description": "...", "camera": "...", ...}},
      "rationale": "本镜设计理由（建议必填：进入该段设计记忆）",
      "dependencies": [{{"kind": "shot|scene_content|character_canonical|border|adjacency|brief", "ref": "..."}}]}}
  ],
  "removed_shot_rationales": {{"<被删的 shot_id>": "删除理由"}},
  "constraint_responses": [{{"constraint_id": "...", "response": "如何满足或为何需改边界"}}],
  "border_change_requests": [{{"target": "prev|next", "statement": "...", "reason": "..."}}],
  "design_notes": "段级设计理由",
  "read_evidence": {{"n_read_calls": <数字——系统会从真实工具调用核对，如实填写>}}
}}
注意：fields 只写要改的字段（省略=保持）；新建镜头的 fields.description 必填。
dependencies 声明本镜决策的前提（border/adjacency 依赖的决策不会持久保存）。"""


def build_instance_instruction(rt: AgentRuntime, project: str,
                               delegation: dict, scene) -> str:
    """校准注入包（§3.2）。所有段从 canonical 现算，不含任何旧对话。"""
    pdir = rt._resolve_project_dir(project)
    store = reg.SubagentStore(pdir, project)
    proj = ProjectStore(pdir).load()

    mem_text, n_mem, n_quarantined = store.render_memory_for_injection(scene)
    locked = delegation.get("locked_border_constraints") or []
    if locked:
        _applies_zh = {"entry": "入口", "exit": "出口", "both": "出入口"}
        locked_lines = "\n".join(
            f"- {c['constraint_id']}（{_applies_zh.get(c.get('applies_to'), '?')}）："
            f"{c.get('statement')}" for c in locked)
        locked_text = ("# 本次任务的锁定边界约束（不可违反；认为必须改 → "
                       "border_change_request）\n"
                       "每条约束必须在提案的 constraint_responses 里给出回应"
                       "（说明如何满足，或为何需改边界）：\n" + locked_lines)
    else:
        locked_text = ("# 本次任务的锁定边界约束（不可违反；认为必须改 → border_change_request）\n"
                       "（无结构化锁定约束；Main Director 的边界意图见任务书）")
    parts = [_INSTANCE_PREAMBLE.format(scene_id=scene.scene_id),
             _render_scene_state_header(scene),
             f"\n本段指纹（proposal.submit 的 basis 用）：{fp.scene_slice_fingerprint(scene)}\n",
             _render_border_pack(proj, scene),
             locked_text]
    from src.agents import session_governance
    brief = session_governance.get_brief(pdir, project)
    parts.append("# 用户创作约束（原始 brief，设计时必须遵守）\n"
                 + (brief or "（本项目未记录原始 brief）"))
    if mem_text:
        parts.append(
            f"# 先验设计决策（advisory；{n_quarantined} 条因前提失效已隔离）\n{mem_text}")
    else:
        parts.append("# 先验设计决策\n（无可用先验）"
                     + (f"（{n_quarantined} 条因前提失效已隔离）" if n_quarantined else ""))
    parts.append(f"# 本次任务书（Main Director）\n{delegation.get('task_brief') or '（空）'}")
    parts.append(_PROPOSAL_FORMAT)
    return "\n\n".join(parts)


# ============ proposal.submit 闭包（E1–E6，§4.4）============
def _make_submit_executor(rt: AgentRuntime, project: str, delegation: dict,
                          state: dict):
    """submit executor 闭包：捕获实例共享 state（trace 在 state['trace']）与
    delegation 上下文。签名与 TOOL_EXECUTORS 一致（同步函数）。"""

    def _submit(rt_, project_, arguments: str, instruction, message_id) -> dict:
        pdir = rt_._resolve_project_dir(project_)
        store = reg.SubagentStore(pdir, project_)
        try:
            args = json.loads(arguments or "{}")
            if not isinstance(args, dict):
                args = {}
        except Exception:
            args = {}

        scene_id = delegation["scene_id"]

        def _fail(code: str, error: str, fix_hint: str, **extra) -> dict:
            return {"ok": False, "operation": "proposal.submit", "code": code,
                    "error": error, "fix_hint": fix_hint, "scene_id": scene_id,
                    **extra}

        # E2 contract 一致性
        if (str(args.get("scene_id") or scene_id) != scene_id
                or (args.get("delegation_id") not in (None, delegation["delegation_id"]))):
            return _fail("contract_mismatch",
                         f"提案 scene_id/delegation_id 与本实例不符"
                         f"（本实例：{scene_id} / {delegation['delegation_id']}）",
                         "scene_id 不填（默认本段）；delegation_id 不填或用实例 id")

        # E1 schema / 图 / 删申明（零副作用；非法 bounce 带修复指引）
        proj = ProjectStore(pdir).load()
        scene = next((s for s in proj.scenes if s.scene_id == scene_id), None)
        if scene is None:
            return _fail("abort_liveness", f"段 {scene_id} 已不存在",
                         "该段已被删除；停止提交，说明情况即可")
        try:
            end_state = normalize_end_state(args.get("end_state"))
            removed_rationales = normalize_removed_rationales(
                args.get("removed_shot_rationales"))
            _constraint_ids = [c.get("constraint_id") for c in
                               (delegation.get("locked_border_constraints") or [])]
            validate_end_state(
                end_state, scene,
                constraint_ids=_constraint_ids,
                constraint_responses=args.get("constraint_responses") or [],
                removed_rationales=removed_rationales,
                require_responses=bool(_constraint_ids))
        except DiffError as e:
            return _fail(e.code, e.message, e.fix_hint, **e.extra)

        # E5 读取证据（从 trace 统计；不信自报）——先于 basis 防伪：
        # 没读过的话一切 basis 都无从谈起，read_first 是更准确的错误。
        trace = state.get("trace") or []
        seg_reads = [t for t in trace if t.get("tool") in ("shot.list", "shot.read")
                     and t.get("ok")]
        if not seg_reads:
            return _fail("read_first",
                         "提交前没有任何真实读取记录（shot.list / shot.read）",
                         "先 shot.list 查看本段镜头（必要时 story.read 段内容）"
                         "再提交——不允许凭记忆提案")
        n_read_calls = sum(1 for t in trace
                           if t.get("tool") in ("story.read", "shot.list",
                                                "shot.read") and t.get("ok"))

        # E4 basis 防伪（实现口径见模块头注释）：
        #   值域检查（编造/过期）→ 组件检查（真实读过对应状态）
        try:
            basis_dict = normalize_basis(args.get("basis"))
        except DiffError as e:
            return _fail(e.code, e.message, e.fix_hint, **e.extra)
        basis_arg = basis_dict.get("scene_slice_fingerprint") or ""
        contract_basis = (delegation.get("basis") or {})
        cur_fp = fp.scene_slice_fingerprint(scene)
        contract_fp = contract_basis.get("scene_slice_fingerprint")
        if not basis_arg:
            return _fail("basis_fabricated", "缺 basis.scene_slice_fingerprint",
                         "从注入包的『本段指纹』原样抄录")
        if basis_arg != contract_fp and basis_arg != cur_fp:
            return _fail("basis_fabricated",
                         f"basis {basis_arg} 既不是本实例校准基准（{contract_fp}）"
                         f"也不是当前段指纹（{cur_fp}）",
                         "basis 必须来自任务开启时的『本段指纹』或重读后的真实值"
                         "（不可编造）")
        if cur_fp != contract_fp:
            # 无论实例用的是旧基准还是重读后的新值：delegation 的校准基准已
            # 过期（飞行期间段被改）→ basis_stale（重读重交 / 提请 rebase）
            return _fail("basis_stale",
                         f"段 {scene_id} 在你工作期间已被修改"
                         f"（当前指纹 {cur_fp} ≠ 校准基准 {contract_fp}）",
                         "重新读取本段（story.read + shot.list），基于新状态重交提案")
        # 组件防伪：真实读过对应状态
        story_tokens = {t.get("revision") for t in trace
                        if t.get("tool") == "story.read" and t.get("ok")}
        list_tokens = {t.get("revision") for t in trace
                       if t.get("tool") == "shot.list" and t.get("ok")}
        if contract_basis.get("scene_content_token") not in story_tokens:
            return _fail("basis_fabricated",
                         "basis 的段内容组件未出现在你的真实读取记录中"
                         "（需要 story.read 本段）",
                         "先 story.read(scene_id) 读取本段内容再提交")
        if contract_basis.get("shots_structure_token") not in list_tokens:
            return _fail("basis_fabricated",
                         "basis 的镜头结构组件未出现在你的真实读取记录中"
                         "（需要 shot.list）",
                         "先 shot.list 查看本段镜头结构再提交")

        # ---- 全部通过：落盘 proposal store ----
        proposal_id = reg.new_proposal_id()
        prev_pid = delegation.get("proposal_id")
        record = {
            "proposal_id": proposal_id,
            "delegation_id": delegation["delegation_id"],
            "scene_id": scene_id,
            "basis": {"scene_slice_fingerprint": basis_arg},
            "end_state": end_state,
            "removed_shot_rationales": removed_rationales,
            "constraint_ids": [c.get("constraint_id") for c in
                               (delegation.get("locked_border_constraints") or [])],
            "constraint_responses": args.get("constraint_responses") or [],
            "border_change_requests": args.get("border_change_requests") or [],
            "design_notes": str(args.get("design_notes") or ""),
            "read_evidence": {"n_read_calls": n_read_calls,
                              "verified_from_trace": True},
            "supersedes": prev_pid,
        }
        store.save_proposal(record)
        if prev_pid:
            old = store.load_proposal(prev_pid)
            if old is not None:
                old["superseded_by"] = proposal_id
                store.save_proposal(old)
        d = store.load_delegation(delegation["delegation_id"]) or dict(delegation)
        d["status"] = "proposed"
        d["proposal_id"] = proposal_id
        store.save_delegation(d)
        # 同步闭包共享的 delegation dict（同实例多次提交 → supersede 链）
        delegation["proposal_id"] = proposal_id

        # 结构化摘要（返回给实例 → 实例最终回复转述给 MD 的素材）
        cur_ids = [sh.shot_id for sh in (scene.shots or [])]
        eff_ids = [it.get("shot_id") for it in end_state if it.get("shot_id")]
        creates = sum(1 for it in end_state if not it.get("shot_id"))
        deletes = len([i for i in cur_ids if i not in set(eff_ids)])
        bcrs = record["border_change_requests"]
        summary = {
            "proposal_id": proposal_id, "scene_id": scene_id,
            "shots": {"before": len(cur_ids),
                      "after": len(end_state),
                      "creates": creates, "deletes": deletes},
            "border_change_requests": bcrs,
            "read_evidence": {"n_read_calls": n_read_calls},
            "design_notes_excerpt": (record["design_notes"] or "")[:120],
        }
        return {"ok": True, "operation": "proposal.submit",
                "proposal_id": proposal_id, "summary": summary,
                "note": "提案已提交并存档。可继续修正后重交（自动取代）；"
                        "完成后向 Main Director 汇报摘要即可。"}

    return _submit


# ============ 实例主流程 ============
async def run_scene_agent_instance(rt: AgentRuntime, project: str,
                                   delegation: dict, *,
                                   event_cb=None, cancel_event=None) -> dict:
    """跑一次性 Scene Agent 实例，返回给 MD 的结果（成功 = 提案摘要；失败 =
    诚实回执）。delegation 状态机：dispatched → proposed / aborted。"""
    from src.agents.director import _run_tool_loop, _WallClockExceeded, _TurnStopped
    from src.agents import session_governance

    scene_id = delegation["scene_id"]
    pdir = rt._resolve_project_dir(project)
    store = reg.SubagentStore(pdir, project)

    client = rt._resolve_client_or_none(agent_id="director")
    if client is None:
        d = store.load_delegation(delegation["delegation_id"]) or dict(delegation)
        d["status"] = "aborted"
        d["aborted_reason"] = "client_unavailable"
        store.save_delegation(d)
        return {"ok": False, "operation": "scene.delegate", "code": "client_unavailable",
                "error": "文本模型不可用，无法启动设计实例",
                "fix_hint": "配置 API Key 后重试，或在主会话直接执行",
                "delegation_id": delegation["delegation_id"], "scene_id": scene_id}

    proj = ProjectStore(pdir).load()
    scene = next((s for s in proj.scenes if s.scene_id == scene_id), None)
    if scene is None:   # 双保险（闸门刚查过；SW 极端竞态）
        d = store.load_delegation(delegation["delegation_id"]) or dict(delegation)
        d["status"] = "aborted"
        d["aborted_reason"] = "scene_gone"
        store.save_delegation(d)
        return {"ok": False, "operation": "scene.delegate", "code": "scene_not_found",
                "error": f"段 {scene_id} 已不存在", "fix_hint": "核对当前段集",
                "delegation_id": delegation["delegation_id"], "scene_id": scene_id}

    session_store = rt._session_store(project)
    instance_session = session_store.create_ephemeral(
        "director", "scene_agent", f"{scene_id}:{delegation['delegation_id']}")
    instruction = build_instance_instruction(rt, project, delegation, scene)

    # 实例 system：与 Director 同定义（F4 证据：同一定义 + 干净会话即高质量）
    from src.agents.director import _build_system
    messages = [{"role": "system", "content": _build_system(proj)},
                {"role": "user", "content": instruction}]
    loop_state: dict = {}
    submit_executor = _make_submit_executor(rt, project, delegation, loop_state)

    final = None
    stop_reason = None
    try:
        final = await _run_tool_loop(
            rt, client, project, messages, instruction, None,
            max_steps=INSTANCE_MAX_STEPS,
            event_cb=event_cb, cancel_event=cancel_event,
            state=loop_state, tools_whitelist=_scene_agent_tools(),
            extra_executors={"proposal.submit": submit_executor})
    except _TurnStopped:
        stop_reason = "cancelled"
    except _WallClockExceeded as e:   # 防御性不可达：rev3 后不再传递 wall_clock_s
        stop_reason = f"wall_clock_{int(e.budget_s)}s"
    except Exception as e:
        log.exception("Scene Agent 实例异常（delegation=%s）",
                      delegation["delegation_id"])
        stop_reason = f"error:{type(e).__name__}"

    # 实例会话留存（一次性：文件保留供审计，不再复用）
    try:
        session_store.append_message(
            instance_session.session_id,
            AgentMessage(role="user", content=instruction))
        final_text = (final or {}).get("text") or ""
        session_store.append_message(
            instance_session.session_id,
            AgentMessage(role="assistant", content=final_text,
                         meta={"delegation_id": delegation["delegation_id"],
                               "stopped": stop_reason,
                               "trace": loop_state.get("trace", [])}))
    except Exception:
        log.exception("实例会话留存失败（不阻断）")

    # 遥测：实例会话独立成行（Compaction 观测口径）
    try:
        _tio = sum(len(str(m.get("content") or "")) for m in messages
                   if m.get("role") == "tool")
        session_governance.log_context_telemetry(
            pdir, project, "director", instance_session.session_id,
            f"scene_agent_{delegation['delegation_id']}",
            history_chars=len(instruction),
            system_chars=len(messages[0]["content"]),
            tool_io_chars=_tio)
    except Exception:
        pass

    # ---- delegation 状态收敛 ----
    d = store.load_delegation(delegation["delegation_id"]) or dict(delegation)
    proposal_id = d.get("proposal_id")
    if proposal_id and d.get("status") == "proposed":
        p = store.load_proposal(proposal_id)
        summary = (p or {}).get("read_evidence") or {}
        bcrs = (p or {}).get("border_change_requests") or []
        end_state = (p or {}).get("end_state") or []
        return {
            "ok": True, "operation": "scene.delegate",
            "delegation_id": delegation["delegation_id"],
            "proposal_id": proposal_id, "scene_id": scene_id,
            "instance_session_id": instance_session.session_id,
            "shots": {"before": len(scene.shots or []), "after": len(end_state),
                      "creates": sum(1 for it in end_state if not it.get("shot_id"))},
            "border_change_requests": bcrs,
            "read_evidence": summary,
            "design_notes_excerpt": ((p or {}).get("design_notes") or "")[:120],
            "note": (f"段 {scene_id} 设计实例已完成提案 {proposal_id}"
                     f"（读取 {summary.get('n_read_calls', 0)} 次）。"
                     "请 reconcile：proposal.read 看全文 → commit / reject；"
                     "bcr 待你裁决。")}
    # 无有效提案（停止/超时/异常/耗尽）
    d["status"] = "aborted"
    d["aborted_reason"] = stop_reason or "no_proposal"
    store.save_delegation(d)
    return {"ok": False, "operation": "scene.delegate",
            "code": "no_proposal",
            "error": (f"设计实例未产出有效提案（原因：{stop_reason or '未提交'}）"
                      "——本次委派无 canonical 改动"),
            "fix_hint": "可重试一次；持续失败请在主会话直接执行",
            "delegation_id": delegation["delegation_id"], "scene_id": scene_id,
            "instance_session_id": instance_session.session_id}
