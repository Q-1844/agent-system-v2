# src/agents/session_governance.py
# 会话治理共享原语（V1 压力测试驱动，2026-09-02）。
#
# 依据 _stress/STRESS_REPORT.md 证据链：
#   - F2/F3：多轮结构修改后的长会话记忆与 canonical 脱节，模型零读工具调用、
#     用陈旧记忆冒充"逐镜精读"（引用已删除镜头、编造剧情）；
#   - F4：同一 Director 定义 + 干净会话即高质量审查者（17 次工具调用、14/14 全读）；
#   - E1→E2b：修改完备性随结构修改轮次累积劣化（去重不彻底、顺序错位无人察觉）。
#
# 本模块提供三项 Runtime 增强（Clean Review / Session Re-grounding / Zero-read Gate）
# 共享的一套原语——"calibrated session spawn" = 新建会话 + canonical 状态头 + 用户
# 创作约束注入。三项增强都在它之上加触发策略，不做三套平行代码。
#
# 禁改约束（与冻结基线一致）：不动 Agent 权限边界 / tool schema / OCC 指纹 / 批量上限 /
# SSE 事件协议 / canonical 数据结构 / 既有 zero-write recovery 行为；review 与 re-ground
# 均为新增只读或会话文件级路径，主链路（SW→CD→Director 正常创作流）行为不变。

from __future__ import annotations
import json
import re
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from src.core.atomic import atomic_write_json
from src.core.store import ProjectStore

from .models import AgentMessage, AgentSession
from .work_memory import WorkMemoryStore


# ============ 触发阈值（配置常量）============
# 结构类写操作 = shot.create / shot.create_many / shot.delete / shot.move（不含 update
# ——内容修改不改变镜头集合与会话结构记忆的映射关系）。
# 计数口径：按【成功调用次数】计（create_many 一批算 1 次；失败/stale/unchanged 的调用
# 未实际改动 canonical，不计）。初始值依据：实验中退化出现在 E1+E2+E2b 约 13 次结构
# 操作之后 → Clean Review 阈值 8（先于退化出现），Re-grounding 阈值 16（两次 Clean
# Review 量级，届时主会话 staleness 密度已实测致幻觉）。
STRUCTURAL_WRITE_OPS = {"shot.create", "shot.create_many", "shot.delete", "shot.move"}
CLEAN_REVIEW_THRESHOLD = 8
REGROUND_THRESHOLD = 16

# 用户主动审查意图（触发 B）：关键词白名单，宁缺勿滥——识别不了就走正常对话，不算失败。
# 只匹配"全片/整体/连续性/逐镜"级的审查请求；"检查一下第2镜"这类单点核对不在此列
# （主会话配合 zero-read gate 即可胜任）。
_REVIEW_INTENT_RE = re.compile(
    r"连续性|连贯性|"
    r"全片(检查|审查|过一遍|梳理|复查)|"
    r"从头(过|读|看|检查|梳理|审|复盘)一?遍|"
    r"整体(审查|检查|梳理|复查)|全面(审查|检查|复查)|"
    r"逐镜(检查|审查|核对|梳理)|逐段(检查|审查|核对|梳理)|"
    r"做一?次(全片|整体|全面)?(审查|检查|复查)")


def is_review_request(content: str) -> bool:
    """用户本轮是否命中连续性审查意图（Clean Review 触发 B）。"""
    return bool(_REVIEW_INTENT_RE.search(content or ""))


