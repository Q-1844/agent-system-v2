"""接口级测试（阶段5③-c）：TestClient + v2 项目 fixture + 单例 swap + job 轮询。

钉死 shot 级 handler 调 AgnesClient 的接线（first-frame/video）与 audio handler 接线：
  - first-frame：POST /first-frame -> JobQueue -> mock agnes generate_image+download -> 候选写回
  - video：POST /video -> JobQueue -> mock agnes create_video+poll+download -> video 产物写回
  - audio：POST /audio -> BackgroundTasks -> mock edge-tts -> timing 反推 num_frames 写回

mock_agnes_swap 把 deps.agnes_client 替成 MockTransport 实例（get_agnes_client 在请求时
读 global，per-test 替换安全）。audio 不走 Agnes，单独 mock edge-tts。
"""
import time
from pathlib import Path

import pytest

import deps
from deps import get_project_manager
from tests.mock_agnes import MockAgnes, make_agnes


PROJECT_NAME = "_test_interface"


def _build_project(pname: str) -> Path:
    """建临时 v2 项目：s01_c01 待生成首帧(i2v)，s02_c01 已有首帧+音频待生成视频(i2v)。"""
    import shutil
    from src.core.store import ProjectStore
    from src.core.models import (Project, Scene, Shot, StyleAnchor, ImageAsset,
                                 Asset, AudioAsset, VideoAsset)
    pm = get_project_manager()
    shutil.rmtree(pm.base_dir / pname, ignore_errors=True)
    pdir = pm.create_project(pname)   # 建目录 + 标准结构（get_project_dir 校验存在，须先建）
    # shot1: 空 first_frame，待生成
    sh1 = Shot(shot_id="s01_c01", scene_id="s01", index=1,
               description="测试首帧", prompt_en="a cat sitting, anime",
               link_mode="i2v", dialogue="喵",
               first_frame=ImageAsset())
    # shot2: 已有首帧 url + 音频 num_frames，待生成视频（i2v 省尾帧）
    ff2 = ImageAsset(
        candidates=[Asset(local=str(pdir / "output" / "frames" / "s02_first.png"),
                          url="https://mock.agnes/img/s02.png", status="done")],
        selected_index=0, status="done")
    sh2 = Shot(shot_id="s02_c01", scene_id="s02", index=1,
               description="测试视频", prompt_en="a dog running, anime",
               link_mode="i2v", dialogue="跑", characters=["小猫"],
               first_frame=ff2,
               audio=AudioAsset(local=str(pdir / "output" / "audio" / "s02_c01.mp3"),
                                status="done", duration_ms=2000),
               video=VideoAsset(num_frames=97, frame_rate=24))
    from src.core.models import NativeAVSettings
    proj = Project(name=pname, style=StyleAnchor(aspect_ratio="16:9", size_tier="1K"),
                   native_av=NativeAVSettings(spoken_language="Mandarin Chinese"),
                   scenes=[Scene(scene_id="s01", shots=[sh1]),
                           Scene(scene_id="s02", shots=[sh2])])
    ProjectStore(pdir).save(proj)
    return pdir


# ---------- fixtures ----------
@pytest.fixture
def v2_project(client):
    """临时 v2 项目，返回 project_name。teardown 删项目目录。"""
    import shutil
    _build_project(PROJECT_NAME)
    yield PROJECT_NAME
    shutil.rmtree(get_project_manager().get_project_dir(PROJECT_NAME), ignore_errors=True)


@pytest.fixture
def mock_agnes_swap(client):
    """替换 deps.agnes_client 为 MockTransport 实例（per-test），yield mock，恢复原值。
    poll_in_progress=0：video poll 第 1 次就 completed（省时间，handler 仍 sleep 5s）。"""
    mock = MockAgnes(poll_in_progress=0)
    agnes = make_agnes(mock)
    real = deps.agnes_client
    deps.agnes_client = agnes
    yield mock
    deps.agnes_client = real


