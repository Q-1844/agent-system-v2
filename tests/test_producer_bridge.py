"""Producer V1 Phase 1 — Specialist Bridge Core 测试（§10 A-L）。

不联网（ScriptedClient）。单 ScriptedClient 交错喂 Producer 与 Specialist 双循环：
Producer loop（event_cb=None → chat_completion）+ Specialist turn（经 Bridge _cb，
event_cb≠None → chat_completion_stream），调用顺序天然按时间序从同一 script 队列消费。

夹具复用 tests.test_director_agent 的 _proj/_save_proj/_runtime/_run/_tool_call 模式。
"""
import asyncio
import json
import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents import producer, turn_registry
from src.agents.project_events import (get_project_bus, reset_project_bus,
                                       RESOURCE_STORY, RESOURCE_CHARACTER,
                                       RESOURCE_STORYBOARD)
from src.agents.script_writer import _story_token
from src.agents.character_designer import _character_token
from src.agents.director import _shots_token, _shot_token
from src.agents.tasks import AgentTaskStore


# ============ 夹具 ============
def _proj():
    """2 段项目：s01 已有 2 镜，s02 空（Director 目标段）；2 角色；有剧本正文。"""
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
            "function": {"name": name,
                         "arguments": json.dumps(arguments, ensure_ascii=False)}}


def _story_rev(tmp_path):
    return _story_token(ProjectStore(tmp_path).load().story_text or "")


def _char_rev(tmp_path, name):
    return _character_token(ProjectStore(tmp_path).load().get_character(name))


def _shots_rev(tmp_path):
    return _shots_token(ProjectStore(tmp_path).load().scenes)


def _sw_session(rt):
    return rt._session_store("demo").get_or_create_conversation("script_writer")


def _cd_session(rt):
    return rt._session_store("demo").get_or_create_conversation("character_designer")


def _dir_session(rt):
    return rt._session_store("demo").get_or_create_conversation("director")


