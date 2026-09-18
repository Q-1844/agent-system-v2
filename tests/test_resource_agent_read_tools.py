"""Agent System V2 Phase B — Commit D：Resource 只读工具 + 资源锁域测试（26.md §7/§12/§15）。

覆盖：
- D1 (R9) inventory 空项目；
- D2 (R10) inventory 混合资源 + filter；
- D3 (R11/R12) resource.read look / prop（详情 + document_revision）；
- D4 (R13) 非法 kind / 缺 id / 不存在 id fail closed；
- D5 shot.list/shot.read schema 从 Director 过滤复用 + 执行器可达（§12）；
- D6 (R57) Character mutation → 只挂 character 域锁（bridge 总线事件序列验证；
  resources 域不被误锁——§15 双域分锁语义）。
"""
import asyncio
import json

from src.core.store import ProjectStore
from src.agents import producer
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.project_events import (get_project_bus, reset_project_bus,
                                       RESOURCE_CHARACTER, RESOURCE_RESOURCES)
from src.agents.character_designer import (_execute_resource_inventory,
                                           _execute_resource_read,
                                           _tool_surface, TOOL_EXECUTORS,
                                           WRITE_TOOL_DOMAIN)
from src.resources.service import ResourceService
from src.resources.models import document_revision
from tests.test_producer_bridge import (ScriptedClient, _save_proj, _runtime,
                                        _run, _tool_call, _char_rev)


def _seed_resources(tmp_path):
    """混合资源：1 clothing + 1 look + 1 prop（走真实 WriteService）。"""
    from src.resources.write_service import ResourceWriteService
    ws = ResourceWriteService(tmp_path, tmp_path.name)
    _run(ws.create_clothing(name="Red Coat", description="wool red coat",
                            expected_document_revision=document_revision(ws.store.load())))
    lk = _run(ws.create_look(character_name="林小雨", label="rain look",
                             expected_document_revision=document_revision(ws.store.load())))
    pr = _run(ws.create_prop(name="Lantern", description="an old brass lantern",
                             expected_document_revision=document_revision(ws.store.load())))
    return ws, lk["target"], pr["target"]


