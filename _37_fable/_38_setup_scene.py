# _37_fable/_38_setup_scene.py — 《狐狸进村》场景系统接线（全走正式 REST；_38_story 为事实源）。
# 1) 新建乡村 Location（identity_description 一开始就带 PURELY EMPTY 禁 fauna 条款——38.md §3.1）
# 2) 村口 LocationView → generate/review/approve 清图循环
# 3) s11（雨林+trail view）/ s12（乡村+村口 view）SceneInstance + 分段状态 PATCH
# 4) 每实例 SIV → 清图循环到 approved
# 5) 9 镜 binding 创建 + 刷新（scene_binding + 狐狸 look）
# 幂等：各实体按存在性跳过；限流退避同 _37_setup_scene_system.py。
import io
import json
import re
import sys
import time
from pathlib import Path

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = "http://127.0.0.1:8000"
PROJ = "Fable_Pilot_Fox_and_Grapes_V1"
from _38_story import (SEGMENTS, FOX_LOOK, FOREST_LOCATION_ID, TRAIL_VIEW,  # noqa: E402
                       SHOTS, CHAIN_ROOT_REF)

VILLAGE_NAME = "村庄村口 (Village Outskirts)"
VILLAGE_DESC = ("A small quiet storybook village at the edge of the forest: a packed dirt "
                "road leading in from the treeline, wooden fences on both sides, an orange "
                "pumpkin patch by the road, golden haystacks, a few rustic cottages with "
                "wooden walls and thatched roofs with thin smoke curls. Warm sunlight over "
                "everything. PURELY EMPTY inhabited set: absolutely NO people, NO humans, "
                "NO animals, NO creatures, NO chickens, NO poultry, NO dogs, NO cats, NO "
                "livestock, NO birds, NO wildlife anywhere — an uninhabited empty village, "
                "buildings, fences, road and plants only.")
VILLAGE_IDENTITY = ("Quiet storybook village outskirts at the forest edge: packed dirt road, "
                    "wooden fences, orange pumpkin patch, golden haystacks, rustic cottages "
                    "with thatched roofs. PURELY EMPTY set: absolutely NO people, NO animals, "
                    "NO creatures, NO chickens, NO dogs, NO cats, NO livestock, NO birds, NO "
                    "wildlife — uninhabited empty village, buildings and props only.")
VILLAGE_STYLE = "Same 2D storybook rendering as production; warm ochre and wood tones, soft sunlight"
VILLAGE_VIEW_LABEL = "village outskirts at forest edge - dirt road, wooden fences, pumpkin patch"


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
    for round_ in range(rounds):
        if round_ > 0:
            time.sleep(45)   # 图片限流退避（36.md §3.4）
        d = api("GET", f"/api/project/{PROJ}/{path}")
        rev = d.get("sciv_revision") or d.get("view_revision") or d.get("revision")
        if str(d.get("approval_status") or "").lower() == "approved":
            print(f"[{label}] already approved", flush=True)
            return True
        # 断点续跑：已有生成+审查通过但未批准（上轮 approve 崩溃）→ 直接补批
        if str(d.get("generation_status") or "") == "ready" and d.get("active_generation_id"):
            gv = api("GET", f"/api/project/{PROJ}/{path}/reviews", timeout=30)
            recs = gv.get("review_records") or gv.get("candidate_reviews") or []
            active = str(d.get("active_generation_id") or "")
            if any(str(c.get("overall") or "") == "pass"
                   for r in recs if str(r.get("generation_id") or "") == active
                   for c in (r.get("candidate_reviews") or [])):
                if try_approve(path, label):
                    return True
        try:
            api("POST", f"/api/project/{PROJ}/{path}/generate",
                json={"expected_revision": rev, "candidate_count": 1})
            print(f"[{label}] r{round_} gen committed", flush=True)
        except RuntimeError as e:
            c = cur(str(e))
            if c:
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


def step1_village_location():
    locs = api("GET", f"/api/project/{PROJ}/scene-system/locations")
    lst = locs.get("locations", locs)
    vil = next((l for l in (lst or []) if VILLAGE_NAME in str(l.get("name", ""))), None)
    if vil:
        print("village location exists:", vil["location_id"])
        return vil["location_id"]
    r = api("POST", f"/api/project/{PROJ}/scene-system/locations", json={
        "name": VILLAGE_NAME, "description": VILLAGE_DESC,
        "identity_description": VILLAGE_IDENTITY, "style_notes": VILLAGE_STYLE,
        "expected_document_revision": doc_rev()})
    print("village location created:", json.dumps(r.get("location_id") or r, ensure_ascii=False)[:120])
    time.sleep(1)
    locs = api("GET", f"/api/project/{PROJ}/scene-system/locations")
    lst = locs.get("locations", locs)
    vil = next(l for l in (lst or []) if VILLAGE_NAME in str(l.get("name", "")))
    return vil["location_id"]


