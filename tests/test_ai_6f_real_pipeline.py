"""AI-6F real Shot-production pipeline 集成测试（§15 Gate A / §18 Gate B / §25 Gate C / §43-46）。

engine 级：直接驱动 AgentRuntime.delegate(shot.first_frame/last_frame/video, real_shot_pipeline=True)
+ **真实** JOB_HANDLERS（routers/shots.py 的 _handler_first_frame/_handler_last_frame/_handler_video）
+ 真实 JobQueue（持久化 + worker）+ FakeAgnes/FakeRehost（不联网，§26 自动测试不调真 Agnes）。

证明 AI-6F 真实接通语义（§0/§50）：
- §17 final path：capability→真实 JOB_HANDLERS kind（first_frame/last_frame/video），runtime/bridge 不变
- §4/§16/§22：Plan preflight → 缺/未批准/过期/不相容/prereq 缺 → 结构化 blocker，不提交 Job、不调 Agnes（§43）
- §10/§24：settler 按 origin_run_id==task_id 精确解析 ImageBatch/Candidate/VideoVersion；Job done 但 artifact 缺 → 不 falsely complete
- §6/§8：同 AgentTask reattach 不重提交；新 AgentTask 产新版本，旧版本保留（§44）
- §20/§27：video restart-recovery fallback 仅在 exact video_id 匹配时 settle（不按 status/latest 猜）
- §25：create_video call count == 1 跨 simulated restart；同 task recovery 不产新版本
"""
import asyncio
import time as _t
from pathlib import Path

import pytest

import deps
from src.core.models import Project, Scene, Shot, Character, StyleAnchor, ShotStatus
from src.core.store import ProjectStore
from src.core.job_queue import JobQueue
from src.agents.models import TaskStatus, ResultStatus
from src.agents.runtime import AgentRuntime, reset_agent_runtime
from src.agents.tasks import AgentTaskStore
from src.agents.settlement import AgentTaskSettler

# 真实 handlers（routers/shots.py）；import 即把 first_frame/last_frame/video 注册进 deps.JOB_HANDLERS
from routers.shots import (_handler_first_frame, _handler_last_frame, _handler_video,
                            _handler_audio)
from routers.production_planner import _read_plans, _write_plans
from src.ai.production_planner import sanitize_plan

PROJECT = "_test_ai6f_real"


# ============ FakeAgnes / FakeRehost / FakePM ============
class _FakeQuota:
    def can(self, kind, amount=1):
        return (True, "")
    def consume(self, kind, amount=1):
        pass


class FakeAgnes:
    """覆盖 chat / generate_image / create_video / poll_video / download / quota。
    create_video 计数用于 §25 call-count 断言。poll_video 可控：done 立即返回 or pending。"""
    def __init__(self):
        self.calls = []
        self.quota = _FakeQuota()
        self._video_done = True            # poll_video 默认立即 done
        self._poll_error = False           # True → poll_video raise AgnesError（terminal remote failure）
        self._next_video_id = "vid_fake_1"

    async def chat(self, messages, *, temperature=0.7, max_tokens=2048,
                   thinking=False, json_mode=False):
        self.calls.append("chat")
        return "a cinematic frame, detailed, anime style, high quality"

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        self.calls.append("gen_image")
        return {"url": "https://fake.agnes/images/frame.png", "b64": None}

    async def create_video(self, prompt, *, mode="i2v", image_url=None, image_urls=None,
                           num_frames=121, frame_rate=24, width=1312, height=736,
                           seed=None, negative_prompt="",
                           model=None, duration_s=None):
        self.calls.append("create_video")
        # Phase55 C：provider 每次提交返回新 video_id（现实语义）——收货幂等按
        # video_id 去重，恒同 id 会让新任务被误判为重复收货。
        seq = int(self._next_video_id.rsplit("_", 1)[1]) + 1 \
            if self._next_video_id.startswith("vid_fake_") else 1
        self._next_video_id = f"vid_fake_{seq}"
        vid = self._next_video_id
        return {"video_id": vid, "task_id": "t_1", "seconds": "5.0", "size": "1280x704"}

    async def poll_video(self, video_id, *, interval=5.0, max_wait=3600,
                         on_progress=None, max_consecutive_failures=5,
                         model_name=None):
        self.calls.append("poll")
        if self._poll_error:
            from src.api.agnes_client import AgnesError
            raise AgnesError("fake remote terminal failure")
        if not self._video_done:
            # 模拟长轮询未完成：阻塞（worker 保持 running；scenario 结束时被 cancel）
            await asyncio.sleep(30)
        if on_progress:
            await on_progress(100, "completed", 0)
        return {"url": "https://fake.agnes/videos/fake.mp4",
                "seconds": 5.0, "size": "1280x704"}

    async def download(self, url, dest):
        self.calls.append("download")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"FAKE_ASSET")
        return str(dest)

    def create_video_count(self):
        return sum(1 for c in self.calls if c == "create_video")


class FakeRehost:
    async def upload(self, path):
        return "https://fake.imgur/rehost.png"


class _FakePM:
    """让真实 handler 的 get_project_manager().get_project_dir(name) 解析到 tmp_path。"""
    def __init__(self, pdir):
        self._pdir = pdir
        self.base_dir = pdir
    def get_project_dir(self, name):
        return self._pdir


# ============ 项目 fixture ============
def _shot(sid, idx=1, desc="机甲启动", chars=None, dur=5.0, link_mode="i2v"):
    s = Shot(shot_id=sid, scene_id=sid.rsplit("_c", 1)[0], index=idx,
             description=desc, characters=chars or [], target_duration_s=dur)
    s.link_mode = link_mode
    return s


def _save_proj(tmp_path, shots=None, chars=None):
    shots = shots if shots is not None else [_shot("s01_c01")]
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事",
                   characters=chars or [Character(name="阿派", description_en="机甲")],
                   scenes=[Scene(scene_id="s01", summary="机甲出发",
                                 characters=[c.name for c in (chars or [Character(name="阿派")])],
                                 shots=shots)])
    ProjectStore(tmp_path).save(proj)
    # Phase55 A：本文件测的是 AgentTask 生命周期（reattach/settle/幂等），非参考链
    # 契约——显式声明 legacy 参考策略（默认 bound_required 闸的覆盖在
    # test_p55_video_contract.py）。
    from src.generation.video_contract import set_project_reference_policy
    set_project_reference_policy(tmp_path, "legacy_allowed", source="test")
    return tmp_path


