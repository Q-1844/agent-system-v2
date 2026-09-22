# tests/test_resources_look_attach_34.py
# 34.md §5/§9 — attach_look_reference 最小修复验收。
#
# 背景：规格 §9 指定路径「正式注册校准资产 → 经 provenance 合同进入项目 Look」
# 的正式链 = register_reference_asset（REST 已有）→ attach 进 Look 候选集（【缺口，
# generate 是唯一追加途径】）→ review（pass 证据）→ approve。本轮补齐 attach，
# 镜像 attach_clothing_reference 全部语义（OCC / 幂等 / asset 校验 / 写后重读验证）。
# attach 不产生 approved 状态——批准仍走 /approve 的 pass 证据门槛（诚实审批链）。
import asyncio
from pathlib import Path

import pytest

from src.core.write_lock import _clear_for_test
from src.resources.models import document_revision
from src.resources.store import ResourceStore
from src.resources.write_service import ResourceWriteService


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False \
        else asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolated_locks():
    _clear_for_test()
    yield
    _clear_for_test()


@pytest.fixture
def env(tmp_path):
    from tests.test_resources_phase1c1 import make_project
    pdir = make_project(tmp_path, with_character=True)
    ws = ResourceWriteService(pdir, pdir.name)
    return {"pdir": pdir, "ws": ws,
            "doc_rev": document_revision(ws.store.load())}


def test_attach_look_reference_happy_path_and_idempotent(env):
    ws = env["ws"]
    r = run(ws.register_reference_asset(
        url="https://cos-platform-outputs.agnes-ai.cn/images/i2i/task_x/output.png",
        source="calibration_33md_import", purpose="cinematic_identity_reference",
        expected_document_revision=env["doc_rev"]))
    assert r["ok"] and r["verified"]
    asset_id = r["target"]

    r2 = run(ws.create_look(character_name="Alice",
                            appearance_description="quadruped red fox",
                            expected_document_revision=r["document_revision_after"]))
    assert r2["ok"]
    look_id = r2["target"]

    r3 = run(ws.attach_look_reference(look_id, asset_id,
                                      expected_revision=r2["revision"]))
    assert r3["ok"] and r3["verified"]
    assert r3["after"]["reference_asset_ids"] == [asset_id]
    # attach 绝不触碰审批状态（诚实审批链：approve 才产生 approved）
    assert r3["after"]["approved_reference_asset_ids"] == []
    assert r3["after"]["approval_status"] == "unreviewed"

    # 幂等重复 attach
    r4 = run(ws.attach_look_reference(look_id, asset_id,
                                      expected_revision=r3["revision"]))
    assert r4["ok"] and r4.get("unchanged") is True

    # 落盘可读回（truth gate）
    back = ResourceStore(env["pdir"], env["pdir"].name).load().looks[look_id]
    assert asset_id in back.reference_asset_ids


def test_attach_look_reference_rejections(env):
    ws = env["ws"]
    r = run(ws.register_reference_asset(
        url="https://x/a.png", expected_document_revision=env["doc_rev"]))
    asset_id = r["target"]
    r2 = run(ws.create_look(character_name="Alice",
                            appearance_description="d",
                            expected_document_revision=r["document_revision_after"]))
    look_id, rev = r2["target"], r2["revision"]

    # 不存在的 asset → 拒绝
    r3 = run(ws.attach_look_reference(look_id, "ref_none", expected_revision=rev))
    assert r3["ok"] is False and r3["error"] == "asset_not_found"

    # 不存在的 look → 拒绝
    r4 = run(ws.attach_look_reference("look_none", asset_id, expected_revision=rev))
    assert r4["ok"] is False and r4["error"] == "not_found"

    # stale revision → 拒绝
    r5 = run(ws.attach_look_reference(look_id, asset_id, expected_revision="deadbeef"))
    assert r5["ok"] is False and r5["error"] == "stale_revision"

    # 拒绝后文件不变（candidate 集仍为空）
    back = ResourceStore(env["pdir"], env["pdir"].name).load().looks[look_id]
    assert back.reference_asset_ids == []


