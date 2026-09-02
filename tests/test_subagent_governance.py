"""治理集成测试（mock 不联网）：扁平化计数恰好一次 / Clean Review 联动 /
reground 携带物条件扩展（空 = bit-for-bit）/ 实例遥测行。
"""
import asyncio
import json

import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents import director as D
from src.agents import session_governance as sg
from src.agents.subagent import tools as T
from src.agents.subagent import registry as reg
from src.agents.subagent import fingerprints as fp

from tests.test_director_agent import _tool_call, ScriptedClient


def make_proj(dirpath):
    s01 = [Shot(shot_id="s01_c01", scene_id="s01", index=1, description="外景"),
           Shot(shot_id="s01_c02", scene_id="s01", index=2, description="走廊",
                source_shot_id="s01_c01", opening_source="inherit_previous")]
    proj = Project(
        name="demo", style=StyleAnchor(), story_text="正文。",
        characters=[Character(name="林晚星", description_en="a keeper")],
        scenes=[Scene(scene_id="s01", summary="段一", characters=["林晚星"], shots=s01),
                Scene(scene_id="s02", summary="段二", characters=[], shots=[])],
        next_scene_seq=3)
    ProjectStore(dirpath).save(proj)
    return proj


class SC(ScriptedClient):
    pass


def rt_for(path, client=None, enabled=True):
    sp = path / "agents_data" / "settings.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps({"subagent_enabled": enabled}), encoding="utf-8")
    return AgentRuntime(project_dir_resolver=lambda name: path,
                        settings_path=sp, client=client)


@pytest.fixture(autouse=True)
def _restore():
    yield
    T.unmount_subagent_tools()
    reg.reset_turn_budget()


def _run(coro):
    return asyncio.run(coro)


def _latest_proposal_id(tmp_path):
    root = tmp_path / "output" / "agents" / "scene_agents" / "proposals"
    return json.loads(sorted(root.glob("prop_*.json"))[-1]
                      .read_text(encoding="utf-8"))["proposal_id"]


