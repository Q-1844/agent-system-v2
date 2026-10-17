# Phase55 D 定向测试：会话接力（预算监控/交接包/主控租约）与审核流水化。
#
# 覆盖（任务书 §11.1「会话」「审核修复」行）：
#   1. context_budget：估算器阈值判定（config 可覆盖，不硬编码报告数字）；
#      UsageLedger 落账/last anchor/totals（成本口径≠上下文长度）；
#   2. master_lease：acquire/续期/他人未过期拒绝/过期接管/transfer/verify（旧会话写拒）；
#   3. handoff：交接包字段完备（canonical 索引/在途任务/待审/grant/下一操作）；
#      relay 产生新会话+租约移交+种子消息；旧会话写尝试被拒；
#   4. producer 预算交接触发（低阈值强制接力）：turn 返回 handoff 信息+接力文本；
#   5. review_pipeline：单镜三帧审核编排（假 vision）+硬错误推导+批次报告落盘。
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents import context_budget as cb
from src.agents import master_lease as ml
from src.agents import handoff as hf

PROJECT = "_test_p55_relay"


# ============ 1. context_budget ============
def test_estimate_thresholds_and_config_override():
    est = cb.estimate_request_tokens(system_chars=1000, tools_chars=2000,
                                     history_chars=1000)
    assert est["est_input_tokens"] == 2000 and not est["handoff"]
    # 低阈值触发（config 覆盖——阈值按真实 provider 配置，不硬编码）
    cfg = {"limit_tokens": 2500, "output_reserve_tokens": 500,
           "handoff_ratio": 0.85, "chars_per_token": 2.0,
           "media_budget_tokens": 0}
    est2 = cb.estimate_request_tokens(system_chars=1000, tools_chars=2000,
                                      history_chars=1000, cfg=cfg)
    assert est2["handoff"]   # 2000 + 500 >= 2500*0.85=2125


def test_usage_ledger(tmp_path):
    led = cb.UsageLedger(tmp_path, "sess_x")
    led.record(kind="chat", usage={"prompt_tokens": 100, "completion_tokens": 10,
                                   "total_tokens": 110})
    led.record(kind="chat_stream", usage={"prompt_tokens": 200,
                                          "completion_tokens": 30,
                                          "total_tokens": 230})
    assert led.last_prompt_tokens() == 200
    t = led.totals()
    assert t["calls"] == 2 and t["prompt_tokens"] == 300
    assert led.handoff_anchor()["last_observed_prompt_tokens"] == 200


# ============ 2. master_lease ============
def test_lease_lifecycle(tmp_path):
    a = ml.acquire(tmp_path, "sess_a")
    assert a["ok"] and a["generation"] == 1
    # 同 holder 续期（代次不变）
    a2 = ml.acquire(tmp_path, "sess_a", ttl_s=50)
    assert a2["ok"] and a2["generation"] == 1 and a2["reason"] == "续期"
    # 他人未过期 → 拒绝
    b = ml.acquire(tmp_path, "sess_b", now=a2["expires_at"] - 10)
    assert not b["ok"] and b["holder"] == "sess_a"
    # 过期 → 接管（新代次）
    c = ml.acquire(tmp_path, "sess_b", now=a2["expires_at"] + 10)
    assert c["ok"] and c["generation"] == 2
    # transfer：仅当前 holder 可移交
    t_bad = ml.transfer(tmp_path, "sess_a", "sess_c")
    assert not t_bad["ok"]
    t = ml.transfer(tmp_path, "sess_b", "sess_c")
    assert t["ok"] and t["generation"] == 3 and t["holder"] == "sess_c"
    # verify：旧会话写拒，新 holder 通过
    assert not ml.verify(tmp_path, "sess_b")["verified"]
    assert ml.verify(tmp_path, "sess_c")["verified"]


def test_lease_expired_holder_continuity(tmp_path):
    """冒烟实证回归：自身租约过期≠失去主控（长 turn 超 TTL 常态）——
    holder 连续性保持；他人接管（generation+1）后才验不过。"""
    a = ml.acquire(tmp_path, "sess_long", ttl_s=50)
    assert a["ok"]
    # 过期后：同 holder 仍 verified（连续性）；他人可接管
    future = a["expires_at"] + 10
    assert ml.verify(tmp_path, "sess_long", now=future)["verified"]
    b = ml.acquire(tmp_path, "sess_other", now=future)
    assert b["ok"] and b["generation"] == 2
    assert not ml.verify(tmp_path, "sess_long", now=future)["verified"]


