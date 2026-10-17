# src/storyboard/structure_service.py
# AI-7D-R · 分镜结构操作（中性，与 delete_service 同级；router 与 agents 均可 import）。
#
# 纯内存操作：调用方负责 ProjectStore.save。不 import router/deps。
# 顺序模型：段无顺序字段，权威顺序 = project.scenes 数组位置；镜头权威顺序 = scene.shots
# 数组位置，Shot.index 只是显示号（任何位置变动后 renumber）。id 是身份证号：插入中间不
# 重排已有 id，新实体取下一个单调序号，删除不回收。参照 src/revision/changeset.py 的
# INSERT_SCENE/INSERT_SHOT/REORDER_SHOTS/UPDATE_* 逻辑。
from __future__ import annotations

from typing import Any, Optional

from src.core.models import Project, Scene, Shot


class StructureError(Exception):
    """结构操作确定性错误。code: not_found / invalid。"""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


def renumber_shot_indices(scene: Scene) -> None:
    """把段内镜头 index 按数组当前顺序重排为 1..N 连续。不改变任何 shot_id。"""
    for i, sh in enumerate(scene.shots, start=1):
        sh.index = i


def allocate_scene_id(project: Project) -> str:
    """分配下一个故事段 id（s{NN:02d}）。防御性重算 next_scene_seq（历史项目/手动改过导致
    水位偏低时取 max+1），删除不回收。会自增 project.next_scene_seq。"""
    used = []
    for sc in project.scenes:
        sid = sc.scene_id or ""
        if sid.startswith("s"):
            try:
                used.append(int(sid[1:]))
            except ValueError:
                pass
    project.next_scene_seq = max(project.next_scene_seq, (max(used) + 1) if used else 1)
    sid = f"s{project.next_scene_seq:02d}"
    project.next_scene_seq += 1
    return sid


def allocate_shot_id(scene: Scene, extra_used: Optional[set[int]] = None) -> str:
    """分配段内下一个镜头 id（{scene_id}_c{NN:02d}）。按已用最大 _cNN+1，删除不回收。
    extra_used: 本批刚分配但还没插入 scene.shots 的号（批量插入时防同批撞号）。"""
    used = list(extra_used or [])
    for sh in scene.shots:
        s = sh.shot_id or ""
        if "_c" in s:
            try:
                used.append(int(s.split("_c")[-1]))
            except ValueError:
                pass
    nxt = (max(used) + 1) if used else 1
    return f"{scene.scene_id}_c{nxt:02d}"


def _coerce_duration(v: Any) -> float:
    try:
        d = float(v)
        return d if d > 0 else 5.0
    except Exception:
        return 5.0


def _build_scene(project: Project, spec: dict) -> Scene:
    """构造一个新故事段并分配 id（不插入数组，调用方决定位置）。"""
    sid = allocate_scene_id(project)
    return Scene(scene_id=sid, title="",
                 summary=str(spec.get("summary") or ""),
                 characters=list(spec.get("characters") or []), shots=[])


def _build_and_insert(project: Project, insert_at: int,
                      specs: list[dict]) -> list[Scene]:
    """在指定下标处构造并插入若干故事段（分配 id）。调用方负责解析 insert_at。"""
    new_scenes = [_build_scene(project, spec) for spec in (specs or [])]
    project.scenes[insert_at:insert_at] = new_scenes
    return new_scenes


def _resolve_anchor_index(project: Project, before: Optional[str],
                          after: Optional[str]) -> int:
    """把 before/after 锚点解析为插入下标。before=在锚点前；after=在锚点后；
    都空=末尾。before/after 同时给抛 invalid；锚点不存在抛 not_found。"""
    if before and after:
        raise StructureError("invalid", "只能指定 before_scene_id 或 after_scene_id 之一")
    anchor = before or after
    if not anchor:
        return len(project.scenes)
    idx = next((i for i, s in enumerate(project.scenes)
                if s.scene_id == anchor), None)
    if idx is None:
        raise StructureError("not_found", f"锚点故事段不存在: {anchor}")
    return idx if before else idx + 1


