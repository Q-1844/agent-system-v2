"""角色路由：三视图生成 / 抠图 / 角色库 CRUD / 项目角色绑定（PHASE_5 §5.2 characters 组）。

包含 13 个接口 + helpers（prompt 构建 / 图像处理 / 角色库 IO）+ 请求模型。
_init_default_characters 供 api_server.startup 调用初始化默认角色库。
"""
import io
import json
import uuid
import base64 as _base64
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import deps
from deps import (
    BASE_DIR, logger, get_agnes_client, get_project_manager, _AGNES_PROXY,
    _read_parsed_script, _write_parsed_script,
    _save_base64_image, _load_image_as_base64,
)

router = APIRouter()


# ============ 抠图器（rembg isnet-anime，进程级懒单例）============
_segmentor = None


def _get_segmentor():
    """获取 rembg 抠图器（延迟初始化）"""
    global _segmentor
    if _segmentor is None:
        from src.segmentation.rembg_module import RembgSegmentor
        _segmentor = RembgSegmentor(model_name="isnet-anime")
    return _segmentor


# ============ 请求模型 ============
class SingleViewRequest(BaseModel):
    """单视角生成请求"""
    description: str          # 角色描述（英文）
    view: str = "front"       # front / side / back
    style: str = "anime"      # 风格
    size_tier: str = "1K"
    ratio: str = "2:3"        # 竖图（角色三视图：正面/侧面/背面）
    # P1 修复：传项目名时把该项目 ProjectVisualProfile 风格指令追加进 prompt
    # （消除角色链路与 profile-aware 链路的风格锚不一致）
    project: Optional[str] = None


class SegmentOnlyRequest(BaseModel):
    """单独抠图请求"""
    image_base64: str


class RegenerateViewRequest(BaseModel):
    """带参考图重新生成单视角"""
    description: str
    view: str                    # 要重新生成的视角
    style: str = "anime"
    size_tier: str = "1K"
    ratio: str = "2:3"        # 竖图（角色三视图：正面/侧面/背面）
    ref_images: list[str] = []   # 参考图 base64 列表
    project: Optional[str] = None   # P1 修复：项目风格锚定


class ChainViewRequest(BaseModel):
    """链式三视图生成请求"""
    description: str
    style: str = "anime"
    size_tier: str = "1K"
    ratio: str = "2:3"        # 竖图（角色三视图：正面/侧面/背面）
    project: Optional[str] = None   # P1 修复：项目风格锚定


class SheetViewRequest(BaseModel):
    """设定图三视图生成请求"""
    description: str
    style: str = "anime"
    size_tier: str = "1K"
    ratio: str = "3:2"        # 横图（设定图：三视角并排）
    project: Optional[str] = None   # P1 修复：项目风格锚定


# ============ prompt 构建 ============
def _apply_project_style(prompt: str, project_name: Optional[str]) -> str:
    """P1 修复（2026-09-18）：legacy 角色链路风格锚定——请求带 project 时，
    把该项目 ProjectVisualProfile 的唯一编译点风格指令追加到 prompt
    （否则角色 hero 与 Look/场景等 profile-aware 链路风格锚不一致，
    实测同一角色先写实后国风）。project 未传 / 读取失败 / unspecified → 原样返回。"""
    if not project_name or not prompt:
        return prompt
    try:
        from deps import get_project_manager
        pdir = get_project_manager().get_project_dir(project_name)
        from src.visual_profile import load_profile_or_none
        from src.visual_profile.compiler import append_style_instruction
        return append_style_instruction(prompt, load_profile_or_none(pdir))
    except Exception as e:   # noqa: BLE001
        logger.warning(f"project 风格指令追加失败（原样返回）: {e}")
        return prompt


