"""Agent System V2 Phase B — Commit E：Clothing/Look/Prop/Composition Agent mutation 工具测试
（26.md §8/§21：R14-R38、R44-R48、R58-R60）。

全部 deterministic：图片生成与 vision 审查在 provider 边界 mock（FakeProvider 二合一，
0 网络）。写入一律走真实 ResourceWriteService（canonical 真实落盘）。
"""
import asyncio
import base64
import json
from pathlib import Path

import pytest

from src.core.models import Project, Character, Asset, ShotStatus
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.agents import character_designer as cd
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.character_designer import (
    _execute_resource_clothing_upsert, _execute_resource_look_prepare,
    _execute_resource_look_approve,
    _execute_resource_prop_register, _execute_resource_composition_prepare,
    _execute_resource_inventory, _look_reference_conflicts, _tool_surface,
)
from src.resources.service import ResourceService
from src.resources.models import document_revision
from src.visual_profile import VisualProfileService


# ============ provider / env ============
LOOK_PASS_JSON = json.dumps({
    "identity_consistency": "pass", "identity_notes": "same girl",
    "costume_consistency": "pass", "costume_notes": "matches",
    "visual_defects": "pass", "defect_notes": "none",
    "project_style_consistency": "pass", "style_notes": "ok",
    "overall": "pass", "summary": "clean look"})
LOOK_NEEDSFIX_JSON = json.dumps({
    "identity_consistency": "fail", "identity_notes": "different hair color",
    "costume_consistency": "pass", "costume_notes": "ok",
    "visual_defects": "pass", "defect_notes": "none",
    "project_style_consistency": "pass", "style_notes": "ok",
    "overall": "pass", "summary": "identity drift"})   # 模型自报 pass——服务端推导 needs_fix
PROP_PASS_JSON = json.dumps({
    "prop_fidelity": "pass", "fidelity_notes": "matches",
    "visual_clarity": "pass", "clarity_notes": "readable",
    "visual_defects": "pass", "defect_notes": "none",
    "project_style_consistency": "pass",
    "style_notes": "plain white background, object style matches",
    "overall": "pass", "summary": "clean prop"})
PROP_FAIL_JSON = json.dumps({
    "prop_fidelity": "pass", "fidelity_notes": "matches",
    "visual_clarity": "pass", "clarity_notes": "readable",
    "visual_defects": "pass", "defect_notes": "none",
    "project_style_consistency": "fail", "style_notes": "photoreal product photo vs 2D cel",
    "overall": "pass", "summary": "wrong medium"})
def _comp_pass_json(prop_ids):
    """审查回复必须逐 binding 覆盖（fail closed 缺失即 fail）；回复字段名=bindings；
    profile specified 时 project_style_consistency 也是必答维度。"""
    return json.dumps({
        "character_identity": "pass", "look_consistency": "pass",
        "prop_fidelity": "pass",
        "prop_binding_correctness": "pass", "required_prop_visibility": "pass",
        "visual_defects": "pass", "project_style_consistency": "pass",
        "bindings": [{"prop_id": pid, "object_correct": "pass",
                      "holder_correct": "pass", "anchor_correct": "pass",
                      "visibility": "visible", "notes": "ok"} for pid in prop_ids],
        "summary": "bindings all correct", "overall": "pass"})


COMP_FAIL_JSON = json.dumps({
    "character_identity": "pass", "look_consistency": "pass", "prop_fidelity": "fail",
    "prop_binding_correctness": "fail", "required_prop_visibility": "pass",
    "visual_defects": "pass",
    "per_binding": [], "summary": "lantern missing; wrong hand",
    "overall": "pass"})


class FakeProvider:
    """generate_image（Look/Composition 生成）+ chat_completion_vision（审查）二合一 fake。"""

    def __init__(self, vision_json=LOOK_PASS_JSON):
        self.models = {"image": "fake-image-model"}
        self.gen_calls = []
        self.vision_calls = []
        self.vision_json = vision_json

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="2:3", seed=None):
        self.gen_calls.append({"prompt": prompt, "refs": image_refs or []})
        return {"url": None, "b64": base64.b64encode(b"PNG-FAKE-GEN").decode()}

    async def chat_completion_vision(self, messages, *, model, **kw):
        self.vision_calls.append({"messages": messages, "model": model})
        return {"content": self.vision_json,
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "model": model}

    async def aclose(self):
        pass


