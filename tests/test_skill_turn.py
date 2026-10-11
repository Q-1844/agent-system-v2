# tests/test_skill_turn.py
"""Skill System V1（40.md §十五）Phase C — Workflow Dry Run / Mock。

用脚本化 fake client 通过【Agent 正式入口】（director_turn）验证：
- skill.load 在真实 tool loop 中执行并返回工作方法；
- hint 注入 LLM 上下文但【不落盘】（session 只存用户原文）；
- trace.jsonl 记录 hint/load；meta.skills 收口本 turn 技能证据；
- 校准链路 dry run：加载技能 → 接缝测量（真实 continuity_service 逻辑被
  跳过——无真实视频，只验证决策流与 trace 完整性）。
"""
import asyncio
import json
from pathlib import Path

from src.core.models import Project
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime


class _FakeClient:
    """脚本化响应 client（非流式 chat_completion）。"""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.received: list = []

    async def chat_completion(self, messages, **kw):
        self.received.append([dict(m) for m in messages])
        if self._responses:
            return self._responses.pop(0)
        return {"content": "完成。", "tool_calls": None}


def _tc(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(args, ensure_ascii=False)}}


def _runtime(pdir: Path, client) -> AgentRuntime:
    return AgentRuntime(project_dir_resolver=lambda name: pdir,
                        settings_path=pdir / "agents_data" / "settings.json",
                        client=client)


def _seed_project(pdir: Path) -> None:
    ProjectStore(pdir).save(Project(name="skill-turn-test", scenes=[], characters=[]))


def test_director_turn_loads_skill_via_official_entry(tmp_path):
    """Phase C：校准任务 → hint 注入 → skill.load 真实执行 → trace/meta 完整。"""
    pdir = tmp_path / "proj_skill_turn"
    pdir.mkdir(parents=True)
    _seed_project(pdir)
    content = "对整条故事做连续性校准"
    fake = _FakeClient([
        {"content": None,
         "tool_calls": [_tc("skill.load",
                            {"skill_id": "full-story-relay-calibration"})]},
        {"content": "已加载全片尾帧接力连续性校准方法：先接缝测量再三档裁定。",
         "tool_calls": None},
    ])
    rt = _runtime(pdir, fake)
    session = rt._session_store(pdir.name).get_or_create_conversation("director")

    from src.agents.director import director_turn
    result = asyncio.run(director_turn(rt, pdir.name, session.session_id, content))

    # ① skill.load 真实执行（trace 中有条目，回执 ok）
    skill_calls = [t for t in result["trace"] if t.get("tool") == "skill.load"]
    assert len(skill_calls) == 1
    assert skill_calls[0]["ok"] is True and skill_calls[0]["result"] == "ok"
    # ② LLM 上下文中 hint 已注入（第一条发给模型的 user 消息含系统提示）
    first_user = fake.received[0][-1]["content"]
    assert "[系统 skill 提示]" in first_user
    assert "full-story-relay-calibration" in first_user
    assert content in first_user
    # ③ session 落盘【不含】hint——只有用户原文
    persisted = rt._session_store(pdir.name).load(session.session_id)
    user_msgs = [m for m in persisted.messages if m.role == "user"]
    assert user_msgs[0].content == content
    # ④ meta.skills 收口本 turn 技能证据（hint + load）
    meta = result["message"]["meta"]
    skill_events = meta.get("skills") or []
    assert [e["event"] for e in skill_events] == ["hint", "load"]
    assert skill_events[1]["skill_id"] == "full-story-relay-calibration"
    assert skill_events[1]["version"] == "1.6.0"
    assert skill_events[1]["detail"]["role"] == "primary_executor"
    # ⑤ trace.jsonl 持久化
    tj = pdir / "output" / "agents" / "skills" / "trace.jsonl"
    assert tj.is_file()
    persisted_events = [json.loads(l) for l in
                        tj.read_text(encoding="utf-8").splitlines()]
    assert [e["event"] for e in persisted_events] == ["hint", "load"]
    # ⑥ assistant 回复正常
    assert "校准" in result["assistant_message"]


def test_director_turn_no_hint_for_irrelevant_task(tmp_path):
    """无关任务：无 hint 注入，meta.skills 为空，行为与基线一致。"""
    pdir = tmp_path / "proj_skill_turn2"
    pdir.mkdir(parents=True)
    _seed_project(pdir)
    content = "把 s01 第1镜改成近景"
    fake = _FakeClient([
        {"content": "好的，我先读取该镜头。", "tool_calls": None},
    ])
    rt = _runtime(pdir, fake)
    session = rt._session_store(pdir.name).get_or_create_conversation("director")

    from src.agents.director import director_turn
    result = asyncio.run(director_turn(rt, pdir.name, session.session_id, content))

    first_user = fake.received[0][-1]["content"]
    assert "[系统 skill 提示]" not in first_user
    assert first_user == content
    assert result["message"]["meta"].get("skills") == []
    tj = pdir / "output" / "agents" / "skills" / "trace.jsonl"
    assert not tj.is_file()


def test_director_turn_skill_load_fail_closed(tmp_path):
    """Agent 尝试加载不属于自己的 skill → 结构化失败喂回模型（不 raise）。"""
    pdir = tmp_path / "proj_skill_turn3"
    pdir.mkdir(parents=True)
    _seed_project(pdir)
    content = "帮我看看有什么可用的技能"
    fake = _FakeClient([
        {"content": None,
         "tool_calls": [_tc("skill.load", {"skill_id": "ghost-skill"})]},
        {"content": "该技能不存在，我先 skill.list 查看目录。", "tool_calls": None},
    ])
    rt = _runtime(pdir, fake)
    session = rt._session_store(pdir.name).get_or_create_conversation("director")

    from src.agents.director import director_turn
    result = asyncio.run(director_turn(rt, pdir.name, session.session_id, content))

    skill_calls = [t for t in result["trace"] if t.get("tool") == "skill.load"]
    assert skill_calls[0]["ok"] is False
    assert "不存在" in (skill_calls[0]["error"] or "")
    meta = result["message"]["meta"]
    assert [e["event"] for e in meta["skills"]] == ["load_fail"]
    assert "不存在" in result["assistant_message"] or \
        "skill.list" in result["assistant_message"]
