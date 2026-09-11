# tests/test_resources_phase1c1.py
# Production Resources V1 — Phase 1C1 验收测试（对应执行文档 §29 T1-T25）。
#
# 覆盖：写安全（OCC/lock/receipt）/ Clothing CRUD / ReferenceAsset 注册（路径防穿越）/
#       Look create·update / generation basis / prompt deterministic / generation records /
#       三阶段生成协议 / stale-basis 保护 / failure semantics / project.json 零影响 / 重启恢复。
# 全部 tmp_path 隔离，不触碰真实 projects/。async service 用 asyncio.run 包（仓库惯例，无 pytest-asyncio）。
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.models import Project, Character, Asset, ShotStatus
from src.core.write_lock import _clear_for_test, project_write_lock
from src.resources.models import (
    document_revision, look_revision, clothing_revision, reference_asset_revision,
)
from src.resources.service import ResourceService
from src.resources.store import ResourceStore
from src.resources.write_service import ResourceWriteService


# ============ fixtures / helpers ============
def make_project(base: Path, with_character: bool = False) -> Path:
    """最小项目目录 + project.json（与 Phase 1B 测试同布局）。可选带三视图 Character。"""
    name = base.name
    pdir = base / name
    proj = Project(name=name)
    if with_character:
        c = Character(name="Alice", description_en="a girl with silver hair")
        c.views = {
            "front": Asset(local="o/alice_front_seg.png", url="https://x/front", status=ShotStatus.DONE.value),
            "side": Asset(local="o/alice_side_seg.png", url="https://x/side", status=ShotStatus.DONE.value),
            "back": Asset(local="o/alice_back_seg.png", url="https://x/back", status=ShotStatus.DONE.value),
        }
        proj.characters = [c]
    ProjectStore(pdir).save(proj)
    return pdir


def make_view_files(pdir: Path) -> dict:
    """在 o/ 下写三个真实小 PNG 字节文件（供 identity ref 选择 / checksum 验证）。"""
    files = {}
    for v in ("front", "side", "back"):
        p = pdir / "o" / f"alice_{v}_seg.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(f"PNG-{v}".encode())
        files[v] = p
    return files


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolated_locks():
    """每个用例独立锁字典（project_write_lock 是进程级全局）。"""
    _clear_for_test()
    yield
    _clear_for_test()


@pytest.fixture
def env(tmp_path):
    """带 Alice（三视图真实文件）的项目 + WriteService + 当前 document revision。"""
    pdir = make_project(tmp_path, with_character=True)
    make_view_files(pdir)
    ws = ResourceWriteService(pdir, pdir.name)
    return {"pdir": pdir, "ws": ws, "doc_rev": document_revision(ws.store.load())}


# =====================================================================
# Work A — 写安全 / OCC / Clothing CRUD / Registration / Look create
# =====================================================================

# ---- T1 create Clothing：document expected_revision 正确 → 成功 ----
def test_t1_create_clothing_with_correct_document_revision(env):
    ws = env["ws"]
    r = run(ws.create_clothing(name="Black Coat", description="long black wool coat",
                               materials=["wool"], colors=["#111111"],
                               expected_document_revision=env["doc_rev"]))
    assert r["ok"] is True and r["verified"] is True
    assert r["target"].startswith("clothing_")
    assert r["revision"] and len(r["revision"]) == 8
    assert r["after"]["approval_status"] == "unreviewed"
    # 落盘可读回（truth gate：磁盘态，非内存态）
    back = ResourceStore(env["pdir"], env["pdir"].name).load().clothings
    assert list(back.keys()) == [r["target"]]
    assert back[r["target"]].name == "Black Coat"


# ---- T2 create Clothing stale revision → 拒绝，文件不变 ----
def test_t2_create_clothing_stale_revision_rejected(env):
    ws = env["ws"]
    p = env["pdir"] / "output" / "resources.json"
    r1 = run(ws.create_clothing(name="First", expected_document_revision=env["doc_rev"]))
    assert r1["ok"]
    before = p.read_bytes()

    r2 = run(ws.create_clothing(name="Second", expected_document_revision=env["doc_rev"]))
    assert r2["ok"] is False and r2["error"] == "stale_revision"
    assert r2["expected_revision"] == env["doc_rev"]
    assert r2["current_revision"] == r1["document_revision_after"]
    assert r2["current_revision"] != r2["expected_revision"]
    assert p.read_bytes() == before   # 拒绝 → 文件字节不变
    assert "Second" not in p.read_text(encoding="utf-8")


