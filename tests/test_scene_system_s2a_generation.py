# tests/test_scene_system_s2a_generation.py
# Scene System V1 — Phase S2A §13-§17/§20 测试（T15-T26）。
#
# T15 provider call outside project lock（provider 等待期间锁必须可被他人获取）
# T16 same View single-flight（第二请求 generation_in_progress，provider 只调一次）
# T17 different Views concurrent（不同 view 并发生成，不全局串行）
# T18 provider failure honest（failed + 诚实 record + 不伪造候选）
# T19 stale Location basis rejects commit
# T20 stale Anchor basis rejects commit
# T21 stale Layout basis rejects commit
# T22 stale View definition rejects commit
# T23 cancellation cleanup（CancelledError 诚实终态 + re-raise）
# T24 startup orphan recovery
# T25 recovery idempotent
# T26 generation never auto approves
#
# （仓库无 pytest-asyncio：async 流程以 asyncio.run 包装在同步测试内。）
import asyncio
import base64
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.scene_system.models import GenerationStatus, view_revision
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
from src.scene_system.visual_service import SceneVisualService
from src.scene_system import generation_records
from src.scene_system.recovery import recover_orphaned_view_generations

PNG_B64 = base64.b64encode(b"\x89PNG fake-image-bytes").decode()


class FakeClient:
    """可控 fake：delay 模拟 provider 耗时；fail/none_result 控制失败模式。"""
    models = {"image": "fake-image-model"}

    def __init__(self, delay: float = 0.0, fail: bool = False, none_result: bool = False,
                 pre_call=None):
        self.delay = delay
        self.fail = fail
        self.none_result = none_result
        self.pre_call = pre_call       # provider 调用前的钩子（锁探测 / 中途 mutation）
        self.calls = []
        self.lock_probe_results = []

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        self.calls.append({"prompt": prompt, "n_refs": len(image_refs or []), "seed": seed})
        if self.pre_call is not None:
            await self.pre_call()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("provider exploded")
        if self.none_result:
            return {"url": None, "b64": None}
        return {"url": None, "b64": PNG_B64}


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


def _fresh_rev(pdir: Path, view_id: str) -> str:
    return view_revision(SceneSystemStore(pdir, pdir.name).load().views[view_id])


# ============ T15：provider 调用在 project lock 之外 ============
def test_t15_provider_call_outside_project_lock(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc)
        vs = SceneVisualService(pdir, pdir.name)

        async def probe():
            # provider 调用期间尝试获取同一 project lock；引擎持锁 → 死锁超时
            try:
                async with asyncio.timeout(1.0):
                    async with SceneSystemStore(pdir, pdir.name).write_lock():
                        probe_client.lock_probe_results.append("acquired_during_provider")
            except TimeoutError:
                probe_client.lock_probe_results.append("timeout_would_deadlock")

        probe_client = FakeClient(delay=0.05, pre_call=probe)
        r = await vs.generate_location_view(ids["va"], expected_revision=ids["va_rev"],
                                            candidate_count=1, agnes_client=probe_client)
        assert r["ok"], r.get("error")
        assert "acquired_during_provider" in probe_client.lock_probe_results, \
            probe_client.lock_probe_results
        assert "timeout_would_deadlock" not in probe_client.lock_probe_results

    asyncio.run(flow())


# ============ T16：same view single-flight ============
def test_t16_same_view_single_flight(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc)
        vs = SceneVisualService(pdir, pdir.name)
        slow_client = FakeClient(delay=0.15)
        rev = _fresh_rev(pdir, ids["va"])
        r1, r2 = await asyncio.gather(
            vs.generate_location_view(ids["va"], expected_revision=rev, candidate_count=1,
                                      agnes_client=slow_client),
            vs.generate_location_view(ids["va"], expected_revision=rev, candidate_count=1,
                                      agnes_client=FakeClient(delay=0)),
        )
        oks = [x for x in (r1, r2) if x["ok"]]
        rejected = [x for x in (r1, r2) if not x["ok"]]
        assert len(oks) == 1 and len(rejected) == 1, (r1.get("error"), r2.get("error"))
        assert rejected[0]["error"] == "generation_in_progress"
        assert len(slow_client.calls) == 1, "single-flight 下 provider 只被调用一次"
        # 被拒侧不写 record（不写假 record，§14）
        recs = generation_records.read_records(pdir, view_id=ids["va"])
        assert len(recs) == 1 and recs[0]["status"] == "committed"

    asyncio.run(flow())


