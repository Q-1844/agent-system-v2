# Phase 39（39.md §23 测试重点 1/2/5/8/9/12/13 + §6.6）：agent 工具面集成测试。
import asyncio
import json
from pathlib import Path

import pytest

from src.core.models import Asset, Project, Scene, Shot, ShotStatus, StyleAnchor, VideoVersion
from src.core.quota import QuotaLedger
from src.core.store import ProjectStore
from src.agents.runtime import AgentError, AgentRuntime
from src.agents.director import (
    DIRECTOR_TOOLS, DIRECTOR_WRITE_OPERATIONS, DIRECTOR_READ_TOOLS,
    DIRECTOR_LONG_TASK_TOOLS, TOOL_EXECUTORS,
)
from src.agents.director_calibration import _execute_seam_measure
from src.agents.director_calibration import _execute_trace_read as _tr_read
from src.agents.director_calibration import _execute_trace_upsert as _tr_upsert
from src.agents.director_frames import _execute_extract_frame, _execute_sample_frames
from src.agents.producer import _build_producer_tools, _execute_project_quota
from src.agents.producer import _derive_changed_refs
from src.agents.script_writer import SCRIPT_WRITER_TOOLS
from src.agents.registry import _build_agents
from src.experiments import calibration_trace as ct


def _runtime(tmp_path, client=None):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client)


def _run(coro):
    return asyncio.run(coro)


def _call(executor, rt, project, args: dict):
    return _run(executor(rt, project, json.dumps(args, ensure_ascii=False), None, None))


# ============ 工具面归属（39.md 测试重点 1/12/13 的工具面部分）============
def test_new_tools_registered():
    names = {t["function"]["name"] for t in DIRECTOR_TOOLS}
    assert {"calibration.seam_measure", "calibration.trace_read",
            "calibration.trace_upsert", "video.extract_frame",
            "visual.sample_frames", "shot.bridge_create"} <= names
    # 42.md 缺陷①：run 创建进工具面（trace 闭环自主完成）
    assert "calibration.run_create" in names
    for n in ("calibration.seam_measure", "calibration.trace_read",
              "calibration.trace_upsert", "video.extract_frame",
              "visual.sample_frames", "shot.bridge_create"):
        assert n in TOOL_EXECUTORS
    assert "calibration.run_create" in TOOL_EXECUTORS


def test_write_read_classification():
    assert "calibration.trace_upsert" in DIRECTOR_WRITE_OPERATIONS
    assert "calibration.run_create" in DIRECTOR_WRITE_OPERATIONS
    assert "shot.bridge_create" in DIRECTOR_WRITE_OPERATIONS
    assert "shot.bridge_create" in DIRECTOR_LONG_TASK_TOOLS
    for n in ("calibration.seam_measure", "calibration.trace_read",
              "video.extract_frame", "visual.sample_frames"):
        assert n in DIRECTOR_READ_TOOLS
        assert n not in DIRECTOR_WRITE_OPERATIONS


def test_disabled_agents_get_nothing_new():
    """禁用身份纪律（Phase 39 名单修订后仍生效的剩余部分）：
    continuity_reviewer / editor / shot_producer / production_planner 零校准/桥能力；
    scene_designer 已按用户 2026-09-25 修订恢复为合法第五 Specialist（条件参与），
    见 test_scene_designer_conditional_fifth_member。"""
    sw_names = {t["function"]["name"] for t in SCRIPT_WRITER_TOOLS}
    assert not any(n.startswith("shot.") for n in sw_names)
    assert not any(n.startswith("calibration.") for n in sw_names)
    # Producer 工具面 = 只读 + delegate + project.quota；无任何 canonical 写
    offered, executors = _build_producer_tools()
    pnames = {t["function"]["name"] for t in offered}
    assert "project.quota" in pnames
    assert not any(n.startswith("calibration.") for n in pnames)
    assert "project.quota" in executors
    agents = {a.id: a for a in _build_agents()}
    for disabled in ("continuity_reviewer", "editor", "shot_producer",
                     "production_planner"):
        caps = agents[disabled].allowed_capabilities
        assert not any("calibration" in c or "bridge" in c for c in caps)


