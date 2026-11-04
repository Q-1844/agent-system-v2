"""C2 dependency-aware execution planning (not a second JobQueue).

The Producer/approved production plan declares *creative* serial preferences.
Hard media prerequisites are always enforced by the executor.  Only a ready
node can occupy a concurrency slot; sharing an already-materialized reference
asset is not a dependency between video-generation jobs.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Set


PARALLEL_PREFERENCES = frozenset({"allow", "required_serial"})
TAIL_REFERENCES = frozenset({"prev_tail", "prev_tail_look", "prev_tail_look_scene"})
MAX_C2_PARALLEL = 2


class C2DependencyError(ValueError):
    pass


def validate_parallelism(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_C2_PARALLEL:
        raise C2DependencyError(f"C2 parallelism must be 1..{MAX_C2_PARALLEL}")
    return value


def shot_dependencies(project, plans: dict, *, selected_scene_ids: Iterable[str] | None = None
                      ) -> Dict[str, Set[str]]:
    """Freeze the film-wide DAG before starting any scene/shot workers.

    A plan's actual `source_shot_id` wins over guesswork based on overlapping
    actors.  `required_serial` is a Producer choice.  The `prev_tail` multi
    video adapter uses the immediately previous shot *in film order*, including
    across scene boundaries.  Frozen plans that replace multi with i2v do not
    accidentally inherit stale draft media references.
    """
    selected = (set(selected_scene_ids) if selected_scene_ids is not None else
                {sc.scene_id for sc in project.scenes})
    ordered = [(sc.scene_id, sh) for sc in project.scenes for sh in sc.shots]
    by_shot = {sh.shot_id: (i, sid, sh) for i, (sid, sh) in enumerate(ordered)}
    if len(by_shot) != len(ordered):
        raise C2DependencyError("duplicate film-wide shot ID")
    deps: Dict[str, Set[str]] = {}
    for i, (scene_id, sh) in enumerate(ordered):
        if scene_id not in selected:
            continue
        plan = plans.get(scene_id)
        if plan is None:
            raise C2DependencyError(f"missing frozen production plan for {scene_id}")
        planned = next((p for p in plan.shots if p.shot_id == sh.shot_id), None)
        if planned is None:
            raise C2DependencyError(f"missing frozen planned shot {sh.shot_id}")
        pref = getattr(planned, "parallel_preference", "allow")
        if pref not in PARALLEL_PREFERENCES:
            raise C2DependencyError(f"invalid Producer parallel preference for {sh.shot_id}")
        sources = set()
        if planned.requires_previous_video_end:
            if not planned.source_shot_id:
                raise C2DependencyError(f"missing source for relay {sh.shot_id}")
            sources.add(planned.source_shot_id)
        if (planned.link_mode == "multi" and
                getattr(sh, "video_reference_asset_id", None) in TAIL_REFERENCES):
            if i == 0:
                raise C2DependencyError(f"first shot cannot use prev_tail: {sh.shot_id}")
            sources.add(ordered[i - 1][1].shot_id)
        if pref == "required_serial" and i > 0:
            sources.add(ordered[i - 1][1].shot_id)
        for source in sources:
            if source not in by_shot:
                raise C2DependencyError(f"unknown shot dependency: {sh.shot_id} -> {source}")
            if by_shot[source][0] >= i:
                raise C2DependencyError(f"forward or cyclic shot dependency: {sh.shot_id} -> {source}")
        deps[sh.shot_id] = sources
    return deps


def scene_dependencies(project, shot_deps: Dict[str, Set[str]],
                       selected_scene_ids: Iterable[str]) -> Dict[str, Set[str]]:
    """Collapse the film DAG to a scene DAG for the project-level scheduler."""
    selected = set(selected_scene_ids)
    scene_for = {sh.shot_id: sc.scene_id for sc in project.scenes for sh in sc.shots}
    result = {sid: set() for sid in selected}
    for shot_id, sources in shot_deps.items():
        scene = scene_for[shot_id]
        for source in sources:
            dep_scene = scene_for[source]
            if dep_scene != scene and dep_scene in selected:
                result[scene].add(dep_scene)
    return result


def upstream_ready(project, source_id: str, *, expected_version: str | None = None) -> bool:
    """A historical/locked media source must have a *matching* real end frame."""
    source = project.get_shot(source_id)
    if not source or not source.final_video:
        return False
    if expected_version is not None and source.final_video != expected_version:
        return False
    end = getattr(source, "video_end_frame", None)
    return (getattr(source.video, "status", None) == "done"
            and bool(getattr(end, "url_ready", False))
            and source.video_end_source_version == source.final_video)


def media_sources_for_shot(project, planned, shot_id: str) -> Set[str]:
    """Identify only dependencies which consume the *actual* produced video tail.

    Producer's required_serial choice also creates a DAG edge, but does not
    require a tail URL.  Keeping the two distinct avoids false production
    blocks and allows independent scenes to overlap.
    """
    sources: Set[str] = set()
    if planned.requires_previous_video_end and planned.source_shot_id:
        sources.add(planned.source_shot_id)
    if (planned.link_mode == "multi" and
            getattr(project.get_shot(shot_id), "video_reference_asset_id", None)
            in TAIL_REFERENCES):
        film = [sh.shot_id for scene in project.scenes for sh in scene.shots]
        if shot_id not in film or film.index(shot_id) == 0:
            raise C2DependencyError(f"invalid prev_tail shot: {shot_id}")
        sources.add(film[film.index(shot_id) - 1])
    return sources


def source_completed_for_run(project, source_id: str, *, parent_run=None,
                             scene_manager=None, require_media: bool = False) -> bool:
    """Check a dependency against the *new* child checkpoint, never old media.

    A project may still retain an ACCEPTED older version while its upstream
    shot is being regenerated. Merely seeing final_video is not permission to
    start a dependent shot. Parent runs select a concrete child shot version;
    historical/unselected sources use their already-materialized final.
    """
    source = project.get_shot(source_id)
    if source is None:
        return False
    if parent_run is not None:
        entries = {e.scene_id: e for e in parent_run.scenes}
        source_scene = next((scene.scene_id for scene in project.scenes
                             if any(s.shot_id == source_id for s in scene.shots)), None)
        if source_scene in entries:
            entry = entries[source_scene]
            if not entry.child_run_id or scene_manager is None:
                return False
            child = scene_manager.get_run(parent_run.project_name, entry.child_run_id)
            if child is None:
                return False
            state = next((s for s in child.shots if s.shot_id == source_id), None)
            if state is None or state.status not in ("completed", "skipped_locked"):
                return False
            # Revisions or concurrent media selection must never let a stale
            # child receipt pass as the currently selected upstream version.
            if not state.video_version_id or source.final_video != state.video_version_id:
                return False
            return (upstream_ready(project, source_id,
                                   expected_version=state.video_version_id)
                    if require_media else True)
    if require_media:
        return upstream_ready(project, source_id)
    return bool(source.final_video and getattr(source.video, "status", None) == "done")
