# src/agents/change_protocol/character_proposal.py
# AI-7B Character Agent 提案运行时（character.propose_change，§18/§19/§20）。
#
# Producer 委派 → Character Agent 从角色角度产出**语义**修改意图。语义意图仅含新
# description_en / name，不含 before/revision/ChangeOperation（§19）。
#
# 红线（§19/§20）：
#   - Character 当前无 character_id，name 即身份 → **改名不支持**（needs_user_decision），
#     不发明 rename 动作、不隐藏身份迁移。
#   - 外貌变更（如「头发改长」）→ update_character.description_en，**绝不**直接改
#     Character.views / 首尾帧 / 视频 / 资产文件（§20）。这些是下游生产产物，仅由 Impact
#     标陈旧，AI-7B 不动。
#
# 复用：ProjectContextBuilder.build_for_specialist + proposal_helpers.call_and_parse +
# create/settle_proposal_task。不调 Agnes 生成图；不改 views/assets。
from __future__ import annotations
from typing import List

from ..inquiry import ProjectContextBuilder, classify_request, _pack_text
from ..project_intelligence import ProjectEvidenceRef
from .models import ModuleProposal, ModuleProposalItem
from .proposal_helpers import (new_item_id, new_proposal_id, call_and_parse,
                                 create_proposal_task, settle_proposal_task,
                                 scope_refs_from_classification, eref)


CHARACTER_ACTIONS = ("update_character", "insert_character", "delete_character")


CHARACTER_PROPOSAL_SYSTEM = """你是 AGNES 的 Character Agent，从角色角度对一个项目修改请求提出**语义**修改意图。

🔴 红线（§19/§20）：
- 你只表达创作意图，绝不产出 before 值、不输出 revision/签名/ChangeOperation。
- **改名不支持**：当前项目用角色 name 作为身份标识（无独立 character_id）。若用户要改名，
  **不要**发明 rename 动作；在 user_decisions_needed 里说明「改名不支持，请用户确认：是保留旧名
  仅改描述，还是新建角色并删旧角色」。
- 外貌变更（头发/服装/外观）只能改 description_en（文字视觉设定）。**绝不**直接修改
  Character.views / 参考图 / 首尾帧 / 视频 / 资产——这些是下游生产产物，由后端 Impact 标记陈旧，
  不在你的语义范围内。

可提出三种语义动作：
- update_character：修改某角色的文字视觉设定。给 name（现有角色）+ description_en（新）。
- insert_character：新增角色。给 name（不能与现有重名）+ description_en。
- delete_character：删除某角色。给 name（现有角色）。

约束：
- description_en 用中文/英文均可，具体可画。
- 若该请求涉及改名或你无法决定（歧义），把问题放进 user_decisions_needed，items 可为空。
- 不替别的模块（剧本/分镜）做决定。

只返回一个 JSON 对象，不要任何解释文字、不要代码块标记：
{
  "title": "本次角色修改的短标题",
  "summary": "一句话说明创作意图",
  "items": [
    {"action": "update_character", "name": "Xiaoyu", "description_en": "long black hair, gray coat", "reason": "..."},
    {"action": "insert_character", "name": "Aran", "description_en": "...", "reason": "..."},
    {"action": "delete_character", "name": "MechDog", "reason": "..."}
  ],
  "user_decisions_needed": ["（可选）需要用户决策的歧义，如改名"]
}"""


def _char_names(project) -> set:
    return {c.name for c in project.characters}


def _parse_character_items(raw: str, project) -> dict:
    """解析 + 校验 → {title, summary, items, user_decisions_needed}。失败抛 ValueError。"""
    from .proposal_helpers import parse_proposal_json
    obj = parse_proposal_json(raw)
    title = str(obj.get("title") or "").strip()
    summary = str(obj.get("summary") or "").strip()
    decisions = [str(x) for x in (obj.get("user_decisions_needed") or []) if x][:20]
    raw_items = obj.get("items") or []
    if not isinstance(raw_items, list):
        raise ValueError("items 不是数组")
    existing = _char_names(project)
    items: List[ModuleProposalItem] = []
    for i, ri in enumerate(raw_items, start=1):
        if not isinstance(ri, dict):
            raise ValueError(f"第 {i} 个 item 不是对象")
        action = str(ri.get("action") or "").strip()
        if action not in CHARACTER_ACTIONS:
            raise ValueError(f"第 {i} 个 item action 非法: {action}（改名不支持，请用 user_decisions_needed）")
        name = str(ri.get("name") or "").strip()
        if not name:
            raise ValueError(f"第 {i} 个 {action} 缺少 name")
        reason = str(ri.get("reason") or "").strip()

        if action == "update_character":
            if name not in existing:
                raise ValueError(f"第 {i} 个 update_character 角色不存在: {name}")
            desc = str(ri.get("description_en") or "").strip()
            if not desc:
                raise ValueError(f"第 {i} 个 update_character 缺少 description_en")
            items.append(ModuleProposalItem(
                item_id=new_item_id(), module="character", action=action,
                target_refs=[eref("character", "character", name)],
                intent={"name": name, "description_en": desc},
                reason=reason, source="user_requested"))

        elif action == "insert_character":
            if name in existing:
                raise ValueError(f"第 {i} 个 insert_character 角色已存在: {name}")
            desc = str(ri.get("description_en") or "").strip()
            if not desc:
                raise ValueError(f"第 {i} 个 insert_character 缺少 description_en")
            items.append(ModuleProposalItem(
                item_id=new_item_id(), module="character", action=action,
                target_refs=[],  # 新角色尚无实体引用
                intent={"name": name, "description_en": desc},
                reason=reason, source="user_requested"))

        elif action == "delete_character":
            if name not in existing:
                raise ValueError(f"第 {i} 个 delete_character 角色不存在: {name}")
            items.append(ModuleProposalItem(
                item_id=new_item_id(), module="character", action=action,
                target_refs=[eref("character", "character", name)],
                intent={"name": name}, reason=reason, source="user_requested"))

    return {"title": title or "角色修改提案", "summary": summary,
            "items": items, "user_decisions_needed": decisions}