# ============ Phase 39 hardening A（用户 2026-09-25 定版）：Clean Review 窄豁免 ============
# 实证（39B replay，_37_fable/_39_shadow_replay.py）：「连续性」这类领域词只表达
# 【话题】，不表达【意图】——含它的校准/修复任务被整 turn 劫持进只读审查会话
# （写工具物理被拒，trace 永远无法落档）。N2 的 bridge-envelope 豁免是同型问题的
# 第一个特例；本节把它推广为内容级窄规则（非关键词粗暴豁免）：
#   Clean Review ⇔ 话题命中（is_review_request）且【无显式 mutation 意图】。
#   - 显式 mutation 意图（修复/重试/重生成/插桥/落档/写入/校准/裁定/执行…）→
#     正常 Agent runtime——写工具不被剥夺，写或不写由 Agent 按纪律自主判断；
#   - 纯审查请求（话题命中且无 mutation 动词）→ 仍走 Clean Review（行为不变）；
#   - 否定窗口保护：「不要修改」「禁止重试」不算 mutation 意图。
_MUTATION_INTENT_RE = re.compile(
    r"(?<!不要)(?<!禁止)(?<!不得)(?<!不能)(?<!无需)(?<!不许)(?<!不)"
    r"(修复|修正|修好|修改|解决|重试|重新生成|重生成|插桥|补桥|落档|写入|记录到|"
    r"校准|裁定|执行|落实|apply|fix|repair|retry|regenerat|calibrat|adjudicat)")


def has_mutation_intent(content: str) -> bool:
    """内容是否含【显式 mutation 意图】动词（否定窗口保护，见 _MUTATION_INTENT_RE）。"""
    return bool(_MUTATION_INTENT_RE.search(content or ""))


def should_route_to_clean_review(content: str) -> bool:
    """Clean Review 路由窄规则（director 主 turn 改道判定入口）。
    - True：纯审查请求 → 只读审查会话（原行为保持）；
    - False：含显式 mutation 意图 → 正常 tool-loop（不物理剥夺写工具）。"""
    return is_review_request(content) and not has_mutation_intent(content)


# ============ Zero-read Gate 检测特征（改动一，三 agent tool-loop 共用）============
# 触发条件（两者同时满足才命中，宁窄勿误——纯探讨轮不拦）：
#   1. 本 turn 零次读工具调用（读工具集由各 agent 自己的 READ_TOOLS 定义）；
#   2. 回复出现任一 read-required 信号：
#      a) 引用 ≥2 个具体 shot_id（逐镜/细节表特征；scene_id 单独出现不计——
#         探讨轮常自然复述用户提到的段号，误伤风险高）；
#      b) 声称"已核对/逐镜精读/完整读了一遍/以工具返回为准"类表述（带否定窗口保护）。
# 镜头/故事段 ID 形态（与 canonical 分配规则一致：s 前缀 + _cNN）。
_SHOT_ID_RE = re.compile(r"(?<![A-Za-z0-9_])(s\d{1,3}_c\d{1,3})(?![A-Za-z0-9_])")
_SCENE_ID_RE = re.compile(r"(?<![A-Za-z0-9_])(s\d{1,3})(?![A-Za-z0-9_])")

_READ_CLAIM_RE = re.compile(
    r"逐镜(精读|读了一遍|读过|核对|检查了|过)|"
    r"逐段(精读|读了一遍|读过|核对|检查了)|"
    r"(完整|全部|所有)镜头(已经?|都)?(读|看|核对|检查)(了|过|一遍)|"
    r"一个一个(地)?(读|看|核对|检查)(了|过)|"
    r"逐一(读|核对|检查|看过)(了|过)|"
    r"已(经)?(核对|读了一遍|全部读|读完|通读|精读)|"
    r"以(工具|真实返回|工具返回|读取结果|本次工具)(的)?(结果|内容|数据|为准)?为准")
_READ_CLAIM_NEG_RE = re.compile(r"(没有|没|并未|并没有|尚未|未|未能|无法|不能|不要|先别|别|建议)")


def extract_cited_shot_ids(text: str) -> List[str]:
    """回复中出现的全部 shot_id（去重、保序）。"""
    out: List[str] = []
    for m in _SHOT_ID_RE.finditer(text or ""):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def extract_cited_scene_ids(text: str) -> List[str]:
    """回复中出现的全部 scene_id（去重、保序；不含 shot_id 里的段前缀部分）。"""
    out: List[str] = []
    for m in _SCENE_ID_RE.finditer(text or ""):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def reply_claims_reading(text: str) -> bool:
    """回复是否声称"已核对/逐镜精读/以工具返回为准"（否定窗口内的不算）。"""
    t = text or ""
    for m in _READ_CLAIM_RE.finditer(t):
        window = t[max(0, m.start() - 6):m.start()]
        if _READ_CLAIM_NEG_RE.search(window):
            continue
        return True
    return False


