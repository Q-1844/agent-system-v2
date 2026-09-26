# Phase 39E：trace 落档时两条 skill 绑定软提示的确定性测试（用户定版实现）。
# 1) mode 对账：record 声称的 generation_mode vs input_visuals 权威推导（t3 谎报教训）。
# 2) 组内 reference-only 意图提醒：段内非首镜+上镜已有视频+无正当理由 → 提醒；
#    有意独立镜（continuity_type=intentional_cut / reference_only_note）不再提醒；
#    组首镜 / 上镜无视频 → 天然豁免。两条都绝不阻断落档。
import asyncio
import json

import pytest

from src.core.models import Project, Scene, Shot, StyleAnchor, VideoVersion
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.director_calibration import _execute_trace_upsert
from src.experiments import calibration_trace as ct

REF_IV = [{"slot": "reference", "semantic_role": "reference_only", "url": "https://h/r.jpg"}]
RELAY_IV = [{"slot": "first", "semantic_role": "relay_start_boundary",
             "url": "https://h/r.jpg", "source_type": "prev_tail_relay"}]


def _proj():
    def mk(sid, idx, ivs):
        sh = Shot(shot_id=f"s01_c0{idx}", scene_id="s01", index=idx,
                  description="x", final_video=f"vv_{idx}",
                  link_mode="multi")
        sh.video_versions.append(VideoVersion(version_id=f"vv_{idx}",
                                              local=f"v{idx}.mp4",
                                              status="done",
                                              input_visuals=list(ivs)))
        return sh
    return Project(name="p39e", style=StyleAnchor(),
                   scenes=[Scene(scene_id="s01", shots=[
                       mk("s01_c01", 1, REF_IV),
                       mk("s01_c02", 2, RELAY_IV),
                       mk("s01_c03", 3, REF_IV)])])


@pytest.fixture()
def env(tmp_path):
    pdir = tmp_path / "proj"
    pdir.mkdir()
    ProjectStore(pdir).save(_proj())
    run = ct.create_run(pdir, story_id="p39e")
    rt = AgentRuntime(project_dir_resolver=lambda name: pdir,
                      settings_path=tmp_path / "agents_data" / "settings.json",
                      client=None)
    return pdir, rt, run["run_id"]


def _upsert(rt, project, run_id, key, record):
    return asyncio.run(_execute_trace_upsert(
        rt, project, json.dumps({"run_id": run_id, "trace_key": key,
                                 "record": record}, ensure_ascii=False),
        None, None))


# ============ mode 对账 ============
def test_mode_mismatch_warning_fires(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c01",
                  {"generation_mode": "multi_relay", "human_deviation_level": "minor"})
    assert out["ok"] is True   # 警告不阻断落档
    assert "mode_mismatch_warning" in out
    assert "multi_reference" in out["mode_mismatch_warning"]
    assert ct.get_link(pdir, run, "s01_c01")["human_deviation_level"] == "minor"


def test_mode_match_no_warning(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c01",
                  {"generation_mode": "multi_reference"})
    assert "mode_mismatch_warning" not in out


def test_no_mode_claim_no_warning(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c01", {"human_deviation_level": "none"})
    assert "mode_mismatch_warning" not in out


# ============ 组内 reference-only 意图提醒 ============
def test_relay_intent_hint_fires_mid_group(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c03", {"human_deviation_level": "minor"})
    assert "relay_intent_hint" in out
    assert "intentional_cut" in out["relay_intent_hint"]
    assert "prev_tail" in out["relay_intent_hint"]


def test_relay_intent_hint_suppressed_by_justification(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c03",
                  {"human_deviation_level": "minor",
                   "continuity_type": "intentional_cut"})
    assert "relay_intent_hint" not in out
    out2 = _upsert(rt, pdir.name, run, "s01_c03",
                   {"reference_only_note": "有意独立镜：云层空镜过渡"})
    assert "relay_intent_hint" not in out2


def test_relay_intent_hint_group_first_exempt(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c01",
                  {"human_deviation_level": "minor"})
    assert "relay_intent_hint" not in out   # 组首镜 reference-only 是约定


def test_relay_intent_hint_relay_shot_exempt(env):
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "s01_c02",
                  {"human_deviation_level": "minor"})
    assert "relay_intent_hint" not in out   # 接力镜本就不是 reference-only


def test_relay_intent_hint_prev_not_ready_exempt(env):
    pdir, rt, run = env
    store = ProjectStore(pdir)
    proj = store.load()
    proj.get_shot("s01_c02").final_video = None   # 上镜无视频 → 无从接力
    store.save(proj)
    out = _upsert(rt, pdir.name, run, "s01_c03",
                  {"human_deviation_level": "minor"})
    assert "relay_intent_hint" not in out


def test_trace_key_not_a_shot_silent(env):
    """trace_key 非镜头 id（如 38 的 M-key）→ 两条提示静默跳过，绝不报错。"""
    pdir, rt, run = env
    out = _upsert(rt, pdir.name, run, "M1",
                  {"generation_mode": "multi_relay", "human_deviation_level": "minor"})
    assert out["ok"] is True
    assert "mode_mismatch_warning" not in out
    assert "relay_intent_hint" not in out


# ============ skill 锚点（补丁内容钉住）============
def test_skill_anchor_interlude_postcheck():
    p = __import__("pathlib").Path(
        "src/agents/skill_system/builtin/full-story-relay-calibration/references/director.md")
    t = p.read_text(encoding="utf-8")
    assert "插曲逐镜落档后同样执行接缝复查" in t
    assert "relay_intent_hint" in t
    assert "mode 对账" in t


def test_skill_anchor_bridge_cross_reference():
    p = __import__("pathlib").Path(
        "src/agents/skill_system/builtin/keyframes-bridge-repair/references/director.md")
    t = p.read_text(encoding="utf-8")
    assert "与插曲的分工" in t
    assert "full-story-relay-calibration" in t
