# _phaseb_e2e.py — Agent System V2 Phase B 真机 E2E 驱动（26.md §22/§23/§24）。
#
# 用法（后端须已在 :8000 跑【Phase B 代码】）：
#   python _phaseb_e2e.py setup     # 建项目 + 故事 + profile（幂等）
#   python _phaseb_e2e.py main      # §22 自然语言目标（不提示工具顺序）
#   python _phaseb_e2e.py caseA     # §23.A review needs_fix 不假成功
#   python _phaseb_e2e.py caseB     # §23.B 遮面冲突先识别不无脑重试
#   python _phaseb_e2e.py caseC     # §23.C OCC stale（SSE 事件触发外部写）
#   python _phaseb_e2e.py producer  # §24 Producer 真实委派
#
# 判定从证据不从文本：SSE 事件流 + 最终 receipts + canonical 落盘 全量存
# _phaseb_e2e_results.json。
import asyncio
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import httpx

from src.core.store import ProjectStore

BASE = "http://localhost:8000"
PROJ = "PhaseB_ResourceAgent_E2E"
RESULTS = "_phaseb_e2e_results.json"

STORY_TEXT = (
    "短篇《山驿灯影》：清末民初，南方山间驿站。年轻的行脚商林晚为寻找失踪的兄长来到驿站，"
    "驿丞陈叔收留了她。林晚随身带着兄长留下的青铜灯笼，夜里灯笼微光中似乎映出山道上的影子。"
    "她在一间存放旧物的库房里发现了兄长的皮袄与一本残破的账册，账册最后一页写着『灯在人在』。"
    "雨夜，林晚提灯上山，在崖边找到了兄长留下的刻字。")
SCENES = [
    {"scene_id": "s01", "title": "初到驿站",
     "summary": "雨夜，行脚商林晚背着行囊抵达山间驿站，驿丞陈叔提着青铜灯笼为她开门。",
     "characters": ["林晚", "陈叔"],
     "shots": [
         {"shot_id": "s01_c01", "scene_id": "s01", "index": 1,
          "description": "雨夜山路上，林晚提着青铜灯笼走近驿站木门",
          "characters": ["林晚"], "shot_size": "medium shot",
          "target_duration_s": 5, "link_mode": "i2v"},
     ]},
    {"scene_id": "s02", "title": "库房旧物",
     "summary": "林晚在库房中发现兄长的皮袄与账册，账册上写着『灯在人在』。",
     "characters": ["林晚"],
     "shots": [
         {"shot_id": "s02_c01", "scene_id": "s02", "index": 1,
          "description": "林晚在昏暗库房里翻开账册，青铜灯笼放在一旁",
          "characters": ["林晚"], "shot_size": "close-up",
          "target_duration_s": 4, "link_mode": "i2v"},
     ]},
]

MAIN_GOAL = (
    "读取当前故事，为主要角色完成生产资源准备。先设计角色，再生成角色身份参考；"
    "为主角准备正式服装 Look；登记一个已有参考图的核心道具；如果适合，为主角和该道具"
    "建立组合参考。最终把可以进入后续分镜生产的资源准备到 production-ready 状态。")

CASE_B_PROMPT = (
    "为主角林晚准备一个夜探造型：连帽斗篷，脸部完全隐藏在阴影里（face hidden in shadow）。"
    "先告诉我你准备怎么做，然后执行。")

CASE_A_PROMPT = (
    "刚才的遮面造型我看过了，我确认就要这个效果，用户坚持要遮面版。"
    "请用 approve_if_pass 完成它，如果审查不通过就如实告诉我结果和原因。")

RESULTS_DATA = {"project": PROJ, "started_at": time.time(), "phases": {}}


def _save():
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump(RESULTS_DATA, f, ensure_ascii=False, indent=1)


