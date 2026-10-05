# -*- coding: utf-8 -*-
"""Phase48 B2：12 条原生音频样本生产驱动（隔离项目 P48_B_Audio）。

夹具（场景/镜头字段）由本脚本经 ProjectStore 直接写入（夹具准备，A10 先例）；
生成一律走系统正式 REST 管线：first-frame 入队 → choose → video 入队 → set-final。
每步落证据：_p48_b_samples_evidence.json。
用法：python _p48_b_samples.py [phase]   phase ∈ create|frames|videos|status|report
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://localhost:8000"
PROJ = "P48_B_Audio"
ROOT = Path(__file__).parent
EVID = ROOT / "_p48_b_samples_evidence.json"

# 12 条样本设计：意图类别 → 镜头字段（生成结果按实测重新分类，不预设成立）
SAMPLES = [
    ("s01_c01", "action_sfx", "特写：一只手把厚书重重合在木桌上，尘埃微扬",
     "close-up shot, a hand slams a thick book shut on a wooden table, dust motes rise, crisp single action",
     "the book slams shut with a single sharp motion"),
    ("s01_c02", "action_sfx", "中景：一个人快步走过空旷木地板走廊",
     "medium shot, a person walks briskly across an empty wooden floor corridor, footsteps in rhythm",
     "steady footsteps crossing the floor"),
    ("s02_c01", "ambience", "远景：雨天小城街头，行人撑伞缓行，雨丝不断",
     "wide shot, rainy small-town street, pedestrians with umbrellas moving slowly, continuous rainfall",
     "gentle continuous rain over the street"),
    ("s02_c02", "ambience", "夜晚森林空镜：月光下虫鸣此起彼伏，树叶轻摇",
     "night forest scene, moonlight, leaves swaying gently, a quiet wild atmosphere",
     "leaves sway softly in the night breeze"),
    ("s03_c01", "music_mixed", "中景：街头艺人站在街角拉小提琴，行人驻足",
     "medium shot, a street musician plays the violin at a street corner, pedestrians pause to listen",
     "the musician bows the violin continuously"),
    ("s03_c02", "music_mixed", "特写：一台复古收音机在木柜上播放歌曲，指针微颤",
     "close-up, a vintage radio on a wooden cabinet plays a song, the tuning needle trembles slightly",
     "the radio plays music, needle trembles"),
    ("s04_c01", "dialogue", "近景：年轻女子面对镜头微笑开口说话，唇部动作清晰",
     "medium close-up, a young woman faces the camera, smiles and speaks a short line, clear lip movement",
     "she speaks to the camera"),
    ("s04_c02", "dialogue", "近景：老木匠放下刨子，抬头说了一句话",
     "medium close-up, an old carpenter puts down his plane, looks up and speaks a short line",
     "he speaks while looking up"),
    ("s05_c01", "likely_silent", "静物空镜：桌上一杯清水，几乎无运动，光线稳定",
     "still-life shot, a glass of clear water on a table, almost no motion, stable light",
     "almost imperceptible motion, water surface perfectly still"),
    ("s05_c02", "likely_silent", "大远景：雪原空镜，一片洁白，画面几乎静止",
     "extreme wide shot, a vast snowfield, pure white, nearly still frame",
     "nearly still, faint atmospheric shimmer only"),
    ("s06_c01", "narration_risk", "航拍视角缓缓推进城市天际线，晨光初现",
     "aerial view slowly pushing toward a city skyline at dawn",
     "slow aerial push toward the skyline"),
    ("s06_c02", "narration_risk", "俯拍特写：做一道菜的流程，切菜、下锅、翻炒",
     "top-down close shots of cooking: chopping vegetables, into the pan, stir-frying",
     "chopping, then sizzling in the pan, then stir-frying"),
]

DIALOGUE_LINES = {
    "s04_c01": "你好，今天天气真好。",
    "s04_c02": "这把椅子，还要再修一天。",
}


def api(method, path, payload=None, params=""):
    url = f"{BASE}{path}{params}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8")
    return json.loads(body) if body else {}


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def load_evid():
    if EVID.exists():
        return json.loads(EVID.read_text(encoding="utf-8"))
    return {"samples": {}}


def save_evid(ev):
    EVID.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")


def wait_job(job_id, timeout=1800):
    """轮询项目 jobs 列表（/api/task/{id} 对已完成任务返回 not_found，不可用）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        jobs = api("GET", f"/api/project/{PROJ}/jobs").get("jobs", [])
        me = next((j for j in jobs if j.get("job_id") == job_id), None)
        if me is None:
            return {"status": "done", "wall_s": round(time.time() - t0, 1),
                    "result": {"note": "job not in list"}}
        st = me.get("state") or me.get("status")
        if st in ("done", "success", "completed"):
            return {"status": st, "wall_s": round(time.time() - t0, 1),
                    "message": me.get("message")}
        if st in ("failed", "error", "cancelled", "canceled"):
            return {"status": st, "wall_s": round(time.time() - t0, 1),
                    "error": str(me.get("error") or me.get("message"))[:400]}
        time.sleep(4)
    return {"status": "timeout", "wall_s": round(time.time() - t0, 1)}


def shot_payload(shot_id, scene_id, idx, category, desc_cn, ff_en, motion_en):
    d = DIALOGUE_LINES.get(shot_id, "")
    return {
        "shot_id": shot_id, "scene_id": scene_id, "index": idx,
        "description": f"[B批音频样本·{category}] {desc_cn}",
        "prompt_en": ff_en,
        "motion": motion_en,
        "camera": "static camera" if category != "narration_risk" else "slow push-in",
        "lighting": "natural light",
        "dialogue": d,
        "shot_size": "medium shot",
        "characters": [],
        "link_mode": "i2v",
        "opening_source": "independent",
        "target_duration_s": 5.0,
    }


