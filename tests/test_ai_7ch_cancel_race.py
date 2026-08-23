# tests/test_ai_7ch_cancel_race.py
# AI-7C-H Cancel/Restart Hotfix：六个关键竞态测试（§十三 Case 1-6 / Gate 1-4）。
#
# 不联网（0 真 Agnes）：view_generator / plan_creator / run_starter 全注入 fake；
# ProjectAutopilotManager 经 monkeypatch 注入 fake（取消传播精确 run_id 断言）。
import asyncio

import pytest

import deps as deps_mod
from src.core.models import Project, Scene, Shot, Character, StyleAnchor, Asset
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.change_protocol import execution_service as cpx
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol.models import (UnifiedChangePlan, PlanPreviewArtifact,
                                                PlanApproval, ChangeSession)
from src.revision.models import ChangeOperation, BeforeAfter, creative_source_signature
from src.revision.changeset import preview_operations_on_project
from src.ai import project_autopilot as pap
from src.ai.project_autopilot import ProjectProductionRun
from src.utils.project_manager import ProjectManager


# ============ fixture：2 角色 + 2 场景项目（update_character 双角色 -> char sync x2 / planner scene x2）============
def _proj_2c2s():
    return Project(
        name="demo", style=StyleAnchor(),
        characters=[Character(name="Xiaoyu", description_en="a girl"),
                    Character(name="Dongdong", description_en="a boy")],
        scenes=[Scene(scene_id="s01", summary="meet", characters=["Xiaoyu"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="see", characters=["Xiaoyu"],
                                  target_duration_s=4, link_mode="i2v")]),
                Scene(scene_id="s02", summary="talk", characters=["Xiaoyu", "Dongdong"],
                      shots=[Shot(shot_id="s02_c01", scene_id="s02", index=1,
                                  description="say", characters=["Xiaoyu", "Dongdong"],
                                  target_duration_s=4, link_mode="i2v")])],
        story_text="once upon a time")


def _proj_1c1s():
    return Project(
        name="demo", style=StyleAnchor(),
        characters=[Character(name="Xiaoyu", description_en="a girl")],
        scenes=[Scene(scene_id="s01", summary="meet", characters=["Xiaoyu"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="see", characters=["Xiaoyu"],
                                  target_duration_s=4, link_mode="i2v")])],
        story_text="once upon a time")


class _NoCall:
    def __init__(self): self.calls = 0

    async def chat(self, *a, **k):
        self.calls += 1
        raise AssertionError("cancel race 路径不应调 Agnes")


async def _seed(tmp_path, proj, second_char_op=False):
    """建 approved plan + commit -> committed execution（stage=character_sync）。
    second_char_op=True 时 ops 含第二个角色（manifest.character_actions=2 个 regenerate）。"""
    ProjectStore(tmp_path).save(proj)
    rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                      settings_path=tmp_path / "agents_data" / "settings.json",
                      client=_NoCall())
    sig = creative_source_signature(proj.to_dict())
    store = ChangeProtocolStore(tmp_path, "demo")
    store.save_session(ChangeSession(session_id="cs_t", project_name="demo",
                                     status="approved_for_execution",
                                     active_plan_id="plan_t", plan_version=1,
                                     source_snapshot_signature=sig, schema_version=2,
                                     created_at=1.0, updated_at=1.0))
    ops = [ChangeOperation(op_type="update_character", character_name="Xiaoyu",
                           description_en=BeforeAfter(before="a girl", after="a girl black hair"))]
    if second_char_op:
        ops.append(ChangeOperation(op_type="update_character", character_name="Dongdong",
                                   description_en=BeforeAfter(before="a boy", after="a boy red hair")))
    source = ProjectStore(tmp_path).load()
    candidate, _d, impact, _w = preview_operations_on_project(source, ops)
    projected = creative_source_signature(candidate.to_dict())
    plan = UnifiedChangePlan(plan_id="plan_t", change_session_id="cs_t", project_name="demo",
                             version=1, source_snapshot_signature=sig,
                             status="approved_for_execution",
                             steps=[{"step_id": "step_x", "module": "character",
                                     "owner_agent_id": "character_designer", "plan_item_ids": [],
                                     "depends_on_step_ids": [], "parallel_group": None}],
                             preview_id="pv_t", created_at=1.0)
    plan.approval = PlanApproval(plan_id="plan_t", plan_version=1, decision="approved",
                                 source_snapshot_signature=sig, decided_at=2.0).to_dict()
    store.save_plan(plan)
    store.save_preview(PlanPreviewArtifact(
        preview_id="pv_t", change_session_id="cs_t", plan_id="plan_t", plan_version=1,
        source_snapshot_signature=sig,
        module_operations=[{"step_id": "step_x", "module": "character",
                            "owner_agent_id": "character_designer", "proposal_item_ids": [],
                            "operations": [o.to_dict() for o in ops]}],
        aggregate_impact=impact.to_dict(), projected_signature=projected,
        applyable=False, created_at=1.0))
    await cpx.start_replay_commit(rt, "demo", "cs_t", "plan_t", 1, "affected_production")
    return rt


