# _34_fable_pilot/_34_video_v1f.py — v1f《狐狸与蝴蝶》自适应分组接力生成驱动。
# 用法: python _34_video_v1f.py <shot_id> [<shot_id> ...]   （组内按序，逐镜等末帧就绪）
# 断点续跑：v1g_runs.jsonl 按 shot 检查点。
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
OUT = HERE / "v1g_runs.jsonl"
RUN_DIR = HERE / "recovered_v1g"
FR = HERE / "_frames" / "v1g"


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


def wait_end_frame(shot_id, version_id, timeout_s=420):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        s = shot_state(shot_id)
        ef = s.get("video_end_frame") or {}
        if s.get("video_end_source_version") == version_id and (ef.get("url") or ""):
            return ef.get("url")
        time.sleep(6)
    return ""


def newest_version(shot_id):
    st = shot_state(shot_id)
    target = st.get("video_end_source_version") or ""
    for v in st.get("video_versions") or []:
        if v.get("version_id") == target:
            return v
    return (st.get("video_versions") or [{}])[-1]


def append(rec):
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def generate_one(sid):
    rec = None
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("shot_id") == sid:
                    rec = r
    if rec and rec.get("job_status") == "done":
        print(f"[{sid}] checkpoint done → skip", flush=True)
        return "done"
    r = api("POST", f"/api/project/{PROJ}/shot/{sid}/video")
    append({"run": "v1g", "shot_id": sid, "job_id": r["job_id"],
            "job_status": "submitted", "ts": time.time()})
    print(f"[{sid}] job={r['job_id']} submitted", flush=True)
    state, err = wait_job(r["job_id"])
    vv = newest_version(sid) if state == "done" else {}
    mp4 = RUN_DIR / f"{sid}_v1g.mp4"
    if vv.get("url"):
        try:
            mp4.write_bytes(httpx.get(vv["url"], timeout=180).content)
        except Exception as e:
            print(f"  download fail: {str(e)[:100]}", flush=True)
    if state == "done" and vv.get("version_id"):
        end_url = wait_end_frame(sid, vv["version_id"]) or ""
        print(f"[{sid}] -> done version={vv.get('version_id')} "
              f"sec={vv.get('seconds_returned')} end_ready={bool(end_url)}", flush=True)
        append({"run": "v1g", "shot_id": sid, "job_id": r["job_id"], "job_status": "done",
                "version_id": vv.get("version_id"), "url": vv.get("url"),
                "local": str(mp4) if mp4.exists() else "",
                "seconds": vv.get("seconds_returned"), "ts": time.time()})
    else:
        print(f"[{sid}] -> {state} ERROR: {err[:300]}", flush=True)
        append({"run": "v1g", "shot_id": sid, "job_id": r["job_id"], "job_status": state,
                "task_error": err[:300], "ts": time.time()})
    return state


def extract_tail(sid):
    """提取该镜尾帧供自适应编剧查看（全分辨率 PNG）。"""
    mp4 = RUN_DIR / f"{sid}_v1g.mp4"
    png = FR / f"{sid}_tail_view.png"
    if not mp4.exists():
        return ""
    if png.exists():
        return str(png)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.1", "-i", str(mp4),
                    "-frames:v", "1", str(png)], capture_output=True)
    return str(png) if png.exists() else ""


def main():
    RUN_DIR.mkdir(exist_ok=True); FR.mkdir(parents=True, exist_ok=True)
    shots = sys.argv[1:]
    if not shots:
        print("usage: _34_video_v1f.py <shot_id> ...")
        return
    for sid in shots:
        state = generate_one(sid)
        if state != "done":
            print(f"[v1g] {sid} 失败，中止", flush=True)
            return
        tail = extract_tail(sid)
        print(f"[v1g] {sid} 尾帧（供下一组编剧参考）: {tail}", flush=True)
    print(f"[v1g] batch done: {shots}", flush=True)


if __name__ == "__main__":
    main()
