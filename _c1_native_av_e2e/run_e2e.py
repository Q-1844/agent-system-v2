# _c1_native_av_e2e/run_e2e.py
# Native AV C1 真机 E2E orchestrator（18.md §32/§33，scratch）。
#
# 与 B1 的关键差异：**走生产 handler 全链**（§32「不是只测实验入口」）——
#   - 直接调用生产 `_handler_video`（JobQueue 内注册的同一函数）：
#       UNBOUND 路径：build_video_prompt → NativeAVGenerationPack →
#                     AV Prompt Compiler → create_video → poll → 下载 →
#                     pre-commit recheck → VideoVersion(generation_basis) + final_video
#       BOUND 路径：  shot_has_visual_production_binding → _gate_ready_pack →
#                     boundary freshness → adapter（生产上下文+风格+AV 编译区）→
#                     pre-dispatch guard → provider → attach
#   - 不经 JobQueue 双 worker（避免与潜在后端进程争用 jobs.json；handler 即生产路径）
#   - 证据：evidence/<spec>/（job_receipt/VideoVersion/generation_basis/freshness/
#     ffprobe/video.mp4）+ review_manifest.json（评审网站数据源）
from __future__ import annotations
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_b1_dialogue_probe"))
# 🔴 本目录最后 insert = sys.path 最前——本目录 samples.py 优先于 B1 的同名模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_common import ffprobe_media, jwrite                         # noqa: E402
from samples import (BOUND_PROJECT_NAME, BOUND_SHOT, LANGUAGE,         # noqa: E402
                     PROJECT_NAME, SHOTS)

EVID = Path(__file__).resolve().parent / "evidence"


def boot():
    """真实 Agnes + 真实 ProjectManager（GP driver boot 同款接线，含 .env 装载）。"""
    import os
    _env_path = ROOT / ".env"
    if _env_path.is_file():
        for _line in _env_path.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
    from src.pipeline.controller import PipelineController
    import deps
    from src.core.quota import QuotaLedger
    from src.core.rate_limiter import RateLimiter
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    agnes_cfg = cfg.get("agnes") or {}
    RateLimiter.get(agnes_cfg.get("rpm"))
    QuotaLedger.get_default(ROOT / "cache" / "quota.json", limits=cfg.get("quota"))
    deps.pipeline_controller = PipelineController("./config/config.yaml")
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager
    return deps.agnes_client


async def run_video_handler(pdir, project_name, shot_id, *, video_model=None):
    """生产 _handler_video 全链（UNBOUND 与 BOUND 由 handler 内路由判定）。"""
    import deps
    from routers.shots import _handler_video
    from src.core.models import ShotStatus

    progress: list[tuple] = []

    async def emit(stage, msg, pct):
        progress.append((stage, str(msg)[:160], pct))
        print(f"      [{shot_id}] {stage}: {str(msg)[:100]}", flush=True)

    params = {"origin_run_id": f"c1_e2e_{shot_id}"}
    if video_model:
        params["video_model"] = video_model
    job = type("J", (), {"project": project_name, "shot_id": shot_id,
                         "params": params, "kind": "video"})()   # 鸭子类型 job 对象
    t0 = time.time()
    await _handler_video(job, emit)
    wall = round(time.time() - t0, 1)

    from src.core.store import ProjectStore
    project = ProjectStore(pdir).load()
    shot = project.get_shot(shot_id)
    version = next(vv for vv in shot.video_versions
                   if vv.version_id == shot.final_video)
    assert version.status == ShotStatus.DONE.value
    return {"shot": shot, "version": version, "wall": wall, "progress": progress}


def _freshness(pdir, project, shot):
    from src.generation.native_av_pack import native_av_freshness
    return native_av_freshness(pdir, project, shot)


def _speaker_expectation(pdir, shot):
    from src.generation.dialogue_control import resolve_dialogue_language, resolve_native_dialogue
    from src.core.store import ProjectStore
    project = ProjectStore(pdir).load()
    try:
        lang = resolve_dialogue_language(project, None)
        ctl = resolve_native_dialogue(pdir, shot, language=lang)
    except Exception as e:                                    # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:200]}
    if ctl is None:
        return {"resolution": "no_dialogue"}
    return {"resolution": ctl.resolution,
            "lines": [{"index": ln.index, "text": ln.text,
                       "speaker": ln.speaker_character_name,
                       "delivery_hints": list(ln.delivery_hints)}
                      for ln in ctl.lines],
            "directions": [{"index": d.index, "text": d.text,
                            "speaker": d.speaker_character_name}
                           for d in ctl.directions],
            "silent": list(ctl.silent_characters)}


