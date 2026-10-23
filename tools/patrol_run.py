#!/usr/bin/env python3
"""Patrol the existing file queue; only verified task receipts count as done.

The subprocess owns durable submission/recovery checkpoints. Read errors and
ambiguous outcomes must never authorize another paid generation.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from tools.video_task_state import (ProductionCheckpoint, request_digest,
                                   task_index, validate_task, verified_record)


def already_produced(token, nonce, prompt=None):
    current = task_index(token, nonce).read()
    done = verified_record(None if current is None else current["doc"], nonce) is not None
    if done and prompt is not None:
        ProductionCheckpoint(task_index(token, nonce, "production.json"), nonce,
                             request_digest(prompt, "agnes-video-2.5-flash"))
    return done


def run_e2e(nonce, prompt):
    env = dict(os.environ, TASK_NONCE=nonce, TASK_PROMPT=prompt, PYTHONPATH=".")
    return subprocess.run([sys.executable, "tools/e2e_autonomous_run.py"],
                          env=env, capture_output=True, text=True, timeout=900)


def main():
    b1_token = os.environ.get("B1_ARCHIVE_TOKEN")
    pending = sorted(glob.glob("tasks/pending/*.json"))
    result = {"event": os.environ.get("GITHUB_EVENT_NAME", "local"),
              "run_id": os.environ.get("GITHUB_RUN_ID"),
              "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "produced": 0, "skipped": 0, "failed": 0, "tasks": []}
    print(f"[patrol] event={result['event']} pending tasks found: {len(pending)}")
    # Check the whole queue before the first paid action. Conflicting copies of
    # the same nonce must not let whichever filename sorts first choose a prompt.
    prompts, conflicts = {}, set()
    for path in pending:
        try:
            nonce, prompt = validate_task(json.loads(Path(path).read_text(encoding="utf-8")))
            if nonce in prompts and prompts[nonce] != prompt:
                conflicts.add(nonce)
            prompts[nonce] = prompt
        except (ValueError, OSError):
            pass  # Report each malformed file in the execution pass below.
    seen = {}
    for path in pending:
        item = {"file": path}
        try:
            nonce, prompt = validate_task(json.loads(Path(path).read_text(encoding="utf-8")))
            item["nonce"] = nonce
            if nonce in conflicts:
                raise ValueError("duplicate nonce has conflicting prompts")
            if nonce in seen:
                if seen[nonce] != prompt:
                    raise ValueError("duplicate nonce has conflicting prompts")
                item["status"] = "DUPLICATE"
                result["skipped"] += 1
                continue
            seen[nonce] = prompt
            if not b1_token:
                raise RuntimeError("missing B1_ARCHIVE_TOKEN")
            if already_produced(b1_token, nonce, prompt):
                item["status"] = "VERIFIED_RESTORABLE"
                result["skipped"] += 1
                print(f"[patrol] {nonce}: verified task receipt -> SKIP")
                continue
            if not os.environ.get("AGNES_API_KEY"):
                raise RuntimeError("missing AGNES_API_KEY")
            summary_path = Path("e2e_run") / nonce / "summary.json"
            summary_path.unlink(missing_ok=True)
            child = run_e2e(nonce, prompt)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if child.returncode != 0 or summary.get("nonce") != nonce or summary.get("status") != "PASS":
                raise RuntimeError(summary.get("error") or
                                   "production failed or did not return this task's PASS receipt")
            if not already_produced(b1_token, nonce, prompt):
                raise RuntimeError("production returned PASS without a verified B1 receipt")
            item["status"] = "PRODUCED"
            result["produced"] += 1
        except Exception as exc:
            from tools.relstore.github_release_adapter import redact
            detail = redact(str(exc), token=b1_token)
            if os.environ.get("AGNES_API_KEY"):
                detail = detail.replace(os.environ["AGNES_API_KEY"], "<REDACTED>")
            item.update(status="FAILED", error_type=type(exc).__name__, error=detail[:300])
            result["failed"] += 1
            print(f"[patrol] {path}: FAILED ({type(exc).__name__}); see per-task evidence")
        finally:
            result["tasks"].append(item)
    result["status"] = "FAIL" if result["failed"] else ("IDLE" if not result["produced"] else "PASS")
    out = Path("e2e_run")
    out.mkdir(exist_ok=True)
    (out / "patrol-summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[patrol] done. produced={result['produced']} skipped={result['skipped']} failed={result['failed']}")
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
