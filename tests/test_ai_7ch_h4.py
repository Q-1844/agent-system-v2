# tests/test_ai_7ch_h4.py
# AI-7C-H §34-A 确定性测试：F-1 签名语义 / K1 appearance_sig 重生成 / F-2 per-scene
# planning checkpoint（planner call-count 证明）/ Event B 启动自动 kickoff。
#
# 全部 deterministic：Agnes/segmentor/下载在边界 mock，0 网络调用。
import asyncio
import json
import time

import pytest

import deps
from src.core.models import Project, Scene, Shot, Character, StyleAnchor, Asset
from src.core.store import ProjectStore
from src.core.job_queue import Job
from src.agents.runtime import AgentRuntime
from src.agents import character_views_handler as cvh
from src.agents.character_views_handler import (
    character_appearance_sig, handler_character_views)
from src.agents.change_protocol import execution_service as cpx
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol.models import (UnifiedChangePlan, PlanPreviewArtifact,
                                               PlanApproval, ChangeSession)
from src.agents.change_protocol.execution_models import ChangeExecutionSession
from src.revision.models import ChangeOperation, BeforeAfter, creative_source_signature
from src.revision.changeset import preview_operations_on_project


# ============ helpers ============
def _proj_2scenes(pname="demo", char="Xiaoyu"):
    """1 char（出现在 2 scene）+ 2 scene 各 1 shot —— planner_scene_ids=[s01,s02]。"""
    return Project(
        name=pname, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
        characters=[Character(name=char, description_en="a girl with short hair")],
        scenes=[
            Scene(scene_id="s01", summary="meet", characters=[char],
                  shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                              description="see", characters=[char],
                              target_duration_s=4, link_mode="i2v")]),
            Scene(scene_id="s02", summary="hangar", characters=[char],
                  shots=[Shot(shot_id="s02_c01", scene_id="s02", index=1,
                              description="enter", characters=[char],
                              target_duration_s=4, link_mode="i2v")]),
        ],
        story_text="once upon a time")


class _NoCall:
    def __init__(self):
        self.calls = 0

    async def chat(self, *a, **k):
        self.calls += 1
        raise AssertionError("此路径不应调 Agnes chat")


class _FakeGenAgnes:
    """K1 handler 测试：记录 generate_image 调用，返回可区分 url。"""

    def __init__(self):
        self.calls = []

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        self.calls.append(prompt[:24])
        return {"url": f"https://fake/{len(self.calls)}.png", "b64": None}

    async def aclose(self):
        pass


class _FakeSeg:
    def remove_background(self, raw):
        return b"\x89PNG-fake-seg", {}


async def _emit(_stage, _msg, _pct=None):
    pass


def _seed_approved_plan(pdir, pname, char, session_id, plan_id,
                        desc_after="a girl with long flowing black hair, green eyes"):
    proj = ProjectStore(pdir).load()
    sig = creative_source_signature(proj.to_dict())
    ops = [ChangeOperation(op_type="update_character", character_name=char,
                           description_en=BeforeAfter(before="a girl with short hair",
                                                       after=desc_after))]
    candidate, _d, impact, _w = preview_operations_on_project(proj, ops)
    projected = creative_source_signature(candidate.to_dict())
    store = ChangeProtocolStore(pdir, pname)
    store.save_session(ChangeSession(session_id=session_id, project_name=pname,
                                      status="approved_for_execution",
                                      active_plan_id=plan_id, plan_version=1,
                                      source_snapshot_signature=sig, schema_version=2,
                                      created_at=1.0, updated_at=1.0))
    plan = UnifiedChangePlan(plan_id=plan_id, change_session_id=session_id,
                             project_name=pname, version=1,
                             source_snapshot_signature=sig,
                             status="approved_for_execution",
                             steps=[{"step_id": "step_ch", "module": "character",
                                     "owner_agent_id": "character_designer",
                                     "plan_item_ids": [], "depends_on_step_ids": [],
                                     "parallel_group": None}],
                             preview_id=f"pv_{plan_id}", created_at=1.0)
    plan.approval = PlanApproval(plan_id=plan_id, plan_version=1, decision="approved",
                                 source_snapshot_signature=sig, decided_at=2.0).to_dict()
    store.save_plan(plan)
    store.save_preview(PlanPreviewArtifact(
        preview_id=f"pv_{plan_id}", change_session_id=session_id, plan_id=plan_id,
        plan_version=1, source_snapshot_signature=sig,
        module_operations=[{"step_id": "step_ch", "module": "character",
                            "owner_agent_id": "character_designer",
                            "proposal_item_ids": [], "operations": [ops[0].to_dict()]}],
        aggregate_impact=impact.to_dict(), projected_signature=projected,
        applyable=False, created_at=1.0))
    return sig


