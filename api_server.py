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
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from src.pipeline.controller import PipelineController
from src.utils.project_manager import ProjectManager
from src.utils.logger import setup_logger

logger = setup_logger("api_server")

app = FastAPI(title="动画分镜工作流 API", version="1.0.0")

# 使用绝对路径确保静态文件正确加载
BASE_DIR = Path(__file__).parent
projects_dir = BASE_DIR / "projects"
projects_dir.mkdir(parents=True, exist_ok=True)
app.mount("/projects", StaticFiles(directory=str(projects_dir)), name="projects")

# 全局配置（从 config.yaml 读取；Agnes 在 Cloudflare 后，国内需代理。Clash 默认 7890）
_AGNES_PROXY = None
_APP_CFG: dict = {}
try:
    import yaml as _yaml
    _APP_CFG = _yaml.safe_load((BASE_DIR / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    _AGNES_PROXY = (_APP_CFG.get("agnes") or {}).get("proxy") or None
except Exception as _e:
    logger.warning(f"读取配置失败: {_e}")

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局状态
pipeline_controller = None
current_task = None
task_progress = {}

# ============ 阶段 3：全局任务队列 ============
# JOB_HANDLERS 是可变 dict，各 handler 定义处注册（first_frame/last_frame/audio/video/compose）。
# startup 时用它在 cache/jobs.json 上构造 JobQueue 并启动 worker（1 视频串行 + 3 轻任务并发）。
JOB_HANDLERS: dict = {}
job_queue = None  # JobQueue 单例，startup 中创建


def get_job_queue():
    """安全获取全局队列。未就绪返回 503。"""
    if job_queue is None:
        raise HTTPException(503, "任务队列未就绪（服务启动中）")
    return job_queue


class ScriptRequest(BaseModel):
    """剧本请求"""
    project_name: str
    script_content: str
    mode: str = "interactive"


class TaskStatus(BaseModel):
    """任务状态"""
    task_id: str
    status: str  # pending, running, completed, failed
    progress: dict
    message: str


@app.on_event("startup")
async def startup():
    """启动时初始化"""
    global pipeline_controller
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
        pipeline_controller = PipelineController(config_path)
        logger.info("API服务器已启动")
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        # 即使初始化失败也允许服务器运行，返回友好错误

    # 初始化默认角色到角色库
    _init_default_characters()

    # 阶段 3：启动全局任务队列 + 崩溃恢复（恢复在后台跑，不阻塞服务就绪）
    global job_queue
    try:
        from src.core.job_queue import JobQueue
        job_queue = JobQueue(BASE_DIR / "cache" / "jobs.json", JOB_HANDLERS)
        await job_queue.start()
        asyncio.create_task(recover_pending_videos())
        logger.info("全局任务队列已就绪")
    except Exception as e:
        logger.error(f"任务队列初始化失败: {e}")


def get_controller():
    """安全获取控制器"""
    if pipeline_controller is None:
        raise HTTPException(
            status_code=503,
            detail="服务未就绪，Pipeline控制器初始化失败。请检查配置文件和API密钥。"
        )
    return pipeline_controller


async def recover_pending_videos():
    """🔴 崩溃恢复（PHASE_3 §3.4）：重启后对 video.status==generating 且有 video_id
    的镜头先 poll_video 查一次，不盲目重提交（省 60s + 不重扣配额）。
    已完成 -> 下载入库；仍在跑/失效 -> 标 queued 并入队由 video handler 续轮询。"""
    try:
        controller = pipeline_controller
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
                    if "video" in JOB_HANDLERS and job_queue is not None:
                        job_queue.submit(p.name, "video", sh.shot_id)
    except Exception as e:
        logger.error(f"recover_pending_videos 失败: {e}")


@app.get("/")
async def root():
    """根路径 - 返回简易版前端页面"""
    html_path = BASE_DIR / "simple.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    return {"message": "动画分镜工作流 API", "version": "1.0.0"}


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {
        "status": "ok" if pipeline_controller else "degraded",
        "controller_ready": pipeline_controller is not None,
    }


@app.get("/api/system/limits")
async def system_limits():
    """返回限流器各桶状态与每日配额用量（供前端排队面板展示）。
    阶段 1 新增只读接口，不改任何现有接口。"""
    from src.core.rate_limiter import RateLimiter
    from src.core.quota import QuotaLedger
    return {
        "limits": RateLimiter.get().snapshot(),
        "quota": QuotaLedger.get_default().snapshot(),
    }


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


@app.get("/api/image/{project_name}/{category}/{filename}")
async def serve_image(project_name: str, category: str, filename: str):
    """提供图片文件服务（带CORS支持）"""
    try:
        image_path = projects_dir / project_name / "output" / category / filename
        if not image_path.exists():
            raise HTTPException(status_code=404, detail="图片不存在")

        # 根据文件扩展名确定媒体类型
        ext = image_path.suffix.lower()
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        media_type = media_types.get(ext, "application/octet-stream")

        return FileResponse(
            path=str(image_path),
            media_type=media_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=3600",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects")
async def list_projects():
    """列出所有项目"""
    try:
        controller = get_controller()
        projects = controller.project_manager.list_projects()
        return {"projects": projects}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/projects/create")
async def create_project(name: str):
    """创建新项目"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.create_project(name)
        return {"message": "项目已创建", "path": str(project_dir)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{project_name}/progress")
async def get_project_progress(project_name: str):
    """获取项目进度"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        from src.utils.progress import ProgressTracker
        progress = ProgressTracker(str(project_dir))
        return progress.get_progress()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{project_name}/scenes")
async def get_project_scenes(project_name: str):
    """获取项目场景"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        script_file = project_dir / "output" / "parsed_script.json"

        if not script_file.exists():
            return {"scenes": []}

        import json
        with open(script_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {"scenes": data.get("scenes", [])}
    except FileNotFoundError:
        return {"scenes": []}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{project_name}/characters")
async def get_project_characters(project_name: str):
    """获取项目角色"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        script_file = project_dir / "output" / "parsed_script.json"

        if not script_file.exists():
            return {"characters": []}

        import json
        with open(script_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        chars = data.get("characters", [])
        # full -> back 兼容（旧数据视角命名，1.1 统一为 front/side/back）
        for c in chars:
            ri = c.get("reference_images", {})
            if isinstance(ri, dict) and "full" in ri and "back" not in ri:
                ri["back"] = ri.pop("full")
        return {"characters": chars}
    except FileNotFoundError:
        return {"characters": []}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{project_name}/outputs")
async def get_project_outputs(project_name: str):
    """获取项目输出文件"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        output_dir = project_dir / "output"

        outputs = {
            "characters": [],
            "storyboards": [],
            "videos": [],
            "audio": [],
            "subtitles": [],
        }

        # 扫描输出目录
        for category in outputs.keys():
            category_dir = output_dir / category
            if category_dir.exists():
                for file in category_dir.iterdir():
                    if file.is_file():
                        outputs[category].append({
                            "name": file.name,
                            "path": str(file),
                            "size": file.stat().st_size,
                        })

        # 检查HTML和报告
        html_file = output_dir / "storyboard.html"
        if html_file.exists():
            outputs["storyboard_html"] = str(html_file)

        report_file = output_dir / "project_report.json"
        if report_file.exists():
            outputs["project_report"] = str(report_file)

        return outputs
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 场景操作 ============

@app.post("/api/projects/{project_name}/scenes/{scene_id}/regenerate")
async def regenerate_scene(project_name: str, scene_id: int):
    """重新生成单个场景的分镜图"""
    try:
        controller = get_controller()

        # 重新运行分镜生成阶段（仅指定场景）
        project_dir = controller.project_manager.get_project_dir(project_name)
        script_file = project_dir / "output" / "parsed_script.json"

        if not script_file.exists():
            raise HTTPException(status_code=404, detail="项目数据不存在，请先运行工作流")

        import json
        with open(script_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 找到指定场景
        target_scene = None
        for scene in data.get("scenes", []):
            if scene["id"] == scene_id:
                target_scene = scene
                break

        if not target_scene:
            raise HTTPException(status_code=404, detail=f"场景 {scene_id} 不存在")

        # 重置场景状态
        target_scene["status"] = "pending"
        target_scene.pop("image_path", None)
        target_scene.pop("score", None)

        # 保存更新后的数据
        with open(script_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"场景 {scene_id} 已重置，等待重新生成")
        return {"message": f"场景 {scene_id} 已重置，请重新运行工作流", "scene_id": scene_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/projects/{project_name}/scenes/{scene_id}/confirm")
async def confirm_scene(project_name: str, scene_id: int):
    """确认场景（标记为已确认）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        script_file = project_dir / "output" / "parsed_script.json"

        if not script_file.exists():
            raise HTTPException(status_code=404, detail="项目数据不存在")

        import json
        with open(script_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        for scene in data.get("scenes", []):
            if scene["id"] == scene_id:
                scene["status"] = "confirmed"
                break
        else:
            raise HTTPException(status_code=404, detail=f"场景 {scene_id} 不存在")

        with open(script_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return {"message": f"场景 {scene_id} 已确认", "scene_id": scene_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 角色操作 ============

@app.post("/api/projects/{project_name}/characters/{character_name}/regenerate")
async def regenerate_character(
    project_name: str, character_name: str, background_tasks: BackgroundTasks
):
    """重新生成单个角色的参考图（真正触发生成，不是只重置）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)

        # 删除已有的角色图片
        char_dir = project_dir / "output" / "characters"
        if char_dir.exists():
            for f in char_dir.iterdir():
                if f.name.startswith(character_name):
                    f.unlink()
                    logger.info(f"已删除: {f}")

        # 读取角色数据
        script_file = project_dir / "output" / "parsed_script.json"
        if not script_file.exists():
            raise HTTPException(status_code=404, detail="项目数据不存在，请先运行工作流")

        import json
        with open(script_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 找到目标角色
        target_char = None
        for char in data.get("characters", []):
            if char["name"] == character_name:
                target_char = char
                char.pop("reference_images", None)
                break

        if not target_char:
            raise HTTPException(status_code=404, detail=f"角色 {character_name} 不存在")

        # 保存重置后的数据
        with open(script_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 后台真正生成角色图片
        background_tasks.add_task(
            _regenerate_character_background,
            controller,
            project_dir,
            target_char,
            data.get("project", {}),
        )

        return {"message": f"角色 {character_name} 正在重新生成..."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _regenerate_character_background(
    controller, project_dir, target_char: dict, project_info: dict
):
    """后台重新生成单个角色"""
    from src.pipeline.character_gen import CharacterGenerator
    from src.utils.file_saver import FileSaver
    from src.utils.evaluator import ImageEvaluator

    try:
        file_saver = FileSaver(project_dir)
        evaluator = ImageEvaluator(controller.agnes_client)

        # 合并配置
        char_config = {**controller.config["agnes"]}
        char_config["project"] = project_info

        generator = CharacterGenerator(
            controller.agnes_client, file_saver, evaluator, char_config
        )

        # 生成单个角色
        characters = await generator.generate([target_char])

        # 更新 parsed_script.json
        script_file = project_dir / "output" / "parsed_script.json"
        import json
        with open(script_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        for char in data.get("characters", []):
            if char["name"] == target_char["name"]:
                char["reference_images"] = target_char.get("reference_images", {})
                break

        with open(script_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"角色 {target_char['name']} 重新生成完成")

    except Exception as e:
        logger.error(f"角色 {target_char['name']} 重新生成失败: {e}")


# ============ 动画操作 ============

@app.post("/api/projects/{project_name}/animations/{from_scene}/{to_scene}/regenerate")
async def regenerate_animation(project_name: str, from_scene: int, to_scene: int):
    """重新生成过渡动画"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)

        # 删除已有的过渡视频
        video_dir = project_dir / "output" / "videos"
        if video_dir.exists():
            pattern = f"transition_{from_scene:02d}_{to_scene:02d}.mp4"
            for f in video_dir.iterdir():
                if f.name == pattern:
                    f.unlink()
                    logger.info(f"已删除: {f}")

        return {"message": f"过渡动画 {from_scene}→{to_scene} 已重置，请重新运行工作流"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 音频操作 ============

@app.post("/api/projects/{project_name}/audio/generate")
async def generate_all_audio(project_name: str, background_tasks: BackgroundTasks):
    """生成所有有对白场景的音频（真正调用 Mimo TTS，后台任务）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        data = _read_parsed_script(project_dir)
        scenes_need = [s for s in data.get("scenes", []) if s.get("dialogue")]
        if not scenes_need:
            return {"message": "无对白场景，无需生成", "count": 0}
        task_id = _new_task("audio")
        background_tasks.add_task(
            _generate_audio_bg, task_id, project_dir, scenes_need, data, controller
        )
        return {
            "task_id": task_id,
            "message": f"开始生成 {len(scenes_need)} 个音频",
            "count": len(scenes_need),
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class GenerateSceneAudioRequest(BaseModel):
    """单场景音频生成请求（支持 TTS 参数）"""
    voice: str = "default"
    speed: float = 1.0


@app.post("/api/project/{project_name}/audio/{scene_id}/generate")
async def generate_scene_audio(
    project_name: str, scene_id: int, req: GenerateSceneAudioRequest,
    background_tasks: BackgroundTasks
):
    """生成单个场景的音频（支持 voice/speed，让前端 TTS 设置生效）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        data = _read_parsed_script(project_dir)
        scene = next((s for s in data.get("scenes", []) if s.get("id") == scene_id), None)
        if not scene:
            raise HTTPException(status_code=404, detail=f"场景 {scene_id} 不存在")
        if not scene.get("dialogue"):
            raise HTTPException(status_code=400, detail=f"场景 {scene_id} 无对白")
        task_id = _new_task("audio")
        background_tasks.add_task(
            _generate_audio_bg, task_id, project_dir, [scene], data, controller,
            req.voice, req.speed
        )
        return {"task_id": task_id, "message": f"场景 {scene_id} 音频生成已启动"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _generate_audio_bg(task_id, project_dir, scenes, data, controller,
                             voice="default", speed=1.0):
    """后台生成音频（调 Mimo TTS，生成 mp3 + srt，写回 parsed_script）"""
    task_store[task_id]["status"] = "running"
    try:
        from src.pipeline.tts_gen import TTSGenerator
        from src.utils.file_saver import FileSaver
        from src.api.edge_tts_client import EdgeTTSClient

        file_saver = FileSaver(project_dir)
        gen = TTSGenerator(EdgeTTSClient(), file_saver)
        total = len(scenes)
        for i, scene in enumerate(scenes):
            task_store[task_id]["message"] = f"生成场景 {scene['id']} 音频 ({i+1}/{total})"
            try:
                result = await gen.generate([scene], voice=voice, speed=speed)
                updated = result[0]
                for j, s in enumerate(data["scenes"]):
                    if s.get("id") == scene["id"]:
                        data["scenes"][j] = updated
                        break
                _write_parsed_script(project_dir, data)
            except Exception as e:
                logger.error(f"场景 {scene['id']} 音频失败: {e}")
            task_store[task_id]["progress"] = {"percent": (i + 1) / total}
        task_store[task_id]["status"] = "completed"
        task_store[task_id]["message"] = "音频生成完成"
    except Exception as e:
        task_store[task_id]["status"] = "failed"
        task_store[task_id]["message"] = f"失败: {e}"
        logger.error(f"音频生成失败: {e}")


@app.post("/api/projects/{project_name}/audio/{scene_id}/regenerate")
async def regenerate_audio(project_name: str, scene_id: int):
    """重新生成单个场景的音频"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)

        # 删除已有的音频文件
        audio_dir = project_dir / "output" / "audio"
        if audio_dir.exists():
            pattern = f"dialog_{scene_id:02d}.mp3"
            for f in audio_dir.iterdir():
                if f.name == pattern:
                    f.unlink()
                    logger.info(f"已删除: {f}")

        # 重置场景中的音频路径
        script_file = project_dir / "output" / "parsed_script.json"
        if script_file.exists():
            import json
            with open(script_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for scene in data.get("scenes", []):
                if scene["id"] == scene_id:
                    scene.pop("audio_path", None)
                    scene.pop("subtitle_path", None)
                    break

            with open(script_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        return {"message": f"场景 {scene_id} 的音频已重置"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 设置操作 ============

@app.put("/api/settings")
async def update_settings(settings: dict):
    """更新全局设置"""
    try:
        # 这里可以将设置保存到文件
        # 暂时只返回成功
        logger.info(f"设置已更新: {settings}")
        return {"message": "设置已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 角色生成（独立操作） ============
# 这些接口用于角色生成页面，每步可单独调用

import io
import base64 as _base64


def _get_segmentor():
    """获取 rembg 抠图器（延迟初始化）"""
    if not hasattr(app.state, '_segmentor'):
        from src.segmentation.rembg_module import RembgSegmentor
        app.state._segmentor = RembgSegmentor(model_name="isnet-anime")
    return app.state._segmentor


class SingleViewRequest(BaseModel):
    """单视角生成请求"""
    description: str          # 角色描述（英文）
    view: str = "front"       # front / side / back
    style: str = "anime"      # 风格
    size: str = "1024x1536"


class SegmentOnlyRequest(BaseModel):
    """单独抠图请求"""
    image_base64: str


class RegenerateViewRequest(BaseModel):
    """带参考图重新生成单视角"""
    description: str
    view: str                    # 要重新生成的视角
    style: str = "anime"
    size: str = "1024x1536"
    ref_images: list[str] = []   # 参考图 base64 列表


class ChainViewRequest(BaseModel):
    """链式三视图生成请求"""
    description: str
    style: str = "anime"
    size: str = "1024x1536"


class SheetViewRequest(BaseModel):
    """设定图三视图生成请求"""
    description: str
    style: str = "anime"
    size: str = "1536x1024"


def _build_view_prompt(description: str, style: str, view: str) -> str:
    """构建单视角 prompt"""
    base = (
        f"{description}, "
        f"{style} illustration, "
        "single small character centered, pure white background, full body, "
        "lots of white space around character, character takes up 40-50% of image, "
        "detailed character design, clean lines, "
        "soft studio lighting, high quality, sharp details"
    )
    views = {
        "front": "facing the camera directly, straight-on front view, symmetrical pose, looking at the viewer, character reference sheet",
        "side": "facing left, perfect side profile view, 90 degree angle, looking to the left, character side portrait, standing straight",
        "back": "back view, from behind, character facing away from camera, showing back to viewer, back of head and body visible, no face shown, full body standing pose",
    }
    return f"{base}, {views.get(view, views['front'])}"


def _build_img2img_prompt(description: str, style: str, view: str) -> str:
    """构建图生图 prompt（用于链式生成）"""
    prompts = {
        "side": (
            f"Rotate this character to show a perfect side profile view, facing left, 90 degree angle. "
            f"Keep the exact same character design: {description}. "
            f"Keep the same {style} art style, clothing, hair color, body proportions. "
            f"Small character centered with lots of white space. "
            f"White background, full body, standing straight, high quality, detailed character design"
        ),
        "back": (
            f"Rotate this character to show the back view, character facing away from camera. "
            f"Keep the exact same character design: {description}. "
            f"Keep the same {style} art style, clothing, hair color, body proportions. "
            f"Small character centered with lots of white space. "
            f"White background, full body, standing pose, back of head and body clearly visible, "
            f"high quality, detailed character design"
        ),
    }
    return prompts.get(view, "")


def _build_sheet_prompt(description: str, style: str) -> str:
    """构建设定图 prompt"""
    return (
        f"Professional character turnaround reference sheet of {description}. "
        f"Three small views arranged horizontally from left to right with lots of white space: "
        f"1) Front view facing the camera, "
        f"2) Right side view showing exact 90-degree profile silhouette, "
        f"3) Back view facing completely away from camera with no face visible. "
        f"Full body, standing in relaxed pose, pure white background, "
        f"even studio lighting, consistent proportions across all views, "
        f"{style} illustration style, clean lines, "
        f"character design sheet format, high quality, detailed"
    )


def _transparent_to_white_bg_bytes(image_bytes: bytes) -> str:
    """透明PNG转白底，返回base64"""
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    white_bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    white_bg.paste(img, mask=img.split()[3])
    white_bg = white_bg.convert("RGB")
    buf = io.BytesIO()
    white_bg.save(buf, format="PNG")
    return _base64.b64encode(buf.getvalue()).decode("utf-8")


def _crop_three_views(image_bytes: bytes) -> dict:
    """从设定图裁剪三个视角"""
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size
    third = w // 3
    crops = {}
    for i, name in enumerate(["front", "side", "back"]):
        left = i * third
        right = (i + 1) * third if i < 2 else w
        cropped = img.crop((left, 0, right, h))
        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        crops[name] = buf.getvalue()
    return crops


@app.post("/api/character/generate-view")
async def character_generate_view(req: SingleViewRequest):
    """
    生成单个视角 + 抠图
    返回: raw_base64 (白底原图), seg_base64 (透明抠图), prompt
    """
    try:
        controller = get_controller()
    except HTTPException:
        raise

    prompt = _build_view_prompt(req.description, req.style, req.view)

    try:
        result = await controller.agnes_client.generate_image(
            prompt=prompt, size=req.size
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
            raw_bytes = img_resp.content

        raw_b64 = _base64.b64encode(raw_bytes).decode("utf-8")

        # 抠图
        segmentor = _get_segmentor()
        seg_bytes, seg_meta = segmentor.remove_background(raw_bytes)
        seg_b64 = _base64.b64encode(seg_bytes).decode("utf-8") if seg_bytes else None

        return {
            "success": True,
            "raw_base64": raw_b64,
            "seg_base64": seg_b64,
            "prompt": prompt,
            "metadata": seg_meta,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/character/segment")
async def character_segment(req: SegmentOnlyRequest):
    """单独抠图（不重新生成）"""
    try:
        image_bytes = _base64.b64decode(req.image_base64)
        segmentor = _get_segmentor()
        seg_bytes, seg_meta = segmentor.remove_background(image_bytes)
        if seg_bytes is None:
            raise HTTPException(500, f"抠图失败: {seg_meta.get('error')}")
        return {
            "success": True,
            "seg_base64": _base64.b64encode(seg_bytes).decode("utf-8"),
            "metadata": seg_meta,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/character/regenerate-view")
async def character_regenerate_view(req: RegenerateViewRequest):
    """
    带参考图重新生成单视角
    传入其他视角的图片作为参考，重新生成指定视角
    """
    try:
        controller = get_controller()
    except HTTPException:
        raise

    import httpx
    segmentor = _get_segmentor()

    # 构建 prompt
    prompt = _build_img2img_prompt(req.description, req.style, req.view)
    if not prompt:
        prompt = _build_view_prompt(req.description, req.style, req.view)

    # 构建参考图列表（转成白底）
    image_refs = []
    for ref_b64 in req.ref_images:
        try:
            ref_bytes = _base64.b64decode(ref_b64)
            white_b64 = _transparent_to_white_bg_bytes(ref_bytes)
            image_refs.append(f"data:image/png;base64,{white_b64}")
        except Exception:
            continue

    try:
        result = await controller.agnes_client.generate_image(
            prompt=prompt, size=req.size,
            image_refs=image_refs if image_refs else None,
        )
        if not result or "data" not in result or not result["data"]:
            raise HTTPException(500, "图片生成失败")

        url = result["data"][0].get("url")
        if not url:
            raise HTTPException(500, "返回数据中没有URL")

        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            raw_bytes = (await client.get(url, timeout=60)).content

        raw_b64 = _base64.b64encode(raw_bytes).decode("utf-8")

        # 抠图
        seg_bytes, seg_meta = segmentor.remove_background(raw_bytes)
        seg_b64 = _base64.b64encode(seg_bytes).decode("utf-8") if seg_bytes else None

        return {
            "success": True,
            "raw_base64": raw_b64,
            "seg_base64": seg_b64,
            "prompt": prompt,
            "metadata": seg_meta,
            "ref_count": len(image_refs),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/character/generate-chain")
async def character_generate_chain(req: ChainViewRequest):
    """
    链式三视图生成
    正面(文生图) → 抠图 → 侧面(图生图,正面作参考) → 抠图 → 背面(图生图,正面作参考) → 抠图
    """
    try:
        controller = get_controller()
    except HTTPException:
        raise

    import httpx
    segmentor = _get_segmentor()
    results = {}
    steps = []

    try:
        # 1. 生成正面
        steps.append({"step": 1, "action": "生成正面", "status": "processing"})
        front_prompt = _build_view_prompt(req.description, req.style, "front")
        front_result = await controller.agnes_client.generate_image(prompt=front_prompt, size=req.size)
        front_url = front_result["data"][0]["url"]
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            front_raw = (await client.get(front_url, timeout=60)).content
        results["front_raw"] = _base64.b64encode(front_raw).decode("utf-8")
        steps[-1]["status"] = "done"

        # 2. 正面抠图
        steps.append({"step": 2, "action": "正面抠图", "status": "processing"})
        front_seg, front_meta = segmentor.remove_background(front_raw)
        results["front_seg"] = _base64.b64encode(front_seg).decode("utf-8")
        results["front_meta"] = front_meta
        steps[-1]["status"] = "done"

        # 3. 生成侧面（正面作参考）
        steps.append({"step": 3, "action": "生成侧面(参考正面)", "status": "processing"})
        side_prompt = _build_img2img_prompt(req.description, req.style, "side")
        side_ref = _transparent_to_white_bg_bytes(front_seg)
        side_result = await controller.agnes_client.generate_image(
            prompt=side_prompt, size=req.size, image_refs=[f"data:image/png;base64,{side_ref}"]
        )
        side_url = side_result["data"][0]["url"]
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            side_raw = (await client.get(side_url, timeout=60)).content
        results["side_raw"] = _base64.b64encode(side_raw).decode("utf-8")
        steps[-1]["status"] = "done"

        # 4. 侧面抠图
        steps.append({"step": 4, "action": "侧面抠图", "status": "processing"})
        side_seg, side_meta = segmentor.remove_background(side_raw)
        results["side_seg"] = _base64.b64encode(side_seg).decode("utf-8")
        results["side_meta"] = side_meta
        steps[-1]["status"] = "done"

        # 5. 生成背面（正面作参考）
        steps.append({"step": 5, "action": "生成背面(参考正面)", "status": "processing"})
        back_prompt = _build_img2img_prompt(req.description, req.style, "back")
        back_ref = _transparent_to_white_bg_bytes(front_seg)
        back_result = await controller.agnes_client.generate_image(
            prompt=back_prompt, size=req.size, image_refs=[f"data:image/png;base64,{back_ref}"]
        )
        back_url = back_result["data"][0]["url"]
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            back_raw = (await client.get(back_url, timeout=60)).content
        results["back_raw"] = _base64.b64encode(back_raw).decode("utf-8")
        steps[-1]["status"] = "done"

        # 6. 背面抠图
        steps.append({"step": 6, "action": "背面抠图", "status": "processing"})
        back_seg, back_meta = segmentor.remove_background(back_raw)
        results["back_seg"] = _base64.b64encode(back_seg).decode("utf-8")
        results["back_meta"] = back_meta
        steps[-1]["status"] = "done"

        return {
            "success": True,
            "raw_views": {
                "front": results["front_raw"],
                "side": results["side_raw"],
                "back": results["back_raw"],
            },
            "seg_views": {
                "front": results["front_seg"],
                "side": results["side_seg"],
                "back": results["back_seg"],
            },
            "metadata": {
                "front": results.get("front_meta", {}),
                "side": results.get("side_meta", {}),
                "back": results.get("back_meta", {}),
            },
            "steps": steps,
        }
    except Exception as e:
        steps[-1]["status"] = "failed"
        steps[-1]["error"] = str(e)
        return {
            "success": False,
            "error": str(e),
            "steps": steps,
            "raw_views": {k.replace("_raw", ""): v for k, v in results.items() if k.endswith("_raw")},
            "seg_views": {k.replace("_seg", ""): v for k, v in results.items() if k.endswith("_seg")},
        }


@app.post("/api/character/generate-sheet")
async def character_generate_sheet(req: SheetViewRequest):
    """
    设定图三视图生成
    一张图出三视角 → 裁剪 → 分别抠图
    """
    try:
        controller = get_controller()
    except HTTPException:
        raise

    import httpx
    segmentor = _get_segmentor()
    results = {}
    steps = []

    try:
        # 1. 生成设定图
        steps.append({"step": 1, "action": "生成设定图", "status": "processing"})
        prompt = _build_sheet_prompt(req.description, req.style)
        sheet_result = await controller.agnes_client.generate_image(prompt=prompt, size=req.size)
        sheet_url = sheet_result["data"][0]["url"]
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            sheet_raw = (await client.get(sheet_url, timeout=60)).content
        results["sheet_raw"] = _base64.b64encode(sheet_raw).decode("utf-8")
        steps[-1]["status"] = "done"

        # 2. 裁剪三视角
        steps.append({"step": 2, "action": "裁剪三视角", "status": "processing"})
        crops = _crop_three_views(sheet_raw)
        results["crops_raw"] = {k: _base64.b64encode(v).decode("utf-8") for k, v in crops.items()}
        steps[-1]["status"] = "done"

        # 3-5. 分别抠图
        for i, view_name in enumerate(["front", "side", "back"]):
            steps.append({"step": i + 3, "action": f"{view_name} 抠图", "status": "processing"})
            seg_bytes, seg_meta = segmentor.remove_background(crops[view_name])
            if seg_bytes:
                results[f"{view_name}_seg"] = _base64.b64encode(seg_bytes).decode("utf-8")
                results[f"{view_name}_meta"] = seg_meta
                steps[-1]["status"] = "done"
            else:
                steps[-1]["status"] = "failed"
                steps[-1]["error"] = seg_meta.get("error", "抠图失败")

        return {
            "success": True,
            "sheet_raw": results["sheet_raw"],
            "crops_raw": results.get("crops_raw", {}),
            "seg_views": {
                "front": results.get("front_seg"),
                "side": results.get("side_seg"),
                "back": results.get("back_seg"),
            },
            "metadata": {
                "front": results.get("front_meta", {}),
                "side": results.get("side_meta", {}),
                "back": results.get("back_meta", {}),
            },
            "steps": steps,
            "prompt": prompt,
        }
    except Exception as e:
        steps[-1]["status"] = "failed"
        steps[-1]["error"] = str(e)
        return {
            "success": False,
            "error": str(e),
            "steps": steps,
            "sheet_raw": results.get("sheet_raw"),
            "crops_raw": results.get("crops_raw", {}),
        }


# ============ 工作流操作 ============

@app.post("/api/run")
async def run_workflow(request: ScriptRequest, background_tasks: BackgroundTasks):
    """运行工作流"""
    global current_task

    try:
        controller = get_controller()
    except HTTPException:
        raise

    if current_task and current_task["status"] == "running":
        raise HTTPException(status_code=400, detail="已有任务在运行中")

    # 保存剧本文件
    script_dir = Path("./input")
    script_dir.mkdir(exist_ok=True)
    script_path = script_dir / f"{request.project_name}.yaml"
    script_path.write_text(request.script_content, encoding="utf-8")

    # 创建任务
    task_id = f"task_{request.project_name}"
    current_task = {
        "task_id": task_id,
        "status": "running",
        "progress": {},
        "message": "正在执行...",
    }

    # 后台运行工作流
    background_tasks.add_task(
        run_workflow_background,
        task_id,
        str(script_path),
        request.project_name,
        request.mode,
    )

    return {"task_id": task_id, "message": "工作流已启动"}


async def run_workflow_background(
    task_id: str, script_path: str, project_name: str, mode: str
):
    """后台运行工作流（捕获所有异常，绝不拖垮服务器）"""
    global current_task

    try:
        await pipeline_controller.run(
            script_path=script_path,
            project_name=project_name,
            mode=mode,
        )
        current_task["status"] = "completed"
        current_task["message"] = "工作流执行完成"
    except Exception as e:
        logger.error(f"工作流执行失败: {e}", exc_info=True)
        current_task["status"] = "failed"
        current_task["message"] = f"执行失败: {str(e)}"
    except BaseException as e:
        # 捕获 SystemExit / KeyboardInterrupt 等，防止拖垮服务器
        logger.error(f"工作流致命错误: {type(e).__name__}: {e}")
        current_task["status"] = "failed"
        current_task["message"] = f"致命错误: {type(e).__name__}: {str(e)}"


@app.get("/api/task/current")
async def get_current_task():
    """获取当前任务状态"""
    if not current_task:
        return {"task_id": "", "status": "idle", "progress": {}, "message": "没有正在运行的任务"}
    return current_task


@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str):
    """获取任务状态（先查 task_store 新任务，再回退旧 current_task）"""
    if task_id in task_store:
        return {"task_id": task_id, **task_store[task_id]}
    if not current_task or current_task["task_id"] != task_id:
        return {"task_id": task_id, "status": "not_found", "progress": {}, "message": "任务不存在"}

    return current_task


@app.get("/api/task/{task_id}/stream")
async def stream_task_status(task_id: str):
    """SSE 实时推送任务状态（每秒推送 task_store，直到 completed/failed）"""
    async def event_stream():
        while True:
            if task_id in task_store:
                t = task_store[task_id]
                payload = {
                    "task_id": task_id,
                    "status": t.get("status"),
                    "message": t.get("message", ""),
                    "progress": t.get("progress", {}),
                    "result": t.get("result"),
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if t.get("status") in ("completed", "failed"):
                    break
            elif current_task and current_task.get("task_id") == task_id:
                yield f"data: {json.dumps({'task_id': task_id, **current_task}, ensure_ascii=False)}\n\n"
                if current_task.get("status") in ("completed", "failed"):
                    break
            else:
                yield f"data: {json.dumps({'task_id': task_id, 'status': 'not_found'}, ensure_ascii=False)}\n\n"
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ============ 角色保存系统 ============
import json
import uuid
from datetime import datetime

# 角色保存目录
SAVED_CHARACTERS_DIR = BASE_DIR / "saved_characters"
SAVED_CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)


class SaveCharacterRequest(BaseModel):
    """保存角色请求"""
    name: str                    # 角色名称
    description: str             # 角色描述（主提示词）
    views: dict                  # 三视图数据 { front: { raw?, seg? }, side: {...}, back: {...} }


class UpdateCharacterRequest(BaseModel):
    """更新角色信息请求"""
    name: Optional[str] = None
    description: Optional[str] = None


def _get_character_dir(char_id: str) -> Path:
    """获取角色保存目录"""
    return SAVED_CHARACTERS_DIR / char_id


def _load_character_meta(char_dir: Path) -> dict:
    """加载角色元数据"""
    meta_file = char_dir / "meta.json"
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_character_meta(char_dir: Path, meta: dict):
    """保存角色元数据"""
    meta_file = char_dir / "meta.json"
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _save_base64_image(base64_str: str, file_path: Path):
    """保存base64图片到文件"""
    image_bytes = _base64.b64decode(base64_str)
    with open(file_path, "wb") as f:
        f.write(image_bytes)


def _load_image_as_base64(file_path: Path) -> Optional[str]:
    """加载图片文件为base64"""
    if file_path.exists():
        with open(file_path, "rb") as f:
            return _base64.b64encode(f.read()).decode("utf-8")
    return None


def _init_default_characters():
    """初始化默认角色到角色库（如果角色库为空）"""
    try:
        # 检查角色库是否为空
        existing = list(SAVED_CHARACTERS_DIR.iterdir())
        if any(d.is_dir() for d in existing):
            logger.info("角色库已有数据，跳过初始化")
            return

        # 默认角色
        default_chars = [
            {
                "name": "小星",
                "description": "a young girl with silver hair, blue eyes, wearing a white cloak, gentle expression",
            },
            {
                "name": "暗影",
                "description": "a tall figure in black armor, glowing red eyes, menacing presence",
            },
        ]

        for char in default_chars:
            char_id = f"{char['name']}_{uuid.uuid4().hex[:8]}"
            char_dir = _get_character_dir(char_id)
            char_dir.mkdir(parents=True, exist_ok=True)

            meta = {
                "id": char_id,
                "name": char["name"],
                "description": char["description"],
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            _save_character_meta(char_dir, meta)
            logger.info(f"已添加默认角色: {char['name']}")

    except Exception as e:
        logger.error(f"初始化默认角色失败: {e}")


@app.post("/api/character/save")
async def save_character(req: SaveCharacterRequest):
    """
    保存角色到本地
    返回: character_id
    """
    try:
        # 生成唯一ID
        char_id = f"{req.name}_{uuid.uuid4().hex[:8]}"
        char_dir = _get_character_dir(char_id)
        char_dir.mkdir(parents=True, exist_ok=True)

        # 保存元数据
        meta = {
            "id": char_id,
            "name": req.name,
            "description": req.description,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        _save_character_meta(char_dir, meta)

        # 保存三视图图片
        for view_name in ["front", "side", "back"]:
            view_data = req.views.get(view_name, {})
            if view_data.get("raw"):
                _save_base64_image(view_data["raw"], char_dir / f"{view_name}_raw.png")
            if view_data.get("seg"):
                _save_base64_image(view_data["seg"], char_dir / f"{view_name}_seg.png")

        logger.info(f"角色已保存: {char_id}")
        return {
            "success": True,
            "character_id": char_id,
            "message": f"角色 '{req.name}' 已保存",
        }
    except Exception as e:
        logger.error(f"保存角色失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")


@app.get("/api/character/list")
async def list_saved_characters():
    """获取已保存的角色列表"""
    try:
        characters = []
        if SAVED_CHARACTERS_DIR.exists():
            for char_dir in SAVED_CHARACTERS_DIR.iterdir():
                if char_dir.is_dir():
                    meta = _load_character_meta(char_dir)
                    if meta:
                        # 检查是否有三视图
                        has_views = {}
                        for view_name in ["front", "side", "back"]:
                            has_views[view_name] = {
                                "raw": (char_dir / f"{view_name}_raw.png").exists(),
                                "seg": (char_dir / f"{view_name}_seg.png").exists(),
                            }
                        meta["has_views"] = has_views
                        characters.append(meta)

        # 按创建时间倒序排列
        characters.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return {"characters": characters}
    except Exception as e:
        logger.error(f"获取角色列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/character/load/{character_id}")
async def load_character(character_id: str):
    """加载已保存的角色（包含三视图数据）"""
    try:
        char_dir = _get_character_dir(character_id)
        if not char_dir.exists():
            raise HTTPException(status_code=404, detail="角色不存在")

        # 加载元数据
        meta = _load_character_meta(char_dir)

        # 加载三视图
        views = {}
        for view_name in ["front", "side", "back"]:
            raw_b64 = _load_image_as_base64(char_dir / f"{view_name}_raw.png")
            seg_b64 = _load_image_as_base64(char_dir / f"{view_name}_seg.png")
            if raw_b64 or seg_b64:
                views[view_name] = {}
                if raw_b64:
                    views[view_name]["raw"] = raw_b64
                if seg_b64:
                    views[view_name]["seg"] = seg_b64

        return {
            "success": True,
            "character": {
                "id": meta.get("id"),
                "name": meta.get("name"),
                "description": meta.get("description"),
                "created_at": meta.get("created_at"),
                "views": views,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"加载角色失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/character/{character_id}")
async def update_character(character_id: str, req: UpdateCharacterRequest):
    """更新角色信息（名称、描述）"""
    try:
        char_dir = _get_character_dir(character_id)
        if not char_dir.exists():
            raise HTTPException(status_code=404, detail="角色不存在")

        meta = _load_character_meta(char_dir)
        if req.name is not None:
            meta["name"] = req.name
        if req.description is not None:
            meta["description"] = req.description
        meta["updated_at"] = datetime.now().isoformat()

        _save_character_meta(char_dir, meta)

        return {"success": True, "message": "角色信息已更新"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新角色失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/character/{character_id}")
async def delete_character(character_id: str):
    """删除已保存的角色"""
    try:
        char_dir = _get_character_dir(character_id)
        if not char_dir.exists():
            raise HTTPException(status_code=404, detail="角色不存在")

        # 删除所有文件
        import shutil
        shutil.rmtree(char_dir)

        logger.info(f"角色已删除: {character_id}")
        return {"success": True, "message": "角色已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除角色失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/character/{character_id}/views")
async def update_character_views(character_id: str, views: dict):
    """更新角色的三视图数据"""
    try:
        char_dir = _get_character_dir(character_id)
        if not char_dir.exists():
            raise HTTPException(status_code=404, detail="角色不存在")

        # 更新三视图
        for view_name in ["front", "side", "back"]:
            view_data = views.get(view_name, {})
            if view_data.get("raw"):
                _save_base64_image(view_data["raw"], char_dir / f"{view_name}_raw.png")
            if view_data.get("seg"):
                _save_base64_image(view_data["seg"], char_dir / f"{view_name}_seg.png")

        # 更新修改时间
        meta = _load_character_meta(char_dir)
        meta["updated_at"] = datetime.now().isoformat()
        _save_character_meta(char_dir, meta)

        return {"success": True, "message": "三视图已更新"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新三视图失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============ 项目角色绑定（角色页生成结果落盘到项目）============
# 修复数据断裂：角色页生成的三视图原本只在内存/角色库，分镜阶段拿不到；
# 绑定后落盘到项目 output/characters/ 并写入 parsed_script.json 的 reference_images。


class BindCharacterRequest(BaseModel):
    """绑定角色到项目请求"""
    name: str
    description: str = ""
    views: dict  # {front:{raw?,seg?}, side:{...}, back:{...}}


class UpdateProjectCharacterRequest(BaseModel):
    """更新项目角色请求"""
    description: Optional[str] = None
    new_name: Optional[str] = None


def _read_parsed_script(project_dir: Path) -> dict:
    """读取项目 parsed_script.json，不存在返回空结构"""
    script_file = project_dir / "output" / "parsed_script.json"
    if script_file.exists():
        with open(script_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"project": {}, "characters": [], "scenes": []}


def _write_parsed_script(project_dir: Path, data: dict):
    """写入项目 parsed_script.json"""
    script_file = project_dir / "output" / "parsed_script.json"
    script_file.parent.mkdir(parents=True, exist_ok=True)
    with open(script_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.post("/api/project/{project_name}/character/bind")
async def bind_character_to_project(project_name: str, req: BindCharacterRequest):
    """
    把角色页生成的三视图绑定到项目：
    - 落盘 raw/seg 到 output/characters/{name}_{view}.png
    - 写入 parsed_script.json 的 reference_images（raw 路径，供分镜生成引用）
    - 角色不存在则追加，存在则更新
    """
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        char_dir = project_dir / "output" / "characters"
        char_dir.mkdir(parents=True, exist_ok=True)

        ref_images = {}
        for view in ["front", "side", "back"]:
            vdata = req.views.get(view, {}) or {}
            if vdata.get("raw"):
                raw_path = char_dir / f"{req.name}_{view}.png"
                _save_base64_image(vdata["raw"], raw_path)
                ref_images[view] = str(raw_path)
            if vdata.get("seg"):
                seg_path = char_dir / f"{req.name}_{view}_seg.png"
                _save_base64_image(vdata["seg"], seg_path)

        data = _read_parsed_script(project_dir)
        chars = data.setdefault("characters", [])
        target = None
        for c in chars:
            if c.get("name") == req.name:
                target = c
                break
        if target is None:
            target = {"name": req.name, "description": req.description}
            chars.append(target)
        else:
            if req.description:
                target["description"] = req.description
        if ref_images:
            target["reference_images"] = ref_images

        _write_parsed_script(project_dir, data)
        logger.info(f"角色 {req.name} 已绑定到项目 {project_name}: {list(ref_images.keys())}")
        return {
            "success": True,
            "reference_images": ref_images,
            "message": f"角色 {req.name} 已绑定到项目 {project_name}",
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"绑定角色失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/project/{project_name}/character/{character_name}")
async def update_project_character(
    project_name: str, character_name: str, req: UpdateProjectCharacterRequest
):
    """更新项目角色的描述/名称（改名时同步重命名文件、更新场景角色名）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        data = _read_parsed_script(project_dir)

        target = None
        for c in data.get("characters", []):
            if c.get("name") == character_name:
                target = c
                break
        if target is None:
            raise HTTPException(
                status_code=404,
                detail=f"角色 {character_name} 不存在于项目 {project_name}",
            )

        renamed = False
        if req.new_name and req.new_name.strip() and req.new_name.strip() != character_name:
            new_name = req.new_name.strip()
            char_dir = project_dir / "output" / "characters"
            ri = target.get("reference_images", {})
            new_ri = {}
            for view, path_str in ri.items():
                p = Path(path_str)
                if p.exists() and p.stem.startswith(character_name + "_"):
                    new_p = p.with_name(f"{new_name}_{p.stem.split('_', 1)[1]}{p.suffix}")
                    p.rename(new_p)
                    new_ri[view] = str(new_p)
                else:
                    new_ri[view] = path_str
            target["reference_images"] = new_ri
            # 同步重命名 seg 文件（不在 reference_images 里，但需保持一致）
            for seg_p in char_dir.glob(f"{character_name}_*_seg.png"):
                rest = seg_p.stem[len(character_name) + 1:]  # e.g. "front_seg"
                seg_p.rename(seg_p.with_name(f"{new_name}_{rest}{seg_p.suffix}"))
            target["name"] = new_name
            for s in data.get("scenes", []):
                s["characters"] = [
                    new_name if cn == character_name else cn
                    for cn in s.get("characters", [])
                ]
            renamed = True

        if req.description is not None:
            target["description"] = req.description

        _write_parsed_script(project_dir, data)
        logger.info(f"项目角色 {character_name} 已更新 (renamed={renamed})")
        return {"success": True, "renamed": renamed, "message": "角色已更新"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新项目角色失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/project/{project_name}/characters/full")
async def get_project_characters_full(project_name: str):
    """获取项目角色（含 reference_images 路径，供角色页/分镜页使用）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        data = _read_parsed_script(project_dir)
        chars = data.get("characters", [])
        for c in chars:
            ri = c.get("reference_images", {})
            if isinstance(ri, dict) and "full" in ri and "back" not in ri:
                ri["back"] = ri.pop("full")
        return {"characters": chars}
    except FileNotFoundError:
        return {"characters": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 简单任务存储（1.7 将重构为 TaskRegistry + SSE）============
task_store: dict = {}


def _new_task(task_type: str) -> str:
    """创建任务并返回 task_id"""
    tid = f"{task_type}_{uuid.uuid4().hex[:8]}"
    task_store[tid] = {"status": "pending", "progress": {}, "message": "", "result": None}
    return tid


# ============ 剧本编辑（保存 YAML + 解析 -> parsed_script.json）============

class SaveScriptRequest(BaseModel):
    script_content: str
    enhance: bool = False  # 是否用AI增强场景描述（需Agnes，慢）


@app.post("/api/project/{project_name}/script")
async def save_project_script(project_name: str, req: SaveScriptRequest):
    """保存剧本YAML并解析为 parsed_script.json（保留已有生成结果与角色参考图）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)

        input_dir = project_dir / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        script_path = input_dir / f"{project_name}.yaml"
        script_path.write_text(req.script_content, encoding="utf-8")

        from src.pipeline.script_parser import ScriptParser
        parser = ScriptParser(controller.agnes_client)
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


# ============ 单场景分镜生成（自动取角色参考图）============

@app.post("/api/project/{project_name}/scene/{scene_id}/generate-storyboard")
async def generate_scene_storyboard(
    project_name: str, scene_id: int, background_tasks: BackgroundTasks
):
    """生成单个场景分镜图（自动用场景角色的 front 图作参考）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        data = _read_parsed_script(project_dir)
        scene = next((s for s in data.get("scenes", []) if s.get("id") == scene_id), None)
        if not scene:
            raise HTTPException(status_code=404, detail=f"场景 {scene_id} 不存在")

        task_id = _new_task("storyboard")
        background_tasks.add_task(
            _generate_scene_storyboard_bg, task_id, project_dir, scene, data, controller
        )
        return {"task_id": task_id, "message": f"场景 {scene_id} 分镜生成已启动"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _generate_scene_storyboard_bg(task_id, project_dir, scene, data, controller):
    """后台生成单场景分镜（单候选，不择优，快速；1.7 接入 SSE 进度）"""
    task_store[task_id]["status"] = "running"
    task_store[task_id]["message"] = f"场景 {scene['id']} 生图中..."
    try:
        import httpx
        from src.utils.file_saver import FileSaver
        from src.pipeline.storyboard_gen import StoryboardGenerator

        file_saver = FileSaver(project_dir)
        chars = data.get("characters", [])
        style = data.get("project", {}).get("style", "anime")
        char_refs = {c["name"]: c.get("reference_images", {}) for c in chars}

        # 角色参考图 -> data url
        references = []
        for cn in scene.get("characters", []):
            front = char_refs.get(cn, {}).get("front")
            if front and Path(front).exists():
                b64 = _base64.b64encode(Path(front).read_bytes()).decode()
                references.append(f"data:image/png;base64,{b64}")

        gen = StoryboardGenerator(controller.agnes_client, file_saver, None, controller.config["agnes"])
        prompt = gen._build_prompt(scene, style)

        result = await controller.agnes_client.generate_image(
            prompt=prompt,
            image_refs=references if references else None,
            size=controller.config["agnes"]["settings"]["image"]["size"],
        )
        url = result["data"][0]["url"]
        async with httpx.AsyncClient() as c:
            img_data = (await c.get(url, timeout=300)).content
        filepath = file_saver.save_storyboard(scene["id"], img_data)
        scene["image_path"] = str(filepath)
        scene["status"] = "completed"

        for i, s in enumerate(data["scenes"]):
            if s.get("id") == scene["id"]:
                data["scenes"][i] = scene
                break
        _write_parsed_script(project_dir, data)

        task_store[task_id]["status"] = "completed"
        task_store[task_id]["result"] = {"image_path": str(filepath)}
        task_store[task_id]["message"] = "分镜生成完成"
    except Exception as e:
        task_store[task_id]["status"] = "failed"
        task_store[task_id]["message"] = f"失败: {e}"
        logger.error(f"场景分镜生成失败: {e}")


# ============ 最终视频合成（ffmpeg）============

# ==================== 阶段 2：配音前置（shot 级）====================
# 配音在视频之前：TTS 拿真实音频时长 -> plan_shot_timing 反推 num_frames -> 写回。
# 走 ProjectStore（v2 project.json）+ store.update_shot。不删旧 scene 级接口。
from typing import Any, List
import time as _time


class ShotAudioRequest(BaseModel):
    voice: str = "default"
    rate: str = "+0%"
    volume: str = "+0%"
    regenerate: bool = False


def _fps_from_config() -> int:
    try:
        return int((_APP_CFG.get("agnes") or {}).get("video", {}).get("frame_rate", 24))
    except Exception:
        return 24


def _audio_out_path(project_dir: Path, shot_id: str) -> Path:
    return project_dir / "output" / "audio" / f"{shot_id}.mp3"


def _apply_audio_to_shot(shot, res, timing, voice, rate):
    """sync mutator：把 TTS 结果 + 时长反推写回 shot（供 store.update_shot）。"""
    from src.core.models import ShotStatus
    if res is None:
        shot.audio.status = ShotStatus.SKIPPED.value
    else:
        shot.audio.local = res["path"]
        shot.audio.duration_ms = res["duration_ms"]
        shot.audio.words = res["words"]
        shot.audio.voice = voice
        shot.audio.rate = rate
        shot.audio.status = ShotStatus.DONE.value
        shot.audio.updated_at = _time.time()
    # 🔴 视频参数在这一步定死（配音前置的关键）
    shot.video.num_frames = timing.num_frames
    shot.video.frame_rate = timing.frame_rate


async def _gen_one_audio(tts, project_dir, shot_id, dialogue, voice, rate, volume, fps):
    """单镜 TTS（并发用），返回 (shot_id, res_or_None, timing, error_or_None)。"""
    from src.core.timing import plan_shot_timing
    try:
        if not dialogue:
            return (shot_id, None, plan_shot_timing(0, fps), None)
        out = _audio_out_path(Path(project_dir), shot_id)
        res = await tts.synthesize_full(dialogue, voice=voice, rate=rate, volume=volume, out_path=out)
        timing = plan_shot_timing(res["duration_ms"] / 1000, fps)
        return (shot_id, res, timing, None)
    except Exception as e:
        return (shot_id, None, None, str(e))


@app.post("/api/project/{project_name}/shot/{shot_id}/audio")
async def shot_audio(project_name: str, shot_id: str, req: ShotAudioRequest,
                     background_tasks: BackgroundTasks):
    """单镜配音（TTS -> 反推 num_frames -> 写回 audio+video 参数）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        from src.core.store import ProjectStore
        if ProjectStore(project_dir).load().get_shot(shot_id) is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        task_id = _new_task("audio")
        background_tasks.add_task(
            _shot_audio_bg, task_id, str(project_dir), shot_id,
            req.voice, req.rate, req.volume
        )
        return {"task_id": task_id, "shot_id": shot_id}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


async def _shot_audio_bg(task_id, project_dir, shot_id, voice, rate, volume):
    from src.core.store import ProjectStore
    from src.api.edge_tts_client import EdgeTTSClient
    task_store[task_id]["status"] = "running"
    try:
        fps = _fps_from_config()
        store = ProjectStore(Path(project_dir))
        shot = store.load().get_shot(shot_id)
        tts = EdgeTTSClient()
        _sid, res, timing, err = await _gen_one_audio(
            tts, project_dir, shot_id, shot.dialogue, voice, rate, volume, fps)
        if err:
            raise RuntimeError(err)
        store.update_shot(shot_id,
                          lambda sh: _apply_audio_to_shot(sh, res, timing, voice, rate))
        msg = f"镜头 {shot_id} 配音完成"
        if timing.needs_split:
            task_store[task_id]["warning"] = {
                "needs_split": True, "suggested_splits": timing.suggested_splits,
                "message": f"台词 {timing.audio_seconds:.1f}s 超单镜上限，建议拆为 {timing.suggested_splits} 个镜头"}
            msg += f"（⚠️ 建议拆为 {timing.suggested_splits} 镜）"
        task_store[task_id]["status"] = "completed"
        task_store[task_id]["result"] = {
            "shot_id": shot_id,
            "duration_ms": res["duration_ms"] if res else 0,
            "num_frames": timing.num_frames, "needs_split": timing.needs_split}
        task_store[task_id]["message"] = msg
    except Exception as e:
        task_store[task_id]["status"] = "failed"
        task_store[task_id]["message"] = f"失败: {e}"
        logger.error(f"镜头 {shot_id} 配音失败: {e}")


class BatchAudioRequest(BaseModel):
    voice: str = "default"
    rate: str = "+0%"
    volume: str = "+0%"
    scope: Any = "all"      # "all" | "pending" | [shot_id, ...]
    skip_locked: bool = True


@app.post("/api/project/{project_name}/audio/batch")
async def batch_audio(project_name: str, req: BatchAudioRequest,
                      background_tasks: BackgroundTasks):
    """批量配音（edge-tts 不占 Agnes 限流桶，并发 4，顺序写回避免丢失更新）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        from src.core.store import ProjectStore
        p = ProjectStore(project_dir).load()
        shots = p.all_shots()
        if req.scope == "pending":
            ids = [s.shot_id for s in shots if s.audio.status != "done"]
        elif isinstance(req.scope, list):
            ids = list(req.scope)
        else:
            ids = [s.shot_id for s in shots]
        if req.skip_locked:
            locked = {s.shot_id for s in shots if s.locked}
            ids = [i for i in ids if i not in locked]
        if not ids:
            return {"task_id": None, "message": "无待配音镜头", "count": 0}
        task_id = _new_task("audio")
        background_tasks.add_task(
            _batch_audio_bg, task_id, str(project_dir), ids, req.voice, req.rate, req.volume
        )
        return {"task_id": task_id, "count": len(ids), "shot_ids": ids}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


async def _batch_audio_bg(task_id, project_dir, shot_ids, voice, rate, volume):
    from src.core.store import ProjectStore
    from src.api.edge_tts_client import EdgeTTSClient
    task_store[task_id]["status"] = "running"
    try:
        fps = _fps_from_config()
        store = ProjectStore(Path(project_dir))
        id2dlg = {s.shot_id: s.dialogue for s in store.load().all_shots()}
        tts = EdgeTTSClient()
        sem = asyncio.Semaphore(4)

        async def one(sid):
            async with sem:
                return await _gen_one_audio(tts, project_dir, sid, id2dlg.get(sid, ""),
                                            voice, rate, volume, fps)

        results = await asyncio.gather(*(one(sid) for sid in shot_ids), return_exceptions=True)
        done = 0
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"批量配音某镜失败: {r}"); continue
            sid, res, timing, err = r
            if err:
                logger.error(f"镜头 {sid} 配音失败: {err}"); continue
            try:
                store.update_shot(
                    sid,
                    lambda sh, res=res, timing=timing: _apply_audio_to_shot(sh, res, timing, voice, rate))
                done += 1
            except Exception as e:
                logger.error(f"镜头 {sid} 写回失败: {e}")
            task_store[task_id]["progress"] = {"percent": done / len(shot_ids)}
        task_store[task_id]["status"] = "completed"
        task_store[task_id]["result"] = {"done": done, "total": len(shot_ids)}
        task_store[task_id]["message"] = f"批量配音完成 {done}/{len(shot_ids)}"
    except Exception as e:
        task_store[task_id]["status"] = "failed"
        task_store[task_id]["message"] = f"失败: {e}"
        logger.error(f"批量配音失败: {e}")


class SplitDialogueRequest(BaseModel):
    mode: str = "auto"
    segments: Optional[List[str]] = None


@app.post("/api/project/{project_name}/shot/{shot_id}/split-dialogue")
async def split_shot_dialogue(project_name: str, shot_id: str, req: SplitDialogueRequest):
    """拆超长台词为多个镜头：原 Shot 保留第1段，后续段插入同场次新 Shot，shot_id 顺移。"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        from src.core.store import ProjectStore
        from src.core.timing import split_dialogue as _split
        from src.core.models import Shot, ImageAsset, ShotStatus
        store = ProjectStore(project_dir)
        p = store.load()
        shot = p.get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        if req.segments:
            segs = [s for s in req.segments if s and s.strip()]
        else:
            segs = _split(shot.dialogue or "")
        if len(segs) <= 1:
            return {"shot_id": shot_id, "split_into": 1, "message": "无需拆分"}
        scene = next((sc for sc in p.scenes if sc.scene_id == shot.scene_id), None)
        k = next((i for i, s in enumerate(scene.shots) if s.shot_id == shot_id), None)
        shot.dialogue = segs[0]
        new_shots = []
        for seg in segs[1:]:
            new_shots.append(Shot(
                shot_id=f"{shot.scene_id}_cTMP", scene_id=shot.scene_id, index=0,
                description=shot.description, prompt_en=shot.prompt_en,
                negative_prompt=shot.negative_prompt, shot_size=shot.shot_size,
                camera=shot.camera, lighting=shot.lighting, motion=shot.motion,
                characters=list(shot.characters), dialogue=seg, link_mode=shot.link_mode,
                seed=shot.seed, first_frame=ImageAsset(), last_frame=ImageAsset(),
                status=ShotStatus.PENDING.value,
            ))
        scene.shots = scene.shots[:k] + [shot] + new_shots + scene.shots[k + 1:]
        for i, sh in enumerate(scene.shots):
            sh.index = i + 1
            sh.shot_id = f"{scene.scene_id}_c{i + 1:02d}"
        store.save(p)
        return {"split_into": len(segs),
                "shot_ids": [sh.shot_id for sh in scene.shots[k:k + len(segs)]]}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/project/{project_name}/timing")
async def get_project_timing(project_name: str):
    """时长预览：每镜 audio_seconds/planned_seconds/needs_split + 全片总时长"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        from src.core.store import ProjectStore
        from src.core.timing import plan_shot_timing
        p = ProjectStore(project_dir).load()
        fps = _fps_from_config()
        out = []
        total = 0.0
        for sh in p.all_shots():
            audio_sec = (sh.audio.duration_ms / 1000) if sh.audio.duration_ms else 0.0
            if sh.video.num_frames and sh.video.frame_rate:
                planned = sh.video.num_frames / sh.video.frame_rate
                needs_split = False
            else:
                t = plan_shot_timing(audio_sec, fps)
                planned = t.planned_seconds
                needs_split = t.needs_split
            out.append({"shot_id": sh.shot_id, "scene_id": sh.scene_id,
                        "audio_seconds": round(audio_sec, 3),
                        "planned_seconds": round(planned, 3),
                        "num_frames": sh.video.num_frames,
                        "needs_split": needs_split,
                        "dialogue": sh.dialogue[:40]})
            total += planned
        return {"shots": out, "total_seconds": round(total, 3), "fps": fps}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ==================== 阶段 3：分镜图 / 尾帧（shot 级，走 JobQueue）====================
# first_frame：2 候选图生图（角色参考图用公网 url 或 base64），存 local+公网 url，
#   status=pending 等用户点选；choose 后 status=done，url_ready 才 True（视频唯一入口）。
# last_frame：仅 keyframes，图生图用 first_frame.url；生成后若 split_next 非空，桥接拷贝给后继首帧。
from src.core.prompt_builder import (
    build_shot_prompt, build_i2i_prompt, build_last_frame_prompt)


def _shot_char_refs(project, shot):
    """取出场角色参考图，返回可送图片接口的 ref 列表（公网 URL 或 data:image URI）。
    都没有返回 []（调用方走文生图）。图片接口支持 base64（FACTS §2.4），无需 ImageHost。"""
    refs = []
    for name in shot.characters:
        c = project.get_character(name)
        if not c:
            continue
        url = c.ref_url()                       # 优先 front/side/back 的公网 url
        if url:
            refs.append(url); continue
        for v in ("front", "side", "back"):     # 只有 local -> base64 data URI
            a = c.views.get(v)
            if a and a.local and Path(str(a.local)).exists():
                b64 = _base64.b64encode(Path(str(a.local)).read_bytes()).decode()
                refs.append(f"data:image/png;base64,{b64}")
                break
    return refs


async def _await_first_frame_url(store, shot_id, emit, timeout=180, interval=5):
    """有界等待 first_frame.url_ready。bridge 场景首帧来自前驱尾帧，可能尚未就绪。
    超时返回 None（调用方报错）。"""
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        sh = store.load().get_shot(shot_id)
        if sh and sh.first_frame.url_ready:
            return sh.first_frame.selected.url
        await asyncio.sleep(interval)
    return None


async def _handler_first_frame(job, emit):
    """生成 N 候选分镜图（图生图/文生图），存 local + 公网 url，status=pending 等用户选。"""
    from src.core.store import ProjectStore
    from src.core.models import Asset, ShotStatus
    controller = get_controller()
    project_dir = controller.project_manager.get_project_dir(job.project)
    store = ProjectStore(project_dir)
    project = store.load()
    shot = project.get_shot(job.shot_id)
    if shot is None:
        raise RuntimeError(f"镜头 {job.shot_id} 不存在")
    force = bool(job.params.get("force", False))
    if shot.locked and not force:
        await emit("skip", f"{shot.shot_id} 已锁定，跳过", 100); return
    if shot.first_frame.ready and not force:
        await emit("skip", f"{shot.shot_id} 已有分镜图，跳过（force=True 可重生成）", 100); return

    variants = max(1, int(job.params.get("variants", 2)))
    size_tier = project.style.size_tier or "1K"
    ratio = project.style.aspect_ratio or "16:9"
    refs = _shot_char_refs(project, shot)
    use_i2i = bool(refs)
    prompt = build_i2i_prompt(project, shot) if use_i2i else build_shot_prompt(project, shot)
    frames_dir = project_dir / "output" / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    candidates = []
    for i in range(variants):
        await emit("gen", f"{shot.shot_id} 生成候选 {i+1}/{variants}", int(i / variants * 100))
        seed = (shot.seed + i) if shot.seed is not None else None   # 多候选要有变化
        res = await controller.agnes_client.generate_image(
            prompt=prompt, image_refs=refs if use_i2i else None,
            size_tier=size_tier, ratio=ratio, seed=seed)
        url = res.get("url")
        if not url:
            raise RuntimeError("图片生成未返回公网 URL")
        dest = frames_dir / f"{shot.shot_id}_first_v{i+1}.png"
        await controller.agnes_client.download(url, dest)   # 无认证 client，复用于图片资产
        candidates.append(Asset(local=str(dest), url=url, status=ShotStatus.DONE.value))
        await emit("gen", f"{shot.shot_id} 候选 {i+1} 完成", int((i + 1) / variants * 100))

    # 写回：候选各自 done，ImageAsset.status=pending（等用户点选 -> url_ready=False）
    def mut(s):
        s.first_frame.candidates = candidates
        s.first_frame.selected_index = 0
        s.first_frame.status = ShotStatus.PENDING.value
        s.first_frame.error = None
        s.first_frame.updated_at = _time.time()
    store.update_shot(job.shot_id, mut)
    await emit("done", f"{shot.shot_id} 已生成 {len(candidates)} 个候选，待选择", 100)


async def _handler_last_frame(job, emit):
    """生成尾帧（仅 keyframes，图生图用 first_frame.url）。
    生成后若 split_next 非空，桥接拷贝给后继首帧（O2.first_frame = O.last_frame，保证 O->O2 无缝）。"""
    from src.core.store import ProjectStore
    from src.core.models import Asset, ImageAsset, ShotStatus
    controller = get_controller()
    project_dir = controller.project_manager.get_project_dir(job.project)
    store = ProjectStore(project_dir)
    project = store.load()
    shot = project.get_shot(job.shot_id)
    if shot is None:
        raise RuntimeError(f"镜头 {job.shot_id} 不存在")
    if shot.link_mode != "keyframes":
        raise RuntimeError(f"仅 keyframes 模式需要尾帧（当前 {shot.link_mode}）")

    # 有界等待 first_frame.url_ready（bridge 场景首帧来自前驱尾帧，可能还没好）
    await emit("wait", f"{shot.shot_id} 等待首帧就绪", 5)
    ff_url = await _await_first_frame_url(store, job.shot_id, emit)
    if not ff_url:
        raise RuntimeError("首帧缺少公网 URL（未选择候选或未生成），无法生成尾帧")

    await emit("gen", f"{shot.shot_id} 生成尾帧", 20)
    prompt = build_last_frame_prompt(project, shot)
    res = await controller.agnes_client.generate_image(
        prompt=prompt, image_refs=[ff_url],
        size_tier=project.style.size_tier or "1K", ratio=project.style.aspect_ratio or "16:9")
    url = res.get("url")
    if not url:
        raise RuntimeError("尾帧生成未返回公网 URL")
    dest = project_dir / "output" / "frames" / f"{shot.shot_id}_last.png"
    await controller.agnes_client.download(url, dest)
    store.update_shot(job.shot_id, lambda s: s.last_frame.mark_done(str(dest), url))
    await emit("done", f"{shot.shot_id} 尾帧完成", 90)

    # 🔴 桥接拷贝：split_next 非空且后继首帧未就绪时，把尾帧拷给后继首帧
    nxt = getattr(shot, "split_next", None)
    if nxt:
        succ = project.get_shot(nxt)
        if succ is not None and not succ.first_frame.ready:
            def mut_succ(s, d=str(dest), u=url):
                s.first_frame = ImageAsset(
                    candidates=[Asset(local=d, url=u, status=ShotStatus.DONE.value)],
                    selected_index=0, status=ShotStatus.DONE.value)
            store.update_shot(nxt, mut_succ)
            await emit("bridge", f"{shot.shot_id} 尾帧桥接为 {nxt} 首帧", 100)
        else:
            await emit("done", f"{shot.shot_id} 尾帧完成（后继 {nxt} 已有首帧，不覆盖）", 100)
    else:
        await emit("done", f"{shot.shot_id} 尾帧完成", 100)


JOB_HANDLERS["first_frame"] = _handler_first_frame
JOB_HANDLERS["last_frame"] = _handler_last_frame


class FirstFrameRequest(BaseModel):
    variants: int = 2
    force: bool = False


class ChooseFrameRequest(BaseModel):
    variant: int = 1   # 1-based


@app.post("/api/project/{project_name}/shot/{shot_id}/first-frame")
async def shot_first_frame(project_name: str, shot_id: str, req: FirstFrameRequest):
    """生成单镜头分镜图候选（入队 first_frame 任务，进度走全局 SSE）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        from src.core.store import ProjectStore
        if ProjectStore(project_dir).load().get_shot(shot_id) is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        q = get_job_queue()
        job = q.submit(project_name, "first_frame", shot_id,
                       variants=req.variants, force=req.force)
        return {"job_id": job.job_id, "shot_id": shot_id}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/project/{project_name}/shot/{shot_id}/first-frame/choose")
async def choose_first_frame(project_name: str, shot_id: str, req: ChooseFrameRequest):
    """选择分镜图候选：设 selected_index + first_frame.status=done（url_ready=True，放行视频）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        from src.core.store import ProjectStore
        from src.core.models import ShotStatus
        store = ProjectStore(project_dir)
        shot = store.load().get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        if not shot.first_frame.candidates:
            raise HTTPException(400, "该镜头尚无分镜图候选，请先生成")
        idx = req.variant - 1
        if not (0 <= idx < len(shot.first_frame.candidates)):
            raise HTTPException(400, f"variant 超出范围（1~{len(shot.first_frame.candidates)}）")
        store.update_shot(shot_id, lambda s, i=idx: (
            s.first_frame.select(i),
            setattr(s.first_frame, "status", ShotStatus.DONE.value),
        ))
        return {"shot_id": shot_id, "selected_index": idx,
                "url": shot.first_frame.candidates[idx].url}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/project/{project_name}/shot/{shot_id}/last-frame")
async def shot_last_frame(project_name: str, shot_id: str):
    """生成单镜头尾帧（仅 keyframes；入队 last_frame 任务）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        from src.core.store import ProjectStore
        shot = ProjectStore(project_dir).load().get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        if shot.link_mode != "keyframes":
            raise HTTPException(400, f"仅 keyframes 模式需要尾帧（当前 {shot.link_mode}）")
        q = get_job_queue()
        job = q.submit(project_name, "last_frame", shot_id)
        return {"job_id": job.job_id, "shot_id": shot_id}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ==================== 阶段 3：镜头视频生成（shot 级，走 JobQueue 串行视频 worker）====================
from src.api.agnes_client import resolve_size as _resolve_size


def _video_preflight(shot, quota, seconds):
    """视频前置硬校验（缺一不可，不通过就不提交，省 60s）。
    仅检查「硬失败」；keyframes 尾帧 in-progress 的有界等待在 handler 里做。"""
    from src.core.models import ShotStatus
    if shot.locked:
        return False, "镜头已锁定"
    if not shot.video.num_frames:
        return False, "尚未配音，无法确定视频时长（请先配音）"
    if not shot.first_frame.url_ready:
        return False, "首帧缺少公网 URL（未选择候选或未生成），请先生成并选择首帧"
    if shot.link_mode == "keyframes":
        if shot.last_frame.status == ShotStatus.FAILED.value:
            return False, "尾帧生成失败，请重新生成尾帧"
    if quota:
        ok, why = quota.can("video_seconds", seconds)
        if not ok:
            return False, f"配额不足: {why}"
    return True, ""


async def _await_last_frame_url(store, shot_id, emit, timeout=180, interval=5):
    """有界等待 last_frame.url_ready（keyframes 尾帧可能还在生成）。超时返回 None。"""
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        sh = store.load().get_shot(shot_id)
        if sh and sh.last_frame.url_ready:
            return sh.last_frame.selected.url
        await asyncio.sleep(interval)
    return None


async def _handler_video(job, emit):
    """镜头视频生成（PHASE_3 §3.4）。
    preflight -> keyframes 等尾帧 -> 提交立刻落库 video_id -> 轮询 -> 下载 -> 回读秒数/尺寸。
    🔴 已有 video_id 且 generating 时直接续轮询，不重提交、不重扣配额。"""
    from src.core.store import ProjectStore
    from src.core.prompt_builder import build_video_prompt
    from src.core.models import ShotStatus
    controller = get_controller()
    project_dir = controller.project_manager.get_project_dir(job.project)
    store = ProjectStore(project_dir)
    project = store.load()
    shot = project.get_shot(job.shot_id)
    if shot is None:
        raise RuntimeError(f"镜头 {job.shot_id} 不存在")

    seconds = shot.video.num_frames / max(shot.video.frame_rate, 1)
    ok, why = _video_preflight(shot, controller.agnes_client.quota, seconds)
    if not ok:
        raise RuntimeError(why)   # 不发起 API 请求

    mode = "keyframes" if shot.link_mode == "keyframes" else "i2v"
    # keyframes 尾帧 in-progress 有界等待
    if mode == "keyframes" and not shot.last_frame.url_ready:
        await emit("wait", f"{shot.shot_id} 等待尾帧就绪", 5)
        if not await _await_last_frame_url(store, job.shot_id, emit):
            raise RuntimeError("等待尾帧超时（尾帧尚未生成完成），请重试")
        shot = store.load().get_shot(job.shot_id)   # 取最新

    prompt = build_video_prompt(shot, mode)
    w, h = map(int, _resolve_size(project.style.size_tier or "1K",
                                  project.style.aspect_ratio or "16:9").split("x"))

    # 🔴 恢复：已有 video_id 且 generating -> 续轮询，不重提交
    if shot.video.video_id and shot.video.status == ShotStatus.GENERATING.value:
        vid = shot.video.video_id
        await emit("recover", f"{shot.shot_id} 恢复轮询 video_id={vid}", 10)
    else:
        await emit("submit", f"{shot.shot_id} 提交中（视频 1 RPM，可能等约 60s）", 10)
        kwargs = dict(num_frames=shot.video.num_frames, frame_rate=shot.video.frame_rate,
                      width=w, height=h, seed=shot.seed, negative_prompt=shot.negative_prompt)
        if mode == "keyframes":
            kwargs["image_urls"] = [shot.first_frame.selected.url, shot.last_frame.selected.url]
        else:
            kwargs["image_url"] = shot.first_frame.selected.url
        created = await controller.agnes_client.create_video(prompt, mode=mode, **kwargs)
        vid = created.get("video_id") or created.get("task_id") or created.get("id")
        if not vid:
            raise RuntimeError(f"视频任务创建无 video_id: {created}")
        # 🔴 提交成功立刻落库 video_id（崩了重启能接着轮询，不重提交、不重扣配额）
        store.update_shot(job.shot_id, lambda s, v=vid, c=created: (
            setattr(s.video, "video_id", v),
            setattr(s.video, "task_id", c.get("task_id")),
            setattr(s.video, "status", ShotStatus.GENERATING.value),
            setattr(s.video, "attempts", s.video.attempts + 1),
        ))
        await emit("submit", f"{shot.shot_id} 已提交 video_id={vid}", 15)

    # 轮询（5s，走 text 桶不占视频桶）
    async def on_prog(pct, st, waited):
        await emit("poll", f"{shot.shot_id} {st} {pct}%（已等 {int(waited)}s）", int(pct))
    res = await controller.agnes_client.poll_video(vid, interval=5, on_progress=on_prog)

    # 下载
    dest = project_dir / "output" / "videos" / f"{shot.shot_id}.mp4"
    await controller.agnes_client.download(res["url"], dest)

    # 🔴 回读入库（seconds/size 是服务端归一化后的真实值，FACTS §3.6）
    store.update_shot(job.shot_id, lambda s, d=str(dest), r=res: (
        s.video.mark_done(d, r["url"]),
        setattr(s.video, "seconds_returned", r["seconds"]),
        setattr(s.video, "size_returned", r["size"]),
    ))
    await emit("done", f"{shot.shot_id} 完成，实际 {res['seconds']}s / {res['size']}", 100)


JOB_HANDLERS["video"] = _handler_video


@app.post("/api/project/{project_name}/shot/{shot_id}/video")
async def shot_video(project_name: str, shot_id: str):
    """生成单镜头视频（入队 video 任务，串行 worker，1 RPM）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        from src.core.store import ProjectStore
        if ProjectStore(project_dir).load().get_shot(shot_id) is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        q = get_job_queue()
        job = q.submit(project_name, "video", shot_id)
        return {"job_id": job.job_id, "shot_id": shot_id}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/project/{project_name}/compose")
async def compose_final_video(
    project_name: str, request: Request, background_tasks: BackgroundTasks
):
    """合成最终视频（ffmpeg：Ken Burns/视频段 + 音频 amix）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        data = _read_parsed_script(project_dir)
        scenes = data.get("scenes", [])
        if not scenes:
            raise HTTPException(status_code=400, detail="项目无场景，请先编辑剧本")

        try:
            body = await request.json()
        except Exception:
            body = {}
        scene_duration = float(body.get("scene_duration", 5.0))
        width = int(body.get("width", 1280))
        height = int(body.get("height", 720))
        fps = int(body.get("fps", 24))

        task_id = _new_task("compose")
        background_tasks.add_task(
            _compose_bg, task_id, project_dir, scenes, width, height, fps, scene_duration
        )
        return {"task_id": task_id, "message": "视频合成已启动"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/project/{project_name}/compose-shots")
async def compose_shots_video(project_name: str, request: Request,
                              background_tasks: BackgroundTasks):
    """阶段 2 shot 级合成（concat->整音轨->烧全片SRT，Ken Burns 兜底，无逐镜偏移）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
        try:
            body = await request.json()
        except Exception:
            body = {}
        width = int(body.get("width", 1280))
        height = int(body.get("height", 720))
        fps = int(body.get("fps", 24))
        burn = bool(body.get("burn_subtitles", True))
        task_id = _new_task("compose")
        background_tasks.add_task(
            _compose_shots_bg, task_id, str(project_dir), width, height, fps, burn
        )
        return {"task_id": task_id, "message": "shot 级合成已启动"}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


async def _compose_shots_bg(task_id, project_dir, width, height, fps, burn):
    from src.core.store import ProjectStore
    from src.pipeline.video_composer import VideoComposer
    from src.utils.file_saver import FileSaver
    task_store[task_id]["status"] = "running"
    task_store[task_id]["message"] = "合成中..."
    try:
        project = ProjectStore(Path(project_dir)).load()
        composer = VideoComposer(FileSaver(Path(project_dir)))

        def on_progress(msg, pct):
            task_store[task_id]["message"] = msg
            if pct is not None:
                task_store[task_id]["progress"] = {"percent": pct}

        final = composer.compose_shots(project, width=width, height=height,
                                       fps=fps, burn_subtitles=burn,
                                       on_progress=on_progress)
        task_store[task_id]["status"] = "completed"
        task_store[task_id]["result"] = {"video_path": str(final)}
        task_store[task_id]["message"] = "合成完成"
        logger.info(f"shot 级合成完成: {final}")
    except Exception as e:
        task_store[task_id]["status"] = "failed"
        task_store[task_id]["message"] = f"失败: {e}"
        logger.error(f"shot 级合成失败: {e}")


async def _compose_bg(task_id, project_dir, scenes, width, height, fps, scene_duration):
    """后台合成最终视频"""
    task_store[task_id]["status"] = "running"
    task_store[task_id]["message"] = "合成中..."
    try:
        from src.pipeline.video_composer import VideoComposer
        from src.utils.file_saver import FileSaver

        file_saver = FileSaver(project_dir)
        composer = VideoComposer(file_saver)

        def on_progress(msg, pct):
            task_store[task_id]["message"] = msg
            if pct is not None:
                task_store[task_id]["progress"] = {"percent": pct}

        final_path = composer.compose(
            scenes, width=width, height=height, fps=fps,
            scene_duration=scene_duration, on_progress=on_progress,
        )
        task_store[task_id]["status"] = "completed"
        task_store[task_id]["result"] = {"video_path": str(final_path)}
        task_store[task_id]["message"] = "合成完成"
        logger.info(f"最终视频合成完成: {final_path}")
    except Exception as e:
        task_store[task_id]["status"] = "failed"
        task_store[task_id]["message"] = f"合成失败: {e}"
        logger.error(f"视频合成失败: {e}")


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
