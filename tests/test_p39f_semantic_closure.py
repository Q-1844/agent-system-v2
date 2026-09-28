# tests/test_p39f_semantic_closure.py
# Phase 39F — Slot / Input Provenance / Skill Semantic Closure 诊断测试（41.md）。
#
# 回答三个核心问题（全部以「截获真实 provider 调用 + 持久化事实」为准，不以
# adapter 意图或报告文字自证）：
#   P1  39E 的 _sync_first_frame_slot（首帧槽同步）是否污染下一次视频生成？
#       Case A  multi/reference-only → rerun（mode 不许变 i2v、slot 图不许成 hard frame 0）
#       Case B  relay boundary → rerun（opening boundary 仍由 relay 语义决定）
#       Case C  reference-only 进槽后 rerun（不得因进槽变成 hard boundary）
#   P2  VideoVersion.input_visuals 必须 = provider 最终收到的图片输入事实
#       （最后一层 capture/spy 对账：URL/槽位/顺序/semantic_role）。
#   P3  relay source_version freshness：A 更新 selected 版本后，B 的历史
#       VideoVersion provenance immutable，不悄悄改写、不伪装。
#
# 全程 Fake provider 零外部调用；PASS/FAIL 全部机器可验证。
import asyncio
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import deps
from src.core.models import Asset, ShotStatus, VideoVersion
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.generation.video_adapter import BoundGenerationError, build_bound_video_input
from src.shot_binding import ShotBindingService

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
from test_shot_binding_s3b1 import LOOK, make_pdir, run  # noqa: E402
from test_shot_binding_s3b2 import _bind, _build_scene  # noqa: E402
from test_shot_binding_s3c2 import (  # noqa: E402
    FakeMediaClient, _load_project, _load_shot, _set_video_ready,
    _StubPM, _run_video_handler,
)
from routers.shots import _sync_first_frame_slot, continuity_status  # noqa: E402
from src.resources.models import ReferenceAsset  # noqa: E402
from src.resources.store import ResourceStore  # noqa: E402

LOOK_URL = "http://example/alice.png"     # make_pdir 的 Alice approved look hero
SCIV_URL = "http://example/sciv.png"      # 场景参考（环境镜 multi 直生）
TAIL1 = "http://example/s01_tail_v1.png"  # 上镜尾帧（V1）
TAIL2 = "http://example/s01_tail_v2.png"  # 上镜尾帧（A 重生成后的 V2）


@pytest.fixture()
def venv(tmp_path, monkeypatch):
    _clear_for_test()
    pdir = make_pdir(tmp_path)
    (pdir / "output" / "scene_system.json").unlink()
    ids = run(_build_scene(pdir))
    svc = ShotBindingService(pdir)
    fake = FakeMediaClient()
    monkeypatch.setattr(deps, "project_manager", _StubPM(pdir))
    monkeypatch.setattr(deps, "agnes_client", fake)
    yield pdir, ids, svc, fake
    _clear_for_test()


# ---------- helpers ----------

def _bind_scene_only(pdir, svc, ids, shot_id="s01_c01"):
    """纯环境镜（cast 空）+ 场景参考资产带公网 URL（模拟 rehost 后状态）。"""
    ProjectStore(pdir).update_shot(shot_id, lambda s: setattr(s, "characters", []))
    rd = ResourceStore(pdir, pdir.name).load()
    rd.reference_assets["ref_sciv0000001"] = ReferenceAsset(
        asset_id="ref_sciv0000001", url=SCIV_URL)
    ResourceStore(pdir, pdir.name).save(rd)
    sp = pdir / "output" / "scene_system.json"
    d = json.loads(sp.read_text(encoding="utf-8"))
    for sciv in (d.get("scene_instance_views") or {}).values():
        if sciv.get("scene_instance_id") == ids["inst"]:
            sciv["reference_asset_id"] = "ref_sciv0000001"
    sp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    r = _bind(svc, shot_id=shot_id, inst=ids["inst"], view=ids["view_a"],
              characters=[])
    assert r["ok"], r


def _add_shot(pdir, shot_id, scene_id="s01", index=2):
    from src.core.models import Scene, Shot
    proj = ProjectStore(pdir).load()
    proj.scenes[0].shots.append(Shot(shot_id=shot_id, scene_id=scene_id,
                                     index=index))
    ProjectStore(pdir).save(proj)


def _set_prev_tail(pdir, prev_id, url, vv_id):
    """模拟上镜视频已完成 + Phase 6A 末帧提取/托管成功。"""
    def mut(s):
        s.video.status = ShotStatus.DONE.value
        s.video_end_frame = Asset(url=url, status=ShotStatus.DONE.value)
        s.video_end_source_version = vv_id
        s.video_versions.append(VideoVersion(
            version_id=vv_id, created_at=1.0, status=ShotStatus.DONE.value,
            video_id=f"vid_{vv_id}", url="http://example/prev.mp4"))
        s.final_video = vv_id
    ProjectStore(pdir).update_shot(prev_id, mut)


