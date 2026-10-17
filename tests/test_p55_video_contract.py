# Phase55 A 定向测试：统一视频提交契约（src/generation/video_contract.py）。
#
# 覆盖（任务书 §10-A「针对角色调用差异、缺场景图、非法字段、旧端点做定向验证」）：
#   1. negative_prompt 编译（M5 事故编译化：项目层意图→合法正文约束）；
#   2. 提交前校验：时长契约 / multi 需绑定 / BOUND 基线等价 / 未知参数拒绝；
#   3. 参考策略三级解析（显式 > 项目级活动 run 策略 > 默认 bound_required）；
#   4. 场景参考消费判定（provider_reference / via_first_frame / absent）；
#   5. 统一输入依据清单（BOUND 资产清单 vs UNBOUND 诚实 None）；
#   6. 接线验证：director 工具默认闸（无绑定拒绝）+ 项目级 legacy 策略放行；
#   7. REST 旧端点：入队前 422（multi 无绑定/时长越界），legacy i2v 放行+manifest。
# 全部确定性（0 网络/0 provider）；READY 级 binding 用合成 validity（不依赖 SIV 夹具）。
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.models import (Project, Scene, Shot, Character, StyleAnchor,
                             ImageAsset, Asset, ShotStatus, ImageBatch, Candidate)
from src.core.store import ProjectStore

from src.generation.video_contract import (
    MAX_VIDEO_SECONDS, MIN_VIDEO_SECONDS,
    build_input_manifest,
    compile_negative_prompt,
    planned_video_seconds,
    reject_unknown_video_params,
    resolve_reference_policy,
    scene_reference_consumed,
    set_project_reference_policy,
    validate_video_submission,
)

PROJECT = "_test_p55_contract"


# ============ 合成对象 ============
def _shot(sid="s01_c01", **kw):
    s = Shot(shot_id=sid, scene_id="s01", index=1, description="机甲出发",
             characters=["阿派"])
    for k, v in kw.items():
        setattr(s, k, v)
    return s


READY = {"shot_id": "s01_c01", "binding_state": "bound", "readiness": "READY",
         "binding_revision": "rev1", "document_revision": "d1",
         "resolved_dependencies": {"scene_instance_view_id": "sciv_1"},
         "dependency_checks": [], "issues": []}
NOT_READY = {**READY, "readiness": "NOT_READY",
             "issues": [{"reason": "look_not_approved", "detail": "Look 未批准"}]}
UNBOUND = {"shot_id": "s01_c01", "binding_state": "unbound", "readiness": "UNBOUND",
           "binding_revision": None, "document_revision": "d1",
           "resolved_dependencies": {}, "dependency_checks": [], "issues": []}


# ============ 1. negative_prompt 编译 ============
def test_compile_negative_prompt_deterministic():
    a = compile_negative_prompt("no blood, no text")
    b = compile_negative_prompt(" no blood, no text ")
    assert a == b and "no blood, no text" in a
    assert compile_negative_prompt("") == ""
    assert compile_negative_prompt(None) == ""


def test_reject_unknown_video_params():
    assert reject_unknown_video_params({"origin_run_id": "x",
                                        "population_constraint": True}) == []
    assert reject_unknown_video_params({"bogus_switch": 1, "video_model": None}) \
        == ["bogus_switch"]


# ============ 2. 提交前校验 ============
def test_validate_duration_out_of_contract():
    s = _shot(target_duration_s=20.0)
    r = validate_video_submission(s, validity=UNBOUND)
    assert not r["ok"]
    codes = [v["code"] for v in r["violations"]]
    assert "duration_out_of_contract" in codes
    # num_frames-only 的历史长镜头同样拒绝（此前被 client 静默夹到 12s）
    s2 = _shot()
    s2.video.num_frames = 441
    s2.video.frame_rate = 24
    r2 = validate_video_submission(s2, validity=UNBOUND)
    assert any(v["code"] == "duration_out_of_contract" for v in r2["violations"])


def test_validate_ok_path_returns_compiled_negative():
    s = _shot(target_duration_s=5.0, negative_prompt="no text")
    r = validate_video_submission(s, validity=UNBOUND)
    assert r["ok"]
    assert "no text" in r["compiled_negative_prompt"]
    # 合法时长往返
    assert MIN_VIDEO_SECONDS <= planned_video_seconds(s) <= MAX_VIDEO_SECONDS


def test_validate_multi_requires_binding_even_legacy():
    s = _shot(link_mode="multi", target_duration_s=5.0)
    r = validate_video_submission(s, require_binding=False, validity=UNBOUND)
    assert not r["ok"]
    assert any(v["code"] == "multi_without_binding" for v in r["violations"])


