# src/agents/review_pipeline.py
# Phase55 D（任务书 §8）：独立审核流水化——把执行侧代行的帧验流程工具化。
#
# 定位：不新增业务角色（任务书 §8「不新增业务角色来凑独立性」）——复用既有
# shot_review 单帧 14 维判定器（SHOT_REVIEW_HARD_DIMS 一票否决已代码化）+
# continuity_service 机械抽帧，按固定抽帧位（first/middle/end）批量审最终视频。
# 独立性诚实标注：审核由 fresh 无状态 vision 调用完成（不与制作共享上下文），
# 但与制作共用同一 vision 模型——回执注明，不宣称完全独立盲审。
# 结果落 output/agents/review_pipeline/{batch_id}.json（抽帧位/判断/证据保存），
# 由 collab run 的 independent_final_review 步引用；修复接线（hard error →
# 修复指令 + repairRequireNew 语义）由调用方（run 执行器/用户）决策。
from __future__ import annotations
import secrets
import time
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

# 抽帧位：首帧（开场）/中点/末帧（收尾）——固定清单，随批次落档（§8 固定裁切区间）。
FRAME_POSITIONS = ("first", "middle", "end")

# ============ Phase56 审查政策 v2（video_review_v2；B1 实证驱动） ============
# B1 发现（run_fba9de2066/rvw_d4421b7e）：P55 硬维清单为静帧设计，直接套视频
# 三帧位=结构性误报——中帧动作位移→missing_character；景别（预存模型边界）
# →shot_size_correctness 硬错→修复轮对 never-win 问题烧额度（违反 04「不增加
# 无休止修复」）。v2 分类：
#   - 视频硬维（故事事实，任一状态帧[首/末] fail → 硬错）：人数/身份/缺人/伪影；
#   - 视频次维（记录不自动修复）：景别/构图（模型能力边界+运动自然变量）；
#   - 聚合：硬维仅在首/末帧 fail 才算硬错；仅中帧 fail=动作位移，记录为
#     motion_minor（开场/终态仍是状态闸，运动不被惩罚）。
REVIEW_POLICY_VERSION = "video_review_v2"
VIDEO_HARD_DIMS = ("duplicate_identity", "unexpected_people", "missing_character",
                   "anatomy_artifact")
VIDEO_MINOR_DIMS = ("shot_size_correctness", "framing_and_composition")
STATE_FRAME_POSITIONS = ("first", "end")   # 状态闸帧位（中帧=运动帧）

# 模块级 import（测试可替换；单帧判定器/硬维清单/机械抽帧复用既有实现）
from .shot_review import review_shot_core, SHOT_REVIEW_HARD_DIMS  # noqa: E402
from src.storyboard.continuity_service import (  # noqa: E402
    extract_shot_frame_local, ContinuityError)


def _classify_shot_frames(frames: List[Dict[str, Any]]) -> dict:
    """审查政策 v2 聚合：视频硬维只在状态帧（首/末）fail 才构成硬错；
    次维（景别/构图）与仅中帧硬维=记录（motion_minor），不触发自动修复。"""
    hard_state: set = set()
    hard_motion_only: set = set()
    minor_all: set = set()
    for f in frames:
        fired = set(f.get("hard_dims_fired") or [])
        minor_here = fired & set(VIDEO_MINOR_DIMS)
        minor_all |= minor_here
        hard_here = fired & set(VIDEO_HARD_DIMS)
        if f.get("position") in STATE_FRAME_POSITIONS:
            hard_state |= hard_here
        else:
            hard_motion_only |= hard_here
    return {
        "hard_errors": sorted(hard_state),
        "motion_minor": sorted(hard_motion_only - hard_state),
        "minor": sorted(minor_all),
    }


def _pick_client(rt):
    """视觉审查 client（复用既有 agnes client；vision 模型由 shot_review 内部定）。"""
    return rt._resolve_client_or_none(agent_id="producer") \
        or rt._resolve_client_or_none(agent_id="director")


def _dim_status(v) -> str:
    """维度判定值→status 字符串（维度为 {status:...} 对象，兼容纯字符串）。"""
    if isinstance(v, dict):
        return str(v.get("status") or "").lower()
    return str(v or "").lower()


def _hard_dims_fired(dims: dict) -> List[str]:
    return [d for d in SHOT_REVIEW_HARD_DIMS if _dim_status(dims.get(d)) == "fail"]


