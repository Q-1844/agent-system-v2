"""AI-7A §33~§52 Team Inquiry Runtime + Specialist Read Intelligence focused Gate（7A-2）。

engine 级：直接驱动 inquiry.run_inquiry / consult_specialist + ScriptedClient（不联网）。
每 case 用 tmp_path 独立项目，互不污染（匹配 tests/test_agents.py 风格）。

Gate 覆盖：请求分类（§33）/ 有界 ContextPack（§9/§21）/ 咨询只读 + AgentTask 持久化（§32/§43）/
问题路径（§50）/ 变更路径 analysis-only（§37/§38/§39）/ 并行独立结算（§34）/ max 2 轮无循环（§35）/
§96 经 Producer 路径 mutation 被拒、无底层 workflow。
"""
import asyncio
import json

import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.tasks import AgentTaskStore
from src.agents import inquiry
from src.agents.inquiry import (run_inquiry as _run_inquiry_async,
                                consult_specialist as _consult_specialist_async,
                                classify_request as _classify,
                                ProjectContextBuilder as _Builder,
                                ContextBudget as _Budget)
from src.agents.project_intelligence import (ProjectEvidenceRef,
                                              is_read_only_capability,
                                              assert_read_only_capability)
from src.agents.registry import Registries


def _run_inquiry(rt, project, req, **kw):
    return asyncio.run(_run_inquiry_async(rt, project, req, **kw))


def _consult_specialist(rt, project, agent_id, question, pack, **kw):
    return asyncio.run(_consult_specialist_async(rt, project, agent_id, question, pack, **kw))


# ============ 辅助 ============
def _proj():
    chars = [Character(name="Aran", description_en="a hero"),
             Character(name="Xiaoyu", description_en="a girl")]
    s1 = Scene(scene_id="s01", summary="Aran wakes", characters=["Aran"],
               shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                           description="Aran opens eyes", characters=["Aran"],
                           target_duration_s=5)])
    s2 = Scene(scene_id="s02", summary="Aran meets Xiaoyu", characters=["Aran", "Xiaoyu"],
               shots=[Shot(shot_id="s02_c01", scene_id="s02", index=1,
                           description="Aran sees Xiaoyu", characters=["Aran", "Xiaoyu"],
                           target_duration_s=4)])
    return Project(name="demo", style=StyleAnchor(), characters=chars,
                   scenes=[s1, s2], story_text="Once upon a time...")


def _save(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    return tmp_path


def _rt(tmp_path, **kw):
    kw.setdefault("settings_path", tmp_path / "agents_data" / "settings.json")
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path, **kw)


def _consult_json():
    return json.dumps({"conclusion": "需要更新该角色的视觉身份",
                       "affected_entities": [{"module": "character", "entity_id": "Xiaoyu"}],
                       "downstream_facts": ["含该角色的镜头", "storyboard s02"],
                       "blockers": [], "user_decisions": [], "evidence": []})


def _analysis_json():
    return json.dumps({
        "direct_changes": [{"module": "character", "owner_agent": "character_designer",
                            "summary": "Xiaoyu 改长头发", "reason": "user_requested"}],
        "downstream_changes": [{"module": "storyboard", "owner_agent": "director",
                                "summary": "含 Xiaoyu 的镜头", "reason": "downstream_impact"}],
        "unchanged": [{"module": "script", "entity_id": "s01"}],
        "user_decisions_needed": ["是否保留旧角色版本"]})


class ScriptedClient:
    """按最后一条 user 消息内容分流返回（咨询 JSON / 综合 JSON / 答案文本）。"""
    def __init__(self, producer="项目级答案", consult=None, synthesis=None,
                 faulty_module=None):
        self.producer = producer
        self.consult = consult or _consult_json()
        self.synthesis = synthesis or _analysis_json()
        self.faulty_module = faulty_module   # 该模块的咨询返回坏 JSON
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        u = messages[-1]["content"] if messages else ""
        if "咨询问题" in u:
            if self.faulty_module and f"「{self.faulty_module}」" in u:
                return "this is not json"
            return self.consult
        if "咨询结果" in u or "综合" in u:
            return self.synthesis
        return self.producer


class RaisingClient:
    """对第 n 次之后的调用抛异常，模拟咨询失败。"""
    def __init__(self, fail_after=1):
        self.fail_after = fail_after
        self.calls = 0
    async def chat(self, messages, **kw):
        self.calls += 1
        if self.calls > self.fail_after:
            raise RuntimeError("network down")


# ============ 1. 请求分类（§33 确定性路由）============
def test_classify_request():
    p = _proj()
    c = _classify("把 Xiaoyu 的头发改长", p)
    assert c["kind"] == "change"
    assert "character_designer" in c["modules"]
    assert c["character_refs"] == ["Xiaoyu"]

    q = _classify("Xiaoyu 出现在哪些场景", p)
    assert q["kind"] == "question"
    assert q["character_refs"] == ["Xiaoyu"]

    ch = _classify("修改结局让 Xiaoyu 救 Aran", p)
    assert ch["kind"] == "change"
    assert "script_writer" in ch["modules"]
    assert set(ch["character_refs"]) == {"Xiaoyu", "Aran"}

    sc = _classify("把 s03 的镜头节奏调整一下", p)
    assert sc["kind"] == "change"
    assert "director" in sc["modules"]
    assert sc["scene_refs"] == ["s03"]


