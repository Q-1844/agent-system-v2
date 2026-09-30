"""Phase 39D：视频版本的"生成输入"记录与显示（用户定版——首尾帧可以是参考）。

系统职责：把每个视频实际消费的输入图（slot + semantic_role + url）持久化到
VideoVersion.input_visuals 并随 API 透出，前端以标记区分（参考=琥珀框、
接力边界=蓝框、硬边界=绿框）。skill 只写指导（参考优先等），不做系统强制。

_routers_shots 的 _build_input_visuals 以 adapter 的 semantic_roles 为权威
槽位语义；本测试覆盖四类 dispatch 形态 + round-trip。
"""
import json
import sys
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
sys.path.insert(0, str(sys_path.parent.parent))

from routers.shots import _build_input_visuals  # noqa: E402
from src.core.models import VideoVersion  # noqa: E402


class _BV:
    """最小 BoundVideoRequest 替身（只用 semantic_roles / video_reference）。"""
    def __init__(self, semantic_roles, video_reference="",
                 identity_reference=None, scene_reference=None):
        self.semantic_roles = semantic_roles
        self.video_reference = video_reference
        self.identity_reference = identity_reference
        self.scene_reference = scene_reference


def test_multi_relay_tail_is_start_boundary():
    """组内接续：上一镜真实尾帧进首帧槽（relay_start_boundary，非参考）。"""
    bv = _BV([{"slot": "multi_reference", "temporal_semantics": "throughout",
               "candidate_id": None, "reference_asset_id": None,
               "source_type": "prev_tail_relay",
               "source_shot_id": "s01_c01", "end_source_version": "vv_a",
               "batch_id": None}], video_reference="http://x/tail.jpg")
    out = _build_input_visuals(mode="multi", first_url="http://x/tail.jpg",
                               last_url=None, bound_video=bv)
    assert len(out) == 1
    e = out[0]
    assert e["slot"] == "first"
    assert e["semantic_role"] == "relay_start_boundary"
    assert e["source_shot_id"] == "s01_c01"
    assert e["url"] == "http://x/tail.jpg"


def test_multi_reference_only_is_marked():
    """multi 直生（组首镜）：关键图作参考 → slot=reference + reference_only
    （前端以琥珀标记区分，这是"参考必标记"的数据落点）。"""
    bv = _BV([{"slot": "multi_reference", "temporal_semantics": "throughout",
               "candidate_id": None, "reference_asset_id": "ref_look0001",
               "source_type": "character_look", "batch_id": None}],
             video_reference="http://x/look.png")
    out = _build_input_visuals(mode="multi", first_url="http://x/look.png",
                               last_url=None, bound_video=bv)
    e = out[0]
    assert e["slot"] == "reference"
    assert e["semantic_role"] == "reference_only"
    assert e["reference_asset_id"] == "ref_look0001"


def test_multi_scene_reference_takes_scene_url_not_tail():
    """44A 取证修复：三参考哨兵第三槽（scene_instance_view）的记录 URL 必须取
    scene_reference，不得回落 video_reference（=上镜尾帧，记录会张冠李戴）。"""
    bv = _BV(
        [{"slot": "multi_reference", "source_type": "prev_tail_relay",
          "source_shot_id": "s02_c02", "end_source_version": "vv_a",
          "reference_asset_id": None, "candidate_id": None, "batch_id": None},
         {"slot": "multi_reference", "source_type": "character_look",
          "reference_asset_id": "ref_look0001", "candidate_id": None,
          "batch_id": None},
         {"slot": "multi_reference", "source_type": "scene_instance_view",
          "reference_asset_id": "ref_siv0001", "candidate_id": None,
          "batch_id": None}],
        video_reference="http://x/tail.jpg",
        identity_reference={"url": "http://x/look.png",
                            "reference_asset_id": "ref_look0001"},
        scene_reference={"url": "http://x/siv.png",
                         "reference_asset_id": "ref_siv0001"})
    out = _build_input_visuals(mode="multi", first_url="http://x/tail.jpg",
                               last_url=None, bound_video=bv)
    assert [e["source_type"] for e in out] == \
        ["prev_tail_relay", "character_look", "scene_instance_view"]
    assert out[0]["url"] == "http://x/tail.jpg"
    assert out[1]["url"] == "http://x/look.png"
    assert out[2]["url"] == "http://x/siv.png", \
        "场景第三槽记录成尾帧 URL = provenance 张冠李戴（44A 实证缺陷）"
    assert out[2]["reference_asset_id"] == "ref_siv0001"


def test_i2v_hard_start_boundary():
    bv = _BV([{"slot": "first", "temporal_semantics": "start",
               "candidate_id": "c1", "batch_id": "b1"}])
    out = _build_input_visuals(mode="i2v", first_url="http://x/ff.png",
                               last_url=None, bound_video=bv)
    assert out == [{"slot": "first", "semantic_role": "hard_start_boundary",
                    "url": "http://x/ff.png", "candidate_id": "c1",
                    "batch_id": "b1"}]


def test_keyframes_two_boundaries():
    bv = _BV([{"slot": "first", "temporal_semantics": "start",
               "candidate_id": "c1", "batch_id": "b1"},
              {"slot": "last", "temporal_semantics": "end",
               "candidate_id": "c2", "batch_id": "b2"}])
    out = _build_input_visuals(mode="keyframes", first_url="http://x/f.png",
                               last_url="http://x/l.png", bound_video=bv)
    assert [e["semantic_role"] for e in out] == \
        ["hard_start_boundary", "hard_end_boundary"]


def test_legacy_recover_path_best_effort():
    """recover/legacy（无 adapter 语义）：按实际发送 URL 尽力标注。"""
    out = _build_input_visuals(mode="multi", first_url="http://x/r.png",
                               last_url=None, bound_video=None)
    assert out == [{"slot": "reference", "semantic_role": "reference_only",
                    "url": "http://x/r.png", "source_type": "legacy_multi"}]
    out2 = _build_input_visuals(mode="i2v", first_url="http://x/ff.png",
                                last_url=None, bound_video=None)
    assert out2[0]["semantic_role"] == "hard_start_boundary"


def test_video_version_input_visuals_roundtrip(tmp_path):
    """新字段落库 round-trip 不丢（_parse_video_version 白名单必须含它）。"""
    vv = VideoVersion(version_id="vv_test0001",
                      input_visuals=[{"slot": "reference",
                                      "semantic_role": "reference_only",
                                      "url": "http://x/a.png"}])
    d = json.loads(json.dumps(vv.to_dict() if hasattr(vv, "to_dict")
                              else _asdict(vv)))
    back = VideoVersion(**{k: v for k, v in d.items()
                           if k in VideoVersion.__dataclass_fields__})
    assert back.input_visuals == vv.input_visuals, \
        "round-trip 丢字段 = 系统显示功能失效"


def _asdict(obj):
    from dataclasses import asdict
    return asdict(obj)
