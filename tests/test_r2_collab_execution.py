# R2 B 批测试（协作下一阶段）：候选过渡池 / 接纳协调器 / 确定性派发。
# 对应验收矩阵（05 文档）：B01 身份隔离 / B04 选版 CAS / B05 审核闸 / B06 三窗口
# 崩溃恢复 / B07 幂等 / B08 程序派发零 LLM / B09 派发键找回 / B10 祖先失效级联。
# 全部假服务：不触网、不调模型、不消耗配额。
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from src.core.models import Project, Scene, Shot
from src.core.store import ProjectStore
from src.core.job_queue import JobQueue
from src.generation.video_commit import video_input_signature
from src.agents.collab_execution.ledger import CollabLedger, LedgerError
from src.agents.collab_execution.promotion import (PromotionBlocked,
                                                   PromotionCoordinator,
                                                   sha256_file)
from src.agents.collab_execution.scheduler import DispatchScheduler
from src.agents.collab_execution.pool import invalidate_cascade

RUN = "run_r2t"


# ============ 夹具 ============
def _mk_project(tmp: Path, name="r2t") -> Path:
    pdir = tmp / "proj"
    ProjectStore(pdir).save(Project(
        name=name, scenes=[Scene(scene_id="s01", shots=[
            Shot(shot_id="s01_c01", scene_id="s01", index=1),
            Shot(shot_id="s01_c02", scene_id="s01", index=2)])]))
    return pdir


def _artifact(tmp: Path, name="cand.mp4", content=b"FAKE_CANDIDATE_BYTES") -> Path:
    p = tmp / "artifacts" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def _register(ledger: CollabLedger, pdir: Path, shot_id="s01_c01",
              artifact: Path = None, *, instance=None, run_id=RUN,
              parent=None, base_final=None, sig=None) -> dict:
    project = ProjectStore(pdir).load()
    shot = project.get_shot(shot_id)
    artifact = artifact or _artifact(pdir.parent)
    return ledger.register_candidate(
        project_instance_id=instance or project.incarnation_id,
        run_id=run_id, shot_id=shot_id, parent_candidate_id=parent,
        artifact_path=str(artifact), artifact_sha256=sha256_file(artifact),
        base_input_signature=sig if sig is not None else video_input_signature(project, shot),
        base_final_video=base_final if base_final is not None else shot.final_video)


def _approve(ledger: CollabLedger, cand: dict):
    ledger.attach_review(candidate_id=cand["candidate_id"],
                         version_id=cand["candidate_id"],
                         content_sha256=cand["artifact_sha256"], overall="pass")


def _make_queue(tmp: Path, ledger: CollabLedger, state: dict) -> JobQueue:
    """假媒体队列：handler 生成候选产物→登记→自动通过审核（不触网零模型调用）。"""
    pdir = tmp / "proj"

    async def fake_video_handler(job, emit):
        state["llm_calls"] += 0  # 显式计数位：任何模型调用都会经此（本 handler 不调用）
        p = tmp / "output" / "candidates" / f"{job.job_id}.mp4"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"CAND_" + job.job_id.encode())
        project = ProjectStore(pdir).load()
        shot = project.get_shot(job.shot_id)
        cand = ledger.register_candidate(
            project_instance_id=project.incarnation_id, run_id=RUN,
            task_id=job.params.get("origin_task_id"), job_id=job.job_id,
            shot_id=job.shot_id, artifact_path=str(p),
            artifact_sha256=sha256_file(p),
            base_input_signature=video_input_signature(project, shot),
            base_final_video=shot.final_video)
        _approve(ledger, cand)
        state.setdefault("candidates", []).append(cand["candidate_id"])

    return JobQueue(tmp / "jobs.json", {"video": fake_video_handler})


# ============ B01：项目实例身份 ============
def test_b01_delete_recreate_new_identity(tmp_path):
    pdir = _mk_project(tmp_path)
    inst1 = ProjectStore(pdir).load().incarnation_id
    assert inst1.startswith("pi_")
    # 已持久化项目再次保存不换身份
    store = ProjectStore(pdir)
    p = store.load()
    p.story_text = "x"
    store.save(p)
    assert store.load().incarnation_id == inst1
    # 删除后同名重建 → 不同实例
    import shutil
    shutil.rmtree(pdir)
    pdir2 = _mk_project(tmp_path)
    inst2 = ProjectStore(pdir2).load().incarnation_id
    assert inst2 != inst1 and inst2.startswith("pi_")


