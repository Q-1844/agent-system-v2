# tests/test_ai_7b_3_analysis.py
# AI-7B-3 依赖/Impact/冲突 + Producer 编译器 + PlanValidator focused Gate（§23/§27-§49）。
#
# 不联网（ScriptedProposalClient，含 Producer 决策选项分派）。每 case 独立 tmp_path。
# 覆盖（§124 gate）：① 单模块 ② 两模块 ③ 三模块（Director round 1）④ 硬冲突→blocked
# ⑤ 创作决策→awaiting→submit→resume→validated ⑥ 覆盖失败→blocked ⑦ ownership 失败（validator）
# ⑧ 全流程 0 mutation。
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.change_protocol import service as cps
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol.models import (ChangeRequest, ChangeSession,
    UnifiedChangePlan, ChangePlanItem, ModuleProposal)
from src.agents.change_protocol.plan_validator import validate as validate_plan


def _proj(name="demo"):
    return Project(
        name=name, style=StyleAnchor(),
        characters=[Character(name="Xiaoyu", description_en="short dark hair, gray coat"),
                    Character(name="Aran", description_en="a hero")],
        scenes=[Scene(scene_id="s01", summary="meet at the gate", characters=["Xiaoyu", "Aran"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="see the gate", characters=["Xiaoyu"],
                                  target_duration_s=4)])],
        story_text="Xiaoyu meets Aran at the gate.")


def _rt(tmp_path, client=None):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client)


class ScriptedProposalClient:
    """按 system prompt 角色分派：Script Writer→script / Character Agent→character /
    Director→director / Producer→决策选项。fail_module 模拟指定模块失败。"""
    def __init__(self, script=None, character=None, director=None, fail_module=None):
        self.script = script or {"title": "剧本", "summary": "改结局",
            "items": [{"action": "update_story_text", "new_story_text": "new story", "reason": "ending"}]}
        self.character = character or {"title": "角色", "summary": "改外观",
            "items": [{"action": "update_character", "name": "Xiaoyu",
                       "description_en": "long black hair, gray coat", "reason": "appearance"}],
            "user_decisions_needed": []}
        self.director = director or {"title": "分镜", "summary": "更快",
            "items": [{"action": "update_shot", "shot_id": "s01_c01",
                       "description": "fast tense shot", "characters": ["Xiaoyu"],
                       "target_duration_s": 3, "reason": "faster"}]}
        self.fail_module = fail_module
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        sys = messages[0]["content"] if messages else ""
        if "Script Writer" in sys:
            if self.fail_module == "script": raise Exception("fail script")
            return json.dumps(self.script, ensure_ascii=False)
        if "Character Agent" in sys:
            if self.fail_module == "character": raise Exception("fail character")
            return json.dumps(self.character, ensure_ascii=False)
        if "Director" in sys:
            if self.fail_module == "storyboard": raise Exception("fail storyboard")
            return json.dumps(self.director, ensure_ascii=False)
        if "Producer" in sys:   # 决策选项
            return json.dumps({"options": [
                {"option_id": "a", "label": "方向 A", "description": "first interpretation"},
                {"option_id": "b", "label": "方向 B", "description": "second interpretation"}]},
                ensure_ascii=False)
        return json.dumps({"items": []}, ensure_ascii=False)


def _make(rt, project, request_text):
    out = cps.create_session(rt, project, request_text)
    return out["session"]["session_id"]


def _resume(rt, project, sid):
    return asyncio.run(cps.resume_session(rt, project, sid))