def insert_scenes_after(project: Project, after_scene_id: Optional[str],
                        specs: list[dict]) -> list[Scene]:
    """在 after_scene_id 之后一次性插入若干故事段（保持 specs 顺序）。
    after_scene_id 为 None/空 → 追加末尾。锚点不存在抛 StructureError(not_found)。
    返回新建 Scene 列表（已进入 project.scenes，id 已分配）。"""
    if after_scene_id:
        idx = next((i for i, s in enumerate(project.scenes)
                    if s.scene_id == after_scene_id), None)
        if idx is None:
            raise StructureError("not_found", f"after_scene_id {after_scene_id} 不存在")
        insert_at = idx + 1
    else:
        insert_at = len(project.scenes)
    return _build_and_insert(project, insert_at, specs)


def insert_scenes(project: Project, *, before: Optional[str] = None,
                  after: Optional[str] = None,
                  specs: Optional[list[dict]] = None) -> list[Scene]:
    """在 before 之前 / after 之后插入若干故事段；都不指定 → 追加末尾。
    before/after 同时给抛 invalid；锚点不存在抛 not_found。返回新建 Scene 列表。
    比 insert_scenes_after 多支持「插到最前」（before=第一个段）。"""
    insert_at = _resolve_anchor_index(project, before, after)
    return _build_and_insert(project, insert_at, specs or [])


def insert_scenes_at_anchors(project: Project, inserts: list[dict]) -> dict:
    """一次在【多个不同锚点】前/后插入新故事段（story.insert_many 的中性层）。

    inserts: [{"before": sid|None, "after": sid|None, "specs": [{summary, characters}, ...]}]
    每项 before/after 必须且只能给一个；scenes(specs) 非空，数组顺序即该锚点处的相对顺序。

    快照语义（核心）：所有锚点都按【调用前快照】解释——先对快照线性走查算出唯一的最终
    顺序，再一次性替换 project.scenes，绝不按「插一个之后的新位置」解释后续锚点。
    走查规则：按快照顺序逐段 S，先放 before=S 的块，再放 S，再放 after=S 的块。
    因此相邻锚点落同一间隙时（after X 与 before Y，X、Y 在快照中相邻）顺序确定：
    X → [after X 块] → [before Y 块] → Y。

    id 分配按最终顺序递增（走查到哪个块就分配哪个块），新 id 与最终位置单调一致。
    同一锚点在一批中出现多次（before/after 都算）→ invalid；锚点不存在 → not_found；
    before/after 同给或都不给 → invalid。任一非法整批不写（纯内存，调用方负责 save）。

    返回 {"new_scenes": [Scene...最终顺序], "placements": [{scene_id, anchor, side}],
          "final_order": [scene_id...]}。"""
    if not isinstance(inserts, list) or not inserts:
        raise StructureError("invalid", "inserts 必须是非空数组")
    existing_ids = [s.scene_id for s in project.scenes]
    existing_set = set(existing_ids)

    parsed: list[tuple[Optional[str], Optional[str], str, list[dict]]] = []
    seen_anchors: set[str] = set()
    for i, it in enumerate(inserts):
        if not isinstance(it, dict):
            raise StructureError("invalid", f"inserts[{i}] 必须是对象")
        before = (it.get("before") or "").strip() or None
        after = (it.get("after") or "").strip() or None
        if bool(before) == bool(after):
            raise StructureError(
                "invalid",
                f"inserts[{i}] 必须且只能指定 before_scene_id / after_scene_id 之一")
        anchor = before or after
        if anchor not in existing_set:
            raise StructureError("not_found", f"锚点故事段不存在: {anchor}")
        if anchor in seen_anchors:
            raise StructureError(
                "invalid",
                f"inserts[{i}] 锚点 {anchor} 在本批重复：同一批中每个锚点只能出现一次"
                "（同一锚点要插多段，请合并到该项的 scenes 数组里按顺序给出）")
        seen_anchors.add(anchor)
        specs = it.get("specs")
        if not isinstance(specs, list) or not specs:
            raise StructureError("invalid", f"inserts[{i}].scenes 必须是非空数组")
        parsed.append((before, after, anchor, specs))

    before_blocks = {b: specs for (b, a, _anchor, specs) in parsed if b}
    after_blocks = {a: specs for (b, a, _anchor, specs) in parsed if a}

    new_scenes: list[Scene] = []
    placements: list[dict] = []
    final_scenes: list = []
    for s in project.scenes:
        for spec in before_blocks.get(s.scene_id, []):
            sc = _build_scene(project, spec)
            new_scenes.append(sc)
            placements.append({"scene_id": sc.scene_id,
                               "anchor": s.scene_id, "side": "before"})
            final_scenes.append(sc)
        final_scenes.append(s)
        for spec in after_blocks.get(s.scene_id, []):
            sc = _build_scene(project, spec)
            new_scenes.append(sc)
            placements.append({"scene_id": sc.scene_id,
                               "anchor": s.scene_id, "side": "after"})
            final_scenes.append(sc)
    project.scenes = final_scenes
    return {"new_scenes": new_scenes, "placements": placements,
            "final_order": [s.scene_id for s in final_scenes]}


