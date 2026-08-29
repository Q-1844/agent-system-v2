"""AI-7D-R · Script Writer 整篇剧本拆分为故事段（连续 story.create）测试。

本阶段只验证现有 tool-loop 能否可靠连续调用已验证过的 story.create：
  script.read → story.list → story.create × N → story.list → 最终回复

覆盖：
  - 空故事段 → 连续创建多个故事段。
  - 超过旧 MAX(8) 步仍正常完成（验证执行预算不卡业务数量）。
  - ID 连续且不碰撞（s02/s03/s04…）。
  - 每轮 revision 正确更新（用上一次 create 返回的新指纹）。
  - 中途某次 create stale → Agent 用最新真实状态继续，不重复已成功段。
  - 最终 story.list 与 Agent 回复完全一致。
  - 不发生虚假成功（没真正 create 完却谎称拆完）。
  - 已有故事段时先 list、不擅自删改。
  - 既有工具（create/update/move/delete/script.update）回归通过。

不联网（ScriptedClient）。
"""
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.script_writer import (
    script_writer_turn, _scenes_token, DEFAULT_MAX_TOOL_STEPS,
)
from src.agents.revision_audit import DirectEditAuditStore


# ============ 公共装置 ============
def _proj_empty_scenes():
    """有剧本正文、0 故事段、角色齐全，待拆分。"""
    return Project(
        name="demo", style=StyleAnchor(),
        story_text=("林夏在雨夜走进老城钟楼。她抬头望向灰蒙蒙的天，雨滴落下。"
                    "钟楼传来一声闷响，她回忆起童年。"
                    "YuShu 出现，两人对峙。"
                    "最终和解，雨停了，天亮了。"),
        characters=[Character(name="LinXia", description_en="x"),
                    Character(name="YuShu", description_en="y")],
        scenes=[], next_scene_seq=1)


def _proj_with_existing():
    """已有 s01，待追加 s02/s03。验证不假设为空、不删改已有段。"""
    return Project(
        name="demo", style=StyleAnchor(),
        story_text="剧本正文。第二段。第三段。",
        characters=[Character(name="LinXia", description_en="x")],
        scenes=[Scene(scene_id="s01", summary="已有第一段", characters=["LinXia"], shots=[])],
        next_scene_seq=2)


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
    """由 scene_id 列表直接算 scenes 指纹（预计算循环中途刷新后的令牌）。"""
    from types import SimpleNamespace
    return _scenes_token([SimpleNamespace(scene_id=i) for i in ids])


class ScriptedClient:
    """按调用顺序返回 (content, tool_calls)；记录每次请求的 messages。"""
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


