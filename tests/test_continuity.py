"""Phase 6A：镜头接力 / 开场来源 最小测试。

覆盖：
- Shot 默认 opening_source=inddependent + video_end_frame 空 Asset
- 旧 project.json（无接力字段）from_dict 正常加载
- _collect_first_frame_refs：character + continuity（reference_previous）
- _effective_first_frame_url：inherit_previous 用来源末帧 url
- continuity_status：ready / stale / broken
- POST /opening-source：independent 清空；reference 设置 source_shot_id+version；第一镜 400；上一镜未完成视频 400
- POST /sync-source：同步 source_version
"""
import shutil
from pathlib import Path

import pytest

from deps import get_project_manager
from src.core.models import (Project, Scene, Shot, StyleAnchor, Asset, VideoAsset,
                             VideoVersion, OpeningSource)
from src.core.store import ProjectStore
from routers.shots import (_collect_first_frame_refs, _effective_first_frame_url,
                           continuity_status)


# ---------- 模型 / 兼容 ----------
def test_opening_source_defaults():
    s = Shot(shot_id="s01_c01", scene_id="s01", index=1)
    assert s.opening_source == OpeningSource.INDEPENDENT.value
    assert s.source_shot_id is None
    assert s.source_version is None
    assert s.video_end_frame.local is None and s.video_end_frame.url is None
    assert s.video_end_source_version is None


def test_legacy_project_loads_continuity_defaults():
    """旧 project.json（无接力字段）from_dict 正常，接力字段取默认。"""
    legacy = {"shot_id": "s01_c01", "scene_id": "s01", "index": 1, "description": "x",
              "prompt_en": "old", "link_mode": "i2v",
              "first_frame": {}, "last_frame": {}, "audio": {}, "video": {}}
    s = Shot.from_dict(legacy)
    assert s.opening_source == "independent"
    assert s.source_shot_id is None
    assert s.video_end_frame.local is None


def test_video_end_frame_roundtrip():
    s = Shot(shot_id="s01_c01", scene_id="s01", index=1,
             video_end_frame=Asset(local="/a/end.png", url="https://x/end.png", status="done"),
             video_end_source_version="vv_1")
    s2 = Shot.from_dict(s.to_dict())
    assert s2.video_end_frame.local == "/a/end.png"
    assert s2.video_end_frame.url == "https://x/end.png"
    assert s2.video_end_source_version == "vv_1"


# ---------- helpers ----------
def _proj_two_shots(shot1_done=True, shot1_end_url="https://x/end.png"):
    """两镜头同场景；shot1 已完成视频 + 末帧；shot2 待接力。"""
    sh1 = Shot(shot_id="s01_c01", scene_id="s01", index=1, characters=["Alan"],
               final_video="vv_a" if shot1_done else None,
               video=VideoAsset(status="done" if shot1_done else "pending", num_frames=81, frame_rate=24),
               video_end_frame=Asset(local="/a/end.png", url=shot1_end_url, status="done") if shot1_done else Asset())
    sh2 = Shot(shot_id="s01_c02", scene_id="s01", index=2, characters=["Alan"])
    return Project(name="t", style=StyleAnchor(aspect_ratio="16:9"),
                   characters=[__import__("src.core.models", fromlist=["Character"]).Character(name="Alan", description_en="a boy", views={"front": Asset(url="https://x/alan.png", status="done")})],
                   scenes=[Scene(scene_id="s01", shots=[sh1, sh2])])


def test_collect_refs_character_and_continuity():
    p = _proj_two_shots()
    sh2 = p.get_shot("s01_c02")
    sh2.opening_source = OpeningSource.REFERENCE_PREVIOUS.value
    sh2.source_shot_id = "s01_c01"
    refs = _collect_first_frame_refs(p, sh2)
    types = [r["type"] for r in refs]
    assert "character" in types
    assert "continuity" in types


def test_effective_first_frame_inherit():
    p = _proj_two_shots()
    sh2 = p.get_shot("s01_c02")
    sh2.opening_source = OpeningSource.INHERIT_PREVIOUS.value
    sh2.source_shot_id = "s01_c01"
    # inherit -> 来源末帧 url
    assert _effective_first_frame_url(p, sh2) == "https://x/end.png"
    # independent -> 本镜已选首帧（空）
    sh2.opening_source = OpeningSource.INDEPENDENT.value
    assert _effective_first_frame_url(p, sh2) == ""