async def run_independent_review(rt, pdir: Path, project: str, *,
                                 reason: str = "",
                                 shot_ids: Optional[List[str]] = None,
                                 positions: tuple = FRAME_POSITIONS,
                                 version_ids: Optional[Dict[str, str]] = None,
                                 max_concurrency: int = 2,
                                 cached_results: Optional[Dict[str, dict]] = None) -> dict:
    """对项目已完成视频（或指定镜头）跑一轮独立帧验。
    返回 {batch_id, shots_reviewed, hard_error_shots, minor_shots, report_path,
    results:[...]}。单镜失败隔离为 incomplete；不取消其它镜头，不当作通过。
    审核绑定调用时的具体版本，异步视觉调用期间选版改变将使结果 stale。"""
    from src.core.store import ProjectStore

    client = _pick_client(rt)
    if client is None:
        raise RuntimeError("视觉审查 client 不可用（Agnes 未配置）")
    proj = ProjectStore(pdir).load()
    if shot_ids is not None:
        shots = [proj.get_shot(s) for s in shot_ids]
    else:
        shots = list(proj.all_shots())
    batch_id = f"rvw_{secrets.token_hex(4)}"
    semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))

    async def _one(index, shot):
        sid = shot.shot_id if shot is not None else shot_ids[index]
        entry: Dict[str, Any] = {"shot_id": sid, "frames": [],
                                 "overall": "incomplete", "hard_errors": [],
                                 "error": None}
        try:
            if shot is None:
                raise ValueError("requested shot missing")
            selected = (version_ids or {}).get(sid) or shot.final_video
            version = next((v for v in shot.video_versions if v.version_id == selected), None)
            if not selected or version is None or version.status != "done" or not version.local:
                raise ValueError("review requires a completed, explicit video version")
            if not positions or not set(STATE_FRAME_POSITIONS).issubset(positions):
                raise ValueError("review must include first/end state frames")
            entry["version_id"] = selected
            from src.generation.video_commit import video_input_signature
            input_signature = video_input_signature(proj, shot)
            entry["input_signature"] = input_signature
            cached = (cached_results or {}).get(sid) or {}
            if (cached.get("version_id") == selected and
                    cached.get("input_signature") == input_signature and
                    cached.get("review_policy_version") == REVIEW_POLICY_VERSION and
                    cached.get("overall") in ("pass", "hard_error", "uncertain")):
                return {**cached, "cache_reused": True}
            # Work on the loaded snapshot. Never switch files mid-review.
            shot.final_video = selected
            seconds = version.seconds_returned or getattr(shot.video, "seconds_returned", None) or 5.0
            pos_map = {"first": "first", "middle": max(0.0, seconds * 0.5),
                       "end": "end"}
            for pos in positions:
                p = pos_map.get(pos, pos)
                async with semaphore:
                    fr = await asyncio.to_thread(
                        extract_shot_frame_local, pdir, proj, shot, position=p,
                        tag=f"rvw_{batch_id}")
                    if (fr.get("source") or {}).get("version_id") != selected:
                        raise ValueError("extracted frame version mismatch")
                    verdict = await review_shot_core(
                        client, project_dir=pdir, proj=proj, shot=shot,
                        frame="first", image_local=fr["local"], persist=False)
                _dims = (verdict.get("review") or {}).get("dimensions") or {}
                if any(_dim_status(_dims.get(d)) not in ("pass", "fail", "uncertain")
                       for d in VIDEO_HARD_DIMS):
                    raise ValueError("review response missing valid hard dimensions")
                _fired = _hard_dims_fired(_dims)
                entry["frames"].append({
                    "position": pos,
                    "local": fr["local"],
                    "source_version": (fr.get("source") or {}).get("version_id"),
                    "overall": (verdict.get("derived") or {}).get("overall"),
                    "hard_dims_fired": _fired,
                    "hard_dims_uncertain": [d for d in VIDEO_HARD_DIMS if _dim_status(_dims.get(d)) == "uncertain"],
                    "summary": ((verdict.get("review") or {}).get("summary")
                                or "")[:300],
                })
            overs = [f["overall"] for f in entry["frames"]]
            cls = _classify_shot_frames(entry["frames"])
            entry["hard_errors"] = cls["hard_errors"]
            entry["motion_minor"] = cls["motion_minor"]
            entry["minor"] = cls["minor"]
            entry["overall"] = ("hard_error" if cls["hard_errors"]
                                else "uncertain" if any(
                                    f.get("hard_dims_uncertain") and f.get("position") in STATE_FRAME_POSITIONS
                                    for f in entry["frames"])
                                else "pass")
        except ContinuityError as e:
            entry["overall"] = "incomplete"
            entry["error"] = f"frame_extract: {str(e)[:160]}"
        except Exception as e:   # noqa: BLE001 —— 单镜失败不阻断整批
            entry["overall"] = "incomplete"
            entry["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        return entry

    results = await asyncio.gather(*[_one(i, s) for i, s in enumerate(shots)])
    # Validate the entire batch at its final read boundary, including cache hits
    # and shots that finished before another shot's asynchronous review.
    try:
        latest_project = ProjectStore(pdir).load()
        from src.generation.video_commit import video_input_signature
        for entry in results:
            if entry["overall"] not in ("pass", "hard_error", "uncertain"):
                continue
            latest = latest_project.get_shot(entry["shot_id"])
            if (latest is None or latest.final_video != entry.get("version_id") or
                    video_input_signature(latest_project, latest) != entry.get("input_signature")):
                entry["overall"] = "stale"
                entry["error"] = "selected version or inputs changed during review"
    except Exception as e:
        for entry in results:
            entry["overall"] = "incomplete"
            entry["error"] = f"final_validation: {type(e).__name__}: {str(e)[:160]}"
    hard_shots = [r["shot_id"] for r in results if r["overall"] == "hard_error"]
    minor_shots = [r["shot_id"] for r in results if r["overall"] == "uncertain"]
    incomplete_shots = [r["shot_id"] for r in results
                        if r["overall"] in ("incomplete", "stale")]
    report = {
        "schema": "independent_review_v2",
        "review_policy_version": REVIEW_POLICY_VERSION,
        "video_hard_dims": list(VIDEO_HARD_DIMS),
        "video_minor_dims": list(VIDEO_MINOR_DIMS),
        "state_frame_positions": list(STATE_FRAME_POSITIONS),
        "batch_id": batch_id, "project": project, "created_at": time.time(),
        "reason": (reason or "")[:200],
        "frame_positions": list(positions),
        "independence_note": "fresh 无状态 vision 调用（不与制作共享会话上下文）；"
                             "与制作共用同一 vision 模型——非完全独立盲审。",
        "shots_reviewed": len(results),
        "hard_error_shots": hard_shots,
        "minor_shots": minor_shots,
        "incomplete_shots": incomplete_shots,
        "machine_passed": bool(results) and not (hard_shots or minor_shots or incomplete_shots),
        "results": results,
    }
    out_dir = Path(pdir) / "output" / "agents" / "review_pipeline"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{batch_id}.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(__import__("json").dumps(report, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(out)
    report["report_path"] = str(out)
    return report


class LiveReviewSession:
    """Bounded review alongside an active Producer; receipts stay version-scoped.

    No canonical writes, generation or repair is triggered by this observer.
    Final review reuses only matching completed receipts, never pending checks.
    """
    def __init__(self, rt, pdir, project, *, concurrency=2, interval_s=1.0):
        self.rt, self.pdir, self.project = rt, Path(pdir), project
        self.interval_s = interval_s
        self.stop_event = asyncio.Event()
        self.slots = asyncio.Semaphore(max(1, int(concurrency)))
        self.seen = set()
        self.tasks = []
        self.cache = {}
        self.reports = []
        self.monitor = None

    def start(self):
        self.monitor = asyncio.create_task(self._watch())

    async def scan_once(self):
        from src.core.store import ProjectStore
        from src.generation.video_commit import video_input_signature
        try:
            proj = ProjectStore(self.pdir).load()
        except (FileNotFoundError, ValueError):
            return
        queue = (self.rt._resolve_job_queue_or_none()
                 if hasattr(self.rt, "_resolve_job_queue_or_none") else None)
        busy = {j.shot_id for j in getattr(queue, "jobs", {}).values()
                if j.project == self.project and j.kind == "video"
                and j.state in ("queued", "running")}
        for shot in proj.all_shots():
            if shot.shot_id in busy or not shot.final_video:
                continue
            key = (shot.shot_id, shot.final_video, video_input_signature(proj, shot))
            if key in self.seen:
                continue
            self.seen.add(key)
            self.tasks.append(asyncio.create_task(self._review(key)))

    async def _review(self, key):
        async with self.slots:
            try:
                report = await run_independent_review(self.rt, self.pdir, self.project,
                    shot_ids=[key[0]], version_ids={key[0]: key[1]}, max_concurrency=1,
                    reason="live review while Producer executes")
                self.reports.append(report["report_path"])
                entry = (report.get("results") or [{}])[0]
                if entry.get("overall") in ("pass", "hard_error", "uncertain"):
                    self.cache[key[0]] = {**entry, "review_policy_version": REVIEW_POLICY_VERSION,
                                         "source_report": report["report_path"]}
            except Exception:
                # A missing client/corrupt input is reported by mandatory final review.
                # It must not cancel the Producer or any unrelated generation job.
                return

    async def _watch(self):
        while not self.stop_event.is_set():
            try:
                await self.scan_once()
            except Exception:
                pass  # Final review is mandatory; observer failure never means PASS.
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.interval_s)
            except asyncio.TimeoutError:
                pass

    async def close(self, *, cancel=False):
        self.stop_event.set()
        if self.monitor is not None:
            await self.monitor
        if cancel:
            for task in self.tasks:
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
