# Phase55 C 定向测试：持久恢复与有界并发（watchdog 休眠保护/收货幂等/并发档位）。
#
# 覆盖（任务书 §11.1「恢复」「容量」行 + M5 事故回归）：
#   1. watchdog：已知 provider task ID 的视频 job 在本地失联（无 progress 超时）时
#      不作废不补交（延长观察窗+计数留痕）；延长达上限如实 FAILED；无 task ID 的
#      job 维持原行为；
#   2. provider task ID 随 job 落档（真实 handler 链路）；
#   3. 收货幂等：同一 provider video_id 双次完成只落一个 VideoVersion（不覆盖新选版）；
#   4. 并发档位：config 档位构造参数生效（video worker 数），默认 1 串行不变。
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import deps
from src.core.job_queue import JobQueue, JobState
from src.core.models import (Project, Scene, Shot, Character, StyleAnchor,
                             ImageAsset, Asset, ShotStatus)

PROJECT = "_test_p55_recovery"


# ============ 引擎级夹具（真实 JobQueue + 真实/注入 handler + FakeAgnes） ============
class _FakeQuota:
    def can(self, kind, amount=1):
        return (True, "")

    def consume(self, kind, amount=1):
        pass


class FakeAgnes:
    def __init__(self):
        self.calls = []
        self.quota = _FakeQuota()
        self.video_id = "vid_fake_1"   # 恒同 id → 收货幂等场景

    async def download(self, url, dest):
        self.calls.append("download")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"FAKE_VIDEO")
        return str(dest)

    async def create_video(self, prompt, *, mode="i2v", image_url=None, image_urls=None,
                           num_frames=121, frame_rate=24, width=1312, height=736,
                           seed=None, negative_prompt="", model=None, duration_s=None):
        self.calls.append("create_video")
        return {"video_id": self.video_id, "task_id": "t_1"}

    async def poll_video(self, video_id, *, interval=5.0, max_wait=3600,
                         on_progress=None, max_consecutive_failures=5, model_name=None):
        self.calls.append("poll")
        if on_progress:
            await on_progress(100, "completed", 0)
        return {"url": "https://fake.agnes/videos/fake.mp4",
                "seconds": 5.0, "size": "1280x704"}


class FakeRehost:
    async def upload(self, path):
        return "https://fake.imgur/rehost.png"


class _FakePM:
    def __init__(self, pdir):
        self._pdir = pdir
        self.base_dir = pdir

    def get_project_dir(self, name):
        return self._pdir


def _save_proj(tmp_path, shots=None):
    shots = shots if shots is not None else [_shot("s01_c01")]
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事",
                   characters=[Character(name="阿派", description_en="机甲")],
                   scenes=[Scene(scene_id="s01", summary="s", characters=["阿派"],
                                 shots=shots)])
    from src.core.store import ProjectStore
    ProjectStore(tmp_path).save(proj)
    return tmp_path


def _shot(sid, idx=1, chars=None, dur=5.0, link_mode="i2v"):
    s = Shot(shot_id=sid, scene_id=sid.rsplit("_c", 1)[0], index=idx,
             description="机甲启动", characters=chars or [], target_duration_s=dur)
    s.link_mode = link_mode
    return s


def _mark_ff_ready(tmp_path, shot_id, url="https://fake.agnes/first.png"):
    from src.core.store import ProjectStore
    store = ProjectStore(tmp_path)

    def mut(s):
        s.first_frame = ImageAsset(
            candidates=[Asset(local="x", url=url, status=ShotStatus.DONE.value)],
            selected_index=0, status=ShotStatus.DONE.value)
    store.update_shot(shot_id, mut)


@pytest.fixture
def rec_env(tmp_path):
    agnes = FakeAgnes()
    saved = (deps.agnes_client, deps._rehost_host, deps.project_manager)
    deps.agnes_client = agnes
    deps._rehost_host = FakeRehost()
    deps.project_manager = _FakePM(tmp_path)
    try:
        yield agnes, tmp_path
    finally:
        deps.agnes_client, deps._rehost_host, deps.project_manager = saved


def _real_handlers():
    from routers.shots import _handler_first_frame, _handler_video
    return {"first_frame": _handler_first_frame, "video": _handler_video}


# ============ 1/2. watchdog：休眠不作废 + task ID 落档 ============
def test_watchdog_spares_video_with_provider_task_id(tmp_path, rec_env):
    """已知 provider task ID：本地失联（无 progress 超 600s 等价——测试用短窗）
    不作废不补交；handler 完成后 job 正常 done；watchdog_extensions 留痕。"""
    agnes, tmp_path = rec_env
    _save_proj(tmp_path)
    _mark_ff_ready(tmp_path, "s01_c01")

    async def slow_video(job, emit):
        job.params["provider_task_id"] = "task_sleep_1"   # 模拟 handler 已提交
        await asyncio.sleep(1.2)                          # 期间无任何 progress
        await emit("poll", "唤醒后恢复", 50)
        await emit("done", "完成", 100)

    q = JobQueue(tmp_path / "jobs.json", {"video": slow_video})
    q.WATCHDOG_TIMEOUT = 0.4
    q.WATCHDOG_INTERVAL = 0.15

    async def scenario():
        await q.start()
        job = q.submit(PROJECT, "video", "s01_c01")
        for _ in range(400):
            if job.state in ("done", "failed", "cancelled"):
                break
            await asyncio.sleep(0.02)
        return job

    job = asyncio.run(scenario())
    assert job.state == "done", (job.state, job.error)
    # 持久证据=延长计数（message 会被 handler 后续 emit 覆盖，不作断言）
    assert int(job.params.get("watchdog_extensions") or 0) >= 1


