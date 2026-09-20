# tests/test_identity_crop.py
# 31.md Phase C4/F — identity crop（G4 基建 + bbox 修复）验收。
#
# 测试策略（Phase F 教训）：isnet-anime 对合成平面图形的分割在运行间不稳定
# （matte 可塌缩为近空），pytest 内一律**不依赖真实 rembg 推理**：
#   - _subject_bbox：纯 numpy 确定性单测（含散点噪声回归——真机实证 getbbox 过敏）；
#   - derive 管线：monkeypatch 假 segmentor（已知 matte，确定性裁剪算术/derivation/
#     缓存/失败路径）；
#   - 真实 rembg 能力由真机 E2E（_31_e2e_driver + Golden 资产）验证，不在 pytest 内。
import base64
import io
import sys
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.generation.image_adapter import build_bound_first_frame_input
from src.generation.identity_crop import (
    CROP_METHODS,
    _subject_bbox,
    derive_identity_crop,
)
from src.resources.store import ResourceStore
from src.shot_binding import ShotBindingService

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
from test_shot_binding_s3b1 import LOOK, make_pdir, run  # noqa: E402
from test_shot_binding_s3b2 import _build_scene  # noqa: E402
from test_shot_binding_s3c1 import (  # noqa: E402
    FakeImageClient,
    _bind_default,
    _load_shot,
    _StubPM,
)

import deps  # noqa: E402

URL = "https://example.com/a.png"


