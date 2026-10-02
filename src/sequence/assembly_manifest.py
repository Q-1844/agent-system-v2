# src/sequence/assembly_manifest.py
# Phase 44B — Assembly Manifest（成片输入契约 + 时间线投影的确定性构建器）。
#
# 性质（44B 任务书 B1）：ephemeral-in / deterministic / zero provider 的派生文档——
#   输入 = (Project canonical, 44A 冻结快照 manifest, SequenceBlock canonical, ffprobe 实测)
#   输出 = output/assembly/manifest_v{N}.json（只追加，assembly_revision 自增）
# 🔴 它【不是】第二 canonical：剪辑编辑面永远是 SequenceBlock（OCC revision）；
#    本文档负责把「冻结快照 pin（版本+哈希）」与「当前剪辑语义」绑成一个可评审、
#    可被 C 批次消费的契约。LLM 不得手写本文档（构建器是唯一写入者）。
#
# 最小不变式（44B B1，全部 fail closed，结构化错误带 shot_id 定位）：
#   1. 每个 source_out > source_in 且不超源实测时长（ffprobe 现场读，不信历史值）；
#   2. 排序、时长、转场可计算成确定时间线（cursor 单调累加，hard cut）；
#   3. 镜头版本、哈希、快照 revision 完全匹配（live 文件 sha256 == 快照 pin）；
#   4. 媒体丢失/不可解码/权限不足 → 可定位错误，绝不偷换素材。
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.atomic import atomic_write_json
from src.resources.generation import fingerprint, sha256_bytes

from .basis import resolve_clip_source
from .models import SequenceBlock, SequenceError
from .timeline import build_timeline_plan

ASSEMBLY_MANIFEST_SCHEMA = "assembly_manifest_v1"
# S1 renderer 只有 hard cut（§11）——transition 字段本批恒为 "cut"；
# 溶解/桥接类【建议】属提案文档（proposal），绝不混进渲染契约。
TRANSITION_HARD_CUT = "cut"


def _err(code: str, message: str) -> SequenceError:
    return SequenceError(code, message)


