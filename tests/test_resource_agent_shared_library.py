"""Agent System V2 Phase B — Commit F：Shared Library Agent 工具测试（26.md §9/§21 R39-R43）。

全部 deterministic：共享库目录 monkeypatch 到 tmp（跨测试隔离）；copy-on-publish /
copy-on-import 语义走真实 SharedLibraryService（canonical 真实落盘）。
"""
import asyncio
import base64
import json

import pytest

from src.core.models import Project, Character, Asset, ShotStatus
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.agents.runtime import AgentRuntime
from src.agents.character_designer import (
    _execute_resource_library_publish, _execute_resource_library_import,
    _execute_resource_clothing_upsert, _execute_resource_look_prepare,
)
from src.resources.service import ResourceService
from src.resources.models import document_revision
from src.visual_profile import VisualProfileService

import src.resources.shared_library as sl


def _patch_shared(tmp_path, monkeypatch):
    """共享库目录隔离到 tmp（返回路径供断言）。"""
    shared = tmp_path / "_shared_lib"
    monkeypatch.setattr(sl, "default_shared_dir", lambda: shared)
    return shared


class _FakeGen:
    """只实现生成契约面的 fake（审查不发生——发布/导入不需要 vision）。"""

    def __init__(self):
        self.models = {"image": "fake-image"}

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="2:3", seed=None):
        return {"url": None, "b64": base64.b64encode(b"PNG-FAKE").decode()}

    async def chat_completion_vision(self, messages, *, model, **kw):
        raise AssertionError("本测试不应触发审查")

    async def aclose(self):
        pass


def _env(tmp_path, name_suffix=""):
    _clear_for_test()
    name = tmp_path.name + name_suffix
    pdir = tmp_path / name
    proj = Project(name=name)
    c = Character(name="Alice", description_en="a girl with silver hair")
    c.views = {v: Asset(local=f"o/alice_{v}_seg.png", url=f"https://x/{v}",
                        status=ShotStatus.DONE.value)
               for v in ("front", "side", "back")}
    proj.characters = [c]
    ProjectStore(pdir).save(proj)
    for v in ("front", "side", "back"):
        p = pdir / "o" / f"alice_{v}_seg.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(f"PNG-{v}".encode())
    VisualProfileService(pdir, name).update(render_family="photorealistic",
                                            expected_revision="")
    return pdir


def _rt(pdir):
    return AgentRuntime(project_dir_resolver=lambda n: pdir,
                        settings_path=pdir.parent / "agents_data" / "settings.json",
                        client=None)


def _doc_rev(pdir):
    return document_revision(ResourceService(pdir, pdir.name).store.load())


def _approved_look(pdir, rt, monkeypatch):
    monkeypatch.setattr("deps.agnes_client", _FakeGen(), raising=False)
    import tests.test_resource_agent_mutations as M
    out = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "hero look",
            "appearance_description": "wearing a cape",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "approve_if_pass"}), None, None))
    # 审查需要 vision——借 mutations 的 PASS JSON 通道
    return out, M


# ============ R39：publish ============
def test_R39_publish_clothing(tmp_path, monkeypatch):
    shared = _patch_shared(tmp_path, monkeypatch)
    pdir = _env(tmp_path)
    rt = _rt(pdir)
    cr = asyncio.run(_execute_resource_clothing_upsert(
        rt, pdir.name, json.dumps({"name": "Red Coat",
                                   "expected_resources_revision": _doc_rev(pdir)}),
        None, None))
    out = asyncio.run(_execute_resource_library_publish(
        rt, pdir.name, json.dumps({"resource_kind": "clothing",
                                   "resource_id": cr["clothing_id"],
                                   "expected_resources_revision": _doc_rev(pdir)}),
        None, None))
    assert out["ok"] and out["published"], out
    assert out["shared_id"]
    assert out["changed_resources"] == [], "发布零改动项目 canonical"
    sdoc = sl.SharedStore(shared).load()
    assert cr["clothing_id"] in [m.original_id for m in sdoc.meta.values()]
    # 幂等 republish：版本+1、同 shared id
    out2 = asyncio.run(_execute_resource_library_publish(
        rt, pdir.name, json.dumps({"resource_kind": "clothing",
                                   "resource_id": cr["clothing_id"],
                                   "expected_resources_revision": _doc_rev(pdir)}),
        None, None))
    assert out2["ok"] and out2["shared_id"] == out["shared_id"]
    assert out2["version"] == out["version"] + 1


