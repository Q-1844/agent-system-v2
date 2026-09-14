# src/audio/av_sync/av_truth.py
# Audio V1 — Phase A2：authoritative Visual Video 解析 + 视频媒体探测（16.md §1/§5）。
#
# 🔴 Video truth 复用（§1 审计结论，2026-09-14）：
#   authoritative = Shot.final_video（version_id 指针）→ video_versions[] 匹配项
#   （project_intelligence._resolve_final_video 的同口径只读复用——不另发明第二套
#   Video canonical）。legacy Shot.video 已由 ProjectStore.load() 迁移为
#   「legacy_video」版本 + 指针推断，故加载后权威路径恒可确定。
# 🔴 VideoVersion 无 generation_basis 字段（Visual V1 冻结，不添加）→ 视频身份 =
#   本地文件内容 sha256（source_video_fingerprint，内容级身份，比 basis 指纹更强）。
# 🔴 时长/规格一律 ffprobe 实测（§5：不使用 DB 猜测值；seconds_returned 仅可交叉核对）。
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from src.core.models import Shot, ShotStatus


class VideoProbeError(Exception):
    """视频探测失败（video_not_ready / unsupported_video 的底层信号）。"""


def resolve_authoritative_video(shot: Shot) -> Optional[Any]:
    """authoritative video = final_video 指针 → video_versions 匹配项。
    指针缺失/无匹配 → None（missing_video；绝不回落 glob/mtime 猜测）。"""
    vid = shot.final_video
    if not vid:
        return None
    return next((v for v in shot.video_versions if v.version_id == vid), None)


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ffprobe_video(path) -> Dict[str, Any]:
    """ffprobe 实测视频规格（§22：video/audio duration、fps、resolution、codecs）。
    失败 → VideoProbeError（caller 转 video_not_ready / unsupported_video，
    绝不伪造 probe 数据）。"""
    p = Path(str(path))
    if not p.exists():
        raise VideoProbeError(f"视频文件不存在: {p}")
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(p)],
            capture_output=True, timeout=60)
        if proc.returncode != 0:
            raise VideoProbeError(f"ffprobe 失败 rc={proc.returncode}")
        # 🔴 显式 UTF-8 + replace（路径含中文，默认 GBK 解码会炸——A1 实录教训）
        info = json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
    except VideoProbeError:
        raise
    except Exception as e:  # noqa: BLE001
        raise VideoProbeError(f"ffprobe 异常: {type(e).__name__}: {str(e)[:120]}") from e
    fmt = info.get("format") or {}
    streams = info.get("streams") or []
    vst = next((s for s in streams if s.get("codec_type") == "video"), None)
    ast = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if vst is None:
        raise VideoProbeError("无视频流（unsupported video）")
    duration_s = fmt.get("duration")
    if duration_s is None and vst.get("duration") is not None:
        duration_s = vst.get("duration")
    try:
        duration_ms = int(round(float(duration_s) * 1000)) if duration_s else 0
    except (TypeError, ValueError):
        duration_ms = 0
    fps = 0.0
    rate = str(vst.get("avg_frame_rate") or vst.get("r_frame_rate") or "0/1")
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            if float(den) > 0:
                fps = round(float(num) / float(den), 3)
        except (TypeError, ValueError):
            fps = 0.0
    return {
        "duration_ms": duration_ms,
        "width": vst.get("width"),
        "height": vst.get("height"),
        "fps": fps,
        "video_codec": vst.get("codec_name"),
        "audio_codec": (ast or {}).get("codec_name"),
        "has_audio": ast is not None,
        "format": fmt.get("format_name"),
        "size_bytes": int(fmt["size"]) if str(fmt.get("size") or "").isdigit() else None,
    }


def resolve_video_truth(shot: Shot) -> Optional[Dict[str, Any]]:
    """authoritative video 完整真相（只读）：
    {artifact_id, version_id, video_id, status, local_path, url, probe, fingerprint}
    - 指针缺失/无匹配 → None；
    - 文件缺失/ffprobe 失败 → VideoProbeError（caller 分类 video_not_ready /
      unsupported_video）。"""
    vv = resolve_authoritative_video(shot)
    if vv is None:
        return None
    local = getattr(vv, "local", None)
    if not local or not Path(str(local)).exists():
        raise VideoProbeError(
            f"authoritative video {vv.version_id} 本地文件缺失: {local!r}")
    probe = ffprobe_video(local)
    return {
        "artifact_id": vv.version_id,
        "video_id": vv.video_id,
        "task_id": vv.task_id,
        "status": vv.status,
        "local_path": str(local),
        "url": vv.url,
        "probe": probe,
        "fingerprint": sha256_file(local),
    }
