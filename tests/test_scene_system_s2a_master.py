# tests/test_scene_system_s2a_master.py
# Scene System V1 — Phase S2A 主验收补充套件（§28 T1-T39 中尚未被专用套件覆盖的部分
# + HTTP 层视觉协议冒烟）。
#
# 覆盖映射（其余场景见各专用文件）：
#   T1  S1 tests remain green          → 回归运行（report 记录）；S1 套件独立存在
#   T2-T5  schema v1/v2/unknown        → test_scene_system_s2a_schema.py
#   T6/T7  anchor referenced delete    → test_scene_system_s2a_integrity.py
#   T8  generate requires real Location → 本文件（直接构造 canonical 缺 location 的场景）
#   T9  invalid visible anchor         → test_scene_system_s1.py（T8）
#   T10 no fixed 8-direction           → test_scene_system_s1.py（T9）+ basis notes
#   T11-T14 basis/prompt/selection     → test_scene_system_s2a_basis.py
#   T15-T26 generation protocol        → test_scene_system_s2a_generation.py
#   T27/T28 review                     → test_scene_system_s2a_review.py
#   T29-T34 approval                   → test_scene_system_s2a_approval.py
#   T35 restart persistence（视觉状态）  → 本文件
#   T36 project.json byte unchanged    → 本文件
#   T37 resources schema unchanged     → 本文件
#   T38/T39 regression                 → 回归运行（report 记录）
#   HTTP 视觉协议冒烟                   → 本文件
import asyncio
import base64
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.resources.service import ResourceService
from src.resources.store import ResourceStore
from src.utils.project_manager import ProjectManager
from src.scene_system.models import GenerationStatus
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
from src.scene_system.visual_service import SceneVisualService
import routers.scene_system as rsc
import deps as deps_mod

PNG_B64 = base64.b64encode(b"\x89PNG fake-master-image").decode()


class FakeGen:
    models = {"image": "fake-image-model"}

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        return {"url": None, "b64": PNG_B64}


class FakeVision:
    def __init__(self, anchor_id: str):
        self.anchor_id = anchor_id

    async def chat_completion_vision(self, msgs, *, model):
        return {"content": json.dumps({
            "location_identity": "pass",
            "anchors": [{"anchor_id": self.anchor_id, "visibility": "clearly_visible",
                         "notes": ""}],
            "anchor_consistency": "pass", "layout_consistency": "pass",
            "view_consistency": "pass", "visual_defects": "pass",
            "project_style_consistency": "pass", "style_notes": "ok",
            "overall": "pass", "summary": "ok"}, ensure_ascii=False), "usage": {}}


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


async def _seed_location_view(svc: SceneSystemService, *, visible=True):
    async def drev():
        return svc.document_overview()["document_revision"]

    loc = (await svc.create_location(name="灯塔监听室",
                                     identity_description="circular control room",
                                     expected_document_revision=await drev()))["target"]
    a1 = (await svc.create_anchor(location_id=loc, name="north door", kind="door",
                                  expected_document_revision=await drev()))["target"]
    va = await svc.create_view(location_id=loc, label="hero", camera_zone="south_west",
                               look_direction="north_east",
                               visible_anchor_ids=[a1] if visible else [],
                               expected_document_revision=await drev())
    return {"loc": loc, "a1": a1, "vid": va["target"], "vrev": va["revision"]}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ============ T8：generate 要求真实 Location（canonical 直接缺 location 的场景） ============
def test_t8_generate_requires_real_location(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_location_view(svc))
    # 直接构造 canonical 缺 location 的状态（模拟手改/外部损坏；正常 API 删除会被
    # location_in_use 挡住，此处验证引擎自身的防御）
    async def break_canonical():
        st = SceneSystemStore(pdir, pdir.name)
        async with st.write_lock():
            d = st.load()
            del d.locations[ids["loc"]]
            st.save(d)
    asyncio.run(break_canonical())
    vs = SceneVisualService(pdir, pdir.name)
    r = asyncio.run(vs.generate_location_view(ids["vid"], expected_revision=ids["vrev"],
                                              candidate_count=1, agnes_client=FakeGen()))
    assert not r["ok"] and r["error"] == "location_not_found", r
    v = SceneSystemStore(pdir, pdir.name).load().views[ids["vid"]]
    assert v.generation_status == "none", "失败不改变状态机"


