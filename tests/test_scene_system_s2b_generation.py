# tests/test_scene_system_s2b_generation.py
# Scene System V1 — Phase S2B：生成引擎测试（§39 T19-T31 的链路部分 + §12/§2 fail closed）。
#
# T19 provider outside lock（锁探针）
# T20 same state view single-flight
# T21 different state views concurrent
# T22 provider failure honest
# T23 global state change → stale commit 拒绝（含 approved 后 lighting 变化链路）
# T24 visible anchor state change → stale
# T26 visible prop change → stale
# T27 base location ref change → stale
# T28 cancellation cleanup
# T29 orphan startup recovery（scene_instance_views）
# T30 recovery idempotent
# T31 generation not auto approved
# §12 revoked/stale base 拒绝（T9/T10/T11 的生成链路部分）
# §2.D missing visible prop → prop_missing fail closed
import asyncio
import base64
from dataclasses import replace
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.resources.models import Prop as ResourceProp, ResourceDocument, ReferenceAsset
from src.resources.store import ResourceStore
from src.scene_system.models import GenerationStatus, view_revision
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
from src.scene_system.visual_service import SceneVisualService
from src.scene_system import generation_records
from src.scene_system.recovery import recover_orphaned_view_generations

PNG_B64 = base64.b64encode(b"\x89PNG s2b-fake-image").decode()


class FakeClient:
    models = {"image": "fake-image-model"}

    def __init__(self, delay: float = 0.0, fail: bool = False, pre_call=None):
        self.delay = delay
        self.fail = fail
        self.pre_call = pre_call
        self.calls = []
        self.lock_probe = []

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        self.calls.append({"prompt": prompt, "n_refs": len(image_refs or []), "seed": seed})
        if self.pre_call is not None:
            await self.pre_call()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("provider exploded")
        return {"url": None, "b64": PNG_B64}


def make_pdir(tmp_path: Path) -> Path:
    name = tmp_path.name
    pdir = tmp_path / name
    proj = Project(name=name)
    proj.scenes = [Scene(scene_id="s09", summary="x")]
    ProjectStore(pdir).save(proj)
    rdoc = ResourceDocument()
    for pid in ("prop_sword01", "prop_hidden01"):
        rdoc.props[pid] = ResourceProp(prop_id=pid, name=pid)
    ResourceStore(pdir, pdir.name).save(rdoc)
    # P1：生成路径 profile-aware（fail closed on unspecified）——测试环境默认写实
    from src.visual_profile import VisualProfileService
    VisualProfileService(pdir, pdir.name).update(render_family="photorealistic",
                                                 expected_revision="")
    return pdir


async def _seed(svc: SceneSystemService, pdir: Path, tmp_name: str):
    """location + anchors + approved base view（含 approval record）+ instance + sciv。"""
    async def drev():
        return svc.document_overview()["document_revision"]

    loc = (await svc.create_location(name="灯塔监听室",
                                     identity_description="circular control room",
                                     expected_document_revision=await drev()))["target"]
    a_n = (await svc.create_anchor(location_id=loc, name="north door", kind="door",
                                   expected_document_revision=await drev()))["target"]
    a_w2 = (await svc.create_anchor(location_id=loc, name="west window 02", kind="window",
                                    expected_document_revision=await drev()))["target"]
    a_s = (await svc.create_anchor(location_id=loc, name="south stairs", kind="stairs",
                                   expected_document_revision=await drev()))["target"]
    v_north = await svc.create_view(location_id=loc, label="North to South",
                                    visible_anchor_ids=[a_n, a_w2],
                                    expected_document_revision=await drev())
    v_south = await svc.create_view(location_id=loc, label="South to North",
                                    visible_anchor_ids=[a_s],
                                    expected_document_revision=await drev())
    inst = (await svc.create_scene_instance(story_scene_id="s09", location_id=loc,
                                            expected_document_revision=await drev()))["target"]
    rp = await svc.patch_scene_state(
        inst, {"time_of_day": "night", "weather": "storm", "lighting": "red emergency",
               "anchor_states": {a_w2: {"condition": "broken"}},
               "prop_placements": {"prop_sword01": {"anchor_id": a_n, "state": "intact"}}},
        expected_revision=svc.read_scene_instance(inst)["revision"])
    assert rp["ok"], rp
    vs = SceneVisualService(pdir, tmp_name)
    r = await vs.create_scene_instance_view(scene_instance_id=inst,
                                            location_view_id=v_north["target"],
                                            expected_document_revision=await drev())
    assert r["ok"], r
    sciv = r["target"]
    r2 = await vs.create_scene_instance_view(scene_instance_id=inst,
                                             location_view_id=v_south["target"],
                                             expected_document_revision=r["document_revision_after"])
    assert r2["ok"], r2
    sciv_b = r2["target"]
    # base approved（捷径 + approval record 供 §2.B 校验）
    add_asset(pdir, "ref_base0000001",
              f"output/scene_system/locations/{loc}/views/{v_north['target']}/g/c_01.png")
    from src.scene_system.view_review import build_review_expectations, expectations_fingerprint
    from src.scene_system.generation_records import approval_records_path
    st = SceneSystemStore(pdir, tmp_name)
    async with st.write_lock():
        d = st.load()
        d.views[v_north["target"]].approval_status = "approved"
        d.views[v_north["target"]].reference_asset_id = "ref_base0000001"
        st.save(d)
    doc = st.load()
    fp = approval_record_fingerprint(pdir, doc, doc.views[v_north["target"]],
                                     ["ref_base0000001"])
    from src.scene_system import generation_records as gr
    gr.append_record(pdir, {"approval_id": "apv_t", "operation": "scene.approve_location_view",
                            "status": "approved", "view_id": v_north["target"],
                            "asset_id": "ref_base0000001", "reviewed_basis_fingerprint": fp,
                            "completed_at": 0.0}, path=approval_records_path(pdir))
    return {"loc": loc, "a_n": a_n, "a_w2": a_w2, "a_s": a_s,
            "v_north": v_north["target"], "v_south": v_south["target"],
            "inst": inst, "sciv": sciv, "sciv_b": sciv_b}


