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
