"""AI-6A Project Revision Foundation 测试（§二十九 7 Case）。

engine 级测试：直接驱动 WorkspaceStore + changeset.preview/apply/reconcile，
不调 Agnes、不依赖 TestClient/server（§二十九：不调用 Agnes 做 AI-6A 测试）。
每个 case 用 tmp_path 独立项目，互不污染。
"""
import json

import pytest

from src.core.models import Project, Scene, Shot, Character
from src.core.store import ProjectStore
from src.revision.workspace import WorkspaceStore
from src.revision import changeset as cs_engine
from src.revision.changeset import preview, apply, reconcile, ChangeSetError, _apply_ops_to_clone
from src.revision.models import ChangeOperation, BeforeAfter, NewShotSpec, OpType as OT, ImpactReasonCode as RC, creative_source_signature
from src.revision.diff import changeset_diff, workspace_aggregate_diff
from src.revision.impact import compute_aggregate_impact


# ============ 辅助 ============
def _proj():
    """3-shot canonical 项目：s01_c01(老,8s) / s01_c02(b,5s) / s01_c03(c,5s)。
    项目含 Character 林夏——shot.characters 必须引用真实项目角色（§二十四 hard）。"""
    return Project(name="t", characters=[Character(name="林夏", description_en="a young woman")], scenes=[Scene(
        scene_id="s01", summary="林夏看雨", characters=["林夏"], shots=[
            Shot(shot_id="s01_c01", scene_id="s01", index=1, description="老", target_duration_s=8, characters=["林夏"]),
            Shot(shot_id="s01_c02", scene_id="s01", index=2, description="b", target_duration_s=5, characters=["林夏"]),
            Shot(shot_id="s01_c03", scene_id="s01", index=3, description="c", target_duration_s=5, characters=["林夏"]),
        ])])


