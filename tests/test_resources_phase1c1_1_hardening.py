# tests/test_resources_phase1c1_1_hardening.py
# Production Resources V1 — Phase 1C1.1 Per-Look Generation Single-Flight Hardening。
#
# 只解决一个问题：同一 CharacterLook 任意时刻最多一个 active generation；
# 不同 look_id 之间仍允许并发。
#
# T1 是"攻击测试"：A 在 provider await 中挂起 → 调用方读取最新 revision
# （含 A 的 generating 翻转）→ 以该最新 revision 发起 B。修复前该测试红
# （B 进入 provider）；修复后 B 在阶段 1 短锁内被 generation_in_progress 拒绝。
import asyncio
from pathlib import Path

import pytest

from src.core.write_lock import _clear_for_test, project_write_lock
from src.resources.models import document_revision
from src.resources.generation_records import read_records as _read_records

from tests.test_resources_phase1c1 import (   # 复用 1C1 的 fixtures/helpers
    FakeAgnes, _seed_look, _current_look_rev, run,
)


@pytest.fixture(autouse=True)
def _isolated_locks():
    _clear_for_test()
    yield
    _clear_for_test()


@pytest.fixture
def env(tmp_path):
    from tests.test_resources_phase1c1 import make_project, make_view_files
    from src.resources.write_service import ResourceWriteService
    pdir = make_project(tmp_path, with_character=True)
    make_view_files(pdir)
    ws = ResourceWriteService(pdir, pdir.name)
    return {"pdir": pdir, "ws": ws, "doc_rev": document_revision(ws.store.load())}


def _snapshot(pdir: Path):
    """canonical 字节 + records 数（零副作用断言用）。"""
    res = pdir / "output" / "resources.json"
    recs = pdir / "output" / "resources" / "generation_records.jsonl"
    return (res.read_bytes() if res.exists() else None,
            len(_read_records(pdir)))


# =====================================================================
# T1 —— 同 look：A 在 provider await 中，用最新 revision 发起 B → 拒绝
# =====================================================================
def test_t1_same_look_second_generation_rejected_while_first_in_flight(env):
    ws = env["ws"]
    pdir = env["pdir"]
    look_id, _ = run(_seed_look(env))
    a_in_provider = asyncio.Event()
    release_a = asyncio.Event()

    async def hang_a(i):
        a_in_provider.set()
        await asyncio.wait_for(release_a.wait(), 10)

    fake_a = FakeAgnes(on_call=hang_a)
    rev_before = _current_look_rev(env, look_id)

    async def scenario():
        t_a = asyncio.create_task(ws.generate_look_candidates(
            look_id, expected_revision=rev_before, agnes_client=fake_a))
        # A 已翻转 generating 并进入 provider await（锁已释放）
        await asyncio.wait_for(a_in_provider.wait(), 5)
        # 攻击：读取最新 Look revision（含 A 的 generating 翻转）发起 B
        rev_now = _current_look_rev(env, look_id)
        assert rev_now != rev_before, "generating 翻转本身应改变 look revision（否则攻击前提不成立）"
        fake_b = FakeAgnes()
        r_b = await ws.generate_look_candidates(
            look_id, expected_revision=rev_now, agnes_client=fake_b)
        # B 被拒后、A 完成前：canonical 仍是 A 的 generating（未被 B 触碰）
        assert ws.store.load().looks[look_id].generation_status == "generating"
        release_a.set()
        r_a = await t_a
        return r_b, fake_b, r_a

    r_b, fake_b, r_a = run(scenario())
    assert r_b["ok"] is False and r_b["error"] == "generation_in_progress", r_b
    assert fake_b.calls == 0, "B 不得进入 provider"
    assert r_a["ok"] and r_a["status"] == "committed", r_a   # 只有 A 继续


