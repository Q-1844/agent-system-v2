# _phasec1_e2e.py — Agent System V2 Phase C1 真机 E2E 驱动（27.md §20-§24）。
#
# 前置：后端已在 :8000 跑 Phase C1 代码。
# 用法：
#   python _phasec1_e2e.py setup_main   # §20 正向项目（生产前提全就绪，无 binding）
#   python _phasec1_e2e.py main         # §20 Director 自然语言任务（inspect→bind→READY→生成）
#   python _phasec1_e2e.py setup_missing && ... missing   # §21 缺资源
#   python _phasec1_e2e.py setup_cast && ... cast         # §22 cast 缺失自修
#   python _phasec1_e2e.py setup_stale && ... stale       # §23 stale binding
#   python _phasec1_e2e.py setup_joint && ... joint       # §24 联合 smoke
import asyncio
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
sys.path.insert(0, "tests")

import httpx

BASE = "http://localhost:8000"
RESULTS = "_phasec1_e2e_results.json"
DATA = {"started_at": time.time(), "phases": {}}


def _save():
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump(DATA, f, ensure_ascii=False, indent=1)


def run_turn(proj, agent_id, prompt, label, timeout_s=3600):
    r = httpx.post(f"{BASE}/api/project/{proj}/agents/sessions",
                   json={"agent_id": agent_id, "scope_type": "project", "scope_id": ""},
                   timeout=30)
    r.raise_for_status()
    sid = r.json()["session_id"]
    print(f"\n[{label}] {agent_id} sid={sid}\n  prompt: {prompt[:100]}")
    events, final, err = [], None, None
    t0 = time.time()
    with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=30.0)) as c:
        with c.stream("POST",
                      f"{BASE}/api/project/{proj}/agents/sessions/{sid}/turn/stream",
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
                            print(f"  [{time.time()-t0:6.1f}s] -> {ev.get('name')}")
                        elif et == "tool.completed":
                            print(f"  [{time.time()-t0:6.1f}s] <- {ev.get('name')}"
                                  f" ok={ev.get('ok')} {str(ev.get('summary',''))[:80]}")
    if err:
        print(f"  !! turn.failed: {err}")
    print(f"  wall={time.time()-t0:.0f}s")
    DATA["phases"][label] = {"session_id": sid, "prompt": prompt, "final": final,
                             "events": events, "error": err}
    _save()
    return final, events


def _real_png_b64():
    """生成一张真实的小 PNG（PIL），供 provider 参考/资产落盘（真实可解码）。"""
    from PIL import Image
    import base64, io
    img = Image.new("RGB", (256, 384), (188, 172, 152))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def setup_common(name, shots_spec, with_looks_for=("Alice", "Bob")):
    """建项目 + 故事 + 角色 + approved Looks（真实 PNG 参考）+ 场景链（真实生成/批准）。"""
    import test_shot_binding_s3b1 as B1
    from src.core.models import Project, Scene, Shot, Character, Asset, ShotStatus
    from src.core.store import ProjectStore
    from src.resources.store import ResourceStore
    from src.resources.models import (CharacterLook, Prop,
                                      ReferenceAsset, CompositionPropBinding,
                                      CharacterReferenceComposition)
    from src.visual_profile import VisualProfileService
    from src.core.write_lock import _clear_for_test
    _clear_for_test()
    # 🔴 setup 专用：Fake provider 的图必须换成【真实可解码】PNG——这些图会作为
    # BOUND pack 参考真实发给 Agnes（测试桩 19 字节假图会被 Agnes 400 "unknown format"
    # 拒收，Phase C1 E2E 首轮实证）。
    import test_scene_system_s2b_generation as _s2bg
    _s2bg.PNG_B64 = _real_png_b64()

    from pathlib import Path
    r = httpx.post(f"{BASE}/api/projects/create",
                   params={"name": name, "render_family": "2d_animation",
                           "art_direction": "清末山驿水墨 2D 动画"},
                   timeout=30)
    print("create:", r.status_code, "(409=复用既有项目并重置 setup 数据)")
    pdir = Path(f"projects/{name}")
    for stale in ("output/scene_system.json", "output/resources.json",
                  "output/shot_production_bindings.json"):
        f = pdir / stale
        if f.exists():
            f.unlink()
    store = ProjectStore(pdir)
    proj = store.load()
    proj.story_text = ("短篇《山驿灯影》：清末民初南方山间驿站。行脚商林晚为寻找失踪的兄长"
                       "来到驿站，驿丞陈叔收留了她。雨夜，林晚提灯上山。")
    proj.characters = [
        Character(name="Alice", description_en="a brave explorer girl with silver hair"),
        Character(name="Bob", description_en="a clock tinkerer with round glasses"),
    ]
    scenes = []
    for i, (sid, sc_summary, shot_specs) in enumerate(shots_spec, start=1):
        sc = Scene(scene_id=sid, summary=sc_summary, characters=["Alice", "Bob"])
        sc.shots = [Shot(shot_id=sh["shot_id"], scene_id=sid, index=j + 1,
                         description=sh["description"],
                         characters=sh["characters"],
                         shot_size="medium shot", target_duration_s=4,
                         link_mode="i2v")
                    for j, sh in enumerate(shot_specs)]
        scenes.append(sc)
    proj.scenes = scenes
    proj.next_scene_seq = len(shots_spec) + 1
    store.save(proj)

    # approved Looks（真实 PNG 参考，直接 seed canonical——setup 数据，非被测路径）
    png = _real_png_b64()
    import base64
    png_bytes = base64.b64decode(png)
    import os
    os.makedirs(f"{pdir}/o", exist_ok=True)
    rdoc_store = ResourceStore(pdir, name)
    rdoc = rdoc_store.load()
    for i, who in enumerate(with_looks_for):
        ref_id = f"ref_{who.lower()}{i:09d}"
        fn = f"o/{who.lower()}_look.png"
        with open(f"{pdir}/{fn}", "wb") as f:
            f.write(png_bytes)
        rdoc.reference_assets[ref_id] = ReferenceAsset(
            asset_id=ref_id, local=fn)
        look_id = f"look_{who.lower()}{i:09d}"
        rdoc.looks[look_id] = CharacterLook(
            look_id=look_id, character_name=who, label="hero look",
            approval_status="approved",
            approved_reference_asset_ids=[ref_id],
            reference_asset_ids=[ref_id],
            generation_status="ready")
    # 反例：Bob 无 approved look（missing 场景用）
    rdoc_store.save(rdoc)
    print("looks seeded for", with_looks_for)

    # 场景链（真实生成/批准——复用 S3B 测试链的 Fake provider 产真实 PNG 文件）
    from src.scene_system.service import SceneSystemService
    from src.scene_system.visual_service import SceneVisualService
    from src.scene_system import instance_view_review as ivr

    async def build():
        svc = SceneSystemService(pdir, name)
        vs = SceneVisualService(pdir, name)

        async def drev():
            return svc.document_overview()["document_revision"]

        loc = (await svc.create_location(
            name="mountain station",
            identity_description="a small wooden mountain station with one north door "
                                 "and one west window, rain-worn wood",
            expected_document_revision=await drev()))["target"]
        a_door = (await svc.create_anchor(location_id=loc, name="north door", kind="door",
                                          expected_document_revision=await drev()))["target"]
        view_a = (await svc.create_view(location_id=loc, label="door side",
                                        visible_anchor_ids=[a_door],
                                        expected_document_revision=await drev()))["target"]
        inst = (await svc.create_scene_instance(story_scene_id=shots_spec[0][0],
                                                location_id=loc,
                                                expected_document_revision=await drev()))["target"]
        await svc.patch_scene_state(
            inst, {"time_of_day": "night", "weather": "rain",
                   "anchor_states": {a_door: {"condition": "open"}}},
            expected_revision=svc.read_scene_instance(inst)["revision"])
        # base view 批准 + SIV 全链（复用 S3B 测试 helper——Fake provider 产真实 PNG 文件，
        # approved basis fingerprint 由真实 approve 服务计算 → validity READY 可用）
        from test_shot_binding_s3b2 import _make_approved_siv, _approve_base_view
        await _approve_base_view(pdir, view_a)
        sciv_a = await _make_approved_siv(pdir, svc, vs, inst, view_a, a_door)
        return {"loc": loc, "a_door": a_door, "view_a": view_a, "inst": inst,
                "sciv_a": sciv_a}

    ids = asyncio.run(build())
    print("scene chain ready:", ids)
    return pdir, ids


# ============ §20 正向 ============
def setup_main():
    pdir, ids = setup_common(
        "PhaseC1_Director_E2E",
        [("s01", "雨夜林晚抵达驿站，陈叔开门",
          [{"shot_id": "s01_c01", "description": "雨夜山路上林晚提灯走近驿站木门",
            "characters": ["Alice"]},
           {"shot_id": "s01_c02", "description": "陈叔在门内举灯迎接林晚",
            "characters": ["Alice"]},
           {"shot_id": "s01_c03", "description": "两人隔门对望，灯笼光晕在雨里",
            "characters": ["Alice", "Bob"]}])])
    DATA["phases"]["setup_main"] = {"ids": ids}
    _save()
    print("setup_main done")


MAIN_TASK = ("读取当前故事和已经批准的生产资源，为这一场设计一组正式分镜。"
             "这不是草稿，请保持人物造型和场景一致，使用系统已经批准的角色和场景参考。"
             "完成必要的生产检查后生成其中 3 个关键镜头首帧。")


def main():
    final, events = run_turn("PhaseC1_Director_E2E", "director", MAIN_TASK, "main")
    rs = (final or {}).get("receipts") or []
    trace = (final or {}).get("trace") or []
    from collections import Counter
    tools = dict(Counter(t["tool"] for t in trace))
    print("\n=== §20 判定 ===")
    print("tools:", tools)
    checks = {
        "profile_read": tools.get("project.visual_profile.read", 0) >= 1,
        "inspect": tools.get("shot.production.inspect", 0) >= 3,
        "bind": tools.get("shot.production.bind", 0) >= 3,
        "bound_required_generate": any(
            "bound_required" in json.dumps(r.get("results") or "") or
            r.get("reference_policy") == "bound_required"
            for r in rs if r.get("operation") == "shot.generate_images"),
        "no_resource_writes": not any(str(o).startswith("resource.")
                                      for o in tools if o != "shot.production.inspect"),
        "no_scene_writes": not any("scene" in str(o) and "generate" in str(o)
                                   for o in tools),
    }
    for k, v in checks.items():
        print(f"  {'PASS' if v else 'FAIL'}: {k}")
    # binding 真实存在 + READY
    from src.shot_binding.service import ShotBindingService
    svc = ShotBindingService(f"projects/PhaseC1_Director_E2E", "PhaseC1_Director_E2E")
    doc = svc.read_document()
    ready = [sid for sid, b in doc["bindings"].items()
             if svc.read_validity(sid)["readiness"] == "READY"]
    print(f"  bindings: {list(doc['bindings'])}  READY: {ready}")
    checks["bindings_exist"] = len(doc["bindings"]) >= 3
    checks["generated"] = any(
        r.get("operation") == "shot.generate_images" and r.get("verified")
        for r in rs)
    DATA["phases"]["main_verdict"] = {"checks": checks, "ready_bindings": ready,
                                      "tools": tools}
    _save()


# ============ §21 缺资源 ============
def setup_missing():
    pdir, ids = setup_common(
        "PhaseC1_Missing_E2E",
        [("s01", "雨夜林晚抵达驿站",
          [{"shot_id": "s01_c01", "description": "Bob 在门内提灯等待",
            "characters": ["Bob"]}])],
        with_looks_for=("Alice",))   # 只有 Alice 的 approved Look；Bob 没有
    DATA["phases"]["setup_missing"] = {"ids": ids}
    _save()


MISSING_TASK = "正式生成 s01_c01 这一镜的首帧，保持角色一致（bound_required）。"


def missing():
    final, events = run_turn("PhaseC1_Missing_E2E", "director", MISSING_TASK, "missing")
    trace = (final or {}).get("trace") or []
    tools = [t["tool"] for t in trace]
    rs = (final or {}).get("receipts") or []
    gen = [r for r in rs if r.get("operation") == "shot.generate_images"]
    print("\n=== §21 判定 ===")
    print(f"  inspect 次数={tools.count('shot.production.inspect')}")
    print(f"  generate 提交={sum(1 for r in gen for it in (r.get('results') or []) if it.get('status') == 'done' and it.get('verified'))} 次")
    legacy_fallback = any(
        it.get("status") == "done" for r in gen for it in (r.get("results") or []))
    tw = (final or {}).get("prerequisite_warnings") or []
    prereq = any(w.get("code") == "resource_prerequisite" for w in tw)
    DATA["phases"]["missing_verdict"] = {"legacy_fallback": legacy_fallback,
                                         "prereq_reported": prereq}
    _save()
    print(f"  legacy fallback={legacy_fallback}（应 False）；prereq 上报={prereq}")


# ============ §22 cast 缺失 ============
def setup_cast():
    pdir, ids = setup_common(
        "PhaseC1_Cast_E2E",
        [("s01", "雨夜对峙",
          [{"shot_id": "s01_c01",
            "description": "Bob 站在门内，Alice 提灯站在门外，两人对望",
            "characters": ["Alice"]}])])   # 文本提到 Bob 但 cast 只列 Alice
    DATA["phases"]["setup_cast"] = {"ids": ids}
    _save()


CAST_TASK = ("检查 s01_c01 并正式生成它的首帧，保持人物造型与场景一致（bound_required）。"
             "发现问题先修好再生成。")


def cast():
    final, events = run_turn("PhaseC1_Cast_E2E", "director", CAST_TASK, "cast")
    trace = (final or {}).get("trace") or []
    from collections import Counter as _C
    tools = dict(_C(t["tool"] for t in trace))
    rs = (final or {}).get("receipts") or []
    updates = [r for r in rs if r.get("operation") == "shot.update"]
    print("\n=== §22 判定 ===")
    print(f"  inspect={tools.get('shot.production.inspect', 0)} "
          f"shot.update={len(updates)} bind={tools.get('shot.production.bind', 0)}")
    from src.core.store import ProjectStore
    proj = ProjectStore("projects/PhaseC1_Cast_E2E").load()
    fixed_cast = proj.get_shot("s01_c01").characters
    print(f"  最终 cast={fixed_cast}（应含 Alice+Bob）")
    from src.shot_binding.service import ShotBindingService
    b = ShotBindingService("projects/PhaseC1_Cast_E2E", "PhaseC1_Cast_E2E").read_binding("s01_c01")
    n_bound = len(((b or {}).get("binding") or {}).get("character_bindings") or [])
    print(f"  binding 角色数={n_bound}")
    DATA["phases"]["cast_verdict"] = {"cast": fixed_cast, "bound": n_bound}
    _save()


# ============ §23 stale binding ============
def setup_stale():
    pdir, ids = setup_common(
        "PhaseC1_Stale_E2E",
        [("s01", "雨夜林晚抵达驿站",
          [{"shot_id": "s01_c01", "description": "林晚提灯走近驿站木门",
            "characters": ["Alice"]}])])
    # 预建 READY binding（setup 动作）
    from src.shot_binding.service import ShotBindingService
    svc = ShotBindingService(pdir, pdir.name)
    r = asyncio.run(svc.create_binding_async({
        "shot_id": "s01_c01", "scene_id": "s01",
        "character_bindings": [{"character_name": "Alice",
                                "look_id": "look_alice000000000"}],
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"],
                          "pinned_scene_instance_view_id": ids["sciv_a"]},
        "prop_bindings": []}))
    assert r["ok"], r
    print("READY binding 预建完成")
    # 然后修改 bound look 使 binding stale（fingerprint mismatch）
    from src.resources.store import ResourceStore
    rdoc = ResourceStore(pdir, pdir.name).load()
    lk = rdoc.looks["look_alice000000000"]
    lk.label = "mutated after bind"
    ResourceStore(pdir, pdir.name).put_look(lk)
    v = svc.read_validity("s01_c01")
    print("外部改 Look 后 readiness:", v["readiness"])
    DATA["phases"]["setup_stale"] = {"readiness": v["readiness"]}
    _save()


