# src/agents/change_protocol/script_proposal.py
# AI-7B Script Writer 提案运行时（story.propose_change，§16/§17）。
#
# Producer 委派 → Script Writer 从剧本角度产出**语义**修改意图（不落盘 canonical、不建
# Workspace、不应用）。语义意图仅含新值/锚点，不含 before/revision/稳定 scene_id/
# ChangeOperation（§17）。LLM 不发明 item_id（§14，服务端 new_item_id）。
#
# 复用：ProjectContextBuilder.build_for_specialist（有界 Context Pack，§16 不 dump 整项目）+
# proposal_helpers.call_and_parse（一次 repair，§102）+ create/settle_proposal_task（§25）。
# 不重写剧本内容逻辑；不调 Agnes 生成；不改 Character/Shot。
from __future__ import annotations
from typing import List, Optional

from ..inquiry import (ProjectContextBuilder, classify_request, _pack_text)
from ..project_intelligence import ProjectEvidenceRef
from ..models import AgentOp
from .models import ModuleProposal, ModuleProposalItem
from .proposal_helpers import (new_item_id, new_proposal_id, call_and_parse,
                                 create_proposal_task, settle_proposal_task,
                                 scope_refs_from_classification, eref)


SCRIPT_ACTIONS = ("update_story_text", "update_scene", "insert_scene",
                  "delete_scene", "reorder_scenes")


SCRIPT_PROPOSAL_SYSTEM = """你是 AGNES 的 Script Writer，从剧本角度对一个项目修改请求提出**语义**修改意图。

🔴 红线（§17）：
- 你只表达创作意图，绝不产出 before 值、不发明稳定 scene_id、不输出 revision/签名/ChangeOperation。
- 新场景不要给 scene_id——后端会用稳定分配器分配。
- 不直接修改正式项目；所有修改意图经后端确定性编译 + 预览 + 用户批准（AI-7C 才执行）。

可提出五种语义动作：
- update_story_text：修改项目级故事文本。给 new_story_text。
- update_scene：修改某故事段。给 scene_id + new_summary（可选）+ characters（可选，只能选自项目角色）。
- insert_scene：在某故事段后插入新段。给 after_scene_id（插在哪个现有段之后；省略/null=加到末尾）+ summary + characters。
- delete_scene：删除某故事段。给 scene_id。
- reorder_scenes：把某故事段移到 after_scene_id 之后（省略/null=移到最前）。给 scene_id + after_scene_id。

约束：
- characters 只能选自项目已有角色（新角色由 Character Agent 负责，不是你的职责）。
- description/summary 用中文、具体。
- 一个请求通常产出 1~N 个 item；若无任何剧本改动，返回空 items 数组并说明原因。

只返回一个 JSON 对象，不要任何解释文字、不要代码块标记：
{
  "title": "本次剧本修改的短标题",
  "summary": "一句话说明创作意图",
  "items": [
    {"action": "update_story_text", "new_story_text": "...", "reason": "..."},
    {"action": "update_scene", "scene_id": "s01", "new_summary": "...", "characters": ["Xiaoyu"], "reason": "..."},
    {"action": "insert_scene", "after_scene_id": "s01", "summary": "...", "characters": ["Xiaoyu"], "reason": "..."},
    {"action": "delete_scene", "scene_id": "s09", "reason": "..."},
    {"action": "reorder_scenes", "scene_id": "s05", "after_scene_id": "s01", "reason": "..."}
  ]
}"""


def _scene_ids(project) -> set:
    return {s.scene_id for s in project.scenes}


