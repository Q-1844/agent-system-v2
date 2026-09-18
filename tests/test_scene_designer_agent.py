# tests/test_scene_designer_agent.py
# Agent System V2 Phase C2 — Scene Agent（28.md）。本文件随 Commit B-J 逐步扩充：
#   Commit B：S1 注册 / S2 会话持久化 / S3 direct turn / S6 旧 identity 不变 + system prompt
#   Commit C-J：inventory/inspect、Location/Anchor/Layout、view prepare、instance/state、
#               Sciv prepare、changed_refs、Producer delegate、Production Room
import asyncio
import json

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
    """写工具单写串行：同一步发多个 scene.* 写工具 → 第 2 个被拒（闸门在 Commit D
    加入真实写工具前用未知 scene.* 工具验证账本分类路径）。"""
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
    assert result["receipts"][0]["ok"] is False   # 工具尚未实现（Commit D 落地）


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
