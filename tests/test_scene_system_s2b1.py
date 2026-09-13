# tests/test_scene_system_s2b1.py
# Scene System V1 — Phase S2B.1 五项专测（①②③ mock 部分；④真机、⑤回归另行执行）。
#
# ① stale peer 不得进入 generation（approved 但语义已漂移的 peer 被跳过并诚实记录）
# ② peer dependency 禁止成环（依赖链经 committed generation record 回到自身 → 排除）
# ③ later peer 不 retroactively stale earlier view（staleness no-peer 口径；
#    peer_reference_changed 不再是 stale 理由；regen 也不受 later peer 影响）
# + regen 使旧批准失效 → refresh cycle 中 stale peer 排除的组合行为
import asyncio
import base64
import json
import uuid
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.resources.models import Prop as ResourceProp, ResourceDocument, ReferenceAsset
from src.resources.store import ResourceStore
from src.scene_system.models import view_revision
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
from src.scene_system.visual_service import SceneVisualService
from src.scene_system import generation_records, instance_view_review as ivr
from src.scene_system.instance_view_basis import (
    _BasisInputError, compute_no_peer_basis, select_state_view_visual_refs)
from src.scene_system.generation_records import approval_records_path

PNG_B64 = base64.b64encode(b"\x89PNG s2b1-fake").decode()


class FakeGen:
    models = {"image": "fake-image-model"}

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        return {"url": None, "b64": PNG_B64}


def make_pdir(tmp_path: Path) -> Path:
    name = tmp_path.name
    pdir = tmp_path / name
    proj = Project(name=name)
    proj.scenes = [Scene(scene_id="s09", summary="x")]
    ProjectStore(pdir).save(proj)
    rdoc = ResourceDocument()
    rdoc.props["prop_sword01"] = ResourceProp(prop_id="prop_sword01", name="木剑")
    ResourceStore(pdir, pdir.name).save(rdoc)
    # P1：生成路径 profile-aware（fail closed on unspecified）——测试环境默认写实
    from src.visual_profile import VisualProfileService
    VisualProfileService(pdir, pdir.name).update(render_family="photorealistic",
                                                 expected_revision="")
    return pdir


def add_asset(pdir: Path, asset_id: str, local_rel: str):
    f = pdir / local_rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\x89PNG " + asset_id.encode())
    rdoc = ResourceStore(pdir, pdir.name).load()
    rdoc.reference_assets[asset_id] = ReferenceAsset(asset_id=asset_id, local=local_rel,
                                                     checksum="c" * 8, source="scene_system")
    ResourceStore(pdir, pdir.name).save(rdoc)


def _sciv_rev(pdir: Path, sciv_id: str) -> str:
    return view_revision(SceneSystemStore(pdir, pdir.name).load()
                         .scene_instance_views[sciv_id])


def _no_peer_fp(pdir: Path, sciv_id: str) -> str:
    """按该 sciv 最近 committed generation 的参数重算 no-peer 指纹（approve 同口径）。"""
    doc = SceneSystemStore(pdir, pdir.name).load()
    sciv = doc.scene_instance_views[sciv_id]
    inst = doc.scene_instances[sciv.scene_instance_id]
    loc = doc.locations[inst.location_id]
    bv = doc.views[sciv.location_view_id]
    gens = [r for r in generation_records.read_records(pdir, generation_id=sciv.active_generation_id)
            if r.get("operation") == "scene_instance_view.generate" and r.get("status") == "committed"]
    gb = gens[-1].get("basis") or {}
    from src.resources.store import ResourceStore as RS
    rdoc = RS(pdir, pdir.name).load()
    from src.visual_profile import load_profile_or_none as _lpon
    return compute_no_peer_basis(
        pdir, doc, rdoc, inst, loc, bv, sciv,
        model_id=(gb.get("model") or {}).get("model_id", ""),
        size_tier=(gb.get("model") or {}).get("size_tier", "1K"),
        ratio=(gb.get("model") or {}).get("ratio", "16:9"),
        seed=gb.get("seed"), candidate_count=gb.get("candidate_count", 2),
        visual_profile=_lpon(pdir),
    )["basis_fingerprint"]


