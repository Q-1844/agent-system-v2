"""Phase 3: bounded, evidence-led Producer decisions and media dependency guard.

This is a decision/validation layer, NOT a new agent or media dispatcher.
The source of truth stays with the frozen collab-mode snapshot, registered
AgentSkills, append-only C2 repair log, and native JobQueue receipts.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

REPAIR_SPECIALISTS = ("director", "script_writer", "character_designer", "scene_designer")
PARALLEL_PREFERENCES = ("allow", "prefer_serial", "required_serial")
DEFAULT_MODE = "combo_recommended"
DEPENDENT_OPENINGS = ("inherit_previous", "reference_previous")

#: Token budget for the Producer's collaboration-planning call.
#:
#: Round-03 local evidence (real Agnes, thinking=True): with a 1500-token
#: budget the visible answer is cut off inside the JSON object, so
#: ``_parse_json`` refuses it and the whole decision silently degrades to
#: ``deterministic_fallback``.  A budget of 3000 was the first one that
#: returned a complete, parseable plan.
#:
#: The budget now comes from the single global policy in
#: ``src/api/model_budget.py`` (default = the endpoint's own measured ceiling
#: 65536), so this call site can never be the one that silently truncates a
#: Producer decision again.
from src.api.model_budget import (
    DEFAULT_TEXT_MAX_TOKENS as PRODUCER_PLANNING_MAX_TOKENS,
    MIN_SAFE_TEXT_MAX_TOKENS as PRODUCER_PLANNING_MIN_SAFE_TOKENS,
)

logger = logging.getLogger(__name__)


class OrchestrationError(ValueError):
    """A proposed action would violate a verified dependency or role binding."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_mode(mode_id: Optional[str]) -> str:
    from src.agents.collab_modes import get_mode
    chosen = mode_id or DEFAULT_MODE
    if not isinstance(chosen, str) or get_mode(chosen) is None:
        raise OrchestrationError(f"unknown collaboration mode: {chosen!r}")
    return chosen


def _bound_skill(role: str, requested: Optional[str], registry) -> tuple[Optional[str], Optional[str]]:
    if registry is None:
        from src.agents.skill_system.registry import get_skill_registry
        registry = get_skill_registry()
    candidates = registry.view_for(role)
    if requested:
        selected = next(((pkg, binding) for pkg, binding in candidates
                         if pkg.skill_id == requested), None)
        if selected is None:
            raise OrchestrationError(f"{role} cannot load unbound or disabled skill {requested!r}")
        pkg = selected[0]
        return pkg.skill_id, pkg.version
    if not candidates:
        return None, None  # Specialist may still supply domain judgment without a Skill.
    pkg = candidates[0][0]
    return pkg.skill_id, pkg.version