# ============ 2. 有界 ContextPack（§9/§21）============
def test_context_pack_relevant_and_bounded(tmp_path):
    _save(tmp_path)
    rt = _rt(tmp_path)
    intel = rt.project_intelligence("demo")
    b = _Builder(intel)
    pack = b.build_for_producer(request="Xiaoyu 出现在哪些场景",
                                classification=_classify("Xiaoyu 出现在哪些场景", _proj()))
    assert pack.project_overview is not None
    assert "character_usage:Xiaoyu" in pack.module_context
    assert any(r.entity_id == "Xiaoyu" for r in pack.evidence_refs)
    # 极小预算 → 截断标记（§21 不静默丢信息）
    tiny = _Builder(intel, _Budget(module_chars=10, overview_chars=10))
    tpack = tiny.build_for_producer(request="Xiaoyu 出现在哪些场景",
                                    classification=_classify("Xiaoyu 出现在哪些场景", _proj()))
    assert tpack.truncated_sections


# ============ 3. 咨询只读 + AgentTask 持久化（§32/§43）============
def test_consult_specialist_read_only_and_persisted(tmp_path):
    _save(tmp_path)
    rt = _rt(tmp_path, client=ScriptedClient())
    intel = rt.project_intelligence("demo")
    pack = _Builder(intel).build_for_specialist(
        agent_id="character_designer", request="把 Xiaoyu 头发改长",
        scope=[ProjectEvidenceRef(module="character", entity_type="character",
                                  entity_id="Xiaoyu")],
        consultation_question="角色层面直接需改什么")
    before = (tmp_path / "output" / "project.json").read_bytes()
    result = _consult_specialist(rt, "demo", "character_designer",
                                 "角色层面直接需改什么", pack, client=ScriptedClient())
    assert result.status == "completed"
    assert result.agent_task_id
    # AgentTask 落盘 + completed + 只读 cap
    task = AgentTaskStore(tmp_path, "demo").get(result.agent_task_id)
    assert task is not None
    assert task.status == "completed"
    assert is_read_only_capability(task.capability_id)
    # canonical 不变（§43）
    assert (tmp_path / "output" / "project.json").read_bytes() == before


# ============ 4. 问题路径（§50）============
def test_run_inquiry_question_path(tmp_path):
    _save(tmp_path)
    rt = _rt(tmp_path, client=ScriptedClient(producer="Xiaoyu 出现在 s02"))
    before = (tmp_path / "output" / "project.json").read_bytes()
    out = _run_inquiry(rt, "demo", "Xiaoyu 出现在哪些场景", client=ScriptedClient(producer="Xiaoyu 出现在 s02"))
    assert out["kind"] == "question"
    assert "s02" in out["answer"]
    assert out["evidence_refs"]
    assert out["context_observability"]["classification"]["kind"] == "question"
    # 问题路径不咨询 → 0 AgentTask
    assert AgentTaskStore(tmp_path, "demo").list() == []
    # canonical 不变
    assert (tmp_path / "output" / "project.json").read_bytes() == before


# ============ 5. 变更路径 analysis-only（§37/§38/§39）============
def test_run_inquiry_change_path(tmp_path):
    _save(tmp_path)
    rt = _rt(tmp_path, client=ScriptedClient())
    before = (tmp_path / "output" / "project.json").read_bytes()
    out = _run_inquiry(rt, "demo", "把 Xiaoyu 的头发改长，并修改结局",
                       client=ScriptedClient())
    assert out["kind"] == "change"
    a = out["analysis"]
    assert a["execution_ready"] is False          # §37：恒 False
    assert a["change_operations"] == []            # §38：0 可执行操作
    assert a["direct_changes"] and a["direct_changes"][0]["reason"] == "user_requested"
    assert a["downstream_changes"]
    assert a["user_decisions_needed"]
    # 咨询已持久化为 AgentTask（§32）
    tasks = AgentTaskStore(tmp_path, "demo").list()
    assert tasks and all(is_read_only_capability(t.capability_id) for t in tasks)
    # 无 workspace（§43：不建 mutation 产物）
    assert not (tmp_path / "workspaces").exists() or not list((tmp_path / "workspaces").iterdir())
    # canonical 不变
    assert (tmp_path / "output" / "project.json").read_bytes() == before


