# src/core/store.py
# 项目数据读写：原子写、v1->v2 迁移、读-改-写单个 shot。
# 🔴 硬性规则：所有生成任务写回结果必须走 store.update_shot()，禁止只改内存字典。
import json, os, shutil, tempfile, time
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Callable

from .models import Project, Scene, Character, StyleAnchor, Asset, Shot


def _atomic_write_json(path: Path, data: dict):
    """原子写：先写临时文件再 rename。防止生成中途崩溃导致 json 半截损坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)          # Windows 上 os.replace 也是原子的
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def migrate_v1_to_v2(old: dict) -> dict:
    """把旧的 parsed_script.json 升级为 v2 结构。每个旧 scene 变成 1 个 scene + 1 个 shot。"""
    scenes = []
    for i, old_scene in enumerate(old.get("scenes", []), start=1):
        sid = f"s{i:02d}"
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
            "first_frame": {"local": old_scene.get("image_path"),
                            "url":   old_scene.get("image_url"),
                            "status": "done" if old_scene.get("image_path") else "pending"},
            "audio":       {"local": old_scene.get("audio_path"),
                            "status": "done" if old_scene.get("audio_path") else "pending"},
            "video":       {"local": old_scene.get("video_path"),
                            "status": "done" if old_scene.get("video_path") else "pending"},
            "subtitle_srt": old_scene.get("subtitle_path"),
            "status": "pending",
        }
        scenes.append({"scene_id": sid, "title": old_scene.get("title", ""),
                       "summary": old_scene.get("description", ""), "shots": [shot]})
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
        return {
            "name": project.name,
            "schema_version": project.schema_version,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "style": asdict(project.style),
            "characters": [
                {"name": c.name, "description_en": c.description_en,
                 "views": {k: asdict(v) for k, v in c.views.items()}}
                for c in project.characters
            ],
            "scenes": [s.to_dict() for s in project.scenes],
        }

    def _from_dict(self, raw: dict) -> Project:
        raw = raw or {}
        style = StyleAnchor(**(raw.get("style") or {}))
        characters = []
        for c in raw.get("characters", []):
            views = {k: Asset(**v) for k, v in (c.get("views") or {}).items()}
            characters.append(Character(
                name=c.get("name", ""),
                description_en=c.get("description_en", ""),
                views=views,
            ))
        scenes = [Scene.from_dict(s) for s in raw.get("scenes", [])]
        return Project(
            name=raw.get("name", "untitled"),
            style=style,
            characters=characters,
            scenes=scenes,
            schema_version=raw.get("schema_version", 2),
            created_at=raw.get("created_at", time.time()),
            updated_at=raw.get("updated_at", time.time()),
        )