async def simulate_approval(pdir: Path, sciv_id: str):
    """测试捷径：为已 generate 的 sciv 补批准态（approved + no-peer 指纹锚点 +
    真实可读的 reference asset——peer 资格检查要求资产可读）。"""
    fp = _no_peer_fp(pdir, sciv_id)
    asset_id = f"ref_sim_{sciv_id[-8:]}"
    local = f"output/scene_system/scene_instances/sim/{sciv_id}/c_01.png"
    f = pdir / local
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\x89PNG sim-" + sciv_id.encode())
    rdoc = ResourceStore(pdir, pdir.name).load()
    rdoc.reference_assets[asset_id] = ReferenceAsset(asset_id=asset_id, local=local,
                                                     checksum="c" * 8, source="scene_system")
    ResourceStore(pdir, pdir.name).save(rdoc)
    st = SceneSystemStore(pdir, pdir.name)

    async with st.write_lock():
        d = st.load()
        v = d.scene_instance_views[sciv_id]
        v.approval_status = "approved"
        v.approved_basis_fingerprint = fp
        v.reference_asset_id = asset_id
        st.save(d)
    return fp


async def _seed_world(svc: SceneSystemService, vs: SceneVisualService, pdir: Path):
    """location + 双向 view（A: 可见 w2；B: 可见 stairs）+ instance + 状态 + 资产。"""
    async def drev():
        return svc.document_overview()["document_revision"]

    loc = (await svc.create_location(name="灯塔监听室",
                                     identity_description="circular control room",
                                     expected_document_revision=await drev()))["target"]
    a_w2 = (await svc.create_anchor(location_id=loc, name="west window 02", kind="window",
                                    expected_document_revision=await drev()))["target"]
    a_s = (await svc.create_anchor(location_id=loc, name="south stairs", kind="stairs",
                                   expected_document_revision=await drev()))["target"]
    v_a = await svc.create_view(location_id=loc, label="A", visible_anchor_ids=[a_w2],
                                expected_document_revision=await drev())
    v_b = await svc.create_view(location_id=loc, label="B", visible_anchor_ids=[a_s],
                                expected_document_revision=await drev())
    inst = (await svc.create_scene_instance(story_scene_id="s09", location_id=loc,
                                            expected_document_revision=await drev()))["target"]
    rp = await svc.patch_scene_state(
        inst, {"time_of_day": "night", "weather": "storm", "lighting": "red emergency",
               "anchor_states": {a_w2: {"condition": "broken"}},
               "prop_placements": {"prop_sword01": {"anchor_id": a_w2, "state": "intact"}}},
        expected_revision=svc.read_scene_instance(inst)["revision"])
    assert rp["ok"], rp
    # 两个 base views approved（捷径 + approval records 供 check_base）
    for vid in (v_a["target"], v_b["target"]):
        asset = f"ref_base_{vid[-8:]}"
        add_asset(pdir, asset,
                  f"output/scene_system/locations/{loc}/views/{vid}/g/c_01.png")
        st = SceneSystemStore(pdir, pdir.name)
        async with st.write_lock():
            d = st.load()
            d.views[vid].approval_status = "approved"
            d.views[vid].reference_asset_id = asset
            st.save(d)
        from test_scene_system_s2b_generation import approval_record_fingerprint
        doc = st.load()
        fp2 = approval_record_fingerprint(pdir, doc, doc.views[vid], [asset])
        generation_records.append_record(pdir, {
            "approval_id": "apv_s", "operation": "scene.approve_location_view",
            "status": "approved", "view_id": vid, "asset_id": asset,
            "reference_asset_ids_used": [asset],
            "reviewed_basis_fingerprint": fp2, "completed_at": 0.0,
        }, path=approval_records_path(pdir))
    return {"loc": loc, "a_w2": a_w2, "a_s": a_s,
            "v_a": v_a["target"], "v_b": v_b["target"], "inst": inst}


async def _make_state_view(svc, vs, pdir, ids, key, base_vid):
    r = await vs.create_scene_instance_view(scene_instance_id=ids["inst"],
                                            location_view_id=base_vid,
                                            expected_document_revision=svc.document_overview()["document_revision"])
    assert r["ok"], r
    gen = await vs.generate_scene_instance_view(
        r["target"], expected_revision=_sciv_rev(pdir, r["target"]),
        candidate_count=1, agnes_client=FakeGen(), include_peer=True)
    assert gen["ok"], gen.get("error")
    return r["target"], gen


