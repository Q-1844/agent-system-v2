"""第05轮 Gate C 夹具：**真实提交链路**的故障注入子进程。

🔴 真实 JobQueue + 真实 routers.shots._handler_video + FakeAgnes（假 provider）：
- 在 provider 边界前后按指定注入点**终止进程**（os._exit）——真实模拟崩溃/被杀；
- 恢复阶段调用**生产真实恢复钩子** services.lifecycle.recover_pending_videos
  （显式传入隔离队列/客户端/项目根），不是自造恢复逻辑；
- 全部使用临时项目目录 + 临时账本：不访问任何外部服务、不读生产树、不做 Git 操作。

注入点：
- crash_before_claim : 入队后、worker 领取前退出 → job 仍 queued
- crash_after_claim  : worker 已领取（handler_started 已落盘）、handler 未开始 → 确知未提交
- crash_before_create: 提交意图已落盘、provider 调用尚未发出 → 结果不可知
- crash_in_create    : provider 请求已发出（调用已记录）、回执未落盘 → 结果未知
- crash_in_download  : provider 已 ACCEPTED（回执已落盘）、项目版本尚未绑定
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT = "_test_p55_recovery"
SHOT_ID = "s01_c01"


def _bootstrap() -> None:
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".writing")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class CrashableAgnes:
    """假 provider：在指定边界**终止进程**，并把每次调用写入共享调用日志。"""

    def __init__(self, args):
        from tests.test_p55_recovery import FakeAgnes
        self._base = FakeAgnes()
        self.args = args
        self.log_path = Path(args.call_log)

    @property
    def calls(self):
        return self._base.calls

    @property
    def quota(self):
        """真实 handler 会读 client.quota（预检时长）——必须透传。"""
        return self._base.quota

    def __getattr__(self, name):
        # 其余属性透传给 FakeAgnes（避免遗漏真实 handler 用到的接口）
        if name.startswith("_"):
            raise AttributeError(name)
        base = self.__dict__.get("_base")
        if base is None:
            raise AttributeError(name)
        return getattr(base, name)

    def _log(self, kind: str, extra=None) -> None:
        entries = []
        if self.log_path.exists():
            try:
                entries = json.loads(self.log_path.read_text(encoding="utf-8"))
            except Exception:
                entries = []
        entries.append({"kind": kind, "pid": os.getpid(), "at": time.time(),
                        **(extra or {})})
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                                 encoding="utf-8")

    async def create_video(self, prompt, **kw):
        if self.args.injection == "crash_before_create":
            os._exit(9)          # 请求尚未发出
        res = await self._base.create_video(prompt, **kw)
        self._log("create_video", {"video_id": res.get("video_id")})
        if self.args.injection == "crash_in_create":
            os._exit(9)          # 请求已发出，回执未落盘
        return res

    async def download(self, url, dest):
        if self.args.injection == "crash_in_download":
            os._exit(9)          # 回执已落盘，版本未绑定
        res = await self._base.download(url, dest)
        self._log("download", {"url": url})
        return res

    async def poll_video(self, video_id, **kw):
        self._log("poll", {"video_id": video_id})
        if self.args.hang_poll:
            await asyncio.sleep(float(self.args.hang_poll))
        return await self._base.poll_video(video_id, **kw)


def _install_env(args, project_dir: Path, create_project: bool):
    import deps
    from tests.test_p55_recovery import FakeRehost, _FakePM, _save_proj, _mark_ff_ready

    deps.agnes_client = CrashableAgnes(args)
    deps._rehost_host = FakeRehost()
    deps.project_manager = _FakePM(project_dir)
    if create_project:
        # 仅首次（submit）建项目；recover 必须看到**崩溃时留下的原状态**，
        # 不得重建项目把 generating/video_id 等现场证据抹掉。
        _save_proj(project_dir)
        _mark_ff_ready(project_dir, SHOT_ID)


async def _mode_submit(args) -> int:
    _bootstrap()
    from src.core.job_queue import JobQueue
    from routers.shots import _handler_video

    project_dir = Path(args.project_dir)
    _install_env(args, project_dir, create_project=True)
    out = {"mode": "submit", "pid": os.getpid(), "injection": args.injection}

    async def handler(job, emit):
        if args.injection == "crash_after_claim":
            # worker 已领取（handler_started 已落盘），但 handler 尚未开始：
            # 此刻**确知未提交**（无提交意图、无 provider 回执）。
            os._exit(9)
        await _handler_video(job, emit)

    q = JobQueue(Path(args.ledger), {"video": handler}, video_worker_count=2)
    try:
        q.submit(PROJECT, "video", SHOT_ID,
                 origin_run_id=args.origin_run_id,
                 autopilot_stage_key=args.stage_key)
        out["submitted"] = True
        if args.injection == "crash_before_claim":
            # 只入队、不启动 worker → job 保持 queued（领取前崩溃）
            await asyncio.sleep(0.2)
            out.update({"status": "queued_and_stopped"})
            _write_json(Path(args.out), out)
            return 0
        await q.start()
        deadline = time.monotonic() + args.wait_s
        while time.monotonic() < deadline:
            if all(j.state in ("done", "failed", "cancelled") for j in q.jobs.values()):
                break
            await asyncio.sleep(0.05)
        out.update({"status": "finished",
                    "states": {k: v.state for k, v in q.jobs.items()}})
    finally:
        q.close()
    _write_json(Path(args.out), out)
    return 0


async def _mode_recover(args) -> int:
    _bootstrap()
    import deps
    from services import lifecycle
    from src.core.job_queue import JobQueue
    from routers.shots import _handler_video

    project_dir = Path(args.project_dir)
    _install_env(args, project_dir, create_project=False)
    out = {"mode": "recover", "pid": os.getpid()}

    q = JobQueue(Path(args.ledger), {"video": _handler_video}, video_worker_count=2)
    if args.watchdog_timeout:
        q.WATCHDOG_TIMEOUT = float(args.watchdog_timeout)
        q.WATCHDOG_INTERVAL = float(args.watchdog_interval or 0.2)
    try:
        # 🔴 生产**真实**恢复钩子：自己决定 poll/reconcile，不重 POST。
        rec = await lifecycle.recover_pending_videos(
            queue=q, client=deps.agnes_client,
            projects_root=project_dir.parent, handlers={"video": _handler_video})
        out["recovery_hook"] = "services.lifecycle.recover_pending_videos"
        await q.start()
        deadline = time.monotonic() + args.wait_s
        while time.monotonic() < deadline:
            if all(j.state in ("done", "failed", "cancelled") for j in q.jobs.values()):
                break
            await asyncio.sleep(0.05)
        out.update({"status": "recovered",
                    "states": {k: v.state for k, v in q.jobs.items()},
                    "job_count": len(q.jobs)})
    except Exception as exc:
        out.update({"status": "error", "error_type": type(exc).__name__,
                    "error": str(exc)[:600]})
    finally:
        # 明确收敛：取消并收割所有 worker / inner task。
        # C8 场景故意让 poll 悬挂（模拟宿主休眠），若不收割，解释器退出阶段会挂在
        # asyncio.run 的 shutdown 上（与被测产品无关，是夹具自身的收敛问题）。
        try:
            pending = list(q._tasks) + list(q._running_tasks.values())
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        except Exception:
            pass
        q.close()
    _write_json(Path(args.out), out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", required=True, choices=("submit", "recover"))
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--call-log", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--injection", default="none")
    ap.add_argument("--origin-run-id", default="round05_run_c")
    ap.add_argument("--stage-key", default="video:s01_c01")
    ap.add_argument("--wait-s", type=float, default=25.0)
    ap.add_argument("--hang-poll", type=float, default=0.0)
    ap.add_argument("--watchdog-timeout", type=float, default=0.0)
    ap.add_argument("--watchdog-interval", type=float, default=0.2)
    args = ap.parse_args()
    if args.mode == "submit":
        rc = asyncio.run(_mode_submit(args))
    else:
        rc = asyncio.run(_mode_recover(args))
    # 证据已落盘（write_text 已关闭文件）→ 用 os._exit 结束：
    # 既避免 asyncio 关闭阶段的悬挂，也正是「进程死亡由 OS 回收 owner 锁」的真实语义。
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(rc)


if __name__ == "__main__":
    main()
