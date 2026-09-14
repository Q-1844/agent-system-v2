# src/audio/media_ops.py
# Audio V1 — Phase A1：音频媒体操作（15.md §17/§37）。
#
#   normalize_to_production_wav：provider 原始音频 → production normalized WAV
#     （PCM s16 / 48kHz / mono，deterministic ffmpeg 参数，-map_metadata -1 去元数据；
#     不覆盖 provider original——raw 与 production 是两个文件）。
#   ffprobe_audio：真实容器探测（duration/sample_rate/channels/codec/bitrate/format/
#     size）——不 hardcode provider 输出格式，每次必须 ffprobe（15.md §17A）。
#
# ffmpeg/ffprobe 从 PATH 取（与 src/core/store.py 的 ffprobe 使用同口径：
# 本项目运行环境前提）。失败 → 明确错误，不伪造 probe 数据（§14 同精神）。
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from .models import VoiceSchemaError


class AudioMediaError(Exception):
    """音频规范化/探测失败（service 层转 audio_normalization_failed）。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


# 内部 production 统一目标（15.md §17B：PCM WAV 48kHz mono——
# 未来 lip-sync / timeline / mix / video mux 都可统一处理）
PRODUCTION_WAV_SPEC = {"format": "wav", "codec": "pcm_s16le",
                       "sample_rate": 48000, "channels": 1}


def normalize_to_production_wav(raw_bytes: bytes, out_path, *, src_suffix: str = ".mp3") -> dict:
    """provider 原始音频字节 → production normalized WAV。返回 {path, audio_spec}。
    deterministic：同一原始输入 → 同一输出字节（固定参数 + 去元数据）。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=src_suffix, dir=str(out_path.parent))
    try:
        with open(fd, "wb") as f:
            f.write(raw_bytes)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-i", tmp,
               "-vn", "-ac", "1", "-ar", str(PRODUCTION_WAV_SPEC["sample_rate"]),
               "-sample_fmt", "s16", "-map_metadata", "-1",
               "-f", "wav", str(out_path)]
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        err = proc.stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise AudioMediaError(
                "audio_normalization_failed",
                f"ffmpeg 规范化失败（{Path(str(out_path)).name}）："
                f"{err.strip()[:300]}")
    finally:
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise AudioMediaError("audio_normalization_failed",
                              "ffmpeg 未产出 production WAV（输出为空）")
    return {"path": str(out_path), "audio_spec": dict(PRODUCTION_WAV_SPEC)}


def ffprobe_audio(path) -> Optional[Dict[str, Any]]:
    """ffprobe 探测真实音频规格（§17：每次必须 ffprobe，不 hardcode）。
    失败返回 None（caller 记录 diagnostic，不伪造数据）。"""
    p = Path(str(path))
    if not p.exists():
        return None
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(p)],
            capture_output=True, timeout=30)
        if proc.returncode != 0:
            return None
        import json as _json
        # 🔴 显式 UTF-8 + replace：ffprobe JSON 的 filename 字段含项目路径
        # （中文用户名/项目名很常见），默认 locale（GBK）解码会 UnicodeDecodeError
        # → 探测静默失败。路径本身不参与解析字段，mojibake 无害。
        info = _json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
    except Exception:
        return None
    fmt = info.get("format") or {}
    streams = [s for s in info.get("streams") or []
               if s.get("codec_type") == "audio"]
    if not streams:
        return None
    st = streams[0]
    return {
        "duration_s": float(fmt["duration"]) if fmt.get("duration") else None,
        "format": fmt.get("format_name"),
        "size_bytes": int(fmt["size"]) if str(fmt.get("size") or "").isdigit() else None,
        "bitrate": int(fmt["bit_rate"]) if str(fmt.get("bit_rate") or "").isdigit() else None,
        "codec": st.get("codec_name"),
        "sample_rate": int(st["sample_rate"]) if str(st.get("sample_rate") or "").isdigit() else None,
        "channels": st.get("channels"),
    }


def save_raw_asset(raw_bytes: bytes, out_path) -> dict:
    """保存 provider original（15.md §17A：先落 raw，绝不覆盖已有文件——
    audio_version_id 单调不回收，同路径已存在 = 程序错误）。"""
    out_path = Path(out_path)
    if out_path.exists():
        raise AudioMediaError("audio_raw_write_conflict",
                              f"raw asset 路径已存在（id 分配异常）: {out_path.name}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw_bytes)
    return {"path": str(out_path)}
