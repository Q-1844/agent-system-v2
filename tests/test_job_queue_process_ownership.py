"""第05轮 Gate B2：JobQueue 跨进程所有权的正式回归测试。

🔴 本文件全部使用 tmp_path 下的**合成账本** + fake handler + **真实子进程**：
- 不指向 BASE_DIR/cache/jobs.json、cache/quota.json 或真实 projects/；
- 不发起任何网络请求（父进程剥离凭据类环境变量后再拉起子进程）；
- 不做任何 Git 操作；不 kill 任何非本测试启动的进程。

覆盖（第05轮执行单 §3 B2 的 7 项）：
1. 两进程同账本且只有 1 job：exactly one handler；
2. 两进程**不同账本**：允许各自执行（不误用全局大锁）；
3. 同一 owner 内两个独立视频任务：并行重叠（AGNES_VIDEO_INFLIGHT=2 仍生效）；
4. 两进程并发提交相同 origin_run_id/stage_key：不产生重复持久 job；既有回执复用；
5. 第二个后端启动冲突：可见拒绝（fail-fast/503），零恢复、零副作用、零 handler；
6. 旧 owner 正常退出 / 被 kill 后：OS 锁自动回收，新 owner 可接管，任务不丢；
7. 账本 JSON 损坏 / 写盘失败 / 路径不可写：保留原文件、阻断执行、报错可见。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).with_name("round05_owner_worker.py")

# 凭据类环境变量不注入假进程（执行单 §1.2）
_SECRET_RE = re.compile(
    r'(API[_-]?KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|\bAUTH\b|_AUTH$|_PAT$|^GH_PAT$)', re.I)


def _child_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not _SECRET_RE.search(k)}
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["WF_STARTUP_RECOVERY_DISABLE"] = "1"
    env["AGNES_VIDEO_INFLIGHT"] = "2"
    env.update(extra)
    return env


def _spawn(args, tmp_path, name, env=None):
    cmd = [sys.executable, str(WORKER)] + [str(a) for a in args]
    out_path = Path(tmp_path) / f"{name}.stdout.txt"
    handle = open(out_path, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT), env=env or _child_env(),
                            stdout=handle, stderr=subprocess.STDOUT)
    proc.stdout_handle = handle
    proc.stdout_path = out_path
    return proc


def _read_json(path):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _wait_file(path, seconds=30.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if Path(path).exists():
            return True
        time.sleep(0.02)
    return Path(path).exists()


def _write_ledger(path: Path, jobs: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")


def _queued_job(job_id: str, project="round05_fake_project", shot_id="s05_c01") -> dict:
    return {"job_id": job_id, "project": project, "kind": "video", "shot_id": shot_id,
            "params": {}, "state": "queued", "progress": 0, "message": "",
            "error": None, "created_at": time.time()}


# ----------------------------------------------------------------- 1
def test_two_processes_same_ledger_exactly_one_handler(tmp_path):
    """同账本双进程：同一 job 只被执行一次，败者被所有权明确拒绝。"""
    ledger = tmp_path / "jobs.json"
    attempts = tmp_path / "attempts"
    _write_ledger(ledger, {"fake_video_A": _queued_job("fake_video_A")})
    ready0 = tmp_path / "p0.ready.json"
    go = tmp_path / "go.signal"

    p0 = _spawn(["--mode", "claim", "--ledger", ledger, "--out", tmp_path / "p0.json",
                 "--attempts-dir", attempts, "--work-ms", "400",
                 "--ready-file", ready0, "--go-file", go], tmp_path, "p0")
    assert _wait_file(ready0, 30), "第一个进程未取得账本所有权"

    p1 = _spawn(["--mode", "claim", "--ledger", ledger, "--out", tmp_path / "p1.json",
                 "--attempts-dir", attempts, "--work-ms", "400"], tmp_path, "p1")
    rc1 = p1.wait(timeout=60)
    r1 = _read_json(tmp_path / "p1.json")
    assert rc1 == 23, f"败者进程应被拒绝（rc=23），实际 rc={rc1}; out={r1}"
    assert r1["error_type"] == "JobQueueOwnershipError"
    assert r1["handler_calls"] == 0

    go.write_text("go", encoding="utf-8")
    assert p0.wait(timeout=60) == 0

    markers = list(attempts.glob("*.json"))
    assert len(markers) == 1, f"同一 job 被执行了 {len(markers)} 次（应为 1）"
    state = json.loads(ledger.read_text(encoding="utf-8"))
    assert state["fake_video_A"]["state"] == "done"


# ----------------------------------------------------------------- 2
def test_two_processes_different_ledgers_both_execute(tmp_path):
    """不同账本不得被同一把锁串行化——两个进程各自完成自己的 job。"""
    ledger_a = tmp_path / "a" / "jobs.json"
    ledger_b = tmp_path / "b" / "jobs.json"
    _write_ledger(ledger_a, {"job_a": _queued_job("job_a")})
    _write_ledger(ledger_b, {"job_b": _queued_job("job_b")})
    attempts = tmp_path / "attempts"

    pa = _spawn(["--mode", "claim", "--ledger", ledger_a, "--out", tmp_path / "pa.json",
                 "--attempts-dir", attempts, "--work-ms", "300"], tmp_path, "pa")
    pb = _spawn(["--mode", "claim", "--ledger", ledger_b, "--out", tmp_path / "pb.json",
                 "--attempts-dir", attempts, "--work-ms", "300"], tmp_path, "pb")
    assert pa.wait(timeout=60) == 0
    assert pb.wait(timeout=60) == 0
    markers = list(attempts.glob("*.json"))
    assert len(markers) == 2, f"不同账本应各自执行一次，实际 {len(markers)} 次"


# ----------------------------------------------------------------- 3
def test_same_owner_two_video_jobs_run_in_parallel(tmp_path):
    """同一 owner 内两路视频并发必须保留（AGNES_VIDEO_INFLIGHT=2 仍生效）。"""
    ledger = tmp_path / "jobs.json"
    _write_ledger(ledger, {"job_a": _queued_job("job_a"), "job_b": _queued_job("job_b")})
    attempts = tmp_path / "attempts"
    p = _spawn(["--mode", "claim", "--ledger", ledger, "--out", tmp_path / "w.json",
                "--attempts-dir", attempts, "--work-ms", "600"], tmp_path, "w")
    assert p.wait(timeout=60) == 0
    res = _read_json(tmp_path / "w.json")
    assert res["video_worker_count"] == 2, f"视频 worker 档位应为 2，实际 {res['video_worker_count']}"
    marks = [json.loads(f.read_text(encoding="utf-8")) for f in attempts.glob("*.json")]
    assert len(marks) == 2, f"两个 job 都应被执行，实际 {len(marks)}"
    overlap = (min(m["finished_at"] for m in marks)
               - max(m["started_at"] for m in marks))
    assert overlap > 0, f"两个独立视频任务未重叠（overlap={overlap}）——同进程并发退化"


# ----------------------------------------------------------------- 4
def test_concurrent_same_origin_submit_creates_single_job(tmp_path):
    """两进程并发提交同一 origin_run_id/stage_key：不得产生重复持久 job。"""
    ledger = tmp_path / "jobs.json"
    _write_ledger(ledger, {})
    args = ["--mode", "submit", "--ledger", ledger, "--out", None,
            "--origin-run-id", "round05_run_x", "--stage-key", "video:s05_c99"]
    p0 = _spawn([str(a) if a is not None else str(tmp_path / "s0.json")
                 for a in args], tmp_path, "s0")
    p1 = _spawn([str(a) if a is not None else str(tmp_path / "s1.json")
                 for a in args], tmp_path, "s1")
    rcs = sorted([p0.wait(timeout=60), p1.wait(timeout=60)])
    assert rcs[0] == 0, f"至少一个进程应成功取得所有权：rcs={rcs}"
    results = [_read_json(tmp_path / "s0.json"), _read_json(tmp_path / "s1.json")]
    created = [r for r in results if r and r.get("created_job")]
    assert len(created) <= 1, f"同一 origin/stage 产生了 {len(created)} 次新建"
    state = json.loads(ledger.read_text(encoding="utf-8"))
    assert len(state) == 1, f"账本出现重复 job：{sorted(state.keys())}"


def test_existing_origin_job_is_reused_not_duplicated(tmp_path):
    """已存在关联回执时复用原 job，不新建（幂等）。"""
    ledger = tmp_path / "jobs.json"
    _write_ledger(ledger, {})
    first = _spawn(["--mode", "submit", "--ledger", ledger, "--out", tmp_path / "s0.json",
                    "--origin-run-id", "round05_run_x", "--stage-key", "video:s05_c99"],
                   tmp_path, "s0")
    assert first.wait(timeout=60) == 0
    r0 = _read_json(tmp_path / "s0.json")
    assert r0["created_job"] is True

    second = _spawn(["--mode", "submit", "--ledger", ledger, "--out", tmp_path / "s1.json",
                     "--origin-run-id", "round05_run_x", "--stage-key", "video:s05_c99"],
                    tmp_path, "s1")
    assert second.wait(timeout=60) == 0
    r1 = _read_json(tmp_path / "s1.json")
    assert r1["created_job"] is False and r1["status"] == "reused"
    assert r1["existing_job_id"] == r0["job_id"]
    state = json.loads(ledger.read_text(encoding="utf-8"))
    assert len(state) == 1, "复用不得产生第二份持久 job"


# ----------------------------------------------------------------- 5
def test_second_backend_startup_is_rejected_with_zero_side_effects(tmp_path):
    """第二个后端进程：可见拒绝（fail-fast），零恢复、零写入、零 handler。"""
    fake_base = tmp_path / "fake_base"
    (fake_base / "cache").mkdir(parents=True, exist_ok=True)
    (fake_base / "projects").mkdir(parents=True, exist_ok=True)
    ledger = fake_base / "cache" / "jobs.json"
    _write_ledger(ledger, {})
    before = ledger.read_bytes()

    holder = _spawn(["--mode", "hold", "--ledger", ledger, "--out", tmp_path / "hold.json",
                     "--release-file", str(tmp_path / "release"),
                     "--hold-seconds", "120"], tmp_path, "hold")
    assert _wait_file(tmp_path / "hold.json", 30)
    assert _read_json(tmp_path / "hold.json")["status"] == "holding"

    api = _spawn(["--mode", "api", "--out", tmp_path / "api.json",
                  "--fake-base", str(fake_base)], tmp_path, "api")
    rc = api.wait(timeout=180)
    res = _read_json(tmp_path / "api.json")
    assert rc == 23, f"第二个后端应被拒绝启动，实际 rc={rc}; out={res}"
    assert res["status"] == "rejected_startup"
    assert res["error_type"] == "JobQueueOwnershipError"
    assert res.get("conflict"), "应记录可见的所有权冲突原因"
    assert ledger.read_bytes() == before, "败者进程不得写入账本"
    assert not list(fake_base.glob("cache/quota.json")), "败者进程不得产生配额写入"

    (tmp_path / "release").write_text("release", encoding="utf-8")
    assert holder.wait(timeout=60) == 0


def test_write_routes_are_503_when_ledger_ownership_conflicted(client):
    """写入类路由在所有权冲突标记下明确 503（fail closed，不排队接单）。"""
    import deps
    deps.job_queue_conflict = {"reason": "round05 synthetic ownership conflict", "at": 0.0}
    try:
        resp = client.post("/api/project/round05_fake_project/production/start", json={})
        assert resp.status_code == 503, f"应返回 503，实际 {resp.status_code}"
        assert ("冲突" in resp.text) or ("ownership" in resp.text.lower())
    finally:
        deps.job_queue_conflict = None


# ----------------------------------------------------------------- 6
def test_owner_normal_exit_then_new_owner_takes_over(tmp_path):
    """旧 owner 正常退出 → 锁自动释放 → 新 owner 可接管（无需人工删锁文件）。"""
    ledger = tmp_path / "jobs.json"
    _write_ledger(ledger, {})
    holder = _spawn(["--mode", "hold", "--ledger", ledger, "--out", tmp_path / "h.json",
                     "--release-file", str(tmp_path / "rel"), "--hold-seconds", "120"],
                    tmp_path, "h")
    assert _wait_file(tmp_path / "h.json", 30)
    assert _read_json(tmp_path / "h.json")["status"] == "holding"

    blocked = _spawn(["--mode", "submit", "--ledger", ledger, "--out", tmp_path / "b.json",
                      "--origin-run-id", "r1", "--stage-key", "k1"], tmp_path, "b")
    assert blocked.wait(timeout=60) == 23, "持有期间的新进程必须被拒绝"
    assert _read_json(tmp_path / "b.json")["error_type"] == "JobQueueOwnershipError"

    (tmp_path / "rel").write_text("rel", encoding="utf-8")
    assert holder.wait(timeout=60) == 0

    new = _spawn(["--mode", "submit", "--ledger", ledger, "--out", tmp_path / "n.json",
                  "--origin-run-id", "r1", "--stage-key", "k1"], tmp_path, "n")
    assert new.wait(timeout=60) == 0, "旧 owner 退出后新 owner 应能取得所有权"
    assert _read_json(tmp_path / "n.json")["created_job"] is True


def test_killed_owner_lock_is_auto_released_and_jobs_survive(tmp_path):
    """旧 owner 被 kill → OS 自动回收锁；新 owner 接管，账本内容不丢。"""
    ledger = tmp_path / "jobs.json"
    _write_ledger(ledger, {"kept": _queued_job("kept")})
    holder = _spawn(["--mode", "hold", "--ledger", ledger, "--out", tmp_path / "h.json",
                     "--hold-seconds", "300"], tmp_path, "h")
    assert _wait_file(tmp_path / "h.json", 30)

    holder.kill()
    holder.wait(timeout=30)

    lock_files = list(tmp_path.glob("*.owner.lock"))
    assert lock_files, "owner lock 文件应仍在磁盘上（解锁靠 OS，不靠删文件）"

    new = _spawn(["--mode", "submit", "--ledger", ledger, "--out", tmp_path / "n.json",
                  "--origin-run-id", "r1", "--stage-key", "k1"], tmp_path, "n")
    assert new.wait(timeout=60) == 0, "旧 owner 被 kill 后新 owner 应能取得所有权"
    state = json.loads(ledger.read_text(encoding="utf-8"))
    assert "kept" in state, f"崩溃后账本中的既有 job 丢失：{sorted(state.keys())}"


# ----------------------------------------------------------------- 7
def test_corrupt_state_is_preserved_and_blocks_execution(tmp_path):
    """账本 JSON 损坏：保留原文件、阻断执行、报错可见。"""
    from src.core.job_queue import JobQueue
    ledger = tmp_path / "jobs.json"
    original = b'{"broken": '
    ledger.write_bytes(original)
    with pytest.raises(RuntimeError, match="recovery required"):
        JobQueue(ledger, {})
    assert ledger.read_bytes() == original, "损坏账本必须原样保留"
    assert not list(tmp_path.glob("*.writing")), "不得生成空账本继续跑"


def test_save_failure_preserves_original_and_reports(tmp_path, monkeypatch):
    """写盘失败（os.replace 失败）：原文件保留、错误可见、不留空账本。"""
    import asyncio

    from src.core.job_queue import JobQueue
    ledger = tmp_path / "jobs.json"

    async def _scenario():
        q = JobQueue(ledger, {})
        q.submit("round05_fake_project", "video", "s05_c01")
        good = ledger.read_bytes()

        def boom(src, dst):
            raise OSError("round05 synthetic os.replace failure")

        monkeypatch.setattr(os, "replace", boom)
        try:
            with pytest.raises(OSError):
                q.submit("round05_fake_project", "video", "s05_c02")
        finally:
            monkeypatch.undo()
        return good, q

    good, q = asyncio.run(_scenario())
    assert ledger.read_bytes() == good, "写盘失败后原账本必须保持不变"
    assert not list(tmp_path.glob("*.tmp")), "写盘失败后不得残留临时文件"
    assert q.jobs, "内存中的 job 不应因单次写失败而丢失"


def test_unwritable_ledger_path_fails_closed_without_creating_ledger(tmp_path):
    """账本父路径不可写：明确报错、不得创建空账本。"""
    from src.core.job_queue import JobQueue, JobQueueOwnershipError
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("i am a regular file", encoding="utf-8")
    ledger = blocker / "jobs.json"
    with pytest.raises(JobQueueOwnershipError):
        JobQueue(ledger, {})
    assert not ledger.exists(), "不得在不可写路径下创建账本"


# ------------------------------------------------- Gate D 恶意错配负例
def test_misdirected_state_path_is_refused_without_writes(tmp_path):
    """D3 负例：把测试状态路径**误指**到另一个活动 owner 正在使用的假账本
    → 必须拒绝，并且对他人账本**零写入**（不得污染、不许抢占）。"""
    from src.core.job_queue import JobQueue, JobQueueOwnershipError

    ledger = tmp_path / "owner_ledger.json"
    _write_ledger(ledger, {"other_owner_job": _queued_job("other_owner_job")})
    before = ledger.read_bytes()

    holder = _spawn(["--mode", "hold", "--ledger", ledger, "--out", tmp_path / "h.json",
                     "--release-file", str(tmp_path / "rel"), "--hold-seconds", "60"],
                    tmp_path, "h")
    assert _wait_file(tmp_path / "h.json", 30)
    assert _read_json(tmp_path / "h.json")["status"] == "holding"

    with pytest.raises(JobQueueOwnershipError):
        JobQueue(ledger, {})
    assert ledger.read_bytes() == before, "被拒绝方不得写入他人账本"

    (tmp_path / "rel").write_text("rel", encoding="utf-8")
    assert holder.wait(timeout=60) == 0
