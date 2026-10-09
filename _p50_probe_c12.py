# -*- coding: utf-8 -*-
"""Phase50 A3 C1/C2 并发探针（PHASE50_A §5）。

C1 基线：正式入口（REST POST /shot/{id}/video → JobQueue）提交 sc01_c01（10s i2v）；
C2 首轮：sc01 在途未完成时提交独立镜头 sc02_c01（不同场景、互不读取生成结果）。
计时证据：cache/p50_timing_ledger.jsonl + jobs 终态 + ffprobe。

用法：python _p50_probe_c12.py setup|choose|submit2|wait <jid1> <jid2>|analyze <j1> <j2>
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, ".")

BASE = "http://127.0.0.1:8000"
PROJ = "P50_Probe"


def req(path, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data,
                               headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.load(resp)


def setup():
    """建项目 + 2 个独立镜头（无人物、互不依赖）+ 提交首帧生成。"""
    try:
        req(f"/api/projects/create?name={PROJ}&render_family=photorealistic"
            "&art_direction=%E5%86%99%E5%AE%9E%E6%B5%B7%E6%B4%8B%E6%8E%A2%E9%92%88",
            None, "POST")
        print("project created")
    except Exception as e:
        print("create:", e)

    # 夹具层：直接用 ProjectStore 写 scene/shot（视频提交走正式 REST 入口）
    from src.core.store import ProjectStore
    from src.core.models import Scene, Shot, ImageAsset
    pdir = Path(f"projects/{PROJ}")
    store = ProjectStore(pdir)
    p = store.load()
    p.scenes = [
        Scene(scene_id="sc01", title="黎明渔港",
              summary="清晨的渔港码头，水平线泛起橙色晨光，薄雾。"),
        Scene(scene_id="sc02", title="空旷海面",
              summary="开阔无人的海面，黄昏侧光，波浪起伏。"),
    ]
    s1 = Shot(shot_id="sc01_c01", scene_id="sc01", index=1, characters=[],
              first_frame=ImageAsset(), last_frame=ImageAsset())
    s1.description = ("Wide establishing shot at dawn: a small white fishing boat moored "
                      "at a quiet harbor pier, orange light on the horizon, thin morning "
                      "mist over calm water, photorealistic, cinematic")
    s1.target_duration_s = 10
    s2 = Shot(shot_id="sc02_c01", scene_id="sc02", index=1, characters=[],
              first_frame=ImageAsset(), last_frame=ImageAsset())
    s2.description = ("Empty open sea at dusk: gentle rolling waves under warm side light, "
                      "a few seabirds gliding over the swell, no people, no boats, "
                      "photorealistic, cinematic")
    s2.target_duration_s = 10
    p.scenes[0].shots = [s1]
    p.scenes[1].shots = [s2]
    store.save(p)
    print("scenes/shots written")

    for sid in ("sc01_c01", "sc02_c01"):
        r = req(f"/api/project/{PROJ}/shot/{sid}/first-frame",
                {"variants": 1, "force": False}, "POST")
        print("ff job:", sid, r["job_id"])


def _wait_ff(timeout=300):
    from src.core.store import ProjectStore
    store = ProjectStore(Path(f"projects/{PROJ}"))
    t0 = time.time()
    while time.time() - t0 < timeout:
        p = store.load()
        ok = True
        for sid in ("sc01_c01", "sc02_c01"):
            sh = p.get_shot(sid)
            if not sh or not sh.first_frame.candidates:
                ok = False
        if ok:
            return True
        time.sleep(4)
    return False


def choose():
    """等首帧完成 → 选定候选 0（视频 handler 会对 local-only 首帧自动 rehost）。"""
    if not _wait_ff():
        print("first frames not ready in time")
        return
    from src.core.store import ProjectStore
    store = ProjectStore(Path(f"projects/{PROJ}"))
    p = store.load()
    for sid in ("sc01_c01", "sc02_c01"):
        sh = p.get_shot(sid)
        cand = sh.first_frame.candidates[0]
        r = req(f"/api/project/{PROJ}/shot/{sid}/first-frame/choose",
                {"candidate_id": cand.candidate_id}, "POST")
        print("chosen", sid, "->", r.get("status", r))


def _job(job_id):
    for j in req(f"/api/project/{PROJ}/jobs").get("jobs", []):
        if j.get("job_id") == job_id:
            return j
    return None


def submit2(gap_s=18):
    """C1 提交 sc01；gap_s 秒后（sc01 在途）C2 提交 sc02。"""
    j1 = req(f"/api/project/{PROJ}/shot/sc01_c01/video", {}, "POST")
    print("C1 job:", j1["job_id"])
    time.sleep(gap_s)
    j2 = req(f"/api/project/{PROJ}/shot/sc02_c01/video", {}, "POST")
    print("C2 job:", j2["job_id"])
    return [j1["job_id"], j2["job_id"]]


def wait(job_ids, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        js = [_job(j) for j in job_ids]
        if all(j and j["state"] in ("done", "failed", "cancelled") for j in js):
            for j in js:
                print("final:", j["job_id"], j["state"],
                      (j.get("error") or "")[:140])
            return
        time.sleep(6)
    print("TIMEOUT")


def analyze(job_ids=None):
    from src.core.timing_ledger import read_events
    evs = [e for e in read_events() if e.get("project") == PROJ]
    by_shot = {}
    for e in evs:
        by_shot.setdefault(e.get("shot_id"), []).append(e)
    report = {}
    for sid, evl in sorted(by_shot.items()):
        m = {e["event"]: e for e in evl if "event" in e}
        ready, sub = m.get("video_ready", {}), m.get("video_submitted", {})
        comp, down = m.get("video_completed", {}), m.get("video_downloaded", {})
        report[sid] = {
            "ready_epoch": ready.get("epoch"),
            "submitted_epoch": sub.get("epoch"),
            "video_id": sub.get("video_id"),
            "completed_epoch": comp.get("epoch"),
            "remote_wait_s": (round(comp.get("epoch", 0) - sub.get("epoch", 0), 1)
                              if sub.get("epoch") and comp.get("epoch") else None),
            "download_s": (round(down.get("epoch", 0) - comp.get("epoch", 0), 1)
                           if comp.get("epoch") and down.get("epoch") else None),
            "seconds_returned": comp.get("seconds_returned"),
        }
    ivs = [(sid, r["submitted_epoch"], r["completed_epoch"])
           for sid, r in report.items()
           if r.get("submitted_epoch") and r.get("completed_epoch")]
    overlap_s = None
    if len(ivs) == 2:
        lo, hi = max(ivs[0][1], ivs[1][1]), min(ivs[0][2], ivs[1][2])
        overlap_s = round(max(0.0, hi - lo), 1)
    for sid in report:
        local = f"projects/{PROJ}/output/videos/{sid}.mp4"
        try:
            ff = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", local], capture_output=True, text=True, timeout=60)
            report[sid]["ffprobe_duration_s"] = round(
                float(json.loads(ff.stdout)["format"]["duration"]), 2)
        except Exception as e:
            report[sid]["ffprobe_error"] = str(e)[:120]
    jobs_out = {}
    for jid in (job_ids or []):
        j = _job(jid)
        if j:
            jobs_out[jid] = {"state": j["state"], "created_at": j.get("created_at"),
                             "started_at": j.get("started_at"),
                             "finished_at": j.get("finished_at"),
                             "shot": j.get("shot_id")}
    out = {"report": report, "inflight_overlap_s": overlap_s, "jobs": jobs_out}
    Path("_p50_probe_c12_result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "setup"
    if cmd == "setup":
        setup()
    elif cmd == "choose":
        choose()
    elif cmd == "submit2":
        submit2()
    elif cmd == "wait":
        wait(sys.argv[2:4])
    elif cmd == "analyze":
        analyze(sys.argv[2:4])
    else:
        print("unknown cmd", cmd)