# ============ T17：different views concurrent ============
def test_t17_different_views_concurrent(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc)
        vs = SceneVisualService(pdir, pdir.name)
        fc = FakeClient(delay=0.1)
        rev_a, rev_b = _fresh_rev(pdir, ids["va"]), _fresh_rev(pdir, ids["vb"])
        r1, r2 = await asyncio.gather(
            vs.generate_location_view(ids["va"], expected_revision=rev_a, candidate_count=1,
                                      agnes_client=fc),
            vs.generate_location_view(ids["vb"], expected_revision=rev_b, candidate_count=1,
                                      agnes_client=fc),
        )
        assert r1["ok"] and r2["ok"], (r1.get("error"), r2.get("error"))
        assert len(fc.calls) == 2, "不同 view 并发：两次 provider 调用（不全局串行）"

    asyncio.run(flow())


# ============ T18：provider failure honest ============
def test_t18_provider_failure_honest(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc)
        vs = SceneVisualService(pdir, pdir.name)
        r = await vs.generate_location_view(ids["va"], expected_revision=ids["va_rev"],
                                            candidate_count=2, agnes_client=FakeClient(fail=True))
        assert not r["ok"] and r["status"] == "failed" and r["error"] == "provider_failed"
        assert "provider exploded" in (r["detail"] or "")
        doc = SceneSystemStore(pdir, pdir.name).load()
        v = doc.views[ids["va"]]
        assert v.generation_status == GenerationStatus.FAILED.value
        assert v.active_generation_id is None
        recs = generation_records.read_records(pdir, view_id=ids["va"])
        assert recs and recs[-1]["status"] == "failed" \
            and "provider exploded" in (recs[-1]["detail"] or "")

    asyncio.run(flow())


def test_t18b_provider_none_result_not_fabricated(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc)
        vs = SceneVisualService(pdir, pdir.name)
        r = await vs.generate_location_view(ids["va"], expected_revision=ids["va_rev"],
                                            candidate_count=1,
                                            agnes_client=FakeClient(none_result=True))
        assert not r["ok"] and r["error"] == "provider_failed"
        assert "未返回 url/b64" in (r["detail"] or "")

    asyncio.run(flow())


# ============ T19-T22：阶段3 stale basis 拒绝 commit ============
def _stale_commit_case(pdir: Path, svc: SceneSystemService, ids: dict, mutate) -> dict:
    """通用：阶段1 后（provider delay 期间）执行 mutation → 阶段3 必须 stale 拒绝。"""
    vs = SceneVisualService(pdir, pdir.name)
    rev = _fresh_rev(pdir, ids["va"])

    async def hook():
        await asyncio.sleep(0.05)
        await mutate(svc, ids)

    client = FakeClient(pre_call=hook)
    r = asyncio.run(vs.generate_location_view(ids["va"], expected_revision=rev,
                                              candidate_count=1, agnes_client=client))
    return r


def _assert_stale_common(pdir: Path, ids: dict, r: dict):
    assert not r["ok"] and r["status"] == "stale_basis" \
        and r["error"] == "stale_generation_basis", (r.get("error"), r.get("detail"))
    doc = SceneSystemStore(pdir, pdir.name).load()
    v = doc.views[ids["va"]]
    assert v.generation_status == "none", "stale 时 view 状态诚实还原为 prev（不卡 generating/ready）"
    assert v.active_generation_id is None, "绝不把旧生成结果 attach 到已变化的语义"
    recs = generation_records.read_records(pdir, view_id=ids["va"])
    assert recs[-1]["status"] == "stale_basis"
    assert recs[-1]["candidate_files"], "候选图保留在 generation 目录供诊断"


