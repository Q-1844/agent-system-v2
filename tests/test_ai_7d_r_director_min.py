"""AI-7D-R · Director 最小 agent 循环测试（隔离，一个工具 add_scenes_from_script）。

不联网（FakeClient）。验证：
  - 工具调用 → 新增 Scene 追加到 project.scenes（add-only，不碰已有）+ scene_id 自增 + 审计 + 工作记忆。
  - 对话回复 → 不落盘。
  - 空 scenes → 失败不写。
  - 已有 scene 不被重复/修改。
  - 权限：非 director 拒绝。
"""
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.director_min import director_turn, _parse_tool_call
from src.agents.revision_audit import DirectEditAuditStore


def _proj():
    return Project(
        name="demo", style=StyleAnchor(), story_text="林夏走进雨里。她抬起头，想起多年前的承诺。后来，雨停了，人没回来。",
        characters=[Character(name="LinXia", description_en="a young woman")],
        scenes=[Scene(scene_id="s01", summary="rain", characters=["LinXia"], shots=[])])


def _save_proj(tmp_path):
    ProjectStore(tmp_path).save(_proj())
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



class FakeClient:
    """测试用：第一次返回固定 reply；若多步循环把工具结果喂回，则第二次返回
    一句自然语言"完成"，模拟真实 LLM 拿到工具结果后结束本轮。需要定制后续回复
    的测试用 SequenceClient。"""
    def __init__(self, reply, followup="完成。"):
        self._reply = reply
        self._followup = followup
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        if self.calls == 1:
            return self._reply
        # 后续调用：若上一条是工具结果，返回自然语言结束
        last = messages[-1].get("content", "") if messages else ""
        if isinstance(last, str) and last.startswith("[工具"):
            return self._followup
        return self._followup


class SequenceClient:
    """按顺序返回一组 reply；每次 chat 取下一个，用完后返回最后一个。
    用于多步工具循环：第一次给工具调用，第二次给自然语言回答。"""
    def __init__(self, *replies):
        self._replies = list(replies)
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        if self.calls - 1 < len(self._replies):
            return self._replies[self.calls - 1]
        return self._replies[-1]


def _run(coro):
    return asyncio.run(coro)


# ============ 工具调用解析 ============
def test_parse_tool_call_basic():
    tc = _parse_tool_call('{"tool":"add_scenes_from_script","scenes":[{"summary":"雨中回忆","characters":["LinXia"]}]}')
    assert tc and tc["tool"] == "add_scenes_from_script"
    assert len(tc["scenes"]) == 1


def test_parse_tool_call_none_for_prose():
    assert _parse_tool_call("好的，我看看剧本。") is None
    assert _parse_tool_call("") is None


def test_parse_tool_call_double_brace_fallback():
    """LLM 照抄双花括号示例 {{...}} 时，折叠后仍能解析。"""
    raw = '{{"tool": "add_scenes_from_script", "scenes": [{{"summary": "x", "characters": []}}]}}'
    tc = _parse_tool_call(raw)
    assert tc and tc["tool"] == "add_scenes_from_script" and len(tc["scenes"]) == 1


def test_parse_tool_call_with_trailing_disclaimer():
    """LLM 在 JSON 后附了'我无法写入'之类散文时，仍能取出 JSON 并执行。"""
    raw = ('{"tool": "add_scenes_from_script", "scenes": [{"summary": "雨停了", "characters": ["LinXia"]}]}'
           '\n\n我无法真正写入你的项目，这只是格式示例。')
    tc = _parse_tool_call(raw)
    assert tc and tc["scenes"][0]["summary"] == "雨停了"


