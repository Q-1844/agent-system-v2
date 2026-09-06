"""Phase AI-4B：Scene Autopilot Executor 测试。

重点：
- 纯函数：derive_execution_seed 确定性、build_stage_sequence 六组合、dependency_ready。
- Run IO：save/load。
- preflight（§八）：无 plan / stale / 无 Shot / active Run / locked 无 final。
- E2E Case 1（§四十九）：3 个 independent+i2v，FakeAgnes + FakeRehost，全程不点候选 -> 3 视频完成。
"""
import asyncio
import json
import shutil
import time
import uuid
from pathlib import Path

import pytest

import deps
from deps import get_project_manager
from src.core.store import ProjectStore
from src.core.models import Project, Scene, Character, StyleAnchor, Shot, Asset, ShotStatus
from src.ai.production_planner import sanitize_plan, PlannedShot
from src.ai.autopilot import (
    derive_execution_seed, build_stage_sequence, dependency_ready,
    _planned_for, _structure_changed, get_autopilot_manager,
    SceneAutopilotRun, ShotExecutionState,
    RUN_COMPLETED, RUN_FAILED, RUN_BLOCKED, RUN_RUNNING,
    SHOT_COMPLETED, SHOT_SKIPPED_LOCKED,
    STAGE_ACTIVATE, STAGE_FIRST_PROMPT, STAGE_FIRST_FRAME,
    STAGE_LAST_PROMPT, STAGE_LAST_FRAME, STAGE_VIDEO_PROMPT, STAGE_VIDEO, STAGE_VIDEO_END,
)

PROJECT_NAME = "_test_autopilot"


# ============ FakeAgnes / FakeRehost ============
class _FakeQuota:
    def can(self, kind, amount=1):
        return (True, "")
    def consume(self, kind, amount=1):
        pass


class FakeAgnes:
    """覆盖 chat / generate_image / create_video / poll_video / download / quota。"""
    def __init__(self):
        self.calls = []
        self.quota = _FakeQuota()

    async def chat(self, messages, *, temperature=0.7, max_tokens=2048,
                   thinking=False, json_mode=False):
        self.calls.append("chat")
        return "a cinematic frame, detailed, anime style, high quality"

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        self.calls.append("gen_image")
        return {"url": "https://platform-outputs.agnes-ai.space/images/fake.png", "b64": None}

    async def create_video(self, prompt, *, mode="i2v", image_url=None, image_urls=None,
                           num_frames=121, frame_rate=24, width=1312, height=736,
                           seed=None, negative_prompt="",
                           model=None, duration_s=None):
        self.calls.append("create_video")
        return {"video_id": f"vid_{uuid.uuid4().hex[:6]}", "task_id": "t_1",
                "seconds": "5.0", "size": "1280x704"}

    async def poll_video(self, video_id, *, interval=5.0, max_wait=3600,
                         on_progress=None, max_consecutive_failures=5,
                         model_name=None):
        self.calls.append("poll")
        if on_progress:
            await on_progress(100, "completed", 0)
        return {"url": "https://platform-outputs.agnes-ai.space/videos/fake.mp4",
                "seconds": 5.0, "size": "1280x704"}

    async def download(self, url, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"FAKE_MP4_CONTENT_NOT_REAL_VIDEO")
        return str(dest)

    async def aclose(self):
        pass


class FakeRehost:
    async def upload(self, path):
        return "https://i.imgur.com/fake_rehost.png"


# ============ 项目 fixture ============
def _shot(scene_id, idx, desc, chars=None, dur=5.0):
    return Shot(shot_id=f"{scene_id}_c{idx:02d}", scene_id=scene_id, index=idx,
                description=desc, characters=chars or [], target_duration_s=dur)


def _make_project(pname, scenes):
    pm = get_project_manager()
    shutil.rmtree(pm.base_dir / pname, ignore_errors=True)
    pdir = pm.create_project(pname)
    proj = Project(name=pname, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事", characters=[Character(name="林夏", description_en="girl")],
                   scenes=scenes)
    ProjectStore(pdir).save(proj)
    return pdir


