# tests/test_resources_phase1c3.py
# Production Resources V1 — Phase 1C3：Shared Production Library 最小闭环验收。
#
# 核心：copy-on-publish / copy-on-import 隔离（A 改不污染 B 与 Shared）、
# version/origin 记录、look 依赖 clothing 先发布、import 捆绑与查重、同名角色冲突。
# shared canonical 全部用 tmp shared_dir（绝不触碰仓库真实 shared_library/ 与 saved_characters/）。
import asyncio
import hashlib
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.models import Project, Character, Asset, ShotStatus
from src.core.write_lock import _clear_for_test
from src.resources.models import document_revision, clothing_revision
from src.resources.write_service import ResourceWriteService
from src.resources.shared_library import (
    SharedLibraryService, SharedStore, SharedDocument,
)

from tests.test_resources_phase1c1 import FakeAgnes, run, make_project, make_view_files


@pytest.fixture(autouse=True)
def _isolated_locks():
    _clear_for_test()
    yield
    _clear_for_test()


async def _seed_project_a(td: Path):
    """项目 A：Alice（front/side 真实文件）+ Black Coat(+ref) + Look(+1 候选)。"""
    pa = td / "pa"
    (pa / "o").mkdir(parents=True)
    (pa / "o" / "alice_front_seg.png").write_bytes(b"ALICE-FRONT")
    (pa / "o" / "alice_side_seg.png").write_bytes(b"ALICE-SIDE")
    (pa / "o" / "coat.png").write_bytes(b"COAT-IMG")
    proj = Project(name="pa")
    c = Character(name="Alice", description_en="a girl with silver hair")
    c.views = {"front": Asset(local=str((pa / "o" / "alice_front_seg.png").resolve()), status=ShotStatus.DONE.value),
               "side": Asset(local=str((pa / "o" / "alice_side_seg.png").resolve()), status=ShotStatus.DONE.value)}
    proj.characters = [c]
    ProjectStore(pa).save(proj)
    # P1：生成路径 profile-aware——测试环境默认写实
    from src.visual_profile import VisualProfileService
    VisualProfileService(pa, "pa").update(render_family="photorealistic",
                                          expected_revision="")
    ws = ResourceWriteService(pa, "pa")
    r = await ws.create_clothing(name="Black Coat", description="long black wool coat",
                                 expected_document_revision=document_revision(ws.store.load()))
    cl = r["target"]
    r2 = await ws.register_reference_asset(local="o/coat.png",
                                           expected_document_revision=r["document_revision_after"])
    cur = ws.store.load().clothings[cl]
    r3 = await ws.attach_clothing_reference(cl, r2["target"], expected_revision=clothing_revision(cur))
    r4 = await ws.create_look(character_name="Alice", label="Alice · Black Coat", clothing_id=cl,
                              expected_document_revision=r3["document_revision_after"])
    look_id = r4["target"]
    gen = await ws.generate_look_candidates(
        look_id, expected_revision=r4["revision"], candidate_count=1,
        agnes_client=FakeAgnes(images=[b"LOOK-CANDIDATE"]))
    assert gen["ok"], gen
    return pa, ws, cl, look_id


def _new_project(td: Path, name: str):
    p = td / name
    (p / "output").mkdir(parents=True, exist_ok=True)
    ProjectStore(p).save(Project(name=name))
    # P1：profile-aware 生成测试环境默认写实
    from src.visual_profile import VisualProfileService
    VisualProfileService(p, name).update(render_family="photorealistic",
                                         expected_revision="")
    ws = ResourceWriteService(p, name)
    svc = SharedLibraryService(p, name, shared_dir=td / "shared_library")
    return p, ws, svc


# =====================================================================
# Publish
# =====================================================================
async def _published_clothing(td):
    pa, ws, cl, look_id = await _seed_project_a(td)
    svc = SharedLibraryService(pa, "pa", shared_dir=td / "shared_library")
    r = await svc.publish(cl)
    assert r["ok"], r
    return pa, ws, svc, cl, r


def test_t1_publish_clothing_copies_media_with_checksum(tmp_path):
    pa, ws, svc, cl, r = run(_published_clothing(tmp_path))
    assert r["kind"] == "clothing" and r["version"] == 1
    store = SharedStore(tmp_path / "shared_library")
    sdoc = store.load()
    entry = sdoc.clothings[r["shared_id"]]
    assert entry.scope == "shared" and entry.name == "Black Coat"
    assert len(entry.reference_asset_ids) == 1
    sra = sdoc.reference_assets[entry.reference_asset_ids[0]]
    assert sra.source == "published" and sra.scope == "shared"
    media = tmp_path / sra.local
    assert media.is_file() and media.read_bytes() == b"COAT-IMG"
    assert sra.checksum == hashlib.sha256(b"COAT-IMG").hexdigest()
    # 项目 canonical 零改动（publish 只读项目）——clothing 无 origin/无 shared 指针写入
    pdoc = ws.store.load()
    assert pdoc.clothings[cl].origin is None


