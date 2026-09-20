# tests/test_shot_generation_post_commit_guard.py
# 31.md Phase C2 — 图片 handler post-provider commit guard（§13 阶段3）。
#
# 视频路径早有 native_av_pre_commit_check；本套件补齐图片侧：
#   - guard 单元：readiness/binding_revision/SIV 对比键（actual-deps-only，
#     document_revision 不比——别的 shot refresh 不误伤本镜 commit）；
#   - handler race：provider 窗口内改被绑 Look approval → 结构化拒绝、批次不落库、
#     候选文件保留（diagnostic artifact）、provider 确实被调用过（诚实）；
#   - handler 无关变更（Bob look label）→ 不误伤、正常 commit；
#   - 尾帧同口径。
# 全程 Fake provider 零外部调用。
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import deps
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.generation.image_adapter import (
    BoundGenerationError,
    post_generation_basis_guard,
)
from src.resources.store import ResourceStore
from src.shot_binding import ShotBindingService

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
from test_shot_binding_s3b1 import LOOK, LOOK_BOB, make_pdir, run  # noqa: E402
from test_shot_binding_s3b2 import _build_scene  # noqa: E402
from test_shot_binding_s3c1 import FakeImageClient, _bind_default, _StubPM  # noqa: E402
from test_shot_binding_s3c2 import (  # noqa: E402
    FakeMediaClient,
    _current_basis,
    _seed_first_frame,
)


def _load_shot(pdir, shot_id="s01_c01"):
    return ProjectStore(pdir).load().get_shot(shot_id)


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


@pytest.fixture()
def venv(tmp_path, monkeypatch):
    _clear_for_test()
    pdir = make_pdir(tmp_path)
    (pdir / "output" / "scene_system.json").unlink()
    ids = run(_build_scene(pdir))
    svc = ShotBindingService(pdir)
    fake = FakeMediaClient()
    monkeypatch.setattr(deps, "project_manager", _StubPM(pdir))
    monkeypatch.setattr(deps, "agnes_client", fake)
    yield pdir, ids, svc, fake
    _clear_for_test()


def _revoke_alice_look(pdir):
    """改被绑 Look：撤销唯一 approved reference（→ validity NOT_READY）。"""
    rd = ResourceStore(pdir, pdir.name).load()
    rd.looks[LOOK].approved_reference_asset_ids = []
    rd.looks[LOOK].approval_status = "unreviewed"
    ResourceStore(pdir, pdir.name).save(rd)


def _touch_bob_look(pdir):
    """无关变更：Bob look label（不进本镜 binding 依赖）。"""
    rd = ResourceStore(pdir, pdir.name).load()
    rd.looks[LOOK_BOB].label = "Bob mutated"
    ResourceStore(pdir, pdir.name).save(rd)


# ============ Guard 单元 ============

def test_guard_passes_when_basis_current(cenv):
    pdir, ids, svc, _fake = cenv
    _bind_default(svc, ids)
    basis = dict(svc.compile_reference_pack("s01_c01")["pack"]["basis"])
    post_generation_basis_guard(svc, "s01_c01", basis)   # 不抛 = 通过


def test_guard_blocks_when_dependency_invalidated(cenv):
    pdir, ids, svc, _fake = cenv
    _bind_default(svc, ids)
    basis = dict(svc.compile_reference_pack("s01_c01")["pack"]["basis"])
    _revoke_alice_look(pdir)
    with pytest.raises(BoundGenerationError) as ei:
        post_generation_basis_guard(svc, "s01_c01", basis)
    assert ei.value.code == "dependency_changed_before_commit"


# ============ First-frame handler race ============

def test_first_frame_race_blocks_commit(cenv, monkeypatch):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    shot_before = _load_shot(pdir)
    batches_before = len(shot_before.first_frame_batches)

    async def racing_generate(*a, **kw):
        _revoke_alice_look(pdir)          # provider 窗口内依赖失效
        return await FakeImageClient.generate_image(fake, **kw)

    monkeypatch.setattr(fake, "generate_image", racing_generate)
    from test_shot_binding_s3c1 import _run_handler
    with pytest.raises(BoundGenerationError) as ei:
        _run_handler(pdir)
    assert ei.value.code == "dependency_changed_before_commit"
    assert len(fake.calls) == 1, "provider 确实被调用过（诚实：不是 dispatch 前拦截）"
    shot_after = _load_shot(pdir)
    assert len(shot_after.first_frame_batches) == batches_before, "批次不落库"
    assert shot_after.selected_first_frame == shot_before.selected_first_frame


def test_first_frame_unrelated_mutation_commits(cenv, monkeypatch):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    batches_before = len(_load_shot(pdir).first_frame_batches)

    async def racing_generate(*a, **kw):
        _touch_bob_look(pdir)             # 无关变更（actual-deps-only 不误伤）
        return await FakeImageClient.generate_image(fake, **kw)

    monkeypatch.setattr(fake, "generate_image", racing_generate)
    from test_shot_binding_s3c1 import _run_handler
    _run_handler(pdir)
    assert len(_load_shot(pdir).first_frame_batches) == batches_before + 1, \
        "无关变更不误伤，批次正常 commit"


# ============ Last-frame handler race ============

def test_last_frame_race_blocks_commit(venv, monkeypatch):
    from test_shot_binding_s3c2 import _run_last_frame_handler
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc), link_mode="keyframes")
    shot_before = _load_shot(pdir)
    batches_before = len(shot_before.last_frame_batches)

    async def racing_generate(*a, **kw):
        _revoke_alice_look(pdir)
        return await FakeMediaClient.generate_image(fake, **kw)

    monkeypatch.setattr(fake, "generate_image", racing_generate)
    with pytest.raises(BoundGenerationError) as ei:
        _run_last_frame_handler(pdir)
    assert ei.value.code == "dependency_changed_before_commit"
    assert len(fake.image_calls) == 1
    shot_after = _load_shot(pdir)
    assert len(shot_after.last_frame_batches) == batches_before, "尾帧批次不落库"
    assert shot_after.selected_last_frame == shot_before.selected_last_frame


def test_last_frame_unrelated_mutation_commits(venv, monkeypatch):
    from test_shot_binding_s3c2 import _run_last_frame_handler
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc), link_mode="keyframes")
    batches_before = len(_load_shot(pdir).last_frame_batches)

    async def racing_generate(*a, **kw):
        _touch_bob_look(pdir)
        return await FakeMediaClient.generate_image(fake, **kw)

    monkeypatch.setattr(fake, "generate_image", racing_generate)
    _run_last_frame_handler(pdir)
    assert len(_load_shot(pdir).last_frame_batches) == batches_before + 1