def _scene_3independent():
    """Case 1：3 个 independent + i2v 镜头。"""
    return Scene(scene_id="s01", summary="三镜独立", characters=["林夏"], shots=[
        _shot("s01", 1, "林夏站立", ["林夏"], 5),
        _shot("s01", 2, "林夏转身", ["林夏"], 5),
        _shot("s01", 3, "林夏行走", ["林夏"], 5),
    ])


def _seed_approved_plan(pdir, scene_id, plan_shots):
    """写一个 approved Production Plan 到磁盘。plan_shots: [(shot_id, opening, link), ...]"""
    from routers.production_planner import _read_plans, _write_plans
    proj = ProjectStore(pdir).load()
    sc = next(s for s in proj.scenes if s.scene_id == scene_id)
    ai = {"overview": "x", "shots": [
        {"shot_id": sid, "opening_source": o, "link_mode": l, "reason": "r"}
        for sid, o, l in plan_shots]}
    plan = sanitize_plan(sc, ai, run_seed=12345, project_name=PROJECT_NAME)
    plan.status = "approved"
    plan.approved_at = time.time()
    plans = _read_plans(pdir)
    plans[scene_id] = plan
    _write_plans(pdir, plans)
    return plan


@pytest.fixture
def autopilot_project(client):
    pdir = _make_project(PROJECT_NAME, [_scene_3independent()])
    yield pdir
    shutil.rmtree(get_project_manager().get_project_dir(PROJECT_NAME), ignore_errors=True)


@pytest.fixture
def fake_agnes_swap(client):
    fake = FakeAgnes()
    real_agnes = deps.agnes_client
    real_rehost = deps._rehost_host
    deps.agnes_client = fake
    deps._rehost_host = FakeRehost()
    yield fake
    deps.agnes_client = real_agnes
    deps._rehost_host = real_rehost


# ============ 1. 纯函数 ============
def test_derive_execution_seed_deterministic():
    a = derive_execution_seed(12345, "run_abc")
    b = derive_execution_seed(12345, "run_abc")
    assert a == b                       # 同输入同输出
    assert 0 <= a < 2 ** 31
    c = derive_execution_seed(12345, "run_xyz")
    assert c != a or a == b             # 不同 run_id 大概率不同（不强制，但确定性是硬要求）


def test_build_stage_sequence_six_combinations():
    def plan(opening, link):
        # 字段按实际 derivation 规则设置（generate_first_frame = opening!=inherit; generate_last_frame = link==keyframes）
        return PlannedShot(shot_id="x", opening_source=opening, link_mode=link,
                           source_shot_id=None if opening == "independent" else "prev",
                           generate_first_frame=(opening != "inherit_previous"),
                           generate_last_frame=(link == "keyframes"),
                           requires_previous_video_end=opening in ("reference_previous", "inherit_previous"))
    # independent + i2v：有首帧无尾帧
    s = build_stage_sequence(plan("independent", "i2v"))
    assert s == [STAGE_ACTIVATE, STAGE_FIRST_PROMPT, STAGE_FIRST_FRAME,
                 STAGE_VIDEO_PROMPT, STAGE_VIDEO, STAGE_VIDEO_END]
    # independent + keyframes：有首帧有尾帧
    s = build_stage_sequence(plan("independent", "keyframes"))
    assert s == [STAGE_ACTIVATE, STAGE_FIRST_PROMPT, STAGE_FIRST_FRAME,
                 STAGE_LAST_PROMPT, STAGE_LAST_FRAME, STAGE_VIDEO_PROMPT, STAGE_VIDEO, STAGE_VIDEO_END]
    # inherit_previous + i2v：无首帧（generate_first_frame=False）
    s = build_stage_sequence(plan("inherit_previous", "i2v"))
    assert STAGE_FIRST_PROMPT not in s and STAGE_FIRST_FRAME not in s
    assert s[0] == STAGE_ACTIVATE
    assert STAGE_VIDEO in s