# ============ 工具调用 → 新增 scene 追加（add-only）+ 自增 + 审计 ============
def test_tool_call_adds_scenes(tmp_path):
    _save_proj(tmp_path)
    reply = json.dumps({"tool": "add_scenes_from_script", "scenes": [
        {"summary": "雨中回忆：林夏想起多年前的承诺", "characters": ["LinXia"]},
        {"summary": "雨停之后：人没回来，她释然", "characters": ["LinXia"]},
    ]}, ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    before_scenes = len(ProjectStore(tmp_path).load().scenes)
    res = _run(director_turn(rt, "demo", sid, "把剧本拆分成几个分镜故事段"))
    assert res["edited"] is True
    assert res["added_scenes"] and len(res["added_scenes"]) == 2
    proj = ProjectStore(tmp_path).load()
    assert len(proj.scenes) == before_scenes + 2   # 追加，未删已有
    assert proj.scenes[0].scene_id == "s01"   # 已有 scene 未变
    new_ids = [s["scene_id"] for s in res["added_scenes"]]
    assert new_ids == ["s02", "s03"]   # next_scene_seq 自增
    assert proj.scenes[-1].summary.startswith("雨停之后")
    # 审计 + 工作记忆
    audit = DirectEditAuditStore(tmp_path, "demo").recent()
    assert audit and audit[-1]["after"] == new_ids
    wm = rt.work_memory("demo").recent()
    assert any("故事段" in e["summary"] for e in wm)


def test_add_only_does_not_touch_existing_scenes(tmp_path):
    """add-only：已有 scene 的 summary/characters/shots 不被改动。"""
    _save_proj(tmp_path)
    reply = json.dumps({"tool": "add_scenes_from_script", "scenes": [
        {"summary": "新段", "characters": ["LinXia"]}]}, ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    _run(director_turn(rt, "demo", sid, "加一段"))
    proj = ProjectStore(tmp_path).load()
    assert proj.scenes[0].scene_id == "s01"
    assert proj.scenes[0].summary == "rain"   # 未被改


# ============ 对话回复 → 不落盘 ============
def test_conversational_reply_no_mutation(tmp_path):
    _save_proj(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    rt = _runtime(tmp_path, client=FakeClient("好的，你想拆几段？"))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "你能帮我拆剧本吗"))
    assert res["edited"] is False
    assert (tmp_path / "output" / "project.json").read_bytes() == before


# ============ 空 scenes → 失败，不写 ============
def test_empty_scenes_fails_no_mutation(tmp_path):
    _save_proj(tmp_path)
    before_count = len(ProjectStore(tmp_path).load().scenes)
    rt = _runtime(tmp_path, client=FakeClient('{"tool":"add_scenes_from_script","scenes":[]}'))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "拆剧本"))
    assert res["edited"] is False
    assert "操作失败" in res["assistant_message"]
    assert len(ProjectStore(tmp_path).load().scenes) == before_count


# ============ 权限：非 director 拒绝 ============
def test_non_director_rejected(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient("ok"))
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    try:
        _run(director_turn(rt, "demo", sid, "拆剧本"))
        assert False, "应拒绝"
    except AgentError as e:
        assert e.code == "permission_denied"


# ============ 未配置 Agnes → not_configured ============
def test_not_configured_soft_fail(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=None,
                  agnes_getter=_raise_unconfigured,
                  sensenova_getter=lambda: None)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "拆剧本"))
    assert res["edited"] is False
    assert "未配置" in res["assistant_message"]


# ============ 工具 ② add_shots_to_scene：拆分尚无镜头的故事段 ============
def test_add_shots_to_scene_splits_empty_scene(tmp_path):
    _save_proj(tmp_path)   # s01 has no shots
    reply = json.dumps({"tool": "add_shots_to_scene", "scene_id": "s01", "shots": [
        {"description": "林夏望向窗外", "characters": ["LinXia"], "target_duration_s": 3},
        {"description": "雨滴滑落玻璃特写", "characters": [], "target_duration_s": 5},
    ]}, ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "把 s01 拆成镜头"))
    assert res["edited"] is True
    assert res["added_shots"] and len(res["added_shots"]) == 2
    proj = ProjectStore(tmp_path).load()
    sc = next(s for s in proj.scenes if s.scene_id == "s01")
    assert len(sc.shots) == 2
    assert sc.shots[0].shot_id == "s01_c01"
    assert sc.shots[1].shot_id == "s01_c02"
    assert sc.shots[0].index == 1 and sc.shots[1].index == 2
    assert sc.shots[0].description == "林夏望向窗外"
    assert sc.shots[1].target_duration_s == 5
    assert sc.shots[0].first_frame is not None  # 生成态字段默认存在（未生成）


