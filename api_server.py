"""
API服务器
提供REST API供前端调用
"""

# 强制UTF-8编码（解决Windows GBK编码emoji报错）
import os
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

import sys
import io
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 加载 .env 文件
from pathlib import Path as _Path
_env_path = _Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _key, _val = _key.strip(), _val.strip()
                if _key and _val and not os.environ.get(_key):
                    os.environ[_key] = _val

import asyncio
import json
import uuid
import base64 as _base64
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from src.pipeline.controller import PipelineController
from src.utils.project_manager import ProjectManager

# 共享状态/单例/getter/配置/路径都在 deps（阶段5③拆分：避免 api_server <-> router 循环导入）。
# 🔴 reassigned 单例（pipeline_controller/job_queue/_rehost_host）用 deps.X 属性访问，
#    不能 from deps import（会绑死 import 时的 None，startup 赋值后看不到）。
import deps
from deps import (
    logger, BASE_DIR, projects_dir, _APP_CFG, _AGNES_PROXY,
    JOB_HANDLERS, task_store, current_task, task_progress,
    get_controller, get_job_queue, get_rehost_host, _new_task,
    _read_parsed_script, _write_parsed_script,
    _save_base64_image, _load_image_as_base64,
)

app = FastAPI(title="动画分镜工作流 API", version="1.0.0")

# 静态文件挂载（projects_dir / BASE_DIR / 配置 / 单例 / getter 都在 deps.py）
app.mount("/projects", StaticFiles(directory=str(projects_dir)), name="projects")

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局状态、任务队列单例、重新托管单例、任务存储、getter、请求模型统已在 deps.py
# （pipeline_controller / job_queue / _rehost_host 用 deps.X 属性访问，见顶部 import 注释）。

# ============ 路由挂载（阶段5③拆分：各域路由在 routers/，按 PHASE_5 §5.2 顺序逐步搬入）============
from routers import system  # noqa: E402
from routers import projects  # noqa: E402
from routers import script  # noqa: E402
from routers import characters  # noqa: E402
from routers import compose  # noqa: E402
from routers import jobs  # noqa: E402
from routers import shots  # noqa: E402
app.include_router(system.router)
app.include_router(projects.router)
app.include_router(script.router)
app.include_router(characters.router)
app.include_router(compose.router)
app.include_router(jobs.router)
app.include_router(shots.router)


@app.on_event("startup")
async def startup():
    """启动时初始化"""
    try:
        # 先初始化限流器与配额单例（用 config 的值），再创建 PipelineController
        # （其 AgnesClient 会复用这两个进程级单例）
        from src.core.rate_limiter import RateLimiter
        from src.core.quota import QuotaLedger
        _agnes_cfg = _APP_CFG.get("agnes") or {}
        RateLimiter.get(_agnes_cfg.get("rpm"))
        QuotaLedger.get_default(BASE_DIR / "cache" / "quota.json",
                                limits=_APP_CFG.get("quota"))

        config_path = os.path.join(os.path.dirname(__file__), "config", "config.yaml")
        deps.pipeline_controller = PipelineController(config_path)
        logger.info("API服务器已启动")
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        # 即使初始化失败也允许服务器运行，返回友好错误

    # 初始化默认角色到角色库
    characters._init_default_characters()

    # 阶段 3：启动全局任务队列 + 崩溃恢复（恢复在后台跑，不阻塞服务就绪）
    try:
        from src.core.job_queue import JobQueue
        deps.job_queue = JobQueue(BASE_DIR / "cache" / "jobs.json", JOB_HANDLERS)
        # 🔴 清理孤儿 job（project/shot 已删除的 running/queued）--在 start route 前，孤儿不进 worker
        purged = deps.job_queue.purge_orphans(_job_target_exists)
        if purged:
            logger.info(f"启动时清理 {purged} 个孤儿 job")
        await deps.job_queue.start()
        asyncio.create_task(recover_pending_videos())
        logger.info("全局任务队列已就绪")
    except Exception as e:
        logger.error(f"任务队列初始化失败: {e}")


