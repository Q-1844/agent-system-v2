# tests/test_scene_camera_review.py
# Agent System V2 Phase C2 Work A（28.md §2）：机位正确性审查加固测试。
#
# 覆盖：
# - camera_zone_group 词元语义（overhead/side/rear/front 命中；compass 自由串不误判；
#   子串陷阱不误命中——"countryside"/"hilltop"）
# - derive_camera_view_consistency 确定性推导（矛盾 → fail；无证据 → 不判 pass；
#   overhead+other → fail；一致 → 模型判定生效）
# - 审查 prompt 含机位语义块（LocationView + SceneInstanceView 两处）
# - review_location_view_candidates 集成：requested=overhead 而模型 observed=front
#   （feature checklist 假视角）→ view_consistency=fail → overall=needs_fix（§2.4
#   诚实原则：假 PASS 不得 approve）
import asyncio
import json

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.scene_system import view_review
from src.scene_system.camera_review import (camera_expectation_block,
                                            camera_zone_group,
                                            derive_camera_view_consistency)
from src.scene_system.service import SceneSystemService
from src.scene_system.store import SceneSystemStore  # noqa: F401
from src.scene_system.visual_service import SceneVisualService


# ============ camera_zone_group ============
def test_zone_group_hits():
    assert camera_zone_group("overhead") == "overhead"
    assert camera_zone_group("top_down") == "overhead"
    assert camera_zone_group("top-down") == "overhead"
    assert camera_zone_group("nadir") == "overhead"
    assert camera_zone_group("left_side") == "side"
    assert camera_zone_group("east_side") == "side"
    assert camera_zone_group("rear") == "rear"
    assert camera_zone_group("back_alley_position") == "rear"
    assert camera_zone_group("front") == "front"
    assert camera_zone_group("front_gate") == "front"


def test_zone_group_compass_and_substring_no_false_hits():
    # compass 自由串：无词元命中 → None（无法确定性验证 → 交给强化后的模型判卷）
    assert camera_zone_group("south_west") is None
    assert camera_zone_group("north_entrance") is None
    assert camera_zone_group("north") is None
    assert camera_zone_group("") is None
    # 子串陷阱：token 精确匹配，不误命中
    assert camera_zone_group("countryside") is None   # 含 "side" 子串但非词元
    assert camera_zone_group("hilltop_cottage") is None
    assert camera_zone_group("outback") is None


# ============ derive_camera_view_consistency ============
def test_derive_contradiction_fails():
    v, note = derive_camera_view_consistency("overhead", "front", "pass")
    assert v == "fail" and "overhead" in note and "front" in note
    v, _ = derive_camera_view_consistency("rear", "front", "pass")
    assert v == "fail"
    v, _ = derive_camera_view_consistency("left_side", "front", "pass")
    assert v == "fail"
    # 模型自报 fail 优先保留（即使 observed 一致）
    v, note = derive_camera_view_consistency("overhead", "overhead", "fail")
    assert v == "fail" and note == ""


def test_derive_no_evidence_caps_pass_to_uncertain():
    # 模型没报告 observed（旧式回复/缺失字段）→ pass 封顶 uncertain（fail closed）
    for observed in (None, "", "uncertain", "sideways", "FRONTAL-ISH"):
        v, note = derive_camera_view_consistency("overhead", observed, "pass")
        assert v == "uncertain", observed
        assert "fail closed" in note
    # 模型本来就 concern/fail → 原样保留
    assert derive_camera_view_consistency("overhead", None, "concern")[0] == "concern"
    assert derive_camera_view_consistency("overhead", None, "fail")[0] == "fail"


def test_derive_other_and_opaque():
    # overhead 请求 + observed=other → fail（§2.2：overhead 必须真接近俯视）
    v, _ = derive_camera_view_consistency("overhead", "other", "pass")
    assert v == "fail"
    # 非 overhead 请求 + observed=other → pass 封顶 uncertain
    v, _ = derive_camera_view_consistency("rear", "other", "pass")
    assert v == "uncertain"
    # 请求机位不可识别（compass）→ 模型判定原样生效
    v, note = derive_camera_view_consistency("south_west", "front", "pass")
    assert v == "pass" and note == ""


# ============ prompt 语义块 ============
def test_camera_block_semantics():
    b = camera_expectation_block("overhead", "site center")
    assert "CAMERA CORRECTNESS" in b
    assert "overhead" in b
    assert "independently of anchor visibility" in b.lower() or \
        "independently" in b.lower()
    assert "camera_observed_zone" in b
    assert "evidence AGAINST" in b   # feature checklist 假视角 = 反证
    # 通用版本（无 zone）仍要求报告 observed
    b2 = camera_expectation_block("", "")
    assert "camera_observed_zone" in b2