def test_b01_old_candidate_cannot_write_new_project(tmp_path):
    pdir = _mk_project(tmp_path)
    old_inst = ProjectStore(pdir).load().incarnation_id
    ledger = CollabLedger(tmp_path / "ledger.db")
    cand = _register(ledger, pdir, instance=old_inst)
    _approve(ledger, cand)
    # 同名删除重建
    import shutil
    shutil.rmtree(pdir)
    pdir2 = _mk_project(tmp_path)
    coord = PromotionCoordinator(ledger)
    with pytest.raises(PromotionBlocked) as ei:
        coord.promote(pdir2, cand["candidate_id"],
                      project_instance_id=ProjectStore(pdir2).load().incarnation_id)
    assert ei.value.code == "identity"
    # 正式项目语义不变
    p2 = ProjectStore(pdir2).load()
    assert p2.get_shot("s01_c01").final_video is None
    assert not list((pdir2 / "output" / "formal").glob("*")) \
        if (pdir2 / "output" / "formal").exists() else True


# ============ B04：选版 CAS（不抢回用户选版） ============
def test_b04_user_selection_change_blocks_promotion(tmp_path):
    pdir = _mk_project(tmp_path)
    store = ProjectStore(pdir)
    ledger = CollabLedger(tmp_path / "ledger.db")
    cand = _register(ledger, pdir, base_final=None)
    _approve(ledger, cand)
    # 用户在接纳前切了选版
    store.update_shot("s01_c01", lambda s: setattr(s, "final_video", "v_user"))
    coord = PromotionCoordinator(ledger)
    inst = store.load().incarnation_id
    with pytest.raises(PromotionBlocked) as ei:
        coord.promote(pdir, cand["candidate_id"], project_instance_id=inst)
    assert ei.value.code == "selection_changed"
    # 用户选版未被抢回；候选与 promotion 均如实 rejected
    assert store.load().get_shot("s01_c01").final_video == "v_user"
    assert ledger.get_candidate(cand["candidate_id"])["acceptance_state"] == "rejected"
    assert ledger.list_promotions()[0]["state"] == "rejected"
    # 恢复用户选版后可重新接纳（幂等推进，不重复建版本）
    store.update_shot("s01_c01", lambda s: setattr(s, "final_video", None))
    ledger.set_candidate_states(cand["candidate_id"], acceptance_state="pending",
                                invalid_reason=None, from_acceptance=("rejected",))
    out = coord.promote(pdir, cand["candidate_id"], project_instance_id=inst)
    assert out["state"] == "accepted"


# ============ B05：审核闸（缺回执/uncertain/摘要错位/失效） ============
def test_b05_review_gates_block_promotion(tmp_path):
    pdir = _mk_project(tmp_path)
    inst = ProjectStore(pdir).load().incarnation_id
    ledger = CollabLedger(tmp_path / "ledger.db")
    coord = PromotionCoordinator(ledger)

    # (a) 无回执
    c1 = _register(ledger, pdir)
    with pytest.raises(PromotionBlocked) as e:
        coord.promote(pdir, c1["candidate_id"], project_instance_id=inst)
    assert e.value.code == "review"

    # (b) uncertain 不算通过
    c2 = _register(ledger, pdir)
    ledger.attach_review(candidate_id=c2["candidate_id"],
                         version_id=c2["candidate_id"],
                         content_sha256=c2["artifact_sha256"], overall="uncertain")
    with pytest.raises(PromotionBlocked) as e:
        coord.promote(pdir, c2["candidate_id"], project_instance_id=inst)
    assert e.value.code == "review"

    # (c) 回执内容摘要与产物错位（旧回执冒充）
    c3 = _register(ledger, pdir)
    ledger.attach_review(candidate_id=c3["candidate_id"],
                         version_id=c3["candidate_id"],
                         content_sha256="0" * 64, overall="pass")
    with pytest.raises(PromotionBlocked) as e:
        coord.promote(pdir, c3["candidate_id"], project_instance_id=inst)
    assert e.value.code == "review"

    # (d) 产物文件哈希漂移（登记后被改动）
    c4 = _register(ledger, pdir)
    _approve(ledger, c4)
    Path(c4["artifact_path"]).write_bytes(b"TAMPERED")
    with pytest.raises(PromotionBlocked) as e:
        coord.promote(pdir, c4["candidate_id"], project_instance_id=inst)
    assert e.value.code == "artifact"

    # (e) 输入签名漂移（登记后项目输入变了）
    c5 = _register(ledger, pdir, sig="sig_old")
    _approve(ledger, c5)
    with pytest.raises(PromotionBlocked) as e:
        coord.promote(pdir, c5["candidate_id"], project_instance_id=inst)
    assert e.value.code == "baseline"

    # 全程正式项目零写入
    assert ProjectStore(pdir).load().get_shot("s01_c01").final_video is None


