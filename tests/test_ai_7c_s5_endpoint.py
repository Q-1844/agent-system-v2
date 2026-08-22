# tests/test_ai_7c_s5_endpoint.py
# AI-7C-S S5-1：公开 HTTP endpoint 集成（B 级）。
# 从真实 FastAPI endpoint 进入（不直接调 execution_service 内部函数）。
# Agnes 只在 client/网络边界 mock（FakeAgnes + handler _download monkeypatch）；
# execution_service / router / character_views_handler 逻辑 / JobQueue 全真。
# 证明：POST execute→revalidation→commit→character_sync；GET manifest→affected scoping（M4）；
#      POST resume→kicked_off；poll GET→stage 推进 character_sync→production_planning + Character.views 再生；
#      无重复 execution。
import io
import time

import deps
from deps import get_project_manager
from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol.models import (UnifiedChangePlan, PlanPreviewArtifact,
                                                PlanApproval, ChangeSession)
from src.revision.models import ChangeOperation, BeforeAfter, creative_source_signature
from src.revision.changeset import preview_operations_on_project

PROJECT = "_test_7cs_s5"
CHAR = "LinXia"


# ============ FakeAgnes（client 边界 mock；不联网）============
class FakeAgnes:
    def __init__(self): self.calls = []

    async def chat(self, *a, **k):
        # 故意非 plan JSON -> production_planning 阶段真 Planner 会失败（expected，B 级只证端点 wiring 到 char_sync）
        return "not a plan json"

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        self.calls.append(prompt[:16])
        return {"url": "https://fake.agnes/char.png", "b64": None}

    async def create_video(self, *a, **k): raise RuntimeError("B 级不应调 video")
    async def poll_video(self, *a, **k): raise RuntimeError("B 级不应调 video")
    async def aclose(self): pass


def _real_png_bytes():
    """真 256x256 灰 png（rembg 可处理；非 fake 头-only png）。"""
    from PIL import Image
    img = Image.new("RGB", (256, 256), (140, 140, 140))
    buf = io.BytesIO(); img.save(buf, "PNG"); return buf.getvalue()


def _make_project(pname):
    pm = get_project_manager()
    import shutil
    shutil.rmtree(pm.base_dir / pname, ignore_errors=True)
    pdir = pm.create_project(pname)
    proj = Project(name=pname, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text="故事", characters=[Character(name=CHAR, description_en="a girl")],
                   scenes=[Scene(scene_id="s01", summary="meet", characters=[CHAR],
                                 shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                             description="look", characters=[CHAR],
                                             target_duration_s=5, link_mode="i2v")])])
    ProjectStore(pdir).save(proj)
    return pdir


def _seed_approved_change_plan(pdir):
    """落 approved ChangePlan（update_character description_en）+ preview。触发 character_sync。"""
    proj = ProjectStore(pdir).load()
    sig = creative_source_signature(proj.to_dict())
    ops = [ChangeOperation(op_type="update_character", character_name=CHAR,
                           description_en=BeforeAfter(before="a girl",
                                                      after="a girl with long black hair"))]
    candidate, _d, impact, _w = preview_operations_on_project(proj, ops)
    projected = creative_source_signature(candidate.to_dict())
    store = ChangeProtocolStore(pdir, PROJECT)
    store.save_session(ChangeSession(session_id="cs_s5", project_name=PROJECT,
                                     status="approved_for_execution",
                                     active_plan_id="plan_s5", plan_version=1,
                                     source_snapshot_signature=sig, schema_version=2,
                                     created_at=time.time(), updated_at=time.time()))
    plan = UnifiedChangePlan(plan_id="plan_s5", change_session_id="cs_s5", project_name=PROJECT,
                             version=1, source_snapshot_signature=sig,
                             status="approved_for_execution",
                             steps=[{"step_id": "step_ch", "module": "character",
                                     "owner_agent_id": "character_designer", "plan_item_ids": [],
                                     "depends_on_step_ids": [], "parallel_group": None}],
                             preview_id="pv_s5", created_at=time.time())
    plan.approval = PlanApproval(plan_id="plan_s5", plan_version=1, decision="approved",
                                 source_snapshot_signature=sig, decided_at=time.time()).to_dict()
    store.save_plan(plan)
    store.save_preview(PlanPreviewArtifact(
        preview_id="pv_s5", change_session_id="cs_s5", plan_id="plan_s5", plan_version=1,
        source_snapshot_signature=sig,
        module_operations=[{"step_id": "step_ch", "module": "character",
                            "owner_agent_id": "character_designer", "proposal_item_ids": [],
                            "operations": [ops[0].to_dict()]}],
        aggregate_impact=impact.to_dict(), projected_signature=projected,
        applyable=False, created_at=time.time()))
    return sig


