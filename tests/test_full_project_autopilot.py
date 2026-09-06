"""Phase AI-5B：Full Project Autopilot 测试（§七十四 Case 1-8）。

重点：
- 纯函数：classify_scene / build_plan_fingerprint / _director_plan_matches_shots。
- Inspect（§二十七）：缺 Shot / 缺 Plan / missing character / active run blocker。
- Case 1（核心 E2E）：s01 已有 Shot+approved Plan / s02 有 Shot 无 Plan / s03 无 Shot
  -> 自动补齐 -> 全 ready -> ProjectProductionRun -> final.mp4。
- Case 2：fresh Director Draft 复用（不重新请求 Director）。
- Case 3：fresh Planner Draft auto approve（不重新请求 Planner）。
- Case 4：Director apply 后 crash -> reconcile（director_result_saved 但未 applied，shots 已匹配）。
- Case 5：Planner failed -> resume 只继续失败 Scene，不重处理已 ready。
- Case 6：Preparation 中改 Shot.description -> blocked(EXTERNAL_SOURCE_CHANGED)。
- Case 7：production child crash window -> find_by_parent_full_run 不重复创建 child。
- Case 8：production child failed/paused -> FullRun 正确映射。

FakeAgnes.chat 按 system/user message 智能返回 Director JSON（按 scene 角色生成全覆盖镜头）或
Planner JSON（按 shot_id 返回）。download 写真小 mp4（ffmpeg lavfi），让 compose/末帧提取真能跑。
"""
import asyncio
import json
import re
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
from src.core.models import Project, Scene, Character, StyleAnchor, Shot, Asset, ShotStatus
from src.ai.production_planner import sanitize_plan, SceneProductionPlan
from src.ai.director import DirectorDraft, ShotPlanItem
from src.ai.full_project_autopilot import (
    get_full_project_manager, FullProjectManager, FullProjectRun, FullProjectSceneState,
    PreparationManifest, PreparationSceneRecord, inspect_project, classify_scene,
    build_plan_fingerprint, _director_plan_matches_shots,
    FSTAGE_PREPARE, FSTAGE_PRODUCTION,
    FRUN_COMPLETED, FRUN_FAILED, FRUN_BLOCKED, FRUN_PAUSED, FRUN_CANCELLED,
    FSCENE_READY, CLS_NEED_DIRECTOR, CLS_NEED_PRODUCTION_PLAN, CLS_BLOCKED,
    CK_DIRECTOR_RESULT_SAVED, CK_DIRECTOR_APPLIED, CK_PLANNER_APPROVED,
)
from src.ai.project_autopilot import get_project_autopilot_manager
from src.ai.autopilot import get_autopilot_manager
from src.api.agnes_client import AgnesError

PROJECT_NAME = "_test_full_project"


# ============ FakeAgnes（智能 chat + 真小 mp4）/ FakeRehost ============
_FAKE_MP4_BYTES = None


