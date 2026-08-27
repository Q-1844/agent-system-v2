"""AI-7D-R · Script Writer 自主多工具组合测试（Phase 3）。

验证 Agent 自主选择、连续组合多个工具完成复杂任务（不预生成计划、不建 workflow）。
所有写工具串行、observation 是唯一事实来源、最终回复须由真实 receipts + 最终读取支持。

覆盖（对应需求 #8 的 11 项）：
 1. read → 多次 create → 最终检查
 2. read/list → update 多段 → move → 最终检查
 3. create → update 新段 → move
 4. update → delete → create → move
 5. 中途一次 stale，Agent 重新读取后继续完成
 6. 中途工具参数错误，Agent 自行修正
 7. 删除触发已有依赖机制（级联删段内镜头）
 8. 超过原 8 turn 的长任务
 9. 最终回复与 canonical 状态完全一致
10. 不出现「工具没成功但 Agent 宣称成功」
11. 不出现连续重复调用同一无效操作导致失控

并验证执行轨迹（turn→tool→target→result→revision）可读。

不联网（ScriptedClient）。
"""
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.script_writer import script_writer_turn, _scenes_token
from src.agents.revision_audit import DirectEditAuditStore


# ============ 公共装置 ============
def _multi_proj():
    def _sc(sid, summ, chars=None, shots=None):
        return Scene(scene_id=sid, summary=summ, characters=chars or ["LinXia"],
                     shots=shots or [])
    return Project(
        name="demo", style=StyleAnchor(), story_text="剧本正文待整理。",
        characters=[Character(name="LinXia", description_en="x"),
                    Character(name="YuShu", description_en="y")],
        scenes=[_sc("s01", "第一段草稿"), _sc("s02", "第二段重复内容"),
                _sc("s03", "第三段", shots=[
                    Shot(shot_id="s03_c01", scene_id="s03", index=1,
                         description="镜头一", characters=["LinXia"], target_duration_s=3)])],
        next_scene_seq=4)


def _empty_proj():
    return Project(name="demo", style=StyleAnchor(),
                   story_text="林夏登场。林夏遇敌。林夏胜利。",
                   characters=[Character(name="LinXia", description_en="x")],
                   scenes=[], next_scene_seq=1)


def _save(tmp_path, proj):
    ProjectStore(tmp_path).save(proj)
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


def _scenes_rev_of_ids(ids):
    from types import SimpleNamespace
    return _scenes_token([SimpleNamespace(scene_id=i) for i in ids])


def _scene_rev(rt, scene_id):
    from src.agents.script_writer import _execute_story_read
    return _execute_story_read(rt, "demo", json.dumps({"scene_id": scene_id}), "u", "m")["revision"]


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