# =====================================================================
# T2 —— 不同 look：A、B 同时生成，两者都能进入 provider（无全局串行化）
# =====================================================================
def test_t2_different_looks_generate_concurrently(env):
    ws = env["ws"]
    look1, _ = run(_seed_look(env))

    async def seed_second():
        r = await ws.create_look(character_name="Alice", label="L2",
                                 expected_document_revision=document_revision(ws.store.load()))
        assert r["ok"], r
        return r["target"]

    look2 = run(seed_second())
    a_in, b_in = asyncio.Event(), asyncio.Event()
    rel_a, rel_b = asyncio.Event(), asyncio.Event()

    async def hang_a(i):
        a_in.set()
        await asyncio.wait_for(rel_a.wait(), 10)

    async def hang_b(i):
        b_in.set()
        await asyncio.wait_for(rel_b.wait(), 10)

    fake_a = FakeAgnes(on_call=hang_a)
    fake_b = FakeAgnes(on_call=hang_b)

    async def scenario():
        t_a = asyncio.create_task(ws.generate_look_candidates(
            look1, expected_revision=_current_look_rev(env, look1), agnes_client=fake_a))
        t_b = asyncio.create_task(ws.generate_look_candidates(
            look2, expected_revision=_current_look_rev(env, look2), agnes_client=fake_b))
        # 两个 look 同时处于 provider await 中——证明不同 look 之间没有被串行化
        await asyncio.wait_for(asyncio.gather(a_in.wait(), b_in.wait()), 5)
        rel_a.set()
        rel_b.set()
        return await asyncio.gather(t_a, t_b)

    r_a, r_b = run(scenario())
    assert r_a["ok"] and r_a["status"] == "committed", r_a
    assert r_b["ok"] and r_b["status"] == "committed", r_b
    assert fake_a.calls == 1 and fake_b.calls == 1
    doc = ws.store.load()
    assert doc.looks[look1].generation_status == "ready"
    assert doc.looks[look2].generation_status == "ready"


# =====================================================================
# T3/T4/T5 —— 终态后同 look 可再次 generate（failed / stale / committed）
# =====================================================================
def test_t3_regenerate_allowed_after_provider_failure(env):
    ws = env["ws"]
    look_id, _ = run(_seed_look(env))
    r1 = run(ws.generate_look_candidates(
        look_id, expected_revision=_current_look_rev(env, look_id),
        agnes_client=FakeAgnes(fail_on=0)))
    assert r1["ok"] is False and ws.store.load().looks[look_id].generation_status == "failed"
    r2 = run(ws.generate_look_candidates(
        look_id, expected_revision=_current_look_rev(env, look_id),
        agnes_client=FakeAgnes(images=[b"RETRY"]))
    )
    assert r2["ok"] and r2["status"] == "committed", r2


def test_t4_regenerate_allowed_after_stale_basis(env):
    ws = env["ws"]
    look_id, _ = run(_seed_look(env))

    async def mutate(i):
        r = await ws.update_look(look_id, {"appearance_delta": "changed mid-flight"},
                                 expected_revision=_current_look_rev(env, look_id))
        assert r["ok"], r

    r1 = run(ws.generate_look_candidates(
        look_id, expected_revision=_current_look_rev(env, look_id),
        agnes_client=FakeAgnes(on_call=mutate)))
    assert r1["ok"] is False and r1["status"] == "stale_basis"
    assert ws.store.load().looks[look_id].generation_status == "none"   # 诚实还原
    r2 = run(ws.generate_look_candidates(
        look_id, expected_revision=_current_look_rev(env, look_id),
        agnes_client=FakeAgnes(images=[b"AFTER-STALE"])))
    assert r2["ok"] and r2["status"] == "committed", r2