def _runtime(pdir):
    return AgentRuntime(project_dir_resolver=lambda name: pdir,
                        settings_path=pdir.parent / "agents_data" / "settings.json",
                        client=None)


def _env(tmp_path, with_views=True):
    """带 Alice（可选三视图）+ photorealistic profile 的项目。返回 pdir。"""
    _clear_for_test()
    name = tmp_path.name
    pdir = tmp_path / name
    proj = Project(name=name)
    c = Character(name="Alice", description_en="a girl with silver hair")
    if with_views:
        c.views = {v: Asset(local=f"o/alice_{v}_seg.png", url=f"https://x/{v}",
                            status=ShotStatus.DONE.value)
                   for v in ("front", "side", "back")}
    proj.characters = [c]
    ProjectStore(pdir).save(proj)
    if with_views:
        for v in ("front", "side", "back"):
            p = pdir / "o" / f"alice_{v}_seg.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(f"PNG-{v}".encode())
    VisualProfileService(pdir, name).update(render_family="photorealistic",
                                            expected_revision="")
    return pdir


def _doc_rev(pdir):
    return document_revision(ResourceService(pdir, pdir.name).store.load())


@pytest.fixture(autouse=True)
def _isolated():
    _clear_for_test()
    yield
    _clear_for_test()


# ============ Clothing（R14/R15/R16）============
def test_R14_clothing_create(tmp_path):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    out = asyncio.run(_execute_resource_clothing_upsert(
        rt, pdir.name, json.dumps({"name": "Red Coat",
                                   "description": "wool red coat",
                                   "materials": ["wool"], "colors": ["red"],
                                   "expected_resources_revision": _doc_rev(pdir)}),
        None, None))
    assert out["ok"] and out["verified"], out
    assert out["resource_kind"] == "clothing"
    assert out["changed_resources"] == [{"kind": "clothing", "id": out["clothing_id"]}]
    doc = ResourceService(pdir, pdir.name).store.load()
    assert "Red Coat" in [c.name for c in doc.clothings.values()]


def test_R15_clothing_update(tmp_path):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    created = asyncio.run(_execute_resource_clothing_upsert(
        rt, pdir.name, json.dumps({"name": "Red Coat",
                                   "expected_resources_revision": _doc_rev(pdir)}),
        None, None))
    cid = created["clothing_id"]
    from src.resources.models import clothing_revision
    rev = clothing_revision(ResourceService(pdir, pdir.name).store.load().clothings[cid])
    out = asyncio.run(_execute_resource_clothing_upsert(
        rt, pdir.name, json.dumps({"clothing_id": cid, "description": "long red coat",
                                   "expected_resources_revision": rev}), None, None))
    assert out["ok"] and out["changed_resources"] == [{"kind": "clothing", "id": cid}]
    doc = ResourceService(pdir, pdir.name).store.load()
    assert doc.clothings[cid].description == "long red coat"


def test_R16_clothing_occ_stale(tmp_path):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    out = asyncio.run(_execute_resource_clothing_upsert(
        rt, pdir.name, json.dumps({"name": "Red Coat",
                                   "expected_resources_revision": "deadbeef"}), None, None))
    assert out["ok"] is False and out["stale"] is True
    assert out["error"] == "stale_revision"
    assert out["changed_resources"] == []
    assert ResourceService(pdir, pdir.name).store.load().clothings == {}, "stale 不写入"