# ============ ① stale peer 不得进入 generation ============
# （"current peer 正常进入"的正面断言在 test_2 流程内：B 生成时 peer=A 进入）
def test_1b_stale_peer_skipped_with_note(tmp_path):
    """直接构造：A stale（lighting 变更后未 regen），B 生成时 peer=A 被跳过 + note。"""
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_world(svc, vs, pdir))

    async def flow():
        sciv_a, _ = await _make_state_view(svc, vs, pdir, ids, "A", ids["v_a"])
        await simulate_approval(pdir, sciv_a)
        # lighting 变更 → A stale
        rev = svc.read_scene_instance(ids["inst"])["revision"]
        await svc.patch_scene_state(ids["inst"], {"lighting": "normal white"},
                                    expected_revision=rev)
        st_a = vs.read_scene_instance_view_status(sciv_a)
        assert st_a["is_stale"] is True
        # B 生成：peer 候选=A（approved 但 stale）→ 排除 + 诚实 note
        r = await vs.create_scene_instance_view(
            scene_instance_id=ids["inst"], location_view_id=ids["v_b"],
            expected_document_revision=svc.document_overview()["document_revision"])
        assert r["ok"], r
        gen = await vs.generate_scene_instance_view(
            r["target"], expected_revision=_sciv_rev(pdir, r["target"]),
            candidate_count=1, agnes_client=FakeGen(), include_peer=True)
        assert gen["ok"], gen.get("error")
        assert gen["basis"].get("peer_state_reference") is None, \
            "stale peer 绝不进入 generation 参考"
        assert any(n.startswith("peer_skipped_stale:") for n in gen["basis"]["selection_notes"]), \
            gen["basis"]["selection_notes"]
    asyncio.run(flow())


# ============ ② peer dependency 禁止成环 ============
def test_2_cycle_peer_excluded(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_world(svc, vs, pdir))

    async def flow():
        # A：生成（include_peer=True，无 peer）+ 批准
        sciv_a, _ = await _make_state_view(svc, vs, pdir, ids, "A", ids["v_a"])
        await simulate_approval(pdir, sciv_a)
        # B：生成 → peer=A 进入；批准
        sciv_b, gen_b = await _make_state_view(svc, vs, pdir, ids, "B", ids["v_b"])
        assert (gen_b["basis"].get("peer_state_reference") or {}).get(
            "scene_instance_view_id") == sciv_a
        await simulate_approval(pdir, sciv_b)
        # regen A（include_peer=True）：peer 候选=B；
        # B 的 generation 依赖链用了 peer=A == 目标自身 → 成环 → 排除
        gen_a2 = await vs.generate_scene_instance_view(
            sciv_a, expected_revision=_sciv_rev(pdir, sciv_a),
            candidate_count=1, agnes_client=FakeGen(), include_peer=True)
        assert gen_a2["ok"], gen_a2.get("error")
        assert gen_a2["basis"].get("peer_state_reference") is None, \
            "成环 peer（B 依赖 A）绝不进入 A 的 generation"
        assert any(n.startswith("peer_skipped_cycle:") for n in gen_a2["basis"]["selection_notes"]), \
            gen_a2["basis"]["selection_notes"]
    asyncio.run(flow())