def validate_repair_collaboration(raw: dict, finding: dict, *, mode_id: str = DEFAULT_MODE,
                                  registry=None, source: str = "producer") -> dict:
    """Validate model output against real registered specialists and Skill bindings.

    LLM output never grants a new tool family, changes a running mode or
    authorizes parallel video POSTs.  Dependencies form a DAG; cap expert
    *read-only proposal chats* to two concurrent requests.
    """
    mode_id = _validate_mode(mode_id)
    if not isinstance(raw, dict):
        raise OrchestrationError("collaboration decision must be an object")
    tasks = raw.get("tasks")
    # The frozen solo preset promises ZERO delegation.  Consulting a Director
    # under the guise of a read-only repair plan would quietly violate an
    # explicit user mode. The Producer may still reason about and approve a
    # local repair itself, using its existing scoped tool grants.
    solo_mode = mode_id == "producer_lead"
    eligible_roles = ("producer",) if solo_mode else REPAIR_SPECIALISTS
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= len(eligible_roles):
        raise OrchestrationError("repair collaboration task count violates frozen mode")
    normalized, seen = [], set()
    for item in tasks:
        if not isinstance(item, dict):
            raise OrchestrationError("specialist task must be an object")
        role = item.get("role")
        if role not in eligible_roles or role in seen:
            raise OrchestrationError("role violates frozen collaboration mode or is duplicated")
        seen.add(role)
        deps = item.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps) or len(set(deps)) != len(deps):
            raise OrchestrationError("depends_on must contain unique role IDs")
        pref = item.get("parallel_preference", "allow")
        if pref not in PARALLEL_PREFERENCES:
            raise OrchestrationError("unknown parallel preference")
        task = item.get("task")
        if not isinstance(task, str) or not task.strip() or len(task) > 900:
            raise OrchestrationError("specialist task needs a bounded objective")
        requested_skill = item.get("skill_id")
        if solo_mode and requested_skill is not None:
            raise OrchestrationError("producer_lead repair cannot delegate or load a specialist Skill")
        if solo_mode:
            skill, version = None, None
        elif "skill_id" in item and requested_skill is None:
            # Explicit opt-out: a specialist may offer domain advice without
            # loading a method. Omitted skill_id retains the legacy highest-
            # priority bound Skill choice for old plans/model responses.
            skill, version = None, None
        else:
            if requested_skill is not None and (not isinstance(requested_skill, str) or not requested_skill):
                raise OrchestrationError("invalid skill ID")
            skill, version = _bound_skill(role, requested_skill, registry)
        normalized.append({"role": role, "task": task.strip(), "skill_id": skill,
                           "skill_version": version, "depends_on": deps,
                           "parallel_preference": pref})
    for item in normalized:
        if set(item["depends_on"]) - seen or item["role"] in item["depends_on"]:
            raise OrchestrationError("specialist dependency missing or cyclic")
    pending = {t["role"]: set(t["depends_on"]) for t in normalized}
    done = set()
    while pending:
        ready = {r for r, dependencies in pending.items() if dependencies <= done}
        if not ready:
            raise OrchestrationError("specialist collaboration cycle")
        done.update(ready)
        for r in ready:
            pending.pop(r)
    reason = raw.get("reason", "")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1600:
        raise OrchestrationError("Producer must explain why these specialists are needed")
    max_parallel = raw.get("max_parallel", 2)
    if type(max_parallel) is not int or not 1 <= max_parallel <= 2:
        raise OrchestrationError("expert parallelism must be 1 or 2")
    plan = {"schema": "producer_collaboration_v1", "mode_id": mode_id,
            "finding_digest": _digest(finding), "lead": "producer",
            "source": source, "tasks": normalized, "max_parallel": max_parallel,
            "reason": reason.strip()}
    plan["plan_digest"] = _digest(plan)
    return plan


def fallback_repair_collaboration(finding: dict, *, mode_id: str = DEFAULT_MODE,
                                  registry=None, reason: str = "deterministic evidence routing") -> dict:
    """Narrow safe fallback when a planning chat is unavailable/malformed.

    No unbound Skill is conjured; R4 requests distinct script and shot opinions.
    """
    if mode_id == "producer_lead":
        return validate_repair_collaboration(
            {"tasks": [{"role": "producer", "task": "Repair the evidenced shot directly",
                        "skill_id": None, "depends_on": [],
                        "parallel_preference": "required_serial"}],
             "max_parallel": 1, "reason": "Frozen solo mode forbids delegation; Producer revises directly"},
            finding, mode_id=mode_id, registry=registry, source="frozen_solo_mode")
    roles = ["script_writer", "director"] if finding.get("level") == "R4" else ["director"]
    return validate_repair_collaboration(
        {"tasks": [{"role": role, "task": ("Check local story causality" if role == "script_writer"
                                              else "Simplify the evidenced shot while preserving causality"),
                    "depends_on": [], "parallel_preference": "allow"}
                   for role in roles], "max_parallel": 2, "reason": reason},
        finding, mode_id=mode_id, registry=registry, source="deterministic_fallback")