def move_scene(project: Project, scene_id: str, *,
               before: Optional[str] = None,
               after: Optional[str] = None) -> Scene:
    """把已存在的故事段移动到 before 之前 / after 之后（都不指定 → 移到末尾）。
    不改 scene_id、不重排段内镜头、不分配新 ID。
    段/锚点不存在抛 not_found；before/after 同时给或指向自身抛 invalid。
    返回被移动的 Scene（同一对象，已在新位置）。"""
    if before and after:
        raise StructureError("invalid", "只能指定 before_scene_id 或 after_scene_id 之一")
    if scene_id and scene_id in (before, after):
        raise StructureError("invalid", "不能把故事段移动到自身前后")
    cur_idx = next((i for i, s in enumerate(project.scenes)
                    if s.scene_id == scene_id), None)
    if cur_idx is None:
        raise StructureError("not_found", f"故事段不存在: {scene_id}")

    anchor = before or after
    if anchor:
        a_idx = next((i for i, s in enumerate(project.scenes)
                      if s.scene_id == anchor), None)
        if a_idx is None:
            raise StructureError("not_found", f"锚点故事段不存在: {anchor}")
        target = a_idx if before else a_idx + 1
    else:
        target = len(project.scenes)

    scene = project.scenes.pop(cur_idx)
    # pop 后，若锚点原本在被移段之后，其下标左移 1
    if anchor and target > cur_idx:
        target -= 1
    elif not anchor:
        target = len(project.scenes)   # 移到末尾：pop 后末尾下标
    project.scenes.insert(target, scene)
    return scene


def move_shot(project: Project, shot_id: str, *,
              before: Optional[str] = None,
              after: Optional[str] = None) -> Shot:
    """把已存在的镜头在同一故事段内移动到 before 之前 / after 之后（都不指定 → 移到段尾）。
    不改 shot_id、不重排其它段、不分配新 ID；移动后本段 index 重排连续。
    段/镜头/锚点不存在抛 not_found；before/after 同时给或指向自身抛 invalid；
    锚点属于其它段抛 invalid（第一版只支持同段内移动：shot_id 身份含段前缀，跨段移动
    会让身份与归属脱节，属后续需求）。返回被移动的 Shot（同一对象，已在新位置）。"""
    if before and after:
        raise StructureError("invalid", "只能指定 before_shot_id 或 after_shot_id 之一")
    if shot_id and shot_id in (before, after):
        raise StructureError("invalid", "不能把镜头移动到自身前/后")
    scene = next((s for s in project.scenes
                  if any(sh.shot_id == shot_id for sh in s.shots)), None)
    if scene is None:
        raise StructureError("not_found", f"镜头不存在: {shot_id}")
    cur_idx = next(i for i, sh in enumerate(scene.shots) if sh.shot_id == shot_id)

    anchor = before or after
    if anchor:
        a_idx = next((i for i, sh in enumerate(scene.shots)
                      if sh.shot_id == anchor), None)
        if a_idx is None:
            raise StructureError(
                "not_found",
                f"锚点镜头 {anchor} 不在同一故事段 {scene.scene_id} 内（第一版只支持同段内移动）")
        target = a_idx if before else a_idx + 1
    else:
        target = len(scene.shots)

    shot = scene.shots.pop(cur_idx)
    # pop 后，若锚点原本在被移镜头之后，其下标左移 1
    if anchor and target > cur_idx:
        target -= 1
    elif not anchor:
        target = len(scene.shots)   # 移到段尾：pop 后末尾下标
    scene.shots.insert(target, shot)
    renumber_shot_indices(scene)
    return shot


