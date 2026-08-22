# tests/test_ai_7c_s5_soak.py
# AI-7C-S S5-2 D2 Soak（mocked Agnes，网络边界 mock）——攻击恢复边界 + 幂等 + 隔离，非压力循环。
# 复用 S5-1 的 FakeAgnes + 真 png + HTTP endpoint 模式；execution/router/handler/JobQueue 全真。
#   B：快速重复 Execute/Resume -> 恰好 1 execution + 恰好 1 组 front/side/back（3 次 generate_image），
#      不出现第二条 char-sync 链 / 双 commit / 双角色生成。
#   C：项目隔离——A/B 同角色名；A execution 只写 A；B canonical + views 不被串；仅 A 产生 3 次调用。
#   E：（见文件末 TODO，待 run_character_sync per-view 失败/重试细节确认后补——依赖恢复路径映射）
import io
import time

import deps
from deps import get_project_manager
from src.core.models import Project, Scene, Shot, Character, StyleAnchor, Asset, ImageAsset, VideoAsset
from src.core.store import ProjectStore
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol.models import (UnifiedChangePlan, PlanPreviewArtifact,
                                                PlanApproval, ChangeSession)
from src.revision.models import ChangeOperation, BeforeAfter, creative_source_signature
from src.revision.changeset import preview_operations_on_project


# ============ FakeAgnes（client 边界 mock；不联网）============
class FakeAgnes:
    """Agnes 在 client/网络边界 mock。generate_image 返回 fake url；handler _download 单独 mock 为真 png。"""

    def __init__(self):
        self.calls = []

    async def chat(self, *a, **k):
        # 非 plan JSON -> production_planning 真 Planner 会失败（expected；本层只证 char-sync 边界）
        return "not a plan json"

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                            ratio="16:9", seed=None, want_b64=False):
        self.calls.append(prompt[:20])
        return {"url": "https://fake.agnes/char.png", "b64": None}

    async def create_video(self, *a, **k):
        raise RuntimeError("soak 不应调 video")

    async def poll_video(self, *a, **k):
        raise RuntimeError("soak 不应调 video")

    async def aclose(self):
        pass


def _real_png_bytes():
    """真 256x256 灰 png（rembg 可处理；非 fake 头-only png）。"""
    from PIL import Image
    img = Image.new("RGB", (256, 256), (140, 140, 140))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _fake_rehost():
    return type("R", (), {"async upload": lambda self, p: "https://fake.rehost/x.png"})()


def _patch_download(monkeypatch):
    from src.agents import character_views_handler as cvh

    async def _fake_download(url, proxy=None):
        return _real_png_bytes()   # 网络边界 mock：真 png（rembg 可处理），不经 HTTP
    monkeypatch.setattr(cvh, "_download", _fake_download)


def _make_project(pname, char_name):
    """throwaway：1 char + 1 scene + 1 shot（i2v）。char 无 views（仅 description_en）。"""
    pm = get_project_manager()
    import shutil
    shutil.rmtree(pm.base_dir / pname, ignore_errors=True)
    pdir = pm.create_project(pname)
    proj = Project(name=pname, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事", characters=[Character(name=char_name,
                                                              description_en="a girl with short hair")],
                   scenes=[Scene(scene_id="s01", summary="meet", characters=[char_name],
                                 shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                             description="look", characters=[char_name],
                                             target_duration_s=5, link_mode="i2v")])])
    ProjectStore(pdir).save(proj)
    return pdir