def _to_planning_stage(tmp_path, pname="demo", char="Xiaoyu"):
    """commit + char-sync（fake views）-> stage=production_planning（manifest 2 scenes）。"""
    pdir = tmp_path
    ProjectStore(pdir).save(_proj_2scenes(pname, char))
    nc = _NoCall()
    rt = AgentRuntime(project_dir_resolver=lambda name: pdir,
                      settings_path=pdir / "agents_data" / "settings.json", client=nc)
    _seed_approved_plan(pdir, pname, char, "cs_h", "plan_h")

    async def _fake_view_gen(char_name):
        return {v: Asset(url=f"https://fake/{char_name}/{v}.png", status="done")
                for v in ("front", "side", "back")}

    asyncio.run(cpx.start_replay_commit(rt, pname, "cs_h", "plan_h", 1,
                                         "affected_production"))
    ex0 = ChangeProtocolStore(pdir, pname).find_execution("plan_h", 1)
    asyncio.run(cpx.run_character_sync(rt, pname, ex0.execution_id,
                                       view_generator=_fake_view_gen))
    ex = ChangeProtocolStore(pdir, pname).find_execution("plan_h", 1)
    assert ex.stage == "production_planning", ex.stage
    return rt, ex


# ============ ① F-1：sanctioned views 写回不改创作签名；真实创作突变仍改 ============
def test_f1_views_writeback_keeps_signature_external_mutation_changes():
    proj = _proj_2scenes()
    sig1 = creative_source_signature(proj.to_dict())
    # sanctioned production writeback：character sync 写 front/side/back views
    for v in ("front", "side", "back"):
        proj.characters[0].views[v] = Asset(url=f"https://fake/{v}.png",
                                            local=f"output/characters/Xiaoyu/{v}_seg.png",
                                            status="done")
    assert creative_source_signature(proj.to_dict()) == sig1, \
        "F-1：生产写回的 views 不得改变 creative_source_signature（重启误报 stale）"
    # 真实外部创作突变：description_en 变 -> 签名必须变（stale 保护仍在）
    proj.characters[0].description_en = "silver short hair, black armor"
    assert creative_source_signature(proj.to_dict()) != sig1, \
        "F-1：description_en 突变后签名必须变化（真实 stale 保护不降级）"


# ============ ② K1 纯函数：appearance_sig 确定性 ============
def test_k1_appearance_sig_deterministic_and_sensitive():
    s1 = character_appearance_sig("a girl with short hair", "anime")
    s2 = character_appearance_sig("a girl with short hair", "anime")
    assert s1 == s2 and len(s1) == 64, "确定性哈希"
    assert character_appearance_sig("a girl with short hair", "anime ") == s1, \
        "尾随空白不参与（strip 语义）"
    assert character_appearance_sig("silver short hair", "anime") != s1, "外观变 -> sig 变"
    assert character_appearance_sig("a girl with short hair", "realistic") != s1, \
        "风格变 -> sig 变（art_style 参与每张 view 生成）"


# ============ ③④ K1 handler：resume 跳过 / 新外观修订全重生成 ============
def _run_views_handler(monkeypatch, tmp_path, char_views, meta_sig, job_sig, char="Xiaoyu"):
    """构造项目 + 预置 views/views_meta，跑 handler_character_views，返回 (fake, project_dir)。"""
    pname = "_test_7ch_k1"
    pdir = tmp_path / pname
    proj = Project(name=pname, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   characters=[Character(name=char, description_en="a girl with short hair",
                                         views=char_views)],
                   scenes=[Scene(scene_id="s01", summary="m", characters=[char], shots=[])],
                   story_text="s")
    ProjectStore(pdir).save(proj)
    if meta_sig is not None:
        d = pdir / "output" / "characters" / char / "views_meta.json"
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_text(json.dumps({"appearance_sig": meta_sig,
                                 "views": {}, "updated_at": time.time()}),
                     encoding="utf-8")
    fake = _FakeGenAgnes()
    monkeypatch.setattr(deps, "agnes_client", fake, raising=False)
    monkeypatch.setattr(deps, "get_project_manager",
                        lambda: type("M", (), {"get_project_dir": staticmethod(
                            lambda name: pdir)})())
    import routers.characters as rc
    monkeypatch.setattr(rc, "_get_segmentor", lambda: _FakeSeg())

    async def _dl(url, proxy=None):
        return b"rawbytes"

    monkeypatch.setattr(cvh, "_download", _dl)
    job = Job(job_id="j_k1", project=pname, kind="character_views",
              params={"character_name": char, "appearance_sig": job_sig})
    asyncio.run(handler_character_views(job, _emit))
    return fake, pdir