# ============ ① 单模块（script update_story_text）→ validated ============
def test_single_module_validated(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient(script={"title": "剧本", "summary": "改故事",
        "items": [{"action": "update_story_text", "new_story_text": "Aran leaves the city.", "reason": "ending"}]})
    rt = _rt(tmp_path, client=client)
    sid = _make(rt, "demo", "修改结局让 Aran 离开城市")
    _resume(rt, "demo", sid)   # round 1
    before = (tmp_path / "output" / "project.json").read_bytes()
    s = _resume(rt, "demo", sid)   # analyze+compile+validate
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    plan = store.get_plan(s["active_plan_id"])
    assert plan.status == "validated"
    assert any(i["module"] == "script" and "update_story_text" in i["summary"]
               for i in plan.direct_change_items)
    reasons = {d["reason"] for d in plan.downstream_change_items}
    assert "STORY_TEXT_CHANGED" in reasons
    # unchanged scope：未触及的 scene/character
    assert any(u["entity_id"] == "s01" for u in plan.unchanged_scope)
    assert any(u["entity_id"] == "Xiaoyu" for u in plan.unchanged_scope)
    # 0 mutation
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not (tmp_path / "workspaces").exists()


# ============ ② 两模块（script update_story_text + character）→ validated ============
def test_two_modules_validated(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient()   # script=update_story_text, character=update_character
    rt = _rt(tmp_path, client=client)
    sid = _make(rt, "demo", "把 Xiaoyu 头发改长并修改结局")
    _resume(rt, "demo", sid)
    s = _resume(rt, "demo", sid)
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    plan = store.get_plan(s["active_plan_id"])
    assert plan.status == "validated"
    mods = {i["module"] for i in plan.direct_change_items}
    assert {"script", "character"} <= mods
    assert len(plan.steps) >= 2   # script + character steps（并行 group 1）
    # 两步无相互依赖（并行）
    assert all(not st["depends_on_step_ids"] for st in plan.steps if st["module"] in ("script", "character"))


# ============ ③ 三模块（Director round 1，有 scene scope）→ validated ============
def test_three_modules_director_round1_validated(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient(script={"title": "剧本", "summary": "改结局段",
        "items": [{"action": "update_scene", "scene_id": "s01", "new_summary": "tragic fight", "reason": "ending"}]})
    rt = _rt(tmp_path, client=client)
    sid = _make(rt, "demo", "把 Xiaoyu 头发改长并修改结局，让场景 s01 分镜更悲壮")
    _resume(rt, "demo", sid)   # script + character + director round 1 (scene s01)
    s = _resume(rt, "demo", sid)
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    plan = store.get_plan(s["active_plan_id"])
    assert plan.status == "validated"
    mods = {i["module"] for i in plan.direct_change_items}
    assert {"script", "character", "storyboard"} <= mods
    # storyboard step 依赖 script/character 步骤（§40 编排）
    sb_step = next(st for st in plan.steps if st["module"] == "storyboard")
    assert sb_step["depends_on_step_ids"]   # 非空


# ============ ④ 硬冲突（delete scene + 改其内镜头）→ blocked ============
def test_hard_conflict_blocked(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient(
        script={"title": "剧本", "summary": "删段", "items": [{"action": "delete_scene", "scene_id": "s01", "reason": "remove"}]},
        director={"title": "分镜", "summary": "改镜头", "items": [{"action": "update_shot", "shot_id": "s01_c01", "description": "x", "characters": ["Xiaoyu"], "target_duration_s": 3, "reason": "faster"}]})
    rt = _rt(tmp_path, client=client)
    sid = _make(rt, "demo", "修改结局并让场景 s01 分镜更快")   # script(结局)+storyboard(分镜), scene_refs=[s01]
    _resume(rt, "demo", sid)
    s = _resume(rt, "demo", sid)
    assert s["status"] == "blocked"   # §46 未解决硬冲突
    assert "硬冲突" in (s["error"] or "")


# ============ ⑤ 创作决策 → awaiting → submit → resume → validated ============
def test_creative_decision_flow(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient(character={"title": "角色", "summary": "改外观+歧义",
        "items": [{"action": "update_character", "name": "Xiaoyu", "description_en": "long black hair", "reason": "appearance"}],
        "user_decisions_needed": ["是否保留旧版角色图"]})
    rt = _rt(tmp_path, client=client)
    sid = _make(rt, "demo", "把 Xiaoyu 头发改长")
    _resume(rt, "demo", sid)                 # round 1
    s = _resume(rt, "demo", sid)             # → awaiting_user_decision
    assert s["status"] == "awaiting_user_decision"
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    dec = store.list_decisions(sid)[0]
    assert dec.blocking and dec.status == "pending"
    assert len(dec.options) == 2
    # 用户回答（submit_decision 是同步的，7B-1）
    cps.submit_decision(rt, "demo", sid, dec.decision_id, "a")
    s2 = _resume(rt, "demo", sid)            # → recompile → validated
    assert s2["status"] == "validating_plan"
    plan = store.get_plan(s2["active_plan_id"])
    assert plan.status == "validated"
    assert dec.decision_id in plan.user_decision_ids


# ============ ⑥ 覆盖为空（提案 items 空）→ AI-7D-R §10 诚实 no_action ============
# R1 之前：空提案 → blocked + PLAN_REQUEST_NOT_COVERED，进而伪造 A/B 方案让用户批准空计划。
# R1 起：所有 specialist 确认无可执行改动且无真实待决问题 → 终态 no_action（不伪造、不声称修改）。
def test_coverage_empty_terminates_no_action(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient(script={"title": "空", "summary": "无需改动", "items": []})
    rt = _rt(tmp_path, client=client)
    sid = _make(rt, "demo", "修改结局")
    _resume(rt, "demo", sid)
    s = _resume(rt, "demo", sid)
    assert s["status"] == "no_action"
    assert s.get("no_action_reason")   # 有诚实原因，非空错误/伪造方案


# ============ ⑦ ownership 失败（validator 直测）============
def test_validator_ownership_failure(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    plan = UnifiedChangePlan(plan_id="p", change_session_id="cs", project_name="demo",
        version=1, source_snapshot_signature="sig", status="draft",
        direct_change_items=[ChangePlanItem(plan_item_id="pi", module="script",
            owner_agent_id="director",   # wrong owner
            proposal_item_ids=["it"], scope_refs=[], summary="x", reason="r",
            source="user_requested").to_dict()])
    session = ChangeSession(session_id="cs", project_name="demo", source_snapshot_signature="sig")
    request = ChangeRequest(request_id="cr", project_name="demo", original_user_request="x",
                            source_snapshot_signature="sig")
    proposals = [ModuleProposal(proposal_id="mp", change_session_id="cs", module="script",
        owner_agent_id="script_writer", round=1, status="completed",
        items=[{"item_id": "it", "module": "script", "action": "update_story_text",
                "intent": {}, "reason": "", "source": "user_requested", "required": True,
                "depends_on_item_ids": []}])]
    issues = validate_plan(request, session, plan, proposals, _proj(), [], [])
    assert any(i["code"] == "PLAN_OWNER_MISMATCH" for i in issues)


# ============ ⑧ 全流程 0 mutation ============
def test_full_flow_no_mutation(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    client = ScriptedProposalClient()
    rt = _rt(tmp_path, client=client)
    sid = _make(rt, "demo", "把 Xiaoyu 头发改长并修改结局")
    before = (tmp_path / "output" / "project.json").read_bytes()
    _resume(rt, "demo", sid)
    _resume(rt, "demo", sid)   # validated
    # canonical / Workspace / Production Plan 全未碰
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not (tmp_path / "workspaces").exists()
    assert not (tmp_path / "output" / "ai" / "production_plans.json").exists()
    assert not (tmp_path / "cache" / "jobs.json").exists()
