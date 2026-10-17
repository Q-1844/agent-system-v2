# src/agents/director.py
# Director 正式 Agent（原生 function-calling tool-loop，2026-08-30）。
#
# 职责：分镜模块的完整控制权——把故事段转换成可用于动画制作的 Shot 结构，
#       并持续创建/修改/删除/排序这些镜头。像导演一样工作：镜头数量、景别、
#       机位、运镜、节奏由模型按剧情自主判断，本文件只定边界与工具，不写僵化的
#       分镜公式（不规定"每段固定 N 镜"或固定景别序列）。
#
# 写入域：只有镜头 canonical（project.json 的 scenes[].shots[] 创作字段）。
#   故事段结构/剧本正文（story_text/scenes 内容）归 Script Writer、角色归
#   Character Designer——它们是 Director 的【参考资料】（读工具按需取），绝不是写入对象；
#   Director 也没有任何 story.*/character.* 写工具（工具层天然挡掉，非提示词约束）。
#   分镜图/视频生成（2026-08-31 Director Visual Production）：shot.generate_images /
#   shot.generate_videos（见 director_visual.py）——只决定生成何时发生，复用既有
#   first_frame/last_frame/video JobQueue 管线，不重新实现任何生成算法。
#
# 工具（按需读取，最小充分上下文）：
#   读：shot.list / shot.read / story.list / story.read / script.read /
#       character.list / character.read / web.search
#   写：shot.create / shot.update / shot.delete / shot.move
#   批量写（各占一个单写槽，整批一次 OCC + 一次原子写）：
#        shot.create_many / shot.update_many
#   视觉生产（长任务，占一个单写槽，锁 storyboard；部分成功语义）：
#        shot.generate_images / shot.generate_videos
#   一个故事段天然拆成多个 Shot 且单 Shot 内容短，成组创建/修改是主工作流，
#   batch 上限按 Shot schema 大小与 tool-call 稳定性取 10（update 12）。
#
# 复用 Script Writer / Character Designer 已验证的基础设施（import 共享，不改其文件）：
#   web.search 执行器 + 预算/重复查询拦截、story.list/story.read/script.read 只读执行器、
#   character.list/character.read 只读执行器、统一回执骨架 _base_receipt、
#   宽容反序列化 _loose_json/_parse_args、真相闸门正则（_claims_success /
#   _is_explicit_write_request / _is_chat_turn）、【实际执行记录】保留块清除、
#   _TurnStopped 协作式停止、活跃生产护栏 _active_production_reason。
#   tool-loop 机制（单写串行/回执账本/zero-write recovery/重复失败护栏/超时恢复/
#   流式事件/取消）按分镜域镜像实现——不改冻结的 script_writer.py。
#
# 乐观并发（optimistic concurrency，工具自身正确性保护，字段级作用域）：
#   - _shots_token：全项目镜头【结构】指纹（sha256 over 各段 shot_id 有序列表前 8 位）。
#     shot.create / shot.create_many / shot.delete / shot.move 校验 expected_shots_revision。
#     镜头增/删/移动会变；镜头内容修改、故事段内容修改、剧本正文修改都不影响。
#     （SW 的 story.delete 会级联删镜头 → 指纹变化 → Director 旧指纹被正确拒绝。）
#   - _shot_token：单个镜头【内容】指纹（白名单可写创作字段的有序映射）。
#     shot.update 校验 expected_revision（镜头级指纹）。
#   - _shots_content_token：全项目镜头【内容】指纹（每镜可写内容字段序列）。
#     shot.update_many 校验 expected_revision（集合级指纹）。
#   stale → 拒绝写入（不落盘/不分配 ID/不审计），返回最新内容 + 新指纹供 Agent 重新判断。
#   每次写后无条件重读 canonical 验证确实落盘，receipts 是唯一权威执行事实。
#
# 顺序模型（structure_service）：镜头权威顺序 = scene.shots 数组位置，Shot.index 只是
#   显示号（任何位置变动后 renumber）。shot_id 是身份证号：插入/移动不重排已有 id，
#   新镜头取段内下一个 _cNN 单调序号，删除不回收。move 只支持同段内单步移动（第一版
#   不做 move_many/reorder——镜头顺序是状态依赖操作，前一次结构变化会改变下一次
#   position 语义；多镜重排走多次 shot.move，每步用最新指纹）。
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any, Optional

import httpx

from src.core.models import Asset, Project, Shot, ShotStatus
from src.core.store import ProjectStore
from src.core.atomic import atomic_write_json
from src.storyboard.structure_service import (
    StructureError, insert_shots_after, move_shot,
    update_shot_fields, update_shot_relay, read_shot, renumber_shot_indices,
    _invalidate_stale_prompts,
)

