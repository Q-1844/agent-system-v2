"""AI-7A §30~§49 Change Analysis Session + analysis-only Plan focused Gate（7A-4）。

engine 级：直接驱动 create/continue_change_analysis + ChangeAnalysisStore +
ReadOnlyPlanValidator + recover_change_analysis + ScriptedClient（不联网）。每 case 用 tmp_path
独立项目，互不污染。

Gate 覆盖：execution_ready 恒 False / 0 ChangeOperation（§37/§38）/ ReadOnlyPlanValidator 拒可执行 op
（§42）/ stale 命中（§49）/ 中途重启恢复：保留 completed、running→failed、resume 重跑 missing（§55/§57）/
失败→blocked→resume（§84）/ 项目隔离（§78）/ 无重复咨询（§56）。
"""
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.change_analysis import (ChangeAnalysisStore, ReadOnlyPlanValidator,
                                          create_change_analysis_session,
                                          continue_change_analysis,
                                          recover_change_analysis, _new_session,
                                          ChangeRequestDraft, ModuleConsultation)
from src.agents.project_intelligence import is_read_only_capability


def _proj(name="demo"):
    return Project(name=name, style=StyleAnchor(),
                   characters=[Character(name="Xiaoyu", description_en="a girl"),
                               Character(name="Aran", description_en="a hero")],
                   scenes=[Scene(scene_id="s01", summary="meet", characters=["Xiaoyu", "Aran"],
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


def _run(coro):
    return asyncio.run(coro)


class ScriptedClient:
    def __init__(self, consult=None, synthesis=None, bad_module=None):
        self.consult = consult or json.dumps(
            {"conclusion": "需改外貌", "affected_entities": [{"module": "character", "entity_id": "Xiaoyu"}],
             "downstream_facts": ["含该角色的镜头"], "blockers": [], "user_decisions": [],
             "evidence": []})
        self.synthesis = synthesis or json.dumps({
            "direct_changes": [{"module": "character", "owner_agent": "character_designer",
                                "summary": "改", "reason": "user_requested"}],
            "downstream_changes": [{"module": "storyboard", "owner_agent": "director",
                                    "summary": "镜头", "reason": "downstream_impact"}],
            "unchanged": [{"module": "script", "entity_id": "s01"}],
            "user_decisions_needed": ["是否保留旧版"]})
        self.bad_module = bad_module

    async def chat(self, messages, **kw):
        u = messages[-1]["content"] if messages else ""
        if "咨询问题" in u:
            if self.bad_module and f"「{self.bad_module}」" in u:
                return "not json"
            return self.consult
        if "咨询结果" in u or "综合" in u:
            return self.synthesis
        return "answer"


REQ = "把 Xiaoyu 的头发改长并修改结局"


# ============ 1. analysis-only plan（§37/§38）============
def test_plan_draft_analysis_only(tmp_path):
    _save(tmp_path)
    rt = _rt(tmp_path, client=ScriptedClient())
    before = (tmp_path / "demo" / "output" / "project.json").read_bytes()
    sess = _run(create_change_analysis_session(rt, "demo", REQ, background=False))
    sess = _run(continue_change_analysis(rt, "demo", sess["session_id"]))
    assert sess["status"] == "ready"
    draft = sess["plan_draft"]
    assert draft["execution_ready"] is False
    assert draft["status"] == "analysis_only"
    assert "change_operations" not in draft           # §38：0 可执行操作
    # 无任何 item 含 op 字段
    for item in draft["direct_changes"] + draft["downstream_changes"]:
        for bad in ("op", "op_type", "update_scene", "update_shot", "after", "before"):
            assert bad not in item
    assert draft["consulted_agents"]
    # canonical 不变 + 无 workspace + 咨询 task 全只读
    assert (tmp_path / "demo" / "output" / "project.json").read_bytes() == before
    assert not (tmp_path / "demo" / "workspaces").exists()
    from src.agents.tasks import AgentTaskStore
    for t in AgentTaskStore(tmp_path / "demo", "demo").list():
        assert is_read_only_capability(t.capability_id)


# ============ 2. ReadOnlyPlanValidator（§42）============
def test_validator_rejects_executable_and_ops():
    v = ReadOnlyPlanValidator()
    assert any(i["code"] == "EXECUTABLE" for i in v.validate(
        {"execution_ready": True, "status": "analysis_only", "direct_changes": [],
         "downstream_changes": [], "consulted_agents": []}))
    assert any(i["code"] == "BAD_STATUS" for i in v.validate(
        {"execution_ready": False, "status": "applied", "direct_changes": [],
         "downstream_changes": [], "consulted_agents": []}))
    op_item = {"module": "character", "owner_agent_id": "character_designer",
               "summary": "x", "reason": "user_requested", "source": "user_requested",
               "op": "update_shot", "after": {"a": 1}}
    issues = v.validate({"execution_ready": False, "status": "analysis_only",
                         "direct_changes": [op_item], "downstream_changes": [],
                         "consulted_agents": ["character_designer"]})
    assert any(i["code"] == "CHANGE_OPERATION" for i in issues)
    # 干净 draft 无 issue
    clean = {"execution_ready": False, "status": "analysis_only",
             "direct_changes": [{"module": "character", "owner_agent_id": "character_designer",
                                  "summary": "x", "source": "user_requested"}],
             "downstream_changes": [], "consulted_agents": ["character_designer"]}
    assert v.validate(clean) == []


# ============ 3. stale 命中（§49）============
def test_stale_detection(tmp_path):
    _save(tmp_path)
    rt = _rt(tmp_path, client=ScriptedClient())
    sess = _run(create_change_analysis_session(rt, "demo", REQ, background=False))
    # 会话启动后改 canonical 创作结构 → 签名变
    p = ProjectStore(tmp_path / "demo").load()
    p.scenes.append(Scene(scene_id="s02", summary="new", characters=["Aran"],
                          shots=[Shot(shot_id="s02_c01", scene_id="s02", index=1,
                                      description="n", characters=["Aran"])]))
    ProjectStore(tmp_path / "demo").save(p)
    sess = _run(continue_change_analysis(rt, "demo", sess["session_id"]))
    assert sess["status"] == "stale"
    assert sess.get("plan_draft") is None   # 不作为当前计划呈现


# ============ 4. 中途重启恢复（§55/§57）============
def test_restart_recovery_mid_consultation(tmp_path):
    _save(tmp_path)
    rt = _rt(tmp_path, client=ScriptedClient())
    sess = _run(create_change_analysis_session(rt, "demo", REQ, background=False))
    sid = sess["session_id"]
    store = ChangeAnalysisStore(tmp_path / "demo", "demo")
    # 模拟崩溃前：character 已完成、script 正在 running（卡住）
    now = 0.0
    sess["consultations"] = [
        ModuleConsultation(consultation_id="mc1", change_session_id=sid,
                           agent_id="character_designer", module="character",
                           question="q", status="completed",
                           result_summary="改外貌", downstream_facts=["镜头"],
                           agent_task_id="t_old_char", created_at=now, updated_at=now).to_dict(),
        ModuleConsultation(consultation_id="mc2", change_session_id=sid,
                           agent_id="script_writer", module="script",
                           question="q", status="running",
                           agent_task_id="t_old_script", created_at=now, updated_at=now).to_dict()]
    sess["status"] = "consulting"
    store.save_session(sess)

    # startup 恢复：running→failed（中断），不重跑；character completed 保留
    n = recover_change_analysis(tmp_path / "demo", "demo")
    assert n == 1
    s2 = store.get_session(sid)
    assert s2["status"] == "blocked"
    statuses = {c["agent_id"]: c["status"] for c in s2["consultations"]}
    assert statuses["character_designer"] == "completed"   # 保留
    assert statuses["script_writer"] == "failed"           # 中断标 failed
    assert s2["consultations"][0]["result_summary"] == "改外貌"  # 结果未丢

    # 显式 resume：重跑 failed（script），character 不重跑；成功 → ready
    s3 = _run(continue_change_analysis(rt, "demo", sid))
    assert s3["status"] == "ready"
    # character 仍是原结果（未重跑、未 dup）
    char = [c for c in s3["consultations"] if c["agent_id"] == "character_designer"][0]
    assert char["agent_task_id"] == "t_old_char"   # 未被重跑覆盖
    assert s3["plan_draft"]["execution_ready"] is False


# ============ 5. 失败→blocked→resume（§84）============
def test_blocked_then_resume(tmp_path):
    _save(tmp_path)
    # script 咨询坏 JSON → failed → blocked
    rt = _rt(tmp_path, client=ScriptedClient(bad_module="script"))
    sess = _run(create_change_analysis_session(rt, "demo", REQ, background=False))
    s1 = _run(continue_change_analysis(rt, "demo", sess["session_id"]))
    assert s1["status"] == "blocked"
    assert any(c["status"] == "failed" for c in s1["consultations"])
    # 换好 client resume → 重跑 failed → ready
    rt2 = _rt(tmp_path, client=ScriptedClient())
    s2 = _run(continue_change_analysis(rt2, "demo", sess["session_id"]))
    assert s2["status"] == "ready"


# ============ 6. 项目隔离（§78）============
def test_project_isolation(tmp_path):
    _save(tmp_path, "A")
    _save(tmp_path, "B")
    rt = _rt(tmp_path, client=ScriptedClient())
    sa = _run(create_change_analysis_session(rt, "A", REQ, background=False))
    sb = _run(create_change_analysis_session(rt, "B", REQ, background=False))
    assert sa["session_id"] != sb["session_id"]
    # A 的会话不在 B 的 store
    assert ChangeAnalysisStore(tmp_path / "B", "B").get_session(sa["session_id"]) is None
    # B 的 list 不含 A 的会话
    assert all(s["project_name"] == "B"
               for s in ChangeAnalysisStore(tmp_path / "B", "B").list_sessions())


# ============ 7. 无重复咨询（§56）============
def test_no_duplicate_consultations(tmp_path):
    _save(tmp_path)
    rt = _rt(tmp_path, client=ScriptedClient())
    sess = _run(create_change_analysis_session(rt, "demo", REQ, background=False))
    sid = sess["session_id"]
    s1 = _run(continue_change_analysis(rt, "demo", sid))
    assert s1["status"] == "ready"
    n_cons = len(s1["consultations"])
    # 幂等：再次 continue（终端态）不重跑、不 dup
    s2 = _run(continue_change_analysis(rt, "demo", sid))
    assert s2["status"] == "ready"
    assert len(s2["consultations"]) == n_cons