def _seed_approved_plan(tmp_path, scene_id, plan_shots):
    """写 approved Production Plan 到 tmp_path/output/ai/production_plans.json。
    plan_shots: [(shot_id, opening, link), ...]"""
    proj = ProjectStore(tmp_path).load()
    sc = next(s for s in proj.scenes if s.scene_id == scene_id)
    ai = {"overview": "x", "shots": [
        {"shot_id": sid, "opening_source": o, "link_mode": l, "reason": "r"}
        for sid, o, l in plan_shots]}
    plan = sanitize_plan(sc, ai, run_seed=12345, project_name=PROJECT)
    plan.status = "approved"
    plan.approved_at = _t.time()
    plans = _read_plans(tmp_path)
    plans[scene_id] = plan
    _write_plans(tmp_path, plans)
    return plan


@pytest.fixture
def real_deps(tmp_path):
    """swap deps 单例为 FakeAgnes/FakeRehost/_FakePM(tmp_path)，用完恢复。"""
    agnes = FakeAgnes()
    rehost = FakeRehost()
    pm = _FakePM(tmp_path)
    saved = (deps.agnes_client, deps._rehost_host, deps.project_manager)
    deps.agnes_client = agnes
    deps._rehost_host = rehost
    deps.project_manager = pm
    try:
        yield agnes
    finally:
        deps.agnes_client, deps._rehost_host, deps.project_manager = saved


def _real_handlers():
    return {"first_frame": _handler_first_frame, "last_frame": _handler_last_frame,
            "video": _handler_video, "audio": _handler_audio}


def _new_queue(tmp_path, handlers=None):
    h = _real_handlers()
    if handlers:
        h.update(handlers)
    return JobQueue(tmp_path / "jobs.json", h)


def _runtime(tmp_path, job_queue, **kw):
    kw.setdefault("settings_path", tmp_path / "agents_data" / "settings.json")
    return AgentRuntime(
        project_dir_resolver=lambda name: tmp_path,
        job_queue_getter=lambda: job_queue,
        agnes_getter=lambda: None,          # real 路径用 deps.agnes_client（handler 侧）；runtime client 仅用于 .quota
        client=None,
        real_shot_pipeline=True,
        **kw)


def _settler(tmp_path, job_queue):
    def _loader_for(project_name):
        # 真实 settler：return () -> Project；这里用 tmp_path
        if not (tmp_path / "output" / "project.json").exists():
            return None
        return lambda: ProjectStore(tmp_path).load()
    return AgentTaskSettler(
        project_lister=lambda: [(PROJECT, tmp_path)],
        job_queue_getter=lambda: job_queue,
        poll_interval=0.05,
        project_loader_for=_loader_for)


def _run(coro):
    return asyncio.run(coro)


async def _wait_job_terminal(q, job_id, timeout=10.0):
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        j = q.get(job_id)
        if j and j.state in ("done", "failed", "cancelled"):
            return j
        await asyncio.sleep(0.02)
    return q.get(job_id)


# ============================================================
# §15 Gate A — shot.first_frame real mapping
# ============================================================
def test_A1_first_frame_real_handler_through_agenttask(tmp_path, real_deps):
    """§15-1/2/3/4: Producer → AgentTask → real first_frame Job handler（FakeAgnes）；
    pending→running；delegate 在 Job 完成前返回；execution_ref 链真实 kind=first_frame。"""
    agnes = real_deps
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="i2v")])
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "i2v")])

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "生成首帧", "shot.first_frame", "execute")
        assert task["status"] == TaskStatus.RUNNING.value
        ref = task["execution_ref"]
        assert ref["kind"] == "first_frame"        # real kind，非 agent_first_frame
        assert ref["phase"] == "first_frame"
        assert ref["origin_task_id"] == task["task_id"]
        job = q.get(ref["job_id"])
        assert job is not None and job.kind == "first_frame"
        await _wait_job_terminal(q, ref["job_id"])

    _run(scenario())
    # FakeAgnes 被真实 handler 调用（§15-1 真实 first-frame Agnes 请求路径）
    assert any(c == "gen_image" for c in agnes.calls), "真实 first_frame handler 应调 generate_image"


def test_A5_success_persists_imagebatch_and_settles(tmp_path, real_deps):
    """§15-5/6: success 持久化 ImageBatch/Candidate；AgentTask 仅在 batch 持久化后 settle completed。"""
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="i2v")])
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "i2v")])

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        jid = task["execution_ref"]["job_id"]
        await _wait_job_terminal(q, jid)
        # batch 持久化于 project.json（origin_run_id==task_id）
        proj = ProjectStore(tmp_path).load()
        sh = proj.get_shot("s01_c01")
        own = [b for b in sh.first_frame_batches if b.origin_run_id == task["task_id"]]
        assert own, "应持久化带 origin_run_id==task_id 的 ImageBatch"
        assert own[-1].candidates, "batch 应有 candidate"
        settler = _settler(tmp_path, q)
        await settler.recover_tasks()
        got = rt.get_task(PROJECT, task["task_id"])
        assert got["status"] == TaskStatus.COMPLETED.value
        assert got["structured_result"]["status"] == ResultStatus.COMPLETED.value
        # §10：artifact_refs 含稳定 batch_id/candidate_id（非「最新一项」猜测）
        types = {r["type"] for r in got["artifact_refs"]}
        assert "batch" in types and "candidate" in types

    _run(scenario())


