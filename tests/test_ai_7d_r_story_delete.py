"""AI-7D-R · Script Writer story.delete 测试。

复用 Director 同款公用删除服务 src.storyboard.delete_service，两阶段确认：
  confirmed=false → 预览（纯读，0 mutation）；confirmed=true → 真正级联删除 + 回读校验。

覆盖：预览 0 mutation / 确认后级联删段+段内镜头 / 两阶段必须（不能一步删）/
非法 scene_id / 段内镜头被一并删 / 其它段不动 / canonical 回读 / 回执与审计 /
执行真实性（只预览就谎称已删被纠偏；真删后诚实汇报通过）/ 活跃生产护栏。

不联网（ScriptedClient）。
"""
import asyncio
import json

import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.script_writer import script_writer_turn
from src.agents.revision_audit import DirectEditAuditStore


def _proj_with_shots():
    s01 = Scene(scene_id="s01", summary="第一段", characters=["LinXia"], shots=[
        Shot(shot_id="s01_c01", scene_id="s01", index=1, description="镜头一",
             characters=["LinXia"], target_duration_s=3),
        Shot(shot_id="s01_c02", scene_id="s01", index=2, description="镜头二",
             characters=["LinXia"], target_duration_s=3),
    ])
    s02 = Scene(scene_id="s02", summary="第二段", characters=["LinXia"], shots=[])
    s03 = Scene(scene_id="s03", summary="第三段", characters=["YuShu"], shots=[
        Shot(shot_id="s03_c01", scene_id="s03", index=1, description="镜头三",
             characters=["YuShu"], target_duration_s=4),
    ])
    return Project(
        name="demo", style=StyleAnchor(), story_text="剧本正文。",
        characters=[Character(name="LinXia", description_en="x"),
                    Character(name="YuShu", description_en="y")],
        scenes=[s01, s02, s03], next_scene_seq=4)


def _save(tmp_path):
    ProjectStore(tmp_path).save(_proj_with_shots())
    return tmp_path


def _runtime(tmp_path, client):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client)


def _run(coro):
    return asyncio.run(coro)


def _tc(name, arguments, call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments, ensure_ascii=False)}}


class ScriptedClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat_completion(self, messages, **kw):
        self.calls.append({"messages": messages, "kw": kw})
        if not self.script:
            return {"content": "（脚本耗尽）", "tool_calls": None}
        content, tool_calls = self.script.pop(0)
        return {"content": content, "tool_calls": tool_calls}

    async def chat(self, messages, **kw):
        return self.script and self.script.pop(0)[0] or ""


def _exec_delete(rt, payload):
    from src.agents.script_writer import _execute_story_delete
    return _execute_story_delete(rt, "demo", json.dumps(payload), "u", "m")


# ============ 1. 预览（显式 confirmed=false）：纯读，0 mutation（方案 B 兼容）============
def test_preview_explicit_false_no_mutation(tmp_path):
    """方案 B：默认直接删；仅显式 confirmed=false 才预览（兼容旧两阶段）。"""
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    before = (tmp_path / "output" / "project.json").read_bytes()
    obs = _exec_delete(rt, {"scene_id": "s01", "confirmed": False})
    assert obs["ok"] is True
    assert obs["confirmed"] is False
    assert obs["verified"] is False          # 预览不算真实变更
    assert obs["will_delete"]["scenes"] == ["s01"]
    assert set(obs["will_delete"]["shots"]) == {"s01_c01", "s01_c02"}
    assert "s02" in obs["other_scenes_untouched"] and "s03" in obs["other_scenes_untouched"]
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not DirectEditAuditStore(tmp_path, "demo").recent()


# ============ 1b. 方案 B 核心：confirmed 省略 → 直接删除（不再预览）============
def test_default_omit_confirmed_directly_deletes(tmp_path):
    """方案 B：不传 confirmed（省略）= 直接删除，不需要两阶段。"""
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    obs = _exec_delete(rt, {"scene_id": "s01"})   # 不传 confirmed
    assert obs["ok"] is True and obs["verified"] is True
    assert obs["confirmed"] is True
    # 真删了
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s02", "s03"]


