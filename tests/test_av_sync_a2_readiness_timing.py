# tests/test_av_sync_a2_readiness_timing.py
# Audio V1 Phase A2 §28：Truth / readiness + Timing + Speaker（16.md §4/§5/§6/§7/§11）。
# 全 Fake Provider / 真实 ffprobe（本地真 mp4 拷贝），零联网。
import asyncio
import io
import shutil
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.models import (
    Character, Project, Scene, Shot, ShotStatus, VideoVersion,
)
from src.core.store import ProjectStore
from src.audio.service import VoiceService
from src.audio.av_sync.av_readiness import compute_av_sync_readiness
from src.audio.av_sync.av_truth import resolve_video_truth
from src.audio.av_sync.models import (
    LipSyncSpeakerTarget,
    build_timing_plan,
    resolve_speaker_target,
)

# 42.md §4.6：权威视频夹具改为仓库内常驻合成 mp4（ffmpeg testsrc 2s，真实可解码）。
# 原值指向 projects/AGNES_GoldenPath_H 的真实成片——该项目 09-24 清理删除后 39 项
# AV-sync/lipsync 测试 FileNotFoundError 挂账（41.md 「零净增」掩盖的环境性失败）。
# A2 路径（外挂 lip-sync，已搁置留档为 fallback）测试只需要一个可解码真实 mp4，
# 内容无关——合成夹具恢复环境条件且不再依赖运行时项目数据。
REAL_MP4 = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "a2_authoritative_video.mp4"


def wav_bytes(seconds=1.0, rate=48000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", 6000 if ((i // 40) % 2) == 0 else 0)
                               for i in range(int(seconds * rate))))
    return buf.getvalue()


class FakeVoiceAdapter:
    """A1 侧 fake（synthesize）。1s 48k WAV → audio 短于 5.04s 视频 = Case A。"""
    async def synthesize(self, pack):
        return {"audio": wav_bytes(1.0), "provider_extra_info": {"usage_characters": 7},
                "request_snapshot": {}, "latency_ms": 1, "word_timestamps": [],
                "word_timestamps_note": "word_timestamps_unavailable"}


def make_env(tmp_path, *, characters=("阿默",), second_shot_multi=False):
    """临时项目：authoritative video（真实 mp4 拷贝）+ A1 audio selected+approved。"""
    shutil.copy(REAL_MP4, tmp_path / "fake_video.mp4")
    p = Project(name=tmp_path.name,
                characters=[Character(name=c) for c in set(characters)])
    vv = VideoVersion(version_id="v_test", status=ShotStatus.DONE.value,
                      local=str(tmp_path / "fake_video.mp4"), url="https://example.com/v.mp4")
    p.scenes.append(Scene(scene_id="s01", characters=list(characters), shots=[
        Shot(shot_id="s01_c01", scene_id="s01", index=1,
             characters=list(characters), dialogue="我们现在出发。",
             video_versions=[vv], final_video="v_test")]))
    if second_shot_multi:
        p.scenes[0].shots.append(Shot(
            shot_id="s01_c02", scene_id="s01", index=2,
            characters=["阿默", "小铃"], dialogue="一起走吗？",
            video_versions=[VideoVersion(version_id="v_test2", status=ShotStatus.DONE.value,
                                         local=str(tmp_path / "fake_video.mp4"))],
            final_video="v_test2"))
    ProjectStore(tmp_path).save(p)
    svc = VoiceService(tmp_path)
    for c in set(characters):
        r = svc.create_profile(c, "male-qn-qingse")
        svc.approve_profile(f"vp_{c}", r["revision"])
    asyncio.run(svc.generate("s01_c01", FakeVoiceAdapter()))
    svc.approve_selected("s01_c01")
    return tmp_path, svc


# ============ readiness（§4）============

def test_readiness_ready_case_a(tmp_path):
    tmp, svc = make_env(tmp_path)
    project = ProjectStore(tmp).load()
    rd = compute_av_sync_readiness(project, svc, "s01_c01")
    assert rd["status"] == "ready"
    assert rd["speaker"] == "阿默"
    assert rd["speaker_target"]["mode"] == "single_visible_speaker"
    assert rd["timing_plan"]["duration_relation"] == "audio_fits"
    assert rd["video"]["duration_ms"] == 5042           # ffprobe 实测真 mp4


def test_readiness_missing_video(tmp_path):
    tmp, svc = make_env(tmp_path)
    project = ProjectStore(tmp).load()
    project.get_shot("s01_c01").final_video = None
    rd = compute_av_sync_readiness(project, svc, "s01_c01")
    assert rd["status"] == "missing_video"


