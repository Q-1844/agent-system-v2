# tests/test_phase3_relay_guards.py
# Phase 3（执行文档 §三）最小防错守卫的无费用测试：
#   1. ffsync 同步槽（slot_semantics=multi_reference_only）→ i2v/keyframes 边界
#      解析被 first_frame_slot_not_hard_boundary 拦下（video_adapter truth gate）；
#   2. resolve_multi_relay_source 跨段接力 cross_segment 标记（组首 prev_tail
#      残留不再静默）；
#   3. _extract_video_end_frame 文件名带来源版本 id——重提取不覆盖旧字节；
#   4. continuity_tail 持久 provenance（BoundImageRequest → GenerationRequest
#      provenance → 批次 generation_basis 的映射）。
# 全部零 provider 调用、零付费。
import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))

from src.core.models import (Asset, Candidate, ImageBatch, Project, Scene,
                             Shot, ShotStatus)


# ============ 1. ffsync 槽位语义 gate ============

def _shot_with_selected_batch(batch: ImageBatch) -> Shot:
    s = Shot(shot_id="s02_c02", scene_id="s02", index=2)
    s.first_frame_batches = [batch]
    s.last_frame_batches = []
    s.selected_first_frame = batch.candidates[0].candidate_id
    return s


def test_ffsync_multi_reference_slot_rejected_as_hard_boundary():
    from src.generation.video_adapter import _resolve_boundary_frame
    from src.generation.image_adapter import BoundGenerationError
    batch = ImageBatch(
        batch_id="batch_ffsync_test01", kind="first_frame", created_at=1.0,
        status="ready",
        generation_basis={"slot_semantics": "multi_reference_only",
                          "source_video_version": "vv_old00001",
                          "source_shot_id": "s02_c01",
                          "end_source_version": "vv_old00001"},
        candidates=[Candidate(candidate_id="cand_ffsync_test01",
                              local="x.jpg", url="https://h/tail.jpg",
                              status=ShotStatus.DONE.value)])
    shot = _shot_with_selected_batch(batch)
    with pytest.raises(BoundGenerationError) as ei:
        _resolve_boundary_frame(shot, {}, "first", [])
    assert ei.value.code == "first_frame_slot_not_hard_boundary"
    # marker 检查在槽位解析后、freshness 前（槽位无关）：把该批次挂到 last 槽
    # 同样被拦（keyframes last 也不会消费 multi 语义参考记录）。
    shot.selected_last_frame = batch.candidates[0].candidate_id
    shot.last_frame_batches = [batch]
    with pytest.raises(BoundGenerationError) as ei2:
        _resolve_boundary_frame(shot, {}, "last", [])
    assert ei2.value.code == "first_frame_slot_not_hard_boundary"


def test_normal_bound_batch_still_passes_gate():
    """非 ffsync 批次（有完整 generation_basis）不受 marker gate 影响——
    freshness 对齐时照常返回 URL（Phase 2 expB 融合首帧路径的回归保护）。"""
    from src.generation.video_adapter import _resolve_boundary_frame
    batch = ImageBatch(
        batch_id="ff_normal0001", kind="first_frame", created_at=1.0,
        status="pending",
        generation_basis={"binding_readiness": "READY",
                          "binding_revision": "f28fc0dc",
                          "resolved_scene_instance_view_id": "sciv_x"},
        candidates=[Candidate(candidate_id="ff_normal0001_1",
                              local="f.png", url="https://h/first.png",
                              status=ShotStatus.DONE.value)])
    shot = _shot_with_selected_batch(batch)
    url = _resolve_boundary_frame(shot, {"binding_revision": "f28fc0dc",
                                         "resolved_scene_instance_view_id": "sciv_x"},
                                  "first", [])
    assert url == "https://h/first.png"


def test_ffsync_slot_without_marker_still_fails_closed_on_missing_basis():
    """历史 ffsync 批次（无 generation_basis）继续被 freshness gate 的
    artifact_basis_missing fail closed——不因 marker 引入放宽。"""
    from src.generation.video_adapter import _resolve_boundary_frame
    from src.generation.image_adapter import BoundGenerationError
    batch = ImageBatch(
        batch_id="batch_ffsync_legacy", kind="first_frame", created_at=1.0,
        status="ready",
        candidates=[Candidate(candidate_id="cand_ffsync_legacy",
                              local="x.jpg", url="https://h/tail.jpg",
                              status=ShotStatus.DONE.value)])
    shot = _shot_with_selected_batch(batch)
    with pytest.raises(BoundGenerationError) as ei:
        _resolve_boundary_frame(shot, {}, "first", [])
    assert ei.value.code == "boundary_frame_stale"


# ============ 2. 跨段接力标记 ============