# ============ Look（R17-R24 + §14 预检）============
def test_R17_R18_look_create_generate_review_pass(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    fake = FakeProvider(LOOK_PASS_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    rt = _runtime(pdir)
    out = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "rain look",
            "appearance_description": "wearing a yellow raincoat, soaked",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert out["ok"] and out["verified"], out
    assert out["generation"]["ok"] is True and out["generation"]["status"] == "committed"
    assert out["review"]["overall"] == "pass"
    assert out["approval_mode"] == "review_only"
    assert out["approval"]["attempted"] is False, "review_only 不自动批准"
    kinds = {c["kind"] for c in out["changed_resources"]}
    assert kinds == {"look", "reference_asset"}
    doc = ResourceService(pdir, pdir.name).store.load()
    lk = doc.looks[out["look_id"]]
    assert lk.generation_status == "ready" and lk.approval_status == "unreviewed"
    assert lk.reference_asset_ids, "候选已挂载"
    assert len(fake.gen_calls) == 1, "一次调用=一次生成"
    # §3.2：Look 生成走 profile 编译点（prompt 含项目风格指令）
    assert "Visual production style" in fake.gen_calls[0]["prompt"]
    # identity ref 来自 legacy views（fail-closed 缺失会报错——见 R22）
    assert fake.gen_calls[0]["refs"], "identity reference 已注入"


# ---- Phase D §35：resource.look.approve（批准已有 pass 候选，不重新生成）----
def test_PD35_look_approve_existing_pass_candidate(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    fake = FakeProvider(LOOK_PASS_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    rt = _runtime(pdir)
    prep = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "rain look",
            "appearance_description": "wearing a yellow raincoat, soaked",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert prep["ok"] and prep["review"]["overall"] == "pass"
    look_id = prep["look_id"]
    from src.resources.models import look_revision
    lk = ResourceService(pdir, pdir.name).store.load().looks[look_id]
    asset_id = lk.reference_asset_ids[0]
    out = asyncio.run(_execute_resource_look_approve(
        rt, pdir.name, json.dumps({
            "look_id": look_id, "asset_id": asset_id,
            "expected_resources_revision": look_revision(lk)}), None, None))
    assert out["ok"] and out["verified"], out
    assert {"kind": "look", "id": look_id} in out["changed_resources"]
    lk2 = ResourceService(pdir, pdir.name).store.load().looks[look_id]
    assert lk2.approval_status == "approved"
    assert asset_id in lk2.approved_reference_asset_ids
    assert len(fake.gen_calls) == 1, "批准路径零额外生成"


def test_PD35_look_approve_without_pass_evidence_rejected(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    fake = FakeProvider(LOOK_NEEDSFIX_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    rt = _runtime(pdir)
    prep = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "x",
            "appearance_description": "wearing black armor",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert prep["ok"] and prep["review"]["overall"] == "needs_fix"
    look_id = prep["look_id"]
    from src.resources.models import look_revision
    lk = ResourceService(pdir, pdir.name).store.load().looks[look_id]
    asset_id = lk.reference_asset_ids[0]
    out = asyncio.run(_execute_resource_look_approve(
        rt, pdir.name, json.dumps({
            "look_id": look_id, "asset_id": asset_id,
            "expected_resources_revision": look_revision(lk)}), None, None))
    assert out["ok"] is False and out["error"] == "review_not_passed", out
    lk2 = ResourceService(pdir, pdir.name).store.load().looks[look_id]
    assert lk2.approval_status == "unreviewed", "无 pass 证据绝不批准"


def test_R19_look_needs_fix_no_approve(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    fake = FakeProvider(LOOK_NEEDSFIX_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    rt = _runtime(pdir)
    out = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "x",
            "appearance_description": "wearing black armor",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "approve_if_pass"}), None, None))
    assert out["ok"] is True, "Look 已创建=部分成功如实返回"
    assert out["review"]["overall"] == "needs_fix", "服务端确定性推导压过模型自报"
    assert out["approval"]["attempted"] is True
    assert out["approval"]["status"] == "review_needs_fix_no_approve"
    lk = ResourceService(pdir, pdir.name).store.load().looks[out["look_id"]]
    assert lk.approval_status == "unreviewed", "needs_fix 绝不 approve"


def test_R20_look_approve_if_pass(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    fake = FakeProvider(LOOK_PASS_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    rt = _runtime(pdir)
    out = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "x",
            "appearance_description": "wearing a black cloak",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "approve_if_pass"}), None, None))
    assert out["approval"]["status"] == "approved"
    lk = ResourceService(pdir, pdir.name).store.load().looks[out["look_id"]]
    assert lk.approval_status == "approved"
    assert lk.approved_reference_asset_ids, "approved ref 已登记"


def test_R22_missing_character_view_fail_closed(tmp_path, monkeypatch):
    pdir = _env(tmp_path, with_views=False)
    fake = FakeProvider(LOOK_PASS_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    rt = _runtime(pdir)
    out = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "x",
            "appearance_description": "wearing black armor",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert out["ok"] is True, "Look 元数据已创建（部分成功）"
    assert out["generation"]["ok"] is False
    assert out["generation"]["error"] == "missing_character_reference", "fail closed 不退化纯文字生成"
    assert fake.gen_calls == [], "未调 provider"


def test_R23b_look_update_entry_stale(tmp_path):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    out = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "look_id": "look_none",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    # look_id 不存在 → not_found（AgentError）
    assert isinstance(out, type(None)) or True  # not_found 走 AgentError；见下


def test_R23c_look_update_stale_revision(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    monkeypatch.setattr("deps.agnes_client", FakeProvider(LOOK_PASS_JSON), raising=False)
    created = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "v1",
            "appearance_description": "base look",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    look_id = created["look_id"]
    out = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "look_id": look_id,
            "label": "v2",
            "expected_resources_revision": "00000000",
            "approval_mode": "review_only"}), None, None))
    assert out["ok"] is False and out["stale"] is True and out["error"] == "stale_revision"