# ============ 扁平化计数恰好一次（勿双计） ============
def test_commit_counts_structural_writes_exactly_once(tmp_path):
    make_proj(tmp_path)
    basis = fp.scene_slice_fingerprint(ProjectStore(tmp_path).load().scenes[0])
    # 端态：删 c02（1 结构写）+ 新建 2 镜（1 次 create_many = 1 结构写）
    end_state = [
        {"shot_id": "s01_c01", "chain": False, "fields": {}},
        {"shot_id": None, "chain": True, "fields": {"description": "新A"}},
        {"shot_id": None, "chain": False, "fields": {"description": "新B"}}]
    client = SC([
        (None, [_tool_call("scene.delegate", {
            "scene_id": "s01", "task_brief": "重做",
            "signal": {"kind": "declared", "evidence": "大改"}})]),
        (None, [_tool_call("story.read", {"scene_id": "s01"})]),
        (None, [_tool_call("shot.list", {"scene_id": "s01"})]),
        (None, [_tool_call("proposal.submit", {
            "basis": {"scene_slice_fingerprint": basis},
            "end_state": end_state,
            "removed_shot_rationales": {"s01_c02": "删"},
            "design_notes": "x"})]),
        ("提案完成。", None),
        ("等待裁决。", None),
    ])
    rt = rt_for(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    _run(D.director_turn(rt, "demo", sid, "重做 s01"))
    pid = _latest_proposal_id(tmp_path)
    client.script = [
        (None, [_tool_call("proposal.commit", {
            "proposal_id": pid, "decision_rationale": "采纳"})]),
        ("已提交。", None),
    ]
    _run(D.director_turn(rt, "demo", sid, "采纳"))
    # 治理计数：delete(1) + create_many(1) = 2（proposal.commit 本身不计；
    # executed_ops 扁平化恰好一次）
    gov = sg.GovernanceStore(tmp_path, "demo")
    snap = gov.snapshot()
    assert snap["structural_since_review"] == 2, snap
    assert snap["structural_since_reground"] == 2
    # registry per-scene 计数：单一计费点（turn 收尾扁平化）——commit turn 的
    # delete + create_many 子回执各 +1 = 2；若 commit executor 内重复累加会是 4
    # （不双计回归哨兵）
    st = reg.SubagentStore(tmp_path, "demo")
    rec = st.scene_record("s01")
    assert rec["structural_since_delegation"] == 2, rec


# ============ Clean Review 联动自动成立 ============
def test_commit_triggers_clean_review_at_threshold(tmp_path):
    """大 commit（≥8 结构子操作）→ 既有自动审查触发（零新代码验证）。"""
    make_proj(tmp_path)
    # 预置 7 次结构写（阈值 8，差 1）
    gov = sg.GovernanceStore(tmp_path, "demo")
    for _ in range(7):
        gov.record_structural_writes([
            {"ok": True, "verified": True, "unchanged": False,
             "operation": "shot.create", "scene_id": "s01"}])
    basis = fp.scene_slice_fingerprint(ProjectStore(tmp_path).load().scenes[0])
    # 端态产生 ≥2 个结构子操作（delete + create_many）
    end_state = [
        {"shot_id": "s01_c01", "chain": False, "fields": {}},
        {"shot_id": None, "chain": True, "fields": {"description": "n1"}},
        {"shot_id": None, "chain": True, "fields": {"description": "n2"}}]
    client = SC([
        (None, [_tool_call("scene.delegate", {
            "scene_id": "s01", "task_brief": "重做",
            "signal": {"kind": "declared", "evidence": "大改"}})]),
        (None, [_tool_call("story.read", {"scene_id": "s01"})]),
        (None, [_tool_call("shot.list", {"scene_id": "s01"})]),
        (None, [_tool_call("proposal.submit", {
            "basis": {"scene_slice_fingerprint": basis},
            "end_state": end_state,
            "removed_shot_rationales": {"s01_c02": "删"},
            "design_notes": "x"})]),
        ("提案完成。", None),
        ("等待裁决。", None),
    ])
    rt = rt_for(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    _run(D.director_turn(rt, "demo", sid, "重做 s01"))
    pid = _latest_proposal_id(tmp_path)
    client.script = [
        (None, [_tool_call("proposal.commit", {
            "proposal_id": pid, "decision_rationale": "采纳"})]),
        ("已提交。", None),
        # 自动 Clean Review 是独立只读会话（脚本继续喂审查实例）
        (None, [_tool_call("story.list", {})]),
        (None, [_tool_call("shot.list", {})]),
        ("审查完成：无重大问题。", None),
    ]
    res = _run(D.director_turn(rt, "demo", sid, "采纳"))
    assert any(e.get("kind") == "continuity_review" for e in res["events"]), \
        "commit 计入 structural_since_review 后应触发既有自动审查"
    gov2 = sg.GovernanceStore(tmp_path, "demo")
    assert gov2.snapshot()["structural_since_review"] == 0   # 触发后清零


# ============ reground 携带物条件扩展 ============
def test_reground_seed_bit_for_bit_when_empty(tmp_path):
    """空全局记忆 → seed 逐字节与现行一致（无 subagent 段）。"""
    make_proj(tmp_path)
    rt = rt_for(tmp_path, client=None, enabled=False)
    store = rt._session_store("demo")
    s = store.get_or_create_conversation("director")
    notice = sg.reground_session(store, s.session_id, tmp_path, "demo",
                                  last_user_intent="继续")
    assert notice
    msgs = store.load(s.session_id).messages
    assert len(msgs) == 1
    content = msgs[0].content
    assert "全局导演决策" not in content


def test_reground_seed_includes_global_memory(tmp_path):
    make_proj(tmp_path)
    st = reg.SubagentStore(tmp_path, "demo")
    st.append_global_memory(reg.make_global_entry(
        "s01 提案 accept：保 B 弃 A（全片节奏需要）", proposal_id="prop_x"))
    rt = rt_for(tmp_path, client=None)
    store = rt._session_store("demo")
    s = store.get_or_create_conversation("director")
    sg.reground_session(store, s.session_id, tmp_path, "demo",
                        last_user_intent="继续")
    content = store.load(s.session_id).messages[0].content
    assert "全局导演决策" in content
    assert "保 B 弃 A" in content and "prop_x" in content


# ============ 实例遥测独立行 ============
def test_instance_telemetry_line(tmp_path):
    make_proj(tmp_path)
    basis = fp.scene_slice_fingerprint(ProjectStore(tmp_path).load().scenes[0])
    client = SC([
        (None, [_tool_call("scene.delegate", {
            "scene_id": "s01", "task_brief": "重做",
            "signal": {"kind": "declared", "evidence": "大改"}})]),
        (None, [_tool_call("shot.list", {"scene_id": "s01"})]),
        (None, [_tool_call("story.read", {"scene_id": "s01"})]),
        (None, [_tool_call("proposal.submit", {
            "basis": {"scene_slice_fingerprint": basis},
            "end_state": [{"shot_id": "s01_c01", "chain": False, "fields": {}},
                          {"shot_id": "s01_c02", "chain": True, "fields": {}}],
            "removed_shot_rationales": {}, "design_notes": "x"})]),
        ("提案完成。", None),
        ("等待裁决。", None),
    ])
    rt = rt_for(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    _run(D.director_turn(rt, "demo", sid, "重做 s01"))
    tel = tmp_path / "output" / "agents" / "telemetry" / "context_size.jsonl"
    assert tel.exists()
    lines = [json.loads(x) for x in tel.read_text(encoding="utf-8").splitlines()]
    inst = [l for l in lines if str(l.get("turn_id", "")).startswith("scene_agent_")]
    assert len(inst) == 1
    assert inst[0]["session"] != sid    # 实例会话独立成行
