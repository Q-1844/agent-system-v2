# _37_fable/_37_setup_scene_system.py — 场景系统接线（全走正式 REST）。
# 1) 空地水塘 LocationView（段 B 用；幂等）→ generate/review/approve 清图循环
# 2) s09/s10 SceneInstance（雨林 Location 复用）+ 分段状态 PATCH
# 3) 每实例 SIV（instance+view）→ 清图循环到 approved
# 4) 9 镜 binding 创建 + 刷新（scene_binding + 狐狸 look）
# 幂等：各实体按存在性跳过。限流：图片连发退避（36.md §3.4 间隔 ≥80s 由循环 sleep 承担）。
import io
import re
import sys
import time
from pathlib import Path

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = "http://127.0.0.1:8000"
PROJ = "Fable_Pilot_Fox_and_Grapes_V1"
from _37_story import (SEGMENTS, FOX_LOOK, LOCATION_ID, TRAIL_VIEW, SHOTS,  # noqa: E402
                       CHAIN_ROOT_REF)

CLEARING_LABEL = "rainforest clearing with pond"


def api(m, p, timeout=300, **kw):
    r = httpx.request(m, f"{BASE}{p}", timeout=timeout, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{p} {r.status_code}: {r.text[:350]}")
    return r.json()


def cur(e):
    m = re.search(r'"current_.*?revision"\s*:\s*"([0-9a-f]+)"', e)
    return m.group(1) if m else None


def doc_rev():
    ov = api("GET", f"/api/project/{PROJ}/scene-system/overview")
    return ov.get("document_revision") or ov.get("revision")


def res_rev():
    # resources 文档修订：/compositions 响应带 document_revision（resources 文档级 OCC）
    return api("GET", f"/api/project/{PROJ}/compositions",
               timeout=15).get("document_revision") or ""


def try_approve(path, label):
    """已有 review pass 的候选直接补 approve（幂等续跑，不再烧图）。"""
    for _att in range(3):
        d = api("GET", f"/api/project/{PROJ}/{path}")
        rev = d.get("sciv_revision") or d.get("view_revision") or d.get("revision")
        try:
            api("POST", f"/api/project/{PROJ}/{path}/approve",
                json={"candidate_index": 0, "expected_revision": rev,
                      "expected_resources_document_revision": res_rev()})
            print(f"[{label}] APPROVED (补批)", flush=True)
            return True
        except RuntimeError as e:
            c = cur(str(e))
            if c:
                continue
            print(f"[{label}] approve err: {str(e)[:180]}", flush=True)
            return False
    return False


def clean_loop(kind, eid, label, rounds=24):
    """generate/review/approve 清图循环（view 与 sciv 通用）。"""
    path = (f"scene-system/views/{eid}" if kind == "view"
            else f"scene-system/scene-instance-views/{eid}")
    d = api("GET", f"/api/project/{PROJ}/{path}")
    # 断点续跑：已有生成+审查通过但未批准（上轮 approve 崩溃）→ 直接补批
    if str(d.get("approval_status") or "").lower() != "approved" \
            and str(d.get("generation_status") or "") == "ready" \
            and d.get("active_generation_id"):
        gv = api("GET", f"/api/project/{PROJ}/{path}/reviews", timeout=30)
        recs = gv.get("review_records") or gv.get("candidate_reviews") or []
        active = str(d.get("active_generation_id") or "")
        pass_hit = False
        for r in recs:
            if str(r.get("generation_id") or "") != active:
                continue
            for c in (r.get("candidate_reviews") or []):
                if str(c.get("overall") or "") == "pass":
                    pass_hit = True
        if pass_hit:
            if try_approve(path, label):
                return True
    for round_ in range(rounds):
        if round_ > 0:
            time.sleep(45)   # 图片限流退避（36.md §3.4）
        d = api("GET", f"/api/project/{PROJ}/{path}")
        rev = d.get("sciv_revision") or d.get("view_revision") or d.get("revision")
        if str(d.get("approval_status") or "").lower() == "approved":
            print(f"[{label}] already approved", flush=True)
            return True
        try:
            api("POST", f"/api/project/{PROJ}/{path}/generate",
                json={"expected_revision": rev, "candidate_count": 1})
            print(f"[{label}] r{round_} gen committed", flush=True)
        except RuntimeError as e:
            c = cur(str(e))
            if c:
                rev = c
                continue
            print(f"[{label}] gen err: {str(e)[:130]}", flush=True)
            time.sleep(45)
            continue
        time.sleep(5)
        try:
            rv = api("POST", f"/api/project/{PROJ}/{path}/review")
            recs = (rv.get("candidate_reviews") or [{}])
            ov = recs[0].get("overall")
            print(f"[{label}] r{round_} review: {ov}", flush=True)
            if ov != "pass":
                continue
        except RuntimeError as e:
            print(f"[{label}] review err: {str(e)[:130]}", flush=True)
            continue
        if try_approve(path, label):
            return True
    return False


def main():
    # ---- 1) 空地水塘 LocationView（幂等）----
    views = api("GET", f"/api/project/{PROJ}/scene-system/views")
    vlist = views.get("views") if isinstance(views, dict) else views
    clearing = next((v for v in (vlist or [])
                     if v.get("location_id") == LOCATION_ID
                     and CLEARING_LABEL in str(v.get("label", ""))), None)
    if clearing and clearing.get("approval_status") == "approved":
        view_b = clearing["view_id"]
        print("clearing view exists approved:", view_b)
    else:
        if clearing:
            view_b = clearing["view_id"]
            print("clearing view exists (unapproved):", view_b)
        else:
            api("POST", f"/api/project/{PROJ}/scene-system/views", json={
                "location_id": LOCATION_ID, "label": CLEARING_LABEL,
                "visible_anchor_ids": [],
                "expected_document_revision": doc_rev()})
            time.sleep(1)
            vs = api("GET", f"/api/project/{PROJ}/scene-system/views")
            vlist2 = vs.get("views") if isinstance(vs, dict) else vs
            clearing = next((v for v in (vlist2 or [])
                             if v.get("location_id") == LOCATION_ID
                             and CLEARING_LABEL in str(v.get("label", ""))), None)
            view_b = clearing["view_id"] if clearing else None
            print("clearing view created:", view_b)
        if not clean_loop("view", view_b, "clearing-view"):
            print("FATAL: clearing view 未过审"); return

    # ---- 2) SceneInstance + 状态 ----
    insts = api("GET", f"/api/project/{PROJ}/scene-system/scene-instances")
    lst = insts.get("scene_instances") if isinstance(insts, dict) else insts
    by_scene = {i.get("story_scene_id"): i for i in (lst or [])}
    view_of_seg = {"A": TRAIL_VIEW, "B": view_b}
    inst_of_seg = {}
    for seg in SEGMENTS:
        sid = "s09" if seg["key"] == "A" else "s10"
        inst = by_scene.get(sid)
        if not inst:
            api("POST", f"/api/project/{PROJ}/scene-system/scene-instances", json={
                "story_scene_id": sid, "location_id": LOCATION_ID,
                "expected_document_revision": doc_rev()})
            time.sleep(1)
            insts = api("GET", f"/api/project/{PROJ}/scene-system/scene-instances")
            lst = insts.get("scene_instances") if isinstance(insts, dict) else insts
            inst = next(i for i in (lst or []) if i.get("story_scene_id") == sid)
            print(f"[{sid}] instance created:", inst.get("scene_instance_id"))
        else:
            print(f"[{sid}] instance exists:", inst.get("scene_instance_id"))
        iid = inst["scene_instance_id"]
        if (inst.get("location_id") or "") != LOCATION_ID:
            api("PATCH", f"/api/project/{PROJ}/scene-system/scene-instances/{iid}/location",
                json={"location_id": LOCATION_ID,
                      "expected_revision": inst.get("revision") or doc_rev()})
            print(f"[{sid}] location rebound")
        fresh = api("GET", f"/api/project/{PROJ}/scene-system/scene-instances/{iid}")
        st = dict(seg["state"], expected_revision=fresh.get("revision"))
        api("PATCH", f"/api/project/{PROJ}/scene-system/scene-instances/{iid}/state",
            json=st)
        print(f"[{sid}] state patched")
        inst_of_seg[seg["key"]] = (iid, view_of_seg[seg["key"]])

    # ---- 3) SIV 清图循环（每实例 1 个，幂等找已批准）----
    sciv_of_seg = {}
    scivs = api("GET", f"/api/project/{PROJ}/scene-system/scene-instance-views")
    slist = scivs.get("scene_instance_views") if isinstance(scivs, dict) else scivs
    for seg_key, (iid, vid) in inst_of_seg.items():
        mine = [s for s in (slist or [])
                if s.get("scene_instance_id") == iid
                and s.get("location_view_id") == vid]
        approved = next((s for s in mine
                         if s.get("approval_status") == "approved"), None)
        if approved:
            sciv_of_seg[seg_key] = approved["scene_instance_view_id"]
            print(f"[seg {seg_key}] SCIV approved:", approved["scene_instance_view_id"])
            continue
        if mine:
            sciv_id = mine[0]["scene_instance_view_id"]
            print(f"[seg {seg_key}] SCIV exists (unapproved):", sciv_id)
        else:
            sciv_id = None
            try:
                api("POST", f"/api/project/{PROJ}/scene-system/scene-instance-views",
                    json={"scene_instance_id": iid, "location_view_id": vid,
                          "expected_document_revision": doc_rev()})
                time.sleep(1)
                ss = api("GET", f"/api/project/{PROJ}/scene-system/scene-instance-views")
                slist2 = ss.get("scene_instance_views") if isinstance(ss, dict) else ss
                mine2 = [s for s in (slist2 or [])
                         if s.get("scene_instance_id") == iid
                         and s.get("location_view_id") == vid]
                sciv_id = mine2[0]["scene_instance_view_id"] if mine2 else None
                print(f"[seg {seg_key}] SCIV created:", sciv_id)
            except RuntimeError as e:
                m2 = re.search(r'"scene_instance_view_id"\s*:\s*"(sciv_[0-9a-f]+)"', str(e))
                if m2:
                    sciv_id = m2.group(1)
                    print(f"[seg {seg_key}] SCIV exists (from 409):", sciv_id)
                else:
                    raise
            if not sciv_id:
                print(f"FATAL: seg {seg_key} SCIV id 未取得"); return
        if not clean_loop("sciv", sciv_id, f"seg-{seg_key}-siv"):
            print(f"FATAL: seg {seg_key} SIV 未过审"); return
        sciv_of_seg[seg_key] = sciv_id

    # ---- 4) binding 创建 + 刷新 ----
    seg_counter = {"A": 0, "B": 0}
    for spec in SHOTS:
        seg_counter[spec["segment"]] += 1
        sid = f"{'s09' if spec['segment'] == 'A' else 's10'}_c{seg_counter[spec['segment']]:02d}"
        iid, vid = inst_of_seg[spec["segment"]]
        payload = {"shot_id": sid, "scene_id": sid.rsplit("_c", 1)[0],
                   "scene_binding": {"scene_instance_id": iid, "location_view_id": vid},
                   "character_bindings": [{"character_name": "狐狸", "look_id": FOX_LOOK}],
                   "prop_bindings": [], "production_notes": ""}
        try:
            api("POST", f"/api/project/{PROJ}/shot-bindings", json=payload)
            print(f"[{sid}] binding created")
        except RuntimeError as e:
            if "已存在" in str(e) or "already" in str(e).lower() or "409" in str(e):
                print(f"[{sid}] binding exists")
            else:
                raise
        # OCC：先取当前 binding revision（36.md §1.2.5 口径）
        b = api("GET", f"/api/project/{PROJ}/shot-bindings/{sid}")
        brev = b.get("binding_revision") or (b.get("binding") or {}).get("binding_revision") or ""
        r = api("POST", f"/api/project/{PROJ}/shot-bindings/{sid}/refresh",
                json={"expected_binding_revision": brev})
        print(f"[{sid}] binding refresh ->", str(r.get("status") or r)[:50])
    print("done: 场景系统接线完成", sciv_of_seg)


if __name__ == "__main__":
    main()