def test_add_shots_to_nonempty_scene_appends_with_correct_index(tmp_path):
    """解锁：已有镜头的段可继续追加；新镜头 index 接在后面，shot_id 不碰撞。"""
    _save_proj(tmp_path)
    p = ProjectStore(tmp_path).load()
    p.scenes[0].shots.append(Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                   description="existing", characters=["LinXia"],
                                   target_duration_s=4))
    ProjectStore(tmp_path).save(p)
    reply = json.dumps({"tool": "add_shots_to_scene", "scene_id": "s01", "shots": [
        {"description": "新镜头A", "characters": ["LinXia"], "target_duration_s": 3},
        {"description": "新镜头B", "characters": [], "target_duration_s": 5}]},
        ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "给 s01 再加两个镜头"))
    assert res["edited"] is True
    sc = ProjectStore(tmp_path).load().scenes[0]
    assert [s.shot_id for s in sc.shots] == ["s01_c01", "s01_c02", "s01_c03"]
    assert [s.index for s in sc.shots] == [1, 2, 3]   # 接在已有镜头后，不是 1,2 重来


def test_add_shots_unknown_scene_refused(tmp_path):
    _save_proj(tmp_path)
    reply = json.dumps({"tool": "add_shots_to_scene", "scene_id": "s99", "shots": [
        {"description": "x", "characters": [], "target_duration_s": 3}]}, ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "拆 s99"))
    assert res["edited"] is False
    assert "操作失败" in res["assistant_message"]


# ============ 工具③④ delete_scene / delete_shot：两阶段确认 + 级联 ============
def _save_proj_with_shots(tmp_path):
    """s01: c01←c02←c03（接力链）；s02: c01 独立。"""
    proj = Project(
        name="demo", style=StyleAnchor(), story_text="雨",
        characters=[Character(name="LinXia", description_en="x")],
        scenes=[
            Scene(scene_id="s01", summary="雨中", characters=["LinXia"], shots=[
                Shot(shot_id="s01_c01", scene_id="s01", index=1, description="a",
                     characters=["LinXia"], target_duration_s=4),
                Shot(shot_id="s01_c02", scene_id="s01", index=2, description="b",
                     characters=["LinXia"], target_duration_s=4, source_shot_id="s01_c01"),
                Shot(shot_id="s01_c03", scene_id="s01", index=3, description="c",
                     characters=["LinXia"], target_duration_s=4, source_shot_id="s01_c02"),
            ]),
            Scene(scene_id="s02", summary="雨后", characters=["LinXia"], shots=[
                Shot(shot_id="s02_c01", scene_id="s02", index=1, description="d",
                     characters=["LinXia"], target_duration_s=4)]),
        ])
    ProjectStore(tmp_path).save(proj)
    return tmp_path


def test_parse_delete_tools():
    tc = _parse_tool_call('{"tool":"delete_scene","scene_id":"s01","confirmed":false}')
    assert tc and tc["tool"] == "delete_scene" and tc["confirmed"] is False
    tc2 = _parse_tool_call('{"tool":"delete_shot","shot_id":"s01_c02","confirmed":true}')
    assert tc2 and tc2["tool"] == "delete_shot" and tc2["confirmed"] is True


def test_delete_scene_two_phase(tmp_path):
    _save_proj_with_shots(tmp_path)
    sid = _runtime(tmp_path).get_or_create_session("demo", "director", "project", "demo")["session_id"]

    # 第一轮：confirmed=false → 预览，不落盘（多步循环：第二次返回自然语言结束）
    reply_false = json.dumps({"tool": "delete_scene", "scene_id": "s01",
                              "confirmed": False}, ensure_ascii=False)
    # 第二次 LLM 把依赖说明转述给用户（含确认问句）
    rt = _runtime(tmp_path, client=SequenceClient(
        reply_false, "将连带删除 s01 的镜头和产物，确定删除吗？"))
    before = (tmp_path / "output" / "project.json").read_bytes()
    res = _run(director_turn(rt, "demo", sid, "删除故事段 s01"))
    assert res["edited"] is False
    assert res["deleted_preview"] is not None
    assert "确定删除" in res["assistant_message"]
    assert (tmp_path / "output" / "project.json").read_bytes() == before   # 未写盘
    assert any(s.scene_id == "s01" for s in ProjectStore(tmp_path).load().scenes)
    audit = DirectEditAuditStore(tmp_path, "demo").recent()
    assert not any("delete" in (a.get("field") or "") for a in audit)       # 未审计

    # 第二轮：confirmed=true → 真正删除
    reply_true = json.dumps({"tool": "delete_scene", "scene_id": "s01",
                             "confirmed": True}, ensure_ascii=False)
    rt2 = _runtime(tmp_path, client=FakeClient(reply_true))
    res2 = _run(director_turn(rt2, "demo", sid, "确定删除"))
    assert res2["edited"] is True
    assert "s01" in (res2["deleted_scenes"] or [])
    proj = ProjectStore(tmp_path).load()
    assert [s.scene_id for s in proj.scenes] == ["s02"]   # s01 没了，s02 留
    assert any("LinXia" == c.name for c in proj.characters)   # 角色不删
    assert any("delete" in (a.get("field") or "")
               for a in DirectEditAuditStore(tmp_path, "demo").recent())


