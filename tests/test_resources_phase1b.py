# tests/test_resources_phase1b.py
# Production Resources V1 — Phase 1B 验收测试（T1-T17）。
# 覆盖：兼容 / 持久化 / 零副作用 / fail-closed / legacy adapter / 轻量 list /
#       完整 read / revision deterministic / 冻结区结构不变 / 只读 HTTP API。
# 全部 tmp_path 隔离，不触碰真实 projects/。
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.store import ProjectStore
from src.core.models import Project, Character, Asset, ShotStatus
from src.utils.project_manager import ProjectManager
from src.resources.models import (
    ReferenceAsset, CharacterLook, Clothing, Prop, ResourceDocument,
    ResourceSchemaError, ResourceScope, ResourceSource, ApprovalState, GenerationStatus,
    new_reference_asset_id, new_look_id, new_clothing_id, new_prop_id,
    document_revision, look_revision, clothing_revision,
)
from src.resources.store import ResourceStore, legacy_character_references
from src.resources.service import ResourceService
import routers.resources as rres


# ============ fixtures / helpers ============
def make_project(base: Path, with_character: bool = False) -> Path:
    """建最小项目目录 + 空 project.json，布局 base/<name>/（与 ProjectManager 一致）。
    可选带一个带三视图的 Character。"""
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


def populate_resources(svc: ResourceService) -> dict:
    """经 service 内部 create 造一组相互关联的资源。返回 id 映射。"""
    ra = svc.create_reference_asset(view="front", purpose="identity",
                                    local="o/ra_front.png", url="https://x/front", checksum="c1")
    ra2 = svc.create_reference_asset(view="hero", purpose="look",
                                     local="o/ra_hero.png", url="https://x/hero")
    cl = svc.create_clothing(name="Black Coat", reference_asset_ids=[ra.asset_id],
                             cover_asset_id=ra.asset_id, tags=["dark"])
    lk = svc.create_look(character_name="Alice", label="Alice · Black Coat",
                         clothing_id=cl.clothing_id,
                         reference_asset_ids=[ra.asset_id, ra2.asset_id],
                         approved_reference_asset_ids=[ra.asset_id],
                         cover_asset_id=ra.asset_id,
                         generation_status=GenerationStatus.READY.value)
    pr = svc.create_prop(name="Sword", reference_asset_ids=[ra2.asset_id])
    return {"ra": ra, "ra2": ra2, "cl": cl, "lk": lk, "pr": pr}


@pytest.fixture
def svc(tmp_path) -> ResourceService:
    pdir = make_project(tmp_path)
    return ResourceService(pdir, pdir.name)


@pytest.fixture
def client(tmp_path):
    """只挂 resources.router 的最小 FastAPI app + PM 指向 tmp_path。"""
    pdir = make_project(tmp_path)
    service = ResourceService(pdir, pdir.name)
    ids = populate_resources(service)
    orig = rres.get_project_manager
    rres.get_project_manager = lambda: ProjectManager(base_dir=str(tmp_path))
    app = FastAPI()
    app.include_router(rres.router)
    try:
        yield TestClient(app), service, ids
    finally:
        rres.get_project_manager = orig


# ============ T1 旧项目无 resources.json：list 空 / 不创建文件 / project.json 字节不变 ============
def test_t1_read_zero_side_effect(tmp_path):
    pdir = make_project(tmp_path)
    pj = pdir / "output" / "project.json"
    before = pj.read_bytes()

    rs = ResourceStore(pdir, pdir.name)
    svc = ResourceService(pdir, pdir.name)
    assert not rs.exists()
    assert rs.load() == ResourceDocument()
    assert svc.list_resources() == []
    assert svc.list_looks() == []
    assert svc.list_clothings() == []
    assert svc.list_props() == []
    # 读后：resources.json 仍不存在；project.json 字节不变
    assert not rs.exists()
    assert pj.read_bytes() == before


# ============ T2 第一次写入才创建 resources.json ============
def test_t2_first_write_creates_file(tmp_path):
    pdir = make_project(tmp_path)
    rs = ResourceStore(pdir, pdir.name)
    assert not rs.path.exists()
    rs.put_reference_asset(ReferenceAsset(asset_id=new_reference_asset_id(), view="front"))
    assert rs.path.exists()
    assert rs.load().reference_assets  # 可读回