def test_watchdog_extension_cap_fails_honestly(tmp_path, rec_env):
    """延长窗达上限（预置 48 次）→ 如实 FAILED（不产生无限等待）。"""
    agnes, tmp_path = rec_env

    async def hanging_video(job, emit):
        job.params["provider_task_id"] = "task_x"
        job.params["watchdog_extensions"] = 48
        await asyncio.sleep(30)

    q = JobQueue(tmp_path / "jobs2.json", {"video": hanging_video})
    q.WATCHDOG_TIMEOUT = 0.3
    q.WATCHDOG_INTERVAL = 0.1

    async def scenario():
        await q.start()
        job = q.submit(PROJECT, "video", "s01_c01")
        for _ in range(300):
            if job.state in ("done", "failed", "cancelled"):
                break
            await asyncio.sleep(0.02)
        return job

    job = asyncio.run(scenario())
    assert job.state == "failed"
    assert "看门狗" in (job.error or "")


def test_watchdog_no_task_id_keeps_legacy_fail(tmp_path, rec_env):
    """无 provider task ID：维持原行为（FAILED + cancel）——提交确实可能未完成。"""
    agnes, tmp_path = rec_env

    async def hanging_video(job, emit):
        await emit("submit", "提交中", 10)   # 无 provider_task_id
        await asyncio.sleep(30)

    q = JobQueue(tmp_path / "jobs3.json", {"video": hanging_video})
    q.WATCHDOG_TIMEOUT = 0.3
    q.WATCHDOG_INTERVAL = 0.1

    async def scenario():
        await q.start()
        job = q.submit(PROJECT, "video", "s01_c01")
        for _ in range(300):
            if job.state in ("done", "failed", "cancelled"):
                break
            await asyncio.sleep(0.02)
        return job

    job = asyncio.run(scenario())
    assert job.state == "failed"


def test_provider_task_id_persisted_by_real_handler(tmp_path, rec_env):
    """真实 _handler_video：提交成功后 provider task ID 随 job.params 落档
    （watchdog 休眠保护的证据基础）。"""
    agnes, tmp_path = rec_env
    _save_proj(tmp_path)
    _mark_ff_ready(tmp_path, "s01_c01")
    q = JobQueue(tmp_path / "jobs4.json", _real_handlers())

    async def scenario():
        await q.start()
        job = q.submit(PROJECT, "video", "s01_c01")
        for _ in range(600):
            if job.state in ("done", "failed", "cancelled"):
                break
            await asyncio.sleep(0.02)
        return job

    job = asyncio.run(scenario())
    assert job.state == "done", (job.state, job.error)
    assert job.params.get("provider_task_id") == "vid_fake_1"


# ============ 3. 收货幂等 ============
def test_receipt_idempotent_same_video_id(tmp_path, rec_env):
    """同一 provider video_id 两次完成（重复通知/双路径）：只落一个 VideoVersion，
    指针刷新，不覆盖新选版语义（历史结果保留）。"""
    agnes, tmp_path = rec_env
    _save_proj(tmp_path)
    _mark_ff_ready(tmp_path, "s01_c01")
    from routers.shots import _handler_video

    async def scenario():
        emit = lambda *a, **k: asyncio.sleep(0)
        job1 = SimpleNamespace(job_id="j1", project=PROJECT, shot_id="s01_c01",
                               kind="video", params={"origin_run_id": "o1"},
                               state="running", progress=0, message="")
        await _handler_video(job1, emit)
        job2 = SimpleNamespace(job_id="j2", project=PROJECT, shot_id="s01_c01",
                               kind="video", params={"origin_run_id": "o2"},
                               state="running", progress=0, message="")
        await _handler_video(job2, emit)   # FakeAgnes 恒同 video_id → 重复收货

    asyncio.run(scenario())
    from src.core.store import ProjectStore
    shot = ProjectStore(tmp_path).load().get_shot("s01_c01")
    vids = [vv.video_id for vv in shot.video_versions]
    assert vids.count("vid_fake_1") == 1, vids
    assert shot.final_video == shot.video_versions[0].version_id
    assert shot.video.status == "done"


# ============ 4. 并发档位 ============
def test_video_worker_count_from_config(tmp_path, rec_env):
    """config 档位（video_worker_count=3）生效；缺省 1 串行不变。"""
    agnes, tmp_path = rec_env
    q3 = JobQueue(tmp_path / "j5b.json", {"video": lambda *a, **k: None},
                  video_worker_count=3)

    async def scenario():
        await q3.start()
        n3 = q3.video_worker_count
        q1b = JobQueue(tmp_path / "j5c.json", {"video": lambda *a, **k: None})
        await q1b.start()
        n1 = q1b.video_worker_count
        return n3, n1

    n3, n1 = asyncio.run(scenario())
    assert n3 == 3
    assert n1 == 1
