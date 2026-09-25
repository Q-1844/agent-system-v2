"""AI-7D-R · Script Writer 原生 function-calling tool-loop 测试。

不联网（ScriptedClient）。验证：
  - 单步 tool_call → 真实原子写 canonical + audit + 工作记忆 → 第二轮自然语言回复。
  - 多步循环（tool → tool → 最终回复）。
  - 纯对话回复 → 0 mutation。
  - 工具错误（空正文/坏 JSON）作为 observation 喂回，Agent 可继续；坏调用不落盘。
  - 乐观并发：expected_revision 不匹配 → 拒绝写入 + 返回最新版供 Agent 重新判断；不落盘、无审计。
  - 未配置 client → not_configured。
  - 权限：非 script_writer 会话拒绝。
  - /turn 端点经 unified_turn 路由到新 runner（端到端）。
"""
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.script_writer import script_writer_turn, SCRIPT_WRITER_TOOLS, _story_token, _scenes_token
from src.agents.revision_audit import DirectEditAuditStore
from types import SimpleNamespace


def _proj():
    return Project(
        name="demo", style=StyleAnchor(), story_text="小雨走进雨里。",
        characters=[Character(name="LinXia", description_en="a young woman")],
        scenes=[Scene(scene_id="s01", summary="rain", characters=["LinXia"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="look up at rain", characters=["LinXia"],
                                  target_duration_s=5)])])


def _save_proj(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    return tmp_path


def _multi_proj():
    """3 段项目（s01/s02/s03，next_scene_seq=4），用于插入/移动测试。"""
    def _sc(sid, summ):
        return Scene(scene_id=sid, summary=summ, characters=["LinXia"], shots=[])
    return Project(
        name="demo", style=StyleAnchor(), story_text="完整剧本正文。",
        characters=[Character(name="LinXia", description_en="a young woman")],
        scenes=[_sc("s01", "第一段"), _sc("s02", "第二段"), _sc("s03", "第三段")],
        next_scene_seq=4)


def _save_multi(tmp_path):
    ProjectStore(tmp_path).save(_multi_proj())
    return tmp_path


def _runtime(tmp_path, client=None, **kw):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client, **kw)

def _raise_unconfigured():
    """hermetic unconfigured（镜像生产 get_agnes_client 的 503 形态）：not_configured
    契约测试显式构造未配置环境，不依赖运行机 credential（Freeze Stabilization §5）。"""
    from fastapi import HTTPException
    raise HTTPException(503, "agnes not configured (hermetic test)")



def _run(coro):
    return asyncio.run(coro)