# ============ T3 ReferenceAsset round-trip ============
def test_t3_reference_asset_round_trip(tmp_path):
    pdir = make_project(tmp_path)
    ra = ReferenceAsset(asset_id=new_reference_asset_id(), media_type="image",
                        local="o/a.png", url="https://x/a", checksum="cafe1234",
                        view="side", purpose="identity", background_type="transparent",
                        source="agnes")
    ResourceStore(pdir, pdir.name).put_reference_asset(ra)
    back = ResourceStore(pdir, pdir.name).get_reference_asset(ra.asset_id)  # 新实例=重启语义
    assert back.local == "o/a.png"
    assert back.url == "https://x/a"
    assert back.checksum == "cafe1234"
    assert back.view == "side"
    assert back.purpose == "identity"
    assert back.background_type == "transparent"
    assert back.source == "agnes"
    assert back.scope == ResourceScope.PROJECT.value


# ============ T4 CharacterLook round-trip（关系保持）============
def test_t4_look_round_trip_relationships(tmp_path):
    pdir = make_project(tmp_path)
    ids = populate_resources(ResourceService(pdir, pdir.name))
    lk, cl, ra, ra2 = ids["lk"], ids["cl"], ids["ra"], ids["ra2"]

    back = ResourceService(pdir, pdir.name).read_look(lk.look_id)
    assert back["character_name"] == "Alice"
    assert back["clothing_id"] == cl.clothing_id
    assert back["reference_asset_ids"] == [ra.asset_id, ra2.asset_id]
    assert back["approved_reference_asset_ids"] == [ra.asset_id]
    assert back["cover_asset_id"] == ra.asset_id
    assert back["generation_status"] == GenerationStatus.READY.value


# ============ T5 Clothing rename：clothing_id 不变；Look 关系不失效 ============
def test_t5_clothing_rename_id_stable(tmp_path):
    pdir = make_project(tmp_path)
    ids = populate_resources(ResourceService(pdir, pdir.name))
    svc = ResourceService(pdir, pdir.name)
    cl, lk = ids["cl"], ids["lk"]
    old_rev = svc.read_clothing(cl.clothing_id)["revision"]

    svc.update_clothing(cl.clothing_id, lambda c: setattr(c, "name", "Dark Coat"))
    back_c = svc.read_clothing(cl.clothing_id)
    back_l = svc.read_look(lk.look_id)
    assert back_c["clothing_id"] == cl.clothing_id       # id 不变
    assert back_c["name"] == "Dark Coat"
    assert back_c["revision"] != old_rev                  # 内容变了 → revision 变
    assert back_l["clothing_id"] == cl.clothing_id        # Look 关系不失效
    assert back_l["clothing"]["name"] == "Dark Coat"


# ============ T6 Look label rename：look_id 不变 ============
def test_t6_look_label_rename_id_stable(tmp_path):
    pdir = make_project(tmp_path)
    ids = populate_resources(ResourceService(pdir, pdir.name))
    svc = ResourceService(pdir, pdir.name)
    lk = ids["lk"]

    svc.update_look(lk.look_id, lambda l: setattr(l, "label", "Alice · Base"))
    back = svc.read_look(lk.look_id)
    assert back["look_id"] == lk.look_id
    assert back["label"] == "Alice · Base"
    # list 同步反映新 label
    assert any(x["id"] == lk.look_id and x["name"] == "Alice · Base"
               for x in svc.list_looks())


# ============ T7 legacy Character.views adapter ============
def test_t7_legacy_adapter_read_only(tmp_path):
    pdir = make_project(tmp_path, with_character=True)
    pj = pdir / "output" / "project.json"
    before = pj.read_bytes()

    refs = legacy_character_references(pdir, "Alice")
    assert [r.view for r in refs] == ["front", "side", "back"]
    assert {r.asset_id for r in refs} == {"legacy:Alice:front", "legacy:Alice:side", "legacy:Alice:back"}
    assert all(r.source == "legacy" and r.purpose == "identity" for r in refs)
    assert refs[0].local == "o/alice_front_seg.png"

    # 不修改 Character / project.json 字节不变
    assert pj.read_bytes() == before
    c = ProjectStore(pdir).load().get_character("Alice")
    assert c.views["front"].local == "o/alice_front_seg.png"

    # 不写 resources.json
    assert not (pdir / "output" / "resources.json").exists()

    # 缺失角色 → []；service 入口同结果
    assert legacy_character_references(pdir, "Nobody") == []
    svc = ResourceService(pdir, pdir.name)
    assert [x["asset_id"] for x in svc.legacy_character_references("Alice")] == \
           [r.asset_id for r in refs]


