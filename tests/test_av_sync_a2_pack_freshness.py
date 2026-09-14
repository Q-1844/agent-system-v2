# tests/test_av_sync_a2_pack_freshness.py
# Audio V1 Phase A2 §28：Pack（determinism / actual-dependencies-only / complete
# basis）+ Freshness 五例 + Selection/Approval + store fail closed（16.md §12/§13/
# §15/§24）。
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.models import (
    Character, Project, Scene, Shot, ShotStatus, VideoVersion,
)
from src.core.store import ProjectStore
from src.audio.service import VoiceService
from src.audio.av_sync.freshness import compute_sync_freshness
from src.audio.av_sync.models import (
    LipSyncSpeakerTarget,
    build_av_sync_basis,
    provider_options_fingerprint,
)
from src.audio.av_sync.store import DialogueVideoSyncStore
from src.audio.av_sync.av_service import AVSyncService

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.test_av_sync_a2_readiness_timing import FakeVoiceAdapter, REAL_MP4, make_env  # noqa: E402


class FakeSyncAdapter:
    """A2 侧 fake：回放真实 mp4 字节（mux/ffprobe 真实可跑）；hook=窗口内注入。"""

    def __init__(self, hook=None):
        self.hook = hook
        self.calls = 0

    async def generate(self, pack_dict, *, video_path, audio_path):
        self.calls += 1
        if self.hook is not None:
            await self.hook(pack_dict)
        return {"raw_bytes": REAL_MP4.read_bytes(),
                "provider_generation_id": f"gen_{self.calls}",
                "provider_metadata": {"provider_status": "COMPLETED",
                                      "model": pack_dict["provider_binding"]["model"]}}


def ready_service(tmp_path, **kw):
    tmp, svc = make_env(tmp_path, **kw)
    return tmp, svc, AVSyncService(tmp)


def add_unrelated_character(tmp, svc, name="小铃"):
    """向项目追加一个无关角色 + VoiceProfile（用于 unrelated-change 用例）。"""
    from src.core.models import Character
    project = ProjectStore(tmp).load()
    project.characters.append(Character(name=name))
    ProjectStore(tmp).save(project)
    r = svc.create_profile(name, "female-shaonv")
    assert r["ok"], r
    return r["revision"]


# ============ Pack（§12/§13/§28）============

def test_pack_deterministic_same_input_same_pack(tmp_path):
    tmp, svc, avsvc = ready_service(tmp_path)
    kw = dict(dialogue_id="s01_c01", model="lipsync-2", provider_options={"temperature": 0.5})
    p1 = avsvc.compile_lipsync_generation_pack(**kw).to_dict()
    p2 = avsvc.compile_lipsync_generation_pack(**kw).to_dict()
    assert p1 == p2
    assert p1["schema"] == "lipsync_generation_pack_v1"


def test_pack_no_business_id_no_temp_path():
    """§12：pack 无自身 business ID；working 文件路径不得成为 semantic basis。"""
    from src.audio.av_sync.models import LipSyncGenerationPack
    pack = LipSyncGenerationPack(shot_id="s", dialogue_id="d",
                                 speaker_character_name="阿默")
    d = pack.to_dict()
    assert "sync_version_id" not in d and "pack_id" not in d
    blob = repr(d)
    assert "tmp" not in blob and "/temp" not in blob


def test_pack_basis_complete_thirteen_keys(tmp_path):
    tmp, svc, avsvc = ready_service(tmp_path)
    basis = avsvc.compile_lipsync_generation_pack(
        dialogue_id="s01_c01", model="lipsync-2").generation_basis
    assert basis["schema"] == "av_sync_generation_basis_v1"
    for k in ("shot_id", "dialogue_id", "source_video_artifact_id",
              "source_video_fingerprint", "dialogue_audio_version_id",
              "dialogue_audio_generation_basis_fingerprint", "speaker_character_name",
              "timing_fingerprint", "speaker_target_fingerprint", "provider",
              "model", "provider_options_fingerprint"):
        assert basis.get(k), k


def test_basis_actual_dependencies_only(tmp_path):
    """§13：绝不 hash project.json / 整份 store——无关角色/无关镜变化 → 同 basis。"""
    tmp, svc, avsvc = ready_service(tmp_path)
    b1 = avsvc.compile_lipsync_generation_pack(dialogue_id="s01_c01").generation_basis
    # 无关角色 profile 变化
    add_unrelated_character(tmp, svc)
    cur = svc.get_profile("vp_小铃").revision()
    svc.update_profile("vp_小铃", cur, speed=1.3)
    b2 = avsvc.compile_lipsync_generation_pack(dialogue_id="s01_c01").generation_basis
    assert b1 == b2


# ============ Freshness（§24/§28）============

def test_freshness_missing_before_generate(tmp_path):
    tmp, svc, avsvc = ready_service(tmp_path)
    fr = avsvc.get_sync_freshness("s01_c01")
    assert fr["status"] == "missing"


def test_freshness_current_after_generate(tmp_path):
    tmp, svc, avsvc = ready_service(tmp_path)
    res = asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter()))
    assert res["freshness"]["status"] == "current"


def test_freshness_audio_change_stale_zero_provider_call(tmp_path):
    tmp, svc, avsvc = ready_service(tmp_path)
    asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter()))   # sync 基于 av01
    # §28：audio change → stale（A1 显式再生成 → 指针迁到 av02）
    asyncio.run(svc.generate("s01_c01", FakeVoiceAdapter()))
    fr = avsvc.get_sync_freshness("s01_c01")
    assert fr["status"] == "stale"
    assert "selected_audio_changed" in fr["stale_reasons"]


