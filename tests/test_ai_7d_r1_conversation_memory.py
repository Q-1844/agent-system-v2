"""AI-7D-R R1 · 长存 Conversation + 基础记忆 + 根因 #1 修复（engine 级）。

不联网、不调真 Agnes（注入 FakeClient）。验证：
  §9  长存 Conversation：同 agent 多次 get_or_create 复用同一 project 级会话；消息有 message_id。
  §10.2 共享工作记忆：add/recent/render。
  §10.3 显式偏好：looks_like_preference + 捕获 + active。
  §7   根因 #1：request_changes 对已落定/blocked 会话抛 CHANGE_SESSION_SETTLED（新变更须新建）。
  §10  0-item 提案 → 诚实 no_action 终态（不伪造 A/B 方案）。
  §11  开放会话的修订反馈：supersede 旧提案 + latest_feedback + 回到 routing（真重跑）。
  §8   连续变更：旧会话落定后新建会话基于最新 canonical，不撞 PLAN_SOURCE_STALE。

风格镜像 test_ai_7d_d1/d2（_runtime/_save_proj/FakeClient + cp_service 直驱）。
"""
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.work_memory import WorkMemoryStore
from src.agents.preferences import PreferenceStore, looks_like_preference
from src.agents.change_protocol import service as cp_service


def _proj():
    return Project(
        name="demo", style=StyleAnchor(),
        story_text="小雨走进雨里。",
        characters=[Character(name="LinXia", description_en="a young woman")],
        scenes=[Scene(scene_id="s01", summary="rain", characters=["LinXia"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="look up at rain", characters=["LinXia"],
                                  target_duration_s=5)])])


def _save_proj(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    return tmp_path


def _runtime(tmp_path, **kw):
    kw.setdefault("settings_path", tmp_path / "agents_data" / "settings.json")
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path, **kw)


class FakeClient:
    """返回固定 reply 的假 LLM client。"""
    def __init__(self, reply: str = "ok"):
        self._reply = reply
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        return self._reply


def _run(coro):
    return asyncio.run(coro)