# ============ T8 ReferenceAsset single truth ============
def test_t8_single_truth_asset_objects(tmp_path):
    pdir = make_project(tmp_path)
    svc = ResourceService(pdir, pdir.name)
    ids = populate_resources(svc)
    doc = ResourceStore(pdir, pdir.name).load()

    assert len(doc.reference_assets) == 2
    # Look/Clothing/Prop 只存 id，不嵌整对象（序列化里无嵌套 reference_assets 键）
    lk_dict = doc.looks[ids["lk"].look_id].to_dict()
    assert "reference_assets" not in lk_dict
    assert set(lk_dict["reference_asset_ids"]) == {ids["ra"].asset_id, ids["ra2"].asset_id}
    cl_dict = doc.clothings[ids["cl"].clothing_id].to_dict()
    assert cl_dict["reference_asset_ids"] == [ids["ra"].asset_id]
    pr_dict = doc.props[ids["pr"].prop_id].to_dict()
    assert pr_dict["reference_asset_ids"] == [ids["ra2"].asset_id]


# ============ T9 轻量 list：无 base64 / bytes / 大型 metadata ============
def test_t9_list_is_lightweight(svc):
    populate_resources(svc)
    lst = svc.list_resources()
    forbidden = {"local", "url", "checksum", "base64", "bytes", "prompt",
                 "receipt", "appearance_description", "appearance_delta",
                 "design_traits", "materials", "colors"}
    leaked = set().union(*[set(d.keys()) for d in lst]) & forbidden
    assert not leaked, f"list leaked heavy fields: {leaked}"
    for d in lst:
        assert {"id", "type", "name", "status", "cover_asset_id", "revision",
                "relationship_summary"} <= set(d.keys())


# ============ T10 read 完整 typed relationship ============
def test_t10_read_full_relationships(svc):
    ids = populate_resources(svc)
    lk, cl, pr, ra, ra2 = ids["lk"], ids["cl"], ids["pr"], ids["ra"], ids["ra2"]

    rl = svc.read_look(lk.look_id)
    assert rl["clothing"]["clothing_id"] == cl.clothing_id
    assert {a["asset_id"] for a in rl["reference_assets"]} == {ra.asset_id, ra2.asset_id}
    assert all(not a["missing"] for a in rl["reference_assets"])
    assert rl["cover_asset"]["asset_id"] == ra.asset_id

    rc = svc.read_clothing(cl.clothing_id)
    assert rc["used_by_looks"] == [lk.look_id]

    rp = svc.read_prop(pr.prop_id)
    assert rp["reference_assets"][0]["asset_id"] == ra2.asset_id

    rra = svc.read_reference_asset(ra2.asset_id)
    assert lk.look_id in rra["used_by"] and pr.prop_id in rra["used_by"]

    # read_resource dispatch
    assert svc.read_resource(lk.look_id)["look_id"] == lk.look_id
    assert svc.read_resource(cl.clothing_id)["clothing_id"] == cl.clothing_id
    assert svc.read_resource(pr.prop_id)["prop_id"] == pr.prop_id
    assert svc.read_resource(ra.asset_id)["asset_id"] == ra.asset_id
    assert svc.read_resource("nope_x") is None

    # 缺失 asset 容错（missing 标记，不抛错）
    svc.store.delete_reference_asset(ra2.asset_id)
    rl2 = svc.read_look(lk.look_id)
    missing = [a for a in rl2["reference_assets"] if a["asset_id"] == ra2.asset_id]
    assert missing and missing[0]["missing"] is True