# ============ 1. read → 多次 create → 最终 list 检查 ============
def test_read_multi_create_final_check(tmp_path):
    _save(tmp_path, _empty_proj())
    rev0 = _scenes_rev_of_ids([])
    rev1 = _scenes_rev_of_ids(["s01"])
    rev2 = _scenes_rev_of_ids(["s01", "s02"])
    client = ScriptedClient([
        (None, [_tc("script.read", {})]),
        (None, [_tc("story.list", {})]),
        (None, [_tc("story.create", {"summary": "林夏登场。", "characters": ["LinXia"],
                                     "expected_scenes_revision": rev0}, "c1")]),
        (None, [_tc("story.create", {"summary": "林夏遇敌。", "characters": ["LinXia"],
                                     "expected_scenes_revision": rev1}, "c2")]),
        (None, [_tc("story.create", {"summary": "林夏胜利。", "characters": ["LinXia"],
                                     "expected_scenes_revision": rev2}, "c3")]),
        (None, [_tc("story.list", {})]),                    # 最终检查
        ("已创建 3 个故事段：s01、s02、s03，顺序正确。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把剧本拆成段"))

    assert res["created"] is True
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s02", "s03"]
    # 轨迹可读：含 read/list/create×3/list
    tools = [t["tool"] for t in res["trace"]]
    assert tools.count("story.create") == 3
    assert tools[-1] == "story.list"   # 收尾读
    # 最终回复与 canonical 一致
    assert "3 个故事段" in res["assistant_message"]


# ============ 2. read/list → update 多段 → move → 最终检查 ============
def test_update_multi_then_move(tmp_path):
    _save(tmp_path, _multi_proj())   # s01/s02/s03
    rt0 = _runtime(tmp_path, ScriptedClient([]))
    rev_s01 = _scene_rev(rt0, "s01")
    rev_s02 = _scene_rev(rt0, "s02")
    # move 用结构指纹：[s01,s02,s03]（update 不变结构指纹）
    rev_struct = _scenes_rev_of_ids(["s01", "s02", "s03"])
    rev_struct_after_move = _scenes_rev_of_ids(["s03", "s01", "s02"])
    client = ScriptedClient([
        (None, [_tc("script.read", {})]),
        (None, [_tc("story.list", {})]),
        # update s01（改摘要）— 用 s01 的段指纹
        (None, [_tc("story.update", {"scene_id": "s01", "expected_revision": rev_s01,
                                     "summary": "第一段整理后。"}, "u1")]),
        # update s02
        (None, [_tc("story.update", {"scene_id": "s02", "expected_revision": rev_s02,
                                     "summary": "第二段整理后。"}, "u2")]),
        # move s03 到最前（结构指纹 rev_struct）
        (None, [_tc("story.move", {"scene_id": "s03", "before_scene_id": "s01",
                                   "expected_scenes_revision": rev_struct}, "m1")]),
        (None, [_tc("story.list", {})]),
        ("已整理 s01/s02 并把 s03 移到最前，现共 3 段：s03、s01、s02。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "整理故事段：改 s01/s02 内容，把 s03 提前"))

    proj = ProjectStore(tmp_path).load()
    assert [s.scene_id for s in proj.scenes] == ["s03", "s01", "s02"]
    assert proj.scenes[1].summary == "第一段整理后。"
    assert proj.scenes[2].summary == "第二段整理后。"
    assert res["scene_updated"] is True and res["moved"] is True
    # 轨迹：两 update + 一 move 都 ok
    ok = [t for t in res["trace"] if t["result"] == "ok"]
    assert sum(1 for t in ok if t["tool"] == "story.update") == 2
    assert sum(1 for t in ok if t["tool"] == "story.move") == 1
    # 最终回复顺序与 canonical 一致
    assert "s03、s01、s02" in res["assistant_message"]


# ============ 3. create → update 新段 → move ============
def test_create_update_new_then_move(tmp_path):
    _save(tmp_path, _multi_proj())   # s01/s02/s03, next_seq=4
    rt0 = _runtime(tmp_path, ScriptedClient([]))
    rev_struct = _scenes_rev_of_ids(["s01", "s02", "s03"])      # create 前
    rev_struct_after_create = _scenes_rev_of_ids(["s01", "s02", "s03", "s04"])
    # create 后 s04 段指纹
    rev_s04 = _scene_rev(_runtime(tmp_path, ScriptedClient([])), "s04") if False else None
    # 由于 create 还没发生，需动态：先构造一次 create 拿 s04 的段指纹。这里直接用一个新项目状态。
    # 改用：先执行 create 拿真实 s04 段指纹（在脚本里第一段就是 create）。
    client = ScriptedClient([
        (None, [_tc("story.create", {"summary": "新增第四段。",
                                     "characters": ["LinXia"],
                                     "expected_scenes_revision": rev_struct}, "c1")]),
        # 现在 s04 已建；read 它拿段指纹
        (None, [_tc("story.read", {"scene_id": "s04"}, "r1")]),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    # 跑到这步拿真实 s04 段指纹
    _run(script_writer_turn(rt, "demo", sid, "先建 s04"))   # 消耗前两步
    rev_s04 = _scene_rev(_runtime(tmp_path, ScriptedClient([])), "s04")
    # 现在执行完整后续（update s04 + move s04 到最前）
    # 但 script 已耗尽——为隔离，重置：新会话
    client2 = ScriptedClient([
        (None, [_tc("story.read", {"scene_id": "s04"}, "r1")]),
        (None, [_tc("story.update", {"scene_id": "s04", "expected_revision": rev_s04,
                                     "summary": "第四段改写后。"}, "u1")]),
        (None, [_tc("story.move", {"scene_id": "s04", "before_scene_id": "s01",
                                   "expected_scenes_revision": rev_struct_after_create}, "m1")]),
        (None, [_tc("story.list", {})]),
        ("已新建并改写 s04，移到最前，现 4 段：s04、s01、s02、s03。", None),
    ])
    rt2 = _runtime(tmp_path, client2)
    sid2 = rt2.get_or_create_session("demo", "script_writer", "project", "demo2")["session_id"]
    res = _run(script_writer_turn(rt2, "demo", sid2, "改 s04 并提前"))

    proj = ProjectStore(tmp_path).load()
    assert [s.scene_id for s in proj.scenes] == ["s04", "s01", "s02", "s03"]
    assert proj.scenes[0].summary == "第四段改写后。"
    assert res["scene_updated"] is True and res["moved"] is True
    assert "s04、s01、s02、s03" in res["assistant_message"]


# ============ 4. update → delete → create → move ============
def test_update_delete_create_move(tmp_path):
    _save(tmp_path, _multi_proj())   # s01/s02/s03(有镜头)
    rt0 = _runtime(tmp_path, ScriptedClient([]))
    rev_s01 = _scene_rev(rt0, "s01")
    rev_struct = _scenes_rev_of_ids(["s01", "s02", "s03"])
    rev_struct_after_delete = _scenes_rev_of_ids(["s01", "s03"])
    rev_struct_after_create = _scenes_rev_of_ids(["s01", "s03", "s04"])
    client = ScriptedClient([
        # update s01
        (None, [_tc("story.update", {"scene_id": "s01", "expected_revision": rev_s01,
                                     "summary": "第一段改后。"}, "u1")]),
        # delete s02（两阶段：先预览）
        (None, [_tc("story.delete", {"scene_id": "s02"}, "d1")]),
        # 确认删除
        (None, [_tc("story.delete", {"scene_id": "s02", "confirmed": True}, "d2")]),
        # create s04（结构指纹此时=[s01,s03]）
        (None, [_tc("story.create", {"summary": "新增段。",
                                     "characters": ["LinXia"],
                                     "expected_scenes_revision": rev_struct_after_delete}, "c1")]),
        # move s04 到最前（结构指纹=[s01,s03,s04]）
        (None, [_tc("story.move", {"scene_id": "s04", "before_scene_id": "s01",
                                   "expected_scenes_revision": rev_struct_after_create}, "m1")]),
        (None, [_tc("story.list", {})]),
        ("已改 s01、删 s02、新增 s04 并提前，现 3 段：s04、s01、s03。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "改 s01，删 s02，新增并提前一段"))

    proj = ProjectStore(tmp_path).load()
    assert [s.scene_id for s in proj.scenes] == ["s04", "s01", "s03"]
    assert proj.scenes[1].summary == "第一段改后。"
    assert res["scene_updated"] and res["deleted"] and res["created"] and res["moved"]
    # 轨迹含四种写操作
    write_tools = {t["tool"] for t in res["trace"] if t["tool"].startswith(("story.", "script."))}
    assert {"story.update", "story.delete", "story.create", "story.move"} <= write_tools
    assert "s04、s01、s03" in res["assistant_message"]


# ============ 5. 中途 stale → Agent 重新读取后继续完成（不重复已成功段）============
def test_mid_stale_recovers_without_duplicate(tmp_path):
    _save(tmp_path, _empty_proj())
    rev0 = _scenes_rev_of_ids([])
    rev1 = _scenes_rev_of_ids(["s01"])
    rev2 = _scenes_rev_of_ids(["s01", "s02"])
    client = ScriptedClient([
        (None, [_tc("story.list", {})]),
        # s01 成功
        (None, [_tc("story.create", {"summary": "第一段。", "characters": ["LinXia"],
                                     "expected_scenes_revision": rev0}, "c1")]),
        # s02 用【过期 rev0】→ stale
        (None, [_tc("story.create", {"summary": "第二段。", "characters": ["LinXia"],
                                     "expected_scenes_revision": rev0}, "c_bad")]),
        # Agent 读最新 list（含 s01）拿到 rev1，重试 s02
        (None, [_tc("story.list", {})]),
        (None, [_tc("story.create", {"summary": "第二段。", "characters": ["LinXia"],
                                     "expected_scenes_revision": rev1}, "c2")]),
        (None, [_tc("story.list", {})]),
        ("已创建 2 段：s01、s02。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "建两段"))

    proj = ProjectStore(tmp_path).load()
    # s01 只一次（没因 stale 重复）
    assert [s.scene_id for s in proj.scenes] == ["s01", "s02"]
    assert proj.next_scene_seq == 3
    # 轨迹：一次 stale + 两次成功 create
    stale_t = [t for t in res["trace"] if t["result"] == "stale"]
    assert len(stale_t) == 1
    ok_create = [t for t in res["trace"] if t["tool"] == "story.create" and t["result"] == "ok"]
    assert len(ok_create) == 2


# ============ 6. 中途工具参数错误，Agent 自行修正 ============
def test_mid_arg_error_self_corrects(tmp_path):
    _save(tmp_path, _multi_proj())
    rev_struct = _scenes_rev_of_ids(["s01", "s02", "s03"])
    client = ScriptedClient([
        # 第一次 create 漏了 expected_scenes_revision → bad_arguments
        (None, [_tc("story.create", {"summary": "新段。", "characters": ["LinXia"]}, "c_bad")]),
        # Agent 看到错误 → 先 list 拿指纹，再正确调用
        (None, [_tc("story.list", {})]),
        (None, [_tc("story.create", {"summary": "新段。", "characters": ["LinXia"],
                                     "expected_scenes_revision": rev_struct}, "c1")]),
        (None, [_tc("story.list", {})]),
        ("已新增 s04，共 4 段。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "加一段"))

    proj = ProjectStore(tmp_path).load()
    assert [s.scene_id for s in proj.scenes] == ["s01", "s02", "s03", "s04"]
    # 轨迹：一次 error + 一次 ok create
    err_t = [t for t in res["trace"] if t["result"] == "error"]
    assert len(err_t) == 1
    assert "expected_scenes_revision" in (err_t[0]["error"] or "")
    ok_create = [t for t in res["trace"] if t["tool"] == "story.create" and t["result"] == "ok"]
    assert len(ok_create) == 1


# ============ 7. 删除触发已有依赖机制（级联删段内镜头）============
def test_delete_triggers_cascade(tmp_path):
    _save(tmp_path, _multi_proj())   # s03 有镜头 s03_c01
    client = ScriptedClient([
        (None, [_tc("story.delete", {"scene_id": "s03"}, "d1")]),       # 预览
        (None, [_tc("story.delete", {"scene_id": "s03", "confirmed": True}, "d2")]),
        (None, [_tc("story.list", {})]),
        ("已删除 s03（含其镜头），现 2 段：s01、s02。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "删掉第三段"))

    proj = ProjectStore(tmp_path).load()
    assert [s.scene_id for s in proj.scenes] == ["s01", "s02"]
    # 段内镜头也被级联删
    all_shots = [sh.shot_id for s in proj.scenes for sh in s.shots]
    assert "s03_c01" not in all_shots
    # 删除 receipt 含级联镜头
    del_r = next(r for r in res["receipts"] if r["operation"] == "story.delete" and r["ok"])
    # deleted 详情在原始 obs（receipts_meta 只保留摘要字段）——从 trace 不含镜头；
    # 用 audit 验证级联
    audit = DirectEditAuditStore(tmp_path, "demo").recent()
    assert audit and "s03" in str(audit[-1]["before"])
    assert res["deleted"] is True


# ============ 8. 超过原 8 turn 的长任务（混合多工具，>8 步）============
def test_long_task_exceeds_8_turns(tmp_path):
    """5 段拆分 + 中间 update + move + 最终 list = 明显 >8 步。"""
    _save(tmp_path, _empty_proj())
    revs = [_scenes_rev_of_ids([])]   # 初始
    ids = []
    script = [(None, [_tc("script.read", {})]), (None, [_tc("story.list", {})])]
    for i in range(5):
        revs.append(_scenes_rev_of_ids(ids + [f"s0{i+1}"]))
        script.append((None, [_tc("story.create",
                                  {"summary": f"段{i+1}。", "characters": ["LinXia"],
                                   "expected_scenes_revision": revs[i]}, f"c{i}")]))
        ids.append(f"s0{i+1}")
    # 最终 list
    script.append((None, [_tc("story.list", {})]))
    script.append(("已拆 5 段。", None))
    client = ScriptedClient(script)
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "拆成 5 段"))

    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ids
    assert len(client.calls) > 8   # read+list+5×create+list+final = 9
    assert "执行预算上限" not in res["assistant_message"]
    assert "5 段" in res["assistant_message"]


# ============ 9. 最终回复与 canonical 状态完全一致 ============
def test_final_reply_matches_canonical(tmp_path):
    _save(tmp_path, _multi_proj())
    rev_struct = _scenes_rev_of_ids(["s01", "s02", "s03"])
    client = ScriptedClient([
        (None, [_tc("story.create", {"summary": "新段。",
                                     "characters": ["LinXia"],
                                     "expected_scenes_revision": rev_struct}, "c1")]),
        (None, [_tc("story.list", {})]),
        ("已新增 s04，现共 4 段，顺序：s01、s02、s03、s04。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "加一段"))

    proj = ProjectStore(tmp_path).load()
    # 回复里声称的数量/顺序 = canonical 真实
    assert "4 段" in res["assistant_message"]
    assert "s01、s02、s03、s04" in res["assistant_message"]
    assert [s.scene_id for s in proj.scenes] == ["s01", "s02", "s03", "s04"]


# ============ 10. 不出现「工具没成功但宣称成功」（夸大→兜底）============
def test_no_false_success_on_overclaim(tmp_path):
    _save(tmp_path, _empty_proj())
    rev0 = _scenes_rev_of_ids([])
    client = ScriptedClient([
        (None, [_tc("story.list", {})]),
        # 只成功建了 1 段
        (None, [_tc("story.create", {"summary": "第一段。", "characters": ["LinXia"],
                                     "expected_scenes_revision": rev0}, "c1")]),
        (None, [_tc("story.list", {})]),
        # 却谎称建了 3 段
        ("已创建 3 个故事段：s01、s02、s03。", None),
        # 纠偏后仍嘴硬
        ("是的，3 段都建好了。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "建三段"))

    # canonical 真实只有 1 段
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01"]
    # 谎称 3 段被拦
    assert "3 个故事段" not in res["assistant_message"]
    assert "3 段都建好了" not in res["assistant_message"]


# ============ 11. 不出现连续重复同一无效操作导致失控 ============
def test_no_runaway_repeat_failure(tmp_path):
    """模型连续 3 次用同样错误参数调 story.create（漏 revision），防失控应停止重复并诚实告知。"""
    _save(tmp_path, _empty_proj())
    bad = _tc("story.create", {"summary": "段。", "characters": ["LinXia"]}, "cb")
    client = ScriptedClient([
        (None, [bad]), (None, [bad]), (None, [bad]),
        # 防失控触发后，模型应换思路或如实告知
        ("我连续失败，先看看当前状态。", None),
        (None, [_tc("story.list", {})]),
        ("当前没有故事段，创建因缺少版本指纹失败，需要先读取。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "加段"))

    # 没有 canonical 变更（坏调用都没成功）
    assert ProjectStore(tmp_path).load().scenes == []
    # 轨迹里 story.create error 出现但被防失控拦下（不无限重复）
    create_errs = [t for t in res["trace"] if t["tool"] == "story.create" and t["result"] == "error"]
    assert len(create_errs) <= 3   # 不超过 MAX_REPEAT_FAIL
    # 最终回复诚实（提到失败/需要读取，没谎称成功）
    assert "失败" in res["assistant_message"] or "缺少" in res["assistant_message"]


# ============ 12. 执行轨迹结构正确（turn→tool→target→result→revision）============
def test_trace_structure(tmp_path):
    _save(tmp_path, _empty_proj())
    rev0 = _scenes_rev_of_ids([])
    client = ScriptedClient([
        (None, [_tc("script.read", {})]),
        (None, [_tc("story.list", {})]),
        (None, [_tc("story.create", {"summary": "段。", "characters": ["LinXia"],
                                     "expected_scenes_revision": rev0}, "c1")]),
        (None, [_tc("story.list", {})]),
        ("已建 1 段：s01。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "建一段"))

    assert res["trace"]
    for t in res["trace"]:
        for k in ("step", "tool", "target", "result", "revision", "error", "ok"):
            assert k in t
    # create 那条 result=ok, target=s01, revision 非空
    create_t = next(t for t in res["trace"] if t["tool"] == "story.create")
    assert create_t["result"] == "ok"
    assert create_t["target"] == "s01"
    assert create_t["revision"]
    # meta 也带 trace
    assert "trace" in res["message"]["meta"]