def test_multi_relay_cross_segment_flag():
    from src.generation.video_adapter import resolve_multi_relay_source
    proj = Project(name="p")
    s1 = Scene(scene_id="s01", shots=[Shot(shot_id="s01_c02", scene_id="s01", index=2)])
    s2 = Scene(scene_id="s02", shots=[Shot(shot_id="s02_c01", scene_id="s02", index=1)])
    proj.scenes = [s1, s2]
    s1.shots[0].video_end_frame = Asset(url="http://example/tail.png",
                                        status=ShotStatus.DONE.value)
    s1.shots[0].video_end_source_version = "vv_prev00001"
    target = s2.shots[0]
    target.video_reference_asset_id = "prev_tail"
    r = resolve_multi_relay_source(proj, target)
    assert r is not None and r["cross_segment"] is True

    # 同段接力：cross_segment=False
    s2.shots.append(Shot(shot_id="s02_c02", scene_id="s02", index=2))
    c02 = s2.shots[1]
    c02.video_reference_asset_id = "prev_tail"
    s2.shots[0].video_end_frame = Asset(url="http://example/tail2.png",
                                        status=ShotStatus.DONE.value)
    r2 = resolve_multi_relay_source(proj, c02)
    assert r2 is not None and r2["cross_segment"] is False


# ============ 3. 末帧文件名版本化 ============

def test_extract_video_end_frame_versioned_filename(tmp_path, monkeypatch):
    """重提取不再覆盖旧字节：输出文件名带 version_id；旧固定路径哨兵文件原样。"""
    import routers.shots as rs
    pdir = tmp_path / "proj"
    frames = pdir / "output" / "frames"
    frames.mkdir(parents=True)
    # 1s 测试视频（本地 ffmpeg，零费用）
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc=duration=1:size=320x240:rate=24",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    # 旧固定路径哨兵：模拟历史产物字节
    sentinel = frames / "s09_c01_video_end.jpg"
    sentinel.write_bytes(b"OLD-BYTES")

    class _FakeHost:
        async def upload(self, p):
            return "https://host/fake.jpg"
    monkeypatch.setattr(rs, "get_rehost_host", lambda: _FakeHost())

    asset, ok = asyncio.run(rs._extract_video_end_frame(
        pdir, "s09_c01", str(video), "vv_new00001"))
    assert ok and asset.error is None
    assert asset.local.endswith("s09_c01_video_end_vv_new00001.jpg")
    assert Path(asset.local).exists()
    assert sentinel.read_bytes() == b"OLD-BYTES"      # 旧文件未被覆盖
    assert asset.url == "https://host/fake.jpg"


# ============ 4. continuity_tail 持久 provenance ============

def test_continuity_ref_flows_to_generation_request_provenance():
    from src.generation.image_adapter import BoundImageRequest
    from src.generation.agnes_adapter import compile_agnes_image_call
    req = BoundImageRequest(
        shot_id="s02_c02", prompt="p",
        image_refs=["https://h/scene.png", "https://h/look.png",
                    "https://h/tail.jpg"],
        ref_layout="continuity_tail",
        continuity_ref={"source_shot_id": "s02_c01",
                        "end_source_version": "vv_97699959",
                        "url": "https://h/tail.jpg",
                        "media_is_data_uri": False})
    gen = req.to_generation_request(generation_type="first_frame",
                                    size_tier="1K", ratio="16:9")
    assert gen.provenance["continuity_tail"]["source_shot_id"] == "s02_c01"
    assert gen.provenance["continuity_tail"]["end_source_version"] == "vv_97699959"
    # 投影 ReferenceInput：尾帧以 continuity_previous_end 角色存在
    tail_inputs = [ri for ri in gen.reference_inputs
                   if ri.source_type == "continuity_previous_end"]
    assert len(tail_inputs) == 1 and tail_inputs[0].media == "https://h/tail.jpg"
    # 编译层仍把它作为最后一张 image_refs 发出（发送顺序保持）
    call = compile_agnes_image_call(gen)
    assert call.image_refs[-1] == "https://h/tail.jpg"


def test_capability_facts_file_exists_with_decommission_fact():
    """能力事实表存在且带 v2.0 下线公告与日期、2.5 reference 本地状态
    （SKILL.md 判断/事实分离的锚）。"""
    p = (Path(__file__).resolve().parents[1] / "src" / "agents" / "skill_system"
         / "builtin" / "full-story-relay-calibration" / "references"
         / "capability-facts.md")
    t = p.read_text(encoding="utf-8")
    assert "2026-09-25" in t and "下线" in t      # v2.0 官方下线公告
    assert "未确认" in t
    assert "已实现" in t          # 2.5 reference 本地编译状态（2026-09-28 起已实现）
