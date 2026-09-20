# tests/test_population_constraint.py
# 31.md Phase C5 — 人数排除子句（G4 杠杆 2，参数门控）验收。
#
# 覆盖：子句确定性（cast 计数）；默认关；开启时 tail 追加、provenance 记录；
# handler job.params 透传；UNBOUND legacy 恒不含子句。
import asyncio
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
from test_shot_binding_s3b1 import LOOK, LOOK_BOB, make_pdir, run  # noqa: E402
from test_shot_binding_s3b2 import _bind, _build_scene  # noqa: E402
from test_shot_binding_s3c1 import (  # noqa: E402
    FakeImageClient,
    _bind_default,
    _load_shot,
    _run_handler,
    _StubPM,
)
from routers.shots import _handler_first_frame  # noqa: E402
from src.shot_binding import ShotBindingService  # noqa: E402

POP_MARK = "Population constraint:"


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


def test_clause_off_by_default_and_on_by_param(cenv):
    pdir, ids, svc, _fake = cenv
    _bind_default(svc, ids)
    project = ProjectStore(pdir).load()
    shot = _load_shot(pdir)

    off = build_bound_first_frame_input(pdir, project, shot)
    assert POP_MARK not in off.prompt
    assert off.population_constraint is False

    on = build_bound_first_frame_input(pdir, project, shot,
                                       population_constraint=True)
    assert POP_MARK in on.prompt
    assert "exactly one person" in on.prompt
    assert on.population_constraint is True


def test_clause_multi_character_count(cenv):
    pdir, ids, svc, _fake = cenv
    _bind_default(svc, ids)
    store = ProjectStore(pdir)

    def _recast(s):
        s.characters = ["Alice", "Bob"]
    store.update_shot("s01_c02", _recast)
    _bind(svc, shot_id="s01_c02", inst=ids["inst"], view=ids["view_a"],
          characters=[{"character_name": "Alice", "look_id": LOOK},
                      {"character_name": "Bob", "look_id": LOOK_BOB}])
    shot2 = _load_shot(pdir, "s01_c02")
    on = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(), shot2,
                                       population_constraint=True)
    assert "exactly 2 people" in on.prompt


def test_handler_threads_population_param(cenv):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)

    async def emit(*a, **k):
        pass
    job = SimpleNamespace(project=pdir.name, shot_id="s01_c01",
                          params={"variants": 1, "population_constraint": True},
                          kind="first_frame")
    asyncio.run(_handler_first_frame(job, emit))
    assert POP_MARK in fake.calls[0]["prompt"]
    # provenance 落库
    shot = _load_shot(pdir)
    gb = shot.first_frame_batches[-1].generation_basis
    assert gb["population_constraint"] is True


def test_unbound_never_contains_clause(cenv):
    """UNBOUND legacy 路径与 population param 无关（31.md §8：不为统一破坏老项目）。"""
    pdir, ids, svc, fake = cenv
    _run_handler(pdir)   # 不 bind；params 不带 flag
    assert POP_MARK not in fake.calls[0]["prompt"]
