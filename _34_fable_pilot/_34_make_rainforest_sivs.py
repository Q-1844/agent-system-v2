# _34_fable_pilot/_34_make_rainforest_sivs.py — 为 s02-s05 的雨林实例批量生成+批准 SIV，并换绑 binding 视图。
import io
import json
import re
import sys
import time
from pathlib import Path

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8000"
PROJ = "Fable_Pilot_Fox_and_Grapes_V1"
LOC = "loc_04970479321e"
LVIEW = "view_70e07a5c7211"
SCENES = {"s02": "scin_9bd1187bd83e", "s03": "scin_08b5c0abd8a4",
          "s04": "scin_4a9db7302c82", "s05": "scin_1ba8dfdd5ba9"}


def api(m, p, **kw):
    r = httpx.request(m, f"{BASE}{p}", timeout=300, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{p} {r.status_code}: {r.text[:400]}")
    return r.json()


def doc_rev():
    ov = api("GET", f"/api/project/{PROJ}/scene-system/overview")
    return ov.get("document_revision") or ov.get("revision")


def res_rev():
    return api("GET", f"/api/project/{PROJ}/resources").get("document_revision") or ""


def current_rev_from_error(e: str):
    m = re.search(r'"current_.*?revision"\s*:\s*"([0-9a-f]+)"', e)
    return m.group(1) if m else None


def main():
    doc = doc_rev()
    for scene_id, iid in SCENES.items():
        print(f"===== {scene_id} ({iid}) =====", flush=True)
        # 1) 创建 SIV（幂等：409/已存在则查现有）
        try:
            r = api("POST", f"/api/project/{PROJ}/scene-system/scene-instance-views", json={
                "scene_instance_id": iid, "location_view_id": LVIEW,
                "label": f"rainforest {scene_id}",
                "expected_document_revision": doc_rev()})
            sciv = r.get("scene_instance_view") or r.get("after") or {}
            sciv_id = (sciv.get("sciv_id") or sciv.get("scene_instance_view_id")
                       or r.get("target"))
        except RuntimeError as e:
            if "409" in str(e) or "exists" in str(e) or "duplicate" in str(e):
                print("  SIV 已存在，改查现有", flush=True)
                lst = api("GET", f"/api/project/{PROJ}/scene-system/scene-instance-views")
                ll = lst.get("scene_instance_views") if isinstance(lst, dict) else lst
                sciv_id = next((x["sciv_id"] for x in (ll or [])
                                if x.get("scene_instance_id") == iid
                                and x.get("location_view_id") == LVIEW), None)
            else:
                print("  create FAIL:", str(e)[:200], flush=True); continue
        if not sciv_id:
            print("  sciv_id 未取到", flush=True); continue
        print("  sciv:", sciv_id, flush=True)

        # 2) generate（candidate_count=1）
        d = api("GET", f"/api/project/{PROJ}/scene-system/scene-instance-views/{sciv_id}")
        vrev = d.get("sciv_revision") or d.get("revision")
        try:
            g = api("POST", f"/api/project/{PROJ}/scene-system/scene-instance-views/{sciv_id}/generate",
                    json={"expected_revision": vrev, "candidate_count": 1})
            print("  generate:", g.get("status"), flush=True)
        except RuntimeError as e:
            if "已存在" in str(e) or "409" in str(e):
                print("  generate 幂等跳过", flush=True)
            else:
                print("  generate FAIL:", str(e)[:250], flush=True); continue

        # 3) review
        try:
            rv = api("POST", f"/api/project/{PROJ}/scene-system/scene-instance-views/{sciv_id}/review")
            print("  review:", rv.get("status"), flush=True)
        except RuntimeError as e:
            print("  review FAIL:", str(e)[:200], flush=True)

        # 4) approve（双 OCC；409 时按回执 current 重试一次）
        for attempt in range(2):
            d = api("GET", f"/api/project/{PROJ}/scene-system/scene-instance-views/{sciv_id}")
            vrev = d.get("sciv_revision") or d.get("revision")
            try:
                ap = api("POST", f"/api/project/{PROJ}/scene-system/scene-instance-views/{sciv_id}/approve",
                         json={"candidate_index": 0, "expected_revision": vrev,
                               "expected_resources_document_revision": res_rev()})
                print("  approve ok:", str(ap.get("asset_id"))[:30], flush=True)
                break
            except RuntimeError as e:
                cur = current_rev_from_error(str(e))
                if cur and attempt == 0:
                    vrev = cur; continue
                print("  approve FAIL:", str(e)[:300], flush=True)

    # 5) 换绑四镜 binding 的 location_view + pinned SIV → refresh
    for sid in ("s02_c01", "s03_c01", "s04_c01", "s05_c01"):
        d = httpx.get(f"{BASE}/api/project/{PROJ}/shot-bindings/{sid}", timeout=15).json()
        b = d["binding"]; rev = d["binding_revision"]
        sb = b.get("scene_binding") or {}
        sb["location_view_id"] = LVIEW
        # 找该实例的已批准 SIV
        lst = api("GET", f"/api/project/{PROJ}/scene-system/scene-instance-views")
        ll = lst.get("scene_instance_views") if isinstance(lst, dict) else lst
        siv = next((x for x in (ll or []) if x.get("scene_instance_id") == sb.get("scene_instance_id")
                    and x.get("location_view_id") == LVIEW
                    and str(x.get("status", "")).lower() in ("approved", "active", "done")), None)
        sb["pinned_scene_instance_view_id"] = (siv or {}).get("sciv_id")
        body = dict(b); body["scene_binding"] = sb; body["expected_binding_revision"] = rev
        r = httpx.put(f"{BASE}/api/project/{PROJ}/shot-bindings/{sid}", json=body, timeout=60)
        if r.status_code >= 400:
            print(f"[{sid}] PUT FAIL:", r.text[:200], flush=True); continue
        d2 = httpx.get(f"{BASE}/api/project/{PROJ}/shot-bindings/{sid}", timeout=15).json()
        r2 = httpx.post(f"{BASE}/api/project/{PROJ}/shot-bindings/{sid}/refresh",
                        json={"expected_binding_revision": d2["binding_revision"]}, timeout=300)
        out = r2.json(); af = out.get("after") or {}
        print(f"[{sid}] refresh ok={out.get('ok')} status={af.get('status')} "
              f"view={(af.get('scene_binding') or {}).get('location_view_id')}", flush=True)


if __name__ == "__main__":
    main()