def test_t5_regenerate_allowed_after_successful_commit(env):
    ws = env["ws"]
    look_id, _ = run(_seed_look(env))
    r1 = run(ws.generate_look_candidates(
        look_id, expected_revision=_current_look_rev(env, look_id),
        agnes_client=FakeAgnes(images=[b"FIRST"])))
    assert r1["ok"] and ws.store.load().looks[look_id].generation_status == "ready"
    r2 = run(ws.generate_look_candidates(
        look_id, expected_revision=_current_look_rev(env, look_id),
        agnes_client=FakeAgnes(images=[b"SECOND"])))
    assert r2["ok"] and r2["status"] == "committed", r2
    doc = ws.store.load()
    assert doc.looks[look_id].reference_asset_ids == \
        r1["candidate_asset_ids"] + r2["candidate_asset_ids"]


# =====================================================================
# T6 —— generation_in_progress：零副作用（无 record / canonical 字节不变）
# =====================================================================
def test_t6_rejection_is_zero_side_effect(env):
    ws = env["ws"]
    pdir = env["pdir"]
    look_id, _ = run(_seed_look(env))
    a_in = asyncio.Event()
    release_a = asyncio.Event()

    async def hang_a(i):
        a_in.set()
        await asyncio.wait_for(release_a.wait(), 10)

    async def scenario():
        t_a = asyncio.create_task(ws.generate_look_candidates(
            look_id, expected_revision=_current_look_rev(env, look_id),
            agnes_client=FakeAgnes(on_call=hang_a)))
        await asyncio.wait_for(a_in.wait(), 5)
        snap_before = _snapshot(pdir)          # A 的阶段1已改 canonical（generating）→ 以此为基线
        status_before = ws.store.load().looks[look_id].generation_status
        assert status_before == "generating"
        r_b = await ws.generate_look_candidates(
            look_id, expected_revision=_current_look_rev(env, look_id),
            agnes_client=FakeAgnes())
        release_a.set()
        await t_a
        return r_b, snap_before

    r_b, snap_before = run(scenario())
    assert r_b["ok"] is False and r_b["error"] == "generation_in_progress"
    snap_after = _snapshot(pdir)
    # B 拒绝瞬间与基线相比：record 数不变（B 不写 record）
    assert snap_before[1] == snap_after[1] - 1  # 只差 A 自己完成时写的那 1 条
    # canonical 除 A 的正常提交外无 B 引入的额外变化：
    # A committed 后 look 只应有 A 的候选；若有 B 的副作用会多出 asset/id
    doc = ws.store.load()
    look = doc.looks[look_id]
    recs = _read_records(pdir, look_id=look_id)
    assert len(recs) == 1 and recs[0]["status"] == "committed"    # 只存在 A 的 record
    assert look.generation_status == "ready"
    assert len(doc.reference_assets) == 1                          # 只有 A 的 1 个候选


# =====================================================================
# Phase 1C1.2 — Generation Recovery（cancel cleanup + startup orphan sweep）
# =====================================================================
from src.resources.recovery import recover_orphaned_generations, recover_all_projects
from src.resources.models import CharacterLook, new_look_id as _new_look_id


class HangingForeverAgnes:
    """provider await 永不返回（模拟 30s 生成窗口）。"""

    def __init__(self):
        self.models = {"image": "agnes-image-test"}

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        await asyncio.Event().wait()


# ---- T-R1 cancel cleanup：provider await 中任务被取消 → 诚实终态 + re-raise ----
def test_cancel_cleanup_writes_failed_and_reraises(env):
    ws = env["ws"]
    look_id, _ = run(_seed_look(env))

    async def scenario():
        t = asyncio.create_task(ws.generate_look_candidates(
            look_id, expected_revision=_current_look_rev(env, look_id),
            agnes_client=HangingForeverAgnes()))
        await asyncio.sleep(0.2)        # 阶段1完成（generating 落盘），已进入 provider
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t                     # 🔴 cancel 必须向上传播（不吞）
        return ws.store.load().looks[look_id]

    look = run(scenario())
    assert look.generation_status == "failed"          # 不卡 generating（cancel cleanup 生效）
    rec = _read_records(env["pdir"], look_id=look_id)
    assert len(rec) == 1 and rec[0]["status"] == "failed" and rec[0]["error"] == "cancelled"
    # 终态后 single-flight 不阻塞：可再次生成
    r2 = run(ws.generate_look_candidates(
        look_id, expected_revision=_current_look_rev(env, look_id),
        agnes_client=FakeAgnes(images=[b"AFTER-CANCEL"])))
    assert r2["ok"] and r2["status"] == "committed"


