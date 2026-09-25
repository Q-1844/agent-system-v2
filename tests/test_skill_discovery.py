# tests/test_skill_discovery.py
"""Skill System V1（40.md §十五）Phase B — Skill Discovery。

覆盖：无关任务不加载 continuity skill / continuity 任务找到正确 skill /
Agent 只看到属于自己的 view / 不加载不相关 skill / role-specific resource /
多 skill 不因 description 重叠乱选 / hint 内容不含未绑定 skill。
"""
from pathlib import Path

from src.agents.skill_system.registry import SkillRegistry
from src.agents.skill_system.runtime import SkillRuntime

BUILTIN = Path(__file__).resolve().parent.parent / "src" / "agents" / \
    "skill_system" / "builtin"


def _rt() -> SkillRuntime:
    return SkillRuntime(SkillRegistry(source_dirs=[BUILTIN]))


# ============ 无关任务不 hint / 相关任务命中 ============

def test_irrelevant_task_no_hint():
    rt = _rt()
    for text in ("把 s03 第2镜改成近景", "给这个角色换一套冬装",
                 "这段剧情节奏太快了帮我重写", "创建一个新地点叫山谷驿站",
                 "读一下项目配额", "把剧本正文压缩到一半"):
        assert rt.activation_hint("director", text) is None, text


def test_continuity_task_hints_calibration():
    rt = _rt()
    for text in ("对整条故事做连续性校准", "检查镜头链接缝质量",
                 "按尾帧接力逐镜生成并校准", "calibration the full story"):
        hint = rt.activation_hint("director", text)
        assert hint is not None, text
        assert "full-story-relay-calibration" in hint
        assert "skill.load" in hint


def test_bridge_task_hints_bridge_skill():
    rt = _rt()
    hint = rt.activation_hint("director", "这两镜之间断裂了，需要插桥做过渡")
    assert hint is not None and "keyframes-bridge-repair" in hint


def test_multi_skill_ranked_by_routing_priority():
    """描述同时命中两个 skill 时，按 Agent×Skill routing_priority 排序（至多 2 个）。"""
    rt = _rt()
    hint = rt.activation_hint("director", "整条故事校准中发生级联断缝，考虑插桥")
    assert hint is not None
    i_cal = hint.index("full-story-relay-calibration")
    i_bridge = hint.index("keyframes-bridge-repair")
    # director: calibration 100 > bridge 90 → calibration 在前
    assert i_cal < i_bridge


# ============ Agent 只看到属于自己的 view ============

def test_hint_respects_agent_view():
    rt = _rt()
    # scene_designer 无 bridge binding：即使任务提到桥也不 hint
    assert rt.activation_hint("scene_designer", "两镜断裂需要插桥") is None
    # producer 有 bridge binding（coordinator, 40）：hint bridge
    hint = rt.activation_hint("producer", "镜头链断裂需要插桥修复")
    assert hint is not None and "keyframes-bridge-repair" in hint
    # script_writer 只有 calibration
    hint2 = rt.activation_hint("script_writer", "帮我设计预编剧冻结方案")
    assert hint2 is not None and "full-story-relay-calibration" in hint2
    assert "keyframes-bridge-repair" not in hint2


def test_catalog_block_per_agent_view():
    rt = _rt()
    d = rt.catalog_block("director")
    assert "full-story-relay-calibration" in d and "keyframes-bridge-repair" in d
    assert "primary_executor" in d
    sc = rt.catalog_block("scene_designer")
    assert "full-story-relay-calibration" in sc
    assert "keyframes-bridge-repair" not in sc
    assert "conditional" in sc
    # 无绑定 agent 无 catalog
    assert rt.catalog_block("editor") == ""


# ============ load 视角与 role resource ============

def test_role_specific_resource_per_agent():
    rt = _rt()
    cases = {
        "director": "接缝测量",
        "producer": "coordinator",
        "script_writer": "预编剧冻结",
        "character_designer": "approved Look 是生产身份真相",
        "scene_designer": "条件参与",
    }
    for agent, anchor in cases.items():
        out = rt.load(agent, "full-story-relay-calibration", agent_tools=None)
        assert out["ok"], agent
        assert anchor in (out["role_instructions"] or ""), (agent, anchor)


def test_load_records_selection_reason(tmp_path):
    """40.md §十三：为什么某 skill 被选择——hint 记录触发证据，load 记录任务摘要。"""
    rt = _rt()
    rt.activation_hint("director", "检查接缝", tmp_path)
    rt.load("director", "full-story-relay-calibration", tmp_path,
            agent_tools=None, task_snippet="检查接缝")
    import json
    events = [json.loads(l) for l in
              (tmp_path / "output" / "agents" / "skills" / "trace.jsonl")
              .read_text(encoding="utf-8").splitlines()]
    assert events[0]["event"] == "hint"
    assert events[0]["detail"]["hinted"] == ["full-story-relay-calibration"]
    assert events[1]["event"] == "load"
    assert events[1]["detail"]["task"] == "检查接缝"