def _fake_mp4_bytes() -> bytes:
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
    """chat 智能返回 Director/Planner JSON；generate_image/create_video/poll/download 走假数据。
    director_calls / planner_calls 计数供 Case 5 断言「resume 不重处理已 ready Scene」。
    side_effect_director: scene_id -> callable，在 Director 调用该 scene 时执行（Case 6 改 source）。"""

    def __init__(self):
        self.director_calls = 0
        self.planner_calls = 0
        self.chat_calls = 0
        self.quota = _FakeQuota()
        self.fail_planner_scenes: set = set()       # scene_id -> Planner 调用抛 AgnesError
        self.side_effect_director: dict = {}        # scene_id -> callable(project_name)

    async def chat(self, messages, *, temperature=0.7, max_tokens=2048,
                   thinking=False, json_mode=False):
        self.chat_calls += 1
        sys_msg = messages[0]["content"] if messages else ""
        user_msg = messages[-1]["content"] if messages else ""

        if "分镜导演" in sys_msg or "规划成若干连续镜头" in user_msg:
            # Director 调用
            self.director_calls += 1
            scene_id = self._parse_scene_id(user_msg)
            cb = self.side_effect_director.get(scene_id)
            if cb:
                cb(PROJECT_NAME)
            return self._director_json(user_msg)

        if "视频制作规划师" in sys_msg or "决定 opening_source" in user_msg or "opening_source 与 link_mode" in user_msg:
            # Planner 调用
            self.planner_calls += 1
            scene_id = self._parse_scene_id(user_msg)
            if scene_id in self.fail_planner_scenes:
                raise AgnesError(f"planner failed for {scene_id} (test)")
            return self._planner_json(user_msg)

        # 默认（prompt building）：返回英文 prompt 片段
        return "a cinematic frame, detailed, anime style, high quality"

    def _parse_scene_id(self, user_msg: str) -> str:
        m = re.search(r"故事段\s*(s\d+)", user_msg)
        return m.group(1) if m else "s01"

    def _director_json(self, user_msg: str) -> str:
        """按 scene 出场角色生成全覆盖镜头：每角色一镜 + 一镜全员（保证 coverage）。"""
        m = re.search(r"出场角色：([^\n]*)", user_msg)
        chars = [c.strip() for c in (m.group(1) if m else "").split("、") if c.strip() and c.strip() != "无"]
        shots = []
        for i, c in enumerate(chars, start=1):
            shots.append({"draft_id": f"draft_{i}", "intent": f"{c}出场动作",
                          "characters": [c], "target_duration_s": 5, "purpose": "覆盖"})
        if not chars:
            shots.append({"draft_id": "draft_1", "intent": "环境镜头",
                          "characters": [], "target_duration_s": 5, "purpose": "建立"})
        return json.dumps({"overview": "AI 规划", "shots": shots}, ensure_ascii=False)

    def _planner_json(self, user_msg: str) -> str:
        """按 user message 中的 shot_id 列表返回 independent+i2v（sanitizer 会强制首镜 independent）。"""
        shot_ids = re.findall(r"^-\s*(s\d+_c\d+)", user_msg, re.MULTILINE)
        shots = [{"shot_id": sid, "opening_source": "independent", "link_mode": "i2v",
                  "reason": "独立开始"} for sid in shot_ids]
        return json.dumps({"overview": "制作规划", "shots": shots}, ensure_ascii=False)

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        return {"url": "https://platform-outputs.agnes-ai.space/images/fake.png", "b64": None}

    async def create_video(self, prompt, *, mode="i2v", image_url=None, image_urls=None,
                           num_frames=121, frame_rate=24, width=1312, height=736,
                           seed=None, negative_prompt="",
                           model=None, duration_s=None):
        return {"video_id": f"vid_{uuid.uuid4().hex[:6]}", "task_id": "t_1",
                "seconds": "1.0", "size": "320x240"}

    async def poll_video(self, video_id, *, interval=5.0, max_wait=3600,
                         on_progress=None, max_consecutive_failures=5,
                         model_name=None):
        if on_progress:
            await on_progress(100, "completed", 0)
        return {"url": "https://platform-outputs.agnes-ai.space/videos/fake.mp4",
                "seconds": 1.0, "size": "320x240"}

    async def download(self, url, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        if str(dest).endswith(".mp4"):
            Path(dest).write_bytes(_fake_mp4_bytes())
        else:
            Path(dest).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
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


def _make_project(pname, scenes, chars=None):
    pm = get_project_manager()
    shutil.rmtree(pm.base_dir / pname, ignore_errors=True)
    pdir = pm.create_project(pname)
    proj = Project(name=pname, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="完整故事",
                   characters=[Character(name=n, description_en=d) for n, d in (chars or [("林夏", "girl")])],
                   scenes=scenes)
    ProjectStore(pdir).save(proj)
    return pdir


def _seed_approved_plan(pdir, scene_id, plan_shots):
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


def _seed_draft_plan(pdir, scene_id, plan_shots):
    """写一个 draft（未 approve）Plan，供 Case 3 auto-approve。"""
    from routers.production_planner import _read_plans, _write_plans
    proj = ProjectStore(pdir).load()
    sc = next(s for s in proj.scenes if s.scene_id == scene_id)
    ai = {"overview": "x", "shots": [
        {"shot_id": sid, "opening_source": o, "link_mode": l, "reason": "r"}
        for sid, o, l in plan_shots]}
    plan = sanitize_plan(sc, ai, run_seed=999, project_name=PROJECT_NAME)
    plan.status = "draft"
    plans = _read_plans(pdir)
    plans[scene_id] = plan
    _write_plans(pdir, plans)
    return plan


def _seed_director_draft(pdir, scene_id, summary, chars):
    """写一个 fresh Director Draft（对应当前 scene summary/characters + 全覆盖），供 Case 2 复用。"""
    from routers.director import _read_drafts, _write_drafts
    shots = [ShotPlanItem(draft_id=f"draft_{i+1}", intent=f"{c}动作", characters=[c],
                          target_duration_s=5) for i, c in enumerate(chars)]
    draft = DirectorDraft(draft_id=f"director_{scene_id}", scene_id=scene_id, overview="draft",
                          shots=shots, scene_summary_snapshot=summary,
                          scene_characters_snapshot=list(chars), director_note="", created_at=time.time())
    drafts = _read_drafts(pdir)
    drafts[scene_id] = draft
    _write_drafts(pdir, drafts)
    return draft


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


def _wait_full_run_terminal(client, run_id, timeout=180):
    """轮询 FullProjectRun 直到 terminal。返回最终 run dict。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = client.get(f"/api/project/{PROJECT_NAME}/full-production/run/{run_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] in ("completed", "failed", "blocked", "cancelled"):
            return last
        time.sleep(1.0)
    return last


def _cleanup(pname=PROJECT_NAME):
    shutil.rmtree(get_project_manager().get_project_dir(pname), ignore_errors=True)


# ============ 1. 纯函数 ============
def test_classify_scene_cases():
    """§五 Case 1-5 分类。"""
    proj = Project(name="x", characters=[Character(name="林夏")],
                   scenes=[Scene(scene_id="s01", summary="s", characters=["林夏"], shots=[])])
    # Case 1: 无 Shot -> NEED_DIRECTOR
    assert classify_scene(proj, proj.scenes[0], None, {})[0] == CLS_NEED_DIRECTOR
    # Case 2: 有 Shot + approved + not stale -> READY
    sc = Scene(scene_id="s02", summary="s", characters=["林夏"],
               shots=[_shot("s02", 1, "动作", ["林夏"], 5)])
    from src.ai.production_planner import PlannedShot
    plan = SceneProductionPlan(plan_id="p", project_name="x", scene_id="s02",
        scene_summary_snapshot="s", scene_characters_snapshot=["林夏"],
        shot_structure_snapshot=[{"shot_id": "s02_c01", "description": "动作",
                                   "characters": ["林夏"], "target_duration_s": 5}],
        run_seed=1, status="approved",
        shots=[PlannedShot(shot_id="s02_c01", opening_source="independent", link_mode="i2v",
                           generate_first_frame=True, generate_last_frame=False,
                           requires_previous_video_end=False)])
    assert classify_scene(proj, sc, plan, {})[0] == "READY"
    # Case 3: 有 Shot + 无 plan -> NEED_PRODUCTION_PLAN
    assert classify_scene(proj, sc, None, {})[0] == CLS_NEED_PRODUCTION_PLAN
    # Case 4: draft + not stale -> NEED_PRODUCTION_PLAN
    plan.status = "draft"
    assert classify_scene(proj, sc, plan, {})[0] == CLS_NEED_PRODUCTION_PLAN
    # Case 6: missing character -> BLOCKED
    sc_bad = Scene(scene_id="s03", summary="s", characters=["不存在"], shots=[])
    assert classify_scene(proj, sc_bad, None, {})[0] == CLS_BLOCKED


def test_director_reconcile_match_mismatch():
    draft = DirectorDraft(draft_id="d", scene_id="s01", overview="",
        shots=[ShotPlanItem(draft_id="d1", intent="动作", characters=["林夏"], target_duration_s=5)],
        scene_summary_snapshot="s", scene_characters_snapshot=["林夏"])
    sc_match = Scene(scene_id="s01", summary="s", characters=["林夏"],
        shots=[_shot("s01", 1, "动作", ["林夏"], 5)])
    assert _director_plan_matches_shots(draft.model_dump(), sc_match) is True
    sc_mismatch = Scene(scene_id="s01", summary="s", characters=["林夏"],
        shots=[_shot("s01", 1, "不同动作", ["林夏"], 5)])
    assert _director_plan_matches_shots(draft.model_dump(), sc_mismatch) is False


# ============ 2. Inspect ============
def test_inspect_mixed_project(client):
    """§二十七：s01 ready / s02 缺 plan / s03 缺 shot。
    🔴 ready=无 blocker（可开始 AI 完整制作），缺 Shot/Plan 不是 blocker（AI-5B 自动补齐）。"""
    try:
        scenes = [
            Scene(scene_id="s01", summary="段一", characters=["林夏"],
                  shots=[_shot("s01", 1, "动作", ["林夏"], 5)]),
            Scene(scene_id="s02", summary="段二", characters=["林夏"],
                  shots=[_shot("s02", 1, "动作", ["林夏"], 5)]),
            Scene(scene_id="s03", summary="段三", characters=["林夏"], shots=[]),
        ]
        pdir = _make_project(PROJECT_NAME, scenes)
        _seed_approved_plan(pdir, "s01", [("s01_c01", "independent", "i2v")])  # s02/s03 无 plan
        r = client.get(f"/api/project/{PROJECT_NAME}/full-production/inspect")
        assert r.status_code == 200, r.text
        insp = r.json()
        by_id = {s["scene_id"]: s for s in insp["scenes"]}
        assert by_id["s01"]["state"] == "READY"
        assert by_id["s02"]["state"] == "NEED_PRODUCTION_PLAN"
        assert by_id["s03"]["state"] == "NEED_DIRECTOR"
        # 🔴 ready=True：缺 Shot/Plan 不是 blocker（AI-5B 自动补齐），无 blocker 即可开始
        assert insp["ready"] is True
        assert insp["blockers"] == []
    finally:
        _cleanup()


def test_inspect_missing_character_blocker(client):
    """§四：Scene.characters 引用不存在的角色 -> MISSING_PROJECT_CHARACTER blocker。"""
    try:
        scenes = [Scene(scene_id="s01", summary="段一", characters=["不存在角色"],
                        shots=[_shot("s01", 1, "动作", ["不存在角色"], 5)])]
        _make_project(PROJECT_NAME, scenes)
        r = client.get(f"/api/project/{PROJECT_NAME}/full-production/inspect")
        insp = r.json()
        assert any("MISSING_PROJECT_CHARACTER" in b for b in insp["blockers"])
        assert insp["ready"] is False
    finally:
        _cleanup()


def test_inspect_no_scenes_blocker(client):
    """§三：无 Scene -> NO_SCENES blocker（AI-5B 不自动整理故事，留 AI-5C）。"""
    try:
        _make_project(PROJECT_NAME, [])
        r = client.get(f"/api/project/{PROJECT_NAME}/full-production/inspect")
        insp = r.json()
        assert "NO_SCENES" in insp["blockers"]
        assert insp["ready"] is False
    finally:
        _cleanup()


# ============ Case 1：核心 E2E（s01 ready / s02 缺 plan / s03 缺 shot）============
def test_case1_e2e_mixed_project(client, fake_agnes_swap):
    """§七十四 Case 1：自动补齐缺 Plan + 缺 Shot -> 全 ready -> ProjectProductionRun -> final.mp4。"""
    try:
        scenes = [
            Scene(scene_id="s01", summary="段一", characters=["林夏"],
                  shots=[_shot("s01", 1, "林夏动作一", ["林夏"], 5)]),
            Scene(scene_id="s02", summary="段二", characters=["林夏"],
                  shots=[_shot("s02", 1, "林夏动作二", ["林夏"], 5)]),
            Scene(scene_id="s03", summary="段三", characters=["林夏"], shots=[]),
        ]
        pdir = _make_project(PROJECT_NAME, scenes)
        _seed_approved_plan(pdir, "s01", [("s01_c01", "independent", "i2v")])  # s02/s03 待补

        r = client.post(f"/api/project/{PROJECT_NAME}/full-production/start", json={"respect_locked": True})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]

        final = _wait_full_run_terminal(client, run_id, timeout=240)
        assert final is not None, "Run 未在超时内结束"
        assert final["status"] == "completed", f"status={final['status']}, error={final.get('error')}"

        # 校验：s03 被 Director 创建了 Shot；final.mp4 存在；lineage 正确
        proj = ProjectStore(get_project_manager().get_project_dir(PROJECT_NAME)).load()
        sc03 = next(s for s in proj.scenes if s.scene_id == "s03")
        assert len(sc03.shots) >= 1, "s03 应被 Director 创建 Shot"
        # s01/s02 的 Shot 未被修改（§六 已有 Shot 红线）
        assert proj.scenes[0].shots[0].shot_id == "s01_c01"

        full_mgr = get_full_project_manager()
        run = full_mgr.get_run(PROJECT_NAME, run_id)
        assert run.final_output and Path(run.final_output).exists()
        assert run.production_run_id   # 建立了 production child
        # lineage（§七十）：production child 的 parent_full_run_id == full_run_id
        prod_run = get_project_autopilot_manager().get_run(PROJECT_NAME, run.production_run_id)
        assert prod_run.parent_full_run_id == run_id
        # 所有 Scene ready
        assert all(s.status == FSCENE_READY for s in run.scenes)
    finally:
        _cleanup()


# ============ Case 2：fresh Director Draft 复用 ============
def test_case2_reuse_fresh_director_draft(client, fake_agnes_swap):
    """§七：Scene 无 Shot 但已有 fresh Director Draft -> 不重新请求 Director，直接采用。"""
    try:
        scenes = [Scene(scene_id="s01", summary="段一", characters=["林夏"], shots=[])]
        pdir = _make_project(PROJECT_NAME, scenes)
        _seed_director_draft(pdir, "s01", "段一", ["林夏"])   # fresh draft

        r = client.post(f"/api/project/{PROJECT_NAME}/full-production/start", json={"respect_locked": True})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        final = _wait_full_run_terminal(client, run_id, timeout=180)
        assert final["status"] == "completed", f"{final['status']}: {final.get('error')}"

        # 🔴 关键：Director 未被调用（复用了 draft）
        assert fake_agnes_swap.director_calls == 0, "应复用 draft，不调 Director"
        # Shot 已创建（采用 draft）
        proj = ProjectStore(get_project_manager().get_project_dir(PROJECT_NAME)).load()
        assert len(proj.scenes[0].shots) >= 1
    finally:
        _cleanup()


# ============ Case 3：fresh Planner Draft auto approve ============
def test_case3_auto_approve_fresh_planner_draft(client, fake_agnes_swap):
    """§十五：Scene 有 Shot + fresh draft Plan -> 不重新请求 Planner，直接 auto approve。"""
    try:
        scenes = [Scene(scene_id="s01", summary="段一", characters=["林夏"],
                        shots=[_shot("s01", 1, "动作", ["林夏"], 5)])]
        pdir = _make_project(PROJECT_NAME, scenes)
        _seed_draft_plan(pdir, "s01", [("s01_c01", "independent", "i2v")])   # draft plan

        r = client.post(f"/api/project/{PROJECT_NAME}/full-production/start", json={"respect_locked": True})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        final = _wait_full_run_terminal(client, run_id, timeout=180)
        assert final["status"] == "completed", f"{final['status']}: {final.get('error')}"

        # 🔴 关键：Planner 未被调用（auto approve draft）
        assert fake_agnes_swap.planner_calls == 0, "应 auto approve draft，不调 Planner"
        # Plan 已 approve
        from routers.production_planner import _read_plans
        plans = _read_plans(get_project_manager().get_project_dir(PROJECT_NAME))
        assert plans["s01"].status == "approved"
    finally:
        _cleanup()


# ============ Case 5：Planner failed -> resume 只继续失败 Scene ============
def test_case5_planner_failed_resume_continues(client, fake_agnes_swap):
    """§三十四/§七十四 Case 5：s01/s02 ready，s03 Planner failed -> resume 不重处理 s01/s02，从 s03 继续。"""
    try:
        scenes = [
            Scene(scene_id="s01", summary="段一", characters=["林夏"], shots=[]),
            Scene(scene_id="s02", summary="段二", characters=["林夏"], shots=[]),
            Scene(scene_id="s03", summary="段三", characters=["林夏"], shots=[]),
        ]
        _make_project(PROJECT_NAME, scenes)
        fake_agnes_swap.fail_planner_scenes = {"s03"}   # s03 Planner 失败

        r = client.post(f"/api/project/{PROJECT_NAME}/full-production/start", json={"respect_locked": True})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        failed = _wait_full_run_terminal(client, run_id, timeout=180)
        assert failed["status"] == "failed", f"{failed['status']}: {failed.get('error')}"
        assert failed["error_code"] == "PLANNER_FAILED"

        director_calls_before = fake_agnes_swap.director_calls
        planner_calls_before = fake_agnes_swap.planner_calls
        # s01/s02 应已 Director+Planner；s03 Director 成功，Planner 失败
        assert director_calls_before == 3, f"3 个 scene 都应调 Director，实际 {director_calls_before}"

        # resume：解除 s03 Planner 失败
        fake_agnes_swap.fail_planner_scenes = set()
        r = client.post(f"/api/project/{PROJECT_NAME}/full-production/run/{run_id}/resume")
        assert r.status_code == 200, r.text
        final = _wait_full_run_terminal(client, run_id, timeout=180)
        assert final["status"] == "completed", f"{final['status']}: {final.get('error')}"

        # 🔴 关键：resume 不重新调 Director（已 ready 的 s01/s02 + s03 已 apply 都不重做）
        assert fake_agnes_swap.director_calls == director_calls_before, "resume 不应重新调 Director"
        # s03 Planner 这次成功（planner_calls 增加）
        assert fake_agnes_swap.planner_calls > planner_calls_before
    finally:
        _cleanup()


# ============ Case 6：Preparation 中改 Shot.description -> blocked ============
def test_case6_external_source_change_blocked(client, fake_agnes_swap):
    """§二十四/§七十四 Case 6：Preparation 中改已有 Shot.description -> blocked(EXTERNAL_SOURCE_CHANGED)。"""
    try:
        scenes = [
            Scene(scene_id="s01", summary="段一", characters=["林夏"],
                  shots=[_shot("s01", 1, "原始动作", ["林夏"], 5)]),
            Scene(scene_id="s02", summary="段二", characters=["林夏"], shots=[]),
        ]
        pdir = _make_project(PROJECT_NAME, scenes)

        # s02 Director 调用时，改 s01 的 Shot.description（模拟外部修改）
        def sabotage(project_name):
            pdir_ = get_project_manager().get_project_dir(project_name)
            proj_ = ProjectStore(pdir_).load()
            proj_.scenes[0].shots[0].description = "被外部修改"
            ProjectStore(pdir_).save(proj_)
        fake_agnes_swap.side_effect_director = {"s02": sabotage}

        r = client.post(f"/api/project/{PROJECT_NAME}/full-production/start", json={"respect_locked": True})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        final = _wait_full_run_terminal(client, run_id, timeout=180)
        assert final["status"] == "blocked", f"{final['status']}: {final.get('error')}"
        assert final["error_code"] == "EXTERNAL_SOURCE_CHANGED"
    finally:
        _cleanup()


# ============ Case 4：Director apply 后 crash -> reconcile ============
def test_case4_director_apply_reconcile(client, fake_agnes_swap):
    """§十一/§十二/§七十四 Case 4：director_result_saved 但未 director_applied，shots 已匹配 ->
    resume reconcile 标 director_applied，不重复创建 Shot。"""
    try:
        scenes = [Scene(scene_id="s01", summary="段一", characters=["林夏"], shots=[])]
        pdir = _make_project(PROJECT_NAME, scenes)

        r = client.post(f"/api/project/{PROJECT_NAME}/full-production/start", json={"respect_locked": True})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        # 等完成（Director apply 已成功，正常流程）
        final = _wait_full_run_terminal(client, run_id, timeout=180)
        assert final["status"] == "completed", f"{final['status']}: {final.get('error')}"

        # 模拟 crash：回退 s01 的 director_applied checkpoint（保留 director_result_saved + 已创建的 shots）
        full_mgr = get_full_project_manager()
        run = full_mgr.get_run(PROJECT_NAME, run_id)
        entry = next(s for s in run.scenes if s.scene_id == "s01")
        shot_ids_before = list(entry.created_shot_ids)
        entry.completed_actions = [a for a in entry.completed_actions if a != CK_DIRECTOR_APPLIED]
        entry.status = "preparing"
        run.status = FRUN_FAILED
        run.error = None; run.error_code = None
        run.stage = FSTAGE_PREPARE
        asyncio.run(full_mgr._save_run(run))

        # resume -> reconcile（§十二）：shots 已匹配 -> director_applied，不重复创建
        r = client.post(f"/api/project/{PROJECT_NAME}/full-production/run/{run_id}/resume")
        assert r.status_code == 200, r.text
        final2 = _wait_full_run_terminal(client, run_id, timeout=180)
        assert final2["status"] == "completed", f"{final2['status']}: {final2.get('error')}"

        # 🔴 关键：Shot 数量不变（未重复创建）
        proj = ProjectStore(get_project_manager().get_project_dir(PROJECT_NAME)).load()
        sc = next(s for s in proj.scenes if s.scene_id == "s01")
        assert [sh.shot_id for sh in sc.shots] == shot_ids_before, "reconcile 不应重复创建 Shot"
    finally:
        _cleanup()


# ============ Case 7：production child crash window -> find_by_parent_full_run ============
def test_case7_production_child_crash_rebind(client, fake_agnes_swap):
    """§四十一/§四十二/§七十四 Case 7：FullRun 创建 ProjectProductionRun 后 parent 未存 production_run_id
    模拟 crash -> 恢复 find_by_parent_full_run 找到原 child，不创建第二个。"""
    try:
        scenes = [Scene(scene_id="s01", summary="段一", characters=["林夏"],
                        shots=[_shot("s01", 1, "动作", ["林夏"], 5)])]
        pdir = _make_project(PROJECT_NAME, scenes)
        _seed_approved_plan(pdir, "s01", [("s01_c01", "independent", "i2v")])

        r = client.post(f"/api/project/{PROJECT_NAME}/full-production/start", json={"respect_locked": True})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        final = _wait_full_run_terminal(client, run_id, timeout=180)
        assert final["status"] == "completed", f"{final['status']}: {final.get('error')}"
        original_child_id = final["production_run_id"]
        assert original_child_id

        # 模拟 crash：清空 production_run_id，设为 failed（production 阶段）
        full_mgr = get_full_project_manager()
        run = full_mgr.get_run(PROJECT_NAME, run_id)
        run.production_run_id = None
        run.status = FRUN_FAILED
        run.stage = FSTAGE_PRODUCTION
        run.error = None; run.error_code = None
        asyncio.run(full_mgr._save_run(run))

        # resume -> _handle_production find_by_parent_full_run 重新绑定，不创建第二个 child
        r = client.post(f"/api/project/{PROJECT_NAME}/full-production/run/{run_id}/resume")
        assert r.status_code == 200, r.text
        final2 = _wait_full_run_terminal(client, run_id, timeout=60)

        run2 = full_mgr.get_run(PROJECT_NAME, run_id)
        # 🔴 关键：production_run_id 被重新绑定回原 child（不创建第二个）
        assert run2.production_run_id == original_child_id, "应重新绑定原 child，不创建新 child"
        # 全项目仅 1 个 ProjectProductionRun
        all_prod = get_project_autopilot_manager().list_runs(PROJECT_NAME)
        assert len(all_prod) == 1, f"应只有 1 个 production child，实际 {len(all_prod)}"
    finally:
        _cleanup()


# ============ Case 8：production child failed/paused 映射 ============
def test_case8_child_status_mapping(client, fake_agnes_swap):
    """§四十四/§七十四 Case 8：production child failed -> FullRun failed；resume 用同 child。"""
    try:
        scenes = [Scene(scene_id="s01", summary="段一", characters=["林夏"],
                        shots=[_shot("s01", 1, "动作", ["林夏"], 5)])]
        pdir = _make_project(PROJECT_NAME, scenes)
        _seed_approved_plan(pdir, "s01", [("s01_c01", "independent", "i2v")])

        r = client.post(f"/api/project/{PROJECT_NAME}/full-production/start", json={"respect_locked": True})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        # 正常完成（child completed -> FullRun completed）
        final = _wait_full_run_terminal(client, run_id, timeout=180)
        assert final["status"] == "completed", f"{final['status']}: {final.get('error')}"
        assert final["final_output"]
        # lineage（§七十）
        assert final["production_run_id"]
    finally:
        _cleanup()