# ============ D1/D2：inventory ============
def test_D1_inventory_empty_R9(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    out = _execute_resource_inventory(rt, "demo", "{}", None, None)
    assert out["ok"] and out["count"] == 0 and out["resources"] == []
    assert out["document_revision"], "空文档也有 revision（OCC 期望值来源）"


def test_D2_inventory_mixed_and_filter_R10(tmp_path):
    _save_proj(tmp_path)
    _, look_id, prop_id = _seed_resources(tmp_path)
    rt = _runtime(tmp_path)
    out = _execute_resource_inventory(rt, "demo", '{"filter": "all"}', None, None)
    kinds = {r["type"] for r in out["resources"]}
    assert kinds == {"clothing", "look", "prop"}
    ids = {r["id"] for r in out["resources"]}
    assert look_id in ids and prop_id in ids
    # look 摘要带 character_name / 审批 / 生成状态 / revision（高信号）
    look_row = next(r for r in out["resources"] if r["id"] == look_id)
    assert look_row["character_name"] == "林小雨"
    assert look_row["status"] == "unreviewed"
    assert look_row["revision"]
    # filter=prop 只出 prop
    out2 = _execute_resource_inventory(rt, "demo", '{"filter": "prop"}', None, None)
    assert {r["type"] for r in out2["resources"]} == {"prop"}
    # 非法 filter fail closed
    with pytest.raises(AgentError):
        _execute_resource_inventory(rt, "demo", '{"filter": "shot"}', None, None)


# ============ D3/D4：resource.read ============
def test_D3_read_look_and_prop_R11_R12(tmp_path):
    _save_proj(tmp_path)
    ws, look_id, prop_id = _seed_resources(tmp_path)
    rt = _runtime(tmp_path)
    look = _execute_resource_read(rt, "demo",
                                  json.dumps({"kind": "look", "resource_id": look_id}),
                                  None, None)
    assert look["ok"] and look["resource"]["look_id"] == look_id
    assert look["resource"]["character_name"] == "林小雨"
    assert look["document_revision"] == document_revision(ws.store.load())
    prop = _execute_resource_read(rt, "demo",
                                  json.dumps({"kind": "prop", "resource_id": prop_id}),
                                  None, None)
    assert prop["ok"] and prop["resource"]["name"] == "Lantern"


def test_D4_read_fail_closed_R13(tmp_path):
    _save_proj(tmp_path)
    _seed_resources(tmp_path)
    rt = _runtime(tmp_path)
    with pytest.raises(AgentError):
        _execute_resource_read(rt, "demo", '{"kind": "shot", "resource_id": "x"}', None, None)
    with pytest.raises(AgentError):
        _execute_resource_read(rt, "demo", '{"kind": "look", "resource_id": ""}', None, None)
    with pytest.raises(AgentError):
        _execute_resource_read(rt, "demo", '{"kind": "look", "resource_id": "look_none"}', None, None)


# ============ D5：shot.list/shot.read（§12）============
def test_D5_shot_read_tools_reuse_director():
    surface = _tool_surface()
    names = [t["function"]["name"] for t in surface]
    assert "shot.list" in names and "shot.read" in names
    # schema 从 Director 过滤复用（同一对象内容，不是第二套 schema）
    from src.agents.director import DIRECTOR_TOOLS
    for n in ("shot.list", "shot.read"):
        d = next(t for t in DIRECTOR_TOOLS if t["function"]["name"] == n)
        mine = next(t for t in surface if t["function"]["name"] == n)
        assert mine == d, f"{n} schema 必须与 Director 完全一致"
    # 执行器可达
    assert TOOL_EXECUTORS["shot.list"].__name__ == "_execute_shot_list_via_director"
    assert TOOL_EXECUTORS["shot.read"].__name__ == "_execute_shot_read_via_director"


# ============ D6 (R57)：character mutation → 只挂 character 域锁 ============
def test_D6_character_mutation_locks_character_domain_only(tmp_path):
    reset_project_bus()
    _save_proj(tmp_path)
    new_desc = "a young woman in a red raincoat"
    env = {"objective": "调整林小雨的视觉设定", "situation": "雨后",
           "constraints": [], "scope": {}, "user_intent": "调整设定"}
    client = ScriptedClient([
        (None, [_tool_call("character_designer.delegate", env, "call_del")]),
        (None, [_tool_call("character.update",
                           {"character_name": "林小雨",
                            "expected_revision": _char_rev(tmp_path, "林小雨"),
                            "description_en": new_desc}, "cd2")]),
        ("已更新。", None),
        ("已让 Resource Agent 调整设定。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    bus = get_project_bus()
    q = bus.subscribe("demo")
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    try:
        res = _run(producer.producer_turn(rt, "demo", sid, "调整林小雨的视觉设定"))
        assert res["receipts"][0]["changed_refs"]["characters"] == ["林小雨"]
    finally:
        bus.unsubscribe("demo", q)
    evs = []
    while not q.empty():
        evs.append(q.get_nowait())
    locked = {e["resource"] for e in evs if e["type"] == "resource.locked"}
    unlocked = {e["resource"] for e in evs if e["type"] == "resource.unlocked"}
    assert locked == {RESOURCE_CHARACTER}, f"只应挂 character 锁，得 {locked}"
    assert unlocked == {RESOURCE_CHARACTER}, "finally 必须释放"
    assert RESOURCE_RESOURCES not in locked, "改角色不得误锁资源域"
    assert bus.is_locked("demo") is False, "turn 结束后无残留锁"


def test_D6b_write_tool_domain_mapping_shape():
    """§15 映射表：character.* → character 域；resource.* mutation → resources 域。"""
    from src.agents.project_events import RESOURCE_CHARACTER
    assert set(WRITE_TOOL_DOMAIN.values()) == {RESOURCE_CHARACTER, RESOURCE_RESOURCES}
    assert set(WRITE_TOOL_DOMAIN) == {"character.create", "character.update",
                                      "character.delete", "character.generate_views",
                                      "resource.clothing.upsert", "resource.look.prepare",
                                      "resource.look.approve",
                                      "resource.prop.register",
                                      "resource.composition.prepare",
                                      "resource.library.publish",
                                      "resource.library.import"}
    for k in ("resource.clothing.upsert", "resource.look.prepare",
              "resource.look.approve",
              "resource.prop.register", "resource.composition.prepare",
              "resource.library.publish", "resource.library.import"):
        assert WRITE_TOOL_DOMAIN[k] == RESOURCE_RESOURCES


import pytest  # noqa: E402  （文件尾 import 供上方 raises 使用）