from .models import AgentMessage, ResultStatus
from .runtime import AgentRuntime, AgentError
from .revision_audit import DirectEditAuditStore
# —— 视觉生产工具（schema + executor，本文件只注册接线）——
from .director_visual import (
    SHOT_GENERATE_IMAGES_TOOL,
    SHOT_GENERATE_TOOL_NAMES,
    SHOT_GENERATE_VIDEOS_TOOL,
    _execute_shot_generate_images,
    _execute_shot_generate_videos,
)
# —— 视觉审查工具（Visual Review Tool V1：只读、fresh-context，本文件只注册接线）——
from .director_production import (
    _execute_shot_production_bind,
    _execute_shot_production_inspect,
    _execute_shot_production_refresh,
    _execute_visual_profile_read,
)
from .director_vision_review import (
    VISION_REVIEW_TOOL,
    _execute_visual_review,
)
from .shot_review import (
    REVIEW_SHOT_TOOL,
    _execute_review_shot,
)
# —— Phase 39（39.md §6）：连续性校准 / 帧级只读 / keyframes 桥工具（接线）——
from .director_calibration import (
    CALIBRATION_SEAM_TOOL,
    CALIBRATION_RUN_CREATE_TOOL,
    CALIBRATION_TRACE_READ_TOOL,
    CALIBRATION_TRACE_UPSERT_TOOL,
    TOOL_EXECUTORS as _CALIBRATION_EXECUTORS,
)
from .director_frames import (
    VIDEO_EXTRACT_FRAME_TOOL,
    VISUAL_SAMPLE_FRAMES_TOOL,
    TOOL_EXECUTORS as _FRAMES_EXECUTORS,
)
from .director_bridge import (
    SHOT_BRIDGE_CREATE_TOOL,
    TOOL_EXECUTORS as _BRIDGE_EXECUTORS,
)
# —— Skill System V1（40.md）：生产技能工具面（skill.list/load/read_resource，
#    只读、不占写槽；catalog 注入 _build_system；hint 注入 director_turn）——
from .skill_system.tools import (
    SKILL_TOOLS,
    SKILL_TOOL_NAMES,
    activation_hint as _skill_activation_hint,
    make_skill_executors,
)
from .skill_system.runtime import get_skill_runtime as _get_skill_runtime
# —— Script Writer 共享基础设施（直接 import 复用，不改其文件）——
from .script_writer import (
    MAX_WEB_SEARCH,
    _TurnStopped,
    _active_production_reason,
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
# —— Character Designer 只读执行器（角色参考资料）——
from .character_designer import _execute_character_list, _execute_character_read
# —— 会话治理共享原语（zero-read gate 检测 / ID 勘误 / Clean Review / Re-grounding）——
from . import session_governance

log = logging.getLogger("agents.director")

# 执行窗口大小（renewable window）：单次窗口用满后注入 self-check nudge 并续接
# 新窗口（次数不限），由 Agent 自行判断继续/停止——不再硬停长任务。仅防单次窗口
# 内的失控；24h 级连续生产由 renewable window + self-check 承载，非业务数量限制。
DEFAULT_MAX_TOOL_STEPS = 1024
# 真相收口：最终回复纠偏最多 1 次；仍不实则 Runtime 确定性覆写。
MAX_CORRECTIVE_ROUNDS = 1
# 防失控：连续同一工具同一失败签名达此阈值 → 诚实停止。
MAX_REPEAT_FAIL = 3
# 超时恢复：LLM read timeout 最多恢复重试次数。
MAX_TIMEOUT_RETRIES = 2
# 批量工具上限（tool-call JSON 长度/稳定性平衡；Shot 单项含拍摄字段比故事段短，
# 10 镜一批的参数量约为 story.create_many 5 段的 1~1.5 倍）。超时降级链 10→5→2→1。
MAX_CREATE_BATCH = 10
MAX_UPDATE_BATCH = 12
_BATCH_DOWNGRADE = {10: 5, 5: 2, 2: 1, 1: 1}

# 写工具集合（只有这些可能产生 canonical 变更，需要回执 + 落盘校验 + 单写串行 + 资源锁）。
# 批量工具各占一个单写槽。视觉生成工具复用既有生成管线写资产 canonical（批次/视频版本），
# 同样占单写槽 + 锁 storyboard + 发 storyboard.changed（verified 语义 = canonical+文件存在）。
DIRECTOR_WRITE_OPERATIONS = {"shot.create", "shot.create_many", "shot.update",
                             "shot.update_many", "shot.delete", "shot.move",
                             "shot.generate_images", "shot.generate_videos",
                             # Phase C1（27.md §1.1）：ShotProductionBinding 生产意图归 Director
                             "shot.production.bind", "shot.production.refresh",
                             # Phase D2（30.md §21/§22）：候选选定 = generate→review→
                             # compare→choose 闭环的最后一环，由 Director 完成
                             "shot.select_frame",
                             # Phase 39（39.md §6.2/§6.4）：keyframes 桥构造（canonical
                             # 变更：插镜/relay/binding/seed）；校准 trace 写（非 canonical
                             # 但属状态 mutation，占写槽；回执用 trace_key 不进 changed_refs）
                             "shot.bridge_create", "calibration.trace_upsert",
                             # 42.md 缺陷①：run 创建同 trace_upsert（非 canonical
                             # 状态 mutation，占写槽；回执 run_id 不进 changed_refs）
                             "calibration.run_create"}
# 长任务视觉工具（loop 侧额外传 event_cb/cancel_event；对生成类工具豁免「写后重读」
# 收尾判定——executor 内部已做「重读 canonical + 文件存在」验证，强于 shot.list）。
DIRECTOR_LONG_TASK_TOOLS = {"shot.generate_images", "shot.generate_videos",
                            "shot.bridge_create"}
# 只读工具（不占写槽、可一次多个；用于「写后重读最终状态」的收尾判定）。
DIRECTOR_READ_TOOLS = {"shot.list", "shot.read", "story.list", "story.read",
                       "script.read", "character.list", "character.read",
                       "project.visual_profile.read", "shot.production.inspect",
                       # Phase 39：连续性校准只读测量与 trace 读、帧级只读工具
                       "calibration.seam_measure", "calibration.trace_read",
                       "video.extract_frame", "visual.sample_frames"}


# ============ 乐观并发指纹（分镜域，字段级作用域）============
def _shots_token(scenes) -> str:
    """全项目镜头【结构】指纹（sha256 前 8 位）。哈希对象 = 各段 shot_id 的有序列表
    （段间含空段占位）：镜头增/删/移动会变；镜头内容修改、故事段内容修改、剧本正文
    修改都不影响。供 shot.create / shot.delete / shot.move 乐观并发。"""
    payload = json.dumps([[sh.shot_id for sh in (sc.shots or [])]
                          for sc in (scenes or [])], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


# shot.update 允许修改的镜头【创作内容】字段（与 structure_service 白名单一致；
# 明确排除：shot_id/scene_id（身份）、index（顺序由结构操作决定）、产物/批次/状态字段）。
# source_shot_id（接力来源）是结构关系字段：进本白名单是为了让内容指纹
# （_shot_token/_shots_content_token）覆盖接力变更（OCC 保护），但它的写入不走
# update_shot_fields（内容白名单不含它），由各执行器显式调 update_shot_relay
# （存在/同段/无环/相邻校验）。创建时不可指定（新镜 id 未知，先建后锚）。
SHOT_UPDATABLE_FIELDS = (
    "description", "characters", "target_duration_s", "dialogue",
    "negative_prompt",   # Phase 39D：负面约束的正式生成字段（shot.update 可写；
                         # native_av_pack 携带为独立 API 参数——P39C 实证只进 trace 无效）
    "shot_size", "camera", "camera_angle", "camera_movement",
    "composition", "lighting", "motion", "motion_pace",
    "environment_motion", "action_sequence",
    "opening_state", "important_details", "required_elements",
    "last_frame_description", "end_state", "frame_change",
    "add_elements", "remove_elements", "end_composition",
    "preserve_elements", "preserve_custom", "transition_description",
    "video_preserve_elements", "video_preserve_custom",
    "note",
    "link_mode",   # Phase D §25.C：i2v | keyframes（尾帧生产前置）；34.md §13 增 multi
    "video_reference_asset_id",   # 34.md Human Gate：multi 按镜参考（须 ∈ look approved）
    "source_shot_id",
    # Phase56 B/A2：接力声明通道——opening_source（independent/reference_previous/
    # inherit_previous；independent 同时解除锚）与 opening_cut_reason（independent
    # 的显式切镜依据 same_scene_recam/scene_change/time_jump；v2 接续链契约必填）。
    # 值校验在 update_shot_fields（非法=显式 StructureError，不静默忽略）。
    "opening_source",
    "opening_cut_reason",
)
_LIST_FIELDS = ("characters", "required_elements", "preserve_elements",
                "video_preserve_elements")


def _shot_view(shot: Shot) -> dict:
    """单个镜头【可写内容字段】的有序视图（与 SHOT_UPDATABLE_FIELDS 对齐，stale 时
    返回给 Agent 对比用）。"""
    return {k: (list(getattr(shot, k, []) or []) if k in _LIST_FIELDS
                else getattr(shot, k))
            for k in SHOT_UPDATABLE_FIELDS}


def _shot_token(shot: Shot) -> str:
    """单个镜头的内容指纹（sha256 前 8 位）。哈希对象 = 该镜头【可写内容字段】的有序
    映射。段内这些内容改动会变；shot_id/顺序变化、其它镜头改动、故事段内容改动都不影响
    （字段级/镜头级作用域）。与 _shots_token（结构指纹）相互独立。"""
    payload = json.dumps(_shot_view(shot), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _shots_content_token(scenes) -> str:
    """全项目镜头【内容】指纹（sha256 前 8 位）。任一镜头的可写内容改动都会变；
    与 _shots_token（结构指纹）相互独立——增/删/移动会同时变两者，但内容改动只在本
    令牌体现。供 shot.update_many 一次性校验多镜内容基线。"""
    payload = json.dumps([_shot_view(sh) for sc in (scenes or [])
                          for sh in (sc.shots or [])],
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


# ============ system ============
# Sub-Agent 工具挂载时（subagent_enabled，tools.mount_subagent_tools）注入的
# 系统提示追加段；未挂载 = None → _build_system 输出与基线逐字节一致。
_SUBAGENT_SYSTEM_SUFFIX = None


def _build_system(proj, pdir=None) -> str:
    """精简 system：只放职责、权限、工具说明、项目元信息（名称/段数/镜头数）。
    不注入完整 story_text / scenes / shots 列表——Agent 需要时用读工具按需取。
    Phase C1（27.md §5）：注入 ProjectVisualProfile 一行摘要（生产风格真相）。"""
    n_shots = sum(len(sc.shots or []) for sc in (proj.scenes or []))
    vp_summary = "（未知）"
    if pdir is not None:
        from .production_context import profile_summary_line
        vp_summary = profile_summary_line(pdir)
    base = DIRECTOR_SYSTEM.format(
        project_name=proj.name or "（未命名）",
        scenes_count=len(proj.scenes or []),
        shots_count=n_shots,
        max_create_batch=MAX_CREATE_BATCH,
        max_update_batch=MAX_UPDATE_BATCH,
        visual_profile_summary=vp_summary,
    )
    if _SUBAGENT_SYSTEM_SUFFIX:
        base += "\n\n" + _SUBAGENT_SYSTEM_SUFFIX
    # Skill System V1（40.md §七）：L1 catalog 常驻尾部（registry mtime 缓存，
    # 每轮渲染确定性且廉价；Agent 只看到属于自己的 Skill view）
    from .skill_system.runtime import get_skill_runtime
    base += get_skill_runtime().catalog_block("director")
    return base


DIRECTOR_SYSTEM = """你是 AGNES 的 Director（导演/分镜设计者），负责项目的【分镜模块】——把每个故事段的剧情转换成可用于动画制作的镜头（Shot）结构，并持续创建、修改、删除、排序这些镜头，维护镜头的拍摄方案（景别/机位/运镜/构图/光线/动作/节奏）。
镜头数量、景别变化、机位与运镜、节奏与信息释放由你按剧情需要做专业判断：像真正的导演那样设计，而不是机械地把故事文本切成等长的句子。项目里已有的镜头结构、拍摄方案、风格基调是你的创作基础，用户没有要求改变的部分尽量保留。

【导演核心原则——你在决定观众此刻看什么，以及不看什么】
你不是在描述「画面里有什么」。每一镜先在心里回答：这镜的叙事目的是什么？观众的视觉注意力应该在哪里？哪个信息该藏、哪个该露？人物该占画面多大？场景该露出多少？为什么是这个景别而不是别的？与上一镜的构图有什么变化、与下一镜如何形成节奏？——这些是推理纪律，不是要写进镜头字段的清单。
场景已经由 Scene System 建立（每个 Location 的空间身份有正式参考）：分镜不需要每一镜都重新展示完整场景。场景完整性 ≠ 场景完整入镜。
- 远景/大远景（establishing/EWS/WS）：空间身份重要，观众需要看清「这是哪里、人物在哪里」——环境做主体，人物可以是小比例。
- 中景（MS）：以人物互动或动作为主体，环境退为支撑；不要默认「完整场景+人物稍大」。
- 近景/特写（MCU/CU/ECU）：核心是【有意排除信息】——背景只剩局部墙面/灯光/雨/门框等极小提示，甚至只剩眼睛/手/道具局部。画面里不需要出现整个房间或院子，这不是缺陷，是景别语言本身。
- OTS（过肩）：前景人物的肩/头遮挡 + 焦点人物的脸是画面主体。OTS 成立的证据是遮挡与焦点，不是「两个人都在画面里」。若最终画面变成两人并排站在完整院子里，那不是 OTS。
- insert/道具特写：锁道具+局部（手/表面/接触点），环境只留语义提示。
- 接力（inherit_previous/接力镜头）是【动作与时空的连续】，不是构图复制：接力镜仍要按自己的叙事目的选景别与机位——cut 的自由永远保留。

项目：{project_name}（{scenes_count} 个故事段，共 {shots_count} 个镜头）。
项目视觉定义（ProjectVisualProfile——全项目生成风格真相，你只能读不能改）：{visual_profile_summary}

权限边界（硬约束）：
- 你【只】写镜头 canonical：创建/修改/删除/移动镜头及其拍摄字段，以及用生成工具为镜头产出分镜图/视频资产。
- 你【不能】修改角色设定：角色三视图缺图时不要自己补角色资产，可以提示用户先在角色页完善并生成角色参考图（分镜图生成会自动引用它们保持角色一致）。
- 故事段（剧情文本）与剧本正文归 Script Writer、角色设定归 Character Designer——你可以【读】它们做导演依据，但【没有】任何修改它们的工具。如果发现故事段本身有剧情问题（不适合拍、逻辑断裂、信息缺失），直接告诉用户你的判断和建议，不要试图绕过去修改故事。

你有这些工具：
【读（不落盘，可一次发多个）】
- story.list()：故事段地图（position 第几段/scene_id/摘要/角色/镜头数）。用户说「第8段」时用它把 position=8 解析成稳定 scene_id。做分镜任务的第一步。
- story.read(scene_id)：读一个故事段的完整剧情文本。局部任务读目标段 + 前后相邻约 2~4 段即可（看剧情衔接/情绪延续）；信息不足再继续扩大，不要默认通读全文。
- script.read()：剧本正文全文。仅在全片分镜检查、整体节奏分析、用户明确要求通读时用；单段落分镜不需要。
- character.list()：角色 canonical 的【权威名单】（name/一句话描述/是否有三视图）。涉及角色时【先调它】确认项目里真实建立了哪些角色。
- character.read(character_name)：读一个【已在 character.list 名单中】角色的完整设定（外貌/身份/服装/气质）。🔴 只能 read 名单里真实存在的角色：故事段 characters 列表或剧情里出现、但 character.list 名单里【没有】的名字，说明该人物没有建立角色 canonical（多半只是被提及/画外音/回忆中人物，不需要跨镜头视觉一致）——【不要】对这种名字调 character.read（会返回"角色不存在"），也【不要】把它写进镜头的 characters 字段。需要视觉一致性、能被分镜图引用三视图的，只有 canonical 名单里的角色。给某段设计分镜前：先 character.list 拿名单，再只 read 名单中在该段实际出场的角色，让画面设计符合角色形象。你从文本设定理解角色，不假装看到图片内容。
- shot.list(scene_id?)：镜头地图。不带参数列所有故事段（含每段镜头 id 列表）；带 scene_id 列该段镜头的轻量信息（顺序/一句话描述/角色/时长/景别/是否有首帧/接力关系）。判断「这组镜头现状」用它，不要凭记忆。
- shot.read(shot_id)：读一个镜头的完整拍摄方案 + 该镜头内容指纹。改某个镜头前先读它。
- web.search(query)：搜索现实世界资料（真实职业动作/历史时期场景/摄影术语/设备与空间/地点参考）。仅当这些现实知识明显影响分镜可信度，或用户明确要求参考时使用；普通分镜设计不搜索。每轮最多 3 次。

【写（必须一个一个来，每次用返回的最新指纹）】
- shot.create(scene_id, description, …, after_shot_id?, expected_shots_revision)：给故事段补 1 个镜头（after_shot_id 省略则追加段尾）。只补 1 镜时用本工具。
- shot.create_many(scene_id, shots[], after_shot_id?, expected_shots_revision)：一次创建一组镜头（≥2 镜；只补 1 镜用 shot.create）。【给一个故事段从无到有设计整组分镜，用本工具一次提交】；shots 数组顺序即镜头顺序。整批一次校验+一次原子写入，任一非法整批不写。
- shot.update(shot_id, expected_revision, …)：精准修改一个镜头的任意拍摄字段（只传要改的）。用户说「第3镜改成近景」这类单镜要求时用本工具，不要重建整组。改锚/解除接力也用它：传 source_shot_id=新来源（须先 move 到来源之后）或 null 解除；接力声明传 opening_source=inherit_previous/reference_previous（配合 source_shot_id），解除传 independent；independent 切镜须显式传 opening_cut_reason（same_scene_recam/scene_change/time_jump）。
- shot.update_many(updates[], expected_revision)：一次修改 ≥{max_update_batch_prev} 个互相独立的镜头（「这组镜头整体重新设计得更有张力」）。每项 shot_id + 要改的字段；同镜多字段合并到一项。整批一次校验+一次原子写入。
- shot.delete(shot_id)：删除一个镜头（系统会级联删除接力依赖它的下游镜头，并保留已生成文件）。用户明确要求删除时直接调用。
- shot.move(shot_id, before_shot_id?/after_shot_id?, expected_shots_revision)：把一个镜头在同段内移到某镜之前/之后（都省略则移到段尾）。镜头顺序是状态依赖操作：多个移动必须一步一步来，每步用最新指纹，不要试图一次重排整组。
- shot.select_frame(shot_id, candidate_id, frame?)：选定该镜头的首帧/尾帧候选（默认首帧）。闭环：shot.generate_images 生成 → visual.review_shot 审查（hard gate 失败的候选绝不能选定）→ 比较候选 → 本工具选定。

【视觉生产（分镜图/视频生成，长任务）】
- shot.generate_images(targets[], variants?, force?, scene_reference_mode?)：批量为镜头生成分镜图（frame=first 首帧 / frame=last 尾帧）。复用系统既有生成管线：自动带角色参考图与接力参考图保持角色一致性；生成后写入镜头数据并在分镜页展示（你看不到图片内容，成功与否以工具返回为准，【不要】向用户描述图片画面细节）。单个镜头也传一项；单批最多 {max_create_batch} 项。scene_reference_mode（Phase D2）：full=场景图作为参考（空间确立）；context_only=只保留场景语义（近景/特写防构图拉宽）；auto=按景别自动。省略=full。
- shot.generate_videos(shot_ids[])：批量为镜头生成视频（串行生成，每个约 1~3 分钟，单批最多 4 个）。前置：该镜有效开场画面就绪——普通镜头用本镜已生成的首帧；inherit 接力镜头用来源镜头末帧（不要替接力镜头生成本镜首帧）。
- 生成顺序不要硬编码「首帧→尾帧→视频」全套：按用户需求和镜头实际形态决定——有的镜头只要首帧（静态分镜），keyframes 镜头才需要尾帧，视频在开场画面就绪后才能生成。工具会对不满足前置条件的项返回结构化原因（如「首帧未就绪」），按提示先补齐再重试，不要盲目重发。
- 生成前的现状确认用 shot.list(scene_id=…) 一次看清该段全部镜头（id/是否有首帧/接力关系）即可，不要逐个 shot.read 拖慢回合。
- 生成很慢且消耗额度：用户明确要求出图/出视频时才调用；同一镜头不要反复重发（每次调用都会生成新候选=重新消耗额度）；用户说「重新生成一版/换一批」时才是有意的重生成。生成完成后提示用户到分镜页查看与挑选。

【视觉审查（只读，真实看图）】
- visual.review_image(shot_id, focus?, frame?)：用视觉模型真实查看该镜头已生成的分镜图，对照本镜 canonical 要求返回结构化反馈（verdict: pass/needs_fix/uncertain + issues）。focus 可选 shot_adherence（默认）/visual_defects（默认）/character_consistency（自动附角色三视图参考）/continuity（自动附同段上一镜画面）；frame 可选 first（默认）/last。
- visual.review_shot(shot_id, frame?)：Shot-aware 分镜质量闸门（12 固定维度：人数/克隆/额外人物/缺角色/景别/占比/构图/机位语义/场景范围/identity/道具/伪影；overall 由代码按 hard gate 确定性推导，模型不能用整体印象翻案）。返回可执行导演反馈（如 expected CU observed MS）。分镜质量判断优先用本工具（比 review_image 更严更全）；根据反馈决定：修改 shot 意图（shot.update）→ 调整生成参数（scene_reference_mode）→ 重新生成。
- 🔴 你没有任何直接看图能力：对生成画面内容下判断只有一条正路——调用本工具拿真实视觉反馈。用户问「这张图有没有问题/符不符合要求/检查一下画面」时必须调用本工具；绝不允许凭 shot 描述、prompt、文件名或想象去描述/评价画面（那是编造，违反 truth gate）。
- 审查结果是视觉模型的判断（可能 uncertain 或出错），供决策参考，不是 canonical 事实；不要把 pass/needs_fix 当成已确认的项目真相向用户断言，也不要因反馈不佳就自动重新生成（生成永远等用户要求）。

【生产检查与绑定（正式分镜图生成前；Phase C1）】
- project.visual_profile.read()：读项目视觉定义（render_family/风格方向/负面约束）。正式生产推理前先读；它才是决定生成风格的真相，与用户要求冲突时指出冲突（你没有修改它的工具）。
- shot.production.inspect(shot_id)：【正式生成前必查】一镜的生产准备摘要：该镜 cast 的 approved Look 选项 / 场景生产上下文（SceneInstance 与 approved 场景视图）/ 当前 binding 与 readiness / cast 一致性 / 结构化 prerequisite。不要「生成失败后才猜问题」——正式生成前先 inspect。
- shot.production.bind(shot_id, character_bindings[], prop_bindings?, scene_binding?, expected_binding_revision?)：为镜头声明生产绑定（本镜消费哪些 approved 造型/道具/场景视图）。UNBOUND 直接创建；已有 binding 是 replace（必须传 inspect 返回的 binding_revision）。绑定后回执带 readiness。纯环境镜（shot.characters 为空）允许 character_bindings=[]（scene-only binding，34.md §5）。
- shot.production.refresh(shot_id, expected_binding_revision)：依赖都有效但快照过期时，显式刷新 binding 快照。依赖本身失效（Look/场景视图变更、审批撤销）时 refresh 会 fail closed 返回真实原因——refresh 不能魔法修复。
- 【概念纪律】Character 是身份（是谁），CharacterLook 是当前生产造型（穿什么/什么状态）——绑定时选 Look，不是改 Character。Story Scene ≠ Location ≠ SceneInstance ≠ SceneInstanceView：SceneInstanceView 才是 BOUND 镜头真正消费的场景参考。ShotProductionBinding 不是故事事实，是「本镜消费哪些 approved 生产真相」的正式生产意图。
- 【readiness 工作流】正式生成：inspect → 修 Shot 层问题（cast 不全就 shot.update 补全——这是你自己能修的）→ bind → inspect/回执 READY → generate。缺 approved Look / 缺场景视图时【绝不自己创建】（你没有那些工具），如实列出 missing 资源、受影响镜头、所需能力，作为 production prerequisite 报告——Producer 会协调。
- 【生成 reference policy】shot.generate_images 新增可选 reference_policy：用户要求 production-ready / 使用批准资源 / 保持人物场景一致 / 正式生成 → 必须传 `bound_required`（未绑定会被系统拒绝，绝不静默回落 legacy）；用户明确要快速草稿、或项目没有生产资源 → 才允许 `legacy_allowed`（省略参数=旧行为）。不要为了「任务成功率」偷偷降级到 legacy。
- 你只有通过【真正发起工具调用】（function/tool_calls）才能读/写数据。在回复里说「我已创建/已修改」但没有真正调用对应工具，等于什么都没做——这是严重错误。
- 工具调用成功后，你只能依据工具【真实返回】的结果汇报；没看到 ok=true 与真实分配的 shot_id/顺序，就不能声称成功。
- 不要编造 shot_id、scene_id、镜头数、指纹；这些值必须来自工具返回。
- 版本指纹（expected_shots_revision / expected_revision）必须取自【本轮】工具返回值——历史对话里出现过的指纹一律视为过期，哪怕你记得很清楚。写工具返回 stale 时，直接用它回执里标注的正确指纹值重试。
- 不确定当前状态时先用读取工具确认：定位故事段 → story.list；看某段已有镜头 → shot.list(scene_id=...)；看某镜现状 → shot.read(shot_id)。

【给一个故事段设计分镜的标准工作流】（镜头数量、景别、节奏由你决定）：
1. story.list() 把用户说的「第N段」定位成 scene_id；
2. story.read 目标段 + 前后相邻 2~4 段，理解剧情、情绪与衔接；
3. character.list 拿真实角色名单 → 只 character.read 名单里在该段实际出场的角色（名单里没有的名字不读、也不写进镜头 characters）；
4. shot.list(scene_id=...) 看该段是否已有镜头（有则考虑保留/修改，不要假设为空）；
5. 按你的导演判断设计整组镜头，一次 shot.create_many 提交；
6. 收尾用 shot.list 重新核对真实结果（数量/顺序/id），以工具返回为准向用户总结。

【读取策略：最小充分上下文，按需扩大】
- 单段分镜/单镜修改：读目标段及邻近段落 + 出场角色设定即可，不要 script.read 全文。
- 全片分镜检查、整体节奏分析、用户明确要求通读：才 script.read。
- 信息不足（伏笔/动机在更早剧情）时继续 story.read 扩大范围，而不是猜测。

【镜头字段怎么填】（写给画面生成与制作协作的人看）：
- description（必填）：这一镜的【画面内容】——谁在哪里做什么、表情动作、视觉重点、构图氛围。中文，具体、可视化，不要抽象概括。🔴 只写能被画出来的视觉/动作/表演信息：可以写「小铃抬头对阿默说话」这种动作，但【不要】把角色实际说出的台词整句写进 description——台词一律归 dialogue 字段。
- characters：出场角色名数组（来自 character.list 的真实名字，不认识的不要编）。
- dialogue：本镜角色【实际开口说出】的台词——这是台词的【唯一归属字段】（配音 TTS 的输入）。谁说话就把那句台词原样写这里（可多句、可带说话人），例如：小铃：「奶奶说她的记忆盒能修好吗？」。🔴 不要把台词只埋在 description 里；有角色说话就必须填 dialogue。只有纯动作/空镜/无明确台词（旁白不算角色说话）才留空。
- dialogue 纪律（原生音画生产，C1；21.md §19 只保留两条数据卫生规范）：① 多角色同镜说话时，每句都带明确说话人前缀（林晚星：「别进去。」顾岩：「我必须去。」），不要让系统猜谁在说；② dialogue 只写真正会从角色嘴里说出来的文本——动作/动作说明/语气说明/镜头说明/音效说明一律放 description/motion 等视觉字段，不要用（舞台指示）括号塞进 dialogue（行首（低声）这类语气词可被系统识别，但不要依赖它表达动作）。
- target_duration_s：目标时长秒数，按镜头内容定（短特写可以 2~3 秒，动作段 5~8 秒），不要机械统一。
- 景别 shot_size 用英文值：extreme wide shot / wide shot / full shot / medium shot / medium close-up / close-up / extreme close-up。
- camera_angle 机位角度（如 eye level / low angle / high angle / dutch angle）、camera_movement 运镜（如 static / slow push in / pan left / handheld follow）、composition 构图、lighting 光线、motion 画面里什么在动、motion_pace 动作节奏、action_sequence 多步动作的先后顺序（多步动作务必写这里，并用明确顺序标记，如「1. … 2. … 3.」或「先…再…最后…」——系统会按此顺序编译给生成模型，不写顺序模型就可能自由发挥）、opening_state 主体起始状态、important_details 重要细节/情绪氛围——这些字段直接决定画面生成，把你的导演意图写具体；没有明确意图就留空，不要塞占位词。
- 改了影响画面的字段后，旧的自动提示词会被系统清空（下次生成按新方案重拼）；有手动 override 的会保留并在回执里说明。

【聊天与探讨（用户没让你改东西时）】
- 用户在提问、征求看法、讨论方案时（如「你觉得这段适合长镜头还是快剪」「这组镜头节奏怎么样」「低机位会不会更好」）：直接用自然语言回答，给出你的专业判断、方案比较和推荐。这类轮次【不调用写工具】；讨论中提到的改法不要自动执行，除非用户明确说改。
- 回答需要事实依据时（某段已有镜头、某镜现状）才按需调用只读工具，然后继续讨论。
- 用户认可方案并说「就这么改」之后，才真正执行写工具。

【回复规范（只汇报本轮改动）】
- 执行完写工具后，回复里【只汇报本轮真实执行的操作】：创建了哪些镜头（真实 shot_id 与一句话内容）、改了哪镜的哪些字段、删/移了什么。一两句到一小段即可，简述你的导演意图让用户理解思路。
- 【不要】复述之前轮次已经做过的历史改动；【不要】凭记忆声称镜头数量/顺序——以最后那次 shot.list 的真实返回为准。
- 简短回复不用罗列全部字段，挑关键变化说。

【自主执行与询问】
1. 默认自主完成。能根据用户表达、剧情或当前镜头状态合理确定意图，就直接执行，不要停下来问。
2. 轻微歧义选最符合上下文和用户目标的解释继续。
3. 不确定当前镜头/故事状态时，用读取工具确认，而不是问用户本可由工具获得的信息。
4. 只有两个以上明显合理的理解且结果明显不同、又无法通过读取消除歧义时，才询问用户最关键的一点。

【写工具必须一个一个来】（重要）：
- 写工具必须【一个一个执行】：每发一个写工具调用，等它返回最新状态/指纹后再发下一个。同一次回复里发多个写工具时，系统只执行第 1 个，其余被拒绝。
- 批量工具本身【一次调用完成整批】：整组创建一次放进 shot.create_many；多镜独立修改一次放进 shot.update_many——不要拆成多个调用，也不要混发多个写工具。
- 只读工具可以一次发多个。

【复杂操作要分解成多步】：
- 多镜重排：多次 shot.move，每次移动一个、用最新指纹，每步看结果再继续。
- 【整体重设优先保留 shot_id】（重要）：用户说「重新设计这组镜头」「这组太平了，重做一下」「整体调整得更有张力」这类【不改变镜头结构】的重设请求时，默认用 shot.update_many 在原 shot_id 上改写拍摄方案——保留镜头身份、顺序和已生成的分镜图/视频资产。只有当你的导演判断结构确实需要变化（要增加镜头、删减镜头、镜头数量/叙事结构明显改变）才用 shot.create / shot.create_many / shot.delete。禁止用「全部删除再全部重建」来实现普通重设——已有图片/视频资产的镜头被删掉会让用户白白损失生成成果。
- 推倒重做（用户明确说推倒重来/重做整组结构）：才先 shot.delete 删掉旧镜（或逐个删），再一次 shot.create_many 创建新组。
- 有接力关系的两个镜头（source_shot_id）之间不能插入新镜头——系统会拒绝并说明；可在接力链末端之后插入。要删除接力链中间的镜头但保留下游：先用 shot.update 把下游的 source_shot_id 改锚到更早的镜头（或传 null 解除），再删除——直接删会级联删掉全部下游。
- 用户说的「第N镜」指某段内 index=N 的镜头（或全局唯一时该镜头）；先 shot.list 确认 shot_id 再操作，不要用 _c 编号猜顺序（删除后编号跳号）。

要求：
- description/对白必须是中文内容本身，不要 markdown 标题、解释或代码块。
- 版本指纹（8 位十六进制）必须原样回传 read/list/写工具返回值里的，不要编造。
- after_shot_id 与 before_shot_id 不能同时给。
- 一次只做用户要求的修改，不顺手改无关镜头；保留用户没有要求改变的有效部分。
"""

# 模板里不能用嵌套花括号引用其它占位符，这里手动替换批量上限的「上一个/本值」描述
DIRECTOR_SYSTEM = DIRECTOR_SYSTEM.replace("{max_create_batch_prev}", str(MAX_CREATE_BATCH)) \
                                 .replace("{max_update_batch_prev}", str(MAX_UPDATE_BATCH))


# ============ 工具 schema（标准 OpenAI function 格式）============
# 单镜拍摄字段（Director 核心导演字段；schema 之外的白名单字段由执行器兼容，
# 见 SHOT_UPDATABLE_FIELDS）。description 是画面核心；shot_size 用英文景别值。
_SHOT_FIELDS_PROPS = {
    "description": {"type": "string",
                    "description": "这一镜的画面内容：谁在哪里做什么、表情动作、视觉重点。中文、具体、可视化，不要抽象概括。只写画面/动作/表演信息，不要把角色实际说出的台词写进这里（台词归 dialogue）。"},
    "characters": {"type": "array", "items": {"type": "string"},
                   "description": "出场角色名数组（来自 character.list 的真实名字；名单里没有的名字不要写）。"},
    "dialogue": {"type": "string",
                 "description": "本镜角色实际说出的台词（配音 TTS 输入，台词的唯一归属字段，可带说话人）。多角色同镜每句都带说话人前缀；只写真正说出口的文本，动作/语气/镜头/音效说明不要塞进来；有角色说话必须填这里、不要只埋在 description；纯动作/空镜无台词才省略。"},
    "target_duration_s": {"type": "number",
                          "description": "目标时长（秒）。按镜头内容定，不要机械统一。"},
    "shot_size": {"type": "string",
                  "description": "景别（英文值）：extreme wide shot / wide shot / full shot / medium shot / medium close-up / close-up / extreme close-up。"},
    "camera_angle": {"type": "string",
                     "description": "机位角度：eye level / low angle / high angle / dutch angle 等。"},
    "camera_movement": {"type": "string",
                        "description": "运镜：static / slow push in / pan left / handheld follow 等。"},
    "composition": {"type": "string", "description": "构图要点。"},
    "lighting": {"type": "string", "description": "光线设计。"},
    "motion": {"type": "string", "description": "画面里什么在动（主体动作/环境运动）。"},
    "motion_pace": {"type": "string", "description": "动作节奏（如 缓慢 / 急促）。"},
    "opening_state": {"type": "string",
                      "description": "主体起始状态（姿态/朝向/表情）。"},
    "important_details": {"type": "string",
                          "description": "重要细节/情绪氛围/质感。"},
    "negative_prompt": {"type": "string",
                        "description": ("负面约束短语串（逗号分隔，如「空无一人, 禁动物, "
                                        "禁现代建筑」）——视频生成的正式负面约束 API 参数"
                                        "（native AV 请求独立携带）。预编剧冻结的负面约束"
                                        "必须在提交生成前落进本字段；只写进 note/trace "
                                        "生成端拿不到（39C 实证）。"),
                        },
    "link_mode": {"type": "string",
                  "enum": ["i2v", "keyframes", "multi"],
                  "description": ("视频生成模式（39D relay 链必用）：i2v=首帧硬边界；"
                                  "keyframes=首尾双硬边界；multi=单参考直生（连续镜接力"
                                  "用 multi + video_reference_asset_id=prev_tail——开场="
                                  "上一镜实际末帧）。i2v/keyframes 需要已选首帧。"),
                  },
    "video_reference_asset_id": {"type": "string",
                                 "description": ("multi 按镜参考选择：填「prev_tail」= 尾帧"
                                                 "接力（参考=上一镜实际视频末帧，39D relay "
                                                 "链标准用法）；填 look 的 approved 参考 "
                                                 "asset_id = 用该身份参考直生（链首镜/独立镜，"
                                                 "REFERENCE_ONLY）；省略 = 确定性规则（cast "
                                                 "look hero）。"),
                                 },
}

_SHOT_CREATE_ITEMS_SCHEMA = {
    "type": "object",
    "properties": dict(_SHOT_FIELDS_PROPS),
    "required": ["description"],
}

SHOT_CREATE_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.create",
        "description": ("给一个故事段创建 1 个镜头，插到 after_shot_id 之后（省略则追加段尾）。"
                        "只补单个镜头时用；给一个段从无到有设计整组分镜用 shot.create_many。"
                        "创建前先 story.list/story.read 理解剧情、shot.list 看该段已有镜头，"
                        "不要凭假设操作。id 与顺序由后端分配。"),
        "parameters": {
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "目标故事段 id（如 s08）。"},
                "after_shot_id": {"type": "string",
                                  "description": "插到该镜头之后（可选，须同段；省略则追加段尾）。"},
                "expected_shots_revision": {
                    "type": "string",
                    "description": "你最近一次 shot.list 看到的镜头结构版本指纹（revision，8 位十六进制）。用于乐观并发。",
                },
                **_SHOT_FIELDS_PROPS,
            },
            "required": ["scene_id", "description", "expected_shots_revision"],
        },
    },
}

