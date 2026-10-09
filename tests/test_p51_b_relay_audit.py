# tests/test_p51_b_relay_audit.py
# Phase51 B（B4）——接力端点 vs 实际保留端点审计定向验证（零 provider）。
# B4：接力依据必须对应最终保留片段；裁切/选版变化后检查真实下游。
import json
from pathlib import Path

import pytest

from src.core.models import Project, Scene, Shot
from src.sequence.relay_audit import audit_relay_endpoints
from src.sequence.timeline import ResolvedClip


def _project_with_relay(prev_end_ver="vv_prev"):
    p = Project(name="proj")
    s1 = Shot(shot_id="s01_c01", scene_id="s01", index=0)
    s1.video_end_source_version = prev_end_ver
    s2 = Shot(shot_id="s02_c01", scene_id="s01", index=1)
    s2.source_shot_id = "s01_c01"          # 接力：s02 开场沿用 s01 实际末帧
    s3 = Shot(shot_id="s03_c01", scene_id="s01", index=2)   # 非接力镜
    sc = Scene(scene_id="s01", summary="场景")
    sc.shots = [s1, s2, s3]
    p.scenes = [sc]
    return p


def _rc(shot_id, version_id="vv1", trim_out_ms=0, dur=10000):
    return ResolvedClip(index=0, shot_id=shot_id, version_id=version_id,
                        source_file="x.mp4", trim_in_ms=0,
                        trim_out_ms=trim_out_ms, source_duration_ms=dur)


def test_audit_no_relay_no_warning():
    p = _project_with_relay()
    resolved = [_rc("s01_c01"), _rc("s03_c01")]
    assert audit_relay_endpoints(p, resolved) is None


def test_audit_relay_intact_no_warning():
    """接力链存在、来源无尾部裁剪、版本一致 → 无风险。"""
    p = _project_with_relay()
    resolved = [_rc("s01_c01", version_id="vv_prev"), _rc("s02_c01")]
    assert audit_relay_endpoints(p, resolved) is None


def test_audit_trim_tail_cut_detected():
    """B4 核心情景：来源被尾部裁剪（trim_out>0）→ 接力依据≠观众所见末帧。"""
    p = _project_with_relay()
    resolved = [_rc("s01_c01", trim_out_ms=1100), _rc("s02_c01")]
    w = audit_relay_endpoints(p, resolved)
    assert w is not None and w["code"] == "relay_endpoint_mismatch_risk"
    kinds = {a["kind"] for a in w["affected"]}
    assert "trim_tail_cut" in kinds
    hit = next(a for a in w["affected"] if a["kind"] == "trim_tail_cut")
    assert hit["shot_id"] == "s02_c01" and hit["source_shot_id"] == "s01_c01"
    assert hit["trim_out_ms"] == 1100


def test_audit_version_mismatch_detected():
    """选版变化：末帧提取自 vv_old，本 render 解析到 vv_new → 版本脱节。"""
    p = _project_with_relay(prev_end_ver="vv_old")
    resolved = [_rc("s01_c01", version_id="vv_new"), _rc("s02_c01")]
    w = audit_relay_endpoints(p, resolved)
    assert w is not None
    hit = next(a for a in w["affected"] if a["kind"] == "version_mismatch")
    assert hit["end_source_version"] == "vv_old"
    assert hit["resolved_version_id"] == "vv_new"


def test_audit_source_missing_reported():
    p = _project_with_relay()
    resolved = [_rc("s02_c01")]   # 来源 s01_c01 不在 resolved（也不在项目？在项目）
    # 来源在项目内但不在 resolved → 不误报版本/trim（只审计 resolved 里的来源）
    assert audit_relay_endpoints(p, resolved) is None
    # 来源彻底不存在 → source_missing
    p2 = _project_with_relay()
    p2.scenes[0].shots[1].source_shot_id = "s09_c09"
    w = audit_relay_endpoints(p2, [_rc("s02_c01")])
    assert w is not None and w["affected"][0]["kind"] == "source_missing"
