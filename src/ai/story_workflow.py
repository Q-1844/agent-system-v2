# src/ai/story_workflow.py
"""AI 整理故事工作流（Phase AI-1，规格 提示词.md §四~§十三、§二十）。

模型职责窄：
  - organize_story          完整故事 -> 故事段 + character_mentions（调 Agnes 文本模型，返回 JSON）
  - draft_character         角色名 + 完整故事 + 相关段落 -> 角色文字设定（调 Agnes，返回文本）
  - draft_characters_batch  批量角色文字设定（调 Agnes，返回 JSON）
  - match_characters        确定性代码，标准化名称后精确匹配，不调 AI、不模糊推断

本模块不生成 Shot / 景别 / 机位 / 运镜 / 首尾帧 / 视频提示词 / 时长。
角色匹配、Character 创建、Scene.characters 写入全部由 routers/story.py 的普通代码完成。
"""
import json
import re
from typing import Optional

from pydantic import BaseModel, Field


# ============ 草稿数据结构（规格 §二十二）============
class DraftScene(BaseModel):
    """AI 整理出的一个故事段草稿。draft_id 仅 AI 草稿内部用，不进 project.json。"""
    draft_id: str
    summary: str = ""
    character_mentions: list[str] = Field(default_factory=list)


class CharacterResolutionItem(BaseModel):
    """一个被提及人物的解决状态。mention 为故事原文称呼，不改写。"""
    mention: str
    # matched / unresolved / created / ignored
    status: str = "unresolved"
    # 真正 Project.characters 中的 name（matched / created 时非空）
    matched_character_name: Optional[str] = None
    # unresolved / created 时可保存的文字草稿（用户可编辑）
    draft_description: Optional[str] = None


class StoryOrganizationDraft(BaseModel):
    """一次 AI 整理故事的完整草稿。落 projects/{p}/output/ai/story_draft.json，不进 project.json。"""
    draft_id: str
    project_name: str
    project_updated_at: float          # organize 时的项目版本；workflow 内建角色时重新盖戳
    story_text_snapshot: str           # organize 时的完整故事快照（apply 时比对，防 stale）
    overview: str = ""
    scenes: list[DraftScene] = Field(default_factory=list)
    characters: list[CharacterResolutionItem] = Field(default_factory=list)


class StoryBreakdownResult(BaseModel):
    """organize_story 的 AI 返回结构（内部用，不落盘）。"""
    overview: str = ""
    scenes: list[DraftScene] = Field(default_factory=list)


# ============ system prompts ============
_ORGANIZE_SYSTEM = """你是一名影视故事编辑。你的任务是把一个完整故事整理成适合后续进行镜头规划的"故事段"。

故事段应按照叙事变化进行划分，例如：
- 一个事件阶段完成
- 人物目标发生变化
- 冲突发生变化
- 地点或时间明显变化
- 情绪进入新的阶段
- 新的重要人物加入
- 故事进入新的推进阶段

不要按以下方式机械拆分：单个动作、景别、摄影机运动、一句话、每几秒。

每个故事段以后还会继续由 AI 导演拆成多个 Shot，所以故事段应该比 Shot 粗。

不要生成以下任何内容：Shot、景别、机位、运镜、首帧、尾帧、视频提示词、视频时长。故事段是叙事段落，不是镜头。

为每个故事段识别其中涉及的人物名称（与故事原文中的称呼一致）。如果某段没有明确人物，character_mentions 为空数组。

只返回一个 JSON 对象，不要有任何解释文字，格式如下：
{"overview": "一句话概述故事被整理成几个阶段", "scenes": [{"draft_id": "draft_1", "summary": "该故事段发生了什么", "character_mentions": ["人物A", "人物B"]}]}
draft_id 从 draft_1 开始递增。"""

_CHAR_SYSTEM = """你是一名角色设定编辑。根据给定的角色名称、完整故事以及该角色出现的故事段，为这个角色写一段文字设定。

设定应包括：外貌特征、性格、身份/背景、在故事中的作用。只写文字设定，不要生成图片，不要写镜头或分镜描述，不要复述剧情。

用中文返回，一段话即可，不要分点编号，不要加标题。"""

_CHAR_BATCH_SYSTEM = """你是一名角色设定编辑。根据给定的完整故事，为指定的每个角色分别写一段文字设定。

设定应包括：外貌特征、性格、身份/背景、在故事中的作用。只写文字设定，不生成图片，不写镜头。

只返回一个 JSON 对象，不要有任何解释文字，格式如下：
{"characters": [{"name": "角色名", "description": "该角色的文字设定"}]}
为每个被请求的角色都给出一项。"""


# ============ 确定性匹配（规格 §九）============
# 常见中英文标点，标准化时移除（处理前后空格、大小写、简单标点差异）
_PUNCT = "，。、！？,.!?:;\"\"\"''''（）()[]【】《》<>"


