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

from tools import quota_gate

MIN_FINAL_SECONDS = 180.0
MIN_PLANNED_SECONDS = 192.0
MAX_PLANNED_SECONDS = 220.0
VIDEO_BUDGET_SECONDS = 240
IMAGE_BUDGET = 120
TEXT_BUDGET = 500


def runtime_deadline_minutes(environ):
    try:
        driver = int(environ.get('DRIVER_TIMEOUT_MINUTES', '330'))
        workflow = int(environ.get('WORKFLOW_TIMEOUT_MINUTES', '360'))
    except (ValueError, TypeError):
        raise ValueError('timeouts must be whole minutes') from None
    if not 30 <= driver <= workflow - 30 or not 60 <= workflow <= 360:
        raise ValueError('driver must leave at least 30 minutes within the <=360-minute workflow')
    return driver

SCENES = [
    ("失联", "深海研究站失去联系，一台橙色双灯、双机械爪的水下救援无人机前来取回记录匣。四镜依次：船底放下无人机；无人机下潜进入黑暗；探照灯照见破损站体；无人机靠近半开的舱门。"),
    ("入舱", "同一台橙色双灯、双机械爪无人机进入淹水的研究站。四镜依次：侧身穿过舱门；灯束扫过漂浮的纸张；避开摇摆电缆；在实验台下发现银色记录匣。"),
    ("取证", "同一无人机要取出银色记录匣。四镜依次：机械爪靠近卡扣；卡扣松开；机械爪夹起记录匣；记录匣收入机身下方固定架。每镜单一动作，记录匣始终只有一个。"),
    ("脱险", "无人机带着记录匣离开时舱体继续变形。四镜依次：舱顶落下碎屑；通道闸门缓慢合拢；无人机从闸门侧隙穿出；冲出站体避开扬起的泥沙。无人机与记录匣外观保持不变。"),
    ("回收", "同一无人机带着唯一的银色记录匣返回海面救援船。四镜依次：无人机向上穿过光柱；浮出波浪；回收吊篮托起无人机；固定架里的记录匣在甲板工作灯下显露。镜头只拍设备，不出现人物。"),
]

# 320s 续集：接着《失联深海站》——记录匣带回后数据揭示热液异常，二度下潜溯源。
# 沿用原片纪律：同一机身外观（橙色双灯；s03 换装采样爪后机身不变）、无人物、
# 无对白、每镜单一动作、记录匣/数据核心始终唯一。
SEQUEL_SCENES = [
    ("破译", "救援船数据舱内，银色记录匣接入读取架开始回传研究站数据。四镜依次：记录匣嵌入读取架卡槽；屏幕亮起数据流；进度条爬满；全息投影浮现研究站完整结构图。镜头只拍设备，不出现人物。"),
    ("异常", "数据揭示失联源于深海热液异常。四镜依次：屏幕上热液读数标红闪烁；警报灯开始旋转；结构图上裂缝路径被高亮；无人机诊断屏列出受损部件清单。"),
    ("整备", "同一架橙色双灯无人机在车间换装采样装备。四镜依次：机械臂为机身换装采样爪；声呐模块装入机身；双探照灯逐一亮起自检；无人机离地悬停测试。换装后机身与双灯外观保持不变。"),
    ("二潜", "无人机重返深海奔赴旧研究站。四镜依次：船底放下无人机潜入黑暗；下潜途中经过发光生物群；探照灯照见旧站残骸；绕开变形坍塌的舱段继续下潜。"),
    ("探源", "无人机抵达热液区寻找失联根源。四镜依次：热液喷口黑烟翻涌；机械爪伸向喷口边缘采集样本；声呐扫描裂缝深处；探照灯照亮喷口之下的异常岩层结构。"),
    ("危局", "热液突然喷发，设备受创。四镜依次：海底震动泥沙腾起；落石击中采样架；机械臂关节迸出火花；无人机紧急转向撤离。"),
    ("舍取", "无人机舍弃样本保住核心数据。四镜依次：机械爪松开采样架；采样架沉入烟尘；机身收紧护住数据核心；无人机冲出扰动区开始上浮。数据核心始终唯一。"),
    ("归晓", "无人机带着完整数据在黎明返回。四镜依次：无人机穿过渐亮的深海水层；朝阳下破水而出；救援船剪影出现在海平面；数据舱屏幕定格完整坐标。镜头只拍设备，不出现人物。"),
]

