"""Phase AI-5A：Prepared Project Autopilot 测试（§七十一 Case 1-7）。

重点：
- 纯函数：build_project_source_signature、source 变化检测。
- Preflight（§十七）：缺 plan / 缺 shot。
- E2E Case 1：2 Scene 全 approved -> 多 SceneRun completed -> manifest -> final.mp4。
- Case 3：pause/resume 同 child 继续，不重新运行已完成 Scene。
- Case 4：crash-safe 不重复创建 child（find_by_parent_project_run）。
- Case 5：compose failure -> failed(stage=compose) -> resume 只重试 compose。
- Case 6：Run consistency（child completed 后改 shot.final_video，manifest 仍用 child 版本）。
- Case 7：source 变化 -> blocked(SOURCE_STRUCTURE_CHANGED)。

FakeAgnes.download 生成真小 mp4（ffmpeg lavfi），让 ffmpeg compose/末帧提取真能跑。
"""
import asyncio
import json
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pytest

import deps
from deps import get_project_manager
from src.core.store import ProjectStore
from src.core.models import Project, Scene, Character, StyleAnchor, Shot, Asset, ShotStatus, VideoVersion
from src.ai.production_planner import sanitize_plan
from src.ai.autopilot import get_autopilot_manager, RUN_COMPLETED
from src.ai.project_autopilot import (
    get_project_autopilot_manager, build_project_source_signature,
    preflight_project, ProjectProductionManager,
    PRUN_COMPLETED, PRUN_FAILED, PRUN_BLOCKED, PRUN_PAUSED, PRUN_CANCELLED,
    ERR_SOURCE_CHANGED, ERR_COMPOSE_FAILED,
)

PROJECT_NAME = "_test_project_autopilot"


# ============ FakeAgnes（真小 mp4）/ FakeRehost ============
_FAKE_MP4_BYTES = None


def _fake_mp4_bytes() -> bytes:
    """生成一个真 1s mp4（ffmpeg lavfi gray），缓存字节。compose / 末帧提取需要真视频。"""
    global _FAKE_MP4_BYTES
    if _FAKE_MP4_BYTES is None:
        f = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        f.close()
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x240:d=1",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", f.name],
            capture_output=True)
        _FAKE_MP4_BYTES = Path(f.name).read_bytes()
        try:
            Path(f.name).unlink()
        except Exception:
            pass
    return _FAKE_MP4_BYTES


class _FakeQuota:
    def can(self, kind, amount=1):
        return (True, "")
    def consume(self, kind, amount=1):
        pass


class FakeAgnes:
    """覆盖 chat / generate_image / create_video / poll_video / download / quota。
    download 写真小 mp4（.mp4）/ 假 png（其它），让 ffmpeg 流程可跑。"""
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
                "seconds": "1.0", "size": "320x240"}

    async def poll_video(self, video_id, *, interval=5.0, max_wait=3600,
                         on_progress=None, max_consecutive_failures=5,
                         model_name=None):
        self.calls.append("poll")
        if on_progress:
            await on_progress(100, "completed", 0)
        return {"url": "https://platform-outputs.agnes-ai.space/videos/fake.mp4",
                "seconds": 1.0, "size": "320x240"}

    async def download(self, url, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        if str(dest).endswith(".mp4"):
            Path(dest).write_bytes(_fake_mp4_bytes())
        else:
            Path(dest).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)   # 假 png
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
                   story_text="完整故事", characters=[Character(name="林夏", description_en="girl")],
                   scenes=scenes)
    ProjectStore(pdir).save(proj)
    return pdir


def _scene_one_shot(scene_id, summary):
    """1 个 independent+i2v 镜头的故事段。"""
    return Scene(scene_id=scene_id, summary=summary, characters=["林夏"],
                 shots=[_shot(scene_id, 1, "林夏动作", ["林夏"], 5)])


def _seed_approved_plan(pdir, scene_id, plan_shots):
    """写一个 approved Production Plan。plan_shots: [(shot_id, opening, link), ...]"""
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


def _seed_all_plans(pdir, scenes):
    for sc in scenes:
        _seed_approved_plan(pdir, sc.scene_id,
                            [(sh.shot_id, "independent", "i2v") for sh in sc.shots])


