#!/usr/bin/env python3
"""Real >=180s acceptance using the existing FullProject/SceneRun production stack.

This is a bounded test driver, not another producer or a new agent runtime.
Fresh runs only: existing nonces require reconciliation, never blind regeneration.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

MIN_FINAL_SECONDS = 180.0
MIN_PLANNED_SECONDS = 192.0
MAX_PLANNED_SECONDS = 220.0
VIDEO_BUDGET_SECONDS = 240
IMAGE_BUDGET = 120
TEXT_BUDGET = 500

SCENES = [
    ("破晓", "雾海中的灯塔黎明。四个不同的连续视觉重点：海天微光、潮水漫过礁石、灯塔外墙被晨光照亮、旋转灯束在薄雾中渐淡。"),
    ("早潮", "早晨沿海岸接近灯塔。四个不同的连续视觉重点：浪花穿过潮池、海草随清水摆动、石阶上的水滴闪光、灯塔门前的野花迎风。"),
    ("午风", "中午风起，灯塔静静守望。四个不同的连续视觉重点：海鸟掠过海面、云影缓慢越过岬角、航标随波轻摇、窗边光影移动。"),
    ("骤雨", "下午一场短雨经过。四个不同的连续视觉重点：远处雨幕靠近、雨点敲打栏杆、排水沟里清水流动、乌云裂开透出一束阳光。"),
    ("归灯", "雨后入夜，灯塔再次点亮。四个不同的连续视觉重点：海面映出金色晚霞、潮湿石阶反射天光、灯室缓缓亮起、灯束稳定扫过静谧海面。"),
]


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def validate_planned_project(project):
    shots = project.all_shots()
    durations = [float(s.target_duration_s or 0) for s in shots]
    if len(project.scenes) != 5 or not 16 <= len(shots) <= 28:
        raise ValueError("acceptance requires five scenes and 16-28 distinct shots")
    if any(not 4 <= d <= 11 for d in durations):
        raise ValueError("acceptance shots must each request 4-11 seconds")
    total = sum(round(d) for d in durations)
    if not MIN_PLANNED_SECONDS <= total <= MAX_PLANNED_SECONDS:
        raise ValueError(f"planned source duration {total}s is outside 192-220s; no media authorized")
    return {"scene_count": len(project.scenes), "shot_count": len(shots),
            "planned_seconds": total, "durations": durations}


def validate_final_media(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                        "-of", "json", str(path)], capture_output=True, text=True,
                       check=True, timeout=60)
    probe = json.loads(r.stdout)
    duration = float(probe["format"]["duration"])
    videos = [s for s in probe["streams"] if s.get("codec_type") == "video"]
    if duration < MIN_FINAL_SECONDS or not videos:
        raise ValueError(f"final film is only {duration}s or has no video stream")
    # Full decode, not just a plausible container header. No loops or padding.
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                   capture_output=True, check=True, timeout=600)
    return {"duration_s": duration, "video": [{k: s.get(k) for k in
            ("codec_name", "width", "height", "avg_frame_rate")} for s in videos],
            "audio_streams": sum(s.get("codec_type") == "audio" for s in probe["streams"]),
            "full_decode_ok": True}


class BoundedRealClient:
    """Forward real calls, with a CAS write-ahead journal and a hard test budget."""

    def __init__(self, client, checkpoint, project_dir, evidence_dir):
        self.client, self.checkpoint = client, checkpoint
        self.project_dir, self.evidence_dir = Path(project_dir), Path(evidence_dir)
        self.lock = asyncio.Lock()
        self.video_attempts = []
        self.image_calls = self.text_calls = 0
        self.plan = None
        self.submission_blocked = False

    def __getattr__(self, name):
        return getattr(self.client, name)

    def media_gate(self):
        if self.plan is None:
            from src.core.store import ProjectStore
            self.plan = validate_planned_project(ProjectStore(self.project_dir).load())
            write_json(self.evidence_dir / "planned-duration.json", self.plan)
        return self.plan

    async def persist(self, **extra):
        async with self.lock:
            fields = dict(video_attempts=self.video_attempts, image_calls=self.image_calls,
                          text_calls=self.text_calls, updated_at=utc_now(), **extra)
            await asyncio.to_thread(self.checkpoint.save, **fields)

    async def chat(self, *args, **kwargs):
        self.text_calls += 1
        if self.text_calls > TEXT_BUDGET:
            raise RuntimeError("acceptance text-call budget exhausted")
        return await self.client.chat(*args, **kwargs)

    async def generate_image(self, *args, **kwargs):
        self.media_gate()
        self.image_calls += 1
        if self.image_calls > IMAGE_BUDGET:
            raise RuntimeError("acceptance image budget exhausted")
        return await self.client.generate_image(*args, **kwargs)

    async def create_video(self, prompt, **kwargs):
        self.media_gate()
        seconds = int(kwargs.get("duration_s") or 5)
        if not 4 <= seconds <= 12:
            raise ValueError("invalid provider duration")
        if self.submission_blocked or any(a["state"] in ("SUBMITTING", "UNKNOWN") for a in self.video_attempts):
            raise RuntimeError("unconfirmed prior submission; reconciliation required, no automatic POST")
        if sum(a["seconds"] for a in self.video_attempts) + seconds > VIDEO_BUDGET_SECONDS:
            raise RuntimeError("acceptance 240-second submission budget exhausted")
        attempt = {"ordinal": len(self.video_attempts) + 1, "seconds": seconds,
                   "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                   "state": "SUBMITTING", "started_at": utc_now()}
        self.video_attempts.append(attempt)
        await self.persist()  # durable intent precedes the paid HTTP request
        try:
            result = await self.client.create_video(prompt, **kwargs)
            vid = result.get("video_id") or result.get("task_id")
            if not isinstance(vid, str) or not vid:
                raise RuntimeError("provider receipt has no confirmed video ID")
            attempt.update(state="ACCEPTED", video_id=vid)
            await self.persist()
            return result
        except Exception:
            self.submission_blocked = True
            # Even a response/persistence failure must block automatic resubmission.
            # Do not overwrite a confirmed ACCEPTED ID with a fabricated rejection.
            if attempt["state"] == "SUBMITTING":
                attempt["state"] = "UNKNOWN"
            try:
                await self.persist()
            except Exception:
                pass
            raise


def archive_completed_project(token, nonce, project_dir, final_path, evidence_dir, source_sha):
    from tools.e2e_autonomous_run import B1GitHubCas, sha256_file
    from tools.relstore import archive_campaign, restore_campaign
    from tools.relstore.github_release_adapter import GitHubReleaseAdapter, HttpxTransport
    from tools.relstore.project_registry import Registry
    from tools.video_task_state import task_index

    adapter = GitHubReleaseAdapter.armed("Q-1844", "agent-system-v2-rundata", token, HttpxTransport())
    tag = f"full-project/{nonce}"
    release = adapter.create_release(tag, name=f"Full project >=180s {nonce}", draft=True,
                                     target_commitish="main")
    media_sha = sha256_file(final_path)
    media = adapter.upload_asset_with_recovery(release.id, "final.mp4", final_path.read_bytes(), media_sha)
    if hashlib.sha256(adapter.download_asset_authenticated(media.id)).hexdigest() != media_sha:
        raise RuntimeError("final MP4 re-download SHA mismatch")
    opdir = evidence_dir / "archive"
    archive_campaign.plan(project_dir=project_dir, opdir=opdir, operation_id=f"archive-{nonce}",
                          project_id=nonce, revision=1, lifecycle_status="completed",
                          source_code_commit=source_sha)
    packaged = archive_campaign.package(opdir)
    package_path = Path(packaged["package"]["path"])
    package_sha = packaged["package"]["sha256"]
    package = adapter.upload_asset_with_recovery(release.id, "project.tar", package_path.read_bytes(), package_sha)
    redownload = adapter.download_asset_authenticated(package.id)
    downloaded = evidence_dir / "redownload-project.tar"
    downloaded.write_bytes(redownload)
    restored = restore_campaign.restore_package(downloaded, evidence_dir / "restore-check",
                                                expected_sha256=package_sha)
    if restored["restorable_level"] != "full" or restored["missing_required"]:
        raise RuntimeError("project snapshot did not pass full restore verification")
    write_json(evidence_dir / "restore-report.json", restored)
    registry = Registry(cas=B1GitHubCas(task_index(token, nonce, "project-registry.json")))
    registry.create_project(project_id=nonce, alias=project_dir.name, operation_id=f"{nonce}-create")
    registry.record_activity(project_id=nonce, kind="generate_video", operation_id=f"{nonce}-activity")
    registry.plan_archive(project_id=nonce, operation_id=f"{nonce}-plan")
    tree_fingerprint = packaged["package"]["payload"]["snapshot_fingerprint"]
    registry.begin_snapshot(project_id=nonce, snapshot_fingerprint=tree_fingerprint, operation_id=f"{nonce}-snapshot")
    ref = {"repo": "Q-1844/agent-system-v2-rundata", "release_tag": tag,
           "release_id": release.id, "asset_id": package.id, "asset_name": "project.tar",
           "sha256": package_sha, "tree_fingerprint": tree_fingerprint,
           "bytes": len(redownload), "final_mp4_asset_id": media.id, "final_mp4_sha256": media_sha}
    registry.set_verified_restorable(project_id=nonce, archive_ref=ref, restorable_level="full",
                                     operation_id=f"{nonce}-verified")
    return {"archive_ref": ref, "registry": registry.to_doc(),
            "restore_files_checked": restored["files_checked"], "restorable_level": "full"}


async def run_acceptance(nonce, out, summary):
    from PIL import Image
    from tools.e2e_autonomous_run import ensure_branch
    from tools.video_task_state import ProductionCheckpoint, task_index
    from src.core.quota import QuotaLedger
    from src.core.store import ProjectStore
    from src.core.models import Project, Scene, StyleAnchor
    from src.core.image_host import GithubPagesHost

    token = os.environ["B1_ARCHIVE_TOKEN"]
    if not os.environ.get("AGNES_API_KEY"):
        raise RuntimeError("missing AGNES_API_KEY")
    idx = task_index(token, nonce, "full-project-production.json")
    ensure_branch(idx)
    digest = hashlib.sha256(json.dumps({"scope": "full-project-180s-v1", "scenes": SCENES,
                                        "video_budget_s": VIDEO_BUDGET_SECONDS}, ensure_ascii=False).encode()).hexdigest()
    cp = ProductionCheckpoint(idx, nonce, digest)
    if cp.current is not None:
        raise RuntimeError("nonce already has a run; restore/reconcile existing evidence before resuming")
    cp.save(phase="STARTING", github_run_id=os.environ.get("GITHUB_RUN_ID"), source_sha=os.environ.get("GITHUB_SHA"))
    # Check the existing image-host write path before any Agnes generation.
    host = GithubPagesHost("Q-1844", "agnes-frame-host", "main",
                           os.environ.get("FRAME_HOST_TOKEN") or token)
    calibration = out / "host-calibration.png"
    Image.new("RGB", (8, 8), (48, 80, 110)).save(calibration)
    summary["frame_host_preflight_url"] = await host.upload(calibration)
    QuotaLedger.get_default(Path("cache/quota.json"), limits={"video_seconds": VIDEO_BUDGET_SECONDS,
                                                           "images": IMAGE_BUDGET, "text_calls": TEXT_BUDGET})
    import deps
    import api_server
    from src.ai.full_project_autopilot import get_full_project_manager
    await api_server.startup()
    if deps.agnes_client is None or deps.job_queue is None or deps.project_manager is None:
        raise RuntimeError("application dependencies failed to initialize")
    deps._rehost_host = host
    project_name = f"cloud3min_{nonce}"
    project_dir = deps.project_manager.create_project(project_name)
    scenes = [Scene(scene_id=f"s{i:02d}", title=title,
                    summary=story + " 本段必须规划4个连续镜头，每镜10秒，总计40秒；动作节奏舒缓，每镜只表现上述一个视觉重点。无人物，无对白。")
              for i, (title, story) in enumerate(SCENES, start=1)]
    project = Project(name=project_name, story_text="《雾海守望》：从破晓、早潮、午风、骤雨到归灯，记录海边灯塔的一天。五段共约200秒；不重复素材，不使用静帧或循环补时长。",
                      style=StyleAnchor(art_style="hand-painted cinematic animation, coherent watercolor illustration",
                                        palette="sea blue, warm amber, restrained natural colors", aspect_ratio="16:9"),
                      characters=[], scenes=scenes, next_scene_seq=6)
    ProjectStore(project_dir).save(project)
    wrapper = BoundedRealClient(deps.agnes_client, cp, project_dir, out)
    deps.agnes_client = wrapper
    manager = get_full_project_manager()
    full = await manager.start_run(project_name)
    summary.update(project_name=project_name, full_run_id=full.run_id,
                   project_dir=str(project_dir), source_sha=os.environ.get("GITHUB_SHA"))
    await wrapper.persist(phase="RUNNING", project_name=project_name, full_run_id=full.run_id)
    deadline, last_report = time.monotonic() + 150 * 60, 0.0
    while True:
        full = manager.get_run(project_name, full.run_id)
        if full.status in ("completed", "failed", "blocked", "cancelled", "paused"):
            break
        if time.monotonic() >= deadline:
            await manager.request_pause(project_name, full.run_id)
            raise TimeoutError("150-minute test deadline; evidence retained, no blind restart")
        if time.monotonic() - last_report >= 30:
            progress = {"at": utc_now(), "full_run": full.model_dump(),
                        "video_attempts": wrapper.video_attempts,
                        "image_calls": wrapper.image_calls, "text_calls": wrapper.text_calls}
            write_json(out / "progress.json", progress)
            await wrapper.persist(progress={"status": full.status, "stage": full.stage,
                                            "scene": full.current_scene_id, "action": full.current_action})
            print(json.dumps({"status": full.status, "stage": full.stage,
                              "scene": full.current_scene_id, "video_submissions": len(wrapper.video_attempts),
                              "at": utc_now()}), flush=True)
            last_report = time.monotonic()
        await asyncio.sleep(3)
    summary["full_run"] = full.model_dump()
    summary["provider_video_attempts"] = wrapper.video_attempts
    write_json(out / "full-run.json", full.model_dump())
    if full.status != "completed":
        await wrapper.persist(phase="FAILED", terminal_status=full.status, error=full.error)
        raise RuntimeError(f"FullProject did not complete: {full.status}: {full.error}")
    from src.ai.project_autopilot import get_project_autopilot_manager
    child = get_project_autopilot_manager().get_run(project_name, full.production_run_id)
    summary["project_production_run"] = child.model_dump()
    if len(child.scenes) != 5 or any(s.status != "completed" for s in child.scenes):
        raise RuntimeError("not all five native SceneRuns completed")
    preparation = json.loads(Path(full.preparation_manifest_file).read_text(encoding="utf-8"))
    if any(s.get("director_source") != "generated_by_full_run" or
           s.get("production_plan_source") != "generated_by_full_run" for s in preparation["scenes"]):
        raise RuntimeError("fresh real Director/Planner evidence is missing")
    manifest = json.loads(Path(child.composition_manifest_file).read_text(encoding="utf-8"))
    project = ProjectStore(project_dir).load()
    clips = manifest["clips"]
    if len(clips) != len(project.all_shots()) or len({c["shot_id"] for c in clips}) != len(clips):
        raise RuntimeError("composition does not contain exactly one clip per original shot")
    sources = []
    accepted_ids = {a.get("video_id") for a in wrapper.video_attempts if a["state"] == "ACCEPTED"}
    for clip in clips:
        shot = project.get_shot(clip["shot_id"])
        version = next((v for v in shot.video_versions if v.version_id == clip["video_version_id"]), None)
        if version is None or version.video_id not in accepted_ids or shot.final_video != version.version_id:
            raise RuntimeError("composition references an unverified or unselected source version")
        data = Path(clip["local_path"]).read_bytes()
        sources.append({"shot_id": shot.shot_id, "video_id": version.video_id,
                        "version_id": version.version_id, "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(), "seconds_returned": version.seconds_returned})
    if len({s["sha256"] for s in sources}) != len(sources):
        raise RuntimeError("duplicate source-video bytes cannot count towards long-film acceptance")
    summary["source_videos"] = sources
    write_json(out / "source-videos.json", sources)
    final = Path(full.final_output)
    summary["media"] = await asyncio.to_thread(validate_final_media, final)
    summary["plan"] = wrapper.media_gate()
    accepted = [a for a in wrapper.video_attempts if a["state"] == "ACCEPTED"]
    if len({a["video_id"] for a in accepted}) != len(accepted):
        raise RuntimeError("provider returned a duplicate video ID across submissions")
    summary["saved"] = await asyncio.to_thread(archive_completed_project, token, nonce, project_dir,
                                               final, out, os.environ.get("GITHUB_SHA", "unknown"))
    await wrapper.persist(phase="COMPLETE", duration_s=summary["media"]["duration_s"], saved=summary["saved"])
    summary["status"] = "PASS"


def main():
    from tools.video_task_state import validate_nonce
    nonce = validate_nonce(os.environ.get("TASK_NONCE") or f"film3m-{os.environ.get('GITHUB_RUN_ID') or time.time_ns()}")
    out = Path("e2e_run") / nonce
    summary = {"scope": "real-full-project-at-least-180s", "nonce": nonce,
               "github_run_id": os.environ.get("GITHUB_RUN_ID"), "started_at": utc_now(), "status": "FAIL"}
    out.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(run_acceptance(nonce, out, summary))
    except Exception as exc:
        detail = str(exc)
        for name in ("AGNES_API_KEY", "B1_ARCHIVE_TOKEN", "FRAME_HOST_TOKEN", "GITHUB_TOKEN"):
            if os.environ.get(name):
                detail = detail.replace(os.environ[name], "<REDACTED>")
        summary.update(error_type=type(exc).__name__, error=detail[:1600])
        print(f"full-project acceptance failed ({type(exc).__name__}): {detail[:300]}", flush=True)
    finally:
        summary["finished_at"] = utc_now()
        write_json(out / "summary.json", summary)
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
