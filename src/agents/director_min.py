# src/agents/director_min.py
# AI-7D-R · Director 最小 agent 循环（测试弹窗）。
#
# 工具：
#   add_scenes_from_script —— 分割剧本【新增】故事段；可选 after_scene_id 在指定段之后插入（不传=追加末尾）。
#   add_shots_to_scene     —— 给故事段追加镜头（可对已有镜头的段继续追加）；可选 after_shot_id 指定位置。
#   update_scene / update_shot —— 原地改段概要/角色、镜头描述/角色/时长（非破坏，不确认）。
#   delete_scene / delete_shot —— 依赖感知级联删除（confirmed 两阶段：先查依赖说明，用户确认后删）。
#
# 安全规则：
#   - 新增/修改：非破坏，不删已有产物、不碰角色、不碰其他段。
#   - 删除：结构依赖由共享服务 src.storyboard.delete_service 确定性算出（代码决定，不由 LLM）；
#     agent 先 confirmed=false 取依赖说明，转述给用户并问"确定删除吗？"，用户明确肯定后
#     再 confirmed=true 执行。删故事段连带其镜头+产物+该段制作计划；删镜头连带接力下游镜头。
#     角色不删（Character Designer 负责）。
#   - 有视频正在生成时，删除会被系统拒绝（并发安全，非权限审核），告知用户等生成结束再删。
#   - 结构位置/分配由 src.storyboard.structure_service 确定性处理（id 身份证号，插入不重排已有 id）。
# 落盘：ProjectStore.save（原子 canonical 写）+ 审计日志 + 工作记忆。
from __future__ import annotations
import json
import re
from typing import Any, Optional

from src.core.store import ProjectStore
from src.core.models import Scene, Shot
from src.storyboard.structure_service import (
    insert_scenes_after, insert_shots_after,
    update_scene_fields, update_shot_fields, update_shots_batch,
    read_shot, read_scene, read_script, list_scenes, StructureError,
)

from .models import AgentMessage, ResultStatus
from .runtime import AgentRuntime, AgentError
from .revision_audit import DirectEditAuditStore