# ============ §9 长存 Conversation ============
def test_conversation_stable_per_agent(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    s1 = rt.get_or_create_session("demo", "script_writer", "project", "demo")
    s2 = rt.get_or_create_session("demo", "script_writer", "project", "demo")
    # 同 (project, agent) 复用同一长存会话
    assert s1["session_id"] == s2["session_id"]
    assert s1.get("conversation_id")
    # 不同 agent 不同会话
    s3 = rt.get_or_create_session("demo", "director", "project", "demo")
    assert s3["session_id"] != s1["session_id"]


def test_conversation_messages_have_ids_and_persist(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient("收到"))
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    _run(rt.unified_turn("demo", sid, "你好"))
    saved = rt.get_session("demo", sid)
    assert saved is not None
    user_msgs = [m for m in saved["messages"] if m["role"] == "user"]
    asst_msgs = [m for m in saved["messages"] if m["role"] == "assistant"]
    assert user_msgs and user_msgs[0]["message_id"]
    assert asst_msgs and asst_msgs[0]["message_id"]
    assert asst_msgs[0]["conversation_id"] == saved["conversation_id"]


def test_conversation_survives_fresh_store_instance(tmp_path):
    """F5/重启：新建 runtime/store 实例后历史仍在（耐久 JSON）。"""
    _save_proj(tmp_path)
    rt1 = _runtime(tmp_path, client=FakeClient("回信"))
    sid = rt1.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    _run(rt1.unified_turn("demo", sid, "记得这句话"))
    rt2 = _runtime(tmp_path, client=FakeClient("回信2"))
    saved = rt2.get_session("demo", sid)
    assert any(m["role"] == "user" and "记得这句话" in m["content"] for m in saved["messages"])


# ============ §10.2 共享工作记忆 ============
def test_work_memory_add_and_recent(tmp_path):
    _save_proj(tmp_path)
    wm = WorkMemoryStore(tmp_path, "demo")
    e1 = wm.add("change", "Script Writer 扩写了第二幕", participants=["script_writer"],
                affected_domains=["script"], references={"change_session_id": "cs_x"})
    e2 = wm.add("completion", "执行完成", participants=["producer"],
                affected_domains=["storyboard"])
    assert e1["id"] and e1["type"] == "change"
    recent = wm.recent(5)
    assert recent[0]["id"] == e2["id"]   # 倒序
    text = wm.render_for_context()
    assert "扩写了第二幕" in text and "cs_x" in text


def test_work_memory_does_not_leak_across_projects(tmp_path):
    _save_proj(tmp_path)
    WorkMemoryStore(tmp_path, "demo").add("change", "demo 项目的工作")
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    assert WorkMemoryStore(other_dir, "other").recent() == []


# ============ §10.3 显式偏好 ============
def test_preference_detection_conservative():
    assert looks_like_preference("以后不要自动增加战斗场面")
    assert looks_like_preference("默认保持这种动画风格")
    assert not looks_like_preference("这次别加战斗")        # 一次性，非长期
    assert not looks_like_preference("把结局改一下？")        # 问句
    assert not looks_like_preference("")


def test_preference_captured_on_explicit_turn(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient("好的，记住了"))
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    _run(rt.unified_turn("demo", sid, "以后除非我说，否则不要自动重新生成视频"))
    active = rt.preference_store("demo").active()
    assert any("不要自动重新生成视频" in p["value"] for p in active)


def test_preference_not_captured_for_one_off(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient("好"))
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    _run(rt.unified_turn("demo", sid, "把最后一句改短一点"))
    assert rt.preference_store("demo").active() == []


# ============ §7 根因 #1：落定/blocked 会话不接受新反馈 ============
def test_request_changes_rejects_settled_session(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    for status in ("approved_for_execution", "blocked", "no_action",
                   "rejected", "stale", "failed", "cancelled"):
        r = cp_service.create_session(rt, "demo", "改剧本", client_request_id=f"creq_{status}")
        sid = r["session"]["session_id"]
        s = rt._resolve_project_dir  # noop, keep ref
        store = cp_service._store(rt, "demo")
        sess = store.get_session(sid)
        sess.status = status
        store.save_session(sess)
        try:
            cp_service.request_changes(rt, "demo", sid, "再改改")
            assert False, f"{status} 应拒绝"
        except AgentError as e:
            assert e.code == "CHANGE_SESSION_SETTLED"


# ============ §10 0-item 提案 → no_action（不伪造 A/B）============
def test_zero_item_proposal_terminates_no_action(tmp_path):
    _save_proj(tmp_path)
    empty = json.dumps({"title": "无需改动", "summary": "当前剧本已满足", "items": []},
                       ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(empty))
    r = cp_service.create_session(rt, "demo", "把剧本改一改", source="direct_agent_chat",
                                  client_request_id="creq_empty")
    sid = r["session"]["session_id"]
    # routing → 首轮提案（script_writer 返回 0 item）→ analyzing → compile → no_action
    s1 = _run(cp_service.resume_session(rt, "demo", sid))
    assert s1["status"] == "analyzing_dependencies"
    s2 = _run(cp_service.resume_session(rt, "demo", sid))
    assert s2["status"] == "no_action", f"expected no_action, got {s2['status']} / {s2.get('error')}"
    store = cp_service._store(rt, "demo")
    sess = store.get_session(sid)
    assert sess.no_action_reason   # 有诚实原因


# ============ §11 开放会话的反馈真重跑（不吞反馈）============
def test_feedback_on_open_session_supersedes_and_reroutes(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient("ok"))
    r = cp_service.create_session(rt, "demo", "把剧本拓展", client_request_id="creq_fb")
    sid = r["session"]["session_id"]
    store = cp_service._store(rt, "demo")
    # 人工置为 ready_for_review（模拟已生成方案），并放一条 completed 提案
    from src.agents.change_protocol.models import ModuleProposal
    mp = ModuleProposal(proposal_id="mp_old", change_session_id=sid, module="script",
                        owner_agent_id="script_writer", round=1, status="completed",
                        source_snapshot_signature=r["session"]["source_snapshot_signature"])
    store.save_proposal(mp)
    sess = store.get_session(sid)
    sess.status = "ready_for_review"
    store.save_session(sess)

    out = cp_service.request_changes(rt, "demo", sid, "再长一点，加对白")
    # 旧 plan 被 supersede，旧 completed 提案被 supersede（下一轮 resume 会真重跑），反馈落 latest_feedback
    assert out["superseded_plan_id"] is None or isinstance(out["superseded_plan_id"], str)
    sess2 = store.get_session(sid)
    assert sess2.status == "routing"
    assert sess2.latest_feedback == "再长一点，加对白"
    # 旧提案不再算 completed → _has_completed_proposal 为 False（真重跑）
    assert not cp_service._has_completed_proposal(store, sid, "script_writer", 1)


# ============ §8 连续变更不撞 PLAN_SOURCE_STALE ============
def test_consecutive_changes_new_session_on_latest_canonical(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    # 第一个变更会话
    r1 = cp_service.create_session(rt, "demo", "第一次修改", client_request_id="creq_1")
    s1 = cp_service._store(rt, "demo").get_session(r1["session"]["session_id"])
    s1.status = "approved_for_execution"   # 模拟已落定（执行完成）
    cp_service._store(rt, "demo").save_session(s1)
    # canonical 前进（模拟执行已提交：签名变化）
    p = ProjectStore(tmp_path).load()
    p.story_text = p.story_text + " 她抬起头。"
    ProjectStore(tmp_path).save(p)

    # 对已落定旧会话 request_changes → SETTLED（前端据此新建，不撞 stale 吞消息）
    try:
        cp_service.request_changes(rt, "demo", r1["session"]["session_id"], "再长一点")
        assert False
    except AgentError as e:
        assert e.code == "CHANGE_SESSION_SETTLED"

    # 新变更会话：基于最新 canonical，签名是新的
    r2 = cp_service.create_session(rt, "demo", "再长一点", client_request_id="creq_2")
    assert r2["session"]["session_id"] != r1["session"]["session_id"]
    assert (r2["session"]["source_snapshot_signature"]
            != r1["session"]["source_snapshot_signature"])
    # 新会话 routing 正常，不被旧会话阻塞
    assert r2["session"]["status"] == "routing"