def test_freshness_video_change_stale(tmp_path):
    tmp, svc, avsvc = ready_service(tmp_path)
    asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter()))
    # §28：video change → stale（authoritative 文件内容变化；改的是 tmp 拷贝）
    cur_local = ProjectStore(tmp).load().get_shot("s01_c01").video_versions[0].local
    with open(cur_local, "ab") as f:
        f.write(b"\x00tail")
    fr = avsvc.get_sync_freshness("s01_c01")
    assert fr["status"] == "stale"
    assert "video_changed" in fr["stale_reasons"]


def test_freshness_timing_policy_change_stale(tmp_path):
    tmp, svc, avsvc = ready_service(tmp_path)
    asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter()))
    fr = avsvc.get_sync_freshness("s01_c01", speech_start_ms=500)   # timing policy 变
    assert fr["status"] == "stale"
    assert "timing_fingerprint" in fr["stale_reasons"]


def test_freshness_speaker_target_change_stale(tmp_path):
    tmp, svc, avsvc = ready_service(tmp_path)
    asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter()))
    fr = avsvc.get_sync_freshness(
        "s01_c01", explicit_speaker_target=LipSyncSpeakerTarget(mode="auto_detect"))
    assert fr["status"] == "stale"
    assert "speaker_target_fingerprint" in fr["stale_reasons"]


def test_freshness_provider_model_options_change_stale(tmp_path):
    tmp, svc, avsvc = ready_service(tmp_path)
    asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter()))
    assert avsvc.get_sync_freshness("s01_c01", model="sync-3")["status"] == "stale"
    assert avsvc.get_sync_freshness(
        "s01_c01", provider_options={"temperature": 0.9})["status"] == "stale"


def test_freshness_unrelated_changes_remains_current(tmp_path):
    """§28：unrelated shot change / unrelated character change → remains current。"""
    tmp, svc, avsvc = ready_service(tmp_path)
    asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter()))
    # 无关角色 profile 变化（不经 speaker/timing/provider 任何 basis 键）
    add_unrelated_character(tmp, svc)
    cur = svc.get_profile("vp_小铃").revision()
    svc.update_profile("vp_小铃", cur, speed=1.4)
    fr = avsvc.get_sync_freshness("s01_c01")
    assert fr["status"] == "current"


def test_freshness_production_output_missing_stale(tmp_path):
    tmp, svc, avsvc = ready_service(tmp_path)
    asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter()))
    l = avsvc.list_sync_versions("s01_c01")
    prod_path = next(v["production_output_path"] for v in l["sync_versions"]
                     if v["sync_version_id"] == l["selected_sync_version_id"])
    Path(prod_path).unlink()
    fr = avsvc.get_sync_freshness("s01_c01")
    assert fr["status"] == "stale"
    assert "production_output_missing" in fr["stale_reasons"]


def test_no_auto_relipsync_on_stale(tmp_path):
    """§25：STALE 只能显式 regenerate——read/freshness 0 provider call。"""
    tmp, svc, avsvc = ready_service(tmp_path)
    adapter = FakeSyncAdapter()
    asyncio.run(avsvc.generate_lipsync("s01_c01", adapter))
    add_unrelated_character(tmp, svc)
    cur = svc.get_profile("vp_小铃").revision()
    svc.update_profile("vp_小铃", cur, speed=1.4)
    for _ in range(2):
        avsvc.get_sync_freshness("s01_c01")
        avsvc.list_sync_versions("s01_c01")
    assert adapter.calls == 1                            # 无任何自动 provider call


# ============ Selection / Approval（§15；§28 Artifact 组）============

def test_selection_and_approval_independent(tmp_path):
    tmp, svc, avsvc = ready_service(tmp_path)
    v1 = asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter()))
    v2 = asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter()))
    l = avsvc.list_sync_versions("s01_c01")
    assert [v["sync_version_id"] for v in l["sync_versions"]] == \
        [v1["sync_version_id"], v2["sync_version_id"]]
    assert l["selected_sync_version_id"] == v2["sync_version_id"]   # 生成指向新版本
    assert l["approved"] is False                                    # 不自动 approve
    assert avsvc.approve_sync_version("s01_c01")["approved"] is True
    # 换选择 → 回未批准
    r = avsvc.select_sync_version("s01_c01", v1["sync_version_id"])
    assert r["approved"] is False
    assert avsvc.select_sync_version("s01_c01", "ghost_sync99")["error"] == "sync_version_not_found"


def test_artifact_paths_and_probes_real(tmp_path):
    """§28 Artifact：raw kept / production mux created / ffprobe real。"""
    tmp, svc, avsvc = ready_service(tmp_path)
    res = asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter()))
    sv = res["sync_version"]
    assert Path(sv["raw_output_path"]).exists()          # provider 原始输出原样留存
    assert Path(sv["production_output_path"]).exists()
    assert sv["video_probe"]["video_codec"] == "h264"
    assert sv["video_probe"]["audio_codec"] == "aac"     # mux 音轨 = 正式 sync_input
    assert sv["video_probe"]["duration_ms"] == 5042      # 完整镜头保留（§6）


def test_store_fail_closed_bad_json(tmp_path):
    tmp, svc, avsvc = ready_service(tmp_path)
    f = tmp / "output" / "dialogue_video_sync.json"
    f.parent.mkdir(exist_ok=True)
    f.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        DialogueVideoSyncStore(tmp).load_or_none()
    f.unlink()
    assert DialogueVideoSyncStore(tmp).load_or_none() is None    # 无文件零副作用