def test_R39b_publish_stale_source_guard(tmp_path, monkeypatch):
    shared = _patch_shared(tmp_path, monkeypatch)
    pdir = _env(tmp_path)
    rt = _rt(pdir)
    cr = asyncio.run(_execute_resource_clothing_upsert(
        rt, pdir.name, json.dumps({"name": "Red Coat",
                                   "expected_resources_revision": _doc_rev(pdir)}),
        None, None))
    out = asyncio.run(_execute_resource_library_publish(
        rt, pdir.name, json.dumps({"resource_kind": "clothing",
                                   "resource_id": cr["clothing_id"],
                                   "expected_resources_revision": "old00000"}),
        None, None))
    assert out["ok"] is False and out["stale"] is True and out["error"] == "stale_revision"
    sdoc = sl.SharedStore(shared).load()
    assert not sdoc.meta, "源状态过期不发布"


# ============ R40-R41：import + 依赖 id remap ============
def test_R40_R41_import_look_bundles_clothing_and_remaps(tmp_path, monkeypatch):
    shared = _patch_shared(tmp_path, monkeypatch)
    pdir = _env(tmp_path)
    rt = _rt(pdir)
    # 建 clothing + approved look（依赖该 clothing）并发布两者
    cr = asyncio.run(_execute_resource_clothing_upsert(
        rt, pdir.name, json.dumps({"name": "Cape",
                                   "expected_resources_revision": _doc_rev(pdir)}),
        None, None))
    import tests.test_resource_agent_mutations as M
    monkeypatch.setattr("deps.agnes_client", M.FakeProvider(M.LOOK_PASS_JSON), raising=False)
    look = asyncio.run(_execute_resource_look_prepare(
        rt, pdir.name, json.dumps({
            "character_name": "Alice", "label": "caped",
            "clothing_id": cr["clothing_id"],
            "appearance_description": "wearing a cape",
            "expected_resources_revision": _doc_rev(pdir),
            "approval_mode": "approve_if_pass"}), None, None))
    assert look["approval"]["status"] == "approved"
    pub_c = asyncio.run(_execute_resource_library_publish(
        rt, pdir.name, json.dumps({"resource_kind": "clothing",
                                   "resource_id": cr["clothing_id"],
                                   "expected_resources_revision": _doc_rev(pdir)}),
        None, None))
    pub_l = asyncio.run(_execute_resource_library_publish(
        rt, pdir.name, json.dumps({"resource_kind": "look",
                                   "resource_id": look["look_id"],
                                   "expected_resources_revision": _doc_rev(pdir)}),
        None, None))
    assert pub_c["ok"] and pub_l["ok"]
    # 新项目导入该 look（项目内已有同名 Alice）
    pdir2 = _env(tmp_path, name_suffix="_b")
    rt2 = _rt(pdir2)
    imp = asyncio.run(_execute_resource_library_import(
        rt2, pdir2.name, json.dumps({"shared_resource_id": pub_l["shared_id"],
                                     "expected_resources_revision": _doc_rev(pdir2)}),
        None, None))
    assert imp["ok"], imp
    kinds = {(c["kind"], c["id"]) for c in imp["changed_resources"]}
    # R41：导入的 look 指向【新导入的】clothing 副本（id remap，不指向共享 id）
    look_new = imp["imported_id"]
    clothing_new = next(cid for k, cid in kinds if k == "clothing")
    assert any(k == "look" and cid == look_new for k, cid in kinds)
    doc2 = ResourceService(pdir2, pdir2.name).store.load()
    assert doc2.looks[look_new].clothing_id == clothing_new
    assert clothing_new != pub_c["shared_id"], "clothing 新 id ≠ 共享 id（copy-on-import）"
    # origin 记录来源
    assert doc2.clothings[clothing_new].origin is not None
    assert doc2.clothings[clothing_new].origin.shared_resource_id == pub_c["shared_id"]


