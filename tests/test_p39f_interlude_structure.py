# tests/test_p39f_interlude_structure.py
# Phase 39F §8 — 插曲制（interlude）结构完整性测试（41.md）。
#
# 插曲 = 尾态大偏离时插入的正式过渡镜（skill 工作流），系统层没有任何
# interlude 特殊实体——本测试验证系统为该工作流提供的确定性基座：
#   1. 插入是正式 Shot：structure_service.insert_shots_after（Director 真实
#      插镜路径）→ id 分配不回收、index 重排、既有 Shot 身份不变；
#   2. Story/Scene 原文不被改写；
#   3. 插曲可 bind（BOUND）→ multi prev_tail 接力按影片顺序自动落到
#      「A → 插曲 → B」链；插曲生成后 B 的 rerun 接力插曲尾帧；
#   4. 插曲 trace 正常（calibration run/link）；
#   5. 级联纪律：下游旧版本 provenance immutable + freshness 如实 stale；
#   6. 2 个连续插曲结构稳定；
#   7. inherit 接力链的插入保护：正接力中的两镜之间插入 → dependency_block
#      （先解除/改锚再插，绝不静默打断接力）。
import json
import sys
from pathlib import Path

import pytest

import deps
from src.core.models import Asset, ShotStatus, VideoVersion
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.shot_binding import ShotBindingService
from src.storyboard.structure_service import insert_shots_after, StructureError

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
from test_shot_binding_s3b1 import make_pdir, run  # noqa: E402
from test_shot_binding_s3b2 import _bind, _build_scene  # noqa: E402
from test_shot_binding_s3c2 import (  # noqa: E402
    FakeMediaClient, _load_project, _load_shot, _StubPM, _run_video_handler,
)
from test_p39f_semantic_closure import (  # noqa: E402
    TAIL1, _set_prev_tail, _slot_state,
)
from src.resources.models import ReferenceAsset  # noqa: E402
from src.resources.store import ResourceStore  # noqa: E402

TAIL_I1 = "http://example/i1_tail.png"


@pytest.fixture()
def venv(tmp_path, monkeypatch):
    """A=s01_c01（尾态偏离但任务已完成）→ B=s01_c02 → C=s02_c01（影片顺序）。"""
    _clear_for_test()
    pdir = make_pdir(tmp_path)
    (pdir / "output" / "scene_system.json").unlink()
    ids = run(_build_scene(pdir))
    svc = ShotBindingService(pdir)
    fake = FakeMediaClient()
    monkeypatch.setattr(deps, "project_manager", _StubPM(pdir))
    monkeypatch.setattr(deps, "agnes_client", fake)
    # C 镜（影片顺序最后）
    proj = ProjectStore(pdir).load()
    from src.core.models import Shot
    proj.scenes[0].shots.append(Shot(shot_id="s02_c01", scene_id="s01", index=3))
    ProjectStore(pdir).save(proj)
    # 场景参考资产（multi 回退路径需要）
    rd = ResourceStore(pdir, pdir.name).load()
    rd.reference_assets["ref_sciv0000001"] = ReferenceAsset(
        asset_id="ref_sciv0000001", url="http://example/sciv.png")
    ResourceStore(pdir, pdir.name).save(rd)
    yield pdir, ids, svc, fake
    _clear_for_test()


def _bind_scene_only(pdir, svc, ids, shot_id):
    ProjectStore(pdir).update_shot(shot_id, lambda s: setattr(s, "characters", []))
    sp = pdir / "output" / "scene_system.json"
    d = json.loads(sp.read_text(encoding="utf-8"))
    for sciv in (d.get("scene_instance_views") or {}).values():
        if sciv.get("scene_instance_id") == ids["inst"]:
            sciv["reference_asset_id"] = "ref_sciv0000001"
    sp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    r = _bind(svc, shot_id=shot_id, inst=ids["inst"], view=ids["view_a"],
              characters=[])
    assert r["ok"], r


def _config_multi_relay(pdir, shot_id):
    # video_prompt_override = 生产中 SW/Director 已设计好 prompt 的既有语义
    # （override 跳过 freshness 自动重建——本测试无 LLM）
    ProjectStore(pdir).update_shot(shot_id, lambda s: (
        setattr(s, "video_reference_asset_id", "prev_tail"),
        setattr(s, "link_mode", "multi"),
        setattr(s, "video_prompt_override", "Interlude designed prompt")))
    _set_video_num_frames(pdir, shot_id)


def _set_video_num_frames(pdir, shot_id, num_frames=97):
    def mut(s):
        s.video.num_frames = num_frames
        s.video.frame_rate = 24
    ProjectStore(pdir).update_shot(shot_id, mut)


def _finalize_video(pdir, shot_id, vv_id, tail_url):
    """模拟镜头视频已完成 + Phase 6A 末帧提取/托管成功。"""
    def mut(s):
        s.video.status = ShotStatus.DONE.value
        s.video_versions.append(VideoVersion(
            version_id=vv_id, created_at=1.0, status=ShotStatus.DONE.value,
            video_id=f"vid_{vv_id}", url="http://example/out.mp4"))
        s.final_video = vv_id
        s.video_end_frame = Asset(url=tail_url, status=ShotStatus.DONE.value)
        s.video_end_source_version = vv_id
    ProjectStore(pdir).update_shot(shot_id, mut)


