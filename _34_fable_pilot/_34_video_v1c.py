# _34_fable_pilot/_34_video_v1c.py — 34.md V1C 尾帧接力链（每镜 1 条，严格串行）。
# 链：s02←s01 尾帧 → s03←s02_V1C 尾帧 → … 每镜完成后等待 Phase 6A 末帧提取+托管
# 就绪再进下一镜。记录 v1c_runs.jsonl。
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
OUT = Path(__file__).parent / "v1c_runs.jsonl"
REC = Path(__file__).parent / "recovered_v1c"
REC.mkdir(exist_ok=True)
CHAIN = ["s02_c01", "s03_c01", "s04_c01", "s05_c01", "s06_c01", "s07_c01"]


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


def wait_end_frame(shot_id, version_id, timeout_s=240):
    """等待 Phase 6A 末帧提取+托管完成（video_end_source_version==本版本 且 url 非空）。"""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        s = shot_state(shot_id)
        ef = s.get("video_end_frame") or {}
        if s.get("video_end_source_version") == version_id and (ef.get("url") or ""):
            return ef.get("url")
        time.sleep(5)
    return ""


def ffprobe(p):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                            "-show_streams", str(p)], capture_output=True, text=True)
        d = json.loads(r.stdout or "{}")
        a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
        v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
        return {"duration": (d.get("format") or {}).get("duration"),
                "size": f'{v.get("width")}x{v.get("height")}' if v else None,
                "audio": bool(a)}
    except Exception as e:
        return {"err": str(e)[:100]}


def main():
    for shot_id in CHAIN:
        st = shot_state(shot_id)
        before = {v.get("version_id") for v in (st.get("video_versions") or [])}
        r = api("POST", f"/api/project/{PROJ}/shot/{shot_id}/video")
        job_id = r["job_id"]
        print(f"[{shot_id}] relay job={job_id} submitted", flush=True)
        state, err = wait_job(job_id)
        st2 = shot_state(shot_id)
        new_vs = [v for v in (st2.get("video_versions") or [])
                  if v.get("version_id") not in before]
        vv = new_vs[-1] if new_vs else {}
        local_rec = ""
        if vv.get("url"):
            dest = REC / f"{shot_id}_V1C.mp4"
            try:
                dest.write_bytes(httpx.get(vv["url"], timeout=180).content)
                local_rec = str(dest)
            except Exception as e:
                print(f"  download fail: {str(e)[:100]}", flush=True)
        tail_url = ""
        if state == "done" and vv.get("version_id"):
            tail_url = wait_end_frame(shot_id, vv["version_id"])
            print(f"  tail ready: {bool(tail_url)}", flush=True)
        rec = {"round": "v1c", "shot_id": shot_id, "candidate": 1,
               "job_id": job_id, "job_status": state, "task_error": err[:300],
               "version_id": vv.get("version_id"), "video_id": vv.get("video_id"),
               "url": vv.get("url"), "local_recovered": local_rec,
               "seconds": vv.get("seconds_returned"),
               "generation_basis": vv.get("generation_basis"),
               "prompt_snapshot": vv.get("prompt_snapshot"),
               "tail_url": tail_url,
               "ffprobe": ffprobe(Path(local_rec)) if local_rec else None,
               "ts": time.time()}
        with OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{shot_id}] -> {state} version={rec['version_id']}", flush=True)
        if state != "done":
            print(f"  ERROR: {err[:200]}", flush=True)


if __name__ == "__main__":
    main()