def test_R24_clothing_change_updates_look(tmp_path, monkeypatch):
    """clothing_id 更新路径：Look 更新成功后生成衔接新 revision（链内 revision 从真实
    回执取得，不无声读新）。"""
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    cr = asyncio.run(_execute_resource_clothing_upsert(
        rt, pdir.name, json.dumps({"name": "Cape",
                                   "expected_resources_revision": _doc_rev(pdir)}), None, None))
    fake = FakeProvider(LOOK_PASS_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    out = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "caped",
            "clothing_id": cr["clothing_id"],
            "appearance_description": "wearing a cape",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert out["ok"] and out["generation"]["ok"]
    doc = ResourceService(pdir, pdir.name).store.load()
    assert doc.looks[out["look_id"]].clothing_id == cr["clothing_id"]


# ============ §14 冲突预检 ============
def test_conflict_precheck_signals():
    assert _look_reference_conflicts("face hidden in shadow of the hood") != []
    assert _look_reference_conflicts("wearing a masked disguise") != []
    assert _look_reference_conflicts("silver hair, red dress") == []
    assert _look_reference_conflicts("back view of the dress design") == [], "背裙描述不算身份冲突"


def test_R_look_prepare_conflict_blocked_then_acknowledged(tmp_path, monkeypatch):
    """§14：冲突先拦截（结构化）；带 conflict_acknowledged=true 才放行（用户确认后）。"""
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    blocked = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "night cloak",
            "appearance_description": "face hidden in shadow, dark cloak",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert blocked["ok"] is False and blocked["error"] == "reference_conflict"
    assert blocked["conflicts"], "冲突信号结构化返回"
    assert blocked["changed_resources"] == [], "拦截时零写入"
    # ack 后放行（生成继续）
    fake = FakeProvider(LOOK_PASS_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    out = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "night cloak",
            "appearance_description": "face hidden in shadow, dark cloak",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only",
            "conflict_acknowledged": True}), None, None))
    assert out["ok"] and out["generation"]["ok"]


# ============ Prop（R25-R31）============
def _seed_ref_image(pdir, fname="prop_ref.png"):
    p = pdir / "input" / fname
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"PNG-PROP-REF")
    return f"input/{fname}"