def test_readiness_video_not_ready_local_missing(tmp_path):
    tmp, svc = make_env(tmp_path)
    project = ProjectStore(tmp).load()
    project.get_shot("s01_c01").video_versions[0].local = str(tmp / "ghost.mp4")
    rd = compute_av_sync_readiness(project, svc, "s01_c01")
    assert rd["status"] == "video_not_ready"


def test_readiness_audio_stale(tmp_path):
    """§25：dialogue 变化 → A1 audio stale → lip-sync 生成门阻断（先修 audio）。"""
    tmp, svc = make_env(tmp_path)
    ProjectStore(tmp).update_shot("s01_c01", lambda sh: setattr(sh, "dialogue", "我们马上出发！"))
    project = ProjectStore(tmp).load()
    rd = compute_av_sync_readiness(project, svc, "s01_c01")
    assert rd["status"] == "audio_stale"


def test_readiness_audio_not_approved(tmp_path):
    tmp, svc = make_env(tmp_path)
    # 重新生成产生新版本 → 指针迁移 → 回到未批准
    asyncio.run(svc.generate("s01_c01", FakeVoiceAdapter()))
    project = ProjectStore(tmp).load()
    rd = compute_av_sync_readiness(project, svc, "s01_c01")
    assert rd["status"] == "audio_not_approved"


def test_readiness_no_speaker(tmp_path):
    tmp, svc = make_env(tmp_path)
    ProjectStore(tmp).update_shot("s01_c01", lambda sh: setattr(sh, "characters", []))
    project = ProjectStore(tmp).load()
    rd = compute_av_sync_readiness(project, svc, "s01_c01")
    assert rd["status"] == "no_speaker"


def test_readiness_multi_face_target_required(tmp_path):
    """§11：多角色镜 speaker 已显式裁决（A1 门在前），audio 已就绪，
    但画面目标未裁决 → fail closed。"""
    tmp, svc = make_env(tmp_path, second_shot_multi=True)
    asyncio.run(svc.generate("s01_c02", FakeVoiceAdapter(), speaker="阿默"))
    svc.approve_selected("s01_c02")
    project = ProjectStore(tmp).load()
    rd = compute_av_sync_readiness(project, svc, "s01_c02", speaker="阿默")
    assert rd["status"] == "multi_face_target_required"


def test_readiness_multi_char_ambiguous_speaker_first(tmp_path):
    """多角色镜连说话人都未裁决时，A1 speaker 门先触发（分层语义）。"""
    tmp, svc = make_env(tmp_path, second_shot_multi=True)
    project = ProjectStore(tmp).load()
    rd = compute_av_sync_readiness(project, svc, "s01_c02")
    assert rd["status"] == "ambiguous_speaker"


def test_readiness_video_too_short(tmp_path):
    """§7：audio > video → fail closed + required 信号（本 env 用 8s 音频 > 5.04s）。"""
    tmp, svc = make_env(tmp_path)
    # 直接造一个 8s approved audio：改 shot dialogue 再生成会更绕——
    # 这里用 timing plan 层验证（§32：audio>video 属本地确定性 policy，
    # 不需要真烧 provider；plan 级断言见下方 timing 组）。
    plan = build_timing_plan(dialogue_id="s01_c01", shot_id="s01_c01",
                             audio_version_id="a", audio_duration_ms=8000,
                             video_artifact_id="v", video_duration_ms=5042)
    assert plan.duration_relation == "video_too_short"
    assert plan.required_video_duration_ms == 8000
    # readiness 集成路径：用 speech_start 把 audio 推出视频外
    project = ProjectStore(tmp).load()
    rd = compute_av_sync_readiness(project, svc, "s01_c01", speech_start_ms=4500)
    assert rd["status"] == "video_too_short"
    assert rd["detail"].startswith("video 5042ms 不足")


def test_readiness_pure_read_zero_side_effect(tmp_path):
    tmp, svc = make_env(tmp_path)
    outdir = tmp / "output"
    before = sorted(str(f.relative_to(outdir)) for f in outdir.rglob("*") if f.is_file())
    for _ in range(3):
        compute_av_sync_readiness(ProjectStore(tmp).load(), svc, "s01_c01")
    after = sorted(str(f.relative_to(outdir)) for f in outdir.rglob("*") if f.is_file())
    assert before == after                              # 连续调用项目文件零变化


# ============ timing（§5/§6/§7/§9；§28 Timing 组）============

def test_timing_video_5s_audio_3s_padded():
    """§28：video 5s / audio 3s → padded sync input = 5s（完整镜头保留）。"""
    plan = build_timing_plan(dialogue_id="d", shot_id="s", audio_version_id="a",
                             audio_duration_ms=3000, video_artifact_id="v",
                             video_duration_ms=5000)
    assert plan.duration_relation == "audio_fits"
    assert plan.tail_silence_ms == 2000
    assert plan.speech_end_ms == 3000
    assert plan.required_video_duration_ms == 3000
    assert plan.playback_ratio == 1.0


