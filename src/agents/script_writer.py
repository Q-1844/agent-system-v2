# src/agents/script_writer.py
# Script Writer 正式 Agent（原生 function-calling tool-loop）。
#
# 职责：剧本正文（story_text）+ 故事段结构（Scene 的增/插/排序）。
#       故事段之下的镜头（Shot）由 Director 负责，SW 不创建/修改镜头。
#
# 工具（按需读取，不把完整 story_text/scenes 长期塞进 system）：
#   读：script.read / story.list / story.read
#   写：script.update / story.create / story.move / story.update / story.delete
#   批量写（各占一个单写槽，整批一次 OCC + 一次原子写）：
#        story.create_many / story.update_many / story.insert_many / story.delete_many
# 循环：LLM → tool_calls → 执行 → observation 喂回 → LLM … → 无 tool_call 即最终回复。
#
# 三层薄约束（对齐 OpenAI Agents SDK tool guardrail，不替 Agent 规划）：
#   工具前：权限（仅 script_writer）+ 参数校验 + 目标（项目存在）。
#   工具执行：乐观并发校验 → 中性层结构操作 → 原子写 canonical project.json + audit。
#   工具后：返回真实结果/错误，让 Agent 自己判断是否继续。
#
# 乐观并发（optimistic concurrency，工具自身正确性保护，非协议仪式）：
#   两个独立的内容指纹令牌，字段级作用域（模块边界）：
#   - story_text 指纹：script.update 校验 expected_revision。
#   - scenes 结构指纹（sha256 over scene_id 有序列表 前 8 位）：story.create / story.move
#     校验 expected_scenes_revision。增/删/重排会变；段内 summary/characters 改动不影响。
#   令牌由 read 工具和写工具的成功 observation 返回；system 不长期注入完整内容。
#   stale → 拒绝写入（不落盘/不分配 ID/不审计），把最新版 + 新指纹返回 Agent 重新判断。
#   每轮工具调用后无条件刷新 system（项目元信息/计数）。
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any, Optional

import httpx

from src.api.tavily_hub_client import TavilyHubClient
from src.core.store import ProjectStore
from src.storyboard.structure_service import (
    StructureError, insert_scenes, insert_scenes_at_anchors, move_scene,
)

from .models import AgentMessage, ResultStatus
from .runtime import AgentRuntime, AgentError
from .revision_audit import DirectEditAuditStore
# —— Skill System V1（40.md）：生产技能工具面（只读，不占写槽）——
from .skill_system.tools import (
    SKILL_TOOLS,
    activation_hint as _skill_activation_hint,
    make_skill_executors,
)
from .skill_system.runtime import get_skill_runtime as _get_skill_runtime

log = logging.getLogger("agents.script_writer")

# AI-7D-R 超时恢复（长批量生成）：单次 LLM read timeout=360s（agnes_client），
# 超时后不原样重发大请求，而是降级当前 turn 的推荐 batch 大小，用 user nudge
# 反馈结构化恢复信息，让 Agent 以更小批次继续剩余任务。最多恢复 2 次。
MAX_TIMEOUT_RETRIES = 2
_BATCH_DOWNGRADE = {5: 2, 2: 1, 1: 1}   # 5 → 2 → 1（到 1 不再降）

# Web Search（第一版，Tavily Hub）：每个 user turn 硬上限 3 次真实搜索。
# Runtime 计数（loop-local，turn 结束随循环销毁自动重置），不依赖 prompt 自觉。
# 超限直接拒绝（不调 Provider），返回结构化 tool error 让 Agent 用已有信息收尾。
MAX_WEB_SEARCH = 3

# web.search Provider 客户端（进程级单例，无状态；测试用 _get_web_search_client 替换）。
_WEB_SEARCH_CLIENT: Optional[TavilyHubClient] = None


def _get_web_search_client() -> TavilyHubClient:
    global _WEB_SEARCH_CLIENT
    if _WEB_SEARCH_CLIENT is None:
        _WEB_SEARCH_CLIENT = TavilyHubClient()
    return _WEB_SEARCH_CLIENT


class _TurnStopped(Exception):
    """AI-7D-R：turn 被协作式取消（用户点停止）。在工具循环【步骤边界】检测到
    cancel_event 后抛出——当前正在进行的单次 LLM 调用/原子写不会被中途打断，
    已完成的写全部保留，未完成部分不执行。由 script_writer_turn 捕获并诚实收尾。"""
    pass


def _committed_scene_ids(receipts: list) -> list:
    """从已成功落盘的写回执里提取 scene_id 列表（checkpoint，供超时恢复 nudge
    告知 Agent 已提交项）。覆盖单写与批量工具的 obs 结构差异。"""
    out = []
    for r in receipts:
        if not (r.get("ok") and r.get("verified") and not r.get("unchanged")):
            continue
        op = r.get("operation")
        if op in ("story.create", "story.update", "story.move"):
            sid = r.get("scene_id")
            if sid and sid not in out:
                out.append(sid)
        elif op == "story.create_many":
            for c in (r.get("created") or []):
                sid = c.get("scene_id")
                if sid and sid not in out:
                    out.append(sid)
        elif op == "story.insert_many":
            for c in (r.get("inserted") or []):
                sid = c.get("scene_id")
                if sid and sid not in out:
                    out.append(sid)
        elif op == "story.update_many":
            for c in (r.get("results") or []):
                sid = c.get("scene_id")
                if sid and sid not in out:
                    out.append(sid)
        elif op == "story.delete_many":
            for c in (r.get("deleted") or []):
                sid = c.get("scene_id")
                if sid and sid not in out:
                    out.append(sid)
    return out


# 执行窗口大小（renewable window）：单次窗口用满后注入 self-check nudge 并续接
# 新窗口（次数不限），由 Agent 自行判断继续/停止——不再硬停长任务。仅防单次窗口
# 内的失控；24h 级连续生产由 renewable window + self-check 承载，非业务数量限制。
DEFAULT_MAX_TOOL_STEPS = 1024
# AI-7D-R 真实性收口：最终回复纠偏最多 1 次；仍不实则 Runtime 确定性覆写（非提示词保证）。
MAX_CORRECTIVE_ROUNDS = 1
# 防失控（需求 #11）：连续同一工具同一失败签名达此阈值 → 诚实停止重复，要求换思路。
MAX_REPEAT_FAIL = 3


def _trace_entry(step: int, name: str, args: str, obs: dict) -> dict:
    """执行轨迹单条（需求 #7）：turn→tool→target→result→revision。
    target 取最能标识操作对象的字段（scene_id/shot_id/story_text）；无则 None。
    result 用 ok/error/stale/unchanged 分类。revision 取回执里的结构或内容指纹。"""
    target = (obs.get("scene_id") or obs.get("shot_id")
              or (obs.get("target") if obs.get("ok") else None))
    if name == "script.update":
        target = "story_text"
    if name == "web.search":
        target = obs.get("target")   # 搜索词（executor 已截断）；成功/失败都展示，不含 snippet
    result = ("stale" if obs.get("stale")
              else "unchanged" if obs.get("unchanged")
              else "ok" if obs.get("ok")
              else "error")
    return {
        "step": step, "tool": name, "target": target, "result": result,
        "revision": obs.get("revision") or obs.get("scenes_revision"),
        "error": (None if result in ("ok", "unchanged")
                  else (obs.get("error") or obs.get("code"))),
        "ok": bool(obs.get("ok")),
    }

# AI-7D-R 流式 UI：工具事件的确定性人类可读 label / summary（复用 _op_human，不让前端
# 重新解释 JSON）。只读工具给通用描述；写工具用 _op_human 的真实回执描述。
_TOOL_LABELS = {
    "script.read": "读取剧本", "script.update": "更新剧本",
    "story.list": "读取故事段", "story.read": "读取故事段",
    "story.create": "创建故事段", "story.move": "移动故事段",
    "story.update": "修改故事段", "story.delete": "删除故事段",
    "story.create_many": "批量创建故事段", "story.update_many": "批量修改故事段",
    "story.delete_many": "批量删除故事段", "story.insert_many": "批量插入故事段",
    "web.search": "搜索资料",
}


def _tool_label(name: str) -> str:
    return _TOOL_LABELS.get(name, name)


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


# 写工具集合（只有这些可能产生 canonical 变更，需要回执 + 落盘校验）。
# 批量工具（create_many/update_many/delete_many/insert_many）整体各占一个单写槽。
WRITE_OPERATIONS = {"script.update", "story.create", "story.move",
                    "story.update", "story.delete",
                    "story.create_many", "story.update_many",
                    "story.delete_many", "story.insert_many"}


def _story_token(text: str) -> str:
    """story_text 的内容指纹（sha256 前 8 位）。空串也有稳定指纹。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]


def _scenes_token(scenes) -> str:
    """故事段集合的结构指纹（sha256 前 8 位）。哈希对象 = scene_id 的有序列表：
    增/删/重排会变；story_text 编辑、段内 summary/characters 改动不影响（字段级作用域）。"""
    payload = json.dumps([s.scene_id for s in (scenes or [])], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


# story.update 允许修改的故事段【自身内容】字段白名单。
# 明确排除：scene_id（身份）、shots（分镜，归 Director）。
# 项目级 story_text 归 script.update，不在这里。
SCENE_UPDATABLE_FIELDS = ("title", "location", "time_of_day", "summary", "characters")


def _scene_token(scene) -> str:
    """单个故事段的内容指纹（sha256 前 8 位）。哈希对象 = 该段【可变内容字段】的有序映射：
    title/location/time_of_day/summary/characters。段内这些内容改动会变；
    scene_id、段内 shots、以及其它段的改动都不影响（字段级/段级作用域）。
    与 _scenes_token（集合结构指纹）相互独立——段内内容修改不误触结构令牌。"""
    if scene is None:
        return hashlib.sha256(b"").hexdigest()[:8]
    payload = json.dumps(
        {k: (list(getattr(scene, k, []) or []) if k == "characters"
             else (getattr(scene, k, "") or ""))
         for k in SCENE_UPDATABLE_FIELDS},
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _scenes_content_token(scenes) -> str:
    """所有故事段【可变内容字段】的全局指纹（sha256 前 8 位）。任一段的段内内容
    (summary/title/location/time_of_day/characters) 改动都会变；与 _scenes_token（结构
    指纹，只看 scene_id 列表）相互独立。供 story.update_many 一次性校验多段内容基线——
    模型从 story.list 拿 scenes_content_revision 回传。增/删/移动也会变（结构变了内容序列
    也变），但段内内容改动只在本令牌体现、不影响 _scenes_token。"""
    payload = json.dumps(
        [{k: (list(getattr(s, k, []) or []) if k == "characters"
              else (getattr(s, k, "") or ""))
          for k in SCENE_UPDATABLE_FIELDS}
         for s in (scenes or [])],
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _build_system(proj) -> str:
    """精简 system：只放职责、权限、工具说明、项目元信息（名称/字数/段数）。
    不注入完整 story_text 或 scenes 列表——Agent 需要时用 read/list 工具按需取。"""
    base = SCRIPT_WRITER_SYSTEM.format(
        project_name=proj.name or "（未命名）",
        story_len=len(proj.story_text or ""),
        scenes_count=len(proj.scenes or []),
    )
    # Skill System V1（40.md §七）：L1 catalog 常驻尾部（只看到自己的 Skill view）
    base += _get_skill_runtime().catalog_block("script_writer")
    return base


SCRIPT_WRITER_SYSTEM = """你是 AGNES 的 Script Writer，负责项目的【剧本正文】与【故事段结构】（故事段 Scene 的增删与排序）。
故事段之下的镜头（Shot）由 Director 负责，你不创建或修改镜头。

项目：{project_name}（剧本正文 {story_len} 字，{scenes_count} 个故事段）。

你有这些工具：
- script.read()：读取【剧本正文全文】+ 剧本版本指纹。仅在这些场景调用：①要 script.update 修改/续写/重写正文前；②整体节奏/全篇逻辑/人物弧光等全局分析；③用户明确要求通读全文。【故事段（Scene）的新增/插入/续写/重写/段内修改/删除/排序都不需要读全文】。
- script.update(new_story_text, expected_revision)：用【完整】新正文覆盖剧本正文。
- story.list()：故事段任务的【第一步】——轻量全局地图：列出所有故事段（position/id/120字摘要/角色/镜头数）+ 结构指纹与内容指纹。不返回正文全文，开销很小。
- story.read(scene_id)：读取一个已有故事段的完整内容（标题/地点/时段/摘要全文/角色/镜头数）+ 该段【内容指纹】。局部任务读目标段 + 前后相邻约 2~4 段即可；信息不足再继续扩大读取范围。
- story.create(summary, characters?, before_scene_id?, after_scene_id?, expected_scenes_revision)：创建一个故事段并插到指定位置；省略 before/after 即追加末尾。id 由后端分配，你不能指定 id 或数字序号。【要一次创建多段时优先用 story.create_many，不要连续多次调 story.create】
- story.create_many(scenes[], before_scene_id?, after_scene_id?, expected_scenes_revision)：一次创建多个故事段，scenes 数组顺序即创建后相对顺序；整批一次乐观并发+一次原子写入，全部合法才写。一次创建 ≥2 段时【优先】用本工具。
- story.insert_many(inserts[], expected_scenes_revision)：一次在【多个不同锚点】前/后插入新故事段。inserts 每项含 before_scene_id 或 after_scene_id（二选一）+ scenes[]（该锚点处连续插入 1 段或多段，数组顺序即相对顺序）。先 story.list 把用户说的「第N段」解析成稳定 scene_id 锚点再调用；所有锚点按调用前快照解释，后端不会按插入后的新位置解释后续锚点。同一批每个锚点只能出现一次（同一锚点插多段就合并到该项 scenes 里）。整批一次乐观并发+一次原子写入，任一非法整批不写。【只在同一个锚点附近插多段用 story.create_many；同一请求有多个不同插入位置才用本工具】。
- story.update(scene_id, expected_revision, summary?, title?, location?, time_of_day?, characters?)：修改一个【已有】故事段的自身内容。只传要改的字段；必须先 story.read 拿到该段 expected_revision 原样回传。不能改 scene_id、不能改镜头、不能改项目剧本正文。
- story.update_many(updates[], expected_revision)：一次修改多个【互相独立、已存在】的故事段。每项 scene_id 必填+要改的字段，同段多字段合并到一项、同 scene_id 一批只出现一次。expected_revision 用 story.list 返回的 scenes_content_revision（内容指纹，非结构 revision）。一次修改 ≥2 段独立内容时【优先】用本工具；但若修改存在前后依赖（后一个依赖前一个改后的状态），仍逐步 story.update。
- story.delete(scene_id)：删除一个已有故事段（会连带删掉该段下的镜头和生成资产）。用户明确要求删除某段时，直接调用本工具删除即可——不需要先预览再问用户确认。删完后用 story.list 核对真实结果并向用户总结删除了什么、当前剩余几段。
- story.delete_many(scene_ids[], expected_scenes_revision)：一次删除多个故事段（用户一次要求删 ≥2 段时【优先】用本工具，不要连续多次调 story.delete）。只接受稳定 scene_id、不接受 position 数字：先 story.list 把用户说的「第N段」全部解析成 scene_id，再一次传入。整批一次乐观并发+一次原子级联删除（含各段镜头与生成资产），任一 scene_id 不存在/重复/revision 过期则整批不删。删完后用 story.list 核对真实结果并总结。
- story.move(scene_id, before_scene_id?, after_scene_id?, expected_scenes_revision)：把已有故事段移动到某段之前/之后；省略 before/after 即移到末尾。
- web.search(query)：搜索现实世界资料，返回约 3 条「标题+摘要+链接」。仅当用户明确要求搜索，或正在创作且现实世界知识明显影响剧本可信度（历史时期、医疗、法律/刑侦、军事、职业流程、城市/地域、文化习俗、真实科技、新闻/现实背景）时使用。普通润色、情绪、对白语气、结构调整、纯虚构创作不搜索。每轮最多 3 次，超限会被系统拒绝。

🔴 最重要的铁律（违反即失败）：
- 你只有通过【真正发起工具调用】（function/tool_calls）才能读/写数据。在回复里用自然语言说「我已创建/已移动/已插入/切换成功」但没有真正调用对应工具，等于什么都没做——这是严重错误。
- 要创建就必须真正调用 story.create（多段用 story.create_many / story.insert_many）；要移动就必须真正调用 story.move；要修改故事段内容就必须真正调用 story.update（多段用 story.update_many）；要删除就必须真正调用 story.delete（多段用 story.delete_many）；要改正文就必须真正调用 script.update；要知道当前有哪些段/正文内容就必须真正调用 story.list / script.read。
- 工具调用成功后，工具会返回结果（含 ok、scene_id、order、最新版本指纹等）。你只能依据工具【真实返回】的结果向用户汇报；没看到工具返回的 ok=true，就不能声称成功。
- 不要编造或猜测版本指纹、scene_id、段数；这些值必须来自 story.list / story.read / script.read / 工具返回值。
- 如果你不确定当前状态，用读取工具确认再决定，不要凭记忆或假设操作：不确定【有哪些段/位置/id/段数】→ story.list；不确定【某段的具体内容】→ story.read(scene_id)；只有操作【剧本正文】或做全篇分析时才 script.read。

【聊天与探讨（用户没让你改东西时）】
- 用户在提问、征求看法、讨论方案、解释原因、回顾你做过什么时（如「你觉得这里有什么问题」「为什么你刚才这么改」「这个角色这样设计合适吗」「如果后面加个转折怎么样」「你觉得这几段节奏快不快」「刚才为什么没成功」「先不要修改，我们讨论一下」）：直接用自然语言回答，长度按问题自然决定。
- 这类轮次【不调用写工具】；讨论中提到的改动想法不要自动执行，除非用户明确提出修改要求。也【不要】输出「我没有真正执行修改/请再说一次你的需求」之类的话——用户本来就没让你改。
- 回答需要事实依据时（用户问到具体某几段的内容、当前段数、最新状态，而这些不在你的上下文里），才按需调用只读工具 story.list / story.read 取事实，然后继续讨论；凭当前上下文就能回答的就直接回答。
- 聊天/讨论轮同样【不自动搜索】：用户问到现实世界事实而你缺乏可靠知识时（如「你觉得这个1940年代上海场景真实吗」），可以先 web.search 再回答；上下文或常识足够就直接回答。
- 不要在讨论中复述完整剧本正文或整段故事段内容；必要时引用一两句即可。

【回复规范（只汇报本轮改动）】
- 执行完写工具后，回复里【只汇报本轮真实执行的操作】：改了哪段、改了哪个字段、新增/删除/移动了什么。一两句即可，必要时简述你这样做的理由（让用户理解思路）。
- 【不要】复述之前轮次已经做过的历史改动。即使会话里之前改过很多段，本轮只动了一段，就只说这一段。用户若想回顾全部历史改动，会明确问（如"总结一下你都做了哪些修改"）——那时再汇总。
- 不要用表格/清单罗列会话里所有历史改动当成"本轮成果"；【实际执行记录】事实块已经只列本轮真实变更，你的正文也只描述本轮。
- 回复保持简短：单字段/单段小修改一两句话；多段批量任务简述每段改了什么即可，不展开全文。

