# _37_fable/_38_bridge_m5b.py — M5→M6 接缝 keyframes 桥（38.md §2.3；双端钉死=接缝修复标准手段）。
# 首槽=M5(s12_c02) 尾帧（直立站大南瓜旁）、尾槽=M6(s12_c03) 首帧（鼻尖凑近南瓜，_m6_first_frame.jpg）。
# 中间过程=provider 插值（低头-凑近-嗅闻）。时长 5s（静态/反应配方；§2.3 的 10s 针对大姿态反转，
# 本桥动作小，尾帧钉死保证收束不截断）。⚠️ basis 时序：refresh→compile+seed→提交，seed 后零 refresh。
import asyncio
import io
import json
import sys
import time
import uuid
from pathlib import Path

import httpx
import yaml

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8000"
PROJ = "Fable_Pilot_Fox_and_Grapes_V1"
ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
PROJ_DIR = ROOT / "projects" / PROJ
LANDING_IMG = HERE / "media" / "_m6_first_frame.jpg"   # 桥尾槽 = M6 视频第 0 帧（鼻尖凑近南瓜）
M5_TAIL = HERE / "media" / "M5_end.jpg"

BRIDGE_SID_EXPECT_SEG = "s12"
INSERT_AFTER = "s12_c02"
DURATION_S = 5.0
TRANSITION_DESC = (
    "The fox lowers its front body and leans in toward the big orange pumpkin, pressing "
    "its nose close to the pumpkin's ridged skin to sniff it carefully, tail stretched "
    "low and body crouched; the camera holds close as the fox's nose finally hovers just "
    "above the pumpkin surface")


