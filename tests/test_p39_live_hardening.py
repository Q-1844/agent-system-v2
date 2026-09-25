# Phase 39 LIVE 前硬ening（用户 2026-09-25 定版）针对性 regression：
# A. Clean Review 路由窄豁免——纯审查仍走 Clean Review；显式 mutation 意图不被劫持；
#    否定窗口（「不要修改」≠ mutation）。
# B. keyframes 桥帧检证据落 trace——sample_frames 对桥镜返回确定性 trace 提醒。
# C. 窄锚定纪律迁移锚定（Skill System V1 prompt 减法后：校准/桥 workflow 纪律
#    1:1 迁入 skill 包，prompt 只留身份/权限/真相纪律——本节断言锚点在包内）。
import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from src.agents import session_governance
from src.core.models import Project, Scene, Shot, StyleAnchor, VideoVersion
from src.core.store import ProjectStore


# ============ A. Clean Review 路由窄规则 ============
def test_pure_review_still_routes_to_clean_review():
    """真正的 Clean Review 不被打穿（回归保护）。"""
    for c in ("帮我全片检查一下连续性",
              "做一次整体审查",
              "逐镜检查一遍，产出问题清单",
              "全片检查一下连续性，不要修改任何镜头",   # 否定窗口：修改在否定语境
              "从头过一遍看看有没有问题"):
        assert session_governance.should_route_to_clean_review(c), c


def test_mutation_intent_not_hijacked():
    """显式 mutation 意图 → 正常 tool-loop（39B replay 劫持事故的针对性回归）。"""
    for c in ("校准这一镜并落档 trace",
              "修复这个连续性问题",
              "对 M3→M4 的断缝完成三档裁定并写入校准 trace",
              "重试 s12_c02，按锚定杠杆重写 description",
              "把这条接缝测一下，若超带按 keyframes 桥处置",
              "历史校准的复盘裁定，把裁定记录到 replay run"):
        assert not session_governance.should_route_to_clean_review(c), c


def test_domain_word_alone_with_mutation_context_not_hijacked():
    """「连续性」出现在修复/校准语境（39B 事故原句形态）→ 不劫持。"""
    assert session_governance.is_review_request(
        "修复这个连续性问题")   # 话题词命中（保持既有语义）
    assert not session_governance.should_route_to_clean_review(
        "修复这个连续性问题")   # 窄规则：不进只读会话


def test_negation_window_blocks_false_mutation():
    """「不要修改/禁止重试」不是 mutation 意图——纯审查请求仍走 Clean Review。"""
    assert session_governance.has_mutation_intent("帮我修改这个镜头")
    assert not session_governance.has_mutation_intent("不要修改任何镜头")
    assert not session_governance.has_mutation_intent("禁止重试")
    assert not session_governance.has_mutation_intent("审查连续性，不要落档")


# ============ B. keyframes 桥帧检 trace 提醒 ============
def _make_video(path: Path, color: str = "red"):
    path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c={color}:size=64x48:rate=8:duration=1",
         "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr
    return path


class _FakeVision:
    def chat_completion_vision(self, msgs, **kwargs):
        return _FakeAwaitable({"content": json.dumps(
            {"discontinuity": "none", "note": "自然过渡"}), "model": "f", "usage": {}})


class _FakeAwaitable(dict):
    def __await__(self):
        if False:
            yield
        return self


def _runtime(tmp_path):
    from src.agents.runtime import AgentRuntime
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=_FakeVision())


def _proj_with_videos(pdir: Path, bridge_link_mode: str):
    v1 = _make_video(pdir / "output" / "videos" / "s01_c01.mp4")
    v2 = _make_video(pdir / "output" / "videos" / "s01_c02.mp4", color="blue")
    c02 = Shot(shot_id="s01_c02", scene_id="s01", index=2, description="B",
               final_video="vv_b", link_mode=bridge_link_mode)
    c02.video_versions.append(VideoVersion(version_id="vv_b", local=str(v2),
                                           status="done"))
    c01 = Shot(shot_id="s01_c01", scene_id="s01", index=1, description="A",
               final_video="vv_a")
    c01.video_versions.append(VideoVersion(version_id="vv_a", local=str(v1),
                                           status="done"))
    ProjectStore(pdir).save(Project(name="p39h", style=StyleAnchor(),
                                    scenes=[Scene(scene_id="s01", shots=[c01, c02])]))


@pytest.mark.parametrize("link_mode,expect_reminder", [
    ("keyframes", True),
    ("i2v", False),
])
def test_sample_frames_bridge_trace_reminder(tmp_path, link_mode, expect_reminder):
    from src.agents.director_frames import _execute_sample_frames
    pdir = tmp_path / "proj"
    pdir.mkdir()
    _proj_with_videos(pdir, link_mode)
    rt = _runtime(pdir)
    out = asyncio.run(_execute_sample_frames(
        rt, pdir.name, json.dumps({"shot_id": "s01_c02"}), None, None))
    assert out["ok"]
    assert ("trace_reminder" in out) == expect_reminder
    if expect_reminder:
        assert "bridge_frame_check" in out["trace_reminder"]
        assert "calibration.trace_upsert" in out["trace_reminder"]


# ============ C. 窄锚定纪律在 skill 包中（prompt 减法后的迁移回归） ============
_SKILL_BUILTIN = Path(__file__).resolve().parents[1] / "src" / "agents" / "skill_system" / "builtin"


def _skill_text(relpath: str) -> str:
    return (_SKILL_BUILTIN / relpath).read_text(encoding="utf-8")


def test_director_narrow_decision_anchors_in_skill():
    """窄锚定：smirk 不单独升级 retry 在校准包；桥需双端证据在桥包。"""
    relay = _skill_text("full-story-relay-calibration/references/director.md")
    assert "非关键表情偏差" in relay
    assert "不得仅凭表情偏差把裁定升级为 retry" in relay
    bridge = _skill_text("keyframes-bridge-repair/references/director.md")
    assert "插桥判定必须依据【双端证据】" in bridge
    assert "单一端点/单帧异常不构成插桥依据" in bridge


def test_director_bridge_frame_check_into_trace_in_skill():
    """bridge_frame_check 落 trace 纪律在 keyframes-bridge-repair 包中。"""
    bridge = _skill_text("keyframes-bridge-repair/references/director.md")
    assert "bridge_frame_check" in bridge
    assert "只有两端 MAE、无帧检记录，不得在 trace 中宣布桥成立" in bridge
