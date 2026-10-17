# tests/test_p51_b_population_wiring.py
# Phase51 B（B6/E2）——人数纪律接线定向验证（零 LLM / 零真实生成）。
# 缺陷：31.md C5 的 population_constraint 机制在生产路径无人传入（休眠）——
# E2 实证宽景缺员（无条款 0/2 双人 vs 有条款 2/2），接线修复=导演工具默认开启
# + 视频 handler 条款注入。本套件证明链路贯通：
#   工具默认 True → job.params → BOUND 首帧 prompt / 视频 prompt tail。
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import deps
from src.core.models import (Project, Scene, Shot, Character, StyleAnchor,
                             ImageAsset, Asset, ShotStatus)
from src.core.store import ProjectStore
from src.core.job_queue import JobQueue
from src.agents.runtime import AgentRuntime
from src.generation.image_adapter import (apply_population_clause,
                                          _population_constraint_clause)
from routers.shots import _handler_first_frame, _handler_video

PROJECT = "_test_p51_pop"


class _FakeQuota:
    def can(self, kind, amount=1):
        return (True, "")

    def consume(self, kind, amount=1):
        pass


class _FakeAgnes:
    def __init__(self):
        self.calls = []
        self.image_prompts = []
        self.video_prompts = []
        self.quota = _FakeQuota()

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        self.calls.append("gen_image")
        self.image_prompts.append(prompt)
        return {"url": "https://fake.agnes/images/frame.png", "b64": None}

    async def download(self, url, dest):
        self.calls.append("download")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"FAKE")
        return str(dest)

    async def create_video(self, prompt, *, mode="i2v", image_url=None,
                           image_urls=None, num_frames=121, frame_rate=24,
                           width=1312, height=736, seed=None,
                           negative_prompt="", model=None, duration_s=None):
        self.calls.append("create_video")
        self.video_prompts.append(prompt)
        return {"video_id": "vid_fake_1", "task_id": "t_1"}

    async def poll_video(self, video_id, *, interval=5.0, max_wait=3600,
                         on_progress=None, max_consecutive_failures=5,
                         model_name=None):
        self.calls.append("poll")
        if on_progress:
            await on_progress(100, "completed", 0)
        return {"url": "https://fake.agnes/videos/fake.mp4",
                "seconds": 3.0, "size": "1280x704"}


class _FakeRehost:
    async def upload(self, path):
        return "https://fake.host/rehost.png"


class _FakePM:
    def __init__(self, pdir):
        self._pdir = pdir
        self.base_dir = pdir

    def get_project_dir(self, name):
        return self._pdir


def _shot(sid, chars=("船长", "大副"), link_mode="i2v"):
    # Phase55 A：默认时长 3.0→5.0（2.5 契约 4-12s，3s 会在 preflight 被时长契约拒绝）
    s = Shot(shot_id=sid, scene_id=sid.rsplit("_c", 1)[0], index=1,
             description="黄昏甲板双人镜头", characters=list(chars),
             target_duration_s=5.0)
    s.link_mode = link_mode
    return s


def _save_proj(tmp_path, shots):
    proj = Project(name=PROJECT,
                   style=StyleAnchor(art_style="photorealistic", aspect_ratio="16:9"),
                   story_text="克苏鲁追船",
                   characters=[Character(name="船长", description_en="old captain"),
                               Character(name="大副", description_en="young mate")],
                   scenes=[Scene(scene_id="s01", summary="海上", characters=["船长", "大副"],
                                 shots=list(shots))])
    ProjectStore(tmp_path).save(proj)
    return proj


def _mark_ff_ready(tmp_path, shot_id):
    store = ProjectStore(tmp_path)

    def mut(s):
        f = tmp_path / "output" / "frames" / f"{shot_id}_first_v1.png"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"PNG")
        s.first_frame = ImageAsset(
            candidates=[Asset(local=str(f), url="https://fake.agnes/first.png",
                              status=ShotStatus.DONE.value)],
            selected_index=0, status=ShotStatus.DONE.value)
    store.update_shot(shot_id, mut)


@pytest.fixture
def pop_env(tmp_path):
    agnes = _FakeAgnes()
    saved = (deps.agnes_client, deps._rehost_host, deps.project_manager)
    deps.agnes_client = agnes
    deps._rehost_host = _FakeRehost()
    deps.project_manager = _FakePM(tmp_path)
    q = JobQueue(tmp_path / "jobs.json",
                 {"first_frame": _handler_first_frame, "video": _handler_video})
    rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                      settings_path=tmp_path / "agents_data" / "settings.json",
                      job_queue_getter=lambda: q, agnes_getter=lambda: agnes,
                      client=None)
    try:
        yield rt, q, agnes, tmp_path
    finally:
        deps.agnes_client, deps._rehost_host, deps.project_manager = saved


POP_MARK = "Population constraint:"


