"""Session Re-grounding（结构修改累积后的校准会话重置）mock 测试（不联网）。

对应 V1 压力测试验收用例：
  - 结构写操作累计 >=16（两次 Clean Review 量级）→ 校准会话重置提示出现；
  - 携带物严格限定：状态头（canonical + work_memory）+ 用户原始 brief + 最近一轮
    用户意图；绝不含旧会话聊天内容（旧历史整体归档而非摘要）；
  - 重置后计数器清零（两个计数器都清）；
  - 同 session_id 原地重置：前端无感，下一 turn 继续在主会话干活，历史里带状态头。
"""
import json

from tests.test_director_agent import (
    _save_proj, _runtime, _run, _tool_call, _shots_rev, ScriptedClient,
)
from src.agents import session_governance
from src.core.store import ProjectStore


def _director():
    from src.agents.director import director_turn
    return director_turn


def _seed_gov(tmp_path, since_review, since_reground):
    gov_path = tmp_path / "output" / "agents" / "session_governance.json"
    gov_path.parent.mkdir(parents=True, exist_ok=True)
    gov_path.write_text(json.dumps({"structural_since_review": since_review,
                                    "structural_since_reground": since_reground}),
                        encoding="utf-8")


def _create_one_shot_script(tmp_path):
    rev = _shots_rev(tmp_path)
    return ScriptedClient([
        (None, [_tool_call("shot.list", {"scene_id": "s02"})]),
        (None, [_tool_call("shot.create",
                           {"scene_id": "s02", "description": "风铃特写，浅景深",
                            "expected_shots_revision": rev})]),
        ("已创建镜头 s02_c01。", None),
    ])


def test_reground_at_threshold(tmp_path):
    """预置 15 次 + 本轮 1 次成功结构写 = 16 → 校准会话重置，计数清零。"""
    _save_proj(tmp_path)
    _seed_gov(tmp_path, since_review=15, since_reground=15)
    client = _create_one_shot_script(tmp_path)
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(_director()(rt, "demo", sid, "在段尾补一个风铃特写"))
    # 主链路写真实生效
    assert res["created"] is True
    assert len(ProjectStore(tmp_path).load().scenes[1].shots) == 1
    # UX：不静默——明确提示已开启校准会话
    assert "已根据当前项目状态开启校准会话" in res["assistant_message"]
    assert any(e["kind"] == "session_reground" for e in res["events"])
    # 旧历史整体归档（不是摘要、不是删除）
    archive = list((tmp_path / "output" / "agents" / "sessions" / "archive")
                   .glob(f"{sid}_preground_*.json"))
    assert len(archive) == 1
    archived = json.loads(archive[0].read_text(encoding="utf-8"))
    assert any(m["content"] == "在段尾补一个风铃特写"
               for m in archived["messages"] if m["role"] == "user")
    # 重置后计数器清零（两个都清）
    st = session_governance.GovernanceStore(tmp_path, "demo").snapshot()
    assert st["structural_since_review"] == 0 and st["structural_since_reground"] == 0
    # 同 session_id 原地重置：新历史 = 校准种子 + 本轮 assistant 回复
    sess = rt.get_session("demo", sid)
    assert sess["messages"][0]["role"] == "user"
    seed = sess["messages"][0]["content"]
    assert "校准会话开启" in seed
    # 携带物严格限定：状态头（canonical 事实，含真实 shot_id）+ brief + 最近意图
    assert "项目状态头" in seed
    assert "s01_c01" in seed and "s02_c01" in seed        # canonical 当前真实镜头
    assert "在段尾补一个风铃特写" in seed                   # brief（首条用户消息）+ 最近意图
    assert len(sess["messages"]) == 2                     # 绝无旧聊天内容
    assert sess["messages"][1]["role"] == "assistant"


def test_reground_next_turn_works_with_state_header(tmp_path):
    """重置后继续在主会话干活：下一 turn 的上下文里带校准状态头。"""
    _save_proj(tmp_path)
    _seed_gov(tmp_path, since_review=15, since_reground=15)
    rt = _runtime(tmp_path, client=_create_one_shot_script(tmp_path))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    _run(_director()(rt, "demo", sid, "在段尾补一个风铃特写"))
    # 下一 turn（普通探讨轮）
    rt._client = ScriptedClient([("节奏没问题，s02 一镜即可。", None)])
    res = _run(_director()(rt, "demo", sid, "现在整体节奏你觉得怎么样？"))
    assert res["intent"] == "conversation"
    seen_seed = [m for m in rt._client.calls[0]["messages"]
                 if m.get("role") == "user" and "项目状态头" in (m.get("content") or "")]
    assert len(seen_seed) == 1       # 状态头作为历史注入，旧聊天不在
    rt._client = None


def test_no_reground_below_threshold(tmp_path):
    """阈值之下不重置（14+1=15 < 16），也不触发 Clean Review（1+1=2 < 8）。"""
    _save_proj(tmp_path)
    _seed_gov(tmp_path, since_review=1, since_reground=14)
    client = _create_one_shot_script(tmp_path)
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(_director()(rt, "demo", sid, "在段尾补一个风铃特写"))
    assert res["created"] is True
    assert "校准会话" not in res["assistant_message"]
    assert "【自动连续性审查】" not in res["assistant_message"]
    st = session_governance.GovernanceStore(tmp_path, "demo").snapshot()
    assert st["structural_since_review"] == 2 and st["structural_since_reground"] == 15
    assert not (tmp_path / "output" / "agents" / "sessions" / "archive").exists()


def test_state_header_from_canonical_and_work_memory(tmp_path):
    """状态头生成源：canonical（scenes/shots/characters）+ work_memory 结构化事实；
    绝不含旧会话聊天内容。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=None)
    rt.record_work("demo", "change", "Director 修改了镜头 s01_c01（description）",
                   participants=["director"], affected_domains=["storyboard"])
    header = session_governance.build_state_header(tmp_path, "demo")
    assert "项目状态头" in header
    assert "s01_c01" in header and "s01_c02" in header
    assert "林小雨" in header                       # 角色 canonical 名单
    assert "Director 修改了镜头 s01_c01" in header   # work_memory 结构化事实
    # brief 注入
    session_governance.capture_brief_if_absent(
        tmp_path, "demo", "台风夜灯塔救援，老船长全程只以无线电声音出现", "script_writer")
    from src.agents.session_governance import get_brief
    assert "老船长" in (get_brief(tmp_path, "demo") or "")
