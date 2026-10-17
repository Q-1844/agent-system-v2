"""批次3：shot 生成请求规则测试（提示词.md §20.1 9/13/14/15/16/17）。

用 MockAgnes 拦截真实 HTTP 请求体，断言：
- 图片请求不含 seed / negative_prompt（§4.1，测试15）
- 视频请求 seed 为空（None）时不发送（测试13）
- 视频请求 negative_prompt 为空时不发送（测试14）
- 用户目标时长 -> num_frames 满足 8n+1 且 <= 分辨率上限（测试16/17）
- 尾帧 image_refs 首帧始终第一张 + 含角色参考；无角色只传首帧（测试9）
"""
import time
import shutil
from pathlib import Path
import pytest

import deps
from deps import get_project_manager
from tests.mock_agnes import MockAgnes, make_agnes
from src.api.agnes_client import max_frames_for


PROJECT = "_test_req_rules"


def _force_rmtree(p, tries=10):
    """Windows 下异步 job 下载文件句柄可能短暂未释放，重试 rmtree。"""
    import time as _t
    for _ in range(tries):
        shutil.rmtree(p, ignore_errors=True)
        if not Path(p).exists():
            return
        _t.sleep(0.4)


def _build(pname):
    import shutil
    from src.core.store import ProjectStore
    from src.core.models import (Project, Scene, Shot, StyleAnchor, ImageAsset,
                                 Asset, AudioAsset, VideoAsset, Character)
    pm = get_project_manager()
    _force_rmtree(pm.base_dir / pname)
    pdir = pm.create_project(pname)
    alan = Character(name="Alan", description_en="a boy",
                     views={"front": Asset(url="https://mock.agnes/alan.png", status="done")})
    # s01: 待首帧（无角色文生图）-> 测图片不含 seed
    sh1 = Shot(shot_id="s01_c01", scene_id="s01", index=1,
               description="首帧", prompt_en="a cat", link_mode="i2v", first_frame=ImageAsset())
    # s02: i2v 已有首帧+音频 -> 测视频 seed/negative/duration
    ff2 = ImageAsset(candidates=[Asset(local=str(pdir/"output"/"frames"/"s02_first.png"),
                                       url="https://mock.agnes/s02.png", status="done")],
                     selected_index=0, status="done")
    sh2 = Shot(shot_id="s02_c01", scene_id="s02", index=1,
               description="视频", prompt_en="a dog running", link_mode="i2v", dialogue="跑",
               characters=["Alan"],
               first_frame=ff2,
               audio=AudioAsset(local=str(pdir/"output"/"audio"/"s02_c01.mp3"),
                                status="done", duration_ms=2000),
               video=VideoAsset(num_frames=97, frame_rate=24))
    # s03: keyframes 已选首帧 + 角色 Alan -> 测尾帧 refs 顺序
    ff3 = ImageAsset(candidates=[Asset(local=str(pdir/"output"/"frames"/"s03_first.png"),
                                       url="https://mock.agnes/s03.png", status="done")],
                     selected_index=0, status="done")
    sh3 = Shot(shot_id="s03_c01", scene_id="s03", index=1,
               description="尾帧", prompt_en="a scene", link_mode="keyframes",
               characters=["Alan"], first_frame=ff3)
    # s04: keyframes 已选首帧，无角色 -> 测无角色尾帧只传首帧
    ff4 = ImageAsset(candidates=[Asset(local=str(pdir/"output"/"frames"/"s04_first.png"),
                                       url="https://mock.agnes/s04.png", status="done")],
                     selected_index=0, status="done")
    sh4 = Shot(shot_id="s04_c01", scene_id="s04", index=1,
               description="无角色尾帧", prompt_en="a scene", link_mode="keyframes",
               first_frame=ff4)
    from src.core.models import NativeAVSettings
    proj = Project(name=pname, style=StyleAnchor(aspect_ratio="16:9", size_tier="1K"),
                   native_av=NativeAVSettings(spoken_language="Mandarin Chinese"),
                   characters=[alan],
                   scenes=[Scene(scene_id="s01", shots=[sh1]), Scene(scene_id="s02", shots=[sh2]),
                           Scene(scene_id="s03", shots=[sh3]), Scene(scene_id="s04", shots=[sh4])])
    ProjectStore(pdir).save(proj)


@pytest.fixture
def proj(client):
    _build(PROJECT)
    yield PROJECT
    time.sleep(0.3)   # 让异步 job 文件句柄释放（Windows）
    _force_rmtree(get_project_manager().get_project_dir(PROJECT))


