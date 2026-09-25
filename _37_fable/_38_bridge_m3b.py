# _37_fable/_38_bridge_m3b.py — 跨 Location keyframes 接续桥（38.md §2.3 配方，内联脚本）。
# 场景：M3(s11_c03 尾帧=雨林边缘) → 桥(s11_c04, M3B) → M4(s12_c01)。
# 桥=穿越本身：首槽=M3 尾帧（雨林边缘）、尾槽=M4 视频内「落到乡村土路」帧（_m4_f60.jpg）。
# 随后 M4 以 prev_tail（播放序上一镜=桥）重生成——直跳被桥吸收。
# ⚠️ basis 时序坑：refresh → compile+seed → 提交，seed 后不得再 refresh。
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
LANDING_IMG = HERE / "media" / "_m4_f60.jpg"       # 桥尾槽 = M4 视频第 60 帧（落进村庄）
M3_TAIL = HERE / "media" / "M3_end.jpg"

BRIDGE_SID = "s11_c04"
TRANSITION_DESC = (
    "The fox breaks into a full sprint from the shadowed treeline and bursts out of the "
    "forest into blinding sunlight; in a surge of golden light the last trees sweep past "
    "and the world opens into the bright village outskirts, the fox landing on the sunlit "
    "dirt road with a puff of dust, fences and pumpkins flashing by on both sides")


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
    """尾槽着陆帧 → Pages 图床（GET-first 内容寻址，幂等）。"""
    host = build_host()
    url = asyncio.run(host.upload(str(LANDING_IMG)))
    print(f"[A] landing frame uploaded: {url[:90]}", flush=True)
    return url


def step_b_structure():
    """插入桥镜（s11_c04，M3 之后）+ 段内接力 relay。幂等。"""
    from src.core.store import ProjectStore
    from src.core.models import ImageAsset
    from src.storyboard.structure_service import insert_shots_after, update_shot_relay
    store = ProjectStore(PROJ_DIR)
    project = store.load()
    have = {sh.shot_id for sc in project.scenes for sh in sc.shots}
    if BRIDGE_SID in have:
        print(f"[B] {BRIDGE_SID} exists, skip create", flush=True)
        return
    new = insert_shots_after(project, "s11", "s11_c03", [{
        "description": "桥梁镜：狐狸从雨林边缘冲出，跃入刺眼阳光，世界展开为明亮的村庄村口",
        "characters": ["狐狸"], "target_duration_s": 10.0}])
    sid = new[0].shot_id
    assert sid == BRIDGE_SID, f"桥镜 id 与预期不符: {sid}（预期 {BRIDGE_SID}）"
    r = update_shot_relay(project, sid, "s11_c03")
    store.save(project)
    print(f"[B] bridge inserted: {sid} relay->{r.get('source_shot_id')}", flush=True)


def step_c_binding():
    payload = {"shot_id": BRIDGE_SID, "scene_id": "s11",
               "scene_binding": {"scene_instance_id": "scin_7f922f31e5f2",
                                 "location_view_id": "view_70e07a5c7211"},
               "character_bindings": [{"character_name": "狐狸",
                                       "look_id": "look_2c6a01d45db5"}],
               "prop_bindings": [], "production_notes": "跨Location keyframes 桥 M3B"}
    try:
        api("POST", f"/api/project/{PROJ}/shot-bindings", json=payload)
        print("[C] binding created", flush=True)
    except RuntimeError as e:
        if "已存在" in str(e) or "already" in str(e).lower() or "409" in str(e):
            print("[C] binding exists", flush=True)
        else:
            raise
    b = api("GET", f"/api/project/{PROJ}/shot-bindings/{BRIDGE_SID}")
    brev = b.get("binding_revision") or (b.get("binding") or {}).get("binding_revision") or ""
    r = api("POST", f"/api/project/{PROJ}/shot-bindings/{BRIDGE_SID}/refresh",
            json={"expected_binding_revision": brev})
    print(f"[C] binding refresh -> {str(r.get('status') or r)[:40]}", flush=True)


