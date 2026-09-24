# _34_fable_pilot/_34_video_v1e.py — 分组接力（v1e）两波生成驱动。
# 波1：s02(链根)/s04(重锚定)/s06(重锚定) 同时提交；各自 done 且末帧托管就绪（Pages）后
# 提交波2 接力镜 s03/s05/s07。断点续跑：v1e_runs.jsonl 按 shot 检查点。
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
OUT = HERE / "v1e_runs.jsonl"
RUN_DIR = HERE / "recovered_v1e"
FR = HERE / "_frames" / "v1e"
WAVE1 = ["s02_c01", "s04_c01", "s06_c01"]
WAVE2 = {"s03_c01": "s02_c01", "s05_c01": "s04_c01", "s07_c01": "s06_c01"}
ALL = WAVE1 + list(WAVE2)


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
    """等 Phase 6A 末帧提取+托管就绪（新管线：Pages 上传+构建 ~40s+）。"""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        s = shot_state(shot_id)
        ef = s.get("video_end_frame") or {}
        if s.get("video_end_source_version") == version_id and (ef.get("url") or ""):
            return ef.get("url")
        time.sleep(6)
    return ""


def frame_extract(mp4, png, last=False):
    mp4, png = Path(mp4), Path(png)
    if not mp4.exists() or png.exists():
        return str(png) if png.exists() else ""
    args = ["ffmpeg", "-y", "-v", "error"]
    if last:
        args += ["-sseof", "-0.1"]
    args += ["-i", str(mp4)]
    if not last:
        args += ["-vf", "select=eq(n\\,0)"]
    args += ["-frames:v", "1", str(png)]
    subprocess.run(args, capture_output=True)
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
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("shot_id") == shot_id:
                    rec = r
    return rec


def newest_version(shot_id):
    st = shot_state(shot_id)
    target = st.get("video_end_source_version") or ""
    for v in st.get("video_versions") or []:
        if v.get("version_id") == target:
            return v
    return (st.get("video_versions") or [{}])[-1]


def generate_one(sid):
    """提交→等 job→存档→等末帧就绪。返回 (state, version, end_url)。"""
    rec = last_record(sid)
    if rec and rec.get("job_status") == "done":
        print(f"[{sid}] checkpoint done → skip", flush=True)
        return "done", rec.get("version_id"), None
    r = api("POST", f"/api/project/{PROJ}/shot/{sid}/video")
    append({"run": "v1e", "shot_id": sid, "job_id": r["job_id"],
            "job_status": "submitted", "ts": time.time()})
    print(f"[{sid}] job={r['job_id']} submitted", flush=True)
    state, err = wait_job(r["job_id"])
    vv = newest_version(sid) if state == "done" else {}
    mp4 = RUN_DIR / f"{sid}_v1e.mp4"
    if vv.get("url"):
        try:
            mp4.write_bytes(httpx.get(vv["url"], timeout=180).content)
        except Exception as e:
            print(f"  download fail: {str(e)[:100]}", flush=True)
    end_url = ""
    if state == "done" and vv.get("version_id"):
        end_url = wait_end_frame(sid, vv["version_id"]) or ""
        print(f"[{sid}] -> {state} version={vv.get('version_id')} "
              f"sec={vv.get('seconds_returned')} end_ready={bool(end_url)}", flush=True)
    else:
        print(f"[{sid}] -> {state} ERROR: {err[:200]}", flush=True)
    append({"run": "v1e", "shot_id": sid, "job_id": r["job_id"], "job_status": state,
            "task_error": err[:300], "version_id": vv.get("version_id"),
            "url": vv.get("url"), "local": str(mp4) if mp4.exists() else "",
            "seconds": vv.get("seconds_returned"),
            "num_frames": vv.get("num_frames"), "end_url": end_url,
            "ts": time.time()})
    return state, vv.get("version_id"), end_url


def main():
    RUN_DIR.mkdir(exist_ok=True); FR.mkdir(parents=True, exist_ok=True)
    print(f"[v1e] 分组接力：波1={WAVE1}（同时提交）", flush=True)
    failed = []
    # ---- 波 1 ----
    for sid in WAVE1:
        state, _, _ = generate_one(sid)
        if state != "done":
            failed.append(sid)
    if failed:
        print(f"[v1e] 波1失败 {failed}，中止", flush=True)
        return
    # ---- 波 2：各自等组内上镜末帧就绪即提交 ----
    print(f"[v1e] 波2={list(WAVE2)}（组内接力）", flush=True)
    for sid, src in WAVE2.items():
        st = shot_state(src)
        ef = st.get("video_end_frame") or {}
        if not (ef.get("url") and st.get("video_end_source_version")):
            generate_one  # noqa —— src 末帧已在波1等待中就绪；此处兜底再等一次
            end_url = wait_end_frame(src, st.get("video_end_source_version") or "", 60)
            print(f"[{sid}] 等待 {src} 末帧就绪: {bool(end_url)}", flush=True)
        state, _, _ = generate_one(sid)
        if state != "done":
            failed.append(sid)
    if failed:
        print(f"[v1e] 完成（有失败: {failed}）", flush=True)
    else:
        print("[v1e] 6/6 finished", flush=True)


if __name__ == "__main__":
    main()
