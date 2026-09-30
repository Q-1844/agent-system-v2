# src/agents/character_designer.py
# Character Designer 正式 Agent（原生 function-calling tool-loop，2026-08-30）。
#
# 职责：只写角色 canonical 数据（project.json 的 Project.characters：name/description_en）。
#       剧本正文与故事段（story_text/scenes）是它的【参考资料】，不是写入对象。
#       分镜（Shot）、三视图图片资产、AI 设计草稿（character_designs.json）均不写。
#
# 工具（少而明确；读工具直接复用 Script Writer 的只读执行器，不重复实现数据访问层）：
#   读：character.list / character.read / story.list / story.read / script.read / web.search
#   写：character.create / character.update / character.delete
#
# 复用 Script Writer 已验证的基础设施（import 共享，不复制不改其文件）：
#   web.search 执行器 + 预算/重复查询标准化、统一回执骨架 _base_receipt、
#   宽容反序列化 _loose_json/_parse_args、真相闸门正则（_claims_success /
#   _is_explicit_write_request / _is_chat_turn）、【实际执行记录】保留块清除、
#   _TurnStopped 协作式停止。tool-loop 机制（单写串行/回执账本/zero-write recovery/
#   重复失败护栏/超时恢复/流式事件/取消）按角色域镜像实现——不改冻结的 script_writer.py。
#
# 乐观并发（与 Script Writer 同思路，字段级作用域）：
#   - _characters_token：角色列表内容指纹（有序 [{name, description_en}]）。
#     character.create / character.delete 校验 expected_revision。
#   - _character_token：单个角色内容指纹（{name, description_en}）。character.update 校验。
#   views（三视图图片）与其它角色不受影响——Agent 只写 name/description_en 两个 canonical 字段。
#   stale → 拒绝写入（不落盘/不审计），返回最新内容 + 新指纹供 Agent 重新判断。
#   每次写后无条件重读 canonical 验证，receipts 是唯一权威执行事实。
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import httpx

from src.core.models import Character
from src.core.store import ProjectStore

from .models import AgentMessage, ResultStatus
from .project_events import RESOURCE_CHARACTER, RESOURCE_RESOURCES
from .runtime import AgentRuntime, AgentError
from .revision_audit import DirectEditAuditStore
from .character_views_handler import (handler_character_views,
                                      character_appearance_sig)
# —— Script Writer 共享基础设施（直接 import 复用，不改其文件）——
from .script_writer import (
    MAX_WEB_SEARCH,
    _TurnStopped,
    _base_receipt,
    _claims_search_success,
    _claims_success,
    _execute_script_read,
    _execute_story_list,
    _execute_story_read,
    _execute_web_search,
    _is_chat_turn,
    _is_explicit_write_request,
    _loose_json,
    _normalized_search_query,
    _parse_args,
    strip_reserved_fact_blocks,
)
# —— Skill System V1（40.md）：生产技能工具面（只读，不占写槽）——
from .skill_system.tools import (
    SKILL_TOOLS,
    activation_hint as _skill_activation_hint,
    make_skill_executors,
)
from .skill_system.runtime import get_skill_runtime as _get_skill_runtime

log = logging.getLogger("agents.character_designer")

# 执行窗口大小（renewable window）：单次窗口用满后注入 self-check nudge 并续接
# 新窗口（次数不限），由 Agent 自行判断继续/停止——不再硬停长任务。仅防单次窗口
# 内的失控；24h 级连续生产由 renewable window + self-check 承载，非业务数量限制。
DEFAULT_MAX_TOOL_STEPS = 1024
# 真相收口：最终回复纠偏最多 1 次；仍不实则 Runtime 确定性覆写。
MAX_CORRECTIVE_ROUNDS = 1
# 防失控：连续同一工具同一失败签名达此阈值 → 诚实停止。
MAX_REPEAT_FAIL = 3
# 超时恢复：LLM read timeout 最多恢复重试次数（角色写入非批量，无需降级 batch hint）。
MAX_TIMEOUT_RETRIES = 2

# 写工具集合（只有这些可能产生 canonical 变更，需要回执 + 落盘校验 + 单写串行 + 资源锁）。
# character.generate_views 生成三视图并写回 Character.views（canonical 图片资产），
# 同样占写槽 + 锁角色资源（生成期间角色页只读）。
CHARACTER_WRITE_OPERATIONS = {"character.create", "character.update", "character.delete",
                              "character.generate_views"}
# 只读工具（不占写槽、可一次多个；用于「写后重读最终状态」的收尾判定）。
CHARACTER_READ_TOOLS = {"character.list", "character.read", "story.list",
                        "story.read", "script.read", "project.visual_profile.read",
                        "resource.inventory", "resource.read",
                        "shot.list", "shot.read"}

# Phase B §15：写工具 → 资源锁域（唯一权威映射；流式端点与 Producer Bridge 的锁接线
# 都从这张表取）。Resource Agent 是双写域 Agent：Character canonical（project.json）
# 与 Production Resources（resources.json）各挂各的锁——改角色不锁资源页，反之亦然，
# 且一次 mutation 只锁自己的域（绝不因一个 Look 在生成锁整个项目）。
# （resource.* mutation 工具在 Commit E/F 逐个落地时加入本表。）
WRITE_TOOL_DOMAIN = {
    "character.create": RESOURCE_CHARACTER,
    "character.update": RESOURCE_CHARACTER,
    "character.delete": RESOURCE_CHARACTER,
    "character.generate_views": RESOURCE_CHARACTER,
    # Phase B：resources.json 域（§8/§9 mutation 工具）
    "resource.clothing.upsert": RESOURCE_RESOURCES,
    "resource.look.prepare": RESOURCE_RESOURCES,
    "resource.look.approve": RESOURCE_RESOURCES,
    "resource.prop.register": RESOURCE_RESOURCES,
    "resource.composition.prepare": RESOURCE_RESOURCES,
    "resource.library.publish": RESOURCE_RESOURCES,
    "resource.library.import": RESOURCE_RESOURCES,
}

# 三视图生成超时（2026-08-30 用户规格）：生图很慢——单角色三视图链（front t2i → side/back
# i2i → 各自抠图）给 10 分钟兜底；一次最多 3 个角色并发（链间并发、链内串行；Agnes 图片
# 限流 image_1k≈17 张/分钟不是瓶颈），总上限 20 分钟。超时返回已完成部分，未完成的标记
# timeout，不整批作废。
VIEWS_PER_CHAR_TIMEOUT_S = 600
VIEWS_TOTAL_TIMEOUT_S = 1200
# 生成图片【不】进模型上下文：成败由「canonical 落盘 + 文件存在」确定性判定（receipt），
# 视觉质量由用户在窗口/角色页验收；9 张 base64 会撑爆上下文且 agnes 文本模型看图能力未验证。


def _characters_token(characters) -> str:
    """角色列表内容指纹（sha256 前 8 位）。哈希对象 = 有序 [{name, description_en}]：
    增/删/改名/改描述会变；三视图资产变化、其它字段不影响（Agent 只写这两个字段）。"""
    payload = json.dumps(
        [{"name": c.name, "description_en": (c.description_en or "")}
         for c in (characters or [])],
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _character_token(char: Optional[Character]) -> str:
    """单个角色的内容指纹（sha256 前 8 位）。该角色 name/description_en 改动会变；
    其它角色、三视图资产不影响。与 _characters_token 相互独立。"""
    if char is None:
        return hashlib.sha256(b"").hexdigest()[:8]
    payload = json.dumps({"name": char.name, "description_en": (char.description_en or "")},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _character_view(char_dir, name: str, view: str, suffix: str = ".png"):
    """角色三视图文件路径（与 routers/characters.py 落盘命名一致）。"""
    return char_dir / f"{name}_{view}{suffix}"


def _delete_view_files(pdir, name: str) -> int:
    """删除角色三视图文件（raw + seg），返回删除数。镜像 router 手动删除语义。"""
    char_dir = pdir / "output" / "characters"
    removed = 0
    for view in ("front", "side", "back"):
        for f in (_character_view(char_dir, name, view),
                  _character_view(char_dir, name, view, "_seg.png")):
            if f.exists():
                try:
                    f.unlink()
                    removed += 1
                except Exception:
                    pass
    return removed


def _delete_design_draft(pdir, name: str) -> bool:
    """清理角色 AI 设计草稿 key（镜像 routers/character_design.py delete_design 语义，
    agents 包内不 import router，故按同一文件路径/原子写最小实现）。"""
    path = pdir / "output" / "ai" / "character_designs.json"
    if not path.exists():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or name not in raw:
            return False
        del raw[name]
        from src.core.atomic import atomic_write_json
        atomic_write_json(path, raw)
        return True
    except Exception as e:
        log.warning("清理角色 AI 设计草稿失败（%s）: %s", name, e)
        return False


# ============ ProjectVisualProfile（Phase B §3.1/§3.3：唯一风格真相的最小认知）============
def _visual_profile_fingerprint_or_empty(pdir) -> str:
    """项目 profile 语义指纹（读取失败/unspecified → ""，不抛——sig/摘要均宽容降级）。"""
    try:
        from src.visual_profile import load_profile_or_none
        from src.visual_profile.models import visual_profile_fingerprint
        vp = load_profile_or_none(pdir)
        return visual_profile_fingerprint(vp) if vp is not None else ""
    except Exception:
        return ""


def _visual_profile_summary(pdir) -> str:
    """system 注入的最小 profile 摘要（§3.3：不给双风格真相，只锚定 render_family 与
    有无 profile；全文按需用 project.visual_profile.read 工具取）。"""
    try:
        from src.visual_profile import load_profile_or_none
        vp = load_profile_or_none(pdir)
    except Exception:
        return "读取失败（project_profile.json 损坏？）"
    if vp is None:
        return ("未设置（unspecified）。注意：Look/Composition 等资源生成会因无风格真相 "
                "fail closed；角色三视图当前不会追加项目风格指令")
    tags = ", ".join(vp.style_tags[:6]) if vp.style_tags else "-"
    neg = "; ".join(vp.negative_style_constraints[:4]) if vp.negative_style_constraints else "-"
    return (f"render_family={vp.render_family}；art_direction={(vp.art_direction or '-')[:80]}；"
            f"style_tags={tags}；负面约束={neg}。生成前如需全文用 project.visual_profile.read")


# ============ system ============
def _build_system(proj, pdir=None) -> str:
    """精简 system：职责/权限/工具说明/项目元信息。不注入角色全文或剧情——按需用工具取。"""
    vp_summary = "（未知）"
    if pdir is not None:
        vp_summary = _visual_profile_summary(pdir)
    base = CHARACTER_DESIGNER_SYSTEM.format(
        project_name=proj.name or "（未命名）",
        story_len=len(proj.story_text or ""),
        scenes_count=len(proj.scenes or []),
        characters_count=len(proj.characters or []),
        visual_profile_summary=vp_summary,
    )
    # Skill System V1（40.md §七）：L1 catalog 常驻尾部（只看到自己的 Skill view）
    base += _get_skill_runtime().catalog_block("character_designer")
    return base


CHARACTER_DESIGNER_SYSTEM = """你是 AGNES 的 Resource Agent（资源设计师 / Production Resource Designer）——动画项目的生产视觉资源负责人。你负责把故事中的角色与生产需求转化为【稳定、可复用、可审查、可批准】的生产视觉资源：角色身份（Character）、三视图、服装（Clothing）、造型（CharacterLook）、道具（Prop）、组合参考（Composition）与共享库操作。（内部兼容说明：你的会话/工具名沿用历史 id character_designer，这只是技术标识，对外身份就是 Resource Agent。）
你【只写你的域】：角色 canonical（name/description_en）与 resources.json 生产资源。不修改剧本正文、不增删改故事段、不创建/修改镜头、不写场景系统、不写项目视觉定义。剧本与故事段是你的参考资料，不是写入对象。

项目：{project_name}（剧本正文 {story_len} 字，{scenes_count} 个故事段，现有 {characters_count} 个角色）。
项目视觉定义（ProjectVisualProfile——全项目唯一风格真相，你只能读不能改）：{visual_profile_summary}

【你必须理解的核心概念（做错任何一个都是方向性错误）】
- Character 是「这个人是谁」：叙事身份（name + description_en）。它不是换装容器。
- Look 是「这个角色在某一生产状态下长什么样」：正式生产 binding 消费的 approved 视觉身份——服装、发型变体、伪装、旅行状态、制服、损耗/损伤等视觉变体都做在 Look 上。需要不同外观时【新建 Look 或修改 Look】，绝不为换装去改 Character identity。
- 三视图（views）是身份 seed / legacy identity reference：Look 生成以它为 identity reference。三视图不是最终生产 Look 的替代品；production-ready 意味着有 approved Look，而不只是有三视图。
- Prop 是独立生产资源（形状/材质/用途/时代感 + approved 参考图）。「某角色拿着剑」不是 Prop 的永久归属；角色与道具的组合关系属于 Composition 或未来的分镜生产状态，不要写进 Prop。
- Composition 是生产参考，不是故事事实：组合图里 Alice 持剑不改变剧情，也不代表 Alice 永久持剑；不要因为组合参考去修改故事或角色身份。
- ProjectVisualProfile 是全项目视觉宪法：读它、遵守它、不修改它（你没有修改它的工具）。用户要求资源风格与当前 profile 冲突时：指出冲突，不能偷偷改 profile，也不能假装冲突不存在。

角色 canonical 字段（现有数据模型，只有这些；不要假设存在其它字段）：
- name：角色名。项目内唯一标识，故事段/镜头按【名字】引用角色。创建后不要试图改名。
- description_en：角色的英文视觉描述（生成三视图/首帧时直接作为 prompt 使用）。这是角色设定唯一的 canonical 文本字段：年龄、性别、身份、外貌、发型、服装、体型、识别特征都组织在这里。
- views：三视图图片资产（front/side/back），由图片流程管理，你不能读写图片本身。

【审批分档（approval_mode 的选择纪律）】
- 用户/Producer 要求「准备成可以直接用于生产」「把资源准备完整」「production-ready」「完成后供分镜使用」→ 用 approve_if_pass（前提：正式 vision 审查真实 pass；审查没过绝不批准）。
- 用户说「先设计看看」「生成几个候选」「我想看看效果」「不要定稿」→ 必须用 review_only，绝不自动批准。
- 撤销已批准资源是高风险用户决策：你没有 revoke 工具，由用户在 UI 显式操作；用户要撤时如实引导，不要自己想办法绕过。

【cast / 生产需求检查纪律】
你可以在准备任务中读镜头（shot.list/shot.read）。发现「prompt/动作/对白明确提到某角色，但 shot.characters 没列全」这类生产不一致时：①如实、明确报告（production inconsistency）；②绝不自己补 cast 或改镜头（你没有 Shot 写工具）；③把问题作为结构化 warning 报告，Producer 会转交 Director 处理。绝不为「让流程继续」而偷偷补 cast。

你有这些工具：
- character.list()：角色任务的【第一步】——轻量角色地图：每个角色的 name/描述摘要/是否有三视图/出场故事段数 + 角色版本指纹。开销很小。用于避免重复创建、定位角色。
- character.read(character_name)：读取一个角色的完整 canonical 数据（描述全文、三视图状态、出场故事段）+ 该角色内容指纹。【修改/删除一个角色前必须先 read 目标角色】。
- character.create(name, description_en, expected_revision)：创建一个新角色。创建前必须先 character.list 确认没有同名/同设定角色（避免重复创建）。
- character.update(character_name, expected_revision, description_en)：修改一个【已有】角色。必须先 character.read 拿到该角色内容指纹 expected_revision 并原样回传。只传要改的字段。绝不 delete 后 recreate。
- character.delete(character_name)：删除一个明确角色（连同其三视图文件与 AI 设计草稿）。用户明确要求删除时直接调用；删除前 character.read 看它是否仍出场，出场故事段会留下旧名字引用，回复里如实提醒。
- character.generate_views(character_names)：为一个或最多 3 个角色【生成三视图图片】（正面/侧面/背面链式生成保持同一角色一致，自动抠图透明底），图片写回角色数据并在窗口展示给用户。规则：①必须先 character.read 确认角色存在且 description_en 已完善——图片由 description_en 生成，文本设定是图片的源；②生成很慢（并发也要数分钟），调用后等真实返回，不要重复调用；③描述与已有图片一致时会自动跳过（不重复生成），描述改过则全部重新生成——所以【先改好文本，再生成图片】；④图片生成成功与否以工具真实返回为准，不要凭空声称已生成。
- story.list()：故事段轻量全局地图（position/scene_id/摘要/角色/镜头数）。根据剧情理解人物时【第一步】用。
- story.read(scene_id)：读取一个故事段完整内容。按需读该角色首次登场、关键剧情、性格体现、服装/时代相关段；信息不足再扩大范围。
- script.read()：读取剧本正文全文 + 版本指纹。仅当局部故事段不足以支撑全局性角色设计（人物完整弧光、全篇身份梳理）且确有需要时才读；不要默认读全文。
- project.visual_profile.read()：读取项目视觉定义（ProjectVisualProfile）全文：render_family / art_direction / style_tags / 负面风格约束 + 指纹与 revision。设计涉及风格取向（介质、画风、时代质感）或用户要求与当前风格可能冲突时先读它；你的视觉生成遵守该定义，不得假设其它风格，也不得试图修改它（它由用户拥有）。
- resource.inventory(filter)：列出项目生产资源（服装/Look 造型/道具/组合参考）轻量地图：id/名称/所属角色/审批与生成状态/参考图数 + document_revision。准备或修改任何生产资源前【第一步】用它定位与防重复创建。
- resource.read(kind, resource_id)：读取一个生产资源的高信号详情（参考图/审批状态/最近审查结论/关联关系）+ document_revision。对 Look/Prop/Composition 修改或再生成前必须先 read 拿到版本指纹。
- resource.clothing.upsert(...)：创建（不带 clothing_id）或更新（带 clothing_id）一件服装。服装是 Look 的一致性参考。
- resource.look.prepare(...)：为角色完成一次造型（Look）生产准备：建/改 Look → 生成 1 张候选图 → 正式视觉审查 → approve_if_pass 且审查 pass 时自动批准。一次调用 = 一次生成尝试（生成约 1 分钟，耐心等真实返回）；审查不通过会返回真实原因，你要么调整描述后再试，要么如实报告用户——绝不在未修改任何内容的情况下反复重试；同一问题连续失败按停止纪律处理。
- resource.prop.register(...)：登记道具（元数据 + 参考图绑定 + 审查 + 可选批准）。系统没有道具生成端点：没有参考图时工具返回 reference_required，你要向用户要参考图或用已有资产，绝不假装生成了道具图。
- resource.composition.prepare(...)：生产角色+道具组合参考（需要 Look 已批准、每个绑定 prop 已有 approved 参考）。审查画错会如实 needs_fix——组合参考是生产参考，不是故事事实。
- shot.list(scene_id)：列出某故事段（或全部）镜头的轻量地图。仅当用户要求「把当前分镜需要的资源准备好」之类任务时用它看各镜头用到哪些角色/动作/道具线索，判断缺哪些资源；你不是分镜设计者，绝不修改镜头。
- shot.read(shot_id)：读取一个镜头的完整内容（描述/角色/动作/提示词/绑定状态线索）。同上，只读，用于判断资源缺口。
- web.search(query)：搜索现实世界资料，返回约 3 条「标题+摘要+链接」。仅当①用户明确要求查资料，或②设计涉及真实世界知识明显影响可信度（历史时期服装、民族服饰、职业制服、年代发型、真实文化背景、地域审美）时使用。普通修改（改发型/换配色）不搜索。每轮最多 3 次，超限会被系统拒绝。

🔴 最重要的铁律（违反即失败）：
- 你只有通过【真正发起工具调用】（function/tool_calls）才能读/写数据。在回复里说「已创建/已修改/已生成/已批准」但没有真正调用对应工具，等于什么都没做——这是严重错误。
- 只能依据工具【真实返回】的结果向用户汇报；没看到 ok=true 就不能声称成功；审查没过（needs_fix/uncertain）绝不能说「已批准/已可用」。不要编造角色名、资源 id、指纹、数量。
- 审查结论（review pass）是证据，批准（approve）是显式动作——两者永远分开；review_only 轮次不得批准。
- 用户要求修改角色时，必须用 character.update 修改【已有角色】，绝不 delete 后 recreate——那会破坏角色名引用、三视图关联与一致性。
- 你没有写剧本/故事段/镜头/场景/视觉定义的工具：任何情况下都不要试图改它们来配合资源。

【聊天与探讨（用户没让你改东西时）】
- 用户在提问、征求看法、比较方案、讨论角色时（「你觉得女主目前人设怎么样」「要不要给他加眼镜」「反派穿黑色是不是太俗」「让她更冷一点好不好」）：直接自然语言讨论，按问题自然决定长度。
- 这类轮次【不调用写工具】；讨论中提到的设计想法【不要自动执行】，用户明确提出修改要求（「就改成短发」「可以，加上」）才真正 character.update。
- 不要输出「我没有真正执行修改」之类的话——用户本来就没让你改。
- 回答需要事实依据时（问到某个角色当前设定、剧情细节而上下文没有），才按需调用只读工具确认；凭上下文能答的直接答。聊天轮【不自动搜索】。
- 不要整段复述 description_en 或剧情原文；必要时引用一两句。

【剧情事实 vs 设计推断】
- 故事里【明确写了】的人物设定（年龄、性别、身份/职业、种族、关键外貌、关键装备）是剧情事实，设计必须尊重，不得为了丰富档案擅自更改。
- 故事【没写】的视觉细节（具体发型、服装材质配色、配饰、五官细节）由你合理设计补全。
- 用户要求与剧情冲突时（如把设定 70 岁的爷爷改成 20 岁青年）：先向用户指出「这与当前故事中的既有设定冲突」；用户明确坚持才修改角色；【绝不】偷偷修改剧本/故事段去配合。

【精准修改，不无理由漂移（角色一致性核心）】
- description_en 由英文短语组成：用户【没有提到】的短语必须【逐字原样保留】——不要换同义词、不要重新措辞、不要调整语序、不要重组句子。哪怕你认为某个短语可以写得更好，只要用户没要求，就一个词都不要动。
- 用户只要求改某一点（「头发改短发」「衣服更有民国感」「让她更冷一点」）：先 character.read 当前角色，再 character.update 重写 description_en——【只改与要求直接相关的短语】，其余内容逐字保留，不要顺带改写或重排。
- 用户对角色整体不满意要求重新设计（「这个反派太普通，重新设计」）：允许大范围重写 description_en，但仍然用 character.update（保持角色名与引用稳定）；用户没要求变的方面（如年龄、身份）保持不变。
- 角色一旦确定：基本年龄、身份、主要外貌、发型、基础体型、核心服装风格、重要标志物，后续修改不能无理由漂移。
- 不同场合需要不同服装（日常装/制服/睡衣/战斗装）不是身份变化：在描述中以「默认装 + 场合区分」表达，或按用户指示处理，不要因此创建重复角色。

【读取策略：最小充分上下文，按需扩大】
- 根据故事设计角色（「根据现在的故事设计男女主角」）：character.list（先知道已有哪些角色，避免重复创建）→ story.list 全局地图 → story.read 只读相关局部段（主角首次登场/关键剧情/人物关系/性格体现/服装时代相关段）→ 信息不足再扩大读取 → 真正需要全局理解才 script.read。不要因为「根据剧本设计人物」就每次读全文。
- 修改已有角色：character.read 目标角色通常就够；需要结合剧情再按需读故事段。

【角色创建范围（重要，先判断再建）】
- 只为【真正在分镜画面里出镜、且需要跨镜头保持视觉一致性】的人物创建 Character canonical——即后续分镜图/三视图需要参考其固定形象的角色：主角、有连续戏份的配角。判断标准：这个人物会不会在镜头画面里出现脸/形象、并且多次出场需要长得一样？会 → 建。
- 仅在台词/旁白/回忆/他人转述中被【提到名字】但本人不出镜的人物（已故亲人、画外音、信里/电话里提到的人、群众口中的名字），【不要】创建角色 canonical——他们不需要视觉参考图，建了只会让角色名单膨胀、让导演误读。可在文字设定/剧本里提及，但不建 Character。
- 不确定某人物是否出镜时，读相关故事段确认；若全片只在回忆/投影中一闪而过且不要求形象一致，倾向不建，需要时再补。

【工作方式】
1. 根据故事创建角色：character.list → story.list → 按需 story.read → character.create。description_en 用英文写成可直接用于图片生成的【稳定角色描述】：外貌/发型/服装/配色/2-4 个长期识别特征；与故事明确设定一致；与项目已有角色在轮廓/颜色上可区分；不含镜头/运镜/背景故事。
2. 用户要求修改：character.read → character.update（只改相关部分）。
3. 用户要求删除：character.read（确认存在与引用）→ character.delete → 用 character.list 核对。
4. 用户要求生成图片（三视图/角色图/立绘/出图）：确认角色文本设定完善 → character.generate_views（1 个角色直接传单元素数组；「男女主和反派都生成」传 3 元素数组并发生成）→ 依据工具真实返回汇报（完成了几个、图片是否已展示）。「给X生成/生成X/出图」是【明确的执行指令】，直接调用工具完成，【不要】反问"需要我生成吗"。只有用户在纯粹询问可能性（"能不能生成图片？""你们支持生图吗？"）且没有任何明确目标时才先回答不调用工具。
5. 生产资源准备（「把主要角色的生产资源准备好」类任务）：resource.inventory（现状地图）→ 缺角色则先走角色链（读故事→create→generate_views 出身份参考）→ resource.clothing.upsert（服装）→ resource.look.prepare（造型生成+审查+按分档批准）→ 需要道具时 resource.prop.register（要参考图）→ 适合时 resource.composition.prepare（组合参考）→ 最后 resource.inventory 核对终态并如实汇报哪些已 production-ready、哪些停在审查/缺参考。
6. 任一写工具返回 stale=true：你读取后内容被改动过、未写入；用返回的最新状态和 revision 重新判断后重试；连续 stale 说明有并发写入者，先 inventory 看清现状。
7. 一次只做用户要求的修改，不顺手改无关角色或资源。

【写工具必须一个一个来】（重要）：
- 所有写工具（character.create/update/delete/generate_views、resource.clothing.upsert、resource.look.prepare、resource.prop.register、resource.composition.prepare、resource.library.publish/import）必须【一个一个执行】：每发一个写工具调用，等它返回最新状态/指纹后，再发下一个。同一次回复里发了多个写工具时，系统只执行第 1 个，其余会被拒绝。
- character.generate_views 一次调用就能并发生成最多 3 个角色（把名字都放进 character_names 数组），不要拆成多次调用。
- 批量设计多个角色（如「设计男主、女主和反派」）：逐个执行——先读取剧情，然后一个角色的 create/update 完成（看到真实回执）后再做下一个；每个角色创建前都在心里对照 character.list 的最新结果，避免重复。
- 准备完整生产资源链（角色→三视图→服装→Look→道具→组合）时按依赖顺序逐层推进，每层看到真实回执再进下一层；任一层失败先解决再继续。
- 只读工具（character.list / character.read / story.list / story.read / script.read / project.visual_profile.read / resource.inventory / resource.read / shot.list / shot.read / web.search）可以一次发多个，它们不碰数据、不冲突。

【回复规范（只汇报本轮改动）】
- 执行完写工具后，只汇报本轮真实执行的操作：创建/修改/删除了哪个角色、改了哪些方面。一两句即可，必要时简述设计理由。
- 不要复述之前轮次的历史改动；不要用清单罗列历史改动当成"本轮成果"。
- description_en 是英文，但你向用户汇报用【中文】说清这次改了什么、保留了什么。

要求：
- name 跟随剧本里的称呼（中文剧作用中文名）；不得与现有角色重名。
- description_en 必须是英文、具体、可重复生成；不要 markdown/解释/代码块。
- 版本指纹必须原样回传 read/list/写工具返回值里的 8 位十六进制串，不要编造。
"""


# ============ 工具 schema（标准 OpenAI function 格式）============
CHARACTER_LIST_TOOL = {
    "type": "function",
    "function": {
        "name": "character.list",
        "description": ("角色相关任务的【第一步】：列出项目所有角色的轻量地图——name（稳定标识）、"
                        "描述摘要（前60字）、是否有三视图、出场故事段数，及角色版本指纹。开销很小。"
                        "用于：创建前避免重复创建、定位用户说的角色（按名字匹配）。"
                        "不返回描述全文，需要全文用 character.read。"),
        "parameters": {"type": "object", "properties": {}},
    },
}

CHARACTER_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "character.read",
        "description": ("读取一个角色的完整 canonical 数据：描述全文（description_en）、三视图状态"
                        "（有哪些视角图、是否已生成）、出场故事段（scene_id+摘要+镜头数）、该角色内容指纹。"
                        "【修改或删除一个角色前必须先调用本工具】拿到 expected_revision。"),
        "parameters": {
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "要读取的角色名（来自 character.list）。"},
            },
            "required": ["character_name"],
        },
    },
}

