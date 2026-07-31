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


# 回归基准项目（阶段 4 跑通完整链路；Task8 全过后才删）
P4 = "_p4_accept"