def _film_order(pdir):
    proj = ProjectStore(pdir).load()
    return [s.shot_id for sc in proj.scenes for s in sc.shots]


def _story_snapshot(pdir):
    """Story/Scene 原文快照（插曲不得改写）。"""
    proj = _load_project(pdir)
    return {"story_text": proj.story_text,
            "scenes": [(sc.scene_id, sc.title, sc.summary)
                       for sc in proj.scenes],
            "descriptions": {sh.shot_id: sh.description
                             for sc in proj.scenes for sh in sc.shots}}


# ============ 1 个插曲 ============

def test_one_interlude_full_structure(venv):
    pdir, ids, svc, fake = venv
    _bind_scene_only(pdir, svc, ids, "s01_c01")
    _bind_scene_only(pdir, svc, ids, "s01_c02")
    _set_prev_tail(pdir, "s01_c01", TAIL1, "vv_a")       # A 视频已完成
    _config_multi_relay(pdir, "s01_c02")                  # B 原本接力 A
    _run_video_handler(pdir, "s01_c02")                   # B 旧版本（接力 A）
    before = _story_snapshot(pdir)
    assert "s01_c02" in before["descriptions"]

    # ---- 插入插曲 I1（Director 真实插镜路径）----
    proj = _load_project(pdir)
    new_shots = insert_shots_after(proj, "s01", "s01_c01",
                                   [{"description": "插曲：从偏离尾帧回正",
                                     "characters": [], "target_duration_s": 8}])
    ProjectStore(pdir).save(proj)
    assert [s.shot_id for s in new_shots] == ["s01_c03"]
    # 镜头顺序正确：A → I1 → B → C；既有 id 身份不变
    assert _film_order(pdir) == ["s01_c01", "s01_c03", "s01_c02", "s02_c01"]
    proj = _load_project(pdir)
    scene = proj.scenes[0]
    assert [s.index for s in scene.shots] == [1, 2, 3, 4], "index 未按新顺序重排"
    # Story/Scene 原文不被改写
    after = _story_snapshot(pdir)
    assert after["story_text"] == before["story_text"]
    assert after["scenes"] == before["scenes"]
    for sid, desc in before["descriptions"].items():
        assert after["descriptions"][sid] == desc
    # 插曲是正式 Shot（非特殊实体）：全字段参与

    # ---- 插曲正常 bind + multi prev_tail 接力（影片顺序 prev = A）----
    _bind_scene_only(pdir, svc, ids, "s01_c03")
    _config_multi_relay(pdir, "s01_c03")
    _run_video_handler(pdir, "s01_c03")
    call_i1 = [c for c in fake.video_calls][-1]
    assert call_i1["image_urls"] == [TAIL1], "插曲未接力上镜 A 真实尾帧"
    vv_i1 = _load_shot(pdir, "s01_c03").video_versions[-1]
    assert vv_i1.input_visuals[0]["semantic_role"] == "relay_start_boundary"
    assert vv_i1.input_visuals[0]["source_shot_id"] == "s01_c01"
    # 插曲首帧槽同步（正常接力镜行为）
    assert _slot_state(pdir, "s01_c03")["selected_url"] == TAIL1
    # 插曲末帧就绪（Phase 6A 模拟）
    _finalize_video(pdir, "s01_c03", "vv_i1", TAIL_I1)

    # ---- trace 正常 ----
    from src.experiments import calibration_trace as ct
    run_doc = ct.create_run(pdir, story_id="story_p39f",
                            title="interlude structure test")
    rec = ct.upsert_link(pdir, run_doc["run_id"], "s01_c03", {
        "expected_tail": "回正到剧本轨道", "actual_tail": "回正完成",
        "human_deviation_level": "none", "human_action": "continue",
        "retry_or_adapt": "adapt", "generation_mode": "multi_relay",
        "purpose": "interlude"})
    assert rec["human_deviation_level"] == "none"
    got = ct.get_link(pdir, run_doc["run_id"], "s01_c03")
    assert got["purpose"] == "interlude"

    # ---- 级联纪律：B 旧版本（接力 A 的历史）immutable + freshness 如实 stale
    # （在 B rerun 之前检查——final 仍指向旧版本时，relay 锚已变为插曲）----
    old_b = _load_shot(pdir, "s01_c02").video_versions[0]
    assert old_b.input_visuals[0]["source_shot_id"] == "s01_c01"
    from src.generation.native_av_pack import native_av_freshness
    proj = _load_project(pdir)
    fr = native_av_freshness(pdir, proj, proj.get_shot("s01_c02"))
    assert fr["status"] == "stale", fr

    # ---- 插曲后 B rerun：接力链自动落到插曲（影片顺序），历史版本不动 ----
    _run_video_handler(pdir, "s01_c02")
    call_b2 = fake.video_calls[-1]
    assert call_b2["image_urls"] == [TAIL_I1], "B rerun 未接力插曲真实尾帧"
    vv_b2 = _load_shot(pdir, "s01_c02").video_versions[-1]
    assert vv_b2.input_visuals[0]["source_shot_id"] == "s01_c03"
    assert _load_shot(pdir, "s01_c02").video_versions[0].input_visuals == \
        old_b.input_visuals