def _seed_char_plan(pdir, pname, char_name, session_id, plan_id,
                    desc_before="a girl with short hair",
                    desc_after="a girl with long flowing black hair, green eyes"):
    """落 approved ChangePlan（update_character description_en）+ preview。触发 character_sync。"""
    proj = ProjectStore(pdir).load()
    sig = creative_source_signature(proj.to_dict())
    ops = [ChangeOperation(op_type="update_character", character_name=char_name,
                           description_en=BeforeAfter(before=desc_before, after=desc_after))]
    candidate, _d, impact, _w = preview_operations_on_project(proj, ops)
    projected = creative_source_signature(candidate.to_dict())
    store = ChangeProtocolStore(pdir, pname)
    store.save_session(ChangeSession(session_id=session_id, project_name=pname,
                                     status="approved_for_execution",
                                     active_plan_id=plan_id, plan_version=1,
                                     source_snapshot_signature=sig, schema_version=2,
                                     created_at=time.time(), updated_at=time.time()))
    plan = UnifiedChangePlan(plan_id=plan_id, change_session_id=session_id, project_name=pname,
                             version=1, source_snapshot_signature=sig,
                             status="approved_for_execution",
                             steps=[{"step_id": "step_ch", "module": "character",
                                     "owner_agent_id": "character_designer", "plan_item_ids": [],
                                     "depends_on_step_ids": [], "parallel_group": None}],
                             preview_id=f"pv_{plan_id}", created_at=time.time())
    plan.approval = PlanApproval(plan_id=plan_id, plan_version=1, decision="approved",
                                 source_snapshot_signature=sig, decided_at=time.time()).to_dict()
    store.save_plan(plan)
    store.save_preview(PlanPreviewArtifact(
        preview_id=f"pv_{plan_id}", change_session_id=session_id, plan_id=plan_id, plan_version=1,
        source_snapshot_signature=sig,
        module_operations=[{"step_id": "step_ch", "module": "character",
                            "owner_agent_id": "character_designer", "proposal_item_ids": [],
                            "operations": [ops[0].to_dict()]}],
        aggregate_impact=impact.to_dict(), projected_signature=projected,
        applyable=False, created_at=time.time()))
    return sig


def _wait_char_views_done(pdir, char_name, timeout=90):
    """轮询直到 char 三视图 front/side/back 全 done（char-sync 完成的持久证据）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        proj = ProjectStore(pdir).load()
        ch = next((c for c in proj.characters if c.name == char_name), None)
        if ch and set(ch.views.keys()) >= {"front", "side", "back"} \
                and all(v.status == "done" for v in ch.views.values()):
            return ch
        time.sleep(1.0)
    return ch


# ============ B：快速重复 Execute/Resume -> 不产生第二条 char-sync 链 ============
def test_soak_b_rapid_resume_no_duplicate_charsync(client, monkeypatch):
    """S5-2 B：execute 后连发 4 次 resume + 1 次重复 execute。
    不变量：恰好 1 execution；恰好 1 组 front/side/back（==3 次 generate_image，无重复 char-sync）；
    Character.views 各视图 done 一次；canonical commit 一次。"""
    real_agnes = deps.agnes_client
    real_rehost = deps._rehost_host
    fake = FakeAgnes()
    deps.agnes_client = fake
    deps._rehost_host = _fake_rehost()
    _patch_download(monkeypatch)
    pname = "_test_7cs_soakB"
    char = "LinXia"
    pdir = _make_project(pname, char)
    _seed_char_plan(pdir, pname, char, "cs_b", "plan_b")
    pm = get_project_manager()
    try:
        # 1) execute -> stage=character_sync（char-sync 尚未起；resume 才 kickoff）
        r = client.post(f"/api/project/{pname}/change-sessions/cs_b/execute",
                       json={"plan_id": "plan_b", "plan_version": 1,
                             "production_mode": "affected_production"})
        assert r.status_code == 200, r.text
        ex = r.json()["execution"]
        eid = ex["execution_id"]
        assert ex["canonical_commit"]["status"] == "committed", f"commit 未完成: {ex.get('blocker')}"
        assert ex["stage"] == "character_sync", ex["stage"]

        # 2) 重复 execute（幂等：同一 plan_version -> 同一 execution，非 409）
        r1b = client.post(f"/api/project/{pname}/change-sessions/cs_b/execute",
                         json={"plan_id": "plan_b", "plan_version": 1,
                               "production_mode": "affected_production"})
        assert r1b.status_code == 200, r1b.text
        assert r1b.json()["execution"]["execution_id"] == eid, "重复 execute 应返回同一 execution"

        # 3) 连发 4 次 resume（仅首次 kickoff char-sync；余下 _INFLIGHT_POSTCOMMIT 守卫 no-op）
        kickoffs = []
        for _ in range(4):
            rr = client.post(f"/api/project/{pname}/change-sessions/cs_b/executions/{eid}/resume")
            assert rr.status_code == 200, rr.text
            kickoffs.append(rr.json().get("kicked_off"))
        # 至少一次 kickoff（首次），其余 idempotent（kicked_off False/缺失）
        assert any(kickoffs), f"resume 从未 kickoff char-sync: {kickoffs}"

        # 4) 轮询 char-sync 完成（持久证据：三视图 done）
        ch = _wait_char_views_done(pdir, char)
        assert ch is not None and set(ch.views.keys()) >= {"front", "side", "back"}, \
            f"char-sync 未完成: views={sorted(ch.views.keys()) if ch else None} calls={fake.calls}"
        assert all(v.status == "done" for v in ch.views.values()), \
            {v: ch.views[v].status for v in ch.views}

        # 5) 核心不变量：恰好 1 组 front/side/back（3 次 generate_image）——证明无第二条 char-sync 链
        assert len(fake.calls) == 3, \
            f"重复 char-sync！期望 3 次 generate_image（front/side/back 一组），得 {len(fake.calls)}: {fake.calls}"

        # 6) 恰好 1 execution（§16）
        rl = client.get(f"/api/project/{pname}/change-sessions/cs_b/executions").json()
        assert len(rl["executions"]) == 1, \
            f"重复 execution: {[e['execution_id'] for e in rl['executions']]}"
        assert rl["executions"][0]["execution_id"] == eid
    finally:
        deps.agnes_client = real_agnes
        deps._rehost_host = real_rehost
        import shutil
        shutil.rmtree(pm.get_project_dir(pname), ignore_errors=True)


# ============ E：失败/重试一致性——永久失败 -> blocked -> 状态一致（无双 commit/无双 execution）============
class FailingFakeAgnes(FakeAgnes):
    """generate_image 永久失败（模拟内容审核拒绝/无 url）。不经 AgnesClient 重试（非 HTTP retriable）。"""

    def __init__(self, fail_after=0):
        super().__init__()
        self.fail_after = fail_after   # 前 N 次正常，之后永久失败

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                            ratio="16:9", seed=None, want_b64=False):
        self.calls.append(prompt[:20])
        if len(self.calls) > self.fail_after:
            raise RuntimeError("内容审核拒绝（永久失败，不可重试）")
        return {"url": "https://fake.agnes/char.png", "b64": None}


def _wait_blocked(client, pname, session, eid, timeout=60):
    """轮询直到 execution 进终态/blocked（char-sync 失败 -> 后台 task catch -> blocked）。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = client.get(f"/api/project/{pname}/change-sessions/{session}/executions/{eid}")
        last = r.json()
        if last["status"] in ("blocked", "done", "cancelled", "completed", "failed"):
            return last
        time.sleep(1.0)
    return last


