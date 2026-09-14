# tests/test_voice_a1_adapter.py
# Audio V1 Phase A1 §28：MiniMaxVoiceAdapter（§13/§14/§25）。
# 全部 Fake（httpx.MockTransport），不联网；secret 纪律 + request mapping + 错误契约。
import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.audio.minimax_adapter import (
    DEFAULT_BASE_URL,
    T2A_PATH,
    MiniMaxVoiceAdapter,
    VoiceProviderError,
)
from src.audio.models import VoiceGenerationPack


def make_pack(text="我们现在出发。", voice_id="male-qn-qingse",
              model="speech-2.8-turbo", speed=1.0, vol=1.0, pitch=0,
              language_boost="Chinese") -> VoiceGenerationPack:
    return VoiceGenerationPack(
        dialogue_id="s01_c01", dialogue_fingerprint="deadbeef",
        speaker_character_name="小明", text=text, language_boost=language_boost,
        voice_profile_id="vp_小明", voice_profile_revision="cafe1234",
        provider_binding={"provider": "minimax", "provider_voice_id": voice_id,
                          "default_model": model},
        synthesis_defaults={"speed": speed, "vol": vol, "pitch": pitch},
        generation_basis={"schema": "voice_generation_basis_v1"})


def t2a_response(payload, status=200):
    return httpx.Response(status, json=payload,
                          request=httpx.Request("POST", DEFAULT_BASE_URL + T2A_PATH))


OK_PAYLOAD = {"data": {"audio": "deadbeef" * 4},
              "extra_info": {"usage_characters": 7, "audio_length": 1000,
                             "audio_sample_rate": 32000},
              "base_resp": {"status_code": 0, "status_msg": "success"}}


def make_adapter(handler, api_key="test-key-123") -> MiniMaxVoiceAdapter:
    transport = httpx.MockTransport(handler)
    return MiniMaxVoiceAdapter(api_key=api_key,
                               client=httpx.AsyncClient(transport=transport))


# ============ request mapping（§14）============

def test_build_request_mapping():
    req = MiniMaxVoiceAdapter(api_key="k").build_request(make_pack())
    assert req["model"] == "speech-2.8-turbo"                  # §0 正式默认
    assert req["text"] == "我们现在出发。"                       # §15 原样
    assert req["stream"] is False
    assert req["voice_setting"] == {"voice_id": "male-qn-qingse",
                                    "speed": 1.0, "vol": 1.0, "pitch": 0}
    assert req["audio_setting"] == {"sample_rate": 32000, "bitrate": 128000,
                                    "format": "mp3", "channel": 1}
    assert req["language_boost"] == "Chinese"
    assert req["subtitle_enable"] is True and req["subtitle_type"] == "word"


def test_build_request_no_language_boost_omits_key():
    req = MiniMaxVoiceAdapter(api_key="k").build_request(
        make_pack(language_boost=""))
    assert "language_boost" not in req


def test_build_request_snapshot_never_contains_secret():
    adapter = MiniMaxVoiceAdapter(api_key="SUPER-SECRET-KEY-42")
    snap = adapter.build_request(make_pack())
    assert "SUPER-SECRET-KEY-42" not in json.dumps(snap)       # 落盘前构造性脱敏
    assert "Authorization" not in json.dumps(snap)


def test_build_request_rejects_out_of_range_settings():
    adapter = MiniMaxVoiceAdapter(api_key="k")
    for field, value in (("speed", 9.9), ("vol", 99.0), ("pitch", 99)):
        pack = make_pack(**{field: value})
        with pytest.raises(VoiceProviderError) as e:
            adapter.build_request(pack)
        assert e.value.code == "provider_request_failed"


# ============ 成功调用 / 解码 ============

def test_synthesize_success_hex_decode():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return t2a_response(OK_PAYLOAD)

    adapter = make_adapter(handler)
    result = asyncio.run(adapter.synthesize(make_pack()))
    assert seen["url"] == DEFAULT_BASE_URL + T2A_PATH          # api.minimax.cn/v1/t2a_v2
    assert seen["auth"] == "Bearer test-key-123"               # key 只进 header
    assert seen["body"]["model"] == "speech-2.8-turbo"
    assert bytes.fromhex("deadbeef" * 4) == result["audio"]    # hex 解码正确
    assert result["provider_extra_info"]["usage_characters"] == 7
    assert result["latency_ms"] >= 0
    assert result["word_timestamps"] == []
    assert result["word_timestamps_note"] == "word_timestamps_unavailable"  # §14
    # request_snapshot 可安全落盘（无 key）
    assert "test-key-123" not in json.dumps(result["request_snapshot"])


# ============ 错误契约（§25）============

