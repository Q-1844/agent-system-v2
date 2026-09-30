"""AI-6B Multi-Agent Foundation 测试（§31）。

engine 级测试：直接驱动 AgentRuntime / registries / stores + 注入 FakeClient/假 getter，
不依赖 TestClient/真 Agnes（§31：不调 Agnes 做 AI-6B 测试）。
每个 case 用 tmp_path 独立项目，互不污染。匹配 tests/test_workspaces.py 风格。
"""
import asyncio
import hashlib
import json

import pytest
from fastapi import HTTPException

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.models import (AgentDefinition, AgentMessage, AgentTask, ModelProfile,
                                ResultStatus, TaskStatus)
from src.agents.registry import (Registries, KNOWN_MODEL_IDS, SPEC_AGENT_IDS,
                                  build_default_capabilities, _build_agents)
from src.agents.capabilities import CapabilityContext, dispatch, is_wired
from src.agents.sessions import AgentSessionStore
from src.agents.tasks import AgentTaskStore
from src.agents.runtime import AgentRuntime, AgentError


# ============ 辅助 ============
def _proj():
    """1 scene 1 shot + Character LinXia（shot.characters 须引用真实项目角色）。"""
    return Project(
        name="demo", style=StyleAnchor(),
        characters=[Character(name="LinXia", description_en="a young woman")],
        scenes=[Scene(scene_id="s01", summary="rain", characters=["LinXia"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="look up at rain",
                                  characters=["LinXia"], target_duration_s=5)])])


def _save_proj(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    return tmp_path


def _runtime(tmp_path, **kw):
    """AgentRuntime 绑定 tmp_path 作项目目录 + 隔离 settings（不污染全局 agents_data）。"""
    kw.setdefault("settings_path", tmp_path / "agents_data" / "settings.json")
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path, **kw)


class FakeClient:
    """注入用假 LLM client（不联网）。"""
    def __init__(self, reply: str = "ok"):
        self._reply = reply

    async def chat(self, messages, **kw):
        return self._reply


def _raise503():
    raise HTTPException(503, "not ready")


# ============ 1. Agent 全注册（28.md §4：identity 稳定 + 允许加法式扩展；
#               Phase C2 scene_designer 加入后为 9 个；Phase 44B assembly
#               加入后为 10 个）============
def test_8_agents_registered():
    r = Registries()
    agents = r.agents.list()
    assert len(agents) == len(SPEC_AGENT_IDS) == 10
    assert [a.id for a in agents] == SPEC_AGENT_IDS
    # 加法式扩展基线：旧 8 identity 一个不少（旧值不迁移不改名）
    assert {"producer", "script_writer", "character_designer", "director",
            "production_planner", "shot_producer", "continuity_reviewer",
            "editor"} <= {a.id for a in agents}


# ============ 2. registry 完整性 ============
def test_registry_integrity():
    r = Registries()
    agent_ids = {a.id for a in r.agents.list()}
    cap_ids = {c.id for c in r.capabilities.list()}
    # ID 唯一 + 精确匹配 spec（Phase C2：9 个 → 44B：10 个，允许加法式扩展）
    assert len(agent_ids) == 10
    assert agent_ids == set(SPEC_AGENT_IDS)
    # 每个 allowed_capabilities id 存在
    for a in r.agents.list():
        for cid in a.allowed_capabilities:
            assert cid in cap_ids, f"{a.id}.allowed_capabilities 引用不存在的能力 {cid}"
    # 每个 owner_agent 存在
    for c in r.capabilities.list():
        assert c.owner_agent in agent_ids, f"{c.id}.owner_agent={c.owner_agent} 不存在"
        # 每个 allowed_agents entry 存在
        for aid in c.allowed_agents:
            assert aid in agent_ids, f"{c.id}.allowed_agents 含不存在的 agent {aid}"


# ============ 3. can_execute allow ============
def test_can_execute_allow():
    r = Registries()
    assert r.capabilities.can_execute("director", "storyboard.inspect", "inspect") is True
    assert r.capabilities.can_execute("producer", "project.inspect", "inspect") is True
    assert r.capabilities.can_execute("shot_producer", "production_plan.inspect", "inspect") is True
    assert r.capabilities.can_execute("continuity_reviewer", "shot.inspect", "inspect") is True
    assert r.capabilities.can_execute("editor", "composition.inspect", "inspect") is True