def test_soak_e_char_sync_failure_consistent_blocked(client, monkeypatch):
    """S5-2 E：char-sync 永久失败 -> execution blocked，状态一致：
    canonical 仍 committed 一次（未被失败回滚/双 commit）；恰好 1 execution；
    blocker 正确；resume-on-blocked 不重 kickoff（无第二条链/无双 commit）；
    Character.views 未落半成品（handler 全有或全无 per char）。
    v1 限制：char-sync 永久失败 -> blocked 无自动 retry（需重新 plan）；本测试据实断言该边界。"""
    real_agnes = deps.agnes_client
    real_rehost = deps._rehost_host
    fake = FailingFakeAgnes(fail_after=0)   # 第 1 次起永久失败
    deps.agnes_client = fake
    deps._rehost_host = _fake_rehost()
    _patch_download(monkeypatch)
    pname = "_test_7cs_soakE"
    char = "LinXia"
    pdir = _make_project(pname, char)
    _seed_char_plan(pdir, pname, char, "cs_e", "plan_e")
    pm = get_project_manager()
    try:
        # execute -> commit 成功（canonical committed）-> stage=character_sync
        r = client.post(f"/api/project/{pname}/change-sessions/cs_e/execute",
                        json={"plan_id": "plan_e", "plan_version": 1,
                              "production_mode": "affected_production"})
        assert r.status_code == 200, r.text
        ex = r.json()["execution"]
        eid = ex["execution_id"]
        assert ex["canonical_commit"]["status"] == "committed", "commit 应成功（在 char-sync 前）"
        commit_sig = ex["canonical_commit"].get("expected_after_signature")

        # resume -> kickoff char-sync -> handler 永久失败 -> execution blocked
        rr = client.post(f"/api/project/{pname}/change-sessions/cs_e/executions/{eid}/resume")
        assert rr.status_code == 200, rr.text
        assert rr.json().get("kicked_off"), "首次 resume 应 kickoff char-sync"
        final = _wait_blocked(client, pname, "cs_e", eid)
        assert final is not None and final["status"] == "blocked", \
            f"char-sync 失败应 -> blocked，得 status={final['status'] if final else None} stage={final.get('stage') if final else None}"
        blocker = final.get("blocker") or {}
        assert blocker.get("code"), f"blocker 应有 code: {blocker}"

        # 一致性：canonical 仍 committed 一次（失败未回滚/未双 commit）
        assert final["canonical_commit"]["status"] == "committed"
        assert final["canonical_commit"].get("expected_after_signature") == commit_sig, \
            "canonical 签名不应被 char-sync 失败改动"

        # 恰好 1 execution
        rl = client.get(f"/api/project/{pname}/change-sessions/cs_e/executions").json()
        assert len(rl["executions"]) == 1, "失败不应产生重复 execution"

        # resume-on-blocked：不重 kickoff（§81 blocked 不可 rebasing resume）
        r2 = client.post(f"/api/project/{pname}/change-sessions/cs_e/executions/{eid}/resume")
        assert r2.status_code == 200, r2.text
        r2j = r2.json()
        assert r2j.get("blocked") is True or r2j.get("idempotent") is True, \
            "blocked execution resume 应返回 blocked/idempotent，不重 kickoff"
        assert not r2j.get("kicked_off"), "blocked resume 不应重 kickoff（无双链）"

        # 仍是 1 execution，canonical 仍 committed 一次
        rl2 = client.get(f"/api/project/{pname}/change-sessions/cs_e/executions").json()
        assert len(rl2["executions"]) == 1
        assert rl2["executions"][0]["canonical_commit"]["status"] == "committed"

        # Character.views 未落半成品（handler 全有或全无 per char：3 视图全成后才写回）
        ch = next((c for c in ProjectStore(pdir).load().characters if c.name == char), None)
        assert ch is not None and ch.views == {}, \
            f"失败不应落半成品 views: {ch.views if ch else None}"
    finally:
        deps.agnes_client = real_agnes
        deps._rehost_host = real_rehost
        import shutil
        shutil.rmtree(pm.get_project_dir(pname), ignore_errors=True)