def _sample_meta(pdir, key, shot_id):
    from src.core.store import ProjectStore
    shot = ProjectStore(pdir).load().get_shot(shot_id)
    return {"key": key, "shot_id": shot_id,
            "characters": list(shot.characters or []),
            "dialogue_truth": shot.dialogue or "",
            "expected_speaker_sequence": _speaker_expectation(pdir, shot)}


async def main():
    from src.core.store import ProjectStore
    from src.generation.native_av_pack import native_av_freshness

    agnes = boot()
    unbound_pdir = ROOT / "projects" / PROJECT_NAME
    bound_pdir = ROOT / "projects" / BOUND_PROJECT_NAME
    assert (unbound_pdir / "output" / "project.json").exists(), "先跑 setup_projects.py"
    assert (bound_pdir / "output" / "project.json").exists(), "BOUND fixture 项目缺失"

    EVID.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    failures: list[dict] = []

    specs = [(s["key"], s["shot_id"], PROJECT_NAME, unbound_pdir) for s in SHOTS]
    specs.append(("G", BOUND_SHOT["shot_id"], BOUND_PROJECT_NAME, bound_pdir))

    for key, shot_id, pname, pdir in specs:
        print(f"\n=== Sample {key} ({pname}/{shot_id}) ===", flush=True)
        d = EVID / key
        if (d / "video.mp4").exists() and (d / "ffprobe.json").exists():
            print("  done (断点续跑跳过)", flush=True)
            continue
        d.mkdir(parents=True, exist_ok=True)
        try:
            t0 = time.time()
            out = await run_video_handler(pdir, pname, shot_id)
            version = out["version"]
            project = ProjectStore(pdir).load()
            freshness = _freshness(pdir, project, project.get_shot(shot_id))
            jwrite(d / "job_receipt.json", {
                "shot_id": shot_id, "project": pname, "wall_seconds": out["wall"],
                "progress": [list(p) for p in out["progress"]]})
            jwrite(d / "video_version.json", {
                "version_id": version.version_id,
                "prompt_snapshot": version.prompt_snapshot,
                "generation_basis": version.generation_basis,
                "model": version.model,
                "num_frames": version.num_frames,
                "seconds_returned": version.seconds_returned,
                "size_returned": version.size_returned,
                "final_video": project.get_shot(shot_id).final_video})
            jwrite(d / "freshness.json", freshness)
            shutil.copy2(version.local, d / "video.mp4")
            jwrite(d / "ffprobe.json", ffprobe_media(d / "video.mp4"))
            print(f"  DONE {key} wall={out['wall']}s freshness={freshness['status']}",
                  flush=True)
            results.append({"key": key, "shot_id": shot_id, "ok": True,
                            "freshness": freshness["status"]})
        except Exception as e:                                # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:400]
            print(f"  FAIL {key}: {err}", flush=True)
            jwrite(d / "error.json", {"error": err})
            failures.append({"key": key, "error": err})

    await agnes.aclose()

    # ---- review manifest（评审网站数据源；§34 复用轻量 review website）----
    samples = []
    for key, shot_id, pname, pdir in specs:
        d = EVID / key
        meta = _sample_meta(pdir, key, shot_id)
        item = {**meta,
                "route": "bound" if key == "G" else "unbound",
                "project": pname}
        if (d / "video_version.json").exists():
            vv = json.loads((d / "video_version.json").read_text(encoding="utf-8"))
            fr = json.loads((d / "freshness.json").read_text(encoding="utf-8"))
            fp = json.loads((d / "ffprobe.json").read_text(encoding="utf-8"))
            item.update({
                "video": f"evidence/{key}/video.mp4",
                "prompt_preview": vv["prompt_snapshot"][:600],
                "prompt_snapshot_full": vv["prompt_snapshot"],
                "generation_basis": vv["generation_basis"],
                "freshness": fr,
                "duration_s": fp.get("duration_seconds"),
                "size": fp.get("size"),
            })
        else:
            item["error"] = (d / "error.json").exists() and json.loads(
                (d / "error.json").read_text(encoding="utf-8"))["error"]
        samples.append(item)
    jwrite(EVID / "review_manifest.json", {
        "language": LANGUAGE, "samples": samples,
        "verdict_dimensions": ["dialogue_correctness", "speaker_correctness",
                               "language", "lip_sync", "natural_performance",
                               "visual_quality", "sfx_ambience_fit",
                               "unexpected_bgm", "overall_av",
                               "bound_continuity"]})

    print(f"\n==== C1 E2E COMPLETE ok={len(results)} fail={len(failures)} ====")
    for f in failures:
        print(f"  FAIL {f['key']}: {f['error'][:200]}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