# ============ T11 revision deterministic ============
def test_t11_revision_deterministic(tmp_path):
    pdir = make_project(tmp_path)
    svc = ResourceService(pdir, pdir.name)
    ids = populate_resources(svc)
    lk, cl = ids["lk"], ids["cl"]

    r1, r2 = svc.read_look(lk.look_id)["revision"], svc.read_look(lk.look_id)["revision"]
    assert r1 == r2 and len(r1) == 8
    assert svc.read_clothing(cl.clothing_id)["revision"] == \
           svc.read_clothing(cl.clothing_id)["revision"]

    doc1 = ResourceStore(pdir, pdir.name).load()
    doc2 = ResourceStore(pdir, pdir.name).load()
    assert document_revision(doc1) == document_revision(doc2)

    # 内容不变 revision 不变；内容变 revision 变
    before = look_revision(ResourceStore(pdir, pdir.name).get_look(lk.look_id))
    svc.update_look(lk.look_id, lambda l: setattr(l, "appearance_delta", "scar on cheek"))
    after = look_revision(ResourceStore(pdir, pdir.name).get_look(lk.look_id))
    assert after != before
    # canonical 内不持久化 revision（derived）
    assert "revision" not in ResourceStore(pdir, pdir.name).get_look(lk.look_id).to_dict()


# ============ T12 未知 schema_version fail closed ============
def test_t12_unknown_schema_version_fail_closed(tmp_path):
    pdir = make_project(tmp_path)
    rs = ResourceStore(pdir, pdir.name)
    rs.path.write_text(json.dumps({"schema_version": 99, "looks": {}}), encoding="utf-8")
    with pytest.raises(ResourceSchemaError):
        rs.load()
    with pytest.raises(ResourceSchemaError):
        ResourceService(pdir, pdir.name).list_looks()


# ============ T13 损坏 resources.json 不被自动覆盖为空 ============
def test_t13_corrupt_json_not_reset(tmp_path):
    pdir = make_project(tmp_path)
    rs = ResourceStore(pdir, pdir.name)
    rs.put_reference_asset(ReferenceAsset(asset_id=new_reference_asset_id()))
    rs.path.write_text("{ broken json", encoding="utf-8")

    with pytest.raises(ResourceSchemaError):
        rs.load()
    with pytest.raises(ResourceSchemaError):
        ResourceService(pdir, pdir.name).list_resources()
    # 文件仍是坏内容（未被悄悄重置为空 doc 覆盖）
    assert "broken" in rs.path.read_text(encoding="utf-8")
    # 顶层非 object 同样 fail closed
    rs.path.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(ResourceSchemaError):
        rs.load()


# ============ T14 restart persistence ============
def test_t14_restart_persistence(tmp_path):
    pdir = make_project(tmp_path)
    svc = ResourceService(pdir, pdir.name)
    ids = populate_resources(svc)

    # 模拟重启：全新 service/store 实例从磁盘重读
    svc2 = ResourceService(pdir, pdir.name)
    assert svc2.read_look(ids["lk"].look_id)["character_name"] == "Alice"
    assert svc2.read_clothing(ids["cl"].clothing_id)["name"] == "Black Coat"
    assert svc2.read_prop(ids["pr"].prop_id)["name"] == "Sword"
    assert len(svc2.store.list_reference_assets()) == 2
    assert svc2.list_looks(character_name="Alice")[0]["id"] == ids["lk"].look_id


# ============ T15 Character Designer 冻结面结构不变 ============
def test_t15_character_designer_tools_intact():
    from src.agents.character_designer import (CHARACTER_DESIGNER_TOOLS,
                                               CHARACTER_WRITE_OPERATIONS,
                                               TOOL_EXECUTORS)
    names = {t["function"]["name"] for t in CHARACTER_DESIGNER_TOOLS}
    # Agent System V2 Phase B 最终工具面（14 基础 + shot.list/read 过滤并入）：
    # + profile/inventory/read 只读 + clothing/look/prop/composition 任务型 mutation
    # 40.md Skill System V1：+skill.list / skill.load / skill.read_resource（只读）
    assert names == {"character.list", "character.read", "character.create",
                     "character.update", "character.delete",
                     "character.generate_views", "web.search",
                     "project.visual_profile.read",
                     "resource.inventory", "resource.read",
                     "resource.clothing.upsert", "resource.look.prepare",
                     "resource.look.approve",
                     "resource.prop.register", "resource.composition.prepare",
                     "resource.library.publish", "resource.library.import",
                     "skill.list", "skill.load", "skill.read_resource"}
    assert {"character.create", "character.update", "character.delete"} <= CHARACTER_WRITE_OPERATIONS
    # 越权写工具仍不得出现（Scene/Story/Shot/Binding/底层细碎 API）
    assert not any(n in ("look.approve", "prop.approve", "revoke_look_approval",
                         "scene_instance.create", "binding.create", "story.update")
                   for n in names)
    # receipt 复用链完好
    from src.agents.script_writer import _base_receipt
    rec = _base_receipt("test.op", ok=True, verified=True)
    assert {"ok", "operation", "verified"} <= set(rec.keys())