def test_A7_job_failure_settles_failed(tmp_path, real_deps):
    """§15-7: first_frame Job failure → AgentTask failed。"""
    agnes = real_deps

    async def _fail_gen(self, prompt, *, image_refs=None, size_tier="1K", ratio="16:9",
                        seed=None, want_b64=False):
        self.calls.append("gen_image")
        raise RuntimeError("fake image gen failure")
    agnes.generate_image = lambda *a, **k: _fail_gen(agnes, *a, **k)

    _save_proj(tmp_path, [_shot("s01_c01", link_mode="i2v")])
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "i2v")])

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])
        assert q.get(task["execution_ref"]["job_id"]).state == "failed"
        settler = _settler(tmp_path, q)
        await settler.recover_tasks()
        got = rt.get_task(PROJECT, task["task_id"])
        assert got["status"] == TaskStatus.FAILED.value

    _run(scenario())


def test_A8_A9_restart_reattaches_no_duplicate_batch(tmp_path, real_deps):
    """§15-8/9: backend restart → 同 AgentTask reattach 同一 Job，不重提交、不产新 batch。"""
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="i2v")])
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "i2v")])
    task_id_holder = []

    async def scenario():
        q = _new_queue(tmp_path)   # 不 start：Job 保持 queued（非终态），模拟「提交后、处理前」in-flight
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        task_id_holder.append((task["task_id"], task["execution_ref"]["job_id"]))
        assert q.get(task["execution_ref"]["job_id"]).state == "queued"

    _run(scenario())
    tid, jid = task_id_holder[0]
    # === 模拟 restart：新 queue _load 既有 queued Job（不 start worker），settler reattach ===
    q2 = _new_queue(tmp_path)
    assert len(q2.jobs) == 1, f"重启前应有 1 个 Job，实际 {len(q2.jobs)}"
    assert q2.get(jid).state == "queued"
    settler = _settler(tmp_path, q2)
    asyncio.run(settler.recover_tasks())
    store = AgentTaskStore(tmp_path, PROJECT)
    t = store.get(tid)
    assert t.status == TaskStatus.RUNNING.value   # reattach 非终态 Job，不结算
    assert len(q2.jobs) == 1, "重复提交！"          # §15-9 no duplicate
    # batch 未生成（worker 没跑）
    proj = ProjectStore(tmp_path).load()
    assert not [b for b in proj.get_shot("s01_c01").first_frame_batches
                if b.origin_run_id == tid], "reattach 不应产新 batch"


def test_A10_repeated_delegate_same_task_no_dup_job(tmp_path, real_deps):
    """§15-10: 同一 AgentTask 再次 delegate（existing_ref 非终态）→ reattach，不重提交 Job。"""
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="i2v")])
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "i2v")])

    async def scenario():
        q = _new_queue(tmp_path)   # 不 start：Job 保持 queued
        rt = _runtime(tmp_path, q)
        task1 = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                  "x", "shot.first_frame", "execute")
        jid = task1["execution_ref"]["job_id"]
        # 再次 delegate 同 task（runtime 同 task_id 复用？不——delegate 每次建新 task）。
        # §15-10 的「repeated delegate of same AgentTask」= settler/executor reattach 同 task：
        # 直接调 execute_shot_production with existing_ref。
        from src.agents.shot_producer_capability import execute_shot_production
        res = await execute_shot_production(
            client=None, project_loader=lambda: ProjectStore(tmp_path).load(),
            project_dir=tmp_path, project_name=PROJECT, scope_type="shot", scope_id="s01_c01",
            instruction="x", capability_id="shot.first_frame", job_queue=q,
            existing_ref=task1["execution_ref"], real=True, origin_task_id=task1["task_id"])
        assert res.status == ResultStatus.RUNNING.value
        assert res.data["job_id"] == jid    # same job
        assert len(q.jobs) == 1              # no duplicate

    _run(scenario())


def test_A11_A12_new_task_new_batch_old_preserved(tmp_path, real_deps):
    """§15-11/12/§44: 新 AgentTask 产新 batch；旧 batch 保留（regeneration=新版本非抹除）。"""
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="i2v")])
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "i2v")])

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        t1 = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                               "x", "shot.first_frame", "execute")
        await _wait_job_terminal(q, t1["execution_ref"]["job_id"])
        await _settler(tmp_path, q).recover_tasks()
        # 第二次：新 AgentTask（新 task_id）→ 新 batch
        t2 = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                               "x", "shot.first_frame", "execute")
        await _wait_job_terminal(q, t2["execution_ref"]["job_id"])
        await _settler(tmp_path, q).recover_tasks()
        return t1["task_id"], t2["task_id"]

    t1, t2 = _run(scenario())
    assert t1 != t2
    proj = ProjectStore(tmp_path).load()
    sh = proj.get_shot("s01_c01")
    b1 = [b for b in sh.first_frame_batches if b.origin_run_id == t1]
    b2 = [b for b in sh.first_frame_batches if b.origin_run_id == t2]
    assert b1 and b2, "两个 AgentTask 各应产自己的 batch"
    assert len(sh.first_frame_batches) >= 2, "旧 batch 应保留（非抹除）"


def test_A13_progress_from_job_state(tmp_path, real_deps):
    """§15-13: progress 从持久 job 态重建（phase/job_state/progress_pct）。"""
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="i2v")])
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "i2v")])

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
        assert data["phase"] == "first_frame"
        assert data["job_state"] == "done"
        assert data["progress_pct"] == 100
        assert data["job_id"] == task["execution_ref"]["job_id"]

    _run(scenario())


def test_A24_job_done_artifact_missing_not_falsely_complete(tmp_path, real_deps):
    """§24/§45: Job done 但 artifact 缺失（无 origin batch）→ AgentTask 不 falsely complete。"""
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="i2v")])
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "i2v")])
    tid_holder = []

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        tid_holder.append(task["task_id"])
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])

    _run(scenario())
    # 手动抹除 batch（模拟 integrity 缺失），settler 应判 integrity error，不 falsely complete
    store = AgentTaskStore(tmp_path, PROJECT)
    tid = tid_holder[0]

    def _strip(s):
        s.first_frame_batches = []
    ProjectStore(tmp_path).update_shot("s01_c01", _strip)
    q2 = _new_queue(tmp_path)
    settler = _settler(tmp_path, q2)
    asyncio.run(settler.recover_tasks())
    t = store.get(tid)
    assert t.status == TaskStatus.FAILED.value, "Job done 但 artifact 缺失应判 integrity error，不 falsely complete"
    assert "artifact" in (t.error or "")


