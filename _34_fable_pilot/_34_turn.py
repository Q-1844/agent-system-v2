# _34_fable_pilot/_34_turn.py — 34.md Producer/Specialist turn 驱动器。
# 与 _phase_d_turn.py 同构（Phase D 真机验证过的模式）：建 session → SSE 流式 turn
# → 事件实时打印 → final + receipts 落盘 _34_fable_pilot/runs/<label>.json。
# 用法：python _34_fable_pilot/_34_turn.py <label> <agent_id> <prompt_file> [timeout_s]
import asyncio
import io
import json
import sys
import time
from pathlib import Path

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
PROJ = "Fable_Pilot_Fox_and_Grapes_V1"
OUT_DIR = Path(__file__).parent / "runs"
OUT_DIR.mkdir(exist_ok=True)


async def run_turn(label: str, agent_id: str, prompt: str, timeout_s: int = 7200):
    r = httpx.post(f"{BASE}/api/project/{PROJ}/agents/sessions",
                   json={"agent_id": agent_id, "scope_type": "project", "scope_id": ""},
                   timeout=30)
    r.raise_for_status()
    sid = r.json()["session_id"]
    print(f"[{label}] {agent_id} sid={sid}", flush=True)
    events, final, err = [], None, None
    t0 = time.time()
    out_path = OUT_DIR / f"{label}.json"
    with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=30.0)) as c:
        with c.stream("POST",
                      f"{BASE}/api/project/{PROJ}/agents/sessions/{sid}/turn/stream",
                      json={"content": prompt, "thinking": False}) as resp:
            buf = b""
            for chunk in resp.iter_bytes():
                buf += chunk
                while b"\n\n" in buf:
                    raw, buf = buf.split(b"\n\n", 1)
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    try:
                        ev = json.loads(data)
                    except Exception:
                        continue
                    et = ev.get("type")
                    if et == "turn.completed":
                        final = ev.get("result")
                    elif et == "turn.failed":
                        err = ev.get("error")
                    else:
                        events.append(ev)
                        if et == "tool.started":
                            print(f"  tool> {ev.get('tool_name') or ev.get('name')}", flush=True)
                        elif et == "token.delta":
                            pass
                        elif et in ("turn.progress", "status"):
                            print(f"  .. {str(ev)[:120]}", flush=True)
    rec = {"label": label, "agent": agent_id, "session_id": sid,
           "prompt": prompt, "final": final, "events_n": len(events),
           "error": err, "wall_s": round(time.time() - t0, 1),
           "events": events}
    out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{label}] done wall={rec['wall_s']}s err={err} final_len="
          f"{len(json.dumps(final, ensure_ascii=False)) if final else 0}", flush=True)


if __name__ == "__main__":
    label, agent_id, pf = sys.argv[1], sys.argv[2], sys.argv[3]
    timeout_s = int(sys.argv[4]) if len(sys.argv) > 4 else 7200
    prompt = Path(pf).read_text(encoding="utf-8")
    asyncio.run(run_turn(label, agent_id, prompt, timeout_s))
