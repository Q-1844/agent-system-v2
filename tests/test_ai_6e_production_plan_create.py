"""AI-6E production_plan.create 集成测试（§25/§26/§27/§28）。

engine 级：直接驱动 AgentRuntime.delegate(production_plan.create) + FakeAgnes 返回 plan JSON，
不调真 Agnes（§25：Use deterministic/fake Agnes responses）。证明：
- delegation async 路径产出真实 SceneProductionPlan 落 production_plans.json（§25-1~9）
- AI 不能 authoritative over deterministic fields（§26，经 Agent 路径产出验证 derived）
- canonical project.json 字节不变 + 不写 shot.opening_source（§27/§18）
- Agent 创建的 plan 参与同一 stale 语义（§28/§19）

与现有 test_production_planner.py（端点级）互补：本文件测 Agent delegation 路径产出
与现有 Planner 端点产出**同一 artifact**（§16 唯一源真相）。
"""
import asyncio
import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor, Asset
from src.core.store import ProjectStore
from src.agents.models import TaskStatus, ResultStatus
from src.agents.runtime import AgentRuntime
from src.agents.capabilities import CapabilityContext, dispatch
from src.agents.tasks import AgentTaskStore
from src.ai.production_planner import read_plans, is_plan_stale

PROJECT = "_test_ai6e"


class FakeAgnes:
    """支持 json_mode + responses 队列（对齐 test_production_planner.FakeAgnes）。"""
    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.calls = []

    async def chat(self, messages, *, temperature=0.7, max_tokens=2048,
                   thinking=False, json_mode=False):
        self.calls.append({"json_mode": json_mode, "n": len(messages)})
        if not self._responses:
            return ""
        return self._responses.pop(0)


def _shot(sid, idx, desc, chars=None, dur=5.0):
    return Shot(shot_id=sid, scene_id=sid.rsplit("_c", 1)[0], index=idx,
                description=desc, characters=chars or [], target_duration_s=dur)


def _save_proj(tmp_path, shots=None):
    """1 scene 多 shot（默认 3 shot 覆盖 reference/inherit derivation）。"""
    shots = shots if shots is not None else [
        _shot("s01_c01", 1, "机甲启动", ["阿派"]),
        _shot("s01_c02", 2, "转头确认", ["阿派"]),
        _shot("s01_c03", 3, "迈步出发", ["阿派"]),
    ]
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事",
                   characters=[Character(name="阿派", description_en="机甲")],
                   scenes=[Scene(scene_id="s01", summary="机甲出发", characters=["阿派"],
                                 shots=shots)])
    ProjectStore(tmp_path).save(proj)
    return tmp_path


def _plans_reader_for(tmp_path):
    """返 dict（model_dump），对齐 composition root（routers/agents._plans_reader 返 dict）。"""
    def _r(project, scene_id):
        pl = read_plans(tmp_path).get(scene_id)
        return pl.model_dump() if pl else None
    return _r


def _runtime(tmp_path, client=None, **kw):
    kw.setdefault("settings_path", tmp_path / "agents_data" / "settings.json")
    rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path, client=client, **kw)
    # 注入 plans_reader 让 production_plan.inspect / build_context 读同一 production_plans.json
    rt.set_plans_reader(_plans_reader_for(tmp_path))
    return rt


# AI 返回：c01 independent，c02/c03 reference_previous（测 source_shot_id derive）
PLAN_REF = ('{"overview":"参考上一镜","shots":['
            '{"shot_id":"s01_c01","opening_source":"independent","link_mode":"i2v","reason":"首镜"},'
            '{"shot_id":"s01_c02","opening_source":"reference_previous","link_mode":"keyframes","reason":"连续"},'
            '{"shot_id":"s01_c03","opening_source":"reference_previous","link_mode":"i2v","reason":"迈步"}]}')


# ============ §25 real Planner integration ============
def test_producer_delegates_and_plan_persisted(tmp_path):
    """§25-1~5: Producer delegate -> AgentTask completed -> 真实 SceneProductionPlan 落 production_plans.json。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeAgnes([PLAN_REF]))
    task = asyncio.run(rt.delegate(PROJECT, "producer", "production_planner", "scene", "s01",
                "规划", "production_plan.create", "execute"))
    # §25-1 task completed
    assert task["status"] == TaskStatus.COMPLETED.value
    # §25-3 现有 Planner workflow 被调用（FakeAgnes.chat 被 call，json_mode=True）
    assert rt._client.calls, "Planner workflow 应被调用"
    assert rt._client.calls[0]["json_mode"] is True
    # §25-5 plan 持久化在 production_plans.json
    plans = read_plans(tmp_path)
    assert "s01" in plans
    plan = plans["s01"]
    assert len(plan.shots) == 3
    assert plan.status == "draft"   # §17 不 approve
    assert plan.run_seed >= 0


def test_inspect_reads_same_plan_after_create(tmp_path):
    """§25-6: production_plan.create 后 production_plan.inspect 立即读到同一 plan（同源）。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeAgnes([PLAN_REF]))
    asyncio.run(rt.delegate(PROJECT, "producer", "production_planner", "scene", "s01",
                "规划", "production_plan.create", "execute"))
    # inspect capability 读同一 production_plans.json（经注入 plans_reader）
    ctx = CapabilityContext(project=PROJECT, scope_type="scene", scope_id="s01",
                            project_loader=lambda: ProjectStore(tmp_path).load(),
                            plans_reader=_plans_reader_for(tmp_path))
    res = dispatch("production_plan.inspect", ctx)
    assert res.status == ResultStatus.COMPLETED.value
    assert res.data["has_plan"] is True
    assert res.data["plan_status"] == "draft"
    assert len(res.data["shots"]) == 3