def _slot_state(pdir, shot_id):
    sh = _load_shot(pdir, shot_id)
    sel = sh.first_frame.selected
    return {"status": sh.first_frame.status,
            "selected_url": (sel.url if sel else None),
            "n_batches": len(sh.first_frame_batches or []),
            "last_snapshot": ((sh.first_frame_batches or [])[-1].prompt_snapshot
                              if sh.first_frame_batches else None),
            "link_mode": sh.link_mode,
            "opening_source": sh.opening_source,
            "vref": sh.video_reference_asset_id}


def _assert_multi_reference_dispatch(call, input_visuals):
    """机器断言：multi 语义 = 单参考 image_urls 槽，无 hard frame 0 槽。"""
    assert call["mode"] == "multi"
    assert call["image_url"] is None, \
        "multi dispatch 出现 image_url 槽 = 参考被当成 hard first frame"
    assert isinstance(call["image_urls"], list) and len(call["image_urls"]) == 1
    assert len(input_visuals) == 1
    iv = input_visuals[0]
    assert call["image_urls"][0] == iv["url"], "参考 URL 与 dispatch 不一致"
    assert iv["semantic_role"] in ("reference_only", "relay_start_boundary")
    assert iv["slot"] in ("reference", "first")


def _assert_hard_boundary_dispatch(call, input_visuals, *, mode):
    """机器断言：i2v/keyframes 语义 = 硬边界槽，URL/顺序逐项一致。"""
    assert call["mode"] == mode
    if mode == "i2v":
        assert call["image_urls"] is None
        first = [e for e in input_visuals if e["slot"] == "first"]
        assert len(first) == 1
        assert first[0]["semantic_role"] == "hard_start_boundary"
        assert call["image_url"] == first[0]["url"]
    else:  # keyframes
        assert call["image_url"] is None
        assert call["image_urls"] == [e["url"] for e in input_visuals]
        assert [e["semantic_role"] for e in input_visuals] == \
            ["hard_start_boundary", "hard_end_boundary"]


# ============ P1 Case A — multi/reference-only → rerun ============

def test_case_a1_character_look_reference_rerun_keeps_multi(venv):
    """组首镜 multi + 人物参考：人物图不进首帧槽；rerun 语义不变。"""
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _set_video_ready(pdir, link_mode="multi")
    _run_video_handler(pdir)
    assert len(fake.video_calls) == 1
    vv1 = _load_shot(pdir).video_versions[-1]
    _assert_multi_reference_dispatch(fake.video_calls[0], vv1.input_visuals)
    assert vv1.input_visuals[0]["semantic_role"] == "reference_only"
    assert vv1.input_visuals[0]["source_type"] == "character_look"
    # 人物图不进首帧槽（39E 纪律）
    slot = _slot_state(pdir, "s01_c01")
    assert slot["status"] != "done" and slot["selected_url"] is None
    # 不手工清槽，直接 rerun
    _run_video_handler(pdir)
    assert len(fake.video_calls) == 2
    vv2 = _load_shot(pdir).video_versions[-1]
    _assert_multi_reference_dispatch(fake.video_calls[1], vv2.input_visuals)
    assert vv2.input_visuals[0]["semantic_role"] == "reference_only"
    # 历史 VideoVersion provenance immutable
    assert vv1.input_visuals == vv2.input_visuals == \
        _load_shot(pdir).video_versions[0].input_visuals


def test_case_a2_scene_reference_synced_to_slot_rerun_keeps_multi(venv):
    """Case A/C 合流：multi 参考已同步进首帧槽（[参考] 标记）→ 不清槽 rerun，
    第二次 dispatch 仍是 multi 参考语义，slot 图不得成为 hard frame 0。"""
    pdir, ids, svc, fake = venv
    _bind_scene_only(pdir, svc, ids)
    _set_video_ready(pdir, link_mode="multi")
    _run_video_handler(pdir)
    call1 = fake.video_calls[0]
    vv1 = _load_shot(pdir).video_versions[-1]
    _assert_multi_reference_dispatch(call1, vv1.input_visuals)
    assert vv1.input_visuals[0]["semantic_role"] == "reference_only"
    assert vv1.input_visuals[0]["source_type"] == "scene_instance_view"
    # 系统把关键 reference 显示/同步到槽位，带 [参考] 标记
    slot = _slot_state(pdir, "s01_c01")
    assert slot["status"] == "done" and slot["selected_url"] == SCIV_URL
    assert "[参考]" in (slot["last_snapshot"] or "")
    # 不手工清槽，直接 rerun 同一 Shot
    _run_video_handler(pdir)
    assert len(fake.video_calls) == 2
    call2 = fake.video_calls[1]
    vv2 = _load_shot(pdir).video_versions[-1]
    _assert_multi_reference_dispatch(call2, vv2.input_visuals)
    assert call2["image_urls"] == [SCIV_URL]
    # mode/配置未被槽位同步偷改
    slot2 = _slot_state(pdir, "s01_c01")
    assert slot2["link_mode"] == "multi"
    assert slot2["opening_source"] in (None, "independent")
    # 历史 input_visuals immutable（rerun 追加，不改写）
    assert vv1.input_visuals == \
        _load_shot(pdir).video_versions[0].input_visuals


