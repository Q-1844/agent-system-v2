"""
人物分割测试服务器
提供三个API端点，分别测试三种背景移除方法
启动: python test_segmentation_server.py
访问: http://localhost:8765
"""

import io
import os
import sys
import json
import asyncio
import base64
import httpx
from pathlib import Path
from typing import Optional

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

# ============================================================
# 自动加载 .env 文件
# ============================================================
def load_dotenv(env_path: str = None):
    """从 .env 文件加载环境变量"""
    if env_path is None:
        env_path = Path(__file__).parent / ".env"
    env_file = Path(env_path)
    if not env_file.exists():
        return
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # 只在环境变量未设置时才加载
                if key and not os.environ.get(key):
                    os.environ[key] = value

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="人物分割测试")

# ============================================================
# 配置 - 自动从环境变量读取 API Key
# ============================================================
AGNES_BASE_URL = "https://apihub.agnes-ai.cn"
AGNES_IMAGE_MODEL = "agnes-image-2.1-flash"
AGNES_API_KEY = os.environ.get("AGNES_API_KEY", "")
REMOVEBG_API_KEY = os.environ.get("REMOVEBG_API_KEY", "")

# 角色描述（可自定义）
CHARACTER_PRESETS = {
    "anime_girl": {
        "name": "anime girl",
        "description": (
            "a young anime girl with long flowing silver hair, "
            "bright blue eyes, wearing a white and blue fantasy dress "
            "with gold trim, white knee-high boots, a small silver tiara, "
            "slender build, graceful posture"
        ),
        "style": "anime",
    },
    "warrior": {
        "name": "warrior",
        "description": (
            "a tall muscular male warrior with short dark brown hair, "
            "brown eyes, wearing heavy plate armor with a red cape, "
            "carrying a large sword on his back, battle scars on his face, "
            "strong jawline, determined expression"
        ),
        "style": "anime",
    },
    "mage": {
        "name": "mage",
        "description": (
            "a mysterious female mage with long curly purple hair, "
            "glowing green eyes, wearing a dark purple robe with silver runes, "
            "holding a crystal staff, pointed hat, "
            "slender figure, ethereal aura"
        ),
        "style": "anime",
    },
}

# ============================================================
# 视角 Prompt 构建（基于 Agnes 官方最佳实践）
# ============================================================

def build_character_prompt(preset: dict, view: str) -> str:
    """
    构建角色视角提示词
    遵循 Agnes 文档推荐结构: [主体] + [场景] + [风格] + [光照] + [构图] + [质量]
    """
    desc = preset["description"]
    style = preset["style"]

    # 公共前缀 - 角色描述 + 风格 + 白底
    base = (
        f"{desc}, "
        f"{style} illustration, "
        "single character, pure white background, full body, "
        "detailed character design, clean lines, "
        "soft studio lighting, high quality, sharp details"
    )

    # 视角关键词
    view_keywords = {
        "front": (
            "facing the camera directly, straight-on front view, "
            "symmetrical pose, looking at the viewer, "
            "character reference sheet, T-pose or relaxed standing pose"
        ),
        "side": (
            "facing left, perfect side profile view, 90 degree angle, "
            "looking to the left, character side portrait, "
            "standing straight, full body visible"
        ),
        "back": (
            "back view, from behind, character facing away from camera, "
            "showing back to viewer, back of head and body visible, "
            "no face shown, full body standing pose, "
            "back of clothing and hair clearly visible"
        ),
    }

    return f"{base}, {view_keywords.get(view, view_keywords['front'])}"


# ============================================================
# Agnes API 调用
# ============================================================

