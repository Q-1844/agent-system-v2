"""测试页/灰色接口路由（PHASE_5 §5.2 test_pages 组，保留待用户确认是否删除）。

独立测试 HTML 页面（character_test/scene_test/storyboard_test.html，8000 端口）的调试后端：
- /character-test /storyboard-test /scene-test：返回测试 HTML
- /api/generate-image(-base64)：直接生成图（测试页用）
- /api/storyboard/generate(-with-ref/batch)：分镜生成测试
- /api/scene/save/list/load/delete：场景库管理
主前端（7777）不调这些接口。HANDOVER §八.3 待确认是否保留；删除前先 grep 测试 HTML。
"""
import json
import uuid
import base64 as _base64
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from deps import (
    logger, BASE_DIR, get_agnes_client, _AGNES_PROXY,
    _save_base64_image, _load_image_as_base64,
)

router = APIRouter()


@router.get("/character-test")
async def character_test_page():
    """角色三视图测试页"""
    html_path = BASE_DIR / "character_test.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    return {"error": "character_test.html not found"}


@router.get("/storyboard-test")
async def storyboard_test_page():
    """分镜生成测试页"""
    html_path = BASE_DIR / "storyboard_test.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    return {"error": "storyboard_test.html not found"}


@router.get("/scene-test")
async def scene_test_page():
    """场景库管理页"""
    html_path = BASE_DIR / "scene_test.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    return {"error": "scene_test.html not found"}


