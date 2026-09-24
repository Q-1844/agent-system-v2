# tests/test_calibration_trace_37.py
# Phase 37A（37.md §2/§3/§16）：Calibration Trace 存储 + REST 契约 + Vision 尾帧评估。
# 全程零真实外部调用（vision 用 stub client）。
import json
import shutil

import pytest

import deps
from deps import get_project_manager
from src.experiments import calibration_trace as trace
from src.experiments.calibration_trace import CalibrationTraceError


PROJECT = "_test_37_trace"


@pytest.fixture
def proj(client):
    pm = get_project_manager()
    shutil.rmtree(pm.base_dir / PROJECT, ignore_errors=True)
    pm.create_project(PROJECT)
    yield PROJECT
    shutil.rmtree(pm.get_project_dir(PROJECT), ignore_errors=True)


PLAN = [
    {"shot_id": "s01_c01", "description": "雨林小径狐狸奔跑",
     "expected_tail": "狐狸四足落地站稳", "duration_s": 8.0},
    {"shot_id": "s02_c01", "description": "发现发光蘑菇",
     "expected_tail": "鼻尖轻触蘑菇", "duration_s": 10.0},
]


def test_run_create_list_get_update(proj):
    pdir = get_project_manager().get_project_dir(PROJECT)
    run = trace.create_run(pdir, story_id="s09", title="雨林奇遇",
                           story_plan=PLAN, chain_root_reference_id="ref_d27e1a01c36f")
    assert run["run_id"] and run["story_frozen"] is True
    runs = trace.list_runs(pdir)
    assert len(runs) == 1 and runs[0]["chain_root_reference_id"] == "ref_d27e1a01c36f"
    got = trace.get_run(pdir, run["run_id"])
    assert got["story_plan"][0]["expected_tail"].startswith("狐狸四足")
    upd = trace.update_run(pdir, run["run_id"], {"status": "completed"})
    assert upd["status"] == "completed"
    with pytest.raises(CalibrationTraceError):
        trace.update_run(pdir, run["run_id"], {"status": "bogus"})


def test_link_upsert_merge_semantics(proj):
    """分次补齐：先 vision 后 human——dict 深合并不丢字段；枚举严格校验。"""
    pdir = get_project_manager().get_project_dir(PROJECT)
    run = trace.create_run(pdir, story_id="s09", story_plan=PLAN)
    rid = run["run_id"]
    trace.upsert_link(pdir, rid, "s01_c01", {
        "provider_id": "agnes", "provider_model": "agnes-video-v2.0",
        "generation_mode": "multi_relay", "duration_s": 8.0,
        "expected_tail": "狐狸四足落地站稳",
        "vision_assessment": {"identity_consistency": "ok",
                              "suggested_deviation": "none"},
    })
    trace.upsert_link(pdir, rid, "s01_c01", {
        "human_deviation_level": "minor",
        "human_action": "add_transition",
        "human_reason": "朝向略偏，下一镜加转身",
        "vision_assessment": {"environment_consistency": "misted trail"},
        "prompt_rebuilt": True,
    })
    link = trace.get_link(pdir, rid, "s01_c01")
    va = link["vision_assessment"]
    assert va["identity_consistency"] == "ok"
    assert va["environment_consistency"] == "misted trail"
    assert link["human_deviation_level"] == "minor"
    assert link["story_id"] == "s09"
    with pytest.raises(CalibrationTraceError):
        trace.upsert_link(pdir, rid, "s01_c01", {"human_deviation_level": "huge"})
    with pytest.raises(CalibrationTraceError):
        trace.upsert_link(pdir, "cal_missing", "s01_c01", {})


def test_rest_run_and_link_roundtrip(client, proj):
    c = client
    r = c.post(f"/api/project/{PROJECT}/calibration/runs", json={
        "story_id": "s09", "title": "雨林奇遇", "story_plan": PLAN,
        "chain_root_reference_id": "ref_d27e1a01c36f"})
    assert r.status_code == 200, r.text
    rid = r.json()["run_id"]
    r2 = c.put(f"/api/project/{PROJECT}/calibration/runs/{rid}/links/s01_c01",
               json={"record": {"human_deviation_level": "none",
                                "human_action": "continue",
                                "human_reason": "尾帧与预期一致"}})
    assert r2.status_code == 200, r2.text
    r3 = c.get(f"/api/project/{PROJECT}/calibration/runs/{rid}")
    body = r3.json()
    assert body["status"] == "active"
    assert len(body["links"]) == 1
    r4 = c.get(f"/api/project/{PROJECT}/calibration/runs/{rid}/links/s01_c01")
    assert r4.json()["human_action"] == "continue"
    r5 = c.patch(f"/api/project/{PROJECT}/calibration/runs/{rid}",
                 json={"status": "completed", "notes": "首轮收官"})
    assert r5.json()["status"] == "completed"
    r6 = c.get(f"/api/project/{PROJECT}/calibration/runs/{rid}/links/s02_c01")
    assert r6.status_code == 404
    r7 = c.get(f"/api/project/{PROJECT}/calibration/runs/cal_nope")
    assert r7.status_code == 404


# ============ Vision 尾帧评估（stub client）============

class _StubVision:
    def __init__(self, reply):
        self.replies = [reply] if isinstance(reply, str) else list(reply)
        self.calls = []

    async def chat_completion_vision(self, messages, *, model, temperature, max_tokens):
        self.calls.append(messages)
        return {"content": self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]}


GOOD_JSON = json.dumps({
    "identity_consistency": "same fox, red-orange fur",
    "environment_consistency": "rainforest trail maintained",
    "character_pose_state": "standing on all fours",
    "character_position": "center-left",
    "prop_state": "no props",
    "action_endpoint": "run completed, settled",
    "camera_state": "side tracking ended static",
    "expected_tail_match": "matches: fox landed and stable",
    "next_shot_readiness": "ready: standing pose can start next action",
    "narrative_state": "mid-journey",
    "unexpected_elements": "none",
    "suggested_deviation": "none",
    "notes": "",
})


def test_assess_tail_parses_structured_dimensions():
    from src.experiments.tail_assessment import assess_tail
    stub = _StubVision(GOOD_JSON)
    out = _run(assess_tail(stub, expected_tail="狐狸四足落地站稳",
                           next_shot_description="发现发光蘑菇"))
    a = out["assessment"]
    assert a["suggested_deviation"] == "none"
    assert a["expected_tail_match"].startswith("matches")
    assert out["meta"]["parse_repair_used"] is False
    # 图片条目位置：image 1 = prev tail, image 2 = current tail
    content = stub.calls[0][1]["content"]
    assert content[0]["type"] == "text"
    assert "EXPECTED TAIL" in content[0]["text"]


def test_assess_tail_repair_and_missing_fields_marked():
    from src.experiments.tail_assessment import assess_tail
    incomplete = GOOD_JSON.replace('"prop_state": "no props",', "")
    stub = _StubVision(["I see a fox in a forest.", incomplete])
    out = _run(assess_tail(stub, expected_tail="x"))
    assert out["meta"]["parse_repair_used"] is True
    assert "prop_state" in out["assessment"].get("_missing_fields", [])


def test_assess_tail_unparseable_raises():
    from src.experiments.tail_assessment import assess_tail
    stub = _StubVision("I cannot assess this image.")
    with pytest.raises(ValueError):
        _run(assess_tail(stub, expected_tail="x"))


def _run(coro):
    import asyncio
    return asyncio.run(coro)