async def generate_character_image(api_key: str, prompt: str, size: str = "1024x1024") -> Optional[bytes]:
    """调用 Agnes API 文生图（无参考图）"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = {
        "model": AGNES_IMAGE_MODEL,
        "prompt": prompt,
        "size": size,
        "extra_body": {"response_format": "url"},
    }

    async with httpx.AsyncClient() as client:
        for attempt in range(3):
            try:
                resp = await client.post(
                    f"{AGNES_BASE_URL}/v1/images/generations",
                    headers=headers,
                    json=data,
                    timeout=120,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    url = result["data"][0].get("url")
                    if url:
                        img_resp = await client.get(url, timeout=60)
                        img_resp.raise_for_status()
                        return img_resp.content
                elif resp.status_code == 429:
                    await asyncio.sleep(2 ** attempt * 3)
                    continue
                else:
                    raise Exception(f"API错误: {resp.status_code} - {resp.text}")
            except httpx.TimeoutException:
                if attempt == 2:
                    raise
                await asyncio.sleep(2)
    return None


async def generate_character_image_with_ref(
    api_key: str,
    prompt: str,
    ref_image_base64: str,
    size: str = "1024x1024",
) -> Optional[bytes]:
    """
    调用 Agnes API 图生图（有参考图）
    用参考图保持角色一致性，用 prompt 控制视角
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = {
        "model": AGNES_IMAGE_MODEL,
        "prompt": prompt,
        "size": size,
        "extra_body": {
            "response_format": "url",
            "image": [f"data:image/png;base64,{ref_image_base64}"],
        },
    }

    async with httpx.AsyncClient() as client:
        for attempt in range(3):
            try:
                resp = await client.post(
                    f"{AGNES_BASE_URL}/v1/images/generations",
                    headers=headers,
                    json=data,
                    timeout=120,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    url = result["data"][0].get("url")
                    if url:
                        img_resp = await client.get(url, timeout=60)
                        img_resp.raise_for_status()
                        return img_resp.content
                elif resp.status_code == 429:
                    await asyncio.sleep(2 ** attempt * 3)
                    continue
                else:
                    raise Exception(f"API错误: {resp.status_code} - {resp.text}")
            except httpx.TimeoutException:
                if attempt == 2:
                    raise
                await asyncio.sleep(2)
    return None


def segment_image_bytes(image_bytes: bytes, method: str = "rembg") -> tuple:
    """
    对图片执行抠图

    Returns:
        (output_bytes, metadata) 或 (None, metadata)
    """
    segs = ensure_segmentors()
    if method not in segs:
        return None, {"error": f"方法 {method} 不可用", "success": False}
    return segs[method].remove_background(image_bytes)


def build_img2img_prompt(preset: dict, view: str) -> str:
    """
    构建图生图的 prompt
    重点: 告诉 AI 要"旋转/改变视角"，同时保持角色外观
    """
    desc = preset["description"]
    style = preset["style"]

    view_instructions = {
        "side": (
            f"Rotate this character to show a perfect side profile view, "
            f"facing left, 90 degree angle. "
            f"Keep the exact same character design: {desc}. "
            f"Keep the same {style} art style, clothing, hair color, body proportions. "
            f"White background, full body, standing straight, "
            f"high quality, detailed character design"
        ),
        "back": (
            f"Rotate this character to show the back view, "
            f"character facing away from camera. "
            f"Keep the exact same character design: {desc}. "
            f"Keep the same {style} art style, clothing, hair color, body proportions. "
            f"White background, full body, standing pose, "
            f"back of head and body clearly visible, "
            f"high quality, detailed character design"
        ),
    }
    return view_instructions.get(view, "")


# ============================================================
# 分割方法初始化
# ============================================================

def get_segmentors():
    """获取三种分割器（延迟初始化）"""
    segmentors = {}

    # 方法一: rembg
    try:
        from src.segmentation.rembg_module import RembgSegmentor
        seg = RembgSegmentor(model_name="isnet-anime")
        if seg.is_available():
            segmentors["rembg"] = seg
    except Exception as e:
        print(f"[WARN] rembg 不可用: {e}")

    # 方法二: transparent-background
    try:
        from src.segmentation.transparent_bg_module import TransparentBgSegmentor
        seg = TransparentBgSegmentor(source="rgba")
        if seg.is_available():
            segmentors["transparent_bg"] = seg
    except Exception as e:
        print(f"[WARN] transparent-background 不可用: {e}")

    # 方法三: remove.bg
    try:
        from src.segmentation.removebg_api_module import RemoveBgApiSegmentor
        seg = RemoveBgApiSegmentor()
        if seg.is_available():
            segmentors["removebg"] = seg
    except Exception as e:
        print(f"[WARN] remove.bg 不可用: {e}")

    return segmentors


# 全局分割器实例（延迟初始化）
_segmentors = None


def ensure_segmentors():
    global _segmentors
    if _segmentors is None:
        _segmentors = get_segmentors()
    return _segmentors


# ============================================================
# API 端点
# ============================================================

class GenerateRequest(BaseModel):
    api_key: str
    preset: str = "anime_girl"
    view: str = "front"
    size: str = "1024x1024"
    custom_description: Optional[str] = None


class SegmentRequest(BaseModel):
    image_base64: str  # base64 编码的图片
    method: str        # rembg / transparent_bg / removebg
    model: Optional[str] = None  # rembg 模型名


@app.get("/", response_class=HTMLResponse)
async def index():
    """返回测试页面"""
    html_path = Path(__file__).parent / "test_segmentation.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>测试页面未找到</h1>")