def _exec(rt):
    return ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo").find_execution("plan_t", 1)


def _views(cn):
    return {v: Asset(url=f"https://fake/{cn}/{v}.png", status="done")
            for v in ("front", "side", "back")}


async def _sync_all_chars(rt, eid):
    """快速把 char sync 推到 production_planning（无屏障，直接完成全部角色）。"""

    async def gen(cn):
        return _views(cn)
    return await cpx.run_character_sync(rt, "demo", eid, view_generator=gen)


def _kickoff_with_fakes(monkeypatch, rt, eid, creator=None, starter=None):
    """经真实 _maybe_kickoff_postcommit 派发 post-commit 后台任务（与生产路径一致，
    _INFLIGHT_POSTCOMMIT 记账生效 -> cancel 收敛语义正确），同时 monkeypatch
    run_production_planning 注入 fake creator/starter（_run_postcommit_to_completion
    运行时解析模块全局名，可替换）。返回后台 task。"""
    orig = cpx.run_production_planning

    async def _wrapped(rt_, project_, execution_id_):
        return await orig(rt_, project_, execution_id_,
                          plan_creator=creator, run_starter=starter)
    monkeypatch.setattr(cpx, "run_production_planning", _wrapped)
    ex = _exec(rt)
    assert cpx._maybe_kickoff_postcommit(rt, "demo", ex) is True
    return cpx._INFLIGHT_POSTCOMMIT[eid]


def _cleanup_watchers(eid):
    w = cpx._STOP_WATCHERS.pop(eid, None)
    if w is not None:
        w.cancel()


# ============ fake ProjectAutopilotManager（取消传播精确断言）============
class _FakeRun:
    def __init__(self, run_id, status="running"):
        self.run_id = run_id
        self.status = status
        self.cancel_requested = False


class _FakeAutopilotMgr:
    def __init__(self, runs):
        self.runs = {r.run_id: r for r in runs}
        self.cancel_calls = []   # [(project, run_id)]

    def get_run(self, project, run_id):
        return self.runs.get(run_id)

    def list_runs(self, project):
        return list(self.runs.values())

    async def request_cancel(self, project, run_id):
        self.cancel_calls.append((project, run_id))
        r = self.runs.get(run_id)
        if r is not None:
            r.cancel_requested = True
        return r


@pytest.fixture
def fake_autopilot(monkeypatch):
    """注入 fake autopilot manager；返回 (mgr, install(runs))。"""
    mgr = _FakeAutopilotMgr([])

    def install(runs):
        mgr.runs = {r.run_id: r for r in runs}
        return mgr
    monkeypatch.setattr("src.ai.project_autopilot.get_project_autopilot_manager", lambda: mgr)
    return mgr, install


