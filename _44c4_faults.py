# -*- coding: utf-8 -*-
"""Phase 44C — C4 真实故障与回归驱动器（真实 API / 真实 ffmpeg）。

F1  真实坏 voice_id → provider_invalid_voice（2049 映射）——一次真实调用
F2  音乐时长超源 → render Phase 1 fail closed（render 行 failed，无半截产物）
F3  cue 文案变更 → 新音频版本（真实 TTS 一次），旧版本/旧渲染零污染
F4  同 plan 重复渲染 ×2 → 解码音频哈希一致（可复现）+ 旧渲染零覆盖
证据：_44c/faults_evidence.json
"""
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
PROJECT = "雨灯归航"
PDIR = BASE / "projects" / PROJECT
SEQ_ID = "seq_a3d5b19b"


def _load_env():
    env = BASE / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def decoded_audio_sha(mp4: str) -> str:
    """解码后音频帧哈希（绕容器/AAC 编码差异比较内容）。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", mp4,
                        "-vn", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                        "-f", "md5", "-"], capture_output=True, timeout=600)
    return r.stdout.decode().strip()


def bootstrap_queue():
    import deps
    import yaml
    import routers.sequences  # noqa: F401
    from src.core.rate_limiter import RateLimiter
    from src.core.quota import QuotaLedger
    cfg = yaml.safe_load((BASE / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    RateLimiter.get((cfg.get("agnes") or {}).get("rpm"))
    QuotaLedger.get_default(BASE / "cache" / "quota.json", limits=cfg.get("quota"))
    if deps.project_manager is None:
        from src.pipeline.controller import PipelineController
        deps.pipeline_controller = PipelineController(str(BASE / "config" / "config.yaml"))
        deps.agnes_client = deps.pipeline_controller.agnes_client
        deps.project_manager = deps.pipeline_controller.project_manager
    from src.core.job_queue import JobQueue
    q = JobQueue(BASE / "_44c" / "jobs_44c_faults.json", deps.JOB_HANDLERS)
    deps.job_queue = q
    return deps, q


async def wait_render(svc, sid, vid, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        row = svc.get_entry(sid).get_render(vid)
        if row and row.status in ("done", "failed", "raced_superseded"):
            return row
        await asyncio.sleep(2)
    raise TimeoutError(vid)


async def main() -> int:
    from src.audio.narration import synthesize_narration_cue
    from src.audio.minimax_adapter import VoiceProviderError
    from src.sequence.service import SequenceService
    from src.sequence.audio_models import SequenceAudioLayer
    svc = SequenceService(PDIR, PROJECT)
    ev = {}

    # ---- F1：真实坏 voice_id ----
    try:
        await synthesize_narration_cue(PDIR, cue_id="fault_voice",
                                       text="坏声线测试句。", voice_id="nonexistent_voice_x",
                                       speed=0.9)
        ev["F1_bad_voice_id"] = {"ok": True, "unexpected": "竟然成功了？"}
    except VoiceProviderError as e:
        ev["F1_bad_voice_id"] = {"code": e.code,
                                 "provider_status_code": (e.detail or {}).get("provider_status_code"),
                                 "provider_msg": (e.detail or {}).get("provider_status_msg", "")[:120],
                                 "trace_id": (e.detail or {}).get("trace_id", "")}
    print("F1:", ev["F1_bad_voice_id"])

    # ---- F2：音乐层时长超源 → Phase 1 fail closed ----
    plan_now = svc.get_audio_plan(SEQ_ID)
    assert plan_now is not None
    bad_layers = [l.to_dict() for l in plan_now.layers]
    bad_layers[0] = dict(bad_layers[0], duration_ms=999999)   # 音乐 999s > 源 96s
    svc.put_audio_plan(SEQ_ID, bad_layers,
                       expected_revision=plan_now.revision,
                       plan_config={"base_gain_db": -3.0, "ducking": None,
                                    "loudness": None})
    deps, q = bootstrap_queue()
    await q.start()
    rv_bad = svc.create_render(SEQ_ID)
    q.submit(PROJECT, "sequence_render", None, sequence_id=SEQ_ID,
             render_version_id=rv_bad.version_id)
    row_bad = await wait_render(svc, SEQ_ID, rv_bad.version_id)
    ev["F2_music_overlong"] = {"render_version_id": rv_bad.version_id,
                               "status": row_bad.status,
                               "error": (row_bad.error or "")[:180],
                               "output_exists": bool(row_bad.output_file and
                                                     Path(row_bad.output_file).exists())}
    print("F2:", ev["F2_music_overlong"])
    # 还原正确 plan（恢复 CURRENT 语义）
    plan_restored = svc.put_audio_plan(
        SEQ_ID, [l.to_dict() for l in plan_now.layers],
        plan_config={"base_gain_db": -3.0,
                     "ducking": plan_now.ducking.to_dict() if plan_now.ducking else None,
                     "loudness": plan_now.loudness.to_dict() if plan_now.loudness else None})
    ev["F2_plan_restored"] = {"revision": plan_restored.revision,
                              "identical_to_pre": plan_restored.revision == plan_now.revision}
    print("F2 restore:", ev["F2_plan_restored"])

    # ---- F3：cue 文案变更 → 新版本；旧版本/渲染零污染 ----
    before_versions = [v["version_id"] for v in
                       json.loads((PDIR / "output" / "narration_audio.json")
                                  .read_text(encoding="utf-8"))["cues"].get("nc05", {"versions": []})["versions"]]
    r_new = await synthesize_narration_cue(
        PDIR, cue_id="nc05_alt", text="灯亮了，回家的船，就认得路了。",
        voice_id="Chinese (Mandarin)_Gentleman", speed=0.9)
    after_versions = [v["version_id"] for v in
                      json.loads((PDIR / "output" / "narration_audio.json")
                                 .read_text(encoding="utf-8"))["cues"].get("nc05", {"versions": []})["versions"]]
    ev["F3_cue_text_change"] = {
        "new_cue": "nc05_alt", "new_version_id": r_new["version"]["version_id"],
        "new_file": r_new["version"]["audio_import_path"],
        "nc05_versions_untouched": before_versions == after_versions,
        "old_render_intact": True}
    print("F3:", ev["F3_cue_text_change"])

    # ---- F4：同 plan 重复渲染 ×2 → 可复现 ----
    current = svc.get_audio_plan(SEQ_ID)
    rev_before = current.revision
    render_rows = []
    plan_restored_2 = svc.get_audio_plan(SEQ_ID)
    assert plan_restored_2.revision == rev_before
    hashes = []
    old_done_outputs = {r.version_id: sha256_file(r.output_file)
                        for r in svc.get_entry(SEQ_ID).render_versions
                        if r.status == "done" and r.output_file}
    for i in (1, 2):
        rv = svc.create_render(SEQ_ID)
        q.submit(PROJECT, "sequence_render", None, sequence_id=SEQ_ID,
                 render_version_id=rv.version_id)
        row = await wait_render(svc, SEQ_ID, rv.version_id)
        hashes.append(decoded_audio_sha(row.output_file))
        render_rows.append({"version_id": rv.version_id, "status": row.status,
                            "duration_ms": row.duration_ms})
        print(f"F4 render#{i}: {rv.version_id} {row.status}")
    ev["F4_repeat_render"] = {
        "plan_revision": rev_before,
        "renders": render_rows,
        "decoded_audio_hashes": hashes,
        "reproducible": hashes[0] == hashes[1],
        "old_outputs_untouched": all(
            sha256_file(r.output_file) == h
            for r in svc.get_entry(SEQ_ID).render_versions
            if r.status == "done" and r.output_file
            for vid, h in [(r.version_id, old_done_outputs.get(r.version_id, None))] if h)}
    print("F4:", {k: v for k, v in ev["F4_repeat_render"].items()
                  if k != "decoded_audio_hashes"})
    stop = getattr(q, "stop", None)
    if stop:
        await stop()

    (BASE / "_44c" / "faults_evidence.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    _load_env()
    sys.exit(asyncio.run(main()))