@router.post("/api/generate-image")
async def generate_image(request: Request):
    """直接生成图片（供测试页调用）"""
    try:
        agnes = get_agnes_client()
        body = await request.json()
        prompt = body.get("prompt", "")
        seed = body.get("seed")
        size = body.get("size", "1024x1024")

        if not prompt:
            raise HTTPException(status_code=400, detail="prompt 不能为空")

        result = await agnes.generate_image(
            prompt=prompt, seed=seed, size=size
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/generate-image-base64")
async def generate_image_base64(request: Request):
    """生成图片并返回 base64（避免 CORS 问题）"""
    try:
        agnes = get_agnes_client()
        body = await request.json()
        prompt = body.get("prompt", "")
        seed = body.get("seed")
        size = body.get("size", "1024x1024")

        if not prompt:
            raise HTTPException(status_code=400, detail="prompt 不能为空")

        result = await agnes.generate_image(
            prompt=prompt, seed=seed, size=size
        )

        if not result or "data" not in result or not result["data"]:
            raise HTTPException(500, "图片生成失败")

        url = result["data"][0].get("url")
        if not url:
            raise HTTPException(500, "返回数据中没有URL")

        # 后端下载图片并转换为 base64
        import httpx
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            img_resp = await client.get(url, timeout=60)
            img_resp.raise_for_status()
            image_bytes = img_resp.content

        image_base64 = _base64.b64encode(image_bytes).decode("utf-8")

        return {
            "success": True,
            "image_base64": image_base64,
            "prompt": prompt,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成图片失败: {e}")
        raise HTTPException(500, str(e))


# ============ 角色生成（独立操作） ============
# 这些接口用于角色生成页面，每步可单独调用

# ============ 分镜生成测试接口 ============

class StoryboardRequest(BaseModel):
    """分镜生成请求"""
    description: str           # 场景描述
    characters: list[str] = [] # 角色名称列表
    camera: str = ""           # 镜头描述
    lighting: str = ""         # 光照描述
    style: str = "anime, cinematic lighting, high quality"
    size: str = "1536x1024"    # 横版，适合场景
    image_refs: list[str] = [] # 参考图列表（base64）


class StoryboardBatchRequest(BaseModel):
    """批量分镜生成请求"""
    scenes: list[dict]         # 场景列表
    style: str = "anime, cinematic lighting, high quality"
    size: str = "1536x1024"


def _build_storyboard_prompt(
    description: str,
    characters: list[str] = None,
    camera: str = None,
    lighting: str = None,
    style: str = None,
) -> str:
    """构建分镜生成的 prompt"""
    parts = [
        description,
        style or "anime, cinematic lighting, high quality",
        "anime scene, detailed background, cinematic composition",
    ]

    if characters:
        char_str = ", ".join(characters)
        parts.append(f"featuring characters: {char_str}")

    if camera:
        parts.append(camera)

    if lighting:
        parts.append(lighting)

    return ", ".join(parts)


@router.post("/api/storyboard/generate")
async def generate_storyboard(req: StoryboardRequest):
    """
    生成单个分镜图

    返回: image_url, image_base64, prompt
    """
    agnes = get_agnes_client()

    # 构建 prompt
    prompt = _build_storyboard_prompt(
        description=req.description,
        characters=req.characters,
        camera=req.camera,
        lighting=req.lighting,
        style=req.style,
    )

    try:
        # 构建参考图
        image_refs = None
        if req.image_refs:
            image_refs = [f"data:image/png;base64,{ref}" for ref in req.image_refs]

        # 生成图片
        result = await agnes.generate_image(
            prompt=prompt,
            size=req.size,
            image_refs=image_refs,
        )

        if not result or "data" not in result or not result["data"]:
            raise HTTPException(500, "图片生成失败")

        url = result["data"][0].get("url")
        if not url:
            raise HTTPException(500, "返回数据中没有URL")

        # 下载图片
        import httpx
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            img_resp = await client.get(url, timeout=60)
            img_resp.raise_for_status()
            image_bytes = img_resp.content

        image_base64 = _base64.b64encode(image_bytes).decode("utf-8")

        return {
            "success": True,
            "image_url": url,
            "image_base64": image_base64,
            "prompt": prompt,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"分镜生成失败: {e}")
        raise HTTPException(500, str(e))


@router.post("/api/storyboard/generate-with-ref")
async def generate_storyboard_with_ref(
    description: str,
    ref_image_base64: str,
    style: str = "anime, cinematic lighting, high quality",
    size: str = "1536x1024",
):
    """
    使用参考图生成分镜（图生图）
    """
    agnes = get_agnes_client()

    # 构建 prompt
    prompt = f"Based on this character reference, create a scene: {description}, {style}, anime scene, detailed background"

    # 构建参考图
    image_ref = f"data:image/png;base64,{ref_image_base64}"

    try:
        result = await agnes.generate_image(
            prompt=prompt,
            size=size,
            image_refs=[image_ref],
        )

        if not result or "data" not in result or not result["data"]:
            raise HTTPException(500, "图片生成失败")

        url = result["data"][0].get("url")
        if not url:
            raise HTTPException(500, "返回数据中没有URL")

        # 下载图片
        import httpx
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            img_resp = await client.get(url, timeout=60)
            img_resp.raise_for_status()
            image_bytes = img_resp.content

        image_base64 = _base64.b64encode(image_bytes).decode("utf-8")

        return {
            "success": True,
            "image_url": url,
            "image_base64": image_base64,
            "prompt": prompt,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"分镜生成失败: {e}")
        raise HTTPException(500, str(e))


@router.post("/api/storyboard/batch")
async def generate_storyboard_batch(req: StoryboardBatchRequest):
    """
    批量生成分镜图

    返回: 每个场景的生成结果
    """
    agnes = get_agnes_client()

    results = []

    for scene in req.scenes:
        scene_id = scene.get("id", 0)
        description = scene.get("description", "")
        characters = scene.get("characters", [])
        camera = scene.get("camera", "")
        lighting = scene.get("lighting", "")

        # 构建 prompt
        prompt = _build_storyboard_prompt(
            description=description,
            characters=characters,
            camera=camera,
            lighting=lighting,
            style=req.style,
        )

        try:
            result = await agnes.generate_image(
                prompt=prompt,
                size=req.size,
            )

            if result and "data" in result and result["data"]:
                url = result["data"][0].get("url")
                if url:
                    # 下载图片
                    import httpx
                    async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
                        img_resp = await client.get(url, timeout=60)
                        img_resp.raise_for_status()
                        image_bytes = img_resp.content

                    image_base64 = _base64.b64encode(image_bytes).decode("utf-8")

                    results.append({
                        "scene_id": scene_id,
                        "success": True,
                        "image_url": url,
                        "image_base64": image_base64,
                        "prompt": prompt,
                    })
                    continue

        except Exception as e:
            logger.error(f"场景 {scene_id} 生成失败: {e}")

        results.append({
            "scene_id": scene_id,
            "success": False,
            "error": "生成失败",
            "prompt": prompt,
        })

    return {"results": results}


# ============ 场景库系统 ============

# 场景保存目录
SAVED_SCENES_DIR = BASE_DIR / "saved_scenes"
SAVED_SCENES_DIR.mkdir(parents=True, exist_ok=True)


class SaveSceneRequest(BaseModel):
    """保存场景请求"""
    name: str                    # 场景名称
    description: str             # 场景描述
    image_base64: str            # 场景图片 base64
    tags: list[str] = []         # 标签（如：室内、室外、白天、夜晚）


def _get_scene_dir(scene_id: str) -> Path:
    """获取场景保存目录"""
    return SAVED_SCENES_DIR / scene_id


def _load_scene_meta(scene_dir: Path) -> dict:
    """加载场景元数据"""
    meta_file = scene_dir / "meta.json"
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_scene_meta(scene_dir: Path, meta: dict):
    """保存场景元数据"""
    meta_file = scene_dir / "meta.json"
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _init_default_scenes():
    """初始化默认场景（如果场景库为空）"""
    try:
        existing = list(SAVED_SCENES_DIR.iterdir())
        if any(d.is_dir() for d in existing):
            logger.info("场景库已有数据，跳过初始化")
            return

        # 默认场景描述（不生成图片，只保存描述）
        # 场景图必须无人物，是纯背景/环境
        default_scenes = [
            {
                "name": "山顶云海",
                "description": "mountain top overlooking sea of clouds, golden sunrise, panoramic view, no humans, empty landscape, anime background",
                "tags": ["室外", "山顶", "晨光"],
            },
            {
                "name": "黑暗云层",
                "description": "dark cloudy sky, ominous atmosphere, red glow, dramatic lighting, no humans, empty sky, anime background",
                "tags": ["室外", "天空", "黑暗"],
            },
            {
                "name": "风暴战场",
                "description": "stormy battlefield, lightning flashes, dramatic clouds, no humans, empty battlefield, anime background",
                "tags": ["室外", "战场", "风暴"],
            },
            {
                "name": "宁静村庄",
                "description": "peaceful anime village, traditional japanese houses, cherry blossoms, sunny day, no humans, empty village, anime background",
                "tags": ["室外", "村庄", "白天"],
            },
            {
                "name": "神秘森林",
                "description": "mystical forest, glowing mushrooms, moonlight, no humans, empty forest, fantasy anime background",
                "tags": ["室外", "森林", "夜晚"],
            },
            {
                "name": "城堡大厅",
                "description": "grand castle hall, stone pillars, torches, medieval fantasy, no humans, empty hall, anime background",
                "tags": ["室内", "城堡", "火光"],
            },
        ]

        for scene in default_scenes:
            scene_id = f"{scene['name']}_{uuid.uuid4().hex[:8]}"
            scene_dir = _get_scene_dir(scene_id)
            scene_dir.mkdir(parents=True, exist_ok=True)

            meta = {
                "id": scene_id,
                "name": scene["name"],
                "description": scene["description"],
                "tags": scene["tags"],
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "has_image": False,  # 默认场景没有图片，需要用户生成
            }
            _save_scene_meta(scene_dir, meta)
            logger.info(f"已添加默认场景: {scene['name']}")

    except Exception as e:
        logger.error(f"初始化默认场景失败: {e}")


# 在启动时初始化默认场景
def init_default_scenes():
    """初始化场景库"""
    _init_default_scenes()


@router.post("/api/scene/save")
async def save_scene(req: SaveSceneRequest):
    """
    保存场景到场景库

    返回: scene_id
    """
    try:
        # 生成唯一ID
        scene_id = f"{req.name}_{uuid.uuid4().hex[:8]}"
        scene_dir = _get_scene_dir(scene_id)
        scene_dir.mkdir(parents=True, exist_ok=True)

        # 保存图片
        if req.image_base64:
            _save_base64_image(req.image_base64, scene_dir / "image.png")

        # 保存元数据
        meta = {
            "id": scene_id,
            "name": req.name,
            "description": req.description,
            "tags": req.tags,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "has_image": bool(req.image_base64),
        }
        _save_scene_meta(scene_dir, meta)

        logger.info(f"场景已保存: {scene_id}")
        return {
            "success": True,
            "scene_id": scene_id,
            "message": f"场景 '{req.name}' 已保存",
        }
    except Exception as e:
        logger.error(f"保存场景失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")


@router.get("/api/scene/list")
async def list_saved_scenes():
    """获取已保存的场景列表"""
    try:
        scenes = []
        if SAVED_SCENES_DIR.exists():
            for scene_dir in SAVED_SCENES_DIR.iterdir():
                if scene_dir.is_dir():
                    meta = _load_scene_meta(scene_dir)
                    if meta:
                        scenes.append(meta)

        # 按创建时间倒序排列
        scenes.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return {"scenes": scenes}
    except Exception as e:
        logger.error(f"获取场景列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/scene/load/{scene_id}")
async def load_scene(scene_id: str):
    """加载已保存的场景（包含图片）"""
    try:
        scene_dir = _get_scene_dir(scene_id)
        if not scene_dir.exists():
            raise HTTPException(status_code=404, detail="场景不存在")

        # 加载元数据
        meta = _load_scene_meta(scene_dir)

        # 加载图片
        image_base64 = _load_image_as_base64(scene_dir / "image.png")

        return {
            "success": True,
            "scene": {
                "id": meta.get("id"),
                "name": meta.get("name"),
                "description": meta.get("description"),
                "tags": meta.get("tags", []),
                "created_at": meta.get("created_at"),
                "image_base64": image_base64,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"加载场景失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/scene/{scene_id}")
async def delete_scene(scene_id: str):
    """删除已保存的场景"""
    try:
        scene_dir = _get_scene_dir(scene_id)
        if not scene_dir.exists():
            raise HTTPException(status_code=404, detail="场景不存在")

        # 删除所有文件
        import shutil
        shutil.rmtree(scene_dir)

        logger.info(f"场景已删除: {scene_id}")
        return {"success": True, "message": "场景已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除场景失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


