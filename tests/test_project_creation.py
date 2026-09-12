"""项目创建测试（batch 0 后续）：新建即初始化 project.json + 重名 409 + 不覆盖旧数据。

覆盖规格：
- 新建后 output/project.json 存在
- ProjectStore.load() 立即加载
- 项目名正确
- schema_version 来自当前模型
- characters / scenes 为空
- 默认 style + aspect_ratio 来自当前 Project / StyleAnchor 模型
- 新项目 shots 接口返回 200 + 空
- 重复创建同名返回 409
- 409 后原 project.json 内容未变
- 存在 parsed_script.json 的旧项目不被空 project.json 覆盖
"""
import json
import shutil
from pathlib import Path

from deps import projects_dir
from src.core.store import ProjectStore
from src.core.models import Project, StyleAnchor

PREFIX = "_test_create_"


def _dir(name: str) -> Path:
    return Path(projects_dir) / name


def _cleanup(names):
    for n in names:
        d = _dir(n)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


def test_create_initializes_project_json(client):
    """新建后 project.json 存在；load() 即时加载；字段来自模型默认。"""
    name = PREFIX + "init"
    _cleanup([name])
    try:
        r = client.post("/api/projects/create",
                        params={"name": name, "render_family": "2d_animation"})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["success"] is True
        assert j["name"] == name

        pj = _dir(name) / "output" / "project.json"
        assert pj.exists(), "project.json 未创建"

        project = ProjectStore(_dir(name)).load()
        assert project.name == name
        # schema_version 来自当前模型（不硬编码）
        assert project.schema_version == Project(name="_").schema_version
        assert project.characters == []
        assert project.scenes == []
        # 默认 style / aspect_ratio 来自 StyleAnchor 模型
        assert project.style.aspect_ratio == StyleAnchor().aspect_ratio
        assert project.style.art_style == StyleAnchor().art_style
        assert project.style.size_tier == StyleAnchor().size_tier
        # next_scene_seq 用 dataclass 默认（Project 已有该字段）
        assert project.next_scene_seq == Project(name="_").next_scene_seq
    finally:
        _cleanup([name])


def test_new_project_shots_returns_200_empty(client):
    name = PREFIX + "shots"
    _cleanup([name])
    try:
        assert client.post("/api/projects/create",
                           params={"name": name, "render_family": "photorealistic"}
                           ).status_code == 200
        r = client.get(f"/api/project/{name}/shots")
        assert r.status_code == 200
        assert r.json()["scenes"] == []
    finally:
        _cleanup([name])


def test_duplicate_create_returns_409(client):
    name = PREFIX + "dup"
    _cleanup([name])
    try:
        assert client.post("/api/projects/create",
                           params={"name": name, "render_family": "stylized"}
                           ).status_code == 200
        pj = _dir(name) / "output" / "project.json"
        before = pj.read_text(encoding="utf-8")

        r2 = client.post("/api/projects/create",
                         params={"name": name, "render_family": "stylized"})
        assert r2.status_code == 409

        # 重复创建后原 project.json 内容未变
        assert pj.read_text(encoding="utf-8") == before, "重复创建改动了原 project.json"
    finally:
        _cleanup([name])


def test_parsed_script_not_overwritten(client):
    """已有 parsed_script.json（无 project.json）的目录，create 应 409，不写空 project.json。"""
    name = PREFIX + "legacy"
    _cleanup([name])
    try:
        d = _dir(name)
        (d / "output").mkdir(parents=True)
        ps = d / "output" / "parsed_script.json"
        ps.write_text(json.dumps({
            "project": {"name": name, "style": "anime", "size": "1152x768"},
            "characters": [],
            "scenes": [{"id": 1, "description": "legacy"}],
        }, ensure_ascii=False), encoding="utf-8")
        before = ps.read_text(encoding="utf-8")

        r = client.post("/api/projects/create",
                        params={"name": name, "render_family": "photorealistic"})
        assert r.status_code == 409

        # parsed_script.json 未变
        assert ps.read_text(encoding="utf-8") == before
        # 没有写入空 project.json（迁移入口保留）
        assert not (d / "output" / "project.json").exists()
    finally:
        _cleanup([name])


def test_create_requires_render_family(client):
    """P1 §6/T7：create contract 必须收到明确 render_family——缺省被请求校验拒绝
    （FastAPI 422），不静默默认 photorealistic。"""
    name = PREFIX + "nofamily"
    _cleanup([name])
    try:
        r = client.post("/api/projects/create", params={"name": name})
        assert r.status_code == 422, "缺 render_family 必须被契约拒绝"
        assert not _dir(name).exists(), "缺 render_family 的 create 不得建目录"
        # 显式空串同样拒绝（router 层 400，带 project_visual_profile_required 说明）
        r2 = client.post("/api/projects/create", params={"name": name, "render_family": ""})
        assert r2.status_code == 400
        assert "render_family" in r2.json()["detail"]
    finally:
        _cleanup([name])


def test_create_rejects_unknown_render_family(client):
    """P1 §4：render_family 必须是 5 值 enum 之一——非法值 400。"""
    name = PREFIX + "badfamily"
    _cleanup([name])
    try:
        r = client.post("/api/projects/create",
                        params={"name": name, "render_family": "watercolor"})
        assert r.status_code == 400
        assert not _dir(name).exists()
    finally:
        _cleanup([name])
