"""第05轮 Gate C：真实提交链路的故障注入矩阵（C1–C8）。

🔴 真实 JobQueue + 真实 routers.shots._handler_video + FakeAgnes（假 provider）：
- 在 provider 边界前后**终止子进程**（os._exit）——真实模拟崩溃/被杀；
- 恢复阶段调用**生产真实恢复钩子** services.lifecycle.recover_pending_videos
  （不是自造恢复逻辑），由它依据**持久化的提交事实**决定 poll/reconcile；
- 全部使用 tmp_path 临时项目 + 临时账本：不访问外部服务、不读生产树、不做 Git 操作。

矩阵机读输出：环境变量 ROUND05_FAULT_MATRIX 指向的 JSON 文件（逐场景追加）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).with_name("round05_fault_worker.py")
PROJECT = "_test_p55_recovery"
SHOT_ID = "s01_c01"

_SECRET_RE = re.compile(
    r'(API[_-]?KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|\bAUTH\b|_AUTH$|_PAT$|^GH_PAT$)', re.I)
_ROWS = []


def _child_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not _SECRET_RE.search(k)}
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["WF_STARTUP_RECOVERY_DISABLE"] = "1"
    env["AGNES_VIDEO_INFLIGHT"] = "2"
    return env


def _spawn(args, tmp_path, name):
    cmd = [sys.executable, str(WORKER)] + [str(a) for a in args]
    out_path = Path(tmp_path) / f"{name}.stdout.txt"
    handle = open(out_path, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT), env=_child_env(),
                            stdout=handle, stderr=subprocess.STDOUT)
    proc.stdout_handle = handle
    return proc


def _read_json(path):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _sha256(path) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _calls(path):
    p = Path(path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _count(path, kind) -> int:
    return len([e for e in _calls(path) if e.get("kind") == kind])


def _ledger_state(path):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"__unparsable__": True}


def _shot_state(project_dir):
    from src.core.store import ProjectStore
    shot = ProjectStore(Path(project_dir)).load().get_shot(SHOT_ID)
    return {
        "video_status": shot.video.status,
        "video_id": shot.video.video_id,
        "final_video": shot.final_video,
        "video_versions": [{"version_id": v.version_id, "video_id": v.video_id}
                           for v in shot.video_versions],
    }


def _dump_row(row):
    _ROWS.append(row)
    out = os.environ.get("ROUND05_FAULT_MATRIX")
    if out:
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(_ROWS, ensure_ascii=False, indent=2), encoding="utf-8")


def _case(tmp_path, name, injection, *, recover_times=1, concurrent_recover=False,
          mutate_shot=False, hang_poll=0.0, watchdog_timeout=0.0, recover_wait_s=25.0):
    """跑一个注入场景：崩溃 → （可选改动来源镜头）→ 恢复 ×N。返回机读行。"""
    root = tmp_path / name
    projects_root = root / "projects"
    project_dir = projects_root / PROJECT
    project_dir.mkdir(parents=True, exist_ok=True)
    ledger = root / "cache" / "jobs.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("{}", encoding="utf-8")
    call_log = root / "provider_calls.json"

    submit = _spawn(["--mode", "submit", "--project-dir", project_dir, "--ledger", ledger,
                     "--call-log", call_log, "--out", root / "submit.json",
                     "--injection", injection], tmp_path, f"{name}_submit")
    rc_submit = submit.wait(timeout=180)
    pre_ledger = _ledger_state(ledger)
    pre_sha = _sha256(ledger)
    creates_before = _count(call_log, "create_video")

    if mutate_shot:
        # C6：来源镜头在崩溃后被修改（下游必须重校验当前上游版本）
        from src.core.store import ProjectStore
        ProjectStore(project_dir).update_shot(
            SHOT_ID, lambda s: setattr(s, "final_video", "vv_round05_other"))

    def _recover_args(i):
        args = ["--mode", "recover", "--project-dir", project_dir, "--ledger", ledger,
                "--call-log", call_log, "--out", root / f"rec{i}.json",
                "--wait-s", recover_wait_s]
        if hang_poll:
            args += ["--hang-poll", hang_poll]
        if watchdog_timeout:
            args += ["--watchdog-timeout", watchdog_timeout]
        return args

    recovers = []
    if concurrent_recover:
        procs = [_spawn(_recover_args(i), tmp_path, f"{name}_rec{i}") for i in range(2)]
        for i, p in enumerate(procs):
            rc = p.wait(timeout=240)
            recovers.append({"rc": rc, "result": _read_json(root / f"rec{i}.json")})
    else:
        for i in range(recover_times):
            p = _spawn(_recover_args(i), tmp_path, f"{name}_rec{i}")
            rc = p.wait(timeout=240)
            recovers.append({"rc": rc, "result": _read_json(root / f"rec{i}.json")})

    post_ledger = _ledger_state(ledger)
    row = {
        "id": name,
        "injection": injection,
        "crash_worker_rc": rc_submit,
        "recover_times": recover_times,
        "concurrent_recover": concurrent_recover,
        "ledger_path": str(ledger),
        "ledger_sha256_before": pre_sha,
        "ledger_sha256_after": _sha256(ledger),
        "provider_calls_log": str(call_log),
        "create_video_before": creates_before,
        "create_video_after": _count(call_log, "create_video"),
        "poll_video_after": _count(call_log, "poll"),
        "download_after": _count(call_log, "download"),
        "pre_state": {jid: {"state": j.get("state"),
                            "has_intent": bool(j.get("params", {}).get("submission_intent_id")),
                            "provider_task_id": j.get("params", {}).get("provider_task_id"),
                            "provider_submit_state": j.get("params", {}).get("provider_submit_state")}
                      for jid, j in pre_ledger.items() if isinstance(j, dict)},
        "post_state": {jid: {"state": j.get("state"),
                             "has_intent": bool(j.get("params", {}).get("submission_intent_id")),
                             "provider_task_id": j.get("params", {}).get("provider_task_id"),
                             "provider_submit_state": j.get("params", {}).get("provider_submit_state"),
                             "watchdog_extensions": j.get("params", {}).get("watchdog_extensions"),
                             "unattached_artifact": bool(j.get("params", {}).get("unattached_artifact"))}
                       for jid, j in post_ledger.items() if isinstance(j, dict)},
        "job_count_after": len([v for v in post_ledger.values() if isinstance(v, dict)]),
        "shot_after": _shot_state(project_dir),
        "recover_results": [{"rc": r["rc"],
                             "status": (r["result"] or {}).get("status"),
                             "states": (r["result"] or {}).get("states"),
                             "error": (r["result"] or {}).get("error")}
                            for r in recovers],
    }
    return row


# ----------------------------------------------------------------- C1
def test_c1_queued_crash_two_owners_execute_exactly_once(tmp_path):
    """C1 领取前崩溃（job 仍 queued）：两个 owner 竞争 → 只执行一次。

    注：第二个 owner 的**确定性拒绝**由 Gate B2 的 ready/go 屏障用例证明
    （tests/test_job_queue_process_ownership.py）；此处关注的是
    「两个 owner 并发时同一 job 只被执行/提交一次」。"""
    row = _case(tmp_path, "C1", "crash_before_claim", concurrent_recover=True)
    row["expectation"] = "同账本双 owner 并发：真实提交恰好 1 次，无重复执行"
    assert row["create_video_after"] == 1, f"提交次数应为 1，实际 {row['create_video_after']}"
    assert row["job_count_after"] == 1
    assert len(row["shot_after"]["video_versions"]) == 1
    assert row["shot_after"]["video_status"] == "done"
    row["verdict"] = "PASS"
    _dump_row(row)


# ----------------------------------------------------------------- C2
def test_c2_claimed_but_no_intent_allows_exactly_one_submission(tmp_path):
    """C2 已领、尚未形成提交意图：**确知未提交** → 允许且仅允许一次提交。"""
    row = _case(tmp_path, "C2", "crash_after_claim")
    row["expectation"] = "确知未提交 → 新 owner 重新排队并提交恰好 1 次（不重复、不丢失）"
    assert row["create_video_after"] == 1, f"提交次数应为 1，实际 {row['create_video_after']}"
    assert row["shot_after"]["video_status"] == "done"
    assert len(row["shot_after"]["video_versions"]) == 1
    row["verdict"] = "PASS"
    _dump_row(row)


# ----------------------------------------------------------------- C3a
def test_c3a_intent_persisted_request_not_sent_no_autopost(tmp_path):
    """C3a 提交意图已落盘、请求未发出：状态不可知 → 禁止自动重发。"""
    row = _case(tmp_path, "C3a", "crash_before_create")
    row["expectation"] = "意图已落盘（结果不可知）→ 不自动重发；create 次数保持 0"
    assert row["create_video_before"] == 0
    assert row["create_video_after"] == 0, f"禁止自动重发，实际 create={row['create_video_after']}"
    assert row["shot_after"]["video_status"] == "generating"
    assert not row["shot_after"]["video_id"]
    row["verdict"] = "PASS"
    _dump_row(row)


# ----------------------------------------------------------------- C3b
def test_c3b_request_sent_receipt_unknown_no_autopost(tmp_path):
    """C3b 请求已发出、回执未落盘（结果未知）→ 禁止自动重发，只能对账。"""
    row = _case(tmp_path, "C3b", "crash_in_create")
    row["expectation"] = "请求已发出但回执未知 → 不自动重发；create 次数保持 1"
    assert row["create_video_before"] == 1
    assert row["create_video_after"] == 1, f"禁止自动重发，实际 create={row['create_video_after']}"
    assert row["shot_after"]["video_status"] == "generating"
    assert not row["shot_after"]["video_id"]
    row["verdict"] = "PASS"
    _dump_row(row)


# ----------------------------------------------------------------- C4
def test_c4_known_provider_task_id_is_polled_not_recreated(tmp_path):
    """C4 provider 已受理、task id 已落盘 → 新 owner 只 poll/取回，不再 create。"""
    row = _case(tmp_path, "C4", "crash_in_download")
    row["expectation"] = "已知 provider task id → 复用原 task 只 poll；create 保持 1"
    assert row["create_video_before"] == 1
    assert row["create_video_after"] == 1, f"不得新建视频 create，实际 {row['create_video_after']}"
    assert row["poll_video_after"] >= 1, "应发生对账轮询"
    assert row["shot_after"]["video_id"], "原 provider task id 应被保留"
    assert len(row["shot_after"]["video_versions"]) == 1
    row["verdict"] = "PASS"
    _dump_row(row)


# ----------------------------------------------------------------- C5
def test_c5_accepted_not_bound_is_bound_once_not_duplicated(tmp_path):
    """C5 ACCEPTED 但版本未绑定 → 绑定/恢复已接受媒体；多次恢复也不产生第二份。"""
    row = _case(tmp_path, "C5", "crash_in_download", recover_times=2)
    row["expectation"] = "已接受媒体被绑定 1 次；重复恢复不生成第二份；create 保持 1"
    assert row["create_video_after"] == 1, f"不得新建视频 create，实际 {row['create_video_after']}"
    assert len(row["shot_after"]["video_versions"]) == 1, "同一 provider 任务只落一个版本"
    assert row["job_count_after"] == 1
    row["verdict"] = "PASS"
    _dump_row(row)


# ----------------------------------------------------------------- C6
def test_c6_upstream_changed_stale_artifact_not_attached(tmp_path):
    """C6 来源镜头修改后中断 → 下游重校验当前上游版本，旧尾帧/旧结果不得抢跑。"""
    row = _case(tmp_path, "C6", "crash_in_download", mutate_shot=True)
    row["expectation"] = "上游已变更 → 过期结果保留但不挂接；不得新建视频"
    assert row["create_video_after"] == 1, f"不得新建视频 create，实际 {row['create_video_after']}"
    assert row["shot_after"]["video_versions"] == [], "过期结果不得挂接为当前版本"
    assert row["shot_after"]["final_video"] == "vv_round05_other", "当前选版不得被覆盖"
    row["verdict"] = "PASS"
    _dump_row(row)


# ----------------------------------------------------------------- C7
def test_c7_repeated_recovery_is_idempotent(tmp_path):
    """C7 同一 Run 多次恢复/多次点击 → 幂等：不重复持久 job、不重复供应商提交。"""
    row = _case(tmp_path, "C7", "crash_in_create", recover_times=3)
    row["expectation"] = "多次恢复幂等：create 保持 1，持久 job 保持 1"
    assert row["create_video_after"] == 1, f"重复提交，实际 {row['create_video_after']}"
    assert row["job_count_after"] == 1, "重复持久 job"
    row["verdict"] = "PASS"
    _dump_row(row)


# ----------------------------------------------------------------- C8
def test_c8_watchdog_with_provider_task_id_does_not_regenerate(tmp_path):
    """C8 宿主休眠/看门狗超时：已知 provider task id → 不把在途当失败、不重新生成。"""
    row = _case(tmp_path, "C8", "crash_in_download", hang_poll=20.0,
                watchdog_timeout=1.0, recover_wait_s=8.0)
    row["expectation"] = "看门狗超时 + 已知 provider task id → job 保持 running，create 保持 1"
    assert row["create_video_after"] == 1, f"不得重新生成，实际 {row['create_video_after']}"
    states = [s.get("state") for s in row["post_state"].values()]
    assert "running" in states, f"在途任务不应被判失败，实际 {states}"
    exts = [s.get("watchdog_extensions") for s in row["post_state"].values()]
    assert any((e or 0) >= 1 for e in exts), f"应留下观察窗延长留痕，实际 {exts}"
    row["verdict"] = "PASS"
    _dump_row(row)