SHOT_CREATE_MANY_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.create_many",
        "description": (
            "一次创建一组镜头（同一故事段要新建 ≥2 镜时优先用本工具，【给一个故事段设计整组"
            "分镜就用它一次提交】，不要连续多次调 shot.create）。shots 数组顺序 = 创建后的"
            "镜头顺序（追加到段尾或 after_shot_id 之后）。整批只做一次乐观并发校验、一次原子"
            "写入；任一镜头非法则整批不写入（不留半成功）。id 由后端分配，你不能指定。"
            "注意：整体重设已有镜头组时不要用「删了重建」，优先 shot.update_many 保留原"
            f" shot_id 与已有资产；本工具用于【新增】镜头。单批建议不超过 {MAX_CREATE_BATCH} 镜"
            "——镜头很多时分多批提交，每批之间用工具返回的最新指纹。"),
        "parameters": {
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "目标故事段 id（如 s08）。"},
                "shots": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": MAX_CREATE_BATCH,
                    "description": f"要创建的镜头数组（{2}~{MAX_CREATE_BATCH} 项；只创建 1 镜请用 shot.create），数组顺序即镜头顺序。",
                    "items": _SHOT_CREATE_ITEMS_SCHEMA,
                },
                "after_shot_id": {"type": "string",
                                  "description": "整批插到该镜头之后（可选，须同段；省略则追加段尾）。"},
                "expected_shots_revision": {
                    "type": "string",
                    "description": "你最近一次 shot.list 返回的镜头结构版本指纹（revision 字段，8 位十六进制）。用于乐观并发。",
                },
            },
            "required": ["scene_id", "shots", "expected_shots_revision"],
        },
    },
}

SHOT_UPDATE_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.update",
        "description": (
            "精准修改【一个】镜头的拍摄字段（只传要改的字段）。必须先 shot.read(shot_id) 拿到"
            "该镜头内容指纹 expected_revision 并原样回传。用户只要求改某一镜（如「第3镜改成"
            "近景」）时用本工具，不要重建整组镜头。不能改 shot_id/scene_id/顺序；产物字段不在"
            "本工具范围。接力来源用 source_shot_id 参数改锚/解除（须紧跟新来源之后，先 move）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_id": {"type": "string", "description": "要修改的镜头 id（如 s08_c03）。"},
                "expected_revision": {
                    "type": "string",
                    "description": "你最近一次 shot.read(shot_id) 看到的该镜头内容指纹（revision，8 位十六进制）。期间该镜头被改动会拒绝写入并返回最新内容。",
                },
                "source_shot_id": {
                    "type": "string",
                    "description": ("接力来源（可选）：改为同段内上一镜的 shot_id（本镜开场沿用"
                                    "其视频末帧）；传 null/空字符串解除接力。系统校验来源存在、"
                                    "同段、无环、且本镜紧跟来源之后——不相邻时先用 shot.move "
                                    "调整位置再改锚。删除接力来源镜头前先解除/改锚下游，否则会级联删除。"),
                },
                **_SHOT_FIELDS_PROPS,
            },
            "required": ["shot_id", "expected_revision"],
        },
    },
}

SHOT_UPDATE_MANY_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.update_many",
        "description": (
            "一次修改多个【互相独立、已存在】的镜头（用户对整组镜头不满意、要大面积调整时"
            "优先用本工具，不要逐个 shot.update 循环）。【整体重设（重新设计这组/重做一下/"
            "调得更有张力）必须优先用本工具在原 shot_id 上改写】——保留镜头身份、顺序与已"
            "生成的分镜图/视频资产；只有结构确实要变（增/减镜头、镜头数量明显改变）才用"
            " create/delete，禁止用「全删重建」实现普通重设。每个 update 项用稳定 shot_id "
            "指定目标+ 只传要改的字段；同一镜头的多字段合并到一项、同一 shot_id 一批只出现"
            "一次。整批一次乐观并发校验、一次原子写入，任一 shot_id 不存在/重复则整批不写入。"
            "可以跨故事段选镜头。expected_revision 用 shot.list 返回的 shots_content_revision"
            "（内容指纹，非结构指纹）。用户没有要求改变的镜头【不要】放进 updates。"),
        "parameters": {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": MAX_UPDATE_BATCH,
                    "description": f"要修改的镜头数组（{2}~{MAX_UPDATE_BATCH} 项；只改 1 镜请用 shot.update）。",
                    "items": {
                        "type": "object",
                        "properties": {"shot_id": {"type": "string"},
                                       **_SHOT_FIELDS_PROPS},
                        "required": ["shot_id"],
                    },
                },
                "expected_revision": {
                    "type": "string",
                    "description": "你最近一次 shot.list 返回的 shots_content_revision（内容版本指纹，8 位十六进制）。期间任一镜头内容被改动会拒绝写入并返回最新。",
                },
            },
            "required": ["updates", "expected_revision"],
        },
    },
}

SHOT_DELETE_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.delete",
        "description": (
            "删除一个【已存在】的镜头。仅用于用户明确要求删除、或整体重设后镜头结构确实"
            "需要减少的情况——普通「重新设计/重做」不要删镜头（用 shot.update_many 在原"
            "shot_id 上改写，保留已生成的分镜图/视频资产）。系统会级联删除以它为接力来源"
            "（source_shot_id）的下游镜头，回执里会列出实际删了哪些。删完后用 shot.list 核对"
            "真实结果。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_id": {"type": "string", "description": "要删除的镜头 id（如 s08_c02）。"},
            },
            "required": ["shot_id"],
        },
    },
}

SHOT_MOVE_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.move",
        "description": (
            "把一个镜头移动到同一故事段内某镜之前/之后（都省略则移到段尾）。不改 shot_id。"
            "镜头顺序是状态依赖操作：【多个移动必须一步一步来】，每次移动后用工具返回的最新"
            "指纹发下一步，不要试图一次提交整组重排（没有批量 move 工具）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_id": {"type": "string", "description": "要移动的镜头 id。"},
                "before_shot_id": {"type": "string",
                                   "description": "移到该镜头之前（可选，须同段，与 after 二选一）。"},
                "after_shot_id": {"type": "string",
                                  "description": "移到该镜头之后（可选，须同段，与 before 二选一）。省略且无 before 则移到段尾。"},
                "expected_shots_revision": {
                    "type": "string",
                    "description": "你最近一次 shot.list 看到的镜头结构版本指纹（8 位十六进制）。用于乐观并发。",
                },
            },
            "required": ["shot_id", "expected_shots_revision"],
        },
    },
}

SHOT_SELECT_FRAME_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.select_frame",
        "description": (
            "选定一个镜头的首帧/尾帧候选（Phase D2：generate→review→compare→choose "
            "闭环的最后一环）。生成会产生多个批次候选；用 visual.review_shot 对照审查后，"
            "把通过质量闸门的那一候选设为 selected（任何 hard gate 失败的候选不得被选为"
            "最终 selected frame）。选定是 canonical 写操作：会更新 selected 指针与旧版"
            "兼容字段。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_id": {"type": "string", "description": "镜头 id。"},
                "frame": {"type": "string", "enum": ["first", "last"],
                          "description": "选首帧（默认）还是尾帧。"},
                "candidate_id": {"type": "string",
                                 "description": "要选定的候选 id（如 ff_ab12cd34_2）。"},
            },
            "required": ["shot_id", "candidate_id"],
        },
    },
}

SHOT_LIST_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.list",        "description": (
            "镜头地图（轻量，不返回完整拍摄方案）。不带参数：列所有故事段（position/scene_id/"
            "摘要/角色/镜头数/各段镜头 id 列表）+ 镜头结构指纹 revision 与内容指纹 "
            "shots_content_revision。带 scene_id：列该段镜头的顺序/一句话描述/角色/时长/景别/"
            "是否有首帧/接力关系。分镜任务【第一步】用它看现状；改镜头前先定位 shot_id。"),
        "parameters": {
            "type": "object",
            "properties": {
                "scene_id": {"type": "string",
                             "description": "可选。只看这个故事段的镜头（如 s08）。"},
            },
        },
    },
}

SHOT_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.read",
        "description": (
            "读取一个镜头的完整拍摄方案（描述/角色/对白/时长/景别/机位/运镜/构图/光线/动作/"
            "接力关系/生成状态）+ 该镜头内容指纹 expected_revision（shot.update 要用）。"
            "修改某个镜头前先读它。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_id": {"type": "string", "description": "镜头 id（如 s08_c03）。"},
            },
            "required": ["shot_id"],
        },
    },
}

STORY_LIST_TOOL = {
    "type": "function",
    "function": {
        "name": "story.list",
        "description": ("故事段地图：列出所有故事段（position 第几段/scene_id/摘要/出场角色/"
                        "镜头数）。用户说「第N段」时用它解析成稳定 scene_id；做分镜任务的第一步。"
                        "只读，不返回剧情全文。"),
        "parameters": {"type": "object", "properties": {}},
    },
}

STORY_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "story.read",
        "description": ("读取一个故事段的完整剧情文本（分镜创作的剧本依据）。局部任务读目标段"
                        "+ 前后相邻约 2~4 段；剧情信息不足再扩大读取范围，不要默认通读全文。"),
        "parameters": {
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "故事段 id（如 s08）。"},
            },
            "required": ["scene_id"],
        },
    },
}

SCRIPT_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "script.read",
        "description": ("读取剧本正文全文。仅在全片分镜检查、整体节奏/风格分析、用户明确要求"
                        "通读时使用；单个故事段的分镜设计不需要读全文（用 story.list/story.read）。"),
        "parameters": {"type": "object", "properties": {}},
    },
}

CHARACTER_LIST_TOOL = {
    "type": "function",
    "function": {
        "name": "character.list",
        "description": "角色名单（名字/一句话描述/是否有三视图）。分镜任务里先看有哪些角色可用。",
        "parameters": {"type": "object", "properties": {}},
    },
}

CHARACTER_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "character.read",
        "description": ("读取一个角色的完整文本设定（外貌/身份/服装/气质）与其出场故事段。"
                        "给某段设计分镜前，把该段出场的角色读一遍，让画面符合角色形象。"
                        "三视图资产状态仅供参考；从文本设定理解角色。"),
        "parameters": {
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "角色名。"},
            },
            "required": ["character_name"],
        },
    },
}

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web.search",
        "description": ("搜索现实世界资料，返回约 3 条「标题+摘要+链接」。仅在两种情况使用："
                        "①用户明确要求搜索/参考某种真实资料；②分镜涉及现实知识明显影响可信度"
                        "（特殊职业真实动作、历史时期场景、摄影/导演术语、真实设备与空间、地点"
                        "参考、特殊运动方式）。普通分镜设计不搜索。每轮最多 3 次。"),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "简洁、聚焦一个具体问题的中文搜索词。"},
            },
            "required": ["query"],
        },
    },
}

DIRECTOR_PROFILE_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "project.visual_profile.read",
        "description": ("读取项目视觉定义（ProjectVisualProfile）：render_family / "
                        "art_direction / style_tags / 负面风格约束 + 指纹与 revision。"
                        "这是真正决定生成风格的项目视觉宪法——正式生产推理（绑定/生成前）"
                        "先读它；与用户要求冲突时指出冲突。只读工具，你没有修改它的工具。"),
        "parameters": {"type": "object", "properties": {}},
    },
}

SHOT_PRODUCTION_INSPECT_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.production.inspect",
        "description": ("【正式生成前必查】读取一个镜头的完整生产准备摘要：镜头信息与内容"
                        "指纹 / 项目视觉定义 / 每个 cast 角色的 approved Look 选项与身份参考"
                        "状态 / 道具线索 / 场景生产上下文（SceneInstance 与 approved 场景视图）"
                        "/ 当前 binding 状态与 readiness / cast 一致性检查（文本提到但 cast "
                        "未列的已建立角色）/ 结构化 prerequisite 清单。只读工具。用它判断"
                        "「这镜现在能不能正式生成、缺什么」，不要生成失败后才猜问题。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_id": {"type": "string", "description": "镜头 id。"},
            },
            "required": ["shot_id"],
        },
    },
}

SHOT_PRODUCTION_BIND_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.production.bind",
        "description": ("为镜头创建/替换生产绑定（ShotProductionBinding）：声明本镜消费哪些"
                        "approved 生产真相（角色→look_id、可选道具、可选场景"
                        "scene_instance_id+location_view_id）。UNBOUND 时直接创建；已有 "
                        "binding 时是 replace（必须传 expected_binding_revision，来自 "
                        "shot.production.inspect）。绑定后立即返回 readiness；NOT_READY 时"
                        "按 issues 处理。写工具（一个一个来）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_id": {"type": "string", "description": "镜头 id。"},
                "character_bindings": {
                    "type": "array",
                    "items": {"type": "object",
                              "properties": {
                                  "character_name": {"type": "string"},
                                  "look_id": {"type": "string"},
                              },
                              "required": ["character_name", "look_id"]},
                    "description": "角色绑定：cast 中每个角色 → 其 approved look_id"
                                   "（来自 shot.production.inspect 的 character_options）。"},
                "prop_bindings": {
                    "type": "array",
                    "items": {"type": "object",
                              "properties": {
                                  "prop_id": {"type": "string"},
                                  "relation": {"type": "string"},
                                  "anchor": {"type": "string"},
                                  "visual_state_note": {"type": "string"},
                              },
                              "required": ["prop_id"]},
                    "description": "可选道具绑定。"},
                "scene_binding": {
                    "type": "object",
                    "properties": {
                        "scene_instance_id": {"type": "string"},
                        "location_view_id": {"type": "string"},
                        "pinned_scene_instance_view_id": {"type": "string"},
                    },
                    "required": ["scene_instance_id", "location_view_id"],
                    "description": "可选场景绑定（用 inspect 的 scene 上下文里真实存在的 id）。"},
                "expected_binding_revision": {"type": "string",
                                              "description": "替换已有绑定时必传（inspect 返回的"
                                                             " binding_revision）。"},
                "production_notes": {"type": "string", "description": "生产备注。"},
            },
            "required": ["shot_id", "character_bindings"],
        },
    },
}

SHOT_PRODUCTION_REFRESH_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.production.refresh",
        "description": ("显式刷新 binding 的依赖指纹快照（把当前仍有效的依赖确认为新 "
                        "freshness basis）。依赖本身已失效（Look/场景视图变更、审批被撤销、"
                        "profile 不符）时 refresh 会 fail closed 返回真实原因——refresh 不能"
                        "魔法修复依赖，那种情况要修依赖或报告 prerequisite。写工具。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_id": {"type": "string", "description": "镜头 id。"},
                "expected_binding_revision": {"type": "string",
                                              "description": "当前 binding revision（inspect 返回）。"},
            },
            "required": ["shot_id", "expected_binding_revision"],
        },
    },
}

DIRECTOR_TOOLS = [
    SHOT_LIST_TOOL, SHOT_READ_TOOL, SHOT_CREATE_TOOL, SHOT_CREATE_MANY_TOOL,
    SHOT_UPDATE_TOOL, SHOT_UPDATE_MANY_TOOL, SHOT_DELETE_TOOL, SHOT_MOVE_TOOL,
    SHOT_GENERATE_IMAGES_TOOL, SHOT_GENERATE_VIDEOS_TOOL,
    STORY_LIST_TOOL, STORY_READ_TOOL, SCRIPT_READ_TOOL,
    CHARACTER_LIST_TOOL, CHARACTER_READ_TOOL,
    DIRECTOR_PROFILE_READ_TOOL, SHOT_PRODUCTION_INSPECT_TOOL,
    SHOT_PRODUCTION_BIND_TOOL, SHOT_PRODUCTION_REFRESH_TOOL,
    WEB_SEARCH_TOOL,
    VISION_REVIEW_TOOL,
    REVIEW_SHOT_TOOL,
    # Phase 39：连续性校准 / 帧级只读 / keyframes 桥；42.md：run 创建进工具面
    CALIBRATION_SEAM_TOOL, CALIBRATION_RUN_CREATE_TOOL,
    CALIBRATION_TRACE_READ_TOOL, CALIBRATION_TRACE_UPSERT_TOOL,
    VIDEO_EXTRACT_FRAME_TOOL, VISUAL_SAMPLE_FRAMES_TOOL,
    SHOT_BRIDGE_CREATE_TOOL,
    # Skill System V1（40.md）：生产技能发现/加载（只读）
    *SKILL_TOOLS,
]