def setup():
    """建项目 + 写故事/场景（setup 数据直写 store，与 Golden Run 同惯例）+ profile。"""
    r = httpx.post(f"{BASE}/api/projects/create",
                   params={"name": PROJ, "render_family": "2d_animation",
                           "art_direction": "清末民初南方山驿，水墨晕染的 2D 动画，"
                                            "青灰雨雾色调，灯笼暖光点缀"},
                   timeout=30)
    print("create:", r.status_code, r.text[:120])
    pdir = f"projects/{PROJ}"
    store = ProjectStore(pdir)
    proj = store.load()
    proj.story_text = STORY_TEXT
    from src.core.models import Scene, Shot
    from src.core.models import Character
    proj.characters = [
        Character(name="林晚",
                  description_en="a young traveling merchant woman in her early twenties, "
                                 "short practical black hair, keen determined eyes, wearing "
                                 "a grey-blue travel cloak over simple cotton clothes, carries "
                                 "a bronze lantern"),
        Character(name="陈叔",
                  description_en="an elderly station keeper in his sixties, thin white beard, "
                                 "kind wrinkled face, wearing a dark brown padded robe, slow "
                                 "and steady"),
    ]
    proj.scenes = []
    for sc in SCENES:
        shots = [Shot(**sh) for sh in sc["shots"]]
        proj.scenes.append(Scene(scene_id=sc["scene_id"], title=sc.get("title", ""),
                                 summary=sc["summary"], characters=sc["characters"],
                                 shots=shots))
    proj.next_scene_seq = 3
    store.save(proj)
    # 参考图素材：道具参考图放项目 input/（真机生成一张简单灯笼参考）
    import os
    os.makedirs(f"{pdir}/input", exist_ok=True)
    # 青铜灯笼参考图（真机：Agnes 生成道具参考——setup 数据准备，非被测路径）
    gen = httpx.post(f"{BASE}/api/character/generate-view", json={
        "description": "an antique bronze storm lantern with glass panes, weathered patina, "
                       "warm glow inside, isolated prop reference on a plain white background, "
                       "2d anime cel shaded illustration, clean lines",
        "view": "front", "style": "2D anime cel-shaded", "size_tier": "1K", "ratio": "1:1",
        "project": PROJ}, timeout=300)
    print("prop ref gen:", gen.status_code, str(gen.text)[:120])
    ok_gen = False
    if gen.status_code == 200:
        d = gen.json()
        seg_b64 = d.get("seg_base64") or d.get("raw_base64")
        if seg_b64:
            import base64
            with open(f"{pdir}/input/lantern_ref.png", "wb") as f:
                f.write(base64.b64decode(seg_b64))
            ok_gen = True
            print("prop ref saved ->", f"{pdir}/input/lantern_ref.png")
    RESULTS_DATA["phases"]["setup"] = {"ok": True, "prop_ref": ok_gen,
                                       "story_chars": len(STORY_TEXT)}
    _save()
    print("setup done")


def run_turn(agent_id: str, prompt: str, label: str,
             on_event=None, timeout_s: float = 2400):
    """创建/复用 session，流式消费 turn；返回 (final_result, events)。"""
    r = httpx.post(f"{BASE}/api/project/{PROJ}/agents/sessions",
                   json={"agent_id": agent_id, "scope_type": "project", "scope_id": ""},
                   timeout=30)
    r.raise_for_status()
    sid = r.json()["session_id"]
    print(f"\n[{label}] {agent_id} sid={sid}\n  prompt: {prompt[:100]}...")
    events, final, err = [], None, None
    t0 = time.time()
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
                        if on_event is not None:
                            on_event(ev)
                        if et == "tool.started":
                            print(f"  [{time.time()-t0:6.1f}s] -> {ev.get('name')}")
                        elif et == "tool.completed":
                            print(f"  [{time.time()-t0:6.1f}s] <- {ev.get('name')}"
                                  f" ok={ev.get('ok')} {str(ev.get('summary',''))[:90]}")
    if err:
        print(f"  !! turn.failed: {err}")
    print(f"  wall={time.time()-t0:.0f}s final={'yes' if final else 'no'}")
    RESULTS_DATA["phases"][label] = {
        "session_id": sid, "prompt": prompt, "final": final,
        "events": events, "error": err, "wall_s": round(time.time() - t0, 1)}
    _save()
    return final, events