def test_lease_corrupt_file_fail_closed(tmp_path):
    """Phase56 A3 语义变更（原 P55 fail-open → fail-closed）：损坏租约=权限
    未核明——verify 拒写、acquire 拒接管；恢复走 diagnose/recover 独立路径。
    详测见 tests/test_p56_a_mechanism.py::test_lease_corrupt_fail_closed_and_recover。"""
    p = tmp_path / "output" / "agents" / "master_lease.json"
    p.parent.mkdir(parents=True)
    p.write_text("{broken", encoding="utf-8")
    v = ml.verify(tmp_path, "sess_a")
    assert not v["verified"] and v.get("corrupt") is True
    assert ml.acquire(tmp_path, "sess_b")["ok"] is False


# ============ 3. handoff / relay ============
class _FakeTask:
    def __init__(self):
        self.task_id = "t1"
        self.status = "running"
        self.capability_id = "shot.video"
        self.scope_id = "s01_c01"


class _FakeSessionStore:
    def __init__(self, tmp):
        self.tmp = tmp

    def load(self, sid):
        return SimpleNamespace(messages=[SimpleNamespace(
            role="user", content="目标：六段短片全流程")])

    def append_message(self, sid, msg):
        self.tmp.setdefault("appended", []).append((sid, msg.content))
        return SimpleNamespace(session_id=sid)

    def create_fresh_conversation(self, agent_id):
        sid = f"sess_{agent_id}_fresh"
        self.tmp.setdefault("fresh", []).append(sid)
        return SimpleNamespace(session_id=sid)


class _FakeRT:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.store = _FakeSessionStore(self.__dict__)

    def _resolve_project_dir(self, project):
        return self.tmp

    def _session_store(self, project):
        return self.store

    def _task_store(self, project):
        class TS:
            def list(self, *a, **k):
                return [_FakeTask()]
        return TS()

    def _resolve_job_queue_or_none(self):
        class Q:
            def list_jobs(self, project=None, state=None, limit=100):
                return [{"job_id": "j1", "kind": "video", "shot_id": "s01_c01",
                         "state": "running",
                         "params": {"provider_task_id": "task_x"}}]
        return Q()


def test_handoff_package_fields(tmp_path):
    from src.core.models import Project, Scene, Shot, Character, StyleAnchor
    from src.core.store import ProjectStore
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime",
                                                   aspect_ratio="16:9"),
                   story_text="s",
                   characters=[Character(name="阿派", description_en="a")],
                   scenes=[Scene(scene_id="s01", summary="s", characters=["阿派"])])
    ProjectStore(tmp_path).save(proj)
    rt = _FakeRT(tmp_path)
    pkg = hf.build_handoff_package(rt, PROJECT, "sess_old", reason="测试交接")
    assert pkg["schema"] == "producer_handoff_v1"
    for k in ("handoff_id", "canonical", "inflight", "grants", "lease",
              "objective_excerpt", "next_action_hint"):
        assert k in pkg, k
    assert "六段短片全流程" in pkg["objective_excerpt"]
    assert pkg["inflight"]["media_jobs"][0]["provider_task_id"] == "task_x"
    assert pkg["inflight"]["agent_tasks"][0]["capability_id"] == "shot.video"
    # 索引不是真相声明
    assert "不要信任本摘要" in pkg["next_action_hint"]
    path = hf.save_handoff_package(tmp_path, pkg)
    assert Path(path).exists()


def test_relay_creates_fresh_session_and_transfers_lease(tmp_path):
    from src.core.models import Project, Scene, Shot, Character, StyleAnchor
    from src.core.store import ProjectStore
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime",
                                                   aspect_ratio="16:9"),
                   story_text="s",
                   characters=[Character(name="阿派", description_en="a")],
                   scenes=[Scene(scene_id="s01", summary="s", characters=["阿派"])])
    ProjectStore(tmp_path).save(proj)
    rt = _FakeRT(tmp_path)
    ml.acquire(tmp_path, "sess_old")
    out = hf.relay_producer_session(rt, PROJECT, "sess_old", reason="预算接力")
    assert out["new_session_id"] == "sess_producer_fresh"
    assert out["lease"]["holder"] == "sess_producer_fresh"
    assert out["lease"]["generation"] == 2
    # 种子消息已注入新会话
    sid, content = rt.store.tmp["appended"][0]
    assert sid == "sess_producer_fresh"
    assert "会话接力" in content and "canonical" in content
    # 旧会话写被拒（租约执行点）
    assert not ml.verify(tmp_path, "sess_old")["verified"]
    assert Path(out["handoff_path"]).exists()