def _build_view_prompt(description: str, style: str, view: str) -> str:
    """构建单视角 prompt"""
    base = (
        f"{description}, "
        f"{style} illustration, "
        "single small character centered, pure white backdrop, full body, "
        "lots of white space around character, character takes up 40-50% of image, "
        "detailed character design, clean lines, "
        "soft studio lighting, high quality, sharp details"
    )
    views = {
        "front": "facing the camera directly, straight-on front view, symmetrical pose, looking at the viewer, character reference sheet",
        "side": "facing left, perfect side profile view, 90 degree angle, looking to the left, character side portrait, standing straight",
        "back": "back view, from behind, character facing away from camera, showing back to viewer, back of head and body visible, no face shown, full body standing pose",
    }
    return f"{base}, {views.get(view, views['front'])}"


def _build_img2img_prompt(description: str, style: str, view: str) -> str:
    """构建图生图 prompt（用于链式生成）"""
    prompts = {
        "side": (
            f"Rotate this character to show a perfect side profile view, facing left, 90 degree angle. "
            f"Keep the exact same character design: {description}. "
            f"Keep the same {style} art style, clothing, hair color, body proportions. "
            f"Small character centered with lots of white space. "
            f"Pure white backdrop, full body, standing straight, high quality, detailed character design"
        ),
        "back": (
            f"Rotate this character to show the back view, character facing away from camera. "
            f"Keep the exact same character design: {description}. "
            f"Keep the same {style} art style, clothing, hair color, body proportions. "
            f"Small character centered with lots of white space. "
            f"Pure white backdrop, full body, standing pose, back of head and body clearly visible, "
            f"high quality, detailed character design"
        ),
    }
    return prompts.get(view, "")


def _build_sheet_prompt(description: str, style: str) -> str:
    """构建设定图 prompt"""
    return (
        f"Professional character turnaround reference sheet of {description}. "
        f"Three small views arranged horizontally from left to right with lots of white space: "
        f"1) Front view facing the camera, "
        f"2) Right side view showing exact 90-degree profile silhouette, "
        f"3) Back view facing completely away from camera with no face visible. "
        f"Full body, standing in relaxed pose, pure white backdrop, "
        f"even studio lighting, consistent proportions across all views, "
        f"{style} illustration style, clean lines, "
        f"character design sheet format, high quality, detailed"
    )


# ============ 图像处理 ============
def _transparent_to_white_bg_bytes(image_bytes: bytes) -> str:
    """透明PNG转白底，返回base64"""
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    white_bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    white_bg.paste(img, mask=img.split()[3])
    white_bg = white_bg.convert("RGB")
    buf = io.BytesIO()
    white_bg.save(buf, format="PNG")
    return _base64.b64encode(buf.getvalue()).decode("utf-8")


def _crop_three_views(image_bytes: bytes) -> dict:
    """从设定图裁剪三个视角"""
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size
    third = w // 3
    crops = {}
    for i, name in enumerate(["front", "side", "back"]):
        left = i * third
        right = (i + 1) * third if i < 2 else w
        cropped = img.crop((left, 0, right, h))
        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        crops[name] = buf.getvalue()
    return crops