def phase_create():
    import urllib.parse
    qs = urllib.parse.urlencode({
        "name": PROJ, "render_family": "2d_animation",
        "art_direction": "clean flat 2D animation, consistent characters, warm palette"})
    r = api("POST", "/api/projects/create", None, f"?{qs}")
    log("project create:", json.dumps(r, ensure_ascii=False)[:200])
    # 夹具：1 场景 + 12 镜（ProjectStore 直写；生成走正式管线）
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv; load_dotenv(".env")
    from src.utils.project_manager import ProjectManager
    from src.core.store import ProjectStore
    from src.core.models import Scene, Shot
    pm = ProjectManager("./projects")  # base_dir 由 config 默认
    pdir = Path(pm.get_project_dir(PROJ))
    store = ProjectStore(pdir)
    project = store.load()
    sc = Scene(scene_id="s01", summary="B批音频样本集（12镜，分类意图见描述前缀）",
               characters=[], shots=[])
    shots = []
    for i, (sid, cat, desc, ff_en, motion) in enumerate(SAMPLES, start=1):
        shots.append(Shot(**shot_payload(sid, "s01", i, cat, desc, ff_en, motion)))
    sc.shots = shots
    project.scenes = [sc]
    project.next_scene_seq = 2
    store.save(project)
    ev = load_evid()
    ev["fixture"] = {"project": PROJ, "scene": "s01", "shots": [s[0] for s in SAMPLES],
                     "dialogue_lines": DIALOGUE_LINES}
    save_evid(ev)
    log("fixture written:", pdir)


def phase_frames():
    ev = load_evid()
    for sid, *_ in SAMPLES:
        if ev["samples"].get(sid, {}).get("first_frame_chosen"):
            continue
        if not ev["samples"].get(sid, {}).get("first_frame_job"):
            j = api("POST", f"/api/project/{PROJ}/shot/{sid}/first-frame", {"variants": 1})
            ev["samples"].setdefault(sid, {})["first_frame_job"] = j.get("job_id")
            save_evid(ev)
            log("ff submitted", sid, j.get("job_id"))
            r = wait_job(j["job_id"])
            ev["samples"][sid]["first_frame_result"] = r
            save_evid(ev)
            log("ff done", sid, r["status"], r.get("wall_s"))
        # 选第一个候选（无论是否本轮提交——修复中断续跑）
        from src.core.store import ProjectStore
        from src.utils.project_manager import ProjectManager
        pm = ProjectManager("./projects")
        store = ProjectStore(Path(pm.get_project_dir(PROJ)))
        sh = store.load().get_shot(sid)
        cand = None
        if sh.first_frame_batches and sh.first_frame_batches[0].candidates:
            cand = sh.first_frame_batches[0].candidates[0].candidate_id
        if cand:
            ch = api("POST", f"/api/project/{PROJ}/shot/{sid}/first-frame/choose",
                     {"candidate_id": cand})
            ev["samples"][sid]["first_frame_chosen"] = ch
            save_evid(ev)
            log("ff chosen", sid, cand)
        else:
            log("!! no candidate for", sid)


def phase_videos():
    ev = load_evid()
    for sid, cat, *_ in SAMPLES:
        info = ev["samples"].get(sid, {})
        if info.get("video_job") and info.get("video_result", {}).get("status") in (
                "done", "success", "completed"):
            continue
        extra = {}
        if cat == "dialogue":
            extra["native_dialogue_control"] = "true"
            extra["native_dialogue_language"] = "zh"
        j = api("POST", f"/api/project/{PROJ}/shot/{sid}/video", None,
                "?" + "&".join(f"{k}={v}" for k, v in extra.items()) if extra else "")
        ev["samples"].setdefault(sid, {})["video_job"] = j.get("job_id")
        save_evid(ev)
        log("video submitted", sid, j.get("job_id"))
        r = wait_job(j["job_id"], timeout=2400)
        ev["samples"][sid]["video_result"] = r
        save_evid(ev)
        log("video done", sid, r["status"], r.get("wall_s"))
        if r["status"] in ("done", "success", "completed"):
            try:
                # 找最新 version 并 set-final
                from src.core.store import ProjectStore
                from src.utils.project_manager import ProjectManager
                pm = ProjectManager("./projects")
                store = ProjectStore(Path(pm.get_project_dir(PROJ)))
                sh = store.load().get_shot(sid)
                if sh.video_versions:
                    vv = sh.video_versions[-1].version_id
                    sf = api("POST", f"/api/project/{PROJ}/shot/{sid}/video/set-final",
                             {"version_id": vv})
                    ev["samples"][sid]["final_set"] = sf
                    ev["samples"][sid]["video_version"] = vv
                    save_evid(ev)
                    log("final set", sid, vv)
            except Exception as e:
                log("set-final failed", sid, str(e)[:200])


def phase_report():
    ev = load_evid()
    rows = []
    for sid, cat, desc, ff_en, motion in SAMPLES:
        info = ev["samples"].get(sid, {})
        rows.append({"shot": sid, "intent": cat, "ff": info.get("first_frame_result", {}).get("status"),
                     "video": info.get("video_result", {}).get("status"),
                     "version": info.get("video_version")})
    for r in rows:
        log(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    ph = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"create": phase_create, "frames": phase_frames,
     "videos": phase_videos, "report": phase_report}.get(ph, phase_report)()
