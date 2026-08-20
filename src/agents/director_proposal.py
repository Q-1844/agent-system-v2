# src/agents/director_proposal.py
# AI-6D Director Proposal Runtime —— 语义意图层 + 确定性编译器 + 编排（§3/§6/§7/§9/§10/§13/§17）。
#
# 不变量（§2/§21）：LLM 只产出创作意图；before/revision/signature/stable shot_id/
# changeset_id/workspace_id 全部由确定性服务端代码产出。无 Agent 代码直写 canonical。
#
# 流程（§11）：
#   read canonical context → call Director(Agnes) → parse+validate(+1 repair) →
#   if valid: create Director Workspace → compile items → cs_engine.preview → persist Proposal.
#   if invalid/Agnes 失败: 返回 proposal-generation failure，不留 workspace、不动 canonical。
#
# 选择性应用（§13/§14）：preview_selected 重新编译“被选中的 item 子集”→ cs_engine.preview →
# 全新 ChangeSet #2；apply 复用 cs_engine.apply（按 changeset_id，§13）。
#
# Import 纪律：只 import src.core.* / src.revision.* / 本包 models。不 import runtime/router。
#   ws_store / proposal_store / agnes client 均由调用方（AgentRuntime）注入——本模块只编排。
from __future__ import annotations
import json
import re
import secrets
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, List, Optional, Tuple

from ..core.models import Project, Scene, Shot
from ..revision.models import (ChangeOperation, BeforeAfter, NewShotSpec, OpType as OT)
from ..revision import changeset as cs_engine
from ..revision.workspace import WorkspaceStore
from ..revision.changeset import ChangeSetError


# ============ 枚举 ============
class ProposalAction(str, Enum):
    UPDATE_SHOT = "update_shot"
    INSERT_SHOT = "insert_shot"
    DELETE_SHOT = "delete_shot"
    REORDER_SHOT = "reorder_shot"        # 语义层单数；编译为 OpType.REORDER_SHOTS（复数）


class ProposalStatus(str, Enum):
    CREATED = "created"                  # full preview 已生成，待用户选择/应用
    SELECTED_PREVIEWED = "selected_previewed"  # 子集已再预览（§13）
    APPLIED = "applied"
    DISMISSED = "dismissed"
    STALE = "stale"                      # workspace revision 已变（§17）


class ProposalScope(str, Enum):
    SHOT = "shot"
    SCENE = "scene"