# ============ ③ later peer 不 retroactively stale earlier view ============
def test_3_later_peer_does_not_retroactively_stale(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_world(svc, vs, pdir))

    async def flow():
        # A：生成（include_peer=True 但当时无 peer 可用）+ 批准
        sciv_a, _ = await _make_state_view(svc, vs, pdir, ids, "A", ids["v_a"])
        await simulate_approval(pdir, sciv_a)
        st_a0 = vs.read_scene_instance_view_status(sciv_a)
        assert st_a0["is_stale"] is False
        # 后来：B 创建 + 生成（peer=A）+ 批准
        sciv_b, gen_b = await _make_state_view(svc, vs, pdir, ids, "B", ids["v_b"])
        assert (gen_b["basis"].get("peer_state_reference") or {}).get(
            "scene_instance_view_id") == sciv_a
        await simulate_approval(pdir, sciv_b)
        # 🔴 核心断言：later peer（B）绝不追溯 stale A（§14/§33；r9 曾实证该缺口）
        st_a1 = vs.read_scene_instance_view_status(sciv_a)
        assert st_a1["is_stale"] is False, \
            f"later peer 不追溯 stale：{st_a1['stale_reasons']}"
        assert not any("peer" in r for r in st_a1["stale_reasons"])
        # 控制：真实语义变化仍使 A stale
        rev = svc.read_scene_instance(ids["inst"])["revision"]
        await svc.patch_scene_state(ids["inst"], {"lighting": "normal white"},
                                    expected_revision=rev)
        st_a2 = vs.read_scene_instance_view_status(sciv_a)
        assert st_a2["is_stale"] is True
        assert any(r.startswith("global_state_changed") for r in st_a2["stale_reasons"])
        assert not any("peer" in r for r in st_a2["stale_reasons"]), \
            "peer_reference_changed 不再是 stale 理由（S2B.1-③）"
    asyncio.run(flow())


# ============ 组合：refresh cycle 中 stale peer 排除（④的 mock 前置） ============
def test_refresh_cycle_stale_peer_excluded_and_regenerated(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_world(svc, vs, pdir))

    async def flow():
        # A/B 双批准（B 用 peer=A）
        sciv_a, _ = await _make_state_view(svc, vs, pdir, ids, "A", ids["v_a"])
        await simulate_approval(pdir, sciv_a)
        sciv_b, _ = await _make_state_view(svc, vs, pdir, ids, "B", ids["v_b"])
        await simulate_approval(pdir, sciv_b)
        # lighting 变更 → 双 stale
        rev = svc.read_scene_instance(ids["inst"])["revision"]
        await svc.patch_scene_state(ids["inst"], {"lighting": "normal white"},
                                    expected_revision=rev)
        assert vs.read_scene_instance_view_status(sciv_a)["is_stale"]
        assert vs.read_scene_instance_view_status(sciv_b)["is_stale"]
        # regen A：peer 候选=B（stale）→ 排除 → 正常 commit
        gen = await vs.generate_scene_instance_view(
            sciv_a, expected_revision=_sciv_rev(pdir, sciv_a),
            candidate_count=1, agnes_client=FakeGen(), include_peer=True)
        assert gen["ok"], gen.get("error")
        assert gen["basis"].get("peer_state_reference") is None
        # regen 使旧批准失效（生成绝不自动 approve）
        v = SceneSystemStore(pdir, pdir.name).load().scene_instance_views[sciv_a]
        assert v.approval_status == "unreviewed" and v.generation_status == "ready"
        # review + approve → A 复鲜；B 仍 stale
        reply = json.dumps({
            "location_identity": "pass", "view_consistency": "pass",
            "anchor_consistency": "pass",
            "scene_state": {"time_of_day": "pass", "weather": "pass",
                            "environment_conditions": "pass",
                            "anchor_states": [{"anchor_id": ids["a_w2"], "verdict": "pass",
                                               "notes": ""}], "notes": ""},
            "lighting_weather": "pass",
            "props": [{"prop_id": "prop_sword01", "observed": "correct",
                       "verdict": "pass", "notes": ""}],
            "visual_defects": "pass",
            "project_style_consistency": "pass", "style_notes": "ok",
            "overall": "pass", "summary": "ok"},
            ensure_ascii=False)

        class FakeV:
            async def chat_completion_vision(self, msgs, *, model):
                return {"content": reply, "usage": {}}
        rr = await ivr.review_scene_instance_view_candidates(pdir, sciv_a, agnes_client=FakeV())
        assert rr["ok"] and rr["candidate_reviews"][0]["overall"] == "pass", rr
        from src.resources.service import ResourceService
        ap = await vs.approve_scene_instance_view(
            sciv_a, candidate_index=0, expected_revision=_sciv_rev(pdir, sciv_a),
            expected_resources_document_revision=ResourceService(pdir, pdir.name).document_revision())
        assert ap["ok"], ap.get("error")
        st_a = vs.read_scene_instance_view_status(sciv_a)
        st_b = vs.read_scene_instance_view_status(sciv_b)
        assert st_a["is_stale"] is False, "A 复鲜"
        assert st_b["is_stale"] is True, "B 仍 stale"
    asyncio.run(flow())
