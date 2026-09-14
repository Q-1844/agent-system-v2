# src/audio/av_sync/sync_labs_adapter.py
# Audio V1 — Phase A2：SyncLabsLipSyncAdapter（16.md §16/§17/§18/§19/§20）。
#
# Provider 契约（2026-09-14 按官方 OpenAPI https://sync.so/docs/openapi.json 重核，
# 16.md §16 要求——历史参数已过时，以本节为准）：
#   Base   https://api.sync.so
#   Auth   header `x-api-key: SYNC_API_KEY`
#   POST /v2/generate   multipart/form-data：model（必填）+ video/audio（binary
#            文件直传）或 input（JSON 字符串，url/assetId）；options 为 JSON 字符串；
#            outputFileName 可选。→ 200 Generation{ id, status }
#   GET  /v2/generate/{id} → Generation{ status: PENDING|PROCESSING|COMPLETED|
#            FAILED|REJECTED, outputUrl, outputDuration, error, errorCode }
#   模型枚举：sync-3 / lipsync-2 / lipsync-1.9.0-beta / lipsync-2-pro / react-1
#   输出恒重编 H.264（CRF17），输入 MP4+WAV 全支持。
#
# 🔴 sync_mode 映射（§18 禁止默认映射 bounce/loop/cut_off——provider 默认是
#    bounce，绝不沿用）：
#     adaptation_mode=pad_audio   → options.sync_mode = "silence"（保留完整视频，
#                                   静音垫尾 = 我们 pad_audio 策略的 provider 侧同义）
#     adaptation_mode=remap_video → options.sync_mode = "remap"（仅 caller 显式）
#    我们的 sync_input.wav 已 ≈ video 时长，provider 不承担 timing policy（§18）。
# 🔴 speaker target 映射（§10/§11）：
#     auto_detect  → options.active_speaker_detection = {auto_detect: true}
#     manual_target→ options.active_speaker_detection = {frame_number, coordinates}
#     single_visible_speaker → 不设 ASD（单人镜无需）
# 🔴 生命周期（§19）：真实 async——submit → id → poll → COMPLETED/FAILED/REJECTED
#    → download；timeout / cancellation / structured error；绝不做阻塞式假同步。
# 🔴 secret 纪律：key 只进 header；request 快照 / evidence / 错误 / 日志零 key。
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from .models import (
    ADAPTATION_PAD_AUDIO,
    ADAPTATION_REMAP_VIDEO,
    SPEAKER_TARGET_AUTO_DETECT,
    SPEAKER_TARGET_MANUAL,
)

DEFAULT_BASE_URL = "https://api.sync.so"
GENERATE_PATH = "/v2/generate"
DEFAULT_POLL_INTERVAL_S = 5.0
DEFAULT_POLL_TIMEOUT_S = 1800.0        # lip-sync 任务分钟级；30min 上限
DEFAULT_UPLOAD_TIMEOUT_S = 300.0

# 结构化错误（§20）
E_PROVIDER_NOT_CONFIGURED = "provider_not_configured"
E_PROVIDER_REQUEST_FAILED = "provider_request_failed"
E_PROVIDER_GENERATION_FAILED = "provider_generation_failed"
E_PROVIDER_TIMEOUT = "provider_timeout"
E_PROVIDER_INVALID_MEDIA = "provider_invalid_media"
E_PROVIDER_DOWNLOAD_FAILED = "provider_download_failed"

_TERMINAL_OK = {"COMPLETED"}
_TERMINAL_BAD = {"FAILED", "REJECTED"}


class LipSyncProviderError(Exception):
    """structured provider 错误（§20）。detail 携带 provider generation id /
    status / errorCode / HTTP status 等脱敏安全信息；secret 零出现。"""

    def __init__(self, code: str, message: str, detail: Optional[dict] = None):
        self.code = code
        self.detail = dict(detail or {})
        super().__init__(f"[{code}] {message}")


