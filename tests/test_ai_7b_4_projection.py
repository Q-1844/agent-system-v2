# tests/test_ai_7b_4_projection.py
# AI-7B-4 纯内存投影 + 真实 Visual Diff focused Gate（§50/§60/§62/§65/§67-§70）。
#
# 不联网。覆盖：① 投影匹配预期 after-state ② 0 ProjectStore/Workspace/Production 落盘
# ③ 确定性 impact ④ 真实 Story/Character/Scene/Shot diff ⑤ applyable 恒 False
# ⑥ 全流程 resume×3→ready_for_review ⑦ insert_scene 投影新 scene ⑧ delete_char 留 forbidden ref→CHAR_REF_INVALID。
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.change_protocol import service as cps
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol.models import (UnifiedChangePlan, ChangePlanItem,
    ChangePlanStep, ModuleProposal, ModuleProposalItem)
from src.agents.change_protocol.projection import ProposalProjectionEngine
from src.revision.changeset import ChangeSetError


def _proj(name="demo"):
    sh = Shot(shot_id="s01_c01", scene_id="s01", index=1, description="see the gate",
              characters=["Xiaoyu"], target_duration_s=4)
    sh.first_frame.mark_done(local="ff.png", url="http://ff")   # 已有首帧 → 角色变更触发 regen 推荐
    sh.final_video = "v1"                                       # 已有视频
    return Project(
        name=name, style=StyleAnchor(),
        characters=[Character(name="Xiaoyu", description_en="short dark hair, gray coat"),
                    Character(name="Aran", description_en="a hero")],
        scenes=[Scene(scene_id="s01", summary="meet at the gate", characters=["Xiaoyu", "Aran"],
                      shots=[sh])],
        story_text="original story.")


def _rt(tmp_path, client=None):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client)


class ScriptedProposalClient:
    def __init__(self, script=None, character=None, director=None):
        self.script = script or {"title": "剧本", "summary": "改故事",
            "items": [{"action": "update_story_text", "new_story_text": "new story.", "reason": "r"}]}
        self.character = character or {"title": "角色", "summary": "改外观",
            "items": [{"action": "update_character", "name": "Xiaoyu",
                       "description_en": "long black hair", "reason": "r"}],
            "user_decisions_needed": []}
        self.director = director or {"title": "分镜", "summary": "改镜头",
            "items": [{"action": "update_shot", "shot_id": "s01_c01",
                       "description": "fast", "characters": ["Xiaoyu"],
                       "target_duration_s": 3, "reason": "r"}]}

    async def chat(self, messages, **kw):
        sys = messages[0]["content"] if messages else ""
        if "Script Writer" in sys: return json.dumps(self.script, ensure_ascii=False)
        if "Character Agent" in sys: return json.dumps(self.character, ensure_ascii=False)
        if "Director" in sys: return json.dumps(self.director, ensure_ascii=False)
        if "Producer" in sys:
            return json.dumps({"options": [{"option_id": "a", "label": "A", "description": "x"},
                                            {"option_id": "b", "label": "B", "description": "y"}]}, ensure_ascii=False)
        return json.dumps({"items": []}, ensure_ascii=False)


def _direct_plan(module, owner, items, sig="sig"):
    """单模块单步 plan + 一个 ModuleProposal，供直接投影测试。"""
    prop = ModuleProposal(proposal_id="mp1", change_session_id="cs", module=module,
        owner_agent_id=owner, round=1, status="completed", items=items,
        source_snapshot_signature=sig)
    plan = UnifiedChangePlan(plan_id="plan1", change_session_id="cs", project_name="demo",
        version=1, source_snapshot_signature=sig, status="validated",
        direct_change_items=[ChangePlanItem(plan_item_id="pi1", module=module,
            owner_agent_id=owner, proposal_item_ids=[it["item_id"] for it in items],
            scope_refs=[], summary="x", reason="r", source="user_requested").to_dict()],
        steps=[ChangePlanStep(step_id="s1", module=module, owner_agent_id=owner,
            plan_item_ids=["pi1"]).to_dict()],
        module_proposal_ids=["mp1"])
    return plan, prop


def _diff_entries(pv):
    return pv.aggregate_diff.get("diff", [])


# ============ ① 投影匹配 after-state + ④ 真实 diff + ② 0 落盘 ============
def test_projection_story_text(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path)
    proj = rt.project_intelligence("demo")._project()
    items = [ModuleProposalItem(item_id="it1", module="script", action="update_story_text",
        target_refs=[{"module": "script", "entity_type": "story", "entity_id": "story_text"}],
        intent={"new_story_text": "new story."}, reason="r", source="user_requested").to_dict()]
    plan, prop = _direct_plan("script", "script_writer", items)
    before = (tmp_path / "output" / "project.json").read_bytes()
    pv = ProposalProjectionEngine().build_preview(proj, plan, [prop])
    # ④ 真实 Story diff
    diffs = _diff_entries(pv)
    assert any(d["entity"] == "story" and d["field"] == "story_text"
               and d["before"] == "original story." and d["after"] == "new story." for d in diffs)
    # ① projected_signature 非空（投影态确实变了）
    assert pv.projected_signature and pv.projected_signature != pv.source_snapshot_signature
    # ⑤ applyable 恒 False
    assert pv.applyable is False
    # ② 0 落盘
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not (tmp_path / "workspaces").exists()