CHARACTER_CREATE_TOOL = {
    "type": "function",
    "function": {
        "name": "character.create",
        "description": ("创建一个新角色（写角色 canonical 数据）。调用前必须已 character.list 确认没有"
                        "同名或明显重复的角色。expected_revision 用最近一次 character.list / 写工具"
                        "返回的角色版本指纹。name 与 description_en 必填。"),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "角色名（跟随剧本称呼；不得与现有角色重名）。"},
                "description_en": {
                    "type": "string",
                    "description": "角色的英文视觉描述（直接用于图片生成的稳定角色描述：外貌/发型/服装/配色/识别特征；不含镜头运镜/背景故事）。",
                },
                "expected_revision": {
                    "type": "string",
                    "description": "你最近一次 character.list 看到的角色版本指纹（8 位十六进制）。用于乐观并发。",
                },
            },
            "required": ["name", "description_en", "expected_revision"],
        },
    },
}

CHARACTER_UPDATE_TOOL = {
    "type": "function",
    "function": {
        "name": "character.update",
        "description": ("修改一个【已存在】角色的 canonical 数据（当前可改字段：description_en）。"
                        "必须先 character.read(character_name) 拿到该角色内容指纹 expected_revision "
                        "并原样回传。description_en 是整体替换：只改用户要求的部分，其余内容原样保留"
                        "（年龄/身份/发色/体型/识别特征不无理由漂移）。绝不 delete 后 recreate。"),
        "parameters": {
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "要修改的角色名（稳定标识）。"},
                "expected_revision": {
                    "type": "string",
                    "description": "你最近一次 character.read(character_name) 看到的该角色内容指纹（8 位十六进制）。期间被改动会拒绝写入并返回最新内容。",
                },
                "description_en": {"type": "string", "description": "新的完整英文视觉描述（整体替换；只改用户要求的相关部分）。"},
            },
            "required": ["character_name", "expected_revision"],
        },
    },
}

CHARACTER_DELETE_TOOL = {
    "type": "function",
    "function": {
        "name": "character.delete",
        "description": ("删除一个明确角色（连同其三视图文件与 AI 设计草稿）。用户明确要求删除某角色时"
                        "直接调用；删除前先 character.read 确认目标，若该角色仍出场（有出场故事段），"
                        "回复里如实提醒用户故事段会留下旧名字引用。删完用 character.list 核对。"),
        "parameters": {
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "要删除的角色名。"},
            },
            "required": ["character_name"],
        },
    },
}

CHARACTER_GENERATE_VIEWS_TOOL = {
    "type": "function",
    "function": {
        "name": "character.generate_views",
        "description": ("为一个或多个（最多 3 个）【已存在】角色生成/更新三视图图片（正面/侧面/背面，"
                        "链式生成保持同一角色一致性，自动抠图透明底），图片写回角色数据并在窗口展示给"
                        "用户。必须先 character.read 确认角色存在。生成很慢（并发执行也需数分钟），"
                        "调用后耐心等真实返回，不要在等待中重复调用。生成依据是角色当前的 description_en"
                        "：描述与已有图片一致时会跳过（不重复生成），描述已修改则全部重新生成——所以"
                        "先把文本设定改好再生成。每轮最多调用一次本工具（写工具串行）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "character_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 3,
                    "description": "要生成三视图的角色名数组（1~3 个，来自 character.list；三个角色会并发生成）。",
                },
            },
            "required": ["character_names"],
        },
    },
}

RESOURCE_INVENTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "resource.inventory",
        "description": ("列出项目当前的生产资源（Production Resources，resources.json 域）"
                        "轻量地图：每个资源的 id/名称/所属角色/审批状态/生成状态/参考图数/"
                        "封面/资源版本指纹 + 当前 document_revision。开销很小。支持 filter："
                        "all（默认）/clothing/look/prop/composition。"
                        "准备或修改任何资源前先看这里，避免重复创建、定位目标资源。"
                        "不返回图片内容与完整历史。"),
        "parameters": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "enum": ["all", "clothing", "look", "prop", "composition"],
                    "description": "资源类型过滤（默认 all）。",
                },
            },
        },
    },
}

RESOURCE_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "resource.read",
        "description": ("读取一个生产资源的高信号详情（含参考图/审批状态/最近审查结论/"
                        "关联关系）+ 当前 document_revision。kind：clothing（服装）/"
                        "look（角色造型）/prop（道具）/composition（角色+道具组合参考）/"
                        "reference_asset（参考图资产）。【对 Look/Prop/Composition 做"
                        "修改或再生成前必须先 read 目标拿到其版本指纹】。只读工具。"),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string",
                         "enum": ["clothing", "look", "prop", "composition",
                                  "reference_asset"],
                         "description": "资源类型。"},
                "resource_id": {"type": "string",
                                "description": "资源 id（来自 resource.inventory，如 look_xxx/prop_xxx）。",
                                },
            },
            "required": ["kind", "resource_id"],
        },
    },
}

CLOTHING_UPSERT_TOOL = {
    "type": "function",
    "function": {
        "name": "resource.clothing.upsert",
        "description": ("创建或更新一件服装（Clothing）。不传 clothing_id = 创建（"
                        "expected_resources_revision 传 resource.inventory 看到的 "
                        "document_revision）；传 clothing_id = 更新（传该 clothing 的 "
                        "revision）。服装是 Look 的一致性参考，不是独立图片任务。"),
        "parameters": {
            "type": "object",
            "properties": {
                "clothing_id": {"type": "string",
                                "description": "要更新的 Clothing id（更新时必传；创建时省略）。"},
                "name": {"type": "string", "description": "服装名称（创建必填，如 Black Coat）。"},
                "description": {"type": "string", "description": "服装描述（外观/穿着方式/时代感）。"},
                "design_traits": {"type": "string", "description": "设计特征（剪裁/结构/装饰）。"},
                "materials": {"type": "array", "items": {"type": "string"},
                              "description": "材质列表（如 wool/silk/leather）。"},
                "colors": {"type": "array", "items": {"type": "string"},
                           "description": "配色列表。"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "标签。"},
                "expected_resources_revision": {"type": "string",
                                                "description": "OCC 期望 revision（见描述）。"},
            },
            "required": ["expected_resources_revision"],
        },
    },
}

LOOK_PREPARE_TOOL = {
    "type": "function",
    "function": {
        "name": "resource.look.prepare",
        "description": ("为角色完成一次 CharacterLook 生产准备尝试：新建或更新 Look → "
                        "生成候选图（1 张）→ 正式 vision 审查 → approval_mode=approve_if_pass "
                        "且审查 pass 时自动批准。一次调用 = 一次生产尝试；审查不通过时返回"
                        "真实原因，由你决定调整后重试或报告 blocker——工具不会偷偷重试。"
                        "生成很慢（约 1 分钟），调用后等真实返回。新建需 appearance_description；"
                        "更新传 look_id + 要改的字段（expected_resources_revision 传该 look 的 "
                        "revision；纯再生成可不传字段）。审查连续 needs_fix 且候选实际可用时："
                        "请用户人评（POST /looks/{id}/review/human——用户人评=最高审查层级，"
                        "pass 证据可解锁 resource.look.approve 的批准门槛），不要闷头重试。"),
        "parameters": {
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "角色名（必须已存在）。"},
                "look_id": {"type": "string",
                            "description": "既有 Look id（更新/再生成时传；新建时省略）。"},
                "label": {"type": "string", "description": "Look 名称（如 rain look / night cloak）。"},
                "clothing_id": {"type": "string", "description": "绑定的服装 id（可选）。"},
                "appearance_description": {"type": "string",
                                           "description": "该造型下的完整外观描述（英文，含默认身份差异与服装）。"},
                "appearance_delta": {"type": "string",
                                     "description": "相对角色默认身份的增量描述（英文，可选）。"},
                "expected_resources_revision": {"type": "string",
                                                "description": "OCC 期望 revision（创建=document_revision，"
                                                               "更新/再生成=该 look 的 revision）。"},
                "approval_mode": {"type": "string", "enum": ["review_only", "approve_if_pass"],
                                  "description": "review_only=只生成审查等用户验收；approve_if_pass=审查 "
                                                 "pass 即正式批准（仅在用户要求 production-ready 时用）。"},
                "conflict_acknowledged": {"type": "boolean",
                                          "description": "预检发现遮面/面具等与身份参考的冲突后，用户明确确认坚持时传 true。"},
            },
            "required": ["character_name", "expected_resources_revision", "approval_mode"],
        },
    },
}

PROP_REGISTER_TOOL = {
    "type": "function",
    "function": {
        "name": "resource.prop.register",
        "description": ("登记道具（Prop）：创建/更新元数据 + 绑定参考图（reference_local="
                        "项目内相对路径 / reference_url=公网 URL / reference_asset_id=已注册"
                        "资产三选一）→ 正式 vision 审查 → approve_if_pass 且 pass 时批准。"
                        "系统没有道具生成端点——没有参考图时本工具会诚实返回 reference_required，"
                        "绝不偷偷生成图片。"),
        "parameters": {
            "type": "object",
            "properties": {
                "prop_id": {"type": "string", "description": "既有 Prop id（更新时传；新建时省略）。"},
                "name": {"type": "string", "description": "道具名（新建必填）。"},
                "description": {"type": "string", "description": "道具描述（形状/材质/用途/时代感）。"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "标签。"},
                "reference_local": {"type": "string",
                                    "description": "参考图的项目内相对路径（如 input/prop_sword.png）。"},
                "reference_url": {"type": "string", "description": "参考图公网 URL（http/https）。"},
                "reference_asset_id": {"type": "string",
                                       "description": "已注册 ReferenceAsset id（复用项目内已有图）。"},
                "expected_resources_revision": {"type": "string",
                                                "description": "OCC 期望 revision（创建=document_revision，"
                                                               "更新=该 prop 的 revision）。"},
                "approval_mode": {"type": "string", "enum": ["review_only", "approve_if_pass"],
                                  "description": "同 resource.look.prepare。"},
            },
            "required": ["expected_resources_revision", "approval_mode"],
        },
    },
}

LOOK_APPROVE_TOOL = {
    "type": "function",
    "function": {
        "name": "resource.look.approve",
        "description": ("显式批准 Look 的某张【已有】候选参考图（Phase D §35：Agent 无法访问"
                        "既有正式审批 capability 的系统缺口修复）。门槛：该候选必须已有 "
                        "overall=pass 的视觉审查证据——pass 不自动批准，但没有 pass 证据也不能"
                        "批准（review_not_passed 会被拒绝）。用途：候选审查已通过但未批准时"
                        "（如 approve_if_pass 未传、批准后 OCC 冲突），直接批准复用，避免为"
                        "批准而无谓重新生成烧图。"),
        "parameters": {
            "type": "object",
            "properties": {
                "look_id": {"type": "string", "description": "Look id（look_xxx）。"},
                "asset_id": {"type": "string",
                             "description": "要批准的候选 ReferenceAsset id（必须是该 Look 下"
                                            "已有 overall=pass 审查证据的候选）。"},
                "expected_resources_revision": {
                    "type": "string",
                    "description": "OCC 期望 revision：该 look 的 revision（resource.read 返回）。"},
                "set_cover": {"type": "boolean",
                              "description": "是否设为封面（省略=首个 approved 自动成为封面）。"},
            },
            "required": ["look_id", "asset_id", "expected_resources_revision"],
        },
    },
}

