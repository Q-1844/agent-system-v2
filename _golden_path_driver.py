# -*- coding: utf-8 -*-
"""Golden Path 全流程 driver（基于已验证的 gp_dir_visual_smoke.run_turn 模式）。

复用既有 API：POST /api/projects/create、POST .../agents/sessions、
POST .../sessions/{sid}/turn/stream（SSE）、GET .../resource-locks、GET .../events。
非新测试框架——仅把 smoke 的 run_turn 项目参数化，供 Golden Path 三阶段复用。

用法：
  python _golden_path_driver.py newproj <name>             # 建项目
  python _golden_path_driver.py turn <project> <agent> "<content>" [--thinking]
  python _golden_path_driver.py canonical <project>        # canonical 摘要
  python _golden_path_driver.py locks <project>            # 资源锁
  python _golden_path_driver.py session <project> <agent>  # 会话 id + 历史
  python _golden_path_driver.py events <project> <seconds> # 订阅项目 SSE N 秒

长 turn 自动写日志到 _gp_<project>_<agent>.log（flush），便于后台 tail。
"""
import asyncio
import json
import os
import sys
import time
import hashlib
import httpx

BASE = "http://localhost:8000"
CWD = os.path.dirname(os.path.abspath(__file__))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def _pj(project: str) -> str:
    return os.path.join(CWD, "projects", project, "output", "project.json")


def say(msg=""):
    print(msg, flush=True)


