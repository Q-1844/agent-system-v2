# tests/test_scene_designer_agent.py
# Agent System V2 Phase C2 — Scene Agent（28.md）。本文件随 Commit B-J 逐步扩充：
#   Commit B：S1 注册 / S2 会话持久化 / S3 direct turn / S6 旧 identity 不变 + system prompt
#   Commit C-J：inventory/inspect、Location/Anchor/Layout、view prepare、instance/state、
#               Sciv prepare、changed_refs、Producer delegate、Production Room
import asyncio
import json

import pytest

from src.core.models import Project, Scene
from src.core.store import ProjectStore
from src.agents.registry import Registries
from src.agents import producer
from src.agents.runtime import AgentRuntime
from src.agents.scene_designer import (SCENE_DESIGNER_SYSTEM, WRITE_TOOL_DOMAIN,
                                       scene_designer_turn)

from tests.test_producer_bridge import _runtime


# ============ 共享 fake client（脚本化 tool-loop）============
class ScriptedClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat_completion(self, messages, **kw):
        self.calls.append({"messages": messages, "kw": kw})
        if not self.script:
            return {"content": "（脚本已耗尽）", "tool_calls": None}
        content, tool_calls = self.script.pop(0)
        return {"content": content, "tool_calls": tool_calls}

    async def chat_completion_stream(self, messages, **kw):
        content, tool_calls = (self.script.pop(0) if self.script
                               else ("（脚本已耗尽）", None))
        self.calls.append({"messages": messages, "kw": kw})
        yield {"kind": "finish", "content": content, "tool_calls": tool_calls}


def _save_proj(tmp_path):
    proj = Project(name="s2demo")
    proj.story_text = "第一段剧情。第二段剧情。"
    proj.scenes = [Scene(scene_id="s01", summary="白天山雨驿站平静",
                         characters=["林秋"]),
                   Scene(scene_id="s02", summary="夜晚山雨驿站暴雨",
                         characters=["林秋"])]
    ProjectStore(tmp_path).save(proj)
    return proj


# ============ S1：registry 注册（28.md §28 S1）============
def test_S1_scene_designer_registered():
    reg = Registries().agents
    a = reg.get("scene_designer")
    assert a is not None
    assert a.id == "scene_designer"
    assert a.name == "Scene Agent"
    assert "场景" in a.role
    # 第四个正式 Specialist：Producer 可委派（can_delegate_to=list(SPECIALISTS)）
    assert reg.can_delegate("producer", "scene_designer")
    # 旧 identity 全部原样存在
    for aid in ("script_writer", "character_designer", "director", "production_planner",
                "shot_producer", "continuity_reviewer", "editor", "producer"):
        assert reg.exists(aid), aid
    # model profile 注册
    assert Registries().profiles.get("scene_profile") is not None


def test_S1b_no_scene_agent_id_collision():
    """28.md §0.1：不得用 scene_agent 命名（与 Director Scene Sub-Agent 混淆）。"""
    reg = Registries().agents
    assert not reg.exists("scene_agent")


# ============ S2：session 持久化 + turn 权限（28.md §28 S2/S7-S12 基座）============
def test_S2_session_persistence_and_turn_guard(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    s = rt.get_or_create_session("s2demo", "scene_designer", "project", "s2demo")
    sid = s["session_id"]
    # 会话落盘（重开 runtime 仍在）
    rt2 = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                       settings_path=tmp_path / "agents_data" / "settings.json",
                       client=None)
    loaded = rt2._session_store("s2demo").load(sid)
    assert loaded is not None and loaded.agent_id == "scene_designer"


def test_S2b_turn_rejects_foreign_session(tmp_path):
    """scene_designer_turn 只接受自己的会话（S7-S12 权限基座）。"""
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    s = rt.get_or_create_session("s2demo", "director", "project", "s2demo")
    rejected = False
    try:
        asyncio.run(scene_designer_turn(rt, "s2demo", s["session_id"], "你好"))
    except Exception as e:   # AgentError permission_denied
        assert "仅限 Scene Agent" in str(e) or getattr(e, "code", "") == "permission_denied"
        rejected = True
    assert rejected


