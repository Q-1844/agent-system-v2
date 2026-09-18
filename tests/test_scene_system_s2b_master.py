# tests/test_scene_system_s2b_master.py
# Scene System V1 — Phase S2B 主验收补充（§39 T42-T44/T47 + HTTP 全链）。
#
# T42 restart persistence（SceneInstanceView 视觉状态跨实例）
# T43 project.json unchanged（全视觉流程）
# T44 resources schema unchanged（approve 只追加唯一条目）
# T47 Agent source zero modification（src/agents、src/ai 无 scene_system 引用）
# + HTTP S2B 全链冒烟（status mapping）
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
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
from src.scene_system.visual_service import SceneVisualService
import routers.scene_system as rsc
import deps as deps_mod

sys_path = Path(__file__).parent
import sys
sys.path.insert(0, str(sys_path))
from test_scene_system_s2b_generation import (  # noqa: E402
    make_pdir, FakeClient, _seed, _sciv_rev,
)

PNG_B64 = base64.b64encode(b"\x89PNG s2b-master").decode()


class FakeVision:
    def __init__(self, content: str):
        self.content = content

    async def chat_completion_vision(self, msgs, *, model):
        return {"content": self.content, "usage": {}}


def _pass_reply(anchor_id: str, prop_id: str) -> str:
    return json.dumps({
        "location_identity": "pass", "view_consistency": "pass",
        "anchor_consistency": "pass",
        "scene_state": {"time_of_day": "pass", "weather": "pass",
                        "environment_conditions": "pass",
                        "anchor_states": [{"anchor_id": anchor_id, "verdict": "pass",
                                           "notes": ""}], "notes": ""},
        "lighting_weather": "pass",
        "props": [{"prop_id": prop_id, "observed": "correct", "verdict": "pass",
                   "notes": ""}],
        "visual_defects": "pass",
        "project_style_consistency": "pass", "style_notes": "ok",
        "overall": "pass", "summary": "ok"},
        ensure_ascii=False)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ============ T42：restart persistence ============
def test_t42_restart_persistence(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed(svc, pdir, pdir.name))
    gen = asyncio.run(vs.generate_scene_instance_view(
        ids["sciv"], expected_revision=_sciv_rev(pdir, ids["sciv"]),
        candidate_count=2, agnes_client=FakeClient(), include_peer=False))
    assert gen["ok"], gen.get("error")
    # "重启"：全新实例
    vs2 = SceneVisualService(pdir, pdir.name)
    got = vs2.read_scene_instance_view(ids["sciv"])
    assert got["generation_status"] == "ready"
    assert got["active_generation_id"] == gen["generation_id"]
    assert all((pdir / f).is_file() for f in gen["candidate_files"])
    # 续链：重启后 review 可走
    from src.scene_system import instance_view_review as ivr
    rr = asyncio.run(ivr.review_scene_instance_view_candidates(
        pdir, ids["sciv"], agnes_client=FakeVision(_pass_reply(ids["a_w2"], "prop_sword01"))))
    assert rr["ok"] and rr["candidate_reviews"][0]["overall"] == "pass"


# ============ T43：project.json unchanged ============
def test_t43_project_json_unchanged(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed(svc, pdir, pdir.name))
    before = _sha(pdir / "output" / "project.json")
    from src.scene_system import instance_view_review as ivr
    gen = asyncio.run(vs.generate_scene_instance_view(
        ids["sciv"], expected_revision=_sciv_rev(pdir, ids["sciv"]),
        candidate_count=1, agnes_client=FakeClient(), include_peer=False))
    assert gen["ok"]
    asyncio.run(ivr.review_scene_instance_view_candidates(
        pdir, ids["sciv"], agnes_client=FakeVision(_pass_reply(ids["a_w2"], "prop_sword01"))))
    rev = _sciv_rev(pdir, ids["sciv"])
    ap = asyncio.run(vs.approve_scene_instance_view(
        ids["sciv"], candidate_index=0, expected_revision=rev,
        expected_resources_document_revision=ResourceService(pdir, pdir.name).document_revision()))
    assert ap["ok"], ap.get("error")
    assert _sha(pdir / "output" / "project.json") == before, "T43：全流程 project.json 字节不变"


