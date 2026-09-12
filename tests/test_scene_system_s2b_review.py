# tests/test_scene_system_s2b_review.py
# Scene System V1 — Phase S2B：Vision Review 测试（§39 T32-T35 + 只读 canonical）。
#
# T32 seven review dimensions present
# T33 per-anchor state evidence（预期 anchor 缺回报 → fail closed；verdict 汇总）
# T34 per-prop evidence（预期 prop 缺回报 → fail closed；observed 枚举）
# T35 deterministic overall（hard 集条件化；模型自报不采信）
# + review 零改 canonical / no_candidates 零 record / 记录字段（§29）
import asyncio
import base64
import json
from pathlib import Path

import pytest

from src.scene_system.models import view_revision
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
from src.scene_system.visual_service import SceneVisualService
from src.scene_system import generation_records
from src.scene_system import instance_view_review as ivr

sys_path = Path(__file__).parent
import sys
sys.path.insert(0, str(sys_path))
from test_scene_system_s2b_generation import (  # noqa: E402
    make_pdir, FakeClient, _seed, _sciv_rev, PNG_B64,
)


class FakeVision:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def chat_completion_vision(self, msgs, *, model):
        self.calls.append({"model": model, "n_images": sum(
            1 for part in msgs[0]["content"] if part.get("type") == "image_url")})
        return {"content": self.content, "usage": {"prompt_tokens": 5, "completion_tokens": 5}}


def _reply(anchor_id: str, prop_id: str, overrides: dict | None = None) -> str:
    d = {
        "location_identity": "pass", "location_notes": "",
        "view_consistency": "pass", "view_notes": "",
        "anchor_consistency": "pass", "anchor_notes": "",
        "scene_state": {"time_of_day": "pass", "weather": "pass",
                        "environment_conditions": "pass",
                        "anchor_states": [{"anchor_id": anchor_id, "verdict": "pass",
                                           "notes": ""}],
                        "notes": ""},
        "lighting_weather": "pass", "lighting_notes": "",
        "props": [{"prop_id": prop_id, "observed": "correct", "verdict": "pass",
                   "notes": ""}],
        "visual_defects": "pass", "defect_notes": "",
        "project_style_consistency": "pass", "style_notes": "ok",
        "overall": "pass", "summary": "ok",
    }
    d.update(overrides or {})
    return json.dumps(d, ensure_ascii=False)


async def _seed_generate(svc: SceneSystemService, vs: SceneVisualService, pdir: Path):
    ids = await _seed(svc, pdir, pdir.name)
    gen = await vs.generate_scene_instance_view(
        ids["sciv"], expected_revision=_sciv_rev(pdir, ids["sciv"]),
        candidate_count=2, agnes_client=FakeClient(), include_peer=False)
    assert gen["ok"], gen.get("error")
    ids["gen"] = gen
    return ids


def test_t32_seven_dimensions_present(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_generate(svc, vs, pdir))
    fv = FakeVision(_reply(ids["a_w2"], "prop_sword01"))
    r = asyncio.run(ivr.review_scene_instance_view_candidates(
        pdir, ids["sciv"], agnes_client=fv))
    assert r["ok"], r.get("error")
    assert r["model"] == "agnes-3.0-flash", "S2B 使用 3.0-flash（探针实证切换）"
    for cr in r["candidate_reviews"]:
        # P1 §15：profile specified 时追加 project_style_consistency（恒 hard 维度）
        assert set(cr["dimensions"].keys()) == (
            set(ivr.SIV_DIMENSIONS) | {"project_style_consistency"})
    # 每候选一次独立调用；对比图 = base（include_peer=False 无 peer）
    assert len(fv.calls) == 2
    assert all(c["n_images"] == 2 for c in fv.calls)   # candidate + base ref
    rec = ivr.read_instance_view_review_records(pdir, scene_instance_view_id=ids["sciv"])[-1]
    assert rec["operation"] == "scene_instance_view.review_candidates"
    assert rec["review_type"] == "scene_instance_view"
    for field in ("review_id", "scene_instance_view_id", "generation_id",
                  "reviewed_basis_fingerprint", "model", "prompt", "candidate_files",
                  "reference_asset_ids_used", "expected_state_projection",
                  "candidate_reviews", "usage", "started_at", "completed_at", "status"):
        assert field in rec, field


