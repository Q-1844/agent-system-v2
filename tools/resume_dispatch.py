#!/usr/bin/env python3
"""有界自动续跑派发：A=排水后自派发，B=巡检兜底拉起。

只做 workflow_dispatch（显式恢复入口，复用原 nonce、原生 ID 与累计预算），
绝不 gh run rerun、绝不新开 nonce。需要仓库 secret RESUME_DISPATCH_TOKEN
（fine-grained PAT，仅本仓 Actions 读写）——GitHub 平台禁止运行中的任务用
GITHUB_TOKEN 触发新运行，防递归；PAT 派发是刻意的、有界的例外。

自动跳数上限 MAX_AUTO_HOPS：跳满后停止自动派发，检查点置 AWAITING_HUMAN
交还人工，防止失控循环空烧配额。token 值永不进入日志、返回值或检查点。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

REPO = "Q-1844/agent-system-v2"
WORKFLOW_FILE = "video-autoproduction-e2e.yml"
DISPATCH_URL = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"

# 3.2 分钟片从零约 4.5h：330 分钟保险丝下单跳即可完成；上限 3 跳覆盖
# 硬死亡重试与跨天接续等极端情况，超出说明链路有病，必须叫人。
MAX_AUTO_HOPS = 3

# 可自动续跑的检查点相位。FAILED+terminal_status=paused 是"到点优雅暂停"
# 的落账形态（pause_and_drain 写入）；FAILED+quota_exhausted 是"每日额度
# 耗尽暂停"（quota_gate 写入，需过日历闸门）；其余 FAILED 是真实失败，交人工。
RESUMABLE_PHASES = {"RUNNING", "RESTORED", "PAUSING", "INTERRUPTED"}
RESUMABLE_TERMINAL = {"paused", "quota_exhausted"}
DEAD_PHASES = {"COMPLETE", "AWAITING_HUMAN", "BLOCKED_PREFLIGHT", "STARTING"}


def hop_count(checkpoint_doc):
    return len((checkpoint_doc or {}).get("resume_history") or [])


def auto_resume_allowed(checkpoint_doc):
    """返回 (allowed, reason)。只依据检查点权威相位与跳数，不猜测。"""
    from tools.quota_gate import gate_status
    doc = checkpoint_doc or {}
    phase = doc.get("phase")
    if phase in DEAD_PHASES:
        return False, f"phase {phase} is terminal or pre-flight"
    if phase not in RESUMABLE_PHASES:
        if phase == "FAILED" and doc.get("terminal_status") in RESUMABLE_TERMINAL:
            pass  # 优雅暂停/额度暂停落账，可恢复（后者需过日历闸门）
        else:
            return False, f"phase {phase} not auto-resumable"
    gate_open, gate_reason = gate_status(doc)
    if not gate_open:
        return False, gate_reason
    hops = hop_count(doc)
    if hops >= MAX_AUTO_HOPS:
        return False, f"auto-hop cap reached: {hops}/{MAX_AUTO_HOPS}"
    return True, "ok"


def dispatch_next_hop(token, ref, resume_run_id, task_nonce,
                      driver_minutes=None, finalize_only=None, target_seconds=None):
    """派发下一跳。成功返回 dispatched=True（GitHub 对 dispatch 回 204）。

    失败返回错误摘要——派发失败不抛异常：调用方仍按原流程收尾留证，
    B 巡检稍后会兜底重试。返回值不含 token。target_seconds 透传片长档位
    （检查点的 film_target_seconds）；档位错误会被 digest 校验拒绝，安全。
    """
    token = (token or "").strip()
    ref, resume_run_id, task_nonce = str(ref or "").strip(), str(resume_run_id or "").strip(), str(task_nonce or "").strip()
    if not token:
        return {"dispatched": False, "reason": "RESUME_DISPATCH_TOKEN not configured"}
    if not ref or not resume_run_id or not task_nonce:
        return {"dispatched": False, "reason": "missing ref/resume_run_id/task_nonce"}

    inputs = {"acceptance_scope": "full-project-3min",
              "resume_run_id": resume_run_id,
              "task_nonce": task_nonce}
    if driver_minutes:
        inputs["driver_timeout_minutes"] = str(driver_minutes)
    if finalize_only is not None:
        inputs["finalize_only"] = "true" if finalize_only else "false"
    if target_seconds:
        inputs["target_duration_seconds"] = str(target_seconds)

    body = json.dumps({"ref": ref, "inputs": inputs}).encode()
    req = urllib.request.Request(
        DISPATCH_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "asv2-bounded-auto-resume"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return {"dispatched": resp.status == 204, "status": resp.status, "inputs": inputs}
    except urllib.error.HTTPError as error:
        detail = error.read(400).decode("utf-8", "replace").replace(token, "<REDACTED>")
        return {"dispatched": False, "status": error.code, "error": detail}
    except (urllib.error.URLError, OSError, ValueError) as error:
        return {"dispatched": False, "status": None, "error": str(error)}


def fetch_run(dispatch_token, run_id):
    """查询 A 仓运行。返回运行 JSON 或 None（查询失败按未知处理，不派发）。"""
    run_id = str(run_id or "").strip()
    if not run_id.isdigit() or not (dispatch_token or "").strip():
        return None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}",
        headers={"Authorization": f"Bearer {dispatch_token.strip()}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "asv2-bounded-auto-resume"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def run_liveness(dispatch_token, run_id):
    run = fetch_run(dispatch_token, run_id)
    return (run or {}).get("status")


def default_branch(dispatch_token):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}",
        headers={"Authorization": f"Bearer {dispatch_token.strip()}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "asv2-bounded-auto-resume"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")).get("default_branch")
    except (urllib.error.URLError, OSError, ValueError):
        return None