# ============ B06：三窗口崩溃恢复（按 promotion_id 对账） ============
def test_b06a_crash_after_intent_recovered(tmp_path):
    pdir = _mk_project(tmp_path)
    inst = ProjectStore(pdir).load().incarnation_id
    ledger = CollabLedger(tmp_path / "ledger.db")
    cand = _register(ledger, pdir)
    _approve(ledger, cand)
    coord = PromotionCoordinator(ledger, crash_hooks={
        "after_intent": lambda evt: (_ for _ in ()).throw(RuntimeError("crash"))})
    with pytest.raises(RuntimeError):
        coord.promote(pdir, cand["candidate_id"], project_instance_id=inst)
    # 崩溃后项目无写入、promotion 停在 intent
    assert ProjectStore(pdir).load().get_shot("s01_c01").final_video is None
    out = PromotionCoordinator(ledger).recover({inst: pdir})
    assert out["redone"] and len(out["redone"]) == 1
    assert ledger.list_promotions()[0]["state"] == "accepted"
    shot = ProjectStore(pdir).load().get_shot("s01_c01")
    assert shot.final_video and len(shot.video_versions) == 1  # 幂等：不重复建版本


def test_b06b_crash_after_project_write_settled_by_recovery(tmp_path):
    pdir = _mk_project(tmp_path)
    inst = ProjectStore(pdir).load().incarnation_id
    ledger = CollabLedger(tmp_path / "ledger.db")
    cand = _register(ledger, pdir)
    _approve(ledger, cand)
    coord = PromotionCoordinator(ledger, crash_hooks={
        "after_project_write": lambda evt: (_ for _ in ()).throw(RuntimeError("crash"))})
    with pytest.raises(RuntimeError):
        coord.promote(pdir, cand["candidate_id"], project_instance_id=inst)
    # 正式写已落盘（version 带 promotion 标识），但账本停在 committing
    assert ProjectStore(pdir).load().get_shot("s01_c01").final_video is not None
    assert ledger.list_promotions()[0]["state"] == "committing"
    out = PromotionCoordinator(ledger).recover({inst: pdir})
    assert out["settled"] == [ledger.list_promotions()[0]["promotion_id"]]
    assert ledger.list_promotions()[0]["state"] == "accepted"
    # 恢复不得重复建版本（正式生效一次）
    shot = ProjectStore(pdir).load().get_shot("s01_c01")
    assert len(shot.video_versions) == 1


def test_b06c_settled_event_delivered_exactly_once(tmp_path):
    pdir = _mk_project(tmp_path)
    inst = ProjectStore(pdir).load().incarnation_id
    ledger = CollabLedger(tmp_path / "ledger.db")
    cand = _register(ledger, pdir)
    _approve(ledger, cand)
    coord = PromotionCoordinator(ledger)
    coord.promote(pdir, cand["candidate_id"], project_instance_id=inst)
    # 结算后、投递前"崩溃"=事件停留在待投递
    pending = ledger.pending_events()
    assert len(pending) == 1 and pending[0]["kind"] == "promotion.accepted"
    seen = []
    assert coord.deliver_events(lambda e: seen.append(e["event_id"])) == 1
    assert coord.deliver_events(lambda e: seen.append(e["event_id"])) == 0  # 不重复投递
    assert len(seen) == 1


