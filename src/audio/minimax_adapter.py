# src/audio/minimax_adapter.py
# Audio V1 — Phase A1：MiniMaxVoiceAdapter（15.md §12/§13/§14/§25）。
#
# Provider 事实（2026-09-14 真实冒烟 PASS，memory: minimax-tts-smoke）：
#   POST https://api.minimax.cn/v1/t2a_v2   （国内直连主接口；备用 api-bj.minimaxi.com 未动用）
#   Authorization: Bearer <MINIMAX_API_KEY>（.env；api_server 启动时注入 environ）
#   请求：model + text 必填；voice_setting{voice_id,speed,vol,pitch}；
#         audio_setting{sample_rate:32000,bitrate:128000,format:mp3,channel:1}
#   响应：data.audio = hex 编码（binascii.unhexlify 解码）；base_resp.status_code==0 为成功
#   模型：speech-2.8-turbo（正式默认，15.md §0；HD 不作默认）
#
# 🔴 secret 纪律（§13）：key 只进 Authorization header，绝不进 request snapshot /
#    JSON / 日志 / 报错 / traceback。build_request 输出可直接落盘（脱敏由构造保证）。
# 🔴 Text 原样（§15）：request.text = pack.text（canonical Dialogue 原样），Adapter
#    绝不 rewrite / summarize / translate / paraphrase / auto-correct。
# 🔴 provider 错误契约（§25）：不包装成普通 500 字符串；structured code + provider
#    status/msg（截断）+ 保留 raw status_code。
from __future__ import annotations

import binascii
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from .models import (
    VOICE_PITCH_MAX, VOICE_PITCH_MIN,
    VOICE_SPEED_MAX, VOICE_SPEED_MIN,
    VoiceGenerationPack,
)

DEFAULT_BASE_URL = "https://api.minimax.cn"   # 冒烟实测国内直连（勿改回 .com 国际站）
T2A_PATH = "/v1/t2a_v2"
DEFAULT_TIMEOUT_S = 60.0

# 内部请求 audio_setting（与冒烟同口径；不 hardcode「provider 永远返回这个」——
# 每次生成仍以 ffprobe 实测为准，15.md §17A）
REQUEST_AUDIO_SETTING = {"sample_rate": 32000, "bitrate": 128000,
                         "format": "mp3", "channel": 1}

# base_resp.status_code → structured error（§25）。MiniMax 未公开完整错误码表，
# 映射表按冒烟与公开文档 best-effort；未命中一律 provider_request_failed，
# raw status_code 始终保留在 detail.provider_status_code。
_PROVIDER_CODE_MAP = {
    1008: "provider_quota_exceeded",     # 余额不足
    2049: "provider_invalid_voice",      # voice_id 无效/不存在（文档口径）
    # Phase 44C 实测补充（F1 真机证据）：当前端点对不存在 voice 实际返回 2054
    # "voice id not exist"——映射到同一结构化错误，raw status_code 始终保留。
    2054: "provider_invalid_voice",
}
_SECRET_KEY_MARKS = ("key", "token", "secret", "authorization", "password")


class VoiceProviderError(Exception):
    """MiniMax 调用 structured 错误（§25）。
    code ∈ provider_not_configured / provider_request_failed /
    provider_invalid_voice / provider_quota_exceeded / audio_decode_failed；
    detail 携带 provider_status_code / provider_status_msg（截断）/ trace_id 等安全信息，
    绝不含 secret。"""

    def __init__(self, code: str, message: str, detail: Optional[dict] = None):
        self.code = code
        self.detail = dict(detail or {})
        super().__init__(f"[{code}] {message}")


def _sanitize_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """防御性脱敏：任何疑似 secret 的键直接丢弃（provider extra_info 正常不含，
    但落盘前不赌）。值统一截断防超大 payload 进 JSON。"""
    out: Dict[str, Any] = {}
    for k, v in (meta or {}).items():
        if any(mark in str(k).lower() for mark in _SECRET_KEY_MARKS):
            continue
        if isinstance(v, str) and len(v) > 2000:
            v = v[:2000] + "…"
        out[str(k)] = v
    return out