# ---- T-R2 startup sweep：孤儿 generating → failed + record；幂等；他人不动 ----
def _make_orphan(env, look_id):
    """模拟死进程磁盘态：直接把 look 置为 generating（绕过协议=崩溃语义）。"""
    ws = env["ws"]
    doc = ws.store.load()
    doc.looks[look_id].generation_status = "generating"
    ws.store.save(doc)


def test_startup_sweep_recovers_orphan_generating(env):
    ws = env["ws"]
    pdir = env["pdir"]
    look_id, _ = run(_seed_look(env))
    healthy_id, _ = run(_seed_look(env))   # 第二个 look（正常态，必须不被触碰）
    _make_orphan(env, look_id)

    r = run(recover_orphaned_generations(pdir, pdir.name))
    assert r["ok"] and r["scanned"] == 2 and r["recovered"] == [look_id]
    doc = ws.store.load()
    assert doc.looks[look_id].generation_status == "failed"
    assert doc.looks[healthy_id].generation_status == "none"              # 健康者零改动
    rec = _read_records(pdir, look_id=look_id)
    assert rec[-1]["error"] == "orphaned_by_process_restart"
    assert rec[-1]["status"] == "failed" and rec[-1]["basis"] is None     # 不臆造 basis
    assert rec[-1]["generation_id"].startswith("gen_orphan_")

    # 幂等：第二遍零恢复、零写入
    res_bytes = (pdir / "output" / "resources.json").read_bytes()
    r2 = run(recover_orphaned_generations(pdir, pdir.name))
    assert r2["recovered"] == [] and r2["scanned"] == 2
    assert (pdir / "output" / "resources.json").read_bytes() == res_bytes
    # 恢复后 single-flight 放行：可再次生成
    r3 = run(ws.generate_look_candidates(
        look_id, expected_revision=_current_look_rev(env, look_id),
        agnes_client=FakeAgnes(images=[b"AFTER-SWEEP"])))
    assert r3["ok"] and r3["status"] == "committed"


# ---- T-R3 recover_all_projects：多项目 + 损坏项目容错不阻塞 ----
def test_recover_all_projects_multi_and_tolerant(tmp_path):
    from src.resources.write_service import ResourceWriteService

    # 项目 A：有孤儿（recovery 只碰 resources.json，无需 project.json）
    pa = tmp_path / "pa"
    (pa / "output").mkdir(parents=True)
    ws_a = ResourceWriteService(pa, "pa")
    lk = CharacterLook(look_id=_new_look_id(), character_name="Alice",
                       generation_status="generating")
    doc = ws_a.store.load()
    doc.looks[lk.look_id] = lk
    ws_a.store.save(doc)
    # 项目 B：干净、无 resources.json
    (tmp_path / "pb" / "output").mkdir(parents=True)
    # 项目 C：resources.json 损坏（fail closed → 记 error，不阻塞其它项目）
    pc = tmp_path / "pc"
    (pc / "output").mkdir(parents=True)
    (pc / "output" / "resources.json").write_text("{ broken", encoding="utf-8")

    from src.utils.project_manager import ProjectManager
    pm = ProjectManager(base_dir=str(tmp_path))
    summary = run(recover_all_projects(pm))
    assert summary["total_recovered"] == 1
    assert summary["projects"]["pa"]["recovered"] == 1
    assert summary["projects"]["pb"]["recovered"] == 0
    assert "pc" in summary["errors"] and summary["ok"] is False
    assert ws_a.store.load().looks[lk.look_id].generation_status == "failed"
