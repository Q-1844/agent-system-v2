"""接力来源可写性（source_shot_id 改锚/解除）mock 测试（不联网）。

背景：Director Sub-Agent Part 1 架构检查发现的 BLOCKER——source_shot_id 此前
不在任何写白名单中，"删镜头但保留接力它的下游"（detach-before-delete）与
"新镜头间挂接接力"两类端态不可表达，唯一变通是删链重建（丢资产 + id 漂移）。

修复：structure_service.update_shot_relay（结构校验：存在/同段/无环/相邻）+
shot.update / shot.update_many 显式接线（save 前应用，失败整单拒绝）。
创建时显式拒绝 source_shot_id（新镜 id 未分配，先建后锚）。
"""
import pytest

from tests.test_director_agent import (
    _save_proj, _runtime, _run, _tool_call, _shots_rev, _shot_rev,
    ScriptedClient,
)
from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.director import director_turn, _shot_token, _shots_content_token


def _proj3():
    """接力测试富夹具：s01 = 3 镜接力链 c01←c02←c03；s02 空；s03 = 4 镜（c02 锚 c01）。"""
    s01 = [Shot(shot_id="s01_c01", scene_id="s01", index=1,
                description="雨夜灯塔外景", characters=["林晚星"]),
           Shot(shot_id="s01_c02", scene_id="s01", index=2,
                description="她冲进走廊", characters=["林晚星"],
                source_shot_id="s01_c01", opening_source="inherit_previous"),
           Shot(shot_id="s01_c03", scene_id="s01", index=3,
                description="楼梯间回望", characters=["林晚星"],
                source_shot_id="s01_c02", opening_source="inherit_previous")]
    s03 = [Shot(shot_id="s03_c01", scene_id="s03", index=1, description="码头全景"),
           Shot(shot_id="s03_c02", scene_id="s03", index=2, description="甲板特写",
                source_shot_id="s03_c01", opening_source="inherit_previous"),
           Shot(shot_id="s03_c03", scene_id="s03", index=3, description="缆绳松动"),
           Shot(shot_id="s03_c04", scene_id="s03", index=4, description="老水手抬头")]
    return Project(
        name="demo", style=StyleAnchor(), story_text="正文。",
        characters=[Character(name="林晚星", description_en="a keeper")],
        scenes=[Scene(scene_id="s01", summary="灯塔段", characters=["林晚星"], shots=s01),
                Scene(scene_id="s02", summary="空段", characters=[], shots=[]),
                Scene(scene_id="s03", summary="码头段", characters=[], shots=s03)],
        next_scene_seq=4)


def _save_proj3(tmp_path):
    ProjectStore(tmp_path).save(_proj3())
    return tmp_path


def _load(tmp_path):
    return ProjectStore(tmp_path).load()