def sha(s) -> str:
    return hashlib.sha256(json.dumps(s, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()[:16]


def project_state(project: str):
    p = _pj(project)
    if not os.path.exists(p):
        return None, {}
    d = json.load(open(p, encoding="utf-8"))
    shots = {}
    for sc in d.get("scenes", []) or []:
        for sh in sc.get("shots", []) or []:
            shots[sh["shot_id"]] = sh
    return d, shots


def domain_hashes(project: str):
    d, _ = project_state(project)
    if not d:
        return None
    return {"story_text": sha(d.get("story_text", "")),
            "scenes_text": sha([(sc.get("scene_id"), sc.get("summary"), sc.get("characters"))
                                 for sc in d.get("scenes", []) or []]),
            "characters": sha(d.get("characters", [])),
            "shots": sha([(sh.get("shot_id"), sh.get("scene_id"), sh.get("index"),
                           sh.get("description"), sh.get("shot_size"), sh.get("camera"))
                          for sc in d.get("scenes", []) or []
                          for sh in sc.get("shots", []) or []])}


def create_project(name: str):
    r = httpx.post(f"{BASE}/api/projects/create", params={"name": name}, timeout=30)
    say(f"create_project -> {r.status_code} {r.text[:200]}")
    return r.json() if r.status_code == 200 else None


def get_session(project: str, agent_id: str) -> str:
    r = httpx.post(f"{BASE}/api/project/{project}/agents/sessions",
                   json={"agent_id": agent_id, "scope_type": "project", "scope_id": project},
                   timeout=30)
    if r.status_code != 200:
        say(f"session create failed: {r.status_code} {r.text[:300]}")
        return ""
    data = r.json()
    sid = data.get("session_id")
    msgs = data.get("messages", [])
    say(f"session({agent_id})={sid} msgs={len(msgs)}")
    return sid


async def run_turn(project: str, agent_id: str, content: str,
                   thinking: bool = False, log_path: str = None) -> dict:
    """消费 turn 流，返回 final_result。带语义不活跃看门狗（复用 smoke 逻辑）。"""
    INACTIVITY = 420
    GRACE = 300
    MAX_ATTEMPTS = 2
    sid = get_session(project, agent_id)
    if not sid:
        return None

    logf = open(log_path, "a", encoding="utf-8") if log_path else None
    def _log(msg):
        say(msg)
        if logf:
            logf.write(str(msg) + "\n"); logf.flush()

    async with httpx.AsyncClient(timeout=httpx.Timeout(720, connect=30)) as client:
        async def _attempt():
            progresses = []
            final = None
            url = f"{BASE}/api/project/{project}/agents/sessions/{sid}/turn/stream"
            async with client.stream("POST", url,
                                     json={"content": content, "thinking": thinking},
                                     timeout=httpx.Timeout(720, connect=30)) as resp:
                if resp.status_code == 409:
                    _log("  [busy] 会话仍有任务在跑（409）"); return "BUSY", progresses
                if resp.status_code != 200:
                    body = await resp.aread()
                    _log(f"  [http] {resp.status_code} {body[:300]}"); return None, progresses
                t0 = time.time()
                wd_fired = False
                deadline = None
                last_act = time.monotonic()
                it = resp.aiter_lines().__aiter__()
                while True:
                    if wd_fired:
                        remaining = deadline - time.monotonic()
                    else:
                        remaining = last_act + INACTIVITY - time.monotonic()
                    try:
                        if remaining <= 0:
                            raise asyncio.TimeoutError
                        line = await asyncio.wait_for(it.__anext__(), timeout=max(remaining, 0.1))
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        if wd_fired:
                            _log("  [watchdog] 宽限期仍无收尾事件，放弃本回合"); break
                        wd_fired = True
                        deadline = time.monotonic() + GRACE
                        _log(f"  [watchdog] {INACTIVITY}s 无真实事件，发送 /turn/cancel 并等待收尾")
                        try:
                            await client.post(f"{BASE}/api/project/{project}/agents/sessions/{sid}/turn/cancel", timeout=30)
                        except Exception as e:
                            _log(f"  [cancel] 失败: {e}")
                        continue
                    except Exception as e:
                        _log(f"  [stream] 读取中断: {type(e).__name__}: {str(e)[:120]}"); break
                    if not line.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(line[6:])
                    except Exception:
                        continue
                    last_act = time.monotonic()
                    t = ev.get("type")
                    if t == "turn.started":
                        _log(f"  [turn.started] thinking={ev.get('thinking')}")
                    elif t == "reasoning.delta":
                        _log(f"  [reasoning] {ev.get('text','')[:80]}")
                    elif t == "tool.started":
                        _log(f"  [tool] {ev.get('label')} ({ev.get('name')})")
                    elif t == "tool.progress":
                        progresses.append(ev.get("message"))
                        _log(f"  [prog] {ev.get('message')}")
                    elif t == "tool.completed":
                        _log(f"  -> ok={ev.get('ok')} {ev.get('summary')}")
                    elif t == "tool.failed":
                        _log(f"  -> FAILED {ev.get('error')}")
                    elif t == "assistant.delta":
                        pass  # 不打印逐 token，太吵
                    elif t == "turn.completed":
                        final = ev.get("result")
                        _log("  [turn.completed]")
                    elif t == "turn.failed":
                        _log(f"  [turn.failed] {ev.get('error')}")
            _log(f"  attempt 耗时 {time.time()-t0:.0f}s")
            return final, progresses

        async def _wait_session_free():
            for _ in range(45):
                try:
                    r = await client.post(f"{BASE}/api/project/{project}/agents/sessions/{sid}/turn/cancel", timeout=30)
                    if not r.json().get("stopped"):
                        return
                except Exception:
                    pass
                await asyncio.sleep(20)
            _log("  [busy] 等待会话空闲超时（15 分钟）")

        for attempt in range(1, MAX_ATTEMPTS + 1):
            final, progresses = await _attempt()
            if final is not None and final != "BUSY":
                if logf: logf.close()
                return final
            if final != "BUSY":
                _log(f"  [watchdog] 第 {attempt}/{MAX_ATTEMPTS} 次尝试未得结果"
                     f"{'，重试' if attempt < MAX_ATTEMPTS else '，放弃'}")
            await _wait_session_free()
        if logf: logf.close()
        return None


def print_meta(final):
    if not final:
        say("  (no final)")
        return
    meta = (final.get("message") or {}).get("meta") or {}
    msg = final.get("message") or {}
    say(f"  status={msg.get('status')} write_success={meta.get('write_success')}/{meta.get('write_attempts')}"
        f" generated_assets={len(meta.get('generated_assets') or [])}")
    for r in meta.get("receipts", []):
        say(f"  receipt {r.get('operation')} ok={r.get('ok')} verified={r.get('verified')}"
            f" cancelled={r.get('cancelled')} count={r.get('count')}")
        for it in (r.get("results") or [])[:12]:
            say(f"    {it.get('shot_id')} {it.get('frame')} {it.get('status')}"
                f" ok={it.get('ok')} {('err=' + str(it.get('error'))[:60]) if it.get('error') else ''}")
    say("  assistant: " + (final.get("assistant_message") or "")[:300].replace("\n", " "))


def canonical_summary(project: str):
    d, shots = project_state(project)
    if not d:
        say(f"canonical: 项目 {project} 无 project.json"); return None
    say(f"=== canonical {project} ===")
    say(f"name={d.get('name')} updated_at={d.get('updated_at')}")
    say(f"story_text 长度={len(d.get('story_text','') or '')}")
    scenes = d.get("scenes", []) or []
    say(f"scenes={len(scenes)} next_scene_seq={d.get('next_scene_seq')}")
    tot_shots = 0
    for sc in scenes:
        sc_shots = sc.get("shots", []) or []
        tot_shots += len(sc_shots)
        say(f"  {sc.get('scene_id')} | {sc.get('title','')[:30]} | shots={len(sc_shots)} | chars={sc.get('characters')}")
        for sh in sc_shots:
            ff = sh.get("first_frame_batches") or []
            sel_ff = sh.get("selected_first_frame")
            fv = sh.get("final_video")
            fv = fv if isinstance(fv, dict) else {}
            n_img = sum(len((b.get("results") or b.get("variants") or [])) for b in ff) if ff else 0
            vid_url = fv.get("url") or fv.get("local") or (fv if isinstance(sh.get("final_video"), str) else None)
            say(f"    {sh.get('shot_id')} idx={sh.get('index')} size={sh.get('shot_size')} |ff_batches={len(ff)} ff_imgs≈{n_img} sel_ff={'Y' if sel_ff else 'N'} |video={'Y' if vid_url else 'N'} status={sh.get('status')}")
    say(f"shots 总计={tot_shots}")
    chars = d.get("characters", []) or []
    say(f"characters={len(chars)}")
    for ch in chars:
        views = ch.get("views") or []
        say(f"  {ch.get('name')} | views={len(views)} | desc_en={(ch.get('description_en') or '')[:50]}")
    return d


def locks_summary(project: str):
    r = httpx.get(f"{BASE}/api/project/{project}/resource-locks", timeout=15)
    say(f"resource-locks -> {r.status_code} {r.text[:400]}")


def main():
    args = sys.argv[1:]
    if not args:
        say(__doc__); return
    cmd = args[0]
    if cmd == "newproj":
        create_project(args[1])
    elif cmd == "turn":
        project, agent, content = args[1], args[2], args[3]
        thinking = "--thinking" in args
        safe_agent = agent.replace(" ", "_")
        logp = os.path.join(CWD, f"_gp_{project.replace(' ','_')}_{safe_agent}.log")
        say(f"=== turn {agent} @ {project} ===")
        say(f"content: {content[:200]}")
        say(f"log -> {logp}")
        final = asyncio.run(run_turn(project, agent, content, thinking=thinking, log_path=logp))
        say("--- meta ---")
        print_meta(final)
        say("--- hashes ---")
        say(json.dumps(domain_hashes(project), ensure_ascii=False))
    elif cmd == "canonical":
        canonical_summary(args[1])
    elif cmd == "locks":
        locks_summary(args[1])
    elif cmd == "session":
        get_session(args[1], args[2])
    elif cmd == "events":
        asyncio.run(_watch_events(args[1], int(args[2]) if len(args) > 2 else 30))
    else:
        say(f"unknown cmd: {cmd}")


async def _watch_events(project: str, seconds: int):
    seen = []
    async with httpx.AsyncClient(timeout=700) as c:
        async with c.stream("GET", f"{BASE}/api/project/{project}/events") as resp:
            t0 = time.time()
            async for line in resp.aiter_lines():
                if time.time() - t0 > seconds:
                    break
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except Exception:
                    continue
                t = ev.get("type")
                say(f"  [bus] {t} {ev.get('resource') or ev.get('operation') or ''}")
                seen.append(t)
    say(f"watched {len(seen)} events in {seconds}s")


if __name__ == "__main__":
    main()
