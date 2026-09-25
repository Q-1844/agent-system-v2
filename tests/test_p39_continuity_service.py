# Phase 39（39.md §6.1/§6.3/§6.5）：continuity_service 确定性测试。
# 核心：MAE 算法与 Phase38 人工实证脚本（_37_fable/_38_*.py 的 mae()）逐字同口径——
# 历史三档基线（≈2 / 26-60 / 90+）只在同算法下有效。
import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from src.core.models import Asset, Project, Scene, Shot, ShotStatus, StyleAnchor, VideoVersion
from src.core.store import ProjectStore
from src.storyboard import continuity_service as cs


# ============ Phase38 参考实现（与 _37_fable/_38_generate.py:75-80 逐字同口径）============
def _reference_mae_38(a_path, b_path, w=128, h=128):
    from PIL import Image
    ia = Image.open(a_path).convert("L").resize((w, h))
    ib = Image.open(b_path).convert("L").resize((w, h))
    pa, pb = list(ia.getdata()), list(ib.getdata())
    return round(sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa), 1)


def _jpg(path: Path, color) -> Path:
    from PIL import Image
    Image.new("RGB", (64, 48), color).save(path, "JPEG", quality=95)
    return path


def _make_video(path: Path, color: str = "red", seconds: float = 1.0) -> Path:
    """ffmpeg 合成小视频（本仓库生成链路的硬依赖，真 ffmpeg）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c={color}:size=64x48:rate=8:duration={seconds}",
         "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr
    return path


def _proj(videos_dir: Path, tail_color=(20, 200, 60)) -> Project:
    """两镜项目：c01/c02 都有最终视频+实际尾帧；c01 尾帧绿，c02 视频红。"""
    v1 = _make_video(videos_dir / "s01_c01.mp4", color="red")
    v2 = _make_video(videos_dir / "s01_c02.mp4", color="blue")
    tail1 = _jpg(videos_dir / "s01_c01_end.jpg", tail_color)
    tail2 = _jpg(videos_dir / "s01_c02_end.jpg", tail_color)
    c01 = Shot(shot_id="s01_c01", scene_id="s01", index=1, description="A",
               final_video="vv_a", video_end_frame=Asset(local=str(tail1), url="https://h/e1.jpg",
                                                         status=ShotStatus.DONE.value),
               video_end_source_version="vv_a")
    c01.video_versions.append(VideoVersion(version_id="vv_a", local=str(v1),
                                           status=ShotStatus.DONE.value))
    c02 = Shot(shot_id="s01_c02", scene_id="s01", index=2, description="B",
               final_video="vv_b", source_shot_id="s01_c01",
               opening_source="inherit_previous",
               video_end_frame=Asset(local=str(tail2), url="https://h/e2.jpg",
                                     status=ShotStatus.DONE.value),
               video_end_source_version="vv_b")
    c02.video_versions.append(VideoVersion(version_id="vv_b", local=str(v2),
                                           status=ShotStatus.DONE.value))
    return Project(name="p39seam", style=StyleAnchor(),
                   scenes=[Scene(scene_id="s01", shots=[c01, c02])])


@pytest.fixture()
def env(tmp_path):
    pdir = tmp_path / "proj"
    pdir.mkdir()
    ProjectStore(pdir).save(_proj(pdir / "output" / "videos"))
    return pdir, ProjectStore(pdir).load()


# ============ MAE 算法一致性（39.md 测试重点 7）============
def test_mae_gray128_matches_phase38_reference(env):
    pdir, _ = env
    a = _jpg(pdir / "_a.jpg", (10, 10, 10))
    b = _jpg(pdir / "_b.jpg", (240, 240, 240))
    c = _jpg(pdir / "_c.jpg", (10, 10, 10))
    assert cs.mae_gray128(a, b) == _reference_mae_38(a, b)
    assert cs.mae_gray128(a, c) == _reference_mae_38(a, c) == 0.0
    assert cs.mae_gray128(a, b) > 100   # 深灰 vs 亮灰：大差异


def test_preprocessing_version_is_phase38():
    assert cs.MAE_PREPROCESSING_VERSION == "gray128_bicubic_v1_phase38"


# ============ 帧提取 / 采样（正式化 38 步骤 1 与 §6.5）============
def test_extract_video_frame_first_and_end(tmp_path):
    v = _make_video(tmp_path / "v.mp4")
    f1 = cs.extract_video_frame(v, tmp_path / "f_first.jpg", position="first")
    fe = cs.extract_video_frame(v, tmp_path / "f_end.jpg", position="end")
    f25 = cs.extract_video_frame(v, tmp_path / "f_025.jpg", position=0.25)
    for f in (f1, fe, f25):
        assert f.exists() and f.stat().st_size > 0


def test_sample_frames_five_positions(env):
    pdir, proj = env
    out = cs.sample_frames(pdir, proj, "s01_c02")
    assert [f["label"] for f in out] == list(cs.SAMPLE_POSITIONS)
    for f in out:
        assert Path(f["local"]).exists() and Path(f["local"]).stat().st_size > 0
        assert f["source_version"] == "vv_b"


def test_sample_frames_rejects_unknown_label(env):
    pdir, proj = env
    with pytest.raises(cs.ContinuityError) as ei:
        cs.sample_frames(pdir, proj, "s01_c02", positions=["halfway"])
    assert ei.value.code == "invalid"


# ============ 接缝测量（tail_to_first 与 end_to_end 分开）============
def test_measure_seam_happy_path(env):
    pdir, proj = env
    out = cs.measure_seam(pdir, proj, "s01_c02", include_end_to_end=True)
    assert out["ok"] and out["operation"] == "continuity.seam_measure"
    # 值与参考实现直接计算一致（左=上镜尾帧绿图；右=本镜视频第 0 帧）
    expected = _reference_mae_38(pdir / "output" / "videos" / "s01_c01_end.jpg",
                                 out["right"]["local"])
    assert out["tail_to_first_mae"] == expected
    assert out["end_to_end_mae"] is not None
    # 字段分开且语义明确（禁止混写）
    assert set(out) >= {"tail_to_first_mae", "end_to_end_mae",
                        "preprocessing_version", "left", "right"}
    assert out["left"]["kind"] == "video_end_frame"
    assert out["right"]["kind"] == "video_first_frame"
    assert out["left"]["shot_id"] == "s01_c01"
    assert out["right"]["shot_id"] == "s01_c02"
    assert out["left"]["source_version"] == "vv_a"
    assert "1.7-2.4" in out["baseline_hint"]


def test_measure_seam_default_no_end_to_end(env):
    pdir, proj = env
    out = cs.measure_seam(pdir, proj, "s01_c02")
    assert out["tail_to_first_mae"] is not None
    assert out["end_to_end_mae"] is None


def test_measure_seam_errors(env):
    pdir, proj = env
    with pytest.raises(cs.ContinuityError) as ei:
        cs.measure_seam(pdir, proj, "s01_c01")   # 第一镜没有上一镜
    assert ei.value.code == "prev_not_found"
    with pytest.raises(cs.ContinuityError) as ei:
        cs.measure_seam(pdir, proj, "s01_c99")
    assert ei.value.code == "shot_not_found"
    # 上镜尾帧未就绪 → fail closed
    proj2 = ProjectStore(pdir).load()
    proj2.get_shot("s01_c01").video_end_frame = Asset(status=ShotStatus.PENDING.value)
    with pytest.raises(cs.ContinuityError) as ei:
        cs.measure_seam(pdir, proj2, "s01_c02")
    assert ei.value.code == "prev_tail_not_ready"
    # 本镜无最终视频 → fail closed
    proj3 = ProjectStore(pdir).load()
    proj3.get_shot("s01_c02").final_video = None
    proj3.get_shot("s01_c02").video.local = None
    with pytest.raises(cs.ContinuityError) as ei:
        cs.measure_seam(pdir, proj3, "s01_c02")
    assert ei.value.code == "video_file_missing"


# ============ 提取+托管（host 注入，async 接口）============
def test_extract_shot_frame_hosting_async(tmp_path):
    class FakeHost:
        def __init__(self):
            self.uploaded = []

        async def upload(self, local):
            self.uploaded.append(local)
            return f"https://host/{Path(local).name}"

    pdir = tmp_path / "proj"
    pdir.mkdir()
    ProjectStore(pdir).save(_proj(pdir / "output" / "videos"))
    proj = ProjectStore(pdir).load()
    host = FakeHost()
    out = asyncio.run(cs.extract_shot_frame(pdir, proj, proj.get_shot("s01_c02"),
                                            position="first", tag="bridge_boundary",
                                            host=host))
    assert out["url"] == f"https://host/{Path(out['local']).name}"
    assert host.uploaded == [out["local"]]
    assert out["source"]["shot_id"] == "s01_c02"
    assert out["source"]["version_id"] == "vv_b"
    # 无 host → local-only（不 fake url；extract_shot_frame_local 是同步函数）
    out2 = cs.extract_shot_frame_local(pdir, proj, proj.get_shot("s01_c01"),
                                       position="end")
    assert out2["url"] is None and Path(out2["local"]).exists()