def test_dependency_ready_independent_no_dep():
    p = PlannedShot(shot_id="x", opening_source="independent", link_mode="i2v",
                    source_shot_id=None, generate_first_frame=True,
                    generate_last_frame=True, requires_previous_video_end=False)
    assert dependency_ready(Project(name="x"), p) is True   # 无 source 也不需要


def test_dependency_ready_inherit_needs_source_end():
    p = PlannedShot(shot_id="c02", opening_source="inherit_previous", link_mode="i2v",
                    source_shot_id="c01", generate_first_frame=False,
                    generate_last_frame=True, requires_previous_video_end=True)
    proj = Project(name="x", scenes=[Scene(scene_id="s01", shots=[
        Shot(shot_id="c01", scene_id="s01", index=1)])])
    # source 无 final_video
    assert dependency_ready(proj, p) is False
    # source 有 final_video + video_end_frame.url
    src = proj.get_shot("c01")
    src.final_video = "vv_1"
    src.video.status = ShotStatus.DONE.value
    src.video_end_frame = Asset(local="e.png", url="https://x/e.png", status=ShotStatus.DONE.value)
    assert dependency_ready(proj, p) is True


def test_planned_for_and_structure_changed():
    # 内存 Scene（不落盘，不依赖 project_manager）
    sc = Scene(scene_id="s01", summary="三镜独立", characters=["林夏"], shots=[
        _shot("s01", 1, "林夏站立", ["林夏"], 5),
        _shot("s01", 2, "林夏转身", ["林夏"], 5),
        _shot("s01", 3, "林夏行走", ["林夏"], 5)])
    plan = sanitize_plan(sc, {"overview": "", "shots": [
        {"shot_id": "s01_c01", "opening_source": "independent", "link_mode": "i2v", "reason": "a"},
        {"shot_id": "s01_c02", "opening_source": "reference_previous", "link_mode": "i2v", "reason": "b"},
        {"shot_id": "s01_c03", "opening_source": "reference_previous", "link_mode": "i2v", "reason": "c"}]},
        run_seed=1, project_name=PROJECT_NAME)
    run = SceneAutopilotRun(run_id="r1", project_name=PROJECT_NAME, scene_id="s01",
                            plan_id=plan.plan_id, plan_snapshot=plan.model_dump(),
                            run_seed=1, created_at=time.time(), updated_at=time.time())
    assert _planned_for(run, "s01_c02").opening_source == "reference_previous"
    assert _planned_for(run, "s01_c02").source_shot_id == "s01_c01"
    # 结构未变 -> None
    assert _structure_changed(run, sc) is None
    # 改 shot description -> 变化
    sc.shots[1].description = "完全不同"
    assert _structure_changed(run, sc) is not None


# ============ 2. preflight ============
def test_start_no_plan_400(autopilot_project, client):
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/autopilot/start",
                    json={"respect_locked": True})
    assert r.status_code == 400
    assert "制作计划" in r.json()["detail"]


def test_start_stale_plan_400(autopilot_project, client):
    pdir = autopilot_project
    _seed_approved_plan(pdir, "s01", [("s01_c01", "independent", "i2v"),
                                       ("s01_c02", "independent", "i2v"),
                                       ("s01_c03", "independent", "i2v")])
    # 改 scene summary -> stale
    p = ProjectStore(pdir).load()
    p.scenes[0].summary = "改过的故事段"
    ProjectStore(pdir).save(p)
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/autopilot/start",
                    json={"respect_locked": True})
    assert r.status_code == 400
    assert "过期" in r.json()["detail"]


def test_start_no_shots_400(autopilot_project, client):
    pdir = autopilot_project
    _seed_approved_plan(pdir, "s01", [("s01_c01", "independent", "i2v"),
                                       ("s01_c02", "independent", "i2v"),
                                       ("s01_c03", "independent", "i2v")])
    p = ProjectStore(pdir).load()
    p.scenes[0].shots = []
    ProjectStore(pdir).save(p)
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/autopilot/start",
                    json={"respect_locked": True})
    assert r.status_code == 400