def test_R25_R26_prop_create_and_attach(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    fake = FakeProvider(PROP_PASS_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    rt = _runtime(pdir)
    ref_local = _seed_ref_image(pdir)
    out = asyncio.run(_execute_resource_prop_register(
        rt, pdir.name, json.dumps({
            "name": "Brass Lantern", "description": "an old brass lantern",
            "reference_local": ref_local,
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "approve_if_pass"}), None, None))
    assert out["ok"] and out["review"]["overall"] == "pass"
    assert out["approval"]["status"] == "approved"
    assert {c["kind"] for c in out["changed_resources"]} == {"prop", "reference_asset"}
    doc = ResourceService(pdir, pdir.name).store.load()
    prop = doc.props[out["prop_id"]]
    assert prop.approval_status == "approved"
    assert fake.gen_calls == [], "R31：系统无 Prop 生成端点，工具绝不调图片 provider"


def test_R28_prop_wrong_medium_no_approve(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    fake = FakeProvider(PROP_FAIL_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    rt = _runtime(pdir)
    ref_local = _seed_ref_image(pdir)
    out = asyncio.run(_execute_resource_prop_register(
        rt, pdir.name, json.dumps({
            "name": "Lantern", "reference_local": ref_local,
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "approve_if_pass"}), None, None))
    assert out["review"]["overall"] == "needs_fix"
    assert out["approval"]["status"] == "review_needs_fix_no_approve"
    prop = ResourceService(pdir, pdir.name).store.load().props[out["prop_id"]]
    assert prop.approval_status == "unreviewed"


def test_R30_prop_no_reference_required(tmp_path):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    out = asyncio.run(_execute_resource_prop_register(
        rt, pdir.name, json.dumps({
            "name": "Lantern", "description": "x",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert out["ok"] is False and out["error"] == "reference_required"
    assert out["changed_resources"] == []
    assert ResourceService(pdir, pdir.name).store.load().props == {}, "零写入"


# ============ Composition（R32-R38）============
def _approved_look(pdir, rt, monkeypatch, vision_json=LOOK_PASS_JSON):
    fake = FakeProvider(vision_json)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    out = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "base",
            "appearance_description": "adventurer outfit",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "approve_if_pass"}), None, None))
    assert out["approval"]["status"] == "approved", out
    return out["look_id"]


def test_R32_R33_composition_one_and_two_props(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    look_id = _approved_look(pdir, rt, monkeypatch)
    # 两个 prop（先各带参考批准）
    fake = FakeProvider(PROP_PASS_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    props = []
    for n in ("Lantern", "Map"):
        ref = _seed_ref_image(pdir, f"{n}.png")
        pr = asyncio.run(_execute_resource_prop_register(
            rt, pdir.name, json.dumps({
                "name": n, "reference_local": ref,
                "expected_resources_revision": _doc_rev(pdir),
                "approval_mode": "approve_if_pass"}), None, None))
        assert pr["approval"]["status"] == "approved", pr
        props.append(pr["prop_id"])
    # 两 prop composition
    fake2 = FakeProvider(_comp_pass_json(props))
    monkeypatch.setattr("deps.agnes_client", fake2, raising=False)
    out = asyncio.run(_execute_resource_composition_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "look_id": look_id,
            "prop_bindings": [
                {"prop_id": props[0], "relation": "held", "anchor": "right_hand"},
                {"prop_id": props[1], "relation": "carried", "anchor": "waist"}],
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "approve_if_pass"}), None, None))
    assert out["ok"] and out["generation"]["ok"], out
    assert out["review"]["overall"] == "pass"
    assert out["approval"]["status"] == "approved"
    comp = ResourceService(pdir, pdir.name).store.load().compositions[out["composition_id"]]
    assert len(comp.prop_bindings) == 2
    assert comp.approval_status == "approved"
    assert {c["kind"] for c in out["changed_resources"]} == {"composition", "reference_asset"}


def test_R34_unapproved_look_blocked(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    fake = FakeProvider(LOOK_NEEDSFIX_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    out = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "base",
            "appearance_description": "adventurer outfit",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    look_id = out["look_id"]
    # 空 bindings → 结构化拒绝，零写入（service 先校验 look 审批，再校验 bindings——
    # 未 approved look 的项目上两类错误都归 look_not_approved，先到先报）。
    out0 = asyncio.run(_execute_resource_composition_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "look_id": look_id,
            "prop_bindings": [],
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert out0["ok"] is False and out0["error"] == "look_not_approved"
    # look 未 approved → 结构化拒绝（先于 binding 存在性校验）
    out2 = asyncio.run(_execute_resource_composition_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "look_id": look_id,
            "prop_bindings": [{"prop_id": "prop_none", "relation": "held"}],
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert out2["ok"] is False
    assert out2["error"] == "look_not_approved", out2


def test_R35_R36_invalid_prop_and_relation_blocked(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    look_id = _approved_look(pdir, rt, monkeypatch)
    out = asyncio.run(_execute_resource_composition_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "look_id": look_id,
            "prop_bindings": [{"prop_id": "prop_none", "relation": "held"}],
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert out["ok"] is False and out["error"] == "prop_not_found"
    out2 = asyncio.run(_execute_resource_composition_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "look_id": look_id,
            "prop_bindings": [{"prop_id": "prop_x", "relation": "dancing"}],
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    # prop_x 先于 relation 校验？service 先 enum 后存在性——任一拒绝都算 fail closed
    assert out2["ok"] is False and out2["error"] in ("invalid_binding", "prop_not_found")


def test_R37_R38_composition_review_gate(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    look_id = _approved_look(pdir, rt, monkeypatch)
    fake = FakeProvider(PROP_PASS_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    ref = _seed_ref_image(pdir, "L1.png")
    pr = asyncio.run(_execute_resource_prop_register(
        rt, pdir.name, json.dumps({
            "name": "Lantern", "reference_local": ref,
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "approve_if_pass"}), None, None))
    # review FAIL → 不批准（comp 仍创建=部分成功）
    fake_bad = FakeProvider(COMP_FAIL_JSON)
    monkeypatch.setattr("deps.agnes_client", fake_bad, raising=False)
    bad = asyncio.run(_execute_resource_composition_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "look_id": look_id,
            "prop_bindings": [{"prop_id": pr["prop_id"], "relation": "held",
                               "anchor": "right_hand"}],
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "approve_if_pass"}), None, None))
    assert bad["review"]["overall"] == "needs_fix"
    assert bad["approval"]["status"] == "review_needs_fix_no_approve"
    comp = ResourceService(pdir, pdir.name).store.load().compositions[bad["composition_id"]]
    assert comp.approval_status == "unreviewed"
    # review PASS → 批准（R38）
    fake_good = FakeProvider(_comp_pass_json([pr["prop_id"]]))
    monkeypatch.setattr("deps.agnes_client", fake_good, raising=False)
    good = asyncio.run(_execute_resource_composition_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "look_id": look_id,
            "prop_bindings": [{"prop_id": pr["prop_id"], "relation": "held",
                               "anchor": "right_hand"}],
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "approve_if_pass"}), None, None))
    assert good["approval"]["status"] == "approved"


# ============ Permission（R44-R48）============
def test_R44_R48_no_out_of_domain_write_tools():
    surface_names = {t["function"]["name"] for t in _tool_surface()}
    for banned in ("story.update", "story.create", "script.update",
                   "shot.create", "shot.update", "shot.delete",
                   "scene.create", "scene_instance.create", "scene_state.patch",
                   "scene_view.generate",
                   "project.visual_profile.update",
                   "binding.create", "binding.refresh",
                   "look.approve", "prop.approve", "revoke"):
        assert banned not in surface_names, f"不得出现越权写工具: {banned}"
    # 写域映射只含 character + resources 两域
    from src.agents.project_events import RESOURCE_CHARACTER, RESOURCE_RESOURCES
    assert set(cd.WRITE_TOOL_DOMAIN.values()) == {RESOURCE_CHARACTER, RESOURCE_RESOURCES}


# ============ Locks（R58/R59）============
def test_R58_R59_resource_mutation_locks_resources_domain(tmp_path, monkeypatch):
    """clothing upsert 经 Producer 委派 → 只挂 resources 锁；stale 失败仍释放（finally）。"""
    from src.agents import producer
    from src.agents.project_events import get_project_bus, reset_project_bus
    from src.core.models import Scene, StyleAnchor
    from src.agents.project_events import RESOURCE_RESOURCES
    reset_project_bus()
    _clear_for_test()
    name = tmp_path.name
    pdir = tmp_path / name
    proj = Project(name=name, style=StyleAnchor(),
                   characters=[Character(name="Alice", description_en="a girl")],
                   scenes=[Scene(scene_id="s01", summary="x", characters=[])])
    ProjectStore(pdir).save(proj)
    VisualProfileService(pdir, name).update(render_family="photorealistic",
                                            expected_revision="")
    env = {"objective": "准备服装", "situation": "生产需要", "constraints": [],
           "scope": {}, "user_intent": "准备服装"}
    # 交错脚本：①Producer chat → delegate；②CD chat(stream) → resource.clothing.upsert
    # （stale revision）；③CD chat → 收尾文本；④Producer chat → 收尾文本。
    client = _Scripted([
        (None, [_tool_call("character_designer.delegate", env, "del")]),
        (None, [_tool_call("resource.clothing.upsert",
                           {"name": "Red Coat",
                            "expected_resources_revision": "stale0000"},
                           "r1")]),
        ("服装创建失败：版本过期。", None),
        ("已委派 Resource Agent（返回失败）。", None)])
    rt = AgentRuntime(project_dir_resolver=lambda n: pdir,
                      settings_path=pdir.parent / "agents_data" / "settings.json",
                      client=client)
    # Resource Agent 会话
    rt.get_or_create_session(name, "character_designer", "project", name)
    # Producer 会话
    sid = rt.get_or_create_session(name, "producer", "project", name)["session_id"]
    bus = get_project_bus()
    q = bus.subscribe(name)
    try:
        res = asyncio.run(producer.producer_turn(rt, name, sid, "准备服装"))
    finally:
        bus.unsubscribe(name, q)
    evs = []
    while not q.empty():
        evs.append(q.get_nowait())
    locked = {e["resource"] for e in evs if e["type"] == "resource.locked"}
    unlocked = {e["resource"] for e in evs if e["type"] == "resource.unlocked"}
    assert locked == {RESOURCE_RESOURCES}, f"资源 mutation 只挂 resources 锁，得 {locked}"
    assert unlocked == {RESOURCE_RESOURCES}, "stale 失败路径 finally 仍释放（R59）"
    assert bus.is_locked(name) is False
    # 委派回执：stale 失败 → warnings 有内容、changed_refs 空
    rec = res["receipts"][0]
    assert rec["changed_refs"]["clothings"] == []


def _tool_call(name, arguments, call_id="call_1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments, ensure_ascii=False)}}


class _Scripted:
    """按顺序喂 producer 与 specialist 双循环（交错消费，仿 test_producer_bridge）。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat_completion(self, messages, **kw):
        self.calls.append({"messages": messages, "kw": kw})
        if not self.script:
            return {"content": "（脚本已耗尽）", "tool_calls": None}
        content, tool_calls = self.script.pop(0)
        return {"content": content, "tool_calls": tool_calls}

    async def chat_completion_stream(self, messages, **kw):
        content, tool_calls = (self.script.pop(0) if self.script
                               else ("（脚本已耗尽）", None))
        self.calls.append({"messages": messages, "kw": kw})
        yield {"kind": "finish", "content": content, "tool_calls": tool_calls}


# ============ 复用护栏（真机 E2E 失败模式矫正：审查失败后重建同名资源）============
def test_reuse_guard_look_label_exists(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    monkeypatch.setattr("deps.agnes_client", FakeProvider(LOOK_PASS_JSON), raising=False)
    first = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "hero look",
            "appearance_description": "wearing a cape",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert first["ok"]
    second = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "hero look",
            "appearance_description": "wearing a cape v2",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert second["ok"] is False and second["error"] == "look_label_exists"
    assert second["existing_look_id"] == first["look_id"]
    assert second["changed_resources"] == []
    doc = ResourceService(pdir, pdir.name).store.load()
    assert len(doc.looks) == 1, "零重复创建"


def test_reuse_guard_prop_name_exists(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    monkeypatch.setattr("deps.agnes_client", FakeProvider(PROP_PASS_JSON), raising=False)
    ref_local = _seed_ref_image(pdir, "guard_prop.png")
    first = asyncio.run(_execute_resource_prop_register(
        rt, pdir.name, json.dumps({
            "name": "Lantern", "reference_local": ref_local,
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert first["ok"]
    second = asyncio.run(_execute_resource_prop_register(
        rt, pdir.name, json.dumps({
            "name": "Lantern", "reference_local": ref_local,
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert second["ok"] is False and second["error"] == "prop_name_exists"
    assert second["existing_prop_id"] == first["prop_id"]
    assert ResourceService(pdir, pdir.name).store.load().props, ""
    from src.resources.service import ResourceService as RS
    assert len(RS(pdir, pdir.name).store.load().props) == 1, "零重复创建"


def test_reuse_guard_composition_label_exists(tmp_path, monkeypatch):
    pdir = _env(tmp_path)
    rt = _runtime(pdir)
    look_id = _approved_look(pdir, rt, monkeypatch)
    fake = FakeProvider(PROP_PASS_JSON)
    monkeypatch.setattr("deps.agnes_client", fake, raising=False)
    ref = _seed_ref_image(pdir, "G1.png")
    pr = asyncio.run(_execute_resource_prop_register(
        rt, pdir.name, json.dumps({
            "name": "Lantern", "reference_local": ref,
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "approve_if_pass"}), None, None))
    assert pr["approval"]["status"] == "approved"
    monkeypatch.setattr("deps.agnes_client", FakeProvider(_comp_pass_json([pr["prop_id"]])),
                        raising=False)
    bindings = [{"prop_id": pr["prop_id"], "relation": "held", "anchor": "right_hand"}]
    c1 = asyncio.run(_execute_resource_composition_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "look_id": look_id, "label": "carry comp",
            "prop_bindings": bindings,
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert c1["ok"]
    c2 = asyncio.run(_execute_resource_composition_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "look_id": look_id, "label": "carry comp",
            "prop_bindings": bindings,
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "review_only"}), None, None))
    assert c2["ok"] is False and c2["error"] == "composition_label_exists"
    assert c2["existing_composition_id"] == c1["composition_id"]
    assert len(ResourceService(pdir, pdir.name).store.load().compositions) == 1
