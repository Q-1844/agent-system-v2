# -*- coding: utf-8 -*-
"""Phase48 E 复验第二轮：多人物道具与对白（独立项目/链/故事）。

夹具：奶奶（灰髻深布衫）递剪刀（柄朝对方）→ 孙女（红头绳粉布衫）双手接过道谢。
两镜 native 对白（spoken_language=zh）：s01_c01=「剪刀拿好，别伤着手。」（奶奶）
s01_c02=「谢谢奶奶！」（孙女）。检查：交接方向/跨镜声线/ASR 逐字/无旁白。

用法：python _p48_e_rv2.py fixture|frames|run
"""
import base64
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
PROJ = "P48_E_RV2"
BASE = "http://localhost:8000"

REF_PROMPT = ("Two Chinese family members standing side by side, full body, clean flat 2D "
              "animation style: on the left a grandmother in her 60s with grey hair in a "
              "low bun, round face, kind eyes, wearing a dark brown traditional cloth "
              "jacket and grey trousers; on the right her 8-year-old granddaughter with a "
              "red hair tie ponytail, cheerful round face, wearing a pink cloth jacket and "
              "red skirt; the grandmother holds a pair of old iron scissors; plain light "
              "background, crisp outlines, flat colors, no text")
FF1_PROMPT = ("Close two-shot: the grey-bunned grandmother in dark brown jacket safely "
              "hands over a pair of iron scissors: her right hand grips the folded "
              "BLADES tightly in her closed palm, offering the RING HANDLES toward the "
              "girl; the red-ponytail girl in pink jacket reaches out with both hands "
              "to take the handles; courtyard background, clean flat 2D animation, "
              "crisp outlines, no text")
FF2_PROMPT = ("Medium shot: the red-ponytail girl in pink jacket happily holds the iron "
              "scissors by their ring handles in both hands, blades safely folded and "
              "pointing away from her, held at chest height, mouth open saying thanks "
              "with a bright smile; the grandmother stands beside her with hands folded, "
              "smiling; courtyard background, clean flat 2D animation, crisp outlines, "
              "no text")


def log(*msg):
    print(f"[{time.strftime('%H:%M:%S')}]", *msg, flush=True)


def api(method, path, payload=None, params=None):
    import urllib.parse
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}{q}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        body = r.read().decode()
    return json.loads(body) if body else {}