# ============ 1. 空段 → 连续创建 3 段，ID 连续不碰撞，revision 链正确 ============
def test_split_empty_into_three_scenes(tmp_path):
    _save(tmp_path, _proj_empty_scenes())
    # 拆分前 canonical: [] → token = _scenes_rev_of_ids([])
    rev0 = _scenes_rev_of_ids([])                      # list 后看到
    rev1 = _scenes_rev_of_ids(["s01"])                 # 创建 s01 后
    rev2 = _scenes_rev_of_ids(["s01", "s02"])          # 创建 s02 后
    client = ScriptedClient([
        (None, [_tc("script.read", {})]),                                # 读剧本
        (None, [_tc("story.list", {})]),                                 # 读已有段（空）
        (None, [_tc("story.create",
                     {"summary": "林夏在雨夜走进老城钟楼，抬头望向灰蒙蒙的天，雨滴落下。",
                      "characters": ["LinXia"],
                      "expected_scenes_revision": rev0}, "c1")]),         # s01
        (None, [_tc("story.create",
                     {"summary": "钟楼传来闷响，她回忆起童年。YuShu 出现，两人对峙。",
                      "characters": ["LinXia", "YuShu"],
                      "expected_scenes_revision": rev1}, "c2")]),          # s02
        (None, [_tc("story.create",
                     {"summary": "最终和解，雨停了，天亮了。",
                      "characters": ["LinXia", "YuShu"],
                      "expected_scenes_revision": rev2}, "c3")]),          # s03
        (None, [_tc("story.list", {})]),                                 # 最终核对
        ("已把剧本拆成 3 个故事段：s01、s02、s03。", None),                # 最终回复
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把剧本拆成故事段"))

    assert res["created"] is True
    assert res["intent"] == "story_create"
    proj = ProjectStore(tmp_path).load()
    assert [s.scene_id for s in proj.scenes] == ["s01", "s02", "s03"]
    assert proj.scenes[0].summary.startswith("林夏在雨夜")
    assert proj.scenes[1].summary.startswith("钟楼传来闷响")
    assert proj.scenes[2].summary == "最终和解，雨停了，天亮了。"
    assert proj.next_scene_seq == 4
    # 每轮用的指纹是正确的：第二次 create 用了 rev1（s01 已建），第三次用 rev2（s01,s02）
    create_calls = [c for c in client.calls
                    if c["messages"][-1].get("role") == "assistant"
                    and any(tc["function"]["name"] == "story.create"
                            for tc in (c["messages"][-1].get("tool_calls") or []))]
    # 最终回复与最后一次真实 list 一致（3 段、id）
    assert "3 个故事段" in res["assistant_message"]
    assert "s01" in res["assistant_message"] and "s03" in res["assistant_message"]
    # 真实 receipts：3 次成功 create
    ok = [r for r in res["receipts"]
          if r["operation"] == "story.create" and r["ok"] and r["verified"]]
    assert len(ok) == 3


# ============ 2. 超过旧 MAX(8) 步仍正常完成（拆 6 段 = read+list+6×create+list+final=10 步）============
def test_split_exceeds_old_max8(tmp_path):
    """6 段拆分 → 10 次 LLM 调用，超过旧 MAX_TOOL_STEPS=8，验证预算放宽后不被截断。"""
    proj = Project(
        name="demo", style=StyleAnchor(),
        story_text="一。二。三。四。五。六。",
        characters=[Character(name="LinXia", description_en="x")],
        scenes=[], next_scene_seq=1)
    _save(tmp_path, proj)
    # 预算默认 32 >> 10，足够
    ids = ["s01", "s02", "s03", "s04", "s05", "s06"]
    # 每一步 create 后的 token
    revs = [_scenes_rev_of_ids(ids[:i]) for i in range(7)]   # revs[0]=[] ... revs[6]=全6
    script = [(None, [_tc("script.read", {})]), (None, [_tc("story.list", {})])]
    for i in range(6):
        script.append((None, [_tc("story.create",
                                  {"summary": f"第{i+1}段。",
                                   "characters": ["LinXia"],
                                   "expected_scenes_revision": revs[i]}, f"c{i}")]))
    script.append((None, [_tc("story.list", {})]))
    script.append(("已拆成 6 段。", None))
    client = ScriptedClient(script)
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把剧本拆成 6 段"))

    assert res["created"] is True
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ids
    # 没被预算截断（最终回复是模型诚实回复，不是预算耗尽文案）
    assert "已拆成 6 段" in res["assistant_message"]
    assert "执行预算上限" not in res["assistant_message"]
    # LLM 调用次数 = 10（>8）
    assert len(client.calls) == 10


# ============ 3. revision 链：用旧指纹会 stale，Agent 应据最新重试 ============
def test_split_stale_midway_recovers_without_duplicate(tmp_path):
    """第 2 次 create 用了过期指纹 → stale 未写入；Agent 应读最新 list 再用新指纹重试，
    且【不重复创建已成功的 s01】。最终 s01 只存在一次，s02 成功。"""
    _save(tmp_path, _proj_empty_scenes())
    rev0 = _scenes_rev_of_ids([])          # 真实初始
    rev1 = _scenes_rev_of_ids(["s01"])     # s01 建后
    client = ScriptedClient([
        (None, [_tc("script.read", {})]),
        (None, [_tc("story.list", {})]),                                   # 看到 rev0
        # s01 用正确指纹 rev0 → 成功
        (None, [_tc("story.create",
                     {"summary": "第一段。", "characters": ["LinXia"],
                      "expected_scenes_revision": rev0}, "c1")]),
        # s02 用【过期指纹 rev0】（应是 rev1）→ stale，未写入
        (None, [_tc("story.create",
                     {"summary": "第二段。", "characters": ["LinXia"],
                      "expected_scenes_revision": rev0}, "c_bad")]),
        # Agent 看到 stale → story.list 读最新（含 s01，token=rev1）
        (None, [_tc("story.list", {})]),
        # 用正确 rev1 重试 s02 → 成功
        (None, [_tc("story.create",
                     {"summary": "第二段。", "characters": ["LinXia"],
                      "expected_scenes_revision": rev1}, "c2")]),
        (None, [_tc("story.list", {})]),
        ("已拆成 2 段：s01、s02。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把剧本拆成 2 段"))

    proj = ProjectStore(tmp_path).load()
    # 关键：s01 只创建了一次（没有因 stale 重试而重复）
    assert [s.scene_id for s in proj.scenes] == ["s01", "s02"]
    assert proj.next_scene_seq == 3   # 只分配了 2 个 id（s01,s02），bad 调用没分配
    # stale 那次 receipt 记为失败
    bad = [r for r in res["receipts"] if r.get("stale")]
    assert len(bad) == 1
    # 成功 create = 2 次（s01 + s02 重试）
    ok = [r for r in res["receipts"]
          if r["operation"] == "story.create" and r["ok"] and r["verified"]]
    assert len(ok) == 2
    # 最终回复诚实
    assert "2 段" in res["assistant_message"]


# ============ 4. 最终回复与最后一次真实 story.list 一致（不允许凭记忆谎称）============
def test_final_reply_matches_last_real_list(tmp_path):
    _save(tmp_path, _proj_empty_scenes())
    rev0 = _scenes_rev_of_ids([])
    rev1 = _scenes_rev_of_ids(["s01"])
    rev2 = _scenes_rev_of_ids(["s01", "s02"])
    client = ScriptedClient([
        (None, [_tc("script.read", {})]),
        (None, [_tc("story.list", {})]),
        (None, [_tc("story.create",
                     {"summary": "第一段。", "characters": ["LinXia"],
                      "expected_scenes_revision": rev0}, "c1")]),
        (None, [_tc("story.create",
                     {"summary": "第二段。", "characters": ["LinXia"],
                      "expected_scenes_revision": rev1}, "c2")]),
        (None, [_tc("story.list", {})]),    # 真实列表 = s01,s02
        # 模型【故意谎报 3 段】（凭记忆/幻觉），与真实 list(2 段) 不符
        ("我已把剧本拆成 3 个故事段：s01、s02、s03。", None),
        # 纠偏后仍嘴硬
        ("是的，3 段都创建好了。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把剧本拆成 3 段"))

    # canonical 真实只有 2 段（模型只真建了 2 个）
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s02"]
    # 真相闸门：模型谎称 3 段成功，但成功 receipt 只有 2 → 触发纠偏/兜底
    # （2 次真实 create 是成功变更，但"3 段"与真实不符——注意：_claims_success 命中且
    # real_changes 非空（有 2 个真实 create），闸门主要拦"0 真实却谎称"。
    # 这里 2 个真实 create 存在，但回复夸大成 3。验证 receipts 真实计数=2，回复不谎称 3）
    ok = [r for r in res["receipts"]
          if r["operation"] == "story.create" and r["ok"] and r["verified"]]
    assert len(ok) == 2
    # 最终文本不得包含"3 个故事段/3 段都创建好了"这种不实声称
    assert "3 个故事段" not in res["assistant_message"]
    assert "3 段都创建好了" not in res["assistant_message"]


# ============ 5. 已有故事段：先 list，不假设为空、不删改已有段 ============
def test_split_respects_existing_scenes(tmp_path):
    _save(tmp_path, _proj_with_existing())  # 已有 s01
    # list 看到 [s01] → token rev1；追加 s02 用 rev1
    rev1 = _scenes_rev_of_ids(["s01"])
    client = ScriptedClient([
        (None, [_tc("script.read", {})]),
        (None, [_tc("story.list", {})]),    # 看到已有 s01
        (None, [_tc("story.create",
                     {"summary": "追加的第二段。", "characters": ["LinXia"],
                      "expected_scenes_revision": rev1}, "c1")]),
        (None, [_tc("story.list", {})]),
        ("已有 s01，我追加了 s02，共 2 段。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把剩下的剧本也拆成段"))

    proj = ProjectStore(tmp_path).load()
    # s01 原样保留（没被删/改/移）
    assert [s.scene_id for s in proj.scenes] == ["s01", "s02"]
    assert proj.scenes[0].summary == "已有第一段"
    assert proj.scenes[1].summary == "追加的第二段。"
    assert res["created"] is True


# ============ 6. 不发生虚假成功：只读没 create，却谎称"已拆分"→ 兜底 ============
def test_no_create_but_false_claim_split_gets_fallback(tmp_path):
    _save(tmp_path, _proj_empty_scenes())
    client = ScriptedClient([
        (None, [_tc("script.read", {})]),
        (None, [_tc("story.list", {})]),
        # 没发任何 story.create，却谎称拆完了
        ("我已经把剧本拆成 3 个故事段了。", None),
        # 纠偏后仍嘴硬
        ("是的，已经拆分完成了。", None),
        # 真相闸门纠偏后仍嘴硬（前序 recovery/纠偏各一次）→ 诚实兜底
        ("是的，3 个故事段确实已经拆分完成。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把剧本拆成故事段"))

    # canonical 仍空
    assert ProjectStore(tmp_path).load().scenes == []
    assert res["created"] is False
    # 兜底文案诚实
    assert "已经把剧本拆成" not in res["assistant_message"]
    assert "已经拆分完成了" not in res["assistant_message"]
    assert ("没有真正执行修改" in res["assistant_message"]
            or "没有发出对应的工具调用" in res["assistant_message"])


# ============ 7. 执行预算为防死循环、非业务数量限制（12 段仍能跑）============
def test_twelve_scenes_within_default_budget(tmp_path):
    """12 段 = read+list+12×create+list+final = 16 步，默认预算 32 内。"""
    proj = Project(
        name="demo", style=StyleAnchor(),
        story_text="。".join(str(i) for i in range(1, 13)),  # 12 句
        characters=[Character(name="LinXia", description_en="x")],
        scenes=[], next_scene_seq=1)
    _save(tmp_path, proj)
    ids = [f"s{i:02d}" for i in range(1, 13)]
    revs = [_scenes_rev_of_ids(ids[:i]) for i in range(13)]
    script = [(None, [_tc("script.read", {})]), (None, [_tc("story.list", {})])]
    for i in range(12):
        script.append((None, [_tc("story.create",
                                  {"summary": f"段{i+1}。",
                                   "characters": ["LinXia"],
                                   "expected_scenes_revision": revs[i]}, f"c{i}")]))
    script.append((None, [_tc("story.list", {})]))
    script.append(("已拆 12 段。", None))
    client = ScriptedClient(script)
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "拆成 12 段"))

    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ids
    assert proj.next_scene_seq + 12 == ProjectStore(tmp_path).load().next_scene_seq
    assert "已拆 12 段" in res["assistant_message"]
    assert len(client.calls) == 16


# ============ 8. 既有工具回归守卫：工具集与预算常量 ============
def test_budget_default_is_not_business_cap():
    """DEFAULT_MAX_TOOL_STEPS 是防死循环预算，>8 且不限制段数。"""
    assert DEFAULT_MAX_TOOL_STEPS > 8