@app.get("/api/status")
async def get_status():
    """获取各方法可用状态"""
    segs = ensure_segmentors()

    # 返回 masked API key（前端可确认已配置）
    def mask_key(key: str) -> str:
        if not key:
            return ""
        if len(key) <= 8:
            return key[:2] + "***"
        return key[:4] + "***" + key[-4:]

    return {
        "rembg": "rembg" in segs,
        "transparent_bg": "transparent_bg" in segs,
        "removebg": "removebg" in segs,
        "presets": list(CHARACTER_PRESETS.keys()),
        "rembg_models": ["u2net", "u2net_human_seg", "isnet-general-use", "isnet-anime", "silueta"],
        # 自动返回已配置的 API Key（masked）
        "agnes_api_key": mask_key(AGNES_API_KEY),
        "removebg_api_key": mask_key(REMOVEBG_API_KEY),
        "agnes_key_full": AGNES_API_KEY,       # 用于前端自动填入
        "removebg_key_full": REMOVEBG_API_KEY, # 用于前端自动填入
    }


@app.post("/api/generate")
async def generate_image(req: GenerateRequest):
    """生成角色图片"""
    if req.preset not in CHARACTER_PRESETS and not req.custom_description:
        raise HTTPException(400, f"未知预设: {req.preset}")

    preset = CHARACTER_PRESETS.get(req.preset, {
        "name": "custom",
        "description": req.custom_description,
        "style": "anime",
    })

    prompt = build_character_prompt(preset, req.view)

    try:
        image_bytes = await generate_character_image(req.api_key, prompt, req.size)
        if not image_bytes:
            raise HTTPException(500, "图片生成失败")

        b64 = base64.b64encode(image_bytes).decode("utf-8")
        return {
            "success": True,
            "image_base64": b64,
            "prompt": prompt,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/segment")
async def segment_image(req: SegmentRequest):
    """执行背景移除"""
    segs = ensure_segmentors()

    if req.method not in segs:
        available = list(segs.keys())
        raise HTTPException(
            400,
            f"方法 '{req.method}' 不可用。可用: {available}",
        )

    segmentor = segs[req.method]

    try:
        image_bytes = base64.b64decode(req.image_base64)
    except Exception:
        raise HTTPException(400, "无效的 base64 图片数据")

    # 如果指定了 rembg 模型，重新创建实例
    if req.method == "rembg" and req.model:
        from src.segmentation.rembg_module import RembgSegmentor
        segmentor = RembgSegmentor(model_name=req.model)
        if not segmentor.is_available():
            raise HTTPException(400, f"rembg 模型 '{req.model}' 不可用")

    output_bytes, metadata = segmentor.remove_background(image_bytes)

    if output_bytes is None:
        raise HTTPException(500, f"处理失败: {metadata.get('error', '未知错误')}")

    b64 = base64.b64encode(output_bytes).decode("utf-8")
    return {
        "success": True,
        "image_base64": b64,
        "metadata": metadata,
    }


# ============================================================
# 三视图链式生成接口
# ============================================================

class ThreeViewRequest(BaseModel):
    api_key: str
    preset: str = "anime_girl"
    size: str = "1024x1024"
    segment_method: str = "rembg"
    custom_description: Optional[str] = None


@app.post("/api/generate-3view")
async def generate_three_views(req: ThreeViewRequest):
    """
    三视图链式生成
    流程: 生成正面 → 抠图 → 用正面当参考生成侧面 → 抠图 → 用正面当参考生成背面 → 抠图
    """
    if req.preset not in CHARACTER_PRESETS and not req.custom_description:
        raise HTTPException(400, f"未知预设: {req.preset}")

    preset = CHARACTER_PRESETS.get(req.preset, {
        "name": "custom",
        "description": req.custom_description,
        "style": "anime",
    })

    # 检查抠图方法是否可用
    segs = ensure_segmentors()
    if req.segment_method not in segs:
        raise HTTPException(400, f"抠图方法 '{req.segment_method}' 不可用")

    results = {}  # 存储每一步的结果
    steps_log = []  # 记录每一步的日志

    try:
        # ========== 步骤1: 生成正面（文生图） ==========
        steps_log.append({"step": 1, "action": "生成正面", "status": "processing"})
        front_prompt = build_character_prompt(preset, "front")
        front_raw = await generate_character_image(req.api_key, front_prompt, req.size)
        if not front_raw:
            raise Exception("正面图生成失败")
        results["front_raw"] = base64.b64encode(front_raw).decode("utf-8")
        steps_log[-1]["status"] = "done"
        steps_log[-1]["prompt"] = front_prompt

        # ========== 步骤2: 正面抠图 ==========
        steps_log.append({"step": 2, "action": "正面抠图", "status": "processing"})
        front_seg, front_meta = segment_image_bytes(front_raw, req.segment_method)
        if front_seg is None:
            raise Exception(f"正面抠图失败: {front_meta.get('error')}")
        results["front"] = base64.b64encode(front_seg).decode("utf-8")
        results["front_meta"] = front_meta
        steps_log[-1]["status"] = "done"

        # ========== 步骤3: 用正面当参考，生成侧面（图生图） ==========
        steps_log.append({"step": 3, "action": "生成侧面(参考正面)", "status": "processing"})
        side_prompt = build_img2img_prompt(preset, "side")
        # 用抠图后的透明PNG当参考（转成白底版本给AI看）
        side_ref = _transparent_to_white_bg(front_seg)
        side_raw = await generate_character_image_with_ref(
            req.api_key, side_prompt, side_ref, req.size
        )
        if not side_raw:
            raise Exception("侧面图生成失败")
        results["side_raw"] = base64.b64encode(side_raw).decode("utf-8")
        steps_log[-1]["status"] = "done"
        steps_log[-1]["prompt"] = side_prompt

        # ========== 步骤4: 侧面抠图 ==========
        steps_log.append({"step": 4, "action": "侧面抠图", "status": "processing"})
        side_seg, side_meta = segment_image_bytes(side_raw, req.segment_method)
        if side_seg is None:
            raise Exception(f"侧面抠图失败: {side_meta.get('error')}")
        results["side"] = base64.b64encode(side_seg).decode("utf-8")
        results["side_meta"] = side_meta
        steps_log[-1]["status"] = "done"

        # ========== 步骤5: 用正面当参考，生成背面（图生图） ==========
        steps_log.append({"step": 5, "action": "生成背面(参考正面)", "status": "processing"})
        back_prompt = build_img2img_prompt(preset, "back")
        back_ref = _transparent_to_white_bg(front_seg)
        back_raw = await generate_character_image_with_ref(
            req.api_key, back_prompt, back_ref, req.size
        )
        if not back_raw:
            raise Exception("背面图生成失败")
        results["back_raw"] = base64.b64encode(back_raw).decode("utf-8")
        steps_log[-1]["status"] = "done"
        steps_log[-1]["prompt"] = back_prompt

        # ========== 步骤6: 背面抠图 ==========
        steps_log.append({"step": 6, "action": "背面抠图", "status": "processing"})
        back_seg, back_meta = segment_image_bytes(back_raw, req.segment_method)
        if back_seg is None:
            raise Exception(f"背面抠图失败: {back_meta.get('error')}")
        results["back"] = base64.b64encode(back_seg).decode("utf-8")
        results["back_meta"] = back_meta
        steps_log[-1]["status"] = "done"

        return {
            "success": True,
            "views": {
                "front": results["front"],
                "side": results["side"],
                "back": results["back"],
            },
            "raw_views": {
                "front": results["front_raw"],
                "side": results["side_raw"],
                "back": results["back_raw"],
            },
            "metadata": {
                "front": results.get("front_meta", {}),
                "side": results.get("side_meta", {}),
                "back": results.get("back_meta", {}),
            },
            "steps": steps_log,
        }

    except Exception as e:
        steps_log[-1]["status"] = "failed"
        steps_log[-1]["error"] = str(e)
        return {
            "success": False,
            "error": str(e),
            "steps": steps_log,
            # 返回已生成的部分结果
            "views": {k: v for k, v in results.items() if k in ("front", "side", "back")},
            "raw_views": {k: v for k, v in results.items() if k.endswith("_raw")},
        }


def _transparent_to_white_bg(image_bytes: bytes) -> str:
    """
    将透明PNG转成白底PNG，返回base64
    Agnes图生图接受 base64 输入
    """
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    white_bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    white_bg.paste(img, mask=img.split()[3])
    white_bg = white_bg.convert("RGB")
    buf = io.BytesIO()
    white_bg.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ============================================================
# 单张设定图生成 + 自动裁剪
# ============================================================

class SheetRequest(BaseModel):
    api_key: str
    preset: str = "anime_girl"
    size: str = "1792x1024"
    segment_method: str = "rembg"
    custom_description: Optional[str] = None


@app.get("/sheet", response_class=HTMLResponse)
async def sheet_page():
    """返回设定图测试页面"""
    html_path = Path(__file__).parent / "test_sheet.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>测试页面未找到</h1>")


