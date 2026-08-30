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

log = logging.getLogger("agents.character_designer")

# 防死循环（LLM 循环步数），非业务数量限制。
DEFAULT_MAX_TOOL_STEPS = 32
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
                        "story.read", "script.read"}

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


# ============ system ============
def _build_system(proj) -> str:
    """精简 system：职责/权限/工具说明/项目元信息。不注入角色全文或剧情——按需用工具取。"""
    return CHARACTER_DESIGNER_SYSTEM.format(
        project_name=proj.name or "（未命名）",
        story_len=len(proj.story_text or ""),
        scenes_count=len(proj.scenes or []),
        characters_count=len(proj.characters or []),
    )


CHARACTER_DESIGNER_SYSTEM = """你是 AGNES 的 Character Designer，负责项目的【角色设定】——project.json 里角色的 canonical 数据。
你【只写角色】：不修改剧本正文、不增删改故事段、不创建/修改镜头、不生成图片。剧本与故事段是你的参考资料，不是写入对象。

项目：{project_name}（剧本正文 {story_len} 字，{scenes_count} 个故事段，现有 {characters_count} 个角色）。

角色 canonical 字段（现有数据模型，只有这些；不要假设存在其它字段）：
- name：角色名。项目内唯一标识，故事段/镜头按【名字】引用角色。创建后不要试图改名。
- description_en：角色的英文视觉描述（生成三视图/首帧时直接作为 prompt 使用）。这是角色设定唯一的 canonical 文本字段：年龄、性别、身份、外貌、发型、服装、体型、识别特征都组织在这里。
- views：三视图图片资产（front/side/back），由图片流程管理，你不能读写图片本身。

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
- web.search(query)：搜索现实世界资料，返回约 3 条「标题+摘要+链接」。仅当①用户明确要求查资料，或②设计涉及真实世界知识明显影响可信度（历史时期服装、民族服饰、职业制服、年代发型、真实文化背景、地域审美）时使用。普通修改（改发型/换配色）不搜索。每轮最多 3 次，超限会被系统拒绝。

🔴 最重要的铁律（违反即失败）：
- 你只有通过【真正发起工具调用】（function/tool_calls）才能读/写数据。在回复里说「已创建/已修改/已删除」但没有真正调用对应工具，等于什么都没做——这是严重错误。
- 只能依据工具【真实返回】的结果向用户汇报；没看到 ok=true 就不能声称成功。不要编造角色名、指纹、数量。
- 用户要求修改角色时，必须用 character.update 修改【已有角色】，绝不 delete 后 recreate——那会破坏角色名引用、三视图关联与一致性。
- 你没有写剧本/故事段的工具：任何情况下都不要试图改剧情去配合角色。

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

【工作方式】
1. 根据故事创建角色：character.list → story.list → 按需 story.read → character.create。description_en 用英文写成可直接用于图片生成的【稳定角色描述】：外貌/发型/服装/配色/2-4 个长期识别特征；与故事明确设定一致；与项目已有角色在轮廓/颜色上可区分；不含镜头/运镜/背景故事。
2. 用户要求修改：character.read → character.update（只改相关部分）。
3. 用户要求删除：character.read（确认存在与引用）→ character.delete → 用 character.list 核对。
4. 用户要求生成图片（三视图/角色图/立绘/出图）：确认角色文本设定完善 → character.generate_views（1 个角色直接传单元素数组；「男女主和反派都生成」传 3 元素数组并发生成）→ 依据工具真实返回汇报（完成了几个、图片是否已展示）。「给X生成/生成X/出图」是【明确的执行指令】，直接调用工具完成，【不要】反问"需要我生成吗"。只有用户在纯粹询问可能性（"能不能生成图片？""你们支持生图吗？"）且没有任何明确目标时才先回答不调用工具。
5. 任一写工具返回 stale=true：你读取后内容被改动过、未写入；用返回的最新内容和新指纹重新判断后重试。
6. 一次只做用户要求的修改，不顺手改无关角色。

【写工具必须一个一个来】（重要）：
- character.create / character.update / character.delete / character.generate_views 必须【一个一个执行】：每发一个写工具调用，等它返回最新状态/指纹后，再发下一个。同一次回复里发了多个写工具时，系统只执行第 1 个，其余会被拒绝。
- character.generate_views 一次调用就能并发生成最多 3 个角色（把名字都放进 character_names 数组），不要拆成多次调用。
- 批量设计多个角色（如「设计男主、女主和反派」）：逐个执行——先读取剧情，然后一个角色的 create/update 完成（看到真实回执）后再做下一个；每个角色创建前都在心里对照 character.list 的最新结果，避免重复。
- 只读工具（character.list / character.read / story.list / story.read / script.read / web.search）可以一次发多个，它们不碰数据、不冲突。

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
    WEB_SEARCH_TOOL,
]


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

    # 外观签名 = sha256(description_en + art_style)：描述未变且已有图 → handler 自动跳过
    #（不重复生成）；描述已改 → sig 变化 → 全部重新生成（一致性语义）。
    sig = character_appearance_sig(char.description_en or "", proj.style.art_style or "")
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


TOOL_EXECUTORS = {
    "character.list": _execute_character_list,
    "character.read": _execute_character_read,
    "character.create": _execute_character_create,
    "character.update": _execute_character_update,
    "character.delete": _execute_character_delete,
    "character.generate_views": _execute_character_generate_views,
    "story.list": _execute_story_list,
    "story.read": _execute_story_read,
    "script.read": _execute_script_read,
    "web.search": _execute_web_search,
}


# ============ 回执渲染 / 真相收口（角色域措辞）============
_TOOL_LABELS = {
    "character.list": "读取角色列表", "character.read": "读取角色",
    "character.create": "创建角色", "character.update": "修改角色",
    "character.delete": "删除角色", "character.generate_views": "生成角色三视图",
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
        return "本轮没有任何写工具被真正调用（没有 character.create / character.update / character.delete 的工具调用记录）。"
    lines = []
    for r in receipts:
        op = r.get("operation")
        if r.get("ok"):
            if r.get("unchanged"):
                lines.append(f"- {op}：ok=true，但内容无变化（unchanged），canonical 未改动。")
            else:
                lines.append(f"- {op}（{r.get('character_name')}）：ok=true，已重新读取 canonical 确认落盘。")
        else:
            why = r.get("error") or "未知错误"
            if r.get("stale"):
                why = "stale（版本过期，被拒绝，未写入）"
            lines.append(f"- {op}（{r.get('character_name')}）：ok=false，原因：{why}。canonical 未被该调用改动。")
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
    canonical 生成，不信模型叙述。纯追加，不删改模型原文。"""
    real_changes = [r for r in receipts if r.get("ok") and r.get("verified") and not r.get("unchanged")]
    if not real_changes:
        return text
    try:
        proj = _reload(rt, project)
        names = [c.name for c in (proj.characters or [])]
        lines = ["\n\n---\n【实际执行记录】（系统据真实回执生成，非模型叙述）"]
        for r in real_changes:
            ok = "✓" if r.get("ok") else "✗"
            lines.append(f"{ok} {_op_human_char(r)}")
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
        messages[0] = {"role": "system", "content": _build_system(proj)}
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
                         cancel_event=None) -> dict:
    """原生 function-calling 循环 + 执行真实性收口（结构性，非提示词）。"""
    created = updated = deleted = views_generated = False
    if state is None:
        state = {}
    events: list = state.setdefault("events", [])
    receipts: list = state.setdefault("receipts", [])
    write_attempts = 0
    corrective_round = 0
    zero_write_recovery_used = False
    trace: list = state.setdefault("trace", [])
    reasonings: list = state.setdefault("reasonings", [])
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

    for step in range(max_steps):
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
                        thinking=thinking, tools=CHARACTER_DESIGNER_TOOLS,
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
                    tools=CHARACTER_DESIGNER_TOOLS, tool_choice="auto")
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
                    "events": events, "receipts": receipts, "trace": trace,
                    "reasonings": reasonings, "search_sources": search_sources}

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

            is_write_tool = (name in CHARACTER_WRITE_OPERATIONS)
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

            # 写域调用记入账本（含拼错的 character.* 未知工具：也算写尝试失败）。
            is_write_domain = (name in CHARACTER_WRITE_OPERATIONS
                               or name.startswith("character."))
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
            if (name in CHARACTER_WRITE_OPERATIONS and obs.get("ok")
                    and obs.get("verified") and not obs.get("unchanged")):
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

    # 预算耗尽（防死循环）：诚实告知已完成的真实工作。
    done = [r for r in receipts if r.get("ok") and r.get("verified") and not r.get("unchanged")]
    if done:
        names = _committed_char_names(receipts)
        tail = (f"已真实完成：{', '.join(_op_human_char(r) for r in done)}。" if names
                else f"已完成部分工作（{len(done)} 次成功写入）。")
        text = (f"（已达到执行预算上限 {max_steps} 步，本轮停止——这是防死循环的保护，不是"
                f"业务数量限制。{tail}还有未完成的部分，请告诉我继续，或把剩余需求拆小一些。）")
    else:
        text = (f"（已达到执行预算上限 {max_steps} 步，本轮停止——这是防死循环的保护。"
                f"本轮没有任何成功写入。请把需求拆小一些再试。）")
    return {"text": text,
            "created": created, "updated": updated, "deleted": deleted,
            "views_generated": views_generated,
            "events": events, "receipts": receipts, "trace": trace,
            "reasonings": reasonings, "search_sources": search_sources}


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
    history = [{"role": m.role, "content": m.content}
               for m in session.messages[:-1]
               if m.role in ("user", "assistant") and m.content and m.status is None]

    client = rt._resolve_client_or_none(agent_id="character_designer")
    assistant_text: str
    status: Optional[str] = None
    created = updated = deleted = views_generated = False
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
        system = _build_system(proj)
        mem = rt._memory_supplement(project, "character_designer")
        if mem:
            system += "\n\n" + mem
        messages: list[dict] = [{"role": "system", "content": system}]
        messages += history
        messages.append({"role": "user", "content": content})

        loop_state: dict = {}
        try:
            final = await _run_tool_loop(rt, client, project, messages, content, user_msg.message_id,
                                         thinking=thinking, event_cb=event_cb,
                                         state=loop_state, cancel_event=cancel_event)
            assistant_text = final["text"]
            created = final["created"]
            updated = final["updated"]
            deleted = final["deleted"]
            views_generated = final.get("views_generated", False)
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
            "pending_delete_scenes": []}
    asst = AgentMessage(role="assistant", content=assistant_text, status=status, meta=meta)
    store.append_message(session_id, asst)

    return {"assistant_message": assistant_text,
            "created": created, "updated": updated, "deleted": deleted,
            "views_generated": views_generated,
            "events": events, "message": asst.to_dict(),
            "intent": intent, "receipts": receipts_meta, "trace": trace,
            "search_sources": search_sources,
            "evidence_refs": [], "linked_task_ids": [],
            "linked_change_session_id": None, "session_id": session_id,
            "agent_id": "character_designer"}
