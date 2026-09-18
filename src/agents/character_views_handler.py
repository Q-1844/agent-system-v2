# src/agents/character_views_handler.py
# AI-7C §44/§45/§49：character_views JobQueue job handler —— 复用既有 chain 生成业务逻辑
# （routers/characters.py 的 prompt builder + agnes.generate_image + rembg 分割），不另造 generator。
#
# §45：用**已批准** identity（Character.description_en）再生当前 reference views（front/side/back），
# 不重新设计角色。§44：front text2img -> side img2img(from front) -> back img2img(from side)。
# §46：durable via JobQueue（origin_run_id + stable stage key）。
#
# AI-7C-H K1：done views 不再无条件跳过。区分两种语义：
#   - resume 同一次生成（job.params.appearance_sig == 当前 views_meta 记录的 sig）
#     -> done views 可安全跳过（重启 re-run 不重复生成）；
#   - 新批准的外观修订（appearance_sig 不同，或 manifest action=regenerate/generate_new）
#     -> 旧 done views 是陈旧输入，**必须真实重新生成**。
# 外观签名 appearance_sig = sha256(description_en + art_style)：description_en 是批准后的
# 创作身份（§45），风格参与图像生成，二者共同决定 views 的语义版本。
# 历史资产保留：新视图落盘为带 sig 前缀的版本化文件（{view}_{sig8}_seg.png），旧文件不删除。
#
# 不在全局 JOB_HANDLERS 注册（无副作用）——由 composition root / 驱动按需注册。
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

from ..core.store import ProjectStore
from ..core.models import Asset
from ..core.write_lock import project_write_lock


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name) or "char"


def character_appearance_sig(description_en: str, art_style: str,
                             visual_profile_fingerprint: str = "") -> str:
    """K1：角色外观语义签名。description_en（批准后创作身份，§45）+ art_style（legacy
    风格短语）+ visual_profile_fingerprint（Agent System V2 Phase B §3.2：ProjectVisualProfile
    是全项目风格真相，参与每张 view 生成的 prompt 后缀，其指纹必须参与签名——profile
    变更 ⇒ 旧 views 是陈旧输入，必须重生成）共同决定三视图的语义版本。确定性、0 LLM。"""
    h = hashlib.sha256()
    h.update((description_en or "").strip().encode("utf-8"))
    h.update(b"\x00")
    h.update((art_style or "").strip().encode("utf-8"))
    h.update(b"\x00")
    h.update((visual_profile_fingerprint or "").strip().encode("utf-8"))
    return h.hexdigest()


async def _download(url: str, proxy=None) -> bytes:
    import httpx
    async with httpx.AsyncClient(proxy=proxy, timeout=60) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content


def _char_dir(project_dir, char_name: str) -> Path:
    return Path(project_dir) / "output" / "characters" / _safe_filename(char_name)


def _save_seg_png(project_dir, char_name: str, view: str, seg_bytes: bytes,
                  sig: str = "") -> str:
    """落盘分割图。带 sig 时用版本化文件名（{view}_{sig8}_seg.png）——同 sig 重生成覆盖
    同名（幂等），不同 sig 写新文件，旧外观文件保留（K1 历史资产不删除）。"""
    d = _char_dir(project_dir, char_name)
    d.mkdir(parents=True, exist_ok=True)
    name = f"{view}_{sig[:8]}_seg.png" if sig else f"{view}_seg.png"
    p = d / name
    p.write_bytes(seg_bytes)
    return str(p)