def test_continuity_status_ready_stale_broken():
    p = _proj_two_shots()
    sh2 = p.get_shot("s01_c02")
    sh2.opening_source = OpeningSource.INHERIT_PREVIOUS.value
    sh2.source_shot_id = "s01_c01"
    sh2.source_version = "vv_a"
    assert continuity_status(p, sh2) == ("ready", "")
    # stale：source_version 与来源 final_video 不一致
    sh2.source_version = "vv_old"
    assert continuity_status(p, sh2)[0] == "stale"
    # broken：来源未完成视频
    p2 = _proj_two_shots(shot1_done=False)
    sh2b = p2.get_shot("s01_c02")
    sh2b.opening_source = OpeningSource.INHERIT_PREVIOUS.value
    sh2b.source_shot_id = "s01_c01"
    assert continuity_status(p2, sh2b)[0] == "broken"
    # broken：来源末帧无 url（inherit 需公网 URL）
    p3 = _proj_two_shots(shot1_end_url=None)
    sh2c = p3.get_shot("s01_c02")
    sh2c.opening_source = OpeningSource.INHERIT_PREVIOUS.value
    sh2c.source_shot_id = "s01_c01"
    sh2c.source_version = "vv_a"
    assert continuity_status(p3, sh2c)[0] == "broken"
    # independent
    sh2d = _proj_two_shots().get_shot("s01_c02")
    assert continuity_status(p, sh2d)[0] == "independent"


# ---------- 接口 ----------
PROJECT = "_test_continuity"


def _build(pname):
    pm = get_project_manager()
    shutil.rmtree(pm.base_dir / pname, ignore_errors=True)
    pdir = pm.create_project(pname)
    sh1 = Shot(shot_id="s01_c01", scene_id="s01", index=1, characters=["Alan"],
               final_video="vv_a", video=VideoAsset(status="done", num_frames=81, frame_rate=24),
               video_end_frame=Asset(local=str(pdir / "output" / "frames" / "s01_c01_video_end.png"),
                                     url="https://x/end.png", status="done"),
               video_versions=[VideoVersion(version_id="vv_a", status="done",
                                            local=str(pdir / "output" / "videos" / "s01_c01.mp4"),
                                            url="https://x/v.mp4")])
    sh2 = Shot(shot_id="s01_c02", scene_id="s01", index=2, characters=["Alan"])
    proj = Project(name=pname, style=StyleAnchor(aspect_ratio="16:9"),
                   scenes=[Scene(scene_id="s01", shots=[sh1, sh2])])
    ProjectStore(pdir).save(proj)
    return pdir


@pytest.fixture
def proj(client):
    _build(PROJECT)
    yield PROJECT
    shutil.rmtree(get_project_manager().get_project_dir(PROJECT), ignore_errors=True)


def test_set_opening_source_reference(proj, client):
    """reference_previous：设置 source_shot_id + source_version（=上一镜 final_video）。"""
    r = client.post(f"/api/project/{proj}/shot/s01_c02/opening-source", json={"source": "reference_previous"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["opening_source"] == "reference_previous"
    assert j["source_shot_id"] == "s01_c01"
    assert j["source_version"] == "vv_a"


def test_set_opening_source_inherit(proj, client):
    r = client.post(f"/api/project/{proj}/shot/s01_c02/opening-source", json={"source": "inherit_previous"})
    assert r.status_code == 200, r.text
    assert r.json()["opening_source"] == "inherit_previous"


def test_set_opening_source_first_shot_400(proj, client):
    """第一镜只能独立生成。"""
    r = client.post(f"/api/project/{proj}/shot/s01_c01/opening-source", json={"source": "reference_previous"})
    assert r.status_code == 400


def test_set_opening_source_independent_clears(proj, client):
    client.post(f"/api/project/{proj}/shot/s01_c02/opening-source", json={"source": "reference_previous"})
    r = client.post(f"/api/project/{proj}/shot/s01_c02/opening-source", json={"source": "independent"})
    assert r.status_code == 200
    j = r.json()
    assert j["source_shot_id"] is None and j["source_version"] is None


def test_sync_source(proj, client):
    client.post(f"/api/project/{proj}/shot/s01_c02/opening-source", json={"source": "inherit_previous"})
    # 模拟 stale：手动改 source_version
    pdir = get_project_manager().get_project_dir(proj)
    store = ProjectStore(pdir)
    store.update_shot("s01_c02", lambda s: setattr(s, "source_version", "vv_old"))
    r = client.post(f"/api/project/{proj}/shot/s01_c02/sync-source")
    assert r.status_code == 200, r.text
    assert r.json()["source_version"] == "vv_a"   # 同步到来源当前 final_video
