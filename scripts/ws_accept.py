# -*- coding: utf-8 -*-
"""Script Writer Web Search 真实验收（A–F）：真后端 + 真 Agnes + 真 Tavily Hub。

A 预期不搜索；D 纯聊天不搜索；B 搜索→局部读→改写；C 主动搜索再创作；
E 两种资料 2~3 次搜索；F 第 4 次搜索被 Runtime 拒绝（search_budget_exhausted）。
每 turn 记录 SSE 工具事件流、最终回复、meta.search_sources、meta.trace。
"""
import json, shutil, sys, time, io
from pathlib import Path
import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
BASE = "http://localhost:8000"
SRC = ROOT / "projects" / "__sw_streamtest"
DST_NAME = "__ws_accept"
DST = ROOT / "projects" / DST_NAME
OUT = ROOT / "scripts" / "ws_accept_report.txt"
rep = io.StringIO()

def w(*a):
    line = " ".join(str(x) for x in a)
    print(line); rep.write(line + "\n")

# ---------- 夹具 ----------
if DST.exists():
    shutil.rmtree(DST)
shutil.copytree(SRC, DST)
agents_dir = DST / "output" / "agents"
if agents_dir.exists():
    shutil.rmtree(agents_dir)
pj = DST / "output" / "project.json"
data = json.loads(pj.read_text(encoding="utf-8"))
data["name"] = DST_NAME
pj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
w(f"[setup] project={DST_NAME} scenes={len(data.get('scenes') or [])}")

r = httpx.post(f"{BASE}/api/project/{DST_NAME}/agents/sessions",
               json={"agent_id": "script_writer", "scope_type": "project", "scope_id": DST_NAME},
               timeout=30)
r.raise_for_status()
sid = r.json()["session_id"]
w(f"[setup] session={sid}")

def run_turn(tag, content, thinking=False):
    w("\n" + "=" * 78)
    w(f"[{tag}] U: {content}")
    t0 = time.time()
    events = []          # (type, name, label/summary)
    completed = None
    failed = None
    for attempt in range(2):
        events = []; completed = None; failed = None
        try:
            with httpx.stream("POST",
                              f"{BASE}/api/project/{DST_NAME}/agents/sessions/{sid}/turn/stream",
                              json={"content": content, "thinking": thinking},
                              timeout=httpx.Timeout(connect=30, read=600, write=60, pool=30)) as resp:
                if resp.status_code != 200:
                    failed = f"HTTP {resp.status_code}: " + resp.read().decode("utf-8", "replace")[:200]
                    break
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
                    if t == "tool.started":
                        events.append(("started", ev.get("name"), ev.get("label") or ""))
                    elif t == "tool.completed":
                        events.append(("completed", ev.get("name"), (ev.get("summary") or "")[:60]))
                    elif t == "tool.failed":
                        events.append(("failed", ev.get("name"), (ev.get("error") or "")[:60]))
                    elif t == "turn.completed":
                        completed = ev.get("result")
                    elif t == "turn.failed":
                        failed = ev.get("error")
        except Exception as e:
            failed = f"{type(e).__name__}: {e}"
        if failed and ("429" in str(failed) or "rate" in str(failed).lower()):
            w(f"  [429，等 45s 重试 {attempt+1}/2]")
            time.sleep(45)
            continue
        break
    dt = time.time() - t0
    if completed:
        reply = (completed.get("assistant_message") or "")
        trace = completed.get("trace") or []
        meta = (completed.get("message") or {}).get("meta") or {}
        sources = meta.get("search_sources") or completed.get("search_sources") or []
        receipts = meta.get("receipts") or []
        status = (completed.get("message") or {}).get("status")
        ws_trace = [t for t in trace if t.get("tool") == "web.search"]
        w(f"  [{dt:.0f}s] status={status} tools:")
        for k, n, info in events:
            w(f"    {k:9s} {n:12s} {info}")
        w(f"  web.search trace: {json.dumps(ws_trace, ensure_ascii=False)[:400]}")
        w(f"  meta.search_sources({len(sources)}): {json.dumps(sources, ensure_ascii=False)[:300]}")
        w(f"  receipts ok数: {sum(1 for x in receipts if x.get('ok'))}/{len(receipts)}")
        w(f"  回复({len(reply)}字): {reply[:400]}")
        return {"tag": tag, "events": events, "trace": trace, "sources": sources,
                "reply": reply, "meta": meta, "status": status}
    w(f"  [{dt:.0f}s] FAILED: {failed}")
    return {"tag": tag, "events": events, "trace": [], "sources": [], "reply": "",
            "meta": {}, "status": "failed", "failed": failed}

# ---------- A：改第8段更紧张 → 预期不搜索 ----------
a = run_turn("A-改写第8段", "把第8段写得更紧张。")
ws_a = [e for e in a["events"] if e[1] == "web.search"]
w(f"  >>> A 判定: web.search 调用 {len(ws_a)} 次（预期 0）；story 工具: "
  f"{sorted({e[1] for e in a['events'] if e[1] and e[1] != 'web.search'})}")