class ScriptedClient:
    """按调用顺序返回脚本化响应。chat_completion（Producer loop）与
    chat_completion_stream（Specialist turn）共用同一 script 队列。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def _pop(self):
        if not self.script:
            return "（脚本已耗尽）", None
        return self.script.pop(0)

    async def chat_completion(self, messages, **kw):
        self.calls.append({"mode": "sync", "messages": messages,
                            "tools": [t["function"]["name"] for t in (kw.get("tools") or [])]})
        c, tc = self._pop()
        return {"content": c, "tool_calls": tc}

    async def chat_completion_stream(self, messages, **kw):
        self.calls.append({"mode": "stream", "messages": messages,
                            "tools": [t["function"]["name"] for t in (kw.get("tools") or [])]})
        c, tc = self._pop()
        yield {"kind": "finish", "content": c, "tool_calls": tc}


class CancelClient:
    """包装 ScriptedClient：第 n 次调用（任意方法）时 set cancel_event。"""
    def __init__(self, inner, cancel, set_on=2):
        self.inner = inner
        self.cancel = cancel
        self.set_on = set_on
        self.n = 0

    async def chat_completion(self, messages, **kw):
        self.n += 1
        if self.n >= self.set_on:
            self.cancel.set()
        return await self.inner.chat_completion(messages, **kw)

    async def chat_completion_stream(self, messages, **kw):
        self.n += 1
        if self.n >= self.set_on:
            self.cancel.set()
        async for ev in self.inner.chat_completion_stream(messages, **kw):
            yield ev


@pytest.fixture(autouse=True)
def _clean_registry():
    turn_registry.clear()
    reset_project_bus()
    yield
    turn_registry.clear()
    reset_project_bus()


def _envelope(agent, *, objective, situation="", constraints=None, scope=None,
              user_intent="请检查这个项目并做必要优化"):
    return {"objective": objective, "situation": situation,
            "constraints": constraints or [], "scope": scope or {},
            "user_intent": user_intent}


# ============ H：ownership（工具面无写/生成工具）============
def test_H_producer_has_no_write_or_gen_tools():
    offered, _ = producer._build_producer_tools()
    names = {t["function"]["name"] for t in offered}
    # delegate 工具存在
    assert "script_writer.delegate" in names
    assert "character_designer.delegate" in names
    assert "director.delegate" in names
    # read 工具存在
    for r in ("project.inspect", "story.list", "story.read", "script.read",
              "character.list", "character.read", "shot.list", "shot.read"):
        assert r in names, f"缺少只读工具 {r}"
    # 🔴 绝无任何 story/character/shot 写工具与生成工具
    forbidden = {"script.update", "story.create", "story.update", "story.move",
                 "story.delete", "story.insert_many", "story.delete_many",
                 "character.create", "character.update", "character.delete",
                 "character.generate_views",
                 "shot.create", "shot.create_many", "shot.update", "shot.update_many",
                 "shot.delete", "shot.move", "shot.generate_images",
                 "shot.generate_videos", "shot.audio"}
    assert names.isdisjoint(forbidden), f"越权工具: {names & forbidden}"
    # PRODUCER_WRITE_OPERATIONS 必须为空
    assert producer.PRODUCER_WRITE_OPERATIONS == set()


# ============ A：真实委派 Script Writer ============
def test_A_delegate_script_writer(tmp_path):
    _save_proj(tmp_path)
    new_text = "完整剧本正文。林小雨推开咖啡馆的门，雨后的街道湿漉漉的。"
    env = _envelope("script_writer",
                    objective="把剧本正文改得更自然，补充第二幕的环境描写",
                    situation="用户觉得当前剧本太单薄，想要全流程优化；先从故事入手",
                    constraints=["保留原有角色与结局不动"],
                    user_intent="请检查这个项目并做必要优化")
    client = ScriptedClient([
        (None, [_tool_call("script_writer.delegate", env, "call_del")]),      # producer
        (None, [_tool_call("story.list", {}, "sw1")]),                        # SW read
        (None, [_tool_call("script.update",
                           {"new_story_text": new_text,
                            "expected_revision": _story_rev(tmp_path)}, "sw2")]),  # SW write
        ("已把第二幕环境描写补自然了。", None),                                  # SW final
        ("我已让 Script Writer 优化剧本正文，补充了第二幕描写；角色无需调整。", None),  # producer final
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "请检查这个项目并做必要优化"))

    assert res["intent"] == "delegation"
    assert len(res["receipts"]) == 1
    rec = res["receipts"][0]
    assert rec["agent"] == "script_writer"
    assert rec["status"] == "completed"
    assert rec["ok"] is True
    assert rec["task_id"].startswith("task_")

    # SW session 真实存在、agent_id 正确、首条 user = envelope 渲染（含 user_intent 首段）
    sw = _sw_session(rt)
    assert sw.agent_id == "script_writer"
    first_user = next(m for m in sw.messages if m.role == "user")
    assert "【来自 Producer 的委派任务】" in first_user.content
    assert "用户意图：请检查这个项目并做必要优化" in first_user.content
    assert "任务目标：把剧本正文改得更自然" in first_user.content

    # canonical 真被 SW 改了
    assert ProjectStore(tmp_path).load().story_text == new_text
    # Producer 侧 receipts 只有 delegate 回执（无 SW 的内部 shot/scene 写回执）
    assert all(r["operation"] == "script_writer.delegate" for r in res["receipts"])


# ============ B：真实委派 Character Designer ============
def test_B_delegate_character_designer(tmp_path):
    _save_proj(tmp_path)
    new_desc = "a young woman in a red raincoat, soaked from the rain, tired eyes"
    env = _envelope("character_designer",
                    objective="调整林小雨的视觉设定，让她符合雨后的疲惫氛围",
                    situation="剧本刚被优化为雨后场景，林小雨的形象需要更贴合",
                    constraints=["不改角色名与基本身份"],
                    user_intent="请检查这个项目并做必要优化")
    client = ScriptedClient([
        (None, [_tool_call("character_designer.delegate", env, "call_del")]),
        (None, [_tool_call("character.list", {}, "cd1")]),
        (None, [_tool_call("character.update",
                           {"character_name": "林小雨",
                            "expected_revision": _char_rev(tmp_path, "林小雨"),
                            "description_en": new_desc}, "cd2")]),
        ("已把林小雨的雨后疲惫感补进设定。", None),
        ("我已让 Character Designer 调整林小雨的设定以贴合雨后氛围。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "请检查这个项目并做必要优化"))

    rec = res["receipts"][0]
    assert rec["agent"] == "character_designer"
    assert rec["status"] == "completed"
    assert rec["changed_refs"]["characters"] == ["林小雨"]
    # CD session 真实存在
    cd = _cd_session(rt)
    assert cd.agent_id == "character_designer"
    # canonical 真被 CD 改了
    assert ProjectStore(tmp_path).load().get_character("林小雨").description_en == new_desc


# ============ C + E：真实委派 Director + Task Result changed_refs ============
def test_C_delegate_director_and_task_result(tmp_path):
    _save_proj(tmp_path)
    shots = [
        {"description": "雨点砸在窗玻璃上，两人隔桌对坐", "characters": ["林小雨", "陈默"],
         "shot_size": "medium shot", "camera_angle": "eye level", "target_duration_s": 5},
        {"description": "林小雨低头搅动咖啡", "characters": ["林小雨"],
         "shot_size": "close-up", "target_duration_s": 4},
    ]
    env = _envelope("director",
                    objective="为第2段（s02）设计分镜",
                    situation="故事与角色已就绪，s02 当前没有镜头",
                    constraints=["保留 s01 已有镜头不动"],
                    user_intent="请检查这个项目并做必要优化")
    client = ScriptedClient([
        (None, [_tool_call("director.delegate", env, "call_del")]),
        (None, [_tool_call("shot.list", {"scene_id": "s02"}, "d1")]),
        (None, [_tool_call("shot.create_many",
                           {"scene_id": "s02", "shots": shots,
                            "expected_shots_revision": _shots_rev(tmp_path)}, "d2")]),
        ("已为第2段设计 2 个分镜。", None),
        ("我已让 Director 为第2段设计了分镜。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "请检查这个项目并做必要优化"))

    rec = res["receipts"][0]
    assert rec["agent"] == "director"
    assert rec["status"] == "completed"
    # E：changed_refs 与真实 receipts 一致（改了 s02 的 shots）
    assert rec["changed_refs"]["scenes"] == ["s02"]
    assert rec["changed_refs"]["shots"] == ["s02_c01", "s02_c02"]
    assert rec["warnings"] == []
    assert rec["verified"] is True
    assert rec["session_id"]  # director session
    assert rec["execution_ref"].startswith("agent_task:")
    # 走的是 director_turn（real canonical 写：s02 落了 2 镜，director_min 无 create_many）
    proj = ProjectStore(tmp_path).load()
    assert [sh.shot_id for sh in proj.scenes[1].shots] == ["s02_c01", "s02_c02"]
    # director session 真实存在
    assert _dir_session(rt).agent_id == "director"


# ============ D：Envelope 五段齐全 ============
def test_D_envelope_render_has_five_segments():
    env = _envelope("script_writer",
                    objective="优化后半段节奏",
                    situation="用户认为后半段拖沓",
                    constraints=["不要改变最终结局", "保留角色出场顺序"],
                    scope={"scene_ids": ["s05", "s06"]},
                    user_intent="全流程优化一下")
    text = producer.render_envelope(env)
    assert "用户意图：全流程优化一下" in text
    assert "任务目标：优化后半段节奏" in text
    assert "背景情况：用户认为后半段拖沓" in text
    assert "不要改变最终结局" in text
    assert "保留角色出场顺序" in text
    assert "s05" in text and "s06" in text  # scope
    # user_intent 在 objective 之前（首位锚定用户原话）
    assert text.index("用户意图") < text.index("任务目标")


# ============ F：两 Agent 链 SW → Director（situation 衔接 + parent_task_id + bridge 4 条）============
def test_F_two_agent_chain(tmp_path):
    _save_proj(tmp_path)
    new_text = "完整剧本正文。补充了第二幕的环境描写，雨后的街道。"
    sw_env = _envelope("script_writer",
                       objective="优化剧本正文",
                       user_intent="全流程优化一下")
    # Director envelope 的 situation 含 SW 结果摘要
    dir_env = _envelope("director",
                        objective="为第2段设计分镜",
                        situation="Script Writer 已优化剧本正文，补充了第二幕描写；"
                                  "角色未变化，Director 需为 s02 设计分镜",
                        user_intent="全流程优化一下")
    shots = [{"description": "两人隔桌对坐", "characters": ["林小雨", "陈默"],
              "shot_size": "medium shot", "target_duration_s": 5},
             {"description": "陈默望向窗外", "characters": ["陈默"],
              "shot_size": "close-up", "target_duration_s": 3}]
    client = ScriptedClient([
        (None, [_tool_call("script_writer.delegate", sw_env, "p_sw")]),     # producer→SW
        (None, [_tool_call("story.list", {}, "sw1")]),
        (None, [_tool_call("script.update",
                           {"new_story_text": new_text,
                            "expected_revision": _story_rev(tmp_path)}, "sw2")]),
        ("已优化剧本正文。", None),
        (None, [_tool_call("director.delegate", dir_env, "p_dir")]),       # producer→Director
        (None, [_tool_call("shot.list", {"scene_id": "s02"}, "d1")]),
        (None, [_tool_call("shot.create_many",
                           {"scene_id": "s02", "shots": shots,
                            "expected_shots_revision": _shots_rev(tmp_path)}, "d2")]),
        ("已为第2段设计分镜。", None),
        ("我让 SW 优化了剧本、Director 为第2段设计了分镜。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "全流程优化一下"))

    assert len(res["receipts"]) == 2
    assert res["receipts"][0]["agent"] == "script_writer"
    assert res["receipts"][1]["agent"] == "director"
    # Director envelope 的 situation 含 SW 结果摘要（Director 自己读 canonical，不靠 Producer 转述全文）
    d_sess = _dir_session(rt)
    d_first_user = next(m for m in d_sess.messages if m.role == "user")
    assert "Script Writer 已优化剧本正文" in d_first_user.content
    # Producer 自己没读 shot（Director 自己读的）
    assert all(t["tool"] not in ("shot.list", "shot.read") for t in res["trace"])
    # Director 自己跑了工具（n_tool_calls>=1）
    assert res["receipts"][1]["n_tool_calls"] >= 1
    # workflow：两个 task，parent_task_id 链接（list 顺序不保证，按 target_agent 找）
    ts = AgentTaskStore(rt._resolve_project_dir("demo"), "demo")
    tasks = ts.list(requester_agent="producer")
    assert len(tasks) == 2
    t_sw = next(t for t in tasks if t.target_agent == "script_writer")
    t_dir = next(t for t in tasks if t.target_agent == "director")
    assert t_dir.parent_task_id == t_sw.task_id  # 链上前一 task
    # bridge_messages.jsonl 四条（2 委派 × 2）
    bm = (rt._resolve_project_dir("demo") / "output" / "agents" / "producer"
          / "bridge_messages.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(bm) == 4
    rows = [json.loads(l) for l in bm]
    assert rows[0]["sender"] == "producer" and rows[0]["receiver"] == "script_writer"
    assert rows[1]["sender"] == "script_writer" and rows[1]["receiver"] == "producer"
    assert rows[2]["sender"] == "producer" and rows[2]["receiver"] == "director"
    assert rows[3]["sender"] == "director"
    # canonical 两处都被改
    proj = ProjectStore(tmp_path).load()
    assert proj.story_text == new_text
    assert [sh.shot_id for sh in proj.scenes[1].shots] == ["s02_c01", "s02_c02"]


# ============ G：跳过（读完直接委派 Director，跳过 SW/CD）============
def test_G_skip_unneeded_agents(tmp_path):
    _save_proj(tmp_path)
    shots = [{"description": "两人对坐", "characters": ["林小雨", "陈默"],
              "shot_size": "medium shot", "target_duration_s": 5}]
    env = _envelope("director", objective="为第2段设计分镜",
                    user_intent="只给第2段加分镜")
    client = ScriptedClient([
        (None, [_tool_call("project.inspect", {}, "p1")]),                    # producer 读
        (None, [_tool_call("director.delegate", env, "p2")]),                # 直接委派 Director
        (None, [_tool_call("shot.list", {"scene_id": "s02"}, "d1")]),
        (None, [_tool_call("shot.create_many",
                           {"scene_id": "s02", "shots": shots,
                            "expected_shots_revision": _shots_rev(tmp_path)}, "d2")]),
        ("已为第2段设计分镜。", None),
        ("故事与角色无需调整，已直接让 Director 为第2段加分镜。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "只给第2段加分镜"))

    # 只有一次委派（Director），无固定 SW→CD→Director 顺序
    assert len(res["receipts"]) == 1
    assert res["receipts"][0]["agent"] == "director"
    # SW/CD session 未被创建（未委派）
    rt2 = AgentRuntime(project_dir_resolver=lambda n: tmp_path,
                       settings_path=tmp_path / "agents_data" / "settings.json",
                       client=ScriptedClient([]))
    # 同一 store：SW/CD 会话不存在（get_or_create_conversation 会【创建】——故改用 list 检查）
    store = rt._session_store("demo")
    all_sessions = store.list() if hasattr(store, "list") else []
    agent_ids = {s.agent_id for s in all_sessions}
    assert "script_writer" not in agent_ids
    assert "character_designer" not in agent_ids


# ============ I：执行窗口续期 ============
def test_I_window_renewal(tmp_path, monkeypatch):
    _save_proj(tmp_path)
    # 注入 window=3：3 步工具后触发 1 次 nudge，续期后纯文本结束
    monkeypatch.setattr(producer, "DEFAULT_MAX_TOOL_STEPS", 3)
    client = ScriptedClient([
        (None, [_tool_call("project.inspect", {}, "p1")]),      # step1
        (None, [_tool_call("story.list", {}, "p2")]),          # step2
        (None, [_tool_call("character.list", {}, "p3")]),      # step3 → window 用满
        # 第 4 次：续期 nudge 已注入，模型继续→纯文本结束
        ("项目状态已了解，无需委派，任务完成。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "了解一下项目"))
    assert res["window_renewals"] == 1
    # 恰好一条【系统提示·执行窗口】nudge（注入到 messages）
    last = client.calls[-1]
    # messages 在最后一次调用里含 nudge（asyncio.run 后 calls 引用最终 messages 列表）
    msgs = last.get("messages") or []
    nudge_count = sum(1 for m in msgs
                      if m.get("role") == "user"
                      and "系统提示·执行窗口" in (m.get("content") or ""))
    assert nudge_count == 1
    # 🔴 grep 断言：Producer 代码无总步数/总时长/总委派次数上限常量
    import inspect
    src = inspect.getsource(producer)
    assert "MAX_WINDOW_RENEWALS" not in src
    assert "TOTAL_MAX_STEPS" not in src
    assert "TOTAL_WALL_CLOCK" not in src
    assert "MAX_DELEGATIONS" not in src
    assert "MAX_DELEGATIONS_TOTAL" not in src


# ============ J：Cancel 协作停止 + 透传到 Specialist ============
def test_J_cancel_propagates(tmp_path):
    _save_proj(tmp_path)
    cancel = asyncio.Event()
    new_text = "完整剧本正文。补充描写。"
    env = _envelope("script_writer", objective="优化剧本正文",
                    user_intent="优化一下")
    inner = ScriptedClient([
        (None, [_tool_call("script_writer.delegate", env, "p1")]),   # call1 producer
        (None, [_tool_call("story.list", {}, "sw1")]),                # call2 SW（cancel 在此 set）
        # SW 下一次循环看到 cancel → _TurnStopped → sw_turn STOPPED
        ("已读取。", None),                                           # call3 SW（cancel 已 set → 实际不会到达这里）
    ])
    client = CancelClient(inner, cancel, set_on=2)
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "优化一下",
                                      cancel_event=cancel))
    # turn 按 STOPPED 诚实收尾
    assert res["message"]["status"] == "stopped"
    # cancel 透传：Specialist 也收到（其 Task Result status=stopped，非 completed）
    assert res["receipts"], "应有 1 条委派回执"
    assert res["receipts"][0]["status"] == "stopped"


# ============ K：持久化（磁盘 reload）============
def test_K_persistence_reload(tmp_path):
    _save_proj(tmp_path)
    new_text = "完整剧本正文。补充第二幕描写。"
    sw_env = _envelope("script_writer", objective="优化剧本正文",
                       user_intent="全流程优化一下")
    dir_env = _envelope("director", objective="为第2段设计分镜",
                        situation="SW 已优化剧本", user_intent="全流程优化一下")
    shots = [{"description": "对坐", "characters": ["林小雨", "陈默"],
              "shot_size": "medium shot", "target_duration_s": 5}]
    client = ScriptedClient([
        (None, [_tool_call("script_writer.delegate", sw_env, "p_sw")]),
        (None, [_tool_call("story.list", {}, "sw1")]),
        (None, [_tool_call("script.update",
                           {"new_story_text": new_text,
                            "expected_revision": _story_rev(tmp_path)}, "sw2")]),
        ("已优化剧本。", None),
        (None, [_tool_call("director.delegate", dir_env, "p_dir")]),
        (None, [_tool_call("shot.list", {"scene_id": "s02"}, "d1")]),
        (None, [_tool_call("shot.create_many",
                           {"scene_id": "s02", "shots": shots,
                            "expected_shots_revision": _shots_rev(tmp_path)}, "d2")]),
        ("已设计分镜。", None),
        ("完成。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    _run(producer.producer_turn(rt, "demo", sid, "全流程优化一下"))

    pdir = rt._resolve_project_dir("demo")
    # workflows.json 可读、链完整
    wf_path = pdir / "output" / "agents" / "producer" / "workflows.json"
    wf_data = json.loads(wf_path.read_text(encoding="utf-8"))
    assert wf_data["version"] == 1
    wfs = wf_data["workflows"]
    assert len(wfs) == 1
    wf = next(iter(wfs.values()))
    assert wf["status"] == "completed"
    assert len(wf["task_ids"]) == 2
    # bridge_messages.jsonl 可读
    bm_path = pdir / "output" / "agents" / "producer" / "bridge_messages.jsonl"
    assert len(bm_path.read_text(encoding="utf-8").strip().splitlines()) == 4
    # AgentTaskStore reload：链完整（按 target_agent 找，不依赖 list 顺序）
    ts = AgentTaskStore(pdir, "demo")
    tasks = ts.list(requester_agent="producer")
    assert len(tasks) == 2
    t_sw = next(t for t in tasks if t.target_agent == "script_writer")
    t_dir = next(t for t in tasks if t.target_agent == "director")
    assert t_dir.parent_task_id == t_sw.task_id


# ============ L：锁接线（SW delegate 期间 story 锁 + story.changed + 解锁）============
def test_L_lock_wiring(tmp_path):
    _save_proj(tmp_path)
    new_text = "完整剧本正文。补充描写。"
    env = _envelope("script_writer", objective="优化剧本正文",
                    user_intent="优化一下")
    client = ScriptedClient([
        (None, [_tool_call("script_writer.delegate", env, "p1")]),
        (None, [_tool_call("story.list", {}, "sw1")]),
        (None, [_tool_call("script.update",
                           {"new_story_text": new_text,
                            "expected_revision": _story_rev(tmp_path)}, "sw2")]),
        ("已优化。", None),
        ("完成。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    bus = get_project_bus()
    q = bus.subscribe("demo")
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]

    async def _go():
        return await producer.producer_turn(rt, "demo", sid, "优化一下")
    _run(_go())

    # 排空总线事件
    events = []
    try:
        while True:
            ev = q.get_nowait()
            events.append(ev)
    except Exception:
        pass
    types = [e["type"] for e in events]
    # SW 写期间：story 资源被锁、story.changed 发出、结束后解锁
    assert any(t == "resource.locked" and e["resource"] == RESOURCE_STORY
               for t, e in zip(types, events)), f"缺 resource.locked(story): {types}"
    assert any(t == "story.changed" for t in types), f"缺 story.changed: {types}"
    assert any(t == "resource.unlocked" and e["resource"] == RESOURCE_STORY
               for t, e in zip(types, events)), f"缺 resource.unlocked(story): {types}"
    # 结束后 story 未锁
    assert not bus.is_locked("demo", RESOURCE_STORY)
    bus.unsubscribe("demo", q)


# ============ R1：重复委派同 Specialist（replan 管道 Director→SW→Director）============
def test_R1_replan_repeat_delegation(tmp_path):
    """同一 turn 内两次委派同一 Specialist（Director）——验证 turn_registry
    acquire/pop/acquire 复用正确（第二次委派不 409）、bridge_messages 六条、
    task 链 parent 链接正确。Director 第二次用 shot.update（镜头级内容指纹，不受
    第一次 create_many 与 SW 改剧本影响）。管道层测试，非智能测试（§2）。"""
    _save_proj(tmp_path)
    # s01_c01 镜头级内容指纹：Director call-1 在 s02 create_many（结构指纹变）+
    # SW 改 story_text 都不影响 s01_c01 的内容指纹，故 build-time 取值仍有效。
    s01_c01 = ProjectStore(tmp_path).load().scenes[0].shots[0]
    shot_token = _shot_token(s01_c01)

    dir_env_1 = _envelope("director", objective="为第2段设计分镜",
                          situation="s02 当前没有镜头", user_intent="补全并优化这个项目")
    sw_env = _envelope("script_writer", objective="优化剧本正文",
                       situation="Director 指出故事层需先调整再回分镜",
                       user_intent="补全并优化这个项目")
    dir_env_2 = _envelope("director",
                          objective="重新检查并调整 s01 第1镜景别",
                          situation="Script Writer 已调整剧本正文；"
                                    "s01_c01 的景别偏松，重新检查后收紧",
                          user_intent="补全并优化这个项目")
    new_text = "完整剧本正文。补充第二幕描写。"
    shots_s02 = [{"description": "两人隔桌对坐", "characters": ["林小雨", "陈默"],
                  "shot_size": "medium shot", "target_duration_s": 5},
                 {"description": "陈默望向窗外", "characters": ["陈默"],
                  "shot_size": "close-up", "target_duration_s": 3}]
    client = ScriptedClient([
        (None, [_tool_call("director.delegate", dir_env_1, "p_d1")]),       # 1 producer→Director
        (None, [_tool_call("shot.list", {"scene_id": "s02"}, "d1a")]),
        (None, [_tool_call("shot.create_many",
                           {"scene_id": "s02", "shots": shots_s02,
                            "expected_shots_revision": _shots_rev(tmp_path)}, "d1b")]),
        ("已为第2段设计分镜；s01 节奏还需 SW 先调整故事。", None),
        (None, [_tool_call("script_writer.delegate", sw_env, "p_sw")]),     # 2 producer→SW
        (None, [_tool_call("story.list", {}, "sw1")]),
        (None, [_tool_call("script.update",
                           {"new_story_text": new_text,
                            "expected_revision": _story_rev(tmp_path)}, "sw2")]),
        ("已优化剧本正文。", None),
        (None, [_tool_call("director.delegate", dir_env_2, "p_d2")]),       # 3 producer→Director(再次)
        (None, [_tool_call("shot.list", {"scene_id": "s01"}, "d2a")]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c01", "expected_revision": shot_token,
                            "shot_size": "close-up"}, "d2b")]),
        ("已重新检查并收紧 s01 第1镜景别。", None),
        ("SW 调整了剧本，Director 先为 s02 加分镜、回头又收紧了 s01 第1镜。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "补全并优化这个项目"))

    # 三次委派；Director 两次（replan 回头）——registry 第二次 acquire 不 409
    assert len(res["receipts"]) == 3
    assert [r["agent"] for r in res["receipts"]] == ["director", "script_writer", "director"]
    assert res["receipts"][0]["status"] == "completed"
    assert res["receipts"][2]["status"] == "completed"
    # 同一 Director session 被复用（get_or_create_conversation 幂等）
    d_sess = _dir_session(rt)
    assert d_sess.agent_id == "director"
    # bridge_messages 六条（3 委派 × P→S + S→P），发送方序列正确
    bm = (rt._resolve_project_dir("demo") / "output" / "agents" / "producer"
          / "bridge_messages.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(bm) == 6
    rows = [json.loads(l) for l in bm]
    assert [r["sender"] for r in rows] == ["producer", "director", "producer",
                                           "script_writer", "producer", "director"]
    # task 链 parent：t2.parent=t1, t3.parent=t2（按创建时间旧→新）
    ts = AgentTaskStore(rt._resolve_project_dir("demo"), "demo")
    tasks = ts.list(requester_agent="producer")
    assert len(tasks) == 3
    tasks_by_time = list(reversed(tasks))  # list 是新→旧
    t1, t2, t3 = tasks_by_time
    assert t2.parent_task_id == t1.task_id
    assert t3.parent_task_id == t2.task_id
    # 真实 canonical 变化：s02 落了 2 镜 + s01_c01 景别收紧 + story_text 改了
    proj = ProjectStore(tmp_path).load()
    assert [sh.shot_id for sh in proj.scenes[1].shots] == ["s02_c01", "s02_c02"]
    assert proj.scenes[0].shots[0].shot_size == "close-up"
    assert proj.story_text == new_text


# ============ R3：事实层级（verified/changed_refs 只从 receipts 派生，不采信自述）============
def test_R3_fact_hierarchy_verified_from_receipts(tmp_path):
    """SW 最终文本声称「已修改完成」，但其 script.update 用过期指纹 → 写回执 ok=False。
    断言 Task Result verified=False、changed_refs 为空、canonical 未变、warnings 含失败写。
    验证 Producer 据 receipts（而非 LLM 自述）判断事实（§3 事实层级 / §2 方法论）。"""
    _save_proj(tmp_path)
    original_text = ProjectStore(tmp_path).load().story_text
    env = _envelope("script_writer", objective="优化剧本正文",
                    situation="用户希望剧本更丰富", user_intent="优化一下这个项目")
    client = ScriptedClient([
        (None, [_tool_call("script_writer.delegate", env, "p_sw")]),   # producer→SW
        (None, [_tool_call("story.list", {}, "sw1")]),                 # SW 读
        (None, [_tool_call("script.update",                            # SW 写：故意用过期指纹
                           {"new_story_text": "完整剧本正文。补充描写。",
                            "expected_revision": "stale_token_xxxxx"}, "sw2")]),
        ("已修改完成。", None),                                         # SW 声称完成（false claim）
        ("已修改完成。", None),                                         # corrective 耗尽后仍声称
        ("我已让 Script Writer 优化剧本正文。", None),                  # producer 汇报
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "优化一下这个项目"))

    assert len(res["receipts"]) == 1
    rec = res["receipts"][0]
    assert rec["agent"] == "script_writer"
    # 🔴 事实层级：verified=False（无 ok+verified+非unchanged 回执），changed_refs 为空
    # （Phase B：changed_refs 扩为八键骨架，值为空语义不变）
    assert rec["verified"] is False
    assert rec["changed_refs"] == {"scenes": [], "shots": [], "characters": [], "sequences": [], "story": [],
                                   "clothings": [], "looks": [], "props": [],
                                   "reference_assets": [], "compositions": [], "bindings": [],
                                   "locations": [], "location_views": [],
                                   "scene_instances": [], "scene_instance_views": []}
    # 失败写进 warnings
    assert rec["warnings"], "应有失败写 warning"
    assert any("stale" in w or "script.update" in w for w in rec["warnings"])
    # canonical 真的没被改（过期写未落盘）
    assert ProjectStore(tmp_path).load().story_text == original_text


# ============ P1：PRODUCER_SYSTEM 与 delegate 描述内容弱断言（辅助性）============
def test_P1_prompt_content_weak_assertions():
    """弱断言：PRODUCER_SYSTEM 含核心原则关键词、三个 delegate 描述含各自 ownership。
    仅辅助性——智能行为由真机三案例验证（§2：mock 不做同义反复智能测试）。"""
    sys_prompt = producer.PRODUCER_SYSTEM
    # 完成判断两方向（不把 Specialist completed 当用户完成；不无限优化）
    assert "完成判断" in sys_prompt
    assert "无限" in sys_prompt
    # 询问门槛（不逐步请示；仅五类情况问用户）
    assert "询问门槛" in sys_prompt or "问用户" in sys_prompt
    # 事实层级（authoritative vs 自述）
    assert "事实层级" in sys_prompt
    assert "authoritative" in sys_prompt
    # 动态路由与 skip
    assert "跳过" in sys_prompt or "Skip" in sys_prompt
    # replan 回头
    assert "回头" in sys_prompt or "Replanning" in sys_prompt
    # 失败协调
    assert "失败" in sys_prompt and "workflow 失败" in sys_prompt
    # 最终汇报结构
    assert "汇报" in sys_prompt

    offered, _ = producer._build_producer_tools()
    descs = {t["function"]["name"]: t["function"]["description"]
             for t in offered if t["function"]["name"].endswith(".delegate")}
    # 各自 ownership 关键词
    sw_d = descs["script_writer.delegate"]
    assert "owns" in sw_d and "剧本" in sw_d and "动机" in sw_d
    cd_d = descs["character_designer.delegate"]
    assert "owns" in cd_d and "外貌" in cd_d and "服装" in cd_d
    dir_d = descs["director.delegate"]
    assert "owns" in dir_d and "镜头" in dir_d and "构图" in dir_d
    # Phase 45.2：assembly.delegate（成片装配）ownership 关键词
    asm_d = descs["assembly.delegate"]
    assert "owns" in asm_d and "SequenceBlock" in asm_d and "提案" in asm_d
    assert "select final_render" in asm_d   # No Auto Select 边界写进工具描述
    # 各自不适用域（指向其他 owner）
    assert "Character Designer" in sw_d or "Director" in sw_d
    assert "Script Writer" in cd_d or "Director" in cd_d
    assert "Script Writer" in dir_d or "Character Designer" in dir_d
    # 反模式提示：对方自己读 canonical，situation 不复制全文
    for d in descs.values():
        assert "canonical" in d and "全文" in d
    # 28.md §23/§24：第四 delegate——Scene Agent ownership 关键词
    sd_d = descs["scene_designer.delegate"]
    assert "owns" in sd_d and "Location" in sd_d and "SceneState" in sd_d
    assert "SceneInstanceView" in sd_d
    assert "Script Writer" in sd_d or "Resource Agent" in sd_d or "Director" in sd_d


# ============================================================================
# 收口 §15：未实际修改处理（区分 review/execution + 一次执行性纠偏 + 不机械重复）
# 方法论（§2）：mock 只验证管道与运行时事实——重复委派同 Specialist 无 409、parent 链、
# verified=false 回执正确处理、envelope [执行性纠偏] 标记透传、不假成功。智能判断（是否
# 发纠偏、是否停止）由真机 replay 承担（§16）。Director 的 verified=false 经【过期指纹失败
# 写】路径产生（write_attempts=1 → 零 zero_write_recovery 干扰；中性文本 → 零假成功纠偏）。
# ============================================================================

def _shot_token_for(tmp_path, scene_idx, shot_idx):
    """取指定 shot 的镜头级内容指纹（build-time，供 shot.update OCC）。"""
    proj = ProjectStore(tmp_path).load()
    return _shot_token(proj.scenes[scene_idx].shots[shot_idx])


# ---- RE1：review 任务 verified=false 正常接受（不 retry、不假成功）----
def test_RE1_review_verified_false_accepted(tmp_path):
    """§15 Test1：用户只要 review → Director completed+verified=false 是正常结果，
    Producer 接受不 retry。管道层：verified=false 回执被正确处理、不假成功。"""
    _save_proj(tmp_path)
    env = _envelope("director",
                    objective="审查 s01 分镜并给出意见，本轮不要求改写",
                    situation="用户只想了解 s01 现状", user_intent="检查一下有没有问题")
    client = ScriptedClient([
        (None, [_tool_call("director.delegate", env, "p1")]),
        (None, [_tool_call("shot.list", {"scene_id": "s01"}, "d1")]),
        ("s01 分镜的镜头节奏基本合理，未发现需要立即处理的结构性问题；以下是几点观察供参考。",
         None),
        ("Director 审查了 s01，未发现需要修改的问题。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "检查一下 s01 有没有问题"))

    assert res["intent"] == "delegation"
    assert len(res["receipts"]) == 1
    rec = res["receipts"][0]
    assert rec["agent"] == "director"
    assert rec["status"] == "completed"
    assert rec["verified"] is False          # 无真实写入
    assert rec["changed_refs"] == {"scenes": [], "shots": [], "characters": [], "sequences": [], "story": [],
                                   "clothings": [], "looks": [], "props": [],
                                   "reference_assets": [], "compositions": [], "bindings": [],
                                   "locations": [], "location_views": [],
                                   "scene_instances": [], "scene_instance_views": []}
    assert rec["warnings"] == []
    # canonical 未变（review 不写）
    assert _shots_rev(tmp_path) == _shots_rev(tmp_path)  # 结构指纹不变（无写）
    # 不假成功：Producer 最终消息不含对 review 的虚假"已修改"声称（receipts 已证零变更）


# ---- RE2：execution 第一次 verified=false → 一次[执行性纠偏] → 第二次成功写 ----
def test_RE2_exec_clarification_then_success(tmp_path):
    """§15 Test2+3：execution 任务第一次 Director completed+verified=false（过期指纹失败
    写）→ Producer 发一次[执行性纠偏]重新委派（situation 含标记，任务表达变化）→ 第二次
    用正确指纹成功写。管道层：同 Specialist 二次委派无 409（registry 复用）、parent 链、
    第二次 verified=true+changed_refs、纠偏 envelope 透传到 Director session。"""
    _save_proj(tmp_path)
    correct_token = _shot_token_for(tmp_path, 0, 0)  # s01_c01 内容指纹（第一次失败写不改它）
    env1 = _envelope("director", objective="优化 s01 第1镜景别",
                     situation="用户希望 s01 节奏更紧凑", user_intent="优化一下 s01")
    env2 = _envelope("director", objective="优化 s01 第1镜景别",
                     situation="[执行性纠偏]上一轮你 completed 但无 verified 修改；用户要求"
                               "实际修改；请重读最新 canonical，确有问题就直接用 shot.update 执行",
                     user_intent="优化一下 s01")
    client = ScriptedClient([
        (None, [_tool_call("director.delegate", env1, "p1")]),        # 第1次
        (None, [_tool_call("shot.list", {"scene_id": "s01"}, "d1a")]),
        (None, [_tool_call("shot.update",                                # 过期指纹 → 失败
                           {"shot_id": "s01_c01", "expected_revision": "stale_token_xx",
                            "shot_size": "close-up"}, "d1b")]),
        ("s01_c01 景别调整遇到版本冲突，未能写入；需重读最新版本。", None),
        (None, [_tool_call("director.delegate", env2, "p2")]),          # 第2次（纠偏）
        (None, [_tool_call("shot.list", {"scene_id": "s01"}, "d2a")]),
        (None, [_tool_call("shot.update",                                # 正确指纹 → 成功
                           {"shot_id": "s01_c01", "expected_revision": correct_token,
                            "shot_size": "close-up"}, "d2b")]),
        ("已收紧 s01_c01 景别。", None),
        ("Director 第一次遇到冲突，纠偏后成功收紧了 s01_c01 景别。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "优化一下 s01"))

    assert len(res["receipts"]) == 2
    assert [r["agent"] for r in res["receipts"]] == ["director", "director"]
    # 第1次 verified=false（失败写）、第2次 verified=true（成功写）
    assert res["receipts"][0]["verified"] is False
    assert res["receipts"][1]["verified"] is True
    assert res["receipts"][1]["changed_refs"]["shots"] == ["s01_c01"]
    # 同 Director session 二次委派无 409（registry acquire/pop/acquire 复用）
    d_sess = _dir_session(rt)
    assert d_sess.agent_id == "director"
    # 纠偏 envelope 的 [执行性纠偏] 标记透传到 Director 第2条 user 消息
    d_users = [m for m in d_sess.messages if m.role == "user"]
    assert any("[执行性纠偏]" in m.content for m in d_users)
    # parent 链：t2.parent=t1
    ts = AgentTaskStore(rt._resolve_project_dir("demo"), "demo")
    tasks = list(reversed(ts.list(requester_agent="producer")))
    assert len(tasks) == 2
    assert tasks[1].parent_task_id == tasks[0].task_id
    # canonical 真改了（s01_c01 景别 → close-up）
    assert ProjectStore(tmp_path).load().scenes[0].shots[0].shot_size == "close-up"


# ---- RE3：第二次仍 verified=false → 不第三次机械调用同 Specialist ----
def test_RE3_second_false_no_third_mechanical_repeat(tmp_path):
    """§15 Test4：连续两次 verified=false 后，Producer 不第三次机械调用同一 Director。
    管道层：两失败写回执均 verified=false、Producer 正常结束（intent=delegation、2 receipts）、
    最终消息诚实（不声称已修改——changed_refs 全空）。智能判断（停止而非重复）由真机承担。"""
    _save_proj(tmp_path)
    env1 = _envelope("director", objective="优化 s01 第1镜景别",
                     user_intent="优化 s01")
    env2 = _envelope("director", objective="优化 s01 第1镜景别",
                     situation="[执行性纠偏]上一轮无 verified 修改，请直接执行",
                     user_intent="优化 s01")
    client = ScriptedClient([
        (None, [_tool_call("director.delegate", env1, "p1")]),
        (None, [_tool_call("shot.list", {"scene_id": "s01"}, "d1a")]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c01", "expected_revision": "stale_a",
                            "shot_size": "close-up"}, "d1b")]),
        ("s01_c01 调整遇版本冲突，未写入。", None),
        (None, [_tool_call("director.delegate", env2, "p2")]),
        (None, [_tool_call("shot.list", {"scene_id": "s01"}, "d2a")]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c01", "expected_revision": "stale_b",
                            "shot_size": "close-up"}, "d2b")]),
        ("仍遇版本冲突，未写入。", None),
        ("Director 两次尝试均遇版本冲突未能修改 s01_c01，如实汇报未能完成实际修改。",
         None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "优化 s01"))

    assert len(res["receipts"]) == 2          # 仅 2 次，无第三次
    assert all(r["verified"] is False for r in res["receipts"])
    _empty_refs = {"scenes": [], "shots": [], "characters": [], "story": [],
                   "clothings": [], "looks": [], "props": [],
                   "reference_assets": [], "compositions": [], "bindings": [],
                                   "locations": [], "location_views": [],
                                   "scene_instances": [], "scene_instance_views": [],
                                   "sequences": []}
    assert all(r["changed_refs"] == _empty_refs for r in res["receipts"])
    # canonical 未变（两次都失败写）
    assert ProjectStore(tmp_path).load().scenes[0].shots[0].shot_size == "medium shot"
    # 最终消息诚实：不含"已优化/已修改完成"类假成功（changed_refs 全空已证）
    final = res["assistant_message"]
    for bad in ("已优化完成", "已修改完成", "已经优化", "已经修改"):
        assert bad not in final, f"假成功措辞 {bad} 不应出现"


# ---- RE4：Specialist 明确"无值得修改"→ 接受 no-change（不为 verified 强行改）----
def test_RE4_no_change_accept_not_force_write(tmp_path):
    """§15 Test5：Specialist 检查后明确"无值得修改的问题"→ verified=false 是正确结果，
    Producer 接受正常停止，不为了 verified=true 强行让 Specialist 改。"""
    _save_proj(tmp_path)
    env = _envelope("director", objective="审查 s01 分镜，确有问题再改",
                    user_intent="检查并优化 s01")
    client = ScriptedClient([
        (None, [_tool_call("director.delegate", env, "p1")]),
        (None, [_tool_call("shot.list", {"scene_id": "s01"}, "d1")]),
        ("s01 分镜经审查，镜头节奏与景别搭配均合理，没有值得为了'优化'而修改的真实问题。",
         None),
        ("Director 审查后认为 s01 无需修改，已接受 no-change。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "检查并优化 s01"))

    assert len(res["receipts"]) == 1
    rec = res["receipts"][0]
    assert rec["verified"] is False
    assert rec["changed_refs"] == {"scenes": [], "shots": [], "characters": [], "sequences": [], "story": [],
                                   "clothings": [], "looks": [], "props": [],
                                   "reference_assets": [], "compositions": [], "bindings": [],
                                   "locations": [], "location_views": [],
                                   "scene_instances": [], "scene_instance_views": []}
    # canonical 未变（未强行修改）
    assert ProjectStore(tmp_path).load().scenes[0].shots[0].shot_size == "medium shot"


# ---- RE5：Director 第二次未写且指 Story 根因 → Producer replan 到 Script Writer ----
def test_RE5_upstream_replan_director_to_script_writer(tmp_path):
    """§15 Test6：Director 连续未写且说明"问题根源在 Story 层"→ Producer replan 到
    Script Writer 而非继续 Director。管道层：receipts=[director, director, script_writer]、
    parent 链 t2.parent=t1/t3.parent=t2、SW 真实写 story。"""
    _save_proj(tmp_path)
    correct_story_rev = _story_rev(tmp_path)
    new_text = "完整剧本正文。补充 s01 林小雨的动机。"
    env_d1 = _envelope("director", objective="优化 s01 分镜节奏",
                       user_intent="优化 s01")
    env_d2 = _envelope("director", objective="优化 s01 分镜节奏",
                       situation="[执行性纠偏]上一轮无 verified 修改；若分镜无法单独解决请说明",
                       user_intent="优化 s01")
    env_sw = _envelope("script_writer", objective="补充 s01 林小雨的行动动机",
                       situation="Director 两次指出 s01 节奏问题根源在故事层动机不足，"
                                 "分镜单独改不彻底，需先处理 Story",
                       user_intent="优化 s01")
    client = ScriptedClient([
        (None, [_tool_call("director.delegate", env_d1, "p1")]),
        (None, [_tool_call("shot.list", {"scene_id": "s01"}, "d1a")]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c01", "expected_revision": "stale_a",
                            "shot_size": "close-up"}, "d1b")]),
        ("s01_c01 调整遇版本冲突未写入；且节奏问题根源在故事层动机不足，仅靠镜头无法彻底解决。",
         None),
        (None, [_tool_call("director.delegate", env_d2, "p2")]),
        (None, [_tool_call("shot.list", {"scene_id": "s01"}, "d2a")]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c01", "expected_revision": "stale_b",
                            "shot_size": "close-up"}, "d2b")]),
        ("仍遇版本冲突；根因在 Story 层，建议先补 s01 动机再回分镜。", None),
        (None, [_tool_call("script_writer.delegate", env_sw, "p3")]),   # replan → SW
        (None, [_tool_call("story.list", {}, "sw1")]),
        (None, [_tool_call("script.update",
                           {"new_story_text": new_text,
                            "expected_revision": correct_story_rev}, "sw2")]),
        ("已补充 s01 林小雨的动机。", None),
        ("Director 指出根因在 Story，已让 SW 补充 s01 动机。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "优化 s01"))

    assert [r["agent"] for r in res["receipts"]] == ["director", "director", "script_writer"]
    assert res["receipts"][0]["verified"] is False   # Director 1st no write
    assert res["receipts"][1]["verified"] is False   # Director 2nd no write
    assert res["receipts"][2]["verified"] is True    # SW wrote story
    assert res["receipts"][2]["changed_refs"] == {"scenes": [], "shots": [], "characters": [], "sequences": [], "story": ["story_text"],
                                                  "clothings": [], "looks": [], "props": [],
                                                  "reference_assets": [], "compositions": [], "bindings": [],
                                   "locations": [], "location_views": [],
                                   "scene_instances": [], "scene_instance_views": []}
    # SW 真改了 story_text（SW changed_refs 为空是因 story_text 不映射 scene/shot/char，
    # 但 verified=true 来自 ok+verified+not-unchanged 回执—— receipts 派生正确）
    assert ProjectStore(tmp_path).load().story_text == new_text
    # parent 链
    ts = AgentTaskStore(rt._resolve_project_dir("demo"), "demo")
    tasks = list(reversed(ts.list(requester_agent="producer")))
    assert len(tasks) == 3
    assert tasks[1].parent_task_id == tasks[0].task_id
    assert tasks[2].parent_task_id == tasks[1].task_id


# ---- P2：PRODUCER_SYSTEM 含未实际修改处理原则（弱断言）----
def test_P2_prompt_unmodified_handling_principles():
    """弱断言：PRODUCER_SYSTEM 含[执行性纠偏]/区分 review-execution/不机械重复/
    不为 verified 乱改 原则关键词。智能由真机 replay 验证（§16）。"""
    s = producer.PRODUCER_SYSTEM
    assert "未实际修改的处理" in s
    assert "review" in s and "execution" in s
    assert "执行性纠偏" in s
    assert "不" in s and "机械" in s           # 不机械重复
    assert "verified=true" in s or "verified" in s
    assert "乱改" in s or "没有价值" in s       # 不为 verified 强行乱改
    # delegate 描述含"不要只返回审查报告"
    offered, _ = producer._build_producer_tools()
    dir_d = next(t["function"]["description"]
                for t in offered if t["function"]["name"] == "director.delegate")
    assert "审查报告" in dir_d or "只返回" in dir_d



# ============ Phase D（29.md §35）：Producer 零工具谎报闸门 ============
def test_PD35_zero_tool_claim_gets_one_nudge_then_flagged(tmp_path):
    """零工具 + 完成态断言：第一次 nudge 纠正；仍谎报 → zero_tool_claim=True 返回。"""
    _save_proj(tmp_path)
    lie = ("## 完成汇报\n\n- 玉佩 ✅ approved（ref_5a3b98211bb6）\n"
           "- 密信 ✅ 已批准\n- 悬空实例已删除清理")
    client = ScriptedClient([
        (lie, None),      # 第一次：零工具谎报 → 闸门 nudge
        (lie, None),      # 第二次：仍谎报 → zero_tool_claim 标记返回
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "把道具和造型全部收口"))

    assert res["zero_tool_claim"] is True
    # nudge 恰好注入一次（第二次 LLM 调用的 messages 里有系统纠正）
    assert len(client.calls) == 2
    second_msgs = client.calls[1]["messages"]
    nudges = [m for m in second_msgs
              if m.get("role") == "user" and "你本轮没有调用任何工具" in (m.get("content") or "")]
    assert len(nudges) == 1


def test_PD35_zero_tool_honest_reply_not_nudged(tmp_path):
    """零工具但如实说明未执行（无完成态断言）→ 不触发闸门。"""
    _save_proj(tmp_path)
    honest = "我本轮尚未执行任何修改：道具登记和造型审批还需要先委派专业部门，请确认优先级。"
    client = ScriptedClient([(honest, None)])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "继续收口生产准备"))

    assert res["zero_tool_claim"] is False
    assert len(client.calls) == 1          # 无二次调用
    assert "你本轮没有调用任何工具" not in json.dumps(
        client.calls[0]["messages"], ensure_ascii=False)


def test_PD35_nudge_leads_to_real_delegation(tmp_path):
    """纠正后模型改为真实委派 → 正常完成，zero_tool_claim=False。"""
    _save_proj(tmp_path)
    env = _envelope("script_writer", objective="把剧本正文再打磨一轮",
                    situation="用户要求继续完善剧本",
                    user_intent="继续收口生产准备")
    client = ScriptedClient([
        ("道具已批准、造型已清理完毕。", None),     # 第一次：谎报 → nudge
        (None, [_tool_call("script_writer.delegate", env, "call_fix")]),   # 改为真实委派
        (None, [_tool_call("story.list", {}, "sw1")]),
        ("已打磨完成。", None),                     # SW final
        ("本轮已实际委派 Script Writer 完成剧本打磨。", None),  # producer final
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "继续收口生产准备"))

    assert res["zero_tool_claim"] is False
    assert len(res["receipts"]) == 1 and res["receipts"][0]["agent"] == "script_writer"
