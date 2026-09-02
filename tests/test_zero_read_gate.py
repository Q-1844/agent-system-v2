"""Zero-read Gate（读域真相闸门）+ 回复 ID 存在性勘误 mock 测试（不联网）。

对应 V1 压力测试验收用例（_stress/STRESS_REPORT.md F2/F3）：
  - F2 复现：陈旧会话 + "把 s01 全部镜头完整读一遍"类请求 + 模型零读工具、引用
    不存在的 shot_id → gate 命中、注入一次 nudge、模型真实读取后再作答；
    若最终回复仍引用不存在的 shot_id → 确定性勘误块标出（不改写模型原文）。
  - 误伤用例：纯探讨轮（零工具、不引用具体镜头、不声称核对）→ 不得触发 nudge。
  - 豁免用例：本轮真实删除的镜头 ID 出现在总结里（"已删除 s01_c01"）→ 不勘误。
"""
import asyncio

from tests.test_director_agent import (
    _proj, _save_proj, _runtime, _run, _tool_call, ScriptedClient,
)
from src.agents import session_governance


# ============ 检测特征单元测试（read-required 回复信号 / ID 提取与校验）============
def test_is_read_required_reply_signals():
    # 引用 ≥2 个 shot_id（逐镜细节表特征）
    assert session_governance.is_read_required_reply(
        "s01_c01 雨中特写；s01_c02 两人对坐。")
    # 声称已核对/逐镜精读（即使不引用 id）
    assert session_governance.is_read_required_reply("我已逐镜精读，全部核对无误。")
    assert session_governance.is_read_required_reply("以下内容以工具返回为准。")
    # 否定语境不算声称
    assert not session_governance.reply_claims_reading("我还没有核对完。")
    assert not session_governance.reply_claims_reading("建议你逐镜检查一遍。")
    # 纯探讨：单个 scene_id（探讨轮常复述用户提到的段号）不触发
    assert not session_governance.is_read_required_reply("s02 这段适合慢节奏。")
    # 纯探讨：无 id、无声称
    assert not session_governance.is_read_required_reply("长镜头更合适，情绪更连贯。")


def test_extract_and_validate_cited_ids():
    text = "s01_c01 与 s05_c03 重复；s05_c03、s99 段问题更大，s01_c02 尚可。"
    assert session_governance.extract_cited_shot_ids(text) == ["s01_c01", "s05_c03", "s01_c02"]
    # scene 提取不吞 shot_id 里的段前缀（s01_c01 中的 s01 不算 scene 引用）
    assert session_governance.extract_cited_scene_ids(text) == ["s99"]
    block = session_governance.validate_cited_ids(
        text, scene_ids={"s01", "s02"}, shot_ids={"s01_c01", "s01_c02"},
        exempt=["s05_c03"])
    assert block is not None
    assert "s05_c03" not in block          # 豁免（本轮刚删除）
    assert "不存在的镜头" not in block or "s01_c01" not in block
    assert "s99" in block                  # 不存在的段被标出
    # 全部合法 → None
    ok = "已修改 s01_c01，保留 s01_c02。"
    assert session_governance.validate_cited_ids(
        ok, scene_ids={"s01"}, shot_ids={"s01_c01", "s01_c02"}) is None


# ============ F2 复现：zero-read gate 命中 → nudge 一次 → 模型真实读取 ============
def test_f2_zero_read_gate_nudge_then_real_read(tmp_path):
    _save_proj(tmp_path)
    hallucinated = (
        "【逐镜精读报告】我已逐镜核对 s01 全部镜头：\n"
        "- s01_c03：林小雨用红围巾包扎手臂（从未存在的剧情）\n"
        "- s01_c04：陈默望向窗外\n")
    client = ScriptedClient([
        (hallucinated, None),                                    # 零工具全幻觉
        (None, [_tool_call("shot.list", {"scene_id": "s01"})]),  # nudge 后真实读取
        ("经真实读取，s01 共 2 个镜头：s01_c01、s01_c02，无 c03/c04。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn_req(rt, "demo", sid, "把 s01 全部镜头一个一个完整读一遍"))
    # nudge 注入恰好一次：最后一次 chat_completion 的完整消息里只有一条 zero-read 提示
    assert len(client.calls) == 3
    nudge_msgs = [m for m in client.calls[-1]["messages"]
                  if m.get("role") == "user"
                  and "零次读工具调用" in (m.get("content") or "")]
    assert len(nudge_msgs) == 1
    # 最终回复基于真实读取，引用的全是存在的 id → 无勘误
    assert "s01_c01" in res["assistant_message"]
    assert "【系统勘误】" not in res["assistant_message"]


def test_f2_errata_when_final_reply_cites_nonexistent_ids(tmp_path):
    """nudge 后模型仍不读取、最终回复引用不存在的 shot_id → 确定性勘误块。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        ("我已逐镜核对 s01_c03 与 s01_c04 的内容。", None),
        ("根据我的记录，s01_c03 是雨中特写，s01_c04 是两人对坐。", None),  # 仍零读取
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn_req(rt, "demo", sid, "把 s01 全部镜头完整读一遍"))
    # 只 nudge 一次，不循环
    assert len(client.calls) == 2
    text = res["assistant_message"]
    assert "【系统勘误】" in text
    assert "s01_c03" in text and "s01_c04" in text
    # 不改写模型原文（勘误是纯追加）
    assert text.startswith("根据我的记录")


# ============ 误伤用例：纯探讨轮不得触发 nudge ============
def test_chat_turn_no_zero_read_nudge(tmp_path):
    _save_proj(tmp_path)
    client = ScriptedClient([
        ("从剧情看，这段两人沉默对坐，慢节奏长镜头更合适；要表现焦灼可以快剪低机位。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn_req(rt, "demo", sid, "你觉得这段适合长镜头还是快剪？"))
    assert len(client.calls) == 1        # 无 nudge、无纠偏
    assert res["intent"] == "conversation"
    assert "【系统勘误】" not in res["assistant_message"]


# ============ 豁免用例：本轮真实删除的 ID 不被勘误 ============
def test_deleted_id_in_summary_not_flagged(tmp_path):
    _save_proj(tmp_path)
    from src.core.store import ProjectStore
    client = ScriptedClient([
        (None, [_tool_call("shot.read", {"shot_id": "s01_c01"})]),
        (None, [_tool_call("shot.delete", {"shot_id": "s01_c01"})]),
        ("已删除 s01_c01（及其接力下游），保留 s01_c02。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(director_turn_req(rt, "demo", sid, "删掉第1镜"))
    assert res["deleted"] is True
    # s01_c01 级联删除了接力下游 s01_c02，两个 ID 都出现在总结里但都不被勘误
    assert "【系统勘误】" not in res["assistant_message"]
    assert len(ProjectStore(tmp_path).load().scenes[0].shots) == 0


def director_turn_req(rt, project, sid, content):
    """延迟 import（test_director_agent 不导出 director_turn 以避免循环）。"""
    from src.agents.director import director_turn
    return director_turn(rt, project, sid, content)