def test_structured_result_references_artifact(tmp_path):
    """§25-7: AgentStructuredResult 引用真实 artifact（不建第二份 copy）。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeAgnes([PLAN_REF]))
    task = asyncio.run(rt.delegate(PROJECT, "producer", "production_planner", "scene", "s01",
                "规划", "production_plan.create", "execute"))
    refs = task["artifact_refs"]
    # production_plan artifact + 3 shot refs
    assert any(r["type"] == "production_plan" and r["scene_id"] == "s01" for r in refs)
    assert sum(1 for r in refs if r["type"] == "shot") == 3
    # data 与落盘 plan 同源（plan_id/run_seed 一致，非第二份 authoritative copy）
    plan = read_plans(tmp_path)["s01"]
    data = task["structured_result"]["data"]
    assert data["plan_id"] == plan.plan_id
    assert data["run_seed"] == plan.run_seed


def test_runtime_recovers_completed_task(tmp_path):
    """§25-8: 新 AgentTaskStore/runtime 能恢复 completed task。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeAgnes([PLAN_REF]))
    task = asyncio.run(rt.delegate(PROJECT, "producer", "production_planner", "scene", "s01",
                "规划", "production_plan.create", "execute"))
    # 新 store 同目录恢复
    store2 = AgentTaskStore(tmp_path, PROJECT)
    got = store2.get(task["task_id"])
    assert got is not None
    assert got.status == TaskStatus.COMPLETED.value
    assert got.capability_id == "production_plan.create"
    assert got.target_agent == "production_planner"


def test_existing_planner_reader_reads_same_plan(tmp_path):
    """§25-9: 现有 Production Planner reader（routers.production_planner._read_plans）能读同一 plan。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeAgnes([PLAN_REF]))
    asyncio.run(rt.delegate(PROJECT, "producer", "production_planner", "scene", "s01",
                "规划", "production_plan.create", "execute"))
    # 现有 router 的 _read_plans（AI-6E alias -> read_plans）读到同一 plan
    from routers.production_planner import _read_plans
    plans = _read_plans(tmp_path)
    assert "s01" in plans
    assert plans["s01"].shots[1].source_shot_id == "s01_c01"   # derived 一致


# ============ §26 deterministic planning boundary（Agent 路径产出验证）============
def test_agent_path_derives_source_shot_id_not_ai(tmp_path):
    """§26: AI 不负责 source_shot_id；代码 derive = 紧邻上一镜。PLAN_REF 的 c02/c03
    未给 source_shot_id（AI 本就不该给），derive 出 c01/c02。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeAgnes([PLAN_REF]))
    task = asyncio.run(rt.delegate(PROJECT, "producer", "production_planner", "scene", "s01",
                "规划", "production_plan.create", "execute"))
    data = task["structured_result"]["data"]
    # 第一镜 independent，source=None
    assert data["shots"][0]["opening_source"] == "independent"
    assert data["shots"][0]["source_shot_id"] is None
    # c02 reference -> source=c01（derive）
    assert data["shots"][1]["source_shot_id"] == "s01_c01"
    assert data["shots"][1]["requires_previous_video_end"] is True
    assert data["shots"][1]["generate_first_frame"] is True   # reference 仍生成首帧
    # c03 reference -> source=c02
    assert data["shots"][2]["source_shot_id"] == "s01_c02"
    # c02 keyframes -> 生成尾帧；c03 i2v -> 不生成尾帧
    assert data["shots"][1]["generate_last_frame"] is True
    assert data["shots"][2]["generate_last_frame"] is False


def test_agent_path_first_shot_forced_independent(tmp_path):
    """§26: 第一镜即使 AI 说 inherit_previous 也强制 independent。"""
    _save_proj(tmp_path)
    bad_plan = ('{"overview":"x","shots":['
                '{"shot_id":"s01_c01","opening_source":"inherit_previous","link_mode":"i2v","reason":"x"},'
                '{"shot_id":"s01_c02","opening_source":"independent","link_mode":"i2v","reason":"y"},'
                '{"shot_id":"s01_c03","opening_source":"independent","link_mode":"i2v","reason":"z"}]}')
    rt = _runtime(tmp_path, client=FakeAgnes([bad_plan]))
    task = asyncio.run(rt.delegate(PROJECT, "producer", "production_planner", "scene", "s01",
                "规划", "production_plan.create", "execute"))
    data = task["structured_result"]["data"]
    assert data["shots"][0]["opening_source"] == "independent"
    assert data["shots"][0]["source_shot_id"] is None
    assert data["shots"][0]["generate_first_frame"] is True


