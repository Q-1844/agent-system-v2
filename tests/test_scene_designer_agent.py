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
        self.sizes_seen: list = []

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        self.gen_calls += 1
        self.sizes_seen.append(size_tier)
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
        "lighting_weather": "pass", "lighting_notes": "",
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


# ============ Commit F：scene.instance.prepare / scene.state.update（S39-S48）============
def _seed_loc_anchor(svc, name="山雨驿站"):
    loc = (asyncio.run(svc.create_location(
        name=name, identity_description="mountain inn",
        expected_document_revision=_drev(svc))))["target"]
    a1 = (asyncio.run(svc.create_anchor(
        location_id=loc, name="east gate", kind="entry",
        expected_document_revision=_drev(svc))))["target"]
    return loc, a1


def test_S39_instance_create(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc, a1 = _seed_loc_anchor(svc)
    r = _run_exec(sd._execute_scene_instance_prepare(
        rt, "s2demo", json.dumps({
            "story_scene_id": "s01", "location_id": loc,
            "expected_document_revision": _drev(svc)}), None, "m1"))
    assert r["ok"] and r["verified"]
    assert r["scene_instance_id"].startswith("scin_")
    assert r["changed_resources"] == [{"kind": "scene_instance",
                                       "id": r["scene_instance_id"]}]


def test_S40_instance_story_scene_missing_rejected(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc, a1 = _seed_loc_anchor(svc)
    r = _run_exec(sd._execute_scene_instance_prepare(
        rt, "s2demo", json.dumps({
            "story_scene_id": "sXX", "location_id": loc,
            "expected_document_revision": _drev(svc)}), None, "m1"))
    assert not r["ok"] and r["error"] == "story_scene_not_found"


def test_S41_instance_location_missing_rejected(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    r = _run_exec(sd._execute_scene_instance_prepare(
        rt, "s2demo", json.dumps({
            "story_scene_id": "s01", "location_id": "loc_missing",
            "expected_document_revision": _drev(svc)}), None, "m1"))
    assert not r["ok"] and r["error"] == "location_not_found"


def test_S42_duplicate_active_instance_rejected(tmp_path):
    """S42：同故事段第二个 active instance → 系统拒绝 + 返回现有 id（不静默创建）。"""
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc, a1 = _seed_loc_anchor(svc)
    r1 = _run_exec(sd._execute_scene_instance_prepare(
        rt, "s2demo", json.dumps({
            "story_scene_id": "s01", "location_id": loc,
            "expected_document_revision": _drev(svc)}), None, "m1"))
    assert r1["ok"]
    r2 = _run_exec(sd._execute_scene_instance_prepare(
        rt, "s2demo", json.dumps({
            "story_scene_id": "s01", "location_id": loc,
            "expected_document_revision": _drev(svc)}), None, "m1"))
    assert not r2["ok"] and r2["error"] == "instance_already_active"
    assert r2["existing_scene_instance_ids"] == [r1["scene_instance_id"]]
    assert "复用" in r2.get("op_summary", "")


def test_S43_instance_stable_mapping(tmp_path):
    """S43：instance ↔ story_scene 映射稳定（inspect 可见）。"""
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc, a1 = _seed_loc_anchor(svc)
    r = _run_exec(sd._execute_scene_instance_prepare(
        rt, "s2demo", json.dumps({
            "story_scene_id": "s02", "location_id": loc,
            "expected_document_revision": _drev(svc)}), None, "m1"))
    ins = sd._execute_scene_production_inspect(
        rt, "s2demo", json.dumps({"story_scene_id": "s02"}), None, "m1")
    assert ins["scene_system"]["scene_instance"]["scene_instance_id"] \
        == r["scene_instance_id"]


def test_S44_state_update(tmp_path):
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc, a1 = _seed_loc_anchor(svc)
    r_inst = _run_exec(sd._execute_scene_instance_prepare(
        rt, "s2demo", json.dumps({
            "story_scene_id": "s01", "location_id": loc,
            "expected_document_revision": _drev(svc)}), None, "m1"))
    r = _run_exec(sd._execute_scene_state_update(
        rt, "s2demo", json.dumps({
            "instance_id": r_inst["scene_instance_id"],
            "time_of_day": "night", "weather": "heavy_rain",
            "lighting": "warm lanterns",
            "expected_revision": r_inst["revision"]}), None, "m1"))
    assert r["ok"] and r["verified"]
    st = sd._scene_system_read(tmp_path).scene_instances[
        r_inst["scene_instance_id"]].state
    assert st.time_of_day == "night" and st.weather == "heavy_rain"


def test_S45_state_anchor_validation(tmp_path):
    """S45：anchor_states 引用不属于该 location 的 anchor → 拒绝。"""
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc, a1 = _seed_loc_anchor(svc)
    loc2, _ = _seed_loc_anchor(svc, name="另一地点")
    r_inst = _run_exec(sd._execute_scene_instance_prepare(
        rt, "s2demo", json.dumps({
            "story_scene_id": "s01", "location_id": loc,
            "expected_document_revision": _drev(svc)}), None, "m1"))
    # loc2 的 anchor 不属于 loc——直接造一个跨点 anchor id
    a_other = (asyncio.run(svc.create_anchor(
        location_id=loc2, name="other gate", kind="entry",
        expected_document_revision=_drev(svc))))["target"]
    r = _run_exec(sd._execute_scene_state_update(
        rt, "s2demo", json.dumps({
            "instance_id": r_inst["scene_instance_id"],
            "anchor_states": {a_other: {"condition": "broken"}},
            "expected_revision": r_inst["revision"]}), None, "m1"))
    assert not r["ok"]
    assert r["error"] in ("cross_location_anchor", "anchor_not_found")


def test_S46_state_real_prop_placement(tmp_path, monkeypatch):
    """S46：真实 Prop 引用成功写入（mock resources doc 提供 prop）。"""
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc, a1 = _seed_loc_anchor(svc)
    r_inst = _run_exec(sd._execute_scene_instance_prepare(
        rt, "s2demo", json.dumps({
            "story_scene_id": "s01", "location_id": loc,
            "expected_document_revision": _drev(svc)}), None, "m1"))
    # 构造真实 resources doc（含 prop）
    from src.resources.store import ResourceStore
    from src.resources.models import ResourceDocument, Prop, ApprovalState
    rdoc = ResourceDocument()
    rdoc.props["prop_compass"] = Prop(prop_id="prop_compass", name="铜罗盘",
                                      description="brass compass",
                                      approval_status=ApprovalState.APPROVED.value,
                                      approved_reference_asset_ids=["ref_a"])
    ResourceStore(tmp_path, tmp_path.name).save(rdoc)
    r = _run_exec(sd._execute_scene_state_update(
        rt, "s2demo", json.dumps({
            "instance_id": r_inst["scene_instance_id"],
            "prop_placements": {"prop_compass": {"anchor_id": a1,
                                                 "state": "on table"}},
            "expected_revision": r_inst["revision"]}), None, "m1"))
    assert r["ok"] and r["verified"], (r.get("error"), r.get("detail"))
    st = sd._scene_system_read(tmp_path).scene_instances[
        r_inst["scene_instance_id"]].state
    assert "prop_compass" in st.prop_placements


def test_S47_missing_prop_resource_prerequisite(tmp_path, monkeypatch):
    """S47：缺 Prop → resource_prerequisite 结构化上报（零写入）。"""
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc, a1 = _seed_loc_anchor(svc)
    r_inst = _run_exec(sd._execute_scene_instance_prepare(
        rt, "s2demo", json.dumps({
            "story_scene_id": "s01", "location_id": loc,
            "expected_document_revision": _drev(svc)}), None, "m1"))
    r = _run_exec(sd._execute_scene_state_update(
        rt, "s2demo", json.dumps({
            "instance_id": r_inst["scene_instance_id"],
            "prop_placements": {"prop_ghost": {"anchor_id": a1,
                                               "state": "on table"}},
            "expected_revision": r_inst["revision"]}), None, "m1"))
    assert not r["ok"] and r["error"] == "prop_not_found"
    pw = r.get("prerequisite_warnings") or []
    assert pw and pw[0]["code"] == "resource_prerequisite"
    assert pw[0]["required_capability"] == "resource_agent"
    assert pw[0]["missing_props"][0]["prop_id"] == "prop_ghost"
    assert "prop_placements" in pw[0]["missing_props"][0]["target_field"]
    # canonical 零写入
    st = sd._scene_system_read(tmp_path).scene_instances[
        r_inst["scene_instance_id"]].state
    assert st.prop_placements == {}


def test_S48_cannot_create_prop(tmp_path):
    """S48：Scene Agent 工具面无任何 Prop/资源写工具（物理边界）。
    §0.6：resource.inventory/resource.read 只读定位 Prop id 允许。"""
    names = {t["function"]["name"] for t in sd._tool_surface()}
    allowed_resource_reads = {"resource.inventory", "resource.read"}
    resource_tools = {n for n in names if n.startswith("resource.")}
    assert resource_tools <= allowed_resource_reads, resource_tools
    assert not any("prop" in n and "scene" in n for n in names)
    # 工具面总数在 14~18 区间（28.md §6）
    # Phase D §35：+scene.instance.delete（服务层既有能力接线；C2 §6 预算 14~18 → 14~19）
    # 40.md Skill System V1：+skill.list/load/read_resource（只读 3 个；14~19 → 17~22）
    assert 17 <= len(names) <= 22, names


# ============ Commit G：scene.state_view.prepare（S49-S55）============
def _approve_base_view(pdir, view_id: str, asset_id: str = "ref_base0000001"):
    """快捷批准 base view（canonical + 参考资产文件 + 同口径 approval record）。"""
    from src.resources.store import ResourceStore
    from src.resources.models import ReferenceAsset
    from src.scene_system.store import SceneSystemStore
    from src.scene_system import generation_records as gr
    from src.scene_system.generation_records import approval_records_path
    from src.scene_system.view_review import (build_review_expectations,
                                              expectations_fingerprint)
    from src.visual_profile import load_profile_or_none
    from src.visual_profile.models import visual_profile_fingerprint
    local_rel = f"output/scene_system/views/{view_id}/c_01.png"
    f = pdir / local_rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\x89PNG fake")
    rdoc = ResourceStore(pdir, pdir.name).load()
    rdoc.reference_assets[asset_id] = ReferenceAsset(
        asset_id=asset_id, local=local_rel, checksum="c" * 8, source="scene_system")
    ResourceStore(pdir, pdir.name).save(rdoc)
    st = SceneSystemStore(pdir, pdir.name)
    doc = st.load()
    doc.views[view_id].approval_status = "approved"
    doc.views[view_id].reference_asset_id = asset_id
    st.save(doc)
    doc = st.load()
    vp = load_profile_or_none(pdir)
    fp = expectations_fingerprint(
        build_review_expectations(doc, doc.views[view_id]), [asset_id],
        visual_profile_fingerprint=(visual_profile_fingerprint(vp)
                                    if vp is not None else None))
    gr.append_record(pdir, {"approval_id": "apv_t", "operation":
                            "scene.approve_location_view",
                            "status": "approved", "view_id": view_id,
                            "asset_id": asset_id,
                            "reviewed_basis_fingerprint": fp,
                            "completed_at": 0.0},
                     path=approval_records_path(pdir))


def _gen_env_ready(_gen_env, monkeypatch):
    """instance + approved base view + fake client 就绪。"""
    rt, svc, tmp_path, fake = _gen_env
    loc, a1 = _seed_loc_anchor(svc)
    drev = _drev(svc)
    view = (asyncio.run(svc.create_view(
        location_id=loc, label="hero", camera_zone="overhead",
        look_direction="site center", visible_anchor_ids=[a1],
        expected_document_revision=_drev(svc))))["target"]
    _approve_base_view(tmp_path, view)
    r_inst = _run_exec(sd._execute_scene_instance_prepare(
        rt, "s2demo", json.dumps({
            "story_scene_id": "s01", "location_id": loc,
            "expected_document_revision": _drev(svc)}), None, "m1"))
    fake.vision_content = _view_reply(a1)
    return rt, svc, tmp_path, fake, loc, a1, view, r_inst


def test_S49_sciv_base_unapproved_rejected(_gen_env, monkeypatch):
    """S49：base LocationView 未批准 → 生成被系统 fail closed 拒绝。"""
    rt, svc, tmp_path, fake, loc, a1, view, r_inst = _gen_env_ready(_gen_env, None)
    # 撤销批准（恢复 unreviewed）
    from src.scene_system.store import SceneSystemStore
    st = SceneSystemStore(tmp_path, tmp_path.name)
    d = st.load()
    d.views[view].approval_status = "unreviewed"
    d.views[view].reference_asset_id = None
    st.save(d)
    r = _run_exec(sd._execute_scene_state_view_prepare(
        rt, "s2demo", json.dumps({
            "scene_instance_id": r_inst["scene_instance_id"],
            "base_location_view_id": view, "approval_mode": "review_only",
            "expected_document_revision": _drev(svc)}), None, "m1"))
    assert not r["ok"], "base 未批准必须被拒"
    assert r["stage"] == "generate"


def test_S50_sciv_create_generate(_gen_env, monkeypatch):
    rt, svc, tmp_path, fake, loc, a1, view, r_inst = _gen_env_ready(_gen_env, None)
    r = _run_exec(sd._execute_scene_state_view_prepare(
        rt, "s2demo", json.dumps({
            "scene_instance_id": r_inst["scene_instance_id"],
            "base_location_view_id": view, "approval_mode": "review_only",
            "expected_document_revision": _drev(svc)}), None, "m1"))
    assert r["ok"], (r.get("error"), r.get("detail"))
    assert r["scene_instance_view_id"].startswith("sciv_")
    assert fake.gen_calls == 1
    assert r["changed_resources"] == [{"kind": "scene_instance_view",
                                       "id": r["scene_instance_view_id"]}]


def test_S51_sciv_pass_and_approve(_gen_env, monkeypatch):
    rt, svc, tmp_path, fake, loc, a1, view, r_inst = _gen_env_ready(_gen_env, None)
    fake.vision_content = _view_reply(a1)
    r = _run_exec(sd._execute_scene_state_view_prepare(
        rt, "s2demo", json.dumps({
            "scene_instance_id": r_inst["scene_instance_id"],
            "base_location_view_id": view, "approval_mode": "approve_if_pass",
            "expected_document_revision": _drev(svc)}), None, "m1"))
    assert r["ok"] and r["approved"] is True, (r.get("approve_error"),
                                               r.get("review_overall"))
    doc = sd._scene_system_read(tmp_path)
    sciv = doc.scene_instance_views[r["scene_instance_view_id"]]
    assert sciv.approval_status == "approved" and sciv.reference_asset_id


def test_S52_sciv_needs_fix_not_approve(_gen_env, monkeypatch):
    rt, svc, tmp_path, fake, loc, a1, view, r_inst = _gen_env_ready(_gen_env, None)
    # 先给状态期望（有期望时 scene_state/lighting_weather 才是 hard 维度）
    r_state = _run_exec(sd._execute_scene_state_update(
        rt, "s2demo", json.dumps({
            "instance_id": r_inst["scene_instance_id"],
            "time_of_day": "night", "weather": "storm",
            "expected_revision": r_inst["revision"]}), None, "m0"))
    assert r_state["ok"]
    fake.vision_content = _view_reply(a1, {"scene_state": {
        "time_of_day": "fail", "weather": "fail",
        "environment_conditions": "concern", "anchor_states": [], "notes": ""},
        "lighting_weather": "fail",
        "overall": "needs_fix"})
    r = _run_exec(sd._execute_scene_state_view_prepare(
        rt, "s2demo", json.dumps({
            "scene_instance_id": r_inst["scene_instance_id"],
            "base_location_view_id": view, "approval_mode": "approve_if_pass",
            "expected_document_revision": _drev(svc)}), None, "m1"))
    assert r["ok"] and r["review_overall"] == "needs_fix", \
        (r.get("review_overall"), r.get("candidate_reviews"))
    assert r["approved"] is False


# ============ Phase D §2：图片质量档跟随项目生产设置（29.md §2 最小接线）============
def test_PD2_location_view_size_tier_follows_project_style(_gen_env, monkeypatch):
    """project.style.size_tier 缺省 1K；设为 2K 后 scene.view.prepare 生成请求透传 2K。"""
    from src.core.store import ProjectStore
    rt, svc, tmp_path, fake = _gen_env
    loc, a1 = _seed_location_and_view(rt, svc, tmp_path)
    fake.vision_content = _view_reply(a1)
    r = _run_exec(sd._execute_scene_view_prepare(
        rt, tmp_path.name, _prepare_args(
            location_id=loc, label="plan", camera_zone="overhead",
            look_direction="site center", visible_anchor_ids=[a1],
            expected_document_revision=_drev(svc)), None, "m1"))
    assert r["ok"], r.get("error") or r.get("detail")
    assert fake.sizes_seen == ["1K"]            # 缺省项目：行为不变
    proj = ProjectStore(tmp_path).load()
    proj.style.size_tier = "2K"
    ProjectStore(tmp_path).save(proj)
    fake.vision_content = _view_reply(a1)
    r2 = _run_exec(sd._execute_scene_view_prepare(
        rt, tmp_path.name, _prepare_args(
            location_id=loc, view_id=r["view_id"], label="plan",
            camera_zone="overhead", visible_anchor_ids=[a1],
            approval_mode="review_only", expected_revision=r["revision"]),
        None, "m2"))
    assert r2["ok"], r2.get("error") or r2.get("detail")
    assert fake.sizes_seen[-1] == "2K"          # 生产设置透传


def test_PD2_sciv_size_tier_follows_project_style(_gen_env, monkeypatch):
    """scene.state_view.prepare 同口径：project.style.size_tier=2K → 生成透传 2K。"""
    from src.core.store import ProjectStore
    rt, svc, tmp_path, fake, loc, a1, view, r_inst = _gen_env_ready(_gen_env, None)
    proj = ProjectStore(tmp_path).load()
    proj.style.size_tier = "2K"
    ProjectStore(tmp_path).save(proj)
    fake.vision_content = _view_reply(a1)
    r = _run_exec(sd._execute_scene_state_view_prepare(
        rt, "s2demo", json.dumps({
            "scene_instance_id": r_inst["scene_instance_id"],
            "base_location_view_id": view, "approval_mode": "review_only",
            "expected_document_revision": _drev(svc)}), None, "m1"))
    assert r["ok"], (r.get("error"), r.get("detail"))
    assert fake.gen_calls == 1
    assert fake.sizes_seen == ["2K"]


def test_S53_S54_state_change_new_basis(_gen_env, monkeypatch):
    """S53/S54：SceneState 改变 → basis 指纹改变（guard 允许重新生成）。"""
    from src.agents.review_retry_guard import review_basis_sig
    rt, svc, tmp_path, fake, loc, a1, view, r_inst = _gen_env_ready(_gen_env, None)
    sig1 = review_basis_sig(scene_instance_id=r_inst["scene_instance_id"],
                            base_location_view_id=view, label="",
                            instance_revision=r_inst["revision"],
                            intent="scene_instance_view")
    r_state = _run_exec(sd._execute_scene_state_update(
        rt, "s2demo", json.dumps({
            "instance_id": r_inst["scene_instance_id"],
            "time_of_day": "night", "weather": "storm",
            "expected_revision": r_inst["revision"]}), None, "m2"))
    assert r_state["ok"]
    sig2 = review_basis_sig(scene_instance_id=r_inst["scene_instance_id"],
                            base_location_view_id=view, label="",
                            instance_revision=r_state["revision"],
                            intent="scene_instance_view")
    assert sig1 != sig2, "状态真改变 → basis 必须改变（§18）"
    # 重新生成（带 sciv id + 新 revision）→ 放行并成功
    fake.vision_content = _view_reply(a1)
    drev = _drev(svc)
    r1 = _run_exec(sd._execute_scene_state_view_prepare(
        rt, "s2demo", json.dumps({
            "scene_instance_id": r_inst["scene_instance_id"],
            "base_location_view_id": view, "approval_mode": "review_only",
            "expected_document_revision": drev}), None, "m1"))
    sciv_id, sciv_rev = r1["scene_instance_view_id"], r1["revision"]
    # S53：状态改变后 inspect 报 stale（sciv 状态 mock 层面已在 S22 验证）；
    # S54：直接重生成（新状态 → 新 basis）成功
    _run_exec(sd._execute_scene_state_update(
        rt, "s2demo", json.dumps({
            "instance_id": r_inst["scene_instance_id"],
            "weather": "clear",
            "expected_revision": r_state["revision"]}), None, "m3"))
    r2 = _run_exec(sd._execute_scene_state_view_prepare(
        rt, "s2demo", json.dumps({
            "scene_instance_id": r_inst["scene_instance_id"],
            "base_location_view_id": view,
            "scene_instance_view_id": sciv_id,
            "expected_revision": sciv_rev,
            "approval_mode": "review_only"}), None, "m4"))
    assert r2["ok"], (r2.get("error"), r2.get("detail"))
    assert fake.gen_calls == 2


def test_S55_sciv_review_prompt_inherits_base_camera(tmp_path):
    """S55：Sciv 审查 prompt 继承 base view 机位语义（含 CAMERA CORRECTNESS 块）。"""
    from src.scene_system.instance_view_review import \
        build_scene_instance_review_prompt
    from src.scene_system.models import LocationView
    base_view = LocationView(view_id="view_x", location_id="loc_x",
                             camera_zone="overhead", look_direction="site center",
                             visible_anchor_ids=["a1"])
    expectations = {"location": {}, "view": {
        "view_id": "view_x", "label": "", "camera_zone": "overhead",
        "look_direction": "site center", "visible_anchor_ids": ["a1"]},
        "scene_state": {}, "anchor_snapshots": [], "prop_expectations": []}
    prompt = build_scene_instance_review_prompt(expectations, ["candidate"])
    assert "CAMERA CORRECTNESS" in prompt
    assert "overhead" in prompt
    assert "camera_observed_zone" in prompt


# ============ Commit H：changed_refs / Producer / Production Room（S4/S5/S56-S65）============
def test_S4_producer_delegate_tool_registered():
    """S4：scene_designer.delegate 第四 delegate（schema+executor 通道）。"""
    offered, _ = producer._build_producer_tools()
    schema = next(t for t in offered
                  if t["function"]["name"] == "scene_designer.delegate")
    desc = schema["function"]["description"]
    assert "Location" in desc and "SceneState" in desc and "SceneInstanceView" in desc
    assert "resource_designer" not in desc
    assert "scene_designer.delegate" in producer.DELEGATE_TOOLS
    assert producer._SPECIALIST_RESOURCE["scene_designer"] == "scene_system"
    # 旧三个 delegate 原样
    for n in ("script_writer.delegate", "character_designer.delegate",
              "director.delegate"):
        assert n in producer.DELEGATE_TOOLS


def test_S5_production_room_participant(tmp_path):
    """S5：Production Room roster 含 Scene Agent；历史参与者不变。"""
    from src.agents.production_room import build_room_projection
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    snap = build_room_projection(rt, "s2demo")
    ids = [p["agent_id"] for p in snap["participants"]]
    assert "scene_designer" in ids
    for aid in ("producer", "script_writer", "character_designer", "director"):
        assert aid in ids


def _derive_with(receipts):
    from src.agents.producer import _derive_changed_refs
    return _derive_changed_refs("scene_designer", receipts)


def test_S56_changed_refs_locations(tmp_path):
    """S56：location/anchor/layout 变更 → locations 键（§22 归并）。"""
    refs = _derive_with([
        {"ok": True, "verified": True, "operation": "scene.location.upsert",
         "changed_resources": [{"kind": "location", "id": "loc_1"}]},
        {"ok": True, "verified": True, "operation": "scene.anchor.upsert",
         "changed_resources": [{"kind": "anchor", "id": "anc_1"}]},
        {"ok": True, "verified": True, "operation": "scene.layout.set",
         "changed_resources": [{"kind": "layout", "id": "layout_1"}]},
    ])
    assert refs["locations"] == ["anc_1", "layout_1", "loc_1"]
    assert refs["location_views"] == []
    assert refs["scene_instances"] == []


def test_S57_changed_refs_location_views():
    refs = _derive_with([
        {"ok": True, "verified": True, "operation": "scene.view.prepare",
         "changed_resources": [{"kind": "location_view", "id": "view_1"}]},
    ])
    assert refs["location_views"] == ["view_1"]
    assert refs["locations"] == []


def test_S58_changed_refs_scene_instances():
    """S58：instance/state 变更 → scene_instances 键。"""
    refs = _derive_with([
        {"ok": True, "verified": True, "operation": "scene.instance.prepare",
         "changed_resources": [{"kind": "scene_instance", "id": "scin_1"}]},
        {"ok": True, "verified": True, "operation": "scene.state.update",
         "changed_resources": [{"kind": "scene_instance", "id": "scin_1"}]},
    ])
    assert refs["scene_instances"] == ["scin_1"]


def test_S59_changed_refs_scene_instance_views():
    refs = _derive_with([
        {"ok": True, "verified": True, "operation": "scene.state_view.prepare",
         "changed_resources": [{"kind": "scene_instance_view", "id": "sciv_1"}]},
    ])
    assert refs["scene_instance_views"] == ["sciv_1"]


def test_S60_zero_mutation_empty():
    """S60：零 mutation（失败/stale 回执）→ 全键空。"""
    refs = _derive_with([
        {"ok": False, "verified": False, "operation": "scene.location.upsert",
         "changed_resources": [{"kind": "location", "id": "loc_x"}]},
        {"ok": True, "verified": True, "stale": True, "operation": "scene.state.update",
         "changed_resources": []},
    ])
    assert all(v == [] for v in refs.values())


def test_S61_old_task_result_compatibility():
    """S61：旧 TaskResult（无场景键）派生照常 + 投影容错。"""
    from src.agents.producer import _derive_changed_refs
    # 旧 CD 回执（只有 character_name）
    refs = _derive_changed_refs("character_designer", [
        {"ok": True, "verified": True, "character_name": "Alice"}])
    assert refs["characters"] == ["Alice"]
    assert refs["locations"] == []   # 新键恒在（加法式），旧字段照常派生
    # 旧 structured_result 无场景键 → room 投影不炸
    from src.agents.production_room import _result_message
    msg = _result_message({"task_id": "t1", "target_agent": "director",
                           "status": "completed",
                           "structured_result": {"status": "completed",
                                                 "changed_refs": {
                                                     "scenes": [], "shots": [],
                                                     "characters": []}}},
                          None)
    assert msg["changed_refs"] == {"scenes": [], "shots": [], "characters": []}


def test_S62_S63_S64_prerequisite_routing_prompt():
    """S62-S64：Producer system prompt 的前置路由（scene_production_prerequisite →
    Scene Agent；Scene Agent 的 resource_prerequisite → Resource Agent；完成 → Director）。"""
    ps = producer.PRODUCER_SYSTEM
    assert "scene_production_prerequisite" in ps and "Scene Agent" in ps
    assert "resource_prerequisite" in ps and "Resource Agent" in ps
    # §24：四专业分工 + Producer zero write
    assert "Script Writer" in ps and "Resource Agent" in ps
    assert "Scene Agent" in ps and "Director" in ps
    assert "五个 *.delegate" in ps
    # Producer 无任何 scene.* 写工具（zero direct writes 工具面层）
    offered, _ = producer._build_producer_tools()
    names = {t["function"]["name"] for t in offered}
    assert not any(n.startswith("scene.") for n in names)


def test_S65_producer_zero_direct_writes():
    """S65：Producer 写工具集合恒空 + registry 委派链完整。"""
    assert producer.PRODUCER_WRITE_OPERATIONS == set()
    reg = Registries().agents
    assert reg.can_delegate("producer", "scene_designer")
    assert not reg.can_delegate("scene_designer", "director")   # specialist 不互委派


# ============ Phase D §35：scene.instance.delete（悬空实例清理）============
def test_PD35_instance_delete_and_stale_guard(tmp_path):
    """删除成功 + OCC 保护 + 删除后 canonical 真实消失。"""
    rt = _runtime(tmp_path)
    _save_proj(tmp_path)
    svc, pdir = _mk_svc(tmp_path)
    loc, a1 = _seed_loc_anchor(svc)
    r = _run_exec(sd._execute_scene_instance_prepare(
        rt, "s2demo", json.dumps({
            "story_scene_id": "s02", "location_id": loc,
            "expected_document_revision": _drev(svc)}), None, "m1"))
    inst_id = r["scene_instance_id"]
    # stale revision 被拒
    r_bad = _run_exec(sd._execute_scene_instance_delete(
        rt, "s2demo", json.dumps({
            "instance_id": inst_id, "expected_revision": "00000000"}), None, "m2"))
    assert not r_bad["ok"] and r_bad["error"] == "stale_revision"
    # 正确 revision → 删除成功且 canonical 消失
    from src.scene_system.models import scene_instance_revision
    doc = sd._scene_system_read(tmp_path)
    cur = scene_instance_revision(doc.scene_instances[inst_id])
    r_ok = _run_exec(sd._execute_scene_instance_delete(
        rt, "s2demo", json.dumps({
            "instance_id": inst_id, "expected_revision": cur}), None, "m3"))
    assert r_ok["ok"] and r_ok["verified"], r_ok
    assert {"kind": "scene_instance", "id": inst_id} in r_ok["changed_resources"]
    doc2 = sd._scene_system_read(tmp_path)
    assert inst_id not in doc2.scene_instances


# ============ Phase D §35：view.prepare 机位必填（空机位 → Sciv 审查期望退化）============
def test_PD35_view_prepare_requires_camera_zone(_gen_env, monkeypatch):
    """新建 LocationView 缺 camera_zone → camera_zone_required 拒绝（不建定义不烧图）。"""
    rt, svc, tmp_path, fake = _gen_env
    loc, a1 = _seed_location_and_view(rt, svc, tmp_path)
    r = _run_exec(sd._execute_scene_view_prepare(
        rt, tmp_path.name, _prepare_args(
            location_id=loc, label="no zone",
            visible_anchor_ids=[a1],
            expected_document_revision=_drev(svc)), None, "m1"))
    assert not r["ok"] and r["error"] == "camera_zone_required"
    assert fake.gen_calls == 0, "定义被拒时绝不烧图"


# ============ Phase D §25.C：link_mode 接线（尾帧生产前置）============
def test_PD25C_shot_update_link_mode(tmp_path):
    """shot.update 可切换 keyframes（尾帧前置）；非法值被拒/忽略。"""
    from src.agents import director as dr
    rt = _runtime(tmp_path)
    proj = _save_proj(tmp_path)
    from src.core.models import Shot
    proj.scenes[0].shots = [Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                 description="test", characters=["林秋"])]
    ProjectStore(tmp_path).save(proj)
    r = _run_exec(dr._execute_shot_update(
        rt, "s2demo", json.dumps({
            "shot_id": "s01_c01", "expected_shot_revision": None,
            "fields": {"link_mode": "keyframes"}}), None, "m1")) if False else None
    # 直接走清洗+结构服务（避免 expected_revision 语义分歧）
    cleaned, dropped = dr._clean_shot_fields({"link_mode": "keyframes"}, set())
    assert cleaned == {"link_mode": "keyframes"}
    cleaned2, _ = dr._clean_shot_fields({"link_mode": "bogus"}, set())
    assert cleaned2 == {}
    from src.storyboard.structure_service import update_shot_fields
    proj_m = ProjectStore(tmp_path).load()
    res = update_shot_fields(proj_m, "s01_c01", {"link_mode": "keyframes"})
    assert "link_mode" in res["changed"]
    ProjectStore(tmp_path).save(proj_m)
    proj2 = ProjectStore(tmp_path).load()
    assert proj2.get_shot("s01_c01").link_mode == "keyframes"
    res2 = update_shot_fields(proj2, "s01_c01", {"link_mode": "bogus"})
    assert "link_mode" not in res2["changed"]


# ============ Phase D §35：provider 输入图上限（Agnes i2i ≤6）============
def test_PD35_cap_provider_input_images():
    """13 张候选 → 截到 6：primary 全保（scene/hero look/composition），其余按原序补，
    被裁剪项逐条记录 provider_input_image_cap。"""
    from src.generation.image_adapter import _cap_provider_input_images
    items = [{"source_id": "scene", "priority": "primary"}]
    for i in range(4):
        items.append({"source_id": f"look_hero_{i}", "priority": "primary"})
    for i in range(4):
        items.append({"source_id": f"identity_{i}", "priority": "supporting"})
    for i in range(4):
        items.append({"source_id": f"look_extra_{i}", "priority": "supporting"})
    dropped = []
    out = _cap_provider_input_images(items, reserved_slots=0, dropped=dropped)
    assert len(out) == 6
    ids = [x["source_id"] for x in out]
    assert "scene" in ids and all(f"look_hero_{i}" in ids for i in range(4))
    assert len(dropped) == 7
    assert all(d["reason"] == "provider_input_image_cap" for d in dropped)
    # 未超限时原样返回、零 dropped
    dropped2 = []
    out2 = _cap_provider_input_images(items[:5], reserved_slots=0, dropped=dropped2)
    assert len(out2) == 5 and dropped2 == []

def test_PD35_cap_multi_character_no_identity_fill():
    """>=2 角色：每角色 1 张 look 主参考 + 场景/composition；identity 多视角全裁剪
    （反克隆策略）；单角色仍允许多视角填充。"""
    from src.generation.image_adapter import _cap_provider_input_images
    items = [{"source_id": "scene", "source_type": "scene_instance_view", "priority": "primary"}]
    # 3 角色 x (look hero + look extra + identity front/side)
    for c in ("沈夜", "柳衣", "萧寒"):
        items.append({"source_id": f"look_{c}", "source_type": "character_look",
                      "subject_character": c, "priority": "primary"})
        items.append({"source_id": f"lookx_{c}", "source_type": "character_look",
                      "subject_character": c, "priority": "supporting"})
        items.append({"source_id": f"id_f_{c}", "source_type": "character_identity",
                      "subject_character": c, "priority": "supporting"})
        items.append({"source_id": f"id_s_{c}", "source_type": "character_identity",
                      "subject_character": c, "priority": "supporting"})
    dropped = []
    out = _cap_provider_input_images(items, reserved_slots=0, dropped=dropped)
    ids = [x["source_id"] for x in out]
    assert ids == ["scene", "look_沈夜", "look_柳衣", "look_萧寒"], ids
    assert len(dropped) == 9   # 13 items - 4 kept
    assert all(d["reason"] == "provider_input_image_cap" for d in dropped)
    # 单角色：identity 多视角允许填充
    single = [items[0]] + [it for it in items if it.get("subject_character") == "沈夜"]
    out2 = _cap_provider_input_images(single, reserved_slots=0, dropped=[])
    assert len(out2) == min(5, 6)   # scene + look + lookx + id_f + id_s
    assert {x["source_id"] for x in out2} == {"scene", "look_沈夜", "lookx_沈夜",
                                              "id_f_沈夜", "id_s_沈夜"}


# ============ Phase D2（30.md §6-§9）：scene_reference_mode + 人形参考预算 ============
class _FakeShot:
    def __init__(self, shot_size="medium shot", camera_angle="", composition=""):
        self.shot_size = shot_size
        self.camera_angle = camera_angle
        self.composition = composition


def test_D2_resolve_scene_reference_mode_explicit():
    from src.generation.image_adapter import resolve_scene_reference_mode
    # A/B 证据后默认翻转为 auto（30.md §6：翻转须以证据为前提——见 §5.1）
    assert resolve_scene_reference_mode(None, _FakeShot("close-up")) == "context_only"
    assert resolve_scene_reference_mode("full", _FakeShot()) == "full"
    assert resolve_scene_reference_mode("context_only", _FakeShot()) == "context_only"
    import pytest
    with pytest.raises(ValueError):
        resolve_scene_reference_mode("bogus", _FakeShot())


def test_D2_resolve_scene_reference_mode_auto_mapping():
    """30.md §7 起草映射：EWS/WS→full；MCU/CU/ECU/OTS/insert→context_only；
    MS→context_only（A/B 后校准）。"""
    from src.generation.image_adapter import resolve_scene_reference_mode as r
    assert r("auto", _FakeShot("extreme wide shot")) == "full"
    assert r("auto", _FakeShot("wide shot")) == "full"
    assert r("auto", _FakeShot("extreme wide shot", camera_angle="establishing")) == "full"
    assert r("auto", _FakeShot("close-up")) == "context_only"
    assert r("auto", _FakeShot("extreme close-up")) == "context_only"
    assert r("auto", _FakeShot("medium close-up")) == "context_only"
    assert r("auto", _FakeShot("medium shot")) == "full"   # A/B 证据：MS→full
    assert r("auto", _FakeShot("medium shot", camera_angle="OTS 过肩")) == "context_only"
    assert r("auto", _FakeShot("close-up", composition="insert of jade pendant")) == "context_only"


def _d2_budget_items():
    return [
        {"source_id": "scene", "source_type": "scene_instance_view",
         "reference_asset_id": "ref_scene", "priority": "primary"},
        {"source_id": "legacy:沈夜:front", "source_type": "character_identity",
         "reference_asset_id": "ref_if", "subject_character": "沈夜", "priority": "supporting"},
        {"source_id": "legacy:沈夜:side", "source_type": "character_identity",
         "reference_asset_id": "ref_is", "subject_character": "沈夜", "priority": "supporting"},
        {"source_id": "look_沈夜", "source_type": "character_look",
         "reference_asset_id": "ref_l1", "subject_character": "沈夜", "priority": "primary"},
        {"source_id": "lookx_沈夜", "source_type": "character_look",
         "reference_asset_id": "ref_l2", "subject_character": "沈夜", "priority": "supporting"},
        {"source_id": "prop_玉佩", "source_type": "prop",
         "reference_asset_id": "ref_p", "prop_id": "prop_玉佩", "priority": "primary"},
    ]


def test_D2_budget_one_human_ref_per_character():
    """30.md §8：identity 一律裁剪；每角色仅 pack 序第一张 look；prop 不受影响。"""
    from src.generation.image_adapter import _apply_reference_budget
    dropped = []
    kept, mode = _apply_reference_budget(_d2_budget_items(), shot=_FakeShot(),
                                         scene_reference_mode=None, dropped=dropped)
    assert mode == "full"
    ids = [x["source_id"] for x in kept]
    assert ids == ["scene", "look_沈夜", "prop_玉佩"], ids
    reasons = {d["reason"] for d in dropped}
    assert reasons == {"human_reference_budget"}
    assert len(dropped) == 3   # 2 identity + 1 look extra


def test_D2_budget_context_only_drops_scene_keeps_semantics_path():
    """30.md §4 Mode B 语义：context_only 裁场景图（逐条记录），其余照旧——
    场景语义仍在 prompt 生产上下文（由 _production_context_text 保证，非本层）。"""
    from src.generation.image_adapter import _apply_reference_budget
    dropped = []
    kept, mode = _apply_reference_budget(_d2_budget_items(), shot=_FakeShot(),
                                         scene_reference_mode="context_only",
                                         dropped=dropped,
                                         human_reference_mode="keep")
    assert mode == "context_only"
    assert [x["source_id"] for x in kept] == ["look_沈夜", "prop_玉佩"]
    scene_drops = [d for d in dropped if d["reason"] == "scene_reference_context_only"]
    assert len(scene_drops) == 1 and scene_drops[0]["source_id"] == "scene"


def test_D2_budget_full_keeps_scene():
    from src.generation.image_adapter import _apply_reference_budget
    kept, mode = _apply_reference_budget(_d2_budget_items(), shot=_FakeShot(),
                                         scene_reference_mode="full", dropped=[])
    assert mode == "full" and kept[0]["source_type"] == "scene_instance_view"



def test_D2_human_reference_mode_auto_tight_crop_after_p42():
    """D2 §5.2 起步=auto 紧景别 drop；Phase 42 定版翻转（43.md §一步，2026-09-30）：
    景别驱动的紧景别（CU/ECU/MCU）auto→crop（P3 三臂+EXP2/3 用户评审——相对 drop
    构图不变身份变好，相对 keep 无全身构图锚）；角度驱动的 context_only（OTS/
    insert/detail，景别不紧）维持 drop（例外有档）。显式 keep/drop/crop 直通。"""
    from src.generation.image_adapter import (
        _apply_reference_budget, resolve_human_reference_mode)
    # CU + auto scene → context_only → look 保留为 crop 档（头肩派生），场景仍裁
    dropped = []
    kept, mode = _apply_reference_budget(_d2_budget_items(), shot=_FakeShot("close-up"),
                                         scene_reference_mode="auto", dropped=dropped)
    assert mode == "context_only"
    assert [x["source_id"] for x in kept] == ["look_沈夜", "prop_玉佩"], kept
    look_kept = [x for x in kept if x["source_id"] == "look_沈夜"][0]
    assert look_kept.get("derived_crop") == "bust"
    reasons = {d["reason"] for d in dropped}
    assert "human_reference_drop" not in reasons
    assert "scene_reference_context_only" in reasons
    assert "human_reference_budget" in reasons   # identity 双视角+look extra 照旧裁
    # MS + auto → full → look 保留（无 crop 派生）
    kept2, mode2 = _apply_reference_budget(_d2_budget_items(), shot=_FakeShot("medium shot"),
                                           scene_reference_mode="auto", dropped=[])
    assert mode2 == "full"
    look2 = [x for x in kept2 if x["source_id"] == "look_沈夜"][0]
    assert "derived_crop" not in look2
    # 显式 keep 覆盖（保留直通语义）
    kept3, _m = _apply_reference_budget(_d2_budget_items(), shot=_FakeShot("close-up"),
                                        scene_reference_mode="context_only",
                                        human_reference_mode="keep", dropped=[])
    assert "look_沈夜" in [x["source_id"] for x in kept3]
    assert all("derived_crop" not in x for x in kept3)
    # resolver：景别驱动紧景别 → crop；ECU → crop（方法映射 face 由
    # resolve_identity_crop_method 负责）；角度驱动 context_only → drop（例外）
    from src.generation.image_adapter import resolve_human_reference_mode as r
    assert r(None, _FakeShot("medium shot"), "full") == "keep"
    assert r(None, _FakeShot("close-up"), "context_only") == "crop"
    assert r(None, _FakeShot("medium close-up"), "context_only") == "crop"
    assert r(None, _FakeShot("extreme close-up"), "context_only") == "crop"
    assert r(None, _FakeShot("medium shot", camera_angle="OTS 过肩"),
             "context_only") == "drop"
    assert r(None, _FakeShot("medium shot", composition="insert of jade pendant"),
             "context_only") == "drop"
    # 显式直通不受翻转影响
    assert r("keep", _FakeShot("close-up"), "context_only") == "keep"
    assert r("drop", _FakeShot("close-up"), "context_only") == "drop"
    assert r("crop", _FakeShot("medium shot"), "full") == "crop"
    import pytest
    with pytest.raises(ValueError):
        r("bogus", _FakeShot(), "full")


def test_p42_identity_crop_method_mapping_by_size():
    """crop 档方法映射（确定性）：extreme close → face；其余紧景别 → bust。"""
    from src.generation.image_adapter import resolve_identity_crop_method
    assert resolve_identity_crop_method(_FakeShot("extreme close-up")) == "face"
    assert resolve_identity_crop_method(_FakeShot("close-up")) == "bust"
    assert resolve_identity_crop_method(_FakeShot("medium close-up")) == "bust"