# ============ 4. producer 预算交接触发（低阈值强制接力） ============
def test_producer_handoff_on_low_budget(tmp_path, monkeypatch):
    """handoff_ratio 压低 → 第一轮预算检查即触发交接：turn 返回接力文本 +
    handoff 信息，不再调用 LLM 做新推理（client 调用计数为 0）。"""
    import src.agents.producer as producer_mod
    from src.agents.producer import _producer_tool_loop
    monkeypatch.setattr(cb, "_cfg", lambda: {
        "limit_tokens": 1000, "output_reserve_tokens": 100,
        "handoff_ratio": 0.1, "chars_per_token": 2.0,
        "media_budget_tokens": 0})

    class _L:
        def record(self, **k):
            pass

        def totals(self):
            return {"calls": 0}

    state = {"usage_ledger": _L(), "session_id": "sess_p"}
    calls = {"n": 0}

    class _Client:
        async def chat_completion_stream(self, *a, **k):
            calls["n"] += 1
            yield {"kind": "finish", "reason": "stop", "content": "x",
                   "tool_calls": None, "usage": None}

    from src.core.models import Project, Scene, Character, StyleAnchor
    from src.core.store import ProjectStore
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime",
                                                   aspect_ratio="16:9"),
                   story_text="s",
                   characters=[Character(name="阿派", description_en="a")],
                   scenes=[Scene(scene_id="s01", summary="s", characters=["阿派"])])
    ProjectStore(tmp_path).save(proj)

    class _RT:
        def _resolve_project_dir(self, project):
            return tmp_path

    out = asyncio.run(_producer_tool_loop(
        _RT(), _Client(), PROJECT,
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "目标"}],
        "instr", "m1", state=state, tool_executors={}, offered_tools=[]))
    assert calls["n"] == 0, "交接后不得再发起新推理"
    assert "会话已自动接力" in out["text"]
    assert state["handoff"]["handoff_id"]
    assert Path(state["handoff"]["path"]).exists()


# ============ 5. review_pipeline ============
def test_review_pipeline_batch(tmp_path, monkeypatch):
    """单镜三帧编排（假 vision 返回可控 JSON）+ 硬错误推导 + 报告落盘。"""
    from src.core.models import (Project, Scene, Shot, Character, StyleAnchor,
                                 VideoAsset, VideoVersion)
    from src.core.store import ProjectStore
    from src.core.models import Asset
    s = Shot(shot_id="s01_c01", scene_id="s01", index=1, description="x",
             characters=["阿派"])
    s.video = VideoAsset(status="done", local=str(tmp_path / "v.mp4"),
                         url="https://fake/v.mp4")
    Path(s.video.local).write_bytes(b"fake")
    vv = VideoVersion(version_id="vv_rvw1", created_at=1.0, status="done",
                      local=str(tmp_path / "v.mp4"), url="https://fake/v.mp4",
                      seconds_returned=5.0)
    s.video_versions = [vv]
    s.final_video = "vv_rvw1"   # 已完成视频（审核对象）
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime",
                                                   aspect_ratio="16:9"),
                   story_text="s",
                   characters=[Character(name="阿派", description_en="a")],
                   scenes=[Scene(scene_id="s01", summary="s", characters=["阿派"],
                                 shots=[s])])
    ProjectStore(tmp_path).save(proj)

    from src.agents.shot_review import SHOT_REVIEW_DIMENSIONS
    reply = {"dimensions": {d: {"status": ("fail" if d == "duplicate_identity"
                                           else "pass"), "note": ""}
                            for d in SHOT_REVIEW_DIMENSIONS},
             "summary": "发现分身"}
    # 直接构造 review_shot_core 的返回（monkeypatch 掉 vision 调用）
    import src.agents.review_pipeline as rp
    from src.agents.shot_review import derive_overall

    async def fake_review_shot_core(client, *, project_dir, proj, shot, frame,
                                    image_local=None, image_url=None, persist=True):
        review = {"dimensions": reply["dimensions"],
                  "summary": reply["summary"]}
        return {"shot_id": shot.shot_id, "review": review,
                "derived": derive_overall(review["dimensions"]),
                "telemetry": {}}

    monkeypatch.setattr(rp, "review_shot_core", fake_review_shot_core)
    monkeypatch.setattr(rp, "extract_shot_frame_local",
                        lambda *a, **k: {"local": "fake.jpg", "url": None,
                                         "source": {"shot_id": "s01_c01",
                                                    "version_id": "vv_rvw1",
                                                    "position": "0.000"}})

    class _RT:
        def _resolve_client_or_none(self, agent_id=None):
            return SimpleNamespace()   # 只要非 None

    out = asyncio.run(rp.run_independent_review(
        _RT(), tmp_path, PROJECT, reason="测试"))
    assert out["shots_reviewed"] == 1
    assert out["hard_error_shots"] == ["s01_c01"]
    assert any(f["hard_dims_fired"] == ["duplicate_identity"]
               for f in out["results"][0]["frames"])
    assert Path(out["report_path"]).exists()
    doc = json.loads(Path(out["report_path"]).read_text(encoding="utf-8"))
    assert doc["batch_id"] == out["batch_id"]
    assert "非完全独立盲审" in doc["independence_note"]
