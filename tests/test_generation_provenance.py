# tests/test_generation_provenance.py
# 31.md Phase C3 — ImageBatch.generation_basis provenance 补全（§12）。
#
# 断言一次生成落库后能完整回答"基于什么生成"：
#   basis（binding_revision/SIV/指纹）+ provider + size_tier/ratio + scene/human mode
#   + ref_layout + 发送序列 + used_reference_ids + semantic_roles（含 checksum，
#   无 media 字节）+ dropped_optional（含 reason）；UNBOUND legacy 只补几何键不越界。
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import deps
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.generation.image_adapter import build_bound_first_frame_input

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
from test_shot_binding_s3b1 import make_pdir, run  # noqa: E402
from test_shot_binding_s3b2 import _build_scene  # noqa: E402
from test_shot_binding_s3c1 import (  # noqa: E402
    FakeImageClient,
    _bind_default,
    _load_shot,
    _StubPM,
)
from test_shot_binding_s3c2 import (  # noqa: E402
    _current_basis,
    _run_last_frame_handler,
    _seed_first_frame,
)
from test_shot_binding_s3c1 import _run_handler as _run_ff  # noqa: E402
from src.shot_binding import ShotBindingService  # noqa: E402


class _StubPM2(_StubPM):
    pass


def _mkenv(tmp_path, monkeypatch, fake):
    _clear_for_test()
    pdir = make_pdir(tmp_path)
    (pdir / "output" / "scene_system.json").unlink()
    ids = run(_build_scene(pdir))
    svc = ShotBindingService(pdir)
    monkeypatch.setattr(deps, "project_manager", _StubPM(pdir))
    monkeypatch.setattr(deps, "agnes_client", fake)
    return pdir, ids, svc


def _last_basis_batch(pdir):
    shot = _load_shot(pdir)
    return shot.first_frame_batches[-1]


def _no_media_bytes(batch):
    """media 字节绝不进 project.json：basis 内不允许出现 data: URI。"""
    import json
    blob = json.dumps(batch.generation_basis, ensure_ascii=False)
    assert "data:" not in blob, "provenance 不得携带 media 字节"
    assert "base64" not in blob


# ============ First frame ============

def test_first_frame_bound_full_provenance(tmp_path, monkeypatch):
    pdir, ids, svc = _mkenv(tmp_path, monkeypatch, FakeImageClient())
    _bind_default(svc, ids)
    bound = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                          _load_shot(pdir))
    _run_ff(pdir)
    gb = _last_basis_batch(pdir).generation_basis
    assert gb["binding_revision"] == bound.basis["binding_revision"]
    assert gb["resolved_scene_instance_view_id"] == \
        bound.basis["resolved_scene_instance_view_id"]
    assert gb["visual_profile_fingerprint"] == bound.basis["visual_profile_fingerprint"]
    assert gb["image_provider"] == "agnes"
    assert gb["size_tier"] and gb["ratio"]
    assert gb["scene_reference_mode"] == bound.scene_reference_mode
    assert gb["human_reference_mode"] == bound.human_reference_mode
    assert gb["ref_layout"] == bound.ref_layout
    assert gb["used_reference_ids"] == bound.used_reference_ids
    assert gb["used_reference_ids"], "BOUND 单角色镜应有已发送参考"
    assert gb["sent_ref_sequence"] == [r["source_type"] for r in gb["semantic_roles"]]
    assert len(gb["semantic_roles"]) == len(bound.image_refs)
    for r in gb["semantic_roles"]:
        assert {"reference_asset_id", "source_type", "purpose", "priority",
                "temporal_scope", "checksum"} <= set(r)
    assert isinstance(gb["dropped_optional"], list)
    _no_media_bytes(_last_basis_batch(pdir))


def test_first_frame_unbound_legacy_basis_minimal(tmp_path, monkeypatch):
    pdir, ids, svc = _mkenv(tmp_path, monkeypatch, FakeImageClient())
    # 不 bind → UNBOUND legacy
    _run_ff(pdir)
    gb = _last_basis_batch(pdir).generation_basis
    assert gb["image_provider"] == "agnes"
    assert gb["size_tier"] and gb["ratio"]
    assert "used_reference_ids" not in gb, "legacy 不伪造 BOUND provenance"
    assert "semantic_roles" not in gb
    _no_media_bytes(_last_basis_batch(pdir))