def test_t2_republish_bumps_version_same_id(tmp_path):
    pa, ws, svc, cl, r1 = run(_published_clothing(tmp_path))
    r2 = run(svc.publish(cl))
    assert r2["ok"] and r2["shared_id"] == r1["shared_id"] and r2["version"] == 2
    meta = SharedStore(tmp_path / "shared_library").load().meta[r1["shared_id"]]
    assert meta.version == 2 and len(meta.history) == 2
    assert meta.origin_project == "pa" and meta.original_id == cl


def test_t3_publish_look_requires_clothing_published(tmp_path):
    pa, ws, cl, look_id = run(_seed_project_a(tmp_path))
    svc = SharedLibraryService(pa, "pa", shared_dir=tmp_path / "shared_library")
    r = run(svc.publish(look_id))
    assert r["ok"] is False and r["error"] == "clothing_not_published"
    # 发布 clothing 后 look 可发布，且 FK 接到共享 clothing、候选媒体进库
    run(svc.publish(cl))
    r2 = run(svc.publish(look_id))
    assert r2["ok"] and r2["kind"] == "look" and len(r2["shared_resource_ids"]) == 1
    sdoc = SharedStore(tmp_path / "shared_library").load()
    sl = sdoc.looks[r2["shared_id"]]
    assert sl.clothing_id is not None and sl.scope == "shared"
    media = tmp_path / sdoc.reference_assets[sl.reference_asset_ids[0]].local
    assert media.read_bytes() == b"LOOK-CANDIDATE"
    m = sdoc.meta[r2["shared_id"]]
    assert m.character_snapshot == {"name": "Alice", "description_en": "a girl with silver hair"}


def test_t4_publish_character_views(tmp_path):
    pa, ws, cl, look_id = run(_seed_project_a(tmp_path))
    svc = SharedLibraryService(pa, "pa", shared_dir=tmp_path / "shared_library")
    r = run(svc.publish_character("Alice"))
    assert r["ok"] and sorted(r["views"].keys()) == ["front", "side"]
    rd = SharedStore(tmp_path / "shared_library").read_shared(r["shared_id"])
    assert rd["kind"] == "character" and rd["name"] == "Alice"
    assert rd["view_assets"]["front"]["checksum"] == hashlib.sha256(b"ALICE-FRONT").hexdigest()
    # 无视图角色 → 拒绝
    proj = ProjectStore(pa).load()
    proj.characters.append(Character(name="Bare", description_en="x"))
    ProjectStore(pa).save(proj)
    r2 = run(svc.publish_character("Bare"))
    assert r2["ok"] is False and r2["error"] == "missing_character_reference"
    r3 = run(svc.publish_character("Nobody"))
    assert r3["ok"] is False and r3["error"] == "character_not_found"


# =====================================================================
# Import
# =====================================================================
def _seed_shared(td):
    async def f():
        pa, ws, cl, look_id = await _seed_project_a(td)
        svc_a = SharedLibraryService(pa, "pa", shared_dir=td / "shared_library")
        rc = await svc_a.publish(cl)
        rl = await svc_a.publish(look_id)
        rch = await svc_a.publish_character("Alice")
        return rc, rl, rch
    return run(f())


def test_t5_import_clothing_copy_on_import_with_origin(tmp_path):
    rc, rl, rch = _seed_shared(tmp_path)
    pb, ws_b, svc_b = _new_project(tmp_path, "pb")
    i = run(svc_b.import_resource(rc["shared_id"],
                                  expected_document_revision=document_revision(ws_b.store.load())))
    assert i["ok"] and i["imported_id"].startswith("clothing_")
    doc = ws_b.store.load()
    c = doc.clothings[i["imported_id"]]
    assert c.name == "Black Coat" and c.scope == "project"
    assert c.origin.source == "shared_library"
    assert c.origin.shared_resource_id == rc["shared_id"]
    assert c.origin.imported_at   # ISO 时间记录
    # 媒体复制进项目、checksum 一致、id 是新分配的
    sdoc = SharedStore(tmp_path / "shared_library").load()
    shared_ref = sdoc.clothings[rc["shared_id"]].reference_asset_ids[0]
    pra = doc.reference_assets[c.reference_asset_ids[0]]
    assert pra.asset_id != shared_ref                       # 新 id（copy-on-import）
    assert pra.origin.shared_resource_id == shared_ref
    assert (tmp_path / "pb" / pra.local).read_bytes() == b"COAT-IMG"
    assert pra.checksum == hashlib.sha256(b"COAT-IMG").hexdigest()
    # shared 与项目 A 零改动
    assert SharedStore(tmp_path / "shared_library").load().clothings[rc["shared_id"]].name == "Black Coat"


