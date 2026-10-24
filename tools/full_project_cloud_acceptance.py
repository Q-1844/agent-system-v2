#!/usr/bin/env python3
"""Real >=180s acceptance using the existing FullProject/SceneRun production stack.

This is a bounded test driver, not another producer or a new agent runtime.
Fresh runs only: existing nonces require reconciliation, never blind regeneration.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
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
    ("失联", "深海研究站失去联系，一台橙色双灯、双机械爪的水下救援无人机前来取回记录匣。四镜依次：船底放下无人机；无人机下潜进入黑暗；探照灯照见破损站体；无人机靠近半开的舱门。"),
    ("入舱", "同一台橙色双灯、双机械爪无人机进入淹水的研究站。四镜依次：侧身穿过舱门；灯束扫过漂浮的纸张；避开摇摆电缆；在实验台下发现银色记录匣。"),
    ("取证", "同一无人机要取出银色记录匣。四镜依次：机械爪靠近卡扣；卡扣松开；机械爪夹起记录匣；记录匣收入机身下方固定架。每镜单一动作，记录匣始终只有一个。"),
    ("脱险", "无人机带着记录匣离开时舱体继续变形。四镜依次：舱顶落下碎屑；通道闸门缓慢合拢；无人机从闸门侧隙穿出；冲出站体避开扬起的泥沙。无人机与记录匣外观保持不变。"),
    ("回收", "同一无人机带着唯一的银色记录匣返回海面救援船。四镜依次：无人机向上穿过光柱；浮出波浪；回收吊篮托起无人机；固定架里的记录匣在甲板工作灯下显露。镜头只拍设备，不出现人物。"),
]


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def acceptance_credentials(environ):
    # The archive token can be scoped to B/B1. Never silently reuse it for D.
    for name in ("AGNES_API_KEY", "B1_ARCHIVE_TOKEN", "FRAME_HOST_TOKEN"):
        if not environ.get(name, "").strip():
            raise RuntimeError(f"missing {name}; no media generation started")
    return environ["B1_ARCHIVE_TOKEN"], environ["FRAME_HOST_TOKEN"]


def make_frame_host_calibration(path):
    """Force the normal content-addressed host path to exercise a fresh PUT."""
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    metadata = PngInfo()
    # An existing public object proves readability, not the current token's
    # ability to write. Fresh metadata changes bytes without changing pixels.
    metadata.add_text("write-check-nonce", os.urandom(16).hex())
    path = Path(path)
    Image.new("RGB", (8, 8), (48, 80, 110)).save(path, pnginfo=metadata)
    return path


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
    container_duration = float(probe["format"]["duration"])
    videos = [s for s in probe["streams"] if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    if len(videos) != 1:
        raise ValueError("final film must have exactly one moving video stream")
    # A long audio track or NaN container duration cannot establish a 3-minute film.
    duration = float(videos[0].get("duration", "nan"))
    if (not math.isfinite(duration) or not math.isfinite(container_duration)
            or duration < MIN_FINAL_SECONDS or container_duration < MIN_FINAL_SECONDS):
        raise ValueError(f"final moving video is only {duration}s (container {container_duration}s)")
    # Full decode, not just a plausible container header. No loops or padding.
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
                   capture_output=True, check=True, timeout=600)
    return {"duration_s": duration, "container_duration_s": container_duration,
            "video": [{k: s.get(k) for k in
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

    def restore_usage(self, document):
        attempts = json.loads(json.dumps(document.get("video_attempts") or []))
        if any(a.get("state") != "ACCEPTED" or not a.get("video_id")
               or type(a.get("seconds")) is not int or not 4 <= a["seconds"] <= 12 for a in attempts):
            raise RuntimeError("unconfirmed paid submission requires reconciliation")
        if len({a["video_id"] for a in attempts}) != len(attempts):
            raise RuntimeError("duplicate restored video receipt")
        self.video_attempts = attempts
        self.image_calls = int(document.get("image_calls") or 0)
        self.text_calls = int(document.get("text_calls") or 0)

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
    from tools.e2e_autonomous_run import ensure_branch
    from tools.video_task_state import ProductionCheckpoint, task_index
    from src.core.quota import QuotaLedger
    from src.core.store import ProjectStore
    from src.core.models import Project, Scene, StyleAnchor
    from src.core.image_host import GithubPagesHost

    token, frame_token = acceptance_credentials(os.environ)
    idx = task_index(token, nonce, "full-project-production.json")
    ensure_branch(idx)
    digest = hashlib.sha256(json.dumps({"scope": "full-project-180s-v1", "scenes": SCENES,
                                        "video_budget_s": VIDEO_BUDGET_SECONDS}, ensure_ascii=False).encode()).hexdigest()
    cp = ProductionCheckpoint(idx, nonce, digest)
    resume_id = os.environ.get("RESUME_RUN_ID", "").strip()
    restored = None
    previous = json.loads(json.dumps(cp.doc))
    if resume_id:
        if cp.current is None:
            raise RuntimeError("resume nonce has no authoritative checkpoint")
        from tools.full_project_resume import download_snapshot, restore_snapshot
        staging = out / "source-snapshot"
        provenance = await asyncio.to_thread(download_snapshot, resume_id, nonce,
                                             os.environ.get("GITHUB_TOKEN"), staging)
        restored = restore_snapshot(staging, Path.cwd(), nonce, previous, provenance)
        import shutil
        shutil.rmtree(staging)  # the live copy is retained; avoid nesting old snapshots in new artifacts
        summary.update(project_name=restored["project_name"], full_run_id=restored["full_run_id"],
                       project_dir=str(Path.cwd()/"projects"/restored["project_name"]),
                       source_sha=os.environ.get("GITHUB_SHA"), resume=restored)
        write_json(out / "restore-input.json", restored)
        cp.save(phase="RESTORED", github_run_id=os.environ.get("GITHUB_RUN_ID"),
                source_sha=os.environ.get("GITHUB_SHA"), resume_from_run_id=resume_id,
                resume_history=[*(previous.get("resume_history") or []), provenance])
    else:
        if cp.current is not None:
            raise RuntimeError("nonce already has a run; restore/reconcile existing evidence before resuming")
        cp.save(phase="STARTING", github_run_id=os.environ.get("GITHUB_RUN_ID"), source_sha=os.environ.get("GITHUB_SHA"))
    # Check the existing image-host write path before any Agnes generation.
    host = GithubPagesHost("Q-1844", "agnes-frame-host", "main", frame_token)
    calibration = make_frame_host_calibration(out / "host-calibration.png")
    try:
        summary["frame_host_preflight_url"] = await host.upload(calibration)
    except Exception as exc:
        summary.update(blocked_stage="frame_host_preflight", no_new_media_submitted=True)
        cp.save(phase="BLOCKED_PREFLIGHT", no_new_media_submitted=True,
                blocked_stage="frame_host_preflight", error_type=type(exc).__name__)
        raise RuntimeError(f"D frame-host preflight failed before media generation: {exc}") from exc
    QuotaLedger.get_default(Path("cache/quota.json"), limits={"video_seconds": VIDEO_BUDGET_SECONDS,
                                                           "images": IMAGE_BUDGET, "text_calls": TEXT_BUDGET})
    import deps
    import api_server
    from src.ai.full_project_autopilot import get_full_project_manager
    await api_server.startup()
    if deps.agnes_client is None or deps.job_queue is None or deps.project_manager is None:
        raise RuntimeError("application dependencies failed to initialize")
    deps._rehost_host = host
    if restored:
        project_name = restored["project_name"]
        project_dir = deps.project_manager.get_project_dir(project_name)
    else:
        project_name = f"cloud3min_{nonce}"
        project_dir = deps.project_manager.create_project(project_name)
        scenes = [Scene(scene_id=f"s{i:02d}", title=title,
                        summary=story + " 本段必须规划4个连续镜头，每镜10秒，总计40秒；每镜只表现上述一个动作。无人物，无对白。")
                  for i, (title, story) in enumerate(SCENES, start=1)]
        project = Project(name=project_name, story_text="《失联深海站》：橙色双灯、双机械爪救援无人机潜入淹水的研究站，取回唯一的银色记录匣，在舱体变形中撤离并返回救援船。五段共约200秒；不重复素材，不使用静帧或循环补时长。此次只验收设备动作、跨场景生产和归档；不声称验收人物或对白一致性。",
                          style=StyleAnchor(art_style="photorealistic cinematic underwater rescue thriller, coherent practical machinery",
                                            palette="deep ocean blue, safety orange, warm work lights", aspect_ratio="16:9"),
                          characters=[], scenes=scenes, next_scene_seq=6)
        ProjectStore(project_dir).save(project)
    wrapper = BoundedRealClient(deps.agnes_client, cp, project_dir, out)
    if restored:
        wrapper.restore_usage(previous)
        wrapper.media_gate()
    deps.agnes_client = wrapper
    manager = get_full_project_manager()
    full = (await manager.resume_run(project_name, restored["full_run_id"]) if restored
            else await manager.start_run(project_name))
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
               "github_run_id": os.environ.get("GITHUB_RUN_ID"), "started_at": utc_now(), "status": "FAIL",
               "working_directory": str(Path.cwd())}
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
