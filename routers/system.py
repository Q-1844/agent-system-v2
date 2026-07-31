"""系统路由：根路径 / 健康检查 / 限流配额 / 图片服务 / 设置（PHASE_5 §5.2 system 组）。"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

import deps
from deps import BASE_DIR, projects_dir, logger

router = APIRouter()


@router.get("/")
async def root():
    """根路径 - 返回简易版前端页面"""
    html_path = BASE_DIR / "simple.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    return {"message": "动画分镜工作流 API", "version": "1.0.0"}


@router.get("/api/health")
async def health_check():
    """健康检查"""
    return {
        "status": "ok" if deps.pipeline_controller else "degraded",
        "controller_ready": deps.pipeline_controller is not None,
    }


@router.get("/api/system/limits")
async def system_limits():
    """返回限流器各桶状态与每日配额用量（供前端排队面板展示）。
    阶段 1 新增只读接口，不改任何现有接口。"""
    from src.core.rate_limiter import RateLimiter
    from src.core.quota import QuotaLedger
    return {
        "limits": RateLimiter.get().snapshot(),
        "quota": QuotaLedger.get_default().snapshot(),
    }


@router.get("/api/image/{project_name}/{category}/{filename}")
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


@router.put("/api/settings")
async def update_settings(settings: dict):
    """更新全局设置"""
    try:
        # 这里可以将设置保存到文件
        # 暂时只返回成功
        logger.info(f"设置已更新: {settings}")
        return {"message": "设置已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