def test_scene_designer_conditional_fifth_member():
    """用户 2026-09-25 名单定版：完整生产协作 roster = 5 Agent；
    两种校准模式默认 active set = 4；scene_designer 是合法第五 Specialist，
    【条件参与】——合法可路由，但普通校准不要求调用，且永远不拿校准工具。"""
    from src.agents.producer import DELEGATE_TOOLS
    from src.agents.scene_designer import SCENE_DESIGNER_TOOLS
    from src.agents.subagent.instance import _scene_agent_tools
    agents = {a.id: a for a in _build_agents()}
    # 1) 合法 roster 成员：场景能力在位
    assert agents["scene_designer"].allowed_capabilities == [
        "scene.inspect", "scene.prepare"]
    # 2) 校准/桥/镜头写能力绝不在它手上（即便条件参与也不拿）
    caps = agents["scene_designer"].allowed_capabilities
    assert not any("calibration" in c or "bridge" in c or "shot" in c for c in caps)
    scene_tool_names = {t["function"]["name"] for t in SCENE_DESIGNER_TOOLS}
    assert not any(n.startswith("calibration.") or "bridge" in n
                   for n in scene_tool_names)
    assert not any(n in ("shot.update", "shot.create", "shot.create_many",
                         "shot.update_many", "shot.delete", "shot.move",
                         "shot.generate_images", "shot.generate_videos")
                   for n in scene_tool_names)
    # 3) Producer 可路由（条件参与的接线必须通）
    offered, _ = _build_producer_tools()
    assert "scene_designer.delegate" in {t["function"]["name"] for t in offered}
    assert "scene_designer.delegate" in DELEGATE_TOOLS
    # 4) 校准工具不进场景子代理提案白名单（Director 裁定链专属，维持排除）
    assert not (_scene_agent_tools() & {
        "calibration.seam_measure", "calibration.trace_read",
        "calibration.trace_upsert", "video.extract_frame",
        "visual.sample_frames", "shot.bridge_create"})


# ============ seam 工具 ============

def _make_video(path: Path, color: str = "red"):
    import subprocess
    path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c={color}:size=64x48:rate=8:duration=1",
         "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr
    return path


@pytest.fixture()
def seam_env(tmp_path):
    pdir = tmp_path / "proj"
    pdir.mkdir()
    v1 = _make_video(pdir / "output" / "videos" / "s01_c01.mp4")
    v2 = _make_video(pdir / "output" / "videos" / "s01_c02.mp4", color="blue")
    from PIL import Image
    tail = pdir / "output" / "videos" / "s01_c01_end.jpg"
    Image.new("RGB", (64, 48), (20, 200, 60)).save(tail, "JPEG", quality=95)
    c01 = Shot(shot_id="s01_c01", scene_id="s01", index=1, description="A",
               final_video="vv_a",
               video_end_frame=Asset(local=str(tail), url="https://h/e.jpg",
                                     status=ShotStatus.DONE.value),
               video_end_source_version="vv_a")
    c01.video_versions.append(VideoVersion(version_id="vv_a", local=str(v1),
                                           status=ShotStatus.DONE.value))
    c02 = Shot(shot_id="s01_c02", scene_id="s01", index=2, description="B",
               source_shot_id="s01_c01", opening_source="inherit_previous",
               final_video="vv_b")
    c02.video_versions.append(VideoVersion(version_id="vv_b", local=str(v2),
                                           status=ShotStatus.DONE.value))
    ProjectStore(pdir).save(Project(name="p39tools", style=StyleAnchor(),
                                    scenes=[Scene(scene_id="s01", shots=[c01, c02])]))
    return pdir


def test_seam_executor_happy(seam_env, tmp_path):
    rt = _runtime(seam_env)
    out = _call(_execute_seam_measure, rt, seam_env.name, {"shot_id": "s01_c02"})
    assert out["ok"] and out["operation"] == "calibration.seam_measure"
    assert isinstance(out["tail_to_first_mae"], float)
    assert out["left"]["shot_id"] == "s01_c01"
    assert out["right"]["shot_id"] == "s01_c02"
    assert out["preprocessing_version"] == "gray128_bicubic_v1_phase38"
    # 读工具回执不得声明 verified=True（不进 shot 变更派生）
    assert not out.get("verified")
    assert out.get("shot_id") is None


def test_seam_executor_error_structured(seam_env, tmp_path):
    rt = _runtime(seam_env)
    with pytest.raises(AgentError) as ei:
        _call(_execute_seam_measure, rt, seam_env.name, {"shot_id": "s01_c01"})
    assert ei.value.code == "prev_not_found"


