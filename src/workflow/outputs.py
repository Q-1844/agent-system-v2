# src/workflow/outputs.py
# Phase 47 B批：节点目标级完成校验与合法复用核验（PHASE47 §4.2/§4.3/§4.4）。
#
# 与 Phase 46 域命中校验（adapter.verify_completion 的 changed_refs 域检查）的关系：
#   域命中仍是底线（存在 ok+verified 回执）；本模块在其上加「本节点目标要求的具体输出」
#   核对——按 kind 读真实项目事实（canonical/资源/分镜/序列），检查存在性、图像/播放
#   文件、审批状态，并产出带 disposition（created/reused）/contentHash/reviewState 的
#   输出记录，供交接包与下游逐节点输入冻结使用。
#
# 复用语义（§4.3 + Phase51 A 跨 attempt 修正）：
#   - require_new（默认）：目标要求本节点产生所指定的新产物——按「本 run 本节点
#     全部 attempts 的真实写入」（changed_refs 收货留档）累计判定；重试/修复
#     attempt 延续此前写入+补缺项，不再因"本 attempt 零新写入"被整轮判死。
#     诚实性底线不变：只承认本节点任务回执覆盖的写入，项目里的旧素材不因此合格。
#   - allow_verified_reuse：节点级允许复用；服务端独立核验（项目归属=本 project、
#     对象存在、内容 hash、文件可读、必要审批），产出 reusedRefs；不是"无写入就放行"。
#   - 单条 spec 可用 allowReuse=true 局部放行（如混合任务补缺图、如实复用已合格图）。
#
# 本模块零 LLM、零写盘：只读项目事实 + 校验，回执由调用方（executor/adapter）落账。
from __future__ import annotations
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("workflow.outputs")

# kind → changed_refs 域（与 producer._derive_changed_refs 的键一致）
KIND_TO_DOMAIN = {
    "character": "characters", "look": "looks", "clothing": "clothings",
    "prop": "props", "composition": "compositions",
    "reference_asset": "reference_assets",
    "location": "locations", "location_view": "location_views",
    "scene_instance": "scene_instances", "scene_instance_view": "scene_instance_views",
    "shot": "shots", "scene": "scenes", "sequence": "sequences", "story": "story",
    "video_version": "shots",     # 视频版本产生 = shot 域变化
}

# 合法 spec 字段（fail closed：未知 kind / 非法字段在 validate 阶段拒绝）
SPEC_FIELDS = {"kind", "id", "character", "shotId", "sequenceId", "label",
               "withImage", "withApprovedRef", "playable", "withCompletedRender",
               "minCount", "allowReuse", "excludeVersionIds"}


class OutputSpecError(ValueError):
    pass


def validate_completion_config(completion: Any) -> List[str]:
    """校验节点 config.completion 结构（validate.py 保存/启动时调用）。返回错误列表。"""
    errs: List[str] = []
    if completion is None:
        return errs
    if not isinstance(completion, dict):
        return ["completion 必须是对象"]
    strategy = completion.get("strategy")
    if strategy is not None and strategy not in ("require_new", "allow_verified_reuse"):
        errs.append(f"completion.strategy 非法: {strategy!r}（require_new | allow_verified_reuse）")
    outputs = completion.get("outputs")
    if outputs is None:
        return errs
    if not isinstance(outputs, list) or not outputs:
        errs.append("completion.outputs 必须是非空数组")
        return errs
    for i, raw in enumerate(outputs):
        if not isinstance(raw, dict):
            errs.append(f"completion.outputs[{i}] 必须是对象")
            continue
        kind = raw.get("kind")
        if kind not in KIND_TO_DOMAIN:
            errs.append(f"completion.outputs[{i}].kind 非法: {kind!r}"
                        f"（合法: {sorted(KIND_TO_DOMAIN)}）")
            continue
        unknown = set(raw) - SPEC_FIELDS
        if unknown:
            errs.append(f"completion.outputs[{i}] 未知字段: {sorted(unknown)}（fail closed）")
        mc = raw.get("minCount", 1)
        if not isinstance(mc, int) or isinstance(mc, bool) or mc < 1:
            errs.append(f"completion.outputs[{i}].minCount 必须是正整数")
        if "excludeVersionIds" in raw:
            exv = raw.get("excludeVersionIds")
            if not isinstance(exv, list) or not exv or \
                    not all(isinstance(v, str) and v.strip() for v in exv):
                errs.append(f"completion.outputs[{i}].excludeVersionIds 必须是"
                            "非空字符串数组（被排除的问题版本 id 清单）")
        for b in ("withImage", "withApprovedRef", "playable", "withCompletedRender",
                  "allowReuse"):
            if b in raw and not isinstance(raw[b], bool):
                errs.append(f"completion.outputs[{i}].{b} 必须是布尔")
        # video_version 允许不带 shotId/id（跨镜头计数型规格：minCount 个可播放
        # 版本，created 判定仍以 changed_refs.shots 为准，不放松诚实性）。
    return errs