def test_projection_scene_update(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path)
    proj = rt.project_intelligence("demo")._project()
    items = [ModuleProposalItem(item_id="it1", module="script", action="update_scene",
        target_refs=[{"module": "scene", "entity_type": "scene", "entity_id": "s01"}],
        intent={"scene_id": "s01", "new_summary": "tragic fight"}, reason="r",
        source="user_requested").to_dict()]
    plan, prop = _direct_plan("script", "script_writer", items)
    pv = ProposalProjectionEngine().build_preview(proj, plan, [prop])
    diffs = _diff_entries(pv)
    assert any(d["entity"] == "scene" and d["id"] == "s01" and d["field"] == "summary"
               and d["after"] == "tragic fight" for d in diffs)
    assert pv.applyable is False


def test_projection_character_update_impact(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path)
    proj = rt.project_intelligence("demo")._project()
    items = [ModuleProposalItem(item_id="it1", module="character", action="update_character",
        target_refs=[{"module": "character", "entity_type": "character", "entity_id": "Xiaoyu"}],
        intent={"name": "Xiaoyu", "description_en": "long black hair"}, reason="r",
        source="user_requested").to_dict()]
    plan, prop = _direct_plan("character", "character_designer", items)
    pv = ProposalProjectionEngine().build_preview(proj, plan, [prop])
    diffs = _diff_entries(pv)
    # ④ 真实 Character diff
    assert any(d["entity"] == "character" and d["id"] == "Xiaoyu"
               and d["field"] == "description_en" and d["after"] == "long black hair" for d in diffs)
    # ③ 确定性 impact：CHARACTER_DESCRIPTION_CHANGED + s01_c01（含 Xiaoyu）frame/video regen
    imp = pv.aggregate_impact
    assert any(i["reason_code"] == "CHARACTER_DESCRIPTION_CHANGED" for i in imp["items"])
    assert "s01_c01" in imp["frame_regeneration_shot_ids"]
    assert "s01_c01" in imp["video_regeneration_shot_ids"]


def test_projection_insert_scene(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path)
    proj = rt.project_intelligence("demo")._project()
    items = [ModuleProposalItem(item_id="it1", module="script", action="insert_scene",
        target_refs=[{"module": "scene", "entity_type": "scene", "entity_id": "s01"}],
        intent={"after_scene_id": "s01", "summary": "new scene", "characters": ["Xiaoyu"]},
        reason="r", source="user_requested").to_dict()]
    plan, prop = _direct_plan("script", "script_writer", items)
    pv = ProposalProjectionEngine().build_preview(proj, plan, [prop])
    diffs = _diff_entries(pv)
    # 新 scene 被投影（s02，next_scene_seq 从 s01 → 2）
    assert any(d["entity"] == "scene" and d["kind"] == "insert" and d["id"] == "s02" for d in diffs)


# ============ ⑧ delete_char 留 forbidden ref → CHAR_REF_INVALID（§32 安全网）============
def test_projection_delete_char_forbidden(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path)
    proj = rt.project_intelligence("demo")._project()
    # Aran 在 s01 cast 但无镜头；删除后 scene cast 仍引用 Aran → _final_validate 拒
    items = [ModuleProposalItem(item_id="it1", module="character", action="delete_character",
        target_refs=[{"module": "character", "entity_type": "character", "entity_id": "Aran"}],
        intent={"name": "Aran"}, reason="r", source="user_requested").to_dict()]
    plan, prop = _direct_plan("character", "character_designer", items)
    import pytest
    with pytest.raises(ChangeSetError) as ei:
        ProposalProjectionEngine().build_preview(proj, plan, [prop])
    assert ei.value.code == "CHAR_REF_INVALID"


# ============ ⑥ 全流程 resume×3 → ready_for_review（7B-2→7B-3→7B-4）============
def test_full_flow_ready_for_review(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient()   # script=update_story_text
    rt = _rt(tmp_path, client=client)
    out = cps.create_session(rt, "demo", "修改结局让 Aran 离开城市")
    sid = out["session"]["session_id"]
    before = (tmp_path / "output" / "project.json").read_bytes()

    s1 = asyncio.run(cps.resume_session(rt, "demo", sid))   # round 1 → analyzing_dependencies
    assert s1["status"] == "analyzing_dependencies"
    s2 = asyncio.run(cps.resume_session(rt, "demo", sid))   # → validating_plan (validated)
    assert s2["status"] == "validating_plan"
    s3 = asyncio.run(cps.resume_session(rt, "demo", sid))   # → ready_for_review (preview built)
    assert s3["status"] == "ready_for_review"
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    plan = store.get_plan(s3["active_plan_id"])
    assert plan.status == "preview_ready"
    pv = store.get_preview(plan.preview_id)
    assert pv is not None and pv.applyable is False
    assert pv.aggregate_diff.get("diff")   # 有 diff
    # ② 全流程 0 落盘
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not (tmp_path / "workspaces").exists()
    assert not (tmp_path / "output" / "ai" / "production_plans.json").exists()
