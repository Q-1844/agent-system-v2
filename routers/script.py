"""剧本路由：保存 YAML 剧本并解析为 parsed_script.json（PHASE_5 §5.2 script 组）。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from deps import get_agnes_client, get_project_manager, logger, _read_parsed_script, _write_parsed_script

router = APIRouter()


class SaveScriptRequest(BaseModel):
    script_content: str
    enhance: bool = False  # 是否用AI增强场景描述（需Agnes，慢）


@router.post("/api/project/{project_name}/script")
async def save_project_script(project_name: str, req: SaveScriptRequest):
    """保存剧本YAML并解析为 parsed_script.json（保留已有生成结果与角色参考图）"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)

        input_dir = project_dir / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        script_path = input_dir / f"{project_name}.yaml"
        script_path.write_text(req.script_content, encoding="utf-8")

        from src.pipeline.script_parser import ScriptParser
        parser = ScriptParser(get_agnes_client())
        parsed = await parser.parse(str(script_path))

        # 保留已有场景生成结果
        existing = _read_parsed_script(project_dir)
        existing_scenes = {s["id"]: s for s in existing.get("scenes", [])}
        for scene in parsed.get("scenes", []):
            sid = scene.get("id")
            if sid in existing_scenes:
                old = existing_scenes[sid]
                for key in ("image_path", "score", "audio_path", "subtitle_path",
                            "video_path", "status", "transitions", "enhanced_prompt"):
                    if key in old and key not in scene:
                        scene[key] = old[key]
                if old.get("status") == "completed":
                    scene["status"] = "completed"
        # 保留已有角色 reference_images（角色页绑定的图不丢）
        existing_chars = {c["name"]: c for c in existing.get("characters", [])}
        for char in parsed.get("characters", []):
            if char["name"] in existing_chars and existing_chars[char["name"]].get("reference_images"):
                char["reference_images"] = existing_chars[char["name"]]["reference_images"]

        if req.enhance:
            parsed = await parser.enhance_descriptions(parsed)

        _write_parsed_script(project_dir, parsed)
        logger.info(f"剧本已保存: {project_name}, 角色{len(parsed.get('characters', []))} 场景{len(parsed.get('scenes', []))}")
        return {
            "success": True,
            "characters_count": len(parsed.get("characters", [])),
            "scenes_count": len(parsed.get("scenes", [])),
            "message": "剧本已保存并解析",
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"剧本格式错误: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存剧本失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