def test_delete_shot_two_phase_cascades_downstream(tmp_path):
    _save_proj_with_shots(tmp_path)
    sid = _runtime(tmp_path).get_or_create_session("demo", "director", "project", "demo")["session_id"]

    reply_false = json.dumps({"tool": "delete_shot", "shot_id": "s01_c02",
                              "confirmed": False}, ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply_false))
    res = _run(director_turn(rt, "demo", sid, "删除镜头 s01_c02"))
    assert res["edited"] is False
    prev = res["deleted_preview"]
    assert set(prev["will_delete"]["shots"]) == {"s01_c02", "s01_c03"}   # 下游 c03 连带

    reply_true = json.dumps({"tool": "delete_shot", "shot_id": "s01_c02",
                             "confirmed": True}, ensure_ascii=False)
    rt2 = _runtime(tmp_path, client=FakeClient(reply_true))
    res2 = _run(director_turn(rt2, "demo", sid, "确定"))
    assert res2["edited"] is True
    assert set(res2["deleted_shots"]) == {"s01_c02", "s01_c03"}
    remaining = {s.shot_id for s in ProjectStore(tmp_path).load().all_shots()}
    assert remaining == {"s01_c01", "s02_c01"}   # 上游 c01 + 他段 c01 保留


def test_delete_unknown_scene_confirmed_fails(tmp_path):
    _save_proj(tmp_path)
    reply = json.dumps({"tool": "delete_scene", "scene_id": "s99",
                        "confirmed": True}, ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    before = (tmp_path / "output" / "project.json").read_bytes()
    res = _run(director_turn(rt, "demo", sid, "删除 s99"))
    assert res["edited"] is False
    assert "操作失败" in res["assistant_message"]
    assert (tmp_path / "output" / "project.json").read_bytes() == before


# ============ 任意位置插入（after 锚点）============
def test_add_scenes_after_anchor_inserts_position(tmp_path):
    _save_proj(tmp_path)   # s01 only, next_scene_seq 会分配 s02
    reply = json.dumps({"tool": "add_scenes_from_script",
                        "after_scene_id": "s01",
                        "scenes": [{"summary": "插在s01后", "characters": ["LinXia"]}]},
                       ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "在 s01 后面插一段"))
    assert res["edited"] is True
    ids = [s.scene_id for s in ProjectStore(tmp_path).load().scenes]
    assert ids == ["s01", "s02"]   # 不是追加到更后面；s02 在 s01 后
    assert res["added_scenes"][0]["scene_id"] == "s02"


def test_add_shots_after_anchor_position_and_index(tmp_path):
    _save_proj(tmp_path)   # s01 无镜头
    # 先加 c01
    r1 = json.dumps({"tool": "add_shots_to_scene", "scene_id": "s01",
                     "shots": [{"description": "第一镜", "characters": ["LinXia"],
                                "target_duration_s": 3}]}, ensure_ascii=False)
    rt1 = _runtime(tmp_path, client=FakeClient(r1))
    s = rt1.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    _run(director_turn(rt1, "demo", s, "拆一个镜头"))
    # 再在 c01 之后插 c02
    r2 = json.dumps({"tool": "add_shots_to_scene", "scene_id": "s01",
                     "after_shot_id": "s01_c01",
                     "shots": [{"description": "插在c01后", "characters": [],
                                "target_duration_s": 5}]}, ensure_ascii=False)
    rt2 = _runtime(tmp_path, client=FakeClient(r2))
    res = _run(director_turn(rt2, "demo", s, "在 c01 后加一镜"))
    assert res["edited"] is True
    sc = next(x for x in ProjectStore(tmp_path).load().scenes if x.scene_id == "s01")
    assert [sh.shot_id for sh in sc.shots] == ["s01_c01", "s01_c02"]
    assert sc.shots[1].index == 2 and sc.shots[1].description == "插在c01后"


