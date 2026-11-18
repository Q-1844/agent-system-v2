"""CP-Heal 防回退（2026-11-18 anime750s run 345 实弹）：修复重排计划后，
锁定镜头旧末帧（local-only 不托管）遇新接力需求 → 补挂自愈；失败 fail-loud。"""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.core.models import (Project, Scene, Shot, Character, StyleAnchor,
                             VideoAsset, ShotStatus, Asset as _A)


def _locked_shot(sid, *, end_local=None, end_url=None, video_local="v.mp4"):
    sh = Shot(shot_id=sid, scene_id=sid.rsplit("_", 1)[0], index=1)
    sh.locked = True
    sh.final_video = "ver_1"
    sh.video = VideoAsset(local=video_local, url="https://v", status="done")
    sh.video_end_frame = _A(local=end_local, url=end_url, status="done")
    return sh


def _plan_entry(sid, requires_end):
    class _P:
        shot_id = sid
        requires_previous_video_end = requires_end
    return _P()


def _project(scene_id, shots):
    sc = Scene(scene_id=scene_id, shots=shots)
    return Project(name="p", style=StyleAnchor(art_style="anime"), characters=[], scenes=[sc])


@pytest.mark.asyncio
async def test_heal_rehosts_local_end(tmp_path):
    """锁定镜 local-only 末帧 + 新计划要求接力 → 补托管得 URL（run 345 s03_c10 形态）。"""
    from routers.shots import heal_locked_video_ends
    local = tmp_path / "end.jpg"; local.write_bytes(b"jpg")
    sh = _locked_shot("s03_c10", end_local=str(local))
    proj = _project("s03", [sh, Shot(shot_id="s03_c11", scene_id="s03", index=2)])
    # 要求接力的是下游 c11 的计划条目（preflight 语义：sh[i] 的末帧被 sh[i+1] 需要）
    plans = {"s03": type("P", (), {"shots": [_plan_entry("s03_c10", False),
                                             _plan_entry("s03_c11", True)]})()}
    host = AsyncMock(); host.upload = AsyncMock(return_value="https://pages/end.jpg")
    with patch("routers.shots.get_rehost_host", return_value=host), \
         patch("src.core.store.ProjectStore.save"):
        healed = await heal_locked_video_ends(proj, plans, tmp_path)
    assert healed == ["s03_c10"]
    assert sh.video_end_frame.url == "https://pages/end.jpg"
    assert sh.video_end_frame.url_ready


@pytest.mark.asyncio
async def test_heal_skips_ready_and_unrequired(tmp_path):
    """已 ready 跳过；下游不要求接力不碰（幂等边界）。"""
    from routers.shots import heal_locked_video_ends
    ready = _locked_shot("s01_c01", end_local="a", end_url="https://ok")
    unreq = _locked_shot("s01_c03", end_local="b")
    proj = _project("s01", [ready, unreq])
    plans = {"s01": type("P", (), {"shots": [_plan_entry("s01_c01", True),
                                             _plan_entry("s01_c03", False)]})()}
    with patch("src.core.store.ProjectStore.save"):
        healed = await heal_locked_video_ends(proj, plans, tmp_path)
    assert healed == []


@pytest.mark.asyncio
async def test_heal_fail_loud_on_missing_everything(tmp_path):
    """无本地末帧且视频文件缺失 → 不静默、不抛死，返回未愈（preflight 继续拦）。"""
    from routers.shots import heal_locked_video_ends
    sh = _locked_shot("s02_c05", end_local=None, video_local="ghost.mp4")
    proj = _project("s02", [sh])
    plans = {"s02": type("P", (), {"shots": [_plan_entry("s02_c05", False),
                                             _plan_entry("s02_c06", True)]})()}
    healed = await heal_locked_video_ends(proj, plans, tmp_path)
    assert healed == []