def is_read_required_reply(text: str) -> bool:
    """zero-read gate 的回复侧信号：引用 ≥2 个 shot_id，或出现读取声称。"""
    t = text or ""
    if not t:
        return False
    if len(extract_cited_shot_ids(t)) >= 2:
        return True
    return reply_claims_reading(t)


_HAS_ANY_ID_RE = re.compile(r"(?<![A-Za-z0-9_])s\d{1,3}(_c\d{1,3})?(?![A-Za-z0-9_])")


def validate_cited_ids(text: str, scene_ids: Iterable[str], shot_ids: Iterable[str],
                       exempt: Sequence[str] = ()) -> Optional[str]:
    """ID 存在性校验（防敷衍）：校验回复中引用的全部 shot_id / scene_id 是否存在于
    当前 canonical。引用不存在的 ID（如已删除的 s05_c03）→ 返回系统勘误块文本
    （不静默、不改写模型原文）；全部合法 → None。exempt = 本轮刚删除的 ID
    （"已删除 s05_c03"是合法陈述，不应被勘误）。"""
    t = text or ""
    if not t or not _HAS_ANY_ID_RE.search(t):
        return None
    sset, hset = set(scene_ids), set(shot_ids)
    ex = set(exempt or ())
    bad_shots = [i for i in extract_cited_shot_ids(t) if i not in hset and i not in ex]
    bad_scenes = [i for i in extract_cited_scene_ids(t) if i not in sset and i not in ex]
    if not bad_shots and not bad_scenes:
        return None
    lines = ["\n\n---\n【系统勘误】以下 ID 在当前项目中不存在（可能已被删除或从未创建），"
             "上文引用这些 ID 的内容不可信，请以工具真实读取为准："]
    if bad_shots:
        lines.append("- 不存在的镜头：" + "、".join(bad_shots))
    if bad_scenes:
        lines.append("- 不存在的故事段：" + "、".join(bad_scenes))
    return "\n".join(lines)


# ============ 结构写操作计数器（Clean Review 与 Re-grounding 共用计数源）============
class GovernanceStore:
    """项目级会话治理计数器（output/agents/session_governance.json，原子写）。

    两个计数器共享同一增量源（每次成功结构写操作同时 +1）：
      - structural_since_review：自上次 Clean Review 后累计，≥ CLEAN_REVIEW_THRESHOLD
        触发 Clean Review，触发后清零；
      - structural_since_reground：自上次 Re-grounding 后累计，≥ REGROUND_THRESHOLD
        触发校准会话重置，触发后两个计数器都清零。
    （若 Clean Review 也清零 reground 计数，16 阈值将永远无法到达。）"""

    def __init__(self, project_dir, project_name: str):
        self.project_dir = Path(project_dir)
        self.project_name = project_name
        self.path = self.project_dir / "output" / "agents" / "session_governance.json"

    def _load(self) -> dict:
        if not self.path.exists():
            return {"structural_since_review": 0, "structural_since_reground": 0}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {
                "structural_since_review": int(data.get("structural_since_review") or 0),
                "structural_since_reground": int(data.get("structural_since_reground") or 0),
            }
        except Exception:
            return {"structural_since_review": 0, "structural_since_reground": 0}

    def _save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(state)
        payload.update({"project": self.project_name, "updated_at": time.time()})
        atomic_write_json(self.path, payload)

    @staticmethod
    def count_structural_writes(receipts: Sequence[dict]) -> int:
        """从本轮真实写回执统计结构类写操作次数（只计 ok+verified+非 unchanged）。"""
        n = 0
        for r in receipts or []:
            if not (r.get("ok") and r.get("verified") and not r.get("unchanged")):
                continue
            if r.get("operation") in STRUCTURAL_WRITE_OPS:
                n += 1
        return n

    def snapshot(self) -> dict:
        return self._load()

    def record_structural_writes(self, receipts: Sequence[dict]) -> int:
        """把本轮回执中的结构写操作计入两个计数器，返回本次新增数。"""
        n = self.count_structural_writes(receipts)
        if n <= 0:
            return 0
        st = self._load()
        st["structural_since_review"] += n
        st["structural_since_reground"] += n
        self._save(st)
        return n

    def reset_since_review(self) -> None:
        st = self._load()
        st["structural_since_review"] = 0
        self._save(st)

    def reset_all(self) -> None:
        """Re-grounding 后计数器清零（两个都清）。"""
        self._save({"structural_since_review": 0, "structural_since_reground": 0})


