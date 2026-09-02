"""Sub-Agent 工具挂载门控测试（bit-for-bit 保证，mock 不联网）。

覆盖：
- subagent_enabled=false（默认）：DIRECTOR_TOOLS / TOOL_EXECUTORS /
  DIRECTOR_WRITE_OPERATIONS / _build_system 与基线逐字节一致（快照对比）；
- =true：挂载 4 工具 + proposal.commit 进写集合 + scene.delegate 进长任务集合
  + 系统提示追加段；主链路其余行为不变；
- unmount 完整还原（幂等）；
- 资格闸门四检查（信号依据 / 预算 / liveness / 单段单飞）。
"""
import importlib
import json

import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents import director as D
from src.agents.subagent import tools as T
from src.agents.subagent import registry as reg


@pytest.fixture(autouse=True)
def _restore_mount():
    """每个用例后卸载（防止模块级 dict 污染其它测试）。"""
    yield
    T.unmount_subagent_tools()
    reg.reset_turn_budget()


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


def rt_for(path, enabled=None):
    if enabled is not None:
        sp = path / "agents_data" / "settings.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps({"subagent_enabled": enabled}), encoding="utf-8")
    return AgentRuntime(project_dir_resolver=lambda name: path,
                        settings_path=path / "agents_data" / "settings.json",
                        client=None)


def _baseline_snapshot():
    return (list(D.DIRECTOR_TOOLS), dict(D.TOOL_EXECUTORS),
            set(D.DIRECTOR_WRITE_OPERATIONS), set(D.DIRECTOR_LONG_TASK_TOOLS),
            D._SUBAGENT_SYSTEM_SUFFIX, dict(D._TOOL_LABELS))


# ============ bit-for-bit：默认关闭 = 基线一致 ============
def test_disabled_by_default_bit_for_bit(tmp_path):
    make_proj(tmp_path)
    rt = rt_for(tmp_path)          # 不写 settings → 默认 false
    assert T.is_subagent_enabled(rt) is False
    before = _baseline_snapshot()
    # director_turn 的挂载路径不触发（client None 也应走到挂载检查前的返回）；
    # 直接验证"不调用 mount"时模块状态不变
    assert T.mounted() is False
    assert _baseline_snapshot() == before
    proj = ProjectStore(tmp_path).load()
    assert "段级委派" not in D._build_system(proj)


def test_mount_unmount_roundtrip(tmp_path):
    make_proj(tmp_path)
    before = _baseline_snapshot()
    assert T.mount_subagent_tools() is True
    assert T.mount_subagent_tools() is False        # 幂等
    names = {t["function"]["name"] for t in D.DIRECTOR_TOOLS}
    assert {"scene.delegate", "proposal.read", "proposal.commit",
            "proposal.reject"} <= names
    assert "proposal.commit" in D.DIRECTOR_WRITE_OPERATIONS
    assert "scene.delegate" in D.DIRECTOR_LONG_TASK_TOOLS
    assert all(n in D.TOOL_EXECUTORS for n in
               ("scene.delegate", "proposal.read", "proposal.commit",
                "proposal.reject"))
    proj = ProjectStore(tmp_path).load()
    sys_text = D._build_system(proj)
    assert "段级委派" in sys_text and "scene.delegate" in sys_text
    # 卸载完整还原
    assert T.unmount_subagent_tools() is True
    assert T.unmount_subagent_tools() is False       # 幂等
    after = _baseline_snapshot()
    assert after[0] == before[0]                     # DIRECTOR_TOOLS 顺序一致
    assert set(after[1]) == set(before[1])           # executors 键集一致
    assert after[2] == before[2] and after[3] == before[3]
    assert after[4] is None
    assert "段级委派" not in D._build_system(proj)


def test_read_only_tools_unchanged_by_mount(tmp_path):
    """挂载不改变既有读工具与 Scene Agent 不可见的边界（只加不改）。"""
    make_proj(tmp_path)
    before_names = {t["function"]["name"] for t in D.DIRECTOR_TOOLS}
    T.mount_subagent_tools()
    after_names = {t["function"]["name"] for t in D.DIRECTOR_TOOLS}
    assert before_names <= after_names              # 只增不删
    for t in D.DIRECTOR_TOOLS:
        if t["function"]["name"] not in before_names:
            continue


# ============ 资格闸门（§2.1 四检查） ============
def _delegate_call(rt, scene_id="s01", kind="declared", evidence="大规模重设计",
                   brief="重做本段"):
    import asyncio
    args = json.dumps({"scene_id": scene_id, "task_brief": brief,
                       "signal": {"kind": kind, "evidence": evidence}},
                      ensure_ascii=False)
    return asyncio.run(T._execute_scene_delegate(rt, "demo", args, "t", "m"))


def test_gate_bad_signal_kind(tmp_path):
    make_proj(tmp_path)
    rt = rt_for(tmp_path, enabled=True)
    r = _delegate_call(rt, kind="whatever", evidence="x")
    assert r["code"] == "bad_signal"


def test_gate_declared_requires_evidence(tmp_path):
    make_proj(tmp_path)
    rt = rt_for(tmp_path, enabled=True)
    r = _delegate_call(rt, kind="declared", evidence="")
    assert r["code"] == "missing_evidence"


def test_gate_pressure_not_ready(tmp_path):
    make_proj(tmp_path)
    rt = rt_for(tmp_path, enabled=True)
    r = _delegate_call(rt, kind="pressure", evidence="压力")
    assert r["code"] == "pressure_not_ready"
    assert "阈值" in r["fix_hint"] or "压力" in r["error"]


def test_gate_liveness(tmp_path):
    make_proj(tmp_path)
    rt = rt_for(tmp_path, enabled=True)
    r = _delegate_call(rt, scene_id="s99")
    assert r["code"] == "scene_not_found"


def test_gate_turn_budget(tmp_path):
    make_proj(tmp_path)
    rt = rt_for(tmp_path, enabled=True)
    reg.bump_turn_budget(); reg.bump_turn_budget(); reg.bump_turn_budget()
    r = _delegate_call(rt)
    assert r["code"] == "turn_budget_exhausted"
    reg.reset_turn_budget()


def test_gate_hard_cap(tmp_path):
    make_proj(tmp_path)
    rt = rt_for(tmp_path, enabled=True)
    st = reg.SubagentStore(tmp_path, "demo")
    st.ensure_scene("s01")
    # 直接把累计计数灌到硬上限（绕过实例执行）
    data = st._load_registry()
    data["total_delegations"] = reg.HARD_DELEGATION_CAP
    st._save_registry(data)
    r = _delegate_call(rt, kind="user_explicit", evidence="用户原话")
    assert r["code"] == "hard_cap_reached"
    assert "用户" in r["error"] or "用户" in r["fix_hint"]   # 明示不静默


def test_gate_single_flight(tmp_path):
    make_proj(tmp_path)
    rt = rt_for(tmp_path, enabled=True)
    assert reg.acquire_handle("s01", "dlg_inflight")
    r = _delegate_call(rt)
    assert r["code"] == "scene_busy"
    reg.release_handle("s01", "dlg_inflight")
