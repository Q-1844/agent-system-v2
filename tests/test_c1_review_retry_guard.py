"""Agent System V2 Phase C1 — Work A：per-resource review failure guard 测试（27.md §3.1，C54-C58）。

规则：同一资源 + 同一语义 basis + 连续 needs_fix ≥ 3 → 后续调用返回
`review_retry_exhausted`（不再 generate/review）。输入变化 / pass / 新 turn → 允许重试。
无 canonical 失败计数器（进程内存 runtime safeguard）。
"""
import asyncio
import json

import pytest

from src.agents.character_designer import (
    _execute_resource_look_prepare, _execute_resource_prop_register,
    _reset_review_retry_state, _review_basis_sig, _REVIEW_RETRY_STATE,
)
from src.resources.service import ResourceService
from tests.test_resource_agent_mutations import (
    FakeProvider, LOOK_NEEDSFIX_JSON, LOOK_PASS_JSON, PROP_FAIL_JSON, PROP_PASS_JSON,
    _doc_rev, _env, _runtime, _seed_ref_image,
)


@pytest.fixture(autouse=True)
def _fresh_guard():
    _reset_review_retry_state()
    yield
    _reset_review_retry_state()


def _needs_fix_call(rt, pdir, look_id=None, desc="wearing a black cloak", mid=None):
    args = {
        "character_name": "Alice", "label": "night cloak",
        "appearance_description": desc,
        "expected_resources_revision": _doc_rev(pdir),
        "approval_mode": "review_only",
    }
    if look_id:
        args["look_id"] = look_id
        args.pop("label")
        # 真实 agent 行为：resource.read 拿该 look 自身 revision 原样回传（update OCC 域）
        args["expected_resources_revision"] = ResourceService(
            pdir, pdir.name).read_look(look_id)["revision"]
    return asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps(args), None, mid))