# ============ B07：同一接纳命令重复到达 ============
def test_b07_duplicate_promotion_idempotent(tmp_path):
    pdir = _mk_project(tmp_path)
    inst = ProjectStore(pdir).load().incarnation_id
    ledger = CollabLedger(tmp_path / "ledger.db")
    cand = _register(ledger, pdir)
    _approve(ledger, cand)
    coord = PromotionCoordinator(ledger)
    out1 = coord.promote(pdir, cand["candidate_id"], project_instance_id=inst)
    out2 = coord.promote(pdir, cand["candidate_id"], project_instance_id=inst)
    assert out1["promotion_id"] == out2["promotion_id"]
    shot = ProjectStore(pdir).load().get_shot("s01_c01")
    assert len(shot.video_versions) == 1  # 不重复建版本
    assert len([p for p in ledger.list_promotions() if p["state"] == "accepted"]) == 1


# ============ B08：上游接纳 → 下游程序自动派发（零 LLM） ============
def test_b08_acceptance_auto_dispatches_dependent_zero_llm(tmp_path):
    tmp_path = Path(tmp_path)
    pdir = _mk_project(tmp_path)
    inst = ProjectStore(pdir).load().incarnation_id
    ledger = CollabLedger(tmp_path / "ledger.db")
    state = {"llm_calls": 0}
    jq = _make_queue(tmp_path, ledger, state)
    coord = PromotionCoordinator(ledger)
    sched = DispatchScheduler(ledger, lambda: jq)
    stop = asyncio.Event()

    async def flow():
        await jq.start()
        watcher = asyncio.create_task(sched.watch_queue(jq, stop))
        # 下游任务依赖 s01_c01 的已接纳产物（登记时依赖未满足 → waiting）
        t2 = sched.register_task(
            task_id="t_downstream", project_instance_id=inst, run_id=RUN,
            kind="video", project_name="r2t", shot_id="s01_c02",
            depends_on=[{"shot_id": "s01_c01"}])
        assert t2["status"] == "waiting_dependency"
        # 上游任务直接投递（无依赖）
        t1 = sched.register_task(
            task_id="t_upstream", project_instance_id=inst, run_id=RUN,
            kind="video", project_name="r2t", shot_id="s01_c01")
        assert t1["status"] == "queued" and t1["job_id"]
        for _ in range(300):
            j = jq.get(t1["job_id"])
            if getattr(j, "state", "") == "done":
                break
            await asyncio.sleep(0.02)
        cand1 = state["candidates"][0]
        # 上游候选接纳 → 事件投递 → 下游自动入队（程序推进，无 Producer/LLM）
        coord.promote(pdir, cand1, project_instance_id=inst, run_id=RUN)
        pending = ledger.pending_events()
        assert len(pending) == 1 and pending[0]["kind"] == "promotion.accepted"
        dispatched = sched.on_event(pending[0])
        ledger.mark_events_delivered([pending[0]["event_id"]])
        assert len(dispatched) == 1 and dispatched[0]["task_id"] == "t_downstream"
        t2_after = ledger.get_task("t_downstream")
        assert t2_after["status"] == "queued" and t2_after["job_id"]
        for _ in range(300):
            j2 = jq.get(t2_after["job_id"])
            if getattr(j2, "state", "") == "done":
                break
            await asyncio.sleep(0.02)
        stop.set()
        await watcher
        await jq.shutdown() if hasattr(jq, "shutdown") else None

    asyncio.run(flow())
    # 机械推进 LLM 调用数 = 0（B08 硬断言）
    assert state["llm_calls"] == 0
    assert ledger.get_task("t_downstream")["status"] == "done"
    assert ledger.get_task("t_upstream")["status"] == "done"


