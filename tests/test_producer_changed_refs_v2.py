"""Agent System V2 Phase B — Commit A：Producer changed_refs / receipt 扩展测试（26.md §2.1 B1）。

覆盖：
- A1 老 Character update → characters 键正确（三老键完全兼容）；
- A2 Look create 回执（changed_resources）→ looks 正确；
- A3 Prop update/register 回执 → props 正确；
- A4 Composition 回执 → compositions 正确；
- A5 多种资源同一 task → 全部记录（含 reference_assets/clothings）；
- A6 零 mutation → changed_refs 仍为空（全键形状，值全空）；
- A7 stale / failed / unchanged 回执不产生 changed_refs（只从 ok+verified+非unchanged 派生）；
- A8 delegate Task Result 全流程：三老键 + 新资源键并存；早期失败回执带全键空骨架。

不联网（ScriptedClient）。派生规则只认真实回执字段，绝不采信模型文本——与既有
事实层级一致。
"""
import asyncio

from src.core.store import ProjectStore
from src.agents import producer
from src.agents.producer import (_derive_changed_refs, _wrap_task_result,
                                 _empty_changed_refs)

from tests.test_producer_bridge import (ScriptedClient, _save_proj, _runtime,
                                        _run, _tool_call, _char_rev)


def _bridge_client(env, specialist_script):
    """Producer 委派 character_designer 的脚本（委派 1 次 + Specialist 脚本 + 双方收尾）。"""
    return ScriptedClient([
        (None, [_tool_call("character_designer.delegate", env, "call_del")]),
        *specialist_script,
    ])