# ============ 4. can_execute deny ============
def test_can_execute_deny():
    r = Registries()
    # editor 不能 character.design
    assert r.capabilities.can_execute("editor", "character.design", "execute") is False
    # script_writer 不能 shot.video
    assert r.capabilities.can_execute("script_writer", "shot.video", "execute") is False
    # director 不能 story.edit（不在 director.allowed_capabilities）
    assert r.capabilities.can_execute("director", "story.edit", "execute") is False
    # producer 不能直接 storyboard.inspect（必须 delegate）
    assert r.capabilities.can_execute("producer", "storyboard.inspect", "inspect") is False
    # 未知 agent / cap / operation
    assert r.capabilities.can_execute("ghost", "storyboard.inspect", "inspect") is False
    assert r.capabilities.can_execute("director", "nope", "inspect") is False
    assert r.capabilities.can_execute("director", "storyboard.inspect", "execute") is False


# ============ 5. specialist 不能 delegate ============
def test_specialist_cannot_delegate(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    # director -> production_planner 禁止
    with pytest.raises(AgentError) as ei:
        asyncio.run(rt.delegate("demo", "director", "production_planner", "scene", "s01",
                    "x", "production_plan.inspect", "inspect"))
    assert ei.value.code == "permission_denied"
    # 无 task 落盘
    assert rt.list_tasks("demo") == []


# ============ 6. AgentSession 隔离 ============
def test_session_isolation(tmp_path):
    _save_proj(tmp_path)
    store = AgentSessionStore(tmp_path, "demo")
    s_prod = store.get_or_create("producer", "project", "demo")
    s_dir = store.get_or_create("director", "scene", "s01")
    assert s_prod.session_id != s_dir.session_id
    # 消息不串
    store.append_message(s_prod.session_id, AgentMessage(role="user", content="hi producer"))
    store.append_message(s_dir.session_id, AgentMessage(role="user", content="hi director"))
    r_prod = store.load(s_prod.session_id)
    r_dir = store.load(s_dir.session_id)
    assert len(r_prod.messages) == 1 and r_prod.messages[0].content == "hi producer"
    assert len(r_dir.messages) == 1 and r_dir.messages[0].content == "hi director"
    assert all(m.content != "hi producer" for m in r_dir.messages)


# ============ 7. get_or_create 复用 ============
def test_session_get_or_create_reuse(tmp_path):
    _save_proj(tmp_path)
    store = AgentSessionStore(tmp_path, "demo")
    s1 = store.get_or_create("director", "scene", "s01")
    s2 = store.get_or_create("director", "scene", "s01")
    assert s1.session_id == s2.session_id


# ============ 8. AgentTask persistence ============
def test_task_persistence(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    task = asyncio.run(rt.delegate("demo", "producer", "director", "scene", "s01",
                       "inspect", "storyboard.inspect", "inspect"))
    # 新 store 同目录恢复
    store2 = AgentTaskStore(tmp_path, "demo")
    got = store2.get(task["task_id"])
    assert got is not None
    assert got.target_agent == "director"
    assert got.status == TaskStatus.COMPLETED.value


# ============ 9. Producer -> Director delegation storyboard.inspect（DEMO）============
def test_delegation_storyboard_inspect(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    task = asyncio.run(rt.delegate("demo", "producer", "director", "scene", "s01",
                       "检查 s01 分镜结构", "storyboard.inspect", "inspect"))
    assert task["status"] == TaskStatus.COMPLETED.value
    assert task["result_summary"]
    assert {"type": "shot", "id": "s01_c01"} in task["artifact_refs"]
    assert task["structured_result"]["data"]["scene_id"] == "s01"
    assert "issues" in task["structured_result"]["data"]


# ============ 10. delegation 权限拒绝（不建 task）============
def test_delegation_permission_denied(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    with pytest.raises(AgentError) as ei:
        asyncio.run(rt.delegate("demo", "producer", "director", "scene", "s01",
                    "x", "character.design", "execute"))
    assert ei.value.code == "permission_denied"
    assert rt.list_tasks("demo") == []


# ============ 11. delegation 未知 capability ============
def test_delegation_unknown_capability(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    with pytest.raises(AgentError) as ei:
        asyncio.run(rt.delegate("demo", "producer", "director", "scene", "s01",
                    "x", "storyboard.unknown", "inspect"))
    assert ei.value.code == "unknown_capability"


# ============ 12. delegation disabled agent ============
def test_delegation_disabled_agent(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    rt.update_agent_setting("director", enabled=False)
    with pytest.raises(AgentError) as ei:
        asyncio.run(rt.delegate("demo", "producer", "director", "scene", "s01",
                    "x", "storyboard.inspect", "inspect"))
    assert ei.value.code == "disabled_agent"


# ============ 13. ModelProfile persistence ============
def test_model_profile_persistence(tmp_path):
    settings_path = tmp_path / "agents_data" / "settings.json"
    rt1 = _runtime(tmp_path, settings_path=settings_path)
    rt1.update_agent_setting("director", profile_id="text_default")
    # 新 runtime（同 settings 文件）能读到
    rt2 = _runtime(tmp_path, settings_path=settings_path)
    s = rt2.settings.get_agent_setting("director")
    assert s.get("model_profile_id") == "text_default"


# ============ 14. unknown model profile ============
def test_unknown_model_profile(tmp_path):
    rt = _runtime(tmp_path)
    with pytest.raises(AgentError) as ei:
        rt.update_agent_setting("director", profile_id="bogus_profile")
    assert ei.value.code == "unknown_model_profile"


# ============ 15. unknown model config（validate 拒绝）============
def test_unknown_model_config():
    r = Registries()
    bogus = ModelProfile(id="bogus", name="bogus", reasoning_model="not-a-real-model")
    with pytest.raises(ValueError):
        r.profiles.validate(bogus)


# ============ 16. send_message not_configured（软失败）============
def test_send_message_not_configured(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=None, agnes_getter=_raise503)
    msg = asyncio.run(rt.send_message("demo", "director", "scene", "s01", "你好"))
    assert msg["status"] == ResultStatus.NOT_CONFIGURED.value
    assert msg["role"] == "assistant"
    # user 消息仍持久化
    s = rt.get_session("demo", _session_id_for(rt, "demo", "director", "scene", "s01"))
    assert any(m["role"] == "user" and m["content"] == "你好" for m in s["messages"])


def _session_id_for(rt, project, agent_id, scope_type, scope_id):
    return rt.get_or_create_session(project, agent_id, scope_type, scope_id)["session_id"]


# ============ 17. send_message with FakeClient ============
def test_send_message_with_fake_client(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient(reply="hello from model"))
    msg = asyncio.run(rt.send_message("demo", "director", "scene", "s01", "检查一下"))
    assert msg["role"] == "assistant"
    assert msg["content"] == "hello from model"
    assert msg.get("status") is None
    sid = _session_id_for(rt, "demo", "director", "scene", "s01")
    s = rt.get_session("demo", sid)
    roles = [m["role"] for m in s["messages"]]
    assert "user" in roles and "assistant" in roles


# ============ 18. restart recovery ============
def test_restart_recovery(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    asyncio.run(rt.delegate("demo", "producer", "director", "scene", "s01",
                "x", "storyboard.inspect", "inspect"))
    # 新 runtime（同目录）恢复 session + task
    rt2 = _runtime(tmp_path)
    tasks = rt2.list_tasks("demo")
    sessions = rt2.list_sessions("demo")
    assert len(tasks) == 1 and tasks[0]["status"] == TaskStatus.COMPLETED.value
    assert len(sessions) == 1 and sessions[0]["agent_id"] == "director"


# ============ 19. reserved capability -> not_implemented ============
def test_reserved_capability_not_implemented(tmp_path):
    _save_proj(tmp_path)
    proj = _proj()
    ctx = CapabilityContext(project="demo", scope_type="scene", scope_id="s01",
                            project_loader=lambda: proj, plans_reader=None)
    res = dispatch("storyboard.propose_change", ctx)
    assert res.status == ResultStatus.NOT_IMPLEMENTED.value
    # delegation 走 reserved capability -> task failed
    rt = _runtime(tmp_path)
    task = asyncio.run(rt.delegate("demo", "producer", "director", "scene", "s01",
                       "x", "storyboard.propose_change", "execute"))
    assert task["status"] == TaskStatus.FAILED.value


# ============ 20. canonical project.json 不变 ============
def test_canonical_unchanged(tmp_path):
    _save_proj(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    rt = _runtime(tmp_path)
    asyncio.run(rt.delegate("demo", "producer", "director", "scene", "s01",
                "x", "storyboard.inspect", "inspect"))
    after = (tmp_path / "output" / "project.json").read_bytes()
    assert before == after, "canonical project.json 被 agent 修改（违反 §18/§19 红线）"


# ============ API 级冒烟（TestClient，确认路由挂载）============
def test_api_list_agents(client):
    r = client.get("/api/agents")
    assert r.status_code == 200
    data = r.json()
    ids = [a["id"] for a in data["agents"]]
    assert set(ids) == set(SPEC_AGENT_IDS)


def test_api_agent_profiles_and_capabilities(client):
    r = client.get("/api/agents/profiles")
    assert r.status_code == 200
    assert len(r.json()["profiles"]) >= 4
    r2 = client.get("/api/agents/director/capabilities")
    assert r2.status_code == 200
    caps = r2.json()["capabilities"]
    assert any(c["id"] == "storyboard.inspect" for c in caps)


# ============ AI-6E §24: production_plan.create capability & authorization ============
def test_production_plan_create_registered_and_implemented():
    """§24-1~3: production_plan.create 存在、implemented=True、owner=production_planner。"""
    r = Registries()
    cap = r.capabilities.get("production_plan.create")
    assert cap is not None
    assert cap.implemented is True
    assert cap.owner_agent == "production_planner"


def test_production_plan_create_authorization():
    """§24-4~6: Planner 可执行；Producer/其他 Specialist 不可直接执行。"""
    r = Registries()
    assert r.capabilities.can_execute("production_planner", "production_plan.create", "execute") is True
    # Producer 不能直接执行（须 delegate）
    assert r.capabilities.can_execute("producer", "production_plan.create", "execute") is False
    # 无关 Specialist 不可
    assert r.capabilities.can_execute("director", "production_plan.create", "execute") is False
    assert r.capabilities.can_execute("script_writer", "production_plan.create", "execute") is False
    assert r.capabilities.can_execute("shot_producer", "production_plan.create", "execute") is False


def test_shot_inspect_allows_planner_owner_unchanged():
    """§24-7~8: production_planner 现可 shot.inspect；owner 仍 shot_producer。"""
    r = Registries()
    assert r.capabilities.can_execute("production_planner", "shot.inspect", "inspect") is True
    cap = r.capabilities.get("shot.inspect")
    assert cap.owner_agent == "shot_producer"
    assert "production_planner" in cap.allowed_agents


def test_delegate_production_plan_create(tmp_path):
    """§24-9: Producer 可 delegate production_plan.create 给 Planner（async 路径，FakeClient 返回 plan JSON）。"""
    _save_proj(tmp_path)
    plan_json = ('{"overview":"独立开场","shots":['
                 '{"shot_id":"s01_c01","opening_source":"independent","link_mode":"i2v","reason":"首镜独立"}]}')
    rt = _runtime(tmp_path, client=FakeClient(reply=plan_json))
    task = asyncio.run(rt.delegate("demo", "producer", "production_planner", "scene", "s01",
                "为场景规划制作", "production_plan.create", "execute"))
    assert task["status"] == TaskStatus.COMPLETED.value
    assert task["target_agent"] == "production_planner"
    assert task["capability_id"] == "production_plan.create"
    # artifact 引用真实 plan（§14/§15）
    assert any(rf["type"] == "production_plan" for rf in task["artifact_refs"])
    # data 含 plan 状态 + derived 字段（§10 确定性）
    data = task["structured_result"]["data"]
    assert data["plan_status"] == "draft"
    assert data["shots"][0]["opening_source"] == "independent"
    assert data["shots"][0]["generate_first_frame"] is True   # independent -> 生成首帧


def test_specialist_cannot_delegate_production_plan_create(tmp_path):
    """§24-10: Specialist → Planner delegation 仍被拒。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient())
    with pytest.raises(AgentError) as ei:
        asyncio.run(rt.delegate("demo", "director", "production_planner", "scene", "s01",
                    "x", "production_plan.create", "execute"))
    assert ei.value.code == "permission_denied"
    assert rt.list_tasks("demo") == []


def test_production_plan_create_rejects_unknown_scene(tmp_path):
    """§24-12: unknown Scene 被干净拒绝（task FAILED，不抛）。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient())
    task = asyncio.run(rt.delegate("demo", "producer", "production_planner", "scene", "sXX",
                "x", "production_plan.create", "execute"))
    assert task["status"] == TaskStatus.FAILED.value
    assert "未找到场景" in task["result_summary"]


def test_production_plan_create_rejects_non_scene_scope(tmp_path):
    """§24-11: 非 scene scope 被干净拒绝。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient())
    task = asyncio.run(rt.delegate("demo", "producer", "production_planner", "project", "demo",
                "x", "production_plan.create", "execute"))
    assert task["status"] == TaskStatus.FAILED.value
    assert "scene" in task["result_summary"]


def test_production_plan_create_rejects_no_shots(tmp_path):
    """§6/§12: 场景无镜头 -> 结构化 blocker（概念上游 Director），task FAILED。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient())
    p = ProjectStore(tmp_path).load()
    p.scenes[0].shots = []
    ProjectStore(tmp_path).save(p)
    task = asyncio.run(rt.delegate("demo", "producer", "production_planner", "scene", "s01",
                "x", "production_plan.create", "execute"))
    assert task["status"] == TaskStatus.FAILED.value
    assert "无镜头" in task["result_summary"]
    assert task["structured_result"]["data"]["blocker"] == "needs_upstream_change"
    assert task["structured_result"]["data"]["upstream_owner"] == "director"


def test_production_plan_create_disabled_planner_rejected(tmp_path):
    """§24-13: 禁用 Planner 仍被拒。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient())
    rt.update_agent_setting("production_planner", enabled=False)
    with pytest.raises(AgentError) as ei:
        asyncio.run(rt.delegate("demo", "producer", "production_planner", "scene", "s01",
                    "x", "production_plan.create", "execute"))
    assert ei.value.code == "disabled_agent"