【读取策略：最小充分上下文，按需扩大】
- 故事段任务（新增/插入/续写/重写某段/修改段内容/局部剧情调整/删除/移动/排序）【不要一上来就 script.read 全文】——故事段与剧本正文是两份独立内容，段的增删改不依赖正文全文：
  1) 先 story.list() 拿轻量全局地图（position/scene_id/摘要/角色/指纹）；
  2) 再 story.read 只读任务相关的局部段落：目标段本身 + 前后相邻约 2~4 段（末尾续写/新增 → 读结尾附近 2~4 段；中间插入 → 读锚点前后 2~4 段；重写某段 → 读该段 + 前后各 1~2 段看衔接）；
  3) 局部读完先判断上下文是否充分：人物动机来自更早剧情、伏笔在更前面、因果无法判断、或用户要求更强一致性时，继续 story.read 扩大范围；
  4) 多次局部读取仍不够、或任务本身是全局性的（整体节奏分析、全篇逻辑一致性检查、人物完整弧光、整篇重构、用户明确要求通读全文），才 script.read 读全文。
- 只有操作【剧本正文本身】（story_text：修改/扩写/重写/压缩正文）才走 script.read() 拿正文和 expected_revision，再 script.update 传【完整】新正文 + 指纹。
- 注意区分：「续写一个故事段」「末尾再加一段」「续一段收尾」是在故事段列表里新增 Scene，属于上面的故事段任务——读结尾附近 2~4 段即可，不要因为"续写"两个字就 script.read 全文。即使是收尾段，人物归宿、伏笔回收、结尾节奏也都在最后几段里；先读局部，确实不够再扩大，最后才读全文。

工作方式：
1. 新建故事段 → 先 story.list() 拿到现有段和 expected_scenes_revision，确定插入位置（用某个已有段的 scene_id 作 before/after 锚点，或省略追加末尾）；按【读取策略】读结尾/锚点附近 2~4 段找承接。创建 1 段用 story.create；在【同一个锚点】一次创建 ≥2 段（或末尾批量追加）用 story.create_many；要在【多个不同位置】分别插入（如「第2段后插一段、第5段前插两段」）→ 先用一次 story.list 把所有「第N段」解析成锚点 scene_id，再一次 story.insert_many（inserts 每项一个锚点+scenes 数组），不要插一次 list 一次地循环。
2. 调整故事段顺序 → 先 story.list() 拿指纹，再 story.move（用 scene_id 指定移动谁、用 before/after_scene_id 指定位置，不要自己算数字序号）。
3. 修改已有故事段的摘要/标题/地点/时段/角色 → 改 1 段：先 story.read(scene_id) 拿该段内容和 expected_revision（需要衔接时按【读取策略】读相邻段），再 story.update 只传要改的字段+指纹。改 ≥2 段且互相独立：先 story.list 拿 scenes_content_revision，再 story.update_many（每项 scene_id+要改字段，同段多字段合并到一项）。若修改存在前后依赖仍逐步 story.update。
4. 删除故事段 → 用户明确要删就直接删（不需要先预览问确认）。删 1 段用 story.delete(scene_id)；一次删 ≥2 段：先用一次 story.list 把所有「第N段」解析成 scene_id，再一次 story.delete_many（整批一起，不要删一个 list 一次地循环），删完用 story.list 核对并据事实总结。
5. 任一写工具返回 stale=true → 你读取后内容被改动过、未写入；用返回的最新版和刷新后的指纹重新判断，再用新指纹调用。

【自主执行与询问】
1. 默认自主完成用户要求。只要能根据用户表达、上下文或当前故事状态合理确定意图，就直接执行，不要为了追求完全明确而询问用户。
2. 如果只是轻微歧义，选择最符合上下文和用户目标的合理解释继续执行，不要停下来询问。
3. 如果不确定的是当前故事状态、段落位置或已有内容，优先使用读取工具确认（段的情况用 story.list / story.read，按【读取策略】来；只有涉及剧本正文或全篇分析才 script.read），而不是询问用户本可由工具获得的信息。
4. 只有当存在两个或以上明显合理的理解，并且不同理解会导致明显不同的修改结果，同时又无法通过读取当前故事消除歧义时，才暂停并询问用户。
5. 询问时只询问完成任务所缺少的最关键一点，不要连续追问，也不要要求用户提供不必要的细节。

【写工具必须一个一个来】（重要）：
- 写工具（script.update / story.create / story.update / story.move / story.delete，及批量工具 story.create_many / story.update_many / story.insert_many / story.delete_many）必须【一个一个执行】：每发一个写工具调用，等它返回最新状态/revision 后，再发下一个。不要在同一次回复里批量发多个写工具——它们会用旧 revision 导致 stale。
- 批量工具本身【一次调用就完成整批】（内部一次乐观并发+一次原子写入），它整体只占一个写槽：要删多段就把 scene_ids 一次放进 story.delete_many，要在多个位置插入就把 inserts 一次放进 story.insert_many——不要把一批拆成多个批量工具调用，也不要在同一次回复里混发多个写工具。
- 只读工具（story.list / script.read / story.read / web.search）可以一次发多个，它们不碰数据、不冲突。
- 这条是硬约束：同一次回复里发了多个写工具时，系统只执行第1个，其余会被拒绝。

【复杂操作要分解成多步】（按场景）：
- 互换两条故事段（如"第1条和最后一条互换"）：不能一步完成。分两步 story.move：先 move 一条到目标位置，再【用最新 revision】move 另一条。每步看结果再继续。
- 批量重排（如"把这几条按某顺序排"）：多次 story.move，每次一个、用最新 revision。
- 批量改内容（如"把这3段的摘要都改一下"）：互相独立的多段内容修改用一次 story.list 拿 scenes_content_revision 后调 story.update_many；存在前后依赖时仍逐段 story.read→story.update。
- 批量删（如"删掉第1、5、7、9段"）：先一次 story.list 把这些 position 全部解析成 scene_id，再【一次】story.delete_many（整批一次 revision 校验+原子级联删）。不要删一个再 list 再删——delete_many 就是为消除这个循环。
- 多点插入（如"第2段后插A、第5段前插B和C、第8段后插D"）：先一次 story.list 把第2/5/8段解析成锚点 scene_id，再【一次】story.insert_many（inserts 三项，锚点都按这次 list 的快照解释）。不要插一个再 list 再插。仅同一锚点连续插多段时用 story.create_many。
- 用户说的「第N条」= story.list 返回里 position=N 的那条；找到它的 scene_id 再操作，不要用 scene_id 的数字猜位置（删除后 id 会跳号，但 position 始终连续）。

【资料搜索（web.search）】
- 搜索是辅助研究能力，不是每次对话的默认步骤：已有上下文足够回答或创作时，不要搜索。
- 允许主动搜索的两种情况：①用户明确要求（搜一下/查一下/联网看看/找资料/根据真实资料写）；②正在实际创作且现实世界知识明显影响可信度（历史时期、医疗、法律/刑侦、军事、职业流程、城市/地域、文化习俗、真实科技、新闻/现代现实背景）。
- 每轮最多 3 次；每次查询保持简洁、聚焦一个具体问题，复杂问题拆成最多 3 次独立查询，不要塞超长查询词。
- 搜索失败（超时/限流/无结果）时可换一个更聚焦的查询词再试一次，或改用已有知识继续，或如实告知用户没有搜索到足够资料；不要反复重试同一查询。
- 搜索结果只是参考素材：由你分析消化后自己创作，不要照抄网页正文；正文必须是你自己的创作。

【整篇剧本拆分成故事段】（连续创建，不生成计划、不经审查流）：
a. 先 script.read() 读完整剧本正文，理解故事脉络。
b. 再 story.list() 读已有故事段——【不要假设当前为空】。已有段一律保留，本阶段不擅自 delete/update/move 已有段。
c. 从剧本正文里一段一段地 story.create 追加（summary=该段故事文本，characters=出场角色）。
   每一次 story.create 成功后，工具会返回【新的 scenes_revision】；下一次 story.create 必须用这个新返回的 revision 作为 expected_scenes_revision，不要复用上上次的旧指纹，否则会 stale。
   id 由后端顺序分配（s01、s02…），你不要自己指定 id 或试图跳号。
d. 每创建一段都看工具真实返回的 ok=true 与分配的 scene_id，确认落盘后再继续下一段。
e. 全部创建完后，最后再 story.list() 读一次真实当前列表。
f. 最终回复里，故事段的数量、id、顺序必须以【最后这次 story.list 的真实返回】为准，不要凭自己之前写的文字记忆声称"已拆分完成 N 段"。
- 如果中途某次 create 返回 stale 或 error：用返回的最新列表/指纹重新判断，【不要重复创建已经成功的那一段】（已成功的段在 canonical 里真实存在，story.list 能看到）。
- 本阶段拆分任务中，专注连续 story.create；update/delete/move 虽可用，但不要在拆分过程中自主混用，除非用户另外明确要求。