def test_t6_import_look_bundles_and_dedupes_clothing(tmp_path):
    rc, rl, rch = _seed_shared(tmp_path)
    pb, ws_b, svc_b = _new_project(tmp_path, "pb")
    # 先单独导入 clothing + character，再导入 look → 捆绑查重：FK 直连已有副本，不产生第二份
    i1 = run(svc_b.import_resource(rc["shared_id"],
                                   expected_document_revision=document_revision(ws_b.store.load())))
    ich = run(svc_b.import_resource(rch["shared_id"],
                                    expected_document_revision=document_revision(ws_b.store.load())))
    assert ich["ok"]
    i2 = run(svc_b.import_resource(rl["shared_id"],
                                   expected_document_revision=document_revision(ws_b.store.load())))
    assert i2["ok"], i2
    doc = ws_b.store.load()
    lk = doc.looks[i2["imported_id"]]
    assert lk.clothing_id == i1["imported_id"]
    clothings = [c for c in doc.clothings.values() if c.name == "Black Coat"]
    assert len(clothings) == 1                              # 无重复导入
    assert lk.origin.shared_resource_id == rl["shared_id"]
    # 项目缺少同名角色 → 拒绝（导入前校验）
    pc, ws_c, svc_c = _new_project(tmp_path, "pc")
    i3 = run(svc_c.import_resource(rl["shared_id"],
                                   expected_document_revision=document_revision(ws_c.store.load())))
    assert i3["ok"] is False and i3["error"] == "character_not_found"


def test_t7_import_character_views_and_conflict(tmp_path):
    rc, rl, rch = _seed_shared(tmp_path)
    pb, ws_b, svc_b = _new_project(tmp_path, "pb")
    i = run(svc_b.import_resource(rch["shared_id"],
                                  expected_document_revision=document_revision(ws_b.store.load())))
    assert i["ok"] and i["imported_id"] == "Alice"
    # 视图文件复制进项目 legacy 通道（ProjectStore 正常写入，非手改）
    c = ProjectStore(tmp_path / "pb").load().get_character("Alice")
    assert c is not None and set(c.views.keys()) == {"front", "side"}
    assert (tmp_path / "pb" / "output" / "characters" / "Alice_front_seg.png").read_bytes() == b"ALICE-FRONT"
    # 同名冲突拒绝
    i2 = run(svc_b.import_resource(rch["shared_id"],
                                   expected_document_revision=document_revision(ws_b.store.load())))
    assert i2["ok"] is False and i2["error"] == "character_exists"
    # look 现在可导入（角色已就位）
    i3 = run(svc_b.import_resource(rl["shared_id"],
                                   expected_document_revision=document_revision(ws_b.store.load())))
    assert i3["ok"]


def test_t8_isolation_a_b_shared(tmp_path):
    rc, rl, rch = _seed_shared(tmp_path)
    shared_file = tmp_path / "shared_library" / "resources.json"
    # B、C 各自导入 clothing
    pb, ws_b, svc_b = _new_project(tmp_path, "pb")
    ib = run(svc_b.import_resource(rc["shared_id"],
                                   expected_document_revision=document_revision(ws_b.store.load())))
    pc, ws_c, svc_c = _new_project(tmp_path, "pc")
    ic = run(svc_c.import_resource(rc["shared_id"],
                                   expected_document_revision=document_revision(ws_c.store.load())))
    pa, ws_a = tmp_path / "pa", ResourceWriteService(tmp_path / "pa", "pa")
    snapshot = shared_file.read_bytes()
    # A 改原版
    orig_id = _orig_cl(ws_a)
    ra = run(ws_a.update_clothing(orig_id, {"name": "A-Modified"},
                                  expected_revision=_cl_rev(ws_a, orig_id)))
    assert ra["ok"], ra
    # B 改副本
    rb = run(ws_b.update_clothing(ib["imported_id"], {"name": "B-Modified"},
                                  expected_revision=_cl_rev(ws_b, ib["imported_id"])))
    assert rb["ok"]
    # shared 字节不变；C 副本仍原名；A/B 各自生效
    assert shared_file.read_bytes() == snapshot
    assert ws_c.store.load().clothings[ic["imported_id"]].name == "Black Coat"
    assert ws_a.store.load().clothings[orig_id].name == "A-Modified"
    assert ws_b.store.load().clothings[ib["imported_id"]].name == "B-Modified"