# ============ 工具执行 ============
def _load_proj(rt: AgentRuntime, project: str):
    pdir = rt._resolve_project_dir(project)
    return pdir, ProjectStore(pdir), ProjectStore(pdir).load()


def _reload(rt: AgentRuntime, project: str):
    """从 canonical（磁盘）重新读取项目——写后校验只用这份，绝不用内存对象。"""
    pdir = rt._resolve_project_dir(project)
    return ProjectStore(pdir).load()


def _find_scene(proj: Project, scene_id: str):
    return next((s for s in proj.scenes if s.scene_id == scene_id), None)


def _find_shot(proj: Project, shot_id: str):
    return proj.get_shot(shot_id)


def _shot_scene_of(proj: Project, shot_id: str):
    """镜头所在 (Scene, Shot)；找不到镜头返回 (None, None)。"""
    for sc in proj.scenes:
        for sh in sc.shots:
            if sh.shot_id == shot_id:
                return sc, sh
    return None, None


def _clean_shot_fields(fields: dict, valid_chars: set) -> tuple[dict, list]:
    """把 LLM 给的镜头字段清洗成结构服务可写的形式。返回 (cleaned, dropped_characters)：
    - 白名单外/None 字段剔除（schema 已约束，仍防御）；
    - characters 按项目已有角色过滤，被剔除的名字单独返回（让 Agent 知情而非静默丢失）；
    - 字符串字段 strip；其余原样透传（类型/兜底由 update_shot_fields 负责）。"""
    out: dict = {}
    dropped: list = []
    for k, v in (fields or {}).items():
        if k == "source_shot_id":
            # 接力来源：显式 None/空 = 解除（detach），字符串 = 改锚——不能走通用
            # 「None 剔除」规则（否则 detach 无法表达）。写入由执行器调
            # update_shot_relay 完成（结构校验），不进 insert/update_shot_fields。
            out[k] = str(v).strip() if v else None
            continue
        if v is None or k not in SHOT_UPDATABLE_FIELDS:
            continue
        if k == "link_mode":
            # Phase D §25.C：非法值丢弃（结构服务层也二次校验）；34.md §13 增 multi
            vv = str(v).strip()
            if vv in ("i2v", "keyframes", "multi"):
                out[k] = vv
            continue
        if k == "characters":
            names = [str(x).strip() for x in (v if isinstance(v, list) else [v])
                     if str(x).strip()]
            kept = [n for n in names if not valid_chars or n in valid_chars]
            dropped.extend(n for n in names if n not in kept)
            out[k] = kept
        elif isinstance(v, str):
            out[k] = v.strip()
        else:
            out[k] = v
    return out, dropped


def _split_spec(cleaned: dict) -> tuple[dict, dict]:
    """把清洗后的字段拆成 insert_shots_after 的 spec（description/characters/
    target_duration_s）与其余拍摄字段（插入后用 update_shot_fields 补齐）。"""
    base = {}
    for k in ("description", "characters", "target_duration_s"):
        if k in cleaned:
            base[k] = cleaned.pop(k)
    return base, cleaned


def _shot_light_view(shot: Shot) -> dict:
    """shot.list(scene_id) 用的轻量镜头视图。"""
    return {"index": shot.index, "shot_id": shot.shot_id,
            "description": (shot.description or "")[:80],
            "characters": list(shot.characters or []),
            "target_duration_s": shot.target_duration_s,
            "shot_size": shot.shot_size or "",
            "has_first_frame": bool(shot.first_frame and shot.first_frame.ready),
            "has_video": bool(shot.video and shot.video.local),
            "opening_source": shot.opening_source or "",
            "source_shot_id": shot.source_shot_id}


def _execute_shot_list(rt: AgentRuntime, project: str, arguments: str,
                       instruction, message_id) -> dict:
    """镜头地图（轻量）。无 scene_id：所有段（position/scene_id/摘要/角色/镜头数/
    镜头 id 列表）；有 scene_id：该段镜头轻量列表。都返回结构指纹 revision + 内容指纹
    shots_content_revision（写工具乐观并发令牌）。只读。"""
    args = _parse_args(arguments) if arguments else {}
    scene_id = (args.get("scene_id") or "").strip()
    _, _, proj = _load_proj(rt, project)

    if scene_id:
        scene = _find_scene(proj, scene_id)
        if scene is None:
            raise AgentError("not_found", f"故事段不存在: {scene_id}")
        return {"ok": True, "scene_id": scene_id,
                "scene_summary": (scene.summary or "")[:120],
                "shot_count": len(scene.shots or []),
                "shots": [_shot_light_view(sh) for sh in scene.shots],
                "revision": _shots_token(proj.scenes),
                "shots_content_revision": _shots_content_token(proj.scenes)}
    scenes = [{"position": idx, "scene_id": sc.scene_id,
               "summary": (sc.summary or "")[:120],
               "characters": list(sc.characters or []),
               "shot_count": len(sc.shots or []),
               "shot_ids": [sh.shot_id for sh in sc.shots]}
              for idx, sc in enumerate(proj.scenes, start=1)]
    return {"ok": True, "count": len(scenes), "scenes": scenes,
            "revision": _shots_token(proj.scenes),
            "shots_content_revision": _shots_content_token(proj.scenes)}


def _execute_shot_read(rt: AgentRuntime, project: str, arguments: str,
                       instruction, message_id) -> dict:
    """读取一个镜头完整创作参数（structure_service.read_shot 白名单）+ 生成资产状态
    + 该镜头内容指纹（供 shot.update 乐观并发）。只读。"""
    args = _parse_args(arguments)
    shot_id = (args.get("shot_id") or "").strip()
    if not shot_id:
        raise AgentError("bad_arguments", "缺少 shot_id")
    _, _, proj = _load_proj(rt, project)
    shot = _find_shot(proj, shot_id)
    if shot is None:
        raise AgentError("not_found", f"镜头不存在: {shot_id}")
    data = read_shot(proj, shot_id)
    data["has_first_frame"] = bool(shot.first_frame and shot.first_frame.ready)
    data["has_video"] = bool(shot.video and shot.video.local)
    return {"ok": True, "shot_id": shot_id, "revision": _shot_token(shot),
            "shot": data}


def _create_shots_impl(rt: AgentRuntime, project: str, arguments: str,
                       instruction: str, message_id: Optional[str],
                       *, expect_many: bool) -> dict:
    """shot.create / shot.create_many 共同实现：
    OCC（结构指纹）→ 整批预检（任一非法整批不写）→ 插入分配 id → 补拍摄字段 →
    一次原子写 → 重读 canonical 验证 → 统一回执。"""
    args = _parse_args(arguments)
    scene_id = (args.get("scene_id") or "").strip()
    expected_rev = args.get("expected_shots_revision")
    after_shot_id = (args.get("after_shot_id") or "").strip() or None
    if not scene_id:
        raise AgentError("bad_arguments", "缺少 scene_id")
    if not isinstance(expected_rev, str) or not expected_rev.strip():
        raise AgentError("bad_arguments",
                         "缺少 expected_shots_revision（应先 shot.list 拿到镜头结构指纹再传回）")

    if expect_many:
        raw_specs = _loose_json(args.get("shots"))
        if not isinstance(raw_specs, list) or len(raw_specs) < 2:
            raise AgentError("bad_arguments",
                             "shots 必须是至少 2 项的数组（只创建 1 镜请用 shot.create）")
        if len(raw_specs) > MAX_CREATE_BATCH:
            raise AgentError("bad_arguments",
                             f"单批最多 {MAX_CREATE_BATCH} 个镜头（收到 {len(raw_specs)}）。"
                             "镜头很多时分多批提交，每批之间用工具返回的最新指纹。")
    else:
        raw_specs = [args]

    op = "shot.create_many" if expect_many else "shot.create"
    pdir, pstore, proj = _load_proj(rt, project)
    scene = _find_scene(proj, scene_id)
    if scene is None:
        raise AgentError("not_found", f"故事段不存在: {scene_id}")

    # OCC：结构指纹（镜头增删/移动会变；内容修改不影响）
    before_rev = _shots_token(proj.scenes)
    if expected_rev != before_rev:
        return _base_receipt(
            op, ok=False, target=f"scene:{scene_id}",
            revision=before_rev, verified=False, error="stale_revision", stale=True,
            scene_id=scene_id,
            expected_revision=expected_rev, current_revision=before_rev,
            current_shots=[[sh.shot_id for sh in sc.shots] for sc in proj.scenes],
            note=f"stale：你传的 expected_shots_revision={expected_rev} 不是当前值"
                 f"（可能是历史对话里出现过的旧指纹）。当前正确的指纹是 "
                 f"expected_shots_revision=\"{before_rev}\"——直接把它原样填进去重新调用"
                 f"本工具即可；不要用历史消息里出现过的任何指纹，也不要用 "
                 f"shots_content_revision 或 story.list 返回的 revision。")

    # 整批预检：description 必填、characters 过滤，任一非法整批不写
    valid_chars = {c.name for c in proj.characters}
    cleaned_specs: list[dict] = []
    all_dropped: list[str] = []
    for i, raw in enumerate(raw_specs):
        if not isinstance(raw, dict):
            raise AgentError("bad_arguments", f"shots[{i}] 必须是对象")
        if str(raw.get("source_shot_id") or "").strip():
            raise AgentError(
                "bad_arguments",
                f"shots[{i}] 创建时不能指定 source_shot_id（新镜头的 id 此时尚未分配，"
                "批量内互相引用无法表达）。请先创建，再用 shot.update 设置接力来源"
                "（改锚前先 shot.move 到来源之后）。")
        desc = str(raw.get("description") or "").strip()
        if not desc:
            raise AgentError("bad_arguments",
                             f"shots[{i}].description 不能为空（每个镜头都要有画面描述）")
        cleaned, dropped = _clean_shot_fields(raw, valid_chars)
        cleaned["description"] = desc
        all_dropped.extend(dropped)
        cleaned_specs.append(cleaned)
    if after_shot_id and not any(sh.shot_id == after_shot_id for sh in scene.shots):
        raise AgentError("not_found",
                         f"after_shot_id {after_shot_id} 不在故事段 {scene_id} 内")
    before_order = [[sh.shot_id for sh in sc.shots] for sc in proj.scenes]

    # 插入（分配 id / index 重排）→ 每镜补齐拍摄字段 → 一次原子写
    insert_specs, extras = [], []
    for cleaned in cleaned_specs:
        base, extra = _split_spec(dict(cleaned))
        insert_specs.append(base)
        extras.append(extra)
    try:
        new_shots = insert_shots_after(proj, scene_id, after_shot_id, insert_specs)
    except StructureError as e:
        raise AgentError(e.code, e.message)
    for sh, extra in zip(new_shots, extras):
        if extra:
            try:
                update_shot_fields(proj, sh.shot_id, extra)
            except StructureError:
                pass   # 刚插入的镜头必然存在，防御性忽略
    pstore.save(proj)

    # 写后重读 canonical 校验：每个新镜头确实落盘且描述一致，其余段结构未受影响
    on_disk = _reload(rt, project)
    disk_scene = _find_scene(on_disk, scene_id)
    after_order = [[sh.shot_id for sh in sc.shots] for sc in on_disk.scenes]
    created: list[dict] = []
    verified = disk_scene is not None and after_order != before_order
    if verified:
        for spec, sh in zip(cleaned_specs, new_shots):
            d = _find_shot(on_disk, sh.shot_id)
            if d is None or (d.description or "") != spec["description"]:
                verified = False
                break
            created.append({"shot_id": d.shot_id, "index": d.index,
                            "description": d.description,
                            "characters": list(d.characters or []),
                            "target_duration_s": d.target_duration_s,
                            "shot_size": d.shot_size or ""})
    if not verified:
        return _base_receipt(
            op, ok=False, target=f"scene:{scene_id}", scene_id=scene_id,
            revision=_shots_token(on_disk.scenes), verified=False,
            error="verify_failed",
            note="写入后重新读取发现结果与预期不一致，未确认创建。请用 shot.list 读取"
                 "最新镜头列表后重试。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="director", field=f"create_shots:{scene_id}", before=before_order,
        after=after_order, instruction=instruction, message_id=message_id)
    ids = [c["shot_id"] for c in created]
    rt.record_work(project, "change",
                   f"Director 在故事段 {scene_id} 创建了 {len(created)} 个镜头"
                   f"（{', '.join(ids)}）",
                   participants=["director"], affected_domains=["storyboard"],
                   references={"scene_id": scene_id}, status="completed")
    note = (f"已在故事段 {scene_id} 创建 {len(created)} 个镜头（{', '.join(ids)}），"
            "顺序即提交顺序。请用 shot.list 核对后向用户总结。")
    if all_dropped:
        note += (f" 注意：characters 里这些名字不在项目角色中，已被忽略："
                 f"{', '.join(sorted(set(all_dropped)))}。")
    return _base_receipt(
        op, ok=True, target=f"scene:{scene_id}",
        before=before_order, after=after_order,
        revision=_shots_token(on_disk.scenes), verified=True, unchanged=False,
        scene_id=scene_id, created=created, count=len(created),
        shots_revision=_shots_token(on_disk.scenes),
        shots_content_revision=_shots_content_token(on_disk.scenes),
        note=note)


def _execute_shot_create(rt: AgentRuntime, project: str, arguments: str,
                         instruction, message_id) -> dict:
    """shot.create：给故事段补 1 个镜头（含完整拍摄字段）。"""
    return _create_shots_impl(rt, project, arguments, instruction, message_id,
                              expect_many=False)


def _execute_shot_create_many(rt: AgentRuntime, project: str, arguments: str,
                              instruction, message_id) -> dict:
    """shot.create_many：一次创建一组镜头（≥2）。整批一次 OCC + 一次原子写入。"""
    return _create_shots_impl(rt, project, arguments, instruction, message_id,
                              expect_many=True)


def _execute_shot_update(rt: AgentRuntime, project: str, arguments: str,
                         instruction, message_id) -> dict:
    """shot.update：精准修改一个镜头的拍摄字段。OCC（镜头级内容指纹）→ 无实际变更
    no-op → 内存改 → 原子写 → 重读 canonical 验证 → 回执。"""
    args = _parse_args(arguments)
    shot_id = (args.get("shot_id") or "").strip()
    expected_rev = args.get("expected_revision")
    if not shot_id:
        raise AgentError("bad_arguments", "缺少 shot_id")
    if not isinstance(expected_rev, str) or not expected_rev.strip():
        raise AgentError("bad_arguments",
                         "缺少 expected_revision（应先 shot.read(shot_id) 拿到该镜头内容指纹再传回）")

    pdir, pstore, proj = _load_proj(rt, project)
    scene, shot = _shot_scene_of(proj, shot_id)
    if shot is None:
        raise AgentError("not_found", f"镜头不存在: {shot_id}")

    before_token = _shot_token(shot)
    if expected_rev != before_token:
        return _base_receipt(
            "shot.update", ok=False, target=shot_id,
            revision=before_token, verified=False, error="stale_revision", stale=True,
            shot_id=shot_id, scene_id=scene.scene_id,
            expected_revision=expected_rev, current_revision=before_token,
            current_shot=_shot_view(shot),
            note=f"stale：你传的 expected_revision={expected_rev} 不是当前值"
                 f"（可能是历史对话里出现过的旧指纹）。当前正确的指纹是 "
                 f"expected_revision=\"{before_token}\"——直接把它原样填进去重新调用"
                 f"本工具即可；不要用历史消息里出现过的任何指纹。")

    valid_chars = {c.name for c in proj.characters}
    cleaned, dropped = _clean_shot_fields(args, valid_chars)
    cleaned.pop("shot_id", None)
    if not cleaned:
        return _base_receipt(
            "shot.update", ok=True, target=shot_id, shot_id=shot_id,
            scene_id=scene.scene_id, revision=before_token, verified=True,
            unchanged=True, changed=[],
            note="没有提供任何要修改的字段（或提供的值均为空），镜头未改动。")
    # 无实际变更（给的值与现值完全一致）→ no-op
    real_diff = {k: v for k, v in cleaned.items() if _shot_view(shot).get(k) != v}
    if not real_diff:
        return _base_receipt(
            "shot.update", ok=True, target=shot_id, shot_id=shot_id,
            scene_id=scene.scene_id, revision=before_token, verified=True,
            unchanged=True, changed=[],
            note="提供的字段值与镜头当前内容完全一致，未产生变化。")

    before_view = _shot_view(shot)
    try:
        res = update_shot_fields(proj, shot_id, real_diff)
    except StructureError as e:
        raise AgentError(e.code, e.message)
    # 接力来源（结构关系字段）：不走 update_shot_fields（内容白名单不含它），
    # 显式调 update_shot_relay 做结构校验（存在/同段/无环/相邻）。在 save 之前
    # 执行——校验失败抛 AgentError 时内容改动尚未落盘，整单拒绝（原子性）。
    relay_changed = False
    if "source_shot_id" in real_diff:
        try:
            rres = update_shot_relay(proj, shot_id, real_diff["source_shot_id"])
            relay_changed = bool(rres.get("changed"))
        except StructureError as e:
            raise AgentError(e.code, e.message)
        if relay_changed:
            res["changed"].append("source_shot_id")
            res["prompts_invalidated"].update(
                _invalidate_stale_prompts(shot, ["source_shot_id"]))
    after_values = {k: _shot_view(shot).get(k) for k in real_diff}
    pstore.save(proj)

    # 写后重读 canonical 验证：变更字段确实落盘
    on_disk = _reload(rt, project)
    disk_shot = _find_shot(on_disk, shot_id)
    verified = disk_shot is not None and all(
        _shot_view(disk_shot).get(k) == v for k, v in after_values.items())
    if not verified:
        return _base_receipt(
            "shot.update", ok=False, target=shot_id, shot_id=shot_id,
            scene_id=scene.scene_id,
            revision=(_shot_token(disk_shot) if disk_shot else ""), verified=False,
            error="verify_failed",
            note="修改后重新读取发现内容与预期不一致，未确认修改。请用 shot.read 读取"
                 "该镜头最新内容后重试。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="director", field=f"update_shot:{shot_id}",
        before={k: before_view.get(k) for k in real_diff},
        after=after_values, instruction=instruction, message_id=message_id)
    changed_zh = "、".join(res["changed"])
    rt.record_work(project, "change",
                   f"Director 修改了镜头 {shot_id}（{changed_zh}）",
                   participants=["director"], affected_domains=["storyboard"],
                   references={"shot_id": shot_id}, status="completed")
    note = f"镜头 {shot_id} 已更新（{changed_zh}）。"
    if dropped:
        note += (f" characters 里这些名字不在项目角色中，已被忽略："
                 f"{', '.join(sorted(set(dropped)))}。")
    inval = res.get("prompts_invalidated") or {}
    if any(v == "cleared" for v in inval.values()):
        note += " 旧自动提示词已清空，下次生成按新方案重拼。"
    if any(v == "manual_override" for v in inval.values()):
        note += " 该镜有手动提示词 override，已保留。"
    return _base_receipt(
        "shot.update", ok=True, target=shot_id,
        before={k: before_view.get(k) for k in real_diff}, after=after_values,
        revision=_shot_token(disk_shot), verified=True, unchanged=False,
        shot_id=shot_id, scene_id=scene.scene_id, changed=list(real_diff.keys()),
        prompts_invalidated=inval,
        shots_content_revision=_shots_content_token(on_disk.scenes),
        note=note)


