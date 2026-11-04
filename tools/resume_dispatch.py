#!/usr/bin/env python3
"""有界自动续跑派发：A=排水后自派发，B=巡检兜底拉起。

只做 workflow_dispatch（显式恢复入口，复用原 nonce、原生 ID 与累计预算），
绝不 gh run rerun、绝不新开 nonce。优先使用 RESUME_DISPATCH_TOKEN，
也支持具有 Actions write 的 GITHUB_TOKEN：workflow_dispatch 是 GitHub
明确允许触发后续运行的例外，不受普通 push 事件防递归规则限制。

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


def dispatch_token(environ=None):
    env = os.environ if environ is None else environ
    return (env.get('RESUME_DISPATCH_TOKEN') or '').strip() or (env.get('GITHUB_TOKEN') or '').strip()

# 3.2 分钟片从零约 4.5h：330 分钟保险丝下单跳即可完成；上限 3 跳覆盖
# 硬死亡重试与跨天接续等极端情况，超出说明链路有病，必须叫人。
MAX_AUTO_HOPS = 50_000

# 可自动续跑的检查点相位。FAILED+terminal_status=paused 是"到点优雅暂停"
# 的落账形态（pause_and_drain 写入）；FAILED+quota_exhausted 是"每日额度
# 耗尽暂停"（quota_gate 写入，需过日历闸门）。新 UNKNOWN 仅在有效持久
# 风险授权下允许进入恢复对账；其余 FAILED 是真实失败，交人工。
RESUMABLE_PHASES = {"RUNNING", "RESTORED", "PAUSING", "INTERRUPTED", "WAITING_QUOTA"}
RESUMABLE_TERMINAL = {"paused", "quota_exhausted"}
DEAD_PHASES = {"COMPLETE", "AWAITING_HUMAN", "BLOCKED_PREFLIGHT", "STARTING"}


def hop_count(checkpoint_doc):
    return len((checkpoint_doc or {}).get("resume_history") or [])


def auto_resume_allowed(checkpoint_doc, *, now=None):
    """返回 (allowed, reason)。依据权威相位、显式授权与跳数，不猜测。"""
    from tools.quota_gate import gate_status
    doc = checkpoint_doc or {}
    phase = doc.get("phase")
    from src.core.submission_recovery import policy_valid
    pending = [a for a in doc.get('video_attempts') or []
               if a.get('state') in ('SUBMITTING', 'UNKNOWN')]
    authorized_unknown = (phase == 'FAILED' and doc.get('terminal_status') == 'failed'
        and bool(doc.get('full_run_id')) and bool(pending)
        and all(not a.get('video_id') and all(a.get(k) for k in
                    ('attempt_id', 'intent_id', 'immutable_input_digest', 'logical_shot_id')) for a in pending)
        and policy_valid(doc.get('submission_recovery_policy'), doc.get('nonce'), doc.get('request_sha256')))
    if phase in DEAD_PHASES:
        return False, f"phase {phase} is terminal or pre-flight"
    if phase not in RESUMABLE_PHASES:
        resumable_pause = (phase in ("FAILED", "STAGE0_BOUND")
            and doc.get("terminal_status") in RESUMABLE_TERMINAL
            and (phase != "STAGE0_BOUND" or (doc.get("full_run_id") and doc.get("video_attempts"))))
        if not (authorized_unknown or resumable_pause):
            return False, f"phase {phase} not auto-resumable"
    gate_open, gate_reason = gate_status(doc) if now is None else gate_status(doc, now=now)
    if not gate_open:
        return False, gate_reason
    hops = hop_count(doc)
    if hops >= MAX_AUTO_HOPS:
        return False, f"auto-hop cap reached: {hops}/{MAX_AUTO_HOPS}"
    return True, "ok"


def dispatch_next_hop(token, ref, resume_run_id, task_nonce,
                      driver_minutes=None, finalize_only=None, target_seconds=None,
                      media_inflight=None):
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
    if media_inflight is not None:
        if str(media_inflight) not in ("1", "2"):
            return {"dispatched": False, "reason": "invalid frozen video inflight setting"}
        inputs["media_inflight"] = str(media_inflight)

    return post_workflow_dispatch(token, WORKFLOW_FILE, ref, inputs)


def post_workflow_dispatch(token, workflow_file, ref, inputs):
    """Explicit dispatch only; no credential appears in receipts or errors."""
    if not (token or '').strip():
        return {'dispatched': False, 'reason': 'workflow dispatch token not configured'}
    body = json.dumps({"ref": ref, "inputs": inputs}).encode()
    req = urllib.request.Request(
        f'https://api.github.com/repos/{REPO}/actions/workflows/{workflow_file}/dispatches',
        data=body, method="POST",
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