# ---------- D：纯聊天 → 预期不搜索 ----------
d = run_turn("D-纯聊天", "你觉得这个角色怎么样？随便聊聊就行，先不要改任何东西。")
ws_d = [e for e in d["events"] if e[1] == "web.search"]
w(f"  >>> D 判定: web.search 调用 {len(ws_d)} 次（预期 0）")

# ---------- B：搜索→局部读→改写 ----------
b = run_turn("B-搜资料后重写第8段", "查一下真实急诊室抢救流程，然后根据资料重写第8段的 summary，让抢救过程更真实。")
ws_ok = [e for e in b["events"] if e[1] == "web.search" and e[0] == "completed"]
tools_b = [e[1] for e in b["events"] if e[1]]
w(f"  >>> B 判定: web.search 成功 {len(ws_ok)} 次；工具序列含 search/read/update: "
  f"{'web.search' in tools_b}/{'story.read' in tools_b or 'story.list' in tools_b}/{'story.update' in tools_b}")
w(f"  >>> B 来源数: {len(b['sources'])}（预期 ≥1，仅 title+url）")

# ---------- C：1940s 上海 → 允许主动搜索 ----------
c = run_turn("C-历史场景创作", "写一个1940年代上海街头的故事段（追加到末尾），要真实可信。")
ws_c = [e for e in c["events"] if e[1] == "web.search" and e[0] == "completed"]
created_c = any(e[1] in ("story.create", "story.create_many") and e[0] == "completed" for e in c["events"])
w(f"  >>> C 判定: 主动搜索 {len(ws_c)} 次（预期 ≥0，允许）；创建成功: {created_c}")

# ---------- E：两种资料 → ≤3 次搜索 ----------
e = run_turn("E-双资料复杂任务",
             "先查两个真实资料：①外卖骑手一天的工作流程；②长途火车硬座的乘车体验。"
             "然后基于资料在末尾追加两个新故事段：一个写骑手冒雨送餐，一个写春运火车硬座上的旅人。")
ws_e = [e for e in e["events"] if e[1] == "web.search" and e[0] == "completed"]
created_e = sum(1 for x in ["story.create", "story.create_many"] for ev in e["events"] if ev[1] == x and ev[0] == "completed")
w(f"  >>> E 判定: web.search 成功 {len(ws_e)} 次（预期 2~3）；创建: {created_e} 次")

# ---------- F：诱导第 4 次 → Runtime 拒绝 ----------
f = run_turn("F-诱导超限搜索",
             "连续搜索四个不同主题：①唐朝长安城的布局；②唐朝官员品级制度；③唐朝饮食文化；④唐朝服饰。"
             "每个主题各搜一次，一共搜四次，然后每次搜索后各写一段（共四段）追加到末尾。")
ws_all_f = [e for e in f["events"] if e[1] == "web.search"]
ws_ok_f = [e for e in ws_all_f if e[0] == "completed"]
ws_fail_f = [e for e in ws_all_f if e[0] == "failed"]
f_trace_ws = [t for t in f["trace"] if t.get("tool") == "web.search"]
budget_refused = [t for t in f_trace_ws if t.get("result") == "error" and "上限" in (t.get("error") or "")]
w(f"  >>> F 判定: web.search 事件 {len(ws_all_f)}（成功 {len(ws_ok_f)}）；trace 中搜索 {len(f_trace_ws)} 条，"
  f"其中 budget 拒绝 {len(budget_refused)} 条（预期 ≥1）")
w(f"  >>> F 回复是否使用已有资料收尾: {'资料' in f['reply'] or '搜索' in f['reply']}")

# ---------- 跨轮污染检查 ----------
w("\n" + "=" * 78)
w("[跨轮检查] 会话消息（应只有 user/assistant 文本，无搜索 snippet）")
r = httpx.get(f"{BASE}/api/project/{DST_NAME}/agents/sessions/{sid}", timeout=30)
msgs = r.json().get("messages") or []
bad = []
for m in msgs:
    body = m.get("content") or ""
    if m.get("role") not in ("user", "assistant") or ("meta" in body and "results" in body):
        bad.append(m)
snippet_markers = ["心肺复苏", "score", "raw_content", "include_answer"]
leak = [(m.get("role"), (m.get("content") or "")[:80]) for m in msgs
        for mk in ["search_budget", "n_results"] if mk in (m.get("content") or "")]
w(f"  消息条数 {len(msgs)}；非 user/assistant 消息: {len(bad)}；含搜索内部字段的持久化消息: {len(leak)}")
for role, head in leak[:5]:
    w(f"    LEAK? {role}: {head}")

rep_text = rep.getvalue()
OUT.write_text(rep_text, encoding="utf-8")
print("\nreport ->", OUT)