def test_t19_stale_location_basis_rejects_commit(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def mutate(s, ids):
        rev = s.read_location(ids["loc"])["revision"]
        await s.update_location(ids["loc"], {"identity_description": "changed mid-flight"},
                                expected_revision=rev)

    async def flow():
        return await _seed(svc)
    ids = asyncio.run(flow())
    r = _stale_commit_case(pdir, svc, ids, mutate)
    _assert_stale_common(pdir, ids, r)


def test_t20_stale_anchor_basis_rejects_commit(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def mutate(s, ids):
        rev = s.read_anchor(ids["a1"])["revision"]
        await s.update_anchor(ids["a1"], {"description": "anchor changed mid-flight"},
                              expected_revision=rev)

    ids = asyncio.run(_seed(svc))
    r = _stale_commit_case(pdir, svc, ids, mutate)
    _assert_stale_common(pdir, ids, r)


def test_t21_stale_layout_basis_rejects_commit(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def mutate(s, ids):
        lay = next(iter(SceneSystemStore(pdir, pdir.name).load().layouts.values()))
        rev = s.read_layout(lay.layout_id)["revision"]
        await s.update_layout(lay.layout_id, {"orientation_convention": "screen_north_up"},
                              expected_revision=rev)

    ids = asyncio.run(_seed(svc))
    r = _stale_commit_case(pdir, svc, ids, mutate)
    _assert_stale_common(pdir, ids, r)


def test_t22_stale_view_definition_rejects_commit(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def mutate(s, ids):
        rev = s.read_view(ids["va"])["revision"]
        await s.update_view(ids["va"], {"visible_anchor_ids": []}, expected_revision=rev)

    ids = asyncio.run(_seed(svc))
    r = _stale_commit_case(pdir, svc, ids, mutate)
    _assert_stale_common(pdir, ids, r)


# ============ T23：cancellation cleanup ============
def test_t23_cancellation_cleanup(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc)
        vs = SceneVisualService(pdir, pdir.name)
        rev = _fresh_rev(pdir, ids["va"])
        task = asyncio.create_task(vs.generate_location_view(
            ids["va"], expected_revision=rev, candidate_count=1,
            agnes_client=FakeClient(delay=1.0)))
        await asyncio.sleep(0.15)   # 进入 provider delay
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        doc = SceneSystemStore(pdir, pdir.name).load()
        assert doc.views[ids["va"]].generation_status == GenerationStatus.FAILED.value, \
            "取消后尽力写诚实终态（cleanup）；随后 re-raise 未吞取消"
        recs = generation_records.read_records(pdir, view_id=ids["va"])
        assert recs and recs[-1]["error"] == "cancelled", recs[-1].get("error")

    asyncio.run(flow())


# ============ T24/T25：startup orphan recovery + 幂等 ============
def test_t24_t25_orphan_recovery_and_idempotent(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed(svc))
    st = SceneSystemStore(pdir, pdir.name)
    # 模拟崩溃遗留：直接把 view 置 generating（绕过协议，模拟上次进程死亡现场）
    async def set_orphan():
        async with st.write_lock():
            doc = st.load()
            doc.views[ids["va"]].generation_status = GenerationStatus.GENERATING.value
            st.save(doc)
    asyncio.run(set_orphan())
    recs_before = generation_records.read_records(pdir)

    async def recover():
        return await recover_orphaned_view_generations(pdir, pdir.name)
    r = asyncio.run(recover())
    assert r["ok"] and r["recovered"] == [ids["va"]], r
    doc = st.load()
    assert doc.views[ids["va"]].generation_status == GenerationStatus.FAILED.value
    recs = generation_records.read_records(pdir)
    assert len(recs) == len(recs_before) + 1
    orphan_rec = recs[-1]
    assert orphan_rec["error"] == "orphaned_by_process_restart"
    assert orphan_rec["operation"] == "scene.recover_orphan"
    # T25 幂等：再跑一次零写入
    r2 = asyncio.run(recover())
    assert r2["ok"] and r2["recovered"] == []
    assert len(generation_records.read_records(pdir)) == len(recs)


# ============ T26：generation 绝不自动 approve ============
def test_t26_generation_never_auto_approves(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        ids = await _seed(svc)
        vs = SceneVisualService(pdir, pdir.name)
        r = await vs.generate_location_view(ids["va"], expected_revision=ids["va_rev"],
                                            candidate_count=1, agnes_client=FakeClient())
        assert r["ok"], r.get("error")
        v = SceneSystemStore(pdir, pdir.name).load().views[ids["va"]]
        assert v.generation_status == "ready"
        assert v.approval_status == "unreviewed", "生成成功 ≠ approved（两维度分离，§8）"

    asyncio.run(flow())


# ============ 共享 seed（同文件内复用） ============
async def _seed(svc: SceneSystemService, *, with_layout: bool = True) -> dict:
    async def drev():
        return svc.document_overview()["document_revision"]

    r = await svc.create_location(name="灯塔监听室",
                                  identity_description="circular control room",
                                  expected_document_revision=await drev())
    loc = r["target"]
    a1 = (await svc.create_anchor(location_id=loc, name="north door", kind="door",
                                  expected_document_revision=await drev()))["target"]
    a2 = (await svc.create_anchor(location_id=loc, name="main console", kind="furniture",
                                  expected_document_revision=await drev()))["target"]
    if with_layout:
        await svc.create_layout(location_id=loc, orientation_convention="north_up",
                                placements=[{"anchor_id": a2, "zone": "center"}],
                                expected_document_revision=await drev())
    va = await svc.create_view(location_id=loc, label="hero A", camera_zone="south_west",
                               look_direction="north_east", visible_anchor_ids=[a1],
                               expected_document_revision=await drev())
    vb = await svc.create_view(location_id=loc, label="hero B", camera_zone="north",
                               look_direction="south", visible_anchor_ids=[a1, a2],
                               expected_document_revision=await drev())
    return {"loc": loc, "a1": a1, "a2": a2, "va": va["target"], "vb": vb["target"],
            "va_rev": va["revision"], "vb_rev": vb["revision"]}