# ---- T3 rename Clothing：id 不变、Look 关系不失效 ----
def test_t3_clothing_rename_id_unchanged(env):
    ws = env["ws"]
    r = run(ws.create_clothing(name="Black Coat", expected_document_revision=env["doc_rev"]))
    cl_id, cl_rev = r["target"], r["revision"]

    r2 = run(ws.update_clothing(cl_id, {"name": "Dark Coat"}, expected_revision=cl_rev))
    assert r2["ok"] and r2["verified"]
    assert r2["after"]["clothing_id"] == cl_id          # id 不变
    assert r2["after"]["name"] == "Dark Coat"           # 只改 name
    assert r2["revision"] != cl_rev                      # 内容变 → revision 变

    # 错 revision → stale，且携带当前态
    r3 = run(ws.update_clothing(cl_id, {"name": "X"}, expected_revision=cl_rev))
    assert r3["ok"] is False and r3["error"] == "stale_revision"
    assert r3["current"]["name"] == "Dark Coat"
    # 未知字段拒绝
    r4 = run(ws.update_clothing(cl_id, {"clothing_id": "hack"}, expected_revision=r2["revision"]))
    assert r4["ok"] is False and r4["error"] == "validation_error"


# ---- T4 delete 被 Look 引用的 Clothing → resource_in_use ----
def test_t4_delete_referenced_clothing_blocked(env):
    ws = env["ws"]
    r = run(ws.create_clothing(name="Black Coat", expected_document_revision=env["doc_rev"]))
    cl_id = r["target"]
    r2 = run(ws.create_look(character_name="Alice", label="Alice · Black Coat",
                            clothing_id=cl_id,
                            expected_document_revision=r["document_revision_after"]))
    assert r2["ok"]
    look_id = r2["target"]

    r3 = run(ws.delete_clothing(cl_id, expected_revision=r["revision"]))
    assert r3["ok"] is False and r3["error"] == "resource_in_use"
    assert r3["used_by_looks"] == [look_id]
    # 未产生悬空 Look：clothing 仍在
    assert cl_id in ResourceStore(env["pdir"], env["pdir"].name).load().clothings

    # 无引用的 clothing 可正常删除
    r4 = run(ws.create_clothing(name="Unused", expected_document_revision=r2["document_revision_after"]))
    r5 = run(ws.delete_clothing(r4["target"], expected_revision=r4["revision"]))
    assert r5["ok"] and r5["verified"] and r5["after"] is None


# ---- T5 register ReferenceAsset：合法 local file → 真实 checksum ----
def test_t5_register_reference_asset_local_checksum(env):
    ws = env["ws"]
    img = env["pdir"] / "o" / "coat_ref.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    payload = b"\x89PNG-real-coat-bytes"
    img.write_bytes(payload)

    r = run(ws.register_reference_asset(local="o/coat_ref.png", view="flat",
                                        purpose="design", background_type="white",
                                        expected_document_revision=env["doc_rev"]))
    assert r["ok"] and r["verified"]
    a = r["after"]
    assert a["checksum"] == hashlib.sha256(payload).hexdigest()   # 真实 sha256（全长 hex）
    assert a["local"] == "o/coat_ref.png"                          # 保存相对 posix 路径
    assert a["url"] is None and a["source"] == "manual"
    assert a["view"] == "flat" and a["purpose"] == "design"
    assert a["background_type"] == "white"                         # 调用方明确提供才写入

    # URL 注册：无 checksum（不臆造）
    r2 = run(ws.register_reference_asset(url="https://x/coat.png",
                                         expected_document_revision=r["document_revision_after"]))
    assert r2["ok"] and r2["after"]["url"].startswith("https://")
    assert r2["after"]["checksum"] is None