# ============ Case 1 — stale object resurrection（最关键测试，§十三 Case 1）============
def test_case1_stale_object_resurrection(tmp_path, monkeypatch):
    """确定性 barrier：后台已读完 running ex、暂停在角色 B 生成中（保存前）→
    另一协程 cancel（磁盘落 cancel_requested）→ 释放后台继续。
    最终：Execution 不能恢复成普通 running；B 单元自然结束（record 允许）；
    不进入 planning；收敛 cancelled。"""

    async def main():
        rt = await _seed(tmp_path, _proj_2c2s(), second_char_op=True)
        ex0 = _exec(rt)
        eid = ex0.execution_id
        assert ex0.stage == "character_sync"
        store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
        assert len(store.get_execution(eid).character_sync) == 0

        b_started = asyncio.Event()
        release_b = asyncio.Event()
        gen_calls = []

        async def gen(cn):
            gen_calls.append(cn)
            if cn == "Dongdong":
                b_started.set()
                await release_b.wait()   # barrier：暂停在 B 保存前
            return _views(cn)

        # 真实后台路径默认走 _real_character_view_generator（JobQueue 真路径，§103）；
        # 注入 fake gen（run_character_sync 的默认 lambda 运行时解析模块全局名，可替换）
        monkeypatch.setattr(cpx, "_real_character_view_generator",
                            lambda rt_, project_, execution_id_, cn: gen(cn))

        # 后台 kickoff（真实路径：_maybe_kickoff_postcommit -> _run_postcommit_to_completion）
        assert cpx._maybe_kickoff_postcommit(rt, "demo", ex0) is True
        await b_started.wait()          # B 单元已开始，后台持 stale ex
        ex_mid = store.get_execution(eid)
        assert ex_mid.status == "running" and not ex_mid.cancel_requested

        # 另一协程 cancel（B 在飞）：committed -> 停止请求（不提前 cancelled，§三/§八）
        res = await cpx.cancel_execution(rt, "demo", eid)
        assert res["creative_committed"] is True
        ex_c = store.get_execution(eid)
        assert ex_c.cancel_requested is True
        assert ex_c.status == "running"   # 「正在停止…」，非提前 cancelled

        release_b.set()                   # 释放旧后台继续（stale 保存竞态窗口）
        await asyncio.sleep(0.2)          # 等后台任务收尾（record + 边界拒绝 + finally 收敛）
        for _ in range(50):
            ex_f = store.get_execution(eid)
            if ex_f.status == "cancelled":
                break
            await asyncio.sleep(0.05)
        ex_f = store.get_execution(eid)
        # Gate 1：旧 Execution 永远不能复活（不回到普通 running）
        assert ex_f.status == "cancelled"
        assert ex_f.cancel_requested is True
        # Gate 2：不产生后续新角色工作（gen 只调 A+B，无第三角色），不进入 planning
        assert sorted(gen_calls) == ["Dongdong", "Xiaoyu"]
        assert ex_f.stage == "character_sync"          # 未推进 stage
        assert ex_f.planning == []                     # 未进入 planning
        # 已开始的 B 单元自然结束（record 允许）+ A 完成保留（不回滚）
        synced = sorted(r["character_name"] for r in ex_f.character_sync)
        assert synced == ["Dongdong", "Xiaoyu"]
        assert len(cpx._INFLIGHT_POSTCOMMIT) == 0
        _cleanup_watchers(eid)

    asyncio.run(main())


# ============ Case 2 — cancel during character_sync（A 完成、B 未开始，§十三 Case 2）============
def test_case2_cancel_between_chars_no_new_work(tmp_path):
    """A 已 sync、B 尚未开始时取消：不启动 B；不进入 planning；restart 后也不续跑。"""

    async def main():
        rt = await _seed(tmp_path, _proj_2c2s(), second_char_op=True)
        ex0 = _exec(rt)
        eid = ex0.execution_id
        store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
        # 手动记 A 已完成（B 未开始）
        ex0.character_sync.append({"character_name": "Xiaoyu", "status": "completed",
                                   "views": ["front", "side", "back"], "completed_at": 1.0})
        store.save_execution(ex0)

        # 取消（无在飞任务/无 run -> 立即收敛 cancelled）
        await cpx.cancel_execution(rt, "demo", eid)
        ex_c = store.get_execution(eid)
        assert ex_c.status == "cancelled"
        assert ex_c.cancel_requested is True

        # 取消后直接调 char sync / kickoff：0 新角色工作（gen 0 调用）
        calls = []

        async def gen(cn):
            calls.append(cn)
            return _views(cn)
        res = await cpx.run_character_sync(rt, "demo", eid, view_generator=gen)
        assert res["idempotent"] is True
        assert calls == []
        # §九：cancel_requested 不 kickoff post-commit
        assert cpx._maybe_kickoff_postcommit(rt, "demo", store.get_execution(eid)) is False
        assert len(cpx._INFLIGHT_POSTCOMMIT) == 0
        # 不进入 planning，A 记录保留
        ex_f = store.get_execution(eid)
        assert ex_f.stage == "character_sync"
        assert [r["character_name"] for r in ex_f.character_sync] == ["Xiaoyu"]
        _cleanup_watchers(eid)

    asyncio.run(main())