async def recover_pending_videos():
    """🔴 崩溃恢复（PHASE_3 §3.4）：重启后对 video.status==generating 且有 video_id
    的镜头先 poll_video 查一次，不盲目重提交（省 60s + 不重扣配额）。
    已完成 -> 下载入库；仍在跑/失效 -> 标 queued 并入队由 video handler 续轮询。"""
    try:
        controller = deps.pipeline_controller
        if controller is None:
            return
        from src.core.store import ProjectStore
        client = controller.agnes_client
        for pdir in projects_dir.iterdir():
            if not pdir.is_dir():
                continue
            if not (pdir / "output" / "project.json").exists():
                continue
            try:
                store = ProjectStore(pdir)
                p = store.load()
            except Exception:
                continue
            for sh in p.all_shots():
                if sh.video.status != "generating" or not sh.video.video_id:
                    continue
                vid = sh.video.video_id
                logger.info(f"恢复检查: {p.name}/{sh.shot_id} video_id={vid}")
                try:
                    res = await client.poll_video(vid, interval=5, max_wait=60)
                    dest = str(pdir / "output" / "videos" / f"{sh.shot_id}.mp4")
                    await client.download(res["url"], dest)
                    store.update_shot(sh.shot_id, lambda s, d=dest, r=res: (
                        s.video.mark_done(d, r["url"]),
                        setattr(s.video, "seconds_returned", r["seconds"]),
                        setattr(s.video, "size_returned", r["size"]),
                    ))
                    logger.info(f"恢复完成: {sh.shot_id}")
                except Exception as e:
                    logger.warning(f"恢复 {sh.shot_id} 未完成（{e}），重新入队续轮询")
                    store.update_shot(sh.shot_id,
                                      lambda s: setattr(s.video, "status", "queued"))
                    if "video" in JOB_HANDLERS and deps.job_queue is not None:
                        deps.job_queue.submit(p.name, "video", sh.shot_id)
    except Exception as e:
        logger.error(f"recover_pending_videos 失败: {e}")


def _job_target_exists(project: Optional[str], shot_id: Optional[str]) -> bool:
    """判断 job 的 target 是否还存在（清理孤儿 job 用）。project/shot 任一不存在 -> False。
    无 project 的 job（如全局 compose）不算孤儿；controller 未就绪时不误清。"""
    if not project:
        return True
    ctrl = deps.pipeline_controller
    if ctrl is None:
        return True
    try:
        pdir = ctrl.project_manager.get_project_dir(project)
    except Exception:
        return False
    if not (pdir / "output" / "project.json").exists():
        return False
    if shot_id:
        from src.core.store import ProjectStore
        try:
            return ProjectStore(pdir).load().get_shot(shot_id) is not None
        except Exception:
            return False
    return True


@app.get("/character-test")
async def character_test_page():
    """角色三视图测试页"""
    html_path = BASE_DIR / "character_test.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    return {"error": "character_test.html not found"}


@app.get("/storyboard-test")
async def storyboard_test_page():
    """分镜生成测试页"""
    html_path = BASE_DIR / "storyboard_test.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    return {"error": "storyboard_test.html not found"}


@app.get("/scene-test")
async def scene_test_page():
    """场景库管理页"""
    html_path = BASE_DIR / "scene_test.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    return {"error": "scene_test.html not found"}


@app.post("/api/generate-image")
async def generate_image(request: Request):
    """直接生成图片（供测试页调用）"""
    try:
        controller = get_controller()
        body = await request.json()
        prompt = body.get("prompt", "")
        seed = body.get("seed")
        size = body.get("size", "1024x1024")

        if not prompt:
            raise HTTPException(status_code=400, detail="prompt 不能为空")

        result = await controller.agnes_client.generate_image(
            prompt=prompt, seed=seed, size=size
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-image-base64")
async def generate_image_base64(request: Request):
    """生成图片并返回 base64（避免 CORS 问题）"""
    try:
        controller = get_controller()
        body = await request.json()
        prompt = body.get("prompt", "")
        seed = body.get("seed")
        size = body.get("size", "1024x1024")

        if not prompt:
            raise HTTPException(status_code=400, detail="prompt 不能为空")

        result = await controller.agnes_client.generate_image(
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


@app.post("/api/storyboard/generate")
async def generate_storyboard(req: StoryboardRequest):
    """
    生成单个分镜图

    返回: image_url, image_base64, prompt
    """
    try:
        controller = get_controller()
    except HTTPException:
        raise

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
        result = await controller.agnes_client.generate_image(
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


@app.post("/api/storyboard/generate-with-ref")
async def generate_storyboard_with_ref(
    description: str,
    ref_image_base64: str,
    style: str = "anime, cinematic lighting, high quality",
    size: str = "1536x1024",
):
    """
    使用参考图生成分镜（图生图）
    """
    try:
        controller = get_controller()
    except HTTPException:
        raise

    # 构建 prompt
    prompt = f"Based on this character reference, create a scene: {description}, {style}, anime scene, detailed background"

    # 构建参考图
    image_ref = f"data:image/png;base64,{ref_image_base64}"

    try:
        result = await controller.agnes_client.generate_image(
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


@app.post("/api/storyboard/batch")
async def generate_storyboard_batch(req: StoryboardBatchRequest):
    """
    批量生成分镜图

    返回: 每个场景的生成结果
    """
    try:
        controller = get_controller()
    except HTTPException:
        raise

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
            result = await controller.agnes_client.generate_image(
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
@app.on_event("startup")
async def init_scenes():
    """初始化场景库"""
    _init_default_scenes()


@app.post("/api/scene/save")
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


@app.get("/api/scene/list")
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


@app.get("/api/scene/load/{scene_id}")
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


@app.delete("/api/scene/{scene_id}")
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