def test_analysis_strips_accidental_ops(tmp_path):
    """§38：LLM 若误塞 op 字段，归一化必须剥除。"""
    _save(tmp_path)
    bad = json.dumps({
        "direct_changes": [{"module": "character", "owner_agent": "character_designer",
                            "summary": "改", "reason": "user_requested",
                            "op": "update_shot", "after": {"x": 1}}],
        "downstream_changes": [], "unchanged": [], "user_decisions_needed": []})
    rt = _rt(tmp_path, client=ScriptedClient(synthesis=bad))
    out = _run_inquiry(rt, "demo", "把 Xiaoyu 的头发改长", client=ScriptedClient(synthesis=bad))
    a = out["analysis"]
    assert a["execution_ready"] is False
    assert a["change_operations"] == []
    assert all("op" not in d and "after" not in d for d in a["direct_changes"])


# ============ 6. 并行独立结算（§34）============
def test_parallel_consult_independent_settle(tmp_path):
    _save(tmp_path)
    # character 咨询成功，script 咨询坏 JSON → blocked；两者都落盘、独立结算
    rt = _rt(tmp_path, client=ScriptedClient(faulty_module="script"))
    out = _run_inquiry(rt, "demo", "把 Xiaoyu 的头发改长，并修改结局",
                       client=ScriptedClient(faulty_module="script"))
    cons = out["consultations"]
    statuses = {c["owner_agent"]: c["status"] for c in cons}
    assert statuses.get("character_designer") == "completed"
    assert statuses.get("script_writer") == "blocked"
    # 两个 task 都落盘，一个 completed 一个 failed
    tasks = {t.target_agent: t for t in AgentTaskStore(tmp_path, "demo").list()}
    assert tasks["character_designer"].status == "completed"
    assert tasks["script_writer"].status == "failed"


# ============ 7. max 2 轮、无无限循环（§35）============
def test_no_infinite_consultation_loop(tmp_path):
    _save(tmp_path)
    rt = _rt(tmp_path, client=ScriptedClient())
    out = _run_inquiry(rt, "demo", "把 Xiaoyu 的头发改长，并修改结局",
                       client=ScriptedClient(), max_rounds=1)
    # round1 咨询数 == 模块数；无自动 round2
    modules = out["context_observability"]["classification"]["modules"]
    assert len(out["consultations"]) == len(modules)
    # max_rounds=2 也不会无限：咨询数 <= 模块数 + 1
    out2 = _run_inquiry(rt, "demo", "把 Xiaoyu 的头发改长，并修改结局",
                        client=ScriptedClient(), max_rounds=2)
    assert len(out2["consultations"]) <= len(modules) + 1


# ============ 8. §96 经 Producer 路径 mutation 被拒、无底层 workflow ============
def test_producer_path_cannot_mutate(tmp_path):
    """§96 强制测试：AI-7A inquiry/咨询路径对全部 mutation 能力 → permission_denied
    （assert_read_only_capability 硬闸）+ 路径只产只读 task，无底层 mutation workflow。"""
    _save(tmp_path)
    rt = _rt(tmp_path, client=ScriptedClient())
    r = Registries()
    # §43/§96：AI-7A 路径的只读硬闸对全部 mutation cap 一律 permission_denied
    for cid in ("storyboard.propose_change", "production_plan.create",
                "shot.video", "shot.first_frame", "shot.last_frame",
                "story.edit", "character.design", "composition.compose"):
        with pytest.raises(AgentError) as ei:
            assert_read_only_capability(cid)
        assert ei.value.code == "permission_denied"
    # 结构性：producer 对全部 mutation cap 无权 execute
    for cid in ("storyboard.propose_change", "production_plan.create",
                "shot.video", "shot.first_frame", "shot.last_frame",
                "story.edit", "character.design", "composition.compose"):
        assert r.capabilities.can_execute("producer", cid, "execute") is False
    # inquiry 路径产生的所有 task 都是只读 inspect cap（无 mutation workflow）
    out = _run_inquiry(rt, "demo", "把 Xiaoyu 的头发改长", client=ScriptedClient())
    for t in AgentTaskStore(tmp_path, "demo").list():
        assert is_read_only_capability(t.capability_id)
    assert out["analysis"]["execution_ready"] is False
    # 无 workspace 产物（§43：不建 mutation 产物）
    assert not (tmp_path / "workspaces").exists() or not list((tmp_path / "workspaces").iterdir())


# ============ 9. Agnes 未配置软失败（不抛）============
def test_inquiry_soft_fails_without_client(tmp_path):
    _save(tmp_path)
    rt = _rt(tmp_path, client=None, agnes_getter=lambda: (_ for _ in ()).throw(
        __import__("fastapi").HTTPException(503, "not ready")))
    # 问题路径：返回未配置提示，不抛
    out = _run_inquiry(rt, "demo", "Xiaoyu 出现在哪些场景", client=None)
    assert out["kind"] == "question"
    assert "未配置" in out["answer"]
    # 变更路径：咨询 blocked + 兜底 analysis，不抛
    out2 = _run_inquiry(rt, "demo", "把 Xiaoyu 头发改长", client=None)
    assert out2["kind"] == "change"
    assert all(c["status"] == "blocked" for c in out2["consultations"])
    assert out2["analysis"]["execution_ready"] is False
