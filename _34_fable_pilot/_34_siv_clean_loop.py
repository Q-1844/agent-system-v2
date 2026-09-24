# _34_fable_pilot/_34_siv_clean_loop.py — 独立进程跑 SIV 清图循环（防 harness 内存清理）。
# 进度写 stdout（重定向到日志）；SUMMARY: 行=结束标志。用法: python _34_siv_clean_loop.py <sciv_id> <shot_label>
import io
import re
import sys
import time

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8000"
PROJ = "Fable_Pilot_Fox_and_Grapes_V1"
SCIV = sys.argv[1] if len(sys.argv) > 1 else "sciv_f9491667b85d"
LABEL = sys.argv[2] if len(sys.argv) > 2 else "s04"


def api(m, p, **kw):
    r = httpx.request(m, f"{BASE}{p}", timeout=300, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{p} {r.status_code}: {r.text[:350]}")
    return r.json()


def cur(e):
    m = re.search(r'"current_.*?revision"\s*:\s*"([0-9a-f]+)"', e)
    return m.group(1) if m else None


def main():
    sciv = SCIV
    ok = False
    for round_ in range(12):
        if round_ > 0:
            time.sleep(45)
        d = api("GET", f"/api/project/{PROJ}/scene-system/scene-instance-views/{sciv}")
        vrev = d.get("sciv_revision") or d.get("revision")
        if str(d.get("approval_status") or "").lower() == "approved":
            print(f"[{LABEL}] already approved", flush=True)
            ok = True
            break
        try:
            api("POST", f"/api/project/{PROJ}/scene-system/scene-instance-views/{sciv}/generate",
                json={"expected_revision": vrev, "candidate_count": 1})
            print(f"[{LABEL}] r{round_} gen committed", flush=True)
        except RuntimeError as e:
            c = cur(str(e))
            if c:
                vrev = c; continue
            print(f"[{LABEL}] gen err: {str(e)[:130]}", flush=True)
            time.sleep(45); continue
        time.sleep(5)
        try:
            rv = api("POST", f"/api/project/{PROJ}/scene-system/scene-instance-views/{sciv}/review")
            recs = (rv.get("candidate_reviews") or [{}])
            ov = recs[0].get("overall")
            print(f"[{LABEL}] r{round_} review: {ov}", flush=True)
            if ov != "pass":
                continue
        except RuntimeError as e:
            print(f"[{LABEL}] review err: {str(e)[:130]}", flush=True); continue
        for att in range(3):
            d = api("GET", f"/api/project/{PROJ}/scene-system/scene-instance-views/{sciv}")
            vrev = d.get("sciv_revision") or d.get("revision")
            try:
                rres = httpx.get(f"{BASE}/api/project/{PROJ}/resources", timeout=15).json().get("document_revision") or ""
                ap = api("POST", f"/api/project/{PROJ}/scene-system/scene-instance-views/{sciv}/approve",
                         json={"candidate_index": 0, "expected_revision": vrev,
                               "expected_resources_document_revision": rres})
                print(f"[{LABEL}] APPROVED", flush=True); ok = True; break
            except RuntimeError as e:
                c = cur(str(e))
                if c:
                    vrev = c; continue
                print(f"[{LABEL}] approve err: {str(e)[:180]}", flush=True)
        if ok:
            break
    print(f"SUMMARY: {{'{LABEL}': {ok}}}", flush=True)


if __name__ == "__main__":
    main()