# ============================================================
# §43 Plan gates — missing/draft/stale/incompatible → no Agnes call
# ============================================================
def test_P1_missing_plan_no_agnes_no_job(tmp_path, real_deps):
    """§43: 无 Plan → blocker，不提交 Job、不调 Agnes。"""
    agnes = real_deps
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="i2v")])
    # 不写 plan

    async def scenario():
        q = _new_queue(tmp_path)
        await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
        return task

    task = _run(scenario())
    assert task["status"] == TaskStatus.FAILED.value
    assert "PRODUCTION_PLAN_MISSING" in (task.get("error") or "") or "blocker" in (task.get("structured_result", {}).get("data", {}).get("blocker_code", "") or "")
    assert agnes.create_video_count() == 0 and not any(c == "gen_image" for c in agnes.calls), "无 Plan 不应调 Agnes"


def test_P2_draft_plan_no_agnes(tmp_path, real_deps):
    """§43: draft（未 approved）Plan → blocker，不调 Agnes。"""
    agnes = real_deps
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="i2v")])
    # 写 draft plan（不 approve）
    proj = ProjectStore(tmp_path).load()
    sc = next(s for s in proj.scenes if s.scene_id == "s01")
    plan = sanitize_plan(sc, {"overview": "x", "shots": [
        {"shot_id": "s01_c01", "opening_source": "independent", "link_mode": "i2v", "reason": "r"}]},
        run_seed=1, project_name=PROJECT)
    # plan.status 保持 "draft"
    plans = _read_plans(tmp_path); plans["s01"] = plan; _write_plans(tmp_path, plans)

    async def scenario():
        q = _new_queue(tmp_path); await q.start()
        rt = _runtime(tmp_path, q)
        return await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
    task = _run(scenario())
    assert task["status"] == TaskStatus.FAILED.value
    assert not any(c == "gen_image" for c in agnes.calls)


def test_P3_stale_plan_no_agnes(tmp_path, real_deps):
    """§43: stale Plan（scene summary 变了）→ blocker，不调 Agnes。"""
    agnes = real_deps
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="i2v")])
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "i2v")])
    # 改 scene summary → stale
    p = ProjectStore(tmp_path).load(); p.scenes[0].summary = "完全不同的故事段"
    ProjectStore(tmp_path).save(p)

    async def scenario():
        q = _new_queue(tmp_path); await q.start()
        rt = _runtime(tmp_path, q)
        return await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.first_frame", "execute")
    task = _run(scenario())
    assert task["status"] == TaskStatus.FAILED.value
    assert not any(c == "gen_image" for c in agnes.calls)


# ============================================================
# §18 Gate B — shot.last_frame real mapping
# ============================================================
def _seed_first_frame_done(tmp_path, shot_id="s01_c01", link_mode="keyframes"):
    """预设一个已就绪首帧（done + 公网 url + selected_first_frame），供尾帧/视频 preflight。"""
    from src.core.models import Asset, ImageAsset, ShotStatus, ImageBatch, Candidate
    def _mut(s):
        s.link_mode = link_mode
        s.first_frame = ImageAsset(
            candidates=[Asset(local="ff.png", url="https://fake.agnes/ff.png",
                               status=ShotStatus.DONE.value)],
            selected_index=0, status=ShotStatus.DONE.value)
        if not any(b.batch_id == "ff_seed" for b in s.first_frame_batches):
            s.first_frame_batches.append(ImageBatch(
                batch_id="ff_seed", kind="first_frame", created_at=_t.time(),
                status=ShotStatus.DONE.value,
                candidates=[Candidate(candidate_id="ff_seed_1", local="ff.png",
                                      url="https://fake.agnes/ff.png",
                                      status=ShotStatus.DONE.value, created_at=_t.time())]))
        s.selected_first_frame = "ff_seed_1"
    ProjectStore(tmp_path).update_shot(shot_id, _mut)


def test_B1_last_frame_real_handler_through_agenttask(tmp_path, real_deps):
    """§18-1/5/6: valid keyframes Shot → last-frame through AgentTask → real last_frame Job →
    持久化尾帧 batch → settler 据 origin 结算 completed。"""
    agnes = real_deps
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="keyframes")])
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "keyframes")])
    _seed_first_frame_done(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path); await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.last_frame", "execute")
        assert task["status"] == TaskStatus.RUNNING.value
        ref = task["execution_ref"]
        assert ref["kind"] == "last_frame"
        assert ref["origin_task_id"] == task["task_id"]
        await _wait_job_terminal(q, ref["job_id"])
        # §18-5: 尾帧 batch 持久化（origin_run_id==task_id）
        sh = ProjectStore(tmp_path).load().get_shot("s01_c01")
        own = [b for b in sh.last_frame_batches if b.origin_run_id == task["task_id"]]
        assert own and own[-1].candidates
        # §18-6: settler 结算 completed
        settler = _settler(tmp_path, q); await settler.recover_tasks()
        got = rt.get_task(PROJECT, task["task_id"])
        assert got["status"] == TaskStatus.COMPLETED.value

    _run(scenario())
    assert any(c == "gen_image" for c in agnes.calls)


def test_B2_missing_first_frame_no_agnes(tmp_path, real_deps):
    """§18-2: 缺已就绪首帧 → blocker，不调 Agnes。"""
    agnes = real_deps
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="keyframes")])   # 无首帧
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "keyframes")])

    async def scenario():
        q = _new_queue(tmp_path); await q.start()
        rt = _runtime(tmp_path, q)
        return await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.last_frame", "execute")
    task = _run(scenario())
    assert task["status"] == TaskStatus.FAILED.value
    assert "PREFLIGHT_DEPENDENCY" in (task.get("error") or "")
    assert not any(c == "gen_image" for c in agnes.calls), "缺首帧不应调 Agnes"


