# tests/test_resources_phase1c2.py
# Production Resources V1 — Phase 1C2：Visual Review + Approval 验收测试。
#
# 核心语义（双向）：review pass ≠ 自动 approved；无 pass 证据不能 approve。
# review evidence 独立持久化（review_records.jsonl），canonical 只被 approve/revoke 改动。
import asyncio
import json
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.resources.models import document_revision
from src.resources.write_service import ResourceWriteService
from src.resources.service import ResourceService
from src.resources import look_review
from src.resources.look_review import (
    review_look_candidates, latest_review_for_asset, has_pass_evidence,
    derive_overall, build_review_prompt, read_review_records,
)

from tests.test_resources_phase1c1 import (
    FakeAgnes, _seed_look, _current_look_rev, run,
    make_project, make_view_files, wclient,
)


@pytest.fixture(autouse=True)
def _isolated_locks():
    _clear_for_test()
    yield
    _clear_for_test()


@pytest.fixture
def env(tmp_path):
    pdir = make_project(tmp_path, with_character=True)
    make_view_files(pdir)
    ws = ResourceWriteService(pdir, pdir.name)
    return {"pdir": pdir, "ws": ws, "doc_rev": document_revision(ws.store.load())}


PASS_JSON = json.dumps({
    "identity_consistency": "pass", "identity_notes": "matches reference",
    "costume_consistency": "pass", "costume_notes": "coat as specified",
    "visual_defects": "pass", "defect_notes": "none",
    "project_style_consistency": "pass", "style_notes": "matches project style",
    "overall": "pass", "summary": "good production reference"})
NEEDSFIX_JSON = json.dumps({
    "identity_consistency": "pass", "identity_notes": "ok",
    "costume_consistency": "pass", "costume_notes": "ok",
    "visual_defects": "fail", "defect_notes": "extra sixth finger",
    "project_style_consistency": "pass", "style_notes": "ok",
    "overall": "pass",   # 模型自报 pass —— 服务端必须推导为 needs_fix
    "summary": "defect"})
CONCERN_JSON = json.dumps({
    "identity_consistency": "pass", "identity_notes": "ok",
    "costume_consistency": "concern", "costume_notes": "buttons differ",
    "visual_defects": "pass", "defect_notes": "none",
    "project_style_consistency": "pass", "style_notes": "ok",
    "overall": "pass", "summary": "minor concern"})
FENCED_PASS = "```json\n" + PASS_JSON + "\n```"


class FakeVision:
    """可编程 chat_completion_vision fake（只实现审查契约面）。"""

    def __init__(self, content=PASS_JSON):
        self.content = content          # str | Exception
        self.calls = []

    async def chat_completion_vision(self, messages, *, model, temperature=0.2, max_tokens=1600):
        self.calls.append({"messages": messages, "model": model})
        if isinstance(self.content, Exception):
            raise self.content
        return {"content": self.content,
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                "model": model}


async def _seed_with_candidate(env, images=(b"CAND-IMG-1",)):
    """look + 已生成候选（每个 image 一个候选），返回 (look_id, asset_ids列表)。"""
    ws = env["ws"]
    look_id, _ = await _seed_look(env)
    r = await ws.generate_look_candidates(
        look_id, expected_revision=_current_look_rev(env, look_id),
        candidate_count=len(images), view_purpose="front",
        agnes_client=FakeAgnes(images=list(images)))
    assert r["ok"], r
    return look_id, list(r["candidate_asset_ids"])


