"""Input-scoped guard for attaching an asynchronously generated video."""
from dataclasses import asdict
import hashlib
import json


def video_input_signature(project, shot) -> str:
    data = shot.to_dict()
    # Outputs and presentation state must not invalidate independent generation.
    for key in ("video", "video_versions", "final_video", "video_end_frame",
                "video_end_source_version", "status", "error", "updated_at",
                "audio", "subtitle_srt"):
        data.pop(key, None)
    data["video_plan"] = {"num_frames": shot.video.num_frames,
                          "frame_rate": shot.video.frame_rate}
    chars = [asdict(c) for c in project.characters if c.name in shot.characters]
    upstream = None
    if (shot.opening_source in ("inherit_previous", "reference_previous")
            or getattr(shot, "video_reference_asset_id", None) == "prev_tail_look_scene"):
        from .video_contract import find_prev_scene_shot
        prev = project.get_shot(shot.source_shot_id) if shot.source_shot_id else find_prev_scene_shot(project, shot)
        if prev:
            upstream = {"shot_id": prev.shot_id, "final_video": prev.final_video,
                        "tail_version": prev.video_end_source_version,
                        "tail": asdict(prev.video_end_frame)}
    payload = {"shot": data, "characters": chars, "style": asdict(project.style),
               "native_av": asdict(project.native_av), "upstream": upstream}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()