# ============ T16 冻结核心模块可导入且未被 resources 包反向依赖 ============
def test_t16_frozen_modules_importable_and_isolated():
    import src.agents.script_writer  # noqa: F401
    import src.agents.character_designer  # noqa: F401
    import src.core.store  # noqa: F401
    import src.core.models  # noqa: F401
    # resources 包不 import 冻结 Agent 模块（只 legacy adapter 引 ProjectStore）
    import src.resources.service as rsvc
    import sys
    src_text = Path(rsvc.__file__).read_text(encoding="utf-8")
    assert "character_designer" not in src_text
    assert "director" not in src_text


# ============ T17 project.json serialization shape 零变化 ============
def test_t17_project_json_shape_unchanged(tmp_path):
    # 1) dataclass 字段冻结：Asset 未被扩展（修正 A）
    assert set(Asset.__dataclass_fields__.keys()) == {"local", "url", "status", "error", "updated_at"}
    # 2) Project.to_dict 顶层 shape（C1 18.md §5：+native_av settings，除此之外不变）
    pdir = make_project(tmp_path, with_character=True)
    raw = json.loads((pdir / "output" / "project.json").read_text(encoding="utf-8"))
    assert set(raw.keys()) == {"name", "schema_version", "created_at", "updated_at",
                               "style", "native_av", "story_text", "characters",
                               "scenes", "next_scene_seq"}
    assert raw["native_av"] == {"spoken_language": ""}
    assert set(raw["characters"][0].keys()) == {"name", "description_en", "views"}
    assert set(raw["characters"][0]["views"]["front"].keys()) == {"local", "url", "status", "error", "updated_at"}
    assert raw["schema_version"] == 2
    # 3) 资源写入后 project.json 字节不变（独立 canonical，互不干扰）
    before = (pdir / "output" / "project.json").read_bytes()
    svc = ResourceService(pdir, pdir.name)
    populate_resources(svc)
    svc.update_look(svc.list_looks()[0]["id"], lambda l: setattr(l, "label", "X"))
    assert (pdir / "output" / "project.json").read_bytes() == before
    # 4) resources.json 与 project.json 是两个文件
    assert (pdir / "output" / "resources.json").exists()


# ============ HTTP API（只读契约）============
def test_api_list_resources(client):
    c, svc, ids = client
    r = c.get(f"/api/project/{svc.project_name}/resources")
    assert r.status_code == 200
    body = r.json()
    types = {d["type"] for d in body["resources"]}
    assert types == {"look", "clothing", "prop"}
    assert not any(k in set().union(*[set(d.keys()) for d in body["resources"]])
                   for k in ("local", "url", "checksum"))

    r = c.get(f"/api/project/{svc.project_name}/resources", params={"type": "look"})
    assert {d["type"] for d in r.json()["resources"]} == {"look"}


