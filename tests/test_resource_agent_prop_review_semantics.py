"""Agent System V2 Phase B — Commit C：Prop Review 判卷口径修复测试（26.md §4）。

背景（Golden Run §六.10 P1 遗留）：Prop reference 是 isolated object / clean background /
neutral presentation 的生产设定图，旧 project_style_consistency 口径会把「背景没有项目
电影感」误判成风格失败，阻塞 B 类项目道具审批。

修复语义（§4.1，不改 schema 不加维度）：
- 判卷对象 = 道具本体（材质/造型语言/纹理/线条/时代感/渲染介质）vs ProjectVisualProfile；
- 素底/白底/透明底/简洁背景本身不得导致 style fail；
- 真正的介质错配（2D cel 项目出 photoreal product photo）仍必须 fail。

测试全部 deterministic：vision 调用 mock（FakeVision），0 网络。
"""
import asyncio
import json

from src.core.write_lock import _clear_for_test
from src.resources.service import ResourceService
from src.resources.write_service import ResourceWriteService
from src.resources.prop_review import (STYLE_DIMENSION, _PROP_DIMENSIONS,
                                       build_prop_review_prompt,
                                       derive_prop_overall,
                                       has_prop_pass_evidence,
                                       review_prop_reference)

_ALL_DIMS = _PROP_DIMENSIONS + (STYLE_DIMENSION,)
from tests.test_resources_phase1c1 import make_project, make_view_files, run
from tests.test_resources_phase1c2 import FakeVision

NEUTRAL_BG_NOTE = ("prop presented as isolated object on plain white background; "
                   "object itself matches the declared 2D cel style")


def _env(tmp_path):
    _clear_for_test()
    pdir = make_project(tmp_path, with_character=True)
    make_view_files(pdir)
    ws = ResourceWriteService(pdir, pdir.name)
    return pdir, ws


def _doc_rev(ws):
    from src.resources.models import document_revision
    return document_revision(ws.store.load())


async def _seed_prop(pdir, ws, *, name="Wooden Sword",
                     description="a short wooden training sword",
                     img_name="sword_ref.png"):
    img = pdir / "o" / img_name
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(f"PNG-REF-{img_name}".encode())
    r = await ws.create_prop(name=name, description=description,
                             expected_document_revision=_doc_rev(ws))
    assert r["ok"], r
    prop_id = r["target"]
    r2 = await ws.register_reference_asset(
        local=f"o/{img_name}", view="hero", purpose="prop",
        expected_document_revision=r["document_revision_after"])
    assert r2["ok"], r2
    asset_id = r2["target"]
    from src.resources.models import prop_revision
    r3 = await ws.attach_prop_reference(prop_id, asset_id,
                                        expected_revision=prop_revision(
                                            ws.store.load().props[prop_id]))
    assert r3["ok"], r3
    return prop_id, asset_id


# ============ C1：审查 prompt 口径（确定性文本断言）============
def test_C1_prompt_judges_prop_object_not_background():
    prompt = build_prop_review_prompt(
        prop_name="Wooden Sword", prop_description="a short wooden training sword",
        prop_tags=["wood"],
        style_expectation="Project style expectation: 2D cel animation.")
    # ① 判卷对象是道具本体
    assert "PROP OBJECT ITSELF" in prompt
    assert "materials, shape language" in prompt
    # ② 素底/白底/透明底/简洁背景本身不得导致 fail
    assert "must NOT by itself" in prompt
    assert "neutral / plain / white / transparent / simple background" in prompt
    assert "do not demand scene atmosphere" in prompt
    # ③ 真正介质错配仍 fail（hard 语义保留）
    assert "photorealistic product photograph" in prompt and "must fail" in prompt
    # ④ 共享编译点的项目风格期望段仍在（同一唯一真相）
    assert "Project style expectation: 2D cel animation." in prompt
    # ⑤ schema 不变：仍是 4 维 + overall
    assert prompt.count("pass|concern|fail") == 4


