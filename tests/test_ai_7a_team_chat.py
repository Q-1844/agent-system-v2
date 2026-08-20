"""AI-7A §22~§24/§54~§57/§76~§79 Team Group Chat 持久化 + 恢复 focused Gate（7A-3 后端）。

engine 级：直接驱动 TeamChatStore + run_team_inquiry_and_persist + ScriptedClient（不联网）。
每 case 用 tmp_path 独立项目，互不污染（匹配 tests/test_agents.py 风格）。

Gate 覆盖：持久化跨重启（§54）/ 项目隔离（§78）/ 幂等不造 dup（§56）/ startup 中断恢复
不重跑 LLM（§57）/ 双路径落盘（question=agent 文本 / change=analysis_card execution_ready=False）
/ 零变更（canonical 不变、无 workspace、无 mutation task）。
"""
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.team_chat import (TeamChatStore, TeamMessage,
                                   run_team_inquiry_and_persist, recover_team_chat)
from src.agents.project_intelligence import is_read_only_capability


def _proj(name="demo"):
    return Project(name=name, style=StyleAnchor(),
                   characters=[Character(name="Xiaoyu", description_en="a girl")],
                   scenes=[Scene(scene_id="s01", summary="meet", characters=["Xiaoyu"],
                                 shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                             description="see", characters=["Xiaoyu"],
                                             target_duration_s=4)])],
                   story_text="story")


def _save(tmp_path, name="demo"):
    p = tmp_path / name
    p.mkdir(parents=True, exist_ok=True)
    ProjectStore(p).save(_proj(name))
    return p


def _rt(tmp_path, **kw):
    kw.setdefault("settings_path", tmp_path / "agents_data" / "settings.json")
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path / name, **kw)


class ScriptedClient:
    def __init__(self, producer="Xiaoyu 出现在 s01",
                 consult=None, synthesis=None):
        self.producer = producer
        self.consult = consult or json.dumps(
            {"conclusion": "需改外貌", "affected_entities": [],
             "downstream_facts": ["含该角色的镜头"], "blockers": [], "user_decisions": [],
             "evidence": []})
        self.synthesis = synthesis or json.dumps({
            "direct_changes": [{"module": "character", "owner_agent": "character_designer",
                                "summary": "改", "reason": "user_requested"}],
            "downstream_changes": [], "unchanged": [], "user_decisions_needed": []})

    async def chat(self, messages, **kw):
        u = messages[-1]["content"] if messages else ""
        if "咨询问题" in u:
            return self.consult
        if "咨询结果" in u or "综合" in u:
            return self.synthesis
        return self.producer


def _run(coro):
    return asyncio.run(coro)


# ============ 1. 持久化跨重启（§54）============
def test_persistence_across_restart(tmp_path):
    _save(tmp_path)
    store = TeamChatStore(tmp_path / "demo", "demo")
    store.get_or_create_thread()
    store.append_message(TeamMessage(message_type="user", text="hello"))
    # 新 store 同目录（模拟重启）读到
    store2 = TeamChatStore(tmp_path / "demo", "demo")
    assert store2.thread_id == store.thread_id   # 确定性派生
    msgs = store2.list_messages()
    assert len(msgs) == 1 and msgs[0]["text"] == "hello"


# ============ 2. 项目隔离（§78）============
def test_project_isolation(tmp_path):
    _save(tmp_path, "projA")
    _save(tmp_path, "projB")
    sa = TeamChatStore(tmp_path / "projA", "projA")
    sb = TeamChatStore(tmp_path / "projB", "projB")
    assert sa.thread_id != sb.thread_id
    sa.append_message(TeamMessage(message_type="user", text="A-only"))
    # B 看不到 A 的消息
    assert all(m["text"] != "A-only" for m in sb.list_messages())
    assert len(sa.list_messages()) == 1
    assert len(sb.list_messages()) == 0


# ============ 3. 幂等：刷新/重连不造 dup（§56）============
def test_get_or_create_idempotent(tmp_path):
    _save(tmp_path)
    store = TeamChatStore(tmp_path / "demo", "demo")
    for _ in range(5):
        store.get_or_create_thread()
    assert len(store.list_messages()) == 0   # 无残留 dup 消息
    store.append_message(TeamMessage(message_type="user", text="one"))
    for _ in range(5):
        store.get_or_create_thread()
    assert len(store.list_messages()) == 1   # 不因 get_or_create 复制