def _sanitize(meta: Dict[str, Any]) -> Dict[str, Any]:
    """防御性脱敏（同 A1 口径）：疑似 secret 键丢弃，长字符串截断。"""
    out: Dict[str, Any] = {}
    for k, v in (meta or {}).items():
        if any(m in str(k).lower() for m in ("key", "token", "secret", "authorization", "password")):
            continue
        if isinstance(v, str) and len(v) > 2000:
            v = v[:2000] + "…"
        out[str(k)] = v
    return out


def build_provider_options(pack_dict: Dict[str, Any]) -> Dict[str, Any]:
    """pack → Sync Labs options（provider-specific 映射只在此层，§36：
    canonical/pack 不含 Sync Labs 独占字段）。"""
    timing = pack_dict.get("timing") or {}
    target = pack_dict.get("speaker_target") or {}
    options: Dict[str, Any] = {}
    mode = timing.get("adaptation_mode")
    if mode == ADAPTATION_PAD_AUDIO:
        options["sync_mode"] = "silence"        # 保留完整视频（§6/§18）
    elif mode == ADAPTATION_REMAP_VIDEO:
        options["sync_mode"] = "remap"          # 仅 caller 显式（§8）
    else:
        raise LipSyncProviderError(
            E_PROVIDER_REQUEST_FAILED, f"未知 adaptation_mode: {mode!r}")
    if target.get("mode") == SPEAKER_TARGET_AUTO_DETECT:
        options["active_speaker_detection"] = {"auto_detect": True}
    elif target.get("mode") == SPEAKER_TARGET_MANUAL:
        asd: Dict[str, Any] = {"auto_detect": False}
        if target.get("frame_number") is not None:
            asd["frame_number"] = int(target["frame_number"])
        coords = target.get("coordinates") or {}
        if coords:
            # 官方扩展槽：像素坐标 [x, y]（source frame 内）
            xy = coords.get("xy") or ([coords["x"], coords["y"]]
                                      if "x" in coords and "y" in coords else None)
            if xy:
                asd["coordinates"] = [int(xy[0]), int(xy[1])]
        options["active_speaker_detection"] = asd
    return options