def test_s5_1_execute_resume_endpoint_path(client, monkeypatch):
    """S5-1：公开 HTTP endpoint 链路（execute→commit→character_sync；resume→kicked_off；poll→stage 推进；
    Character.views 再生；无重复 execution）。Agnes 在 client/网络边界 mock，execution 路径全真。"""
    # mock Agnes 在 client 边界 + handler 网络下载边界（真 png 供 rembg）
    real_agnes = deps.agnes_client; real_rehost = deps._rehost_host
    fake = FakeAgnes()
    deps.agnes_client = fake
    deps._rehost_host = type("R", (), {"async upload": lambda self, p: "https://fake.rehost/x.png"})()
    from src.agents import character_views_handler as cvh
    async def _fake_download(url, proxy=None):
        return _real_png_bytes()   # 网络边界 mock：真 png（rembg 可处理），不经 HTTP
    monkeypatch.setattr(cvh, "_download", _fake_download)
    pdir = _make_project(PROJECT)
    _seed_approved_change_plan(pdir)
    try:
        # POST /execute
        r = client.post(f"/api/project/{PROJECT}/change-sessions/cs_s5/execute",
                        json={"plan_id": "plan_s5", "plan_version": 1,
                              "production_mode": "affected_production"})
        assert r.status_code == 200, r.text
        ex = r.json()["execution"]
        eid = ex["execution_id"]
        assert ex["canonical_commit"]["status"] == "committed", f"commit 未完成: {ex.get('blocker')}"
        assert ex["stage"] == "character_sync", f"execute 后应到 character_sync（{ex['stage']}）"

        # GET /manifest（M4 scoping 经 HTTP）：char regenerate + 受影响 scene s01
        rm = client.get(f"/api/project/{PROJECT}/change-sessions/cs_s5/executions/{eid}/manifest")
        assert rm.status_code == 200, rm.text
        m = rm.json()
        assert m["character_actions"].get(CHAR) == "regenerate"
        assert "s01" in m["planner_scene_ids"], f"manifest 应 scoping 到 s01（M4 via HTTP）: {m['planner_scene_ids']}"

        # POST /resume -> kicked_off（后台 kickoff；不创建重复 execution）
        r2 = client.post(f"/api/project/{PROJECT}/change-sessions/cs_s5/executions/{eid}/resume")
        assert r2.status_code == 200, r2.text
        # resume 返回 kicked_off（首次）或 idempotent（已在飞）—— 两者皆可，关键是推进
        assert "execution" in r2.json()

        # 轮询 GET /executions/{eid}：观察 stage 推进到 production_planning（char sync 完成经后台 task）
        # portal loop 在 time.sleep 期间推进 background task + JobQueue worker（_wait_run_terminal 同模式）
        deadline = time.time() + 90
        stage_seen = []
        final = None
        while time.time() < deadline:
            rg = client.get(f"/api/project/{PROJECT}/change-sessions/cs_s5/executions/{eid}")
            assert rg.status_code == 200, rg.text
            final = rg.json()
            stage_seen.append(final["stage"])
            # char sync 完成 -> production_planning（FakeAgnes Planner 会失败 -> 可能 blocked@production_planning）
            if final["stage"] in ("production_planning", "production_prepare", "done") or final["status"] == "blocked":
                break
            time.sleep(1.0)
        assert final is not None
        # stage 必须推进出 character_sync（多个状态）
        assert any(s == "production_planning" for s in stage_seen) or final["stage"] == "production_planning", \
            f"stage 未推进到 production_planning（seen={stage_seen}, final={final['stage']}/{final['status']})"

        # Character.views 经真 handler + FakeAgnes + rembg 再生（front/side/back done）
        ch = next(c for c in ProjectStore(pdir).load().characters if c.name == CHAR)
        assert set(ch.views.keys()) >= {"front", "side", "back"}, \
            f"Character.views 未再生全: {sorted(ch.views.keys())}（calls={fake.calls}）"
        for v in ("front", "side", "back"):
            assert ch.views[v].status == "done", f"{v} 未 done: {ch.views[v].status}"
        assert len(fake.calls) >= 3, f"应至少 3 次 generate_image（front/side/back），得 {len(fake.calls)}"

        # 无重复 execution（§16：一 plan_version 一 execution）
        rl = client.get(f"/api/project/{PROJECT}/change-sessions/cs_s5/executions").json()
        assert len(rl["executions"]) == 1, f"出现重复 execution: {[e['execution_id'] for e in rl['executions']]}"
    finally:
        deps.agnes_client = real_agnes; deps._rehost_host = real_rehost
        import shutil
        shutil.rmtree(get_project_manager().get_project_dir(PROJECT), ignore_errors=True)