def build_sheet_prompt(preset: dict) -> str:
    """
    构建单张设定图的 prompt
    在一张图里生成正面/侧面/背面三个视角
    """
    desc = preset["description"]
    style = preset["style"]

    return (
        f"Professional character turnaround reference sheet of {desc}. "
        f"Three views arranged horizontally from left to right: "
        f"1) Front view facing the camera, "
        f"2) Right side view showing exact 90-degree profile silhouette, "
        f"3) Back view facing completely away from camera with no face visible. "
        f"Full body, standing in relaxed pose, pure white background, "
        f"even studio lighting, consistent proportions across all views, "
        f"{style} illustration style, clean lines, "
        f"character design sheet format, high quality, detailed"
    )


def crop_three_views(image_bytes: bytes) -> dict:
    """
    从设定图中裁剪出三个视角
    假设三个视角水平排列，等宽分割

    Returns:
        {"front": bytes, "side": bytes, "back": bytes}
    """
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size

    # 三等分裁剪
    third = w // 3
    crops = {}
    names = ["front", "side", "back"]

    for i, name in enumerate(names):
        left = i * third
        right = (i + 1) * third if i < 2 else w  # 最后一段吃掉余量
        cropped = img.crop((left, 0, right, h))

        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        crops[name] = buf.getvalue()

    return crops


