"""bit-for-bit 抽查 + 上下文经济（验收 3）。

- subagent_enabled=false：MD 一轮普通创作流（mock）与基线行为一致——
  client 可见工具列表不含 4 个新工具、canonical 正常变化、events/receipts
  口径与既有基线一致、主会话无 subagent 痕迹；
- 上下文经济（Part 1 §10.2）：proposal 全文不进 MD 主会话历史（只进
  proposal store；主会话只见结构化摘要）。
"""
import asyncio
import json

import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents import director as D
from src.agents.subagent import tools as T
from src.agents.subagent import registry as reg
from src.agents.subagent import fingerprints as fp

from tests.test_director_agent import _tool_call, _shots_rev, ScriptedClient


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


def rt_for(path, client=None, enabled=False):
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


def test_disabled_normal_flow_bit_for_bit(tmp_path):
    """开关关闭：一轮普通创作流（story.read → create_many → 收尾 list）——
    client 可见工具 = 基线 DIRECTOR_TOOLS（无 subagent 工具），行为口径不变。"""
    make_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("story.read", {"scene_id": "s02"})]),
        (None, [_tool_call("shot.list", {"scene_id": "s02"})]),
        (None, [_tool_call("shot.create_many", {
            "scene_id": "s02",
            "shots": [{"description": "雨落窗棂"}, {"description": "她抬头"}],
            "expected_shots_revision": _shots_rev(tmp_path)})]),
        (None, [_tool_call("shot.list", {"scene_id": "s02"})]),
        ("已为 s02 创建 2 个镜头。", None),
    ])
    rt = rt_for(tmp_path, client=client, enabled=False)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(D.director_turn(rt, "demo", sid, "给第2段设计分镜"))
    # 行为与基线一致：intent / events / canonical
    assert res["intent"] == "shot_create"
    kinds = [e["kind"] for e in res["events"]]
    assert "shot_create_many" in kinds
    assert len(ProjectStore(tmp_path).load().scenes[1].shots) == 2
    # client 可见工具列表 = 基线（无 subagent 工具）
    baseline_names = {t["function"]["name"] for t in D.DIRECTOR_TOOLS}
    for c in client.calls:
        names = {t["function"]["name"] for t in c["kw"]["tools"]}
        assert names == baseline_names
        assert not (names & {"scene.delegate", "proposal.read",
                             "proposal.commit", "proposal.reject"})
    # system 提示无 subagent 段
    sys_msg = client.calls[0]["messages"][0]["content"]
    assert "段级委派" not in sys_msg
    # 无任何 subagent 落盘痕迹
    assert not (tmp_path / "output" / "agents" / "scene_agents").exists()


def test_proposal_fulltext_not_in_main_history(tmp_path):
    """上下文经济（Part 1 §10.2）：proposal 全文只进 proposal store，
    MD 主会话只见结构化摘要（无 end_state 逐镜全文）。"""
    make_proj(tmp_path)
    basis = fp.scene_slice_fingerprint(ProjectStore(tmp_path).load().scenes[0])
    end_state = [
        {"shot_id": "s01_c01", "chain": False, "fields": {}, "rationale": "r1"},
        {"shot_id": "s01_c02", "chain": True, "fields": {},
         "rationale": "r2"}]
    client = ScriptedClient([
        (None, [_tool_call("scene.delegate", {
            "scene_id": "s01", "task_brief": "重做",
            "signal": {"kind": "declared", "evidence": "大改"}})]),
        (None, [_tool_call("story.read", {"scene_id": "s01"})]),
        (None, [_tool_call("shot.list", {"scene_id": "s01"})]),
        (None, [_tool_call("proposal.submit", {
            "basis": {"scene_slice_fingerprint": basis},
            "end_state": end_state, "removed_shot_rationales": {},
            "design_notes": "段级说明全文XX"})]),
        ("提案完成。", None),
        ("等待裁决。", None),
    ])
    rt = rt_for(tmp_path, client=client, enabled=True)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    _run(D.director_turn(rt, "demo", sid, "重做 s01"))
    # 主会话 assistant 消息（MD 视角）不含提案逐镜全文与 design_notes 全文
    main = rt.get_session("demo", sid)
    asst = [m["content"] for m in main["messages"] if m["role"] == "assistant"]
    combined = "\n".join(asst)
    assert "end_state" not in combined
    assert "段级说明全文XX" not in combined
    # MD 可见的 tool observation 含结构化摘要（proposal_id），但无逐镜全文
    tool_msgs = []
    for c in client.calls:
        for m in c["messages"]:
            if m.get("role") == "tool" and "scene.delegate" in str(m.get("content")):
                tool_msgs.append(m["content"])
    assert tool_msgs and "prop_" in tool_msgs[-1]
    assert "end_state" not in tool_msgs[-1]
    # 全文在 proposal store
    root = tmp_path / "output" / "agents" / "scene_agents" / "proposals"
    p = json.loads(sorted(root.glob("prop_*.json"))[-1].read_text(encoding="utf-8"))
    assert p["end_state"] == end_state


def test_interrupted_commit_resume_e2e(tmp_path):
    """D4 resume e2e：interrupted_commit 状态 + resume=true 显式恢复。"""
    make_proj(tmp_path)
    basis = fp.scene_slice_fingerprint(ProjectStore(tmp_path).load().scenes[0])
    end_state = [
        {"shot_id": "s01_c01", "chain": False,
         "fields": {"description": "外景（改）"}},
        {"shot_id": "s01_c02", "chain": True, "fields": {}}]
    client = ScriptedClient([
        (None, [_tool_call("scene.delegate", {
            "scene_id": "s01", "task_brief": "重做",
            "signal": {"kind": "declared", "evidence": "大改"}})]),
        (None, [_tool_call("story.read", {"scene_id": "s01"})]),
        (None, [_tool_call("shot.list", {"scene_id": "s01"})]),
        (None, [_tool_call("proposal.submit", {
            "basis": {"scene_slice_fingerprint": basis},
            "end_state": end_state, "removed_shot_rationales": {},
            "design_notes": "x"})]),
        ("提案完成。", None),
        ("等待裁决。", None),
    ])
    rt = rt_for(tmp_path, client=client, enabled=True)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    _run(D.director_turn(rt, "demo", sid, "重做 s01"))
    st = reg.SubagentStore(tmp_path, "demo")
    pid = st.list_delegations()[0]["proposal_id"]
    # 模拟 commit 中断（delegation 状态 → interrupted_commit；canonical 未动）
    d = st.load_delegation(st.list_delegations()[0]["delegation_id"])
    d["status"] = "interrupted_commit"
    st.save_delegation(d)
    # 不带 resume → resume_required（防"用户杀进程被自动续跑"）
    r1 = T._execute_proposal_commit(
        rt, "demo", json.dumps({"proposal_id": pid,
                                "decision_rationale": "恢复"},
                               ensure_ascii=False), "t", "m")
    assert r1["code"] == "resume_required"
    # resume=true → D4 对当前 canonical 重算 → 成功
    r2 = T._execute_proposal_commit(
        rt, "demo", json.dumps({"proposal_id": pid, "decision_rationale": "恢复",
                                "resume": True}, ensure_ascii=False), "t", "m")
    assert r2["ok"], r2
    assert ProjectStore(tmp_path).load().get_shot("s01_c01").description == "外景（改）"
    assert st.load_delegation(d["delegation_id"])["status"] == "committed"