# ============ 2. 删除：级联删段 + 段内镜头，canonical 回读验证 ============
def test_confirmed_deletes_scene_and_its_shots(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    obs = _exec_delete(rt, {"scene_id": "s01", "confirmed": True})

    assert obs["ok"] is True and obs["verified"] is True
    assert obs["confirmed"] is True
    assert obs["scene_id"] == "s01"
    assert obs["deleted"]["scenes"] == ["s01"]
    assert set(obs["deleted"]["shots"]) == {"s01_c01", "s01_c02"}

    proj = ProjectStore(tmp_path).load()
    # s01 真的没了
    assert [s.scene_id for s in proj.scenes] == ["s02", "s03"]
    # 段内镜头也没了，其它段镜头保留
    all_shot_ids = [sh.shot_id for s in proj.scenes for sh in s.shots]
    assert "s01_c01" not in all_shot_ids and "s01_c02" not in all_shot_ids
    assert "s03_c01" in all_shot_ids
    # 审计记录
    audit = DirectEditAuditStore(tmp_path, "demo").recent()
    assert audit and audit[-1]["field"] == "delete_scene:s01"
    assert audit[-1]["after"] == []


# ============ 3. 两阶段强制：只调一次 confirmed=true（没预览）也能删，但预览阶段不删 ============
def test_preview_does_not_delete(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    # 预览
    _exec_delete(rt, {"scene_id": "s02", "confirmed": False})
    # 仍然存在
    assert any(s.scene_id == "s02" for s in ProjectStore(tmp_path).load().scenes)


# ============ 4a. 非法 scene_id（预览/确认）→ not_found，不写盘 ============
@pytest.mark.parametrize("confirmed", [False, True])
def test_nonexistent_scene_not_found(tmp_path, confirmed):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    before = (tmp_path / "output" / "project.json").read_bytes()
    with pytest.raises(AgentError) as ei:
        _exec_delete(rt, {"scene_id": "s99", "confirmed": confirmed})
    assert ei.value.code == "not_found"
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not DirectEditAuditStore(tmp_path, "demo").recent()


# ============ 4b. 缺 scene_id → bad_arguments ============
def test_missing_scene_id(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    with pytest.raises(AgentError) as ei:
        _exec_delete(rt, {"confirmed": True})
    assert ei.value.code == "bad_arguments"


# ============ 5. 删中段不影响其它段内容/顺序 ============
def test_delete_middle_preserves_others(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    _exec_delete(rt, {"scene_id": "s02", "confirmed": True})
    proj = ProjectStore(tmp_path).load()
    assert [s.scene_id for s in proj.scenes] == ["s01", "s03"]
    s01 = next(s for s in proj.scenes if s.scene_id == "s01")
    assert s01.summary == "第一段" and [sh.shot_id for sh in s01.shots] == ["s01_c01", "s01_c02"]
    s03 = next(s for s in proj.scenes if s.scene_id == "s03")
    assert s03.summary == "第三段" and [sh.shot_id for sh in s03.shots] == ["s03_c01"]


# ============ 6. 写盘失败（save no-op）→ verify_failed，ok=false ============
def test_delete_save_not_persisted_verify_failed(tmp_path, monkeypatch):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    # execute_delete 内部会 save；把 ProjectStore.save 变成 no-op 模拟落盘失败
    monkeypatch.setattr(ProjectStore, "save", lambda self, project: None)
    obs = _exec_delete(rt, {"scene_id": "s01", "confirmed": True})
    assert obs["ok"] is False and obs["error"] == "verify_failed"
    assert obs["verified"] is False
    # canonical 未变
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s02", "s03"]


# ============ 7. 方案 B：用户说删 → Agent 直接删（单阶段，不预览问确认）============
def test_turn_direct_delete_no_two_phase(tmp_path):
    """方案 B：story.delete(scene_id) 不传 confirmed → 直接删除。"""
    _save(tmp_path)
    client = ScriptedClient([
        (None, [_tc("story.delete", {"scene_id": "s03"}, "d1")]),
        ("已删除故事段 s03。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "删掉第三段"))

    assert res.get("deleted") is True
    assert res["intent"] == "story_delete"
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s02"]
    # 真实成功 → 事实摘要追加真实操作
    assert "删除故事段 s03" in res["assistant_message"]


def test_turn_confirmed_delete_after_user_yes(tmp_path):
    _save(tmp_path)
    client = ScriptedClient([
        # confirmed=true 显式删（与省略等价）
        (None, [_tc("story.delete", {"scene_id": "s02", "confirmed": True}, "d1")]),
        ("已删除故事段 s02。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "确定删掉第二段"))

    assert res.get("deleted") is True
    assert res["intent"] == "story_delete"
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s03"]
    assert "已删除故事段 s02。" in res["assistant_message"]
    assert len(client.calls) == 2


# ============ 8. 执行真实性：没真删却谎称「已删除」→ 兜底（方案 B：默认就删，更少空转）============
def test_turn_false_claim_without_delete_gets_fallback(tmp_path):
    _save(tmp_path)
    client = ScriptedClient([
        # 没发任何 story.delete，却谎称删了
        ("s03 已经删除了。", None),
        # zero-write recovery（明确删除请求但零写尝试）后仍嘴硬
        ("已经删掉了。", None),
        # 既有真相闸门纠偏后还嘴硬 → 诚实兜底
        ("s03 确实已经删除完成。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "删掉第三段"))

    assert res.get("deleted") is False
    # canonical 未变
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s02", "s03"]
    # 兜底文案诚实
    assert "已经删除了" not in res["assistant_message"]
    assert ("没有真正执行修改" in res["assistant_message"]
            or "没有发出对应的工具调用" in res["assistant_message"])


# ============ 9. 活跃生产护栏：有视频在生成时 confirmed=true 被拒（并发安全）============
def test_confirmed_delete_blocked_while_production_active(tmp_path, monkeypatch):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    # 打桩「有正在进行的视频生成」（复用 change_protocol 护栏，best-effort 导入）。
    import src.agents.script_writer as sw
    monkeypatch.setattr(sw, "_active_production_reason", lambda project: "镜头 s03_c01 正在生成视频")
    # 预览仍可（告知用户），但带 active_production 标记
    prev = _exec_delete(rt, {"scene_id": "s03", "confirmed": False})
    assert prev["ok"] is True and prev["confirmed"] is False
    assert prev["preview"].get("active_production")
    # 确认删除被拒，不写盘
    before = (tmp_path / "output" / "project.json").read_bytes()
    with pytest.raises(AgentError) as ei:
        _exec_delete(rt, {"scene_id": "s03", "confirmed": True})
    assert ei.value.code == "production_active"
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert any(s.scene_id == "s03" for s in ProjectStore(tmp_path).load().scenes)


# ============ 10. 回执统一结构 ============
def test_delete_receipt_shape(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    obs = _exec_delete(rt, {"scene_id": "s02", "confirmed": True})
    for key in ("ok", "operation", "target", "before", "after",
                "revision", "verified", "error", "confirmed"):
        assert key in obs
    assert obs["operation"] == "story.delete"
    assert obs["target"] == "scene:s02"
    assert obs["before"] == ["s01", "s02", "s03"]
    assert obs["after"] == ["s01", "s03"]
