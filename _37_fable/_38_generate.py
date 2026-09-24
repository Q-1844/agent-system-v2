# _37_fable/_38_generate.py — 《狐狸进村》逐镜驱动（submit / record / status / links）。
# 链路与 _37_generate.py 同型：prompt rebuild（正式端点）→ binding refresh → 提交（JobQueue 串行）→
# 等末帧 → MAE（128 灰度同刻度）→ Vision 尾帧评估 → 本地 trace + 正式 REST run links 双落档。
# 人工裁定用 record 子命令落档（Vision 只建议，人工必看图——38.md §3.3）。
import io
import json
import sys
import time
from pathlib import Path

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8000"
PROJ = "Fable_Pilot_Fox_and_Grapes_V1"
HERE = Path(__file__).parent
ROOT = HERE.parent
TRACE_FILE = HERE / "_38_trace_run.json"
RUN_ID_FILE = HERE / "_38_run_id.txt"

from _38_story import SHOTS  # noqa: E402

SEG_PREFIX = {"C": "s11", "D": "s12"}


def seg_shot_map():
    """M-key -> shot_id（段内 c01 起计）。"""
    counter = {"C": 0, "D": 0}
    out = {}
    for spec in SHOTS:
        counter[spec["segment"]] += 1
        out[spec["shot"]] = (f"{SEG_PREFIX[spec['segment']]}_c{counter[spec['segment']]:02d}",
                             spec)
    return out