# ============ 原地修改 update_scene / update_shot ============
def test_update_scene(tmp_path):
    _save_proj(tmp_path)
    reply = json.dumps({"tool": "update_scene", "scene_id": "s01",
                        "summary": "改过的概要"}, ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "把 s01 概要改成…"))
    assert res["edited"] is True
    sc = next(x for x in ProjectStore(tmp_path).load().scenes if x.scene_id == "s01")
    assert sc.summary == "改过的概要"
    assert res["updated_scenes"] and res["updated_scenes"][0]["scene_id"] == "s01"
    # 审计记录 before/after
    audit = DirectEditAuditStore(tmp_path, "demo").recent()
    assert any("update_scene" in (a.get("field") or "") for a in audit)


def test_update_shot_fields(tmp_path):
    _save_proj_with_shots(tmp_path)   # s01 has c01..c03
    reply = json.dumps({"tool": "update_shot", "shot_id": "s01_c02",
                        "target_duration_s": 8, "characters": []}, ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "把 s01_c02 时长改成8秒"))
    assert res["edited"] is True
    sh = ProjectStore(tmp_path).load().get_shot("s01_c02")
    assert sh.target_duration_s == 8 and sh.characters == []
    assert "target_duration_s" in res["updated_shots"][0]["changed"]


def test_parse_update_tools_and_anchors():
    u = _parse_tool_call('{"tool":"update_scene","scene_id":"s01","summary":"x"}')
    assert u and u["tool"] == "update_scene"
    u2 = _parse_tool_call('{"tool":"update_shot","shot_id":"s01_c02","target_duration_s":8}')
    assert u2 and u2["tool"] == "update_shot"
    a = _parse_tool_call('{"tool":"add_scenes_from_script","after_scene_id":"s01","scenes":[]}')
    assert a and a["after_scene_id"] == "s01"
    a2 = _parse_tool_call('{"tool":"add_shots_to_scene","scene_id":"s01","after_shot_id":"s01_c01","shots":[]}')
    assert a2 and a2["after_shot_id"] == "s01_c01"
    ub = _parse_tool_call('{"tool":"update_shots","scene_id":"s02","shots":[{"shot_id":"s02_c01"}]}')
    assert ub and ub["tool"] == "update_shots"


def test_update_shots_batch(tmp_path):
    _save_proj_with_shots(tmp_path)   # s01 has c01..c03
    reply = json.dumps({"tool": "update_shots", "scene_id": "s01", "shots": [
        {"shot_id": "s01_c01", "shot_size": "close-up", "lighting": "右侧自然光",
         "important_details": "安静、专注"},
        {"shot_id": "s01_c02", "camera_movement": "极缓慢推镜头",
         "target_duration_s": 6},
    ]}, ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "为 s01 制定拍摄方案"))
    assert res["edited"] is True
    p = ProjectStore(tmp_path).load()
    c01 = p.get_shot("s01_c01")
    assert c01.shot_size == "close-up" and c01.lighting == "右侧自然光"
    assert c01.important_details == "安静、专注"
    c02 = p.get_shot("s01_c02")
    assert c02.camera_movement == "极缓慢推镜头" and c02.target_duration_s == 6
    # 审计记录
    audit = DirectEditAuditStore(tmp_path, "demo").recent()
    assert any("update_shots" in (a.get("field") or "") for a in audit)


