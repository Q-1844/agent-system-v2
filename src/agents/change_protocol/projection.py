# src/agents/change_protocol/projection.py
# AI-7B ProposalProjectionEngine（§60/§61/§63/§65）。
#
# 把批准前 plan 的语义 proposal items 编译成 ChangeOperation[]，喂给**纯内存预览核心**
# （revision.preview_operations_on_project，§62），产出最终投影态 + diff + impact，
# 包装成 PlanPreviewArtifact。**全程零落盘**（不建 workspace、不写 canonical/working/Plan，
# §61/§64）；applyable 恒 False（§65）。
#
# 投影态是派生数据（§63：非真相源，标记 proposal_only / non_authoritative）。
from __future__ import annotations
import secrets
import time
from typing import List

from src.revision.models import creative_source_signature
from src.revision.changeset import preview_operations_on_project, ChangeSetError

from .models import UnifiedChangePlan, ModuleProposal, PlanPreviewArtifact, ModuleOperationBundle
from .compilers import (compile_script_items, compile_character_items,
                          compile_storyboard_items)


class PureIDAllocator:
    """纯内存稳定 ID 分配器（§54/§61，无 workspace）。shot_id 每 scene 从 max 已用 c 号 +1
    （只增不回收）；scene_id 从 max 已用 s 号 +1（复用 Project.next_scene_seq 语义）。"""

    def __init__(self, source_project):
        self._shot_seq = {}
        for s in source_project.scenes:
            nums = []
            for sh in s.shots:
                if sh.shot_id.startswith(s.scene_id + "_c"):
                    try:
                        nums.append(int(sh.shot_id[(len(s.scene_id) + 2):]))
                    except ValueError:
                        pass
            self._shot_seq[s.scene_id] = (max(nums) + 1) if nums else 1
        used = []
        for s in source_project.scenes:
            if s.scene_id.startswith("s"):
                try:
                    used.append(int(s.scene_id[1:]))
                except ValueError:
                    pass
        self._scene_seq = (max(used) + 1) if used else 1

    def shot_id(self, scene_id: str) -> str:
        n = self._shot_seq.get(scene_id, 1)
        self._shot_seq[scene_id] = n + 1
        return f"{scene_id}_c{n:02d}"

    def scene_id(self) -> str:
        sid = f"s{self._scene_seq:02d}"
        self._scene_seq += 1
        return sid


class ProposalProjectionEngine:
    """§60：编译 plan → 纯内存投影 → PlanPreviewArtifact。零落盘。"""

    def build_preview(self, source_project, plan: UnifiedChangePlan,
                      proposals: List[ModuleProposal]) -> PlanPreviewArtifact:
        allocator = PureIDAllocator(source_project)
        # 索引：plan_item_id → ChangePlanItem；proposal_item_id → (item_dict, module)
        pi_by_id = {pi["plan_item_id"]: pi for pi in plan.direct_change_items}
        prop_item_by_id = {}
        for prop in proposals:
            for it in prop.items:
                prop_item_by_id[it.get("item_id")] = (it, prop.module)

        all_ops = []
        bundles = []
        for step in plan.steps:
            module = step.get("module", "")
            step_ops = []
            step_pids = []
            for piid in step.get("plan_item_ids", []):
                cpi = pi_by_id.get(piid)
                if not cpi:
                    continue
                for prop_item_id in cpi.get("proposal_item_ids", []):
                    step_pids.append(prop_item_id)
                    tup = prop_item_by_id.get(prop_item_id)
                    if not tup:
                        continue
                    item_dict, mod = tup
                    if mod != module:
                        continue   # §43 ownership：只编译本 step 模块的 item
                    if module == "script":
                        step_ops += compile_script_items([item_dict], source_project, allocator.scene_id)
                    elif module == "character":
                        step_ops += compile_character_items([item_dict], source_project)
                    elif module == "storyboard":
                        step_ops += compile_storyboard_items([item_dict], source_project, allocator.shot_id)
            all_ops += step_ops
            bundles.append(ModuleOperationBundle(
                step_id=step.get("step_id", ""), module=module,
                owner_agent_id=step.get("owner_agent_id", ""),
                proposal_item_ids=step_pids,
                operations=[o.to_dict() for o in step_ops]).to_dict())

        # §62：纯内存预览核心——零落盘、不建 workspace、不碰 canonical
        candidate, diff, impact, warnings = preview_operations_on_project(source_project, all_ops)
        projected_sig = creative_source_signature(candidate.to_dict())
        return PlanPreviewArtifact(
            preview_id="pv_" + secrets.token_hex(6),
            change_session_id=plan.change_session_id, plan_id=plan.plan_id,
            plan_version=plan.version, source_snapshot_signature=plan.source_snapshot_signature,
            module_operations=bundles,
            aggregate_diff={"diff": diff},
            aggregate_impact=impact.to_dict(),
            warnings=[{"message": w} for w in warnings],
            projected_signature=projected_sig,
            status="preview_only", applyable=False,   # §65 恒 False
            created_at=time.time())