def _parse_script_items(raw: str, project) -> dict:
    """解析 + 校验 LLM 输出 → {title, summary, items: List[ModuleProposalItem]}。
    失败抛 ValueError（call_and_parse 转 repair）。校验针对当前 canonical project。"""
    from .proposal_helpers import parse_proposal_json
    obj = parse_proposal_json(raw)
    title = str(obj.get("title") or "").strip()
    summary = str(obj.get("summary") or "").strip()
    raw_items = obj.get("items") or []
    if not isinstance(raw_items, list):
        raise ValueError("items 不是数组")
    existing = _scene_ids(project)
    proj_chars = {c.name for c in project.characters}
    items: List[ModuleProposalItem] = []
    for i, ri in enumerate(raw_items, start=1):
        if not isinstance(ri, dict):
            raise ValueError(f"第 {i} 个 item 不是对象")
        action = str(ri.get("action") or "").strip()
        if action not in SCRIPT_ACTIONS:
            raise ValueError(f"第 {i} 个 item action 非法: {action}")
        reason = str(ri.get("reason") or "").strip()

        def _chars(field="characters"):
            cs = ri.get(field)
            if cs is None:
                return None
            cs = [str(c).strip() for c in cs if str(c).strip()]
            bad = [c for c in cs if c not in proj_chars]
            if bad and proj_chars:
                raise ValueError(f"第 {i} 个 item characters 不在项目角色: {bad}")
            return cs

        if action == "update_story_text":
            new_text = str(ri.get("new_story_text") or "").strip()
            if not new_text:
                raise ValueError(f"第 {i} 个 update_story_text 缺少 new_story_text")
            items.append(ModuleProposalItem(
                item_id=new_item_id(), module="script", action=action,
                target_refs=[eref("script", "story", "story_text")],
                intent={"new_story_text": new_text}, reason=reason,
                source="user_requested"))

        elif action == "update_scene":
            sid = str(ri.get("scene_id") or "").strip()
            if not sid or sid not in existing:
                raise ValueError(f"第 {i} 个 update_scene scene_id 不存在: {sid}")
            new_summary = str(ri.get("new_summary") or "").strip() or None
            chars = _chars()
            if new_summary is None and chars is None:
                raise ValueError(f"第 {i} 个 update_scene 未提供任何新字段")
            intent = {"scene_id": sid}
            if new_summary is not None:
                intent["new_summary"] = new_summary
            if chars is not None:
                intent["characters"] = chars
            items.append(ModuleProposalItem(
                item_id=new_item_id(), module="script", action=action,
                target_refs=[eref("scene", "scene", sid)],
                intent=intent, reason=reason, source="user_requested"))

        elif action == "insert_scene":
            after = str(ri.get("after_scene_id") or "").strip() or None
            if after and after not in existing:
                raise ValueError(f"第 {i} 个 insert_scene after_scene_id 不存在: {after}")
            scene_summary = str(ri.get("summary") or "").strip()
            if not scene_summary:
                raise ValueError(f"第 {i} 个 insert_scene 缺少 summary")
            chars = _chars() or []
            intent = {"after_scene_id": after, "summary": scene_summary, "characters": chars}
            items.append(ModuleProposalItem(
                item_id=new_item_id(), module="script", action=action,
                target_refs=([eref("scene", "scene", after)] if after else []),
                intent=intent, reason=reason, source="user_requested"))

        elif action == "delete_scene":
            sid = str(ri.get("scene_id") or "").strip()
            if not sid or sid not in existing:
                raise ValueError(f"第 {i} 个 delete_scene scene_id 不存在: {sid}")
            items.append(ModuleProposalItem(
                item_id=new_item_id(), module="script", action=action,
                target_refs=[eref("scene", "scene", sid)],
                intent={"scene_id": sid}, reason=reason, source="user_requested"))

        elif action == "reorder_scenes":
            sid = str(ri.get("scene_id") or "").strip()
            if not sid or sid not in existing:
                raise ValueError(f"第 {i} 个 reorder_scenes scene_id 不存在: {sid}")
            after = str(ri.get("after_scene_id") or "").strip() or None
            if after and after not in existing:
                raise ValueError(f"第 {i} 个 reorder_scenes after_scene_id 不存在: {after}")
            items.append(ModuleProposalItem(
                item_id=new_item_id(), module="script", action=action,
                target_refs=[eref("scene", "scene", sid)],
                intent={"scene_id": sid, "after_scene_id": after},
                reason=reason, source="user_requested"))

    return {"title": title or "剧本修改提案", "summary": summary, "items": items}


def _build_question(user_request: str) -> str:
    return (f"用户变更请求：「{user_request}」\n"
            "请从剧本（故事文本 / 故事段结构 / 场景顺序 / 出场角色）角度，提出语义修改意图。"
            "只返回上面要求的 JSON 对象。")


async def run_script_proposal(rt, project: str, session_id: str,
                              change_request, sig: str, round: int = 1,
                              *, client=None) -> ModuleProposal:
    """story.propose_change 运行时（§16）。有界 context → LLM → parse+validate(+1 repair) →
    ModuleProposal（语义 items）。persist-before-gather（§25）。失败返回 failed proposal（不抛，
    供 service 标 blocked §103）。授权失败抛 AgentError。"""
    agent_id = "script_writer"
    cap_id = "story.propose_change"
    intel = rt.project_intelligence(project)
    proj = intel._project()
    classification = classify_request(change_request.original_user_request, proj)
    scope = scope_refs_from_classification(classification)
    pack = ProjectContextBuilder(intel).build_for_specialist(
        agent_id=agent_id, request=change_request.original_user_request,
        scope=scope, consultation_question=_build_question(change_request.original_user_request))
    question = _build_question(change_request.original_user_request)
    task = create_proposal_task(rt, project, agent_id, cap_id, question)

    proposal = ModuleProposal(
        proposal_id=new_proposal_id(), change_session_id=session_id, module="script",
        owner_agent_id=agent_id, round=round, status="running", summary="",
        source_snapshot_signature=sig, agent_task_id=task.task_id)
    # 落 running shell（§25：gather 前先 persist）。change_protocol proposals 用本包 store。
    from .store import ChangeProtocolStore
    cp_store = ChangeProtocolStore(rt._resolve_project_dir(project), project)
    cp_store.save_proposal(proposal)

    cli = client if client is not None else rt._resolve_client_or_none()
    if cli is None:
        proposal.status = "failed"
        proposal.blockers = ["Agnes 未配置，无法生成剧本提案"]
        cp_store.save_proposal(proposal)
        settle_proposal_task(rt, project, task, proposal, failed=True)
        return proposal

    messages = [{"role": "system", "content": SCRIPT_PROPOSAL_SYSTEM + "\n\n" + _pack_text(pack)},
                {"role": "user", "content": question}]
    parsed, err = await call_and_parse(cli, messages, lambda raw: _parse_script_items(raw, proj))
    if err:
        proposal.status = "failed"
        proposal.blockers = [err]
        cp_store.save_proposal(proposal)
        settle_proposal_task(rt, project, task, proposal, failed=True)
        return proposal

    proposal.summary = parsed["summary"]
    proposal.items = [it.to_dict() for it in parsed["items"]]
    # AI-7D-R §10：未产出任何 item 但也未报错 → 诚实的 completed 空提案（非失败、不伪造
    # “剧本无需改动”A/B 决策）。service._analyze_and_compile 据此终态化为 no_action。
    proposal.status = "completed"
    proposal.user_decisions_needed = list(parsed.get("user_decisions_needed") or [])
    cp_store.save_proposal(proposal)
    settle_proposal_task(rt, project, task, proposal, failed=False)
    return proposal
