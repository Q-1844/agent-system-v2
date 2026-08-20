# src/agents/change_protocol/proposal_helpers.py
# AI-7B Specialist 提案运行时共享助手（§15/§25/§102）。
#
# 复用不重写：镜像 inquiry._create_consultation_task / _settle_consultation_task /
# director_proposal._parse_json + parse+validate(+1 repair)。区别：
#   - operation=PROPOSE（非 inspect），capability=story/character.propose_change。
#   - 产出 ModuleProposal（含语义 ModuleProposalItem），非 ConsultationResult。
#   - 运行于 PROPOSAL_ONLY 模式（§6 代码硬闸 assert_mode_allowed）。
#
# §25 persist-before-gather：每 runtime 先落 running shell + AgentTask 再 gather LLM，独立结算。
# §102：strict parse → validate → 一次 repair → 干净失败（返回 failed ModuleProposal，不无限重试）。
from __future__ import annotations
import secrets
import time
from typing import Any, Callable, List, Optional, Tuple

from ..models import AgentTask, AgentOp, TaskStatus
from ..project_intelligence import ProjectEvidenceRef
from .modes import AgentExecutionMode, assert_mode_allowed
from .models import ModuleProposal


def new_item_id() -> str:
    return "item_" + secrets.token_hex(4)


def new_proposal_id() -> str:
    return "mp_" + secrets.token_hex(6)


def parse_proposal_json(raw: str) -> dict:
    """json.loads + 去 ``` 代码块。失败抛 ValueError（caller 转 repair 提示）。"""
    if raw is None:
        raise ValueError("AI 返回为空")
    text = raw.strip()
    if not text:
        raise ValueError("AI 返回为空")
    import json
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
    try:
        obj = json.loads(text)
    except Exception as e:
        raise ValueError(f"无法解析为 JSON：{e}")
    if not isinstance(obj, dict):
        raise ValueError("AI 返回不是 JSON 对象")
    return obj


async def call_and_parse(client, messages: list, parser: Callable[[str], Any]
                         ) -> Tuple[Any, Optional[str]]:
    """LLM json 调用 → parser(raw) → 一次 repair → (parsed, None) | (None, error)。
    parser 抛 ValueError 视为可 repair 的解析/校验失败；其它异常视为 LLM 失败。
    绝不无限重试（§102）。"""
    try:
        raw = await client.chat(messages, json_mode=True, temperature=0.3, max_tokens=2048)
    except Exception as e:
        return None, f"Agnes 调用失败：{str(e)[:200]}"
    try:
        return parser(raw), None
    except ValueError as e:
        repair = messages + [{"role": "system",
                              "content": f"上次返回无法使用：{e}。请严格遵循 schema，只返回一个合法 JSON 对象。"}]
        try:
            raw2 = await client.chat(repair, json_mode=True, temperature=0.2, max_tokens=2048)
            return parser(raw2), None
        except ValueError as e2:
            return None, f"输出无法解析/校验：{e2}"
        except Exception as e2:
            return None, f"Agnes repair 调用失败：{str(e2)[:200]}"
    except Exception as e:
        return None, f"提案解析异常：{str(e)[:200]}"


def create_proposal_task(rt, project: str, agent_id: str, cap_id: str,
                         question: str) -> AgentTask:
    """§25：gather 前先 persist AgentTask（running, operation=propose）。
    §6 代码硬闸：assert_mode_allowed(PROPOSAL_ONLY, propose) + can_execute 五重校验。"""
    assert_mode_allowed(AgentExecutionMode.PROPOSAL_ONLY.value, AgentOp.PROPOSE.value)
    if not rt.capabilities.can_execute(agent_id, cap_id, AgentOp.PROPOSE.value):
        from ..runtime import AgentError
        raise AgentError("permission_denied", f"{agent_id} 无权 {cap_id}/propose")
    now = time.time()
    task = AgentTask(
        task_id="", project=project, requester_agent="producer", target_agent=agent_id,
        capability_id=cap_id, instruction=question, scope_type="project", scope_id=project,
        operation=AgentOp.PROPOSE.value, status=TaskStatus.RUNNING.value,
        created_at=now, updated_at=now)
    return rt._task_store(project).create(task)


def settle_proposal_task(rt, project: str, task: AgentTask,
                         proposal: ModuleProposal, failed: bool = False) -> None:
    """结算 AgentTask：structured_result=proposal 摘要；completed/failed。"""
    task.structured_result = {"proposal_id": proposal.proposal_id,
                              "module": proposal.module, "status": proposal.status,
                              "summary": proposal.summary,
                              "item_count": len(proposal.items),
                              "blockers": list(proposal.blockers),
                              "user_decisions_needed": list(proposal.user_decisions_needed)}
    task.result_summary = proposal.summary or (proposal.blockers[0] if proposal.blockers else "")
    task.status = TaskStatus.FAILED.value if failed else TaskStatus.COMPLETED.value
    if failed:
        task.error = (proposal.blockers[0] if proposal.blockers
                      else proposal.summary or "proposal failed")
    rt._task_store(project).update_task(task)


def scope_refs_from_classification(classification: dict) -> List[ProjectEvidenceRef]:
    """把 classify_request 的 refs 转 ProjectEvidenceRef scope（供 build_for_specialist）。"""
    refs: List[ProjectEvidenceRef] = []
    for name in classification.get("character_refs", []):
        refs.append(ProjectEvidenceRef(module="character", entity_type="character", entity_id=name))
    for sid in classification.get("scene_refs", []):
        refs.append(ProjectEvidenceRef(module="scene", entity_type="scene", entity_id=sid))
    for shid in classification.get("shot_refs", []):
        refs.append(ProjectEvidenceRef(module="shot", entity_type="shot", entity_id=shid))
    return refs


def eref(module: str, etype: str, eid: str) -> dict:
    """target_refs 用：返回 ProjectEvidenceRef.to_dict()（ModuleProposalItem.target_refs 是 List[dict]）。"""
    return ProjectEvidenceRef(module=module, entity_type=etype, entity_id=eid).to_dict()