# ============ S6 修复主体：detach-before-delete ============
def test_detach_then_delete_source_keeps_downstream(tmp_path):
    """删 s01_c01 但保留接力它的 s01_c02：先解除再删——下游存活、无级联。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.read", {"shot_id": "s01_c02"})]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c02",
                            "expected_revision": _shot_rev(tmp_path, "s01_c02"),
                            "source_shot_id": None})]),
        (None, [_tool_call("shot.delete", {"shot_id": "s01_c01"})]),
        ("已解除 s01_c02 的接力并删除 s01_c01。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "删掉第1镜，但第2镜保留"))
    assert res["deleted"] is True
    proj = _load(tmp_path)
    c02 = proj.get_shot("s01_c02")
    assert c02 is not None                       # 下游存活（旧路径会被级联删除）
    assert c02.source_shot_id is None            # 接力已解除
    assert c02.source_version is None
    assert proj.get_shot("s01_c01") is None


def test_reanchor_after_move(tmp_path):
    """改锚：先 move 到新来源之后，再 update source_shot_id（相邻性校验通过）。"""
    _save_proj3(tmp_path)
    # s01 = [c01, c02(锚c01), c03(锚c02)]；目标：c03 改锚 c01
    client = ScriptedClient([
        (None, [_tool_call("shot.move",
                           {"shot_id": "s01_c03", "after_shot_id": "s01_c01",
                            "expected_shots_revision": _shots_rev(tmp_path)})]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c03",
                            "expected_revision": _shot_rev(tmp_path, "s01_c03"),
                            "source_shot_id": "s01_c01"})]),
        ("已把 s01_c03 改锚到 s01_c01。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "把第3镜改为接力第1镜"))
    assert res["updated"] is True
    proj = _load(tmp_path)
    order = [sh.shot_id for sh in proj.scenes[0].shots]
    assert order == ["s01_c01", "s01_c03", "s01_c02"]
    assert proj.get_shot("s01_c03").source_shot_id == "s01_c01"


def test_reanchor_not_adjacent_rejected(tmp_path):
    """不相邻改锚被拒（先 move 的顺序约束）；canonical 零改动。"""
    _save_proj3(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.read", {"shot_id": "s01_c03"})]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c03",
                            "expected_revision": _shot_rev(tmp_path, "s01_c03"),
                            "source_shot_id": "s01_c01"})]),   # c02 在中间 → 不相邻
        ("收到，先调整位置。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "把第3镜改为接力第1镜"))
    # 工具层拒绝以错误回执返回（trace 可见），canonical 未变
    assert _load(tmp_path).get_shot("s01_c03").source_shot_id == "s01_c02"
    failed = [t for t in res["trace"] if t["tool"] == "shot.update" and not t["ok"]]
    assert failed and "不相邻" in (failed[0].get("error") or "")


def test_relay_validation_errors(tmp_path):
    """结构校验四连：不存在 / 跨段 / 自身 / 成环。"""
    from src.storyboard.structure_service import update_shot_relay, StructureError
    proj = _proj3()   # 不落盘，纯内存校验
    # 不存在
    with pytest.raises(StructureError):
        update_shot_relay(proj, "s01_c02", "s01_c99")
    # 跨段（s03 存在但不同段）
    with pytest.raises(StructureError):
        update_shot_relay(proj, "s01_c02", "s03_c01")
    # 自身
    with pytest.raises(StructureError):
        update_shot_relay(proj, "s01_c02", "s01_c02")
    # 成环：c01 改锚 c02（c02 已锚 c01）——先满足相邻：c01 在 c02 前不相邻，
    # 但环检测在相邻性之前，直接命中环
    with pytest.raises(StructureError, match="成环"):
        update_shot_relay(proj, "s01_c01", "s01_c02")
    # 幂等 detach：已无接力再解除 → changed=False
    r = update_shot_relay(proj, "s01_c01", None)
    assert r["changed"] is False


def test_relay_only_update_and_token_coverage(tmp_path):
    """纯接力修改（无内容字段）可单独执行；接力变更被内容指纹覆盖（OCC 保护）。"""
    _save_proj(tmp_path)
    tok_before = _shot_rev(tmp_path, "s01_c02")
    content_before = _shots_content_token(_load(tmp_path).scenes)
    client = ScriptedClient([
        (None, [_tool_call("shot.read", {"shot_id": "s01_c02"})]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c02",
                            "expected_revision": _shot_rev(tmp_path, "s01_c02"),
                            "source_shot_id": None})]),
        ("已解除 s01_c02 的接力。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "解除第2镜的接力"))
    assert res["updated"] is True
    assert res["receipts"][0]["changed"] == ["source_shot_id"]
    # 指纹变化：旧指纹重放同操作会被 stale 拒绝（OCC 覆盖接力域）
    assert _shot_rev(tmp_path, "s01_c02") != tok_before
    assert _shots_content_token(_load(tmp_path).scenes) != content_before


def test_relay_noop_unchanged(tmp_path):
    """接力值与当前一致 → unchanged，不写盘。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.read", {"shot_id": "s01_c02"})]),
        (None, [_tool_call("shot.update",
                           {"shot_id": "s01_c02",
                            "expected_revision": _shot_rev(tmp_path, "s01_c02"),
                            "source_shot_id": "s01_c01"})]),   # 与现值相同
        ("接力关系未变。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "确认第2镜的接力"))
    assert res["receipts"][0]["unchanged"] is True


def test_update_many_with_relay(tmp_path):
    """update_many 携带接力修改：批量原子（内容+改锚一起落盘）。"""
    _save_proj3(tmp_path)
    content_rev = _shots_content_token(_load(tmp_path).scenes)
    # s03_c02 已锚 s03_c01（相邻）→ 改描述 + 解除接力
    client = ScriptedClient([
        (None, [_tool_call("shot.update_many",
                           {"expected_revision": content_rev,
                            "updates": [
                                {"shot_id": "s03_c01", "description": "码头全景（黄昏）"},
                                {"shot_id": "s03_c02", "description": "甲板特写",
                                 "source_shot_id": None}]} )]),
        ("批量修改完成。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "第1镜改黄昏，第2镜解除接力"))
    assert res["updated"] is True
    proj = _load(tmp_path)
    assert proj.get_shot("s03_c01").description == "码头全景（黄昏）"
    assert proj.get_shot("s03_c02").source_shot_id is None
    # 回执分项可见接力修改
    r2 = next(r for r in res["receipts"][0]["results"] if r["shot_id"] == "s03_c02")
    assert "source_shot_id" in r2["changed"]


def test_create_with_relay_rejected(tmp_path):
    """创建时指定 source_shot_id → 显式拒绝（整批不写，原子性）。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.create_many",
                           {"scene_id": "s02",
                            "shots": [
                                {"description": "起手"},
                                {"description": "承接",
                                 "source_shot_id": "s01_c01"}],
                            "expected_shots_revision": _shots_rev(tmp_path)})]),
        ("收到，先创建再挂接。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn(rt, "demo", sid, "给第2段设计两个镜头，第二个接力第1段末镜"))
    assert _load(tmp_path).scenes[1].shots == []   # 整批未写
    failed = [t for t in res["trace"]
              if t["tool"] == "shot.create_many" and not t["ok"]]
    assert failed and "先创建" in (failed[0].get("error") or "")