def test_B3_incompatible_plan_no_agnes(tmp_path, real_deps):
    """§18-3: Plan link_mode=i2v（非 keyframes）+ last_frame 能力 → blocker，不调 Agnes。"""
    agnes = real_deps
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="i2v")])
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "i2v")])  # i2v
    _seed_first_frame_done(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path); await q.start()
        rt = _runtime(tmp_path, q)
        return await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.last_frame", "execute")
    task = _run(scenario())
    assert task["status"] == TaskStatus.FAILED.value
    assert "PRODUCTION_PLAN_INCOMPATIBLE" in (task.get("error") or "")
    assert not any(c == "gen_image" for c in agnes.calls)


def test_B7_B9_history_preserved_new_task_new_version(tmp_path, real_deps):
    """§18-7/9 + §44: 新 AgentTask 产新尾帧 batch；旧 batch 保留。"""
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="keyframes")])
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "keyframes")])
    _seed_first_frame_done(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path); await q.start()
        rt = _runtime(tmp_path, q)
        t1 = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                               "x", "shot.last_frame", "execute")
        await _wait_job_terminal(q, t1["execution_ref"]["job_id"])
        await _settler(tmp_path, q).recover_tasks()
        t2 = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                               "x", "shot.last_frame", "execute")
        await _wait_job_terminal(q, t2["execution_ref"]["job_id"])
        await _settler(tmp_path, q).recover_tasks()
        return t1["task_id"], t2["task_id"]

    t1, t2 = _run(scenario())
    sh = ProjectStore(tmp_path).load().get_shot("s01_c01")
    b1 = [b for b in sh.last_frame_batches if b.origin_run_id == t1]
    b2 = [b for b in sh.last_frame_batches if b.origin_run_id == t2]
    assert b1 and b2 and len(sh.last_frame_batches) >= 2, "新旧尾帧 batch 都应保留"


def test_B8_same_task_restart_no_dup(tmp_path, real_deps):
    """§18-8: 同 AgentTask restart reattach 不重提交、不产额外 batch。"""
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="keyframes")])
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "keyframes")])
    _seed_first_frame_done(tmp_path)
    holder = []

    async def scenario():
        q = _new_queue(tmp_path)   # 不 start：Job 保持 queued
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.last_frame", "execute")
        holder.append((task["task_id"], task["execution_ref"]["job_id"]))

    _run(scenario())
    tid, jid = holder[0]
    q2 = _new_queue(tmp_path)
    assert len(q2.jobs) == 1
    asyncio.run(_settler(tmp_path, q2).recover_tasks())
    t = AgentTaskStore(tmp_path, PROJECT).get(tid)
    assert t.status == TaskStatus.RUNNING.value   # reattach 非终态
    assert len(q2.jobs) == 1


# ============================================================
# §25 Gate C — shot.video real mapping + restart/idempotency
# ============================================================
async def _wait_video_id(tmp_path, shot_id, timeout=8.0):
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        sh = ProjectStore(tmp_path).load().get_shot(shot_id)
        if sh and getattr(sh.video, "video_id", None):
            return sh.video.video_id
        await asyncio.sleep(0.05)
    return None


def _seed_video_ready(tmp_path):
    """i2v shot + 已就绪首帧（done+url），供视频 preflight/handler。"""
    _save_proj(tmp_path, [_shot("s01_c01", link_mode="i2v")])
    _seed_approved_plan(tmp_path, "s01", [("s01_c01", "independent", "i2v")])
    _seed_first_frame_done(tmp_path, link_mode="i2v")


def test_C1_video_real_handler_create_video_once_and_settles(tmp_path, real_deps):
    """§25-1/4/5/9/10: AgentTask → real video Job（i2v）→ create_video exactly once →
    video_id persisted → poll done → VideoVersion(origin=task_id) → settler completed。"""
    agnes = real_deps
    _seed_video_ready(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path); await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.video", "execute")
        assert task["status"] == TaskStatus.RUNNING.value
        ref = task["execution_ref"]
        assert ref["kind"] == "video"
        assert ref["origin_task_id"] == task["task_id"]
        await _wait_job_terminal(q, ref["job_id"])
        # §25-9: VideoVersion persisted (origin=task_id)
        sh = ProjectStore(tmp_path).load().get_shot("s01_c01")
        own = [v for v in sh.video_versions if v.origin_run_id == task["task_id"]]
        assert own, "应持久化带 origin_run_id==task_id 的 VideoVersion"
        # §25-10: 仅在 VideoVersion 持久化后 settler complete
        settler = _settler(tmp_path, q); await settler.recover_tasks()
        got = rt.get_task(PROJECT, task["task_id"])
        assert got["status"] == TaskStatus.COMPLETED.value
        assert any(r["type"] == "video_version" for r in got["artifact_refs"])
        return task["task_id"], ref["job_id"]

    tid, jid = _run(scenario())
    assert agnes.create_video_count() == 1, "§25-5 create_video exactly once"


def test_C6_terminal_remote_failure_settles_failed(tmp_path, real_deps):
    """§25-11: terminal remote failure（poll AgnesError）→ Job failed → AgentTask failed。"""
    agnes = real_deps
    agnes._poll_error = True
    _seed_video_ready(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path); await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.video", "execute")
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])
        assert q.get(task["execution_ref"]["job_id"]).state == "failed"
        settler = _settler(tmp_path, q); await settler.recover_tasks()
        got = rt.get_task(PROJECT, task["task_id"])
        assert got["status"] == TaskStatus.FAILED.value

    _run(scenario())
    assert agnes.create_video_count() == 1   # 提交了一次（remote terminal 后失败）


