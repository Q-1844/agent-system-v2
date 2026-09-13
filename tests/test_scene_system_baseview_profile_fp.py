# tests/test_scene_system_baseview_profile_fp.py
# Golden Path 发现的跨阶段集成 blocker 回归锁定（2026-09-13）。
#
# 症状：P1 §15 把 visual_profile_fingerprint 加进 view review/approve 的期望指纹后，
# check_base_location_view（SIV 生成门槛）重算漏传该维度 → 任何有 ProjectVisualProfile
# 的项目，真实 review+approve 链批准的 base view 永远误判 location_view_stale，
# SIV 无法生成。
#
# 本文件双向锁定（防止修复退化成"永远 fresh"）：
#   Case 1 同 Profile：真实 generate→review→approve 后 check 必须 fresh，
#                      SIV 生成门槛可放行；
#   Case 2 Profile 真变化：approve 后修改 ProjectVisualProfile → check 必须判
#                      location_view_stale（批准语义含项目风格维度）。
import asyncio
import base64
import json
import sys
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))

from src.core.models import Project, Scene
from src.core.store import ProjectStore
from src.resources.service import ResourceService
from src.scene_system import view_review
from src.scene_system.models import view_revision
from src.scene_system.service import SceneSystemService
from src.scene_system.store import SceneSystemStore
from src.scene_system.visual_basis import load_resources_doc_or_none
from src.scene_system.visual_service import SceneVisualService
from src.visual_profile import VisualProfileService

PNG_B64 = base64.b64encode(b"\x89PNG fake-image").decode()


class FakeGen:
    models = {"image": "fake-image-model"}

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        return {"url": None, "b64": PNG_B64}


class FakeVision:
    def __init__(self, content: str):
        self.content = content

    async def chat_completion_vision(self, msgs, *, model):
        return {"content": self.content, "usage": {}}


def _pass_reply(anchor_id: str) -> str:
    return json.dumps({
        "location_identity": "pass", "location_notes": "",
        "anchors": [{"anchor_id": anchor_id, "visibility": "clearly_visible",
                     "notes": ""}],
        "anchor_consistency": "pass", "anchor_notes": "",
        "layout_consistency": "pass", "layout_notes": "",
        "view_consistency": "pass", "view_notes": "",
        "visual_defects": "pass", "defect_notes": "",
        "project_style_consistency": "pass", "style_notes": "ok",
        "overall": "pass", "summary": "ok"}, ensure_ascii=False)


def _cur_profile_revision(pdir: Path) -> str:
    from src.visual_profile.models import profile_revision
    cur = VisualProfileService(pdir, pdir.name).store.load_or_none()
    return profile_revision(cur) if cur is not None else ""


@pytest.fixture()
def approved_view_env(tmp_path):
    """project + P1 Profile + 真实 generate→review→approve 链批准的 base view。"""
    pdir = tmp_path / tmp_path.name
    pdir.mkdir()
    proj = Project(name=tmp_path.name)
    proj.scenes = [Scene(scene_id="s01", summary="x")]
    ProjectStore(pdir).save(proj)
    VisualProfileService(pdir, pdir.name).update(render_family="photorealistic",
                                                 expected_revision="")
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        async def drev():
            return svc.document_overview()["document_revision"]

        loc = (await svc.create_location(
            name="L", identity_description="a small test room",
            expected_document_revision=await drev()))["target"]
        a1 = (await svc.create_anchor(location_id=loc, name="north door", kind="door",
                                      expected_document_revision=await drev()))["target"]
        va = await svc.create_view(location_id=loc, label="hero",
                                   camera_zone="south_west",
                                   look_direction="north_east",
                                   visible_anchor_ids=[a1],
                                   expected_document_revision=await drev())
        vid = va["target"]
        vs = SceneVisualService(pdir, pdir.name)
        gen = await vs.generate_location_view(vid, expected_revision=va["revision"],
                                              candidate_count=1,
                                              agnes_client=FakeGen())
        assert gen["ok"], gen.get("error")
        rv = await view_review.review_location_view_candidates(
            pdir, vid, agnes_client=FakeVision(_pass_reply(a1)))
        assert rv["ok"] and rv["candidate_reviews"][0]["overall"] == "pass", rv
        ap = await vs.approve_location_view(
            vid, candidate_index=0, expected_revision=gen["revision_after"],
            expected_resources_document_revision=ResourceService(
                pdir, pdir.name).document_revision())
        assert ap["ok"], ap
        return vs, vid, loc, a1

    vs, vid, loc, a1 = asyncio.run(flow())
    return pdir, svc, vs, vid, loc, a1


def _check(vs, pdir, vid):
    from src.scene_system.models import ApprovalState
    d = SceneSystemStore(pdir, pdir.name).load()
    return vs.check_base_location_view(
        d, load_resources_doc_or_none(pdir), d.views[vid])


def test_case1_same_profile_base_view_fresh_and_siv_gate_proceeds(approved_view_env):
    """Case 1：同 Profile 下真实链批准的 base view —— check fresh（不得误判 stale），
    SIV 生成门槛放行。"""
    pdir, svc, vs, vid, loc, a1 = approved_view_env
    r = _check(vs, pdir, vid)
    assert r["ok"], f"同 Profile 已批准 base view 不得判 stale: {r}"
    # SIV 生成门槛可放行：create + generate 不返回 location_view_stale
    from test_scene_system_s2b_generation import FakeClient
    inst = (asyncio.run(svc.create_scene_instance(
        story_scene_id="s01", location_id=loc,
        expected_document_revision=svc.document_overview()["document_revision"])))["target"]
    sciv = (asyncio.run(vs.create_scene_instance_view(
        scene_instance_id=inst, location_view_id=vid,
        expected_document_revision=svc.document_overview()["document_revision"])))["target"]
    gen = asyncio.run(vs.generate_scene_instance_view(
        sciv, expected_revision=view_revision(
            SceneSystemStore(pdir, pdir.name).load().scene_instance_views[sciv]),
        candidate_count=1, include_peer=False, agnes_client=FakeClient()))
    assert gen["ok"], f"SIV 生成门槛未放行: {gen.get('error')}: {gen.get('detail')}"


def test_case2_profile_change_base_view_stale(approved_view_env):
    """Case 2：批准后 ProjectVisualProfile 真变化 → check 必须判 location_view_stale
    （本修复不得退化成"永远 fresh"）。"""
    pdir, svc, vs, vid, loc, a1 = approved_view_env
    assert _check(vs, pdir, vid)["ok"]
    r = VisualProfileService(pdir, pdir.name).update(
        render_family="2d_animation",
        art_direction="changed style direction",
        expected_revision=_cur_profile_revision(pdir))
    assert r["ok"], r
    r2 = _check(vs, pdir, vid)
    assert not r2["ok"] and r2["error"] == "location_view_stale", \
        f"Profile 变化后 base view 必须判 stale: {r2}"
