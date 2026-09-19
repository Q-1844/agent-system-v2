# tests/test_shot_review.py
# Phase D2（30.md §10-§13）：visual.review_shot 单元测试。
# 真机 Vision 调用不在单测范围（mock 契约测试 + fixture 真机验证见 30.md §13 策略）。
import pytest

from src.agents.shot_review import (
    SHOT_REVIEW_DIMENSIONS,
    SHOT_REVIEW_HARD_DIMS,
    _clean_shot_review,
    _validate_shot_review,
    derive_overall,
)


def _dims(**overrides):
    dims = {k: {"status": "pass", "note": ""} for k in SHOT_REVIEW_DIMENSIONS}
    for k, v in overrides.items():
        dims[k] = {"status": v[0], "note": v[1]}
    return dims


def test_D2_derive_overall_all_pass():
    d = derive_overall(_dims())
    assert d["overall"] == "pass" and d["hard_fails"] == []


def test_D2_derive_overall_hard_fail_cannot_pass():
    """30.md §12：hard gate 失败 → overall 绝不 pass（代码推导，不信模型整体印象）。"""
    for hard in SHOT_REVIEW_HARD_DIMS:
        d = derive_overall(_dims(**{hard: ("fail", "x")}))
        assert d["overall"] == "needs_fix", hard
        assert hard in d["hard_fails"]


def test_D2_derive_overall_soft_fail():
    d = derive_overall(_dims(subject_scale=("fail", "too small")))
    assert d["overall"] == "needs_fix" and d["hard_fails"] == []
    assert d["soft_fails"] == ["subject_scale"]


def test_D2_derive_overall_uncertain():
    d = derive_overall(_dims(identity_look=("uncertain", "no ref")))
    assert d["overall"] == "uncertain"


def test_D2_derive_overall_missing_dimension_treated_uncertain():
    dims = _dims()
    dims.pop("prop_fidelity")
    d = derive_overall(dims)
    assert d["overall"] == "uncertain" and "prop_fidelity" in d["uncertains"]


def test_D2_validate_contract():
    good = {
        "observed": {"people_count": 1, "people_notes": "a man",
                     "shot_size_observed": "CU", "camera_semantics_observed": "none",
                     "scene_visibility": "partial_environment"},
        "dimensions": {k: {"status": "pass", "note": "ok"} for k in SHOT_REVIEW_DIMENSIONS},
        "director_feedback": ["fine"],
        "summary": "s",
    }
    assert _validate_shot_review(good) == []
    bad = dict(good)
    bad["observed"] = {**good["observed"], "people_count": "one"}
    assert any("people_count" in e for e in _validate_shot_review(bad))
    bad2 = {**good, "dimensions": {**good["dimensions"],
                                   "cast_cardinality": {"status": "maybe"}}}
    assert any("cast_cardinality" in e for e in _validate_shot_review(bad2))


def test_D2_clean_truncates():
    obj = {
        "observed": {"people_count": 2, "people_notes": "x" * 500,
                     "shot_size_observed": "MS", "camera_semantics_observed": "none",
                     "scene_visibility": "full_location"},
        "dimensions": {k: {"status": "pass", "note": "n" * 200}
                       for k in SHOT_REVIEW_DIMENSIONS},
        "director_feedback": [f"fb{i}" for i in range(10)],
        "summary": "s" * 400,
    }
    c = _clean_shot_review(obj)
    assert len(c["observed"]["people_notes"]) <= 300
    assert len(c["director_feedback"]) <= 6
    assert len(c["summary"]) <= 300
    assert all(len(v["note"]) <= 120 for v in c["dimensions"].values())


# ============ 工具执行器接线（真 bug 防回归：D2 真机曾因缺 ProjectStore 导入失败）============
def test_D2_execute_review_shot_wiring(tmp_path, monkeypatch):
    """stub rt + stub vision client：_execute_review_shot 必须走通
    （导入完整、候选解析、契约解析、receipt 形状）。"""
    import asyncio
    import json as _json
    from pathlib import Path
    from src.core.models import Candidate, ImageBatch, Project as P, Scene, Shot as S
    from src.core.store import ProjectStore
    from src.agents import shot_review as sr

    # 最小项目：1 镜 + 1 批次 1 候选（本地 png）
    proj = P(name="t")
    proj.scenes = [Scene(scene_id="s01", summary="x", characters=["沈夜"],
                         shots=[S(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="玉佩特写", shot_size="extreme close-up",
                                  characters=["沈夜"])])]
    pdir = tmp_path / "proj"
    pdir.mkdir()
    (pdir / "output").mkdir()
    ProjectStore(pdir).save(proj)
    img = pdir / "f.png"
    img.write_bytes(b"\x89PNG fake")
    proj = ProjectStore(pdir).load()
    shot = proj.get_shot("s01_c01")
    batch = ImageBatch(batch_id="ff_x", kind="first_frame", created_at=1.0,
                       candidates=[Candidate(candidate_id="ff_x_1", local=str(img),
                                             status="done")])
    shot.first_frame_batches = [batch]
    shot.selected_first_frame = "ff_x_1"
    ProjectStore(pdir).save(proj)

    good = {
        "observed": {"people_count": 1, "people_notes": "one", "shot_size_observed": "ECU",
                     "camera_semantics_observed": "none", "scene_visibility": "minimal_background"},
        "dimensions": {k: {"status": "pass", "note": "ok"} for k in sr.SHOT_REVIEW_DIMENSIONS},
        "director_feedback": ["ok"],
        "summary": "s",
    }

    class _Client:
        async def chat_completion_vision(self, messages, **kw):
            return {"content": _json.dumps(good), "model": sr.VISION_MODEL, "usage": {}}

    class _StubRT:
        def _resolve_project_dir(self, project):
            return pdir
        def record_work(self, *a, **k):
            pass
        def _resolve_client_or_none(self, **k):
            return _Client()

    receipt = asyncio.run(sr._execute_review_shot(
        _StubRT(), "t", _json.dumps({"shot_id": "s01_c01"}), None, None))
    assert receipt["ok"] is True
    assert receipt["derived"]["overall"] == "pass"
    assert receipt["reviewed_refs"]["image_source"] == "ff_x_1"
