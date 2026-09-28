# -*- coding: utf-8 -*-
"""Phase 41 修复阶段针对测试驱动器（正式 REST 流式入口，thinking=true）。

与 _41_redo_run.py（进程内装配）不同：后端已在跑（用户验收用），改走
39C canary 同款 REST 流式入口——单一写者，不与后端抢 projects/。
- 会话：POST /agents/sessions（agent_id=director，全新会话）
- turn：POST /agents/sessions/{sid}/turn/stream（thinking=true）
- 证据：events_1.jsonl 全事件 + result_1.json 终态，落 _41_repair/
- 外层重试：瞬态（5xx/网络/超时）同 session 重发同任务 ≤5 次（resume 语义）
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
PROJ = "雨灯归航"
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_41_repair"
TASK_FILE = OUT / "task_bridge_s03c03.txt"
TRANSIENT_SIGNS = ("500", "502", "503", "504", "do_request_failed", "timeout",
                   "timed out", "connection", "stream 错误", "disconnect")


def ensure_session() -> str:
    r = httpx.post(f"{BASE}/api/project/{PROJ}/agents/sessions",
                   json={"agent_id": "director", "scope_type": "project",
                         "scope_id": ""}, timeout=30)
    r.raise_for_status()
    d = r.json()
    sid = d.get("session_id") or d.get("id") or d["session"]["session_id"]
    (OUT / "session_created.json").write_text(
        json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return sid


def stream_turn(session_id: str, content: str, timeout_s: float = 5400.0):
    events, result = [], None
    with httpx.stream(
        "POST",
        f"{BASE}/api/project/{PROJ}/agents/sessions/{session_id}/turn/stream",
        json={"content": content, "thinking": True}, timeout=timeout_s,
    ) as r:
        r.raise_for_status()
        with (OUT / "events_1.jsonl").open("a", encoding="utf-8") as f:
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except Exception:
                    continue
                events.append(ev)
                f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
                t = ev.get("type")
                if t == "tool.completed":
                    print(f"  [tool] {ev.get('name')}", flush=True)
                elif t == "tool.progress":
                    print(f"  [progress] {(ev.get('message') or '')[:90]}",
                          flush=True)
                elif t in ("turn.completed", "turn.failed"):
                    result = ev
                    print(f"  [{t}]", flush=True)
    return result


def main() -> int:
    task = TASK_FILE.read_text(encoding="utf-8").strip()
    sid = ensure_session()
    print(f"director session: {sid}", flush=True)
    t0 = time.time()
    result = None
    for attempt in range(1, 6):
        failure = None
        try:
            result = stream_turn(sid, task)
            raw = json.dumps(result, ensure_ascii=False, default=str) if result else ""
            if "[执行失败" in raw or "turn.failed" in raw:
                failure = raw[:300]
        except Exception as e:  # noqa: BLE001
            result = None
            failure = f"{type(e).__name__}: {e}"
        if failure is None and result is not None:
            break
        transient = any(s in failure for s in TRANSIENT_SIGNS) if failure else False
        if attempt >= 5 or not transient:
            break
        wait_s = min(30 * attempt, 120)
        print(f"\n[retry {attempt}/5] turn failed（{failure[:160]}）"
              f"→ {wait_s}s 后重发同任务（session={sid}）", flush=True)
        time.sleep(wait_s)
    (OUT / "result_1.json").write_text(
        json.dumps({"session_id": sid, "terminal": result,
                    "wall_s": int(time.time() - t0)},
                   ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    if result is None:
        print("=== turn 未到达终态（见 events_1.jsonl 尾部）===", flush=True)
        return 1
    msg = result.get("assistant_message") or result.get("message") or ""
    print(f"\n=== turn completed in {int(time.time()-t0)}s ===", flush=True)
    print(str(msg)[:1500], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