# ============ T35：重启持久化（视觉状态 + 证据流） ============
def test_t35_restart_persistence_visual_state(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_location_view(svc))
    vs = SceneVisualService(pdir, pdir.name)
    gen = asyncio.run(vs.generate_location_view(ids["vid"], expected_revision=ids["vrev"],
                                                candidate_count=2, seed=3,
                                                agnes_client=FakeGen()))
    assert gen["ok"], gen.get("error")
    rev_after = gen["revision_after"]
    # "重启"：全新 service 实例
    vs2 = SceneVisualService(pdir, pdir.name)
    svc2 = SceneSystemService(pdir, pdir.name)
    v = svc2.read_view(ids["vid"])
    assert v["generation_status"] == "ready"
    assert v["active_generation_id"] == gen["generation_id"]
    assert v["approval_status"] == "unreviewed"
    assert all((pdir / f).is_file() for f in gen["candidate_files"])
    # 视觉状态是 revision 的一部分（重读 revision 一致）
    from src.scene_system.models import view_revision
    assert view_revision(SceneSystemStore(pdir, pdir.name).load().views[ids["vid"]]) == rev_after
    # 续生成/审批链在"重启后"仍可走：review（新实例）成功
    from src.scene_system import view_review
    rv = asyncio.run(view_review.review_location_view_candidates(
        pdir, ids["vid"], agnes_client=FakeVision(ids["a1"])))
    assert rv["ok"] and rv["candidate_reviews"][0]["overall"] == "pass"


# ============ T36：project.json 字节不变（全视觉流程） ============
def test_t36_project_json_bytes_unchanged(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_location_view(svc))
    proj_before = _sha(pdir / "output" / "project.json")
    expected_res_rev = ResourceService(pdir, pdir.name).document_revision()

    async def flow():
        vs = SceneVisualService(pdir, pdir.name)
        gen = await vs.generate_location_view(ids["vid"], expected_revision=ids["vrev"],
                                              candidate_count=1, agnes_client=FakeGen())
        assert gen["ok"]
        from src.scene_system import view_review
        rv = await view_review.review_location_view_candidates(
            pdir, ids["vid"], agnes_client=FakeVision(ids["a1"]))
        assert rv["ok"]
        ap = await vs.approve_location_view(ids["vid"], candidate_index=0,
                                            expected_revision=gen["revision_after"],
                                            expected_resources_document_revision=expected_res_rev)
        assert ap["ok"], ap.get("error")
    asyncio.run(flow())
    assert _sha(pdir / "output" / "project.json") == proj_before, \
        "全视觉流程（generate/review/approve）project.json 字节不变（T36）"