要求：
- new_story_text / summary 必须是中文正文本身，不要 markdown 标题、解释或代码块。
- characters 是出场角色名数组（来自当前项目角色，可空）。
- 版本指纹必须原样回传 read/list/写工具返回值里的 8 位十六进制串，不要编造。
- before_scene_id 与 after_scene_id 不能同时给。
- 一次只做用户要求的修改，不顺手改无关内容。
"""


# ============ 工具 schema（标准 OpenAI function 格式）============
SCRIPT_UPDATE_TOOL = {
    "type": "function",
    "function": {
        "name": "script.update",
        "description": "用新的完整剧本正文覆盖当前剧本正文。用于修改、续写、扩写、重写、压缩剧本。",
        "parameters": {
            "type": "object",
            "properties": {
                "new_story_text": {
                    "type": "string",
                    "description": "修改后的【完整】剧本正文（中文正文，不要 markdown/解释/代码块）。",
                },
                "expected_revision": {
                    "type": "string",
                    "description": "你最近一次 script.read 看到的剧本版本指纹（8 位十六进制）。用于乐观并发：若期间剧本被改动，写入会被拒绝并返回最新版。",
                },
            },
            "required": ["new_story_text", "expected_revision"],
        },
    },
}

SCRIPT_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "script.read",
        "description": ("读取当前剧本正文全文及其版本指纹。【仅在这些情况调用】①调用 script.update "
                        "修改/续写/重写正文前；②整体节奏、全篇逻辑、人物弧光等全局分析；③用户明确要求"
                        "通读全文。故事段（Scene）的新增/插入/续写/重写/段内修改/删除/排序【不需要】"
                        "读本工具——那些先 story.list 拿地图、再按需 story.read 局部段落即可。"),
        "parameters": {"type": "object", "properties": {}},
    },
}

STORY_CREATE_TOOL = {
    "type": "function",
    "function": {
        "name": "story.create",
        "description": ("创建一个新的故事段（Scene），插到 before_scene_id 之前或 after_scene_id 之后"
                        "（都省略则追加末尾）。id 与最终顺序由后端分配，你不能指定。【创建前的读取】"
                        "先 story.list 定位，再 story.read 读插入点/结尾附近 2~4 段获取承接上下文即可，"
                        "不要先 script.read 全文——局部信息不够再扩大，最后才考虑全文（全局性任务除外）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "该故事段的故事文本（中文正文，不要 markdown/解释/代码块）。",
                },
                "characters": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "出场角色名数组（来自当前项目角色，可空）。",
                },
                "before_scene_id": {
                    "type": "string",
                    "description": "插到该故事段之前（可选，与 after_scene_id 二选一）。",
                },
                "after_scene_id": {
                    "type": "string",
                    "description": "插到该故事段之后（可选，与 before_scene_id 二选一）。省略且无 before 则追加末尾。",
                },
                "expected_scenes_revision": {
                    "type": "string",
                    "description": "你最近一次 story.list 看到的故事段版本指纹（8 位十六进制）。用于乐观并发。",
                },
            },
            "required": ["summary", "expected_scenes_revision"],
        },
    },
}

STORY_LIST_TOOL = {
    "type": "function",
    "function": {
        "name": "story.list",
        "description": ("故事段相关任务（新增/插入/续写/重写/段内修改/删除/移动/局部调整）的【第一步】："
                        "列出所有故事段，每段含 position（第几条，1-based）、scene_id（身份ID）、摘要"
                        "（前120字）、出场角色、镜头数，及结构版本指纹与内容指纹。这是轻量全局地图，"
                        "不返回正文全文。用户说「第N条」时，position=N 对应的就是那条的 scene_id——"
                        "删除后 scene_id 可能跳号但 position 始终连续，用 position 定位不要用 scene_id "
                        "的数字。拿到地图后按需 story.read 局部段落，不要默认 script.read 全文。"),
        "parameters": {"type": "object", "properties": {}},
    },
}

STORY_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "story.read",
        "description": ("读取一个故事段的完整内容（摘要全文、标题/地点/时段、出场角色、镜头数）及该段"
                        "内容指纹。局部创作/修改时，读目标段本身 + 前后相邻约 2~4 段获取衔接上下文即可；"
                        "若伏笔/动机涉及更早剧情，再继续 story.read 扩大范围，不要直接 script.read 全文。"),
        "parameters": {
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "要读取的故事段 id（如 s01）。"},
            },
            "required": ["scene_id"],
        },
    },
}

STORY_MOVE_TOOL = {
    "type": "function",
    "function": {
        "name": "story.move",
        "description": "把一个已存在的故事段移动到某段之前或之后（都省略则移到末尾）。用稳定 scene_id 指定，不改变任何 id，不影响段内镜头。",
        "parameters": {
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "要移动的故事段 id。"},
                "before_scene_id": {
                    "type": "string",
                    "description": "移到该故事段之前（可选，与 after_scene_id 二选一）。",
                },
                "after_scene_id": {
                    "type": "string",
                    "description": "移到该故事段之后（可选，与 before_scene_id 二选一）。省略且无 before 则移到末尾。",
                },
                "expected_scenes_revision": {
                    "type": "string",
                    "description": "你最近一次 story.list 看到的故事段版本指纹（8 位十六进制）。用于乐观并发。",
                },
            },
            "required": ["scene_id", "expected_scenes_revision"],
        },
    },
}

STORY_UPDATE_TOOL = {
    "type": "function",
    "function": {
        "name": "story.update",
        "description": (
            "修改一个【已存在】故事段的自身内容字段（summary/title/location/time_of_day/"
            "characters 中的任意一个或多个，只传要改的字段）。必须先 story.read(scene_id) 拿"
            "到该段内容指纹 expected_revision 并原样回传。不能改 scene_id、不能改分镜(shots)、"
            "不能改项目剧本正文。只传不改动的字段不会有副作用，但不要传 scene_id/shots 等禁止字段。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "要修改的故事段 id（如 s02）。"},
                "expected_revision": {
                    "type": "string",
                    "description": "你最近一次 story.read(scene_id) 看到的该段内容指纹（8 位十六进制）。用于乐观并发：期间该段被改动会拒绝写入并返回最新内容。",
                },
                "summary": {"type": "string", "description": "新的故事文本（中文正文，不要 markdown/解释/代码块）。"},
                "title": {"type": "string", "description": "段标题（可选）。"},
                "location": {"type": "string", "description": "地点（可选）。"},
                "time_of_day": {"type": "string", "description": "时段，如 日/夜/清晨（可选）。"},
                "characters": {
                    "type": "array", "items": {"type": "string"},
                    "description": "新的出场角色名数组（来自当前项目角色）。这是整体覆盖而非追加。",
                },
            },
            "required": ["scene_id", "expected_revision"],
        },
    },
}

STORY_DELETE_TOOL = {
    "type": "function",
    "function": {
        "name": "story.delete",
        "description": (
            "删除一个【已存在】的故事段（会连带删掉该段下的镜头和生成资产）。用户明确要求"
            "删除某段时，直接传 scene_id 调用本工具执行删除即可——不需要先预览再问用户确认。"
            "删完后用 story.list 核对真实结果。删除是级联的，会移除整个故事段及其镜头。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "要删除的故事段 id（如 s03）。"},
                "confirmed": {
                    "type": "boolean",
                    "description": "可选。省略或 true=直接执行级联删除（默认）；false=仅返回删除预览不删（一般不需要）。",
                },
            },
            "required": ["scene_id"],
        },
    },
}

STORY_CREATE_MANY_TOOL = {
    "type": "function",
    "function": {
        "name": "story.create_many",
        "description": (
            "一次创建多个故事段（同一请求里要新建 ≥2 段时优先用本工具，不要连续多次调 story.create）。"
            "scenes 数组顺序 = 创建后的相对顺序。整批只做一次乐观并发校验、一次原子写入，全部参数"
            "合法才执行，任一非法则整批不写入（不留半成功）。id 与最终顺序由后端分配，你不能指定。"
            "省略 before_scene_id/after_scene_id 则全部追加末尾。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scenes": {
                    "type": "array",
                    "minItems": 2,
                    "description": "要创建的故事段数组（至少 2 项；只创建 1 段请用 story.create），顺序即创建后相对顺序。每项含 summary（必填，中文正文）与 characters（可选，角色名数组）。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string", "description": "该段故事文本（中文正文，不要 markdown/解释/代码块）。"},
                            "characters": {"type": "array", "items": {"type": "string"}, "description": "出场角色名数组（可空）。"},
                        },
                        "required": ["summary"],
                    },
                },
                "before_scene_id": {"type": "string", "description": "整批插到该段之前（可选，与 after 二选一）。"},
                "after_scene_id": {"type": "string", "description": "整批插到该段之后（可选，与 before 二选一）。都省略则追加末尾。"},
                "expected_scenes_revision": {
                    "type": "string",
                    "description": "你最近一次 story.list 返回的结构版本指纹（revision 字段，8 位十六进制）。用于乐观并发。",
                },
            },
            "required": ["scenes", "expected_scenes_revision"],
        },
    },
}

STORY_UPDATE_MANY_TOOL = {
    "type": "function",
    "function": {
        "name": "story.update_many",
        "description": (
            "一次修改多个【互相独立、已存在】的故事段（同一请求里要改 ≥2 段时优先用本工具，不要"
            "连续多次调 story.update）。每个 updates 项用稳定 scene_id 指定目标，只传要改的字段；"
            "同一段的多个字段改动合并到该项，同一 scene_id 在一批中只能出现一次。整批一次乐观并发"
            "校验、一次原子写入，任一参数非法/段不存在则整批不写入。只能改 summary/title/location/"
            "time_of_day/characters；禁止 move/reorder/swap/delete/create——这些走各自的单步工具。"
            "如果修改之间存在前后依赖（如后一个依赖前一个改后的状态），不要用本工具，仍走逐步 update。"
            "expected_revision 用 story.list 返回的 scenes_content_revision（内容指纹，非结构指纹）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "minItems": 2,
                    "description": "要修改的故事段数组（至少 2 项；只改 1 段请用 story.update）。每项 scene_id 必填 + 要改的字段（只传改的）。同 scene_id 一批只能一项。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "scene_id": {"type": "string", "description": "要修改的故事段 id（如 s02）。"},
                            "summary": {"type": "string", "description": "新的故事文本。"},
                            "title": {"type": "string", "description": "段标题（可选）。"},
                            "location": {"type": "string", "description": "地点（可选）。"},
                            "time_of_day": {"type": "string", "description": "时段，如 日/夜（可选）。"},
                            "characters": {"type": "array", "items": {"type": "string"}, "description": "新的出场角色名数组（整体覆盖）。"},
                        },
                        "required": ["scene_id"],
                    },
                },
                "expected_revision": {
                    "type": "string",
                    "description": "你最近一次 story.list 返回的 scenes_content_revision（内容版本指纹，8 位十六进制）。用于乐观并发：期间任一段内容被改动会拒绝写入并返回最新。",
                },
            },
            "required": ["updates", "expected_revision"],
        },
    },
}

STORY_DELETE_MANY_TOOL = {
    "type": "function",
    "function": {
        "name": "story.delete_many",
        "description": (
            "一次删除多个【已存在】的故事段（同一请求里要删 ≥2 段时优先用本工具，不要连续"
            "多次调 story.delete）。只接受稳定 scene_id、不接受 position 数字：先 story.list "
            "把用户说的「第N段」全部解析成 scene_id，再把 scene_ids 一次传入。整批只做一次"
            "乐观并发校验、一次原子级联删除（各段下的镜头和生成资产一并清理），任一 scene_id "
            "不存在/重复/revision 过期则整批不删（不留半删除）。删除是级联的、不可恢复。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scene_ids": {
                    "type": "array",
                    "minItems": 2,
                    "items": {"type": "string"},
                    "description": "要删除的故事段 scene_id 数组（至少 2 项，如 [\"s01\", \"s05\"]；只删 1 段请用 story.delete）。必须来自 story.list 的真实 id，不能用数字序号；同一 id 不能重复。",
                },
                "expected_scenes_revision": {
                    "type": "string",
                    "description": "你最近一次 story.list 返回的结构版本指纹（revision 字段，8 位十六进制）。用于乐观并发。",
                },
            },
            "required": ["scene_ids", "expected_scenes_revision"],
        },
    },
}

STORY_INSERT_MANY_TOOL = {
    "type": "function",
    "function": {
        "name": "story.insert_many",
        "description": (
            "一次在【多个不同锚点】前/后插入新故事段（同一请求里要在 ≥2 个不同位置插入时用"
            "本工具）。先 story.list 把用户说的「第N段」全部解析成稳定 scene_id 锚点，再把 "
            "inserts 一次传入——所有锚点都按这次调用前的快照解释，后端不会按插入后的新位置"
            "解释后续锚点。每个 insert 项 = before_scene_id / after_scene_id 二选一 + scenes "
            "数组（在该锚点处连续插入 1 段或多段，scenes 顺序即相对顺序）。同一批每个锚点"
            "只能出现一次：同一锚点要插多段就合并到该项 scenes 里。整批一次乐观并发校验、"
            "一次原子写入，任一锚点不存在/重复/参数非法则整批不写入。id 与最终顺序由后端"
            "分配。【只在同一个锚点附近连续创建多段（或末尾追加）仍用 story.create_many；"
            "本工具负责多个不同插入位置】。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "inserts": {
                    "type": "array",
                    "minItems": 2,
                    "description": "插入项数组（至少 2 个不同锚点；只有一个插入位置/末尾追加请用 story.create_many）。每项指定一个锚点和要在该锚点处插入的 scenes。各项锚点按同一调用前快照解释。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "before_scene_id": {"type": "string", "description": "插到该故事段之前（与 after_scene_id 二选一）。"},
                            "after_scene_id": {"type": "string", "description": "插到该故事段之后（与 before_scene_id 二选一）。"},
                            "scenes": {
                                "type": "array",
                                "description": "在该锚点处连续插入的故事段数组，顺序即相对顺序。每项含 summary（必填，中文正文）与 characters（可选，角色名数组）。",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "summary": {"type": "string", "description": "该段故事文本（中文正文，不要 markdown/解释/代码块）。"},
                                        "characters": {"type": "array", "items": {"type": "string"}, "description": "出场角色名数组（可空）。"},
                                    },
                                    "required": ["summary"],
                                },
                            },
                        },
                        "required": ["scenes"],
                    },
                },
                "expected_scenes_revision": {
                    "type": "string",
                    "description": "你最近一次 story.list 返回的结构版本指纹（revision 字段，8 位十六进制）。用于乐观并发。",
                },
            },
            "required": ["inserts", "expected_scenes_revision"],
        },
    },
}

# Web Search（第一版）：模型只见 query 单参数；搜索深度/结果数/超时全部由 Runtime 固定。
WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web.search",
        "description": "搜索现实世界资料，返回约 3 条「标题+摘要+链接」。仅在两种情况使用：①用户明确要求搜索（搜一下/查一下/找资料/根据真实资料写）；②正在创作且现实世界知识明显影响剧本可信度（历史时期、医疗、法律/刑侦、军事、职业流程、城市/地域、文化习俗、真实科技、新闻/现实背景）。普通润色、情绪、对白语气、结构调整、纯虚构创作不要搜索。每轮最多 3 次（超过会被系统拒绝）。查询词保持简洁、聚焦一个具体问题；复杂问题拆成最多 3 次独立查询。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "简洁、聚焦一个具体问题的中文搜索词，如「1940年代上海租界 生活状况」",
                },
            },
            "required": ["query"],
        },
    },
}

SCRIPT_WRITER_TOOLS = [
    SCRIPT_UPDATE_TOOL, SCRIPT_READ_TOOL,
    STORY_CREATE_TOOL, STORY_LIST_TOOL, STORY_READ_TOOL, STORY_MOVE_TOOL,
    STORY_UPDATE_TOOL, STORY_DELETE_TOOL,
    STORY_CREATE_MANY_TOOL, STORY_UPDATE_MANY_TOOL,
    STORY_DELETE_MANY_TOOL, STORY_INSERT_MANY_TOOL,
    WEB_SEARCH_TOOL,
    # Skill System V1（40.md）：生产技能发现/加载（只读）
    *SKILL_TOOLS,
]


# ============ 工具执行 ============
def _parse_args(arguments: str) -> dict:
    try:
        return json.loads(arguments) if arguments else {}
    except Exception as e:
        raise AgentError("bad_arguments", f"工具参数不是合法 JSON：{e}")


def _loose_json(value):
    """LLM tool-call 宽容反序列化：实测 Agnes 在【大参数 + 中文引号】时会把嵌套的
    array/object 序列化成 JSON 字符串再塞进参数（scenes 发成 "[{\\"summary\\":...}]"
    这种 str 而非 list），导致 isinstance(list) 判定失败、整批被拒。str 且看起来像
    JSON（[ 或 { 开头）时尝试 json.loads 还原；还原失败或非 str 原样返回，交给后续
    类型校验报准确错误。"""
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in ("[", "{"):
            try:
                return json.loads(s)
            except Exception:
                pass
    return value


def _load_proj(rt: AgentRuntime, project: str):
    pdir = rt._resolve_project_dir(project)
    return pdir, ProjectStore(pdir), ProjectStore(pdir).load()


def _reload(rt: AgentRuntime, project: str):
    """从 canonical（磁盘）重新读取项目——写后校验只用这份，绝不用内存对象。"""
    pdir = rt._resolve_project_dir(project)
    return ProjectStore(pdir).load()


def _base_receipt(operation: str, *, ok: bool, target: Any = None,
                  before: Any = None, after: Any = None,
                  revision: Optional[str] = None, verified: bool = False,
                  error: Optional[str] = None, **extra) -> dict:
    """统一执行回执（execution receipt）。Runtime 据此判断本轮真实成功/失败，
    不依赖模型自报。extra 携带操作相关字段（scene_id/order/...）供 observation。"""
    rec = {"ok": ok, "operation": operation, "target": target,
           "before": before, "after": after, "revision": revision,
           "verified": verified, "error": error}
    rec.update(extra)
    return rec


def _execute_script_read(rt: AgentRuntime, project: str, arguments: str,
                         instruction, message_id) -> dict:
    """读取剧本正文全文 + 版本指纹。只读，不写盘/不审计。"""
    _, _, proj = _load_proj(rt, project)
    text = proj.story_text or ""
    return {"ok": True, "text": text, "length": len(text),
            "revision": _story_token(text)}


def _execute_script_update(rt: AgentRuntime, project: str, arguments: str,
                           instruction: str, message_id: Optional[str]) -> dict:
    """执行 script.update。参数校验 → 乐观并发（story_text 域）→ 原子写 + audit →
    【重新读取 canonical 验证确实落盘】→ 返回统一回执。任何未落盘情形 ok=False。"""
    args = _parse_args(arguments)
    new_text = args.get("new_story_text")
    expected_rev = args.get("expected_revision")
    if not isinstance(new_text, str) or not new_text.strip():
        raise AgentError("edit_empty", "new_story_text 不能为空")
    new_text = new_text.strip()
    if not isinstance(expected_rev, str) or not expected_rev.strip():
        raise AgentError("bad_arguments", "缺少 expected_revision（应先 script.read 拿到版本指纹再传回）")

    pdir, pstore, proj = _load_proj(rt, project)
    old = proj.story_text or ""
    current_rev = _story_token(old)

    if expected_rev != current_rev:
        return _base_receipt(
            "script.update", ok=False, target="story_text",
            before=len(old), after=len(old), revision=current_rev, verified=False,
            error="stale_revision", stale=True,
            expected_revision=expected_rev, current_revision=current_rev,
            current_head=old[:80],
            current_tail=old[-80:] if len(old) > 80 else "",
            old_len=len(old),
            note="你读取的版本已过期（期间剧本被改动），未写入。上面是当前最新版。请基于最新内容重新判断，再用新的 expected_revision 调用 script.update。")

    if new_text == old:
        return _base_receipt(
            "script.update", ok=True, target="story_text",
            before=len(old), after=len(old), revision=current_rev, verified=True,
            unchanged=True, old_len=len(old), new_len=len(new_text),
            note="新正文与当前剧本完全一致，未产生变化。")

    proj.story_text = new_text
    pstore.save(proj)

    # 写后重新读取 canonical 验证——绝不据内存对象判定成功。
    on_disk = _reload(rt, project)
    disk_text = on_disk.story_text or ""
    if disk_text != new_text:
        return _base_receipt(
            "script.update", ok=False, target="story_text",
            before=len(old), after=len(disk_text),
            revision=_story_token(disk_text), verified=False,
            error="verify_failed",
            note="写入后重新读取发现内容与预期不一致，未确认更新。请用 script.read 读取当前最新版后重试。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="script_writer", field="story_text", before=old, after=new_text,
        instruction=instruction, message_id=message_id)
    rt.record_work(project, "change", "Script Writer 修改了剧本正文",
                   participants=["script_writer"], affected_domains=["script"],
                   references={}, status="completed")
    new_rev = _story_token(new_text)
    return _base_receipt(
        "script.update", ok=True, target="story_text",
        before=len(old), after=len(new_text), revision=new_rev, verified=True,
        unchanged=False, old_len=len(old), new_len=len(new_text),
        head=new_text[:80], tail=new_text[-80:] if len(new_text) > 80 else "",
        note="剧本已更新。请确认结果后用一句话回复用户改了什么。")


def _execute_story_list(rt: AgentRuntime, project: str, arguments: str,
                        instruction, message_id) -> dict:
    """列出所有故事段 + 结构版本指纹。只读。每个段带 position（第几条，从1开始），
    让 Agent 不用自己数「第N条对应哪个 scene_id」——尤其删除后 ID 跳号、序号与ID脱节时。"""
    _, _, proj = _load_proj(rt, project)
    scenes = [
        {"position": idx,                 # 第几条（位置序号，1-based；删除后自动重算）
         "scene_id": s.scene_id,          # 身份 ID（删除不回收、不重排，可能与位置序号脱节）
         "summary": (s.summary or "")[:120],
         "characters": list(s.characters or []),
         "shot_count": len(s.shots or [])}
        for idx, s in enumerate(proj.scenes, start=1)
    ]
    return {"ok": True, "count": len(scenes),
            "revision": _scenes_token(proj.scenes),
            "scenes_content_revision": _scenes_content_token(proj.scenes),
            "scenes": scenes}


def _execute_story_read(rt: AgentRuntime, project: str, arguments: str,
                        instruction, message_id) -> dict:
    """读取单个故事段全文 + 该段内容指纹（供 story.update 乐观并发用）。只读。
    段级 revision 只覆盖段内可变内容字段，与 scenes 结构令牌相互独立。"""
    args = _parse_args(arguments)
    scene_id = (args.get("scene_id") or "").strip()
    if not scene_id:
        raise AgentError("bad_arguments", "缺少 scene_id")
    _, _, proj = _load_proj(rt, project)
    scene = next((s for s in proj.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise AgentError("not_found", f"故事段不存在: {scene_id}")
    return {"ok": True,
            "revision": _scene_token(scene),
            "scene": {"scene_id": scene.scene_id,
                      "title": scene.title or "",
                      "location": scene.location or "",
                      "time_of_day": scene.time_of_day or "",
                      "summary": scene.summary or "",
                      "characters": list(scene.characters or []),
                      "shot_count": len(scene.shots or [])}}


def _anchor_args(args: dict) -> tuple[Optional[str], Optional[str]]:
    """从参数解析 before/after 锚点（去除空白，空串视为 None）。"""
    before = (args.get("before_scene_id") or "").strip() or None
    after = (args.get("after_scene_id") or "").strip() or None
    return before, after


def _execute_story_create(rt: AgentRuntime, project: str, arguments: str,
                          instruction: str, message_id: Optional[str]) -> dict:
    """执行 story.create：参数校验 → 乐观并发（scenes 域）→ 中性层插入 → 原子写 + audit。
    支持 before/after_scene_id 任意位置插入（都省略 → 追加末尾）。"""
    args = _parse_args(arguments)
    summary = args.get("summary")
    expected_rev = args.get("expected_scenes_revision")
    characters = args.get("characters", [])
    before, after = _anchor_args(args)

    if not isinstance(summary, str) or not summary.strip():
        raise AgentError("bad_arguments", "summary 不能为空（该故事段的故事文本）")
    summary = summary.strip()
    if characters is None:
        characters = []
    if not isinstance(characters, list):
        raise AgentError("bad_arguments", "characters 必须是字符串数组")
    characters = [str(c) for c in characters]
    if not isinstance(expected_rev, str) or not expected_rev.strip():
        raise AgentError("bad_arguments", "缺少 expected_scenes_revision（应先 story.list 拿到版本指纹再传回）")

    pdir, pstore, proj = _load_proj(rt, project)
    current_rev = _scenes_token(proj.scenes)
    if expected_rev != current_rev:
        current_scenes = [{"scene_id": s.scene_id, "summary": (s.summary or "")[:80]}
                          for s in proj.scenes]
        return _base_receipt(
            "story.create", ok=False, target="scenes",
            before=len(proj.scenes), after=len(proj.scenes),
            revision=current_rev, verified=False, error="stale_revision", stale=True,
            expected_revision=expected_rev, current_revision=current_rev,
            current_scenes=current_scenes,
            note="你读取的故事段列表已过期（期间被改动），未创建。上面是当前最新列表。请基于最新列表重新判断，再用新的 expected_scenes_revision 调用 story.create。")

    before_ids = [s.scene_id for s in proj.scenes]
    try:
        new_scenes = insert_scenes(
            proj, before=before, after=after,
            specs=[{"summary": summary, "characters": characters}])
    except StructureError as e:
        raise AgentError(e.code, e.message)
    new_scene = new_scenes[0]
    new_id = new_scene.scene_id
    pstore.save(proj)

    # 写后重新读取 canonical 验证：新段确实在磁盘上、且字段一致、顺序对。
    on_disk = _reload(rt, project)
    disk_ids = [s.scene_id for s in on_disk.scenes]
    disk_scene = next((s for s in on_disk.scenes if s.scene_id == new_id), None)
    if disk_scene is None or disk_scene.summary != summary \
            or list(disk_scene.characters or []) != characters:
        return _base_receipt(
            "story.create", ok=False, target="scenes",
            before=len(before_ids), after=len(disk_ids),
            revision=_scenes_token(on_disk.scenes), verified=False,
            error="verify_failed",
            note="创建后重新读取未在 canonical 中找到一致的新故事段，未确认创建。请用 story.list 读取当前列表后重试。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="script_writer", field="scenes",
        before=before_ids, after=disk_scene.to_dict(),
        instruction=instruction, message_id=message_id)
    rt.record_work(project, "change", f"Script Writer 创建故事段 {new_id}",
                   participants=["script_writer"], affected_domains=["storyboard"],
                   references={"scene_id": new_id}, status="completed")

    order = disk_ids.index(new_id) + 1
    new_rev = _scenes_token(on_disk.scenes)
    return _base_receipt(
        "story.create", ok=True, target="scenes",
        before=len(before_ids), after=len(disk_ids), revision=new_rev, verified=True,
        scene_id=new_id, order=order, scene=disk_scene.to_dict(),
        scenes_revision=new_rev,
        note=f"故事段 {new_id} 已创建（第 {order} 段）。请确认结果后用一句话回复用户。")


def _execute_story_move(rt: AgentRuntime, project: str, arguments: str,
                        instruction: str, message_id: Optional[str]) -> dict:
    """执行 story.move：参数校验 → 乐观并发（scenes 域）→ 中性层移动 → 原子写 + audit。"""
    args = _parse_args(arguments)
    scene_id = (args.get("scene_id") or "").strip()
    expected_rev = args.get("expected_scenes_revision")
    before, after = _anchor_args(args)

    if not scene_id:
        raise AgentError("bad_arguments", "缺少 scene_id（要移动的故事段）")
    if not isinstance(expected_rev, str) or not expected_rev.strip():
        raise AgentError("bad_arguments", "缺少 expected_scenes_revision（应先 story.list 拿到版本指纹再传回）")

    pdir, pstore, proj = _load_proj(rt, project)
    current_rev = _scenes_token(proj.scenes)
    if expected_rev != current_rev:
        current_scenes = [{"scene_id": s.scene_id, "summary": (s.summary or "")[:80]}
                          for s in proj.scenes]
        return _base_receipt(
            "story.move", ok=False, target="scenes",
            before=len(proj.scenes), after=len(proj.scenes),
            revision=current_rev, verified=False, error="stale_revision", stale=True,
            expected_revision=expected_rev, current_revision=current_rev,
            current_scenes=current_scenes,
            note="你读取的故事段列表已过期（期间被改动），未移动。上面是当前最新列表。请用新的 expected_scenes_revision 重新调用 story.move。")

    before_ids = [s.scene_id for s in proj.scenes]
    try:
        moved = move_scene(proj, scene_id, before=before, after=after)
    except StructureError as e:
        raise AgentError(e.code, e.message)
    expected_order = [s.scene_id for s in proj.scenes]
    expected_pos = expected_order.index(scene_id) + 1
    pstore.save(proj)

    # 写后重新读取 canonical 验证：顺序确实按预期落盘。
    on_disk = _reload(rt, project)
    new_order = [s.scene_id for s in on_disk.scenes]
    if new_order != expected_order:
        return _base_receipt(
            "story.move", ok=False, target="scenes",
            before=before_ids, after=new_order,
            revision=_scenes_token(on_disk.scenes), verified=False,
            error="verify_failed",
            note="移动后重新读取发现顺序与预期不一致，未确认移动。请用 story.list 读取当前列表后重试。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="script_writer", field="scenes",
        before=before_ids, after=new_order,
        instruction=instruction, message_id=message_id)
    rt.record_work(project, "change", f"Script Writer 移动故事段 {scene_id}",
                   participants=["script_writer"], affected_domains=["storyboard"],
                   references={"scene_id": scene_id}, status="completed")

    new_rev = _scenes_token(on_disk.scenes)
    return _base_receipt(
        "story.move", ok=True, target="scenes",
        before=before_ids, after=new_order, revision=new_rev, verified=True,
        scene_id=scene_id, order=expected_pos,
        scenes_order=new_order, scenes_revision=new_rev,
        note=f"故事段 {scene_id} 已移到第 {expected_pos} 位。请确认结果后用一句话回复用户。")


def _execute_story_update(rt: AgentRuntime, project: str, arguments: str,
                          instruction: str, message_id: Optional[str]) -> dict:
    """执行 story.update：修改【单个已有故事段】的自身内容字段（白名单）。

    流程：参数校验（scene_id + expected_revision + 仅白名单字段）→ 乐观并发（段级内容指纹）
    → 无实际变更 no-op（不写盘/不审计）→ 内存改字段 → 原子写 →【重读 canonical 校验每个字段
    确实落盘】→ audit + 工作记忆 → 统一回执。

    边界：不改 scene_id（身份）、不改 shots（分镜归 Director）、不改项目级 story_text/角色定义。
    段内内容修改只变段级 _scene_token，不变 _scenes_token 结构指纹（增/删/移动才变）。"""
    args = _parse_args(arguments)
    scene_id = (args.get("scene_id") or "").strip()
    expected_rev = args.get("expected_revision")
    if not scene_id:
        raise AgentError("bad_arguments", "缺少 scene_id")
    if not isinstance(expected_rev, str) or not expected_rev.strip():
        raise AgentError("bad_arguments",
                        "缺少 expected_revision（应先 story.read(scene_id) 拿到该段内容指纹再传回）")

    # 字段白名单：只接受 SCENE_UPDATABLE_FIELDS；其余（scene_id/shots/story_text...）一律拒绝，
    # 不静默忽略，避免模型误以为改了其实没改。
    unknown = [k for k in args.keys()
               if k not in SCENE_UPDATABLE_FIELDS
               and k not in ("scene_id", "expected_revision")]
    if unknown:
        raise AgentError(
            "bad_arguments",
            f"不允许修改字段：{', '.join(unknown)}。story.update 只能改 "
            f"{', '.join(SCENE_UPDATABLE_FIELDS)}；不能改 scene_id、分镜(shots)、项目剧"
            f"本正文或角色定义。")

    # 收集本次要改的字段（已校验类型）；characters 必须是字符串数组。
    changes: dict = {}
    for field in SCENE_UPDATABLE_FIELDS:
        if field not in args or args.get(field) is None:
            continue
        val = args.get(field)
        if field == "characters":
            if not isinstance(val, list):
                raise AgentError("bad_arguments", "characters 必须是字符串数组")
            changes[field] = [str(c).strip() for c in val if str(c).strip()]
        else:
            if not isinstance(val, str):
                raise AgentError("bad_arguments", f"{field} 必须是字符串")
            changes[field] = val.strip()

    pdir, pstore, proj = _load_proj(rt, project)
    scene = next((s for s in proj.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise AgentError("not_found", f"故事段不存在: {scene_id}")

    before_token = _scene_token(scene)
    if expected_rev != before_token:
        current_scene = {k: (list(getattr(scene, k, []) or []) if k == "characters"
                             else (getattr(scene, k, "") or ""))
                         for k in SCENE_UPDATABLE_FIELDS}
        return _base_receipt(
            "story.update", ok=False, target=f"scene:{scene_id}",
            before=current_scene, after=current_scene,
            revision=before_token, verified=False, error="stale_revision", stale=True,
            scene_id=scene_id, expected_revision=expected_rev,
            current_revision=before_token, current_scene=current_scene,
            note="你读取的该故事段内容已过期（期间被改动），未修改。上面是当前最新内容。"
                 "请基于最新内容重新判断，再用新的 expected_revision 调用 story.update。")

    # 无实际变更（没给任何可改字段，或给的值与现值完全一致）→ no-op，不写盘/不审计。
    real_diff = {}
    for f, v in changes.items():
        cur = list(getattr(scene, f, []) or []) if f == "characters" \
            else (getattr(scene, f, "") or "")
        if cur != v:
            real_diff[f] = v
    if not real_diff:
        current_scene = {k: (list(getattr(scene, k, []) or []) if k == "characters"
                             else (getattr(scene, k, "") or ""))
                         for k in SCENE_UPDATABLE_FIELDS}
        return _base_receipt(
            "story.update", ok=True, target=f"scene:{scene_id}",
            before=current_scene, after=current_scene,
            revision=before_token, verified=True, unchanged=True, scene_id=scene_id,
            changed=[],
            note="提供的内容与该故事段当前内容完全一致，未产生变化。")

    before_scene = {k: (list(getattr(scene, k, []) or []) if k == "characters"
                        else (getattr(scene, k, "") or ""))
                    for k in SCENE_UPDATABLE_FIELDS}
    before_order = [s.scene_id for s in proj.scenes]

    # 应用到内存对象（逐字段 set）。
    for f, v in real_diff.items():
        setattr(scene, f, v)
    pstore.save(proj)

    # 写后重读 canonical 校验：目标段每个变更字段确实落盘，且段身份/顺序未受影响。
    on_disk = _reload(rt, project)
    disk_scene = next((s for s in on_disk.scenes if s.scene_id == scene_id), None)
    after_order = [s.scene_id for s in on_disk.scenes]
    verified = (disk_scene is not None and after_order == before_order
                and all(
                    (list(getattr(disk_scene, f, []) or []) if f == "characters"
                     else (getattr(disk_scene, f, "") or "")) == v
                    for f, v in real_diff.items()))
    after_scene = None
    if disk_scene is not None:
        after_scene = {k: (list(getattr(disk_scene, k, []) or []) if k == "characters"
                           else (getattr(disk_scene, k, "") or ""))
                       for k in SCENE_UPDATABLE_FIELDS}
    if not verified:
        return _base_receipt(
            "story.update", ok=False, target=f"scene:{scene_id}",
            before=before_scene, after=after_scene,
            revision=_scene_token(disk_scene), verified=False,
            error="verify_failed", scene_id=scene_id,
            note="修改后重新读取发现内容与预期不一致，未确认修改。请用 story.read 读取该段最新内容后重试。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="script_writer", field="scene",
        before={"scene_id": scene_id, **before_scene},
        after={"scene_id": scene_id, **after_scene},
        instruction=instruction, message_id=message_id)
    changed_summary = "、".join(
        ("出场角色" if f == "characters" else f) for f in real_diff.keys())
    rt.record_work(project, "change",
                   f"Script Writer 修改故事段 {scene_id}（{changed_summary}）",
                   participants=["script_writer"], affected_domains=["storyboard"],
                   references={"scene_id": scene_id}, status="completed")

    new_rev = _scene_token(disk_scene)
    return _base_receipt(
        "story.update", ok=True, target=f"scene:{scene_id}",
        before=before_scene, after=after_scene, revision=new_rev, verified=True,
        unchanged=False, scene_id=scene_id, changed=list(real_diff.keys()),
        note=f"故事段 {scene_id} 已更新（{changed_summary}）。请确认结果后用一句话回复用户改了什么。")


def _execute_story_create_many(rt: AgentRuntime, project: str, arguments: str,
                                instruction: str, message_id: Optional[str]) -> dict:
    """执行 story.create_many：一次创建多个故事段（整批一次 OCC + 一次原子写）。

    atomic：先校验所有 spec 合法（summary 非空、characters 数组），任一非法抛 AgentError 不写。
    OCC 用 _scenes_token（结构指纹，同 story.create）。中性层 insert_scenes 一次性插入保持
    数组顺序、连续分配 id。写后重读 canonical 校验所有新段落盘 + 顺序连续 + 字段一致。
    单写串行 fence：本工具整体算一个写操作（WRITE_OPERATIONS）。"""
    args = _parse_args(arguments)
    # 宽容反序列化：provider 偶发把嵌套数组/对象序列化成 JSON 字符串（见 _loose_json）
    scenes_arr = _loose_json(args.get("scenes"))
    expected_rev = args.get("expected_scenes_revision")
    before, after = _anchor_args(args)

    if not isinstance(scenes_arr, list) or not scenes_arr:
        raise AgentError("bad_arguments", "scenes 必须是非空数组")
    if not isinstance(expected_rev, str) or not expected_rev.strip():
        raise AgentError("bad_arguments",
                        "缺少 expected_scenes_revision（应先 story.list 拿到版本指纹再传回）")

    # atomic：先校验所有 spec，任一非法整批不写
    specs = []
    for i, sc0 in enumerate(scenes_arr):
        sc = _loose_json(sc0)   # 数组项也可能被 stringify
        if not isinstance(sc, dict):
            raise AgentError("bad_arguments", f"scenes[{i}] 必须是对象")
        summary = sc.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise AgentError("bad_arguments", f"scenes[{i}].summary 不能为空")
        chars = _loose_json(sc.get("characters") or [])
        if not isinstance(chars, list):
            raise AgentError("bad_arguments", f"scenes[{i}].characters 必须是字符串数组")
        specs.append({"summary": summary.strip(),
                      "characters": [str(c).strip() for c in chars if str(c).strip()]})

    pdir, pstore, proj = _load_proj(rt, project)
    current_rev = _scenes_token(proj.scenes)
    if expected_rev != current_rev:
        current_scenes = [{"scene_id": s.scene_id, "summary": (s.summary or "")[:80]}
                          for s in proj.scenes]
        return _base_receipt(
            "story.create_many", ok=False, target="scenes",
            before=len(proj.scenes), after=len(proj.scenes),
            revision=current_rev, verified=False, error="stale_revision", stale=True,
            expected_revision=expected_rev, current_revision=current_rev,
            current_scenes=current_scenes,
            note="你读取的故事段列表已过期（期间被改动），未创建。上面是当前最新列表。请基于最新列表用新的 expected_scenes_revision 重新调用 story.create_many。")

    before_ids = [s.scene_id for s in proj.scenes]
    try:
        new_scenes = insert_scenes(proj, before=before, after=after, specs=specs)
    except StructureError as e:
        raise AgentError(e.code, e.message)
    new_ids = [s.scene_id for s in new_scenes]
    pstore.save(proj)

    # 写后重读 canonical 校验：所有新段在盘 + 顺序连续 + 字段一致
    on_disk = _reload(rt, project)
    disk_ids = [s.scene_id for s in on_disk.scenes]
    disk_by_id = {s.scene_id: s for s in on_disk.scenes}
    verified = True
    if not new_ids:
        verified = False
    else:
        try:
            start = disk_ids.index(new_ids[0])
        except ValueError:
            verified = False
            start = -1
        if verified and disk_ids[start:start + len(new_ids)] != new_ids:
            verified = False
        for spec, nid in zip(specs, new_ids):
            ds = disk_by_id.get(nid)
            if ds is None or ds.summary != spec["summary"] \
                    or list(ds.characters or []) != spec["characters"]:
                verified = False
                break
    if not verified:
        return _base_receipt(
            "story.create_many", ok=False, target="scenes",
            before=len(before_ids), after=len(disk_ids),
            revision=_scenes_token(on_disk.scenes), verified=False,
            error="verify_failed", created=[{"scene_id": nid} for nid in new_ids],
            note="创建后重新读取未在 canonical 中确认全部新段一致落盘，未确认。请用 story.list 读取当前列表后重试。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="script_writer", field="scenes",
        before=before_ids,
        after=[{"scene_id": nid, "summary": (sp["summary"] or "")[:80]}
               for nid, sp in zip(new_ids, specs)],
        instruction=instruction, message_id=message_id)
    rt.record_work(project, "change",
                   f"Script Writer 批量创建 {len(new_ids)} 个故事段",
                   participants=["script_writer"], affected_domains=["storyboard"],
                   status="completed")

    new_rev = _scenes_token(on_disk.scenes)
    created = [{"scene_id": nid, "order": disk_ids.index(nid) + 1} for nid in new_ids]
    return _base_receipt(
        "story.create_many", ok=True, target="scenes",
        before=len(before_ids), after=len(disk_ids), revision=new_rev, verified=True,
        created=created, count=len(created), scenes_revision=new_rev,
        note=f"已创建 {len(created)} 段：{', '.join(new_ids)}。请确认结果后用一句话回复用户。")


def _execute_story_update_many(rt: AgentRuntime, project: str, arguments: str,
                                instruction: str, message_id: Optional[str]) -> dict:
    """执行 story.update_many：一次修改多个互相独立、已存在的故事段（整批一次 OCC + 一次原子写）。

    OCC 用 _scenes_content_token（全局内容指纹，catch 任一段内容改动；与结构指纹 _scenes_token
    独立）。atomic：先校验所有项（scene_id、字段白名单、同 scene 一批最多一次、scene 存在），
    任一非法抛 AgentError 不写。无实际变更 no-op。写后重读校验所有变更段落盘 + 顺序未变。
    禁止 move/delete/create/order（不接受相关字段；只接受 SCENE_UPDATABLE_FIELDS）。"""
    args = _parse_args(arguments)
    # 宽容反序列化：provider 偶发把嵌套数组/对象序列化成 JSON 字符串（见 _loose_json）
    updates_arr = _loose_json(args.get("updates"))
    expected_rev = args.get("expected_revision")

    if not isinstance(updates_arr, list) or not updates_arr:
        raise AgentError("bad_arguments", "updates 必须是非空数组")
    if not isinstance(expected_rev, str) or not expected_rev.strip():
        raise AgentError("bad_arguments",
                        "缺少 expected_revision（应先 story.list 拿到 scenes_content_revision 再传回）")

    # atomic：先校验所有项合法
    seen_ids: set = set()
    parsed: list = []   # [(scene_id, changes_dict)]
    for i, it0 in enumerate(updates_arr):
        it = _loose_json(it0)   # 数组项也可能被 stringify
        if not isinstance(it, dict):
            raise AgentError("bad_arguments", f"updates[{i}] 必须是对象")
        sid = (it.get("scene_id") or "").strip()
        if not sid:
            raise AgentError("bad_arguments", f"updates[{i}] 缺少 scene_id")
        if sid in seen_ids:
            raise AgentError("bad_arguments",
                            f"updates[{i}] scene_id={sid} 重复：同一批中同一段只能出现一次，"
                            "请把该段的多个字段改动合并到该项。")
        seen_ids.add(sid)
        unknown = [k for k in it.keys()
                   if k not in SCENE_UPDATABLE_FIELDS and k not in ("scene_id", "expected_revision")]
        if unknown:
            raise AgentError(
                "bad_arguments",
                f"updates[{i}] 不允许字段：{', '.join(unknown)}。story.update_many 只能改 "
                f"{', '.join(SCENE_UPDATABLE_FIELDS)}；禁止 move/delete/create/order。"
                "需要移动/删除/创建请用各自的单独工具。")
        changes: dict = {}
        for field in SCENE_UPDATABLE_FIELDS:
            if field not in it or it.get(field) is None:
                continue
            val = it.get(field)
            if field == "characters":
                val = _loose_json(val)   # 角色数组也可能被 stringify
                if not isinstance(val, list):
                    raise AgentError("bad_arguments", f"updates[{i}].characters 必须是字符串数组")
                changes[field] = [str(c).strip() for c in val if str(c).strip()]
            else:
                if not isinstance(val, str):
                    raise AgentError("bad_arguments", f"updates[{i}].{field} 必须是字符串")
                changes[field] = val.strip()
        parsed.append((sid, changes))

    pdir, pstore, proj = _load_proj(rt, project)
    current_rev = _scenes_content_token(proj.scenes)
    if expected_rev != current_rev:
        return _base_receipt(
            "story.update_many", ok=False, target="scenes_content",
            before=len(proj.scenes), after=len(proj.scenes),
            revision=current_rev, verified=False, error="stale_revision", stale=True,
            expected_revision=expected_rev, current_revision=current_rev,
            note="你读取的故事段内容已过期（期间被改动），未修改。请用 story.list 读取当前内容"
                 "并拿新的 scenes_content_revision 后重新调用 story.update_many。")

    # 校验所有 scene_id 存在（atomic：任一不存在整批不写）
    scene_map = {s.scene_id: s for s in proj.scenes}
    for sid, _ in parsed:
        if sid not in scene_map:
            raise AgentError("not_found", f"故事段不存在: {sid}")

    # 无实际变更检测
    real_changes: list = []   # [(sid, real_diff)]
    for sid, changes in parsed:
        scene = scene_map[sid]
        real_diff = {}
        for f, v in changes.items():
            cur = list(getattr(scene, f, []) or []) if f == "characters" \
                else (getattr(scene, f, "") or "")
            if cur != v:
                real_diff[f] = v
        if real_diff:
            real_changes.append((sid, real_diff))

    def _scene_content_view(scene):
        return {k: (list(getattr(scene, k, []) or []) if k == "characters"
                   else (getattr(scene, k, "") or ""))
                for k in SCENE_UPDATABLE_FIELDS}

    if not real_changes:
        return _base_receipt(
            "story.update_many", ok=True, target="scenes_content",
            before=len(proj.scenes), after=len(proj.scenes),
            revision=current_rev, verified=True, unchanged=True,
            results=[], count=0,
            note="提供的修改与各段当前内容完全一致，未产生变化。")

    before_order = [s.scene_id for s in proj.scenes]
    before_scenes = {sid: _scene_content_view(scene_map[sid]) for sid, _ in real_changes}

    # 应用到内存对象
    for sid, diff in real_changes:
        scene = scene_map[sid]
        for f, v in diff.items():
            setattr(scene, f, v)
    pstore.save(proj)

    # 写后重读 canonical 校验：所有变更字段落盘 + 顺序未变
    on_disk = _reload(rt, project)
    after_order = [s.scene_id for s in on_disk.scenes]
    disk_map = {s.scene_id: s for s in on_disk.scenes}
    verified = (after_order == before_order and all(
        (list(getattr(disk_map[sid], f, []) or []) if f == "characters"
         else (getattr(disk_map[sid], f, "") or "")) == v
        for sid, diff in real_changes for f, v in diff.items())
        and all(sid in disk_map for sid, _ in real_changes))
    if not verified:
        return _base_receipt(
            "story.update_many", ok=False, target="scenes_content",
            before=before_order, after=after_order,
            revision=_scenes_content_token(on_disk.scenes), verified=False,
            error="verify_failed",
            note="修改后重新读取发现内容与预期不一致，未确认修改。请用 story.list 读取当前内容后重试。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="script_writer", field="scenes_content",
        before={sid: before_scenes[sid] for sid, _ in real_changes},
        after={sid: _scene_content_view(disk_map[sid]) for sid, _ in real_changes},
        instruction=instruction, message_id=message_id)
    rt.record_work(project, "change",
                   f"Script Writer 批量修改 {len(real_changes)} 个故事段",
                   participants=["script_writer"], affected_domains=["storyboard"],
                   status="completed")

    new_rev = _scenes_content_token(on_disk.scenes)
    results = [{"scene_id": sid, "changed": list(diff.keys())}
               for sid, diff in real_changes]
    return _base_receipt(
        "story.update_many", ok=True, target="scenes_content",
        before=len(before_order), after=len(after_order),
        revision=new_rev, verified=True,
        results=results, count=len(results), scenes_content_revision=new_rev,
        note=f"已修改 {len(results)} 段。请确认结果后用一句话回复用户改了什么。")


def _active_production_reason(project: str) -> Optional[str]:
    """该项目有正在跑的生产流/视频 job 时返回原因串，否则 None（复用 Director 同护栏：
    删正在生成的镜头会让 worker 写回空镜头）。best-effort，测试环境无生产模块→None。"""
    try:
        from src.agents.change_protocol.execution_service import _active_production
        return _active_production(project)
    except Exception:
        return None


def _execute_story_delete(rt: AgentRuntime, project: str, arguments: str,
                          instruction: str, message_id: Optional[str]) -> dict:
    """执行 story.delete(scene_id)：单阶段直接级联删除（方案 B：用户明确要删就直接删，
    不再两阶段预览，避免 Agent「用文字假装预览却没发工具调用」导致的确认后无法删除）。

    直接执行：活跃生产护栏 + execute_delete 级联删（原子写 canonical + 取消孤儿
    job + 清理资产文件）→ 重读 canonical 验证段确实消失 → audit + 工作记忆 → 回执。
    confirmed 参数保留兼容（显式传 false 才预览，不传/传 true 都直接删）。

    复用 Director 同款公用服务 src/storyboard/delete_service，不重复造轮子。
    边界：SW 只删故事段（scene）；段内镜头由 delete_service 确定性级联（不能留孤儿镜头），
    但 SW 不提供单独 delete_shot（那归 Director）。scene_id 不存在由服务返回 found=False，
    转 not_found 错误。"""
    args = _parse_args(arguments)
    scene_id = (args.get("scene_id") or "").strip()
    if not scene_id:
        raise AgentError("bad_arguments", "缺少 scene_id")
    # 方案 B：默认直接删（confirmed 不传或 true 都删）。仅显式 confirmed=false 才走预览（兼容）。
    confirmed = args.get("confirmed", True)
    confirmed = (confirmed is True or
                 (isinstance(confirmed, str) and confirmed.lower() == "true"))

    from src.storyboard.delete_service import preview_delete, execute_delete, DeleteError
    pdir, pstore, proj = _load_proj(rt, project)

    # ---- 预览分支（仅显式 confirmed=false；兼容旧两阶段调用）----
    prev = preview_delete(proj, "scene", scene_id)
    if not prev.get("found", True):
        raise AgentError("not_found", f"故事段不存在: {scene_id}")

    if not confirmed:
        reason = _active_production_reason(project)
        if reason:
            prev = dict(prev)
            prev["active_production"] = reason
        wd = prev.get("will_delete", {}) or {}
        return _base_receipt(
            "story.delete", ok=True, target=f"scene:{scene_id}",
            before={"scenes": wd.get("scenes", []), "shots": wd.get("shots", [])},
            after=None, revision=None, verified=False,
            confirmed=False, preview=prev,
            will_delete=wd,
            protected_characters=prev.get("protected_characters", []),
            other_scenes_untouched=prev.get("other_scenes_untouched", []),
            note="这是删除预览（尚未删除，仅 confirmed=false 时返回）。")

    # ---- 直接删除（默认）----
    reason = _active_production_reason(project)
    if reason:
        raise AgentError("production_active",
                         f"当前项目有正在进行的视频生成（{reason}），等生成结束后再删除")

    before_ids = [s.scene_id for s in proj.scenes]
    before_struct_rev = _scenes_token(proj.scenes)
    try:
        res = execute_delete(pdir, proj, "scene", scene_id)
    except DeleteError as e:
        raise AgentError(e.code, e.message)

    deleted = res["deleted"]
    # 写后重读 canonical 验证：目标段确实消失，其余段保留。
    on_disk = _reload(rt, project)
    after_ids = [s.scene_id for s in on_disk.scenes]
    gone = scene_id not in after_ids and scene_id in deleted.get("scenes", [])
    if not gone:
        return _base_receipt(
            "story.delete", ok=False, target=f"scene:{scene_id}",
            before=before_ids, after=after_ids,
            revision=_scenes_token(on_disk.scenes), verified=False,
            confirmed=True, error="verify_failed",
            note="删除后重新读取发现故事段仍在，未确认删除。请用 story.list 确认当前列表后重试。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="script_writer", field=f"delete_scene:{scene_id}",
        before={"scenes": deleted.get("scenes", []), "shots": deleted.get("shots", []),
                "plans": deleted.get("plans", []), "removed_files": res["removed_files"]},
        after=[], instruction=instruction, message_id=message_id)
    n_scenes = len(deleted.get("scenes", []))
    n_shots = len(deleted.get("shots", []))
    rt.record_work(project, "change",
                   f"Script Writer 删除了 {n_scenes} 个故事段、{n_shots} 个镜头",
                   participants=["script_writer"], affected_domains=["storyboard"],
                   references={"scene_id": scene_id}, status="completed")

    new_rev = _scenes_token(on_disk.scenes)
    parts = [f"故事段 {'、'.join(deleted.get('scenes', []))}"]
    if deleted.get("shots"):
        parts.append(f"镜头 {'、'.join(deleted['shots'])}")
    return _base_receipt(
        "story.delete", ok=True, target=f"scene:{scene_id}",
        before=before_ids, after=after_ids, revision=new_rev, verified=True,
        confirmed=True, scene_id=scene_id,
        deleted=deleted, removed_files=res["removed_files"],
        plan_removed=res.get("plan_removed", False),
        scenes_revision=new_rev,
        note=f"已删除{'、'.join(parts)}"
             + (f"，清理 {res['removed_files']} 个生成资产文件" if res["removed_files"] else "")
             + "。请用一句话回复用户删除了什么。")


def _execute_story_delete_many(rt: AgentRuntime, project: str, arguments: str,
                               instruction: str, message_id: Optional[str]) -> dict:
    """执行 story.delete_many：一次删除多个已锁定的故事段（整批一次 OCC + 一次原子写）。

    只接受稳定 scene_id（不接受 position——Agent 应先 story.list 把「第N段」解析成 id）。
    atomic：参数解析阶段拒绝空/重复 id；OCC 用 _scenes_token（结构指纹）；中性层
    apply_delete_scenes_many_to_memory 先校验所有 id 存在（任一不存在整批不删），再一次性
    级联删（段+镜头+制作计划+资产文件聚合），一次 save、一次 cancel/commit、一次重读验证。
    单写串行 fence：本工具整体算一个写操作。"""
    args = _parse_args(arguments)
    # 宽容反序列化：provider 偶发把嵌套数组序列化成 JSON 字符串（见 _loose_json）
    ids_raw = _loose_json(args.get("scene_ids"))
    expected_rev = args.get("expected_scenes_revision")

    if not isinstance(ids_raw, list) or not ids_raw:
        raise AgentError("bad_arguments",
                         "scene_ids 必须是非空数组（稳定 scene_id；不要传 position 数字，"
                         "先 story.list 把「第N段」解析成 scene_id）")
    scene_ids: list = []
    seen: set = set()
    for i, v in enumerate(ids_raw):
        if not isinstance(v, str) or not v.strip():
            raise AgentError("bad_arguments", f"scene_ids[{i}] 必须是非空 scene_id 字符串")
        sid = v.strip()
        if sid in seen:
            raise AgentError("bad_arguments",
                             f"scene_ids 含重复 scene_id：{sid}（同一批中同一段只能出现一次）")
        seen.add(sid)
        scene_ids.append(sid)
    if not isinstance(expected_rev, str) or not expected_rev.strip():
        raise AgentError("bad_arguments",
                        "缺少 expected_scenes_revision（应先 story.list 拿到版本指纹再传回）")

    from src.storyboard.delete_service import (
        apply_delete_scenes_many_to_memory, commit_disk_effects,
        cancel_orphan_jobs, DeleteError)

    pdir, pstore, proj = _load_proj(rt, project)
    current_rev = _scenes_token(proj.scenes)
    if expected_rev != current_rev:
        current_scenes = [{"scene_id": s.scene_id, "summary": (s.summary or "")[:80]}
                          for s in proj.scenes]
        return _base_receipt(
            "story.delete_many", ok=False, target="scenes",
            before=len(proj.scenes), after=len(proj.scenes),
            revision=current_rev, verified=False, error="stale_revision", stale=True,
            expected_revision=expected_rev, current_revision=current_rev,
            current_scenes=current_scenes,
            note="你读取的故事段列表已过期（期间被改动），未删除。上面是当前最新列表。"
                 "请基于最新列表重新解析要删的 scene_id，再用新的 expected_scenes_revision "
                 "调用 story.delete_many。")

    # 活跃生产护栏（与单删同款：删正在生成的镜头会让 worker 写回空镜头）
    reason = _active_production_reason(project)
    if reason:
        raise AgentError("production_active",
                         f"当前项目有正在进行的视频生成（{reason}），等生成结束后再删除")

    before_ids = [s.scene_id for s in proj.scenes]
    shots_by_scene = {s.scene_id: [sh.shot_id for sh in (s.shots or [])]
                      for s in proj.scenes}

    try:
        plan = apply_delete_scenes_many_to_memory(proj, scene_ids)
    except DeleteError as e:
        raise AgentError(e.code, e.message)
    pstore.save(proj)

    # 写后重读 canonical 验证：目标段全部消失、幸存段顺序与快照一致（无半删除）。
    on_disk = _reload(rt, project)
    after_ids = [s.scene_id for s in on_disk.scenes]
    target_set = set(scene_ids)
    expected_survivors = [sid for sid in before_ids if sid not in target_set]
    if after_ids != expected_survivors or any(sid in after_ids for sid in scene_ids):
        return _base_receipt(
            "story.delete_many", ok=False, target="scenes",
            before=before_ids, after=after_ids,
            revision=_scenes_token(on_disk.scenes), verified=False,
            error="verify_failed",
            deleted=[{"scene_id": sid} for sid in scene_ids],
            note="批量删除后重新读取发现结果与预期不一致，未确认删除。"
                 "请用 story.list 读取当前列表后重试。")

    # 磁盘副作用（整批一次）：取消孤儿 job + 删资产文件 + 制作计划
    cancel_orphan_jobs(getattr(on_disk, "name", "") or project,
                       {sh.shot_id for sh in on_disk.all_shots()})
    fx = commit_disk_effects(pdir, plan["plan_keys"], plan["asset_files"],
                             log_ctx=f"delete_many:{len(scene_ids)}")

    deleted_items = [{"scene_id": sid, "shots": shots_by_scene.get(sid, [])}
                     for sid in scene_ids]
    DirectEditAuditStore(pdir, project).record(
        agent_id="script_writer", field="delete_scenes_many",
        before={"scenes": plan["deleted"]["scenes"], "shots": plan["deleted"]["shots"],
                "plans": plan["plan_keys"], "removed_files": fx["removed_files"]},
        after=[], instruction=instruction, message_id=message_id)
    n_shots = len(plan["deleted"]["shots"])
    rt.record_work(project, "change",
                   f"Script Writer 批量删除了 {len(scene_ids)} 个故事段、{n_shots} 个镜头",
                   participants=["script_writer"], affected_domains=["storyboard"],
                   references={"scene_ids": scene_ids}, status="completed")

    new_rev = _scenes_token(on_disk.scenes)
    return _base_receipt(
        "story.delete_many", ok=True, target="scenes",
        before=before_ids, after=after_ids, revision=new_rev, verified=True,
        deleted=deleted_items, count=len(deleted_items),
        deleted_scenes=plan["deleted"]["scenes"], deleted_shots=plan["deleted"]["shots"],
        removed_files=fx["removed_files"], plan_removed=fx["plan_removed"],
        scenes_revision=new_rev,
        note=f"已批量删除 {len(scene_ids)} 段（{'、'.join(scene_ids)}）"
             + (f"及 {n_shots} 个镜头" if n_shots else "")
             + (f"，清理 {fx['removed_files']} 个生成资产文件" if fx["removed_files"] else "")
             + "。请用 story.list 核对后用一句话回复用户删除了什么。")


def _execute_story_insert_many(rt: AgentRuntime, project: str, arguments: str,
                               instruction: str, message_id: Optional[str]) -> dict:
    """执行 story.insert_many：一次在多个不同锚点前/后插入新故事段（整批一次 OCC + 一次
    原子写）。

    快照语义：所有锚点由 Agent 从同一次 story.list 解析为稳定 scene_id；中性层
    insert_scenes_at_anchors 对调用前快照线性走查算出唯一最终顺序，不按「插一个后的新
    位置」解释后续锚点。atomic：先校验所有 insert 项（before/after 二选一、锚点不重复、
    scenes 非空、spec 合法），任一非法抛 AgentError 不写；OCC 用 _scenes_token；锚点不存在
    由中性层抛 not_found。写后重读校验最终顺序与每个新段字段。单写串行 fence：整批一个
    写槽。与 create_many 边界：单锚点连续创建/末尾追加走 create_many，本工具负责多锚点。"""
    args = _parse_args(arguments)
    inserts_raw = _loose_json(args.get("inserts"))
    expected_rev = args.get("expected_scenes_revision")

    if not isinstance(inserts_raw, list) or not inserts_raw:
        raise AgentError("bad_arguments", "inserts 必须是非空数组")
    if not isinstance(expected_rev, str) or not expected_rev.strip():
        raise AgentError("bad_arguments",
                        "缺少 expected_scenes_revision（应先 story.list 拿到版本指纹再传回）")

    # atomic：先校验所有 insert 项与所有 spec，任一非法整批不写
    parsed: list = []
    seen_anchors: set = set()
    for i, it0 in enumerate(inserts_raw):
        it = _loose_json(it0)   # 数组项也可能被 stringify
        if not isinstance(it, dict):
            raise AgentError("bad_arguments", f"inserts[{i}] 必须是对象")
        before = (it.get("before_scene_id") or "").strip() or None
        after = (it.get("after_scene_id") or "").strip() or None
        if bool(before) == bool(after):
            raise AgentError(
                "bad_arguments",
                f"inserts[{i}] 必须且只能指定 before_scene_id / after_scene_id 之一")
        anchor = before or after
        if anchor in seen_anchors:
            raise AgentError(
                "bad_arguments",
                f"inserts[{i}] 锚点 {anchor} 在本批重复：同一批每个锚点只能出现一次"
                "（同一锚点要插多段，请合并到该项的 scenes 数组里按顺序给出）")
        seen_anchors.add(anchor)
        scenes_arr = _loose_json(it.get("scenes"))
        if not isinstance(scenes_arr, list) or not scenes_arr:
            raise AgentError("bad_arguments", f"inserts[{i}].scenes 必须是非空数组")
        specs = []
        for j, sc0 in enumerate(scenes_arr):
            sc = _loose_json(sc0)
            if not isinstance(sc, dict):
                raise AgentError("bad_arguments",
                                 f"inserts[{i}].scenes[{j}] 必须是对象")
            summary = sc.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                raise AgentError("bad_arguments",
                                 f"inserts[{i}].scenes[{j}].summary 不能为空")
            chars = _loose_json(sc.get("characters") or [])
            if not isinstance(chars, list):
                raise AgentError("bad_arguments",
                                 f"inserts[{i}].scenes[{j}].characters 必须是字符串数组")
            specs.append({"summary": summary.strip(),
                          "characters": [str(c).strip() for c in chars if str(c).strip()]})
        parsed.append({"before": before, "after": after, "specs": specs})

    pdir, pstore, proj = _load_proj(rt, project)
    current_rev = _scenes_token(proj.scenes)
    if expected_rev != current_rev:
        current_scenes = [{"scene_id": s.scene_id, "summary": (s.summary or "")[:80]}
                          for s in proj.scenes]
        return _base_receipt(
            "story.insert_many", ok=False, target="scenes",
            before=len(proj.scenes), after=len(proj.scenes),
            revision=current_rev, verified=False, error="stale_revision", stale=True,
            expected_revision=expected_rev, current_revision=current_rev,
            current_scenes=current_scenes,
            note="你读取的故事段列表已过期（期间被改动），未插入。上面是当前最新列表。"
                 "请基于最新列表重新解析锚点 scene_id，再用新的 expected_scenes_revision "
                 "调用 story.insert_many。")

    before_ids = [s.scene_id for s in proj.scenes]
    try:
        result = insert_scenes_at_anchors(proj, parsed)
    except StructureError as e:
        raise AgentError(e.code, e.message)
    new_scenes = result["new_scenes"]
    placements = result["placements"]
    new_ids = [sc.scene_id for sc in new_scenes]
    expected_final = result["final_order"]
    pstore.save(proj)

    # 写后重读 canonical 校验：最终顺序与快照语义一致 + 每个新段字段落盘
    on_disk = _reload(rt, project)
    disk_ids = [s.scene_id for s in on_disk.scenes]
    disk_by_id = {s.scene_id: s for s in on_disk.scenes}
    verified = disk_ids == expected_final
    if verified:
        for built in new_scenes:
            ds = disk_by_id.get(built.scene_id)
            if ds is None or ds.summary != built.summary \
                    or list(ds.characters or []) != list(built.characters or []):
                verified = False
                break
    if not verified:
        return _base_receipt(
            "story.insert_many", ok=False, target="scenes",
            before=len(before_ids), after=len(disk_ids),
            revision=_scenes_token(on_disk.scenes), verified=False,
            error="verify_failed",
            inserted=[{"scene_id": nid} for nid in new_ids],
            note="批量插入后重新读取未在 canonical 中确认最终顺序与新段一致落盘，未确认。"
                 "请用 story.list 读取当前列表后重试。")

    DirectEditAuditStore(pdir, project).record(
        agent_id="script_writer", field="scenes",
        before=before_ids,
        after=[{"scene_id": p["scene_id"], "anchor": p["anchor"], "side": p["side"]}
               for p in placements],
        instruction=instruction, message_id=message_id)
    rt.record_work(project, "change",
                   f"Script Writer 在 {len(parsed)} 个锚点批量插入 {len(new_ids)} 个故事段",
                   participants=["script_writer"], affected_domains=["storyboard"],
                   references={"scene_ids": new_ids}, status="completed")

    new_rev = _scenes_token(on_disk.scenes)
    inserted = [{"scene_id": p["scene_id"],
                 "order": disk_ids.index(p["scene_id"]) + 1,
                 "anchor": p["anchor"], "anchor_side": p["side"]}
                for p in placements]
    return _base_receipt(
        "story.insert_many", ok=True, target="scenes",
        before=len(before_ids), after=len(disk_ids), revision=new_rev, verified=True,
        inserted=inserted, count=len(inserted), scenes_revision=new_rev,
        note=f"已在 {len(parsed)} 个锚点插入 {len(inserted)} 段：{', '.join(new_ids)}。"
             "请确认结果后用一句话回复用户。")


# ============ Web Search（Tavily Hub，Runtime 受控）============
def _normalized_search_query(arguments: str) -> str:
    """web.search query 的轻量标准化（仅用于本 turn 重复检测）：
    解析失败/缺 query 返回 ""；否则去除全部空白、小写（中文查询里的空格是无意义填充）。
    只做完全相同/标准化相同的字面判断，不做任何语义相似判断。"""
    try:
        q = str((_parse_args(arguments) or {}).get("query") or "")
    except AgentError:
        return ""
    return re.sub(r"\s+", "", q).lower()


async def _execute_web_search(rt: AgentRuntime, project: str, arguments: str,
                              instruction: str = "", message_id: Optional[str] = None) -> dict:
    """web.search(query)：搜索现实世界资料（第一版 Provider：Tavily Hub 国内代理）。

    预算（每 user turn 最多 3 次真实搜索）由 _run_tool_loop 在 dispatch 前拦截计数；
    本 executor 只负责参数校验 + 调 Provider + 压缩 obs。Provider 的 timeout/429/5xx/
    网络失败/零结果一律映射为结构化 obs（绝不抛异常让 turn 崩溃）；obs 只含
    title/url/content(≤600字)/score，不含 raw_content/图片/完整原始 JSON/AI answer。
    """
    try:
        args = _parse_args(arguments)
    except AgentError as e:
        return {"ok": False, "operation": "web.search", "code": e.code,
                "error": e.message, "target": ""}
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "operation": "web.search", "code": "bad_arguments",
                "error": "query 不能为空，请给一个聚焦的搜索词", "target": ""}
    if len(query) > 200:
        return {"ok": False, "operation": "web.search", "code": "bad_arguments",
                "error": "query 过长（>200 字），请拆成更聚焦的查询", "target": query[:60]}
    res = await _get_web_search_client().search(query)
    if not res.get("ok"):
        return {"ok": False, "operation": "web.search",
                "code": res.get("code", "network_error"),
                "error": res.get("error", "搜索失败"), "target": query[:60]}
    n = res.get("n_results", 0)
    obs = {"ok": True, "operation": "web.search", "target": query[:60],
           "query": query, "n_results": n,
           "results": res.get("results", []),
           "sources": res.get("sources", [])}
    if n == 0:
        obs["note"] = ("没有找到相关资料。可换一个更聚焦的查询词重试（注意每轮搜索次数上限），"
                       "或基于已有知识继续。")
    return obs


TOOL_EXECUTORS = {
    "script.read": _execute_script_read,
    "script.update": _execute_script_update,
    "story.list": _execute_story_list,
    "story.read": _execute_story_read,
    "story.create": _execute_story_create,
    "story.move": _execute_story_move,
    "story.update": _execute_story_update,
    "story.delete": _execute_story_delete,
    "story.create_many": _execute_story_create_many,
    "story.update_many": _execute_story_update_many,
    "story.delete_many": _execute_story_delete_many,
    "story.insert_many": _execute_story_insert_many,
    "web.search": _execute_web_search,
    # Skill System V1（40.md）：skill.list/load/read_resource（只读）
    **make_skill_executors("script_writer"),
}


# 用户确认意图的措辞（肯定删除/继续删除）。否定（"先别删/取消"）不匹配。
_CONFIRM_DELETE_RE = re.compile(
    r"确认|确定|删吧|删除|可以|好的|继续|执行|同意|yes|ok|确认删除|确定删除", re.IGNORECASE)
_NEGATE_DELETE_RE = re.compile(r"先别|不要|取消|不删|算了|放弃|undo|cancel", re.IGNORECASE)


async def _maybe_auto_confirm_deletes(rt: AgentRuntime, project: str,
                                       session, user_content: str,
                                       message_id: Optional[str]):
    """删除两阶段兜底：若上一条 assistant 带有 pending_delete_scenes（预览未确认），
    且本轮用户内容是确认（非取消），则由 Runtime 直接执行 confirmed=true 的级联删除。

    返回 (receipts, trace, any_deleted)。无待确认/非确认 → ([], [], False)。
    这是结构性兜底：模型在收到「确定删除」后常常不发 confirmed=true 工具调用（空转），
    导致「确认了却没删」。用户的明确确认已存在，Runtime 兑现它，不依赖模型自主补发。"""
    msgs = session.messages if hasattr(session, "messages") else []
    # 找上一条 assistant（带 meta.pending_delete_scenes）
    pending: list = []
    for m in reversed(msgs[:-1] if msgs else []):
        if m.role == "assistant":
            meta = m.meta or {}
            pending = list(meta.get("pending_delete_scenes") or [])
            break
        if m.role == "user":
            break   # 连续两条 user（不该发生），保守退出
    if not pending:
        return [], [], False
    # 用户内容必须是确认、且非取消
    if _NEGATE_DELETE_RE.search(user_content):
        return [], [], False
    if not _CONFIRM_DELETE_RE.search(user_content):
        return [], [], False

    # 执行已确认删除：逐个 confirmed=true（复用 _execute_story_delete）。
    receipts: list = []
    trace: list = []
    any_deleted = False
    for i, sid in enumerate(pending):
        args = json.dumps({"scene_id": sid, "confirmed": True}, ensure_ascii=False)
        try:
            obs = _execute_story_delete(rt, project, args, user_content, message_id)
        except AgentError as e:
            obs = {"ok": False, "error": e.message, "code": e.code,
                   "operation": "story.delete", "scene_id": sid, "verified": False}
        else:
            obs.setdefault("scene_id", sid)
        obs.setdefault("operation", "story.delete")
        receipts.append(obs)
        trace.append(_trace_entry(i, "story.delete", args, obs))
        if obs.get("ok") and obs.get("verified"):
            any_deleted = True
    return receipts, trace, any_deleted


# ============ 主 turn ============
async def script_writer_turn(rt: AgentRuntime, project: str, session_id: str,
                             content: str, *, thinking: bool = False,
                             event_cb=None, cancel_event=None) -> dict:
    """Script Writer 原生 tool-loop turn。

    返回 {assistant_message, edited, created, moved, events, message, ...}。
    thinking/event_cb 仅流式端点 /turn/stream 传入（snapshot 固定）；默认值时行为与
    既有 /turn 完全一致（非流式 chat_completion + 无事件）。
    cancel_event（可选 asyncio.Event）：协作式停止——置位后在工具循环【步骤边界】
    安全中断（不打断当前单次调用/原子写），已完成的写保留，按 STOPPED 诚实收尾。"""
    store = rt._session_store(project)
    session = store.load(session_id)
    if session is None:
        raise AgentError("unknown_scope", f"session 不存在: {session_id}")
    if session.agent_id != "script_writer":
        raise AgentError("permission_denied", "该会话仅限 Script Writer")
    if not rt._is_enabled("script_writer"):
        raise AgentError("disabled_agent", "Script Writer 已禁用")

    user_msg = AgentMessage(role="user", content=content)
    store.append_message(session_id, user_msg)
    rt._maybe_capture_preference(project, content, user_msg.message_id)
    session = store.load(session_id)

    pdir = rt._resolve_project_dir(project)
    # 会话治理（best-effort，不影响主流程）：项目首个 ≥10 字符用户消息 → 创作 brief，
    # 供 Clean Review / Re-grounding 注入用户硬约束（STRESS §8 案例1 的盲区修复）。
    from .session_governance import capture_brief_if_absent
    capture_brief_if_absent(pdir, project, content, "script_writer")
    history = [{"role": m.role, "content": m.content}
               for m in session.messages[:-1]
               if m.role in ("user", "assistant") and m.content and m.status is None]

    # 模型切换（2026-08-29）：按 script_writer 绑定的 ModelProfile 选择文本客户端
    # （默认 Agnes；输入区开关绑定 SenseNova 档案后，下一次 turn 起走 SenseNova）
    client = rt._resolve_client_or_none(agent_id="script_writer")
    assistant_text: str
    status: Optional[str] = None
    edited = created = moved = scene_updated = deleted = False
    events: list = []
    receipts: list = []
    reasoning: list = []
    trace: list = []
    search_sources: list = []   # 本轮 web.search 的轻量来源（title+url，无正文）

    if client is None:
        assistant_text = "[chat 未配置：Agnes 推理模型不可用，无法修改剧本。请在设置中配置 API Key。]"
        status = ResultStatus.NOT_CONFIGURED.value
    else:
        proj = ProjectStore(pdir).load()
        system = _build_system(proj)
        mem = rt._memory_supplement(project, "script_writer")
        if mem:
            system += "\n\n" + mem
        messages: list[dict] = [{"role": "system", "content": system}]
        messages += history
        # Skill System V1（40.md §七）：turn 入口确定性 trigger hint（不落盘）
        _skill_hint = _skill_activation_hint("script_writer", content, pdir)
        messages.append({"role": "user", "content":
                         (_skill_hint + "\n\n" + content) if _skill_hint else content})

        # —— 删除两阶段：用户已确认但模型上轮没发 confirmed=true 的兜底 ——
        # 上一条 assistant 若有 pending_delete_scenes（预览未确认），且本轮 user 内容像确认，
        # 则【由 Runtime 直接执行已确认的删除】（兑现用户已给的明确确认，不依赖模型自主补发）。
        pre_exec_receipts: list = []
        if client is not None:
            pre_exec_receipts, pre_trace, pre_deleted = await _maybe_auto_confirm_deletes(
                rt, project, session, content, user_msg.message_id)
            if pre_exec_receipts:
                receipts.extend(pre_exec_receipts)
                trace.extend(pre_trace)
                if pre_deleted:
                    deleted = True
                # 把执行结果作为 system 说明喂给模型，让它据此总结（而非自己声称已删）
                done_ids = [r.get("scene_id") for r in pre_exec_receipts
                            if r.get("ok") and r.get("verified")]
                fail_ids = [f"{r.get('scene_id')}({r.get('error')})"
                            for r in pre_exec_receipts if not r.get("ok")]
                note = ("用户已确认删除，系统已直接执行确认删除。真实结果："
                        + (f"成功删除 {', '.join(done_ids)}。" if done_ids else "")
                        + (f"失败：{', '.join(fail_ids)}。" if fail_ids else "")
                        + "请据此向用户如实总结，不要重复调用删除工具。")
                messages.append({"role": "tool", "tool_call_id": "auto_confirm_delete",
                                 "content": json.dumps({"ok": True, "auto_executed": True,
                                                        "deleted_scenes": done_ids,
                                                        "failed": fail_ids,
                                                        "note": note}, ensure_ascii=False)})
                messages.append({"role": "assistant", "content": note})

        loop_state: dict = {}   # 异常时仍可取回已执行的 trace/receipts/events（可观测性）
        try:
            final = await _run_tool_loop(rt, client, project, messages, content, user_msg.message_id,
                                         thinking=thinking, event_cb=event_cb,
                                         state=loop_state, cancel_event=cancel_event)
            assistant_text = final["text"]
            edited = final["edited"]
            created = final["created"]
            moved = final["moved"]
            scene_updated = final.get("scene_updated", False)
            # 合并预执行删除结果与 tool-loop 结果
            if final.get("deleted"):
                deleted = True
            events = final["events"]
            receipts = pre_exec_receipts + final.get("receipts", []) if pre_exec_receipts else final.get("receipts", [])
            trace = (pre_trace + final.get("trace", [])) if pre_trace else final.get("trace", [])
            reasoning = final.get("reasonings", [])
            search_sources = final.get("search_sources", [])
        except _TurnStopped:
            # 协作式停止（用户点停止）：在步骤边界安全中断。已完成的写全部真实落盘，
            # 未完成部分不执行。诚实收尾，账本来自 loop_state（与失败路径同样保留）。
            log.info("script_writer_turn 被停止（project=%s session=%s）", project, session_id)
            done = _committed_scene_ids(loop_state.get("receipts", []))
            done_str = ("已完成：" + "、".join(done)) if done else "本次没有已完成的修改"
            assistant_text = f"[任务已停止] {done_str}；其余未完成部分未执行。"[:300]
            status = ResultStatus.STOPPED.value
            events = loop_state.get("events", [])
            receipts = pre_exec_receipts + loop_state.get("receipts", [])
            trace = (pre_trace + loop_state.get("trace", [])) if pre_trace else loop_state.get("trace", [])
            reasoning = loop_state.get("reasonings", [])
            search_sources = loop_state.get("search_sources", [])
        except Exception as e:
            # 可观测性：完整 traceback 进日志（定位网络断/服务端错误的真实身份）；
            # str(e) 可能为空（如 TimeoutError），文本带异常类型名保证用户可见失败原因。
            log.exception("script_writer_turn 工具循环异常（project=%s session=%s）",
                          project, session_id)
            e_desc = str(e).strip()
            assistant_text = f"[执行失败: {type(e).__name__}: {e_desc}]"[:300]
            status = ResultStatus.FAILED.value
            # 保留已执行的部分（哪些写已真实落盘），不再清空成黑盒；
            # bool 标志保持 False（intent 退回 conversation，失败态下不猜测变更类别）。
            events = loop_state.get("events", [])
            receipts = pre_exec_receipts + loop_state.get("receipts", [])
            trace = (pre_trace + loop_state.get("trace", [])) if pre_trace else loop_state.get("trace", [])
            reasoning = loop_state.get("reasonings", [])
            search_sources = loop_state.get("search_sources", [])

    if created:
        intent = "story_create"
    elif moved:
        intent = "story_move"
    elif scene_updated:
        intent = "story_update"
    elif deleted:
        intent = "story_delete"
    elif edited:
        intent = "script_update"
    else:
        intent = "conversation"

    # AI-7D-R 真实性收口：meta 带本轮写工具真实回执（成功/失败分类），供上层/前端核对，
    # 不依赖 assistant 自然语言。write_success 只算 verified=true（删除预览 ok=true 但
    # verified=false 不算真实变更）。pending_delete_scenes 携带本轮【预览但未确认】的
    # 删除目标，供下一轮检测「用户确认后自动执行」。
    receipts_meta = []
    pending_delete_scenes: list = []
    for r in receipts:
        is_pending_delete = (r.get("operation") == "story.delete"
                             and r.get("ok") and not r.get("verified")
                             and not r.get("confirmed"))
        if is_pending_delete:
            wd = r.get("will_delete") or {}
            for sid in (wd.get("scenes") or []):
                if sid and sid not in pending_delete_scenes:
                    pending_delete_scenes.append(sid)
        receipts_meta.append({
            "operation": r.get("operation"), "ok": bool(r.get("ok")),
            "verified": bool(r.get("verified")), "stale": bool(r.get("stale")),
            "unchanged": bool(r.get("unchanged")),
            "confirmed": bool(r.get("confirmed")),
            "error": r.get("error"),
            "scene_id": r.get("scene_id"), "order": r.get("order"),
            "revision": r.get("revision"),
        })
    meta = {"intent": intent, "events": events, "evidence_refs": [],
            "receipts": receipts_meta,
            "write_attempts": len(receipts_meta),
            "write_success": sum(1 for r in receipts_meta
                                 if r["ok"] and r["verified"] and not r["unchanged"]),
            "trace": trace,   # 执行轨迹（需求 #7）：每步 tool→target→result→revision
            "reasonings": reasoning,   # 每步思考文本（持久化，前端刷新后重建折叠条 reasoning）
            "search_sources": search_sources,   # web.search 轻量来源（title+url，供 UI 展示；无正文）
            # Skill System V1（40.md §十三）：本 turn 技能激活/加载证据
            "skills": _get_skill_runtime().collect_and_reset("script_writer"),
            "pending_delete_scenes": pending_delete_scenes}   # 本轮预览未确认的删除目标
    asst = AgentMessage(role="assistant", content=assistant_text, status=status, meta=meta)
    store.append_message(session_id, asst)

    return {"assistant_message": assistant_text, "edited": edited,
            "created": created, "moved": moved, "scene_updated": scene_updated,
            "deleted": deleted,
            "events": events, "message": asst.to_dict(),
            "intent": intent, "receipts": receipts_meta, "trace": trace,
            # Phase 46：原始回执（含 created/results 等机器可读 id 字段）。
            # receipts_meta 是给会话 meta/UI 的白名单投影（剥掉富字段）；
            # 委派底座（_wrap_task_result → _derive_changed_refs）必须吃原始回执，
            # 否则 story.create_many / story.update_many 的产物 id 会丢失。
            "receipts_raw": receipts,
            "search_sources": search_sources,
            "evidence_refs": [], "linked_task_ids": [],
            "linked_change_session_id": None, "session_id": session_id,
            "agent_id": "script_writer"}


# 「已完成」措辞检测：匹配过去/完成态（已创建、插入了、移到最前面了、移动完成、成功更新…），
# 不匹配提议/将来态（「我可以帮你创建」），避免对纯对话误触发。
# 动词与「了/完成」之间允许最多 12 字（如「移到最前面了」「把 s03 移动到第一位了」）。
_VERBS = (r"创建|新建|插入|添加|追加|加好|移动|移到|移至|更新|调整|"
          r"修改|改写|改正|改好|改到|重写|续写|删除|删掉|拆分|拆成|拆好|拆完|划分|分段|"
          # 2026-08-30 真机补词（Character Designer R2 假成功漏网）：「已将X改为Y」「换成/设成/加上」
          # 同样是完成态声称，此前不在动词表导致真相闸门漏拦。对既有路径纯增强：闸门只对
          # 「声称成功但零真实变更」触发，聊天轮保护不变。
          r"改为|改成|换为|换成|设为|设成|加上")
_SUCCESS_CLAIM_RE = re.compile(
    rf"(已(经)?\s*(在|把|将)?[^。！？\n]{{0,8}}?({_VERBS})|"
    rf"({_VERBS})[^。！？\n]{{0,12}}了|"
    rf"({_VERBS})?\s*完成|"
    rf"成功\s*({_VERBS})|"
    rf"(created|inserted|moved|updated|added|done|complete[dw]?)\b)",
    re.IGNORECASE)

# 否定式：诚实回答「没有/未/并未 创建/移动/完成」不应被当成声称成功。
_NEGATION_RE = re.compile(
    r"(没有|没|并未|并没有|尚未|未|未能|无法|不能)\s*"
    rf"({_VERBS}|完成|成功)?",
)


# 聊天/探讨语气标记（AI-7D-R 交互路由）：【仅】用于真相闸门是否「上膛」，不参与任何
# 路由或分类。用户本轮在提问/征求看法/讨论/回顾时，助手对【过往轮次】工作的完成态描述
# （如「我上一轮把 s08 改了」）不是「本轮谎报」——本轮本来就没有修改请求。此时不发
# 「请调用工具」纠正 nudge、不覆写兜底文案。编辑请求（把/给/帮我 + 动作、无探讨标记）
# 不受影响，闸门照常捕获假成功。标记宁窄勿宽：拿不准是否聊天时一律按编辑轮处理。
_CHAT_TURN_RE = re.compile(
    r"为什么|怎么看|怎么样|怎么办|好不好|看法|觉得|认为|讨论|聊聊|聊一下|说说|"
    r"解释|原因|什么问题|有没有问题|合适吗|行不行|可以吗|好吗|快不快|慢不慢|"
    r"对吗|是吗|是这样吗|如何|做了什么|干了什么|是什么|有什么|不用修改|不要修改|先别改")


def _is_chat_turn(instruction: str) -> bool:
    """用户本轮消息是否为探讨/提问语气（仅用于闸门上膛保护，宁窄勿宽）。"""
    return bool(_CHAT_TURN_RE.search(instruction or ""))


# —— 搜索谎报检测（web.search 真实性收口）——
# 模型可能不调用 web.search 却在回复里编造「搜索结果/已搜索/查到N条」（真机验收 F 场景
# 实测两次）。闸门动词表（_VERBS）只覆盖写操作，这里单独识别「声称搜索成功/有搜索结果」，
# 由 _run_tool_loop 对照本轮真实 web.search trace 收口。先定点剔除否定/失败搭配（「没有
# 搜索到」「搜索失败」是诚实表述；只删否定词与搜索词的紧邻搭配，不动整句），再匹配肯定式声明。
_SEARCH_CLAIM_STRIP_RE = re.compile(
    r"(没有|没|并未|并没有|尚未|未|未能|无法|不能)\s*(发起了?|完成了?|执行了?|使用了?)?\s*(了)?\s*"
    r"(web\.?search|搜索|查询|检索|联网查)[^。！？\n]{0,20}"
    r"|(搜索|查询|检索|联网)(失败|未完成|不完整|受限|超时|被拒|出错|错误)[^。！？\n]{0,20}"
    r"|(因|由于)[^。！？\n]{0,16}(不足|超限|失败|耗尽)[^。！？\n]{0,20}",
    re.IGNORECASE)
_SEARCH_CLAIM_RE = re.compile(
    r"已(经)?\s*(发起|完成|执行|使用)?\s*(了)?\s*(web\.?search|搜索|查询|检索|联网查)|"
    r"(搜索|查询|检索)(了|完成|成功)|"
    r"(搜|查)(到|出)了|"
    r"(找到|查到)\s*\d+\s*(个|条|篇)|"
    r"(web\.?search|搜索).{0,8}(结果|来源|资料)(如下|汇报|：)",
    re.IGNORECASE)


def _claims_search_success(text: str) -> bool:
    """回复是否声称本轮发生过真实搜索（剔除否定/失败句段后仍命中肯定式声明）。"""
    stripped = _SEARCH_CLAIM_STRIP_RE.sub("", text or "")
    return bool(_SEARCH_CLAIM_RE.search(stripped))


# zero-write recovery（极窄兜底）专用：判定用户本轮是否【明确要求实际写入/修改】。
# 不做 LLM 分类、不看模型回复措辞（「已修改」动词正则永远补不全——假成功与纯讨论
# 都会漏），只看用户消息本身：探讨/提问语气优先排除（宁漏勿误，不破坏聊天）；
# 强祈使结构（把…/给…+动作、只改/直接改/按你说的做）即使带「你觉得」也算写入请求，
# 如「把你觉得最平淡的那句润色一下，只改第2段」——但整句以问号结尾时仍按探讨处理。
_DISCUSSION_RE = re.compile(
    r"为什么|怎么看|怎么样|怎么办|好不好|看法|觉得|认为|讨论|聊聊|聊一下|说说|"
    r"解释|原因|什么问题|有没有问题|合适吗|行不行|可以吗|好吗|快不快|慢不慢|"
    r"对吗|是吗|是这样吗|要不要|是不是|需不需要|该不该|能不能|会不会|是否|如何|"
    r"做了什么|干了什么|做了哪些|干了哪些|哪些|是什么|有什么|"
    r"总结一下|总结下|回顾一下|说说|不用修改|不要修改|先别改|先不要|先别")

_STRONG_IMPERATIVE_RE = re.compile(
    r"把[^。？?！!]{0,40}(改|润色|重写|续写|扩写|设|加|添|删|移|挪|换|拆|调|压缩)|"
    r"给[^。？?！!]{0,20}(加|添|改|删|设)|"
    # 2026-08-30 真机补词：「让X更Y一些。」（无问号）是明确的修改请求（Character Designer
    # R2 案例），此前零写 recovery 不上膛。问句仍被 _DISCUSSION_RE 保护（探讨语气优先）。
    r"让[^。？?！!]{0,20}(更|变得|改为|改成|换成|设为|设成)|"
    r"只改|直接改|帮我改|帮我把|改吧|就这么改|按你说的做|按你说的改|开始改|动手改")

_WRITE_REQUEST_RE = re.compile(
    r"改成|改为|改一下|改下|改写|修改|润色|重写|续写|扩写|压缩|"
    r"新增|增加|添加|加上|加个|加一?段|加一?[个句名点些]|添一?段|插入|创建|新建|建一?段|"
    r"删除|删掉|删了|移除|去掉|"
    r"移动|移到|移至|挪到|互换|交换|调换|换一下|"
    r"设为|设置成|设成|"
    r"拆分|拆成|分段|分成[两三1-9]段|"
    # 2026-08-30 真机补词（Character Designer：「给林远生成三视图」被误判为讨论）：
    # 生成/绘制/出图同样是明确执行请求。
    r"调整|改吧|就这么改|按你说的做|按你说的改|"
    r"生成|绘制|出图|画(出|一张|一?个)")


def _is_explicit_write_request(instruction: str) -> bool:
    """用户本轮是否明确要求实际写入/修改项目（zero-write recovery 专用，宁漏勿误）。"""
    t = (instruction or "").strip()
    if not t:
        return False
    is_question = ("?" in t or "？" in t)
    # 2026-08-30：强祈使（把/给/让…+动作）提前判定——此前只在内含探讨词时才检查，
    # 「让他的眼神更锐利一些」这类不含常规写动词的祈使句会漏判（真机 R2 案例）。
    # 问句仍不算（探讨语气保护不变）。
    if not is_question and _STRONG_IMPERATIVE_RE.search(t):
        return True
    if _DISCUSSION_RE.search(t):
        # 探讨语气默认排除；强祈使且整句非问句（无问号）时仍算写入请求
        if not is_question and _STRONG_IMPERATIVE_RE.search(t):
            return True
        return False
    return bool(_WRITE_REQUEST_RE.search(t))


_ZERO_WRITE_RECOVERY_NUDGE = (
    "【系统提示】用户本轮明确要求实际修改项目内容，但你到目前为止【还没有发起任何写工具"
    "调用】——在回复里用自然语言描述修改不等于完成修改。请立即：①先用只读工具"
    "（story.list / story.read；正文任务用 script.read）确认当前真实状态；②真正发起对应"
    "的写工具（story.create / story.update / story.move / story.delete / story.create_many / "
    "story.update_many / story.insert_many / story.delete_many / script.update）完成用户要求，"
    "等工具真实返回 ok=true 后再汇报结果；写工具一个一个来，每次用上一次返回的最新版本指纹。"
    "如果你确实无法执行（需求不清楚、目标不存在等），就明确告诉用户原因，【不要】在没有"
    "工具真实返回的情况下声称已经修改完成。")


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


def _claims_success(text: str) -> bool:
    """仅当存在完成态声称、且该声称不在否定语境中才判定为「声称成功」。
    用最近的一次匹配向前查找否定词，避免「我没移动，但我创建了」这类被全句否定误伤。"""
    if not text:
        return False
    for m in _SUCCESS_CLAIM_RE.finditer(text):
        start = m.start()
        window = text[max(0, start - 6):start]
        if _NEGATION_RE.search(window):
            continue
        return True
    return False


def _claimed_count_mismatch(text: str, real_count: int) -> Optional[int]:
    """检测文本里「本次创建/拆分了 N 段」的 N 是否与真实成功创建数不符。
    返回文本里第一个与 real_count 不一致的数量（用于纠偏）；一致或无→None。

    抠两种计数表述：
      (1) 动词在前：「已创建 3 段 / 拆成 3 段 / 建了 3 个故事段」
      (2) 数字在前 + 创建完成态：「3 段都建好了 / 3 段创建完成」
    不抠「现共 4 段/现有 4 段」这类【总数】——总数≠本次新建数，属正常表述。"""
    t = text or ""
    # (1) 动词→数字→段
    m = re.search(r"(?:创建|新建|拆成|拆分|建|新增)[^0-9]{0,6}?(\d+)\s*(?:个)?\s*(?:故事)?段", t)
    if m:
        v = int(m.group(1))
        if v != real_count:
            return v
    # (2) N 段…（建好/创建/完成）
    m = re.search(r"(\d+)\s*(?:个)?\s*(?:故事)?段[^。！？\n]{0,8}?(?:建好|创建|建成|完成|都建)",
                  t)
    if m:
        v = int(m.group(1))
        if v != real_count:
            return v
    return None


def _refresh_system(messages: list, rt: AgentRuntime, project: str) -> None:
    try:
        pdir = rt._resolve_project_dir(project)
        proj = ProjectStore(pdir).load()
        messages[0] = {"role": "system", "content": _build_system(proj)}
    except Exception:
        pass


def _ledger_brief(receipts: list) -> str:
    """把本轮真实写工具结果渲染成给模型看的账本（确定性，来自回执而非模型叙述）。"""
    if not receipts:
        return ("本轮没有任何写工具被真正调用（没有 script.update / story.create / "
                "story.move 的工具调用记录）。")
    lines = []
    for r in receipts:
        op = r.get("operation")
        if r.get("ok"):
            if r.get("unchanged"):
                lines.append(f"- {op}：ok=true，但内容无变化（unchanged），canonical 未改动。")
            else:
                extra = ""
                if r.get("scene_id"):
                    extra = f"（{r['scene_id']}，第 {r.get('order')} 位）"
                elif r.get("new_len") is not None:
                    extra = f"（{r.get('old_len')}→{r.get('new_len')} 字）"
                lines.append(f"- {op}：ok=true{extra}，已重新读取 canonical 确认落盘。")
        else:
            why = r.get("error") or "未知错误"
            if r.get("stale"):
                why = "stale（版本过期，被拒绝，未写入）"
            lines.append(f"- {op}：ok=false，原因：{why}。canonical 未被该调用改动。")
    return "\n".join(lines)


def _op_human(r: dict) -> str:
    """把一条真实回执渲染成人类可读的操作描述（确定性，来自回执而非模型叙述）。"""
    op = r.get("operation")
    sid = r.get("scene_id")
    if op == "story.create":
        return f"创建故事段 {sid}（第 {r.get('order')} 位）"
    if op == "story.move":
        return f"移动故事段 {sid} 到第 {r.get('order')} 位"
    if op == "story.update":
        ch = r.get("changed") or []
        return f"修改故事段 {sid}（{'、'.join(ch) or '内容'}）"
    if op == "story.create_many":
        ids = [c.get("scene_id") for c in (r.get("created") or []) if c.get("scene_id")]
        return f"批量创建 {len(ids)} 段故事段（{', '.join(ids)}）" if ids else "批量创建故事段"
    if op == "story.insert_many":
        ids = [c.get("scene_id") for c in (r.get("inserted") or []) if c.get("scene_id")]
        return f"多锚点批量插入 {len(ids)} 段故事段（{', '.join(ids)}）" if ids else "批量插入故事段"
    if op == "story.update_many":
        n = len(r.get("results") or [])
        return f"批量修改 {n} 段故事段"
    if op == "story.delete_many":
        items = r.get("deleted") or []
        ids = [c.get("scene_id") for c in items if c.get("scene_id")]
        n_shots = sum(len(c.get("shots") or []) for c in items)
        extra = f"及 {n_shots} 个镜头" if n_shots else ""
        return (f"批量删除 {len(ids)} 段故事段（{', '.join(ids)}）{extra}"
                if ids else "批量删除故事段")
    if op == "story.delete":
        d = r.get("deleted") or {}
        extra = f"及 {len(d.get('shots', []))} 个镜头" if d.get("shots") else ""
        return f"删除故事段 {sid}{extra}"
    if op == "script.update":
        return f"更新剧本正文（{r.get('old_len')}→{r.get('new_len')} 字）"
    return f"{op}({sid or ''})"


def _honest_fallback(receipts: list) -> str:
    """纠偏后模型仍谎称成功时的确定性兜底文案——纯由真实回执生成，绝不采信模型自述。"""
    real_changes = [r for r in receipts if r.get("ok") and r.get("verified") and not r.get("unchanged")]
    if real_changes:
        return "已完成的修改：" + "；".join(_op_human(r) for r in real_changes) + "。"
    if receipts:
        reasons = []
        for r in receipts:
            if r.get("stale"):
                reasons.append("内容版本已过期")
            elif r.get("error") == "verify_failed":
                reasons.append("写入后校验未通过")
            else:
                reasons.append(r.get("error") or "工具执行失败")
        uniq = "、".join(dict.fromkeys(reasons))
        return (f"这次修改没有真正生效（{uniq}）。项目内容保持原样。"
                f"可以让我重试，或先让我读取当前最新内容再改。")
    return "这次我没有真正执行修改（没有发出对应的工具调用）。请再说一次你的需求，我会实际操作。"


_FACT_SUMMARY_MARK = "【实际执行记录】"


def strip_reserved_fact_blocks(text: str) -> str:
    """删除模型仿写的系统保留【实际执行记录】块（在 _append_fact_summary 追加真实
    记录【之前】调用）。

    真实块由 _append_fact_summary 在本函数之后追加，故此刻 text 中任何含
    【实际执行记录】标记的块都是模型伪造（模型甚至可能抄走"（系统据真实回执
    生成，非模型叙述）"声明）。确定性删除以防：
      - UI 出现"模型伪造 + 系统真实"重复块；
      - 模型冒充系统声明；
      - 假记录进入下一轮 history 污染上下文。

    保留块结构 = 可选 --- 分隔符 + 标题行 + 若干 ✓/✗/当前故事段 内容行。
    模型若在伪造块【之后】写了正常正文，该正文保留。"""
    if not text or _FACT_SUMMARY_MARK not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _FACT_SUMMARY_MARK in lines[i]:
            # 去掉紧邻的前导 --- 分隔符（若有）
            if out and out[-1].strip() == "---":
                out.pop()
            i += 1  # 跳过标题行
            # 跳过后续事实块内容行（空行 / ✓ / ✗ / ● / 当前故事段）
            while i < len(lines):
                s = lines[i].strip()
                if (s == "" or s.startswith(("✓", "✗", "✘", "●"))
                        or s.startswith("当前故事段")):
                    i += 1
                else:
                    break
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _append_fact_summary(rt: AgentRuntime, project: str, text: str, receipts: list) -> str:
    """有真实变更时，在模型回复后追加【确定性事实摘要】——由真实 receipts + 当前
    canonical 生成，不信模型叙述。彻底解决：
      - 模型说"改了A"实际改了B（描述不符）
      - 模型说"没改"实际改了（[129]→[131] 场景）
      - 用户/下一轮无法核对真实结果

    覆盖全成功/部分成功/全失败三态：已成功资产明确列出并保留，未完成项也如实标注。
    纯追加，不删改模型原文；若模型已诚实且准确，摘要是冗余但无害的确认。"""
    if not receipts:
        return text
    real_changes = [r for r in receipts if r.get("ok") and r.get("verified") and not r.get("unchanged")]
    failed = [r for r in receipts if not r.get("ok")]
    # Script Writer 无视觉生成操作：纯 CRUD 失败且无成功变更（如删除不存在的段、stale 被拒）
    # 不追加事实块（失败原因由错误信息/真相闸门体现）；有成功变更才列 ✓/✗ 与计数。
    if not real_changes:
        return text
    try:
        proj = _reload(rt, project)
        scenes = proj.scenes or []
        scene_list = [s.scene_id for s in scenes]
        lines = ["\n\n---\n【实际执行记录】（系统据真实回执生成，非模型叙述）"]
        for r in real_changes:
            lines.append(f"✓ {_op_human(r)}")
        for r in failed:
            why = r.get("error") or r.get("code") or "失败"
            if r.get("stale"):
                why = "版本过期，已拒绝"
            elif r.get("cancelled"):
                why = "已取消"
            sid = r.get("scene_id") or ""
            lines.append(f"✗ {r.get('operation', '操作')}{(' ' + sid) if sid else ''}：{why}")
        # 确定性完成情况总结行（不让模型猜）
        if failed and not real_changes:
            lines.append(f"\n本次没有完成生成（{len(failed)} 项未成功）。")
        elif failed and real_changes:
            lines.append(f"\n已完成 {len(real_changes)} 项，{len(failed)} 项未完成/取消。")
        else:
            lines.append(f"\n已完成 {len(real_changes)} 项。")
        lines.append(f"\n当前故事段共 {len(scenes)} 个：{', '.join(scene_list) if scene_list else '（空）'}")
        return text + "\n".join(lines)
    except Exception:
        return text


async def _run_tool_loop(rt: AgentRuntime, client, project: str,
                         messages: list, instruction: str,
                         message_id: Optional[str],
                         max_steps: int = DEFAULT_MAX_TOOL_STEPS,
                         thinking: bool = False,
                         event_cb=None,
                         state: Optional[dict] = None,
                         cancel_event=None) -> dict:
    """原生 function-calling 循环 + 执行真实性收口（结构性，非提示词）。

    每轮：
      1. 执行模型发起的 tool_calls，每个写工具返回统一回执（写后重读 canonical 校验）。
      2. 维护本轮 receipts 账本（区分 success/unchanged/stale/error/verify_failed/not_called）。
      3. 模型无 tool_call 给出最终文本时，做真相闸门：若文本声称「已创建/移动/更新」但账本里
         没有任何 ok=true 真实变更回执，则把真实账本作为纠正消息喂回，要求模型基于真实 tool
         result 重答（可重试工具），最多 MAX_CORRECTIVE_ROUNDS 次；仍谎称则 Runtime 确定性覆写。

    max_steps 仅防死循环（LLM 循环步数），非业务数量限制；批量拆分等长任务由调用方上调。
    """
    edited = created = moved = scene_updated = deleted = False
    summary = ""
    # 共享容器：state 为空 dict 时也是真容器（空 dict falsy，不能用 or 短路，
    # 否则 setdefault 会写进临时 dict、调用方永远拿不到）。
    if state is None:
        state = {}
    events: list = state.setdefault("events", [])
    receipts: list = state.setdefault("receipts", [])   # 本轮所有写工具调用的真实回执（成功与失败都记）
    write_attempts = 0           # 写工具被调用的次数（含失败）
    corrective_round = 0
    # zero-write recovery（极窄兜底）：用户明确要求写入但本轮零写尝试时，注入一次恢复
    # 提示。turn-local 一次性，不持久化、不占 MAX_CORRECTIVE_ROUNDS 预算（恢复成功后
    # 不应误伤既有 needs_final_read 等纠偏）。只管「根本没尝试写」，write_attempts>0
    # 的 stale/error/失败一律走既有路径。
    zero_write_recovery_used = False
    # 执行轨迹（需求 #7）：每个工具调用（读+写）一条 {step,tool,target,result,revision}。
    # 供调试「为什么移动三次才成/为什么多建一段」——只随 turn 结果返回，不进 system prompt。
    trace: list = state.setdefault("trace", [])
    # AI-7D-R reasoning 持久化：每个 step 的思考文本累积，turn 结束随 meta 落盘，
    # 供前端刷新后重建执行过程折叠条的 reasoning 部分（tool 从 trace 重建、reasoning 从此重建）。
    reasonings: list = state.setdefault("reasonings", [])
    last_read_after_write = -1   # 最后一次只读调用所在的 step（用于最终完成判定）
    # Web Search（第一版）：loop-local 搜索计数（每 user turn 随循环销毁自动重置），
    # 硬上限 MAX_WEB_SEARCH 由 Runtime 在 dispatch 前拦截，不依赖 prompt 自觉。
    # search_sources 只收 title+url（随 turn 落盘 meta.search_sources 供 UI 展示来源），
    # snippet/网页正文绝不进 trace/meta——搜索内容只在当前 tool-loop 的 tool message
    # 里存在，下一轮 LLM history 重建时不含它们（不长期污染 context）。
    search_count = 0
    search_sources: list = state.setdefault("search_sources", [])
    # 本 turn 已发起过的搜索词（标准化后）。Hub 对完全相同 query 会返回空结果（缓存缺陷），
    # 重复搜索纯属浪费预算：完全相同/标准化相同直接拒绝，不计数、不调 Provider。
    search_queries_seen: set = state.setdefault("search_queries_seen", set())
    # 防失控（需求 #11）：连续同一工具同一失败的次数；超阈值则诚实停止，不无限重试。
    repeat_fail_streak = 0
    last_fail_sig = None         # (tool, error/code) 去重签名

    # AI-7D-R 超时恢复（长批量生成）：turn 级状态，turn 结束即清零。
    # ReadTimeout 发生在 LLM 响应尚未形成完整 tool_call 时，无真实 tool_call_id，
    # 故不构造假 tool message，而是用 user nudge 把结构化恢复信息反馈给下一轮模型。
    timeout_retries = 0
    max_batch_hint = 5           # 5 → 2 → 1

    def _on_read_timeout() -> bool:
        """LLM ReadTimeout 命中：降级 batch hint + 注入结构化恢复 nudge。
        返回 True=可继续重试（额度未用尽），False=恢复额度用尽（应 re-raise 走失败路径）。"""
        nonlocal timeout_retries, max_batch_hint
        if timeout_retries >= MAX_TIMEOUT_RETRIES:
            return False
        timeout_retries += 1
        max_batch_hint = _BATCH_DOWNGRADE.get(max_batch_hint, 1)
        committed = _committed_scene_ids(receipts)
        committed_str = ", ".join(committed) if committed else "（无）"
        nudge = (
            f"[系统恢复提示 MODEL_READ_TIMEOUT] 上一次批量生成因模型响应超时被中断"
            f"（read timeout，第 {timeout_retries}/{MAX_TIMEOUT_RETRIES} 次）。"
            f"本次没有形成完整工具调用、没有新增任何提交。"
            f"已成功提交的项：{committed_str}。"
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
        # AI-7D-R 协作式停止：每步开始前检查取消信号（安全停在步骤边界，不打断
        # 当前单次 LLM 调用/原子写）。由 script_writer_turn 捕获 _TurnStopped 收尾。
        if cancel_event is not None and cancel_event.is_set():
            raise _TurnStopped()
        # AI-7D-R 流式：event_cb 存在时走 chat_completion_stream，实时 forward
        # reasoning_delta；content 缓冲到 step 末，仅最终回复（无 tool_calls）才作
        # assistant.delta 流出——与持久化（只存最终 step content）严格一致。无 event_cb
        # 时走原非流式 chat_completion，行为零变化。
        content = None
        tool_calls = None
        try:
            if event_cb:
                step_reasoning_started = False
                step_reason_t0 = 0.0
                step_reasoning_buf: list[str] = []   # 累积本步 reasoning 文本（持久化用，不进 system）
                step_content_buf: list[str] = []
                async for ev in client.chat_completion_stream(
                        messages, temperature=0.7, max_tokens=65536,
                        thinking=thinking, tools=SCRIPT_WRITER_TOOLS,
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
                        # 工具名已出现、参数还在流式生成（大参数阶段最耗时）：
                        # 提前 emit，让前端显示"正在调用某工具"而非笼统"思考中"。
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
                    tools=SCRIPT_WRITER_TOOLS, tool_choice="auto")
                tool_calls = resp.get("tool_calls")
                content = resp.get("content")
        except httpx.ReadTimeout:
            # AI-7D-R 超时恢复：仅 LLM/network ReadTimeout 走降级重试（规则 6）。
            # 超时发生在响应尚未形成完整 tool_call 时，无任何新内容落盘，
            # 已成功批次作为 checkpoint 保留（committed_items 反馈给 Agent）。
            if not _on_read_timeout():
                raise   # 恢复额度用尽（2 次），走 script_writer_turn except 诚实结束
            continue   # 回循环顶，Agent 基于降级 nudge 用更小批次重发

        if not tool_calls:
            text = (content or "").strip()
            # ---- 真相闸门 ----
            real_changes = [r for r in receipts if r.get("ok") and r.get("verified") and not r.get("unchanged")]
            # (a) 声称成功但 0 真实变更 → 谎称。
            # 聊天/探讨轮保护：本轮零写尝试且用户消息是提问/探讨语气时，助手对【过往轮次】
            # 工作的完成态回顾不是本轮谎报（本轮本无修改请求），不触发纠正/兜底覆写。
            # 编辑请求轮（有写尝试，或用户明确要求修改）的闸门逻辑完全不变。
            chat_turn = (write_attempts == 0 and _is_chat_turn(instruction))
            false_claim = _claims_success(text) and not real_changes and not chat_turn
            # 搜索谎报：声称搜索成功/给出搜索结果，但本轮没有任何一次真实成功的
            # web.search 调用（trace 为准，budget 拒绝/失败/未调用都算无）。与写谎报
            # 同一收口机制：纠正 nudge → 模型真调工具或如实承认；聊天轮不上膛。
            real_searches = [t for t in trace if t["tool"] == "web.search" and t.get("ok")]
            search_false_claim = (_claims_search_success(text) and not real_searches
                                  and not chat_turn)
            # (b) 声称拆分了 N 段，但真实成功创建数与 N 不符 → 夸大（用户需求 #3：
            #     最终数量必须以真实 receipt 为准，不许凭文字记忆谎称）
            real_create_count = sum(1 for r in real_changes
                                    if r.get("operation") == "story.create")
            over_claim_n = (_claimed_count_mismatch(text, real_create_count)
                            if real_changes and real_create_count else None)
            # (c) 完成判定（需求 #4）：本轮发生过 mutation 且文本声称完成、且回复涉及
            #     数量/顺序总结（批量/多步任务），但最后一次只读发生在最后一次写【之前】
            #     （或从没读过）→ 没有以最新真实状态收尾，要求补读。
            #     只读任务或单次简单修改+简短回复不触发，避免无谓往返。
            last_write_step = max((t["step"] for t in trace
                                   if t["tool"] in WRITE_OPERATIONS), default=-1)
            reply_summarizes = bool(re.search(r"\d+\s*(个|段)|第.{1,4}位|顺序|顺序是|共\d+",
                                              text or ""))
            needs_final_read = (real_changes and _claims_success(text)
                                and reply_summarizes
                                and last_read_after_write < last_write_step)
            # ---- zero-write recovery（极窄兜底，独立一次性额度）----
            # 用户明确要求实际写入，但本轮【根本没尝试任何写工具】→ 注入一次恢复提示
            # 让模型真正执行；恢复后仍零写工具则不重复触发，交给既有闸门/诚实逻辑收尾。
            if (not zero_write_recovery_used
                    and write_attempts == 0 and not real_changes
                    and _is_explicit_write_request(instruction)):
                zero_write_recovery_used = True
                log.info("zero-write recovery 触发（project=%s）：明确写入请求但零写尝试", project)
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
                            "web.search 工具调用】（真实记录里要么没调用、要么全部失败）。"
                            "光用文字编造搜索结果是不允许的。请要么现在真正调用 web.search 搜索"
                            "（注意每轮最多 3 次），要么如实告诉用户：本轮没有搜索，或搜索失败、"
                            "资料不足，并基于已有知识继续。")
                    elif needs_final_read:
                        nudge = (
                            "你做了一些修改并声称完成，但最后一次真实读取发生在最后一次写入【之前】"
                            "（或本轮还没读过最终状态）。收尾前必须先读取与任务有关的最新真实状态"
                            "（故事段任务用 story.list，需要核对具体内容再 story.read；剧本正文任务"
                            "用 script.read），然后【以这次读取的真实结果】总结你创建/修改/删除了"
                            "什么、当前数量与顺序。不要凭自己之前写的文字记忆声称完成。")
                    elif over_claim_n is not None:
                        nudge = (
                            f"你上面说拆成了 {over_claim_n} 段，但工具的真实成功创建回执只有 "
                            f"{real_create_count} 次（{brief}）。数量必须以工具真实返回为准，"
                            "不许凭文字记忆声称。请基于真实回执重新回答：要么如实说已创建几个"
                            "（并给出真实 id），要么继续调用 story.create 把剩余的段真正创建出来。")
                    elif write_attempts == 0:
                        nudge = (
                            "你上面声称已经创建/移动/更新了内容，但本轮你【根本没有发起对应的"
                            "工具调用】。光用文字说「已完成」不会改动任何东西。\n"
                            + brief + "\n请要么真正调用对应工具（script.update/story.create/"
                            "story.move）完成修改，要么如实告诉用户你没有改动、需要怎么做。")
                    else:
                        nudge = (
                            "你上面声称已经完成，但工具的真实返回显示没有任何一次写入成功确认：\n"
                            + brief + "\n请只依据上面真实的工具返回结果回答。可以用最新数据重新调用"
                            "工具重试；如果不打算重试，就如实告诉用户这次没有改动成功。")
                    messages.append({"role": "user", "content": nudge})
                    _refresh_system(messages, rt, project)
                    continue   # 回到循环顶部，让模型重答/重试
                # 纠偏额度用尽 → 确定性兜底，绝不把不实文本交给用户
                text = _honest_fallback(receipts)
            # 有真实变更时，追加【确定性事实摘要】——由 receipts + 当前 canonical 生成，
            # 不信模型叙述。彻底解决「模型说改了A实际改了B」「说没改其实改了」。
            # 先删模型仿写的保留块（模型无权生成【实际执行记录】），再追加唯一权威块。
            text = strip_reserved_fact_blocks(text)
            text = _append_fact_summary(rt, project, text, receipts)
            return {"text": text or "已完成。",
                    "edited": edited, "created": created, "moved": moved, "scene_updated": scene_updated, "deleted": deleted,
                    "summary": summary, "events": events, "receipts": receipts, "trace": trace,
                    "reasonings": reasonings, "search_sources": search_sources,
                    "window_renewals": window_renewals}

        messages.append({"role": "assistant", "content": content or "",
                         "tool_calls": tool_calls})

        write_done_this_step = False   # 思路①：写工具强制串行——一步只执行一个写操作
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            args = fn.get("arguments") or "{}"
            call_id = tc.get("id") or name

            # AI-7D-R 流式：tool.started（执行/拒绝前）
            if event_cb:
                event_cb({"type": "tool.started", "step_id": step, "call_id": call_id,
                          "name": name, "label": _tool_label(name)})

            # 思路①：写工具串行。同一步若模型一次发多个写工具，只执行第1个，
            # 第2个起直接拒绝（ok=false），要求等前一个写完成拿最新 revision 再发下一个。
            # 只读工具（script.read/story.list/story.read）可批量。这从根上消除
            # 「批量发起用同一旧 revision → 后续 stale」的低效与混乱。
            is_write_tool = (name in WRITE_OPERATIONS)
            if is_write_tool and write_done_this_step:
                obs = {
                    "ok": False, "operation": name,
                    "error": "本步已执行过一个写操作。写工具必须一个一个来：等前一个完成、"
                             "拿到返回的最新 revision/状态后，再发下一个写工具调用。"
                             "不要在同一次回复里批量发多个写工具（会用旧 revision 导致 stale）。"
                             "只读工具（story.list/script.read/story.read/web.search）可以一次多个。",
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
            exc_msg = None   # AI-7D-R 流式：异常→tool.failed，否则→tool.completed
            # Web Search 预算拦截（Runtime 硬上限，不依赖 prompt）：本 turn 已满
            # MAX_WEB_SEARCH 次时不再调 Provider，直接结构化拒绝，让 Agent 用已有
            # 信息完成任务。拒绝同样走标准 obs→tool message→trace→tool.completed 路径。
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
                       "error": ("本 turn 已用完全相同的查询词搜索过（Hub 对重复查询只会返回"
                                 "同样的结果）。请换一个更具体或不同角度的查询词，或基于已获取"
                                 "的结果继续，不要重发同一查询。")}
            elif executor is None:
                obs = {"ok": False, "error": f"未知工具：{name}", "operation": name}
            else:
                if name == "web.search":
                    search_count += 1   # 仅真实调 Provider 的次数计入预算
                    _q_norm = _normalized_search_query(args)
                    if _q_norm:
                        search_queries_seen.add(_q_norm)
                try:
                    if asyncio.iscoroutinefunction(executor):
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

            # —— 防失控（需求 #11）：连续同一工具同一失败签名 → 诚实停止，不无限重试 ——
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
                    # 连续 N 次同样的失败：把真实账本交给模型并要求它换思路或如实告知，
                    # 而非继续重复无效操作。
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": json.dumps(obs, ensure_ascii=False)})
                    _refresh_system(messages, rt, project)
                    nudge = (f"「{name}」已连续 {repeat_fail_streak} 次以同样方式失败"
                             f"（{obs.get('error') or obs.get('code')}）。继续重复同样调用不会成功。"
                             "请改用只读工具（story.list/script.read）确认当前真实状态，换个参数或"
                             "换种做法，或如实告诉用户当前遇到的问题、哪些已成功完成。")
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
                    break   # 跳出 tool_calls 循环，回到 step 循环让模型重新决定

            # 写域调用：记入账本（含已知写工具 + 看似写工具但未知/拼错的，如 story.insert）。
            # 后者 obs 是 ok=false 的未知工具错误，也要算作「有写尝试但失败」，不能误判为
            # 「根本没调用工具」——这样兜底文案才准确。只读工具不记账本。
            is_write_domain = name in WRITE_OPERATIONS or name.startswith(("script.", "story."))
            if is_write_domain and name not in ("script.read", "story.list", "story.read"):
                write_attempts += 1
                obs.setdefault("operation", name)
                receipts.append(obs)
                write_done_this_step = True   # 思路①：本步写槽已占用，后续写工具拒绝
            else:
                # 只读工具成功 → 记录其 step，供最终完成判定「最后是否读过真实状态」。
                # web.search 除外：它读的是外部资料而非故事状态，不应参与该判定
                # （否则搜索发生在写之后会误跳过「写后重读最终状态」的收尾纠偏）。
                if ok_now and name != "web.search":
                    last_read_after_write = step
                # Web Search 轻量来源（仅 title+url）：随 turn 落盘 meta.search_sources
                # 供 UI「资料来源」折叠区展示；snippet/网页正文不进 meta（不跨轮持久化）。
                if name == "web.search" and obs.get("ok") and obs.get("sources"):
                    for s in obs["sources"]:
                        if s and (s.get("url") or s.get("title")) \
                                and s not in search_sources:
                            search_sources.append({"title": s.get("title") or "",
                                                   "url": s.get("url") or ""})

            # 据【真实回执】（而非模型叙述）设置 mutated 标志。
            # 必须 verified=true：删除预览(confirmed=false)虽 ok=true 但 verified=false，
            # 不算真实变更——否则模型只 preview 就会被当成「已删除」。
            if (name in WRITE_OPERATIONS and obs.get("ok")
                    and obs.get("verified") and not obs.get("unchanged")):
                if name == "script.update":
                    edited = True
                    summary = f"剧本正文 {obs.get('old_len')}→{obs.get('new_len')} 字"
                    events.append({"kind": "story_update", "summary": summary})
                elif name == "story.create":
                    created = True
                    s = f"创建故事段 {obs['scene_id']}"
                    events.append({"kind": "story_create",
                                   "scene_id": obs["scene_id"], "summary": s})
                    summary = s
                elif name == "story.move":
                    moved = True
                    s = f"移动故事段 {obs['scene_id']} 到第 {obs['order']} 位"
                    events.append({"kind": "story_move",
                                   "scene_id": obs["scene_id"], "summary": s})
                    summary = s
                elif name == "story.update":
                    scene_updated = True
                    changed = "、".join(obs.get("changed") or []) or "内容"
                    s = f"修改故事段 {obs['scene_id']}（{changed}）"
                    events.append({"kind": "story_scene_update",
                                   "scene_id": obs["scene_id"], "summary": s})
                    summary = s
                elif name == "story.delete":
                    deleted = True
                    d = obs.get("deleted", {}) or {}
                    n_shots = len(d.get("shots", []))
                    s = f"删除故事段 {obs['scene_id']}" + (f"及 {n_shots} 个镜头" if n_shots else "")
                    events.append({"kind": "story_delete",
                                   "scene_id": obs["scene_id"], "summary": s})
                    summary = s
                elif name == "story.delete_many":
                    deleted = True
                    items = obs.get("deleted") or []
                    ids = [c.get("scene_id") for c in items if c.get("scene_id")]
                    n_shots = sum(len(c.get("shots") or []) for c in items)
                    s = f"批量删除 {len(ids)} 段" + (f"及 {n_shots} 个镜头" if n_shots else "")
                    events.append({"kind": "story_delete_many",
                                   "scene_ids": ids, "summary": s})
                    summary = s
                elif name == "story.create_many":
                    created = True
                    ids = [c.get("scene_id") for c in (obs.get("created") or []) if c.get("scene_id")]
                    s = f"批量创建 {len(ids)} 段：{', '.join(ids)}" if ids else "批量创建故事段"
                    events.append({"kind": "story_create_many", "scene_ids": ids, "summary": s})
                    summary = s
                elif name == "story.insert_many":
                    created = True
                    ids = [c.get("scene_id") for c in (obs.get("inserted") or []) if c.get("scene_id")]
                    s = f"多锚点批量插入 {len(ids)} 段：{', '.join(ids)}" if ids else "批量插入故事段"
                    events.append({"kind": "story_insert_many", "scene_ids": ids, "summary": s})
                    summary = s
                elif name == "story.update_many":
                    scene_updated = True
                    res = obs.get("results") or []
                    s = f"批量修改 {len(res)} 段"
                    events.append({"kind": "story_scene_update_many", "results": res, "summary": s})
                    summary = s

            # AI-7D-R 流式：tool.completed / tool.failed（据真实 obs/exc_msg，非模型叙述）
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

            # 执行轨迹（需求 #7）：每个工具调用一条，供调试真实执行顺序/结果。
            trace.append(_trace_entry(step, name, args, obs))

        # 无条件刷新 system（项目元信息/计数）。成功/stale/no-op/错误都需 Agent 基于最新状态继续。
        _refresh_system(messages, rt, project)

    # （原「预算耗尽」诚实停止段已删除：renewable window 下窗口用满时注入
    # self-check nudge 续期，由 Agent 自行判断继续/停止——while True 无 break，
    # 此段不再可达。）
