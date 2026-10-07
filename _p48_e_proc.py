# -*- coding: utf-8 -*-
"""Phase48 E 用户反馈轮：单主体流程片时长与逐秒分拍测试。

用户反馈三点：①多人物是《风筝与竹竿》主要失败面；②多数动作需 ≥8s 否则只能接续；
③提示词应按每一秒写动作。本轮：photoreal 单主体（手+蜡烛/火柴，无脸），
s01_c01=10s 逐秒分拍完整因果链，s01_c02=8s 接续镜（c01 尾态续燃）。
先不配音（无对白无旁白）。

用法：python _p48_e_proc.py fixture|frames|run
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
PROJ = "P48_E_Proc"
BASE = "http://localhost:8000"

STYLE = ("photorealistic cinematic rendering, natural warm lighting, shallow depth of "
         "field, filmic grade")
REF_PROMPT = ("Still life setup on a dark wooden table, {style}: on the right a tall white "
              "candle standing unlit in a small brass holder, on the left a small open "
              "matchbox and one wooden matchstick lying beside it, a tiny ceramic dish "
              "near the candle, single warm practical lamp from upper left, no people, "
              "no text")

# s01_c01 = 10s 逐秒分拍（完整因果链：划→燃→移→点→稳）
C01_PROMPT = ("Photorealistic close-up, hands-only scene on a dark wooden table, {style}: "
              "a right hand holds a wooden matchstick pressed against the matchbox side, "
              "poised to strike; the white candle in the brass holder stands to the right "
              "COMPLETELY UNLIT — its wick is cold, black and flameless, absolutely NO "
              "flame on the candle, the only light sources are the warm lamp bulb and "
              "ambient light; no faces, no text")
# s01_c02 = 8s 接续镜首帧（c01 尾态：烛已燃、火柴仍在燃）
C02_PROMPT = ("Photorealistic close-up, hands-only scene on a dark wooden table, {style}: "
              "the white candle is NOW LIT with a steady warm flame, in the foreground the "
              "right hand holds the still-burning matchstick having just lit the wick, "
              " wisps of smoke, no faces, no text")


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
            "name": PROJ, "render_family": "photorealistic",
            "art_direction": "cinematic photorealistic rendering, natural warm lighting, "
                             "shallow depth of field, filmic grade"})
    from src.core.store import ProjectStore
    from src.core.models import Scene, Shot
    st = ProjectStore(ROOT / "projects" / PROJ)
    project = st.load()
    if not any(x.scene_id == "s01" for x in project.scenes):
        sc = Scene(scene_id="s01", summary="用户反馈轮：划火柴点蜡烛（时长与逐秒分拍）",
                   characters=[], shots=[])
        project.scenes = list(project.scenes) + [sc]
    sc = next(x for x in project.scenes if x.scene_id == "s01")
    have = {s.shot_id for s in sc.shots}
    # 逐秒分拍写进 description（导演写 prompt 时逐秒化）；时长 10s/8s
    shots = [
        ("s01_c01", 10.0,
         "[用户反馈轮·10s逐秒分拍] 0-1s 手捏火柴贴住盒侧；1-2s 擦燃迸火星；2-4s 火柴焰稳定燃烧；"
         "4-6s 手持燃焰移向烛芯；6-8s 火苗舔到烛芯、烛芯点燃；8-10s 甩灭火柴、烛焰独立稳定站立。",
         "slow deliberate hands-only action, single continuous take"),
        ("s01_c02", 8.0,
         "[用户反馈轮·8s接续镜·承c01尾态] 0-2s 烛焰特写稳定燃烧；2-4s 手将燃着的火柴梗放入小瓷碟任其燃尽；"
         "4-6s 双手轻拢烛焰测试气流、焰身晃动不灭；6-8s 手撤出画面、烛焰在桌面投下稳定光晕。",
         "slow deliberate hands-only action, continuation from previous shot tail state"),
    ]
    n = 0
    for sid, dur, desc, motion in shots:
        if sid in have:
            continue
        n += 1
        sc.shots = list(sc.shots) + [Shot(
            shot_id=sid, scene_id="s01", index=n, description=desc,
            prompt_en=REF_PROMPT.format(style=STYLE), motion=motion,
            characters=[], camera="static close-up", lighting="warm practical",
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
    ev_path = ROOT / "_p48_e_proc_evidence.json"
    ev = json.loads(ev_path.read_text(encoding="utf-8")) if ev_path.exists() else {}
    refs_b64 = None
    if ev.get("ref_png"):
        refs_b64 = [base64.b64encode((ROOT / ev["ref_png"]).read_bytes()).decode()]
    else:
        raw, rec = gen_image(REF_PROMPT.format(style=STYLE))
        (out_dir / "scene_ref.png").write_bytes(raw)
        ev["ref_png"] = str(out_dir / "scene_ref.png")
        ev["ref"] = rec
        ev_path.write_text(json.dumps(ev, ensure_ascii=False, indent=1),
                           encoding="utf-8")
        refs_b64 = [base64.b64encode(raw).decode()]
        log("scene ref ready", rec["sha16"])
    for sid, prompt in (("s01_c01", C01_PROMPT), ("s01_c02", C02_PROMPT)):
        if ev.get(sid, {}).get("ff_ready"):
            log(sid, "already ready")
            continue
        raw, rec = gen_image(prompt.format(style=STYLE), refs_b64=refs_b64)
        img = out_dir / f"{sid}_ff_custom.png"
        img.write_bytes(raw)
        rec["local"] = str(img)
        import asyncio
        from deps import get_rehost_host
        url = asyncio.run(get_rehost_host().upload(str(img)))
        cid = f"c_inj_{sid}_{int(time.time())}"
        batch = ImageBatch(batch_id=f"ffb_inj_{sid}_{int(time.time())}",
                           kind="first_frame",
                           prompt_snapshot=f"[用户反馈轮夹具] {prompt[:60]}",
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
    body = {"workflowId": "wf_e_proc", "params": {
        "videoObjective": (
            "两镜为单主体流程片（划火柴点蜡烛），photoreal、无对白无旁白。"
            "用户反馈三项硬要求：①镜头时长必须做满设定值——s01_c01 目标 10 秒、s01_c02 目标 8 秒"
            "（target_duration_s 已设定，不得缩短）；②两镜 description 已按秒分拍动作"
            "（0-1s/1-2s/…），生成提示词必须把逐秒分拍原样编入，让动作节奏对齐拍点；"
            "③s01_c02 是 s01_c01 尾态的接续镜（烛已点燃）——按 relay skill v1.4.0 起步决策入口"
            "选择接续方式（同地点同主体端点合格时可考虑尾帧接力 a，或按本镜首帧独立起步，"
            "由你按技能判据决定并记录理由）。完成后逐镜核对：实际时长是否达标、分拍动作是否按秒发生、"
            "两镜衔接是否自然。"),
        "videoCount": 2}}
    out = api("POST", f"/api/project/{PROJ}/workflow/templates/W05/create", body)
    print("def:", out.get("workflowId"), "valid:", (out.get("validation") or {}).get("ok"))
    out = api("POST", f"/api/project/{PROJ}/workflow/definitions/wf_e_proc/runs",
              {"idempotencyKey": "p48e_proc_v1"})
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
