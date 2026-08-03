"""分镜列表路由（batch 0）：保存对账。

PUT /api/project/{p}/scenes 按 scene_id 做对账（绝不做数组位置 diff）：
- 请求有 + 磁盘有：更新 title，镜头及所有产物原样保留
- 请求有 + 磁盘无（scene_id=null）：新增，分配 s{next_scene_seq:02d} 后自增
- 磁盘有 + 请求无：删除，清理其镜头产物文件
- 顺序变化：仅重写 scenes 数组顺序，不触发产物增删

scene_id 是身份证号，一经分配任何操作都不改（拖拽只换数组顺序）。
详见 PRODUCT_FLOW_DESIGN §一、§二。
"""
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import deps
from deps import get_project_manager, logger, BASE_DIR

router = APIRouter()


class SceneItem(BaseModel):
    scene_id: Optional[str] = None   # null = 新增（后端按 next_scene_seq 分配）
    summary: str = ""                # 分镜故事文本
    characters: List[str] = []       # 出场角色名（来自当前项目角色）


class SaveScenesRequest(BaseModel):
    scenes: List[SceneItem]
    story_text: Optional[str] = None   # 项目级完整故事（None=不改动）


def _cleanup_deleted_scene_products(project_dir: Path, deleted_scenes: list):
    """删除已删除分镜下所有镜头的产物文件（视频/音频/首帧/尾帧）。
    路径可能是相对 BASE_DIR 的（如 projects\\xxx\\output\\...），统一解析。"""
    for sc in deleted_scenes:
        for sh in sc.shots:
            paths = []
            if sh.video and sh.video.local:
                paths.append(sh.video.local)
            if sh.audio and sh.audio.local:
                paths.append(sh.audio.local)
            for img in (sh.first_frame, sh.last_frame):
                if img and img.candidates:
                    for c in img.candidates:
                        if c.local:
                            paths.append(c.local)
            for p in paths:
                try:
                    pp = Path(p)
                    if not pp.is_absolute():
                        pp = BASE_DIR / pp
                    pp = pp.resolve()
                    # 安全护栏：只删本项目目录内的文件，防止 stale 路径误删其他项目数据
                    try:
                        pp.relative_to(project_dir.resolve())
                    except ValueError:
                        logger.warning(f"跳过清理（不在项目目录内，可能 stale 路径）: {pp}")
                        continue
                    if pp.exists():
                        os.remove(pp)
                except Exception as e:
                    logger.warning(f"清理产物失败 {p}: {e}")


@router.put("/api/project/{project_name}/scenes")
async def save_scenes(project_name: str, req: SaveScenesRequest):
    """保存分镜列表，按 scene_id 对账。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        from src.core.models import Scene
        store = ProjectStore(project_dir)
        project = store.load()

        existing = {sc.scene_id: sc for sc in project.scenes}
        request_ids = {s.scene_id for s in req.scenes if s.scene_id}

        # 待删除：磁盘有、请求无
        deleted_scenes = [sc for sid, sc in existing.items() if sid not in request_ids]

        new_scenes = []
        seen = set()
        for item in req.scenes:
            sid = item.scene_id
            if sid and sid in existing and sid not in seen:
                # 更新：复用现有 Scene（保留 shots/产物），只改 title
                sc = existing[sid]
                sc.summary = item.summary
                sc.characters = list(item.characters)
                new_scenes.append(sc)
                seen.add(sid)
            else:
                # 新增：scene_id 为 null，或请求了不存在/重复的 id —— 一律新分配，绝不顶旧号
                new_sid = f"s{project.next_scene_seq:02d}"
                project.next_scene_seq += 1
                new_scenes.append(Scene(scene_id=new_sid, summary=item.summary,
                                        characters=list(item.characters), shots=[]))
                seen.add(new_sid)

        project.scenes = new_scenes
        if req.story_text is not None:
            project.story_text = req.story_text
        store.save(project)

        # 删除已落盘后，清理孤立产物文件
        if deleted_scenes:
            _cleanup_deleted_scene_products(project_dir, deleted_scenes)

        logger.info(
            f"分镜列表已保存: {project_name}, 保留{len(new_scenes)}条, "
            f"删除{len(deleted_scenes)}条, next_scene_seq={project.next_scene_seq}"
        )
        return {
            "ok": True,
            "scenes": [{"scene_id": sc.scene_id, "summary": sc.summary,
                        "characters": list(sc.characters)} for sc in new_scenes],
        }
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存分镜列表失败: {e}")
        raise HTTPException(500, str(e))