def _snapshot_index(snapshot_manifest: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    shots = snapshot_manifest.get("shots")
    if not isinstance(shots, list) or not shots:
        raise _err("snapshot_invalid", "快照 manifest 缺少 shots 数组（fail closed）")
    idx: Dict[str, Dict[str, Any]] = {}
    for s in shots:
        sid = str(s.get("shot_id") or "")
        if not sid or sid in idx:
            raise _err("snapshot_invalid", f"快照 manifest shot_id 缺失或重复: {sid!r}")
        for key in ("video_version_id", "media_sha256", "approved_status"):
            if not s.get(key):
                raise _err("snapshot_invalid",
                           f"快照 manifest {sid} 缺少 {key}（fail closed）")
        idx[sid] = s
    return idx


def build_assembly_manifest(project, snapshot_manifest: Dict[str, Any],
                            block: SequenceBlock, *, assembly_revision: int,
                            snapshot_path: Optional[str] = None,
                            narration_cues: Optional[List[Dict[str, Any]]] = None,
                            music_bed: Optional[Dict[str, Any]] = None,
                            seam_notes: Optional[List[Dict[str, Any]]] = None,
                            probe_fn=None) -> Dict[str, Any]:
    """由 (project, snapshot, block) 构建成片时间线契约文档（确定性；同输入同输出）。
    probe_fn 可注入伪探测（单测零 ffmpeg）；默认 media_ops.probe_video 现场实测。"""
    if probe_fn is None:
        from .media_ops import probe_video as probe_fn   # noqa: 函数内绑定保 monkeypatch
    if not isinstance(assembly_revision, int) or assembly_revision < 1:
        raise _err("manifest_invalid", f"assembly_revision 非法: {assembly_revision!r}")
    snap = _snapshot_index(snapshot_manifest)

    shot_ids = [c.shot_id for c in block.clips]
    if len(set(shot_ids)) != len(shot_ids):
        raise _err("manifest_invalid", f"clips 存在重复 shot_id: {shot_ids}")
    unknown = [sid for sid in shot_ids if sid not in snap]
    if unknown:
        raise _err("shot_not_in_snapshot",
                   f"镜头不在冻结快照中（不偷换素材）: {unknown}")

    # 逐 clip 解析 + 哈希核验 + 实测探测（fail closed，错误带 shot_id 定位）
    items: List[Dict[str, Any]] = []
    cursor = 0
    prev_scene: Optional[str] = None
    for i, clip in enumerate(block.clips):
        s = snap[clip.shot_id]
        try:
            _shot, version = resolve_clip_source(project, clip)
        except SequenceError as e:
            raise _err(e.code, f"clip[{i}] {clip.shot_id}: {e.message}")
        if version.version_id != s["video_version_id"]:
            raise _err("version_mismatch",
                       f"clip[{i}] {clip.shot_id}: live final_video="
                       f"{version.version_id} 与冻结快照 pin={s['video_version_id']} "
                       f"不符（不偷换素材；先由视频线收口再成片）")
        src_file = str(version.local or "")
        try:
            actual_sha = sha256_bytes(Path(src_file).read_bytes())
        except OSError as e:
            raise _err("source_unreadable",
                       f"clip[{i}] {clip.shot_id}: 源文件不可读 {src_file}: {e}")
        if actual_sha != s["media_sha256"]:
            raise _err("hash_mismatch",
                       f"clip[{i}] {clip.shot_id}: 源 sha256={actual_sha[:16]}… 与快照登记 "
                       f"{s['media_sha256'][:16]}… 不符——媒体已被替换/损坏（不偷换素材）")
        if version.status != "done":
            raise _err("final_video_not_done",
                       f"clip[{i}] {clip.shot_id}: 版本状态 {version.status}（未完成）")
        probe = probe_fn(src_file)
        if not probe.get("has_video", False):
            raise _err("source_unreadable",
                       f"clip[{i}] {clip.shot_id}: 源无视频流（不可解码）")
        src_dur = int(probe["duration_ms"])
        source_in_ms = clip.trim_in_ms
        source_out_ms = src_dur - clip.trim_out_ms
        if source_out_ms <= source_in_ms:
            raise _err("trim_invalid",
                       f"clip[{i}] {clip.shot_id}: source_out({source_out_ms}) <= "
                       f"source_in({source_in_ms})（fail closed）")
        if source_out_ms > src_dur or source_in_ms < 0:
            raise _err("trim_invalid",
                       f"clip[{i}] {clip.shot_id}: 截取范围 [{source_in_ms},{source_out_ms})"
                       f" 超出源时长 {src_dur}ms（fail closed）")
        effective = source_out_ms - source_in_ms
        scene_id = str(s.get("scene_id") or "")
        items.append({
            "index": i,
            "shot_id": clip.shot_id,
            "scene_id": scene_id,
            "scene_boundary": bool(prev_scene is not None and scene_id != prev_scene),
            "video_version_id": version.version_id,
            "media_sha256": s["media_sha256"],
            "source_file": src_file,
            "source_duration_ms": src_dur,
            "source_in_ms": source_in_ms,
            "source_out_ms": source_out_ms,
            "effective_ms": effective,
            "timeline_start_ms": cursor,
            "timeline_end_ms": cursor + effective,
            "transition": TRANSITION_HARD_CUT,
            "approved_status": s["approved_status"],
            "probe_now": {k: probe[k] for k in
                          ("width", "height", "fps", "vcodec", "pix_fmt",
                           "has_audio", "acodec") if k in probe},
        })
        # 复用既有 timeline 纯派生（同一 trim 语义）；cursor 累加与 TimelinePlan
        # 复算互为对照（见下）。
        cursor += effective
        prev_scene = scene_id

    # TimelinePlan 复算一遍（与 items 的 cursor 累加互为对照，两条路必须一致）
    from .timeline import ResolvedClip as _RC
    rcs = [_RC(index=it["index"], shot_id=it["shot_id"],
               version_id=it["video_version_id"], source_file=it["source_file"],
               trim_in_ms=it["source_in_ms"],
               trim_out_ms=it["source_duration_ms"] - it["source_out_ms"],
               source_duration_ms=it["source_duration_ms"],
               has_audio=bool(it["probe_now"].get("has_audio"))) for it in items]
    plan = build_timeline_plan(rcs)
    for it, tc in zip(items, plan.clips):
        if (it["timeline_start_ms"], it["timeline_end_ms"]) != \
                (tc.timeline_start_ms, tc.timeline_end_ms):
            raise _err("manifest_invalid",
                       f"clip[{it['index']}] {it['shot_id']}: timeline 计算不一致"
                       f"（{it['timeline_start_ms']}..{it['timeline_end_ms']} vs "
                       f"{tc.timeline_start_ms}..{tc.timeline_end_ms}）")

    profile = block.effective_render_profile()
    doc: Dict[str, Any] = {
        "schema": ASSEMBLY_MANIFEST_SCHEMA,
        "project_id": snapshot_manifest.get("project_id"),
        "snapshot_id": snapshot_manifest.get("snapshot_id"),
        "snapshot_mode": snapshot_manifest.get("mode"),
        "snapshot_path": snapshot_path,
        "sequence_id": block.sequence_id,
        "sequence_revision": block.revision,
        "assembly_revision": assembly_revision,
        "shot_items": items,
        "total_duration_ms": plan.total_duration_ms,
        "seam_count": max(len(items) - 1, 0),
        "seam_notes": list(seam_notes or []),
        "narration_cues": list(narration_cues or []),
        "music_bed": music_bed,
        "render_profile": dict(profile),
        "render_profile_fingerprint": fingerprint(profile),
        "audio_strategy": str(profile.get("audio", "native")),
        "renderer_schema": "sequence_renderer_v1",
    }
    # 指纹排除 assembly_revision（revision 只做编号；同语义重发 = 空转版本，
    # publish 层据指纹拒绝）。篡改任何内容键 → 指纹不符（fail closed）。
    doc["doc_fingerprint"] = fingerprint(
        {k: v for k, v in doc.items()
         if k not in ("doc_fingerprint", "assembly_revision")})
    return doc


def derive_audio_projection(plan, narration_versions: Optional[Dict[str, Any]] = None
                            ) -> Dict[str, Any]:
    """Phase 45.1：从 AudioPlan canonical（+旁白仓）确定性投影 manifest 声音段。
    44B 交付时 music_bed 恒 null——分场景音乐与全片旁白（44C 终版）后 manifest
    必须携带自己的声音事实。**只投影 canonical**：plan 是唯一来源，调用方不得
    手工改写投影结果（与 narration_cues 由构建器组装的语义一致）。
    plan: SequenceAudioPlan；narration_versions: NarrationStore.load() 的 cues dict
    （cue_id → {versions: [{audio_import_path, text, version_id, audio_sha256}]}）。"""
    layers_out: List[Dict[str, Any]] = []
    cues_out: List[Dict[str, Any]] = []
    # 旁白仓文件路径 → (cue_id, text, version_id, sha256)（取每 cue 最新版本）
    by_file: Dict[str, Dict[str, Any]] = {}
    for cue_id, entry in (narration_versions or {}).items():
        vs = (entry or {}).get("versions") or []
        if vs:
            v = vs[-1]
            by_file[str(v.get("audio_import_path") or "")] = {
                "cue_id": cue_id, "text": v.get("text"),
                "narration_version_id": v.get("version_id"),
                "audio_sha256_12": str(v.get("audio_sha256") or "")[:12]}
    for l in (plan.layers or []):
        if l.role == "narration":
            row: Dict[str, Any] = {
                "layer_id": l.layer_id, "role": l.role,
                "source": {"kind": l.source.kind, "file": l.source.file},
                "start_ms": l.start_ms, "duration_ms": l.duration_ms,
                "source_in_ms": l.source_in_ms, "gain_db": l.gain_db,
                "fade_in_ms": l.fade_in_ms, "fade_out_ms": l.fade_out_ms}
            if l.source.file in by_file:
                row.update(by_file[l.source.file])
            cues_out.append(row)
            continue
        layers_out.append({
            "layer_id": l.layer_id, "role": l.role,
            "source": {"kind": l.source.kind, "file": l.source.file},
            "start_ms": l.start_ms, "duration_ms": l.duration_ms,
            "source_in_ms": l.source_in_ms, "gain_db": l.gain_db,
            "fade_in_ms": l.fade_in_ms, "fade_out_ms": l.fade_out_ms})
    return {"source": "audio_plan_projection",
            "audio_plan_revision": plan.revision,
            "base_gain_db": plan.base_gain_db,
            "layers": layers_out,
            "narration_cues": cues_out}


def validate_assembly_manifest(doc: Dict[str, Any]) -> None:
    """加载/交接时复核不变式（防手改；fail closed）。"""
    if not isinstance(doc, dict) or doc.get("schema") != ASSEMBLY_MANIFEST_SCHEMA:
        raise _err("manifest_invalid", f"未知 manifest schema: "
                                       f"{(doc or {}).get('schema')!r}")
    stored_fp = doc.get("doc_fingerprint")
    recomputed = fingerprint({k: v for k, v in doc.items()
                              if k not in ("doc_fingerprint", "assembly_revision")})
    if stored_fp != recomputed:
        raise _err("manifest_tampered",
                   "manifest 指纹不符（文档被手改或损坏；fail closed）")
    items = doc.get("shot_items")
    if not isinstance(items, list) or not items:
        raise _err("manifest_invalid", "manifest 缺少 shot_items")
    cursor = 0
    for i, it in enumerate(items):
        for key in ("shot_id", "video_version_id", "media_sha256",
                    "source_in_ms", "source_out_ms", "timeline_start_ms"):
            if key not in it:
                raise _err("manifest_invalid", f"shot_items[{i}] 缺少 {key}")
        if int(it["source_out_ms"]) <= int(it["source_in_ms"]):
            raise _err("trim_invalid", f"shot_items[{i}]: source_out <= source_in")
        if int(it["timeline_start_ms"]) != cursor:
            raise _err("manifest_invalid",
                       f"shot_items[{i}]: timeline_start {it['timeline_start_ms']} "
                       f"≠ cursor {cursor}（时间线不确定）")
        cursor += int(it["source_out_ms"]) - int(it["source_in_ms"])
    if int(doc.get("total_duration_ms") or -1) != cursor:
        raise _err("manifest_invalid", "total_duration_ms 与逐镜累加不一致")
    if doc.get("transition") and doc.get("transition") != TRANSITION_HARD_CUT:
        raise _err("manifest_invalid", "renderer v1 仅支持 hard cut")
    for it in items:
        if it.get("transition") not in (None, TRANSITION_HARD_CUT):
            raise _err("manifest_invalid",
                       f"shot_items {it.get('shot_id')}: 非法 transition "
                       f"{it.get('transition')!r}")


# ============ 只追加落盘（output/assembly/）============

def _assembly_root(project_dir: Path) -> Path:
    return Path(project_dir) / "output" / "assembly"


def _index_path(project_dir: Path) -> Path:
    return _assembly_root(project_dir) / "assembly_manifests_index.json"


def load_assembly_index(project_dir: Path) -> Dict[str, Any]:
    p = _index_path(project_dir)
    if not p.exists():
        return {"schema": "assembly_manifest_index_v1", "latest_revision": 0,
                "history": []}
    import json
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise _err("manifest_store_corrupt",
                   f"assembly index 读取失败（不静默重置）: {e}")


def publish_assembly_manifest(project_dir: Path, doc: Dict[str, Any]) -> Dict[str, Any]:
    """append-only 落盘：manifest_v{N}.json + index 更新。N 必须等于 latest+1
    （并发/乱序 → manifest_revision_conflict；不覆盖既有 revision）。"""
    validate_assembly_manifest(doc)
    root = _assembly_root(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    index = load_assembly_index(project_dir)
    rev = int(doc["assembly_revision"])
    if rev != int(index.get("latest_revision") or 0) + 1:
        raise _err("manifest_revision_conflict",
                   f"assembly_revision={rev} 但 latest="
                   f"{index.get('latest_revision')}（只追加，不覆盖）")
    if any(h.get("doc_fingerprint") == doc["doc_fingerprint"]
           for h in index.get("history", [])):
        raise _err("manifest_revision_conflict",
                   f"内容与 revision {rev} 之前某版完全相同（拒绝空转版本；"
                   f"先改 sequence 再发布）")
    file = root / f"manifest_v{rev}.json"
    atomic_write_json(file, doc)
    index["latest_revision"] = rev
    index.setdefault("history", []).append({
        "revision": rev,
        "file": str(file),
        "sequence_id": doc.get("sequence_id"),
        "sequence_revision": doc.get("sequence_revision"),
        "doc_fingerprint": doc["doc_fingerprint"],
        "total_duration_ms": doc.get("total_duration_ms"),
        "published_at": time.time(),
    })
    atomic_write_json(_index_path(project_dir), index)
    return {"file": str(file), "index": index}


def load_assembly_manifest(project_dir: Path, revision: Optional[int] = None) -> Dict[str, Any]:
    """读取某版（None = 最新）manifest 并复核不变式。"""
    import json
    index = load_assembly_index(project_dir)
    if revision is None:
        revision = int(index.get("latest_revision") or 0)
    if revision < 1:
        raise _err("manifest_not_found", "项目尚无 assembly manifest")
    file = _assembly_root(project_dir) / f"manifest_v{revision}.json"
    if not file.exists():
        raise _err("manifest_not_found", f"manifest_v{revision}.json 不存在")
    try:
        doc = json.loads(file.read_text(encoding="utf-8"))
    except Exception as e:
        raise _err("manifest_store_corrupt", f"manifest_v{revision}.json 读取失败: {e}")
    validate_assembly_manifest(doc)
    return doc
