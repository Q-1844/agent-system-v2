"""deps.py - 进程级共享状态与依赖（api_server + 各 router 共用，避免循环导入）。

阶段5③拆分（PHASE_5 §5.2）：把 api_server.py 模块级的单例、getter、配置、路径、
任务存储抽到此处。api_server 与各 router 都从此 import，互不循环。

🔴 reassigned 单例（pipeline_controller / job_queue / _rehost_host）在 api_server.startup()
中赋值：`deps.pipeline_controller = ...` / `deps.job_queue = ...`。
getter（get_controller / get_job_queue / get_rehost_host）读取本模块 global，未就绪抛 503。
因此引用这三个单例时必须用 `deps.X` 属性访问，不能用 `from deps import X`
（后者会在 import 时绑死 None，startup 赋值后看不到）。
dict 单例（JOB_HANDLERS / task_store / task_progress）只 mutate 不 reassign，`from deps import` 安全。
"""
import os
import json
import uuid
import base64 as _base64
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel

from src.utils.logger import setup_logger

logger = setup_logger("api_server")

BASE_DIR = Path(__file__).resolve().parent
projects_dir = BASE_DIR / "projects"
projects_dir.mkdir(parents=True, exist_ok=True)

# ============ 全局配置（config.yaml；Agnes 在 Cloudflare 后，国内需代理。Clash 默认 7890）============
_APP_CFG: dict = {}
_AGNES_PROXY = None
try:
    import yaml as _yaml
    _APP_CFG = _yaml.safe_load((BASE_DIR / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    _AGNES_PROXY = (_APP_CFG.get("agnes") or {}).get("proxy") or None
except Exception as _e:
    logger.warning(f"读取配置失败: {_e}")


# ============ 阶段 3：全局任务队列 ============
# JOB_HANDLERS 是可变 dict，各 handler 定义处注册（first_frame/last_frame/audio/video/compose）。
# startup 时用它在 cache/jobs.json 上构造 JobQueue 并启动 worker（1 视频串行 + 3 轻任务并发）。
JOB_HANDLERS: dict = {}

# 全局单例（startup 中赋值；getter 未就绪抛 503）
pipeline_controller = None
job_queue = None


def get_controller():
    """安全获取控制器"""
    if pipeline_controller is None:
        raise HTTPException(
            status_code=503,
            detail="服务未就绪，Pipeline控制器初始化失败。请检查配置文件和API密钥。"
        )
    return pipeline_controller


def get_job_queue():
    """安全获取全局队列。未就绪返回 503。"""
    if job_queue is None:
        raise HTTPException(503, "任务队列未就绪（服务启动中）")
    return job_queue


# 重新托管单例（imgbb：把本地首帧托管成公网 URL，100% 保真）
_rehost_host = None


def get_rehost_host():
    """安全获取重新托管 ImageHost（imgbb 单例）。未就绪返回 503。
    🔴 imgbb 实测唯一可用方案（2026-07-31）：上传原字节 100% 保真，Agnes 服务端能下载。
    用于把 v1 迁移的本地首帧（local有/url空）补公网 URL（/first-frame/rehost + video preflight 自动调用）。
    按文件哈希缓存（cache/imgbb_cache.json），上传失败明示抛错不静默回落。"""
    global _rehost_host
    if _rehost_host is None:
        from src.core.image_host import ImgbbHost
        ih = (_APP_CFG.get("image_host") or {}).get("imgbb") or {}
        key = ih.get("api_key", "")
        if "${IMGBB_API_KEY}" in str(key):
            key = os.environ.get("IMGBB_API_KEY", "")
        _rehost_host = ImgbbHost(key, ih.get("expiration", 0),
                                 cache_path=str(BASE_DIR / "cache" / "imgbb_cache.json"))
    return _rehost_host


# ============ 简单任务存储（1.7 将重构为 TaskRegistry + SSE）============
task_store: dict = {}
current_task = None
task_progress = {}


def _new_task(task_type: str) -> str:
    """创建任务并返回 task_id"""
    tid = f"{task_type}_{uuid.uuid4().hex[:8]}"
    task_store[tid] = {"status": "pending", "progress": {}, "message": "", "result": None}
    return tid


# ============ parsed_script.json 读写（script + characters 共用）============
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


# ============ base64 图片 IO（characters + scene 灰色路由共用）============
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


# ============ 保留的旧请求/状态模型（纯搬运；阶段5③-b 再清死代码）============
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