def test_case_c_synced_reference_never_becomes_hard_frame_on_mode_switch(venv):
    """Case C 收尾：slot 里已有参考图后，即便 mode 被改为 i2v，系统也不把
    无 provenance 的同步图静默当 fresh hard first frame（fail closed，零 provider
    调用）——display state 无法借道变成 generation boundary。"""
    pdir, ids, svc, fake = venv
    _bind_scene_only(pdir, svc, ids)
    _set_video_ready(pdir, link_mode="multi")
    _run_video_handler(pdir)
    assert _slot_state(pdir, "s01_c01")["selected_url"] == SCIV_URL
    # 用户显式改 mode 为 i2v 后 rerun
    ProjectStore(pdir).update_shot("s01_c01", lambda s: setattr(s, "link_mode", "i2v"))
    n_before = len(fake.video_calls)
    with pytest.raises(BoundGenerationError) as ei:
        _run_video_handler(pdir)
    # Phase 3：fail closed 从「意外的 artifact_basis_missing」升级为设计的
    # first_frame_slot_not_hard_boundary（ffsync 批次现带 slot_semantics 标记，
    # video_adapter 编译层 truth gate 拦截）；历史批次（无 marker）仍走
    # artifact_basis_missing / reference_media_unresolvable 路径。
    assert ei.value.code in ("first_frame_slot_not_hard_boundary",
                             "reference_media_unresolvable") or \
        "artifact_basis_missing" in json.dumps(ei.value.detail, default=str)
    assert len(fake.video_calls) == n_before, \
        "同步进槽的参考图被当成 hard first frame 发给了 provider"


# ============ P1 Case B — relay boundary → rerun ============

def test_case_b_multi_relay_rerun_keeps_relay_semantics(venv):
    """B 从 A 的真实 video_end_frame 接力（multi+prev_tail）；生成后首帧槽同步为
    A 尾帧；不清槽 rerun：opening boundary 仍由 relay 语义决定，不退化 i2v hard
    start，不生成新 storyboard first frame 覆盖 relay。"""
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _add_shot(pdir, "s02_c01", index=2)
    _bind(svc, shot_id="s02_c01", inst=ids["inst"], view=ids["view_a"],
          characters=[])
    _set_prev_tail(pdir, "s01_c02", TAIL1, "vv_a1")
    ProjectStore(pdir).update_shot("s02_c01", lambda s: (
        setattr(s, "video_reference_asset_id", "prev_tail"),
        setattr(s, "link_mode", "multi")))
    _set_video_ready(pdir, shot_id := "s02_c01", link_mode="multi")

    _run_video_handler(pdir, "s02_c01")
    call1 = fake.video_calls[0]
    vv1 = _load_shot(pdir, "s02_c01").video_versions[-1]
    _assert_multi_reference_dispatch(call1, vv1.input_visuals)
    iv1 = vv1.input_visuals[0]
    assert iv1["semantic_role"] == "relay_start_boundary"
    assert iv1["source_shot_id"] == "s01_c02"
    assert iv1["end_source_version"] == "vv_a1"
    assert call1["image_urls"] == [TAIL1]
    # 首帧槽同步为 A 的真实尾帧，带接力标记
    slot = _slot_state(pdir, "s02_c01")
    assert slot["status"] == "done" and slot["selected_url"] == TAIL1
    assert "[接力边界]" in (slot["last_snapshot"] or "")

    # 不清槽 rerun
    n_batches_before = slot["n_batches"]
    _run_video_handler(pdir, "s02_c01")
    call2 = fake.video_calls[1]
    vv2 = _load_shot(pdir, "s02_c01").video_versions[-1]
    _assert_multi_reference_dispatch(call2, vv2.input_visuals)
    iv2 = vv2.input_visuals[0]
    assert iv2["semantic_role"] == "relay_start_boundary"
    assert iv2["source_shot_id"] == "s01_c02"
    assert call2["image_urls"] == [TAIL1], "rerun 未按 relay 语义重解析上镜尾帧"
    assert call2["image_url"] is None, "slot 图把 relay 镜变成 i2v hard start"
    # 未生成新的 storyboard first-frame 覆盖 relay（首帧批次数不变）
    assert _slot_state(pdir, "s02_c01")["n_batches"] == n_batches_before
    # 历史 provenance immutable
    proj = _load_project(pdir)
    assert proj.get_shot("s02_c01").video_versions[0].input_visuals == vv1.input_visuals