# ============ trace 工具（39.md 测试重点 8/9）============
def test_trace_upsert_enum_empty_string_rejected(tmp_path):
    rt = _runtime(tmp_path)
    run = ct.create_run(tmp_path, story_id="story39")
    with pytest.raises(AgentError) as ei:
        _call(_tr_upsert, rt, tmp_path.name,
              {"run_id": run["run_id"], "trace_key": "M1",
               "record": {"human_deviation_level": ""}})
    assert ei.value.code == "invalid"
    # None 显式可过（None 可以，空串不可以）
    out = _call(_tr_upsert, rt, tmp_path.name,
                {"run_id": run["run_id"], "trace_key": "M1",
                 "record": {"human_deviation_level": None, "tail_to_first_mae": 2.1}})
    assert out["ok"] and out["verified"] is True
    link = ct.get_link(tmp_path, run["run_id"], "M1")
    assert link["tail_to_first_mae"] == 2.1
    # trace 回执不含 shot_id → 不污染 Producer changed_refs
    refs = _derive_changed_refs("director", [out])
    assert refs["shots"] == [] and refs["scenes"] == []


# ============ run 创建工具（42.md §二缺陷①：trace 闭环自主完成）============
def test_run_create_tool_closes_trace_loop(tmp_path):
    from src.agents.director_calibration import _execute_run_create
    rt = _runtime(tmp_path)
    out = _call(_execute_run_create, rt, tmp_path.name,
                {"story_id": "story42", "title": "封板验收校准",
                 "notes": "agent 自主建 run"})
    assert out["ok"] and out["operation"] == "calibration.run_create"
    assert out["run_id"].startswith("cal_")
    assert out.get("shot_id") is None and not out.get("verified")
    # 落盘可读（服务层同源）
    run = ct.get_run(tmp_path, out["run_id"])
    assert run["story_id"] == "story42" and run["status"] == "active"
    # 建完即可 trace_upsert（不再 not_found）——Phase 41 缺陷的闭环验证
    rec = _call(_tr_upsert, rt, tmp_path.name,
                {"run_id": out["run_id"], "trace_key": "s01_c01",
                 "record": {"human_deviation_level": "minor",
                            "human_action": "continue", "why": "闭环验证"}})
    assert rec["ok"] and rec["run_id"] == out["run_id"]
    # 缺 story_id 结构化拒绝
    with pytest.raises(AgentError) as ei:
        _call(_execute_run_create, rt, tmp_path.name, {})
    assert ei.value.code == "bad_arguments"


def test_trace_upsert_archive_history_not_overwritten(tmp_path):
    rt = _runtime(tmp_path)
    run = ct.create_run(tmp_path, story_id="story39")
    # 首次裁定入库
    _call(_tr_upsert, rt, tmp_path.name,
          {"run_id": run["run_id"], "trace_key": "M3",
           "record": {"human_deviation_level": "major",
                      "human_action": "retry_generation", "why": "ECU 特写+地理丢失"}})
    # 未带 archive_existing 直接改写 → 覆盖（模拟误操作时旧值仍在合并前 link 中）
    # 正确流程：重试录新 attempt 前 archive_existing=true → 旧裁定归档 M3_a1
    out = _call(_tr_upsert, rt, tmp_path.name,
                {"run_id": run["run_id"], "trace_key": "M3",
                 "record": {"human_deviation_level": "minor",
                            "human_action": "continue", "why": "a2 修复"},
                 "archive_existing": True})
    assert out["ok"] and out["archived_to"] == "M3_a1"
    archived = ct.get_link(tmp_path, run["run_id"], "M3_a1")
    current = ct.get_link(tmp_path, run["run_id"], "M3")
    assert archived["human_deviation_level"] == "major"
    assert archived["human_action"] == "retry_generation"
    assert current["human_deviation_level"] == "minor"
    # 第二次归档 → M3_a2（历史链不覆盖）
    out2 = _call(_tr_upsert, rt, tmp_path.name,
                 {"run_id": run["run_id"], "trace_key": "M3",
                  "record": {"human_deviation_level": "none"},
                  "archive_existing": True})
    assert out2["archived_to"] == "M3_a2"
    assert ct.get_link(tmp_path, run["run_id"], "M3_a2")["human_deviation_level"] == "minor"


def test_trace_read_granularity(tmp_path):
    rt = _runtime(tmp_path)
    runs = _call(_tr_read, rt, tmp_path.name, {})
    assert runs["ok"] and runs["count"] == 0
    run = ct.create_run(tmp_path, story_id="story39",
                        story_plan=[{"shot_id": "M1", "expected_tail": "站定"}])
    run_view = _call(_tr_read, rt, tmp_path.name, {"run_id": run["run_id"]})
    assert run_view["run"]["story_frozen"] is True
    assert run_view["run"]["story_plan"][0]["expected_tail"] == "站定"
    _call(_tr_upsert, rt, tmp_path.name,
          {"run_id": run["run_id"], "trace_key": "M1",
           "record": {"human_deviation_level": "none"}})
    link = _call(_tr_read, rt, tmp_path.name,
                 {"run_id": run["run_id"], "trace_key": "M1"})
    assert link["link"]["human_deviation_level"] == "none"
    with pytest.raises(AgentError) as ei:
        _call(_tr_read, rt, tmp_path.name, {"run_id": "cal_nope"})
    assert ei.value.code == "not_found"