def _store(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    return WorkspaceStore(tmp_path, "t", lambda: ProjectStore(tmp_path).load())


def _upd(shot_id, field, before, after):
    """构造 update_shot operation。"""
    return ChangeOperation(op_type=OT.UPDATE_SHOT.value, scene_id="s01", shot_id=shot_id,
                           **{field: BeforeAfter(before, after)})


# ============ Case 1: UpdateShot preview -> apply；workspace 变，canonical 不变 ============
def test_case1_update_shot_apply_canonical_unchanged(tmp_path):
    st = _store(tmp_path)
    ws = st.create("exp")
    wid = ws.workspace_id

    pv = preview(st, ws, "改c01", "压缩", ws.revision,
                 [_upd("s01_c01", "description", "老", "新")])
    r = apply(st, ws, pv["changeset_id"])
    assert r["applied"] and r["revision"] == 1

    wp = st.load_working_project(wid)
    assert wp.get_shot("s01_c01").description == "新"
    # canonical base 必须完全不变
    base = st.load_base_project(wid)
    assert base.get_shot("s01_c01").description == "老"
    # canonical project.json（正式项目）也不变
    canonical = ProjectStore(tmp_path).load()
    assert canonical.get_shot("s01_c01").description == "老"


# ============ Case 2: UpdateShot + InsertShot + ReorderShots；Preview Diff == Apply 实际结果 ============
def test_case2_preview_diff_equals_apply_result(tmp_path):
    st = _store(tmp_path)
    ws = st.create("exp")
    wid = ws.workspace_id

    before = st.load_working_project(wid)   # apply 前快照
    ops = [
        _upd("s01_c01", "target_duration_s", 8, 5),
        ChangeOperation(op_type=OT.INSERT_SHOT.value, scene_id="s01",
                        new_shot=NewShotSpec(description="林夏从掩体后出现", characters=["林夏"], target_duration_s=5)),
        ChangeOperation(op_type=OT.REORDER_SHOTS.value, scene_id="s01",
                        ordered_shot_ids=["s01_c01", "s01_c04", "s01_c02", "s01_c03"]),
    ]
    pv = preview(st, ws, "combo", "", ws.revision, ops)
    apply(st, ws, pv["changeset_id"])

    after = st.load_working_project(wid)
    actual = changeset_diff(before, after)
    assert actual == pv["diff"], "Preview Diff 必须等于 Apply 实际结果"


# ============ Case 3: Insert/Reorder 后所有旧 Shot ID 不变 ============
def test_case3_stable_shot_ids_on_insert_reorder(tmp_path):
    st = _store(tmp_path)
    ws = st.create("exp")
    wid = ws.workspace_id
    original_ids = {"s01_c01", "s01_c02", "s01_c03"}

    # insert
    ins_op = ChangeOperation(op_type=OT.INSERT_SHOT.value, scene_id="s01",
                             new_shot=NewShotSpec(description="新镜", characters=["林夏"]))
    pv1 = preview(st, ws, "ins", "", ws.revision, [ins_op])
    new_id = ins_op.new_shot.shot_id            # Preview 分配的最终 stable id（§十一）
    assert new_id == "s01_c04", new_id
    apply(st, ws, pv1["changeset_id"])

    # reorder
    pv2 = preview(st, ws, "reorder", "", ws.revision,
                  [ChangeOperation(op_type=OT.REORDER_SHOTS.value, scene_id="s01",
                                   ordered_shot_ids=["s01_c04", "s01_c01", "s01_c02", "s01_c03"])])
    apply(st, ws, pv2["changeset_id"])

    wp = st.load_working_project(wid)
    ids = [s.shot_id for s in wp.scenes[0].shots]
    # 旧 ID 全部保留（身份不变），新 ID 加入，display order = 数组顺序
    assert original_ids.issubset(set(ids))
    assert ids == ["s01_c04", "s01_c01", "s01_c02", "s01_c03"]
    assert "s01_c04" in set(ids)


# ============ Case 4: 旧 changeset base_revision=0 vs workspace revision=1 -> 409 WORKSPACE_CHANGED ============
def test_case4_stale_changeset_409(tmp_path):
    st = _store(tmp_path)
    ws = st.create("exp")

    pv_old = preview(st, ws, "old", "", ws.revision, [_upd("s01_c01", "description", "老", "X")])
    # 先 apply 另一个把 revision 顶到 1
    pv_new = preview(st, ws, "new", "", ws.revision, [_upd("s01_c02", "description", "b", "Y")])
    apply(st, ws, pv_new["changeset_id"])
    assert ws.revision == 1

    # 旧 changeset base_revision=0 != 1
    with pytest.raises(ChangeSetError) as ei:
        apply(st, ws, pv_old["changeset_id"])
    assert ei.value.code == "WORKSPACE_CHANGED"


# ============ Case 5: crash 模拟 -> reconcile 认定 applied，不重复应用 ============
def test_case5_crash_recovery_reconcile(tmp_path):
    st = _store(tmp_path)
    ws = st.create("exp")
    wid = ws.workspace_id

    pv = preview(st, ws, "crash", "", ws.revision, [_upd("s01_c01", "description", "老", "Z")])
    cid = pv["changeset_id"]
    after_sig = pv["after_signature"]

    # 模拟崩溃窗口：写 working + bump revision，但**不**标 changeset applied
    cs = __import__("src.revision.models", fromlist=["WorkspaceChangeSet"]).WorkspaceChangeSet.from_dict(
        st.load_changeset(wid, cid))
    work = st.load_working_project(wid)
    clone = Project.from_dict(work.to_dict())
    cand, _ = _apply_ops_to_clone(clone, cs.operations)
    st.write_working_project_dict(wid, cand.to_dict())
    ws.revision += 1
    ws.working_project_signature = after_sig
    st.save_workspace(ws)

    # changeset 仍 ready_to_apply
    assert st.load_changeset(wid, cid)["status"] == "ready_to_apply"

    reconcile(st, ws)
    assert st.load_changeset(wid, cid)["status"] == "applied"

    # 重复 apply 幂等
    r = apply(st, ws, cid)
    assert r.get("idempotent") is True


# ============ Case 6: UpdateShot.description Impact 报 Scene plan / Shot regen / adjacent continuity ============
def test_case6_impact_update_shot_description(tmp_path):
    st = _store(tmp_path)
    ws = st.create("exp")

    pv = preview(st, ws, "改desc", "", ws.revision, [_upd("s01_c01", "description", "老", "新")])
    imp = pv["impact"]
    reasons = {it["reason_code"] for it in imp["items"]}
    assert RC.SHOT_INTENT_CHANGED.value in reasons
    assert RC.ADJACENCY_CHANGED.value in reasons   # 相邻 c02 受影响
    # 当前 shot regeneration recommended
    shot_item = next(it for it in imp["items"] if it["reason_code"] == RC.SHOT_INTENT_CHANGED.value)
    assert "regeneration recommended" in shot_item["recommended_action"]
    assert imp["summary"]["production_plan"] == "needs re-planning"
    assert imp["summary"]["existing_assets"] == "retained"


# ============ Case 7: canonical 改变 -> base_is_stale=true，但仍可 preview/apply ============
def test_case7_base_stale_but_editable(tmp_path):
    st = _store(tmp_path)
    ws = st.create("exp")
    assert st.base_is_stale(ws) is False

    # 改 canonical（正式项目）
    canon = ProjectStore(tmp_path).load()
    canon.get_shot("s01_c01").description = "正式改了"
    ProjectStore(tmp_path).save(canon)

    ws = st.load(ws.workspace_id)   # 重新读
    assert st.base_is_stale(ws) is True

    # 但 workspace 仍可 preview/apply（working 独立于 canonical）
    pv = preview(st, ws, "stale-but-ok", "", ws.revision, [_upd("s01_c02", "description", "b", "B2")])
    r = apply(st, ws, pv["changeset_id"])
    assert r["applied"]
    assert st.load_working_project(ws.workspace_id).get_shot("s01_c02").description == "B2"


# ============ Case 8: hard validation（§二十四：引用不存在角色 / 非法 duration）============
def test_case8_hard_validation_char_ref_and_duration(tmp_path):
    st = _store(tmp_path); ws = st.create("exp")
    # 引用不存在项目角色 -> hard CHAR_REF_INVALID
    with pytest.raises(ChangeSetError) as ei:
        preview(st, ws, "badchar", "", ws.revision,
                [ChangeOperation(op_type=OT.INSERT_SHOT.value, scene_id="s01",
                                  new_shot=NewShotSpec(description="x", characters=["不存在的角色"], target_duration_s=5))])
    assert ei.value.code == "CHAR_REF_INVALID"
    # 非法 duration -> hard INVALID_DURATION
    with pytest.raises(ChangeSetError) as ei2:
        preview(st, ws, "baddur", "", ws.revision,
                [ChangeOperation(op_type=OT.UPDATE_SHOT.value, scene_id="s01", shot_id="s01_c01",
                                  target_duration_s=BeforeAfter(8, -3))])
    assert ei2.value.code == "INVALID_DURATION"


# ============ Case 9: aggregate impact 按 base↔working 重算（§四十八，不相加历史）============
def test_case9_aggregate_impact_recompute(tmp_path):
    st = _store(tmp_path); ws = st.create("exp"); wid = ws.workspace_id
    base = st.load_base_project(wid)
    assert compute_aggregate_impact(base, st.load_working_project(wid)).summary["shots_modified"] == 0
    pv1 = preview(st, ws, "8to5", "", ws.revision, [_upd("s01_c01", "target_duration_s", 8, 5)])
    apply(st, ws, pv1["changeset_id"])
    rep1 = compute_aggregate_impact(base, st.load_working_project(wid))
    assert "s01_c01" in rep1.affected_shot_ids and rep1.summary["shots_modified"] == 1
    # 5->8 还原：aggregate 必须为空（不能相加历史，§四十八）
    ws = st.load(wid)
    pv2 = preview(st, ws, "5to8", "", ws.revision, [_upd("s01_c01", "target_duration_s", 5, 8)])
    apply(st, ws, pv2["changeset_id"])
    rep2 = compute_aggregate_impact(base, st.load_working_project(wid))
    assert rep2.summary["shots_modified"] == 0 and not rep2.affected_shot_ids, "aggregate 必须按 base↔working 重算"


# ============ Case 10: creative source signature 排除资产、含顺序（§六十/六十一）============
def test_case10_creative_source_signature(tmp_path):
    sig1 = creative_source_signature(_proj())
    # 加生成资产/updated_at 不改 creative sig
    p2 = _proj()
    p2.scenes[0].shots[0].first_frame.mark_done("/tmp/x.png", "http://x")
    p2.scenes[0].shots[0].video.mark_done("/tmp/x.mp4", "http://x")
    p2.updated_at = 999.0
    assert creative_source_signature(p2) == sig1, "creative sig 必须排除生成资产/updated_at（§六十）"
    # shot 顺序变 -> sig 变（§六十一）
    p3 = _proj()
    p3.scenes[0].shots = list(reversed(p3.scenes[0].shots))
    assert creative_source_signature(p3) != sig1, "shot 顺序必须进入 signature（§六十一）"