def test_update_shots_rejects_foreign_or_unknown_shot(tmp_path):
    _save_proj_with_shots(tmp_path)
    # s99_c01 不属于任何段 → 批量更新应失败、不写盘
    reply = json.dumps({"tool": "update_shots", "scene_id": "s01", "shots": [
        {"shot_id": "s99_c01", "description": "x"}]}, ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    before = (tmp_path / "output" / "project.json").read_bytes()
    res = _run(director_turn(rt, "demo", sid, "更新"))
    assert res["edited"] is False
    assert "操作失败" in res["assistant_message"]
    assert (tmp_path / "output" / "project.json").read_bytes() == before


# ============ 完整镜头上下文（读取分层）============
def test_project_context_is_lightweight_directory(tmp_path):
    """上下文只放轻量目录（段+镜头id+描述截断），完整参数走 read_shot。
    大项目几百镜头时不能把所有参数塞进 system prompt（上下文爆炸+不实时）。"""
    from src.agents.director_min import _project_context_text
    _save_proj(tmp_path)
    p = ProjectStore(tmp_path).load()
    p.scenes[0].shots.extend([
        Shot(shot_id="s01_c01", scene_id="s01", index=1, description="林夏坐窗边",
             target_duration_s=5, shot_size="medium shot", camera_angle="固定"),
        Shot(shot_id="s01_c02", scene_id="s01", index=2, description="指尖抚叶",
             target_duration_s=6, camera_movement="缓慢推镜", lighting="侧逆光",
             opening_source="inherit_previous", source_shot_id="s01_c01"),
    ])
    ProjectStore(tmp_path).save(p)
    ctx = _project_context_text(p)
    # 目录包含：段、镜头 id、一句描述
    assert "s01" in ctx and "s01_c01" in ctx and "s01_c02" in ctx
    assert "林夏坐窗边" in ctx
    # 但不包含完整参数（时长/机位/接力/景别）——这些用 read_shot 实时读
    assert "5s" not in ctx and "6s" not in ctx
    assert "medium shot" not in ctx and "侧逆光" not in ctx
    assert "inherit" not in ctx


# ============ 镜头间插入的接力约束 ============
def test_insert_between_continuity_pair_blocked(tmp_path):
    _save_proj_with_shots(tmp_path)   # s01: c01 ← c02 ← c03
    # 把 s01_c02 设为接力 c01（_save_proj_with_shots 里已设 source_shot_id 链）
    p = ProjectStore(tmp_path).load()
    before_ids = [s.shot_id for s in p.scenes[0].shots]
    reply = json.dumps({"tool": "add_shots_to_scene", "scene_id": "s01",
                        "after_shot_id": "s01_c01",
                        "shots": [{"description": "插队镜", "characters": [],
                                   "target_duration_s": 3}]}, ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    before = (tmp_path / "output" / "project.json").read_bytes()
    res = _run(director_turn(rt, "demo", sid, "在 c01 后面插一个镜头"))
    assert res["edited"] is False
    assert "接力" in res["assistant_message"] and "插入" in res["assistant_message"]
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert [s.shot_id for s in ProjectStore(tmp_path).load().scenes[0].shots] == before_ids


def test_insert_after_chain_end_succeeds(tmp_path):
    _save_proj_with_shots(tmp_path)
    reply = json.dumps({"tool": "add_shots_to_scene", "scene_id": "s01",
                        "after_shot_id": "s01_c03",
                        "shots": [{"description": "链尾新镜", "characters": [],
                                   "target_duration_s": 3}]}, ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "在 c03 后面加镜"))
    assert res["edited"] is True
    ids = [s.shot_id for s in ProjectStore(tmp_path).load().scenes[0].shots]
    assert ids[-1] == "s01_c04" and "s01_c02" in ids   # 链尾可插，链没断


# ============ 只读查询 read_shot / read_scene ============
def test_read_shot_returns_params_without_mutation(tmp_path):
    _save_proj_with_shots(tmp_path)
    p = ProjectStore(tmp_path).load()
    c02 = p.get_shot("s01_c02")
    c02.target_duration_s = 8
    c02.camera_movement = "缓慢推镜"
    ProjectStore(tmp_path).save(p)
    before = (tmp_path / "output" / "project.json").read_bytes()

    # 接受 LLM 可能用的别名 get_shot_plan；多步循环：第二次返回自然语言复述
    reply = json.dumps({"tool": "get_shot_plan", "shot_id": "s01_c02"},
                       ensure_ascii=False)
    rt = _runtime(tmp_path, client=SequenceClient(reply, "s01_c02 时长8秒，运镜缓慢推镜。"))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "s01_c02 时长多少、什么运镜？"))
    assert res["edited"] is False
    assert "8" in res["assistant_message"] and "缓慢推镜" in res["assistant_message"]
    # 只读，不写盘
    assert (tmp_path / "output" / "project.json").read_bytes() == before