def test_C1b_prompt_without_profile_keeps_three_dims():
    prompt = build_prop_review_prompt(prop_name="X", prop_description="",
                                      style_expectation="")
    assert "project_style_consistency" not in prompt
    assert prompt.count("pass|concern|fail") == 3


def test_C1c_overall_derivation_unchanged():
    # 4 维全 pass → pass；style fail → needs_fix（与 review_prop_reference 的调用口径一致：
    # 传全维度集）；3 维口径（无 profile）下行为不变。
    assert derive_prop_overall({"prop_fidelity": "pass", "visual_clarity": "pass",
                                "visual_defects": "pass",
                                "project_style_consistency": "pass"}, _ALL_DIMS) == "pass"
    assert derive_prop_overall({"prop_fidelity": "pass", "visual_clarity": "pass",
                                "visual_defects": "pass",
                                "project_style_consistency": "fail"}, _ALL_DIMS) == "needs_fix"
    assert derive_prop_overall({"prop_fidelity": "concern", "visual_clarity": "pass",
                                "visual_defects": "pass"}) == "uncertain"
    assert derive_prop_overall({"prop_fidelity": None, "visual_clarity": "pass",
                                "visual_defects": "pass"}) == "uncertain"


# ============ R27：素底设定图不因背景本身 style fail ============
def test_R27_neutral_background_style_pass(tmp_path):
    pdir, ws = _env(tmp_path)
    prop_id, asset_id = run(_seed_prop(pdir, ws))
    vision_json = json.dumps({
        "prop_fidelity": "pass", "fidelity_notes": "wooden training sword as described",
        "visual_clarity": "pass", "clarity_notes": "whole object readable",
        "visual_defects": "pass", "defect_notes": "none",
        "project_style_consistency": "pass", "style_notes": NEUTRAL_BG_NOTE,
        "overall": "pass", "summary": "clean prop reference on neutral background"})
    fv = FakeVision(vision_json)
    r = run(review_prop_reference(pdir, prop_id, agnes_client=fv))
    assert r["ok"] and r["status"] == "completed", r
    cr = r["candidate_reviews"][0]
    assert cr["overall"] == "pass", "素底 + 道具本体合风格 → pass"
    assert cr["style_notes"] == NEUTRAL_BG_NOTE
    # pass 证据可用（approve 门槛打开）
    from src.resources.prop_review import has_prop_pass_evidence
    assert has_prop_pass_evidence(pdir, prop_id, asset_id) is True
    # record 诚实标注风格口径已 evaluated
    assert r["review_record"]["project_style_consistency"] == "evaluated"


# ============ R28：真正介质错配仍 fail（hard 语义保留）============
def test_R28_genuine_medium_mismatch_still_fails(tmp_path):
    pdir, ws = _env(tmp_path)
    prop_id, asset_id = run(_seed_prop(pdir, ws))
    vision_json = json.dumps({
        "prop_fidelity": "pass", "fidelity_notes": "object is the described sword",
        "visual_clarity": "pass", "clarity_notes": "readable",
        "visual_defects": "pass", "defect_notes": "none",
        "project_style_consistency": "fail",
        "style_notes": "photorealistic product photo; project declares 2D cel animation",
        "overall": "pass",   # 模型自报 pass —— 服务端确定性推导必须压过
        "summary": "wrong medium"})
    fv = FakeVision(vision_json)
    r = run(review_prop_reference(pdir, prop_id, agnes_client=fv))
    cr = r["candidate_reviews"][0]
    assert cr["overall"] == "needs_fix", "介质错配 → needs_fix（确定性推导，不信模型自报）"
    assert has_prop_pass_evidence(pdir, prop_id, asset_id) is False
    # 无 pass 证据 → approve 被拒（双向严格分开）
    from src.resources.models import prop_revision
    rr = run(ws.approve_prop_reference(
        prop_id, asset_id,
        expected_revision=prop_revision(ResourceStore_reload(pdir, ws, prop_id))))
    assert rr["ok"] is False and rr["error"] == "review_not_passed"


def ResourceStore_reload(pdir, ws, prop_id):
    return ws.store.load().props[prop_id]