# ============ Case 3 — cancel during planning（Scene1 完成、Scene2 开始时，§十三 Case 3）============
def test_case3_cancel_during_planning_no_run(tmp_path, monkeypatch):
    """s01 完成、s02 正在开始时取消：不规划新 Scene（s02 已开始单元可结束）；
    不进入 run starter；不创建 ProductionRun；最终 cancelled。
    经真实 _maybe_kickoff_postcommit 派发（in-flight 记账生效，§三 stopping 语义可观测）。"""

    async def main():
        rt = await _seed(tmp_path, _proj_2c2s(), second_char_op=True)
        ex0 = _exec(rt)
        eid = ex0.execution_id
        await _sync_all_chars(rt, eid)          # -> production_planning
        store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
        assert store.get_execution(eid).stage == "production_planning"

        s02_started = asyncio.Event()
        release_s02 = asyncio.Event()
        creator_calls = []
        starter_calls = []

        async def creator(sid):
            creator_calls.append(sid)
            if sid == "s02":
                s02_started.set()
                await release_s02.wait()        # barrier：s02 正在开始
            return {"plan_id": f"pp_{sid}", "scene_id": sid, "status": "approved"}

        async def starter(manifest):
            starter_calls.append(manifest)
            return {"run_id": "prun_orphan"}

        task = _kickoff_with_fakes(monkeypatch, rt, eid, creator=creator, starter=starter)
        await s02_started.wait()                # s01 已 checkpoint、s02 单元已开始
        res = await cpx.cancel_execution(rt, "demo", eid)
        assert res["creative_committed"] is True
        ex_c = store.get_execution(eid)
        assert ex_c.cancel_requested is True
        assert ex_c.status == "running"         # stopping

        release_s02.set()                       # s02 单元自然结束
        await task
        for _ in range(50):
            if store.get_execution(eid).status == "cancelled":
                break
            await asyncio.sleep(0.05)
        ex_f = store.get_execution(eid)
        assert ex_f.status == "cancelled"       # Gate 2/4：最终 cancelled
        assert starter_calls == []              # 不进入 run starter
        assert ex_f.production_run_id is None   # 不创建 ProductionRun
        assert sorted(creator_calls) == ["s01", "s02"]   # 无第三个 Scene
        # checkpoint：已开始单元可记录（record 语义），但 stage 未推进
        assert ex_f.stage == "production_planning"
        _cleanup_watchers(eid)

    asyncio.run(main())


# ============ Case 4a — cancel 先于 run-start（§七 情况 A：不创建 run）============
def test_case4a_cancel_before_run_start_no_run(tmp_path, monkeypatch):
    """最终 cancel 检查先于 start_run：planner 已跑完（s01 已 checkpoint）后取消发生在
    run-start 临界区前 -> 情况 A：不创建 ProductionRun。"""

    async def main():
        rt = await _seed(tmp_path, _proj_1c1s())
        ex0 = _exec(rt)
        eid = ex0.execution_id
        await _sync_all_chars(rt, eid)          # -> production_planning（1 scene）
        store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")

        entered = asyncio.Event()
        release = asyncio.Event()
        starter_calls = []

        async def creator(sid):
            entered.set()
            await release.wait()                # barrier：s01 creator 在飞时取消
            return {"plan_id": f"pp_{sid}", "scene_id": sid, "status": "approved"}

        async def starter(manifest):
            starter_calls.append(manifest)
            return {"run_id": "prun_a"}

        task = _kickoff_with_fakes(monkeypatch, rt, eid, creator=creator, starter=starter)
        await entered.wait()
        await cpx.cancel_execution(rt, "demo", eid)   # 取消落在 run-start 前
        release.set()
        await task
        for _ in range(50):
            if store.get_execution(eid).status == "cancelled":
                break
            await asyncio.sleep(0.05)
        ex_f = store.get_execution(eid)
        assert ex_f.status == "cancelled"
        assert starter_calls == []              # §七 情况 A：run 根本不创建
        assert ex_f.production_run_id is None
        _cleanup_watchers(eid)

    asyncio.run(main())


