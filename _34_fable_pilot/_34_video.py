# _34_fable_pilot/_34_video.py — 34.md §14 candidate 驱动器（supervisor 工具）。
# 用法：python _34_fable_pilot/_34_video.py <shot_id> <n_candidates>
# 行为：对指定镜头提交 n 个视频 job（JobQueue 视频串行 1 RPM），逐个等待完成，
#       把每 candidate 的完整 provenance（VideoVersion + binding 参考 + ffprobe）
#       追加到 _34_fable_pilot/candidates.jsonl。
# 不写任何 canonical——video job 经正式 API；参考身份只读解析记录（§13 报告字段）。
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
PDIR = Path(__file__).resolve().parent.parent / "projects" / PROJ
OUT = Path(__file__).parent / "candidates.jsonl"


def api(method: str, path: str, **kw):
    r = httpx.request(method, f"{BASE}{path}", timeout=60, **kw)
    r.raise_for_status()
    return r.json()


def wait_task(job_id: str, timeout_s: int = 1800):
    """video job 走 JobQueue——轮询 /api/project/{P}/jobs 里的 state 字段。"""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        d = api("GET", f"/api/project/{PROJ}/jobs")
        jobs = d.get("jobs") if isinstance(d, dict) else d
        if isinstance(jobs, dict):
            jobs = list(jobs.values())
        for j in (jobs or []):
            if j.get("job_id") == job_id:
                st = j.get("state")
                if st in ("done", "failed", "cancelled"):
                    return {"status": st, "error": j.get("error")}
                break
        time.sleep(6)
    return {"status": "wait_timeout"}


def reference_identity(shot_id: str) -> dict:
    """只读解析 multi 槽参考身份（与 video_adapter._select_video_reference 同规则），
    供 §13 报告「每镜实际使用的 reference assets」。"""
    from src.resources.store import ResourceStore
    rdoc = ResourceStore(PDIR, PROJ).load()
    binding = json.loads((PDIR / "shot_production_bindings.json").read_text(encoding="utf-8")) \
        if (PDIR / "shot_production_bindings.json").exists() else {}
    b = binding.get(shot_id) or binding.get("bindings", {}).get(shot_id) \
        or (binding.get("bindings") if isinstance(binding.get("bindings"), dict) else {})
    out = {"binding_revision": (b or {}).get("binding_revision"),
           "readiness": (b or {}).get("readiness")}
    cb = ((b or {}).get("character_bindings") or [])
    if cb:
        look = rdoc.looks.get(cb[0].get("look_id"))
        if look is not None:
            approved = list(look.approved_reference_asset_ids or [])
            hero = (look.cover_asset_id if look.cover_asset_id in approved
                    else (approved[0] if approved else None))
            out["look_id"] = look.look_id
            out["hero_reference_asset_id"] = hero
            if hero and hero in rdoc.reference_assets:
                out["hero_reference_url"] = rdoc.reference_assets[hero].url
    sb = (b or {}).get("scene_binding") or {}
    if sb:
        out["scene_instance_id"] = sb.get("scene_instance_id")
        out["location_view_id"] = sb.get("location_view_id")
    return out


def ffprobe(p: Path) -> dict:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(p)],
            capture_output=True, text=True)
        d = json.loads(r.stdout or "{}")
        a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
        v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
        return {"duration": d.get("format", {}).get("duration"),
                "size": f'{v.get("width")}x{v.get("height")}' if v else None,
                "audio": bool(a), "audio_codec": a.get("codec_name") if a else None}
    except Exception as e:
        return {"ffprobe_error": str(e)[:120]}


def shot_state(shot_id: str) -> dict:
    d = api("GET", f"/api/project/{PROJ}/shots")
    for sc in (d.get("scenes") or []):
        for s in (sc.get("shots") or []):
            if s.get("shot_id") == shot_id:
                return s
    return {}


def main():
    shot_id, n = sys.argv[1], int(sys.argv[2])
    st = shot_state(shot_id)
    print(f"[{shot_id}] link_mode={st.get('link_mode')} "
          f"versions={len(st.get('video_versions') or [])}")
    if st.get("link_mode") != "multi":
        raise SystemExit(f"FAIL: {shot_id} link_mode={st.get('link_mode')!r} != multi")
    for i in range(n):
        pre = reference_identity(shot_id)
        r = api("POST", f"/api/project/{PROJ}/shot/{shot_id}/video")
        job_id = r["job_id"]
        print(f"[{shot_id}] candidate {i + 1}/{n} job={job_id} submitted", flush=True)
        t = wait_task(job_id)
        st2 = shot_state(shot_id)
        versions = st2.get("video_versions") or []
        vv = versions[-1] if versions else {}
        rec = {
            "shot_id": shot_id, "candidate": i + 1, "job_id": job_id,
            "job_status": t.get("status"), "task_error": (t.get("error") or "")[:400],
            "version_id": vv.get("version_id"), "video_id": vv.get("video_id"),
            "url": vv.get("url"), "local": vv.get("local"),
            "seconds": vv.get("seconds_returned"), "size": vv.get("size_returned"),
            "model": vv.get("model"),
            "generation_basis": vv.get("generation_basis"),
            "prompt_snapshot": vv.get("prompt_snapshot"),
            "reference_identity": pre,
            "ffprobe": ffprobe(Path(vv["local"])) if vv.get("local") else None,
            "ts": time.time(),
        }
        with OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{shot_id}] candidate {i + 1}: status={rec['job_status']} "
              f"version={rec['version_id']} seconds={rec['seconds']}", flush=True)
        if rec["job_status"] != "done":
            print(f"  ERROR: {rec['task_error']}", flush=True)


if __name__ == "__main__":
    main()
