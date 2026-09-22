# _34_fable_pilot/_34_video_v1b.py — 34.md V1B 分组迭代批次（每组 1 条高质量视频）。
# 每组：提交视频 job（正式 API）→ JobQueue 轮询 → 完成后立即从 provider URL
# 下载独立文件（规避 VideoVersion.local 按镜覆盖）→ 记录 v1b_runs.jsonl。
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
PDIR = Path(__file__).resolve().parent.parent / "projects" / PROJ
OUT = Path(__file__).parent / "v1b_runs.jsonl"
REC = Path(__file__).parent / "recovered_v1b"
REC.mkdir(exist_ok=True)
JOBS = ["s02_c01", "s03_c01", "s04_c01", "s05_c01", "s06_c01", "s07_c01"]


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


def wait_task(job_id, timeout_s=1800):
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


def ffprobe(p):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                            "-show_streams", str(p)], capture_output=True, text=True)
        d = json.loads(r.stdout or "{}")
        a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
        v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
        return {"duration": (d.get("format") or {}).get("duration"),
                "size": f'{v.get("width")}x{v.get("height")}' if v else None,
                "audio": bool(a), "audio_codec": (a or {}).get("codec_name")}
    except Exception as e:
        return {"err": str(e)[:100]}


def main():
    shots = sys.argv[1:] or JOBS
    for shot_id in shots:
        st = shot_state(shot_id)
        before = {v.get("version_id") for v in (st.get("video_versions") or [])}
        ref = st.get("video_reference_asset_id")
        r = api("POST", f"/api/project/{PROJ}/shot/{shot_id}/video")
        job_id = r["job_id"]
        print(f"[{shot_id}] ref={ref} job={job_id} submitted", flush=True)
        state, err = wait_task(job_id)
        st2 = shot_state(shot_id)
        new_vs = [v for v in (st2.get("video_versions") or [])
                  if v.get("version_id") not in before]
        vv = new_vs[-1] if new_vs else {}
        # 立即独立存档（防覆盖）
        local_rec = ""
        if vv.get("url"):
            dest = REC / f"{shot_id}_V1B.mp4"
            try:
                data = httpx.get(vv["url"], timeout=180).content
                dest.write_bytes(data)
                local_rec = str(dest)
            except Exception as e:
                print(f"  download fail: {str(e)[:100]}", flush=True)
        rec = {"round": "v1b", "shot_id": shot_id, "candidate": 1,
               "job_id": job_id, "job_status": state, "task_error": err[:300],
               "version_id": vv.get("version_id"), "video_id": vv.get("video_id"),
               "url": vv.get("url"), "local_recovered": local_rec,
               "seconds": vv.get("seconds_returned"), "size": vv.get("size_returned"),
               "model": vv.get("model"), "generation_basis": vv.get("generation_basis"),
               "prompt_snapshot": vv.get("prompt_snapshot"),
               "video_reference_asset_id": ref,
               "ffprobe": ffprobe(Path(local_rec)) if local_rec else None,
               "ts": time.time()}
        with OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{shot_id}] -> {state} version={rec['version_id']} "
              f"saved={bool(local_rec)}", flush=True)


if __name__ == "__main__":
    main()
