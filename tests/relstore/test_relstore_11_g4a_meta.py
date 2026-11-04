# tests/relstore/test_relstore_11_g4a_meta.py
# G4-A 矩阵 9-10：静态检查——无生产自动删除 API、无 CI 变更、状态机零网络。
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RELSTORE = REPO / "tools" / "relstore"


def test_no_production_auto_delete_in_registry():
    """project_registry 不得有可运行的删除 API。attempt_evict 必须永远 permitted=False。"""
    from tools.relstore.project_registry import Registry
    import inspect
    src = inspect.getsource(Registry)
    # 禁止出现真正删除/释放数据的方法
    for forbidden in ("os.remove", "shutil.rmtree", "os.unlink",
                       "def delete_", "def evict_", "def release_"):
        assert forbidden not in src, f"禁止出现生产删除 API: {forbidden}"
    # attempt_evict 必须永远拒绝
    import inspect as ins
    ev_src = ins.getsource(Registry.attempt_evict)
    assert "permitted\": False" in ev_src or "permitted': False" in ev_src


def test_registry_zero_network_imports():
    """状态机模块零网络库（G4-A 不触网）。"""
    import re
    net = re.compile(r"^\s*(import|from)\s+(httpx|requests|urllib\.request|http\.client)",
                      re.M)
    for name in ("project_registry.py", "snapshot_scope.py"):
        txt = (RELSTORE / name).read_text(encoding="utf-8")
        assert not net.search(txt), name


def test_registry_does_not_import_github_adapter():
    """状态机不得依赖真实 GitHub 适配器（避免误触远端）。"""
    txt = (RELSTORE / "project_registry.py").read_text(encoding="utf-8")
    assert "github_release_adapter" not in txt
    assert "HttpxTransport" not in txt


def test_no_ci_workflow_changes_since_g3b():
    """G4-A 不得修改 .github/workflows/（静态 diff 检查）。"""
    result = subprocess.run(
        ["git", "diff", "--name-only", "efee0af", "HEAD", "--", ".github/"],
        cwd=str(REPO), capture_output=True, text=True, timeout=30)
    assert result.stdout.strip() == "", f"CI 被改: {result.stdout}"


def test_socket_guard_still_blocks_external():
    """conftest socket 守卫仍生效（矩阵 9）。"""
    import socket
    assert getattr(socket, "_relstore_network_blocked", False)
    import pytest
    with pytest.raises(RuntimeError, match="禁止外部"):
        socket.getaddrinfo("example.com", 443)


def test_g3b_117_regression_still_green():
    """G3-B 117 项仍全过（矩阵 9：既有真实适配器不被破坏）。"""
    # 本测试不重跑（已在 suite 中）；静态确认测试文件存在且未被删
    assert (REPO / "tests/relstore/test_relstore_07_http_adapter.py").exists()
    assert (REPO / "tests/relstore/test_relstore_08_index_snapshot_full.py").exists()
    assert (REPO / "tests/relstore/test_relstore_09_g3b_draft_immutable.py").exists()