def test_api_read_look_and_dispatch(client):
    c, svc, ids = client
    r = c.get(f"/api/project/{svc.project_name}/looks/{ids['lk'].look_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["character_name"] == "Alice"
    assert body["clothing"]["name"] == "Black Coat"
    assert len(body["reference_assets"]) == 2

    # dispatch 端点
    r2 = c.get(f"/api/project/{svc.project_name}/resources/{ids['cl'].clothing_id}")
    assert r2.status_code == 200 and r2.json()["used_by_looks"] == [ids["lk"].look_id]

    # 404
    assert c.get(f"/api/project/{svc.project_name}/looks/look_missing").status_code == 404
    assert c.get(f"/api/project/{svc.project_name}/resources/ref_missing").status_code == 404


def test_api_looks_filter_and_legacy(client):
    c, svc, ids = client
    r = c.get(f"/api/project/{svc.project_name}/looks", params={"character_name": "Alice"})
    assert r.status_code == 200 and len(r.json()["looks"]) == 1

    # legacy 端点（tmp 项目无 Character → 空数组仍 200）
    r = c.get(f"/api/project/{svc.project_name}/resources/legacy-character-references/Alice")
    assert r.status_code == 200 and r.json()["references"] == []


def test_api_read_only_no_mutation_endpoints(client):
    """写端点白名单契约（Phase 1B 只读 → Phase 1C1 §22 开放有限写 API 后演进）：
    resources router 的写方法必须恰为本轮规定的清单，不得夹带其它 mutation
    （无 Look delete / 无 Prop 写 API / 无 approve / reject / 无 Agent 接线端点）。"""
    write_methods = {"post", "put", "patch", "delete"}
    allowed = {
        ("POST", "/api/project/{project}/clothings"),
        ("PATCH", "/api/project/{project}/clothings/{clothing_id}"),
        ("DELETE", "/api/project/{project}/clothings/{clothing_id}"),
        ("POST", "/api/project/{project}/reference-assets"),
        ("POST", "/api/project/{project}/clothings/{clothing_id}/references"),
        ("DELETE", "/api/project/{project}/clothings/{clothing_id}/references/{asset_id}"),
        ("POST", "/api/project/{project}/looks"),
        ("PATCH", "/api/project/{project}/looks/{look_id}"),
        ("POST", "/api/project/{project}/looks/{look_id}/generate"),
        # Phase 1C2：Visual Review + Approval
        ("POST", "/api/project/{project}/looks/{look_id}/review"),
        ("POST", "/api/project/{project}/looks/{look_id}/approve"),
        ("POST", "/api/project/{project}/looks/{look_id}/revoke-approval"),
        # Phase 1C3：Shared Production Library
        ("POST", "/api/project/{project}/resources/publish"),
        ("POST", "/api/project/{project}/resources/publish-character"),
        ("POST", "/api/project/{project}/resources/import"),
        # Phase 1C4：Prop CRUD（Location 不在 Resources 域——归 Scene System）
        ("POST", "/api/project/{project}/props"),
        ("PATCH", "/api/project/{project}/props/{prop_id}"),
        ("DELETE", "/api/project/{project}/props/{prop_id}"),
        ("POST", "/api/project/{project}/props/{prop_id}/references"),
        ("DELETE", "/api/project/{project}/props/{prop_id}/references/{asset_id}"),
        # Phase 1C5：Prop Reference Review + Approval（§7 最小闭环）
        ("POST", "/api/project/{project}/props/{prop_id}/review"),
        ("POST", "/api/project/{project}/props/{prop_id}/approve"),
        ("POST", "/api/project/{project}/props/{prop_id}/revoke-approval"),
        # Phase 1C5：Composition CRUD + generation（§6/§8-§14）
        ("POST", "/api/project/{project}/compositions"),
        ("PATCH", "/api/project/{project}/compositions/{composition_id}"),
        ("POST", "/api/project/{project}/compositions/{composition_id}/generate"),
        # Phase 1C5：Composition Visual Review + Approval（§15/§16）
        ("POST", "/api/project/{project}/compositions/{composition_id}/review"),
        ("POST", "/api/project/{project}/compositions/{composition_id}/approve"),
        ("POST", "/api/project/{project}/compositions/{composition_id}/revoke-approval"),
        # 34.md §5/§9：Look 候选 attach（外部正式资产经 register→attach→review→approve
        # 链进入）+ 人评审查证据（用户人评=最高审查层级，34.md §16/§18）
        # + local-only 资产 rehost 补公网 URL（视频接口公网 URL 约束）
        ("POST", "/api/project/{project}/looks/{look_id}/references"),
        ("POST", "/api/project/{project}/looks/{look_id}/review/human"),
        ("POST", "/api/project/{project}/reference-assets/{asset_id}/rehost"),
        # 34.md Human Gate：从 approved 参考派生姿态/视角变体（同一身份多姿态参考库）
        ("POST", "/api/project/{project}/looks/{look_id}/derive"),
    }
    write_routes = []
    for route in rres.router.routes:
        methods = {m.upper() for m in getattr(route, "methods", set())}
        if methods & {m.upper() for m in write_methods}:
            write_routes.append(route)
            for m in methods & {mm.upper() for mm in write_methods}:
                assert (m, route.path) in allowed, f"未规定的 mutation 端点: {m} {route.path}"
    # 写端点确实存在（防止白名单形同虚设）
    assert len(write_routes) >= 8


def test_api_project_not_found(tmp_path):
    rres.get_project_manager = lambda: ProjectManager(base_dir=str(tmp_path))
    app = FastAPI()
    app.include_router(rres.router)
    c = TestClient(app)
    assert c.get("/api/project/__no_such__/resources").status_code == 404