def _orig_cl(ws):
    """项目里 origin 为空的那个原版 clothing（导入副本都带 origin）。"""
    for c in ws.store.load().clothings.values():
        if c.origin is None:
            return c.clothing_id
    raise AssertionError("no original clothing")


def _cl_rev(ws, cl_id):
    return clothing_revision(ws.store.load().clothings[cl_id])


def test_t9_import_occ_stale(tmp_path):
    rc, rl, rch = _seed_shared(tmp_path)
    pb, ws_b, svc_b = _new_project(tmp_path, "pb")
    i = run(svc_b.import_resource(rc["shared_id"], expected_document_revision="00000000"))
    assert i["ok"] is False and i["error"] == "stale_revision"
    i2 = run(svc_b.import_resource("clothing_none", expected_document_revision=document_revision(ws_b.store.load())))
    assert i2["ok"] is False and i2["error"] == "not_found"


# =====================================================================
# HTTP 契约（patch default_shared_dir 到 tmp，绝不写真实 shared_library/）
# =====================================================================
def test_http_publish_list_import(tmp_path, monkeypatch):
    import src.resources.shared_library as sl_mod
    import routers.resources as rres
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.utils.project_manager import ProjectManager

    pdir = make_project(tmp_path, with_character=False)
    shared_dir = tmp_path / "shared_library"
    monkeypatch.setattr(sl_mod, "default_shared_dir", lambda: shared_dir, raising=True)
    orig = rres.get_project_manager
    rres.get_project_manager = lambda: ProjectManager(base_dir=str(tmp_path))
    app = FastAPI()
    app.include_router(rres.router)
    try:
        c = TestClient(app)
        name = pdir.name
        # 建 clothing + ref
        rev = c.get(f"/api/project/{name}/resources").json()["document_revision"]
        r = c.post(f"/api/project/{name}/clothings", json={
            "name": "Shared Coat", "expected_document_revision": rev})
        cl_id, cl_rev = r.json()["target"], r.json()["revision"]
        (pdir / "o").mkdir(exist_ok=True)
        (pdir / "o" / "coat.png").write_bytes(b"HTTP-COAT")
        r2 = c.post(f"/api/project/{name}/reference-assets", json={
            "local": "o/coat.png", "expected_document_revision":
                c.get(f"/api/project/{name}/resources").json()["document_revision"]})
        cur = c.get(f"/api/project/{name}/clothings/{cl_id}").json()["revision"]
        r3 = c.post(f"/api/project/{name}/clothings/{cl_id}/references",
                    json={"asset_id": r2.json()["target"], "expected_revision": cur})
        assert r3.status_code == 200
        # publish
        p = c.post(f"/api/project/{name}/resources/publish", json={"resource_id": cl_id})
        assert p.status_code == 200 and p.json()["version"] == 1, p.text
        shared_id = p.json()["shared_id"]
        # shared list/read（全局命名空间）
        lst = c.get("/api/shared/resources").json()["resources"]
        assert [x["type"] for x in lst] == ["clothing"] and lst[0]["id"] == shared_id
        rd = c.get(f"/api/shared/resources/{shared_id}").json()
        assert rd["name"] == "Shared Coat" and rd["version"] == 1
        assert c.get("/api/shared/resources/shared_none").status_code == 404
        # import 回另一个项目
        (tmp_path / "p2" / "output").mkdir(parents=True)
        from src.core.store import ProjectStore
        from src.core.models import Project
        ProjectStore(tmp_path / "p2").save(Project(name="p2"))
        rev2 = c.get("/api/project/p2/resources").json()["document_revision"]
        im = c.post("/api/project/p2/resources/import", json={
            "shared_resource_id": shared_id, "expected_document_revision": rev2})
        assert im.status_code == 200 and im.json()["ok"], im.text
        body = c.get(f"/api/project/p2/clothings/{im.json()['imported_id']}").json()
        assert body["name"] == "Shared Coat" and body["origin"]["shared_resource_id"] == shared_id
        # OCC
        bad = c.post("/api/project/p2/resources/import", json={
            "shared_resource_id": shared_id, "expected_document_revision": "00000000"})
        assert bad.status_code == 409 and bad.json()["detail"]["error"] == "stale_revision"
    finally:
        rres.get_project_manager = orig
