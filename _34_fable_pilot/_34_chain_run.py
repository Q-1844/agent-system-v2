# _34_fable_pilot/_34_chain_run.py — V1C 尾帧接力链通用驱动（34.md 后续漂移实验）。
# 用法: python _34_chain_run.py <run_id> [shot_id ...]   （缺省跑全链 s02→s07）
# 相比 _34_video_v1c.py 的增强：
#   1. 断点继续：v1c_runs.jsonl 按 (round=run_id, shot_id) 检查点——done 跳过 /
#      submitted 带 job_id 重入等待（不重复扣配额）
#   2. 每轮独立存档 recovered_v1c/<run_id>/（修 VideoVersion.local 按镜覆盖问题）
#   3. 每镜自动量化衔接 diff（MAE 灰度 128，与历史 3.3-9.0/35-50 同刻度，
#      校准复核 run2: 4.2/27.7/3.5/8.5/4.3/3.3）
#   4. 首镜 relay 金丝雀：diff>20 → 疑似静默回落 hero（白跑 bug 形态），
#      中止余链（最多浪费 1 条，fail-loud）
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
OUT = HERE / "v1c_runs.jsonl"
CHAIN = ["s02_c01", "s03_c01", "s04_c01", "s05_c01", "s06_c01", "s07_c01"]
ROOT_TAIL = HERE.parent / "projects" / "Fable_Pilot_Fox_and_Grapes_V1" / \
    "output" / "videos" / "s01_c01.mp4"
CANARY_DIFF = 20.0  # relay 实证 3.3-9.0（s02 根衔接 4.2）；回落 hero 会远超


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


def wait_end_frame(shot_id, version_id, timeout_s=300):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        s = shot_state(shot_id)
        ef = s.get("video_end_frame") or {}
        if s.get("video_end_source_version") == version_id and (ef.get("url") or ""):
            return ef.get("url")
        time.sleep(5)
    return ""


def frame_extract(mp4, png, last=False):
    mp4, png = Path(mp4), Path(png)
    if not mp4.exists():
        return ""
    if png.exists():
        return str(png)
    args = ["ffmpeg", "-y", "-v", "error"]
    if last:
        args += ["-sseof", "-0.1"]
    args += ["-i", str(mp4)]
    if not last:
        args += ["-vf", "select=eq(n\\,0)"]  # 输出选项必须在 -i 之后
    args += ["-frames:v", "1", str(png)]
    subprocess.run(args, capture_output=True)
    return str(png) if png.exists() else ""


def mae(a, b, w=128, h=128):
    try:
        from PIL import Image
        ia = Image.open(a).convert("L").resize((w, h))
        ib = Image.open(b).convert("L").resize((w, h))
        pa, pb = list(ia.getdata()), list(ib.getdata())
        return sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa)
    except Exception:
        return -1.0


def last_record(run_id, shot_id):
    rec = None
    for line in OUT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("round") == run_id and r.get("shot_id") == shot_id:
            rec = r
    return rec