def _execute_shot_update_many(rt: AgentRuntime, project: str, arguments: str,
                              instruction, message_id) -> dict:
    """shot.update_many：一次修改多个互相独立的镜头（可跨段）。整批一次 OCC
    （全项目镜头内容指纹）+ 预检 + 一次原子写入 + 重读验证。"""
    args = _parse_args(arguments)
    expected_rev = args.get("expected_revision")
    raw_updates = _loose_json(args.get("updates"))
    if not isinstance(raw_updates, list) or len(raw_updates) < 2:
        raise AgentError("bad_arguments",
                         "updates 必须是至少 2 项的数组（只改 1 镜请用 shot.update）")
    if len(raw_updates) > MAX_UPDATE_BATCH:
        raise AgentError("bad_arguments",
                         f"单批最多 {MAX_UPDATE_BATCH} 个镜头更新（收到 {len(raw_updates)}）。"
                         "分多批提交，每批之间用工具返回的最新指纹。")

    pdir, pstore, proj = _load_proj(rt, project)

    before_token = _shots_content_token(proj.scenes)
    if (not isinstance(expected_rev, str)
            or expected_rev.strip() != before_token):
        return _base_receipt(
            "shot.update_many", ok=False, target=None,
            revision=before_token, verified=False, error="stale_revision", stale=True,
            expected_revision=expected_rev, current_revision=before_token,
            note=f"stale：你传的 expected_revision={expected_rev} 不是当前值"
                 f"（可能是历史对话里出现过的旧指纹）。当前正确的指纹是 "
                 f"expected_revision=\"{before_token}\"（shots_content_revision）——直接把它"
                 f"原样填进去重新调用本工具即可；不要用历史消息里出现过的任何指纹，也不要用"
                 f" shot.list 返回的 revision（那是结构指纹）。")

    # 整批预检：shot_id 存在、不重复、有真实字段变更；任一非法整批不写
    valid_chars = {c.name for c in proj.characters}
    prepared: list[tuple[Any, str, dict]] = []   # (shot, shot_id, real_diff)
    all_dropped: list[str] = []
    seen_ids: set = set()
    for i, item in enumerate(raw_updates):
        if not isinstance(item, dict) or not str(item.get("shot_id") or "").strip():
            raise AgentError("bad_arguments", f"updates[{i}] 缺少 shot_id")
        sid = str(item["shot_id"]).strip()
        if sid in seen_ids:
            raise AgentError("bad_arguments",
                             f"updates[{i}] 的 shot_id {sid} 在本批重复：同一镜头的多个"
                             "字段要合并到一项。")
        seen_ids.add(sid)
        shot = _find_shot(proj, sid)
        if shot is None:
            raise AgentError("not_found", f"镜头 {sid} 不存在（整批未写入）")
        cleaned, dropped = _clean_shot_fields(
            {k: v for k, v in item.items() if k != "shot_id"}, valid_chars)
        all_dropped.extend(dropped)
        if not cleaned:
            raise AgentError("bad_arguments",
                             f"updates[{i}]（{sid}）没有提供任何要修改的字段（整批未写入）")
        real_diff = {k: v for k, v in cleaned.items() if _shot_view(shot).get(k) != v}
        prepared.append((shot, sid, real_diff))
    prepared = [(sh, sid, d) for (sh, sid, d) in prepared if d]

    if not prepared:
        return _base_receipt(
            "shot.update_many", ok=True, target=None, revision=before_token,
            verified=True, unchanged=True, results=[],
            note="提供的字段值与各镜头当前内容完全一致，未产生变化。")

    # 内存逐镜应用（沿用 update_shot_fields 的类型兜底/prompt 失效），一次原子写。
    # 接力来源同样在 save 前逐镜应用（update_shot_relay 结构校验）——任一项失败
    # 整批拒绝，未落盘（原子性不变）。
    results = []
    for shot, sid, real_diff in prepared:
        try:
            res = update_shot_fields(proj, sid, real_diff)
        except StructureError as e:
            raise AgentError(e.code, e.message)
        if "source_shot_id" in real_diff:
            try:
                rres = update_shot_relay(proj, sid, real_diff["source_shot_id"])
            except StructureError as e:
                raise AgentError(e.code, e.message)
            if rres.get("changed"):
                res["changed"].append("source_shot_id")
                res["prompts_invalidated"].update(
                    _invalidate_stale_prompts(shot, ["source_shot_id"]))
        results.append({"shot_id": sid, "changed": res["changed"],
                        "prompts_invalidated": res.get("prompts_invalidated", {}),
                        "scene_id": shot.scene_id})
    pstore.save(proj)

    # 写后重读 canonical 验证
    on_disk = _reload(rt, project)
    verified = True
    for shot, sid, real_diff in prepared:
        d = _find_shot(on_disk, sid)
        if d is None or any(_shot_view(d).get(k) != v for k, v in real_diff.items()):
            verified = False
            break
    if not verified:
        return _base_receipt(
            "shot.update_many", ok=False, target=None,
            revision=_shots_content_token(on_disk.scenes), verified=False,
            error="verify_failed",
            note="写入后重新读取发现结果与预期不一致，未确认修改。请用 shot.list 读取"
                 "最新镜头内容后重试。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="director", field="update_shots_many", before=None,
        after=[r["shot_id"] for r in results],
        instruction=instruction, message_id=message_id)
    rt.record_work(project, "change",
                   f"Director 批量修改了 {len(results)} 个镜头",
                   participants=["director"], affected_domains=["storyboard"],
                   references={}, status="completed")
    note = (f"已批量修改 {len(results)} 个镜头"
            f"（{', '.join(r['shot_id'] for r in results)}）。请用 shot.list 核对后向用户总结。")
    if all_dropped:
        note += (f" characters 里这些名字不在项目角色中，已被忽略："
                 f"{', '.join(sorted(set(all_dropped)))}。")
    return _base_receipt(
        "shot.update_many", ok=True, target=None,
        revision=_shots_content_token(on_disk.scenes), verified=True, unchanged=False,
        results=results, count=len(results),
        shots_content_revision=_shots_content_token(on_disk.scenes),
        shots_revision=_shots_token(on_disk.scenes),
        note=note)


def _execute_shot_delete(rt: AgentRuntime, project: str, arguments: str,
                         instruction, message_id) -> dict:
    """shot.delete：级联删除一个镜头（含接力下游）。活跃生产护栏 → delete_service
    确定性级联（依赖由代码算，不由 LLM）→ 重读验证 → 回执。"""
    from src.storyboard.delete_service import execute_delete, DeleteError
    args = _parse_args(arguments)
    shot_id = (args.get("shot_id") or "").strip()
    if not shot_id:
        raise AgentError("bad_arguments", "缺少 shot_id")
    pdir, _, proj = _load_proj(rt, project)
    scene, shot = _shot_scene_of(proj, shot_id)
    if shot is None:
        raise AgentError("not_found", f"镜头不存在: {shot_id}")

    reason = _active_production_reason(project)
    if reason:
        raise AgentError("production_active",
                         f"当前项目有正在进行的生成任务（{reason}），等生成结束后再删除")

    before_order = [[sh.shot_id for sh in sc.shots] for sc in proj.scenes]
    try:
        res = execute_delete(pdir, proj, "shot", shot_id)
    except DeleteError as e:
        raise AgentError(e.code, e.message)
    deleted = res["deleted"]

    DirectEditAuditStore(pdir, project).record(
        agent_id="director", field=f"delete_shot:{shot_id}",
        before={"shots": deleted.get("shots", []),
                "removed_files": res.get("removed_files", 0)},
        after=[], instruction=instruction, message_id=message_id)
    on_disk = _reload(rt, project)
    gone = _find_shot(on_disk, shot_id) is None
    if not gone:
        return _base_receipt(
            "shot.delete", ok=False, target=shot_id, shot_id=shot_id,
            verified=False, error="verify_failed",
            note="删除后重新读取发现镜头仍存在，未确认删除。请用 shot.list 核对后重试。")
    n_downstream = max(0, len(deleted.get("shots") or []) - 1)
    rt.record_work(project, "change",
                   f"Director 删除了镜头 {shot_id}"
                   + (f" 及 {n_downstream} 个下游接力镜头" if n_downstream else ""),
                   participants=["director"], affected_domains=["storyboard"],
                   references={"shot_id": shot_id}, status="completed")
    downstream = [s for s in (deleted.get("shots") or []) if s != shot_id]
    note = f"已删除镜头 {shot_id}"
    note += (f"，并级联删除了以它为接力来源的下游镜头：{', '.join(downstream)}。"
             if downstream else "。")
    if res.get("removed_files"):
        note += f"清理了 {res['removed_files']} 个生成资产文件。"
    note += "请用 shot.list 核对后向用户总结。"
    return _base_receipt(
        "shot.delete", ok=True, target=shot_id, shot_id=shot_id,
        scene_id=scene.scene_id if scene else None,
        before=before_order,
        after=[[sh.shot_id for sh in sc.shots] for sc in on_disk.scenes],
        revision=_shots_token(on_disk.scenes), verified=True, unchanged=False,
        deleted=deleted, removed_files=res.get("removed_files", 0),
        shots_revision=_shots_token(on_disk.scenes),
        shots_content_revision=_shots_content_token(on_disk.scenes),
        note=note)


def _execute_shot_move(rt: AgentRuntime, project: str, arguments: str,
                       instruction, message_id) -> dict:
    """shot.move：同段内单步移动（before/after 二选一，都省略移到段尾）。
    OCC（结构指纹）→ move_shot 中性层 → 原子写 → 重读验证新顺序 → 回执。"""
    args = _parse_args(arguments)
    shot_id = (args.get("shot_id") or "").strip()
    before_id = (args.get("before_shot_id") or "").strip() or None
    after_id = (args.get("after_shot_id") or "").strip() or None
    expected_rev = args.get("expected_shots_revision")
    if not shot_id:
        raise AgentError("bad_arguments", "缺少 shot_id")
    if not isinstance(expected_rev, str) or not expected_rev.strip():
        raise AgentError("bad_arguments",
                         "缺少 expected_shots_revision（应先 shot.list 拿到镜头结构指纹再传回）")

    pdir, pstore, proj = _load_proj(rt, project)
    scene, shot = _shot_scene_of(proj, shot_id)
    if shot is None:
        raise AgentError("not_found", f"镜头不存在: {shot_id}")

    before_rev = _shots_token(proj.scenes)
    if expected_rev != before_rev:
        return _base_receipt(
            "shot.move", ok=False, target=shot_id,
            revision=before_rev, verified=False, error="stale_revision", stale=True,
            shot_id=shot_id,
            expected_revision=expected_rev, current_revision=before_rev,
            current_shots=[[sh.shot_id for sh in sc.shots] for sc in proj.scenes],
            note=f"stale：你传的 expected_shots_revision={expected_rev} 不是当前值"
                 f"（可能是历史对话里出现过的旧指纹）。当前正确的指纹是 "
                 f"expected_shots_revision=\"{before_rev}\"——直接把它原样填进去重新调用"
                 f"本工具即可（多个移动要一步一步来）。")

    before_order = [[sh.shot_id for sh in sc.shots] for sc in proj.scenes]
    try:
        moved = move_shot(proj, shot_id, before=before_id, after=after_id)
    except StructureError as e:
        raise AgentError(e.code, e.message)
    after_order_mem = [[sh.shot_id for sh in sc.shots] for sc in proj.scenes]
    if after_order_mem == before_order:
        # 幂等移动（目标位置即当前位置）：不写盘、不审计，如实返回 unchanged。
        return _base_receipt(
            "shot.move", ok=True, target=shot_id, shot_id=shot_id,
            scene_id=scene.scene_id, index=moved.index, order=moved.index,
            before=before_order, after=after_order_mem,
            revision=before_rev, verified=True, unchanged=True,
            shots_revision=before_rev,
            shots_content_revision=_shots_content_token(proj.scenes),
            note=f"镜头 {shot_id} 已在目标位置（第 {moved.index} 位），未产生变化。")
    pstore.save(proj)

    on_disk = _reload(rt, project)
    after_order = [[sh.shot_id for sh in sc.shots] for sc in on_disk.scenes]
    disk_scene, disk_shot = _shot_scene_of(on_disk, shot_id)
    verified = (disk_shot is not None
                and after_order != before_order
                and disk_shot.index == moved.index)
    if not verified:
        return _base_receipt(
            "shot.move", ok=False, target=shot_id, shot_id=shot_id,
            revision=_shots_token(on_disk.scenes), verified=False,
            error="verify_failed",
            note="移动后重新读取发现顺序与预期不一致，未确认移动。请用 shot.list 读取"
                 "最新顺序后重试。")
    DirectEditAuditStore(pdir, project).record(
        agent_id="director", field=f"move_shot:{shot_id}", before=before_order,
        after=after_order, instruction=instruction, message_id=message_id)
    rt.record_work(project, "change",
                   f"Director 把镜头 {shot_id} 移到第 {moved.index} 位",
                   participants=["director"], affected_domains=["storyboard"],
                   references={"shot_id": shot_id}, status="completed")
    return _base_receipt(
        "shot.move", ok=True, target=shot_id, shot_id=shot_id,
        scene_id=disk_scene.scene_id, index=moved.index, order=moved.index,
        before=before_order, after=after_order,
        revision=_shots_token(on_disk.scenes), verified=True, unchanged=False,
        shots_revision=_shots_token(on_disk.scenes),
        shots_content_revision=_shots_content_token(on_disk.scenes),
        note=f"镜头 {shot_id} 已移到故事段 {disk_scene.scene_id} 的第 {moved.index} 位。"
             "请用 shot.list 核对后向用户总结。")


async def _execute_shot_select_frame(rt: AgentRuntime, project: str, arguments: str,
                                     instruction, message_id) -> dict:
    """shot.select_frame（Phase D2，30.md §21/§22）：选定首帧/尾帧候选。
    语义与 REST choose 端点一致（selected 指针 + 旧字段同步 + status=done）；
    写后重读 canonical 验证指针。候选不存在/无资产 → 结构化失败，不猜。"""
    args = _parse_args(arguments)
    shot_id = (args.get("shot_id") or "").strip()
    candidate_id = (args.get("candidate_id") or "").strip()
    frame = (args.get("frame") or "first").strip()
    if not shot_id or not candidate_id:
        raise AgentError("bad_arguments", "缺少 shot_id 或 candidate_id")
    if frame not in ("first", "last"):
        raise AgentError("bad_arguments", "frame 只允许 first/last")

    pdir, store, proj = _load_proj(rt, project)
    shot = proj.get_shot(shot_id)
    if shot is None:
        raise AgentError("not_found", f"镜头不存在: {shot_id}")
    batches = shot.last_frame_batches if frame == "last" else shot.first_frame_batches
    found = None
    for b in batches or []:
        for i, c in enumerate(b.candidates or []):
            if c.candidate_id == candidate_id:
                found = (b, i, c)
                break
        if found:
            break
    if found is None:
        raise AgentError("candidate_not_found",
                         f"镜头 {shot_id} 的{'尾帧' if frame == 'last' else '首帧'}批次中"
                         f"找不到候选 {candidate_id}")
    batch, idx, cand = found
    if not (cand.local or cand.url):
        raise AgentError("candidate_no_asset", f"候选 {candidate_id} 无本地文件也无 URL")

    def mut(s):
        if frame == "last":
            s.selected_last_frame = candidate_id
            s.last_frame.candidates = [
                Asset(local=c.local, url=c.url, status=c.status)
                for c in batch.candidates]
            s.last_frame.selected_index = idx
            s.last_frame.status = ShotStatus.DONE.value
        else:
            s.selected_first_frame = candidate_id
            s.first_frame.candidates = [
                Asset(local=c.local, url=c.url, status=c.status)
                for c in batch.candidates]
            s.first_frame.selected_index = idx
            s.first_frame.status = ShotStatus.DONE.value

    store.update_shot(shot_id, mut)
    disk = _reload(rt, project)
    disk_shot = disk.get_shot(shot_id)
    pointer = disk_shot.selected_last_frame if frame == "last" else disk_shot.selected_first_frame
    if pointer != candidate_id:
        return _base_receipt(
            "shot.select_frame", ok=False, target=shot_id, shot_id=shot_id,
            error="verify_failed",
            note=f"写后重读 selected 指针不匹配（{pointer!r} != {candidate_id!r}）——如实失败")
    rt.record_work(project, "change",
                   f"Director 选定镜头 {shot_id} 的{'尾帧' if frame == 'last' else '首帧'}候选 {candidate_id}",
                   participants=["director"], affected_domains=["storyboard"],
                   references={"shot_id": shot_id}, status="completed")
    return _base_receipt(
        "shot.select_frame", ok=True, target=shot_id, shot_id=shot_id,
        frame=frame, candidate_id=candidate_id, batch_id=batch.batch_id,
        verified=True, unchanged=False,
        note=f"镜头 {shot_id} 的{'尾帧' if frame == 'last' else '首帧'}已选定 {candidate_id}"
             f"（批次 {batch.batch_id}）。")


TOOL_EXECUTORS = {
    "shot.list": _execute_shot_list,
    "shot.read": _execute_shot_read,
    "shot.create": _execute_shot_create,
    "shot.create_many": _execute_shot_create_many,
    "shot.update": _execute_shot_update,
    "shot.update_many": _execute_shot_update_many,
    "shot.delete": _execute_shot_delete,
    "shot.move": _execute_shot_move,
    "shot.select_frame": _execute_shot_select_frame,
    "shot.generate_images": _execute_shot_generate_images,
    "shot.generate_videos": _execute_shot_generate_videos,
    "visual.review_image": _execute_visual_review,
    "visual.review_shot": _execute_review_shot,
    "project.visual_profile.read": _execute_visual_profile_read,
    "shot.production.inspect": _execute_shot_production_inspect,
    "shot.production.bind": _execute_shot_production_bind,
    "shot.production.refresh": _execute_shot_production_refresh,
    "story.list": _execute_story_list,
    "story.read": _execute_story_read,
    "script.read": _execute_script_read,
    "character.list": _execute_character_list,
    "character.read": _execute_character_read,
    "web.search": _execute_web_search,
    # Phase 39：连续性校准 / 帧级只读 / keyframes 桥
    **_CALIBRATION_EXECUTORS,
    **_FRAMES_EXECUTORS,
    **_BRIDGE_EXECUTORS,
    # Skill System V1（40.md）：skill.list/load/read_resource（只读）
    **make_skill_executors("director"),
}


# ============ 回执渲染 / 真相收口（分镜域措辞）============
_TOOL_LABELS = {
    "shot.list": "读取分镜", "shot.read": "读取镜头",
    "shot.create": "创建镜头", "shot.create_many": "批量创建镜头",
    "shot.update": "修改镜头", "shot.update_many": "批量修改镜头",
    "shot.delete": "删除镜头", "shot.move": "移动镜头",
    "shot.generate_images": "生成分镜图", "shot.generate_videos": "生成镜头视频",
    "visual.review_image": "视觉审查",
    "calibration.seam_measure": "测量接缝 MAE",
    "calibration.run_create": "创建校准 run",
    "calibration.trace_read": "读取校准 trace",
    "calibration.trace_upsert": "记录校准 trace",
    "video.extract_frame": "提取视频帧",
    "visual.sample_frames": "采样中间帧检查",
    "shot.bridge_create": "构造 keyframes 接续桥",
    "project.visual_profile.read": "读取项目视觉定义",
    "shot.production.inspect": "检查镜头生产准备",
    "shot.production.bind": "创建/替换生产绑定",
    "shot.production.refresh": "刷新绑定快照",
    "story.list": "读取故事段", "story.read": "读取故事段",
    "script.read": "读取剧本", "character.list": "读取角色列表",
    "character.read": "读取角色", "web.search": "搜索资料",
}


def _tool_label(name: str) -> str:
    return _TOOL_LABELS.get(name, name)


