"""AI-7D-R · 批量写工具 story.delete_many / story.insert_many 测试。

设计约束（与需求逐条对应）：
  delete_many：
    1. 一次删除原第 1/5/7/9 段：一次 story.list 解析 position→scene_id + 一次 delete_many
    2. 删除后 order/position 正确重排（position 始终连续）
    3. 任一 scene_id 不存在 → 整批不删除（atomic）
    4. stale revision → 整批拒绝
    5. 重复 scene_id → 拒绝
    6. atomic 无半成功（含镜头级联：要删的段镜头随段走，幸存段镜头不动）
  insert_many：
    1. 多个不同锚点一次插入成功
    2. 同一锚点一次插入多段，scenes[] 顺序保持
    3. 所有锚点按调用前 snapshot 解释（相邻锚点落同一间隙时序确定）
    4. 无效锚点 → 整批不写入
    5. stale revision → 整批拒绝
    6. atomic 无半成功（第二项 spec 非法 → 一段都不插）
    7. insert 后最终 order/position 正确
    8. 与 create_many 单锚点场景职责不冲突（单锚点多段仍走 create_many；
       同锚点多 insert 项被拒；before/after 二选一）
  另：e2e turn（list→批量工具→list→总结）+ 单写串行 fence（批量工具占一个写槽）+
      stringify 容错（provider 怪癖）。
"""
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.script_writer import (
    script_writer_turn, _scenes_token,
    _execute_story_list, _execute_story_delete_many, _execute_story_insert_many,
    _execute_story_create_many,
)


# ============ fixtures ============
def _proj(n=9, with_shots=()):
    scenes = []
    for i in range(1, n + 1):
        sid = f"s{i:02d}"
        shots = []
        if i in with_shots:
            shots = [Shot(shot_id=f"{sid}_c01", scene_id=sid, index=1,
                          description=f"{sid}镜头", characters=["LinXia"],
                          target_duration_s=3)]
        scenes.append(Scene(scene_id=sid, summary=f"第{i}段",
                            characters=["LinXia"], shots=shots))
    return Project(name="demo", style=StyleAnchor(), story_text="剧本正文。",
                   characters=[Character(name="LinXia", description_en="x")],
                   scenes=scenes, next_scene_seq=n + 1)


def _save(tmp_path, n=9, with_shots=()):
    ProjectStore(tmp_path).save(_proj(n, with_shots=with_shots))
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


def _list_rev(rt):
    """模拟 Agent：一次 story.list 拿快照（position/scene_id/结构指纹）。"""
    obs = _execute_story_list(rt, "demo", "{}", "u", "m")
    assert obs["ok"]
    return obs


def _ids_by_positions(list_obs, positions):
    """把用户说的「第N段」（position，1-based）解析成稳定 scene_id。"""
    by_pos = {s["position"]: s["scene_id"] for s in list_obs["scenes"]}
    return [by_pos[p] for p in positions]


def _disk_ids(tmp_path):
    return [s.scene_id for s in ProjectStore(tmp_path).load().scenes]


# ================================================================
# delete_many
# ================================================================