# ============ C：项目隔离——A/B 同角色名，A execution 不串 B ============
def test_soak_c_project_isolation(client, monkeypatch):
    """S5-2 C：项目 A/B 同角色名 "LinXia"。A 的 char-sync（real handler）只写 A；
    B 的 canonical 签名不变 + char views 仍空 + 不产生 B 的调用。仅 A 3 次 generate_image。"""
    real_agnes = deps.agnes_client
    real_rehost = deps._rehost_host
    fake = FakeAgnes()
    deps.agnes_client = fake
    deps._rehost_host = _fake_rehost()
    _patch_download(monkeypatch)
    pA = _make_project("_test_7cs_isoA", "LinXia")
    pB = _make_project("_test_7cs_isoB", "LinXia")   # 同名角色，不同项目
    _seed_char_plan(pA, "_test_7cs_isoA", "LinXia", "cs_a", "plan_a")
    projB_before = ProjectStore(pB).load()
    sigB_before = creative_source_signature(projB_before.to_dict())
    pm = get_project_manager()
    try:
        # A execute + resume -> char-sync 跑 A 的 LinXia 三视图
        r = client.post("/api/project/_test_7cs_isoA/change-sessions/cs_a/execute",
                        json={"plan_id": "plan_a", "plan_version": 1,
                              "production_mode": "affected_production"})
        assert r.status_code == 200, r.text
        eid = r.json()["execution"]["execution_id"]
        client.post(f"/api/project/_test_7cs_isoA/change-sessions/cs_a/executions/{eid}/resume")

        chA = _wait_char_views_done(pA, "LinXia")
        assert chA is not None and set(chA.views.keys()) >= {"front", "side", "back"}, \
            f"A char-sync 未完成: {chA.views if chA else None}"
        assert all(v.status == "done" for v in chA.views.values())

        # B 不被串：canonical 签名不变 + char views 仍空
        projB_after = ProjectStore(pB).load()
        assert creative_source_signature(projB_after.to_dict()) == sigB_before, \
            "B canonical 被串（A execution 写到了 B）"
        chB = next((c for c in projB_after.characters if c.name == "LinXia"), None)
        assert chB is not None and chB.views == {}, \
            f"B 同名角色 views 被串: {chB.views if chB else 'no char'}"

        # 仅 A 产生 3 次 generate_image（B 0 次）
        assert len(fake.calls) == 3, \
            f"应仅 A 3 次调用，得 {len(fake.calls)}（B 被串生成？）: {fake.calls}"
    finally:
        deps.agnes_client = real_agnes
        deps._rehost_host = real_rehost
        import shutil
        shutil.rmtree(pm.get_project_dir("_test_7cs_isoA"), ignore_errors=True)
        shutil.rmtree(pm.get_project_dir("_test_7cs_isoB"), ignore_errors=True)