def test_read_shot_unknown_reports_failure(tmp_path):
    _save_proj_with_shots(tmp_path)
    reply = json.dumps({"tool": "read_shot", "shot_id": "s99_c01"},
                       ensure_ascii=False)
    # 读取失败时 observation 含"读取失败"，但它是只读会继续循环；第二次自然语言结束
    client = SequenceClient(reply, "没找到该镜头。")
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "读 s99_c01"))
    assert res["edited"] is False
    assert client.calls >= 1   # 工具确实被调用过（失败已在 observation）


# ============ read_script：Director 可按需读剧本 ============
def test_read_script_returns_full_text(tmp_path):
    _save_proj(tmp_path)
    p = ProjectStore(tmp_path).load()
    p.story_text = "林夏走进雨里。她抬起头。" * 50   # 较长剧本
    ProjectStore(tmp_path).save(p)
    before = (tmp_path / "output" / "project.json").read_bytes()

    reply = json.dumps({"tool": "read_script"}, ensure_ascii=False)
    rt = _runtime(tmp_path, client=SequenceClient(reply, "剧本开头是林夏走进雨里，已读完。"))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "读一下剧本"))
    assert res["edited"] is False
    assert "林夏走进雨里" in res["assistant_message"]
    # 只读不写
    assert (tmp_path / "output" / "project.json").read_bytes() == before


def test_multi_step_read_then_answer(tmp_path):
    """多步循环：agent 可连续调只读工具（read_script + list_scenes）后给最终回答。"""
    _save_proj(tmp_path)
    class MultiClient:
        def __init__(self): self.calls = 0
        async def chat(self, messages, **kw):
            self.calls += 1
            last = messages[-1]["content"]
            if self.calls == 1:
                return json.dumps({"tool": "read_script"})
            if "[read_script]" in last:
                return json.dumps({"tool": "list_scenes"})
            return "已读完剧本和分镜目录，可以给建议了。"
    client = MultiClient()
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "结合剧本和各段现状给建议"))
    assert res["edited"] is False
    assert "建议" in res["assistant_message"]
    assert client.calls == 3   # read_script → list_scenes → 最终回答


def test_multiple_tools_in_one_reply_with_tags(tmp_path):
    """一条回复里用 <tool_call> 包多个工具调用，应全部解析并执行（list+reads+updates）。"""
    _save_proj_with_shots(tmp_path)   # s01: c01..c03
    class BatchClient:
        def __init__(self): self.calls = 0
        async def chat(self, messages, **kw):
            self.calls += 1
            if self.calls == 1:
                return (
                    '<tool_call>{"tool":"list_scenes"}</tool_call>\n'
                    '<tool_call>{"tool":"read_scene","scene_id":"s01"}</tool_call>\n'
                    '<tool_call>{"tool":"update_scene","scene_id":"s01","summary":"改过的s01"}</tool_call>'
                )
            return "全部完成。"
    client = BatchClient()
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "读取并更新"))
    assert res["edited"] is True
    s01 = next(s for s in ProjectStore(tmp_path).load().scenes if s.scene_id == "s01")
    assert s01.summary == "改过的s01"   # 第三个工具生效
    assert client.calls == 2            # 第一批工具 → 最终回答


def test_mixed_reads_updates_adds_in_one_reply(tmp_path):
    """你遇到的真实场景：一条回复里 list_scenes + read_scene×2 + update_scene×2
    + add_scenes，全部应解析执行。"""
    _save_proj_with_shots(tmp_path)   # s01..s02
    class BigBatch:
        def __init__(self): self.calls = 0
        async def chat(self, messages, **kw):
            self.calls += 1
            if self.calls == 1:
                return (
                    '<tool_call>{"tool":"list_scenes"}</tool_call>'
                    '<tool_call>{"tool":"read_scene","scene_id":"s01"}</tool_call>'
                    '<tool_call>{"tool":"update_scene","scene_id":"s01","summary":"s01新概要"}</tool_call>'
                    '<tool_call>{"tool":"add_scenes_from_script","scenes":[{"summary":"新段A","characters":[]}]}'
                )
            return "完成。"
    client = BigBatch()
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "综合调整"))
    assert res["edited"] is True
    p = ProjectStore(tmp_path).load()
    assert next(s.summary for s in p.scenes if s.scene_id == "s01") == "s01新概要"
    assert any(s.summary == "新段A" for s in p.scenes)   # 新增的段
    assert len(res["updated_scenes"]) >= 1 and len(res["added_scenes"]) >= 1