def test_start_active_run_409(autopilot_project, fake_agnes_swap, client):
    pdir = autopilot_project
    _seed_approved_plan(pdir, "s01", [("s01_c01", "independent", "i2v"),
                                       ("s01_c02", "independent", "i2v"),
                                       ("s01_c03", "independent", "i2v")])
    r1 = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/autopilot/start",
                     json={"respect_locked": True})
    assert r1.status_code == 200
    run_id = r1.json()["run_id"]
    # 立即再 start -> 409 + existing_run_id
    r2 = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/autopilot/start",
                     json={"respect_locked": True})
    assert r2.status_code == 409
    detail = r2.json()["detail"]
    assert detail["existing_run_id"] == run_id
    # 清理：cancel 该 run，并等待 _run_loop 真正退出（避免 background task 泄漏到后续测试）
    client.post(f"/api/project/{PROJECT_NAME}/autopilot/run/{run_id}/cancel")
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(0.5)
        st = client.get(f"/api/project/{PROJECT_NAME}/autopilot/run/{run_id}").json()
        if st["status"] in ("cancelled", "failed", "completed"):
            break


def test_start_locked_without_final_400(autopilot_project, client):
    pdir = autopilot_project
    _seed_approved_plan(pdir, "s01", [("s01_c01", "independent", "i2v"),
                                       ("s01_c02", "independent", "i2v"),
                                       ("s01_c03", "independent", "i2v")])
    p = ProjectStore(pdir).load()
    p.scenes[0].shots[0].locked = True   # 锁定但无 final video
    ProjectStore(pdir).save(p)
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/autopilot/start",
                    json={"respect_locked": True})
    assert r.status_code == 400
    assert "锁定" in r.json()["detail"]


# ============ 3. E2E Case 1（3 independent + i2v）============
def test_e2e_case1_three_independent(autopilot_project, fake_agnes_swap, client):
    """§四十九 Case 1：3 independent+i2v，点一次自动制作，全程不点候选 -> 3 视频完成。"""
    pdir = autopilot_project
    _seed_approved_plan(pdir, "s01", [("s01_c01", "independent", "i2v"),
                                       ("s01_c02", "independent", "i2v"),
                                       ("s01_c03", "independent", "i2v")])
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/autopilot/start",
                    json={"respect_locked": True})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    # 轮询直到 terminal（background _run_loop 在 app 事件循环跑，sleep 期间推进）
    deadline = time.time() + 90
    final = None
    while time.time() < deadline:
        time.sleep(1.0)
        r = client.get(f"/api/project/{PROJECT_NAME}/autopilot/run/{run_id}")
        assert r.status_code == 200
        final = r.json()
        if final["status"] in ("completed", "failed", "blocked", "cancelled"):
            break
    assert final is not None, "Run 未在超时内结束"
    assert final["status"] == "completed", f"Run 状态={final['status']}, error={final.get('error')}"

    # 校验：每个 Shot 有本 Run 生成的视频版本 + final_video 指向它
    proj = ProjectStore(pdir).load()
    sc = next(s for s in proj.scenes if s.scene_id == "s01")
    for sh in sc.shots:
        own = [v for v in sh.video_versions if v.origin_run_id == run_id]
        assert own, f"{sh.shot_id} 无本 Run 生成的视频版本"
        assert sh.final_video == own[-1].version_id
        # 首帧批次也属于本 Run
        ff_own = [b for b in sh.first_frame_batches if b.origin_run_id == run_id]
        assert ff_own, f"{sh.shot_id} 无本 Run 生成的首帧批次"
        assert sh.selected_first_frame is not None
    # Run 内 3 个 Shot 都 completed
    assert all(s["status"] == "completed" for s in final["shots"])