def test_validate_bound_required_gate():
    s = _shot(target_duration_s=5.0, link_mode="i2v")
    r = validate_video_submission(s, require_binding=True, validity=UNBOUND)
    assert any(v["code"] == "binding_required" for v in r["violations"])
    # READY 放行
    r2 = validate_video_submission(s, require_binding=True, validity=READY)
    assert r2["ok"]
    # NOT_READY 带结构化 issues
    r3 = validate_video_submission(s, require_binding=True, validity=NOT_READY)
    v3 = next(v for v in r3["violations"] if v["code"] == "binding_not_ready")
    assert v3["issues"][0]["reason"] == "look_not_approved"


def test_validate_unknown_params_rejected():
    s = _shot(target_duration_s=5.0)
    r = validate_video_submission(s, validity=READY, params={"bogus": 1})
    assert any(v["code"] == "unknown_params" for v in r["violations"])


# ============ 3. 参考策略解析 ============
def test_reference_policy_resolution(tmp_path):
    # 默认 bound_required（无文件）
    assert resolve_reference_policy(tmp_path) == "bound_required"
    # 显式参数最高
    assert resolve_reference_policy(tmp_path, explicit="legacy_allowed") \
        == "legacy_allowed"
    # 项目级活动 run 策略次之
    set_project_reference_policy(tmp_path, "legacy_allowed", source="mode_run",
                                 run_id="run_x")
    assert resolve_reference_policy(tmp_path) == "legacy_allowed"
    assert resolve_reference_policy(tmp_path, explicit="bound_required") \
        == "bound_required"
    # 损坏文件回落默认
    p = tmp_path / "output" / "agents" / "reference_policy.json"
    p.write_text("{broken", encoding="utf-8")
    assert resolve_reference_policy(tmp_path) == "bound_required"
    # 非法写入显式拒绝
    with pytest.raises(ValueError):
        set_project_reference_policy(tmp_path, "whatever")


# ============ 4. 场景参考消费判定 ============
def test_scene_reference_provider_reference_for_multi_bound():
    s = _shot(link_mode="multi")
    r = scene_reference_consumed(None, s, None, validity=READY)
    assert r["state"] == "provider_reference"


def test_scene_reference_via_first_frame_for_bound_i2v():
    s = _shot(link_mode="i2v")
    s.selected_first_frame = "b1_1"
    s.first_frame_batches = [ImageBatch(
        batch_id="b1", kind="first_frame", created_at=1.0, status="done",
        prompt_snapshot="p", candidates=[Candidate(candidate_id="b1_1")],
        generation_basis={"scene_reference_mode": "full"})]
    r = scene_reference_consumed(None, s, None, validity=READY)
    assert r["state"] == "via_first_frame"


def test_scene_reference_absent_for_unbound():
    s = _shot(link_mode="i2v", target_duration_s=5.0)
    r = scene_reference_consumed(None, s, None, validity=UNBOUND)
    assert r["state"] == "absent"
    # BOUND i2v 但首帧批次无场景参考记录 = absent（诚实，不冒充有场景）
    s2 = _shot(link_mode="i2v")
    s2.selected_first_frame = "b1_1"
    s2.first_frame_batches = [ImageBatch(
        batch_id="b1", kind="first_frame", created_at=1.0, status="done",
        prompt_snapshot="p", candidates=[Candidate(candidate_id="b1_1")],
        generation_basis={})]
    r2 = scene_reference_consumed(None, s2, None, validity=READY)
    assert r2["state"] == "absent"


# ============ 5. 输入依据清单 ============
def test_manifest_unbound_honest_inventory():
    s = _shot(target_duration_s=5.0, opening_state="门闭", end_state="门开")
    m = build_input_manifest(None, s, stage="video", mode="i2v",
                             video_model=None, seconds=5.0, validity=UNBOUND,
                             scene_ref={"state": "absent", "detail": "x"},
                             compiled_negative_prompt=True)
    assert m["schema"] == "input_manifest_v1"
    assert m["shot"]["opening_state"] == "门闭" and m["shot"]["end_state"] == "门开"
    assert m["assets"]["characters"][0]["look_id"] is None
    assert "unbound" in m["assets"]["characters"][0]["note"]
    assert m["reference_consumption"]["state"] == "absent"
    assert m["compiled_negative_prompt"] is True
    assert m["upstream"]["opening_source"] == "independent"


