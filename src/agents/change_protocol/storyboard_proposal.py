# src/agents/change_protocol/storyboard_proposal.py
# AI-7B Storyboard Director 提案运行时（Team ChangeSession 路径，§21）。
#
# §21：复用既有 Director 语义提案下层 generate_semantic_proposal（LLM→DirectorProposalIntent），
# 仅把 DirectorProposalItem 转成 ModuleProposalItem 存入 Team 的 ModuleProposal。
# **绝不**调用 apply_proposal / preview_selected / cs_engine.apply / 建 Director Workspace
# （那些是既有 direct Director UI 流程的专属；编译/预览/投影在 7B-4 经纯内存 core）。
#
# Director 提案是 scene-scoped（build_director_context 要求 scope_type=scene|shot）。
# service 在路由命中 storyboard 且有 scene scope 时调用本函数；无 scene scope 则 Director
# 延后到 round 2（§26，7B-3 接通——待 Script 定稿受影响场景后再问 Director）。
from __future__ import annotations

from ..director_proposal import (generate_semantic_proposal,
                                   ProposalGenerationError)
from .models import ModuleProposal, ModuleProposalItem
from .proposal_helpers import (new_item_id, new_proposal_id,
                                 create_proposal_task, settle_proposal_task, eref)


def _director_item_to_module_item(ditem, module: str = "storyboard") -> ModuleProposalItem:
    """DirectorProposalItem → ModuleProposalItem。语义字段进 intent dict；
    shot/scene 进 target_refs。不拷贝 before/revision/changeset（Director item 本就无这些）。"""
    intent = {"action": ditem.action, "scene_id": ditem.scene_id}
    if ditem.shot_id is not None:
        intent["shot_id"] = ditem.shot_id
    if ditem.description is not None:
        intent["description"] = ditem.description
    if ditem.characters is not None:
        intent["characters"] = list(ditem.characters)
    if ditem.target_duration_s is not None:
        intent["target_duration_s"] = ditem.target_duration_s
    if ditem.after_shot_id is not None:
        intent["after_shot_id"] = ditem.after_shot_id
    target_refs = []
    if ditem.shot_id:
        target_refs.append(eref("shot", "shot", ditem.shot_id))
    elif ditem.scene_id:
        target_refs.append(eref("scene", "scene", ditem.scene_id))
    return ModuleProposalItem(
        item_id=new_item_id(), module=module, action=ditem.action,
        target_refs=target_refs, intent=intent, reason=ditem.reason,
        source="user_requested")


async def run_storyboard_proposal(rt, project: str, session_id: str,
                                  change_request, sig: str,
                                  scope_type: str, scope_id: str,
                                  round: int = 1, *, client=None) -> ModuleProposal:
    """storyboard.propose_change（Team 路径，§21）。generate_semantic_proposal → 转 ModuleProposalItem。
    不建 Workspace / 不编译 / 不预览（7B-4）。失败返回 failed proposal；授权失败抛 AgentError。"""
    agent_id = "director"
    cap_id = "storyboard.propose_change"
    instruction = change_request.original_user_request
    question = (f"用户变更请求：「{instruction}」\n"
                f"请从分镜（镜头级）角度，对 {scope_type}:{scope_id} 提出语义修改意图。")
    task = create_proposal_task(rt, project, agent_id, cap_id, question)

    proposal = ModuleProposal(
        proposal_id=new_proposal_id(), change_session_id=session_id, module="storyboard",
        owner_agent_id=agent_id, round=round, status="running", summary="",
        source_snapshot_signature=sig, agent_task_id=task.task_id)
    from .store import ChangeProtocolStore
    cp_store = ChangeProtocolStore(rt._resolve_project_dir(project), project)
    cp_store.save_proposal(proposal)   # §25 running shell

    cli = client if client is not None else rt._resolve_client_or_none()
    if cli is None:
        proposal.status = "failed"
        proposal.blockers = ["Agnes 未配置，无法生成分镜提案"]
        cp_store.save_proposal(proposal)
        settle_proposal_task(rt, project, task, proposal, failed=True)
        return proposal

    try:
        intent = await generate_semantic_proposal(
            cli, rt._project_loader(project), scope_type, scope_id, instruction)
    except ProposalGenerationError as e:
        proposal.status = "failed"
        proposal.blockers = [f"{e.code}: {e.message}"]
        cp_store.save_proposal(proposal)
        settle_proposal_task(rt, project, task, proposal, failed=True)
        return proposal

    proposal.summary = intent.summary or intent.title
    proposal.items = [_director_item_to_module_item(it).to_dict()
                      for it in intent.items]
    proposal.status = "completed"
    cp_store.save_proposal(proposal)
    settle_proposal_task(rt, project, task, proposal, failed=False)
    return proposal