# ---- 1. 一次 list 解析 + 一次 delete_many 删除原第 1/5/7/9 段 ----
def test_delete_many_positions_1_5_7_9(tmp_path):
    _save(tmp_path, n=10, with_shots=(1, 5, 7, 9))
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    targets = _ids_by_positions(snap, [1, 5, 7, 9])
    assert targets == ["s01", "s05", "s07", "s09"]

    obs = _execute_story_delete_many(
        rt, "demo",
        json.dumps({"scene_ids": targets,
                    "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
        "instr", "m1")

    assert obs["ok"] and obs["verified"]
    assert obs["count"] == 4
    assert [d["scene_id"] for d in obs["deleted"]] == ["s01", "s05", "s07", "s09"]
    # 级联：四个段各带 1 个镜头，共 4 个镜头被删
    assert sorted(obs["deleted_shots"]) == ["s01_c01", "s05_c01", "s07_c01", "s09_c01"]
    # canonical：幸存段且相对顺序保持
    assert _disk_ids(tmp_path) == ["s02", "s03", "s04", "s06", "s08", "s10"]
    # 新结构指纹返回且与盘上一致
    assert obs["scenes_revision"] == _scenes_token(ProjectStore(tmp_path).load().scenes)
    assert obs["scenes_revision"] != snap["revision"]


# ---- 2. 删除后 position 连续重排（id 跳号但 position 不跳）----
def test_delete_many_positions_reflow(tmp_path):
    _save(tmp_path, n=10)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    targets = _ids_by_positions(snap, [1, 5, 7, 9])
    obs = _execute_story_delete_many(
        rt, "demo",
        json.dumps({"scene_ids": targets,
                    "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
        "instr", "m1")
    assert obs["ok"] and obs["verified"]

    after = _list_rev(rt)
    assert after["count"] == 6
    # position 1..6 连续，依次对应幸存段
    assert [s["scene_id"] for s in after["scenes"]] == \
        ["s02", "s03", "s04", "s06", "s08", "s10"]
    assert [s["position"] for s in after["scenes"]] == [1, 2, 3, 4, 5, 6]
    # 新 list 的指纹与删除回执一致（Agent 下一次写用它）
    assert after["revision"] == obs["scenes_revision"]


# ---- 3. 任一 scene_id 不存在 → 整批不删除 ----
def test_delete_many_missing_scene_rejects_whole_batch(tmp_path):
    _save(tmp_path, n=6, with_shots=(1,))
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    before_bytes = (tmp_path / "output" / "project.json").read_bytes()
    try:
        _execute_story_delete_many(
            rt, "demo",
            json.dumps({"scene_ids": ["s01", "s99"],   # s99 不存在
                        "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
            "instr", "m1")
        assert False, "应拒绝不存在的 scene_id"
    except AgentError as e:
        assert e.code == "not_found" and "s99" in e.message
    # canonical 完全未动（s01 及其镜头都还在）
    assert _disk_ids(tmp_path) == ["s01", "s02", "s03", "s04", "s05", "s06"]
    proj = ProjectStore(tmp_path).load()
    assert len(proj.scenes[0].shots) == 1
    assert (tmp_path / "output" / "project.json").read_bytes() == before_bytes


# ---- 4. stale revision → 整批拒绝，不删除 ----
def test_delete_many_stale_rejected(tmp_path):
    _save(tmp_path, n=4)
    rt = _runtime(tmp_path)
    obs = _execute_story_delete_many(
        rt, "demo",
        json.dumps({"scene_ids": ["s01", "s02"],
                    "expected_scenes_revision": "deadbeef"}, ensure_ascii=False),
        "instr", "m1")
    assert not obs["ok"] and obs.get("stale") and obs["error"] == "stale_revision"
    assert obs["operation"] == "story.delete_many"
    # 拒绝时附带当前最新列表，供 Agent 重新解析
    assert {s["scene_id"] for s in obs["current_scenes"]} == {"s01", "s02", "s03", "s04"}
    assert _disk_ids(tmp_path) == ["s01", "s02", "s03", "s04"]


# ---- 5. 重复 scene_id → 拒绝 ----
def test_delete_many_duplicate_id_rejected(tmp_path):
    _save(tmp_path, n=4)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    try:
        _execute_story_delete_many(
            rt, "demo",
            json.dumps({"scene_ids": ["s01", "s01", "s02"],
                        "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
            "instr", "m1")
        assert False, "应拒绝重复 scene_id"
    except AgentError as e:
        assert e.code == "bad_arguments" and "重复" in e.message
    assert _disk_ids(tmp_path) == ["s01", "s02", "s03", "s04"]


def test_delete_many_bad_args(tmp_path):
    """空数组 / 非字符串 id / 缺 revision → bad_arguments，不写盘。"""
    _save(tmp_path, n=3)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    bad_payloads = [
        {"scene_ids": [], "expected_scenes_revision": snap["revision"]},
        {"scene_ids": ["s01", 3], "expected_scenes_revision": snap["revision"]},
        {"scene_ids": ["s01"]},
        {"scene_ids": "s01", "expected_scenes_revision": snap["revision"]},
    ]
    for payload in bad_payloads:
        try:
            _execute_story_delete_many(rt, "demo",
                                       json.dumps(payload, ensure_ascii=False), "u", "m")
            assert False, f"应拒绝参数: {payload}"
        except AgentError as e:
            assert e.code == "bad_arguments", payload
    assert _disk_ids(tmp_path) == ["s01", "s02", "s03"]


# ---- 6. atomic：非法项不产生半删除；幸存段镜头不受影响 ----
def test_delete_many_atomic_no_half_delete(tmp_path):
    _save(tmp_path, n=6, with_shots=(2, 3, 6))
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    # s02/s03 合法但与不存在的 s88 同批 → 一个都不能删
    try:
        _execute_story_delete_many(
            rt, "demo",
            json.dumps({"scene_ids": ["s02", "s03", "s88"],
                        "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
            "instr", "m1")
        assert False
    except AgentError as e:
        assert e.code == "not_found"
    proj = ProjectStore(tmp_path).load()
    assert [s.scene_id for s in proj.scenes] == ["s01", "s02", "s03", "s04", "s05", "s06"]
    # 镜头级联未发生：s02/s03/s06 的镜头都还在，且 index 连续
    for sid in ("s02", "s03", "s06"):
        sc = next(s for s in proj.scenes if s.scene_id == sid)
        assert len(sc.shots) == 1 and sc.shots[0].shot_id == f"{sid}_c01"


# ================================================================
# insert_many
# ================================================================

# ---- 1. 多个不同锚点一次插入（after s02→A；before s05→B,C；after s08→D）----
def test_insert_many_multiple_anchors(tmp_path):
    _save(tmp_path, n=9)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    obs = _execute_story_insert_many(
        rt, "demo",
        json.dumps({"inserts": [
            {"after_scene_id": "s02", "scenes": [{"summary": "A", "characters": ["LinXia"]}]},
            {"before_scene_id": "s05", "scenes": [{"summary": "B"}, {"summary": "C"}]},
            {"after_scene_id": "s08", "scenes": [{"summary": "D"}]},
        ], "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
        "instr", "m1")

    assert obs["ok"] and obs["verified"]
    assert obs["count"] == 4
    # id 按最终顺序连续分配
    assert [c["scene_id"] for c in obs["inserted"]] == ["s10", "s11", "s12", "s13"]
    # 最终顺序：所有锚点按调用前快照解释
    assert _disk_ids(tmp_path) == [
        "s01", "s02", "s10", "s03", "s04", "s11", "s12",
        "s05", "s06", "s07", "s08", "s13", "s09"]
    # 回执里每个新段带最终 order 与锚点关系
    by_id = {c["scene_id"]: c for c in obs["inserted"]}
    assert by_id["s10"]["order"] == 3 and by_id["s10"]["anchor"] == "s02" \
        and by_id["s10"]["anchor_side"] == "after"
    assert by_id["s11"]["order"] == 6 and by_id["s11"]["anchor"] == "s05" \
        and by_id["s11"]["anchor_side"] == "before"
    assert by_id["s12"]["order"] == 7
    assert by_id["s13"]["order"] == 12 and by_id["s13"]["anchor"] == "s08"
    # 字段落盘
    proj = ProjectStore(tmp_path).load()
    assert next(s for s in proj.scenes if s.scene_id == "s10").summary == "A"
    assert next(s for s in proj.scenes if s.scene_id == "s10").characters == ["LinXia"]
    assert [next(s for s in proj.scenes if s.scene_id == i).summary
            for i in ("s11", "s12", "s13")] == ["B", "C", "D"]
    assert obs["scenes_revision"] == _scenes_token(proj.scenes)


# ---- 2. 同一锚点一次插入多段，scenes[] 顺序严格保持 ----
def test_insert_many_same_anchor_multiple_scenes_order(tmp_path):
    _save(tmp_path, n=4)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    obs = _execute_story_insert_many(
        rt, "demo",
        json.dumps({"inserts": [
            {"after_scene_id": "s02", "scenes": [
                {"summary": "A"}, {"summary": "B"}, {"summary": "C"}]}],
            "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
        "instr", "m1")
    assert obs["ok"] and obs["verified"] and obs["count"] == 3
    assert _disk_ids(tmp_path) == ["s01", "s02", "s05", "s06", "s07", "s03", "s04"]
    proj = ProjectStore(tmp_path).load()
    assert [s.summary for s in proj.scenes[2:5]] == ["A", "B", "C"]
    assert [c["order"] for c in obs["inserted"]] == [3, 4, 5]


# ---- 3. 锚点按调用前 snapshot 解释：相邻锚点落同一间隙，顺序确定 ----------
# after s02 块 与 before s03 块 都落在 s02|s03 间隙；线性走查规则 =>
# s02 → [after s02 块] → [before s03 块] → s03。
# 若错误地按「插一个之后的新位置」顺序解释，before s03 会插到新段之后。
def test_insert_many_anchors_interpreted_against_snapshot(tmp_path):
    _save(tmp_path, n=4)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    obs = _execute_story_insert_many(
        rt, "demo",
        json.dumps({"inserts": [
            {"after_scene_id": "s02", "scenes": [{"summary": "A"}]},
            {"before_scene_id": "s03", "scenes": [{"summary": "B"}]},
        ], "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
        "instr", "m1")
    assert obs["ok"] and obs["verified"]
    # after 块（s05=A）紧贴 s02，before 块（s06=B）紧贴 s03
    assert _disk_ids(tmp_path) == ["s01", "s02", "s05", "s06", "s03", "s04"]

    # 更早锚点插入大块也不影响后续锚点解析（id 锚定，不依赖裸 position）
    _save(tmp_path, n=4)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    obs = _execute_story_insert_many(
        rt, "demo",
        json.dumps({"inserts": [
            {"before_scene_id": "s02", "scenes": [{"summary": f"X{i}"} for i in range(3)]},
            {"after_scene_id": "s04", "scenes": [{"summary": "尾插"}]},
        ], "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
        "instr", "m2")
    assert obs["ok"]
    ids = _disk_ids(tmp_path)
    # s02 前 3 段，s04（快照最后一段）后 1 段
    assert ids == ["s01", "s05", "s06", "s07", "s02", "s03", "s04", "s08"]


# ---- 4. 无效锚点 → 整批不写入 ----
def test_insert_many_invalid_anchor_no_write(tmp_path):
    _save(tmp_path, n=4)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    before_bytes = (tmp_path / "output" / "project.json").read_bytes()
    try:
        _execute_story_insert_many(
            rt, "demo",
            json.dumps({"inserts": [
                {"after_scene_id": "s02", "scenes": [{"summary": "A"}]},
                {"before_scene_id": "s99", "scenes": [{"summary": "B"}]},
            ], "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
            "instr", "m1")
        assert False, "应拒绝不存在的锚点"
    except AgentError as e:
        assert e.code == "not_found" and "s99" in e.message
    assert _disk_ids(tmp_path) == ["s01", "s02", "s03", "s04"]
    assert (tmp_path / "output" / "project.json").read_bytes() == before_bytes


# ---- 5. stale revision → 整批拒绝 ----
def test_insert_many_stale_rejected(tmp_path):
    _save(tmp_path, n=4)
    rt = _runtime(tmp_path)
    obs = _execute_story_insert_many(
        rt, "demo",
        json.dumps({"inserts": [
            {"after_scene_id": "s02", "scenes": [{"summary": "A"}]}],
            "expected_scenes_revision": "deadbeef"}, ensure_ascii=False),
        "instr", "m1")
    assert not obs["ok"] and obs.get("stale") and obs["error"] == "stale_revision"
    assert obs["operation"] == "story.insert_many"
    assert _disk_ids(tmp_path) == ["s01", "s02", "s03", "s04"]


# ---- 6. atomic：任一 insert 项 spec 非法 → 整批不写入 ----
def test_insert_many_bad_spec_no_partial(tmp_path):
    _save(tmp_path, n=6)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    # 第 2 项的第 1 个 scene summary 为空
    try:
        _execute_story_insert_many(
            rt, "demo",
            json.dumps({"inserts": [
                {"after_scene_id": "s01", "scenes": [{"summary": "合法A"}]},
                {"before_scene_id": "s05", "scenes": [{"summary": "  "}, {"summary": "合法C"}]},
            ], "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
            "instr", "m1")
        assert False, "应拒绝空 summary"
    except AgentError as e:
        assert e.code == "bad_arguments"
    assert _disk_ids(tmp_path) == ["s01", "s02", "s03", "s04", "s05", "s06"]


def test_insert_many_bad_args(tmp_path):
    """空 inserts / before+after 同给 / 都不给 / 空 scenes / 缺 revision → bad_arguments。"""
    _save(tmp_path, n=4)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    rev = snap["revision"]
    bad_payloads = [
        {"inserts": [], "expected_scenes_revision": rev},
        {"inserts": [{"after_scene_id": "s02", "before_scene_id": "s03",
                      "scenes": [{"summary": "A"}]}], "expected_scenes_revision": rev},
        {"inserts": [{"scenes": [{"summary": "A"}]}], "expected_scenes_revision": rev},
        {"inserts": [{"after_scene_id": "s02", "scenes": []}],
         "expected_scenes_revision": rev},
        {"inserts": [{"after_scene_id": "s02", "scenes": [{"summary": "A"}]}]},
    ]
    for payload in bad_payloads:
        try:
            _execute_story_insert_many(rt, "demo",
                                       json.dumps(payload, ensure_ascii=False), "u", "m")
            assert False, f"应拒绝参数: {payload}"
        except AgentError as e:
            assert e.code == "bad_arguments", payload
    assert _disk_ids(tmp_path) == ["s01", "s02", "s03", "s04"]


# ---- 7. insert 后最终 order/position 正确 ----
def test_insert_many_final_positions(tmp_path):
    _save(tmp_path, n=5)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    obs = _execute_story_insert_many(
        rt, "demo",
        json.dumps({"inserts": [
            {"before_scene_id": "s01", "scenes": [{"summary": "开头"}]},
            {"after_scene_id": "s03", "scenes": [{"summary": "中"}]},
        ], "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
        "instr", "m1")
    assert obs["ok"]
    after = _list_rev(rt)
    assert after["count"] == 7
    assert [s["position"] for s in after["scenes"]] == [1, 2, 3, 4, 5, 6, 7]
    by_pos = {s["position"]: s["scene_id"] for s in after["scenes"]}
    # 最终顺序：s06(新,before s01), s01, s02, s03, s07(新,after s03), s04, s05
    assert by_pos[1] == "s06"
    assert by_pos[5] == "s07" and by_pos[6] == "s04"
    assert after["revision"] == obs["scenes_revision"]


# ---- 8a. 重复锚点（同批两项指向同一锚点）→ 拒绝 --------------------
def test_insert_many_duplicate_anchor_rejected(tmp_path):
    _save(tmp_path, n=4)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    try:
        _execute_story_insert_many(
            rt, "demo",
            json.dumps({"inserts": [
                {"after_scene_id": "s02", "scenes": [{"summary": "A"}]},
                {"after_scene_id": "s02", "scenes": [{"summary": "B"}]},
            ], "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
            "instr", "m1")
        assert False, "应拒绝重复锚点"
    except AgentError as e:
        assert e.code == "bad_arguments" and "s02" in e.message
    # before s02 + after s02 同批也算重复锚点（同一锚点多个项）
    try:
        _execute_story_insert_many(
            rt, "demo",
            json.dumps({"inserts": [
                {"before_scene_id": "s02", "scenes": [{"summary": "A"}]},
                {"after_scene_id": "s02", "scenes": [{"summary": "B"}]},
            ], "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
            "instr", "m2")
        assert False, "应拒绝 before/after 同锚点的重复项"
    except AgentError as e:
        assert e.code == "bad_arguments"
    assert _disk_ids(tmp_path) == ["s01", "s02", "s03", "s04"]


# ---- 8b. 职责边界：单锚点连续多段仍由 create_many 处理，行为不变 ----
def test_create_many_single_anchor_still_works(tmp_path):
    _save(tmp_path, n=4)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    obs = _execute_story_create_many(
        rt, "demo",
        json.dumps({"scenes": [{"summary": "A"}, {"summary": "B"}],
                    "after_scene_id": "s02",
                    "expected_scenes_revision": snap["revision"]}, ensure_ascii=False),
        "instr", "m1")
    assert obs["ok"] and obs["verified"] and obs["count"] == 2
    assert _disk_ids(tmp_path) == ["s01", "s02", "s05", "s06", "s03", "s04"]


# ---- 9. stringify 容错：provider 把 inserts/scenes 序列化成 JSON 字符串 ----
def test_insert_many_stringified_args(tmp_path):
    _save(tmp_path, n=3)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    inserts = [{"after_scene_id": "s01",
                "scenes": [{"summary": "串化", "characters": ["LinXia"]}]}]
    raw = json.dumps({"inserts": json.dumps(inserts, ensure_ascii=False),
                      "expected_scenes_revision": snap["revision"]})
    obs = _execute_story_insert_many(rt, "demo", raw, "instr", "m1")
    assert obs["ok"] and obs["verified"] and obs["count"] == 1
    proj = ProjectStore(tmp_path).load()
    assert proj.scenes[1].summary == "串化" and proj.scenes[1].characters == ["LinXia"]


def test_delete_many_stringified_args(tmp_path):
    _save(tmp_path, n=3)
    rt = _runtime(tmp_path)
    snap = _list_rev(rt)
    raw = json.dumps({"scene_ids": json.dumps(["s01", "s02"]),
                      "expected_scenes_revision": snap["revision"]})
    obs = _execute_story_delete_many(rt, "demo", raw, "instr", "m1")
    assert obs["ok"] and obs["verified"] and obs["count"] == 2
    assert _disk_ids(tmp_path) == ["s03"]


# ================================================================
# 端到端：script_writer_turn（list → 批量工具 → list → 总结）
# ================================================================
class _ScriptedClient:
    """流式 scripted client（与 test_ai_7d_r_batch_tools 同款）。"""
    def __init__(self, script):
        self.script = list(script)

    async def chat_completion_stream(self, messages, **kw):
        step = self.script.pop(0) if self.script else {"content": "完成。"}
        if step.get("tool_calls"):
            yield {"kind": "finish", "reason": "tool_calls", "content": None,
                   "tool_calls": step["tool_calls"], "usage": None}
        else:
            text = step.get("content", "")
            if text:
                yield {"kind": "content_delta", "delta": text}
            yield {"kind": "finish", "reason": "stop", "content": text,
                   "tool_calls": None, "usage": None}

    async def chat(self, messages, **kw):
        return ""


def _start_session(tmp_path, client):
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    return rt, sid


def test_e2e_delete_many_one_call(tmp_path):
    """端到端：模型 list 解析 → 一次 delete_many → list 核对 → 总结。"""
    _save(tmp_path, n=10, with_shots=(1, 5, 7, 9))
    # 初始快照指纹（scripted 静态参数，指纹确定性可预算）
    rev = _scenes_token(_proj(10, with_shots=(1, 5, 7, 9)).scenes)
    client = _ScriptedClient([
        {"tool_calls": [_tc("story.list", {})]},
        {"tool_calls": [_tc("story.delete_many", {
            "scene_ids": ["s01", "s05", "s07", "s09"],
            "expected_scenes_revision": rev})]},
        {"tool_calls": [_tc("story.list", {}, call_id="c3")]},
        {"content": "已删除第1、5、7、9段，当前剩余 6 段。"},
    ])
    rt, sid = _start_session(tmp_path, client)
    events = []
    res = _run(script_writer_turn(rt, "demo", sid, "把第1、5、7、9段删掉",
                                 event_cb=lambda e: events.append(e)))
    assert res["deleted"] is True
    assert res["intent"] == "story_delete"
    # 无串行拒绝、无失败工具事件
    failed = [e for e in events if e.get("type") == "tool.failed"]
    assert not failed, failed
    assert _disk_ids(tmp_path) == ["s02", "s03", "s04", "s06", "s08", "s10"]
    # 回执账本里 delete_many 是唯一成功写
    writes = [r for r in res["receipts"] if r["ok"] and r["verified"]]
    assert [r["operation"] for r in writes] == ["story.delete_many"]


def test_e2e_insert_many_one_call(tmp_path):
    """端到端：模型 list 解析锚点 → 一次 insert_many（3 锚点 4 段）→ list 核对 → 总结。"""
    _save(tmp_path, n=9)
    rev = _scenes_token(_proj(9).scenes)
    client = _ScriptedClient([
        {"tool_calls": [_tc("story.list", {})]},
        {"tool_calls": [_tc("story.insert_many", {
            "inserts": [
                {"after_scene_id": "s02", "scenes": [{"summary": "A"}]},
                {"before_scene_id": "s05", "scenes": [{"summary": "B"}, {"summary": "C"}]},
                {"after_scene_id": "s08", "scenes": [{"summary": "D"}]},
            ],
            "expected_scenes_revision": rev})]},
        {"tool_calls": [_tc("story.list", {}, call_id="c3")]},
        {"content": "已在 3 个位置插入新段，当前共 13 段。"},
    ])
    rt, sid = _start_session(tmp_path, client)
    events = []
    res = _run(script_writer_turn(rt, "demo", sid,
                                 "第2段后加A，第5段前加B和C，第8段后加D",
                                 event_cb=lambda e: events.append(e)))
    assert res["created"] is True
    assert res["intent"] == "story_create"
    assert not [e for e in events if e.get("type") == "tool.failed"], events
    assert _disk_ids(tmp_path) == [
        "s01", "s02", "s10", "s03", "s04", "s11", "s12",
        "s05", "s06", "s07", "s08", "s13", "s09"]
    writes = [r for r in res["receipts"] if r["ok"] and r["verified"]]
    assert [r["operation"] for r in writes] == ["story.insert_many"]


# ---- 单写串行 fence：批量工具整体占一个写槽，第二步写被拒 ----
def test_write_fence_batch_tools_count_as_one_slot(tmp_path):
    _save(tmp_path, n=5)
    rev = _scenes_token(_proj(5).scenes)
    # 同一步：insert_many + delete_many 两个写工具 → 只执行第 1 个
    client = _ScriptedClient([
        {"tool_calls": [
            _tc("story.insert_many", {
                "inserts": [{"after_scene_id": "s02",
                             "scenes": [{"summary": "A"}]}],
                "expected_scenes_revision": rev}, call_id="i1"),
            _tc("story.delete_many", {
                "scene_ids": ["s04"],
                "expected_scenes_revision": rev}, call_id="d1"),
        ]},
        {"tool_calls": [_tc("story.list", {}, call_id="c2")]},
        {"content": "已插入一段。"},
    ])
    rt, sid = _start_session(tmp_path, client)
    events = []
    res = _run(script_writer_turn(rt, "demo", sid, "批量操作",
                                 event_cb=lambda e: events.append(e)))
    # 第一个写（insert_many）落盘
    assert _disk_ids(tmp_path) == ["s01", "s02", "s06", "s03", "s04", "s05"]
    # 第二个写（delete_many）被串行 fence 拒绝：trace 里有一条 error 结果
    fence = [t for t in res["trace"]
             if t["tool"] == "story.delete_many" and t["result"] == "error"]
    assert fence, res["trace"]
    # s04 未被删
    assert "s04" in _disk_ids(tmp_path)
    # insert_many 成功
    assert res["created"] is True and not res["deleted"]