def _tool_call(name, arguments, call_id="call_1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


def _rev(tmp_path):
    """当前 canonical story_text 的版本指纹（乐观并发令牌）。
    测试用它模拟「Agent 读取时看到的版本」。"""
    return _story_token(ProjectStore(tmp_path).load().story_text or "")


def _scenes_rev(tmp_path):
    """当前 canonical scenes 的版本指纹（乐观并发令牌）。
    测试用它模拟「Agent 读取时看到的故事段列表版本」。"""
    return _scenes_token(ProjectStore(tmp_path).load().scenes)


def _scenes_token_of_ids(ids):
    """由 scene_id 列表直接算 scenes 指纹（用于预计算循环中途刷新后的令牌）。"""
    return _scenes_token([SimpleNamespace(scene_id=i) for i in ids])


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

    async def chat(self, messages, **kw):
        # 新 runner 不用 chat；保留以防回退
        return self.script and self.script.pop(0)[0] or ""


# ============ 单步工具调用 → 写入 → 最终回复 ============
def test_single_tool_call_edits_and_replies(tmp_path):
    _save_proj(tmp_path)
    new_text = "小雨走进雨里。她抬起头，望着灰蒙蒙的天。"
    client = ScriptedClient([
        (None, [_tool_call("script.update",
                           {"new_story_text": new_text, "expected_revision": _rev(tmp_path)})]),
        ("我已经把第二幕的描写改得更自然了。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把第二幕对白改自然一点"))

    assert res["edited"] is True
    assert "改" in res["assistant_message"]
    # canonical 真的改了
    assert ProjectStore(tmp_path).load().story_text == new_text
    # 审计 + 工作记忆
    audit = DirectEditAuditStore(tmp_path, "demo").recent()
    assert audit and audit[-1]["before"] == "小雨走进雨里。" and audit[-1]["after"] == new_text
    wm = rt.work_memory("demo").recent()
    assert any("剧本正文" in e["summary"] for e in wm)
    # 调了两次 LLM（一次 tool，一次 final）
    assert len(client.calls) == 2
    # 第一次请求带了 tools 定义
    assert client.calls[0]["kw"].get("tools") == SCRIPT_WRITER_TOOLS


# ============ 纯对话 → 0 mutation，一次 LLM 调用 ============
def test_conversation_no_mutation(tmp_path):
    _save_proj(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    client = ScriptedClient([("你想怎么改剧本？我可以帮你调整正文。", None)])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "你能做什么"))
    assert res["edited"] is False
    assert "你想怎么改" in res["assistant_message"]
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert len(client.calls) == 1


# ============ 工具错误喂回 → Agent 继续，坏调用不落盘 ============
def test_tool_error_feeds_back_and_continues(tmp_path):
    _save_proj(tmp_path)
    good_text = "修正后的完整剧本正文。"
    client = ScriptedClient([
        # 第一次：空正文（错误）
        (None, [_tool_call("script.update",
                           {"new_story_text": "  ", "expected_revision": _rev(tmp_path)},
                           "call_bad")]),
        # 第二次：修正
        (None, [_tool_call("script.update",
                           {"new_story_text": good_text, "expected_revision": _rev(tmp_path)},
                           "call_good")]),
        ("已修正。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "改剧本"))
    assert res["edited"] is True
    assert ProjectStore(tmp_path).load().story_text == good_text
    # 坏调用的 error 作为 tool 消息喂回了
    # ScriptedClient 存的是 messages 列表引用（被 _run_tool_loop 原地追加），
    # 故所有 calls 指向同一份最终列表；取最后一次调用的即可，避免重复计数。
    tool_msgs = [m for m in client.calls[-1]["messages"] if m.get("role") == "tool"]
    assert any("不能为空" in json.loads(m["content"]).get("error", "") for m in tool_msgs)


def test_unknown_tool_feeds_back(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("script.delete", {}, "call_x")]),
        ("我只有修改正文的能力。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    before = ProjectStore(tmp_path).load().story_text
    res = _run(script_writer_turn(rt, "demo", sid, "删掉剧本"))
    assert res["edited"] is False
    assert ProjectStore(tmp_path).load().story_text == before


# ============ 未变更（相同正文）→ edited False ============
def test_unchanged_text_not_edited(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("script.update",
                           {"new_story_text": "小雨走进雨里。", "expected_revision": _rev(tmp_path)})]),
        ("正文没有变化。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "重新保存一下"))
    assert res["edited"] is False


# ============ 乐观并发：过期版本 → 拒绝写入 + 返回最新版，不落盘 ============
def test_stale_rejection_no_write_returns_latest(tmp_path):
    _save_proj(tmp_path)
    original = "小雨走进雨里。"
    new_text = "这是基于过期版本的修改。"
    # 故意传一个错误的 expected_revision（模拟「读取后、写入前剧本被外部改动」）
    client = ScriptedClient([
        (None, [_tool_call("script.update",
                           {"new_story_text": new_text, "expected_revision": "deadbeef"},
                           "call_stale")]),
        ("我注意到剧本已被改动，已按最新版重新判断，本次未做修改。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "改剧本"))

    assert res["edited"] is False
    # canonical 未被写入
    assert ProjectStore(tmp_path).load().story_text == original
    # 没有审计记录（未写入）
    assert not DirectEditAuditStore(tmp_path, "demo").recent()
    # stale observation 喂回了，含当前最新指纹
    # ScriptedClient 存的是 messages 列表引用（被 _run_tool_loop 原地追加），
    # 故所有 calls 指向同一份最终列表；取最后一次调用的即可，避免重复计数。
    tool_msgs = [m for m in client.calls[-1]["messages"] if m.get("role") == "tool"]
    assert tool_msgs
    obs = json.loads(tool_msgs[0]["content"])
    assert obs.get("stale") is True
    assert obs.get("expected_revision") == "deadbeef"
    assert obs.get("current_revision") == _story_token(original)
    # 第二轮 system 已刷新（带最新指纹），Agent 基于最新状态回复
    assert len(client.calls) == 2


# ============ 权限：非 script_writer 拒绝 ============
def test_non_script_writer_rejected(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=ScriptedClient([]))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    try:
        _run(script_writer_turn(rt, "demo", sid, "改剧本"))
        assert False, "应拒绝"
    except AgentError as e:
        assert e.code == "permission_denied"


# ============ 未配置 client → not_configured ============
def test_not_configured_soft_fail(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=None,
                  agnes_getter=_raise_unconfigured,
                  sensenova_getter=lambda: None)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "改剧本"))
    assert res["edited"] is False
    assert "未配置" in res["assistant_message"]


# ============ 端到端：unified_turn /turn 路由到新 runner ============
def test_unified_turn_routes_to_script_writer(tmp_path):
    _save_proj(tmp_path)
    new_text = "经 unified_turn 修改后的剧本。"
    client = ScriptedClient([
        (None, [_tool_call("script.update",
                           {"new_story_text": new_text, "expected_revision": _rev(tmp_path)})]),
        ("已通过 /turn 修改。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    result = _run(rt.unified_turn("demo", sid, "帮我改剧本"))
    assert "已通过 /turn 修改。" in result["assistant_message"]
    assert result["edited"] is True
    assert ProjectStore(tmp_path).load().story_text == new_text
    # 返回结构含 message
    assert result["message"]["role"] == "assistant"


# ============================================================
# Phase 2a · story.create 单故事段工具测试
# _proj() 已有 1 段 s01（next_scene_seq=1）→ 首个新段 = s02，next_scene_seq→3
# ============================================================

# ============ 1. 创建成功 ============
def test_story_create_success(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("story.create",
                           {"summary": "林夏抬头望向灰蒙蒙的天空，雨滴落下。",
                            "characters": ["LinXia"],
                            "expected_scenes_revision": _scenes_rev(tmp_path)},
                           "call_1")]),
        ("已创建故事段 s02。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把第二段整理成故事段"))

    assert res["created"] is True
    assert res["edited"] is False
    assert res["intent"] == "story_create"
    # events 记了 story_create
    assert any(e.get("kind") == "story_create" and e.get("scene_id") == "s02"
               for e in res["events"])
    # canonical 真的写了
    proj = ProjectStore(tmp_path).load()
    assert len(proj.scenes) == 2
    new_scene = proj.scenes[-1]
    assert new_scene.scene_id == "s02"
    assert new_scene.summary == "林夏抬头望向灰蒙蒙的天空，雨滴落下。"
    assert new_scene.characters == ["LinXia"]
    assert new_scene.shots == []
    # next_scene_seq 自增（s02 分配后 → 3）
    assert proj.next_scene_seq == 3
    # 审计：field=scenes，before=[s01]，after.scene_id=s02
    audit = DirectEditAuditStore(tmp_path, "demo").recent()
    assert audit and audit[-1]["field"] == "scenes"
    assert audit[-1]["before"] == ["s01"]
    assert audit[-1]["after"]["scene_id"] == "s02"
    # 工作记忆
    wm = rt.work_memory("demo").recent()
    assert any("故事段" in e["summary"] for e in wm)
    # observation 喂回（含 scene_id/order/scene/scenes_revision）
    # ScriptedClient 存的是 messages 列表引用（被 _run_tool_loop 原地追加），
    # 故所有 calls 指向同一份最终列表；取最后一次调用的即可，避免重复计数。
    tool_msgs = [m for m in client.calls[-1]["messages"] if m.get("role") == "tool"]
    assert tool_msgs
    obs = json.loads(tool_msgs[0]["content"])
    assert obs.get("ok") is True
    assert obs.get("scene_id") == "s02"
    assert obs.get("order") == 2
    assert obs.get("scene", {}).get("scene_id") == "s02"
    assert obs.get("scenes_revision") == _scenes_token_of_ids(["s01", "s02"])
    # 2 次 LLM 调用（tool → final），第一次带 tools 定义
    assert len(client.calls) == 2
    assert client.calls[0]["kw"].get("tools") == SCRIPT_WRITER_TOOLS


# ============ 2. ID 确定性 + 单循环内连续创建 s02→s03 不撞号 ============
def test_story_create_id_determinism_sequential(tmp_path):
    _save_proj(tmp_path)
    # 第一步用当前 token；第二步须用刷新后 token（s01,s02）
    rev0 = _scenes_rev(tmp_path)                              # [s01]
    rev1 = _scenes_token_of_ids(["s01", "s02"])              # 第一段创建后
    client = ScriptedClient([
        (None, [_tool_call("story.create",
                           {"summary": "第一段新故事。",
                            "characters": ["LinXia"],
                            "expected_scenes_revision": rev0},
                           "call_1")]),
        (None, [_tool_call("story.create",
                           {"summary": "第二段新故事。",
                            "characters": [],
                            "expected_scenes_revision": rev1},
                           "call_2")]),
        ("已创建 s02 和 s03。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "创建两个故事段"))

    assert res["created"] is True
    proj = ProjectStore(tmp_path).load()
    # 顺序 + ID 单调不撞号
    assert [s.scene_id for s in proj.scenes] == ["s01", "s02", "s03"]
    assert proj.next_scene_seq == 4
    # 两个 story_create 事件按序
    create_evs = [e for e in res["events"] if e.get("kind") == "story_create"]
    assert [e["scene_id"] for e in create_evs] == ["s02", "s03"]


# ============ 3. 顺序：追加末尾，不动已有段 ============
def test_story_create_order_preservation(tmp_path):
    _save_proj(tmp_path)
    before_proj = ProjectStore(tmp_path).load()
    client = ScriptedClient([
        (None, [_tool_call("story.create",
                           {"summary": "新追加的故事段。",
                            "characters": ["LinXia"],
                            "expected_scenes_revision": _scenes_rev(tmp_path)},
                           "call_1")]),
        ("已追加。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    _run(script_writer_turn(rt, "demo", sid, "追加一段"))

    proj = ProjectStore(tmp_path).load()
    assert len(proj.scenes) == 2
    # 原段仍在前，关键字段不变
    assert proj.scenes[0].scene_id == "s01"
    assert proj.scenes[0].summary == before_proj.scenes[0].summary
    assert proj.scenes[0].characters == before_proj.scenes[0].characters
    assert proj.scenes[0].shots[0].shot_id == "s01_c01"   # 镜头不动
    assert len(proj.scenes[0].shots) == 1
    # 新段在末尾，无镜头
    assert proj.scenes[-1].scene_id == "s02"
    assert len(proj.scenes[-1].shots) == 0
    # observation order == 2（数组长度 / 1-based 末尾位置）
    # ScriptedClient 存的是 messages 列表引用（被 _run_tool_loop 原地追加），
    # 故所有 calls 指向同一份最终列表；取最后一次调用的即可，避免重复计数。
    tool_msgs = [m for m in client.calls[-1]["messages"] if m.get("role") == "tool"]
    obs = json.loads(tool_msgs[0]["content"])
    assert obs["order"] == 2


# ============ 4. 字段校验：坏调用作 error obs 喂回，不落盘，Agent 可继续 ============
def test_story_create_field_validation(tmp_path):
    _save_proj(tmp_path)
    good_summary = "合格的故事段正文。"
    # 坏 JSON 的 tool_call 必须绕过 _tool_call（它会对 arguments 再 json.dumps）
    bad_json_tc = {"id": "call_d", "type": "function",
                   "function": {"name": "story.create",
                                "arguments": "{not valid json"}}
    client = ScriptedClient([
        # (a) 空 summary
        (None, [_tool_call("story.create",
                           {"summary": "  ",
                            "characters": ["LinXia"],
                            "expected_scenes_revision": _scenes_rev(tmp_path)},
                           "call_a")]),
        # (b) characters 非列表（传字符串）
        (None, [_tool_call("story.create",
                           {"summary": "正文",
                            "characters": "LinXia",
                            "expected_scenes_revision": _scenes_rev(tmp_path)},
                           "call_b")]),
        # (c) 缺 expected_scenes_revision
        (None, [_tool_call("story.create",
                           {"summary": "正文",
                            "characters": ["LinXia"]},
                           "call_c")]),
        # (d) 坏 JSON
        (None, [bad_json_tc]),
        # (e) 修正后成功（坏调用都不写盘，token 仍是 [s01]）
        (None, [_tool_call("story.create",
                           {"summary": good_summary,
                            "characters": ["LinXia"],
                            "expected_scenes_revision": _scenes_rev(tmp_path)},
                           "call_e")]),
        ("已创建。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "创建一段"))

    assert res["created"] is True
    proj = ProjectStore(tmp_path).load()
    assert len(proj.scenes) == 2
    assert proj.scenes[-1].scene_id == "s02"
    assert proj.scenes[-1].summary == good_summary
    # 4 条错误 + 1 条成功 = 5 条 tool 消息
    # ScriptedClient 存的是 messages 列表引用（被 _run_tool_loop 原地追加），
    # 故所有 calls 指向同一份最终列表；取最后一次调用的即可，避免重复计数。
    tool_msgs = [m for m in client.calls[-1]["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 5
    errors = [json.loads(m["content"]) for m in tool_msgs
              if not json.loads(m["content"]).get("ok")]
    assert len(errors) == 4
    err_blob = " ".join(e.get("error", "") for e in errors)
    assert "summary 不能为空" in err_blob
    assert "characters 必须是字符串数组" in err_blob
    assert "expected_scenes_revision" in err_blob
    assert "合法 JSON" in err_blob


# ============ 5. 并发冲突：stale 不分配 ID/不写盘/不审计，返回最新列表 ============
def test_story_create_stale_rejection_no_write(tmp_path):
    _save_proj(tmp_path)
    original_seq = ProjectStore(tmp_path).load().next_scene_seq   # 1
    client = ScriptedClient([
        (None, [_tool_call("story.create",
                           {"summary": "基于过期列表的创建。",
                            "characters": ["LinXia"],
                            "expected_scenes_revision": "deadbeef"},
                           "call_stale")]),
        ("故事段列表已被改动，我按最新版重新判断，本次未创建。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "创建一段"))

    assert res["created"] is False
    assert res["edited"] is False
    proj = ProjectStore(tmp_path).load()
    assert len(proj.scenes) == 1
    assert proj.scenes[0].scene_id == "s01"
    # 关键：stale 不分配 ID（next_scene_seq 未自增）
    assert proj.next_scene_seq == original_seq
    # 无审计
    assert not DirectEditAuditStore(tmp_path, "demo").recent()
    # stale obs 喂回，含 current_revision + current_scenes 快照
    # ScriptedClient 存的是 messages 列表引用（被 _run_tool_loop 原地追加），
    # 故所有 calls 指向同一份最终列表；取最后一次调用的即可，避免重复计数。
    tool_msgs = [m for m in client.calls[-1]["messages"] if m.get("role") == "tool"]
    assert tool_msgs
    obs = json.loads(tool_msgs[0]["content"])
    assert obs.get("stale") is True
    assert obs.get("expected_revision") == "deadbeef"
    assert obs.get("current_revision") == _scenes_token_of_ids(["s01"])
    assert obs.get("current_scenes") and obs["current_scenes"][0]["scene_id"] == "s01"
    # 2 次 LLM 调用（stale → 刷新 → 最终回复）
    assert len(client.calls) == 2


# ============ 6. 工具错误喂回：未知工具 → error obs，0 写盘，Agent 继续 ============
def test_story_create_unknown_tool_feeds_back(tmp_path):
    _save_proj(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    # story.delete 已是真实工具（两阶段删除）；用一个确实不存在的工具名验证未知工具分支。
    client = ScriptedClient([
        (None, [_tool_call("story.cut", {"scene_id": "s01"}, "call_x")]),
        ("我没有这种能力。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "切掉第一段"))

    assert res["created"] is False
    assert res["edited"] is False
    assert res.get("deleted") is False
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    # ScriptedClient 存的是 messages 列表引用（被 _run_tool_loop 原地追加），
    # 故所有 calls 指向同一份最终列表；取最后一次调用的即可，避免重复计数。
    tool_msgs = [m for m in client.calls[-1]["messages"] if m.get("role") == "tool"]
    assert tool_msgs
    obs = json.loads(tool_msgs[0]["content"])
    assert obs.get("ok") is False
    assert "未知工具" in obs.get("error", "")


# ============ 7. 0 非预期 mutation：纯对话 ============
def test_story_create_conversation_no_mutation(tmp_path):
    _save_proj(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    before_proj = ProjectStore(tmp_path).load()
    # 明确写入请求（加一段）但模型只反问澄清、零写尝试 → zero-write recovery 触发一次；
    # 恢复后模型仍澄清（未声称成功）→ 按设计放行澄清回复，全程零落盘。
    client = ScriptedClient([
        ("你想创建什么样的故事段？告诉我内容。", None),
        ("你还没告诉我具体内容，想加什么故事？", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "我想加一段"))

    assert res["created"] is False
    assert res["edited"] is False
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    after_proj = ProjectStore(tmp_path).load()
    assert len(after_proj.scenes) == len(before_proj.scenes)
    assert after_proj.story_text == before_proj.story_text
    assert len(client.calls) == 2   # 原始澄清 + recovery 后再澄清（恢复仅此一次）


# ============ 8. 回归守卫：工具集注册表 ============
def test_tools_registry_includes_all():
    """写/读工具齐全。原 script.update / story.create 测试断言 tools == SCRIPT_WRITER_TOOLS
    （列表对象），工具增减不破坏对象身份；本测试守名字集合。"""
    names = set(t["function"]["name"] for t in SCRIPT_WRITER_TOOLS)
    # 40.md Skill System V1：+skill.list / skill.load / skill.read_resource（只读）
    assert names == {"script.update", "script.read",
                     "story.create", "story.list", "story.read",
                     "story.move", "story.update", "story.delete",
                     "story.create_many", "story.update_many",
                     "story.delete_many", "story.insert_many",
                     "web.search",
                     "skill.list", "skill.load", "skill.read_resource"}


# ============================================================
# Phase 2a-next · 按需读取 + system 瘦身 + 任意位置插入 + story.move
# ============================================================
from src.agents.script_writer import (   # noqa: E402
    _execute_script_read, _execute_story_list, _execute_story_read,
    _execute_story_create, _execute_story_move, _build_system,
)


def _rt(tmp_path, multi=False):
    ProjectStore(tmp_path).save(_multi_proj() if multi else _proj())
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=ScriptedClient([]))


# ============ 按需读取工具 ============
def test_script_read_returns_full_text_and_revision(tmp_path):
    rt = _rt(tmp_path)
    obs = _execute_script_read(rt, "demo", "{}", "u", "m")
    assert obs["ok"] is True
    assert obs["text"] == "小雨走进雨里。"
    assert obs["length"] == len("小雨走进雨里。")
    assert obs["revision"] == _story_token("小雨走进雨里。")
    # 只读不写盘
    assert not DirectEditAuditStore(tmp_path, "demo").recent()


def test_story_list_returns_scenes_and_revision(tmp_path):
    rt = _rt(tmp_path, multi=True)
    obs = _execute_story_list(rt, "demo", "{}", "u", "m")
    assert obs["ok"] is True
    assert obs["count"] == 3
    assert [s["scene_id"] for s in obs["scenes"]] == ["s01", "s02", "s03"]
    assert obs["revision"] == _scenes_token_of_ids(["s01", "s02", "s03"])
    assert obs["scenes"][0]["shot_count"] == 0
    assert not DirectEditAuditStore(tmp_path, "demo").recent()


def test_story_read_returns_one_scene(tmp_path):
    rt = _rt(tmp_path, multi=True)
    obs = _execute_story_read(rt, "demo", json.dumps({"scene_id": "s02"}), "u", "m")
    assert obs["ok"] is True
    assert obs["scene"]["scene_id"] == "s02"
    assert obs["scene"]["summary"] == "第二段"
    # 不存在
    try:
        _execute_story_read(rt, "demo", json.dumps({"scene_id": "s99"}), "u", "m")
        assert False, "应拒绝"
    except AgentError as e:
        assert e.code == "not_found"


# ============ 0 非必要上下文注入：system 不含正文/段摘要全文 ============
def test_system_does_not_inject_full_content(tmp_path):
    rt = _rt(tmp_path, multi=True)
    proj = ProjectStore(tmp_path).load()
    sysmsg = _build_system(proj)
    # 不含完整剧本正文
    assert "完整剧本正文。" not in sysmsg
    # 不含任何故事段摘要全文
    assert "第一段" not in sysmsg and "第二段" not in sysmsg and "第三段" not in sysmsg
    # 只含元信息（项目名/字数/段数）
    assert "demo" in sysmsg
    assert "3 个故事段" in sysmsg
    # 工具说明提到职责边界（镜头归 Director）
    assert "Director" in sysmsg


def test_system_length_matches_real_story_len(tmp_path):
    """字数元信息用真实 len(story_text)。"""
    proj = ProjectStore(tmp_path).load() if False else _multi_proj()
    sysmsg = _build_system(proj)
    assert f"{len(proj.story_text or '')} 字" in sysmsg


# ============ 任意位置插入（before/after/末尾/最前）============
def test_story_create_before_anchor(tmp_path):
    rt = _rt(tmp_path, multi=True)
    rev = _scenes_rev(tmp_path)
    obs = _execute_story_create(
        rt, "demo",
        json.dumps({"summary": "插在s02前", "characters": ["LinXia"],
                    "before_scene_id": "s02", "expected_scenes_revision": rev}),
        "u", "m")
    assert obs["ok"] is True
    assert obs["scene_id"] == "s04"
    assert obs["order"] == 2
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == \
        ["s01", "s04", "s02", "s03"]


def test_story_create_after_anchor(tmp_path):
    rt = _rt(tmp_path, multi=True)
    obs = _execute_story_create(
        rt, "demo",
        json.dumps({"summary": "插在s01后", "characters": [],
                    "after_scene_id": "s01", "expected_scenes_revision": _scenes_rev(tmp_path)}),
        "u", "m")
    assert obs["order"] == 2
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == \
        ["s01", "s04", "s02", "s03"]


def test_story_create_at_front(tmp_path):
    rt = _rt(tmp_path, multi=True)
    obs = _execute_story_create(
        rt, "demo",
        json.dumps({"summary": "插到最前", "before_scene_id": "s01",
                    "expected_scenes_revision": _scenes_rev(tmp_path)}),
        "u", "m")
    assert obs["order"] == 1
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == \
        ["s04", "s01", "s02", "s03"]


def test_story_create_append_default(tmp_path):
    rt = _rt(tmp_path, multi=True)
    obs = _execute_story_create(
        rt, "demo",
        json.dumps({"summary": "追加末尾", "expected_scenes_revision": _scenes_rev(tmp_path)}),
        "u", "m")
    assert obs["order"] == 4
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == \
        ["s01", "s02", "s03", "s04"]


# ============ story.move 首/中/尾 ============
def test_story_move_to_front(tmp_path):
    rt = _rt(tmp_path, multi=True)
    obs = _execute_story_move(
        rt, "demo",
        json.dumps({"scene_id": "s03", "before_scene_id": "s01",
                    "expected_scenes_revision": _scenes_rev(tmp_path)}),
        "u", "m")
    assert obs["ok"] is True
    assert obs["order"] == 1
    assert obs["scenes_order"] == ["s03", "s01", "s02"]
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == \
        ["s03", "s01", "s02"]


def test_story_move_to_middle(tmp_path):
    rt = _rt(tmp_path, multi=True)
    # s03 移到 s01 之后（中间）
    obs = _execute_story_move(
        rt, "demo",
        json.dumps({"scene_id": "s03", "after_scene_id": "s01",
                    "expected_scenes_revision": _scenes_rev(tmp_path)}),
        "u", "m")
    assert obs["order"] == 2
    assert obs["scenes_order"] == ["s01", "s03", "s02"]


def test_story_move_to_end(tmp_path):
    rt = _rt(tmp_path, multi=True)
    obs = _execute_story_move(
        rt, "demo",
        json.dumps({"scene_id": "s01", "expected_scenes_revision": _scenes_rev(tmp_path)}),
        "u", "m")
    assert obs["order"] == 3
    assert obs["scenes_order"] == ["s02", "s03", "s01"]
    # move 不分配新 ID、不改段内镜头
    assert DirectEditAuditStore(tmp_path, "demo").recent()[-1]["after"] == \
        ["s02", "s03", "s01"]


# ============ 非法目标 / 锚点 ============
def test_story_create_both_anchors_rejected(tmp_path):
    rt = _rt(tmp_path, multi=True)
    before = (tmp_path / "output" / "project.json").read_bytes()
    try:
        _execute_story_create(
            rt, "demo",
            json.dumps({"summary": "x", "before_scene_id": "s01", "after_scene_id": "s02",
                        "expected_scenes_revision": _scenes_rev(tmp_path)}),
            "u", "m")
        assert False, "应拒绝"
    except AgentError as e:
        assert e.code == "invalid"
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not DirectEditAuditStore(tmp_path, "demo").recent()


def test_story_create_nonexistent_anchor(tmp_path):
    rt = _rt(tmp_path, multi=True)
    try:
        _execute_story_create(
            rt, "demo",
            json.dumps({"summary": "x", "after_scene_id": "s99",
                        "expected_scenes_revision": _scenes_rev(tmp_path)}),
            "u", "m")
        assert False
    except AgentError as e:
        assert e.code == "not_found"
    # 未写盘
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == \
        ["s01", "s02", "s03"]


def test_story_move_self_or_both_anchors_rejected(tmp_path):
    rt = _rt(tmp_path, multi=True)
    before = (tmp_path / "output" / "project.json").read_bytes()

    def _expect_invalid(payload):
        try:
            _execute_story_move(rt, "demo", json.dumps(payload), "u", "m")
            assert False, "应拒绝"
        except AgentError as e:
            assert e.code == "invalid"

    # 指向自身
    _expect_invalid({"scene_id": "s02", "before_scene_id": "s02",
                     "expected_scenes_revision": _scenes_rev(tmp_path)})
    # 同时给 before/after
    _expect_invalid({"scene_id": "s02", "before_scene_id": "s01", "after_scene_id": "s03",
                     "expected_scenes_revision": _scenes_rev(tmp_path)})
    # 不存在的段
    try:
        _execute_story_move(
            rt, "demo",
            json.dumps({"scene_id": "s99", "expected_scenes_revision": _scenes_rev(tmp_path)}),
            "u", "m")
        assert False
    except AgentError as e:
        assert e.code == "not_found"
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not DirectEditAuditStore(tmp_path, "demo").recent()


# ============ stale 冲突：create 与 move 都不写盘/不分配 ID ============
def test_story_create_stale_no_write(tmp_path):
    rt = _rt(tmp_path, multi=True)
    seq_before = ProjectStore(tmp_path).load().next_scene_seq
    obs = _execute_story_create(
        rt, "demo",
        json.dumps({"summary": "过期创建", "expected_scenes_revision": "deadbeef"}),
        "u", "m")
    assert obs["stale"] is True
    assert obs["current_revision"] == _scenes_token_of_ids(["s01", "s02", "s03"])
    assert [s["scene_id"] for s in obs["current_scenes"]] == ["s01", "s02", "s03"]
    proj = ProjectStore(tmp_path).load()
    assert len(proj.scenes) == 3
    assert proj.next_scene_seq == seq_before   # stale 不分配 ID
    assert not DirectEditAuditStore(tmp_path, "demo").recent()


def test_story_move_stale_no_write(tmp_path):
    rt = _rt(tmp_path, multi=True)
    before = (tmp_path / "output" / "project.json").read_bytes()
    obs = _execute_story_move(
        rt, "demo",
        json.dumps({"scene_id": "s03", "before_scene_id": "s01",
                    "expected_scenes_revision": "deadbeef"}),
        "u", "m")
    assert obs["stale"] is True
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == \
        ["s01", "s02", "s03"]
    assert not DirectEditAuditStore(tmp_path, "demo").recent()


# ============ 端到端：按需 read → write（不依赖 system 注入正文）============
def test_turn_read_then_update_no_system_injection(tmp_path):
    """Agent 先 script.read 拿正文+指纹，再 script.update；system 不含正文。"""
    _save_proj(tmp_path)
    new_text = "按需读取后修改的完整正文。"
    client = ScriptedClient([
        (None, [_tool_call("script.read", {})]),
        (None, [_tool_call("script.update",
                           {"new_story_text": new_text,
                            "expected_revision": _rev(tmp_path)})]),
        ("已按读取到的内容修改。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "改剧本"))
    assert res["edited"] is True
    assert ProjectStore(tmp_path).load().story_text == new_text
    # 第一次发给 LLM 的 system 不含原正文
    first_sys = client.calls[0]["messages"][0]["content"]
    assert "小雨走进雨里" not in first_sys


def test_turn_list_then_create_and_move(tmp_path):
    """Agent 先 story.list 拿指纹，再 story.create，再 story.move。"""
    _save_multi(tmp_path)
    rev0 = _scenes_rev(tmp_path)
    rev1 = _scenes_token_of_ids(["s01", "s02", "s03", "s04"])
    client = ScriptedClient([
        (None, [_tool_call("story.list", {})]),
        (None, [_tool_call("story.create",
                           {"summary": "新段", "characters": [],
                            "expected_scenes_revision": rev0})]),
        (None, [_tool_call("story.move",
                           {"scene_id": "s04", "before_scene_id": "s01",
                            "expected_scenes_revision": rev1})]),
        ("已创建并移到最前。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "加一段并置顶"))
    assert res["created"] is True and res["moved"] is True
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == \
        ["s04", "s01", "s02", "s03"]


# ============ Renewable window 镜像：续期后正常完成（三 Agent 统一）============
def test_window_renewal_then_complete(tmp_path):
    """窗口用满 → 注入 self-check nudge → 续期 → 继续读工具 → 纯文本正常结束。
    与 tests/test_long_execution.test_window_renewal_then_complete（director 侧）
    对齐，验证 Script Writer 的 _run_tool_loop 同一 renewable window 机制。"""
    _save_proj(tmp_path)
    from src.agents.script_writer import _run_tool_loop as sw_loop
    client = ScriptedClient([
        (None, [_tool_call("story.list", {})]),    # step 0
        (None, [_tool_call("story.list", {})]),    # step 1
        (None, [_tool_call("story.list", {})]),    # step 2
        # 窗口用满（max_steps=3）→ 续期注入 nudge → 第 4 次调用看到 nudge
        (None, [_tool_call("story.list", {})]),    # step 3（续期后）
        ("当前状态已确认，无需进一步操作。", None),   # 纯文本 → 正常结束
    ])
    rt = _runtime(tmp_path, client=client)
    instruction = "看看当前故事段的情况如何？"   # 含「如何」→ chat_turn，绕过写域闸门
    messages = [{"role": "system", "content": "你是剧本作者。"},
                {"role": "user", "content": instruction}]
    res = _run(sw_loop(rt, client, "demo", messages, instruction, None, max_steps=3))
    assert len(client.calls) == 5
    nudge_msgs = [m for m in client.calls[3]["messages"]
                  if m.get("role") == "user"
                  and "【系统提示·执行窗口】" in (m.get("content") or "")]
    assert len(nudge_msgs) == 1
    assert res["window_renewals"] == 1
    assert res["text"] == "当前状态已确认，无需进一步操作。"
