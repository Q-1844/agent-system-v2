"""Phase 39D 确定性预检（HANDOFF §3.3——prose 不够的地方给牙齿）。

3.3.1 prompt-reference 姿态冲突预检（night_cloak / P39C s01 两次实证）：
  - 纯检测器 detect_pose_reference_conflict（词表判定）
  - 解析器 shot_pose_reference_conflict（binding/resources 只读解析）
  - _execute_generation 接线：bound_required 图片阻断 / legacy 结构化 warning
3.3.2 negative_prompt 写通路：两层白名单（structure_service + agent 工具层）
  + update_shot_fields 落库 + 不触发正向 prompt 失效（独立 API 参数）。

全部 deterministic，0 网络；跨域夹具复用 S3B 系列（make_pdir/_build_scene）。
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from src.agents.director import SHOT_UPDATABLE_FIELDS, _clean_shot_fields
from src.agents.pregen_checks import (detect_pose_reference_conflict,
                                      shot_pose_reference_conflict)
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.resources.models import ReferenceAsset
from src.resources.store import ResourceStore
from src.shot_binding import ShotBindingService
from src.storyboard.structure_service import update_shot_fields

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
from test_shot_binding_s3b1 import LOOK, make_pdir, run  # noqa: E402
from test_shot_binding_s3b2 import _build_scene  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated():
    _clear_for_test()
    yield
    _clear_for_test()


# ============ 3.3.1 纯检测器 ============
def test_detector_static_ref_vs_locomotion_motion():
    c = detect_pose_reference_conflict(
        ["丹顶鹤在晨光中的天空飞行，飞过森林"], [("ref_a", "站姿正面")])
    assert c is not None
    assert "飞行" in c["matched_motion_keywords"]
    assert c["static_reference_ids"] == ["ref_a"]
    assert c["motion_matched_reference_ids"] == []
    assert "why" in c


def test_detector_static_motion_no_conflict():
    assert detect_pose_reference_conflict(
        ["丹顶鹤全身端立在湖畔，收拢翅膀"],
        [("ref_a", "站姿正面")]) is None


def test_detector_english_wordlist():
    c = detect_pose_reference_conflict(
        ["the crane is flying across the sky"],
        [("ref_a", "standing pose")])
    assert c is not None
    assert "flying" in c["matched_motion_keywords"]
    assert "standing" in c["static_pose_keywords"]["ref_a"]


def test_detector_chinese_gliding_word():
    """P39D 首镜语义（云端滑翔）必须命中——词表回归锚定。"""
    c = detect_pose_reference_conflict(
        ["大鹤以从容的姿态滑翔于云端之上"], [("ref_a", "站立正面")])
    assert c is not None
    assert "滑翔" in c["matched_motion_keywords"]


def test_detector_multi_pose_look_advisory():
    """38 walk_side 先例：Look 已含动作匹配参考 → 冲突 + 修复通道提示。"""
    c = detect_pose_reference_conflict(
        ["丹顶鹤掠过湖面"], [("ref_hero", "站姿正面"), ("ref_side", "walk_side")])
    assert c is not None
    assert c["static_reference_ids"] == ["ref_hero"]
    assert c["motion_matched_reference_ids"] == ["ref_side"]


def test_detector_empty_inputs_none():
    assert detect_pose_reference_conflict([""], [("ref_a", "站姿正面")]) is None
    assert detect_pose_reference_conflict(["飞行"], []) is None
    assert detect_pose_reference_conflict(None, [("ref_a", "站姿正面")]) is None


# ============ 3.3.1 解析器（真实跨域夹具） ============
def _env_with_standing_ref(tmp_path, extra_motion_ref: bool = False):
    """make_pdir + 真实场景链 + binding；Alice Look 的 approved 参考标记为
    静态站姿（P39C s01 同型）。extra_motion_ref=True 时再加一张 walk_side 参考
    （多姿态 Look）。返回 (pdir, ids)。"""
    pdir = make_pdir(tmp_path)
    (pdir / "output" / "scene_system.json").unlink()
    ids = run(_build_scene(pdir))
    rdoc = ResourceStore(pdir).load()
    ref = rdoc.reference_assets["ref_alice000001"]
    ref.view = "站姿正面"
    ResourceStore(pdir).put_reference_asset(ref)
    if extra_motion_ref:
        rdoc = ResourceStore(pdir).load()
        rdoc.reference_assets["ref_side0000001"] = ReferenceAsset(
            asset_id="ref_side0000001", url="http://example/side.png",
            view="walk_side")
        ResourceStore(pdir).save(rdoc)
        rdoc = ResourceStore(pdir).load()
        lk = rdoc.looks[LOOK]
        lk.approved_reference_asset_ids.append("ref_side0000001")
        ResourceStore(pdir).put_look(lk)
    _ensure_bound(pdir, ids)
    # 镜头动作 = 飞行（P39C s01 同型：站姿参考 × 飞行文本）
    ProjectStore(pdir).update_shot(
        "s01_c01",
        lambda s: setattr(s, "description", "丹顶鹤在晨光中的天空飞行"))
    return pdir, ids


def _ensure_bound(pdir, ids):
    r = run(ShotBindingService(pdir).create_binding_async({
        "shot_id": "s01_c01", "scene_id": "s01",
        "character_bindings": [{"character_name": "Alice", "look_id": LOOK}],
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"],
                          "pinned_scene_instance_view_id": ids["sciv_a"]},
        "prop_bindings": []}))
    assert r["ok"]
    return r


def test_resolver_bound_shot_conflict(tmp_path):
    pdir, _ids = _env_with_standing_ref(tmp_path)
    proj = ProjectStore(pdir).load()
    shot = proj.get_shot("s01_c01")
    c = shot_pose_reference_conflict(pdir, pdir.name, shot)
    assert c is not None
    assert c["shot_id"] == "s01_c01"
    assert c["characters"] == ["Alice"]
    assert "ref_alice000001" in c["static_reference_ids"]


def test_resolver_unbound_shot_none(tmp_path):
    pdir, _ids = _env_with_standing_ref(tmp_path)
    proj = ProjectStore(pdir).load()
    shot = proj.get_shot("s01_c02")   # 未 binding
    assert shot_pose_reference_conflict(pdir, pdir.name, shot) is None


def test_resolver_motion_matched_ref_advisory(tmp_path):
    pdir, _ids = _env_with_standing_ref(tmp_path, extra_motion_ref=True)
    proj = ProjectStore(pdir).load()
    shot = proj.get_shot("s01_c01")
    c = shot_pose_reference_conflict(pdir, pdir.name, shot)
    assert c is not None
    assert "ref_side0000001" in c["motion_matched_reference_ids"], \
        "多姿态 Look：walk_side 参考列入修复通道"


def test_resolver_never_raises_on_missing_binding(tmp_path):
    pdir, _ids = _env_with_standing_ref(tmp_path)
    class _Ghost:
        shot_id = "nope_c99"
        description = "飞行"
        motion = ""
    assert shot_pose_reference_conflict(pdir, pdir.name, _Ghost()) is None


# ============ 3.3.1 _execute_generation 接线 ============
class _FakeJob:
    def __init__(self, job_id, shot_id):
        self.job_id, self.shot_id = job_id, shot_id
        self.state = "done"

    def cancel(self, force=False):
        self.state = "cancelled"


class _FakeQueue:
    def __init__(self):
        self.submitted = []

    def submit(self, project, kind, shot_id, **extra):
        j = _FakeJob(f"j{len(self.submitted) + 1}", shot_id)
        self.submitted.append({"project": project, "kind": kind,
                               "shot_id": shot_id, **extra})
        return j


def _rt(pdir, q):
    from src.agents.runtime import AgentRuntime
    return AgentRuntime(project_dir_resolver=lambda name: pdir,
                        settings_path=pdir.parent / "agents_data" / "settings.json",
                        client=None, job_queue_getter=lambda: q)


def _gen_images(rt, pdir, args):
    from src.agents.director_visual import _execute_shot_generate_images
    return run(_execute_shot_generate_images(rt, pdir.name, json.dumps(args),
                                             "生成", None))


def test_bound_required_blocks_pose_conflict_before_job(tmp_path):
    pdir, _ids = _env_with_standing_ref(tmp_path)
    q, rt = _FakeQueue(), None
    rt = _rt(pdir, q)
    out = _gen_images(rt, pdir, {
        "targets": [{"shot_id": "s01_c01", "frame": "first"}],
        "reference_policy": "bound_required"})
    assert len(q.submitted) == 0, "姿态冲突：bound_required 提交前拦截，零额度消耗"
    item = out["results"][0]
    assert item["error"] == "pose_reference_conflict"
    assert item["pose_conflict"]["static_reference_ids"]
    assert "video_reference_asset_id" in item["fix_hint"]


def test_legacy_image_path_carries_structured_warning(tmp_path):
    pdir, _ids = _env_with_standing_ref(tmp_path)
    q = _FakeQueue()
    out = _gen_images(_rt(pdir, q), pdir, {
        "targets": [{"shot_id": "s01_c01", "frame": "first"}]})
    assert len(q.submitted) == 1, "legacy 默认行为：不阻断照常提交"
    assert out["pose_reference_warnings"], "回执必须携带结构化姿态冲突预警"
    w = out["pose_reference_warnings"][0]
    assert w["shot_id"] == "s01_c01"
    assert "姿态冲突预警" in out["note"]


# ============ 3.3.2 negative_prompt 写通路 ============
def test_negative_prompt_in_both_whitelists():
    assert "negative_prompt" in SHOT_UPDATABLE_FIELDS
    from src.storyboard.structure_service import _SHOT_WRITABLE_FIELDS
    assert "negative_prompt" in _SHOT_WRITABLE_FIELDS


def test_clean_shot_fields_passes_negative_prompt():
    cleaned, _dropped = _clean_shot_fields({"negative_prompt": " 空无一人, 禁动物 "},
                                           set())
    assert cleaned["negative_prompt"] == "空无一人, 禁动物"


def test_update_shot_fields_persists_negative_prompt(tmp_path):
    pdir = make_pdir(tmp_path)
    proj = ProjectStore(pdir).load()
    res = update_shot_fields(proj, "s01_c01",
                             {"negative_prompt": "空无一人, 禁动物"})
    assert "negative_prompt" in res["changed"]
    assert res["prompts_invalidated"] == {}, "独立 API 参数，不失效正向 prompt"
    ProjectStore(pdir).save(proj)
    on_disk = ProjectStore(pdir).load()
    assert on_disk.get_shot("s01_c01").negative_prompt == "空无一人, 禁动物"


def test_negative_prompt_roundtrip_and_fingerprint(tmp_path):
    """落库 → 落盘 → 重读一致；字段进镜头内容指纹（OCC 覆盖）。"""
    from src.agents.director import _shot_token
    pdir = make_pdir(tmp_path)
    proj = ProjectStore(pdir).load()
    update_shot_fields(proj, "s01_c01", {"negative_prompt": "空无一人"})
    tok_before = _shot_token(proj.get_shot("s01_c01"))
    ProjectStore(pdir).save(proj)
    on_disk = ProjectStore(pdir).load()
    shot = on_disk.get_shot("s01_c01")
    assert shot.negative_prompt == "空无一人"
    assert _shot_token(shot) == tok_before


def test_native_av_pack_carries_negative_prompt(tmp_path):
    """生成通路终点：native_av_pack 的 provider options 投影携带
    shot.negative_prompt（独立 API 参数，非正向 prompt 拼接）。"""
    from src.generation.native_av_pack import _default_provider_options
    pdir = make_pdir(tmp_path)
    proj = ProjectStore(pdir).load()
    update_shot_fields(proj, "s01_c01", {"negative_prompt": "空无一人, 禁动物"})
    req = _default_provider_options(proj, proj.get_shot("s01_c01"))
    assert req.get("negative_prompt") == "空无一人, 禁动物"