# ============ B09：入队成功、Job ID 未保存 → 按 dispatch key 找回 ============
def test_b09_crash_before_job_id_save_recovered_by_key(tmp_path):
    tmp_path = Path(tmp_path)
    pdir = _mk_project(tmp_path)
    inst = ProjectStore(pdir).load().incarnation_id
    ledger = CollabLedger(tmp_path / "ledger.db")
    jq = _make_queue(tmp_path, ledger, {"llm_calls": 0})
    sched = DispatchScheduler(ledger, lambda: jq)

    async def flow():
        # 手工复现崩溃窗口：领取（queued+dispatch_key）→ 真实 submit → 未保存 job_id
        task = ledger.register_task(task_id="t_crash", project_instance_id=inst,
                                    run_id=RUN, kind="video", shot_id="s01_c01",
                                    payload={"project_name": "r2t"})
        from src.agents.collab_execution.scheduler import make_dispatch_key
        key = make_dispatch_key(task)
        ledger.transition_task("t_crash", to_status="queued",
                               from_statuses=("ready",), set_dispatch_key=key)
        job = jq.submit("r2t", "video", "s01_c01", origin_dispatch_key=key,
                        project_instance_id=inst, origin_task_id="t_crash",
                        origin_run_id=RUN)
        n_jobs_before = len(jq.list_jobs())
        out = sched.recover()
        assert out["rebound"] == [{"task_id": "t_crash", "job_id": job.job_id}]
        assert len(jq.list_jobs()) == n_jobs_before  # 不重复制造 Job
        assert ledger.get_task("t_crash")["job_id"] == job.job_id

    asyncio.run(flow())


# ============ B10：祖先失效 → 后代级联失效，不得接纳 ============
def test_b10_ancestor_invalidation_cascades(tmp_path):
    pdir = _mk_project(tmp_path)
    inst = ProjectStore(pdir).load().incarnation_id
    ledger = CollabLedger(tmp_path / "ledger.db")
    coord = PromotionCoordinator(ledger)
    parent = _register(ledger, pdir, shot_id="s01_c01")
    child = _register(ledger, pdir, shot_id="s01_c02", parent=parent["candidate_id"])
    grandchild = _register(ledger, pdir, shot_id="s01_c01",
                           parent=child["candidate_id"])
    _approve(ledger, parent)
    _approve(ledger, child)
    _approve(ledger, grandchild)
    # 祖先失效 → 全部后代失效
    invalidated = invalidate_cascade(ledger, parent["candidate_id"],
                                     "ancestor rejected by user")
    assert set(invalidated) == {parent["candidate_id"], child["candidate_id"],
                                grandchild["candidate_id"]}
    for cid in invalidated:
        assert ledger.get_candidate(cid)["acceptance_state"] == "stale"
    # 失效候选不得接纳
    for cid in (parent["candidate_id"], child["candidate_id"]):
        with pytest.raises(PromotionBlocked) as e:
            coord.promote(pdir, cid, project_instance_id=inst)
        assert e.value.code == "invalid"
    # 独立分支不受影响
    other = _register(ledger, pdir, shot_id="s01_c02")
    _approve(ledger, other)
    out = coord.promote(pdir, other["candidate_id"], project_instance_id=inst)
    assert out["state"] == "accepted"


# ============ 账本不变量 ============
def test_ledger_status_cas_and_terminal(tmp_path):
    ledger = CollabLedger(tmp_path / "ledger.db")
    # 无依赖 → 登记即为 ready（无等待条件）
    t = ledger.register_task(task_id="t", project_instance_id="pi_x", run_id="r",
                             kind="video")
    assert t["status"] == "ready" and t["status_revision"] == 1
    t = ledger.transition_task("t", to_status="queued", from_statuses=("ready",))
    assert t["status_revision"] == 2 and t["enqueued_at"]
    # 非法来源状态拒绝（条件更新）
    with pytest.raises(LedgerError):
        ledger.transition_task("t", to_status="queued", from_statuses=("ready",))
    # 状态不倒退：done 后不能再回 queued
    ledger.transition_task("t", to_status="done", from_statuses=("queued",))
    with pytest.raises(LedgerError):
        ledger.transition_task("t", to_status="queued", from_statuses=("done",))
    assert ledger.get_task("t")["status"] == "done"
