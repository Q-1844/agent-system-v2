# tests/test_resources_phase1c4.py
# Production Resources V1 — Phase 1C4：Prop CRUD（create/update/delete/reference）。
# Prop 当前不被其它资源引用（Look 无 prop FK）→ delete 无 resource_in_use；
# 未来加 prop 引用时必须补 reference sweep（write_service docstring 已注明）。
import pytest

from src.core.write_lock import _clear_for_test
from src.resources.models import document_revision, prop_revision
from src.resources.write_service import ResourceWriteService
from src.resources.store import ResourceStore

from tests.test_resources_phase1c1 import run, make_project, make_view_files, wclient


@pytest.fixture(autouse=True)
def _isolated_locks():
    _clear_for_test()
    yield
    _clear_for_test()


@pytest.fixture
def env(tmp_path):
    pdir = make_project(tmp_path, with_character=False)
    ws = ResourceWriteService(pdir, pdir.name)
    return {"pdir": pdir, "ws": ws, "doc_rev": document_revision(ws.store.load())}


def test_prop_crud_full_cycle(env):
    ws = env["ws"]
    r = run(ws.create_prop(name="Sword", description="a broken longsword",
                           tags=["weapon"], expected_document_revision=env["doc_rev"]))
    assert r["ok"] and r["verified"] and r["after"]["approval_status"] == "unreviewed"
    prop_id = r["target"]

    # update：rename（prop_id 不变）+ 白名单
    r2 = run(ws.update_prop(prop_id, {"name": "Rusted Sword", "tags": ["weapon", "quest"]},
                            expected_revision=r["revision"]))
    assert r2["ok"] and r2["after"]["prop_id"] == prop_id and r2["after"]["name"] == "Rusted Sword"
    r3 = run(ws.update_prop(prop_id, {"prop_id": "hack"}, expected_revision=r2["revision"]))
    assert r3["ok"] is False and r3["error"] == "validation_error"
    r4 = run(ws.update_prop(prop_id, {"name": "X"}, expected_revision="00000000"))
    assert r4["ok"] is False and r4["error"] == "stale_revision"

    # reference attach/detach（幂等 + asset 校验）
    (env["pdir"] / "o").mkdir(exist_ok=True)
    (env["pdir"] / "o" / "sword.png").write_bytes(b"SWORD-IMG")
    r5 = run(ws.register_reference_asset(
        local="o/sword.png", view="flat",
        expected_document_revision=document_revision(ws.store.load())))
    ra = r5["target"]
    r6 = run(ws.attach_prop_reference(prop_id, ra, expected_revision=r5["revision"] and r2["revision"]))
    # 上面 expected_revision 拿错了（register 改的是文档不是 prop）→ 用当前 prop revision 重试
    if not r6["ok"]:
        cur = ws.store.load().props[prop_id]
        r6 = run(ws.attach_prop_reference(prop_id, ra, expected_revision=prop_revision(cur)))
    assert r6["ok"] and r6["after"]["reference_asset_ids"] == [ra], r6
    cur = ws.store.load().props[prop_id]
    r7 = run(ws.attach_prop_reference(prop_id, ra, expected_revision=prop_revision(cur)))
    assert r7["ok"] and r7.get("unchanged") is True
    r8 = run(ws.attach_prop_reference(prop_id, "ref_none", expected_revision=prop_revision(ws.store.load().props[prop_id])))
    assert r8["ok"] is False and r8["error"] == "asset_not_found"
    r9 = run(ws.detach_prop_reference(prop_id, ra, expected_revision=prop_revision(ws.store.load().props[prop_id])))
    assert r9["ok"] and r9["after"]["reference_asset_ids"] == []

    # delete（无 resource_in_use——当前无资源引用 Prop）
    r10 = run(ws.delete_prop(prop_id, expected_revision=prop_revision(ws.store.load().props[prop_id])))
    assert r10["ok"] and r10["verified"] and r10["after"] is None
    assert prop_id not in ResourceStore(env["pdir"], env["pdir"].name).load().props
    # 重复删除 → not_found
    r11 = run(ws.delete_prop(prop_id, expected_revision="whatever"))
    assert r11["ok"] is False and r11["error"] == "not_found"


def test_http_prop_endpoints(wclient):
    c, pdir, name = wclient
    rev = c.get(f"/api/project/{name}/resources").json()["document_revision"]
    r = c.post(f"/api/project/{name}/props", json={
        "name": "Lantern", "tags": ["light"], "expected_document_revision": rev})
    assert r.status_code == 200 and r.json()["ok"], r.text
    prop_id, prop_rev = r.json()["target"], r.json()["revision"]

    r2 = c.patch(f"/api/project/{name}/props/{prop_id}",
                 json={"description": "old street lantern", "expected_revision": prop_rev})
    assert r2.status_code == 200 and r2.json()["after"]["description"] == "old street lantern"

    r3 = c.delete(f"/api/project/{name}/props/{prop_id}", params={"expected_revision": r2.json()["revision"]})
    assert r3.status_code == 200 and r3.json()["ok"]

    # props list/read 端点反映生命周期
    r4 = c.post(f"/api/project/{name}/props", json={
        "name": "Second", "expected_document_revision":
            c.get(f"/api/project/{name}/resources").json()["document_revision"]})
    lst = c.get(f"/api/project/{name}/props").json()["props"]
    assert any(x["id"] == r4.json()["target"] for x in lst)
    rd = c.get(f"/api/project/{name}/props/{r4.json()['target']}").json()
    assert rd["name"] == "Second"