@pytest.fixture
def mock_edge_tts(client, monkeypatch):
    """避免真实 edge-tts 网络调用：写占位 mp3 + 返回假 duration/words。"""
    from src.api import edge_tts_client

    async def fake_synthesize_full(self, text, *, voice, rate, volume, out_path):
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"mock-mp3")
        return {"path": str(p), "duration_ms": 2000,
                "words": [{"text": (text or "")[:5], "offset_ms": 0, "duration_ms": 2000}]}

    monkeypatch.setattr(edge_tts_client.EdgeTTSClient, "synthesize_full",
                        fake_synthesize_full)


def _wait_job(client, project, job_id, timeout=15):
    """轮询 /api/project/{p}/jobs 直到 job 终态，返回 job dict。"""
    job = None
    for _ in range(int(timeout / 0.1)):
        r = client.get(f"/api/project/{project}/jobs")
        jobs = r.json().get("jobs", [])
        job = next((j for j in jobs if j.get("job_id") == job_id), None)
        if job and job.get("state") in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    return job


# ---------- first-frame ----------
def test_first_frame_interface(client, v2_project, mock_agnes_swap):
    """POST /first-frame -> job done -> 2 候选写回（url+local 文件），status=pending 等点选"""
    r = client.post(f"/api/project/{v2_project}/shot/s01_c01/first-frame",
                    json={"variants": 2, "force": False})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    job = _wait_job(client, v2_project, job_id, timeout=10)
    assert job and job["state"] == "done", f"first-frame job 未完成: {job}"

    r = client.get(f"/api/project/{v2_project}/shots")
    sh1 = r.json()["scenes"][0]["shots"][0]
    ff = sh1["first_frame"]
    assert len(ff["candidates"]) == 2
    assert ff["status"] == "pending"                          # 等用户点选
    assert ff["candidates"][0]["url"] == mock_agnes_swap.image_url
    assert ff["candidates"][0]["status"] == "done"
    pdir = get_project_manager().get_project_dir(v2_project)
    assert (pdir / "output" / "frames" / "s01_c01_first_v1.png").exists()


# ---------- video ----------
def test_video_interface(client, v2_project, mock_agnes_swap):
    """POST /video -> job done -> video 产物写回（video_id/seconds/size/local mp4）"""
    r = client.post(f"/api/project/{v2_project}/shot/s02_c01/video")
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    job = _wait_job(client, v2_project, job_id, timeout=20)   # poll 5s + buffer
    assert job and job["state"] == "done", f"video job 未完成: {job}"

    r = client.get(f"/api/project/{v2_project}/shots")
    sh2 = r.json()["scenes"][1]["shots"][0]
    v = sh2["video"]
    assert v["video_id"] == mock_agnes_swap.video_id
    assert v["seconds_returned"] == 3.4
    assert v["size_returned"] == "1280x704"
    assert v["status"] == "done"
    pdir = get_project_manager().get_project_dir(v2_project)
    assert Path(v["local"]).exists()


# ---------- audio ----------
def test_audio_interface(client, v2_project, mock_edge_tts):
    """POST /audio -> task completed -> audio 写回 + num_frames 反推（8n+1）"""
    r = client.post(f"/api/project/{v2_project}/shot/s01_c01/audio",
                    json={"voice": "default", "rate": "+0%", "volume": "+0%"})
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    t = {}
    for _ in range(50):
        t = client.get(f"/api/task/{task_id}").json()
        if t.get("status") in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert t.get("status") == "completed", f"audio task 未完成: {t}"

    r = client.get(f"/api/project/{v2_project}/shots")
    sh1 = r.json()["scenes"][0]["shots"][0]
    a = sh1["audio"]
    assert a["status"] == "done"
    assert a["duration_ms"] == 2000
    assert a["local"]                                        # 路径写入
    nf = sh1["video"]["num_frames"]
    assert nf > 0
    assert (nf - 1) % 8 == 0                                 # 8n+1
