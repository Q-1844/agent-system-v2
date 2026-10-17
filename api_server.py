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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.pipeline.controller import PipelineController

# 共享状态/单例/getter/配置/路径都在 deps（阶段5③拆分：避免 api_server <-> router 循环导入）。
# 🔴 reassigned 单例（pipeline_controller/job_queue/_rehost_host）用 deps.X 属性访问，
#    不能 from deps import（会绑死 import 时的 None，startup 赋值后看不到）。
import deps
from deps import logger, BASE_DIR, projects_dir, _APP_CFG, JOB_HANDLERS
from services import lifecycle  # 启动期：崩溃恢复 + 孤儿 job 判定

app = FastAPI(title="动画分镜工作流 API", version="1.0.0")

# 静态文件挂载（projects_dir / BASE_DIR / 配置 / 单例 / getter 都在 deps.py）
app.mount("/projects", StaticFiles(directory=str(projects_dir)), name="projects")
# Phase 1C3/1C4：共享库媒体静态服务（shared_library/media/...；目录不存在则创建空挂载）
from src.resources.shared_library import default_shared_dir as _shared_dir_fn  # noqa: E402
_shared_dir_fn().mkdir(parents=True, exist_ok=True)
app.mount("/shared_library", StaticFiles(directory=str(_shared_dir_fn())), name="shared_library")

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
from routers import scenes  # noqa: E402
from routers import story  # noqa: E402
from routers import character_design  # noqa: E402
from routers import director  # noqa: E402
from routers import production_planner  # noqa: E402
from routers import autopilot  # noqa: E402
from routers import project_autopilot  # noqa: E402
from routers import full_project_autopilot  # noqa: E402
from routers import workspaces  # noqa: E402  AI-6A Creative Workspace / ChangeSet
from routers import agents  # noqa: E402  AI-6B Multi-Agent Foundation
from routers import resources  # noqa: E402  Production Resources V1 Phase 1B (read-only)
from routers import scene_system  # noqa: E402  Scene System V1 Phase S1（空间事实系统；不与 /scenes 冲突）
from routers import sequences  # noqa: E402  Sequence S1 Assembly Foundation（24.md；Shot Production 的消费者）
from routers import shot_binding  # noqa: E402  Shot Production Binding HTTP 层（S3B.4；业务在 src/shot_binding）
from routers import experiments  # noqa: E402  Phase 37A Calibration Trace（37.md §2/§16；非 canonical 实验记录）
from routers import workflow as workflow_router  # noqa: E402  Phase 46 六员可视化工作流（定义/校验/编译/顺序执行/审核/恢复）
app.include_router(system.router)
app.include_router(projects.router)
app.include_router(script.router)
app.include_router(characters.router)
app.include_router(compose.router)
app.include_router(jobs.router)
app.include_router(shots.router)
app.include_router(scenes.router)
app.include_router(story.router)
app.include_router(character_design.router)
app.include_router(director.router)
app.include_router(production_planner.router)
app.include_router(autopilot.router)
app.include_router(project_autopilot.router)
app.include_router(full_project_autopilot.router)
app.include_router(workspaces.router)
app.include_router(agents.router)   # AI-6B Multi-Agent Foundation
app.include_router(resources.router)   # Production Resources V1 Phase 1B (read-only)
app.include_router(scene_system.router)   # Scene System V1 Phase S1（Location/Anchor/Layout/View/SceneInstance）
app.include_router(sequences.router)   # Sequence S1 Assembly Foundation（24.md；hard cut + trim + render versioning）
app.include_router(shot_binding.router)   # Shot Production Binding（S3B.4；BOUND 生成参考）
app.include_router(experiments.router)   # Calibration Trace（37A；尾帧接力校准实验记录）
app.include_router(workflow_router.router)   # Phase 46 可视化工作流后端（单条顺序链首版）


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
        # 阶段5③-b 单例化：从 controller 拆出 agnes_client / project_manager，供各 router
        # 用 get_agnes_client()/get_project_manager() 直接注入（42 处旧 controller.X 引用已改）。
        # 构造逻辑（config 加载 + ${AGNES_API_KEY} 环境变量替换 + 实例化）仍留在
        # PipelineController，本轮不动；controller 容器整体删除留待垫片1/2 清理轮。
        deps.agnes_client = deps.pipeline_controller.agnes_client
        # SenseNova 文本客户端（Agent 模型切换用；sensenova 块不完整时为 None，运行时回落 Agnes）
        deps.sensenova_text_client = deps.pipeline_controller.sensenova_text_client
        deps.project_manager = deps.pipeline_controller.project_manager
        # Production Resources 1C1.2：启动时清理孤儿 generating（崩溃/kill 遗留；
        # 此刻无 in-flight generation，判定无歧义）。失败仅告警，不阻塞启动。
        try:
            from src.resources.recovery import recover_all_projects
            _rec = await recover_all_projects(deps.project_manager)
            if _rec.get("total_recovered") or _rec.get("errors"):
                logger.warning("资源生成孤儿恢复: recovered=%s errors=%s",
                               _rec.get("total_recovered"), _rec.get("errors"))
        except Exception as _rec_err:
            logger.warning("资源生成孤儿恢复跳过: %s", _rec_err)
        # Scene System S2A §15：LocationView 孤儿 generating 启动清理（同判定依据，
        # 同幂等语义；无 daemon 无轮询）。失败仅告警，不阻塞启动。
        try:
            from src.scene_system.recovery import recover_all_projects as _scene_recover
            _srec = await _scene_recover(deps.project_manager)
            if _srec.get("total_recovered") or _srec.get("errors"):
                logger.warning("场景视图生成孤儿恢复: recovered=%s errors=%s",
                               _srec.get("total_recovered"), _srec.get("errors"))
        except Exception as _srec_err:
            logger.warning("场景视图生成孤儿恢复跳过: %s", _srec_err)
        logger.info("API服务器已启动")
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        # 即使初始化失败也允许服务器运行，返回友好错误

    # 初始化默认角色到角色库
    characters._init_default_characters()

    # 阶段 3：启动全局任务队列 + 崩溃恢复（恢复在后台跑，不阻塞服务就绪）
    try:
        from src.core.job_queue import JobQueue
        _video_inflight = None
        try:
            # Phase55 C：并发档位进 config（concurrency.video_inflight，默认 1=串行
            # 基线不变；AGNES_VIDEO_INFLIGHT env 仍为最高优先逃生口）。
            _video_inflight = int(((_APP_CFG or {}).get("concurrency") or {})
                                  .get("video_inflight") or 0) or None
        except Exception:
            _video_inflight = None
        deps.job_queue = JobQueue(BASE_DIR / "cache" / "jobs.json", JOB_HANDLERS,
                                  video_worker_count=_video_inflight)
        # 🔴 清理孤儿 job（project/shot 已删除的 running/queued）--在 start route 前，孤儿不进 worker
        purged = deps.job_queue.purge_orphans(lifecycle.job_target_exists)
        if purged:
            logger.info(f"启动时清理 {purged} 个孤儿 job")
        await deps.job_queue.start()
        asyncio.create_task(lifecycle.recover_pending_videos())
        logger.info("全局任务队列已就绪")
    except Exception as e:
        logger.error(f"任务队列初始化失败: {e}")

    # 阶段 AI-4B：恢复中断的 Autopilot Run（running/pausing）。
    # active job 由 JobQueue 恢复 + find_by_origin 复用，不重复 submit（§三十二）。
    try:
        from src.ai.autopilot import get_autopilot_manager
        asyncio.create_task(get_autopilot_manager().recover_runs())
    except Exception as e:
        logger.error(f"Autopilot 恢复失败: {e}")

    # 阶段 AI-5A：恢复中断的 ProjectRun（running/pausing/composing）。
    # 🔴 必须在 SceneAutopilotManager.recover_runs() 之后（Project 恢复时需要看到 child 正确状态，§三十）。
    try:
        from src.ai.project_autopilot import get_project_autopilot_manager
        asyncio.create_task(get_project_autopilot_manager().recover_runs())
    except Exception as e:
        logger.error(f"ProjectRun 恢复失败: {e}")

    # 阶段 AI-5B：恢复中断的 FullProjectRun（preparing/pausing/producing）。
    # 🔴 必须在 ProjectProductionManager.recover_runs() 之后（FullProject 最外层，恢复时需看到
    # production child 正确状态，§四十七）。
    try:
        from src.ai.full_project_autopilot import get_full_project_manager
        asyncio.create_task(get_full_project_manager().recover_runs())
    except Exception as e:
        logger.error(f"FullProjectRun 恢复失败: {e}")

    # 阶段 AI-6F.0：AgentTask durable settlement 对账（§10/§12）。
    # 🔴 必须在 JobQueue.start() + 各 ProductionRun 恢复之后：本 bridge 扫描所有项目 running
    # AgentTask，据 linked Job（已被 JobQueue._load 恢复）状态 reattach/settle，不重复 submit。
    # 先一次性 recover_tasks（startup 对账），再启后台 poll loop（§12 polling fallback——
    # 正确性不依赖内存 SSE，持久态是真相）。
    # Phase48 E：WF_STARTUP_RECOVERY_DISABLE=1 时跳过（测试进程隔离闸门——conftest
    # 的 session 级 client 会以真实 projects 目录启动本 app，其启动恢复/结算轮询
    # 会把开发机上活着的真机 run/任务误判为"重启遗留"并杀掉，真机实证两次：
    # 2026-10-06 18:53 假停靠、19:13 杀活任务）。
    _wf_rec_off = os.environ.get("WF_STARTUP_RECOVERY_DISABLE") == "1"
    if not _wf_rec_off:
        try:
            from src.agents.settlement import get_settler
            settler = get_settler()
            asyncio.create_task(settler.recover_tasks())
            await settler.start()
            logger.info("AgentTask settlement bridge 已就绪")
        except Exception as e:
            logger.error(f"AgentTask settlement 恢复失败: {e}")
    else:
        logger.info("AgentTask settlement bridge 已按 WF_STARTUP_RECOVERY_DISABLE 跳过")

    # 阶段 AI-7A：Team Chat 持久化恢复（§54/§57）。扫各项目 team thread 的 pending user
    # 消息，标中断、不重跑 LLM（§57 no infinite loop）。persistence 即真相，polling-first。
    try:
        from src.agents.team_chat import recover_team_chat
        pdir = deps.projects_dir
        n = 0
        if pdir.exists():
            for d in pdir.iterdir():
                if d.is_dir() and (d / "output" / "project.json").exists():
                    n += recover_team_chat(d, d.name)
        if n:
            logger.info(f"Team Chat 恢复：标记 {n} 条中断分析")
        logger.info("Team Chat 持久化恢复已就绪")
    except Exception as e:
        logger.error(f"Team Chat 恢复失败: {e}")

    # 阶段 AI-7A：Change Analysis Session 恢复（§55/§57）。扫非终端会话，stuck running 咨询
    # 标 failed（中断），会话→blocked。不自动重跑 LLM（§57）；用户显式 /resume 才继续。
    try:
        from src.agents.change_analysis import recover_change_analysis
        pdir2 = deps.projects_dir
        n2 = 0
        if pdir2.exists():
            for d in pdir2.iterdir():
                if d.is_dir() and (d / "output" / "project.json").exists():
                    n2 += recover_change_analysis(d, d.name)
        if n2:
            logger.info(f"Change Analysis 恢复：标记 {n2} 个中断会话")
        logger.info("Change Analysis 恢复已就绪")
    except Exception as e:
        logger.error(f"Change Analysis 恢复失败: {e}")

    # 阶段 AI-7B：Change Protocol Session 恢复（§87）。扫非终端会话，in-flight 子态→blocked，
    # stuck-running proposal→failed。不自动重跑 LLM（§57/§87）；用户显式 /resume 才继续。
    # 不 apply、不写 canonical/Workspace/Production（§2/§76 硬边界）。
    try:
        from src.agents.change_protocol.migrations import recover_change_protocol
        pdir3 = deps.projects_dir
        n3 = 0
        if pdir3.exists():
            for d in pdir3.iterdir():
                if d.is_dir() and (d / "output" / "project.json").exists():
                    n3 += recover_change_protocol(d, d.name)
        if n3:
            logger.info(f"Change Protocol 恢复：标记 {n3} 个中断会话")
        logger.info("Change Protocol 恢复已就绪")
    except Exception as e:
        logger.error(f"Change Protocol 恢复失败: {e}")

    # 阶段 AI-7C：ChangeExecutionSession startup discovery（§73/§96）。扫各项目非终态 execution，
    # 记账待恢复。AI-7C-1 仅发现+记账，不自动推进（workspace replay/commit/production 在后续 gate）。
    try:
        from src.agents.change_protocol.execution_service import recover_executions
        pdir4 = deps.projects_dir
        n4 = 0
        if pdir4.exists():
            for d in pdir4.iterdir():
                if d.is_dir() and (d / "output" / "project.json").exists():
                    n4 += recover_executions(d, d.name)
        if n4:
            logger.info(f"AI-7C Execution 恢复：发现 {n4} 个 active execution")
        logger.info("AI-7C Execution 恢复已就绪")
    except Exception as e:
        logger.error(f"AI-7C Execution 恢复失败: {e}")

    # Phase 46：工作流 run 启动恢复——对账在途 attempt（不自动派发新节点；等待显式
    # 继续/审核决策）。诚实语义见 routers/workflow.recover_workflows。
    # WF_STARTUP_RECOVERY_DISABLE=1 时跳过（测试隔离，见上方 settler 块注释）。
    if not _wf_rec_off:
        try:
            from routers.workflow import recover_workflows
            rec = await recover_workflows()
            if rec.get("scanned") or rec.get("errors"):
                logger.info(f"Workflow 恢复：扫描 {rec.get('scanned')} 个活跃 run，"
                            f"对账 {rec.get('reconciled')} 个在途 attempt"
                            + (f"，错误 {rec['errors']}" if rec.get("errors") else ""))
            logger.info("Workflow 恢复已就绪")
        except Exception as e:
            logger.error(f"Workflow 恢复失败: {e}")
    else:
        logger.info("Workflow 恢复已按 WF_STARTUP_RECOVERY_DISABLE 跳过")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