async def producer_choose_repair_collaboration(client, finding: dict, project, *,
                                               mode_id: str = DEFAULT_MODE,
                                               registry=None) -> dict:
    """Ask the Producer, not all four experts, who should contribute and how.

    Invalid advice becomes an explicitly recorded deterministic fallback, never
    an invented Skill, silently changed collaboration mode, or a blocker.
    """
    mode_id = _validate_mode(mode_id)
    if mode_id == "producer_lead":
        # Do not invoke planning-chat fallback which could delegate despite
        # a frozen explicit solo selection; this choice is deterministic.
        return fallback_repair_collaboration(finding, mode_id=mode_id, registry=registry)
    scene = next((s for s in project.scenes if s.scene_id == finding["scene_id"]), None)
    if registry is None:
        from src.agents.skill_system.registry import get_skill_registry
        registry = get_skill_registry()
    available_skills = {
        role: [{"skill_id": pkg.skill_id, "version": pkg.version}
               for pkg, _binding in registry.view_for(role)]
        for role in REPAIR_SPECIALISTS
    }
    context = {"finding": finding, "scene": (scene.summary if scene else ""),
               "mode_id": mode_id, "available_specialists": list(REPAIR_SPECIALISTS),
               "available_skills_by_role": available_skills,
               "mode_is_frozen": True}
    try:
        response = await client.chat([
            {"role": "system", "content": (
                "You are Producer deciding which specialists to CONSULT for an evidenced R3/R4 "
                "local repair. Choose only necessary roles (director, script_writer, "
                "character_designer, scene_designer). Do not switch the run's collaboration "
                "mode or change acceptance policy. JSON only: {tasks:[{role,task,depends_on,"
                "parallel_preference,skill_id?}],max_parallel:1|2,reason}. Use only "
                "listed role-bound skill IDs; skill_id:null explicitly means no Skill, "
                "omitting skill_id uses the highest-priority bound Skill. "
                "Explicit depends_on references other selected role IDs; choose required_serial when an expert must inspect "
                "another's result. Parallel expert advice is read-only, never parallel media POST."
            )}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}
        ], thinking=True, max_tokens=PRODUCER_PLANNING_MAX_TOKENS, temperature=0.2)
        from src.ai.repair_revision import _parse_json
        return validate_repair_collaboration(_parse_json(response), finding,
                                             mode_id=mode_id, registry=registry)
    except Exception as exc:
        # A silent fallback would hide the loss of the real Producer decision.
        logger.warning(
            "producer collaboration planning fell back to the deterministic "
            "routing: %s: %s", type(exc).__name__, str(exc)[:220])
        return fallback_repair_collaboration(
            finding, mode_id=mode_id, registry=registry,
            reason=f"safe fallback after {type(exc).__name__}: {str(exc)[:220]}")


def verify_stored_collaboration(plan: dict, finding: dict, *, mode_id: str,
                                registry=None) -> dict:
    """Re-validate a persisted decision and actual Skill versions on resume."""
    mode_id = _validate_mode(mode_id)
    if not isinstance(plan, dict) or plan.get("mode_id") != mode_id or plan.get("finding_digest") != _digest(finding):
        raise OrchestrationError("collaboration plan does not match the frozen run/finding")
    raw = {"tasks": plan.get("tasks"), "reason": plan.get("reason"),
           "max_parallel": plan.get("max_parallel")}
    rebuilt = validate_repair_collaboration(raw, finding, mode_id=mode_id,
                                            registry=registry,
                                            source=plan.get("source", "producer"))
    if rebuilt != plan:
        raise OrchestrationError("stored collaboration plan/Skill version has changed")
    return rebuilt


RELAY_REFERENCES = ("prev_tail", "prev_tail_look", "prev_tail_look_scene")


def existing_active_video_job(queue, project_name: str, shot_id: str) -> Optional[str]:
    """Never enqueue the same logical shot twice while a JobQueue intent is live.

    Queue.submit() is synchronous, so checking immediately before submit and
    enqueuing without an intervening await is atomic within this event loop.
    External CAS/UNKNOWN requests still require their existing reconciliation.
    """
    jobs = getattr(queue, "jobs", None) or {}
    for job in jobs.values():
        if (getattr(job, "project", None) == project_name
                and getattr(job, "shot_id", None) == shot_id
                and getattr(job, "kind", None) == "video"
                and getattr(job, "state", None) in ("queued", "running")):
            return str(job.job_id)
    return None


def _selected_tail(source, source_id: Optional[str], target_id: str,
                   pending_ids: set[str]) -> tuple[Optional[str], Optional[dict]]:
    if source is None or source_id == target_id:
        return "missing_or_self_source", None
    if source_id in pending_ids:
        return "upstream_in_same_unsettled_batch", None
    if not source.final_video or not any(
            v.version_id == source.final_video and v.status == "done"
            for v in source.video_versions):
        return "upstream_video_not_accepted", None
    ef = getattr(source, "video_end_frame", None)
    if not getattr(ef, "url_ready", False):
        return "upstream_real_tail_unavailable", None
    if getattr(source, "video_end_source_version", None) != source.final_video:
        return "upstream_tail_version_stale", None
    # Pin the *actual* selected URL as well as its version. A rehost/re-extract
    # between precheck and queued intent must not silently change the input.
    return None, {"source_shot_id": source_id,
                  "source_version": source.final_video,
                  "end_source_version": source.video_end_source_version,
                  "tail_url_sha256": hashlib.sha256(ef.url.encode("utf-8")).hexdigest()}