# 片长档位表：只支持定义过的档位（剧本与预算强绑定，防止误配）。
# 200 档 payload 与旧公式逐字节一致（scope/SCENES/240 不变）→ 旧 nonce 的
# resume 链 digest 完全兼容；320 档新 scope → 新片新身份。
FILM_PROFILES = {
    200: {
        "scope": "full-project-180s-v1",
        "scenes": SCENES,
        "story_text": "《失联深海站》：橙色双灯、双机械爪救援无人机潜入淹水的研究站，取回唯一的银色记录匣，在舱体变形中撤离并返回救援船。五段共约200秒；不重复素材，不使用静帧或循环补时长。此次只验收设备动作、跨场景生产和归档；不声称验收人物或对白一致性。",
        "min_final": 180.0, "planned_min": 192, "planned_max": 220,
        "budget": 240, "image_budget": 120, "text_budget": 500,
        "scene_count": 5, "shot_min": 16, "shot_max": 28,
    },
    320: {
        "scope": "full-project-320s-sequel-v1",
        "scenes": SEQUEL_SCENES,
        "story_text": "《深海回响》：橙色双灯、双机械爪救援无人机携记录匣返航后，数据揭示研究站失联源于深海热液异常；无人机换装采样装备二度下潜，穿越旧站残骸抵达热液区，在喷发危机中舍弃样本保住核心数据，于黎明浮出水面。八段共约320秒；不重复素材，不使用静帧或循环补时长。此次只验收设备动作、跨场景生产和归档；不声称验收人物或对白一致性。",
        "min_final": 300.0, "planned_min": 300, "planned_max": 352,
        "budget": 384, "image_budget": 200, "text_budget": 800,
        "scene_count": 8, "shot_min": 24, "shot_max": 40,
    },
}


def film_profile(environ=None):
    """解析 FILM_TARGET_SECONDS 到受支持的片长档位。返回 (target, profile)。

    'auto' → (AUTO_TARGET, None)：自主规划模式，profile 由 Stage 0 冻结蓝图
    动态导出（见 auto_request_profile），不使用静态档位表。
    """
    raw = (environ or os.environ).get("FILM_TARGET_SECONDS", "200").strip()
    if raw == AUTO_TARGET:
        return AUTO_TARGET, None
    try:
        target = int(raw)
    except (ValueError, TypeError):
        raise ValueError('FILM_TARGET_SECONDS must be whole seconds of a supported profile') from None
    if target not in FILM_PROFILES:
        raise ValueError(f"unsupported film target {target}s; supported: {sorted(FILM_PROFILES)} + 'auto'")
    return target, FILM_PROFILES[target]


def request_digest(target, profile):
    """nonce 身份指纹：scope+剧本+视频预算。200 档与旧公式逐字节一致。"""
    payload = {"scope": profile["scope"], "scenes": profile["scenes"],
               "video_budget_s": profile["budget"]}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


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


def validate_planned_project(project, profile=None):
    profile = profile or FILM_PROFILES[200]
    shots = project.all_shots()
    durations = [float(s.target_duration_s or 0) for s in shots]
    if len(project.scenes) != profile["scene_count"] or not profile["shot_min"] <= len(shots) <= profile["shot_max"]:
        raise ValueError(f"acceptance requires {profile['scene_count']} scenes and "
                         f"{profile['shot_min']}-{profile['shot_max']} distinct shots")
    if any(not 4 <= d <= 11 for d in durations):
        raise ValueError("acceptance shots must each request 4-11 seconds")
    total = sum(round(d) for d in durations)
    if not profile["planned_min"] <= total <= profile["planned_max"]:
        raise ValueError(f"planned source duration {total}s is outside "
                         f"{profile['planned_min']}-{profile['planned_max']}s; no media authorized")
    return {"scene_count": len(project.scenes), "shot_count": len(shots),
            "planned_seconds": total, "durations": durations}