def test_t33_per_anchor_state_evidence_fail_closed(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_generate(svc, vs, pdir))
    # 预期 anchor（west window 02 broken）未回报 → fail closed
    r = asyncio.run(ivr.review_scene_instance_view_candidates(
        pdir, ids["sciv"], agnes_client=FakeVision(_reply("anc_wrong00001", "prop_sword01"))))
    cr = r["candidate_reviews"][0]
    assert cr["dimensions"]["scene_state_consistency"] == "fail"
    items = {i["item"]: i for i in cr["scene_state_items"]}
    assert items[f"anchor_state:{ids['a_w2']}"]["verdict"] == "fail"
    assert cr["overall"] == "needs_fix", "hard 维度 fail → needs_fix"


def test_t34_per_prop_evidence_fail_closed(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_generate(svc, vs, pdir))
    # prop 未回报 → fail closed
    r = asyncio.run(ivr.review_scene_instance_view_candidates(
        pdir, ids["sciv"], agnes_client=FakeVision(_reply(ids["a_w2"], "prop_wrong001"))))
    cr = r["candidate_reviews"][0]
    assert cr["dimensions"]["prop_placement_consistency"] == "fail"
    assert cr["per_prop"][0]["prop_id"] == "prop_sword01"
    assert cr["per_prop"][0]["observed"] == "missing"
    assert cr["overall"] == "needs_fix", "有可见 props 时 prop 维度 hard"


def test_t35_deterministic_overall(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_generate(svc, vs, pdir))
    # 全 pass 但模型自报 needs_fix → 推导 pass（自报不采信）
    reply = _reply(ids["a_w2"], "prop_sword01", {"overall": "needs_fix"})
    r = asyncio.run(ivr.review_scene_instance_view_candidates(
        pdir, ids["sciv"], agnes_client=FakeVision(reply)))
    cr = r["candidate_reviews"][0]
    assert cr["overall"] == "pass" and cr["model_overall"] == "needs_fix"
    # view_consistency fail（hard）→ needs_fix
    reply2 = _reply(ids["a_w2"], "prop_sword01", {"view_consistency": "fail"})
    r2 = asyncio.run(ivr.review_scene_instance_view_candidates(
        pdir, ids["sciv"], agnes_client=FakeVision(reply2)))
    assert r2["candidate_reviews"][0]["overall"] == "needs_fix"
    # concern（非 fail）→ uncertain
    reply3 = _reply(ids["a_w2"], "prop_sword01", {"visual_defects": "concern"})
    r3 = asyncio.run(ivr.review_scene_instance_view_candidates(
        pdir, ids["sciv"], agnes_client=FakeVision(reply3)))
    assert r3["candidate_reviews"][0]["overall"] == "uncertain"


def test_review_read_only_and_no_candidates(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)

    async def seed_only():
        ids = await _seed(svc, pdir, pdir.name)
        return ids
    ids = asyncio.run(seed_only())
    rev_before = view_revision(SceneSystemStore(pdir, pdir.name).load()
                               .scene_instance_views[ids["sciv"]])
    r = asyncio.run(ivr.review_scene_instance_view_candidates(
        pdir, ids["sciv"], agnes_client=FakeVision(_reply(ids["a_w2"], "prop_sword01"))))
    assert not r["ok"] and r["error"] == "no_candidates"
    assert ivr.read_instance_view_review_records(pdir, scene_instance_view_id=ids["sciv"]) == []
    # 生成后再 review：canonical 不动
    gen = asyncio.run(vs.generate_scene_instance_view(
        ids["sciv"], expected_revision=_sciv_rev(pdir, ids["sciv"]),
        candidate_count=1, agnes_client=FakeClient(), include_peer=False))
    assert gen["ok"]
    asyncio.run(ivr.review_scene_instance_view_candidates(
        pdir, ids["sciv"], agnes_client=FakeVision(_reply(ids["a_w2"], "prop_sword01"))))
    v = SceneSystemStore(pdir, pdir.name).load().scene_instance_views[ids["sciv"]]
    assert v.approval_status == "unreviewed" and v.reference_asset_id is None
