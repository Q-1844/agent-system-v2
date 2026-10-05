# -*- coding: utf-8 -*-
"""Phase48 D：多人物、道具交接与人物对白驱动（隔离项目 P48_D_Cast）。

逐级真实测试（执行书 PHASE48_D_MULTI_CHARACTER_PROP_AND_DIALOGUE.md）：
  L1 同框身份 / L2 空间与遮挡 / L3 道具交接 / L4 单人短对白 / L5 双人轮流对白 / L6 跨镜同一角色
低级失败先诊断不堆复杂动作。首帧用批准双 Look 参考（i2i，正式图像路径）；
对白走 native_dialogue_control + 内联说话人（B 批验证的路径）。
道具/空间账本与逐镜起止状态入证据 JSON；ASR 对白核对沿用 B 批双阈值。
用法：python _p48_d_driver.py fixture|frames|videos|report
"""
import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://localhost:8000"
PROJ = "P48_D_Cast"
ROOT = Path(__file__).parent
EVID = ROOT / "_p48_d_evidence.json"
sys.path.insert(0, str(ROOT))

CHARS = {"小满": "girl with twin braids, yellow raincoat, blue overalls, freckles",
         "阿岩": "boy with spiky black hair, blue-white striped t-shirt, dark green overalls, band-aid on cheek"}
PROP = "red small flashlight"

# 6 级 × 3 镜（重复观察随机性）= 18 核心镜；对白镜带说话人内联
LEVELS = [
    ("L1_sameframe", [
        ("小满、阿岩在木屋门口并肩站着，各自看向镜头方向",
         "two characters side by side at a wooden cabin door: {xiaoman}; {ayan}; both face the camera",
         "they stand still, the girl waves briefly",
         ""),
        ("小满、阿岩在院子里一起把一筐苹果抬上木桌",
         "two characters in a yard lifting a basket of apples onto a wooden table: {xiaoman} on the left, {ayan} on the right",
         "they lift the basket together and set it down",
         ""),
        ("小满、阿岩在篱笆边并肩向左走",
         "two characters walking side by side along a fence: {xiaoman} and {ayan}",
         "they walk a few steps together",
         ""),
    ]),
    ("L2_spatial", [
        ("阿岩在小满身后半步，小满转身",
         "{ayan} stands half a step behind {xiaoman}, she begins to turn around",
         "she turns to face him, he steps aside slightly",
         ""),
        ("两人交错走过狭窄的小桥",
         "two characters crossing a narrow plank bridge in opposite directions: {xiaoman} and {ayan}",
         "they pass each other, momentarily occluding one another",
         ""),
        ("阿岩弯腰钻到桌前，小满站在桌后被桌沿半遮",
         "{ayan} crouches in front of a table, {xiaoman} stands behind the table partially occluded by its edge",
         "he picks up a flashlight from under the table, she leans slightly to see",
         ""),
    ]),
    ("L3_handoff", [
        ("小满把红色小手电筒递给阿岩，两人手部接触",
         "close two-shot: {xiaoman} hands the {prop} to {ayan}, their hands meet in frame",
         "she passes the flashlight, he takes it, hands touch briefly",
         ""),
        ("（交接后）阿岩持红色小手电筒照亮木箱内部",
         "{ayan} holds the {prop} and shines it into an open wooden crate",
         "he switches it on and sweeps the light inside the crate",
         ""),
        ("（交接后）小满空手指向木箱，阿岩持手电筒在旁",
         "{xiaoman} points at the crate with an empty hand, {ayan} beside her still holds the {prop}",
         "she points, he raises the flashlight to where she points",
         ""),
    ]),
    ("L4_mono_dialogue", [
        ("小满面对镜头说一句短句，阿岩在旁沉默",
         "two-shot: {xiaoman} faces the camera and speaks, {ayan} stands beside her silent",
         "she speaks to the camera, he stays quiet and still",
         "小满：我们要在天黑前找到出口。"),
        ("阿岩摇头回应动作（不说话），小满等待",
         "two-shot: {ayan} shakes his head slowly, {xiaoman} watches him",
         "he shakes his head, she waits",
         ""),
        ("小满再次开口补充一句，阿岩点头",
         "two-shot: {xiaoman} speaks again, {ayan} nods",
         "she speaks, he nods once",
         "小满：那就走左边的小路。"),
    ]),
    ("L5_duo_dialogue", [
        ("阿岩先开口一句，小满听",
         "two-shot: {ayan} speaks first, {xiaoman} listens",
         "he speaks, she listens attentively",
         "阿岩：左边有水声，可能是河。"),
        ("小满回一句并做手势，阿岩看她的手势",
         "two-shot: {xiaoman} replies with a hand gesture, {ayan} looks at her hand",
         "she replies and points left, he follows her gesture",
         "小满：那就顺着水声走。"),
        ("阿岩结尾一句并举起手电筒",
         "two-shot: {ayan} says a final line and raises the {prop}",
         "he speaks and lifts the flashlight",
         "阿岩：好，天黑前出发。"),
    ]),
    ("L6_cross_shot", [
        ("（跨镜A）小满近景：同一句台词在两个镜头中延续",
         "medium close-up of {xiaoman} speaking",
         "she speaks the same line with the same voice",
         "小满：小心脚下，木板松了。"),
        ("（跨镜B）同场景另一机位，小满继续说完同一句",
         "reverse angle over the plank bridge, {xiaoman} continuing to speak",
         "she finishes the sentence, same voice and tone",
         "小满：……踩外侧就不会断。"),
        ("（跨镜C）阿岩回应镜头，声线与前镜一致",
         "medium shot of {ayan} responding",
         "he responds to her, steady voice",
         "阿岩：知道了，你先走。"),
    ]),
]