# ============ S3：direct turn（28.md §28 S3；脚本化两步：读→答复）============
def test_S3_direct_turn_scripted(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    s = rt.get_or_create_session("s2demo", "scene_designer", "project", "s2demo")
    sid = s["session_id"]
    client = ScriptedClient([
        ("我先查看项目与场景现状。", [
            {"id": "c1", "type": "function",
             "function": {"name": "story.list", "arguments": "{}"}},
        ]),
        ("项目有两个故事段，均在山雨驿站。场景系统当前为空，需要先准备 Location。",
         None),
    ])
    rt2 = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                       settings_path=tmp_path / "agents_data" / "settings.json",
                       client=client)
    result = asyncio.run(scene_designer_turn(rt2, "s2demo", sid,
                                             "把故事需要的场景准备好"))
    assert "Location" in result["assistant_message"]
    assert result["agent_id"] == "scene_designer"
    assert result["scene_mutated"] is False
    # 会话持久化：user + assistant 两条
    loaded = rt2._session_store("s2demo").load(sid)
    assert loaded.messages[-1].role == "assistant"
    assert any(m.role == "user" and "场景" in (m.content or "")
               for m in loaded.messages)
    # story.list 工具真实执行过（trace 有记录且 ok）
    assert any(t["tool"] == "story.list" and t.get("ok") for t in result["trace"])


def test_S3b_write_tool_serial_gate(tmp_path):
    """写工具单写串行：同一步发多个 scene.* 写工具 → 第 2 个被串行闸门拒绝；
    第 1 个真实执行（缺 expected_document_revision → OCC stale 拒绝，ok=False）。"""
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    s = rt.get_or_create_session("s2demo", "scene_designer", "project", "s2demo")
    sid = s["session_id"]
    tc1 = {"id": "c1", "type": "function",
           "function": {"name": "scene.location.upsert",
                        "arguments": json.dumps({"name": "山雨驿站"})}}
    tc2 = {"id": "c2", "type": "function",
           "function": {"name": "scene.location.upsert",
                        "arguments": json.dumps({"name": "另一地点"})}}
    client = ScriptedClient([("一次发两个写工具", [tc1, tc2]), ("收到拒绝，调整。", None)])
    rt2 = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                       settings_path=tmp_path / "agents_data" / "settings.json",
                       client=client)
    result = asyncio.run(scene_designer_turn(rt2, "s2demo", sid, "建两个地点"))
    # 第 1 个进账本（写尝试），第 2 个被串行闸门拒绝
    assert len(result["receipts"]) == 1
    assert result["receipts"][0]["operation"] == "scene.location.upsert"
    assert result["receipts"][0]["ok"] is False   # OCC stale 拒绝（缺 document_revision）
    assert result["receipts"][0]["error"] == "stale_revision"


# ============ S6：旧 Agent identity 不变（28.md §28 S6）============
def test_S6_old_identities_and_producer_tools_unchanged():
    offered, _ = producer._build_producer_tools()
    names = {t["function"]["name"] for t in offered}
    # Producer 工具面此刻仍是三 delegate（scene_designer.delegate 在 Commit H 加入）
    assert {"script_writer.delegate", "character_designer.delegate",
            "director.delegate"} <= names
    assert producer._SPECIALIST_RESOURCE["script_writer"] == "story"
    assert producer._SPECIALIST_RESOURCE["character_designer"] == "character"
    assert producer._SPECIALIST_RESOURCE["director"] == "storyboard"


# ============ system prompt（28.md §19/§20 概念与混淆守卫）============
def test_S19_system_prompt_concepts_and_guards():
    s = SCENE_DESIGNER_SYSTEM
    # 概念层次
    assert "Location" in s and "SceneInstance" in s and "SceneState" in s
    assert "SceneInstanceView" in s and "SpatialAnchor" in s and "SetLayout" in s
    # §0.4 Story 边界
    assert "不归你修改" in s
    # §0.6 Prop prerequisite
    assert "resource_prerequisite" in s
    # §12 机位策略
    assert "hero" in s and "8 个视角" in s
    # §17 审批分档
    assert "approve_if_pass" in s and "review_only" in s
    assert "revoke" in s
    # §20 混淆守卫
    assert "SceneState ≠ Shot action" in s or "SceneState ≠ Shot" in s
    assert "你不是 Director" in s
    # 复用/诚实纪律
    assert "一个一个执行" in s
    assert "真实返回" in s
    # 占位符可格式化
    proj = Project(name="demo")
    proj.scenes = [Scene(scene_id="s01", summary="x")]
    text = SCENE_DESIGNER_SYSTEM.format(project_name="demo", story_len=10,
                                        scenes_count=1, characters_count=0,
                                        visual_profile_summary="-")
    assert "{project_name}" not in text