# ============ T44：resources schema unchanged ============
def test_t44_resources_schema_unchanged(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed(svc, pdir, pdir.name))
    TOP = {"schema_version", "looks", "clothings", "props", "compositions", "reference_assets"}
    from src.scene_system import instance_view_review as ivr
    gen = asyncio.run(vs.generate_scene_instance_view(
        ids["sciv"], expected_revision=_sciv_rev(pdir, ids["sciv"]),
        candidate_count=1, agnes_client=FakeClient(), include_peer=False))
    assert gen["ok"]
    # ready 阶段：resources 无 scene_instance_view 资产（候选不进 Resources；
    # base 夹具资产 source=scene_system 属测试捷径，purpose 不是 scene_instance_view）
    rdoc = ResourceStore(pdir, pdir.name).load()
    assert set(rdoc.to_dict().keys()) == TOP
    assert not [a for a in rdoc.reference_assets.values()
                if a.purpose == "scene_instance_view"]
    asyncio.run(ivr.review_scene_instance_view_candidates(
        pdir, ids["sciv"], agnes_client=FakeVision(_pass_reply(ids["a_w2"], "prop_sword01"))))
    ap = asyncio.run(vs.approve_scene_instance_view(
        ids["sciv"], candidate_index=0, expected_revision=_sciv_rev(pdir, ids["sciv"]),
        expected_resources_document_revision=ResourceService(pdir, pdir.name).document_revision()))
    assert ap["ok"], ap.get("error")
    raw = json.loads((pdir / "output" / "resources.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    assert set(raw.keys()) == TOP, "T44：Resources schema/顶层结构不变"
    assets = [a for a in raw["reference_assets"].values()
              if a["purpose"] == "scene_instance_view"]
    assert len(assets) == 1, "approve 只注册唯一 scene_instance_view 资产（base 夹具资产除外）"
    assert assets[0]["source"] == "scene_system"


# ============ T47：Agent source zero modification ============
def test_t47_no_agent_source_references_scene_system():
    """Phase C2 演进版（28.md §0.3）：scene_system 引用只允许
    production_context.py（共享只读投影层）+ scene_designer.py（Scene System 唯一
    合法写入者）+ project_events.py（锁/事件域常量）——
    详见 test_scene_system_s1.T26 同款边界。"""
    root = Path(__file__).resolve().parent.parent
    allowed = {"production_context.py", "scene_designer.py", "project_events.py",
               "producer.py"}
    for rel in ("src/agents", "src/ai"):
        pkg = root / rel
        assert pkg.exists()
        for py in pkg.rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="ignore")
            if "scene_system" not in text:
                continue
            assert py.name in allowed, f"Agent 源出现 scene_system 引用: {py}"


# ============ HTTP S2B 全链冒烟 ============
@pytest.fixture
def client(tmp_path):
    pdir = make_pdir(tmp_path)
    orig = rsc.get_project_manager
    rsc.get_project_manager = lambda: ProjectManager(base_dir=str(tmp_path))
    app = FastAPI()
    app.include_router(rsc.router)
    saved = deps_mod.agnes_client
    deps_mod.agnes_client = FakeClient()
    try:
        yield TestClient(app), pdir
    finally:
        rsc.get_project_manager = orig
        deps_mod.agnes_client = saved


def test_http_s2b_full_chain(client):
    c, pdir = client
    B = f"/api/project/{pdir.name}/scene-system"
    # S2B 场景搭建（走 HTTP）
    doc_rev = c.get(f"{B}/overview").json()["document_revision"]
    loc = c.post(f"{B}/locations", json={"name": "灯塔监听室",
                                          "identity_description": "circular control room",
                                          "expected_document_revision": doc_rev}).json()["target"]
    doc_rev = c.get(f"{B}/overview").json()["document_revision"]
    a1 = c.post(f"{B}/anchors", json={"location_id": loc, "name": "north door", "kind": "door",
                                       "expected_document_revision": doc_rev}).json()["target"]
    doc_rev = c.get(f"{B}/overview").json()["document_revision"]
    v1 = c.post(f"{B}/views", json={"location_id": loc, "label": "N to S",
                                     "visible_anchor_ids": [a1],
                                     "expected_document_revision": doc_rev}).json()
    # 预置 approved base（捷径：直置 + approval record——正式审批链在 S2A 套件覆盖）
    st = SceneSystemStore(pdir, pdir.name)

    async def approve_base():
        f = pdir / "output" / "scene_system" / "locations" / loc / "views" / v1["target"] / "g" / "c_01.png"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\x89PNG base-ref")
        async with st.write_lock():
            d = st.load()
            d.views[v1["target"]].approval_status = "approved"
            d.views[v1["target"]].reference_asset_id = "ref_baseHTTP0001"
            st.save(d)
        from test_scene_system_s2b_generation import approval_record_fingerprint
        doc = st.load()
        fp = approval_record_fingerprint(pdir, doc, doc.views[v1["target"]],
                                         ["ref_baseHTTP0001"])
        from src.scene_system.generation_records import approval_records_path
        from src.scene_system import generation_records as gr
        gr.append_record(pdir, {"approval_id": "apv_h", "operation": "scene.approve_location_view",
                                "status": "approved", "view_id": v1["target"],
                                "asset_id": "ref_baseHTTP0001",
                                "reviewed_basis_fingerprint": fp, "completed_at": 0.0},
                         path=approval_records_path(pdir))
    asyncio.run(approve_base())
    rdoc = ResourceStore(pdir, pdir.name).load()
    from src.resources.models import ReferenceAsset
    rdoc.reference_assets["ref_baseHTTP0001"] = ReferenceAsset(
        asset_id="ref_baseHTTP0001",
        local=f"output/scene_system/locations/{loc}/views/{v1['target']}/g/c_01.png",
        checksum="c" * 8, source="scene_system")
    ResourceStore(pdir, pdir.name).save(rdoc)
    doc_rev = c.get(f"{B}/overview").json()["document_revision"]
    inst = c.post(f"{B}/scene-instances", json={"story_scene_id": "s09", "location_id": loc,
                                                 "expected_document_revision": doc_rev}).json()["target"]
    # location mismatch → 422
    doc_rev = c.get(f"{B}/overview").json()["document_revision"]
    loc2 = c.post(f"{B}/locations", json={"name": "其它地点",
                                           "expected_document_revision": doc_rev}).json()["target"]
    doc_rev = c.get(f"{B}/overview").json()["document_revision"]
    v2 = c.post(f"{B}/views", json={"location_id": loc2, "label": "x",
                                     "expected_document_revision": doc_rev}).json()
    r = c.post(f"{B}/scene-instance-views", json={
        "scene_instance_id": inst, "location_view_id": v2["target"],
        "expected_document_revision": c.get(f"{B}/overview").json()["document_revision"]})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "location_mismatch"
    # 正创建
    doc_rev = c.get(f"{B}/overview").json()["document_revision"]
    r = c.post(f"{B}/scene-instance-views", json={
        "scene_instance_id": inst, "location_view_id": v1["target"],
        "expected_document_revision": doc_rev})
    assert r.status_code == 200, r.text
    sciv = r.json()["target"]
    # generate
    r = c.post(f"{B}/scene-instance-views/{sciv}/generate",
               json={"expected_revision": r.json()["revision"], "candidate_count": 1})
    assert r.status_code == 200, r.text
    gen = r.json()
    # review（fake vision——deps fake 无 vision 方法，直接 monkeypatch get_agnes_client 路径：
    # review 惰性 import deps.get_agnes_client → 返回 deps.agnes_client）
    class FakeV:
        async def chat_completion_vision(self, msgs, *, model):
            return {"content": _pass_reply(a1, "prop_sword01"), "usage": {}}
    saved2 = deps_mod.agnes_client
    try:
        deps_mod.agnes_client = FakeV()
        r = c.post(f"{B}/scene-instance-views/{sciv}/review")
        assert r.status_code == 200, r.text
        assert r.json()["candidate_reviews"][0]["overall"] == "pass"
    finally:
        deps_mod.agnes_client = saved2
    # approve
    res_rev = ResourceService(pdir, pdir.name).document_revision()
    r = c.post(f"{B}/scene-instance-views/{sciv}/approve",
               json={"candidate_index": 0, "expected_revision": gen["revision_after"],
                     "expected_resources_document_revision": res_rev})
    assert r.status_code == 200, r.text
    # read 模型：is_stale=False + 投影 + base view
    d = c.get(f"{B}/scene-instance-views/{sciv}").json()
    assert d["is_stale"] is False and d["base_location_view"]["view_id"] == v1["target"]
    assert "view_relevant_scene_state" in d
    # lighting 变化 → is_stale=True + reasons
    rev_inst = c.get(f"{B}/scene-instances/{inst}").json()["revision"]
    r = c.patch(f"{B}/scene-instances/{inst}/state",
                json={"lighting": "normal white", "expected_revision": rev_inst})
    assert r.status_code == 200
    d2 = c.get(f"{B}/scene-instance-views/{sciv}").json()
    assert d2["is_stale"] is True
    assert any(x.startswith("global_state_changed") for x in d2["stale_reasons"])
    # list
    lst = c.get(f"{B}/scene-instance-views", params={"scene_instance_id": inst}).json()
    assert len(lst["scene_instance_views"]) == 1