# ============ P2 — input_visuals ↔ 最终 provider dispatch 对账 ============

def _assert_planned_matches_dispatch(pdir, shot_id, call, input_visuals):
    """adapter planned visuals ↔ final provider payload ↔ persisted 三方对账。"""
    proj = _load_project(pdir)
    shot = proj.get_shot(shot_id)
    planned = build_bound_video_input(pdir, proj, shot)
    if planned.provider_mode == "multi":
        _assert_multi_reference_dispatch(call, input_visuals)
        assert planned.video_reference == call["image_urls"][0]
        assert len(planned.semantic_roles) == len(input_visuals) == 1
        assert planned.semantic_roles[0]["source_type"] == \
            input_visuals[0].get("source_type")
    else:
        _assert_hard_boundary_dispatch(call, input_visuals, mode=planned.provider_mode)
        if planned.provider_mode == "i2v":
            assert planned.first_frame == call["image_url"]
        else:
            assert [planned.first_frame, planned.last_frame] == call["image_urls"]
        # slot 语义与 URL 逐项一致
        role_by_slot = {r["slot"]: r for r in planned.semantic_roles}
        for iv in input_visuals:
            r = role_by_slot[iv["slot"]]
            assert r["candidate_id"] == iv.get("candidate_id")
            assert r["batch_id"] == iv.get("batch_id")


def test_p2_multi_character_look_input_visuals_matches_dispatch(venv):
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _set_video_ready(pdir, link_mode="multi")
    _run_video_handler(pdir)
    vv = _load_shot(pdir).video_versions[-1]
    _assert_planned_matches_dispatch(pdir, "s01_c01", fake.video_calls[0],
                                     vv.input_visuals)


def test_p2_multi_scene_reference_input_visuals_matches_dispatch(venv):
    pdir, ids, svc, fake = venv
    _bind_scene_only(pdir, svc, ids)
    _set_video_ready(pdir, link_mode="multi")
    _run_video_handler(pdir)
    vv = _load_shot(pdir).video_versions[-1]
    _assert_planned_matches_dispatch(pdir, "s01_c01", fake.video_calls[0],
                                     vv.input_visuals)
    assert vv.input_visuals[0]["source_type"] == "scene_instance_view"


def test_p2_multi_relay_prev_tail_input_visuals_matches_dispatch(venv):
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _add_shot(pdir, "s02_c01", index=2)
    _bind(svc, shot_id="s02_c01", inst=ids["inst"], view=ids["view_a"],
          characters=[])
    _set_prev_tail(pdir, "s01_c02", TAIL1, "vv_a1")
    ProjectStore(pdir).update_shot("s02_c01", lambda s: (
        setattr(s, "video_reference_asset_id", "prev_tail"),
        setattr(s, "link_mode", "multi")))
    _set_video_ready(pdir, "s02_c01", link_mode="multi")
    _run_video_handler(pdir, "s02_c01")
    vv = _load_shot(pdir, "s02_c01").video_versions[-1]
    _assert_planned_matches_dispatch(pdir, "s02_c01", fake.video_calls[0],
                                     vv.input_visuals)
    assert vv.input_visuals[0]["end_source_version"] == "vv_a1"
    assert vv.input_visuals[0]["source_shot_id"] == "s01_c02"


def _seed_boundary(pdir, svc, shot_id, slot, url):
    """seed 一个带当前 pack basis 的已选边界批次（freshness 合法）。"""
    from src.core.models import Candidate, ImageBatch
    compiled = svc.compile_reference_pack(shot_id)
    basis = dict(compiled["pack"]["basis"])
    cand_id = f"{slot}_seed0001"
    batch_id = f"ffb_{slot}_01"

    def mut(s):
        batches = s.first_frame_batches if slot == "first" else s.last_frame_batches
        batches.append(ImageBatch(
            batch_id=batch_id, kind=f"{slot}_frame", created_at=1.0,
            status="done",
            candidates=[Candidate(candidate_id=cand_id, url=url,
                                  status=ShotStatus.DONE.value)],
            generation_basis=basis))
        if slot == "first":
            s.selected_first_frame = cand_id
            s.first_frame.candidates = [Asset(local=None, url=url,
                                              status=ShotStatus.DONE.value)]
            s.first_frame.selected_index = 0
            s.first_frame.status = "done"
        else:
            s.selected_last_frame = cand_id
            s.last_frame.candidates = [Asset(local=None, url=url,
                                             status=ShotStatus.DONE.value)]
            s.last_frame.selected_index = 0
            s.last_frame.status = "done"
    ProjectStore(pdir).update_shot(shot_id, mut)