def test_timing_lead_in_shifts_timestamps():
    """§28：lead-in offset → timestamps correctly shifted。"""
    plan = build_timing_plan(dialogue_id="d", shot_id="s", audio_version_id="a",
                             audio_duration_ms=3000, video_artifact_id="v",
                             video_duration_ms=5000, speech_start_ms=700)
    assert plan.speech_start_ms == 700
    assert plan.speech_end_ms == 3700
    assert plan.lead_in_ms == 700
    assert plan.tail_silence_ms == 1300


def test_timing_audio_longer_video_too_short_no_truncation():
    """§7：绝不自动裁对白/loop/bounce/加速——fail closed + required。"""
    plan = build_timing_plan(dialogue_id="d", shot_id="s", audio_version_id="a",
                             audio_duration_ms=5200, video_artifact_id="v",
                             video_duration_ms=4000)
    assert plan.duration_relation == "video_too_short"
    assert plan.required_video_duration_ms == 5200
    assert plan.tail_silence_ms == 0


def test_timing_remap_only_explicit_and_ratio_recorded():
    plan = build_timing_plan(dialogue_id="d", shot_id="s", audio_version_id="a",
                             audio_duration_ms=5200, video_artifact_id="v",
                             video_duration_ms=4000, adaptation_mode="remap_video")
    assert plan.adaptation_mode == "remap_video"
    assert plan.playback_ratio == round(4000 / 5200, 6)
    # fits 时显式 remap 回落 pad（remap 无意义）
    plan2 = build_timing_plan(dialogue_id="d", shot_id="s", audio_version_id="a",
                              audio_duration_ms=3000, video_artifact_id="v",
                              video_duration_ms=5000, adaptation_mode="remap_video")
    assert plan2.adaptation_mode == "pad_audio" and plan2.playback_ratio == 1.0


def test_timing_deterministic():
    kw = dict(dialogue_id="d", shot_id="s", audio_version_id="a",
              audio_duration_ms=3000, video_artifact_id="v", video_duration_ms=5000)
    assert build_timing_plan(**kw).to_dict() == build_timing_plan(**kw).to_dict()


# ============ speaker target（§10/§11；§28 Speaker 组）============

def test_speaker_single_auto_allowed():
    t, code = resolve_speaker_target(["阿默"])
    assert t.mode == "single_visible_speaker"


def test_speaker_multi_no_target_blocked():
    """§11：多人物镜不猜——绝不静默同步「画面里最大的脸」。"""
    t, code = resolve_speaker_target(["阿默", "小铃"])
    assert t is None and code == "multi_face_target_required"


def test_speaker_manual_target_accepted():
    t, code = resolve_speaker_target(
        ["阿默", "小铃"],
        LipSyncSpeakerTarget(mode="manual_target", frame_number=10,
                             coordinates={"x": 640, "y": 350}))
    assert t.mode == "manual_target" and t.coordinates == {"x": 640, "y": 350}


def test_speaker_auto_detect_explicit_only():
    t, _ = resolve_speaker_target(["阿默", "小铃"],
                                  LipSyncSpeakerTarget(mode="auto_detect"))
    assert t.mode == "auto_detect"


def test_speaker_name_never_becomes_coordinates():
    """§11：character_name 绝不伪装成 face coordinates（identity 纪律）。"""
    t, _ = resolve_speaker_target(["阿默"])
    assert t.coordinates is None and t.frame_number is None


# ============ video truth（§1）============

def test_video_truth_resolution_from_real_file(tmp_path):
    shutil.copy(REAL_MP4, tmp_path / "fake_video.mp4")
    vv = VideoVersion(version_id="v1", status=ShotStatus.DONE.value,
                      local=str(tmp_path / "fake_video.mp4"), url="https://x/v.mp4")
    shot = Shot(shot_id="s01_c01", scene_id="s01", index=1,
                video_versions=[vv], final_video="v1")
    truth = resolve_video_truth(shot)
    assert truth["artifact_id"] == "v1"
    assert truth["probe"]["duration_ms"] == 5042
    assert truth["probe"]["video_codec"] == "h264"
    assert truth["probe"]["has_audio"] is True          # Agnes 混合音轨（§23：mux 时替换）
    assert len(truth["fingerprint"]) == 64


def test_video_truth_no_pointer_returns_none():
    shot = Shot(shot_id="s01_c01", scene_id="s01", index=1)
    assert resolve_video_truth(shot) is None            # 绝不 glob/mtime 猜测