def validate_final_media(path, min_final=MIN_FINAL_SECONDS):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                        "-of", "json", str(path)], capture_output=True, text=True,
                       check=True, timeout=60)
    probe = json.loads(r.stdout)
    container_duration = float(probe["format"]["duration"])
    videos = [s for s in probe["streams"] if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    if len(videos) != 1:
        raise ValueError("final film must have exactly one moving video stream")
    # A long audio track or NaN container duration cannot establish the film length.
    duration = float(videos[0].get("duration", "nan"))
    if (not math.isfinite(duration) or not math.isfinite(container_duration)
            or duration < min_final or container_duration < min_final):
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

    def __init__(self, client, checkpoint, project_dir, evidence_dir, *, finalize_only=False,
                 video_budget=VIDEO_BUDGET_SECONDS, image_budget=IMAGE_BUDGET,
                 text_budget=TEXT_BUDGET, film_target_seconds=200):
        self.client, self.checkpoint = client, checkpoint
        self.project_dir, self.evidence_dir = Path(project_dir), Path(evidence_dir)
        self.lock = asyncio.Lock()
        self.video_attempts = []
        self.image_calls = self.text_calls = 0
        self.plan = None
        self.submission_blocked = False
        self.quota_gate_after = None  # 每日额度耗尽时的日历闸门（UTC ISO）
        self.finalize_only = finalize_only
        self.video_budget, self.image_budget, self.text_budget = video_budget, image_budget, text_budget
        self.film_target_seconds = film_target_seconds  # 档位透传给自动续跑派发

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
            profile = FILM_PROFILES.get(self.film_target_seconds)
            self.plan = validate_planned_project(ProjectStore(self.project_dir).load(), profile)
            write_json(self.evidence_dir / "planned-duration.json", self.plan)
        return self.plan

    async def persist(self, **extra):
        async with self.lock:
            fields = dict(video_attempts=self.video_attempts, image_calls=self.image_calls,
                          text_calls=self.text_calls, updated_at=utc_now(), **extra)
            await asyncio.to_thread(self.checkpoint.save, **fields)

    async def chat(self, *args, **kwargs):
        if self.finalize_only:
            raise RuntimeError('finalize-only forbids new Agnes generation calls')
        self.text_calls += 1
        if self.text_calls > self.text_budget:
            raise RuntimeError("acceptance text-call budget exhausted")
        return await self.client.chat(*args, **kwargs)

    async def generate_image(self, *args, **kwargs):
        if self.finalize_only:
            raise RuntimeError('finalize-only forbids new Agnes generation calls')
        if self.submission_blocked:
            raise RuntimeError("new media submissions are paused; reconcile existing work")
        self.media_gate()
        self.image_calls += 1
        if self.image_calls > self.image_budget:
            raise RuntimeError("acceptance image budget exhausted")
        return await self.client.generate_image(*args, **kwargs)

    async def create_video(self, prompt, **kwargs):
        if self.finalize_only:
            raise RuntimeError('finalize-only forbids new Agnes generation calls')
        self.media_gate()
        seconds = int(kwargs.get("duration_s") or 5)
        if not 4 <= seconds <= 12:
            raise ValueError("invalid provider duration")
        if self.submission_blocked or any(a["state"] in ("SUBMITTING", "UNKNOWN") for a in self.video_attempts):
            raise RuntimeError("unconfirmed prior submission; reconciliation required, no automatic POST")
        if sum(a["seconds"] for a in self.video_attempts) + seconds > self.video_budget:
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
        except Exception as error:
            # 每日额度耗尽（本地账本预检或供应商 4xx 明确拒绝）：无任务号、
            # 无扣费——意图条目安全回滚并记日历闸门；其余异常一律维持
            # UNKNOWN 对账纪律，绝不凭猜测丢弃收据。
            if quota_gate.classify_quota_rejection(error):
                self.video_attempts = [a for a in self.video_attempts if a is not attempt]
                self.submission_blocked = True
                self.quota_gate_after = quota_gate.next_quota_reset_utc()
                try:
                    await self.persist(quota_resume_after=self.quota_gate_after)
                except Exception:
                    pass  # 闸门落账失败则保留 SUBMITTING → UNKNOWN 对账，保守处置
                raise
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


def snapshot_settled(project_dir, attempts, queue):
    """A parent PAUSED flag alone does not establish a recoverable snapshot."""
    project_dir = Path(project_dir)
    paths = [*project_dir.glob('output/full_runs/*/run.json'),
             *project_dir.glob('output/production_runs/*/run.json'),
             *project_dir.glob('output/ai/autopilot_runs/*.json')]
    if not paths:
        return False
    try:
        if any(json.loads(path.read_text(encoding='utf-8')).get('status') not in
               ('paused', 'completed', 'failed', 'blocked', 'cancelled') for path in paths):
            return False
        if any(job.project == project_dir.name and job.state not in ('done', 'failed')
               for job in queue.jobs.values()):
            return False
        if any(a.get('state') != 'ACCEPTED' or not a.get('video_id') for a in attempts):
            return False
        from src.core.store import ProjectStore
        project = ProjectStore(project_dir).load()
        versions = [v for shot in project.all_shots() for v in shot.video_versions]
        for attempt in attempts:
            matched = [v for v in versions if v.video_id == attempt['video_id'] and v.status == 'done']
            if len(matched) != 1 or not matched[0].local:
                return False
            path = Path(matched[0].local)
            if not path.resolve().is_relative_to(project_dir.resolve()) or not path.is_file() or not path.stat().st_size:
                return False
        return True
    except (OSError, ValueError, KeyError):
        return False


async def pause_and_drain(manager, project_name, run_id, wrapper, queue, *, grace_seconds=900):
    """Stop new media, let accepted work land, then record actual recoverability."""
    wrapper.submission_blocked = True
    await manager.request_pause(project_name, run_id)
    await wrapper.persist(phase='PAUSING', deadline_reached=True, drain_complete=False)
    deadline = time.monotonic() + grace_seconds
    last_report = time.monotonic()
    while True:
        full = manager.get_run(project_name, run_id)
        if snapshot_settled(wrapper.project_dir, wrapper.video_attempts, queue):
            full = manager.get_run(project_name, run_id)
            await wrapper.persist(phase='RUNNING' if full.status == 'completed' else 'FAILED',
                terminal_status=None if full.status == 'completed' else full.status,
                error=None if full.status == 'completed' else 'configured deadline; accepted work saved',
                drain_complete=True)
            return True
        if time.monotonic() >= deadline:
            await wrapper.persist(phase='INTERRUPTED', terminal_status=full.status,
                error='deadline drain expired; reconcile accepted work before recovery', drain_complete=False)
            return False
        # Keep the remote heartbeat current while a submitted task is draining.
        if time.monotonic() - last_report >= 30:
            await wrapper.persist(phase='PAUSING', progress={'status': full.status, 'stage': 'deadline_drain'})
            last_report = time.monotonic()
        await asyncio.sleep(3)


def ensure_archive_input(project_dir, nonce):
    """Preserve real input; otherwise materialize the actual canonical story source."""
    project_dir = Path(project_dir)
    if any(p.is_file() for p in (project_dir/'input').rglob('*')):
        return
    canonical = json.loads((project_dir/'output/project.json').read_text(encoding='utf-8'))
    scenes = [{k: scene.get(k) for k in ('scene_id', 'title', 'summary')}
              for scene in canonical.get('scenes', [])]
    if not (canonical.get('story_text') or '').strip() or not scenes or any(not s.get('summary') for s in scenes):
        raise ValueError('no real input or canonical story source available for full archival')
    write_json(project_dir/'input/cloud-acceptance-source.json', {
        'schema_version': 1, 'provenance': 'materialized_from_canonical_project_source',
        'nonce': nonce, 'project_name': canonical['name'],
        'story_text': canonical['story_text'], 'scenes': scenes, 'style': canonical.get('style')})


def archive_completed_project(token, nonce, project_dir, final_path, evidence_dir, source_sha):
    from tools.e2e_autonomous_run import B1GitHubCas, sha256_file
    from tools.relstore import archive_campaign, restore_campaign
    from tools.relstore.github_release_adapter import GitHubReleaseAdapter, HttpxTransport
    from tools.relstore.project_registry import Registry
    from tools.video_task_state import task_index

    adapter = GitHubReleaseAdapter.armed("Q-1844", "agent-system-v2-rundata", token, HttpxTransport())
    tag = f"full-project/{nonce}"
    existing_id = os.environ.get('ARCHIVE_RELEASE_ID', '').strip()
    if existing_id:
        if not existing_id.isdigit():
            raise ValueError('archive release ID must be numeric')
        existing = adapter.get_release(existing_id)
        if not existing or existing.get('tag_name') != tag:
            raise ValueError('existing release belongs to a different project')
        release_id = existing_id
    else:
        release_id = adapter.create_release(tag, name=f"Full project >=180s {nonce}", draft=True,
                                            target_commitish="main").id
    media_sha = sha256_file(final_path)
    media = adapter.upload_asset_with_recovery(release_id, "final.mp4", final_path.read_bytes(), media_sha)
    if hashlib.sha256(adapter.download_asset_authenticated(media.id)).hexdigest() != media_sha:
        raise RuntimeError("final MP4 re-download SHA mismatch")
    opdir = evidence_dir / "archive"
    ensure_archive_input(project_dir, nonce)
    archive_campaign.plan(project_dir=project_dir, opdir=opdir, operation_id=f"archive-{nonce}",
                          project_id=nonce, revision=1, lifecycle_status="completed",
                          source_code_commit=source_sha)
    packaged = archive_campaign.package(opdir)
    package_path = Path(packaged["package"]["path"])
    package_sha = packaged["package"]["sha256"]
    package_name = f"project-{os.environ.get('GITHUB_RUN_ID') or package_sha[:12]}.tar" if existing_id else "project.tar"
    package = adapter.upload_asset_with_recovery(release_id, package_name, package_path.read_bytes(), package_sha)
    redownload = adapter.download_asset_authenticated(package.id)
    downloaded = evidence_dir / "redownload-project.tar"
    downloaded.write_bytes(redownload)
    restored = restore_campaign.restore_package(downloaded, evidence_dir / "restore-check",
                                                expected_sha256=package_sha)
    write_json(evidence_dir / "restore-report.json", restored)
    if restored["restorable_level"] != "full" or restored["missing_required"]:
        raise RuntimeError(f"project snapshot did not pass full restore verification: {restored.get('missing_required')}")
    registry = Registry(cas=B1GitHubCas(task_index(token, nonce, "project-registry.json")))
    registry.create_project(project_id=nonce, alias=project_dir.name, operation_id=f"{nonce}-create")
    registry.record_activity(project_id=nonce, kind="generate_video", operation_id=f"{nonce}-activity")
    registry.plan_archive(project_id=nonce, operation_id=f"{nonce}-plan")
    tree_fingerprint = packaged["package"]["payload"]["snapshot_fingerprint"]
    registry.begin_snapshot(project_id=nonce, snapshot_fingerprint=tree_fingerprint, operation_id=f"{nonce}-snapshot")
    ref = {"repo": "Q-1844/agent-system-v2-rundata", "release_tag": tag,
           "release_id": release_id, "asset_id": package.id, "asset_name": package_name,
           "sha256": package_sha, "tree_fingerprint": tree_fingerprint,
           "bytes": len(redownload), "final_mp4_asset_id": media.id, "final_mp4_sha256": media_sha}
    registry.set_verified_restorable(project_id=nonce, archive_ref=ref, restorable_level="full",
                                     operation_id=f"{nonce}-verified")
    return {"archive_ref": ref, "registry": registry.to_doc(),
            "restore_files_checked": restored["files_checked"], "restorable_level": "full"}


async def auto_resume_next_hop(wrapper, nonce, driver_minutes):
    """A 机制：排水干净结束后立即派发下一跳（workflow_dispatch 显式恢复入口）。

    无 RESUME_DISPATCH_TOKEN、相位不可恢复或跳数超限时安静降级为人工，
    绝不影响既有留证与退出路径。返回值写入 summary 与检查点 auto_resume 字段。
    """
    from tools.resume_dispatch import auto_resume_allowed, dispatch_next_hop, hop_count
    token = os.environ.get("RESUME_DISPATCH_TOKEN", "")
    if not token.strip():
        return {"dispatched": False, "reason": "RESUME_DISPATCH_TOKEN not configured"}
    allowed, reason = auto_resume_allowed(wrapper.checkpoint.doc)
    hops = hop_count(wrapper.checkpoint.doc)
    if not allowed:
        if "cap" in reason:
            await wrapper.persist(phase="AWAITING_HUMAN", error=reason)
        return {"dispatched": False, "reason": reason, "hops": hops}
    result = dispatch_next_hop(token, ref=os.environ.get("GITHUB_REF_NAME", ""),
                               resume_run_id=os.environ.get("GITHUB_RUN_ID", ""),
                               task_nonce=nonce, driver_minutes=driver_minutes,
                               target_seconds=wrapper.film_target_seconds)
    result["hops"] = hops
    await wrapper.persist(auto_resume=result)
    return result


AUTO_TARGET = "auto"
#: auto 模式的执行摘要（P0-1）：自然语言任务文本，时长可有可无。
PRODUCTION_REQUEST_MAX = 12000


# ---------------------------------------------------------------------------
# 自主规划模式（PR B1）：Stage 0 全链——任务身份 → 规划 → 冻结 → 物化 → 绑定。
# 200/320 固定档链路零改动；本段为 auto 专属。
# ---------------------------------------------------------------------------

def normalize_dispatch_input_to_task_spec(nonce, environ=None):
    """workflow 输入 → TaskSpec（P0-1：任何 LLM 调用之前完成身份构造）。

    production_request 缺席或超长 → fail fast（不进规划）。
    时长意图经确定性正则提取（extract_duration_intent），LLM 不参与身份。
    意图分流（suggest_intent）：B1 只放行 create；继续类意图 → NotYetWired
    （P1-4，零媒体零文本，PR C1/C2 接线）。
    """
    from src.ai.producer_planning_models import TaskSpec, suggest_intent
    from src.ai.producer_planning_service import NotYetWired, extract_duration_intent

    env = environ or os.environ
    request = (env.get("PRODUCTION_REQUEST") or "").strip()
    if not request:
        raise ValueError("auto 模式要求 workflow input production_request（任务自然语言）")
    if len(request) > PRODUCTION_REQUEST_MAX:
        raise ValueError(f"production_request 超长（>{PRODUCTION_REQUEST_MAX} 字符）")
    source_project = (env.get("SOURCE_PROJECT") or "").strip() or None
    intent, reason = suggest_intent(request, source_project=source_project)
    if intent != "create":
        raise NotYetWired(
            f"任务被识别为 {intent}（{reason}）；该意图的无值守接线在 PR C1/C2 交付，"
            "本阶段拒绝执行且不产生任何付费调用")
    return TaskSpec(intent="create", request_text=request,
                    duration=extract_duration_intent(request),
                    source_project=None)


def auto_request_profile(blueprint):
    """冻结蓝图 → request_digest 用的动态 profile（auto 执行摘要）。

    与固定档同构（scope/scenes/budget 三元组），scenes 取蓝图段摘要原文。
    """
    scenes = [(s.get("title") or "", s.get("summary") or "")
              for s in (blueprint.get("scenes") or [])]
    return {"scope": "full-project-auto-v1", "scenes": scenes,
            "budget": int((blueprint.get("execution_bounds") or {})
                          .get("video_seconds") or 0)}


def _blueprint_to_result(blueprint, task_spec):
    """冻结蓝图的 dict 形态 → ProducerPlanningResult（复用既有契约校验）。"""
    from src.ai.producer_planning_models import (PlannedScene,
                                                 ProducerPlanningResult, SceneBudget)
    scenes = [PlannedScene(scene_id=s["scene_id"], title=s.get("title") or "",
                          summary=s["summary"])
              for s in blueprint["scenes"]]
    budgets = [SceneBudget(scene_id=b["scene_id"], min_s=b["min_s"],
                           max_s=b["max_s"], shot_min=b["shot_min"],
                           shot_max=b["shot_max"], target_s=b.get("target_s"))
               for b in blueprint["scene_budgets"]]
    return ProducerPlanningResult(
        task_spec_sha256=blueprint.get("task_spec_sha256") or task_spec.sha256(),
        intent="create", story_text=blueprint["story_text"],
        scenes=tuple(scenes), scene_budgets=tuple(budgets),
        execution_bounds=dict(blueprint.get("execution_bounds") or {}),
        style=dict(blueprint.get("style") or {}))


async def _ensure_app_started():
    """auto B1 阶段只依赖 agnes_client（规划 chat）；复用固定档的启动方式。"""
    import api_server
    await api_server.startup()


async def _run_auto_acceptance(nonce, out, summary, token):
    """auto 模式主链（B1 边界：到执行身份绑定 + 显式停止为止，生产接线属 PR B2）。

    事务序列（P0-2）：
      bootstrap stage0（任务身份）→ plan（文本预留）→ freeze（原子）→
      materialize（本地幂等）→ bind ProductionCheckpoint（执行 digest）。
    恢复语义：plan_fingerprint 存在即必须恢复原蓝图（不得重规划）。
    """
    from tools.stage0_state import (STAGE0_FILENAME, Stage0Conflict, Stage0Record)
    from tools.video_task_state import ProductionCheckpoint, task_index, validate_nonce
    from src.ai.producer_planning_service import ProducerPlanningService

    validate_nonce(nonce)
    task_spec = normalize_dispatch_input_to_task_spec(nonce)
    stage0_idx = task_index(token, nonce, STAGE0_FILENAME)
    from tools.e2e_autonomous_run import ensure_branch
    ensure_branch(stage0_idx)
    stage0 = Stage0Record(stage0_idx, nonce)
    limits = {"text_calls": 60, "image_calls": 40, "video_seconds": 240}

    try:
        stage0.bootstrap(task_spec)  # 同 nonce 换任务 → Stage0Conflict（T25）
    except Stage0Conflict as exc:
        summary.update(blocked_stage="stage0_identity", no_new_media_submitted=True)
        raise RuntimeError(str(exc)) from exc
    if stage0.plan_fingerprint is not None:
        stage0.verify()  # 恢复路径：蓝图指纹必须与内容一致（T28）
    summary.update(stage0_status=stage0.doc.get("status"),
                   task_spec_sha256=stage0.doc.get("task_spec_sha256"))
    write_json(out / "stage0-status.json",
               {"status": stage0.doc.get("status"),
                "task_spec_sha256": stage0.doc.get("task_spec_sha256"),
                "plan_fingerprint": stage0.plan_fingerprint,
                "text_calls_used": stage0.text_calls_used})

    # ---- 规划（未冻结才规划；已冻结直接恢复原蓝图）----
    if stage0.plan_fingerprint is None:
        stage0.mark_planning()
        import deps
        await _ensure_app_started()
        if deps.agnes_client is None:
            raise RuntimeError("agnes client unavailable for stage0 planning")
        service = ProducerPlanningService(deps.agnes_client, stage0)
        result = await service.plan(task_spec, limits)
        blueprint = json.loads(json.dumps(result.canonical_payload()))
        plan_fp = stage0.freeze(blueprint)  # 单次原子（方案 A）
        summary.update(stage0_status="FROZEN", plan_fingerprint=plan_fp)
    else:
        blueprint = stage0.blueprint
        summary.update(stage0_status="FROZEN_RESTORED",
                       plan_fingerprint=stage0.plan_fingerprint)

    # ---- 物化（幂等：项目已在则复用，不重建）----
    project_name = f"cloud3min_{nonce}"
    from src.core.models import Project, Scene, StyleAnchor
    from src.core.store import ProjectStore
    import deps as _deps
    project_dir = _deps.project_manager.get_project_dir(project_name)
    if not (project_dir / "output" / "project.json").is_file():
        _deps.project_manager.create_project(project_name)
        scenes = [Scene(scene_id=s["scene_id"], title=s.get("title") or "",
                        summary=s["summary"])
                  for s in blueprint["scenes"]]
        style = blueprint.get("style") or {}
        project = Project(
            name=project_name, story_text=blueprint["story_text"],
            style=StyleAnchor(art_style=str(style.get("art_style") or ""),
                              palette=str(style.get("palette") or ""),
                              aspect_ratio=str(style.get("aspect_ratio") or "16:9")),
            characters=[], scenes=scenes)
        ProjectStore(project_dir).save(project)
    summary.update(project_name=project_name, project_dir=str(project_dir))

    # ---- 绑定执行身份（ProductionCheckpoint：只在冻结后创建）----
    profile = auto_request_profile(blueprint)
    digest = request_digest(AUTO_TARGET, profile)
    idx = task_index(token, nonce, "full-project-production.json")
    cp = ProductionCheckpoint(idx, nonce, digest)
    if cp.current is None:
        cp.save(phase="STAGE0_BOUND", github_run_id=os.environ.get("GITHUB_RUN_ID"),
                source_sha=os.environ.get("GITHUB_SHA"),
                film_target_seconds=AUTO_TARGET,
                task_spec_sha256=stage0.doc.get("task_spec_sha256"),
                plan_fingerprint=stage0.plan_fingerprint,
                stage0_text_calls_used=stage0.text_calls_used)
    else:
        cp.save(phase="STAGE0_BOUND",
                github_run_id=os.environ.get("GITHUB_RUN_ID"))
    summary.update(execution_request_sha256=digest,
                   stage0_text_calls_used=stage0.text_calls_used)
    # ---- B1 终点：执行摘要落账 + 显式停止（媒体生产接线属 PR B2）----
    plan = {"planned_target_seconds": sum(
                b.get("target_s") or b.get("min_s") or 0
                for b in blueprint["scene_budgets"]),
            "scene_count": len(blueprint["scenes"]),
            "video_seconds_cap": (blueprint.get("execution_bounds") or {})
            .get("video_seconds")}
    summary["auto_plan"] = plan
    summary["status"] = "STAGE0_COMPLETE"
    write_json(out / "stage0-blueprint.json", blueprint)
    raise RuntimeError(
        "PR B1 边界：auto 任务已冻结并绑定执行身份（零媒体），"
        "媒体生产接线在 PR B2 交付后开放；此 run 应到此终止")


async def run_acceptance(nonce, out, summary):
    from tools.e2e_autonomous_run import ensure_branch
    from tools.video_task_state import ProductionCheckpoint, task_index
    from src.core.quota import QuotaLedger
    from src.core.store import ProjectStore
    from src.core.models import Project, Scene, StyleAnchor
    from src.core.image_host import GithubPagesHost

    driver_minutes = runtime_deadline_minutes(os.environ)
    finalize_only = os.environ.get('FINALIZE_ONLY', 'false').lower() == 'true'
    if finalize_only and not os.environ.get('RESUME_RUN_ID', '').strip():
        raise ValueError('finalize-only requires explicit resume_run_id')
    if os.environ.get('ARCHIVE_RELEASE_ID', '').strip() and not finalize_only:
        raise ValueError('existing archive release reuse requires finalize-only')
    target, profile = film_profile(os.environ)  # 档位错误 → digest 不匹配 → resume 拒绝，安全
    summary.update(driver_timeout_minutes=driver_minutes, finalize_only=finalize_only,
                   film_target_seconds=target)
    token, frame_token = acceptance_credentials(os.environ)
    if target == AUTO_TARGET:
        # 自主规划模式（PR B1）：Stage 0 独立身份域，不进固定档链路。
        return await _run_auto_acceptance(nonce, out, summary, token)
    from tools.stage0_state import Stage0Record
    fixed_idx = task_index(token, nonce, STAGE0_FILENAME)
    if Stage0Record(fixed_idx, nonce).exists():
        # P1-1 反向护栏：auto 任务的 nonce 不得被固定档复用（模式不匹配即拒）。
        raise RuntimeError(
            f"nonce 已绑定 auto 模式任务，拒绝以固定 {target}s 档续跑；"
            "请以 target_duration_seconds=auto 恢复")
    idx = task_index(token, nonce, "full-project-production.json")
    ensure_branch(idx)
    digest = request_digest(target, profile)
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
        restored = restore_snapshot(staging, Path.cwd(), nonce, previous, provenance, finalize_only=finalize_only)
        import shutil
        shutil.rmtree(staging)  # the live copy is retained; avoid nesting old snapshots in new artifacts
        summary.update(project_name=restored["project_name"], full_run_id=restored["full_run_id"],
                       project_dir=str(Path.cwd()/"projects"/restored["project_name"]),
                       source_sha=os.environ.get("GITHUB_SHA"), resume=restored)
        write_json(out / "restore-input.json", restored)
        provenance["previous_terminal_status"] = previous.get("terminal_status")
        provenance["previous_error"] = previous.get("error")
        cp.save(phase="RESTORED", github_run_id=os.environ.get("GITHUB_RUN_ID"),
                source_sha=os.environ.get("GITHUB_SHA"), resume_from_run_id=resume_id,
                updated_at=utc_now(),
                terminal_status=None, error=None,
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
    QuotaLedger.get_default(Path("cache/quota.json"), limits={"video_seconds": profile["budget"],
                                                           "images": profile["image_budget"], "text_calls": profile["text_budget"]})
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
                  for i, (title, story) in enumerate(profile["scenes"], start=1)]
        project = Project(name=project_name, story_text=profile["story_text"],
                          style=StyleAnchor(art_style="photorealistic cinematic underwater rescue thriller, coherent practical machinery",
                                            palette="deep ocean blue, safety orange, warm work lights", aspect_ratio="16:9"),
                          characters=[], scenes=scenes, next_scene_seq=len(scenes) + 1)
        ProjectStore(project_dir).save(project)
    wrapper = BoundedRealClient(deps.agnes_client, cp, project_dir, out, finalize_only=finalize_only,
                                video_budget=profile["budget"], image_budget=profile["image_budget"],
                                text_budget=profile["text_budget"], film_target_seconds=target)
    if restored:
        wrapper.restore_usage(previous)
        wrapper.media_gate()
    deps.agnes_client = wrapper
    manager = get_full_project_manager()
    existing_full = manager.get_run(project_name, restored['full_run_id']) if restored else None
    if finalize_only and existing_full and existing_full.status == 'completed':
        full = existing_full
    elif restored:
        full = await manager.resume_run(project_name, restored['full_run_id'])
    else:
        full = await manager.start_run(project_name)
    summary.update(project_name=project_name, full_run_id=full.run_id,
                   project_dir=str(project_dir), source_sha=os.environ.get("GITHUB_SHA"))
    await wrapper.persist(phase="RUNNING", project_name=project_name, full_run_id=full.run_id,
                           film_target_seconds=target)
    deadline, last_report = time.monotonic() + driver_minutes * 60, 0.0
    while True:
        full = manager.get_run(project_name, full.run_id)
        if full.status in ("completed", "failed", "blocked", "cancelled", "paused"):
            break
        if time.monotonic() >= deadline:
            summary.update(deadline_reached=True, provider_video_attempts=wrapper.video_attempts)
            drained = await pause_and_drain(manager, project_name, full.run_id, wrapper, deps.job_queue)
            full = manager.get_run(project_name, full.run_id)
            summary.update(drain_complete=drained, full_run=full.model_dump())
            if drained and full.status == 'completed':
                break   # no new production; finish final-media validation and archival
            summary["auto_resume"] = await auto_resume_next_hop(wrapper, nonce, driver_minutes)
            raise TimeoutError("configured deadline; bounded drain finished; inspect checkpoint before recovery")
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
        if wrapper.quota_gate_after:
            # 按天接续：额度耗尽是可预期的干净暂停，不是生产失败。意图条目
            # 已在提交路径回滚（无收据无扣费），落账走日历闸门等巡检拉起。
            await wrapper.persist(phase="FAILED",
                                   terminal_status=quota_gate.QUOTA_TERMINAL,
                                   error="daily video quota exhausted; day-gated resume",
                                   quota_resume_after=wrapper.quota_gate_after,
                                   drain_complete=True)
            summary["quota_pause"] = {"resume_after": wrapper.quota_gate_after,
                                      "accepted_attempts": len(wrapper.video_attempts)}
            summary["auto_resume"] = await auto_resume_next_hop(
                wrapper, nonce, driver_minutes)  # 闸门未开时仅记录派发理由
            raise RuntimeError(f"FullProject quota-paused until {wrapper.quota_gate_after}: {full.error}")
        await wrapper.persist(phase="FAILED", terminal_status=full.status, error=full.error)
        raise RuntimeError(f"FullProject did not complete: {full.status}: {full.error}")
    from src.ai.project_autopilot import get_project_autopilot_manager
    child = get_project_autopilot_manager().get_run(project_name, full.production_run_id)
    summary["project_production_run"] = child.model_dump()
    if len(child.scenes) != profile["scene_count"] or any(s.status != "completed" for s in child.scenes):
        raise RuntimeError(f"not all {profile['scene_count']} native SceneRuns completed")
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
    summary["media"] = await asyncio.to_thread(validate_final_media, final, profile["min_final"])
    summary["plan"] = wrapper.media_gate()
    accepted = [a for a in wrapper.video_attempts if a["state"] == "ACCEPTED"]
    if len({a["video_id"] for a in accepted}) != len(accepted):
        raise RuntimeError("provider returned a duplicate video ID across submissions")
    try:
        summary["saved"] = await asyncio.to_thread(archive_completed_project, token, nonce, project_dir,
                                                   final, out, os.environ.get("GITHUB_SHA", "unknown"))
    except Exception:
        await wrapper.persist(phase='FAILED', terminal_status='archive_failed',
                              error='project archival failed; inspect restore-report.json and summary')
        raise
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
