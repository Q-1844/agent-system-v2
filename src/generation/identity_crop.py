# src/generation/identity_crop.py
# Identity Crop（31.md Phase C4 / G4 基建）——adapter 级派生人形参考。
#
# 背景（D2 §5.2 实证）：紧景别发全身白底立绘 → 构图被锚定在「全身+环境」（CU 渲染
# WS/MS）；人形参考全退（drop）→ identity 只剩 prompt 文本。crop 档 = 第三条路：
# 从 approved look hero 资产**确定性派生**头肩裁剪参考——identity 信号保留（脸部/
# 发型/上装），全身构图锚解除。auto 默认已定版（Phase 42，2026-09-30）：景别驱动的
# 紧景别（CU 家族）auto→crop——P3 三臂对照（相对 drop 构图不变身份变好）+ EXP2/3
# 用户逐图评审（跨镜最稳配方）；角度驱动 context_only（OTS/insert/detail）维持
# drop（无实证，例外有档）。见 image_adapter.resolve_human_reference_mode。
#
# 纪律：
#   - adapter 级派生物：输入 = approved ReferenceAsset 字节；输出 = data URI +
#     derivation provenance。**绝不写回 Resources / 任何 canonical**（31.md §17）。
#   - 确定性：同输入字节 + 同方法 → 同裁剪框 → 同输出（无随机/无时钟；进程内缓存
#     按 (method, sha256) 键）。
#   - 失败诚实降级：rembg/PIL 失败或找不到主体 → 返回 None，调用方回落源参考媒体
#     并记 diagnostic（绝不 silent fallback，31.md §18）。
from __future__ import annotations

import base64
import hashlib
import io
from typing import Any, Dict, Optional, Tuple

# 裁剪档位（相对主体 bbox 的确定性比例，非像素魔法数）：
#   bust = 头+肩+上胸（高度取主体上部 45%，横向全宽外扩 20%，头顶上留 8%）
#   face = 面部特写（高度取主体上部 22%，横向外扩 10%，头顶上留 5%）
CROP_METHODS = ("bust", "face")
_METHOD_PARAMS = {
    "bust": {"height_frac": 0.45, "pad_x": 0.20, "pad_top": 0.08},
    "face": {"height_frac": 0.22, "pad_x": 0.10, "pad_top": 0.05},
}
_DERIVATION_VERSION = "identity_crop_v1"

_cache: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
_CACHE_MAX = 64

_segmentor = None


def _get_segmentor():
    """进程内懒加载单例（复用 src.segmentation 的 isnet-anime 惯例）。"""
    global _segmentor
    if _segmentor is None:
        from src.segmentation.rembg_module import RembgSegmentor
        _segmentor = RembgSegmentor("isnet-anime")
    return _segmentor


def _subject_bbox(img_rgba, mass_percentile: float = 0.98):
    """alpha 质量分位 bbox（Photoshop-trim 口径）→ 主体 bbox（无主体/退化 → None）。
    🔴 不用 alpha.getbbox()——它对散点噪声过敏：场景风 look plate 的半透明雾/光晕
    像素散布全画幅 → bbox=全画幅 → 裁剪反而搬运环境（Phase F 真机实证：
    s16_c02 crop 变体渲染出参考图里的雪山夕阳背景）。改为取包含 central
    `mass_percentile` alpha 质量（alpha>16 计数）的行/列窗口——确定性、无随机。"""
    try:
        import numpy as np
    except ImportError:            # numpy 随 rembg 必在；防御性兜底 getbbox
        alpha = img_rgba.split()[-1]
        bbox = alpha.getbbox()
        if bbox is None:
            return None
        l, t, r, b = bbox
        if r - l < 4 or b - t < 4:
            return None
        return (l, t, r, b)
    a = np.asarray(img_rgba.split()[-1], dtype=np.uint8)
    mask = (a > 64).astype(np.uint8)     # 阈值 64：排除半透明雾/光晕散点（真机实证）
    total = int(mask.sum())
    if total < 64:
        return None
    lo_frac, hi_frac = (1.0 - mass_percentile) / 2.0, (1.0 + mass_percentile) / 2.0

    def _window(axis_mass):
        cum = np.cumsum(axis_mass)
        n = len(axis_mass)
        lo = int(np.searchsorted(cum, total * lo_frac))
        hi = int(np.searchsorted(cum, total * hi_frac))
        return max(0, min(lo, n - 1)), max(1, min(hi, n))

    l, r = _window(mask.sum(axis=0))
    t, b = _window(mask.sum(axis=1))
    if r - l < 4 or b - t < 4:
        return None
    return (int(l), int(t), int(r), int(b))


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def derive_identity_crop(image_bytes: bytes, *, method: str = "bust"
                         ) -> Optional[Dict[str, Any]]:
    """参考图字节 → 头肩/面部裁剪派生。返回 {media: data URI, derivation: {...}}；
    不可派生 → None（调用方诚实降级）。确定性：结果按 (method, sha256) 缓存。"""
    if method not in CROP_METHODS:
        raise ValueError(f"identity crop method 非法: {method!r}（允许 {'/'.join(CROP_METHODS)}）")
    key = (method, hashlib.sha256(image_bytes).hexdigest())
    if key in _cache:
        return _cache[key]
    result = _derive(image_bytes, method)
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = result
    return result