# ============ 用户创作约束（原始 brief）============
# 修复 STRESS_REPORT §8 案例 1：用户硬约束（如"老船长全程只露无线电声音"）经 SW 违反
# 后由 canonical 向下游传染、无人能查——审查者/校准会话必须知道约束才查得出。
# 获取方案（最简可行）：项目首个 ≥10 字符的用户消息被记为 brief（正常创作流里这就是
# 用户交给 SW 的故事 brief），落盘 output/agents/creative_brief.json；三 agent turn
# 均 best-effort 调用 capture_brief_if_absent。

class BriefStore:
    def __init__(self, project_dir, project_name: str):
        self.project_dir = Path(project_dir)
        self.project_name = project_name
        self.path = self.project_dir / "output" / "agents" / "creative_brief.json"

    def get(self) -> Optional[str]:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8")).get("brief") or None
        except Exception:
            return None

    def capture_if_absent(self, content: str, agent_id: str,
                          min_chars: int = 10) -> Optional[str]:
        """首个 ≥min_chars 的用户消息记为 brief（已存在则不动）。返回存储的 brief。"""
        existing = self.get()
        if existing:
            return existing
        t = (content or "").strip()
        if len(t) < min_chars:
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, {
            "project": self.project_name, "brief": t,
            "captured_from_agent": agent_id, "captured_at": time.time(),
        })
        return t


def get_brief(project_dir, project_name: str) -> Optional[str]:
    return BriefStore(project_dir, project_name).get()


def capture_brief_if_absent(project_dir, project_name: str, content: str,
                            agent_id: str) -> None:
    """best-effort 捕获用户创作约束（任何失败都不阻断主流程）。"""
    try:
        BriefStore(project_dir, project_name).capture_if_absent(content, agent_id)
    except Exception:
        pass


# ============ 校准状态头（calibrated session spawn 的核心携带物）============
def build_state_header(project_dir, project_name: str) -> str:
    """从 canonical + work_memory 结构化事实生成状态头。【绝不含旧会话聊天内容、
    绝不做旧对话摘要】——否则等于自己制造 F2。生成源：
      - ProjectStore（canonical）：当前 scenes/shots 结构与摘要、characters 名单；
      - WorkMemoryStore（结构化共享工作记忆）：最近 storyboard 域变更记录。"""
    proj = ProjectStore(Path(project_dir)).load()
    lines = ["# 项目状态头（系统生成：canonical + 结构化工作记忆；不是对话回忆，当前状态以此为准）"]
    lines.append("## 故事段与镜头（当前真实状态）")
    for sc in (proj.scenes or []):
        ids = ", ".join(sh.shot_id for sh in (sc.shots or [])) or "（无镜头）"
        summary = (sc.summary or sc.title or "")[:60]
        lines.append(f"- {sc.scene_id}「{summary}」{len(sc.shots or [])} 镜：{ids}")
    chars = proj.characters or []
    if chars:
        lines.append("## 角色 canonical（权威名单）")
        for c in chars:
            lines.append(f"- {c.name}：{(getattr(c, 'description_en', '') or '')[:80]}")
    try:
        wm = WorkMemoryStore(project_dir, project_name).recent(
            limit=8, domains=["storyboard"])
        if wm:
            lines.append("## 最近结构/内容变更（共享工作记忆，结构化事实）")
            for e in wm:
                lines.append(f"- {e.get('summary') or ''}")
    except Exception:
        pass
    return "\n".join(lines)