def test_p2_i2v_hard_start_input_visuals_matches_dispatch(venv):
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _seed_boundary(pdir, svc, "s01_c01", "first", "http://example/ff.png")
    _set_video_ready(pdir, link_mode="i2v")
    _run_video_handler(pdir)
    vv = _load_shot(pdir).video_versions[-1]
    _assert_planned_matches_dispatch(pdir, "s01_c01", fake.video_calls[0],
                                     vv.input_visuals)


def test_p2_keyframes_both_boundaries_input_visuals_matches_dispatch(venv):
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _seed_boundary(pdir, svc, "s01_c01", "first", "http://example/ff.png")
    _seed_boundary(pdir, svc, "s01_c01", "last", "http://example/lf.png")
    _set_video_ready(pdir, link_mode="keyframes")
    _run_video_handler(pdir)
    vv = _load_shot(pdir).video_versions[-1]
    _assert_planned_matches_dispatch(pdir, "s01_c01", fake.video_calls[0],
                                     vv.input_visuals)


def test_p2_i2v_inherit_relay_hard_start_matches_dispatch(venv):
    """inherit_previous 接力（i2v hard relay）：首帧=上镜真实尾帧 URL，
    input_visuals 如实记 hard_start_boundary（slot 同步不发生——槽内语义已是
    硬边界，39E sync 早退）。"""
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _add_shot(pdir, "s02_c01", index=2)
    _bind(svc, shot_id="s02_c01", inst=ids["inst"], view=ids["view_a"],
          characters=[])
    _set_prev_tail(pdir, "s01_c02", TAIL1, "vv_a1")
    ProjectStore(pdir).update_shot("s02_c01", lambda s: (
        setattr(s, "opening_source", "inherit_previous"),
        setattr(s, "source_shot_id", "s01_c02"),
        setattr(s, "source_version", "vv_a1")))
    _set_video_ready(pdir, "s02_c01", link_mode="i2v")
    _run_video_handler(pdir, "s02_c01")
    call = fake.video_calls[0]
    vv = _load_shot(pdir, "s02_c01").video_versions[-1]
    _assert_hard_boundary_dispatch(call, vv.input_visuals, mode="i2v")
    assert call["image_url"] == TAIL1
    # inherit 接力镜不做首帧槽同步（槽内已是实际输入）
    assert _slot_state(pdir, "s02_c01")["selected_url"] is None


def test_p2_legacy_unbound_i2v_input_visuals_best_effort(venv):
    """UNBOUND legacy i2v：无 adapter 语义，按实际发送 URL 尽力标注
    hard_start_boundary——不得把「未知」写成「没有输入」。"""
    from test_shot_binding_s3c2 import _seed_first_frame
    pdir, ids, svc, fake = venv
    _seed_first_frame(pdir, url="http://example/ff.png")
    _set_video_ready(pdir, link_mode="i2v")
    _run_video_handler(pdir)
    call = fake.video_calls[0]
    vv = _load_shot(pdir).video_versions[-1]
    assert call["mode"] == "i2v" and call["image_url"] == "http://example/ff.png"
    ivs = vv.input_visuals
    assert len(ivs) == 1 and ivs[0]["semantic_role"] == "hard_start_boundary"
    assert ivs[0]["url"] == call["image_url"]


def test_p2_recover_path_honest_provenance_and_no_phantom_version(venv):
    """recover/续轮询路径：不重提交 provider；不产生幽灵 legacy_video 版本；
    历史 VideoVersion input_visuals 原样保留；recover 新版本的 provenance
    尽力标注（无法恢复历史事实时为诚实空缺省，models.py 已文档化）。"""
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _set_video_ready(pdir, link_mode="multi")
    _run_video_handler(pdir)
    shot = _load_shot(pdir)
    vid = shot.video.video_id
    vv1_snapshot = copy.deepcopy(shot.video_versions[-1].input_visuals)
    # 伪造在途状态 → handler 走 recover 分支（只轮询不重提交）
    def _mut_inflight(s):
        s.video.status = "generating"
    ProjectStore(pdir).update_shot("s01_c01", _mut_inflight)
    n_calls = len(fake.video_calls)
    _run_video_handler(pdir)
    assert len(fake.video_calls) == n_calls, "recover 路径不应重新提交 provider"
    shot = _load_shot(pdir)
    versions = shot.video_versions
    assert all(v.version_id != "legacy_video" for v in versions), \
        "版本史出现幽灵 legacy_video 条目（provenance 污染）"
    assert versions[0].input_visuals == vv1_snapshot, "recover 改写了历史版本 provenance"
    assert versions[-1].video_id == vid, "recover 版本应指向同一 video_id"