# ============ Case 4b — run starter 先取得锁（§七 情况 B：run_id 关联后可被精确取消）============
def test_case4b_run_start_then_cancel_propagates(tmp_path, monkeypatch, fake_autopilot):
    """start_run 在 §七 临界区内（最终检查通过后）执行，cancel 在锁上排队 ->
    run_id 关联落盘后 cancel 精确找到 R1 并传播取消；无孤立 run。"""

    async def main():
        mgr, install = fake_autopilot
        run = _FakeRun("prun_b1", status="running")

        rt = await _seed(tmp_path, _proj_1c1s())
        ex0 = _exec(rt)
        eid = ex0.execution_id
        await _sync_all_chars(rt, eid)          # -> production_planning
        store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")

        # run 在 seed 后才出现（seed 前安装会让 §20 active-production preflight 阻断提交）
        install([run])

        starter_entered = asyncio.Event()
        finish_start = asyncio.Event()

        async def creator(sid):
            return {"plan_id": f"pp_{sid}", "scene_id": sid, "status": "approved"}

        async def starter(manifest):
            starter_entered.set()               # 已在 §七 临界区内（持有 project_write_lock）
            await finish_start.wait()
            return {"run_id": "prun_b1"}

        task = _kickoff_with_fakes(monkeypatch, rt, eid, creator=creator, starter=starter)
        await starter_entered.wait()            # run 创建中（锁被 planning 持有）

        cancel_task = asyncio.create_task(cpx.cancel_execution(rt, "demo", eid))
        await asyncio.sleep(0.05)               # cancel 在 project_write_lock 上排队
        finish_start.set()                      # run_id 关联落盘 -> 释放锁
        await cancel_task

        ex_c = store.get_execution(eid)
        # §七 情况 B：R1 已创建且被精确关联
        assert ex_c.production_run_id == "prun_b1"
        assert ex_c.cancel_requested is True
        assert ex_c.status == "running"         # stopping（run 仍活跃，§八不提前 cancelled）
        # 取消精确传播到 R1（不猜「任意 active run」）
        assert mgr.cancel_calls == [("demo", "prun_b1")]
        assert run.cancel_requested is True
        await task                              # planning 正常收尾（production_prepare）

        # run 停止后收敛 cancelled
        run.status = "cancelled"
        await cpx.maybe_converge_cancellation(rt, "demo", eid)
        ex_f = store.get_execution(eid)
        assert ex_f.status == "cancelled"       # Gate 3：传播成立
        assert cpx._get_owned_run("demo", "prun_b1") is run   # 精确关联可查（无孤立 R1）
        _cleanup_watchers(eid)

    asyncio.run(main())


# ============ Case 5 — ProductionRun 已 active 时 restart（§十三 Case 5 / Gate 4）============
def test_case5_active_run_cancel_then_new_session_unblocked(tmp_path, fake_autopilot):
    """E1 -> R1 active：cancel E1 -> 精确 cancel R1 -> 等 R1 安全停止 -> E1 cancelled ->
    _active_production 不再报活跃（新 S2 不会撞 PROJECT_PRODUCTION_ACTIVE）。"""

    async def main():
        mgr, install = fake_autopilot
        run = _FakeRun("prun_c1", status="running")

        rt = await _seed(tmp_path, _proj_1c1s())
        ex0 = _exec(rt)
        eid = ex0.execution_id
        await _sync_all_chars(rt, eid)
        store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
        # 手动推进到 production_prepare + 关联 R1（模拟 planning 已完成、run 活跃）。
        # run 在 seed 后才出现（seed 前安装会让 §20 preflight 阻断提交）
        install([run])
        ex1 = store.get_execution(eid)
        ex1.stage = "production_prepare"
        ex1.production_run_id = "prun_c1"
        store.save_execution(ex1)

        res = await cpx.cancel_execution(rt, "demo", eid)
        assert res["creative_committed"] is True
        ex_c = store.get_execution(eid)
        # §八：run 仍活跃时不提前宣称 cancelled
        assert ex_c.status == "running" and ex_c.cancel_requested is True
        assert mgr.cancel_calls == [("demo", "prun_c1")]

        # restart 等待期间 R1 安全停止（cooperative 收尾后 cancelled）
        run.status = "cancelled"
        await cpx.maybe_converge_cancellation(rt, "demo", eid)
        ex_f = store.get_execution(eid)
        assert ex_f.status == "cancelled"       # 旧执行安全停止

        # Gate 4：新计划不被已放弃的 R1 阻塞（_active_production 兜底检查通过）
        assert cpx._active_production("demo") is None
        _cleanup_watchers(eid)

    asyncio.run(main())