def _make_pdir(tmp_path):
    name = tmp_path.name
    pdir = tmp_path / name
    proj = Project(name=name)
    proj.scenes = [Scene(scene_id="s01", summary="x")]
    ProjectStore(pdir).save(proj)
    from src.visual_profile import VisualProfileService
    VisualProfileService(pdir, pdir.name).update(render_family="photorealistic",
                                                 expected_revision="")
    return pdir


class FakeGen:
    models = {"image": "fake-image-model"}

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        return {"url": None, "b64": "aW1n"}


class FakeVision:
    def __init__(self, content: str):
        self.content = content

    async def chat_completion_vision(self, msgs, *, model):
        return {"content": self.content,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _reply(anchor_id: str, overrides: dict | None = None) -> str:
    d = {
        "location_identity": "pass", "location_notes": "",
        "anchors": [{"anchor_id": anchor_id, "visibility": "clearly_visible", "notes": ""}],
        "anchor_consistency": "pass", "anchor_notes": "",
        "layout_consistency": "pass", "layout_notes": "",
        "camera_observed_zone": "overhead", "camera_evidence": "flat map view",
        "view_consistency": "pass", "view_notes": "",
        "visual_defects": "pass", "defect_notes": "",
        "project_style_consistency": "pass", "style_notes": "ok",
        "overall": "pass", "summary": "ok",
    }
    d.update(overrides or {})
    return json.dumps(d, ensure_ascii=False)


async def _seed_overhead_view(svc: SceneSystemService, pdir, name):
    async def drev():
        return svc.document_overview()["document_revision"]

    loc = (await svc.create_location(name="watchtower yard",
                                     identity_description="stone yard with tower",
                                     expected_document_revision=await drev()))["target"]
    a1 = (await svc.create_anchor(location_id=loc, name="west chimney", kind="landmark",
                                  expected_document_revision=await drev()))["target"]
    va = await svc.create_view(location_id=loc, label="plan", camera_zone="overhead",
                               look_direction="site center", visible_anchor_ids=[a1],
                               expected_document_revision=await drev())
    vid = va["target"]
    vs = SceneVisualService(pdir, name)
    gen = await vs.generate_location_view(vid, expected_revision=va["revision"],
                                          candidate_count=1, agnes_client=FakeGen())
    assert gen["ok"], gen.get("error")
    return {"loc": loc, "a1": a1, "vid": vid, "view_rev": va["revision"]}


def test_feature_checklist_fake_overhead_is_needs_fix(tmp_path):
    """28.md §2 核心场景：requested=overhead，模型 observed=front（所有 anchor 塞进
    正面 hero 图）→ view_consistency 确定性 fail → overall=needs_fix（不得假 PASS）。"""
    pdir = _make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_overhead_view(svc, pdir, pdir.name))
    reply = _reply(ids["a1"], {"camera_observed_zone": "front",
                               "camera_evidence": "frontal hero composition",
                               "view_consistency": "pass", "overall": "pass"})
    r = asyncio.run(view_review.review_location_view_candidates(
        pdir, ids["vid"], agnes_client=FakeVision(reply)))
    cr = r["candidate_reviews"][0]
    assert cr["dimensions"]["view_consistency"] == "fail"
    assert cr["overall"] == "needs_fix"
    assert cr["model_overall"] == "pass"   # 模型自报不采信
    assert "矛盾" in cr["camera_derivation_note"] or "front" in cr["camera_derivation_note"]


def test_no_observed_evidence_caps_to_uncertain(tmp_path):
    """模型回复缺 camera_observed_zone（如旧式回复）→ view_consistency 不判 pass。"""
    pdir = _make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_overhead_view(svc, pdir, pdir.name))
    d = json.loads(_reply(ids["a1"]))
    del d["camera_observed_zone"]
    del d["camera_evidence"]
    r = asyncio.run(view_review.review_location_view_candidates(
        pdir, ids["vid"], agnes_client=FakeVision(json.dumps(d))))
    cr = r["candidate_reviews"][0]
    assert cr["dimensions"]["view_consistency"] == "uncertain"
    assert cr["overall"] == "uncertain"


def test_review_prompt_contains_camera_block(tmp_path):
    pdir = _make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_overhead_view(svc, pdir, pdir.name))
    asyncio.run(view_review.review_location_view_candidates(
        pdir, ids["vid"], agnes_client=FakeVision(_reply(ids["a1"]))))
    rec = view_review.read_view_review_records(pdir, view_id=ids["vid"])[-1]
    assert "CAMERA CORRECTNESS" in rec["prompt"]
    assert "camera_observed_zone" in rec["prompt"]