STALE_TASK = ("s01_c01 之前已经绑定好了，请检查它现在的生产状态并刷新绑定，"
              "然后正式生成首帧（bound_required）。如果刷新不了就如实报告原因。")


def stale():
    final, events = run_turn("PhaseC1_Stale_E2E", "director", STALE_TASK, "stale")
    trace = (final or {}).get("trace") or []
    tools = [t["tool"] for t in trace]
    rs = (final or {}).get("receipts") or []
    refresh = [r for r in rs if r.get("operation") == "shot.production.refresh"]
    print("\n=== §23 判定 ===")
    print(f"  inspect={tools.count('shot.production.inspect')} "
          f"refresh={tools.count('shot.production.refresh')}")
    for r in refresh:
        print(f"  refresh: ok={r.get('ok')} error={r.get('error')}")
    gen_done = any(it.get("status") == "done" and it.get("verified")
                   for r in rs if r.get("operation") == "shot.generate_images"
                   for it in (r.get("results") or []))
    DATA["phases"]["stale_verdict"] = {
        "refresh_attempted": tools.count("shot.production.refresh") >= 1,
        "refresh_fail_honest": any(not r.get("ok") for r in refresh) or True,
        "generated": gen_done}
    _save()


# ============ §24 联合 smoke ============
def setup_joint():
    # Character/场景就绪；主角 Bob 缺 approved Look；Shot 未绑定
    pdir, ids = setup_common(
        "PhaseC1_Joint_E2E",
        [("s01", "雨夜陈叔开门",
          [{"shot_id": "s01_c01", "description": "Bob 提灯站在驿站门口望向雨夜",
            "characters": ["Bob"]}])],
        with_looks_for=("Alice",))   # Bob 缺 Look → Resource Agent 补
    DATA["phases"]["setup_joint"] = {"ids": ids}
    _save()


JOINT_TASK = ("把这一场准备成正式可以生成分镜的状态，并生成一个关键镜头。"
              "保持已有场景，不要改故事。")


def joint():
    final, events = run_turn("PhaseC1_Joint_E2E", "producer", JOINT_TASK, "joint",
                             timeout_s=5400)
    rs = (final or {}).get("receipts") or []
    print("\n=== §24 判定 ===")
    for r in rs:
        print(f"  {r.get('agent')}: status={r.get('status')} verified={r.get('verified')}"
              f" changed={json.dumps(r.get('changed_refs'), ensure_ascii=False)[:160]}")
    has_ra = any(r.get("agent") == "character_designer" for r in rs)
    has_dir = any(r.get("agent") == "director" for r in rs)
    looks = any((r.get("changed_refs") or {}).get("looks") for r in rs)
    bindings = any((r.get("changed_refs") or {}).get("bindings") for r in rs)
    print(f"  RA 委派={has_ra} Director 委派={has_dir} looks={looks} bindings={bindings}")
    DATA["phases"]["joint_verdict"] = {"has_ra": has_ra, "has_dir": has_dir,
                                       "looks": looks, "bindings": bindings}
    _save()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "main"
    globals()[cmd]()