@pytest.fixture
def mock_agnes(client):
    mock = MockAgnes(poll_in_progress=0)
    agnes = make_agnes(mock)
    real = deps.agnes_client
    deps.agnes_client = agnes
    yield mock
    deps.agnes_client = real


def _wait_job(client, project, job_id, timeout=25):
    for _ in range(int(timeout / 0.1)):
        r = client.get(f"/api/project/{project}/jobs")
        job = next((j for j in r.json().get("jobs", []) if j.get("job_id") == job_id), None)
        if job and job.get("state") in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    return job


def test_image_request_no_seed_no_negative(client, proj, mock_agnes):
    """图片请求不含 seed / negative_prompt（§4.1，测试15）。"""
    r = client.post(f"/api/project/{proj}/shot/s01_c01/first-frame", json={"variants": 2})
    assert r.status_code == 200
    assert _wait_job(client, proj, r.json()["job_id"])["state"] == "done"
    bodies = mock_agnes.bodies("POST", "/v1/images/generations")
    assert bodies, "无图片请求"
    for b in bodies:
        assert "seed" not in b, f"图片请求含 seed: {b}"
        assert "negative_prompt" not in b, f"图片请求含 negative_prompt: {b}"


def test_video_request_no_seed_when_none(client, proj, mock_agnes):
    """seed 为空（None）时视频请求不发送 seed（测试13）。"""
    r = client.post(f"/api/project/{proj}/shot/s02_c01/video")
    assert r.status_code == 200
    assert _wait_job(client, proj, r.json()["job_id"])["state"] == "done"
    for b in mock_agnes.bodies("POST", "/v1/videos"):
        assert "seed" not in b, f"视频请求含 seed（应 None 不发）: {b}"


def test_video_request_no_negative_when_empty(client, proj, mock_agnes):
    """negative_prompt 为空时视频请求不发送（测试14）。"""
    r = client.post(f"/api/project/{proj}/shot/s02_c01/video")
    _wait_job(client, proj, r.json()["job_id"])
    for b in mock_agnes.bodies("POST", "/v1/videos"):
        assert "negative_prompt" not in b, f"视频请求含空 negative_prompt: {b}"


def test_video_target_duration_maps_to_seconds(client, proj, mock_agnes):
    """用户目标时长 -> 2.5 契约 seconds（2026-09-28：v2.0 num_frames 8n+1 契约
    随 v2.0 删除——时长经规划帧数反推为 4-12 秒字符串）。"""
    assert client.put(f"/api/project/{proj}/shot/s02_c01", json={"target_duration_s": 5.0}).status_code == 200
    r = client.post(f"/api/project/{proj}/shot/s02_c01/video")
    assert r.status_code == 200
    assert _wait_job(client, proj, r.json()["job_id"])["state"] == "done"
    bodies = mock_agnes.bodies("POST", "/v1/videos")
    assert bodies
    b = bodies[-1]
    assert "num_frames" not in b, f"2.5 契约不带 num_frames: {b}"
    assert b.get("seconds") == "5", f"目标 5.0s 应映射 seconds='5': {b}"


def test_last_frame_refs_first_frame_first_with_char(client, proj, mock_agnes):
    """尾帧 image_refs：首帧始终第一张 + 含角色参考（测试9）。"""
    r = client.post(f"/api/project/{proj}/shot/s03_c01/last-frame")
    assert r.status_code == 200
    job = _wait_job(client, proj, r.json()["job_id"])
    assert job and job["state"] == "done", f"尾帧 job 未完成: {job}"
    bodies = mock_agnes.bodies("POST", "/v1/images/generations")
    assert bodies
    b = bodies[-1]
    refs = b.get("image") or (b.get("extra_body") or {}).get("image") or []
    assert refs, f"尾帧请求无 image_refs: {b}"
    assert refs[0] == "https://mock.agnes/s03.png", f"首帧非第一张: {refs}"
    assert "https://mock.agnes/alan.png" in refs, f"未含角色参考: {refs}"


def test_last_frame_refs_no_char_only_first_frame(client, proj, mock_agnes):
    """无角色尾帧只传首帧（§11，测试7）。"""
    r = client.post(f"/api/project/{proj}/shot/s04_c01/last-frame")
    assert r.status_code == 200
    assert _wait_job(client, proj, r.json()["job_id"])["state"] == "done"
    bodies = mock_agnes.bodies("POST", "/v1/images/generations")
    b = bodies[-1]
    refs = b.get("image") or (b.get("extra_body") or {}).get("image") or []
    assert refs == ["https://mock.agnes/s04.png"], f"无角色尾帧应只传首帧: {refs}"