# ============ A1：老 Character update（三老键兼容，端到端 delegate 流）============
def test_A1_character_update_changed_refs(tmp_path):
    _save_proj(tmp_path)
    new_desc = "a young woman in a red raincoat, soaked from the rain"
    env = {"objective": "调整林小雨的视觉设定",
           "situation": "雨后氛围",
           "constraints": [], "scope": {}, "user_intent": "调整设定"}
    client = _bridge_client(env, [
        (None, [_tool_call("character.update",
                           {"character_name": "林小雨",
                            "expected_revision": _char_rev(tmp_path, "林小雨"),
                            "description_en": new_desc}, "cd2")]),
        ("已更新林小雨设定。", None),
        ("已让 Resource Agent 调整林小雨设定。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "调整林小雨的视觉设定"))
    rec = res["receipts"][0]
    assert rec["changed_refs"]["characters"] == ["林小雨"]
    # 新键并存且为空（老 Specialist 不产出资源键）
    assert rec["changed_refs"]["looks"] == []
    assert rec["changed_refs"]["props"] == []
    assert rec["changed_refs"]["clothings"] == []
    assert rec["changed_refs"]["compositions"] == []
    assert rec["changed_refs"]["reference_assets"] == []
    # canonical 真被改了
    assert ProjectStore(tmp_path).load().get_character("林小雨").description_en == new_desc


# ============ A2-A7：_derive_changed_refs 派生规则（确定性单测）============
def test_A2_look_create_receipt_derives_looks():
    receipts = [{"ok": True, "verified": True, "operation": "resource.look.prepare",
                 "resource_kind": "look", "resource_id": "look_abc123",
                 "changed_resources": [{"kind": "look", "id": "look_abc123"}]}]
    refs = _derive_changed_refs("character_designer", receipts)
    assert refs["looks"] == ["look_abc123"]
    assert refs["characters"] == [] and refs["scenes"] == [] and refs["shots"] == []


def test_A3_prop_receipts_derive_props():
    receipts = [
        # changed_resources 聚合形态（register 工具回执）
        {"ok": True, "verified": True, "operation": "resource.prop.register",
         "changed_resources": [{"kind": "prop", "id": "prop_sword"},
                               {"kind": "reference_asset", "id": "ref_img1"}]},
        # 单数字段兼容形态（write-service 原始回执）
        {"ok": True, "verified": True, "operation": "resource.update_prop",
         "prop_id": "prop_sword"},
    ]
    refs = _derive_changed_refs("character_designer", receipts)
    assert refs["props"] == ["prop_sword"]
    assert refs["reference_assets"] == ["ref_img1"]


def test_A4_composition_receipt_derives_compositions():
    receipts = [{"ok": True, "verified": True, "operation": "resource.composition.prepare",
                 "changed_resources": [{"kind": "composition", "id": "comp_01"}],
                 "candidate_asset_ids": ["ref_c1", "ref_c2"]}]
    refs = _derive_changed_refs("character_designer", receipts)
    assert refs["compositions"] == ["comp_01"]
    assert refs["reference_assets"] == ["ref_c1", "ref_c2"]


def test_A5_mixed_resources_all_recorded():
    receipts = [
        {"ok": True, "verified": True, "operation": "character.update",
         "character_name": "林小雨"},
        {"ok": True, "verified": True, "operation": "resource.clothing.upsert",
         "changed_resources": [{"kind": "clothing", "id": "clothing_red"}]},
        {"ok": True, "verified": True, "operation": "resource.look.prepare",
         "changed_resources": [{"kind": "look", "id": "look_a"}],
         "candidate_asset_ids": ["ref_a1"]},
        {"ok": True, "verified": True, "operation": "resource.prop.register",
         "changed_resources": [{"kind": "prop", "id": "prop_p1"}]},
        {"ok": True, "verified": True, "operation": "resource.composition.prepare",
         "changed_resources": [{"kind": "composition", "id": "comp_c1"}]},
    ]
    refs = _derive_changed_refs("character_designer", receipts)
    assert refs["characters"] == ["林小雨"]
    assert refs["clothings"] == ["clothing_red"]
    assert refs["looks"] == ["look_a"]
    assert refs["props"] == ["prop_p1"]
    assert refs["compositions"] == ["comp_c1"]
    assert refs["reference_assets"] == ["ref_a1"]
    # 排序稳定
    refs2 = _derive_changed_refs("character_designer", list(reversed(receipts)))
    assert refs2 == refs


def test_A6_zero_mutation_all_keys_empty():
    refs = _derive_changed_refs("character_designer", [])
    assert refs == {"scenes": [], "shots": [], "characters": [], "sequences": [], "story": [],
                    "clothings": [], "looks": [], "props": [],
                    "reference_assets": [], "compositions": [], "bindings": [],
                    "locations": [], "location_views": [],
                    "scene_instances": [], "scene_instance_views": []}


def test_A7_stale_failed_unchanged_excluded():
    receipts = [
        {"ok": False, "verified": False, "stale": True, "operation": "resource.look.prepare",
         "changed_resources": [{"kind": "look", "id": "look_stale"}]},
        {"ok": True, "verified": False, "operation": "resource.prop.register",
         "changed_resources": [{"kind": "prop", "id": "prop_unverified"}]},
        {"ok": True, "verified": True, "unchanged": True, "operation": "resource.clothing.upsert",
         "changed_resources": [{"kind": "clothing", "id": "clothing_same"}]},
    ]
    refs = _derive_changed_refs("character_designer", receipts)
    assert refs["looks"] == [] and refs["props"] == [] and refs["clothings"] == []


def test_A7b_empty_changed_resources_and_bad_shapes_ignored():
    receipts = [{"ok": True, "verified": True, "operation": "x",
                 "changed_resources": [None, "look_a", {"kind": "?", "id": "x"},
                                       {"kind": "look", "id": "  "},
                                       {"kind": "look", "id": "look_ok"}]}]
    refs = _derive_changed_refs("character_designer", receipts)
    assert refs["looks"] == ["look_ok"]


# ============ A8：TaskResult 包装全流程（新键进 changed_refs；verified 语义不变）============
def test_A8_wrap_task_result_resource_keys():
    tr = {"receipts": [{"ok": True, "verified": True, "operation": "resource.look.prepare",
                        "changed_resources": [{"kind": "look", "id": "look_a"}]},
                       {"ok": True, "verified": True, "operation": "character.update",
                        "character_name": "林小雨"}],
          "message": {"status": None}, "assistant_message": "完成", "trace": []}
    out = _wrap_task_result(tr, None, "task_x", "wf_x", "character_designer", "sess_x")
    assert out["changed_refs"]["characters"] == ["林小雨"]
    assert out["changed_refs"]["looks"] == ["look_a"]
    assert out["verified"] is True
    assert out["status"] == "completed"


def test_A8b_empty_changed_refs_skeleton_shape():
    # 28.md §22：Phase C2 场景域四键
    assert _empty_changed_refs() == {"scenes": [], "shots": [], "characters": [], "sequences": [], "story": [],
                                     "clothings": [], "looks": [], "props": [],
                                     "reference_assets": [], "compositions": [],
                                     "bindings": [], "locations": [],
                                     "location_views": [], "scene_instances": [],
                                     "scene_instance_views": []}