# =====================================================================
# Review：真实维度检查 + 独立证据
# =====================================================================
def test_review_pass_full_flow(env):
    look_id, asset_ids = run(_seed_with_candidate(env))
    asset_id = asset_ids[0]
    fv = FakeVision(PASS_JSON)
    r = run(review_look_candidates(env["pdir"], look_id, agnes_client=fv))
    assert r["ok"] and r["status"] == "completed", r
    cr = r["candidate_reviews"][0]
    assert cr["asset_id"] == asset_id and cr["status"] == "reviewed"
    assert (cr["identity_consistency"], cr["costume_consistency"], cr["visual_defects"]) == \
        ("pass", "pass", "pass")
    assert cr["overall"] == "pass"
    # vision 契约面：显式 agnes-2.5-flash + data URI 图片输入（local 可读优先）
    assert fv.calls and fv.calls[0]["model"] == "agnes-2.5-flash"
    img_part = [c for c in fv.calls[0]["messages"][0]["content"] if c["type"] == "image_url"]
    assert img_part and img_part[0]["image_url"]["url"].startswith("data:image/png;base64,")
    # 期望基准进 prompt（身份/服装/appearance）
    text_part = [c for c in fv.calls[0]["messages"][0]["content"] if c["type"] == "text"][0]["text"]
    assert "a girl with silver hair" in text_part and "Black Coat" in text_part
    # 独立证据持久化：review_records.jsonl（与 generation_records.jsonl 分离）
    assert (env["pdir"] / "output" / "resources" / "review_records.jsonl").exists()
    recs = read_review_records(env["pdir"], look_id=look_id)
    assert len(recs) == 1 and recs[0]["review_id"] == r["review_id"]
    assert recs[0]["status"] == "completed" and recs[0]["verified"] is True
    assert "base64" not in (env["pdir"] / "output" / "resources" / "review_records.jsonl").read_text(encoding="utf-8")
    # 🔴 review pass ≠ approved（正向不自动批准）
    look = env["ws"].store.load().looks[look_id]
    assert look.approved_reference_asset_ids == [] and look.approval_status == "unreviewed"
    assert look.generation_status == "ready"      # review 不触碰生成状态
    # read 视图带最近审查结论
    rl = ResourceService(env["pdir"], env["pdir"].name).read_look(look_id)
    assert rl["latest_reviews"][asset_id]["overall"] == "pass"


def test_review_overall_derived_not_trusted_from_model(env):
    look_id, _ = run(_seed_with_candidate(env))   # noqa: 单候选即可
    r = run(review_look_candidates(env["pdir"], look_id, agnes_client=FakeVision(NEEDSFIX_JSON)))
    cr = r["candidate_reviews"][0]
    assert cr["overall"] == "needs_fix"                 # 服务端推导（visual_defects=fail）
    assert cr["model_overall"] == "pass"                # 模型自报留档
    r2 = run(review_look_candidates(env["pdir"], look_id, agnes_client=FakeVision(CONCERN_JSON)))
    assert r2["candidate_reviews"][0]["overall"] == "uncertain"   # concern（非全 pass）→ uncertain


def test_review_parse_and_provider_isolation(env):
    look_id, (a1, a2) = run(_seed_with_candidate(env, images=(b"C1", b"C2")))
    # 候选2 unparsable / 候选1 fenced JSON → 逐个隔离
    fv = FakeVision(None)

    async def two():
        calls = {"n": 0}

        class Alternating(FakeVision):
            async def chat_completion_vision(self, messages, *, model, temperature=0.2, max_tokens=1600):
                calls["n"] += 1
                self.calls.append({"model": model})
                if calls["n"] == 1:
                    return {"content": FENCED_PASS, "usage": {}, "model": model}
                return {"content": "抱歉我无法输出 JSON", "usage": {}, "model": model}

        return await review_look_candidates(env["pdir"], look_id, agnes_client=Alternating())

    r = run(two())
    crs = {c["asset_id"]: c for c in r["candidate_reviews"]}
    assert crs[a1]["status"] == "reviewed" and crs[a1]["overall"] == "pass"   # fence 剥离
    assert crs[a2]["status"] == "parse_failed" and crs[a2]["overall"] == "uncertain"
    # provider 错误隔离（单候选失败不影响其它）
    fv2 = FakeVision(RuntimeError("vision boom"))

    class Exploding(FakeVision):
        async def chat_completion_vision(self, messages, *, model, temperature=0.2, max_tokens=1600):
            raise RuntimeError("vision boom")

    r2 = run(review_look_candidates(env["pdir"], look_id, agnes_client=Exploding()))
    assert all(c["status"] == "provider_error" and c["overall"] == "uncertain"
               for c in r2["candidate_reviews"])
    # 审查失败也诚实记录
    recs = read_review_records(env["pdir"], look_id=look_id)
    assert recs[-1]["candidate_reviews"][0]["status"] == "provider_error"