class SyncLabsLipSyncAdapter:
    """sync_labs 适配器（A2 首家 cloud lip-sync provider；本地模型留 seam，§36）。"""

    def __init__(self, api_key: Optional[str] = None,
                 base_url: Optional[str] = None,
                 client: Optional[httpx.AsyncClient] = None,
                 poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
                 poll_timeout_s: float = DEFAULT_POLL_TIMEOUT_S):
        # key 语义与 A1 同口径：None=读 environ（SYNC_API_KEY）；""=显式禁用
        self._api_key = (str(api_key) if api_key is not None
                         else os.environ.get("SYNC_API_KEY", ""))
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._client = client
        self.poll_interval_s = poll_interval_s
        self.poll_timeout_s = poll_timeout_s

    # ============ request 组装（可落盘快照，无 key）============
    def build_request_parts(self, pack_dict: Dict[str, Any], *,
                            output_file_name: str = "") -> Dict[str, Any]:
        """multipart 文本字段 + options（provider_options_fingerprint 的输入）。
        绝不含 key / 文件内容——文件在 dispatch 时以 multipart binary 附上。"""
        options = build_provider_options(pack_dict)
        parts: Dict[str, Any] = {
            "model": str((pack_dict.get("provider_binding") or {}).get("model") or ""),
            "options": json.dumps(options, ensure_ascii=False),
        }
        if not parts["model"]:
            raise LipSyncProviderError(E_PROVIDER_REQUEST_FAILED,
                                       "pack 缺少 provider_binding.model")
        if output_file_name:
            parts["outputFileName"] = output_file_name
        return {"fields": parts, "options": options}

    # ============ 生命周期（§19）============
    async def generate(self, pack_dict: Dict[str, Any], *,
                       video_path: str, audio_path: str,
                       output_file_name: str = "",
                       cancel_event: Optional[asyncio.Event] = None) -> Dict[str, Any]:
        """submit → poll → download。返回 {raw_bytes, provider_generation_id,
        provider_metadata}。失败 → LipSyncProviderError（structured）。"""
        if not self._api_key:
            raise LipSyncProviderError(
                E_PROVIDER_NOT_CONFIGURED, "SYNC_API_KEY 未配置（.env / environ）")
        parts = self.build_request_parts(pack_dict, output_file_name=output_file_name)
        vpath, apath = Path(video_path), Path(audio_path)
        if not vpath.exists():
            raise LipSyncProviderError(E_PROVIDER_REQUEST_FAILED,
                                       f"video 文件不存在: {vpath}")
        if not apath.exists():
            raise LipSyncProviderError(E_PROVIDER_REQUEST_FAILED,
                                       f"audio 文件不存在: {apath}")

        headers = {"x-api-key": self._api_key}
        t0 = time.monotonic()
        # ---- submit（multipart 文件直传）----
        fields = {k: (str(v) if k != "options" else str(v)) for k, v in parts["fields"].items()}
        files = [
            ("video", (vpath.name, vpath.read_bytes(), "video/mp4")),
            ("audio", (apath.name, apath.read_bytes(), "audio/wav")),
        ]
        gen = await self._submit(fields, files, headers, pack_dict)
        gen_id = str(gen.get("id") or "")
        if not gen_id:
            raise LipSyncProviderError(
                E_PROVIDER_INVALID_MEDIA, "submit 响应缺少 generation id",
                {"http_payload": _sanitize(gen)})
        # ---- poll ----
        final = await self._poll(gen_id, headers, cancel_event)
        latency_ms = int((time.monotonic() - t0) * 1000)
        status = str(final.get("status") or "")
        if status in _TERMINAL_BAD:
            raise LipSyncProviderError(
                E_PROVIDER_GENERATION_FAILED,
                f"generation {gen_id} {status}: {str(final.get('error', ''))[:200]}",
                {"provider_generation_id": gen_id, "provider_status": status,
                 "provider_error_code": str(final.get("errorCode") or "")})
        if status not in _TERMINAL_OK:
            raise LipSyncProviderError(
                E_PROVIDER_GENERATION_FAILED,
                f"generation {gen_id} 非预期终态: {status!r}",
                {"provider_generation_id": gen_id, "provider_status": status})
        output_url = str(final.get("outputUrl") or "")
        if not output_url:
            raise LipSyncProviderError(
                E_PROVIDER_INVALID_MEDIA, "COMPLETED 但缺 outputUrl",
                {"provider_generation_id": gen_id})
        # ---- download ----
        raw = await self._download(output_url, gen_id)
        metadata = _sanitize({
            "provider_generation_id": gen_id,
            "provider_status": status,
            "output_url": output_url,
            "output_duration_s": final.get("outputDuration"),
            "provider_error_code": str(final.get("errorCode") or ""),
            "model": parts["fields"]["model"],
            "options": parts["options"],
            "total_latency_ms": latency_ms,
            "submitted_at": gen.get("createdAt"),
        })
        return {"raw_bytes": raw,
                "provider_generation_id": gen_id,
                "provider_metadata": metadata}

    # ---- 内部：submit / poll / download（独立方法便于测试注入）----
    async def _submit(self, fields: Dict[str, str], files: list,
                      headers: Dict[str, str], pack_dict: Dict[str, Any]):
        url = self.base_url + GENERATE_PATH
        try:
            if self._client is not None:
                resp = await self._client.post(url, data=fields, files=files,
                                               headers=headers)
            else:
                async with httpx.AsyncClient(
                        timeout=DEFAULT_UPLOAD_TIMEOUT_S) as c:
                    resp = await c.post(url, data=fields, files=files, headers=headers)
        except LipSyncProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise LipSyncProviderError(
                E_PROVIDER_REQUEST_FAILED,
                f"submit 失败: {type(e).__name__}: {str(e)[:200]}",
                {"url_host": self.base_url}) from e
        if resp.status_code in (401, 403):
            raise LipSyncProviderError(
                E_PROVIDER_REQUEST_FAILED,
                f"鉴权失败 HTTP {resp.status_code}",
                {"http_status": resp.status_code})
        if resp.status_code == 402:
            raise LipSyncProviderError(
                E_PROVIDER_REQUEST_FAILED, "余额/配额不足 HTTP 402",
                {"http_status": resp.status_code})
        if resp.status_code >= 400:
            raise LipSyncProviderError(
                E_PROVIDER_REQUEST_FAILED,
                f"submit HTTP {resp.status_code}",
                {"http_status": resp.status_code,
                 "safe_body": resp.text[:300]})
        try:
            return resp.json()
        except Exception as e:
            raise LipSyncProviderError(
                E_PROVIDER_REQUEST_FAILED,
                f"submit 响应非 JSON（HTTP {resp.status_code}）",
                {"http_status": resp.status_code}) from e

    async def _poll(self, gen_id: str, headers: Dict[str, str],
                    cancel_event: Optional[asyncio.Event]):
        url = self.base_url + GENERATE_PATH + f"/{gen_id}"
        deadline = time.monotonic() + self.poll_timeout_s
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise LipSyncProviderError(
                    E_PROVIDER_TIMEOUT,
                    f"generation {gen_id} 被 caller 取消",
                    {"provider_generation_id": gen_id})
            try:
                if self._client is not None:
                    resp = await self._client.get(url, headers=headers)
                else:
                    async with httpx.AsyncClient(timeout=60.0) as c:
                        resp = await c.get(url, headers=headers)
            except Exception as e:  # noqa: BLE001
                raise LipSyncProviderError(
                    E_PROVIDER_REQUEST_FAILED,
                    f"poll 失败: {type(e).__name__}: {str(e)[:200]}",
                    {"provider_generation_id": gen_id}) from e
            if resp.status_code >= 400:
                raise LipSyncProviderError(
                    E_PROVIDER_REQUEST_FAILED,
                    f"poll HTTP {resp.status_code}",
                    {"http_status": resp.status_code,
                     "provider_generation_id": gen_id})
            try:
                payload = resp.json()
            except Exception as e:
                raise LipSyncProviderError(
                    E_PROVIDER_REQUEST_FAILED, "poll 响应非 JSON",
                    {"provider_generation_id": gen_id}) from e
            status = str(payload.get("status") or "")
            if status in _TERMINAL_OK or status in _TERMINAL_BAD:
                return payload
            if time.monotonic() > deadline:
                raise LipSyncProviderError(
                    E_PROVIDER_TIMEOUT,
                    f"poll 超时（>{int(self.poll_timeout_s)}s），generation {gen_id} 仍 {status!r}",
                    {"provider_generation_id": gen_id,
                     "provider_status": status})
            await asyncio.sleep(self.poll_interval_s)

    async def _download(self, output_url: str, gen_id: str) -> bytes:
        try:
            if self._client is not None:
                resp = await self._client.get(output_url)
            else:
                async with httpx.AsyncClient(timeout=DEFAULT_UPLOAD_TIMEOUT_S,
                                             follow_redirects=True) as c:
                    resp = await c.get(output_url)
        except Exception as e:  # noqa: BLE001
            raise LipSyncProviderError(
                E_PROVIDER_DOWNLOAD_FAILED,
                f"下载失败: {type(e).__name__}: {str(e)[:200]}",
                {"provider_generation_id": gen_id}) from e
        if resp.status_code >= 400:
            raise LipSyncProviderError(
                E_PROVIDER_DOWNLOAD_FAILED,
                f"下载 HTTP {resp.status_code}",
                {"http_status": resp.status_code,
                 "provider_generation_id": gen_id})
        raw = resp.content
        if not raw:
            raise LipSyncProviderError(
                E_PROVIDER_DOWNLOAD_FAILED, "下载内容为空",
                {"provider_generation_id": gen_id})
        return raw