def test_C54_same_basis_needs_fix_x3_then_guard(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    fake = FakeProvider(LOOK_NEEDSFIX_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    # 第 1 次：create + generate → needs_fix
    r1 = _needs_fix_call(rt, pdir)
    assert r1["ok"] and r1["review"]["overall"] == "needs_fix"
    look_id = r1["look_id"]
    # 第 2、3 次：同 look + 同 basis → needs_fix
    r2 = _needs_fix_call(rt, pdir, look_id=look_id)
    r3 = _needs_fix_call(rt, pdir, look_id=look_id)
    assert r2["review"]["overall"] == "needs_fix"
    assert r3["review"]["overall"] == "needs_fix"
    gen_calls_before = len(fake.gen_calls)
    vision_calls_before = len(fake.vision_calls)
    # 第 4 次：同 basis → review_retry_exhausted（不 generate、不 review）
    r4 = _needs_fix_call(rt, pdir, look_id=look_id)
    assert r4["ok"] is False
    assert r4["error"] == "review_retry_exhausted"
    assert r4["changed_resources"] == []
    assert len(fake.gen_calls) == gen_calls_before, "guard 后零生成"
    assert len(fake.vision_calls) == vision_calls_before, "guard 后零审查"
    # canonical 零变化
    from src.resources.models import look_revision
    doc = ResourceService(pdir, pdir.name).store.load()
    assert doc.looks[look_id].generation_status == "ready", "无新候选"


def test_C55_changed_inputs_retry_allowed(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    fake = FakeProvider(LOOK_NEEDSFIX_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    r1 = _needs_fix_call(rt, pdir)
    look_id = r1["look_id"]
    _needs_fix_call(rt, pdir, look_id=look_id)
    _needs_fix_call(rt, pdir, look_id=look_id)
    # 改 appearance（换描述）→ 新 basis → 放行
    r4 = _needs_fix_call(rt, pdir, look_id=look_id,
                         desc="wearing a black cloak with silver embroidery")
    assert r4["error"] != "review_retry_exhausted"
    assert r4["ok"] is True, "输入变化后允许重试"


def test_C56_new_user_turn_retry_allowed(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    fake = FakeProvider(LOOK_NEEDSFIX_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    r1 = _needs_fix_call(rt, pdir, mid="msg_turn1")
    look_id = r1["look_id"]
    _needs_fix_call(rt, pdir, look_id=look_id, mid="msg_turn1")
    _needs_fix_call(rt, pdir, look_id=look_id, mid="msg_turn1")
    # 新 turn（新 message_id）→ 用户明确要求再试 → 计数隔离，允许重试
    r4 = _needs_fix_call(rt, pdir, look_id=look_id, mid="msg_turn2")
    assert r4["error"] != "review_retry_exhausted"
    assert r4["ok"] is True


def test_C57_no_canonical_failure_counter(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    fake = FakeProvider(LOOK_NEEDSFIX_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    r1 = _needs_fix_call(rt, pdir)
    import json as _json
    raw = (pdir / "output" / "resources.json").read_text(encoding="utf-8")
    assert "needs_fix" not in raw and "retry" not in raw, "canonical 不记录失败计数"
    assert isinstance(_REVIEW_RETRY_STATE, dict), "仅进程内存态"
    assert _review_basis_sig(a=1) == _review_basis_sig(a=1)
    assert _review_basis_sig(a=1) != _review_basis_sig(a=2)


def test_C58_successful_review_resets_loop(tmp_path, monkeypatch):
    """pass 清零可观测序列：nf×2 → pass（清零）→ nf×2 后下一次仍放行
    （若无清零，计数会累积到 4 → 第 6 次即被 guard 拦截）。"""
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    fake = FakeProvider(LOOK_NEEDSFIX_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    r1 = _needs_fix_call(rt, pdir)
    look_id = r1["look_id"]
    _needs_fix_call(rt, pdir, look_id=look_id)          # count=2
    monkeypatch.setattr("deps.agnes_client", FakeProvider(LOOK_PASS_JSON), raising=False)
    ok1 = _needs_fix_call(rt, pdir, look_id=look_id)    # pass → 清零
    assert ok1["review"]["overall"] == "pass"
    monkeypatch.setattr("deps.agnes_client", FakeProvider(LOOK_NEEDSFIX_JSON), raising=False)
    _needs_fix_call(rt, pdir, look_id=look_id)          # count=1
    _needs_fix_call(rt, pdir, look_id=look_id)          # count=2
    r_next = _needs_fix_call(rt, pdir, look_id=look_id)  # 若无清零此处已 exhausted
    assert r_next["error"] != "review_retry_exhausted", "pass 清零后重新计数"
    assert r_next["review"]["overall"] == "needs_fix"   # count=3
    r_blocked = _needs_fix_call(rt, pdir, look_id=look_id)
    assert r_blocked["error"] == "review_retry_exhausted"


def test_C_prop_guard_same_reference(tmp_path, monkeypatch):
    """Prop：同一参考图连续 needs_fix ×3 → guard；换参考 → 放行。"""
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    fake = FakeProvider(PROP_FAIL_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    ref = _seed_ref_image(pdir, "guardA.png")
    args = {"name": "Lantern", "reference_local": ref,
            "expected_resources_revision": _doc_rev(pdir), "approval_mode": "review_only"}
    r1 = asyncio.run(_execute_resource_prop_register(rt, pdir.name, json.dumps(args), None, None))
    pid = r1["prop_id"]
    for _ in range(2):
        args2 = {"prop_id": pid, "reference_local": ref,
                 "expected_resources_revision": _doc_rev(pdir),
                 "approval_mode": "review_only"}
        asyncio.run(_execute_resource_prop_register(rt, pdir.name, json.dumps(args2), None, None))
    # 同参考第 4 次 → exhausted
    args3 = {"prop_id": pid, "reference_local": ref,
             "expected_resources_revision": _doc_rev(pdir), "approval_mode": "review_only"}
    r4 = asyncio.run(_execute_resource_prop_register(rt, pdir.name, json.dumps(args3), None, None))
    assert r4["ok"] is False and r4["error"] == "review_retry_exhausted"
    # 换参考 → 放行
    ref2 = _seed_ref_image(pdir, "guardB.png")
    args4 = {"prop_id": pid, "reference_local": ref2,
             "expected_resources_revision": _doc_rev(pdir), "approval_mode": "review_only"}
    r5 = asyncio.run(_execute_resource_prop_register(rt, pdir.name, json.dumps(args4), None, None))
    assert r5["error"] != "review_retry_exhausted"