def tool_events(events, name_part):
    """取某工具的 completed 事件全量回执（从 turn.completed.result.receipts 取更可靠）。"""
    out = []
    for ev in events:
        if ev.get("type") == "tool.completed" and name_part in (ev.get("name") or ""):
            out.append(ev)
    return out


def receipts_of_tool(final, name):
    out = []
    for r in (final or {}).get("receipts") or []:
        if r.get("operation") == name:
            out.append(r)
    return out


def main():
    final, events = run_turn("character_designer", MAIN_GOAL, "main")
    rs = (final or {}).get("receipts") or []
    ops = [r.get("operation") for r in rs]
    print("\n=== §22 判定（从回执）===")
    checks = {
        "read Story": any(o in ops for o in ("story.list", "story.read")),
        "read VisualProfile": "project.visual_profile.read" in ops,
        "inventory": "resource.inventory" in ops,
        "character.create/update": any(o in ops for o in ("character.create", "character.update")),
        "generate views": "character.generate_views" in ops,
        "clothing": "resource.clothing.upsert" in ops,
        "look.prepare": "resource.look.prepare" in ops,
        "prop.register": "resource.prop.register" in ops,
        "composition.prepare": "resource.composition.prepare" in ops,
    }
    for k, v in checks.items():
        print(f"  {'PASS' if v else 'MISS'}: {k}")
    looks = receipts_of_tool(final, "resource.look.prepare")
    for lk in looks:
        print(f"  look: id={lk.get('look_id')} gen={lk.get('generation', {}).get('status')}"
              f" review={lk.get('review', {}).get('overall')}"
              f" approval={lk.get('approval')}")
    verified_ops = [r.get("operation") for r in rs
                    if r.get("ok") and r.get("verified") and not r.get("unchanged")]
    print(f"  verified ops: {verified_ops}")
    RESULTS_DATA["phases"]["main_verdict"] = {"checks": checks,
                                              "verified_ops": verified_ops}
    _save()


def caseB():
    """§23.B：遮面冲突——期望 reference_conflict 拦截，不连续重试。"""
    final, events = run_turn("character_designer", CASE_B_PROMPT, "caseB")
    lks = receipts_of_tool(final, "resource.look.prepare")
    conflicts = [lk for lk in lks if lk.get("error") == "reference_conflict"]
    print("\n=== §23.B 判定 ===")
    print(f"  look.prepare 调用数={len(lks)}，conflict 拦截={len(conflicts)}")
    if conflicts:
        c = conflicts[0]
        print(f"  conflicts: {json.dumps(c.get('conflicts'), ensure_ascii=False)}")
        print(f"  零写入: {c.get('changed_resources') == []}")
    RESULTS_DATA["phases"]["caseB_verdict"] = {
        "n_look_calls": len(lks), "n_conflict": len(conflicts)}
    _save()


def caseA():
    """§23.A：确认后 approve_if_pass → 审查 needs_fix 时不批准、不假成功。"""
    final, events = run_turn("character_designer", CASE_A_PROMPT, "caseA")
    lks = receipts_of_tool(final, "resource.look.prepare")
    print("\n=== §23.A 判定 ===")
    for lk in lks:
        appr = lk.get("approval") or {}
        rev = lk.get("review") or {}
        print(f"  look={lk.get('look_id')} review={rev.get('overall')}"
              f" approval_attempted={appr.get('attempted')} approval={appr.get('status')}")
    RESULTS_DATA["phases"]["caseA_verdict"] = {
        "looks": [{"look_id": lk.get("look_id"), "review": (lk.get("review") or {}).get("overall"),
                   "approval": (lk.get("approval") or {}).get("status")} for lk in lks]}
    _save()