def test_C_restart_settler_fallback_exact_video_id(tmp_path, real_deps):
    """§20/§27 + 用户决策2: video restart-recovery 路径无带 origin 的 VideoVersion → settler 仅在
    **exact video_id 匹配**（execution_ref.video_id == shot.video.video_id）+ shot.video done 时
    fallback settle completed。same T1/J1/V1；settler 不调 create_video。

    模拟 crash 后状态：J1 running orphan、V1 已持久化、recovery 已完成（poll+download，未建 origin-version）。
    （create_video「恰好一次」由 C1 真实 handler 证明；recovery 不重 create_video 由 C_recovery 证明；
      本测试证明 settler fallback 的身份校验——三者合证 §25「create_video==1 跨 restart」。）"""
    agnes = real_deps
    _seed_video_ready(tmp_path)
    from src.core.models import ShotStatus
    from src.core.job_queue import Job
    from src.agents.models import AgentTask

    # crash 后 + recovery 完成态：video done，video_id=V1，但无带 origin 的 VideoVersion
    def _done(s):
        s.video.video_id = "V1"; s.video.status = ShotStatus.DONE.value
        s.video.local = str(tmp_path / "out.mp4"); s.video.url = "https://fake.agnes/v.mp4"
    ProjectStore(tmp_path).update_shot("s01_c01", _done)
    # T1 running，execution_ref 已录 expected video_id=V1（先前 poll 录入）
    store = AgentTaskStore(tmp_path, PROJECT)
    t = AgentTask(task_id="task_T1", project=PROJECT, requester_agent="producer",
                  target_agent="shot_producer", capability_id="shot.video",
                  scope_type="shot", scope_id="s01_c01", operation="execute",
                  status=TaskStatus.RUNNING.value,
                  execution_ref={"job_id": "J1", "kind": "video", "phase": "video",
                                 "shot_id": "s01_c01", "origin_task_id": "task_T1",
                                 "video_id": "V1"})
    store.create(t)
    # J1 running orphan（crash 后未被 worker 重新接管）
    q = _new_queue(tmp_path)
    q.jobs["J1"] = Job(job_id="J1", project=PROJECT, kind="video", shot_id="s01_c01",
                       state="running", progress=15)
    q._save()
    # settler 录 video_id（ref 已有 V1，无需录）+ fallback settle
    asyncio.run(_settler(tmp_path, q).recover_tasks())
    t2 = store.get("task_T1")
    assert t2.status == TaskStatus.COMPLETED.value, "exact video_id fallback 应 settle completed"
    assert t2.execution_ref["job_id"] == "J1"
    assert t2.execution_ref["video_id"] == "V1"   # same V1
    assert agnes.create_video_count() == 0, "settler fallback 不应 create_video"


# ============================================================
# §1 shutdown-fix 验证：S4 REAL blocking-poll restart now clean / S5 idempotent
# ============================================================
def test_S4_restart_video_create_video_once_with_shutdown_fix(tmp_path, real_deps):
    """§25/§27 + shutdown fix: REAL blocking-poll restart 现在 clean 结束（此前会 hang）。
    T1→J1→create_video once→V1 持久化→asyncio.run cleanup（worker 终止，J1 保留 RUNNING）
    →restart→new queue _load J1→settler fallback settle same T1/J1/V1。create_video==1；no second create_video。"""
    import threading
    agnes = real_deps
    agnes._video_done = False   # poll blocks (mid-video crash simulation)
    _seed_video_ready(tmp_path)
    holder = []

    async def phase1():
        q = _new_queue(tmp_path); await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.video", "execute")
        jid = task["execution_ref"]["job_id"]
        vid = await _wait_video_id(tmp_path, "s01_c01")
        await _settler(tmp_path, q).recover_tasks()   # 录 video_id 入 execution_ref
        holder.append((task["task_id"], jid, vid))

    err = []

    def _run():
        try:
            asyncio.run(phase1())
        except BaseException as e:
            err.append(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(20.0)   # hard deadline：超时 = FAIL（不当作 PASS）
    assert not t.is_alive(), "phase1 asyncio.run cleanup hung (shutdown fix broken)"
    assert not err, f"phase1 raised: {err}"
    assert holder, "phase1 did not record task/job/vid"
    tid, jid, vid = holder[0]

    # restart: new queue _load → J1 kept RUNNING (fix#2)
    q2 = _new_queue(tmp_path)
    j1 = q2.get(jid)
    assert j1 is not None and j1.state == "running", "shutdown 应保留 video Job RUNNING（restart reattach）"
    # simulate recovery completion（recover_pending_videos 续 poll V1 → done → 下载入库）
    from src.core.models import ShotStatus
    def _done(s):
        s.video.status = ShotStatus.DONE.value
        s.video.local = str(tmp_path / "out.mp4"); s.video.url = "https://fake.agnes/v.mp4"
    ProjectStore(tmp_path).update_shot("s01_c01", _done)
    # restart settler: reattach J1 + fallback settle (exact video_id)
    asyncio.run(_settler(tmp_path, q2).recover_tasks())
    t2 = AgentTaskStore(tmp_path, PROJECT).get(tid)
    assert t2.status == TaskStatus.COMPLETED.value
    assert t2.execution_ref["job_id"] == jid
    assert t2.execution_ref["video_id"] == vid
    assert agnes.create_video_count() == 1, "restart-recovery 不应二次 create_video（§25-15）"


def test_S5_repeated_shutdown_reconcile_idempotent(tmp_path, real_deps):
    """shutdown/reconstruction + settle + settle again → 无 dup Job/AgentTask/batch/version/create_video。"""
    agnes = real_deps
    _seed_video_ready(tmp_path)
    holder = []

    async def phase1():
        q = _new_queue(tmp_path); await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.video", "execute")
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])   # poll done (default) → complete
        holder.append(task["task_id"])

    asyncio.run(phase1())
    tid = holder[0]
    # restart + settle twice
    q2 = _new_queue(tmp_path)
    settler = _settler(tmp_path, q2)
    asyncio.run(settler.recover_tasks())
    asyncio.run(settler.recover_tasks())
    t = AgentTaskStore(tmp_path, PROJECT).get(tid)
    assert t.status == TaskStatus.COMPLETED.value
    assert len(q2.jobs) == 1, "no dup Job"
    sh = ProjectStore(tmp_path).load().get_shot("s01_c01")
    own = [v for v in sh.video_versions if v.origin_run_id == tid]
    assert len(own) == 1, "no dup VideoVersion for same-task"
    assert agnes.create_video_count() == 1, "no dup create_video"


