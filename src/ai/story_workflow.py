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


class StoryContinuationDraft(BaseModel):
    """AI 续写故事草稿（Phase AI-1B）。落 projects/{p}/output/ai/story_continuation_draft.json。
    append-only：apply 时只追加 story_text 末尾 + Scene 末尾，不动已有内容。
    stale 围绕 story_text + scene 结构签名判断，不比 updated_at（避免视频/图片任务假 stale）。"""
    draft_id: str
    project_name: str
    base_story_text: str                          # 续写时的完整故事快照（apply stale 比对）
    base_scene_signature: list[dict]              # [{scene_id, summary, characters}] 不含产物（apply stale 比对）
    direction: str = ""                           # 用户填写的续写方向
    scale: str = "standard"                       # short / standard / long
    continuation_text: str = ""                   # AI 续写正文（用户可编辑）
    new_scene_drafts: list[DraftScene] = Field(default_factory=list)
    character_resolution: list[CharacterResolutionItem] = Field(default_factory=list)
    stage: str = "writing"                        # writing/continuation_ready/scenes_ready/ready_to_apply/applied/stale
    created_at: float = 0.0


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


# ============ Phase AI-1B：AI 续写故事 ============
_CONTINUE_SYSTEM = """你是一名影视故事编剧。请在用户现有故事的基础上继续创作。

要求：
1. 只写新的续写部分，不要重复输出原故事。
2. 延续现有人物、世界观、事件因果和情绪。
3. 不突然改变已经确定的人物身份和关系。
4. 如果用户提供了续写方向，优先遵守。
5. 如果用户没有提供方向，根据当前未解决的冲突和目标自然推进。
6. 根据"短篇 / 标准 / 较长"调整剧情推进幅度：短篇推进较少，标准中等，较长推进更多。
7. 优先继续使用已有主要人物。只有剧情确实需要时才引入新角色，不要为了增加变化而频繁创造新人物。
8. 不要写摄影术语，不要写 Shot，不要写首帧、尾帧或视频 prompt。
9. 输出可以直接追加到完整故事末尾的正文。
10. 不要以"续写：""下面是……"等说明性文字开头，直接开始写故事正文。"""

_ORGANIZE_CONTINUATION_SYSTEM = """你是一名影视故事编辑。把"新的续写正文"整理成适合后续镜头规划的"故事段"。

上下文中会给出：已有故事的最后一部分、已有最后的故事段。这些只用于保证连续、理解故事从哪里接、避免重复。
绝不能把已有内容再输出一遍。只把"新的续写正文"拆成新的故事段。

故事段按叙事变化划分（一个事件阶段完成 / 人物目标变化 / 冲突变化 / 地点或时间变化 / 情绪进入新阶段 / 新人物加入 / 故事进入新推进阶段），不要按单个动作、景别、摄影机运动、一句话、每几秒机械拆分。故事段比 Shot 粗（以后还会被拆成多个 Shot）。

不要生成：Shot、景别、机位、运镜、首帧、尾帧、视频提示词、时长。

为每个故事段识别涉及的人物名称（与续写原文中的称呼一致）。无人物则 character_mentions 为空数组。

只返回一个 JSON 对象，不要有任何解释文字：
{"overview": "一句话概述续写被整理成几个阶段", "scenes": [{"draft_id": "draft_1", "summary": "该故事段发生了什么", "character_mentions": ["人物A"]}]}
draft_id 从 draft_1 开始递增。"""

_SCALE_HINT = {
    "short": "短篇（约推进 1-2 个故事段的剧情）",
    "standard": "标准（约推进 3-4 个故事段）",
    "long": "较长（约推进 5-7 个故事段）",
}


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
        obj = await self._chat_json(
            [{"role": "system", "content": _ORGANIZE_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.7, max_tokens=4096)
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
        obj = await self._chat_json(
            [{"role": "system", "content": _CHAR_BATCH_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.7, max_tokens=2048)
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

    # ---------- AI：JSON 调用 + 解析（parse 失败自动重试一次，规格 §二十九）----------
    async def _chat_json(self, messages: list[dict], **kw) -> Optional[dict]:
        """调 chat(json_mode=True) + 解析 JSON。parse 失败自动重试一次（最多一次，不做复杂 repair）。"""
        raw = await self._client.chat(messages, json_mode=True, **kw)
        obj = _parse_json_object(raw)
        if obj is not None:
            return obj
        raw2 = await self._client.chat(
            messages + [{"role": "system",
                         "content": "重要：只返回一个合法的 JSON 对象，以 { 开头，不要任何说明文字、代码块标记或其他内容。"}],
            json_mode=True, **kw)
        return _parse_json_object(raw2)

    # ---------- Phase AI-1B：AI 续写故事（正文，纯文本）----------
    async def continue_story(self, story_text: str, existing_chars: list[tuple[str, str]],
                             existing_scene_summaries: list[str], direction: str = "",
                             scale: str = "standard", style_weak: str = "") -> str:
        """续写故事正文。json_mode=False，返回普通文本（规格 §六：续写与拆分职责分开，更稳定）。"""
        scale_hint = _SCALE_HINT.get(scale, _SCALE_HINT["standard"])
        chars_hint = ""
        if existing_chars:
            lines = [f"- {n}：{d}" if d else f"- {n}" for n, d in existing_chars[:20]]
            chars_hint = "\n\n现有主要人物：\n" + "\n".join(lines)
        scenes_hint = ""
        if existing_scene_summaries:
            tail = existing_scene_summaries[-2:]   # 已有最后 1-2 段作边界
            scenes_hint = "\n\n已有最后的故事段：\n" + "\n".join(f"- {s}" for s in tail)
        dir_hint = (f"\n\n用户希望接下来发生的方向（请优先遵守）：{direction.strip()}"
                    if direction and direction.strip() else "")
        style_hint = f"\n\n世界观背景（仅作弱参考）：{style_weak}" if style_weak else ""
        user = (f"现有故事：\n{story_text}{chars_hint}{scenes_hint}{dir_hint}{style_hint}\n\n"
                f"续写规模：{scale_hint}\n\n请直接写出续写正文。")
        text = await self._client.chat(
            [{"role": "system", "content": _CONTINUE_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.8, max_tokens=2048)
        return text.strip()

    # ---------- Phase AI-1B：整理新增正文（JSON，只拆 continuation_text）----------
    async def organize_continuation(self, story_text: str, last_scene_summaries: list[str],
                                    continuation_text: str, style_weak: str = "") -> StoryBreakdownResult:
        """只把 continuation_text 拆成新故事段。已有 story/scenes 仅作边界上下文，不重复输出。"""
        story_tail = story_text[-600:] if len(story_text) > 600 else story_text   # 已有故事最后 ~600 字
        scenes_hint = ""
        if last_scene_summaries:
            tail = last_scene_summaries[-2:]
            scenes_hint = "\n\n已有最后的故事段：\n" + "\n".join(f"- {s}" for s in tail)
        user = (f"已有故事的最后一部分：\n{story_tail}{scenes_hint}\n\n"
                f"下面是新的续写正文（只整理这部分，不要整理已有内容）：\n{continuation_text}")
        obj = await self._chat_json(
            [{"role": "system", "content": _ORGANIZE_CONTINUATION_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.7, max_tokens=4096)
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
            mentions = [str(m).strip() for m in (s.get("character_mentions") or []) if str(m).strip()]
            scenes.append(DraftScene(draft_id=did, summary=summary, character_mentions=mentions))
        return StoryBreakdownResult(overview=overview, scenes=scenes)

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