# ============ Case 6 — stopping 中 backend restart（§十三 Case 6 / §九）============
def test_case6a_restart_during_stopping_no_run(tmp_path):
    """cancel_requested=true + backend restart（无 owned run）：恢复进入完成取消路径——
    不恢复旧生产链续跑；收敛 cancelled。"""

    async def main():
        rt = await _seed(tmp_path, _proj_2c2s(), second_char_op=True)
        ex0 = _exec(rt)
        eid = ex0.execution_id
        await _sync_all_chars(rt, eid)          # -> production_planning（s01 尚未 checkpoint）
        store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
        # 模拟：s01 已 checkpoint、s02 未跑；用户已取消、进程崩溃
        ex1 = store.get_execution(eid)
        ex1.planning = [{"scene_id": "s01", "plan_id": "pp_s01", "status": "approved"}]
        ex1.cancel_requested = True             # stopping 中崩溃
        store.save_execution(ex1)

        cpx._INFLIGHT_POSTCOMMIT.clear()        # 新进程：无在飞任务
        n = cpx.recover_executions(rt._resolve_project_dir("demo"), "demo", rt=rt)
        assert n == 1
        ex_f = store.get_execution(eid)
        assert ex_f.status == "cancelled"       # 收敛 cancelled，不复活
        assert ex_f.stage == "production_planning"   # 未推进（s02 不再规划）
        assert len(ex_f.planning) == 1
        assert len(cpx._INFLIGHT_POSTCOMMIT) == 0    # 不 kickoff 旧生产链
        _cleanup_watchers(eid)

    asyncio.run(main())


def test_case6b_restart_during_stopping_active_run(tmp_path, fake_autopilot):
    """cancel_requested=true + owned run 仍 active + backend restart：
    继续请求/恢复 R1 cancellation；run 停止后收敛 cancelled；不恢复旧生产链。"""

    async def main():
        mgr, install = fake_autopilot
        run = _FakeRun("prun_d1", status="running")

        rt = await _seed(tmp_path, _proj_1c1s())
        ex0 = _exec(rt)
        eid = ex0.execution_id
        await _sync_all_chars(rt, eid)
        store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
        # run 在 seed 后才出现（seed 前安装会让 §20 preflight 阻断提交）
        install([run])
        ex1 = store.get_execution(eid)
        ex1.stage = "production_prepare"
        ex1.production_run_id = "prun_d1"
        ex1.cancel_requested = True             # stopping 中崩溃（run 未停）
        store.save_execution(ex1)

        cpx._INFLIGHT_POSTCOMMIT.clear()
        n = cpx.recover_executions(rt._resolve_project_dir("demo"), "demo", rt=rt)
        assert n == 1
        await asyncio.sleep(0.05)               # 让 propagate task 跑完
        assert ("demo", "prun_d1") in mgr.cancel_calls   # §九：继续请求取消 R1
        ex_mid = store.get_execution(eid)
        assert ex_mid.status == "running" and ex_mid.cancel_requested is True  # stopping
        assert len(cpx._INFLIGHT_POSTCOMMIT) == 0        # 不 kickoff 旧生产链

        run.status = "cancelled"                # R1 安全停止
        await cpx.maybe_converge_cancellation(rt, "demo", eid)
        assert store.get_execution(eid).status == "cancelled"
        _cleanup_watchers(eid)

    asyncio.run(main())


# ============ 附：resume 尊重 cancel_requested（§六 + Gate 2）============
def test_resume_cancelling_returns_no_new_work(tmp_path):
    """正在停止的 execution 不可 resume 推进新工作；resume 驱动收敛。"""

    async def main():
        rt = await _seed(tmp_path, _proj_1c1s())
        ex0 = _exec(rt)
        eid = ex0.execution_id
        store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
        # 模拟 stopping（running + cancel_requested，无在飞/无 run）
        ex0.cancel_requested = True
        store.save_execution(ex0)

        res = await cpx.resume_execution(rt, "demo", eid)
        assert res.get("cancelling") is True
        assert len(cpx._INFLIGHT_POSTCOMMIT) == 0
        ex_f = store.get_execution(eid)
        assert ex_f.status == "cancelled"       # resume 路径收敛完成取消
        _cleanup_watchers(eid)

    asyncio.run(main())