def video_submission_dependencies(project, targets: list[dict], *,
                                  preference: str = "allow") -> tuple[list, list, dict]:
    """Validate REAL video-tail dependencies before JobQueue submission.

    Both explicit opening continuity and the independent/multi prev_tail
    reference family count. A prev_tail may cross scenes, as the actual video
    adapter resolves the immediately previous shot in film order. Sharing a
    person or Look alone never forces serial production.
    """
    if preference not in PARALLEL_PREFERENCES:
        raise OrchestrationError("unknown media parallel preference")
    ready, blocked, deps = [], [], {}
    pending_ids = {x["shot_id"] for x in targets}
    ordered = [s for scene in project.scenes for s in scene.shots]
    prior_shot = {s.shot_id: (ordered[i - 1] if i else None)
                  for i, s in enumerate(ordered)}
    film_index = {s.shot_id: i for i, s in enumerate(ordered)}
    for item in targets:
        sid = item["shot_id"]
        shot = project.get_shot(sid)
        if shot is None:
            blocked.append({**item, "ok": False, "verified": False,
                            "status": "unknown_shot_dependency", "error": "unknown_shot_dependency"})
            continue
        origin = str(getattr(shot, "opening_source", "") or "")
        reference = str(getattr(shot, "video_reference_asset_id", "") or "")
        requirements = []
        if origin in DEPENDENT_OPENINGS:
            src_id = getattr(shot, "source_shot_id", None)
            src = project.get_shot(src_id) if src_id else None
            if src and src_id != sid and film_index.get(src.shot_id, len(ordered)) >= film_index[sid]:
                error, snapshot = "invalid_source_order", None
            else:
                error, snapshot = _selected_tail(src, src_id, sid, pending_ids)
            if not error and getattr(shot, "source_version", None) and shot.source_version != src.final_video:
                error = "downstream_source_version_stale"
            requirements.append(("opening", src_id, error, snapshot))
        # prev_tail is consumed only by the multi video adapter. A stale
        # sentinel on an i2v/keyframes shot cannot invent a media dependency.
        if reference in RELAY_REFERENCES and getattr(shot, "link_mode", "") == "multi":
            src = prior_shot.get(sid)
            src_id = src.shot_id if src else None
            error, snapshot = _selected_tail(src, src_id, sid, pending_ids)
            if src is None:
                error = "missing_reference_source"
            requirements.append(("reference_tail", src_id, error, snapshot))
        failed = next(((kind, src_id, error) for kind, src_id, error, _ in requirements if error), None)
        if failed:
            kind, src_id, why = failed
            blocked.append({**item, "ok": False, "verified": False,
                            "status": "continuity_dependency_unready",
                            "error": "continuity_dependency_unready",
                            "dependency": src_id, "dependency_type": kind,
                            "dependency_reason": why,
                            "fix_hint": "Wait for selected upstream video and verified actual ending frame; resync shot before retry; no provider POST."})
            continue
        if requirements:
            by_kind = {kind: snap for kind, _, _, snap in requirements}
            # Keep old opening snapshot shape for the C2/consumer contract;
            # optional secondary reference has a separate explicit receipt.
            snapshot = dict(by_kind.get("opening") or by_kind.get("reference_tail") or {})
            if "reference_tail" in by_kind:
                snapshot["reference_tail"] = by_kind["reference_tail"]
            deps[sid] = snapshot
        ready.append(item)
    return ready, blocked, deps


def assert_pinned_video_dependencies(project, shot_id: str, expected: Optional[dict]) -> None:
    """Worker-side validation of the original queued media snapshot.

    The Producer tool/REST preflight is not sufficient: a video may remain
    queued while its selected upstream video/tail is revised or rehosted.
    Validate again inside the canonical Store's atomic submit/commit guard,
    immediately before any new provider POST, and on result attachment.
    Existing recovering provider jobs bypass *new POST* validation upstream.
    """
    targets = [{"shot_id": shot_id, "frame": "video"}]
    _ready, blocked, current = video_submission_dependencies(project, targets)
    if blocked:
        raise OrchestrationError(
            f"video dependency unready before provider submission: "
            f"{blocked[0].get('dependency_reason') or blocked[0].get('error')}")
    if current.get(shot_id) != expected:
        raise OrchestrationError("queued video dependency version/tail changed; re-plan before submission")
