#!/usr/bin/env python3
"""B 兜底：巡检发现"死链长片"自动拉起续跑（workflow_dispatch 显式入口）。

死链 = B1 检查点处于可续跑相位，但其 github_run_id 对应的 A 仓运行已完结
超过 DEADLINK_GRACE_MINUTES。这覆盖 A 机制自身派发失败、runner 硬死亡
（VM 崩溃/平台强杀，A 根本没机会说话）与状态卡死三类场景。

纪律：只读 B1、只派发、不生成任何媒体；token 不入日志；单个 nonce 的
任何异常都不拖垮整轮巡检；跳数超限置 AWAITING_HUMAN 交还人工。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

from tools.resume_dispatch import (
    MAX_AUTO_HOPS, auto_resume_allowed, default_branch, dispatch_next_hop,
    fetch_run, hop_count)

B1_REPO = "Q-1844/agent-system-v2-rundata-1"
CHECKPOINT_FILE = "full-project-production.json"
# A 机制在运行结束后 ~2 分钟内完成派发；宽限给足余量避免双派发。
DEADLINK_GRACE_MINUTES = 15
# 派发后子运行最长约 3 分钟才开始写检查点；期间跳过同一 nonce。
RECENT_DISPATCH_SKIP_MINUTES = 30

ACTIVE_RUN_STATES = {"queued", "in_progress", "waiting", "requested", "pending"}


def utc_now_ts():
    return time.time()


def parse_iso(value):
    if not isinstance(value, str) or not value:
        return None
    from datetime import datetime, timezone
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def b1_get(path, token):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{B1_REPO}/{path}",
        headers={"Authorization": f"Bearer {token.strip()}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "asv2-bounded-auto-resume"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def list_e2e_nonces(token):
    data = b1_get("branches?per_page=100", token)
    if not isinstance(data, list):
        return []
    return [b["name"][len("test/e2e-"):] for b in data
            if isinstance(b, dict) and str(b.get("name", "")).startswith("test/e2e-")]


def read_checkpoint(nonce, token):
    path = (f"contents/e2e/{nonce}/{CHECKPOINT_FILE}"
            f"?ref=test/e2e-{nonce}")
    data = b1_get(path, token)
    if not isinstance(data, dict) or "content" not in data:
        return None
    try:
        return json.loads(base64.b64decode(data["content"]).decode("utf-8", "replace"))
    except (ValueError, OSError):
        return None


def mark_awaiting_human(nonce, doc, token, reason):
    """跳数超限时写回 AWAITING_HUMAN（走 CAS 检查点纪律，冲突即弃）。"""
    from tools.video_task_state import ProductionCheckpoint, task_index
    digest = doc.get("request_sha256")
    if not digest:
        return {"written": False, "reason": "no request_sha256"}
    try:
        index = task_index(token, nonce, CHECKPOINT_FILE)
        checkpoint = ProductionCheckpoint(index, nonce, digest)
        checkpoint.save(phase="AWAITING_HUMAN", error=reason)
        return {"written": True}
    except Exception as error:  # noqa: BLE001 单 nonce 失败不拖垮整轮
        return {"written": False, "reason": str(error)[:200]}


def sweep_once(b1_token, dispatch_token):
    results = []
    if not (dispatch_token or "").strip():
        print(json.dumps({"sweep": "skipped",
                          "reason": "RESUME_DISPATCH_TOKEN not configured"},
                         ensure_ascii=False))
        return results
    ref = default_branch(dispatch_token)
    if not ref:
        print(json.dumps({"sweep": "skipped", "reason": "cannot resolve default branch"},
                         ensure_ascii=False))
        return results
    for nonce in list_e2e_nonces(b1_token):
        item = {"nonce": nonce, "action": "skip", "reason": "unhandled"}
        try:
            item = sweep_nonce(nonce, b1_token, dispatch_token, ref) or item
        except Exception as error:  # noqa: BLE001
            item = {"nonce": nonce, "action": "error", "reason": str(error)[:200]}
        results.append(item)
    print(json.dumps({"sweep": "done", "ref": ref, "nonces": len(results),
                      "results": results}, ensure_ascii=False, indent=1))
    return results


def sweep_nonce(nonce, b1_token, dispatch_token, ref, *, _read=None):
    payload = _read if _read is not None else read_checkpoint(nonce, b1_token)
    if not isinstance(payload, dict) or not payload:
        return {"nonce": nonce, "action": "skip", "reason": "no checkpoint readable"}
    # 兼容两种形态：GitHubContentsIndex 风格 {"doc": ...} 与直接文件本体。
    doc = payload["doc"] if isinstance(payload.get("doc"), dict) else payload
    allowed, reason = auto_resume_allowed(doc)
    if not allowed:
        if "cap" in reason and doc.get("phase") != "AWAITING_HUMAN":
            return {"nonce": nonce, "action": "awaiting_human",
                    "mark": mark_awaiting_human(nonce, doc, b1_token, reason)}
        return {"nonce": nonce, "action": "skip", "reason": reason}
    run_id = str(doc.get("github_run_id") or "")
    if not run_id.isdigit():
        return {"nonce": nonce, "action": "skip",
                "reason": "no authoritative github_run_id"}
    prior = doc.get("auto_resume") or {}
    dispatched_at = prior.get("at") if isinstance(prior, dict) else None
    if (isinstance(dispatched_at, (int, float))
            and utc_now_ts() - dispatched_at < RECENT_DISPATCH_SKIP_MINUTES * 60):
        return {"nonce": nonce, "action": "skip",
                "reason": "dispatched recently, waiting for child run"}
    run = fetch_run(dispatch_token, run_id)
    if not run:
        return {"nonce": nonce, "action": "skip",
                "reason": "run query failed, treat as unknown"}
    if run.get("status") in ACTIVE_RUN_STATES:
        return {"nonce": nonce, "action": "skip", "reason": "run alive"}
    completed = parse_iso(run.get("completed_at") or run.get("updated_at"))
    if completed is None or utc_now_ts() - completed < DEADLINK_GRACE_MINUTES * 60:
        return {"nonce": nonce, "action": "skip",
                "reason": "run recently finished (A may be dispatching)"}
    result = dispatch_next_hop(dispatch_token, ref=ref,
                               resume_run_id=run_id, task_nonce=nonce,
                               target_seconds=doc.get("film_target_seconds"),
                               media_inflight=doc.get("producer_video_inflight"))
    result["at"] = utc_now_ts()
    if result.get("dispatched"):
        try:  # 记录派发时间，防止下一轮巡检在子运行落账前重复派发
            from tools.video_task_state import ProductionCheckpoint, task_index
            index = task_index(b1_token, nonce, CHECKPOINT_FILE)
            ProductionCheckpoint(index, nonce, doc["request_sha256"]).save(
                auto_resume=result)
        except Exception:  # noqa: BLE001 写失败不影响派发结果
            pass
    return {"nonce": nonce,
            "action": "dispatch" if result.get("dispatched") else "dispatch_failed",
            "result": {k: v for k, v in result.items() if k != "at"}}


def main():
    b1 = os.environ.get("B1_ARCHIVE_TOKEN", "")
    dispatch = os.environ.get("RESUME_DISPATCH_TOKEN", "")
    if not b1.strip():
        print(json.dumps({"sweep": "skipped", "reason": "B1_ARCHIVE_TOKEN not configured"},
                         ensure_ascii=False))
        return 0
    sweep_once(b1, dispatch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