class ProposalValidationError(Exception):
    """LLM 输出解析/校验失败（§9）。message 用于一次 repair 提示。"""

    def __init__(self, message: str, raw: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.raw = raw


class ProposalGenerationError(Exception):
    """提案生成失败（Agnes 不可用 / 校验失败 / 编译失败）。router 映射 HTTP。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ============ 语义模型 ============
@dataclass
class DirectorProposalItem:
    """一个语义修改意图（≠ 一个 ChangeOperation；§7 一个 item 可编译为多个 op）。
    item_id 服务端生成，LLM 不发明（§6）。shot_id 是被操作的目标镜头（insert 时为空，
    服务端 allocate）。after_shot_id 是 insert/reorder 的目标位置（插在谁后面/移到谁后面，
    None=末尾/开头）。characters/description/target_duration_s 是新值（before 由编译器读 working）。"""
    item_id: str
    action: str                              # ProposalAction 值
    scene_id: str = ""
    shot_id: Optional[str] = None             # update/delete/reorder 目标；insert 无
    description: Optional[str] = None
    characters: Optional[List[str]] = None
    target_duration_s: Optional[float] = None
    after_shot_id: Optional[str] = None       # insert/reorder 位置锚点
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id, "action": self.action, "scene_id": self.scene_id,
            "shot_id": self.shot_id, "description": self.description,
            "characters": list(self.characters) if self.characters is not None else None,
            "target_duration_s": self.target_duration_s,
            "after_shot_id": self.after_shot_id, "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DirectorProposalItem":
        d = d or {}
        return cls(
            item_id=d.get("item_id", ""), action=d.get("action", ""),
            scene_id=d.get("scene_id", ""), shot_id=d.get("shot_id"),
            description=d.get("description"),
            characters=list(d.get("characters")) if d.get("characters") is not None else None,
            target_duration_s=d.get("target_duration_s"),
            after_shot_id=d.get("after_shot_id"),
            reason=d.get("reason", ""),
        )


@dataclass
class DirectorProposalIntent:
    """一次 Director 修改意图（LLM 产出 + 服务端校验后）。title/summary 给 UI 看。"""
    title: str = ""
    summary: str = ""
    items: List[DirectorProposalItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"title": self.title, "summary": self.summary,
                "items": [i.to_dict() for i in self.items]}

    @classmethod
    def from_dict(cls, d: dict) -> "DirectorProposalIntent":
        d = d or {}
        return cls(title=d.get("title", ""), summary=d.get("summary", ""),
                   items=[DirectorProposalItem.from_dict(i) for i in d.get("items", [])])


@dataclass
class DirectorProposal:
    """一个 Director Proposal 的持久化形态（§10）。base_is_stale 动态判断不持久化。"""
    proposal_id: str
    project: str
    agent_id: str = "director"
    scope_type: str = ProposalScope.SCENE.value
    scope_id: str = ""                       # scene scope=scene_id；shot scope=shot_id
    instruction: str = ""
    title: str = ""
    summary: str = ""
    items: List[DirectorProposalItem] = field(default_factory=list)
    workspace_id: str = ""
    base_workspace_revision: int = 0
    full_preview_changeset_id: str = ""
    selected_preview_changeset_id: Optional[str] = None
    applied_changeset_id: Optional[str] = None
    # 完整预览结果（含 diff/impact/warnings/changeset_id）——持久化以便刷新恢复（§16）。
    # changeset.json 只存 operations+impact_report+signatures，不存 diff/warnings，故预览结果随 Proposal 存。
    full_preview: Optional[dict] = None
    selected_preview: Optional[dict] = None
    status: str = ProposalStatus.CREATED.value
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id, "project": self.project,
            "agent_id": self.agent_id, "scope_type": self.scope_type,
            "scope_id": self.scope_id, "instruction": self.instruction,
            "title": self.title, "summary": self.summary,
            "items": [i.to_dict() for i in self.items],
            "workspace_id": self.workspace_id,
            "base_workspace_revision": self.base_workspace_revision,
            "full_preview_changeset_id": self.full_preview_changeset_id,
            "selected_preview_changeset_id": self.selected_preview_changeset_id,
            "applied_changeset_id": self.applied_changeset_id,
            "full_preview": self.full_preview,
            "selected_preview": self.selected_preview,
            "status": self.status, "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DirectorProposal":
        d = d or {}
        return cls(
            proposal_id=d.get("proposal_id", ""), project=d.get("project", ""),
            agent_id=d.get("agent_id", "director"),
            scope_type=d.get("scope_type", ProposalScope.SCENE.value),
            scope_id=d.get("scope_id", ""), instruction=d.get("instruction", ""),
            title=d.get("title", ""), summary=d.get("summary", ""),
            items=[DirectorProposalItem.from_dict(i) for i in d.get("items", [])],
            workspace_id=d.get("workspace_id", ""),
            base_workspace_revision=d.get("base_workspace_revision", 0),
            full_preview_changeset_id=d.get("full_preview_changeset_id", ""),
            selected_preview_changeset_id=d.get("selected_preview_changeset_id"),
            applied_changeset_id=d.get("applied_changeset_id"),
            full_preview=d.get("full_preview"),
            selected_preview=d.get("selected_preview"),
            status=d.get("status", ProposalStatus.CREATED.value),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
        )


# ============ 作用域解析 ============
def _scene_of(project: Project, scene_id: str) -> Optional[Scene]:
    return next((s for s in project.scenes if s.scene_id == scene_id), None)


def resolve_scope_scene(project: Project, scope_type: str, scope_id: str) -> Scene:
    """scene scope -> 该 scene；shot scope -> shot 所在 scene。不存在抛 ValueError。"""
    if scope_type == ProposalScope.SCENE.value:
        sc = _scene_of(project, scope_id)
        if sc is None:
            raise ValueError(f"故事段 {scope_id} 不存在")
        return sc
    if scope_type == ProposalScope.SHOT.value:
        sh = project.get_shot(scope_id)
        if sh is None:
            raise ValueError(f"镜头 {scope_id} 不存在")
        sc = _scene_of(project, sh.scene_id)
        if sc is None:
            raise ValueError(f"镜头 {scope_id} 所属故事段不存在")
        return sc
    raise ValueError(f"未知 scope_type: {scope_type}")


# ============ Director 上下文（§5：有用且最小，不 dump 整个项目）============
def _shot_ctx(sh: Shot) -> dict:
    """单个镜头的最小可编辑 + 生成态字段（§5）。不含 base64/URL/批次/签名。"""
    return {
        "shot_id": sh.shot_id,
        "index": sh.index,
        "description": sh.description,
        "characters": list(sh.characters),
        "target_duration_s": sh.target_duration_s,
        "status": sh.status,
        "first_frame_status": sh.first_frame.status,
        "video_status": sh.video.status,
        "has_final_video": bool(sh.final_video),
    }


def build_director_context(project: Project, scope_type: str, scope_id: str) -> dict:
    """构建 Director 分镜上下文（§5）。shot scope 聚焦选中镜头+邻接；scene scope 全段。"""
    sc = resolve_scope_scene(project, scope_type, scope_id)
    shot_ids = [sh.shot_id for sh in sc.shots]
    ctx = {
        "project": project.name,
        "style": {
            "art_style": project.style.art_style,
            "palette": project.style.palette,
            "aspect_ratio": project.style.aspect_ratio,
            "era": project.style.era,
        },
        "scope": {"type": scope_type, "id": scope_id, "scene_id": sc.scene_id},
        "scene": {
            "scene_id": sc.scene_id,
            "title": sc.title,
            "summary": sc.summary,
            "characters": list(sc.characters),
            "shot_order": shot_ids,
        },
        "shots": [_shot_ctx(sh) for sh in sc.shots],
    }
    if scope_type == ProposalScope.SHOT.value:
        idx = shot_ids.index(scope_id) if scope_id in shot_ids else -1
        ctx["selected_shot"] = scope_id
        ctx["neighbors"] = {
            "prev": shot_ids[idx - 1] if idx > 0 else None,
            "next": shot_ids[idx + 1] if 0 <= idx < len(shot_ids) - 1 else None,
        }
    return ctx


def _render_context_text(ctx: dict) -> str:
    """把上下文 dict 渲染成给 LLM 看的紧凑文本。"""
    sc = ctx["scene"]
    lines = [
        f"# 当前上下文",
        f"project: {ctx['project']}",
        f"视觉风格: {ctx['style']['art_style']} / 配色 {ctx['style']['palette']} / 比例 {ctx['style']['aspect_ratio']}",
        f"作用域: {ctx['scope']['type']}:{ctx['scope']['id']}（故事段 {sc['scene_id']}）",
        f"故事段 {sc['scene_id']}「{sc['title'] or sc['summary'][:24]}」: {sc['summary']}",
        f"出场角色: {('、'.join(sc['characters'])) if sc['characters'] else '无'}",
        f"当前镜头顺序: {' → '.join(sc['shot_order']) if sc['shot_order'] else '（无镜头）'}",
    ]
    if ctx.get("selected_shot"):
        nb = ctx.get("neighbors") or {}
        lines.append(f"选中镜头: {ctx['selected_shot']}（前接 {nb.get('prev')} / 后接 {nb.get('next')}）")
    lines.append("# 现有镜头（可编辑创作字段 + 生成态）")
    for sh in ctx["shots"]:
        lines.append(
            f"  - {sh['shot_id']} (#{sh['index']}): {sh['description']} | 角色 {sh['characters']} | "
            f"时长 {sh['target_duration_s']}s | 状态 {sh['status']} | 首帧 {sh['first_frame_status']} | 视频 {sh['video_status']}")
    return "\n".join(lines)


# ============ LLM schema / messages ============
DIRECTOR_PROPOSAL_SYSTEM = """你是 AGNES 的 Director，负责在一个故事段内重设计分镜（镜头级编辑）。

🔴 红线：你只表达"创作意图"，绝不生成 before 值、不发明稳定 shot_id、不输出 revision/签名/changeset_id/workspace_id。这些由后端确定性代码产出。你不直接修改正式项目——所有修改经 Workspace/ChangeSet。

可提出四种语义动作（只限当前故事段内，不得跨故事段）：
- update_shot：修改已有镜头的创作字段（description / characters / target_duration_s）。需给出 shot_id 与新值。
- insert_shot：在该故事段插入一个新镜头。给 scene_id、位置锚点 after_shot_id（插在哪个现有镜头之后；省略或为 null 表示加到末尾）、新镜头的 description/characters/target_duration_s。🔴 不要给新镜头编 shot_id——后端会分配。
- delete_shot：删除一个已有镜头。给 shot_id。
- reorder_shot：把一个已有镜头移到 after_shot_id 之后（省略/null 表示移到段首）。给 shot_id 与 after_shot_id。

约束：
- characters 只能选自当前故事段的出场角色。
- 不得对同一镜头同时 delete 与 update/reorder（矛盾）。
- 一个语义动作可以对应后端的多个技术操作（例如"在镜头 02 后插入反应镜头"会被后端编译成 INSERT + REORDER），你只管给出语义意图。
- 保持创作合理：description 用中文、具体动作/状态/变化；target_duration_s 取 3/5/8/10 附近的合理秒数。

只返回一个 JSON 对象，不要任何解释文字、不要代码块标记：
{
  "title": "本次修改的短标题",
  "summary": "一句话说明这次修改的创作意图",
  "items": [
    {"action": "update_shot", "shot_id": "...", "description": "...", "characters": ["..."], "target_duration_s": 3, "reason": "..."},
    {"action": "insert_shot", "scene_id": "...", "after_shot_id": "...", "description": "...", "characters": ["..."], "target_duration_s": 2, "reason": "..."},
    {"action": "delete_shot", "shot_id": "...", "reason": "..."},
    {"action": "reorder_shot", "shot_id": "...", "after_shot_id": "...", "reason": "..."}
  ]
}
items 可为空数组（表示不需要改动），但通常至少 1 项。"""


def build_proposal_messages(system_prompt: str, ctx: dict, instruction: str) -> List[dict]:
    user = (
        f"{_render_context_text(ctx)}\n\n"
        f"# 用户修改指令\n{instruction.strip() or '请基于当前分镜提出合理的修改意图。'}\n\n"
        f"请据此只返回一个 JSON 对象。"
    )
    return [{"role": "system", "content": system_prompt},
            {"role": "user", "content": user}]


# ============ 解析 + 校验（§9）============
def _parse_json(raw: str) -> dict:
    """json.loads + 去 ``` 代码块（chat(json_mode=True) 已基本清理，双保险）。"""
    if raw is None:
        raise ProposalValidationError("AI 返回为空", raw)
    text = raw.strip()
    if not text:
        raise ProposalValidationError("AI 返回为空", raw)
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
    try:
        obj = json.loads(text)
    except Exception as e:
        raise ProposalValidationError(f"无法解析为 JSON：{e}", raw)
    if not isinstance(obj, dict):
        raise ProposalValidationError("AI 返回不是 JSON 对象", raw)
    return obj


_VALID_ACTIONS = {a.value for a in ProposalAction}


def _existing_shot_ids(project: Project, scene: Scene) -> set:
    return {sh.shot_id for sh in scene.shots}


def parse_and_validate(raw: str, project: Project,
                       scope_type: str, scope_id: str) -> DirectorProposalIntent:
    """解析 + 校验 LLM 输出（§9）。校验针对 Director 看到的项目（canonical，== 创建时 base）。
    服务端为每个 item 分配 item_id（§6）。失败抛 ProposalValidationError。"""
    obj = _parse_json(raw)
    title = str(obj.get("title") or "").strip()
    summary = str(obj.get("summary") or "").strip()
    raw_items = obj.get("items") or []
    if not isinstance(raw_items, list):
        raise ProposalValidationError("items 不是数组", raw)

    sc = resolve_scope_scene(project, scope_type, scope_id)   # 不存在会抛 ValueError -> 转 ProposalValidationError
    cast = set(sc.characters)
    existing = _existing_shot_ids(project, sc)

    items: List[DirectorProposalItem] = []
    targeted: dict = {}   # shot_id -> set(action)，用于矛盾检测
    for i, ri in enumerate(raw_items, start=1):
        if not isinstance(ri, dict):
            raise ProposalValidationError(f"第 {i} 个 item 不是对象", raw)
        action = str(ri.get("action") or "").strip()
        if action not in _VALID_ACTIONS:
            raise ProposalValidationError(f"第 {i} 个 item action 非法: {action!r}", raw)

        # 公共字段
        scene_id = str(ri.get("scene_id") or sc.scene_id).strip()
        if scene_id and scene_id != sc.scene_id:
            raise ProposalValidationError(
                f"第 {i} 个 item 跨故事段: scene_id={scene_id}（只允许 {sc.scene_id}）", raw)
        reason = str(ri.get("reason") or "").strip()

        # characters：过滤掉不在 cast 的（安全：不让非法角色进 changeset）
        raw_chars = ri.get("characters")
        chars = [str(c).strip() for c in raw_chars if str(c).strip()] if raw_chars is not None else None
        if chars is not None:
            chars = [c for c in chars if c in cast or not cast]   # 无 cast 的故事段放行
        # target_duration_s
        dur = ri.get("target_duration_s")
        if dur is not None:
            try:
                dur = float(dur)
                if dur <= 0:
                    raise ValueError()
            except Exception:
                raise ProposalValidationError(f"第 {i} 个 item target_duration_s 非法: {dur!r}", raw)
        description = str(ri.get("description") or "").strip() or None
        shot_id = str(ri.get("shot_id") or "").strip() or None
        after_shot_id = str(ri.get("after_shot_id") or "").strip() or None

        # action 专属校验
        if action == ProposalAction.INSERT_SHOT.value:
            # 🔴 LLM 不得发明 shot_id；忽略其提供的（服务端 allocate）
            shot_id = None
            if not description:
                raise ProposalValidationError(f"第 {i} 个 insert_shot 缺少 description", raw)
            # after_shot_id 若给则必须指向段内现有镜头
            if after_shot_id and after_shot_id not in existing:
                raise ProposalValidationError(
                    f"第 {i} 个 insert_shot after_shot_id 不存在: {after_shot_id}", raw)
        else:
            # update/delete/reorder 必须有合法 shot_id 且属于本段
            if not shot_id:
                raise ProposalValidationError(f"第 {i} 个 {action} 缺少 shot_id", raw)
            if shot_id not in existing:
                raise ProposalValidationError(
                    f"第 {i} 个 {action} 的 shot_id 不在故事段内: {shot_id}", raw)
            if action == ProposalAction.UPDATE_SHOT.value and not any(
                    v is not None for v in (description, chars, dur)):
                raise ProposalValidationError(
                    f"第 {i} 个 update_shot 未提供任何新字段", raw)
            if action == ProposalAction.REORDER_SHOT.value and after_shot_id:
                if after_shot_id not in existing:
                    raise ProposalValidationError(
                        f"第 {i} 个 reorder_shot after_shot_id 不存在: {after_shot_id}", raw)
            # 矛盾检测：同一 shot 不得同时 delete 与 update/reorder
            acts = targeted.setdefault(shot_id, set())
            acts.add(action)
            if ProposalAction.DELETE_SHOT.value in acts and len(acts) > 1:
                raise ProposalValidationError(
                    f"镜头 {shot_id} 同时被 delete 与其它动作操作（矛盾）", raw)

        items.append(DirectorProposalItem(
            item_id="item_" + secrets.token_hex(4), action=action, scene_id=sc.scene_id,
            shot_id=shot_id, description=description, characters=chars,
            target_duration_s=dur, after_shot_id=after_shot_id, reason=reason))

    if not items:
        raise ProposalValidationError("proposal 为空（没有任何修改意图）", raw)

    # 位置锚点不得指向被删镜头（§9 矛盾：after_shot_id 会被删掉）
    delete_targets = {it.shot_id for it in items if it.action == ProposalAction.DELETE_SHOT.value}
    for it in items:
        if it.after_shot_id and it.after_shot_id in delete_targets:
            raise ProposalValidationError(
                f"位置锚点 {it.after_shot_id} 被同一提案删除（矛盾）", raw)

    return DirectorProposalIntent(title=title or "Director 修改提案", summary=summary, items=items)


# ============ 确定性编译器（§7/§13）============
def _move_after(order: List[str], moving_id: str, anchor_id: Optional[str]) -> List[str]:
    """把 moving_id 从 order 移到 anchor_id 之后；anchor None/空=移到段首。返回新列表。"""
    if moving_id not in order:
        return order
    order = [x for x in order if x != moving_id]
    if not anchor_id:
        order.insert(0, moving_id)
    else:
        try:
            idx = order.index(anchor_id)
        except ValueError:
            order.append(moving_id)   # anchor 不在（不应发生），兜底末尾
        else:
            order.insert(idx + 1, moving_id)
    return order


def compile_items_pure(items: List["DirectorProposalItem"], working: Project,
                        alloc_fn: Callable[[str], str]) -> List[ChangeOperation]:
    """语义 items → ChangeOperation[]（§7/§13）。纯函数、可对任意子集重跑。
    before 读 working（绝不取自 LLM）；insert 由 alloc_fn(scene_id) 分配稳定 id；
    带位置的 insert/reorder 编译为额外 REORDER_SHOTS（§7：一个 item 可对应多个 op）。
    §21/§62：抽出纯版本——AI-7B ProposalProjection 复用，传纯内存 allocator，不需 ws_store。"""
    ops: List[ChangeOperation] = []
    positional: dict = {}     # scene_id -> list[(moving_id, anchor_id)]
    deletes: dict = {}        # scene_id -> set(shot_id)

    for item in items:
        if item.action == ProposalAction.UPDATE_SHOT.value:
            shot = working.get_shot(item.shot_id)
            if shot is None:
                raise ProposalGenerationError("compile_error", f"update 目标镜头不存在: {item.shot_id}")
            desc_ba = chars_ba = dur_ba = None
            if item.description is not None:
                desc_ba = BeforeAfter(before=shot.description, after=item.description)
            if item.characters is not None:
                chars_ba = BeforeAfter(before=list(shot.characters), after=list(item.characters))
            if item.target_duration_s is not None:
                dur_ba = BeforeAfter(before=shot.target_duration_s, after=item.target_duration_s)
            ops.append(ChangeOperation(
                op_type=OT.UPDATE_SHOT.value, scene_id=item.scene_id, shot_id=item.shot_id,
                description=desc_ba, characters=chars_ba, target_duration_s=dur_ba))

        elif item.action == ProposalAction.DELETE_SHOT.value:
            deletes.setdefault(item.scene_id, set()).add(item.shot_id)
            ops.append(ChangeOperation(
                op_type=OT.DELETE_SHOT.value, scene_id=item.scene_id, shot_id=item.shot_id))

        elif item.action == ProposalAction.INSERT_SHOT.value:
            # 服务端分配稳定 shot_id（§2/§11）；preview/apply 复用同一 id
            new_id = alloc_fn(item.scene_id)
            ns = NewShotSpec(shot_id=new_id, description=item.description or "",
                             characters=list(item.characters or []),
                             target_duration_s=item.target_duration_s)
            ops.append(ChangeOperation(
                op_type=OT.INSERT_SHOT.value, scene_id=item.scene_id, new_shot=ns))
            if item.after_shot_id is not None:
                positional.setdefault(item.scene_id, []).append((new_id, item.after_shot_id))

        elif item.action == ProposalAction.REORDER_SHOT.value:
            positional.setdefault(item.scene_id, []).append(
                (item.shot_id, item.after_shot_id))

    for scene_id, moves in positional.items():
        scene = _scene_of(working, scene_id)
        if scene is None:
            continue
        del_set = deletes.get(scene_id, set())
        original_kept = [sh.shot_id for sh in scene.shots if sh.shot_id not in del_set]
        appended = [op.new_shot.shot_id for op in ops
                    if op.op_type == OT.INSERT_SHOT.value and op.scene_id == scene_id]
        natural = original_kept + appended
        final = list(natural)
        for moving_id, anchor_id in moves:
            final = _move_after(final, moving_id, anchor_id)
        if final != natural:
            ops.append(ChangeOperation(
                op_type=OT.REORDER_SHOTS.value, scene_id=scene_id,
                ordered_shot_ids=final))
    return ops


def compile_items(items: List[DirectorProposalItem], ws_store: WorkspaceStore,
                  ws, working: Project) -> List[ChangeOperation]:
    """既有 direct Director UI 流程的编译入口（§7/§13）。§21：委托 compile_items_pure，
    注入 ws_store.allocate_shot_id 作为稳定 id 分配器。行为不变。"""
    return compile_items_pure(items, working, lambda scene_id: ws_store.allocate_shot_id(ws, scene_id))


# ============ 编排 ============
def _workspace_name(scene: Scene, intent: DirectorProposalIntent) -> str:
    """§10 人类可读名：Director · {scene_id} · {短标题}。"""
    short = re.sub(r"[\\/:*?\"<>|]", " ", intent.title or "修改").strip()[:24] or "修改"
    return f"Director · {scene.scene_id} · {short}"


def _preview_to_changeset(pv: dict) -> dict:
    """把 cs_engine.preview 返回压成前端 ProposalChangeset 形态。"""
    return {
        "changeset_id": pv.get("changeset_id"),
        "status": pv.get("status"),
        "diff": pv.get("diff", []),
        "impact": pv.get("impact", {}),
        "warnings": pv.get("warnings", []),
        "base_workspace_revision": pv.get("base_workspace_revision"),
        "after_signature": pv.get("after_signature"),
    }


def proposal_to_api(proposal: DirectorProposal, base_is_stale: bool,
                    preview: Optional[dict] = None) -> dict:
    """合并 Proposal + 当前预览（full 或 selected）为前端 currentProposal 形态。
    preview 显式传入优先；否则取 proposal.selected_preview / full_preview（刷新恢复用，§16）。"""
    d = proposal.to_dict()
    cur = preview if preview is not None else (proposal.selected_preview or proposal.full_preview)
    d["base_is_stale"] = base_is_stale
    d["changeset"] = _preview_to_changeset(cur) if cur else None
    return d


async def generate_semantic_proposal(client, project_loader: Callable[[], Project],
                                     scope_type: str, scope_id: str,
                                     instruction: str) -> DirectorProposalIntent:
    """§21：LLM → 语义 DirectorProposalIntent（**不建 workspace / 不编译 / 不预览 / 不落盘**）。
    AI-7B Team ChangeSession 仅复用此安全下层（+ compile_items + 自有纯内存预览，绝不走 Apply）；
    既有 direct Director 流程也经此生成意图。提取自 generate_proposal 前半段，行为不变。"""
    if client is None:
        raise ProposalGenerationError("not_configured", "Agnes 推理模型不可用，无法生成提案。")
    project = project_loader()
    try:
        ctx = build_director_context(project, scope_type, scope_id)
    except ValueError as e:
        raise ProposalGenerationError("bad_scope", str(e))
    messages = build_proposal_messages(DIRECTOR_PROPOSAL_SYSTEM, ctx, instruction)

    # 1. 调 LLM
    try:
        raw = await client.chat(messages, json_mode=True, temperature=0.3, max_tokens=2048)
    except Exception as e:
        raise ProposalGenerationError("agnes_failed", f"Agnes 调用失败：{str(e)[:200]}")

    # 2. parse + validate（一次 repair，§102）
    try:
        return parse_and_validate(raw, project, scope_type, scope_id)
    except ProposalValidationError as e:
        repair = messages + [{"role": "system",
                              "content": f"上次返回无法使用：{e.message}。请严格遵循 schema，只返回一个合法 JSON 对象。"}]
        try:
            raw2 = await client.chat(repair, json_mode=True, temperature=0.2, max_tokens=2048)
            return parse_and_validate(raw2, project, scope_type, scope_id)
        except ProposalValidationError as e2:
            raise ProposalGenerationError("invalid_output",
                                          f"Director 输出无法解析/校验：{e2.message}")
        except Exception as e2:
            raise ProposalGenerationError("agnes_failed", f"Agnes repair 调用失败：{str(e2)[:200]}")


async def generate_proposal(client, project_loader: Callable[[], Project],
                            ws_store: WorkspaceStore, proposal_store,
                            scope_type: str, scope_id: str,
                            instruction: str) -> Tuple[DirectorProposal, dict]:
    """生成提案（§11）：generate_semantic_proposal → create workspace →
    compile → cs_engine.preview → persist。失败抛 ProposalGenerationError，不留 workspace。
    §21 重构：语义生成抽到 generate_semantic_proposal；本函数=既有 direct Director UI 流程
    （create workspace + compile + preview + persist Proposal），行为不变。"""
    intent = await generate_semantic_proposal(client, project_loader, scope_type, scope_id, instruction)
    project = project_loader()

    # 创建 Director Workspace（仅在合法后，§11）
    sc = resolve_scope_scene(project, scope_type, scope_id)
    ws = ws_store.create(_workspace_name(sc, intent))
    try:
        working = ws_store.load_working_project(ws.workspace_id)
        operations = compile_items(intent.items, ws_store, ws, working)
        pv = cs_engine.preview(ws_store, ws, intent.title, instruction,
                               ws.revision, operations)
    except ChangeSetError as e:
        # 编译/预览失败：删除刚建的 workspace，不留垃圾（§11）。保留 ChangeSetError code 供 router 映射。
        ws_store.delete(ws.workspace_id)
        raise ProposalGenerationError(e.code, f"[{e.code}] {e.message}")
    except ProposalGenerationError as e:
        ws_store.delete(ws.workspace_id)
        raise

    # 持久化 Proposal（含完整预览结果，刷新恢复用 §16）
    proposal = DirectorProposal(
        proposal_id="", project=project.name, agent_id="director",
        scope_type=scope_type, scope_id=scope_id, instruction=instruction,
        title=intent.title, summary=intent.summary, items=intent.items,
        workspace_id=ws.workspace_id, base_workspace_revision=ws.revision,
        full_preview_changeset_id=pv["changeset_id"], full_preview=pv,
        status=ProposalStatus.CREATED.value,
    )
    proposal = proposal_store.create(proposal)
    return proposal, pv


def preview_selected(proposal: DirectorProposal, selected_item_ids: List[str],
                     ws_store: WorkspaceStore, proposal_store) -> dict:
    """选择性子集再预览（§13/§14）：重编译被选 item → cs_engine.preview → 全新 ChangeSet。
    workspace revision 不匹配 -> stale（§17）。返回 proposal_to_api 形态（含新 changeset）。"""
    ws = ws_store.load(proposal.workspace_id)
    cs_engine.reconcile(ws_store, ws)
    if ws.revision != proposal.base_workspace_revision:
        proposal.status = ProposalStatus.STALE.value
        proposal_store.save(proposal)
        return {"stale": True, "base_workspace_revision": proposal.base_workspace_revision,
                "workspace_revision": ws.revision}
    sel_ids = set(selected_item_ids or [])
    sel = [it for it in proposal.items if it.item_id in sel_ids]
    working = ws_store.load_working_project(proposal.workspace_id)
    operations = compile_items(sel, ws_store, ws, working)
    pv = cs_engine.preview(ws_store, ws, proposal.title, proposal.instruction,
                           ws.revision, operations)
    proposal.selected_preview_changeset_id = pv["changeset_id"]
    proposal.selected_preview = pv
    proposal.status = ProposalStatus.SELECTED_PREVIEWED.value
    proposal_store.save(proposal)
    return proposal_to_api(proposal, ws_store.base_is_stale(ws))


def apply_proposal(proposal: DirectorProposal, changeset_id: str,
                   ws_store: WorkspaceStore, proposal_store) -> dict:
    """应用选中的 ChangeSet（§13：按 changeset_id，复用 cs_engine.apply）。
    cs_engine.apply 已三重校验 revision+before_signature+after_signature（§17）。"""
    ws = ws_store.load(proposal.workspace_id)
    res = cs_engine.apply(ws_store, ws, changeset_id)   # 冲突抛 ChangeSetError
    proposal.applied_changeset_id = changeset_id
    proposal.status = ProposalStatus.APPLIED.value
    proposal_store.save(proposal)
    stale = ws_store.base_is_stale(ws_store.load(proposal.workspace_id))
    return {**res, "base_is_stale": stale,
            "workspace_id": proposal.workspace_id,
            "status": proposal.status}