def _build_question(user_request: str, scope_chars: list | None = None) -> str:
    hint = ""
    if scope_chars:
        hint = (f"\n用户当前在页面上选中的角色：{'、'.join(scope_chars)}——"
                "「这个角色」等指代即指向它，直接对它操作，不要询问是哪个角色。\n")
    return (f"用户变更请求：「{user_request}」\n{hint}"
            "请从角色（身份 / 外貌文字设定 / 新增 / 删除）角度，提出语义修改意图。"
            "改名不支持——若涉及，放进 user_decisions_needed。只返回上面要求的 JSON 对象。")


async def run_character_proposal(rt, project: str, session_id: str,
                                 change_request, sig: str, round: int = 1,
                                 *, client=None) -> ModuleProposal:
    """character.propose_change 运行时（§18）。有界 context → LLM → parse+validate(+1 repair) →
    ModuleProposal。§19 改名→user_decisions_needed；§20 外貌→description_en only（无 views/assets）。
    persist-before-gather（§25）。失败返回 failed proposal；授权失败抛 AgentError。"""
    agent_id = "character_designer"
    cap_id = "character.propose_change"
    intel = rt.project_intelligence(project)
    proj = intel._project()
    classification = classify_request(change_request.original_user_request, proj)
    # AI-7C-H §33 Probe 2：合并持久化 context_scope（entity_refs 的 character 指向）。
    # 「把这个角色的盔甲换成黑色」文本不含角色名——纯文本分类拿不到指向，LLM 会反问
    # Which Character（§9 禁止）。页面上下文（选中角色）才是解析指向。
    for ref in (change_request.entity_refs or []):
        if ref.get("entity_type") == "character" and ref.get("entity_id"):
            name = str(ref["entity_id"])
            if name not in classification.get("character_refs", []):
                classification["character_refs"] = \
                    classification.get("character_refs", []) + [name]
    scope = scope_refs_from_classification(classification)
    scope_chars = list(classification.get("character_refs", []))
    question = _build_question(change_request.original_user_request, scope_chars)
    pack = ProjectContextBuilder(intel).build_for_specialist(
        agent_id=agent_id, request=change_request.original_user_request,
        scope=scope, consultation_question=question)
    task = create_proposal_task(rt, project, agent_id, cap_id, question)

    proposal = ModuleProposal(
        proposal_id=new_proposal_id(), change_session_id=session_id, module="character",
        owner_agent_id=agent_id, round=round, status="running", summary="",
        source_snapshot_signature=sig, agent_task_id=task.task_id)
    from .store import ChangeProtocolStore
    cp_store = ChangeProtocolStore(rt._resolve_project_dir(project), project)
    cp_store.save_proposal(proposal)   # §25 running shell

    cli = client if client is not None else rt._resolve_client_or_none()
    if cli is None:
        proposal.status = "failed"
        proposal.blockers = ["Agnes 未配置，无法生成角色提案"]
        cp_store.save_proposal(proposal)
        settle_proposal_task(rt, project, task, proposal, failed=True)
        return proposal

    messages = [{"role": "system", "content": CHARACTER_PROPOSAL_SYSTEM + "\n\n" + _pack_text(pack)},
                {"role": "user", "content": question}]
    parsed, err = await call_and_parse(cli, messages, lambda raw: _parse_character_items(raw, proj))
    if err:
        proposal.status = "failed"
        proposal.blockers = [err]
        cp_store.save_proposal(proposal)
        settle_proposal_task(rt, project, task, proposal, failed=True)
        return proposal

    proposal.summary = parsed["summary"]
    proposal.items = [it.to_dict() for it in parsed["items"]]
    proposal.user_decisions_needed = parsed["user_decisions_needed"]
    proposal.status = "completed"
    cp_store.save_proposal(proposal)
    settle_proposal_task(rt, project, task, proposal, failed=False)
    return proposal
