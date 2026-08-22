# src/agents/character_views_handler.py
# AI-7C §44/§45/§49：character_views JobQueue job handler —— 复用既有 chain 生成业务逻辑
# （routers/characters.py 的 prompt builder + agnes.generate_image + rembg 分割），不另造 generator。
#
# §45：用**已批准** identity（Character.description_en）再生当前 reference views（front/side/back），
# 不重新设计角色。§44：front text2img -> side img2img(from front) -> back img2img(from side)。
# §46：durable via JobQueue（origin_run_id + stable stage key）；本 handler 通过 Character.views
# 已落盘状态做幂等（重启 re-run 跳过已 done view）——coarse v1，够 §103 acceptance。
#
# 不在全局 JOB_HANDLERS 注册（无副作用）——由 composition root / 驱动按需注册。
from __future__ import annotations
import base64 as _b64
import re

from ..core.store import ProjectStore
from ..core.models import Asset


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name) or "char"


async def _download(url: str, proxy=None) -> bytes:
    import httpx
    async with httpx.AsyncClient(proxy=proxy, timeout=60) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content


def _save_seg_png(project_dir, char_name: str, view: str, seg_bytes: bytes) -> str:
    d = project_dir / "output" / "characters" / _safe_filename(char_name)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{view}_seg.png"
    p.write_bytes(seg_bytes)
    return str(p)


async def handler_character_views(job, emit):
    """character_views job handler：再生角色三视图，写回 Character.views（§44 chain）。
    job.params: character_name（必）；origin_run_id/autopilot_stage_key（幂等用）。
    幂等：Character.views[view].status=='done' 视为已生成（coarse §46）——重启 re-run 跳过。"""
    from deps import get_agnes_client, get_project_manager
    # 复用既有 prompt builder + segmentor（routers.characters 模块级函数）
    from routers.characters import _build_view_prompt, _build_img2img_prompt, _get_segmentor
    project_dir = get_project_manager().get_project_dir(job.project)
    store = ProjectStore(project_dir)
    project = store.load()
    char_name = job.params.get("character_name") or ""
    char = next((c for c in project.characters if c.name == char_name), None)
    if char is None:
        raise RuntimeError(f"角色 {char_name} 不存在")
    style = project.style.art_style or "anime"
    size_tier = project.style.size_tier or "1K"
    ratio = project.style.aspect_ratio or "16:9"
    agnes = get_agnes_client()
    segmentor = _get_segmentor()
    proxy = getattr(agnes, "_proxy", None) or getattr(agnes, "proxy", None)

    # 已 done 的 view 跳过（coarse §46 幂等）
    done_views = {v for v, a in (char.views or {}).items()
                  if getattr(a, "status", None) == "done"}

    async def _gen(view: str, prompt: str, refs=None, pct: int = 0) -> Asset:
        if view in done_views:
            await emit(view, f"{char_name} {view} 已存在，跳过", pct)
            return char.views[view]
        await emit(view, f"{char_name} {view} 生成中", pct)
        res = await agnes.generate_image(prompt=prompt, size_tier=size_tier, ratio=ratio,
                                         image_refs=refs)
        url = (res or {}).get("url")
        if not url:
            raise RuntimeError(f"{view} 生成失败：无 url")
        raw = await _download(url, proxy=proxy)
        seg, _meta = segmentor.remove_background(raw)
        local = _save_seg_png(project_dir, char_name, view, seg) if seg else None
        return Asset(url=url, local=local, status="done")

    views: dict = {}
    front = await _gen("front", _build_view_prompt(char.description_en, style, "front"), None, 15)
    views["front"] = front
    front_url = front.url
    side = await _gen("side", _build_img2img_prompt(char.description_en, style, "side"),
                      refs=[front_url], pct=45)
    views["side"] = side
    back = await _gen("back", _build_img2img_prompt(char.description_en, style, "back"),
                     refs=[side.url], pct=75)
    views["back"] = back

    # 写回 Character.views（reload latest + 仅改该 char 的 views，保无关态）
    p = store.load()
    c = next((x for x in p.characters if x.name == char_name), None)
    if c is None:
        raise RuntimeError(f"角色 {char_name} 不存在（写回时）")
    c.views = views
    store.save(p)
    await emit("done", f"{char_name} 三视图完成", 100)
