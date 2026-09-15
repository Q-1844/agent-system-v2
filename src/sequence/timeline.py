# src/sequence/timeline.py
# Sequence S1 — Timeline Plan（24.md §13）。
#
# 性质（24.md §13）：ephemeral / deterministic / zero provider call 的纯派生对象。
# render 前由 Phase 1 从 ffprobe 实测的 source duration + canonical trim 算出；
# 绝不持久化为第二 canonical，也绝不根据 Shot.target_duration_s / provider request
# duration / 数据库历史值猜（§14：最终媒体事实必须从文件读取）。
#
# S1 没有 transition overlap（§11 hard cut only），因此：
#   timeline_start(cursor) 单调累加；总 duration = sum(effective clip durations)。
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .models import SequenceError


@dataclass
class ResolvedClip:
    """Phase 1 对单个 clip 的源真相解析结果（24.md §7 Source Video Truth）。
    shot_id → Shot.final_video → VideoVersion → authoritative source mp4。
    source_duration_ms / has_audio 来自 ffprobe 文件实测（§14），不是回读库值。"""
    index: int
    shot_id: str
    version_id: str                       # resolved final_video version_id
    source_file: str                      # authoritative source mp4 本地路径
    trim_in_ms: int
    trim_out_ms: int
    source_duration_ms: int = 0           # ffprobe 实测（Phase 1 填充；0=未探测）
    has_audio: bool = False               # ffprobe 实测是否含音频流


@dataclass
class TimelineClip:
    """一个 clip 在 Sequence timeline 上的投影（纯派生，int ms）。"""
    index: int
    shot_id: str
    version_id: str
    source_file: str
    source_duration_ms: int
    trim_in_ms: int
    trim_out_ms: int
    effective_ms: int                     # source_duration - trim_in - trim_out
    timeline_start_ms: int
    timeline_end_ms: int
    has_audio: bool = True                # ffprobe 实测（Phase 2 决定是否补 silence）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "shot_id": self.shot_id,
            "version_id": self.version_id,
            "source_file": self.source_file,
            "source_duration_ms": self.source_duration_ms,
            "trim_in_ms": self.trim_in_ms,
            "trim_out_ms": self.trim_out_ms,
            "effective_ms": self.effective_ms,
            "timeline_start_ms": self.timeline_start_ms,
            "timeline_end_ms": self.timeline_end_ms,
            "has_audio": self.has_audio,
        }


@dataclass
class SequenceTimelinePlan:
    """整个 Sequence 的 timeline 计划（24.md §13）。"""
    clips: List[TimelineClip] = field(default_factory=list)
    total_duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clips": [c.to_dict() for c in self.clips],
            "total_duration_ms": self.total_duration_ms,
        }


def validate_trims_vs_source(source_duration_ms: int, trim_in_ms: int, trim_out_ms: int,
                             *, shot_id: str) -> int:
    """24.md §12 trim 合法性（对 ffprobe 实测的 source duration）：
      0 <= trim_in，0 <= trim_out（models 层已保证结构）；
      trim_in + trim_out < source_duration。
    非法 → FAIL CLOSED（trim_invalid），绝不自动修正用户数字。
    返回 effective_ms。"""
    if source_duration_ms <= 0:
        raise SequenceError(
            "source_unreadable",
            f"clip {shot_id}: source duration 非法（{source_duration_ms}ms）——ffprobe 失败或空文件")
    if trim_in_ms + trim_out_ms >= source_duration_ms:
        raise SequenceError(
            "trim_invalid",
            f"clip {shot_id}: trim_in({trim_in_ms}) + trim_out({trim_out_ms}) "
            f">= source_duration({source_duration_ms})ms——非法（fail closed，不自动修正）")
    return source_duration_ms - trim_in_ms - trim_out_ms


def build_timeline_plan(resolved: List[ResolvedClip]) -> SequenceTimelinePlan:
    """由 resolved clips（已 probe、已验证 trim）构建 timeline 计划。
    确定性：同输入 → 同 plan；S1 无 overlap，cursor 单调累加（24.md §13）。"""
    clips: List[TimelineClip] = []
    cursor = 0
    for rc in resolved:
        effective = validate_trims_vs_source(
            rc.source_duration_ms, rc.trim_in_ms, rc.trim_out_ms, shot_id=rc.shot_id)
        clips.append(TimelineClip(
            index=rc.index,
            shot_id=rc.shot_id,
            version_id=rc.version_id,
            source_file=rc.source_file,
            source_duration_ms=rc.source_duration_ms,
            trim_in_ms=rc.trim_in_ms,
            trim_out_ms=rc.trim_out_ms,
            effective_ms=effective,
            timeline_start_ms=cursor,
            timeline_end_ms=cursor + effective,
            has_audio=rc.has_audio,
        ))
        cursor += effective
    return SequenceTimelinePlan(clips=clips, total_duration_ms=cursor)