def api(m, p, timeout=120, **kw):
    r = httpx.request(m, f"{BASE}{p}", timeout=timeout, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{p} {r.status_code}: {r.text[:350]}")
    return r.json()


def build_host():
    from src.core.image_host import build_image_host
    import os
    envp = ROOT / ".env"
    if envp.exists():
        for line in envp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line \
                    and not os.environ.get(line.split("=", 1)[0]):
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip()
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    ih = json.loads(json.dumps(cfg["image_host"]))
    gp = ih.get("github_pages") or {}
    if isinstance(gp.get("token"), str) and "${GITHUB_TOKEN}" in gp["token"]:
        gp["token"] = gp["token"].replace("${GITHUB_TOKEN}",
                                          os.environ.get("GITHUB_TOKEN", ""))
        ih["github_pages"] = gp
    return build_image_host(ih)


def step_a_upload():
    host = build_host()
    url = asyncio.run(host.upload(str(LANDING_IMG)))
    print(f"[A] landing frame uploaded: {url[:90]}", flush=True)
    return url


def step_b_structure():
    """插入桥镜（M5 之后，段内位次 3；id 按段内计数器顺延）+ 段内接力 relay。幂等。"""
    from src.core.store import ProjectStore
    from src.storyboard.structure_service import insert_shots_after, update_shot_relay
    store = ProjectStore(PROJ_DIR)
    project = store.load()
    scene = next(s for s in project.scenes if s.scene_id == BRIDGE_SID_EXPECT_SEG)
    have = {sh.shot_id for sh in scene.shots}
    existing_bridge = next((sh for sh in scene.shots
                            if sh.source_shot_id == INSERT_AFTER
                            and sh.index == 3), None)
    if existing_bridge:
        print(f"[B] bridge exists: {existing_bridge.shot_id}", flush=True)
        return existing_bridge.shot_id
    assert INSERT_AFTER in have, f"锚点 {INSERT_AFTER} 不存在"
    new = insert_shots_after(project, BRIDGE_SID_EXPECT_SEG, INSERT_AFTER, [{
        "description": "桥梁镜：狐狸压低前身凑近大南瓜，鼻尖贴着南瓜皮低头嗅闻",
        "characters": ["狐狸"], "target_duration_s": DURATION_S}])
    sid = new[0].shot_id
    r = update_shot_relay(project, sid, INSERT_AFTER)
    store.save(project)
    print(f"[B] bridge inserted: {sid} (index={new[0].index}) relay->{r.get('source_shot_id')}",
          flush=True)
    return sid


def step_c_binding(sid):
    payload = {"shot_id": sid, "scene_id": "s12",
               "scene_binding": {"scene_instance_id": "scin_c20fc5401d1e",
                                 "location_view_id": "view_21c6bab8b0fb"},
               "character_bindings": [{"character_name": "狐狸",
                                       "look_id": "look_2c6a01d45db5"}],
               "prop_bindings": [], "production_notes": "M5→M6 接缝 keyframes 桥 M5B"}
    try:
        api("POST", f"/api/project/{PROJ}/shot-bindings", json=payload)
        print("[C] binding created", flush=True)
    except RuntimeError as e:
        if "已存在" in str(e) or "already" in str(e).lower() or "409" in str(e):
            print("[C] binding exists", flush=True)
        else:
            raise
    b = api("GET", f"/api/project/{PROJ}/shot-bindings/{sid}")
    brev = b.get("binding_revision") or (b.get("binding") or {}).get("binding_revision") or ""
    r = api("POST", f"/api/project/{PROJ}/shot-bindings/{sid}/refresh",
            json={"expected_binding_revision": brev})
    print(f"[C] binding refresh -> {str(r.get('status') or r)[:40]}", flush=True)


def step_d_put_keyframes(sid):
    r = api("PUT", f"/api/project/{PROJ}/shot/{sid}", json={
        "link_mode": "keyframes", "opening_source": "inherit_previous",
        "transition_description": TRANSITION_DESC, "video_prompt_en": "",
        "target_duration_s": DURATION_S})
    print(f"[D] keyframes PUT ok, invalidated={r.get('prompts_invalidated')}", flush=True)


def step_e_compile_seed(sid, landing_url):
    from src.shot_binding.service import ShotBindingService
    from src.core.store import ProjectStore
    from src.core.models import ImageBatch, Candidate
    svc = ShotBindingService(PROJ_DIR, PROJ)
    compiled = svc.compile_reference_pack(sid)
    if compiled.get("status") != "COMPILED":
        raise RuntimeError(f"compile BLOCKED: {json.dumps(compiled.get('issues'))[:300]}")
    basis = compiled["pack"]["basis"]
    print(f"[E] compiled, basis.binding_revision={basis.get('binding_revision')}", flush=True)

    store = ProjectStore(PROJ_DIR)
    project = store.load()
    shot = project.get_shot(sid)
    now = time.time()
    cand_id = f"cand_m5b_lf_{uuid.uuid4().hex[:6]}"
    batch_id = f"batch_m5b_lf_{uuid.uuid4().hex[:6]}"
    shot.last_frame_batches.append(ImageBatch(
        batch_id=batch_id, kind="last_frame", created_at=now, status="ready",
        prompt_snapshot="[bridge seed] M6 first frame (nose-close to pumpkin)",
        candidates=[Candidate(candidate_id=cand_id, local=str(LANDING_IMG),
                              url=landing_url, status="done", created_at=now)],
        generation_basis=basis))
    shot.selected_last_frame = cand_id
    shot.last_frame.mark_done(local=str(LANDING_IMG), url=landing_url)
    store.save(project)
    print(f"[E] seeded: batch={batch_id} cand={cand_id}", flush=True)


def wait_job(job_id, timeout_s=2400):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        d = api("GET", f"/api/project/{PROJ}/jobs")
        jobs = d.get("jobs") if isinstance(d, dict) else d
        if isinstance(jobs, dict):
            jobs = list(jobs.values())
        for j in jobs or []:
            if j.get("job_id") == job_id and j.get("state") in ("done", "failed", "cancelled"):
                return j.get("state"), (j.get("error") or "")
        time.sleep(8)
    return "wait_timeout", ""


def wait_end_frame(sid, timeout_s=420):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        shots = api("GET", f"/api/project/{PROJ}/shots")
        for sc in shots.get("scenes") or []:
            for s in sc.get("shots") or []:
                if s.get("shot_id") == sid:
                    ef = s.get("video_end_frame") or {}
                    if (ef.get("url") or "") and ef.get("status") == "done":
                        return ef
        time.sleep(6)
    return None


def mae(a_path, b_path, w=128, h=128):
    from PIL import Image
    ia = Image.open(a_path).convert("L").resize((w, h))
    ib = Image.open(b_path).convert("L").resize((w, h))
    pa, pb = list(ia.getdata()), list(ib.getdata())
    return round(sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa), 1)


def main():
    landing_url = step_a_upload()
    sid = step_b_structure()
    step_c_binding(sid)
    step_d_put_keyframes(sid)
    step_e_compile_seed(sid, landing_url)
    # ⚠️ seed 后零 refresh，直接提交（keyframes：首槽=M5 尾帧、尾槽=M6 首帧）
    rv = api("POST", f"/api/project/{PROJ}/shot/{sid}/video")
    job_id = rv.get("job_id") or (rv.get("job") or {}).get("job_id")
    print(f"[F] job: {job_id}", flush=True)
    state, err = wait_job(job_id)
    print(f"[F] job state={state} err={err[:150]}", flush=True)
    if state != "done":
        return
    ef = wait_end_frame(sid)
    if not ef:
        print("[G] end frame 未就绪（超时）", flush=True)
        return
    local = ROOT / ef["local"].replace("\\", "/")
    print(f"[G] end frame: {ef['url'][:90]}", flush=True)
    m_landing = mae(local, LANDING_IMG) if LANDING_IMG.exists() else None
    m_m5tail = mae(local, M5_TAIL) if M5_TAIL.exists() else None
    print(f"[H] MAE 桥尾帧 vs M6首帧(尾槽目标): {m_landing} ｜ vs M5尾帧(首槽来源): {m_m5tail}",
          flush=True)
    print(f"DONE bridge_sid={sid}", flush=True)


if __name__ == "__main__":
    main()