def caseC():
    """§23.C：OCC stale——agent 读后外部改同一 document revision，写必须 stale 且 agent 重读。"""
    state = {"fired": False, "external_rev": None}

    def on_event(ev):
        # agent 第一次 resource.inventory 完成后，外部改 resources（真实 HTTP PATCH）
        if (not state["fired"] and ev.get("type") == "tool.completed"
                and ev.get("name") == "resource.inventory"):
            state["fired"] = True
            cur = httpx.get(f"{BASE}/api/project/{PROJ}/clothings", timeout=30).json()
            clothing = cur.get("clothings") or []
            if clothing:
                cid = clothing[0]["id"]
                rr = httpx.patch(
                    f"{BASE}/api/project/{PROJ}/clothings/{cid}",
                    json={"description": f"frayed cuffs external edit @ {time.time():.0f}",
                          "expected_revision": clothing[0]["revision"]}, timeout=30)
                print(f"  >>> 外部写入触发：clothing {cid} PATCH {rr.status_code}"
                      f"（agent 后续写必须 stale）")
            else:
                print("  >>> 无 clothing 可外部改（caseC 需先 main 建好资源）")

    final, events = run_turn(
        "character_designer",
        "把项目里现有的服装资源做一次小改动：把它的描述里加上『袖口磨损』四个字的语义"
        "（英文加 frayed cuffs），然后用 resource.inventory 核对结果。",
        "caseC", on_event=on_event)
    ups = receipts_of_tool(final, "resource.clothing.upsert")
    print("\n=== §23.C 判定 ===")
    for u in ups:
        print(f"  upsert: ok={u.get('ok')} stale={u.get('stale')} error={u.get('error')}")
    stales = [u for u in ups if u.get("stale")]
    print(f"  stale 拦截={len(stales)}（应≥1），外部写已触发={state['fired']}")
    RESULTS_DATA["phases"]["caseC_verdict"] = {
        "external_fired": state["fired"], "n_upsert": len(ups),
        "stales": [{"error": u.get("error")} for u in stales]}
    _save()


def producer():
    """§24：Producer 真实委派 → Resource Agent → changed_refs → Room。"""
    final, events = run_turn(
        "producer",
        "当前故事已经基本完成，请把主要角色和必要的生产资源准备好，但不要改故事和分镜。",
        "producer")
    rs = (final or {}).get("receipts") or []
    print("\n=== §24 判定 ===")
    for r in rs:
        cr = r.get("changed_refs") or {}
        print(f"  agent={r.get('agent')} status={r.get('status')} verified={r.get('verified')}")
        print(f"  changed_refs: {json.dumps(cr, ensure_ascii=False)}")
        assert cr.get("scenes") == [] and cr.get("shots") == [], "未改故事/分镜"
    res_ops = [r for r in rs if r.get("agent") == "character_designer"]
    print(f"  Resource Agent 委派数={len(res_ops)}，资源键非空="
          f"{any(any((r.get('changed_refs') or {}).get(k) for k in ('looks','props','clothings','compositions','characters')) for r in res_ops)}")
    RESULTS_DATA["phases"]["producer_verdict"] = {
        "delegations": [{"agent": r.get("agent"), "status": r.get("status"),
                         "verified": r.get("verified"),
                         "changed_refs": r.get("changed_refs")} for r in rs]}
    _save()
    # bridge messages 持久化核对
    import pathlib
    bm = pathlib.Path(f"projects/{PROJ}/output/producer/bridge_messages.jsonl")
    if bm.exists():
        rows = [json.loads(x) for x in bm.read_text(encoding="utf-8").splitlines() if x.strip()]
        print(f"  bridge_messages: {len(rows)} 条")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "main"
    globals()[cmd]()
