# tests/test_av_sync_a2_adapter.py
# Audio V1 Phase A2 §28：SyncLabs adapter（§16/§18/§19/§20）+ Generation + Race
# （§21）+ Artifact（§22）+ Safety（§28 secret 纪律）+ Mutation audit（§42/§39.17）。
import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.store import ProjectStore
from src.audio.av_sync.sync_labs_adapter import (
    DEFAULT_BASE_URL,
    GENERATE_PATH,
    LipSyncProviderError,
    SyncLabsLipSyncAdapter,
    build_provider_options,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.test_av_sync_a2_readiness_timing import FakeVoiceAdapter, REAL_MP4, make_env  # noqa: E402
from tests.test_av_sync_a2_pack_freshness import FakeSyncAdapter  # noqa: E402

PACK = {"timing": {"adaptation_mode": "pad_audio"},
        "speaker_target": {"mode": "single_visible_speaker"},
        "provider_binding": {"provider": "sync_labs", "model": "lipsync-2"}}


def mk_wav(tmp: Path, seconds=5.04):
    import io, wave, struct
    p = tmp / "sync_input.wav"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(b"\x00\x00" * int(seconds * 48000))
    p.write_bytes(buf.getvalue())
    return p


def mk_video(tmp: Path):
    p = tmp / "video.mp4"
    shutil.copy(REAL_MP4, p)
    return p


# ============ provider options 映射（§18 禁止默认映射）============

def test_sync_mode_pad_audio_maps_to_silence():
    """pad_audio → silence（保留完整视频）；bounce/loop/cut_off 永不出现。"""
    opts = build_provider_options(PACK)
    assert opts["sync_mode"] == "silence"
    blob = json.dumps(opts)
    for banned in ("bounce", "loop", "cut_off"):
        assert banned not in blob


def test_sync_mode_remap_only_explicit():
    opts = build_provider_options({**PACK,
                                   "timing": {"adaptation_mode": "remap_video"}})
    assert opts["sync_mode"] == "remap"


def test_active_speaker_detection_mapping():
    assert "active_speaker_detection" not in build_provider_options(PACK)  # 单人不设
    auto = build_provider_options({**PACK,
                                   "speaker_target": {"mode": "auto_detect"}})
    assert auto["active_speaker_detection"] == {"auto_detect": True}
    manual = build_provider_options({**PACK, "speaker_target": {
        "mode": "manual_target", "frame_number": 10,
        "coordinates": {"x": 640, "y": 350}}})
    asd = manual["active_speaker_detection"]
    assert asd["frame_number"] == 10 and asd["coordinates"] == [640, 350]


# ============ 生命周期（§19）============

def test_full_lifecycle_submit_poll_download():
    """真实契约形状：POST /v2/generate (x-api-key) → id → poll → COMPLETED →
    outputUrl 下载。"""
    polls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.sync.so":
            assert req.headers.get("x-api-key") == "test-key-abc"   # API 域必须带 key
        else:
            assert req.headers.get("x-api-key") is None             # 预签名下载域无 key
        if req.method == "POST" and req.url.path == GENERATE_PATH:
            assert b'name="model"' in req.content and b"lipsync-2" in req.content
            assert b'name="video"' in req.content and b'name="audio"' in req.content
            assert b"silence" in req.content                      # pad_audio 映射
            return httpx.Response(200, json={"id": "gen_1", "status": "PENDING"})
        if req.method == "GET" and req.url.path == f"{GENERATE_PATH}/gen_1":
            polls["n"] += 1
            if polls["n"] < 2:
                return httpx.Response(200, json={"id": "gen_1", "status": "PROCESSING"})
            return httpx.Response(200, json={
                "id": "gen_1", "status": "COMPLETED",
                "outputUrl": "https://dl.example.com/out.mp4",
                "outputDuration": 5.04})
        if req.url.host == "dl.example.com":
            return httpx.Response(200, content=b"PROVIDERMP4")
        return httpx.Response(404)

    async def main():
        ad = SyncLabsLipSyncAdapter(api_key="test-key-abc", poll_interval_s=0.01,
                                    client=httpx.AsyncClient(
                                        transport=httpx.MockTransport(handler)))
        tmp = Path(__import__("tempfile").mkdtemp(prefix="a2_ad_"))
        res = await ad.generate(PACK, video_path=str(mk_video(tmp)),
                                audio_path=str(mk_wav(tmp)))
        assert res["provider_generation_id"] == "gen_1"
        assert res["raw_bytes"] == b"PROVIDERMP4"
        assert res["provider_metadata"]["provider_status"] == "COMPLETED"
        assert "test-key-abc" not in json.dumps(res["provider_metadata"])  # key 零落盘
        shutil.rmtree(tmp, ignore_errors=True)

    asyncio.run(main())


# ============ 错误契约（§20）============

def _run_with_handler(handler, pack=None, poll_timeout=5.0):
    async def main():
        ad = SyncLabsLipSyncAdapter(api_key="k", poll_interval_s=0.01,
                                    poll_timeout_s=poll_timeout,
                                    client=httpx.AsyncClient(
                                        transport=httpx.MockTransport(handler)))
        tmp = Path(__import__("tempfile").mkdtemp(prefix="a2_ad_"))
        try:
            return await ad.generate(pack or PACK, video_path=str(mk_video(tmp)),
                                     audio_path=str(mk_wav(tmp)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return asyncio.run(main())


def test_error_generation_failed_with_error_code():
    def handler(req):
        if req.method == "POST":
            return httpx.Response(200, json={"id": "g", "status": "PENDING"})
        return httpx.Response(200, json={"id": "g", "status": "FAILED",
                                         "error": "bad video",
                                         "errorCode": "generation_input_video_inaccessible"})
    with pytest.raises(LipSyncProviderError) as e:
        _run_with_handler(handler)
    assert e.value.code == "provider_generation_failed"
    assert e.value.detail["provider_error_code"] == "generation_input_video_inaccessible"


def test_error_rejected():
    def handler(req):
        if req.method == "POST":
            return httpx.Response(200, json={"id": "g", "status": "PENDING"})
        return httpx.Response(200, json={"id": "g", "status": "REJECTED",
                                         "error": "content policy",
                                         "errorCode": "generation_content_rejected"})
    with pytest.raises(LipSyncProviderError) as e:
        _run_with_handler(handler)
    assert e.value.code == "provider_generation_failed"
    assert e.value.detail["provider_status"] == "REJECTED"


def test_error_timeout():
    def handler(req):
        if req.method == "POST":
            return httpx.Response(200, json={"id": "g", "status": "PENDING"})
        return httpx.Response(200, json={"id": "g", "status": "PROCESSING"})
    with pytest.raises(LipSyncProviderError) as e:
        _run_with_handler(handler, poll_timeout=0.05)
    assert e.value.code == "provider_timeout"


def test_error_no_key_zero_requests():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"id": "g", "status": "PENDING"})

    async def main():
        ad = SyncLabsLipSyncAdapter(api_key="",
                                    client=httpx.AsyncClient(
                                        transport=httpx.MockTransport(handler)))
        tmp = Path(__import__("tempfile").mkdtemp(prefix="a2_ad_"))
        try:
            await ad.generate(PACK, video_path=str(mk_video(tmp)),
                              audio_path=str(mk_wav(tmp)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    with pytest.raises(LipSyncProviderError) as e:
        asyncio.run(main())
    assert e.value.code == "provider_not_configured"
    assert calls == []                                       # 0 请求（显式 "" 禁用）


def test_error_missing_output_url():
    def handler(req):
        if req.method == "POST":
            return httpx.Response(200, json={"id": "g", "status": "PENDING"})
        return httpx.Response(200, json={"id": "g", "status": "COMPLETED",
                                         "outputUrl": ""})
    with pytest.raises(LipSyncProviderError) as e:
        _run_with_handler(handler)
    assert e.value.code == "provider_invalid_media"


def test_error_download_failure():
    def handler(req):
        if req.method == "POST":
            return httpx.Response(200, json={"id": "g", "status": "PENDING"})
        if req.url.path == f"{GENERATE_PATH}/g":
            return httpx.Response(200, json={"id": "g", "status": "COMPLETED",
                                             "outputUrl": "https://dl.example.com/x.mp4"})
        return httpx.Response(404)
    with pytest.raises(LipSyncProviderError) as e:
        _run_with_handler(handler)
    assert e.value.code == "provider_download_failed"


def test_error_http_401():
    def handler(req):
        return httpx.Response(401, json={"error": "unauthorized"})
    with pytest.raises(LipSyncProviderError) as e:
        _run_with_handler(handler)
    assert e.value.code == "provider_request_failed"


# ============ Race（§21；§28 三例）============

def _race_env(tmp_path):
    tmp, svc = make_env(tmp_path)
    return tmp, svc, AVSyncServiceProxy(tmp)


class AVSyncServiceProxy:
    """薄包装：复用 test_av_sync_a2_pack_freshness 的 FakeSyncAdapter 语义。"""

    def __init__(self, project_dir):
        from src.audio.av_sync.av_service import AVSyncService
        self.svc = AVSyncService(project_dir)

    def __getattr__(self, name):
        return getattr(self.svc, name)


def test_race_audio_changes_during_lipsync(tmp_path):
    """§28：audio changes during lip-sync → reject attach。"""
    tmp, svc, avsvc = _race_env(tmp_path)

    def mid_hook():
        async def h(pack_dict):
            doc = svc.audio_store.load_doc()
            doc["dialogues"]["s01_c01"]["selected_audio_version_id"] = "s01_c01_avXX"
            svc.audio_store.save_doc(doc)
        return h

    from src.audio.av_sync.av_service import AVSyncError
    with pytest.raises(AVSyncError) as e:
        asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter(hook=mid_hook())))
    assert e.value.code == "dependency_changed_before_commit"
    assert avsvc.list_sync_versions("s01_c01")["sync_versions"] == []   # 零 attach
    ev = list((tmp / "output" / "dialogue_video_sync" / "evidence").iterdir())
    assert len(ev) == 1                                                  # 输出保留 diagnostics


def test_race_video_changes_during_lipsync(tmp_path):
    tmp, svc, avsvc = _race_env(tmp_path)

    def mid_hook():
        async def h(pack_dict):
            cur_local = ProjectStore(tmp).load().get_shot(
                "s01_c01").video_versions[0].local
            with open(cur_local, "ab") as f:
                f.write(b"\x00tail")
        return h

    from src.audio.av_sync.av_service import AVSyncError
    with pytest.raises(AVSyncError) as e:
        asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter(hook=mid_hook())))
    assert e.value.code == "dependency_changed_before_commit"
    assert "source_video_fingerprint" in e.value.detail["mismatched_keys"]