def test_read_after_write_in_same_reply_sees_fresh_data(tmp_path):
    """关键回归：一条回复里先 update/add，再 list_scenes，只读工具必须能读到刚写入的数据
    （之前 proj 是旧快照，导致 LLM 以为没写成功）。"""
    _save_proj_with_shots(tmp_path)   # s01, s02
    class WriteThenRead:
        def __init__(self): self.calls = 0
        async def chat(self, messages, **kw):
            self.calls += 1
            if self.calls == 1:
                return (
                    '<tool_call>{"tool":"update_scene","scene_id":"s01","summary":"s01已改"}</tool_call>'
                    '<tool_call>{"tool":"add_scenes_from_script","scenes":[{"summary":"新段X","characters":[]}]}</tool_call>'
                    '<tool_call>{"tool":"list_scenes"}</tool_call>'
                )
            # 第二次：根据 list_scenes 结果回答（结果在最后一条 user 消息里）
            last = messages[-1]["content"]
            assert "s01已改" in last, "写入后 list_scenes 必须能读到新数据"
            assert "新段X" in last
            return "确认已生效。"
    client = WriteThenRead()
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "改完再核对"))
    assert res["edited"] is True
    p = ProjectStore(tmp_path).load()
    assert next(s.summary for s in p.scenes if s.scene_id == "s01") == "s01已改"
    assert any(s.summary == "新段X" for s in p.scenes)


def test_script_preview_in_context_is_lite(tmp_path):
    """system prompt 只放剧本预览（字数+开头），完整剧本走 read_script；不把全量塞进上下文。"""
    from src.agents.director_min import _script_preview
    p = _proj()
    p.story_text = "开头内容。" + "x" * 500 + "结尾。"
    prev = _script_preview(p)
    assert "共" in prev and "字" in prev
    assert "结尾" not in prev       # 完整结尾不在预览里
    assert "read_script" in prev    # 提示用工具读完整


def test_multi_step_write_in_one_turn(tmp_path):
    """多步写入：一轮内可连续 新增 + 修改（方案A）。"""
    _save_proj(tmp_path)
    class PlanClient:
        def __init__(self): self.calls = 0
        async def chat(self, messages, **kw):
            self.calls += 1
            if self.calls == 1:
                return json.dumps({"tool": "add_scenes_from_script",
                                   "scenes": [{"summary": "新段A", "characters": []}]})
            if self.calls == 2:
                # 第二步：修改刚建的段（id 从返回结果读，但测试里直接用 s02）
                return json.dumps({"tool": "update_scene", "scene_id": "s02",
                                   "summary": "新段A-改"})
            # 第三步：拿到两个工具结果后输出最终总结，结束
            return "已完成新增和修改。"
    client = PlanClient()
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "加一段并修改它"))
    assert res["edited"] is True
    p = ProjectStore(tmp_path).load()
    assert any(s.scene_id == "s02" for s in p.scenes)
    s02 = next(s for s in p.scenes if s.scene_id == "s02")
    assert s02.summary == "新段A-改"   # 第二步的修改生效
    assert len(res["added_scenes"]) == 1 and len(res["updated_scenes"]) == 1


def test_cannot_self_confirm_delete_in_one_turn(tmp_path):
    """安全：一轮内 LLM 不能自己从 confirmed=false 跳到 confirmed=true（必须等用户确认）。"""
    _save_proj_with_shots(tmp_path)
    class SneakyClient:
        def __init__(self): self.calls = 0
        async def chat(self, messages, **kw):
            self.calls += 1
            if self.calls == 1:
                return json.dumps({"tool": "delete_scene", "scene_id": "s01",
                                   "confirmed": False})
            # 试图自行确认
            return json.dumps({"tool": "delete_scene", "scene_id": "s01",
                               "confirmed": True})
    rt = _runtime(tmp_path, client=SneakyClient())
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    before = (tmp_path / "output" / "project.json").read_bytes()
    res = _run(director_turn(rt, "demo", sid, "删掉 s01"))
    assert res["edited"] is False
    # s01 仍在（自我确认被拒）
    assert any(s.scene_id == "s01" for s in ProjectStore(tmp_path).load().scenes)
    assert "确认" in res["assistant_message"]