def agnes():
    import os
    for line in open(ROOT / ".env", encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    from src.pipeline.controller import PipelineController
    return PipelineController().agnes_client


def gen_image(prompt, refs_b64=None):
    import asyncio
    client = agnes()

    async def _g():
        if refs_b64:
            return await client.generate_image(prompt, image_refs=refs_b64,
                                               size_tier="1K", ratio="16:9",
                                               want_b64=True)
        return await client.generate_image(prompt, size_tier="1K",
                                           ratio="16:9", want_b64=True)
    r = asyncio.run(_g())
    b64 = r.get("b64")
    if not b64 and r.get("url"):
        b64 = base64.b64encode(urllib.request.urlopen(r["url"], timeout=90).read()).decode()
    raw = base64.b64decode(b64)
    return raw, {"sha16": hashlib.sha256(raw).hexdigest()[:16]}


def phase_fixture():
    projs = api("GET", "/api/projects")
    if PROJ not in (projs.get("projects") or []):
        api("POST", "/api/projects/create", params={
            "name": PROJ, "render_family": "2d_animation",
            "art_direction": "clean flat 2D animation, crisp outlines, flat colors, "
                             "theatrical anime feature look"})
    api("PUT", f"/api/project/{PROJ}/native-av-settings",
        payload={"spoken_language": "zh"})
    from src.core.store import ProjectStore
    from src.core.models import Scene, Shot
    st = ProjectStore(ROOT / "projects" / PROJ)
    project = st.load()
    if not any(x.scene_id == "s01" for x in project.scenes):
        sc = Scene(scene_id="s01", summary="复验二：递剪刀（柄朝对方）与道谢对白",
                   characters=["奶奶", "孙女"], shots=[])
        project.scenes = list(project.scenes) + [sc]
    sc = next(x for x in project.scenes if x.scene_id == "s01")
    have = {s.shot_id for s in sc.shots}
    shots = [
        ("s01_c01", "递接瞬间：奶奶把剪刀柄朝对方递出，孙女双手来接",
         "剪刀拿好，别伤着手。", "奶奶", 4.5),
        ("s01_c02", "孙女双手握剪贴胸，开口道谢，奶奶含笑",
         "谢谢奶奶！", "孙女", 3.0),
    ]
    n = 0
    for sid, desc, dialogue, speaker, dur in shots:
        if sid in have:
            continue
        n += 1
        sc.shots = list(sc.shots) + [Shot(
            shot_id=sid, scene_id="s01", index=n, description=f"[复验二] {desc}",
            prompt_en=REF_PROMPT, motion="two-shot, gentle handover",
            dialogue=dialogue, characters=[speaker],
            camera="static two-shot", lighting="daylight",
            link_mode="i2v", opening_source="independent",
            target_duration_s=dur)]
    st.save(project)
    log("fixture shots ready:", len(sc.shots))


def phase_frames():
    from src.core.store import ProjectStore
    from src.core.models import Candidate, ImageBatch, ShotStatus, Asset
    st = ProjectStore(ROOT / "projects" / PROJ)
    out_dir = ROOT / "projects" / PROJ / "output" / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    ev_path = ROOT / "_p48_e_rv2_evidence.json"
    ev = json.loads(ev_path.read_text(encoding="utf-8")) if ev_path.exists() else {}
    if not ev.get("ref_png"):
        raw, rec = gen_image(REF_PROMPT)
        (out_dir / "twochar_ref.png").write_bytes(raw)
        ev["ref_png"] = str(out_dir / "twochar_ref.png")
        ev["ref"] = rec
        ev_path.write_text(json.dumps(ev, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    refs_b64 = [base64.b64encode((ROOT / ev["ref_png"]).read_bytes()).decode()]
    for sid, prompt in (("s01_c01", FF1_PROMPT), ("s01_c02", FF2_PROMPT)):
        if ev.get(sid, {}).get("ff_ready"):
            continue
        raw, rec = gen_image(prompt, refs_b64=refs_b64)
        img = out_dir / f"{sid}_ff_custom.png"
        img.write_bytes(raw)
        rec["local"] = str(img)
        import asyncio
        from deps import get_rehost_host
        url = asyncio.run(get_rehost_host().upload(str(img)))
        cid = f"c_inj_{sid}_{int(time.time())}"
        batch = ImageBatch(batch_id=f"ffb_inj_{sid}_{int(time.time())}",
                           kind="first_frame",
                           prompt_snapshot=f"[复验二夹具] {prompt[:70]}",
                           candidates=[Candidate(candidate_id=cid, local=str(img),
                                                 url=url, status=ShotStatus.DONE.value,
                                                 created_at=time.time())])

        def mut(s, b=batch, c=cid):
            s.first_frame_batches = list(s.first_frame_batches) + [b]
            s.first_frame.candidates = [Asset(local=x.local, url=x.url, status=x.status)
                                        for x in b.candidates]
            s.first_frame.selected_index = 0
            setattr(s.first_frame, "status", ShotStatus.DONE.value)
            s.selected_first_frame = c
        st.update_shot(sid, mut)
        ev.setdefault(sid, {})["ff_ready"] = True
        ev[sid]["receipt"] = rec
        ev_path.write_text(json.dumps(ev, ensure_ascii=False, indent=1),
                           encoding="utf-8")
        log("ff injected:", sid, rec["sha16"])


def phase_run():
    body = {"workflowId": "wf_e_rv2", "params": {
        "videoObjective": ("两镜为祖孙递剪刀与道谢：镜头1奶奶递剪刀（刀刃握在自己掌心、剪刀柄朝对方——"
                           "交接方向是本镜核心）；镜头2孙女双手握剪开口道谢（native 对白，语言=中文）。"
                           "按 relay skill v1.4.0 起步决策入口逐镜选择接续方式；完成后核对：①剪刀交接方向；"
                           "②两人跨镜身份一致；③对白口型与声线（老年女声vs少女声）。无旁白。"),
        "videoCount": 2}}
    out = api("POST", f"/api/project/{PROJ}/workflow/templates/W05/create", body)
    print("def:", out.get("workflowId"), "valid:", (out.get("validation") or {}).get("ok"))
    out = api("POST", f"/api/project/{PROJ}/workflow/definitions/wf_e_rv2/runs",
              {"idempotencyKey": "p48e_rv2_v1"})
    run = out.get("run") or {}
    print("run:", run.get("runId"), run.get("status"))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("fixture", "all"):
        phase_fixture()
    if cmd in ("frames", "all"):
        phase_frames()
    if cmd in ("run", "all"):
        phase_run()
