# -*- coding: utf-8 -*-
"""Phase48 E 复验第一轮：连续动作与身份（独立项目/链/故事）。

链路：P48_E_RV1（2d_animation）→ 夹具 2 镜（端碗下石阶：中段→末阶落地）→
i2i 首帧（同一 Look 参考锚定身份，正式 provider 路径落回执）→ W05（绑定 relay
skill v1.4.0）→ 视频生成 → 审定选用。

用法：
  python _p48_e_rv1.py fixture   # 建项目+夹具镜
  python _p48_e_rv1.py frames    # 生成+注入首帧
  python _p48_e_rv1.py run       # 建 W05 定义并启动
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
PROJ = "P48_E_RV1"
BASE = "http://localhost:8000"

LOOK_PROMPT = ("Character reference sheet, single 10-year-old Chinese girl named A'ruo, "
               "double bun hairstyle with red hair ties, powder-blue traditional buttoned "
               "jacket and dark grey trousers, round gentle face, holding a white porcelain "
               "bowl of water with both hands, full body front view, clean flat 2D animation "
               "style, crisp outlines, flat colors, plain light background, no text")
FF1_PROMPT = ("Same girl A'ruo (double buns, red hair ties, powder-blue jacket, dark grey "
              "trousers) walking down mid-stone-steps of a village stairway, holding the "
              "porcelain bowl of water steadily with both hands, eyes on the bowl, side "
              "view, stone steps and village wall background, clean flat 2D animation, "
              "crisp outlines, no text")
FF2_PROMPT = ("Same girl A'ruo (double buns, red hair ties, powder-blue jacket, dark grey "
              "trousers) landing on the last stone step in a soft knee-bend缓冲 crouch, "
              "the porcelain bowl of water sloshing slightly but not spilling, determined "
              "expression, front three-quarter view, same village stairway bottom, clean "
              "flat 2D animation, crisp outlines, no text")


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
    from dotenv import load_dotenv
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
    return raw, {"sha16": hashlib.sha256(raw).hexdigest()[:16],
                 "task": (r.get("task_id") or r.get("video_id") or "")[:24]}


def phase_fixture():
    projs = api("GET", "/api/projects")
    if PROJ not in (projs.get("projects") or []):
        api("POST", "/api/projects/create", params={
            "name": PROJ, "render_family": "2d_animation",
            "art_direction": "clean flat 2D animation, crisp outlines, flat colors, "
                             "theatrical anime feature look"})
        log("project created")
    from src.core.store import ProjectStore
    from src.core.models import Scene, Shot
    st = ProjectStore(ROOT / "projects" / PROJ)
    project = st.load()
    if not any(x.scene_id == "s01" for x in project.scenes):
        sc = Scene(scene_id="s01", summary="复验一：端碗下石阶连续动作与身份",
                   characters=["阿箬"], shots=[])
        project.scenes = list(project.scenes) + [sc]
    sc = next(x for x in project.scenes if x.scene_id == "s01")
    have = {s.shot_id for s in sc.shots}
    shots = [
        ("s01_c01", "石阶中段：阿箬双手端碗稳步下行，碗水平稳",
         "medium shot, side view, steady walk, bowl level"),
        ("s01_c02", "末阶落地：阿箬屈膝缓冲轻落地，碗中水晃而不洒",
         "medium shot, front three-quarter, soft landing, water sloshing"),
    ]
    n = 0
    for sid, desc, motion in shots:
        if sid in have:
            continue
        n += 1
        sc.shots = list(sc.shots) + [Shot(
            shot_id=sid, scene_id="s01", index=n, description=f"[复验一] {desc}",
            prompt_en=LOOK_PROMPT if n == 1 else FF2_PROMPT, motion=motion,
            characters=["阿箬"], camera="static camera", lighting="daylight",
            link_mode="i2v", opening_source="independent", target_duration_s=4.0)]
    st.save(project)
    log("fixture shots ready:", len(sc.shots))


def phase_frames():
    from src.core.store import ProjectStore
    from deps import get_rehost_host
    from src.core.models import Candidate, ImageBatch, ShotStatus, Asset
    st = ProjectStore(ROOT / "projects" / PROJ)
    out_dir = ROOT / "projects" / PROJ / "output" / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    ev_path = ROOT / "_p48_e_rv1_evidence.json"
    ev = json.loads(ev_path.read_text(encoding="utf-8")) if ev_path.exists() else {}

    look_raw = look_rec = None
    if ev.get("look_png"):
        look_raw = (ROOT / ev["look_png"]).read_bytes()
    else:
        look_raw, look_rec = gen_image(LOOK_PROMPT)
        (out_dir / "look_ref.png").write_bytes(look_raw)
        ev["look"] = look_rec
        ev["look_png"] = str(out_dir / "look_ref.png")
        ev_path.write_text(json.dumps(ev, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    refs_b64 = [base64.b64encode(look_raw).decode()]
    log("look ref ready", ev.get("look"))

    for sid, prompt in (("s01_c01", FF1_PROMPT), ("s01_c02", FF2_PROMPT)):
        if ev.get(sid, {}).get("ff_ready"):
            log(sid, "ff already ready")
            continue
        raw, rec = gen_image(prompt, refs_b64=refs_b64)
        img = out_dir / f"{sid}_ff_custom.png"
        img.write_bytes(raw)
        rec["local"] = str(img)
        url = None
        for attempt in range(3):
            try:
                url = asyncio_run_upload(str(img))
                break
            except Exception as e:
                log(f"rehost retry {attempt+1}: {str(e)[:80]}")
                time.sleep(15)
        cid = f"c_inj_{sid}_{int(time.time())}"
        batch = ImageBatch(batch_id=f"ffb_inj_{sid}_{int(time.time())}",
                           kind="first_frame",
                           prompt_snapshot=f"[复验一夹具] {prompt[:80]}",
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


def asyncio_run_upload(local):
    import asyncio
    from deps import get_rehost_host
    return asyncio.run(get_rehost_host().upload(local))


def phase_run():
    from pathlib import Path as _P
    _p = ROOT / "projects" / PROJ
    body = {"workflowId": "wf_e_rv1", "params": {
        "videoObjective": ("两镜为同地点连续动作（下阶中段→末阶落地缓冲），同一角色阿箬同一 Look。"
                           "按 relay skill v1.4.0 起步决策入口逐镜选择接续方式（端点合格且同机位同动作时"
                           "a/c/d 按制作意图选，不为连续性强行接力），完成后核对两镜身份一致性"
                           "（发式/发饰/服装/碗）与动作衔接。全片无旁白。"),
        "videoCount": 2}}
    out = api("POST", f"/api/project/{PROJ}/workflow/templates/W05/create", body)
    print("def:", out.get("workflowId"), "valid:", (out.get("validation") or {}).get("ok"))
    out = api("POST", f"/api/project/{PROJ}/workflow/definitions/wf_e_rv1/runs",
              {"idempotencyKey": "p48e_rv1_v1"})
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
