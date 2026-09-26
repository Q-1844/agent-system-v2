# -*- coding: utf-8 -*-
"""Phase 40 — System Agent Canary 驱动（真实 API，经运行中后端的正式入口）。

设计纪律（40.md）：
- 只用生产环境正常会获得的入口：POST /agents/sessions（新建 director 会话）+
  POST /agents/sessions/{sid}/turn/stream（完整 tool-loop，turn.completed 携带
  receipts+trace）。不注入 39F 结论、不代答、不预修输入状态。
- 全部 SSE 事件增量落盘（events.jsonl），turn.completed 的完整 result
  （含 tool receipts/trace）单存 result.json——证据可重放。
- 会话持久化副本（session.json）每 turn 后抓取。

用法：
  python _40_canary_driver.py new  --tag canary_a
  python _40_canary_driver.py turn --session sess_xxx --out _40_canary/canary_a --task-file task.txt
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import requests

BASE = "http://localhost:8000"
PROJECT = "Crane_Lake_P40A"
ROOT = Path(__file__).parent
PDIR = ROOT / "projects" / PROJECT
EVDIR = ROOT / "_40_canary"


def _sid_path(sid: str) -> Path:
    return EVDIR / "sessions" / f"{sid}.json"


def cmd_new(tag: str) -> str:
    # project scope 恒复用长存会话（AI-7D-R）——Canary 需要全新干净对话，
    # 用唯一 scope 走 get_or_create 的新建路径（scope 仅做会话隔离元数据）。
    r = requests.post(
        f"{BASE}/api/project/{PROJECT}/agents/sessions",
        json={"agent_id": "director", "scope_type": "scene",
              "scope_id": f"canary_p40_{tag}_{int(time.time())}"},
        timeout=30)
    r.raise_for_status()
    s = r.json()
    sid = s["session_id"]
    EVDIR.joinpath("sessions").mkdir(parents=True, exist_ok=True)
    _sid_path(sid).write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"new session: {sid} (scope_id={s.get('scope_id')})")
    return sid


def _snapshot_session(sid: str, out: Path):
    src = PDIR / "output" / "agents" / "sessions" / f"{sid}.json"
    if src.exists():
        shutil.copy2(src, out / "session.json")


def cmd_turn(sid: str, task: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    n = len(list(out.glob("turn_*_result.json"))) + 1
    ev_path = out / f"turn_{n}_events.jsonl"
    print(f"turn {n} -> {ev_path}（长任务，SSE 增量落盘）")
    t0 = time.time()
    completed = None
    failed = None
    with requests.post(
            f"{BASE}/api/project/{PROJECT}/agents/sessions/{sid}/turn/stream",
            json={"content": task}, stream=True, timeout=(10, 7200)) as resp:
        resp.raise_for_status()
        with ev_path.open("w", encoding="utf-8") as f:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[len("data: "):])
                except Exception:
                    ev = {"type": "unparsed", "raw": line}
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
                f.flush()
                t = ev.get("type")
                if t == "turn.completed":
                    completed = ev.get("result")
                elif t == "turn.failed":
                    failed = ev.get("error")
                elif t in ("tool.started", "tool.completed"):
                    print(f"  [{int(time.time()-t0):>5}s] {t}: {ev.get('tool') or ev.get('name')}")
                elif t in ("turn.progress", "progress"):
                    print(f"  [{int(time.time()-t0):>5}s] progress: {str(ev.get('message') or ev.get('detail') or '')[:90]}")
    dur = time.time() - t0
    if completed is not None:
        (out / f"turn_{n}_result.json").write_text(
            json.dumps(completed, ensure_ascii=False, indent=1), encoding="utf-8")
        reply = completed.get("assistant_message") or ""
        tools = [(t.get("tool"), bool(t.get("ok", t.get("ok", True)))) for t in (completed.get("trace") or [])]
        print(f"turn {n} completed in {int(dur)}s")
        print(f"tools ({len(tools)}): {[t[0] for t in tools]}")
        print(f"reply head: {reply[:400]}")
    else:
        print(f"turn {n} FAILED after {int(dur)}s: {failed}")
        sys.exit(2)
    _snapshot_session(sid, out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["new", "turn"])
    ap.add_argument("--tag", default="canary")
    ap.add_argument("--session", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--task", default=None)
    ap.add_argument("--task-file", default=None)
    a = ap.parse_args()
    if a.cmd == "new":
        cmd_new(a.tag)
        return
    sid = a.session
    assert sid, "--session required for turn"
    task = a.task
    if a.task_file:
        task = Path(a.task_file).read_text(encoding="utf-8")
    assert task, "--task or --task-file required"
    cmd_turn(sid, task, Path(a.out) if a.out else EVDIR / "turns")


if __name__ == "__main__":
    main()