def test_manifest_bound_inventory_from_binding_doc(tmp_path):
    """BOUND：资产清单从真实 binding 文档读取（look_id/pinned sciv）。"""
    from src.shot_binding.models import (CharacterBinding, SceneBinding,
                                         ShotProductionBinding)
    from src.shot_binding.store import ShotBindingStore
    s = _shot(target_duration_s=5.0)
    s.characters = ["Alice"]
    b = ShotProductionBinding(
        shot_id="s01_c01", scene_id="s01",
        scene_binding=SceneBinding(scene_instance_id="inst1",
                                   location_view_id="view1",
                                   pinned_scene_instance_view_id="sciv1"),
        character_bindings=[CharacterBinding(character_name="Alice",
                                             look_id="look_1")])
    store = ShotBindingStore(tmp_path, PROJECT)
    doc = store.load()
    doc.bindings["s01_c01"] = b
    store.save(doc)
    m = build_input_manifest(None, s, stage="video", mode="multi",
                             video_model=None, seconds=5.0,
                             project_dir=tmp_path, validity=READY,
                             scene_ref={"state": "provider_reference"})
    assert m["assets"]["characters"][0]["look_id"] == "look_1"
    assert m["assets"]["scene"]["pinned_scene_instance_view_id"] == "sciv1"


# ============ 6. director 工具接线（默认闸 + 项目级 legacy 放行） ============
# 复用 test_director_visual 的引擎级夹具（真实 JobQueue+真实 handler+FakeAgnes）——
# _execute_generation 会等 job 终态，FakeQueue 会挂到总超时，必须真实 worker。
import deps   # noqa: E402
from src.core.job_queue import JobQueue   # noqa: E402
from src.agents.runtime import AgentRuntime   # noqa: E402
from routers.shots import (   # noqa: E402
    _handler_first_frame as _p55_hff,
    _handler_video as _p55_hv,
)
import sys as _sys   # noqa: E402
_sys.path.insert(0, str(Path(__file__).parent))
from test_director_visual import (FakeAgnes, FakeRehost, _FakePM,   # noqa: E402
                                  _shot as _p55_dv_shot)


@pytest.fixture
def tool_env(tmp_path):
    agnes = FakeAgnes()
    saved = (deps.agnes_client, deps._rehost_host, deps.project_manager)
    deps.agnes_client = agnes
    deps._rehost_host = FakeRehost()
    deps.project_manager = _FakePM(tmp_path)
    q = JobQueue(tmp_path / "jobs.json",
                 {"first_frame": _p55_hff, "video": _p55_hv})
    rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                      settings_path=tmp_path / "agents_data" / "settings.json",
                      job_queue_getter=lambda: q, agnes_getter=lambda: agnes,
                      client=None)
    import src.agents.director_visual as dv
    try:
        yield rt, q, agnes, tmp_path, dv
    finally:
        deps.agnes_client, deps._rehost_host, deps.project_manager = saved


def _save_min_proj(tmp_path, shots):
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事",
                   characters=[Character(name="阿派", description_en="a")],
                   scenes=[Scene(scene_id="s01", summary="s", characters=["阿派"],
                                 shots=list(shots))])
    ProjectStore(tmp_path).save(proj)
    return proj


def _mark_ff_ready(tmp_path, shot_id, url="https://fake.agnes/first.png"):
    store = ProjectStore(tmp_path)

    def mut(s):
        s.first_frame = ImageAsset(
            candidates=[Asset(local=str(tmp_path / "output" / "frames" /
                                        f"{shot_id}_first_v1.png"),
                              url=url, status=ShotStatus.DONE.value)],
            selected_index=0, status=ShotStatus.DONE.value)
    store.update_shot(shot_id, mut)


def test_director_tool_default_gate_blocks_unbound(tool_env):
    """Phase55 基线等价：shot.generate_videos 默认 bound_required——无绑定镜头
    入队前结构化拒绝（binding_required），不提交 job。"""
    rt, q, agnes, tmp_path, dv = tool_env
    _save_min_proj(tmp_path, [_p55_dv_shot("s01_c01", dur=5.0)])
    _mark_ff_ready(tmp_path, "s01_c01")   # 越过结构预检，直达参考契约闸

    async def scenario():
        await q.start()
        return await dv._execute_shot_generate_videos(
            rt, PROJECT, json.dumps({"shot_ids": ["s01_c01"]}), "u", "m")

    obs = asyncio.run(scenario())
    assert not obs["ok"]
    r = obs["results"][0]
    assert r["status"] == "binding_required"
    assert "BOUND" in r["detail"] or "参考链" in r["detail"]
    assert "create_video" not in agnes.calls