def add_asset(pdir: Path, asset_id: str, local_rel: str):
    f = pdir / local_rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\x89PNG " + asset_id.encode())
    rdoc = ResourceStore(pdir, pdir.name).load()
    rdoc.reference_assets[asset_id] = ReferenceAsset(asset_id=asset_id, local=local_rel,
                                                     checksum="c" * 8, source="scene_system")
    ResourceStore(pdir, pdir.name).save(rdoc)


def _sciv_rev(pdir: Path, sciv_id: str) -> str:
    return view_revision(SceneSystemStore(pdir, pdir.name).load().scene_instance_views[sciv_id])


def approval_record_fingerprint(pdir: Path, doc, view, asset_ids) -> str:
    """快捷批准记录的期望指纹——与真实 review/approve 链**同口径**
    （P1 §15：期望指纹含 visual_profile_fingerprint；unspecified → None）。
    Golden Path blocker（2026-09-13）回归锁定的一部分：真实链 review+approve 写入的
    记录含 profile 维度；快捷批准若仍用无 profile 旧口径，修复后的
    check_base_location_view 会（正确地）把这类记录判 location_view_stale。"""
    from src.scene_system.view_review import (build_review_expectations,
                                              expectations_fingerprint)
    from src.visual_profile import load_profile_or_none
    from src.visual_profile.models import visual_profile_fingerprint
    vp = load_profile_or_none(pdir)
    return expectations_fingerprint(
        build_review_expectations(doc, view), list(asset_ids),
        visual_profile_fingerprint=(visual_profile_fingerprint(vp)
                                    if vp is not None else None))


# ============ §12 fail closed 家族 ============
def test_generate_requires_approved_base(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc, pdir, pdir.name)
        # revoke base（经协议）
        rev = svc.read_view(ids["v_north"])["revision"]
        rr = await vs.revoke_view_approval(ids["v_north"], expected_revision=rev, reason="t")
        assert rr["ok"]
        r = await vs.generate_scene_instance_view(
            ids["sciv"], expected_revision=_sciv_rev(pdir, ids["sciv"]),
            candidate_count=1, agnes_client=FakeClient())
        assert not r["ok"] and r["error"] == "location_view_not_approved", r.get("error")
        # 恢复 approved 但语义已变化（§2.B stale base）：
        # 直置 approved + 旧 approval record 指纹已不匹配（view 定义被改过）
        st = SceneSystemStore(pdir, pdir.name)
        async with st.write_lock():
            d = st.load()
            d.views[ids["v_north"]].approval_status = "approved"
            st.save(d)
        r2 = await vs.generate_scene_instance_view(
            ids["sciv"], expected_revision=_sciv_rev(pdir, ids["sciv"]),
            candidate_count=1, agnes_client=FakeClient())
        assert not r2["ok"] and r2["error"] == "location_view_not_approved", r2.get("error")
    asyncio.run(flow())