def step2_village_view(loc_id):
    views = api("GET", f"/api/project/{PROJ}/scene-system/views")
    vlist = views.get("views") if isinstance(views, dict) else views
    vil_view = next((v for v in (vlist or [])
                     if v.get("location_id") == loc_id
                     and VILLAGE_VIEW_LABEL in str(v.get("label", ""))), None)
    if vil_view and vil_view.get("approval_status") == "approved":
        print("village view exists approved:", vil_view["view_id"])
        return vil_view["view_id"]
    if not vil_view:
        api("POST", f"/api/project/{PROJ}/scene-system/views", json={
            "location_id": loc_id, "label": VILLAGE_VIEW_LABEL,
            "visible_anchor_ids": [],
            "expected_document_revision": doc_rev()})
        time.sleep(1)
        vs = api("GET", f"/api/project/{PROJ}/scene-system/views")
        vlist2 = vs.get("views") if isinstance(vs, dict) else vs
        vil_view = next(v for v in (vlist2 or [])
                        if v.get("location_id") == loc_id
                        and VILLAGE_VIEW_LABEL in str(v.get("label", "")))
        print("village view created:", vil_view["view_id"])
    if not clean_loop("view", vil_view["view_id"], "village-view"):
        print("FATAL: village view 未过审")
        return None
    return vil_view["view_id"]


def step3_instances(village_loc_id, village_view_id):
    insts = api("GET", f"/api/project/{PROJ}/scene-system/scene-instances")
    lst = insts.get("scene_instances") if isinstance(insts, dict) else insts
    by_scene = {i.get("story_scene_id"): i for i in (lst or [])}
    view_of_seg = {"C": TRAIL_VIEW, "D": village_view_id}
    loc_of_seg = {"C": FOREST_LOCATION_ID, "D": village_loc_id}
    inst_of_seg = {}
    for seg in SEGMENTS:
        sid = "s11" if seg["key"] == "C" else "s12"
        inst = by_scene.get(sid)
        if not inst:
            api("POST", f"/api/project/{PROJ}/scene-system/scene-instances", json={
                "story_scene_id": sid, "location_id": loc_of_seg[seg["key"]],
                "expected_document_revision": doc_rev()})
            time.sleep(1)
            insts = api("GET", f"/api/project/{PROJ}/scene-system/scene-instances")
            lst = insts.get("scene_instances") if isinstance(insts, dict) else insts
            inst = next(i for i in (lst or []) if i.get("story_scene_id") == sid)
            print(f"[{sid}] instance created:", inst.get("scene_instance_id"))
        else:
            print(f"[{sid}] instance exists:", inst.get("scene_instance_id"))
        iid = inst["scene_instance_id"]
        if (inst.get("location_id") or "") != loc_of_seg[seg["key"]]:
            api("PATCH", f"/api/project/{PROJ}/scene-system/scene-instances/{iid}/location",
                json={"location_id": loc_of_seg[seg["key"]],
                      "expected_revision": inst.get("revision") or doc_rev()})
            print(f"[{sid}] location rebound")
        fresh = api("GET", f"/api/project/{PROJ}/scene-system/scene-instances/{iid}")
        st = dict(seg["state"], expected_revision=fresh.get("revision"))
        api("PATCH", f"/api/project/{PROJ}/scene-system/scene-instances/{iid}/state",
            json=st)
        print(f"[{sid}] state patched")
        inst_of_seg[seg["key"]] = (iid, view_of_seg[seg["key"]])
    return inst_of_seg


def step4_sivs(inst_of_seg):
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
                print(f"FATAL: seg {seg_key} SCIV id 未取得")
                return None
        if not clean_loop("sciv", sciv_id, f"seg-{seg_key}-siv"):
            print(f"FATAL: seg {seg_key} SIV 未过审")
            return None
        sciv_of_seg[seg_key] = sciv_id
    return sciv_of_seg


def step5_bindings(inst_of_seg, sciv_of_seg):
    seg_counter = {"C": 0, "D": 0}
    for spec in SHOTS:
        seg_counter[spec["segment"]] += 1
        sid = f"{'s11' if spec['segment'] == 'C' else 's12'}_c{seg_counter[spec['segment']]:02d}"
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
        b = api("GET", f"/api/project/{PROJ}/shot-bindings/{sid}")
        brev = b.get("binding_revision") or (b.get("binding") or {}).get("binding_revision") or ""
        r = api("POST", f"/api/project/{PROJ}/shot-bindings/{sid}/refresh",
                json={"expected_binding_revision": brev})
        print(f"[{sid}] binding refresh ->", str(r.get("status") or r)[:50])


def main():
    loc_id = step1_village_location()
    view_id = step2_village_view(loc_id)
    if not view_id:
        return
    inst_of_seg = step3_instances(loc_id, view_id)
    sciv_of_seg = step4_sivs(inst_of_seg)
    if not sciv_of_seg:
        return
    step5_bindings(inst_of_seg, sciv_of_seg)
    print("done: 场景系统接线完成", json.dumps(sciv_of_seg))


if __name__ == "__main__":
    main()