# ============ 一次性校准会话 spawn（Clean Review 用；Re-grounding 见下）============
def create_review_session(store, project_name: str) -> AgentSession:
    """新建一次性 review session（不复用、用完即弃——复用会让 review session 自己
    积累 staleness）。scope_type 用专用值 "review"：不进入 get_or_create /
    get_or_create_conversation 的复用键，不影响现有长存会话语义。"""
    return store.create_ephemeral("director", "review",
                                  f"review_{int(time.time() * 1000)}")


# ============ Session Re-grounding（校准会话重置）============
def reground_session(store, session_id: str, project_dir, project_name: str,
                     last_user_intent: str = "") -> str:
    """重开 calibrated working session：同 session_id（前端无感），历史整体归档后
    重新注入【严格限定】的携带物——状态头（canonical + work_memory）+ 用户原始
    brief + 最近一轮用户意图（一句话）。绝不含旧会话聊天内容、绝不做旧对话摘要。
    返回给用户的明确提示文本（不静默——否则体感是"它忘事了"）。"""
    project_dir = Path(project_dir)
    s = store.load(session_id)
    if s is None:
        return ""
    # 旧历史归档（保留证据，不删除）
    archive_dir = project_dir / "output" / "agents" / "sessions" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    atomic_write_json(archive_dir / f"{session_id}_preground_{ts}.json", s.to_dict())
    # 重建携带物
    seed: List[str] = ["【校准会话开启】系统已根据当前项目状态重置本会话（旧对话已归档，"
                       "未删除；本消息之后为全新上下文，当前项目状态以下方状态头为准）。"]
    seed.append(build_state_header(project_dir, project_name))
    brief = get_brief(project_dir, project_name)
    if brief:
        seed.append("# 用户创作约束（原始 brief，创作与修改时必须遵守）\n" + brief)
    # Sub-Agent 全局决策记忆（Errata E3）：MD 的全局创作决策 rationale 在
    # Re-grounding 后不再丢失。条件追加——global_memory 为空时不产生任何字节
    # （seed 与现行逐字节一致，bit-for-bit 保护）。best-effort：读取失败静默跳过。
    try:
        from src.agents.subagent.registry import SubagentStore
        _gm = SubagentStore(project_dir, project_name).render_global_memory_for_injection()
        if _gm:
            seed.append(_gm)
    except Exception:
        pass
    if last_user_intent:
        seed.append(f"# 最近一轮用户意图\n{last_user_intent.strip()[:200]}")
    s.messages = [AgentMessage(role="user", content="\n\n".join(seed))]
    store.save(s)
    return ("⚠️ 已根据当前项目状态开启校准会话：本会话历史已整体归档（"
            f"output/agents/sessions/archive/{session_id}_preground_{ts}.json），"
            "新上下文由当前 canonical 状态 + 你的原始创作约束重新校准。"
            "继续告诉我你要做的修改即可。")


# ============ Compaction 遥测（改动四：只观察，不做 compaction 本体）============
# 实验否决了基于上下文长度的压缩（10 轮后 history 仅 ~11k chars，阈值从未接近；
# 有损摘要本身可能成为新失真源）。只落盘每 turn 的上下文规模一行结构化日志，
# 将来若真出现长上下文证据，compaction 的阈值与设计有真实数据可用。
def log_context_telemetry(project_dir, project: str, agent_id: str, session_id: str,
                          turn_id: str, *, history_chars: int, system_chars: int,
                          tool_io_chars: int) -> None:
    """每 turn 落盘一行上下文规模遥测（output/agents/telemetry/context_size.jsonl）。
    best-effort：任何失败静默跳过，绝不影响 turn 主流程。"""
    try:
        d = Path(project_dir) / "output" / "agents" / "telemetry"
        d.mkdir(parents=True, exist_ok=True)
        line = {"ts": time.time(), "project": project, "agent": agent_id,
                "session": session_id, "turn_id": turn_id,
                "history_chars": int(history_chars),
                "system_chars": int(system_chars),
                "tool_io_chars": int(tool_io_chars)}
        with open(d / "context_size.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass
