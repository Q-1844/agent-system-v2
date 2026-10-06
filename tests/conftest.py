"""pytest fixtures：TestClient 共享一个 app 实例（session 级）。

阶段 5 测试骨架（PHASE_5 §5.4）：
  - 第 1 层 连通性 + 第 3 层 接口测试 用 TestClient（不真调 Agnes）
  - 第 2 层 静态分析 由 py_compile / vue-tsc 覆盖
  - 第 4 层 端到端 用 _p4_accept 手动回归（Task8）
  - core 纯函数单元测试不依赖 TestClient

mock_agnes（httpx MockTransport 拦截 Agnes）留待下轮：调 Agnes 的接口
（first-frame/video/audio）测试需要它，本轮先钉死不调 Agnes 的接口与 core 行为。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os as _os
# Phase48 E 测试隔离闸门：client fixture 以真实 projects 目录启动 api_server app，
# 其启动恢复（recover_workflows + settler.recover_tasks/轮询）会把开发机上活着的
# 真机 run/在途任务误判为"重启遗留"并杀掉（真机实证 2026-10-06 18:53 假停靠/
# 19:13 杀活任务）。测试进程禁用启动恢复；恢复语义测试自建引擎/夹具
# （test_p46_workflow_engine 恢复对账路径不受影响——那是显式调用，非启动钩子）。
_os.environ.setdefault("WF_STARTUP_RECOVERY_DISABLE", "1")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    """启动 api_server 的 FastAPI app（触发 startup：controller + JobQueue 初始化）。

    不真调 Agnes：startup 只初始化 AgnesClient（连接池/限流/配额），不发 HTTP。
    recover_pending_videos 会扫 projects/，但 _p4_accept 的 video 都已 done，不触发 poll。
    """
    import api_server
    with TestClient(api_server.app) as c:
        yield c


# ============ deps 全局状态守卫（Freeze Stabilization 2026-09-13）============
# startup() 会把 pipeline_controller/agnes_client/sensenova_text_client/
# project_manager/job_queue 写进 deps 模块单例；client fixture 是 session 级——
# 第一个 client 测试之后，整个进程的引擎级测试都继承「已配置」环境：
#   - not_configured 分支不可达（依赖开发机恰好没配 API key）；
#   - 「不联网」的控制/意图测试被改道到真 Agnes（intent 分类漂移）。
# 守卫语义（两态模型）：
#   - client 测试：进入前确保 configured 快照在位（session startup 只跑一次，
#     中间的非 client 测试可能已把单例复位）；结束时把当前 configured 形态
#     刷新进快照，供后续 client 测试复用。
#   - 非 client 测试：结束后恢复 pristine（全 None）——引擎级测试的 canonical
#     环境，杜绝跨测试环境污染。
_DEPS_GUARD_KEYS = ("pipeline_controller", "agnes_client", "sensenova_text_client",
                    "project_manager", "job_queue", "_rehost_host")
_CONFIGURED_SNAPSHOT: dict = {}


@pytest.fixture(autouse=True)
def _deps_state_guard(request):
    import deps
    uses_client = "client" in getattr(request, "fixturenames", ())
    if uses_client:
        # configured 快照回灌（前一个非 client 测试可能已复位为 pristine）
        for k, v in _CONFIGURED_SNAPSHOT.items():
            if getattr(deps, k, None) is None and v is not None:
                setattr(deps, k, v)
    saved = {k: getattr(deps, k, None) for k in _DEPS_GUARD_KEYS}
    yield
    if uses_client:
        for k in _DEPS_GUARD_KEYS:
            cur = getattr(deps, k, None)
            if cur is not None:
                _CONFIGURED_SNAPSHOT[k] = cur
        target = saved                     # client 测试：维持进入时形态
    else:
        target = {k: None for k in _DEPS_GUARD_KEYS}   # 非 client：pristine
    for k, v in target.items():
        setattr(deps, k, v)


# 回归基准项目（阶段 4 跑通完整链路；Task8 全过后才删）
P4 = "_p4_accept"
