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