# ============ 三视图生成接口 ============
@router.post("/api/character/generate-view")
async def character_generate_view(req: SingleViewRequest):
    """
    生成单个视角 + 抠图
    返回: raw_base64 (白底原图), seg_base64 (透明抠图), prompt
    """
    agnes = get_agnes_client()

    prompt = _apply_project_style(
        _build_view_prompt(req.description, req.style, req.view), req.project)

    try:
        result = await agnes.generate_image(
            prompt=prompt, size_tier=req.size_tier, ratio=req.ratio
        )
        if not result or not result.get("url"):
            raise HTTPException(500, "图片生成失败")
        url = result["url"]

        # 下载图片
        import httpx
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            img_resp = await client.get(url, timeout=60)
            img_resp.raise_for_status()
            raw_bytes = img_resp.content

        raw_b64 = _base64.b64encode(raw_bytes).decode("utf-8")

        # 抠图
        segmentor = _get_segmentor()
        seg_bytes, seg_meta = segmentor.remove_background(raw_bytes)
        seg_b64 = _base64.b64encode(seg_bytes).decode("utf-8") if seg_bytes else None

        return {
            "success": True,
            "raw_base64": raw_b64,
            "seg_base64": seg_b64,
            "prompt": prompt,
            "metadata": seg_meta,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/character/segment")
async def character_segment(req: SegmentOnlyRequest):
    """单独抠图（不重新生成）"""
    try:
        image_bytes = _base64.b64decode(req.image_base64)
        segmentor = _get_segmentor()
        seg_bytes, seg_meta = segmentor.remove_background(image_bytes)
        if seg_bytes is None:
            raise HTTPException(500, f"抠图失败: {seg_meta.get('error')}")
        return {
            "success": True,
            "seg_base64": _base64.b64encode(seg_bytes).decode("utf-8"),
            "metadata": seg_meta,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/character/regenerate-view")
async def character_regenerate_view(req: RegenerateViewRequest):
    """
    带参考图重新生成单视角
    传入其他视角的图片作为参考，重新生成指定视角
    """
    agnes = get_agnes_client()

    import httpx
    segmentor = _get_segmentor()

    # 构建 prompt
    prompt = _build_img2img_prompt(req.description, req.style, req.view)
    if not prompt:
        prompt = _build_view_prompt(req.description, req.style, req.view)
    prompt = _apply_project_style(prompt, req.project)

    # 构建参考图列表（转成白底）
    image_refs = []
    for ref_b64 in req.ref_images:
        try:
            ref_bytes = _base64.b64decode(ref_b64)
            white_b64 = _transparent_to_white_bg_bytes(ref_bytes)
            image_refs.append(f"data:image/png;base64,{white_b64}")
        except Exception:
            continue

    try:
        result = await agnes.generate_image(
            prompt=prompt, size_tier=req.size_tier, ratio=req.ratio,
            image_refs=image_refs if image_refs else None,
        )
        if not result or not result.get("url"):
            raise HTTPException(500, "图片生成失败")
        url = result["url"]

        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            raw_bytes = (await client.get(url, timeout=60)).content

        raw_b64 = _base64.b64encode(raw_bytes).decode("utf-8")

        # 抠图
        seg_bytes, seg_meta = segmentor.remove_background(raw_bytes)
        seg_b64 = _base64.b64encode(seg_bytes).decode("utf-8") if seg_bytes else None

        return {
            "success": True,
            "raw_base64": raw_b64,
            "seg_base64": seg_b64,
            "prompt": prompt,
            "metadata": seg_meta,
            "ref_count": len(image_refs),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/character/generate-chain")
async def character_generate_chain(req: ChainViewRequest):
    """
    链式三视图生成
    正面(文生图) -> 抠图 -> 侧面(图生图,正面作参考) -> 抠图 -> 背面(图生图,正面作参考) -> 抠图
    """
    agnes = get_agnes_client()

    import httpx
    segmentor = _get_segmentor()
    results = {}
    steps = []

    try:
        # 1. 生成正面
        steps.append({"step": 1, "action": "生成正面", "status": "processing"})
        front_prompt = _apply_project_style(
            _build_view_prompt(req.description, req.style, "front"), req.project)
        front_result = await agnes.generate_image(prompt=front_prompt, size_tier=req.size_tier, ratio=req.ratio)
        front_url = front_result["url"]
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            front_raw = (await client.get(front_url, timeout=60)).content
        results["front_raw"] = _base64.b64encode(front_raw).decode("utf-8")
        steps[-1]["status"] = "done"

        # 2. 正面抠图
        steps.append({"step": 2, "action": "正面抠图", "status": "processing"})
        front_seg, front_meta = segmentor.remove_background(front_raw)
        results["front_seg"] = _base64.b64encode(front_seg).decode("utf-8")
        results["front_meta"] = front_meta
        steps[-1]["status"] = "done"

        # 3. 生成侧面（正面作参考）
        steps.append({"step": 3, "action": "生成侧面(参考正面)", "status": "processing"})
        side_prompt = _apply_project_style(
            _build_img2img_prompt(req.description, req.style, "side"), req.project)
        side_ref = _transparent_to_white_bg_bytes(front_seg)
        side_result = await agnes.generate_image(
            prompt=side_prompt, size_tier=req.size_tier, ratio=req.ratio, image_refs=[f"data:image/png;base64,{side_ref}"]
        )
        side_url = side_result["url"]
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            side_raw = (await client.get(side_url, timeout=60)).content
        results["side_raw"] = _base64.b64encode(side_raw).decode("utf-8")
        steps[-1]["status"] = "done"

        # 4. 侧面抠图
        steps.append({"step": 4, "action": "侧面抠图", "status": "processing"})
        side_seg, side_meta = segmentor.remove_background(side_raw)
        results["side_seg"] = _base64.b64encode(side_seg).decode("utf-8")
        results["side_meta"] = side_meta
        steps[-1]["status"] = "done"

        # 5. 生成背面（正面作参考）
        steps.append({"step": 5, "action": "生成背面(参考正面)", "status": "processing"})
        back_prompt = _apply_project_style(
            _build_img2img_prompt(req.description, req.style, "back"), req.project)
        back_ref = _transparent_to_white_bg_bytes(front_seg)
        back_result = await agnes.generate_image(
            prompt=back_prompt, size_tier=req.size_tier, ratio=req.ratio, image_refs=[f"data:image/png;base64,{back_ref}"]
        )
        back_url = back_result["url"]
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            back_raw = (await client.get(back_url, timeout=60)).content
        results["back_raw"] = _base64.b64encode(back_raw).decode("utf-8")
        steps[-1]["status"] = "done"

        # 6. 背面抠图
        steps.append({"step": 6, "action": "背面抠图", "status": "processing"})
        back_seg, back_meta = segmentor.remove_background(back_raw)
        results["back_seg"] = _base64.b64encode(back_seg).decode("utf-8")
        results["back_meta"] = back_meta
        steps[-1]["status"] = "done"

        return {
            "success": True,
            "raw_views": {
                "front": results["front_raw"],
                "side": results["side_raw"],
                "back": results["back_raw"],
            },
            "seg_views": {
                "front": results["front_seg"],
                "side": results["side_seg"],
                "back": results["back_seg"],
            },
            "metadata": {
                "front": results.get("front_meta", {}),
                "side": results.get("side_meta", {}),
                "back": results.get("back_meta", {}),
            },
            "steps": steps,
        }
    except Exception as e:
        steps[-1]["status"] = "failed"
        steps[-1]["error"] = str(e)
        return {
            "success": False,
            "error": str(e),
            "steps": steps,
            "raw_views": {k.replace("_raw", ""): v for k, v in results.items() if k.endswith("_raw")},
            "seg_views": {k.replace("_seg", ""): v for k, v in results.items() if k.endswith("_seg")},
        }


@router.post("/api/character/generate-sheet")
async def character_generate_sheet(req: SheetViewRequest):
    """
    设定图三视图生成
    一张图出三视角 -> 裁剪 -> 分别抠图
    """
    agnes = get_agnes_client()

    import httpx
    segmentor = _get_segmentor()
    results = {}
    steps = []

    try:
        # 1. 生成设定图
        steps.append({"step": 1, "action": "生成设定图", "status": "processing"})
        prompt = _apply_project_style(
            _build_sheet_prompt(req.description, req.style), req.project)
        sheet_result = await agnes.generate_image(prompt=prompt, size_tier=req.size_tier, ratio=req.ratio)
        sheet_url = sheet_result["url"]
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            sheet_raw = (await client.get(sheet_url, timeout=60)).content
        results["sheet_raw"] = _base64.b64encode(sheet_raw).decode("utf-8")
        steps[-1]["status"] = "done"

        # 2. 裁剪三视角
        steps.append({"step": 2, "action": "裁剪三视角", "status": "processing"})
        crops = _crop_three_views(sheet_raw)
        results["crops_raw"] = {k: _base64.b64encode(v).decode("utf-8") for k, v in crops.items()}
        steps[-1]["status"] = "done"

        # 3-5. 分别抠图
        for i, view_name in enumerate(["front", "side", "back"]):
            steps.append({"step": i + 3, "action": f"{view_name} 抠图", "status": "processing"})
            seg_bytes, seg_meta = segmentor.remove_background(crops[view_name])
            if seg_bytes:
                results[f"{view_name}_seg"] = _base64.b64encode(seg_bytes).decode("utf-8")
                results[f"{view_name}_meta"] = seg_meta
                steps[-1]["status"] = "done"
            else:
                steps[-1]["status"] = "failed"
                steps[-1]["error"] = seg_meta.get("error", "抠图失败")

        return {
            "success": True,
            "sheet_raw": results["sheet_raw"],
            "crops_raw": results.get("crops_raw", {}),
            "seg_views": {
                "front": results.get("front_seg"),
                "side": results.get("side_seg"),
                "back": results.get("back_seg"),
            },
            "metadata": {
                "front": results.get("front_meta", {}),
                "side": results.get("side_meta", {}),
                "back": results.get("back_meta", {}),
            },
            "steps": steps,
            "prompt": prompt,
        }
    except Exception as e:
        steps[-1]["status"] = "failed"
        steps[-1]["error"] = str(e)
        return {
            "success": False,
            "error": str(e),
            "steps": steps,
            "sheet_raw": results.get("sheet_raw"),
            "crops_raw": results.get("crops_raw", {}),
        }


# ============ 角色保存系统 ============
# 角色保存目录
SAVED_CHARACTERS_DIR = BASE_DIR / "saved_characters"
SAVED_CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)


class SaveCharacterRequest(BaseModel):
    """保存角色请求"""
    name: str                    # 角色名称
    description: str             # 角色描述（主提示词）
    views: dict                  # 三视图数据 { front: { raw?, seg? }, side: {...}, back: {...} }


class UpdateCharacterRequest(BaseModel):
    """更新角色信息请求"""
    name: Optional[str] = None
    description: Optional[str] = None


def _get_character_dir(char_id: str) -> Path:
    """获取角色保存目录"""
    return SAVED_CHARACTERS_DIR / char_id


def _load_character_meta(char_dir: Path) -> dict:
    """加载角色元数据"""
    meta_file = char_dir / "meta.json"
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_character_meta(char_dir: Path, meta: dict):
    """保存角色元数据"""
    meta_file = char_dir / "meta.json"
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _init_default_characters():
    """初始化默认角色到角色库（如果角色库为空）。api_server.startup 调用。"""
    try:
        # 检查角色库是否为空
        existing = list(SAVED_CHARACTERS_DIR.iterdir())
        if any(d.is_dir() for d in existing):
            logger.info("角色库已有数据，跳过初始化")
            return

        # 默认角色
        default_chars = [
            {
                "name": "小星",
                "description": "a young girl with silver hair, blue eyes, wearing a white cloak, gentle expression",
            },
            {
                "name": "暗影",
                "description": "a tall figure in black armor, glowing red eyes, menacing presence",
            },
        ]

        for char in default_chars:
            char_id = f"{char['name']}_{uuid.uuid4().hex[:8]}"
            char_dir = _get_character_dir(char_id)
            char_dir.mkdir(parents=True, exist_ok=True)

            meta = {
                "id": char_id,
                "name": char["name"],
                "description": char["description"],
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            _save_character_meta(char_dir, meta)
            logger.info(f"已添加默认角色: {char['name']}")

    except Exception as e:
        logger.error(f"初始化默认角色失败: {e}")


@router.post("/api/character/save")
async def save_character(req: SaveCharacterRequest):
    """
    保存角色到本地
    返回: character_id
    """
    try:
        # 生成唯一ID
        char_id = f"{req.name}_{uuid.uuid4().hex[:8]}"
        char_dir = _get_character_dir(char_id)
        char_dir.mkdir(parents=True, exist_ok=True)

        # 保存元数据
        meta = {
            "id": char_id,
            "name": req.name,
            "description": req.description,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        _save_character_meta(char_dir, meta)

        # 保存三视图图片
        for view_name in ["front", "side", "back"]:
            view_data = req.views.get(view_name, {})
            if view_data.get("raw"):
                _save_base64_image(view_data["raw"], char_dir / f"{view_name}_raw.png")
            if view_data.get("seg"):
                _save_base64_image(view_data["seg"], char_dir / f"{view_name}_seg.png")

        logger.info(f"角色已保存: {char_id}")
        return {
            "success": True,
            "character_id": char_id,
            "message": f"角色 '{req.name}' 已保存",
        }
    except Exception as e:
        logger.error(f"保存角色失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")


@router.get("/api/character/list")
async def list_saved_characters():
    """获取已保存的角色列表"""
    try:
        characters = []
        if SAVED_CHARACTERS_DIR.exists():
            for char_dir in SAVED_CHARACTERS_DIR.iterdir():
                if char_dir.is_dir():
                    meta = _load_character_meta(char_dir)
                    if meta:
                        # 检查是否有三视图
                        has_views = {}
                        for view_name in ["front", "side", "back"]:
                            has_views[view_name] = {
                                "raw": (char_dir / f"{view_name}_raw.png").exists(),
                                "seg": (char_dir / f"{view_name}_seg.png").exists(),
                            }
                        meta["has_views"] = has_views
                        characters.append(meta)

        # 按创建时间倒序排列
        characters.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return {"characters": characters}
    except Exception as e:
        logger.error(f"获取角色列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/character/load/{character_id}")
async def load_character(character_id: str):
    """加载已保存的角色（包含三视图数据）"""
    try:
        char_dir = _get_character_dir(character_id)
        if not char_dir.exists():
            raise HTTPException(status_code=404, detail="角色不存在")

        # 加载元数据
        meta = _load_character_meta(char_dir)

        # 加载三视图
        views = {}
        for view_name in ["front", "side", "back"]:
            raw_b64 = _load_image_as_base64(char_dir / f"{view_name}_raw.png")
            seg_b64 = _load_image_as_base64(char_dir / f"{view_name}_seg.png")
            if raw_b64 or seg_b64:
                views[view_name] = {}
                if raw_b64:
                    views[view_name]["raw"] = raw_b64
                if seg_b64:
                    views[view_name]["seg"] = seg_b64

        return {
            "success": True,
            "character": {
                "id": meta.get("id"),
                "name": meta.get("name"),
                "description": meta.get("description"),
                "created_at": meta.get("created_at"),
                "views": views,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"加载角色失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/character/{character_id}")
async def update_character(character_id: str, req: UpdateCharacterRequest):
    """更新角色信息（名称、描述）"""
    try:
        char_dir = _get_character_dir(character_id)
        if not char_dir.exists():
            raise HTTPException(status_code=404, detail="角色不存在")

        meta = _load_character_meta(char_dir)
        if req.name is not None:
            meta["name"] = req.name
        if req.description is not None:
            meta["description"] = req.description
        meta["updated_at"] = datetime.now().isoformat()

        _save_character_meta(char_dir, meta)

        return {"success": True, "message": "角色信息已更新"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新角色失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/character/{character_id}")
async def delete_character(character_id: str):
    """删除已保存的角色"""
    try:
        char_dir = _get_character_dir(character_id)
        if not char_dir.exists():
            raise HTTPException(status_code=404, detail="角色不存在")

        # 删除所有文件
        import shutil
        shutil.rmtree(char_dir)

        logger.info(f"角色已删除: {character_id}")
        return {"success": True, "message": "角色已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除角色失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/character/{character_id}/views")
async def update_character_views(character_id: str, views: dict):
    """更新角色的三视图数据"""
    try:
        char_dir = _get_character_dir(character_id)
        if not char_dir.exists():
            raise HTTPException(status_code=404, detail="角色不存在")

        # 更新三视图
        for view_name in ["front", "side", "back"]:
            view_data = views.get(view_name, {})
            if view_data.get("raw"):
                _save_base64_image(view_data["raw"], char_dir / f"{view_name}_raw.png")
            if view_data.get("seg"):
                _save_base64_image(view_data["seg"], char_dir / f"{view_name}_seg.png")

        # 更新修改时间
        meta = _load_character_meta(char_dir)
        meta["updated_at"] = datetime.now().isoformat()
        _save_character_meta(char_dir, meta)

        return {"success": True, "message": "三视图已更新"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新三视图失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============ 项目角色绑定（角色页生成结果落盘到项目）============
# 修复数据断裂：角色页生成的三视图原本只在内存/角色库，分镜阶段拿不到；
# 绑定后落盘到项目 output/characters/ 并写入 parsed_script.json 的 reference_images。

class BindCharacterRequest(BaseModel):
    """绑定角色到项目请求"""
    name: str
    description: str = ""
    views: dict  # {front:{raw?,seg?}, side:{...}, back:{...}}


class UpdateProjectCharacterRequest(BaseModel):
    """更新项目角色请求"""
    description: Optional[str] = None
    new_name: Optional[str] = None


@router.post("/api/project/{project_name}/character/bind")
async def bind_character_to_project(project_name: str, req: BindCharacterRequest):
    """
    把角色页生成的三视图绑定到项目：
    - 落盘 raw/seg 到 output/characters/{name}_{view}.png
    - 写入 parsed_script.json 的 reference_images（raw 路径，供分镜生成引用）
    - 角色不存在则追加，存在则更新
    """
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        char_dir = project_dir / "output" / "characters"
        char_dir.mkdir(parents=True, exist_ok=True)

        from src.core.store import ProjectStore
        from src.core.models import Character, Asset, ShotStatus
        import time
        store = ProjectStore(project_dir)
        p = store.load()
        c = p.get_character(req.name)
        if c is None:
            c = Character(name=req.name, description_en=req.description or "")
            p.characters.append(c)
        else:
            if req.description:
                c.description_en = req.description

        ref_images = {}
        for view in ["front", "side", "back"]:
            vdata = req.views.get(view, {}) or {}
            if vdata.get("raw"):
                raw_path = char_dir / f"{req.name}_{view}.png"
                _save_base64_image(vdata["raw"], raw_path)
                ref_images[view] = str(raw_path)
                # 合并：只更新本次提供的视角，其余保留
                if view not in c.views:
                    c.views[view] = Asset()
                c.views[view].local = str(raw_path)
                c.views[view].status = ShotStatus.DONE.value
                c.views[view].error = None
                c.views[view].updated_at = time.time()
            if vdata.get("seg"):
                seg_path = char_dir / f"{req.name}_{view}_seg.png"
                _save_base64_image(vdata["seg"], seg_path)

        store.save(p)
        logger.info(f"角色 {req.name} 已保存到项目 {project_name}: {list(ref_images.keys())}")
        return {
            "success": True,
            "reference_images": ref_images,
            "message": f"角色 {req.name} 已保存到项目 {project_name}",
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"绑定角色失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/project/{project_name}/character/{character_name}")
async def update_project_character(
    project_name: str, character_name: str, req: UpdateProjectCharacterRequest
):
    """更新项目角色的描述/名称（改名时同步重命名文件、更新场景角色名）"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        store = ProjectStore(project_dir)
        p = store.load()
        c = p.get_character(character_name)
        if c is None:
            raise HTTPException(
                status_code=404,
                detail=f"角色 {character_name} 不存在于项目 {project_name}",
            )

        renamed = False
        if req.new_name and req.new_name.strip() and req.new_name.strip() != character_name:
            new_name = req.new_name.strip()
            char_dir = project_dir / "output" / "characters"
            # 重命名 raw + seg 文件，更新 views.local
            for view in list(c.views.keys()):
                old_raw = char_dir / f"{character_name}_{view}.png"
                new_raw = char_dir / f"{new_name}_{view}.png"
                if old_raw.exists():
                    old_raw.rename(new_raw)
                    c.views[view].local = str(new_raw)
                old_seg = char_dir / f"{character_name}_{view}_seg.png"
                new_seg = char_dir / f"{new_name}_{view}_seg.png"
                if old_seg.exists():
                    old_seg.rename(new_seg)
            c.name = new_name
            # 更新 shots 里的角色名引用
            for sc in p.scenes:
                for sh in sc.shots:
                    sh.characters = [new_name if cn == character_name else cn for cn in sh.characters]
            renamed = True

        if req.description is not None:
            c.description_en = req.description

        store.save(p)
        if renamed:
            # 角色 AI 设计草稿 key 随改名迁移（Phase AI-2 §25）
            from routers.character_design import migrate_design_key
            migrate_design_key(project_dir, character_name, new_name)
        logger.info(f"项目角色 {character_name} 已更新 (renamed={renamed})")
        return {"success": True, "renamed": renamed, "message": "角色已更新"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新项目角色失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/project/{project_name}/characters/full")
async def get_project_characters_full(project_name: str):
    """获取项目角色（含 reference_images 路径，供角色页/分镜页使用）"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        p = ProjectStore(project_dir).load()
        out = []
        for c in p.characters:
            views_b64 = {}
            for view_name, asset in c.views.items():
                raw_b64 = None
                seg_b64 = None
                if asset.local:
                    raw_path = Path(str(asset.local))
                    if not raw_path.is_absolute():
                        raw_path = BASE_DIR / raw_path
                    if raw_path.exists():
                        raw_b64 = _load_image_as_base64(raw_path)
                    seg_path = raw_path.with_name(raw_path.stem + "_seg.png")
                    if seg_path.exists():
                        seg_b64 = _load_image_as_base64(seg_path)
                views_b64[view_name] = {"raw": raw_b64, "seg": seg_b64}
            out.append({"name": c.name, "description_en": c.description_en, "views": views_b64})
        return {"characters": out}
    except FileNotFoundError:
        return {"characters": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/project/{project_name}/character/{character_name}")
async def delete_project_character(project_name: str, character_name: str):
    """从当前项目删除角色（v2 project.json）+ 清理其三视图文件。不影响角色库。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        store = ProjectStore(project_dir)
        p = store.load()
        c = p.get_character(character_name)
        if c is None:
            raise HTTPException(status_code=404, detail=f"角色 {character_name} 不存在")
        # 清理三视图文件
        char_dir = project_dir / "output" / "characters"
        for view in c.views:
            for suffix in [f"_{view}.png", f"_{view}_seg.png"]:
                f = char_dir / f"{character_name}{suffix}"
                if f.exists():
                    try: f.unlink()
                    except Exception: pass
        p.characters = [ch for ch in p.characters if ch.name != character_name]
        store.save(p)
        # 清理角色 AI 设计草稿（Phase AI-2 §26）
        from routers.character_design import delete_design
        delete_design(project_dir, character_name)
        logger.info(f"角色 {character_name} 已从项目 {project_name} 删除")
        return {"success": True, "message": "角色已删除"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除角色失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