# ============ P3 — relay source_version freshness ============

def test_p3_relay_freshness_and_historical_immutability(venv):
    """A 生成 V1 → B relay V1 → A 重生成 V2 并 selected →
    * 不重新生成 B：B 历史 input_visuals 仍 V1（immutable），freshness 读模型
      如实报 stale；
    * 之后重生成 B：新 VideoVersion 才记录 V2；V1 版本不被改写。"""
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _add_shot(pdir, "s02_c01", index=2)
    _bind(svc, shot_id="s02_c01", inst=ids["inst"], view=ids["view_a"],
          characters=[])
    _set_prev_tail(pdir, "s01_c02", TAIL1, "vv_a1")
    ProjectStore(pdir).update_shot("s02_c01", lambda s: (
        setattr(s, "video_reference_asset_id", "prev_tail"),
        setattr(s, "link_mode", "multi")))
    _set_video_ready(pdir, "s02_c01", link_mode="multi")

    _run_video_handler(pdir, "s02_c01")
    proj = _load_project(pdir)
    b_versions = proj.get_shot("s02_c01").video_versions
    vv_b1 = b_versions[-1]
    vv_b1_snapshot = copy.deepcopy(vv_b1.input_visuals)
    assert vv_b1.input_visuals[0]["end_source_version"] == "vv_a1"

    # freshness 读模型：A 未更新时 current
    from src.generation.native_av_pack import native_av_freshness
    fr1 = native_av_freshness(pdir, proj, proj.get_shot("s02_c01"))
    assert fr1["status"] == "current", fr1

    # A 再生成 V2 并切为 selected（末帧随 V2 更新）
    def mut_a(s):
        s.video_versions.append(VideoVersion(
            version_id="vv_a2", created_at=2.0, status=ShotStatus.DONE.value,
            video_id="vid_vv_a2", url="http://example/prev_v2.mp4"))
        s.final_video = "vv_a2"
        s.video_end_frame = Asset(url=TAIL2, status=ShotStatus.DONE.value)
        s.video_end_source_version = "vv_a2"
    ProjectStore(pdir).update_shot("s01_c02", mut_a)

    # 不重新生成 B：历史 provenance immutable + UI 不伪装 + freshness 诚实
    proj = _load_project(pdir)
    b_now = proj.get_shot("s02_c01")
    assert b_now.video_versions[0].input_visuals == vv_b1_snapshot
    assert b_now.video_versions[0].input_visuals[0]["end_source_version"] == "vv_a1"
    assert b_now.final_video == vv_b1.version_id
    fr2 = native_av_freshness(pdir, proj, b_now)
    assert fr2["status"] == "stale", fr2
    assert "visual_basis_fingerprint" in (fr2.get("detail") or {}).get(
        "mismatched_keys", [])

    # 用户之后重新生成 B → 新版本记录 V2；V1 版本原样保留
    _run_video_handler(pdir, "s02_c01")
    proj = _load_project(pdir)
    b_versions = proj.get_shot("s02_c01").video_versions
    assert len(b_versions) == 2
    vv_b2 = b_versions[-1]
    assert vv_b2.input_visuals[0]["end_source_version"] == "vv_a2"
    assert vv_b2.input_visuals[0]["url"] == TAIL2
    assert b_versions[0].input_visuals == vv_b1_snapshot
    assert proj.get_shot("s02_c01").final_video == vv_b2.version_id
    # 第二次 dispatch 确实用了 V2 尾帧
    assert fake.video_calls[-1]["image_urls"] == [TAIL2]