def test_review_image_unreadable_and_errors(env):
    look_id, asset_ids = run(_seed_with_candidate(env))
    asset_id = asset_ids[0]
    # 候选图文件删除 + 无 url → image_unreadable（fail closed uncertain）
    doc = env["ws"].store.load()
    rel = doc.reference_assets[asset_id].local
    (env["pdir"] / rel).unlink()
    r = run(review_look_candidates(env["pdir"], look_id, agnes_client=FakeVision()))
    assert r["candidate_reviews"][0]["status"] == "image_unreadable"
    # not_in_candidates
    r2 = run(review_look_candidates(env["pdir"], look_id, asset_ids=["ref_none"],
                                    agnes_client=FakeVision()))
    assert r2["ok"] is False and r2["error"] == "not_in_candidates"
    # not_found
    r3 = run(review_look_candidates(env["pdir"], "look_none", agnes_client=FakeVision()))
    assert r3["ok"] is False and r3["error"] == "not_found"


def test_review_derive_overall_and_prompt_deterministic():
    assert derive_overall({"identity_consistency": "pass", "costume_consistency": "pass",
                           "visual_defects": "pass"}) == "pass"
    assert derive_overall({"identity_consistency": "fail", "costume_consistency": "pass",
                           "visual_defects": "pass"}) == "needs_fix"
    assert derive_overall({"identity_consistency": "pass", "costume_consistency": "concern",
                           "visual_defects": "pass"}) == "uncertain"
    assert derive_overall({"identity_consistency": None, "costume_consistency": None,
                           "visual_defects": None}) == "uncertain"   # 缺失不判 pass（fail closed）
    kw = dict(character_description="a girl", clothing_snapshot={"name": "Coat"},
              appearance_delta="scar", view_purpose="front")
    assert build_review_prompt(**kw) == build_review_prompt(**kw)


# =====================================================================
# Approval：显式动作 + pass 证据门槛
# =====================================================================
def test_approve_requires_pass_evidence(env):
    ws = env["ws"]
    look_id, asset_ids = run(_seed_with_candidate(env))
    asset_id = asset_ids[0]
    rev = _current_look_rev(env, look_id)
    # 无任何审查证据 → 拒绝
    r = run(ws.approve_look_reference(look_id, asset_id, expected_revision=rev))
    assert r["ok"] is False and r["error"] == "review_not_passed", r
    # needs_fix 证据 → 仍拒绝
    run(review_look_candidates(env["pdir"], look_id, agnes_client=FakeVision(NEEDSFIX_JSON)))
    r2 = run(ws.approve_look_reference(look_id, asset_id,
                                       expected_revision=_current_look_rev(env, look_id)))
    assert r2["ok"] is False and r2["error"] == "review_not_passed"
    # uncertain（concern）→ 拒绝
    run(review_look_candidates(env["pdir"], look_id, agnes_client=FakeVision(CONCERN_JSON)))
    r3 = run(ws.approve_look_reference(look_id, asset_id,
                                       expected_revision=_current_look_rev(env, look_id)))
    assert r3["ok"] is False and r3["error"] == "review_not_passed"
    # pass 证据 → 通过：approved ids + approval_status + 自动封面
    run(review_look_candidates(env["pdir"], look_id, agnes_client=FakeVision(PASS_JSON)))
    r4 = run(ws.approve_look_reference(look_id, asset_id,
                                       expected_revision=_current_look_rev(env, look_id)))
    assert r4["ok"] and r4["verified"], r4
    look = ws.store.load().looks[look_id]
    assert look.approved_reference_asset_ids == [asset_id]
    assert look.approval_status == "approved"
    assert look.cover_asset_id == asset_id            # 首个 approved 自动成为封面
    assert has_pass_evidence(env["pdir"], look_id, asset_id)
    # 重复 approve → 幂等 unchanged
    r5 = run(ws.approve_look_reference(look_id, asset_id,
                                       expected_revision=r4["revision"]))
    assert r5["ok"] and r5.get("unchanged") is True
    # 证据按 asset 隔离：pass 的是这个 asset，别的 asset 不能搭车（多候选场景）
    # （_seed_with_candidate 多候选时逐 asset 审查的隔离由 review 测试覆盖）


def test_approve_rejects_asset_out_of_candidates_and_stale(env):
    ws = env["ws"]
    look_id, asset_ids = run(_seed_with_candidate(env))
    asset_id = asset_ids[0]
    run(review_look_candidates(env["pdir"], look_id, agnes_client=FakeVision(PASS_JSON)))
    # 不在候选集中的 asset → 拒绝（即使该 id 有 pass 证据也进不来）
    r = run(ws.approve_look_reference(look_id, "ref_outside",
                                      expected_revision=_current_look_rev(env, look_id)))
    assert r["ok"] is False and r["error"] == "not_in_candidates"
    # OCC
    r2 = run(ws.approve_look_reference(look_id, asset_id, expected_revision="00000000"))
    assert r2["ok"] is False and r2["error"] == "stale_revision"


