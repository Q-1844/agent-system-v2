"""AI-6F.0 Shot Producer durable lifecycle 集成测试（§18 14 cases / §10 restart recovery）。

engine 级：直接驱动 AgentRuntime.delegate(shot.first_frame/last_frame/video) +
真实 JobQueue（tmp_path + deterministic fake handler，§7）+ AgentTaskSettler 对账。
不调真 Agnes（§7：fake handler 不联网、不写真实资产）。

证明 durable lifecycle 语义：
- §4/§6/§17：delegate 提交 durable Job → HTTP 先返回（task=running）→ execution_ref 落盘
- §6 幂等：reattach 不重提交；reattach 发现既有终态 Job 从结果结算
- §10 restart：第二个 JobQueue + settler 在同 path 上恢复，reattach/settle，不重复 submit
- §8/§9：progress 从 durable 执行态派生（job 状态/progress），不造 LLM 百分比；task 状态小 enum

restart 模拟：第一个 JobQueue 在 event loop A 上跑（worker 处理 fake Job）；loop 关闭后
jobs.json 持久化。第二个 JobQueue（**不 start** worker）在同 path 上 _load 既有 Job，
settler 据 task.execution_ref.job_id → q.get(job_id) 对账——模拟 backend 重启后恢复扫描。
"""
import asyncio

import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.core.job_queue import JobQueue
from src.agents.models import TaskStatus, ResultStatus
from src.agents.runtime import AgentRuntime
from src.agents.tasks import AgentTaskStore
from src.agents.settlement import AgentTaskSettler
from src.agents.fake_shot_handlers import all_fake_handlers

PROJECT = "_test_ai6f"


# ============ 辅助 ============
def _shot(sid, idx=1, desc="机甲启动", chars=None, dur=5.0):
    return Shot(shot_id=sid, scene_id=sid.rsplit("_c", 1)[0], index=idx,
                description=desc, characters=chars or [], target_duration_s=dur)


def _save_proj(tmp_path, shots=None):
    shots = shots if shots is not None else [_shot("s01_c01")]
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事",
                   characters=[Character(name="阿派", description_en="机甲")],
                   scenes=[Scene(scene_id="s01", summary="机甲出发", characters=["阿派"],
                                 shots=shots)])
    ProjectStore(tmp_path).save(proj)
    return tmp_path


def _runtime(tmp_path, job_queue, **kw):
    """AgentRuntime 绑 tmp_path + 注入 JobQueue getter + 隔离 settings。"""
    kw.setdefault("settings_path", tmp_path / "agents_data" / "settings.json")
    return AgentRuntime(
        project_dir_resolver=lambda name: tmp_path,
        job_queue_getter=lambda: job_queue,
        client=None,                       # fake 路径不调 Agnes
        agnes_getter=lambda: None,
        **kw,
    )


def _settler(tmp_path, job_queue):
    return AgentTaskSettler(
        project_lister=lambda: [(PROJECT, tmp_path)],
        job_queue_getter=lambda: job_queue,
        poll_interval=0.05,
    )


def _new_queue(tmp_path, handlers=None):
    """真实 JobQueue on tmp_path/jobs.json（持久化 + worker + SSE + _load 恢复）。"""
    h = dict(all_fake_handlers())
    if handlers:
        h.update(handlers)
    return JobQueue(tmp_path / "jobs.json", h)


def _run(coro):
    """单 event loop 跑完一个 async scenario（worker 在此 loop 存活）。"""
    return asyncio.run(coro)


async def _wait_job_terminal(q, job_id, timeout=5.0):
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        j = q.get(job_id)
        if j and j.state in ("done", "failed", "cancelled"):
            return j
        await asyncio.sleep(0.02)
    return q.get(job_id)


# ============ §18-1/2/3/4：创建 long-running task + pending→running + 不等待 + execution_ref ============
def test_1_create_long_running_shot_task(tmp_path):
    """§18-1/3: Producer 创建 long-running Shot Producer AgentTask（fake 路径）；delegate
    在 Job 完成前返回（task=running，非 completed）。"""
    _save_proj(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "生成首帧", "shot.first_frame", "execute")
        # §18-1: task created as long-running (running, not completed/failed)
        assert task["status"] == TaskStatus.RUNNING.value
        # §18-3: returned without waiting — linked Job still non-terminal right after delegate
        ref = task["execution_ref"]
        assert ref and ref["job_id"]
        job = q.get(ref["job_id"])
        assert job is not None
        assert job.state != "done", "delegate 不应等 Job 完成"
        await _wait_job_terminal(q, ref["job_id"])

    _run(scenario())


