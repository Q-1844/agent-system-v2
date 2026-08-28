"""AI-7D-R · 批量写工具 story.create_many / story.update_many 测试。

覆盖：
  1. 批量创建 3/5/8 段一次成功
  2. 批量修改多个不同 scene
  3. 同 scene 重复 update 被拒
  4. batch 参数非法时不产生半成功（atomic）
  5. stale revision 仍被拒
  6. move/delete/order 不能混入 update_many
  7. 原有单写工具行为零回归（create/update 仍工作）
"""
import asyncio
import json
from types import SimpleNamespace

from src.core.models import Project, Scene, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.script_writer import (script_writer_turn, _scenes_token, _scenes_content_token,
                                       _execute_story_create_many, _execute_story_update_many)


def _proj(n=6):
    return Project(name="demo", style=StyleAnchor(),
                   story_text="剧本正文。",
                   characters=[Character(name="LinXia", description_en="x")],
                   scenes=[Scene(scene_id=f"s0{i}", summary=f"第{i}段", characters=["LinXia"], shots=[])
                           for i in range(1, n + 1)],
                   next_scene_seq=n + 1)


def _save(tmp_path, n=6):
    ProjectStore(tmp_path).save(_proj(n))
    return tmp_path


def _runtime(tmp_path, client=None):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client)


def _run(coro):
    return asyncio.run(coro)


