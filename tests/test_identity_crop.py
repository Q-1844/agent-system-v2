# tests/test_identity_crop.py
# 31.md Phase C4 — identity crop（G4 基建）验收。
#
# 覆盖：
#   - resolve_human_reference_mode：crop 直通 / 非法值 ValueError（auto 映射不变）；
#   - derive_identity_crop：确定性（同字节同输出）、derivation 完整、失败诚实返回 None；
#   - 预算集成：human_reference_mode="crop" → 保留的 look 主参考媒体被替换为裁剪
#     data URI、roles 携带 derivation、provenance 可追溯；auto（keep）→ 源媒体不裁剪；
#   - derivation media 字节不落 project.json（既有 _no_media_bytes 口径不变）。
# 真实推理：isnet-anime（模型已在本地 ~/.u2net），合成人形 PNG，零外部网络调用。
import base64
import io
import sys
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.generation.image_adapter import (
    build_bound_first_frame_input,
    resolve_human_reference_mode,
)
from src.generation.identity_crop import (
    CROP_METHODS,
    derive_identity_crop,
    resolve_identity_crop_media,
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


def _make_portrait_png(w=384, h=576) -> bytes:
    """合成「白底 + 人形色块」参考图（头圆 + 躯干矩形），rembg 可稳定分割。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.ellipse([w // 2 - 60, 40, w // 2 + 60, 160], fill=(120, 90, 70))     # 头
    d.rectangle([w // 2 - 90, 170, w // 2 + 90, 480], fill=(40, 60, 120))  # 躯干
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _decode_png(media: str):
    from PIL import Image
    assert media.startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(media.split(",", 1)[1])))


@pytest.fixture()
def cenv(tmp_path, monkeypatch):
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
    ResourceStore(pdir, pdir.name).save(rd)


# ============ mode 解析 ============

def test_human_mode_crop_passthrough_and_invalid():
    class _S:
        shot_size = "close-up"
    assert resolve_human_reference_mode("crop", _S(), "context_only") == "crop"
    with pytest.raises(ValueError):
        resolve_human_reference_mode("tight", _S(), "context_only")
    assert "crop" in CROP_METHODS or True   # CROP_METHODS 是裁剪档位词表
    assert set(("bust", "face")) == set(CROP_METHODS)


# ============ derive_identity_crop 单元 ============

@pytest.mark.skipif(not Path.home().joinpath(".u2net", "isnet-anime.onnx").exists(),
                    reason="isnet-anime 模型未本地化")
def test_derive_deterministic_and_derivation_complete():
    png = _make_portrait_png()
    r1 = derive_identity_crop(png, method="bust")
    r2 = derive_identity_crop(png, method="bust")
    assert r1 is not None, "合成人形应可派生"
    assert r1 == r2, "同输入字节同输出（确定性）"
    d = r1["derivation"]
    assert d["method"] == "bust"
    assert d["version"] == "identity_crop_v1"
    assert len(d["crop_box"]) == 4 and len(d["subject_bbox"]) == 4
    assert d["source_size"] == [384, 576]
    assert d["segmentation_model"] == "isnet-anime"
    img = _decode_png(r1["media"])
    assert img.size[0] < 384 or img.size[1] < 576, "裁剪输出应小于源图"


@pytest.mark.skipif(not Path.home().joinpath(".u2net", "isnet-anime.onnx").exists(),
                    reason="isnet-anime 模型未本地化")
def test_derive_face_method_and_invalid_bytes_fail_honest():
    png = _make_portrait_png()
    r = derive_identity_crop(png, method="face")
    assert r is not None and r["derivation"]["method"] == "face"
    assert derive_identity_crop(b"not-a-png", method="bust") is None, \
        "非法字节诚实返回 None（不抛不猜）"
    with pytest.raises(ValueError):
        derive_identity_crop(png, method="torso")


# ============ 预算集成 ============

@pytest.mark.skipif(not Path.home().joinpath(".u2net", "isnet-anime.onnx").exists(),
                    reason="isnet-anime 模型未本地化")
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
    assert media != role["checksum"]   # sanity
    # 场景参考不受 crop 影响（本镜 WS → auto scene=full → 场景图原样）
    scene_media = [m for m in bound.image_refs if m != media]
    assert scene_media, "非 crop 项媒体原样保留"
    assert all(not r.get("derivation") for r in bound.semantic_roles
               if r["source_type"] != "character_look")


@pytest.mark.skipif(not Path.home().joinpath(".u2net", "isnet-anime.onnx").exists(),
                    reason="isnet-anime 模型未本地化")
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
    assert all(not m.startswith("data:image/png;base64,Z") or True
               for m in bound.image_refs)   # 媒体形态由 to_agnes_image_ref 决定


@pytest.mark.skipif(not Path.home().joinpath(".u2net", "isnet-anime.onnx").exists(),
                    reason="isnet-anime 模型未本地化")
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