# ============ D：角色重生保留旧生产资产（char-sync 只写 Character.views，不级联删 shot 资产）============
def _make_project_with_old_assets(pname, char_name):
    """throwaway：char 无 views（使 char-sync 真再生）+ shot 已有旧 first_frame/video/final_video。
    v1 注：update_character 只改 description_en 不碰 views（compilers.py §20）；handler done_views
    跳过已 done view —— 故 char 已有 done views 时 char-sync 不再生（coarse v1，本轮不改，报告记录）。
    本 setup 用无 views char 让再生发生，以证"再生时不级联删旧 shot 资产"。"""
    pm = get_project_manager()
    import shutil
    shutil.rmtree(pm.base_dir / pname, ignore_errors=True)
    pdir = pm.create_project(pname)
    old_ff = "https://old/ff.png"
    old_vid = "vid_old_7cD"
    old_final = "projects/old_final_d.mp4"
    proj = Project(name=pname, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事",
                   characters=[Character(name=char_name,
                                         description_en="a girl with short hair")],   # 无 views
                   scenes=[Scene(scene_id="s01", summary="meet", characters=[char_name],
                                 shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                             description="look", characters=[char_name],
                                             target_duration_s=5, link_mode="i2v",
                                             first_frame=ImageAsset(
                                                 candidates=[Asset(url=old_ff, status="done")],
                                                 selected_index=0, status="done"),
                                             video=VideoAsset(video_id=old_vid, url="https://old/vid.mp4",
                                                              status="done"),
                                             final_video=old_final)])])
    ProjectStore(pdir).save(proj)
    return pdir, old_ff, old_vid, old_final


def test_soak_d_char_sync_preserves_old_shot_assets(client, monkeypatch):
    """S5-2 D：shot 已有旧 first_frame/video/final_video；char 无 views。
    修改外观 -> char-sync（FakeAgnes 再生 views）-> 确认：views 再生为新 url；旧 first_frame/video/
    final_video 不被删/覆盖（character_views_handler 只写 Character.views line 99，不碰 shot 资产）。"""
    real_agnes = deps.agnes_client
    real_rehost = deps._rehost_host
    fake = FakeAgnes()
    deps.agnes_client = fake
    deps._rehost_host = _fake_rehost()
    _patch_download(monkeypatch)
    pname = "_test_7cs_soakD"
    char = "LinXia"
    pdir, old_ff, old_vid, old_final = _make_project_with_old_assets(pname, char)
    _seed_char_plan(pdir, pname, char, "cs_d", "plan_d")   # before="a girl with short hair" 与 seed 一致
    pm = get_project_manager()
    try:
        r = client.post(f"/api/project/{pname}/change-sessions/cs_d/execute",
                        json={"plan_id": "plan_d", "plan_version": 1,
                              "production_mode": "affected_production"})
        assert r.status_code == 200, r.text
        ex = r.json()["execution"]
        eid = ex["execution_id"]
        assert ex["canonical_commit"]["status"] == "committed", "commit 应成功（char-sync 前）"
        client.post(f"/api/project/{pname}/change-sessions/cs_d/executions/{eid}/resume")
        ch = _wait_char_views_done(pdir, char)
        assert ch is not None and set(ch.views.keys()) >= {"front", "side", "back"}, \
            f"char-sync 未完成: {ch.views if ch else None}"
        # views 再生为新（fake）url
        assert all(v.url == "https://fake.agnes/char.png" for v in ch.views.values()), \
            {v: ch.views[v].url for v in ch.views}

        # 旧 shot 生产资产不被删/覆盖（char-sync 只写 Character.views，不级联删 shot）
        sh = ProjectStore(pdir).load().get_shot("s01_c01")
        assert sh is not None, "shot 不应被删"
        assert sh.video.video_id == old_vid, f"旧 video_id 被覆盖: {sh.video.video_id}"
        assert sh.video.status == "done", f"旧 video status 被改: {sh.video.status}"
        assert sh.final_video == old_final, f"旧 final_video 被删/改: {sh.final_video}"
        ff = sh.first_frame
        assert ff.status == "done" and ff.candidates and ff.candidates[0].url == old_ff, \
            f"旧 first_frame 被删/改: status={ff.status} cands={[c.url for c in (ff.candidates or [])]}"
        # 仅 3 次（views 再生一组；shot 资产未被 char-sync 触及 -> 不增调用）
        assert len(fake.calls) == 3, f"应仅 3 次 char views 调用，得 {len(fake.calls)}（shot 被串生成？）"
    finally:
        deps.agnes_client = real_agnes
        deps._rehost_host = real_rehost
        import shutil
        shutil.rmtree(pm.get_project_dir(pname), ignore_errors=True)
