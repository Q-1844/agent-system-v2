# _34_fable_pilot/_34_video_v1d.py — 动作接力参考组生成（v1d，每镜 1 条，背靠背无链式等待）。
# 参考=上镜结尾动作的已批准姿态变体（_34_set_action_refs.py 已写入）。
# 金丝雀：s02 完成后对比 ff vs 其参考图（姿态参考强条件化开场，R1 实证≈逐像素；
# 若 diff 大=资格校验静默回落 hero → 中止余链 fail-loud）。
import asyncio
import io
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8000"
PROJ = "Fable_Pilot_Fox_and_Grapes_V1"
HERE = Path(__file__).parent
OUT = HERE / "v1d_runs.jsonl"
RUN_DIR = HERE / "recovered_v1d"
FR = HERE / "_frames" / "v1d"
SHOTS = ["s02_c01", "s03_c01", "s04_c01", "s05_c01", "s06_c01", "s07_c01"]
REFS = {
    "s02_c01": "ref_2ecaca2f3553", "s03_c01": "ref_2ecaca2f3553",
    "s04_c01": "ref_c19d9a0df2ec", "s05_c01": "ref_a1b93821b4a6",
    "s06_c01": "ref_e5c261d74890", "s07_c01": "ref_e5c261d74890",
}
CANARY_DIFF = 15.0


def api(method, path, **kw):
    r = httpx.request(method, f"{BASE}{path}", timeout=60, **kw)
    r.raise_for_status()
    return r.json()


def shot_state(shot_id):
    d = api("GET", f"/api/project/{PROJ}/shots")
    for sc in d.get("scenes") or []:
        for s in sc.get("shots") or []:
            if s.get("shot_id") == shot_id:
                return s
    return {}


def wait_job(job_id, timeout_s=1800):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        d = api("GET", f"/api/project/{PROJ}/jobs")
        jobs = d.get("jobs") if isinstance(d, dict) else d
        if isinstance(jobs, dict):
            jobs = list(jobs.values())
        for j in jobs or []:
            if j.get("job_id") == job_id:
                if j.get("state") in ("done", "failed", "cancelled"):
                    return j.get("state"), (j.get("error") or "")
                break
        time.sleep(6)
    return "wait_timeout", ""


def ref_asset_urls():
    """从 R3 look 详情取 asset_id -> 本地路径/GCS url（金丝雀比对用）。"""
    d = api("GET", f"/api/project/{PROJ}/resources/look_2c6a01d45db5")
    out = {}
    for a in d.get("approved_reference_assets") or []:
        out[a.get("asset_id")] = {"local": a.get("local"), "url": a.get("url")}
    return out


def frame_extract(mp4, png):
    mp4, png = Path(mp4), Path(png)
    if not mp4.exists() or png.exists():
        return str(png) if png.exists() else ""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4),
                    "-vf", "select=eq(n\\,0)", "-frames:v", "1", str(png)],
                   capture_output=True)
    return str(png) if png.exists() else ""


def mae(a, b, w=128, h=128):
    from PIL import Image
    ia = Image.open(a).convert("L").resize((w, h))
    ib = Image.open(b).convert("L").resize((w, h))
    pa, pb = list(ia.getdata()), list(ib.getdata())
    return sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa)


def append(rec):
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def last_record(shot_id):
    rec = None
    for line in OUT.read_text(encoding="utf-8").splitlines() if OUT.exists() else []:
        if line.strip():
            r = json.loads(line)
            if r.get("shot_id") == shot_id:
                rec = r
    return rec


def main():
    RUN_DIR.mkdir(exist_ok=True); FR.mkdir(parents=True, exist_ok=True)
    refs = ref_asset_urls()
    print(f"[v1d] 动作接力组：提交 {SHOTS}（背靠背）", flush=True)

    # 1) 全部提交（断点续跑：跳过已 done）
    jobs = {}
    for sid in SHOTS:
        rec = last_record(sid)
        if rec and rec.get("job_status") == "done":
            print(f"[{sid}] checkpoint done → skip", flush=True)
            jobs[sid] = None
            continue
        r = api("POST", f"/api/project/{PROJ}/shot/{sid}/video")
        jobs[sid] = r["job_id"]
        append({"run": "v1d", "shot_id": sid, "ref": REFS[sid], "job_id": r["job_id"],
                "job_status": "submitted", "ts": time.time()})
        print(f"[{sid}] job={r['job_id']} ref={REFS[sid]}", flush=True)

    # 2) 逐个收割（队列串行，结果按完成序出现）
    aborted = ""
    for sid in SHOTS:
        if aborted:
            print(f"[{sid}] SKIPPED（{aborted} 异常中止）", flush=True)
            continue
        t0 = time.time()
        if jobs[sid] is None:
            st = shot_state(sid)
            vv = next((v for v in st.get("video_versions") or []
                       if v.get("version_id") == st.get("video_end_source_version")), {})
            mp4 = RUN_DIR / f"{sid}_v1d.mp4"
            if vv.get("url") and not mp4.exists():
                mp4.write_bytes(httpx.get(vv["url"], timeout=180).content)
            print(f"[{sid}] done (checkpoint) version={vv.get('version_id')}", flush=True)
            continue
        state, err = wait_job(jobs[sid])
        st = shot_state(sid)
        vv = next((v for v in st.get("video_versions") or []
                   if v.get("version_id") == st.get("video_end_source_version")), {})
        mp4 = RUN_DIR / f"{sid}_v1d.mp4"
        if vv.get("url"):
            try:
                mp4.write_bytes(httpx.get(vv["url"], timeout=180).content)
            except Exception as e:
                print(f"  download fail: {str(e)[:100]}", flush=True)
        ff = frame_extract(mp4, FR / f"{sid}_ff.png") if mp4.exists() else ""
        # 金丝雀（仅 s02）：ff 应≈参考姿态图
        diff = None
        if sid == "s02_c01" and ff:
            ref_info = refs.get(REFS[sid]) or {}
            ref_png = FR / "ref_s02.png"
            if ref_info.get("local") and Path(ref_info["local"]).exists():
                ref_png.write_bytes(Path(ref_info["local"]).read_bytes())
            elif ref_info.get("url"):
                ref_png.write_bytes(httpx.get(ref_info["url"], timeout=60).content)
            if ref_png.exists():
                diff = mae(ref_png, ff)
        rec = {"run": "v1d", "shot_id": sid, "ref": REFS[sid], "job_id": jobs[sid],
               "job_status": state, "task_error": err[:300],
               "version_id": vv.get("version_id"), "video_id": vv.get("video_id"),
               "url": vv.get("url"), "local": str(mp4) if mp4.exists() else "",
               "seconds": vv.get("seconds_returned"),
               "canary_diff_vs_ref": round(diff, 1) if diff is not None else None,
               "ff_frame": ff, "wall_s": round(time.time() - t0, 1), "ts": time.time()}
        append(rec)
        print(f"[{sid}] -> {state} version={rec['version_id']} "
              f"diff_vs_ref={rec['canary_diff_vs_ref']} wall={rec['wall_s']}s", flush=True)
        if state != "done":
            aborted = sid
            print(f"  ERROR: {err[:200]}", flush=True)
            continue
        if sid == "s02_c01" and (diff is None or diff > CANARY_DIFF):
            print(f"  CANARY FAIL: ff vs 参考 diff={diff}（疑似静默回落 hero）→ 中止余链", flush=True)
            aborted = sid
    print(f"[v1d] finished" + (f" aborted_at={aborted}" if aborted else " 6/6"), flush=True)


if __name__ == "__main__":
    main()