def append(rec):
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    run_id = sys.argv[1] if len(sys.argv) > 1 else f"v1c_r{int(time.time())}"
    shots = sys.argv[2:] or CHAIN
    run_dir = HERE / "recovered_v1c" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    fr_dir = HERE / "_frames" / run_id
    fr_dir.mkdir(parents=True, exist_ok=True)
    print(f"[chain] run={run_id} shots={shots}", flush=True)

    prev_tail_png = ""  # 上一镜本轮末帧（本 run 存档重建，imgbb 不信任）
    aborted = ""
    for i, shot_id in enumerate(shots):
        if aborted:
            print(f"[{shot_id}] SKIPPED（前镜 {aborted} 异常中止）", flush=True)
            continue
        t_shot = time.time()
        err = ""
        mp4 = run_dir / f"{shot_id}_{run_id}.mp4"
        rec = last_record(run_id, shot_id)
        state = job_id = ""
        if rec and rec.get("job_status") == "done" and rec.get("local_recovered"):
            print(f"[{shot_id}] checkpoint done → skip", flush=True)
            mp4 = Path(rec["local_recovered"])
            state = "done"
        elif rec and rec.get("job_id") and rec.get("job_status") in (
                "submitted", "running", "wait_timeout"):
            job_id = rec["job_id"]
            print(f"[{shot_id}] checkpoint rewait job={job_id}", flush=True)
            state, err = wait_job(job_id)
        else:
            r = api("POST", f"/api/project/{PROJ}/shot/{shot_id}/video")
            job_id = r["job_id"]
            append({"round": run_id, "shot_id": shot_id, "candidate": 1,
                    "job_id": job_id, "job_status": "submitted",
                    "ts": time.time()})
            print(f"[{shot_id}] relay job={job_id} submitted", flush=True)
            state, err = wait_job(job_id)

        vv = {}
        if state == "done":
            st2 = shot_state(shot_id)
            # 本轮版本 = end_source_version 当前指向者（wait_end_frame 已核对）
            vid_target = st2.get("video_end_source_version") or ""
            for v in st2.get("video_versions") or []:
                if v.get("version_id") == vid_target:
                    vv = v
                    break
        local_rec = ""
        if vv.get("url"):
            try:
                mp4.write_bytes(httpx.get(vv["url"], timeout=180).content)
                local_rec = str(mp4)
            except Exception as e:
                print(f"  download fail: {str(e)[:100]}", flush=True)

        tail_url = ""
        if state == "done" and vv.get("version_id"):
            tail_url = wait_end_frame(shot_id, vv["version_id"]) or ""
            print(f"  tail ready: {bool(tail_url)}", flush=True)

        # 本镜末帧重建（下一镜的 relay 物料，独立存档不依赖托管 URL）
        this_tail_png = frame_extract(local_rec, fr_dir / f"{shot_id}_tail.png",
                                      last=True) if local_rec else ""
        ff_png = frame_extract(local_rec, fr_dir / f"{shot_id}_ff.png") if local_rec else ""
        diff = -1.0
        if not prev_tail_png and i > 0:
            # 断点续跑恢复：从上一镜本轮存档重建 relay 物料
            prev_tail_png = frame_extract(
                str(run_dir / f"{shots[i-1]}_{run_id}.mp4"),
                fr_dir / f"{shots[i-1]}_tail.png", last=True)
        if ff_png and (prev_tail_png or i == 0):
            base = prev_tail_png if prev_tail_png else frame_extract(
                str(ROOT_TAIL), fr_dir / "_s01_root_tail.png", last=True)
            if base:
                diff = mae(base, ff_png)

        rec = {"round": run_id, "shot_id": shot_id, "candidate": 1,
               "job_id": job_id, "job_status": state, "task_error": (err if state != "done" else "")[:300],
               "version_id": vv.get("version_id"), "video_id": vv.get("video_id"),
               "url": vv.get("url"), "local_recovered": local_rec,
               "seconds": vv.get("seconds_returned"),
               "generation_basis": vv.get("generation_basis"),
               "prompt_snapshot": vv.get("prompt_snapshot"),
               "tail_url": tail_url,
               "relay_diff": round(diff, 1) if diff >= 0 else None,
               "ff_frame": ff_png, "tail_frame": this_tail_png,
               "wall_s": round(time.time() - t_shot, 1),
               "ts": time.time()}
        append(rec)
        print(f"[{shot_id}] -> {state} version={rec['version_id']} "
              f"diff={rec['relay_diff']} wall={rec['wall_s']}s", flush=True)
        if state != "done":
            aborted = shot_id
            print(f"  ERROR: {rec['task_error'][:200]}", flush=True)
            continue
        # 金丝雀：链首镜 relay 未生效（回落 hero）→ 中止余链
        if i == 0 and shots[0] == CHAIN[0] and (diff < 0 or diff > CANARY_DIFF):
            print(f"  CANARY FAIL: s02 relay diff={diff} > {CANARY_DIFF} "
                  f"——疑似静默回落 hero，中止余链（fail-loud）", flush=True)
            aborted = shot_id
            continue
        prev_tail_png = this_tail_png

    print(f"[chain] run={run_id} finished"
          + (f" aborted_at={aborted}" if aborted else ""), flush=True)


if __name__ == "__main__":
    main()