# ---- T6 local path traversal / 绝对路径 / 缺失文件 → 拒绝 ----
def test_t6_local_path_traversal_rejected(env):
    ws = env["ws"]
    cases = [
        "../evil.png",                 # 穿越
        "a/../../evil.png",            # 混合穿越
        str(env["pdir"] / "o" / "x.png"),  # 绝对路径（即使指向项目内）
        "o/missing.png",               # 不存在
        "o/notes.txt",                 # 非图片扩展名
        "",                            # 空
    ]
    for i, local in enumerate(cases):
        r = run(ws.register_reference_asset(
            local=local, expected_document_revision=env["doc_rev"] if i == 0 else None or env["doc_rev"]))
        assert r["ok"] is False and r["error"] == "invalid_reference_input", (local, r)
    # 全部拒绝后 resources.json 未创建（零副作用）
    assert not (env["pdir"] / "output" / "resources.json").exists() or \
           ResourceStore(env["pdir"], env["pdir"].name).load().reference_assets == {}


# ---- T7 create CharacterLook：真实 Character + optional Clothing ----
def test_t7_create_look_valid_references(env):
    ws = env["ws"]
    # 无 clothing（optional）
    r = run(ws.create_look(character_name="Alice", label="Alice · Base",
                           expected_document_revision=env["doc_rev"]))
    assert r["ok"] and r["verified"]
    lk = r["after"]
    assert lk["character_name"] == "Alice" and lk["label"] == "Alice · Base"
    assert lk["generation_status"] == "none"          # 不自动生成
    assert lk["approval_status"] == "unreviewed"
    assert lk["reference_asset_ids"] == [] and lk["approved_reference_asset_ids"] == []

    # 带 clothing
    r2 = run(ws.create_clothing(name="Black Coat", expected_document_revision=r["document_revision_after"]))
    r3 = run(ws.create_look(character_name="Alice", label="Alice · Black Coat",
                            clothing_id=r2["target"],
                            expected_document_revision=r2["document_revision_after"]))
    assert r3["ok"] and r3["after"]["clothing_id"] == r2["target"]
    # look_id 稳定独立身份
    assert r3["target"].startswith("look_")

    # project.json 不被改（Character 不动）
    c = ProjectStore(env["pdir"]).load().get_character("Alice")
    assert c.views["front"].url == "https://x/front" and len(c.views) == 3


# ---- T8 不存在 Character → 拒绝 ----
def test_t8_create_look_missing_character(env):
    ws = env["ws"]
    r = run(ws.create_look(character_name="Nobody", label="x",
                           expected_document_revision=env["doc_rev"]))
    assert r["ok"] is False and r["error"] == "character_not_found"
    assert not (env["pdir"] / "output" / "resources.json").exists()   # 未写入


# ---- T9 不存在 Clothing → 拒绝 ----
def test_t9_create_look_missing_clothing(env):
    ws = env["ws"]
    r = run(ws.create_look(character_name="Alice", clothing_id="clothing_missing",
                           expected_document_revision=env["doc_rev"]))
    assert r["ok"] is False and r["error"] == "clothing_not_found"


# ---- Look update：字段白名单 + clothing 换绑验证 ----
def test_look_update_patch_semantics(env):
    ws = env["ws"]
    r = run(ws.create_look(character_name="Alice", label="L1",
                           expected_document_revision=env["doc_rev"]))
    look_id, look_rev = r["target"], r["revision"]
    r2 = run(ws.create_clothing(name="C1", expected_document_revision=r["document_revision_after"]))
    cl_id = r2["target"]

    r3 = run(ws.update_look(look_id, {"label": "L2", "clothing_id": cl_id,
                                      "appearance_delta": "scar on cheek"},
                            expected_revision=look_rev))
    assert r3["ok"] and r3["after"]["label"] == "L2" and r3["after"]["clothing_id"] == cl_id
    # 换绑到不存在 clothing → 拒绝
    r4 = run(ws.update_look(look_id, {"clothing_id": "clothing_missing"},
                            expected_revision=r3["revision"]))
    assert r4["ok"] is False and r4["error"] == "clothing_not_found"
    # 解绑（null）允许
    r5 = run(ws.update_look(look_id, {"clothing_id": None}, expected_revision=r3["revision"]))
    assert r5["ok"] and r5["after"]["clothing_id"] is None
    # character_name 不可改
    r6 = run(ws.update_look(look_id, {"character_name": "Bob"}, expected_revision=r5["revision"]))
    assert r6["ok"] is False and r6["error"] == "validation_error"


