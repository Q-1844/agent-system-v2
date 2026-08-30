"""Director Agent（正式版，原生 function-calling tool-loop）smoke 测试。

不联网（ScriptedClient）。对齐任务书 §十八 关键路径：
  - 普通讨论 → 零写（conversation）。
  - 单故事段生成分镜 → story.list/story.read/character.read/shot.list 局部读取 + shot.create_many verified。
  - 单镜头修改 → shot.read + shot.update verified。
  - 用户整体不满意 → shot.update_many 整组重设。
  - 聊天后执行（zero-write recovery）：用户明确要求写入但模型先只说话 → Runtime 注入一次恢复提示。
  - OCC：stale 不覆盖（结构指纹 / 镜头内容指纹 / 集合内容指纹三条独立验证）。
  - 权限：Director 无 story/character 写工具；非 director 会话拒绝。
  - shot.move 单步移动 + shot.delete 级联。
"""
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.director import (
    director_turn, DIRECTOR_TOOLS, TOOL_EXECUTORS, DIRECTOR_WRITE_OPERATIONS,
    _shots_token, _shot_token, _shots_content_token,
)


def _proj():
    """2 段项目：s01 已有 2 镜（c02 接力 c01），s02 空（整组分镜目标段）。"""
    s01_c01 = Shot(shot_id="s01_c01", scene_id="s01", index=1,
                   description="林小雨推开咖啡馆的门", characters=["林小雨"],
                   shot_size="medium shot")
    s01_c02 = Shot(shot_id="s01_c02", scene_id="s01", index=2,
                   description="她看到靠窗的位置空着", characters=["林小雨"],
                   source_shot_id="s01_c01", opening_source="inherit_previous")
    return Project(
        name="demo", style=StyleAnchor(), story_text="完整剧本正文。",
        characters=[Character(name="林小雨", description_en="a young woman, red coat"),
                    Character(name="陈默", description_en="a tall man with glasses")],
        scenes=[Scene(scene_id="s01", summary="林小雨走进咖啡馆，心情低落。",
                      characters=["林小雨"], shots=[s01_c01, s01_c02]),
                Scene(scene_id="s02", summary="窗外下起了雨，两人沉默地对坐。",
                      characters=["林小雨", "陈默"], shots=[])],
        next_scene_seq=3)


def _save_proj(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    return tmp_path


def _runtime(tmp_path, client=None):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client)


def _run(coro):
    return asyncio.run(coro)