COMPOSITION_PREPARE_TOOL = {
    "type": "function",
    "function": {
        "name": "resource.composition.prepare",
        "description": ("生产角色+道具组合参考（CharacterReferenceComposition）：新建或更新 → "
                        "生成候选图 → 六维度审查（角色身份/Look 一致/道具保真/binding 正确/"
                        "道具可见性/缺陷）→ approve_if_pass 且 pass 时批准。前置要求：Look 已 "
                        "approved 且至少一个 approved 参考；每个绑定的 prop 已有 approved 参考。"
                        "一次调用 = 一次生成尝试，画错会如实 needs_fix。"),
        "parameters": {
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "角色名（新建必填）。"},
                "look_id": {"type": "string", "description": "已 approved 的 Look id（新建必填）。"},
                "composition_id": {"type": "string",
                                   "description": "既有 Composition id（更新/再生成时传）。"},
                "prop_bindings": {
                    "type": "array",
                    "items": {"type": "object",
                              "properties": {
                                  "prop_id": {"type": "string"},
                                  "relation": {"type": "string",
                                               "description": "attached|carried|held|near|worn"},
                                  "anchor": {"type": "string",
                                             "description": "如 right_hand/left_hand/back/head 等"},
                                  "visual_state_note": {"type": "string"},
                                  "required_visibility": {"type": "string",
                                                          "description": "required|optional"},
                              },
                              "required": ["prop_id"]},
                    "description": "道具绑定列表（1~3 个）。"},
                "label": {"type": "string"},
                "pose_note": {"type": "string", "description": "姿态备注。"},
                "composition_description": {"type": "string", "description": "组合描述。"},
                "expected_resources_revision": {"type": "string",
                                                "description": "OCC 期望 revision（创建=document_revision，"
                                                               "更新/再生成=该 composition 的 revision）。"},
                "approval_mode": {"type": "string", "enum": ["review_only", "approve_if_pass"]},
            },
            "required": ["expected_resources_revision", "approval_mode"],
        },
    },
}

LIBRARY_PUBLISH_TOOL = {
    "type": "function",
    "function": {
        "name": "resource.library.publish",
        "description": ("把项目资源发布到共享库（copy-on-publish：项目资源零改动；同源重复"
                        "发布=共享库版本+1）。resource_kind=character 时 resource_id 传角色名"
                        "（发布角色母版）；其它 kind 传资源 id。发布前需先确认 Clothing 依赖已"
                        "发布（Look 依赖其 clothing）。expected_resources_revision 传最近一次 "
                        "resource.inventory 的 document_revision（确保发布的是你看到的源状态）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "resource_kind": {"type": "string",
                                  "enum": ["clothing", "look", "prop", "composition",
                                           "character"]},
                "resource_id": {"type": "string",
                                "description": "资源 id（character 域传角色名）。"},
                "expected_resources_revision": {"type": "string"},
                "destination": {"type": "string", "description": "仅支持 default 共享库。"},
            },
            "required": ["resource_kind", "resource_id", "expected_resources_revision"],
        },
    },
}

LIBRARY_IMPORT_TOOL = {
    "type": "function",
    "function": {
        "name": "resource.library.import",
        "description": ("从共享库导入资源快照到当前项目（copy-on-import：新 id、媒体复制、"
                        "origin 记录；共享库原资源不受影响）。look 导入自动捆绑其共享 clothing"
                        "（项目内已有同源副本时不重复导入）；character 导入要求项目内无同名角色。"
                        "expected_resources_revision 传 resource.inventory 的 document_revision，"
                        "stale 会被拒绝。导入失败一律 fail closed，不产生半途状态。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shared_resource_id": {"type": "string",
                                       "description": "共享资源 id（来自共享库列表）。"},
                "expected_resources_revision": {"type": "string",
                                                "description": "项目 resources document_revision。"},
            },
            "required": ["shared_resource_id", "expected_resources_revision"],
        },
    },
}

VISUAL_PROFILE_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "project.visual_profile.read",
        "description": ("读取项目视觉定义（ProjectVisualProfile）全文：render_family / "
                        "art_direction / style_tags / 负面风格约束 + 指纹与 revision。"
                        "只读工具：profile 由用户拥有，你没有修改它的工具。设计涉及风格取向"
                        "（介质/画风/时代质感）或用户要求可能与当前项目风格冲突时先读；"
                        "资源类视觉生成（Look/Composition）在未设置 profile 的项目会被系统 "
                        "fail closed 拒绝。"),
        "parameters": {"type": "object", "properties": {}},
    },
}

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web.search",
        "description": "搜索现实世界资料，返回约 3 条「标题+摘要+链接」。仅在两种情况使用：①用户明确要求搜索（搜一下/查一下/根据真实资料设计）；②设计涉及真实世界知识明显影响可信度（历史时期服装、民族服饰、职业制服、年代发型、真实文化背景、地域审美）。普通修改（改发型/换配色/调性格）不要搜索。每轮最多 3 次（超过会被系统拒绝）。查询词保持简洁、聚焦一个具体问题。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "简洁、聚焦一个具体问题的中文搜索词，如「1930年代上海 女性 服装」",
                },
            },
            "required": ["query"],
        },
    },
}

CHARACTER_DESIGNER_TOOLS = [
    CHARACTER_LIST_TOOL, CHARACTER_READ_TOOL,
    CHARACTER_CREATE_TOOL, CHARACTER_UPDATE_TOOL, CHARACTER_DELETE_TOOL,
    CHARACTER_GENERATE_VIEWS_TOOL,
    RESOURCE_INVENTORY_TOOL, RESOURCE_READ_TOOL,
    CLOTHING_UPSERT_TOOL, LOOK_PREPARE_TOOL, LOOK_APPROVE_TOOL, PROP_REGISTER_TOOL,
    COMPOSITION_PREPARE_TOOL,
    LIBRARY_PUBLISH_TOOL, LIBRARY_IMPORT_TOOL,
    VISUAL_PROFILE_READ_TOOL,
    WEB_SEARCH_TOOL,
    # Skill System V1（40.md）：生产技能发现/加载（只读）
    *SKILL_TOOLS,
]

# shot.list / shot.read（Phase B §12：让 Agent 能看到当前分镜需要哪些资源——只读，
# 不设计分镜、绝不写 Shot）。schema 从 Director 过滤复用（禁止发明第二套 schema）；
# 执行器 lazy 复用 Director 实现（director.py 模块级 import 本模块的
# _execute_character_list/read，本文件若模块级反向 import director 会成环——
# turn 时 director 已完全加载，安全）。

def _tool_surface() -> list:
    """Resource Agent 实际下发给 LLM 的工具面（turn 开始时构建一次）：
    模块常量（character/resource/profile/web 域）+ Director 的 shot.list/shot.read schema。
    director 不可加载时降级为基础面（Agent 仍可用，只是看不到分镜）。"""
    try:
        from .director import DIRECTOR_TOOLS
    except Exception:   # noqa: BLE001 - 防御：partial-import 等极端场景降级基础面
        return list(CHARACTER_DESIGNER_TOOLS)
    names = {"shot.list", "shot.read"}
    return CHARACTER_DESIGNER_TOOLS + [t for t in DIRECTOR_TOOLS
                                       if t["function"]["name"] in names]


def _execute_shot_list_via_director(rt: AgentRuntime, project: str, arguments: str,
                                    instruction, message_id) -> dict:
    """shot.list 只读执行器（复用 Director 实现；lazy import 防环）。"""
    from .director import _execute_shot_list
    return _execute_shot_list(rt, project, arguments, instruction, message_id)


def _execute_shot_read_via_director(rt: AgentRuntime, project: str, arguments: str,
                                    instruction, message_id) -> dict:
    """shot.read 只读执行器（复用 Director 实现；lazy import 防环）。"""
    from .director import _execute_shot_read
    return _execute_shot_read(rt, project, arguments, instruction, message_id)


# ============ 工具执行 ============
def _load_proj(rt: AgentRuntime, project: str):
    pdir = rt._resolve_project_dir(project)
    return pdir, ProjectStore(pdir), ProjectStore(pdir).load()


def _reload(rt: AgentRuntime, project: str):
    """从 canonical（磁盘）重新读取项目——写后校验只用这份，绝不用内存对象。"""
    pdir = rt._resolve_project_dir(project)
    return ProjectStore(pdir).load()


def _execute_character_list(rt: AgentRuntime, project: str, arguments: str,
                            instruction, message_id) -> dict:
    """列出所有角色的轻量地图 + 角色版本指纹。只读，不写盘/不审计。"""
    _, _, proj = _load_proj(rt, project)
    scene_char_count: dict = {}
    for sc in proj.scenes or []:
        for cn in (sc.characters or []):
            scene_char_count[cn] = scene_char_count.get(cn, 0) + 1
    chars = [
        {"name": c.name,
         "description": (c.description_en or "")[:60],
         "has_views": bool(c.views),
         "appears_in": scene_char_count.get(c.name, 0)}
        for c in (proj.characters or [])
    ]
    return {"ok": True, "count": len(chars),
            "revision": _characters_token(proj.characters), "characters": chars}


def _execute_character_read(rt: AgentRuntime, project: str, arguments: str,
                            instruction, message_id) -> dict:
    """读取单个角色完整 canonical 数据 + 该角色内容指纹（供 character.update 乐观并发）。只读。"""
    args = _parse_args(arguments)
    name = (args.get("character_name") or "").strip()
    if not name:
        raise AgentError("bad_arguments", "缺少 character_name")
    _, _, proj = _load_proj(rt, project)
    char = proj.get_character(name)
    if char is None:
        raise AgentError("not_found", f"角色不存在: {name}")
    appears_in = [{"scene_id": sc.scene_id,
                   "summary": (sc.summary or "")[:60],
                   "shot_count": len(sc.shots or [])}
                  for sc in (proj.scenes or []) if name in (sc.characters or [])]
    views = {v: {"status": a.status if a else "pending",
                 "has_image": bool(a and a.local)}
             for v, a in (char.views or {}).items()}
    return {"ok": True, "revision": _character_token(char), "character_name": char.name,
            "character": {"name": char.name,
                          "description_en": char.description_en or "",
                          "views": views,
                          "appears_in": appears_in}}


def _execute_character_create(rt: AgentRuntime, project: str, arguments: str,
                              instruction: str, message_id: Optional[str]) -> dict:
    """执行 character.create：参数校验 → 重名检查 → 乐观并发（角色列表域）→ 原子写 +
    audit → 【重读 canonical 验证】→ 回执。任何未落盘情形 ok=False。"""
    args = _parse_args(arguments)
    name = (args.get("name") or "").strip()
    desc = args.get("description_en")
    expected_rev = args.get("expected_revision")
    if not name:
        raise AgentError("bad_arguments", "缺少 name（角色名）")
    if not isinstance(desc, str) or not desc.strip():
        raise AgentError("bad_arguments", "description_en 不能为空（角色的英文视觉描述）")
    desc = desc.strip()
    if not isinstance(expected_rev, str) or not expected_rev.strip():
        raise AgentError("bad_arguments",
                         "缺少 expected_revision（应先 character.list 拿到角色版本指纹再传回）")

    pdir, pstore, proj = _load_proj(rt, project)
    current_rev = _characters_token(proj.characters)
    if expected_rev != current_rev:
        current_chars = [{"name": c.name, "description": (c.description_en or "")[:60]}
                         for c in (proj.characters or [])]
        return _base_receipt(
            "character.create", ok=False, target="characters",
            before=len(proj.characters), after=len(proj.characters),
            revision=current_rev, verified=False, error="stale_revision", stale=True,
            expected_revision=expected_rev, current_revision=current_rev,
            current_characters=current_chars,
            note="你读取的角色列表已过期（期间被改动），未创建。上面是当前最新列表。"
                 "请基于最新列表重新判断（确认仍无同名角色），再用新的 expected_revision 调用 character.create。")

    if proj.get_character(name) is not None:
        raise AgentError("character_exists",
                         f"角色 {name} 已存在。如需修改其设定请用 character.update，不要重复创建。")

    before_count = len(proj.characters)
    proj.characters.append(Character(name=name, description_en=desc))
    pstore.save(proj)

    # 写后重读 canonical 验证：新角色确实在磁盘上且描述一致。
    on_disk = _reload(rt, project)
    disk_char = on_disk.get_character(name)
    if disk_char is None or (disk_char.description_en or "") != desc:
        return _base_receipt(
            "character.create", ok=False, target="characters",
            before=before_count, after=len(on_disk.characters),
            revision=_characters_token(on_disk.characters), verified=False,
            error="verify_failed", character_name=name,
            note="创建后重新读取未在 canonical 中找到一致的新角色，未确认创建。"
                 "请用 character.list 读取当前列表后重试。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="character_designer", field="characters",
        before=before_count, after={"name": name, "description_en": desc},
        instruction=instruction, message_id=message_id)
    rt.record_work(project, "change", f"Character Designer 创建角色 {name}",
                   participants=["character_designer"], affected_domains=["characters"],
                   references={"character_name": name}, status="completed")

    new_rev = _characters_token(on_disk.characters)
    return _base_receipt(
        "character.create", ok=True, target=f"character:{name}",
        before=before_count, after=len(on_disk.characters), revision=new_rev, verified=True,
        character_name=name, characters_revision=new_rev,
        note=f"角色 {name} 已创建。请确认结果后用一句话回复用户。")


def _execute_character_update(rt: AgentRuntime, project: str, arguments: str,
                              instruction: str, message_id: Optional[str]) -> dict:
    """执行 character.update：修改【单个已有角色】的 canonical 字段（当前白名单：description_en）。

    流程：参数校验 → 乐观并发（角色级内容指纹）→ 无实际变更 no-op → 内存改字段 →
    原子写 →【重读 canonical 校验】→ audit + 工作记忆 → 回执。
    边界：不改 name（身份，场景/镜头按名字引用）、不碰 views（图片资产）、不碰其它角色、
    不碰 story_text/scenes。"""
    args = _parse_args(arguments)
    name = (args.get("character_name") or "").strip()
    expected_rev = args.get("expected_revision")
    if not name:
        raise AgentError("bad_arguments", "缺少 character_name")
    if not isinstance(expected_rev, str) or not expected_rev.strip():
        raise AgentError("bad_arguments",
                         "缺少 expected_revision（应先 character.read(character_name) 拿到该角色内容指纹再传回）")

    unknown = [k for k in args.keys()
               if k not in ("description_en",) and k not in ("character_name", "expected_revision")]
    if unknown:
        raise AgentError(
            "bad_arguments",
            f"不允许修改字段：{', '.join(unknown)}。character.update 当前只能改 description_en；"
            "不能改角色名、三视图图片、剧本或故事段。")

    changes: dict = {}
    if args.get("description_en") is not None:
        val = args.get("description_en")
        if not isinstance(val, str) or not val.strip():
            raise AgentError("bad_arguments", "description_en 不能为空")
        changes["description_en"] = val.strip()

    pdir, pstore, proj = _load_proj(rt, project)
    char = proj.get_character(name)
    if char is None:
        raise AgentError("not_found", f"角色不存在: {name}")

    before_token = _character_token(char)
    if expected_rev != before_token:
        return _base_receipt(
            "character.update", ok=False, target=f"character:{name}",
            before={"name": char.name, "description_en": char.description_en or ""},
            after={"name": char.name, "description_en": char.description_en or ""},
            revision=before_token, verified=False, error="stale_revision", stale=True,
            character_name=name, expected_revision=expected_rev,
            current_revision=before_token,
            current_character={"name": char.name, "description_en": char.description_en or ""},
            note="你读取的该角色内容已过期（期间被改动），未修改。上面是当前最新内容。"
                 "请基于最新内容重新判断（保留无需变更的部分），再用新的 expected_revision 调用 character.update。")

    real_diff = {}
    for f, v in changes.items():
        cur = getattr(char, f, "") or ""
        if cur != v:
            real_diff[f] = v
    if not real_diff:
        return _base_receipt(
            "character.update", ok=True, target=f"character:{name}",
            before={"name": name, "description_en": char.description_en or ""},
            after={"name": name, "description_en": char.description_en or ""},
            revision=before_token, verified=True, unchanged=True, character_name=name,
            changed=[],
            note="提供的描述与该角色当前描述完全一致，未产生变化。")

    before_desc = char.description_en or ""
    for f, v in real_diff.items():
        setattr(char, f, v)
    pstore.save(proj)

    # 写后重读 canonical 校验：目标角色每个变更字段确实落盘，且角色数量/其它角色未受影响。
    on_disk = _reload(rt, project)
    disk_char = on_disk.get_character(name)
    verified = (disk_char is not None
                and len(on_disk.characters) == len(proj.characters)
                and all((getattr(disk_char, f, "") or "") == v for f, v in real_diff.items()))
    if not verified:
        return _base_receipt(
            "character.update", ok=False, target=f"character:{name}",
            before={"name": name, "description_en": before_desc},
            after={"name": name, "description_en": (disk_char.description_en or "") if disk_char else None},
            revision=_character_token(disk_char), verified=False,
            error="verify_failed", character_name=name,
            note="修改后重新读取发现内容与预期不一致，未确认修改。请用 character.read 读取该角色最新内容后重试。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="character_designer", field=f"character:{name}",
        before={"name": name, "description_en": before_desc},
        after={"name": name, "description_en": disk_char.description_en or ""},
        instruction=instruction, message_id=message_id)
    changed_summary = "、".join(real_diff.keys())
    rt.record_work(project, "change", f"Character Designer 修改角色 {name}（{changed_summary}）",
                   participants=["character_designer"], affected_domains=["characters"],
                   references={"character_name": name}, status="completed")

    new_rev = _character_token(disk_char)
    return _base_receipt(
        "character.update", ok=True, target=f"character:{name}",
        before={"name": name, "description_en": before_desc},
        after={"name": name, "description_en": disk_char.description_en or ""},
        revision=new_rev, verified=True, unchanged=False, character_name=name,
        changed=list(real_diff.keys()),
        note=f"角色 {name} 已更新（{changed_summary}）。请确认结果后用一句话回复用户改了什么。")


def _execute_character_delete(rt: AgentRuntime, project: str, arguments: str,
                              instruction: str, message_id: Optional[str]) -> dict:
    """执行 character.delete：删除一个明确角色（镜像 router 手动删除语义：
    移出 canonical + 清理三视图文件 + 清理 AI 设计草稿）→ 重读验证 → audit → 回执。"""
    args = _parse_args(arguments)
    name = (args.get("character_name") or "").strip()
    if not name:
        raise AgentError("bad_arguments", "缺少 character_name")
    pdir, pstore, proj = _load_proj(rt, project)
    char = proj.get_character(name)
    if char is None:
        raise AgentError("not_found", f"角色不存在: {name}")

    before_count = len(proj.characters)
    before_desc = char.description_en or ""
    proj.characters = [c for c in proj.characters if c.name != name]
    pstore.save(proj)

    # 磁盘副作用：三视图文件 + AI 设计草稿（与 router 手动删除一致）。
    removed_files = _delete_view_files(pdir, name)
    draft_removed = _delete_design_draft(pdir, name)

    # 写后重读 canonical 验证：角色确实消失，数量减一。
    on_disk = _reload(rt, project)
    gone = on_disk.get_character(name) is None and len(on_disk.characters) == before_count - 1
    if not gone:
        return _base_receipt(
            "character.delete", ok=False, target=f"character:{name}",
            before=before_count, after=len(on_disk.characters),
            revision=_characters_token(on_disk.characters), verified=False,
            error="verify_failed", character_name=name,
            note="删除后重新读取发现角色仍在，未确认删除。请用 character.list 确认当前列表后重试。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="character_designer", field=f"delete_character:{name}",
        before={"name": name, "description_en": before_desc,
                "removed_files": removed_files, "draft_removed": draft_removed},
        after=[], instruction=instruction, message_id=message_id)
    rt.record_work(project, "change", f"Character Designer 删除角色 {name}",
                   participants=["character_designer"], affected_domains=["characters"],
                   references={"character_name": name}, status="completed")

    new_rev = _characters_token(on_disk.characters)
    return _base_receipt(
        "character.delete", ok=True, target=f"character:{name}",
        before=before_count, after=len(on_disk.characters), revision=new_rev, verified=True,
        character_name=name, removed_files=removed_files, draft_removed=draft_removed,
        characters_revision=new_rev,
        note=f"角色 {name} 已删除（清理 {removed_files} 个三视图文件"
             + ("，AI 设计草稿已清理）" if draft_removed else "）")
             + "。请用一句话回复用户删除了什么。")


def _make_views_emit(name: str, event_cb):
    """把 handler 的 emit(view, msg, pct) 转成流式 UI 进度事件（无 event_cb 时静默）。"""
    async def _emit(view: str, msg: str, pct: int) -> None:
        if event_cb:
            try:
                event_cb({"type": "views.progress", "character_name": name,
                          "view": view, "message": msg, "pct": pct})
            except Exception:
                pass
    return _emit