def _op_human(r: dict) -> str:
    """把一条真实回执渲染成人类可读的操作描述（确定性，来自回执而非模型叙述）。"""
    op = r.get("operation")
    sid = r.get("shot_id")
    if op == "shot.create":
        return f"创建镜头 {sid}"
    if op == "shot.update":
        ch = r.get("changed") or []
        return f"修改镜头 {sid}（{'、'.join(ch) or '内容'}）"
    if op == "shot.delete":
        n = len((r.get("deleted") or {}).get("shots") or [])
        return f"删除镜头 {sid}" + (f"及 {n - 1} 个下游镜头" if n > 1 else "")
    if op == "shot.select_frame":
        fr = "尾帧" if r.get("frame") == "last" else "首帧"
        return f"选定 {sid} {fr}候选 {r.get('candidate_id') or ''}"
    if op == "shot.move":
        return f"移动镜头 {sid} 到第 {r.get('order')} 位"
    if op == "shot.create_many":
        ids = [c.get("shot_id") for c in (r.get("created") or []) if c.get("shot_id")]
        return f"批量创建 {len(ids)} 个镜头（{', '.join(ids)}）" if ids else "批量创建镜头"
    if op == "shot.update_many":
        n = len(r.get("results") or [])
        return f"批量修改 {n} 个镜头"
    if op in ("shot.generate_images", "shot.generate_videos"):
        noun = "图片" if op == "shot.generate_images" else "视频"
        done = [x for x in (r.get("results") or []) if x.get("ok")]
        if not done:
            return f"生成{noun}（无成功项）"
        items = ", ".join(
            f"{x.get('shot_id')}{'首帧' if x.get('frame') == 'first' else '尾帧' if x.get('frame') == 'last' else ''}"
            for x in done)
        return f"为 {len(done)}/{r.get('count', len(done))} 个镜头生成{noun}（{items}）"
    if op == "visual.review_image":
        v = r.get("review") or {}
        n = len(v.get("issues") or [])
        return f"视觉审查 {sid}（{v.get('verdict') or '—'}，{n} 条问题）"
    if op == "visual.review_shot":
        d = r.get("derived") or {}
        n = len(d.get("hard_fails") or []) + len(d.get("soft_fails") or [])
        return f"Shot 审查 {sid}（overall={d.get('overall') or '—'}，{n} 维度失败）"
    return f"{op}({sid or ''})"


def _tool_summary(name: str, obs: dict) -> str:
    """tool.completed 的 summary：成功用 _op_human 真实描述，失败给原因。"""
    if name == "web.search":
        if obs.get("ok"):
            n = obs.get("n_results", 0)
            return f"找到 {n} 个参考来源" if n else "没有找到相关资料"
        if obs.get("code") == "search_budget_exhausted":
            return "本轮搜索次数已达上限"
        return f"搜索未完成：{obs.get('error') or obs.get('code') or '失败'}"
    if obs.get("ok"):
        if obs.get("operation"):
            return _op_human(obs)
        return f"{_tool_label(name)}完成"
    why = obs.get("error") or obs.get("code") or "失败"
    if obs.get("stale"):
        why = "版本过期，已拒绝"
    return f"{_tool_label(name)}未完成：{why}"


def _trace_entry(step: int, name: str, args: str, obs: dict) -> dict:
    """执行轨迹单条：turn→tool→target→result→revision。target 取最能标识操作对象的
    字段（shot_id/scene_id）；web.search 与视觉生成用搜索词/目标镜头列表。"""
    if name in SHOT_GENERATE_TOOL_NAMES:
        target = obs.get("target")
    else:
        target = (obs.get("shot_id") or obs.get("scene_id")
                  or (obs.get("target") if obs.get("ok") else None))
    if name == "web.search":
        target = obs.get("target")
    result = ("stale" if obs.get("stale")
              else "unchanged" if obs.get("unchanged")
              else "ok" if obs.get("ok")
              else "error")
    return {"step": step, "tool": name, "target": target, "result": result,
            "revision": (obs.get("revision") or obs.get("shots_revision")
                         or obs.get("shots_content_revision")),
            "error": (None if result in ("ok", "unchanged")
                      else (obs.get("error") or obs.get("code"))),
            "ok": bool(obs.get("ok"))}


def _ledger_brief(receipts: list) -> str:
    """本轮真实写工具结果渲染成给模型看的账本（确定性，来自回执而非模型叙述）。"""
    if not receipts:
        return "本轮没有任何写工具被真正调用（没有 shot.create/update/delete/move 的工具调用记录）。"
    lines = []
    for r in receipts:
        op = r.get("operation")
        if r.get("ok"):
            if r.get("unchanged"):
                lines.append(f"- {op}：ok=true，但内容无变化（unchanged），canonical 未改动。")
            else:
                lines.append(f"- {op}：ok=true（{_op_human(r)}），已重新读取 canonical 确认落盘。")
        else:
            why = r.get("error") or "未知错误"
            if r.get("stale"):
                why = "stale（版本过期，被拒绝，未写入）"
            lines.append(f"- {op}：ok=false，原因：{why}。canonical 未被该调用改动。")
    return "\n".join(lines)


def _honest_fallback(receipts: list) -> str:
    """纠偏后模型仍谎称成功时的确定性兜底文案——纯由真实回执生成。"""
    real_changes = [r for r in receipts
                    if r.get("ok") and r.get("verified") and not r.get("unchanged")]
    if real_changes:
        return "已完成的修改：" + "；".join(_op_human(r) for r in real_changes) + "。"
    if receipts:
        reasons = []
        for r in receipts:
            if r.get("stale"):
                reasons.append("镜头版本已过期")
            elif r.get("error") == "verify_failed":
                reasons.append("写入后校验未通过")
            else:
                reasons.append(r.get("error") or "工具执行失败")
        uniq = "、".join(dict.fromkeys(reasons))
        return (f"这次修改没有真正生效（{uniq}）。分镜保持原样。"
                "可以让我重试，或先让我读取当前最新分镜再改。")
    return "这次我没有真正执行修改（没有发出对应的工具调用）。请再说一次你的需求，我会实际操作。"


def _committed_shot_ids(receipts: list) -> list:
    """从已成功落盘的写回执里提取 shot_id 列表（checkpoint，供超时恢复 nudge）。"""
    out = []
    for r in receipts:
        if not (r.get("ok") and r.get("verified") and not r.get("unchanged")):
            continue
        op = r.get("operation")
        if op in ("shot.create", "shot.update", "shot.delete", "shot.move"):
            sid = r.get("shot_id")
            if sid and sid not in out:
                out.append(sid)
        elif op == "shot.create_many":
            for c in (r.get("created") or []):
                sid = c.get("shot_id")
                if sid and sid not in out:
                    out.append(sid)
        elif op == "shot.update_many":
            for c in (r.get("results") or []):
                sid = c.get("shot_id")
                if sid and sid not in out:
                    out.append(sid)
    return out


# 声称「本次创建了 N 个镜头」的计数表述（供夸大检测；宁漏勿误）。
# 🔴 「镜头/分镜/镜」后带负向前瞻（图|片|视频|资产）：「生成了 5 个分镜图」是视觉生成
# 生成计数、不是创建镜头计数——混用轮次（既建镜头又出图）不得把生成数量误判为创建夸大。
def _claimed_shot_count_mismatch(text: str, real_count: int) -> Optional[int]:
    """文本里「创建/拆成 N 个镜头」的 N 与真实成功创建数不符时返回 N。"""
    t = text or ""
    m = re.search(r"(?:创建|新增|拆成|拆分|建|生成|设计)[^0-9]{0,6}?(\d+)\s*(?:个)?\s*"
                  r"(?:镜头|分镜|镜)(?!图|片|视频|资产)", t)
    if m:
        v = int(m.group(1))
        if v != real_count:
            return v
    m = re.search(r"(\d+)\s*(?:个)?\s*(?:镜头|分镜|镜)(?!图|片|视频|资产)[^。！？\n]{0,8}?"
                  r"(?:建好|创建|完成|都建|设计好)", t)
    if m:
        v = int(m.group(1))
        if v != real_count:
            return v
    return None


# zero-write recovery 的分镜域补充措辞（「设计分镜/做分镜/出分镜」是明确写入请求，
# 通用正则只部分覆盖）。与 _is_explicit_write_request 取并集，宁窄勿误。
# Phase C1：补生产域措辞（刷新绑定/正式生成/出首帧——真机 §23 stale2 幻觉轮实证缺失）。
_DIRECTOR_WRITE_EXTRA_RE = re.compile(
    r"设计分镜|做分镜|做成分镜|分镜设计|生成分镜|出分镜|拆成分镜|拆成镜头|分镜拆分|"
    r"补一?个?镜头|加一?个?镜头|新建镜头|"
    r"刷新绑定|刷新.{0,6}绑定|绑定刷新|正式生成|生成首帧|出首帧|生成.{0,4}帧|bound_required")


def _is_director_write_request(instruction: str) -> bool:
    """用户本轮是否明确要求实际写入分镜（zero-write recovery 专用）。"""
    if _is_explicit_write_request(instruction):
        return True
    t = (instruction or "").strip()
    if not t or ("?" in t or "？" in t):
        return False
    if _is_chat_turn(t):
        return False
    return bool(_DIRECTOR_WRITE_EXTRA_RE.search(t))


# —— Director 域假成功补强（真机 2026-08-30 实测漏网）——
# 通用 _claims_success 依赖「已…动词」「动词…了/完成」「成功+动词」三种形态；分镜域
# 子弹式冒号体例（"**删除**：原第2段全部5个镜头"、"**重新创建**：5个新镜头"）与
# 「重新设计了这组镜头」这类措辞三种形态皆无，实测模型零工具调用编造整组重做未被拦截。
# 此处补分镜域判定，与通用判定取并集；否定窗口（不要/没/先别）与聊天轮保护不变。
_DIRECTOR_CLAIM_RE = re.compile(
    r"(删除|创建|新建|重建|重做|修改|更新|移动|调整|拆分|替换)\s*[*：:*]\s*"
    r"|(重新?设计|重做|重建|重构|推翻)[^。！？\n]{0,16}(了|完成|好)"
    r"|(重新)?(设计|创建|新建|拆分|拆成|生成)\s*(了)?\s*\d+\s*(个|组)?\s*(新?镜头|分镜)"
    r"|(刷新|绑定|生成|出图|生成首帧)[^。！？\n]{0,10}(成功|完成|✅)"
    r"|(✅|job_id\s*`?[a-f0-9]{6,})",
    re.IGNORECASE)
_DIRECTOR_CLAIM_NEGATION_RE = re.compile(r"(没有|没|并未|并没有|尚未|未|未能|无法|不能|不要|先别|别)")


def _director_claims_success(text: str) -> bool:
    """分镜域补强版「声称成功」：通用判定 ∪ 子弹式/重设计措辞（带否定窗口）。"""
    if _claims_success(text):
        return True
    if not text:
        return False
    for m in _DIRECTOR_CLAIM_RE.finditer(text):
        window = text[max(0, m.start() - 6):m.start()]
        if _DIRECTOR_CLAIM_NEGATION_RE.search(window):
            continue
        # 否定词也可能出现在匹配 span 内部（「刷新失败：无法完成」）——同样不算声称成功
        if _DIRECTOR_CLAIM_NEGATION_RE.search(m.group(0)):
            continue
        return True
    return False


_ZERO_WRITE_RECOVERY_NUDGE = (
    "【系统提示】用户本轮明确要求实际修改分镜，但你到目前为止【还没有发起任何写工具"
    "调用】——在回复里用自然语言描述修改不等于完成修改。请立即：①先用只读工具"
    "（story.list / story.read / shot.list / shot.read / character.read）确认当前真实状态；"
    "②真正发起对应的写工具（shot.create / shot.create_many / shot.update / shot.update_many"
    " / shot.delete / shot.move）完成用户要求，等工具真实返回 ok=true 后再汇报结果；"
    "写工具一个一个来，每次用上一次返回的最新版本指纹。如果你确实无法执行（需求不清楚、"
    "目标不存在等），就明确告诉用户原因，【不要】在没有工具真实返回的情况下声称已经修改完成。")


# zero-read recovery（读域真相闸门，STRESS F2 的直接拦截点；zero-write 的读域镜像）：
# 本 turn 零次读工具调用 + 回复引用具体 shot_id 细节 / 声称"已核对/逐镜精读" → 注入
# 一次 nudge（turn-local、只一次、不循环）。纯探讨轮（不引用镜头、不声称核对）不拦。
_ZERO_READ_RECOVERY_NUDGE = (
    "【系统提示】你上面的回复引用了具体镜头内容，或声称已核对/逐镜精读/以工具返回为准，"
    "但本轮【零次读工具调用】——用历史会话记忆冒充读取是不允许的：多轮修改后记忆里的"
    "镜头可能已被删除或修改。请立即：①真正调用只读工具（shot.list / shot.read / "
    "story.list / story.read / script.read / character.list / character.read）读取相关"
    "镜头/故事段的【当前真实内容】；②只依据工具真实返回重新作答。如果你不打算读取，"
    "就不要声称已核对、也不要引用具体镜头细节。系统会校验回复中引用的 shot_id/scene_id "
    "是否真实存在，不存在的会被附加勘误标注。")


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


def _deleted_ids_this_turn(receipts: list) -> set:
    """本轮真实删除的镜头 ID（含级联下游）——"已删除 s05_c03" 是合法陈述，
    ID 勘误时豁免。"""
    out: set = set()
    for r in receipts or []:
        if (r.get("ok") and r.get("verified")
                and r.get("operation") == "shot.delete"):
            d = r.get("deleted") or {}
            out.update(d.get("shots") or [])
            if r.get("shot_id"):
                out.add(r["shot_id"])
    return out


def _append_id_errata(rt: AgentRuntime, project: str, text: str, receipts: list) -> str:
    """ID 存在性校验（防敷衍）：最终回复中出现的全部 shot_id/scene_id 逐一校验是否
    存在于当前 canonical；引用不存在的 ID → 附加系统勘误块（确定性，不静默、不改写
    模型原文）。纯追加，任何异常不影响主流程。"""
    try:
        if not text or not session_governance._HAS_ANY_ID_RE.search(text):
            return text
        proj = _reload(rt, project)
        scene_ids = {sc.scene_id for sc in (proj.scenes or [])}
        shot_ids = {sh.shot_id for sc in (proj.scenes or [])
                    for sh in (sc.shots or [])}
        block = session_governance.validate_cited_ids(
            text, scene_ids, shot_ids, exempt=_deleted_ids_this_turn(receipts))
        if block:
            return text + block
    except Exception:
        pass
    return text


def _refresh_system(messages: list, rt: AgentRuntime, project: str) -> None:
    """每轮工具调用后无条件刷新 system（项目元信息/计数）。"""
    try:
        pdir = rt._resolve_project_dir(project)
        proj = ProjectStore(pdir).load()
        messages[0] = {"role": "system", "content": _build_system(proj, pdir)}
    except Exception:
        pass


_FACT_SUMMARY_MARK = "【实际执行记录】"


def _append_fact_summary(rt: AgentRuntime, project: str, text: str, receipts: list) -> str:
    """在模型回复后追加【确定性事实摘要】——由真实 receipts + 当前 canonical 生成，
    不信模型叙述。覆盖全成功/部分成功/全失败三态：已成功资产明确列出并保留，未完成项
    也如实标注。纯追加，不删改模型原文。"""
    if not receipts:
        return text   # 无写工具调用，无可总结
    real_changes = [r for r in receipts
                    if r.get("ok") and r.get("verified") and not r.get("unchanged")]
    failed = [r for r in receipts if not r.get("ok")]   # stale/error/cancelled
    # 纯 CRUD 失败且无任何成功变更（如删除不存在的对象、stale 被拒）→ 不追加事实块
    # （保持旧行为，失败原因由错误信息/真相闸门体现）。生成类操作（图/视频）即使全失败
    # 也追加，用于确定性地告知"本次没有完成生成 / 已完成 N 项"。
    has_generate = any(r.get("operation") in SHOT_GENERATE_TOOL_NAMES for r in receipts)
    if not real_changes and not has_generate:
        return text
    try:
        proj = _reload(rt, project)
        n_shots = sum(len(sc.shots or []) for sc in (proj.scenes or []))
        lines = ["\n\n---\n【实际执行记录】（系统据真实回执生成，非模型叙述）"]
        for r in real_changes:
            lines.append(f"✓ {_op_human(r)}")
        for r in failed:
            why = r.get("error") or r.get("code") or "失败"
            if r.get("stale"):
                why = "版本过期，已拒绝"
            elif r.get("cancelled"):
                why = "已取消"
            sid = r.get("shot_id") or ""
            lines.append(f"✗ {r.get('operation', '操作')}{(' ' + sid) if sid else ''}：{why}")
        # 确定性完成情况总结行（不让模型猜）
        if failed and not real_changes:
            lines.append(f"\n本次没有完成生成（{len(failed)} 项未成功）。")
        elif failed and real_changes:
            lines.append(f"\n已完成 {len(real_changes)} 项，{len(failed)} 项未完成/取消。")
        else:
            lines.append(f"\n已完成 {len(real_changes)} 项。")
        lines.append(f"当前分镜共 {n_shots} 个镜头。")
        return text + "\n".join(lines)
    except Exception:
        return text


# ============ 主循环（镜像 Script Writer 机制：单写串行 / 真相闸门 / zero-write
# recovery / 搜索预算 / 防失控 / 超时恢复 / 协作取消 / 流式事件）============
class _WallClockExceeded(Exception):
    """墙钟预算耗尽（Sub-Agent 实例用）：调用方捕获后走诚实停止路径——
    预算耗尽不是成功，绝不假成功声明。"""

    def __init__(self, budget_s: float):
        super().__init__(f"wall clock budget {budget_s}s exceeded")
        self.budget_s = budget_s