# ============ AI-7C-H Final Closure — paused owned run 取消收敛（真实 Manager 路径）============
# 文档：详细流程文件/AI-7C-H_PAUSED_CANCEL_CLOSURE_TRAE.md
# 与上面 fake_autopilot 系列不同：这里用**真实** ProjectProductionManager + 真实磁盘
# 持久化（deps.project_manager -> tmp_path），因为本闭环修复的正是真实
# request_cancel / recover_runs 的 paused 分支。

def _persist_prun(mgr, run_id, status, *, cancel_requested=False, parent_exec=None,
                  stage="scenes"):
    run = ProjectProductionRun(
        run_id=run_id, project_name="demo", status=status, stage=stage,
        cancel_requested=cancel_requested,
        parent_change_execution_id=parent_exec,
        created_at=1.0, updated_at=1.0)
    mgr._write_run(run)
    return run


@pytest.fixture
def real_autopilot(tmp_path, monkeypatch):
    """真实 ProjectProductionManager + 真实 ProjectManager(tmp_path) 持久化路径。
    重置模块单例，避免其他测试留下的状态。"""
    pm = ProjectManager(str(tmp_path))
    (tmp_path / "demo").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(deps_mod, "project_manager", pm)
    mgr = pap.ProjectProductionManager()
    monkeypatch.setattr(pap, "_manager", mgr)
    return mgr


def test_paused_owned_run_cancels_normally(tmp_path, real_autopilot):
    """Closure Case 1：paused owned R1 + 用户取消 E1 -> 真实 request_cancel 将 R1
    直接转终态 cancelled（无 loop 可消费请求）-> E1 经收敛链 cancelled；
    无新 run / 无新 loop / 无资产删除路径；替代会话不再被 R1 阻塞。"""

    async def main():
        mgr = real_autopilot
        rt = await _seed(tmp_path, _proj_1c1s())
        ex0 = _exec(rt)
        eid = ex0.execution_id
        store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
        # E1 已到 production_prepare 并精确拥有 paused R1
        ex1 = store.get_execution(eid)
        ex1.stage = "production_prepare"
        ex1.production_run_id = "prun_p1"
        store.save_execution(ex1)
        _persist_prun(mgr, "prun_p1", "paused", parent_exec=eid)
        assert len(mgr.list_runs("demo")) == 1

        # 用户「停止当前任务并重新规划」-> 真实 cancel 链
        res = await cpx.cancel_execution(rt, "demo", eid)
        assert res["creative_committed"] is True
        # R1：paused -> cancelled 直接终态（真实 request_cancel paused 分支）
        r1 = mgr.get_run("demo", "prun_p1")
        assert r1.status == "cancelled"
        assert r1.cancel_requested is False
        # E1：不提前宣称 cancelled（§八），由读取惰性收敛（GET 轮询驱动）到终态
        ex_c = store.get_execution(eid)
        assert ex_c.cancel_requested is True
        assert ex_c.status == "running"
        await cpx.maybe_converge_cancellation(rt, "demo", eid)
        ex_f = store.get_execution(eid)
        assert ex_f.status == "cancelled"
        # 无新 run / 无活跃 loop（不建新生产、不 resume）
        assert len(mgr.list_runs("demo")) == 1
        assert mgr._run_tasks == {}
        # Gate 4：替代会话不被已放弃的 R1 阻塞
        assert cpx._active_production("demo") is None
        # cancelled 终态不被 resume 复活（§四 invariant 2）
        with pytest.raises(ValueError):
            await mgr.resume_run("demo", "prun_p1")
        _cleanup_watchers(eid)

    asyncio.run(main())