def test_generate_missing_visible_prop_fail_closed(tmp_path):
    """§2.D：可见 prop 从 Resources 删除 → prop_missing，绝不假装输入完整。"""
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc, pdir, pdir.name)
        rdoc = ResourceStore(pdir, pdir.name).load()
        del rdoc.props["prop_sword01"]
        ResourceStore(pdir, pdir.name).save(rdoc)
        r = await vs.generate_scene_instance_view(
            ids["sciv"], expected_revision=_sciv_rev(pdir, ids["sciv"]),
            candidate_count=1, agnes_client=FakeClient())
        assert not r["ok"] and r["error"] == "prop_missing", r.get("error")
        v = SceneSystemStore(pdir, pdir.name).load().scene_instance_views[ids["sciv"]]
        assert v.generation_status == "none"
    asyncio.run(flow())


def test_generate_corrupt_resources_fail_closed(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc, pdir, pdir.name)
        (pdir / "output" / "resources.json").write_text("{corrupt", encoding="utf-8")
        r = await vs.generate_scene_instance_view(
            ids["sciv"], expected_revision=_sciv_rev(pdir, ids["sciv"]),
            candidate_count=1, agnes_client=FakeClient())
        assert not r["ok"] and r["error"] == "resources_lookup_failed", r.get("error")
    asyncio.run(flow())


# ============ T19：provider outside lock ============
def test_t19_provider_call_outside_project_lock(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc, pdir, pdir.name)
        vs = SceneVisualService(pdir, pdir.name)

        async def probe():
            try:
                async with asyncio.timeout(1.0):
                    async with SceneSystemStore(pdir, pdir.name).write_lock():
                        probe_client.lock_probe.append("acquired_during_provider")
            except TimeoutError:
                probe_client.lock_probe.append("timeout_would_deadlock")

        probe_client = FakeClient(delay=0.05, pre_call=probe)
        r = await vs.generate_scene_instance_view(
            ids["sciv"], expected_revision=_sciv_rev(pdir, ids["sciv"]),
            candidate_count=1, agnes_client=probe_client)
        assert r["ok"], r.get("error")
        assert "acquired_during_provider" in probe_client.lock_probe
        assert "timeout_would_deadlock" not in probe_client.lock_probe
    asyncio.run(flow())


# ============ T20/T21：single-flight / 并发 ============
def test_t20_same_state_view_single_flight(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc, pdir, pdir.name)
        vs = SceneVisualService(pdir, pdir.name)
        slow = FakeClient(delay=0.15)
        rev = _sciv_rev(pdir, ids["sciv"])
        r1, r2 = await asyncio.gather(
            vs.generate_scene_instance_view(ids["sciv"], expected_revision=rev,
                                            candidate_count=1, agnes_client=slow),
            vs.generate_scene_instance_view(ids["sciv"], expected_revision=rev,
                                            candidate_count=1, agnes_client=FakeClient()))
        oks = [x for x in (r1, r2) if x["ok"]]
        rejected = [x for x in (r1, r2) if not x["ok"]]
        assert len(oks) == 1 and len(rejected) == 1
        assert rejected[0]["error"] == "generation_in_progress"
        assert len(slow.calls) == 1
        recs = [rec for rec in generation_records.read_records(pdir)
                if rec.get("operation") == "scene_instance_view.generate"]
        assert len(recs) == 1 and recs[0]["status"] == "committed"
    asyncio.run(flow())


def test_t21_different_state_views_concurrent(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc, pdir, pdir.name)
        # sciv_b 的 base 是 v_south（未 approved）→ 先给 v_south 走 approval 门槛
        add_asset(pdir, "ref_baseB000001",
                  f"output/scene_system/locations/{ids['loc']}/views/{ids['v_south']}/g/c_01.png")
        st = SceneSystemStore(pdir, pdir.name)
        from src.scene_system.view_review import build_review_expectations, expectations_fingerprint
        from src.scene_system.generation_records import approval_records_path
        async with st.write_lock():
            d = st.load()
            d.views[ids["v_south"]].approval_status = "approved"
            d.views[ids["v_south"]].reference_asset_id = "ref_baseB000001"
            st.save(d)
        doc = st.load()
        fp = approval_record_fingerprint(pdir, doc, doc.views[ids["v_south"]],
                                         ["ref_baseB000001"])
        from src.scene_system import generation_records as gr
        gr.append_record(pdir, {"approval_id": "apv_b", "operation": "scene.approve_location_view",
                                "status": "approved", "view_id": ids["v_south"],
                                "asset_id": "ref_baseB000001",
                                "reviewed_basis_fingerprint": fp, "completed_at": 0.0},
                         path=approval_records_path(pdir))
        vs = SceneVisualService(pdir, pdir.name)
        fc = FakeClient(delay=0.1)
        r1, r2 = await asyncio.gather(
            vs.generate_scene_instance_view(ids["sciv"],
                                            expected_revision=_sciv_rev(pdir, ids["sciv"]),
                                            candidate_count=1, agnes_client=fc,
                                            include_peer=False),
            vs.generate_scene_instance_view(ids["sciv_b"],
                                            expected_revision=_sciv_rev(pdir, ids["sciv_b"]),
                                            candidate_count=1, agnes_client=fc,
                                            include_peer=False),
        )
        assert r1["ok"] and r2["ok"], (r1.get("error"), r2.get("error"))
        assert len(fc.calls) == 2
    asyncio.run(flow())