def test_p3_i2v_inherit_relay_stale_verdict(venv):
    """i2v inherit 接力的既有 freshness verdict：来源镜更新后 continuity_status
    = stale（「上一镜头已更新」），不静默替换下游。"""
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _add_shot(pdir, "s02_c01", index=2)
    _bind(svc, shot_id="s02_c01", inst=ids["inst"], view=ids["view_a"],
          characters=[])
    _set_prev_tail(pdir, "s01_c02", TAIL1, "vv_a1")
    ProjectStore(pdir).update_shot("s02_c01", lambda s: (
        setattr(s, "opening_source", "inherit_previous"),
        setattr(s, "source_shot_id", "s01_c02"),
        setattr(s, "source_version", "vv_a1")))
    _set_video_ready(pdir, "s02_c01", link_mode="i2v")
    proj = _load_project(pdir)
    status, msg = continuity_status(proj, proj.get_shot("s02_c01"))
    assert status == "ready", (status, msg)
    # A 更新到 V2
    def mut_a(s):
        s.video_versions.append(VideoVersion(
            version_id="vv_a2", created_at=2.0, status=ShotStatus.DONE.value,
            video_id="vid_vv_a2", url="http://example/prev_v2.mp4"))
        s.final_video = "vv_a2"
        s.video_end_frame = Asset(url=TAIL2, status=ShotStatus.DONE.value)
        s.video_end_source_version = "vv_a2"
    ProjectStore(pdir).update_shot("s01_c02", mut_a)
    proj = _load_project(pdir)
    status, msg = continuity_status(proj, proj.get_shot("s02_c01"))
    assert status == "stale" and "更新" in msg, (status, msg)


# ============ 补充：_sync_first_frame_slot 只动显示，不动语义配置 ============

def test_sync_slot_does_not_touch_semantics_config(venv, tmp_path):
    """39E sync 的写面 = first_frame 批次/指针/selected；绝不改 link_mode /
    opening_source / video_reference_asset_id（mode 偷变的唯一通道被封死）。"""
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _add_shot(pdir, "s02_c01", index=2)
    _set_prev_tail(pdir, "s01_c02", TAIL1, "vv_a1")
    ProjectStore(pdir).update_shot("s02_c01", lambda s: (
        setattr(s, "video_reference_asset_id", "prev_tail"),
        setattr(s, "link_mode", "multi")))
    _set_video_ready(pdir, "s02_c01", link_mode="multi")
    vv = VideoVersion(version_id="vv_x", created_at=1.0,
                      status=ShotStatus.DONE.value,
                      input_visuals=[{"slot": "first",
                                      "semantic_role": "relay_start_boundary",
                                      "url": TAIL1,
                                      "source_type": "prev_tail_relay",
                                      "source_shot_id": "s01_c02",
                                      "end_source_version": "vv_a1"}])
    asyncio.run(_sync_first_frame_slot(ProjectStore(pdir), "s02_c01", vv,
                                       "vv_x", None))
    slot = _slot_state(pdir, "s02_c01")
    assert slot["status"] == "done" and slot["selected_url"] == TAIL1
    assert slot["link_mode"] == "multi"
    assert slot["opening_source"] in (None, "independent")
    assert slot["vref"] == "prev_tail"


# ============ P10 — pregen check fail-open 诚实（41.md §10）============

def test_p10_pregen_check_unavailable_diagnostic(tmp_path, monkeypatch):
    """预检自身异常：fail-open 放行（生成不被阻断），但回执必须带
    pregen_check_unavailable 非阻断诊断——绝不静默冒充「已证明无冲突」。
    正常路径（预检成功）不得出现该诊断（零噪音）。"""
    import json as _json
    import tests.test_p39d_prechecks as _p39d
    pdir, _ids = _p39d._env_bound(tmp_path)

    import src.agents.pregen_checks as _pc
    real = _pc.shot_input_semantics
    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("pregen boom (simulated)")

    monkeypatch.setattr("src.agents.pregen_checks.shot_input_semantics", _boom)
    q = _p39d._FakeQueue()
    out = _p39d._gen_videos(_p39d._rt(pdir, q), pdir, {"shot_ids": ["s01_c01"]})
    assert calls["n"] >= 1, "预检应被实际调用（异常路径被触发）"
    assert len(q.submitted) == 1, "fail-open：预检异常不得阻断生成"
    unavail = out.get("pregen_unavailable_diagnostics") or []
    assert unavail and unavail[0]["diagnostic"] == "pregen_check_unavailable"
    assert "不表示已证明无冲突" in (unavail[0].get("hint") or "")
    assert "pregen_check_unavailable" in (out.get("note") or "")
    flagged = [r for r in (out.get("results") or [])
               if r.get("pregen_check") == "unavailable"]
    assert flagged, "分项结果须带 pregen_check=unavailable 标记"

    # 正常路径零噪音
    monkeypatch.setattr("src.agents.pregen_checks.shot_input_semantics", real)
    q2 = _p39d._FakeQueue()
    out2 = _p39d._gen_videos(_p39d._rt(pdir, q2), pdir, {"shot_ids": ["s01_c01"]})
    assert not (out2.get("pregen_unavailable_diagnostics") or [])
    assert "pregen_check_unavailable" not in (out2.get("note") or "")


# ============ P5/P6/P7/P9 — Skill 文本锚点（39F 定版条款防回退）============