# ---- attach / detach reference（Clothing 绑定，幂等）----
def test_attach_detach_clothing_reference(env):
    ws = env["ws"]
    img = env["pdir"] / "o" / "ref.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"PNG")
    r = run(ws.register_reference_asset(local="o/ref.png",
                                        expected_document_revision=env["doc_rev"]))
    ra_id = r["target"]
    r2 = run(ws.create_clothing(name="C", expected_document_revision=r["document_revision_after"]))
    cl_id = r2["target"]

    r3 = run(ws.attach_clothing_reference(cl_id, ra_id, expected_revision=r2["revision"]))
    assert r3["ok"] and r3["verified"] and r3["after"]["reference_asset_ids"] == [ra_id]
    # 幂等重复 attach
    r4 = run(ws.attach_clothing_reference(cl_id, ra_id, expected_revision=r3["revision"]))
    assert r4["ok"] and r4.get("unchanged") is True
    # 不存在 asset → 拒绝
    r5 = run(ws.attach_clothing_reference(cl_id, "ref_none", expected_revision=r4["revision"]))
    assert r5["ok"] is False and r5["error"] == "asset_not_found"
    # detach
    r6 = run(ws.detach_clothing_reference(cl_id, ra_id, expected_revision=r4["revision"]))
    assert r6["ok"] and r6["after"]["reference_asset_ids"] == []
    # 幂等重复 detach
    r7 = run(ws.detach_clothing_reference(cl_id, ra_id, expected_revision=r6["revision"]))
    assert r7["ok"] and r7.get("unchanged") is True
    # approved_reference_asset_ids 本阶段不被 attach/detach 触碰
    assert r6["after"]["approved_reference_asset_ids"] == []


# ---- T22 project.json schema/数据不受 Resource writes 影响 ----
def test_t22_project_json_untouched_by_resource_writes(env):
    ws = env["ws"]
    pdir = env["pdir"]
    pj = pdir / "output" / "project.json"
    before = pj.read_bytes()
    raw_before = json.loads(before.decode("utf-8"))

    # 走一整套资源写（create/register/attach/look/update/delete）
    r = run(ws.create_clothing(name="Black Coat", expected_document_revision=env["doc_rev"]))
    r2 = run(ws.register_reference_asset(url="https://x/c.png",
                                         expected_document_revision=r["document_revision_after"]))
    r3 = run(ws.attach_clothing_reference(r["target"], r2["target"],
                                          expected_revision=r["revision"]))
    r4 = run(ws.create_look(character_name="Alice", clothing_id=r["target"],
                            expected_document_revision=r3["document_revision_after"]))
    r5 = run(ws.update_look(r4["target"], {"label": "X"}, expected_revision=r4["revision"]))
    assert all(x["ok"] for x in (r, r2, r3, r4, r5))

    after = pj.read_bytes()
    assert after == before                                   # 字节级不变
    raw_after = json.loads(after.decode("utf-8"))
    assert set(raw_after.keys()) == set(raw_before.keys())   # 顶层 shape 不变
    assert raw_after["schema_version"] == 2
    assert len(raw_after["characters"]) == 1 and \
           set(raw_after["characters"][0].keys()) == {"name", "description_en", "views"}


# ---- 并发串行化：同 document revision 的两个并发 create 只成功一个 ----
def test_concurrent_creates_serialized_by_lock(env):
    ws = env["ws"]

    async def two():
        async def mk(tag):
            return await ws.create_clothing(name=tag, expected_document_revision=env["doc_rev"])
        return await asyncio.gather(mk("A"), mk("B"))

    res = run(two())
    assert sorted(x["ok"] for x in res) == [False, True]   # 锁串行化：同快照只有一个能进
    okr = [x for x in res if x["ok"]][0]
    assert okr["verified"] is True


# ---- 生成期间锁可被他人获取（Work A 层面前置验证：mutation 临界区短）----
def test_lock_released_after_each_mutation(env):
    ws = env["ws"]
    run(ws.create_clothing(name="C", expected_document_revision=env["doc_rev"]))
    lock = project_write_lock(env["pdir"].name)
    assert lock.locked() is False   # mutation 返回后锁必须已释放