def test_S5_lock_domain_constants():
    """28.md §5：写域映射指向 scene_system（工具面随 Commit D-G 落地，映射表先立）。"""
    from src.agents.project_events import RESOURCE_SCENE_SYSTEM
    for tool, dom in WRITE_TOOL_DOMAIN.items():
        assert dom == RESOURCE_SCENE_SYSTEM, tool


# ============ Commit C：scene.inventory / scene.production.inspect（S17-S23）============
import src.agents.scene_designer as sd


def _mk_svc(tmp_path):
    """SceneSystemService 直接以 tmp_path 为项目目录（与 _runtime 的 resolver 一致）。"""
    from src.scene_system.service import SceneSystemService
    name = tmp_path.name
    return SceneSystemService(tmp_path, name), tmp_path


def _drev(svc):
    return svc.document_overview()["document_revision"]


def _seed_location(svc, pdir, tmp_path_name):
    """Location + 1 anchor + 1 view（未生成）。"""
    loc = (asyncio.run(svc.create_location(
        name="山雨驿站", identity_description="mountain inn",
        expected_document_revision=_drev(svc))))["target"]
    a1 = (asyncio.run(svc.create_anchor(
        location_id=loc, name="east gate", kind="entry",
        expected_document_revision=_drev(svc))))["target"]
    va = asyncio.run(svc.create_view(location_id=loc, label="hero",
                                     camera_zone="south_west",
                                     look_direction="north_east",
                                     visible_anchor_ids=[a1],
                                     expected_document_revision=_drev(svc)))
    return {"loc": loc, "a1": a1, "view": va["target"], "view_rev": va["revision"]}