# ============ T22：provider failure honest ============
def test_t22_provider_failure_honest(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc, pdir, pdir.name)
        vs = SceneVisualService(pdir, pdir.name)
        r = await vs.generate_scene_instance_view(
            ids["sciv"], expected_revision=_sciv_rev(pdir, ids["sciv"]),
            candidate_count=1, agnes_client=FakeClient(fail=True))
        assert not r["ok"] and r["error"] == "provider_failed"
        v = SceneSystemStore(pdir, pdir.name).load().scene_instance_views[ids["sciv"]]
        assert v.generation_status == "failed" and v.active_generation_id is None
        recs = [rec for rec in generation_records.read_records(pdir)
                if rec.get("scene_instance_view_id") == ids["sciv"]]
        assert recs[-1]["status"] == "failed" and "provider exploded" in (recs[-1]["detail"] or "")
    asyncio.run(flow())


# ============ T23/T24/T26/T27：stale commit 拒绝 ============
def _stale_case(pdir: Path, svc: SceneSystemService, ids: dict, mutate):
    vs = SceneVisualService(pdir, pdir.name)
    rev = _sciv_rev(pdir, ids["sciv"])

    async def hook():
        await asyncio.sleep(0.05)
        await mutate(svc, ids)

    client = FakeClient(pre_call=hook)
    r = asyncio.run(vs.generate_scene_instance_view(
        ids["sciv"], expected_revision=rev, candidate_count=1,
        agnes_client=client, include_peer=False))
    return r


def _assert_stale_common(pdir: Path, ids: dict, r: dict):
    assert not r["ok"] and r["status"] == "stale_basis" \
        and r["error"] == "stale_generation_basis", (r.get("error"), r.get("detail"))
    v = SceneSystemStore(pdir, pdir.name).load().scene_instance_views[ids["sciv"]]
    assert v.generation_status == "none", "stale 时诚实还原 prev 状态"
    assert v.active_generation_id is None
    recs = [rec for rec in generation_records.read_records(pdir)
            if rec.get("scene_instance_view_id") == ids["sciv"]]
    assert recs[-1]["status"] == "stale_basis" and recs[-1]["candidate_files"]