DIRECTOR_MIN_SYSTEM = """你是 AGNES 的 Director，负责把剧本正文（story_text）分割成故事段（Scene）、把每个故事段拆成镜头（Shot），并在用户要求时读取、修改或删除故事段/镜头。

你一共有十个工具（3 个只读 + 7 个写入）。**只读工具真实存在且随时可用**，绝不要声称"没有读取工具/无法读取"——要知道任何当前值，就调用它们。

【只读工具——不落盘，可在一轮内连续调用】
你可以连续调用多个只读工具收集信息（例如先 read_script，再 list_scenes，再对几个段 read_scene/read_shot），系统会把每次结果喂回给你，直到你能给出最终回答或决定写入。不要凭空臆测现状。
R1) read_script —— 读取完整剧本正文。拆分镜、理解剧情、回答"剧本写了什么"前**必须先调它**，不要凭下方预览臆测。无参数：{"tool":"read_script"}
R2) read_shot {"shot_id":"s01_c02"} —— 读取一个镜头的完整当前参数（时长、景别、机位、构图、运镜、光线、运动、开场来源/接力、描述等）。
R3) read_scene {"scene_id":"s01"} —— 读取一个故事段及其所有镜头的完整参数。
R4) list_scenes —— 一次性列出所有故事段及镜头的结构化信息（id/概要/角色/镜头数/每镜时长与接力）。要做全片对比、增删建议时用它最高效。无参数：{"tool":"list_scenes"}

【写入工具】
W1) add_scenes_from_script：【新增】故事段。scenes 数组每项：
   {"summary": "概要（中文，具体动作/场景/情绪）", "characters": ["出场角色名"]}
   可选 "after_scene_id": "s02" —— 插到 s02 之后；不传则追加末尾。
   例：{"tool":"add_scenes_from_script","after_scene_id":"s01","scenes":[{"summary":"...","characters":["..."]}]}
W2) add_shots_to_scene：给故事段新增镜头（可对已有镜头的段继续追加）。shots 每项：
   {"description": "画面", "characters": ["出场角色"], "target_duration_s": 5}
   可选 "after_shot_id" 插到指定镜头之后；不传则段尾追加。target_duration_s 取 3/5/8/10。
   例：{"tool":"add_shots_to_scene","scene_id":"s01","after_shot_id":"s01_c01","shots":[{"description":"...","characters":["..."],"target_duration_s":5}]}
W3) update_scene：原地改故事段。{"scene_id":"s01","summary":"...","characters":[...]}（只传要改的字段）。
W4) update_shot：原地改单个镜头。可写 description/characters/target_duration_s 及拍摄方案字段：
   shot_size（景别）、camera_angle（机位）、composition（构图）、camera_movement（运镜）、
   lighting（光线）、motion（画面运动）、important_details（细节/情绪）。只传要改的字段。
   例：{"tool":"update_shot","shot_id":"s01_c02","shot_size":"close-up","camera_movement":"缓慢推镜"}
W5) update_shots：**批量**改一段内多个镜头（一次给整段拍摄方案最高效）。每个 shot 带已存在 shot_id + 要改字段：
   {"scene_id":"s02","shots":[{"shot_id":"s02_c01","shot_size":"...","camera_movement":"...","lighting":"..."}]}
   中文维度映射：景别→shot_size；机位角度→camera_angle；构图/入画→composition；
   运镜→camera_movement；画面运动/动作→motion；光线→lighting；情绪/氛围/质感→important_details。
W6) delete_scene：删故事段，两阶段确认。{"scene_id":"s01","confirmed":false}
W7) delete_shot：删镜头，两阶段确认。{"shot_id":"s01_c02","confirmed":false}

关于位置与编号：id（s01、s01_c02）是身份证号，插入只取下一个编号、**不改已有 id**；顺序由列表位置决定。

🔴 工作流：读取/回答现状 → 用 R1/R2/R3；改动前若不确定当前值，先 read 再 write。绝不要凭记忆或目录里的截断描述臆测当前参数。

🔴 输出工具调用必须带 "tool" 字段，且**只输出一个 JSON 对象**，不要把 JSON 当说明文字贴出来——没有 tool 字段的 JSON 不会被执行。

🔴 修改（W3/W4/W5）直接生效，不需确认，不删产物；改动后该段制作计划自动标过期，无需你处理。

🔴 删除（W6/W7）必须分两步（依赖由系统算出，不由你编）：
   a. 第一次 confirmed=false → 系统返回会连带删什么；你转述并问「确定删除吗？」，不要声称已删。
   b. 用户明确肯定（确定/确认/删吧/yes）后，第二次同一 id、confirmed=true 才真正级联删除。
   - 删段连带其全部镜头+产物+该段计划；删镜连带接力依赖它的下游镜头；角色和其他段都不删。
   - 不删角色（Character Designer 负责）。

🔴 关键：输出 JSON 就等于**真实调用工具**，运行时会自动执行并写入项目。你**能**、也**正在**通过输出 JSON 真实修改项目。绝不可说“我无法写入/这只是格式示例/我没有权限”——这些是错的。输出 JSON，系统就落盘。

行为规则：
- 问"现在是什么/时长多少/什么运镜/剧本写了什么" → 先调 R1/R2/R3 读取，再回答。
- 拆剧本成段/加段 → W1（要插某段之后给 after_scene_id）。
- 加/拆镜头 → W2（要插某镜之后给 after_shot_id）。
- 改段 → W3；改单镜 → W4；一次给整段多镜拍摄方案 → W5（最高效）。改前不确定当前值就先 R2/R3 读。
- 用户给的中文拍摄维度（景别/机位/构图/运镜/光线/运动/情绪）按 W5 的字段映射填，不要直接贴原始 JSON。
- 删除 → W6/W7 先走 confirmed=false。
- characters 只能选自项目已有角色；不知道就给空数组。
- 调用工具时**只输出一个 JSON 对象**，用单花括号，不要附加解释或代码块标记。
- 不调用工具时（闲聊/提问）自然语言回复，不输出 JSON。

🔴 插入位置规则：可在任意镜头之后插入，**但两个有接力关系的镜头之间不能插**（下一镜开场来源 inherit/reference 且接力当前镜）。系统会拒绝并返回 dependency_block（用 R2 查 source_shot_id 判断）。告诉用户"这两镜有接力，不能在中间插；可在接力链末端之后插，或先解除接力"。故事段之间无此限制。

🔴 下方只给剧本预览和镜头目录用于定位；完整剧本用 R1 读，完整镜头参数用 R2/R3 读，不要凭目录/预览臆测。

{story_text}

故事段与镜头目录（完整参数用 R1/R2/R3 读取）：
{existing_scenes}
"""


def _extract_json_objects(text: str) -> list:
    """从一段文本里提取所有平衡的 {...} JSON 对象（处理嵌套大括号、字符串内的括号）。
    支持 LLM 用 <tool_call>...</tool_call> 包裹、或一次输出多个工具调用。"""
    if not text:
        return []
    # 去掉 <tool_call> / </tool_call> / 代码块标记
    cleaned = re.sub(r"</?tool_call>", "", text)
    cleaned = cleaned.replace("```json", "```").replace("```", " ")
    # 双花括号 {{ }} 是模板转义，折叠
    cleaned = cleaned.replace("{{", "{").replace("}}", "}")
    objs = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    quote = ""
    for i, ch in enumerate(cleaned):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = cleaned[start:i + 1]
                    try:
                        objs.append(json.loads(candidate))
                    except Exception:
                        pass
                    start = -1
    return objs


def _normalize_tool(obj: dict) -> Optional[dict]:
    """把一个解析出的 JSON 对象归一化成受支持的工具调用；不认识的返回 None。"""
    if not isinstance(obj, dict):
        return None
    t = obj.get("tool")
    if t == "add_scenes_from_script" and isinstance(obj.get("scenes"), list):
        return obj
    if t == "add_shots_to_scene" and isinstance(obj.get("shots"), list) and obj.get("scene_id"):
        return obj
    if t == "delete_scene" and obj.get("scene_id"):
        return obj
    if t == "delete_shot" and obj.get("shot_id"):
        return obj
    if t == "update_scene" and obj.get("scene_id"):
        return obj
    if t == "update_shot" and obj.get("shot_id"):
        return obj
    if t == "update_shots" and obj.get("scene_id") and isinstance(obj.get("shots"), list):
        return obj
    if t in ("read_shot", "get_shot_plan", "get_shot", "query_shot") and obj.get("shot_id"):
        obj["tool"] = "read_shot"
        return obj
    if t in ("read_scene", "get_scene", "query_scene") and obj.get("scene_id"):
        obj["tool"] = "read_scene"
        return obj
    if t in ("read_script", "get_script", "read_story", "get_story_text"):
        obj["tool"] = "read_script"
        return obj
    if t in ("list_scenes", "get_scene_list", "list_storyboard"):
        obj["tool"] = "list_scenes"
        return obj
    return None


