# -*- coding: utf-8 -*-
"""验收补测：B-retry（搜索→局部读→改写成功路径）+ F-retry（第4次搜索被 Runtime 拒绝）。
复用 __ws_accept 项目与会话。"""
import json, sys, time, io
from pathlib import Path
import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "http://localhost:8000"
P = "__ws_accept"
OUT = Path(__file__).resolve().parent.parent / "scripts" / "ws_accept_retry_report.txt"
rep = io.StringIO()

def w(*a):
    line = " ".join(str(x) for x in a)
    print(line); rep.write(line + "\n")

r = httpx.get(f"{BASE}/api/project/{P}/agents/sessions", timeout=30)
sess = r.json()["sessions"][0]["session_id"] if isinstance(r.json(), dict) and r.json().get("sessions") else None
# 兜底：直接取该项目 sessions 列表
if sess is None:
    r2 = httpx.get(f"{BASE}/api/project/{P}/agents/sessions", timeout=30)
    w("[setup] sessions resp:", r2.text[:200])
    raise SystemExit(1)
w(f"[setup] project={P} session={sess}")

def run_turn(tag, content):
    w("\n" + "=" * 78)
    w(f"[{tag}] U: {content}")
    t0 = time.time()
    events, completed, failed = [], None, None
    with httpx.stream("POST", f"{BASE}/api/project/{P}/agents/sessions/{sess}/turn/stream",
                      json={"content": content, "thinking": False},
                      timeout=httpx.Timeout(connect=30, read=600, write=60, pool=30)) as resp:
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                ev = json.loads(payload)
            except Exception:
                continue
            t = ev.get("type")
            if t in ("tool.started", "tool.completed", "tool.failed"):
                events.append((t.split(".")[1], ev.get("name"),
                               (ev.get("summary") or ev.get("error") or "")[:70]))
            elif t == "turn.completed":
                completed = ev.get("result")
            elif t == "turn.failed":
                failed = ev.get("error")
    w(f"  [{time.time()-t0:.0f}s]")
    for k, n, info in events:
        w(f"    {k:9s} {n:14s} {info}")
    if completed:
        meta = (completed.get("message") or {}).get("meta") or {}
        ws = [x for x in (completed.get("trace") or []) if x.get("tool") == "web.search"]
        w(f"  web.search trace: {json.dumps(ws, ensure_ascii=False)[:500]}")
        w(f"  meta.search_sources({len(meta.get('search_sources') or [])})")
        w(f"  receipts: {json.dumps([{'op': x.get('operation'), 'ok': x.get('ok'), 'err': x.get('error')} for x in meta.get('receipts') or []], ensure_ascii=False)[:400]}")
        w(f"  回复: {(completed.get('assistant_message') or '')[:350]}")
        return completed
    w(f"  FAILED: {failed}")
    return None

# ---------- B-retry：搜索 → 局部读 → 改写成功 ----------
b = run_turn("B-retry", "重试上次没成功的修改：先查一次真实急诊室抢救流程，然后用 story.read 读取第8段（s08）的最新内容和版本指纹，最后根据搜索到的资料重写第8段的 summary，让抢救过程更真实。")
ws_b = [x for x in (b.get("trace") or []) if x.get("tool") == "web.search"] if b else []
ru_ok = bool(b) and any(x.get("operation") == "story.update" and x.get("ok") and x.get("verified")
                        for x in ((b.get("message") or {}).get("meta") or {}).get("receipts") or [])
w(f"  >>> B-retry 判定: web.search {len(ws_b)} 次（{sum(1 for x in ws_b if x.get('ok'))} 成功）；story.update 真实成功: {ru_ok}")

# ---------- F-retry：诱导第 4 次搜索 → Runtime 拒绝 ----------
f = run_turn("F-retry", "请依次发起四次 web.search 搜索，每次一个查询词：①唐朝长安城的布局；②唐朝官员品级制度；③唐朝饮食文化；④唐朝服饰。搜索全部结束后，再基于搜到的资料创建四个故事段追加到末尾。")
ws_f = [x for x in (f.get("trace") or []) if x.get("tool") == "web.search"] if f else []
ok_f = sum(1 for x in ws_f if x.get("ok"))
refused_f = [x for x in ws_f if "上限" in (x.get("error") or "")]
w(f"  >>> F-retry 判定: web.search 共 {len(ws_f)} 次（成功 {ok_f}）；budget 拒绝 {len(refused_f)} 条（预期 ≥1）")

OUT.write_text(rep.getvalue(), encoding="utf-8")
print("\nreport ->", OUT)