def normalize_name(name: str) -> str:
    """标准化角色名：去前后空格、小写、去常见标点、折叠内部空白。
    用于确定性精确匹配（规格 §九），不做模糊推断（不认为"老王"=="王师傅"）。"""
    if not name:
        return ""
    s = name.strip().lower()
    for ch in _PUNCT:
        s = s.replace(ch, "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_json_object(raw: str) -> Optional[dict]:
    """从 Agnes 返回中提取首个 JSON 对象（兼容带/不带 code fence，json_mode 已去 fence 但兜底）。"""
    if not raw:
        return None
    text = raw.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


class StoryWorkflow:
    """AI 整理故事工作流。client 为 AgnesClient 实例（测试可注入 fake）。"""

    def __init__(self, client):
        self._client = client

    # ---------- AI：整理故事 ----------
    async def organize_story(self, story_text: str, existing_char_names: list[str],
                             style_weak: str = "") -> StoryBreakdownResult:
        """完整故事 -> 故事段 + character_mentions。调 Agnes 文本模型返回 JSON。"""
        chars_hint = ""
        if existing_char_names:
            chars_hint = (
                "\n\n项目中已有以下角色（若故事中提到这些角色，请在 character_mentions "
                "中使用与这些名称一致的称呼）：\n" + "、".join(existing_char_names)
            )
        user = f"完整故事：\n{story_text}{chars_hint}"
        raw = await self._client.chat(
            [{"role": "system", "content": _ORGANIZE_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.7, max_tokens=4096, json_mode=True)
        obj = _parse_json_object(raw)
        if obj is None:
            raise ValueError("AI 未返回可解析的 JSON")
        overview = (obj.get("overview") or "").strip()
        scenes_raw = obj.get("scenes") or []
        scenes: list[DraftScene] = []
        for i, s in enumerate(scenes_raw, start=1):
            if not isinstance(s, dict):
                continue
            did = str(s.get("draft_id") or f"draft_{i}").strip()
            summary = str(s.get("summary") or "").strip()
            mentions = [str(m).strip() for m in (s.get("character_mentions") or [])
                        if str(m).strip()]
            scenes.append(DraftScene(draft_id=did, summary=summary, character_mentions=mentions))
        return StoryBreakdownResult(overview=overview, scenes=scenes)

    # ---------- AI：单角色文字草稿 ----------
    async def draft_character(self, name: str, story_text: str,
                              related_summaries: list[str], style_weak: str = "") -> str:
        """角色名 + 完整故事 + 该角色出现的段落 -> 角色文字设定（中文一段话）。"""
        related = "\n\n".join(f"故事段：\n{s}" for s in related_summaries)
        if not related:
            related = "（故事中未单独提及该角色的段落）"
        user = (f"角色名称：{name}\n\n完整故事：\n{story_text}\n\n"
                f"该角色出现的故事段：\n{related}")
        text = await self._client.chat(
            [{"role": "system", "content": _CHAR_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.7, max_tokens=800)
        return text.strip()

    # ---------- AI：批量角色文字草稿 ----------
    async def draft_characters_batch(self, names: list[str], story_text: str,
                                     style_weak: str = "") -> list[tuple[str, str]]:
        """批量生成角色文字设定。返回 [(name, description), ...]，保证每个请求 name 都有一项。"""
        if not names:
            return []
        user = f"需要设定的角色：{('、'.join(names))}\n\n完整故事：\n{story_text}"
        raw = await self._client.chat(
            [{"role": "system", "content": _CHAR_BATCH_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.7, max_tokens=2048, json_mode=True)
        obj = _parse_json_object(raw)
        if obj is None:
            raise ValueError("AI 未返回可解析的 JSON")
        out: list[tuple[str, str]] = []
        for c in (obj.get("characters") or []):
            if not isinstance(c, dict):
                continue
            n = str(c.get("name") or "").strip()
            d = str(c.get("description") or "").strip()
            if n:
                out.append((n, d))
        # 缺失的 name 给空串占位（前端可再单独生成）
        got = {n for n, _ in out}
        for n in names:
            if n not in got:
                out.append((n, ""))
        return out

    # ---------- 纯代码：确定性匹配 ----------
    @staticmethod
    def match_characters(mentions: list[str], project_char_names: list[str]) -> dict:
        """标准化名称后精确匹配。返回 {mention: matched_project_name or None}。
        不调 AI，不做模糊推断（规格 §九）。"""
        norm_map: dict[str, str] = {}   # normalized -> 原始项目角色名
        for cn in project_char_names:
            nk = normalize_name(cn)
            if nk and nk not in norm_map:
                norm_map[nk] = cn
        result: dict[str, Optional[str]] = {}
        for m in mentions:
            nk = normalize_name(m)
            result[m] = norm_map.get(nk)
        return result
