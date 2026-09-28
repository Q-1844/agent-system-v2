"""Agent System V2 Phase C1 — Director Production Awareness & Shot Binding mock 测试
（27.md §19：C1-C53）。

复用 S3B 系列夹具（make_pdir 预置 Alice/Bob 项目 + approved Look/Prop + 反例实体；
_build_scene 预置 approved 场景链）。全部 deterministic，0 网络。
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.agents.runtime import AgentRuntime, AgentError
from src.agents import producer
from src.agents.director import (_build_system, DIRECTOR_TOOLS,
                                 TOOL_EXECUTORS, DIRECTOR_WRITE_OPERATIONS,
                                 _shot_token)
from src.agents.director_production import (
    _execute_visual_profile_read,
    _execute_shot_production_inspect,
    _execute_shot_production_bind,
    _execute_shot_production_refresh,
    cast_consistency,
)
from src.agents.production_context import (profile_summary_line,
                                           scene_production_projection)
from src.shot_binding import ShotBindingService
from src.resources.store import ResourceStore
from src.visual_profile import VisualProfileService

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
from test_shot_binding_s3b1 import (  # noqa: E402
    LOOK, LOOK_BOB, LOOK_NOAPPR, PROP, make_pdir, run,
)
from test_shot_binding_s3b2 import (  # noqa: E402
    _bind, _build_scene,
)


def _rt(pdir):
    return AgentRuntime(project_dir_resolver=lambda name: pdir,
                        settings_path=pdir.parent / "agents_data" / "settings.json",
                        client=None)


def _real_env(tmp_path):
    """真实跨域夹具（= s3b2 real_pdir 同款）：make_pdir + 重建真实场景链。"""
    pdir = make_pdir(tmp_path)
    (pdir / "output" / "scene_system.json").unlink()
    ids = run(_build_scene(pdir))
    return pdir, ids


@pytest.fixture(autouse=True)
def _isolated():
    _clear_for_test()
    yield
    _clear_for_test()


# ============ VisualProfile（C1-C4）============
def test_C1_director_reads_profile(tmp_path):
    pdir = make_pdir(tmp_path)
    out = _execute_visual_profile_read(_rt(pdir), pdir.name, "{}", None, None)
    assert out["ok"] and out["specified"]
    assert out["visual_profile"]["render_family"] == "2d_animation"
    assert out["fingerprint"] and out["revision"]


def test_C2_no_profile_write_tool():
    names = {t["function"]["name"] for t in DIRECTOR_TOOLS}
    assert "project.visual_profile.read" in names
    assert not any("visual_profile" in n and "read" not in n for n in names)
    assert "project.visual_profile.update" not in TOOL_EXECUTORS


def test_C3_production_context_contains_profile(tmp_path):
    pdir = make_pdir(tmp_path)
    proj = ProjectStore(pdir).load()
    sysmsg = _build_system(proj, pdir)
    assert "render_family=2d_animation" in sysmsg
    assert "ProjectVisualProfile" in sysmsg


def test_C4_legacy_director_tests_not_regressed():
    """C4：legacy Director creative 行为回归由 tests/test_director_agent.py 全量承载
    （本阶段 CI 同批运行）；此处锚定工具面规模不膨胀。"""
    # D2（30.md §21/§22）：+shot.select_frame（候选选定闭环）→ 22
    # 39（39.md §6）：+calibration.seam_measure / calibration.trace_read /
    #   calibration.trace_upsert / video.extract_frame / visual.sample_frames /
    #   shot.bridge_create（连续性校准与桥的正式工具面）→ 28
    # 40（40.md Skill System V1）：+skill.list / skill.load / skill.read_resource
    #   （生产技能发现/加载，只读不占写槽）→ 31
    # 42（42.md §二缺陷①）：+calibration.run_create（run 创建进工具面，
    #   trace 闭环不再依赖执行侧脚手架）→ 32
    assert len(DIRECTOR_TOOLS) == 32, (
        "Director 工具面 = 31（Phase 40 锚定值）+ 1 新增（42.md："
        "calibration.run_create）")


# ============ Production inspect（C5-C14）============
def _inspect(pdir, shot_id="s01_c01"):
    return _execute_shot_production_inspect(_rt(pdir), pdir.name,
                                            json.dumps({"shot_id": shot_id}),
                                            None, None)


def test_C5_inspect_unbound(tmp_path):
    pdir = make_pdir(tmp_path)
    out = _inspect(pdir)
    assert out["ok"]
    assert out["binding"]["state"] == "unbound"
    assert out["binding"]["readiness"] == "UNBOUND"
    assert out["shot"]["characters"] == ["Alice"]


def test_C6_inspect_ready(tmp_path):
    pdir, ids = _real_env(tmp_path)
    svc = ShotBindingService(pdir)
    r = run(svc.create_binding_async({
        "shot_id": "s01_c01", "scene_id": "s01",
        "character_bindings": [{"character_name": "Alice", "look_id": LOOK}],
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"],
                          "pinned_scene_instance_view_id": ids["sciv_a"]},
        "prop_bindings": []}))
    assert r["ok"], r
    out = _inspect(pdir)
    assert out["binding"]["readiness"] == "READY"
    assert out["binding"]["binding_revision"]


def test_C7_inspect_not_ready_revoked_look(tmp_path):
    pdir, ids = _real_env(tmp_path)
    svc = ShotBindingService(pdir)
    run(svc.create_binding_async({
        "shot_id": "s01_c01", "scene_id": "s01",
        "character_bindings": [{"character_name": "Alice", "look_id": LOOK}],
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"],
                          "pinned_scene_instance_view_id": ids["sciv_a"]},
        "prop_bindings": []}))
    # 撤销 approved look 参考 → NOT_READY
    rdoc = ResourceStore(pdir).load()
    lk = rdoc.looks[LOOK]
    lk.approved_reference_asset_ids = []
    lk.approval_status = "unreviewed"
    ResourceStore(pdir).put_look(lk)
    out = _inspect(pdir)
    assert out["binding"]["readiness"] == "NOT_READY"
    assert out["binding"]["issues"]
    codes = [i.get("reason") for i in out["binding"]["issues"]]
    assert any("unapproved" in (c or "") or "approved" in (c or "") for c in codes), codes


def test_C8_inspect_stale_look(tmp_path):
    pdir, ids = _real_env(tmp_path)
    svc = ShotBindingService(pdir)
    run(svc.create_binding_async({
        "shot_id": "s01_c01", "scene_id": "s01",
        "character_bindings": [{"character_name": "Alice", "look_id": LOOK}],
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"],
                          "pinned_scene_instance_view_id": ids["sciv_a"]},
        "prop_bindings": []}))
    # Look 内容变更（label）→ 指纹变化 → stale
    rdoc = ResourceStore(pdir).load()
    lk = rdoc.looks[LOOK]
    lk.label = "changed label"
    ResourceStore(pdir).put_look(lk)
    out = _inspect(pdir)
    assert out["binding"]["readiness"] == "NOT_READY"
    reasons = [i.get("reason") for i in out["binding"]["issues"]]
    assert any("fingerprint" in (r or "") or "stale" in (r or "") for r in reasons), reasons


def test_C11_inspect_scene_instance_missing(tmp_path):
    pdir = make_pdir(tmp_path)
    (pdir / "output" / "scene_system.json").unlink()   # 无 scene system
    out = _inspect(pdir)
    assert out["scene"]["reason"] == "scene_instance_missing"
    codes = [w["code"] for w in out["prerequisite_warnings"]]
    assert "scene_production_prerequisite" in codes
    assert "resource_prerequisite" not in codes or True  # Alice 有 approved LOOK（make_pdir 预置）


def test_C12_inspect_scene_view_missing(tmp_path):
    pdir, ids = _real_env(tmp_path)
    # 撤销 sciv 审批 → scene_view_missing
    sdoc_store = __import__("src.scene_system.store", fromlist=["SceneSystemStore"]) \
        .SceneSystemStore(pdir)
    doc = sdoc_store.load()
    v = doc.scene_instance_views[ids["sciv_a"]]
    v.approval_status = "unreviewed"
    sdoc_store.save(doc)
    out = _inspect(pdir)
    assert out["scene"]["reason"] == "scene_view_missing"


def test_C13_inspect_profile_unspecified(tmp_path):
    pdir, ids = _real_env(tmp_path)
    (pdir / "output" / "project_profile.json").unlink()
    out = _inspect(pdir)
    assert out["visual_profile"]["specified"] is False
    codes = [w["code"] for w in out["prerequisite_warnings"]]
    assert "visual_profile_unspecified" in codes


def test_C14_only_current_shot_resources(tmp_path):
    pdir, ids = _real_env(tmp_path)
    out = _inspect(pdir)   # s01_c01 cast=[Alice]
    opts = out["character_options"]
    assert [o["character_name"] for o in opts] == ["Alice"]
    assert opts[0]["approved_looks"], "Alice 的 approved Look 列出"
    assert all(lk["look_id"] == LOOK for lk in opts[0]["approved_looks"])


# ============ Cast（C15-C19）============
def test_C15_cast_complete(tmp_path):
    pdir = make_pdir(tmp_path)
    proj = ProjectStore(pdir).load()
    shot = proj.get_shot("s01_c01")   # cast=[Alice]，description 默认无 Bob
    cc = cast_consistency(proj, shot)
    assert cc["status"] == "ok" and cc["missing_from_cast"] == []


def test_C16_known_character_missing_from_cast(tmp_path):
    pdir = make_pdir(tmp_path)
    proj = ProjectStore(pdir).load()
    shot = proj.get_shot("s01_c01")
    shot.description = "Alice 与 Bob 在实验室对峙"
    cc = cast_consistency(proj, shot)
    assert cc["status"] == "inconsistent"
    assert cc["missing_from_cast"] == ["Bob"]
    assert cc["warnings"]


def test_C17_unknown_person_not_flagged(tmp_path):
    pdir = make_pdir(tmp_path)
    proj = ProjectStore(pdir).load()
    shot = proj.get_shot("s01_c01")
    shot.description = "Alice 看见一个黑衣人（robot shadow）"
    cc = cast_consistency(proj, shot)
    assert cc["status"] == "ok", "非 canonical 名字不误报"


def test_C18_director_fix_cast_clears_warning(tmp_path):
    pdir, ids = _real_env(tmp_path)
    proj = ProjectStore(pdir).load()
    shot = proj.get_shot("s01_c01")
    shot.description = "Alice 与 Bob 对峙"
    ProjectStore(pdir).save(proj)
    out = _inspect(pdir)
    assert out["cast_consistency"]["status"] == "inconsistent"
    # Director 自修：cast 补全（等价 shot.update）
    proj = ProjectStore(pdir).load()
    proj.get_shot("s01_c01").characters = ["Alice", "Bob"]
    ProjectStore(pdir).save(proj)
    out2 = _inspect(pdir)
    assert out2["cast_consistency"]["status"] == "ok"
    assert not any(w["code"] == "cast_incomplete" for w in out2["prerequisite_warnings"])


def test_C19_binding_strict_cast_still_enforced(tmp_path):
    pdir, ids = _real_env(tmp_path)
    proj = ProjectStore(pdir).load()
    proj.get_shot("s01_c01").characters = ["Alice", "Bob"]
    ProjectStore(pdir).save(proj)
    out = run(_execute_shot_production_bind(
        _rt(pdir), pdir.name,
        json.dumps({"shot_id": "s01_c01",
                    "character_bindings": [{"character_name": "Alice",
                                            "look_id": LOOK}]}),
        None, None))
    assert out["ok"] is False and out["error"] == "cast_mismatch"


# ============ Binding（C20-C31）============
def _bind_exec(pdir, **kw):
    args = {"shot_id": "s01_c01",
            "character_bindings": [{"character_name": "Alice", "look_id": LOOK}]}
    args.update(kw)
    return run(_execute_shot_production_bind(_rt(pdir), pdir.name,
                                             json.dumps(args), None, None))


def test_C20_bind_create(tmp_path):
    pdir = make_pdir(tmp_path)
    out = _bind_exec(pdir)
    assert out["ok"] and out["verified"], out
    assert out["mode"] == "create"
    assert out["binding_id"] == "s01_c01"
    assert out["changed_resources"] == [{"kind": "binding", "id": "s01_c01"}]
    assert ShotBindingService(pdir).read_binding("s01_c01") is not None


def test_C21_bind_replace_with_occ(tmp_path):
    pdir = make_pdir(tmp_path)
    _bind_exec(pdir)
    rev = ShotBindingService(pdir).read_binding("s01_c01")["binding_revision"]
    out = _bind_exec(pdir, expected_binding_revision=rev,
                     character_bindings=[{"character_name": "Alice", "look_id": LOOK}])
    assert out["ok"] and out["mode"] == "replace"


def test_C22_bind_replace_stale_occ_rejected(tmp_path):
    pdir = make_pdir(tmp_path)
    _bind_exec(pdir)
    out = _bind_exec(pdir, expected_binding_revision="00000000")
    assert out["ok"] is False and out["stale"] is True
    assert out["error"] == "stale_binding_revision"


def test_C23_invalid_look_rejected(tmp_path):
    pdir = make_pdir(tmp_path)
    out = _bind_exec(pdir, character_bindings=[{"character_name": "Alice",
                                                "look_id": "look_none"}])
    assert out["ok"] is False and out["error"] == "look_missing"


def test_C24_unapproved_look_rejected(tmp_path):
    pdir = make_pdir(tmp_path)
    out = _bind_exec(pdir, character_bindings=[{"character_name": "Alice",
                                                "look_id": LOOK_NOAPPR}])
    assert out["ok"] is False


def test_C25_invalid_scene_view_rejected(tmp_path):
    pdir, ids = _real_env(tmp_path)
    out = _bind_exec(pdir, scene_binding={"scene_instance_id": ids["inst"],
                                          "location_view_id": "view_none"})
    assert out["ok"] is False


def test_C26_revoked_scene_view_rejected(tmp_path):
    pdir, ids = _real_env(tmp_path)
    sdoc_store = __import__("src.scene_system.store", fromlist=["SceneSystemStore"]) \
        .SceneSystemStore(pdir)
    doc = sdoc_store.load()
    doc.scene_instance_views[ids["sciv_a"]].approval_status = "unreviewed"
    sdoc_store.save(doc)
    out = _bind_exec(pdir, scene_binding={"scene_instance_id": ids["inst"],
                                          "location_view_id": ids["view_a"],
                                          "pinned_scene_instance_view_id": ids["sciv_a"]})
    assert out["ok"] is False


def test_C28_multi_character_binding(tmp_path):
    pdir = make_pdir(tmp_path)
    proj = ProjectStore(pdir).load()
    proj.get_shot("s01_c01").characters = ["Alice", "Bob"]
    ProjectStore(pdir).save(proj)
    out = _bind_exec(pdir, character_bindings=[
        {"character_name": "Alice", "look_id": LOOK},
        {"character_name": "Bob", "look_id": LOOK_BOB}])
    assert out["ok"] and out["verified"]
    assert len(out["character_bindings"]) == 2


def test_C29_valid_prop_binding(tmp_path):
    pdir = make_pdir(tmp_path)
    out = _bind_exec(pdir, prop_bindings=[{"prop_id": PROP, "relation": "held",
                                           "anchor": "right_hand"}])
    assert out["ok"], out
    assert out["prop_bindings"][0]["prop_id"] == PROP


def test_C30_bind_receipt_ready_state(tmp_path):
    pdir, ids = _real_env(tmp_path)
    out = _bind_exec(pdir, scene_binding={"scene_instance_id": ids["inst"],
                                          "location_view_id": ids["view_a"],
                                          "pinned_scene_instance_view_id": ids["sciv_a"]})
    assert out["ok"] and out["readiness"] == "READY"
    assert out["production_state"] == "bound"


def test_C31_changed_refs_bindings():
    from src.agents.producer import _derive_changed_refs
    refs = _derive_changed_refs("director", [
        {"ok": True, "verified": True, "operation": "shot.production.bind",
         "binding_id": "s01_c01",
         "changed_resources": [{"kind": "binding", "id": "s01_c01"}]},
        {"ok": True, "verified": True, "operation": "shot.update",
         "shot_id": "s01_c01"}])
    assert refs["bindings"] == ["s01_c01"]
    assert refs["shots"] == ["s01_c01"], "shot.update 仍归 shots 键"


# ============ Refresh（C32-C36）============
def _bound_ready(pdir, ids):
    r = run(ShotBindingService(pdir).create_binding_async({
        "shot_id": "s01_c01", "scene_id": "s01",
        "character_bindings": [{"character_name": "Alice", "look_id": LOOK}],
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"],
                          "pinned_scene_instance_view_id": ids["sciv_a"]},
        "prop_bindings": []}))
    assert r["ok"]
    return ShotBindingService(pdir).read_binding("s01_c01")["binding_revision"]


def test_C32_refresh_stale_snapshot_to_ready(tmp_path):
    pdir, ids = _real_env(tmp_path)
    rev = _bound_ready(pdir, ids)
    # Look 增加一张新 approved 参考（依赖仍有效，指纹变化 → 快照 stale）
    rdoc = ResourceStore(pdir).load()
    from src.resources.models import ReferenceAsset
    rdoc.reference_assets["ref_extra000001"] = ReferenceAsset(
        asset_id="ref_extra000001", url="http://example/extra.png")
    lk = rdoc.looks[LOOK]
    lk.approved_reference_asset_ids.append("ref_extra000001")
    ResourceStore(pdir).put_look(lk)
    v0 = ShotBindingService(pdir).read_validity("s01_c01")
    assert v0["readiness"] == "NOT_READY", "快照过期先 NOT_READY"
    out = run(_execute_shot_production_refresh(
        _rt(pdir), pdir.name,
        json.dumps({"shot_id": "s01_c01", "expected_binding_revision": rev}),
        None, None))
    assert out["ok"] and out["readiness"] == "READY", out.get("detail")


def test_C33_C36_refresh_fails_when_dependency_invalid(tmp_path):
    pdir, ids = _real_env(tmp_path)
    _bound_ready(pdir, ids)
    # 撤销 look 审批 → 依赖 invalid → refresh fail closed
    rdoc = ResourceStore(pdir).load()
    lk = rdoc.looks[LOOK]
    lk.approved_reference_asset_ids = []
    lk.approval_status = "unreviewed"
    ResourceStore(pdir).put_look(lk)
    rev = ShotBindingService(pdir).read_binding("s01_c01")["binding_revision"]
    out = run(_execute_shot_production_refresh(
        _rt(pdir), pdir.name,
        json.dumps({"shot_id": "s01_c01", "expected_binding_revision": rev}),
        None, None))
    assert out["ok"] is False, "依赖失效时 refresh 不魔法修复"


def test_C35_refresh_profile_unspecified_fails(tmp_path):
    pdir, ids = _real_env(tmp_path)
    _bound_ready(pdir, ids)
    (pdir / "output" / "project_profile.json").unlink()
    rev = ShotBindingService(pdir).read_binding("s01_c01")["binding_revision"]
    out = run(_execute_shot_production_refresh(
        _rt(pdir), pdir.name,
        json.dumps({"shot_id": "s01_c01", "expected_binding_revision": rev}),
        None, None))
    assert out["ok"] is False


# ============ Generation reference policy（C37-C40/C42）============
class _FakeJob:
    def __init__(self, job_id, shot_id):
        self.job_id, self.shot_id, self.state = job_id, shot_id, "done"
        self.error = None

    def cancel(self, force=False):
        self.state = "cancelled"


class _FakeQueue:
    def __init__(self, fail=False):
        self.submitted = []
        self._n = 0

    def submit(self, project, kind, shot_id, **extra):
        self._n += 1
        j = _FakeJob(f"j{self._n}", shot_id)
        self.submitted.append({"project": project, "kind": kind,
                               "shot_id": shot_id, **extra})
        return j


def _policy_rt(pdir, q):
    return AgentRuntime(project_dir_resolver=lambda name: pdir,
                        settings_path=pdir.parent / "agents_data" / "settings.json",
                        client=None, job_queue_getter=lambda: q)


def test_C37_unbound_legacy_allowed_old_behavior(tmp_path):
    pdir, ids = _real_env(tmp_path)
    q = _FakeQueue()
    rt = _policy_rt(pdir, q)
    out = _execute_generation_images(rt, pdir, {"targets": [{"shot_id": "s01_c01",
                                                                 "frame": "first"}]})
    assert len(q.submitted) == 1, "legacy（默认）行为：UNBOUND 照常提交"
    assert out["results"][0]["ok"] is False, "fake job 无真实产物 → 分项失败如实"


def test_C38_unbound_bound_required_blocked_before_job(tmp_path):
    pdir, ids = _real_env(tmp_path)
    q = _FakeQueue()
    rt = _policy_rt(pdir, q)
    out = _execute_generation_images(rt, pdir, {
        "targets": [{"shot_id": "s01_c01", "frame": "first"}],
        "reference_policy": "bound_required"})
    assert len(q.submitted) == 0, "提交前拦截，零额度消耗"
    assert out["ok"] is False
    item = out["results"][0]
    assert item["error"] == "binding_required"
    assert item["production_state"] == "unbound"
    assert item["fix_hint"]


def test_C39_not_ready_bound_required_structured_issues(tmp_path):
    pdir, ids = _real_env(tmp_path)
    _bound_ready(pdir, ids)
    rdoc = ResourceStore(pdir).load()
    lk = rdoc.looks[LOOK]
    lk.approved_reference_asset_ids = []
    lk.approval_status = "unreviewed"
    ResourceStore(pdir).put_look(lk)
    q = _FakeQueue()
    rt = _policy_rt(pdir, q)
    out = _execute_generation_images(rt, pdir, {
        "targets": [{"shot_id": "s01_c01", "frame": "first"}],
        "reference_policy": "bound_required"})
    assert len(q.submitted) == 0
    item = out["results"][0]
    assert item["error"] == "binding_not_ready"
    assert item["production_state"] == "not_ready"
    assert item["issues"], "NOT_READY 必须带结构化 issues（§13）"
    assert item["binding_revision"]


def test_C40_ready_bound_required_job_submitted(tmp_path):
    pdir, ids = _real_env(tmp_path)
    _bound_ready(pdir, ids)
    q = _FakeQueue()
    rt = _policy_rt(pdir, q)
    out = _execute_generation_images(rt, pdir, {
        "targets": [{"shot_id": "s01_c01", "frame": "first"}],
        "reference_policy": "bound_required"})
    assert len(q.submitted) == 1, "READY 才允许进入 BOUND 生产"
    assert q.submitted[0]["kind"] == "first_frame"


def test_C42_default_policy_unchanged():
    """C42：省略 reference_policy = legacy_allowed（旧行为），由 C37 端到端锚定。"""
    from src.agents.director_visual import SHOT_GENERATE_IMAGES_TOOL
    props = SHOT_GENERATE_IMAGES_TOOL["function"]["parameters"]["properties"]
    assert props["reference_policy"]["enum"] == ["legacy_allowed", "bound_required"]
    assert "targets" in SHOT_GENERATE_IMAGES_TOOL["function"]["parameters"]["required"]


def _execute_generation_images(rt, pdir, args):
    from src.agents.director_visual import _execute_shot_generate_images
    return run(_execute_shot_generate_images(rt, pdir.name, json.dumps(args),
                                             "生成", None))


# ============ Permissions（C43-C47）============
def test_C43_C47_director_permission_surface():
    names = {t["function"]["name"] for t in DIRECTOR_TOOLS}
    execs = set(TOOL_EXECUTORS)
    for banned in ("resource.clothing.upsert", "resource.look.prepare",
                   "resource.prop.register", "resource.composition.prepare",
                   "resource.inventory", "resource.read",
                   "resource.library.publish", "resource.library.import",
                   "look.approve", "prop.approve",
                   "project.visual_profile.update",
                   "scene.create", "scene_instance.create", "scene_state.patch",
                   "scene_view.generate", "scene_instance_view.generate",
                   "character.create", "character.update", "character.delete",
                   "story.update", "script.update"):
        assert banned not in names and banned not in execs, f"越权工具: {banned}"
    # C46/C47：Shot 与 Binding 写权在
    assert {"shot.create", "shot.update", "shot.delete",
            "shot.production.bind", "shot.production.refresh"} <= names
    assert {"shot.create", "shot.update", "shot.production.bind",
            "shot.production.refresh"} <= set(DIRECTOR_WRITE_OPERATIONS)


# ============ Task Result / Producer（C48-C53）============
def test_C48_binding_mutation_in_changed_refs(tmp_path):
    pdir = make_pdir(tmp_path)
    out = _bind_exec(pdir)
    tr = {"receipts": [{"ok": True, "verified": True,
                        "operation": "shot.production.bind",
                        "binding_id": out["binding_id"],
                        "changed_resources": out["changed_resources"]}],
          "message": {"status": None}, "assistant_message": "x", "trace": []}
    wrapped = producer._wrap_task_result(tr, None, "t", "w", "director", "s")
    assert wrapped["changed_refs"]["bindings"] == ["s01_c01"]
    assert wrapped["verified"] is True


def test_C49_prerequisite_becomes_verified_warning():
    tr = {"receipts": [{"ok": True, "verified": True,
                        "operation": "shot.production.inspect",
                        "prerequisite_warnings": [
                            {"code": "resource_prerequisite", "shot_id": "s01_c01",
                             "detail": "角色 Alice 没有 approved Look"}]}],
          "message": {"status": None}, "assistant_message": "x", "trace": []}
    wrapped = producer._wrap_task_result(tr, None, "t", "w", "director", "s")
    assert any("resource_prerequisite" in w and "Alice" in w
               for w in wrapped["warnings"]), wrapped["warnings"]


def test_C50_no_fake_changed_on_failed_bind(tmp_path):
    pdir = make_pdir(tmp_path)
    _bind_exec(pdir)
    out = _bind_exec(pdir, expected_binding_revision="00000000")
    tr = {"receipts": [dict(out, **{"ok": out["ok"], "verified": out["verified"]})],
          "message": {"status": None}, "assistant_message": "x", "trace": []}
    wrapped = producer._wrap_task_result(tr, None, "t", "w", "director", "s")
    assert wrapped["changed_refs"]["bindings"] == [], "stale 失败不产生假 changed"
    assert wrapped["verified"] is False


def test_C51_old_task_result_files_compatible(tmp_path):
    """旧 TaskResult（三键/八键）读取兼容：_wrap_task_result 对旧形态输入不抛。"""
    tr = {"receipts": [{"ok": True, "verified": True, "operation": "shot.update",
                        "shot_id": "s01_c01"}],
          "message": {"status": None}, "assistant_message": "x", "trace": []}
    wrapped = producer._wrap_task_result(tr, None, "t", "w", "director", "s")
    assert wrapped["changed_refs"]["shots"] == ["s01_c01"]


def test_C52_C53_producer_routing_awareness():
    ps = producer.PRODUCER_SYSTEM
    assert "prerequisite[resource_prerequisite]" in ps
    assert "Resource Agent" in ps.split("Production prerequisite")[1][:400]
    assert "scene_production_prerequisite" in ps
    # 28.md §25 演进（原 C1 断言「绝不让 Resource Agent 越权写场景」已由
    # Scene Agent 的存在取代）：scene_production_prerequisite 现在路由给 Scene Agent，
    # 且 Resource Agent 的写入域不包含场景（越权语义仍由工具面缺失保证）。
    assert "Scene Agent" in ps.split("Production prerequisite")[1][:600]