def _tc(name, args, call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _rev_scenes(ids):
    return _scenes_token([SimpleNamespace(scene_id=i) for i in ids])


# ============ 1. 批量创建 3/5/8 段 ============
def test_create_many_3(tmp_path):
    _save(tmp_path, 4)
    rt = _runtime(tmp_path)
    rev = _rev_scenes(["s01", "s02", "s03", "s04"])
    obs = _execute_story_create_many(rt, "demo",
        json.dumps({"scenes": [{"summary": f"段{x}", "characters": ["LinXia"]} for x in (1, 2, 3)],
                    "expected_scenes_revision": rev}), "instr", "m1")
    assert obs["ok"] and obs["verified"]
    assert obs["count"] == 3
    ids = [c["scene_id"] for c in obs["created"]]
    assert ids == ["s05", "s06", "s07"]   # 连续 id
    assert [c["order"] for c in obs["created"]] == [5, 6, 7]   # 顺序连续
    # canonical 落盘
    scenes = [s.scene_id for s in ProjectStore(tmp_path).load().scenes]
    assert scenes == ["s01", "s02", "s03", "s04", "s05", "s06", "s07"]


def test_create_many_5_and_8(tmp_path):
    for n, exp in [(5, 5), (8, 8)]:
        _save(tmp_path, 2)
        rt = _runtime(tmp_path)
        rev = _rev_scenes(["s01", "s02"])
        obs = _execute_story_create_many(rt, "demo",
            json.dumps({"scenes": [{"summary": f"段{i}", "characters": []} for i in range(n)],
                        "expected_scenes_revision": rev}), "instr", "m1")
        assert obs["ok"] and obs["verified"] and obs["count"] == exp
        scenes = ProjectStore(tmp_path).load().scenes
        assert len(scenes) == 2 + exp
        assert [s.summary for s in scenes[2:]] == [f"段{i}" for i in range(n)]   # 顺序保留


def test_create_many_after_anchor(tmp_path):
    _save(tmp_path, 4)
    rt = _runtime(tmp_path)
    rev = _rev_scenes(["s01", "s02", "s03", "s04"])
    obs = _execute_story_create_many(rt, "demo",
        json.dumps({"scenes": [{"summary": "A"}, {"summary": "B"}],
                    "after_scene_id": "s02", "expected_scenes_revision": rev}), "instr", "m1")
    assert obs["ok"]
    scenes = [s.scene_id for s in ProjectStore(tmp_path).load().scenes]
    # s02 后插入 s05,s06
    assert scenes == ["s01", "s02", "s05", "s06", "s03", "s04"]


# ============ 2. 批量修改多个不同 scene ============
def test_update_many_different_scenes(tmp_path):
    _save(tmp_path, 4)
    rt = _runtime(tmp_path)
    before_scenes = ProjectStore(tmp_path).load().scenes
    struct_rev = _scenes_token(before_scenes)          # 结构指纹
    content_rev = _scenes_content_token(before_scenes)  # 内容指纹
    obs = _execute_story_update_many(rt, "demo",
        json.dumps({"updates": [
            {"scene_id": "s01", "summary": "新摘要1"},
            {"scene_id": "s03", "summary": "新摘要3", "location": "室内"}],
            "expected_revision": content_rev}), "instr", "m1")
    assert obs["ok"] and obs["verified"]
    assert obs["count"] == 2
    res_by = {r["scene_id"]: r for r in obs["results"]}
    assert set(res_by["s01"]["changed"]) == {"summary"}
    assert set(res_by["s03"]["changed"]) == {"summary", "location"}
    # canonical
    proj = ProjectStore(tmp_path).load()
    s01 = next(s for s in proj.scenes if s.scene_id == "s01")
    assert s01.summary == "新摘要1"
    s03 = next(s for s in proj.scenes if s.scene_id == "s03")
    assert s03.summary == "新摘要3" and s03.location == "室内"
    # 结构指纹不变（段内改动不影响结构），内容指纹变了
    assert _scenes_token(proj.scenes) == struct_rev   # 结构未变
    assert _scenes_content_token(proj.scenes) != content_rev   # 内容变了


# ============ 3. 同 scene 重复 update 被拒 ============
def test_update_many_dup_scene_rejected(tmp_path):
    _save(tmp_path, 3)
    rt = _runtime(tmp_path)
    rev = _scenes_content_token(ProjectStore(tmp_path).load().scenes)
    try:
        _execute_story_update_many(rt, "demo",
            json.dumps({"updates": [
                {"scene_id": "s01", "summary": "A"},
                {"scene_id": "s01", "summary": "B"}],   # 重复 s01
                "expected_revision": rev}), "instr", "m1")
        assert False, "应拒绝重复 scene_id"
    except AgentError as e:
        assert e.code == "bad_arguments" and "重复" in e.message
    # canonical 未改（atomic）
    s01 = next(s for s in ProjectStore(tmp_path).load().scenes if s.scene_id == "s01")
    assert s01.summary == "第1段"


# ============ 4. batch 参数非法 → 不产生半成功（atomic）============
def test_create_many_bad_spec_no_partial(tmp_path):
    _save(tmp_path, 3)
    rt = _runtime(tmp_path)
    rev = _rev_scenes(["s01", "s02", "s03"])
    # 第2个 spec summary 为空 → 整批不写
    try:
        _execute_story_create_many(rt, "demo",
            json.dumps({"scenes": [{"summary": "好的"}, {"summary": ""},
                                   {"summary": "也好的"}],
                        "expected_scenes_revision": rev}), "instr", "m1")
        assert False, "应拒绝空 summary"
    except AgentError as e:
        assert e.code == "bad_arguments"
    # canonical 无新增
    assert len(ProjectStore(tmp_path).load().scenes) == 3


def test_update_many_bad_field_no_partial(tmp_path):
    _save(tmp_path, 3)
    rt = _runtime(tmp_path)
    rev = _scenes_content_token(ProjectStore(tmp_path).load().scenes)
    # s02 给 scene_id 改动（禁止字段 scene_id）——其实 scene_id 是定位字段不算；
    # 用未知字段 order 模拟 order 混入
    try:
        _execute_story_update_many(rt, "demo",
            json.dumps({"updates": [
                {"scene_id": "s01", "summary": "A"},
                {"scene_id": "s02", "order": "3"}],   # order 非法
                "expected_revision": rev}), "instr", "m1")
        assert False, "应拒绝 order 字段"
    except AgentError as e:
        assert e.code == "bad_arguments"
    assert len(ProjectStore(tmp_path).load().scenes) == 3
    s01 = next(s for s in ProjectStore(tmp_path).load().scenes if s.scene_id == "s01")
    assert s01.summary == "第1段"   # 未改


# ============ 5. stale revision 仍被拒 ============
def test_create_many_stale_rejected(tmp_path):
    _save(tmp_path, 3)
    rt = _runtime(tmp_path)
    obs = _execute_story_create_many(rt, "demo",
        json.dumps({"scenes": [{"summary": "x"}],
                    "expected_scenes_revision": "deadbeef"}), "instr", "m1")
    assert not obs["ok"] and obs.get("stale") and obs["error"] == "stale_revision"
    assert len(ProjectStore(tmp_path).load().scenes) == 3   # 未写


def test_update_many_stale_rejected(tmp_path):
    _save(tmp_path, 3)
    rt = _runtime(tmp_path)
    obs = _execute_story_update_many(rt, "demo",
        json.dumps({"updates": [{"scene_id": "s01", "summary": "x"}],
                    "expected_revision": "deadbeef"}), "instr", "m1")
    assert not obs["ok"] and obs.get("stale")
    assert next(s for s in ProjectStore(tmp_path).load().scenes if s.scene_id == "s01").summary == "第1段"


# ============ 6. move/delete/order 不能混入 update_many ============
def test_update_many_rejects_non_content_fields(tmp_path):
    _save(tmp_path, 3)
    rt = _runtime(tmp_path)
    rev = _scenes_content_token(ProjectStore(tmp_path).load().scenes)
    for bad_field in ("before_scene_id", "after_scene_id", "confirmed", "shots"):
        try:
            _execute_story_update_many(rt, "demo",
                json.dumps({"updates": [{"scene_id": "s01", "summary": "x", bad_field: "v"}],
                            "expected_revision": rev}), "instr", "m1")
            assert False, f"应拒绝字段 {bad_field}"
        except AgentError as e:
            assert e.code == "bad_arguments"
    # scene 不存在
    try:
        _execute_story_update_many(rt, "demo",
            json.dumps({"updates": [{"scene_id": "s99", "summary": "x"}],
                        "expected_revision": rev}), "instr", "m1")
        assert False, "应拒绝不存在 scene"
    except AgentError as e:
        assert e.code == "not_found"


# ============ 7. 原有单写工具零回归 ============
def test_single_create_still_works(tmp_path):
    _save(tmp_path, 3)
    rt = _runtime(tmp_path)
    rev = _rev_scenes(["s01", "s02", "s03"])
    from src.agents.script_writer import _execute_story_create
    obs = _execute_story_create(rt, "demo",
        json.dumps({"summary": "新段", "characters": ["LinXia"],
                    "expected_scenes_revision": rev}), "instr", "m1")
    assert obs["ok"] and obs["verified"] and obs["scene_id"] == "s04"


def test_single_update_still_works(tmp_path):
    _save(tmp_path, 3)
    rt = _runtime(tmp_path)
    from src.agents.script_writer import _execute_story_update, _scene_token
    scene = next(s for s in ProjectStore(tmp_path).load().scenes if s.scene_id == "s02")
    rev = _scene_token(scene)
    obs = _execute_story_update(rt, "demo",
        json.dumps({"scene_id": "s02", "summary": "改后", "expected_revision": rev}),
        "instr", "m1")
    assert obs["ok"] and obs["verified"]


def test_create_many_no_change_noop(tmp_path):
    """全 no-op（提供内容与现值一致）→ ok unchanged 不写盘。"""
    _save(tmp_path, 2)
    rt = _runtime(tmp_path)
    rev = _scenes_content_token(ProjectStore(tmp_path).load().scenes)
    # s01 当前 summary="第1段"，传相同 → no-op
    obs = _execute_story_update_many(rt, "demo",
        json.dumps({"updates": [{"scene_id": "s01", "summary": "第1段"}],
                    "expected_revision": rev}), "instr", "m1")
    assert obs["ok"] and obs.get("unchanged") and obs["count"] == 0


# ============ 端到端：script_writer_turn 用 create_many 一次创建多段 ============
class _ScriptedClient:
    def __init__(self, script): self.script = list(script); self.thinking = []
    async def chat_completion_stream(self, messages, **kw):
        self.thinking.append(kw.get("thinking", False))
        step = self.script.pop(0) if self.script else {"content": "完成。"}
        if step.get("tool_calls"):
            yield {"kind": "finish", "reason": "tool_calls", "content": None,
                   "tool_calls": step["tool_calls"], "usage": None}
        else:
            for d in [step.get("content", "")]:
                if d:
                    yield {"kind": "content_delta", "delta": d}
            yield {"kind": "finish", "reason": "stop", "content": step.get("content", ""),
                   "tool_calls": None, "usage": None}
    async def chat(self, messages, **kw): return ""


def test_e2e_create_many_one_call(tmp_path):
    """端到端：模型一次调 create_many 创建 3 段，无串行拒绝、一轮成功。"""
    _save(tmp_path, 4)
    rev = _rev_scenes(["s01", "s02", "s03", "s04"])
    client = _ScriptedClient([
        {"tool_calls": [_tc("story.create_many", {
            "scenes": [{"summary": "A", "characters": ["LinXia"]},
                       {"summary": "B", "characters": ["LinXia"]},
                       {"summary": "C", "characters": ["LinXia"]}],
            "expected_scenes_revision": rev})]},
        {"content": "已创建 3 段。"},
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    events = []
    res = _run(script_writer_turn(rt, "demo", sid, "加 3 段", event_cb=lambda e: events.append(e)))
    assert res["created"] is True
    assert res["intent"] == "story_create"
    # 无串行拒绝（create_many 一次成功）
    assert not any(e.get("type") == "tool.completed" and e.get("ok") is False for e in events)
    scenes = [s.scene_id for s in ProjectStore(tmp_path).load().scenes]
    assert scenes[-3:] == ["s05", "s06", "s07"]


# ============ 8. provider 把嵌套数组/对象 stringify 的容错（实测 Agnes 大参数怪癖） ============
# 真 Agnes 在大段中文+引号时，会把 scenes/updates 发成 "[{...}]" 这种 JSON 字符串而非数组，
# 导致 isinstance(list) 失败、整批被拒（"scenes 必须是非空数组"）。executor 需宽容还原。

def test_create_many_stringified_scenes_array(tmp_path):
    """scenes 整体是 JSON 字符串（模型怪癖）→ json.loads 还原后正常创建。"""
    _save(tmp_path, 4)
    rt = _runtime(tmp_path)
    rev = _rev_scenes(["s01", "s02", "s03", "s04"])
    specs = [{"summary": f"串化段{x}", "characters": []} for x in (1, 2)]
    # 注意：scenes 的值是 json.dumps(specs) 得到的【字符串】，不是 list
    raw = json.dumps({"scenes": json.dumps(specs, ensure_ascii=False),
                      "expected_scenes_revision": rev})
    obs = _execute_story_create_many(rt, "demo", raw, "instr", "m1")
    assert obs["ok"] and obs["verified"]
    assert obs["count"] == 2
    scenes = ProjectStore(tmp_path).load().scenes
    assert [s.summary for s in scenes[-2:]] == ["串化段1", "串化段2"]


def test_create_many_stringified_items_and_characters(tmp_path):
    """scenes 数组项被 stringify、characters 也被 stringify → 逐项还原。"""
    _save(tmp_path, 2)
    rt = _runtime(tmp_path)
    rev = _rev_scenes(["s01", "s02"])
    item_str = json.dumps({"summary": "项也串化", "characters": json.dumps(["LinXia"])},
                          ensure_ascii=False)
    raw = json.dumps({"scenes": [item_str], "expected_scenes_revision": rev})
    obs = _execute_story_create_many(rt, "demo", raw, "instr", "m1")
    assert obs["ok"] and obs["verified"]
    new = ProjectStore(tmp_path).load().scenes[-1]
    assert new.summary == "项也串化"
    assert new.characters == ["LinXia"]


def test_update_many_stringified_updates(tmp_path):
    """updates 整体是 JSON 字符串 → 还原后正常修改。"""
    _save(tmp_path, 3)
    rt = _runtime(tmp_path)
    rev = _scenes_content_token(ProjectStore(tmp_path).load().scenes)
    updates = [{"scene_id": "s01", "summary": "改后内容"}]
    raw = json.dumps({"updates": json.dumps(updates, ensure_ascii=False),
                      "expected_revision": rev})
    obs = _execute_story_update_many(rt, "demo", raw, "instr", "m1")
    assert obs["ok"] and obs["verified"]
    assert ProjectStore(tmp_path).load().scenes[0].summary == "改后内容"


def test_create_many_unparseable_string_rejected(tmp_path):
    """scenes 是无法解析的非 JSON 字符串 → 仍报准确的 bad_arguments，不写盘。"""
    import pytest
    _save(tmp_path, 2)
    rt = _runtime(tmp_path)
    rev = _rev_scenes(["s01", "s02"])
    raw = json.dumps({"scenes": "这不是json也不是数组", "expected_scenes_revision": rev})
    with pytest.raises(AgentError) as ei:
        _execute_story_create_many(rt, "demo", raw, "instr", "m1")
    assert "scenes 必须是非空数组" in str(ei.value)
    # 未写盘
    assert len(ProjectStore(tmp_path).load().scenes) == 2