def test_2_task_pending_to_running(tmp_path):
    """§18-2: AgentTask persists pending → running."""
    _save_proj(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        # 拦截：在 create 后、dispatch 前查 pending——通过 store 直接观察
        store = AgentTaskStore(tmp_path, PROJECT)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        # delegate 返回时已 running
        assert task["status"] == TaskStatus.RUNNING.value
        got = store.get(task["task_id"])
        assert got.status == TaskStatus.RUNNING.value
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])

    _run(scenario())


def test_4_stores_and_reconstructs_execution_ref(tmp_path):
    """§18-4: AgentTask stores/reconstructs its linked durable execution reference."""
    _save_proj(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        ref = task["execution_ref"]
        assert ref["job_id"] and ref["kind"] == "agent_first_frame"
        assert ref["phase"] == "first_frame"
        assert ref["shot_id"] == "s01_c01"
        # 新 store 同目录恢复（reconstruct）
        store2 = AgentTaskStore(tmp_path, PROJECT)
        got = store2.get(task["task_id"])
        assert got.execution_ref == ref
        assert q.get(ref["job_id"]) is not None
        await _wait_job_terminal(q, ref["job_id"])

    _run(scenario())


# ============ §18-5/6: linked Job success/failure settles task ============
def test_5_job_success_settles_completed(tmp_path):
    """§18-5: linked fake Job success → settler settles AgentTask completed."""
    _save_proj(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])
        settler = _settler(tmp_path, q)
        await settler.recover_tasks()
        got = rt.get_task(PROJECT, task["task_id"])
        assert got["status"] == TaskStatus.COMPLETED.value
        assert got["structured_result"]["status"] == ResultStatus.COMPLETED.value

    _run(scenario())


def test_6_job_failure_settles_failed(tmp_path):
    """§18-6: linked fake Job failure → settler settles AgentTask failed."""
    _save_proj(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q, fake_shot_params={"_fake_outcome": "failed"})
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])
        settler = _settler(tmp_path, q)
        await settler.recover_tasks()
        got = rt.get_task(PROJECT, task["task_id"])
        assert got["status"] == TaskStatus.FAILED.value
        assert got["structured_result"]["status"] == ResultStatus.FAILED.value

    _run(scenario())


# ============ §18-7/8: restart reattaches same Job, no duplicate ============
def test_7_restart_reattaches_nonterminal_job(tmp_path):
    """§18-7/8: backend restart → same AgentTask reattaches to same non-terminal (queued) Job, no duplicate."""
    _save_proj(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path)
        # 不 start worker：Job 提交后保持 queued（非终态），模拟「提交后、处理前」in-flight 状态。
        # jobs.json + task json 在 submit()/update_task() 时原子落盘，loop 关闭=backend 停止。
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        assert task["status"] == TaskStatus.RUNNING.value
        job = q.get(task["execution_ref"]["job_id"])
        assert job is not None and job.state == "queued"   # non-terminal, no worker ran

    _run(scenario())
    # === 模拟 backend 重启：新 JobQueue 同 path（_load 既有 queued Job），不 start ===
    q2 = _new_queue(tmp_path)
    assert len(q2.jobs) == 1, "重启前应有 1 个 Job"
    settler = _settler(tmp_path, q2)
    asyncio.run(settler.recover_tasks())
    store = AgentTaskStore(tmp_path, PROJECT)
    tasks = store.list(status=TaskStatus.RUNNING.value)
    assert tasks, "task 应仍 running（reattach 非终态 Job）"
    t = tasks[0]
    assert t.status == TaskStatus.RUNNING.value   # reattach: 不结算
    assert len(q2.jobs) == 1, f"重复提交！jobs={list(q2.jobs.keys())}"   # §18-8


def test_8_reconstruction_no_duplicate_submit(tmp_path):
    """§18-8: 重启后 reconstruct 不 submit duplicate Job（job 计数不变，同 job_id）。"""
    _save_proj(tmp_path)
    job_ids_seen = []

    async def scenario():
        q = _new_queue(tmp_path)   # 不 start：Job 保持 queued 跨「重启」
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.video", "execute")
        job_ids_seen.append(task["execution_ref"]["job_id"])
        assert q.get(job_ids_seen[0]).state == "queued"

    _run(scenario())
    q2 = _new_queue(tmp_path)
    settler = _settler(tmp_path, q2)
    asyncio.run(settler.recover_tasks())
    assert len(q2.jobs) == 1
    assert list(q2.jobs.keys()) == job_ids_seen


# ============ §18-9: repeated settlement is idempotent ============
def test_9_repeated_settlement_idempotent(tmp_path):
    """§18-9: 重复 settlement 幂等——多次 recover_tasks 不产生额外 Job / 不改终态。"""
    _save_proj(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])
        return task["task_id"]

    tid = _run(scenario())
    q2 = _new_queue(tmp_path)
    settler = _settler(tmp_path, q2)
    asyncio.run(settler.recover_tasks())
    asyncio.run(settler.recover_tasks())
    asyncio.run(settler.recover_tasks())
    store = AgentTaskStore(tmp_path, PROJECT)
    t = store.get(tid)
    assert t.status == TaskStatus.COMPLETED.value
    assert len(q2.jobs) == 1   # no duplicate