def test_revoke_approval_semantics(env):
    ws = env["ws"]
    look_id, (a1, a2) = run(_seed_with_candidate(env, images=(b"C1", b"C2")))
    run(review_look_candidates(env["pdir"], look_id, agnes_client=FakeVision(PASS_JSON)))
    r1 = run(ws.approve_look_reference(look_id, a1,
                                       expected_revision=_current_look_rev(env, look_id)))
    r2 = run(ws.approve_look_reference(look_id, a2,
                                       expected_revision=r1["revision"]))
    look = ws.store.load().looks[look_id]
    assert look.approved_reference_asset_ids == [a1, a2]
    assert look.cover_asset_id == a1                  # cover 不被后续 approve 抢走
    # revoke a1：cover 清空、approved 剩 a2、状态仍 approved
    r3 = run(ws.revoke_look_approval(look_id, a1, expected_revision=r2["revision"]))
    assert r3["ok"] and r3["verified"]
    look = ws.store.load().looks[look_id]
    assert look.approved_reference_asset_ids == [a2]
    assert look.cover_asset_id is None
    assert look.approval_status == "approved"
    # revoke a2：approved 空 → 回 unreviewed
    r4 = run(ws.revoke_look_approval(look_id, a2, expected_revision=r3["revision"]))
    look = ws.store.load().looks[look_id]
    assert look.approved_reference_asset_ids == [] and look.approval_status == "unreviewed"
    # 幂等 revoke
    r5 = run(ws.revoke_look_approval(look_id, a2, expected_revision=r4["revision"]))
    assert r5["ok"] and r5.get("unchanged") is True
    # 候选仍在（revoke 不删候选）
    assert look.reference_asset_ids == [a1, a2]


# =====================================================================
# HTTP 契约
# =====================================================================
def test_http_review_and_approve_gate(wclient, monkeypatch):
    import deps as deps_mod
    from src.resources.models import look_revision
    from src.resources.store import ResourceStore

    c, pdir, name = wclient
    r = c.post(f"/api/project/{name}/looks", json={
        "character_name": "Alice", "label": "L",
        "expected_document_revision": c.get(f"/api/project/{name}/resources").json()["document_revision"]})
    look_id, look_rev = r.json()["target"], r.json()["revision"]
    fake_gen = FakeAgnes()
    monkeypatch.setattr(deps_mod, "get_agnes_client", lambda: fake_gen, raising=True)
    g = c.post(f"/api/project/{name}/looks/{look_id}/generate", json={
        "expected_revision": look_rev})
    assert g.status_code == 200, g.text
    asset_id = g.json()["candidate_asset_ids"][0]
    rev_after_gen = look_revision(ResourceStore(pdir, name).load().looks[look_id])

    # 未审查直接 approve → 409 review_not_passed
    a0 = c.post(f"/api/project/{name}/looks/{look_id}/approve", json={
        "asset_id": asset_id, "expected_revision": rev_after_gen})
    assert a0.status_code == 409 and a0.json()["detail"]["error"] == "review_not_passed"

    # 审查（fake vision）→ pass → approve 200
    fv = FakeVision(PASS_JSON)
    monkeypatch.setattr(deps_mod, "get_agnes_client", lambda: fv, raising=True)
    rv = c.post(f"/api/project/{name}/looks/{look_id}/review", json={})
    assert rv.status_code == 200, rv.text
    assert rv.json()["candidate_reviews"][0]["overall"] == "pass"

    cur_rev = look_revision(ResourceStore(pdir, name).load().looks[look_id])
    a1 = c.post(f"/api/project/{name}/looks/{look_id}/approve", json={
        "asset_id": asset_id, "expected_revision": cur_rev})
    assert a1.status_code == 200 and a1.json()["ok"] is True, a1.text
    body = c.get(f"/api/project/{name}/looks/{look_id}").json()
    assert body["approval_status"] == "approved"
    assert body["approved_reference_asset_ids"] == [asset_id]
    assert body["latest_reviews"][asset_id]["overall"] == "pass"
