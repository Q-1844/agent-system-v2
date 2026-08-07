# src/ai/character_designer.py
"""AI 角色设计（Phase AI-2，规格 提示词.md §六~§十五）。

职责窄（Character Designer Specialist，§28）：角色在故事中的叙事信息 -> 可用于生成稳定角色形象的视觉设定。
不输出故事/Scene/Shot/镜头/视频/台词；不控制项目写入（0 工具调用）。
应用 generation_prompt -> Character.description_en 由 routers/character_design.py 普通代码完成，
三视图生成复用现有角色页 generate-chain，不建第二套图片接口。
"""
from typing import Optional
from pydantic import BaseModel, Field

from src.ai.story_workflow import _parse_json_object


class CharacterDesignDraft(BaseModel):
    """AI 角色视觉设计草稿（§九）。部分字段可空（人类/机器人/动物不同）。"""
    character_name: str
    role_summary: str = ""               # 角色在故事中的作用
    visual_concept: str = ""             # 视觉概念
    appearance: str = ""                 # 外貌
    clothing_or_body: str = ""           # 服装 / 身体结构
    distinctive_features: str = ""       # 长期识别特征
    color_palette: str = ""              # 配色
    consistency_rules: list[str] = Field(default_factory=list)  # 一致性规则
    generation_prompt: str = ""          # 真正用于图片生成的英文 prompt


class CharacterDesignDraftState(BaseModel):
    """一个角色 AI 设计草稿的完整状态（落 character_designs.json）。"""
    character_name: str
    base_character_name: str             # 创建时的角色名（改名检测，§25）
    base_character_description: str      # 创建时的 description_en（stale 比对，§21）
    base_related_scene_signature: list[dict]   # 该角色出现的场景签名 [{scene_id, summary}]
    base_project_style_signature: str    # 创建时的项目风格（stale 比对）
    draft: CharacterDesignDraft
    status: str = "draft"                # draft / applied / stale
    created_at: float = 0.0


_DESIGN_SYSTEM = """你是一名影视角色视觉设计师。根据故事中这个角色的身份、经历和作用，为后续 AI 图片生成设计一个稳定、容易重复生成的角色视觉设定。

要求：
1. 视觉设计必须服务于故事中的角色身份。
2. 设计需要清楚、具体、可重复。
3. 给角色保留 2-4 个非常明显的长期识别特征。
4. 不要堆大量微小随机细节。
5. 避免依赖图片模型很难稳定保持的细节。
6. 与项目已有主要角色在轮廓、颜色、服装/身体结构上保持足够区分。
7. 不改变故事里已经明确的人物：年龄、性别、身份、种族/物种、关键装备。
8. 如果故事没有明确某一视觉细节，可以合理设计。
9. 不修改故事。
10. 不产生镜头或摄影信息。
11. generation_prompt 必须是英文，适合 Agnes Image 2.1 生成角色设定图/三视图：描述外貌、服装、颜色、识别特征，适合多个镜头持续复用。不要包含镜头/运镜/背景故事。
12. 角色必须适合多个镜头持续复用。

只返回一个 JSON 对象，不要有任何解释文字，格式如下：
{"character_name":"角色名","role_summary":"角色在故事中的作用","visual_concept":"视觉概念","appearance":"外貌","clothing_or_body":"服装或身体结构","distinctive_features":"长期识别特征","color_palette":"配色","consistency_rules":["一致性规则1","一致性规则2"],"generation_prompt":"英文生成 prompt"}
根据角色是人类/机器人/动物等，可以留空不适用的字段（空字符串），但 generation_prompt 必须有内容。"""


class CharacterDesigner:
    """AI 角色设计师。client 为 AgnesClient 实例（测试可注入 fake）。"""

    def __init__(self, client):
        self._client = client

    async def _chat_json(self, messages: list[dict], **kw) -> Optional[dict]:
        """调 chat(json_mode=True) + 解析 JSON。parse 失败自动重试一次（最多一次）。"""
        raw = await self._client.chat(messages, json_mode=True, **kw)
        obj = _parse_json_object(raw)
        if obj is not None:
            return obj
        raw2 = await self._client.chat(
            messages + [{"role": "system",
                         "content": "重要：只返回一个合法的 JSON 对象，以 { 开头，不要任何说明文字、代码块标记或其他内容。"}],
            json_mode=True, **kw)
        return _parse_json_object(raw2)

    async def design_character(
        self,
        character_name: str,
        character_description: str,
        story_text: str,
        related_scene_summaries: list[tuple[str, str]],   # [(scene_id, summary)]
        other_chars: list[tuple[str, str]],                # [(name, short_desc)]
        style_weak: str = "",
    ) -> CharacterDesignDraft:
        """角色叙事信息 -> 视觉设定草稿。"""
        # 完整故事：中短直接给，很长的截断（v1 不做 RAG，§八）
        story = story_text or ""
        if len(story) > 2000:
            story = story[:1000] + "\n……（中略）……\n" + story[-1000:]
        story_block = f"\n\n完整故事：\n{story}" if story else "\n\n（无完整故事）"

        # 该角色出现的故事段（最重要上下文，§八）
        if related_scene_summaries:
            scenes_block = "\n\n该角色出现的故事段：\n" + "\n".join(
                f"故事段 {sid}：{summ}" for sid, summ in related_scene_summaries)
        else:
            scenes_block = "\n\n（故事段中未显式标注该角色出场，请根据故事推断）"

        # 其他主要角色（避免设计相似，§八）-- 不塞 base64
        if other_chars:
            others_block = "\n\n项目其他主要角色（请在轮廓/颜色/服装上与其区分）：\n" + "\n".join(
                f"- {n}：{d[:100]}" if d else f"- {n}" for n, d in other_chars[:15])
        else:
            others_block = ""

        style_block = f"\n\n项目视觉风格（弱参考，三视图比例不由此决定）：{style_weak}" if style_weak else ""

        user = (f"角色名称：{character_name}\n"
                f"角色现有描述：{character_description or '（空，需根据故事设计）'}"
                f"{story_block}{scenes_block}{others_block}{style_block}\n\n"
                f"请为这个角色设计视觉设定。")
        obj = await self._chat_json(
            [{"role": "system", "content": _DESIGN_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.7, max_tokens=1500)
        if obj is None:
            raise ValueError("AI 未返回可解析的 JSON")
        return _build_draft(obj, character_name)


def _build_draft(obj: dict, fallback_name: str) -> CharacterDesignDraft:
    """从 AI 返回 dict 构造 CharacterDesignDraft，容错缺字段。"""
    def g(k: str) -> str:
        v = obj.get(k)
        return str(v).strip() if v else ""

    rules_raw = obj.get("consistency_rules") or []
    rules = [str(r).strip() for r in rules_raw if str(r).strip()]
    name = g("character_name") or fallback_name
    return CharacterDesignDraft(
        character_name=name,
        role_summary=g("role_summary"),
        visual_concept=g("visual_concept"),
        appearance=g("appearance"),
        clothing_or_body=g("clothing_or_body"),
        distinctive_features=g("distinctive_features"),
        color_palette=g("color_palette"),
        consistency_rules=rules,
        generation_prompt=g("generation_prompt"),
    )