def test_C_recovery_no_resubmit_no_create_video(tmp_path, real_deps):
    """§20/§25-15: recover_pending_videos 对 video.status==generating + video_id 的 shot 续 poll+download，
    不调 create_video、不重提交 video Job（除非 poll 未完成，此时才 re-enqueue——但 handler 续轮询不重 create_video）。"""
    import services.lifecycle as lifecycle
    agnes = real_deps
    # project 置于 tmp_path/PROJECT（projects_dir=tmp_path 才能被 recover 扫到）
    pdir = tmp_path / PROJECT
    _save_proj(pdir, [_shot("s01_c01", link_mode="i2v")])
    _seed_approved_plan(pdir, "s01", [("s01_c01", "independent", "i2v")])
    from src.core.models import ShotStatus
    def _gen(s):
        s.video.video_id = "V1"; s.video.status = ShotStatus.GENERATING.value
    ProjectStore(pdir).update_shot("s01_c01", _gen)
    saved_pd = lifecycle.projects_dir
    saved_jq = deps.job_queue
    lifecycle.projects_dir = tmp_path
    deps.job_queue = None      # 避免 recover re-enqueue（本测试只验 poll+download 不重 create_video）
    try:
        asyncio.run(lifecycle.recover_pending_videos())
    finally:
        lifecycle.projects_dir = saved_pd
        deps.job_queue = saved_jq
    assert agnes.create_video_count() == 0, "recover 不应 create_video（§20 续 poll 不重提交）"
    assert "poll" in agnes.calls and "download" in [c for c in agnes.calls], "recover 应 poll+download"
    sh = ProjectStore(pdir).load().get_shot("s01_c01")
    assert sh.video.status == ShotStatus.DONE.value


# ============ 42.md §二缺陷②④：恢复钩子参数化 + 提交结果不明可见化 ============
def test_C42_recover_parameterized_queue_isolated_driver(tmp_path, real_deps):
    """42.md 缺陷②：进程内驱动器（隔离队列文件）传自身 queue 给 recover——
    poll 未完成的 generating 任务重排队进【驱动器自己的队列】，而不是 deps 全局；
    驱动器进程重启后可获得与后端 startup 同等的恢复钩子。"""
    import services.lifecycle as lifecycle
    agnes = real_deps
    pdir = tmp_path / PROJECT
    _save_proj(pdir, [_shot("s01_c01", link_mode="i2v")])
    _seed_approved_plan(pdir, "s01", [("s01_c01", "independent", "i2v")])
    from src.core.models import ShotStatus
    ProjectStore(pdir).update_shot(
        "s01_c01", lambda s: (
            setattr(s.video, "video_id", "V42"),
            setattr(s.video, "status", ShotStatus.GENERATING.value),
            setattr(s.video, "model", "agnes-video-2.5-flash")))
    saved_pd = lifecycle.projects_dir
    lifecycle.projects_dir = tmp_path
    agnes._poll_error = True   # poll 异常 → recover 走「未完成 → 重新入队」分支
    try:
        driver_q = _new_queue(tmp_path)          # 驱动器隔离队列（未 start，仅收 job）
        asyncio.run(lifecycle.recover_pending_videos(queue=driver_q, client=agnes))
    finally:
        lifecycle.projects_dir = saved_pd
    assert agnes.create_video_count() == 0, "recover 不重提交 create_video"
    sh = ProjectStore(pdir).load().get_shot("s01_c01")
    assert sh.video.status == "queued", "poll 未完成 → 标 queued 续轮询"
    assert len(driver_q.jobs) == 1, "重排队必须进【传入的驱动器队列】（不是 deps 全局）"
    _j = next(iter(driver_q.jobs.values()))
    assert _j.kind == "video"


def test_C42_recover_submit_uncertain_left_untouched(tmp_path, real_deps):
    """42.md 缺陷④：generating 且无 video_id（提交结果不明）——recover 不自动
    处置（不重提交、不改状态），显式告警；处置留给显式重提交（带标注）。"""
    import services.lifecycle as lifecycle
    agnes = real_deps
    pdir = tmp_path / PROJECT
    _save_proj(pdir, [_shot("s01_c01", link_mode="i2v")])
    _seed_approved_plan(pdir, "s01", [("s01_c01", "independent", "i2v")])
    from src.core.models import ShotStatus
    ProjectStore(pdir).update_shot(
        "s01_c01", lambda s: setattr(s.video, "status", ShotStatus.GENERATING.value))
    saved_pd = lifecycle.projects_dir
    lifecycle.projects_dir = tmp_path
    try:
        driver_q = _new_queue(tmp_path)
        asyncio.run(lifecycle.recover_pending_videos(queue=driver_q, client=agnes))
    finally:
        lifecycle.projects_dir = saved_pd
    sh = ProjectStore(pdir).load().get_shot("s01_c01")
    assert sh.video.status == ShotStatus.GENERATING.value, "结果不明状态不得被自动改写"
    assert len(driver_q.jobs) == 0, "结果不明任务不得自动入队重提交"
    assert agnes.create_video_count() == 0 and not any(
        c == "poll" for c in agnes.calls), "无 video_id 不可轮询"


