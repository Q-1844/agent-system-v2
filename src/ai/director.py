# src/ai/director.py
"""AI 导演（Phase AI-3，规格 提示词.md §六~§三十）。

职责窄（受控 Workflow，不是开放式 Agent）：一个故事段 Scene -> 判断拆几个镜头
-> 为每个镜头规划"本镜意图" + 出场角色 + 建议时长 -> 用户确认 -> 创建 Shot[]。
不生成首帧/尾帧/视频提示词、不生成图片视频、不写 opening_source/link_mode（§二十/二十一/二十二）。
AI 只返回 DirectorDraft；draft -> Shot[] 由 routers/director.py 普通代码完成。
"""
from typing import Optional
from pydantic import BaseModel, Field

from src.ai.story_workflow import _parse_json_object


class ShotPlanItem(BaseModel):
    """一个镜头规划草稿。进入 Shot 的核心字段：intent/characters/target_duration_s。"""
    draft_id: str
    intent: str                       # 本镜意图 -> Shot.description
    characters: list[str] = Field(default_factory=list)
    target_duration_s: float = 5.0
    purpose: str = ""                 # 给用户看的 AI 解释，不写入 project.json


class DirectorDraft(BaseModel):
    """一个故事段的 AI 镜头规划草稿。落 director_drafts.json，按 scene_id 存。"""
    draft_id: str
    scene_id: str
    overview: str = ""
    shots: list[ShotPlanItem] = Field(default_factory=list)
    # stale 比对（§二十四）：只看 scene summary/characters，不看 updated_at
    scene_summary_snapshot: str = ""
    scene_characters_snapshot: list[str] = Field(default_factory=list)
    director_note: str = ""           # 本次规划的补充指令（不存入 Shot，§五）
    created_at: float = 0.0


_DIRECTOR_SYSTEM = """你是一名影视分镜导演。你的任务是把一个"故事段"规划成若干连续镜头。

你负责：
- 判断需要几个 Shot
- 为每个 Shot 定义明确的叙事意图（intent）
- 决定当前 Scene 中哪些角色出现在这个 Shot
- 建议合理时长（target_duration_s）
- 保证镜头之间有明确推进关系

你不负责：
- 修改故事
- 创建新角色或修改角色设定
- 摄影参数（景别/机位/运镜/光照）
- 首帧/尾帧/视频 prompt
- 图片/视频生成

一个 Shot 表示一个连续镜头中的一个明确叙事动作或视觉重点。不要在单个 Shot 中描述多次切镜、场景切换、远景->特写->远景连续剪辑。如果故事需要明显不同的视觉重点，拆成多个 Shot；但不要为了拆镜而拆镜。

镜头数量：普通故事段通常 1-4 个 Shot，复杂故事段 5-6 个，超过 6 个除非故事段明显过长否则保持克制。不要固定每 Scene 3 Shot。如果故事段内容过于复杂需要 8-10 个 Shot，可在 overview 中提示"建议未来考虑拆成两个故事段"。

避免拆得太碎：不要把"抬头/眨眼/转头/抬手"各自拆成独立 Shot，除非它们真的承担不同的重要叙事信息。例如"角色苏醒"可以是两个 Shot（灯亮恢复 + 起身确认），而不是五个镜头。

避免塞得太满：一个 5 秒 Shot 不应该描述"起身+走到门口+开门+跑出+遇敌+战斗"这种多阶段内容，应拆成多个 Shot。结合 target_duration_s 保持动作密度合理。

target_duration_s：3 秒适合一个明确动作/表情变化/环境建立/简短反应；5 秒适合一个完整动作过程（起身/转身/开门/扫描）；8-10 秒适合有明确前后阶段的连续动作或较慢的环境建立。推荐 3/5/8/10 附近的合理值。一个 Shot = 一个连续镜头，不要包含多个剪辑机位。

characters：只能从当前 Scene 的出场角色中选择，不能加入 Scene 之外的角色。每个 Scene 角色应至少出现在一个 Shot 中（角色覆盖）。无角色 Scene 允许规划无人物 Shot（characters 为空数组）。

intent：本镜意图，回答"这一镜具体负责表现什么"。用中文，描述具体动作/状态/变化，不要写摄影术语（如 medium shot / low angle / cinematic lighting）。

只返回一个 JSON 对象，不要有任何解释文字：
{"overview":"一句话概述镜头规划","shots":[{"draft_id":"draft_1","intent":"本镜意图","characters":["角色A"],"target_duration_s":5,"purpose":"这一镜在叙事中的作用"}]}
draft_id 从 draft_1 开始递增。"""