def _parse_tool_calls(raw: str) -> list:
    """从 LLM 回复里解析出所有工具调用（支持 <tool_call> 标签、一条回复多个工具）。
    按出现顺序返回归一化后的 dict 列表。"""
    objs = _extract_json_objects(raw)
    calls = []
    for o in objs:
        n = _normalize_tool(o)
        if n is not None:
            calls.append(n)
    return calls


def _parse_tool_call(raw: str) -> Optional[dict]:
    """兼容旧调用：返回回复里第一个工具调用（支持 <tool_call> 标签）。
    新逻辑用 _parse_tool_calls 支持一条回复多个工具。"""
    calls = _parse_tool_calls(raw)
    return calls[0] if calls else None


def _script_preview(proj) -> str:
    """剧本预览：字数 + 开头 200 字。完整剧本让 agent 调 read_script 读，避免长文本常驻。"""
    text = (proj.story_text or "").strip()
    if not text:
        return "剧本正文：（空）"
    head = text[:200]
    more = "…" if len(text) > 200 else ""
    return f"剧本正文（共 {len(text)} 字，以下为开头预览，完整内容用 read_script 读取）：\n{head}{more}"


def _project_context_text(proj) -> str:
    """轻量目录：每个故事段一行（id/概要/角色/镜头数），下面列镜头 id + 一句描述截断。
    🔴 不把所有镜头的完整参数塞进上下文——大项目（几百个镜头）会撑爆上下文且不实时。
    需要某镜头的完整当前参数（时长/机位/运镜/光线/接力等）时，agent 必须调 read_shot/read_scene
    实时读取，不要臆测，也不要依赖这里的截断描述。"""
    if not proj.scenes:
        return "（尚无故事段）"
    lines = []
    for sc in proj.scenes:
        chars = "、".join(sc.characters) if sc.characters else "无"
        lines.append(f"- {sc.scene_id} {sc.summary[:60]}｜角色: {chars}｜{len(sc.shots)} 个镜头")
        if not sc.shots:
            lines.append("  （尚无镜头，可拆分）")
            continue
        for sh in sc.shots:
            desc = (sh.description or "").strip().replace("\n", " ")
            if len(desc) > 50:
                desc = desc[:50] + "…"
            tail = f" — {desc}" if desc else ""
            lines.append(f"  · {sh.shot_id}{tail}")
    return "\n".join(lines)