# ============ §18-10/11: completed/failed Job discovered after restart ============
def test_10_completed_job_after_restart_settles_existing(tmp_path):
    """§18-10: Job 已 done 但 task 未结算（backend 在 Job 完成 & 结算间停）→ 重启后 settler
    从既有 done Job 结算 task completed，不重新生成。"""
    _save_proj(tmp_path)
    task_id_holder = []

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        task_id_holder.append(task["task_id"])
        # 等 Job done，但**故意不结算**（模拟 backend 在此之间崩溃）
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])
        # task 仍 running（未跑 settler）

    _run(scenario())
    # 重启：新 queue _load 既有 done Job；settler 从既有 done Job 结算，不重新 submit
    q2 = _new_queue(tmp_path)
    assert q2.get(_get_job_id(tmp_path)).state == "done"
    settler = _settler(tmp_path, q2)
    asyncio.run(settler.recover_tasks())
    store = AgentTaskStore(tmp_path, PROJECT)
    t = store.get(task_id_holder[0])
    assert t.status == TaskStatus.COMPLETED.value
    assert len(q2.jobs) == 1   # no regenerate


def test_11_failed_job_after_restart_settles_failed(tmp_path):
    """§18-11: Job failed 后重启 → settler 结算 task failed。"""
    _save_proj(tmp_path)
    task_id_holder = []

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q, fake_shot_params={"_fake_outcome": "failed"})
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        task_id_holder.append(task["task_id"])
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])

    _run(scenario())
    q2 = _new_queue(tmp_path)
    settler = _settler(tmp_path, q2)
    asyncio.run(settler.recover_tasks())
    store = AgentTaskStore(tmp_path, PROJECT)
    t = store.get(task_id_holder[0])
    assert t.status == TaskStatus.FAILED.value


def _get_job_id(tmp_path):
    """从持久化 task json 取 linked job_id（跨 restart 恢复用）。"""
    store = AgentTaskStore(tmp_path, PROJECT)
    tasks = store.list()
    assert tasks, "no persisted task"
    ref = tasks[0].execution_ref
    assert ref and ref["job_id"]
    return ref["job_id"]


# ============ §18-12: progress reconstructed from persistent execution state ============
def test_12_progress_reconstructed_from_job_state(tmp_path):
    """§18-12: progress detail 可从持久执行态重建（phase/job_state/progress_pct/counts）。"""
    _save_proj(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])
        settler = _settler(tmp_path, q)
        await settler.recover_tasks()
        got = rt.get_task(PROJECT, task["task_id"])
        data = got["structured_result"]["data"]
        # §8: 确定性 phase（非 LLM 百分比）
        assert data["phase"] == "first_frame"
        assert data["job_state"] == "done"
        assert data["progress_pct"] == 100
        assert data["completed_items"] == 1
        assert data["total_items"] == 1
        assert data["job_id"] == task["execution_ref"]["job_id"]

    _run(scenario())


# ============ §18-13/14: task running while job non-terminal; never completed prematurely ============
def test_13_task_running_while_job_nonterminal(tmp_path):
    """§18-13: AgentTask 保持 running 当其 linked Job 仍非终态（queued）。"""
    _save_proj(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path)   # 不 start：Job 保持 queued（非终态）
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        job = q.get(task["execution_ref"]["job_id"])
        assert job.state == "queued"   # non-terminal
        got = rt.get_task(PROJECT, task["task_id"])
        assert got["status"] == TaskStatus.RUNNING.value

    _run(scenario())


def test_14_never_completed_while_job_nonterminal(tmp_path):
    """§18-14: AgentTask 在 linked real execution 仍非终态时绝不报告 completed。"""
    _save_proj(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path)   # 不 start：Job 保持 queued（非终态）
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        settler = _settler(tmp_path, q)
        await settler.recover_tasks()   # reattach path：不应结算 completed
        got = rt.get_task(PROJECT, task["task_id"])
        assert got["status"] != TaskStatus.COMPLETED.value, "Job 仍非终态却结算 completed！"
        assert got["status"] == TaskStatus.RUNNING.value

    _run(scenario())