# ============ 4. 问题路径落盘 agent 文本（§50/§76）============
def test_question_path_persists_answer(tmp_path):
    _save(tmp_path)
    rt = _rt(tmp_path, client=ScriptedClient(producer="Xiaoyu 出现在 s01"))
    before = (tmp_path / "demo" / "output" / "project.json").read_bytes()
    store = TeamChatStore(tmp_path / "demo", "demo")
    store.get_or_create_thread()
    user_msg = store.append_message(
        TeamMessage(message_type="user", text="Xiaoyu 出现在哪些场景", pending_inquiry=True))
    _run(run_team_inquiry_and_persist(rt, "demo", user_msg.message_id,
                                      "Xiaoyu 出现在哪些场景"))
    msgs = store.list_messages()
    assert len(msgs) == 2
    assert msgs[0]["message_type"] == "user" and msgs[0]["pending_inquiry"] is False
    assert msgs[1]["message_type"] == "agent" and msgs[1]["agent_id"] == "producer"
    assert "s01" in msgs[1]["text"]
    # canonical 不变
    assert (tmp_path / "demo" / "output" / "project.json").read_bytes() == before


# ============ 5. 变更路径落盘 analysis_card（§37/§52）============
def test_change_path_persists_analysis_card(tmp_path):
    _save(tmp_path)
    rt = _rt(tmp_path, client=ScriptedClient())
    before = (tmp_path / "demo" / "output" / "project.json").read_bytes()
    store = TeamChatStore(tmp_path / "demo", "demo")
    store.get_or_create_thread()
    user_msg = store.append_message(
        TeamMessage(message_type="user", text="把 Xiaoyu 的头发改长", pending_inquiry=True))
    _run(run_team_inquiry_and_persist(rt, "demo", user_msg.message_id,
                                      "把 Xiaoyu 的头发改长"))
    msgs = store.list_messages()
    card = next(m for m in msgs if m["message_type"] == "analysis_card")
    assert card["card_data"]["execution_ready"] is False
    assert card["card_data"]["change_operations"] == []
    assert card["card_data"]["direct_changes"]
    # 无 workspace 产物
    assert not (tmp_path / "demo" / "workspaces").exists()
    # 咨询 task 全是只读 cap
    from src.agents.tasks import AgentTaskStore
    for t in AgentTaskStore(tmp_path / "demo", "demo").list():
        assert is_read_only_capability(t.capability_id)
    assert (tmp_path / "demo" / "output" / "project.json").read_bytes() == before


# ============ 6. startup 中断恢复不重跑（§57）============
def test_startup_recovery_interrupted(tmp_path):
    _save(tmp_path)
    store = TeamChatStore(tmp_path / "demo", "demo")
    store.get_or_create_thread()
    # 模拟 backend 在 run_inquiry 中途挂掉：user 消息 pending 未清，无 producer 回复
    store.append_message(TeamMessage(message_type="user", text="把头发改长", pending_inquiry=True))
    assert store.list_pending_inquiries()
    n = recover_team_chat(tmp_path / "demo", "demo")
    assert n == 1
    msgs = store.list_messages()
    # 1 user + 1 system 中断提示；无 agent 回复（未重跑 LLM）
    assert len(msgs) == 2
    assert msgs[1]["message_type"] == "system"
    assert msgs[0]["pending_inquiry"] is False
    assert not store.list_pending_inquiries()


# ============ 7. 后台异常不泄漏（优雅降级：不抛、pending 清除、用户见失败说明）============
def test_run_failure_degrades_gracefully(tmp_path):
    _save(tmp_path)

    class BoomClient:
        async def chat(self, messages, **kw):
            raise RuntimeError("model exploded")
    rt = _rt(tmp_path, client=BoomClient())
    store = TeamChatStore(tmp_path / "demo", "demo")
    store.get_or_create_thread()
    user_msg = store.append_message(
        TeamMessage(message_type="user", text="any question", pending_inquiry=True))
    # run_inquiry 软失败（_producer_answer 捕获异常返回失败文本），不应抛到外层
    _run(run_team_inquiry_and_persist(rt, "demo", user_msg.message_id, "any question"))
    msgs = store.list_messages()
    assert len(msgs) == 2
    # pending 已清；用户见到一条 producer 失败说明（非崩溃、非静默）
    assert msgs[0]["pending_inquiry"] is False
    assert msgs[1]["agent_id"] == "producer"
    assert ("失败" in msgs[1]["text"]) or ("未配置" in msgs[1]["text"])