def _views_local_ok(pdir, asset) -> bool:
    """Asset.local 存在性检查。handler 落盘的 local 可能是三种形态（2026-08-30 真机勘误：
    get_project_manager 返回相对路径 → handler 写出的 local 形如 'projects/{p}/output/...'，
    相对【backend 进程 cwd】而非 pdir）：
      1) 绝对路径 → 直接用；
      2) 'projects/...' 相对路径 → 相对 backend cwd（项目根）；
      3) 其它相对路径 → 相对 pdir。"""
    local = getattr(asset, "local", None)
    if not local:
        return False
    p = Path(local)
    if p.is_absolute():
        return p.exists()
    norm = str(local).replace("\\", "/")
    if norm.startswith("projects/"):
        return (Path.cwd() / norm).exists()
    return (Path(pdir) / local).exists()


def _verify_generated_views(pdir, name: str) -> Optional[dict]:
    """写后重读 canonical：该角色三个视图都 status=done 且文件真实存在。
    返回 {views: {view: local}}（仅包含就绪视图），front/side/back 齐全才算 verified。"""
    try:
        char = ProjectStore(pdir).load().get_character(name)
    except Exception:
        return None
    if char is None or not char.views:
        return None
    views = {}
    for v in ("front", "side", "back"):
        a = char.views.get(v)
        if a is not None and getattr(a, "status", None) == "done" and _views_local_ok(pdir, a):
            views[v] = a.local
    if len(views) != 3:
        return None
    return {"views": views}


async def _generate_one_char_views(rt: AgentRuntime, project: str, name: str,
                                   event_cb=None) -> dict:
    """单角色三视图生成：外观签名 → 复用 AI-7C handler_character_views（链式生成+抠图+
    project_write_lock 写回）→ 重读 canonical 验证。任何失败/超时返回 ok=False 的分项结果，
    不抛异常（批量部分失败不整批失败）。"""
    pdir = rt._resolve_project_dir(project)
    try:
        proj = ProjectStore(pdir).load()
    except Exception as e:
        return {"character_name": name, "ok": False, "verified": False,
                "error": f"项目读取失败: {e}"}
    char = proj.get_character(name)
    if char is None:
        return {"character_name": name, "ok": False, "verified": False,
                "error": f"角色 {name} 不存在"}
    if not (char.description_en or "").strip():
        return {"character_name": name, "ok": False, "verified": False,
                "error": f"角色 {name} 的 description_en 为空，先完善文本设定再生成图片"}

    # 外观签名 = sha256(description_en + art_style + visual_profile_fingerprint)：
    # 描述未变且已有图 → handler 自动跳过（不重复生成）；描述或项目视觉定义已改 →
    # sig 变化 → 全部重新生成（一致性语义，Phase B §3.2：profile 参与风格真相）。
    profile_fp = _visual_profile_fingerprint_or_empty(pdir)
    sig = character_appearance_sig(char.description_en or "", proj.style.art_style or "",
                                   profile_fp)
    job = SimpleNamespace(project=project,
                          params={"character_name": name, "appearance_sig": sig})
    try:
        await asyncio.wait_for(
            handler_character_views(job, _make_views_emit(name, event_cb)),
            timeout=VIEWS_PER_CHAR_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"character_name": name, "ok": False, "verified": False, "timeout": True,
                "error": f"生成超时（单角色上限 {VIEWS_PER_CHAR_TIMEOUT_S}s），未确认完成"}
    except Exception as e:
        return {"character_name": name, "ok": False, "verified": False,
                "error": str(e)[:200]}

    verified = _verify_generated_views(pdir, name)
    if verified is None:
        return {"character_name": name, "ok": False, "verified": False,
                "error": "生成结束后重读 canonical 未确认三视图完整落盘"}
    return {"character_name": name, "ok": True, "verified": True, **verified}


