# tests/test_scene_system_s2a_review.py
# Scene System V1 — Phase S2A §21-§24 测试（T27/T28 + review 证据链）。
#
# T27 Review dimensions all present（记录含全部六维度）
# T28 overall verdict deterministic（模型自报不采信；关键维度 fail→needs_fix；
#     concern→uncertain；维度缺失/anchor 缺回报→fail closed 不判 pass）
# + review 只读 canonical（不改 view revision / approval_status）
# + no_candidates / 证据流 append-only
import asyncio
import base64
import json
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.scene_system.models import view_revision
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
from src.scene_system.visual_service import SceneVisualService
from src.scene_system import view_review, generation_records

PNG_B64 = base64.b64encode(b"\x89PNG fake-image").decode()


class FakeGen:
    models = {"image": "fake-image-model"}

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        return {"url": None, "b64": PNG_B64}


class FakeVision:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def chat_completion_vision(self, msgs, *, model):
        self.calls.append({"model": model, "n_images": sum(
            1 for part in msgs[0]["content"] if part.get("type") == "image_url")})
        return {"content": self.content,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _reply(anchor_id: str, overrides: dict | None = None) -> str:
    d = {
        "location_identity": "pass", "location_notes": "",
        "anchors": [{"anchor_id": anchor_id, "visibility": "clearly_visible", "notes": ""}],
        "anchor_consistency": "pass", "anchor_notes": "",
        "layout_consistency": "pass", "layout_notes": "",
        "view_consistency": "pass", "view_notes": "",
        "visual_defects": "pass", "defect_notes": "",
        "project_style_consistency": "pass", "style_notes": "ok",
        "overall": "pass", "summary": "ok",
    }
    d.update(overrides or {})
    return json.dumps(d, ensure_ascii=False)


def make_pdir(tmp_path: Path) -> Path:
    name = tmp_path.name
    pdir = tmp_path / name
    proj = Project(name=name)
    proj.scenes = [Scene(scene_id="s01", summary="x")]
    ProjectStore(pdir).save(proj)
    # P1：生成路径 profile-aware（fail closed on unspecified）——测试环境默认写实
    from src.visual_profile import VisualProfileService
    VisualProfileService(pdir, pdir.name).update(render_family="photorealistic",
                                                 expected_revision="")
    return pdir


async def _seed_and_generate(svc: SceneSystemService, pdir: Path, tmp_path_name: str):
    async def drev():
        return svc.document_overview()["document_revision"]

    loc = (await svc.create_location(name="灯塔监听室",
                                     identity_description="circular control room",
                                     expected_document_revision=await drev()))["target"]
    a1 = (await svc.create_anchor(location_id=loc, name="north door", kind="door",
                                  expected_document_revision=await drev()))["target"]
    va = await svc.create_view(location_id=loc, label="hero", camera_zone="south_west",
                               look_direction="north_east", visible_anchor_ids=[a1],
                               expected_document_revision=await drev())
    vid = va["target"]
    vs = SceneVisualService(pdir, tmp_path_name)
    gen = await vs.generate_location_view(vid, expected_revision=va["revision"],
                                          candidate_count=2, agnes_client=FakeGen())
    assert gen["ok"], gen.get("error")
    return {"loc": loc, "a1": a1, "vid": vid, "gen": gen}


def test_t27_review_dimensions_all_present(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_and_generate(svc, pdir, pdir.name))
    r = asyncio.run(view_review.review_location_view_candidates(
        pdir, ids["vid"], agnes_client=FakeVision(_reply(ids["a1"]))))
    assert r["ok"], r.get("error")
    for cr in r["candidate_reviews"]:
        # P1 §15：profile specified 时追加 project_style_consistency（hard 维度）
        assert set(cr["dimensions"].keys()) == (
            set(view_review.VIEW_DIMENSIONS) | {"project_style_consistency"})
    rec = view_review.read_view_review_records(pdir, view_id=ids["vid"])[-1]
    assert rec["operation"] == "scene.review_candidates"
    for field in ("review_id", "location_id", "view_id", "generation_id",
                  "reviewed_basis_fingerprint", "model", "prompt", "candidate_files",
                  "reference_asset_ids_used", "candidate_reviews", "usage",
                  "started_at", "completed_at", "status"):
        assert field in rec, field
    # 每个候选一次独立 vision 调用
    assert len(r["candidate_reviews"]) == 2


def test_t28_overall_deterministic_key_dim_fail(tmp_path):
    """关键维度 fail：即使模型自报 overall=pass，推导仍 needs_fix。"""
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_and_generate(svc, pdir, pdir.name))
    reply = _reply(ids["a1"], {"location_identity": "fail", "overall": "pass"})
    r = asyncio.run(view_review.review_location_view_candidates(
        pdir, ids["vid"], agnes_client=FakeVision(reply)))
    cr = r["candidate_reviews"][0]
    assert cr["dimensions"]["location_identity"] == "fail"
    assert cr["overall"] == "needs_fix", "关键维度 fail → overall 不得 PASS（§23）"
    assert cr["model_overall"] == "pass"