def _tight_closeup_env(tmp_path, monkeypatch):
    """紧景别 crop 档公共环境：close-up + auto → context_only + human crop。"""
    from test_shot_binding_s3c1 import FakeImageClient as _F
    pdir, ids, svc = _mkenv(tmp_path, monkeypatch, _F())
    _bind_default(svc, ids)
    store = ProjectStore(pdir)

    def _tighten(s):
        s.shot_size = "close-up"
    store.update_shot("s01_c01", _tighten)
    return pdir


def test_first_frame_budget_crop_derivation_provenance(tmp_path, monkeypatch):
    """Phase 42 定版（43.md §一步）：紧景别 auto→crop——look 以头肩派生进请求，
    derivation provenance 进 semantic_roles；被裁项 reason 可追溯。"""
    import src.generation.identity_crop as ic
    monkeypatch.setattr(
        ic, "resolve_identity_crop_media",
        lambda *a, **k: {"media": "data:image/png;base64,QUJD",
                         "derivation": {"version": "identity_crop_v1",
                                        "method": "bust"}})
    pdir = _tight_closeup_env(tmp_path, monkeypatch)
    _run_ff(pdir)
    gb = _last_basis_batch(pdir).generation_basis
    reasons = {d.get("reason") for d in gb["dropped_optional"]}
    assert "scene_reference_context_only" in reasons, \
        f"场景图裁剪必须可追溯，实际 dropped reasons={reasons}"
    assert "human_reference_drop" not in reasons, "crop 档 look 不再被 drop"
    assert gb["scene_reference_mode"] == "context_only"
    assert gb["human_reference_mode"] == "crop"
    roles = gb.get("semantic_roles") or []
    look_roles = [r for r in roles if r.get("source_type") == "character_look"]
    assert look_roles, f"look 必须仍在请求中（crop 档），实际 roles={roles}"
    assert look_roles[0].get("derivation", {}).get("method") == "bust"
    _no_media_bytes(_last_basis_batch(pdir))


def test_first_frame_budget_crop_derivation_fails_honestly(tmp_path, monkeypatch):
    """crop 派生失败 → 诚实降级：diagnostic 记录 + look 回落源参考媒体（不 silent，
    不因派生失败把 look 整个丢掉）。"""
    import src.generation.identity_crop as ic
    monkeypatch.setattr(ic, "resolve_identity_crop_media",
                        lambda *a, **k: None)
    pdir = _tight_closeup_env(tmp_path, monkeypatch)
    _run_ff(pdir)
    gb = _last_basis_batch(pdir).generation_basis
    diags = {d.get("reason") for d in (gb.get("diagnostics") or [])}
    assert "identity_crop_derivation_failed" in diags, \
        f"派生失败必须记 diagnostic，实际 diagnostics={diags}"
    roles = gb.get("semantic_roles") or []
    look_roles = [r for r in roles if r.get("source_type") == "character_look"]
    assert look_roles, "回落源媒体后 look 仍应在请求中"
    assert look_roles[0].get("derivation") is None


# ============ Last frame ============

def test_last_frame_bound_full_provenance(tmp_path, monkeypatch):
    pdir, ids, svc = _mkenv(tmp_path, monkeypatch, FakeImageClient())
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc), link_mode="keyframes")
    # last-frame handler 假定 frames 目录已存在（真实流程先有首帧生成）；FakeImageClient
    # download 不建目录（s3c2 FakeMediaClient 会建）——这里镜像真实不变量。
    (pdir / "output" / "frames").mkdir(parents=True, exist_ok=True)
    _run_last_frame_handler(pdir)
    shot = _load_shot(pdir)
    gb = shot.last_frame_batches[-1].generation_basis
    assert gb["binding_readiness"] == "READY"
    assert gb["image_provider"] == "agnes"
    assert gb["size_tier"] and gb["ratio"]
    assert gb["scene_reference_mode"] in ("full", "context_only")
    assert gb["human_reference_mode"] in ("keep", "drop")
    assert gb["ref_layout"] == "first_frame_head"
    assert gb["used_reference_ids"], "BOUND 尾帧应有已发送参考（构图基础除外）"
    assert gb["sent_ref_sequence"] == [r["source_type"] for r in gb["semantic_roles"]]
    _no_media_bytes(shot.last_frame_batches[-1])