def test_t23_global_state_change_stale(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def mutate(s, ids):
        rev = s.read_scene_instance(ids["inst"])["revision"]
        await s.patch_scene_state(ids["inst"], {"lighting": "normal white"},
                                  expected_revision=rev)
    ids = asyncio.run(_seed(svc, pdir, pdir.name))
    r = _stale_case(pdir, svc, ids, mutate)
    _assert_stale_common(pdir, ids, r)


def test_t24_visible_anchor_state_change_stale(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def mutate(s, ids):
        rev = s.read_scene_instance(ids["inst"])["revision"]
        await s.patch_scene_state(ids["inst"],
                                  {"anchor_states": {ids["a_w2"]: {"condition": "shattered"}}},
                                  expected_revision=rev)
    ids = asyncio.run(_seed(svc, pdir, pdir.name))
    r = _stale_case(pdir, svc, ids, mutate)
    _assert_stale_common(pdir, ids, r)


def test_t26_visible_prop_change_stale(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def mutate(s, ids):
        rev = s.read_scene_instance(ids["inst"])["revision"]
        await s.patch_scene_state(ids["inst"],
                                  {"prop_placements": {"prop_sword01": {"anchor_id": ids["a_n"],
                                                                        "state": "broken"}}},
                                  expected_revision=rev)
    ids = asyncio.run(_seed(svc, pdir, pdir.name))
    r = _stale_case(pdir, svc, ids, mutate)
    _assert_stale_common(pdir, ids, r)


def test_t27_base_location_ref_change_stale(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def mutate(s, ids):
        # base view 的批准参考资产被替换（同 view，不同 asset）
        add_asset(pdir, "ref_baseNEW0001",
                  f"output/scene_system/locations/{ids['loc']}/views/{ids['v_north']}/g2/c_01.png")
        st = SceneSystemStore(pdir, pdir.name)
        async with st.write_lock():
            d = st.load()
            d.views[ids["v_north"]].reference_asset_id = "ref_baseNEW0001"
            st.save(d)
    ids = asyncio.run(_seed(svc, pdir, pdir.name))
    r = _stale_case(pdir, svc, ids, mutate)
    _assert_stale_common(pdir, ids, r)


def test_t25_invisible_anchor_change_does_not_stale_commit(tmp_path):
    """§14/§33：不可见 anchor 变化不产生 stale commit 拒绝（生成照常 committed）。"""
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def mutate(s, ids):
        rev = s.read_scene_instance(ids["inst"])["revision"]
        # 🔴 anchor_states 整 dict 替换：保留可见状态，只改不可见的 a_s
        await s.patch_scene_state(ids["inst"],
                                  {"anchor_states": {ids["a_w2"]: {"condition": "broken"},
                                                     ids["a_s"]: {"condition": "worse"}}},
                                  expected_revision=rev)
    ids = asyncio.run(_seed(svc, pdir, pdir.name))
    vs = SceneVisualService(pdir, pdir.name)
    rev = _sciv_rev(pdir, ids["sciv"])

    async def hook():
        await asyncio.sleep(0.05)
        await mutate(svc, ids)
    r = asyncio.run(vs.generate_scene_instance_view(
        ids["sciv"], expected_revision=rev, candidate_count=1,
        agnes_client=FakeClient(pre_call=hook), include_peer=False))
    assert r["ok"] and r["status"] == "committed", (r.get("error"), r.get("detail"))


# ============ T28：cancellation ============
def test_t28_cancellation_cleanup(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc, pdir, pdir.name)
        vs = SceneVisualService(pdir, pdir.name)
        rev = _sciv_rev(pdir, ids["sciv"])
        task = asyncio.create_task(vs.generate_scene_instance_view(
            ids["sciv"], expected_revision=rev, candidate_count=1,
            agnes_client=FakeClient(delay=1.0), include_peer=False))
        await asyncio.sleep(0.15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        v = SceneSystemStore(pdir, pdir.name).load().scene_instance_views[ids["sciv"]]
        assert v.generation_status == "failed"
        recs = [rec for rec in generation_records.read_records(pdir)
                if rec.get("scene_instance_view_id") == ids["sciv"]]
        assert recs[-1]["error"] == "cancelled"
    asyncio.run(flow())


# ============ T29/T30：orphan recovery ============
def test_t29_t30_orphan_recovery_and_idempotent(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed(svc, pdir, pdir.name))
    st = SceneSystemStore(pdir, pdir.name)

    async def set_orphan():
        async with st.write_lock():
            d = st.load()
            d.scene_instance_views[ids["sciv"]].generation_status = "generating"
            st.save(d)
    asyncio.run(set_orphan())
    n_before = len(generation_records.read_records(pdir))

    async def recover():
        return await recover_orphaned_view_generations(pdir, pdir.name)
    r = asyncio.run(recover())
    assert r["ok"] and ids["sciv"] in r["recovered"], r
    v = st.load().scene_instance_views[ids["sciv"]]
    assert v.generation_status == "failed"
    recs = generation_records.read_records(pdir)
    assert len(recs) == n_before + 1
    assert recs[-1]["operation"] == "scene.recover_orphan"
    assert recs[-1].get("scene_instance_view_id") == ids["sciv"]
    r2 = asyncio.run(recover())
    assert r2["ok"] and r2["recovered"] == []
    assert len(generation_records.read_records(pdir)) == len(recs)


# ============ T31：generation not auto approve ============
def test_t31_generation_never_auto_approves(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc, pdir, pdir.name)
        vs = SceneVisualService(pdir, pdir.name)
        r = await vs.generate_scene_instance_view(
            ids["sciv"], expected_revision=_sciv_rev(pdir, ids["sciv"]),
            candidate_count=1, agnes_client=FakeClient(), include_peer=False)
        assert r["ok"], r.get("error")
        v = SceneSystemStore(pdir, pdir.name).load().scene_instance_views[ids["sciv"]]
        assert v.generation_status == "ready"
        assert v.approval_status == "unreviewed"
        assert v.reference_asset_id is None
    asyncio.run(flow())
