# tests/test_44b_assembly_fixture.py
# Phase 44B 共享 fixture：带 3 个 done final_video 的 tmp 项目 + 冻结快照 manifest。
# 覆盖：同组（s01_c01→s01_c02）+ 跨组（s01_c02→s02_c01）两种切点。
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.core.models import Project, Scene, Shot, VideoVersion
from src.core.store import ProjectStore

SHOT_IDS = ("s01_c01", "s01_c02", "s02_c01")
SOURCE_DURATION_MS = 8000


def build_media_project(tmp_path) -> dict:
    """每个 shot 一个 done VideoVersion，local 指向真实 dummy 文件（返回 sha 映射）。"""
    files: dict = {}
    vv_by_shot = {}
    for sid in SHOT_IDS:
        f = tmp_path / "media" / f"{sid}.mp4"
        f.parent.mkdir(parents=True, exist_ok=True)
        data = f"BYTES-FOR-44B-{sid}".encode("utf-8")
        f.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        files[sid] = {"path": str(f), "sha256": sha}
        vv_by_shot[sid] = VideoVersion(
            version_id=f"vv_{sid.replace('_', '')}", status="done", local=str(f))

    def _shot(sid: str, scene: str, index: int) -> Shot:
        return Shot(shot_id=sid, scene_id=scene, index=index,
                    description=f"测试镜 {sid}",
                    video_versions=[vv_by_shot[sid]], final_video=vv_by_shot[sid].version_id)

    proj = Project(name="p44b")
    proj.scenes = [
        Scene(scene_id="s01", title="场一",
              shots=[_shot("s01_c01", "s01", 1), _shot("s01_c02", "s01", 2)]),
        Scene(scene_id="s02", title="场二",
              shots=[_shot("s02_c01", "s02", 1)]),
    ]
    ProjectStore(tmp_path).save(proj)
    return files


def build_snapshot_manifest(files: dict, *, mode: str = "formal",
                            snapshot_id: str = "snap_44b_test") -> dict:
    shots = []
    for i, sid in enumerate(SHOT_IDS, start=1):
        scene = "s01" if sid.startswith("s01") else "s02"
        shots.append({
            "order": i, "scene_id": scene, "shot_id": sid,
            "video_version_id": f"vv_{sid.replace('_', '')}",
            "approved_status": "user_locked",
            "verdict_source": "44B 测试 fixture",
            "media_uri": files[sid]["path"],
            "media_sha256": files[sid]["sha256"],
            "snapshot_media": files[sid]["path"],
            "duration_probe": {"duration": SOURCE_DURATION_MS / 1000, "width": 1280,
                               "height": 720, "fps": "24/1", "video_codec": "h264",
                               "has_audio": True, "audio_codec": "aac"},
        })
    return {"project_id": "p44b", "snapshot_id": snapshot_id, "mode": mode,
            "shots": shots}


def write_snapshot_file(tmp_path, manifest: dict) -> Path:
    """把快照写到 tmp_path/_44a/assembly_input_manifest_formal.json
    （_snapshot_load 的默认解析路径——repo_root 探测以 _44a 目录存在为准）。"""
    d = tmp_path / "_44a"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "assembly_input_manifest_formal.json"
    f.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return f


def fake_probe(path: str) -> dict:
    """零 ffmpeg 伪探测（44B 测试统一源时长 8000ms，1280x720@24，有音轨）。"""
    return {"duration_ms": SOURCE_DURATION_MS, "has_video": True, "width": 1280,
            "height": 720, "fps": 24.0, "vcodec": "h264", "pix_fmt": "yuv420p",
            "has_audio": True, "acodec": "aac", "sample_rate": 32000, "channels": 2}