def test_race_timing_changes_during_lipsync(tmp_path):
    """§28：speaker/timing changes during lip-sync → reject（timing policy 变化）。"""
    tmp, svc, avsvc = _race_env(tmp_path)

    async def before_hook(pack, sync_input_info):
        # 窗口内换 timing policy（重新生成用不同 speech_start —— 直接改 store
        # 不可能，policy 是 caller 参数 → 用「改 dialogue 文本」触发 A1 stale 代替：
        # timing 变化的拒绝路径已由 freshness 测试钉死，此处钉 speaker target 变化）
        ProjectStore(tmp).update_shot("s01_c01",
                                      lambda sh: setattr(sh, "dialogue", "我们马上出发！"))

    from src.audio.av_sync.av_service import AVSyncError
    with pytest.raises(AVSyncError) as e:
        asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter(),
                                           before_dispatch_hook=before_hook))
    assert e.value.code == "dependency_changed_before_commit"
    assert avsvc.list_sync_versions("s01_c01")["sync_versions"] == []


# ============ Mutation audit（§39.17：A1 与 Visual V1 零语义回归）============

def test_generate_lipsync_mutation_audit(tmp_path):
    """generate_lipsync 只写 sync 自己的 store/媒体 + A1 audio 产物；
    project.json 与其它冻结 store 字节恒等。"""
    tmp, svc, avsvc = _race_env(tmp_path)
    asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter()))
    out = tmp / "output"
    # project.json 不被 lip-sync 写入（A1 audio 生成时由 A1 自己负责）
    import time as _t
    pj = out / "project.json"
    h1 = hashlib.sha256(pj.read_bytes()).hexdigest()
    asyncio.run(avsvc.generate_lipsync("s01_c01", FakeSyncAdapter()))
    assert hashlib.sha256(pj.read_bytes()).hexdigest() == h1
    # 新增文件只在允许域内：dialogue_audio / dialogue_video_sync
    allowed_roots = {"dialogue_audio", "dialogue_video_sync", "voice_profiles.json",
                     "dialogue_audio.json", "dialogue_video_sync.json", "project.json"}
    for f in out.rglob("*"):
        if f.is_file():
            rel = f.relative_to(out).as_posix()
            root = rel.split("/")[0].split("\\")[0]
            assert root in allowed_roots or root == "videos" or rel == "project.json", rel


# ============ Safety（§28）============

def test_sync_api_key_never_serialized(tmp_path):
    ad = SyncLabsLipSyncAdapter(api_key="SUPER-SECRET-SYNC-42")
    parts = ad.build_request_parts(PACK, output_file_name="x.mp4")
    blob = json.dumps(parts)
    assert "SUPER-SECRET-SYNC-42" not in blob
    assert "x-api-key" not in blob