# ============ §27 canonical project.json protection ============
def test_canonical_project_json_bytes_unchanged(tmp_path):
    """§27: delegate production_plan.create 前后 project.json 字节不变。"""
    _save_proj(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    rt = _runtime(tmp_path, client=FakeAgnes([PLAN_REF]))
    asyncio.run(rt.delegate(PROJECT, "producer", "production_planner", "scene", "s01",
                "规划", "production_plan.create", "execute"))
    after = (tmp_path / "output" / "project.json").read_bytes()
    assert before == after, "canonical project.json 被 production_plan.create 修改（违反 §18）"


def test_create_does_not_write_shot_opening_source(tmp_path):
    """§18/§27: plan 含 reference_previous，但 canonical Shot.opening_source 仍 independent（plan 是意图）。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeAgnes([PLAN_REF]))
    asyncio.run(rt.delegate(PROJECT, "producer", "production_planner", "scene", "s01",
                "规划", "production_plan.create", "execute"))
    proj = ProjectStore(tmp_path).load()
    sc = next(s for s in proj.scenes if s.scene_id == "s01")
    for sh in sc.shots:
        assert sh.opening_source == "independent"   # canonical 默认，plan 未写
        assert sh.source_shot_id is None
        assert sh.source_version is None


# ============ §28 stale behavior（Agent 创建的 plan 参与同一 stale 语义）============
def test_agent_created_plan_participates_in_stale(tmp_path):
    """§28: Agent 创建 plan -> fresh -> 改 Storyboard summary -> is_plan_stale 报 stale。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeAgnes([PLAN_REF]))
    asyncio.run(rt.delegate(PROJECT, "producer", "production_planner", "scene", "s01",
                "规划", "production_plan.create", "execute"))
    # fresh: 不 stale
    plan = read_plans(tmp_path)["s01"]
    proj = ProjectStore(tmp_path).load()
    sc = next(s for s in proj.scenes if s.scene_id == "s01")
    assert is_plan_stale(plan, sc) is None
    # 改 scene summary -> stale（三快照之一变）
    sc.summary = "改过的故事段"
    ProjectStore(tmp_path).save(proj)
    plan2 = read_plans(tmp_path)["s01"]
    proj2 = ProjectStore(tmp_path).load()
    sc2 = next(s for s in proj2.scenes if s.scene_id == "s01")
    assert is_plan_stale(plan2, sc2) is not None


def test_agent_created_plan_not_stale_on_asset_change(tmp_path):
    """§28/§19: 生成资产/Views 变化不触发 stale（与现有 Planner 同语义，无第二 stale checker）。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeAgnes([PLAN_REF]))
    asyncio.run(rt.delegate(PROJECT, "producer", "production_planner", "scene", "s01",
                "规划", "production_plan.create", "execute"))
    proj = ProjectStore(tmp_path).load()
    sc = next(s for s in proj.scenes if s.scene_id == "s01")
    # 改资产状态 + 角色 views（§八：不触发 stale）
    sc.shots[0].first_frame.mark_done("ff.png", "https://x/ff.png")
    sc.shots[0].status = "done"
    proj.get_character("阿派").views["front"] = Asset(local="c.png", status="done")
    ProjectStore(tmp_path).save(proj)
    plan = read_plans(tmp_path)["s01"]
    proj2 = ProjectStore(tmp_path).load()
    sc2 = next(s for s in proj2.scenes if s.scene_id == "s01")
    assert is_plan_stale(plan, sc2) is None


# ============ §9/§16/§23 软失败 + 无效输出 ============
def test_not_configured_when_no_client(tmp_path):
    """§9/§23: Agnes 未配置 -> NOT_CONFIGURED 软失败，task FAILED，无 plan 落盘。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=None, agnes_getter=lambda: None)
    task = asyncio.run(rt.delegate(PROJECT, "producer", "production_planner", "scene", "s01",
                "规划", "production_plan.create", "execute"))
    assert task["status"] == TaskStatus.FAILED.value
    assert "未配置" in task["error"]
    assert "s01" not in read_plans(tmp_path)


def test_agent_path_invalid_ai_output_failed(tmp_path):
    """§16: AI 输出无法解析 -> 一次 repair 仍失败 -> FAILED，不留 plan。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeAgnes(["not json", "still not json"]))
    task = asyncio.run(rt.delegate(PROJECT, "producer", "production_planner", "scene", "s01",
                "规划", "production_plan.create", "execute"))
    assert task["status"] == TaskStatus.FAILED.value
    assert "s01" not in read_plans(tmp_path)