DIALOGUE_LINES = {}


def api(method, path, payload=None, params=""):
    url = f"{BASE}{path}{params}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        body = r.read().decode("utf-8")
    return json.loads(body) if body else {}


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def load_evid():
    if EVID.exists():
        return json.loads(EVID.read_text(encoding="utf-8"))
    return {"shots": {}}


def save_evid(ev):
    EVID.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")


def store():
    from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
    from src.utils.project_manager import ProjectManager
    from src.core.store import ProjectStore
    return ProjectStore(Path(__import__("os").path.join(".", "projects", PROJ)))


def look_refs_b64():
    ev = load_evid()
    out = []
    for lk in ev.get("looks", []):
        cover = lk.get("cover")
        p = ROOT / "shared_library" / "media" / f"{cover}.png"
        if not p.exists():
            cands = list((ROOT / "projects" / PROJ / "output" / "resources" / "looks" /
                          lk["look_id"] / "generations").glob("*/candidate_01.png"))
            if cands:
                p = cands[0]
        if p.exists():
            out.append(f"data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}")
    return out


def fmt(template: str) -> str:
    return template.format(xiaoman=CHARS["小满"], ayan=CHARS["阿岩"], prop=PROP)


def wait_job(job_id, timeout=2400):
    t0 = time.time()
    while time.time() - t0 < timeout:
        jobs = api("GET", f"/api/project/{PROJ}/jobs").get("jobs", [])
        me = next((j for j in jobs if j.get("job_id") == job_id), None)
        if me is None:
            return {"status": "done", "wall_s": round(time.time() - t0, 1)}
        st = me.get("state") or me.get("status")
        if st in ("done", "success", "completed"):
            return {"status": st, "wall_s": round(time.time() - t0, 1)}
        if st in ("failed", "error", "cancelled", "canceled"):
            return {"status": st, "wall_s": round(time.time() - t0, 1),
                    "error": str(me.get("error") or me.get("message"))[:300]}
        time.sleep(5)
    return {"status": "timeout", "wall_s": round(time.time() - t0, 1)}


def phase_fixture():
    ev = load_evid()
    st = store()
    from src.core.models import Scene, Shot
    project = st.load()
    sc = next((x for x in project.scenes if x.scene_id == "s01"), None)
    if sc is None:
        sc = Scene(scene_id="s01", summary="D批多人物/道具/对白逐级测试", characters=[],
                   shots=[])
        project.scenes = list(project.scenes) + [sc]
    exist = {s.shot_id for s in sc.shots}
    n = len(sc.shots)
    ledger = ev.setdefault("shots", {})
    for level, shots in LEVELS:
        for i, (desc, ff_tpl, motion, dialogue) in enumerate(shots, start=1):
            sid = f"s01_c{n + 1:02d}"
            n += 1
            DIALOGUE_LINES[sid] = dialogue
            ent = ledger.setdefault(sid, {"level": level, "desc": desc,
                                          "dialogue": dialogue,
                                          "dialogue_speaker": dialogue.split("：")[0] if dialogue else None})
            # 逐镜起止状态账本（结构字段以现有 canonical 为准，无结构实体则记录在案）
            ent["start_state"] = desc
            ent["motion"] = motion
            if sid in exist:
                continue
            chars = []
            if dialogue:
                chars = [dialogue.split("：")[0]]
            elif "小满" in desc and "阿岩" in desc:
                chars = ["小满", "阿岩"]
            elif "小满" in desc:
                chars = ["小满"]
            elif "阿岩" in desc:
                chars = ["阿岩"]
            sc.shots = list(sc.shots) + [Shot(
                shot_id=sid, scene_id="s01", index=n,
                description=f"[D批·{level}] {desc}",
                prompt_en=fmt(ff_tpl), motion=fmt(motion),
                dialogue=dialogue, characters=chars,
                camera="static camera", lighting="natural light",
                link_mode="i2v", opening_source="independent",
                target_duration_s=4.0)]
    st.save(project)
    ev["fixture"] = {"dialogue_lines": DIALOGUE_LINES,
                     "levels": [l for l, _ in LEVELS]}
    save_evid(ev)
    log("fixture done, shots:", len(sc.shots))