@app.post("/api/generate-sheet")
async def generate_character_sheet(req: SheetRequest):
    """
    单张设定图生成流程:
    1. 生成一张包含三视图的设定图
    2. 裁剪出三个视角
    3. 分别抠图
    """
    if req.preset not in CHARACTER_PRESETS and not req.custom_description:
        raise HTTPException(400, f"未知预设: {req.preset}")

    preset = CHARACTER_PRESETS.get(req.preset, {
        "name": "custom",
        "description": req.custom_description,
        "style": "anime",
    })

    segs = ensure_segmentors()
    if req.segment_method not in segs:
        raise HTTPException(400, f"抠图方法 '{req.segment_method}' 不可用")

    results = {}
    steps_log = []

    try:
        # 步骤1: 生成设定图
        steps_log.append({"step": 1, "action": "生成设定图", "status": "processing"})
        prompt = build_sheet_prompt(preset)
        sheet_raw = await generate_character_image(req.api_key, prompt, req.size)
        if not sheet_raw:
            raise Exception("设定图生成失败")
        results["sheet_raw"] = base64.b64encode(sheet_raw).decode("utf-8")
        steps_log[-1]["status"] = "done"
        steps_log[-1]["prompt"] = prompt

        # 步骤2: 裁剪三个视角
        steps_log.append({"step": 2, "action": "裁剪三个视角", "status": "processing"})
        crops = crop_three_views(sheet_raw)
        results["crops_raw"] = {k: base64.b64encode(v).decode("utf-8") for k, v in crops.items()}
        steps_log[-1]["status"] = "done"

        # 步骤3-5: 分别抠图
        for i, view_name in enumerate(["front", "side", "back"]):
            steps_log.append({"step": i + 3, "action": f"{view_name} 抠图", "status": "processing"})
            seg_bytes, seg_meta = segment_image_bytes(crops[view_name], req.segment_method)
            if seg_bytes is None:
                steps_log[-1]["status"] = "failed"
                steps_log[-1]["error"] = seg_meta.get("error", "未知错误")
                continue
            results[f"{view_name}_seg"] = base64.b64encode(seg_bytes).decode("utf-8")
            results[f"{view_name}_meta"] = seg_meta
            steps_log[-1]["status"] = "done"

        return {
            "success": True,
            "sheet_raw": results["sheet_raw"],
            "crops_raw": results.get("crops_raw", {}),
            "views": {
                "front": results.get("front_seg"),
                "side": results.get("side_seg"),
                "back": results.get("back_seg"),
            },
            "metadata": {
                "front": results.get("front_meta", {}),
                "side": results.get("side_meta", {}),
                "back": results.get("back_meta", {}),
            },
            "steps": steps_log,
            "prompt": prompt,
        }

    except Exception as e:
        steps_log[-1]["status"] = "failed"
        steps_log[-1]["error"] = str(e)
        return {
            "success": False,
            "error": str(e),
            "steps": steps_log,
            "sheet_raw": results.get("sheet_raw"),
            "crops_raw": results.get("crops_raw", {}),
            "views": {k: v for k, v in results.items() if k.endswith("_seg")},
        }


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("人物分割测试服务器")
    print("主页: http://localhost:8765")
    print("设定图测试: http://localhost:8765/sheet")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8765)


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("人物分割测试服务器")
    print("访问: http://localhost:8765")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8765)