def _as_bool(v, default: bool = False) -> bool:
    """confirmed 字段容错：接受 bool / "true"/"false" / 1/0。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "确认", "确定")
    return default


def _execute_add_shots(rt: AgentRuntime, project: str, scene_id: str,
                        shots_raw: list, after_shot_id: Optional[str],
                        instruction: str, message_id: Optional[str]) -> dict:
    """执行 add_shots_to_scene：把故事段拆成镜头插入/追加。可对已有镜头的段继续追加（无锁）。
    after_shot_id 非空 → 插到该镜头之后；None/空 → 追加段尾。位置/id/index 由
    structure_service 确定性处理（id 取段内 max _cNN+1 不回收，index 重排连续）。
    add-only，不碰已有 shot 的创作字段。"""
    if not isinstance(shots_raw, list) or not shots_raw:
        raise AgentError("add_shots_empty", "未提供任何镜头")
    pdir = rt._resolve_project_dir(project)
    pstore = ProjectStore(pdir)
    proj = pstore.load()
    valid_chars = {c.name for c in proj.characters}
    specs = []
    for sh in shots_raw:
        if not isinstance(sh, dict):
            continue
        desc = str(sh.get("description") or "").strip()
        if not desc:
            continue
        chars = [str(c).strip() for c in (sh.get("characters") or []) if str(c).strip()]
        chars = [c for c in chars if not valid_chars or c in valid_chars]
        dur = sh.get("target_duration_s")
        if dur is None:
            dur = 5
        try:
            dur = float(dur)
            if dur <= 0:
                dur = 5.0
        except Exception:
            dur = 5.0
        specs.append({"description": desc, "characters": chars, "target_duration_s": dur})
    if not specs:
        raise AgentError("add_shots_empty", "提供的镜头都缺少有效 description")
    try:
        new_shots = insert_shots_after(proj, scene_id, after_shot_id or None, specs)
    except StructureError as e:
        raise AgentError(e.code, e.message)
    pstore.save(proj)   # 原子 canonical 写
    added = [{"shot_id": sh.shot_id, "index": sh.index, "description": sh.description,
              "characters": list(sh.characters), "target_duration_s": sh.target_duration_s}
             for sh in new_shots]
    DirectEditAuditStore(pdir, project).record(
        agent_id="director", field=f"add_shots:{scene_id}", before=[],
        after=[a["shot_id"] for a in added], instruction=instruction, message_id=message_id)
    where = f"在 {after_shot_id} 之后" if after_shot_id else "段尾"
    rt.record_work(project, "change",
                   f"Director 给故事段 {scene_id} {where}新增 {len(added)} 个镜头",
                   participants=["director"], affected_domains=["storyboard"],
                   references={}, status="completed")
    return {"added": added, "count": len(added), "scene_id": scene_id}


def _execute_add_scenes(rt: AgentRuntime, project: str, scenes_raw: list,
                        after_scene_id: Optional[str],
                        instruction: str, message_id: Optional[str]) -> dict:
    """执行 add_scenes_from_script：校验 + 分配 id + 插入/追加 + 原子写 + 审计。
    after_scene_id 非空 → 插到该段之后；None/空 → 追加末尾。位置/id 由 structure_service
    确定性处理（id 取下一个 s{NN}，删除不回收）。"""
    if not isinstance(scenes_raw, list) or not scenes_raw:
        raise AgentError("add_scenes_empty", "未提供任何新增故事段")
    pdir = rt._resolve_project_dir(project)
    pstore = ProjectStore(pdir)
    proj = pstore.load()
    valid_chars = {c.name for c in proj.characters}
    specs = []
    for s in scenes_raw:
        if not isinstance(s, dict):
            continue
        summary = str(s.get("summary") or "").strip()
        if not summary:
            continue   # 跳过空 summary 的段
        chars = [str(c).strip() for c in (s.get("characters") or []) if str(c).strip()]
        chars = [c for c in chars if not valid_chars or c in valid_chars]
        specs.append({"summary": summary, "characters": chars})
    if not specs:
        raise AgentError("add_scenes_empty", "提供的 scenes 都缺少有效 summary")
    try:
        new_scenes = insert_scenes_after(proj, after_scene_id or None, specs)
    except StructureError as e:
        raise AgentError(e.code, e.message)
    pstore.save(proj)   # 原子 canonical 写
    added = [{"scene_id": sc.scene_id, "summary": sc.summary,
              "characters": list(sc.characters)} for sc in new_scenes]
    DirectEditAuditStore(pdir, project).record(
        agent_id="director", field="add_scenes", before=[],
        after=[a["scene_id"] for a in added], instruction=instruction, message_id=message_id)
    where = f"在 {after_scene_id} 之后" if after_scene_id else "末尾"
    rt.record_work(project, "change",
                   f"Director {where}新增 {len(added)} 个故事段",
                   participants=["director"], affected_domains=["storyboard"],
                   references={}, status="completed")
    return {"added": added, "count": len(added)}


def _execute_update_scene(rt: AgentRuntime, project: str, scene_id: str,
                          fields: dict, instruction: str,
                          message_id: Optional[str]) -> dict:
    """update_scene：原地改故事段概要/角色（非破坏，不确认）。只传要改的字段。"""
    pdir = rt._resolve_project_dir(project)
    pstore = ProjectStore(pdir)
    proj = pstore.load()
    before = next((s for s in proj.scenes if s.scene_id == scene_id), None)
    if before is None:
        raise AgentError("scene_not_found", f"故事段 {scene_id} 不存在")
    valid_chars = {c.name for c in proj.characters}
    summary = fields.get("summary")
    chars_raw = fields.get("characters")
    if summary is not None:
        summary = str(summary).strip()
    characters = None
    if chars_raw is not None:
        characters = [str(c).strip() for c in chars_raw if str(c).strip()]
        characters = [c for c in characters if not valid_chars or c in valid_chars]
    try:
        res = update_scene_fields(proj, scene_id, summary=summary, characters=characters)
    except StructureError as e:
        raise AgentError(e.code, e.message)
    after = next(s for s in proj.scenes if s.scene_id == scene_id)
    pstore.save(proj)
    DirectEditAuditStore(pdir, project).record(
        agent_id="director", field=f"update_scene:{scene_id}",
        before={k: (before.summary if k == "summary" else list(before.characters))
                for k in res["changed"]},
        after={k: (after.summary if k == "summary" else list(after.characters))
               for k in res["changed"]},
        instruction=instruction, message_id=message_id)
    rt.record_work(project, "change",
                   f"Director 修改了故事段 {scene_id} 的 {'、'.join(res['changed'])}",
                   participants=["director"], affected_domains=["storyboard"],
                   references={}, status="completed")
    return {"scene_id": scene_id, "changed": res["changed"],
            "summary": after.summary, "characters": list(after.characters)}


_SHOT_FIELD_LABELS = {
    "description": "画面描述", "characters": "出场角色", "target_duration_s": "时长(秒)",
    "shot_size": "景别", "camera": "机位+运镜(旧)", "camera_angle": "机位角度",
    "camera_movement": "运镜", "composition": "构图", "lighting": "光线",
    "motion": "画面运动", "motion_pace": "节奏", "environment_motion": "环境运动",
    "action_sequence": "动作顺序", "opening_state": "起始状态",
    "important_details": "重要细节", "last_frame_description": "尾帧描述",
    "end_state": "结束状态", "frame_change": "帧间变化",
    "transition_description": "过渡描述", "opening_source": "开场来源",
    "source_shot_id": "接力来源镜头", "link_mode": "视频模式", "note": "备注",
}


def _format_shot_read(d: dict) -> str:
    """把 read_shot 返回的字段 dict 排成中文可读文本（只列非空字段）。"""
    lines = [f"镜头 {d.get('shot_id')}（故事段 {d.get('scene_id')}，第 {d.get('index')} 镜）："]
    for k, label in _SHOT_FIELD_LABELS.items():
        v = d.get(k)
        if v in (None, "", [], 0) and k != "target_duration_s":
            continue
        if k == "target_duration_s" and v in (None, 0):
            continue
        if isinstance(v, list):
            v = "、".join(str(x) for x in v) if v else "（无）"
        lines.append(f"  {label}：{v}")
    return "\n".join(lines)


def _format_scene_read(d: dict) -> str:
    """把 read_scene 返回排成中文可读文本。"""
    chars = "、".join(d.get("characters") or []) or "无"
    lines = [f"故事段 {d.get('scene_id')}：{d.get('summary') or ''}",
             f"出场角色：{chars}，共 {len(d.get('shots') or [])} 个镜头："]
    for sh in d.get("shots") or []:
        lines.append("  " + _format_shot_read(sh).replace("\n  ", "\n    "))
    return "\n".join(lines)


def _prompt_invalidation_note(invalidated: dict) -> str:
    """把 {shot_id: {first_frame/last_frame/video: cleared|manual_override}} 汇总成一句提示。
    cleared=旧自动 prompt 已清，下次生成按新字段重拼；manual_override=用户手写提示词保留（改动不生效）。"""
    if not invalidated:
        return ""
    cleared_shots = [sid for sid, act in invalidated.items()
                     if any(v == "cleared" for v in act.values())]
    manual_shots = [sid for sid, act in invalidated.items()
                    if any(v == "manual_override" for v in act.values())]
    notes = []
    if cleared_shots:
        notes.append("、".join(cleared_shots) + " 的旧自动提示词已清空，下次生成会按新方案重新拼提示词")
    if manual_shots:
        notes.append("、".join(manual_shots) + " 有手动提示词（override），已保留；若要按新方案生成请在工作台清掉手动提示词")
    return "\n（" + "；".join(notes) + "。）" if notes else ""


def _clean_shot_fields(fields: dict, valid_chars: set) -> dict:
    """把 LLM 给的镜头字段清洗成结构服务可写的形式：characters 按项目角色过滤，
    其余原样透传（白名单/类型由 update_shot_fields 负责）。None 值剔除。"""
    out = {}
    for k, v in (fields or {}).items():
        if v is None:
            continue
        if k == "characters":
            names = [str(x).strip() for x in v if str(x).strip()]
            out[k] = [n for n in names if not valid_chars or n in valid_chars]
        elif k == "description":
            out[k] = str(v).strip()
        else:
            out[k] = v
    return out


def _execute_update_shot(rt: AgentRuntime, project: str, shot_id: str,
                         fields: dict, instruction: str,
                         message_id: Optional[str]) -> dict:
    """update_shot：原地改镜头字段（描述/角色/时长 + 机位/构图/运镜/光线等结构化字段，
    非破坏，不确认）。只传要改的字段。改了影响画面的字段会自动让旧自动 prompt 失效。"""
    pdir = rt._resolve_project_dir(project)
    pstore = ProjectStore(pdir)
    proj = pstore.load()
    before = proj.get_shot(shot_id)
    if before is None:
        raise AgentError("shot_not_found", f"镜头 {shot_id} 不存在")
    valid_chars = {c.name for c in proj.characters}
    cleaned = _clean_shot_fields(fields, valid_chars)
    try:
        res = update_shot_fields(proj, shot_id, cleaned)
    except StructureError as e:
        raise AgentError(e.code, e.message)
    after = proj.get_shot(shot_id)
    pstore.save(proj)
    DirectEditAuditStore(pdir, project).record(
        agent_id="director", field=f"update_shot:{shot_id}",
        before={k: getattr(before, k) for k in res["changed"]},
        after={k: getattr(after, k) for k in res["changed"]},
        instruction=instruction, message_id=message_id)
    rt.record_work(project, "change",
                   f"Director 修改了镜头 {shot_id} 的 {'、'.join(res['changed'])}",
                   participants=["director"], affected_domains=["storyboard"],
                   references={}, status="completed")
    return {"shot_id": shot_id, "changed": res["changed"],
            "prompts_invalidated": res.get("prompts_invalidated", {}),
            "description": after.description, "characters": list(after.characters),
            "target_duration_s": after.target_duration_s}


def _active_production_reason(project: str) -> Optional[str]:
    """§20 best-effort：该项目有正在跑的生产流/视频 job 时返回原因串，否则 None。
    复用既有并发安全护栏（删正在生成的镜头会让 worker 写回空镜头）。测试环境无生产模块→None。"""
    try:
        from src.agents.change_protocol.execution_service import _active_production
        return _active_production(project)
    except Exception:
        return None


def _execute_update_shots(rt: AgentRuntime, project: str, scene_id: str,
                          shots_raw: list, instruction: str,
                          message_id: Optional[str]) -> dict:
    """update_shots：批量更新一个段内多个镜头的创作字段（含机位/构图/运镜/光线等结构化字段）。
    非破坏、不确认。shot 必须已存在且属于该段（否则 StructureError not_found）。
    未知字段被结构服务忽略（不报错），避免 LLM 多吐字段整轮失败。"""
    if not isinstance(shots_raw, list) or not shots_raw:
        raise AgentError("update_shots_empty", "未提供任何镜头更新")
    pdir = rt._resolve_project_dir(project)
    pstore = ProjectStore(pdir)
    proj = pstore.load()
    valid_chars = {c.name for c in proj.characters}
    # 清洗：只保留 shot_id + 白名单字段；characters 按项目角色过滤
    cleaned = []
    for item in shots_raw:
        if not isinstance(item, dict) or not item.get("shot_id"):
            continue
        c = {"shot_id": str(item["shot_id"])}
        c.update(_clean_shot_fields(
            {k: v for k, v in item.items() if k != "shot_id"}, valid_chars))
        cleaned.append(c)
    if not cleaned:
        raise AgentError("update_shots_empty", "提供的镜头都缺少 shot_id")
    try:
        res = update_shots_batch(proj, scene_id, cleaned)
    except StructureError as e:
        raise AgentError(e.code, e.message)
    pstore.save(proj)
    changed_ids = [r["shot_id"] for r in res["results"] if r["changed"]]
    DirectEditAuditStore(pdir, project).record(
        agent_id="director", field=f"update_shots:{scene_id}", before=[],
        after=changed_ids, instruction=instruction, message_id=message_id)
    rt.record_work(project, "change",
                   f"Director 更新了故事段 {scene_id} 的 {len(changed_ids)} 个镜头方案",
                   participants=["director"], affected_domains=["storyboard"],
                   references={}, status="completed")
    return {"scene_id": scene_id, "results": res["results"],
            "count": len(changed_ids),
            "prompts_invalidated": res.get("prompts_invalidated", {}),
            "updated_shots": [{"shot_id": r["shot_id"], "changed": r["changed"]}
                              for r in res["results"] if r["changed"]]}


def _execute_delete(rt: AgentRuntime, project: str, entity_type: str, entity_id: str,
                    confirmed: bool, instruction: str,
                    message_id: Optional[str]) -> dict:
    """delete_scene / delete_shot 两阶段执行。
    confirmed=False：调 preview（纯读，不落盘/不审计/不写工作记忆），返回依赖说明。
    confirmed=True：活跃生产护栏 + execute_delete 级联删 + 审计 + 工作记忆。"""
    from src.storyboard.delete_service import preview_delete, execute_delete, DeleteError
    pdir = rt._resolve_project_dir(project)
    proj = ProjectStore(pdir).load()

    if not confirmed:
        prev = preview_delete(proj, entity_type, entity_id)
        if not prev.get("supported", True):
            # 角色等不支持类型：把说明文本回给 agent，让它解释
            return {"confirmed": False, "preview": prev}
        reason = _active_production_reason(project)
        if reason:
            prev = dict(prev)
            prev["active_production"] = reason
            prev["text"] = (prev.get("text", "") +
                            f"\n\n⚠️ 当前项目有正在进行的视频生成（{reason}），"
                            "请等生成结束后再确认删除。")
        return {"confirmed": False, "preview": prev}

    # confirmed=True：并发安全护栏
    reason = _active_production_reason(project)
    if reason:
        raise AgentError("production_active",
                         f"当前项目有正在进行的视频生成（{reason}），等生成结束后再删除")
    try:
        res = execute_delete(pdir, proj, entity_type, entity_id)
    except DeleteError as e:
        raise AgentError(e.code, e.message)
    deleted = res["deleted"]
    # 审计（before=被删实体 id，after=空）
    DirectEditAuditStore(pdir, project).record(
        agent_id="director",
        field=f"delete_{entity_type}:{entity_id}",
        before={"scenes": deleted["scenes"], "shots": deleted["shots"],
                "plans": deleted["plans"], "removed_files": res["removed_files"]},
        after=[], instruction=instruction, message_id=message_id)
    parts = []
    if deleted["scenes"]:
        parts.append(f"故事段 {'、'.join(deleted['scenes'])}")
    if deleted["shots"]:
        parts.append(f"镜头 {'、'.join(deleted['shots'])}")
    summary = f"Director 删除了 {len(deleted['scenes'])} 个故事段、{len(deleted['shots'])} 个镜头"
    rt.record_work(project, "change", summary,
                   participants=["director"], affected_domains=["storyboard"],
                   references={}, status="completed")
    return {
        "confirmed": True,
        "deleted": deleted,
        "removed_files": res["removed_files"],
        "plan_removed": res["plan_removed"],
        "message": f"已删除{'、'.join(parts)}"
                   + (f"，清理 {res['removed_files']} 个生成资产文件" if res["removed_files"] else "")
                   + "。",
    }


async def director_turn(rt: AgentRuntime, project: str, session_id: str,
                         content: str) -> dict:
    """Director 最小 agent 循环：上下文 → LLM → (工具调用 | 对话) → 执行 → 落盘 → 诚实回复。"""
    store = rt._session_store(project)
    session = store.load(session_id)
    if session is None:
        raise AgentError("unknown_scope", f"session 不存在: {session_id}")
    if session.agent_id != "director":
        raise AgentError("permission_denied", "测试弹窗仅限 Director")
    if not rt._is_enabled("director"):
        raise AgentError("disabled_agent", "Director 已禁用")

    user_msg = AgentMessage(role="user", content=content)
    store.append_message(session_id, user_msg)
    rt._maybe_capture_preference(project, content, user_msg.message_id)
    session = store.load(session_id)

    pdir = rt._resolve_project_dir(project)
    proj = ProjectStore(pdir).load()
    history = [{"role": m.role, "content": m.content}
               for m in session.messages[:-1]
               if m.role in ("user", "assistant") and m.content and m.status is None]

    client = rt._resolve_client_or_none()
    assistant_text: str
    status: Optional[str] = None
    edited = False
    # 多步写入：结果累积成列表（一轮内可连续增/改/删多个实体）
    added_scenes: list = []
    added_shots: list = []
    updated_scenes: list = []
    updated_shots: list = []
    deleted_scenes: list = []
    deleted_shots: list = []
    removed_files: int = 0
    plan_removed: bool = False
    deleted_preview: Optional[dict] = None
    events: list = []

    if client is None:
        assistant_text = "[chat 未配置：Agnes 推理模型不可用，无法拆分。请配置 API Key。]"
        status = ResultStatus.NOT_CONFIGURED.value
    else:
        messages = [{"role": "system",
                     "content": DIRECTOR_MIN_SYSTEM.replace(
                         "{story_text}", _script_preview(proj)).replace(
                         "{existing_scenes}", _project_context_text(proj))}]
        mem = rt._memory_supplement(project, "director")
        if mem:
            messages[0]["content"] += "\n\n" + mem
        messages += history
        messages.append({"role": "user", "content": content})

        # ---- 单工具执行闭包：返回 (observation, failed) ----
        _MUTATING_TOOLS = {"add_scenes_from_script", "add_shots_to_scene",
                           "update_scene", "update_shot", "update_shots",
                           "delete_scene", "delete_shot"}

        def _execute_one_tool(tool: dict):
            nonlocal edited, removed_files, plan_removed, deletion_proposed
            nonlocal deleted_preview, proj
            obs = ""
            failed = False
            tname = tool.get("tool")
            if tname == "add_scenes_from_script":
                res = _execute_add_scenes(rt, project, tool.get("scenes") or [],
                                          tool.get("after_scene_id"),
                                          content, user_msg.message_id)
                edited = True
                added_scenes.extend(res["added"])
                parts = [f"- {a['scene_id']}: {a['summary']}" for a in res["added"]]
                obs = f"已新增 {res['count']} 个故事段：\n" + "\n".join(parts)
                events.append({"kind": "add_scenes", "summary": f"新增 {res['count']} 个故事段"})
            elif tname == "add_shots_to_scene":
                res = _execute_add_shots(rt, project, tool.get("scene_id", ""),
                                         tool.get("shots") or [],
                                         tool.get("after_shot_id"),
                                         content, user_msg.message_id)
                edited = True
                added_shots.extend(res["added"])
                parts = [f"- {a['shot_id']}: {a['description']} ({a['target_duration_s']}s)"
                         for a in res["added"]]
                obs = (f"已把故事段 {res['scene_id']} 拆成 {res['count']} 个镜头：\n"
                       + "\n".join(parts))
                events.append({"kind": "add_shots",
                               "summary": f"故事段 {res['scene_id']} 拆成 {res['count']} 个镜头"})
            elif tname == "update_scene":
                res = _execute_update_scene(
                    rt, project, str(tool.get("scene_id") or ""),
                    {k: tool.get(k) for k in ("summary", "characters") if k in tool},
                    content, user_msg.message_id)
                edited = True
                updated_scenes.append(res)
                obs = f"已更新故事段 {res['scene_id']} 的 {'、'.join(res['changed'])}。"
                events.append({"kind": "update_scene", "summary": f"更新故事段 {res['scene_id']}"})
            elif tname == "update_shot":
                res = _execute_update_shot(
                    rt, project, str(tool.get("shot_id") or ""),
                    {k: v for k, v in tool.items() if k not in ("tool", "shot_id")},
                    content, user_msg.message_id)
                edited = True
                updated_shots.append(res)
                obs = (f"已更新镜头 {res['shot_id']} 的 {'、'.join(res['changed'])}。"
                       + _prompt_invalidation_note(
                           {res['shot_id']: res.get('prompts_invalidated', {})}))
                events.append({"kind": "update_shot", "summary": f"更新镜头 {res['shot_id']}"})
            elif tname == "update_shots":
                res = _execute_update_shots(
                    rt, project, str(tool.get("scene_id") or ""),
                    tool.get("shots") or [], content, user_msg.message_id)
                edited = True
                updated_shots.extend(res["updated_shots"])
                fields = sorted({f for r in res["results"] for f in r["changed"]})
                obs = (f"已为故事段 {res['scene_id']} 的 {res['count']} 个镜头更新方案"
                       + (f"（{'、'.join(fields)}）" if fields else "") + "。"
                       + _prompt_invalidation_note(res.get("prompts_invalidated", {})))
                events.append({"kind": "update_shots", "summary": f"批量更新 {res['count']} 个镜头"})
            elif tname in ("read_shot", "read_scene", "read_script", "list_scenes"):
                try:
                    if tname == "read_shot":
                        obs = _format_shot_read(
                            read_shot(proj, str(tool.get("shot_id") or "")))
                    elif tname == "read_scene":
                        obs = _format_scene_read(
                            read_scene(proj, str(tool.get("scene_id") or "")))
                    elif tname == "read_script":
                        d = read_script(proj)
                        obs = f"剧本正文（{d['length']} 字）：\n\n" + (d["text"] or "（空）")
                    else:
                        d = list_scenes(proj)
                        obs = json.dumps(d, ensure_ascii=False, indent=2)
                except StructureError as e:
                    obs = f"读取失败：{e.message}"
                    failed = True
                events.append({"kind": "read",
                               "summary": f"读取 {tool.get('shot_id') or tool.get('scene_id') or tname}"})
            elif tname in ("delete_scene", "delete_shot"):
                etype = "scene" if tname == "delete_scene" else "shot"
                eid = tool.get("scene_id") if etype == "scene" else tool.get("shot_id")
                confirmed = _as_bool(tool.get("confirmed"), False)
                if confirmed and deletion_proposed:
                    obs = ("删除需要你明确确认。我已说明影响，请等待用户回复"
                           "「确定删除」后再执行，不要自行确认。")
                    failed = True
                else:
                    res = _execute_delete(rt, project, etype, str(eid or ""),
                                         confirmed, content, user_msg.message_id)
                    if not res.get("confirmed", False):
                        deleted_preview = res.get("preview")
                        obs = (deleted_preview or {}).get("text", "无法取得依赖说明。")
                        deletion_proposed = True
                        events.append({"kind": "delete_preview",
                                       "summary": f"待删除 {etype} {eid}（待用户确认）"})
                    else:
                        edited = True
                        deleted = res["deleted"]
                        deleted_scenes.extend(deleted["scenes"] or [])
                        deleted_shots.extend(deleted["shots"] or [])
                        removed_files += res.get("removed_files", 0)
                        plan_removed = plan_removed or res.get("plan_removed", False)
                        obs = res["message"]
                        events.append({"kind": "delete",
                                       "summary": f"删除 {len(deleted['scenes'])} 段 {len(deleted['shots'])} 镜"})
            else:
                obs = f"未知工具：{tname}"
                failed = True
            # 写入/删除成功后，重新从磁盘加载 proj，保证后续只读工具能读到最新数据
            # （各 _execute_* 自己存盘，但闭包外的 proj 是旧快照）
            if not failed and tname in _MUTATING_TOOLS and edited:
                proj = ProjectStore(pdir).load()
            return obs, failed

        # ---- 多步工具循环（agentic loop）----
        # 只读工具可在一个 turn 内连续调用以收集信息；写入/删除工具执行后即结束本轮，
        # 把其结果作为最终回复。纯文本回复（无工具调用）即为最终回答。
        reply = None
        MAX_TOOL_STEPS = 12
        final_text: Optional[str] = None
        # 本轮内若已提出删除建议（confirmed=false），禁止 LLM 自行 confirmed=true，
        # 必须等用户在下一轮明确确认，避免 agent 自我批准破坏性操作。
        deletion_proposed = False
        for step in range(MAX_TOOL_STEPS):
            try:
                reply = await client.chat(messages, temperature=0.5, max_tokens=65536)
            except Exception as e:
                final_text = f"[调用失败: {str(e)[:200]}]"
                status = ResultStatus.FAILED.value
                break
            messages.append({"role": "assistant", "content": reply})

            tools = _parse_tool_calls(reply)
            if not tools:
                final_text = reply   # 纯文本 = 最终回答
                break

            # 一条回复可含多个工具调用（LLM 常用 <tool_call> 包裹或连续多个 JSON），
            # 逐个执行，把所有 observation 汇总后一次性喂回 LLM。
            combined_obs = []
            step_failed = False
            for tool in tools:
                try:
                    obs, failed = _execute_one_tool(tool)
                except AgentError as e:
                    final_text = f"操作失败：{e.message}"
                    status = ResultStatus.FAILED.value
                    step_failed = True
                    break
                if failed:
                    final_text = obs
                    status = ResultStatus.FAILED.value
                    step_failed = True
                    break
                if obs:
                    combined_obs.append(f"[{tool.get('tool')}] {obs}")
            if step_failed:
                break
            if combined_obs:
                messages.append({"role": "user",
                                 "content": "工具返回结果：\n" + "\n\n".join(combined_obs)})
        else:
            final_text = f"（工具调用次数超过上限 {MAX_TOOL_STEPS}，已停止。请把需求拆小一些。）"
            status = ResultStatus.FAILED.value

        assistant_text = final_text or reply

    meta = {"intent": "director_edit" if edited else "conversation",
            "events": events, "evidence_refs": []}
    asst = AgentMessage(role="assistant", content=assistant_text, status=status, meta=meta)
    store.append_message(session_id, asst)

    return {"assistant_message": assistant_text, "edited": edited,
            "added_scenes": added_scenes, "added_shots": added_shots,
            "updated_scenes": updated_scenes, "updated_shots": updated_shots,
            "deleted_scenes": deleted_scenes, "deleted_shots": deleted_shots,
            "deleted_preview": deleted_preview,
            "removed_files": removed_files, "plan_removed": plan_removed,
            "message": asst.to_dict()}
