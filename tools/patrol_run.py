#!/usr/bin/env python3
"""Patrol — scheduled monitor (Phase 3). Reuses e2e_autonomous_run via subprocess.

Loop:
  - scan tasks/pending/*.json (from checkout)
  - for each pending task (nonce+prompt): if B1 already has its state.json
    (test/e2e-<nonce> branch) -> already produced -> SKIP (dedup, no re-production)
  - else -> run e2e production (real Agnes gen -> MP4 -> save B + register B1)
  - no pending (or all done) -> IDLE (no gen, no write, no commit)

Dedup is via B1 state.json existence (no A-repo writes -> no CI noise, no self-trigger).
Patrol workflow triggers on schedule (+ workflow_dispatch) only, NOT on push, so its own
state has no feedback loop into itself.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

OWNER_B1 = "Q-1844"
REPO_B1 = "agent-system-v2-rundata-1"


def _http(method, url, token, **kw):
    import httpx
    with httpx.Client(timeout=30.0) as c:
        return c.request(method, url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }, **kw)


def already_produced(token, nonce):
    """B1 has e2e/<nonce>/state.json on test/e2e-<nonce> branch? -> dedup."""
    r = _http("GET",
              f"https://api.github.com/repos/{OWNER_B1}/{REPO_B1}"
              f"/contents/e2e/{nonce}/state.json?ref=test/e2e-{nonce}",
              token)
    return r.status_code == 200


def run_e2e(nonce, prompt):
    env = dict(os.environ)
    env["TASK_NONCE"] = nonce
    env["TASK_PROMPT"] = prompt
    env["PYTHONPATH"] = "."
    # AGNES_API_KEY + B1_ARCHIVE_TOKEN already in env (from workflow)
    return subprocess.run([sys.executable, "tools/e2e_autonomous_run.py"],
                           env=env, capture_output=True, text=True, timeout=900)


def main():
    api_key = os.environ.get("AGNES_API_KEY")
    b1_token = os.environ.get("B1_ARCHIVE_TOKEN")
    if not api_key or not b1_token:
        print("[patrol] missing AGNES_API_KEY or B1_ARCHIVE_TOKEN env")
        return 1

    pending = sorted(glob.glob("tasks/pending/*.json"))
    print(f"[patrol] {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
          f"pending tasks found: {len(pending)}")

    if not pending:
        print("[patrol] no pending tasks -> IDLE (no gen, no write, no commit). standby.")
        return 0

    produced = 0
    skipped = 0
    failed = 0
    for p in pending:
        try:
            t = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[patrol] skip unreadable {p}: {e}")
            continue
        nonce = t.get("nonce")
        prompt = t.get("prompt")
        if not nonce or not prompt:
            print(f"[patrol] skip {p}: missing nonce/prompt")
            continue

        if already_produced(b1_token, nonce):
            print(f"[patrol] {nonce}: already produced (B1 state.json exists) "
                  f"-> SKIP (no re-production)")
            skipped += 1
            continue

        print(f"[patrol] {nonce}: pending + not produced -> run production...")
        r = run_e2e(nonce, prompt)
        try:
            summ = json.loads(Path("e2e_run/summary.json").read_text(encoding="utf-8"))
        except Exception:
            summ = {"status": "UNKNOWN",
                    "stdout_tail": (r.stdout or "")[-600:],
                    "stderr_tail": (r.stderr or "")[-600:]}
        if summ.get("status") == "PASS":
            produced += 1
            reg = summ.get("save", {}).get("b1_registry", {})
            print(f"[patrol] {nonce}: PRODUCED -> B1 {reg.get('branch')} "
                  f"rev={reg.get('registry_revision')} sha={summ.get('mp4_sha256','')[:16]}")
        else:
            failed += 1
            print(f"[patrol] {nonce}: production FAILED -> "
                  f"{(summ.get('error') or summ.get('stderr_tail','?'))[:300]}")

    print(f"[patrol] done. produced={produced} skipped={skipped} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
