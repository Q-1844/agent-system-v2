# -*- coding: utf-8 -*-
"""Replay driver for Session Governance Hardening 真机验收.

基于已验证的 stress_driver SSE 消费逻辑（不污染 _stress/ 证据），增强：
  - governance 工件检视（session_governance.json / creative_brief / telemetry /
    reviews/ / sessions/archive/）
  - canonical 快照（结构 + 内容指纹）
  - session trace / 指定 session 文件 dump（审查会话 / 归档会话）
  - 直接 canonical 编辑（seed violation / 制造 staleness，记录在案）

用法见 main()。所有日志/证据落 _replay/logs 与 _replay/evidence。
"""
import asyncio, hashlib, json, os, sys, time
import httpx

BASE = "http://localhost:8000"
CWD = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(CWD)
LOG_DIR = os.path.join(CWD, "logs")
SNAP_DIR = os.path.join(CWD, "evidence")
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(SNAP_DIR, exist_ok=True)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def say(msg=""):
    print(msg, flush=True)


def _pj(project):
    return os.path.join(REPO, "projects", project, "output", "project.json")


def _agents_dir(project):
    return os.path.join(REPO, "projects", project, "output", "agents")


def load_project(project):
    p = _pj(project)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def sha(s):
    return hashlib.sha256(json.dumps(s, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()[:16]


def get_session(project, agent_id):
    r = httpx.post(f"{BASE}/api/project/{project}/agents/sessions",
                   json={"agent_id": agent_id, "scope_type": "project",
                         "scope_id": project}, timeout=30)
    if r.status_code != 200:
        say(f"session create failed: {r.status_code} {r.text[:300]}")
        return {}
    return r.json()


def _session_file(project, agent_id):
    root = os.path.join(_agents_dir(project), "sessions")
    if not os.path.exists(root):
        return None, None
    best_fp, best_data, best_mtime = None, None, -1
    for f in os.listdir(root):
        if not f.endswith(".json"):
            continue
        fp = os.path.join(root, f)
        try:
            with open(fp, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            continue
        if d.get("agent_id") == agent_id:
            m = os.path.getmtime(fp)
            if m > best_mtime:
                best_fp, best_data, best_mtime = fp, d, m
    return best_fp, best_data


async def run_turn(project, agent_id, content, thinking=False, tag=None):
    """消费 turn/stream，事件全量落 JSONL，返回 final_result。复用 stress_driver
    的看门狗 + cancel 逻辑（已验证）。"""
    INACTIVITY, GRACE, MAX_ATTEMPTS = 420, 300, 2
    sess = get_session(project, agent_id)
    sid = sess.get("session_id")
    if not sid:
        return None
    tag = tag or time.strftime("%H%M%S")
    evpath = os.path.join(LOG_DIR, f"{project}_{agent_id}_{tag}.events.jsonl")
    txtpath = os.path.join(LOG_DIR, f"{project}_{agent_id}_{tag}.log")
    evf = open(evpath, "a", encoding="utf-8")
    logf = open(txtpath, "a", encoding="utf-8")

    def _log(m):
        say(m)
        logf.write(str(m) + "\n"); logf.flush()

    def _ev(ev):
        evf.write(json.dumps({"t": round(time.time() - t0g, 1),
                              "type": ev.get("type"), "raw": ev},
                             ensure_ascii=False) + "\n")
        evf.flush()

    t0g = time.time()
    _log(f"=== turn {agent_id} @ {project} tag={tag} t0={time.strftime('%H:%M:%S')} ===")
    _log(f"content: {content[:400]}")
    _log(f"events -> {evpath}")

    async with httpx.AsyncClient(timeout=httpx.Timeout(720, connect=30)) as client:
        async def _attempt():
            final = None
            url = f"{BASE}/api/project/{project}/agents/sessions/{sid}/turn/stream"
            async with client.stream("POST", url,
                                     json={"content": content, "thinking": thinking},
                                     timeout=httpx.Timeout(720, connect=30)) as resp:
                if resp.status_code == 409:
                    _log("  [busy] 409"); return "BUSY"
                if resp.status_code != 200:
                    body = await resp.aread()
                    _log(f"  [http] {resp.status_code} {body[:300]}"); return None
                t0 = time.time()
                wd, deadline = False, None
                last_act = time.monotonic()
                it = resp.aiter_lines().__aiter__()
                while True:
                    remaining = (deadline - time.monotonic()) if wd else (
                        last_act + INACTIVITY - time.monotonic())
                    try:
                        if remaining <= 0:
                            raise asyncio.TimeoutError
                        line = await asyncio.wait_for(it.__anext__(), timeout=max(remaining, 0.1))
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        if wd:
                            _log("  [watchdog] grace 仍无收尾，放弃本回合"); break
                        wd = True
                        deadline = time.monotonic() + GRACE
                        _log(f"  [watchdog] {INACTIVITY}s 无事件，发 /turn/cancel")
                        try:
                            await client.post(f"{BASE}/api/project/{project}/agents/sessions/{sid}/turn/cancel", timeout=30)
                        except Exception as e:
                            _log(f"  [cancel] 失败: {e}")
                        continue
                    except Exception as e:
                        _log(f"  [stream] 中断: {type(e).__name__}: {str(e)[:120]}"); break
                    if not line.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(line[6:])
                    except Exception:
                        continue
                    last_act = time.monotonic()
                    _ev(ev)
                    t = ev.get("type")
                    if t == "turn.started":
                        _log(f"  [turn.started] thinking={ev.get('thinking')}")
                    elif t == "tool.started":
                        _log(f"  [tool] {ev.get('label')} ({ev.get('name')})")
                    elif t == "tool.completed":
                        _log(f"  -> ok={ev.get('ok')} {ev.get('summary')}")
                    elif t == "tool.failed":
                        _log(f"  -> FAILED {ev.get('error')}")
                    elif t == "turn.completed":
                        final = ev.get("result"); _log("  [turn.completed]")
                    elif t == "turn.failed":
                        _log(f"  [turn.failed] {ev.get('error')}")
                _log(f"  attempt 耗时 {time.time()-t0:.0f}s")
            return final

        async def _wait_free():
            for _ in range(45):
                try:
                    r = await client.post(f"{BASE}/api/project/{project}/agents/sessions/{sid}/turn/cancel", timeout=30)
                    if not r.json().get("stopped"):
                        return
                except Exception:
                    pass
                await asyncio.sleep(20)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            final = await _attempt()
            if final is not None and final != "BUSY":
                evf.close(); logf.close()
                return final
            if final != "BUSY":
                _log(f"  [watchdog] 第 {attempt}/{MAX_ATTEMPTS} 未得结果"
                     f"{'，重试' if attempt < MAX_ATTEMPTS else '，放弃'}")
            await _wait_free()
    evf.close(); logf.close()
    return None


def print_meta(final):
    if not final:
        say("  (no final)"); return
    meta = (final.get("message") or {}).get("meta") or {}
    msg = final.get("message") or {}
    say(f"  status={msg.get('status')} write_success={meta.get('write_success')}/{meta.get('write_attempts')}"
        f" intent={meta.get('intent')} assets={len(meta.get('generated_assets') or [])}")
    for r in meta.get("receipts", []):
        say(f"  receipt {r.get('operation')} ok={r.get('ok')} verified={r.get('verified')}"
            f" stale={r.get('stale')} unchanged={r.get('unchanged')} count={r.get('count')}"
            f" shot_id={r.get('shot_id')}")
    tr = meta.get("trace") or []
    say(f"  trace({len(tr)}): " + " -> ".join(
        f"{t.get('tool')}{'~' if not t.get('ok') else ''}" for t in tr)[:1600])
    say("  assistant: " + (final.get("assistant_message") or "")[:600].replace("\n", " "))


def gov_dump(project):
    """治理工件全景：计数器 / brief / telemetry 尾行 / reviews / archive。"""
    ag = _agents_dir(project)
    say(f"=== governance artifacts @ {project} ===")
    gov = os.path.join(ag, "session_governance.json")
    if os.path.exists(gov):
        say("  session_governance.json: " + open(gov, encoding="utf-8").read())
    else:
        say("  session_governance.json: (absent)")
    bp = os.path.join(ag, "creative_brief.json")
    if os.path.exists(bp):
        d = json.loads(open(bp, encoding="utf-8").read())
        say(f"  creative_brief.json: brief_len={len(d.get('brief',''))} from={d.get('captured_from_agent')}")
    else:
        say("  creative_brief.json: (absent)")
    tel = os.path.join(ag, "telemetry", "context_size.jsonl")
    if os.path.exists(tel):
        lines = open(tel, encoding="utf-8").read().strip().split("\n")
        say(f"  telemetry: {len(lines)} 行; 末3行:")
        for ln in lines[-3:]:
            try:
                d = json.loads(ln)
                say(f"    {d.get('agent')}/{d.get('turn_id')} hist={d.get('history_chars')} "
                    f"sys={d.get('system_chars')} tool_io={d.get('tool_io_chars')}")
            except Exception:
                say("    " + ln[:200])
    else:
        say("  telemetry: (absent)")
    rv = os.path.join(ag, "reviews")
    if os.path.exists(rv):
        files = sorted(os.listdir(rv))
        say(f"  reviews/: {len(files)} 个")
        for f in files:
            fp = os.path.join(rv, f)
            try:
                d = json.loads(open(fp, encoding="utf-8").read())
                say(f"    {f}: trigger={d.get('trigger')} tool_calls={d.get('tool_calls')} "
                    f"review_session={d.get('review_session_id')}")
            except Exception:
                say(f"    {f}: (parse err)")
    else:
        say("  reviews/: (absent)")
    ar = os.path.join(ag, "sessions", "archive")
    if os.path.exists(ar):
        files = sorted(os.listdir(ar))
        say(f"  sessions/archive/: {len(files)} 个: {files}")
    else:
        say("  sessions/archive/: (absent)")
    # sessions dir 全景（含 review/主会话）
    sd = os.path.join(ag, "sessions")
    if os.path.exists(sd):
        say("  sessions/: " + ", ".join(
            f for f in sorted(os.listdir(sd)) if f.endswith(".json"))[:400])


def snapshot(project, tag=None):
    d = load_project(project)
    if not d:
        say("no canonical"); return None
    tag = tag or time.strftime("%H%M%S")
    fp = os.path.join(SNAP_DIR, f"{project}_{tag}.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    scenes = d.get("scenes", []) or []
    shots = [(sh.get("shot_id"), sc.get("scene_id"), sh.get("index"))
             for sc in scenes for sh in (sc.get("shots") or [])]
    dig = {"tag": tag, "file": fp, "n_scenes": len(scenes), "n_shots": len(shots),
           "story_len": len(d.get("story_text", "") or ""),
           "n_chars": len(d.get("characters", []) or []),
           "shot_structure": sha(shots),
           "shot_content": sha([(sh.get("shot_id"), sh.get("description"),
                                 sh.get("dialogue"), sh.get("characters"),
                                 sh.get("shot_size")) for sc in scenes for sh in (sc.get("shots") or [])])}
    say(json.dumps(dig, ensure_ascii=False))
    with open(os.path.join(SNAP_DIR, f"{project}_digests.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(dig, ensure_ascii=False) + "\n")
    return dig


def trace_summary(project, agent_id):
    fp, data = _session_file(project, agent_id)
    if not data:
        say(f"no session for {agent_id}"); return
    say(f"session file: {fp}")
    msgs = data.get("messages", [])
    say(f"messages={len(msgs)} scope={data.get('scope_type')}/{data.get('scope_id')}")
    for i, m in enumerate(msgs):
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        meta = m.get("meta") or {}
        tr = meta.get("trace") or []
        if role == "user":
            say(f"[{i}] USER: {m.get('content','')[:140]}")
        else:
            tools = " -> ".join(f"{t.get('tool')}{'~' if not t.get('ok') else ''}" for t in tr)
            say(f"[{i}] ASST tools({len(tr)}): {tools[:2200]}")
            st = m.get("status")
            if st and st != "completed":
                say(f"      status={st}")
            ev = meta.get("events") or []
            if ev:
                say(f"      events: {[e.get('kind') for e in ev]}")


def dump_session(project, sid):
    """按 session_id 前缀找文件并 dump（review / archive 会话用）。"""
    sd = os.path.join(_agents_dir(project), "sessions")
    ar = os.path.join(sd, "archive")
    found = None
    for root in (sd, ar):
        if not os.path.exists(root):
            continue
        for f in os.listdir(root):
            if f.startswith(sid) or sid in f:
                found = os.path.join(root, f); break
        if found:
            break
    if not found:
        say(f"session {sid} not found"); return
    say(f"=== session file: {found} ===")
    d = json.loads(open(found, encoding="utf-8").read())
    say(f"session_id={d.get('session_id')} agent={d.get('agent_id')} "
        f"scope={d.get('scope_type')}/{d.get('scope_id')} msgs={len(d.get('messages',[]))}")
    for i, m in enumerate(d.get("messages", [])):
        role = m.get("role"); c = m.get("content") or ""
        meta = m.get("meta") or {}
        say(f"[{i}] {role}: {c[:500]}".replace("\n", " "))
        if meta:
            tr = meta.get("trace") or []
            say(f"     meta: trigger={meta.get('trigger')} tool_calls={len(tr)} "
                f"trace={[t.get('tool') for t in tr]}")


def edit_shot(project, shot_id, field, value):
    """直接编辑 canonical 某镜头某字段（测试 seed 用：制造 violation / staleness）。
    记录在 _replay/evidence/manual_edits.jsonl。"""
    import ast
    d = load_project(project)
    found = None
    for sc in d.get("scenes", []):
        for sh in sc.get("shots", []):
            if sh.get("shot_id") == shot_id:
                found = (sc, sh); break
        if found:
            break
    if not found:
        say(f"shot {shot_id} not found"); return
    sc, sh = found
    old = sh.get(field)
    try:
        newv = ast.literal_eval(value) if value.startswith(("[", "{")) else value
    except Exception:
        newv = value
    sh[field] = newv
    with open(_pj(project), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    log = os.path.join(SNAP_DIR, "manual_edits.jsonl")
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "project": project, "shot_id": shot_id,
                            "field": field, "old": old, "new": newv}, ensure_ascii=False) + "\n")
    say(f"edited {shot_id}.{field}: {old!r} -> {newv!r} (logged {log})")


def canonical_summary(project):
    d = load_project(project)
    if not d:
        say(f"canonical: 项目 {project} 无 project.json"); return
    say(f"=== canonical {project} ===")
    scenes = d.get("scenes", []) or []
    say(f"scenes={len(scenes)} chars={[c.get('name') for c in d.get('characters',[]) or []]}")
    tot = 0
    for sc in scenes:
        shs = sc.get("shots", []) or []
        tot += len(shs)
        say(f"  {sc.get('scene_id')} | {sc.get('title','')[:20]} | shots={len(shs)} | chars={sc.get('characters')}")
        for sh in shs:
            say(f"    {sh.get('shot_id')} idx={sh.get('index')} size={sh.get('shot_size')} |chars={sh.get('characters')} |desc={str(sh.get('description'))[:60]}")
    say(f"shots 总计={tot}")


def main():
    args = sys.argv[1:]
    if not args:
        say(__doc__); return
    cmd = args[0]
    if cmd == "turn":
        project, agent, content = args[1], args[2], args[3]
        thinking = "--thinking" in args
        tag = args[args.index("--tag") + 1] if "--tag" in args else None
        final = asyncio.run(run_turn(project, agent, content, thinking=thinking, tag=tag))
        print_meta(final)
    elif cmd == "gov":
        gov_dump(args[1])
    elif cmd == "snap":
        snapshot(args[1], args[2] if len(args) > 2 else None)
    elif cmd == "trace":
        trace_summary(args[1], args[2])
    elif cmd == "session":
        dump_session(args[1], args[2])
    elif cmd == "edit_shot":
        edit_shot(args[1], args[2], args[3], args[4])
    elif cmd == "canonical":
        canonical_summary(args[1])
    else:
        say(f"unknown cmd: {cmd}")


if __name__ == "__main__":
    main()