def test_k1_resume_same_sig_skips_done_views(monkeypatch, tmp_path):
    """K1：appearance_sig 相同（resume 同一次生成）-> done views 全部跳过，0 次 generate。"""
    views = {v: Asset(url=f"https://old/{v}.png", local=f"old/{v}_seg.png", status="done")
             for v in ("front", "side", "back")}
    sig = character_appearance_sig("a girl with short hair", "anime")
    fake, _ = _run_views_handler(monkeypatch, tmp_path, views, meta_sig=sig, job_sig=sig)
    assert fake.calls == [], f"resume 同 sig 应 0 次生成，得 {fake.calls}"
    # views 保持 done（未被覆盖为 pending）
    proj = ProjectStore(tmp_path / "_test_7ch_k1").load()
    assert all(a.status == "done" for a in proj.characters[0].views.values())


def test_k1_new_appearance_sig_regenerates_all_views(monkeypatch, tmp_path):
    """K1：appearance_sig 不同（新批准外观修订）-> 旧 done views 是陈旧输入，必须真实重生成。
    验证：3 次生成、新 views lineage、views_meta 更新为新 sig、版本化文件落盘。"""
    views = {v: Asset(url=f"https://old/{v}.png", local=f"old/{v}_seg.png", status="done")
             for v in ("front", "side", "back")}
    old_sig = character_appearance_sig("a girl with short hair", "anime")
    new_sig = character_appearance_sig("silver short hair, black armor", "anime")
    assert new_sig != old_sig
    fake, pdir = _run_views_handler(monkeypatch, tmp_path, views,
                                    meta_sig=old_sig, job_sig=new_sig)
    assert len(fake.calls) == 3, \
        f"新外观修订必须全量重生成（front/side/back），得 {len(fake.calls)}: {fake.calls}"
    # 新 views lineage（url 与旧不同）
    proj = ProjectStore(pdir).load()
    ch = proj.characters[0]
    for v in ("front", "side", "back"):
        assert ch.views[v].url != f"https://old/{v}.png", f"{v} 未重生成"
        assert ch.views[v].status == "done"
    # views_meta 已更新为新 sig
    meta = json.loads((pdir / "output" / "characters" / "Xiaoyu" / "views_meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["appearance_sig"] == new_sig, "views_meta 必须记录新外观签名"
    # K1 版本化文件：{view}_{sig8}_seg.png（历史资产保留语义由不同 sig 不覆盖同名保证）
    seg_dir = pdir / "output" / "characters" / "Xiaoyu"
    for v in ("front", "side", "back"):
        assert (seg_dir / f"{v}_{new_sig[:8]}_seg.png").exists(), \
            f"{v} 版本化落盘缺失"


def test_k1_no_meta_with_sig_regenerates(monkeypatch, tmp_path):
    """K1：有 done views 但无 views_meta（历史项目/K1 前数据）+ 明确 appearance_sig
    -> 视为陈旧输入，重生成（不能因缺 meta 而永远卡在旧外观）。"""
    views = {v: Asset(url=f"https://old/{v}.png", local=f"old/{v}_seg.png", status="done")
             for v in ("front", "side", "back")}
    sig = character_appearance_sig("a girl with short hair", "anime")
    fake, _ = _run_views_handler(monkeypatch, tmp_path, views, meta_sig=None, job_sig=sig)
    assert len(fake.calls) == 3, f"缺 meta + 明确 sig 应重生成，得 {fake.calls}"


def test_k1_legacy_call_no_sig_keeps_old_skip(monkeypatch, tmp_path):
    """K1 兼容：appearance_sig 缺失（旧调用方）-> 保持旧行为 done views 跳过。"""
    views = {v: Asset(url=f"https://old/{v}.png", local=f"old/{v}_seg.png", status="done")
             for v in ("front", "side", "back")}
    fake, _ = _run_views_handler(monkeypatch, tmp_path, views,
                                 meta_sig=None, job_sig="")
    assert fake.calls == [], "无 sig 的旧调用应保持 done-skip 行为"


# ============ ⑤ F-2：per-scene planning checkpoint（planner call-count 证明）============
def test_f2_per_scene_checkpoint_no_duplicate_planner_on_reentry(tmp_path):
    """§21 必备场景：2 scene，s01 planner 完成 -> s02 planner 崩溃 -> 重入。
    证明：s01 planner 恰好调用 1 次（checkpoint 复用），s02 续跑，终态正确。"""
    rt, ex = _to_planning_stage(tmp_path)
    pname, pdir = "demo", tmp_path
    calls = []

    async def creator_crash_on_s02(scene_id):
        calls.append(scene_id)
        if scene_id == "s02":
            raise RuntimeError("simulated crash during s02 planning")
        return {"plan_id": f"plan_{scene_id}", "scene_id": scene_id,
                "status": "approved", "shot_ids": [f"{scene_id}_c01"]}

    with pytest.raises(RuntimeError):
        asyncio.run(cpx.run_production_planning(rt, pname, ex.execution_id,
                                                plan_creator=creator_crash_on_s02))
    assert calls == ["s01", "s02"], "第一轮：s01 完成 + s02 崩溃"
    # checkpoint 持久化：ex.planning 已含 s01
    ex_mid = ChangeProtocolStore(pdir, pname).find_execution("plan_h", 1)
    assert [p["scene_id"] for p in ex_mid.planning] == ["s01"]
    assert ex_mid.planning[0]["status"] == "approved"

    # ---- 重入（模拟重启后 resume）----
    async def creator_ok(scene_id):
        calls.append(scene_id)
        return {"plan_id": f"plan_{scene_id}", "scene_id": scene_id,
                "status": "approved", "shot_ids": [f"{scene_id}_c01"]}

    started = []

    async def starter(manifest):
        started.append(list(manifest.planner_scene_ids))
        return {"run_id": "prun_f2"}

    res = asyncio.run(cpx.run_production_planning(rt, pname, ex.execution_id,
                                                  plan_creator=creator_ok,
                                                  run_starter=starter))
    # 核心：s01 不再调用（planner call count s01 == 1），只续跑 s02
    assert calls == ["s01", "s02", "s02"], \
        f"重入不得重复 s01 planner，得 {calls}"
    assert calls.count("s01") == 1, "F-2：s01 planner 恰好 1 次（§21 call-count 证明）"
    ex_final = ChangeProtocolStore(pdir, pname).find_execution("plan_h", 1)
    assert ex_final.stage == "production_prepare"
    assert ex_final.production_run_id == "prun_f2"
    assert started == [["s01", "s02"]], "targeted run 覆盖全部受影响 scene"
    # planning 记录完整（s01 checkpoint 保留 + s02 补齐），不重复
    ids = [p["scene_id"] for p in ex_final.planning]
    assert ids == ["s01", "s02"], f"planning 记录 {ids}"
    assert res["planned_scenes"] == 2


# ============ ⑥ Event B：recover_executions 启动自动 kickoff ============
def test_event_b_recover_kickoffs_postcommit_only(monkeypatch, tmp_path):
    """Event B（§27）：后端重启 -> post-commit 活跃执行自动续跑；
    blocked/终态/pre-commit 不 kickoff。"""
    pname = "demo"
    store = ChangeProtocolStore(tmp_path, pname)

    def _ex(eid, status, stage):
        return ChangeExecutionSession(execution_id=eid, project_name=pname,
                                      change_session_id="cs_h", plan_id="plan_h",
                                      plan_version=1, status=status, stage=stage,
                                      created_at=1.0, updated_at=1.0)

    cases = [
        _ex("ex_sync", "running", "character_sync"),          # -> kickoff
        _ex("ex_plan", "running", "production_planning"),     # -> kickoff
        _ex("ex_blocked", "blocked", "character_sync"),       # -> 不
        _ex("ex_done", "completed", "production_prepare"),    # -> 不（terminal）
        _ex("ex_failed", "failed", "character_sync"),         # -> 不（terminal）
        _ex("ex_precommit", "running", "canonical_commit"),   # -> 不（pre-commit gate 管）
    ]
    for c in cases:
        store.save_execution(c)

    kicked = []
    monkeypatch.setattr(cpx, "_maybe_kickoff_postcommit",
                        lambda rt, project, ex: kicked.append(ex.execution_id) or True)
    rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                      settings_path=tmp_path / "agents_data" / "settings.json",
                      client=_NoCall())
    n = cpx.recover_executions(tmp_path, pname, rt=rt)
    assert n >= 1, "发现 active execution"
    assert sorted(kicked) == ["ex_plan", "ex_sync"], \
        f"只 kickoff post-commit 活跃执行，得 {kicked}"


def test_event_b_inflight_guard_prevents_double_kickoff(tmp_path):
    """Event B 守卫：同一 execution 已在飞（_INFLIGHT_POSTCOMMIT）-> 不二次派发
    （真实 _maybe_kickoff_postcommit 的 in-flight 分支，非 mock）。"""
    pname = "demo"
    store = ChangeProtocolStore(tmp_path, pname)
    store.save_execution(ChangeExecutionSession(
        execution_id="ex_x", project_name=pname, change_session_id="cs_h",
        plan_id="plan_h", plan_version=1, status="running", stage="character_sync",
        created_at=1.0, updated_at=1.0))
    rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                      settings_path=tmp_path / "agents_data" / "settings.json",
                      client=_NoCall())
    ex = store.get_execution("ex_x")

    async def _check():
        cpx._INFLIGHT_POSTCOMMIT["ex_x"] = object()   # 模拟已在飞
        try:
            assert cpx._maybe_kickoff_postcommit(rt, pname, ex) is False, \
                "in-flight 执行不得二次 kickoff"
        finally:
            cpx._INFLIGHT_POSTCOMMIT.pop("ex_x", None)

    asyncio.run(_check())