def _make_portrait_png(w=384, h=576) -> bytes:
    """合成「白底 + 人形色块」源图（头圆 + 躯干矩形）。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.ellipse([w // 2 - 60, 40, w // 2 + 60, 160], fill=(120, 90, 70))     # 头
    d.rectangle([w // 2 - 90, 170, w // 2 + 90, 480], fill=(40, 60, 120))  # 躯干
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _make_known_matte(w=384, h=576) -> bytes:
    """确定性假 matte：主体=居中 100×300 实心块（alpha 255），无噪声。"""
    from PIL import Image
    rgba = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    px = rgba.load()
    for x in range(142, 242):
        for y in range(138, 438):
            px[x, y] = (10, 10, 10, 255)
    out = io.BytesIO()
    rgba.save(out, format="PNG")
    return out.getvalue()


class FakeSegmentor:
    """确定性 segmentor：恒返回已知 matte。"""
    model_name = "fake-seg"

    def __init__(self, matte: bytes):
        self._matte = matte

    def is_available(self):
        return True

    def remove_background(self, image_bytes, **kw):
        return self._matte, {"success": True}


@pytest.fixture()
def fake_seg(monkeypatch):
    import src.generation.identity_crop as ic
    seg = FakeSegmentor(_make_known_matte())
    monkeypatch.setattr(ic, "_get_segmentor", lambda: seg)
    monkeypatch.setattr(ic, "_cache", {})      # 隔离进程内缓存
    return seg


# ============ _subject_bbox（纯 numpy，确定性）============

def test_subject_bbox_tight_on_solid_subject():
    from PIL import Image
    rgba = Image.new("RGBA", (384, 576), (255, 255, 255, 0))
    px = rgba.load()
    for x in range(142, 242):
        for y in range(138, 438):
            px[x, y] = (10, 10, 10, 255)
    bbox = _subject_bbox(rgba)
    assert bbox is not None
    l, t, r, b = bbox
    assert 135 <= l <= 150 and 230 <= r <= 250, f"水平窗口贴主体，实际 {bbox}"
    assert 130 <= t <= 145 and 430 <= b <= 445, f"垂直窗口贴主体，实际 {bbox}"


def test_subject_bbox_ignores_scattered_haze():
    """Phase F 回归：getbbox 对散点噪声过敏（场景风 plate 雾/光晕散点 → bbox=全画幅
    → 裁剪搬运环境——s16_c02 crop 变体真机实证）。质量分位窗口 + 64 阈值必须贴主体。"""
    from PIL import Image
    w, h = 400, 600
    rgba = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    px = rgba.load()
    for x in range(150, 250):          # 实心主体（居中 100×300）
        for y in range(100, 400):
            px[x, y] = (10, 10, 10, 255)
    for x in range(0, w, 13):          # 散布全画幅的低 alpha 噪声（雾/光晕）
        for y in range(0, h, 11):
            if px[x, y][3] == 0:
                px[x, y] = (200, 200, 200, 30)
    bbox = _subject_bbox(rgba)
    assert bbox is not None
    l, t, r, b = bbox
    assert l >= 130 and r <= 270, f"水平窗口必须贴住主体，实际 {bbox}"
    assert t >= 80 and b <= 420, f"垂直窗口必须贴住主体，实际 {bbox}"
    # 全零 alpha → None（无主体诚实返回）
    blank = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    assert _subject_bbox(blank) is None


# ============ derive 管线（假 segmentor，确定性）============

def test_derive_deterministic_and_derivation_complete(fake_seg):
    png = _make_portrait_png()
    r1 = derive_identity_crop(png, method="bust")
    r2 = derive_identity_crop(png, method="bust")
    assert r1 is not None
    assert r1 == r2, "同输入字节同输出（确定性，含缓存）"
    d = r1["derivation"]
    assert d["method"] == "bust"
    assert d["version"] == "identity_crop_v1"
    assert d["bbox_method"] == "alpha_mass_98"
    assert len(d["crop_box"]) == 4 and len(d["subject_bbox"]) == 4
    assert d["source_size"] == [384, 576]
    assert d["segmentation_model"] == "fake-seg"
    assert r1["media"].startswith("data:image/png;base64,")
    # 裁剪输出应小于源图（bust = 主体上部 45% + 外扩）
    from PIL import Image
    img = Image.open(io.BytesIO(base64.b64decode(r1["media"].split(",", 1)[1])))
    assert img.size[1] < 576


def test_derive_face_method_and_invalid_bytes_fail_honest(fake_seg):
    png = _make_portrait_png()
    r = derive_identity_crop(png, method="face")
    assert r is not None and r["derivation"]["method"] == "face"
    assert derive_identity_crop(b"not-a-png", method="bust") is None, \
        "非法字节诚实返回 None（不抛不猜）"
    with pytest.raises(ValueError):
        derive_identity_crop(png, method="torso")


def test_derive_segmentor_failure_returns_none(monkeypatch):
    import src.generation.identity_crop as ic

    class BrokenSeg:
        model_name = "broken"

        def is_available(self):
            return True

        def remove_background(self, image_bytes, **kw):
            return None, {"success": False, "error": "boom"}

    monkeypatch.setattr(ic, "_get_segmentor", lambda: BrokenSeg())
    monkeypatch.setattr(ic, "_cache", {})
    assert derive_identity_crop(_make_portrait_png(), method="bust") is None


# ============ 预算集成（假 segmentor）============

@pytest.fixture()
def cenv(tmp_path, monkeypatch, fake_seg):
    _clear_for_test()
    pdir = make_pdir(tmp_path)
    (pdir / "output" / "scene_system.json").unlink()
    ids = run(_build_scene(pdir))
    svc = ShotBindingService(pdir)
    fake = FakeImageClient()
    monkeypatch.setattr(deps, "project_manager", _StubPM(pdir))
    monkeypatch.setattr(deps, "agnes_client", fake)
    yield pdir, ids, svc, fake
    _clear_for_test()


def _point_hero_to_local(pdir, png_path: Path):
    rd = ResourceStore(pdir, pdir.name).load()
    rd.reference_assets["ref_alice000001"].local = str(png_path)
    rd.reference_assets["ref_alice000001"].url = None
    ResourceStore(pdir, pdir.name).save(rd)


def test_crop_mode_replaces_look_media_with_derivation(cenv, tmp_path):
    pdir, ids, svc, _fake = cenv
    _bind_default(svc, ids)
    png_path = tmp_path / "alice_hero.png"
    png_path.write_bytes(_make_portrait_png())
    _point_hero_to_local(pdir, png_path)

    bound = build_bound_first_frame_input(
        pdir, ProjectStore(pdir).load(), _load_shot(pdir),
        human_reference_mode="crop")
    look_roles = [r for r in bound.semantic_roles
                  if r["source_type"] == "character_look"]
    assert look_roles, "crop 档必须保留每角色 1 张（裁剪后的）look 参考"
    assert len(look_roles) == 1
    role = look_roles[0]
    assert role["derivation"] and role["derivation"]["method"] in ("bust", "face")
    media = bound.image_refs[bound.semantic_roles.index(role)]
    assert media.startswith("data:image/png;base64,")
    # 场景参考不受 crop 影响（本镜 WS → auto scene=full → 场景图原样）
    scene_media = [m for m in bound.image_refs if m != media]
    assert scene_media, "非 crop 项媒体原样保留"
    assert all(not r.get("derivation") for r in bound.semantic_roles
               if r["source_type"] != "character_look")


def test_keep_mode_source_media_untouched(cenv, tmp_path):
    pdir, ids, svc, _fake = cenv
    _bind_default(svc, ids)
    png_path = tmp_path / "alice_hero.png"
    png_path.write_bytes(_make_portrait_png())
    _point_hero_to_local(pdir, png_path)

    bound = build_bound_first_frame_input(
        pdir, ProjectStore(pdir).load(), _load_shot(pdir),
        human_reference_mode="keep")
    for r in bound.semantic_roles:
        assert r.get("derivation") is None, "keep 档不派生"


def test_crop_failure_falls_back_with_diagnostic(cenv, tmp_path, monkeypatch):
    """派生失败 → 回落源媒体 + identity_crop_derivation_failed diagnostic（不 silent）。"""
    import src.generation.identity_crop as ic
    pdir, ids, svc, _fake = cenv
    _bind_default(svc, ids)
    png_path = tmp_path / "alice_hero.png"
    png_path.write_bytes(_make_portrait_png())
    _point_hero_to_local(pdir, png_path)
    monkeypatch.setattr(ic, "derive_identity_crop", lambda *a, **k: None)

    bound = build_bound_first_frame_input(
        pdir, ProjectStore(pdir).load(), _load_shot(pdir),
        human_reference_mode="crop")
    reasons = [d.get("reason") for d in bound.diagnostics]
    assert "identity_crop_derivation_failed" in reasons
    look_roles = [r for r in bound.semantic_roles if r["source_type"] == "character_look"]
    assert look_roles and look_roles[0].get("derivation") is None
    # 回落源媒体（local → data URI 全图），参考数不变
    assert len(bound.image_refs) == len(bound.semantic_roles)