class DirectorWorkflow:
    """AI 导演。client 为 AgnesClient 实例（测试可注入 fake）。"""

    def __init__(self, client):
        self._client = client

    async def _chat_json(self, messages: list[dict], **kw) -> Optional[dict]:
        """调 chat(json_mode=True) + 解析 JSON。parse 失败自动重试一次（§三十一）。"""
        raw = await self._client.chat(messages, json_mode=True, **kw)
        obj = _parse_json_object(raw)
        if obj is not None:
            return obj
        raw2 = await self._client.chat(
            messages + [{"role": "system",
                         "content": "重要：只返回一个合法的 JSON 对象，以 { 开头，不要任何说明文字、代码块标记或其他内容。"}],
            json_mode=True, **kw)
        return _parse_json_object(raw2)

    async def plan_scene(self, project, scene, director_note: str = "") -> DirectorDraft:
        """一个故事段 -> 镜头规划草稿。"""
        sid = scene.scene_id
        scene_chars = list(scene.characters)
        # 角色描述（只 name + desc，不塞 base64，§六）
        char_descs = []
        for c in project.characters:
            if c.name in scene_chars:
                char_descs.append(f"- {c.name}：{c.description_en}" if c.description_en else f"- {c.name}")

        # 相邻故事段（§六）
        scenes = project.scenes
        idx = next((i for i, sc in enumerate(scenes) if sc.scene_id == sid), -1)
        prev_summ = scenes[idx - 1].summary if idx > 0 and idx >= 0 else ""
        next_summ = scenes[idx + 1].summary if 0 <= idx < len(scenes) - 1 else ""

        # 完整故事（长则截断，§六）
        story = project.story_text or ""
        if len(story) > 2000:
            story = story[:1000] + "\n……（中略）……\n" + story[-1000:]

        # 已有 Shot（§六：只告诉数量 + 简要，不塞批次/base64）
        existing = scene.shots or []
        existing_block = ""
        if existing:
            existing_block = f"\n\n当前故事段已经存在 {len(existing)} 个镜头。" + (
                " 现有镜头意图：" + " / ".join(f"「{sh.description}」" for sh in existing[:6] if sh.description)
                if any(sh.description for sh in existing) else "")

        note_block = f"\n\n导演要求（请据此调整规划）：{director_note.strip()}" if director_note and director_note.strip() else ""
        style = project.style.suffix() if project.style else ""
        style_block = f"\n\n项目视觉风格（弱参考，不要堆摄影术语）：{style}" if style else ""

        adjacent = ""
        if prev_summ:
            adjacent += f"\n上一故事段：{prev_summ}"
        if next_summ:
            adjacent += f"\n下一故事段：{next_summ}"
        adjacent_block = (f"\n\n相邻故事段（用于不重复上一段、不提前演完下一段）：{adjacent}") if adjacent else ""

        user = (
            f"当前故事段 {sid}：\n{scene.summary or '（未填写故事）'}\n\n"
            f"出场角色：{('、'.join(scene_chars)) if scene_chars else '无'}"
            + (f"\n角色设定：\n" + "\n".join(char_descs) if char_descs else "")
            + f"\n\n完整故事：\n{story or '（无）'}"
            + adjacent_block + existing_block + note_block + style_block
            + "\n\n请把这个故事段规划成若干连续镜头。"
        )
        obj = await self._chat_json(
            [{"role": "system", "content": _DIRECTOR_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.7, max_tokens=2048)
        if obj is None:
            raise ValueError("AI 未返回可解析的 JSON")
        return _build_draft(obj, sid, scene.summary or "", scene_chars, director_note)


def _build_draft(obj: dict, scene_id: str, scene_summary: str,
                 scene_chars: list[str], director_note: str) -> DirectorDraft:
    """从 AI 返回构造 DirectorDraft，容错 + 约束 characters 只能在 scene_chars 内。"""
    overview = str(obj.get("overview") or "").strip()
    shots_raw = obj.get("shots") or []
    shots: list[ShotPlanItem] = []
    for i, s in enumerate(shots_raw, start=1):
        if not isinstance(s, dict):
            continue
        did = str(s.get("draft_id") or f"draft_{i}").strip()
        intent = str(s.get("intent") or "").strip()
        if not intent:
            continue   # 没有 intent 的镜头无意义
        raw_chars = s.get("characters") or []
        # 约束：只能从 scene_chars 选（§十二）；过滤掉 Scene 外的角色
        chars = [str(c).strip() for c in raw_chars if str(c).strip() in scene_chars]
        dur = s.get("target_duration_s")
        try:
            dur_f = float(dur) if dur is not None else 5.0
        except Exception:
            dur_f = 5.0
        if dur_f <= 0:
            dur_f = 5.0
        purpose = str(s.get("purpose") or "").strip()
        shots.append(ShotPlanItem(draft_id=did, intent=intent, characters=chars,
                                  target_duration_s=dur_f, purpose=purpose))
    return DirectorDraft(
        draft_id=f"director_{scene_id}",
        scene_id=scene_id,
        overview=overview,
        shots=shots,
        scene_summary_snapshot=scene_summary,
        scene_characters_snapshot=scene_chars,
        director_note=director_note or "",
        created_at=0.0,   # 由 router 填 time.time()
    )


def coverage_missing(draft: DirectorDraft) -> list[str]:
    """角色覆盖校验（§十三）：返回未被任何镜头覆盖的 scene 角色名。无角色 Scene 返回 []。"""
    if not draft.scene_characters_snapshot:
        return []
    covered = set()
    for sh in draft.shots:
        covered.update(sh.characters)
    return [c for c in draft.scene_characters_snapshot if c not in covered]