@pytest.fixture
def project_2scenes(client):
    """2 Scene（各 1 independent+i2v 镜头），全 approved plan。"""
    scenes = [_scene_one_shot("s01", "故事段一"), _scene_one_shot("s02", "故事段二")]
    pdir = _make_project(PROJECT_NAME, scenes)
    _seed_all_plans(pdir, scenes)
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


def _wait_run_terminal(client, run_id, timeout=120):
    """轮询 ProjectRun 直到 terminal。返回最终 run dict。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = client.get(f"/api/project/{PROJECT_NAME}/production/run/{run_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] in ("completed", "failed", "blocked", "cancelled"):
            return last
        time.sleep(1.0)
    return last


# ============ 1. 纯函数 ============
def test_build_source_signature_excludes_execution_state():
    """signature 不含 prompt/批次/video version/opening_source/link_mode 等执行态（§十四）。"""
    sc = Scene(scene_id="s01", summary="x", characters=["林夏"], shots=[
        _shot("s01", 1, "林夏站立", ["林夏"], 5)])
    proj = Project(name="p", style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事", characters=[Character(name="林夏", description_en="g")],
                   scenes=[sc])
    sig = build_project_source_signature(proj)
    # 不含执行态字段
    sig_str = json.dumps(sig, ensure_ascii=False, sort_keys=True, default=str)
    assert "prompt_en" not in sig_str
    assert "video_versions" not in sig_str
    assert "first_frame_batches" not in sig_str
    assert "opening_source" not in sig_str
    assert "link_mode" not in sig_str
    # 含创作字段
    assert sig["story_text"] == "故事"
    assert sig["scenes"][0]["shots"][0]["description"] == "林夏站立"
    assert sig["scenes"][0]["shots"][0]["target_duration_s"] == 5


def test_source_signature_detects_changes():
    """story/scene summary/shot desc/character views 变化 -> signature 变（§十三/§十五）。"""
    sc = Scene(scene_id="s01", summary="x", characters=["林夏"], shots=[
        _shot("s01", 1, "林夏站立", ["林夏"], 5)])
    proj = Project(name="p", style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事", characters=[Character(name="林夏", description_en="g")],
                   scenes=[sc])
    sig1 = build_project_source_signature(proj)
    # 执行态变化 -> signature 不变（autopilot 自己改的不算 stale）
    sc.shots[0].prompt_en = "changed prompt"
    sc.shots[0].opening_source = "inherit_previous"
    sc.shots[0].link_mode = "keyframes"
    assert build_project_source_signature(proj) == sig1
    # 创作态变化 -> signature 变
    sc.shots[0].description = "林夏坐下"
    assert build_project_source_signature(proj) != sig1
    # character views 变化 -> signature 变（§十五）
    proj2 = Project(name="p", style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                    story_text="故事",
                    characters=[Character(name="林夏", description_en="g",
                                          views={"front": Asset(url="https://x/a.png", status="done")})],
                    scenes=[Scene(scene_id="s01", summary="x", characters=["林夏"],
                                  shots=[_shot("s01", 1, "林夏站立", ["林夏"], 5)])])
    assert build_project_source_signature(proj2) != sig1


# ============ 2. Preflight ============
def test_preflight_missing_plan_not_ready(client):
    pdir = _make_project(PROJECT_NAME + "_pf", [
        _scene_one_shot("s01", "段一"), _scene_one_shot("s02", "段二")])
    _seed_approved_plan(pdir, "s01", [("s01_c01", "independent", "i2v")])   # s02 无 plan
    try:
        r = client.get(f"/api/project/{PROJECT_NAME}_pf/production/preflight")
        assert r.status_code == 200
        pre = r.json()
        assert pre["ready"] is False
        s02 = next(s for s in pre["scenes"] if s["scene_id"] == "s02")
        assert "NO_PRODUCTION_PLAN" in s02["issues"]
    finally:
        shutil.rmtree(get_project_manager().get_project_dir(PROJECT_NAME + "_pf"),
                      ignore_errors=True)


def test_start_missing_plan_400(project_2scenes, client):
    """Case 2：删 s02 的 plan -> start 400，不创建 Run。"""
    pdir = project_2scenes
    from routers.production_planner import _read_plans, _write_plans
    plans = _read_plans(pdir)
    del plans["s02"]
    _write_plans(pdir, plans)
    r = client.post(f"/api/project/{PROJECT_NAME}/production/start", json={"respect_locked": True})
    assert r.status_code == 400
    assert "未准备完成" in r.json()["detail"]


# ============ Case 1：E2E 完整 prepared project ============
def test_case1_e2e_complete_project(project_2scenes, fake_agnes_swap, client):
    """§七十一 Case 1：2 Scene 全 approved -> 多 SceneRun completed -> manifest -> final.mp4。"""
    r = client.post(f"/api/project/{PROJECT_NAME}/production/start", json={"respect_locked": True})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    final = _wait_run_terminal(client, run_id, timeout=120)
    assert final is not None, "Run 未在超时内结束"
    assert final["status"] == "completed", f"status={final['status']}, error={final.get('error')}"

    # 校验：每个 Scene 有 child Run + final.mp4 存在 + manifest 有 2 clips
    proj_mgr = get_project_autopilot_manager()
    run = proj_mgr.get_run(PROJECT_NAME, run_id)
    scene_mgr = get_autopilot_manager()
    assert run.final_output and Path(run.final_output).exists(), f"final.mp4 不存在: {run.final_output}"
    assert run.composition_manifest_file and Path(run.composition_manifest_file).exists()

    proj = ProjectStore(get_project_manager().get_project_dir(PROJECT_NAME)).load()
    for entry in run.scenes:
        assert entry.status == "completed", f"{entry.scene_id} 未完成"
        assert entry.child_run_id
        child = scene_mgr.get_run(PROJECT_NAME, entry.child_run_id)
        assert child is not None and child.parent_project_run_id == run_id
        # child 的每个 shot 有本 child 生成的视频版本
        sc = next(s for s in proj.scenes if s.scene_id == entry.scene_id)
        for sh in sc.shots:
            own = [v for v in sh.video_versions if v.origin_run_id == entry.child_run_id]
            assert own, f"{sh.shot_id} 无 child 生成的视频版本"
            state = next(s for s in child.shots if s.shot_id == sh.shot_id)
            assert state.video_version_id == own[-1].version_id

    # manifest 2 clips
    manifest = json.loads(Path(run.composition_manifest_file).read_text(encoding="utf-8"))
    assert len(manifest["clips"]) == 2
    # final.mp4 非空
    assert Path(run.final_output).stat().st_size > 0


def test_active_project_run_409(project_2scenes, fake_agnes_swap, client):
    """§二十：active ProjectRun 已存在 -> 409 + existing_run_id。"""
    r1 = client.post(f"/api/project/{PROJECT_NAME}/production/start", json={"respect_locked": True})
    assert r1.status_code == 200
    run_id = r1.json()["run_id"]
    # 立即再 start -> 409
    r2 = client.post(f"/api/project/{PROJECT_NAME}/production/start", json={"respect_locked": True})
    assert r2.status_code == 409
    assert r2.json()["detail"]["existing_run_id"] == run_id
    # 清理：cancel 并等待退出
    client.post(f"/api/project/{PROJECT_NAME}/production/run/{run_id}/cancel")
    _wait_run_terminal(client, run_id, timeout=60)


# ============ Case 3：pause / resume ============
def test_case3_pause_resume(project_2scenes, fake_agnes_swap, client):
    """§七十一 Case 3：暂停 -> child paused -> Project paused -> resume 同 child 继续。"""
    r = client.post(f"/api/project/{PROJECT_NAME}/production/start", json={"respect_locked": True})
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    # 等待 scene 1 child 创建
    deadline = time.time() + 30
    child_id_s1 = None
    while time.time() < deadline:
        run = client.get(f"/api/project/{PROJECT_NAME}/production/run/{run_id}").json()
        if run["scenes"][0]["child_run_id"]:
            child_id_s1 = run["scenes"][0]["child_run_id"]
            break
        time.sleep(0.5)
    assert child_id_s1, "scene 1 child 未创建"

    # 暂停
    client.post(f"/api/project/{PROJECT_NAME}/production/run/{run_id}/pause")
    # 等待 paused（child 当前 job 完成后停）
    deadline = time.time() + 90
    while time.time() < deadline:
        run = client.get(f"/api/project/{PROJECT_NAME}/production/run/{run_id}").json()
        if run["status"] == "paused":
            break
        time.sleep(1.0)
    assert run["status"] == "paused", f"pause 后 status={run['status']}"

    # resume
    client.post(f"/api/project/{PROJECT_NAME}/production/run/{run_id}/resume")
    final = _wait_run_terminal(client, run_id, timeout=120)
    assert final["status"] == "completed", f"resume 后 status={final['status']}, error={final.get('error')}"

    # 校验：scene 1 child 仍是同一个（不重新创建）
    run = client.get(f"/api/project/{PROJECT_NAME}/production/run/{run_id}").json()
    assert run["scenes"][0]["child_run_id"] == child_id_s1


# ============ Case 4：crash-safe 不重复创建 child ============
def test_case4_no_duplicate_child(project_2scenes, fake_agnes_swap, client):
    """§七十一 Case 4 / §七：crash 后恢复不创建第二套 child（find_by_parent_project_run 复用）。
    直接测不变量：_start_or_reuse_child 对已有 child 复用而非新建。"""
    r = client.post(f"/api/project/{PROJECT_NAME}/production/start", json={"respect_locked": True})
    run_id = r.json()["run_id"]
    # 等 scene 1 child 创建
    deadline = time.time() + 30
    while time.time() < deadline:
        run = client.get(f"/api/project/{PROJECT_NAME}/production/run/{run_id}").json()
        if run["scenes"][0]["child_run_id"]:
            break
        time.sleep(0.5)
    scene_mgr = get_autopilot_manager()
    children_before = scene_mgr.list_runs_for_scene(PROJECT_NAME, "s01")
    assert len(children_before) == 1

    # 模拟 crash 后再次 _start_or_reuse_child（Project 恢复时会调）
    proj_mgr = get_project_autopilot_manager()
    run_obj = proj_mgr.get_run(PROJECT_NAME, run_id)
    entry = run_obj.scenes[0]
    # 若 child 已 terminal（completed），_start_or_reuse_child 直接复用；若 active 也复用
    child2 = asyncio.run(
        proj_mgr._start_or_reuse_child(PROJECT_NAME, run_obj, entry))
    assert child2.run_id == children_before[0].run_id, "应复用已有 child，不新建"
    children_after = scene_mgr.list_runs_for_scene(PROJECT_NAME, "s01")
    assert len(children_after) == 1, "不应创建第二个 child"
    # 清理
    client.post(f"/api/project/{PROJECT_NAME}/production/run/{run_id}/cancel")
    _wait_run_terminal(client, run_id, timeout=120)


def test_case4_recover_rebinds_child(project_2scenes, fake_agnes_swap, client):
    """§七/§三十一：crash 后 parent 未保存 child_run_id -> 恢复时 find_by_parent_project_run
    重新绑定已有 child，不创建第二个。通过 /resume endpoint 在 app 事件循环内重新调度。"""
    proj_mgr = get_project_autopilot_manager()
    scene_mgr = get_autopilot_manager()
    r = client.post(f"/api/project/{PROJECT_NAME}/production/start", json={"respect_locked": True})
    run_id = r.json()["run_id"]
    # 等 scene 1 child 创建
    deadline = time.time() + 30
    child_id_s1 = None
    while time.time() < deadline:
        run = client.get(f"/api/project/{PROJECT_NAME}/production/run/{run_id}").json()
        if run["scenes"][0]["child_run_id"]:
            child_id_s1 = run["scenes"][0]["child_run_id"]
            break
        time.sleep(0.5)
    assert child_id_s1, "scene 1 child 未创建"
    # 模拟 crash：cancel project _run_loop task（child 继续跑）
    proj_task = proj_mgr._run_tasks.get(run_id)
    if proj_task:
        proj_task.cancel()
    time.sleep(1.0)
    # 模拟 crash 窗口（§七）：parent 未保存 child_run_id，状态置为可 resume
    from src.core.store import _atomic_write_json
    run_obj = proj_mgr.get_run(PROJECT_NAME, run_id)
    run_obj.scenes[0].child_run_id = None
    run_obj.scenes[0].status = "pending"
    run_obj.scenes[0].error = None
    run_obj.status = PRUN_PAUSED   # resumable
    run_obj.current_child_run_id = None
    run_obj.pause_requested = False
    run_obj.cancel_requested = False
    _atomic_write_json(proj_mgr._run_path(PROJECT_NAME, run_id), run_obj.model_dump())
    # resume -> 新 _run_loop 在 app loop 内启动，find_by_parent 重新绑定 child
    rr = client.post(f"/api/project/{PROJECT_NAME}/production/run/{run_id}/resume")
    assert rr.status_code == 200, rr.text
    final = _wait_run_terminal(client, run_id, timeout=120)
    assert final["status"] == "completed", f"status={final['status']}, error={final.get('error')}"
    # scene 1 重新绑定到原 child（不新建）
    assert final["scenes"][0]["child_run_id"] == child_id_s1
    # 每个 Scene 只有 1 个 child（无重复）
    for sid in ("s01", "s02"):
        children = scene_mgr.list_runs_for_scene(PROJECT_NAME, sid)
        assert len(children) == 1, f"{sid} 有 {len(children)} 个 child（应只有 1）"


# ============ Case 5：compose failure -> retry only compose ============
def test_case5_compose_failure_retry(project_2scenes, fake_agnes_swap, client):
    """§七十一 Case 5 / §四十三：第一次 compose fail -> failed(stage=compose, COMPOSE_FAILED) ->
    resume 只重试 compose，不重新生成视频。"""
    calls = {"n": 0}
    orig = ProjectProductionManager._compose_sync

    def failing(self, project_name, run, manifest):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated compose failure")
        return orig(self, project_name, run, manifest)

    ProjectProductionManager._compose_sync = failing
    try:
        r = client.post(f"/api/project/{PROJECT_NAME}/production/start", json={"respect_locked": True})
        run_id = r.json()["run_id"]
        # 等待 failed（stage=compose）
        final = _wait_run_terminal(client, run_id, timeout=120)
        assert final["status"] == "failed", f"应 failed, got {final['status']}"
        assert final["stage"] == "compose"
        assert final["error_code"] == ERR_COMPOSE_FAILED
        # 所有 Scene 仍 completed（compose 失败不回滚 Scene）
        assert all(s["status"] == "completed" for s in final["scenes"])
        # manifest 已落盘（compose 失败前已保存，§四十三）
        run_obj = get_project_autopilot_manager().get_run(PROJECT_NAME, run_id)
        assert run_obj.composition_manifest_file and Path(run_obj.composition_manifest_file).exists()
        # resume -> 只重试 compose
        client.post(f"/api/project/{PROJECT_NAME}/production/run/{run_id}/resume")
        final2 = _wait_run_terminal(client, run_id, timeout=60)
        assert final2["status"] == "completed", f"retry 后 status={final2['status']}, error={final2.get('error')}"
        assert calls["n"] == 2, "应只重试 compose 一次"
    finally:
        ProjectProductionManager._compose_sync = orig


# ============ Case 6：Run consistency ============
def test_case6_run_consistency(project_2scenes, fake_agnes_swap, client):
    """§七十一 Case 6 / §三十六：child completed 后改 shot.final_video，
    manifest 重建仍用 child ShotExecutionState.video_version_id。"""
    r = client.post(f"/api/project/{PROJECT_NAME}/production/start", json={"respect_locked": True})
    run_id = r.json()["run_id"]
    final = _wait_run_terminal(client, run_id, timeout=120)
    assert final["status"] == "completed"

    proj_mgr = get_project_autopilot_manager()
    scene_mgr = get_autopilot_manager()
    run = proj_mgr.get_run(PROJECT_NAME, run_id)
    entry = run.scenes[0]
    child = scene_mgr.get_run(PROJECT_NAME, entry.child_run_id)
    shot_id = child.shots[0].shot_id
    v_child = child.shots[0].video_version_id   # child 记录的版本
    assert v_child

    # 手工把 shot.final_video 改到另一个（legacy）版本，模拟用户外部修改
    pdir = get_project_manager().get_project_dir(PROJECT_NAME)
    store = ProjectStore(pdir)
    proj = store.load()
    sh = proj.get_shot(shot_id)
    # 加一个 legacy 版本并把 final 指向它
    legacy = VideoVersion(version_id="legacy_v_test", created_at=time.time(),
                          status=ShotStatus.DONE.value, local=str(Path(pdir)/"output"/"videos"/"legacy.mp4"),
                          seconds_returned=1.0)
    Path(legacy.local).parent.mkdir(parents=True, exist_ok=True)
    Path(legacy.local).write_bytes(_fake_mp4_bytes())
    sh.video_versions.append(legacy)
    sh.final_video = legacy.version_id   # 改 final 指向 legacy
    store.save(proj)

    # 重建 manifest -> 应仍用 child 的 v_child，不是 legacy
    manifest = proj_mgr._build_composition_manifest(PROJECT_NAME, run)
    clip = next(c for c in manifest.clips if c.shot_id == shot_id)
    assert clip.video_version_id == v_child, "manifest 应使用 child 记录的版本，而非被修改的 final_video"
    assert clip.video_version_id != legacy.version_id


# ============ Case 7：source changed -> blocked ============
def test_case7_source_changed_blocked(project_2scenes, fake_agnes_swap, client,
                                      monkeypatch):
    """§七十一 Case 7 / §十三/§十五：进入下一 Scene 前检测 source signature 变化 -> blocked。

    确定化（Freeze Stabilization §7）：FakeAgnes 下 scene 1 在测试 0.5s 轮询间隔内
    即可跑完并越过 scene 2 校验点——原实现「等到 child_run_id 就改 source」与
    「scene 2 校验」之间存在无同步竞态（改动落在校验之后 → completed）。
    现用生产校验边界作同步 seam：第 1 次校验（scene 1）直接放行；第 2 次校验
    （scene 2 边界=契约保护窗）先 Event 通知测试、等测试完成 source 改动后放行，
    再执行**真实** _validate_source_for_scene——不 mock 校验器、不伪造 blocked，
    业务判断完全由生产代码做出。"""
    import threading
    from src.ai.project_autopilot import ProjectProductionManager

    entered = threading.Event()
    proceed = threading.Event()
    calls = {"n": 0}
    orig_validate = ProjectProductionManager._validate_source_for_scene

    def _synced_validate(self, project_name, run):
        calls["n"] += 1
        if calls["n"] >= 2:          # scene 2 边界 = 受保护执行窗
            entered.set()
            assert proceed.wait(timeout=30), "测试未在时限内完成 source 改动"
        return orig_validate(self, project_name, run)

    monkeypatch.setattr(ProjectProductionManager, "_validate_source_for_scene",
                        _synced_validate)

    r = client.post(f"/api/project/{PROJECT_NAME}/production/start", json={"respect_locked": True})
    run_id = r.json()["run_id"]
    # 等 scene 1 child 创建（scene 1 已启动）
    deadline = time.time() + 30
    while time.time() < deadline:
        run = client.get(f"/api/project/{PROJECT_NAME}/production/run/{run_id}").json()
        if run["scenes"][0]["child_run_id"]:
            break
        time.sleep(0.5)
    # 等生产代码抵达 scene 2 校验边界（受保护窗开始），随后改 source：
    # scene 2 的 shot description（创作态，signature 变）
    assert entered.wait(timeout=60), "生产未抵达 scene 2 校验边界"
    pdir = get_project_manager().get_project_dir(PROJECT_NAME)
    store = ProjectStore(pdir)
    proj = store.load()
    proj.scenes[1].shots[0].description = "完全不同的镜头描述"
    store.save(proj)
    proceed.set()                    # 放行 → 真实校验执行 → BLOCKED
    # 等待 blocked（scene 1 完成 -> 进 scene 2 前校验捕获）
    final = _wait_run_terminal(client, run_id, timeout=120)
    assert final["status"] == "blocked", f"应 blocked, got {final['status']}, error={final.get('error')}"
    assert final["error_code"] == ERR_SOURCE_CHANGED
    # scene 1 应已完成（改动在 scene 1 之后），scene 2 未启动
    assert final["scenes"][0]["status"] == "completed"
    assert final["scenes"][1]["status"] in ("pending", "blocked")