class MiniMaxVoiceAdapter:
    """speech-2.8-turbo 适配器（A1 唯一 provider；不做抽象 framework，15.md §27）。"""

    def __init__(self, api_key: Optional[str] = None,
                 base_url: Optional[str] = None,
                 client: Optional[httpx.AsyncClient] = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S):
        # key 从项目现有 secret 机制读取：environ（.env 由 api_server 启动注入；
        # 测试/驱动脚本自行加载 .env）。注入参数语义：
        #   api_key=None  → 读 environ（默认）
        #   api_key=""    → 显式禁用（caller 裁决优先于环境——全量回归中 client
        #                    fixture 会把 .env 灌进 environ，显式 "" 必须仍然禁用）
        self._api_key = (str(api_key) if api_key is not None
                         else os.environ.get("MINIMAX_API_KEY", ""))
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._client = client            # 注入（测试 MockTransport / 复用连接池）
        self._owns_client = client is None
        self.timeout_s = timeout_s

    # ============ request mapping（§14）============
    def build_request(self, pack: VoiceGenerationPack, *,
                      subtitle_enable: bool = True,
                      subtitle_type: str = "word") -> dict:
        """pack → MiniMax t2a_v2 request body。输出天然脱敏（不含 key），
        可直接作为 provider_request_snapshot 落盘（§18）。"""
        binding = pack.provider_binding or {}
        settings = pack.synthesis_defaults or {}
        speed = float(settings.get("speed", 1.0))
        vol = float(settings.get("vol", 1.0))
        pitch = int(settings.get("pitch", 0))
        # 越界双重守卫（Profile 校验层之外再挡一次，防 pack 被构造绕过）
        if not (VOICE_SPEED_MIN <= speed <= VOICE_SPEED_MAX):
            raise VoiceProviderError("provider_request_failed",
                                     f"speed 越界: {speed}")
        if not (0 < vol <= 10.0):
            raise VoiceProviderError("provider_request_failed",
                                     f"vol 越界: {vol}")
        if not (VOICE_PITCH_MIN <= pitch <= VOICE_PITCH_MAX):
            raise VoiceProviderError("provider_request_failed",
                                     f"pitch 越界: {pitch}")
        request: Dict[str, Any] = {
            "model": str(binding.get("default_model") or ""),
            "text": pack.text,                       # 原样（§15）
            "stream": False,
            "voice_setting": {
                "voice_id": str(binding.get("provider_voice_id") or ""),
                "speed": speed,
                "vol": vol,
                "pitch": pitch,
            },
            "audio_setting": dict(REQUEST_AUDIO_SETTING),
        }
        if pack.language_boost:
            request["language_boost"] = pack.language_boost
        if subtitle_enable:
            request["subtitle_enable"] = True
            request["subtitle_type"] = subtitle_type
        if not request["model"] or not request["voice_setting"]["voice_id"]:
            raise VoiceProviderError(
                "provider_request_failed",
                "request mapping 缺少 model / voice_id（pack 不完整）")
        return request

    # ============ 调用（§13）============
    async def synthesize(self, pack: VoiceGenerationPack, *,
                         pronunciation_dict: Optional[dict] = None) -> dict:
        """调用 t2a_v2。返回 {audio, provider_extra_info, request_snapshot,
        latency_ms, word_timestamps, word_timestamps_note, subtitle_file_url,
        trace_id}。
        失败 → VoiceProviderError（structured，§25）。

        pronunciation_dict：MiniMax 官方发音替换（{"tone": ["处理/(chu3)(li3)"]}）。
        显式 caller 参数（测试 fixture 用），Adapter 绝不从 Dialogue 文本自动注入
        （§16：生产 text 不得被偷偷改写）。进 request_snapshot 留证。"""
        if not self._api_key:
            raise VoiceProviderError(
                "provider_not_configured",
                "MINIMAX_API_KEY 未配置（.env / environ）")
        request = self.build_request(pack)
        if pronunciation_dict:
            request["pronunciation_dict"] = dict(pronunciation_dict)
        url = self.base_url + T2A_PATH
        headers = {"Authorization": f"Bearer {self._api_key}",
                   "Content-Type": "application/json"}
        t0 = time.monotonic()
        try:
            if self._client is not None:
                resp = await self._client.post(url, json=request, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self.timeout_s) as c:
                    resp = await c.post(url, json=request, headers=headers)
        except VoiceProviderError:
            raise
        except Exception as e:   # noqa: BLE001 —— 网络/超时/连接错误统一 structured
            raise VoiceProviderError(
                "provider_request_failed",
                f"MiniMax 请求失败: {type(e).__name__}: {str(e)[:200]}",
                {"url_host": self.base_url}) from e
        latency_ms = int((time.monotonic() - t0) * 1000)

        try:
            payload = resp.json()
        except Exception as e:
            raise VoiceProviderError(
                "provider_request_failed",
                f"MiniMax 响应不是合法 JSON（HTTP {resp.status_code}）",
                {"http_status": resp.status_code}) from e

        base = payload.get("base_resp") or {}
        status_code = base.get("status_code")
        if status_code != 0:
            code = _PROVIDER_CODE_MAP.get(status_code, "provider_request_failed")
            raise VoiceProviderError(code,
                                     f"MiniMax 返回错误 status_code={status_code}",
                                     {"provider_status_code": status_code,
                                      "provider_status_msg":
                                          str(base.get("status_msg", ""))[:300],
                                      "model": request["model"],
                                      "provider_voice_id":
                                          request["voice_setting"]["voice_id"],
                                      "trace_id": str(payload.get("trace_id") or "")})

        data = payload.get("data") or {}
        audio_hex = data.get("audio")
        if not audio_hex or not isinstance(audio_hex, str):
            raise VoiceProviderError(
                "audio_decode_failed",
                "MiniMax 响应缺少 data.audio（HTTP 200 但无音频）",
                {"http_status": resp.status_code})
        try:
            audio_bytes = binascii.unhexlify(audio_hex)
        except (binascii.Error, ValueError) as e:
            raise VoiceProviderError(
                "audio_decode_failed",
                f"data.audio hex 解码失败: {str(e)[:120]}",
                {"hex_len": len(audio_hex)}) from e
        if not audio_bytes:
            raise VoiceProviderError("audio_decode_failed", "解码后音频为空")

        extra = _sanitize_meta(payload.get("extra_info") or {})
        # data 里除 audio hex 外的可记录字段（如 subtitle_file URL）一并留证
        data_meta = _sanitize_meta({k: v for k, v in data.items() if k != "audio"})
        if data_meta:
            extra.setdefault("data_fields", data_meta)
        subtitle_file_url = str(data.get("subtitle_file") or "")
        trace_id = str(payload.get("trace_id") or "")
        if trace_id:
            extra.setdefault("trace_id", trace_id)   # §36：trace_id 进 artifact metadata

        # 词级时间戳（§14）：subtitle_type=word 时 provider 经 subtitle_file 下发；
        # response 内嵌列表有就存；都没有记 diagnostic，绝不伪造、不失败
        timestamps: List[Dict[str, Any]] = []
        note = "word_timestamps_unavailable"
        for k in ("audio_word_timestamps", "word_timestamps"):
            v = extra.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                timestamps = v
                note = ""
                break
        if not timestamps and subtitle_file_url:
            note = "word_timestamps_via_subtitle_file"   # 有时间戳载体（URL），非缺失

        return {
            "audio": audio_bytes,
            "provider_extra_info": extra,
            "request_snapshot": dict(request),   # 脱敏由 build_request 构造保证
            "latency_ms": latency_ms,
            "word_timestamps": timestamps,
            "word_timestamps_note": note,
            "subtitle_file_url": subtitle_file_url,
            "trace_id": trace_id,
        }