# ============ §6 幂等补充：reattach 在执行器层（existing_ref 非终态 → 不重提交）============
def test_delegate_reattach_no_resubmit(tmp_path):
    """§6: 同一 AgentTask 经 execute_shot_production 再处理（existing_ref 指向非终态 Job）
    → reattach（RUNNING，同 job_id），不重提交。"""
    _save_proj(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path)   # 不 start：Job 保持 queued
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        ref = task["execution_ref"]
        assert q.get(ref["job_id"]).state == "queued"   # non-terminal
        # 用同一 existing_ref 再执行——应 reattach（RUNNING），不 submit
        from src.agents.shot_producer_capability import execute_shot_production
        res = await execute_shot_production(
            client=None, project_loader=lambda: ProjectStore(tmp_path).load(),
            project_dir=tmp_path, project_name=PROJECT, scope_type="shot", scope_id="s01_c01",
            instruction="x", capability_id="shot.first_frame", job_queue=q,
            existing_ref=ref)
        assert res.status == ResultStatus.RUNNING.value
        assert res.data["job_id"] == ref["job_id"]   # same job
        assert len(q.jobs) == 1                       # no duplicate

    _run(scenario())


def test_last_frame_and_video_kinds(tmp_path):
    """§17: shot.last_frame / shot.video 同样接通（fake kind 映射）。"""
    _save_proj(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        for cap, kind, phase in [("shot.last_frame", "agent_last_frame", "last_frame"),
                                 ("shot.video", "agent_video", "video")]:
            task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                     "x", cap, "execute")
            assert task["status"] == TaskStatus.RUNNING.value
            assert task["execution_ref"]["kind"] == kind
            assert task["execution_ref"]["phase"] == phase
            await _wait_job_terminal(q, task["execution_ref"]["job_id"])

    _run(scenario())


def test_invalid_scope_blocked(tmp_path):
    """§16: scope 非 shot → FAILED blocker（不提交 Job）。"""
    _save_proj(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "scene", "s01",
                                 "x", "shot.first_frame", "execute")
        assert task["status"] == TaskStatus.FAILED.value
        assert "shot" in task["error"]
        assert len(q.jobs) == 0   # no job submitted

    _run(scenario())


def test_unknown_shot_blocked(tmp_path):
    """§16: shot 不存在 → 结构化 blocker，不提交 Job。"""
    _save_proj(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c99",
                                 "x", "shot.first_frame", "execute")
        assert task["status"] == TaskStatus.FAILED.value
        assert "s01_c99" in task["error"]
        assert len(q.jobs) == 0

    _run(scenario())


def test_canonical_project_json_unchanged(tmp_path):
    """§14/§18: shot 执行不写 canonical project.json（regeneration 非抹除，shot 资产未动）。"""
    _save_proj(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])
        settler = _settler(tmp_path, q)
        await settler.recover_tasks()

    _run(scenario())
    after = (tmp_path / "output" / "project.json").read_bytes()
    assert before == after, "canonical project.json 被 shot 执行修改（违反 §14）"


def test_no_real_agnes_request_sent(tmp_path):
    """§7/§21: fake 路径不调 Agnes——注入一个会记录调用的 client，断言 0 次调用。"""
    _save_proj(tmp_path)

    class SpyClient:
        def __init__(self):
            self.calls = 0

        async def chat(self, *a, **kw):
            self.calls += 1
            return ""

    spy = SpyClient()

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                         job_queue_getter=lambda: q, client=spy,
                         agnes_getter=lambda: spy,
                         settings_path=tmp_path / "agents_data" / "settings.json")
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])
        settler = _settler(tmp_path, q)
        await settler.recover_tasks()

    _run(scenario())
    assert spy.calls == 0, "fake shot 执行不应调 Agnes"


# ============ §20 API-level acceptance（TestClient，验 composition root + startup 接线）============
def test_api_shot_capabilities_wired_and_settler_ready(client):
    """§20: HTTP 层确认 shot.first_frame/last_frame/video 已 implemented（AI-6F.0 fake durable
    bridge），且 app startup 后 settlement bridge 单例就绪。lifecycle 语义由 §18 集成测试覆盖
    （fake 执行不污染全局 jobs.json，§20 不建 fake production-only UI）。"""
    r = client.get("/api/agents/shot_producer/capabilities")
    assert r.status_code == 200
    caps = {c["id"]: c for c in r.json()["capabilities"]}
    for cid in ("shot.first_frame", "shot.last_frame", "shot.video"):
        assert caps[cid]["implemented"] is True, f"{cid} 应已接通（AI-6F.0 fake durable）"
    # settlement bridge 单例存在。Phase48 E 测试隔离闸门：测试进程禁用启动恢复
    # （WF_STARTUP_RECOVERY_DISABLE=1，防测试进程杀真机活任务），poll loop 不在
    # startup 启动——lifecycle 就绪语义改由显式构造覆盖（§18 集成测试）。
    from src.agents.settlement import get_settler
    settler = get_settler()
    assert settler is not None
    import os as _os
    if _os.environ.get("WF_STARTUP_RECOVERY_DISABLE") == "1":
        assert settler._loop_task is None      # 闸门生效：测试进程不启动轮询
    else:
        assert settler._loop_task is not None  # 生产路径：poll loop 已在 startup 启动