def test_C42_video_handler_marks_previous_submit_uncertain(tmp_path, real_deps):
    """42.md 缺陷④ + Phase56 A3 收口：上次提交结果不明（generating 无 video_id）
    → ①自动路径（delegate 无确认）新提交被拒绝（待核验——previous_submit_uncertain
    只是标记，不是自动重发许可）；②显式确认（confirm_uncertain_resubmit）放行后
    VideoVersion.previous_submit_uncertain=True（重复生成风险如实落库）。"""
    agnes = real_deps
    _seed_video_ready(tmp_path)
    from src.core.models import ShotStatus
    from src.core.models import VideoVersion

    async def scenario():
        q = _new_queue(tmp_path); await q.start()
        rt = _runtime(tmp_path, q)
        ProjectStore(tmp_path).update_shot(
            "s01_c01", lambda s: setattr(s.video, "status", ShotStatus.GENERATING.value))
        # ① 自动路径：无显式确认 → 拒绝新提交（待核验）
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot",
                                 "s01_c01", "x", "shot.video", "execute")
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])
        job1 = q.get(task["execution_ref"]["job_id"])
        assert job1.state == "failed", job1
        assert "待核验" in (job1.error or ""), job1.error
        # ② 显式确认放行（operator 人工对账后的合法重发通道）
        job2 = q.submit(PROJECT, "video", "s01_c01",
                        confirm_uncertain_resubmit=True,
                        origin_run_id="explicit_confirm_test")
        await _wait_job_terminal(q, job2.job_id)
        return job2

    job2 = asyncio.run(scenario())
    assert job2.state == "done", job2.error
    sh = ProjectStore(tmp_path).load().get_shot("s01_c01")
    own = [v for v in sh.video_versions
           if v.origin_run_id == "explicit_confirm_test"]
    assert own and own[0].previous_submit_uncertain is True
    # 对照：正常 fresh 提交（无历史不明）应为 False
    _seed_video_ready(tmp_path)
    ProjectStore(tmp_path).update_shot(
        "s01_c01", lambda s: (setattr(s.video, "status", ShotStatus.PENDING.value),
                              setattr(s.video, "video_id", None)))
    # 已 DONE 的旧字段不重置（本对照只验字段缺省）：直接构造版本级默认断言
    assert VideoVersion(version_id="vv_x").previous_submit_uncertain is False


def test_C_no_false_complete_on_video_id_mismatch(tmp_path, real_deps):
    """§24/用户决策2: expected_video_id ≠ shot.video.video_id → 不 falsely complete。
    （另一来源的视频不应被错 settle 给本 AgentTask；fallback 必须 exact 匹配。）"""
    agnes = real_deps
    _seed_video_ready(tmp_path)
    from src.core.models import ShotStatus
    from src.core.job_queue import Job
    from src.agents.models import AgentTask
    # T1 的 execution_ref 录了 V1（expected_video_id），但 shot.video 现在是 V_OTHER + done
    def _other(s):
        s.video.video_id = "V_OTHER"; s.video.status = ShotStatus.DONE.value
        s.video.local = str(tmp_path / "other.mp4"); s.video.url = "https://fake/other.mp4"
    ProjectStore(tmp_path).update_shot("s01_c01", _other)
    store = AgentTaskStore(tmp_path, PROJECT)
    t = AgentTask(task_id="task_T1", project=PROJECT, requester_agent="producer",
                  target_agent="shot_producer", capability_id="shot.video",
                  scope_type="shot", scope_id="s01_c01", operation="execute", status="running",
                  execution_ref={"job_id": "J1", "kind": "video", "phase": "video",
                                 "shot_id": "s01_c01", "origin_task_id": "task_T1",
                                 "video_id": "V1"})      # expected = V1（≠ V_OTHER）
    store.create(t)
    q = _new_queue(tmp_path)
    q.jobs["J1"] = Job(job_id="J1", project=PROJECT, kind="video", shot_id="s01_c01",
                       state="running", progress=15)
    q._save()
    asyncio.run(_settler(tmp_path, q).recover_tasks())
    t2 = store.get("task_T1")
    assert t2.status != TaskStatus.COMPLETED.value, "video_id 不匹配不应 falsely complete"
    assert agnes.create_video_count() == 0


def test_C_new_task_new_version_old_preserved(tmp_path, real_deps):
    """§25-9 + §44: 新 AgentTask T2 → 新 J2 + 新 VideoVersion；旧 T1 的 VideoVersion 保留。
    create_video 总计 2（每个 task 一次）。"""
    agnes = real_deps
    _seed_video_ready(tmp_path)

    async def scenario():
        q = _new_queue(tmp_path); await q.start()
        rt = _runtime(tmp_path, q)
        t1 = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                               "x", "shot.video", "execute")
        await _wait_job_terminal(q, t1["execution_ref"]["job_id"])
        await _settler(tmp_path, q).recover_tasks()
        assert rt.get_task(PROJECT, t1["task_id"])["status"] == TaskStatus.COMPLETED.value
        # 第二次：新 AgentTask（新 task_id）→ 新 video 生成
        t2 = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                               "x", "shot.video", "execute")
        await _wait_job_terminal(q, t2["execution_ref"]["job_id"])
        await _settler(tmp_path, q).recover_tasks()
        assert rt.get_task(PROJECT, t2["task_id"])["status"] == TaskStatus.COMPLETED.value
        return t1["task_id"], t2["task_id"]

    t1, t2 = _run(scenario())
    assert t1 != t2
    sh = ProjectStore(tmp_path).load().get_shot("s01_c01")
    v1 = [v for v in sh.video_versions if v.origin_run_id == t1]
    v2 = [v for v in sh.video_versions if v.origin_run_id == t2]
    assert v1 and v2, "两个 AgentTask 各应产自己的 VideoVersion"
    assert len(sh.video_versions) >= 2, "旧 VideoVersion 应保留（非抹除）"
    assert agnes.create_video_count() == 2


def test_C_settle_twice_idempotent(tmp_path, real_deps):
    """§25-14/§45: 重复 settlement 幂等——多次 recover_tasks 同一最终结果。"""
    _seed_video_ready(tmp_path)
    holder = []

    async def scenario():
        q = _new_queue(tmp_path); await q.start()
        rt = _runtime(tmp_path, q)
        task = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", "s01_c01",
                                 "x", "shot.video", "execute")
        await _wait_job_terminal(q, task["execution_ref"]["job_id"])
        holder.append(task["task_id"])

    _run(scenario())
    tid = holder[0]
    q2 = _new_queue(tmp_path)
    settler = _settler(tmp_path, q2)
    asyncio.run(settler.recover_tasks())
    r1 = AgentTaskStore(tmp_path, PROJECT).get(tid)
    asyncio.run(settler.recover_tasks())
    asyncio.run(settler.recover_tasks())
    r2 = AgentTaskStore(tmp_path, PROJECT).get(tid)
    assert r1.status == TaskStatus.COMPLETED.value
    assert r2.status == TaskStatus.COMPLETED.value
    assert r2.structured_result["status"] == r1.structured_result["status"]