# ============ 2 个连续插曲 ============

def test_two_consecutive_interludes_structure(venv):
    pdir, ids, svc, fake = venv
    _bind_scene_only(pdir, svc, ids, "s01_c01")
    _set_prev_tail(pdir, "s01_c01", TAIL1, "vv_a")
    before = _story_snapshot(pdir)

    proj = _load_project(pdir)
    new_shots = insert_shots_after(proj, "s01", "s01_c01",
                                   [{"description": "插曲1", "characters": [],
                                     "target_duration_s": 8},
                                    {"description": "插曲2", "characters": [],
                                     "target_duration_s": 8}])
    ProjectStore(pdir).save(proj)
    assert [s.shot_id for s in new_shots] == ["s01_c03", "s01_c04"]
    assert _film_order(pdir) == ["s01_c01", "s01_c03", "s01_c04", "s01_c02",
                                 "s02_c01"]
    proj = _load_project(pdir)
    assert [s.index for s in proj.scenes[0].shots] == [1, 2, 3, 4, 5]
    after = _story_snapshot(pdir)
    assert after["story_text"] == before["story_text"]
    assert after["scenes"] == before["scenes"]
    for sid, desc in before["descriptions"].items():
        assert after["descriptions"][sid] == desc

    # 两个插曲接力链：A → I1 → I2 → B（逐镜真实尾帧接力）
    _bind_scene_only(pdir, svc, ids, "s01_c03")
    _config_multi_relay(pdir, "s01_c03")
    _run_video_handler(pdir, "s01_c03")            # I1 接力 A
    assert fake.video_calls[-1]["image_urls"] == [TAIL1]
    def _mut_i1(s):
        s.video_end_frame = Asset(url=TAIL_I1, status=ShotStatus.DONE.value)
        s.video_end_source_version = "vv_i1"
    ProjectStore(pdir).update_shot("s01_c03", _mut_i1)
    _bind_scene_only(pdir, svc, ids, "s01_c04")
    _config_multi_relay(pdir, "s01_c04")
    _run_video_handler(pdir, "s01_c04")            # I2 接力 I1
    assert fake.video_calls[-1]["image_urls"] == [TAIL_I1]
    vv_i2 = _load_shot(pdir, "s01_c04").video_versions[-1]
    assert vv_i2.input_visuals[0]["source_shot_id"] == "s01_c03"


# ============ inherit 接力链的插入保护（负面约束）============

def test_inherit_relay_chain_insertion_guard(venv):
    """B 正接力 A（inherit_previous）时，A、B 之间插入 → dependency_block
    （绝不静默打断接力）；先解除 B 的接力 → 插入成功 → B 改锚到插曲后 ready。"""
    pdir, ids, svc, fake = venv
    _bind_scene_only(pdir, svc, ids, "s01_c01")
    _set_prev_tail(pdir, "s01_c01", TAIL1, "vv_a")
    ProjectStore(pdir).update_shot("s01_c02", lambda s: (
        setattr(s, "opening_source", "inherit_previous"),
        setattr(s, "source_shot_id", "s01_c01"),
        setattr(s, "source_version", "vv_a")))
    _bind_scene_only(pdir, svc, ids, "s01_c02")

    proj = _load_project(pdir)
    with pytest.raises(StructureError) as ei:
        insert_shots_after(proj, "s01", "s01_c01",
                           [{"description": "插曲", "characters": []}])
    assert ei.value.code == "dependency_block"
    assert _film_order(pdir) == ["s01_c01", "s01_c02", "s02_c01"], \
        "被拒插入不得改动镜头顺序"

    # 显式解除接力 → 插入 → 改锚到插曲（插曲末帧就绪 + source_version 同步）
    ProjectStore(pdir).update_shot("s01_c02", lambda s: (
        setattr(s, "opening_source", "independent"),
        setattr(s, "source_shot_id", "")))
    proj = _load_project(pdir)
    insert_shots_after(proj, "s01", "s01_c01",
                       [{"description": "插曲：回正", "characters": []}])
    ProjectStore(pdir).save(proj)
    assert _film_order(pdir) == ["s01_c01", "s01_c03", "s01_c02", "s02_c01"]
    _finalize_video(pdir, "s01_c03", "vv_i1", TAIL_I1)
    ProjectStore(pdir).update_shot("s01_c02", lambda s: (
        setattr(s, "opening_source", "inherit_previous"),
        setattr(s, "source_shot_id", "s01_c03"),
        setattr(s, "source_version", "vv_i1")))
    from routers.shots import continuity_status
    proj = _load_project(pdir)
    status, msg = continuity_status(proj, proj.get_shot("s01_c02"))
    assert status == "ready", (status, msg)