# ============ R42：跨项目隔离 ============
def test_R42_cross_project_isolation(tmp_path, monkeypatch):
    shared = _patch_shared(tmp_path, monkeypatch)
    pdir = _env(tmp_path)
    rt = _rt(pdir)
    cr = asyncio.run(_execute_resource_clothing_upsert(
        rt, pdir.name, json.dumps({"name": "Red Coat",
                                   "expected_resources_revision": _doc_rev(pdir)}),
        None, None))
    pub = asyncio.run(_execute_resource_library_publish(
        rt, pdir.name, json.dumps({"resource_kind": "clothing",
                                   "resource_id": cr["clothing_id"],
                                   "expected_resources_revision": _doc_rev(pdir)}),
        None, None))
    # 项目 B 导入
    pdir2 = _env(tmp_path, name_suffix="_b")
    rt2 = _rt(pdir2)
    imp = asyncio.run(_execute_resource_library_import(
        rt2, pdir2.name, json.dumps({"shared_resource_id": pub["shared_id"],
                                     "expected_resources_revision": _doc_rev(pdir2)}),
        None, None))
    assert imp["ok"]
    # 源项目与共享库均不受导入影响；两项目 snapshot 互不污染
    doc1 = ResourceService(pdir, pdir.name).store.load()
    doc2 = ResourceService(pdir2, pdir2.name).store.load()
    assert imp["imported_id"] not in doc1.clothings
    assert doc2.clothings[imp["imported_id"]].name == "Red Coat"
    sdoc = sl.SharedStore(shared).load()
    assert pub["shared_id"] in sdoc.clothings


# ============ R43：同名冲突 fail closed ============
def test_R43_import_character_name_conflict(tmp_path, monkeypatch):
    shared = _patch_shared(tmp_path, monkeypatch)
    pdir = _env(tmp_path)
    rt = _rt(pdir)
    pub = asyncio.run(_execute_resource_library_publish(
        rt, pdir.name, json.dumps({"resource_kind": "character",
                                   "resource_id": "Alice",
                                   "expected_resources_revision": _doc_rev(pdir)}),
        None, None))
    assert pub["ok"], pub
    # 项目 B 已有同名 Alice → character_exists fail closed
    pdir2 = _env(tmp_path, name_suffix="_b")
    rt2 = _rt(pdir2)
    imp = asyncio.run(_execute_resource_library_import(
        rt2, pdir2.name, json.dumps({"shared_resource_id": pub["shared_id"],
                                     "expected_resources_revision": _doc_rev(pdir2)}),
        None, None))
    assert imp["ok"] is False and imp["error"] == "character_exists"
    assert imp["changed_resources"] == []
    assert [c.name for c in ProjectStore(pdir2).load().characters] == ["Alice"], "无半途状态"


def test_R_import_stale(tmp_path, monkeypatch):
    shared = _patch_shared(tmp_path, monkeypatch)
    pdir = _env(tmp_path)
    rt = _rt(pdir)
    pub = asyncio.run(_execute_resource_library_publish(
        rt, pdir.name, json.dumps({"resource_kind": "character",
                                   "resource_id": "Alice",
                                   "expected_resources_revision": _doc_rev(pdir)}),
        None, None))
    pdir2 = _env(tmp_path, name_suffix="_b")
    rt2 = _rt(pdir2)
    imp = asyncio.run(_execute_resource_library_import(
        rt2, pdir2.name, json.dumps({"shared_resource_id": pub["shared_id"],
                                     "expected_resources_revision": "deadbeef"}),
        None, None))
    assert imp["ok"] is False and imp["stale"] is True


