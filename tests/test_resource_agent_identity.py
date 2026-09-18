"""Agent System V2 Phase B — Commit G：Resource Agent 身份 / Producer / Room 兼容测试
（26.md §0.1/§11/§16/§17；R3/R56）。

核心兼容语义：内部 agent_id 恒为 character_designer（不迁移）；用户可见身份显示为
Resource Agent；delegate 工具名不变但描述升级。
"""
import json

from src.agents.registry import Registries
from src.agents import producer
from src.agents.character_designer import (CHARACTER_DESIGNER_SYSTEM,
                                           character_designer_turn)
from src.core.store import ProjectStore
from src.core.models import Project, Character, StyleAnchor
from src.agents.runtime import AgentRuntime

from tests.test_producer_bridge import (_save_proj, _runtime)


def test_G1_registry_name_display_resource_agent():
    """§17：内部 id 不变；注册表显示名/角色升级为 Resource Agent。"""
    reg = Registries().agents
    a = reg.get("character_designer")
    assert a is not None
    assert a.id == "character_designer", "内部稳定 id 不迁移（§0.1）"
    assert a.name == "Resource Agent"
    assert "生产资源" in a.role
    assert "character_designer.delegate" in producer.DELEGATE_TOOLS


def test_G2_delegate_tool_name_unchanged_description_upgraded():
    """§16.2：工具名保持 character_designer.delegate；描述明确 Resource Agent 兼容名。"""
    offered, _ = producer._build_producer_tools()
    schema = next(t for t in offered
                  if t["function"]["name"] == "character_designer.delegate")
    assert schema["function"]["name"] == "character_designer.delegate"
    desc = schema["function"]["description"]
    assert "Resource Agent" in desc
    assert "character_designer" in desc, "描述说明兼容内部 id"
    assert "Clothing" in desc or "服装" in desc
    assert "Prop" in desc or "道具" in desc
    # 其余两个 delegate 工具不受影响
    names = {t["function"]["name"] for t in offered}
    assert {"script_writer.delegate", "director.delegate"} <= names


def test_G3_producer_system_mentions_resource_agent():
    """§16.1：PRODUCER_SYSTEM 三分功叙述升级（无第四 Specialist）。"""
    ps = producer.PRODUCER_SYSTEM
    assert "Resource Agent" in ps
    assert "character_designer.delegate" in ps, "兼容工具名出现"
    assert "Clothing" in ps or "服装" in ps
    # 三个 delegate 工具，不新增第四个
    assert ps.count(".delegate") >= 3
    assert "resource_agent" not in ps


def test_G4_system_prompt_resource_agent_identity():
    """§11：人设重写——身份/概念/审批分档/profile 宪法/prop 语义/组合语义。"""
    s = CHARACTER_DESIGNER_SYSTEM
    assert "Resource Agent" in s and "Production Resource Designer" in s
    assert "character_designer" in s, "兼容 id 说明"
    # §11.1 Character vs Look
    assert "这个人是谁" in s and "生产状态下长什么样" in s
    assert "绝不为换装去改 Character identity" in s
    # §11.2 三视图是身份 seed，不是生产 Look 替代品
    assert "身份 seed" in s
    # §11.3 Prop 独立资源、组合关系不写进 Prop
    assert "不是 Prop 的永久归属" in s
    # §11.4 Composition 是生产参考不是故事事实
    assert "不是故事事实" in s
    # §11.5 profile 宪法
    assert "视觉宪法" in s and "不修改它" in s
    # §10 审批分档
    assert "approve_if_pass" in s and "review_only" in s
    assert "revoke" in s, "无 revoke 工具的纪律说明"
    # §13 cast 检查纪律
    assert "shot.characters" in s and "偷偷补 cast" in s
    # 占位符仍可格式化（turn 构建不炸）
    proj = Project(name="demo", style=StyleAnchor(), characters=[Character(name="A")])
    out = CHARACTER_DESIGNER_SYSTEM.format(
        project_name=proj.name, story_len=0, scenes_count=0, characters_count=1,
        visual_profile_summary="未设置（unspecified）")
    assert "Resource Agent" in out


def test_G5_old_session_id_still_works_R3(tmp_path):
    """R3：旧 session（agent_id=character_designer）可恢复——turn 入口身份校验不破。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = rt.get_or_create_session("demo", "character_designer",
                                   "project", "demo")["session_id"]
    sess = rt._session_store("demo").load(sid)
    assert sess.agent_id == "character_designer"
    # turn 入口接受该会话（权限校验通过——非 chat 未配置错误）
    res = _run_turn(rt, tmp_path, sid)
    assert res["agent_id"] == "character_designer"
    assert res["session_id"] == sid


def _run_turn(rt, tmp_path, sid):
    import asyncio
    from tests.test_character_designer_agent import ScriptedClient
    rt._client = ScriptedClient([("好的，先聊到这里。", None)])
    return asyncio.run(character_designer_turn(
        rt, "demo", sid, "你觉得女主目前的人设怎么样？不要修改"))


def test_G6_room_identity_frontend_map_R56():
    """R56：Production Room 前端身份映射显示 Resource Agent（静态断言 TS 映射行）。"""
    from pathlib import Path
    ts = (Path(__file__).resolve().parent.parent / "src" / "components" / "agent"
          / "roomIdentity.ts").read_text(encoding="utf-8")
    assert "name: 'Resource Agent'" in ts
    assert "角色 · 造型 · 道具 · 生产资源" in ts
    assert "character_designer" in ts, "映射键仍是内部 id"
