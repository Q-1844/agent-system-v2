# src/core/store.py
# 项目数据读写：原子写、v1->v2 迁移、读-改-写单个 shot。
# 🔴 硬性规则：所有生成任务写回结果必须走 store.update_shot()，禁止只改内存字典。
import json, shutil, subprocess, time
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Callable

from .models import Project, Scene, Character, StyleAnchor, Asset, Shot
from .atomic import atomic_write_json as _atomic_write_json  # 共享原子写；store 调用点不变（AI-6A §十四）


# _atomic_write_json 已迁移到 src/core/atomic.py（上方 import 提供同名别名，调用点不变）


def _ffprobe_duration_ms_sync(path: str) -> int:
    """同步 ffprobe 探音频时长（ms）。失败返回 0。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10)
        return int(float(r.stdout.strip()) * 1000)
    except Exception:
        return 0


def _backfill_shot_timing(shot: dict, fps: int = 24) -> bool:
    """检测 audio.local 有但 video.num_frames 缺失/0 -> ffprobe 探时长 -> plan_shot_timing 反推回填。
    不碰音频文件（音色/断句不变）。返回是否改动。
    🔴 v1 迁移数据 audio 有 local 但 num_frames=0，导致前端「已配音0.00s+尚未配音」矛盾。
    用现有音频 ffprobe 量时长反推，不要求用户重新配音。"""
    from .timing import plan_shot_timing
    audio = shot.get("audio") or {}
    video = shot.get("video") or {}
    local = audio.get("local")
    if not local or video.get("num_frames"):
        return False
    dur_ms = _ffprobe_duration_ms_sync(local)
    if dur_ms <= 0:
        return False
    timing = plan_shot_timing(dur_ms / 1000, fps)
    audio["duration_ms"] = dur_ms
    video["num_frames"] = timing.num_frames
    video["frame_rate"] = timing.frame_rate
    return True


def migrate_v1_to_v2(old: dict) -> dict:
    """把旧的 parsed_script.json 升级为 v2 结构。每个旧 scene 变成 1 个 scene + 1 个 shot。"""
    scenes = []
    for i, old_scene in enumerate(old.get("scenes", []), start=1):
        sid = f"s{i:02d}"
        # first_frame 用 ImageAsset(candidates+selected_index) 形状（红线12：支持2张点选）
        _ff_local = old_scene.get("image_path")
        _ff_url = old_scene.get("image_url")
        _ff_status = "done" if _ff_local else "pending"
        shot = {
            "shot_id": f"{sid}_c01", "scene_id": sid, "index": 1,
            "description": old_scene.get("description", ""),
            "prompt_en":   old_scene.get("prompt", ""),
            "camera":      old_scene.get("camera", ""),
            "lighting":    old_scene.get("lighting", ""),
            "motion":      old_scene.get("motion", ""),
            "characters":  old_scene.get("characters", []),
            "dialogue":    old_scene.get("dialogue", ""),
            "link_mode":   "i2v",
            # 旧字段挂到新结构上，一个都不丢
            "first_frame": {
                "candidates": [{"local": _ff_local, "url": _ff_url, "status": _ff_status}] if _ff_local else [],
                "selected_index": 0,
                "status": _ff_status,
            },
            "audio":       {"local": old_scene.get("audio_path"),
                            "status": "done" if old_scene.get("audio_path") else "pending"},
            "video":       {"local": old_scene.get("video_path"),
                            "status": "done" if old_scene.get("video_path") else "pending"},
            "subtitle_srt": old_scene.get("subtitle_path"),
            "status": "pending",
        }
        _backfill_shot_timing(shot)   # ffprobe 量现有音频时长反推 num_frames（不碰音频）
        scenes.append({"scene_id": sid, "title": old_scene.get("title", ""),
                       "summary": old_scene.get("description", ""),
                       "characters": old_scene.get("characters", []),
                       "shots": [shot]})
    return {
        "name": (old.get("project") or {}).get("name") or old.get("project_name") or "untitled",
        "schema_version": 2,
        "style": {"art_style": (old.get("project") or {}).get("style", "anime"),
                  "aspect_ratio": "16:9", "size_tier": "1K"},
        "characters": [
            {"name": c.get("name",""), "description_en": c.get("description",""),
             "views": {k: {"local": v, "status": "done"}
                       for k, v in (c.get("reference_images") or {}).items() if v}}
            for c in old.get("characters", [])
        ],
        "story_text": "",
        "scenes": scenes,
    }


class ProjectStore:
    def __init__(self, project_dir: Path):
        self.dir = Path(project_dir)
        self.path = self.dir / "output" / "project.json"
        self.legacy = self.dir / "output" / "parsed_script.json"

    def load(self) -> Project:
        raw = None
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        elif self.legacy.exists():
            old = json.loads(self.legacy.read_text(encoding="utf-8"))
            # 旧文件备份（不删原文件）：parsed_script.json -> parsed_script.v1.bak.json
            backup = self.legacy.parent / (self.legacy.stem + ".v1.bak.json")
            shutil.copy2(self.legacy, backup)
            raw = migrate_v1_to_v2(old)
            _atomic_write_json(self.path, raw)
        if raw is None:
            raise FileNotFoundError(f"项目数据不存在: {self.dir}")
        if raw.get("schema_version", 1) < 2:
            raw = migrate_v1_to_v2(raw)
            _atomic_write_json(self.path, raw)
        # backfill：已 v2 但 num_frames 没反推的（迁移在 backfill 加入前做的项目，如「我的动画短片」）
        changed = False
        for sc in raw.get("scenes", []):
            for sh in sc.get("shots", []):
                if _backfill_shot_timing(sh):
                    changed = True
        if changed:
            _atomic_write_json(self.path, raw)
        return self._from_dict(raw)

    def save(self, project: Project):
        project.updated_at = time.time()
        _atomic_write_json(self.path, self._to_dict(project))

    def update_shot(self, shot_id: str, mutator: Callable[[Shot], None]):
        """读-改-写单个 shot。所有生成任务写回都必须走这个方法，禁止直接改内存对象。"""
        p = self.load()
        shot = p.get_shot(shot_id)
        if shot is None:
            raise KeyError(shot_id)
        mutator(shot)
        self.save(p)
        return shot

    # ---- 序列化 / 反序列化 ----
    def _to_dict(self, project: Project) -> dict:
        return project.to_dict()

    def _from_dict(self, raw: dict) -> Project:
        return Project.from_dict(raw)