def api(m, p, **kw):
    r = httpx.request(m, f"{BASE}{p}", timeout=120, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{p} {r.status_code}: {r.text[:350]}")
    return r.json()


def wait_job(job_id, timeout_s=2400):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        d = api("GET", f"/api/project/{PROJ}/jobs")
        jobs = d.get("jobs") if isinstance(d, dict) else d
        if isinstance(jobs, dict):
            jobs = list(jobs.values())
        for j in jobs or []:
            if j.get("job_id") == job_id:
                if j.get("state") in ("done", "failed", "cancelled"):
                    return j.get("state"), (j.get("error") or ""), j
        time.sleep(8)
    return "wait_timeout", "", None


def wait_end_frame(shot_id, timeout_s=420):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        shots = api("GET", f"/api/project/{PROJ}/shots")
        for sc in shots.get("scenes") or []:
            for s in sc.get("shots") or []:
                if s.get("shot_id") == shot_id:
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


def trace_load():
    if TRACE_FILE.exists():
        return json.loads(TRACE_FILE.read_text(encoding="utf-8"))
    return {}


def trace_save(rec):
    TRACE_FILE.write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def run_id():
    return RUN_ID_FILE.read_text(encoding="utf-8").strip()


def rest_upsert_link(nkey, link):
    """正式 REST run link 同步（38.md §5 T3：trace 落档双保险）。"""
    try:
        api("PUT", f"/api/project/{PROJ}/calibration/runs/{run_id()}/links/{nkey}",
            json={"record": link})
    except RuntimeError as e:
        print(f"   [warn] REST link upsert fail: {str(e)[:130]}", flush=True)


def _vision_client():
    """独立进程的 Vision client（后端 deps 未配置时按 config 直构）。"""
    try:
        import deps
        return deps.get_agnes_client()
    except Exception:
        import os
        import re
        import yaml
        from src.api.agnes_client import AgnesClient
        envp = ROOT / ".env"
        if envp.exists():
            for line in envp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line \
                        and not os.environ.get(line.split("=", 1)[0]):
                    k, _, v = line.partition("=")
                    os.environ[k.strip()] = v.strip()
        cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
        ag = json.loads(json.dumps(cfg["agnes"]))
        if isinstance(ag.get("api_key"), str) and "${AGNES_API_KEY}" in ag["api_key"]:
            ag["api_key"] = re.sub(r"\$\{AGNES_API_KEY\}",
                                   os.environ.get("AGNES_API_KEY", ""), ag["api_key"])
        return AgnesClient(ag)


def httpx_sync_vision(agnes, spec, tail_img):
    import asyncio
    from src.experiments.tail_assessment import assess_tail
    return asyncio.run(assess_tail(
        agnes, expected_tail=spec["expected_tail"], tail_image=tail_img))


def submit(nkey):
    """单镜全流程：rebuild → refresh → 提交 → 等待 → MAE → vision → trace 双落档。"""
    shot_id, spec = seg_shot_map()[nkey]
    print(f"== [{nkey}={shot_id}] submit", flush=True)

    # 1) prompt 正式重建（生成前必须新鲜）
    rr = api("POST", f"/api/project/{PROJ}/shot/{shot_id}/prompt/rebuild",
             json={"stages": ["video"]})
    vp = rr["stages"]["video"]
    if not vp.get("ok"):
        print("   prompt rebuild FAIL:", vp.get("error"))
        return
    print(f"   video prompt rebuilt hash={vp['prompt_hash']}", flush=True)
    print(f"   prompt: {vp['prompt'][:180]}", flush=True)

    # 2) binding refresh（OCC）
    b = api("GET", f"/api/project/{PROJ}/shot-bindings/{shot_id}")
    brev = b.get("binding_revision") or (b.get("binding") or {}).get("binding_revision") or ""
    rb = api("POST", f"/api/project/{PROJ}/shot-bindings/{shot_id}/refresh",
             json={"expected_binding_revision": brev})
    print("   binding:", str(rb.get("status") or rb)[:40], flush=True)

    # 3) 提交视频（JobQueue 串行 1 RPM）
    rv = api("POST", f"/api/project/{PROJ}/shot/{shot_id}/video")
    job_id = rv.get("job_id") or (rv.get("job") or {}).get("job_id")
    print("   job:", job_id, flush=True)
    state, err, job = wait_job(job_id)
    print(f"   job state={state} err={err[:120]}", flush=True)
    if state != "done":
        return

    # 4) 等末帧（自动提取+Pages）
    ef = wait_end_frame(shot_id)
    if not ef:
        print("   end frame 未就绪（超时）")
        return
    local = ROOT / ef["local"].replace("\\", "/") if ef.get("local") else None
    print(f"   end frame: {ef['url'][:80]}", flush=True)

    # 5) MAE（衔接连续性，历史同刻度；M4 是跨 Location 接力，MAE 预期显著偏高）
    mae_v = None
    prev_key = None
    keys = [s["shot"] for s in SHOTS]
    idx = keys.index(nkey)
    if idx > 0:
        prev_key = keys[idx - 1]
    if prev_key:
        prev_id, _ = seg_shot_map()[prev_key]
        rec = trace_load()
        prev_local = (rec.get(prev_key) or {}).get("end_frame_local")
        if prev_local and local and Path(local).exists() and Path(prev_local).exists():
            mae_v = mae(prev_local, local)
            print(f"   MAE vs {prev_key} tail: {mae_v}", flush=True)

    # 6) Vision 结构化评估（证据非裁定）
    vision = None
    if local and Path(local).exists():
        from src.experiments.tail_assessment import assess_tail, _image_entry
        agnes = _vision_client()
        tail_img = _image_entry(Path(local), None)
        try:
            out = httpx_sync_vision(agnes, spec, tail_img)
            vision = out
            print(f"   vision: suggested={out['assessment'].get('suggested_deviation')}",
                  flush=True)
            for k in ("expected_tail_match", "next_shot_readiness", "unexpected_elements"):
                print(f"     {k}: {out['assessment'].get(k)}", flush=True)
        except Exception as e:
            print("   vision FAIL:", str(e)[:150], flush=True)

    # 7) trace 落档（本地 + REST run links）；已有裁定记录先归档为 attempt（重试不丢历史）
    first_of_story = nkey == keys[0]
    rec = trace_load()
    old = rec.get(nkey)
    if old and old.get("human_deviation_level"):
        n_att = 1 + sum(1 for k in rec if k.startswith(f"{nkey}_a"))
        rec[f"{nkey}_a{n_att}"] = old
        print(f"   archived attempt: {nkey}_a{n_att} "
              f"({old.get('human_deviation_level')}/{old.get('human_action')})", flush=True)
    rec[nkey] = {
        "shot_id": shot_id, "segment": spec["segment"],
        "provider_id": "agnes", "provider_model": "agnes-video-v2.0",
        "generation_mode": "multi_reference" if first_of_story else "multi_relay",
        "duration_s": spec["duration_s"],
        "prompt_hash": vp["prompt_hash"], "prompt_snapshot": vp["prompt"],
        "reference_asset_ids": [] if first_of_story else ["prev_tail"],
        "opening_reference_type": ("chain_root_reference" if first_of_story
                                   else "prev_tail_relay"),
        "expected_tail": spec["expected_tail"],
        "end_frame_url": ef["url"], "end_frame_local": str(local) if local else "",
        "mae_prev": mae_v,
        "vision_assessment": (vision or {}).get("assessment") if vision else None,
        "vision_meta": (vision or {}).get("meta") if vision else None,
        "human_deviation_level": None, "human_action": None, "human_reason": "",
        "shot_patch": {}, "prompt_rebuilt": True,
        "next_shot_id": "", "result_after_correction": "",
        "retry_or_adapt": None, "choice_reason": "",
    }
    trace_save(rec)
    rest_upsert_link(nkey, rec[nkey])
    print(f"== [{nkey}] 检查点就绪——等待人工裁定（record 子命令）", flush=True)


def record(nkey, level, action, reason, patch=None):
    """人工裁定落档（可选携带对下一镜的校准 patch）。"""
    assert level in ("none", "minor", "major"), level
    assert action in ("continue", "add_transition", "insert_story_beat",
                      "retry_generation", "break_chain_and_reanchor", "other"), action
    rec = trace_load()
    if nkey not in rec:
        print("no trace for", nkey)
        return
    rec[nkey]["human_deviation_level"] = level
    rec[nkey]["human_action"] = action
    rec[nkey]["human_reason"] = reason
    rec[nkey]["retry_or_adapt"] = ("retry" if action == "retry_generation" else "adapt")
    keys = [s["shot"] for s in SHOTS]
    idx = keys.index(nkey)
    if patch:
        rec[nkey]["shot_patch"] = patch
        if idx + 1 < len(keys):
            nk = keys[idx + 1]
            next_id, _ = seg_shot_map()[nk]
            rec[nkey]["next_shot_id"] = next_id
            # 校准 patch 写下一镜字段（update_shot_fields 正式路径）
            from src.core.store import ProjectStore
            from src.storyboard.structure_service import update_shot_fields
            store = ProjectStore(ROOT / "projects" / PROJ)
            project = store.load()
            r = update_shot_fields(project, next_id, patch)
            store.save(project)
            print(f"   patch -> {next_id}: changed={r.get('changed')} "
                  f"invalidated={r.get('prompts_invalidated')}", flush=True)
    if idx + 1 < len(keys):
        rec[nkey]["next_shot_id"] = rec[nkey].get("next_shot_id") or seg_shot_map()[keys[idx + 1]][0]
    trace_save(rec)
    rest_upsert_link(nkey, rec[nkey])
    print(f"== [{nkey}] 裁定落档: {level}/{action} — {reason}", flush=True)


def status():
    rec = trace_load()
    print(f"{'shot':5s} {'shot_id':10s} {'dur':4s} {'MAE':6s} {'vision':8s} {'human':7s} action")
    for spec in SHOTS:
        k = spec["shot"]
        r = rec.get(k) or {}
        v = (r.get("vision_assessment") or {}).get("suggested_deviation", "-") \
            if r.get("vision_assessment") else "-"
        print(f"{k:5s} {r.get('shot_id','-'):10s} {spec['duration_s']:<4.0f} "
              f"{str(r.get('mae_prev') or '-')!s:6s} {v:8s} "
              f"{str(r.get('human_deviation_level') or '-')!s:7s} "
              f"{str(r.get('human_action') or '-')}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "submit" and len(sys.argv) > 2:
        submit(sys.argv[2])
    elif cmd == "record" and len(sys.argv) > 4:
        patch = json.loads(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] != "-" else None
        reason = sys.argv[6] if len(sys.argv) > 6 else ""
        record(sys.argv[2], sys.argv[3], sys.argv[4], reason, patch)
    elif cmd == "status":
        status()
    elif cmd == "links":
        # 全量补同步本地 trace -> REST run links
        rec = trace_load()
        for k, v in rec.items():
            rest_upsert_link(k, v)
        print(f"synced {len(rec)} links -> run {run_id()}")
    else:
        print("usage: _38_generate.py submit <M-key> | record <M-key> <level> <action> "
              "[patch-json|-] [reason] | status | links")