def test_director_tool_project_policy_legacy_allows(tool_env):
    """项目级活动 run 策略=legacy_allowed 时放行（显式逃生口，manifest 落档）。"""
    rt, q, agnes, tmp_path, dv = tool_env
    set_project_reference_policy(tmp_path, "legacy_allowed", source="test")
    _save_min_proj(tmp_path, [_p55_dv_shot("s01_c01", dur=5.0)])
    _mark_ff_ready(tmp_path, "s01_c01")

    async def scenario():
        await q.start()
        return await dv._execute_shot_generate_videos(
            rt, PROJECT, json.dumps({"shot_ids": ["s01_c01"]}), "u", "m")

    obs = asyncio.run(scenario())
    assert obs["ok"], obs
    proj = ProjectStore(tmp_path).load()
    shot = proj.get_shot("s01_c01")
    mine = [vv for vv in shot.video_versions
            if vv.origin_run_id == obs["results"][0]["origin_run_id"]]
    assert len(mine) == 1
    im = mine[0].generation_basis.get("input_manifest")
    assert im and im["reference_consumption"]["state"] == "absent"


def test_director_tool_duration_violation_blocked_pre_queue(tool_env):
    """时长越界入队前拒绝（M5 类事故不再排队后逐个失败）。"""
    rt, q, agnes, tmp_path, dv = tool_env
    set_project_reference_policy(tmp_path, "legacy_allowed", source="test")
    _save_min_proj(tmp_path, [_p55_dv_shot("s01_c01", dur=30.0)])
    _mark_ff_ready(tmp_path, "s01_c01")   # 越过结构预检，直达时长契约闸

    async def scenario():
        await q.start()
        return await dv._execute_shot_generate_videos(
            rt, PROJECT, json.dumps({"shot_ids": ["s01_c01"]}), "u", "m")

    obs = asyncio.run(scenario())
    assert not obs["ok"]
    assert obs["results"][0]["status"] == "duration_out_of_contract"
    assert "create_video" not in agnes.calls


# ============ 7. REST 旧端点 ============
def _rest_env(tmp_path, monkeypatch):
    import deps
    import routers.shots as rs

    class _FakePM:
        def __init__(self, pdir):
            self._pdir = pdir
            self.base_dir = pdir

        def get_project_dir(self, name):
            return self._pdir

    saved = deps.project_manager
    deps.project_manager = _FakePM(tmp_path)
    yield rs
    deps.project_manager = saved


def test_rest_endpoint_multi_without_binding_422(tmp_path, monkeypatch):
    rs = next(_rest_env(tmp_path, monkeypatch))
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事",
                   characters=[Character(name="阿派", description_en="a")],
                   scenes=[Scene(scene_id="s01", summary="s", characters=["阿派"],
                                 shots=[_shot(link_mode="multi",
                                              target_duration_s=5.0)])])
    ProjectStore(tmp_path).save(proj)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(rs.shot_video(PROJECT, "s01_c01"))
    assert ei.value.status_code == 422
    assert ei.value.detail["error"] == "video_contract_violation"
    codes = [v["code"] for v in ei.value.detail["violations"]]
    assert "multi_without_binding" in codes


def test_rest_endpoint_legacy_i2v_enqueues_with_manifest(tmp_path, monkeypatch):
    rs = next(_rest_env(tmp_path, monkeypatch))
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事",
                   characters=[Character(name="阿派", description_en="a")],
                   scenes=[Scene(scene_id="s01", summary="s", characters=["阿派"],
                                 shots=[_shot(target_duration_s=5.0)])])
    ProjectStore(tmp_path).save(proj)

    class _Q:
        def __init__(self):
            self.last = None

        def submit(self, project, kind, shot_id, **params):
            self.last = SimpleNamespace(job_id="j1", project=project, kind=kind,
                                        shot_id=shot_id, params=dict(params))
            return self.last

    import deps
    saved_q = deps.job_queue
    fq = _Q()
    deps.job_queue = fq
    try:
        out = asyncio.run(rs.shot_video(PROJECT, "s01_c01"))
    finally:
        deps.job_queue = saved_q
    assert out["job_id"] == "j1"
    im = fq.last.params["input_manifest"]
    assert im["stage"] == "video" and im["method"]["mode"] == "i2v"
    assert im["reference_consumption"]["state"] == "absent"


def test_rest_endpoint_duration_violation_422(tmp_path, monkeypatch):
    rs = next(_rest_env(tmp_path, monkeypatch))
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事",
                   characters=[Character(name="阿派", description_en="a")],
                   scenes=[Scene(scene_id="s01", summary="s", characters=["阿派"],
                                 shots=[_shot(target_duration_s=30.0)])])
    ProjectStore(tmp_path).save(proj)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(rs.shot_video(PROJECT, "s01_c01"))
    assert ei.value.status_code == 422