def gen_ff(shot_id, prompt):
    """双 Look 参考 i2i 首帧（正式 provider 路径，记录回执）。"""
    from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
    from src.pipeline.controller import PipelineController
    import asyncio
    client = PipelineController().agnes_client

    async def _g():
        return await client.generate_image(prompt, image_refs=look_refs_b64(),
                                           size_tier="1K", ratio="16:9", want_b64=True)
    r = asyncio.run(_g())
    b64 = r.get("b64")
    if not b64 and r.get("url"):
        b64 = base64.b64encode(urllib.request.urlopen(r["url"], timeout=60).read()).decode()
    out = ROOT / "projects" / PROJ / "output" / "frames" / f"{shot_id}_ff_custom.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(b64))
    return {"local": str(out), "sha16": __import__("hashlib").sha256(out.read_bytes()).hexdigest()[:16]}


def inject_ff(st, shot_id, img_local: Path, note):
    import asyncio
    from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
    from deps import get_rehost_host
    from src.core.models import Candidate, ImageBatch, ShotStatus
    url = None
    for attempt in range(3):
        try:
            url = asyncio.run(get_rehost_host().upload(str(img_local)))
            break
        except Exception as e:
            if attempt == 2:
                raise
            log(f"rehost retry {attempt+1}: {str(e)[:100]}")
            time.sleep(20)
    cid = f"c_inj_{shot_id}_{int(time.time())}"
    batch = ImageBatch(batch_id=f"ffb_inj_{shot_id}_{int(time.time())}", kind="first_frame",
                       prompt_snapshot=f"[injected] {note}",
                       candidates=[Candidate(candidate_id=cid, local=str(img_local),
                                             url=url, status=ShotStatus.DONE.value,
                                             created_at=time.time())])
    from src.core.models import Asset

    def mut(s, b=batch, c=cid):
        s.first_frame_batches = list(s.first_frame_batches) + [b]
        s.first_frame.candidates = [Asset(local=x.local, url=x.url, status=x.status)
                                    for x in b.candidates]
        s.first_frame.selected_index = 0
        setattr(s.first_frame, "status", ShotStatus.DONE.value)
        s.selected_first_frame = c
    st.update_shot(shot_id, mut)
    return {"candidate_id": cid, "url": url}


def phase_frames():
    ev = load_evid()
    st = store()
    for sid, ent in ev["shots"].items():
        if ent.get("ff_ready"):
            continue
        sh = st.load().get_shot(sid)
        rec = ent.get("ff_receipt")
        if rec and rec.get("local") and (ROOT / rec["local"]).exists():
            pass  # 已有产物直接复用（重跑不重复烧图）
        else:
            ent["ff_receipt"] = gen_ff(sid, sh.prompt_en)
            save_evid(ev)
            rec = ent["ff_receipt"]
        p = ROOT / rec["local"]
        ent["ff_injection"] = inject_ff(st, sid, p, "双 Look 参考重建首帧（多人物夹具）")
        ent["ff_ready"] = True
        save_evid(ev)
        log("ff ready", sid)


def phase_videos():
    ev = load_evid()
    st = store()
    for sid, ent in ev["shots"].items():
        if ent.get("video_version"):
            continue
        if not ent.get("video_job"):
            extra = ""
            if ent.get("dialogue"):
                extra = "?native_dialogue_control=true&native_dialogue_language=zh"
            j = api("POST", f"/api/project/{PROJ}/shot/{sid}/video{extra}")
            ent["video_job"] = j.get("job_id")
            save_evid(ev)
            log("video submitted", sid, ent["level"])
        r = wait_job(ent["video_job"])
        ent["video_result"] = r
        save_evid(ev)
        log("video", sid, r.get("status"), r.get("error", ""))
        if r.get("status") == "done":
            sh = st.load().get_shot(sid)
            if sh.video_versions:
                vv = sh.video_versions[-1]
                api("POST", f"/api/project/{PROJ}/shot/{sid}/video/set-final",
                    {"version_id": vv.version_id})
                ent["video_version"] = vv.version_id
                ent["video_local"] = vv.local
                save_evid(ev)


def phase_report():
    ev = load_evid()
    for sid, ent in sorted(ev["shots"].items()):
        print(sid, ent["level"], ent.get("video_result", {}).get("status"),
              ent.get("video_version"))


if __name__ == "__main__":
    ph = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"fixture": phase_fixture, "frames": phase_frames, "videos": phase_videos,
     "report": phase_report}.get(ph, phase_report)()