def test_paused_cancel_restart_reconciliation(tmp_path, real_autopilot):
    """Closure Case 2：持久化 R1 paused+cancel_requested + E1 running+cancel_requested
    （崩溃窗口/旧数据组合态）-> 真实 recover_runs 对账 R1 -> cancelled ->
    recover_executions 收敛 E1 -> cancelled；0 新 run、0 kickoff、不 resume。"""

    async def main():
        mgr = real_autopilot
        rt = await _seed(tmp_path, _proj_1c1s())
        ex0 = _exec(rt)
        eid = ex0.execution_id
        store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
        # 模拟崩溃窗口持久化态：E1 stopping + owned R1 paused+cancel_requested
        ex1 = store.get_execution(eid)
        ex1.stage = "production_prepare"
        ex1.production_run_id = "prun_p2"
        ex1.cancel_requested = True
        store.save_execution(ex1)
        _persist_prun(mgr, "prun_p2", "paused", cancel_requested=True, parent_exec=eid)

        # 模拟 backend restart：新进程无在飞任务；真实恢复链
        # （recover_runs 对账 run 在前、recover_executions 收敛 execution 在后，
        #  与 api_server startup 两个幂等驱动一致）
        cpx._INFLIGHT_POSTCOMMIT.clear()
        await mgr.recover_runs()
        r1 = mgr.get_run("demo", "prun_p2")
        assert r1.status == "cancelled"            # paused+cancel_requested -> cancelled
        assert r1.cancel_requested is False
        n = cpx.recover_executions(rt._resolve_project_dir("demo"), "demo", rt=rt)
        assert n == 1
        ex_f = store.get_execution(eid)
        assert ex_f.status == "cancelled"          # E1 收敛 cancelled
        assert ex_f.stage == "production_prepare"  # 未推进（不复活旧计划）
        # 0 新 run / 0 新 loop / 0 kickoff
        assert len(mgr.list_runs("demo")) == 1
        assert mgr._run_tasks == {}
        assert len(cpx._INFLIGHT_POSTCOMMIT) == 0
        _cleanup_watchers(eid)

    asyncio.run(main())


def test_paused_cancel_idempotent_exact_ownership(tmp_path, real_autopilot):
    """Closure Case 3 + 精确 ownership：paused R1（E1 拥有）+ paused R2（同项目无关 run）。
    cancel E1 -> 只 R1 -> cancelled，R2 原样不动；重复 cancel 幂等；restart 对账后
    R1 仍 cancelled、R2 仍 paused（不误取消、不自动 resume）；0 新 run/loop。"""

    async def main():
        mgr = real_autopilot
        rt = await _seed(tmp_path, _proj_1c1s())
        ex0 = _exec(rt)
        eid = ex0.execution_id
        store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
        ex1 = store.get_execution(eid)
        ex1.stage = "production_prepare"
        ex1.production_run_id = "prun_p3"
        store.save_execution(ex1)
        _persist_prun(mgr, "prun_p3", "paused", parent_exec=eid)
        _persist_prun(mgr, "prun_r2", "paused")    # 无关 run（不被 E1 拥有）

        # 第一次取消：只 owned R1 被取消
        await cpx.cancel_execution(rt, "demo", eid)
        assert mgr.get_run("demo", "prun_p3").status == "cancelled"
        r2 = mgr.get_run("demo", "prun_r2")
        assert r2.status == "paused" and r2.cancel_requested is False   # 精确 ownership
        await cpx.maybe_converge_cancellation(rt, "demo", eid)
        assert store.get_execution(eid).status == "cancelled"

        # 重复 cancel：幂等（terminal 直接返回，无新副作用）
        res2 = await cpx.cancel_execution(rt, "demo", eid)
        assert res2["idempotent"] is True
        assert mgr.get_run("demo", "prun_p3").status == "cancelled"

        # restart 对账：R1 保持 cancelled，R2 保持 paused（不误取消/不自动 resume）
        cpx._INFLIGHT_POSTCOMMIT.clear()
        await mgr.recover_runs()
        assert mgr.get_run("demo", "prun_p3").status == "cancelled"
        assert mgr.get_run("demo", "prun_r2").status == "paused"
        cpx.recover_executions(rt._resolve_project_dir("demo"), "demo", rt=rt)
        assert store.get_execution(eid).status == "cancelled"
        assert len(mgr.list_runs("demo")) == 2
        assert mgr._run_tasks == {}
        _cleanup_watchers(eid)

    asyncio.run(main())
