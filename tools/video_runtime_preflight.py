#!/usr/bin/env python3
"""Read-only work inspection before installing media packages on a fresh runner."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from tools.video_task_state import task_index, validate_nonce, validate_task, verified_record


def needs_media(mode, token, *, nonce="", pending_dir=Path("tasks/pending")):
    if mode == "e2e":
        if not nonce:
            return True  # the producer will allocate a fresh immutable nonce
        nonces = [validate_nonce(nonce)]
    elif mode == "patrol":
        nonces = [validate_task(json.loads(p.read_text(encoding="utf-8")))[0]
                  for p in sorted(pending_dir.glob("*.json"))]
    else:
        raise ValueError("expected e2e or patrol")
    for task_nonce in nonces:
        if not token:
            raise RuntimeError("missing B1_ARCHIVE_TOKEN")
        state = task_index(token, task_nonce).read()
        if not verified_record(None if state is None else state["doc"], task_nonce):
            return True
    return False


def main():
    try:
        needed = needs_media(sys.argv[1], os.environ.get("B1_ARCHIVE_TOKEN"),
                             nonce=os.environ.get("TASK_NONCE", ""))
        output = f"needs_media={'true' if needed else 'false'}\n"
        print(output.strip())
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
                f.write(output)
        return 0
    except Exception as exc:
        print(f"work inspection failed: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