def _derive(image_bytes: bytes, method: str) -> Optional[Dict[str, Any]]:
    try:
        from PIL import Image

        seg = _get_segmentor()
        if not seg.is_available():
            return None
        matte_bytes, meta = seg.remove_background(image_bytes)
        if not matte_bytes or not meta.get("success"):
            return None
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        matte = Image.open(io.BytesIO(matte_bytes)).convert("RGBA")
        if matte.size != img.size:
            return None
        bbox = _subject_bbox(matte)
        if bbox is None:
            return None
        l, t, r, b = bbox
        params = _METHOD_PARAMS[method]
        subj_w, subj_h = r - l, b - t
        # 裁剪窗：横向=主体全宽外扩 pad_x；纵向=自主体顶（再上留 pad_top）向下
        # 取主体高度 height_frac。
        cx0 = _clamp(int(l - subj_w * params["pad_x"]), 0, img.width)
        cx1 = _clamp(int(r + subj_w * params["pad_x"]), 0, img.width)
        cy0 = _clamp(int(t - subj_h * params["pad_top"]), 0, img.height)
        cy1 = _clamp(int(t + subj_h * params["height_frac"]), 0, img.height)
        if cx1 - cx0 < 8 or cy1 - cy0 < 8:
            return None
        crop = img.crop((cx0, cy0, cx1, cy1)).convert("RGB")
        out = io.BytesIO()
        crop.save(out, format="PNG")
        media = "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
        return {
            "media": media,
            "derivation": {
                "version": _DERIVATION_VERSION,
                "method": method,
                "bbox_method": "alpha_mass_98",
                "crop_box": [cx0, cy0, cx1, cy1],
                "subject_bbox": [l, t, r, b],
                "source_size": [img.width, img.height],
                "source_sha256": hashlib.sha256(image_bytes).hexdigest(),
                "segmentation_model": seg.model_name,
            },
        }
    except Exception:   # noqa: BLE001 —— 派生是增强不是门；任何失败都诚实降级
        return None


def resolve_identity_crop_media(project_dir, *, url: Optional[str],
                                local: Optional[str], method: str = "bust"
                                ) -> Optional[Dict[str, Any]]:
    """ReferenceAsset 媒体 → 裁剪派生。取字节顺序与 to_agnes_image_ref 同序
    （local 可读优先——内容可验证；否则公网 URL 下载，Agnes 资产 URL 公开读且
    不过期）；都不可用或派生失败 → None。"""
    data: Optional[bytes] = None
    if local:
        try:
            from src.resources.generation import load_image_bytes
            data = load_image_bytes(project_dir, local)
        except Exception:   # noqa: BLE001
            data = None
    if data is None and url:
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
        except Exception:   # noqa: BLE001
            return None
    if not data:
        return None
    return derive_identity_crop(data, method=method)