# ============ 项目事实读取 ============
def _content_hash(entity: Any) -> str:
    raw = json.dumps(entity, ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _file_ok(pdir: Path, local: Optional[str]) -> bool:
    """媒体文件可读判定。项目内 local 路径存在两种真实存储形态：
    ①相对项目目录（legacy 部分）；②相对仓库根（frames/characters 等生成资产，
    如 "projects\\{p}\\output\\frames\\x.png"）。两种都试，绝对路径直接判。"""
    if not local:
        return False
    try:
        p = Path(local)
        if p.is_absolute():
            return p.is_file() and p.stat().st_size > 0
        for base in (pdir, _repo_root()):
            cand = base / local
            if cand.is_file() and cand.stat().st_size > 0:
                return True
        return False
    except OSError:
        return False


def _repo_root() -> Path:
    global _REPO_ROOT_CACHE
    if _REPO_ROOT_CACHE is None:
        try:
            from deps import BASE_DIR
            _REPO_ROOT_CACHE = Path(BASE_DIR)
        except Exception:
            _REPO_ROOT_CACHE = Path(__file__).resolve().parents[2]
    return _REPO_ROOT_CACHE


_REPO_ROOT_CACHE: Optional[Path] = None


def _resources_doc(pdir: Path):
    from src.resources.store import ResourceStore
    return ResourceStore(pdir).load()


def _scene_doc(pdir: Path):
    from src.scene_system.store import SceneSystemStore
    return SceneSystemStore(pdir).load()


def _project_doc(pdir: Path):
    from src.core.store import ProjectStore
    return ProjectStore(pdir).load()


def _sequences(pdir: Path, project: str):
    from src.sequence.service import SequenceService
    return SequenceService(pdir, project).list_entries()


def _ref_asset_ok(pdir: Path, doc, asset_id: Optional[str]) -> bool:
    """引用资产存在且本地文件可读（URL-only 也算可用——下载由生成服务负责）。"""
    a = (doc.reference_assets or {}).get(asset_id or "")
    if a is None:
        return False
    return bool(a.local and _file_ok(pdir, a.local)) or bool(a.url)


# ============ 单 kind 解析（读真实事实 → 候选输出记录） ============
def _resolve_kind(pdir: Path, project: str, spec: dict) -> List[dict]:
    """按 spec 过滤项目事实，返回匹配实体（已带 withImage/playable/approved 判定）。
    每条记录：{kind, entityId, versionId?, reviewState, fileOk, approved, contentHash}。"""
    kind = spec["kind"]
    want_id = (spec.get("id") or "").strip() or None
    out: List[dict] = []

    def _rec(entity_id: str, *, version_id: Optional[str] = None,
             review_state: str = "none", file_ok: bool = True,
             approved: Optional[bool] = None, hash_payload: Any = None) -> dict:
        r = {"kind": kind, "entityId": entity_id, "versionId": version_id,
             "reviewState": review_state, "fileOk": file_ok}
        if approved is not None:
            r["approved"] = approved
        if hash_payload is not None:
            r["contentHash"] = _content_hash(hash_payload)
        out.append(r)
        return r

    if kind == "character":
        proj = _project_doc(pdir)
        for c in (proj.characters or []):
            if want_id and c.name != want_id:
                continue
            views = c.views or {}
            img_ok = any(a and getattr(a, "local", None) and _file_ok(pdir, a.local)
                         for a in views.values())
            _rec(c.name, file_ok=img_ok,
                 hash_payload={"name": c.name, "description_en": c.description_en,
                               "views": sorted(views.keys())})
        return out

    if kind in ("look", "clothing", "prop", "composition"):
        doc = _resources_doc(pdir)
        coll = getattr(doc, {"look": "looks", "clothing": "clothings",
                             "prop": "props", "composition": "compositions"}[kind])
        for eid, e in (coll or {}).items():
            if want_id and eid != want_id:
                continue
            if kind == "look" and spec.get("character") and \
                    e.character_name != spec["character"]:
                continue
            if kind == "look" and spec.get("label") and (e.label or "") != spec["label"]:
                continue
            refs = list(e.reference_asset_ids or [])
            img_ok = any(_ref_asset_ok(pdir, doc, rid) for rid in refs)
            approved = bool(e.approved_reference_asset_ids)
            state = getattr(e, "approval_status", "unreviewed")
            _rec(eid, review_state=("selected" if state == "approved" else "candidate"
                                    if state == "unreviewed" else "rejected"),
                 file_ok=img_ok, approved=approved,
                 hash_payload=e.to_dict() if hasattr(e, "to_dict") else vars(e))
        return out

    if kind == "reference_asset":
        doc = _resources_doc(pdir)
        assets = doc.reference_assets or {}
        matches = [(aid, a) for aid, a in assets.items()
                   if (not want_id or aid == want_id)]
        ok_n = 0
        for aid, a in matches:
            ok = _ref_asset_ok(pdir, doc, aid)
            if ok:
                ok_n += 1
            _rec(aid, file_ok=ok, hash_payload=a.to_dict())
        if not want_id and spec.get("minCount", 1) > 1:
            # 无 id 的计数型 spec：只保留可读实体，便于 minCount 判定
            return [r for r in out if r["fileOk"]] if ok_n else []
        return out

    if kind in ("location", "location_view", "scene_instance", "scene_instance_view"):
        doc = _scene_doc(pdir)
        # SceneSystemDocument 字段：locations / views（LocationView）/ scene_instances /
        # scene_instance_views（changed_refs 域键 location_views ≠ 文档字段名）
        coll = {"location": doc.locations, "location_view": doc.views,
                "scene_instance": doc.scene_instances,
                "scene_instance_view": doc.scene_instance_views}[kind]
        for eid, e in (coll or {}).items():
            if want_id and eid != want_id:
                continue
            ref_id = getattr(e, "reference_asset_id", None)
            rdoc = None
            try:
                rdoc = _resources_doc(pdir)
            except Exception:
                rdoc = None
            img_ok = bool(ref_id) and rdoc is not None and \
                _ref_asset_ok(pdir, rdoc, ref_id)
            state = getattr(e, "approval_status", "unreviewed")
            _rec(eid, review_state=("selected" if state == "approved" else "candidate"),
                 file_ok=img_ok, hash_payload=e.to_dict())
        return out

    if kind == "scene":
        proj = _project_doc(pdir)
        for sc in (proj.scenes or []):
            if want_id and sc.scene_id != want_id:
                continue
            has_content = bool((getattr(sc, "summary", "") or "").strip()
                               or (sc.shots or []))
            _rec(sc.scene_id, file_ok=has_content,
                 hash_payload={"scene_id": sc.scene_id,
                               "summary": getattr(sc, "summary", ""),
                               "characters": list(getattr(sc, "characters", None) or []),
                               "shots": [s.shot_id for s in (sc.shots or [])]})
        return out

    if kind == "shot":
        proj = _project_doc(pdir)
        for sc in (proj.scenes or []):
            for sh in (sc.shots or []):
                if want_id and sh.shot_id != want_id:
                    continue
                ff = getattr(sh, "first_frame", None)
                cands = list(getattr(ff, "candidates", None) or [])
                img_ok = any(c.local and _file_ok(pdir, c.local) for c in cands)
                # hash 只覆盖分镜身份（首帧批次+选中首帧）——不含 n_video_versions
                # （D 批真机：下游视频生成会改变镜头的视频版本数，属下游进度，
                #   不是分镜内容变更；计入 hash 会把上游交接包误判为 stale。）
                _rec(sh.shot_id, file_ok=img_ok,
                     hash_payload={"shot_id": sh.shot_id,
                                   "first_frame_batch": getattr(ff, "batch_id", None),
                                   "selected_first_frame":
                                       getattr(sh, "selected_first_frame", None)})
        return out

    if kind == "video_version":
        proj = _project_doc(pdir)
        want_shot = (spec.get("shotId") or "").strip() or None
        for sc in (proj.scenes or []):
            for sh in (sc.shots or []):
                if want_shot and sh.shot_id != want_shot:
                    continue
                for vv in (sh.video_versions or []):
                    if want_id and vv.version_id != want_id:
                        continue
                    playable = bool(vv.local and _file_ok(pdir, vv.local)) or bool(vv.url)
                    selected = bool(sh.final_video and sh.final_video == vv.version_id)
                    r = _rec(sh.shot_id, version_id=vv.version_id,
                             review_state=("selected" if selected else "candidate"),
                             file_ok=playable,
                             hash_payload={"version_id": vv.version_id,
                                           "video_id": vv.video_id,
                                           "status": vv.status,
                                           "local": vv.local, "url": vv.url})
                    # Phase51 C：createdAt 供计入窗口判定（文本更新污染 changed_refs
                    # 时，旧版本不得借 shot 成员资格冒充本 run 产出）
                    r["createdAt"] = float(getattr(vv, "created_at", 0) or 0)
        return out

    if kind == "sequence":
        entries = _sequences(pdir, project)
        for sid, entry in (entries or {}).items():
            if want_id and sid != want_id:
                continue
            renders = list(entry.render_versions or [])
            done = [r for r in renders if getattr(r, "status", "") == "done"]
            render_ok = any(_file_ok(pdir, getattr(r, "output_file", None)) for r in done)
            sel = entry.final_render if entry.final_render in \
                [r.version_id for r in done] else None
            review_state = "selected" if sel else ("candidate" if done else "none")
            _rec(sid, version_id=sel or (done[-1].version_id if done else None),
                 review_state=review_state, file_ok=render_ok,
                 hash_payload={"sequence_id": sid,
                               "clips": [getattr(c, "shot_id", None)
                                         for c in (entry.block.clips or [])],
                               "n_renders": len(renders)})
        return out

    if kind == "story":
        proj = _project_doc(pdir)
        text = (getattr(proj, "story_text", "") or "")
        want_scene = (spec.get("id") or "").strip() or None
        scenes = [{"id": sc.scene_id, "summary": (getattr(sc, "summary", "") or ""),
                   "characters": list(getattr(sc, "characters", None) or []),
                   "shots": len(sc.shots or [])}
                  for sc in (proj.scenes or [])]
        scene_ids = [s["id"] for s in scenes]
        ok = bool(text.strip()) and (not want_scene or want_scene in scene_ids)
        if ok or want_id is None:
            _rec(want_scene or "story_text", file_ok=ok,
                 hash_payload={"story_text": text, "scenes": scenes})
        return out

    return out


# ============ 完成校验（§4.2/§4.3 主入口） ============
def verify_node_outputs(pdir: Path, project: str, node_plan: dict,
                        changed: Dict[str, List[str]],
                        prior_changed: Optional[Dict[str, List[str]]] = None,
                        window_start: Optional[float] = None) -> dict:
    """目标级完成校验。返回：
    {ok, code, reason, missing: [...], outputs: [...created...], reused: [...]}。
    无 completion 配置 → {ok: True, code: "domain_hit_only"}（沿用域命中语义，不重复判定）。

    Phase51 A（修复 require_new 重试判死）：prior_changed = 本节点在本 run 此前
    attempts 的真实写入汇总（来自各 attempt 收货摘要的 changed_refs，服务端留档）。
    重试/修复 attempt 的合理行为是「延续+补缺」——此前 attempt 已真实写入并满足
    质量标记的实体继续有效（writeSource=prior_attempt，逐条可溯），attempt 只需
    补齐缺项；require_new 的诚实性底线不变：仍然只承认本节点任务回执覆盖的写入，
    项目里更早的/别的节点的旧素材不因此合格。

    Phase51 C（video_version 计入窗口）：window_start（epoch，通常=run 创建时刻）
    非空时，video_version 仅凭 shot 成员资格计入还须其 createdAt ≥ window_start——
    文本更新（negative_prompt 清理等）会把 shot 写进 changed_refs，旧视频版本
    不得借成员资格冒充本 run 产出（run_36919eb5a073 实证：14 镜文本更新被计入
    视频写入；若污染满 minCount 会假通过）。versionId 精确命中不受窗口限制
    （显式指名=真实写入凭据）。"""
    comp = (node_plan.get("completion") or {}) if node_plan else {}
    specs = comp.get("outputs") or []
    if not specs:
        return {"ok": True, "code": "domain_hit_only", "reason": "无目标级输出要求",
                "missing": [], "outputs": [], "reused": []}
    strategy = comp.get("strategy") or "require_new"
    prior_changed = prior_changed or {}
    created: List[dict] = []
    reused: List[dict] = []
    missing: List[dict] = []
    n_prior_credited = 0

    for i, spec in enumerate(specs):
        kind = spec["kind"]
        domain = KIND_TO_DOMAIN.get(kind, "")
        min_count = int(spec.get("minCount") or 1)
        spec_allow_reuse = bool(spec.get("allowReuse")) or strategy == "allow_verified_reuse"
        # Phase48 A08：修复目标钉定——被显式排除的版本（如已知问题视频本身）不得
        # 满足本 spec（created 与 reused 两路都排除），防止"旧产物绕过修复目标"。
        exclude_ids = {v for v in (spec.get("excludeVersionIds") or [])
                       if isinstance(v, str) and v.strip()}
        try:
            matches = _resolve_kind(pdir, project, spec)
        except Exception as e:   # 项目事实不可读 = 该 spec 无法满足（诚实缺项，不猜）
            missing.append({"index": i, "kind": kind,
                            "reason": f"项目事实读取失败：{type(e).__name__}: {str(e)[:160]}"})
            continue
        if exclude_ids:
            excluded_hits = [m for m in matches
                             if (m.get("versionId") or "") in exclude_ids]
            matches = [m for m in matches
                       if (m.get("versionId") or "") not in exclude_ids]
        else:
            excluded_hits = []
        changed_ids = set(changed.get(domain) or [])
        prior_ids = set(prior_changed.get(domain) or [])

        def _write_source(m: dict) -> Optional[str]:
            """实体写入来源：current=本 attempt；prior=本节点此前 attempt（同 run）；
            None=与本节点任务回执无关（旧素材，不计）。versionId 或 entityId 命中
            均算（video_version 的 changed 域记的是 shotId=entityId）。
            video_version + 窗口判定：仅凭 shot 成员资格计入时要求版本 createdAt ≥
            window_start（文本更新污染防护，Phase51 C）；versionId 精确命中不受
            窗口限制（显式指名=真实写入凭据）。"""
            vid, eid = m.get("versionId"), m["entityId"]
            exact = bool(vid and (vid in changed_ids or vid in prior_ids))
            if (not exact) and kind == "video_version" and window_start and \
                    float(m.get("createdAt") or 0) < float(window_start):
                return None   # 旧版本：shot 只是文本更新，媒体非本 run 产出
            if (vid and vid in changed_ids) or eid in changed_ids:
                return "current"
            if (vid and vid in prior_ids) or eid in prior_ids:
                return "prior"
            return None

        changed_created = [m for m in matches if _write_source(m) is not None]
        # Phase54 A2（P53-M 实证豁口）：修复目标镜必须本 attempt 真实写入——
        # reject 修复 attempt 对目标镜零重生成时，旧版本（writeSource=prior，本节点
        # 此前 attempt 写入）不再计入（旧版本冒充修复成果直达审核的豁口封堵）。
        _rrn = (node_plan or {}).get("repairRequireNew") or {}
        _rrn_shots = {str(x) for x in (_rrn.get("shotIds") or [])}
        if _rrn_shots and kind == "video_version":
            changed_created = [
                m for m in changed_created
                if _write_source(m) == "current"
                or (str(m.get("shotId") or m.get("entityId") or "")
                    not in _rrn_shots
                    and str(spec.get("shotId") or "") not in _rrn_shots)]
        # 硬性质量标记（withImage/withApprovedRef/playable/withCompletedRender）
        def _flag_ok(m: dict) -> bool:
            if spec.get("withImage") and not m.get("fileOk"):
                return False
            if spec.get("withApprovedRef") and not m.get("approved"):
                return False
            if spec.get("playable") and not m.get("fileOk"):
                return False
            if spec.get("withCompletedRender") and not (
                    m.get("reviewState") in ("selected", "candidate")
                    and m.get("fileOk")):
                return False
            return True

        quality_created = []
        for m in changed_created:
            if not _flag_ok(m):
                continue
            m = dict(m)
            m["writeSource"] = _write_source(m)
            quality_created.append(m)
        if len(quality_created) >= min_count:
            taken = quality_created[:min_count]
            n_prior_credited += sum(1 for m in taken if m.get("writeSource") == "prior")
            created.extend(taken)
            continue
        # 新产物不足 → 复用路径（节点级或 spec 级明确允许才可）
        if spec_allow_reuse:
            quality_all = [m for m in matches if _flag_ok(m)]
            # 复用核验（§4.3）：对象存在（matches 来自真实事实）+质量标记+内容 hash
            # 已由 _resolve_kind 产出；approval 状态如要求则已查。
            if len(quality_all) >= min_count:
                take = []
                for m in quality_all[:min_count]:
                    r = dict(m)
                    r["disposition"] = "reused"
                    r["verifyBasis"] = ("服务端读取项目事实核验：存在+内容hash"
                                        + ("+文件可读" if m.get("fileOk") else "")
                                        + ("+审批状态" if spec.get("withApprovedRef") else ""))
                    take.append(r)
                reused.extend(take)
                continue
        missing.append({
            "index": i, "kind": kind,
            "spec": {k: v for k, v in spec.items() if k != "note"},
            "reason": _missing_reason(spec, matches, len(quality_created),
                                      min_count, spec_allow_reuse)
                      + (f"；已被目标排除的问题版本 {sorted(exclude_ids)} 不计入"
                         f"（命中 {len(excluded_hits)} 项）" if exclude_ids else "")})
    ok = not missing
    prior_note = (f"（其中 {n_prior_credited} 项由本节点此前 attempt 的真实写入计入，"
                  "逐条 writeSource=prior_attempt 可溯）") if n_prior_credited else ""
    return {"ok": ok, "code": "outputs_verified" if ok else "outputs_missing",
            "reason": ("目标级输出核对通过（created=%d, reused=%d）%s"
                       % (len(created), len(reused), prior_note)) if ok else
                      f"目标级输出缺项 {len(missing)} 项",
            "missing": missing, "outputs": created, "reused": reused}


def _missing_reason(spec: dict, matches: List[dict], quality_created: int,
                    min_count: int, allow_reuse: bool) -> str:
    kind = spec["kind"]
    flags = [f for f in ("withImage", "withApprovedRef", "playable",
                         "withCompletedRender") if spec.get(f)]
    base = (f"要求 {kind} ×{min_count}"
            + (f"（{', '.join(flags)}）" if flags else "")
            + (f"（id={spec.get('id')}" if spec.get('id') else "")
            + (f", shot={spec.get('shotId')}）" if spec.get('shotId') else
               ("）" if spec.get('id') else "")))
    if not matches:
        return base + "：项目内不存在匹配实体"
    if quality_created < min_count and not allow_reuse:
        return (base + f"：找到 {len(matches)} 个既有实体，但本节点各 attempt 真实写入"
                f"且满足质量标记（{flags or '存在性'}）的仅 {quality_created} 个（需 "
                f"{min_count}）——节点未启用 allow_verified_reuse，"
                "不得以任务回执之外的旧素材冒充新产物")
    return (base + f"：匹配 {len(matches)} 个、其中本节点各 attempt 真实写入且满足质量标记 "
            f"{quality_created} 个（需 {min_count}）")


# ============ 交接包输出记录（§4.4） ============
def output_record_from(resolved: dict, disposition: str) -> dict:
    """resolved（verify/resolve 产物）→ 交接包 outputs 条目。"""
    return {
        "kind": resolved.get("kind"),
        "entityId": resolved.get("entityId"),
        "versionId": resolved.get("versionId"),
        "disposition": disposition,                     # created | reused
        "contentHash": resolved.get("contentHash"),
        "reviewState": resolved.get("reviewState"),
        "fileOk": bool(resolved.get("fileOk")),
    }


# ============ 逐节点输入冻结（§4.5） ============
def freeze_node_inputs(pdir: Path, project: str, run: dict, node_plan: dict) -> dict:
    """派发前解析上游输出，冻结具体输入版本与指纹。
    数据源：沿上游链收集各节点 handoff.outputs（v2，带 versionId/contentHash/
    reviewState）；服务端重读项目事实核对 contentHash 与 fileOk（freshness 检查）。
    返回 {resolvedInputs: [...], stale: [...], upstreamNodeIds: [...]}。
    上游尚未生产素材时返回空 resolvedInputs（启动时假装已固定是被禁止的）。"""
    resolved: List[dict] = []
    stale: List[dict] = []
    chain: List[str] = []
    nodes = run.get("nodes") or {}
    cur = node_plan.get("upstreamNodeId")
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        n = nodes.get(cur) or {}
        handoff = n.get("handoff") or {}
        for o in handoff.get("outputs") or []:
            if o.get("kind") in (None, "story"):
                resolved.append(dict(o))      # 文本/计划类无独立文件，直接采用
                continue
            # 重新读取项目事实核对 freshness（hash 比对 + 文件可读）
            spec = {"kind": o.get("kind")}
            if o.get("kind") == "video_version":
                spec["shotId"] = o.get("entityId")
                if o.get("versionId"):
                    spec["id"] = o["versionId"]
            else:
                spec["id"] = o.get("entityId")
            try:
                now = _resolve_kind(pdir, project, spec)
            except Exception as e:
                stale.append({**o, "staleReason": f"读取失败: {e}"})
                continue
            match = next((m for m in now
                          if m["entityId"] == o.get("entityId")
                          and (m.get("versionId") or None) == (o.get("versionId") or None)),
                         None)
            if match is None:
                stale.append({**o, "staleReason": "对象已不存在（版本消失）"})
            elif match.get("contentHash") and o.get("contentHash") and \
                    match["contentHash"] != o["contentHash"]:
                stale.append({**o, "staleReason": "内容已变化（hash 不一致）",
                              "currentHash": match["contentHash"]})
            else:
                m = dict(match)
                m["fromNodeId"] = cur
                m["disposition"] = o.get("disposition") or "created"
                resolved.append(m)
        cur = n.get("upstreamNodeId")
    return {"resolvedInputs": resolved, "stale": stale, "upstreamNodeIds": chain}