# ============ ⑦ F-1/Event B soak：双角色 char-sync 中途崩溃重启（§17 必备场景）============
class _TwoCharFakeAgnes:
    """A 正常生成；可选挂起 B（模拟崩溃窗口：A 完成、B 进行中）。
    串行 char-sync 下 calls[0:3]=A 的 front/side/back，calls[3:]=B。"""

    def __init__(self, hang_from_index=None):
        self.calls = []
        self.hang_from = hang_from_index   # 第 N 次调用起挂起（60s）

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        idx = len(self.calls)
        self.calls.append(prompt[:20])
        if self.hang_from is not None and idx >= self.hang_from:
            await asyncio.sleep(60)   # 崩溃窗口：B 生成中
        return {"url": f"https://fake/{idx}.png", "b64": None}

    async def chat(self, *a, **k):
        return "not a plan json"

    async def aclose(self):
        pass


def _seed_2char_project(pdir, pname):
    """2 char（A/B 均有旧 done views + views_meta 旧 sig）+ 1 scene + approved 2-op plan。"""
    char_a, char_b = "LinXia", "Aria"
    old_views = {v: Asset(url=f"https://old/{v}.png", local=f"old/{v}_seg.png",
                          status="done") for v in ("front", "side", "back")}
    proj = Project(
        name=pname, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
        characters=[
            Character(name=char_a, description_en="a girl with short hair",
                      views=dict(old_views)),
            Character(name=char_b, description_en="a calm ranger in green cloak",
                      views=dict(old_views)),
        ],
        scenes=[Scene(scene_id="s01", summary="meet", characters=[char_a, char_b],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="see", characters=[char_a, char_b],
                                  target_duration_s=4, link_mode="i2v")])],
        story_text="once upon a time")
    ProjectStore(pdir).save(proj)
    # 旧外观 views_meta（K1：新批准外观 sig 不同 -> 必须重生成）
    for cn, desc in ((char_a, "a girl with short hair"),
                     (char_b, "a calm ranger in green cloak")):
        d = pdir / "output" / "characters" / cn / "views_meta.json"
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_text(json.dumps(
            {"appearance_sig": character_appearance_sig(desc, "anime"),
             "views": {}, "updated_at": time.time()}), encoding="utf-8")
    # approved plan：A、B 都换新外观
    src = ProjectStore(pdir).load()
    sig = creative_source_signature(src.to_dict())
    ops = [
        ChangeOperation(op_type="update_character", character_name=char_a,
                        description_en=BeforeAfter(
                            before="a girl with short hair",
                            after="a girl with silver hair and black armor")),
        ChangeOperation(op_type="update_character", character_name=char_b,
                        description_en=BeforeAfter(
                            before="a calm ranger in green cloak",
                            after="a calm ranger in silver cloak")),
    ]
    candidate, _d, impact, _w = preview_operations_on_project(src, ops)
    projected = creative_source_signature(candidate.to_dict())
    store = ChangeProtocolStore(pdir, pname)
    store.save_session(ChangeSession(session_id="cs_soak", project_name=pname,
                                      status="approved_for_execution",
                                      active_plan_id="plan_soak", plan_version=1,
                                      source_snapshot_signature=sig, schema_version=2,
                                      created_at=1.0, updated_at=1.0))
    plan = UnifiedChangePlan(plan_id="plan_soak", change_session_id="cs_soak",
                             project_name=pname, version=1,
                             source_snapshot_signature=sig,
                             status="approved_for_execution",
                             steps=[{"step_id": "step_ch", "module": "character",
                                     "owner_agent_id": "character_designer",
                                     "plan_item_ids": [], "depends_on_step_ids": [],
                                     "parallel_group": None}],
                             preview_id="pv_soak", created_at=1.0)
    plan.approval = PlanApproval(plan_id="plan_soak", plan_version=1, decision="approved",
                                 source_snapshot_signature=sig, decided_at=2.0).to_dict()
    store.save_plan(plan)
    store.save_preview(PlanPreviewArtifact(
        preview_id="pv_soak", change_session_id="cs_soak", plan_id="plan_soak",
        plan_version=1, source_snapshot_signature=sig,
        module_operations=[{"step_id": "step_ch", "module": "character",
                            "owner_agent_id": "character_designer",
                            "proposal_item_ids": [],
                            "operations": [o.to_dict() for o in ops]}],
        aggregate_impact=impact.to_dict(), projected_signature=projected,
        applyable=False, created_at=1.0))
    return char_a, char_b