# ============ T37：resources schema unchanged（approve 只追加 reference_assets 条目） ============
def test_t37_resources_schema_unchanged(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_location_view(svc))
    TOP_KEYS = {"schema_version", "looks", "clothings", "props", "compositions",
                "reference_assets"}

    async def flow():
        vs = SceneVisualService(pdir, pdir.name)
        gen = await vs.generate_location_view(ids["vid"], expected_revision=ids["vrev"],
                                              candidate_count=1, agnes_client=FakeGen())
        assert gen["ok"]
        # 生成/ready 阶段 resources.json 完全不变（§17 候选不进 Resources）
        res_after_gen = ResourceStore(pdir, pdir.name).load()
        assert set(res_after_gen.to_dict().keys()) == TOP_KEYS
        assert res_after_gen.reference_assets == {}
        from src.scene_system import view_review
        await view_review.review_location_view_candidates(
            pdir, ids["vid"], agnes_client=FakeVision(ids["a1"]))
        from src.scene_system.models import view_revision
        rev = view_revision(SceneSystemStore(pdir, pdir.name).load().views[ids["vid"]])
        from src.resources.service import ResourceService
        ap = await vs.approve_location_view(
            ids["vid"], candidate_index=0, expected_revision=rev,
            expected_resources_document_revision=ResourceService(pdir, pdir.name).document_revision())
        assert ap["ok"], ap.get("error")
    asyncio.run(flow())
    raw = json.loads((pdir / "output" / "resources.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2, "Resources schema_version 不被 Scene 流改动"
    assert set(raw.keys()) == TOP_KEYS, "Resources 顶层结构不变（T37）"
    assert len(raw["reference_assets"]) == 1, "只有 approval 注册了唯一 ReferenceAsset"
    asset = next(iter(raw["reference_assets"].values()))
    assert asset["purpose"] == "location_view" and asset["source"] == "scene_system"
    assert not any("base64" in json.dumps(v) for k, v in raw.items() if k == "reference_assets")


# ============ HTTP 视觉协议冒烟（status mapping + 全链） ============
@pytest.fixture
def client(tmp_path):
    pdir = make_pdir(tmp_path)
    orig = rsc.get_project_manager
    rsc.get_project_manager = lambda: ProjectManager(base_dir=str(tmp_path))
    app = FastAPI()
    app.include_router(rsc.router)
    saved = deps_mod.agnes_client
    deps_mod.agnes_client = FakeGen()
    try:
        yield TestClient(app), pdir
    finally:
        rsc.get_project_manager = orig
        deps_mod.agnes_client = saved


def _base(client: TestClient, pdir: Path) -> str:
    return f"/api/project/{pdir.name}/scene-system"


def _seed_via_http(client: TestClient, pdir: Path):
    c = client
    B = _base(c, pdir)
    doc_rev = c.get(f"{B}/overview").json()["document_revision"]
    loc = c.post(f"{B}/locations", json={"name": "灯塔监听室",
                                          "identity_description": "circular control room",
                                          "expected_document_revision": doc_rev}).json()["target"]
    doc_rev = c.get(f"{B}/overview").json()["document_revision"]
    aid = c.post(f"{B}/anchors", json={"location_id": loc, "name": "north door",
                                        "kind": "door",
                                        "expected_document_revision": doc_rev}).json()["target"]
    doc_rev = c.get(f"{B}/overview").json()["document_revision"]
    vr = c.post(f"{B}/views", json={"location_id": loc, "label": "hero",
                                     "camera_zone": "south_west",
                                     "look_direction": "north_east",
                                     "visible_anchor_ids": [aid],
                                     "expected_document_revision": doc_rev}).json()
    return {"loc": loc, "aid": aid, "vid": vr["target"], "vrev": vr["revision"]}


def test_http_visual_full_chain_and_status_mapping(client):
    c, pdir = client
    B = _base(c, pdir)
    ids = _seed_via_http(c, pdir)
    # generate 200
    r = c.post(f"{B}/views/{ids['vid']}/generate",
               json={"expected_revision": ids["vrev"], "candidate_count": 2})
    assert r.status_code == 200, r.text
    gen = r.json()
    assert gen["ok"] and gen["status"] == "committed" and len(gen["candidate_files"]) == 2
    # generate stale → 409
    r = c.post(f"{B}/views/{ids['vid']}/generate",
               json={"expected_revision": "deadbeef", "candidate_count": 1})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "stale_revision"
    # 单飞 → 409 generation_in_progress：先手工置 generating（模拟 in-flight）
    st = SceneSystemStore(pdir, pdir.name)

    async def set_generating():
        async with st.write_lock():
            d = st.load()
            d.views[ids["vid"]].generation_status = "generating"
            st.save(d)
    asyncio.run(set_generating())
    cur_rev = c.get(f"{B}/views/{ids['vid']}").json()["revision"]
    r = c.post(f"{B}/views/{ids['vid']}/generate",
               json={"expected_revision": cur_rev, "candidate_count": 1})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "generation_in_progress"

    async def reset_ready():
        async with st.write_lock():
            d = st.load()
            d.views[ids["vid"]].generation_status = "ready"
            st.save(d)
    asyncio.run(reset_ready())
    # reset_ready 改了 revision → 重新取当前值
    cur_rev = c.get(f"{B}/views/{ids['vid']}").json()["revision"]
    # review 前（无 pass 证据）approve → 409 review_pass_required
    res_rev = ResourceService(pdir, pdir.name).document_revision()
    r = c.post(f"{B}/views/{ids['vid']}/approve",
               json={"candidate_index": 0, "expected_revision": cur_rev,
                     "expected_resources_document_revision": res_rev})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "review_pass_required"
    # review 200（deps fake vision 需要 anchor id —— 换 FakeVision 实例）
    saved = deps_mod.agnes_client
    try:
        deps_mod.agnes_client = FakeVision(ids["aid"])
        r = c.post(f"{B}/views/{ids['vid']}/review")
        assert r.status_code == 200, r.text
        assert r.json()["candidate_reviews"][0]["overall"] == "pass"
        # approve 200
        r = c.post(f"{B}/views/{ids['vid']}/approve",
                   json={"candidate_index": 0, "expected_revision": cur_rev,
                         "expected_resources_document_revision": res_rev})
        assert r.status_code == 200, r.text
        asset_id = r.json()["asset_id"]
        # 重复 approve → 409 already_approved
        r = c.post(f"{B}/views/{ids['vid']}/approve",
                   json={"candidate_index": 0, "expected_revision": cur_rev,
                         "expected_resources_document_revision": res_rev})
        assert r.status_code == 409 and r.json()["detail"]["error"] == "already_approved"
    finally:
        deps_mod.agnes_client = saved
    # 证据流端点
    assert c.get(f"{B}/views/{ids['vid']}/generations").json()["generation_records"]
    assert c.get(f"{B}/views/{ids['vid']}/reviews").json()["review_records"]
    approvals = c.get(f"{B}/views/{ids['vid']}/approvals").json()["approval_records"]
    assert approvals and approvals[-1]["status"] == "approved"
    # revoke 200
    cur_rev2 = c.get(f"{B}/views/{ids['vid']}").json()["revision"]
    r = c.post(f"{B}/views/{ids['vid']}/revoke",
               json={"expected_revision": cur_rev2, "reason": "http smoke"})
    assert r.status_code == 200 and r.json()["approval_status"] == "rejected"
    # recovery 200（幂等零恢复）
    r = c.post(f"{B}/recovery/run")
    assert r.status_code == 200 and r.json()["recovered"] == []


def test_http_generate_candidate_count_validation(client):
    c, pdir = client
    B = _base(c, pdir)
    ids = _seed_via_http(c, pdir)
    r = c.post(f"{B}/views/{ids['vid']}/generate",
               json={"expected_revision": ids["vrev"], "candidate_count": 99})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_candidate_count"
