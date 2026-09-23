"""deps.py - 进程级共享状态与依赖（api_server + 各 router 共用，避免循环导入）。

阶段5③拆分（PHASE_5 §5.2）：把 api_server.py 模块级的单例、getter、配置、路径、
任务存储抽到此处。api_server 与各 router 都从此 import，互不循环。

🔴 reassigned 单例（pipeline_controller / agnes_client / project_manager / job_queue /
_rehost_host）在 api_server.startup() 中赋值：
`deps.pipeline_controller = ...` / `deps.job_queue = ...`。
agnes_client / project_manager 由 PipelineController 构造后从 controller 拆出赋值
（构造逻辑/env 替换仍留在 controller，本轮只搬访问点）。
getter（get_controller / get_agnes_client / get_project_manager / get_job_queue /
get_rehost_host）读取本模块 global，未就绪抛 503。
因此引用这些单例时必须用 `deps.X` 属性访问，不能用 `from deps import X`
（后者会在 import 时绑死 None，startup 赋值后看不到）。
dict 单例（JOB_HANDLERS / task_store / task_progress）只 mutate 不 reassign，`from deps import` 安全。
getter 函数本身可 `from deps import`（调用时才读 global）。
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
# agnes_client / project_manager 由 PipelineController 构造后拆出（阶段5③-b 单例化）：
# 构造逻辑（config 加载 + ${AGNES_API_KEY} 环境变量替换 + AgnesClient/ProjectManager 实例化）
# 仍留在 controller.py，本轮只把访问点从 controller.X 改成 get_X() 依赖注入。
agnes_client = None
# SenseNova 文本客户端（2026-08-29 Agent 模型切换用；仅文本，startup 时赋值，可为 None）
sensenova_text_client = None
project_manager = None
job_queue = None


def get_controller():
    """安全获取控制器"""
    if pipeline_controller is None:
        raise HTTPException(
            status_code=503,
            detail="服务未就绪，Pipeline控制器初始化失败。请检查配置文件和API密钥。"
        )
    return pipeline_controller


def get_agnes_client():
    """安全获取 AgnesClient 单例（全项目共享，限流/配额/连接池复用）。未就绪抛 503。"""
    if agnes_client is None:
        raise HTTPException(
            status_code=503,
            detail="服务未就绪，AgnesClient 初始化失败。请检查配置文件和API密钥。"
        )
    return agnes_client


def get_sensenova_text_client():
    """SenseNova 文本客户端（Agent 模型切换用）。未配置/未就绪返回 None（不抛 503）——
    调用方（runtime._resolve_client_or_none）据此回落 Agnes 共享客户端。"""
    return sensenova_text_client


# SenseNova 图片客户端（2026-09-17 全链路验收轮：image provider 对比路径）
_sensenova_image_client = None


def get_sensenova_image_client():
    """SenseNova 图片客户端单例（job.params image_provider="sensenova" 时使用）。
    惰性构造：首次调用读 SENSENOVA_API_KEY 环境变量（.env 已在 startup 注入）。
    未配置抛 503（明确 fail fast，不静默回落 Agnes）。"""
    global _sensenova_image_client
    if _sensenova_image_client is None:
        import os
        key = os.environ.get("SENSENOVA_API_KEY", "").strip()
        if not key:
            raise HTTPException(503, "SENSENOVA_API_KEY 未配置（.env）")
        from src.api.sensenova_client import SensenovaImageClient
        _sensenova_image_client = SensenovaImageClient(key)
    return _sensenova_image_client


def resolve_image_client(provider: str):
    """image_provider → 图片客户端实例（生成端点/handler 统一入口）。
    agnes（默认）| sensenova（商汤 u1.5-lite）。未知值 400。"""
    p = (provider or "agnes").lower()
    if p == "sensenova":
        return get_sensenova_image_client()
    if p == "agnes":
        return get_agnes_client()
    raise HTTPException(400, f"未知 image_provider: {provider}（可选 agnes|sensenova）")


def get_project_manager():
    """安全获取 ProjectManager 单例（项目目录管理）。未就绪抛 503。"""
    if project_manager is None:
        raise HTTPException(
            status_code=503,
            detail="服务未就绪，ProjectManager 初始化失败。"
        )
    return project_manager


def get_job_queue():
    """安全获取全局队列。未就绪返回 503。"""
    if job_queue is None:
        raise HTTPException(503, "任务队列未就绪（服务启动中）")
    return job_queue


# 重新托管单例（imgbb：把本地首帧托管成公网 URL，100% 保真）
_rehost_host = None


def get_rehost_host():
    """安全获取重新托管 ImageHost（单例）。未就绪返回 503。
    🔴 2026-09-24 起默认 github_pages（i.ibb.co SNI 级线路阻断，35.md）——统一工厂
    build_image_host 分派 kind（imgbb 保留为 config 回退开关）。
    用于：视频末帧提取+托管（relay/boundary 物料）/ 首帧补托管 / v1 迁移首帧重托管 / 参考资产 rehost。
    上传失败明示抛错不静默回落。"""
    global _rehost_host
    if _rehost_host is None:
        from src.core.image_host import build_image_host
        ih = dict(_APP_CFG.get("image_host") or {})
        # ${VAR} env 替换（token/key 类字段；与 AGNES_API_KEY 同机制）
        for sect in ("github_pages", "imgbb"):
            sub = ih.get(sect) or {}
            for k, v in list(sub.items()):
                if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                    sub[k] = os.environ.get(v[2:-1], "")
        _rehost_host = build_image_host(ih,
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