def _read_views_meta(project_dir, char_name: str) -> dict:
    """读当前 Character.views 的语义版本记录（K1）。"""
    p = _char_dir(project_dir, char_name) / "views_meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_views_meta(project_dir, char_name: str, sig: str, views: dict) -> None:
    """记录 views 对应的 appearance_sig（与 Character.views 同临界区写回）。"""
    d = _char_dir(project_dir, char_name)
    d.mkdir(parents=True, exist_ok=True)
    meta = {"appearance_sig": sig,
            "views": {k: {"local": (v.local if hasattr(v, "local") else v.get("local")),
                          "url": (v.url if hasattr(v, "url") else v.get("url")),
                          "status": "done"}
                      for k, v in (views or {}).items()},
            "updated_at": __import__("time").time()}
    (d / "views_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


async def handler_character_views(job, emit):
    """character_views job handler：再生角色三视图，写回 Character.views（§44 chain）。
    job.params: character_name（必）；origin_run_id/autopilot_stage_key（幂等用）；
    appearance_sig（K1：本次生成目标的外观签名，由 _real_character_view_generator 从
    提交后的 canonical 计算）。

    K1 跳过规则（appearance_sig 提供时）：
      sig == views_meta.appearance_sig            -> done views 跳过（resume 同一生成）；
      sig != views_meta.appearance_sig 或无 meta   -> 全部重新生成（新批准外观修订）。
    appearance_sig 缺失（旧调用方/无外观语义）-> 保持旧行为：done views 跳过。"""
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
    # L2/§44：角色三视图用竖图 2:3，与既有 routers.characters chain 一致（非视频画幅 16:9）。
    ratio = "2:3"
    agnes = get_agnes_client()
    segmentor = _get_segmentor()
    proxy = getattr(agnes, "_proxy", None) or getattr(agnes, "proxy", None)

    # ---- Phase B §3.2：Agent 路径与正式 HTTP Character generation 同一风格真相 ----
    # HTTP 链路 _apply_project_style（routers/characters.py）对 chain 全部 prompt 追加
    # ProjectVisualProfile 的唯一编译点风格指令（append_style_instruction）；profile
    # 未设置/读取失败 → 原样返回（宽容降级，与 HTTP 行为一致，不再另造第三种行为）。
    from src.visual_profile import load_profile_or_none
    from src.visual_profile.compiler import append_style_instruction
    try:
        visual_profile = load_profile_or_none(project_dir)
    except Exception:
        visual_profile = None   # noqa: BLE001 - 与 _apply_project_style 的宽容语义一致

    def _styled(prompt: str) -> str:
        return append_style_instruction(prompt, visual_profile)

    # ---- K1：外观语义版本判定 ----
    target_sig = (job.params.get("appearance_sig") or "").strip()
    meta_sig = (_read_views_meta(project_dir, char_name).get("appearance_sig") or "")
    _done = {v for v, a in (char.views or {}).items()
             if getattr(a, "status", None) == "done"}
    if not target_sig:
        # legacy（无外观语义的旧调用方）：保持既有 done-skip 行为，不触发重生成
        done_views = _done
    elif target_sig == meta_sig:
        # resume 同一次生成（sig 一致）：done views 可安全跳过
        done_views = _done
    else:
        # 新批准外观修订（sig 不同/无 meta 记录）：旧 done views 是陈旧输入，全部重新生成
        done_views = set()

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
        local = _save_seg_png(project_dir, char_name, view, seg, sig=target_sig) if seg else None
        return Asset(url=url, local=local, status="done")

    views: dict = {}
    front = await _gen("front", _styled(_build_view_prompt(char.description_en, style, "front")),
                       None, 15)
    views["front"] = front
    front_url = front.url
    side = await _gen("side", _styled(_build_img2img_prompt(char.description_en, style, "side")),
                      refs=[front_url], pct=45)
    views["side"] = side
    back = await _gen("back", _styled(_build_img2img_prompt(char.description_en, style, "back")),
                     refs=[side.url], pct=75)
    views["back"] = back

    # 写回 Character.views（M3/§33：project_write_lock 短临界区，防并发写丢更新——
    # handler 与 commit_canonical / 其它 char sync 串行；reload latest + 仅改该 char 的 views，保无关态）
    async with project_write_lock(job.project):
        p = store.load()
        c = next((x for x in p.characters if x.name == char_name), None)
        if c is None:
            raise RuntimeError(f"角色 {char_name} 不存在（写回时）")
        c.views = views
        store.save(p)
        # K1：同临界区记录 views 的语义版本（appearance_sig），供重启/重生成判定
        if target_sig:
            _write_views_meta(project_dir, char_name, target_sig, views)
    await emit("done", f"{char_name} 三视图完成", 100)