def step_d_put_keyframes():
    r = api("PUT", f"/api/project/{PROJ}/shot/{BRIDGE_SID}", json={
        "link_mode": "keyframes", "opening_source": "inherit_previous",
        "transition_description": TRANSITION_DESC, "video_prompt_en": "",
        "target_duration_s": 10.0})
    print(f"[D] keyframes PUT ok, invalidated={r.get('prompts_invalidated')}", flush=True)


def step_e_compile_seed(landing_url):
    """compile pack → basis → seed 尾帧批次。⚠️ 此后不得再 refresh。"""
    from src.shot_binding.service import ShotBindingService
    from src.core.store import ProjectStore
    from src.core.models import ImageBatch, Candidate, ImageAsset
    svc = ShotBindingService(PROJ_DIR, PROJ)
    compiled = svc.compile_reference_pack(BRIDGE_SID)
    if compiled.get("status") != "COMPILED":
        raise RuntimeError(f"compile BLOCKED: {json.dumps(compiled.get('issues'))[:300]}")
    basis = compiled["pack"]["basis"]
    print(f"[E] compiled, basis.binding_revision={basis.get('binding_revision')}", flush=True)

    store = ProjectStore(PROJ_DIR)
    project = store.load()
    shot = project.get_shot(BRIDGE_SID)
    now = time.time()
    cand_id = f"cand_m3b_lf_{uuid.uuid4().hex[:6]}"
    batch_id = f"batch_m3b_lf_{uuid.uuid4().hex[:6]}"
    batch = ImageBatch(batch_id=batch_id, kind="last_frame", created_at=now,
                       status="ready", prompt_snapshot="[bridge seed] M4 f60 landing frame",
                       candidates=[Candidate(candidate_id=cand_id, local=str(LANDING_IMG),
                                             url=landing_url, status="done",
                                             created_at=now)],
                       generation_basis=basis)
    shot.last_frame_batches.append(batch)
    shot.selected_last_frame = cand_id
    shot.last_frame.mark_done(local=str(LANDING_IMG), url=landing_url)
    store.save(project)
    print(f"[E] seeded: batch={batch_id} cand={cand_id} basis.keys={sorted(basis.keys())[:6]}",
          flush=True)


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


def wait_end_frame(timeout_s=420):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        shots = api("GET", f"/api/project/{PROJ}/shots")
        for sc in shots.get("scenes") or []:
            for s in sc.get("shots") or []:
                if s.get("shot_id") == BRIDGE_SID:
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
    step_b_structure()
    step_c_binding()
    step_d_put_keyframes()
    step_e_compile_seed(landing_url)
    # ⚠️ seed 后零 refresh，直接提交（keyframes：首槽=M3 尾帧、尾槽=M4 f60）
    rv = api("POST", f"/api/project/{PROJ}/shot/{BRIDGE_SID}/video")
    job_id = rv.get("job_id") or (rv.get("job") or {}).get("job_id")
    print(f"[F] job: {job_id}", flush=True)
    state, err = wait_job(job_id)
    print(f"[F] job state={state} err={err[:150]}", flush=True)
    if state != "done":
        return
    ef = wait_end_frame()
    if not ef:
        print("[G] end frame 未就绪（超时）", flush=True)
        return
    local = ROOT / ef["local"].replace("\\", "/")
    print(f"[G] end frame: {ef['url'][:90]}", flush=True)
    m_landing = mae(local, LANDING_IMG) if LANDING_IMG.exists() else None
    m_m3tail = mae(local, M3_TAIL) if M3_TAIL.exists() else None
    print(f"[H] MAE 桥尾帧 vs M4f60(尾槽目标): {m_landing} ｜ vs M3尾帧(首槽来源): {m_m3tail}",
          flush=True)
    print("   （37A 对标：keyframes 桥=2.9 成功 / 多参考版=43.6 被吸引子劫持）", flush=True)


if __name__ == "__main__":
    main()