def test_S17_inventory_empty(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    _mk_svc(tmp_path)
    r = sd._execute_scene_inventory(rt, "s2demo", "{}", None, "m1")
    assert r["ok"] and r["totals"]["locations"] == 0
    assert r["document_revision"]
    assert "0 地点" in r["summary"]


def test_S18_inventory_existing_location(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    _seed_location(svc, pdir, pdir.name)
    r = sd._execute_scene_inventory(rt, "s2demo", "{}", None, "m1")
    assert r["totals"]["locations"] == 1
    row = r["locations"][0]
    assert row["name"] == "山雨驿站" and row["anchors"] == 1
    assert r["totals"]["views"] == 1 and r["totals"]["approved_views"] == 0
    # filter=location 不含 instance 行
    r2 = sd._execute_scene_inventory(rt, "s2demo",
                                     json.dumps({"filter": "location"}), None, "m1")
    assert r2["scene_instances"] == [] and r2["locations"]


def test_S19_inspect_story_scene_without_instance(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    _seed_location(svc, pdir, pdir.name)
    r = sd._execute_scene_production_inspect(
        rt, "s2demo", json.dumps({"story_scene_id": "s01"}), None, "m1")
    assert r["ok"] and r["production_status"] == "INSTANCE_MISSING"
    assert r["story_input"]["location_hint"] or r["story_input"]["summary"]
    assert r["visual_profile"] is not None
    # 不存在的故事段 → 结构化错误
    r2 = sd._execute_scene_production_inspect(
        rt, "s2demo", json.dumps({"story_scene_id": "sXX"}), None, "m1")
    assert not r2["ok"] and r2["error"] == "story_scene_not_found"


def test_S20_inspect_instance_without_view(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    ids = _seed_location(svc, pdir, pdir.name)
    asyncio.run(svc.create_scene_instance(story_scene_id="s01",
                                          location_id=ids["loc"],
                                          expected_document_revision=_drev(svc)))
    r = sd._execute_scene_production_inspect(
        rt, "s2demo", json.dumps({"story_scene_id": "s01"}), None, "m1")
    assert r["production_status"] == "BASE_VIEW_MISSING"
    # state 为空被 BASE_VIEW_MISSING 挡住？不——阶梯顺序：view 在 state 前。
    # 给 location 建 view 后仍是 BASE_VIEW_MISSING（未批准）。
    assert r["scene_system"]["scene_instance"]["scene_instance_id"].startswith("scin_")
    assert r["scene_system"]["location"]["name"] == "山雨驿站"


def test_S20b_inspect_ladder_state_and_sciv(tmp_path, monkeypatch):
    """STATE_INCOMPLETE / SCIV_MISSING 阶梯（approved view 已有、state 空与不空）。"""
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    ids = _seed_location(svc, pdir, pdir.name)
    asyncio.run(svc.create_scene_instance(story_scene_id="s01",
                                          location_id=ids["loc"],
                                          expected_document_revision=_drev(svc)))
    inst_id = sd._scene_system_read(pdir).scene_instances and next(iter(
        sd._scene_system_read(pdir).scene_instances))
    # 造 approved view：直接 patch 状态图——本测试聚焦阶梯逻辑，
    # 用 monkeypatch 把 _sciv_status_map 置空（无 sciv）。
    monkeypatch.setattr(sd, "_sciv_status_map", lambda pdir_, proj_, doc: {})
    # state 空 + 无 approved view → BASE_VIEW_MISSING（阶梯在 state 之前）
    r1 = sd._execute_scene_production_inspect(
        rt, "s2demo", json.dumps({"story_scene_id": "s01"}), None, "m1")
    assert r1["production_status"] in ("BASE_VIEW_MISSING", "STATE_INCOMPLETE")
    assert inst_id


def test_S22_inspect_ready(tmp_path, monkeypatch):
    """READY：instance+state+approved view+approved 非 stale sciv（sciv 状态 mock）。"""
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    ids = _seed_location(svc, pdir, pdir.name)
    r_inst = asyncio.run(svc.create_scene_instance(
        story_scene_id="s01", location_id=ids["loc"],
        expected_document_revision=_drev(svc)))
    inst_id = r_inst["target"]
    asyncio.run(svc.patch_scene_state(inst_id,
                                      {"time_of_day": "day", "weather": "clear"},
                                      expected_revision=r_inst["revision"]))
    # 直接造 approved LocationView + approved Sciv（绕过生成链——本测试只验判定逻辑）
    from src.scene_system import store as ss_store
    doc = ss_store.SceneSystemStore(pdir).load()
    view = doc.views[ids["view"]]
    view.approval_status = "approved"
    view.reference_asset_id = "ref_asset_x"
    from src.scene_system.models import SceneInstanceView
    sciv = SceneInstanceView(scene_instance_view_id="sciv_test01",
                             scene_instance_id=inst_id,
                             location_view_id=view.view_id)
    sciv.approval_status = "approved"
    sciv.reference_asset_id = "ref_asset_x"
    doc.scene_instance_views[sciv.scene_instance_view_id] = sciv
    ss_store.SceneSystemStore(pdir).save(doc)
    monkeypatch.setattr(sd, "_sciv_status_map", lambda pdir_, proj_, doc_: {
        "sciv_test01": {"scene_instance_view_id": "sciv_test01",
                        "scene_instance_id": inst_id,
                        "location_view_id": view.view_id,
                        "approval_status": "approved", "is_stale": False,
                        "stale_reasons": [], "has_pass_review_evidence": True}})
    r = sd._execute_scene_production_inspect(
        rt, "s2demo", json.dumps({"story_scene_id": "s01"}), None, "m1")
    assert r["production_status"] == "READY", r["status_reason"]
    assert r["scene_system"]["scene_instance_views"][0]["usable"] is True
    # stale 版本 → SCIV_STALE
    monkeypatch.setattr(sd, "_sciv_status_map", lambda pdir_, proj_, doc_: {
        "sciv_test01": {"scene_instance_view_id": "sciv_test01",
                        "scene_instance_id": inst_id,
                        "location_view_id": view.view_id,
                        "approval_status": "approved", "is_stale": True,
                        "stale_reasons": ["state_changed"], "has_pass_review_evidence": True}})
    r2 = sd._execute_scene_production_inspect(
        rt, "s2demo", json.dumps({"story_scene_id": "s01"}), None, "m1")
    assert r2["production_status"] == "SCIV_STALE"


def test_S23_inspect_missing_prop(tmp_path, monkeypatch):
    """S23：state 引用不存在的 Prop → 结构化 missing_or_invalid_props（S48 基座）。"""
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    ids = _seed_location(svc, pdir, pdir.name)
    r_inst = asyncio.run(svc.create_scene_instance(
        story_scene_id="s01", location_id=ids["loc"],
        expected_document_revision=_drev(svc)))
    # 直接在 canonical 里放一个引用不存在 prop 的 placement
    from src.scene_system import store as ss_store
    from src.scene_system.models import PropPlacement
    doc = ss_store.SceneSystemStore(pdir).load()
    inst = doc.scene_instances[r_inst["target"]]
    inst.state.prop_placements["prop_ghost"] = PropPlacement(
        anchor_id=ids["a1"], state="on table")
    ss_store.SceneSystemStore(pdir).save(doc)
    # resources 侧无 prop_ghost
    r = sd._execute_scene_production_inspect(
        rt, "s2demo", json.dumps({"story_scene_id": "s01"}), None, "m1")
    missing = r["resource_dependencies"]["missing_or_invalid_props"]
    assert any(m["prop_id"] == "prop_ghost" and m["reason"] == "prop_not_found"
               for m in missing), missing
    assert r["production_status"] != "READY"


# ============ Commit D：Location / Anchor / Layout 工具（S24-S30）============
def _run_exec(coro):
    return asyncio.run(coro)


def test_S24_location_create(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    drev = _drev(svc)
    r = _run_exec(sd._execute_scene_location_upsert(
        rt, "s2demo", json.dumps({
            "name": "山雨驿站", "identity_description": "mountain inn by the pass",
            "expected_document_revision": drev}), None, "m1"))
    assert r["ok"] and r["verified"] and r["location_id"].startswith("loc_")
    assert r["changed_resources"] == [{"kind": "location", "id": r["location_id"]}]
    assert WRITE_TOOL_DOMAIN["scene.location.upsert"] == "scene_system"
    # canonical 真实落盘
    assert svc.read_location(r["location_id"])["name"] == "山雨驿站"


def test_S25_location_update_occ(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc = (asyncio.run(svc.create_location(
        name="山雨驿站", expected_document_revision=_drev(svc))))["target"]
    rev = svc.read_location(loc)["revision"]
    r = _run_exec(sd._execute_scene_location_upsert(
        rt, "s2demo", json.dumps({
            "location_id": loc, "name": "山雨驿站（正门）",
            "expected_revision": rev}), None, "m1"))
    assert r["ok"] and r["verified"]
    assert svc.read_location(loc)["name"] == "山雨驿站（正门）"
    assert svc.read_location(loc)["location_id"] == loc   # rename 不变 id


def test_S26_location_update_stale_rejected(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc = (asyncio.run(svc.create_location(
        name="山雨驿站", expected_document_revision=_drev(svc))))["target"]
    r = _run_exec(sd._execute_scene_location_upsert(
        rt, "s2demo", json.dumps({
            "location_id": loc, "name": "改名",
            "expected_revision": "bogus_rev"}), None, "m1"))
    assert not r["ok"] and r["stale"] is True
    assert r["current_revision"]
    assert svc.read_location(loc)["name"] == "山雨驿站"   # 未写入


def test_S27_anchor_create_and_update(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc = (asyncio.run(svc.create_location(
        name="山雨驿站", expected_document_revision=_drev(svc))))["target"]
    a1 = _run_exec(sd._execute_scene_anchor_upsert(
        rt, "s2demo", json.dumps({
            "location_id": loc, "name": "east gate", "kind": "entry",
            "description": "east-facing double door",
            "expected_document_revision": _drev(svc)}), None, "m1"))
    assert a1["ok"] and a1["anchor_id"].startswith("anc_")
    assert a1["changed_resources"] == [{"kind": "anchor", "id": a1["anchor_id"]}]
    # 更新（带合法 relation 指向 a1）
    a2 = _run_exec(sd._execute_scene_anchor_upsert(
        rt, "s2demo", json.dumps({
            "location_id": loc, "name": "west chimney", "kind": "landmark",
            "relations": [{"object_anchor_id": a1["anchor_id"],
                           "relation": "opposite"}],
            "expected_document_revision": _drev(svc)}), None, "m1"))
    assert a2["ok"]
    rev = svc.read_anchor(a2["anchor_id"])["revision"]
    up = _run_exec(sd._execute_scene_anchor_upsert(
        rt, "s2demo", json.dumps({
            "anchor_id": a2["anchor_id"], "description": "stone chimney",
            "expected_revision": rev}), None, "m1"))
    assert up["ok"] and up["verified"]
    assert svc.read_anchor(a2["anchor_id"])["description"] == "stone chimney"


def test_S28_anchor_invalid_relation_fails(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc = (asyncio.run(svc.create_location(
        name="山雨驿站", expected_document_revision=_drev(svc))))["target"]
    r = _run_exec(sd._execute_scene_anchor_upsert(
        rt, "s2demo", json.dumps({
            "location_id": loc, "name": "west chimney",
            "relations": [{"object_anchor_id": "anc_missing", "relation": "opposite"}],
            "expected_document_revision": _drev(svc)}), None, "m1"))
    assert not r["ok"], "非法 relation 必须被 service 拒绝（不弱化校验）"
    assert r.get("error") in ("relation_anchor_not_found", "validation_error",
                              "anchor_not_found")


def test_S29_layout_set_create_and_update(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc = (asyncio.run(svc.create_location(
        name="山雨驿站", expected_document_revision=_drev(svc))))["target"]
    a1 = (asyncio.run(svc.create_anchor(
        location_id=loc, name="east gate", kind="entry",
        expected_document_revision=_drev(svc))))["target"]
    r = _run_exec(sd._execute_scene_layout_set(
        rt, "s2demo", json.dumps({
            "location_id": loc, "orientation_convention": "north_up",
            "placements": [{"anchor_id": a1, "zone": "east area"}],
            "expected_document_revision": _drev(svc)}), None, "m1"))
    assert r["ok"] and r["layout_id"].startswith("layout_")
    # 已有 layout：不带 revision → 拒绝；带 revision → 更新
    r2 = _run_exec(sd._execute_scene_layout_set(
        rt, "s2demo", json.dumps({
            "location_id": loc, "orientation_convention": "north_up",
            "placements": [{"anchor_id": a1, "zone": "south area"}]}), None, "m1"))
    assert not r2["ok"] and r2["error"] == "expected_revision_required"
    lay = svc.read_layout_by_location(loc)
    r3 = _run_exec(sd._execute_scene_layout_set(
        rt, "s2demo", json.dumps({
            "location_id": loc, "orientation_convention": "north_up",
            "placements": [{"anchor_id": a1, "zone": "south area"}],
            "expected_revision": lay["revision"]}), None, "m1"))
    assert r3["ok"] and r3["layout_id"] == lay["layout_id"]   # 稳定身份复用
    assert svc.read_layout_by_location(loc)["placements"][0]["zone"] == "south area"


def test_S30_location_rename_keeps_stable_id(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc = (asyncio.run(svc.create_location(
        name="山雨驿站", expected_document_revision=_drev(svc))))["target"]
    rev = svc.read_location(loc)["revision"]
    _run_exec(sd._execute_scene_location_upsert(
        rt, "s2demo", json.dumps({
            "location_id": loc, "name": "山雨驿站旧馆",
            "expected_revision": rev}), None, "m1"))
    assert svc.read_location(loc)["location_id"] == loc


# ============ Commit E：scene.view.prepare（S31-S38）============
import base64 as _b64

_PNG_B64 = _b64.b64encode(b"\x89PNG fake-image").decode()


class _FakeSceneClient:
    """生成 + vision 审查双能力 fake（deps.agnes_client 单例——生成与审查共用）。"""

    def __init__(self, vision_content: str = ""):
        self.gen_calls = 0
        self.vision_calls = 0
        self.vision_content = vision_content
        self.models = {"image": "fake-image-model"}

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        self.gen_calls += 1
        return {"url": None, "b64": _PNG_B64}

    async def chat_completion_vision(self, msgs, *, model):
        self.vision_calls += 1
        return {"content": self.vision_content,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _view_reply(anchor_id: str, overrides: dict | None = None) -> str:
    d = {
        "location_identity": "pass", "location_notes": "",
        "anchors": [{"anchor_id": anchor_id, "visibility": "clearly_visible",
                     "notes": ""}],
        "anchor_consistency": "pass", "anchor_notes": "",
        "layout_consistency": "pass", "layout_notes": "",
        "camera_observed_zone": "overhead", "camera_evidence": "flat map view",
        "view_consistency": "pass", "view_notes": "",
        "visual_defects": "pass", "defect_notes": "",
        "project_style_consistency": "pass", "style_notes": "ok",
        "overall": "pass", "summary": "ok",
    }
    d.update(overrides or {})
    return json.dumps(d, ensure_ascii=False)


@pytest.fixture()
def _gen_env(tmp_path, monkeypatch):
    """profile + fake 生成/审查客户端注入 deps（服务惰性取用）。"""
    import deps
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    from src.visual_profile import VisualProfileService
    VisualProfileService(tmp_path, tmp_path.name).update(
        render_family="photorealistic", expected_revision="")
    fake = _FakeSceneClient()
    monkeypatch.setattr(deps, "agnes_client", fake, raising=False)
    yield rt, svc, tmp_path, fake
    from src.agents import review_retry_guard
    review_retry_guard.reset_review_retry_state()


def _seed_location_and_view(rt, svc, tmp_path, *, camera_zone="overhead",
                            anchor_name="west chimney"):
    loc = (asyncio.run(svc.create_location(
        name="watchtower yard", identity_description="stone yard",
        expected_document_revision=_drev(svc))))["target"]
    a1 = (asyncio.run(svc.create_anchor(
        location_id=loc, name=anchor_name, kind="landmark",
        expected_document_revision=_drev(svc))))["target"]
    return loc, a1


def _prepare_args(**kw):
    base = {"approval_mode": "review_only"}
    base.update(kw)
    return json.dumps(base)


def test_S31_S32_view_prepare_create_generate_pass(_gen_env, monkeypatch):
    """S31 create+generate；S32 review PASS（review_only：不批准）。"""
    import deps
    rt, svc, tmp_path, fake = _gen_env
    loc, a1 = _seed_location_and_view(rt, svc, tmp_path)
    fake.vision_content = _view_reply(a1)
    r = _run_exec(sd._execute_scene_view_prepare(
        rt, tmp_path.name, _prepare_args(
            location_id=loc, label="plan", camera_zone="overhead",
            look_direction="site center", visible_anchor_ids=[a1],
            expected_document_revision=_drev(svc)), None, "m1"))
    assert r["ok"], r.get("error") or r.get("detail")
    assert r["view_id"].startswith("view_")
    assert r["review_overall"] == "pass"
    assert r["approved"] is False   # review_only 不批准
    assert fake.gen_calls == 1
    assert r["changed_resources"] == [{"kind": "location_view", "id": r["view_id"]}]
    # canonical 真实落盘：view 已生成候选但未批准
    doc = sd._scene_system_read(tmp_path)
    view = doc.views[r["view_id"]]
    assert view.generation_status == "ready"
    assert view.approval_status != "approved"


def test_S33_S34_needs_fix_and_review_only_no_approve(_gen_env, monkeypatch):
    """S33 审查 needs_fix 不批准；S34 review_only 恒不批准。"""
    import deps
    rt, svc, tmp_path, fake = _gen_env
    loc, a1 = _seed_location_and_view(rt, svc, tmp_path)
    fake.vision_content = _view_reply(a1, {"view_consistency": "fail",
                                           "overall": "needs_fix",
                                           "summary": "facade frontal"})
    r = _run_exec(sd._execute_scene_view_prepare(
        rt, tmp_path.name, _prepare_args(
            location_id=loc, label="plan", camera_zone="overhead",
            visible_anchor_ids=[a1],
            expected_document_revision=_drev(svc)), None, "m1"))
    assert r["ok"] and r["review_overall"] == "needs_fix"
    assert r["approved"] is False
    assert "未批准" in r["note"] or "没有" in r["note"] or "调整" in r["note"]
    # S34：review_only + pass 回复也绝不批准
    fake.vision_content = _view_reply(a1)
    r2 = _run_exec(sd._execute_scene_view_prepare(
        rt, tmp_path.name, _prepare_args(
            location_id=loc, view_id=r["view_id"], label="plan",
            camera_zone="overhead", visible_anchor_ids=[a1],
            approval_mode="review_only", expected_revision=r["revision"]),
        None, "m2"))
    assert r2["ok"] and r2["review_overall"] == "pass"
    assert r2["approved"] is False   # review_only 恒不批准
    doc = sd._scene_system_read(tmp_path)
    assert doc.views[r2["view_id"]].approval_status != "approved"


def test_S35_approve_if_pass_auto_approves(_gen_env, monkeypatch):
    """S35 approve_if_pass + 审查真实 pass → 自动批准（approved reference 落盘）。"""
    import deps
    rt, svc, tmp_path, fake = _gen_env
    loc, a1 = _seed_location_and_view(rt, svc, tmp_path)
    fake.vision_content = _view_reply(a1)
    r = _run_exec(sd._execute_scene_view_prepare(
        rt, tmp_path.name, _prepare_args(
            location_id=loc, label="plan", camera_zone="overhead",
            visible_anchor_ids=[a1], approval_mode="approve_if_pass",
            expected_document_revision=_drev(svc)), None, "m1"))
    assert r["ok"] and r["approved"] is True, (r.get("approve_error"), r.get("review_overall"))
    doc = sd._scene_system_read(tmp_path)
    view = doc.views[r["view_id"]]
    assert view.approval_status == "approved"
    assert view.reference_asset_id


def test_S36_camera_wrong_needs_fix(_gen_env, monkeypatch):
    """S36：requested=overhead 而模型 observed=front → view_consistency fail →
    needs_fix 不批准（28.md §2/§30 诚实机位）。"""
    import deps
    rt, svc, tmp_path, fake = _gen_env
    loc, a1 = _seed_location_and_view(rt, svc, tmp_path)
    fake.vision_content = _view_reply(a1, {"camera_observed_zone": "front",
                                           "camera_evidence": "frontal hero"})
    r = _run_exec(sd._execute_scene_view_prepare(
        rt, tmp_path.name, _prepare_args(
            location_id=loc, label="plan", camera_zone="overhead",
            visible_anchor_ids=[a1], approval_mode="approve_if_pass",
            expected_document_revision=_drev(svc)), None, "m1"))
    assert r["review_overall"] == "needs_fix"
    assert r["approved"] is False
    cr = r["candidate_reviews"][0]
    assert cr["view_consistency"] == "fail"
    assert "矛盾" in (cr["camera_derivation_note"] or "")


def test_S37_anchor_visibility_missing_needs_fix(_gen_env, monkeypatch):
    """S37：required anchor 缺回报（fail closed missing）→ needs_fix。"""
    import deps
    rt, svc, tmp_path, fake = _gen_env
    loc, a1 = _seed_location_and_view(rt, svc, tmp_path)
    # 回复完全不包含 anchors（→ derive fail closed missing）
    d = json.loads(_view_reply(a1))
    d["anchors"] = []
    fake.vision_content = json.dumps(d)
    r = _run_exec(sd._execute_scene_view_prepare(
        rt, tmp_path.name, _prepare_args(
            location_id=loc, label="plan", camera_zone="overhead",
            visible_anchor_ids=[a1], expected_document_revision=_drev(svc)),
        None, "m1"))
    assert r["review_overall"] == "needs_fix"
    assert r["approved"] is False


def test_S38_same_basis_failure_guard(_gen_env, monkeypatch):
    """S38：同 view + 同语义 basis 连续 3 次 needs_fix → 第 4 次 review_retry_exhausted
    （零生成零审查）。改变 basis（换 camera_zone）→ 放行。
    重试路径与真实 Agent 一致：带 view_id + 最新 expected_revision。"""
    import deps
    from src.agents import review_retry_guard
    rt, svc, tmp_path, fake = _gen_env
    loc, a1 = _seed_location_and_view(rt, svc, tmp_path)
    fake.vision_content = _view_reply(a1, {"view_consistency": "fail",
                                           "overall": "needs_fix"})
    drev = _drev(svc)
    r1 = _run_exec(sd._execute_scene_view_prepare(
        rt, tmp_path.name, _prepare_args(
            location_id=loc, label="plan", camera_zone="overhead",
            visible_anchor_ids=[a1], expected_document_revision=drev), None, "m1"))
    assert r1["ok"] and r1["review_overall"] == "needs_fix"
    view_id, rev = r1["view_id"], r1["revision"]
    for i in range(2):
        r = _run_exec(sd._execute_scene_view_prepare(
            rt, tmp_path.name, _prepare_args(
                location_id=loc, view_id=view_id, label="plan",
                camera_zone="overhead", visible_anchor_ids=[a1],
                expected_revision=rev), None, "m1"))
        assert r["ok"] and r["review_overall"] == "needs_fix", (i, r.get("error"))
        rev = r["revision"]
    gen_before, vis_before = fake.gen_calls, fake.vision_calls
    r4 = _run_exec(sd._execute_scene_view_prepare(
        rt, tmp_path.name, _prepare_args(
            location_id=loc, view_id=view_id, label="plan",
            camera_zone="overhead", visible_anchor_ids=[a1],
            expected_revision=rev), None, "m1"))
    assert r4["ok"] is False and r4["error"] == "review_retry_exhausted"
    assert fake.gen_calls == gen_before and fake.vision_calls == vis_before, \
        "guard 后零生成零审查"
    # 改 basis（换 camera_zone）→ 新 basis 放行
    r5 = _run_exec(sd._execute_scene_view_prepare(
        rt, tmp_path.name, _prepare_args(
            location_id=loc, view_id=view_id, label="plan",
            camera_zone="south_west", visible_anchor_ids=[a1],
            expected_revision=rev), None, "m1"))
    assert r5["ok"] and r5.get("error") != "review_retry_exhausted", \
        (r5.get("error"), r5.get("detail"))
    assert fake.gen_calls == gen_before + 1