# ============ 工具层 → job.params ============
def test_b_w1_images_default_population_on(pop_env):
    """导演批量首帧工具：省略参数 → 默认 True 进 job.params（E2 接线修复）。"""
    from src.agents.director_visual import _execute_shot_generate_images
    rt, q, agnes, tmp_path = pop_env
    _save_proj(tmp_path, [_shot("s01_c01")])

    async def scenario():
        await q.start()
        return await _execute_shot_generate_images(
            rt, PROJECT, json.dumps({"targets": [{"shot_id": "s01_c01",
                                                   "frame": "first"}]}),
            "instr", "m1")

    obs = asyncio.run(scenario())
    assert obs["ok"], obs
    job = next(j for j in q.jobs.values() if j.kind == "first_frame")
    assert job.params.get("population_constraint") is True


def test_b_w2_images_explicit_opt_out(pop_env):
    """创作意图含额外人物：显式 false → job.params False（导演保留控制权）。"""
    from src.agents.director_visual import _execute_shot_generate_images
    rt, q, agnes, tmp_path = pop_env
    _save_proj(tmp_path, [_shot("s01_c01")])

    async def scenario():
        await q.start()
        return await _execute_shot_generate_images(
            rt, PROJECT,
            json.dumps({"targets": [{"shot_id": "s01_c01", "frame": "first"}],
                        "population_constraint": False}),
            "instr", "m1")

    obs = asyncio.run(scenario())
    assert obs["ok"], obs
    job = next(j for j in q.jobs.values() if j.kind == "first_frame")
    assert job.params.get("population_constraint") is False


def test_b_w3_videos_default_population_on(pop_env):
    """导演批量视频工具：省略参数 → 默认 True 进 video job.params。"""
    from src.agents.director_visual import _execute_shot_generate_videos
    rt, q, agnes, tmp_path = pop_env
    _save_proj(tmp_path, [_shot("s01_c01")])
    _mark_ff_ready(tmp_path, "s01_c01")

    async def scenario():
        await q.start()
        # Phase55 A：本测试对象=人数纪律默认值接线，非参考链契约——显式 legacy
        return await _execute_shot_generate_videos(
            rt, PROJECT, json.dumps({"shot_ids": ["s01_c01"],
                                     "reference_policy": "legacy_allowed"}), "instr", "m1")

    obs = asyncio.run(scenario())
    assert obs["ok"], obs
    job = next(j for j in q.jobs.values() if j.kind == "video")
    assert job.params.get("population_constraint") is True


# ============ 视频 handler → prompt ============
def test_b_w4_video_handler_appends_clause(pop_env):
    """视频 handler：params.population_constraint=True + 镜头有角色 →
    视频 prompt tail 含确定性人数子句（E2 语义）。"""
    rt, q, agnes, tmp_path = pop_env
    _save_proj(tmp_path, [_shot("s01_c01")])
    _mark_ff_ready(tmp_path, "s01_c01")

    async def emit(*a, **k):
        pass
    job = SimpleNamespace(project=PROJECT, shot_id="s01_c01",
                          params={"population_constraint": True}, kind="video")
    asyncio.run(_handler_video(job, emit))
    assert agnes.video_prompts, "video 未提交"
    assert POP_MARK in agnes.video_prompts[0]
    assert "exactly 2 people" in agnes.video_prompts[0]


def test_b_w5_video_handler_no_chars_no_clause(pop_env):
    """空船镜头（characters=[]）→ 无子句（不强迫人数，也不误伤）。"""
    rt, q, agnes, tmp_path = pop_env
    _save_proj(tmp_path, [_shot("s01_c01", chars=())])
    _mark_ff_ready(tmp_path, "s01_c01")

    async def emit(*a, **k):
        pass
    job = SimpleNamespace(project=PROJECT, shot_id="s01_c01",
                          params={"population_constraint": True}, kind="video")
    asyncio.run(_handler_video(job, emit))
    assert agnes.video_prompts, "video 未提交"
    assert POP_MARK not in agnes.video_prompts[0]


def test_b_w6_video_handler_opt_out(pop_env):
    rt, q, agnes, tmp_path = pop_env
    _save_proj(tmp_path, [_shot("s01_c01")])
    _mark_ff_ready(tmp_path, "s01_c01")

    async def emit(*a, **k):
        pass
    job = SimpleNamespace(project=PROJECT, shot_id="s01_c01",
                          params={"population_constraint": False}, kind="video")
    asyncio.run(_handler_video(job, emit))
    assert agnes.video_prompts, "video 未提交"
    assert POP_MARK not in agnes.video_prompts[0]


# ============ 纯函数 ============
def test_b_w7_apply_population_clause_unit():
    shot = Shot(shot_id="s01_c01", scene_id="s01", index=0,
                characters=["船长", "大副"])
    p = apply_population_clause("base prompt", shot, enabled=True)
    assert p.startswith("base prompt") and POP_MARK in p and "exactly 2 people" in p
    assert apply_population_clause("base prompt", shot, enabled=False) == "base prompt"
    empty = Shot(shot_id="s01_c01", scene_id="s01", index=0, characters=[])
    assert apply_population_clause("base prompt", empty, enabled=True) == "base prompt"
    # 确定性：同 shot 两次调用结果一致
    assert apply_population_clause("x", shot, enabled=True) == \
        apply_population_clause("x", shot, enabled=True)
    # 原 _population_constraint_clause 语义未被包装改变
    assert _population_constraint_clause(empty) == ""
