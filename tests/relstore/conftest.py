# tests/relstore/conftest.py
# G2 relstore 测试：session 级 socket 封锁（除被明确标记的本地回环外全部拒绝）+
# 离线样本项目夹具。relstore 本体零网络依赖；真实仓代码（ProjectStore 等）在测试中
# 仅以本地文件/内存方式使用。
import json
import socket
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------- socket 硬封锁（地址感知）----------------
# 允许 socket 构造与 loopback（asyncio ProactorEventLoop self-pipe 需要），
# 拒绝任何非本地连接与外部域名解析。

_BLOCKED_ATTR = "_relstore_network_blocked"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "255.255.255.255",
                None, ""}


def _is_local_addr(addr):
    try:
        host = addr[0]
    except Exception:
        return False
    if host in _LOCAL_HOSTS:
        return True
    if isinstance(host, (bytes, bytearray)):
        host = host.decode()
    return host.startswith("127.") or host == "::1"


@pytest.fixture(scope="session", autouse=True)
def _block_all_network():
    import io as _io
    orig_socket = socket.socket
    orig_create = socket.create_connection
    orig_getaddr = socket.getaddrinfo
    orig_getname = socket.getnameinfo

    class GuardedSocket(orig_socket):
        def connect(self, address):
            if not _is_local_addr(address):
                raise RuntimeError(f"RELSTORE TEST: 禁止外部连接 {address}")
            return super().connect(address)

        def connect_ex(self, address):
            if not _is_local_addr(address):
                raise RuntimeError(f"RELSTORE TEST: 禁止外部连接 {address}")
            return super().connect_ex(address)

    def _create(address, *a, **k):
        if not _is_local_addr(address):
            raise RuntimeError(f"RELSTORE TEST: 禁止 create_connection {address}")
        return orig_create(address, *a, **k)

    def _getaddrinfo(host, *a, **k):
        if host not in _LOCAL_HOSTS:
            raise RuntimeError(f"RELSTORE TEST: 禁止外部 DNS 解析 {host}")
        return orig_getaddr(host, *a, **k)

    def _deny_nameinfo(*a, **k):
        raise RuntimeError("RELSTORE TEST: getnameinfo 被禁止")

    socket.socket = GuardedSocket
    socket.create_connection = _create
    socket.getaddrinfo = _getaddrinfo
    socket.getnameinfo = _deny_nameinfo
    setattr(socket, _BLOCKED_ATTR, True)
    try:
        yield
    finally:
        socket.socket = orig_socket
        socket.create_connection = orig_create
        socket.getaddrinfo = orig_getaddr
        socket.getnameinfo = orig_getname


# ---------------- 样本项目 ----------------

PROJECT_NAME = "SAMPLE_PRJ"


def _project_doc():
    return {
        "name": PROJECT_NAME, "schema_version": 2,
        "created_at": 1792236311.1, "updated_at": 1792242753.9,
        "style": {}, "native_av": {}, "story_text": "sample",
        "characters": [], "next_scene_seq": 3,
        "scenes": [
            {"scene_id": "s1", "title": "t", "location": "", "time_of_day": "",
             "summary": "", "characters": [], "shots": [
                {"shot_id": "s01_c01", "index": 1, "scene_id": "s1",
                 "status": "pending", "video_versions": [], "final_video": None,
                 "first_frame": {"candidates": [
                     {"local": f"projects\\{PROJECT_NAME}\\output\\frames\\s01_c01_first_v1.png",
                      "url": "https://cos-platform-outputs.agnes-ai.cn/images/x/y.png"}]}},
                {"shot_id": "s01_c02", "index": 2, "scene_id": "s1",
                 "status": "done",
                 "video_versions": [
                     {"version_id": "vv_aaaa1111", "status": "done",
                      "local": f"projects\\{PROJECT_NAME}\\output\\videos\\s01_c02.mp4",
                      "url": "https://cos-platform-outputs.agnes-ai.cn/videos/x/y.mp4",
                      "created_at": 1792242322.0}],
                 "final_video": "vv_aaaa1111",
                 "first_frame": {"candidates": []}}]}]}


@pytest.fixture
def sample_project(tmp_path):
    """结构近似真实项目（含 input/skills trace/lease/jsonl 审计）。"""
    root = tmp_path / "work" / PROJECT_NAME
    dirs = ["input", "output/frames", "output/videos", "output/agents/skills",
            "output/agents/collab_runs", "output/agents/sessions",
            "output/sequences/seq_x", "cache/checkpoints"]
    for d in dirs:
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "input/script.yaml").write_text("scenes: []\n", encoding="utf-8")
    (root / "output/project.json").write_text(
        json.dumps(_project_doc(), ensure_ascii=False, indent=1), encoding="utf-8")
    (root / "output/resources.json").write_text(
        json.dumps({"schema_version": 1, "items": []}), encoding="utf-8")
    (root / "output/sequences.json").write_text(json.dumps(
        {"schema_version": 1, "sequences": {"seq_x": {"render_versions": []}}}),
        encoding="utf-8")
    (root / "output/shot_production_bindings.json").write_text(
        json.dumps({"bindings": []}), encoding="utf-8")
    (root / "output/scene_system.json").write_text(
        json.dumps({"schema_version": 1}), encoding="utf-8")
    (root / "output/frames/s01_c01_first_v1.png").write_bytes(b"\x89PNG" + b"f" * 2000)
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"v" * 5000
    (root / "output/videos/s01_c02.mp4").write_bytes(mp4)
    (root / "output/sequences/seq_x/srender_x.mp4").write_bytes(b"SEQ" + b"m" * 3000)
    # skill trace：3 次 load+load_fail（复刻 P55 形态）
    trace = []
    for i in range(3):
        trace.append(json.dumps(
            {"ts": 1792236311.0 + i, "event": "load", "ok": True,
             "agent_id": "script_writer",
             "skill_id": "full-story-relay-calibration", "version": "1.6.0"}))
        trace.append(json.dumps(
            {"ts": 1792236312.0 + i, "event": "load_fail", "ok": False,
             "agent_id": "script_writer", "skill_id": None, "version": None}))
    (root / "output/agents/skills/trace.jsonl").write_text(
        "\n".join(trace) + "\n", encoding="utf-8")
    (root / "output/agents/grants_audit.jsonl").write_text(
        "\n".join([json.dumps({"i": i, "ref": f"projects\\{PROJECT_NAME}\\x"})
                   for i in range(3)]) + "\n", encoding="utf-8")
    (root / "output/agents/master_lease.json").write_text(json.dumps(
        {"holder": "sess_old", "generation": 1,
         "acquired_at": 1.0, "expires_at": 2.0}), encoding="utf-8")
    return root


@pytest.fixture
def opdir(tmp_path):
    d = tmp_path / "op"
    d.mkdir()
    return d


@pytest.fixture
def dest_parent(tmp_path):
    d = tmp_path / "restores"
    d.mkdir()
    return d