def test_error_quota_exceeded():
    def handler(request):
        return t2a_response({"base_resp": {"status_code": 1008,
                                           "status_msg": "insufficient balance"}})
    adapter = make_adapter(handler)
    with pytest.raises(VoiceProviderError) as e:
        asyncio.run(adapter.synthesize(make_pack()))
    assert e.value.code == "provider_quota_exceeded"
    assert e.value.detail["provider_status_code"] == 1008
    assert "test-key-123" not in str(e.value)
    assert "test-key-123" not in json.dumps(e.value.detail)


def test_error_invalid_voice():
    def handler(request):
        return t2a_response({"base_resp": {"status_code": 2049,
                                           "status_msg": "voice not exist"}})
    adapter = make_adapter(handler)
    with pytest.raises(VoiceProviderError) as e:
        asyncio.run(adapter.synthesize(make_pack()))
    assert e.value.code == "provider_invalid_voice"


def test_error_unknown_code_fallback_generic():
    def handler(request):
        return t2a_response({"base_resp": {"status_code": 9999,
                                           "status_msg": "mystery"}})
    adapter = make_adapter(handler)
    with pytest.raises(VoiceProviderError) as e:
        asyncio.run(adapter.synthesize(make_pack()))
    assert e.value.code == "provider_request_failed"           # 未命中映射 → 通用
    assert e.value.detail["provider_status_code"] == 9999      # raw 保留


def test_error_empty_audio():
    def handler(request):
        return t2a_response({"data": {"audio": ""},
                             "base_resp": {"status_code": 0}})
    adapter = make_adapter(handler)
    with pytest.raises(VoiceProviderError) as e:
        asyncio.run(adapter.synthesize(make_pack()))
    assert e.value.code == "audio_decode_failed"


def test_error_bad_hex():
    def handler(request):
        return t2a_response({"data": {"audio": "zzz-not-hex"},
                             "base_resp": {"status_code": 0}})
    adapter = make_adapter(handler)
    with pytest.raises(VoiceProviderError) as e:
        asyncio.run(adapter.synthesize(make_pack()))
    assert e.value.code == "audio_decode_failed"


def test_error_missing_audio_field():
    def handler(request):
        return t2a_response({"data": {}, "base_resp": {"status_code": 0}})
    adapter = make_adapter(handler)
    with pytest.raises(VoiceProviderError) as e:
        asyncio.run(adapter.synthesize(make_pack()))
    assert e.value.code == "audio_decode_failed"


def test_error_network_failure_structured():
    def handler(request):
        raise httpx.ConnectError("connection refused")
    adapter = make_adapter(handler)
    with pytest.raises(VoiceProviderError) as e:
        asyncio.run(adapter.synthesize(make_pack()))
    assert e.value.code == "provider_request_failed"


def test_error_non_json_response():
    def handler(request):
        return httpx.Response(200, text="<html>gateway</html>",
                              request=httpx.Request("POST", DEFAULT_BASE_URL + T2A_PATH))
    adapter = make_adapter(handler)
    with pytest.raises(VoiceProviderError) as e:
        asyncio.run(adapter.synthesize(make_pack()))
    assert e.value.code == "provider_request_failed"


def test_provider_not_configured_zero_requests():
    """无 key → provider_not_configured，且 0 次请求。"""
    calls = []

    def handler(request):
        calls.append(request)
        return t2a_response(OK_PAYLOAD)

    adapter = make_adapter(handler, api_key="")
    with pytest.raises(VoiceProviderError) as e:
        asyncio.run(adapter.synthesize(make_pack()))
    assert e.value.code == "provider_not_configured"
    assert calls == []                                          # 0 请求


# ============ word timestamps（§14：有则存，无则 diagnostic 不失败）============

def test_word_timestamps_captured_when_present():
    payload = {**OK_PAYLOAD,
               "extra_info": {"usage_characters": 7,
                              "audio_word_timestamps":
                                  [{"text": "我们", "offset_ms": 0, "duration_ms": 200}]}}
    adapter = make_adapter(lambda request: t2a_response(payload))
    result = asyncio.run(adapter.synthesize(make_pack()))
    assert result["word_timestamps"][0]["text"] == "我们"
    assert result["word_timestamps_note"] == ""                 # 无 diagnostic


def test_sanitize_meta_strips_secret_like_keys():
    from src.audio.minimax_adapter import _sanitize_meta
    out = _sanitize_meta({"usage_characters": 7, "api_key": "x",
                          "access_token": "y", "Authorization": "z",
                          "note": "n" * 3000})
    assert "api_key" not in out and "access_token" not in out and "Authorization" not in out
    assert out["usage_characters"] == 7
    assert len(out["note"]) == 2001                             # 截断防超大 payload
