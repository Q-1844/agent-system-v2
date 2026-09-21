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