def _soak_env(monkeypatch, pdir, fake):
    """公共边界 mock：agnes/segmentor/download/pm。"""
    monkeypatch.setattr(deps, "agnes_client", fake, raising=False)
    monkeypatch.setattr(deps, "get_project_manager",
                        lambda: type("M", (), {"get_project_dir": staticmethod(
                            lambda name: pdir)})())
    import routers.characters as rc
    monkeypatch.setattr(rc, "_get_segmentor", lambda: _FakeSeg())

    async def _dl(url, proxy=None):
        return b"rawbytes"

    monkeypatch.setattr(cvh, "_download", _dl)


def test_f1_eventb_two_char_crash_restart(monkeypatch, tmp_path):
    """§17 必备：A 重生成完成 + B 未完成 -> 硬停 -> 重启（Event B auto-kickoff）。
    证明：A 不再重生成（calls A==3 恰一组）；B 续跑完成；execution 继续
    （无 PROJECT_CHANGED_AFTER_APPROVAL 误报——F-1：sanctioned views 写回不改签名）。"""
    from src.core.job_queue import JobQueue

    pname = "_test_7ch_soak2c"
    pdir = tmp_path
    char_a, char_b = _seed_2char_project(pdir, pname)
    jobs_path = pdir / "cache" / "jobs.json"

    # ---- Phase A：commit -> resume -> A 完成、B 挂起中 -> 进程"死" ----
    fake_a = _TwoCharFakeAgnes(hang_from_index=3)   # 第 4 次调用起（B front）挂起
    _soak_env(monkeypatch, pdir, fake_a)

    async def phase_a():
        q = JobQueue(jobs_path, {"character_views": handler_character_views})
        await q.start()
        rt = AgentRuntime(project_dir_resolver=lambda name: pdir,
                          job_queue_getter=lambda: q, client=fake_a,
                          settings_path=pdir / "agents_data" / "settings.json")
        res = await cpx.start_replay_commit(rt, pname, "cs_soak", "plan_soak", 1,
                                            "affected_production")
        ex = res["execution"]
        assert ex["canonical_commit"]["status"] == "committed", ex.get("blocker")
        assert ex["stage"] == "character_sync", ex["stage"]
        eid = ex["execution_id"]
        await cpx.resume_execution(rt, pname, eid)   # kickoff 后台 char-sync
        # 等 A 完成（durable：ex.character_sync 落盘）——B 随后开始并挂起
        deadline = time.time() + 30
        store = ChangeProtocolStore(pdir, pname)
        while time.time() < deadline:
            exx = store.get_execution(eid)
            if exx and any(r["character_name"] == char_a
                           for r in exx.character_sync):
                break
            await asyncio.sleep(0.2)
        exx = store.get_execution(eid)
        assert exx is not None and any(r["character_name"] == char_a
                                       for r in exx.character_sync), \
            f"Phase A：A 未完成 char-sync（calls={fake_a.calls}）"
        assert len(fake_a.calls) >= 3, f"A 应已完成一组三视图: {fake_a.calls}"
        # 等 B 的挂起调用确实开始（calls 长度 >= 4 说明 B front 已发起且卡住）
        t0 = time.time()
        while len(fake_a.calls) < 4 and time.time() - t0 < 10:
            await asyncio.sleep(0.2)
        return eid
        # return -> asyncio.run 结束：worker/后台 task 取消 = 后端硬停（B job 落盘 running）

    eid = asyncio.run(phase_a())
    calls_after_a = list(fake_a.calls)
    assert len(calls_after_a) >= 4, f"崩溃窗口应发生在 B 进行中: {calls_after_a}"
    # 模拟新进程：in-flight 记账清空（重启语义，§73 注释）
    cpx._INFLIGHT_POSTCOMMIT.pop(eid, None)

    # ---- Phase B：重启 -> JobQueue _load（B running->queued）-> recover（Event B
    #      auto-kickoff）-> B 续跑完成。planning no-op（本测试聚焦 char-sync 恢复）。 ----
    fake_b = _TwoCharFakeAgnes()
    _soak_env(monkeypatch, pdir, fake_b)

    async def _no_planning(rt, project, execution_id, **k):
        return {"planned_scenes": 0}

    monkeypatch.setattr(cpx, "run_production_planning", _no_planning)

    async def phase_b():
        q2 = JobQueue(jobs_path, {"character_views": handler_character_views})
        await q2.start()
        rt2 = AgentRuntime(project_dir_resolver=lambda name: pdir,
                           job_queue_getter=lambda: q2, client=fake_b,
                           settings_path=pdir / "agents_data" / "settings.json")
        n = cpx.recover_executions(pdir, pname, rt=rt2)   # Event B：自动 kickoff
        assert n >= 1, "应发现 active execution"
        store = ChangeProtocolStore(pdir, pname)
        deadline = time.time() + 60
        while time.time() < deadline:
            exx = store.get_execution(eid)
            if exx and any(r["character_name"] == char_b
                           for r in exx.character_sync):
                break
            await asyncio.sleep(0.3)
        return store.get_execution(eid)

    ex_final = asyncio.run(phase_b())
    assert ex_final is not None and any(r["character_name"] == char_b
                                        for r in ex_final.character_sync), \
        f"Phase B：B 未续跑完成（calls_b={fake_b.calls}）"
    # === 核心不变量 ===
    # ① A 不再重生成：phase B 的 3 次生成全部属于 B（calls 总数恰 3，无 A 的重做）
    assert len(fake_b.calls) == 3, \
        f"重启后应只补 B 的一组三视图（A 不重生成），得 {fake_b.calls}"
    # ② B 完成且是新外观（K1：url 与旧的不同）
    proj = ProjectStore(pdir).load()
    ch_b = next(c for c in proj.characters if c.name == char_b)
    assert all(v.status == "done" for v in ch_b.views.values())
    assert ch_b.views["front"].url != "https://old/front.png"
    # ③ F-1：execution 未被 stale 误报阻塞；commit 仍 committed；阶段推进
    assert ex_final.status != "blocked", \
        f"F-1 回归：sanctioned views 写回后不应 blocked（blocker={ex_final.blocker}）"
    assert (ex_final.canonical_commit or {}).get("status") == "committed"
    assert ex_final.stage in ("production_planning", "production_prepare"), ex_final.stage
    # ④ 无第二 execution
    store = ChangeProtocolStore(pdir, pname)
    assert len(store.list_executions()) == 1