_SKILL_DIR = Path(__file__).parent.parent / "src" / "agents" / "skill_system" / "builtin"


def test_p5_group_definition_is_soft_semantics():
    """段=组 绝对措辞已改为软语义（41.md §5 四情形矩阵）。"""
    def _norm(t):
        return " ".join(t.split())
    skill = _SKILL_DIR / "full-story-relay-calibration" / "SKILL.md"
    ref = _SKILL_DIR / "full-story-relay-calibration" / "references" / "director.md"
    prod = _SKILL_DIR / "full-story-relay-calibration" / "references" / "producer.md"
    assert "段即组" not in _norm(skill.read_text(encoding="utf-8"))
    assert "段=组" not in _norm((skill.read_text(encoding="utf-8")
                                 + ref.read_text(encoding="utf-8")
                                 + prod.read_text(encoding="utf-8")))
    assert "镜头间是否承接上一条真实 `video_end_frame` 决定" in _norm(skill.read_text(encoding="utf-8"))
    assert "跨段 + 显式接力" in _norm(skill.read_text(encoding="utf-8"))
    assert "同段 + intentional_cut" in _norm(skill.read_text(encoding="utf-8"))
    assert "镜头间是否承接上一条真实 `video_end_frame` 决定" in _norm(ref.read_text(encoding="utf-8"))
    assert "镜头间是否承接上一条真实 `video_end_frame` 决定" in _norm(prod.read_text(encoding="utf-8"))


def test_p6_skill_local_trace_closure():
    """trace 完全缺失盲区的 Skill-local 收尾自检条款（41.md §6）。"""
    skill = (_SKILL_DIR / "full-story-relay-calibration" / "SKILL.md").read_text(encoding="utf-8")
    assert "收尾自检" in skill and "trace_read" in skill
    assert "视频已生成完成但本镜 trace 完全不存在" in skill
    ref = (_SKILL_DIR / "full-story-relay-calibration" / "references" / "director.md").read_text(encoding="utf-8")
    assert "收尾自检（39F 定版" in ref
    # Producer 汇报读 trace 事实
    assert "必须读取 calibration.trace_read 的 trace 事实" in skill


def test_p7_seam_single_authority():
    """seam 数值唯一权威口径条款（41.md §7）。"""
    skill = (_SKILL_DIR / "full-story-relay-calibration" / "SKILL.md").read_text(encoding="utf-8")
    assert "唯一权威口径" in skill
    assert "禁止自己另写 ffmpeg" in skill
    assert "不得写成 production trace 的 authoritative seam" in skill
    assert "seam MAE 是证据，不直接决定 minor/major" in skill
    ref = (_SKILL_DIR / "full-story-relay-calibration" / "references" / "director.md").read_text(encoding="utf-8")
    assert "唯一权威口径" in ref


def test_p9_bridge_negative_constraints():
    """bridge 负面约束：intentional cut/新组/跳 Location/蒙太奇默认不插桥（41.md §9）。"""
    skill = (_SKILL_DIR / "keyframes-bridge-repair" / "SKILL.md").read_text(encoding="utf-8")
    assert "Bridge 前提" in skill
    assert "A 端点必须保留 + B 端点必须保留 + 两者业务上必须连续过渡" in skill
    assert "deliberate location jump" in skill and "montage cut" in skill
    assert "「跨 Location」本身不构成插桥理由" in skill


# ============ §2 尾部 — provenance 不完整不得产生假 mode mismatch ============

def test_mode_mismatch_not_fired_on_incomplete_provenance(tmp_path):
    """input_visuals 空（legacy/recover 未知 provenance）→ 推导 None →
    绝不根据缺失数据产生假的 mode_mismatch_warning（41.md §2）。"""
    from src.core.models import Scene, Shot, Project, Character
    from src.core.store import ProjectStore as PS
    from src.agents.director_calibration import _trace_input_hints
    pdir = tmp_path / "proj"
    pdir.mkdir()
    proj = Project(name="proj")
    proj.characters = [Character(name="Alice")]
    s1 = Scene(scene_id="s01")
    s1.shots = [Shot(shot_id="s01_c01", scene_id="s01", index=1)]
    proj.scenes = [s1]
    PS(pdir).save(proj)
    _set_prev_tail(pdir, "s01_c01", "http://example/t.png", "vv_legacy1")
    PS(pdir).update_shot("s01_c01", lambda s: setattr(
        s.video_versions[0], "input_visuals", []))   # 未知 provenance
    hints = _trace_input_hints(pdir, "s01_c01",
                               {"generation_mode": "multi_reference"})
    assert "mode_mismatch_warning" not in hints, \
        "空 provenance 不得推导出假 mode mismatch"
