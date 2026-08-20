# src/agents/change_protocol/dependency.py
# AI-7B 确定性依赖事实（§27）。复用 ProjectIntelligenceService.dependencies
# （character→scene/shot、scene→shot/plan、shot→source/asset）——LLM 不决定结构依赖（§27）。
#
# 输入：ModuleProposal[]（语义 items 的 target_refs）+ ProjectIntelligenceService。
# 输出：DependencyFact[] dict（fact_id/relation/source/target/summary）。纯代码，不调 LLM。
from __future__ import annotations
import secrets
from typing import List

from .models import ModuleProposal, ModuleProposalItem


def _fid() -> str:
    return "dep_" + secrets.token_hex(4)


def build_dependency_facts(proposals: List[ModuleProposal], intel) -> List[dict]:
    """§27：对每个 proposal item 的 target_refs 查 intel.dependencies → 收集确定性依赖边。
    story_text 无结构依赖（项目级）；character/scene/shot 有。去重。"""
    facts: List[dict] = []
    seen = set()
    for prop in proposals:
        for item_dict in prop.items:
            item = ModuleProposalItem.from_dict(item_dict)
            for ref in item.target_refs:
                etype = (ref.get("entity_type") or "").lower()
                eid = ref.get("entity_id") or ""
                if not etype or not eid or etype == "story":
                    continue   # story_text 项目级，无结构依赖
                try:
                    res = intel.dependencies(etype, eid)
                except Exception:
                    continue
                for edge in (res.data.get("edges") or []):
                    # edge 可能是 DependencyEdge dataclass 或 dict（取决于 _result 序列化）
                    if isinstance(edge, dict):
                        tgt = edge.get("target") or {}
                        relation = edge.get("relation", "")
                    else:
                        tgt = edge.target
                        relation = edge.relation
                    tgt = tgt if isinstance(tgt, dict) else (
                        tgt.to_dict() if hasattr(tgt, "to_dict") else {"entity_id": str(tgt)})
                    tgt_id = tgt.get("entity_id", "")
                    tgt_type = tgt.get("entity_type", "")
                    tgt_module = tgt.get("module", "")
                    key = (etype, eid, relation, tgt_type, tgt_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    facts.append({
                        "fact_id": _fid(),
                        "relation": relation,
                        "source": {"entity_type": etype, "entity_id": eid,
                                   "module": ref.get("module", "")},
                        "target": {"entity_type": tgt_type, "entity_id": tgt_id,
                                   "module": tgt_module},
                        "summary": f"{etype} {eid} → {tgt_type} {tgt_id} ({relation})",
                    })
    return facts
