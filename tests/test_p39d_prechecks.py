"""Phase 39D 输入语义预检（用户 2026-09-26 定版：reference ≠ first frame）。

四类视觉输入角色互不共用姿态 gate：
  A. REFERENCE_ONLY（multi 参考槽/Look 场景参考）：姿态命中只记
     pose_reference_mismatch_soft——不阻断/不要求补参考/不请用户改故事；
  B. HARD_START_BOUNDARY（i2v 首帧）：静态参考资产被选作首帧 × 位移动作开场
     = boundary conflict（阻断级，换边界/换 mode/如实报告）；
  C. RELAY_START_BOUNDARY（vref=prev_tail）：开场=上一镜真实 video_end_frame，
     Look 不得覆盖，不做姿态预判；
  D/E/F（MINOR/MAJOR/Intentional Cut）为 skill 文本锚定，见
     test_skill_content_canary_lessons.py。

全部 deterministic，0 网络；跨域夹具复用 S3B 系列（make_pdir/_build_scene）。
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from src.agents.director import SHOT_UPDATABLE_FIELDS, _clean_shot_fields
from src.agents.pregen_checks import (classify_video_input_role,
                                      detect_pose_reference_conflict,
                                      shot_input_semantics)
from src.core.models import Asset, ShotStatus
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


# ============ 纯检测器（角色无关） ============
def test_detector_static_ref_vs_locomotion_motion():
    c = detect_pose_reference_conflict(
        ["丹顶鹤在晨光中的天空飞行，飞过森林"], [("ref_a", "站姿正面")])
    assert c is not None
    assert "飞行" in c["matched_motion_keywords"]
    assert c["static_reference_ids"] == ["ref_a"]
    assert c["motion_matched_reference_ids"] == []


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


# ============ 角色分类（真实 dispatch 语义） ============
def test_classify_multi_no_relay_is_reference_only():
    class _S:   # multi + vref=None + 无 relay
        shot_id = "s01_c01"
        link_mode = "multi"
        video_reference_asset_id = ""
        opening_source = ""
    role = classify_video_input_role(_S(), None)
    assert role["generation_mode"] == "multi"
    assert role["semantic_role"] == "reference_only"


def test_classify_multi_relay_is_relay_boundary():
    class _S:
        shot_id = "s01_c02"
        link_mode = "multi"
        video_reference_asset_id = "prev_tail"
        opening_source = ""
    relay = {"source_shot_id": "s01_c01", "end_source_version": "vv_1", "url": "http://x"}
    role = classify_video_input_role(_S(), relay)
    assert role["semantic_role"] == "relay_start_boundary"
    assert role["relay_source_shot"] == "s01_c01"


def test_classify_i2v_is_hard_start_boundary():
    class _S:
        shot_id = "s01_c01"
        link_mode = "i2v"
        video_reference_asset_id = ""
        opening_source = ""
    assert classify_video_input_role(_S(), None)["semantic_role"] \
        == "hard_start_boundary"


# ============ Case A：multi 直生 + 站姿 Look × 飞行开场 ============
def _env_bound(tmp_path, extra_motion_ref: bool = False):
    """make_pdir + 场景链 + binding；Alice Look approved 参考标记站姿。"""
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
    r = run(ShotBindingService(pdir).create_binding_async({
        "shot_id": "s01_c01", "scene_id": "s01",
        "character_bindings": [{"character_name": "Alice", "look_id": LOOK}],
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"],
                          "pinned_scene_instance_view_id": ids["sciv_a"]},
        "prop_bindings": []}))
    assert r["ok"]
    ProjectStore(pdir).update_shot(
        "s01_c01",
        lambda s: (setattr(s, "link_mode", "multi"),
                   setattr(s, "description", "丹顶鹤在晨光中的天空滑翔")))
    return pdir, ids


def test_caseA_reference_only_soft_not_block(tmp_path):
    """multi 直生 + 站姿 Look × 飞行开场：soft 诊断，绝不阻断。"""
    pdir, _ids = _env_bound(tmp_path)
    proj = ProjectStore(pdir).load()
    sem = shot_input_semantics(pdir, pdir.name, proj, proj.get_shot("s01_c01"))
    assert sem is not None
    assert sem["semantic_role"] == "reference_only"
    assert sem["pose_gate"]["outcome"] == "soft_diagnostic"
    assert sem["pose_gate"]["applied"] is True
    assert {i["semantic_role"] for i in sem["input_images"]} == {"reference_only"}
    assert "boundary_conflict" not in sem, "reference-only 不得产生边界冲突"


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


def _gen_videos(rt, pdir, args):
    from src.agents.director_visual import _execute_shot_generate_videos
    return run(_execute_shot_generate_videos(rt, pdir.name, json.dumps(args),
                                             "生成", None))


def _gen_images(rt, pdir, args):
    from src.agents.director_visual import _execute_shot_generate_images
    return run(_execute_shot_generate_images(rt, pdir.name, json.dumps(args),
                                             "生成", None))


def test_caseA_wiring_video_proceeds_with_soft_diagnostic(tmp_path):
    pdir, _ids = _env_bound(tmp_path)
    q = _FakeQueue()
    out = _gen_videos(_rt(pdir, q), pdir, {
        "shot_ids": ["s01_c01"]})
    assert len(q.submitted) == 1, "reference-only soft 风险不得阻断提交"
    roles = out.get("reference_roles") or []
    assert roles and roles[0]["semantic_role"] == "reference_only"
    assert roles[0]["pose_gate"]["outcome"] == "soft_diagnostic"
    soft = out.get("pose_soft_diagnostics") or []
    assert soft and soft[0]["diagnostic"] == "pose_reference_mismatch_soft"
    assert "pose_reference_mismatch_soft" in (out.get("note") or "")
    assert "⚠️" not in (out.get("note") or ""), "soft 诊断不得升级为告警口吻"


def test_caseA_wiring_bound_required_images_not_blocked(tmp_path):
    """分镜图（bound_required）× 站姿 Look × 飞行描述：不得阻断（39D 修正
    ——image refs 也是 reference_only，39C 时期的 pose 阻断已废除）。"""
    pdir, _ids = _env_bound(tmp_path)
    q = _FakeQueue()
    out = _gen_images(_rt(pdir, q), pdir, {
        "targets": [{"shot_id": "s01_c01", "frame": "first"}],
        "reference_policy": "bound_required"})
    assert len(q.submitted) == 1, "bound_required 图片生成不得被姿态软风险阻断"
    assert not any(r.get("error") == "pose_reference_conflict"
                   for r in out.get("results") or [])


# ============ Case B：硬首帧边界冲突 ============
def test_caseB_i2v_standing_ref_as_first_frame_blocks(tmp_path):
    """i2v + 静态站姿参考资产被直接选作硬首帧 × 飞行开场 = boundary conflict。"""
    pdir, _ids = _env_bound(tmp_path)
    ProjectStore(pdir).update_shot(
        "s01_c01",
        lambda s: (setattr(s, "link_mode", "i2v"),
                   setattr(s, "description", "丹顶鹤已在云端滑翔")))
    ref = ResourceStore(pdir).load().reference_assets["ref_alice000001"]
    ProjectStore(pdir).update_shot(
        "s01_c01",
        lambda s: s.first_frame.selected.__setattr__(
            "url", ref.url) if s.first_frame.selected else None)
    # 若无 selected 候选，直接构造一个（url 指向站姿参考资产）
    ProjectStore(pdir).update_shot(
        "s01_c01",
        lambda s: (s.first_frame.candidates.append(
            Asset(local="ff.png", url=ref.url, status=ShotStatus.DONE.value)),
            setattr(s.first_frame, "selected_index",
                    len(s.first_frame.candidates) - 1),
            setattr(s.first_frame, "status", ShotStatus.DONE.value)))
    proj = ProjectStore(pdir).load()
    sem = shot_input_semantics(pdir, pdir.name, proj, proj.get_shot("s01_c01"))
    assert sem["semantic_role"] == "hard_start_boundary"
    assert sem["pose_gate"]["outcome"] == "boundary_conflict"
    assert sem.get("boundary_conflict")

    q = _FakeQueue()
    out = _gen_videos(_rt(pdir, q), pdir, {
        "shot_ids": ["s01_c01"]})
    assert len(q.submitted) == 0, "硬首帧边界冲突必须提交前拦截"
    item = out["results"][0]
    assert item["error"] == "hard_start_boundary_conflict"
    assert "不得因此改写故事" in item["fix_hint"]


# ============ Case C：relay 边界 ============
def test_caseC_relay_boundary_no_pose_gate(tmp_path):
    pdir, _ids = _env_bound(tmp_path)
    ProjectStore(pdir).update_shot(
        "s01_c02",
        lambda s: (setattr(s, "link_mode", "multi"),
                   setattr(s, "video_reference_asset_id", "prev_tail"),
                   setattr(s, "description", "丹顶鹤俯瞰森林，继续滑翔")))
    ProjectStore(pdir).update_shot(
        "s01_c01",
        lambda s: s.video_end_frame.mark_done("end.jpg", "http://example/end.jpg"))
    proj = ProjectStore(pdir).load()
    sem = shot_input_semantics(pdir, pdir.name, proj, proj.get_shot("s01_c02"))
    assert sem["semantic_role"] == "relay_start_boundary"
    assert sem["relay_source_shot"] == "s01_c01"
    assert sem["pose_gate"]["applied"] is False
    assert sem["pose_gate"]["outcome"] == "relay_no_pose_gate"
    assert {i["semantic_role"] for i in sem["input_images"]} \
        == {"relay_start_boundary"}, "Look 不得出现在 relay 边位"

    q = _FakeQueue()
    out = _gen_videos(_rt(pdir, q), pdir, {
        "shot_ids": ["s01_c02"]})
    assert len(q.submitted) == 1, "relay 就绪即提交（multi 无首帧前置）"
    roles = out.get("reference_roles") or []
    assert roles and roles[0]["semantic_role"] == "relay_start_boundary"


def test_caseC_relay_not_ready_blocked_at_precheck(tmp_path):
    """vref=prev_tail 但上一镜末帧未就绪：工具层拦截（省配额，同 adapter fail-closed）。"""
    pdir, _ids = _env_bound(tmp_path)
    ProjectStore(pdir).update_shot(
        "s01_c02",
        lambda s: (setattr(s, "link_mode", "multi"),
                   setattr(s, "video_reference_asset_id", "prev_tail")))
    q = _FakeQueue()
    out = _gen_videos(_rt(pdir, q), pdir, {
        "shot_ids": ["s01_c02"]})
    assert len(q.submitted) == 0
    assert "relay 未就绪" in (out["results"][0].get("error") or "")


def test_caseA_multi_first_shot_needs_no_first_frame(tmp_path):
    """multi 直生镜无分镜首帧 = 正常路径（用户定版：首镜不生成首帧）。"""
    pdir, _ids = _env_bound(tmp_path)
    q = _FakeQueue()
    out = _gen_videos(_rt(pdir, q), pdir, {
        "shot_ids": ["s01_c01"]})
    blocked_errors = [r.get("error") for r in out.get("results") or []
                      if r.get("status") == "blocked"]
    assert not any("开场画面未就绪" in str(e) for e in blocked_errors), \
        "multi 不得要求首帧前置"


# ============ negative_prompt 写通路（39D §3.3 既有锚定，保持） ============
def test_negative_prompt_in_both_whitelists():
    assert "negative_prompt" in SHOT_UPDATABLE_FIELDS
    from src.storyboard.structure_service import _SHOT_WRITABLE_FIELDS
    assert "negative_prompt" in _SHOT_WRITABLE_FIELDS


def test_negative_prompt_in_tool_schemas():
    """工具 schema（LLM 接口面）必须与执行白名单一致——39D t2 实证：只改白名单
    不改 schema，Agent 按 schema 拒写 negative_prompt（落到 note 字段）。t3 又
    实证同类缺口：link_mode/video_reference_asset_id 不在 schema → Agent 无法
    设置 multi relay 链。"""
    from src.agents.director import DIRECTOR_TOOLS
    for name, get_props in (
            ("shot.update", lambda t: t["function"]["parameters"]["properties"]),
            ("shot.create", lambda t: t["function"]["parameters"]["properties"]),
            ("shot.update_many",
             lambda t: t["function"]["parameters"]["properties"]["updates"]["items"]["properties"])):
        tool = next(t for t in DIRECTOR_TOOLS if t["function"]["name"] == name)
        props = get_props(tool)
        assert "negative_prompt" in props, f"{name} schema 缺 negative_prompt"
        assert "link_mode" in props, f"{name} schema 缺 link_mode"
        assert "video_reference_asset_id" in props, \
            f"{name} schema 缺 video_reference_asset_id"


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
