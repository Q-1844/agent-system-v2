"""Director N2 极小修正测试（2026-09-06 收口，不联网）。

真机证据（Producer V1 Phase 2 Case3 / Phase 3 Turn1）：Producer 委派 objective 含
"连续性"时，is_review_request 的裸词命中把执行任务改道进只读审查会话
（receipts=[]/trace=[] → n_tool_calls=0/verified=false），[执行性纠偏] 重发还二次命中。

修复语义：
  - Bridge 委派 envelope 且 用户意图/任务目标 行明确要求实际修改 → 走正常写 loop
    （Director 自主判断写或不写；无值得修改的问题可明确 no-change，不强制写）；
  - 纯检查/审查 envelope（无执行动词）仍走只读审查（Clean Review 契约不破坏）；
  - 用户直聊行为逐字节不变；
  - 审查 turn 的 trace 如实回传（n_tool_calls 与报告文本口径一致）。
"""
import json

from tests.test_director_agent import (
    _save_proj, _runtime, _run, _tool_call, _shots_rev, ScriptedClient,
)
from src.core.store import ProjectStore
from src.agents.director import _bridge_envelope_demands_execution


def _director():
    from src.agents.director import director_turn
    return director_turn


def _envelope(objective: str, user_intent: str, situation: str = "") -> str:
    return ("【来自 Producer 的委派任务】\n"
            f"用户意图：{user_intent}\n"
            f"任务目标：{objective}\n"
            f"背景情况：{situation}\n"
            "必须遵守的约束：\n- 以最新 canonical 为事实来源\n"
            "作用范围：场景：s01, s02")


# ============ 闸门单元行为 ============
def test_gate_detects_execution_intent_in_envelope():
    env = _envelope("修复 s01 分镜的连续性问题，并重新设计受影响的镜头",
                    "请检查后半段剧情节奏，把真正有问题的地方处理好，并同步受到影响的分镜。")
    assert _bridge_envelope_demands_execution(env)


def test_gate_ignores_review_only_envelope():
    env = _envelope("对全片分镜的连续性做一次全面审查，输出问题清单",
                    "帮我检查一下有没有连续性问题")
    assert not _bridge_envelope_demands_execution(env)


def test_gate_ignores_non_envelope_content():
    # 用户直聊（无 envelope 前缀）一律不命中——直聊审查路由行为不变
    assert not _bridge_envelope_demands_execution("修复全片连续性问题")
    assert not _bridge_envelope_demands_execution("")


def test_gate_ignores_situation_only_execution_words():
    """执行动词只在 用户意图/任务目标 行生效；situation 提及不改变路由。"""
    env = _envelope("对全片分镜做一次连续性审查，只输出问题清单",
                    "先审查再决定要不要改",
                    situation="Script Writer 刚修改了 s01/s02，请重新检查分镜是否需要修改")
    assert not _bridge_envelope_demands_execution(env)


# ============ 路由行为（真实 director_turn 链路）============
def test_execution_envelope_with_continuity_word_runs_write_loop(tmp_path):
    """objective 含 连续性 + 修复 → 不进审查路由，正常写 loop 真实写 canonical。"""
    _save_proj(tmp_path)
    shots = [{"description": "雨点砸在窗上，两人对坐", "characters": ["林小雨", "陈默"],
              "shot_size": "medium shot", "target_duration_s": 5},
             {"description": "林小雨低头搅动咖啡", "characters": ["林小雨"],
              "shot_size": "close-up", "target_duration_s": 4}]
    env = _envelope("修复 s02 的分镜连续性问题：s02 当前没有镜头，"
                    "请为其创建与最新剧情匹配的分镜序列",
                    "请检查后半段剧情节奏，把真正有问题的地方处理好，并同步受到影响的分镜。",
                    situation="Script Writer 已调整 s02 剧情，分镜需同步。")
    client = ScriptedClient([
        (None, [_tool_call("shot.list", {"scene_id": "s02"}, "d1")]),
        (None, [_tool_call("shot.create_many",
                           {"scene_id": "s02", "shots": shots,
                            "expected_shots_revision": _shots_rev(tmp_path)}, "d2")]),
        ("已为 s02 创建 2 个镜头，修复连续性问题。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(_director()(rt, "demo", sid, env))

    # 未被审查路由劫持：正常执行、真实写入
    assert res["intent"] != "continuity_review"
    assert "【连续性审查报告】" not in res["assistant_message"]
    assert len(res["receipts"]) >= 1
    assert res["trace"], "写 loop 的 trace 不应为空"
    proj = ProjectStore(tmp_path).load()
    assert [sh.shot_id for sh in proj.scenes[1].shots] == ["s02_c01", "s02_c02"]


def test_review_envelope_still_routes_to_review(tmp_path):
    """纯审查 envelope（无执行动词）仍走只读审查——Clean Review 契约不变。"""
    _save_proj(tmp_path)
    report = "问题清单：\n1. s01 两镜衔接正常。\n总体结论：结构完整。"
    env = _envelope("对 s01/s02 的分镜连续性做一次全面审查，只输出问题清单",
                    "帮我检查一下有没有连续性问题")
    client = ScriptedClient([
        (None, [_tool_call("story.list", {})]),
        (None, [_tool_call("shot.list", {})]),
        (report, None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(_director()(rt, "demo", sid, env))
    assert res["intent"] == "continuity_review"
    assert res["assistant_message"].startswith("【连续性审查报告】")
    assert res["receipts"] == []
    assert ProjectStore(tmp_path).load().story_text == "完整剧本正文。"


def test_review_turn_trace_honest_not_zero(tmp_path):
    """审查 turn 的 trace 如实回传（此前硬编码 [] → n_tool_calls 恒 0 的口径断裂）。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("story.list", {})]),
        (None, [_tool_call("shot.list", {})]),
        ("审查完成：未发现重复镜头。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(_director()(rt, "demo", sid, "做一次全片连续性审查"))
    assert res["intent"] == "continuity_review"
    assert len(res["trace"]) == 2                       # 两次真实读取
    assert res["events"][0]["tool_calls"] == len(res["trace"])
    # Producer 侧口径：_wrap_task_result 的 n_tool_calls 由此变诚实
    assert res["message"]["meta"]["trace"] == res["trace"]


def test_direct_user_chat_review_unchanged(tmp_path):
    """用户直聊"全片检查连续性"仍走审查（非 envelope，闸门不介入）。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.list", {})]),
        ("审查完成，无重大问题。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(_director()(rt, "demo", sid, "帮我全片检查一下连续性"))
    assert res["intent"] == "continuity_review"