def _tool_call(name, arguments, call_id="call_1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


def _shots_rev(tmp_path):
    return _shots_token(ProjectStore(tmp_path).load().scenes)


def _content_rev(tmp_path):
    return _shots_content_token(ProjectStore(tmp_path).load().scenes)


def _shot_rev(tmp_path, shot_id):
    proj = ProjectStore(tmp_path).load()
    return _shot_token(proj.get_shot(shot_id))


class ScriptedClient:
    """按调用顺序返回脚本化的 chat_completion 响应。每条是 (content, tool_calls)。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat_completion(self, messages, **kw):
        self.calls.append({"messages": messages, "kw": kw})
        if not self.script:
            return {"content": "（脚本已耗尽）", "tool_calls": None}
        content, tool_calls = self.script.pop(0)
        return {"content": content, "tool_calls": tool_calls}


# ============ 权限与工具边界 ============
def test_no_story_or_character_write_tools():
    names = {t["function"]["name"] for t in DIRECTOR_TOOLS}
    assert "shot.create" in names and "shot.create_many" in names
    assert "shot.update" in names and "shot.update_many" in names
    assert "shot.delete" in names and "shot.move" in names
    assert "shot.list" in names and "shot.read" in names
    assert "story.list" in names and "story.read" in names
    assert "script.read" in names and "web.search" in names
    assert "character.list" in names and "character.read" in names
    # 🔴 绝不允许出现任何 story/script/character 写工具
    for n in names:
        if n.startswith("story.") or n.startswith("character."):
            assert n in ("story.list", "story.read", "character.list", "character.read")
    assert "script.update" not in names
    assert TOOL_EXECUTORS.keys() & {
        "script.update", "story.create", "story.update", "story.move",
        "story.delete", "character.create", "character.update",
        "character.delete"} == set()
    assert DIRECTOR_WRITE_OPERATIONS <= set(TOOL_EXECUTORS.keys())


def test_wrong_agent_session_denied(tmp_path):
    import pytest
    from src.agents.runtime import AgentError
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=ScriptedClient([]))
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    with pytest.raises(AgentError) as ei:
        _run(director_turn(rt, "demo", sid, "给第2段设计分镜"))
    assert ei.value.code == "permission_denied"


def test_unconfigured_client_not_configured(tmp_path):
    """client 不可用 → not_configured 软失败。注意：全量套件环境（conftest 加载
    api_server → .env）会配置真实 Agnes client，此时无法构造 not_configured 场景
    （与既有 script_writer/character_designer 同名测试同样的套件环境限制，跳过）。"""
    import pytest
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=None)
    if rt._resolve_client_or_none(agent_id="director") is not None:
        pytest.skip("套件环境已配置真实文本 client，无法模拟未配置")
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "给第2段设计分镜"))
    assert res["created"] is False
    assert res["message"]["status"] == "not_configured"


# ============ smoke 1：普通讨论 → 零写 ============
def test_chat_turn_zero_write(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        ("从剧情看，这段两人沉默对坐，慢节奏长镜头更合适；如果要表现内心焦灼，快剪低机位也可以。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "你觉得第2段适合慢节奏还是快剪？"))
    assert res["created"] is False and res["updated"] is False
    assert res["intent"] == "conversation"
    assert res["receipts"] == []
    assert len(ProjectStore(tmp_path).load().scenes[1].shots) == 0


# ============ smoke 2：单故事段生成分镜（局部读取 + create_many）============
def test_create_storyboard_for_scene(tmp_path):
    _save_proj(tmp_path)
    shots = [
        {"description": "雨点砸在窗玻璃上，两人隔桌对坐", "characters": ["林小雨", "陈默"],
         "shot_size": "medium shot", "camera_angle": "eye level", "target_duration_s": 5},
        {"description": "林小雨低头搅动咖啡", "characters": ["林小雨"],
         "shot_size": "close-up", "dialogue": "你最近还好吗", "target_duration_s": 4},
        {"description": "陈默望向窗外的雨", "characters": ["陈默"],
         "shot_size": "close-up", "target_duration_s": 3},
    ]
    client = ScriptedClient([
        (None, [_tool_call("story.list", {})]),
        (None, [_tool_call("story.read", {"scene_id": "s02"}),
                _tool_call("character.read", {"character_name": "林小雨"})]),
        (None, [_tool_call("shot.list", {"scene_id": "s02"})]),
        (None, [_tool_call("shot.create_many",
                           {"scene_id": "s02", "shots": shots,
                            "expected_shots_revision": _shots_rev(tmp_path)})]),
        (None, [_tool_call("shot.list", {"scene_id": "s02"})]),
        ("已为第2段设计 3 个分镜：中景开场、两个近景收束情绪。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "给第2段设计分镜"))

    assert res["created"] is True
    assert res["intent"] == "shot_create"
    ok = [r for r in res["receipts"] if r["operation"] == "shot.create_many"]
    assert len(ok) == 1 and ok[0]["ok"] and ok[0]["verified"]
    ids = [c["shot_id"] for c in ok[0]["created"]]
    assert ids == ["s02_c01", "s02_c02", "s02_c03"]

    proj = ProjectStore(tmp_path).load()
    s02 = proj.scenes[1]
    assert [sh.shot_id for sh in s02.shots] == ids
    assert [sh.index for sh in s02.shots] == [1, 2, 3]
    assert s02.shots[1].shot_size == "close-up"
    assert s02.shots[1].dialogue == "你最近还好吗"
    assert s02.shots[2].characters == ["陈默"]
    # 剧本正文与故事段内容未被触碰（写入域边界）
    assert proj.story_text == "完整剧本正文。"
    assert proj.scenes[0].summary == "林小雨走进咖啡馆，心情低落。"


# ============ smoke 3：单镜头修改 ============
def test_update_single_shot(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.read", {"shot_id": "s01_c01"})]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c01",
                            "expected_revision": _shot_rev(tmp_path, "s01_c01"),
                            "shot_size": "close-up",
                            "camera_angle": "low angle"})]),
        ("第 1 镜已改成低机位近景。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "把第1镜改成低机位的面部近景"))

    assert res["updated"] is True and res["intent"] == "shot_update"
    upd = [r for r in res["receipts"] if r["operation"] == "shot.update"][0]
    assert upd["ok"] and upd["verified"]
    assert set(upd["changed"]) == {"shot_size", "camera_angle"}
    shot = ProjectStore(tmp_path).load().get_shot("s01_c01")
    assert shot.shot_size == "close-up" and shot.camera_angle == "low angle"
    # 未要求的字段未动
    assert shot.description == "林小雨推开咖啡馆的门"


def test_update_single_shot_stale_rejected(tmp_path):
    _save_proj(tmp_path)
    stale_rev = "deadbeef"
    client = ScriptedClient([
        (None, [_tool_call("shot.read", {"shot_id": "s01_c01"})]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c01", "expected_revision": stale_rev,
                            "shot_size": "close-up"})]),
        ("该镜头在我读取后被改动过，写入被拒绝；我基于最新内容重新说明。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "把第1镜改成近景"))

    upd = [r for r in res["receipts"] if r["operation"] == "shot.update"][0]
    assert upd["ok"] is False and upd["stale"] is True and upd["verified"] is False
    assert upd["current_revision"] == _shot_rev(tmp_path, "s01_c01")
    # 未落盘
    assert ProjectStore(tmp_path).load().get_shot("s01_c01").shot_size == "medium shot"


# ============ smoke 4：整体不满意 → update_many 重设 ============
def test_update_many_redesign(tmp_path):
    _save_proj(tmp_path)
    updates = [
        {"shot_id": "s01_c01", "shot_size": "wide shot",
         "description": "咖啡馆全景，雨夜暖光，林小雨缩在角落"},
        {"shot_id": "s01_c02", "camera_angle": "high angle",
         "important_details": "压抑的顶部构图，强化孤独感"},
    ]
    client = ScriptedClient([
        (None, [_tool_call("shot.list", {"scene_id": "s01"})]),
        (None, [_tool_call("shot.update_many",
                           {"updates": updates,
                            "expected_revision": _content_rev(tmp_path)})]),
        ("这组镜头整体重新设计过了：大远景开场 + 高机位压抑感。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "这组镜头太平了，重新设计得更有张力"))

    assert res["updated"] is True
    um = [r for r in res["receipts"] if r["operation"] == "shot.update_many"][0]
    assert um["ok"] and um["verified"] and um["count"] == 2
    proj = ProjectStore(tmp_path).load()
    assert proj.get_shot("s01_c01").shot_size == "wide shot"
    assert proj.get_shot("s01_c02").camera_angle == "high angle"
    # 结构未变（update_many 不改 shot_id 列表）
    assert [sh.shot_id for sh in proj.scenes[0].shots] == ["s01_c01", "s01_c02"]


def test_update_many_stale_rejected(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.update_many",
                           {"updates": [{"shot_id": "s01_c01", "shot_size": "wide shot"},
                                        {"shot_id": "s01_c02", "shot_size": "close-up"}],
                            "expected_revision": "ffffffff"})]),
        ("镜头内容在我读取后有变化，整批写入被拒绝。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "整组重新设计"))
    um = [r for r in res["receipts"] if r["operation"] == "shot.update_many"][0]
    assert um["ok"] is False and um["stale"] is True
    assert ProjectStore(tmp_path).load().get_shot("s01_c01").shot_size == "medium shot"


# ============ shot.create（单镜补充）+ stale ============
def test_create_single_shot(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.create",
                           {"scene_id": "s01", "after_shot_id": "s01_c02",
                            "description": "门上的风铃轻轻晃动",
                            "shot_size": "close-up",
                            "expected_shots_revision": _shots_rev(tmp_path)})]),
        ("已在段尾补充一个风铃特写。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "在段尾补一个风铃特写"))
    c = [r for r in res["receipts"] if r["operation"] == "shot.create"][0]
    assert c["ok"] and c["verified"] and c["created"][0]["shot_id"] == "s01_c03"
    proj = ProjectStore(tmp_path).load()
    # 新镜头追加段尾，id 取段内下一个单调序号（删除不回收、不填补空号）
    assert [sh.shot_id for sh in proj.scenes[0].shots] == ["s01_c01", "s01_c02", "s01_c03"]
    assert proj.get_shot("s01_c03").shot_size == "close-up"


def test_create_shot_stale_rejected(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.create",
                           {"scene_id": "s01", "description": "x",
                            "expected_shots_revision": "ffffffff"})]),
        ("镜头结构在我读取后有变化，创建被拒绝。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "补一个镜头"))
    c = [r for r in res["receipts"] if r["operation"] == "shot.create"][0]
    assert c["ok"] is False and c["stale"] is True
    assert len(ProjectStore(tmp_path).load().scenes[0].shots) == 2


def test_create_many_invalid_batch_no_partial_write(tmp_path):
    """批量里有一项缺 description → 整批不写（无半成功）。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.create_many",
                           {"scene_id": "s02",
                            "shots": [{"description": "雨夜街道"},
                                      {"description": "  "}],
                            "expected_shots_revision": _shots_rev(tmp_path)})]),
        ("第 2 个镜头缺少画面描述，整批没有写入。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    _run(director_turn(rt, "demo", sid, "给第2段设计分镜"))
    assert len(ProjectStore(tmp_path).load().scenes[1].shots) == 0


def test_create_between_relay_pair_blocked(tmp_path):
    """接力约束：c02 接力 c01，不能在它俩之间插入（dependency_block 由中性层拒绝）。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.create",
                           {"scene_id": "s01", "after_shot_id": "s01_c01",
                            "description": "插进接力对之间",
                            "expected_shots_revision": _shots_rev(tmp_path)})]),
        ("这两镜有接力关系，不能在中间插入。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "在第1镜后面补一个镜头"))
    c = [r for r in res["receipts"] if r["operation"] == "shot.create"][0]
    assert c["ok"] is False and "接力" in (c.get("error") or "")
    assert len(ProjectStore(tmp_path).load().scenes[0].shots) == 2


def test_bullet_style_false_claim_corrected(tmp_path):
    """真机 2026-08-30 实测漏网回归：模型零工具调用，用子弹式冒号体例编造
    「删除：…/重新创建：…」整组重做 —— 分镜域补强闸门必须拦下并纠偏。"""
    _save_proj(tmp_path)
    bogus = ("**实际执行结果（基于 shot.list 真实返回）：**\n\n"
             "- **删除**：原第2段（s02）全部5个镜头\n"
             "- **重新创建**：5个新镜头（s02_c01, s02_c02, s02_c03, s02_c04, s02_c05）")
    client = ScriptedClient([
        (bogus, None),                                     # 假成功（零写尝试）
        (bogus, None),                                     # 纠偏后仍谎称 → 兜底覆写
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "这组镜头太平了，重新设计得更有张力"))
    # canonical 未被改动（零写入），最终回复是确定性兜底而非模型编造
    assert len(ProjectStore(tmp_path).load().scenes[1].shots) == 0
    assert res["receipts"] == []
    assert "没有真正执行修改" in res["assistant_message"] or "没有发出对应的工具调用" in res["assistant_message"]


# ============ shot.move：单步移动 + 顺序 ============
def test_move_shot_noop_returns_unchanged(tmp_path):
    """目标位置即当前位置 → 幂等返回 unchanged，不写盘、不误报。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.move",
                           {"shot_id": "s01_c02", "after_shot_id": None,
                            "expected_shots_revision": _shots_rev(tmp_path)})]),
        ("第 2 镜已经在段尾了。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "把第2镜移到段尾"))
    mv = [r for r in res["receipts"] if r["operation"] == "shot.move"][0]
    assert mv["ok"] and mv["verified"] and mv["unchanged"] is True


def test_move_shot_changes_order(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.move",
                           {"shot_id": "s01_c02", "before_shot_id": "s01_c01",
                            "expected_shots_revision": _shots_rev(tmp_path)})]),
        ("已把第 2 镜移到第 1 镜之前。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "把第2镜移到最前面"))
    mv = [r for r in res["receipts"] if r["operation"] == "shot.move"][0]
    assert mv["ok"] and mv["verified"] and mv["order"] == 1
    proj = ProjectStore(tmp_path).load()
    # c02 移到最前：数组顺序变化，index 重排连续（id 不变）
    assert [sh.shot_id for sh in proj.scenes[0].shots] == ["s01_c02", "s01_c01"]
    assert [sh.index for sh in proj.scenes[0].shots] == [1, 2]
    assert proj.get_shot("s01_c02").index == 1


# ============ shot.delete：级联 ============
def test_delete_shot_cascades(tmp_path):
    """s01_c02 接力 s01_c01：删 c01 应级联删掉 c02。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.delete", {"shot_id": "s01_c01"})]),
        ("已删除第 1 镜及依赖它的下游镜头。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "删掉第1镜"))
    d = [r for r in res["receipts"] if r["operation"] == "shot.delete"][0]
    assert d["ok"] and d["verified"]
    assert set(d["deleted"]["shots"]) == {"s01_c01", "s01_c02"}
    proj = ProjectStore(tmp_path).load()
    assert len(proj.scenes[0].shots) == 0


# ============ 聊天后执行（zero-write recovery）============
def test_zero_write_recovery(tmp_path):
    """用户明确要求写入，模型第一轮只说话没调工具 → Runtime 注入恢复提示 →
    第二轮真正调用写工具。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        ("好的，我可以把第 1 镜改成近景，这样更有张力。", None),   # 零写尝试
        (None, [_tool_call("shot.read", {"shot_id": "s01_c01"})]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c01",
                            "expected_revision": _shot_rev(tmp_path, "s01_c01"),
                            "shot_size": "close-up"})]),
        ("已把第 1 镜改成近景。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "把第1镜改成角色面部近景"))

    # 恢复提示确实注入过（第一条 assistant 后跟系统 nudge）
    first_user_nudges = [m for m in client.calls[1]["messages"]
                         if m["role"] == "user" and "还没有发起任何写工具" in m.get("content", "")]
    assert first_user_nudges
    upd = [r for r in res["receipts"] if r["operation"] == "shot.update"][0]
    assert upd["ok"] and upd["verified"]
    assert ProjectStore(tmp_path).load().get_shot("s01_c01").shot_size == "close-up"


def test_chat_after_discussion_then_execute(tmp_path):
    """smoke 5：讨论轮零写；用户确认后写工具真实落盘（跨轮）。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        ("低机位会让压迫感更强，适合这场戏。", None),   # 轮1：讨论，零写
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res1 = _run(director_turn(rt, "demo", sid, "你觉得第1镜用低机位怎么样？"))
    assert res1["intent"] == "conversation" and res1["receipts"] == []

    client2 = ScriptedClient([
        (None, [_tool_call("shot.read", {"shot_id": "s01_c01"})]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c01",
                            "expected_revision": _shot_rev(tmp_path, "s01_c01"),
                            "camera_angle": "low angle"})]),
        ("已按讨论把第 1 镜改成低机位。", None),
    ])
    rt._client = client2
    res2 = _run(director_turn(rt, "demo", sid, "可以，就这么改"))
    assert res2["updated"] is True and res2["intent"] == "shot_update"
    assert ProjectStore(tmp_path).load().get_shot("s01_c01").camera_angle == "low angle"


# ============ OCC 指纹独立性 ============
def test_tokens_scope_independence(tmp_path):
    _save_proj(tmp_path)
    proj = ProjectStore(tmp_path).load()
    struct_before = _shots_token(proj.scenes)
    content_before = _shots_content_token(proj.scenes)

    # 只改镜头内容 → 结构指纹不变、内容指纹变
    proj.get_shot("s01_c01").shot_size = "close-up"
    assert _shots_token(proj.scenes) == struct_before
    assert _shots_content_token(proj.scenes) != content_before
    proj.get_shot("s01_c01").shot_size = "medium shot"

    # 只改故事段内容/剧本正文 → 两个镜头指纹都不变（域隔离）
    proj.scenes[0].summary = "改了剧情"
    proj.story_text = "正文也改了"
    assert _shots_token(proj.scenes) == struct_before
    assert _shots_content_token(proj.scenes) == content_before


def test_write_then_verify_receipts_persisted_in_meta(tmp_path):
    """meta.receipts / meta.trace / meta.intent 落盘，UI 与历史只信任 receipt。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.read", {"shot_id": "s01_c01"})]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c01",
                            "expected_revision": _shot_rev(tmp_path, "s01_c01"),
                            "lighting": "冷白荧光灯"})]),
        ("已调整第 1 镜光线。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "把第1镜光线改成冷白荧光"))
    meta = res["message"]["meta"]
    assert meta["intent"] == "shot_update"
    assert meta["write_success"] == 1
    assert meta["receipts"][0]["operation"] == "shot.update"
    assert meta["receipts"][0]["verified"] is True
    assert any(t["tool"] == "shot.update" and t["ok"] for t in meta["trace"])
