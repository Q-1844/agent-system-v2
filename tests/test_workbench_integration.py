"""批次5：工作台集成回归（提示词.md §20.1.20 + 多批次/指针保护）。

- 新结构化字段经 PUT /shot 保存后 GET /shots 可回读
- PUT /shot 不覆盖多批次历史与选择指针（first_frame_batches/video_versions/selected_*/final_video）
- 旧 project.json（缺新字段）GET /shots 正常加载，新字段取默认
"""
import json
import shutil
from pathlib import Path

import pytest

from deps import get_project_manager, projects_dir


PROJECT = "_test_wb_int"


def _build(pname):
    from src.core.store import ProjectStore
    from src.core.models import (Project, Scene, Shot, StyleAnchor, ImageAsset,
                                 Asset, VideoAsset, VideoVersion, ImageBatch, Candidate)
    pm = get_project_manager()
    shutil.rmtree(pm.base_dir / pname, ignore_errors=True)
    pdir = pm.create_project(pname)
    ff_batch = ImageBatch(batch_id="ff_existing", kind="first_frame",
                          candidates=[Candidate(candidate_id="ff_existing_1",
                                                local=str(pdir / "output" / "frames" / "s01_first.png"),
                                                url="https://mock.agnes/s01.png", status="done")])
    vv = VideoVersion(version_id="vv_existing", status="done",
                      local=str(pdir / "output" / "videos" / "s01_c01.mp4"),
                      url="https://mock.agnes/s01.mp4",
                      seconds_returned=3.4, size_returned="1280x704")
    sh = Shot(shot_id="s01_c01", scene_id="s01", index=1, description="旧描述",
              prompt_en="old prompt", link_mode="i2v",
              first_frame=ImageAsset(candidates=[Asset(local=str(pdir / "output" / "frames" / "s01_first.png"),
                                                       url="https://mock.agnes/s01.png", status="done")],
                                     selected_index=0, status="done"),
              first_frame_batches=[ff_batch], selected_first_frame="ff_existing_1",
              video_versions=[vv], final_video="vv_existing",
              video=VideoAsset(num_frames=81, frame_rate=24, status="done",
                               local=str(pdir / "output" / "videos" / "s01_c01.mp4")))
    proj = Project(name=pname, style=StyleAnchor(aspect_ratio="16:9", size_tier="1K"),
                   scenes=[Scene(scene_id="s01", shots=[sh])])
    ProjectStore(pdir).save(proj)


@pytest.fixture
def proj(client):
    _build(PROJECT)
    yield PROJECT
    shutil.rmtree(get_project_manager().get_project_dir(PROJECT), ignore_errors=True)


def test_new_fields_save_and_reload(client, proj):
    """新结构化字段经 PUT 保存后 GET 可回读（§20.1.20）。"""
    r = client.put(f"/api/project/{proj}/shot/s01_c01", json={
        "opening_state": "机甲前倾", "camera_angle": "low angle", "composition": "rule of thirds",
        "required_elements": ["爆炸火光"], "target_duration_s": 5.0,
        "last_frame_description": "结束画面", "video_prompt_en": "video en",
        "first_frame_prompt_override": "manual ff", "first_frame_prompt_sig": "abc",
    })
    assert r.status_code == 200, r.text
    s = client.get(f"/api/project/{proj}/shots").json()["scenes"][0]["shots"][0]
    assert s["opening_state"] == "机甲前倾"
    assert s["camera_angle"] == "low angle"
    assert s["composition"] == "rule of thirds"
    assert s["required_elements"] == ["爆炸火光"]
    assert s["target_duration_s"] == 5.0
    assert s["last_frame_description"] == "结束画面"
    assert s["video_prompt_en"] == "video en"
    assert s["first_frame_prompt_override"] == "manual ff"
    assert s["first_frame_prompt_sig"] == "abc"


def test_update_shot_preserves_multibatch_and_pointers(client, proj):
    """PUT /shot 不覆盖多批次历史与选择指针。"""
    r = client.put(f"/api/project/{proj}/shot/s01_c01", json={
        "opening_state": "新值", "description": "改描述",
    })
    assert r.status_code == 200, r.text
    s = client.get(f"/api/project/{proj}/shots").json()["scenes"][0]["shots"][0]
    # 多批次历史保留
    assert len(s["first_frame_batches"]) == 1
    assert s["first_frame_batches"][0]["batch_id"] == "ff_existing"
    assert s["first_frame_batches"][0]["candidates"][0]["candidate_id"] == "ff_existing_1"
    assert len(s["video_versions"]) == 1
    assert s["video_versions"][0]["version_id"] == "vv_existing"
    # 选择指针保留
    assert s["selected_first_frame"] == "ff_existing_1"
    assert s["final_video"] == "vv_existing"
    # 新值已写入
    assert s["opening_state"] == "新值"
    assert s["description"] == "改描述"


def test_legacy_project_loads_with_new_fields_defaults(client):
    """旧 project.json（无新字段）GET /shots 正常，新字段取默认。"""
    pname = "_test_wb_legacy"
    pdir = Path(projects_dir) / pname
    shutil.rmtree(pdir, ignore_errors=True)
    (pdir / "output").mkdir(parents=True)
    (pdir / "output" / "project.json").write_text(json.dumps({
        "name": pname, "schema_version": 2,
        "style": {"art_style": "anime", "aspect_ratio": "16:9", "size_tier": "1K"},
        "characters": [],
        "scenes": [{"scene_id": "s01", "shots": [
            {"shot_id": "s01_c01", "scene_id": "s01", "index": 1, "description": "旧",
             "prompt_en": "old", "link_mode": "i2v",
             "first_frame": {}, "last_frame": {}, "audio": {}, "video": {"num_frames": 81, "frame_rate": 24}}
        ]}],
    }, ensure_ascii=False), encoding="utf-8")
    try:
        s = client.get(f"/api/project/{pname}/shots").json()["scenes"][0]["shots"][0]
        assert s["opening_state"] == ""
        assert s["required_elements"] == []
        assert s["target_duration_s"] is None
        assert s["camera_angle"] == ""
        assert s["prompt_en"] == "old"           # 旧字段保留
    finally:
        shutil.rmtree(pdir, ignore_errors=True)