def test_rehost_reference_asset(tmp_path, monkeypatch):
    """34.md §5：local-only 资产经标准 ImageHost 补公网 URL（视频消费前置）；
    幂等（已有 url → unchanged）；OCC；无 local → 结构化拒绝。"""
    from tests.test_resources_phase1c1 import make_project
    _clear_for_test()
    pdir = make_project(tmp_path, with_character=False)
    ws = ResourceWriteService(pdir, pdir.name)
    rev = document_revision(ws.store.load())

    # local 文件真实存在（register 校验 local 必须在项目内）
    img = pdir / "o" / "scene.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"PNG")

    r = run(ws.register_reference_asset(local="o/scene.png", source="scene_system",
                                        expected_document_revision=rev))
    assert r["ok"] and r["target"]
    aid = r["target"]
    rev2 = r["document_revision_after"]
    assert ResourceStore(pdir, pdir.name).load().reference_assets[aid].url is None

    class _FakeHost:
        async def upload(self, path):
            assert str(path).endswith("scene.png")
            return "https://i.ibb.co/fake/scene.png"

    import deps
    monkeypatch.setattr(deps, "get_rehost_host", lambda: _FakeHost())

    r2 = run(ws.rehost_reference_asset(aid, expected_document_revision=rev2))
    assert r2["ok"] and r2["verified"]
    assert r2["after"]["url"] == "https://i.ibb.co/fake/scene.png"

    # 幂等：已有 url → unchanged
    r3 = run(ws.rehost_reference_asset(
        aid, expected_document_revision=r2["document_revision_after"]))
    assert r3["ok"] and r3.get("unchanged") is True

    # stale OCC → 拒绝
    r4 = run(ws.rehost_reference_asset(aid, expected_document_revision=rev))
    assert r4["ok"] is False and r4["error"] == "stale_revision"
    _clear_for_test()


def test_derive_look_reference(tmp_path, monkeypatch):
    """34.md Human Gate：从 approved 参考派生姿态/视角变体——基底资格校验
    （只许 approved）、候选登记进 look（不自动批准）、OCC、诚实 record。"""
    from tests.test_resources_phase1c1 import make_project
    _clear_for_test()
    pdir = make_project(tmp_path, with_character=True)
    ws = ResourceWriteService(pdir, pdir.name)
    rev = document_revision(ws.store.load())
    r = run(ws.register_reference_asset(url="https://cos.example/r3.png",
                                        expected_document_revision=rev))
    r2 = run(ws.create_look(character_name="Alice",
                            appearance_description="quadruped red fox",
                            expected_document_revision=r["document_revision_after"]))
    look_id = r2["target"]
    # base 不在 approved → 拒绝（派生只从已批准身份出发）
    r3 = run(ws.derive_look_reference(look_id, base_asset_id=r["target"],
                                      prompt="walking side profile",
                                      expected_revision=r2["revision"]))
    assert r3["ok"] is False and r3["error"] == "base_not_approved"

    # 把资产放入 approved 后再派生（直接构造 approved 状态——真实链走 review+approve）
    doc = ws.store.load()
    doc.looks[look_id].approved_reference_asset_ids.append(r["target"])
    ws.store.save(doc)

    class _FakeClient:
        models = {"image": "agnes-image-test"}
        async def generate_image(self, *, prompt, image_refs=None, size_tier="1K",
                                 ratio="16:9", seed=None, **kw):
            assert image_refs and image_refs[0].startswith("data:") or image_refs[0].startswith("http")
            assert "walking side profile" in prompt
            return {"url": "https://cos.example/derived.png",
                    "b64": __import__("base64").b64encode(b"PNGDATA").decode()}
    import deps as _deps
    monkeypatch.setattr(_deps, "get_agnes_client", lambda: _FakeClient())

    lk_now = ws.store.load().looks[look_id]
    from src.resources.models import look_revision
    r5 = run(ws.derive_look_reference(look_id, base_asset_id=r["target"],
                                      prompt="walking side profile",
                                      expected_revision=look_revision(lk_now)))
    assert r5["ok"], r5
    new_aid = r5["target"]
    back = ResourceStore(pdir, pdir.name).load()
    assert new_aid in back.reference_assets
    assert new_aid in back.looks[look_id].reference_asset_ids
    # 候选不自动批准（审批门槛不变）
    assert new_aid not in back.looks[look_id].approved_reference_asset_ids
    # 空 prompt → 拒绝
    r6 = run(ws.derive_look_reference(look_id, base_asset_id=r["target"],
                                      prompt="  ",
                                      expected_revision=r5["revision"]))
    assert r6["ok"] is False and r6["error"] == "validation_error"
    _clear_for_test()