# ============ Producer 配额可见性（39.md §6.6）============
def test_producer_quota_reads_real_ledger(tmp_path, monkeypatch):
    ledger = QuotaLedger(tmp_path / "quota.json",
                         limits={"video_seconds": 500, "images": 4000})
    ledger.consume("video_seconds", 93)
    monkeypatch.setattr(QuotaLedger, "_default", ledger, raising=False)
    out = _execute_project_quota(_runtime(tmp_path), "proj", "{}", None, None)
    assert out["ok"] and out["operation"] == "project.quota"
    assert out["quota"]["video_seconds"] == {"used": 93, "limit": 500}
    assert out["quota"]["images"]["limit"] == 4000


def test_producer_quota_not_hardcoded(tmp_path, monkeypatch):
    """不同 limits 配置下读数随之变化——证明读的是真实账本而非 prompt 硬编码。"""
    ledger = QuotaLedger(tmp_path / "q2.json", limits={"video_seconds": 777})
    monkeypatch.setattr(QuotaLedger, "_default", ledger, raising=False)
    out = _execute_project_quota(_runtime(tmp_path), "proj", "{}", None, None)
    assert out["quota"]["video_seconds"]["limit"] == 777


# ============ 帧工具 ============
class _FakeVisionClient:
    """sample_frames 的帧对检查 fake：按调用序返回契约 JSON。"""

    def __init__(self, replies=None):
        self.replies = replies or []
        self.calls = []

    def chat_completion_vision(self, msgs, **kwargs):
        self.calls.append(msgs)
        idx = min(len(self.calls) - 1, len(self.replies) - 1)
        content = (self.replies[idx] if self.replies else
                   json.dumps({"discontinuity": "none", "note": "自然过渡"}))
        return _FakeAwaitable({"content": content, "model": "fake", "usage": {}})


class _FakeAwaitable(dict):
    """可 await 的 dict（模拟 async chat_completion_vision 的返回值）。"""

    def __await__(self):
        if False:
            yield
        return self


def test_extract_frame_executor(seam_env, monkeypatch):
    import deps

    class FakeHost:
        async def upload(self, local):
            return f"https://host/{Path(local).name}"

    monkeypatch.setattr(deps, "_rehost_host", FakeHost(), raising=False)
    rt = _runtime(seam_env)
    out = _call(_execute_extract_frame, rt, seam_env.name,
                {"shot_id": "s01_c02", "position": "first",
                 "purpose": "bridge_boundary"})
    assert out["ok"] and out["asset"]["url"].startswith("https://host/")
    assert out["provenance"]["shot_id"] == "s01_c02"
    assert out["provenance"]["purpose"] == "bridge_boundary"
    assert Path(out["asset"]["local"]).exists()


def test_sample_frames_executor(seam_env, monkeypatch):
    from src.agents.director_frames import _execute_sample_frames
    rt = _runtime(seam_env, client=_FakeVisionClient())
    out = _call(_execute_sample_frames, rt, seam_env.name, {"shot_id": "s01_c02"})
    assert out["ok"] and out["operation"] == "visual.sample_frames"
    assert [f["label"] for f in out["frames"]] == [
        "beginning", "early_middle", "middle", "late_middle", "ending"]
    # 五位采样 → 4 个相邻帧对检查
    assert len(out["pairwise_evidence"]) == 4
    assert all(e["discontinuity"] in ("none", "suspected", "severe", "unresolved")
               for e in out["pairwise_evidence"])
    assert not out.get("verified")
    assert out["discontinuity_summary"]["severe"] == 0


def test_sample_frames_reports_severe(seam_env):
    from src.agents.director_frames import _execute_sample_frames
    replies = [json.dumps({"discontinuity": "severe", "note": "画面瞬间切换"})]
    rt = _runtime(seam_env, client=_FakeVisionClient(replies=replies))
    out = _call(_execute_sample_frames, rt, seam_env.name, {"shot_id": "s01_c02"})
    assert out["discontinuity_summary"]["severe"] >= 1