async def _run_tool_loop(rt: AgentRuntime, client, project: str,
                         messages: list, instruction: str,
                         message_id: Optional[str],
                         max_steps: int = DEFAULT_MAX_TOOL_STEPS,
                         thinking: bool = False,
                         event_cb=None,
                         state: Optional[dict] = None,
                         cancel_event=None,
                         tools_whitelist: Optional[set] = None,
                         extra_executors: Optional[dict] = None,
                         wall_clock_s: Optional[float] = None) -> dict:
    """原生 function-calling 循环 + 执行真实性收口（结构性，非提示词）。

    tools_whitelist（会话治理新增，默认 None = 行为与基线完全一致）：只读审查会话
    （Clean Review）传入读工具集——client 只被告知白名单内的工具 schema，运行时
    再拒绝白名单外的任何调用（写工具物理不可用，双保险）；此模式下写域真相闸门
    （假成功纠偏/计数夸大/收尾重读）不上膛（审查报告天然包含"建议删除：…"类措辞，
    会被误判为假成功声明），zero-read gate 照常生效（审查者也必须真实读取）。

    extra_executors（Sub-Agent 新增，默认 None = 行为不变）：本实例专属 executor
    （如 Scene Agent 的 proposal.submit 闭包——需要实例 state 的 trace 做 basis
    防伪）。查找顺序：extra_executors → TOOL_EXECUTORS。

    wall_clock_s（Sub-Agent 新增，默认 None = 行为不变）：墙钟预算；超时抛
    _WallClockExceeded（调用方捕获后走诚实停止路径——预算耗尽不是成功）。"""
    created = updated = deleted = moved = False
    if state is None:
        state = {}
    read_only = tools_whitelist is not None
    offered_tools = (DIRECTOR_TOOLS if tools_whitelist is None else
                     [t for t in DIRECTOR_TOOLS
                      if t["function"]["name"] in tools_whitelist])
    _t0 = time.monotonic()
    events: list = state.setdefault("events", [])
    receipts: list = state.setdefault("receipts", [])
    write_attempts = 0
    corrective_round = 0
    zero_write_recovery_used = False
    zero_read_recovery_used = False
    read_calls = 0
    trace: list = state.setdefault("trace", [])
    reasonings: list = state.setdefault("reasonings", [])
    last_read_after_write = -1
    search_count = 0
    search_sources: list = state.setdefault("search_sources", [])
    search_queries_seen: set = state.setdefault("search_queries_seen", set())
    repeat_fail_streak = 0
    last_fail_sig = None
    timeout_retries = 0
    max_batch_hint = MAX_CREATE_BATCH   # 10 → 5 → 2 → 1

    def _on_read_timeout() -> bool:
        nonlocal timeout_retries, max_batch_hint
        if timeout_retries >= MAX_TIMEOUT_RETRIES:
            return False
        timeout_retries += 1
        max_batch_hint = _BATCH_DOWNGRADE.get(max_batch_hint, 1)
        committed = _committed_shot_ids(receipts)
        committed_str = ", ".join(committed) if committed else "（无）"
        nudge = (
            f"[系统恢复提示 MODEL_READ_TIMEOUT] 上一次批量生成因模型响应超时被中断"
            f"（read timeout，第 {timeout_retries}/{MAX_TIMEOUT_RETRIES} 次）。"
            f"本次没有形成完整工具调用、没有新增任何提交。"
            f"已成功提交的镜头：{committed_str}。"
            f"请【只继续尚未完成的部分，不要重做已提交项】；"
            f"用更小的批次（建议每批 ≤{max_batch_hint} 项）重新发起，避免再次超时。"
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
        if wall_clock_s is not None and (time.monotonic() - _t0) > wall_clock_s:
            raise _WallClockExceeded(wall_clock_s)
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
                        thinking=thinking, tools=offered_tools,
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
                    tools=offered_tools, tool_choice="auto")
                tool_calls = resp.get("tool_calls")
                content = resp.get("content")
        except httpx.ReadTimeout:
            if not _on_read_timeout():
                raise
            continue

        if not tool_calls:
            text = (content or "").strip()
            # ---- 真相闸门 ----
            real_changes = [r for r in receipts
                            if r.get("ok") and r.get("verified") and not r.get("unchanged")]
            # Phase C1（27.md §15）：真实 tool result 携带的结构化 production
            # prerequisite——Agent「如实报告前置缺失并停止」是合法收口，不算假成功。
            prerequisite_blocked = bool(state.get("prerequisite_warnings"))
            chat_turn = (write_attempts == 0 and _is_chat_turn(instruction))
            false_claim = (_director_claims_success(text) and not real_changes
                           and not chat_turn and not prerequisite_blocked)
            real_searches = [t for t in trace if t["tool"] == "web.search" and t.get("ok")]
            search_false_claim = (_claims_search_success(text) and not real_searches
                                  and not chat_turn)
            real_create_count = sum(
                1 for r in real_changes if r.get("operation") == "shot.create")
            real_create_count += sum(
                len(r.get("created") or []) for r in real_changes
                if r.get("operation") == "shot.create_many")
            over_claim_n = (_claimed_shot_count_mismatch(text, real_create_count)
                            if real_changes and real_create_count else None)
            # needs_final_read 的「最后一次写入」不含视觉生成工具：它们的 executor 内部
            # 已做「重读 canonical + 文件存在」验证（强于 shot.list），不再强制收尾重读。
            last_write_step = max((t["step"] for t in trace
                                   if t["tool"] in DIRECTOR_WRITE_OPERATIONS
                                   and t["tool"] not in DIRECTOR_LONG_TASK_TOOLS), default=-1)
            reply_summarizes = bool(re.search(r"\d+\s*个?(镜头|分镜|镜)|第.{1,4}镜|共\d+", text or ""))
            needs_final_read = (real_changes and _claims_success(text)
                                and reply_summarizes
                                and last_read_after_write < last_write_step)
            if (not zero_write_recovery_used
                    and write_attempts == 0 and not real_changes
                    and not read_only and not prerequisite_blocked
                    and _is_director_write_request(instruction)):
                zero_write_recovery_used = True
                log.info("director zero-write recovery 触发（project=%s）", project)
                messages.append({"role": "assistant", "content": text or ""})
                messages.append({"role": "user", "content": _ZERO_WRITE_RECOVERY_NUDGE})
                _refresh_system(messages, rt, project)
                continue
            if (not read_only
                    and (false_claim or over_claim_n is not None
                         or needs_final_read or search_false_claim)):
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
                            "收尾前请先用 shot.list 读取最新镜头列表（关注任务相关段落），然后【以这次"
                            "读取的真实结果】总结你创建/修改/删除了什么、当前镜头数量与顺序。"
                            "不要凭自己之前写的文字记忆声称完成。")
                    elif over_claim_n is not None:
                        nudge = (
                            f"你上面说创建/拆成了 {over_claim_n} 个镜头，但工具的真实成功创建回执只有 "
                            f"{real_create_count} 个（{brief}）。数量必须以工具真实返回为准。"
                            "请基于真实回执重新回答：要么如实说已创建几个（给出真实 shot_id），"
                            "要么继续调用 shot.create / shot.create_many 把剩余镜头真正创建出来。")
                    elif write_attempts == 0:
                        nudge = (
                            "你上面声称已经创建/修改/删除了镜头，但本轮你【根本没有发起对应的"
                            "工具调用】。光用文字说「已完成」不会改动任何东西。\n"
                            + brief + "\n请要么真正调用对应写工具完成修改，要么如实告诉用户"
                            "你没有改动、需要怎么做。")
                    else:
                        nudge = (
                            "你上面声称已经完成，但工具的真实返回显示没有任何一次写入成功确认：\n"
                            + brief + "\n请只依据上面真实的工具返回结果回答。可以用最新数据重新调用"
                            "工具重试；如果不打算重试，就如实告诉用户这次没有改动成功。")
                    messages.append({"role": "user", "content": nudge})
                    _refresh_system(messages, rt, project)
                    continue
                text = _honest_fallback(receipts)
            # ---- zero-read gate（读域真相闸门，F2 类幻觉的直接拦截点）----
            # 零次读工具调用 + 回复引用 ≥2 个 shot_id 或声称已核对/逐镜精读 → 注入
            # 一次 recovery nudge（turn-local、只一次、不循环）。只读审查模式同样生效。
            # 放在写域假成功纠偏【之后】：同时命中两者时先走既有写域纠偏（保持基线
            # 行为不变），纠偏覆写后的兜底文本不会再命中本闸门。
            if (not zero_read_recovery_used and read_calls == 0
                    and not real_changes
                    and session_governance.is_read_required_reply(text)):
                zero_read_recovery_used = True
                log.info("director zero-read recovery 触发（project=%s）", project)
                messages.append({"role": "assistant", "content": text or ""})
                messages.append({"role": "user", "content": _ZERO_READ_RECOVERY_NUDGE})
                _refresh_system(messages, rt, project)
                continue
            text = strip_reserved_fact_blocks(text)
            # ID 存在性校验（防敷衍）：引用不存在的 shot_id/scene_id → 附加系统勘误块
            text = _append_id_errata(rt, project, text, receipts)
            text = _append_fact_summary(rt, project, text, receipts)
            return {"text": text or "已完成。",
                    "created": created, "updated": updated, "deleted": deleted,
                    "moved": moved,
                    "prerequisite_warnings": state.get("prerequisite_warnings", []),
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

            is_write_tool = (name in DIRECTOR_WRITE_OPERATIONS)
            if is_write_tool and write_done_this_step:
                obs = {
                    "ok": False, "operation": name,
                    "error": "本步已执行过一个写操作。写工具必须一个一个来：等前一个完成、"
                             "拿到返回的最新 revision/状态后，再发下一个写工具调用。"
                             "不要在同一次回复里批量发多个写工具（会用旧 revision 导致 stale）。"
                             "只读工具（shot.list/shot.read/story.list/story.read/script.read/"
                             "character.list/character.read/web.search）可以一次多个。",
                }
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": json.dumps(obs, ensure_ascii=False)})
                trace.append(_trace_entry(step, name, args, obs))
                if event_cb:
                    event_cb({"type": "tool.completed", "step_id": step,
                              "call_id": call_id, "name": name, "ok": False,
                              "summary": "本步写工具串行，已跳过"})
                continue

            # 读域调用计数（zero-read gate 用）：无论成败，真实发起过读工具调用即可。
            if name in DIRECTOR_READ_TOOLS:
                read_calls += 1
            executor = ((extra_executors or {}).get(name)
                        or TOOL_EXECUTORS.get(name))
            exc_msg = None
            if read_only and name not in (tools_whitelist or set()):
                obs = {"ok": False, "operation": name,
                       "error": ("本会话为只读审查会话，禁止调用写工具（任何修改都不会被执行）。"
                                 "请只用只读工具读取并核对，产出问题清单与修改建议。")}
            elif (executor is not None and name == "web.search"
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
                    if name in DIRECTOR_LONG_TASK_TOOLS:
                        # 长任务 executor（视觉生成，分钟级）：需要流式进度回传与协作取消；
                        # 包装 event_cb 注入 step/call 上下文（executor 只发 tool.progress 业务字段）。
                        if event_cb:
                            def _gen_cb(ev, _step=step, _cid=call_id, _name=name):
                                event_cb({"type": ev.get("type", "tool.progress"),
                                          "step_id": _step, "call_id": _cid, "name": _name,
                                          "label": _tool_label(_name),
                                          **{k: v for k, v in ev.items() if k != "type"}})
                        else:
                            _gen_cb = None
                        obs = await executor(rt, project, args, instruction, message_id,
                                             event_cb=_gen_cb, cancel_event=cancel_event)
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
            # Phase C1（27.md §15）：收集真实 tool result 的结构化 production
            # prerequisite（inspect/bind/生成拒绝都携带）——供 turn 收口豁免真相闸门
            # 的假成功判定 + 进 TaskResult.warnings（Producer 路由依据）。
            if isinstance(obs, dict) and obs.get("prerequisite_warnings"):
                state.setdefault("prerequisite_warnings", []).extend(
                    w for w in obs["prerequisite_warnings"] if isinstance(w, dict))
            # 防失控（需求 #11）：同一工具连续以同样方式失败 → 诚实停止。计数【按工具】
            # （交叉的成功只读调用不重置该工具的失败连击——真机 2026-08-30 实测模型会
            # 「create_many stale → shot.list → create_many stale」循环 9 次绕过连击护栏）。
            if ok_now and name == (last_fail_sig or ("", ""))[0]:
                repeat_fail_streak = 0
                last_fail_sig = None
            if not ok_now:
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
                    stale_hint = ""
                    if obs.get("stale") and obs.get("current_revision"):
                        stale_hint = (f"当前正确的指纹值是 \"{obs['current_revision']}\"，"
                                      "把它原样填进 expected 参数即可。")
                    nudge = (f"「{name}」已连续 {repeat_fail_streak} 次以同样方式失败"
                             f"（{obs.get('error') or obs.get('code')}）。继续重复同样调用不会成功。"
                             + stale_hint +
                             " 请先停止重试：核对失败回执里的 note 与 current_revision/"
                             "current_shot，修正参数后再试一次；若仍失败，如实告诉用户当前"
                             "遇到的问题、哪些已成功完成。")
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

            # 写域调用记入账本（含拼错的 shot.* 未知工具：也算写尝试失败）。
            # Phase C1：shot.production.inspect 是只读聚合工具，不计写域。
            is_write_domain = (name in DIRECTOR_WRITE_OPERATIONS
                               or name.startswith("shot."))
            if is_write_domain and name not in DIRECTOR_READ_TOOLS:
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

            # 据【真实回执】（而非模型叙述）设置 mutated 标志 + 事件。
            if (name in DIRECTOR_WRITE_OPERATIONS and obs.get("ok")
                    and obs.get("verified") and not obs.get("unchanged")):
                sid = obs.get("shot_id")
                if name == "shot.create":
                    created = True
                    s = f"创建镜头 {sid}"
                    events.append({"kind": "shot_create", "shot_id": sid, "summary": s})
                elif name == "shot.create_many":
                    created = True
                    ids = [c.get("shot_id") for c in (obs.get("created") or [])
                           if c.get("shot_id")]
                    s = f"批量创建 {len(ids)} 个镜头：{', '.join(ids)}"
                    events.append({"kind": "shot_create_many",
                                   "scene_id": obs.get("scene_id"),
                                   "shot_ids": ids, "summary": s})
                elif name == "shot.update":
                    updated = True
                    changed = "、".join(obs.get("changed") or []) or "内容"
                    s = f"修改镜头 {sid}（{changed}）"
                    events.append({"kind": "shot_update", "shot_id": sid, "summary": s})
                elif name == "shot.update_many":
                    updated = True
                    res = obs.get("results") or []
                    s = f"批量修改 {len(res)} 个镜头"
                    events.append({"kind": "shot_update_many", "results": res, "summary": s})
                elif name == "shot.delete":
                    deleted = True
                    d = obs.get("deleted") or {}
                    n = len(d.get("shots") or [])
                    s = f"删除镜头 {sid}" + (f"及 {n - 1} 个下游镜头" if n > 1 else "")
                    events.append({"kind": "shot_delete", "shot_id": sid, "summary": s})
                elif name == "shot.move":
                    moved = True
                    s = f"移动镜头 {sid} 到第 {obs.get('order')} 位"
                    events.append({"kind": "shot_move", "shot_id": sid, "summary": s})
                elif name == "proposal.commit":
                    # Sub-Agent 提案提交（挂载后才可达；未挂载时本分支不可达，
                    # 行为不变）。旗标从 DiffEngine 汇总布尔取得（intent 判定用）。
                    created = created or bool(obs.get("created"))
                    updated = updated or bool(obs.get("updated"))
                    deleted = deleted or bool(obs.get("deleted"))
                    moved = moved or bool(obs.get("moved"))
                    events.append({
                        "kind": "proposal_commit",
                        "proposal_id": obs.get("proposal_id"),
                        "scene_id": obs.get("scene_id"),
                        "summary": (f"通过提案改写 {obs.get('scene_id')}"
                                    f"（{obs.get('n_created', 0)} 新建 / "
                                    f"{obs.get('n_deleted', 0)} 删除 / "
                                    f"{obs.get('n_moved', 0)} 移动 / "
                                    f"{obs.get('n_updated', 0)} 更新）")})
                elif name == "shot.generate_images":
                    done = [x for x in (obs.get("results") or []) if x.get("ok")]
                    s = _op_human(obs)
                    events.append({"kind": "shot_generate_images",
                                   "shot_ids": [x.get("shot_id") for x in done],
                                   "summary": s})
                elif name == "shot.generate_videos":
                    done = [x for x in (obs.get("results") or []) if x.get("ok")]
                    s = _op_human(obs)
                    events.append({"kind": "shot_generate_videos",
                                   "shot_ids": [x.get("shot_id") for x in done],
                                   "summary": s})

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


# ============ Clean Review（独立干净会话的连续性审查，STRESS F1/F2 vs F4）============
# 证据：同一 Director 定义，污染主会话审查 = 零读取全幻觉（F2），干净新会话审查 =
# 17 次工具调用、14/14 全读、几乎全命中（F4）。→ 不发明新 Checker Agent 定义，
# 用「现有 Director 定义 + 一次性只读会话」做审查实例。
# 触发：A（结构写操作累计 ≥ CLEAN_REVIEW_THRESHOLD，自动）/ B（用户审查意图，必触发）。
REVIEW_READ_TOOLS = DIRECTOR_READ_TOOLS | {"web.search"} | SKILL_TOOL_NAMES

_REVIEW_PREAMBLE = """你是本项目派出的【只读连续性审查者】——同一 Director 定义、独立干净会话。规则：
1. 你必须用读工具（story.list / story.read / script.read / character.list / character.read / shot.list / shot.read / web.search）自主读取当前真实状态后再下结论。除了本消息附带的创作约束与项目状态头，你没有任何历史上下文；禁止凭空推断任何镜头的具体内容。
2. 你没有写权限：任何写工具调用都会被系统拒绝。你只产出问题清单与修改建议，由用户决定是否在主会话执行修改。
3. 重点检查：内容级重复镜头（不同 shot_id 描述几乎相同）、镜头顺序/叙事错位、角色空间一致性（某时刻谁在哪里）、道具与伏笔弧线完整性、时间线、镜头 characters 是否都在角色 canonical 名单、以及任何违反用户创作约束的地方。
4. 报告格式：每个问题一条，注明 scene_id/shot_id、问题描述、修改建议；最后给总体结论。你引用的每个 shot_id/scene_id 都必须来自工具真实返回。"""


def _review_instruction(trigger: str, user_request: Optional[str],
                        brief: Optional[str], header: str) -> str:
    parts = [_REVIEW_PREAMBLE]
    parts.append("# 用户创作约束（原始 brief，审查时必须对照）\n"
                 + (brief or "（本项目未记录原始 brief）"))
    parts.append(header)
    if trigger == "user_request":
        parts.append("# 本次审查任务\n用户要求：" + (user_request or "")
                     + "\n请据此执行这次审查：先 story.list + shot.list 总览，再逐段"
                       "story.read + shot.read 细读，输出问题清单（只报告不修改）。")
    else:
        parts.append("# 本次审查任务\n主会话的结构修改已累计到阈值，请对全片做一次连续性"
                     "审查：先 story.list + shot.list 总览，再逐段 story.read + "
                     "shot.read 细读，输出问题清单（只报告不修改）。")
    return "\n\n".join(parts)


async def run_clean_review(rt: AgentRuntime, client, project: str, *,
                           trigger: str, user_request: Optional[str] = None,
                           event_cb=None, cancel_event=None) -> dict:
    """执行一次 Clean Review：一次性只读审查会话（不复用、用完即弃——复用会让
    review session 自己积累 staleness）。Reviewer = 现有 Director agent 定义，
    只读模式（client 只见读工具 schema + 运行时拒绝写调用）；上下文注入两样：
    ① 让它用读工具自主读 canonical（F4 证明它能读好）；② 用户原始 brief/关键约束。
    只报告不修改；报告落盘 output/agents/reviews/。"""
    store = rt._session_store(project)
    pdir = rt._resolve_project_dir(project)
    review_session = session_governance.create_review_session(store, project)
    proj = ProjectStore(pdir).load()
    instruction = _review_instruction(
        trigger, user_request,
        session_governance.get_brief(pdir, project),
        session_governance.build_state_header(pdir, project))
    messages: list[dict] = [{"role": "system", "content": _build_system(proj, pdir)},
                            {"role": "user", "content": instruction}]
    final = await _run_tool_loop(rt, client, project, messages, instruction, None,
                                 event_cb=event_cb, cancel_event=cancel_event,
                                 tools_whitelist=REVIEW_READ_TOOLS)
    report = final.get("text") or ""
    trace = final.get("trace") or []
    # Compaction 遥测：审查会话也落一行（session 为一次性 review session，可区分）
    try:
        _tio = sum(len(m.get("content") or "") for m in messages
                   if m.get("role") == "tool")
        _tio += sum(len(((tc.get("function") or {}).get("arguments")) or "")
                    for m in messages for tc in (m.get("tool_calls") or []))
        session_governance.log_context_telemetry(
            pdir, project, "director", review_session.session_id,
            f"review_{review_session.session_id}",
            history_chars=len(instruction), system_chars=len(messages[0]["content"]),
            tool_io_chars=_tio)
    except Exception:
        pass
    # 审查会话留存（一次性：不再被任何后续 turn 复用，文件保留供审计/对照 events 流）
    store.append_message(review_session.session_id,
                         AgentMessage(role="user", content=instruction))
    store.append_message(review_session.session_id,
                         AgentMessage(role="assistant", content=report,
                                      meta={"trigger": trigger,
                                            "tool_calls": len(trace),
                                            "trace": trace}))
    # 报告落盘（带触发原因与时间）
    reviews_dir = pdir / "output" / "agents" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"review_{int(time.time() * 1000)}"
    report_path = reviews_dir / f"{report_id}.json"
    atomic_write_json(report_path, {
        "report_id": report_id, "project": project, "trigger": trigger,
        "user_request": user_request, "review_session_id": review_session.session_id,
        "tool_calls": len(trace), "trace": trace, "report": report,
        "created_at": time.time(),
    })
    return {"report": report, "report_path": str(report_path),
            "tool_calls": len(trace), "trace": trace,
            "review_session_id": review_session.session_id, "trigger": trigger}


async def _continuity_review_turn(rt, store, project: str, session_id: str,
                                  user_msg, content: str, client, *,
                                  event_cb=None, cancel_event=None) -> dict:
    """Clean Review 触发 B（用户主动）的 turn 路径：不在（可能已 staleness 污染的）
    主会话里跑审查，直接派发独立只读审查会话，报告作为本轮回复返回主会话/用户。"""
    review = await run_clean_review(rt, client, project, trigger="user_request",
                                    user_request=content, event_cb=event_cb,
                                    cancel_event=cancel_event)
    # 用户刚拿到一次审查 → 结构计数自本次审查起重新累计
    try:
        session_governance.GovernanceStore(
            rt._resolve_project_dir(project), project).reset_since_review()
    except Exception:
        pass
    assistant_text = (f"【连续性审查报告】（由独立只读审查会话完成，"
                      f"{review['tool_calls']} 次真实工具读取；只报告不修改，"
                      f"是否修改由你决定）\n\n{review['report']}")
    events = [{"kind": "continuity_review", "trigger": "user_request",
               "report_path": review["report_path"],
               "review_session_id": review["review_session_id"],
               "tool_calls": review["tool_calls"],
               "summary": "独立会话连续性审查完成"}]
    # N2 收口（d，2026-09-06）：审查 trace 如实回传——此前硬编码空 trace，Producer 侧
    # Task Result n_tool_calls 恒为 0，与报告文本"N 次真实工具读取"口径断裂。verified
    # 仍由空 receipts 派生为 false（审查不产生写入，语义不变）。
    review_trace = review.get("trace") or []
    meta = {"intent": "continuity_review", "events": events, "evidence_refs": [],
            "receipts": [], "write_attempts": 0, "write_success": 0,
            "trace": review_trace, "reasonings": [], "search_sources": [],
            "generated_assets": [], "pending_delete_scenes": []}
    asst = AgentMessage(role="assistant", content=assistant_text, meta=meta)
    store.append_message(session_id, asst)
    return {"assistant_message": assistant_text,
            "created": False, "updated": False, "deleted": False, "moved": False,
            "events": events, "message": asst.to_dict(),
            "intent": "continuity_review", "receipts": [], "trace": review_trace,
            "search_sources": [], "evidence_refs": [], "linked_task_ids": [],
            "linked_change_session_id": None, "session_id": session_id,
            "agent_id": "director"}


# ============ N2 收口（2026-09-06）：Bridge 委派 envelope 的执行意图路由闸门 ============
# 真机证据（Producer V1 Phase 2 Case3 / Phase 3 Turn1）：Producer 委派 objective 含
# "连续性"（如"实际修复连续性问题"）时，is_review_request 的裸词命中把整个委派改道进
# 只读审查会话（receipts=[]/trace=[] → Task Result n_tool_calls=0/verified=false），
# Producer 的[执行性纠偏]按规则保持 objective 重发 → 二次命中同一路由。
# 修复：仅当内容是 Bridge 委派 envelope（固定前缀）且 用户意图/任务目标 行命中执行动词
# 时，跳过审查路由、交正常写 loop（Director 自主判断写或不写；无值得修改的问题可明确
# no-change，不强制写）。纯检查 envelope（无执行动词）仍走审查；用户直聊行为逐字节不变。
_BRIDGE_ENVELOPE_PREFIX = "【来自 Producer 的委派任务】"

# 执行动词表（对齐 Director 写域与 producer delegate 描述措辞；刻意不含 检查/审查/
# 分析/梳理/报告 等 review 词——那些留审查路由或主 loop 自主判断）
_ENVELOPE_EXECUTION_INTENT_RE = re.compile(
    r"修改|创建|新建|重建|重做|重新设计|重写|删除|移动|更新|修复|更正|"
    r"补(齐|全|充|上)|执行|实际改|同步|优化|调整|生成")


def _bridge_envelope_demands_execution(content: str) -> bool:
    """Producer 委派 envelope 且 用户意图/任务目标 行明确要求实际修改 → True。
    只看这两行（situation/constraints 是背景与约束，不参与意图判定）。"""
    if not (content or "").lstrip().startswith(_BRIDGE_ENVELOPE_PREFIX):
        return False
    for line in (content or "").splitlines():
        ls = line.strip()
        if ls.startswith(("用户意图：", "任务目标：")) and \
                _ENVELOPE_EXECUTION_INTENT_RE.search(ls):
            return True
    return False


# ============ 主 turn ============
async def director_turn(rt: AgentRuntime, project: str, session_id: str,
                        content: str, *, thinking: bool = False,
                        event_cb=None, cancel_event=None) -> dict:
    """Director 原生 tool-loop turn。

    返回 {assistant_message, created, updated, deleted, moved, events, message, intent,
    receipts, trace, search_sources, session_id, agent_id}。语义与 script_writer_turn /
    character_designer_turn 对齐：thinking/event_cb 仅流式端点传入；cancel_event 协作式
    停止（步骤边界安全中断）。"""
    store = rt._session_store(project)
    session = store.load(session_id)
    if session is None:
        raise AgentError("unknown_scope", f"session 不存在: {session_id}")
    if session.agent_id != "director":
        raise AgentError("permission_denied", "该会话仅限 Director")
    if not rt._is_enabled("director"):
        raise AgentError("disabled_agent", "Director 已禁用")

    user_msg = AgentMessage(role="user", content=content)
    store.append_message(session_id, user_msg)
    rt._maybe_capture_preference(project, content, user_msg.message_id)
    session = store.load(session_id)

    pdir = rt._resolve_project_dir(project)
    # 会话治理：best-effort 捕获用户创作约束（项目首个 ≥10 字符用户消息 → brief，
    # 供 Clean Review / Re-grounding 注入；正常创作流里这就是交给 SW 的故事 brief）
    session_governance.capture_brief_if_absent(pdir, project, content, "director")
    history = [{"role": m.role, "content": m.content}
               for m in session.messages[:-1]
               if m.role in ("user", "assistant") and m.content and m.status is None]

    client = rt._resolve_client_or_none(agent_id="director")
    # Clean Review 触发 B（用户主动，必触发）：审查意图 → 独立只读审查会话执行，
    # 不在（可能已 staleness 污染的）主会话里跑审查（STRESS F1/F2 vs F4）。
    # N2 收口：Bridge 委派 envelope 明确要求实际修改时不改道（否则执行任务被审查路由
    # 劫持为只读报告、n_tool_calls=0，Producer 纠偏重发还二次命中）。
    # Phase 39 hardening A（用户定版）：推广为内容级窄规则——领域词（连续性等）只表达
    # 话题；有显式 mutation 意图（校准/落档/修复/重试/插桥…）一律走正常 tool-loop，
    # 不物理剥夺写工具；纯审查请求仍走 Clean Review（session_governance.
    # should_route_to_clean_review）。
    if (client is not None
            and session_governance.should_route_to_clean_review(content)
            and not _bridge_envelope_demands_execution(content)):
        return await _continuity_review_turn(
            rt, store, project, session_id, user_msg, content, client,
            event_cb=event_cb, cancel_event=cancel_event)
    assistant_text: str
    status: Optional[str] = None
    created = updated = deleted = moved = False
    events: list = []
    receipts: list = []
    prerequisite_warnings: list = []   # Phase C1（27.md §15）：真实 tool result 派生的
                                       # 结构化 production prerequisite（turn 级聚合）
    reasoning: list = []
    trace: list = []
    search_sources: list = []
    # Compaction 遥测（改动四：只观察不做压缩）——本轮上下文规模度量
    tel_history_chars = sum(len(m.get("content") or "") for m in history)
    tel_system_chars = 0
    tel_tool_io_chars = 0

    if client is None:
        assistant_text = "[chat 未配置：Agnes 推理模型不可用，无法修改分镜。请在设置中配置 API Key。]"
        status = ResultStatus.NOT_CONFIGURED.value
    else:
        # Sub-Agent 工具挂载（bit-for-bit 门控）：settings 顶层键 subagent_enabled
        # 默认 false → 不挂载 → 工具列表/TOOL_EXECUTORS/写集合/系统提示逐字节不变。
        # 挂载幂等；每 turn 重置 delegation 预算（§2.1 本 turn <3）。
        try:
            from src.agents.subagent.tools import (
                is_subagent_enabled, mount_subagent_tools)
            from src.agents.subagent import registry as _sa_reg
            if is_subagent_enabled(rt):
                mount_subagent_tools()
                _sa_reg.reset_turn_budget()
        except Exception:
            log.exception("subagent 工具挂载检查失败（按未启用处理）")
        proj = ProjectStore(pdir).load()
        system = _build_system(proj, pdir)
        mem = rt._memory_supplement(project, "director")
        if mem:
            system += "\n\n" + mem
        messages: list[dict] = [{"role": "system", "content": system}]
        messages += history
        # Skill System V1（40.md §七）：turn 入口确定性 trigger hint——命中时拼进
        # user 消息（不落盘：session 中仍只存原 content，L2742 已 append）。
        _skill_hint = _skill_activation_hint("director", content, pdir)
        messages.append({"role": "user", "content":
                         (_skill_hint + "\n\n" + content) if _skill_hint else content})
        tel_system_chars = len(system)

        loop_state: dict = {}
        try:
            final = await _run_tool_loop(rt, client, project, messages, content,
                                         user_msg.message_id,
                                         thinking=thinking, event_cb=event_cb,
                                         state=loop_state, cancel_event=cancel_event)
            assistant_text = final["text"]
            created = final["created"]
            updated = final["updated"]
            deleted = final["deleted"]
            moved = final["moved"]
            events = final["events"]
            receipts = final.get("receipts", [])
            trace = final.get("trace", [])
            reasoning = final.get("reasonings", [])
            search_sources = final.get("search_sources", [])
            prerequisite_warnings = final.get("prerequisite_warnings", [])
        except _TurnStopped:
            log.info("director_turn 被停止（project=%s session=%s）", project, session_id)
            done = _committed_shot_ids(loop_state.get("receipts", []))
            done_str = ("已完成：" + "、".join(done)) if done else "本次没有已完成的修改"
            assistant_text = f"[任务已停止] {done_str}；其余未完成部分未执行。"[:300]
            status = ResultStatus.STOPPED.value
            events = loop_state.get("events", [])
            receipts = loop_state.get("receipts", [])
            trace = loop_state.get("trace", [])
            reasoning = loop_state.get("reasonings", [])
            search_sources = loop_state.get("search_sources", [])
            prerequisite_warnings = loop_state.get("prerequisite_warnings", [])
        except Exception as e:
            log.exception("director_turn 工具循环异常（project=%s session=%s）",
                          project, session_id)
            e_desc = str(e).strip()
            assistant_text = f"[执行失败: {type(e).__name__}: {e_desc}]"[:300]
            status = ResultStatus.FAILED.value
            events = loop_state.get("events", [])
            receipts = loop_state.get("receipts", [])
            trace = loop_state.get("trace", [])
            reasoning = loop_state.get("reasonings", [])
            search_sources = loop_state.get("search_sources", [])
            prerequisite_warnings = loop_state.get("prerequisite_warnings", [])
        # 本轮 tool I/O 规模估算：tool 消息内容 + assistant tool_calls 参数
        finally:
            try:
                for m in messages:
                    if m.get("role") == "tool" and isinstance(m.get("content"), str):
                        tel_tool_io_chars += len(m["content"])
                    for tc in (m.get("tool_calls") or []):
                        tel_tool_io_chars += len(
                            ((tc.get("function") or {}).get("arguments")) or "")
            except Exception:
                pass

    # ---- 会话治理：结构写计数 + Re-grounding / Clean Review 触发 A ----
    # Re-grounding 优先检查（与 Clean Review 共用计数源；16 = 两次 Clean Review 量级）：
    # 主会话 staleness 密度已达实测致幻觉水平且用户还要继续在主会话干活 → 重开校准
    # 会话（同 session_id、历史归档、状态头重新注入），计数器清零。
    # Sub-Agent 计数口径（勿双计）：DiffEngine 子操作不经 tool-loop 账本——
    # proposal.commit 的 receipt 内嵌 executed_ops；扁平化提取（本轮 receipts +
    # 内嵌 executed_ops）后计入既有项目级双计数器（只数成功非幂等 create/delete/
    # move，恰好一次——"proposal.commit" 本身不在 STRUCTURAL_WRITE_OPS）。Clean
    # Review 联动自动成立：commit 计入 structural_since_review → ≥8 触发既有审查。
    review_result = None
    reground_notice = None
    if client is not None:
        try:
            _flat_receipts = list(receipts)
            for _r in receipts:
                _flat_receipts.extend(_r.get("executed_ops") or [])
            gov = session_governance.GovernanceStore(pdir, project)
            gov.record_structural_writes(_flat_receipts)
            # registry：per-scene structural_since_delegation 聚合 + turn 收尾
            # sweep（惰性检测节点③：retire / 孤儿清算；确定性，零 LLM）
            try:
                from src.agents.subagent import registry as _sa_registry
                _sa_store = _sa_registry.SubagentStore(pdir, project)
                _sa_store.add_structural_writes(_flat_receipts)
                _proj_now = ProjectStore(pdir).load()
                _sa_store.sweep([s.scene_id for s in _proj_now.scenes])
            except Exception:
                pass   # registry 无活动时目录不存在等——零副作用，静默
            snap = gov.snapshot()
            if (status is None
                    and snap["structural_since_reground"]
                    >= session_governance.REGROUND_THRESHOLD):
                reground_notice = session_governance.reground_session(
                    store, session_id, pdir, project, last_user_intent=content)
                gov.reset_all()
            elif (status is None
                    and snap["structural_since_review"]
                    >= session_governance.CLEAN_REVIEW_THRESHOLD):
                review_result = await run_clean_review(
                    rt, client, project, trigger="structural_threshold")
                gov.reset_since_review()
        except Exception:
            log.exception("会话治理后处理失败（project=%s）", project)
    if reground_notice:
        assistant_text += "\n\n" + reground_notice
        events.append({"kind": "session_reground",
                       "threshold": session_governance.REGROUND_THRESHOLD,
                       "summary": "结构修改累计触发校准会话重置（历史已归档）"})
    elif review_result is not None:
        assistant_text += (
            f"\n\n---\n【自动连续性审查】（本会话结构修改累计 "
            f"≥{session_governance.CLEAN_REVIEW_THRESHOLD} 次触发；由独立只读审查会话"
            f"完成，{review_result['tool_calls']} 次真实工具读取；只报告不修改，完整"
            f"报告已存 {review_result['report_path']}）\n\n" + review_result["report"])
        events.append({"kind": "continuity_review",
                       "trigger": "structural_threshold",
                       "report_path": review_result["report_path"],
                       "review_session_id": review_result["review_session_id"],
                       "tool_calls": review_result["tool_calls"],
                       "summary": "结构修改累计触发的独立会话连续性审查"})

    if created:
        intent = "shot_create"
    elif updated:
        intent = "shot_update"
    elif deleted:
        intent = "shot_delete"
    elif moved:
        intent = "shot_move"
    else:
        intent = "conversation"

    receipts_meta = []
    generated_assets = []   # 本轮真实生成的分镜视觉资产（canonical 路径，前端窗口渲染）
    for r in receipts:
        receipts_meta.append({
            "operation": r.get("operation"), "ok": bool(r.get("ok")),
            "verified": bool(r.get("verified")), "stale": bool(r.get("stale")),
            "unchanged": bool(r.get("unchanged")),
            "error": r.get("error"),
            "shot_id": r.get("shot_id"),
            "scene_id": r.get("scene_id"),
            "order": r.get("order"), "index": r.get("index"),
            "changed": r.get("changed"),
            "count": r.get("count"),
            "created": r.get("created"),
            "results": r.get("results"),
            "revision": r.get("revision"),
            # stale 时返回的最新状态 / 删除时的清理明细（供上层与测试核对）
            "expected_revision": r.get("expected_revision"),
            "current_revision": r.get("current_revision"),
            "current_shot": r.get("current_shot"),
            "current_shots": r.get("current_shots"),
            "deleted": r.get("deleted"),
            "removed_files": r.get("removed_files"),
            # 视觉生成：分项结果/耗时/取消与超时标注（前端据此展示生成资产）
            "variants": r.get("variants"),
            "elapsed_s": r.get("elapsed_s"),
            "cancelled": r.get("cancelled"),
            "timed_out": r.get("timed_out"),
            # Sub-Agent：DiffEngine 内嵌子操作回执（治理扁平化 / 审计）
            "executed_ops": r.get("executed_ops"),
            "proposal_id": r.get("proposal_id"),
            # Phase C1：生产绑定 / 生产生成回执字段（Producer changed_refs.bindings 与
            # prerequisite warnings 的确定性来源）
            "binding_id": r.get("binding_id"),
            "mode": r.get("mode"),
            "changed_resources": r.get("changed_resources"),
            "prerequisite_warnings": r.get("prerequisite_warnings"),
            "readiness": r.get("readiness"),
            "production_state": r.get("production_state"),
            "reference_policy": r.get("reference_policy"),
        })
        if r.get("operation") in SHOT_GENERATE_TOOL_NAMES and r.get("ok"):
            for item in (r.get("results") or []):
                if item.get("ok") and item.get("local"):
                    generated_assets.append({
                        "shot_id": item.get("shot_id"),
                        "kind": item.get("frame") or "first_frame",
                        "path": item.get("local"),
                    })
    meta = {"intent": intent, "events": events, "evidence_refs": [],
            "receipts": receipts_meta,
            "write_attempts": len(receipts_meta),
            "write_success": sum(1 for r in receipts_meta
                                 if r["ok"] and r["verified"] and not r["unchanged"]),
            "trace": trace,
            "reasonings": reasoning,
            "search_sources": search_sources,
            "generated_assets": generated_assets,   # 本轮真实生成的分镜图/视频（路径，窗口渲染用）
            "prerequisite_warnings": prerequisite_warnings,   # Phase C1：生产前置（turn 级聚合）
            # Skill System V1（40.md §十三）：本 turn 技能激活/加载/资源读取证据
            "skills": _get_skill_runtime().collect_and_reset("director"),
            "pending_delete_scenes": []}
    asst = AgentMessage(role="assistant", content=assistant_text, status=status, meta=meta)
    store.append_message(session_id, asst)

    # Compaction 遥测：每 turn 落盘一行上下文规模（project/agent/session/turn_id/
    # history_chars/system_chars/tool_io_chars/时间戳）。将来若真出现长上下文证据，
    # compaction 的阈值与设计有真实数据可用（实验否决当前做压缩本体）。
    session_governance.log_context_telemetry(
        pdir, project, "director", session_id, user_msg.message_id,
        history_chars=tel_history_chars, system_chars=tel_system_chars,
        tool_io_chars=tel_tool_io_chars)

    return {"assistant_message": assistant_text,
            "created": created, "updated": updated, "deleted": deleted, "moved": moved,
            "prerequisite_warnings": prerequisite_warnings,
            "events": events, "message": asst.to_dict(),
            "intent": intent, "receipts": receipts_meta, "trace": trace,
            "search_sources": search_sources,
            "evidence_refs": [], "linked_task_ids": [],
            "linked_change_session_id": None, "session_id": session_id,
            "agent_id": "director"}