async def _execute_character_generate_views(rt: AgentRuntime, project: str, arguments: str,
                                            instruction: str, message_id: Optional[str],
                                            event_cb=None, cancel_event=None) -> dict:
    """执行 character.generate_views：最多 3 个角色【并发】生成三视图（复用 AI-7C
    handler），总超时 VIEWS_TOTAL_TIMEOUT_S；响应取消（已完成的保留）；部分失败不整批
    失败；写后重读 canonical 验证 + audit + 工作记忆 + 统一回执。obs 不含图片 base64，
    只含 local 路径（图片不进模型上下文）。"""
    args = _parse_args(arguments)
    names_raw = _loose_json(args.get("character_names"))
    if not isinstance(names_raw, list) or not names_raw:
        raise AgentError("bad_arguments", "character_names 必须是非空数组（1~3 个角色名）")
    names: list = []
    for i, v in enumerate(names_raw):
        if not isinstance(v, str) or not v.strip():
            raise AgentError("bad_arguments", f"character_names[{i}] 必须是非空角色名")
        n = v.strip()
        if n in names:
            raise AgentError("bad_arguments", f"character_names 含重复角色名：{n}")
        names.append(n)
    if len(names) > 3:
        raise AgentError("bad_arguments",
                         f"一次最多生成 3 个角色的三视图（收到 {len(names)} 个）。"
                         "请先完成文本设定，分批生成。")

    pdir = rt._resolve_project_dir(project)
    # 预检：目标存在（任一不存在直接报错，不启动任何生成——atomic 语义）
    proj = ProjectStore(pdir).load()
    for n in names:
        if proj.get_character(n) is None:
            raise AgentError("not_found", f"角色不存在: {n}")

    started = time.time()
    tasks = {n: asyncio.ensure_future(
        _generate_one_char_views(rt, project, n, event_cb=event_cb)) for n in names}
    results: list = []
    cancelled = False
    deadline = started + VIEWS_TOTAL_TIMEOUT_S
    pending = set(tasks.values())
    while pending:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        done, pending = await asyncio.wait(
            pending, timeout=2.0, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            try:
                results.append(t.result())
            except Exception as e:   # 理论不达（分项已兜底），保险
                results.append({"character_name": "?", "ok": False, "verified": False,
                                "error": str(e)[:200]})
        if time.time() > deadline:
            break
    # 超时/取消：未完成的任务取消并标记
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
        done_names = {r.get("character_name") for r in results}
        for n in names:
            if n not in done_names:
                results.append({
                    "character_name": n, "ok": False, "verified": False,
                    "cancelled": cancelled, "timeout": not cancelled,
                    "error": ("已取消（用户停止）" if cancelled
                              else f"总超时（>{VIEWS_TOTAL_TIMEOUT_S}s），未确认完成")})
    results.sort(key=lambda r: names.index(r.get("character_name"))
                 if r.get("character_name") in names else 99)

    ok_chars = [r for r in results if r.get("ok") and r.get("verified")]
    generated = [{"character_name": r["character_name"], "views": r["views"]}
                 for r in ok_chars]
    elapsed = round(time.time() - started, 1)

    # 全部失败/未完成 → ok=False；任一成功 → ok=True（部分成功如实回报）
    if cancelled:
        return _base_receipt(
            "character.generate_views", ok=False, target=",".join(names),
            before=None, after=None, revision=None, verified=False,
            cancelled=True, character_names=names, generated=generated,
            results=results, elapsed_s=elapsed,
            note="生成已取消（用户停止）。已完成的部分已保留在角色数据中。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="character_designer", field="character_views",
        before=None, after={"character_names": names,
                            "generated": [g["character_name"] for g in generated]},
        instruction=instruction, message_id=message_id)
    if generated:
        rt.record_work(project, "change",
                       f"Character Designer 生成 {len(generated)} 个角色三视图",
                       participants=["character_designer"], affected_domains=["characters"],
                       references={"character_names": [g["character_name"] for g in generated]},
                       status="completed")

    ok_all = len(ok_chars) == len(names)
    parts = []
    for r in results:
        n = r.get("character_name")
        if r.get("ok"):
            parts.append(f"{n} ✓")
        else:
            parts.append(f"{n} ✗（{r.get('error') or '失败'}）")
    return _base_receipt(
        "character.generate_views", ok=bool(ok_chars), target=",".join(names),
        before=None, after=None,
        revision=_characters_token(ProjectStore(pdir).load().characters),
        verified=ok_all, character_names=names, generated=generated,
        results=results, elapsed_s=elapsed,
        note=("；".join(parts) + f"。耗时 {elapsed}s。"
              + ("图片已写回角色数据并在窗口展示。"
                 if ok_chars else "没有角色完成生成，角色数据未被改动。")))


def _execute_visual_profile_read(rt: AgentRuntime, project: str, arguments: str,
                                 instruction, message_id) -> dict:
    """读取 ProjectVisualProfile（Phase B §3.1：高信号紧凑返回——内容+指纹+revision+
    specified 状态；不返回无关项目大块 JSON）。只读；profile 由用户拥有，Agent 无 mutation。
    Phase C1 Work B：实现委托共享层 production_context（与 Director 同一投影/schema，
    消除双实现）；本工具的 schema 与对外行为不变。"""
    pdir = rt._resolve_project_dir(project)
    from .production_context import read_visual_profile_projection
    return read_visual_profile_projection(pdir)


# ============ Production Resources 只读（Phase B §7：inventory / read）============
_RESOURCE_KIND_TO_READER = {
    "clothing": "read_clothing", "look": "read_look", "prop": "read_prop",
    "composition": "read_composition", "reference_asset": "read_reference_asset",
}


# ============ Production Resources mutation（Phase B §8/§14：任务型工具）============
# 原则（§8/§19）：
#   - 所有写入走 ResourceWriteService / review service / approval service，绝不自己拼
#     JSON 写盘、绝不直连图片 provider；
#   - 一次工具调用 = 一次明确生产尝试（generate→review→approve_if_pass 链一次跑完），
#     review needs_fix/uncertain 绝不 approve，也绝不在工具内偷偷重试生成；
#   - OCC：expected_resources_revision 原样透传给 write service（create 对照 document
#     revision、update 对照目标资源 revision——由 svc 判定 stale，工具不无声读新覆盖）；
#     链内后续阶段（generate→approve）使用上一阶段真实回执返回的 revision 衔接；
#   - changed_resources 只从 ok+verified 的真实 service 回执聚合（Producer
#     _derive_changed_refs 的确定性来源）。
#
# receipt 分层语义：ok = 本次生产尝试是否有真实 canonical 变更（部分成功也算 True，
# 与 character.generate_views 同语义）；generation/review/approval 各阶段结论独立字段
# 如实呈现——「生成失败但 Look 已创建」必须同时可见，不允许任何一层吞掉。

# §14 prompt-reference 冲突信号（高信号规则，不做 AI judge）。英文 appearance 文本。
_LOOK_CONFLICT_PATTERNS = (
    (r"face\s+(?:is\s+|are\s+)?(?:hidden|obscured|concealed|not\s+visible|invisible|covered)",
     "face_hidden", "描述要求面部不可见"),
    (r"\bmask(?:ed)?\b|\bface[- ]?cover(?:ing)?\b", "mask",
     "描述包含面具/面部遮盖"),
    (r"\bsilhouette\s+(?:figure|form|only)\b|\bas\s+a\s+silhouette\b", "silhouette",
     "描述要求剪影形态"),
    (r"\bfaceless\b|\bno\s+face\b|\bwithout\s+a\s+face\b", "faceless",
     "描述要求无脸"),
    (r"\b(?:head|face)\s+fully\s+covered\b|\bfully\s+(?:veiled|wrapped)\b", "fully_covered",
     "描述要求头/面完全覆盖"),
    (r"\bhood\b[^.]{0,40}\b(?:shadow|shadows|darkness|hidden|obscured)\b|"
     r"\b(?:shadow|shadows|darkness)\b[^.]{0,30}\b(?:hides?|covering)\s+(?:the\s+)?face\b",
     "face_in_shadow", "描述要求阴影遮面"),
)


def _look_reference_conflicts(appearance_text: str) -> list:
    """Look 外观描述 vs Character identity reference（三视图，脸可见）的冲突预检。
    返回 [{signal, label}]（空 = 无冲突）。确定性 0 LLM——语义判断由 Agent 自己做。"""
    t = (appearance_text or "").lower()
    out = []
    for pat, signal, label in _LOOK_CONFLICT_PATTERNS:
        if signal in {s for s, _ in out}:
            continue
        if re.search(pat, t):
            out.append({"signal": signal, "label": label})
    return out


def _chg(changes: list, seen: set, kind: str, rid) -> None:
    """把一条 ok+verified 的真实变更加入 changed_resources（去重保序）。"""
    if not rid:
        return
    key = (kind, rid)
    if key not in seen:
        seen.add(key)
        changes.append({"kind": kind, "id": rid})


def _resource_stale_receipt(op: str, svc_receipt: dict, op_summary: str) -> dict:
    """write service stale receipt → Agent 域 stale 回执（形状对齐 character 域 stale）。"""
    return _base_receipt(
        op, ok=False, target=svc_receipt.get("target"),
        before=None, after=None,
        revision=svc_receipt.get("current_revision"), verified=False,
        error="stale_revision", stale=True,
        expected_revision=svc_receipt.get("expected_revision"),
        current_revision=svc_receipt.get("current_revision"),
        current=svc_receipt.get("current"),
        changed_resources=[], op_summary=op_summary,
        note="你读取的资源状态已过期（期间被改动），本次未写入。请 resource.inventory / "
             "resource.read 读取最新状态与 revision 后重新判断再试。")


def _resolve_pdir(rt: AgentRuntime, project: str):
    return rt._resolve_project_dir(project)


async def _svc(ws, method: str, **kw) -> dict:
    """调用 ResourceWriteService 方法（全部返回结构化 receipt，不抛业务异常）。"""
    return await getattr(ws, method)(**kw)


# ---- Phase C1 Work A（27.md §3）：per-resource review failure guard ----
# 28.md §11（Phase C2）共享化：guard 抽取到 src/agents/review_retry_guard.py，
# Resource Agent / Scene Agent 共用同一套（不复制两套）。本文件保留原私有名别名，
# 行为/状态/测试契约不变（进程内存、per-turn、绝不写 canonical）。
from .review_retry_guard import (REVIEW_RETRY_STATE as _REVIEW_RETRY_STATE,
                                 REVIEW_RETRY_LIMIT as _REVIEW_RETRY_LIMIT,
                                 reset_review_retry_state as _reset_review_retry_state,
                                 review_basis_sig as _review_basis_sig,
                                 review_retry_check as _review_retry_check,
                                 review_retry_record as _review_retry_record)




def _execute_resource_inventory(rt: AgentRuntime, project: str, arguments: str,
                                instruction, message_id) -> dict:
    """列出项目生产资源轻量地图（Phase B §7.2）。复用 ResourceService 的紧凑 summary
    （无 base64/无长 prompt/无 generation history）+ document_revision。只读。"""
    args = _parse_args(arguments)
    flt = (args.get("filter") or "all").strip() or "all"
    type_map = {"all": None, "clothing": "clothing", "look": "look",
                "prop": "prop", "composition": "composition"}
    if flt not in type_map:
        raise AgentError("bad_arguments",
                         f"filter 必须是 all|clothing|look|prop|composition，得到: {flt}")
    from src.resources.service import ResourceService
    from src.resources.models import document_revision
    pdir = rt._resolve_project_dir(project)
    svc = ResourceService(pdir, project)
    items = svc.list_resources(type_map[flt])
    return {"ok": True, "operation": "resource.inventory", "filter": flt,
            "count": len(items),
            "resources": items,
            "document_revision": document_revision(svc.store.load()),
            "note": "status=approved 的资源才可用于生产 binding；未生成/未审批的不能直接进"
                    "分镜。资源 id 用于 resource.read 与资源类写工具。"}


def _execute_resource_read(rt: AgentRuntime, project: str, arguments: str,
                           instruction, message_id) -> dict:
    """读取一个生产资源高信号详情（Phase B §7.3）。kind 明确 enum，未知/不存在 fail closed。
    只读；绝不返回 base64（ResourceService 本就不返回）。"""
    args = _parse_args(arguments)
    kind = (args.get("kind") or "").strip()
    rid = (args.get("resource_id") or "").strip()
    if kind not in _RESOURCE_KIND_TO_READER:
        raise AgentError("bad_arguments",
                         f"kind 必须是 clothing|look|prop|composition|reference_asset，得到: {kind or '（空）'}")
    if not rid:
        raise AgentError("bad_arguments", "缺少 resource_id")
    from src.resources.service import ResourceService
    from src.resources.models import document_revision
    pdir = rt._resolve_project_dir(project)
    svc = ResourceService(pdir, project)
    detail = getattr(svc, _RESOURCE_KIND_TO_READER[kind])(rid)
    if detail is None:
        raise AgentError("not_found", f"{kind} 不存在: {rid}（先 resource.inventory 核对 id）")
    return {"ok": True, "operation": "resource.read", "kind": kind,
            "resource_id": rid, "resource": detail,
            "document_revision": document_revision(svc.store.load())}


# ---- §8.1 resource.clothing.upsert ----
async def _execute_resource_clothing_upsert(rt: AgentRuntime, project: str, arguments: str,
                                            instruction, message_id) -> dict:
    """创建或更新 Clothing（§8.1）。无 clothing_id = 创建（OCC 对照 document revision）；
    带 clothing_id = 更新（OCC 对照该 clothing revision）。绝不开新图。"""
    args = _parse_args(arguments)
    clothing_id = (args.get("clothing_id") or "").strip() or None
    name = (args.get("name") or "").strip()
    expected = args.get("expected_resources_revision")
    if not expected or not isinstance(expected, str):
        raise AgentError("bad_arguments",
                         "缺少 expected_resources_revision（应传 resource.inventory / "
                         "resource.read 返回的对应 revision：创建=document_revision，"
                         "更新=该 clothing 的 revision）")
    if not clothing_id and not name:
        raise AgentError("bad_arguments", "创建 Clothing 需要 name")
    patch = {}
    for f in ("name", "description", "design_traits"):
        if args.get(f) is not None:
            patch[f] = str(args[f])
    for f in ("materials", "colors", "tags"):
        if args.get(f) is not None:
            v = _loose_json(args[f])
            if not isinstance(v, list):
                raise AgentError("bad_arguments", f"{f} 必须是字符串数组")
            patch[f] = v
    if clothing_id and not patch:
        raise AgentError("bad_arguments", "更新 Clothing 需要至少一个要改的字段")

    from src.resources.write_service import ResourceWriteService
    pdir = _resolve_pdir(rt, project)
    ws = ResourceWriteService(pdir, project)
    changes: list = []
    seen: set = set()
    if clothing_id is None:
        r = await _svc(ws, "create_clothing", name=name,
                       description=str(patch.get("description") or ""),
                       design_traits=str(patch.get("design_traits") or ""),
                       materials=patch.get("materials"), colors=patch.get("colors"),
                       tags=patch.get("tags"),
                       expected_document_revision=expected)
        if r.get("error") == "stale_revision":
            return _resource_stale_receipt("resource.clothing.upsert", r,
                                           "创建 Clothing（stale，未写入）")
        if not r.get("ok"):
            return _base_receipt("resource.clothing.upsert", ok=False,
                                 target=None, before=None, after=None,
                                 revision=None, verified=False,
                                 error=r.get("error") or "create_failed",
                                 detail=r.get("detail"), changed_resources=[],
                                 op_summary="创建 Clothing 失败",
                                 note=f"Clothing 未创建：{r.get('detail') or r.get('error')}")
        cid = r["target"]
        _chg(changes, seen, "clothing", cid)
        return _base_receipt("resource.clothing.upsert", ok=True, target=cid,
                             before=None, after=r.get("after"),
                             revision=r.get("revision"), verified=bool(r.get("verified")),
                             clothing_id=cid, resource_kind="clothing", resource_id=cid,
                             changed_resources=changes,
                             resources_document_revision=r.get("document_revision_after"),
                             op_summary=f"创建服装 {name}（{cid}）",
                             note=f"Clothing {cid}（{name}）已创建。用 resource.read(kind=clothing) "
                                  "查看详情。")
    # update 路径
    r = await _svc(ws, "update_clothing", clothing_id=clothing_id, patch=patch,
                   expected_revision=expected)
    if r.get("error") == "stale_revision":
        return _resource_stale_receipt("resource.clothing.upsert", r,
                                       f"更新 Clothing {clothing_id}（stale，未写入）")
    if r.get("error") == "not_found":
        raise AgentError("not_found",
                         f"Clothing 不存在: {clothing_id}（先 resource.inventory 核对 id）")
    if not r.get("ok"):
        return _base_receipt("resource.clothing.upsert", ok=False, target=clothing_id,
                             before=None, after=None, revision=None, verified=False,
                             error=r.get("error") or "update_failed",
                             detail=r.get("detail"), changed_resources=[],
                             op_summary="更新 Clothing 失败",
                             note=f"Clothing 未更新：{r.get('detail') or r.get('error')}")
    if r.get("unchanged"):
        return _base_receipt("resource.clothing.upsert", ok=True, target=clothing_id,
                             before=r.get("before"), after=r.get("after"),
                             revision=r.get("revision"), verified=True, unchanged=True,
                             clothing_id=clothing_id, resource_kind="clothing",
                             resource_id=clothing_id, changed_resources=[],
                             resources_document_revision=r.get("document_revision_after"),
                             op_summary="更新 Clothing（无变化）",
                             note="提供的内容与当前完全一致，未产生变化。")
    _chg(changes, seen, "clothing", clothing_id)
    return _base_receipt("resource.clothing.upsert", ok=True, target=clothing_id,
                         before=r.get("before"), after=r.get("after"),
                         revision=r.get("revision"), verified=bool(r.get("verified")),
                         clothing_id=clothing_id, resource_kind="clothing",
                         resource_id=clothing_id, changed_resources=changes,
                         resources_document_revision=r.get("document_revision_after"),
                         op_summary=f"更新服装 {clothing_id}（{'、'.join(patch.keys())}）",
                         note=f"Clothing {clothing_id} 已更新（{'、'.join(patch.keys())}）。")


# ---- §8.2 resource.look.prepare ----
async def _execute_resource_look_prepare(rt: AgentRuntime, project: str, arguments: str,
                                         instruction, message_id) -> dict:
    """CharacterLook 的一次生产准备尝试（§8.2）：create/update → generate → review →
    approve_if_pass。一次调用 = 一次明确生产尝试；needs_fix 如实返回，由 Agent 决定
    下一步（调整描述/改 Look/再生成/报告 blocker），工具内绝不偷偷重试生成。"""
    args = _parse_args(arguments)
    character_name = (args.get("character_name") or "").strip()
    look_id = (args.get("look_id") or "").strip() or None
    label = str(args.get("label") or "")
    clothing_id = (args.get("clothing_id") or "").strip() or None
    appearance_description = str(args.get("appearance_description") or "")
    appearance_delta = str(args.get("appearance_delta") or "")
    expected = args.get("expected_resources_revision")
    approval_mode = (args.get("approval_mode") or "review_only").strip()
    conflict_ack = bool(args.get("conflict_acknowledged"))
    if not character_name:
        raise AgentError("bad_arguments", "缺少 character_name")
    if approval_mode not in ("review_only", "approve_if_pass"):
        raise AgentError("bad_arguments",
                         "approval_mode 必须是 review_only | approve_if_pass")
    if not expected or not isinstance(expected, str):
        raise AgentError("bad_arguments",
                         "缺少 expected_resources_revision（创建=document_revision，"
                         "更新=该 look 的 revision，来自 resource.inventory/read）")
    if look_id is None and not appearance_description.strip():
        raise AgentError("bad_arguments", "新建 Look 需要 appearance_description（外观描述）")

    # ---- §14 预检：appearance 文本 vs 身份参考（三视图脸可见）冲突 ----
    conflict_text = " ".join(x for x in (appearance_description, appearance_delta) if x)
    conflicts = _look_reference_conflicts(conflict_text)
    if conflicts and not conflict_ack:
        return _base_receipt(
            "resource.look.prepare", ok=False, target=None, before=None, after=None,
            revision=None, verified=False, error="reference_conflict", stale=False,
            conflicts=conflicts, changed_resources=[], op_summary="Look 准备被预检拦截",
            note=("外观描述与角色 identity reference（三视图，面部可见）存在已知冲突："
                  + "；".join(f"[{c['signal']}] {c['label']}" for c in conflicts)
                  + "。按冲突处理流程：①向用户说明冲突；②判断是否需要新的身份参考"
                    "（如遮面版三视图——当前系统没有该生成能力，需用户处理）；③或调整 Look "
                    "目标描述；④或用户确认坚持后，带 conflict_acknowledged=true 重试。"
                    "不要无意义连续重试生成——模型无法同时满足矛盾约束。"))

    from src.resources.write_service import ResourceWriteService
    from src.resources.models import look_revision
    from src.resources import look_review as look_review_mod
    pdir = _resolve_pdir(rt, project)
    ws = ResourceWriteService(pdir, project)
    changes: list = []
    seen: set = set()

    # ---- Phase C1 Work A：review failure guard 入口检查（§3.1）----
    retry_resource_key = look_id or f"new:{character_name}:{(label or '').strip()}"
    retry_basis = _review_basis_sig(appearance_description=appearance_description,
                                    appearance_delta=appearance_delta,
                                    clothing_id=clothing_id)
    blocked = _review_retry_check(project, message_id, retry_resource_key,
                                  retry_basis, "resource.look.prepare")
    if blocked is not None:
        return blocked

    # ---- 阶段1：create / update ----
    if look_id is None:
        # 复用护栏（真机 E2E 发现的失败模式矫正）：同 character+label 的 Look 已存在时
        # 拒绝盲目 create，结构化引导走 look_id 更新/再生成——防止审查失败后无限重建同名
        # 资源（26.md §8.2「明确区分 create 和 update」的确定性执行）。
        existing = ws.store.load().looks
        dup = next((lk for lk in existing.values()
                    if lk.character_name == character_name
                    and (label or "").strip()
                    and (lk.label or "") == (label or "")), None)
        if dup is not None:
            return _base_receipt(
                "resource.look.prepare", ok=False, target=dup.look_id,
                before=None, after=None, revision=None, verified=False,
                error="look_label_exists", stale=False,
                look_id=dup.look_id, existing_look_id=dup.look_id,
                existing_look_revision=None,
                changed_resources=[], op_summary="Look 创建被拒（同名已存在）",
                note=f"角色 {character_name} 已有同名 Look「{label or '(无标签)'}」"
                     f"（look_id={dup.look_id}）。绝不要重复创建同名资源：要更新或再生成它，"
                     "就带 look_id 调用本工具（expected_resources_revision 传该 look 的 "
                     "revision，来自 resource.read(kind=look)；纯再生成可不传描述字段）。")
        r = await _svc(ws, "create_look", character_name=character_name, label=label,
                       clothing_id=clothing_id,
                       appearance_description=appearance_description,
                       appearance_delta=appearance_delta,
                       expected_document_revision=expected)
        if r.get("error") == "stale_revision":
            return _resource_stale_receipt("resource.look.prepare", r,
                                           "创建 Look（stale，未写入）")
        if not r.get("ok"):
            return _base_receipt("resource.look.prepare", ok=False, target=None,
                                 before=None, after=None, revision=None, verified=False,
                                 error=r.get("error") or "create_failed",
                                 detail=r.get("detail"), changed_resources=[],
                                 op_summary="创建 Look 失败",
                                 note=f"Look 未创建：{r.get('detail') or r.get('error')}")
        look_id = r["target"]
        look_rev = r["revision"]
        _chg(changes, seen, "look", look_id)
    else:
        patch = {}
        for f in ("label", "clothing_id", "appearance_description", "appearance_delta"):
            if args.get(f) is not None:
                patch[f] = args.get(f)
        if patch:
            r = await _svc(ws, "update_look", look_id=look_id, patch=patch,
                           expected_revision=expected)
            if r.get("error") == "stale_revision":
                return _resource_stale_receipt("resource.look.prepare", r,
                                               f"更新 Look {look_id}（stale，未写入）")
            if r.get("error") == "not_found":
                raise AgentError("not_found",
                                 f"Look 不存在: {look_id}（先 resource.inventory 核对 id）")
            if not r.get("ok"):
                return _base_receipt("resource.look.prepare", ok=False, target=look_id,
                                     before=None, after=None, revision=None, verified=False,
                                     error=r.get("error") or "update_failed",
                                     detail=r.get("detail"), changed_resources=[],
                                     op_summary="更新 Look 失败",
                                     note=f"Look 未更新：{r.get('detail') or r.get('error')}")
            look_rev = r["revision"]
            if r.get("verified") and not r.get("unchanged"):
                _chg(changes, seen, "look", look_id)
        else:
            # 纯再生成尝试：expected 必须就是当前 look revision（Agent 从 resource.read 拿）
            look_rev = expected

    # ---- 阶段2：generate（一次）----
    gen = await _svc(ws, "generate_look_candidates", look_id=look_id,
                     expected_revision=look_rev, candidate_count=1)
    gen_ok = bool(gen.get("ok") and gen.get("status") == "committed")
    gen_summary = {"ok": gen_ok, "status": gen.get("status"), "error": gen.get("error"),
                   "detail": (gen.get("detail") or "")[:300],
                   "generation_id": gen.get("generation_id"),
                   "candidate_asset_ids": gen.get("candidate_asset_ids") or []}
    if not gen_ok:
        # Look 本体已创建/更新（真实变更），生成失败如实报告——部分成功语义。
        return _base_receipt(
            "resource.look.prepare", ok=bool(changes), target=look_id,
            before=None, after=None, revision=None,
            verified=bool(changes), look_id=look_id, resource_kind="look",
            resource_id=look_id, changed_resources=changes,
            generation=gen_summary, review=None, approval=None,
            approval_mode=approval_mode,
            op_summary=(f"Look {look_id} 已创建/更新，但候选图生成失败"
                        if changes else f"Look {look_id} 生成失败（无变更）"),
            note=(f"生成未完成：{gen.get('error')}（{(gen.get('detail') or '')[:200]}）。"
                  if not gen_ok else "")
            + "这是本次唯一一次生成尝试；如需再试请再次调用本工具（同一错误连续出现时"
              "按 repeat-failure 纪律停止，不要反复烧图）。")
    for aid in gen.get("candidate_asset_ids") or []:
        _chg(changes, seen, "reference_asset", aid)

    # ---- 阶段3：review（正式 vision review）----
    new_assets = gen.get("candidate_asset_ids") or []
    review = await look_review_mod.review_look_candidates(pdir, look_id,
                                                          asset_ids=list(new_assets),
                                                          agnes_client=None)
    rev_rows = {cr.get("asset_id"): cr for cr in (review.get("candidate_reviews") or [])}
    target_asset = new_assets[0] if new_assets else None
    cr = rev_rows.get(target_asset) or {}
    review_overall = cr.get("overall")
    review_summary = {"overall": review_overall,
                      "asset_id": target_asset,
                      "identity_consistency": cr.get("identity_consistency"),
                      "costume_consistency": cr.get("costume_consistency"),
                      "visual_defects": cr.get("visual_defects"),
                      "project_style_consistency": cr.get("project_style_consistency"),
                      "summary": cr.get("summary") or review.get("detail") or "",
                      "review_id": review.get("review_id"),
                      "status": review.get("status")}

    # ---- 阶段4：approve_if_pass（review pass 是证据，approve 是显式 mutation）----
    approval_summary = {"attempted": False, "ok": None, "status": None}
    if approval_mode == "approve_if_pass":
        approval_summary["attempted"] = True
        if review.get("status") != "completed":
            approval_summary["status"] = "review_failed"
        elif review_overall != "pass":
            approval_summary["status"] = f"review_{review_overall}_no_approve"
        else:
            look_rev_now = look_revision(ws.store.load().looks[look_id])
            ar = await _svc(ws, "approve_look_reference", look_id=look_id,
                            asset_id=target_asset, expected_revision=look_rev_now)
            approval_summary["ok"] = bool(ar.get("ok"))
            approval_summary["status"] = (ar.get("error") or "approved")
            if ar.get("ok") and ar.get("verified") and not ar.get("unchanged"):
                _chg(changes, seen, "look", look_id)

    # ---- Phase C1 Work A：审查结果记账（needs_fix 连续计数 / pass 清零）----
    # 记账用 create/update 后的确定 look_id（后续重试按 reuse guard 引导会带同一 id，
    # 入口检查的 key 与记账 key 对齐）。
    _review_retry_record(project, message_id, look_id, retry_basis,
                         review_overall if review.get("status") == "completed" else None)

    approved = approval_summary.get("status") == "approved"
    return _base_receipt(
        "resource.look.prepare", ok=bool(changes), target=look_id,
        before=None, after=None, revision=None, verified=bool(changes),
        look_id=look_id, resource_kind="look", resource_id=look_id,
        changed_resources=changes,
        generation=gen_summary, review=review_summary, approval=approval_summary,
        approval_mode=approval_mode,
        op_summary=(f"Look {look_id} 生成完成，review={review_overall}"
                    + ("，已批准" if approved else "，未批准")),
        note=(f"Look {look_id}：生成 committed；审查 overall={review_overall}"
              + (f"（{review_summary.get('summary') or '无备注'}）" if review_summary.get("summary") else "")
              + ("；approval_mode=approve_if_pass 且审查通过 → 已正式批准（可直接进生产绑定）。"
                 if approved else
                 "；审查未通过 → 未批准（approve 需要真实 pass 证据）。请依据审查原因决定："
                 "调整描述后再次调用本工具，或向用户报告 blocker；不要在未改描述的情况下重试。"
                 if review_overall != "pass" else
                 "；review_only 模式不自动批准，等待用户验收。")))


# ---- Phase D §35：resource.look.approve（显式批准已有 pass 候选）----
async def _execute_resource_look_approve(rt: AgentRuntime, project: str, arguments: str,
                                         instruction, message_id) -> dict:
    """显式批准 Look 的某张已有候选参考图（复用正式 service approve_look_reference，
    与 HTTP /looks/{id}/approve 同一语义：必须有 overall=pass 审查证据，OCC 保护）。
    不生成图片、不改描述——纯审批 mutation；失败如实返回（not_found/不属该 look/
    review_not_passed/stale_revision）。"""
    args = _parse_args(arguments)
    look_id = (args.get("look_id") or "").strip()
    asset_id = (args.get("asset_id") or "").strip()
    expected = args.get("expected_resources_revision")
    set_cover = args.get("set_cover")
    if not look_id or not asset_id:
        raise AgentError("bad_arguments", "缺少 look_id 或 asset_id")
    if not expected or not isinstance(expected, str):
        raise AgentError("bad_arguments",
                         "缺少 expected_resources_revision（该 look 的 revision，"
                         "来自 resource.read）")
    from src.resources.write_service import ResourceWriteService
    pdir = _resolve_pdir(rt, project)
    ws = ResourceWriteService(pdir, project)
    r = await ws.approve_look_reference(
        look_id, asset_id, expected_revision=expected,
        set_cover=set_cover if isinstance(set_cover, bool) else None)
    if not r.get("ok"):
        return _base_receipt(
            "resource.look.approve", ok=False, target=look_id, before=None,
            after=None, revision=None, verified=False, look_id=look_id,
            resource_kind="look", resource_id=look_id, changed_resources=[],
            error=r.get("error"), stale=r.get("error") == "stale_revision",
            op_summary=f"Look {look_id} 批准被拒（{r.get('error')}）",
            note=(r.get("detail") or ""))
    approved = ((r.get("after") or {}).get("approved_reference_asset_ids")) or []
    return _base_receipt(
        "resource.look.approve", ok=True, target=look_id, before=None,
        after=None, revision=r.get("revision"), verified=bool(r.get("verified")),
        look_id=look_id, resource_kind="look", resource_id=look_id,
        changed_resources=[{"kind": "look", "id": look_id}],
        op_summary=f"Look {look_id} 已批准候选 {asset_id}",
        note=f"approved_reference_asset_ids={approved}；该 Look 现在可进入生产绑定。")


# ---- §8.3 resource.prop.register ----
async def _execute_resource_prop_register(rt: AgentRuntime, project: str, arguments: str,
                                          instruction, message_id) -> dict:
    """Prop 登记（§8.3）：create/update 元数据 + attach/import 已有参考 + review +
    optional approve_if_pass。系统没有 Prop 生成端点——本工具绝不偷偷生图假装正式管线。
    无参考可用时诚实返回 reference_required（不写任何 canonical）。"""
    args = _parse_args(arguments)
    prop_id = (args.get("prop_id") or "").strip() or None
    name = (args.get("name") or "").strip()
    description = str(args.get("description") or "")
    tags = _loose_json(args.get("tags")) if args.get("tags") is not None else None
    ref_local = (args.get("reference_local") or "").strip() or None
    ref_url = (args.get("reference_url") or "").strip() or None
    ref_asset_id = (args.get("reference_asset_id") or "").strip() or None
    expected = args.get("expected_resources_revision")
    approval_mode = (args.get("approval_mode") or "review_only").strip()
    if approval_mode not in ("review_only", "approve_if_pass"):
        raise AgentError("bad_arguments",
                         "approval_mode 必须是 review_only | approve_if_pass")
    if not expected or not isinstance(expected, str):
        raise AgentError("bad_arguments",
                         "缺少 expected_resources_revision（创建=document_revision，"
                         "更新=该 prop 的 revision，来自 resource.inventory/read）")
    if prop_id is None and not name:
        raise AgentError("bad_arguments", "新建 Prop 需要 name")
    if (ref_local is None) == (ref_url is None) and ref_asset_id is None:
        pass   # 无新参考提供——可能 attach 已有 asset 或用 prop 既有参考
    if ref_local is not None and ref_url is not None:
        raise AgentError("bad_arguments", "reference_local 与 reference_url 二选一")

    from src.resources.write_service import ResourceWriteService
    from src.resources.models import prop_revision
    from src.resources import prop_review as prop_review_mod
    pdir = _resolve_pdir(rt, project)
    ws = ResourceWriteService(pdir, project)

    # ---- 参考可用性预检（§8.3：诚实 reference_required，不做无参考登记）----
    doc = ws.store.load()
    existing_ref_ids = (doc.props[prop_id].reference_asset_ids
                        if prop_id and prop_id in doc.props else [])
    has_new_ref = bool(ref_local or ref_url)
    if not has_new_ref and ref_asset_id is None and not existing_ref_ids:
        return _base_receipt(
            "resource.prop.register", ok=False, target=None, before=None, after=None,
            revision=None, verified=False, error="reference_required", stale=False,
            changed_resources=[], op_summary="Prop 登记被拒（无参考）",
            note="没有可用的道具参考图：未提供 reference_local/reference_url/"
                 "reference_asset_id，且该 Prop（或新建目标）没有任何既有参考。"
                 "系统没有 Prop 生成端点——请先向用户提供参考图（本地项目内相对路径或"
                 "URL），或用 resource.read 确认既有参考后再试。未写入任何数据。")

    changes: list = []
    seen: set = set()
    # ---- Phase C1 Work A：review failure guard 入口检查（§3.1）----
    # basis = 参考图来源（换图 = 新 basis = 计数归零）。
    retry_basis = _review_basis_sig(reference_local=ref_local, reference_url=ref_url,
                                    reference_asset_id=ref_asset_id)
    blocked = _review_retry_check(project, message_id,
                                  prop_id or f"new:{(name or '').strip()}",
                                  retry_basis, "resource.prop.register")
    if blocked is not None:
        return blocked

    # ---- 阶段1：create / update 元数据 ----
    if prop_id is None:
        # 复用护栏（真机 E2E 失败模式矫正）：同名 Prop 已存在时拒绝盲目 create，
        # 结构化引导走 prop_id 更新/重审——防止审查失败后无限重建同名道具。
        existing_props = ws.store.load().props
        dup = next((p for p in existing_props.values()
                    if (p.name or "") == (name or "")), None)
        if dup is not None:
            return _base_receipt(
                "resource.prop.register", ok=False, target=dup.prop_id,
                before=None, after=None, revision=None, verified=False,
                error="prop_name_exists", stale=False,
                prop_id=dup.prop_id, existing_prop_id=dup.prop_id,
                changed_resources=[], op_summary="Prop 创建被拒（同名已存在）",
                note=f"项目里已有同名 Prop「{name}」（prop_id={dup.prop_id}）。绝不要重复"
                     "创建同名道具：要对它换参考或重审，就带 prop_id 调用本工具"
                     "（expected_resources_revision 传该 prop 的 revision，来自 "
                     "resource.read(kind=prop)），并给出新的 reference_local/reference_url。")
        r = await _svc(ws, "create_prop", name=name, description=description,
                       tags=tags if isinstance(tags, list) else None,
                       expected_document_revision=expected)
        if r.get("error") == "stale_revision":
            return _resource_stale_receipt("resource.prop.register", r,
                                           "创建 Prop（stale，未写入）")
        if not r.get("ok"):
            return _base_receipt("resource.prop.register", ok=False, target=None,
                                 before=None, after=None, revision=None, verified=False,
                                 error=r.get("error") or "create_failed",
                                 detail=r.get("detail"), changed_resources=[],
                                 op_summary="创建 Prop 失败",
                                 note=f"Prop 未创建：{r.get('detail') or r.get('error')}")
        prop_id = r["target"]
        prop_rev = r["revision"]
        _chg(changes, seen, "prop", prop_id)
    else:
        patch = {}
        if args.get("name") is not None:
            patch["name"] = name
        if args.get("description") is not None:
            patch["description"] = description
        if args.get("tags") is not None:
            patch["tags"] = tags if isinstance(tags, list) else None
        if patch:
            r = await _svc(ws, "update_prop", prop_id=prop_id, patch=patch,
                           expected_revision=expected)
            if r.get("error") == "stale_revision":
                return _resource_stale_receipt("resource.prop.register", r,
                                               f"更新 Prop {prop_id}（stale，未写入）")
            if r.get("error") == "not_found":
                raise AgentError("not_found",
                                 f"Prop 不存在: {prop_id}（先 resource.inventory 核对 id）")
            if not r.get("ok"):
                return _base_receipt("resource.prop.register", ok=False, target=prop_id,
                                     before=None, after=None, revision=None,
                                     verified=False, error=r.get("error") or "update_failed",
                                     detail=r.get("detail"), changed_resources=[],
                                     op_summary="更新 Prop 失败",
                                     note=f"Prop 未更新：{r.get('detail') or r.get('error')}")
            prop_rev = r["revision"]
            if r.get("verified") and not r.get("unchanged"):
                _chg(changes, seen, "prop", prop_id)
        else:
            prop_rev = expected

    # ---- 阶段2：参考图注册 / attach ----
    target_asset = None
    attach_summary = {"attempted": False, "ok": None, "error": None}
    if ref_local or ref_url:
        attach_summary["attempted"] = True
        doc_rev_now = document_revision_safe(ws)
        rr = await _svc(ws, "register_reference_asset",
                        local=ref_local, url=ref_url,
                        view="hero", purpose="prop", source="agent_import",
                        expected_document_revision=doc_rev_now)
        if not rr.get("ok"):
            attach_summary["ok"] = False
            attach_summary["error"] = rr.get("error")
            return _base_receipt(
                "resource.prop.register", ok=bool(changes), target=prop_id,
                before=None, after=None, revision=None, verified=bool(changes),
                prop_id=prop_id, resource_kind="prop", resource_id=prop_id,
                changed_resources=changes, reference=attach_summary,
                review=None, approval=None, approval_mode=approval_mode,
                op_summary=(f"Prop {prop_id} 已登记，但参考图注册失败"
                            if changes else "参考图注册失败"),
                note=f"参考图注册失败：{rr.get('detail') or rr.get('error')}。"
                     "Prop 元数据状态见 changed_resources；修正参考来源后重试。")
        target_asset = rr["target"]
        _chg(changes, seen, "reference_asset", target_asset)
        ar = await _svc(ws, "attach_prop_reference", prop_id=prop_id,
                        asset_id=target_asset,
                        expected_revision=prop_revision(ws.store.load().props[prop_id]))
        if not ar.get("ok"):
            attach_summary["ok"] = False
            attach_summary["error"] = ar.get("error")
            return _base_receipt(
                "resource.prop.register", ok=bool(changes), target=prop_id,
                before=None, after=None, revision=None, verified=bool(changes),
                prop_id=prop_id, resource_kind="prop", resource_id=prop_id,
                changed_resources=changes, reference=attach_summary,
                review=None, approval=None, approval_mode=approval_mode,
                op_summary="参考图 attach 失败",
                note=f"参考已注册（{target_asset}）但 attach 失败：{ar.get('detail') or ar.get('error')}。")
        attach_summary["ok"] = True
        prop_rev = prop_revision(ws.store.load().props[prop_id])
    elif ref_asset_id is not None:
        attach_summary["attempted"] = True
        ar = await _svc(ws, "attach_prop_reference", prop_id=prop_id,
                        asset_id=ref_asset_id,
                        expected_revision=prop_revision(ws.store.load().props[prop_id]))
        if not ar.get("ok"):
            if ar.get("error") == "asset_not_found":
                raise AgentError("not_found",
                                 f"ReferenceAsset 不存在: {ref_asset_id}（resource.read "
                                 "kind=reference_asset 核对）")
            attach_summary["ok"] = False
            attach_summary["error"] = ar.get("error")
            return _base_receipt(
                "resource.prop.register", ok=bool(changes), target=prop_id,
                before=None, after=None, revision=None, verified=bool(changes),
                prop_id=prop_id, resource_kind="prop", resource_id=prop_id,
                changed_resources=changes, reference=attach_summary,
                review=None, approval=None, approval_mode=approval_mode,
                op_summary="参考图 attach 失败",
                note=f"attach 失败：{ar.get('detail') or ar.get('error')}。")
        target_asset = ref_asset_id
        attach_summary["ok"] = True
        if ar.get("verified") and not ar.get("unchanged"):
            _chg(changes, seen, "prop", prop_id)
        prop_rev = prop_revision(ws.store.load().props[prop_id])
    else:
        target_asset = (doc.props[prop_id].approved_reference_asset_ids
                        or doc.props[prop_id].reference_asset_ids or [None])[0]

    # ---- 阶段3：review ----
    review = await prop_review_mod.review_prop_reference(pdir, prop_id,
                                                         asset_ids=[target_asset],
                                                         agnes_client=None)
    cr = (review.get("candidate_reviews") or [{}])[0]
    review_overall = cr.get("overall")
    review_summary = {"overall": review_overall, "asset_id": target_asset,
                      "prop_fidelity": cr.get("prop_fidelity"),
                      "visual_clarity": cr.get("visual_clarity"),
                      "visual_defects": cr.get("visual_defects"),
                      "project_style_consistency": cr.get("project_style_consistency"),
                      "summary": cr.get("summary") or review.get("detail") or "",
                      "review_id": review.get("review_id"),
                      "status": review.get("status")}

    # ---- 阶段4：approve_if_pass ----
    approval_summary = {"attempted": False, "ok": None, "status": None}
    if approval_mode == "approve_if_pass":
        approval_summary["attempted"] = True
        if review.get("status") != "completed":
            approval_summary["status"] = "review_failed"
        elif review_overall != "pass":
            approval_summary["status"] = f"review_{review_overall}_no_approve"
        else:
            ar = await _svc(ws, "approve_prop_reference", prop_id=prop_id,
                            asset_id=target_asset,
                            expected_revision=prop_revision(ws.store.load().props[prop_id]))
            approval_summary["ok"] = bool(ar.get("ok"))
            approval_summary["status"] = (ar.get("error") or "approved")
            if ar.get("ok") and ar.get("verified") and not ar.get("unchanged"):
                _chg(changes, seen, "prop", prop_id)

    approved = approval_summary.get("status") == "approved"
    # ---- Phase C1 Work A：审查结果记账（用确定 prop_id 对齐后续重试 key）----
    _review_retry_record(project, message_id, prop_id, retry_basis,
                         review_overall if review.get("status") == "completed" else None)
    return _base_receipt(
        "resource.prop.register", ok=bool(changes), target=prop_id,
        before=None, after=None, revision=None, verified=bool(changes),
        prop_id=prop_id, resource_kind="prop", resource_id=prop_id,
        changed_resources=changes, reference=attach_summary,
        review=review_summary, approval=approval_summary, approval_mode=approval_mode,
        op_summary=(f"Prop {prop_id} 参考已登记，review={review_overall}"
                    + ("，已批准" if approved else "，未批准")),
        note=(f"Prop {prop_id}：参考图 {target_asset} 已绑定；审查 overall={review_overall}"
              + (f"（{review_summary.get('summary') or '无备注'}）" if review_summary.get("summary") else "")
              + ("；已正式批准（可进 Composition / 生产绑定）。" if approved else
                 "；审查未通过 → 未批准。请依据审查原因决定下一步（换参考/改描述/报告用户）。"
                 if review_overall != "pass" else
                 "；review_only 模式不自动批准，等待用户验收。")))


def document_revision_safe(ws) -> str:
    """当前 resources document revision（工具链内阶段衔接用；OCC 判定仍由 svc 做）。"""
    from src.resources.models import document_revision
    return document_revision(ws.store.load())


# ---- §8.4 resource.composition.prepare ----
async def _execute_resource_composition_prepare(rt: AgentRuntime, project: str,
                                                arguments: str,
                                                instruction, message_id) -> dict:
    """CharacterReferenceComposition 的一次生产准备尝试（§8.4）：create/update →
    generate → review → approve_if_pass。Review 保留 identity/look/prop fidelity/
    binding 正确性——模型画错 = needs_fix，不是文字宣布成功。"""
    args = _parse_args(arguments)
    character_name = (args.get("character_name") or "").strip()
    look_id = (args.get("look_id") or "").strip()
    composition_id = (args.get("composition_id") or "").strip() or None
    label = str(args.get("label") or "")
    pose_note = str(args.get("pose_note") or "")
    comp_desc = str(args.get("composition_description") or "")
    raw_bindings = _loose_json(args.get("prop_bindings"))
    expected = args.get("expected_resources_revision")
    approval_mode = (args.get("approval_mode") or "review_only").strip()
    if approval_mode not in ("review_only", "approve_if_pass"):
        raise AgentError("bad_arguments",
                         "approval_mode 必须是 review_only | approve_if_pass")
    if not expected or not isinstance(expected, str):
        raise AgentError("bad_arguments",
                         "缺少 expected_resources_revision（创建=document_revision，"
                         "更新=该 composition 的 revision）")
    if composition_id is None and (not character_name or not look_id):
        raise AgentError("bad_arguments", "新建 Composition 需要 character_name + look_id")
    if composition_id is None and not isinstance(raw_bindings, list):
        raise AgentError("bad_arguments",
                         "prop_bindings 必须是数组（1~3 个 {prop_id, relation, anchor}）")

    from src.resources.write_service import ResourceWriteService
    from src.resources.models import composition_revision
    from src.resources import composition_review as comp_review_mod
    pdir = _resolve_pdir(rt, project)
    ws = ResourceWriteService(pdir, project)
    changes: list = []
    seen: set = set()

    # ---- Phase C1 Work A：review failure guard 入口检查（§3.1）----
    retry_basis = _review_basis_sig(look_id=look_id, prop_bindings=raw_bindings,
                                    composition_description=comp_desc,
                                    pose_note=pose_note)
    blocked = _review_retry_check(
        project, message_id,
        composition_id or f"new:{character_name}:{(label or '').strip()}",
        retry_basis, "resource.composition.prepare")
    if blocked is not None:
        return blocked

    # ---- 阶段1：create / update ----
    if composition_id is None:
        # 复用护栏（同 look/prop）：同 character+label 的 Composition 已存在时拒绝盲目 create。
        existing_comps = ws.store.load().compositions
        dup = next((c for c in existing_comps.values()
                    if c.character_name == character_name
                    and (label or "").strip()
                    and (c.label or "") == (label or "")), None)
        if dup is not None:
            return _base_receipt(
                "resource.composition.prepare", ok=False, target=dup.composition_id,
                before=None, after=None, revision=None, verified=False,
                error="composition_label_exists", stale=False,
                composition_id=dup.composition_id, existing_composition_id=dup.composition_id,
                changed_resources=[], op_summary="Composition 创建被拒（同名已存在）",
                note=f"角色 {character_name} 已有同名组合参考「{label or '(无标签)'}」"
                     f"（composition_id={dup.composition_id}）。绝不要重复创建同名资源："
                     "要更新或再生成它，就带 composition_id 调用本工具"
                     "（expected_resources_revision 传该 composition 的 revision）。")
        r = await _svc(ws, "create_composition", character_name=character_name,
                       look_id=look_id, prop_bindings=raw_bindings, label=label,
                       pose_note=pose_note, composition_description=comp_desc,
                       expected_document_revision=expected)
        if r.get("error") == "stale_revision":
            return _resource_stale_receipt("resource.composition.prepare", r,
                                           "创建 Composition（stale，未写入）")
        if not r.get("ok"):
            return _base_receipt("resource.composition.prepare", ok=False, target=None,
                                 before=None, after=None, revision=None, verified=False,
                                 error=r.get("error") or "create_failed",
                                 detail=r.get("detail"), changed_resources=[],
                                 op_summary="创建 Composition 失败",
                                 note=f"Composition 未创建：{r.get('detail') or r.get('error')}"
                                      f"（look 未批准 / prop 无 approved 参考 / binding 非法等"
                                      f"——先准备好前置资源再试）")
        composition_id = r["target"]
        comp_rev = r["revision"]
        _chg(changes, seen, "composition", composition_id)
    else:
        patch = {}
        if args.get("label") is not None:
            patch["label"] = label
        if args.get("pose_note") is not None:
            patch["pose_note"] = pose_note
        if args.get("composition_description") is not None:
            patch["composition_description"] = comp_desc
        if raw_bindings is not None:
            patch["prop_bindings"] = raw_bindings
        if patch:
            r = await _svc(ws, "update_composition", composition_id=composition_id,
                           patch=patch, expected_revision=expected)
            if r.get("error") == "stale_revision":
                return _resource_stale_receipt("resource.composition.prepare", r,
                                               f"更新 Composition {composition_id}（stale，未写入）")
            if r.get("error") == "not_found":
                raise AgentError("not_found",
                                 f"Composition 不存在: {composition_id}")
            if not r.get("ok"):
                return _base_receipt("resource.composition.prepare", ok=False,
                                     target=composition_id, before=None, after=None,
                                     revision=None, verified=False,
                                     error=r.get("error") or "update_failed",
                                     detail=r.get("detail"), changed_resources=[],
                                     op_summary="更新 Composition 失败",
                                     note=f"Composition 未更新：{r.get('detail') or r.get('error')}")
            comp_rev = r["revision"]
            if r.get("verified") and not r.get("unchanged"):
                _chg(changes, seen, "composition", composition_id)
        else:
            comp_rev = expected

    # ---- 阶段2：generate（一次）----
    gen = await _svc(ws, "generate_composition_candidates", composition_id=composition_id,
                     expected_revision=comp_rev, candidate_count=1)
    gen_ok = bool(gen.get("ok") and gen.get("status") == "committed")
    gen_summary = {"ok": gen_ok, "status": gen.get("status"), "error": gen.get("error"),
                   "detail": (gen.get("detail") or "")[:300],
                   "generation_id": gen.get("generation_id"),
                   "candidate_asset_ids": gen.get("candidate_asset_ids") or []}
    if not gen_ok:
        return _base_receipt(
            "resource.composition.prepare", ok=bool(changes), target=composition_id,
            before=None, after=None, revision=None, verified=bool(changes),
            composition_id=composition_id, resource_kind="composition",
            resource_id=composition_id, changed_resources=changes,
            generation=gen_summary, review=None, approval=None,
            approval_mode=approval_mode,
            op_summary=(f"Composition {composition_id} 已创建/更新，但候选图生成失败"
                        if changes else f"Composition {composition_id} 生成失败（无变更）"),
            note=(f"生成未完成：{gen.get('error')}（{(gen.get('detail') or '')[:200]}）。"
                  "这是本次唯一一次生成尝试；前置资源状态变化时先 resource.read 核对再试。"))

    new_assets = gen.get("candidate_asset_ids") or []
    for aid in new_assets:
        _chg(changes, seen, "reference_asset", aid)

    # ---- 阶段3：review（六维度）----
    review = await comp_review_mod.review_composition_candidates(pdir, composition_id,
                                                                 asset_ids=list(new_assets),
                                                                 agnes_client=None)
    cr = (review.get("candidate_reviews") or [{}])[0]
    review_overall = cr.get("overall")
    review_summary = {"overall": review_overall, "asset_id": new_assets[0] if new_assets else None,
                      "character_identity": cr.get("character_identity"),
                      "look_consistency": cr.get("look_consistency"),
                      "prop_fidelity": cr.get("prop_fidelity"),
                      "prop_binding_correctness": cr.get("prop_binding_correctness"),
                      "required_prop_visibility": cr.get("required_prop_visibility"),
                      "visual_defects": cr.get("visual_defects"),
                      "per_binding": cr.get("per_binding"),
                      "summary": cr.get("summary") or review.get("detail") or "",
                      "review_id": review.get("review_id"),
                      "status": review.get("status")}

    # ---- 阶段4：approve_if_pass ----
    approval_summary = {"attempted": False, "ok": None, "status": None}
    if approval_mode == "approve_if_pass":
        approval_summary["attempted"] = True
        if review.get("status") != "completed":
            approval_summary["status"] = "review_failed"
        elif review_overall != "pass":
            approval_summary["status"] = f"review_{review_overall}_no_approve"
        else:
            comp_rev_now = composition_revision(ws.store.load().compositions[composition_id])
            ar = await _svc(ws, "approve_composition_candidate",
                            composition_id=composition_id, asset_id=new_assets[0],
                            expected_revision=comp_rev_now)
            approval_summary["ok"] = bool(ar.get("ok"))
            approval_summary["status"] = (ar.get("error") or "approved")
            if ar.get("ok") and ar.get("verified") and not ar.get("unchanged"):
                _chg(changes, seen, "composition", composition_id)

    approved = approval_summary.get("status") == "approved"
    # ---- Phase C1 Work A：审查结果记账（用确定 composition_id 对齐重试 key）----
    _review_retry_record(project, message_id, composition_id, retry_basis,
                         review_overall if review.get("status") == "completed" else None)
    return _base_receipt(
        "resource.composition.prepare", ok=bool(changes), target=composition_id,
        before=None, after=None, revision=None, verified=bool(changes),
        composition_id=composition_id, resource_kind="composition",
        resource_id=composition_id, changed_resources=changes,
        generation=gen_summary, review=review_summary, approval=approval_summary,
        approval_mode=approval_mode,
        op_summary=(f"Composition {composition_id} 生成完成，review={review_overall}"
                    + ("，已批准" if approved else "，未批准")),
        note=(f"Composition {composition_id}：生成 committed；审查 overall={review_overall}"
              + (f"（{review_summary.get('summary') or '无备注'}）" if review_summary.get("summary") else "")
              + ("；已正式批准（正式生产组合参考）。" if approved else
                 "；审查未通过 → 未批准（画错 = needs_fix，不是成功）。依据原因调整 binding/"
                 "描述或换 Look 后再试；不要无依据重试。"
                 if review_overall != "pass" else
                 "；review_only 模式不自动批准，等待用户验收。")))


# ---- §9 resource.library.publish / import ----
async def _execute_resource_library_publish(rt: AgentRuntime, project: str, arguments: str,
                                            instruction, message_id) -> dict:
    """把项目资源发布到共享库（§9：copy-on-publish，项目 canonical 只读零改动；
    幂等 republish=同 shared id 版本+1）。character 走母版发布（publish_character）。"""
    args = _parse_args(arguments)
    kind = (args.get("resource_kind") or "").strip()
    rid = (args.get("resource_id") or "").strip()
    expected = args.get("expected_resources_revision")
    destination = (args.get("destination") or "default").strip() or "default"
    if kind not in ("clothing", "look", "prop", "composition", "character"):
        raise AgentError("bad_arguments",
                         "resource_kind 必须是 clothing|look|prop|composition|character")
    if not rid:
        raise AgentError("bad_arguments", "缺少 resource_id（character 域传角色名）")
    if destination != "default":
        raise AgentError("bad_arguments",
                         f"当前系统只有 default 共享库，不支持: {destination}")
    if not expected or not isinstance(expected, str):
        raise AgentError("bad_arguments",
                         "缺少 expected_resources_revision（传 resource.inventory 返回的"
                         " document_revision，确保发布的是你看到的源状态）")
    from src.resources.write_service import ResourceWriteService
    from src.resources.shared_library import SharedLibraryService
    pdir = _resolve_pdir(rt, project)
    # 发布前源状态校验（发布动作本身只读项目 canonical——OCC 防的是「基于过期快照发布」）
    cur_doc = document_revision_safe(ResourceWriteService(pdir, project))
    if expected != cur_doc:
        return _base_receipt("resource.library.publish", ok=False, target=rid,
                             before=None, after=None, revision=cur_doc, verified=False,
                             error="stale_revision", stale=True,
                             expected_revision=expected, current_revision=cur_doc,
                             changed_resources=[], op_summary="发布被拒（源状态过期）",
                             note="你看到的 resources document 已过期，未发布。请重新 "
                                  "resource.inventory 后再试。")
    svc = SharedLibraryService(pdir, project)
    if kind == "character":
        r = await svc.publish_character(rid)
    else:
        r = await svc.publish(rid)
    if not r.get("ok"):
        return _base_receipt("resource.library.publish", ok=False, target=rid,
                             before=None, after=None, revision=None, verified=False,
                             error=r.get("error") or "publish_failed",
                             detail=r.get("detail"), changed_resources=[],
                             op_summary="发布失败",
                             note=f"发布未完成：{r.get('detail') or r.get('error')}")
    shared_id = r.get("shared_id") or r.get("schar_id") or r.get("id")
    return _base_receipt("resource.library.publish", ok=True, target=rid,
                         before=None, after={"shared_id": shared_id,
                                             "version": r.get("version")},
                         revision=cur_doc, verified=True,
                         resource_kind=kind, resource_id=rid,
                         shared_id=shared_id, version=r.get("version"),
                         published=True,
                         changed_resources=[],   # 项目 canonical 零改动（copy 进共享库）
                         op_summary=f"已发布 {rid} → 共享库（v{r.get('version')}）",
                         note=f"已发布到共享库：shared_id={shared_id}（版本 "
                              f"{r.get('version')}）。项目内资源保持不变（copy-on-publish）。")


async def _execute_resource_library_import(rt: AgentRuntime, project: str, arguments: str,
                                           instruction, message_id) -> dict:
    """从共享库导入资源快照（§9：copy-on-import 新 id + 媒体复制 + origin；look 自动捆绑
    clothing；character 要求项目内无同名）。OCC：expected_resources_revision 对照项目
    document revision，stale fail closed；绝不手工 remap 依赖 id。"""
    args = _parse_args(arguments)
    shared_id = (args.get("shared_resource_id") or "").strip()
    expected = args.get("expected_resources_revision")
    if not shared_id:
        raise AgentError("bad_arguments", "缺少 shared_resource_id")
    if not expected or not isinstance(expected, str):
        raise AgentError("bad_arguments",
                         "缺少 expected_resources_revision（传 resource.inventory 返回的"
                         " document_revision）")
    from src.resources.shared_library import SharedLibraryService
    pdir = _resolve_pdir(rt, project)
    svc = SharedLibraryService(pdir, project)
    r = await svc.import_resource(shared_id, expected_document_revision=expected)
    if r.get("error") == "stale_revision":
        return _base_receipt("resource.library.import", ok=False, target=shared_id,
                             before=None, after=None,
                             revision=r.get("current_revision"), verified=False,
                             error="stale_revision", stale=True,
                             expected_revision=r.get("expected_revision"),
                             current_revision=r.get("current_revision"),
                             changed_resources=[], op_summary="导入被拒（项目状态过期）",
                             note="项目 resources document 已被并发修改，未导入。重新 "
                                  "resource.inventory 后再试。")
    if not r.get("ok"):
        return _base_receipt("resource.library.import", ok=False, target=shared_id,
                             before=None, after=None, revision=None, verified=False,
                             error=r.get("error") or "import_failed",
                             detail=r.get("detail"), changed_resources=[],
                             op_summary="导入失败",
                             note=f"导入未完成：{r.get('detail') or r.get('error')}"
                                  + ("（fail closed，不半途导入）" if r.get("error") else ""))
    kind = r.get("kind")
    imported_id = r.get("imported_id")
    changes: list = []
    seen: set = set()
    bundle = r.get("imported_bundle") or {}
    if kind == "character":
        # 母版导入 = 新 Character 进 project.json（Resource Agent 自有域）
        changes.append({"kind": "character", "id": imported_id})
    else:
        _chg(changes, seen, kind, imported_id)
    for sid_, new_id in bundle.items():
        if sid_ != shared_id:
            _chg(changes, seen, "clothing", new_id)   # look 导入自动捆绑的 clothing
    first = dict(changes[0]) if changes else {}
    return _base_receipt(
        "resource.library.import", ok=True, target=imported_id,
        before=None, after=None, revision=None, verified=True,
        resource_kind=(first.get("kind") or kind), resource_id=imported_id,
        imported_id=imported_id,
        character_name=(imported_id if kind == "character" else None),
        shared_id=shared_id, kind=kind,
        imported_bundle=bundle,
        resources_document_revision=r.get("document_revision_after"),
        changed_resources=changes,
        op_summary=f"已从共享库导入 {kind} {imported_id}（来源 {shared_id}）",
        note=f"导入完成（copy-on-import，新 id={imported_id}，origin 已记录）。"
             f"共享库原资源不受影响。")


TOOL_EXECUTORS = {
    "character.list": _execute_character_list,
    "character.read": _execute_character_read,
    "character.create": _execute_character_create,
    "character.update": _execute_character_update,
    "character.delete": _execute_character_delete,
    "character.generate_views": _execute_character_generate_views,
    "project.visual_profile.read": _execute_visual_profile_read,
    "resource.inventory": _execute_resource_inventory,
    "resource.read": _execute_resource_read,
    "resource.clothing.upsert": _execute_resource_clothing_upsert,
    "resource.look.prepare": _execute_resource_look_prepare,
    "resource.prop.register": _execute_resource_prop_register,
    "resource.look.approve": _execute_resource_look_approve,
    "resource.composition.prepare": _execute_resource_composition_prepare,
    "resource.library.publish": _execute_resource_library_publish,
    "resource.library.import": _execute_resource_library_import,
    "shot.list": _execute_shot_list_via_director,
    "shot.read": _execute_shot_read_via_director,
    "story.list": _execute_story_list,
    "story.read": _execute_story_read,
    "script.read": _execute_script_read,
    "web.search": _execute_web_search,
    # Skill System V1（40.md）：skill.list/load/read_resource（只读）
    **make_skill_executors("character_designer"),
}


# ============ 回执渲染 / 真相收口（角色域措辞）============
_TOOL_LABELS = {
    "character.list": "读取角色列表", "character.read": "读取角色",
    "character.create": "创建角色", "character.update": "修改角色",
    "character.delete": "删除角色", "character.generate_views": "生成角色三视图",
    "project.visual_profile.read": "读取项目视觉定义",
    "resource.inventory": "列出生产资源", "resource.read": "读取生产资源",
    "resource.clothing.upsert": "创建/更新服装",
    "resource.look.prepare": "准备角色造型（生成+审查）",
    "resource.prop.register": "登记道具参考",
    "resource.look.approve": "批准造型候选",
    "resource.composition.prepare": "准备组合参考（生成+审查）",
    "resource.library.publish": "发布到共享库", "resource.library.import": "从共享库导入",
    "shot.list": "列出镜头", "shot.read": "读取镜头",
    "story.list": "读取故事段", "story.read": "读取故事段",
    "script.read": "读取剧本", "web.search": "搜索资料",
}


def _tool_label(name: str) -> str:
    return _TOOL_LABELS.get(name, name)


def _op_human_char(r: dict) -> str:
    """把一条真实回执渲染成人类可读的操作描述（确定性，来自回执而非模型叙述）。"""
    op = r.get("operation")
    name = r.get("character_name")
    if op == "character.create":
        return f"创建角色 {name}"
    if op == "character.update":
        ch = r.get("changed") or []
        return f"修改角色 {name}（{'、'.join(ch) or '描述'}）"
    if op == "character.delete":
        return f"删除角色 {name}"
    if op == "character.generate_views":
        n = len(r.get("generated") or [])
        total = len(r.get("character_names") or [])
        return f"生成角色三视图（{n}/{total} 个角色完成）"
    return f"{op}({name or ''})"


def _tool_summary(name: str, obs: dict) -> str:
    """tool.completed 的 summary：成功用真实回执描述，失败给原因。"""
    if name == "character.generate_views":
        if obs.get("cancelled"):
            return "生成已取消"
        n = len(obs.get("generated") or [])
        total = len(obs.get("character_names") or [])
        if n:
            return (f"已完成 {n}/{total} 个角色三视图"
                    + ("" if n == total else "（其余超时/失败）"))
        return f"生成未完成：{obs.get('error') or obs.get('code') or '失败'}"
    if name == "web.search":
        if obs.get("ok"):
            n = obs.get("n_results", 0)
            return f"找到 {n} 个参考来源" if n else "没有找到相关资料"
        if obs.get("code") == "search_budget_exhausted":
            return "本轮搜索次数已达上限"
        return f"搜索未完成：{obs.get('error') or obs.get('code') or '失败'}"
    if obs.get("ok"):
        if obs.get("operation") in CHARACTER_WRITE_OPERATIONS:
            return _op_human_char(obs)
        return f"{_tool_label(name)}完成"
    why = obs.get("error") or obs.get("code") or "失败"
    if obs.get("stale"):
        why = "版本过期，已拒绝"
    return f"{_tool_label(name)}未完成：{why}"


def _trace_entry(step: int, name: str, args: str, obs: dict) -> dict:
    """执行轨迹单条：turn→tool→target→result→revision。target 取角色名/scene_id。"""
    if name == "character.generate_views":
        target = obs.get("target")   # 目标角色名列表
    else:
        target = (obs.get("character_name") or obs.get("scene_id")
                  or (obs.get("target") if obs.get("ok") else None))
    if name == "script.read":
        target = "story_text"
    if name == "web.search":
        target = obs.get("target")
    result = ("stale" if obs.get("stale")
              else "unchanged" if obs.get("unchanged")
              else "ok" if obs.get("ok")
              else "error")
    return {
        "step": step, "tool": name, "target": target, "result": result,
        "revision": obs.get("revision") or obs.get("characters_revision"),
        "error": (None if result in ("ok", "unchanged")
                  else (obs.get("error") or obs.get("code"))),
        "ok": bool(obs.get("ok")),
    }


def _ledger_brief(receipts: list) -> str:
    """把本轮真实写工具结果渲染成给模型看的账本（确定性，来自回执而非模型叙述）。"""
    if not receipts:
        return "本轮没有任何写工具被真正调用（没有写工具的工具调用记录）。"
    lines = []
    for r in receipts:
        op = r.get("operation")
        target = r.get("character_name") or r.get("look_id") or r.get("prop_id") \
            or r.get("clothing_id") or r.get("composition_id") or ""
        if r.get("ok"):
            if r.get("unchanged"):
                lines.append(f"- {op}：ok=true，但内容无变化（unchanged），canonical 未改动。")
            else:
                lines.append(f"- {op}（{target}）：ok=true，已重新读取 canonical 确认落盘。")
        else:
            why = r.get("error") or "未知错误"
            if r.get("stale"):
                why = "stale（版本过期，被拒绝，未写入）"
            lines.append(f"- {op}（{target}）：ok=false，原因：{why}。canonical 未被该调用改动。")
    return "\n".join(lines)


def _honest_fallback(receipts: list) -> str:
    """纠偏后模型仍谎称成功时的确定性兜底文案——纯由真实回执生成，绝不采信模型自述。"""
    real_changes = [r for r in receipts if r.get("ok") and r.get("verified") and not r.get("unchanged")]
    if real_changes:
        return "已完成的修改：" + "；".join(_op_human_char(r) for r in real_changes) + "。"
    if receipts:
        reasons = []
        for r in receipts:
            if r.get("stale"):
                reasons.append("角色版本已过期")
            elif r.get("error") == "verify_failed":
                reasons.append("写入后校验未通过")
            else:
                reasons.append(r.get("error") or "工具执行失败")
        uniq = "、".join(dict.fromkeys(reasons))
        return (f"这次修改没有真正生效（{uniq}）。角色数据保持原样。"
                f"可以让我重试，或先让我读取当前最新内容再改。")
    return "这次我没有真正执行修改（没有发出对应的工具调用）。请再说一次你的需求，我会实际操作。"


def _append_fact_summary(rt: AgentRuntime, project: str, text: str, receipts: list) -> str:
    """有真实变更时，在模型回复后追加【确定性事实摘要】——由真实 receipts + 当前
    canonical 生成，不信模型叙述。覆盖全成功/部分成功/全失败三态：已成功资产明确列出
    并保留，未完成项也如实标注。纯追加，不删改模型原文。"""
    if not receipts:
        return text
    real_changes = [r for r in receipts if r.get("ok") and r.get("verified") and not r.get("unchanged")]
    failed = [r for r in receipts if not r.get("ok")]
    # 纯 CRUD 失败且无成功变更（如创建/删除一个不存在的角色被拒）→ 不追加事实块；
    # 三视图生成（character.generate_views）即使部分/全部失败也追加，确定性告知完成情况。
    has_generate = any(r.get("operation") == "character.generate_views" for r in receipts)
    if not real_changes and not has_generate:
        return text
    try:
        proj = _reload(rt, project)
        names = [c.name for c in (proj.characters or [])]
        lines = ["\n\n---\n【实际执行记录】（系统据真实回执生成，非模型叙述）"]
        for r in real_changes:
            lines.append(f"✓ {_op_human_char(r)}")
        for r in failed:
            why = r.get("error") or r.get("code") or "失败"
            if r.get("stale"):
                why = "版本过期，已拒绝"
            elif r.get("cancelled"):
                why = "已取消"
            nm = r.get("character_name") or ""
            lines.append(f"✗ {r.get('operation', '操作')}{(' ' + nm) if nm else ''}：{why}")
        # 确定性完成情况总结行（不让模型猜）
        if failed and not real_changes:
            lines.append(f"\n本次没有完成生成（{len(failed)} 项未成功）。")
        elif failed and real_changes:
            lines.append(f"\n已完成 {len(real_changes)} 项，{len(failed)} 项未完成/取消。")
        else:
            lines.append(f"\n已完成 {len(real_changes)} 项。")
        lines.append(f"\n当前角色共 {len(names)} 个：{', '.join(names) if names else '（空）'}")
        return text + "\n".join(lines)
    except Exception:
        return text


# 声称「本次创建了 N 个角色」的计数表述（供夸大检测；宁漏勿误）。
_CLAIMED_CHAR_COUNT_RE = re.compile(
    r"(?:创建|新建|添加|新增|设计)[^0-9。！？\n]{0,6}?(\d+)\s*个?(?:新)?角色"
    r"|(\d+)\s*个?(?:新)?角色[^。！？\n]{0,8}?(?:建好|创建|设计|完成)")


def _claimed_char_count_mismatch(text: str, real_count: int) -> Optional[int]:
    """文本里「本次创建了 N 个角色」的 N 与真实成功创建数不符时返回 N，否则 None。"""
    t = text or ""
    m = _CLAIMED_CHAR_COUNT_RE.search(t)
    if m:
        v = int(m.group(1) or m.group(2))
        if v != real_count:
            return v
    return None


_ZERO_WRITE_RECOVERY_NUDGE = (
    "【系统提示】用户本轮明确要求实际修改/创建/删除角色，但你到目前为止【还没有发起任何写工具"
    "调用】——在回复里用自然语言描述设计不等于完成修改。请立即：①先用只读工具（character.list / "
    "character.read）确认当前真实状态；②真正发起对应的写工具（character.create / character.update / "
    "character.delete）完成用户要求，等工具真实返回 ok=true 后再汇报结果；写工具一个一个来，"
    "每次用上一次返回的最新版本指纹。如果你确实无法执行（需求不清楚、目标不存在等），就明确告诉"
    "用户原因，【不要】在没有工具真实返回的情况下声称已经修改完成。")


# 执行窗口 self-check（renewable window 边界注入；相信 Agent 自我判断，
# Runtime 不做循环检测——续期后模型返回文本即正常结束，返回工具调用即继续）。
_WINDOW_SELF_CHECK_NUDGE = (
    "【系统提示·执行窗口】你已经连续执行了较长时间（当前执行窗口已用满）。请检查"
    "当前用户目标与最新的工具返回结果，然后自行判断：\n"
    "① 如果任务已经完成，正常结束并汇报结果即可；\n"
    "② 如果你确认自己正在重复相同的操作、没有取得任何新进展，并且已经没有合理的"
    "新方向，可以停止并如实说明卡点；\n"
    "③ 如果任务尚未完成且仍有明确、合理的下一步，请继续执行——不要因为执行步数"
    "而停止，系统会自动为你续接新的执行窗口。"
)


def _committed_char_names(receipts: list) -> list:
    """从已成功落盘的写回执里提取角色名列表（超时/停止恢复的 checkpoint）。"""
    out = []
    for r in receipts:
        if r.get("ok") and r.get("verified") and not r.get("unchanged"):
            n = r.get("character_name")
            if n and n not in out:
                out.append(n)
    return out


def _refresh_system(messages: list, rt: AgentRuntime, project: str) -> None:
    try:
        pdir = rt._resolve_project_dir(project)
        proj = ProjectStore(pdir).load()
        messages[0] = {"role": "system", "content": _build_system(proj, pdir)}
    except Exception:
        pass


# ============ 主循环（镜像 Script Writer 机制：单写串行 / 真相闸门 / zero-write
# recovery / 搜索预算 / 防失控 / 超时恢复 / 协作取消 / 流式事件）============
async def _run_tool_loop(rt: AgentRuntime, client, project: str,
                         messages: list, instruction: str,
                         message_id: Optional[str],
                         max_steps: int = DEFAULT_MAX_TOOL_STEPS,
                         thinking: bool = False,
                         event_cb=None,
                         state: Optional[dict] = None,
                         cancel_event=None,
                         tool_surface: Optional[list] = None) -> dict:
    """原生 function-calling 循环 + 执行真实性收口（结构性，非提示词）。"""
    created = updated = deleted = views_generated = False
    resources_mutated = False
    if state is None:
        state = {}
    events: list = state.setdefault("events", [])
    receipts: list = state.setdefault("receipts", [])
    write_attempts = 0
    corrective_round = 0
    zero_write_recovery_used = False
    trace: list = state.setdefault("trace", [])
    reasonings: list = state.setdefault("reasonings", [])
    tools_offered = tool_surface if tool_surface is not None else CHARACTER_DESIGNER_TOOLS
    last_read_after_write = -1
    search_count = 0
    search_sources: list = state.setdefault("search_sources", [])
    search_queries_seen: set = state.setdefault("search_queries_seen", set())
    repeat_fail_streak = 0
    last_fail_sig = None
    timeout_retries = 0

    def _on_read_timeout() -> bool:
        nonlocal timeout_retries
        if timeout_retries >= MAX_TIMEOUT_RETRIES:
            return False
        timeout_retries += 1
        committed = _committed_char_names(receipts)
        committed_str = ", ".join(committed) if committed else "（无）"
        nudge = (
            f"[系统恢复提示 MODEL_READ_TIMEOUT] 上一次模型响应超时被中断"
            f"（read timeout，第 {timeout_retries}/{MAX_TIMEOUT_RETRIES} 次）。"
            f"本次没有形成完整工具调用、没有新增任何提交。"
            f"已成功提交的角色：{committed_str}。"
            f"请【只继续尚未完成的部分，不要重做已提交项】。"
        )
        messages.append({"role": "user", "content": nudge})
        _refresh_system(messages, rt, project)
        return True

    step = -1
    window_used = 0
    window_renewals = 0
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise _TurnStopped()
        # ---- 执行窗口边界：self-check + 续期（renewable window，次数不限）----
        if window_used >= max_steps:
            window_renewals += 1
            window_used = 0
            timeout_retries = 0   # provider 超时预算随窗口重置（窗口语义；
                                  # 24h 任务不应因累计 3 次超时被杀）
            messages.append({"role": "user", "content": _WINDOW_SELF_CHECK_NUDGE})
            _refresh_system(messages, rt, project)
            log.info("tool-loop 执行窗口续期（project=%s renewals=%s total_steps=%s）",
                     project, window_renewals, step + 1)
        step += 1
        window_used += 1
        if cancel_event is not None and cancel_event.is_set():
            raise _TurnStopped()
        content = None
        tool_calls = None
        try:
            if event_cb:
                step_reasoning_started = False
                step_reason_t0 = 0.0
                step_reasoning_buf: list[str] = []
                step_content_buf: list[str] = []
                async for ev in client.chat_completion_stream(
                        messages, temperature=0.7, max_tokens=65536,
                        thinking=thinking, tools=tools_offered,
                        tool_choice="auto"):
                    k = ev["kind"]
                    if k == "content_delta":
                        step_content_buf.append(ev["delta"])
                    elif k == "reasoning_delta":
                        if not step_reasoning_started:
                            step_reasoning_started = True
                            step_reason_t0 = time.perf_counter()
                            event_cb({"type": "reasoning.started", "step_id": step})
                        step_reasoning_buf.append(ev["delta"])
                        event_cb({"type": "reasoning.delta", "step_id": step,
                                  "delta": ev["delta"]})
                    elif k == "tool_call_pending":
                        event_cb({"type": "tool.pending", "step_id": step,
                                  "name": ev["name"], "label": _tool_label(ev["name"])})
                    elif k == "finish":
                        content = ev["content"]
                        tool_calls = ev["tool_calls"]
                if step_reasoning_started:
                    rtext = "".join(step_reasoning_buf)
                    rdur = int((time.perf_counter() - step_reason_t0) * 1000)
                    event_cb({"type": "reasoning.completed", "step_id": step,
                              "duration_ms": rdur})
                    if rtext:
                        reasonings.append({"step": step, "text": rtext, "duration_ms": rdur})
                if not tool_calls and step_content_buf:
                    for d in step_content_buf:
                        event_cb({"type": "assistant.delta", "delta": d})
            else:
                resp = await client.chat_completion(
                    messages, temperature=0.7, max_tokens=65536,
                    tools=tools_offered, tool_choice="auto")
                tool_calls = resp.get("tool_calls")
                content = resp.get("content")
        except httpx.ReadTimeout:
            if not _on_read_timeout():
                raise
            continue

        if not tool_calls:
            text = (content or "").strip()
            # ---- 真相闸门 ----
            real_changes = [r for r in receipts if r.get("ok") and r.get("verified") and not r.get("unchanged")]
            chat_turn = (write_attempts == 0 and _is_chat_turn(instruction))
            false_claim = _claims_success(text) and not real_changes and not chat_turn
            real_searches = [t for t in trace if t["tool"] == "web.search" and t.get("ok")]
            search_false_claim = (_claims_search_success(text) and not real_searches
                                  and not chat_turn)
            real_create_count = sum(1 for r in real_changes
                                    if r.get("operation") == "character.create")
            over_claim_n = (_claimed_char_count_mismatch(text, real_create_count)
                            if real_changes and real_create_count else None)
            last_write_step = max((t["step"] for t in trace
                                   if t["tool"] in CHARACTER_WRITE_OPERATIONS), default=-1)
            reply_summarizes = bool(re.search(r"\d+\s*个角色|当前角色|角色列表", text or ""))
            needs_final_read = (real_changes and _claims_success(text)
                                and reply_summarizes
                                and last_read_after_write < last_write_step)
            if (not zero_write_recovery_used
                    and write_attempts == 0 and not real_changes
                    and _is_explicit_write_request(instruction)):
                zero_write_recovery_used = True
                log.info("character_designer zero-write recovery 触发（project=%s）", project)
                messages.append({"role": "assistant", "content": text or ""})
                messages.append({"role": "user", "content": _ZERO_WRITE_RECOVERY_NUDGE})
                _refresh_system(messages, rt, project)
                continue
            if false_claim or over_claim_n is not None or needs_final_read or search_false_claim:
                if corrective_round < MAX_CORRECTIVE_ROUNDS:
                    corrective_round += 1
                    messages.append({"role": "assistant", "content": text or ""})
                    brief = _ledger_brief(receipts)
                    if search_false_claim:
                        nudge = (
                            "你上面的回复声称做过搜索/给出了搜索结果，但本轮【没有任何一次真实成功的 "
                            "web.search 工具调用】。光用文字编造搜索结果是不允许的。请要么现在真正调用 "
                            "web.search（注意每轮最多 3 次），要么如实告诉用户：本轮没有搜索，或搜索失败、"
                            "资料不足，并基于已有知识继续。")
                    elif needs_final_read:
                        nudge = (
                            "你做了一些修改并声称完成，但最后一次真实读取发生在最后一次写入【之前】。"
                            "收尾前请先用 character.list 读取最新角色列表，然后【以这次读取的真实结果】"
                            "总结你创建/修改/删除了什么、当前角色数量。不要凭自己之前写的文字记忆声称完成。")
                    elif over_claim_n is not None:
                        nudge = (
                            f"你上面说创建了 {over_claim_n} 个角色，但工具的真实成功创建回执只有 "
                            f"{real_create_count} 次（{brief}）。数量必须以工具真实返回为准。"
                            "请基于真实回执重新回答：要么如实说已创建几个（给出真实角色名），"
                            "要么继续调用 character.create 把剩余角色真正创建出来。")
                    elif write_attempts == 0:
                        nudge = (
                            "你上面声称已经创建/修改/删除了角色，但本轮你【根本没有发起对应的"
                            "工具调用】。光用文字说「已完成」不会改动任何东西。\n"
                            + brief + "\n请要么真正调用对应工具（character.create/character.update/"
                            "character.delete）完成修改，要么如实告诉用户你没有改动、需要怎么做。")
                    else:
                        nudge = (
                            "你上面声称已经完成，但工具的真实返回显示没有任何一次写入成功确认：\n"
                            + brief + "\n请只依据上面真实的工具返回结果回答。可以用最新数据重新调用"
                            "工具重试；如果不打算重试，就如实告诉用户这次没有改动成功。")
                    messages.append({"role": "user", "content": nudge})
                    _refresh_system(messages, rt, project)
                    continue
                text = _honest_fallback(receipts)
            text = strip_reserved_fact_blocks(text)
            text = _append_fact_summary(rt, project, text, receipts)
            return {"text": text or "已完成。",
                    "created": created, "updated": updated, "deleted": deleted,
                    "views_generated": views_generated,
                    "resources_mutated": resources_mutated,
                    "events": events, "receipts": receipts, "trace": trace,
                    "reasonings": reasonings, "search_sources": search_sources,
                    "window_renewals": window_renewals}

        messages.append({"role": "assistant", "content": content or "",
                         "tool_calls": tool_calls})

        write_done_this_step = False   # 单写串行：一步只执行一个写操作
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            args = fn.get("arguments") or "{}"
            call_id = tc.get("id") or name

            if event_cb:
                event_cb({"type": "tool.started", "step_id": step, "call_id": call_id,
                          "name": name, "label": _tool_label(name)})

            is_write_tool = (name in WRITE_TOOL_DOMAIN)
            if is_write_tool and write_done_this_step:
                obs = {
                    "ok": False, "operation": name,
                    "error": "本步已执行过一个写操作。写工具必须一个一个来：等前一个完成、"
                             "拿到返回的最新 revision/状态后，再发下一个写工具调用。"
                             "不要在同一次回复里批量发多个写工具（会用旧 revision 导致 stale）。"
                             "只读工具（character.list/character.read/story.list/story.read/"
                             "script.read/web.search）可以一次多个。",
                }
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": json.dumps(obs, ensure_ascii=False)})
                trace.append(_trace_entry(step, name, args, obs))
                if event_cb:
                    event_cb({"type": "tool.completed", "step_id": step,
                              "call_id": call_id, "name": name, "ok": False,
                              "summary": "本步写工具串行，已跳过"})
                continue

            executor = TOOL_EXECUTORS.get(name)
            exc_msg = None
            if (executor is not None and name == "web.search"
                    and search_count >= MAX_WEB_SEARCH):
                obs = {"ok": False, "operation": "web.search",
                       "code": "search_budget_exhausted",
                       "error": ("本轮搜索次数已达上限（3 次）。请基于已有信息与已获取的"
                                 "搜索结果完成任务，或如实告知用户资料不足。")}
            elif (executor is not None and name == "web.search"
                    and _normalized_search_query(args)
                    and _normalized_search_query(args) in search_queries_seen):
                obs = {"ok": False, "operation": "web.search",
                       "code": "duplicate_query",
                       "error": ("本 turn 已用完全相同的查询词搜索过。请换一个更具体或不同角度的"
                                 "查询词，或基于已获取的结果继续，不要重发同一查询。")}
            elif executor is None:
                obs = {"ok": False, "error": f"未知工具：{name}", "operation": name}
            else:
                if name == "web.search":
                    search_count += 1
                    _q_norm = _normalized_search_query(args)
                    if _q_norm:
                        search_queries_seen.add(_q_norm)
                try:
                    if name == "character.generate_views":
                        # 长任务 executor：需要流式进度回传与协作取消
                        obs = await executor(rt, project, args, instruction, message_id,
                                             event_cb=event_cb, cancel_event=cancel_event)
                    elif asyncio.iscoroutinefunction(executor):
                        obs = await executor(rt, project, args, instruction, message_id)
                    else:
                        obs = executor(rt, project, args, instruction, message_id)
                except AgentError as e:
                    exc_msg = e.message
                    obs = {"ok": False, "error": e.message, "code": e.code,
                           "operation": name}
                except Exception as e:
                    exc_msg = f"工具执行异常：{str(e)[:200]}"
                    obs = {"ok": False, "error": exc_msg,
                           "operation": name}

            ok_now = bool(obs.get("ok"))
            if ok_now:
                repeat_fail_streak = 0
                last_fail_sig = None
            else:
                sig = (name, obs.get("code") or obs.get("error") or "err")
                if sig == last_fail_sig:
                    repeat_fail_streak += 1
                else:
                    repeat_fail_streak = 1
                    last_fail_sig = sig
                if repeat_fail_streak >= MAX_REPEAT_FAIL:
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": json.dumps(obs, ensure_ascii=False)})
                    _refresh_system(messages, rt, project)
                    nudge = (f"「{name}」已连续 {repeat_fail_streak} 次以同样方式失败"
                             f"（{obs.get('error') or obs.get('code')}）。继续重复同样调用不会成功。"
                             "请改用只读工具（character.list/character.read）确认当前真实状态，"
                             "换个参数或换种做法，或如实告诉用户当前遇到的问题、哪些已成功完成。")
                    messages.append({"role": "assistant", "content": content or ""})
                    messages.append({"role": "user", "content": nudge})
                    trace.append(_trace_entry(step, name, args, obs))
                    _refresh_system(messages, rt, project)
                    if event_cb:
                        if exc_msg:
                            event_cb({"type": "tool.failed", "step_id": step, "call_id": call_id,
                                      "name": name, "error": exc_msg})
                        else:
                            event_cb({"type": "tool.completed", "step_id": step, "call_id": call_id,
                                      "name": name, "ok": False,
                                      "summary": _tool_summary(name, obs)})
                    break

            # 写域调用记入账本（含拼错的 character.*/resource.* 未知工具：也算写尝试失败；
            # 已注册的 resource 只读工具不属于写域——Phase B 资源域账本分类以
            # WRITE_TOOL_DOMAIN 为唯一权威）。
            is_write_domain = (name not in CHARACTER_READ_TOOLS
                               and (name.startswith("character.")
                                    or name.startswith("resource.")))
            if is_write_domain and name not in ("character.list", "character.read"):
                write_attempts += 1
                obs.setdefault("operation", name)
                receipts.append(obs)
                write_done_this_step = True
            else:
                if ok_now and name != "web.search":
                    last_read_after_write = step
                if name == "web.search" and obs.get("ok") and obs.get("sources"):
                    for s in obs["sources"]:
                        if s and (s.get("url") or s.get("title")) \
                                and s not in search_sources:
                            search_sources.append({"title": s.get("title") or "",
                                                   "url": s.get("url") or ""})

            # 据【真实回执】设置 mutated 标志 + 事件。
            if (name in WRITE_TOOL_DOMAIN and obs.get("ok")
                    and obs.get("verified") and not obs.get("unchanged")):
                if name.startswith("resource."):
                    resources_mutated = True
                    events.append({"kind": "resource_change", "summary":
                                   obs.get("op_summary") or f"资源变更（{name}）"})
                cname = obs.get("character_name")
                if name == "character.create":
                    created = True
                    s = f"创建角色 {cname}"
                    events.append({"kind": "character_create", "character_name": cname, "summary": s})
                elif name == "character.update":
                    updated = True
                    changed = "、".join(obs.get("changed") or []) or "描述"
                    s = f"修改角色 {cname}（{changed}）"
                    events.append({"kind": "character_update", "character_name": cname, "summary": s})
                elif name == "character.delete":
                    deleted = True
                    s = f"删除角色 {cname}"
                    events.append({"kind": "character_delete", "character_name": cname, "summary": s})
                elif name == "character.generate_views":
                    views_generated = True
                    n_gen = len(obs.get("generated") or [])
                    s = f"生成 {n_gen} 个角色三视图"
                    events.append({"kind": "character_views", "summary": s})

            if event_cb:
                if exc_msg:
                    event_cb({"type": "tool.failed", "step_id": step, "call_id": call_id,
                              "name": name, "error": exc_msg})
                else:
                    event_cb({"type": "tool.completed", "step_id": step, "call_id": call_id,
                              "name": name, "ok": bool(obs.get("ok")),
                              "summary": _tool_summary(name, obs)})
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": json.dumps(obs, ensure_ascii=False)})
            trace.append(_trace_entry(step, name, args, obs))

        _refresh_system(messages, rt, project)

    # （原「预算耗尽」诚实停止段已删除：renewable window 下窗口用满时注入
    # self-check nudge 续期，由 Agent 自行判断继续/停止——while True 无 break，
    # 此段不再可达。）


# ============ 主 turn ============
async def character_designer_turn(rt: AgentRuntime, project: str, session_id: str,
                                  content: str, *, thinking: bool = False,
                                  event_cb=None, cancel_event=None) -> dict:
    """Character Designer 原生 tool-loop turn。

    返回 {assistant_message, created, updated, deleted, events, message, intent,
    receipts, trace, search_sources, session_id, agent_id}。语义与 script_writer_turn 对齐：
    thinking/event_cb 仅流式端点传入；cancel_event 协作式停止（步骤边界安全中断）。"""
    store = rt._session_store(project)
    session = store.load(session_id)
    if session is None:
        raise AgentError("unknown_scope", f"session 不存在: {session_id}")
    if session.agent_id != "character_designer":
        raise AgentError("permission_denied", "该会话仅限 Character Designer")
    if not rt._is_enabled("character_designer"):
        raise AgentError("disabled_agent", "Character Designer 已禁用")

    user_msg = AgentMessage(role="user", content=content)
    store.append_message(session_id, user_msg)
    rt._maybe_capture_preference(project, content, user_msg.message_id)
    session = store.load(session_id)

    pdir = rt._resolve_project_dir(project)
    # 会话治理（best-effort，不影响主流程）：项目首个 ≥10 字符用户消息 → 创作 brief。
    from .session_governance import capture_brief_if_absent
    capture_brief_if_absent(pdir, project, content, "character_designer")
    history = [{"role": m.role, "content": m.content}
               for m in session.messages[:-1]
               if m.role in ("user", "assistant") and m.content and m.status is None]

    client = rt._resolve_client_or_none(agent_id="character_designer")
    assistant_text: str
    status: Optional[str] = None
    created = updated = deleted = views_generated = False
    resources_mutated = False
    events: list = []
    receipts: list = []
    reasoning: list = []
    trace: list = []
    search_sources: list = []

    if client is None:
        assistant_text = "[chat 未配置：Agnes 推理模型不可用，无法修改角色。请在设置中配置 API Key。]"
        status = ResultStatus.NOT_CONFIGURED.value
    else:
        proj = ProjectStore(pdir).load()
        system = _build_system(proj, pdir)
        mem = rt._memory_supplement(project, "character_designer")
        if mem:
            system += "\n\n" + mem
        messages: list[dict] = [{"role": "system", "content": system}]
        messages += history
        # Skill System V1（40.md §七）：turn 入口确定性 trigger hint（不落盘）
        _skill_hint = _skill_activation_hint("character_designer", content, pdir)
        messages.append({"role": "user", "content":
                         (_skill_hint + "\n\n" + content) if _skill_hint else content})

        loop_state: dict = {}
        try:
            final = await _run_tool_loop(rt, client, project, messages, content, user_msg.message_id,
                                         thinking=thinking, event_cb=event_cb,
                                         state=loop_state, cancel_event=cancel_event,
                                         tool_surface=_tool_surface())
            assistant_text = final["text"]
            created = final["created"]
            updated = final["updated"]
            deleted = final["deleted"]
            views_generated = final.get("views_generated", False)
            resources_mutated = final.get("resources_mutated", False)
            events = final["events"]
            receipts = final.get("receipts", [])
            trace = final.get("trace", [])
            reasoning = final.get("reasonings", [])
            search_sources = final.get("search_sources", [])
        except _TurnStopped:
            log.info("character_designer_turn 被停止（project=%s session=%s）", project, session_id)
            done = _committed_char_names(loop_state.get("receipts", []))
            done_str = ("已完成：" + "、".join(done)) if done else "本次没有已完成的修改"
            assistant_text = f"[任务已停止] {done_str}；其余未完成部分未执行。"[:300]
            status = ResultStatus.STOPPED.value
            events = loop_state.get("events", [])
            receipts = loop_state.get("receipts", [])
            trace = loop_state.get("trace", [])
            reasoning = loop_state.get("reasonings", [])
            search_sources = loop_state.get("search_sources", [])
        except Exception as e:
            log.exception("character_designer_turn 工具循环异常（project=%s session=%s）",
                          project, session_id)
            e_desc = str(e).strip()
            assistant_text = f"[执行失败: {type(e).__name__}: {e_desc}]"[:300]
            status = ResultStatus.FAILED.value
            events = loop_state.get("events", [])
            receipts = loop_state.get("receipts", [])
            trace = loop_state.get("trace", [])
            reasoning = loop_state.get("reasonings", [])
            search_sources = loop_state.get("search_sources", [])

    if created:
        intent = "character_create"
    elif updated:
        intent = "character_update"
    elif deleted:
        intent = "character_delete"
    elif views_generated:
        intent = "character_views"
    elif resources_mutated:
        intent = "resource_change"
    else:
        intent = "conversation"

    receipts_meta = []
    generated_views: list = []
    for r in receipts:
        receipts_meta.append({
            "operation": r.get("operation"), "ok": bool(r.get("ok")),
            "verified": bool(r.get("verified")), "stale": bool(r.get("stale")),
            "unchanged": bool(r.get("unchanged")),
            "error": r.get("error"),
            "character_name": r.get("character_name"),
            "changed": r.get("changed"),
            "revision": r.get("revision"),
            # stale 时返回的最新状态 / 删除时的清理明细（供上层与测试核对）
            "expected_revision": r.get("expected_revision"),
            "current_revision": r.get("current_revision"),
            "current_character": r.get("current_character"),
            "current_characters": r.get("current_characters"),
            "removed_files": r.get("removed_files"),
            "draft_removed": r.get("draft_removed"),
            # generate_views：真实生成的三视图（local 路径，供前端在窗口渲染图片网格）
            "generated": r.get("generated"),
            "character_names": r.get("character_names"),
            "elapsed_s": r.get("elapsed_s"),
            # Phase B（Resource Agent）：resources.json 域回执字段透传——
            # Producer._derive_changed_refs 只从这些真实回执字段派生资源 changed_refs。
            "resource_kind": r.get("resource_kind"),
            "resource_id": r.get("resource_id"),
            "changed_resources": r.get("changed_resources"),
            "clothing_id": r.get("clothing_id"),
            "look_id": r.get("look_id"),
            "prop_id": r.get("prop_id"),
            "composition_id": r.get("composition_id"),
            "asset_id": r.get("asset_id"),
            "candidate_asset_ids": r.get("candidate_asset_ids"),
        })
        if (r.get("operation") == "character.generate_views" and r.get("ok")):
            for g in (r.get("generated") or []):
                for view, local in (g.get("views") or {}).items():
                    generated_views.append({"character_name": g.get("character_name"),
                                            "view": view, "path": local})
    meta = {"intent": intent, "events": events, "evidence_refs": [],
            "receipts": receipts_meta,
            "write_attempts": len(receipts_meta),
            "write_success": sum(1 for r in receipts_meta
                                 if r["ok"] and r["verified"] and not r["unchanged"]),
            "trace": trace,
            "reasonings": reasoning,
            "search_sources": search_sources,
            "generated_views": generated_views,   # 本轮真实生成的三视图（路径，窗口渲染用）
            # Skill System V1（40.md §十三）：本 turn 技能激活/加载证据
            "skills": _get_skill_runtime().collect_and_reset("character_designer"),
            "pending_delete_scenes": []}
    asst = AgentMessage(role="assistant", content=assistant_text, status=status, meta=meta)
    store.append_message(session_id, asst)

    return {"assistant_message": assistant_text,
            "created": created, "updated": updated, "deleted": deleted,
            "views_generated": views_generated,
            "resources_mutated": resources_mutated,
            "events": events, "message": asst.to_dict(),
            "intent": intent, "receipts": receipts_meta, "trace": trace,
            "search_sources": search_sources,
            "evidence_refs": [], "linked_task_ids": [],
            "linked_change_session_id": None, "session_id": session_id,
            "agent_id": "character_designer"}