def test_t28b_overall_uncertain_on_concern(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_and_generate(svc, pdir, pdir.name))
    reply = _reply(ids["a1"], {"view_consistency": "concern"})
    r = asyncio.run(view_review.review_location_view_candidates(
        pdir, ids["vid"], agnes_client=FakeVision(reply)))
    cr = r["candidate_reviews"][0]
    assert cr["overall"] == "uncertain"


def test_t28c_missing_dimension_fail_closed(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_and_generate(svc, pdir, pdir.name))
    # layout_consistency 维度缺失
    reply = _reply(ids["a1"])
    d = json.loads(reply)
    del d["layout_consistency"]
    r = asyncio.run(view_review.review_location_view_candidates(
        pdir, ids["vid"], agnes_client=FakeVision(json.dumps(d))))
    cr = r["candidate_reviews"][0]
    assert cr["dimensions"]["layout_consistency"] is None
    assert cr["overall"] == "uncertain", "维度缺失不判 pass（fail closed）"
    assert cr["overall"] != "pass"


def test_t28d_anchor_missing_from_reply_fail_closed(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_and_generate(svc, pdir, pdir.name))
    # 回报了错误 anchor_id（真实 anchor 未回报）→ fail closed
    reply = _reply("anc_wrong000001")
    r = asyncio.run(view_review.review_location_view_candidates(
        pdir, ids["vid"], agnes_client=FakeVision(reply)))
    cr = r["candidate_reviews"][0]
    assert cr["dimensions"]["anchor_visibility"] == "fail"
    assert cr["per_anchor"][0]["visibility"] == "missing"
    assert cr["overall"] == "needs_fix"


def test_t28e_anchor_partially_is_not_pass(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_and_generate(svc, pdir, pdir.name))
    d = json.loads(_reply(ids["a1"]))
    d["anchors"][0]["visibility"] = "partially"
    r = asyncio.run(view_review.review_location_view_candidates(
        pdir, ids["vid"], agnes_client=FakeVision(json.dumps(d))))
    cr = r["candidate_reviews"][0]
    assert cr["dimensions"]["anchor_visibility"] == "uncertain"
    assert cr["overall"] == "uncertain", "partially 可见不可作为可批准 pass"


def test_review_is_read_only_on_canonical(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_and_generate(svc, pdir, pdir.name))
    rev_before = view_revision(SceneSystemStore(pdir, pdir.name).load().views[ids["vid"]])
    r = asyncio.run(view_review.review_location_view_candidates(
        pdir, ids["vid"], agnes_client=FakeVision(_reply(ids["a1"]))))
    assert r["ok"]
    v = SceneSystemStore(pdir, pdir.name).load().views[ids["vid"]]
    assert view_revision(v) == rev_before, "review 不改 Scene canonical（§24）"
    assert v.approval_status == "unreviewed"


def test_review_requires_committed_candidate(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def seed_only():
        async def drev():
            return svc.document_overview()["document_revision"]
        loc = (await svc.create_location(name="L", expected_document_revision=await drev()))["target"]
        va = await svc.create_view(location_id=loc, label="v",
                                   expected_document_revision=await drev())
        return va["target"]
    vid = asyncio.run(seed_only())
    r = asyncio.run(view_review.review_location_view_candidates(
        pdir, vid, agnes_client=FakeVision(_reply("x"))))
    assert not r["ok"] and r["error"] == "no_candidates"
    # 不写失败运行的 record？——写（provider/parse 分支有 record；no_candidates 在
    # 构造 review 之前返回，不产生 record）
    assert view_review.read_view_review_records(pdir, view_id=vid) == []
