# tests/test_p50_a_minrepairs.py
"""Phase50 A 批最小修复回归（PHASE50_A §2/§3/§4）。

覆盖：
- activation_hint：工作流显式绑定轮次（envelope 含「本节点已绑定正式 Skill」）不注入
  trigger 词提示（Phase49 W05 实证缺陷：任务文本偶含他包触发词会提示加载错误 Skill）；
- rate_limiter：AGNES_VIDEO_RPM 环境变量只提升 video 桶（默认 1 逐字节不变）；
- job_queue：AGNES_VIDEO_INFLIGHT 环境变量控制视频 worker 数（默认 1）；
- timing_ledger：记录/读回/fail-open（观测件，绝不影响生成主链）。
"""
import asyncio
import importlib
import json
import os
from pathlib import Path

import pytest


# ============ activation_hint：显式绑定轮次抑制 ============

def _rt():
    from src.agents.skill_system.registry import SkillRegistry
    from src.agents.skill_system.runtime import SkillRuntime
    builtin = Path(__file__).resolve().parent.parent / "src" / "agents" / \
        "skill_system" / "builtin"
    return SkillRuntime(SkillRegistry(source_dirs=[builtin]))


def test_binding_marker_suppresses_hint():
    rt = _rt()
    envelope_text = ("【来自 Producer 的委派任务】\n任务目标：逐镜生成视频\n"
                     "必须遵守的约束：\n- 本节点已绑定正式 Skill："
                     "full-story-relay-calibration v1.4.0"
                     "（适用时用 skill.load 加载并遵循其方法）。\n"
                     "- 分镜图候选要完整")
    assert rt.activation_hint("director", envelope_text) is None


def test_binding_marker_suppresses_hint_even_on_trigger_words():
    """任务文本含候选包触发词 + 显式绑定标记 → 仍不提示（Phase49 W05 情景）。"""
    rt = _rt()
    text = ("本节点已绑定正式 Skill：full-story-relay-calibration v1.4.0"
            "（适用时用 skill.load 加载并遵循其方法）。任务：生成分镜图候选")
    assert rt.activation_hint("director", text) is None


def test_free_turn_hint_still_works():
    rt = _rt()
    hint = rt.activation_hint("director", "对整条故事做连续性校准")
    assert hint is not None and "full-story-relay-calibration" in hint


# ============ rate_limiter：video 桶 env 提升 ============

def test_video_rpm_default_unchanged(monkeypatch):
    monkeypatch.delenv("AGNES_VIDEO_RPM", raising=False)
    import src.core.rate_limiter as rl
    importlib.reload(rl)
    assert rl.DEFAULT_RPM["video"] == 1


def test_video_rpm_env_override(monkeypatch):
    monkeypatch.setenv("AGNES_VIDEO_RPM", "5")
    import src.core.rate_limiter as rl
    importlib.reload(rl)
    try:
        assert rl.DEFAULT_RPM["video"] == 5
    finally:
        # monkeypatch 的 env 还原发生在 fixture 收尾（晚于本 finally）——先显式删掉再
        # reload，才能真正还原模块态，避免污染其它用例。
        monkeypatch.delenv("AGNES_VIDEO_RPM", raising=False)
        importlib.reload(rl)
    assert rl.DEFAULT_RPM["video"] == 1


def test_video_rpm_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv("AGNES_VIDEO_RPM", "abc")
    import src.core.rate_limiter as rl
    importlib.reload(rl)
    assert rl.DEFAULT_RPM["video"] == 1


# ============ job_queue：视频 worker 数 env ============

def test_video_inflight_default_one(monkeypatch, tmp_path):
    monkeypatch.delenv("AGNES_VIDEO_INFLIGHT", raising=False)
    from src.core.job_queue import JobQueue
    q = JobQueue(tmp_path / "jobs.json", {})

    async def _run():
        await q.start()
        for t in q._tasks:
            t.cancel()
        return q.video_worker_count
    assert asyncio.run(_run()) == 1


def test_video_inflight_env_three(monkeypatch, tmp_path):
    monkeypatch.setenv("AGNES_VIDEO_INFLIGHT", "3")
    from src.core.job_queue import JobQueue
    q = JobQueue(tmp_path / "jobs.json", {})

    async def _run():
        await q.start()
        for t in q._tasks:
            t.cancel()
        return q.video_worker_count
    assert asyncio.run(_run()) == 3


# ============ timing_ledger：记录 / 读回 / fail-open ============

def test_ledger_record_and_readback(tmp_path, monkeypatch):
    monkeypatch.setenv("AGNES_TIMING_LEDGER", str(tmp_path / "ledger.jsonl"))
    from src.core import timing_ledger
    importlib.reload(timing_ledger)
    e1 = timing_ledger.record("video_ready", project="P", shot_id="s01",
                              seconds_requested=10)
    e2 = timing_ledger.record("video_submitted", project="P", shot_id="s01",
                              video_id="v_abc", note=None)
    assert e1 and e2
    assert e1["utc"] and e1["mono"] >= 0
    assert "note" not in e2          # None 值字段不落盘
    events = timing_ledger.read_events()
    assert [e["event"] for e in events] == ["video_ready", "video_submitted"]
    assert events[1]["video_id"] == "v_abc"


def test_ledger_fail_open(tmp_path, monkeypatch):
    """账本路径不可写时 record 返回 None 且不抛（绝不影响生成主链）。"""
    monkeypatch.setenv("AGNES_TIMING_LEDGER",
                       str(tmp_path / "no_dir" / "x.jsonl"))
    from src.core import timing_ledger
    importlib.reload(timing_ledger)
    # 正常：父目录会自动创建
    assert timing_ledger.record("probe") is not None
    # 目录被替换成文件 → open 失败 → fail-open
    bad = tmp_path / "badfile"
    bad.write_text("not a dir", encoding="utf-8")
    monkeypatch.setenv("AGNES_TIMING_LEDGER", str(bad / "child" / "l.jsonl"))
    assert timing_ledger.record("probe") is None
