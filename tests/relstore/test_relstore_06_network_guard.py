# tests/relstore/test_relstore_06_network_guard.py
# 矩阵 9：socket 层封锁实证、真实写适配器硬禁用、relstore 零网络库/零 .env 依赖。
import socket
from pathlib import Path

import pytest

from tools.relstore import release_adapter
from tools.relstore.errors import RemoteWriteDisabled
from tools.relstore.release_adapter import G2DisabledReleaseAdapter

RELSTORE_DIR = Path(__file__).resolve().parents[2] / "tools" / "relstore"


def test_external_socket_blocked_loopback_allowed():
    assert getattr(socket, "_relstore_network_blocked", False)
    with pytest.raises(RuntimeError, match="禁止外部连接|禁止 create"):
        socket.create_connection(("example.com", 443), timeout=1)
    with pytest.raises(RuntimeError, match="禁止外部 DNS"):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(RuntimeError, match="禁止外部连接"):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("1.2.3.4", 80))
    # loopback 构造/连接尝试不被策略拦截（拒绝连接是 OS 行为，不是封锁策略）
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", 1))
    except (ConnectionRefusedError, OSError):
        pass
    # 本地 DNS 解析允许（asyncio 自举需要）
    socket.getaddrinfo("127.0.0.1", 0)


def test_disabled_adapter_rejects_all_writes():
    a = G2DisabledReleaseAdapter()
    for method, args in [
        ("create_release", ("t",)),
        ("upload_asset", ("r", "n", b"x", "0" * 64)),
        ("get_asset", ("r", "n")),
        ("download_asset", ("a",)),
        ("list_assets", ("r",)),
        ("publish_index", ("p", {})),
    ]:
        with pytest.raises(RemoteWriteDisabled):
            getattr(a, method)(*args)
    assert a.call_log() == []


def test_relstore_source_network_imports_are_lazy_and_sanctioned():
    # G3-A 起允许真实 HTTP transport，但只允许：
    # - httpx【惰性】导入于 HttpxTransport 内（模块顶层导入即失败→G3-A 单测进程零网络栈加载）
    # - urllib 仅 parse（URL 转义），禁止 urllib.request/http.client（真实抓取只能走 httpx）
    offenders = []
    for p in RELSTORE_DIR.rglob("*.py"):
        lines = p.read_text(encoding="utf-8").splitlines()
        top = "\n".join(l for l in lines if l and not l.startswith((" ", "\t")))
        if "import httpx" in top or "from httpx" in top:
            offenders.append((p.name, "httpx 顶层导入（必须惰性）"))
        for b in ("import requests", "from requests", "import urllib.request",
                  "from urllib.request", "import http.client",
                  "dotenv", "load_dotenv"):
            if b in "\n".join(lines):
                offenders.append((p.name, b))
    assert offenders == []
    # 惰性 httpx 仅存在于明确授权的真实适配器
    gha = (RELSTORE_DIR / "github_release_adapter.py").read_text(encoding="utf-8")
    assert "import httpx" in gha
    assert gha.count("import httpx") == 1
    # G2 核心模块仍零网络库（只查 import 形态，允许注释文字）
    import re as _re
    net_import = _re.compile(r"^\s*(import|from)\s+(httpx|requests|urllib\.request|http\.client)",
                             _re.M)
    for name in ("archive_campaign.py", "manifest.py", "safe_tar.py",
                 "path_rebase.py", "fake_release_adapter.py", "restore_campaign.py"):
        txt = (RELSTORE_DIR / name).read_text(encoding="utf-8")
        assert not net_import.search(txt), name


def test_relstore_source_never_reads_env_values():
    # 禁止【代码】读 os.environ/getenv/dotenv；注释行内的说明文字允许
    for p in RELSTORE_DIR.rglob("*.py"):
        code_lines = [ln for ln in p.read_text(encoding="utf-8").splitlines()
                      if ln.strip() and not ln.lstrip().startswith("#")]
        code = "\n".join(code_lines)
        assert "os.environ" not in code, p.name
        assert "load_dotenv" not in code, p.name
        assert "getenv(" not in code, p.name


def test_no_real_post_put_paths_in_adapter_interface():
    # 抽象层只定义接口；G2Disabled 是唯一非 fake 实现
    txt = (RELSTORE_DIR / "release_adapter.py").read_text(encoding="utf-8")
    for verb in ("requests.post", "requests.put", "httpx.put", "http_post"):
        assert verb not in txt