def insert_shots_after(project: Project, scene_id: str, after_shot_id: Optional[str],
                       specs: list[dict]) -> list[Shot]:
    """在 scene_id 段内、after_shot_id 之后一次性插入若干镜头。after_shot_id 为 None/空
    → 追加段尾。段/锚点不存在抛 not_found。返回新建 Shot 列表（已进入 scene.shots，index
    已按新顺序重排）。"""
    scene = next((s for s in project.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise StructureError("not_found", f"故事段 {scene_id} 不存在")
    if after_shot_id:
        idx = next((i for i, sh in enumerate(scene.shots)
                    if sh.shot_id == after_shot_id), None)
        if idx is None:
            raise StructureError("not_found", f"after_shot_id {after_shot_id} 不存在")
        # 接力约束：若 anchor 的紧随后继正接力 anchor（source_shot_id 指向它），
        # 不能在二者之间插入——新镜头会打断"后继的上一镜是 anchor"。段尾（无后继）可插。
        if idx + 1 < len(scene.shots):
            successor = scene.shots[idx + 1]
            if successor.source_shot_id == after_shot_id:
                raise StructureError(
                    "dependency_block",
                    f"镜头 {successor.shot_id} 正接力 {after_shot_id}，不能在它俩之间插入"
                    f"（会打断接力）。可改在 {successor.shot_id} 之后插入，或先解除接力关系。")
        insert_at = idx + 1
    else:
        insert_at = len(scene.shots)

    new_shots = []
    used_nums: set[int] = set()   # 本批已分配的 c 号（切片插入前还没进 scene.shots）
    for spec in (specs or []):
        sid = allocate_shot_id(scene, extra_used=used_nums)
        try:
            used_nums.add(int(sid.split("_c")[-1]))
        except ValueError:
            pass
        sh = Shot(shot_id=sid, scene_id=scene_id, index=0,
                  description=str(spec.get("description") or ""),
                  characters=list(spec.get("characters") or []),
                  target_duration_s=_coerce_duration(spec.get("target_duration_s")))
        new_shots.append(sh)
    scene.shots[insert_at:insert_at] = new_shots
    renumber_shot_indices(scene)
    return new_shots


def update_scene_fields(project: Project, scene_id: str, *,
                        summary: Optional[str] = None,
                        characters: Optional[list] = None) -> dict:
    """原地改故事段字段。只改非 None 字段。返回 {scene_id, changed:[...]}。"""
    scene = next((s for s in project.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise StructureError("not_found", f"故事段 {scene_id} 不存在")
    changed = []
    if summary is not None:
        scene.summary = summary
        changed.append("summary")
    if characters is not None:
        scene.characters = list(characters)
        changed.append("characters")
    return {"scene_id": scene_id, "changed": changed}


# 可通过 update_shot_fields 写入的结构化字段白名单（创作字段，不含产物/接力/状态）。
# 这些字段都被 src/core/prompt_builder.py 消费，最终决定生成画面。
# dialogue（对白文本）是 TTS 配音（routers/shots.py audio）的创作输入，同样是镜头
# canonical 创作字段——不进图片 prompt，但归属镜头创作数据，故列在白名单内。
# negative_prompt 不进正向 prompt 拼接（独立 API 参数，native_av_pack 携带），
# 但它是视频生成的正式负面约束通路（Phase 39D；P39C 实证：只进 trace 无效）。
_SHOT_WRITABLE_FIELDS = (
    "description", "characters", "target_duration_s", "dialogue",
    "negative_prompt",   # Phase 39D：负面约束的正式生成字段通路
    "shot_size", "camera", "camera_angle", "camera_movement",
    "composition", "lighting", "motion", "motion_pace",
    "environment_motion", "action_sequence",
    "performance_intent",   # C2 22.md §13：表演意图（dict{emotion,intensity,note}）
    "video_reference_asset_id",   # 34.md Human Gate：multi 按镜参考选择（adapter 校验资格）
    "opening_state", "important_details", "required_elements",
    "last_frame_description", "end_state", "frame_change",
    "add_elements", "remove_elements", "end_composition",
    "preserve_elements", "preserve_custom", "transition_description",
    "video_preserve_elements", "video_preserve_custom",
    "note",
    "link_mode",   # Phase D §25.C：尾帧生产需 keyframes 模式（值校验见 update_shot_fields）
    "opening_source",   # Phase56 B：agent 接力声明通道（值校验见 update_shot_fields；
                        # 生成侧「opening_source==inherit 且 source_shot_id 非空」双条件+preflight 依赖闸兜底）
    "opening_cut_reason",   # Phase56 A2 v2：independent 切镜显式依据（值校验见 update_shot_fields）
)

# 哪些结构化字段影响哪个生成 prompt（与 prompt_builder 消费的字段对齐）。
# 改了这些字段 → 对应 *_prompt_en 若存在且无手动 override，就清空，让下次生成按新字段重拼。
_FIRST_FRAME_FIELDS = frozenset({
    "description", "characters", "shot_size", "camera_angle", "lighting",
    "composition", "important_details", "required_elements", "opening_state",
    # 接力来源决定开场画面从哪来（inherit 用来源末帧，detach 后回退本镜生成），
    # 改锚/解除同样让旧的首帧 prompt 不再代表新语义。
    "source_shot_id",
})
_LAST_FRAME_FIELDS = frozenset({
    "motion", "frame_change", "last_frame_description", "end_state",
    "add_elements", "remove_elements", "end_composition",
    "preserve_elements", "preserve_custom", "characters",
})
_VIDEO_FIELDS = frozenset({
    "motion", "camera", "camera_movement", "motion_pace",
    "environment_motion", "action_sequence", "transition_description",
    "video_preserve_elements", "video_preserve_custom", "characters",
    "performance_intent",   # C2 22.md §13：表演意图进视频编译——变更使旧 prompt 失效
})


def invalidate_stale_prompts(shot, changed: list[str]) -> dict:
    """改了影响画面的字段后，让旧的自动英文 prompt 失效（下次生成按新字段重拼）。
    - 有手动 *_prompt_override 的：不清（用户刻意手动覆盖），标记 manual_override。
    - 有 *_prompt_en 的：清空，标记 cleared（旧 prompt 不再代表新字段）。
    - 都没有：无需处理（本就走字段拼接）。
    不删已生成的图片/视频文件（用户可能要对比）。返回 {first_frame,last_frame,video} 的动作。
    （37.md §1.1：公开为正式 mutation receipt 入口——REST PUT 与 agent 工具共用，
    不再只服务 update_shot_fields 一条路。）"""
    actions = {}
    changed_set = set(changed)
    mapping = [
        # 首帧自动 prompt 字段是 prompt_en（兼容字段），无 first_frame_prompt_en
        ("first_frame", _FIRST_FRAME_FIELDS,
         "prompt_en", "first_frame_prompt_override",
         "first_frame_prompt_sig"),
        ("last_frame", _LAST_FRAME_FIELDS,
         "last_frame_prompt_en", "last_frame_prompt_override",
         "last_frame_prompt_sig"),
        ("video", _VIDEO_FIELDS,
         "video_prompt_en", "video_prompt_override", "video_prompt_sig"),
    ]
    for label, field_set, en_attr, ov_attr, sig_attr in mapping:
        if not (changed_set & field_set):
            continue
        if getattr(shot, ov_attr, None):
            actions[label] = "manual_override"
            continue
        if getattr(shot, en_attr, None):
            setattr(shot, en_attr, "")
            setattr(shot, sig_attr, "")
            actions[label] = "cleared"
        elif getattr(shot, sig_attr, None):
            setattr(shot, sig_attr, "")
            actions.setdefault(label, "cleared")
    return actions


# 兼容旧名（tests/内部调用沿用私有名）。
_invalidate_stale_prompts = invalidate_stale_prompts


def update_shot_relay(project: Project, shot_id: str, source_shot_id) -> dict:
    """设置/解除镜头的接力来源（source_shot_id）。只改内存，调用方负责 save。

    接力是结构关系字段（谁的末帧是我的开场），不属于内容字段白名单
    （update_shot_fields），单独走本函数并做完整结构校验：
      - source_shot_id 为 None/空 → 解除接力（同时清 source_version；opening_source
        保留——生成侧以「opening_source==inherit 且 source_shot_id 非空」双条件分支，
        悬空 opening_source 无害，与 delete_service 幸存镜头的处理同口径）；
      - 设置时校验：来源存在、同段（接力按构造只在段内）、非自身、沿链无环、
        且该镜头当前紧跟来源之后（接力语义 = 上一镜的末帧；不相邻先 move——
        与 insert_shots_after 的接力约束同一口径）。
    返回 {shot_id, source_shot_id, changed}。校验失败抛 StructureError。"""
    shot = project.get_shot(shot_id)
    if shot is None:
        raise StructureError("not_found", f"镜头 {shot_id} 不存在")
    src = str(source_shot_id).strip() if source_shot_id else ""
    if not src:
        if shot.source_shot_id:
            shot.source_shot_id = None
            shot.source_version = None
            return {"shot_id": shot_id, "source_shot_id": None, "changed": True}
        return {"shot_id": shot_id, "source_shot_id": None, "changed": False}
    if src == shot_id:
        raise StructureError("invalid", "接力来源不能是镜头自身")
    src_shot = project.get_shot(src)
    if src_shot is None:
        raise StructureError("not_found", f"接力来源镜头不存在: {src}")
    if src_shot.scene_id != shot.scene_id:
        raise StructureError(
            "invalid", f"接力来源 {src} 不在同一故事段（接力按构造只在段内）")
    # 环检测：从来源沿链上溯，回到自身即成环
    seen = {shot_id}
    cur = src_shot
    while cur is not None and cur.source_shot_id:
        if cur.source_shot_id in seen:
            raise StructureError("invalid", "接力来源成环（A接力B、B又接力A）")
        seen.add(cur.source_shot_id)
        cur = project.get_shot(cur.source_shot_id)
    # 相邻性：接力镜头必须紧跟来源之后（insert 的接力约束同口径；diff/调用方先 move）
    scene = next((s for s in project.scenes if s.scene_id == shot.scene_id), None)
    ids = [sh.shot_id for sh in (scene.shots if scene else [])]
    if ids.index(src) + 1 != ids.index(shot_id):
        raise StructureError(
            "invalid",
            f"接力镜头必须紧跟来源 {src} 之后（当前不相邻）——请先用 shot.move 把"
            f" {shot_id} 移到 {src} 之后，再设置接力来源")
    changed = shot.source_shot_id != src
    if changed:
        shot.source_shot_id = src
        shot.source_version = None   # 换锚后旧版本引用失效
    return {"shot_id": shot_id, "source_shot_id": src, "changed": changed}


def update_shot_fields(project: Project, shot_id: str, fields: Optional[dict] = None,
                       **kwargs) -> dict:
    """原地改镜头字段。只改白名单内且调用方提供（非 None）的字段。
    - fields: 字段字典（批量/结构化更新用）；**kwargs 兼容旧调用（description/characters/
      target_duration_s）。两者合并，kwargs 优先。
    - duration 非法/≤0 兜底 5；characters/list 字段拷贝成 list。
    - 改了影响首帧/尾帧/视频的字段时，自动让旧的自动 prompt 失效（见 _invalidate_stale_prompts），
      但保留手动 override，不删已生成资产。
    - 未知字段直接忽略（不报错，避免 LLM 多吐字段导致整轮失败）。
    返回 {shot_id, changed:[...], prompts_invalidated:{...}}。"""
    shot = project.get_shot(shot_id)
    if shot is None:
        raise StructureError("not_found", f"镜头 {shot_id} 不存在")
    merged: dict = {}
    if fields:
        merged.update(fields)
    merged.update({k: v for k, v in kwargs.items() if v is not None})

    changed = []
    for key, val in merged.items():
        if key not in _SHOT_WRITABLE_FIELDS or val is None:
            continue
        if key == "target_duration_s":
            setattr(shot, key, _coerce_duration(val))
        elif key == "performance_intent":
            # C2 22.md §13：dict → PerformanceIntent（{} = 清空为未表达）
            from src.core.models import PerformanceIntent
            setattr(shot, key,
                    PerformanceIntent.from_dict(val) if isinstance(val, dict) else None)
        elif key == "link_mode":
            # Phase D §25.C：尾帧生成需 keyframes 模式；34.md §13 增 multi（单参考
            # 直生，仅 BOUND 可用——handler/video_adapter fail closed 把关）。
            # 非法值忽略。自管 changed（合法才追加），不落到链尾的统一 append。
            if str(val) in ("i2v", "keyframes", "multi"):
                setattr(shot, key, str(val))
                changed.append(key)
            continue
        elif key == "opening_source":
            # Phase56 B：接力声明通道——合法值白名单（非法值=显式 StructureError）。
            # independent 同时解除接力锚（与 REST opening-source 同口径）；
            # reference/inherit 的锚由 source_shot_id（update_shot_relay）管理，
            # 生成侧双条件+preflight dependency_ready 兜底（来源未就绪=提交前拦截）。
            from src.core.models import OpeningSource
            vals = {m.value for m in OpeningSource}
            if str(val) not in vals:
                raise StructureError(
                    "invalid_opening_source",
                    f"非法 opening_source: {val}（合法值：{sorted(vals)}）")
            setattr(shot, key, str(val))
            changed.append(key)
            if str(val) == OpeningSource.INDEPENDENT.value:
                shot.source_shot_id = None
                shot.source_version = None
            continue
        elif key == "opening_cut_reason":
            # Phase56 A2 v2：independent 切镜依据——合法值白名单（非法值=显式
            # StructureError，不静默忽略：v2 提交闸拒绝未声明镜，错误值必须
            # 在声明时即暴露）。
            from src.generation.video_contract import OPENING_CUT_REASONS
            if str(val) not in OPENING_CUT_REASONS:
                raise StructureError(
                    "invalid_cut_reason",
                    f"非法 opening_cut_reason: {val}（合法值：{OPENING_CUT_REASONS}）")
            setattr(shot, key, str(val))
            changed.append(key)
            continue
        elif key in ("characters", "required_elements", "preserve_elements",
                     "video_preserve_elements"):
            setattr(shot, key, list(val) if isinstance(val, (list, tuple)) else [val])
        else:
            setattr(shot, key, str(val))
        changed.append(key)
    prompts_invalidated = _invalidate_stale_prompts(shot, changed) if changed else {}
    return {"shot_id": shot_id, "changed": changed,
            "prompts_invalidated": prompts_invalidated}


def update_shots_batch(project: Project, scene_id: str,
                       shots: list[dict]) -> dict:
    """批量更新一个段内的多个镜头（每个 shot 带 shot_id + 要改的字段）。
    段不存在 / 某镜头不在该段 → StructureError(not_found)。全部在内存改完后返回，调用方 save。
    返回 {scene_id, results:[...], count, prompts_invalidated:{shot_id: actions}}。"""
    scene = next((s for s in project.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise StructureError("not_found", f"故事段 {scene_id} 不存在")
    in_scene = {sh.shot_id for sh in scene.shots}
    results = []
    invalidated = {}
    for item in shots or []:
        if not isinstance(item, dict):
            continue
        sid = item.get("shot_id")
        if not sid or sid not in in_scene:
            raise StructureError("not_found",
                                 f"镜头 {sid} 不在故事段 {scene_id}")
        fields = {k: v for k, v in item.items() if k != "shot_id"}
        r = update_shot_fields(project, sid, fields)
        results.append(r)
        if r.get("prompts_invalidated"):
            invalidated[sid] = r["prompts_invalidated"]
    return {"scene_id": scene_id, "results": results,
            "count": len(results), "prompts_invalidated": invalidated}


# ============ 只读查询（供 agent 主动读取完整参数）============
# 镜头创作字段（不含产物/批次/内部签名/prompt_en 等内部字段），给 agent 看的完整视图。
_SHOT_READ_FIELDS = (
    "shot_id", "scene_id", "index", "description", "characters", "dialogue",
    "target_duration_s", "shot_size", "camera", "camera_angle", "camera_movement",
    "composition", "lighting", "motion", "motion_pace", "environment_motion",
    "action_sequence", "opening_state", "important_details", "required_elements",
    "last_frame_description", "end_state", "frame_change", "transition_description",
    "opening_source", "source_shot_id", "link_mode", "note",
)


def read_shot(project: Project, shot_id: str) -> dict:
    """返回一个镜头的完整创作参数（白名单字段，不含产物/内部字段）。镜头不存在抛 not_found。"""
    shot = project.get_shot(shot_id)
    if shot is None:
        raise StructureError("not_found", f"镜头 {shot_id} 不存在")
    return {k: getattr(shot, k) for k in _SHOT_READ_FIELDS if hasattr(shot, k)}


def read_scene(project: Project, scene_id: str) -> dict:
    """返回一个故事段及其镜头的完整创作参数（不含产物）。段不存在抛 not_found。"""
    scene = next((s for s in project.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise StructureError("not_found", f"故事段 {scene_id} 不存在")
    return {
        "scene_id": scene.scene_id,
        "summary": scene.summary,
        "characters": list(scene.characters),
        "shots": [read_shot(project, sh.shot_id) for sh in scene.shots],
    }


def read_script(project: Project) -> dict:
    """返回剧本正文（完整、实时）。供 Director 等不拥有剧本的 agent 按需读取，
    避免把长剧本整段塞进 system prompt。"""
    text = project.story_text or ""
    return {"length": len(text), "text": text}


def list_scenes(project: Project) -> dict:
    """列出所有故事段及其镜头的结构化信息（比系统目录更完整的机器可读视图）。
    供 agent 在做多段对比/增删建议时一次性获取全貌。不含产物字段。"""
    return {
        "count": len(project.scenes),
        "scenes": [
            {"scene_id": s.scene_id, "summary": s.summary,
             "characters": list(s.characters),
             "shot_count": len(s.shots),
             "shots": [{"shot_id": sh.shot_id, "index": sh.index,
                        "description": sh.description,
                        "characters": list(sh.characters),
                        "target_duration_s": sh.target_duration_s,
                        "opening_source": sh.opening_source,
                        "source_shot_id": sh.source_shot_id}
                       for sh in s.shots]}
            for s in project.scenes
        ],
    }
